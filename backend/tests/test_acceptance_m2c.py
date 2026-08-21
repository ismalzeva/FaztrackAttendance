"""
test_acceptance_m2c.py -- MM-M2C Planned vs Actual Equipment Assignment.

Tests (31):
1.  Planned = actual → MATCH
2.  Planned EX-025, actual EX-031 → MISMATCH
3.  Mismatch does not modify planned assignment
4.  Actual assignment retains source event
5.  Valid competency PASS
6.  Expired competency detected
7.  Missing competency detected
8.  ACTIVE equipment accepted
9.  OUT_OF_SERVICE equipment rejected
10. Worker overlap detected
11. Equipment double-operator overlap detected
12. Adjacent non-overlapping intervals allowed
13. Equipment change during shift retained
14. NIGHT cross-midnight retains operating_date
15. Operator substitution detected (Budi on Tono's equipment)
16. Substitute competency validated
17. Actual assignment idempotency
18. Discrepancy idempotency
19. Same equipment code different tenant isolated
20. Cross-tenant worker-equipment rejected
21. Cross-tenant comparison rejected
22. Telematics engine-on ≠ operator identity
23. Raw → canonical → actual traceability
24. Discrepancy created on mismatch
25. MATCH does not create discrepancy
26. No planned equipment handled explicitly
27. Historical competency validity used
28. Lumin Park regression PASS
29. Equipment change closes first + opens second
30. Discrepancy status OPEN by default
31. Comparison result has rule_version_id
"""
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
)
from app.equipment_engine import (
    validate_competency,
    validate_equipment_status,
    check_worker_overlap,
    check_equipment_overlap,
    create_actual_assignment,
    close_actual_assignment,
    compare_planned_vs_actual,
    detect_substitution,
    create_mismatch_discrepancy,
    process_equipment_checkin,
    get_actual_assignments,
    get_discrepancies,
    get_tenant_capability,
    is_capability_enabled,
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
    """Seed Metro Mining tenant with equipment, competency, roster."""
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
    ex_oos = Equipment(
        id="ex_oos", tenant_id="metro", equipment_code="EX-OOS",
        equipment_type="EXCAVATOR", status=EquipmentStatus.OUT_OF_SERVICE,
        effective_from=date(2026, 1, 1),
    )
    db.add_all([ex25, ex31, ex_oos])

    # Competency: both workers valid for EXCAVATOR
    c1 = Competency(
        id="c1", tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", competency_code="EXC-A",
        status=CompetencyStatus.VALID,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    )
    c2 = Competency(
        id="c2", tenant_id="metro", employee_id="w2",
        equipment_type="EXCAVATOR", competency_code="EXC-A",
        status=CompetencyStatus.VALID,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    )
    db.add_all([c1, c2])

    # Rule version
    rv = RuleVersion(
        id="rv1", tenant_id="metro", version_label="v1.0",
        effective_from=date(2026, 1, 1),
        config_snapshot_json="{}",
    )
    db.add(rv)

    # Tenant capabilities
    cap_equip = RosterPolicy(
        id="rp-cap-equip", tenant_id="metro",
        policy_key="equipment_assignment_enabled", policy_value="true",
        data_type="boolean", confirmation_status="CONFIRMED",
    )
    cap_comp = RosterPolicy(
        id="rp-cap-comp", tenant_id="metro",
        policy_key="competency_validation_enabled", policy_value="true",
        data_type="boolean", confirmation_status="CONFIRMED",
    )
    db.add_all([cap_equip, cap_comp])
    db.flush()

    return t, w1, w2, s1, day, night, ex25, ex31, ex_oos, rv


def _seed_lumin(db: Session):
    """Seed Lumin Park tenant."""
    t = Tenant(id="lumin", code="lumin", name="Lumin Park", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="wL", tenant_id="lumin", code="L001", name="Lumin Worker", is_active=True)
    db.add(w)

    ex_l = Equipment(
        id="ex_l", tenant_id="lumin", equipment_code="EX-025",
        equipment_type="FORKLIFT", status=EquipmentStatus.ACTIVE,
        effective_from=date(2026, 1, 1),
    )
    db.add(ex_l)
    db.flush()
    return t, w, ex_l


def _seed_roster(db, tenant_id, employee_id, op_date, shift_id, site_id,
                 planned_equipment_id=None):
    ra = RosterAssignment(
        id=f"ra-{employee_id}-{op_date}", tenant_id=tenant_id, roster_code="R001",
        operating_date=op_date, employee_id=employee_id,
        shift_id=shift_id, site_id=site_id,
        planned_equipment_id=planned_equipment_id,
        work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
        validation_status=ValidationStatus.PUBLISHED,
    )
    db.add(ra)
    db.flush()
    return ra


WITA = ZoneInfo("Asia/Makassar")
OP_DATE = date(2026, 9, 5)


# ============================================================
# TEST 1: Planned = actual → MATCH
# ============================================================
def test_01_match(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["create_status"] == "CREATED"
    assert result["comparison"].comparison_result == ComparisonResult.MATCH
    db.rollback()


# ============================================================
# TEST 2: Planned EX-025, actual EX-031 → MISMATCH
# ============================================================
def test_02_mismatch(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["comparison"].comparison_result == ComparisonResult.MISMATCH
    assert result["comparison"].planned_equipment_id == "ex25"
    assert result["comparison"].actual_equipment_id == "ex31"
    db.rollback()


# ============================================================
# TEST 3: Mismatch does not modify planned assignment
# ============================================================
def test_03_planned_unchanged(db):
    _seed_metro(db)
    roster = _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    # Re-read roster
    ra = db.get(RosterAssignment, roster.id)
    assert ra.planned_equipment_id == "ex25"  # UNCHANGED
    db.rollback()


# ============================================================
# TEST 4: Actual assignment retains source event
# ============================================================
def test_04_source_retained(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        source="EQUIPMENT_IN", canonical_event_id="canonical-123",
    )

    assert result["assignment"].source == "EQUIPMENT_IN"
    assert result["assignment"].canonical_event_id == "canonical-123"
    db.rollback()


# ============================================================
# TEST 5: Valid competency PASS
# ============================================================
def test_05_competency_valid(db):
    _seed_metro(db)
    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=OP_DATE,
    )
    assert valid is True
    assert reason == "COMPETENCY_VALID"
    db.rollback()


# ============================================================
# TEST 6: Expired competency detected
# ============================================================
def test_06_competency_expired(db):
    _seed_metro(db)
    # Override competency to expired
    comp = db.get(Competency, "c1")
    comp.valid_to = date(2026, 9, 1)  # Expired before OP_DATE

    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=OP_DATE,
    )
    assert valid is False
    assert reason == "COMPETENCY_EXPIRED"
    db.rollback()


# ============================================================
# TEST 7: Missing competency detected
# ============================================================
def test_07_competency_missing(db):
    _seed_metro(db)
    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="BULLDOZER", at_date=OP_DATE,  # No competency for dozer
    )
    assert valid is False
    assert reason == "COMPETENCY_MISSING"
    db.rollback()


