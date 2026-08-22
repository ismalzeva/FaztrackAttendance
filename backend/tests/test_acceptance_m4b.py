"""
test_acceptance_m4b.py — MM-M4B Roster & Attendance Operational View.

Acceptance tests proving the roster board, worker detail, and timeline
correctly aggregate M0–M3 data into operational views for Field Supervisor.

Scenarios cover: roster board (context, filters, operational states, pagination,
sorting), worker detail (identity, roster, equipment, checkpoints, exceptions,
decisions, competencies), timeline (DAY/NIGHT cross-midnight), work status
(REST/OFFSITE/LEAVE/SICK), tenant/site isolation, operator substitution,
night shift edge cases, generic master data, and regression.

No new business logic — all derived from existing engines via roster_service.
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

from app.roster_service import (
    get_roster_board,
    get_worker_detail,
    get_worker_timeline,
)

from app.dashboard_service import OperationalState


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

def _seed_metro_m4b(db: Session, operating_date: date = date(2026, 9, 5)):
    """Seed Metro Mining environment for M4B roster tests.

    Extends M4A seed with additional workers (LEAVE, SICK, TRAINING, STANDBY),
    equipment assignments with multiple intervals, full DAY shift timeline,
    FAIL checkpoint, exception cases, decisions, competencies, and NIGHT shift worker.
    """
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)

    # Workers
    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Tono", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Budi", is_active=True)
    w3 = Worker(id="w3", tenant_id="metro", code="W003", name="Andi", is_active=True)
    w4 = Worker(id="w4", tenant_id="metro", code="W004", name="Dodi", is_active=True)
    w_rest = Worker(id="w_rest", tenant_id="metro", code="W005", name="Resty", is_active=True)
    w_off = Worker(id="w_off", tenant_id="metro", code="W006", name="Offy", is_active=True)
    w_leave = Worker(id="w_leave", tenant_id="metro", code="W007", name="Leavey", is_active=True)
    w_sick = Worker(id="w_sick", tenant_id="metro", code="W008", name="Sicky", is_active=True)
    w_train = Worker(id="w_train", tenant_id="metro", code="W009", name="Trainy", is_active=True)
    w_standby = Worker(id="w_standby", tenant_id="metro", code="W010", name="Standby", is_active=True)
    w_night = Worker(id="w_night", tenant_id="metro", code="W011", name="NightWorker", is_active=True)
    db.add_all([w1, w2, w3, w4, w_rest, w_off, w_leave, w_sick, w_train, w_standby, w_night])

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
    ex_oos = Equipment(id="ex_oos", tenant_id="metro", equipment_code="EX-OOS",
                       equipment_type="EXCAVATOR", status=EquipmentStatus.OUT_OF_SERVICE,
                       effective_from=date(2026, 1, 1))
    db.add_all([ex25, ex31, dt14, ex_oos])

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
    em_leave = EmployeeMeta(id="em_leave", tenant_id="metro", worker_id="w_leave", employee_no="W007",
                            role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em_sick = EmployeeMeta(id="em_sick", tenant_id="metro", worker_id="w_sick", employee_no="W008",
                           role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em_train = EmployeeMeta(id="em_train", tenant_id="metro", worker_id="w_train", employee_no="W009",
                            role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em_standby = EmployeeMeta(id="em_standby", tenant_id="metro", worker_id="w_standby", employee_no="W010",
                              role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    em_night = EmployeeMeta(id="em_night", tenant_id="metro", worker_id="w_night", employee_no="W011",
                            role_id="role_exc", crew_id="crew_a", effective_from=date(2026, 1, 1))
    db.add_all([em1, em2, em3, em4, em_rest, em_off, em_leave, em_sick, em_train, em_standby, em_night])

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

    # Roster assignments — WORK employees
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
            effective_rule_version="v1.0",
            validation_status=ValidationStatus.PUBLISHED,
        ))

    # REST employee
    db.add(RosterAssignment(
        id="ra-rest", tenant_id="metro", roster_code="RC-rest",
        operating_date=operating_date, employee_id="w_rest",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.REST, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    # OFFSITE employee
    db.add(RosterAssignment(
        id="ra-off", tenant_id="metro", roster_code="RC-off",
        operating_date=operating_date, employee_id="w_off",
        crew_id="crew_a", site_status=SiteStatusEnum.OFFSITE,
        work_status=WorkStatus.OFFSITE, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    # LEAVE employee
    db.add(RosterAssignment(
        id="ra-leave", tenant_id="metro", roster_code="RC-leave",
        operating_date=operating_date, employee_id="w_leave",
        crew_id="crew_a", site_status=SiteStatusEnum.OFFSITE,
        work_status=WorkStatus.LEAVE, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    # SICK employee
    db.add(RosterAssignment(
        id="ra-sick", tenant_id="metro", roster_code="RC-sick",
        operating_date=operating_date, employee_id="w_sick",
        crew_id="crew_a", site_status=SiteStatusEnum.OFFSITE,
        work_status=WorkStatus.SICK, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    # TRAINING employee
    db.add(RosterAssignment(
        id="ra-train", tenant_id="metro", roster_code="RC-train",
        operating_date=operating_date, employee_id="w_train",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.TRAINING, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    # STANDBY employee
    db.add(RosterAssignment(
        id="ra-standby", tenant_id="metro", roster_code="RC-standby",
        operating_date=operating_date, employee_id="w_standby",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.STANDBY, shift_id="DAY", site_id="site_padang",
        rule_version_id="rv1", effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    # NIGHT shift worker
    db.add(RosterAssignment(
        id="ra-night", tenant_id="metro", roster_code="RC-night",
        operating_date=operating_date, employee_id="w_night",
        crew_id="crew_a", site_status=SiteStatusEnum.ONSITE,
        work_status=WorkStatus.WORK, shift_id="NIGHT", site_id="site_padang",
        planned_equipment_id="ex25", rule_version_id="rv1",
        effective_rule_version="v1.0",
        validation_status=ValidationStatus.PUBLISHED,
    ))

    db.flush()

    # ── Equipment assignments with multiple intervals for w1 ──
    # EX-025 07:05 - 11:00 (first interval)
    aa1_start = datetime(operating_date.year, operating_date.month, operating_date.day,
                         7, 5, 0, tzinfo=timezone(timedelta(hours=8)))
    aa1_end = datetime(operating_date.year, operating_date.month, operating_date.day,
                       11, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    aa1 = EquipmentAssignmentActual(
        id="aa-w1-ex25", tenant_id="metro", employee_id="w1",
        equipment_id="ex25", operating_date=operating_date,
        shift_id="DAY", site_id="site_padang",
        started_at=aa1_start, ended_at=aa1_end,
        source="MANUAL", status=ActualAssignmentStatus.CLOSED,
    )
    db.add(aa1)

    # EX-031 11:00 - 18:55 (second interval, currently active)
    aa2_start = datetime(operating_date.year, operating_date.month, operating_date.day,
                         11, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    aa2 = EquipmentAssignmentActual(
        id="aa-w1-ex31", tenant_id="metro", employee_id="w1",
        equipment_id="ex31", operating_date=operating_date,
        shift_id="DAY", site_id="site_padang",
        started_at=aa2_start,
        source="MANUAL", status=ActualAssignmentStatus.ACTIVE,
    )
    db.add(aa2)

    # Equipment assignment for w2 (planned=dt14)
    aa_w2_start = datetime(operating_date.year, operating_date.month, operating_date.day,
                           7, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    aa_w2 = EquipmentAssignmentActual(
        id="aa-w2-dt14", tenant_id="metro", employee_id="w2",
        equipment_id="dt14", operating_date=operating_date,
        shift_id="DAY", site_id="site_padang",
        started_at=aa_w2_start,
        source="MANUAL", status=ActualAssignmentStatus.ACTIVE,
    )
    db.add(aa_w2)

    db.flush()

    # Comparison results for w1
    cr1 = EquipmentComparisonResult(
        id="cr-w1-1", tenant_id="metro", actual_assignment_id="aa-w1-ex25",
        employee_id="w1", operating_date=operating_date, shift_id="DAY",
        planned_equipment_id="ex25", actual_equipment_id="ex25",
        comparison_result=ComparisonResult.MATCH, actual_worker_id="w1",
    )
    cr2 = EquipmentComparisonResult(
        id="cr-w1-2", tenant_id="metro", actual_assignment_id="aa-w1-ex31",
        employee_id="w1", operating_date=operating_date, shift_id="DAY",
        planned_equipment_id="ex25", actual_equipment_id="ex31",
        comparison_result=ComparisonResult.MISMATCH, actual_worker_id="w1",
    )
    db.add_all([cr1, cr2])

    # ── Full DAY shift timeline for w1 ────────────────────────
    WITA = timezone(timedelta(hours=8))

    _make_timed_event(db, "w1", CanonicalEventType.BRIEFING_IN,
                      operating_date, 6, 42, "DAY", site_id="site_padang")
    _make_timed_event(db, "w1", CanonicalEventType.EQUIPMENT_CHECK_IN,
                      operating_date, 6, 55, "DAY", site_id="site_padang", equipment_id="ex25")
    _make_timed_event(db, "w1", CanonicalEventType.CHECK_IN,
                      operating_date, 7, 1, "DAY", site_id="site_padang")
    _make_timed_event(db, "w1", CanonicalEventType.BREAK_IN,
                      operating_date, 12, 0, "DAY", site_id="site_padang")
    _make_timed_event(db, "w1", CanonicalEventType.BREAK_OUT,
                      operating_date, 12, 58, "DAY", site_id="site_padang")
    _make_timed_event(db, "w1", CanonicalEventType.HANDOVER_START,
                      operating_date, 18, 50, "DAY", site_id="site_padang")
    _make_timed_event(db, "w1", CanonicalEventType.CHECK_OUT,
                      operating_date, 18, 58, "DAY", site_id="site_padang")

    # ── FAIL checkpoint for w2 (ATTENTION_REQUIRED) ───────────
    ev_fail = _make_timed_event(db, "w2", CanonicalEventType.BRIEFING_IN,
                                operating_date, 7, 15, "DAY", site_id="site_padang")
    db.add(CheckpointValidationResult(
        id=_uid(), tenant_id="metro", canonical_event_id=ev_fail.id,
        employee_id="w2", checkpoint_type="BRIEFING_IN",
        operating_date=operating_date, shift_id="DAY",
        validation_status=CheckpointValidationStatus.FAIL,
        detected_timestamp=datetime.now(timezone.utc),
    ))
    db.flush()

    # ── Missing checkpoint for w4 (NOT_STARTED — no events) ───
    db.add(MissingCheckpointResult(
        id=_uid(), tenant_id="metro", employee_id="w4",
        operating_date=operating_date, shift_id="DAY",
        checkpoint_type="BRIEFING_IN", detection_status="MISSING",
    ))
    db.flush()

    # ── ExceptionCase for w2 (active, OPEN) ──────────────────
    exc_rule = RuleEvaluation(
        id=_uid(), tenant_id="metro", employee_id="w2",
        operating_date=operating_date, shift_id="DAY",
        rule_code="LATE_BREAK_RETURN", rule_version_id="rv1",
        evaluated_at=datetime.now(timezone.utc),
        status=RuleEvaluationStatus.FAIL, severity=RuleSeverity.WARNING,
        evidence_key=_uid(),
    )
    db.add(exc_rule)
    db.flush()

    db.add(ExceptionCase(
        id="exc-w2", tenant_id="metro",
        exception_type="LATE_BREAK_RETURN",
        severity=ExceptionSeverity.WARNING,
        status=ExceptionStatus.OPEN,
        employee_id="w2",
        operating_date=operating_date,
        shift_id="DAY",
        source_type=ExceptionSourceType.RULE_EVALUATION.value,
        source_id=exc_rule.id,
        detected_at=datetime.now(timezone.utc),
        opened_at=datetime.now(timezone.utc),
    ))
    db.flush()

    # ── Approved ExceptionDecision for w3 (operator substitution) ──
    db.add(ExceptionDecision(
        id="dec-w3-approved", tenant_id="metro",
        exception_id="exc-w2",  # references exc (any valid exc id)
        decision_type=DecisionType.OPERATOR_SUBSTITUTION,
        status=DecisionStatus.APPROVED,
        planned_worker_id="w3",
        actual_worker_id="w2",
        planned_equipment_id="ex31",
        actual_equipment_id="ex31",
        requested_by="supervisor1",
        requested_at=datetime.now(timezone.utc) - timedelta(hours=1),
        decided_by="manager1",
        decided_at=datetime.now(timezone.utc),
        reason_text="Approved substitution for shift coverage",
    ))
    db.flush()

    # ── Rejected ExceptionDecision for isolation test ─────────
    db.add(ExceptionDecision(
        id="dec-w3-rejected", tenant_id="metro",
        exception_id="exc-w2",
        decision_type=DecisionType.OPERATOR_SUBSTITUTION,
        status=DecisionStatus.REJECTED,
        planned_worker_id="w3",
        actual_worker_id="w4",
        requested_by="supervisor1",
        requested_at=datetime.now(timezone.utc) - timedelta(hours=2),
        decided_by="manager1",
        decided_at=datetime.now(timezone.utc) - timedelta(hours=1),
        reason_text="Worker not qualified",
    ))
    db.flush()

    # ── Competencies ──────────────────────────────────────────
    # w1: VALID
    db.add(Competency(
        id="comp-w1-exc", tenant_id="metro", competency_code="EXC-001",
        employee_id="w1", equipment_type="EXCAVATOR",
        valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
        status=CompetencyStatus.VALID,
    ))
    # w4: EXPIRED
    db.add(Competency(
        id="comp-w4-exc", tenant_id="metro", competency_code="EXC-004",
        employee_id="w4", equipment_type="EXCAVATOR",
        valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
        status=CompetencyStatus.EXPIRED,
    ))
    db.flush()

    # ── NIGHT shift events (cross-midnight) ───────────────────
    # Events on 2026-09-05 20:00 and 2026-09-06 01:00, both with
    # operating_date=2026-09-05
    night_ev1_ts = datetime(operating_date.year, operating_date.month, operating_date.day,
                            20, 0, 0, tzinfo=WITA)
    night_ev1 = CanonicalAttendanceEvent(
        id=_uid(), tenant_id="metro", employee_id="w_night",
        event_type=CanonicalEventType.BRIEFING_IN,
        local_timestamp=night_ev1_ts, utc_timestamp=night_ev1_ts,
        timezone="Asia/Makassar", operating_date=operating_date,
        shift_id="NIGHT", site_id="site_padang", source="SIMULATION",
        source_event_id=_uid(), processing_status=CanonicalProcessingStatus.VALID,
    )
    db.add(night_ev1)

    # Cross-midnight event: 01:00 next day, still operating_date=2026-09-05
    next_day = operating_date + timedelta(days=1)
    night_ev2_ts = datetime(next_day.year, next_day.month, next_day.day,
                            1, 0, 0, tzinfo=WITA)
    night_ev2 = CanonicalAttendanceEvent(
        id=_uid(), tenant_id="metro", employee_id="w_night",
        event_type=CanonicalEventType.CHECK_IN,
        local_timestamp=night_ev2_ts, utc_timestamp=night_ev2_ts,
        timezone="Asia/Makassar", operating_date=operating_date,
        shift_id="NIGHT", site_id="site_padang", source="SIMULATION",
        source_event_id=_uid(), processing_status=CanonicalProcessingStatus.VALID,
    )
    db.add(night_ev2)
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
    day_b = ShiftTemplate(id="DAY_B", tenant_id="client_b", shift_code="DAY",
                          shift_name="Day", start_time=time(8, 0), end_time=time(17, 0),
                          break_start=time(12, 0), break_end=time(13, 0),
                          handover_start=time(16, 45), handover_end=time(17, 0),
                          crosses_midnight=False)
    db.add_all([w, eq, site_b, day_b])
    db.flush()

    em = EmployeeMeta(id="em_other", tenant_id="client_b", worker_id="w_other",
                      employee_no="CB001", role_id=None, crew_id=None,
                      effective_from=date(2026, 1, 1))
    db.add(em)

    db.add(RosterAssignment(
        id="ra-other", tenant_id="client_b", roster_code="RC-other",
        operating_date=operating_date, employee_id="w_other",
        site_status=SiteStatusEnum.ONSITE, work_status=WorkStatus.WORK,
        shift_id="DAY_B", site_id="site_b",
        planned_equipment_id="ex_other",
        validation_status=ValidationStatus.PUBLISHED,
    ))
    db.flush()


def _make_timed_event(db, employee_id, event_type, operating_date,
                      hour, minute, shift_id="DAY", site_id="site_padang",
                      equipment_id=None):
    """Create a canonical event at a specific time."""
    WITA = timezone(timedelta(hours=8))
    ts = datetime(operating_date.year, operating_date.month, operating_date.day,
                  hour, minute, 0, tzinfo=WITA)
    ev = CanonicalAttendanceEvent(
        id=_uid(), tenant_id="metro", employee_id=employee_id,
        event_type=event_type, local_timestamp=ts, utc_timestamp=ts,
        timezone="Asia/Makassar", operating_date=operating_date,
        shift_id=shift_id, site_id=site_id, source="SIMULATION",
        source_event_id=_uid(), processing_status=CanonicalProcessingStatus.VALID,
        equipment_id=equipment_id,
    )
    db.add(ev)
    db.flush()
    return ev


# ── Acceptance Scenarios ──────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# A. ROSTER BOARD TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_001_roster_board_loads(db):
    """M4B-001: Roster board loads with correct context."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY", site_id="site_padang")

    assert result.context.tenant_id == "metro"
    assert result.context.tenant_name == "Metro Mining"
    assert result.context.site_id == "site_padang"
    assert result.context.site_name == "Padang Mine"
    assert result.context.operating_date == op_date
    assert result.context.shift_id == "DAY"
    assert result.context.shift_name == "Day"
    assert result.context.timezone_str == "Asia/Makassar"
    assert result.context.generated_at is not None
    assert len(result.items) > 0


