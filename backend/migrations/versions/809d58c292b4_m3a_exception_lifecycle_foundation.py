"""m3a_exception_lifecycle_foundation

Revision ID: 809d58c292b4
Revises: 726fa9455e03
Create Date: 2026-08-21 21:44:38.167031
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '809d58c292b4'
down_revision: Union[str, None] = '726fa9455e03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── M3A: Exception Lifecycle Foundation ──
    # Only create new tables. No modifications to existing tables.

    op.create_table(
        'exception_cases',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('exception_type', sa.String(length=80), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('employee_id', sa.String(length=36), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('shift_id', sa.String(length=36), nullable=True),
        sa.Column('equipment_id', sa.String(length=36), nullable=True),
        sa.Column('site_id', sa.String(length=36), nullable=True),
        sa.Column('source_type', sa.String(length=80), nullable=False),
        sa.Column('source_id', sa.String(length=36), nullable=False),
        sa.Column('rule_version_id', sa.String(length=36), nullable=True),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('waived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_owner_id', sa.String(length=36), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['rule_version_id'], ['rule_versions.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shift_templates.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'source_type', 'source_id', 'exception_type',
            name='uq_exception_source',
        ),
    )
    op.create_index('ix_exception_case_employee', 'exception_cases',
                     ['tenant_id', 'employee_id', 'operating_date'], unique=False)
    op.create_index('ix_exception_case_rule_code', 'exception_cases',
                     ['tenant_id', 'exception_type'], unique=False)
    op.create_index('ix_exception_case_status', 'exception_cases',
                     ['tenant_id', 'status'], unique=False)
    op.create_index('ix_exception_case_tenant_date', 'exception_cases',
                     ['tenant_id', 'operating_date'], unique=False)
    op.create_index(op.f('ix_exception_cases_employee_id'), 'exception_cases',
                     ['employee_id'], unique=False)
    op.create_index(op.f('ix_exception_cases_tenant_id'), 'exception_cases',
                     ['tenant_id'], unique=False)

    op.create_table(
        'exception_actions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('exception_id', sa.String(length=36), nullable=False),
        sa.Column('action_type', sa.String(length=20), nullable=False),
        sa.Column('actor_user_id', sa.String(length=36), nullable=True),
        sa.Column('action_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('previous_status', sa.String(length=20), nullable=False),
        sa.Column('new_status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('evidence_ref', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['exception_id'], ['exception_cases.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_exception_action_case', 'exception_actions',
                     ['exception_id'], unique=False)
    op.create_index('ix_exception_action_tenant', 'exception_actions',
                     ['tenant_id'], unique=False)
    op.create_index(op.f('ix_exception_actions_exception_id'), 'exception_actions',
                     ['exception_id'], unique=False)
    op.create_index(op.f('ix_exception_actions_tenant_id'), 'exception_actions',
                     ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_exception_actions_tenant_id'), table_name='exception_actions')
    op.drop_index(op.f('ix_exception_actions_exception_id'), table_name='exception_actions')
    op.drop_index('ix_exception_action_tenant', table_name='exception_actions')
    op.drop_index('ix_exception_action_case', table_name='exception_actions')
    op.drop_table('exception_actions')

    op.drop_index(op.f('ix_exception_cases_tenant_id'), table_name='exception_cases')
    op.drop_index(op.f('ix_exception_cases_employee_id'), table_name='exception_cases')
    op.drop_index('ix_exception_case_tenant_date', table_name='exception_cases')
    op.drop_index('ix_exception_case_status', table_name='exception_cases')
    op.drop_index('ix_exception_case_rule_code', table_name='exception_cases')
    op.drop_index('ix_exception_case_employee', table_name='exception_cases')
    op.drop_table('exception_cases')
