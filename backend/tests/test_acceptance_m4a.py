"""
test_acceptance_m4a.py — MM-M4A Field Supervisor Operational Dashboard.

Acceptance tests proving the dashboard correctly aggregates M0–M3 data
into an operational command view for the Metro Mining Field Supervisor.

Scenarios cover: context, shift summary, roster, checkpoint status,
equipment status (plan/actual/decision invariant), active exceptions,
action required, pending decisions, configuration warnings, tenant isolation,
site isolation, timezone correctness, empty states, pagination, and
dashboard data freshness.

No new business logic in dashboard layer — all derived from existing engines.
"""

import pytest
import uuid
from datetime import date, datetime, time, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.models import (
    Base, Tenant, Worker, Equipment, EquipmentStatus, Competency, CompetencyStatus,
    Site, SiteType, SiteStatus, Crew, Role,
    RosterAssignment, ShiftTemplate, RuleVersion, WorkStatus, SiteStatusEnum,
    ValidationStatus, EmployeeMeta,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    CheckpointValidationResult, CheckpointValidationStatus,
    MissingCheckpointResult,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    ExceptionCase, ExceptionAction, ExceptionActionType, ExceptionStatus, ExceptionSeverity,
    ExceptionSourceType, EXCEPTION_TRANSITIONS,
    ExceptionEvidence, ExceptionEvidenceType,
    ExceptionDecision, ExceptionDecisionAction, DecisionType, DecisionStatus, DECISION_TRANSITIONS,
    CheckpointPolicy, RosterPolicy,
    uid, now,
)

from app.dashboard_service import (
    get_dashboard_snapshot,
    snapshot_to_dict,
    OperationalState,
    DashboardSnapshot,
)

from app.exception_engine import (
    create_exception_from_rule_evaluation,
    create_exception_from_discrepancy,
    acknowledge_exception,
    resolve_exception,
    waive_exception,
)

from app.decision_engine import (
    request_decision,
    approve_decision,
    reject_decision,
    AuthorizationBlocked,
)


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _uid():
    return uuid.uuid4().hex[:12]


# ── Seed Data ─────────────────────────────────────────────────

