"""m2d_operational_rule_engine

Revision ID: 726fa9455e03
Revises: 0b4d5c9f09b8
Create Date: 2026-08-21 19:48:51.113266
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '726fa9455e03'
down_revision: Union[str, None] = '0b4d5c9f09b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rule_evaluations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('employee_id', sa.String(length=36), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('shift_id', sa.String(length=36), nullable=True),
        sa.Column('rule_code', sa.String(length=80), nullable=False),
        sa.Column('rule_version_id', sa.String(length=36), nullable=True),
        sa.Column('source_checkpoint_result_id', sa.String(length=36), nullable=True),
        sa.Column('source_canonical_event_id', sa.String(length=36), nullable=True),
        sa.Column('equipment_id', sa.String(length=36), nullable=True),
        sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.Enum('PASS', 'FAIL', 'NOT_APPLICABLE', 'CONFIG_INCOMPLETE', 'BLOCKED_POLICY_DECISION', name='ruleevaluationstatus'), nullable=False),
        sa.Column('severity', sa.Enum('CRITICAL', 'WARNING', 'INFO', name='ruleseverity'), nullable=False),
        sa.Column('actual_value', sa.String(length=200), nullable=True),
        sa.Column('expected_value', sa.String(length=200), nullable=True),
        sa.Column('evidence_json', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('evidence_key', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['rule_version_id'], ['rule_versions.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shift_templates.id']),
        sa.ForeignKeyConstraint(['source_canonical_event_id'], ['canonical_attendance_events.id']),
        sa.ForeignKeyConstraint(['source_checkpoint_result_id'], ['checkpoint_validation_results.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'employee_id', 'operating_date', 'rule_code',
            'rule_version_id', 'evidence_key',
            name='uq_rule_eval_identity',
        ),
    )
    op.create_index('ix_rule_eval_tenant_date', 'rule_evaluations', ['tenant_id', 'operating_date'], unique=False)
    op.create_index('ix_rule_eval_employee', 'rule_evaluations', ['tenant_id', 'employee_id', 'operating_date'], unique=False)
    op.create_index('ix_rule_eval_rule_code', 'rule_evaluations', ['tenant_id', 'rule_code'], unique=False)
    op.create_index('ix_rule_evaluations_employee_id', 'rule_evaluations', ['employee_id'], unique=False)
    op.create_index('ix_rule_evaluations_tenant_id', 'rule_evaluations', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_rule_evaluations_tenant_id', table_name='rule_evaluations')
    op.drop_index('ix_rule_evaluations_employee_id', table_name='rule_evaluations')
    op.drop_index('ix_rule_eval_rule_code', table_name='rule_evaluations')
    op.drop_index('ix_rule_eval_employee', table_name='rule_evaluations')
    op.drop_index('ix_rule_eval_tenant_date', table_name='rule_evaluations')
    op.drop_table('rule_evaluations')
    # SQLite stores enums as VARCHAR — no DROP TYPE needed.
    # For PostgreSQL: op.execute("DROP TYPE IF EXISTS ruleevaluationstatus")
    #                op.execute("DROP TYPE IF EXISTS ruleseverity")
