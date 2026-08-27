#!/usr/bin/env python3
"""
Standalone Metro Mining demo seed script.
Seeds all master data + demo operational data into PostgreSQL.
No Excel dependency — all data is hardcoded.

Usage:
  cd /home/ubuntu/FaztrackAttendance/backend
  source .venv/bin/activate
  FAZTRACK_DATABASE_URL=postgresql+psycopg://faztrack_metro:MetroPilot2026Secure@localhost:5436/faztrack_attendance_metro \
    python3 scripts/seed_metro_standalone.py
"""

import json
import os
import sys
from datetime import date, datetime, time, timezone, timedelta

# ---------------------------------------------------------------------------
# Bootstrap: add backend to path, build engine from env
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

DB_URL = os.environ.get(
    "FAZTRACK_DATABASE_URL",
    "postgresql+psycopg://faztrack_metro:MetroPilot2026Secure@localhost:5436/faztrack_attendance_metro",
)

engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# App imports (after sys.path fix)
from app.models import (
    Base, Tenant, TenantStatus,
    User, Membership, RoleCode, MembershipStatus,
    Worker, Site, SiteType, SiteStatus,
    Equipment, EquipmentStatus,
    Role, Crew,
    Competency, CompetencyStatus,
    ShiftTemplate, CheckpointPolicy, RosterPolicy,
    RuleVersion,
    RosterAssignment, WorkStatus, SiteStatusEnum, ValidationStatus,
    EmployeeMeta,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    ExceptionCase, ExceptionStatus, ExceptionSeverity, ExceptionSourceType,
    uid, now,
)
from app.security import hash_password
from app.rule_versioning import snapshot_rules
from app.roster_generator import generate_assignments, read_roster_policy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANT_ID = "metro-mining-001"
PASSWORD = os.environ.get("FAZTRACK_DEMO_SEED_PASSWORD", "MetroDemo2026!")
DEMO_START = date(2026, 9, 1)
DEMO_END = date(2026, 9, 7)

# WITA = UTC+8
WITA_OFFSET = timedelta(hours=8)


def wita_to_utc(dt_local: datetime) -> datetime:
    """Convert naive WITA datetime to aware UTC datetime."""
    return dt_local.replace(tzinfo=timezone(WITA_OFFSET)).astimezone(timezone.utc)


def aware_utc(dt_local: datetime) -> datetime:
    """Return a timezone-aware UTC datetime from a naive WITA datetime."""
    return wita_to_utc(dt_local)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_or_none(db: Session, model, **kwargs):
    """Return existing row or None."""
    return db.execute(select(model).filter_by(**kwargs)).scalar_one_or_none()


def ensure_tenant(db: Session) -> Tenant:
    t = get_or_none(db, Tenant, id=TENANT_ID)
    if t:
        return t
    t = Tenant(id=TENANT_ID, code="metro-mining", name="Metro Mining",
               timezone="Asia/Makassar", status=TenantStatus.ACTIVE)
    db.add(t)
    db.flush()
    return t


def ensure_user(db: Session, user_id: str, login_id: str, display_name: str) -> User:
    u = get_or_none(db, User, id=user_id)
    if u:
        return u
    u = User(id=user_id, login_id=login_id, display_name=display_name,
             password_hash=hash_password(PASSWORD))
    db.add(u)
    db.flush()
    return u


def ensure_membership(db: Session, user_id: str, role: RoleCode) -> Membership:
    m = get_or_none(db, Membership, tenant_id=TENANT_ID, user_id=user_id, role=role)
    if m:
        return m
    m = Membership(id=uid(), tenant_id=TENANT_ID, user_id=user_id, role=role,
                   status=MembershipStatus.ACTIVE)
    db.add(m)
    db.flush()
    return m


def ensure_site(db: Session) -> Site:
    s = get_or_none(db, Site, id="site-padang")
    if s:
        return s
    s = Site(
        id="site-padang", tenant_id=TENANT_ID,
        site_code="PADANG-01", site_name="Padang Mine",
        site_type=SiteType.MINE_SITE, status=SiteStatus.ACTIVE,
        timezone="Asia/Makassar", effective_from=DEMO_START,
        notes="[SIMULATION/NON_PRODUCTION] Geofence data TBC",
    )
    db.add(s)
    db.flush()
    return s


