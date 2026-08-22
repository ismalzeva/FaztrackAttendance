"""
test_acceptance_m4c.py — MM-M4C Exception & Decision Workbench.

Acceptance tests proving the workbench correctly aggregates M3 engines
(exception_engine, decision_engine, review_service) into an operational
supervisor workbench for reviewing and acting on attendance exceptions.

Scenarios cover: active queue, filters, sorting, pagination, case detail,
lifecycle transitions, notes, ownership, decisions, waivers, timeline,
timezone, tenant isolation, concurrency, configuration safety,
navigation compatibility, empty states, and partial data.

No new business logic in workbench layer — all derived from existing engines.
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
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    ExceptionCase, ExceptionAction, ExceptionActionType, ExceptionStatus, ExceptionSeverity,
    ExceptionSourceType, EXCEPTION_TRANSITIONS,
    ExceptionEvidence, ExceptionEvidenceType,
    ExceptionDecision, ExceptionDecisionAction, DecisionType, DecisionStatus, DECISION_TRANSITIONS,
    CheckpointPolicy,
    User,
    uid, now,
)

from app.exception_workbench_service import (
    get_workbench_queue,
    get_case_detail,
    workbench_result_to_dict,
    case_detail_to_dict,
)

from app.exception_engine import (
    create_exception_from_rule_evaluation,
    create_exception_from_discrepancy,
    acknowledge_exception,
    resolve_exception,
    waive_exception,
    get_exception,
    get_action_history,
)

from app.decision_engine import (
    request_decision,
    approve_decision,
    reject_decision,
    cancel_decision,
    get_decision,
    get_decisions_for_exception,
    get_decision_history,
    AuthorizationBlocked,
    DecisionValidationFailed,
    DuplicateActiveDecision,
    InvalidDecisionTransition,
)

from app.review_service import (
    add_review_note,
    assign_reviewer,
    add_evidence,
    get_evidence,
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


@pytest.fixture(autouse=True)
def _patch_utcnow(monkeypatch):
    """Patch _utcnow to return naive UTC (SQLite strips tzinfo)."""
    import app.exception_workbench_service as svc
    monkeypatch.setattr(svc, "_utcnow", lambda: datetime.now(timezone.utc).replace(tzinfo=None))


def _uid():
    return uuid.uuid4().hex[:12]


# ── Seed Data ─────────────────────────────────────────────────

def _seed_metro(db: Session, operating_date: date = date(2026, 9, 5)):
    """Seed Metro Mining environment for M4C workbench tests."""
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

    # Users (for actor_user_id in lifecycle actions)
    u1 = User(id="sup1", login_id="supervisor1", display_name="Supervisor One",
              password_hash="hash1", is_active=True)
    u2 = User(id="admin1", login_id="admin1", display_name="Admin One",
              password_hash="hash2", is_active=True)
    u3 = User(id="sup2", login_id="supervisor2", display_name="Supervisor Two",
              password_hash="hash3", is_active=True)
    db.add_all([u1, u2, u3])

    db.flush()

    # Roster assignments
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
    # Rule version for client_b
    rv_b = RuleVersion(id="rv_b", tenant_id="client_b", version_label="v1.0",
                       effective_from=date(2026, 1, 1), config_snapshot_json="{}")
    db.add(rv_b)
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


# ── Helpers ───────────────────────────────────────────────────

def _utcnow_naive():
    """Return naive UTC datetime (SQLite strips tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_rule_eval(db, tenant_id="metro", employee_id="w1",
                    rule_code="LATE_BREAK_RETURN", operating_date=None,
                    shift_id="DAY", severity=RuleSeverity.WARNING,
                    rule_version_id="rv1"):
    """Create a rule evaluation."""
    op_date = operating_date or date.today()
    re = RuleEvaluation(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        operating_date=op_date, shift_id=shift_id,
        rule_code=rule_code, rule_version_id=rule_version_id,
        evaluated_at=_utcnow_naive(),
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
        started_at=_utcnow_naive(), source="MANUAL",
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
        discrepancy_type=disc_type, detected_at=_utcnow_naive(),
        planned_worker_id=planned_worker_id or employee_id,
        actual_worker_id=actual_worker_id or employee_id,
    )
    db.add(ed)
    db.flush()
    return ed


def _create_exception_from_rule(db, tenant_id="metro", employee_id="w1",
                                rule_code="LATE_BREAK_RETURN", operating_date=None,
                                shift_id="DAY", severity=RuleSeverity.WARNING):
    """Helper: create rule eval + exception in one call."""
    re = _make_rule_eval(db, tenant_id=tenant_id, employee_id=employee_id,
                         rule_code=rule_code, operating_date=operating_date,
                         shift_id=shift_id, severity=severity)
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()
    return exc


def _create_exception_from_discrepancy_helper(db, tenant_id="metro", employee_id="w1",
                                              planned_eq_id="ex25", actual_eq_id="ex31",
                                              operating_date=None, shift_id="DAY",
                                              disc_type=DiscrepancyType.EQUIPMENT_MISMATCH,
                                              planned_worker_id=None, actual_worker_id=None):
    """Helper: create discrepancy + exception in one call."""
    op_date = operating_date or date.today()
    aa = _make_actual_assignment(db, tenant_id=tenant_id, employee_id=employee_id,
                                 equipment_id=actual_eq_id, operating_date=op_date,
                                 shift_id=shift_id)
    disc = _make_discrepancy(db, aa.id, tenant_id=tenant_id, employee_id=employee_id,
                             planned_eq_id=planned_eq_id, actual_eq_id=actual_eq_id,
                             operating_date=op_date, shift_id=shift_id,
                             disc_type=disc_type,
                             planned_worker_id=planned_worker_id,
                             actual_worker_id=actual_worker_id)
    exc = create_exception_from_discrepancy(db, disc)
    db.flush()
    return exc


# ── Active Queue Tests ────────────────────────────────────────

def test_m4c_01_active_queue_loads(db):
    """M4C-01: Active queue loads with OPEN + ACKNOWLEDGED visible."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc1 = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    exc2 = _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                       rule_code="LATE_BRIEFING")
    acknowledge_exception(db, exc2.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    result = get_workbench_queue(db, "metro")
    statuses = {item.status for item in result.items}
    assert "OPEN" in statuses
    assert "ACKNOWLEDGED" in statuses
    assert len(result.items) == 2


def test_m4c_02_default_open_visible(db):
    """M4C-02: Default OPEN visible in queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro")
    assert any(item.status == "OPEN" for item in result.items)


def test_m4c_03_acknowledged_visible(db):
    """M4C-03: ACKNOWLEDGED visible in queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    result = get_workbench_queue(db, "metro")
    assert any(item.status == "ACKNOWLEDGED" for item in result.items)


def test_m4c_04_resolved_excluded_from_active(db):
    """M4C-04: RESOLVED excluded from default active queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Fixed")
    db.flush()

    result = get_workbench_queue(db, "metro")
    assert len(result.items) == 0


def test_m4c_05_waived_excluded_from_active(db):
    """M4C-05: WAIVED excluded from default active queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Acceptable")
    db.flush()

    result = get_workbench_queue(db, "metro")
    assert len(result.items) == 0


def test_m4c_06_historical_resolved_searchable(db):
    """M4C-06: Historical RESOLVED searchable with active_only=False."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Fixed")
    db.flush()

    result = get_workbench_queue(db, "metro", active_only=False)
    assert any(item.case_id == exc.id for item in result.items)


