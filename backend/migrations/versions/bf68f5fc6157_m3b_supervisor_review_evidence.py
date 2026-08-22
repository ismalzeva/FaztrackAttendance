"""m3b_supervisor_review_evidence

Revision ID: bf68f5fc6157
Revises: 809d58c292b4
Create Date: 2026-08-22 01:15:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bf68f5fc6157"
down_revision: Union[str, None] = "809d58c292b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exception_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("exception_id", sa.String(36), sa.ForeignKey("exception_cases.id"), nullable=False, index=True),
        sa.Column("evidence_type", sa.String(30), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_by", sa.String(36), nullable=True),
        sa.Column("is_system_generated", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "exception_id", "evidence_type", "source_type", "source_id",
            name="uq_exception_evidence_source",
        ),
    )
    op.create_index("ix_exception_evidence_case", "exception_evidence", ["exception_id"])
    op.create_index("ix_exception_evidence_tenant", "exception_evidence", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("exception_evidence")
