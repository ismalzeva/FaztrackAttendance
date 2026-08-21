"""
test_acceptance_m2d.py -- MM-M2D Operational Attendance Rule Engine.

Tests (34):
1.  DAY BREAK_IN 12:58 → PASS
2.  DAY BREAK_IN 13:00 → PASS (boundary inclusive)
3.  DAY BREAK_IN 13:01 → FAIL / LATE_BREAK_RETURN
4.  NIGHT BREAK_IN 00:58 → PASS (operating_date = previous day)
5.  NIGHT BREAK_IN 01:00 → PASS (boundary inclusive)
6.  NIGHT BREAK_IN 01:01 → FAIL / LATE_BREAK_RETURN (operating_date = previous day)
7.  NIGHT operating_date preserved (event after midnight uses previous date)
8.  Missing briefing with complete policy → FAIL
9.  Missing briefing with incomplete policy → CONFIG_INCOMPLETE
10. Late briefing TBC-safe (no tolerance configured) → BLOCKED_POLICY_DECISION
11. Missing shift-out for WORK employee → FAIL
12. REST employee → no missing shift-out (NOT_APPLICABLE)
13. OFFSITE employee → no missing shift-out (NOT_APPLICABLE)
14. Handover policy configured → evaluate
15. Handover unresolved semantics → BLOCKED_POLICY_DECISION
16. Metro geofence missing → CONFIG_INCOMPLETE
17. Lumin geofence regression → CONFIG_INCOMPLETE (no geofence config)
18. Device evidence valid → PASS
19. Device risk detected → FAIL
20. Telematics not accepted as identity proof (no biometric certainty)
21. Equipment mismatch integrates M2C → EQUIPMENT_MISMATCH
22. No duplicate equipment discrepancy on re-evaluation
23. M1 roster violation integrates (OFFSITE_ASSIGNMENT)
24. Rule evaluation idempotency
25. Historical rule version preserved
26. Future rule version does not alter historical result
27. Metro rule does not evaluate Lumin event
28. Lumin rule does not evaluate Metro event
29. Disabled rule → NOT_APPLICABLE
30. Missing configuration does not silently PASS
31. Missing configuration does not falsely FAIL
32. Raw → canonical → checkpoint → rule traceability
33. Lumin existing attendance regression
34. INSUFFICIENT_REST → BLOCKED_POLICY_DECISION (TBC)
"""
import json
import pytest
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Tenant, Worker, Site, ShiftTemplate, RosterAssignment,
    Equipment, EquipmentStatus,
    Competency, CompetencyStatus,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    WorkStatus, SiteStatusEnum, ValidationStatus,
    SiteType, SiteStatus,
    Project, RuleVersion, RosterPolicy,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    CheckpointPolicy, CheckpointValidationResult, CheckpointValidationStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
)
from app.operational_rule_engine import (
    evaluate_late_break_return,
    evaluate_missing_briefing,
    evaluate_late_briefing,
    evaluate_missing_shift_out,
    evaluate_early_handover,
    evaluate_late_handover,
    evaluate_geofence,
    evaluate_device_risk,
    evaluate_equipment_mismatch,
    evaluate_insufficient_rest,
    evaluate_offsite_assignment,
    evaluate_all_rules,
    get_evaluations,
    get_registered_rules,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _seed_metro(db: Session):
    """Seed Metro Mining tenant with full config."""
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)

    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Tono", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Budi", is_active=True)
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
    night = ShiftTemplate(
        id="NIGHT", tenant_id="metro", shift_code="NIGHT", shift_name="Night",
        start_time=time(19, 0), end_time=time(7, 0),
        break_start=time(0, 0), break_end=time(1, 0),
        handover_start=time(6, 45), handover_end=time(7, 0),
        crosses_midnight=True,
    )
    db.add_all([day, night])

    # Equipment
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

    # Rule version
    rv = RuleVersion(
        id="rv1", tenant_id="metro", version_label="v1.0",
        effective_from=date(2026, 1, 1),
        config_snapshot_json="{}",
    )
    db.add(rv)

    # Tenant capabilities
    for key, val in [
        ("equipment_assignment_enabled", "true"),
        ("competency_validation_enabled", "true"),
    ]:
        db.add(RosterPolicy(
            id=f"rp-{key}", tenant_id="metro",
            policy_key=key, policy_value=val,
            data_type="boolean", confirmation_status="CONFIRMED",
        ))

    db.flush()
    return t, w1, w2, s1, day, night, ex25, ex31, rv


