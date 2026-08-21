"""
exception_engine.py — M3A Exception Lifecycle Foundation.

Converts detection results from M1/M2 into auditable operational cases.
Provides: creation eligibility, lifecycle transitions, idempotency, audit trail.

Architecture:
    Detection (RuleEvaluation / EquipmentDiscrepancy / CheckpointValidation)
    → ExceptionCase (OPEN)
    → ExceptionAction (lifecycle history)
    → Terminal state (RESOLVED / WAIVED)

Immutable: original detection is never rewritten.
Idempotent: same source → same exception, no duplicates.
Tenant-isolated: all queries scoped by tenant_id.
"""

from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import (
    ExceptionCase,
    ExceptionAction,
    ExceptionActionType,
    ExceptionSourceType,
    ExceptionStatus,
    ExceptionSeverity,
    EXCEPTION_TRANSITIONS,
    RuleEvaluation,
    RuleEvaluationStatus,
    EquipmentDiscrepancy,
    DiscrepancyStatus,
    CheckpointValidationResult,
    CheckpointValidationStatus,
    uid,
    now,
)


# ── Creation eligibility ──────────────────────────────────────

# RuleEvaluationStatus values that create an exception
_ELIGIBLE_RULE_STATUSES = {RuleEvaluationStatus.FAIL}

# RuleEvaluationStatus values that NEVER create an employee exception
_SKIP_RULE_STATUSES = {
    RuleEvaluationStatus.PASS,
    RuleEvaluationStatus.NOT_APPLICABLE,
    RuleEvaluationStatus.CONFIG_INCOMPLETE,
    RuleEvaluationStatus.BLOCKED_POLICY_DECISION,
}

# DiscrepancyStatus values that create an exception
_ELIGIBLE_DISCREPANCY_STATUSES = {
    DiscrepancyStatus.OPEN,
    DiscrepancyStatus.PENDING_REVIEW,
}

# ValidationStatus values that create an exception
_ELIGIBLE_VALIDATION_STATUSES = {CheckpointValidationStatus.FAIL}


# ── Mapping: rule_code → exception_type ───────────────────────
# Rule codes from M2D map directly to exception types.
# Some rule codes may need mapping if names differ.
RULE_CODE_TO_EXCEPTION_TYPE = {
    "LATE_BREAK_RETURN": "LATE_BREAK_RETURN",
    "MISSING_BRIEFING": "MISSING_BRIEFING",
    "LATE_BRIEFING": "LATE_BRIEFING",
    "MISSING_SHIFT_OUT": "MISSING_SHIFT_OUT",
    "EARLY_HANDOVER": "EARLY_HANDOVER",
    "LATE_HANDOVER": "LATE_HANDOVER",
    "LOCATION_OUTSIDE_GEOFENCE": "LOCATION_OUTSIDE_GEOFENCE",
    "DEVICE_OR_IDENTITY_RISK": "DEVICE_OR_IDENTITY_RISK",
    "EQUIPMENT_MISMATCH": "EQUIPMENT_MISMATCH",
    "INSUFFICIENT_REST": "INSUFFICIENT_REST",
    "OFFSITE_ASSIGNMENT": "OFFSITE_ASSIGNMENT",
}

# Discrepancy type → exception type mapping
DISCREPANCY_TO_EXCEPTION_TYPE = {
    "EQUIPMENT_MISMATCH": "EQUIPMENT_MISMATCH",
    "OPERATOR_SUBSTITUTION": "EQUIPMENT_MISMATCH",
}


def _utcnow() -> datetime:
    """UTC now with timezone."""
    return datetime.now(timezone.utc)


# ── Create from RuleEvaluation ────────────────────────────────

