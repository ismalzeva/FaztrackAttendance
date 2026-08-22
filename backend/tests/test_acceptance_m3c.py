"""
test_acceptance_m3c.py — MM-M3C Substitution, Override & Controlled Decision acceptance tests.

Metro Mining controlled-decision infrastructure:
- Decision request creation (operator/equipment substitution, operational override)
- Decision lifecycle (PENDING → APPROVED / REJECTED / CANCELLED)
- Validation (worker active, competency, equipment status, conflicts)
- Authorization (policy-driven, TBC-safe, simulation)
- Audit trail (immutable decision history)
- Idempotency (no duplicate requests/approvals/rejections)
- Concurrency (first-valid-decides)
- Tenant isolation (strict client-cluster)
- History preservation (planned/actual never rewritten)
- Exception lifecycle interaction (approval ≠ auto-resolve)

45 test scenarios.
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
    Competency, CompetencyStatus,
    ExceptionCase, ExceptionAction, ExceptionActionType,
    ExceptionSourceType, ExceptionStatus, ExceptionSeverity,
    RosterAssignment, WorkStatus, SiteStatusEnum,
    EquipmentAssignmentActual,
    DecisionType, DecisionStatus,
    ExceptionDecision, ExceptionDecisionAction,
)
from app.exception_engine import (
    create_exception_from_rule_evaluation,
    acknowledge_exception,
    resolve_exception,
    get_exception,
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
    """Seed Metro Mining minimal environment for M3C tests."""
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)

    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Tono", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Budi", is_active=True)
    w_inactive = Worker(id="w3", tenant_id="metro", code="W003", name="Cakra", is_active=False)
    db.add_all([w1, w2, w_inactive])

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
    ex_oos = Equipment(
        id="ex_oos", tenant_id="metro", equipment_code="EX-OOS",
        equipment_type="EXCAVATOR", status=EquipmentStatus.OUT_OF_SERVICE,
        effective_from=date(2026, 1, 1),
    )
    ex_inactive = Equipment(
        id="ex_inactive", tenant_id="metro", equipment_code="EX-INA",
        equipment_type="EXCAVATOR", status=EquipmentStatus.INACTIVE,
        effective_from=date(2026, 1, 1),
    )
    db.add_all([ex25, ex31, ex_oos, ex_inactive])

    rv = RuleVersion(
        id="rv1", tenant_id="metro", version_label="v1.0",
        effective_from=date(2026, 1, 1), config_snapshot_json="{}",
    )
    db.add(rv)

    # Competencies
    comp_w1_ex = Competency(
        id="comp1", tenant_id="metro", competency_code="EXC-A",
        employee_id="w1", equipment_type="EXCAVATOR",
        valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
        status=CompetencyStatus.VALID,
    )
    comp_w2_ex = Competency(
        id="comp2", tenant_id="metro", competency_code="EXC-A",
        employee_id="w2", equipment_type="EXCAVATOR",
        valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31),
        status=CompetencyStatus.VALID,
    )
    comp_expired = Competency(
        id="comp_exp", tenant_id="metro", competency_code="EXC-A",
        employee_id="w3", equipment_type="EXCAVATOR",
        valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
        status=CompetencyStatus.EXPIRED,
    )
    db.add_all([comp_w1_ex, comp_w2_ex, comp_expired])
    db.flush()


def _seed_lumin(db: Session):
    """Seed Lumin Park tenant for cross-tenant tests."""
    t = Tenant(id="lumin", code="lumin", name="Lumin Park", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="lw1", tenant_id="lumin", code="LW001", name="Sari", is_active=True)
    db.add(w)
    eq = Equipment(
        id="lex1", tenant_id="lumin", equipment_code="L-EX01",
        equipment_type="EXCAVATOR", status=EquipmentStatus.ACTIVE,
        effective_from=date(2026, 1, 1),
    )
    db.add(eq)
    db.flush()


def _create_open_exception(db, tenant_id="metro", employee_id="w1",
                           exception_type="EQUIPMENT_MISMATCH",
                           severity=ExceptionSeverity.CRITICAL,
                           operating_date=None, shift_id="DAY", equipment_id="ex25"):
    """Create an OPEN ExceptionCase for decision tests."""
    case = ExceptionCase(
        id=_uid(), tenant_id=tenant_id,
        exception_type=exception_type, severity=severity,
        status=ExceptionStatus.OPEN,
        employee_id=employee_id,
        operating_date=operating_date or date(2026, 9, 5),
        shift_id=shift_id, equipment_id=equipment_id, site_id=None,
        source_type=ExceptionSourceType.EQUIPMENT_DISCREPANCY.value,
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


# ── Decision Request Tests ────────────────────────────────────

class TestDecisionRequest:
    """M3C-01 to M3C-06: Decision request creation."""

    def test_m3c_01_operator_substitution_request(self, db):
        """M3C-01: Create operator substitution request."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        assert dec.status == DecisionStatus.PENDING
        assert dec.decision_type == DecisionType.OPERATOR_SUBSTITUTION
        assert dec.planned_worker_id == "w1"
        assert dec.actual_worker_id == "w2"
        assert dec.actual_equipment_id == "ex25"
        assert dec.tenant_id == "metro"
        assert dec.exception_id == case.id

    def test_m3c_02_equipment_substitution_request(self, db):
        """M3C-02: Create equipment substitution request."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            planned_equipment_id="ex25",
            actual_worker_id="w1",
            actual_equipment_id="ex31",
        )

        assert dec.status == DecisionStatus.PENDING
        assert dec.decision_type == DecisionType.EQUIPMENT_SUBSTITUTION
        assert dec.planned_equipment_id == "ex25"
        assert dec.actual_equipment_id == "ex31"

    def test_m3c_03_operational_override_request(self, db):
        """M3C-03: Create operational override request."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATIONAL_OVERRIDE,
            requested_by="sup1",
            reason_text="Emergency operational override",
        )

        assert dec.status == DecisionStatus.PENDING
        assert dec.decision_type == DecisionType.OPERATIONAL_OVERRIDE

    def test_m3c_04_decision_linked_to_exception(self, db):
        """M3C-04: Decision is linked to the correct exception case."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        decisions = get_decisions_for_exception(db, case.id, "metro")
        assert len(decisions) == 1
        assert decisions[0].exception_id == case.id

    def test_m3c_05_request_creates_audit_action(self, db):
        """M3C-05: Request creates immutable audit action."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        history = get_decision_history(db, dec.id, "metro")
        assert len(history) == 1
        assert history[0].action_type == "REQUEST"
        assert history[0].actor_user_id == "sup1"
        assert history[0].new_status == DecisionStatus.PENDING

    def test_m3c_06_request_nonexistent_exception(self, db):
        """M3C-06: Request for nonexistent exception raises ValueError."""
        _seed_metro(db)
        db.commit()

        with pytest.raises(ValueError, match="not found"):
            request_decision(
                db, "metro", "nonexistent",
                DecisionType.OPERATOR_SUBSTITUTION,
                requested_by="sup1",
            )


