import enum
import uuid
from datetime import date, datetime, time, timezone
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def uid() -> str: return str(uuid.uuid4())
def now() -> datetime: return datetime.now(timezone.utc)

class TenantStatus(str, enum.Enum): ACTIVE="ACTIVE"; SUSPENDED="SUSPENDED"
class MembershipStatus(str, enum.Enum): ACTIVE="ACTIVE"; SUSPENDED="SUSPENDED"; REVOKED="REVOKED"
class RoleCode(str, enum.Enum): OWNER="OWNER"; ADMIN="ADMIN"; SUPERVISOR="SUPERVISOR"; WORKER="WORKER"; AUDITOR="AUDITOR"
class EnrollmentStatus(str, enum.Enum): PENDING="PENDING"; APPROVED="APPROVED"; REJECTED="REJECTED"; SUPERSEDED="SUPERSEDED"
class DeviceStatus(str, enum.Enum): ACTIVE="ACTIVE"; REVOKED="REVOKED"
class AttendanceType(str, enum.Enum): CHECK_IN="CHECK_IN"; CHECK_OUT="CHECK_OUT"
class AttendanceStatus(str, enum.Enum): VALID="VALID"; REVIEW="REVIEW"; REJECTED="REJECTED"
class TimesheetPeriodStatus(str, enum.Enum): OPEN="OPEN"; CLOSED="CLOSED"

