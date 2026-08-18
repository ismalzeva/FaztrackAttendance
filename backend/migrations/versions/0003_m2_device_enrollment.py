"""M2 verified device enrollment.

Revision ID: 0003_m2_device_enrollment
Revises: 0002_m1_master_data
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str="0003_m2_device_enrollment"
down_revision: str | None="0002_m1_master_data"
branch_labels: str | Sequence[str] | None=None
depends_on: str | Sequence[str] | None=None

def upgrade() -> None:
    op.add_column("workers",sa.Column("pin_hash",sa.String(300),nullable=True))
    op.create_table("device_challenges",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("challenge_hash",sa.String(64),nullable=False),sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),sa.Column("used_at",sa.DateTime(timezone=True)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    for col in ("tenant_id","worker_id","expires_at"): op.create_index(f"ix_device_challenges_{col}","device_challenges",[col])
    enrollment_status=sa.Enum("PENDING","APPROVED","REJECTED","SUPERSEDED",name="enrollmentstatus")
    op.create_table("device_enrollments",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("public_key_jwk",sa.Text(),nullable=False),sa.Column("public_key_thumbprint",sa.String(64),nullable=False),sa.Column("device_label",sa.String(120),nullable=False),sa.Column("status",enrollment_status,nullable=False),sa.Column("requested_at",sa.DateTime(timezone=True),nullable=False),sa.Column("reviewed_at",sa.DateTime(timezone=True)),sa.Column("reviewed_by",sa.String(36),sa.ForeignKey("users.id")),sa.Column("rejection_reason",sa.String(300)))
    for col in ("tenant_id","worker_id","public_key_thumbprint","status"): op.create_index(f"ix_device_enrollments_{col}","device_enrollments",[col])
    device_status=sa.Enum("ACTIVE","REVOKED",name="devicestatus")
    op.create_table("device_bindings",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("worker_id",sa.String(36),sa.ForeignKey("workers.id"),nullable=False),sa.Column("enrollment_id",sa.String(36),sa.ForeignKey("device_enrollments.id"),nullable=False,unique=True),sa.Column("public_key_jwk",sa.Text(),nullable=False),sa.Column("public_key_thumbprint",sa.String(64),nullable=False),sa.Column("device_label",sa.String(120),nullable=False),sa.Column("status",device_status,nullable=False),sa.Column("activated_at",sa.DateTime(timezone=True),nullable=False),sa.Column("revoked_at",sa.DateTime(timezone=True)),sa.Column("revoked_by",sa.String(36),sa.ForeignKey("users.id")),sa.Column("revoke_reason",sa.String(300)))
    for col in ("tenant_id","worker_id","public_key_thumbprint","status"): op.create_index(f"ix_device_bindings_{col}","device_bindings",[col])
    op.create_index("uq_active_device_per_worker","device_bindings",["tenant_id","worker_id"],unique=True,postgresql_where=sa.text("status = 'ACTIVE'"),sqlite_where=sa.text("status = 'ACTIVE'"))

def downgrade() -> None:
    op.drop_table("device_bindings"); op.drop_table("device_enrollments"); op.drop_table("device_challenges"); op.drop_column("workers","pin_hash")
