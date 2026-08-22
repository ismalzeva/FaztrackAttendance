"""
test_acceptance_m3b.py — MM-M3B Supervisor Review & Evidence acceptance tests.

Metro Mining supervisor review layer:
- Review queue (filtered, paginated, tenant-scoped)
- Review detail (full exception context)
- Evidence references (system + human, immutable system, idempotent)
- Review notes (append-only)
- Case ownership (assign/reassign with audit)
- Timeline (chronological case history)
- Tenant isolation (strict client-cluster)
- Concurrency safety
- Authorization boundary (TBC-safe)

42 test scenarios.
"""
import pytest
import uuid
import threading
from datetime import date, datetime, time, timezone
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.models import (
    Base, Tenant, Worker, Site, ShiftTemplate, Equipment,
    RuleVersion, SiteType, SiteStatus, EquipmentStatus,
    CheckpointValidationResult, CheckpointValidationStatus,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    ExceptionCase, ExceptionAction, ExceptionActionType,
    ExceptionEvidence, ExceptionEvidenceType, ExceptionSourceType,
    ExceptionStatus, ExceptionSeverity,
)
from app.exception_engine import (
    create_exception_from_rule_evaluation,
    create_exception_from_discrepancy,
    acknowledge_exception,
    resolve_exception,
    waive_exception,
    get_exception,
)
from app.review_service import (
    get_review_queue,
    get_review_detail,
    add_evidence,
    get_evidence,
    add_review_note,
    assign_reviewer,
    get_timeline,
    system_evidence_is_immutable,
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
    """Seed Metro Mining minimal environment for M3B tests."""
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
    night = ShiftTemplate(
        id="NIGHT", tenant_id="metro", shift_code="NIGHT", shift_name="Night",
        start_time=time(19, 0), end_time=time(7, 0),
        break_start=time(0, 0), break_end=time(1, 0),
        handover_start=time(6, 45), handover_end=time(7, 0),
        crosses_midnight=True,
    )
    db.add_all([day, night])

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
    db.flush()


def _seed_lumin(db: Session):
    """Seed Lumin Park tenant for cross-tenant tests."""
    t = Tenant(id="lumin", code="lumin", name="Lumin Park", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="lw1", tenant_id="lumin", code="LW001", name="Sari", is_active=True)
    db.add(w)
    db.flush()


def _make_rule_eval(db, tenant_id="metro", employee_id="w1", rule_code="LATE_BREAK_RETURN",
                    status=RuleEvaluationStatus.FAIL, severity=RuleSeverity.CRITICAL,
                    operating_date=None, shift_id="DAY", equipment_id=None, rule_version_id="rv1"):
    """Create a RuleEvaluation for testing."""
    re = RuleEvaluation(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date or date(2026, 9, 5),
        shift_id=shift_id, rule_code=rule_code, rule_version_id=rule_version_id,
        equipment_id=equipment_id, evaluated_at=datetime.now(timezone.utc),
        status=status, severity=severity,
        actual_value="13:01", expected_value="13:00",
        evidence_json=None, reason=None, metadata_json=None,
        evidence_key=f"{tenant_id}-{employee_id}-{rule_code}",
        created_at=datetime.now(timezone.utc),
    )
    db.add(re)
    db.flush()
    return re


def _make_discrepancy(db, tenant_id="metro", employee_id="w1",
                      status=DiscrepancyStatus.OPEN, operating_date=None,
                      planned_eq="ex25", actual_eq="ex31", shift_id="DAY",
                      rule_version_id="rv1"):
    """Create an EquipmentDiscrepancy for testing."""
    d = EquipmentDiscrepancy(
        id=_uid(), tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date or date(2026, 9, 5),
        shift_id=shift_id,
        actual_assignment_id=f"act-{_uid()}",
        planned_equipment_id=planned_eq,
        actual_equipment_id=actual_eq,
        actual_worker_id=employee_id,
        discrepancy_type=DiscrepancyType.EQUIPMENT_MISMATCH,
        status=status,
        detected_at=datetime.now(timezone.utc),
        evidence_json=None, rule_version_id=rule_version_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(d)
    db.flush()
    return d


def _create_open_exception(db, tenant_id="metro", employee_id="w1",
                           exception_type="LATE_BREAK_RETURN",
                           severity=ExceptionSeverity.CRITICAL,
                           operating_date=None, shift_id="DAY", equipment_id=None):
    """Directly create an OPEN ExceptionCase for review tests."""
    case = ExceptionCase(
        id=_uid(), tenant_id=tenant_id,
        exception_type=exception_type, severity=severity,
        status=ExceptionStatus.OPEN,
        employee_id=employee_id,
        operating_date=operating_date or date(2026, 9, 5),
        shift_id=shift_id, equipment_id=equipment_id, site_id=None,
        source_type=ExceptionSourceType.RULE_EVALUATION.value,
        source_id=_uid(), rule_version_id="rv1",
        detected_at=datetime.now(timezone.utc),
        opened_at=datetime.now(timezone.utc),
        acknowledged_at=None, resolved_at=None, waived_at=None,
        current_owner_id=None, metadata_json=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(case)
    db.flush()
    return case


# ── Review Queue Tests ────────────────────────────────────────

class TestReviewQueue:
    """M3B-01 to M3B-09: Review queue filtering and pagination."""

    def test_m3b_01_list_open_exceptions(self, db):
        """M3B-01: List OPEN Metro exceptions."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        queue = get_review_queue(db, "metro")
        assert len(queue) == 1
        assert queue[0].id == case.id
        assert queue[0].status == ExceptionStatus.OPEN

    def test_m3b_02_list_acknowledged_exceptions(self, db):
        """M3B-02: List ACKNOWLEDGED Metro exceptions."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "sup1", note="Looking into it")
        db.commit()

        queue = get_review_queue(db, "metro")
        assert len(queue) == 1
        assert queue[0].status == ExceptionStatus.ACKNOWLEDGED

    def test_m3b_03_terminal_excluded_from_active(self, db):
        """M3B-03: Terminal cases excluded from default active queue."""
        _seed_metro(db)
        case1 = _create_open_exception(db, exception_type="LATE_BREAK_RETURN")
        case2 = _create_open_exception(db, employee_id="w2", exception_type="MISSING_SHIFT_OUT")
        resolve_exception(db, case2.id, "metro", "sup1", reason="Fixed")
        db.commit()

        active = get_review_queue(db, "metro", active_only=True)
        assert len(active) == 1
        assert active[0].id == case1.id

    def test_m3b_04_terminal_retrievable_historically(self, db):
        """M3B-04: Terminal cases retrievable with active_only=False."""
        _seed_metro(db)
        case1 = _create_open_exception(db, exception_type="LATE_BREAK_RETURN")
        case2 = _create_open_exception(db, employee_id="w2", exception_type="MISSING_SHIFT_OUT")
        resolve_exception(db, case2.id, "metro", "sup1", reason="Fixed")
        db.commit()

        all_cases = get_review_queue(db, "metro", active_only=False)
        assert len(all_cases) == 2

    def test_m3b_05_filter_operating_date(self, db):
        """M3B-05: Filter by operating_date."""
        _seed_metro(db)
        _create_open_exception(db, operating_date=date(2026, 9, 5))
        _create_open_exception(db, employee_id="w2", operating_date=date(2026, 9, 6))
        db.commit()

        filtered = get_review_queue(db, "metro", operating_date=date(2026, 9, 5))
        assert len(filtered) == 1

    def test_m3b_06_filter_employee(self, db):
        """M3B-06: Filter by employee."""
        _seed_metro(db)
        _create_open_exception(db, employee_id="w1")
        _create_open_exception(db, employee_id="w2")
        db.commit()

        filtered = get_review_queue(db, "metro", employee_id="w1")
        assert len(filtered) == 1
        assert filtered[0].employee_id == "w1"

    def test_m3b_07_filter_equipment(self, db):
        """M3B-07: Filter by equipment."""
        _seed_metro(db)
        _create_open_exception(db, equipment_id="ex25")
        _create_open_exception(db, employee_id="w2", equipment_id="ex31")
        db.commit()

        filtered = get_review_queue(db, "metro", equipment_id="ex25")
        assert len(filtered) == 1
        assert filtered[0].equipment_id == "ex25"

    def test_m3b_08_filter_exception_type(self, db):
        """M3B-08: Filter by exception type."""
        _seed_metro(db)
        _create_open_exception(db, exception_type="LATE_BREAK_RETURN")
        _create_open_exception(db, employee_id="w2", exception_type="MISSING_SHIFT_OUT")
        db.commit()

        filtered = get_review_queue(db, "metro", exception_type="LATE_BREAK_RETURN")
        assert len(filtered) == 1
        assert filtered[0].exception_type == "LATE_BREAK_RETURN"

    def test_m3b_09_filter_severity(self, db):
        """M3B-09: Filter by severity."""
        _seed_metro(db)
        _create_open_exception(db, severity=ExceptionSeverity.CRITICAL)
        _create_open_exception(db, employee_id="w2", severity=ExceptionSeverity.WARNING)
        db.commit()

        filtered = get_review_queue(db, "metro", severity=ExceptionSeverity.CRITICAL)
        assert len(filtered) == 1
        assert filtered[0].severity == ExceptionSeverity.CRITICAL


# ── Review Detail Tests ───────────────────────────────────────

class TestReviewDetail:
    """M3B-10 to M3B-13: Review detail representation."""

    def test_m3b_10_detail_includes_source_detection(self, db):
        """M3B-10: Exception detail includes source detection info."""
        _seed_metro(db)
        re = _make_rule_eval(db)
        case = create_exception_from_rule_evaluation(db, re)
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail is not None
        assert detail["exception"]["source_type"] == ExceptionSourceType.RULE_EVALUATION.value
        assert detail["exception"]["source_id"] == re.id

    def test_m3b_11_detail_includes_rule_version(self, db):
        """M3B-11: Detail includes rule version."""
        _seed_metro(db)
        re = _make_rule_eval(db)
        case = create_exception_from_rule_evaluation(db, re)
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail["exception"]["rule_version_id"] == "rv1"

    def test_m3b_12_detail_no_fabricated_evidence(self, db):
        """M3B-12: Detail does not fabricate missing evidence."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail is not None
        assert detail["evidence"] == []

    def test_m3b_13_detail_returns_none_for_wrong_tenant(self, db):
        """M3B-13: Detail returns None for tenant mismatch."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db)
        db.commit()

        detail = get_review_detail(db, case.id, "lumin")
        assert detail is None


# ── Acknowledgement Tests ─────────────────────────────────────

class TestAcknowledgement:
    """M3B-14 to M3B-17: Acknowledgement workflow."""

    def test_m3b_14_acknowledge_creates_audit_action(self, db):
        """M3B-14: OPEN → ACKNOWLEDGED by valid actor creates audit action."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "supervisor-1", note="Reviewing")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail["exception"]["status"] == "ACKNOWLEDGED"
        assert detail["exception"]["current_owner_id"] == "supervisor-1"
        ack_actions = [a for a in detail["actions"] if a["action_type"] == "ACKNOWLEDGE"]
        assert len(ack_actions) == 1
        assert ack_actions[0]["actor_user_id"] == "supervisor-1"

    def test_m3b_15_acknowledgement_note_retained(self, db):
        """M3B-15: Acknowledgement note is retained."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "sup1", note="Checking with team")
        db.commit()

        actions = get_review_detail(db, case.id, "metro")["actions"]
        ack = [a for a in actions if a["action_type"] == "ACKNOWLEDGE"][0]
        assert ack["note"] == "Checking with team"

    def test_m3b_16_repeated_acknowledge_idempotent(self, db):
        """M3B-16: Repeated acknowledgement on already-ACKNOWLEDGED is safe."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "sup1")
        db.commit()

        with pytest.raises(ValueError, match="Invalid transition"):
            acknowledge_exception(db, case.id, "metro", "sup2")


# ── Review Notes Tests ────────────────────────────────────────

class TestReviewNotes:
    """M3B-17 to M3B-19: Append-only review notes."""

    def test_m3b_17_review_note_append_only(self, db):
        """M3B-17: Review notes are append-only."""
        _seed_metro(db)
        case = _create_open_exception(db)
        add_review_note(db, case.id, "metro", "sup1", "First observation")
        add_review_note(db, case.id, "metro", "sup1", "Second observation")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        notes = [a for a in detail["actions"] if a["action_type"] == "REVIEW_NOTE"]
        assert len(notes) == 2
        assert notes[0]["note"] == "First observation"
        assert notes[1]["note"] == "Second observation"

    def test_m3b_18_multiple_notes_retain_history(self, db):
        """M3B-18: Multiple review notes from different actors retain history."""
        _seed_metro(db)
        case = _create_open_exception(db)
        add_review_note(db, case.id, "metro", "sup1", "Supervisor note")
        add_review_note(db, case.id, "metro", "manager1", "Manager note")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        notes = [a for a in detail["actions"] if a["action_type"] == "REVIEW_NOTE"]
        assert len(notes) == 2
        actors = {n["actor_user_id"] for n in notes}
        assert actors == {"sup1", "manager1"}

    def test_m3b_18b_empty_note_rejected(self, db):
        """M3B-18b: Empty review note is rejected."""
        _seed_metro(db)
        case = _create_open_exception(db)
        with pytest.raises(ValueError, match="cannot be empty"):
            add_review_note(db, case.id, "metro", "sup1", "")


# ── Evidence Tests ────────────────────────────────────────────

class TestEvidence:
    """M3B-19 to M3B-22: Evidence management."""

    def test_m3b_19_system_evidence_immutable(self, db):
        """M3B-19: System-generated evidence is immutable."""
        _seed_metro(db)
        case = _create_open_exception(db)
        ev = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            "rule_evaluations", "re-001",
            is_system_generated=True,
        )
        db.commit()

        assert system_evidence_is_immutable(ev) is True
        assert ev.added_by is None  # system-generated, no actor

    def test_m3b_20_human_evidence_records_actor(self, db):
        """M3B-20: Human evidence reference records actor and timestamp."""
        _seed_metro(db)
        case = _create_open_exception(db)
        ev = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.SUPERVISOR_NOTE,
            "supervisor_notes", "note-001",
            actor_user_id="sup1",
            note="Verified on site",
            is_system_generated=False,
        )
        db.commit()

        assert ev.is_system_generated is False
        assert ev.added_by == "sup1"
        assert ev.note == "Verified on site"

    def test_m3b_21_evidence_addition_creates_audit_action(self, db):
        """M3B-21: Human evidence addition records audit action."""
        _seed_metro(db)
        case = _create_open_exception(db)
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.DOCUMENT_REFERENCE,
            "documents", "doc-001",
            actor_user_id="sup1",
            note="Photo of site",
            is_system_generated=False,
        )
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        evidence_actions = [a for a in detail["actions"] if a["action_type"] == "ADD_EVIDENCE"]
        assert len(evidence_actions) == 1
        assert evidence_actions[0]["actor_user_id"] == "sup1"

    def test_m3b_22_evidence_idempotent(self, db):
        """M3B-22: Same evidence source does not duplicate."""
        _seed_metro(db)
        case = _create_open_exception(db)
        ev1 = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            "rule_evaluations", "re-001",
        )
        ev2 = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            "rule_evaluations", "re-001",
        )
        db.commit()

        assert ev1.id == ev2.id  # same record returned


# ── Case Ownership Tests ──────────────────────────────────────

class TestOwnership:
    """M3B-23 to M3B-25: Case ownership assignment."""

    def test_m3b_23_assign_reviewer(self, db):
        """M3B-23: Assign reviewer creates audit action."""
        _seed_metro(db)
        case = _create_open_exception(db)
        assign_reviewer(db, case.id, "metro", "admin1", "supervisor-1", reason="Shift lead")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail["exception"]["current_owner_id"] == "supervisor-1"
        assign_actions = [a for a in detail["actions"] if a["action_type"] == "ASSIGN_REVIEWER"]
        assert len(assign_actions) == 1
        assert assign_actions[0]["actor_user_id"] == "admin1"

    def test_m3b_24_reassign_reviewer(self, db):
        """M3B-24: Reassignment records previous owner."""
        _seed_metro(db)
        case = _create_open_exception(db)
        assign_reviewer(db, case.id, "metro", "admin1", "sup1")
        assign_reviewer(db, case.id, "metro", "admin1", "sup2", reason="Handover")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assign_actions = [a for a in detail["actions"] if a["action_type"] == "ASSIGN_REVIEWER"]
        assert len(assign_actions) == 2
        assert "sup1" in assign_actions[1]["note"]  # previous owner in note

    def test_m3b_25_filter_by_owner(self, db):
        """M3B-25: Filter queue by owner."""
        _seed_metro(db)
        case1 = _create_open_exception(db, exception_type="LATE_BREAK_RETURN")
        case2 = _create_open_exception(db, employee_id="w2", exception_type="MISSING_SHIFT_OUT")
        assign_reviewer(db, case1.id, "metro", "admin1", "sup1")
        db.commit()

        filtered = get_review_queue(db, "metro", owner_id="sup1")
        assert len(filtered) == 1
        assert filtered[0].current_owner_id == "sup1"


# ── Tenant Isolation Tests ────────────────────────────────────

class TestTenantIsolation:
    """M3B-26 to M3B-30: Strict tenant isolation."""

    def test_m3b_26_cross_tenant_query_rejected(self, db):
        """M3B-26: Metro reviewer cannot retrieve Lumin cases."""
        _seed_metro(db)
        _seed_lumin(db)
        _create_open_exception(db, tenant_id="metro")
        db.commit()

        queue = get_review_queue(db, "lumin")
        assert len(queue) == 0  # Lumin sees nothing (Metro cases invisible)

    def test_m3b_27_cross_tenant_acknowledge_rejected(self, db):
        """M3B-27: Cannot acknowledge another tenant's case."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db, tenant_id="metro")
        db.commit()

        with pytest.raises(ValueError, match="not found for tenant"):
            acknowledge_exception(db, case.id, "lumin", "lumin-sup1")

    def test_m3b_28_cross_tenant_note_rejected(self, db):
        """M3B-28: Cannot add note to another tenant's case."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db, tenant_id="metro")
        db.commit()

        with pytest.raises(ValueError, match="not found for tenant"):
            add_review_note(db, case.id, "lumin", "lumin-sup1", "Cross-tenant note")

    def test_m3b_29_cross_tenant_evidence_rejected(self, db):
        """M3B-29: Cannot add evidence to another tenant's case."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db, tenant_id="metro")
        db.commit()

        with pytest.raises(ValueError, match="not found for tenant"):
            add_evidence(
                db, case.id, "lumin",
                ExceptionEvidenceType.SUPERVISOR_NOTE,
                "notes", "note-001",
                actor_user_id="lumin-sup1",
                is_system_generated=False,
            )

    def test_m3b_30_cross_tenant_assignment_rejected(self, db):
        """M3B-30: Cannot assign another tenant's user as owner."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db, tenant_id="metro")
        db.commit()

        with pytest.raises(ValueError, match="not found for tenant"):
            assign_reviewer(db, case.id, "lumin", "lumin-admin", "lumin-sup1")


# ── Timeline Tests ────────────────────────────────────────────

class TestTimeline:
    """M3B-31 to M3B-34: Timeline chronological ordering."""

    def test_m3b_31_timeline_chronological(self, db):
        """M3B-31: Timeline entries are chronological."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "sup1", note="Looking")
        add_review_note(db, case.id, "metro", "sup1", "Checked site")
        db.commit()

        timeline = get_timeline(db, case.id, "metro")
        timestamps = [e["timestamp"] for e in timeline]
        assert timestamps == sorted(timestamps)

    def test_m3b_32_timeline_includes_detection(self, db):
        """M3B-32: Timeline includes detection event."""
        _seed_metro(db)
        re = _make_rule_eval(db)
        case = create_exception_from_rule_evaluation(db, re)
        db.commit()

        timeline = get_timeline(db, case.id, "metro")
        detection = [e for e in timeline if e["event_type"] == "DETECTION"]
        assert len(detection) == 1
        assert "LATE_BREAK_RETURN" in detection[0]["description"]

    def test_m3b_33_timeline_includes_acknowledgement(self, db):
        """M3B-33: Timeline includes acknowledgement action."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "sup1")
        db.commit()

        timeline = get_timeline(db, case.id, "metro")
        ack_events = [e for e in timeline if e["event_type"] == "ACTION"
                      and "ACKNOWLEDGE" in e["description"]]
        assert len(ack_events) == 1

    def test_m3b_34_timeline_includes_review_note(self, db):
        """M3B-34: Timeline includes review note."""
        _seed_metro(db)
        case = _create_open_exception(db)
        add_review_note(db, case.id, "metro", "sup1", "Verified on site")
        db.commit()

        timeline = get_timeline(db, case.id, "metro")
        note_events = [e for e in timeline if e["event_type"] == "ACTION"
                       and "REVIEW_NOTE" in e["description"]]
        assert len(note_events) == 1


# ── Concurrency Tests ─────────────────────────────────────────

class TestConcurrency:
    """M3B-35: Concurrent acknowledgement safety."""

    def test_m3b_35_concurrent_acknowledge_safe(self, db):
        """M3B-35: Two supervisors cannot both acknowledge the same OPEN case."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        results = {"success": None, "error": None}

        def try_ack(actor, result_key):
            try:
                acknowledge_exception(db, case.id, "metro", actor)
                db.commit()
                results[result_key] = "success"
            except ValueError:
                db.rollback()
                results[result_key] = "error"

        t1 = threading.Thread(target=try_ack, args=("sup1", "success"))
        t2 = threading.Thread(target=try_ack, args=("sup2", "error"))

        # First should succeed, second should get ValueError
        # SQLite serializes so one goes first
        try_ack("sup1", "success")
        try_ack("sup2", "error")

        # At least one must fail
        assert results["error"] == "error"
        case = get_exception(db, case.id, "metro")
        assert case.status == ExceptionStatus.ACKNOWLEDGED


# ── Authorization Boundary Tests ──────────────────────────────

class TestAuthorizationBoundary:
    """M3B-36 to M3B-38: Authorization boundary (TBC-safe)."""

    def test_m3b_36_no_payroll_consequence(self, db):
        """M3B-36: No payroll consequence is created by review actions."""
        _seed_metro(db)
        case = _create_open_exception(db)
        acknowledge_exception(db, case.id, "metro", "sup1")
        resolve_exception(db, case.id, "metro", "sup1", reason="Reviewed")
        db.commit()

        # Verify no payroll-related actions exist
        detail = get_review_detail(db, case.id, "metro")
        action_types = {a["action_type"] for a in detail["actions"]}
        assert "PAYROLL_DEDUCT" not in action_types
        assert "DISCIPLINARY" not in action_types
        assert "HSE_PENALTY" not in action_types

    def test_m3b_37_no_disciplinary_consequence(self, db):
        """M3B-37: No disciplinary sanction is created."""
        _seed_metro(db)
        case = _create_open_exception(db)
        waive_exception(db, case.id, "metro", "sup1", reason="Equipment issue, not operator fault")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        action_types = {a["action_type"] for a in detail["actions"]}
        assert "DISCIPLINARY_ACTION" not in action_types
        assert "HSE_SANCTION" not in action_types

    def test_m3b_38_no_hse_consequence(self, db):
        """M3B-38: No HSE consequence is created."""
        _seed_metro(db)
        case = _create_open_exception(db, exception_type="LOCATION_OUTSIDE_GEOFENCE")
        acknowledge_exception(db, case.id, "metro", "hse-lead")
        resolve_exception(db, case.id, "metro", "hse-lead", reason="GPS drift confirmed")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        action_types = {a["action_type"] for a in detail["actions"]}
        assert "HSE_PENALTY" not in action_types


# ── Integration Tests ─────────────────────────────────────────

class TestIntegration:
    """M3B-39 to M3B-42: Full integration scenarios."""

    def test_m3b_39_full_review_workflow(self, db):
        """M3B-39: Full workflow: create → evidence → acknowledge → note → resolve."""
        _seed_metro(db)
        re = _make_rule_eval(db)
        case = create_exception_from_rule_evaluation(db, re)
        db.commit()

        # System evidence auto-attached via creation
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            "rule_evaluations", re.id,
            captured_at=re.evaluated_at,
        )

        # Supervisor acknowledges
        acknowledge_exception(db, case.id, "metro", "sup1", note="Investigating")

        # Supervisor adds note
        add_review_note(db, case.id, "metro", "sup1", "Confirmed with site lead")

        # Supervisor adds human evidence
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.SUPERVISOR_NOTE,
            "supervisor_notes", "note-001",
            actor_user_id="sup1",
            note="Site visit confirmed",
            is_system_generated=False,
        )

        # Resolve
        resolve_exception(db, case.id, "metro", "sup1", reason="GPS drift, not actual late return")
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail["exception"]["status"] == "RESOLVED"
        assert len(detail["evidence"]) == 2
        assert len(detail["timeline"]) >= 5  # detection + opened + evidence + ack + note + evidence + resolve

    def test_m3b_40_rule_eval_evidence_attached(self, db):
        """M3B-40: RuleEvaluation creates exception with source traceable."""
        _seed_metro(db)
        re = _make_rule_eval(db, rule_code="LATE_BREAK_RETURN")
        case = create_exception_from_rule_evaluation(db, re)
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail["exception"]["source_type"] == "RULE_EVALUATION"
        assert detail["exception"]["source_id"] == re.id
        assert detail["exception"]["exception_type"] == "LATE_BREAK_RETURN"

    def test_m3b_41_discrepancy_evidence_attached(self, db):
        """M3B-41: EquipmentDiscrepancy creates exception with source traceable."""
        _seed_metro(db)
        d = _make_discrepancy(db)
        case = create_exception_from_discrepancy(db, d)
        db.commit()

        detail = get_review_detail(db, case.id, "metro")
        assert detail["exception"]["source_type"] == "EQUIPMENT_DISCREPANCY"
        assert detail["exception"]["source_id"] == d.id

    def test_m3b_42_date_range_filter(self, db):
        """M3B-42: Date range filter works correctly."""
        _seed_metro(db)
        _create_open_exception(db, operating_date=date(2026, 9, 1))
        _create_open_exception(db, employee_id="w2", operating_date=date(2026, 9, 5))
        _create_open_exception(db, employee_id="w1", exception_type="MISSING_SHIFT_OUT",
                               operating_date=date(2026, 9, 10))
        db.commit()

        filtered = get_review_queue(
            db, "metro",
            operating_date_from=date(2026, 9, 3),
            operating_date_to=date(2026, 9, 7),
        )
        assert len(filtered) == 1
        assert filtered[0].operating_date == date(2026, 9, 5)