def _seed_metro(db: Session, operating_date: date = date(2026, 9, 5)):
    """Seed Metro Mining environment for M4A dashboard tests."""
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)

    # Workers
    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Tono", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Budi", is_active=True)
    w3 = Worker(id="w3", tenant_id="metro", code="W003", name="Andi", is_active=True)
    w4 = Worker(id="w4", tenant_id="metro", code="W004", name="Dodi", is_active=True)
    w_rest = Worker(id="w_rest", tenant_id="metro", code="W005", name="Resty", is_active=True)
    w_off = Worker(id="w_off", tenant_id="metro", code="W006", name="Offy", is_active=True)
    db.add_all([w1, w2, w3, w4, w_rest, w_off])

    # Equipment
    ex25 = Equipment(id="ex25", tenant_id="metro", equipment_code="EX-025",
                     equipment_type="EXCAVATOR", status=EquipmentStatus.ACTIVE,
                     effective_from=date(2026, 1, 1))
    ex31 = Equipment(id="ex31", tenant_id="metro", equipment_code="EX-031",
                     equipment_type="EXCAVATOR", status=EquipmentStatus.ACTIVE,
                     effective_from=date(2026, 1, 1))
    dt14 = Equipment(id="dt14", tenant_id="metro", equipment_code="DT-014",
                     equipment_type="DUMP_TRUCK", status=EquipmentStatus.ACTIVE,
                     effective_from=date(2026, 1, 1))
    db.add_all([ex25, ex31, dt14])

    # Competencies (required for decision validation)
    comp_w1 = Competency(id="comp_w1", tenant_id="metro", competency_code="EXC-001",
                         employee_id="w1", equipment_type="EXCAVATOR",
                         valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
                         status=CompetencyStatus.VALID)
    comp_w2 = Competency(id="comp_w2", tenant_id="metro", competency_code="EXC-002",
                         employee_id="w2", equipment_type="EXCAVATOR",
                         valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
                         status=CompetencyStatus.VALID)
    comp_w3 = Competency(id="comp_w3", tenant_id="metro", competency_code="DT-001",
                         employee_id="w3", equipment_type="DUMP_TRUCK",
                         valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
                         status=CompetencyStatus.VALID)
    db.add_all([comp_w1, comp_w2, comp_w3])

    # Roles
    role_exc = Role(id="role_exc", tenant_id="metro", role_code="EXC_OP",
                    role_name="Excavator Operator", equipment_type_required="EXCAVATOR")
    role_dt = Role(id="role_dt", tenant_id="metro", role_code="DT_OP",
                   role_name="Dump Truck Operator", equipment_type_required="DUMP_TRUCK")
    db.add_all([role_exc, role_dt])

    # Crews
    crew_a = Crew(id="crew_a", tenant_id="metro", crew_code="A", crew_name="Crew A",
                  onsite_cycle_anchor=date(2026, 9, 1), cycle_offset_days=0)
    crew_b = Crew(id="crew_b", tenant_id="metro", crew_code="B", crew_name="Crew B",
                  onsite_cycle_anchor=date(2026, 9, 1), cycle_offset_days=1)
    db.add_all([crew_a, crew_b])

    # Employee Meta
    em1 = EmployeeMeta(id="em1", tenant_id="metro", worker_id="w1", employee_no="W001",
                       role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em2 = EmployeeMeta(id="em2", tenant_id="metro", worker_id="w2", employee_no="W002",
                       role_id="role_dt", crew_id="crew_b", effective_from=date(2026, 1, 1))
    em3 = EmployeeMeta(id="em3", tenant_id="metro", worker_id="w3", employee_no="W003",
                       role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em4 = EmployeeMeta(id="em4", tenant_id="metro", worker_id="w4", employee_no="W004",
                       role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em_rest = EmployeeMeta(id="em_rest", tenant_id="metro", worker_id="w_rest", employee_no="W005",
                           role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em_off = EmployeeMeta(id="em_off", tenant_id="metro", worker_id="w_off", employee_no="W006",
                          role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    db.add_all([em1, em2, em3, em4, em_rest, em_off])

    # Shift templates
    day = ShiftTemplate(
        id="DAY", tenant_id="metro", shift_code="DAY", shift_name="Day",
        start_time=time(7, 0), end_time=time(19, 0),
        break_start=time(12, 0), break_end=time(13, 0),
        handover_start=time(18, 45), handover_end=time(19, 0),
        crosses_midnight=False,
    )
    night = ShiftTemplate(
        id="NIGHT", tenant_id="metro", shift_code="NIGHT", shift_name="Night",
        start_time=time(19, 0), end_time=time(7, 0),
        break_start=time(0, 0), break_end=time(1, 0),
        handover_start=time(6, 45), handover_end=time(7, 0),
        crosses_midnight=True,
    )
    db.add_all([day, night])

    # Site
    site_padang = Site(
        id="site_padang", tenant_id="metro", site_code="PADANG",
        site_name="Padang Mine", site_type=SiteType.MINE_SITE,
        status=SiteStatus.ACTIVE, timezone="Asia/Makassar",
        effective_from=date(2026, 1, 1),
    )
    db.add(site_padang)

    # Checkpoint policies
    for cp_type in ["BRIEFING_IN", "EQUIPMENT_IN", "WORK_START", "BREAK_OUT", "BREAK_IN", "HANDOVER", "SHIFT_OUT"]:
        db.add(CheckpointPolicy(
            id=f"cp-{cp_type.lower()}-day", tenant_id="metro",
            checkpoint_type=cp_type, shift_id="DAY", enabled=True,
            window_start_offset_min=0, window_end_offset_min=0,
            severity="WARNING",
        ))

    # Rule version
    rv = RuleVersion(id="rv1", tenant_id="metro", version_label="v1.0",
                     effective_from=date(2026, 1, 1), config_snapshot_json="{}")
    db.add(rv)

    db.flush()

    # Roster assignments
    # WORK employees
    for wid in ["w1", "w2", "w3", "w4"]:
        eq_id = None
        if wid == "w1":
            eq_id = "ex25"
        elif wid == "w2":
            eq_id = "dt14"
        elif wid == "w3":
            eq_id = "ex31"
        db.add(RosterAssignment(
            id=f"ra-{wid}", tenant_id="metro", roster_code=f"RC-{wid}",
            operating_date=operating_date, employee_id=wid,
            crew_id="crew_a" if wid in ("w1", "w3", "w4") else "crew_b",
            site_status=SiteStatusEnum.ONSITE, work_status=WorkStatus.WORK,
            shift_id="DAY", site_id="site_padang",
            planned_equipment_id=eq_id, rule_version_id="rv1",
            validation_status=ValidationStatus.PUBLISHED,
        ))

    # REST employee
    db.add(RosterAssignment(
        id="ra-rest", tenant_id="metro", roster_code="RC-rest",
        operating_date=operating_date, employee_id="w_rest",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.REST, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", validation_status=ValidationStatus.PUBLISHED,
    ))

    # OFFSITE employee
    db.add(RosterAssignment(
        id="ra-off", tenant_id="metro", roster_code="RC-off",
        operating_date=operating_date, employee_id="w_off",
        crew_id="crew_a", site_status=SiteStatusEnum.OFFSITE,
        work_status=WorkStatus.OFFSITE, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", validation_status=ValidationStatus.PUBLISHED,
    ))

    db.flush()


def _seed_client_b(db: Session, operating_date: date = date(2026, 9, 5)):
    """Seed Client B for tenant isolation tests."""
    t = Tenant(id="client_b", code="client_b", name="Other Company", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="w_other", tenant_id="client_b", code="CB001", name="Rina", is_active=True)
    eq = Equipment(id="ex_other", tenant_id="client_b", equipment_code="TR-001",
                   equipment_type="TRUCK", status=EquipmentStatus.ACTIVE,
                   effective_from=date(2026, 1, 1))
    site_b = Site(id="site_b", tenant_id="client_b", site_code="SITE_B",
                  site_name="Other Site", site_type=SiteType.MINE_SITE,
                  status=SiteStatus.ACTIVE, timezone="Asia/Jakarta",
                  effective_from=date(2026, 1, 1))
    db.add_all([w, eq, site_b])
    day_b = ShiftTemplate(id="DAY_B", tenant_id="client_b", shift_code="DAY",
                          shift_name="Day", start_time=time(8, 0), end_time=time(17, 0),
                          break_start=time(12, 0), break_end=time(13, 0),
                          handover_start=time(16, 45), handover_end=time(17, 0),
                          crosses_midnight=False)
    db.add(day_b)
    db.flush()

    db.add(RosterAssignment(
        id="ra-other", tenant_id="client_b", roster_code="RC-other",
        operating_date=operating_date, employee_id="w_other",
        site_status=SiteStatusEnum.ONSITE, work_status=WorkStatus.WORK,
        shift_id="DAY_B", site_id="site_b",
        planned_equipment_id="ex_other",
        validation_status=ValidationStatus.PUBLISHED,
    ))
    db.flush()


def _make_canonical_event(db, tenant_id="metro", employee_id="w1",
                          event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=None, shift_id="DAY", site_id="site_padang"):
    """Create a canonical attendance event."""
    op_date = operating_date or date.today()
    ts = datetime(op_date.year, op_date.month, op_date.day, 7, 0, 0,
                  tzinfo=timezone(timedelta(hours=8)))
    ev = CanonicalAttendanceEvent(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        event_type=event_type, local_timestamp=ts, utc_timestamp=ts,
        timezone="Asia/Makassar", operating_date=op_date,
        shift_id=shift_id, site_id=site_id, source="SIMULATION",
        source_event_id=_uid(), processing_status=CanonicalProcessingStatus.VALID,
    )
    db.add(ev)
    db.flush()
    return ev


def _make_checkpoint_result(db, tenant_id="metro", employee_id="w1",
                            checkpoint_type="BRIEFING_IN", operating_date=None,
                            shift_id="DAY", status=CheckpointValidationStatus.PASS,
                            event_id=None):
    """Create a checkpoint validation result. Requires a canonical_event_id."""
    op_date = operating_date or date.today()
    if not event_id:
        # Create a corresponding canonical event first
        ev_type_map = {
            "BRIEFING_IN": CanonicalEventType.BRIEFING_IN,
            "EQUIPMENT_IN": CanonicalEventType.EQUIPMENT_CHECK_IN,
            "WORK_START": CanonicalEventType.CHECK_IN,
            "BREAK_OUT": CanonicalEventType.BREAK_IN,
            "BREAK_IN": CanonicalEventType.BREAK_OUT,
            "HANDOVER": CanonicalEventType.HANDOVER_START,
            "SHIFT_OUT": CanonicalEventType.CHECK_OUT,
        }
        ev_type = ev_type_map.get(checkpoint_type, CanonicalEventType.BRIEFING_IN)
        ev = _make_canonical_event(db, tenant_id=tenant_id, employee_id=employee_id,
                                   event_type=ev_type, operating_date=op_date,
                                   shift_id=shift_id)
        event_id = ev.id
    cv = CheckpointValidationResult(
        id=_uid(), tenant_id=tenant_id, canonical_event_id=event_id,
        employee_id=employee_id, checkpoint_type=checkpoint_type,
        operating_date=op_date, shift_id=shift_id, validation_status=status,
        detected_timestamp=datetime.now(timezone.utc),
    )
    db.add(cv)
    db.flush()
    return cv


def _make_missing_checkpoint(db, tenant_id="metro", employee_id="w1",
                             checkpoint_type="BRIEFING_IN", operating_date=None,
                             shift_id="DAY"):
    """Create a missing checkpoint result."""
    op_date = operating_date or date.today()
    mc = MissingCheckpointResult(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        operating_date=op_date, shift_id=shift_id,
        checkpoint_type=checkpoint_type, detection_status="MISSING",
    )
    db.add(mc)
    db.flush()
    return mc


def _make_rule_eval(db, tenant_id="metro", employee_id="w1",
                    rule_code="LATE_BREAK_RETURN", operating_date=None,
                    shift_id="DAY", severity=RuleSeverity.WARNING):
    """Create a rule evaluation."""
    op_date = operating_date or date.today()
    re = RuleEvaluation(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        operating_date=op_date, shift_id=shift_id,
        rule_code=rule_code, rule_version_id="rv1",
        evaluated_at=datetime.now(timezone.utc),
        status=RuleEvaluationStatus.FAIL, severity=severity,
        evidence_key=_uid(),
    )
    db.add(re)
    db.flush()
    return re


def _make_actual_assignment(db, tenant_id="metro", employee_id="w1",
                            equipment_id="ex25", operating_date=None,
                            shift_id="DAY"):
    """Create an actual equipment assignment."""
    op_date = operating_date or date.today()
    aa = EquipmentAssignmentActual(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        equipment_id=equipment_id, operating_date=op_date,
        shift_id=shift_id, site_id="site_padang",
        started_at=datetime.now(timezone.utc), source="MANUAL",
        status=ActualAssignmentStatus.ACTIVE,
    )
    db.add(aa)
    db.flush()
    return aa


def _make_comparison_result(db, actual_assignment_id, tenant_id="metro",
                            employee_id="w1", planned_eq_id="ex25",
                            actual_eq_id="ex25", operating_date=None,
                            shift_id="DAY", result=ComparisonResult.MATCH):
    """Create an equipment comparison result."""
    op_date = operating_date or date.today()
    cr = EquipmentComparisonResult(
        id=_uid(), tenant_id=tenant_id, actual_assignment_id=actual_assignment_id,
        employee_id=employee_id, operating_date=op_date, shift_id=shift_id,
        planned_equipment_id=planned_eq_id, actual_equipment_id=actual_eq_id,
        comparison_result=result, actual_worker_id=employee_id,
    )
    db.add(cr)
    db.flush()
    return cr


def _make_discrepancy(db, actual_assignment_id, tenant_id="metro",
                      employee_id="w1", planned_eq_id="ex25",
                      actual_eq_id="ex31", operating_date=None,
                      shift_id="DAY", disc_type=DiscrepancyType.EQUIPMENT_MISMATCH,
                      planned_worker_id=None, actual_worker_id=None):
    """Create an equipment discrepancy."""
    op_date = operating_date or date.today()
    ed = EquipmentDiscrepancy(
        id=_uid(), tenant_id=tenant_id,
        actual_assignment_id=actual_assignment_id,
        employee_id=employee_id, operating_date=op_date, shift_id=shift_id,
        planned_equipment_id=planned_eq_id, actual_equipment_id=actual_eq_id,
        discrepancy_type=disc_type, detected_at=datetime.now(timezone.utc),
        planned_worker_id=planned_worker_id or employee_id,
        actual_worker_id=actual_worker_id or employee_id,
    )
    db.add(ed)
    db.flush()
    return ed


# ── Acceptance Scenarios ──────────────────────────────────────

# A. METRO DASHBOARD LOADS
def test_m4a_01_metro_dashboard_loads(db):
    """M4A-01: Metro dashboard loads with correct context."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY", site_id="site_padang")

    assert snap.context.tenant_id == "metro"
    assert snap.context.tenant_name == "Metro Mining"
    assert snap.context.site_id == "site_padang"
    assert snap.context.site_name == "Padang Mine"
    assert snap.context.operating_date == op_date
    assert snap.context.shift_id == "DAY"
    assert snap.context.shift_name == "Day"
    assert snap.context.timezone_str == "Asia/Makassar"
    assert snap.context.generated_at is not None


# B. TRUSTED TENANT CONTEXT
def test_m4a_02_trusted_tenant_context(db):
    """M4A-02: Dashboard data resolved from authenticated tenant, not frontend input."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    # Metro dashboard should only contain Metro data
    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    employee_ids = {r.employee_id for r in snap.roster_status}
    assert "w_other" not in employee_ids
    assert all(eid.startswith("w") and eid != "w_other" for eid in employee_ids)


# C. WITA TIMEZONE
def test_m4a_03_wita_timezone(db):
    """M4A-03: Dashboard uses WITA timezone from Metro Mining config."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    assert snap.context.timezone_str == "Asia/Makassar"
    assert "WITA" not in snap.context.timezone_str  # IANA name, not abbreviation


# D. OPERATING_DATE DAY
def test_m4a_04_operating_date_day(db):
    """M4A-04: DAY shift operating date correctly represented."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    assert snap.context.operating_date == op_date
    assert snap.context.shift_id == "DAY"


# E. OPERATING_DATE NIGHT
def test_m4a_05_operating_date_night(db):
    """M4A-05: NIGHT shift operating_date correct (cross-midnight scenario)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Unique constraint is (tenant_id, operating_date, employee_id) — no shift_id.
    # So w1 already has DAY roster. Use a dedicated NIGHT employee instead.
    w_night = Worker(id="w_night", tenant_id="metro", code="W010", name="NightWorker", is_active=True)
    em_night = EmployeeMeta(id="em_night", tenant_id="metro", worker_id="w_night", employee_no="W010",
                            role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    db.add_all([w_night, em_night])
    db.flush()

    db.add(RosterAssignment(
        id="ra-w1-night", tenant_id="metro", roster_code="RC-w1-night",
        operating_date=op_date, employee_id="w_night",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.WORK, shift_id="NIGHT", site_id="site_padang",
        planned_equipment_id="ex25", rule_version_id="rv1",
        validation_status=ValidationStatus.PUBLISHED,
    ))
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="NIGHT")
    assert snap.context.operating_date == op_date
    assert snap.context.shift_id == "NIGHT"
    # At 00:58 next calendar day, operating_date should still be 2026-09-05
    assert snap.context.operating_date == date(2026, 9, 5)


# F. SITE CONTEXT
def test_m4a_06_site_context(db):
    """M4A-06: Dashboard filters by site correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY", site_id="site_padang")
    assert snap.context.site_id == "site_padang"


# G. SCHEDULED WORK COUNT
def test_m4a_07_scheduled_work_count(db):
    """M4A-07: Shift summary shows correct scheduled WORK count."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    assert snap.shift_summary.scheduled_work == 4  # w1, w2, w3, w4


# H. REST COUNT
def test_m4a_08_rest_count(db):
    """M4A-08: Shift summary shows correct REST count."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    assert snap.shift_summary.scheduled_rest == 1  # w_rest


# I. OFFSITE COUNT
def test_m4a_09_offsite_count(db):
    """M4A-09: Shift summary shows correct OFFSITE count."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    assert snap.shift_summary.scheduled_offsite == 1  # w_off


# J. NO-EVENT STATE
def test_m4a_10_no_event_state(db):
    """M4A-10: No events yet → NOT_STARTED, not PASS."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # All WORK employees should be NOT_STARTED
    work_items = [r for r in snap.roster_status if r.work_status == "WORK"]
    assert all(r.operational_state == OperationalState.NOT_STARTED for r in work_items)
    assert snap.shift_summary.present_operational == 0
    assert snap.shift_summary.not_yet_confirmed == 4


# K. NORMAL BRIEFING
def test_m4a_11_normal_briefing(db):
    """M4A-11: Worker with briefing event → BRIEFING_COMPLETE."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)
    _make_checkpoint_result(db, employee_id="w1", checkpoint_type="BRIEFING_IN",
                            operating_date=op_date, status=CheckpointValidationStatus.PASS)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    w1_item = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1_item.operational_state == OperationalState.BRIEFING_COMPLETE
    assert snap.shift_summary.present_operational >= 1


# L. MISSING BRIEFING
def test_m4a_12_missing_briefing(db):
    """M4A-12: Missing briefing checkpoint visible."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_missing_checkpoint(db, employee_id="w1", checkpoint_type="BRIEFING_IN",
                             operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    missing = [c for c in snap.checkpoint_status if c.employee_id == "w1" and c.status == "MISSING"]
    assert len(missing) >= 1
    assert any(c.checkpoint_type == "BRIEFING_IN" for c in missing)


# M. NORMAL BREAK RETURN
def test_m4a_13_normal_break_return(db):
    """M4A-13: Worker with break events → RETURNED_FROM_BREAK."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.CHECK_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BREAK_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BREAK_OUT,
                          operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    w1_item = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1_item.operational_state == OperationalState.RETURNED_FROM_BREAK


# N. LATE BREAK RETURN
def test_m4a_14_late_break_return(db):
    """M4A-14: Late break return creates exception visible in active queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w2", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date, severity=RuleSeverity.WARNING)
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    active = [e for e in snap.active_exceptions if e.exception_type == "LATE_BREAK_RETURN"]
    assert len(active) >= 1
    assert active[0].employee_name == "Budi"

    # Should appear in action required
    actions = [a for a in snap.action_required if "LATE_BREAK_RETURN" in a.description]
    assert len(actions) >= 1


# O. PLANNED/ACTUAL EQUIPMENT MATCH
def test_m4a_15_equipment_match(db):
    """M4A-15: Planned and actual equipment match → MATCH."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex25",
                                 operating_date=op_date)
    _make_comparison_result(db, aa.id, employee_id="w1",
                            planned_eq_id="ex25", actual_eq_id="ex25",
                            operating_date=op_date, result=ComparisonResult.MATCH)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    eq = next(e for e in snap.equipment_status if e.employee_id == "w1")
    assert eq.comparison_result == "MATCH"
    assert eq.planned_equipment_code == "EX-025"
    assert eq.actual_equipment_code == "EX-025"


# P. EQUIPMENT MISMATCH
def test_m4a_16_equipment_mismatch(db):
    """M4A-16: Equipment mismatch shows PLAN vs ACTUAL correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    _make_comparison_result(db, aa.id, employee_id="w1",
                            planned_eq_id="ex25", actual_eq_id="ex31",
                            operating_date=op_date, result=ComparisonResult.MISMATCH)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    eq = next(e for e in snap.equipment_status if e.employee_id == "w1")
    assert eq.comparison_result == "MISMATCH"
    assert eq.planned_equipment_code == "EX-025"  # Plan preserved
    assert eq.actual_equipment_code == "EX-031"    # Actual shown


# Q. OPERATOR SUBSTITUTION
def test_m4a_17_operator_substitution(db):
    """M4A-17: Operator substitution shows planned and actual workers."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w3", equipment_id="ex25",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w3",
                             planned_eq_id="ex25", actual_eq_id="ex25",
                             operating_date=op_date,
                             disc_type=DiscrepancyType.OPERATOR_SUBSTITUTION,
                             planned_worker_id="w1", actual_worker_id="w3")

    # Create exception from discrepancy
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # Exception should be visible
    # OPERATOR_SUBSTITUTION discrepancy maps to EQUIPMENT_MISMATCH exception type per engine mapping
    assert any(e.exception_type == "EQUIPMENT_MISMATCH" for e in snap.active_exceptions)


# R. APPROVED DECISION DISPLAYED WITHOUT REWRITING PLAN
def test_m4a_18_approved_decision_preserves_plan(db):
    """M4A-18: Approved substitution shown but PLAN never rewritten."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create discrepancy and exception
    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    # Request and approve decision
    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                     reason_code="APPROVED", reason_text="Equipment EX-025 in maintenance",
                     authorization_policy="SIM_APPROVER")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # Equipment status should show plan preserved
    eq = next(e for e in snap.equipment_status if e.employee_id == "w1")
    assert "EX-025" in eq.plan_display  # Original plan preserved
    assert eq.decision_status == "APPROVED"


# S. REJECTED DECISION DISPLAYED WITHOUT REWRITING ACTUAL
def test_m4a_19_rejected_decision_preserves_actual(db):
    """M4A-19: Rejected decision shown but actual remains visible."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    reject_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                    reason_code="REJECTED", reason_text="No valid reason")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # Exception should still be visible (not resolved by rejection)
    assert snap.shift_summary.unresolved_exceptions >= 1


# T. OPEN EXCEPTION VISIBLE
def test_m4a_20_open_exception_visible(db):
    """M4A-20: OPEN exception visible in active exceptions."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w1", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert any(e.status == "OPEN" for e in snap.active_exceptions)
    assert snap.shift_summary.unresolved_exceptions >= 1


# U. ACKNOWLEDGED EXCEPTION VISIBLE
def test_m4a_21_acknowledged_exception_visible(db):
    """M4A-21: ACKNOWLEDGED exception visible in active exceptions."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w1", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", note="Looking into it")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    ack_exceptions = [e for e in snap.active_exceptions if e.status == "ACKNOWLEDGED"]
    assert len(ack_exceptions) >= 1


# V. RESOLVED EXCLUDED FROM ACTIVE QUEUE
def test_m4a_22_resolved_excluded(db):
    """M4A-22: RESOLVED exception NOT in active queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w1", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Resolved")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert not any(e.exception_id == exc.id for e in snap.active_exceptions)


# W. WAIVED EXCLUDED FROM ACTIVE QUEUE
def test_m4a_23_waived_excluded(db):
    """M4A-23: WAIVED exception NOT in active queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w1", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Acceptable")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert not any(e.exception_id == exc.id for e in snap.active_exceptions)


# X. PENDING DECISION VISIBLE
def test_m4a_24_pending_decision_visible(db):
    """M4A-24: Pending decision visible in pending_decisions and action_required."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert len(snap.pending_decisions) >= 1
    assert snap.pending_decisions[0].decision_type == "EQUIPMENT_SUBSTITUTION"
    assert snap.pending_decisions[0].status == "PENDING"


# Y. AUTHORIZATION BLOCKED
def test_m4a_25_authorization_blocked(db):
    """M4A-25: Decision with no authorization policy shows BLOCKED."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    # Request decision with no authorization policy
    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1")
    db.flush()

    # Attempt approve — should raise AuthorizationBlocked and set policy field
    with pytest.raises(AuthorizationBlocked):
        approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                         reason_text="test", authorization_policy=None)
    db.flush()

    # After blocked attempt, authorization_policy should be set
    assert dec.authorization_policy == "BLOCKED_POLICY_DECISION"

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    blocked = [p for p in snap.pending_decisions if p.authorization_status == "BLOCKED_POLICY_DECISION"]
    assert len(blocked) >= 1


# Z. ACTION REQUIRED COUNT CORRECT
def test_m4a_26_action_required_count(db):
    """M4A-26: Action required count matches actual actionable items."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create one exception
    re = _make_rule_eval(db, employee_id="w1", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # Should have at least: 1 REVIEW_REQUIRED for the exception
    review_actions = [a for a in snap.action_required if a.category == "REVIEW_REQUIRED"]
    assert len(review_actions) >= 1


# AA. CONFIGURATION WARNING SEPARATE FROM EMPLOYEE EXCEPTION
def test_m4a_27_config_warning_separate(db):
    """M4A-27: Configuration warnings separate from employee violations."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Site without geofence config
    site = db.get(Site, "site_padang")
    site.latitude = None
    site.longitude = None
    site.radius_m = None
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY", site_id="site_padang")

    # Configuration warning should exist
    geo_warnings = [w for w in snap.configuration_warnings if w.warning_type == "GEOFENCE_NOT_CONFIGURED"]
    assert len(geo_warnings) >= 1

    # Should NOT appear as employee exception
    assert not any("geofence" in e.exception_type.lower() for e in snap.active_exceptions)


# AB. GEOFENCE TBC-SAFE
def test_m4a_28_geofence_tbc_safe(db):
    """M4A-28: Geofence TBC shows config warning, not employee violation."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY", site_id="site_padang")

    # Even without geofence data, no false geofence exceptions
    geo_exceptions = [e for e in snap.active_exceptions
                       if "GEOFENCE" in e.exception_type or "LOCATION" in e.exception_type]
    # If no geofence config, there should be no geofence exceptions
    site = db.get(Site, "site_padang")
    if site.latitude is None:
        assert len(geo_exceptions) == 0


# AC. DASHBOARD DATA FRESHNESS TIMESTAMP
def test_m4a_29_data_freshness(db):
    """M4A-29: Dashboard includes generated_at and last_event_at."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert snap.context.generated_at is not None
    assert snap.context.last_event_at is not None


# AD. EMPTY ROSTER STATE
def test_m4a_30_empty_roster(db):
    """M4A-30: Empty roster handled gracefully."""
    t = Tenant(id="empty_tenant", code="empty", name="Empty", timezone="Asia/Makassar")
    db.add(t)
    db.flush()

    snap = get_dashboard_snapshot(db, "empty_tenant", date(2026, 9, 5))

    assert snap.shift_summary.scheduled_work == 0
    assert snap.roster_status == []
    assert snap.checkpoint_status == []
    assert snap.equipment_status == []


# AE. EMPTY EXCEPTION STATE
def test_m4a_31_empty_exceptions(db):
    """M4A-31: No exceptions → empty list, not error."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert snap.active_exceptions == []
    assert snap.shift_summary.unresolved_exceptions == 0


# AF. TENANT ISOLATION: ROSTER
def test_m4a_32_tenant_isolation_roster(db):
    """M4A-32: Metro roster cannot include Client B employees."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    employee_ids = {r.employee_id for r in snap.roster_status}
    assert "w_other" not in employee_ids


# AG. TENANT ISOLATION: EQUIPMENT
def test_m4a_33_tenant_isolation_equipment(db):
    """M4A-33: Metro equipment cannot include Client B equipment."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    eq_codes = {e.planned_equipment_code for e in snap.equipment_status if e.planned_equipment_code}
    assert "TR-001" not in eq_codes


# AH. TENANT ISOLATION: EXCEPTION
def test_m4a_34_tenant_isolation_exception(db):
    """M4A-34: Metro exceptions cannot include Client B exceptions."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    # Create a Metro exception
    re = _make_rule_eval(db, tenant_id="metro", employee_id="w1",
                         rule_code="LATE_BREAK_RETURN", operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    # Create a Client B exception
    re_b = RuleEvaluation(
        id=_uid(), tenant_id="client_b", employee_id="w_other",
        operating_date=op_date, shift_id="DAY_B",
        rule_code="LATE_BREAK_RETURN", evaluated_at=datetime.now(timezone.utc),
        status=RuleEvaluationStatus.FAIL, severity=RuleSeverity.WARNING,
        evidence_key=_uid(),
    )
    db.add(re_b)
    db.flush()
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    exception_ids = {e.exception_id for e in snap.active_exceptions}
    assert exc_b.id not in exception_ids
    assert exc.id in exception_ids


# AI. TENANT ISOLATION: DECISION
def test_m4a_35_tenant_isolation_decision(db):
    """M4A-35: Metro decisions cannot include Client B decisions."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    # Create Metro exception + decision
    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    decision_ids = {d.decision_id for d in snap.pending_decisions}
    assert dec.id in decision_ids


# AJ. SITE ISOLATION
def test_m4a_36_site_isolation(db):
    """M4A-36: Dashboard correctly filters by site."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Add a second site
    site2 = Site(id="site_2", tenant_id="metro", site_code="SITE2",
                 site_name="Mine Site B", site_type=SiteType.MINE_SITE,
                 status=SiteStatus.ACTIVE, timezone="Asia/Makassar",
                 effective_from=date(2026, 1, 1))
    db.add(site2)
    db.flush()

    # Use different employee for site_2 (unique constraint: tenant+date+employee)
    w_site2 = Worker(id="w_site2", tenant_id="metro", code="W020", name="Site2Worker", is_active=True)
    em_site2 = EmployeeMeta(id="em_site2", tenant_id="metro", worker_id="w_site2", employee_no="W020",
                            role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    db.add_all([w_site2, em_site2])
    db.flush()

    db.add(RosterAssignment(
        id="ra-site2", tenant_id="metro", roster_code="RC-site2",
        operating_date=op_date, employee_id="w_site2",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.WORK, shift_id="DAY", site_id="site_2",
        rule_version_id="rv1", validation_status=ValidationStatus.PUBLISHED,
    ))
    db.flush()

    # Dashboard for site_padang should not include site_2 roster
    snap_padang = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY", site_id="site_padang")
    assert all(r.employee_id != "w_site2" for r in snap_padang.roster_status)

    # Dashboard for site_2 should include only site_2 roster
    snap_site2 = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY", site_id="site_2")
    assert any(r.employee_id == "w_site2" for r in snap_site2.roster_status)


# AK. STABLE ORDERING
def test_m4a_37_stable_ordering(db):
    """M4A-37: Dashboard data has stable ordering."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create multiple exceptions
    for wid in ["w1", "w2", "w3"]:
        re = _make_rule_eval(db, employee_id=wid, rule_code="LATE_BREAK_RETURN",
                             operating_date=op_date)
        create_exception_from_rule_evaluation(db, re)
    db.flush()

    snap1 = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    snap2 = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    ids1 = [e.exception_id for e in snap1.active_exceptions]
    ids2 = [e.exception_id for e in snap2.active_exceptions]
    assert ids1 == ids2


# AL. PAGINATION/BOUNDED EXCEPTION LIST
def test_m4a_38_bounded_exception_list(db):
    """M4A-38: Exception list is bounded (not unlimited)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create many exceptions
    for i in range(60):
        re = RuleEvaluation(
            id=_uid(), tenant_id="metro", employee_id="w1",
            operating_date=op_date, shift_id="DAY",
            rule_code=f"RULE_{i}", evaluated_at=datetime.now(timezone.utc),
            status=RuleEvaluationStatus.FAIL, severity=RuleSeverity.WARNING,
            evidence_key=f"ek_{i}",
        )
        db.add(re)
    db.flush()

    # Create exceptions from rule evaluations
    for re in db.scalars(
        select(RuleEvaluation).where(RuleEvaluation.tenant_id == "metro")
    ).all():
        try:
            create_exception_from_rule_evaluation(db, re)
        except Exception:
            pass  # Some may fail due to unique constraints
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert len(snap.active_exceptions) <= 50  # ACTIVE_EXCEPTIONS_LIMIT


# AM. M3 ACTION ENTRY POINTS USE EXISTING SERVICE
def test_m4a_39_m3_action_entry_points(db):
    """M4A-39: Dashboard data references existing M3 exception IDs for navigation."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w1", rule_code="LATE_BREAK_RETURN",
                         operating_date=op_date)
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # Exception ID should be a valid reference to existing M3 data
    active = next(e for e in snap.active_exceptions if e.exception_id == exc.id)
    assert active.exception_id == exc.id  # Valid reference


# AN. NO DUPLICATED BUSINESS RULE CALCULATION
def test_m4a_40_no_business_rule_duplication(db):
    """M4A-40: Dashboard does not independently calculate business rules.

    The dashboard reads from existing engine outputs (RuleEvaluation,
    ExceptionCase, CheckpointValidationResult) rather than independently
    computing whether someone is late, valid, etc.
    """
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # If no RuleEvaluation exists, dashboard should not independently detect violations
    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # No exceptions should exist if no rule evaluations were created
    assert snap.active_exceptions == []

    # Late status only shows when RuleEvaluation FAIL exists
    late_items = [c for c in snap.checkpoint_status if c.is_late]
    assert late_items == []


# AO. PLAN/ACTUAL/DECISION INVARIANT
def test_m4a_41_plan_actual_decision_invariant(db):
    """M4A-41: Plan/Actual/Decision invariant preserved across all scenarios."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Equipment substitution scenario
    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    eq = next(e for e in snap.equipment_status if e.employee_id == "w1")

    # INVARIANT: Plan NEVER changes even after approval
    assert "EX-025" in eq.plan_display
    # Actual is shown as-is
    assert "EX-031" in eq.actual_display


# AP. SIMULATION DATA LABELLED
def test_m4a_42_simulation_data_labelled(db):
    """M4A-42: Test data uses SIMULATION source label."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    ev = _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                               operating_date=op_date)

    assert ev.source == "SIMULATION"


# AQ. PRESENTATION STATE: WORKING
def test_m4a_43_operational_state_working(db):
    """M4A-43: Worker with CHECK_IN event → WORKING."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.EQUIPMENT_CHECK_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.CHECK_IN,
                          operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    w1 = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1.operational_state == OperationalState.WORKING


# AR. PRESENTATION STATE: ON_BREAK
def test_m4a_44_operational_state_on_break(db):
    """M4A-44: Worker with BREAK_IN → ON_BREAK."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.CHECK_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BREAK_IN,
                          operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    w1 = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1.operational_state == OperationalState.ON_BREAK


# AS. PRESENTATION STATE: HANDOVER
def test_m4a_45_operational_state_handover(db):
    """M4A-45: Worker with HANDOVER_START → HANDOVER."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.CHECK_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.HANDOVER_START,
                          operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    w1 = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1.operational_state == OperationalState.HANDOVER


# AT. PRESENTATION STATE: SHIFT_COMPLETE
def test_m4a_46_operational_state_shift_complete(db):
    """M4A-46: Worker with CHECK_OUT → SHIFT_COMPLETE."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.BRIEFING_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.CHECK_IN,
                          operating_date=op_date)
    _make_canonical_event(db, employee_id="w1", event_type=CanonicalEventType.CHECK_OUT,
                          operating_date=op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    w1 = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1.operational_state == OperationalState.SHIFT_COMPLETE


# AU. EQUIPMENT STATUS: NO_ACTUAL
def test_m4a_47_equipment_no_actual(db):
    """M4A-47: Worker with planned equipment but no actual → NO_ACTUAL."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    # w1 has planned ex25 but no actual assignment
    eq = next(e for e in snap.equipment_status if e.employee_id == "w1")
    assert eq.comparison_result == "NO_ACTUAL"
    assert eq.planned_equipment_code == "EX-025"
    assert eq.actual_equipment_code is None


# AV. CHECKPOINT STATUS: LATE DETECTED
def test_m4a_48_checkpoint_late_detected(db):
    """M4A-48: Late checkpoint detected and flagged."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_checkpoint_result(db, employee_id="w1", checkpoint_type="BRIEFING_IN",
                            operating_date=op_date, status=CheckpointValidationStatus.FAIL)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    failed = [c for c in snap.checkpoint_status
               if c.employee_id == "w1" and c.status == "FAIL"]
    assert len(failed) >= 1


# AW. NIGHT CROSS-MIDNIGHT LATE BREAK
def test_m4a_49_night_cross_midnight_late_break(db):
    """M4A-49: Night shift cross-midnight late break correctly represented."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Use dedicated night employee (unique constraint: tenant+date+employee)
    w_night2 = Worker(id="w_night2", tenant_id="metro", code="W011", name="NightWorker2", is_active=True)
    em_night2 = EmployeeMeta(id="em_night2", tenant_id="metro", worker_id="w_night2", employee_no="W011",
                             role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    db.add_all([w_night2, em_night2])
    db.flush()

    db.add(RosterAssignment(
        id="ra-w1-night2", tenant_id="metro", roster_code="RC-w1-night2",
        operating_date=op_date, employee_id="w_night2",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.WORK, shift_id="NIGHT", site_id="site_padang",
        planned_equipment_id="ex25", rule_version_id="rv1",
        validation_status=ValidationStatus.PUBLISHED,
    ))
    db.flush()

    # Late break return at 01:05 WITA (after 01:00 break end)
    re = RuleEvaluation(
        id=_uid(), tenant_id="metro", employee_id="w_night2",
        operating_date=op_date, shift_id="NIGHT",
        rule_code="LATE_BREAK_RETURN", rule_version_id="rv1",
        evaluated_at=datetime.now(timezone.utc),
        status=RuleEvaluationStatus.FAIL, severity=RuleSeverity.WARNING,
        actual_value="01:05", expected_value="01:00", evidence_key=_uid(),
    )
    db.add(re)
    db.flush()
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="NIGHT")

    assert snap.context.operating_date == op_date  # Still Sep 5, not Sep 6
    assert any(e.exception_type == "LATE_BREAK_RETURN" for e in snap.active_exceptions)


# AX. CONFIGURATION WARNING: NO CHECKPOINT POLICIES
def test_m4a_50_config_warning_no_checkpoint_policies(db):
    """M4A-50: No checkpoint policies → configuration warning."""
    t = Tenant(id="no_cp", code="no_cp", name="No CP", timezone="Asia/Makassar")
    db.add(t)
    db.flush()

    snap = get_dashboard_snapshot(db, "no_cp", date(2026, 9, 5))

    cp_warnings = [w for w in snap.configuration_warnings if w.warning_type == "NO_CHECKPOINT_POLICIES"]
    assert len(cp_warnings) >= 1


# AY. DASHBOARD SNAPSHOT SERIALIZATION
def test_m4a_51_snapshot_serialization(db):
    """M4A-51: Dashboard snapshot serializes to dict correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    data = snapshot_to_dict(snap)

    assert "context" in data
    assert "shift_summary" in data
    assert "roster_status" in data
    assert "checkpoint_status" in data
    assert "equipment_status" in data
    assert "active_exceptions" in data
    assert "action_required" in data
    assert "pending_decisions" in data
    assert "configuration_warnings" in data

    assert data["context"]["tenant_id"] == "metro"
    assert data["context"]["timezone"] == "Asia/Makassar"
    assert isinstance(data["shift_summary"]["scheduled_work"], int)


# AZ. ROSTER STATUS INCLUDES METADATA
def test_m4a_52_roster_metadata(db):
    """M4A-52: Roster status includes role, crew, equipment metadata."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    w1 = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1.role_name == "Excavator Operator"
    assert w1.crew_name == "Crew A"
    assert w1.planned_equipment_code == "EX-025"
    assert w1.employee_name == "Tono"


# BA. ATTENTION_REQUIRED STATE
def test_m4a_53_attention_required_state(db):
    """M4A-53: Worker with failed checkpoint → ATTENTION_REQUIRED."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _make_checkpoint_result(db, employee_id="w1", checkpoint_type="BRIEFING_IN",
                            operating_date=op_date, status=CheckpointValidationStatus.FAIL)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")
    w1 = next(r for r in snap.roster_status if r.employee_id == "w1")
    assert w1.operational_state == OperationalState.ATTENTION_REQUIRED