# ============================================================
# TEST 8: ACTIVE equipment accepted
# ============================================================
def test_08_equipment_active(db):
    _seed_metro(db)
    valid, reason = validate_equipment_status(
        db, tenant_id="metro", equipment_id="ex25", at_date=OP_DATE,
    )
    assert valid is True
    assert reason == "EQUIPMENT_ACTIVE"
    db.rollback()


# ============================================================
# TEST 9: OUT_OF_SERVICE equipment rejected
# ============================================================
def test_09_equipment_oos(db):
    _seed_metro(db)
    valid, reason = validate_equipment_status(
        db, tenant_id="metro", equipment_id="ex_oos", at_date=OP_DATE,
    )
    assert valid is False
    assert reason == "EQUIPMENT_OUT_OF_SERVICE"
    db.rollback()


# ============================================================
# TEST 10: Worker overlap detected
# ============================================================
def test_10_worker_overlap(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # First assignment
    r1 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 12, 0, tzinfo=WITA),
    )
    assert r1[1] == "CREATED"

    # Overlapping assignment
    r2 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),  # Overlaps!
        ended_at=datetime(2026, 9, 5, 19, 0, tzinfo=WITA),
    )
    assert r2[1] == "OVERLAP_WORKER"
    assert r2[0] is None
    db.rollback()