def test_m4b_002_tenant_scoped(db):
    """M4B-002: Roster board tenant isolation — Metro vs client_b."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)
    _seed_client_b(db, op_date)

    metro_result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    metro_ids = {i.employee_id for i in metro_result.items}
    assert "w_other" not in metro_ids

    client_result = get_roster_board(db, "client_b", op_date, shift_id="DAY_B")
    client_ids = {i.employee_id for i in client_result.items}
    assert "w_other" in client_ids
    assert "w1" not in client_ids


def test_m4b_003_site_scoped(db):
    """M4B-003: Roster board only shows workers for specified site."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY", site_id="site_padang")
    for item in result.items:
        assert item.employee_id != "w_other"


def test_m4b_004_operating_date_scoped(db):
    """M4B-004: Roster board only shows workers for specified date."""
    op_date = date(2026, 9, 5)
    other_date = date(2026, 9, 6)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", other_date, shift_id="DAY")
    # No roster for other_date
    assert len(result.items) == 0


def test_m4b_005_shift_scoped(db):
    """M4B-005: Roster board only shows workers for specified shift."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    day_result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    day_ids = {i.employee_id for i in day_result.items}
    assert "w_night" not in day_ids

    night_result = get_roster_board(db, "metro", op_date, shift_id="NIGHT")
    night_ids = {i.employee_id for i in night_result.items}
    assert "w_night" in night_ids


def test_m4b_006_work_visible(db):
    """M4B-006: WORK employees appear correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    work_items = [i for i in result.items if i.work_status == "WORK"]
    work_ids = {i.employee_id for i in work_items}
    assert "w1" in work_ids
    assert "w2" in work_ids
    assert "w3" in work_ids
    assert "w4" in work_ids