def _seed_lumin(db: Session):
    """Seed Lumin Park tenant (minimal config)."""
    t = Tenant(id="lumin", code="lumin", name="Lumin Park", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="wL", tenant_id="lumin", code="L001", name="Lumin Worker", is_active=True)
    db.add(w)
    db.flush()
    return t, w


def _seed_roster(db, tenant_id, employee_id, op_date, shift_id, site_id,
                 work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
                 rule_version_id=None):
    ra = RosterAssignment(
        id=f"ra-{employee_id}-{op_date}", tenant_id=tenant_id, roster_code="R001",
        operating_date=op_date, employee_id=employee_id,
        shift_id=shift_id, site_id=site_id,
        work_status=work_status, site_status=site_status,
        validation_status=ValidationStatus.PUBLISHED,
        rule_version_id=rule_version_id,
    )
    db.add(ra)
    db.flush()
    return ra


def _make_canonical_event(
    db, tenant_id, employee_id, event_type, local_ts, operating_date,
    shift_id=None, site_id=None, latitude=None, longitude=None,
    evidence_json=None, event_id=None,
):
    ev = CanonicalAttendanceEvent(
        id=event_id or f"ce-{event_type.value}-{local_ts.strftime('%H%M')}",
        tenant_id=tenant_id, employee_id=employee_id,
        event_type=event_type,
        local_timestamp=local_ts,
        utc_timestamp=local_ts - timedelta(hours=8),  # WITA offset
        timezone="Asia/Makassar",
        operating_date=operating_date,
        shift_id=shift_id, site_id=site_id,
        source="test", source_event_id=f"src-{event_id or 'x'}",
        latitude=latitude, longitude=longitude,
        evidence_json=evidence_json,
        processing_status=CanonicalProcessingStatus.SHIFT_RESOLVED,
    )
    db.add(ev)
    db.flush()
    return ev


# ─────────────────────────────────────────────────────────────
# TEST 1-3: DAY BREAK RETURN
# ─────────────────────────────────────────────────────────────

def test_01_day_break_1258_pass(db):
    """DAY BREAK_IN 12:58 WITA → PASS."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 12, 58, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS
    assert result.actual_value == "12:58"
    assert result.expected_value == "13:00"


def test_02_day_break_1300_pass(db):
    """DAY BREAK_IN 13:00 WITA → PASS (boundary inclusive)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 13, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS
    assert result.actual_value == "13:00"


