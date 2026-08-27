"""E2E anti-titip absen Lumin: device binding kripto (ECDSA P-256).

Menguji alur lengkap:
  1. Login PIN unik per karyawan (faktor pengetahuan).
  2. Enroll perangkat (kunci privat ECDSA P-256 dibuat di sisi klien).
  3. Supervisor/approve menyetujui perangkat -> DeviceBinding aktif.
  4. Setiap absen WAJIB ditandatangani kunci privat perangkat (faktor kepemilikan).
  5. Negatif: absen yang ditandatangani perangkat LAIN ditolak
     (INVALID_ATTENDANCE_SIGNATURE) — inilah pengunci "HP teman tidak bisa absen".
  6. Duplikat check-in ditolak + check-out berhasil.

Worker uji: EMP-022 Yus (PRJ-01 Kantor Pemasaran Lumin).
Jalan via /tmp/lumin_run.sh (env proses BE diwarisi; PIN/password tak pernah dicetak).
"""
import json, os, math, sys, datetime, base64, urllib.request, urllib.error

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

BASE = "http://localhost:8011/api/v1/worker-web"
BASEV1 = "http://localhost:8011/api/v1"
TENANT_ID = "lumin-park-001"
WORKER_CODE = "EMP-022"            # Yus — PRJ-01
WORKER_NAME = "Yus"
PINFILE = "/home/ubuntu/FaztrackAttendance/backend/lumin_pins.json"

with open(PINFILE) as f:
    PIN = json.load(f).get(WORKER_CODE, "")
ADMIN_PASSWORD = os.environ.get("FAZTRACK_DEMO_SEED_PASSWORD", "")
DB_URL = os.environ.get("FAZTRACK_DATABASE_URL", "")
if not PIN:
    print(f"FAIL: PIN {WORKER_CODE} tidak ada di lumin_pins.json"); sys.exit(2)
if not ADMIN_PASSWORD:
    print("FAIL: FAZTRACK_DEMO_SEED_PASSWORD tidak ada di env"); sys.exit(2)


# ---------- kripto (mirror backend app/device_crypto.py) ----------
def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))

class Device:
    """Perangkat yang memegang kunci privat ECDSA P-256 (simulasi WebCrypto)."""
    def __init__(self):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        pub = self.priv.public_key().public_numbers()
        self.jwk = {"kty": "EC", "crv": "P-256",
                    "x": b64url(pub.x.to_bytes(32, "big")),
                    "y": b64url(pub.y.to_bytes(32, "big"))}
    def sign_b64(self, msg: bytes) -> str:
        der = self.priv.sign(msg, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))

def signed_payload(b: dict) -> bytes:
    """Canonical JSON identik dgn app.attendance.signed_payload (wajib byte-per-byte)."""
    data = {"accuracy_m": f"{b['accuracy_m']:.1f}",
            "captured_at_client": b["captured_at_client"],
            "challenge": b["challenge"],
            "challenge_id": b["challenge_id"],
            "event_type": b["event_type"],
            "latitude": f"{b['latitude']:.6f}",
            "longitude": f"{b['longitude']:.6f}",
            "project_id": b["project_id"]}
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode()


# ---------- reset data hari ini utk EMP-022 (isolasi test) ----------
def reset_today():
    import psycopg
    raw = DB_URL.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg.connect(raw); conn.autocommit = True
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT id FROM workers WHERE code=%s AND tenant_id=%s", (WORKER_CODE, TENANT_ID))
    row = cur.fetchone()
    if not row:
        print(f"WARN: {WORKER_CODE} tidak ditemukan"); conn.close(); return
    wid = row[0]

    # urutan hapus: child (bindings) dulu, lalu enrollments/challenges/attendance
    cur.execute("DELETE FROM device_bindings WHERE worker_id=%s", (wid,))
    nb = cur.rowcount
    cur.execute("DELETE FROM device_enrollments WHERE worker_id=%s", (wid,))
    ne = cur.rowcount
    cur.execute("DELETE FROM device_challenges WHERE worker_id=%s", (wid,))
    nch = cur.rowcount
    cur.execute("DELETE FROM attendance_events WHERE worker_id=%s AND work_date=%s", (wid, today))
    nae = cur.rowcount
    cur.execute("DELETE FROM attendance_challenges WHERE worker_id=%s AND work_date=%s", (wid, today))
    nac = cur.rowcount

    # Pastikan jadwal untuk hari ini (upsert — abaikan kalau sudah ada)
    cur.execute(
        """INSERT INTO work_schedules (id, tenant_id, worker_id, work_date, start_time, end_time, is_working_day)
           VALUES (gen_random_uuid()::text, %s, %s, %s, '08:00', '17:00', TRUE)
           ON CONFLICT (tenant_id, worker_id, work_date) DO NOTHING""",
        (TENANT_ID, wid, today))
    nws = cur.rowcount
    conn.close()
    print(f"reset {WORKER_CODE}: {nb} bindings, {ne} enrollments, {nch} device-challenges, {nae} events, {nac} attendance-challenges, +{nws} schedule")