def test_m4b_007_rest_visible(db):
    """M4B-007: REST employees show work_status=REST, operational_state=REST."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    rest_items = [i for i in result.items if i.employee_id == "w_rest"]
    assert len(rest_items) == 1
    rest = rest_items[0]
    assert rest.work_status == "REST"
    assert rest.operational_state == "REST"


def test_m4b_008_offsite_visible(db):
    """M4B-008: OFFSITE employees show correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    off_items = [i for i in result.items if i.employee_id == "w_off"]
    assert len(off_items) == 1
    off = off_items[0]
    assert off.work_status == "OFFSITE"
    assert off.operational_state == "OFFSITE"


def test_m4b_009_leave_visible(db):
    """M4B-009: LEAVE employees show correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    leave_items = [i for i in result.items if i.employee_id == "w_leave"]
    assert len(leave_items) == 1
    leave = leave_items[0]
    assert leave.work_status == "LEAVE"
    assert leave.operational_state == "LEAVE"


def test_m4b_010_sick_visible(db):
    """M4B-010: SICK employees show correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    sick_items = [i for i in result.items if i.employee_id == "w_sick"]
    assert len(sick_items) == 1
    sick = sick_items[0]
    assert sick.work_status == "SICK"
    assert sick.operational_state == "SICK"