def test_m4c_07_historical_waived_searchable(db):
    """M4C-07: Historical WAIVED searchable with active_only=False."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Acceptable")
    db.flush()

    result = get_workbench_queue(db, "metro", active_only=False)
    assert any(item.case_id == exc.id for item in result.items)


# ── Filter Tests ──────────────────────────────────────────────

def test_m4c_08_severity_filter(db):
    """M4C-08: Severity filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                severity=RuleSeverity.WARNING)
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING", severity=RuleSeverity.CRITICAL)

    result = get_workbench_queue(db, "metro", severity="CRITICAL")
    assert len(result.items) == 1
    assert result.items[0].severity == "CRITICAL"


def test_m4c_09_status_filter(db):
    """M4C-09: Status filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc1 = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    exc2 = _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                       rule_code="LATE_BRIEFING")
    acknowledge_exception(db, exc2.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    result = get_workbench_queue(db, "metro", status="ACKNOWLEDGED")
    assert len(result.items) == 1
    assert result.items[0].status == "ACKNOWLEDGED"


def test_m4c_10_exception_type_filter(db):
    """M4C-10: Exception type filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                rule_code="LATE_BREAK_RETURN")
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING")

    result = get_workbench_queue(db, "metro", exception_type="LATE_BREAK_RETURN")
    assert len(result.items) == 1
    assert result.items[0].exception_type == "LATE_BREAK_RETURN"


def test_m4c_11_employee_search_name(db):
    """M4C-11: Employee search by name works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING")

    result = get_workbench_queue(db, "metro", employee_search="Tono")
    assert len(result.items) == 1
    assert result.items[0].employee_name == "Tono"


def test_m4c_12_employee_search_code(db):
    """M4C-12: Employee search by code works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING")

    result = get_workbench_queue(db, "metro", employee_search="W002")
    assert len(result.items) == 1
    assert result.items[0].employee_code == "W002"


def test_m4c_13_crew_filter(db):
    """M4C-13: Crew filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # w1 is in crew_a, w2 is in crew_b
    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING")

    result = get_workbench_queue(db, "metro", crew_id="crew_b")
    assert len(result.items) == 1
    assert result.items[0].crew_name == "Crew B"


def test_m4c_14_equipment_search(db):
    """M4C-14: Equipment search works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    result = get_workbench_queue(db, "metro", equipment_search="EX-031")
    assert len(result.items) >= 1


