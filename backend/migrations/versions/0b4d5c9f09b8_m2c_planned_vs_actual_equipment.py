"""m2c_planned_vs_actual_equipment

Revision ID: 0b4d5c9f09b8
Revises: 8c07e14aa3d8
Create Date: 2026-08-21 18:47:00.809358
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0b4d5c9f09b8'
down_revision: Union[str, None] = '8c07e14aa3d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New tables ──────────────────────────────────────────

    op.create_table('equipment_comparison_results',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('actual_assignment_id', sa.String(36), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('shift_id', sa.String(36), nullable=True),
        sa.Column('planned_equipment_id', sa.String(36), nullable=True),
        sa.Column('actual_equipment_id', sa.String(36), nullable=False),
        sa.Column('comparison_result', sa.String(30), nullable=False),
        sa.Column('planned_worker_id', sa.String(36), nullable=True),
        sa.Column('actual_worker_id', sa.String(36), nullable=False),
        sa.Column('reason_code', sa.String(100), nullable=True),
        sa.Column('rule_version_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['actual_assignment_id'], ['equipment_assignments_actual.id']),
        sa.ForeignKeyConstraint(['actual_equipment_id'], ['equipments.id']),
        sa.ForeignKeyConstraint(['planned_equipment_id'], ['equipments.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shift_templates.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('actual_assignment_id', name='uq_comparison_actual'),
    )
    op.create_index('ix_comparison_employee', 'equipment_comparison_results',
                     ['tenant_id', 'employee_id', 'operating_date'])
    op.create_index('ix_comparison_tenant_date', 'equipment_comparison_results',
                     ['tenant_id', 'operating_date'])
    op.create_index('ix_ecr_assignment', 'equipment_comparison_results', ['actual_assignment_id'])
    op.create_index('ix_ecr_employee', 'equipment_comparison_results', ['employee_id'])
    op.create_index('ix_ecr_tenant', 'equipment_comparison_results', ['tenant_id'])

    op.create_table('equipment_discrepancies',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('actual_assignment_id', sa.String(36), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('shift_id', sa.String(36), nullable=True),
        sa.Column('planned_equipment_id', sa.String(36), nullable=True),
        sa.Column('actual_equipment_id', sa.String(36), nullable=False),
        sa.Column('planned_worker_id', sa.String(36), nullable=True),
        sa.Column('actual_worker_id', sa.String(36), nullable=False),
        sa.Column('discrepancy_type', sa.String(40), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(80), nullable=True),
        sa.Column('canonical_event_id', sa.String(36), nullable=True),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('supervisor_id', sa.String(36), nullable=True),
        sa.Column('evidence_json', sa.Text(), nullable=True),
        sa.Column('rule_version_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['actual_assignment_id'], ['equipment_assignments_actual.id']),
        sa.ForeignKeyConstraint(['actual_equipment_id'], ['equipments.id']),
        sa.ForeignKeyConstraint(['planned_equipment_id'], ['equipments.id']),
        sa.ForeignKeyConstraint(['shift_id'], ['shift_templates.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'actual_assignment_id', 'discrepancy_type',
                           name='uq_discrepancy_assignment_type'),
    )
    op.create_index('ix_discrepancy_employee', 'equipment_discrepancies',
                     ['tenant_id', 'employee_id', 'operating_date'])
    op.create_index('ix_discrepancy_tenant_date', 'equipment_discrepancies',
                     ['tenant_id', 'operating_date'])
    op.create_index('ix_ed_assignment', 'equipment_discrepancies', ['actual_assignment_id'])
    op.create_index('ix_ed_employee', 'equipment_discrepancies', ['employee_id'])
    op.create_index('ix_ed_tenant', 'equipment_discrepancies', ['tenant_id'])

    # ── Extend equipment_assignments_actual ─────────────────
    # SQLite batch_alter_table for safe column additions
    with op.batch_alter_table('equipment_assignments_actual') as batch:
        batch.add_column(sa.Column('operating_date', sa.Date(), nullable=False, server_default='2026-01-01'))
        batch.add_column(sa.Column('shift_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('site_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('canonical_event_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('reason', sa.Text(), nullable=True))
        batch.add_column(sa.Column('status', sa.String(20), nullable=False, server_default='ACTIVE'))
        batch.add_column(sa.Column('rule_version_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                                   server_default=sa.text("CURRENT_TIMESTAMP")))
        batch.create_index('ix_actual_assignment_employee', ['tenant_id', 'employee_id', 'operating_date'])
        batch.create_index('ix_actual_assignment_equipment', ['tenant_id', 'equipment_id', 'operating_date'])
        batch.create_index('ix_actual_assignment_tenant_date', ['tenant_id', 'operating_date'])
        batch.create_unique_constraint('uq_actual_assignment_identity',
                                       ['tenant_id', 'employee_id', 'equipment_id', 'started_at'])
        batch.create_foreign_key('fk_actual_site', 'sites', ['site_id'], ['id'])
        batch.create_foreign_key('fk_actual_rule_version', 'rule_versions', ['rule_version_id'], ['id'])
        batch.create_foreign_key('fk_actual_shift', 'shift_templates', ['shift_id'], ['id'])
        batch.create_foreign_key('fk_actual_canonical', 'canonical_attendance_events',
                                 ['canonical_event_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('equipment_assignments_actual') as batch:
        batch.drop_constraint('fk_actual_canonical', type_='foreignkey')
        batch.drop_constraint('fk_actual_shift', type_='foreignkey')
        batch.drop_constraint('fk_actual_rule_version', type_='foreignkey')
        batch.drop_constraint('fk_actual_site', type_='foreignkey')
        batch.drop_constraint('uq_actual_assignment_identity', type_='unique')
        batch.drop_index('ix_actual_assignment_tenant_date')
        batch.drop_index('ix_actual_assignment_equipment')
        batch.drop_index('ix_actual_assignment_employee')
        batch.drop_column('created_at')
        batch.drop_column('rule_version_id')
        batch.drop_column('status')
        batch.drop_column('reason')
        batch.drop_column('canonical_event_id')
        batch.drop_column('site_id')
        batch.drop_column('shift_id')
        batch.drop_column('operating_date')

    op.drop_index('ix_ed_tenant', table_name='equipment_discrepancies')
    op.drop_index('ix_ed_employee', table_name='equipment_discrepancies')
    op.drop_index('ix_ed_assignment', table_name='equipment_discrepancies')
    op.drop_index('ix_discrepancy_tenant_date', table_name='equipment_discrepancies')
    op.drop_index('ix_discrepancy_employee', table_name='equipment_discrepancies')
    op.drop_table('equipment_discrepancies')

    op.drop_index('ix_ecr_tenant', table_name='equipment_comparison_results')
    op.drop_index('ix_ecr_employee', table_name='equipment_comparison_results')
    op.drop_index('ix_ecr_assignment', table_name='equipment_comparison_results')
    op.drop_index('ix_comparison_tenant_date', table_name='equipment_comparison_results')
    op.drop_index('ix_comparison_employee', table_name='equipment_comparison_results')
    op.drop_table('equipment_comparison_results')
