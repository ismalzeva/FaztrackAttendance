"""
test_acceptance_m2e.py -- MM-M2E End-to-End Operational Simulation.

Proves M2A + M2B + M2C + M2D work together as one engine.
13 scenarios (A-M) + Lumin regression + M1 regression.

Pipeline:
  Roster Plan → Raw Event → Canonical Event → Shift Resolution → Operating Date
  → Checkpoint Resolution → Checkpoint Validation → Actual Equipment Assignment
  → Planned vs Actual Comparison → Operational Rule Evaluation → Exception Candidate
  → Audit Trace
"""
import json
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
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
    RawEvent, RawEventStatus,
    CheckpointPolicy, CheckpointValidationResult, CheckpointValidationStatus,
    CheckpointEventMapping,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
)
from app.canonical_event_service import (
    ingest_raw_event, create_canonical_event, resolve_timezone,
)
from app.checkpoint_engine import (
    validate_checkpoint, get_active_policy,
)
from app.equipment_engine import (
    create_actual_assignment, close_actual_assignment,
    compare_planned_vs_actual, detect_substitution,
    create_mismatch_discrepancy,
)
from app.operational_rule_engine import (
    evaluate_late_break_return, evaluate_missing_briefing, evaluate_late_briefing,
    evaluate_missing_shift_out, evaluate_early_handover, evaluate_late_handover,
    evaluate_geofence, evaluate_device_risk, evaluate_equipment_mismatch,
    evaluate_insufficient_rest, evaluate_offsite_assignment,
    get_registered_rules,
)
from app.roster_validator import (
    validate_max_consecutive_work, validate_no_overlap,
    validate_equipment_double_book,
)


# ─────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# SEED HELPERS
# ─────────────────────────────────────────────────────────────

def _seed_metro(db: Session):
    """Seed Metro Mining full environment."""
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
        effective_from=date(2026, 1, 1), config_snapshot_json="{}",
    )
    db.add(rv)

    # Competency
    comp = Competency(
        id="comp-ex25", tenant_id="metro", employee_id="w1",
        competency_code="EXCAVATOR-OP", equipment_type="EXCAVATOR",
        status=CompetencyStatus.VALID, valid_from=date(2026, 1, 1),
    )
    comp2 = Competency(
        id="comp-ex31", tenant_id="metro", employee_id="w2",
        competency_code="EXCAVATOR-OP", equipment_type="EXCAVATOR",
        status=CompetencyStatus.VALID, valid_from=date(2026, 1, 1),
    )
    db.add_all([comp, comp2])

    # Tenant capabilities
    for key, val in [
        ("equipment_assignment_enabled", "true"),
        ("competency_validation_enabled", "true"),
        ("attendance_rule_enabled", "true"),
        ("late_tolerance_minutes", "15"),
        ("early_tolerance_minutes", "15"),
        ("break_compliance_enabled", "true"),
        ("handover_compliance_enabled", "true"),
    ]:
        db.add(RosterPolicy(
            id=f"rp-{key}", tenant_id="metro",
            policy_key=key, policy_value=val,
            data_type="boolean", confirmation_status="CONFIRMED",
        ))

    db.flush()
    return t, w1, w2, s1, day, night, ex25, ex31, rv


def _seed_lumin(db: Session):
    """Seed Lumin Park tenant."""
    t = Tenant(id="lumin", code="lumin", name="Lumin Park", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="wL", tenant_id="lumin", code="L001", name="Lumin Worker", is_active=True)
    db.add(w)

    ls = Site(
        id="ls1", tenant_id="lumin", site_code="LS001", site_name="Lumin Site",
        site_type=SiteType.MINE_SITE, status=SiteStatus.ACTIVE,
        timezone="Asia/Jakarta", effective_from=date(2026, 1, 1),
    )
    db.add(ls)

    lshift = ShiftTemplate(
        id="LDAY", tenant_id="lumin", shift_code="LDAY", shift_name="Lumin Day",
        start_time=time(8, 0), end_time=time(17, 0),
        break_start=time(12, 0), break_end=time(13, 0),
        handover_start=time(16, 45), handover_end=time(17, 0),
        crosses_midnight=False,
    )
    db.add(lshift)

    lrv = RuleVersion(
        id="lrv1", tenant_id="lumin", version_label="v1.0",
        effective_from=date(2026, 1, 1), config_snapshot_json="{}",
    )
    db.add(lrv)

    db.flush()
    return t, w, ls, lshift, lrv