def create_exception_from_rule_evaluation(
    db: Session,
    rule_eval: RuleEvaluation,
) -> ExceptionCase | None:
    """Create an ExceptionCase from a FAIL RuleEvaluation.

    Returns the new ExceptionCase, or None if:
    - status is not FAIL (PASS/NOT_APPLICABLE/CONFIG_INCOMPLETE/BLOCKED_POLICY_DECISION)
    - exception already exists (idempotent)

    Original RuleEvaluation is NEVER modified.
    """
    if rule_eval.status not in _ELIGIBLE_RULE_STATUSES:
        return None

    exception_type = RULE_CODE_TO_EXCEPTION_TYPE.get(
        rule_eval.rule_code, rule_eval.rule_code
    )

    # Idempotency check
    existing = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.tenant_id == rule_eval.tenant_id,
            ExceptionCase.source_type == ExceptionSourceType.RULE_EVALUATION.value,
            ExceptionCase.source_id == rule_eval.id,
            ExceptionCase.exception_type == exception_type,
        )
    ).first()
    if existing:
        return existing

    severity = _map_severity(rule_eval.severity)
    detected_at = rule_eval.evaluated_at or rule_eval.created_at or _utcnow()

    case = ExceptionCase(
        id=uid(),
        tenant_id=rule_eval.tenant_id,
        exception_type=exception_type,
        severity=severity,
        status=ExceptionStatus.OPEN,
        employee_id=rule_eval.employee_id,
        operating_date=rule_eval.operating_date,
        shift_id=rule_eval.shift_id,
        equipment_id=rule_eval.equipment_id,
        site_id=None,
        source_type=ExceptionSourceType.RULE_EVALUATION.value,
        source_id=rule_eval.id,
        rule_version_id=rule_eval.rule_version_id,
        detected_at=detected_at,
        opened_at=_utcnow(),
        acknowledged_at=None,
        resolved_at=None,
        waived_at=None,
        current_owner_id=None,
        metadata_json=rule_eval.evidence_json,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(case)
    db.flush()
    return case


# ── Create from EquipmentDiscrepancy ──────────────────────────

def create_exception_from_discrepancy(
    db: Session,
    discrepancy: EquipmentDiscrepancy,
) -> ExceptionCase | None:
    """Create an ExceptionCase from an EquipmentDiscrepancy.

    Returns the new ExceptionCase, or None if:
    - status is not eligible (not OPEN/PENDING_REVIEW)
    - exception already exists (idempotent)

    Original EquipmentDiscrepancy is NEVER modified.
    """
    if discrepancy.status not in _ELIGIBLE_DISCREPANCY_STATUSES:
        return None

    exception_type = DISCREPANCY_TO_EXCEPTION_TYPE.get(
        discrepancy.discrepancy_type.value
        if hasattr(discrepancy.discrepancy_type, 'value')
        else str(discrepancy.discrepancy_type),
        "EQUIPMENT_MISMATCH",
    )

    # Idempotency check
    existing = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.tenant_id == discrepancy.tenant_id,
            ExceptionCase.source_type == ExceptionSourceType.EQUIPMENT_DISCREPANCY.value,
            ExceptionCase.source_id == discrepancy.id,
            ExceptionCase.exception_type == exception_type,
        )
    ).first()
    if existing:
        return existing

    severity = ExceptionSeverity.WARNING  # equipment mismatch default
    detected_at = discrepancy.detected_at or _utcnow()

    case = ExceptionCase(
        id=uid(),
        tenant_id=discrepancy.tenant_id,
        exception_type=exception_type,
        severity=severity,
        status=ExceptionStatus.OPEN,
        employee_id=discrepancy.employee_id,
        operating_date=discrepancy.operating_date,
        shift_id=discrepancy.shift_id,
        equipment_id=discrepancy.actual_equipment_id,
        site_id=None,
        source_type=ExceptionSourceType.EQUIPMENT_DISCREPANCY.value,
        source_id=discrepancy.id,
        rule_version_id=discrepancy.rule_version_id,
        detected_at=detected_at,
        opened_at=_utcnow(),
        acknowledged_at=None,
        resolved_at=None,
        waived_at=None,
        current_owner_id=None,
        metadata_json=discrepancy.evidence_json,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(case)
    db.flush()
    return case


# ── Create from CheckpointValidationResult ─────────────────────

def create_exception_from_checkpoint(
    db: Session,
    checkpoint_result: CheckpointValidationResult,
) -> ExceptionCase | None:
    """Create an ExceptionCase from a FAIL CheckpointValidationResult.

    Returns the new ExceptionCase, or None if:
    - status is not FAIL
    - exception already exists (idempotent)

    Original CheckpointValidationResult is NEVER modified.
    """
    if checkpoint_result.validation_status not in _ELIGIBLE_VALIDATION_STATUSES:
        return None

    # Map checkpoint type to exception type
    exception_type = _checkpoint_type_to_exception(
        checkpoint_result.checkpoint_type
        if hasattr(checkpoint_result, 'checkpoint_type')
        else "CHECKPOINT_FAILURE"
    )

    # Idempotency check
    existing = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.tenant_id == checkpoint_result.tenant_id,
            ExceptionCase.source_type == ExceptionSourceType.CHECKPOINT_VALIDATION.value,
            ExceptionCase.source_id == checkpoint_result.id,
            ExceptionCase.exception_type == exception_type,
        )
    ).first()
    if existing:
        return existing

    detected_at = checkpoint_result.detected_timestamp or _utcnow()

    case = ExceptionCase(
        id=uid(),
        tenant_id=checkpoint_result.tenant_id,
        exception_type=exception_type,
        severity=ExceptionSeverity.WARNING,
        status=ExceptionStatus.OPEN,
        employee_id=checkpoint_result.employee_id,
        operating_date=checkpoint_result.operating_date,
        shift_id=checkpoint_result.shift_id,
        equipment_id=None,
        site_id=None,
        source_type=ExceptionSourceType.CHECKPOINT_VALIDATION.value,
        source_id=checkpoint_result.id,
        rule_version_id=None,
        detected_at=detected_at,
        opened_at=_utcnow(),
        acknowledged_at=None,
        resolved_at=None,
        waived_at=None,
        current_owner_id=None,
        metadata_json=None,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(case)
    db.flush()
    return case


# ── Lifecycle Transitions ─────────────────────────────────────

def acknowledge_exception(
    db: Session,
    exception_id: str,
    tenant_id: str,
    actor_user_id: str,
    reason: str | None = None,
    note: str | None = None,
) -> ExceptionCase:
    """Transition an exception from OPEN → ACKNOWLEDGED.

    Raises ValueError if:
    - exception not found
    - tenant mismatch
    - invalid transition (not OPEN)
    """
    case = _get_case_for_tenant(db, exception_id, tenant_id)
    _validate_transition(case.status, ExceptionStatus.ACKNOWLEDGED)

    prev_status = case.status
    case.status = ExceptionStatus.ACKNOWLEDGED
    case.acknowledged_at = _utcnow()
    case.current_owner_id = actor_user_id
    case.updated_at = _utcnow()

    _create_action(
        db, case, ExceptionActionType.ACKNOWLEDGE,
        actor_user_id, prev_status, ExceptionStatus.ACKNOWLEDGED,
        reason=reason, note=note,
    )
    db.flush()
    return case


def resolve_exception(
    db: Session,
    exception_id: str,
    tenant_id: str,
    actor_user_id: str,
    reason: str | None = None,
    note: str | None = None,
    evidence_ref: str | None = None,
) -> ExceptionCase:
    """Transition an exception to RESOLVED.

    Valid from: OPEN or ACKNOWLEDGED.
    Raises ValueError on invalid transition or tenant mismatch.
    """
    case = _get_case_for_tenant(db, exception_id, tenant_id)
    _validate_transition(case.status, ExceptionStatus.RESOLVED)

    prev_status = case.status
    case.status = ExceptionStatus.RESOLVED
    case.resolved_at = _utcnow()
    case.current_owner_id = actor_user_id
    case.updated_at = _utcnow()

    _create_action(
        db, case, ExceptionActionType.RESOLVE,
        actor_user_id, prev_status, ExceptionStatus.RESOLVED,
        reason=reason, note=note, evidence_ref=evidence_ref,
    )
    db.flush()
    return case


def waive_exception(
    db: Session,
    exception_id: str,
    tenant_id: str,
    actor_user_id: str,
    reason: str,
    note: str | None = None,
    evidence_ref: str | None = None,
) -> ExceptionCase:
    """Transition an exception to WAIVED.

    Valid from: OPEN or ACKNOWLEDGED.
    reason is REQUIRED for waiver.
    Raises ValueError on invalid transition, missing reason, or tenant mismatch.
    """
    if not reason or not reason.strip():
        raise ValueError("Waiver requires a documented reason.")

    case = _get_case_for_tenant(db, exception_id, tenant_id)
    _validate_transition(case.status, ExceptionStatus.WAIVED)

    prev_status = case.status
    case.status = ExceptionStatus.WAIVED
    case.waived_at = _utcnow()
    case.current_owner_id = actor_user_id
    case.updated_at = _utcnow()

    _create_action(
        db, case, ExceptionActionType.WAIVE,
        actor_user_id, prev_status, ExceptionStatus.WAIVED,
        reason=reason, note=note, evidence_ref=evidence_ref,
    )
    db.flush()
    return case


# ── Queries ───────────────────────────────────────────────────

def get_exception(db: Session, exception_id: str, tenant_id: str) -> ExceptionCase | None:
    """Get an exception case by ID, scoped to tenant."""
    return db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()


def get_exceptions_for_employee(
    db: Session,
    tenant_id: str,
    employee_id: str,
    operating_date: date | None = None,
    status: ExceptionStatus | None = None,
) -> list[ExceptionCase]:
    """Get exception cases for an employee, optionally filtered."""
    q = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.tenant_id == tenant_id,
            ExceptionCase.employee_id == employee_id,
        )
    )
    if operating_date:
        q = q.filter(ExceptionCase.operating_date == operating_date)
    if status:
        q = q.filter(ExceptionCase.status == status)
    return q.order_by(ExceptionCase.operating_date.desc()).all()