def ensure_shift(db: Session, sid: str, code: str, name: str,
                 start: time, end: time,
                 brk_start: time, brk_end: time,
                 ho_start: time, ho_end: time,
                 crosses: bool) -> ShiftTemplate:
    st = get_or_none(db, ShiftTemplate, id=sid)
    if st:
        return st
    st = ShiftTemplate(
        id=sid, tenant_id=TENANT_ID,
        shift_code=code, shift_name=name,
        start_time=start, end_time=end,
        break_start=brk_start, break_end=brk_end,
        handover_start=ho_start, handover_end=ho_end,
        crosses_midnight=crosses,
    )
    db.add(st)
    db.flush()
    return st


def ensure_role(db: Session, rid: str, code: str, name: str, eq_type: str | None) -> Role:
    r = get_or_none(db, Role, id=rid)
    if r:
        return r
    r = Role(id=rid, tenant_id=TENANT_ID, role_code=code, role_name=name,
             equipment_type_required=eq_type, status="ACTIVE")
    db.add(r)
    db.flush()
    return r


def ensure_crew(db: Session, cid: str, code: str, name: str,
                anchor: date, offset: int) -> Crew:
    c = get_or_none(db, Crew, id=cid)
    if c:
        return c
    c = Crew(id=cid, tenant_id=TENANT_ID, crew_code=code, crew_name=name,
             onsite_cycle_anchor=anchor, cycle_offset_days=offset)
    db.add(c)
    db.flush()
    return c


def ensure_worker(db: Session, wid: str, code: str, name: str) -> Worker:
    w = get_or_none(db, Worker, id=wid)
    if w:
        return w
    w = Worker(id=wid, tenant_id=TENANT_ID, code=code, name=name, is_active=True,
               pin_hash=hash_password("1234"))
    # Also update existing workers without pin_hash
    db.add(w)
    db.flush()
    return w


def ensure_equipment(db: Session, eid: str, code: str, eq_type: str) -> Equipment:
    e = get_or_none(db, Equipment, id=eid)
    if e:
        return e
    e = Equipment(id=eid, tenant_id=TENANT_ID, equipment_code=code,
                  equipment_type=eq_type, status=EquipmentStatus.ACTIVE,
                  effective_from=DEMO_START)
    db.add(e)
    db.flush()
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
    db.add(em)
    db.flush()
    return em


def ensure_competency(db: Session, comp_id: str, worker_id: str,
                      eq_type: str, cert_no: str) -> Competency:
    c = get_or_none(db, Competency, id=comp_id)
    if c:
        return c
    c = Competency(
        id=comp_id, tenant_id=TENANT_ID,
        competency_code=f"COMP-{eq_type}",
        employee_id=worker_id, equipment_type=eq_type,
        certification_no=cert_no,
        valid_from=DEMO_START, status=CompetencyStatus.VALID,
        source="SEED",
    )
    db.add(c)
    db.flush()
    return c


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
    db.add(cp)
    db.flush()
    return cp


def ensure_roster_policy(db: Session, rp_id: str, key: str, value: str,
                         dtype: str, status: str) -> RosterPolicy:
    rp = get_or_none(db, RosterPolicy, id=rp_id)
    if rp:
        # Converge to the decided value/status (so re-seed corrects stale rows,
        # e.g. minimum_rest_hours "TBC" -> "1").
        if rp.policy_value != value or rp.confirmation_status != status or rp.data_type != dtype:
            rp.policy_key = key
            rp.policy_value = value
            rp.data_type = dtype
            rp.confirmation_status = status
            db.flush()
        return rp
    rp = RosterPolicy(
        id=rp_id, tenant_id=TENANT_ID,
        policy_key=key, policy_value=value,
        data_type=dtype, confirmation_status=status,
    )
    db.add(rp)
    db.flush()
    return rp


