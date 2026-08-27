"""Migrasi + seed Lumin: multi-checkin tenant, 2 project perumahan, 5 tukang bangunan.

Idempotent. Tidak menampilkan kredensial.
"""
import hashlib
import json
import os
import secrets
import sys

sys.path.insert(0, "/home/ubuntu/FaztrackAttendance/backend")

from sqlalchemy import select
from app.config import get_settings
from app.database import SessionLocal
from app.models import (Assignment, Project, Tenant, WorkSchedule, Worker)
import datetime as dt

TENANT_ID = "lumin-park-001"
TENANT_CODE = "lumin-park"
PINFILE = os.path.join(os.path.dirname(__file__), "..", "lumin_pins.json")

WORKERS = [
    ("TKN-APE", "Apes", "083186049749"),
    ("TKN-ALI", "Ali", "082285088368"),
    ("TKN-YUS", "Yus", "083182310429"),
    ("TKN-FER", "Ferdi", "085185783200"),
    ("TKN-RAP", "Rapi", "083844174344"),
]

PROJECTS = [
    # code, name, lat, lon, radius_m
    ("RUMAH-BLK-B2", "Perumahan Griya — Blok B No 2", -0.84290, 100.37310, 120, dt.time(8, 0), dt.time(17, 0)),
    ("RUMAH-BLK-A12", "Perumahan Griya — Blok A No 12", -0.84410, 100.37420, 120, dt.time(8, 0), dt.time(17, 0)),
]


def hash_pin(pin: str) -> str:
    from app.security import hash_password
    return hash_password(pin)


def load_unique_pins() -> dict[str, str]:
    """Baca PIN unik per worker dari lumin_pins.json (mode 600, tidak di-commit)."""
    try:
        with open(PINFILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main() -> None:
    default_pin = get_settings().demo_worker_pin
    unique_pins = load_unique_pins()

    db = SessionLocal()
    try:
        t = db.get(Tenant, TENANT_ID)
        t.allow_multi_checkin = True
        db.add(t)

        proj_ids = {}
        for code, name, lat, lon, radius, wstart, wend in PROJECTS:
            p = db.scalar(select(Project).where(Project.tenant_id == TENANT_ID, Project.code == code))
            if not p:
                p = Project(tenant_id=TENANT_ID, code=code, name=name,
                            latitude=lat, longitude=lon, geofence_radius_m=radius,
                            work_start=wstart, work_end=wend)
                db.add(p)
                db.flush()
                print(f"+ project {code}: {name}")
            proj_ids[code] = p.id

        today = dt.date.today()
        for code, name, phone in WORKERS:
            w = db.scalar(select(Worker).where(Worker.tenant_id == TENANT_ID, Worker.code == code))
            unique = unique_pins.get(code) or default_pin
            assert unique, f"PIN untuk {code} tidak tersedia (lumin_pins.json / FAZTRACK_DEMO_WORKER_PIN)"
            if not w:
                w = Worker(tenant_id=TENANT_ID, code=code, name=name, phone=phone,
                           pin_hash=hash_pin(unique))
                db.add(w)
                db.flush()
                print(f"+ worker {code} {name}")
            # PIN unik per worker: re-hash tiap run agar idempotent & konsisten dengan lumin_pins.json
            w.pin_hash = hash_pin(unique)
            if not db.scalar(select(Assignment).where(Assignment.worker_id == w.id,
                                                      Assignment.work_date == today)):
                base_project = proj_ids["RUMAH-BLK-B2"]
                db.add(Assignment(tenant_id=TENANT_ID, worker_id=w.id,
                                  project_id=base_project, work_date=today))
            if not db.scalar(select(WorkSchedule).where(WorkSchedule.worker_id == w.id,
                                                        WorkSchedule.work_date == today)):
                db.add(WorkSchedule(tenant_id=TENANT_ID, worker_id=w.id, work_date=today,
                                    start_time=dt.time(8, 0), end_time=dt.time(17, 0),
                                    is_working_day=True))
        db.commit()

        print("\nVerifikasi:")
        for code, name, _ in WORKERS:
            w = db.scalar(select(Worker).where(Worker.tenant_id == TENANT_ID, Worker.code == code))
            a = db.scalar(select(Assignment).where(Assignment.worker_id == w.id,
                                                   Assignment.work_date == today))
            s = db.scalar(select(WorkSchedule).where(WorkSchedule.worker_id == w.id,
                                                     WorkSchedule.work_date == today))
            print(f"  {code} {name}: assignment={'OK' if a else 'MISSING'} schedule={'OK' if s else 'MISSING'} has_pin={bool(w.pin_hash)}")
        t2 = db.get(Tenant, TENANT_ID)
        print(f"  allow_multi_checkin={t2.allow_multi_checkin}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
