"""Leave Management — Pengajuan izin, sakit, cuti, dinas luar.

Models:
- LeaveRequest: pengajuan izin/sakit/cuti/dinas dengan approval workflow

Status kehadiran efektif:
- HADIR: ada check-in hari itu
- TERLAMBAT: check-in > jam kerja
- IZIN/SAKIT/CUTI/DINAS_LUAR: ada leave request approved
- TANPA_KETERANGAN: tidak ada check-in dan tidak ada leave
"""
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.dependencies import RequestContext, admin_context, approval_context
from app.models import Worker, uid, now
from app.schedule_management import ScheduleTemplate, EmployeeSchedule, HolidayCalendar, ScheduleOverride

from sqlalchemy import Column, String, Boolean, Date, DateTime, ForeignKey, Text, UniqueConstraint


class LeaveStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class LeaveType:
    IZIN = "IZIN"
    SAKIT = "SAKIT"
    CUTI = "CUTI"
    DINAS_LUAR = "DINAS_LUAR"


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "worker_id", "date_from", name="uq_leave_worker_date"),
        {"extend_existing": True},
    )
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), index=True, nullable=False)
    worker_id = Column(String(36), ForeignKey("workers.id"), index=True, nullable=False)
    leave_type = Column(String(20), nullable=False)  # IZIN/SAKIT/CUTI/DINAS_LUAR
    date_from = Column(Date, nullable=False, index=True)
    date_to = Column(Date, nullable=True)  # untuk cuti multi-hari
    reason = Column(Text, nullable=True)
    attachment_url = Column(String(500), nullable=True)  # surat dokter, surat dinas, dll
    status = Column(String(20), nullable=False, default=LeaveStatus.PENDING)
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=now)


router = APIRouter(prefix="/api/v1/leave", tags=["leave-management"])


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
# PENGAJUAN (oleh karyawan atau admin)
# ──────────────────────────────────────────────

class LeaveCreate(BaseModel):
    worker_id: str
    leave_type: str = Field(pattern="^(IZIN|SAKIT|CUTI|DINAS_LUAR)$")
    date_from: date
    date_to: date | None = None
    reason: str | None = None