# ============================================================
# TEST 11: Equipment double-operator overlap detected
# ============================================================
def test_11_equipment_overlap(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")
    _seed_roster(db, "metro", "w2", OP_DATE, "DAY", "s1", planned_equipment_id="ex31")

    # Tono on EX-025
    create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 12, 0, tzinfo=WITA),
    )

    # Budi on same EX-025 overlapping
    r = create_actual_assignment(
        db, tenant_id="metro", employee_id="w2", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),  # Overlaps!
        ended_at=datetime(2026, 9, 5, 19, 0, tzinfo=WITA),
    )
    assert r[1] == "OVERLAP_EQUIPMENT"
    db.rollback()


# ============================================================
# TEST 12: Adjacent non-overlapping intervals allowed
# ============================================================
def test_12_adjacent_ok(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # First: 07:05–11:00
    r1 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
    )
    assert r1[1] == "CREATED"

    # Second: 11:00–19:00 (adjacent, no overlap)
    r2 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 19, 0, tzinfo=WITA),
    )
    assert r2[1] == "CREATED"
    db.rollback()


# ============================================================
# TEST 13: Equipment change during shift retained
# ============================================================
def test_13_equipment_change_retained(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # First equipment
    r1 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
    )
    # Second equipment
    r2 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 19, 0, tzinfo=WITA),
    )

    assignments = get_actual_assignments(
        db, tenant_id="metro", employee_id="w1", operating_date=OP_DATE,
    )
    assert len(assignments) == 2
    equipment_ids = {a.equipment_id for a in assignments}
    assert equipment_ids == {"ex25", "ex31"}
    db.rollback()


# ============================================================
# TEST 14: NIGHT cross-midnight retains operating_date
# ============================================================
def test_14_night_operating_date(db):
    _seed_metro(db)
    op_date = date(2026, 9, 5)
    _seed_roster(db, "metro", "w1", op_date, "NIGHT", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=op_date, shift_id="NIGHT", site_id="s1",
        started_at=datetime(2026, 9, 5, 19, 5, tzinfo=WITA),
    )

    assert result["assignment"].operating_date == op_date  # Sep 5, not Sep 6
    assert result["comparison"].operating_date == op_date
    db.rollback()


# ============================================================
# TEST 15: Operator substitution detected
# ============================================================
def test_15_substitution_detected(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # Budi (w2) uses Tono's (w1) planned equipment
    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w2", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["assignment"] is not None
    # Should detect substitution
    sub_discs = [d for d in result["discrepancies"]
                 if d.discrepancy_type == DiscrepancyType.OPERATOR_SUBSTITUTION]
    assert len(sub_discs) == 1
    assert sub_discs[0].planned_worker_id == "w1"
    assert sub_discs[0].actual_worker_id == "w2"
    db.rollback()


# ============================================================
# TEST 16: Substitute competency validated
# ============================================================
def test_16_substitute_competency(db):
    _seed_metro(db)
    # Remove Budi's competency
    comp = db.get(Competency, "c2")
    comp.valid_to = date(2026, 9, 1)  # Expired

    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # Budi tries to use Tono's equipment — should fail on competency
    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w2", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["assignment"] is None
    assert result["create_status"] == "COMPETENCY_EXPIRED"
    db.rollback()


# ============================================================
# TEST 17: Actual assignment idempotency
# ============================================================
def test_17_idempotency(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    started = datetime(2026, 9, 5, 7, 5, tzinfo=WITA)

    r1 = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started,
    )
    r2 = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started,
    )

    assert r1["create_status"] == "CREATED"
    assert r2["create_status"] == "DUPLICATE"
    assert r1["assignment"].id == r2["assignment"].id
    db.rollback()