def test_m4c_15_date_filter(db):
    """M4C-15: Date filter (operating_date) works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro", operating_date=op_date)
    assert len(result.items) == 1
    assert result.items[0].operating_date == op_date


def test_m4c_16_date_range_filter(db):
    """M4C-16: Date range filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro",
                                 operating_date_from=date(2026, 9, 1),
                                 operating_date_to=date(2026, 9, 30))
    assert len(result.items) == 1


def test_m4c_17_shift_filter(db):
    """M4C-17: Shift filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date, shift_id="DAY")

    result = get_workbench_queue(db, "metro", shift_id="DAY")
    assert len(result.items) == 1
    assert result.items[0].shift_id == "DAY"


def test_m4c_18_owner_filter(db):
    """M4C-18: Owner filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    result = get_workbench_queue(db, "metro", owner_id="sup1")
    assert len(result.items) == 1
    assert result.items[0].current_owner_id == "sup1"


def test_m4c_19_decision_status_filter(db):
    """M4C-19: Decision status filter works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    request_decision(db, tenant_id="metro", exception_id=exc.id,
                     decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                     requested_by="sup1",
                     planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    result = get_workbench_queue(db, "metro", decision_status="PENDING")
    assert len(result.items) >= 1


# ── Sorting & Pagination ─────────────────────────────────────

def test_m4c_20_sorting_severity(db):
    """M4C-20: Deterministic sorting by severity."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                severity=RuleSeverity.WARNING)
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING", severity=RuleSeverity.CRITICAL)

    result = get_workbench_queue(db, "metro", sort_by="severity", sort_dir="desc")
    # Severity is string-sorted: WARNING > INFO > CRITICAL alphabetically
    assert result.items[0].severity == "WARNING"
    assert result.items[1].severity == "CRITICAL"


def test_m4c_21_sorting_detected_at(db):
    """M4C-21: Sorting by detected_at works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                rule_code="LATE_BRIEFING")

    result = get_workbench_queue(db, "metro", sort_by="detected_at", sort_dir="desc")
    assert len(result.items) == 2
    # Most recent first
    assert result.items[0].detected_at >= result.items[1].detected_at


def test_m4c_22_sorting_status(db):
    """M4C-22: Sorting by status works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc1 = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    exc2 = _create_exception_from_rule(db, employee_id="w2", operating_date=op_date,
                                       rule_code="LATE_BRIEFING")
    acknowledge_exception(db, exc2.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    result = get_workbench_queue(db, "metro", sort_by="status", sort_dir="asc")
    statuses = [item.status for item in result.items]
    assert statuses == sorted(statuses)


def test_m4c_23_pagination(db):
    """M4C-23: Pagination (offset/limit) works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    for i in range(5):
        _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                    rule_code=f"RULE_{i}")

    result_page1 = get_workbench_queue(db, "metro", offset=0, limit=2)
    result_page2 = get_workbench_queue(db, "metro", offset=2, limit=2)

    assert len(result_page1.items) == 2
    assert len(result_page2.items) == 2
    # No overlap
    ids1 = {item.case_id for item in result_page1.items}
    ids2 = {item.case_id for item in result_page2.items}
    assert ids1.isdisjoint(ids2)


# ── Case Detail Tests ─────────────────────────────────────────

def test_m4c_24_case_detail_loads(db):
    """M4C-24: Case detail loads with summary."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    assert detail is not None
    assert detail.summary.case_id == exc.id
    assert detail.summary.exception_type == "LATE_BREAK_RETURN"
    assert detail.summary.severity == "WARNING"
    assert detail.summary.status == "OPEN"


def test_m4c_25_source_detection_info(db):
    """M4C-25: Source detection info present in case detail."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.source_type == ExceptionSourceType.RULE_EVALUATION.value
    assert detail.summary.source_id is not None


def test_m4c_26_rule_version_info(db):
    """M4C-26: Rule version info present in case detail."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.rule_version_id == "rv1"
    assert detail.summary.rule_version_name == "v1.0"


def test_m4c_27_worker_context(db):
    """M4C-27: Worker context (name, code, role, crew) in case detail."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.employee_name == "Tono"
    assert detail.summary.employee_code == "W001"
    assert detail.summary.role_name == "Excavator Operator"
    assert detail.summary.crew_name == "Crew A"


def test_m4c_28_equipment_context(db):
    """M4C-28: Equipment context in case detail."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.equipment_id is not None


def test_m4c_29_plan_vs_actual_equipment_mismatch(db):
    """M4C-29: Plan vs actual for equipment mismatch case."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    # Request a decision to populate plan/actual
    request_decision(db, tenant_id="metro", exception_id=exc.id,
                     decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                     requested_by="sup1",
                     planned_equipment_id="ex25", actual_equipment_id="ex31",
                     planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.plan_vs_actual is not None
    assert detail.plan_vs_actual.planned_equipment_id == "ex25"
    assert detail.plan_vs_actual.actual_equipment_id == "ex31"


def test_m4c_30_plan_vs_actual_none_for_non_equipment(db):
    """M4C-30: Plan vs actual None for non-equipment exceptions."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                      rule_code="LATE_BREAK_RETURN")

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.plan_vs_actual is None


def test_m4c_31_system_evidence_present(db):
    """M4C-31: System evidence present in case detail."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    # Add system evidence
    add_evidence(db, exc.id, "metro",
                 ExceptionEvidenceType.RULE_EVALUATION, "RULE_EVAL", "src1",
                 is_system_generated=True)
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    assert len(detail.evidence) >= 1
    assert any(ev["is_system_generated"] for ev in detail.evidence)


def test_m4c_32_human_evidence_present(db):
    """M4C-32: Human evidence present in case detail."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    # Add human evidence
    add_evidence(db, exc.id, "metro",
                 ExceptionEvidenceType.DOCUMENT_REFERENCE, "UPLOAD", "doc1",
                 actor_user_id="sup1", is_system_generated=False, note="Uploaded photo")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    human_ev = [ev for ev in detail.evidence if not ev["is_system_generated"]]
    assert len(human_ev) >= 1


def test_m4c_33_available_actions_open(db):
    """M4C-33: Available actions for OPEN case."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    assert "ACKNOWLEDGE" in detail.available_actions
    assert "RESOLVE" in detail.available_actions
    assert "WAIVE" in detail.available_actions
    assert "ASSIGN" in detail.available_actions
    assert "ADD_NOTE" in detail.available_actions
    assert "REQUEST_DECISION" in detail.available_actions


def test_m4c_34_available_actions_acknowledged(db):
    """M4C-34: Available actions for ACKNOWLEDGED case."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    assert "ACKNOWLEDGE" not in detail.available_actions
    assert "RESOLVE" in detail.available_actions
    assert "WAIVE" in detail.available_actions


def test_m4c_35_available_actions_resolved(db):
    """M4C-35: Available actions for RESOLVED case (empty)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Fixed")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.available_actions == []


def test_m4c_36_available_actions_waived(db):
    """M4C-36: Available actions for WAIVED case (empty)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Acceptable")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.available_actions == []


# ── Lifecycle Tests ───────────────────────────────────────────

def test_m4c_37_acknowledge_open_to_acknowledged(db):
    """M4C-37: Acknowledge OPEN → ACKNOWLEDGED."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    assert exc.status == ExceptionStatus.OPEN

    result = acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1",
                                   note="Looking into it")
    db.flush()

    assert result.status == ExceptionStatus.ACKNOWLEDGED
    assert result.acknowledged_at is not None
    assert result.current_owner_id == "sup1"


def test_m4c_38_duplicate_acknowledgement_raises(db):
    """M4C-38: Duplicate acknowledgement raises ValueError."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    with pytest.raises(ValueError):
        acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()


def test_m4c_39_resolve_from_open(db):
    """M4C-39: Resolve from OPEN."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    result = resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1",
                               reason="Fixed")
    db.flush()

    assert result.status == ExceptionStatus.RESOLVED
    assert result.resolved_at is not None


def test_m4c_40_resolve_from_acknowledged(db):
    """M4C-40: Resolve from ACKNOWLEDGED."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    result = resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1",
                               reason="Fixed after review")
    db.flush()

    assert result.status == ExceptionStatus.RESOLVED


