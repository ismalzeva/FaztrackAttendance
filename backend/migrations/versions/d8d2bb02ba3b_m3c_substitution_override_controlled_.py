"""m3c_substitution_override_controlled_decision.

Adds:
- exception_decisions: controlled decision for substitution/override
- exception_decision_actions: immutable audit trail for decisions

Revision ID: d8d2bb02ba3b
Revises: bf68f5fc6157
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa

revision = "d8d2bb02ba3b"
down_revision = "bf68f5fc6157"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "exception_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), index=True),
        sa.Column("exception_id", sa.String(36), sa.ForeignKey("exception_cases.id"), index=True),
        sa.Column("decision_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(100), nullable=True),
        sa.Column("reason_text", sa.Text, nullable=True),
        sa.Column("planned_worker_id", sa.String(36), nullable=True),
        sa.Column("planned_equipment_id", sa.String(36), nullable=True),
        sa.Column("actual_worker_id", sa.String(36), nullable=True),
        sa.Column("actual_equipment_id", sa.String(36), nullable=True),
        sa.Column("requested_value", sa.String(200), nullable=True),
        sa.Column("previous_value", sa.String(200), nullable=True),
        sa.Column("rule_version_id", sa.String(36), sa.ForeignKey("rule_versions.id"), nullable=True),
        sa.Column("authorization_policy", sa.String(100), nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_decision_tenant_exception", "exception_decisions", ["tenant_id", "exception_id"])
    op.create_index("ix_decision_tenant_status", "exception_decisions", ["tenant_id", "status"])

    op.create_table(
        "exception_decision_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), index=True),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("exception_decisions.id"), index=True),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("action_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("evidence_ref", sa.Text, nullable=True),
        sa.Column("authorization_result", sa.String(100), nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_decision_action_decision", "exception_decision_actions", ["decision_id"])
    op.create_index("ix_decision_action_tenant", "exception_decision_actions", ["tenant_id"])


def downgrade():
    op.drop_table("exception_decision_actions")
    op.drop_table("exception_decisions")