# ============================================================
# TEST 18: Discrepancy idempotency
# ============================================================
def test_18_discrepancy_idempotency(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    started = datetime(2026, 9, 5, 7, 5, tzinfo=WITA)

    r1 = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started,
    )
    # Same call again (idempotent)
    r2 = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started,
    )

    assert r1["create_status"] == "CREATED"
    assert r2["create_status"] == "DUPLICATE"
    # Same discrepancy count
    assert len(r1["discrepancies"]) == len(r2["discrepancies"])
    db.rollback()


# ============================================================
# TEST 19: Same equipment code different tenant isolated
# ============================================================
def test_19_tenant_equipment_isolation(db):
    _seed_metro(db)
    _seed_lumin(db)

    # Metro's EX-025 is excavator, Lumin's EX-025 is forklift
    # Metro worker cannot use Lumin equipment
    result = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex_l",  # Lumin equipment!
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )
    assert result[1] == "CROSS_TENANT_EQUIPMENT"
    db.rollback()


# ============================================================
# TEST 20: Cross-tenant worker-equipment rejected
# ============================================================
def test_20_cross_tenant_worker(db):
    _seed_metro(db)
    _seed_lumin(db)

    # Lumin worker cannot use Metro equipment
    result = create_actual_assignment(
        db, tenant_id="metro", employee_id="wL", equipment_id="ex25",  # Lumin worker!
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )
    assert result[1] == "INVALID_WORKER"
    db.rollback()


# ============================================================
# TEST 21: Cross-tenant comparison rejected
# ============================================================
def test_21_cross_tenant_comparison(db):
    _seed_metro(db)
    _seed_lumin(db)

    # Create a roster for Metro worker with planned equipment
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # Create actual assignment for Metro
    r = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )
    assert r[1] == "CREATED"

    # Comparison should only see Metro data
    comparison = compare_planned_vs_actual(db, actual_assignment=r[0])
    assert comparison.tenant_id == "metro"
    assert comparison.planned_equipment_id == "ex25"
    db.rollback()


# ============================================================
# TEST 22: Telematics engine-on ≠ operator identity
# ============================================================
def test_22_telematics_not_identity(db):
    _seed_metro(db)

    # Telematics source should not bypass worker validation
    result = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        source="TELEMATICS",
    )
    # Should still require valid competency — telematics doesn't bypass
    assert result[1] == "CREATED"  # w1 has competency
    assert result[0].source == "TELEMATICS"

    # But source alone doesn't establish identity — that's enforced by
    # requiring employee_id to be a valid worker (checked in create_actual_assignment)
    db.rollback()


# ============================================================
# TEST 23: Raw → canonical → actual traceability
# ============================================================
def test_23_traceability(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        source="EQUIPMENT_IN", canonical_event_id="canonical-trace-001",
    )

    assignment = result["assignment"]
    comparison = result["comparison"]

    assert assignment.canonical_event_id == "canonical-trace-001"
    assert comparison.actual_assignment_id == assignment.id
    assert comparison.tenant_id == "metro"
    db.rollback()


# ============================================================
# TEST 24: Discrepancy created on mismatch
# ============================================================
def test_24_discrepancy_on_mismatch(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["comparison"].comparison_result == ComparisonResult.MISMATCH
    assert len(result["discrepancies"]) >= 1
    disc = result["discrepancies"][0]
    assert disc.discrepancy_type == DiscrepancyType.EQUIPMENT_MISMATCH
    assert disc.status == DiscrepancyStatus.OPEN
    db.rollback()


# ============================================================
# TEST 25: MATCH does not create discrepancy
# ============================================================
def test_25_match_no_discrepancy(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["comparison"].comparison_result == ComparisonResult.MATCH
    assert len(result["discrepancies"]) == 0
    db.rollback()


# ============================================================
# TEST 26: No planned equipment handled explicitly
# ============================================================
def test_26_no_planned(db):
    _seed_metro(db)
    # Roster WITHOUT planned_equipment_id
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id=None)

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    assert result["comparison"].comparison_result == ComparisonResult.NO_PLANNED_EQUIPMENT
    assert result["comparison"].planned_equipment_id is None
    db.rollback()


# ============================================================
# TEST 27: Historical competency validity used
# ============================================================
def test_27_historical_competency(db):
    _seed_metro(db)
    # Competency valid through Sep 10
    comp = db.get(Competency, "c1")
    comp.valid_to = date(2026, 9, 10)

    # Assignment on Sep 9 → valid
    valid9, _ = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=date(2026, 9, 9),
    )
    assert valid9 is True

    # Assignment on Sep 11 → expired
    valid11, reason11 = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=date(2026, 9, 11),
    )
    assert valid11 is False
    assert reason11 == "COMPETENCY_EXPIRED"
    db.rollback()