def ensure_roster_assignment(db: Session, ra_id: str, op_date: date,
                              emp_id: str, crew_id: str,
                              work_status: WorkStatus,
                              shift_id: str | None,
                              site_id: str | None,
                              equip_id: str | None,
                              rule_ver: RuleVersion | None,
                              *,
                              site_cycle_day: int = 0,
                              site_status: str | None = None) -> RosterAssignment:
    ra = get_or_none(db, RosterAssignment, id=ra_id)
    if ra:
        return ra
    if site_status is None:
        # REST stays ON-SITE (worker is on-site but resting) — only OFFSITE
        # (out-cycle) is truly off-site.
        site_status = SiteStatusEnum.OFFSITE if work_status == WorkStatus.OFFSITE else SiteStatusEnum.ONSITE
    else:
        site_status = SiteStatusEnum(site_status)
    ra = RosterAssignment(
        id=ra_id, tenant_id=TENANT_ID,
        roster_code=f"ROSTER-{op_date.strftime('%Y%m%d')}",
        operating_date=op_date,
        employee_id=emp_id, crew_id=crew_id,
        site_cycle_day=site_cycle_day, site_status=site_status,
        work_status=work_status,
        shift_id=shift_id, site_id=site_id,
        planned_equipment_id=equip_id,
        rule_version_id=rule_ver.id if rule_ver else None,
        effective_rule_version=rule_ver.version_label if rule_ver else None,
        validation_status=ValidationStatus.DRAFT,
    )
    db.add(ra)
    db.flush()
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
        timezone="Asia/Makassar",
        operating_date=op_date, shift_id=shift_id, site_id=site_id,
        source="SEED", source_event_id=source_ev_id,
        processing_status=CanonicalProcessingStatus.VALID,
    )
    db.add(ce)
    db.flush()
    return ce


def ensure_actual_assignment(db: Session, aa_id: str, emp_id: str,
                              equip_id: str, op_date: date,
                              shift_id: str | None,
                              site_id: str | None,
                              started_at: datetime,
                              ended_at: datetime | None,
                              roster_id: str | None) -> EquipmentAssignmentActual:
    aa = get_or_none(db, EquipmentAssignmentActual, id=aa_id)
    if aa:
        return aa
    aa = EquipmentAssignmentActual(
        id=aa_id, tenant_id=TENANT_ID,
        employee_id=emp_id, equipment_id=equip_id,
        operating_date=op_date, shift_id=shift_id, site_id=site_id,
        started_at=started_at, ended_at=ended_at,
        source="SEED", status=ActualAssignmentStatus.ACTIVE,
        roster_id=roster_id,
    )
    db.add(aa)
    db.flush()
    return aa


def ensure_comparison_result(db: Session, cr_id: str, actual_id: str,
                              emp_id: str, op_date: date,
                              shift_id: str | None,
                              planned_eq: str | None,
                              actual_eq: str,
                              result: ComparisonResult,
                              planned_worker: str | None,
                              actual_worker: str) -> EquipmentComparisonResult:
    cr = get_or_none(db, EquipmentComparisonResult, id=cr_id)
    if cr:
        return cr
    cr = EquipmentComparisonResult(
        id=cr_id, tenant_id=TENANT_ID,
        actual_assignment_id=actual_id,
        employee_id=emp_id, operating_date=op_date, shift_id=shift_id,
        planned_equipment_id=planned_eq, actual_equipment_id=actual_eq,
        comparison_result=result,
        planned_worker_id=planned_worker, actual_worker_id=actual_worker,
    )
    db.add(cr)
    db.flush()
    return cr


def ensure_discrepancy(db: Session, disc_id: str, actual_id: str,
                        emp_id: str, op_date: date,
                        shift_id: str | None,
                        planned_eq: str | None, actual_eq: str,
                        planned_worker: str | None, actual_worker: str,
                        disc_type: DiscrepancyType) -> EquipmentDiscrepancy:
    d = get_or_none(db, EquipmentDiscrepancy, id=disc_id)
    if d:
        return d
    d = EquipmentDiscrepancy(
        id=disc_id, tenant_id=TENANT_ID,
        actual_assignment_id=actual_id,
        employee_id=emp_id, operating_date=op_date, shift_id=shift_id,
        planned_equipment_id=planned_eq, actual_equipment_id=actual_eq,
        planned_worker_id=planned_worker, actual_worker_id=actual_worker,
        discrepancy_type=disc_type,
        status=DiscrepancyStatus.OPEN,
        source="SEED",
    )
    db.add(d)
    db.flush()
    return d