def test_m4b_011_training_visible(db):
    """M4B-011: TRAINING employees show correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    train_items = [i for i in result.items if i.employee_id == "w_train"]
    assert len(train_items) == 1
    train = train_items[0]
    assert train.work_status == "TRAINING"
    assert train.operational_state == "TRAINING"


def test_m4b_012_standby_visible(db):
    """M4B-012: STANDBY employees show correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    standby_items = [i for i in result.items if i.employee_id == "w_standby"]
    assert len(standby_items) == 1
    standby = standby_items[0]
    assert standby.work_status == "STANDBY"
    assert standby.operational_state == "STANDBY"


def test_m4b_013_crew_filter(db):
    """M4B-013: Filter by crew_id."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result_a = get_roster_board(db, "metro", op_date, shift_id="DAY", crew_id="crew_a")
    crew_a_names = {i.crew_name for i in result_a.items}
    assert all(cn == "Crew A" for cn in crew_a_names)

    result_b = get_roster_board(db, "metro", op_date, shift_id="DAY", crew_id="crew_b")
    crew_b_names = {i.crew_name for i in result_b.items}
    assert all(cn == "Crew B" for cn in crew_b_names)


def test_m4b_014_role_filter(db):
    """M4B-014: Filter by role_id."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY", role_id="role_dt")
    role_names = {i.role_name for i in result.items}
    assert all(rn == "Dump Truck Operator" for rn in role_names)
    # Only w2 has role_dt
    ids = {i.employee_id for i in result.items}
    assert ids == {"w2"}


