"""M0 tenant, identity, scope, and audit foundation.

Revision ID: 0001_m0_foundation
Revises:
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_m0_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tenant_status = sa.Enum("ACTIVE", "SUSPENDED", name="tenantstatus")
    membership_status = sa.Enum(
        "ACTIVE", "SUSPENDED", "REVOKED", name="membershipstatus"
    )
    role_code = sa.Enum(
        "OWNER", "ADMIN", "SUPERVISOR", "WORKER", "AUDITOR", name="rolecode"
    )

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("status", tenant_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_code", "tenants", ["code"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("login_id", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_login_id", "users", ["login_id"], unique=True)

    op.create_table(
        "memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", role_code, nullable=False),
        sa.Column("status", membership_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "role", name="uq_membership_role"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "project_scopes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("membership_id", sa.String(36), sa.ForeignKey("memberships.id"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("tenant_id", "membership_id", "project_id", name="uq_project_scope"),
    )
    op.create_index("ix_project_scopes_tenant_id", "project_scopes", ["tenant_id"])
    op.create_index("ix_project_scopes_membership_id", "project_scopes", ["membership_id"])
    op.create_index("ix_project_scopes_project_id", "project_scopes", ["project_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36)),
        sa.Column("actor_user_id", sa.String(36)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(120), nullable=False),
        sa.Column("entity_id", sa.String(36)),
        sa.Column("reason", sa.Text()),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("tenant_id", "actor_user_id", "action", "entity_type", "correlation_id", "created_at"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])
    op.create_index(
        "ix_audit_tenant_entity_time",
        "audit_events",
        ["tenant_id", "entity_type", "entity_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("project_scopes")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("tenants")
