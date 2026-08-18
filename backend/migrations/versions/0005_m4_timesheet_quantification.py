"""M4 timesheet quantification and closing.

Revision ID: 0005_m4_timesheet_quantification
Revises: 0004_m3_attendance_proof
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str="0005_m4_timesheet_quantification"
down_revision: str | None="0004_m3_attendance_proof"
branch_labels: str | Sequence[str] | None=None
depends_on: str | Sequence[str] | None=None

def upgrade() -> None:
    op.create_table("attendance_policies",sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),primary_key=True),sa.Column("late_grace_minutes",sa.Integer(),nullable=False),sa.Column("early_leave_grace_minutes",sa.Integer(),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_by",sa.String(36),sa.ForeignKey("users.id")))
    period_status=sa.Enum("OPEN","CLOSED",name="timesheetperiodstatus")
    op.create_table("timesheet_periods",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("date_from",sa.Date(),nullable=False),sa.Column("date_to",sa.Date(),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("projects.id")),sa.Column("scope_key",sa.String(36),nullable=False),sa.Column("status",period_status,nullable=False),sa.Column("snapshot_json",sa.Text()),sa.Column("snapshot_hash",sa.String(64)),sa.Column("closed_at",sa.DateTime(timezone=True)),sa.Column("closed_by",sa.String(36),sa.ForeignKey("users.id")),sa.UniqueConstraint("tenant_id","date_from","date_to","scope_key",name="uq_timesheet_period_scope"))
    for col in ("tenant_id","date_from","date_to","project_id","status"): op.create_index(f"ix_timesheet_periods_{col}","timesheet_periods",[col])

def downgrade() -> None:
    op.drop_table("timesheet_periods"); op.drop_table("attendance_policies")
