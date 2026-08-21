"""m2b_checkpoint_engine

Revision ID: 8c07e14aa3d8
Revises: af69ae8e38de
Create Date: 2026-08-21 17:18:44.568626
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '8c07e14aa3d8'
down_revision: Union[str, None] = 'af69ae8e38de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New tables (no existing data, safe for SQLite) ---
    op.create_table('checkpoint_event_mappings',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('source', sa.String(80), nullable=False),
        sa.Column('event_type', sa.String(80), nullable=False),
        sa.Column('checkpoint_type', sa.String(80), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'source', 'event_type', name='uq_checkpoint_mapping'),
    )
    op.create_index('ix_checkpoint_mapping_tenant', 'checkpoint_event_mappings', ['tenant_id'])

    op.create_table('checkpoint_validation_results',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('canonical_event_id', sa.String(36), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('checkpoint_type', sa.String(80), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('shift_id', sa.String(36), nullable=True),
        sa.Column('policy_id', sa.String(36), nullable=True),
        sa.Column('rule_version_id', sa.String(36), nullable=True),
        sa.Column('validation_status', sa.String(30), nullable=False),
        sa.Column('detected_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason_code', sa.String(100), nullable=True),
        sa.Column('evidence_json', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['canonical_event_id'], ['canonical_attendance_events.id']),
        sa.ForeignKeyConstraint(['policy_id'], ['checkpoint_policies.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shift_templates.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('canonical_event_id', 'checkpoint_type', name='uq_checkpoint_result_event_type'),
    )
    op.create_index('ix_checkpoint_result_employee', 'checkpoint_validation_results', ['tenant_id', 'employee_id', 'operating_date'])
    op.create_index('ix_checkpoint_result_tenant_date', 'checkpoint_validation_results', ['tenant_id', 'operating_date'])

    op.create_table('missing_checkpoint_results',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('shift_id', sa.String(36), nullable=True),
        sa.Column('checkpoint_type', sa.String(80), nullable=False),
        sa.Column('policy_id', sa.String(36), nullable=True),
        sa.Column('expected_window_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expected_window_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('detection_status', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['policy_id'], ['checkpoint_policies.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shift_templates.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'employee_id', 'operating_date', 'checkpoint_type', name='uq_missing_checkpoint'),
    )
    op.create_index('ix_missing_checkpoint_tenant_date', 'missing_checkpoint_results', ['tenant_id', 'operating_date'])

    # --- Extend checkpoint_policies (existing table, needs batch for SQLite) ---
    with op.batch_alter_table('checkpoint_policies') as batch:
        batch.add_column(sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'))
        batch.add_column(sa.Column('sequence_order', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('tolerance_min', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('default_validation_behavior', sa.String(30), nullable=False, server_default='CONFIG_INCOMPLETE'))
        batch.add_column(sa.Column('effective_from', sa.Date(), nullable=False, server_default=sa.text("'2026-01-01'")))
        batch.add_column(sa.Column('effective_to', sa.Date(), nullable=True))
        batch.add_column(sa.Column('rule_version_id', sa.String(36), nullable=True))
        batch.create_index('ix_checkpoint_policy_tenant', ['tenant_id', 'enabled'])
        batch.create_foreign_key('fk_checkpoint_policy_rule_version', 'rule_versions', ['rule_version_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('checkpoint_policies') as batch:
        batch.drop_constraint('fk_checkpoint_policy_rule_version', type_='foreignkey')
        batch.drop_index('ix_checkpoint_policy_tenant')
        batch.drop_column('rule_version_id')
        batch.drop_column('effective_to')
        batch.drop_column('effective_from')
        batch.drop_column('default_validation_behavior')
        batch.drop_column('tolerance_min')
        batch.drop_column('sequence_order')
        batch.drop_column('enabled')

    op.drop_table('missing_checkpoint_results')
    op.drop_table('checkpoint_validation_results')
    op.drop_table('checkpoint_event_mappings')