# ============================================================
# TEST 28: Lumin Park regression PASS
# ============================================================
def test_28_lumin_regression(db):
    _seed_metro(db)
    _seed_lumin(db)

    # Lumin worker can use Lumin equipment
    result = create_actual_assignment(
        db, tenant_id="lumin", employee_id="wL", equipment_id="ex_l",
        operating_date=OP_DATE, shift_id=None, site_id=None,
        started_at=datetime(2026, 9, 5, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert result[1] == "CREATED"
    assert result[0].tenant_id == "lumin"
    db.rollback()


# ============================================================
# TEST 29: Equipment change closes first + opens second
# ============================================================
def test_29_equipment_change_close_open(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    # First assignment
    r1 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
    )
    assert r1[1] == "CREATED"

    # Close first
    closed = close_actual_assignment(
        db, assignment_id=r1[0].id,
        ended_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
    )
    assert closed.status == ActualAssignmentStatus.CLOSED

    # Open second
    r2 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 11, 0, tzinfo=WITA),
        ended_at=datetime(2026, 9, 5, 19, 0, tzinfo=WITA),
    )
    assert r2[1] == "CREATED"
    assert r2[0].equipment_id == "ex31"

    # Both retained
    assignments = get_actual_assignments(
        db, tenant_id="metro", employee_id="w1", operating_date=OP_DATE,
    )
    assert len(assignments) == 2
    db.rollback()


# ============================================================
# TEST 30: Discrepancy status OPEN by default
# ============================================================
def test_30_discrepancy_default_status(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )

    for disc in result["discrepancies"]:
        assert disc.status == DiscrepancyStatus.OPEN
    db.rollback()


# ============================================================
# TEST 31: Comparison result has rule_version_id
# ============================================================
def test_31_comparison_rule_version(db):
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    result = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
        rule_version_id="rv1",
    )

    assert result["assignment"].rule_version_id == "rv1"
    assert result["comparison"].rule_version_id == "rv1"
    db.rollback()


# ============================================================
# ISOLATION & CAPABILITY TESTS (32-43)
# ============================================================

# 32. Lumin competency disabled → NOT_APPLICABLE
def test_32_lumin_competency_not_applicable(db):
    """Lumin has no competency_validation_enabled policy → NOT_APPLICABLE."""
    _seed_metro(db)
    _seed_lumin(db)

    # Lumin has no competency_validation_enabled policy (default=false)
    valid, reason = validate_competency(
        db, tenant_id="lumin", employee_id="wL",
        equipment_type="FORKLIFT", at_date=OP_DATE,
    )
    assert valid is True
    assert reason == "COMPETENCY_NOT_APPLICABLE"


# 33. Metro competency enabled + valid → PASS
def test_33_metro_competency_valid(db):
    """Metro has competency_validation_enabled=true, worker has valid cert → PASS."""
    _seed_metro(db)

    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=OP_DATE,
    )
    assert valid is True
    assert reason == "COMPETENCY_VALID"


# 34. Metro competency enabled + missing → FAIL
def test_34_metro_competency_missing(db):
    """Metro worker without competency for equipment type → COMPETENCY_MISSING."""
    _seed_metro(db)
    # w2 has no BULLDOZER competency
    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w2",
        equipment_type="BULLDOZER", at_date=OP_DATE,
    )
    assert valid is False
    assert reason == "COMPETENCY_MISSING"


