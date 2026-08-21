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
    radius_m: Mapped[int] = mapped_column(Integer, default=150)
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
    equipment_type: Mapped[str] = mapped_column(String(80))  # DUMP_TRUCK, EXCAVATOR, etc.
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
    __tablename__ = "competencies"
    __table_args__ = (UniqueConstraint("tenant_id", "employee_id", "equipment_type", name="uq_competency_emp_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    competency_code: Mapped[str] = mapped_column(String(50))
    employee_id: Mapped[str] = mapped_column(String(36), index=True)  # references Worker.id
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
    __table_args__ = (UniqueConstraint("tenant_id", "checkpoint_type", "shift_id", name="uq_checkpoint_shift"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    checkpoint_type: Mapped[str] = mapped_column(String(80))  # BRIEFING_IN, EQUIPMENT_CHECK_IN, etc.
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("shift_templates.id"), index=True)
    window_start_offset_min: Mapped[int] = mapped_column(Integer, default=0)
    window_end_offset_min: Mapped[int] = mapped_column(Integer, default=0)
    required_evidence: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="WARNING")

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
    employee_id: Mapped[str] = mapped_column(String(36), index=True)  # Worker.id
    crew_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    site_cycle_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    site_status: Mapped[SiteStatusEnum] = mapped_column(Enum(SiteStatusEnum), default=SiteStatusEnum.ONSITE)
    work_status: Mapped[WorkStatus] = mapped_column(Enum(WorkStatus))
    shift_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shift_templates.id"), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sites.id"), nullable=True)
    planned_equipment_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("equipments.id"), nullable=True)
    effective_rule_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    validation_status: Mapped[ValidationStatus] = mapped_column(Enum(ValidationStatus), default=ValidationStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class EquipmentAssignmentActual(Base):
    __tablename__ = "equipment_assignments_actual"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    roster_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("roster_assignments.id"), nullable=True, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), index=True)
    equipment_id: Mapped[str] = mapped_column(String(36), ForeignKey("equipments.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="MANUAL")
    supervisor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

class ExceptionSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"

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
    approved_by: Mapped[str] = mapped_column(String(36))  # User.id
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class EmployeeMeta(Base):
    """Extended worker metadata for mining operations (role, crew, lifecycle)."""
    __tablename__ = "employee_meta"
    __table_args__ = (UniqueConstraint("tenant_id", "worker_id", name="uq_employee_meta_worker"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), unique=True)
    employee_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("roles.id"), nullable=True)
    crew_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("crews.id"), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_offset_days: Mapped[int] = mapped_column(Integer, default=0)