def ensure_rule_evaluation(db: Session, re_id: str, emp_id: str,
                            op_date: date, shift_id: str | None,
                            rule_code: str,
                            rule_ver: RuleVersion | None,
                            status: RuleEvaluationStatus,
                            severity: RuleSeverity,
                            actual_val: str, expected_val: str,
                            evidence_key: str,
                            reason: str) -> RuleEvaluation:
    re = get_or_none(db, RuleEvaluation, id=re_id)
    if re:
        return re
    re = RuleEvaluation(
        id=re_id, tenant_id=TENANT_ID,
        employee_id=emp_id, operating_date=op_date, shift_id=shift_id,
        rule_code=rule_code,
        rule_version_id=rule_ver.id if rule_ver else None,
        status=status, severity=severity,
        actual_value=actual_val, expected_value=expected_val,
        evidence_key=evidence_key,
        reason=reason,
    )
    db.add(re)
    db.flush()
    return re


def ensure_exception_case(db: Session, ec_id: str, emp_id: str,
                           op_date: date, shift_id: str | None,
                           source_type: str, source_id: str,
                           exc_type: str,
                           severity: ExceptionSeverity,
                           rule_ver: RuleVersion | None,
                           detected_at: datetime,
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
    db.add(ec)
    db.flush()
    return ec


# ---------------------------------------------------------------------------
# Demo-state reset (--reset): hapus state exception yang bisa dimutasi saat
# rehearsal/demo, supaya reseed menghasilkan kasus fresh status OPEN.
# Idempoten tanpa flag TETAP terjaga (ensure_* return-early).
# ---------------------------------------------------------------------------
RESET_TABLES = [
    "exception_actions",
    "exception_evidence",
    "exception_decisions",
    "exception_cases",
]


def reset_demo_state() -> None:
    with engine.begin() as conn:
        for tbl in RESET_TABLES:
            res = conn.execute(text(f'DELETE FROM {tbl}'))
            print(f"  reset: {tbl}: {res.rowcount} row dihapus")


# ---------------------------------------------------------------------------
# Main seed
# ---------------------------------------------------------------------------
def seed():
    if "--reset" in sys.argv:
        print("== RESET demo state ==")
        reset_demo_state()
    counts = {}
    db = SessionLocal()
    try:
        # ── Tenant ──────────────────────────────────────────────────────
        tenant = ensure_tenant(db)
        counts["tenant"] = 1

        # ── Users & Memberships ─────────────────────────────────────────
        admin = ensure_user(db, "metro-admin-001", "admin@metro-mining.id", "Metro Admin")
        sup = ensure_user(db, "metro-sup-001", "supervisor@metro-mining.id", "Supervisor Demo")
        ensure_membership(db, admin.id, RoleCode.OWNER)
        ensure_membership(db, sup.id, RoleCode.SUPERVISOR)
        counts["users"] = 2
        counts["memberships"] = 2

        # ── Site ────────────────────────────────────────────────────────
        site = ensure_site(db)
        counts["sites"] = 1

        # ── Shift Templates ─────────────────────────────────────────────
        day_shift = ensure_shift(
            db, "DAY", "DAY", "Shift Siang",
            time(7, 0), time(19, 0),
            time(12, 0), time(13, 0),
            time(18, 45), time(19, 0),
            False,
        )
        night_shift = ensure_shift(
            db, "NIGHT", "NIGHT", "Shift Malam",
            time(19, 0), time(7, 0),
            time(0, 0), time(1, 0),
            time(6, 45), time(7, 0),
            True,
        )
        counts["shift_templates"] = 2

        # ── Roles ───────────────────────────────────────────────────────
        role_exc = ensure_role(db, "role_exc", "role_exc", "Excavator Operator", "EXCAVATOR")
        role_dt = ensure_role(db, "role_dt", "role_dt", "Dump Truck Operator", "DUMP_TRUCK")
        counts["roles"] = 2

        # ── Crews ───────────────────────────────────────────────────────
        crew_a = ensure_crew(db, "crew_a", "crew_a", "Crew Alpha", DEMO_START, 0)
        crew_b = ensure_crew(db, "crew_b", "crew_b", "Crew Bravo", DEMO_START, 1)
        counts["crews"] = 2

        # ── Workers (12) ────────────────────────────────────────────────
        workers_def = [
            ("w1",  "W001", "Budi Santoso"),
            ("w2",  "W002", "Andi Pratama"),
            ("w3",  "W003", "Dedi Kurniawan"),
            ("w4",  "W004", "Tono Sugiarto"),
            ("w5",  "W005", "Rizal Maulana"),
            ("w6",  "W006", "Hendra Wijaya"),
            ("w7",  "W007", "Agus Setiawan"),
            ("w8",  "W008", "Joko Susilo"),
            ("w9",  "W009", "Rudi Hartono"),
            ("w10", "W010", "Slamet Riyadi"),
            ("w11", "W011", "Wahyu Nugroho"),
            ("w12", "W012", "Dimas Prayogo"),
        ]
        workers = {}
        for wid, code, name in workers_def:
            workers[wid] = ensure_worker(db, wid, code, name)
        counts["workers"] = len(workers)

        # ── Equipment (3) ───────────────────────────────────────────────
        ex25 = ensure_equipment(db, "ex25", "EX-025", "EXCAVATOR")
        ex31 = ensure_equipment(db, "ex31", "EX-031", "EXCAVATOR")
        dt14 = ensure_equipment(db, "dt14", "DT-014", "DUMP_TRUCK")
        counts["equipment"] = 3

        # ── EmployeeMeta (12) ───────────────────────────────────────────
        em_map = {
            "w1":  ("em-w1",  role_exc.id, crew_a.id),
            "w2":  ("em-w2",  role_dt.id,  crew_a.id),
            "w3":  ("em-w3",  role_exc.id, crew_a.id),
            "w4":  ("em-w4",  role_dt.id,  crew_b.id),
            "w5":  ("em-w5",  role_exc.id, crew_b.id),
            "w6":  ("em-w6",  role_dt.id,  crew_b.id),
            "w7":  ("em-w7",  role_exc.id, crew_a.id),
            "w8":  ("em-w8",  role_dt.id,  crew_a.id),
            "w9":  ("em-w9",  role_exc.id, crew_b.id),
            "w10": ("em-w10", role_dt.id,  crew_b.id),
            "w11": ("em-w11", role_exc.id, crew_a.id),
            "w12": ("em-w12", role_dt.id,  crew_b.id),
        }
        for wid, (em_id, rid, cid) in em_map.items():
            ensure_employee_meta(db, em_id, workers[wid].id, rid, cid)
        counts["employee_meta"] = len(em_map)

        # ── Competencies (6) ────────────────────────────────────────────
        comp_defs = [
            ("comp-w1",  "w1",  "EXCAVATOR",  "CERT-EX-001"),
            ("comp-w2",  "w2",  "DUMP_TRUCK", "CERT-DT-001"),
            ("comp-w3",  "w3",  "EXCAVATOR",  "CERT-EX-002"),
            ("comp-w4",  "w4",  "DUMP_TRUCK", "CERT-DT-002"),
            ("comp-w5",  "w5",  "EXCAVATOR",  "CERT-EX-003"),
            ("comp-w6",  "w6",  "DUMP_TRUCK", "CERT-DT-003"),
        ]
        for comp_id, wid, eq_type, cert_no in comp_defs:
            ensure_competency(db, comp_id, workers[wid].id, eq_type, cert_no)
        counts["competencies"] = len(comp_defs)

        # ── Checkpoint Policies (7 types × 2 shifts = 14) ──────────────
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
            for shift in [day_shift, night_shift]:
                cp_id = f"cp-{cp_type.lower()}-{shift.id.lower()}"
                ensure_checkpoint_policy(db, cp_id, cp_type, shift.id, seq, ws, we)
                cp_count += 1
        counts["checkpoint_policies"] = cp_count

        # ── Roster Policies ─────────────────────────────────────────────
        ensure_roster_policy(db, "rp-equip", "equipment_assignment_enabled", "true", "boolean", "CONFIRMED")
        ensure_roster_policy(db, "rp-comp",  "competency_validation_enabled", "true", "boolean", "CONFIRMED")
        ensure_roster_policy(db, "rp-rest",  "minimum_rest_hours", "1", "integer", "CONFIRMED")
        ensure_roster_policy(db, "rp-geo",   "geofence_radius_m", "", "integer", "TBC")

        # Cycle / rest / shift rules — Decisions D4–D8 (docs/TBC_REGISTER.md).
        ensure_roster_policy(db, "rp-maxwork",  "max_consecutive_workdays", "12", "integer", "CONFIRMED")
        ensure_roster_policy(db, "rp-restdays", "mandatory_rest_days", "1", "integer", "CONFIRMED")
        ensure_roster_policy(db, "rp-sameshift", "max_same_shift_streak", "7", "integer", "CONFIRMED")
        ensure_roster_policy(db, "rp-onsite",   "onsite_weeks", "12", "integer", "CONFIRMED")
        ensure_roster_policy(db, "rp-offsite",  "offsite_weeks", "2", "integer", "CONFIRMED")
        counts["roster_policies"] = 9

        # ── Rule Version ────────────────────────────────────────────────
        rule_ver = snapshot_rules(db, TENANT_ID, "METRO-RULE-v0.1", DEMO_START)
        counts["rule_versions"] = 1

        # ── Roster Assignments (2026-09-01 to 2026-09-07) ──────────────
        # Build lookup for planned equipment per worker
        planned_equip = {
            "w1": ex25.id, "w2": dt14.id, "w3": ex31.id,
            "w4": dt14.id, "w5": ex25.id, "w6": ex31.id,
        }

        # Per-worker cycle context (crew + cycle offset + equipment key).
        # The roster is GENUINELY generated from the cycle rules — each worker
        # is anchored to a different phase of the 98-day cycle on DEMO_START so
        # the demo snapshot shows the intended operating states:
        #   offset 0   -> WORK / DAY      (w1..w3, w7, w8)
        #   offset 8   -> WORK / NIGHT    (w4..w6, w9, w10)
        #   offset 12  -> REST  (day-13 rest boundary, resets counter)
        #   offset 84  -> OFFSITE (first day of the 2-week off-site block)
        worker_cycle = {
            "w1":  (crew_a.id, 0,  "w1"),
            "w2":  (crew_a.id, 0,  "w2"),
            "w3":  (crew_a.id, 0,  "w3"),
            "w4":  (crew_b.id, 8,  "w4"),
            "w5":  (crew_b.id, 8,  "w5"),
            "w6":  (crew_b.id, 8,  "w6"),
            "w7":  (crew_a.id, 0,  None),
            "w8":  (crew_a.id, 0,  None),
            "w9":  (crew_b.id, 8,  None),
            "w10": (crew_b.id, 8,  None),
            "w11": (crew_a.id, 12, None),
            "w12": (crew_b.id, 84, None),
        }

        shift_id_by_key = {"DAY": day_shift.id, "NIGHT": night_shift.id}
        ws_by_key = {
            "WORK": WorkStatus.WORK,
            "REST": WorkStatus.REST,
            "OFFSITE": WorkStatus.OFFSITE,
        }

        ra_count = 0
        # Store roster IDs for w1, w2, w4 on 2026-09-01 (needed for operational data)
        roster_ids = {}
        # Generator consumes integer policy only (minimum_rest_hours is a
        # validator concern, not a generator input). Reading from the seeded
        # RosterPolicy rows keeps "output = persis input": the roster is
        # derived from the configured rules, never re-declared.
        _GEN_KEYS = (
            "onsite_weeks", "offsite_weeks",
            "max_consecutive_workdays", "mandatory_rest_days",
            "max_same_shift_streak",
        )
        policy = {k: v for k, v in read_roster_policy(db, TENANT_ID).items() if k in _GEN_KEYS}
        for wid, (cid, offset, eq_key) in worker_cycle.items():
            plans = generate_assignments(DEMO_START, offset, DEMO_START, DEMO_END, **policy)
            for p in plans:
                ds = p["date"].strftime("%Y%m%d")
                ra_id = f"ra-{wid}-{ds}"
                ws = ws_by_key[p["work_status"]]
                shift_id = shift_id_by_key[p["shift_key"]] if p["shift_key"] else None
                equip_id = (
                    planned_equip.get(eq_key)
                    if (p["work_status"] == "WORK" and eq_key) else None
                )
                ra = ensure_roster_assignment(
                    db, ra_id, p["date"], workers[wid].id, cid,
                    ws, shift_id, site.id, equip_id, rule_ver,
                    site_status=p["site_status"],
                    site_cycle_day=p["site_cycle_day"],
                )
                if p["date"] == DEMO_START and wid in ("w1", "w2", "w4"):
                    roster_ids[wid] = ra.id
                ra_count += 1
        counts["roster_assignments"] = ra_count

        # ── Demo Operational Data (2026-09-01 only) ─────────────────────
        op_date = DEMO_START

        # Canonical Attendance Events
        # w1: CHECK_IN 06:55 WITA, CHECK_OUT 19:05 WITA
        w1_in_local = datetime(2026, 9, 1, 6, 55)
        w1_in_utc = wita_to_utc(w1_in_local)
        ce_w1_in = ensure_canonical_event(
            db, "ce-w1-in", workers["w1"].id, CanonicalEventType.CHECK_IN,
            w1_in_local.replace(tzinfo=timezone(WITA_OFFSET)),
            w1_in_utc, op_date, day_shift.id, site.id, "seed-w1-checkin",
        )

        w1_out_local = datetime(2026, 9, 1, 19, 5)
        w1_out_utc = wita_to_utc(w1_out_local)
        ce_w1_out = ensure_canonical_event(
            db, "ce-w1-out", workers["w1"].id, CanonicalEventType.CHECK_OUT,
            w1_out_local.replace(tzinfo=timezone(WITA_OFFSET)),
            w1_out_utc, op_date, day_shift.id, site.id, "seed-w1-checkout",
        )

        # w2: CHECK_IN 07:10 WITA (late), CHECK_OUT 19:00 WITA
        w2_in_local = datetime(2026, 9, 1, 7, 10)
        w2_in_utc = wita_to_utc(w2_in_local)
        ce_w2_in = ensure_canonical_event(
            db, "ce-w2-in", workers["w2"].id, CanonicalEventType.CHECK_IN,
            w2_in_local.replace(tzinfo=timezone(WITA_OFFSET)),
            w2_in_utc, op_date, day_shift.id, site.id, "seed-w2-checkin",
        )

        w2_out_local = datetime(2026, 9, 1, 19, 0)
        w2_out_utc = wita_to_utc(w2_out_local)
        ce_w2_out = ensure_canonical_event(
            db, "ce-w2-out", workers["w2"].id, CanonicalEventType.CHECK_OUT,
            w2_out_local.replace(tzinfo=timezone(WITA_OFFSET)),
            w2_out_utc, op_date, day_shift.id, site.id, "seed-w2-checkout",
        )

        # w4: CHECK_IN 18:55 WITA, CHECK_OUT 07:05 WITA (next day)
        w4_in_local = datetime(2026, 9, 1, 18, 55)
        w4_in_utc = wita_to_utc(w4_in_local)
        ce_w4_in = ensure_canonical_event(
            db, "ce-w4-in", workers["w4"].id, CanonicalEventType.CHECK_IN,
            w4_in_local.replace(tzinfo=timezone(WITA_OFFSET)),
            w4_in_utc, op_date, night_shift.id, site.id, "seed-w4-checkin",
        )

        w4_out_local = datetime(2026, 9, 2, 7, 5)  # next day
        w4_out_utc = wita_to_utc(w4_out_local)
        ce_w4_out = ensure_canonical_event(
            db, "ce-w4-out", workers["w4"].id, CanonicalEventType.CHECK_OUT,
            w4_out_local.replace(tzinfo=timezone(WITA_OFFSET)),
            w4_out_utc, op_date, night_shift.id, site.id, "seed-w4-checkout",
        )
        counts["canonical_events"] = 6

        # Equipment Assignment Actuals
        # w1 → ex25 (MATCH)
        aa_w1_start = aware_utc(datetime(2026, 9, 1, 7, 0))
        aa_w1_end = aware_utc(datetime(2026, 9, 1, 19, 0))
        aa_w1 = ensure_actual_assignment(
            db, "aa-w1-0901", workers["w1"].id, ex25.id,
            op_date, day_shift.id, site.id,
            aa_w1_start, aa_w1_end, roster_ids.get("w1"),
        )

        # w2 → ex31 (MISMATCH — planned dt14)
        aa_w2_start = aware_utc(datetime(2026, 9, 1, 7, 10))
        aa_w2_end = aware_utc(datetime(2026, 9, 1, 19, 0))
        aa_w2 = ensure_actual_assignment(
            db, "aa-w2-0901", workers["w2"].id, ex31.id,
            op_date, day_shift.id, site.id,
            aa_w2_start, aa_w2_end, roster_ids.get("w2"),
        )

        # w4 → dt14 (MATCH)
        aa_w4_start = aware_utc(datetime(2026, 9, 1, 19, 0))
        aa_w4_end = aware_utc(datetime(2026, 9, 2, 7, 5))
        aa_w4 = ensure_actual_assignment(
            db, "aa-w4-0901", workers["w4"].id, dt14.id,
            op_date, night_shift.id, site.id,
            aa_w4_start, aa_w4_end, roster_ids.get("w4"),
        )
        counts["actual_assignments"] = 3

        # Equipment Comparison Results
        # w1: MATCH
        ensure_comparison_result(
            db, "cr-w1-0901", aa_w1.id, workers["w1"].id,
            op_date, day_shift.id,
            ex25.id, ex25.id, ComparisonResult.MATCH,
            workers["w1"].id, workers["w1"].id,
        )
        # w2: MISMATCH
        ensure_comparison_result(
            db, "cr-w2-0901", aa_w2.id, workers["w2"].id,
            op_date, day_shift.id,
            dt14.id, ex31.id, ComparisonResult.MISMATCH,
            workers["w2"].id, workers["w2"].id,
        )
        # w4: MATCH
        ensure_comparison_result(
            db, "cr-w4-0901", aa_w4.id, workers["w4"].id,
            op_date, night_shift.id,
            dt14.id, dt14.id, ComparisonResult.MATCH,
            workers["w4"].id, workers["w4"].id,
        )
        counts["comparison_results"] = 3

        # Equipment Discrepancy (w2's mismatch)
        disc_w2 = ensure_discrepancy(
            db, "disc-w2-0901", aa_w2.id, workers["w2"].id,
            op_date, day_shift.id,
            dt14.id, ex31.id,
            workers["w2"].id, workers["w2"].id,
            DiscrepancyType.EQUIPMENT_MISMATCH,
        )
        counts["discrepancies"] = 1

        # Rule Evaluation (w2's late arrival)
        re_w2 = ensure_rule_evaluation(
            db, "re-w2-late-0901", workers["w2"].id,
            op_date, day_shift.id,
            "LATE_CHECK_IN", rule_ver,
            RuleEvaluationStatus.FAIL, RuleSeverity.WARNING,
            "07:10", "07:00",
            f"w2-{op_date}-CHECK_IN",
            "Check-in at 07:10 WITA exceeds shift start 07:00 WITA",
        )
        counts["rule_evaluations"] = 1

        # Exception Case (from w2's equipment discrepancy)
        detected_ts = aware_utc(datetime(2026, 9, 1, 19, 5))
        ensure_exception_case(
            db, "exc-w2-equip-0901", workers["w2"].id,
            op_date, day_shift.id,
            ExceptionSourceType.EQUIPMENT_DISCREPANCY.value,
            disc_w2.id,
            "EQUIPMENT_MISMATCH",
            ExceptionSeverity.WARNING,
            rule_ver,
            detected_ts,
            sup.id,
        )
        counts["exception_cases"] = 1

        # ── Commit ──────────────────────────────────────────────────────
        db.commit()

        # ── Summary ─────────────────────────────────────────────────────
        print("=" * 60)
        print("  Metro Mining Seed — COMPLETE")
        print("=" * 60)
        print(f"  Tenant:              {tenant.code} ({tenant.id})")
        print(f"  Database:            {DB_URL.split('@')[-1] if '@' in DB_URL else DB_URL}")
        print()
        for key, val in counts.items():
            print(f"  {key:25s}: {val}")
        print()
        print(f"  Demo period:         {DEMO_START} → {DEMO_END}")
        print(f"  Operational data:    {DEMO_START} only")
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