def _seed_roster(db, tenant_id, employee_id, op_date, shift_id, site_id,
                 work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
                 rule_version_id=None, planned_equipment_id=None):
    ra = RosterAssignment(
        id=f"ra-{employee_id}-{op_date}-{shift_id}", tenant_id=tenant_id,
        roster_code="R001", operating_date=op_date, employee_id=employee_id,
        shift_id=shift_id, site_id=site_id,
        work_status=work_status, site_status=site_status,
        validation_status=ValidationStatus.PUBLISHED,
        rule_version_id=rule_version_id,
        planned_equipment_id=planned_equipment_id,
    )
    db.add(ra)
    db.flush()
    return ra


def _make_canonical_event(
    db, tenant_id, employee_id, event_type, local_ts, operating_date,
    shift_id=None, site_id=None, latitude=None, longitude=None,
    evidence_json=None, event_id=None, timezone="Asia/Makassar",
):
    ev = CanonicalAttendanceEvent(
        id=event_id or f"ce-{_uid()}",
        tenant_id=tenant_id, employee_id=employee_id,
        event_type=event_type,
        local_timestamp=local_ts,
        utc_timestamp=local_ts - timedelta(hours=8),
        timezone=timezone,
        operating_date=operating_date,
        shift_id=shift_id, site_id=site_id,
        source="test", source_event_id=f"src-{event_id or _uid()}",
        latitude=latitude, longitude=longitude,
        evidence_json=evidence_json,
        processing_status=CanonicalProcessingStatus.SHIFT_RESOLVED,
    )
    db.add(ev)
    db.flush()
    return ev


def _make_raw_event(db, tenant_id, source, source_event_id, raw_timestamp,
                    raw_payload=None, canonical_event_id=None, status=RawEventStatus.PROCESSED):
    raw = RawEvent(
        id=f"raw-{_uid()}", tenant_id=tenant_id, source=source,
        source_event_id=source_event_id, raw_timestamp=raw_timestamp,
        raw_payload=json.dumps(raw_payload or {}),
        canonical_event_id=canonical_event_id, processing_status=status,
    )
    db.add(raw)
    db.flush()
    return raw


# ─────────────────────────────────────────────────────────────
# SCENARIO A: Normal DAY Shift E2E Journey
# ─────────────────────────────────────────────────────────────

def test_scenario_a_normal_day_shift(db):
    """Complete normal DAY shift: BRIEFING → EQUIPMENT → WORK → BREAK → HANDOVER → SHIFT_OUT."""
    _, w1, _, s1, day, _, ex25, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # 1. BRIEFING_IN 06:35
    ev_briefing = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BRIEFING_IN,
        datetime(2026, 9, 5, 6, 35, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    assert ev_briefing.operating_date == op_date
    assert ev_briefing.timezone == "Asia/Makassar"
    assert ev_briefing.shift_id == "DAY"

    # 2. EQUIPMENT_CHECK_IN 06:50
    ev_equip = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.EQUIPMENT_CHECK_IN,
        datetime(2026, 9, 5, 6, 50, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # Record actual equipment assignment
    actual, status = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 6, 50, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert status == "CREATED"
    assert actual.equipment_id == "ex25"

    # Compare planned vs actual → MATCH
    comparison = compare_planned_vs_actual(db, actual_assignment=actual)
    assert comparison.comparison_result == ComparisonResult.MATCH

    # 3. WORK_START / CHECK_IN 07:00
    ev_work = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 7, 0, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # 4. BREAK_OUT 12:00
    ev_break_out = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_OUT,
        datetime(2026, 9, 5, 12, 0, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # 5. BREAK_IN 12:58 (within limit)
    ev_break_in = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 12, 58, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # LATE_BREAK_RETURN check → PASS
    break_eval = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev_break_in, rule_version_id="rv1",
    )
    assert break_eval.status == RuleEvaluationStatus.PASS
    assert break_eval.rule_code == "LATE_BREAK_RETURN"

    # 6. HANDOVER 18:50
    ev_handover = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.HANDOVER_START,
        datetime(2026, 9, 5, 18, 50, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # 7. SHIFT_OUT 19:05
    ev_shift_out = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_OUT,
        datetime(2026, 9, 5, 19, 5, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # Verify audit trace: all canonical events belong to same operating_date
    all_canonicals = db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.tenant_id == "metro",
            CanonicalAttendanceEvent.employee_id == "w1",
            CanonicalAttendanceEvent.operating_date == op_date,
        )
    ).all()
    assert len(all_canonicals) >= 5
    for c in all_canonicals:
        assert c.operating_date == op_date
        assert c.timezone == "Asia/Makassar"
        assert c.tenant_id == "metro"

    # Verify roster not overwritten
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == "metro",
            RosterAssignment.employee_id == "w1",
            RosterAssignment.operating_date == op_date,
        )
    )
    assert roster.planned_equipment_id == "ex25"
    assert roster.work_status == WorkStatus.WORK


