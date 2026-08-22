"""
dashboard.py — MM-M4A Dashboard API endpoints.

Provides the Field Supervisor operational dashboard snapshot.
All queries are tenant-scoped via authenticated context.
"""

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, tenant_context
from app.dashboard_service import get_dashboard_snapshot, snapshot_to_dict

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/snapshot")
def dashboard_snapshot(
    request: Request,
    operating_date: date = Query(..., description="Operating date (YYYY-MM-DD)"),
    shift_id: str | None = Query(None, description="Shift template ID (DAY/NIGHT)"),
    site_id: str | None = Query(None, description="Site ID"),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Get Field Supervisor dashboard snapshot.

    Returns aggregated operational view: shift summary, roster, checkpoints,
    equipment status, active exceptions, action required, pending decisions,
    and configuration warnings.
    """
    try:
        snapshot = get_dashboard_snapshot(
            db=db,
            tenant_id=ctx.membership.tenant_id,
            operating_date=operating_date,
            shift_id=shift_id,
            site_id=site_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))

    data = snapshot_to_dict(snapshot)
    return {
        "data": data,
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": snapshot.context.generated_at.isoformat(),
            "version": "v1",
        },
    }