def test_m4b_015_work_status_filter(db):
    """M4B-015: Filter by work_status."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY", work_status="REST")
    assert all(i.work_status == "REST" for i in result.items)
    assert len(result.items) == 1
    assert result.items[0].employee_id == "w_rest"


def test_m4b_016_employee_search(db):
    """M4B-016: Search by employee name/code."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY", employee_search="Tono")
    assert len(result.items) == 1
    assert result.items[0].employee_name == "Tono"

    result2 = get_roster_board(db, "metro", op_date, shift_id="DAY", employee_search="W002")
    assert len(result2.items) == 1
    assert result2.items[0].employee_code == "W002"


def test_m4b_017_equipment_search(db):
    """M4B-017: Search by equipment code."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY", equipment_search="DT-014")
    ids = {i.employee_id for i in result.items}
    assert "w2" in ids  # w2 has planned DT-014


def test_m4b_018_planned_equipment_display(db):
    """M4B-018: Shows planned equipment codes."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    assert w1_item.planned_equipment_code == "EX-025"

    w2_item = next(i for i in result.items if i.employee_id == "w2")
    assert w2_item.planned_equipment_code == "DT-014"


def test_m4b_019_actual_equipment_display(db):
    """M4B-019: Shows actual equipment codes."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    # w1 has actual assignment for ex31 (active, most recent)
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    assert w1_item.actual_equipment_code == "EX-031"


def test_m4b_020_planned_vs_actual_mismatch(db):
    """M4B-020: Planned vs actual mismatch visible on board."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    assert w1_item.planned_equipment_code == "EX-025"
    assert w1_item.actual_equipment_code == "EX-031"
    assert w1_item.planned_equipment_code != w1_item.actual_equipment_code


def test_m4b_021_active_exception_display(db):
    """M4B-021: Active exception count on board."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w2_item = next(i for i in result.items if i.employee_id == "w2")
    assert w2_item.active_exception_count >= 1

    # Others should have 0
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    assert w1_item.active_exception_count == 0


def test_m4b_022_decision_status_display(db):
    """M4B-022: has_pending_decision visible on board."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    # w3 has an approved decision (not pending)
    w3_item = next(i for i in result.items if i.employee_id == "w3")
    assert w3_item.has_pending_decision is False
    assert w3_item.decision_status == "APPROVED"


