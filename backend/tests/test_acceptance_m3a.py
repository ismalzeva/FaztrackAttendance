"""
test_acceptance_m3a.py — MM-M3A Exception Lifecycle Foundation acceptance tests.

Metro Mining exception lifecycle:
- Exception creation from RuleEvaluation, EquipmentDiscrepancy, CheckpointValidation
- Lifecycle transitions: OPEN → ACKNOWLEDGED → RESOLVED / WAIVED
- Immutable audit trail (ExceptionAction history)
- Idempotency (no duplicate exceptions)
- Tenant isolation
- TBC-safe behavior

35 test scenarios.
"""
import pytest
import uuid
from datetime import date, datetime, time, timezone
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.models import (
    Base, Tenant, Worker, Site, ShiftTemplate, Equipment,
    RuleVersion, SiteType, SiteStatus, EquipmentStatus,
    Competency, CompetencyStatus,
    RosterPolicy,
    CheckpointValidationResult, CheckpointValidationStatus,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    ExceptionCase, ExceptionAction, ExceptionActionType, ExceptionSourceType,
    ExceptionStatus, ExceptionSeverity, EXCEPTION_TRANSITIONS,
)
from app.exception_engine import (
    create_exception_from_rule_evaluation,
    create_exception_from_discrepancy,
    create_exception_from_checkpoint,
    acknowledge_exception,
    resolve_exception,
    waive_exception,
    get_exception,
    get_exceptions_for_employee,
    get_action_history,
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


def _seed_metro(db: Session):
    """Seed Metro Mining minimal environment for M3A tests."""
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)

    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Budi", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Andi", is_active=True)
    db.add_all([w1, w2])

    s1 = Site(
        id="s1", tenant_id="metro", site_code="S001", site_name="Mine Site A",
        site_type=SiteType.MINE_SITE, status=SiteStatus.ACTIVE,
        timezone="Asia/Makassar", effective_from=date(2026, 9, 1),
    )
    db.add(s1)

    day = ShiftTemplate(
        id="DAY", tenant_id="metro", shift_code="DAY", shift_name="Day",
        start_time=time(7, 0), end_time=time(19, 0),
        break_start=time(12, 0), break_end=time(13, 0),
        handover_start=time(18, 45), handover_end=time(19, 0),
        crosses_midnight=False,
    )
    db.add(day)

    ex25 = Equipment(
        id="ex25", tenant_id="metro", equipment_code="EX-025",
        equipment_type="EXCAVATOR", status=EquipmentStatus.ACTIVE,
        effective_from=date(2026, 1, 1),
    )
    ex31 = Equipment(
        id="ex31", tenant_id="metro", equipment_code="EX-031",
        equipment_type="EXCAVATOR", status=EquipmentStatus.ACTIVE,
        effective_from=date(2026, 1, 1),
    )
    db.add_all([ex25, ex31])

    rv = RuleVersion(
        id="rv1", tenant_id="metro", version_label="v1.0",
        effective_from=date(2026, 1, 1), config_snapshot_json="{}",
    )
    db.add(rv)

    db.commit()


# ── Helpers ───────────────────────────────────────────────────

def _make_rule_eval(db: Session, employee_id: str, op_date: date,
                    rule_code: str = "LATE_BREAK_RETURN",
                    status=RuleEvaluationStatus.FAIL,
                    severity=RuleSeverity.WARNING,
                    shift_id: str = "DAY",
                    rule_version_id: str = "rv1") -> RuleEvaluation:
    """Create a RuleEvaluation record for testing."""
    re = RuleEvaluation(
        id=_uid(),
        tenant_id="metro", employee_id=employee_id,
        operating_date=op_date, shift_id=shift_id,
        rule_code=rule_code, rule_version_id=rule_version_id,
        evaluated_at=datetime.now(timezone.utc),
        status=status, severity=severity,
        actual_value="13:05", expected_value="13:00",
        evidence_key=f"{op_date}-{rule_code}-{employee_id}-{_uid()}",
        created_at=datetime.now(timezone.utc),
    )
    db.add(re)
    db.flush()
    return re


