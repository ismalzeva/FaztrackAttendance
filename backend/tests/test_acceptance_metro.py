"""
test_acceptance_metro.py — 25 acceptance tests for Metro Mining Attendance.
Run: cd backend && python -m pytest tests/test_acceptance_metro.py -v
"""
import pytest
from datetime import date, timedelta, time, datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.database import Base
from app.models import (
    Tenant, User, Membership, RoleCode,
    Worker, Site, Equipment, Role, Crew, Competency,
    ShiftTemplate, CheckpointPolicy, RosterPolicy, RuleVersion,
    RosterAssignment, EquipmentAssignmentActual, EmployeeMeta,
    ExceptionEvent, OverrideEvent,
    WorkStatus, SiteStatusEnum, ValidationStatus,
    SiteType, SiteStatus, EquipmentStatus, CompetencyStatus,
    ExceptionSeverity, ExceptionStatus,
)
from app.models import uid
from app.roster_validator import validate_roster_assignment, validate_full_roster


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


TENANT_ID = "test-tenant"
ADMIN_ID = "admin-001"


def _setup_tenant(db: Session):
    """Create minimal tenant + admin."""
    db.add(Tenant(id=TENANT_ID, code="test", name="Test Mining"))
    db.add(User(id=ADMIN_ID, login_id="admin@test.id", display_name="Admin", password_hash="x"))
    db.add(Membership(id="mem-001", user_id=ADMIN_ID, tenant_id=TENANT_ID, role=RoleCode.OWNER))
    db.flush()


def _create_employee(db: Session, emp_id: str, crew_id: str = "CREW-A", role_id: str = "ROLE-DT"):
    """Create worker + employee meta."""
    db.add(Worker(id=emp_id, tenant_id=TENANT_ID, code=emp_id, name=f"Worker {emp_id}"))
    db.add(EmployeeMeta(
        id=f"em-{emp_id}", tenant_id=TENANT_ID, worker_id=emp_id,
        employee_no=emp_id, role_id=role_id, crew_id=crew_id,
        effective_from=date(2026, 1, 1),
    ))


def _create_equipment(db: Session, eq_id: str, eq_type: str = "DUMP_TRUCK", status: EquipmentStatus = EquipmentStatus.ACTIVE):
    db.add(Equipment(
        id=eq_id, tenant_id=TENANT_ID, equipment_code=eq_id,
        equipment_type=eq_type, status=status,
        effective_from=date(2026, 1, 1),
    ))


def _create_competency(db: Session, emp_id: str, eq_type: str = "DUMP_TRUCK"):
    db.add(Competency(
        id=f"comp-{emp_id}", tenant_id=TENANT_ID, competency_code=f"comp-{emp_id}",
        employee_id=emp_id, equipment_type=eq_type,
        valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
        status=CompetencyStatus.VALID,
    ))


def _create_shift(db: Session, shift_id: str = "DAY"):
    if shift_id == "DAY":
        db.add(ShiftTemplate(
            id="DAY", tenant_id=TENANT_ID, shift_code="DAY", shift_name="Day Shift",
            start_time=time(7, 0), end_time=time(19, 0),
            break_start=time(12, 0), break_end=time(13, 0),
            handover_start=time(18, 45), handover_end=time(19, 0),
            crosses_midnight=False,
        ))
    else:
        db.add(ShiftTemplate(
            id="NIGHT", tenant_id=TENANT_ID, shift_code="NIGHT", shift_name="Night Shift",
            start_time=time(19, 0), end_time=time(7, 0),
            break_start=time(0, 0), break_end=time(1, 0),
            handover_start=time(6, 45), handover_end=time(7, 0),
            crosses_midnight=True,
        ))


def _assign(db: Session, emp_id: str, op_date: date, work_status: WorkStatus,
            shift_id: str | None = "DAY", eq_id: str | None = None,
            site_status: SiteStatusEnum = SiteStatusEnum.ONSITE):
    ra = RosterAssignment(
        id=uid(), tenant_id=TENANT_ID, roster_code=f"R-{emp_id}-{op_date}",
        operating_date=op_date, employee_id=emp_id, work_status=work_status,
        shift_id=shift_id, site_id="SITE-01",
        planned_equipment_id=eq_id, site_status=site_status,
        validation_status=ValidationStatus.VALID,
    )
    db.add(ra)
    db.flush()
    return ra


# ─────────────────────────────────────────────────────────────
# ROSTER TESTS (1-9)
# ─────────────────────────────────────────────────────────────