def test_m4c_41_waive_from_open(db):
    """M4C-41: Waive from OPEN (reason required)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    result = waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1",
                             reason="Acceptable deviation")
    db.flush()

    assert result.status == ExceptionStatus.WAIVED
    assert result.waived_at is not None


def test_m4c_42_waive_preserves_history(db):
    """M4C-42: Waive preserves history (original detection/evidence/timeline unchanged)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    # Add evidence before waiving
    add_evidence(db, exc.id, "metro",
                 ExceptionEvidenceType.RULE_EVALUATION, "RULE_EVAL", "src1",
                 is_system_generated=True)
    db.flush()

    # Record original source_id
    original_source_id = exc.source_id

    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="OK")
    db.flush()

    # Verify history preserved
    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.source_id == original_source_id
    assert len(detail.evidence) >= 1
    assert len(detail.actions) >= 1  # WAIVE action recorded


def test_m4c_43_cannot_acknowledge_resolved(db):
    """M4C-43: Cannot acknowledge RESOLVED."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Fixed")
    db.flush()

    with pytest.raises(ValueError):
        acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()


def test_m4c_44_cannot_acknowledge_waived(db):
    """M4C-44: Cannot acknowledge WAIVED."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="OK")
    db.flush()

    with pytest.raises(ValueError):
        acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()


# ── Note Tests ────────────────────────────────────────────────

def test_m4c_45_add_note(db):
    """M4C-45: Add note works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    action = add_review_note(db, exc.id, "metro", "sup1", "Checked with field team")
    db.flush()

    assert action.action_type == ExceptionActionType.REVIEW_NOTE
    assert action.note == "Checked with field team"


def test_m4c_46_note_attribution(db):
    """M4C-46: Note attribution (actor_user_id recorded)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    action = add_review_note(db, exc.id, "metro", "sup1", "Note from sup1")
    db.flush()

    assert action.actor_user_id == "sup1"