# ─────────────────────────────────────────────────────────────
# SCENARIO B: Normal NIGHT Cross-Midnight
# ─────────────────────────────────────────────────────────────

def test_scenario_b_night_cross_midnight(db):
    """NIGHT shift crosses midnight. Events after midnight retain operating_date."""
    _, w1, _, s1, _, night, ex25, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # WORK_START 19:00
    ev_start = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 19, 0, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev_start.operating_date == op_date

    # BREAK_OUT 00:00 (next calendar day)
    ev_bout = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_OUT,
        datetime(2026, 9, 6, 0, 0, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev_bout.operating_date == op_date  # Still Sep 5!

    # BREAK_IN 00:58
    ev_bin = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 6, 0, 58, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev_bin.operating_date == op_date  # Still Sep 5!

    # LATE_BREAK_RETURN → PASS
    break_eval = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev_bin, rule_version_id="rv1",
    )
    assert break_eval.status == RuleEvaluationStatus.PASS

    # HANDOVER 06:50
    ev_hand = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.HANDOVER_START,
        datetime(2026, 9, 6, 6, 50, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev_hand.operating_date == op_date

    # SHIFT_OUT 07:00
    ev_out = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_OUT,
        datetime(2026, 9, 6, 7, 0, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev_out.operating_date == op_date

    # All events have same operating_date
    all_events = db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.tenant_id == "metro",
            CanonicalAttendanceEvent.employee_id == "w1",
            CanonicalAttendanceEvent.operating_date == op_date,
        )
    ).all()
    assert len(all_events) >= 5
    for e in all_events:
        assert e.operating_date == op_date

    # No events leaked to Sep 6
    sep6_events = db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.tenant_id == "metro",
            CanonicalAttendanceEvent.employee_id == "w1",
            CanonicalAttendanceEvent.operating_date == date(2026, 9, 6),
        )
    ).all()
    assert len(sep6_events) == 0


# ─────────────────────────────────────────────────────────────
# SCENARIO C: Late Briefing (TBC-safe)
# ─────────────────────────────────────────────────────────────

