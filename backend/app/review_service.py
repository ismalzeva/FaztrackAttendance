"""
review_service.py — M3B Supervisor Review & Evidence service.

Provides:
- Review queue (filtered, paginated, tenant-scoped)
- Review detail (full exception context)
- Evidence attachment (reference-based, system + human)
- Review notes (append-only)
- Case ownership (assign/reassign)
- Timeline (chronological case history)

Architecture:
    ExceptionCase (M3A)
    → Review Queue (filtered queries)
    → Evidence Context (ExceptionEvidence references)
    → Supervisor Review (acknowledge, notes, evidence)
    → Timeline (system + human events combined)

Tenant-isolated: all queries scoped by tenant_id.
Evidence immutable: system-generated evidence cannot be edited by supervisor.
"""

from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models import (
    ExceptionCase,
    ExceptionAction,
    ExceptionActionType,
    ExceptionEvidence,
    ExceptionEvidenceType,
    ExceptionSourceType,
    ExceptionStatus,
    ExceptionSeverity,
    uid,
    now,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Review Queue ────────────────────────────────────────────

ACTIVE_STATUSES = {ExceptionStatus.OPEN, ExceptionStatus.ACKNOWLEDGED}
TERMINAL_STATUSES = {ExceptionStatus.RESOLVED, ExceptionStatus.WAIVED}


def get_review_queue(
    db: Session,
    tenant_id: str,
    *,
    active_only: bool = True,
    status: ExceptionStatus | None = None,
    severity: ExceptionSeverity | None = None,
    exception_type: str | None = None,
    employee_id: str | None = None,
    equipment_id: str | None = None,
    shift_id: str | None = None,
    operating_date: date | None = None,
    operating_date_from: date | None = None,
    operating_date_to: date | None = None,
    owner_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ExceptionCase]:
    """Get filtered, paginated review queue for a tenant.

    Default: active_only=True returns OPEN + ACKNOWLEDGED cases.
    Set active_only=False to include terminal cases (RESOLVED, WAIVED).
    """
    q = db.query(ExceptionCase).filter(ExceptionCase.tenant_id == tenant_id)

    if status:
        q = q.filter(ExceptionCase.status == status)
    elif active_only:
        q = q.filter(ExceptionCase.status.in_([
            ExceptionStatus.OPEN, ExceptionStatus.ACKNOWLEDGED
        ]))

    if severity:
        q = q.filter(ExceptionCase.severity == severity)
    if exception_type:
        q = q.filter(ExceptionCase.exception_type == exception_type)
    if employee_id:
        q = q.filter(ExceptionCase.employee_id == employee_id)
    if equipment_id:
        q = q.filter(ExceptionCase.equipment_id == equipment_id)
    if shift_id:
        q = q.filter(ExceptionCase.shift_id == shift_id)
    if operating_date:
        q = q.filter(ExceptionCase.operating_date == operating_date)
    if operating_date_from:
        q = q.filter(ExceptionCase.operating_date >= operating_date_from)
    if operating_date_to:
        q = q.filter(ExceptionCase.operating_date <= operating_date_to)
    if owner_id:
        q = q.filter(ExceptionCase.current_owner_id == owner_id)

    return q.order_by(
        ExceptionCase.severity.desc(),  # CRITICAL first
        ExceptionCase.detected_at.desc(),
    ).offset(offset).limit(limit).all()


# ── Review Detail ───────────────────────────────────────────

def get_review_detail(
    db: Session,
    exception_id: str,
    tenant_id: str,
) -> dict | None:
    """Get full review context for an exception case.

    Returns dict with:
    - exception: ExceptionCase fields
    - evidence: list of ExceptionEvidence records
    - actions: list of ExceptionAction records (history)
    - timeline: combined chronological view

    Returns None if not found or tenant mismatch.
    """
    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        return None

    evidence = db.query(ExceptionEvidence).filter(
        and_(
            ExceptionEvidence.exception_id == exception_id,
            ExceptionEvidence.tenant_id == tenant_id,
        )
    ).order_by(ExceptionEvidence.created_at.asc()).all()

    actions = db.query(ExceptionAction).filter(
        and_(
            ExceptionAction.exception_id == exception_id,
            ExceptionAction.tenant_id == tenant_id,
        )
    ).order_by(ExceptionAction.action_timestamp.asc()).all()

    timeline = _build_timeline(case, evidence, actions)

    return {
        "exception": _case_to_dict(case),
        "evidence": [_evidence_to_dict(e) for e in evidence],
        "actions": [_action_to_dict(a) for a in actions],
        "timeline": timeline,
    }


# ── Evidence Management ─────────────────────────────────────

def add_evidence(
    db: Session,
    exception_id: str,
    tenant_id: str,
    evidence_type: ExceptionEvidenceType,
    source_type: str,
    source_id: str,
    *,
    actor_user_id: str | None = None,
    captured_at: datetime | None = None,
    note: str | None = None,
    is_system_generated: bool = True,
) -> ExceptionEvidence:
    """Add evidence reference to an exception case.

    System-generated evidence (is_system_generated=True) is immutable.
    Human-added evidence records actor_user_id.

    Idempotent: same (tenant, exception, evidence_type, source_type, source_id)
    returns existing record.
    """
    # Verify exception exists and belongs to tenant
    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        raise ValueError(f"ExceptionCase {exception_id} not found for tenant {tenant_id}")

    # Idempotency check
    existing = db.query(ExceptionEvidence).filter(
        and_(
            ExceptionEvidence.tenant_id == tenant_id,
            ExceptionEvidence.exception_id == exception_id,
            ExceptionEvidence.evidence_type == evidence_type,
            ExceptionEvidence.source_type == source_type,
            ExceptionEvidence.source_id == source_id,
        )
    ).first()
    if existing:
        return existing

    ev = ExceptionEvidence(
        id=uid(),
        tenant_id=tenant_id,
        exception_id=exception_id,
        evidence_type=evidence_type,
        source_type=source_type,
        source_id=source_id,
        captured_at=captured_at,
        added_by=actor_user_id if not is_system_generated else None,
        is_system_generated=is_system_generated,
        note=note,
        metadata_json=None,
        created_at=_utcnow(),
    )
    db.add(ev)

    # Create audit action for evidence addition
    if not is_system_generated and actor_user_id:
        action = ExceptionAction(
            id=uid(),
            tenant_id=tenant_id,
            exception_id=exception_id,
            action_type=ExceptionActionType.ADD_EVIDENCE,
            actor_user_id=actor_user_id,
            action_timestamp=_utcnow(),
            previous_status=case.status,
            new_status=case.status,
            reason=None,
            note=f"Evidence added: {evidence_type.value} from {source_type}:{source_id}",
            evidence_ref=f"{source_type}:{source_id}",
            metadata_json=None,
            created_at=_utcnow(),
        )
        db.add(action)

    db.flush()
    return ev


def get_evidence(
    db: Session,
    exception_id: str,
    tenant_id: str,
) -> list[ExceptionEvidence]:
    """Get all evidence for an exception case, tenant-scoped."""
    return db.query(ExceptionEvidence).filter(
        and_(
            ExceptionEvidence.exception_id == exception_id,
            ExceptionEvidence.tenant_id == tenant_id,
        )
    ).order_by(ExceptionEvidence.created_at.asc()).all()


def system_evidence_is_immutable(evidence: ExceptionEvidence) -> bool:
    """Check if evidence record is system-generated (immutable)."""
    return evidence.is_system_generated


# ── Review Notes ─────────────────────────────────────────────

def add_review_note(
    db: Session,
    exception_id: str,
    tenant_id: str,
    actor_user_id: str,
    note: str,
    evidence_ref: str | None = None,
) -> ExceptionAction:
    """Append a review note to an exception case.

    Notes are append-only — each call creates a new ExceptionAction.
    Previous notes are never overwritten.
    """
    if not note or not note.strip():
        raise ValueError("Review note cannot be empty.")

    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        raise ValueError(f"ExceptionCase {exception_id} not found for tenant {tenant_id}")

    action = ExceptionAction(
        id=uid(),
        tenant_id=tenant_id,
        exception_id=exception_id,
        action_type=ExceptionActionType.REVIEW_NOTE,
        actor_user_id=actor_user_id,
        action_timestamp=_utcnow(),
        previous_status=case.status,
        new_status=case.status,  # note does not change status
        reason=None,
        note=note,
        evidence_ref=evidence_ref,
        metadata_json=None,
        created_at=_utcnow(),
    )
    db.add(action)
    db.flush()
    return action


# ── Case Ownership ──────────────────────────────────────────

def assign_reviewer(
    db: Session,
    exception_id: str,
    tenant_id: str,
    actor_user_id: str,
    new_owner_id: str,
    reason: str | None = None,
) -> ExceptionCase:
    """Assign or reassign a reviewer to an exception case.

    Records the assignment as an audit action with previous/new owner.
    Cross-tenant assignment is rejected.
    """
    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        raise ValueError(f"ExceptionCase {exception_id} not found for tenant {tenant_id}")

    previous_owner = case.current_owner_id
    case.current_owner_id = new_owner_id
    case.updated_at = _utcnow()

    action = ExceptionAction(
        id=uid(),
        tenant_id=tenant_id,
        exception_id=exception_id,
        action_type=ExceptionActionType.ASSIGN_REVIEWER,
        actor_user_id=actor_user_id,
        action_timestamp=_utcnow(),
        previous_status=case.status,
        new_status=case.status,  # assignment does not change status
        reason=reason,
        note=f"Reviewer changed: {previous_owner} → {new_owner_id}",
        evidence_ref=None,
        metadata_json=None,
        created_at=_utcnow(),
    )
    db.add(action)
    db.flush()
    return case


# ── Timeline ────────────────────────────────────────────────

def get_timeline(
    db: Session,
    exception_id: str,
    tenant_id: str,
) -> list[dict]:
    """Get chronological timeline for an exception case.

    Combines: detection, evidence, and human actions.
    Returns sorted list of timeline entries.
    """
    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        return []

    evidence = db.query(ExceptionEvidence).filter(
        and_(
            ExceptionEvidence.exception_id == exception_id,
            ExceptionEvidence.tenant_id == tenant_id,
        )
    ).all()

    actions = db.query(ExceptionAction).filter(
        and_(
            ExceptionAction.exception_id == exception_id,
            ExceptionAction.tenant_id == tenant_id,
        )
    ).all()

    return _build_timeline(case, evidence, actions)


def _build_timeline(
    case: ExceptionCase,
    evidence: list[ExceptionEvidence],
    actions: list[ExceptionAction],
) -> list[dict]:
    """Build chronological timeline from case, evidence, and actions."""
    entries = []

    # Detection event
    entries.append({
        "timestamp": case.detected_at.isoformat(),
        "event_type": "DETECTION",
        "description": f"{case.exception_type} detected",
        "actor": None,
        "detail": {
            "severity": case.severity.value if hasattr(case.severity, 'value') else str(case.severity),
            "employee_id": case.employee_id,
            "operating_date": case.operating_date.isoformat() if hasattr(case.operating_date, 'isoformat') else str(case.operating_date),
        },
    })

    # Exception opened
    entries.append({
        "timestamp": case.opened_at.isoformat(),
        "event_type": "OPENED",
        "description": "Exception case opened",
        "actor": None,
        "detail": {"status": "OPEN"},
    })

    # Evidence events
    for ev in evidence:
        ts = ev.captured_at or ev.created_at
        entries.append({
            "timestamp": ts.isoformat() if ts else None,
            "event_type": "EVIDENCE",
            "description": f"Evidence: {ev.evidence_type.value}",
            "actor": ev.added_by,
            "detail": {
                "evidence_type": ev.evidence_type.value,
                "source_type": ev.source_type,
                "source_id": ev.source_id,
                "is_system": ev.is_system_generated,
                "note": ev.note,
            },
        })

    # Action events
    for act in actions:
        entries.append({
            "timestamp": act.action_timestamp.isoformat(),
            "event_type": "ACTION",
            "description": f"{act.action_type.value}: {act.previous_status.value} → {act.new_status.value}",
            "actor": act.actor_user_id,
            "detail": {
                "action_type": act.action_type.value,
                "previous_status": act.previous_status.value,
                "new_status": act.new_status.value,
                "reason": act.reason,
                "note": act.note,
                "evidence_ref": act.evidence_ref,
            },
        })

    # Sort by timestamp (None last)
    entries.sort(key=lambda e: e["timestamp"] or "9999-12-31")
    return entries


# ── Serializers ─────────────────────────────────────────────

def _case_to_dict(case: ExceptionCase) -> dict:
    """Serialize ExceptionCase to dict for review detail."""
    return {
        "id": case.id,
        "tenant_id": case.tenant_id,
        "exception_type": case.exception_type,
        "severity": case.severity.value if hasattr(case.severity, 'value') else str(case.severity),
        "status": case.status.value if hasattr(case.status, 'value') else str(case.status),
        "employee_id": case.employee_id,
        "operating_date": case.operating_date.isoformat() if hasattr(case.operating_date, 'isoformat') else str(case.operating_date),
        "shift_id": case.shift_id,
        "equipment_id": case.equipment_id,
        "site_id": case.site_id,
        "source_type": case.source_type,
        "source_id": case.source_id,
        "rule_version_id": case.rule_version_id,
        "detected_at": case.detected_at.isoformat() if case.detected_at else None,
        "opened_at": case.opened_at.isoformat() if case.opened_at else None,
        "acknowledged_at": case.acknowledged_at.isoformat() if case.acknowledged_at else None,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        "waived_at": case.waived_at.isoformat() if case.waived_at else None,
        "current_owner_id": case.current_owner_id,
        "metadata_json": case.metadata_json,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def _evidence_to_dict(ev: ExceptionEvidence) -> dict:
    """Serialize ExceptionEvidence to dict."""
    return {
        "id": ev.id,
        "evidence_type": ev.evidence_type.value if hasattr(ev.evidence_type, 'value') else str(ev.evidence_type),
        "source_type": ev.source_type,
        "source_id": ev.source_id,
        "captured_at": ev.captured_at.isoformat() if ev.captured_at else None,
        "added_by": ev.added_by,
        "is_system_generated": ev.is_system_generated,
        "note": ev.note,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


def _action_to_dict(act: ExceptionAction) -> dict:
    """Serialize ExceptionAction to dict."""
    return {
        "id": act.id,
        "action_type": act.action_type.value if hasattr(act.action_type, 'value') else str(act.action_type),
        "actor_user_id": act.actor_user_id,
        "action_timestamp": act.action_timestamp.isoformat() if act.action_timestamp else None,
        "previous_status": act.previous_status.value if hasattr(act.previous_status, 'value') else str(act.previous_status),
        "new_status": act.new_status.value if hasattr(act.new_status, 'value') else str(act.new_status),
        "reason": act.reason,
        "note": act.note,
        "evidence_ref": act.evidence_ref,
    }