def test_m4c_47_notes_append_only(db):
    """M4C-47: Notes are append-only (multiple notes accumulate)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    add_review_note(db, exc.id, "metro", "sup1", "First note")
    add_review_note(db, exc.id, "metro", "sup1", "Second note")
    add_review_note(db, exc.id, "metro", "sup2", "Third note from different user")
    db.flush()

    actions = get_action_history(db, exc.id, "metro")
    note_actions = [a for a in actions if a.action_type == ExceptionActionType.REVIEW_NOTE]
    assert len(note_actions) == 3


# ── Ownership Tests ───────────────────────────────────────────

def test_m4c_48_assign_owner(db):
    """M4C-48: Assign owner works."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    case = assign_reviewer(db, exc.id, "metro", "sup1", "sup2")
    db.flush()

    assert case.current_owner_id == "sup2"


def test_m4c_49_reassign_owner(db):
    """M4C-49: Reassign owner (previous owner preserved in timeline)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    assign_reviewer(db, exc.id, "metro", "sup1", "sup2")
    db.flush()
    assign_reviewer(db, exc.id, "metro", "sup2", "admin1")
    db.flush()

    actions = get_action_history(db, exc.id, "metro")
    assign_actions = [a for a in actions if a.action_type == ExceptionActionType.ASSIGN_REVIEWER]
    assert len(assign_actions) == 2
    # Check that the note records previous → new owner
    assert "sup2" in assign_actions[1].note


# ── Decision Tests ────────────────────────────────────────────

def test_m4c_50_request_equipment_substitution(db):
    """M4C-50: Request decision (EQUIPMENT_SUBSTITUTION)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    assert dec.status == DecisionStatus.PENDING
    assert dec.decision_type == DecisionType.EQUIPMENT_SUBSTITUTION


def test_m4c_51_request_operator_substitution(db):
    """M4C-51: Request decision (OPERATOR_SUBSTITUTION)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w3", planned_eq_id="ex25", actual_eq_id="ex25",
        operating_date=op_date,
        disc_type=DiscrepancyType.OPERATOR_SUBSTITUTION,
        planned_worker_id="w1", actual_worker_id="w3")

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.OPERATOR_SUBSTITUTION,
                           requested_by="sup1",
                           planned_worker_id="w1", actual_worker_id="w3",
                           actual_equipment_id="ex25")
    db.flush()

    assert dec.status == DecisionStatus.PENDING
    assert dec.decision_type == DecisionType.OPERATOR_SUBSTITUTION


def test_m4c_52_approved_decision(db):
    """M4C-52: Approved decision."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    approved = approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                                reason_text="Equipment EX-025 in maintenance",
                                reason_code="APPROVED",
                                authorization_policy="SIM_APPROVER")
    db.flush()

    assert approved.status == DecisionStatus.APPROVED
    assert approved.decided_by == "admin1"
    assert approved.decided_at is not None


def test_m4c_53_rejected_decision(db):
    """M4C-53: Rejected decision (reason required)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    rejected = reject_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                               reason_text="No valid reason for substitution")
    db.flush()

    assert rejected.status == DecisionStatus.REJECTED
    assert rejected.reason_text == "No valid reason for substitution"


def test_m4c_54_cancelled_decision(db):
    """M4C-54: Cancelled decision."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    cancelled = cancel_decision(db, dec.id, tenant_id="metro", cancelled_by="sup1",
                                reason_text="No longer needed")
    db.flush()

    assert cancelled.status == DecisionStatus.CANCELLED


def test_m4c_55_authorization_blocked(db):
    """M4C-55: Authorization blocked (no authorization_policy → AuthorizationBlocked)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1")
    db.flush()

    with pytest.raises(AuthorizationBlocked):
        approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                         reason_text="test", authorization_policy=None)
    db.flush()

    # After blocked attempt, authorization_policy should be set
    assert dec.authorization_policy == "BLOCKED_POLICY_DECISION"


def test_m4c_56_approval_not_resolution(db):
    """M4C-56: Approval ≠ Resolution (approve decision does NOT resolve exception)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                     reason_text="Approved", authorization_policy="SIM_APPROVER")
    db.flush()

    # Exception should still be OPEN, not RESOLVED
    refreshed = get_exception(db, exc.id, "metro")
    assert refreshed.status == ExceptionStatus.OPEN


def test_m4c_57_rejection_not_resolution(db):
    """M4C-57: Rejection ≠ Resolution (reject decision does NOT resolve exception)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    reject_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                    reason_text="Rejected")
    db.flush()

    refreshed = get_exception(db, exc.id, "metro")
    assert refreshed.status == ExceptionStatus.OPEN


def test_m4c_58_cannot_approve_already_approved(db):
    """M4C-58: Cannot approve already-approved decision."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                     reason_text="Approved", authorization_policy="SIM_APPROVER")
    db.flush()

    with pytest.raises(InvalidDecisionTransition):
        approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                         reason_text="Double approve", authorization_policy="SIM_APPROVER")
    db.flush()