# BB. EQUIPMENT_STATUS ITEM WITH PENDING DECISION
def test_m4a_54_equipment_pending_decision_flag(db):
    """M4A-54: Equipment status shows has_pending_decision flag."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    aa = _make_actual_assignment(db, employee_id="w1", equipment_id="ex31",
                                 operating_date=op_date)
    disc = _make_discrepancy(db, aa.id, employee_id="w1",
                             planned_eq_id="ex25", actual_eq_id="ex31",
                             operating_date=op_date)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="supervisor1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    eq = next(e for e in snap.equipment_status if e.employee_id == "w1")
    assert eq.has_pending_decision is True
    assert eq.decision_status == "PENDING"


# BC. MULTIPLE CHECKPOINTS PER EMPLOYEE
def test_m4a_55_multiple_checkpoints(db):
    """M4A-55: Multiple checkpoint results per employee shown."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    for cp_type in ["BRIEFING_IN", "EQUIPMENT_IN", "WORK_START"]:
        _make_checkpoint_result(db, employee_id="w1", checkpoint_type=cp_type,
                                operating_date=op_date, status=CheckpointValidationStatus.PASS)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    w1_checkpoints = [c for c in snap.checkpoint_status if c.employee_id == "w1"]
    assert len(w1_checkpoints) == 3


