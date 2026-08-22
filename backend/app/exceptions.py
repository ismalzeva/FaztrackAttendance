"""
exceptions.py — MM-M4C Exception & Decision Workbench API.

Provides operational endpoints for the Field Supervisor workbench:
- Exception queue (filtered, paginated)
- Case detail (full context)
- Case timeline
- Lifecycle actions (acknowledge, resolve, waive)
- Review actions (notes, ownership)
- Decision actions (request, approve, reject, cancel)

All endpoints are tenant-scoped via authenticated context.
Delegates to M3 engines — no duplicated business logic.
"""

from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, tenant_context

from app.exception_workbench_service import (
    get_workbench_queue,
    get_case_detail,
    workbench_result_to_dict,
    case_detail_to_dict,
)

# M3 engines — delegate, don't duplicate
from app.exception_engine import (
    acknowledge_exception,
    resolve_exception,
    waive_exception,
    get_action_history,
)
from app.decision_engine import (
    request_decision,
    approve_decision,
    reject_decision,
    cancel_decision,
    AuthorizationBlocked,
    DecisionValidationFailed,
    DuplicateActiveDecision,
)
from app.review_service import (
    add_review_note,
    assign_reviewer,
    add_evidence,
)

from app.models import (
    DecisionType,
    ExceptionEvidenceType,
)

router = APIRouter(prefix="/api/v1/exceptions", tags=["exceptions"])


# ── Request Bodies ───────────────────────────────────────────

class AcknowledgeRequest(BaseModel):
    reason: str | None = None
    note: str | None = None


class ResolveRequest(BaseModel):
    reason: str | None = None
    note: str | None = None
    evidence_ref: str | None = None


class WaiveRequest(BaseModel):
    reason: str
    note: str | None = None
    evidence_ref: str | None = None


class NoteRequest(BaseModel):
    note: str
    evidence_ref: str | None = None


class AssignRequest(BaseModel):
    owner_id: str


class DecisionRequest(BaseModel):
    decision_type: str
    planned_worker_id: str | None = None
    planned_equipment_id: str | None = None
    actual_worker_id: str | None = None
    actual_equipment_id: str | None = None
    reason_text: str | None = None
    reason_code: str | None = None


class ApproveDecisionRequest(BaseModel):
    reason_text: str
    reason_code: str | None = None
    authorization_policy: str | None = None
    note: str | None = None
    evidence_ref: str | None = None


class RejectDecisionRequest(BaseModel):
    reason_text: str
    reason_code: str | None = None
    note: str | None = None


class CancelDecisionRequest(BaseModel):
    reason_text: str | None = None


# ── Case List ────────────────────────────────────────────────

