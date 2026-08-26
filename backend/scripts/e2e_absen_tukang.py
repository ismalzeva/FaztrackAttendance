"""E2E alur tukang bangunan: multi check-in + auto check-out titik lama.
Jalan via /tmp/lumin_run.sh (env proses BE diwarisi, PIN tidak pernah dicetak).
"""
import json,os,math,sys,datetime,urllib.request,urllib.error

BASE="http://localhost:8011/api/v1/worker-web"
PIN=os.environ.get("FAZTRACK_DEMO_WORKER_PIN","")
DB_URL=os.environ.get("FAZTRACK_DATABASE_URL","")
if not PIN:
    print("FAIL: demo pin tidak ada di env"); sys.exit(2)

# ---- RESET data hari ini untuk TKN-APE (test isolation) ----
def reset_today():
    """Hapus event+challenge hari ini untuk TKN-APE agar E2E mulai bersih."""
    import psycopg
    raw_url = DB_URL.replace("postgresql+psycopg://","postgresql://").replace("postgresql+psycopg2://","postgresql://")
    conn = psycopg.connect(raw_url)
    conn.autocommit = True
    cur = conn.cursor()
    today = datetime.date.today()
    # Cari worker_id TKN-APE
    cur.execute("SELECT id FROM workers WHERE code='TKN-APE'")
    row = cur.fetchone()
    if not row:
        print("WARN: TKN-APE tidak ditemukan di DB"); conn.close(); return
    wid = row[0]
    cur.execute("DELETE FROM attendance_events WHERE worker_id=%s AND work_date=%s", (wid, today))
    ne = cur.rowcount
    cur.execute("DELETE FROM attendance_challenges WHERE worker_id=%s AND work_date=%s", (wid, today))
    nc = cur.rowcount
    conn.close()
    print(f"reset TKN-APE {today}: {ne} events, {nc} challenges dihapus")

reset_today()

def call(path,body=None,tok=None):
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(BASE+path,data=data,method="POST" if body is not None else "GET",
        headers={"Content-Type":"application/json",**({"Authorization":"Bearer "+tok} if tok else {})})
    try:
        with urllib.request.urlopen(req,timeout=20) as r: return r.status,json.load(r)
    except urllib.error.HTTPError as e:
        try: return e.code,json.load(e)
        except Exception: return e.code,{}

def unwrap(d):
    return d.get("data",d) if isinstance(d,dict) else d

def offset(lat,lon,meters_north=12.0,meters_east=7.0):
    """titik ~14m dari pusat -> di dalam geofence."""
    return (lat+meters_north/111320.0, lon+meters_east/(111320.0*math.cos(math.radians(lat))))

results=[]
def step(name,ok,info=None):
    results.append((name,ok,str(info)))
    print(("PASS" if ok else "FAIL")+" | "+name+((" | "+str(info)) if info is not None else ""))

def err_code(d):
    det=d.get("detail") if isinstance(d,dict) else None
    return det.get("code") if isinstance(det,dict) else None

def submit(proj,evtype):
    s,c=call("/challenge",{"event_type":evtype,"project_id":proj["id"]},tok=tok)
    if s!=200: return s,err_code(c),None
    c=unwrap(c)
    la,lo=offset(proj["latitude"],proj["longitude"])
    body={"challenge_id":c["challenge_id"],"challenge":c["challenge"],"event_type":evtype,
          "project_id":proj["id"],"latitude":la,"longitude":lo,"accuracy_m":8.0}
    s,r=call("/events",body,tok=tok)
    return s,c,r

# ---- LOGIN ----
s,d=call("/login",{"tenant_code":"lumin-park","worker_code":"TKN-APE","pin":"wrong"})
step("login PIN salah ditolak",s==401,(s,err_code(d)))
s,d=call("/login",{"tenant_code":"lumin-park","worker_code":"TKN-APE","pin":PIN})
tok=(unwrap(d) or {}).get("access_token")
step("login Apes OK",s==200 and bool(tok),s)

# ---- SHIFT AWAL ----
s,sh=call("/shift",tok=tok); sh=unwrap(sh)
projs={p["code"]:p for p in sh["projects"]}
b2,a12=projs.get("RUMAH-BLK-B2"),projs.get("RUMAH-BLK-A12")
step("shift: 2 project Griya ada",bool(b2 and a12),list(projs.keys()))
step("shift: terjadwal hari ini",sh.get("scheduled") is True)

# ---- 1) CHECK-IN Blok B2 ----
s,c,r=submit(b2,"CHECK_IN")
r=unwrap(r) if isinstance(r,dict) else {}
step("check-in Blok B2 VALID",r.get("status")=="VALID",(s,r.get("status"),r.get("reason_code")))

# duplikat check-in project sama harus ditolak guard
s,c,r=submit(b2,"CHECK_IN")
step("duplikat check-in Blok B2 ditolak",c=="ATTENDANCE_ALREADY_RECORDED",(s,c))

# ---- 2) PINDAH ke Blok A12 -> auto check-out B2 ----
s,c,r=submit(a12,"CHECK_IN"); r=unwrap(r) if isinstance(r,dict) else {}
step("check-in Blok A12 VALID (pindah lokasi)",r.get("status")=="VALID",(s,r.get("status")))

s,sh=call("/shift",tok=tok); sh=unwrap(sh)
tl=sh["timeline"]
auto=[e for e in tl if e["event_type"]=="CHECK_OUT" and e["auto"]]
step("auto check-out titik lama tercatat",len(auto)>=1,[ (e["project_name"],e.get("reason_code")) for e in auto])
open_id=(sh.get("open_shift") or {}).get("project_id")
step("open_shift kini Blok A12",open_id==a12["id"],(open_id==a12["id"],))

# ---- 3) CHECK-OUT akhir ----
s,c,r=submit(a12,"CHECK_OUT"); r=unwrap(r) if isinstance(r,dict) else {}
step("check-out akhir VALID",r.get("status")=="VALID",(s,r.get("status")))
s,sh=call("/shift",tok=tok); sh=unwrap(sh)
step("open_shift kosong setelah check-out",sh.get("open_shift") is None)

# ---- 4) negatif: check-out tanpa shift terbuka ----
s,c,r=submit(a12,"CHECK_OUT")
# r=None jika challenge ditolak (409), c berisi error code
step("check-out tanpa shift ditolak (CHECK_IN_REQUIRED)",c=="CHECK_IN_REQUIRED",(s,c))

n_pass=sum(1 for _,ok,_ in results if ok)
print(f"\n=== E2E TUKANG: {n_pass}/{len(results)} PASS ===")
sys.exit(0 if n_pass==len(results) else 1)