reset_today()


# ---------- cari project EMP-022 ----------
def find_project():
    import psycopg
    raw = DB_URL.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg.connect(raw)
    cur = conn.cursor()
    cur.execute(
        """SELECT p.id, p.code, p.name, p.latitude, p.longitude
           FROM projects p
           JOIN assignments a ON a.project_id=p.id AND a.tenant_id=%s
           JOIN workers w ON a.worker_id=w.id
           WHERE w.code=%s AND w.tenant_id=%s AND p.tenant_id=%s""",
        (TENANT_ID, WORKER_CODE, TENANT_ID, TENANT_ID))
    row = cur.fetchone()
    conn.close()
    if not row:
        print(f"FAIL: {WORKER_CODE} tidak punya project assignment"); sys.exit(2)
    return {"id": row[0], "code": row[1], "name": row[2], "latitude": float(row[3]), "longitude": float(row[4])}


PROJECT = find_project()
print(f"project: {PROJECT['code']} {PROJECT['name']} ({PROJECT['latitude']}, {PROJECT['longitude']})")


# ---------- HTTP helper ----------
def call(base, path, body=None, tok=None, extra_headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    if method is None:
        method = "POST" if body is not None else "GET"
    headers = {"Content-Type": "application/json"}
    if tok: headers["Authorization"] = "Bearer " + tok
    if extra_headers: headers.update(extra_headers)
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try: return e.code, json.load(e)
        except Exception: return e.code, {}

def w(path, body=None, tok=None, extra_headers=None, method=None):
    return call(BASE, path, body, tok, extra_headers, method)

def v1(path, body=None, tok=None, extra_headers=None, method=None):
    return call(BASEV1, path, body, tok, extra_headers, method)

def unwrap(d):
    return d.get("data", d) if isinstance(d, dict) else d

def offset(lat, lon, m_north=12.0, m_east=7.0):
    return (lat + m_north / 111320.0, lon + m_east / (111320.0 * math.cos(math.radians(lat))))

results = []
def step(name, ok, info=None):
    results.append((name, ok, str(info) if info is not None else ""))
    print(("PASS" if ok else "FAIL") + " | " + name + ((" | " + str(info)) if info is not None else ""))

def err_code(d):
    det = d.get("detail") if isinstance(d, dict) else None
    return det.get("code") if isinstance(det, dict) else None


# ---------- LOGIN (PIN unik) ----------
s, d = w("/login", {"tenant_code": "lumin-park", "worker_code": WORKER_CODE, "pin": "wrong"})
step("login PIN salah ditolak", s == 401, (s, err_code(d)))

s, d = w("/login", {"tenant_code": "lumin-park", "worker_code": WORKER_CODE, "pin": PIN})
tok = (unwrap(d) or {}).get("access_token")
step(f"login {WORKER_NAME} OK (PIN unik)", s == 200 and bool(tok), s)

# ---------- device binding belum ada (faktor kepemilikan harus dipenuhi) ----------
s, d = w("/device", tok=tok); d = unwrap(d)
step("device awal: belum terdaftar", s == 200 and d.get("enrolled") is False, (s, d.get("enrolled")))

s, d = w("/challenge", {"event_type": "CHECK_IN"}, tok=tok)
step("challenge tanpa device ditolak (NO_ACTIVE_DEVICE)", err_code(d) == "NO_ACTIVE_DEVICE", (s, err_code(d)))

# ---------- ENROLL device (kunci privat dibuat di sisi klien) ----------
dev = Device()
s, d = v1("/worker/device-enrollment/challenge", tok=tok, method="POST"); d = unwrap(d)
step("enroll: challenge diterima", s == 200 and bool(d.get("challenge_id")), s)
ch_dev = d
sig = dev.sign_b64(b64url_dec(ch_dev["challenge"]))
s, d = v1("/worker/device-enrollment/requests",
          {"challenge_id": ch_dev["challenge_id"], "public_key_jwk": dev.jwk,
           "signature": sig, "device_label": f"HP {WORKER_NAME}"},
          tok=tok, extra_headers={"X-Device-Challenge": ch_dev["challenge"]})
d = unwrap(d)
enrollment_id = d.get("enrollment_id") if s == 200 else None
step("enroll: permintaan terkirim (PENDING)", s == 200 and bool(enrollment_id), (s, d.get("status")))

s, d = w("/device", tok=tok); d = unwrap(d)
step("device: menunggu persetujuan", d.get("enrollment_status") == "PENDING", d.get("enrollment_status"))

# ---------- APPROVE oleh admin/mandor ----------
s, d = v1("/auth/login", {"login_id": "admin@luminpark.id", "password": ADMIN_PASSWORD})
admin_tok = (unwrap(d) or {}).get("access_token")
step("login admin OK", s == 200 and bool(admin_tok), s)

s, d = v1(f"/device-enrollments/{enrollment_id}/approve", {"reason": "e2e verifikasi"},
          tok=admin_tok, extra_headers={"X-Tenant-ID": TENANT_ID})
step("approve perangkat OK", s == 200, (s, err_code(d)))

s, d = w("/device", tok=tok); d = unwrap(d)
step("device: terdaftar & aktif", s == 200 and d.get("enrolled") is True, d.get("enrolled"))

# ---------- SHIFT ----------
s, sh = w("/shift", tok=tok); sh = unwrap(sh)
step(f"shift: terjadwal hari ini (project {PROJECT['code']})",
     sh.get("scheduled") is True and len(sh.get("projects", [])) >= 1,
     (sh.get("scheduled"), [p.get("code") for p in sh.get("projects", [])]))


# ---------- helper submit bertanda tangan ----------
def submit(proj, evtype, dev_):
    s, c = w("/challenge", {"event_type": evtype, "project_id": proj["id"]}, tok=tok)
    if s != 200: return s, err_code(c), None
    c = unwrap(c)
    la, lo = offset(proj["latitude"], proj["longitude"])
    lat, lon = round(la, 6), round(lo, 6)
    captured = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    body = {"challenge_id": c["challenge_id"], "challenge": c["challenge"],
            "event_type": evtype, "project_id": proj["id"],
            "latitude": lat, "longitude": lon, "accuracy_m": 8.0,
            "captured_at_client": captured}
    body["signature"] = dev_.sign_b64(signed_payload(body))
    s, r = w("/events", body, tok=tok)
    return s, c, r


# ---------- NEGATIF: perangkat LAIN (HP teman) ditolak ----------
s, c, r = submit(PROJECT, "CHECK_IN", Device())
step("check-in dari perangkat LAIN ditolak (INVALID_ATTENDANCE_SIGNATURE)",
     err_code(r) == "INVALID_ATTENDANCE_SIGNATURE", (s, err_code(r)))

# ---------- alur normal pakai perangkat sah ----------
s, c, r = submit(PROJECT, "CHECK_IN", dev); r = unwrap(r) if isinstance(r, dict) else {}
step(f"check-in {PROJECT['code']} VALID", r.get("status") == "VALID", (s, r.get("status"), r.get("reason_code")))

s, c, r = submit(PROJECT, "CHECK_IN", dev)
step("duplikat check-in ditolak", c == "ATTENDANCE_ALREADY_RECORDED", (s, c))

# ---------- CHECK-OUT ----------
s, c, r = submit(PROJECT, "CHECK_OUT", dev); r = unwrap(r) if isinstance(r, dict) else {}
step("check-out VALID", r.get("status") == "VALID", (s, r.get("status")))

s, sh = w("/shift", tok=tok); sh = unwrap(sh)
step("open_shift kosong setelah check-out", sh.get("open_shift") is None)

s, c, r = submit(PROJECT, "CHECK_OUT", dev)
step("check-out tanpa shift ditolak (CHECK_IN_REQUIRED)", c == "CHECK_IN_REQUIRED", (s, c))

n_pass = sum(1 for _, ok, _ in results if ok)
print(f"\n=== E2E ANTI-TITIP: {n_pass}/{len(results)} PASS ===")
sys.exit(0 if n_pass == len(results) else 1)