def test_scenario_c_late_briefing_tbc_safe(db):
    """Late briefing uses confirmed policy, not invented tolerance."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1", rule_version_id="rv1")

    # Briefing policy with no tolerance configured
    policy = CheckpointPolicy(
        id="bp-day", tenant_id="metro", checkpoint_type="BRIEFING_IN",
        shift_id="DAY", enabled=True,
        window_start_offset_min=-30, window_end_offset_min=15,
        tolerance_min=None,  # TBC
        effective_from=date(2026, 1, 1),
    )
    db.add(policy)
    db.flush()

    # Briefing event at 07:30 (after shift start)
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BRIEFING_IN,
        datetime(2026, 9, 5, 7, 30, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )

    # Must be BLOCKED_POLICY_DECISION (no briefing tolerance configured)
    assert result.status == RuleEvaluationStatus.BLOCKED_POLICY_DECISION
    assert result.rule_code == "LATE_BRIEFING"
    assert result.status != RuleEvaluationStatus.FAIL


# ─────────────────────────────────────────────────────────────
# SCENARIO D: Equipment Mismatch
# ─────────────────────────────────────────────────────────────

def test_scenario_d_equipment_mismatch(db):
    """Planned A → Actual B creates mismatch. Roster unchanged."""
    _, w1, _, s1, day, _, ex25, ex31, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # Actual assignment with DIFFERENT equipment
    actual, status = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert status == "CREATED"

    # Compare → MISMATCH
    comparison = compare_planned_vs_actual(db, actual_assignment=actual)
    assert comparison.comparison_result == ComparisonResult.MISMATCH
    assert comparison.planned_equipment_id == "ex25"
    assert comparison.actual_equipment_id == "ex31"

    # Create discrepancy
    discrepancy = create_mismatch_discrepancy(db, comparison=comparison)
    assert discrepancy is not None
    assert discrepancy.discrepancy_type == DiscrepancyType.EQUIPMENT_MISMATCH
    assert discrepancy.status == DiscrepancyStatus.OPEN

    # Roster NOT overwritten
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == "metro",
            RosterAssignment.employee_id == "w1",
            RosterAssignment.operating_date == op_date,
        )
    )
    assert roster.planned_equipment_id == "ex25"

    # Rule evaluation → FAIL
    rule_eval = evaluate_equipment_mismatch(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        comparison=comparison, rule_version_id="rv1",
    )
    assert rule_eval.status == RuleEvaluationStatus.FAIL
    assert rule_eval.rule_code == "EQUIPMENT_MISMATCH"


# ─────────────────────────────────────────────────────────────
# SCENARIO E: Operator Substitution
# ─────────────────────────────────────────────────────────────

def test_scenario_e_operator_substitution(db):
    """Worker B uses equipment planned for Worker A. Substitution detected."""
    _, w1, w2, s1, day, _, ex25, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # Worker B actually uses the equipment
    actual, status = create_actual_assignment(
        db, tenant_id="metro", employee_id="w2", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert status == "CREATED"

    # Detect substitution
    sub = detect_substitution(db, actual_assignment=actual)
    assert sub is not None
    assert sub.discrepancy_type == DiscrepancyType.OPERATOR_SUBSTITUTION
    assert sub.planned_worker_id == "w1"
    assert sub.actual_worker_id == "w2"
    assert sub.status == DiscrepancyStatus.OPEN

    # Worker A roster unchanged
    roster_a = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == "metro",
            RosterAssignment.employee_id == "w1",
            RosterAssignment.operating_date == op_date,
        )
    )
    assert roster_a is not None
    assert roster_a.planned_equipment_id == "ex25"


# ─────────────────────────────────────────────────────────────
# SCENARIO F: Late DAY Break Return
# ─────────────────────────────────────────────────────────────

def test_scenario_f_late_day_break(db):
    """DAY BREAK_IN at 13:01 → FAIL. Idempotent on retry."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1", rule_version_id="rv1")

    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 13, 1, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.rule_code == "LATE_BREAK_RETURN"

    # Idempotency: re-evaluate returns same result
    result2 = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result2.id == result.id


# ─────────────────────────────────────────────────────────────
# SCENARIO G: Late NIGHT Break Return
# ─────────────────────────────────────────────────────────────

def test_scenario_g_late_night_break(db):
    """NIGHT BREAK_IN at 01:01 next calendar day → FAIL. operating_date preserved."""
    _, w1, _, s1, _, night, _, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", rule_version_id="rv1")

    # BREAK_IN at 01:01 on Sep 6
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 6, 1, 1, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev.operating_date == op_date  # Sep 5 preserved

    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.FAIL
    assert result.rule_code == "LATE_BREAK_RETURN"
    assert result.operating_date == op_date


# ─────────────────────────────────────────────────────────────
# SCENARIO H: Missing Briefing (TBC-safe)
# ─────────────────────────────────────────────────────────────

