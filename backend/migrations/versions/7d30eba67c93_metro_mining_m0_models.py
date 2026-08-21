"""metro_mining_m0_models

Revision ID: 7d30eba67c93
Revises: 0005_m4_timesheet_quantification
Create Date: 2026-08-21 14:43:18.143057
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '7d30eba67c93'
down_revision: Union[str, None] = '0005_m4_timesheet_quantification'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── NEW ENUMS (Metro Mining only) ──
    site_type = sa.Enum('MINE_SITE', 'MESS', 'BRIEFING_POINT', 'OPERATING_AREA', name='sitetype')
    site_status = sa.Enum('ACTIVE', 'INACTIVE', 'SUSPENDED', name='sitestatus')
    equipment_status = sa.Enum('ACTIVE', 'INACTIVE', 'OUT_OF_SERVICE', name='equipmentstatus')
    competency_status = sa.Enum('VALID', 'EXPIRED', 'SUSPENDED', name='competencystatus')
    work_status = sa.Enum('WORK', 'REST', 'OFFSITE', 'LEAVE', 'SICK', 'TRAINING', 'STANDBY', name='workstatus')
    site_status_enum = sa.Enum('ONSITE', 'OFFSITE', name='sitestatusenum')
    validation_status = sa.Enum('DRAFT', 'VALID', 'INVALID', 'PUBLISHED', 'LOCKED', 'REVISED', name='validationstatus')
    exception_severity = sa.Enum('CRITICAL', 'WARNING', 'INFO', name='exceptionseverity')
    exception_status = sa.Enum('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'WAIVED', name='exceptionstatus')

    # Create enums
    site_type.create(op.get_bind(), checkfirst=True)
    site_status.create(op.get_bind(), checkfirst=True)
    equipment_status.create(op.get_bind(), checkfirst=True)
    competency_status.create(op.get_bind(), checkfirst=True)
    work_status.create(op.get_bind(), checkfirst=True)
    site_status_enum.create(op.get_bind(), checkfirst=True)
    validation_status.create(op.get_bind(), checkfirst=True)
    exception_severity.create(op.get_bind(), checkfirst=True)
    exception_status.create(op.get_bind(), checkfirst=True)

    # ── NEW TABLES (Metro Mining only) ──

    # 1. Sites
    op.create_table('sites',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('site_code', sa.String(50), nullable=False),
        sa.Column('site_name', sa.String(200), nullable=False),
        sa.Column('site_type', site_type, nullable=False),
        sa.Column('status', site_status, nullable=False),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('geofence_radius_m', sa.Integer(), nullable=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'site_code', name='uq_site_tenant_code'),
    )
    op.create_index('ix_sites_tenant_id', 'sites', ['tenant_id'])

    # 2. Equipment
    op.create_table('equipments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('equipment_code', sa.String(50), nullable=False),
        sa.Column('equipment_name', sa.String(200), nullable=True),
        sa.Column('equipment_type', sa.String(80), nullable=False),
        sa.Column('status', equipment_status, nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'equipment_code', name='uq_equipment_tenant_code'),
    )
    op.create_index('ix_equipments_tenant_id', 'equipments', ['tenant_id'])

    # 3. Roles
    op.create_table('roles',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('role_code', sa.String(50), nullable=False),
        sa.Column('role_name', sa.String(200), nullable=False),
        sa.Column('equipment_type_required', sa.String(80), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.UniqueConstraint('tenant_id', 'role_code', name='uq_role_tenant_code'),
    )
    op.create_index('ix_roles_tenant_id', 'roles', ['tenant_id'])

    # 4. Crews
    op.create_table('crews',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('crew_code', sa.String(50), nullable=False),
        sa.Column('crew_name', sa.String(200), nullable=False),
        sa.Column('onsite_cycle_anchor', sa.Date(), nullable=True),
        sa.Column('cycle_offset_days', sa.Integer(), nullable=False),
        sa.UniqueConstraint('tenant_id', 'crew_code', name='uq_crew_tenant_code'),
    )
    op.create_index('ix_crews_tenant_id', 'crews', ['tenant_id'])

    # 5. Shift Templates
    op.create_table('shift_templates',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('shift_code', sa.String(20), nullable=False),
        sa.Column('shift_name', sa.String(100), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('break_start', sa.Time(), nullable=True),
        sa.Column('break_end', sa.Time(), nullable=True),
        sa.Column('handover_start', sa.Time(), nullable=True),
        sa.Column('handover_end', sa.Time(), nullable=True),
        sa.Column('crosses_midnight', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'shift_code', name='uq_shift_tenant_code'),
    )
    op.create_index('ix_shift_templates_tenant_id', 'shift_templates', ['tenant_id'])

    # 6. Checkpoint Policies
    op.create_table('checkpoint_policies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('checkpoint_type', sa.String(50), nullable=False),
        sa.Column('shift_id', sa.String(36), sa.ForeignKey('shift_templates.id'), nullable=False),
        sa.Column('window_start_offset_min', sa.Integer(), nullable=False),
        sa.Column('window_end_offset_min', sa.Integer(), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'checkpoint_type', 'shift_id', name='uq_checkpoint_shift'),
    )
    op.create_index('ix_checkpoint_policies_tenant_id', 'checkpoint_policies', ['tenant_id'])
    op.create_index('ix_checkpoint_policies_shift_id', 'checkpoint_policies', ['shift_id'])

    # 7. Roster Policies
    op.create_table('roster_policies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('policy_key', sa.String(100), nullable=False),
        sa.Column('policy_value', sa.String(200), nullable=False),
        sa.Column('data_type', sa.String(20), nullable=False),
        sa.Column('confirmation_status', sa.String(20), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'policy_key', name='uq_roster_policy_key'),
    )
    op.create_index('ix_roster_policies_tenant_id', 'roster_policies', ['tenant_id'])

    # 8. Rule Versions
    op.create_table('rule_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('version_label', sa.String(100), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('config_snapshot_json', sa.Text(), nullable=False),
        sa.Column('created_by', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'version_label', name='uq_rule_version_label'),
    )
    op.create_index('ix_rule_versions_tenant_id', 'rule_versions', ['tenant_id'])

    # 9. Employee Meta
    op.create_table('employee_meta',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('worker_id', sa.String(36), sa.ForeignKey('workers.id'), nullable=False),
        sa.Column('employee_no', sa.String(50), nullable=False),
        sa.Column('role_id', sa.String(36), sa.ForeignKey('roles.id'), nullable=True),
        sa.Column('crew_id', sa.String(36), sa.ForeignKey('crews.id'), nullable=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'worker_id', name='uq_employee_meta_worker'),
    )
    op.create_index('ix_employee_meta_tenant_id', 'employee_meta', ['tenant_id'])

    # 10. Competencies
    op.create_table('competencies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('competency_code', sa.String(50), nullable=False),
        sa.Column('competency_name', sa.String(200), nullable=True),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('equipment_type', sa.String(80), nullable=False),
        sa.Column('certification_no', sa.String(100), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('status', competency_status, nullable=False),
        sa.Column('source', sa.String(80), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'employee_id', 'equipment_type', name='uq_competency_emp_type'),
    )
    op.create_index('ix_competencies_tenant_id', 'competencies', ['tenant_id'])
    op.create_index('ix_competencies_employee_id', 'competencies', ['employee_id'])

    # 11. Roster Assignments
    op.create_table('roster_assignments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('roster_code', sa.String(100), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('work_status', work_status, nullable=False),
        sa.Column('shift_id', sa.String(36), sa.ForeignKey('shift_templates.id'), nullable=True),
        sa.Column('site_id', sa.String(36), sa.ForeignKey('sites.id'), nullable=True),
        sa.Column('planned_equipment_id', sa.String(36), sa.ForeignKey('equipments.id'), nullable=True),
        sa.Column('site_status', site_status_enum, nullable=False),
        sa.Column('validation_status', validation_status, nullable=False),
        sa.Column('effective_rule_version', sa.String(100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'operating_date', 'employee_id', name='uq_roster_emp_date'),
    )
    op.create_index('ix_roster_assignments_tenant_id', 'roster_assignments', ['tenant_id'])
    op.create_index('ix_roster_assignments_employee_id', 'roster_assignments', ['employee_id'])
    op.create_index('ix_roster_tenant_date', 'roster_assignments', ['tenant_id', 'operating_date'])
    op.create_index('ix_roster_tenant_crew', 'roster_assignments', ['tenant_id', 'operating_date', 'employee_id'])

    # 12. Equipment Assignment Actuals
    op.create_table('equipment_assignments_actual',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('roster_id', sa.String(36), sa.ForeignKey('roster_assignments.id'), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('equipment_id', sa.String(36), sa.ForeignKey('equipments.id'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_equipment_assignments_actual_tenant_id', 'equipment_assignments_actual', ['tenant_id'])
    op.create_index('ix_equipment_assignments_actual_roster_id', 'equipment_assignments_actual', ['roster_id'])
    op.create_index('ix_equipment_assignments_actual_employee_id', 'equipment_assignments_actual', ['employee_id'])
    op.create_index('ix_equipment_assignments_actual_equipment_id', 'equipment_assignments_actual', ['equipment_id'])

    # 13. Exception Events
    op.create_table('exception_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('operating_date', sa.Date(), nullable=False),
        sa.Column('employee_id', sa.String(36), nullable=False),
        sa.Column('equipment_id', sa.String(36), nullable=True),
        sa.Column('rule_code', sa.String(80), nullable=False),
        sa.Column('rule_version', sa.String(100), nullable=True),
        sa.Column('severity', exception_severity, nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', exception_status, nullable=False),
        sa.Column('evidence_ref', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_exception_events_tenant_id', 'exception_events', ['tenant_id'])
    op.create_index('ix_exception_events_employee_id', 'exception_events', ['employee_id'])
    op.create_index('ix_exception_events_rule_code', 'exception_events', ['rule_code'])
    op.create_index('ix_exception_tenant_date', 'exception_events', ['tenant_id', 'operating_date'])

    # 14. Override Events
    op.create_table('override_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('exception_id', sa.String(36), sa.ForeignKey('exception_events.id'), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('approved_by', sa.String(36), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_override_events_tenant_id', 'override_events', ['tenant_id'])
    op.create_index('ix_override_events_exception_id', 'override_events', ['exception_id'])


def downgrade() -> None:
    op.drop_table('override_events')
    op.drop_table('exception_events')
    op.drop_table('equipment_assignments_actual')
    op.drop_table('roster_assignments')
    op.drop_table('competencies')
    op.drop_table('employee_meta')
    op.drop_table('rule_versions')
    op.drop_table('roster_policies')
    op.drop_table('checkpoint_policies')
    op.drop_table('shift_templates')
    op.drop_table('crews')
    op.drop_table('roles')
    op.drop_table('equipments')
    op.drop_table('sites')

    # Drop enums
    sa.Enum(name='exceptionstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='exceptionseverity').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='validationstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='sitestatusenum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='workstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='competencystatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='equipmentstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='sitestatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='sitetype').drop(op.get_bind(), checkfirst=True)
