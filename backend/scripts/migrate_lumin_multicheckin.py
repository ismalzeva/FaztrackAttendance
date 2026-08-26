"""Migrasi skema Lumin untuk multi-checkin (idempotent). Tanpa output kredensial."""
import sys

sys.path.insert(0, "/home/ubuntu/FaztrackAttendance/backend")
from sqlalchemy import text
from app.database import engine

STMTS = [
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allow_multi_checkin BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS site_note VARCHAR(200)",
    "ALTER TABLE attendance_events ALTER COLUMN device_binding_id DROP NOT NULL",
    "ALTER TABLE attendance_events ALTER COLUMN challenge_id DROP NOT NULL",
    "DROP INDEX IF EXISTS uq_counted_attendance_event",
    """CREATE INDEX IF NOT EXISTS ix_counted_attendance_event ON attendance_events
       (tenant_id, worker_id, work_date, event_type)
       WHERE status IN ('VALID','REVIEW')""",
]

with engine.begin() as conn:
    for s in STMTS:
        conn.execute(text(s))
print("MIGRASI OK")