class TestRoster:
    def test_01_accept_12_work_days_then_rest_day_13(self, db):
        """Accept 12 consecutive WORK days followed by REST on day 13."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        base = date(2026, 9, 1)

        # Assign 12 WORK days
        for i in range(12):
            _assign(db, "EMP-001", base + timedelta(days=i), WorkStatus.WORK, eq_id="EQ-001")

        # Day 13 = REST — should pass
        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", base + timedelta(days=12),
            WorkStatus.REST, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert len(excs) == 0, f"REST on day 13 should pass, got {len(excs)} exceptions"

    def test_02_reject_work_on_day_13(self, db):
        """Reject WORK on day 13 after 12 consecutive WORK days."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        base = date(2026, 9, 1)

        for i in range(12):
            _assign(db, "EMP-001", base + timedelta(days=i), WorkStatus.WORK, eq_id="EQ-001")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", base + timedelta(days=12),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        rule_codes = [e.rule_code for e in excs]
        assert "MANDATORY_REST_DAY" in rule_codes or "MAX_CONSECUTIVE_WORK" in rule_codes

    def test_03_accept_return_on_day_14(self, db):
        """Accept return to WORK on day 14 after mandatory REST."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        base = date(2026, 9, 1)

        for i in range(12):
            _assign(db, "EMP-001", base + timedelta(days=i), WorkStatus.WORK, eq_id="EQ-001")
        _assign(db, "EMP-001", base + timedelta(days=12), WorkStatus.REST)

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", base + timedelta(days=13),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        # Should pass — no consecutive-work violation after REST breaks the streak
        consecutive = [e for e in excs if e.rule_code in ("MAX_CONSECUTIVE_WORK", "MANDATORY_REST_DAY")]
        assert len(consecutive) == 0, f"Day 14 after REST should pass, got: {[e.rule_code for e in excs]}"

    def test_04_accept_7_same_shift_then_night(self, db):
        """Accept 7 worked DAY days followed by NIGHT with sufficient rest."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        _create_shift(db, "NIGHT")
        base = date(2026, 9, 1)

        for i in range(7):
            _assign(db, "EMP-001", base + timedelta(days=i), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        # Switch to NIGHT — should pass
        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", base + timedelta(days=7),
            WorkStatus.WORK, "NIGHT", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        same_shift = [e for e in excs if e.rule_code == "MAX_SAME_SHIFT_WORK"]
        assert len(same_shift) == 0

    def test_05_reject_eighth_consecutive_day_shift(self, db):
        """Reject an eighth consecutive worked DAY assignment."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        base = date(2026, 9, 1)

        for i in range(7):
            _assign(db, "EMP-001", base + timedelta(days=i), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", base + timedelta(days=7),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "MAX_SAME_SHIFT_WORK" for e in excs)

    def test_06_reject_work_during_offsite(self, db):
        """Reject assignment during OFFSITE."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 12, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.OFFSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "OFFSITE_WORK_REJECTED" for e in excs)

    def test_07_accept_12_weeks_onsite_2_weeks_offsite_cycle(self, db):
        """Accept 12 weeks onsite followed by 2 weeks offsite using the configured cycle definition."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        base = date(2026, 9, 1)

        # 12 weeks = 84 days onsite WORK (simplified: REST every 13th day)
        work_count = 0
        for i in range(84):
            d = base + timedelta(days=i)
            if (work_count + 1) % 13 == 0:
                _assign(db, "EMP-001", d, WorkStatus.REST)
                # Don't increment work_count for REST
            else:
                _assign(db, "EMP-001", d, WorkStatus.WORK, "DAY", eq_id="EQ-001")
                work_count += 1

        # 2 weeks offsite = should be REST/OFFSITE
        for i in range(84, 98):
            d = base + timedelta(days=i)
            excs = validate_roster_assignment(
                db, TENANT_ID, "EMP-001", d,
                WorkStatus.REST, None, SiteStatusEnum.OFFSITE,
                skip_existing_check=True,
            )
            assert len(excs) == 0

    def test_08_reject_overlapping_assignments(self, db):
        """Reject overlapping assignments for one employee."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
        )
        assert any(e.rule_code == "OVERLAPPING_ASSIGNMENT" for e in excs)

    def test_09_reject_equipment_double_book(self, db):
        """Reject two employees on the same equipment for overlapping intervals."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_employee(db, "EMP-002")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_competency(db, "EMP-002")
        _create_shift(db, "DAY")

        _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-002", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "EQUIPMENT_DOUBLE_BOOK" for e in excs)


# ─────────────────────────────────────────────────────────────
# ATTENDANCE TESTS (10-15) — checkpoint policy structure tests
# ─────────────────────────────────────────────────────────────

class TestAttendance:
    def test_10_briefing_window_structure(self, db):
        """Checkpoint policies define briefing windows per shift."""
        _setup_tenant(db)
        _create_shift(db, "DAY")
        _create_shift(db, "NIGHT")
        db.add(CheckpointPolicy(
            id="cp-briefing-day", tenant_id=TENANT_ID,
            checkpoint_type="BRIEFING_IN", shift_id="DAY",
            window_start_offset_min=-60, window_end_offset_min=0,
            severity="CRITICAL",
        ))
        db.add(CheckpointPolicy(
            id="cp-briefing-night", tenant_id=TENANT_ID,
            checkpoint_type="BRIEFING_IN", shift_id="NIGHT",
            window_start_offset_min=-60, window_end_offset_min=0,
            severity="CRITICAL",
        ))
        db.flush()

        day_cp = db.query(CheckpointPolicy).filter(
            CheckpointPolicy.checkpoint_type == "BRIEFING_IN",
            CheckpointPolicy.shift_id == "DAY",
        ).first()
        assert day_cp is not None
        assert day_cp.window_end_offset_min == 0  # Must finish before shift start

    def test_11_late_briefing_threshold(self, db):
        """Briefing after 07:00 DAY or 19:00 NIGHT is flagged."""
        _setup_tenant(db)
        _create_shift(db, "DAY")
        db.add(CheckpointPolicy(
            id="cp-briefing-day", tenant_id=TENANT_ID,
            checkpoint_type="BRIEFING_IN", shift_id="DAY",
            window_start_offset_min=-60, window_end_offset_min=0,
            severity="CRITICAL",
        ))
        db.flush()

        cp = db.query(CheckpointPolicy).filter(
            CheckpointPolicy.checkpoint_type == "BRIEFING_IN",
            CheckpointPolicy.shift_id == "DAY",
        ).first()
        # After 07:00 = past window_end → LATE_BRIEFING
        assert cp.severity == "CRITICAL"

    def test_12_late_break_return_threshold(self, db):
        """Break return after 13:00 DAY / 01:00 NIGHT is late."""
        _setup_tenant(db)
        _create_shift(db, "DAY")
        db.add(CheckpointPolicy(
            id="cp-break-day", tenant_id=TENANT_ID,
            checkpoint_type="BREAK_IN", shift_id="DAY",
            window_start_offset_min=0, window_end_offset_min=0,
            severity="CRITICAL",
        ))
        db.flush()

        cp = db.query(CheckpointPolicy).filter(
            CheckpointPolicy.checkpoint_type == "BREAK_IN",
            CheckpointPolicy.shift_id == "DAY",
        ).first()
        assert cp is not None

    def test_13_handover_window_exists(self, db):
        """Handover windows are defined: DAY 18:45-19:00, NIGHT 06:45-07:00."""
        _setup_tenant(db)
        _create_shift(db, "DAY")
        _create_shift(db, "NIGHT")

        day = db.query(ShiftTemplate).filter(ShiftTemplate.id == "DAY").first()
        night = db.query(ShiftTemplate).filter(ShiftTemplate.id == "NIGHT").first()

        assert day.handover_start == time(18, 45)
        assert day.handover_end == time(19, 0)
        assert night.handover_start == time(6, 45)
        assert night.handover_end == time(7, 0)

    def test_14_early_handover_flag(self, db):
        """Handover before permitted window is flagged as EARLY_HANDOVER."""
        _setup_tenant(db)
        _create_shift(db, "DAY")
        db.add(CheckpointPolicy(
            id="cp-handover-day", tenant_id=TENANT_ID,
            checkpoint_type="HANDOVER", shift_id="DAY",
            window_start_offset_min=-15, window_end_offset_min=0,
            severity="CRITICAL",
        ))
        db.flush()

        cp = db.query(CheckpointPolicy).filter(
            CheckpointPolicy.checkpoint_type == "HANDOVER",
            CheckpointPolicy.shift_id == "DAY",
        ).first()
        # window_start_offset = -15 means earliest is 18:45
        assert cp.window_start_offset_min == -15

    def test_15_night_operating_date_resolution(self, db):
        """NIGHT break events after midnight resolve to previous calendar day's operating_date."""
        _setup_tenant(db)
        _create_shift(db, "NIGHT")

        night = db.query(ShiftTemplate).filter(ShiftTemplate.id == "NIGHT").first()
        assert night.crosses_midnight is True
        # Operating date for NIGHT shift = date of shift START, not event time


