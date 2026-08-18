"""Load the synthetic Lumin Park pilot through the public M1 API contract."""

import os

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Membership, RoleCode, Tenant, User
from app.security import hash_password

SHEET_URL="https://docs.google.com/spreadsheets/d/1tzHlQuUbFtCVzYFXERttKSM_UXANMcGc1Li8XnwilRU/edit"

def seed_identity() -> tuple[str,str]:
    password=os.environ["FAZTRACK_DEMO_SEED_PASSWORD"]
    with SessionLocal.begin() as db:
        tenant=db.scalar(select(Tenant).where(Tenant.code=="LUMIN-PILOT"))
        if not tenant:
            tenant=Tenant(code="LUMIN-PILOT",name="Lumin Park Property"); db.add(tenant); db.flush()
        admin=db.scalar(select(User).where(User.login_id=="admin.demo"))
        if not admin:
            admin=User(login_id="admin.demo",display_name="Admin Demo",password_hash=hash_password(password)); db.add(admin); db.flush()
        if not db.scalar(select(Membership).where(Membership.tenant_id==tenant.id,Membership.user_id==admin.id,Membership.role==RoleCode.ADMIN)):
            db.add(Membership(tenant_id=tenant.id,user_id=admin.id,role=RoleCode.ADMIN))
        for number in range(1,5):
            login_id=f"supervisor.{number:02d}"
            user=db.scalar(select(User).where(User.login_id==login_id))
            if not user:
                user=User(login_id=login_id,display_name=f"Supervisor Demo {number}",password_hash=hash_password(password)); db.add(user); db.flush()
            if not db.scalar(select(Membership).where(Membership.tenant_id==tenant.id,Membership.user_id==user.id,Membership.role==RoleCode.SUPERVISOR)):
                db.add(Membership(tenant_id=tenant.id,user_id=user.id,role=RoleCode.SUPERVISOR))
        return tenant.id,password

def main() -> None:
    tenant_id,password=seed_identity()
    with TestClient(app) as client:
        login=client.post("/api/v1/auth/login",json={"login_id":"admin.demo","password":password}); login.raise_for_status()
        headers={"Authorization":f"Bearer {login.json()['data']['access_token']}","X-Tenant-ID":tenant_id}
        preview=client.post("/api/v1/master-data/imports/google-sheets/preview",headers=headers,json={"spreadsheet_url":SHEET_URL}); preview.raise_for_status()
        result=preview.json()["data"]
        if result["errors"]: raise RuntimeError(result["errors"])
        confirm=client.post(f"/api/v1/master-data/imports/{result['batch_id']}/confirm",headers=headers); confirm.raise_for_status()
        worker_pin=os.environ.get("FAZTRACK_DEMO_WORKER_PIN")
        if worker_pin:
            from app.models import Worker
            with SessionLocal.begin() as db:
                for worker in db.scalars(select(Worker).where(Worker.tenant_id==tenant_id)).all():
                    if not worker.pin_hash: worker.pin_hash=hash_password(worker_pin)
        summary=client.get("/api/v1/master-data/summary",headers=headers); summary.raise_for_status()
        print({"preview":result,"confirm":confirm.json()["data"],"summary":summary.json()["data"]})

if __name__=="__main__": main()
