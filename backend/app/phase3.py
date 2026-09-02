"""Phase 3 — Koreksi Absensi, Lembur, Laporan Bulanan.

Features:
- Koreksi absensi: admin edit jam masuk/keluar, data asli tetap tersimpan di audit trail
- Lembur: auto-detect check-out > jam kerja, approval workflow
- Laporan bulanan: rekap kehadiran per karyawan per bulan
"""
from datetime import date, datetime, timezone, time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, or_, extract
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.dependencies import RequestContext, admin_context, approval_context
from app.models import (
    Worker, AttendanceEvent, AttendanceType, AttendanceStatus,
    Project, uid, now,
)
from app.schedule_management import ScheduleTemplate, EmployeeSchedule, HolidayCalendar, ScheduleOverride
from app.leave_management import LeaveRequest, LeaveStatus

from sqlalchemy import Column, String, Boolean, Date, DateTime, Float, ForeignKey, Text, Integer


# ──────────────────────────────────────────────
# MODELS
# ──────────────────────────────────────────────

class AttendanceCorrection(Base):
    """Audit trail untuk koreksi absensi. Data asli tetap di attendance_events."""
    __tablename__ = "attendance_corrections"
    __table_args__ = ({"extend_existing": True},)
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)
    event_id = Column(String(36), ForeignKey("attendance_events.id"), index=True, nullable=False)
    worker_id = Column(String(36), ForeignKey("workers.id"), index=True, nullable=False)
    # Data sebelum koreksi
    original_time = Column(DateTime(timezone=True), nullable=False)
    original_status = Column(String(20), nullable=False)
    # Data sesudah koreksi
    corrected_time = Column(DateTime(timezone=True), nullable=False)
    corrected_status = Column(String(20), nullable=True)
    # Metadata
    reason = Column(Text, nullable=False)
    corrected_by = Column(String(36), nullable=False)  # user id
    corrected_at = Column(DateTime(timezone=True), default=now)


class OvertimeRequest(Base):
    """Pengajuan lembur."""
    __tablename__ = "overtime_requests"
    __table_args__ = ({"extend_existing": True},)
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)
    worker_id = Column(String(36), ForeignKey("workers.id"), index=True, nullable=False)
    work_date = Column(Date, nullable=False, index=True)
    hours = Column(Float, nullable=False)  # jam lembur
    reason = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING/APPROVED/REJECTED
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now)


router = APIRouter(prefix="/api/v1/phase3", tags=["phase3"])


def _envelope(data, request: Request):
    return {
        "data": data,
        "meta": {
            "correlation_id": request.state.correlation_id,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
        },
    }


# ──────────────────────────────────────────────
# KOREKSI ABSENSI
# ──────────────────────────────────────────────

class CorrectionCreate(BaseModel):
    event_id: str
    corrected_time: datetime
    reason: str = Field(min_length=1, max_length=500)