def _make_discrepancy(db: Session, employee_id: str, op_date: date,
                      actual_equipment_id: str = "ex31",
                      planned_equipment_id: str = "ex25") -> EquipmentDiscrepancy:
    """Create an EquipmentDiscrepancy record for testing."""
    d = EquipmentDiscrepancy(
        id=_uid(),
        tenant_id="metro",
        actual_assignment_id=_uid(),
        employee_id=employee_id,
        operating_date=op_date,
        shift_id="DAY",
        planned_equipment_id=planned_equipment_id,
        actual_equipment_id=actual_equipment_id,
        planned_worker_id=employee_id,
        actual_worker_id=employee_id,
        discrepancy_type=DiscrepancyType.EQUIPMENT_MISMATCH,
        detected_at=datetime.now(timezone.utc),
        status=DiscrepancyStatus.OPEN,
        rule_version_id="rv1",
        created_at=datetime.now(timezone.utc),
    )
    db.add(d)
    db.flush()
    return d


def _make_checkpoint_result(db: Session, employee_id: str, op_date: date,
                            checkpoint_type: str = "BRIEFING_IN",
                            validation_status=CheckpointValidationStatus.FAIL
                            ) -> CheckpointValidationResult:
    """Create a CheckpointValidationResult record for testing."""
    cr = CheckpointValidationResult(
        id=_uid(),
        tenant_id="metro",
        canonical_event_id=_uid(),
        employee_id=employee_id,
        checkpoint_type=checkpoint_type,
        operating_date=op_date,
        shift_id="DAY",
        validation_status=validation_status,
        detected_timestamp=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(cr)
    db.flush()
    return cr


# ═══════════════════════════════════════════════════════════════
# SCENARIO 1: FAIL RuleEvaluation creates OPEN ExceptionCase
# ═══════════════════════════════════════════════════════════════

def test_fail_rule_eval_creates_exception(db):
    """FAIL RuleEvaluation → OPEN ExceptionCase."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, "LATE_BREAK_RETURN")

    case = create_exception_from_rule_evaluation(db, re)

    assert case is not None
    assert case.status == ExceptionStatus.OPEN
    assert case.exception_type == "LATE_BREAK_RETURN"
    assert case.employee_id == "w1"
    assert case.operating_date == op_date
    assert case.severity == ExceptionSeverity.WARNING
    assert case.source_type == ExceptionSourceType.RULE_EVALUATION.value
    assert case.source_id == re.id
    assert case.rule_version_id == "rv1"
    assert case.detected_at is not None
    assert case.opened_at is not None
    assert case.tenant_id == "metro"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 2: PASS creates no exception
# ═══════════════════════════════════════════════════════════════

def test_pass_rule_eval_no_exception(db):
    """PASS RuleEvaluation → no exception created."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, status=RuleEvaluationStatus.PASS)

    case = create_exception_from_rule_evaluation(db, re)

    assert case is None
    assert db.query(ExceptionCase).count() == 0


# ═══════════════════════════════════════════════════════════════
# SCENARIO 3: NOT_APPLICABLE creates no exception
# ═══════════════════════════════════════════════════════════════

def test_not_applicable_no_exception(db):
    """NOT_APPLICABLE → no exception."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, status=RuleEvaluationStatus.NOT_APPLICABLE)

    case = create_exception_from_rule_evaluation(db, re)

    assert case is None
    assert db.query(ExceptionCase).count() == 0


# ═══════════════════════════════════════════════════════════════
# SCENARIO 4: CONFIG_INCOMPLETE creates no employee exception
# ═══════════════════════════════════════════════════════════════

def test_config_incomplete_no_exception(db):
    """CONFIG_INCOMPLETE → no employee violation exception."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, status=RuleEvaluationStatus.CONFIG_INCOMPLETE)

    case = create_exception_from_rule_evaluation(db, re)

    assert case is None
    assert db.query(ExceptionCase).count() == 0


# ═══════════════════════════════════════════════════════════════
# SCENARIO 5: BLOCKED_POLICY_DECISION creates no exception
# ═══════════════════════════════════════════════════════════════

def test_blocked_policy_no_exception(db):
    """BLOCKED_POLICY_DECISION → no confirmed employee violation."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, status=RuleEvaluationStatus.BLOCKED_POLICY_DECISION)

    case = create_exception_from_rule_evaluation(db, re)

    assert case is None
    assert db.query(ExceptionCase).count() == 0


# ═══════════════════════════════════════════════════════════════
# SCENARIO 6: EquipmentDiscrepancy creates linked exception
# ═══════════════════════════════════════════════════════════════

def test_discrepancy_creates_exception(db):
    """EquipmentDiscrepancy (OPEN) → ExceptionCase EQUIPMENT_MISMATCH."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    d = _make_discrepancy(db, "w1", op_date)

    case = create_exception_from_discrepancy(db, d)

    assert case is not None
    assert case.status == ExceptionStatus.OPEN
    assert case.exception_type == "EQUIPMENT_MISMATCH"
    assert case.source_type == ExceptionSourceType.EQUIPMENT_DISCREPANCY.value
    assert case.source_id == d.id
    assert case.employee_id == "w1"
    assert case.equipment_id == "ex31"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 7: Duplicate processing is idempotent (rule eval)
# ═══════════════════════════════════════════════════════════════

def test_idempotent_rule_eval(db):
    """Same RuleEvaluation processed twice → one ExceptionCase."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)

    case1 = create_exception_from_rule_evaluation(db, re)
    case2 = create_exception_from_rule_evaluation(db, re)

    assert case1.id == case2.id
    assert db.query(ExceptionCase).count() == 1


# ═══════════════════════════════════════════════════════════════
# SCENARIO 8: Duplicate processing is idempotent (discrepancy)
# ═══════════════════════════════════════════════════════════════

def test_idempotent_discrepancy(db):
    """Same EquipmentDiscrepancy processed twice → one ExceptionCase."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    d = _make_discrepancy(db, "w1", op_date)

    case1 = create_exception_from_discrepancy(db, d)
    case2 = create_exception_from_discrepancy(db, d)

    assert case1.id == case2.id
    assert db.query(ExceptionCase).count() == 1


# ═══════════════════════════════════════════════════════════════
# SCENARIO 9: OPEN → ACKNOWLEDGED valid
# ═══════════════════════════════════════════════════════════════

def test_open_to_acknowledged(db):
    """OPEN → ACKNOWLEDGED transition is valid."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    result = acknowledge_exception(
        db, case.id, "metro",
        actor_user_id="supervisor1",
        reason="Reviewing this case",
    )

    assert result.status == ExceptionStatus.ACKNOWLEDGED
    assert result.acknowledged_at is not None
    assert result.current_owner_id == "supervisor1"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 10: ACKNOWLEDGED → RESOLVED valid
# ═══════════════════════════════════════════════════════════════

def test_acknowledged_to_resolved(db):
    """ACKNOWLEDGED → RESOLVED transition is valid."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    acknowledge_exception(db, case.id, "metro", actor_user_id="sup1")

    result = resolve_exception(
        db, case.id, "metro",
        actor_user_id="sup1",
        reason="Break was justified due to equipment issue",
    )

    assert result.status == ExceptionStatus.RESOLVED
    assert result.resolved_at is not None


# ═══════════════════════════════════════════════════════════════
# SCENARIO 11: OPEN → WAIVED valid
# ═══════════════════════════════════════════════════════════════

def test_open_to_waived(db):
    """OPEN → WAIVED transition is valid."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    result = waive_exception(
        db, case.id, "metro",
        actor_user_id="admin1",
        reason="First offense, verbal warning issued",
    )

    assert result.status == ExceptionStatus.WAIVED
    assert result.waived_at is not None


# ═══════════════════════════════════════════════════════════════
# SCENARIO 12: ACKNOWLEDGED → WAIVED valid
# ═══════════════════════════════════════════════════════════════

def test_acknowledged_to_waived(db):
    """ACKNOWLEDGED → WAIVED transition is valid."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    acknowledge_exception(db, case.id, "metro", actor_user_id="sup1")

    result = waive_exception(
        db, case.id, "metro",
        actor_user_id="sup1",
        reason="Verified: supervisor-approved extended break",
    )

    assert result.status == ExceptionStatus.WAIVED


# ═══════════════════════════════════════════════════════════════
# SCENARIO 13: Invalid RESOLVED → OPEN rejected
# ═══════════════════════════════════════════════════════════════

def test_resolved_cannot_reopen(db):
    """RESOLVED → OPEN is an invalid transition (terminal state)."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    resolve_exception(db, case.id, "metro", actor_user_id="sup1", reason="Done")

    with pytest.raises(ValueError, match="Invalid transition"):
        acknowledge_exception(db, case.id, "metro", actor_user_id="sup2")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 14: Invalid WAIVED → ACKNOWLEDGED rejected
# ═══════════════════════════════════════════════════════════════

def test_waived_cannot_acknowledge(db):
    """WAIVED → ACKNOWLEDGED is an invalid transition (terminal state)."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    waive_exception(db, case.id, "metro", actor_user_id="sup1", reason="Waived")

    with pytest.raises(ValueError, match="Invalid transition"):
        acknowledge_exception(db, case.id, "metro", actor_user_id="sup2")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 15: Action history created for acknowledgement
# ═══════════════════════════════════════════════════════════════

def test_acknowledge_creates_action(db):
    """Acknowledging creates an immutable ExceptionAction record."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    acknowledge_exception(db, case.id, "metro", actor_user_id="sup1")

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 1
    assert actions[0].action_type == ExceptionActionType.ACKNOWLEDGE
    assert actions[0].actor_user_id == "sup1"
    assert actions[0].previous_status == ExceptionStatus.OPEN
    assert actions[0].new_status == ExceptionStatus.ACKNOWLEDGED


# ═══════════════════════════════════════════════════════════════
# SCENARIO 16: Action history created for resolution
# ═══════════════════════════════════════════════════════════════

def test_resolve_creates_action(db):
    """Resolving creates an immutable ExceptionAction record."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    resolve_exception(db, case.id, "metro", actor_user_id="sup1", reason="Resolved")

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 1
    assert actions[0].action_type == ExceptionActionType.RESOLVE
    assert actions[0].reason == "Resolved"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 17: Action history created for waiver
# ═══════════════════════════════════════════════════════════════

def test_waive_creates_action(db):
    """Waiving creates an immutable ExceptionAction record."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)
    waive_exception(db, case.id, "metro", actor_user_id="sup1", reason="Waived reason")

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 1
    assert actions[0].action_type == ExceptionActionType.WAIVE
    assert actions[0].reason == "Waived reason"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 18: Previous/new status retained in action
# ═══════════════════════════════════════════════════════════════

def test_status_transition_recorded(db):
    """Each action records previous_status and new_status."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    acknowledge_exception(db, case.id, "metro", actor_user_id="sup1")
    resolve_exception(db, case.id, "metro", actor_user_id="sup1", reason="Done")

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 2

    assert actions[0].previous_status == ExceptionStatus.OPEN
    assert actions[0].new_status == ExceptionStatus.ACKNOWLEDGED

    assert actions[1].previous_status == ExceptionStatus.ACKNOWLEDGED
    assert actions[1].new_status == ExceptionStatus.RESOLVED


# ═══════════════════════════════════════════════════════════════
# SCENARIO 19: Original RuleEvaluation unchanged after waiver
# ═══════════════════════════════════════════════════════════════

def test_rule_eval_unchanged_after_waiver(db):
    """Waiving an exception does NOT modify the source RuleEvaluation."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    original_status = re.status
    original_severity = re.severity

    case = create_exception_from_rule_evaluation(db, re)
    waive_exception(db, case.id, "metro", actor_user_id="sup1", reason="Waived")

    db.refresh(re)
    assert re.status == original_status
    assert re.severity == original_severity


# ═══════════════════════════════════════════════════════════════
# SCENARIO 20: Original EquipmentDiscrepancy unchanged after resolution
# ═══════════════════════════════════════════════════════════════

def test_discrepancy_unchanged_after_resolution(db):
    """Resolving an exception does NOT modify the source EquipmentDiscrepancy."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    d = _make_discrepancy(db, "w1", op_date)
    original_status = d.status
    original_type = d.discrepancy_type

    case = create_exception_from_discrepancy(db, d)
    resolve_exception(db, case.id, "metro", actor_user_id="sup1", reason="Fixed")

    db.refresh(d)
    assert d.status == original_status
    assert d.discrepancy_type == original_type


# ═══════════════════════════════════════════════════════════════
# SCENARIO 21: Reason stored for waiver
# ═══════════════════════════════════════════════════════════════

def test_waiver_reason_stored(db):
    """Waiver reason is stored in the ExceptionAction."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    waive_exception(
        db, case.id, "metro",
        actor_user_id="admin1",
        reason="Verified medical emergency during break",
        note="Worker provided hospital letter",
    )

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 1
    assert actions[0].reason == "Verified medical emergency during break"
    assert actions[0].note == "Worker provided hospital letter"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 22: Actor stored for action
# ═══════════════════════════════════════════════════════════════

def test_actor_stored(db):
    """Actor user ID is stored in each ExceptionAction."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    acknowledge_exception(db, case.id, "metro", actor_user_id="supervisor-A")
    resolve_exception(db, case.id, "metro", actor_user_id="supervisor-B", reason="Done")

    actions = get_action_history(db, case.id, "metro")
    assert actions[0].actor_user_id == "supervisor-A"
    assert actions[1].actor_user_id == "supervisor-B"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 23: Rule version preserved
# ═══════════════════════════════════════════════════════════════

def test_rule_version_preserved(db):
    """ExceptionCase retains the rule_version_id from the source."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, rule_version_id="rv1")

    case = create_exception_from_rule_evaluation(db, re)
    assert case.rule_version_id == "rv1"

    waive_exception(db, case.id, "metro", actor_user_id="admin", reason="Test")
    assert case.rule_version_id == "rv1"


# ═══════════════════════════════════════════════════════════════
# SCENARIO 24: Severity preserved
# ═══════════════════════════════════════════════════════════════

def test_severity_preserved(db):
    """ExceptionCase severity matches source RuleEvaluation severity."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date, severity=RuleSeverity.CRITICAL)

    case = create_exception_from_rule_evaluation(db, re)
    assert case.severity == ExceptionSeverity.CRITICAL


# ═══════════════════════════════════════════════════════════════
# SCENARIO 25: Tenant isolation on exception creation
# ═══════════════════════════════════════════════════════════════

def test_tenant_isolation_creation(db):
    """Metro exception cannot be queried via another tenant."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    found = get_exception(db, case.id, "metro")
    assert found is not None

    not_found = get_exception(db, case.id, "lumin")
    assert not_found is None


# ═══════════════════════════════════════════════════════════════
# SCENARIO 26: Tenant isolation on lifecycle action
# ═══════════════════════════════════════════════════════════════

def test_tenant_isolation_lifecycle(db):
    """Cannot acknowledge/resolve/waive an exception with wrong tenant."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    with pytest.raises(ValueError, match="not found for tenant"):
        acknowledge_exception(db, case.id, "wrong_tenant", actor_user_id="sup1")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 27: Cross-tenant source detection rejected
# ═══════════════════════════════════════════════════════════════

def test_cross_tenant_source_rejected(db):
    """Exception creation respects tenant_id from source."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    assert case.tenant_id == "metro"

    lumin_cases = get_exceptions_for_employee(db, "lumin", "w1")
    assert len(lumin_cases) == 0


# ═══════════════════════════════════════════════════════════════
# SCENARIO 28: Repeated lifecycle request idempotent
# ═══════════════════════════════════════════════════════════════

def test_repeated_acknowledge_no_duplicate_history(db):
    """Cannot acknowledge twice — second attempt raises ValueError."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    acknowledge_exception(db, case.id, "metro", actor_user_id="sup1")

    with pytest.raises(ValueError, match="Invalid transition"):
        acknowledge_exception(db, case.id, "metro", actor_user_id="sup2")

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 1


# ═══════════════════════════════════════════════════════════════
# SCENARIO 29: Terminal states block all transitions
# ═══════════════════════════════════════════════════════════════

def test_terminal_states_block_all(db):
    """RESOLVED and WAIVED are terminal — no transitions allowed."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)

    # RESOLVED case — no transitions allowed
    re1 = _make_rule_eval(db, "w1", op_date, "LATE_BREAK_RETURN")
    case1 = create_exception_from_rule_evaluation(db, re1)
    resolve_exception(db, case1.id, "metro", actor_user_id="sup1", reason="Done")

    with pytest.raises(ValueError, match="Invalid transition"):
        acknowledge_exception(db, case1.id, "metro", actor_user_id="sup2")
    with pytest.raises(ValueError, match="Invalid transition"):
        resolve_exception(db, case1.id, "metro", actor_user_id="sup2", reason="Again")
    with pytest.raises(ValueError, match="Invalid transition"):
        waive_exception(db, case1.id, "metro", actor_user_id="sup2", reason="test")

    # WAIVED case — no transitions allowed
    re2 = _make_rule_eval(db, "w1", op_date, "MISSING_BRIEFING")
    case2 = create_exception_from_rule_evaluation(db, re2)
    waive_exception(db, case2.id, "metro", actor_user_id="sup1", reason="Waived")

    with pytest.raises(ValueError, match="Invalid transition"):
        acknowledge_exception(db, case2.id, "metro", actor_user_id="sup2")
    with pytest.raises(ValueError, match="Invalid transition"):
        resolve_exception(db, case2.id, "metro", actor_user_id="sup2", reason="test")
    with pytest.raises(ValueError, match="Invalid transition"):
        waive_exception(db, case2.id, "metro", actor_user_id="sup2", reason="test")


# ═══════════════════════════════════════════════════════════════
# SCENARIO 30: Full trace detection → exception → action
# ═══════════════════════════════════════════════════════════════

def test_full_trace(db):
    """Full audit trace: RuleEvaluation → ExceptionCase → ExceptionAction history."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)

    re = _make_rule_eval(db, "w1", op_date, "LATE_BREAK_RETURN",
                         severity=RuleSeverity.CRITICAL)
    eval_id = re.id

    case = create_exception_from_rule_evaluation(db, re)
    assert case.status == ExceptionStatus.OPEN
    assert case.source_id == eval_id
    assert case.severity == ExceptionSeverity.CRITICAL

    acknowledge_exception(db, case.id, "metro", actor_user_id="sup1",
                          reason="Looking into it")
    assert case.status == ExceptionStatus.ACKNOWLEDGED

    resolve_exception(db, case.id, "metro", actor_user_id="sup1",
                      reason="Equipment issue caused delay",
                      evidence_ref="photo://evidence/001")
    assert case.status == ExceptionStatus.RESOLVED

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 2

    assert actions[0].action_type == ExceptionActionType.ACKNOWLEDGE
    assert actions[0].previous_status == ExceptionStatus.OPEN
    assert actions[0].new_status == ExceptionStatus.ACKNOWLEDGED
    assert actions[0].reason == "Looking into it"

    assert actions[1].action_type == ExceptionActionType.RESOLVE
    assert actions[1].previous_status == ExceptionStatus.ACKNOWLEDGED
    assert actions[1].new_status == ExceptionStatus.RESOLVED
    assert actions[1].reason == "Equipment issue caused delay"
    assert actions[1].evidence_ref == "photo://evidence/001"

    db.refresh(re)
    assert re.id == eval_id
    assert re.status == RuleEvaluationStatus.FAIL
    assert re.severity == RuleSeverity.CRITICAL


# ═══════════════════════════════════════════════════════════════
# SCENARIO 31: CheckpointValidationResult FAIL creates exception
# ═══════════════════════════════════════════════════════════════

def test_checkpoint_fail_creates_exception(db):
    """FAIL CheckpointValidationResult → ExceptionCase."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    cr = _make_checkpoint_result(db, "w1", op_date, "BRIEFING_IN",
                                 CheckpointValidationStatus.FAIL)

    case = create_exception_from_checkpoint(db, cr)

    assert case is not None
    assert case.status == ExceptionStatus.OPEN
    assert case.source_type == ExceptionSourceType.CHECKPOINT_VALIDATION.value
    assert case.source_id == cr.id
    assert case.exception_type == "MISSING_BRIEFING"


def test_checkpoint_pass_no_exception(db):
    """PASS CheckpointValidationResult → no exception."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    cr = _make_checkpoint_result(db, "w1", op_date, "BRIEFING_IN",
                                 CheckpointValidationStatus.PASS)

    case = create_exception_from_checkpoint(db, cr)
    assert case is None