def get_action_history(
    db: Session,
    exception_id: str,
    tenant_id: str,
) -> list[ExceptionAction]:
    """Get immutable action history for an exception case."""
    return db.query(ExceptionAction).filter(
        and_(
            ExceptionAction.exception_id == exception_id,
            ExceptionAction.tenant_id == tenant_id,
        )
    ).order_by(ExceptionAction.action_timestamp.asc()).all()


# ── Internal Helpers ──────────────────────────────────────────

def _get_case_for_tenant(
    db: Session, exception_id: str, tenant_id: str
) -> ExceptionCase:
    """Get exception case or raise ValueError."""
    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        raise ValueError(
            f"ExceptionCase {exception_id} not found for tenant {tenant_id}"
        )
    return case


def _validate_transition(
    current: ExceptionStatus, target: ExceptionStatus
) -> None:
    """Validate that a lifecycle transition is allowed."""
    allowed = EXCEPTION_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Invalid transition: {current.value} → {target.value}. "
            f"Allowed from {current.value}: "
            f"{[s.value for s in allowed] if allowed else 'none (terminal)'}"
        )


def _create_action(
    db: Session,
    case: ExceptionCase,
    action_type: ExceptionActionType,
    actor_user_id: str,
    previous_status: ExceptionStatus,
    new_status: ExceptionStatus,
    reason: str | None = None,
    note: str | None = None,
    evidence_ref: str | None = None,
) -> ExceptionAction:
    """Create an immutable action history record."""
    action = ExceptionAction(
        id=uid(),
        tenant_id=case.tenant_id,
        exception_id=case.id,
        action_type=action_type,
        actor_user_id=actor_user_id,
        action_timestamp=_utcnow(),
        previous_status=previous_status,
        new_status=new_status,
        reason=reason,
        note=note,
        evidence_ref=evidence_ref,
        metadata_json=None,
        created_at=_utcnow(),
    )
    db.add(action)
    return action


def _map_severity(rule_severity) -> ExceptionSeverity:
    """Map RuleSeverity to ExceptionSeverity (same values, different types)."""
    val = rule_severity.value if hasattr(rule_severity, 'value') else str(rule_severity)
    try:
        return ExceptionSeverity(val)
    except ValueError:
        return ExceptionSeverity.WARNING


def _checkpoint_type_to_exception(checkpoint_type: str) -> str:
    """Map checkpoint type to exception type."""
    mapping = {
        "BRIEFING_IN": "MISSING_BRIEFING",
        "EQUIPMENT_CHECK_IN": "EQUIPMENT_MISMATCH",
        "HANDOVER_START": "EARLY_HANDOVER",
        "HANDOVER_END": "LATE_HANDOVER",
        "CHECK_OUT": "MISSING_SHIFT_OUT",
    }
    return mapping.get(checkpoint_type, "CHECKPOINT_FAILURE")