@router.get("/corrections")
def list_corrections(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    worker_id: str | None = Query(None),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """List semua koreksi absensi."""
    tid = ctx.membership.tenant_id
    q = select(AttendanceCorrection).where(AttendanceCorrection.tenant_id == tid)
    if date_from:
        q = q.where(AttendanceCorrection.corrected_at >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.where(AttendanceCorrection.corrected_at <= datetime.combine(date_to, time.max))
    if worker_id:
        q = q.where(AttendanceCorrection.worker_id == worker_id)
    q = q.order_by(AttendanceCorrection.corrected_at.desc())
    rows = db.scalars(q).all()
    result = []
    for r in rows:
        w = db.get(Worker, r.worker_id)
        result.append({
            "id": r.id,
            "event_id": r.event_id,
            "worker_code": w.code if w else "?",
            "worker_name": w.name if w else "?",
            "original_time": r.original_time.isoformat(),
            "original_status": r.original_status,
            "corrected_time": r.corrected_time.isoformat(),
            "corrected_status": r.corrected_status,
            "reason": r.reason,
            "corrected_by": r.corrected_by,
            "corrected_at": r.corrected_at.isoformat() if r.corrected_at else None,
        })
    return _envelope(result, request)


@router.post("/corrections", status_code=201)
def create_correction(
    body: CorrectionCreate,
    request: Request,
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Koreksi jam absensi. Data asli tetap tersimpan di audit trail."""
    tid = ctx.membership.tenant_id
    ev = db.scalar(
        select(AttendanceEvent).where(
            AttendanceEvent.id == body.event_id,
            AttendanceEvent.tenant_id == tid,
        )
    )
    if not ev:
        raise HTTPException(404, detail={"code": "EVENT_NOT_FOUND"})
    
    # Simpan data asli ke audit trail
    correction = AttendanceCorrection(
        tenant_id=tid,
        event_id=ev.id,
        worker_id=ev.worker_id,
        original_time=ev.server_time,
        original_status=ev.status.value if hasattr(ev.status, 'value') else str(ev.status),
        corrected_time=body.corrected_time,
        corrected_status=ev.status.value if hasattr(ev.status, 'value') else str(ev.status),
        reason=body.reason,
        corrected_by=ctx.user.id,
    )
    db.add(correction)
    
    # Update waktu di attendance_events
    ev.server_time = body.corrected_time
    db.commit()
    
    return _envelope({
        "correction_id": correction.id,
        "event_id": ev.id,
        "original_time": correction.original_time.isoformat(),
        "corrected_time": correction.corrected_time.isoformat(),
    }, request)


# ──────────────────────────────────────────────
# LEMBUR
# ──────────────────────────────────────────────

class OvertimeCreate(BaseModel):
    worker_id: str
    work_date: date
    hours: float = Field(gt=0, le=24)
    reason: str | None = None


class OvertimeDecision(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    note: str | None = None


@router.get("/overtime")
def list_overtime(
    request: Request,
    status: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """List pengajuan lembur."""
    tid = ctx.membership.tenant_id
    q = select(OvertimeRequest).where(OvertimeRequest.tenant_id == tid)
    if status:
        q = q.where(OvertimeRequest.status == status)
    if date_from:
        q = q.where(OvertimeRequest.work_date >= date_from)
    if date_to:
        q = q.where(OvertimeRequest.work_date <= date_to)
    q = q.order_by(OvertimeRequest.work_date.desc())
    rows = db.scalars(q).all()
    result = []
    for r in rows:
        w = db.get(Worker, r.worker_id)
        result.append({
            "id": r.id,
            "worker_id": r.worker_id,
            "worker_code": w.code if w else "?",
            "worker_name": w.name if w else "?",
            "work_date": str(r.work_date),
            "hours": r.hours,
            "reason": r.reason,
            "status": r.status,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_note": r.review_note,
        })
    return _envelope(result, request)


@router.post("/overtime", status_code=201)
def create_overtime(
    body: OvertimeCreate,
    request: Request,
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Buat pengajuan lembur."""
    tid = ctx.membership.tenant_id
    w = db.scalar(select(Worker).where(Worker.id == body.worker_id, Worker.tenant_id == tid))
    if not w:
        raise HTTPException(404, detail={"code": "WORKER_NOT_FOUND"})
    # Check duplicate
    existing = db.scalar(
        select(OvertimeRequest).where(
            OvertimeRequest.tenant_id == tid,
            OvertimeRequest.worker_id == body.worker_id,
            OvertimeRequest.work_date == body.work_date,
            OvertimeRequest.status.in_(["PENDING", "APPROVED"]),
        )
    )
    if existing:
        raise HTTPException(409, detail={"code": "OVERTIME_ALREADY_EXISTS"})
    ot = OvertimeRequest(
        tenant_id=tid,
        worker_id=body.worker_id,
        work_date=body.work_date,
        hours=body.hours,
        reason=body.reason,
    )
    db.add(ot)
    db.commit()
    return _envelope({"id": ot.id, "hours": ot.hours, "status": ot.status}, request)


@router.post("/overtime/{overtime_id}/decide")
def decide_overtime(
    overtime_id: str,
    body: OvertimeDecision,
    request: Request,
    ctx: RequestContext = Depends(approval_context),
    db: Session = Depends(get_db),
):
    """Approve/reject lembur."""
    tid = ctx.membership.tenant_id
    ot = db.scalar(
        select(OvertimeRequest).where(
            OvertimeRequest.id == overtime_id,
            OvertimeRequest.tenant_id == tid,
        )
    )
    if not ot:
        raise HTTPException(404, detail={"code": "OVERTIME_NOT_FOUND"})
    if ot.status != "PENDING":
        raise HTTPException(409, detail={"code": "NOT_PENDING"})
    ot.status = body.decision
    ot.reviewed_by = ctx.user.id
    ot.reviewed_at = datetime.now(timezone.utc)
    ot.review_note = body.note
    db.commit()
    return _envelope({"id": ot.id, "status": ot.status}, request)


@router.get("/overtime/detect")
def detect_overtime(
    request: Request,
    check_date: date = Query(...),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Deteksi otomatis lembur dari check-out > jam kerja."""
    tid = ctx.membership.tenant_id
    
    # Get all check-out events for the date
    events = db.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.tenant_id == tid,
            AttendanceEvent.work_date == check_date,
            AttendanceEvent.event_type == AttendanceType.CHECK_OUT,
            AttendanceEvent.status.in_([AttendanceStatus.VALID, AttendanceStatus.REVIEW]),
        )
    ).all()
    
    # Get schedules
    schedules = db.scalars(select(EmployeeSchedule).where(EmployeeSchedule.tenant_id == tid)).all()
    schedule_map = {s.worker_id: s for s in schedules}
    template_ids = set(s.template_id for s in schedules)
    templates = {}
    if template_ids:
        for t in db.scalars(select(ScheduleTemplate).where(ScheduleTemplate.id.in_(template_ids))).all():
            templates[t.id] = t
    
    result = []
    for ev in events:
        w = db.get(Worker, ev.worker_id)
        # Get work_end from template
        work_end_hour = 17  # default
        if ev.worker_id in schedule_map:
            tmpl = templates.get(schedule_map[ev.worker_id].template_id)
            if tmpl:
                try:
                    work_end_hour = int(tmpl.work_end.split(":")[0])
                except:
                    pass
        
        checkout_time = ev.server_time.astimezone(timezone.utc)
        checkout_hour = checkout_time.hour + checkout_time.minute / 60
        
        if checkout_hour > work_end_hour:
            overtime_hours = round(checkout_hour - work_end_hour, 1)
            # Check if already recorded
            existing = db.scalar(
                select(OvertimeRequest).where(
                    OvertimeRequest.tenant_id == tid,
                    OvertimeRequest.worker_id == ev.worker_id,
                    OvertimeRequest.work_date == check_date,
                )
            )
            result.append({
                "worker_id": ev.worker_id,
                "worker_code": w.code if w else "?",
                "worker_name": w.name if w else "?",
                "checkout_time": checkout_time.isoformat(),
                "work_end": f"{work_end_hour:02d}:00",
                "overtime_hours": overtime_hours,
                "already_recorded": existing is not None,
                "overtime_status": existing.status if existing else None,
            })
    
    return _envelope({"date": str(check_date), "detections": result}, request)


# ──────────────────────────────────────────────
# LAPORAN BULANAN
# ──────────────────────────────────────────────

@router.get("/monthly-report")
def monthly_report(
    request: Request,
    year: int = Query(...),
    month: int = Query(...),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Laporan bulanan lengkap per karyawan.
    
    Returns per worker:
    - Total hari kerja
    - Hadir, Terlambat, Izin, Sakit, Cuti, Dinas Luar, Tanpa Keterangan
    - Total jam kerja
    - Total jam lembur (approved)
    """
    from calendar import monthrange
    from collections import defaultdict
    
    tid = ctx.membership.tenant_id
    _, days_in_month = monthrange(year, month)
    date_from = date(year, month, 1)
    date_to = date(year, month, days_in_month)
    
    # Get all active workers
    workers = db.scalars(
        select(Worker).where(Worker.tenant_id == tid, Worker.is_active.is_(True))
        .order_by(Worker.code)
    ).all()
    
    # Get schedules
    schedules = db.scalars(select(EmployeeSchedule).where(EmployeeSchedule.tenant_id == tid)).all()
    schedule_map = {s.worker_id: s for s in schedules}
    template_ids = set(s.template_id for s in schedules)
    templates = {}
    if template_ids:
        for t in db.scalars(select(ScheduleTemplate).where(ScheduleTemplate.id.in_(template_ids))).all():
            templates[t.id] = t
    
    # Get holidays for the month
    holidays = db.scalars(
        select(HolidayCalendar).where(
            HolidayCalendar.tenant_id == tid,
            HolidayCalendar.holiday_date >= date_from,
            HolidayCalendar.holiday_date <= date_to,
            HolidayCalendar.is_active.is_(True),
        )
    ).all()
    holiday_dates = {h.holiday_date for h in holidays}
    
    # Get overrides for the month
    overrides = db.scalars(
        select(ScheduleOverride).where(
            ScheduleOverride.tenant_id == tid,
            ScheduleOverride.override_date >= date_from,
            ScheduleOverride.override_date <= date_to,
        )
    ).all()
    override_map = defaultdict(dict)
    for o in overrides:
        override_map[o.worker_id][o.override_date] = o
    
    # Get attendance events for the month
    events = db.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.tenant_id == tid,
            AttendanceEvent.work_date >= date_from,
            AttendanceEvent.work_date <= date_to,
            AttendanceEvent.status.in_([AttendanceStatus.VALID, AttendanceStatus.REVIEW]),
        )
    ).all()
    worker_events = defaultdict(list)
    for ev in events:
        worker_events[(ev.worker_id, ev.work_date)].append(ev)
    
    # Get approved leaves for the month
    leaves = db.scalars(
        select(LeaveRequest).where(
            LeaveRequest.tenant_id == tid,
            LeaveRequest.date_from <= date_to,
            LeaveRequest.date_to >= date_from,
            LeaveRequest.status == LeaveStatus.APPROVED,
        )
    ).all()
    worker_leaves = defaultdict(list)
    for l in leaves:
        # Expand multi-day leaves
        d = l.date_from
        end = l.date_to or l.date_from
        while d <= end:
            worker_leaves[(l.worker_id, d)] = l
            d = date(d.year, d.month, d.day + 1) if d.day < 31 else date(d.year, d.month + 1, 1) if d.month < 12 else date(d.year + 1, 1, 1)
    
    # Get approved overtime for the month
    overtimes = db.scalars(
        select(OvertimeRequest).where(
            OvertimeRequest.tenant_id == tid,
            OvertimeRequest.work_date >= date_from,
            OvertimeRequest.work_date <= date_to,
            OvertimeRequest.status == "APPROVED",
        )
    ).all()
    worker_overtime = defaultdict(float)
    for ot in overtimes:
        worker_overtime[(ot.worker_id, ot.work_date)] = ot.hours
    
    # Build report
    result = []
    for w in workers:
        total_workdays = 0
        hadir = 0
        terlambat = 0
        izin = 0
        sakit = 0
        cuti = 0
        dinas_luar = 0
        tanpa_keterangan = 0
        libur = 0
        total_work_hours = 0.0
        total_overtime_hours = 0.0
        
        d = date_from
        while d <= date_to:
            # Check if workday
            is_workday = True
            work_start = "08:00"
            work_end = "17:00"
            
            if w.id in override_map and d in override_map[w.id]:
                is_workday = override_map[w.id][d].is_workday
            elif d in holiday_dates:
                is_workday = False
            elif w.id in schedule_map:
                tmpl = templates.get(schedule_map[w.id].template_id)
                if tmpl:
                    day_of_week = d.weekday()
                    work_days = [int(x) for x in tmpl.work_days.split(",") if x.strip()]
                    is_workday = day_of_week in work_days
                    work_start = tmpl.work_start
                    work_end = tmpl.work_end
            
            if not is_workday:
                libur += 1
            else:
                total_workdays += 1
                evts = worker_events.get((w.id, d), [])
                leave = worker_leaves.get((w.id, d))
                
                check_in = None
                check_out = None
                for ev in evts:
                    etype = ev.event_type.value if hasattr(ev.event_type, 'value') else str(ev.event_type)
                    if etype == "CHECK_IN":
                        check_in = ev
                    elif etype == "CHECK_OUT":
                        check_out = ev
                
                if leave:
                    ltype = leave.leave_type
                    if ltype == "IZIN": izin += 1
                    elif ltype == "SAKIT": sakit += 1
                    elif ltype == "CUTI": cuti += 1
                    elif ltype == "DINAS_LUAR": dinas_luar += 1
                elif check_in:
                    ci_time = check_in.server_time.astimezone(timezone.utc).strftime("%H:%M")
                    if ci_time > work_start:
                        terlambat += 1
                    else:
                        hadir += 1
                    # Calculate work hours
                    if check_out:
                        ci_dt = check_in.server_time.astimezone(timezone.utc)
                        co_dt = check_out.server_time.astimezone(timezone.utc)
                        hours = (co_dt - ci_dt).total_seconds() / 3600
                        total_work_hours += max(0, hours)
                else:
                    tanpa_keterangan += 1
                
                # Overtime
                ot_hours = worker_overtime.get((w.id, d), 0)
                total_overtime_hours += ot_hours
            
            # Next day
            if d.day < days_in_month:
                d = date(d.year, d.month, d.day + 1)
            elif d.month < 12:
                d = date(d.year, d.month + 1, 1)
            else:
                d = date(d.year + 1, 1, 1)
        
        result.append({
            "worker_code": w.code,
            "worker_name": w.name,
            "total_workdays": total_workdays,
            "hadir": hadir,
            "terlambat": terlambat,
            "izin": izin,
            "sakit": sakit,
            "cuti": cuti,
            "dinas_luar": dinas_luar,
            "tanpa_keterangan": tanpa_keterangan,
            "libur": libur,
            "total_work_hours": round(total_work_hours, 1),
            "total_overtime_hours": round(total_overtime_hours, 1),
        })
    
    return _envelope({
        "year": year,
        "month": month,
        "total_workers": len(result),
        "rows": result,
    }, request)


@router.get("/monthly-report/export")
def export_monthly(
    request: Request,
    year: int = Query(...),
    month: int = Query(...),
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Export laporan bulanan ke CSV/Excel."""
    import io
    from fastapi.responses import StreamingResponse
    
    # Reuse monthly report logic (simplified — call the same function)
    # For now, just export the basic data
    tid = ctx.membership.tenant_id
    workers = db.scalars(
        select(Worker).where(Worker.tenant_id == tid, Worker.is_active.is_(True))
        .order_by(Worker.code)
    ).all()
    
    rows = []
    for w in workers:
        rows.append([w.code, w.name, "", "", "", "", "", "", "", "", "", "", ""])
    
    if format == "csv":
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Kode", "Nama", "Total Hari Kerja", "Hadir", "Terlambat", "Izin", "Sakit", "Cuti", "Dinas Luar", "Tanpa Keterangan", "Jam Kerja", "Jam Lembur"])
        writer.writerows(rows)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=laporan_bulanan_{year}_{month:02d}.csv"},
        )
    else:
        try:
            from openpyxl import Workbook
        except ImportError:
            raise HTTPException(500, detail={"code": "XLSX_NOT_INSTALLED"})
        wb = Workbook()
        ws = wb.active
        ws.title = f"Laporan {year}-{month:02d}"
        ws.append(["Kode", "Nama", "Total Hari Kerja", "Hadir", "Terlambat", "Izin", "Sakit", "Cuti", "Dinas Luar", "Tanpa Keterangan", "Jam Kerja", "Jam Lembur"])
        for row in rows:
            ws.append(row)
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=laporan_bulanan_{year}_{month:02d}.xlsx"},
        )
