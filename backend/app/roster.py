"""
roster.py — MM-M4B Roster & Attendance Operational View API.

Provides detailed roster board, worker detail, and attendance timeline
for Field Supervisor operational inspection of M4A data.
"""

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, tenant_context
from app.roster_service import (
    get_roster_board,
    get_worker_detail,
    get_worker_timeline,
)

router = APIRouter(prefix="/api/v1/roster", tags=["roster"])


@router.get("/operational")
def roster_board(
    request: Request,
    operating_date: date = Query(..., description="Operating date (YYYY-MM-DD)"),
    shift_id: str | None = Query(None, description="Shift template ID (DAY/NIGHT)"),
    site_id: str | None = Query(None, description="Site ID filter"),
    crew_id: str | None = Query(None, description="Crew ID filter"),
    role_id: str | None = Query(None, description="Role ID filter"),
    work_status: str | None = Query(None, description="Work status filter"),
    operational_state: str | None = Query(None, description="Operational state filter"),
    has_exception: bool | None = Query(None, description="Filter by exception presence"),
    equipment_search: str | None = Query(None, description="Equipment code search"),
    employee_search: str | None = Query(None, description="Employee name/code search"),
    sort_by: str = Query("employee_name", description="Sort field"),
    sort_dir: str = Query("asc", description="Sort direction"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=200, description="Pagination limit"),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Roster board: tabular view of all workers for a shift."""
    try:
        result = get_roster_board(
            db=db,
            tenant_id=ctx.membership.tenant_id,
            operating_date=operating_date,
            shift_id=shift_id,
            site_id=site_id,
            crew_id=crew_id,
            role_id=role_id,
            work_status=work_status,
            operational_state_filter=operational_state,
            has_exception=has_exception,
            equipment_search=equipment_search,
            employee_search=employee_search,
            sort_by=sort_by,
            sort_dir=sort_dir,
            offset=offset,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "data": _serialize_roster_board(result),
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": result.context.generated_at.isoformat(),
            "version": "v1",
        },
    }


@router.get("/operational/{worker_id}")
def worker_detail(
    request: Request,
    worker_id: str,
    operating_date: date = Query(..., description="Operating date (YYYY-MM-DD)"),
    shift_id: str | None = Query(None, description="Shift template ID"),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Worker detail: identity, roster, plan vs actual, timeline, exceptions, decisions."""
    result = get_worker_detail(
        db=db,
        tenant_id=ctx.membership.tenant_id,
        worker_id=worker_id,
        operating_date=operating_date,
        shift_id=shift_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Worker not found or no roster for this date")

    from datetime import datetime, timezone
    return {
        "data": _serialize_worker_detail(result),
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


@router.get("/operational/{worker_id}/timeline")
def worker_timeline(
    request: Request,
    worker_id: str,
    operating_date: date = Query(..., description="Operating date (YYYY-MM-DD)"),
    shift_id: str | None = Query(None, description="Shift template ID"),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Worker attendance timeline: chronological event list."""
    result = get_worker_timeline(
        db=db,
        tenant_id=ctx.membership.tenant_id,
        worker_id=worker_id,
        operating_date=operating_date,
        shift_id=shift_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Worker not found or no roster for this date")

    from datetime import datetime, timezone
    return {
        "data": [_serialize_timeline_entry(e) for e in result],
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ── Serialization helpers ────────────────────────────────────

def _serialize_roster_board(result):
    return {
        "context": {
            "tenant_id": result.context.tenant_id,
            "tenant_name": result.context.tenant_name,
            "site_id": result.context.site_id,
            "site_name": result.context.site_name,
            "operating_date": str(result.context.operating_date),
            "shift_id": result.context.shift_id,
            "shift_name": result.context.shift_name,
            "timezone": result.context.timezone_str,
            "generated_at": result.context.generated_at.isoformat(),
            "total_count": result.context.total_count,
            "filtered_count": result.context.filtered_count,
        },
        "items": [_serialize_roster_item(i) for i in result.items],
    }


def _serialize_roster_item(item):
    return {
        "employee_id": item.employee_id,
        "employee_name": item.employee_name,
        "employee_code": item.employee_code,
        "role_name": item.role_name,
        "crew_name": item.crew_name,
        "work_status": item.work_status,
        "shift_id": item.shift_id,
        "shift_name": item.shift_name,
        "site_status": item.site_status,
        "planned_equipment_code": item.planned_equipment_code,
        "actual_equipment_code": item.actual_equipment_code,
        "operational_state": item.operational_state,
        "checkpoint_status_summary": item.checkpoint_status_summary,
        "active_exception_count": item.active_exception_count,
        "has_pending_decision": item.has_pending_decision,
        "decision_status": item.decision_status,
        "attention_badge": item.attention_badge,
    }


def _serialize_worker_detail(d):
    return {
        "identity": {
            "employee_id": d.identity.employee_id,
            "employee_name": d.identity.employee_name,
            "employee_code": d.identity.employee_code,
            "employee_no": d.identity.employee_no,
            "role_name": d.identity.role_name,
            "role_code": d.identity.role_code,
            "crew_name": d.identity.crew_name,
            "crew_code": d.identity.crew_code,
            "is_active": d.identity.is_active,
        },
        "roster": {
            "operating_date": str(d.roster.operating_date),
            "shift_id": d.roster.shift_id,
            "shift_name": d.roster.shift_name,
            "work_status": d.roster.work_status,
            "site_status": d.roster.site_status,
            "planned_equipment_code": d.roster.planned_equipment_code,
            "planned_equipment_id": d.roster.planned_equipment_id,
            "rule_version": d.roster.rule_version,
        },
        "operational_state": d.operational_state,
        "equipment_history": {
            "planned_equipment_id": d.equipment_history.planned_equipment_id,
            "planned_equipment_code": d.equipment_history.planned_equipment_code,
            "planned_equipment_type": d.equipment_history.planned_equipment_type,
            "actual_intervals": [
                {
                    "equipment_id": i.equipment_id,
                    "equipment_code": i.equipment_code,
                    "equipment_type": i.equipment_type,
                    "started_at": i.started_at.isoformat(),
                    "ended_at": i.ended_at.isoformat() if i.ended_at else None,
                    "is_current": i.is_current,
                    "source": i.source,
                }
                for i in d.equipment_history.actual_intervals
            ],
            "comparison_results": d.equipment_history.comparison_results,
            "has_mismatch": d.equipment_history.has_mismatch,
        },
        "timeline": [_serialize_timeline_entry(e) for e in d.timeline],
        "checkpoint_details": [
            {
                "checkpoint_type": c.checkpoint_type,
                "timestamp": c.timestamp.isoformat() if c.timestamp else None,
                "validation_status": c.validation_status,
                "rule_version": c.rule_version,
                "source": c.source,
                "evidence_available": c.evidence_available,
                "site_name": c.site_name,
                "equipment_code": c.equipment_code,
                "reason_code": c.reason_code,
                "is_missing": c.is_missing,
                "expected_window_start": c.expected_window_start.isoformat() if c.expected_window_start else None,
                "expected_window_end": c.expected_window_end.isoformat() if c.expected_window_end else None,
            }
            for c in d.checkpoint_details
        ],
        "exceptions": [
            {
                "exception_id": e.exception_id,
                "exception_type": e.exception_type,
                "severity": e.severity,
                "status": e.status,
                "detected_at": e.detected_at.isoformat(),
                "current_owner_id": e.current_owner_id,
                "equipment_code": e.equipment_code,
            }
            for e in d.exceptions
        ],
        "decisions": [
            {
                "decision_id": dc.decision_id,
                "decision_type": dc.decision_type,
                "status": dc.status,
                "planned_display": dc.planned_display,
                "actual_display": dc.actual_display,
                "decided_by": dc.decided_by,
                "decided_at": dc.decided_at.isoformat() if dc.decided_at else None,
                "reason_text": dc.reason_text,
                "authorization_status": dc.authorization_status,
            }
            for dc in d.decisions
        ],
        "competencies": [
            {
                "equipment_type": c.equipment_type,
                "status": c.status,
                "valid_from": str(c.valid_from) if c.valid_from else None,
                "valid_to": str(c.valid_to) if c.valid_to else None,
            }
            for c in d.competencies
        ],
    }


def _serialize_timeline_entry(e):
    return {
        "timestamp": e.timestamp.isoformat(),
        "event_type": e.event_type,
        "display_label": e.display_label,
        "validation_status": e.validation_status,
        "source": e.source,
        "equipment_code": e.equipment_code,
        "site_name": e.site_name,
        "reason_code": e.reason_code,
        "evidence_available": e.evidence_available,
    }