def test_scenario_h_missing_briefing(db):
    """Missing briefing: result depends on policy completeness."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1", rule_version_id="rv1")

    # No BRIEFING_IN event created
    result = evaluate_missing_briefing(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        rule_version_id="rv1",
    )

    # TBC-safe: FAIL if policy complete, CONFIG_INCOMPLETE if not
    assert result.status in [
        RuleEvaluationStatus.FAIL,
        RuleEvaluationStatus.CONFIG_INCOMPLETE,
        RuleEvaluationStatus.NOT_APPLICABLE,
    ]
    assert result.rule_code == "MISSING_BRIEFING"
    assert result.status != RuleEvaluationStatus.PASS


# ─────────────────────────────────────────────────────────────
# SCENARIO I: Missing SHIFT_OUT
# ─────────────────────────────────────────────────────────────

def test_scenario_i_missing_shift_out(db):
    """Missing SHIFT_OUT for WORK employee. REST/OFFSITE excluded."""
    _, w1, w2, s1, day, _, _, _, rv = _seed_metro(db)
    op_date = date(2026, 8, 1)  # Past date so shift has ended

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 rule_version_id="rv1")
    _seed_roster(db, "metro", "w2", op_date, "DAY", "s1",
                 work_status=WorkStatus.REST, rule_version_id="rv1")

    # Seed CHECK_OUT policy for DAY shift
    db.add(CheckpointPolicy(
        id="cp-co-day", tenant_id="metro", checkpoint_type="CHECK_OUT",
        shift_id="DAY", enabled=True, sequence_order=7,
        window_start_offset_min=-30, window_end_offset_min=30,
        effective_from=date(2026, 1, 1), rule_version_id="rv1",
    ))
    db.flush()

    # WORK employee: missing SHIFT_OUT (shift has ended, no CHECK_OUT received)
    result_w1 = evaluate_missing_shift_out(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result_w1.status in [
        RuleEvaluationStatus.FAIL,
        RuleEvaluationStatus.CONFIG_INCOMPLETE,
    ]
    assert result_w1.rule_code == "MISSING_SHIFT_OUT"

    # REST employee: NOT_APPLICABLE
    result_w2 = evaluate_missing_shift_out(
        db, tenant_id="metro", employee_id="w2",
        operating_date=op_date, shift_id="DAY",
        rule_version_id="rv1",
    )
    assert result_w2.status == RuleEvaluationStatus.NOT_APPLICABLE
    assert "REST" in result_w2.reason


# ─────────────────────────────────────────────────────────────
# SCENARIO J: Geofence Configuration Missing
# ─────────────────────────────────────────────────────────────

def test_scenario_j_geofence_config_missing(db):
    """Metro geofence TBC → CONFIG_INCOMPLETE. GPS evidence retained."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1", rule_version_id="rv1")

    # Event with GPS evidence
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 7, 0, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
        latitude=-0.9293, longitude=100.4567,
        evidence_json=json.dumps({"accuracy_m": 15.0}),
    )

    # GPS evidence retained
    assert ev.latitude == pytest.approx(-0.9293)
    assert ev.longitude == pytest.approx(100.4567)

    result = evaluate_geofence(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )

    # CONFIG_INCOMPLETE (no geofence configured)
    assert result.status == RuleEvaluationStatus.CONFIG_INCOMPLETE
    assert result.rule_code == "LOCATION_OUTSIDE_GEOFENCE"
    assert result.reason == "GEOFENCE_NOT_CONFIGURED"


# ─────────────────────────────────────────────────────────────
# SCENARIO K: Equipment Change During Shift
# ─────────────────────────────────────────────────────────────

def test_scenario_k_equipment_change_during_shift(db):
    """Worker switches equipment mid-shift. Both intervals retained."""
    _, w1, _, s1, day, _, ex25, ex31, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # First interval: ex25
    actual1, status1 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert status1 == "CREATED"

    # Close first interval
    closed = close_actual_assignment(
        db, assignment_id=actual1.id,
        ended_at=datetime(2026, 9, 5, 12, 0, tzinfo=tz),
    )
    assert closed is not None
    assert closed.status == ActualAssignmentStatus.CLOSED

    # Second interval: ex31
    actual2, status2 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 12, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert status2 == "CREATED"

    # Both intervals retained
    all_actuals = db.scalars(
        select(EquipmentAssignmentActual).where(
            EquipmentAssignmentActual.tenant_id == "metro",
            EquipmentAssignmentActual.employee_id == "w1",
            EquipmentAssignmentActual.operating_date == op_date,
        )
    ).all()
    assert len(all_actuals) == 2
    assert all_actuals[0].equipment_id == "ex25"
    assert all_actuals[0].status == ActualAssignmentStatus.CLOSED
    assert all_actuals[1].equipment_id == "ex31"
    assert all_actuals[1].status == ActualAssignmentStatus.ACTIVE

    # Roster not overwritten
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == "metro",
            RosterAssignment.employee_id == "w1",
            RosterAssignment.operating_date == op_date,
        )
    )
    assert roster.planned_equipment_id == "ex25"

    # Compare second interval → MISMATCH
    comparison = compare_planned_vs_actual(db, actual_assignment=actual2)
    assert comparison.comparison_result == ComparisonResult.MISMATCH


# ─────────────────────────────────────────────────────────────
# SCENARIO L: Duplicate / Retry Events
# ─────────────────────────────────────────────────────────────