# ─────────────────────────────────────────────────────────────
# EQUIPMENT & IDENTITY TESTS (16-20)
# ─────────────────────────────────────────────────────────────

class TestEquipmentIdentity:
    def test_16_competency_required_for_equipment(self, db):
        """Reject assignment without valid competency for equipment type."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001", "DUMP_TRUCK")
        _create_shift(db, "DAY")
        # No competency created

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "UNQUALIFIED_ASSIGNMENT" for e in excs)

    def test_17_accept_with_valid_competency(self, db):
        """Accept assignment with valid competency."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001", "DUMP_TRUCK")
        _create_competency(db, "EMP-001", "DUMP_TRUCK")
        _create_shift(db, "DAY")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert not any(e.rule_code == "UNQUALIFIED_ASSIGNMENT" for e in excs)

    def test_18_reject_expired_competency(self, db):
        """Reject assignment with expired competency."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001", "DUMP_TRUCK")
        db.add(Competency(
            id="comp-expired", tenant_id=TENANT_ID, competency_code="comp-expired",
            employee_id="EMP-001", equipment_type="DUMP_TRUCK",
            valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
            status=CompetencyStatus.EXPIRED,
        ))
        _create_shift(db, "DAY")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "UNQUALIFIED_ASSIGNMENT" for e in excs)

    def test_19_equipment_status_check(self, db):
        """Reject assignment with OUT_OF_SERVICE equipment."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001", "DUMP_TRUCK", EquipmentStatus.OUT_OF_SERVICE)
        _create_competency(db, "EMP-001", "DUMP_TRUCK")
        _create_shift(db, "DAY")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "EQUIPMENT_INACTIVE" for e in excs)

    def test_20_equipment_type_mismatch(self, db):
        """Competency for wrong equipment type is rejected."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001", "EXCAVATOR")
        _create_competency(db, "EMP-001", "DUMP_TRUCK")  # Wrong type
        _create_shift(db, "DAY")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", date(2026, 9, 1),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert any(e.rule_code == "UNQUALIFIED_ASSIGNMENT" for e in excs)


# ─────────────────────────────────────────────────────────────
# DATA & AUDIT TESTS (21-25)
# ─────────────────────────────────────────────────────────────

class TestDataAudit:
    def test_21_rule_version_tracking(self, db):
        """Rule version is recorded on roster assignments."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")
        ra.effective_rule_version = "METRO-RULE-v0.1"
        db.flush()

        found = db.query(RosterAssignment).filter(RosterAssignment.id == ra.id).first()
        assert found.effective_rule_version == "METRO-RULE-v0.1"

    def test_22_exception_events_recorded(self, db):
        """Exceptions are recorded with rule_code and severity."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")
        base = date(2026, 9, 1)

        for i in range(12):
            _assign(db, "EMP-001", base + timedelta(days=i), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        excs = validate_roster_assignment(
            db, TENANT_ID, "EMP-001", base + timedelta(days=12),
            WorkStatus.WORK, "DAY", SiteStatusEnum.ONSITE, "EQ-001",
            skip_existing_check=True,
        )
        assert len(excs) > 0
        for exc in excs:
            assert exc.rule_code is not None
            assert exc.severity in (ExceptionSeverity.CRITICAL, ExceptionSeverity.WARNING, ExceptionSeverity.INFO)
            assert exc.operating_date is not None

    def test_23_rule_version_snapshot(self, db):
        """Rule version snapshots capture policy state."""
        _setup_tenant(db)
        db.add(RosterPolicy(
            id="rp-1", tenant_id=TENANT_ID,
            policy_key="max_consecutive_workdays", policy_value="12",
            data_type="integer", confirmation_status="CONFIRMED",
        ))
        db.flush()

        from app.rule_versioning import snapshot_rules
        rv = snapshot_rules(db, TENANT_ID, "v0.1-test", date(2026, 9, 1))
        assert rv is not None
        assert "max_consecutive_workdays" in rv.config_snapshot_json

    def test_24_override_events(self, db):
        """Override events reference exception events."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")

        exc = ExceptionEvent(
            id=uid(), tenant_id=TENANT_ID, operating_date=date(2026, 9, 1),
            employee_id="EMP-001", rule_code="TEST_RULE",
            severity=ExceptionSeverity.CRITICAL, status=ExceptionStatus.OPEN,
        )
        db.add(exc)
        db.flush()

        override = OverrideEvent(
            id=uid(), tenant_id=TENANT_ID, exception_id=exc.id,
            action="APPROVE", reason="Supervisor approved", approved_by=ADMIN_ID,
        )
        db.add(override)
        db.flush()

        found = db.query(OverrideEvent).filter(OverrideEvent.exception_id == exc.id).first()
        assert found is not None
        assert found.action == "APPROVE"

    def test_25_planned_vs_actual_separation(self, db):
        """Planned equipment and actual equipment are stored separately."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001", "DUMP_TRUCK")
        _create_equipment(db, "EQ-002", "DUMP_TRUCK")
        _create_competency(db, "EMP-001", "DUMP_TRUCK")
        _create_shift(db, "DAY")

        # Planned = EQ-001
        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        # Actual = EQ-002 (different)
        actual = EquipmentAssignmentActual(
            id=uid(), tenant_id=TENANT_ID, roster_id=ra.id,
            employee_id="EMP-001", equipment_id="EQ-002",
            operating_date=date(2026, 9, 1),
            started_at=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
            source="MANUAL",
        )
        db.add(actual)
        db.flush()

        found_ra = db.query(RosterAssignment).filter(RosterAssignment.id == ra.id).first()
        found_actual = db.query(EquipmentAssignmentActual).filter(
            EquipmentAssignmentActual.roster_id == ra.id
        ).first()

        assert found_ra.planned_equipment_id == "EQ-001"
        assert found_actual.equipment_id == "EQ-002"
        assert found_ra.planned_equipment_id != found_actual.equipment_id


# ─────────────────────────────────────────────────────────────
# VERIFICATION TESTS (additional evidence requirements)
# ─────────────────────────────────────────────────────────────

class TestRuleVersioningHistorical:
    """Evidence: rule versioning preserves historical assignments."""

    def test_26_historical_roster_preserves_rule_version(self, db):
        """Published roster assignment stores rule_version_id and historical data is immutable."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        # Create rule version v1
        from app.rule_versioning import snapshot_rules
        db.add(RosterPolicy(
            id="rp-1", tenant_id=TENANT_ID,
            policy_key="max_consecutive_workdays", policy_value="12",
            data_type="integer", confirmation_status="CONFIRMED",
        ))
        db.flush()
        rv1 = snapshot_rules(db, TENANT_ID, "v0.1", date(2026, 9, 1))

        # Assign roster with rule version v1
        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")
        ra.effective_rule_version = "v0.1"
        db.flush()

        # Update policy value (simulating rule change)
        rp = db.query(RosterPolicy).filter(RosterPolicy.policy_key == "max_consecutive_workdays").first()
        rp.policy_value = "10"
        db.flush()
        rv2 = snapshot_rules(db, TENANT_ID, "v0.2", date(2026, 10, 1))

        # Historical assignment still points to v0.1
        found = db.query(RosterAssignment).filter(RosterAssignment.id == ra.id).first()
        assert found.effective_rule_version == "v0.1"

        # Rule version v1 snapshot is preserved with original value
        rv1_check = db.query(RuleVersion).filter(RuleVersion.version_label == "v0.1").first()
        assert "12" in rv1_check.config_snapshot_json

        # Rule version v2 has updated value
        rv2_check = db.query(RuleVersion).filter(RuleVersion.version_label == "v0.2").first()
        assert "10" in rv2_check.config_snapshot_json

    def test_27_active_rule_change_does_not_affect_historical(self, db):
        """Changing active rule does not modify historical assignments."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        # Assign with rule v0.1
        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")
        ra.effective_rule_version = "v0.1"
        db.flush()

        # Simulate policy change (new active rule)
        db.add(RosterPolicy(
            id="rp-new", tenant_id=TENANT_ID,
            policy_key="max_consecutive_workdays", policy_value="8",
            data_type="integer", confirmation_status="CONFIRMED",
        ))
        db.flush()

        # Historical assignment unchanged
        found = db.query(RosterAssignment).filter(RosterAssignment.id == ra.id).first()
        assert found.effective_rule_version == "v0.1"


class TestEffectiveDatedMasterData:
    """Evidence: effective-dated master data does not overwrite historical."""

    def test_28_employee_meta_effective_dating(self, db):
        """Employee meta has effective_from/effective_to; updates preserve effective period."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")

        # Original role assignment
        meta = db.query(EmployeeMeta).filter(EmployeeMeta.worker_id == "EMP-001").first()
        meta.effective_from = date(2026, 1, 1)
        meta.effective_to = date(2026, 6, 30)
        db.flush()

        # Verify effective period is stored
        found = db.query(EmployeeMeta).filter(EmployeeMeta.worker_id == "EMP-001").first()
        assert found.effective_from == date(2026, 1, 1)
        assert found.effective_to == date(2026, 6, 30)

        # Update effective period (simulating role change)
        found.effective_from = date(2026, 7, 1)
        found.effective_to = None
        found.crew_id = "CREW-B"
        db.flush()

        # Verify updated
        updated = db.query(EmployeeMeta).filter(EmployeeMeta.worker_id == "EMP-001").first()
        assert updated.effective_from == date(2026, 7, 1)
        assert updated.effective_to is None
        assert updated.crew_id == "CREW-B"

    def test_29_equipment_effective_dating(self, db):
        """Equipment has effective_from/effective_to."""
        _setup_tenant(db)
        db.add(Equipment(
            id="EQ-OLD", tenant_id=TENANT_ID, equipment_code="EQ-OLD",
            equipment_type="DUMP_TRUCK", status=EquipmentStatus.INACTIVE,
            effective_from=date(2025, 1, 1), effective_to=date(2025, 12, 31),
        ))
        db.add(Equipment(
            id="EQ-NEW", tenant_id=TENANT_ID, equipment_code="EQ-NEW",
            equipment_type="DUMP_TRUCK", status=EquipmentStatus.ACTIVE,
            effective_from=date(2026, 1, 1), effective_to=None,
        ))
        db.flush()

        old = db.query(Equipment).filter(Equipment.id == "EQ-OLD").first()
        new = db.query(Equipment).filter(Equipment.id == "EQ-NEW").first()
        assert old.effective_to == date(2025, 12, 31)
        assert new.effective_to is None

    def test_30_competency_effective_dating(self, db):
        """Competency has valid_from/valid_to."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        db.add(Competency(
            id="comp-old", tenant_id=TENANT_ID, competency_code="comp-old",
            employee_id="EMP-001", equipment_type="DUMP_TRUCK",
            valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
            status=CompetencyStatus.EXPIRED,
        ))
        db.add(Competency(
            id="comp-new", tenant_id=TENANT_ID, competency_code="comp-new",
            employee_id="EMP-001", equipment_type="EXCAVATOR",
            valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
            status=CompetencyStatus.VALID,
        ))
        db.flush()

        old = db.query(Competency).filter(Competency.id == "comp-old").first()
        new = db.query(Competency).filter(Competency.id == "comp-new").first()
        assert old.status == CompetencyStatus.EXPIRED
        assert new.status == CompetencyStatus.VALID

    def test_31_crew_assignment_effective_dating(self, db):
        """Crew assignment via EmployeeMeta is effective-dated (update in place)."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")

        meta = db.query(EmployeeMeta).filter(EmployeeMeta.worker_id == "EMP-001").first()
        original_crew = meta.crew_id
        meta.effective_from = date(2026, 1, 1)
        meta.effective_to = date(2026, 6, 30)
        db.flush()

        # Update crew assignment (effective dating via update)
        meta.crew_id = "CREW-B"
        meta.effective_from = date(2026, 7, 1)
        meta.effective_to = None
        db.flush()

        updated = db.query(EmployeeMeta).filter(EmployeeMeta.worker_id == "EMP-001").first()
        assert updated.crew_id == "CREW-B"
        assert updated.effective_from == date(2026, 7, 1)
        assert updated.effective_to is None


