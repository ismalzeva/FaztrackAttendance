"""Lumin Dashboard — simple attendance overview for Property Marketing.

Independent from the mining dashboard model (no shift/crew/equipment).
Reads directly from attendance_events + workers + projects.
"""
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, tenant_context
from app.models import (
    AttendanceEvent, AttendanceStatus, AttendanceType,
    Assignment, Project, Worker, WorkSchedule,
)

router = APIRouter(prefix="/api/v1/lumin", tags=["lumin-dashboard"])


def _wib(dt: datetime | None) -> str | None:
    """Convert UTC datetime to WIB string HH:MM."""
    if not dt:
        return None
    wib = dt + timezone.utc.utcoffset(None) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    # Just return ISO, frontend will format
    return dt.isoformat()


@router.get("/dashboard")
def lumin_dashboard(
    request: Request,
    operating_date: date = Query(..., description="Date YYYY-MM-DD"),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Lumin-specific dashboard: who's checked in today, at which project."""
    tid = ctx.membership.tenant_id

    # 1. All active workers for this tenant
    workers = db.scalars(
        select(Worker).where(Worker.tenant_id == tid, Worker.is_active.is_(True))
        .order_by(Worker.code)
    ).all()
    total_workers = len(workers)

    # 2. All active projects
    projects = db.scalars(
        select(Project).where(Project.tenant_id == tid, Project.is_active.is_(True))
        .order_by(Project.code)
    ).all()
    project_map = {p.id: p for p in projects}

    # 3. Attendance events for this date
    events = db.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.tenant_id == tid,
            AttendanceEvent.work_date == operating_date,
            AttendanceEvent.status.in_([AttendanceStatus.VALID, AttendanceStatus.REVIEW]),
        ).order_by(AttendanceEvent.server_time)
    ).all()

    # 4. Build per-worker attendance record
    worker_map = {w.id: w for w in workers}
    attendance_records: dict[str, dict] = {}  # worker_id -> record

    for ev in events:
        wid = ev.worker_id
        if wid not in attendance_records:
            w = worker_map.get(wid)
            p = project_map.get(ev.project_id)
            attendance_records[wid] = {
                "worker_code": w.code if w else "?",
                "worker_name": w.name if w else "?",
                "project_code": p.code if p else "?",
                "project_name": p.name if p else "?",
                "check_in": None,
                "check_out": None,
                "status": ev.status.value if hasattr(ev.status, 'value') else str(ev.status),
                "has_signature": bool(ev.signature),
                "latitude": ev.latitude,
                "longitude": ev.longitude,
            }
        rec = attendance_records[wid]
        etype = ev.event_type if isinstance(ev.event_type, str) else ev.event_type.value
        if etype == "CHECK_IN":
            rec["check_in"] = ev.server_time.isoformat()
            rec["status"] = ev.status.value if hasattr(ev.status, 'value') else str(ev.status)
            rec["has_signature"] = bool(ev.signature)
            rec["latitude"] = ev.latitude
            rec["longitude"] = ev.longitude
            pid = ev.project_id
            p = project_map.get(pid)
            if p:
                rec["project_code"] = p.code
                rec["project_name"] = p.name
        elif etype == "CHECK_OUT":
            rec["check_out"] = ev.server_time.isoformat()

    # 5. Build per-project summary
    project_summary: dict[str, dict] = {}
    for p in projects:
        project_summary[p.id] = {
            "code": p.code,
            "name": p.name,
            "checked_in": 0,
            "total": 0,
        }

    # Count workers per project (from assignments or attendance)
    for rec in attendance_records.values():
        pid = None
        for p in projects:
            if p.code == rec["project_code"]:
                pid = p.id
                break
        if pid and pid in project_summary:
            project_summary[pid]["checked_in"] += 1

    # Count total assigned workers per project
    assignments = db.scalars(
        select(Assignment).where(
            Assignment.tenant_id == tid,
            Assignment.work_date == operating_date,
        )
    ).all()
    for a in assignments:
        if a.project_id in project_summary:
            project_summary[a.project_id]["total"] += 1

    # 6. Workers who haven't checked in
    checked_in_ids = set(attendance_records.keys())
    not_checked_in = [
        {"worker_code": w.code, "worker_name": w.name}
        for w in workers
        if w.id not in checked_in_ids
    ]

    # 7. Work schedule info
    schedule = db.scalar(
        select(WorkSchedule).where(
            WorkSchedule.tenant_id == tid,
            WorkSchedule.work_date == operating_date,
        )
    )
    is_workday = True  # default
    if schedule:
        is_workday = not schedule.is_holiday if hasattr(schedule, 'is_holiday') else True

    return {
        "data": {
            "date": str(operating_date),
            "is_workday": is_workday,
            "summary": {
                "total_workers": total_workers,
                "checked_in": len(attendance_records),
                "not_checked_in": total_workers - len(attendance_records),
                "projects_active": len([p for p in project_summary.values() if p["checked_in"] > 0]),
                "total_projects": len(projects),
            },
            "attendance": sorted(attendance_records.values(), key=lambda r: r["worker_code"]),
            "not_checked_in": not_checked_in,
            "projects": list(project_summary.values()),
        },
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }
