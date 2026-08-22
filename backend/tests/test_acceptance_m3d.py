"""
test_acceptance_m3d.py — MM-M3D End-to-End Supervisor Control & M3 Closure.

Integration tests proving M3A + M3B + M3C work as one complete, auditable
supervisor control workflow for Metro Mining.

Scenarios A–O plus invariant tests.
"""

import pytest
import uuid
from datetime import date, datetime, time, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.models import (
    Base, Tenant, Worker, Equipment, EquipmentStatus, Competency, CompetencyStatus,
    Site, SiteType, SiteStatus, Crew, Role,
    RosterAssignment, ShiftTemplate, RuleVersion,
    EquipmentAssignmentActual, EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    CheckpointValidationResult, CheckpointValidationStatus,
    ExceptionCase, ExceptionAction, ExceptionActionType, ExceptionStatus, ExceptionSeverity,
    ExceptionSourceType, EXCEPTION_TRANSITIONS,
    ExceptionEvidence, ExceptionEvidenceType,
    ExceptionDecision, ExceptionDecisionAction, DecisionType, DecisionStatus, DECISION_TRANSITIONS,
    uid, now, WorkStatus,
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

from app.review_service import (
    get_review_queue,
    get_review_detail,
    add_evidence,
    add_review_note,
    assign_reviewer,
    get_evidence,
    get_timeline,
    system_evidence_is_immutable,
)

from app.decision_engine import (
    request_decision,
    approve_decision,
    reject_decision,
    cancel_decision,
    get_decision,
    get_decisions_for_exception,
    get_decision_history,
    InvalidDecisionTransition,
    DecisionValidationFailed,
    AuthorizationBlocked,
    DuplicateActiveDecision,
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

def _seed_metro(db: Session):
    """Seed Metro Mining environment for M3D integration tests."""
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)

    # Workers
    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Tono", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Budi", is_active=True)
    w3_inactive = Worker(id="w3_inactive", tenant_id="metro", code="W003", name="Cakra", is_active=False)
    w4_offsite = Worker(id="w4_offsite", tenant_id="metro", code="W004", name="Dodi", is_active=True)
    db.add_all([w1, w2, w3_inactive, w4_offsite])

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
    ex_broken = Equipment(
        id="ex_broken", tenant_id="metro", equipment_code="EX-99",
        equipment_type="EXCAVATOR", status=EquipmentStatus.OUT_OF_SERVICE,
        effective_from=date(2026, 1, 1),
    )
    db.add_all([ex25, ex31, ex_broken])

    # Competencies — w1 and w2 have VALID; w1 also has an EXPIRED record
    comp_w1 = Competency(
        id="comp_w1", tenant_id="metro", competency_code="EXC-A",
        employee_id="w1", equipment_type="EXCAVATOR",
        valid_from=date(2026, 1, 1),
        valid_to=date.today() + timedelta(days=365),
        status=CompetencyStatus.VALID,
    )
    comp_w2 = Competency(
        id="comp_w2", tenant_id="metro", competency_code="EXC-A",
        employee_id="w2", equipment_type="EXCAVATOR",
        valid_from=date(2026, 1, 1),
        valid_to=date.today() + timedelta(days=365),
        status=CompetencyStatus.VALID,
    )
    comp_w1_expired = Competency(
        id="comp_w1_expired", tenant_id="metro", competency_code="EXC-A",
        employee_id="w1", equipment_type="EXCAVATOR",
        valid_from=date(2025, 1, 1),
        valid_to=date.today() - timedelta(days=30),
        status=CompetencyStatus.EXPIRED,
    )
    db.add_all([comp_w1, comp_w2, comp_w1_expired])

    # Shift template
    day = ShiftTemplate(
        id="DAY", tenant_id="metro", shift_code="DAY", shift_name="Day",
        start_time=time(7, 0), end_time=time(19, 0),
        break_start=time(12, 0), break_end=time(13, 0),
        handover_start=time(18, 45), handover_end=time(19, 0),
        crosses_midnight=False,
    )
    db.add(day)

    # Site
    site_padang = Site(
        id="site_padang", tenant_id="metro", site_code="PADANG",
        site_name="Padang Mine", site_type=SiteType.MINE_SITE,
        status=SiteStatus.ACTIVE, timezone="Asia/Makassar",
        effective_from=date(2026, 1, 1),
    )
    db.add(site_padang)

    # Rule version
    rv = RuleVersion(
        id="rv1", tenant_id="metro", version_label="v1.0",
        effective_from=date(2026, 1, 1), config_snapshot_json="{}",
    )
    db.add(rv)

    db.flush()


def _seed_client_b(db: Session):
    """Seed Client B tenant for cross-tenant isolation tests."""
    t = Tenant(id="client_b", code="client_b", name="Other Company", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="w_other", tenant_id="client_b", code="CB001", name="Rina", is_active=True)
    eq = Equipment(
        id="ex_other", tenant_id="client_b", equipment_code="TR-001",
        equipment_type="TRUCK", status=EquipmentStatus.ACTIVE,
        effective_from=date(2026, 1, 1),
    )
    db.add_all([w, eq])
    db.flush()


# ── Test Helpers ──────────────────────────────────────────────

def _make_rule_eval(db, tenant_id="metro", employee_id="w1",
                    rule_code="LATE_BREAK_RETURN",
                    status=RuleEvaluationStatus.FAIL,
                    severity=RuleSeverity.WARNING,
                    operating_date=None, shift_id="DAY",
                    rule_version_id="rv1", equipment_id=None):
    """Create a RuleEvaluation with all required fields."""
    re = RuleEvaluation(
        id=_uid(),
        tenant_id=tenant_id,
        employee_id=employee_id,
        operating_date=operating_date or date.today(),
        shift_id=shift_id,
        rule_code=rule_code,
        rule_version_id=rule_version_id,
        equipment_id=equipment_id,
        evaluated_at=datetime.now(timezone.utc),
        status=status,
        severity=severity,
        actual_value="13:05",
        expected_value="13:00",
        evidence_key=f"{tenant_id}-{employee_id}-{rule_code}-{_uid()}",
        created_at=datetime.now(timezone.utc),
    )
    db.add(re)
    db.flush()
    return re


def _make_discrepancy(db, tenant_id="metro", employee_id="w1",
                      planned_equip_id="ex25", actual_equip_id="ex31",
                      planned_worker_id=None, actual_worker_id=None,
                      operating_date=None, shift_id="DAY",
                      discrepancy_type=DiscrepancyType.EQUIPMENT_MISMATCH,
                      rule_version_id="rv1"):
    """Create an EquipmentDiscrepancy with all required fields."""
    d = EquipmentDiscrepancy(
        id=_uid(),
        tenant_id=tenant_id,
        actual_assignment_id=f"act-{_uid()}",
        employee_id=employee_id,
        operating_date=operating_date or date.today(),
        shift_id=shift_id,
        planned_equipment_id=planned_equip_id,
        actual_equipment_id=actual_equip_id,
        planned_worker_id=planned_worker_id or employee_id,
        actual_worker_id=actual_worker_id or employee_id,
        discrepancy_type=discrepancy_type,
        detected_at=datetime.now(timezone.utc),
        status=DiscrepancyStatus.OPEN,
        rule_version_id=rule_version_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(d)
    db.flush()
    return d


def _create_open_exception(db, tenant_id="metro", employee_id="w1",
                           exception_type="LATE_BREAK_RETURN",
                           severity=ExceptionSeverity.WARNING,
                           operating_date=None, shift_id="DAY",
                           equipment_id=None, source_type=None, source_id=None):
    """Directly create an OPEN ExceptionCase for test setup."""
    case = ExceptionCase(
        id=_uid(),
        tenant_id=tenant_id,
        exception_type=exception_type,
        severity=severity,
        status=ExceptionStatus.OPEN,
        employee_id=employee_id,
        operating_date=operating_date or date.today(),
        shift_id=shift_id,
        equipment_id=equipment_id,
        site_id=None,
        source_type=source_type or ExceptionSourceType.RULE_EVALUATION.value,
        source_id=source_id or _uid(),
        rule_version_id="rv1",
        detected_at=datetime.now(timezone.utc),
        opened_at=datetime.now(timezone.utc),
        acknowledged_at=None,
        resolved_at=None,
        waived_at=None,
        current_owner_id=None,
        metadata_json=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(case)
    db.flush()
    return case


def _action_types(actions):
    """Extract action_type values from action list."""
    return [a.action_type.value if hasattr(a.action_type, "value") else str(a.action_type)
            for a in actions]


def _create_client_b_exception(db):
    """Create a Client B exception for cross-tenant tests."""
    op_date = date.today()
    re_b = RuleEvaluation(
        id=_uid(), tenant_id="client_b", employee_id="w_other",
        operating_date=op_date, shift_id=None,
        rule_code="LATE_BREAK_RETURN", rule_version_id=None,
        evaluated_at=datetime.now(timezone.utc),
        status=RuleEvaluationStatus.FAIL, severity=RuleSeverity.WARNING,
        actual_value="13:05", expected_value="13:00",
        evidence_key=f"client_b-w_other-LATE_BREAK_RETURN-{_uid()}",
        created_at=datetime.now(timezone.utc),
    )
    db.add(re_b)
    db.flush()
    return create_exception_from_rule_evaluation(db, re_b)


# ═══════════════════════════════════════════════════════════════
# SCENARIO A: Late Break Review and Resolution
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioALateBreak:
    """Full late-break workflow: detection → queue → acknowledge → evidence → resolve."""

    def test_m3d_scenario_a_late_break_resolution(self, db):
        """Complete late-break lifecycle from detection to resolution."""
        _seed_metro(db)
        op_date = date.today()

        # 1. Create RuleEvaluation for LATE_BREAK_RETURN with FAIL status
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL,
                             severity=RuleSeverity.WARNING,
                             operating_date=op_date, shift_id="DAY")

        # 2. create_exception_from_rule_evaluation → case created (OPEN)
        case = create_exception_from_rule_evaluation(db, re)
        assert case is not None
        assert case.status == ExceptionStatus.OPEN
        assert case.exception_type == "LATE_BREAK_RETURN"
        assert case.employee_id == "w1"

        # 3. Verify case appears in review queue
        queue = get_review_queue(db, "metro")
        queue_ids = [c.id for c in queue]
        assert case.id in queue_ids

        # 4. get_review_detail → full context
        detail = get_review_detail(db, case.id, "metro")
        assert detail is not None
        assert detail["exception"]["id"] == case.id
        assert detail["exception"]["status"] == "OPEN"
        assert detail["exception"]["employee_id"] == "w1"
        assert isinstance(detail["evidence"], list)
        assert isinstance(detail["actions"], list)
        assert isinstance(detail["timeline"], list)

        # 5. acknowledge_exception → ACKNOWLEDGED
        acked = acknowledge_exception(db, case.id, "metro", "supervisor_1",
                                       reason="Reviewing late break return")
        assert acked.status == ExceptionStatus.ACKNOWLEDGED
        assert acked.acknowledged_at is not None

        # 6. add_review_note
        note_action = add_review_note(db, case.id, "metro", "supervisor_1",
                                       "Supervisor confirmed late return from break")
        assert note_action.action_type == ExceptionActionType.REVIEW_NOTE

        # 7. add_evidence (SUPERVISOR_NOTE, human-added)
        ev = add_evidence(db, case.id, "metro",
                          ExceptionEvidenceType.SUPERVISOR_NOTE,
                          source_type="manual", source_id="note-001",
                          actor_user_id="supervisor_1",
                          is_system_generated=False,
                          note="Supervisor confirmed late return")
        assert ev is not None
        assert ev.is_system_generated is False
        assert ev.added_by == "supervisor_1"

        # 8. resolve_exception → RESOLVED
        resolved = resolve_exception(db, case.id, "metro", "supervisor_1",
                                      reason="Reviewed and resolved — worker counseling completed")
        assert resolved.status == ExceptionStatus.RESOLVED
        assert resolved.resolved_at is not None

        # 9. Verify: original RuleEvaluation.status still FAIL (never modified)
        db.refresh(re)
        assert re.status == RuleEvaluationStatus.FAIL

        # 10. Verify: exception status is RESOLVED
        exc = get_exception(db, case.id, "metro")
        assert exc is not None
        assert exc.status == ExceptionStatus.RESOLVED

        # 11. Verify: full timeline includes all transitions
        timeline = get_timeline(db, case.id, "metro")
        event_types = [e["event_type"] for e in timeline]
        assert "DETECTION" in event_types
        assert "OPENED" in event_types
        assert "EVIDENCE" in event_types
        assert "ACTION" in event_types

        # 12. Verify: action history has ACKNOWLEDGE, REVIEW_NOTE, ADD_EVIDENCE, RESOLVE
        actions = get_action_history(db, case.id, "metro")
        atypes = _action_types(actions)
        assert "ACKNOWLEDGE" in atypes
        assert "REVIEW_NOTE" in atypes
        assert "ADD_EVIDENCE" in atypes
        assert "RESOLVE" in atypes

        db.rollback()

    def test_m3d_scenario_a_queue_shows_open_only(self, db):
        """Resolved exception disappears from review queue."""
        _seed_metro(db)
        op_date = date.today()

        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        # Appears in queue while OPEN
        queue = get_review_queue(db, "metro")
        assert any(c.id == case.id for c in queue)

        # Resolve
        resolve_exception(db, case.id, "metro", "sup1",
                           reason="Resolved immediately")

        # Disappears from queue after RESOLVED
        queue_after = get_review_queue(db, "metro")
        assert not any(c.id == case.id for c in queue_after)

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO B: Equipment Substitution Approved
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioBEquipmentSubstitution:
    """Equipment substitution: discrepancy → exception → decision → approve → resolve."""

    def test_m3d_scenario_b_equipment_substitution_approved(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create EquipmentDiscrepancy (planned=ex25, actual=ex31, worker=w1)
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)

        # 2. create_exception_from_discrepancy → case OPEN
        case = create_exception_from_discrepancy(db, disc)
        assert case is not None
        assert case.status == ExceptionStatus.OPEN
        assert case.exception_type == "EQUIPMENT_MISMATCH"

        # 3. acknowledge_exception
        acked = acknowledge_exception(db, case.id, "metro", "sup1")
        assert acked.status == ExceptionStatus.ACKNOWLEDGED

        # 4. Add supervisor note/evidence
        add_review_note(db, case.id, "metro", "sup1",
                        "Reviewing equipment substitution request")
        add_evidence(db, case.id, "metro",
                     ExceptionEvidenceType.SUPERVISOR_NOTE,
                     source_type="manual", source_id="note-sub-001",
                     actor_user_id="sup1", is_system_generated=False,
                     note="Supervisor note for substitution")

        # 5. request_decision (EQUIPMENT_SUBSTITUTION)
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        assert dec.status == DecisionStatus.PENDING

        # 6. approve_decision with authorization_policy="SIM_APPROVER"
        approved = approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="EX-025 under maintenance, using EX-031",
            authorization_policy="SIM_APPROVER",
        )
        assert approved.status == DecisionStatus.APPROVED

        # 7. Verify: planned_equipment_id on decision still ex25
        stored = get_decision(db, dec.id, "metro")
        assert stored.planned_equipment_id == "ex25"

        # 8. Verify: actual_equipment_id on decision still ex31
        assert stored.actual_equipment_id == "ex31"

        # 9. Verify: original discrepancy planned_equipment_id still ex25
        db.refresh(disc)
        assert disc.planned_equipment_id == "ex25"

        # 10. Verify: original discrepancy actual_equipment_id still ex31
        assert disc.actual_equipment_id == "ex31"

        # 11. Verify: exception status is ACKNOWLEDGED (approval does NOT auto-resolve)
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.ACKNOWLEDGED

        # 12. Explicit resolve_exception → RESOLVED
        resolved = resolve_exception(db, case.id, "metro", "sup1",
                                      reason="Substitution approved and resolved")
        assert resolved.status == ExceptionStatus.RESOLVED

        # 13. Verify: separate RESOLVE action in history
        actions = get_action_history(db, case.id, "metro")
        atypes = _action_types(actions)
        assert "RESOLVE" in atypes
        resolve_actions = [a for a in actions if a.action_type == ExceptionActionType.RESOLVE]
        assert len(resolve_actions) == 1

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO C: Operator Substitution Approved
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioCOperatorSubstitution:
    """Operator substitution: different worker on same equipment, approved."""

    def test_m3d_scenario_c_operator_substitution_approved(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create EquipmentDiscrepancy (planned_worker=w1, actual_worker=w2, equipment=ex25)
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex25",
                                 planned_worker_id="w1", actual_worker_id="w2",
                                 operating_date=op_date,
                                 discrepancy_type=DiscrepancyType.OPERATOR_SUBSTITUTION)

        # 2. create_exception_from_discrepancy → case OPEN
        case = create_exception_from_discrepancy(db, disc)
        assert case is not None
        assert case.status == ExceptionStatus.OPEN

        # 3. acknowledge_exception
        acknowledge_exception(db, case.id, "metro", "sup1")

        # 4. request_decision (OPERATOR_SUBSTITUTION)
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )
        assert dec.status == DecisionStatus.PENDING

        # 5. approve_decision with SIM_APPROVER
        approved = approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Tono called in sick, Budi is qualified",
            authorization_policy="SIM_APPROVER",
        )
        assert approved.status == DecisionStatus.APPROVED

        # 6. Verify: decision records planned_worker_id=w1, actual_worker_id=w2
        stored = get_decision(db, dec.id, "metro")
        assert stored.planned_worker_id == "w1"
        assert stored.actual_worker_id == "w2"

        # 7. Verify: original discrepancy planned_worker_id still w1 (never modified)
        db.refresh(disc)
        assert disc.planned_worker_id == "w1"
        assert disc.actual_worker_id == "w2"

        # 8. Verify: worker validation passed (w2 is active, has competency for EXCAVATOR)
        w2 = db.query(Worker).filter(Worker.id == "w2").first()
        assert w2.is_active is True
        comp = db.query(Competency).filter(
            Competency.employee_id == "w2",
            Competency.equipment_type == "EXCAVATOR",
            Competency.status == CompetencyStatus.VALID,
        ).first()
        assert comp is not None

        # 9. Verify: exception status still ACKNOWLEDGED
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.ACKNOWLEDGED

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO D: Substitution Rejected
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioDSubstitutionRejected:
    """Decision rejection preserves all state."""

    def test_m3d_scenario_d_substitution_rejected(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create EquipmentDiscrepancy (planned=ex25, actual=ex31, worker=w1)
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)

        # 2. create_exception_from_discrepancy → OPEN
        case = create_exception_from_discrepancy(db, disc)
        assert case.status == ExceptionStatus.OPEN

        # 3. request_decision (EQUIPMENT_SUBSTITUTION)
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        # 4. reject_decision with reason
        rejected = reject_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Equipment EX-031 reserved for different site",
        )

        # 5. Verify: decision status REJECTED
        assert rejected.status == DecisionStatus.REJECTED
        assert rejected.decided_by == "sup1"

        # 6. Verify: original discrepancy still exists (not deleted)
        db.refresh(disc)
        assert disc.id is not None
        assert disc.planned_equipment_id == "ex25"
        assert disc.actual_equipment_id == "ex31"

        # 7. Verify: exception still OPEN (rejection does not resolve)
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.OPEN

        # 8. Verify: rejection reason recorded
        stored = get_decision(db, dec.id, "metro")
        assert stored.reason_text == "Equipment EX-031 reserved for different site"

        # 9. Verify: rejection actor recorded
        assert stored.decided_by == "sup1"

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO E: Authorization Missing
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioEAuthorizationMissing:
    """Missing authorization policy must block approval."""

    def test_m3d_scenario_e_authorization_missing(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create discrepancy → exception → acknowledge
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        # 2. request_decision (EQUIPMENT_SUBSTITUTION)
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        # 3. approve_decision WITHOUT authorization_policy → AuthorizationBlocked
        # Per M3C spec: missing auth → must NOT approve automatically.
        with pytest.raises(AuthorizationBlocked):
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should be blocked without auth",
                authorization_policy=None,
            )

        # 4. Verify: decision remains PENDING
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.PENDING

        # 5. Verify: exception unchanged
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.ACKNOWLEDGED

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO F: Critical Validation Blocks Approval
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioFValidationBlocks:
    """Validation failures block decision approval."""

    def test_m3d_scenario_f_inactive_worker_blocks(self, db):
        """Inactive worker (w3_inactive) blocks operator substitution."""
        _seed_metro(db)
        op_date = date.today()

        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex25",
                                 planned_worker_id="w1", actual_worker_id="w3_inactive",
                                 operating_date=op_date,
                                 discrepancy_type=DiscrepancyType.OPERATOR_SUBSTITUTION)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w3_inactive",
            actual_equipment_id="ex25",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail — inactive worker",
                authorization_policy="SIM_APPROVER",
            )
        assert any("inactive" in f for f in exc_info.value.failures)

        # Decision remains PENDING
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.PENDING

        db.rollback()

    def test_m3d_scenario_f_out_of_service_equipment_blocks(self, db):
        """OUT_OF_SERVICE equipment blocks equipment substitution."""
        _seed_metro(db)
        op_date = date.today()

        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex_broken",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex_broken",
            actual_worker_id="w1",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail — broken equipment",
                authorization_policy="SIM_APPROVER",
            )
        assert any("OUT_OF_SERVICE" in f for f in exc_info.value.failures)

        # Decision remains PENDING
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.PENDING

        db.rollback()

    def test_m3d_scenario_f_expired_competency_blocks(self, db):
        """Worker with no valid competency blocks operator substitution."""
        _seed_metro(db)
        op_date = date.today()

        # Create worker with ONLY expired competency (no VALID one)
        w5 = Worker(id="w5", tenant_id="metro", code="W005", name="Eka", is_active=True)
        db.add(w5)
        comp_exp = Competency(
            id="comp_w5_exp", tenant_id="metro", competency_code="EXC-A",
            employee_id="w5", equipment_type="EXCAVATOR",
            valid_from=date(2025, 1, 1),
            valid_to=date.today() - timedelta(days=30),
            status=CompetencyStatus.EXPIRED,
        )
        db.add(comp_exp)
        db.flush()

        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex25",
                                 planned_worker_id="w1", actual_worker_id="w5",
                                 operating_date=op_date,
                                 discrepancy_type=DiscrepancyType.OPERATOR_SUBSTITUTION)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w5",
            actual_equipment_id="ex25",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail — expired competency",
                authorization_policy="SIM_APPROVER",
            )
        assert any("competency" in f for f in exc_info.value.failures)

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO G: Waiver
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioGWaiver:
    """Waiver transitions exception to WAIVED, preserving all history."""

    def test_m3d_scenario_g_waiver(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create RuleEvaluation FAIL → exception OPEN
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)
        assert case.status == ExceptionStatus.OPEN

        # 2. waive_exception with reason
        waived = waive_exception(db, case.id, "metro", "admin_1",
                                  reason="Reviewed: operational necessity confirmed")
        assert waived.status == ExceptionStatus.WAIVED

        # 3. Verify: waived_at set
        assert waived.waived_at is not None

        # 4. Verify: original RuleEvaluation still FAIL (not modified)
        db.refresh(re)
        assert re.status == RuleEvaluationStatus.FAIL

        # 5. Verify: waiver action recorded with actor/reason
        actions = get_action_history(db, case.id, "metro")
        waive_actions = [a for a in actions if a.action_type == ExceptionActionType.WAIVE]
        assert len(waive_actions) == 1
        assert waive_actions[0].actor_user_id == "admin_1"
        assert waive_actions[0].reason == "Reviewed: operational necessity confirmed"

        # 6. Verify: exception retains all history (detection, actions)
        exc = get_exception(db, case.id, "metro")
        assert exc is not None
        assert exc.status == ExceptionStatus.WAIVED
        assert exc.source_id == re.id

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO H: Review Ownership
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioHReviewOwnership:
    """Reviewer assignment, reassignment, and cross-tenant isolation."""

    def test_m3d_scenario_h_review_ownership(self, db):
        """Assign and reassign reviewer with full audit trail."""
        _seed_metro(db)
        op_date = date.today()

        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        # 1. assign_reviewer → supervisor_1
        assigned = assign_reviewer(db, case.id, "metro", "admin1",
                                    new_owner_id="supervisor_1")
        assert assigned.current_owner_id == "supervisor_1"

        # 2. Verify current_owner_id
        exc = get_exception(db, case.id, "metro")
        assert exc.current_owner_id == "supervisor_1"

        # 3. reassign to supervisor_2
        reassigned = assign_reviewer(db, case.id, "metro", "admin1",
                                      new_owner_id="supervisor_2")
        assert reassigned.current_owner_id == "supervisor_2"

        # 4. Verify current_owner_id updated
        exc = get_exception(db, case.id, "metro")
        assert exc.current_owner_id == "supervisor_2"

        # 5. Verify: two ASSIGN_REVIEWER actions in history
        actions = get_action_history(db, case.id, "metro")
        assign_actions = [a for a in actions if a.action_type == ExceptionActionType.ASSIGN_REVIEWER]
        assert len(assign_actions) == 2

        # 6. Each action has previous/new owner info
        assert assign_actions[0].actor_user_id == "admin1"
        assert assign_actions[1].actor_user_id == "admin1"

        db.rollback()

    def test_m3d_scenario_h_cross_tenant_assign_blocked(self, db):
        """Cross-tenant reviewer assignment raises ValueError."""
        _seed_metro(db)
        _seed_client_b(db)
        op_date = date.today()

        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        with pytest.raises(ValueError, match="not found"):
            assign_reviewer(db, case.id, "client_b", "client_admin",
                            new_owner_id="client_supervisor")

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO I: Evidence Chain
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioIEvidenceChain:
    """Evidence chain: system + human evidence, immutability, cross-tenant block."""

    def test_m3d_scenario_i_evidence_chain(self, db):
        """Full evidence lifecycle: system evidence immutable, human evidence mutable."""
        _seed_metro(db)
        op_date = date.today()

        # 1. Create exception from RuleEvaluation
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        # 2. Add system evidence: RULE_EVALUATION, is_system_generated=True
        ev_sys = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            source_type="rule_evaluations", source_id=re.id,
            is_system_generated=True,
            note="Auto-attached rule evaluation",
        )
        assert ev_sys.is_system_generated is True
        assert ev_sys.added_by is None  # system — no actor

        # 3. Add human evidence: SUPERVISOR_NOTE, actor_user_id="sup1"
        ev_human1 = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.SUPERVISOR_NOTE,
            source_type="manual", source_id="note-sup1",
            actor_user_id="sup1",
            is_system_generated=False,
            note="Supervisor observation",
        )
        assert ev_human1.is_system_generated is False
        assert ev_human1.added_by == "sup1"

        # 4. Add human evidence: EQUIPMENT_ASSIGNMENT, actor_user_id="sup2"
        ev_human2 = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.EQUIPMENT_ASSIGNMENT,
            source_type="manual", source_id="equip-status-001",
            actor_user_id="sup2",
            is_system_generated=False,
            note="Equipment status check",
        )
        assert ev_human2.added_by == "sup2"

        # 5. get_evidence → 3 records
        evidence_list = get_evidence(db, case.id, "metro")
        assert len(evidence_list) == 3

        # 6. Verify: system evidence is_system_generated=True
        system_ev = [e for e in evidence_list if e.is_system_generated]
        assert len(system_ev) == 1
        assert system_ev[0].evidence_type == ExceptionEvidenceType.RULE_EVALUATION

        # 7. Verify: human evidence has added_by set
        human_ev = [e for e in evidence_list if not e.is_system_generated]
        assert len(human_ev) == 2
        added_by_values = {e.added_by for e in human_ev}
        assert "sup1" in added_by_values
        assert "sup2" in added_by_values

        # 8. Verify: system_evidence_is_immutable returns True for system evidence
        assert system_evidence_is_immutable(system_ev[0]) is True
        # And False for human evidence
        assert system_evidence_is_immutable(human_ev[0]) is False

        # 9. Verify: review detail includes all 3 evidence records
        detail = get_review_detail(db, case.id, "metro")
        assert len(detail["evidence"]) == 3

        db.rollback()

    def test_m3d_scenario_i_cross_tenant_evidence_blocked(self, db):
        """Cross-tenant evidence attachment raises ValueError."""
        _seed_metro(db)
        _seed_client_b(db)
        op_date = date.today()

        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        with pytest.raises(ValueError, match="not found"):
            add_evidence(
                db, case.id, "client_b",
                ExceptionEvidenceType.SUPERVISOR_NOTE,
                source_type="manual", source_id="cross-tenant-attempt",
                actor_user_id="client_sup",
                is_system_generated=False,
                note="Cross-tenant evidence attempt",
            )

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO J: Concurrent Acknowledgement
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioJConcurrentAck:
    """Second acknowledgement attempt must fail (invalid transition)."""

    def test_m3d_scenario_j_concurrent_ack(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create exception OPEN
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)
        assert case.status == ExceptionStatus.OPEN

        # 2. First acknowledge → ACKNOWLEDGED
        acked = acknowledge_exception(db, case.id, "metro", "sup1")
        assert acked.status == ExceptionStatus.ACKNOWLEDGED

        # 3. Second acknowledge attempt → ValueError (invalid transition)
        with pytest.raises(ValueError, match="Invalid transition"):
            acknowledge_exception(db, case.id, "metro", "sup2")

        # 4. Only one ACKNOWLEDGE action in history
        actions = get_action_history(db, case.id, "metro")
        ack_actions = [a for a in actions if a.action_type == ExceptionActionType.ACKNOWLEDGE]
        assert len(ack_actions) == 1
        assert ack_actions[0].actor_user_id == "sup1"

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO K: Concurrent Decision
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioKConcurrentDecision:
    """Second decision status change must fail (terminal state)."""

    def test_m3d_scenario_k_concurrent_decision(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create exception, request decision
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        # 2. First approve (SIM_APPROVER) → APPROVED
        approved = approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved substitution",
            authorization_policy="SIM_APPROVER",
        )
        assert approved.status == DecisionStatus.APPROVED

        # 3. Second reject attempt → InvalidDecisionTransition
        with pytest.raises(InvalidDecisionTransition):
            reject_decision(
                db, dec.id, "metro", "sup2",
                reason_text="Should fail — already approved",
            )

        # 4. Only one decision status change (APPROVED), one APPROVE action
        history = get_decision_history(db, dec.id, "metro")
        approve_actions = [a for a in history if a.action_type == "APPROVE"]
        assert len(approve_actions) == 1

        # Decision still APPROVED
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.APPROVED

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO L: Duplicate/Retry Workflow (Idempotency)
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioLIdempotency:
    """Idempotent operations: same input → same result, no duplicates."""

    def test_m3d_scenario_l_idempotency(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create same RuleEvaluation twice → same exception (idempotent)
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case1 = create_exception_from_rule_evaluation(db, re)
        case2 = create_exception_from_rule_evaluation(db, re)
        assert case1.id == case2.id  # same exception returned

        # Verify only one ExceptionCase for this source
        count = db.query(ExceptionCase).filter(
            ExceptionCase.source_id == re.id,
        ).count()
        assert count == 1

        # 2. Add same evidence twice → same evidence record (idempotent)
        ev1 = add_evidence(
            db, case1.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            source_type="rule_evaluations", source_id=re.id,
            is_system_generated=True,
        )
        ev2 = add_evidence(
            db, case1.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            source_type="rule_evaluations", source_id=re.id,
            is_system_generated=True,
        )
        assert ev1.id == ev2.id  # same evidence record returned

        # 3. Request same decision type twice → same PENDING decision (idempotent)
        dec1 = request_decision(
            db, "metro", case1.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        dec2 = request_decision(
            db, "metro", case1.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        assert dec1.id == dec2.id  # same PENDING decision returned
        assert dec1.status == DecisionStatus.PENDING

        # 4. Approve twice → second raises InvalidDecisionTransition
        approve_decision(
            db, dec1.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )
        with pytest.raises(InvalidDecisionTransition):
            approve_decision(
                db, dec1.id, "metro", "sup1",
                reason_text="Second approval attempt",
                authorization_policy="SIM_APPROVER",
            )

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO M: Full Timeline
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioMFullTimeline:
    """Complete workflow with timeline and decision-history verification."""

    def test_m3d_scenario_m_full_timeline(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create EquipmentDiscrepancy
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)

        # 2. create_exception_from_discrepancy → OPEN
        case = create_exception_from_discrepancy(db, disc)
        assert case.status == ExceptionStatus.OPEN

        # 3. add_evidence (system: EQUIPMENT_COMPARISON)
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.EQUIPMENT_COMPARISON,
            source_type="equipment_discrepancies", source_id=disc.id,
            is_system_generated=True,
            note="Auto-attached discrepancy",
        )

        # 4. acknowledge_exception
        acknowledge_exception(db, case.id, "metro", "sup1",
                               reason="Starting review")

        # 5. add_review_note
        add_review_note(db, case.id, "metro", "sup1",
                        "Reviewing substitution request")

        # 6. add_evidence (human: SUPERVISOR_NOTE)
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.SUPERVISOR_NOTE,
            source_type="manual", source_id="sup-note-001",
            actor_user_id="sup1",
            is_system_generated=False,
            note="On-site verification completed",
        )

        # 7. request_decision (EQUIPMENT_SUBSTITUTION)
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        # 8. approve_decision (SIM_APPROVER)
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved — equipment substitution justified",
            authorization_policy="SIM_APPROVER",
        )

        # 9. resolve_exception
        resolve_exception(db, case.id, "metro", "sup1",
                           reason="Substitution workflow complete")

        # 10. get_timeline → verify chronological order
        timeline = get_timeline(db, case.id, "metro")
        assert len(timeline) > 0

        # Verify timestamps are chronological
        timestamps = [e["timestamp"] for e in timeline if e["timestamp"]]
        assert timestamps == sorted(timestamps)

        # 11. Verify timeline has DETECTION, OPENED, EVIDENCE, ACTION entries
        event_types = [e["event_type"] for e in timeline]
        assert "DETECTION" in event_types
        assert "OPENED" in event_types

        # Evidence entries
        evidence_events = [e for e in timeline if e["event_type"] == "EVIDENCE"]
        assert len(evidence_events) >= 2  # system + human

        # Action entries (lifecycle + review)
        action_events = [e for e in timeline if e["event_type"] == "ACTION"]
        action_descriptions = [e["description"] for e in action_events]
        assert any("ACKNOWLEDGE" in d for d in action_descriptions)
        assert any("REVIEW_NOTE" in d for d in action_descriptions)
        assert any("RESOLVE" in d for d in action_descriptions)

        # Verify decision history separately (tracked in ExceptionDecisionAction)
        dec_history = get_decision_history(db, dec.id, "metro")
        dec_action_types = [a.action_type for a in dec_history]
        assert "REQUEST" in dec_action_types
        assert "APPROVE" in dec_action_types

        db.rollback()

    def test_m3d_scenario_m_decision_history_chain(self, db):
        """Decision history records REQUEST and APPROVE with timestamps."""
        _seed_metro(db)
        op_date = date.today()

        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        history = get_decision_history(db, dec.id, "metro")
        assert len(history) >= 2

        req = [a for a in history if a.action_type == "REQUEST"]
        app = [a for a in history if a.action_type == "APPROVE"]
        assert len(req) == 1
        assert len(app) == 1

        # Timestamps are ordered: REQUEST before APPROVE
        assert req[0].action_timestamp <= app[0].action_timestamp

        # APPROVE has authorization result
        assert app[0].authorization_result is not None

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO N: Technical Tenant Isolation
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioNTenantIsolation:
    """Strict tenant isolation across all M3A/M3B/M3C operations."""

    def test_m3d_scenario_n_cross_tenant_read_blocked(self, db):
        """Metro cannot read or acknowledge client_b exceptions."""
        _seed_metro(db)
        _seed_client_b(db)
        op_date = date.today()

        client_case = _create_client_b_exception(db)

        # Metro cannot read client_b exception
        result = get_exception(db, client_case.id, "metro")
        assert result is None

        # Metro cannot acknowledge client_b exception
        with pytest.raises(ValueError, match="not found"):
            acknowledge_exception(db, client_case.id, "metro", "metro_sup")

        db.rollback()

    def test_m3d_scenario_n_cross_tenant_modifications_blocked(self, db):
        """Metro cannot modify client_b exceptions (notes, evidence, reviewer)."""
        _seed_metro(db)
        _seed_client_b(db)

        client_case = _create_client_b_exception(db)

        # Cannot add note
        with pytest.raises(ValueError, match="not found"):
            add_review_note(db, client_case.id, "metro", "metro_sup",
                            "Cross-tenant note attempt")

        # Cannot attach evidence
        with pytest.raises(ValueError, match="not found"):
            add_evidence(
                db, client_case.id, "metro",
                ExceptionEvidenceType.SUPERVISOR_NOTE,
                source_type="manual", source_id="cross-tenant-ev",
                actor_user_id="metro_sup",
                is_system_generated=False,
            )

        # Cannot assign reviewer
        with pytest.raises(ValueError, match="not found"):
            assign_reviewer(db, client_case.id, "metro", "metro_sup",
                            new_owner_id="metro_reviewer")

        db.rollback()

    def test_m3d_scenario_n_cross_tenant_decision_blocked(self, db):
        """Metro cannot request decisions on client_b exceptions."""
        _seed_metro(db)
        _seed_client_b(db)

        client_case = _create_client_b_exception(db)

        with pytest.raises(ValueError, match="not found"):
            request_decision(
                db, "metro", client_case.id,
                DecisionType.EQUIPMENT_SUBSTITUTION,
                requested_by="metro_sup",
            )

        db.rollback()

    def test_m3d_scenario_n_queue_and_decisions_isolated(self, db):
        """Review queue and decision lists are tenant-scoped."""
        _seed_metro(db)
        _seed_client_b(db)
        op_date = date.today()

        # Create metro exception + decision
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        metro_case = create_exception_from_rule_evaluation(db, re)
        acknowledge_exception(db, metro_case.id, "metro", "metro_sup")

        dec = request_decision(
            db, "metro", metro_case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="metro_sup",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        # Create client_b exception
        _create_client_b_exception(db)

        # Metro review queue returns only metro cases
        metro_queue = get_review_queue(db, "metro")
        for c in metro_queue:
            assert c.tenant_id == "metro"

        # Cross-tenant decision list returns empty
        client_decisions = get_decisions_for_exception(db, metro_case.id, "client_b")
        assert len(client_decisions) == 0

        # Metro can see its own decision
        metro_decisions = get_decisions_for_exception(db, metro_case.id, "metro")
        assert len(metro_decisions) == 1
        assert metro_decisions[0].id == dec.id

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# SCENARIO O: TBC-Safe Decision
# ═══════════════════════════════════════════════════════════════

class TestM3DScenarioOTbcSafe:
    """Missing authorization must not auto-approve decisions."""

    def test_m3d_scenario_o_tbc_safe(self, db):
        _seed_metro(db)
        op_date = date.today()

        # 1. Create discrepancy → exception → acknowledge
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        # 2. request_decision (OPERATIONAL_OVERRIDE)
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATIONAL_OVERRIDE,
            requested_by="sup1",
            reason_text="Emergency operational override",
        )
        assert dec.status == DecisionStatus.PENDING

        # 3. approve_decision WITHOUT authorization_policy → AuthorizationBlocked
        with pytest.raises(AuthorizationBlocked):
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should be blocked — no auth policy",
                authorization_policy=None,
            )

        # 4. Decision remains PENDING (not auto-approved)
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.PENDING

        db.rollback()


