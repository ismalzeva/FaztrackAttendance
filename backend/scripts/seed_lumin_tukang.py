"""Terapkan PIN unik untuk 5 tukang (EMP-020..EMP-024) + enable multi-checkin.

Idempotent. TIDAK membuat worker/project baru — master data (EMP-001..024,
PRJ-01..03) datang dari `seed_lumin_standalone.py`. TIDAK menampilkan nilai PIN.
Membaca kredensial dari `.env.lumin` (sama seperti standalone seed).
"""
import json
import os
import sys

BACKEND_DIR = "/home/ubuntu/FaztrackAttendance/backend"
sys.path.insert(0, BACKEND_DIR)


def _load_env_file(path):
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out


_env = _load_env_file(os.path.join(BACKEND_DIR, ".env.lumin"))
DB_URL = os.environ.get(
    "FAZTRACK_DATABASE_URL",
    _env.get("FAZTRACK_DATABASE_URL"),
)
if not DB_URL:
    print("ERROR: FAZTRACK_DATABASE_URL tidak ditemukan")
    sys.exit(1)

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from app import models  # noqa: E402,F401
from app.models import Tenant, Worker  # noqa: E402

TENANT_ID = "lumin-park-001"
PINFILE = os.path.join(BACKEND_DIR, "lumin_pins.json")

# 5 tukang sesuai master data (Pekerja Bangunan = EMP-020..EMP-024)
TUKANG_CODES = ["EMP-020", "EMP-021", "EMP-022", "EMP-023", "EMP-024"]


def hash_pin(pin: str) -> str:
    from app.security import hash_password
    return hash_password(pin)


def load_unique_pins() -> dict:
    try:
        with open(PINFILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def main() -> None:
    default_pin = os.environ.get("FAZTRACK_DEMO_WORKER_PIN") or _env.get("FAZTRACK_DEMO_WORKER_PIN")
    unique_pins = load_unique_pins()

    db = SessionLocal()
    try:
        t = db.get(Tenant, TENANT_ID)
        if t is None:
            print("ERROR: tenant tidak ada — jalankan seed_lumin_standalone.py dulu.")
            sys.exit(1)
        t.allow_multi_checkin = True
        db.add(t)

        for code in TUKANG_CODES:
            w = db.scalar(select(Worker).where(Worker.tenant_id == TENANT_ID,
                                               Worker.code == code))
            if w is None:
                print(f"WARN: worker {code} tidak ada — skip")
                continue
            unique = unique_pins.get(code) or default_pin
            assert unique, f"PIN utk {code} tidak tersedia (lumin_pins.json / FAZTRACK_DEMO_WORKER_PIN)"
            w.pin_hash = hash_pin(unique)
            db.add(w)
        db.commit()

        print("\nVerifikasi (nilai PIN tidak ditampilkan):")
        for code in TUKANG_CODES:
            w = db.scalar(select(Worker).where(Worker.tenant_id == TENANT_ID,
                                               Worker.code == code))
            if w is None:
                print(f"  {code}: MISSING")
                continue
            print(f"  {code} {w.name}: has_pin={bool(w.pin_hash)}")
        t2 = db.get(Tenant, TENANT_ID)
        print(f"  allow_multi_checkin={t2.allow_multi_checkin}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
