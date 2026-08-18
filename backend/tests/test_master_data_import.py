from datetime import time
from sqlalchemy import select

from app.models import Assignment, Project, SupervisorProject, Worker, WorkSchedule


RAW={
    "Projects":[{"project_code":"PRJ-01","project_name":"Lumin Park A","latitude":"-6.2","longitude":"106.8","radius_m":"150","work_start":"08:00","work_end":"17:00"}],
    "Workers":[{"worker_code":"EMP-001","worker_name":"Budi","phone":"0812","is_active":"TRUE"}],
    "Assignments":[{"worker_code":"EMP-001","project_code":"PRJ-01","work_date":"2026-08-17"}],
    "Schedules":[{"worker_code":"EMP-001","work_date":"2026-08-17","start_time":"08:00","end_time":"17:00","is_working_day":"TRUE"}],
    "Supervisors":[{"login_id":"supervisor.1","project_code":"PRJ-01"}],
}

def auth_headers(client,seeded):
    tenant,*_=seeded
    login=client.post("/api/v1/auth/login",json={"login_id":"ahmad","password":"secret123"})
    return {"Authorization":f"Bearer {login.json()['data']['access_token']}","X-Tenant-ID":tenant.id}

def test_preview_confirm_and_repeat_are_idempotent(client,seeded,db,monkeypatch):
    monkeypatch.setattr("app.master_data.read_google_sheet",lambda _:RAW)
    headers=auth_headers(client,seeded)
    preview=client.post("/api/v1/master-data/imports/google-sheets/preview",headers=headers,json={"spreadsheet_url":"https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz123456/edit"})
    assert preview.status_code==200
    assert preview.json()["data"]["errors"]==[]
    batch_id=preview.json()["data"]["batch_id"]
    confirmed=client.post(f"/api/v1/master-data/imports/{batch_id}/confirm",headers=headers)
    assert confirmed.status_code==200
    assert len(db.scalars(select(Project)).all())==1
    assert len(db.scalars(select(Worker)).all())==1
    assert len(db.scalars(select(Assignment)).all())==1
    assert len(db.scalars(select(WorkSchedule)).all())==1
    assert len(db.scalars(select(SupervisorProject)).all())==1
    repeated=client.post(f"/api/v1/master-data/imports/{batch_id}/confirm",headers=headers)
    assert repeated.status_code==200
    assert repeated.json()["data"]["idempotent"] is True
    assert len(db.scalars(select(Worker)).all())==1

def test_preview_rejects_unknown_reference(client,seeded,monkeypatch):
    invalid={key:[dict(row) for row in rows] for key,rows in RAW.items()}
    invalid["Assignments"][0]["project_code"]="UNKNOWN"
    monkeypatch.setattr("app.master_data.read_google_sheet",lambda _:invalid)
    response=client.post("/api/v1/master-data/imports/google-sheets/preview",headers=auth_headers(client,seeded),json={"spreadsheet_url":"https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz123456/edit"})
    assert response.status_code==200
    assert response.json()["data"]["status"]=="REJECTED"
    assert response.json()["data"]["errors"][0]["error"]=="unknown project_code"

def test_master_summary_is_tenant_scoped(client,seeded,db):
    tenant_a,tenant_b,_,_=seeded
    db.add(Project(tenant_id=tenant_b.id,code="SECRET",name="Other tenant",latitude=0,longitude=0,geofence_radius_m=100,work_start=time(8),work_end=time(17))); db.commit()
    response=client.get("/api/v1/master-data/summary",headers=auth_headers(client,seeded))
    assert response.status_code==200
    assert response.json()["data"]["projects"]==0