# ── Approval Tests ────────────────────────────────────────────

class TestApproval:
    """M3C-07 to M3C-18: Decision approval with validation."""

    def test_m3c_07_approve_operator_substitution(self, db):
        """M3C-07: Approve operator substitution with valid worker."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        approved = approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Tono called in sick, Budi is qualified",
            authorization_policy="SIM_APPROVER",
        )

        assert approved.status == DecisionStatus.APPROVED
        assert approved.decided_by == "sup1"
        assert approved.decided_at is not None
        assert approved.reason_text == "Tono called in sick, Budi is qualified"

    def test_m3c_08_approve_equipment_substitution(self, db):
        """M3C-08: Approve equipment substitution with valid equipment."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            planned_equipment_id="ex25",
            actual_worker_id="w1",
            actual_equipment_id="ex31",
        )

        approved = approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="EX-025 under maintenance",
            authorization_policy="SIM_APPROVER",
        )

        assert approved.status == DecisionStatus.APPROVED
        assert approved.planned_equipment_id == "ex25"
        assert approved.actual_equipment_id == "ex31"

    def test_m3c_09_planned_history_preserved(self, db):
        """M3C-09: Approval does NOT rewrite planned assignment."""
        _seed_metro(db)
        case = _create_open_exception(db, employee_id="w1")
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved substitution",
            authorization_policy="SIM_APPROVER",
        )

        # Verify planned values preserved in decision
        stored = get_decision(db, dec.id, "metro")
        assert stored.planned_worker_id == "w1"

        # Verify exception case employee_id unchanged
        from app.exception_engine import get_exception
        exc = get_exception(db, case.id, "metro")
        assert exc.employee_id == "w1"

    def test_m3c_10_actual_history_preserved(self, db):
        """M3C-10: Approval does NOT rewrite actual event data."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

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
            reason_text="EX-025 breakdown",
            authorization_policy="SIM_APPROVER",
        )

        stored = get_decision(db, dec.id, "metro")
        assert stored.actual_equipment_id == "ex31"
        assert stored.planned_equipment_id == "ex25"
        # Both remain different — history preserved

    def test_m3c_11_approval_does_not_erase_detection(self, db):
        """M3C-11: Approval does not erase original mismatch/detection."""
        _seed_metro(db)
        case = _create_open_exception(db, exception_type="EQUIPMENT_MISMATCH")
        db.commit()

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

        # Exception still exists with original detection data
        exc = get_exception(db, case.id, "metro")
        assert exc is not None
        assert exc.exception_type == "EQUIPMENT_MISMATCH"
        assert exc.equipment_id == "ex25"  # original planned

    def test_m3c_12_inactive_worker_blocks_approval(self, db):
        """M3C-12: Inactive worker blocks approval."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w3",  # inactive
            actual_equipment_id="ex25",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("inactive" in f for f in exc_info.value.failures)

    def test_m3c_13_expired_competency_blocks_approval(self, db):
        """M3C-13: Expired competency blocks approval."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        # w3 has expired competency; use w1 but point to non-existent equipment type
        # Better: use w3 which has EXPIRED competency
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="w3",  # has EXPIRED competency
            actual_equipment_id="ex25",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("inactive" in f or "expired" in f or "competency" in f for f in exc_info.value.failures)

    def test_m3c_14_out_of_service_equipment_blocks_approval(self, db):
        """M3C-14: OUT_OF_SERVICE equipment blocks approval."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex_oos",
            actual_worker_id="w1",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("OUT_OF_SERVICE" in f for f in exc_info.value.failures)

    def test_m3c_15_inactive_equipment_blocks_approval(self, db):
        """M3C-15: Inactive equipment blocks approval."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex_inactive",
            actual_worker_id="w1",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("inactive" in f for f in exc_info.value.failures)

    def test_m3c_16_missing_competency_blocks_approval(self, db):
        """M3C-16: Missing competency blocks approval."""
        _seed_metro(db)
        # w1 has competency for EXCAVATOR but no competency for BULLDOZER
        dozer = Equipment(
            id="ex_doz", tenant_id="metro", equipment_code="BD-001",
            equipment_type="BULLDOZER", status=EquipmentStatus.ACTIVE,
            effective_from=date(2026, 1, 1),
        )
        db.add(dozer)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="ex_doz",
            actual_worker_id="w1",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("competency" in f for f in exc_info.value.failures)

    def test_m3c_17_cross_tenant_worker_rejected(self, db):
        """M3C-17: Cross-tenant worker reference rejected during validation."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            planned_worker_id="w1",
            actual_worker_id="lw1",  # Lumin worker!
            actual_equipment_id="ex25",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("not found" in f or "tenant" in f for f in exc_info.value.failures)

    def test_m3c_18_cross_tenant_equipment_rejected(self, db):
        """M3C-18: Cross-tenant equipment reference rejected during validation."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            planned_equipment_id="ex25",
            actual_equipment_id="lex1",  # Lumin equipment!
            actual_worker_id="w1",
        )

        with pytest.raises(DecisionValidationFailed) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Should fail",
                authorization_policy="SIM_APPROVER",
            )
        assert any("not found" in f or "tenant" in f for f in exc_info.value.failures)


# ── Authorization Tests ───────────────────────────────────────

class TestAuthorization:
    """M3C-19 to M3C-24: Authorization policy tests."""

    def test_m3c_19_missing_authorization_blocks(self, db):
        """M3C-19: Missing authorization policy → BLOCKED_POLICY_DECISION."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        # M3D FIX: Missing authorization policy → AuthorizationBlocked
        # Previously: approval succeeded silently with BLOCKED_POLICY_DECISION recorded.
        # Correct: missing auth blocks approval, decision remains PENDING.
        with pytest.raises(AuthorizationBlocked) as exc_info:
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="Approved without policy",
                # No authorization_policy
            )
        assert "BLOCKED_POLICY_DECISION" in str(exc_info.value) or "no valid policy" in str(exc_info.value)

        # Decision remains PENDING
        fresh = get_decision(db, dec.id, "metro")
        assert fresh.status == DecisionStatus.PENDING

    def test_m3c_20_no_automatic_approval(self, db):
        """M3C-20: Decision starts PENDING, never auto-approved."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        # Verify PENDING
        stored = get_decision(db, dec.id, "metro")
        assert stored.status == DecisionStatus.PENDING
        assert stored.decided_by is None
        assert stored.decided_at is None

    def test_m3c_21_simulation_authorization_works(self, db):
        """M3C-21: Simulation authorization policy allows approval."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        approved = approve_decision(
            db, dec.id, "metro", "sim_actor",
            reason_text="Simulation approved",
            authorization_policy="SIM_APPROVER",
        )

        assert approved.status == DecisionStatus.APPROVED
        history = get_decision_history(db, dec.id, "metro")
        approve_action = [a for a in history if a.action_type == "APPROVE"][0]
        assert "SIM_APPROVER" in approve_action.authorization_result

    def test_m3c_22_cross_tenant_actor_rejected(self, db):
        """M3C-22: Cross-tenant actor cannot access Metro decision."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        # Try to approve with Lumin tenant_id
        with pytest.raises(ValueError, match="not found for tenant"):
            approve_decision(
                db, dec.id, "lumin", "lumin_actor",
                reason_text="Cross-tenant attempt",
                authorization_policy="SIM_APPROVER",
            )

    def test_m3c_23_approval_reason_required(self, db):
        """M3C-23: Approval without reason raises ValueError."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        with pytest.raises(ValueError, match="Reason"):
            approve_decision(
                db, dec.id, "metro", "sup1",
                reason_text="",
                authorization_policy="SIM_APPROVER",
            )

    def test_m3c_24_decision_timestamp_recorded(self, db):
        """M3C-24: Decision timestamp is recorded on approval."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        before = _utcnow_for_test()
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        stored = get_decision(db, dec.id, "metro")
        assert stored.decided_at is not None
        assert stored.decided_at >= before


# ── Rejection Tests ───────────────────────────────────────────

class TestRejection:
    """M3C-25 to M3C-30: Decision rejection tests."""

    def test_m3c_25_reject_decision(self, db):
        """M3C-25: Reject a PENDING decision."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        rejected = reject_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Not enough evidence of emergency",
        )

        assert rejected.status == DecisionStatus.REJECTED
        assert rejected.decided_by == "sup1"
        assert rejected.reason_text == "Not enough evidence of emergency"

    def test_m3c_26_rejection_reason_required(self, db):
        """M3C-26: Rejection without reason raises ValueError."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        with pytest.raises(ValueError, match="Reason"):
            reject_decision(db, dec.id, "metro", "sup1", reason_text="")

    def test_m3c_27_rejected_decision_preserved(self, db):
        """M3C-27: Rejected decision remains in history."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        reject_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Insufficient evidence",
        )

        # Decision still accessible
        stored = get_decision(db, dec.id, "metro")
        assert stored is not None
        assert stored.status == DecisionStatus.REJECTED

        # History preserved
        history = get_decision_history(db, dec.id, "metro")
        assert len(history) == 2  # REQUEST + REJECT
        assert history[0].action_type == "REQUEST"
        assert history[1].action_type == "REJECT"

    def test_m3c_28_rejection_preserves_exception(self, db):
        """M3C-28: Rejection does not alter original exception."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        reject_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Denied",
        )

        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.OPEN  # unchanged

    def test_m3c_29_new_request_after_rejection(self, db):
        """M3C-29: New request does not overwrite rejected decision."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec1 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )
        reject_decision(db, dec1.id, "metro", "sup1", reason_text="Denied")

        # New request for same type — creates new PENDING
        dec2 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )

        assert dec2.id != dec1.id
        assert dec2.status == DecisionStatus.PENDING

        decisions = get_decisions_for_exception(db, case.id, "metro")
        assert len(decisions) == 2

    def test_m3c_30_rejection_actor_recorded(self, db):
        """M3C-30: Rejection records actor and timestamp."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        reject_decision(
            db, dec.id, "metro", "mgr1",
            reason_text="Budget constraints",
        )

        stored = get_decision(db, dec.id, "metro")
        assert stored.decided_by == "mgr1"
        assert stored.decided_at is not None

        history = get_decision_history(db, dec.id, "metro")
        reject_action = [a for a in history if a.action_type == "REJECT"][0]
        assert reject_action.actor_user_id == "mgr1"


# ── Idempotency & Concurrency Tests ──────────────────────────

class TestIdempotencyConcurrency:
    """M3C-31 to M3C-35: Idempotency and concurrency."""

    def test_m3c_31_duplicate_request_idempotent(self, db):
        """M3C-31: Duplicate PENDING request returns existing."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec1 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )
        dec2 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )

        assert dec1.id == dec2.id  # same object returned

    def test_m3c_32_duplicate_approval_idempotent(self, db):
        """M3C-32: Second approval attempt raises InvalidDecisionTransition."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="First approval",
            authorization_policy="SIM_APPROVER",
        )

        with pytest.raises(InvalidDecisionTransition):
            approve_decision(
                db, dec.id, "metro", "sup2",
                reason_text="Second approval",
                authorization_policy="SIM_APPROVER",
            )

    def test_m3c_33_duplicate_rejection_idempotent(self, db):
        """M3C-33: Second rejection attempt raises InvalidDecisionTransition."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        reject_decision(db, dec.id, "metro", "sup1", reason_text="First rejection")

        with pytest.raises(InvalidDecisionTransition):
            reject_decision(db, dec.id, "metro", "sup2", reason_text="Second rejection")

    def test_m3c_34_simultaneous_approve_protected(self, db):
        """M3C-34: Simultaneous approve/reject — first wins.

        Note: SQLite in-memory doesn't support true concurrent writes.
        We test the logical protection: once decided, second attempt fails.
        """
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        # First decision wins
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="First approval wins",
            authorization_policy="SIM_APPROVER",
        )

        # Second attempt (simulating concurrent loser) must fail
        with pytest.raises(InvalidDecisionTransition):
            reject_decision(
                db, dec.id, "metro", "sup2",
                reason_text="Late rejection",
            )

    def test_m3c_35_duplicate_active_pending_prevented(self, db):
        """M3C-35: Cannot create duplicate active PENDING for same exception+type."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        # First request
        dec1 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )

        # Second request same type — idempotent (returns existing)
        dec2 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )

        assert dec1.id == dec2.id

        # Only one PENDING decision
        pending = get_decisions_for_exception(
            db, case.id, "metro", status=DecisionStatus.PENDING
        )
        assert len(pending) == 1


# ── Exception Lifecycle Interaction Tests ─────────────────────

class TestExceptionLifecycle:
    """M3C-36 to M3C-38: Exception lifecycle interaction."""

    def test_m3c_36_approval_does_not_auto_resolve(self, db):
        """M3C-36: Approved substitution does NOT silently resolve exception."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved substitution",
            authorization_policy="SIM_APPROVER",
        )

        # Exception still OPEN — approval ≠ resolution
        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.OPEN

    def test_m3c_37_explicit_resolution_after_approval(self, db):
        """M3C-37: Explicit resolution is separate and auditable."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        # First acknowledge
        acknowledge_exception(db, case.id, "metro", "sup1", note="Reviewing")

        # Then approve decision
        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        # Explicit resolve
        resolve_exception(db, case.id, "metro", "sup1", note="Resolved after approval")

        exc = get_exception(db, case.id, "metro")
        assert exc.status == ExceptionStatus.RESOLVED

    def test_m3c_38_cancel_decision(self, db):
        """M3C-38: Cancel a PENDING decision."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        cancelled = cancel_decision(
            db, dec.id, "metro", "sup1",
            reason_text="No longer needed",
        )

        assert cancelled.status == DecisionStatus.CANCELLED
        assert cancelled.decided_by == "sup1"