class LeaveDecision(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    note: str | None = None


@router.get("/requests")
def list_requests(
    request: Request,
    status: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    worker_id: str | None = Query(None),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """List pengajuan izin/sakit/cuti/dinas."""
    tid = ctx.membership.tenant_id
    q = select(LeaveRequest).where(LeaveRequest.tenant_id == tid)
    if status:
        q = q.where(LeaveRequest.status == status)
    if date_from:
        q = q.where(LeaveRequest.date_from >= date_from)
    if date_to:
        q = q.where(LeaveRequest.date_from <= date_to)
    if worker_id:
        q = q.where(LeaveRequest.worker_id == worker_id)
    q = q.order_by(LeaveRequest.created_at.desc())
    rows = db.scalars(q).all()
    result = []
    for r in rows:
        w = db.get(Worker, r.worker_id)
        result.append({
            "id": r.id,
            "worker_id": r.worker_id,
            "worker_code": w.code if w else "?",
            "worker_name": w.name if w else "?",
            "leave_type": r.leave_type,
            "date_from": str(r.date_from),
            "date_to": str(r.date_to) if r.date_to else None,
            "reason": r.reason,
            "status": r.status,
            "reviewed_by": r.reviewed_by,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_note": r.review_note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return _envelope(result, request)


@router.post("/requests", status_code=201)
def create_request(
    body: LeaveCreate,
    request: Request,
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Buat pengajuan izin/sakit/cuti/dinas (admin untuk karyawan)."""
    tid = ctx.membership.tenant_id
    # Check worker
    w = db.scalar(select(Worker).where(Worker.id == body.worker_id, Worker.tenant_id == tid))
    if not w:
        raise HTTPException(404, detail={"code": "WORKER_NOT_FOUND"})
    # Check duplicate
    existing = db.scalar(
        select(LeaveRequest).where(
            LeaveRequest.tenant_id == tid,
            LeaveRequest.worker_id == body.worker_id,
            LeaveRequest.date_from == body.date_from,
            LeaveRequest.status.in_([LeaveStatus.PENDING, LeaveStatus.APPROVED]),
        )
    )
    if existing:
        raise HTTPException(409, detail={"code": "LEAVE_ALREADY_EXISTS"})
    lr = LeaveRequest(
        tenant_id=tid,
        worker_id=body.worker_id,
        leave_type=body.leave_type,
        date_from=body.date_from,
        date_to=body.date_to or body.date_from,
        reason=body.reason,
        status=LeaveStatus.PENDING,
    )
    db.add(lr)
    db.commit()
    return _envelope({"id": lr.id, "leave_type": lr.leave_type, "status": lr.status}, request)


@router.post("/requests/{request_id}/decide")
def decide_request(
    request_id: str,
    body: LeaveDecision,
    request: Request,
    ctx: RequestContext = Depends(approval_context),
    db: Session = Depends(get_db),
):
    """Approve atau reject pengajuan izin."""
    tid = ctx.membership.tenant_id
    lr = db.scalar(
        select(LeaveRequest).where(LeaveRequest.id == request_id, LeaveRequest.tenant_id == tid)
    )
    if not lr:
        raise HTTPException(404, detail={"code": "LEAVE_NOT_FOUND"})
    if lr.status != LeaveStatus.PENDING:
        raise HTTPException(409, detail={"code": "NOT_PENDING"})
    lr.status = body.decision
    lr.reviewed_by = ctx.user.id
    lr.reviewed_at = datetime.now(timezone.utc)
    lr.review_note = body.note
    db.commit()
    return _envelope({
        "id": lr.id,
        "status": lr.status,
        "reviewed_at": lr.reviewed_at.isoformat(),
    }, request)


@router.delete("/requests/{request_id}")
def cancel_request(
    request_id: str,
    request: Request,
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Batalkan pengajuan (hanya yang masih PENDING)."""
    tid = ctx.membership.tenant_id
    lr = db.scalar(
        select(LeaveRequest).where(LeaveRequest.id == request_id, LeaveRequest.tenant_id == tid)
    )
    if not lr:
        raise HTTPException(404, detail={"code": "LEAVE_NOT_FOUND"})
    if lr.status != LeaveStatus.PENDING:
        raise HTTPException(409, detail={"code": "CANNOT_CANCEL_NON_PENDING"})
    lr.status = LeaveStatus.CANCELLED
    db.commit()
    return _envelope({"id": lr.id, "status": lr.status}, request)


# ──────────────────────────────────────────────
# STATUS KEHADIRAN EFEKTIF
# ──────────────────────────────────────────────

@router.get("/attendance-status")
def attendance_status(
    request: Request,
    check_date: date = Query(...),
    ctx: RequestContext = Depends(admin_context),
    db: Session = Depends(get_db),
):
    """Status kehadiran semua karyawan untuk tanggal tertentu.
    
    Returns per worker:
    - HADIR: ada check-in
    - TERLAMBAT: check-in > jam kerja
    - IZIN/SAKIT/CUTI/DINAS_LUAR: ada leave approved
    - LIBUR: hari libur / bukan hari kerja
    - TANPA_KETERANGAN: tidak ada check-in dan tidak ada leave
    """
    from app.models import AttendanceEvent, AttendanceType, AttendanceStatus as AttStatus
    from app.schedule_management import ScheduleTemplate, EmployeeSchedule, HolidayCalendar, ScheduleOverride
    
    tid = ctx.membership.tenant_id
    
    # Get all active workers
    workers = db.scalars(
        select(Worker).where(Worker.tenant_id == tid, Worker.is_active.is_(True))
        .order_by(Worker.code)
    ).all()
    
    # Get attendance events for the date
    events = db.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.tenant_id == tid,
            AttendanceEvent.work_date == check_date,
            AttendanceEvent.status.in_([AttStatus.VALID, AttStatus.REVIEW]),
        )
    ).all()
    
    # Group events by worker
    from collections import defaultdict
    worker_events = defaultdict(list)
    for ev in events:
        worker_events[ev.worker_id].append(ev)
    
    # Get approved leaves for the date
    leaves = db.scalars(
        select(LeaveRequest).where(
            LeaveRequest.tenant_id == tid,
            LeaveRequest.date_from <= check_date,
            LeaveRequest.date_to >= check_date,
            LeaveRequest.status == LeaveStatus.APPROVED,
        )
    ).all()
    worker_leaves = {l.worker_id: l for l in leaves}
    
    # Also check single-day leaves
    single_leaves = db.scalars(
        select(LeaveRequest).where(
            LeaveRequest.tenant_id == tid,
            LeaveRequest.date_from == check_date,
            LeaveRequest.date_to.is_(None),
            LeaveRequest.status == LeaveStatus.APPROVED,
        )
    ).all()
    for l in single_leaves:
        if l.worker_id not in worker_leaves:
            worker_leaves[l.worker_id] = l
    
    # Get holidays
    holiday = db.scalar(
        select(HolidayCalendar).where(
            HolidayCalendar.tenant_id == tid,
            HolidayCalendar.holiday_date == check_date,
            HolidayCalendar.is_active.is_(True),
        )
    )
    
    # Get schedule info per worker
    schedules = db.scalars(
        select(EmployeeSchedule).where(EmployeeSchedule.tenant_id == tid)
    ).all()
    schedule_map = {s.worker_id: s for s in schedules}
    template_ids = set(s.template_id for s in schedules)
    templates = {}
    if template_ids:
        for t in db.scalars(select(ScheduleTemplate).where(ScheduleTemplate.id.in_(template_ids))).all():
            templates[t.id] = t
    
    # Get overrides
    overrides = db.scalars(
        select(ScheduleOverride).where(
            ScheduleOverride.tenant_id == tid,
            ScheduleOverride.override_date == check_date,
        )
    ).all()
    override_map = {o.worker_id: o for o in overrides}
    
    # Build result
    result = []
    for w in workers:
        # Check if workday
        is_workday = True
        work_start = "08:00"
        
        if w.id in override_map:
            is_workday = override_map[w.id].is_workday
        elif holiday:
            is_workday = False
        elif w.id in schedule_map:
            tmpl = templates.get(schedule_map[w.id].template_id)
            if tmpl:
                day_of_week = check_date.weekday()
                work_days = [int(d) for d in tmpl.work_days.split(",") if d.strip()]
                is_workday = day_of_week in work_days
                work_start = tmpl.work_start
        else:
            is_workday = check_date.weekday() < 6
        
        # Determine status
        evts = worker_events.get(w.id, [])
        leave = worker_leaves.get(w.id)
        
        check_in_ev = None
        check_out_ev = None
        for ev in evts:
            etype = ev.event_type.value if hasattr(ev.event_type, 'value') else str(ev.event_type)
            if etype == "CHECK_IN":
                check_in_ev = ev
            elif etype == "CHECK_OUT":
                check_out_ev = ev
        
        if not is_workday:
            status = "LIBUR"
            status_detail = "Hari libur"
        elif leave:
            status = leave.leave_type
            status_detail = leave.reason or leave.leave_type
        elif check_in_ev:
            # Check if late
            check_in_time = check_in_ev.server_time.astimezone(timezone.utc).strftime("%H:%M")
            if check_in_time > work_start:
                status = "TERLAMBAT"
                status_detail = f"Terlambat ({check_in_time} > {work_start})"
            else:
                status = "HADIR"
                status_detail = f"Hadir ({check_in_time})"
        else:
            status = "TANPA_KETERANGAN"
            status_detail = "Tidak ada absen dan tidak ada pengajuan"
        
        result.append({
            "worker_id": w.id,
            "worker_code": w.code,
            "worker_name": w.name,
            "status": status,
            "status_detail": status_detail,
            "check_in": check_in_ev.server_time.isoformat() if check_in_ev else None,
            "check_out": check_out_ev.server_time.isoformat() if check_out_ev else None,
            "is_workday": is_workday,
        })
    
    return _envelope({
        "date": str(check_date),
        "holiday": {"name": holiday.name, "type": holiday.holiday_type} if holiday else None,
        "total_workers": len(result),
        "summary": {
            "hadir": len([r for r in result if r["status"] == "HADIR"]),
            "terlambat": len([r for r in result if r["status"] == "TERLAMBAT"]),
            "izin": len([r for r in result if r["status"] == "IZIN"]),
            "sakit": len([r for r in result if r["status"] == "SAKIT"]),
            "cuti": len([r for r in result if r["status"] == "CUTI"]),
            "dinas_luar": len([r for r in result if r["status"] == "DINAS_LUAR"]),
            "tanpa_keterangan": len([r for r in result if r["status"] == "TANPA_KETERANGAN"]),
            "libur": len([r for r in result if r["status"] == "LIBUR"]),
        },
        "rows": result,
    }, request)