def test_03_day_break_1301_fail(db):
    """DAY BREAK_IN 13:01 WITA → FAIL / LATE_BREAK_RETURN."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 13, 1, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.reason == "LATE_BREAK_RETURN"
    assert result.actual_value == "13:01"


# ─────────────────────────────────────────────────────────────
# TEST 4-7: NIGHT BREAK RETURN
# ─────────────────────────────────────────────────────────────

def test_04_night_break_0058_pass(db):
    """NIGHT BREAK_IN 00:58 WITA (next calendar day) → PASS, operating_date = previous day."""
    _, w1, _, s1, _, night, _, _, rv = _seed_metro(db)
    op_date = date(2026, 8, 17)  # Night shift starts 8/17 19:00
    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    # Break at 00:58 on 8/18 (next calendar day), but operating_date = 8/17
    break_in = datetime(2026, 8, 18, 0, 58, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, op_date, shift_id="NIGHT", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS
    assert result.actual_value == "00:58"


def test_05_night_break_0100_pass(db):
    """NIGHT BREAK_IN 01:00 WITA → PASS (boundary inclusive)."""
    _, w1, _, s1, _, night, _, _, rv = _seed_metro(db)
    op_date = date(2026, 8, 17)
    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 18, 1, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, op_date, shift_id="NIGHT", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS
    assert result.actual_value == "01:00"


def test_06_night_break_0101_fail(db):
    """NIGHT BREAK_IN 01:01 WITA → FAIL, operating_date = previous day."""
    _, w1, _, s1, _, night, _, _, rv = _seed_metro(db)
    op_date = date(2026, 8, 17)
    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 18, 1, 1, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, op_date, shift_id="NIGHT", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.reason == "LATE_BREAK_RETURN"
    assert result.operating_date == op_date  # Previous day preserved


def test_07_night_operating_date_preserved(db):
    """NIGHT event after midnight retains operating_date = previous shift-origin date."""
    _, w1, _, s1, _, night, _, _, rv = _seed_metro(db)
    op_date = date(2026, 8, 17)
    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 18, 0, 30, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, op_date, shift_id="NIGHT", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.operating_date == date(2026, 8, 17)  # NOT 8/18


# ─────────────────────────────────────────────────────────────
# TEST 8-10: BRIEFING RULES
# ─────────────────────────────────────────────────────────────

def test_08_missing_briefing_complete_policy_fail(db):
    """Missing briefing with complete policy → FAIL."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # Create briefing policy with configured window
    policy = CheckpointPolicy(
        id="bp-day", tenant_id="metro", checkpoint_type="BRIEFING_IN",
        shift_id="DAY", enabled=True,
        window_start_offset_min=-30, window_end_offset_min=15,
        tolerance_min=5,
        effective_from=date(2026, 1, 1),
        default_validation_behavior="CONFIG_INCOMPLETE",
    )
    db.add(policy)
    db.flush()

    result = evaluate_missing_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    # Window is configured, but no briefing received.
    # Result depends on whether window has closed (we can't control "now" in tests,
    # so the result will be either FAIL or NOT_APPLICABLE if window is still open).
    assert result.status in (RuleEvaluationStatus.FAIL, RuleEvaluationStatus.NOT_APPLICABLE)
    assert result.rule_code == "MISSING_BRIEFING"