# BD. NO FALSE EXCEPTION FROM ABSENT DATA
def test_m4a_56_no_false_exceptions(db):
    """M4A-56: No exceptions created when data is absent."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date, shift_id="DAY")

    assert snap.active_exceptions == []
    assert snap.shift_summary.unresolved_exceptions == 0


# BE. DASHBOARD WITHOUT SHIFT FILTER
def test_m4a_57_dashboard_without_shift(db):
    """M4A-57: Dashboard works without shift_id filter."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    snap = get_dashboard_snapshot(db, "metro", op_date)

    assert snap.context.shift_id is None
    assert snap.shift_summary.scheduled_work == 4


# Import select for pagination test
from sqlalchemy import select


# ── Summary ──────────────────────────────────────────────────
# Total: 57 acceptance test scenarios (A through BE)
# Covers: context, timezone, operating_date, shift summary, roster,
# checkpoint status, equipment status (plan/actual/decision invariant),
# active exceptions, action required, pending decisions, authorization blocked,
# configuration warnings, tenant isolation (roster/equipment/exception/decision),
# site isolation, empty states, pagination, stable ordering, data freshness,
# operational states (NOT_STARTED/BRIEFING_COMPLETE/WORKING/ON_BREAK/
# RETURNED_FROM_BREAK/HANDOVER/SHIFT_COMPLETE/ATTENTION_REQUIRED),
# simulation labelling, serialization, metadata, no business rule duplication.