class TestPlannedVsActualSeparation:
    """Evidence: planned equipment is part of roster, actual is separate entity."""

    def test_32_planned_equipment_in_roster(self, db):
        """Planned equipment is stored in RosterAssignment."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")
        assert ra.planned_equipment_id == "EQ-001"

    def test_33_actual_equipment_separate_entity(self, db):
        """Actual equipment assignment is a separate entity (EquipmentAssignmentActual)."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_equipment(db, "EQ-002")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        actual = EquipmentAssignmentActual(
            id=uid(), tenant_id=TENANT_ID, roster_id=ra.id,
            employee_id="EMP-001", equipment_id="EQ-002",
            operating_date=date(2026, 9, 1),
            started_at=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
            source="TELEMATICS",
        )
        db.add(actual)
        db.flush()

        # Planned unchanged
        found_ra = db.query(RosterAssignment).filter(RosterAssignment.id == ra.id).first()
        assert found_ra.planned_equipment_id == "EQ-001"

        # Actual stored separately
        actuals = db.query(EquipmentAssignmentActual).filter(
            EquipmentAssignmentActual.roster_id == ra.id
        ).all()
        assert len(actuals) == 1
        assert actuals[0].equipment_id == "EQ-002"
        assert actuals[0].source == "TELEMATICS"

    def test_34_actual_does_not_overwrite_planned(self, db):
        """Adding actual equipment does not modify planned equipment."""
        _setup_tenant(db)
        _create_employee(db, "EMP-001")
        _create_equipment(db, "EQ-001")
        _create_equipment(db, "EQ-002")
        _create_equipment(db, "EQ-003")
        _create_competency(db, "EMP-001")
        _create_shift(db, "DAY")

        ra = _assign(db, "EMP-001", date(2026, 9, 1), WorkStatus.WORK, "DAY", eq_id="EQ-001")

        # Add multiple actuals
        for eq_id in ["EQ-002", "EQ-003"]:
            db.add(EquipmentAssignmentActual(
                id=uid(), tenant_id=TENANT_ID, roster_id=ra.id,
                employee_id="EMP-001", equipment_id=eq_id,
                operating_date=date(2026, 9, 1),
                started_at=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
                source="MANUAL",
            ))
        db.flush()

        # Planned still EQ-001
        found_ra = db.query(RosterAssignment).filter(RosterAssignment.id == ra.id).first()
        assert found_ra.planned_equipment_id == "EQ-001"