# ═══════════════════════════════════════════════════════════════
# INVARIANT TESTS
# ═══════════════════════════════════════════════════════════════

class TestM3DInvariants:
    """Structural invariants that must hold across all workflows."""

    def test_m3d_invariant_approval_ne_resolution(self, db):
        """Approval ≠ Resolution. Approval does NOT auto-resolve the exception."""
        _seed_metro(db)
        op_date = date.today()

        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        # Exception is ACKNOWLEDGED, NOT RESOLVED
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.ACKNOWLEDGED

        # Explicit resolve required
        resolved = resolve_exception(db, case.id, "metro", "sup1",
                                      reason="Now resolving")
        assert resolved.status == ExceptionStatus.RESOLVED

        # Separate RESOLVE action in history
        actions = get_action_history(db, case.id, "metro")
        resolve_actions = [a for a in actions if a.action_type == ExceptionActionType.RESOLVE]
        assert len(resolve_actions) == 1

        db.rollback()

    def test_m3d_invariant_rejection_ne_resolution(self, db):
        """Rejection does NOT resolve or delete the exception."""
        _seed_metro(db)
        op_date = date.today()

        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        reject_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Not approved",
        )

        # Exception still OPEN
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.OPEN

        # Original discrepancy preserved
        db.refresh(disc)
        assert disc.planned_equipment_id == "ex25"
        assert disc.actual_equipment_id == "ex31"

        db.rollback()

    def test_m3d_invariant_waiver_ne_deletion(self, db):
        """Waiver does NOT delete the source or exception case."""
        _seed_metro(db)
        op_date = date.today()

        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        waive_exception(db, case.id, "metro", "admin_1",
                         reason="Operational necessity")

        # Original RuleEvaluation still exists with FAIL status
        db.refresh(re)
        assert re.status == RuleEvaluationStatus.FAIL

        # Exception case still exists with WAIVED status
        exc = get_exception(db, case.id, "metro")
        assert exc is not None
        assert exc.status == ExceptionStatus.WAIVED

        # All action history preserved
        actions = get_action_history(db, case.id, "metro")
        assert len(actions) >= 1
        waive_actions = [a for a in actions if a.action_type == ExceptionActionType.WAIVE]
        assert len(waive_actions) == 1

        db.rollback()

    def test_m3d_invariant_planned_actual_decision_separation(self, db):
        """Planned, actual, and decision are three independent records."""
        _seed_metro(db)
        op_date = date.today()

        # Create discrepancy (planned=w1→ex25, actual=w1→ex31)
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        # PLAN: discrepancy.planned_equipment_id = ex25
        db.refresh(disc)
        assert disc.planned_equipment_id == "ex25"

        # ACTUAL: discrepancy.actual_equipment_id = ex31
        assert disc.actual_equipment_id == "ex31"

        # DECISION: decision.status = APPROVED, with planned/actual refs
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.APPROVED
        assert stored.planned_equipment_id == "ex25"
        assert stored.actual_equipment_id == "ex31"

        # None of the three are collapsed into one mutable value
        assert disc.planned_equipment_id != disc.actual_equipment_id
        assert stored.planned_equipment_id == disc.planned_equipment_id
        assert stored.actual_equipment_id == disc.actual_equipment_id

        db.rollback()

    def test_m3d_no_payroll_consequence(self, db):
        """Full workflow creates no payroll, disciplinary, or HSE side effects."""
        _seed_metro(db)
        op_date = date.today()

        # Full workflow: exception → decision → approve → resolve
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        case = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case.id, "metro", "sup1")

        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.EQUIPMENT_COMPARISON,
            source_type="equipment_discrepancies", source_id=disc.id,
            is_system_generated=True,
        )

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )
        resolve_exception(db, case.id, "metro", "sup1",
                           reason="Resolved")

        # Negative test: verify no unexpected side effects
        exception_count = db.query(ExceptionCase).filter(
            ExceptionCase.tenant_id == "metro"
        ).count()
        assert exception_count == 1

        action_count = db.query(ExceptionAction).filter(
            ExceptionAction.tenant_id == "metro"
        ).count()
        assert action_count >= 1

        decision_count = db.query(ExceptionDecision).filter(
            ExceptionDecision.tenant_id == "metro"
        ).count()
        assert decision_count == 1

        evidence_count = db.query(ExceptionEvidence).filter(
            ExceptionEvidence.tenant_id == "metro"
        ).count()
        assert evidence_count >= 1

        # No payroll-related models exist in this system scope
        # (no PayrollDeduction, DisciplinarySanction, or HSESanction tables)

        db.rollback()

    def test_m3d_full_audit_trace(self, db):
        """Complete action history chain with valid transitions and timestamps."""
        _seed_metro(db)
        op_date = date.today()

        # 1. Create EquipmentDiscrepancy with full context
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)

        # 2. create_exception_from_discrepancy
        case = create_exception_from_discrepancy(db, disc)

        # 3. add_evidence (system)
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.EQUIPMENT_COMPARISON,
            source_type="equipment_discrepancies", source_id=disc.id,
            is_system_generated=True,
        )

        # 4. acknowledge_exception
        acknowledge_exception(db, case.id, "metro", "sup1")

        # 5. add_evidence (human)
        add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.SUPERVISOR_NOTE,
            source_type="manual", source_id="audit-note-001",
            actor_user_id="sup1",
            is_system_generated=False,
            note="On-site check completed",
        )

        # 6. request_decision
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex31",
            actual_worker_id="w1",
        )

        # 7. approve_decision
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved after review",
            authorization_policy="SIM_APPROVER",
        )

        # 8. resolve_exception
        resolve_exception(db, case.id, "metro", "sup1",
                           reason="Workflow complete")

        # 9. Verify complete action history chain
        actions = get_action_history(db, case.id, "metro")

        # Each action has required fields
        for action in actions:
            assert action.action_type is not None
            assert action.actor_user_id is not None
            assert action.action_timestamp is not None
            assert action.previous_status is not None
            assert action.new_status is not None

        # Timestamps are chronological
        timestamps = [a.action_timestamp for a in actions]
        assert timestamps == sorted(timestamps)

        # Statuses form valid transition chain
        # ACKNOWLEDGE: OPEN → ACKNOWLEDGED
        ack = [a for a in actions if a.action_type == ExceptionActionType.ACKNOWLEDGE]
        assert len(ack) == 1
        assert ack[0].previous_status == ExceptionStatus.OPEN
        assert ack[0].new_status == ExceptionStatus.ACKNOWLEDGED

        # RESOLVE: ACKNOWLEDGED → RESOLVED
        res = [a for a in actions if a.action_type == ExceptionActionType.RESOLVE]
        assert len(res) == 1
        assert res[0].previous_status == ExceptionStatus.ACKNOWLEDGED
        assert res[0].new_status == ExceptionStatus.RESOLVED

        # Decision history also has valid chain
        dec_history = get_decision_history(db, dec.id, "metro")
        assert len(dec_history) >= 2  # REQUEST + APPROVE
        request_action = [a for a in dec_history if a.action_type == "REQUEST"][0]
        approve_action = [a for a in dec_history if a.action_type == "APPROVE"][0]
        assert request_action.previous_status == DecisionStatus.PENDING
        assert request_action.new_status == DecisionStatus.PENDING
        assert approve_action.previous_status == DecisionStatus.PENDING
        assert approve_action.new_status == DecisionStatus.APPROVED

        db.rollback()

    def test_m3d_invariant_source_immutability(self, db):
        """Source records (RuleEvaluation, EquipmentDiscrepancy) are never mutated."""
        _seed_metro(db)
        op_date = date.today()

        # RuleEvaluation path
        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)
        acknowledge_exception(db, case.id, "metro", "sup1")
        resolve_exception(db, case.id, "metro", "sup1", reason="Done")
        db.refresh(re)
        assert re.status == RuleEvaluationStatus.FAIL  # untouched

        # EquipmentDiscrepancy path
        disc = _make_discrepancy(db, "metro", "w1",
                                 planned_equip_id="ex25", actual_equip_id="ex31",
                                 operating_date=op_date)
        orig_planned = disc.planned_equipment_id
        orig_actual = disc.actual_equipment_id
        case2 = create_exception_from_discrepancy(db, disc)
        acknowledge_exception(db, case2.id, "metro", "sup1")
        resolve_exception(db, case2.id, "metro", "sup1", reason="Done")
        db.refresh(disc)
        assert disc.planned_equipment_id == orig_planned
        assert disc.actual_equipment_id == orig_actual

        db.rollback()

    def test_m3d_invariant_evidence_never_deleted(self, db):
        """Evidence records persist through the entire exception lifecycle."""
        _seed_metro(db)
        op_date = date.today()

        re = _make_rule_eval(db, "metro", "w1", "LATE_BREAK_RETURN",
                             status=RuleEvaluationStatus.FAIL, operating_date=op_date)
        case = create_exception_from_rule_evaluation(db, re)

        ev = add_evidence(
            db, case.id, "metro",
            ExceptionEvidenceType.RULE_EVALUATION,
            source_type="rule_evaluations", source_id=re.id,
            is_system_generated=True,
        )
        ev_id = ev.id

        acknowledge_exception(db, case.id, "metro", "sup1")
        resolve_exception(db, case.id, "metro", "sup1", reason="Done")

        # Evidence still exists after resolution
        evidence_list = get_evidence(db, case.id, "metro")
        assert any(e.id == ev_id for e in evidence_list)

        db.rollback()
