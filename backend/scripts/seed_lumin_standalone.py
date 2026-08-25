#!/usr/bin/env python3
"""
Standalone Lumin Park demo seed script (theme-park vertical).
Seeds all master data + demo operational data into PostgreSQL.
Idempotent — safe to re-run. No Excel dependency.

Usage:
  cd /home/ubuntu/FaztrackAttendance/backend
  .venv/bin/python scripts/seed_lumin_standalone.py

Credentials are read from .env.lumin (FAZTRACK_DATABASE_URL /
FAZTRACK_DEMO_SEED_PASSWORD) so no secret lives in this file.
"""

import os
import sys
from datetime import date, datetime, time, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _load_env_file(path):
    """Minimal .env loader (KEY=VALUE lines) without overriding existing env."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out

_env = _load_env_file(os.path.join(BACKEND_DIR, ".env.lumin"))

DB_URL = os.environ.get(
    "FAZTRACK_DATABASE_URL",
    _env.get(
        "FAZTRACK_DATABASE_URL",
        "postgresql+psycopg://faztrack_lumin@localhost:5437/faztrack_attendance_lumin",
    ),
)
PASSWORD = os.environ.get(
    "FAZTRACK_DEMO_SEED_PASSWORD", _env.get("FAZTRACK_DEMO_SEED_PASSWORD", "LuminDemo2026!")
)

engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# App imports (after sys.path fix)
from app import models  # noqa: E402,F401
from app.models import Base  # noqa: E402
from app.models import (  # noqa: E402
    Tenant, TenantStatus,
    User, Membership, RoleCode, MembershipStatus,
    Worker, Site, SiteType, SiteStatus,
    Equipment, EquipmentStatus,
    Role, Crew,
    ShiftTemplate, CheckpointPolicy, RosterPolicy,
    RuleVersion,
    RosterAssignment, WorkStatus, SiteStatusEnum, ValidationStatus,
    EmployeeMeta,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    ExceptionCase, ExceptionSeverity, ExceptionStatus,
)
from app.security import hash_password  # noqa: E402
from app.rule_versioning import snapshot_rules  # noqa: E402


def bootstrap_schema():
    """Replicate metro bootstrap path: Base.metadata.create_all + enum sync.

    The alembic chain double-emits CREATE TYPE sitetype on a fresh DB
    (explicit .create() plus column auto-create), so fresh instances are
    bootstrapped via metadata.create_all like app/seed_metro.py does.
    """
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TYPE sitetype ADD VALUE IF NOT EXISTS 'ATTRACTION_SITE'"
        ))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANT_ID = "lumin-park-001"
DEMO_START = date(2026, 9, 1)
DEMO_END = date(2026, 9, 7)

# WIB = UTC+7 (contrast: Metro uses WITA UTC+8)
WIB_OFFSET = timedelta(hours=7)


def wib_to_utc(dt_local: datetime) -> datetime:
    return dt_local.replace(tzinfo=timezone(WIB_OFFSET)).astimezone(timezone.utc)


def aware_utc(dt_local: datetime) -> datetime:
    return wib_to_utc(dt_local)


def get_or_none(db: Session, model, **kwargs):
    return db.execute(select(model).filter_by(**kwargs)).scalar_one_or_none()


def ensure_tenant(db: Session) -> Tenant:
    t = get_or_none(db, Tenant, id=TENANT_ID)
    if t:
        return t
    t = Tenant(id=TENANT_ID, code="lumin-park", name="Lumin Park",
               timezone="Asia/Jakarta", status=TenantStatus.ACTIVE)
    db.add(t); db.flush()
    return t


def ensure_user(db: Session, user_id: str, login_id: str, display_name: str) -> User:
    u = get_or_none(db, User, id=user_id)
    if u:
        return u
    u = User(id=user_id, login_id=login_id, display_name=display_name,
             password_hash=hash_password(PASSWORD))
    db.add(u); db.flush()
    return u


def ensure_membership(db: Session, user_id: str, role: RoleCode) -> Membership:
    m = get_or_none(db, Membership, tenant_id=TENANT_ID, user_id=user_id, role=role)
    if m:
        return m
    m = Membership(id=f"mem-{user_id}", tenant_id=TENANT_ID, user_id=user_id,
                   role=role, status=MembershipStatus.ACTIVE)
    db.add(m); db.flush()
    return m


def ensure_site(db: Session) -> Site:
    s = get_or_none(db, Site, id="site-lumin-main")
    if s:
        return s
    s = Site(
        id="site-lumin-main", tenant_id=TENANT_ID,
        site_code="LUMIN-MAIN", site_name="Lumin Park",
        site_type=SiteType.ATTRACTION_SITE, status=SiteStatus.ACTIVE,
        timezone="Asia/Jakarta", effective_from=DEMO_START,
        notes="[SIMULATION/NON_PRODUCTION] Demo data for theme-park vertical",
    )
    db.add(s); db.flush()
    return s


def ensure_shift(db: Session, sid: str, code: str, name: str,
                 start: time, end: time,
                 brk_start: time, brk_end: time,
                 crosses: bool) -> ShiftTemplate:
    st = get_or_none(db, ShiftTemplate, id=sid)
    if st:
        return st
    st = ShiftTemplate(
        id=sid, tenant_id=TENANT_ID,
        shift_code=code, shift_name=name,
        start_time=start, end_time=end,
        break_start=brk_start, break_end=brk_end,
        handover_start=end, handover_end=end,
        crosses_midnight=crosses,
    )
    db.add(st); db.flush()
    return st


def ensure_role(db: Session, rid: str, code: str, name: str) -> Role:
    r = get_or_none(db, Role, id=rid)
    if r:
        return r
    r = Role(id=rid, tenant_id=TENANT_ID, role_code=code, role_name=name,
             equipment_type_required=None, status="ACTIVE")
    db.add(r); db.flush()
    return r


def ensure_crew(db: Session, cid: str, code: str, name: str) -> Crew:
    c = get_or_none(db, Crew, id=cid)
    if c:
        return c
    c = Crew(id=cid, tenant_id=TENANT_ID, crew_code=code, crew_name=name,
             onsite_cycle_anchor=DEMO_START, cycle_offset_days=0)
    db.add(c); db.flush()
    return c


def ensure_worker(db: Session, wid: str, code: str, name: str) -> Worker:
    w = get_or_none(db, Worker, id=wid)
    if w:
        return w
    w = Worker(id=wid, tenant_id=TENANT_ID, code=code, name=name, is_active=True)
    db.add(w); db.flush()
    return w


def ensure_equipment(db: Session, eid: str, code: str, eq_type: str) -> Equipment:
    e = get_or_none(db, Equipment, id=eid)
    if e:
        return e
    e = Equipment(id=eid, tenant_id=TENANT_ID, equipment_code=code,
                  equipment_type=eq_type, status=EquipmentStatus.ACTIVE,
                  effective_from=DEMO_START)
    db.add(e); db.flush()
    return e


def ensure_employee_meta(db: Session, em_id: str, worker_id: str,
                         role_id: str, crew_id: str) -> EmployeeMeta:
    em = get_or_none(db, EmployeeMeta, id=em_id)
    if em:
        return em
    em = EmployeeMeta(
        id=em_id, tenant_id=TENANT_ID, worker_id=worker_id,
        employee_no=em_id, role_id=role_id, crew_id=crew_id,
        effective_from=DEMO_START, cycle_offset_days=0,
    )
    db.add(em); db.flush()
    return em


def ensure_checkpoint_policy(db: Session, cp_id: str, cp_type: str,
                              shift_id: str, seq: int,
                              win_start: int, win_end: int) -> CheckpointPolicy:
    cp = get_or_none(db, CheckpointPolicy, id=cp_id)
    if cp:
        return cp
    cp = CheckpointPolicy(
        id=cp_id, tenant_id=TENANT_ID,
        checkpoint_type=cp_type, shift_id=shift_id,
        enabled=True, sequence_order=seq,
        window_start_offset_min=win_start,
        window_end_offset_min=win_end,
        severity="WARNING",
        effective_from=DEMO_START,
    )
    db.add(cp); db.flush()
    return cp


def ensure_roster_policy(db: Session, rp_id: str, key: str, value: str,
                         dtype: str, status: str) -> RosterPolicy:
    rp = get_or_none(db, RosterPolicy, id=rp_id)
    if rp:
        return rp
    rp = RosterPolicy(
        id=rp_id, tenant_id=TENANT_ID,
        policy_key=key, policy_value=value,
        data_type=dtype, confirmation_status=status,
    )
    db.add(rp); db.flush()
    return rp


def ensure_roster_assignment(db: Session, ra_id: str, op_date: date,
                              emp_id: str, crew_id: str,
                              work_status: WorkStatus,
                              shift_id: str | None,
                              site_id: str | None,
                              equip_id: str | None,
                              rule_ver) -> RosterAssignment:
    ra = get_or_none(db, RosterAssignment, id=ra_id)
    if ra:
        return ra
    site_status = SiteStatusEnum.ONSITE if work_status == WorkStatus.WORK else SiteStatusEnum.OFFSITE
    ra = RosterAssignment(
        id=ra_id, tenant_id=TENANT_ID,
        roster_code=f"ROSTER-{op_date.strftime('%Y%m%d')}",
        operating_date=op_date,
        employee_id=emp_id, crew_id=crew_id,
        site_cycle_day=0, site_status=site_status,
        work_status=work_status,
        shift_id=shift_id, site_id=site_id,
        planned_equipment_id=equip_id,
        rule_version_id=rule_ver.id if rule_ver else None,
        effective_rule_version=rule_ver.version_label if rule_ver else None,
        validation_status=ValidationStatus.DRAFT,
    )
    db.add(ra); db.flush()
    return ra


def ensure_canonical_event(db: Session, ce_id: str, emp_id: str,
                            event_type: CanonicalEventType,
                            local_ts: datetime, utc_ts: datetime,
                            op_date: date, shift_id: str | None,
                            site_id: str | None,
                            source_ev_id: str) -> CanonicalAttendanceEvent:
    ce = get_or_none(db, CanonicalAttendanceEvent, id=ce_id)
    if ce:
        return ce
    ce = CanonicalAttendanceEvent(
        id=ce_id, tenant_id=TENANT_ID,
        employee_id=emp_id, event_type=event_type,
        local_timestamp=local_ts, utc_timestamp=utc_ts,
        timezone="Asia/Jakarta",
        operating_date=op_date, shift_id=shift_id, site_id=site_id,
        source="SEED", source_event_id=source_ev_id,
        processing_status=CanonicalProcessingStatus.VALID,
    )
    db.add(ce); db.flush()
    return ce


def ensure_rule_evaluation(db: Session, re_id: str, emp_id: str,
                            op_date: date, shift_id: str | None,
                            rule_code: str, rule_ver,
                            status: RuleEvaluationStatus,
                            severity: RuleSeverity,
                            actual_val: str, expected_val: str,
                            evidence_key: str, reason: str) -> RuleEvaluation:
    re_ = get_or_none(db, RuleEvaluation, id=re_id)
    if re_:
        return re_
    re_ = RuleEvaluation(
        id=re_id, tenant_id=TENANT_ID,
        employee_id=emp_id, operating_date=op_date, shift_id=shift_id,
        rule_code=rule_code,
        rule_version_id=rule_ver.id if rule_ver else None,
        status=status, severity=severity,
        actual_value=actual_val, expected_value=expected_val,
        evidence_key=evidence_key, reason=reason,
    )
    db.add(re_); db.flush()
    return re_


def ensure_exception_case(db: Session, ec_id: str, emp_id: str,
                           op_date: date, shift_id: str | None,
                           source_type: str, source_id: str,
                           exc_type: str, severity: ExceptionSeverity,
                           rule_ver, detected_at: datetime,
                           owner_id: str | None) -> ExceptionCase:
    ec = get_or_none(db, ExceptionCase, id=ec_id)
    if ec:
        return ec
    ec = ExceptionCase(
        id=ec_id, tenant_id=TENANT_ID,
        exception_type=exc_type, severity=severity,
        status=ExceptionStatus.OPEN,
        employee_id=emp_id, operating_date=op_date, shift_id=shift_id,
        source_type=source_type, source_id=source_id,
        rule_version_id=rule_ver.id if rule_ver else None,
        detected_at=detected_at, opened_at=detected_at,
        current_owner_id=owner_id,
    )
    db.add(ec); db.flush()
    return ec


def seed():
    bootstrap_schema()
    counts = {}
    db = SessionLocal()
    try:
        # ── Tenant ──────────────────────────────────────────────────────
        tenant = ensure_tenant(db)
        counts["tenant"] = 1

        # ── Users & Memberships ─────────────────────────────────────────
        admin = ensure_user(db, "lumin-admin-001", "admin@luminpark.id",
                            "Lumin Park Admin")
        sup = ensure_user(db, "lumin-sup-001", "supervisor@luminpark.id",
                          "Supervisor Lumin Park")
        ensure_membership(db, admin.id, RoleCode.OWNER)
        ensure_membership(db, sup.id, RoleCode.SUPERVISOR)
        counts["users"] = 2
        counts["memberships"] = 2

        # ── Site (ATTRACTION_SITE) ──────────────────────────────────────
        site = ensure_site(db)
        counts["sites"] = 1

        # ── Shift Templates (WIB) ───────────────────────────────────────
        pagi = ensure_shift(db, "PAGI", "PAGI", "Shift Pagi",
                            time(8, 0), time(16, 0),
                            time(12, 0), time(13, 0), False)
        sore = ensure_shift(db, "SORE", "SORE", "Shift Sore",
                            time(15, 0), time(23, 0),
                            time(19, 0), time(20, 0), False)
        counts["shift_templates"] = 2

        # ── Roles (theme park) ──────────────────────────────────────────
        role_ride = ensure_role(db, "role_ride", "role_ride", "Ride Operator")
        role_guest = ensure_role(db, "role_guest", "role_guest", "Guest Service")
        role_cash = ensure_role(db, "role_cashier", "role_cashier", "Cashier")
        role_maint = ensure_role(db, "role_maint", "role_maint", "Maintenance")
        counts["roles"] = 4

        # ── Crews ───────────────────────────────────────────────────────
        crew_a = ensure_crew(db, "crew_a", "crew_a", "Crew A")
        crew_b = ensure_crew(db, "crew_b", "crew_b", "Crew B")
        counts["crews"] = 2

        # ── Workers (12, LP001-LP012) ───────────────────────────────────
        workers_def = [
            ("lp01", "LP001", "Adi Nugraha"),
            ("lp02", "LP002", "Putri Maharani"),
            ("lp03", "LP003", "Bagus Wicaksono"),
            ("lp04", "LP004", "Sari Rahmadani"),
            ("lp05", "LP005", "Dimas Ardiansyah"),
            ("lp06", "LP006", "Ratna Dewi Puspita"),
            ("lp07", "LP007", "Fajar Ramadhan"),
            ("lp08", "LP008", "Intan Permata Sari"),
            ("lp09", "LP009", "Yoga Prasetyo"),
            ("lp10", "LP010", "Maya Anggraeni"),
            ("lp11", "LP011", "Reza Fahlevi"),
            ("lp12", "LP012", "Nadia Kartika"),
        ]
        workers = {}
        for wid, code, name in workers_def:
            workers[wid] = ensure_worker(db, wid, code, name)
        counts["workers"] = len(workers)

        # ── Equipment (2 ride units) ────────────────────────────────────
        rc1 = ensure_equipment(db, "rc01", "RC-01", "ROLLER_COASTER")
        fw1 = ensure_equipment(db, "fw01", "FW-01", "FERRIS_WHEEL")
        counts["equipment"] = 2

        # ── EmployeeMeta ────────────────────────────────────────────────
        em_map = {
            "lp01": ("em-lp01", role_ride.id,  crew_a.id),
            "lp02": ("em-lp02", role_guest.id, crew_a.id),
            "lp03": ("em-lp03", role_cash.id,  crew_a.id),
            "lp04": ("em-lp04", role_maint.id, crew_a.id),
            "lp05": ("em-lp05", role_ride.id,  crew_b.id),
            "lp06": ("em-lp06", role_guest.id, crew_b.id),
            "lp07": ("em-lp07", role_ride.id,  crew_b.id),
            "lp08": ("em-lp08", role_cash.id,  crew_b.id),
            "lp09": ("em-lp09", role_maint.id, crew_b.id),
            "lp10": ("em-lp10", role_ride.id,  crew_a.id),
            "lp11": ("em-lp11", role_guest.id, crew_a.id),
            "lp12": ("em-lp12", role_ride.id,  crew_b.id),
        }
        for wid, (em_id, rid, cid) in em_map.items():
            ensure_employee_meta(db, em_id, workers[wid].id, rid, cid)
        counts["employee_meta"] = len(em_map)

        # ── Checkpoint Policies ─────────────────────────────────────────
        cp_types = [
            ("BRIEFING_START", 1, -15, 0),
            ("BRIEFING_END",   2, -5,  5),
            ("SHIFT_START",    3, -5,  5),
            ("BREAK_START",    4, -5,  5),
            ("BREAK_END",      5, -5,  5),
            ("HANDOVER_START", 6, -5,  5),
            ("SHIFT_END",      7, -5,  5),
        ]
        cp_count = 0
        for cp_type, seq, ws, we in cp_types:
            for shift in [pagi, sore]:
                cp_id = f"cp-{cp_type.lower()}-{shift.id.lower()}"
                ensure_checkpoint_policy(db, cp_id, cp_type, shift.id, seq, ws, we)
                cp_count += 1
        counts["checkpoint_policies"] = cp_count

        # ── Roster Policies ─────────────────────────────────────────────
        ensure_roster_policy(db, "rp-equip", "equipment_assignment_enabled", "true", "boolean", "CONFIRMED")
        ensure_roster_policy(db, "rp-comp",  "competency_validation_enabled", "true", "boolean", "CONFIRMED")
        ensure_roster_policy(db, "rp-rest",  "minimum_rest_hours", "TBC", "integer", "TBC")
        ensure_roster_policy(db, "rp-geo",   "geofence_radius_m", "TBC", "integer", "TBC")
        counts["roster_policies"] = 4

        # ── Rule Version ────────────────────────────────────────────────
        rule_ver = snapshot_rules(db, TENANT_ID, "LUMIN-RULE-v0.1", DEMO_START)
        counts["rule_versions"] = 1

        # ── Roster Assignments (12 workers × 7 days) ────────────────────
        # wid, work_status, shift, crew, planned_equipment (or None)
        planned_equip = {
            "lp01": rc1.id, "lp05": fw1.id,
            "lp07": rc1.id, "lp10": fw1.id, "lp12": rc1.id,
        }
        ra_defs = [
            ("lp01", WorkStatus.WORK, pagi.id, crew_a.id, "lp01"),
            ("lp02", WorkStatus.WORK, pagi.id, crew_a.id, None),
            ("lp03", WorkStatus.WORK, pagi.id, crew_a.id, None),
            ("lp04", WorkStatus.WORK, sore.id, crew_a.id, None),
            ("lp05", WorkStatus.WORK, sore.id, crew_b.id, "lp05"),
            ("lp06", WorkStatus.WORK, sore.id, crew_b.id, None),
            ("lp07", WorkStatus.WORK, pagi.id, crew_b.id, "lp07"),
            ("lp08", WorkStatus.WORK, pagi.id, crew_b.id, None),
            ("lp09", WorkStatus.WORK, sore.id, crew_b.id, None),
            ("lp10", WorkStatus.WORK, pagi.id, crew_a.id, "lp10"),
            ("lp11", WorkStatus.REST, pagi.id, crew_a.id, None),
            ("lp12", WorkStatus.OFFSITE, sore.id, crew_b.id, "lp12"),
        ]
        ra_count = 0
        roster_ids = {}
        d = DEMO_START
        while d <= DEMO_END:
            ds = d.strftime("%Y%m%d")
            for wid, ws_, sid_, cid_, eq_key in ra_defs:
                ra_id = f"ra-{wid}-{ds}"
                equip_id = planned_equip.get(eq_key) if eq_key else None
                ensure_roster_assignment(
                    db, ra_id, d, workers[wid].id, cid_,
                    ws_, sid_, site.id, equip_id, rule_ver,
                )
                if d == DEMO_START and wid in ("lp02", "lp05"):
                    roster_ids[wid] = ra_id
                ra_count += 1
            d += timedelta(days=1)
        counts["roster_assignments"] = ra_count

        # ── Demo Operational Data (2026-09-01 only, WIB) ────────────────
        op_date = DEMO_START

        # lp02 (Guest Service, PAGI): CHECK_IN 07:55 WIB, CHECK_OUT 16:05 WIB
        lp02_in_local = datetime(2026, 9, 1, 7, 55)
        ensure_canonical_event(
            db, "ce-lp02-in", workers["lp02"].id, CanonicalEventType.CHECK_IN,
            lp02_in_local.replace(tzinfo=timezone(WIB_OFFSET)),
            wib_to_utc(lp02_in_local), op_date, pagi.id, site.id,
            "seed-lp02-checkin",
        )
        lp02_out_local = datetime(2026, 9, 1, 16, 5)
        ce_lp02_out = ensure_canonical_event(
            db, "ce-lp02-out", workers["lp02"].id, CanonicalEventType.CHECK_OUT,
            lp02_out_local.replace(tzinfo=timezone(WIB_OFFSET)),
            wib_to_utc(lp02_out_local), op_date, pagi.id, site.id,
            "seed-lp02-checkout",
        )

        # lp05 (Ride Operator, SORE): CHECK_IN 15:00 WIB only — missing checkout
        lp05_in_local = datetime(2026, 9, 1, 15, 0)
        ensure_canonical_event(
            db, "ce-lp05-in", workers["lp05"].id, CanonicalEventType.CHECK_IN,
            lp05_in_local.replace(tzinfo=timezone(WIB_OFFSET)),
            wib_to_utc(lp05_in_local), op_date, sore.id, site.id,
            "seed-lp05-checkin",
        )
        counts["canonical_events"] = 3

        # ── Rule Evaluation: lp02 late check-in (WARNING) ───────────────
        re_lp02 = ensure_rule_evaluation(
            db, f"re-lp02-late-{op_date.strftime('%Y%m%d')}",
            workers["lp02"].id, op_date, pagi.id,
            "LATE_CHECK_IN", rule_ver,
            RuleEvaluationStatus.FAIL, RuleSeverity.WARNING,
            "08:10", "08:00",
            f"lp02-{op_date}-CHECK_IN",
            "Check-in at 08:10 WIB exceeds PAGI shift start 08:00 WIB",
        )
        counts["rule_evaluations"] = 1

        # ── Exception Case 1: Late Check-in — WARNING / OPEN ────────────
        detected_1 = aware_utc(datetime(2026, 9, 1, 8, 15))
        ensure_exception_case(
            db, "exc-lp02-late-0901", workers["lp02"].id,
            op_date, pagi.id,
            "RULE_EVALUATION", re_lp02.id,
            "LATE_CHECK_IN",
            ExceptionSeverity.WARNING,
            rule_ver, detected_1,
            sup.id,
        )
        counts["exception_late_checkin"] = 1

        # ── Exception Case 2: Missing Checkout — CRITICAL / OPEN ────────
        # lp05 checked in at 15:00 WIB but never checked out (SORE ends
        # 23:00 WIB) → detection simulated after shift end.
        detected_2 = aware_utc(datetime(2026, 9, 1, 23, 30))
        ensure_exception_case(
            db, "exc-lp05-missing-checkout-0901", workers["lp05"].id,
            op_date, sore.id,
            "CHECKPOINT_VALIDATION", f"ce-lp05-in:{op_date.strftime('%Y%m%d')}",
            "MISSING_CHECKOUT",
            ExceptionSeverity.CRITICAL,
            rule_ver, detected_2,
            sup.id,
        )
        counts["exception_missing_checkout"] = 1

        # ── Commit ──────────────────────────────────────────────────────
        db.commit()

        # ── Summary ─────────────────────────────────────────────────────
        print("=" * 60)
        print("  Lumin Park Seed — COMPLETE")
        print("=" * 60)
        print(f"  Tenant:              {tenant.code} ({tenant.id})")
        db_host = DB_URL.split('@')[-1] if '@' in DB_URL else DB_URL
        print(f"  Database:            {db_host}")
        print()
        for key, val in counts.items():
            print(f"  {key:25s}: {val}")
        print()
        print(f"  Demo period:         {DEMO_START} → {DEMO_END}")
        print(f"  Operational data:    {DEMO_START} only")
        print(f"  Timezone:            Asia/Jakarta (WIB, UTC+7)")
        print(f"  Rule version:        LUMIN-RULE-v0.1")
        print(f"  Password:            {'*' * len(PASSWORD)}")
        print("=" * 60)

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    seed()

