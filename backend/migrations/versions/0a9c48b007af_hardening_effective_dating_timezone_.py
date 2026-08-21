"""hardening_effective_dating_timezone_geofence

Revision ID: 0a9c48b007af
Revises: 7d30eba67c93
Create Date: 2026-08-21 16:36:12.495686
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0a9c48b007af'
down_revision: Union[str, None] = '7d30eba67c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- EmployeeMeta: remove 1:1 unique, add effective-dated unique ---
    with op.batch_alter_table("employee_meta") as batch:
        batch.drop_constraint("uq_employee_meta_worker", type_="unique")
        batch.create_unique_constraint(
            "uq_employee_meta_worker_from", ["tenant_id", "worker_id", "effective_from"]
        )
        batch.create_index("ix_employee_meta_tenant_worker", ["tenant_id", "worker_id"])
        batch.create_index("ix_employee_meta_worker_id", ["worker_id"])

    # --- Competency: remove unique on (tenant, emp, type), add index ---
    with op.batch_alter_table("competencies") as batch:
        batch.drop_constraint("uq_competency_emp_type", type_="unique")
        batch.create_index("ix_competency_emp_type", ["tenant_id", "employee_id", "equipment_type"])
        batch.create_index("ix_competency_tenant", ["tenant_id"])

    # --- RosterAssignment: add rule_version_id FK ---
    with op.batch_alter_table("roster_assignments") as batch:
        batch.add_column(sa.Column("rule_version_id", sa.String(36), nullable=True))
        batch.create_index("ix_roster_assignments_rule_version_id", ["rule_version_id"])
        batch.create_foreign_key(
            "fk_roster_assignment_rule_version",
            "rule_versions",
            ["rule_version_id"],
            ["id"],
        )

    # --- Site: radius_m nullable ---
    with op.batch_alter_table("sites") as batch:
        batch.alter_column("radius_m", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("sites") as batch:
        batch.alter_column("radius_m", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("roster_assignments") as batch:
        batch.drop_constraint("fk_roster_assignment_rule_version", type_="foreignkey")
        batch.drop_index("ix_roster_assignments_rule_version_id")
        batch.drop_column("rule_version_id")

    with op.batch_alter_table("competencies") as batch:
        batch.drop_index("ix_competency_tenant")
        batch.drop_index("ix_competency_emp_type")
        batch.create_unique_constraint("uq_competency_emp_type", ["tenant_id", "employee_id", "equipment_type"])

    with op.batch_alter_table("employee_meta") as batch:
        batch.drop_index("ix_employee_meta_worker_id")
        batch.drop_index("ix_employee_meta_tenant_worker")
        batch.drop_constraint("uq_employee_meta_worker_from", type_="unique")
        batch.create_unique_constraint("uq_employee_meta_worker", ["tenant_id", "worker_id"])