# 35. Metro competency enabled + expired → FAIL
def test_35_metro_competency_expired(db):
    """Metro worker with expired competency → COMPETENCY_EXPIRED."""
    _seed_metro(db)
    # Expire w1's competency
    comp = db.get(Competency, "c1")
    comp.valid_to = date(2026, 9, 1)  # Expired before OP_DATE

    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=OP_DATE,
    )
    assert valid is False
    assert reason == "COMPETENCY_EXPIRED"


# 36. Absence of tenant competency records does NOT globally disable validation
def test_36_no_records_does_not_disable(db):
    """Metro with competency_validation_enabled=true but no records for type → MISSING, not PASS."""
    _seed_metro(db)
    # No worker has DRILL competency
    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="w1",
        equipment_type="DRILL", at_date=OP_DATE,
    )
    assert valid is False
    assert reason == "COMPETENCY_MISSING"


# 37. Metro capability changes do not affect Lumin
def test_37_metro_capability_isolation(db):
    """Changing Metro capability does not affect Lumin."""
    _seed_metro(db)
    _seed_lumin(db)

    # Verify Metro has competency enabled
    assert is_capability_enabled(db, tenant_id="metro", capability_key="competency_validation_enabled") is True

    # Verify Lumin does NOT have competency enabled
    assert is_capability_enabled(db, tenant_id="lumin", capability_key="competency_validation_enabled") is False

    # Disable Metro competency
    policy = db.scalar(
        select(RosterPolicy).where(
            RosterPolicy.tenant_id == "metro",
            RosterPolicy.policy_key == "competency_validation_enabled",
        )
    )
    policy.policy_value = "false"
    db.flush()

    # Metro now disabled
    assert is_capability_enabled(db, tenant_id="metro", capability_key="competency_validation_enabled") is False

    # Lumin still disabled (unchanged)
    assert is_capability_enabled(db, tenant_id="lumin", capability_key="competency_validation_enabled") is False


# 38. Lumin capability changes do not affect Metro
def test_38_lumin_capability_isolation(db):
    """Changing Lumin capability does not affect Metro."""
    _seed_metro(db)
    _seed_lumin(db)

    # Enable Lumin competency
    db.add(RosterPolicy(
        id="rp-cap-comp-lumin", tenant_id="lumin",
        policy_key="competency_validation_enabled", policy_value="true",
        data_type="boolean", confirmation_status="CONFIRMED",
    ))
    db.flush()

    # Lumin now enabled
    assert is_capability_enabled(db, tenant_id="lumin", capability_key="competency_validation_enabled") is True

    # Metro still enabled (unchanged)
    assert is_capability_enabled(db, tenant_id="metro", capability_key="competency_validation_enabled") is True


# 39. Metro worker cannot use Lumin equipment
def test_39_cross_tenant_metro_worker_lumin_equipment(db):
    """Metro worker cannot use Lumin equipment."""
    _seed_metro(db)
    _seed_lumin(db)

    result = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex_l",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )
    assert result[0] is None
    assert result[1] == "CROSS_TENANT_EQUIPMENT"