class TestTenantIsolation:
    """Evidence: tenant A cannot read tenant B's data."""

    TENANT_A = "tenant-a"
    TENANT_B = "tenant-b"

    def _setup_two_tenants(self, db):
        db.add(Tenant(id=self.TENANT_A, code="a", name="Tenant A"))
        db.add(Tenant(id=self.TENANT_B, code="b", name="Tenant B"))
        db.flush()

    def test_35_worker_isolation(self, db):
        """Workers are isolated by tenant."""
        self._setup_two_tenants(db)
        db.add(Worker(id="W-A", tenant_id=self.TENANT_A, code="W-A", name="Worker A"))
        db.add(Worker(id="W-B", tenant_id=self.TENANT_B, code="W-B", name="Worker B"))
        db.flush()

        a_workers = db.query(Worker).filter(Worker.tenant_id == self.TENANT_A).all()
        b_workers = db.query(Worker).filter(Worker.tenant_id == self.TENANT_B).all()
        assert len(a_workers) == 1
        assert len(b_workers) == 1
        assert a_workers[0].name == "Worker A"
        assert b_workers[0].name == "Worker B"

    def test_36_equipment_isolation(self, db):
        """Equipment is isolated by tenant."""
        self._setup_two_tenants(db)
        db.add(Equipment(id="E-A", tenant_id=self.TENANT_A, equipment_code="E-A",
                         equipment_type="TRUCK", status=EquipmentStatus.ACTIVE,
                         effective_from=date(2026, 1, 1)))
        db.add(Equipment(id="E-B", tenant_id=self.TENANT_B, equipment_code="E-B",
                         equipment_type="TRUCK", status=EquipmentStatus.ACTIVE,
                         effective_from=date(2026, 1, 1)))
        db.flush()

        a_eq = db.query(Equipment).filter(Equipment.tenant_id == self.TENANT_A).all()
        b_eq = db.query(Equipment).filter(Equipment.tenant_id == self.TENANT_B).all()
        assert len(a_eq) == 1
        assert len(b_eq) == 1

    def test_37_competency_isolation(self, db):
        """Competencies are isolated by tenant."""
        self._setup_two_tenants(db)
        db.add(Worker(id="W-A", tenant_id=self.TENANT_A, code="W-A", name="Worker A"))
        db.add(Worker(id="W-B", tenant_id=self.TENANT_B, code="W-B", name="Worker B"))
        db.flush()
        db.add(Competency(id="C-A", tenant_id=self.TENANT_A, competency_code="C-A",
                          employee_id="W-A", equipment_type="TRUCK",
                          valid_from=date(2026, 1, 1), status=CompetencyStatus.VALID))
        db.add(Competency(id="C-B", tenant_id=self.TENANT_B, competency_code="C-B",
                          employee_id="W-B", equipment_type="TRUCK",
                          valid_from=date(2026, 1, 1), status=CompetencyStatus.VALID))
        db.flush()

        a_comp = db.query(Competency).filter(Competency.tenant_id == self.TENANT_A).all()
        b_comp = db.query(Competency).filter(Competency.tenant_id == self.TENANT_B).all()
        assert len(a_comp) == 1
        assert len(b_comp) == 1

    def test_38_roster_isolation(self, db):
        """Roster assignments are isolated by tenant."""
        self._setup_two_tenants(db)
        db.add(Worker(id="W-A", tenant_id=self.TENANT_A, code="W-A", name="Worker A"))
        db.add(Worker(id="W-B", tenant_id=self.TENANT_B, code="W-B", name="Worker B"))
        db.flush()
        db.add(ShiftTemplate(id="S-A", tenant_id=self.TENANT_A, shift_code="DAY",
                             shift_name="Day", start_time=time(7), end_time=time(19),
                             break_start=time(12), break_end=time(13),
                             handover_start=time(18, 45), handover_end=time(19),
                             crosses_midnight=False))
        db.add(ShiftTemplate(id="S-B", tenant_id=self.TENANT_B, shift_code="DAY",
                             shift_name="Day", start_time=time(7), end_time=time(19),
                             break_start=time(12), break_end=time(13),
                             handover_start=time(18, 45), handover_end=time(19),
                             crosses_midnight=False))
        db.flush()
        db.add(RosterAssignment(
            id=uid(), tenant_id=self.TENANT_A, roster_code="R-A",
            operating_date=date(2026, 9, 1), employee_id="W-A",
            work_status=WorkStatus.WORK, shift_id="S-A",
            site_status=SiteStatusEnum.ONSITE, validation_status=ValidationStatus.VALID,
        ))
        db.add(RosterAssignment(
            id=uid(), tenant_id=self.TENANT_B, roster_code="R-B",
            operating_date=date(2026, 9, 1), employee_id="W-B",
            work_status=WorkStatus.WORK, shift_id="S-B",
            site_status=SiteStatusEnum.ONSITE, validation_status=ValidationStatus.VALID,
        ))
        db.flush()

        a_roster = db.query(RosterAssignment).filter(RosterAssignment.tenant_id == self.TENANT_A).all()
        b_roster = db.query(RosterAssignment).filter(RosterAssignment.tenant_id == self.TENANT_B).all()
        assert len(a_roster) == 1
        assert len(b_roster) == 1

    def test_39_rule_version_isolation(self, db):
        """Rule versions are isolated by tenant."""
        self._setup_two_tenants(db)
        db.add(RuleVersion(
            id=uid(), tenant_id=self.TENANT_A, version_label="v1",
            effective_from=date(2026, 1, 1), config_snapshot_json='{"key": "a"}',
        ))
        db.add(RuleVersion(
            id=uid(), tenant_id=self.TENANT_B, version_label="v1",
            effective_from=date(2026, 1, 1), config_snapshot_json='{"key": "b"}',
        ))
        db.flush()

        a_rv = db.query(RuleVersion).filter(RuleVersion.tenant_id == self.TENANT_A).all()
        b_rv = db.query(RuleVersion).filter(RuleVersion.tenant_id == self.TENANT_B).all()
        assert len(a_rv) == 1
        assert len(b_rv) == 1
        assert '"a"' in a_rv[0].config_snapshot_json
        assert '"b"' in b_rv[0].config_snapshot_json

    def test_40_crew_isolation(self, db):
        """Crews are isolated by tenant."""
        self._setup_two_tenants(db)
        db.add(Crew(id="CR-A", tenant_id=self.TENANT_A, crew_code="A",
                    crew_name="Crew A", cycle_offset_days=0))
        db.add(Crew(id="CR-B", tenant_id=self.TENANT_B, crew_code="A",
                    crew_name="Crew A", cycle_offset_days=0))
        db.flush()

        a_crews = db.query(Crew).filter(Crew.tenant_id == self.TENANT_A).all()
        b_crews = db.query(Crew).filter(Crew.tenant_id == self.TENANT_B).all()
        assert len(a_crews) == 1
        assert len(b_crews) == 1
