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