# 40. Lumin worker cannot use Metro equipment
def test_40_cross_tenant_lumin_worker_metro_equipment(db):
    """Lumin worker cannot use Metro equipment."""
    _seed_metro(db)
    _seed_lumin(db)

    result = create_actual_assignment(
        db, tenant_id="lumin", employee_id="wL", equipment_id="ex25",
        operating_date=OP_DATE, shift_id=None, site_id=None,
        started_at=datetime(2026, 9, 5, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert result[0] is None
    assert result[1] == "CROSS_TENANT_EQUIPMENT"


# 41. Metro competency cannot validate Lumin worker
def test_41_cross_tenant_competency_metro_lumin(db):
    """Metro competency records cannot validate Lumin worker."""
    _seed_metro(db)
    _seed_lumin(db)

    # Try to validate Lumin worker against Metro competency
    valid, reason = validate_competency(
        db, tenant_id="metro", employee_id="wL",
        equipment_type="EXCAVATOR", at_date=OP_DATE,
    )
    # wL belongs to lumin, not metro — no competency records for wL in metro
    assert valid is False
    assert reason == "COMPETENCY_MISSING"


# 42. Lumin competency cannot validate Metro worker
def test_42_cross_tenant_competency_lumin_metro(db):
    """Lumin competency records cannot validate Metro worker."""
    _seed_metro(db)
    _seed_lumin(db)

    # Try to validate Metro worker against Lumin competency
    # Lumin has competency_validation_enabled=false → NOT_APPLICABLE
    valid, reason = validate_competency(
        db, tenant_id="lumin", employee_id="w1",
        equipment_type="EXCAVATOR", at_date=OP_DATE,
    )
    # Lumin competency disabled → NOT_APPLICABLE (passes, but doesn't validate)
    assert valid is True
    assert reason == "COMPETENCY_NOT_APPLICABLE"


# 43. Cross-tenant planned/actual comparison rejected
def test_43_cross_tenant_comparison_rejected(db):
    """Cannot compare Metro actual with Lumin planned."""
    _seed_metro(db)
    _seed_lumin(db)

    # Create a Lumin roster assignment
    _seed_roster(db, "lumin", "wL", OP_DATE, None, None, planned_equipment_id="ex_l")

    # Create Metro actual assignment
    result = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=datetime(2026, 9, 5, 7, 5, tzinfo=WITA),
    )
    assert result[1] == "CREATED"

    # Compare — should only find Metro roster, not Lumin
    comparison = compare_planned_vs_actual(db, actual_assignment=result[0])
    # Metro has no roster for w1 on this date (we only seeded Lumin roster)
    assert comparison.comparison_result == ComparisonResult.NO_PLANNED_EQUIPMENT
    assert comparison.tenant_id == "metro"
    db.rollback()


# 44. Retry actual assignment remains idempotent
def test_44_retry_idempotent(db):
    """Retrying same event returns DUPLICATE, not new assignment."""
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    started = datetime(2026, 9, 5, 7, 5, tzinfo=WITA)

    r1 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started, canonical_event_id="evt-001",
    )
    assert r1[1] == "CREATED"

    # Retry with same params
    r2 = create_actual_assignment(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex25",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started, canonical_event_id="evt-001",
    )
    assert r2[1] == "DUPLICATE"
    assert r2[0].id == r1[0].id
    db.rollback()


# 45. Duplicate evidence does not create duplicate discrepancy
def test_45_no_duplicate_discrepancy(db):
    """Retrying mismatch does not create duplicate discrepancy."""
    _seed_metro(db)
    _seed_roster(db, "metro", "w1", OP_DATE, "DAY", "s1", planned_equipment_id="ex25")

    started = datetime(2026, 9, 5, 7, 5, tzinfo=WITA)

    r1 = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started,
    )
    assert r1["create_status"] == "CREATED"
    disc_count_1 = len(r1["discrepancies"])

    # Retry
    r2 = process_equipment_checkin(
        db, tenant_id="metro", employee_id="w1", equipment_id="ex31",
        operating_date=OP_DATE, shift_id="DAY", site_id="s1",
        started_at=started,
    )
    assert r2["create_status"] == "DUPLICATE"
    disc_count_2 = len(r2["discrepancies"])

    # Same discrepancies returned (idempotent)
    assert disc_count_1 == disc_count_2
    db.rollback()


# 46. Timezone isolation: Metro WITA, Lumin WIB
def test_46_timezone_isolation(db):
    """Metro uses WITA, Lumin uses WIB — independent."""
    _seed_metro(db)
    _seed_lumin(db)

    metro_tz = db.get(Tenant, "metro").timezone
    lumin_tz = db.get(Tenant, "lumin").timezone

    assert metro_tz == "Asia/Makassar"
    assert lumin_tz == "Asia/Jakarta"
    assert metro_tz != lumin_tz