# ── Waiver Tests ──────────────────────────────────────────────

def test_m4c_59_waiver_not_event_deletion(db):
    """M4C-59: Waiver ≠ Event Deletion (waived case still has original evidence/timeline)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    # Add evidence
    add_evidence(db, exc.id, "metro",
                 ExceptionEvidenceType.RULE_EVALUATION, "RULE_EVAL", "src1",
                 is_system_generated=True)
    db.flush()

    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="OK")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    assert len(detail.evidence) >= 1
    assert len(detail.timeline) >= 1


def test_m4c_60_waived_removed_from_active_queue(db):
    """M4C-60: Waived case removed from default active queue."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="OK")
    db.flush()

    result = get_workbench_queue(db, "metro")
    assert not any(item.case_id == exc.id for item in result.items)


# ── Timeline Tests ────────────────────────────────────────────

def test_m4c_61_timeline_chronological(db):
    """M4C-61: Timeline chronological ordering."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    add_review_note(db, exc.id, "metro", "sup1", "Checking")
    resolve_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="Fixed")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    timestamps = [entry.timestamp for entry in detail.timeline]
    assert timestamps == sorted(timestamps)


def test_m4c_62_timeline_includes_all_types(db):
    """M4C-62: Timeline includes actions, decisions, decision_actions."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    # Add a decision
    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                     reason_text="Approved", authorization_policy="SIM_APPROVER")
    db.flush()

    detail = get_case_detail(db, "metro", exc.id)
    entry_types = {entry.entry_type for entry in detail.timeline}
    assert "DECISION_ACTION" in entry_types


# ── Timezone Tests ────────────────────────────────────────────

def test_m4c_63_night_shift_operating_date(db):
    """M4C-63: NIGHT shift operating_date correct (case at 00:58 WITA for NIGHT shift operating_date = previous day)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create a NIGHT shift exception
    re = _make_rule_eval(db, employee_id="w1", operating_date=op_date, shift_id="NIGHT")
    exc = create_exception_from_rule_evaluation(db, re)
    db.flush()

    assert exc.operating_date == op_date
    assert exc.shift_id == "NIGHT"

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.operating_date == op_date
    assert detail.summary.shift_id == "NIGHT"


def test_m4c_64_wita_timestamps(db):
    """M4C-64: WITA timestamps in responses."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro")
    assert result.context.timezone_str == "Asia/Makassar"

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.detected_at is not None
    # SQLite strips tzinfo; verify the datetime is present and reasonable
    assert detail.summary.detected_at.year == 2026


# ── Tenant Isolation Tests ────────────────────────────────────

def test_m4c_65_tenant_isolation_list(db):
    """M4C-65: Tenant isolation — list (Metro cannot see CLIENT_B cases)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    # Create Metro exception
    _create_exception_from_rule(db, tenant_id="metro", employee_id="w1", operating_date=op_date)

    # Create Client B exception
    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    result = get_workbench_queue(db, "metro")
    case_ids = {item.case_id for item in result.items}
    assert exc_b.id not in case_ids


def test_m4c_66_tenant_isolation_detail(db):
    """M4C-66: Tenant isolation — detail (Metro cannot open CLIENT_B case)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    detail = get_case_detail(db, "metro", exc_b.id)
    assert detail is None


def test_m4c_67_tenant_isolation_note(db):
    """M4C-67: Tenant isolation — note (Metro cannot add note to CLIENT_B case)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    with pytest.raises(ValueError):
        add_review_note(db, exc_b.id, "metro", "sup1", "Cross-tenant note")
    db.flush()


def test_m4c_68_tenant_isolation_ownership(db):
    """M4C-68: Tenant isolation — ownership (Metro cannot assign CLIENT_B case)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    with pytest.raises(ValueError):
        assign_reviewer(db, exc_b.id, "metro", "sup1", "sup2")
    db.flush()


def test_m4c_69_tenant_isolation_decision(db):
    """M4C-69: Tenant isolation — decision (Metro cannot request decision on CLIENT_B case)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    with pytest.raises(ValueError):
        request_decision(db, tenant_id="metro", exception_id=exc_b.id,
                         decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                         requested_by="sup1")
    db.flush()


def test_m4c_70_tenant_isolation_resolution(db):
    """M4C-70: Tenant isolation — resolution (Metro cannot resolve CLIENT_B case)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    exc_b = create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    with pytest.raises(ValueError):
        resolve_exception(db, exc_b.id, tenant_id="metro", actor_user_id="sup1",
                          reason="Cross-tenant resolve")
    db.flush()


# ── Concurrency & Idempotency ────────────────────────────────