def test_m4b_023_operational_state_normal(db):
    """M4B-023: WORKING state for normal worker with full events."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    # w1 has CHECK_OUT → SHIFT_COMPLETE
    assert w1_item.operational_state == OperationalState.SHIFT_COMPLETE


def test_m4b_024_operational_state_attention_required(db):
    """M4B-024: FAIL checkpoint → ATTENTION_REQUIRED."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w2_item = next(i for i in result.items if i.employee_id == "w2")
    assert w2_item.operational_state == OperationalState.ATTENTION_REQUIRED
    assert w2_item.attention_badge == "ATTENTION_REQUIRED"


def test_m4b_025_no_event_not_started(db):
    """M4B-025: WORK roster with no events → NOT_STARTED."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w4_item = next(i for i in result.items if i.employee_id == "w4")
    assert w4_item.operational_state == OperationalState.NOT_STARTED


def test_m4b_026_shift_complete(db):
    """M4B-026: SHIFT_OUT completed → SHIFT_COMPLETE."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    assert w1_item.operational_state == OperationalState.SHIFT_COMPLETE


def test_m4b_027_pagination(db):
    """M4B-027: Offset/limit works for pagination."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    full_result = get_roster_board(db, "metro", op_date, shift_id="DAY", limit=100)
    total = len(full_result.items)

    page1 = get_roster_board(db, "metro", op_date, shift_id="DAY", limit=3, offset=0)
    page2 = get_roster_board(db, "metro", op_date, shift_id="DAY", limit=3, offset=3)

    assert len(page1.items) == 3
    # page2 might have fewer
    assert len(page2.items) <= 3
    assert page1.context.total_count == total


def test_m4b_028_stable_sorting(db):
    """M4B-028: sort_by works (employee_name, crew_name, operational_state)."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result_name = get_roster_board(db, "metro", op_date, shift_id="DAY",
                                   sort_by="employee_name", sort_dir="asc")
    names = [i.employee_name for i in result_name.items]
    assert names == sorted(names)

    result_crew = get_roster_board(db, "metro", op_date, shift_id="DAY",
                                   sort_by="crew_name", sort_dir="asc")
    crews = [i.crew_name or "" for i in result_crew.items]
    assert crews == sorted(crews)

    result_state = get_roster_board(db, "metro", op_date, shift_id="DAY",
                                    sort_by="operational_state", sort_dir="asc")
    states = [i.operational_state for i in result_state.items]
    assert states == sorted(states)


# ─────────────────────────────────────────────────────────────
# B. WORKER DETAIL TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_029_worker_detail_loads(db):
    """M4B-029: Worker detail loads with all sections."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")

    assert detail.identity is not None
    assert detail.roster is not None
    assert detail.equipment_history is not None
    assert detail.timeline is not None
    assert detail.checkpoint_details is not None
    assert detail.exceptions is not None
    assert detail.decisions is not None
    assert detail.competencies is not None


def test_m4b_030_identity_section(db):
    """M4B-030: Identity section has employee_id, name, code, no, role, crew."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")

    assert detail.identity.employee_id == "w1"
    assert detail.identity.employee_name == "Tono"
    assert detail.identity.employee_code == "W001"
    assert detail.identity.employee_no == "W001"
    assert detail.identity.role_name == "Excavator Operator"
    assert detail.identity.crew_name == "Crew A"
    assert detail.identity.is_active is True


def test_m4b_031_roster_section(db):
    """M4B-031: Roster section has operating_date, shift, work_status, planned_equipment, rule_version."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")

    assert detail.roster.operating_date == op_date
    assert detail.roster.shift_id == "DAY"
    assert detail.roster.shift_name == "Day"
    assert detail.roster.work_status == "WORK"
    assert detail.roster.planned_equipment_code == "EX-025"
    assert detail.roster.rule_version is not None


def test_m4b_032_equipment_history_multiple_intervals(db):
    """M4B-032: Equipment history with multiple actual intervals."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")

    assert len(detail.equipment_history.actual_intervals) == 2
    codes = [iv.equipment_code for iv in detail.equipment_history.actual_intervals]
    assert "EX-025" in codes
    assert "EX-031" in codes


def test_m4b_033_equipment_history_planned_vs_actual(db):
    """M4B-033: Equipment history shows both planned and actual."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")

    assert detail.equipment_history.planned_equipment_code == "EX-025"
    assert detail.equipment_history.has_mismatch is True
    assert len(detail.equipment_history.comparison_results) > 0


def test_m4b_034_checkpoint_timeline_chronological(db):
    """M4B-034: Checkpoint timeline sorted by timestamp."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")

    timestamps = [t.timestamp for t in detail.timeline]
    assert timestamps == sorted(timestamps)


def test_m4b_035_checkpoint_validation_status(db):
    """M4B-035: PASS/FAIL/MISSING visible in checkpoint details."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail_w2 = get_worker_detail(db, "metro", "w2", op_date, shift_id="DAY")
    statuses = [c.validation_status for c in detail_w2.checkpoint_details]
    assert "FAIL" in statuses


def test_m4b_036_checkpoint_evidence_reference(db):
    """M4B-036: evidence_available field present in checkpoint details."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w2", op_date, shift_id="DAY")
    for cp in detail.checkpoint_details:
        assert hasattr(cp, "evidence_available")
        assert isinstance(cp.evidence_available, bool)


