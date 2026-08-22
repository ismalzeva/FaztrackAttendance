"""
reports.py — M4D Reports & Export API Router.

Provides three report families:
  A. Shift Attendance Report
  B. Exception & Decision Report
  C. Roster vs Actual Equipment Report

Each supports JSON response + CSV/XLSX export.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import RequestContext, tenant_context
from app.report_service import (
    ReportFilter,
    get_shift_attendance_report,
    get_exception_report,
    get_roster_vs_actual_report,
    shift_attendance_to_dicts,
    exception_report_to_dicts,
    roster_vs_actual_to_dicts,
    export_csv,
    export_xlsx,
    generate_filename,
    build_report_metadata,
    SHIFT_ATTENDANCE_COLUMNS,
    EXCEPTION_REPORT_COLUMNS,
    ROSTER_VS_ACTUAL_COLUMNS,
)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _build_filter(
    ctx: RequestContext,
    operating_date: Optional[date] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    site_id: Optional[str] = None,
    shift_id: Optional[str] = None,
    crew_id: Optional[str] = None,
    role_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    equipment_id: Optional[str] = None,
    work_status: Optional[str] = None,
    exception_type: Optional[str] = None,
    severity: Optional[str] = None,
    exception_status: Optional[str] = None,
    decision_status: Optional[str] = None,
) -> ReportFilter:
    return ReportFilter(
        tenant_id=ctx.membership.tenant_id,
        operating_date=operating_date,
        date_from=date_from,
        date_to=date_to,
        site_id=site_id,
        shift_id=shift_id,
        crew_id=crew_id,
        role_id=role_id,
        employee_id=employee_id,
        equipment_id=equipment_id,
        work_status=work_status,
        exception_type=exception_type,
        severity=severity,
        exception_status=exception_status,
        decision_status=decision_status,
    )


# ── Report A: Shift Attendance ────────────────────────────────

@router.get("/shift-attendance")
def shift_attendance_json(
    operating_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    site_id: Optional[str] = Query(None),
    shift_id: Optional[str] = Query(None),
    crew_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    work_status: Optional[str] = Query(None),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Shift Attendance report as JSON."""
    f = _build_filter(ctx, operating_date, date_from, date_to, site_id,
                      shift_id, crew_id, role_id, employee_id,
                      work_status=work_status)
    rows = get_shift_attendance_report(db, f)
    return {
        "report_type": "shift_attendance",
        "row_count": len(rows),
        "rows": shift_attendance_to_dicts(rows),
    }


@router.get("/shift-attendance/export")
def shift_attendance_export(
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    operating_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    site_id: Optional[str] = Query(None),
    shift_id: Optional[str] = Query(None),
    crew_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    work_status: Optional[str] = Query(None),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Shift Attendance export as CSV or XLSX."""
    f = _build_filter(ctx, operating_date, date_from, date_to, site_id,
                      shift_id, crew_id, role_id, employee_id,
                      work_status=work_status)
    rows = get_shift_attendance_report(db, f)
    dicts = shift_attendance_to_dicts(rows)
    metadata = build_report_metadata(db, ctx, "shift_attendance", f, len(rows))
    filename = generate_filename("shift_attendance", ctx.membership.tenant_id,
                                 operating_date, date_from, date_to,
                                 shift_id, format)

    if format == "csv":
        content = export_csv(dicts, SHIFT_ATTENDANCE_COLUMNS)
        return StreamingResponse(
            iter([content]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        xlsx_bytes = export_xlsx(dicts, SHIFT_ATTENDANCE_COLUMNS, "Attendance",
                                 metadata, filename)
        return StreamingResponse(
            iter([xlsx_bytes]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


# ── Report B: Exception & Decision ────────────────────────────

@router.get("/exceptions")
def exception_report_json(
    operating_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    site_id: Optional[str] = Query(None),
    shift_id: Optional[str] = Query(None),
    crew_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    exception_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    exception_status: Optional[str] = Query(None),
    decision_status: Optional[str] = Query(None),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Exception & Decision report as JSON."""
    f = _build_filter(ctx, operating_date, date_from, date_to, site_id,
                      shift_id, crew_id, role_id, employee_id, equipment_id,
                      exception_type=exception_type, severity=severity,
                      exception_status=exception_status,
                      decision_status=decision_status)
    rows = get_exception_report(db, f)
    return {
        "report_type": "exceptions",
        "row_count": len(rows),
        "rows": exception_report_to_dicts(rows),
    }


@router.get("/exceptions/export")
def exception_report_export(
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    operating_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    site_id: Optional[str] = Query(None),
    shift_id: Optional[str] = Query(None),
    crew_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    exception_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    exception_status: Optional[str] = Query(None),
    decision_status: Optional[str] = Query(None),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Exception & Decision export as CSV or XLSX."""
    f = _build_filter(ctx, operating_date, date_from, date_to, site_id,
                      shift_id, crew_id, role_id, employee_id, equipment_id,
                      exception_type=exception_type, severity=severity,
                      exception_status=exception_status,
                      decision_status=decision_status)
    rows = get_exception_report(db, f)
    dicts = exception_report_to_dicts(rows)
    metadata = build_report_metadata(db, ctx, "exceptions", f, len(rows))
    filename = generate_filename("exceptions", ctx.membership.tenant_id,
                                 operating_date, date_from, date_to,
                                 shift_id, format)

    if format == "csv":
        content = export_csv(dicts, EXCEPTION_REPORT_COLUMNS)
        return StreamingResponse(
            iter([content]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        xlsx_bytes = export_xlsx(dicts, EXCEPTION_REPORT_COLUMNS, "Exceptions",
                                 metadata, filename)
        return StreamingResponse(
            iter([xlsx_bytes]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


# ── Report C: Roster vs Actual Equipment ──────────────────────

@router.get("/roster-vs-actual")
def roster_vs_actual_json(
    operating_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    site_id: Optional[str] = Query(None),
    shift_id: Optional[str] = Query(None),
    crew_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Roster vs Actual Equipment report as JSON."""
    f = _build_filter(ctx, operating_date, date_from, date_to, site_id,
                      shift_id, crew_id, role_id, employee_id, equipment_id)
    rows = get_roster_vs_actual_report(db, f)
    return {
        "report_type": "roster_vs_actual",
        "row_count": len(rows),
        "rows": roster_vs_actual_to_dicts(rows),
    }


@router.get("/roster-vs-actual/export")
def roster_vs_actual_export(
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    operating_date: Optional[date] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    site_id: Optional[str] = Query(None),
    shift_id: Optional[str] = Query(None),
    crew_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    ctx: RequestContext = Depends(tenant_context),
    db: Session = Depends(get_db),
):
    """Roster vs Actual Equipment export as CSV or XLSX."""
    f = _build_filter(ctx, operating_date, date_from, date_to, site_id,
                      shift_id, crew_id, role_id, employee_id, equipment_id)
    rows = get_roster_vs_actual_report(db, f)
    dicts = roster_vs_actual_to_dicts(rows)
    metadata = build_report_metadata(db, ctx, "roster_vs_actual", f, len(rows))
    filename = generate_filename("roster_vs_actual", ctx.membership.tenant_id,
                                 operating_date, date_from, date_to,
                                 shift_id, format)

    if format == "csv":
        content = export_csv(dicts, ROSTER_VS_ACTUAL_COLUMNS)
        return StreamingResponse(
            iter([content]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        xlsx_bytes = export_xlsx(dicts, ROSTER_VS_ACTUAL_COLUMNS, "Roster_vs_Actual",
                                 metadata, filename)
        return StreamingResponse(
            iter([xlsx_bytes]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