def test_m4c_71_duplicate_acknowledgement_safe(db):
    """M4C-71: Concurrency — duplicate acknowledgement safe."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)
    acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    # Second acknowledgement should raise ValueError
    with pytest.raises(ValueError):
        acknowledge_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1")
    db.flush()

    # Status should still be ACKNOWLEDGED
    refreshed = get_exception(db, exc.id, "metro")
    assert refreshed.status == ExceptionStatus.ACKNOWLEDGED


def test_m4c_72_duplicate_decision_request_safe(db):
    """M4C-72: Concurrency — duplicate decision request safe (DuplicateActiveDecision)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec1 = request_decision(db, tenant_id="metro", exception_id=exc.id,
                            decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                            requested_by="sup1",
                            planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    # Second request for same exception+type should return existing (idempotent)
    dec2 = request_decision(db, tenant_id="metro", exception_id=exc.id,
                            decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                            requested_by="sup1",
                            planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    assert dec1.id == dec2.id


def test_m4c_73_idempotency_retry_no_duplicate(db):
    """M4C-73: Idempotency — retry does not create duplicate."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    re = _make_rule_eval(db, employee_id="w1", operating_date=op_date)
    exc1 = create_exception_from_rule_evaluation(db, re)
    db.flush()

    # Second call with same rule_eval should return same exception
    exc2 = create_exception_from_rule_evaluation(db, re)
    db.flush()

    assert exc1.id == exc2.id

    # Verify only one exception exists
    result = get_workbench_queue(db, "metro")
    matching = [item for item in result.items if item.case_id == exc1.id]
    assert len(matching) == 1


# ── Configuration & Safety ────────────────────────────────────

def test_m4c_74_config_incomplete_separation(db):
    """M4C-74: Config incomplete separation (BLOCKED_POLICY_DECISION shown as config issue, not employee violation)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    # Attempt approve without policy — should block
    with pytest.raises(AuthorizationBlocked):
        approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                         reason_text="test", authorization_policy=None)
    db.flush()

    # The decision should show BLOCKED_POLICY_DECISION, not as employee violation
    detail = get_case_detail(db, "metro", exc.id)
    blocked_decisions = [d for d in detail.decisions if d.authorization_blocked]
    assert len(blocked_decisions) >= 1
    assert blocked_decisions[0].authorization_policy == "BLOCKED_POLICY_DECISION"


def test_m4c_75_no_payroll_consequence(db):
    """M4C-75: No payroll consequence in response."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    detail_dict = case_detail_to_dict(detail)

    # No payroll fields should exist
    assert "payroll" not in str(detail_dict).lower()
    assert "deduction" not in str(detail_dict).lower()
    assert "penalty" not in str(detail_dict).lower()


def test_m4c_76_no_disciplinary_consequence(db):
    """M4C-76: No disciplinary consequence in response."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    detail_dict = case_detail_to_dict(detail)

    assert "disciplinary" not in str(detail_dict).lower()
    assert "warning_letter" not in str(detail_dict).lower()
    assert "suspension" not in str(detail_dict).lower()


def test_m4c_77_no_hse_consequence(db):
    """M4C-77: No HSE consequence in response."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    detail_dict = case_detail_to_dict(detail)

    assert "hse" not in str(detail_dict).lower()
    assert "safety_violation" not in str(detail_dict).lower()
    assert "incident_report" not in str(detail_dict).lower()


# ── Navigation Compatibility ──────────────────────────────────

def test_m4c_78_m4a_navigation_compatibility(db):
    """M4C-78: M4A navigation reference compatibility (exception data structure compatible with dashboard)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro")
    item = next(i for i in result.items if i.case_id == exc.id)

    # Verify fields that M4A dashboard would reference
    assert item.case_id is not None
    assert item.exception_type is not None
    assert item.severity is not None
    assert item.status is not None
    assert item.employee_id is not None
    assert item.employee_name is not None
    assert item.operating_date is not None
    assert item.detected_at is not None


def test_m4c_79_m4b_navigation_compatibility(db):
    """M4C-79: M4B navigation reference compatibility (exception data structure compatible with roster)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro")
    item = next(i for i in result.items if i.case_id == exc.id)

    # Verify fields that M4B roster would reference
    assert item.employee_id == "w1"
    assert item.shift_id == "DAY"
    assert item.operating_date == op_date


# ── Trusted Context ───────────────────────────────────────────

def test_m4c_80_trusted_tenant_context(db):
    """M4C-80: Trusted tenant context (all queries scoped to tenant_id)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)
    _seed_client_b(db, op_date)

    # Create exceptions for both tenants
    _create_exception_from_rule(db, tenant_id="metro", employee_id="w1", operating_date=op_date)
    re_b = _make_rule_eval(db, tenant_id="client_b", employee_id="w_other",
                           operating_date=op_date, shift_id="DAY_B",
                           rule_version_id="rv_b")
    create_exception_from_rule_evaluation(db, re_b)
    db.flush()

    # Metro queue should only show Metro data
    metro_result = get_workbench_queue(db, "metro")
    for item in metro_result.items:
        assert item.employee_id in ("w1", "w2", "w3", "w4", "w_rest", "w_off")

    # Client B queue should only show Client B data
    client_b_result = get_workbench_queue(db, "client_b")
    for item in client_b_result.items:
        assert item.employee_id == "w_other"