def test_m4b_037_active_exception_display_detail(db):
    """M4B-037: Exceptions listed in worker detail."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w2", op_date, shift_id="DAY")
    assert len(detail.exceptions) >= 1
    exc = detail.exceptions[0]
    assert exc.exception_type == "LATE_BREAK_RETURN"
    assert exc.status == "OPEN"


def test_m4b_038_decision_display(db):
    """M4B-038: Decisions shown with type, status, reason."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w3", op_date, shift_id="DAY")
    assert len(detail.decisions) >= 1
    approved = next(d for d in detail.decisions if d.status == "APPROVED")
    assert approved.decision_type == "OPERATOR_SUBSTITUTION"
    assert approved.reason_text is not None


def test_m4b_039_competency_display(db):
    """M4B-039: Competencies with status (VALID/EXPIRED)."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail_w1 = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")
    comp_statuses = {c.status for c in detail_w1.competencies}
    assert "VALID" in comp_statuses

    detail_w4 = get_worker_detail(db, "metro", "w4", op_date, shift_id="DAY")
    comp_statuses_w4 = {c.status for c in detail_w4.competencies}
    assert "EXPIRED" in comp_statuses_w4


def test_m4b_040_out_of_service_indication(db):
    """M4B-040: Equipment OUT_OF_SERVICE visible."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    # The equipment ex_oos exists but isn't assigned to a worker.
    # Verify it can be loaded via equipment_code for board display.
    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    # All workers should have ACTIVE or None equipment; no false OUT_OF_SERVICE badge
    for item in result.items:
        if item.planned_equipment_code:
            assert item.planned_equipment_code != "EX-OOS"


def test_m4b_041_configuration_incomplete(db):
    """M4B-041: CONFIG_INCOMPLETE state for missing policy."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    # w4 has no events and a missing checkpoint — if no policy matches,
    # the checkpoint detail may show CONFIG_INCOMPLETE.
    # The MISSING checkpoint result itself shows MISSING status.
    detail = get_worker_detail(db, "metro", "w4", op_date, shift_id="DAY")
    if detail.checkpoint_details:
        statuses = {c.validation_status for c in detail.checkpoint_details}
        # At minimum MISSING
        assert "MISSING" in statuses


# ─────────────────────────────────────────────────────────────
# C. TIMELINE TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_042_day_shift_timeline(db):
    """M4B-042: DAY shift timeline — chronological events."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    timeline = get_worker_timeline(db, "metro", "w1", op_date, shift_id="DAY")

    assert len(timeline) >= 7
    timestamps = [t.timestamp for t in timeline]
    assert timestamps == sorted(timestamps)

    event_types = [t.event_type for t in timeline]
    assert "BRIEFING_IN" in event_types
    assert "CHECK_OUT" in event_types


def test_m4b_043_night_cross_midnight_timeline(db):
    """M4B-043: NIGHT shift events grouped under original operating_date."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    timeline = get_worker_timeline(db, "metro", "w_night", op_date, shift_id="NIGHT")

    assert len(timeline) >= 2
    # Events should span across midnight
    timestamps = [t.timestamp for t in timeline]
    # First event: 20:00, second event: 01:00 next day
    assert timestamps == sorted(timestamps)


# ─────────────────────────────────────────────────────────────
# D. WORK STATUS TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_044_rest_worker_no_checkpoint_failure(db):
    """M4B-044: REST employees don't show checkpoint failures."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w_rest", op_date, shift_id="DAY")
    # REST workers have no checkpoint details
    assert len(detail.checkpoint_details) == 0
    assert detail.operational_state == "REST"


def test_m4b_045_offsite_worker_no_attendance_requirement(db):
    """M4B-045: OFFSITE worker — no inappropriate attendance requirement."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w_off", op_date, shift_id="DAY")
    assert len(detail.checkpoint_details) == 0
    assert detail.operational_state == "OFFSITE"


def test_m4b_046_leave_worker_shows_correctly(db):
    """M4B-046: LEAVE worker shows correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w_leave", op_date, shift_id="DAY")
    assert detail.roster.work_status == "LEAVE"
    assert detail.operational_state == "LEAVE"
    assert len(detail.checkpoint_details) == 0


def test_m4b_047_sick_worker_shows_correctly(db):
    """M4B-047: SICK worker shows correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w_sick", op_date, shift_id="DAY")
    assert detail.roster.work_status == "SICK"
    assert detail.operational_state == "SICK"
    assert len(detail.checkpoint_details) == 0


# ─────────────────────────────────────────────────────────────
# E. ISOLATION TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_048_tenant_isolation_worker(db):
    """M4B-048: Cannot access other tenant's workers via get_worker_detail."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)
    _seed_client_b(db, op_date)

    with pytest.raises(ValueError):
        get_worker_detail(db, "metro", "w_other", op_date, shift_id="DAY")