class Tenant(Base):
    __tablename__="tenants"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    code: Mapped[str]=mapped_column(String(50),unique=True,index=True)
    name: Mapped[str]=mapped_column(String(200))
    timezone: Mapped[str]=mapped_column(String(80),default="Asia/Jakarta")
    status: Mapped[TenantStatus]=mapped_column(Enum(TenantStatus),default=TenantStatus.ACTIVE)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class User(Base):
    __tablename__="users"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    login_id: Mapped[str]=mapped_column(String(120),unique=True,index=True)
    display_name: Mapped[str]=mapped_column(String(200))
    password_hash: Mapped[str]=mapped_column(String(300))
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Membership(Base):
    __tablename__="memberships"
    __table_args__=(UniqueConstraint("tenant_id","user_id","role",name="uq_membership_role"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id"),index=True)
    role: Mapped[RoleCode]=mapped_column(Enum(RoleCode))
    status: Mapped[MembershipStatus]=mapped_column(Enum(MembershipStatus),default=MembershipStatus.ACTIVE)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    tenant: Mapped[Tenant]=relationship()
    user: Mapped[User]=relationship()

class ProjectScope(Base):
    __tablename__="project_scopes"
    __table_args__=(UniqueConstraint("tenant_id","membership_id","project_id",name="uq_project_scope"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(String(36),index=True)
    membership_id: Mapped[str]=mapped_column(ForeignKey("memberships.id"),index=True)
    project_id: Mapped[str]=mapped_column(String(36),index=True)

class AuditEvent(Base):
    __tablename__="audit_events"
    __table_args__=(Index("ix_audit_tenant_entity_time","tenant_id","entity_type","entity_id","created_at"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str | None]=mapped_column(String(36),index=True,nullable=True)
    actor_user_id: Mapped[str | None]=mapped_column(String(36),index=True,nullable=True)
    action: Mapped[str]=mapped_column(String(120),index=True)
    entity_type: Mapped[str]=mapped_column(String(120),index=True)
    entity_id: Mapped[str | None]=mapped_column(String(36),nullable=True)
    reason: Mapped[str | None]=mapped_column(Text,nullable=True)
    correlation_id: Mapped[str]=mapped_column(String(100),index=True)
    payload_json: Mapped[str | None]=mapped_column(Text,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)

class Project(Base):
    __tablename__="projects"
    __table_args__=(UniqueConstraint("tenant_id","code",name="uq_project_tenant_code"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    code: Mapped[str]=mapped_column(String(50))
    name: Mapped[str]=mapped_column(String(200))
    latitude: Mapped[float]=mapped_column(Float)
    longitude: Mapped[float]=mapped_column(Float)
    geofence_radius_m: Mapped[int]=mapped_column(Integer,default=150)
    work_start: Mapped[time]=mapped_column(Time)
    work_end: Mapped[time]=mapped_column(Time)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)

class Worker(Base):
    __tablename__="workers"
    __table_args__=(UniqueConstraint("tenant_id","code",name="uq_worker_tenant_code"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    code: Mapped[str]=mapped_column(String(50))
    name: Mapped[str]=mapped_column(String(200))
    phone: Mapped[str | None]=mapped_column(String(40),nullable=True)
    pin_hash: Mapped[str | None]=mapped_column(String(300),nullable=True)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)

class Assignment(Base):
    __tablename__="assignments"
    __table_args__=(UniqueConstraint("tenant_id","worker_id","work_date",name="uq_worker_assignment_date"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("projects.id"),index=True)
    work_date: Mapped[date]=mapped_column(Date,index=True)

class WorkSchedule(Base):
    __tablename__="work_schedules"
    __table_args__=(UniqueConstraint("tenant_id","worker_id","work_date",name="uq_worker_schedule_date"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    work_date: Mapped[date]=mapped_column(Date,index=True)
    start_time: Mapped[time]=mapped_column(Time)
    end_time: Mapped[time]=mapped_column(Time)
    is_working_day: Mapped[bool]=mapped_column(Boolean,default=True)

class SupervisorProject(Base):
    __tablename__="supervisor_projects"
    __table_args__=(UniqueConstraint("tenant_id","membership_id","project_id",name="uq_supervisor_project"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    membership_id: Mapped[str]=mapped_column(ForeignKey("memberships.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("projects.id"),index=True)

class ImportStatus(str, enum.Enum): PREVIEW="PREVIEW"; CONFIRMED="CONFIRMED"; REJECTED="REJECTED"
class ImportBatch(Base):
    __tablename__="import_batches"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    spreadsheet_id: Mapped[str]=mapped_column(String(160))
    status: Mapped[ImportStatus]=mapped_column(Enum(ImportStatus),default=ImportStatus.PREVIEW,index=True)
    payload_json: Mapped[str]=mapped_column(Text)
    summary_json: Mapped[str]=mapped_column(Text)
    created_by: Mapped[str]=mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    confirmed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)

class DeviceChallenge(Base):
    __tablename__="device_challenges"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    challenge_hash: Mapped[str]=mapped_column(String(64))
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    used_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class DeviceEnrollment(Base):
    __tablename__="device_enrollments"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    public_key_jwk: Mapped[str]=mapped_column(Text)
    public_key_thumbprint: Mapped[str]=mapped_column(String(64),index=True)
    device_label: Mapped[str]=mapped_column(String(120))
    status: Mapped[EnrollmentStatus]=mapped_column(Enum(EnrollmentStatus),default=EnrollmentStatus.PENDING,index=True)
    requested_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    reviewed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    reviewed_by: Mapped[str | None]=mapped_column(ForeignKey("users.id"),nullable=True)
    rejection_reason: Mapped[str | None]=mapped_column(String(300),nullable=True)

class DeviceBinding(Base):
    __tablename__="device_bindings"
    __table_args__=(Index("uq_active_device_per_worker","tenant_id","worker_id",unique=True,sqlite_where=text("status = 'ACTIVE'"),postgresql_where=text("status = 'ACTIVE'")),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    enrollment_id: Mapped[str]=mapped_column(ForeignKey("device_enrollments.id"),unique=True)
    public_key_jwk: Mapped[str]=mapped_column(Text)
    public_key_thumbprint: Mapped[str]=mapped_column(String(64),index=True)
    device_label: Mapped[str]=mapped_column(String(120))
    status: Mapped[DeviceStatus]=mapped_column(Enum(DeviceStatus),default=DeviceStatus.ACTIVE,index=True)
    activated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    revoked_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    revoked_by: Mapped[str | None]=mapped_column(ForeignKey("users.id"),nullable=True)
    revoke_reason: Mapped[str | None]=mapped_column(String(300),nullable=True)

class AttendanceChallenge(Base):
    __tablename__="attendance_challenges"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("projects.id"),index=True)
    event_type: Mapped[AttendanceType]=mapped_column(Enum(AttendanceType))
    challenge_hash: Mapped[str]=mapped_column(String(64))
    work_date: Mapped[date]=mapped_column(Date,index=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    used_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class AttendanceEvent(Base):
    __tablename__="attendance_events"
    __table_args__=(Index("uq_counted_attendance_event","tenant_id","worker_id","work_date","event_type",unique=True,sqlite_where=text("status IN ('VALID','REVIEW')"),postgresql_where=text("status IN ('VALID','REVIEW')")),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    worker_id: Mapped[str]=mapped_column(ForeignKey("workers.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("projects.id"),index=True)
    device_binding_id: Mapped[str]=mapped_column(ForeignKey("device_bindings.id"),index=True)
    challenge_id: Mapped[str]=mapped_column(ForeignKey("attendance_challenges.id"),unique=True)
    event_type: Mapped[AttendanceType]=mapped_column(Enum(AttendanceType),index=True)
    status: Mapped[AttendanceStatus]=mapped_column(Enum(AttendanceStatus),index=True)
    reason_code: Mapped[str | None]=mapped_column(String(80),nullable=True)
    work_date: Mapped[date]=mapped_column(Date,index=True)
    server_time: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
    captured_at_client: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    latitude: Mapped[float]=mapped_column(Float)
    longitude: Mapped[float]=mapped_column(Float)
    accuracy_m: Mapped[float]=mapped_column(Float)
    distance_m: Mapped[float]=mapped_column(Float)
    signature: Mapped[str]=mapped_column(Text)
    reviewed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    reviewed_by: Mapped[str | None]=mapped_column(ForeignKey("users.id"),nullable=True)
    review_reason: Mapped[str | None]=mapped_column(String(300),nullable=True)

class AttendancePolicy(Base):
    __tablename__="attendance_policies"
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),primary_key=True)
    late_grace_minutes: Mapped[int]=mapped_column(Integer,default=0)
    early_leave_grace_minutes: Mapped[int]=mapped_column(Integer,default=0)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
    updated_by: Mapped[str | None]=mapped_column(ForeignKey("users.id"),nullable=True)

class TimesheetPeriod(Base):
    __tablename__="timesheet_periods"
    __table_args__=(UniqueConstraint("tenant_id","date_from","date_to","scope_key",name="uq_timesheet_period_scope"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    date_from: Mapped[date]=mapped_column(Date,index=True)
    date_to: Mapped[date]=mapped_column(Date,index=True)
    project_id: Mapped[str | None]=mapped_column(ForeignKey("projects.id"),nullable=True,index=True)
    scope_key: Mapped[str]=mapped_column(String(36),default="ALL")
    status: Mapped[TimesheetPeriodStatus]=mapped_column(Enum(TimesheetPeriodStatus),default=TimesheetPeriodStatus.OPEN,index=True)
    snapshot_json: Mapped[str | None]=mapped_column(Text,nullable=True)
    snapshot_hash: Mapped[str | None]=mapped_column(String(64),nullable=True)
    closed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    closed_by: Mapped[str | None]=mapped_column(ForeignKey("users.id"),nullable=True)

# ─────────────────────────────────────────────────────────────
# Metro Mining tenant-specific models (M0)
# All models are tenant-scoped and reusable for any mining tenant.
# ─────────────────────────────────────────────────────────────

class SiteType(str, enum.Enum):
    MINE_SITE = "MINE_SITE"
    MESS = "MESS"
    BRIEFING_POINT = "BRIEFING_POINT"
    OPERATING_AREA = "OPERATING_AREA"

class SiteStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"

class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("tenant_id", "site_code", name="uq_site_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_code: Mapped[str] = mapped_column(String(50))
    site_name: Mapped[str] = mapped_column(String(200))
    site_type: Mapped[SiteType] = mapped_column(Enum(SiteType))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Jakarta")
    status: Mapped[SiteStatus] = mapped_column(Enum(SiteStatus), default=SiteStatus.ACTIVE)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class EquipmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"

class Equipment(Base):
    __tablename__ = "equipments"
    __table_args__ = (UniqueConstraint("tenant_id", "equipment_code", name="uq_equipment_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    equipment_code: Mapped[str] = mapped_column(String(50))
    equipment_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[EquipmentStatus] = mapped_column(Enum(EquipmentStatus), default=EquipmentStatus.ACTIVE)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "role_code", name="uq_role_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    role_code: Mapped[str] = mapped_column(String(50))
    role_name: Mapped[str] = mapped_column(String(200))
    equipment_type_required: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")

class Crew(Base):
    __tablename__ = "crews"
    __table_args__ = (UniqueConstraint("tenant_id", "crew_code", name="uq_crew_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    crew_code: Mapped[str] = mapped_column(String(50))
    crew_name: Mapped[str] = mapped_column(String(200))
    onsite_cycle_anchor: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_offset_days: Mapped[int] = mapped_column(Integer, default=0)

class CompetencyStatus(str, enum.Enum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"

class Competency(Base):
    """Employee competency/certification history.
    Supports multiple records per employee+equipment_type (renewal, suspension, expiry).
    """
    __tablename__ = "competencies"
    __table_args__ = (
        Index("ix_competency_emp_type", "tenant_id", "employee_id", "equipment_type"),
        Index("ix_competency_tenant", "tenant_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    competency_code: Mapped[str] = mapped_column(String(50))
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    equipment_type: Mapped[str] = mapped_column(String(80))
    certification_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[CompetencyStatus] = mapped_column(Enum(CompetencyStatus), default=CompetencyStatus.VALID)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class ShiftTemplate(Base):
    __tablename__ = "shift_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "shift_code", name="uq_shift_tenant_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    shift_code: Mapped[str] = mapped_column(String(50))
    shift_name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_start: Mapped[time] = mapped_column(Time)
    break_end: Mapped[time] = mapped_column(Time)
    handover_start: Mapped[time] = mapped_column(Time)
    handover_end: Mapped[time] = mapped_column(Time)
    crosses_midnight: Mapped[bool] = mapped_column(Boolean, default=False)

class CheckpointPolicy(Base):
    __tablename__ = "checkpoint_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "checkpoint_type", "shift_id", name="uq_checkpoint_shift"),
        Index("ix_checkpoint_policy_tenant", "tenant_id", "enabled"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    checkpoint_type: Mapped[str] = mapped_column(String(80))
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("shift_templates.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sequence_order: Mapped[int] = mapped_column(Integer, default=0)
    window_start_offset_min: Mapped[int] = mapped_column(Integer, default=0)
    window_end_offset_min: Mapped[int] = mapped_column(Integer, default=0)
    tolerance_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_evidence: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="WARNING")
    default_validation_behavior: Mapped[str] = mapped_column(String(30), default="CONFIG_INCOMPLETE")
    effective_from: Mapped[date] = mapped_column(Date, default=date(2026, 1, 1))
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("rule_versions.id"), nullable=True, index=True)

class RosterPolicy(Base):
    __tablename__ = "roster_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "policy_key", name="uq_roster_policy_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    policy_key: Mapped[str] = mapped_column(String(100))
    policy_value: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(20), default="string")
    confirmation_status: Mapped[str] = mapped_column(String(20), default="TBC")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "version_label", name="uq_rule_version_label"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    version_label: Mapped[str] = mapped_column(String(100))
    effective_from: Mapped[date] = mapped_column(Date)
    config_snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class WorkStatus(str, enum.Enum):
    WORK = "WORK"
    REST = "REST"
    OFFSITE = "OFFSITE"
    LEAVE = "LEAVE"
    SICK = "SICK"
    TRAINING = "TRAINING"
    STANDBY = "STANDBY"

class SiteStatusEnum(str, enum.Enum):
    ONSITE = "ONSITE"
    OFFSITE = "OFFSITE"

class ValidationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALID = "VALID"
    INVALID = "INVALID"
    PUBLISHED = "PUBLISHED"
    LOCKED = "LOCKED"
    REVISED = "REVISED"

class RosterAssignment(Base):
    __tablename__ = "roster_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "operating_date", "employee_id", name="uq_roster_emp_date"),
        Index("ix_roster_tenant_date", "tenant_id", "operating_date"),
        Index("ix_roster_tenant_crew", "tenant_id", "crew_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    roster_code: Mapped[str] = mapped_column(String(50))
    operating_date: Mapped[date] = mapped_column(Date)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    crew_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    site_cycle_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    site_status: Mapped[SiteStatusEnum] = mapped_column(Enum(SiteStatusEnum), default=SiteStatusEnum.ONSITE)
    work_status: Mapped[WorkStatus] = mapped_column(Enum(WorkStatus))
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sites.id"), nullable=True)
    planned_equipment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("equipments.id"), nullable=True)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("rule_versions.id"), nullable=True, index=True)
    effective_rule_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    validation_status: Mapped[ValidationStatus] = mapped_column(Enum(ValidationStatus), default=ValidationStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

# ─────────────────────────────────────────────────────────────
# M2C: Planned vs Actual Equipment Assignment
# ─────────────────────────────────────────────────────────────

class ActualAssignmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"

class ComparisonResult(str, enum.Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    NO_PLANNED_EQUIPMENT = "NO_PLANNED_EQUIPMENT"
    NO_ACTUAL_EQUIPMENT = "NO_ACTUAL_EQUIPMENT"
    CONFIG_INCOMPLETE = "CONFIG_INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"

class DiscrepancyType(str, enum.Enum):
    EQUIPMENT_MISMATCH = "EQUIPMENT_MISMATCH"
    OPERATOR_SUBSTITUTION = "OPERATOR_SUBSTITUTION"
    NO_PLANNED = "NO_PLANNED"
    NO_ACTUAL = "NO_ACTUAL"
    COMPETENCY_INVALID = "COMPETENCY_INVALID"
    EQUIPMENT_UNAVAILABLE = "EQUIPMENT_UNAVAILABLE"

class DiscrepancyStatus(str, enum.Enum):
    OPEN = "OPEN"
    PENDING_REVIEW = "PENDING_REVIEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"

class EquipmentAssignmentActual(Base):
    """Actual equipment assignment with interval tracking.
    One operator can have multiple assignments in a shift (equipment change).
    One equipment can have multiple operators across shifts (not overlapping).
    ACTUAL MUST NEVER OVERWRITE PLANNED.
    """
    __tablename__ = "equipment_assignments_actual"
    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_id", "equipment_id", "started_at", name="uq_actual_assignment_identity"),
        Index("ix_actual_assignment_tenant_date", "tenant_id", "operating_date"),
        Index("ix_actual_assignment_employee", "tenant_id", "employee_id", "operating_date"),
        Index("ix_actual_assignment_equipment", "tenant_id", "equipment_id", "operating_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    roster_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("roster_assignments.id"), nullable=True, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    equipment_id: Mapped[str] = mapped_column(String(36), ForeignKey("equipments.id"), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sites.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="MANUAL")
    canonical_event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("canonical_attendance_events.id"), nullable=True)
    supervisor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ActualAssignmentStatus] = mapped_column(Enum(ActualAssignmentStatus), default=ActualAssignmentStatus.ACTIVE)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("rule_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ExceptionSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class EquipmentComparisonResult(Base):
    """Result of comparing planned vs actual equipment assignment.
    One per actual assignment event. Idempotent.
    """
    __tablename__ = "equipment_comparison_results"
    __table_args__ = (
        UniqueConstraint("actual_assignment_id", name="uq_comparison_actual"),
        Index("ix_comparison_tenant_date", "tenant_id", "operating_date"),
        Index("ix_comparison_employee", "tenant_id", "employee_id", "operating_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actual_assignment_id: Mapped[str] = mapped_column(String(36), ForeignKey("equipment_assignments_actual.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    planned_equipment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("equipments.id"), nullable=True)
    actual_equipment_id: Mapped[str] = mapped_column(String(36), ForeignKey("equipments.id"))
    comparison_result: Mapped[ComparisonResult] = mapped_column(Enum(ComparisonResult))
    planned_worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actual_worker_id: Mapped[str] = mapped_column(String(36))
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EquipmentDiscrepancy(Base):
    """Discrepancy/substitution candidate from planned vs actual mismatch.
    Created when comparison detects MISMATCH, operator substitution, or other anomaly.
    Full supervisor approval belongs to MM-M3.
    """
    __tablename__ = "equipment_discrepancies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "actual_assignment_id", "discrepancy_type", name="uq_discrepancy_assignment_type"),
        Index("ix_discrepancy_tenant_date", "tenant_id", "operating_date"),
        Index("ix_discrepancy_employee", "tenant_id", "employee_id", "operating_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actual_assignment_id: Mapped[str] = mapped_column(String(36), ForeignKey("equipment_assignments_actual.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    planned_equipment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("equipments.id"), nullable=True)
    actual_equipment_id: Mapped[str] = mapped_column(String(36), ForeignKey("equipments.id"))
    planned_worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actual_worker_id: Mapped[str] = mapped_column(String(36))
    discrepancy_type: Mapped[DiscrepancyType] = mapped_column(Enum(DiscrepancyType))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    canonical_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[DiscrepancyStatus] = mapped_column(Enum(DiscrepancyStatus), default=DiscrepancyStatus.OPEN)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    supervisor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ExceptionStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"

class ExceptionEvent(Base):
    __tablename__ = "exception_events"
    __table_args__ = (
        Index("ix_exception_tenant_date", "tenant_id", "operating_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    equipment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_code: Mapped[str] = mapped_column(String(80), index=True)
    rule_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[ExceptionSeverity] = mapped_column(Enum(ExceptionSeverity))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[ExceptionStatus] = mapped_column(Enum(ExceptionStatus), default=ExceptionStatus.OPEN)
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class OverrideEvent(Base):
    __tablename__ = "override_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    exception_id: Mapped[str] = mapped_column(ForeignKey("exception_events.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str] = mapped_column(String(36))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class EmployeeMeta(Base):
    """Extended worker metadata for mining operations (role, crew, lifecycle).
    Supports multiple effective periods per worker (effective-dated history).
    """
    __tablename__ = "employee_meta"
    __table_args__ = (
        UniqueConstraint("tenant_id", "worker_id", "effective_from", name="uq_employee_meta_worker_from"),
        Index("ix_employee_meta_tenant_worker", "tenant_id", "worker_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), index=True)
    employee_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("roles.id"), nullable=True)
    crew_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("crews.id"), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_offset_days: Mapped[int] = mapped_column(Integer, default=0)

# ─────────────────────────────────────────────────────────────
# M2A: Canonical Attendance Event Foundation
# Reusable for all tenants (Metro Mining, Lumin Park, future).
# ─────────────────────────────────────────────────────────────

class RawEventStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"

class CanonicalEventType(str, enum.Enum):
    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    BREAK_IN = "BREAK_IN"
    BREAK_OUT = "BREAK_OUT"
    BRIEFING_IN = "BRIEFING_IN"
    BRIEFING_OUT = "BRIEFING_OUT"
    EQUIPMENT_CHECK_IN = "EQUIPMENT_CHECK_IN"
    EQUIPMENT_CHECK_OUT = "EQUIPMENT_CHECK_OUT"
    HANDOVER_START = "HANDOVER_START"
    HANDOVER_END = "HANDOVER_END"
    SUPERVISOR_OVERRIDE = "SUPERVISOR_OVERRIDE"

class CanonicalProcessingStatus(str, enum.Enum):
    PENDING = "PENDING"
    SHIFT_RESOLVED = "SHIFT_RESOLVED"
    AMBIGUOUS_SHIFT = "AMBIGUOUS_SHIFT"
    MISSING_SHIFT = "MISSING_SHIFT"
    VALID = "VALID"
    INVALID = "INVALID"

class RawEvent(Base):
    """Immutable raw event from any source. Never modified after ingestion.
    Sensitive material (credentials, tokens, biometric templates) must NOT be stored.
    """
    __tablename__ = "raw_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "source_event_id", name="uq_raw_event_source"),
        Index("ix_raw_event_tenant_status", "tenant_id", "processing_status"),
        Index("ix_raw_event_tenant_received", "tenant_id", "received_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    source_event_id: Mapped[str] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    raw_timestamp: Mapped[str] = mapped_column(String(100))
    raw_payload: Mapped[str] = mapped_column(Text)
    processing_status: Mapped[RawEventStatus] = mapped_column(Enum(RawEventStatus), default=RawEventStatus.PENDING)
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class CanonicalAttendanceEvent(Base):
    """Normalized attendance event -- canonical representation for all tenants.
    Links back to raw source for full auditability.
    """
    __tablename__ = "canonical_attendance_events"
    __table_args__ = (
        Index("ix_canonical_tenant_emp_date", "tenant_id", "employee_id", "operating_date"),
        Index("ix_canonical_tenant_date", "tenant_id", "operating_date"),
        Index("ix_canonical_raw_event", "raw_event_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[CanonicalEventType] = mapped_column(Enum(CanonicalEventType))
    local_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    utc_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80))
    operating_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sites.id"), nullable=True)
    location_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    equipment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("equipments.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    source_event_id: Mapped[str] = mapped_column(String(200))
    raw_event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("raw_events.id"), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_status: Mapped[CanonicalProcessingStatus] = mapped_column(
        Enum(CanonicalProcessingStatus), default=CanonicalProcessingStatus.PENDING
    )
    roster_assignment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("roster_assignments.id"), nullable=True
    )
    legacy_attendance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("attendance_events.id"), nullable=True
    )
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ─────────────────────────────────────────────────────────────
# M2B: Checkpoint Engine
# Generic checkpoint validation for all tenants.
# ─────────────────────────────────────────────────────────────

class CheckpointValidationStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    CONFIG_INCOMPLETE = "CONFIG_INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED_POLICY_DECISION = "BLOCKED_POLICY_DECISION"


class CheckpointEventMapping(Base):
    """Config-driven mapping: source event characteristics -> checkpoint type.
    Allows each tenant to define how incoming events map to checkpoint types.
    """
    __tablename__ = "checkpoint_event_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "event_type", name="uq_checkpoint_mapping"),
        Index("ix_checkpoint_mapping_tenant", "tenant_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(80))
    checkpoint_type: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[date] = mapped_column(Date, default=date(2026, 1, 1))
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class CheckpointValidationResult(Base):
    """Result of checkpoint validation. One per canonical event + checkpoint type.
    Idempotent: same canonical_event_id + checkpoint_type -> same result.
    """
    __tablename__ = "checkpoint_validation_results"
    __table_args__ = (
        UniqueConstraint("canonical_event_id", "checkpoint_type", name="uq_checkpoint_result_event_type"),
        Index("ix_checkpoint_result_tenant_date", "tenant_id", "operating_date"),
        Index("ix_checkpoint_result_employee", "tenant_id", "employee_id", "operating_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    canonical_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("canonical_attendance_events.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    checkpoint_type: Mapped[str] = mapped_column(String(80))
    operating_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("checkpoint_policies.id"), nullable=True)
    rule_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    validation_status: Mapped[CheckpointValidationStatus] = mapped_column(Enum(CheckpointValidationStatus))
    detected_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MissingCheckpointResult(Base):
    """Expected checkpoint that did not arrive. Generated by missing checkpoint detection.
    Only for WORK status employees with applicable checkpoint policies.
    """
    __tablename__ = "missing_checkpoint_results"
    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_id", "operating_date", "checkpoint_type", name="uq_missing_checkpoint"),
        Index("ix_missing_checkpoint_tenant_date", "tenant_id", "operating_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    operating_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(80))
    policy_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("checkpoint_policies.id"), nullable=True)
    expected_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detection_status: Mapped[str] = mapped_column(String(30), default="MISSING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