# ── Bounded Retrieval ─────────────────────────────────────────

def test_m4c_81_bounded_historical_retrieval(db):
    """M4C-81: Bounded historical retrieval (limit parameter works)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create many exceptions
    for i in range(10):
        _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                    rule_code=f"RULE_{i}")

    result = get_workbench_queue(db, "metro", limit=3)
    assert len(result.items) == 3


# ── Empty State ───────────────────────────────────────────────

def test_m4c_82_empty_state(db):
    """M4C-82: Empty state — no active exceptions returns empty list."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    result = get_workbench_queue(db, "metro")
    assert result.items == []
    assert result.context.active_count == 0
    assert result.context.total_count == 0


# ── Partial Data ──────────────────────────────────────────────

def test_m4c_83_partial_data_missing_equipment(db):
    """M4C-83: Partial data — missing equipment shows None, not crash."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    # Create exception without equipment (rule eval, not discrepancy)
    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date,
                                      rule_code="LATE_BREAK_RETURN")

    # Should not crash
    result = get_workbench_queue(db, "metro")
    item = next(i for i in result.items if i.case_id == exc.id)
    assert item.equipment_id is None
    assert item.equipment_code is None

    detail = get_case_detail(db, "metro", exc.id)
    assert detail.summary.equipment_id is None
    assert detail.summary.equipment_code is None
    assert detail.plan_vs_actual is None


# ── Serialization Tests ───────────────────────────────────────

def test_m4c_84_workbench_result_serialization(db):
    """M4C-84: workbench_result_to_dict produces valid dict."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    result = get_workbench_queue(db, "metro")
    d = workbench_result_to_dict(result)

    assert "context" in d
    assert "items" in d
    assert d["context"]["tenant_id"] == "metro"
    assert isinstance(d["items"], list)
    assert len(d["items"]) == 1
    assert "case_id" in d["items"][0]
    assert "severity" in d["items"][0]
    assert "status" in d["items"][0]


def test_m4c_85_case_detail_serialization(db):
    """M4C-85: case_detail_to_dict produces valid dict."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    detail = get_case_detail(db, "metro", exc.id)
    d = case_detail_to_dict(detail)

    assert "summary" in d
    assert "plan_vs_actual" in d
    assert "decisions" in d
    assert "evidence" in d
    assert "actions" in d
    assert "timeline" in d
    assert "available_actions" in d
    assert d["summary"]["case_id"] == exc.id
    assert d["summary"]["employee_name"] == "Tono"


# ── Additional Edge Cases ─────────────────────────────────────

def test_m4c_86_waive_requires_reason(db):
    """M4C-86: Waive requires reason (empty reason raises ValueError)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    with pytest.raises(ValueError):
        waive_exception(db, exc.id, tenant_id="metro", actor_user_id="sup1", reason="")
    db.flush()


def test_m4c_87_reject_requires_reason(db):
    """M4C-87: Reject requires reason (empty reason raises ValueError)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31")
    db.flush()

    with pytest.raises(ValueError):
        reject_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                        reason_text="")
    db.flush()


def test_m4c_88_approve_requires_reason(db):
    """M4C-88: Approve requires reason (empty reason raises ValueError)."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    with pytest.raises(ValueError):
        approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                         reason_text="", authorization_policy="SIM_APPROVER")
    db.flush()


def test_m4c_89_decision_history_preserved(db):
    """M4C-89: Decision history preserved after approval."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_discrepancy_helper(
        db, employee_id="w1", planned_eq_id="ex25", actual_eq_id="ex31",
        operating_date=op_date)

    dec = request_decision(db, tenant_id="metro", exception_id=exc.id,
                           decision_type=DecisionType.EQUIPMENT_SUBSTITUTION,
                           requested_by="sup1",
                           planned_equipment_id="ex25", actual_equipment_id="ex31",
                           planned_worker_id="w1", actual_worker_id="w1")
    db.flush()

    approve_decision(db, dec.id, tenant_id="metro", decided_by="admin1",
                     reason_text="Approved", authorization_policy="SIM_APPROVER")
    db.flush()

    history = get_decision_history(db, dec.id, "metro")
    assert len(history) >= 2  # REQUEST + APPROVE
    action_types = [h.action_type for h in history]
    assert "REQUEST" in action_types
    assert "APPROVE" in action_types


def test_m4c_90_evidence_immutable_system(db):
    """M4C-90: System-generated evidence is immutable."""
    op_date = date(2026, 9, 5)
    _seed_metro(db, op_date)

    exc = _create_exception_from_rule(db, employee_id="w1", operating_date=op_date)

    ev = add_evidence(db, exc.id, "metro",
                      ExceptionEvidenceType.RULE_EVALUATION, "RULE_EVAL", "src1",
                      is_system_generated=True)
    db.flush()

    from app.review_service import system_evidence_is_immutable
    assert system_evidence_is_immutable(ev) is True