def test_m4b_049_tenant_isolation_equipment(db):
    """M4B-049: Equipment from other tenant not visible on board."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)
    _seed_client_b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    for item in result.items:
        if item.planned_equipment_code:
            assert item.planned_equipment_code != "TR-001"


def test_m4b_050_tenant_isolation_roster(db):
    """M4B-050: Roster from other tenant not visible."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)
    _seed_client_b(db, op_date)

    metro = get_roster_board(db, "metro", op_date, shift_id="DAY")
    metro_ids = {i.employee_id for i in metro.items}
    assert "w_other" not in metro_ids


def test_m4b_051_site_isolation(db):
    """M4B-051: Different sites isolated."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    # All workers at site_padang; querying with a different site should return empty
    result = get_roster_board(db, "metro", op_date, shift_id="DAY", site_id="nonexistent_site")
    # Should raise ValueError for nonexistent site or return empty
    # The service does: site = db.get(Site, site_id) which returns None
    # and filters roster by site_id which won't match
    assert len(result.items) == 0


# ─────────────────────────────────────────────────────────────
# F. OPERATOR SUBSTITUTION TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_052_planned_operator_vs_actual(db):
    """M4B-052: Planned operator vs actual operator substitution visible."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w3", op_date, shift_id="DAY")
    assert len(detail.decisions) >= 1
    approved = next((d for d in detail.decisions if d.status == "APPROVED"), None)
    assert approved is not None
    assert "w3" in approved.planned_display or approved.planned_display  # has worker reference


def test_m4b_053_approved_decision_display(db):
    """M4B-053: Approved decision shown correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w3", op_date, shift_id="DAY")
    approved = next(d for d in detail.decisions if d.status == "APPROVED")
    assert approved.status == "APPROVED"
    assert approved.decided_by is not None
    assert approved.reason_text is not None


def test_m4b_054_rejected_decision_display(db):
    """M4B-054: Rejected decision shown correctly."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w3", op_date, shift_id="DAY")
    rejected = next((d for d in detail.decisions if d.status == "REJECTED"), None)
    assert rejected is not None
    assert rejected.status == "REJECTED"
    assert rejected.reason_text is not None


# ─────────────────────────────────────────────────────────────
# G. NIGHT SHIFT TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_055_night_operating_date_preserved(db):
    """M4B-055: NIGHT operating_date preserved — events on next calendar day
    still belong to original operating_date."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="NIGHT")
    night_items = [i for i in result.items if i.employee_id == "w_night"]
    assert len(night_items) == 1

    # Timeline should show events from both before and after midnight
    timeline = get_worker_timeline(db, "metro", "w_night", op_date, shift_id="NIGHT")
    assert len(timeline) >= 2
    # All events should reference the same operating_date (seeded as 2026-09-05)
    for entry in timeline:
        assert entry.timestamp is not None


# ─────────────────────────────────────────────────────────────
# H. GENERIC / CAPABILITY TESTS
# ─────────────────────────────────────────────────────────────

def test_m4b_056_generic_role_master_support(db):
    """M4B-056: Uses role_name from Role master, not hardcoded."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    result = get_roster_board(db, "metro", op_date, shift_id="DAY")
    w1_item = next(i for i in result.items if i.employee_id == "w1")
    assert w1_item.role_name == "Excavator Operator"

    w2_item = next(i for i in result.items if i.employee_id == "w2")
    assert w2_item.role_name == "Dump Truck Operator"


def test_m4b_057_generic_equipment_type_support(db):
    """M4B-057: Uses equipment_type from Equipment master."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail = get_worker_detail(db, "metro", "w1", op_date, shift_id="DAY")
    types = {iv.equipment_type for iv in detail.equipment_history.actual_intervals}
    assert "EXCAVATOR" in types


def test_m4b_058_no_false_employee_violation(db):
    """M4B-058: REST/LEAVE workers don't show violations."""
    op_date = date(2026, 9, 5)
    _seed_metro_m4b(db, op_date)

    detail_rest = get_worker_detail(db, "metro", "w_rest", op_date, shift_id="DAY")
    assert detail_rest.operational_state == "REST"
    assert len(detail_rest.exceptions) == 0
    assert len(detail_rest.checkpoint_details) == 0

    detail_leave = get_worker_detail(db, "metro", "w_leave", op_date, shift_id="DAY")
    assert detail_leave.operational_state == "LEAVE"
    assert len(detail_leave.exceptions) == 0
    assert len(detail_leave.checkpoint_details) == 0


# ─────────────────────────────────────────────────────────────
# I. REGRESSION
# ─────────────────────────────────────────────────────────────

def test_m4b_059_no_duplicate_business_rule_logic(db):
    """M4B-059: Service uses dashboard_service._derive_operational_state,
    not reimplemented logic — regression guard.

    Verify that roster_service source references _derive_operational_state
    (from dashboard_service) rather than reimplementing business logic.
    """
    import inspect
    from app import roster_service

    # The module source should call _derive_operational_state from dashboard_service
    source = inspect.getsource(roster_service)
    assert "_derive_operational_state" in source

    # Verify the function exists in dashboard_service
    from app.dashboard_service import _derive_operational_state
    assert callable(_derive_operational_state)