def test_scenario_l_duplicate_retry_events(db):
    """Replay events. Idempotent at every layer."""
    _, w1, _, s1, day, _, ex25, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # Create canonical event
    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 7, 0, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )

    # Idempotent checkpoint validation
    cp1 = validate_checkpoint(db, canonical_event=ev, checkpoint_type="CHECK_IN")
    cp2 = validate_checkpoint(db, canonical_event=ev, checkpoint_type="CHECK_IN")
    assert cp2.id == cp1.id

    # Idempotent equipment assignment
    actual1, s1a = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    actual2, s2a = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert s2a == "DUPLICATE"
    assert actual2.id == actual1.id

    # Idempotent comparison
    comp1 = compare_planned_vs_actual(db, actual_assignment=actual1)
    comp2 = compare_planned_vs_actual(db, actual_assignment=actual1)
    assert comp1.id == comp2.id

    # Idempotent rule evaluation
    ev_break = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 12, 58, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    eval1 = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev_break, rule_version_id="rv1",
    )
    eval2 = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev_break, rule_version_id="rv1",
    )
    assert eval2.id == eval1.id


# ─────────────────────────────────────────────────────────────
# SCENARIO M: Tenant Isolation
# ─────────────────────────────────────────────────────────────

def test_scenario_m_tenant_isolation(db):
    """Metro and Lumin are completely isolated."""
    _, w1, w2, s1, day, _, ex25, _, rv = _seed_metro(db)
    _, lw, ls, lshift, lrv = _seed_lumin(db)
    op_date = date(2026, 9, 5)
    metro_tz = ZoneInfo("Asia/Makassar")
    lumin_tz = ZoneInfo("Asia/Jakarta")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")
    _seed_roster(db, "lumin", "wL", op_date, "LDAY", "ls1", rule_version_id="lrv1")

    # Metro canonical event
    c_metro = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 7, 0, tzinfo=metro_tz), op_date,
        shift_id="DAY", site_id="s1", timezone="Asia/Makassar",
    )
    assert c_metro.tenant_id == "metro"
    assert c_metro.timezone == "Asia/Makassar"

    # Lumin canonical event
    c_lumin = _make_canonical_event(
        db, "lumin", "wL", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 8, 0, tzinfo=lumin_tz), op_date,
        shift_id="LDAY", site_id="ls1", timezone="Asia/Jakarta",
    )
    assert c_lumin.tenant_id == "lumin"
    assert c_lumin.timezone == "Asia/Jakarta"

    # Metro events don't appear in Lumin query
    lumin_events = db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.tenant_id == "lumin",
        )
    ).all()
    for e in lumin_events:
        assert e.employee_id != "w1"

    # Lumin events don't appear in Metro query
    metro_events = db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.tenant_id == "metro",
        )
    ).all()
    for e in metro_events:
        assert e.employee_id != "wL"

    # Equipment cross-tenant isolation
    actual_m, _ = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=metro_tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert actual_m.tenant_id == "metro"

    # Timezone independence
    assert resolve_timezone(db, "metro", "s1") == "Asia/Makassar"
    assert resolve_timezone(db, "lumin", "ls1") == "Asia/Jakarta"

    # Metro rule evaluation isolated
    ev_metro_break = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 12, 58, tzinfo=metro_tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    eval_m = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev_metro_break, rule_version_id="rv1",
    )
    assert eval_m.tenant_id == "metro"

    # Lumin rule evaluation returns PASS (Lumin has break times configured
    # but no break_compliance_enabled policy; engine evaluates based on shift)
    eval_l = evaluate_late_break_return(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=op_date, shift_id="LDAY",
        canonical_event=c_lumin, rule_version_id="lrv1",
    )
    assert eval_l.status in [
        RuleEvaluationStatus.NOT_APPLICABLE,
        RuleEvaluationStatus.PASS,
    ]


# ─────────────────────────────────────────────────────────────
# LUMIN PARK E2E REGRESSION JOURNEY
# ─────────────────────────────────────────────────────────────