# ═══════════════════════════════════════════════════════════════
# SCENARIO 32: Waiver without reason rejected
# ═══════════════════════════════════════════════════════════════

def test_waiver_requires_reason(db):
    """Waiving without a reason raises ValueError."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    with pytest.raises(ValueError, match="requires a documented reason"):
        waive_exception(db, case.id, "metro", actor_user_id="sup1", reason="")

    with pytest.raises(ValueError, match="requires a documented reason"):
        waive_exception(db, case.id, "metro", actor_user_id="sup1", reason=None)


# ═══════════════════════════════════════════════════════════════
# SCENARIO 33: Multiple rule codes create distinct exception types
# ═══════════════════════════════════════════════════════════════

def test_multiple_rule_codes(db):
    """Different rule codes create distinct exception types."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)

    codes = [
        "LATE_BREAK_RETURN", "MISSING_BRIEFING", "LATE_BRIEFING",
        "MISSING_SHIFT_OUT", "EARLY_HANDOVER", "LATE_HANDOVER",
        "EQUIPMENT_MISMATCH", "INSUFFICIENT_REST", "OFFSITE_ASSIGNMENT",
    ]

    for code in codes:
        re = _make_rule_eval(db, "w1", op_date, rule_code=code)
        case = create_exception_from_rule_evaluation(db, re)
        assert case is not None
        assert case.exception_type == code

    assert db.query(ExceptionCase).count() == len(codes)