# ── Audit & Integrity Tests ───────────────────────────────────

class TestAuditIntegrity:
    """M3C-39 to M3C-45: Audit trail and data integrity."""

    def test_m3c_39_decision_history_immutable(self, db):
        """M3C-39: Decision history actions are immutable."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        history = get_decision_history(db, dec.id, "metro")
        assert len(history) == 2  # REQUEST + APPROVE

        # History records are separate from decision
        assert history[0].action_type == "REQUEST"
        assert history[1].action_type == "APPROVE"
        assert history[0].actor_user_id == "sup1"
        assert history[1].actor_user_id == "sup1"

    def test_m3c_40_rule_version_preserved(self, db):
        """M3C-40: Decision preserves rule version reference."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            rule_version_id="rv1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )

        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        stored = get_decision(db, dec.id, "metro")
        assert stored.rule_version_id == "rv1"

    def test_m3c_41_no_payroll_consequence(self, db):
        """M3C-41: Decision does not create payroll consequence."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
            actual_equipment_id="ex25",
        )
        approve_decision(
            db, dec.id, "metro", "sup1",
            reason_text="Approved",
            authorization_policy="SIM_APPROVER",
        )

        # No payroll tables modified — verify decision metadata is clean
        stored = get_decision(db, dec.id, "metro")
        assert stored.metadata_json is None  # no payroll data injected

    def test_m3c_42_tenant_isolation_decision_access(self, db):
        """M3C-42: Tenant cannot access other tenant's decisions."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db)
        db.commit()

        dec = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        # Lumin cannot see Metro decision
        result = get_decision(db, dec.id, "lumin")
        assert result is None

    def test_m3c_43_tenant_isolation_decision_list(self, db):
        """M3C-43: Decision list scoped to tenant."""
        _seed_metro(db)
        _seed_lumin(db)
        case = _create_open_exception(db)
        db.commit()

        request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
        )

        # Lumin has no decisions
        lumin_decisions = get_decisions_for_exception(db, case.id, "lumin")
        assert len(lumin_decisions) == 0

    def test_m3c_44_get_decision_not_found(self, db):
        """M3C-44: Get nonexistent decision returns None."""
        _seed_metro(db)
        db.commit()

        result = get_decision(db, "nonexistent", "metro")
        assert result is None

    def test_m3c_45_multiple_decision_types_for_exception(self, db):
        """M3C-45: Multiple different decision types for same exception."""
        _seed_metro(db)
        case = _create_open_exception(db)
        db.commit()

        # Operator substitution
        dec1 = request_decision(
            db, "metro", case.id,
            DecisionType.OPERATOR_SUBSTITUTION,
            requested_by="sup1",
            actual_worker_id="w2",
        )

        # Equipment substitution — different type, allowed
        dec2 = request_decision(
            db, "metro", case.id,
            DecisionType.EQUIPMENT_SUBSTITUTION,
            requested_by="sup1",
            actual_equipment_id="ex31",
        )

        assert dec1.id != dec2.id
        assert dec1.decision_type == DecisionType.OPERATOR_SUBSTITUTION
        assert dec2.decision_type == DecisionType.EQUIPMENT_SUBSTITUTION

        all_decisions = get_decisions_for_exception(db, case.id, "metro")
        assert len(all_decisions) == 2


# ── Helper ────────────────────────────────────────────────────

def _utcnow_for_test():
    return datetime.now(timezone.utc)