@router.get("")
def list_exceptions(
    request: Request,
    active_only: bool = Query(True, description="Only active (OPEN+ACKNOWLEDGED)"),
    status: str | None = Query(None, description="Status filter"),
    severity: str | None = Query(None, description="Severity filter"),
    exception_type: str | None = Query(None, description="Exception type filter"),
    employee_search: str | None = Query(None, description="Employee name/code search"),
    crew_id: str | None = Query(None, description="Crew ID filter"),
    equipment_search: str | None = Query(None, description="Equipment code search"),
    operating_date: date | None = Query(None, description="Operating date"),
    operating_date_from: date | None = Query(None, description="Date range start"),
    operating_date_to: date | None = Query(None, description="Date range end"),
    shift_id: str | None = Query(None, description="Shift ID filter"),
    owner_id: str | None = Query(None, description="Owner ID filter"),
    decision_status: str | None = Query(None, description="Decision status filter"),
    sort_by: str = Query("severity", description="Sort field"),
    sort_dir: str = Query("desc", description="Sort direction"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=200, description="Pagination limit"),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Exception workbench queue. Default: active cases only."""
    result = get_workbench_queue(
        db=db,
        tenant_id=ctx.membership.tenant_id,
        active_only=active_only,
        status=status,
        severity=severity,
        exception_type=exception_type,
        employee_search=employee_search,
        crew_id=crew_id,
        equipment_search=equipment_search,
        operating_date=operating_date,
        operating_date_from=operating_date_from,
        operating_date_to=operating_date_to,
        shift_id=shift_id,
        owner_id=owner_id,
        decision_status=decision_status,
        sort_by=sort_by,
        sort_dir=sort_dir,
        offset=offset,
        limit=limit,
    )

    return {
        "data": workbench_result_to_dict(result),
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": result.context.generated_at.isoformat(),
            "version": "v1",
        },
    }


# ── Case Detail ──────────────────────────────────────────────

@router.get("/{exception_id}")
def get_exception_detail(
    request: Request,
    exception_id: str,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Full case detail: summary, plan/actual, decisions, evidence, timeline."""
    detail = get_case_detail(
        db=db,
        tenant_id=ctx.membership.tenant_id,
        exception_id=exception_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Exception not found")

    return {
        "data": case_detail_to_dict(detail),
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ── Case Timeline ────────────────────────────────────────────

@router.get("/{exception_id}/timeline")
def get_exception_timeline(
    request: Request,
    exception_id: str,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Case timeline: chronological combined view."""
    detail = get_case_detail(
        db=db,
        tenant_id=ctx.membership.tenant_id,
        exception_id=exception_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Exception not found")

    return {
        "data": [
            {
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "entry_type": t.entry_type,
                "description": t.description,
                "actor_id": t.actor_id,
                "actor_name": t.actor_name,
                "details": t.details,
            }
            for t in detail.timeline
        ],
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ── Lifecycle Actions ────────────────────────────────────────

@router.post("/{exception_id}/acknowledge")
def acknowledge_case(
    request: Request,
    exception_id: str,
    body: AcknowledgeRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Acknowledge an OPEN exception. OPEN → ACKNOWLEDGED."""
    try:
        case = acknowledge_exception(
            db=db,
            exception_id=exception_id,
            tenant_id=ctx.membership.tenant_id,
            actor_user_id=ctx.membership.user_id,
            reason=body.reason,
            note=body.note,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "case_id": case.id,
            "status": case.status.value,
            "acknowledged_at": case.acknowledged_at.isoformat() if case.acknowledged_at else None,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


@router.post("/{exception_id}/resolve")
def resolve_case(
    request: Request,
    exception_id: str,
    body: ResolveRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Resolve an exception. OPEN/ACKNOWLEDGED → RESOLVED."""
    try:
        case = resolve_exception(
            db=db,
            exception_id=exception_id,
            tenant_id=ctx.membership.tenant_id,
            actor_user_id=ctx.membership.user_id,
            reason=body.reason,
            note=body.note,
            evidence_ref=body.evidence_ref,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "case_id": case.id,
            "status": case.status.value,
            "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


@router.post("/{exception_id}/waive")
def waive_case(
    request: Request,
    exception_id: str,
    body: WaiveRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Waive an exception. OPEN/ACKNOWLEDGED → WAIVED. Reason required."""
    try:
        case = waive_exception(
            db=db,
            exception_id=exception_id,
            tenant_id=ctx.membership.tenant_id,
            actor_user_id=ctx.membership.user_id,
            reason=body.reason,
            note=body.note,
            evidence_ref=body.evidence_ref,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "case_id": case.id,
            "status": case.status.value,
            "waived_at": case.waived_at.isoformat() if case.waived_at else None,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ── Review Actions ───────────────────────────────────────────

@router.post("/{exception_id}/notes")
def add_note(
    request: Request,
    exception_id: str,
    body: NoteRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Add a supervisor review note. Append-only, actor-attributed."""
    try:
        action = add_review_note(
            db=db,
            exception_id=exception_id,
            tenant_id=ctx.membership.tenant_id,
            actor_user_id=ctx.membership.user_id,
            note=body.note,
            evidence_ref=body.evidence_ref,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "action_id": action.id,
            "action_type": action.action_type.value,
            "actor_user_id": action.actor_user_id,
            "action_timestamp": action.action_timestamp.isoformat(),
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


@router.post("/{exception_id}/assign")
def assign_owner(
    request: Request,
    exception_id: str,
    body: AssignRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Assign or reassign case ownership."""
    try:
        action = assign_reviewer(
            db=db,
            exception_id=exception_id,
            tenant_id=ctx.membership.tenant_id,
            actor_user_id=ctx.membership.user_id,
            new_owner_id=body.owner_id,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "action_id": action.id,
            "action_type": action.action_type.value,
            "new_owner_id": body.owner_id,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ── Decision Actions ─────────────────────────────────────────

@router.post("/{exception_id}/decisions")
def request_new_decision(
    request: Request,
    exception_id: str,
    body: DecisionRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Request a new decision for an exception."""
    try:
        dt = DecisionType(body.decision_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision type: {body.decision_type}",
        )

    try:
        decision = request_decision(
            db=db,
            tenant_id=ctx.membership.tenant_id,
            exception_id=exception_id,
            decision_type=dt,
            requested_by=ctx.membership.user_id,
            planned_worker_id=body.planned_worker_id,
            planned_equipment_id=body.planned_equipment_id,
            actual_worker_id=body.actual_worker_id,
            actual_equipment_id=body.actual_equipment_id,
            reason_text=body.reason_text,
            reason_code=body.reason_code,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DuplicateActiveDecision as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "data": {
            "decision_id": decision.id,
            "decision_type": decision.decision_type.value,
            "status": decision.status.value,
            "requested_at": decision.requested_at.isoformat(),
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ── Decision Approve/Reject/Cancel ──────────────────────────

@router.post("/decisions/{decision_id}/approve")
def approve_decision_endpoint(
    request: Request,
    decision_id: str,
    body: ApproveDecisionRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Approve a PENDING decision. Authorization checked by M3 engine."""
    try:
        decision = approve_decision(
            db=db,
            decision_id=decision_id,
            tenant_id=ctx.membership.tenant_id,
            decided_by=ctx.membership.user_id,
            reason_text=body.reason_text,
            reason_code=body.reason_code,
            authorization_policy=body.authorization_policy,
            note=body.note,
            evidence_ref=body.evidence_ref,
        )
        db.commit()
    except AuthorizationBlocked as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DecisionValidationFailed as e:
        raise HTTPException(status_code=422, detail=f"Validation failed: {'; '.join(e.failures)}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "decision_id": decision.id,
            "status": decision.status.value,
            "decided_by": decision.decided_by,
            "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


@router.post("/decisions/{decision_id}/reject")
def reject_decision_endpoint(
    request: Request,
    decision_id: str,
    body: RejectDecisionRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Reject a PENDING decision. Reason required."""
    try:
        decision = reject_decision(
            db=db,
            decision_id=decision_id,
            tenant_id=ctx.membership.tenant_id,
            decided_by=ctx.membership.user_id,
            reason_text=body.reason_text,
            reason_code=body.reason_code,
            note=body.note,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "decision_id": decision.id,
            "status": decision.status.value,
            "decided_by": decision.decided_by,
            "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


@router.post("/decisions/{decision_id}/cancel")
def cancel_decision_endpoint(
    request: Request,
    decision_id: str,
    body: CancelDecisionRequest,
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Cancel a PENDING decision."""
    try:
        decision = cancel_decision(
            db=db,
            decision_id=decision_id,
            tenant_id=ctx.membership.tenant_id,
            cancelled_by=ctx.membership.user_id,
            reason_text=body.reason_text,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "data": {
            "decision_id": decision.id,
            "status": decision.status.value,
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }
