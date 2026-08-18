"""M3 signed attendance proof and geofence.

Revision ID: 0004_m3_attendance_proof
Revises: 0003_m2_device_enrollment
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str="0004_m3_attendance_proof"
down_revision: str | None="0003_m2_device_enrollment"
branch_labels: str | Sequence[str] | None=None
depends_on: str | Sequence[str] | None=None

def upgrade() -> None:
    attendance_type=sa.Enum("CHECK_IN","CHECK_OUT",name="attendancetype")
    attendance_status=sa.Enum("VALID","REVIEW","REJECTED",name="attendancestatus")
    op.create_table("attendance_challenges",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("projects.id"),nullable=False),sa.Column("event_type",attendance_type,nullable=False),sa.Column("challenge_hash",sa.String(64),nullable=False),sa.Column("work_date",sa.Date(),nullable=False),sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),sa.Column("used_at",sa.DateTime(timezone=True)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    for col in ("tenant_id","worker_id","project_id","work_date","expires_at"): op.create_index(f"ix_attendance_challenges_{col}","attendance_challenges",[col])
    op.create_table("attendance_events",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("projects.id"),nullable=False),sa.Column("device_binding_id",sa.String(36),sa.ForeignKey("device_bindings.id"),nullable=False),sa.Column("challenge_id",sa.String(36),sa.ForeignKey("attendance_challenges.id"),nullable=False,unique=True),sa.Column("event_type",attendance_type,nullable=False),sa.Column("status",attendance_status,nullable=False),sa.Column("reason_code",sa.String(80)),sa.Column("work_date",sa.Date(),nullable=False),sa.Column("server_time",sa.DateTime(timezone=True),nullable=False),sa.Column("captured_at_client",sa.DateTime(timezone=True),nullable=False),sa.Column("latitude",sa.Float(),nullable=False),sa.Column("longitude",sa.Float(),nullable=False),sa.Column("accuracy_m",sa.Float(),nullable=False),sa.Column("distance_m",sa.Float(),nullable=False),sa.Column("signature",sa.Text(),nullable=False),sa.Column("reviewed_at",sa.DateTime(timezone=True)),sa.Column("reviewed_by",sa.String(36),sa.ForeignKey("users.id")),sa.Column("review_reason",sa.String(300)))
    for col in ("tenant_id","worker_id","project_id","device_binding_id","event_type","status","work_date","server_time"): op.create_index(f"ix_attendance_events_{col}","attendance_events",[col])
    op.create_index("uq_counted_attendance_event","attendance_events",["tenant_id","worker_id","work_date","event_type"],unique=True,postgresql_where=sa.text("status IN ('VALID','REVIEW')"),sqlite_where=sa.text("status IN ('VALID','REVIEW')"))

def downgrade() -> None:
    op.drop_table("attendance_events"); op.drop_table("attendance_challenges")