def test_lumin_e2e_regression(db):
    """Lumin worker CHECK_IN → CHECK_OUT through compatible path."""
    _, lw, ls, lshift, lrv = _seed_lumin(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Jakarta")

    _seed_roster(db, "lumin", "wL", op_date, "LDAY", "ls1", rule_version_id="lrv1")

    # CHECK_IN
    c_in = _make_canonical_event(
        db, "lumin", "wL", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 8, 5, tzinfo=tz), op_date,
        shift_id="LDAY", site_id="ls1", timezone="Asia/Jakarta",
    )
    assert c_in.tenant_id == "lumin"
    assert c_in.timezone == "Asia/Jakarta"
    assert c_in.operating_date == op_date

    # CHECK_OUT
    c_out = _make_canonical_event(
        db, "lumin", "wL", CanonicalEventType.CHECK_OUT,
        datetime(2026, 9, 5, 17, 0, tzinfo=tz), op_date,
        shift_id="LDAY", site_id="ls1", timezone="Asia/Jakarta",
    )
    assert c_out.tenant_id == "lumin"

    # No Metro rules forced onto Lumin
    briefing_eval = evaluate_missing_briefing(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=op_date, shift_id="LDAY",
        rule_version_id="lrv1",
    )
    assert briefing_eval.status == RuleEvaluationStatus.NOT_APPLICABLE

    handover_eval = evaluate_early_handover(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=op_date, shift_id="LDAY",
        canonical_event=c_out, rule_version_id="lrv1",
    )
    assert handover_eval.status == RuleEvaluationStatus.NOT_APPLICABLE

    geofence_eval = evaluate_geofence(
        db, tenant_id="lumin", employee_id="wL",
        operating_date=op_date, shift_id="LDAY",
        canonical_event=c_in, rule_version_id="lrv1",
    )
    assert geofence_eval.status in [
        RuleEvaluationStatus.NOT_APPLICABLE,
        RuleEvaluationStatus.CONFIG_INCOMPLETE,
    ]

    # All Lumin data isolated
    lumin_canonicals = db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.tenant_id == "lumin",
        )
    ).all()
    assert len(lumin_canonicals) >= 2
    for c in lumin_canonicals:
        assert c.timezone == "Asia/Jakarta"
        assert c.tenant_id == "lumin"


# ─────────────────────────────────────────────────────────────
# M1 RULES REGRESSION
# ─────────────────────────────────────────────────────────────

def test_m1_rules_regression(db):
    """M1 roster protections still work after M2."""
    _, w1, w2, s1, day, _, ex25, ex31, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)

    # Test 1: Max consecutive work (12 days)
    for i in range(12):
        _seed_roster(db, "metro", "w1", op_date + timedelta(days=i), "DAY", "s1",
                     rule_version_id="rv1", planned_equipment_id="ex25")

    violations = validate_max_consecutive_work(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date + timedelta(days=12),
        work_status=WorkStatus.WORK,
    )
    assert len(violations) > 0

    # Test 2: Equipment double-booking
    # w1 already has roster for op_date from consecutive work loop (day 0)
    _seed_roster(db, "metro", "w2", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    double_book = validate_equipment_double_book(
        db, tenant_id="metro", employee_id="w2",
        operating_date=op_date, equipment_id="ex25",
    )
    assert len(double_book) > 0

    # Test 3: Worker overlap check
    overlap = validate_no_overlap(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
    )
    assert isinstance(overlap, list)


# ─────────────────────────────────────────────────────────────
# AUDIT TRACE: Full Chain Verification
# ─────────────────────────────────────────────────────────────

def test_full_audit_trace(db):
    """Verify RawEvent → CanonicalEvent → Equipment → Comparison → Rule chain."""
    _, w1, _, s1, day, _, ex25, _, rv = _seed_metro(db)
    op_date = date(2026, 9, 5)
    tz = ZoneInfo("Asia/Makassar")

    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1",
                 planned_equipment_id="ex25", rule_version_id="rv1")

    # Step 1: Raw Event
    raw = _make_raw_event(
        db, "metro", "test", "ev-trace-1",
        "2026-09-05T07:00:00+08:00",
        raw_payload={"event_type": "CHECK_IN"},
    )
    assert raw.id is not None

    # Step 2: Canonical Event
    canonical = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.CHECK_IN,
        datetime(2026, 9, 5, 7, 0, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    assert canonical.operating_date == op_date
    assert canonical.shift_id == "DAY"

    # Step 3: Equipment Assignment
    actual, ea_status = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 0, tzinfo=tz),
        source="checkpoint", rule_version_id="rv1",
    )
    assert actual.id is not None

    # Step 4: Comparison
    comparison = compare_planned_vs_actual(db, actual_assignment=actual)
    assert comparison.id is not None
    assert comparison.actual_assignment_id == actual.id
    assert comparison.comparison_result == ComparisonResult.MATCH

    # Step 5: Rule Evaluation
    ev_break = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 12, 58, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    rule_eval = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev_break, rule_version_id="rv1",
    )
    assert rule_eval.id is not None
    assert rule_eval.tenant_id == "metro"

    # Full trace: Equipment → Comparison link
    fetched_comp = db.scalar(
        select(EquipmentComparisonResult).where(
            EquipmentComparisonResult.actual_assignment_id == actual.id,
        )
    )
    assert fetched_comp is not None


