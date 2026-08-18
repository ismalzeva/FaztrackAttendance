"""M1 master data and Google Sheets imports.

Revision ID: 0002_m1_master_data
Revises: 0001_m0_foundation
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str="0002_m1_master_data"
down_revision: str | None="0001_m0_foundation"
branch_labels: str | Sequence[str] | None=None
depends_on: str | Sequence[str] | None=None

def upgrade() -> None:
    op.create_table("projects",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("code",sa.String(50),nullable=False),sa.Column("name",sa.String(200),nullable=False),sa.Column("latitude",sa.Float(),nullable=False),sa.Column("longitude",sa.Float(),nullable=False),sa.Column("geofence_radius_m",sa.Integer(),nullable=False),sa.Column("work_start",sa.Time(),nullable=False),sa.Column("work_end",sa.Time(),nullable=False),sa.Column("is_active",sa.Boolean(),nullable=False),sa.UniqueConstraint("tenant_id","code",name="uq_project_tenant_code"))
    op.create_index("ix_projects_tenant_id","projects",["tenant_id"])
    op.create_table("workers",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("code",sa.String(50),nullable=False),sa.Column("name",sa.String(200),nullable=False),sa.Column("phone",sa.String(40)),sa.Column("is_active",sa.Boolean(),nullable=False),sa.UniqueConstraint("tenant_id","code",name="uq_worker_tenant_code"))
    op.create_index("ix_workers_tenant_id","workers",["tenant_id"])
    op.create_table("assignments",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("projects.id"),nullable=False),sa.Column("work_date",sa.Date(),nullable=False),sa.UniqueConstraint("tenant_id","worker_id","work_date",name="uq_worker_assignment_date"))
    for col in ("tenant_id","worker_id","project_id","work_date"): op.create_index(f"ix_assignments_{col}","assignments",[col])
    op.create_table("work_schedules",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("work_date",sa.Date(),nullable=False),sa.Column("start_time",sa.Time(),nullable=False),sa.Column("end_time",sa.Time(),nullable=False),sa.Column("is_working_day",sa.Boolean(),nullable=False),sa.UniqueConstraint("tenant_id","worker_id","work_date",name="uq_worker_schedule_date"))
    for col in ("tenant_id","worker_id","work_date"): op.create_index(f"ix_work_schedules_{col}","work_schedules",[col])
    op.create_table("supervisor_projects",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("membership_id",sa.String(36),sa.ForeignKey("memberships.id"),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("projects.id"),nullable=False),sa.UniqueConstraint("tenant_id","membership_id","project_id",name="uq_supervisor_project"))
    for col in ("tenant_id","membership_id","project_id"): op.create_index(f"ix_supervisor_projects_{col}","supervisor_projects",[col])
    import_status=sa.Enum("PREVIEW","CONFIRMED","REJECTED",name="importstatus")
    op.create_table("import_batches",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("spreadsheet_id",sa.String(160),nullable=False),sa.Column("status",import_status,nullable=False),sa.Column("payload_json",sa.Text(),nullable=False),sa.Column("summary_json",sa.Text(),nullable=False),sa.Column("created_by",sa.String(36),sa.ForeignKey("users.id"),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("confirmed_at",sa.DateTime(timezone=True)))
    op.create_index("ix_import_batches_tenant_id","import_batches",["tenant_id"]); op.create_index("ix_import_batches_status","import_batches",["status"])

def downgrade() -> None:
    for table in ("import_batches","supervisor_projects","work_schedules","assignments","workers","projects"): op.drop_table(table)
