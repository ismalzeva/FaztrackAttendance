import csv
import io
import re
from datetime import date, time

import httpx

TABS=("Projects","Workers","Assignments","Schedules","Supervisors")
REQUIRED={
    "Projects":{"project_code","project_name","latitude","longitude","radius_m","work_start","work_end"},
    "Workers":{"worker_code","worker_name"},
    "Assignments":{"worker_code","project_code","work_date"},
    "Schedules":{"worker_code","work_date","start_time","end_time","is_working_day"},
    "Supervisors":{"login_id","project_code"},
}

def spreadsheet_id(value: str) -> str:
    match=re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)",value)
    if match: return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}",value): return value
    raise ValueError("INVALID_SPREADSHEET_URL")

def read_google_sheet(book_id: str) -> dict[str,list[dict[str,str]]]:
    result={}
    with httpx.Client(timeout=20,follow_redirects=True) as client:
        for tab in TABS:
            url=f"https://docs.google.com/spreadsheets/d/{book_id}/gviz/tq"
            response=client.get(url,params={"tqx":"out:csv","sheet":tab})
            response.raise_for_status()
            result[tab]=[{str(k).strip():str(v).strip() for k,v in row.items()} for row in csv.DictReader(io.StringIO(response.text))]
    return result

def _bool(value: str) -> bool:
    normalized=value.strip().lower()
    if normalized in {"true","1","yes","ya"}: return True
    if normalized in {"false","0","no","tidak"}: return False
    raise ValueError("must be TRUE or FALSE")

def validate(raw: dict[str,list[dict[str,str]]]) -> tuple[dict,list[dict]]:
    errors=[]; clean={tab:[] for tab in TABS}
    for tab in TABS:
        rows=raw.get(tab,[])
        headers=set(rows[0]) if rows else set()
        missing=REQUIRED[tab]-headers
        if missing: errors.append({"tab":tab,"row":1,"error":f"missing columns: {', '.join(sorted(missing))}"}); continue
        for number,row in enumerate(rows,start=2):
            try:
                item=dict(row)
                if tab=="Projects":
                    item.update(latitude=float(row["latitude"]),longitude=float(row["longitude"]),radius_m=int(row["radius_m"]),work_start=time.fromisoformat(row["work_start"]),work_end=time.fromisoformat(row["work_end"]))
                    if item["radius_m"] < 25: raise ValueError("radius_m must be at least 25")
                elif tab=="Workers": item["is_active"]=_bool(row.get("is_active","TRUE"))
                elif tab=="Assignments": item["work_date"]=date.fromisoformat(row["work_date"])
                elif tab=="Schedules": item.update(work_date=date.fromisoformat(row["work_date"]),start_time=time.fromisoformat(row["start_time"]),end_time=time.fromisoformat(row["end_time"]),is_working_day=_bool(row["is_working_day"]))
                clean[tab].append(item)
            except (ValueError,TypeError) as exc: errors.append({"tab":tab,"row":number,"error":str(exc)})
    projects={r["project_code"] for r in clean["Projects"]}; workers={r["worker_code"] for r in clean["Workers"]}
    for tab in ("Assignments","Supervisors"):
        for number,row in enumerate(clean[tab],start=2):
            if row["project_code"] not in projects: errors.append({"tab":tab,"row":number,"error":"unknown project_code"})
    for tab in ("Assignments","Schedules"):
        for number,row in enumerate(clean[tab],start=2):
            if row["worker_code"] not in workers: errors.append({"tab":tab,"row":number,"error":"unknown worker_code"})
    return clean,errors

def serializable(clean: dict) -> dict:
    return {tab:[{k:(v.isoformat() if isinstance(v,(date,time)) else v) for k,v in row.items()} for row in rows] for tab,rows in clean.items()}