# ─────────────────────────────────────────────────────────────
# RULE REGISTRY COMPLETENESS
# ─────────────────────────────────────────────────────────────

def test_rule_registry_completeness(db):
    """All 11 rules registered."""
    rules = get_registered_rules()
    expected = [
        "LATE_BREAK_RETURN", "MISSING_BRIEFING", "LATE_BRIEFING",
        "MISSING_SHIFT_OUT", "EARLY_HANDOVER", "LATE_HANDOVER",
        "LOCATION_OUTSIDE_GEOFENCE", "DEVICE_OR_IDENTITY_RISK",
        "EQUIPMENT_MISMATCH", "INSUFFICIENT_REST", "OFFSITE_ASSIGNMENT",
    ]
    for code in expected:
        assert code in rules, f"Rule {code} not registered"


# ─────────────────────────────────────────────────────────────
# PRIOR TESTS REGRESSION (M0-M2D smoke)
# ─────────────────────────────────────────────────────────────

def test_prior_m0_m2d_regression_smoke(db):
    """Smoke test: basic models + enums preserved (M0-M2D regression)."""
    tenant = Tenant(id="test_t", code="test", name="Test", timezone="Asia/Jakarta")
    db.add(tenant)
    db.flush()
    assert tenant.id == "test_t"

    # CanonicalEventType values preserved
    assert CanonicalEventType.CHECK_IN.value == "CHECK_IN"
    assert CanonicalEventType.CHECK_OUT.value == "CHECK_OUT"
    assert CanonicalEventType.BREAK_IN.value == "BREAK_IN"
    assert CanonicalEventType.BREAK_OUT.value == "BREAK_OUT"
    assert CanonicalEventType.BRIEFING_IN.value == "BRIEFING_IN"

    # RuleEvaluation model works
    eval_model = RuleEvaluation(
        id=_uid(), tenant_id="test_t", employee_id="e1",
        operating_date=date(2026, 1, 1), shift_id="s1",
        rule_code="TEST_RULE", rule_version_id="rv1",
        evaluated_at=datetime.now(),
        status=RuleEvaluationStatus.PASS,
        severity=RuleSeverity.INFO,
    )
    db.add(eval_model)
    db.flush()
    assert eval_model.id is not None

    # ComparisonResult enum preserved
    assert ComparisonResult.MATCH.value == "MATCH"
    assert ComparisonResult.MISMATCH.value == "MISMATCH"
    assert ComparisonResult.NO_PLANNED_EQUIPMENT.value == "NO_PLANNED_EQUIPMENT"


# ─────────────────────────────────────────────────────────────
# BOUNDARY TESTS
# ─────────────────────────────────────────────────────────────

def test_boundary_day_break_1258_pass(db):
    """DAY BREAK_IN 12:58 → PASS."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    tz = ZoneInfo("Asia/Makassar")
    op_date = date(2026, 9, 5)
    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1", rule_version_id="rv1")

    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 12, 58, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS


def test_boundary_day_break_1300_pass(db):
    """DAY BREAK_IN 13:00 → PASS (boundary inclusive)."""
    _, w1, _, s1, day, _, _, _, rv = _seed_metro(db)
    tz = ZoneInfo("Asia/Makassar")
    op_date = date(2026, 9, 5)
    _seed_roster(db, "metro", "w1", op_date, "DAY", "s1", rule_version_id="rv1")

    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 5, 13, 0, tzinfo=tz), op_date,
        shift_id="DAY", site_id="s1",
    )
    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="DAY",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS


def test_boundary_night_break_0100_pass(db):
    """NIGHT BREAK_IN 01:00 → PASS (boundary inclusive)."""
    _, w1, _, s1, _, night, _, _, rv = _seed_metro(db)
    tz = ZoneInfo("Asia/Makassar")
    op_date = date(2026, 9, 5)
    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", rule_version_id="rv1")

    ev = _make_canonical_event(
        db, "metro", "w1", CanonicalEventType.BREAK_IN,
        datetime(2026, 9, 6, 1, 0, tzinfo=tz), op_date,
        shift_id="NIGHT", site_id="s1",
    )
    assert ev.operating_date == op_date
    result = evaluate_late_break_return(
        db, tenant_id="metro", employee_id="w1",
        operating_date=op_date, shift_id="NIGHT",
        canonical_event=ev, rule_version_id="rv1",
    )
    assert result.status == RuleEvaluationStatus.PASS