# ═══════════════════════════════════════════════════════════════
# SCENARIO 34: Employee query filters correctly
# ═══════════════════════════════════════════════════════════════

def test_employee_query_filtering(db):
    """get_exceptions_for_employee filters by tenant, employee, date, status."""
    _seed_metro(db)
    op_date1 = date(2026, 9, 5)
    op_date2 = date(2026, 9, 6)

    re1 = _make_rule_eval(db, "w1", op_date1, "LATE_BREAK_RETURN")
    re2 = _make_rule_eval(db, "w1", op_date2, "MISSING_BRIEFING")
    re3 = _make_rule_eval(db, "w2", op_date1, "LATE_BREAK_RETURN")

    create_exception_from_rule_evaluation(db, re1)
    create_exception_from_rule_evaluation(db, re2)
    create_exception_from_rule_evaluation(db, re3)

    w1_cases = get_exceptions_for_employee(db, "metro", "w1")
    assert len(w1_cases) == 2

    w1_d1 = get_exceptions_for_employee(db, "metro", "w1", operating_date=op_date1)
    assert len(w1_d1) == 1
    assert w1_d1[0].operating_date == op_date1

    w1_open = get_exceptions_for_employee(db, "metro", "w1", status=ExceptionStatus.OPEN)
    assert len(w1_open) == 2

    w1_resolved = get_exceptions_for_employee(db, "metro", "w1", status=ExceptionStatus.RESOLVED)
    assert len(w1_resolved) == 0

    w2_cases = get_exceptions_for_employee(db, "metro", "w2")
    assert len(w2_cases) == 1

    lumin_cases = get_exceptions_for_employee(db, "lumin", "w1")
    assert len(lumin_cases) == 0


# ═══════════════════════════════════════════════════════════════
# SCENARIO 35: OPEN → RESOLVED direct resolution allowed
# ═══════════════════════════════════════════════════════════════

def test_open_direct_to_resolved(db):
    """OPEN → RESOLVED direct transition is allowed."""
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    re = _make_rule_eval(db, "w1", op_date)
    case = create_exception_from_rule_evaluation(db, re)

    result = resolve_exception(
        db, case.id, "metro",
        actor_user_id="sup1",
        reason="Immediate resolution possible",
    )

    assert result.status == ExceptionStatus.RESOLVED
    assert result.resolved_at is not None

    actions = get_action_history(db, case.id, "metro")
    assert len(actions) == 1
    assert actions[0].previous_status == ExceptionStatus.OPEN
    assert actions[0].new_status == ExceptionStatus.RESOLVED