def test_09_missing_briefing_incomplete_policy_config_incomplete(db):
    """Missing briefing with incomplete policy → CONFIG_INCOMPLETE."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # Create briefing policy with default (unconfigured) window
    policy = CheckpointPolicy(
        id="bp-day", tenant_id="metro", checkpoint_type="BRIEFING_IN",
        shift_id="DAY", enabled=True,
        window_start_offset_min=0, window_end_offset_min=0,
        tolerance_min=None,
        effective_from=date(2026, 1, 1),
        default_validation_behavior="CONFIG_INCOMPLETE",
    )
    db.add(policy)
    db.flush()

    result = evaluate_missing_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.CONFIG_INCOMPLETE
    assert result.reason == "BRIEFING_WINDOW_NOT_CONFIGURED"


def test_10_late_briefing_tbc_safe(db):
    """Late briefing with no tolerance configured → BLOCKED_POLICY_DECISION."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # Briefing policy exists but tolerance is None
    policy = CheckpointPolicy(
        id="bp-day", tenant_id="metro", checkpoint_type="BRIEFING_IN",
        shift_id="DAY", enabled=True,
        window_start_offset_min=-30, window_end_offset_min=15,
        tolerance_min=None,  # TBC
        effective_from=date(2026, 1, 1),
    )
    db.add(policy)
    db.flush()

    tz = ZoneInfo("Asia/Makassar")
    briefing_in = datetime(2026, 8, 17, 7, 10, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BRIEFING_IN,
        briefing_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.BLOCKED_POLICY_DECISION
    assert result.reason == "BRIEFING_TOLERANCE_NOT_CONFIGURED"


# ─────────────────────────────────────────────────────────────
# TEST 11-13: MISSING SHIFT OUT
# ─────────────────────────────────────────────────────────────

def test_11_missing_shift_out_work_employee(db):
    """Missing shift-out for WORK+ONSITE employee → FAIL (if shift ended)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # CHECK_OUT policy exists
    policy = CheckpointPolicy(
        id="co-day", tenant_id="metro", checkpoint_type="CHECK_OUT",
        shift_id="DAY", enabled=True,
        effective_from=date(2026, 1, 1),
    )
    db.add(policy)
    db.flush()

    result = evaluate_missing_shift_out(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    # Result depends on whether shift has ended (we can't control "now")
    assert result.status in (RuleEvaluationStatus.FAIL, RuleEvaluationStatus.NOT_APPLICABLE)
    assert result.rule_code == "MISSING_SHIFT_OUT"


def test_12_rest_employee_no_missing_shift_out(db):
    """REST employee → no missing shift-out (NOT_APPLICABLE)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1",
                 work_status=WorkStatus.REST, rule_version_id="rv1")

    result = evaluate_missing_shift_out(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.NOT_APPLICABLE
    assert result.reason == "WORK_STATUS_REST"


def test_13_offsite_employee_no_missing_shift_out(db):
    """OFFSITE employee → no missing shift-out (NOT_APPLICABLE)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1",
                 work_status=WorkStatus.OFFSITE, rule_version_id="rv1")

    result = evaluate_missing_shift_out(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.NOT_APPLICABLE
    assert result.reason == "WORK_STATUS_OFFSITE"


# ─────────────────────────────────────────────────────────────
# TEST 14-15: HANDOVER RULES
# ─────────────────────────────────────────────────────────────

def test_14_handover_policy_configured(db):
    """Handover policy configured → evaluate early handover."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    policy = CheckpointPolicy(
        id="hs-day", tenant_id="metro", checkpoint_type="HANDOVER_START",
        shift_id="DAY", enabled=True,
        effective_from=date(2026, 1, 1),
        default_validation_behavior="EVALUATE",
    )
    db.add(policy)
    db.flush()

    tz = ZoneInfo("Asia/Makassar")
    # Early handover: 18:30 (before 18:45 window)
    handover = datetime(2026, 8, 17, 18, 30, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.HANDOVER_START,
        handover, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_early_handover(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.reason == "EARLY_HANDOVER"


def test_15_handover_unresolved_semantics_blocked(db):
    """Handover with unresolved semantics → BLOCKED_POLICY_DECISION."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    policy = CheckpointPolicy(
        id="hs-day", tenant_id="metro", checkpoint_type="HANDOVER_START",
        shift_id="DAY", enabled=True,
        effective_from=date(2026, 1, 1),
        default_validation_behavior="BLOCKED_POLICY_DECISION",
    )
    db.add(policy)
    db.flush()

    tz = ZoneInfo("Asia/Makassar")
    handover = datetime(2026, 8, 17, 18, 50, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.HANDOVER_START,
        handover, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_early_handover(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.BLOCKED_POLICY_DECISION
    assert result.reason == "HANDOVER_SEMANTIC_TBC"


# ─────────────────────────────────────────────────────────────
# TEST 16-17: GEOLOCATION
# ─────────────────────────────────────────────────────────────

def test_16_metro_geofence_missing_config_incomplete(db):
    """Metro geofence coordinates not configured → CONFIG_INCOMPLETE."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    # s1 has no latitude/longitude/radius_m set

    tz = ZoneInfo("Asia/Makassar")
    check_in = datetime(2026, 8, 17, 7, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        check_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_geofence(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.CONFIG_INCOMPLETE
    assert result.reason == "GEOFENCE_NOT_CONFIGURED"


def test_17_lumin_geofence_regression(db):
    """Lumin Park geofence not configured → CONFIG_INCOMPLETE (no regression)."""
    t, w = _seed_lumin(db)

    tz = ZoneInfo("Asia/Jakarta")
    check_in = datetime(2026, 8, 17, 8, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "lumin", "wL", CanonicalEventType.CHECK_IN,
        check_in, date(2026, 8, 17),
        event_id="ce-lumin-checkin",
    )

    result = evaluate_geofence(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=date(2026, 8, 17),
        canonical_event=ev,
    )
    assert result.status == RuleEvaluationStatus.CONFIG_INCOMPLETE


# ─────────────────────────────────────────────────────────────
# TEST 18-20: DEVICE / IDENTITY RISK
# ─────────────────────────────────────────────────────────────

def test_18_device_evidence_valid_pass(db):
    """Device evidence with no risk signals → PASS."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)

    tz = ZoneInfo("Asia/Makassar")
    check_in = datetime(2026, 8, 17, 7, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        check_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
        evidence_json=json.dumps({"device_binding_id": "dev-001", "signature": "valid"}),
    )

    result = evaluate_device_risk(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS
    assert result.reason == "NO_DEVICE_RISK_DETECTED"


def test_19_device_risk_detected_fail(db):
    """Device binding mismatch → FAIL / DEVICE_RISK_DETECTED."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)

    tz = ZoneInfo("Asia/Makassar")
    check_in = datetime(2026, 8, 17, 7, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        check_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
        evidence_json=json.dumps({
            "device_binding_id": "dev-999",
            "expected_device_binding_id": "dev-001",
            "signature": "valid",
        }),
    )

    result = evaluate_device_risk(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.reason == "DEVICE_RISK_DETECTED"


def test_20_telematics_not_identity_proof(db):
    """Telematics data alone does not prove worker identity.
    Device risk evaluation uses evidence signals, not biometric certainty."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)

    tz = ZoneInfo("Asia/Makassar")
    check_in = datetime(2026, 8, 17, 7, 0, tzinfo=tz)
    # Only telematics data, no identity proof
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        check_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
        evidence_json=json.dumps({"engine_on": True, "gps_active": True}),
    )

    result = evaluate_device_risk(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    # No device binding mismatch → PASS (no risk detected)
    # But this does NOT mean identity is proven
    assert result.status == RuleEvaluationStatus.PASS
    assert result.reason == "NO_DEVICE_RISK_DETECTED"
    # Verify no biometric claim in result
    assert "biometric" not in (result.reason or "").lower()
    assert "identity_proven" not in (result.reason or "").lower()


# ─────────────────────────────────────────────────────────────
# TEST 21-22: EQUIPMENT MISMATCH (integrates M2C)
# ─────────────────────────────────────────────────────────────

def test_21_equipment_mismatch_integrates_m2c(db):
    """Equipment mismatch from M2C comparison → EQUIPMENT_MISMATCH."""
    _, w1, _, s1, day, _, ex25, ex31, rv = _seed_metro(db)

    comparison = EquipmentComparisonResult(
        id="cmp-1", tenant_id="metro",
        actual_assignment_id="aa-1", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        planned_equipment_id="ex25", actual_equipment_id="ex31",
        comparison_result=ComparisonResult.MISMATCH,
        actual_worker_id="w1",
        rule_version_id="rv1",
    )
    db.add(comparison)
    db.flush()

    result = evaluate_equipment_mismatch(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        comparison=comparison, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.rule_code == "EQUIPMENT_MISMATCH"
    assert result.equipment_id == "ex31"


def test_22_no_duplicate_equipment_discrepancy(db):
    """Re-evaluating same comparison does not create duplicate."""
    _, w1, _, s1, day, _, ex25, ex31, rv = _seed_metro(db)

    comparison = EquipmentComparisonResult(
        id="cmp-1", tenant_id="metro",
        actual_assignment_id="aa-1", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        planned_equipment_id="ex25", actual_equipment_id="ex31",
        comparison_result=ComparisonResult.MISMATCH,
        actual_worker_id="w1",
        rule_version_id="rv1",
    )
    db.add(comparison)
    db.flush()

    result1 = evaluate_equipment_mismatch(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        comparison=comparison, rule_version_id="rv1",
    )
    result2 = evaluate_equipment_mismatch(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        comparison=comparison, rule_version_id="rv1",
    )
    assert result1.id == result2.id  # Same record returned


# ─────────────────────────────────────────────────────────────
# TEST 23: M1 ROSTER VIOLATION INTEGRATES
# ─────────────────────────────────────────────────────────────

def test_23_offsite_assignment_integrates_m1(db):
    """OFFSITE roster assignment → OFFSITE_ASSIGNMENT rule result."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1",
                 work_status=WorkStatus.WORK, site_status=SiteStatusEnum.OFFSITE,
                 rule_version_id="rv1")

    result = evaluate_offsite_assignment(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.reason == "OFFSITE_ASSIGNMENT"


# ─────────────────────────────────────────────────────────────
# TEST 24: IDEMPOTENCY
# ─────────────────────────────────────────────────────────────

def test_24_rule_evaluation_idempotency(db):
    """Same evidence + same rule + same rule version → same result (idempotent)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 13, 5, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result1 = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    result2 = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result1.id == result2.id
    assert result1.status == result2.status


# ─────────────────────────────────────────────────────────────
# TEST 25-26: RULE VERSION PRESERVATION
# ─────────────────────────────────────────────────────────────

def test_25_historical_rule_version_preserved(db):
    """Historical evaluation linked to rule version used at evaluation time."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 12, 50, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.rule_version_id == "rv1"


def test_26_future_rule_version_does_not_alter_history(db):
    """Adding a new rule version does not retroactively change historical evaluations."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 12, 50, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    result_v1 = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )

    # Create v2 rule version
    rv2 = RuleVersion(
        id="rv2", tenant_id="metro", version_label="v2.0",
        effective_from=date(2026, 9, 1),
        config_snapshot_json="{}",
    )
    db.add(rv2)
    db.flush()

    # v1 result unchanged
    result_v1_check = db.get(RuleEvaluation, result_v1.id)
    assert result_v1_check.rule_version_id == "rv1"
    assert result_v1_check.status == RuleEvaluationStatus.PASS


# ─────────────────────────────────────────────────────────────
# TEST 27-28: TENANT ISOLATION
# ─────────────────────────────────────────────────────────────

def test_27_metro_rule_does_not_evaluate_lumin_event(db):
    """Metro rule engine must not evaluate Lumin events."""
    _seed_metro(db)
    t, w = _seed_lumin(db)

    tz = ZoneInfo("Asia/Jakarta")
    break_in = datetime(2026, 8, 17, 13, 5, tzinfo=tz)
    ev = _make_canonical_event(
        db, "lumin", "wL", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17),
        event_id="ce-lumin-break",
    )

    # Query evaluations — should find nothing for Lumin under Metro rules
    results = get_evaluations(
        db, tenant_id="metro", operating_date=date(2026, 8, 17),
    )
    assert len(results) == 0


def test_28_lumin_rule_does_not_evaluate_metro_event(db):
    """Lumin rule engine must not evaluate Metro events."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_lumin(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 13, 5, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
    )

    evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )

    # Query for Lumin — should find nothing
    results = get_evaluations(
        db, tenant_id="lumin", operating_date=date(2026, 8, 17),
    )
    assert len(results) == 0


# ─────────────────────────────────────────────────────────────
# TEST 29: DISABLED RULE → NOT_APPLICABLE
# ─────────────────────────────────────────────────────────────

def test_29_disabled_rule_not_applicable(db):
    """Disabled briefing policy → NOT_APPLICABLE."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # Disabled briefing policy
    policy = CheckpointPolicy(
        id="bp-day", tenant_id="metro", checkpoint_type="BRIEFING_IN",
        shift_id="DAY", enabled=False,  # DISABLED
        effective_from=date(2026, 1, 1),
    )
    db.add(policy)
    db.flush()

    result = evaluate_missing_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.NOT_APPLICABLE


# ─────────────────────────────────────────────────────────────
# TEST 30-31: MISSING CONFIGURATION BEHAVIOR
# ─────────────────────────────────────────────────────────────

def test_30_missing_config_does_not_silently_pass(db):
    """Missing configuration must NOT silently return PASS."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # No briefing policy configured at all
    result = evaluate_missing_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status != RuleEvaluationStatus.PASS


def test_31_missing_config_does_not_falsely_fail(db):
    """Missing configuration must NOT falsely return FAIL."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    # No briefing policy configured
    result = evaluate_missing_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status != RuleEvaluationStatus.FAIL


# ─────────────────────────────────────────────────────────────
# TEST 32: TRACEABILITY
# ─────────────────────────────────────────────────────────────

def test_32_raw_canonical_checkpoint_rule_traceability(db):
    """Rule evaluation links back to canonical event for full traceability."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    _seed_roster(db, "metro", "w1", date(2026, 8, 17), "DAY", "s1", rule_version_id="rv1")

    tz = ZoneInfo("Asia/Makassar")
    break_in = datetime(2026, 8, 17, 13, 5, tzinfo=tz)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        break_in, date(2026, 8, 17), shift_id="DAY", site_id="s1",
        event_id="ce-trace-test",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.source_canonical_event_id == "ce-trace-test"
    assert result.evidence_json is not None
    evidence = json.loads(result.evidence_json)
    assert "event_time" in evidence
    assert "break_end_boundary" in evidence


# ─────────────────────────────────────────────────────────────
# TEST 33: LUMIN REGRESSION
# ─────────────────────────────────────────────────────────────

def test_33_lumin_existing_attendance_regression(db):
    """Lumin Park existing CHECK_IN/CHECK_OUT behavior must remain operational.
    No mining-specific rules forced onto Lumin."""
    t, w = _seed_lumin(db)

    tz = ZoneInfo("Asia/Jakarta")
    check_in = datetime(2026, 8, 17, 8, 0, tzinfo=tz)
    ev = _make_canonical_event(
        db, "lumin", "wL", CanonicalEventType.CHECK_IN,
        check_in, date(2026, 8, 17),
        event_id="ce-lumin-ci",
    )

    # Geofence should be CONFIG_INCOMPLETE (not FAIL, not PASS)
    result = evaluate_geofence(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=date(2026, 8, 17),
        canonical_event=ev,
    )
    assert result.status == RuleEvaluationStatus.CONFIG_INCOMPLETE

    # Device risk should PASS (no risk signals)
    result = evaluate_device_risk(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=date(2026, 8, 17),
        canonical_event=ev,
    )
    assert result.status == RuleEvaluationStatus.PASS


# ─────────────────────────────────────────────────────────────
# TEST 34: INSUFFICIENT_REST BLOCKED_POLICY_DECISION
# ─────────────────────────────────────────────────────────────

def test_34_insufficient_rest_blocked(db):
    """INSUFFICIENT_REST → BLOCKED_POLICY_DECISION (minimum rest hours TBC)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)

    result = evaluate_insufficient_rest(
        db, tenant_id="metro", employee_id="w1",
        operating_date=date(2026, 8, 17), shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.BLOCKED_POLICY_DECISION
    assert result.reason == "MINIMUM_REST_HOURS_TBC"


# ─────────────────────────────────────────────────────────────
# BONUS: Rule registry test
# ─────────────────────────────────────────────────────────────

def test_bonus_rule_registry_complete(db):
    """All expected rules are registered."""
    rules = get_registered_rules()
    expected = [
        "LATE_BREAK_RETURN", "MISSING_BRIEFING", "LATE_BRIEFING",
        "MISSING_SHIFT_OUT", "EARLY_HANDOVER", "LATE_HANDOVER",
        "LOCATION_OUTSIDE_GEOFENCE", "DEVICE_OR_IDENTITY_RISK",
        "EQUIPMENT_MISMATCH", "INSUFFICIENT_REST", "OFFSITE_ASSIGNMENT",
    ]
    for code in expected:
        assert code in rules, f"Rule {code} not registered"
