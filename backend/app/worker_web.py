"""Worker self-service endpoints untuk halaman web absen karyawan.

Alur anti-titip-absen (2 faktor):
1. Login PIN         -> faktor pengetahuan (PIN unik per karyawan).
2. Device binding    -> faktor kepemilikan: HP karyawan mendaftarkan kunci
   publik ECDSA P-256 (via /api/v1/worker/device-enrollment/*) dan disetujui
   supervisor. Setelah aktif, tiap challenge + absen WAJIB ditandatangani
   kunci privat yang hanya ada di perangkat tersebut. HP lain (HP teman)
   tidak bisa absen karena tidak punya kunci privatnya.

Mekanisme kripto-nya identik dengan alur PWA penuh (`app/attendance.py`),
hanya kontrak endpoint-nya yang ringan untuk web.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.attendance import attendance_status, envelope, signed_payload, work_date_for
from app.database import get_db
from app.dependencies import WorkerContext, worker_context
from app.device_crypto import b64url_decode, b64url_encode, verify_signature
from app.models import (Assignment, AttendanceChallenge, AttendanceEvent,
                        AttendanceStatus, AttendanceType, DeviceBinding,
                        DeviceEnrollment, DeviceStatus, EnrollmentStatus,
                        Project, Tenant, WorkSchedule, Worker)
from app.schemas import AttendanceChallengeRequest

router=APIRouter(prefix="/api/v1/worker-web",tags=["worker-web"])


class WebLoginRequest(BaseModel):
    tenant_code: str = Field(min_length=1,max_length=50)
    worker_code: str = Field(min_length=1,max_length=50)
    pin: str = Field(min_length=4,max_length=32)


class WebSubmitRequest(BaseModel):
    challenge_id: str
    challenge: str
    event_type: str
    project_id: str
    latitude: float
    longitude: float
    accuracy_m: float
    captured_at_client: str
    signature: str
    site_note: str | None = None


@router.post("/login")
def web_login(body: WebLoginRequest,request: Request,db: Session=Depends(get_db)):
    tenant=db.scalar(select(Tenant).where(Tenant.code==body.tenant_code))
    if not tenant: raise HTTPException(404,detail={"code":"TENANT_NOT_FOUND"})
    worker=db.scalar(select(Worker).where(Worker.tenant_id==tenant.id,Worker.code==body.worker_code.upper(),Worker.is_active.is_(True)))
    from app.security import create_worker_token, verify_password
    if not worker or not worker.pin_hash or not verify_password(body.pin,worker.pin_hash):
        raise HTTPException(401,detail={"code":"INVALID_CREDENTIALS"})
    return envelope({"access_token":create_worker_token(worker.id,tenant.id),"worker":{"code":worker.code,"name":worker.name}},request)


@router.get("/device")
def device_status(request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    """Status device binding karyawan. Frontend pakai ini untuk menentukan:
    - enrolled=false + enrollment_status=null  -> minta daftarkan HP ini
    - enrolled=false + enrollment_status=PENDING -> tunggu persetujuan supervisor
    - enrolled=true                            -> HP sah, bisa absen
    """
    binding=db.scalar(select(DeviceBinding).where(DeviceBinding.tenant_id==ctx.tenant_id,DeviceBinding.worker_id==ctx.worker.id,DeviceBinding.status==DeviceStatus.ACTIVE))
    pending=db.scalar(select(DeviceEnrollment).where(DeviceEnrollment.tenant_id==ctx.tenant_id,DeviceEnrollment.worker_id==ctx.worker.id,DeviceEnrollment.status==EnrollmentStatus.PENDING).order_by(DeviceEnrollment.requested_at.desc()))
    return envelope({"enrolled":binding is not None,
        "device_label":binding.device_label if binding else None,
        "thumbprint":binding.public_key_thumbprint if binding else None,
        "enrollment_status":pending.status.value if pending else None,
        "enrollment_id":pending.id if pending else None},request)


@router.get("/shift")
def today_shift(request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    tenant=db.get(Tenant,ctx.tenant_id); work_date=work_date_for(tenant)
    schedule=db.scalar(select(WorkSchedule).where(WorkSchedule.tenant_id==ctx.tenant_id,WorkSchedule.worker_id==ctx.worker.id,WorkSchedule.work_date==work_date))
    assignments=db.scalars(select(Assignment).where(Assignment.tenant_id==ctx.tenant_id,Assignment.worker_id==ctx.worker.id,Assignment.work_date==work_date)).all()
    counted=[AttendanceStatus.VALID,AttendanceStatus.REVIEW]
    events=db.scalars(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.tenant_id,AttendanceEvent.worker_id==ctx.worker.id,AttendanceEvent.work_date==work_date,AttendanceEvent.status.in_(counted)).order_by(AttendanceEvent.server_time)).all()
    projects={p.id:p for p in db.scalars(select(Project).where(Project.tenant_id==ctx.tenant_id)).all()}
    open_shift=None; timeline=[]
    for e in events:
        timeline.append({"event_type":e.event_type.value,"project_name":projects[e.project_id].name,"server_time":e.server_time.isoformat(),"status":e.status.value,"reason_code":e.reason_code,"distance_m":round(e.distance_m,1),"auto":e.signature.startswith("AUTO")})
        if e.event_type==AttendanceType.CHECK_IN and e.status!=AttendanceStatus.REJECTED: open_shift=e
        if e.event_type==AttendanceType.CHECK_OUT and e.status==AttendanceStatus.VALID: open_shift=None
    # project list utk pemilihan lokasi (multi-site tenant: semua project tenant)
    project_list=[{"id":p.id,"code":p.code,"name":p.name,"latitude":p.latitude,"longitude":p.longitude,"radius_m":p.geofence_radius_m} for p in db.scalars(select(Project).where(Project.tenant_id==ctx.tenant_id,Project.is_active.is_(True))).all()] if hasattr(Project,"is_active") else [{"id":p.id,"code":p.code,"name":p.name,"latitude":p.latitude,"longitude":p.longitude,"radius_m":p.geofence_radius_m} for p in db.scalars(select(Project).where(Project.tenant_id==ctx.tenant_id)).all()]
    return envelope({"work_date":work_date.isoformat(),"timezone":tenant.timezone,"allow_multi_checkin":tenant.allow_multi_checkin,
        "schedule":{"start":schedule.start_time.isoformat() if schedule else None,"end":schedule.end_time.isoformat() if schedule else None},
        "scheduled":schedule is not None and schedule.is_working_day,
        "projects":project_list,"open_shift":{"project_id":open_shift.project_id,"project_name":projects[open_shift.project_id].name,"since":open_shift.server_time.isoformat()} if open_shift else None,
        "timeline":timeline},request)


def _open_project_counts(db,ctx,work_date,counted):
    """Map project_id -> jumlah check-in terbuka (belum ditutup check-out VALID)."""
    rows=db.scalars(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.tenant_id,AttendanceEvent.worker_id==ctx.worker.id,AttendanceEvent.work_date==work_date,AttendanceEvent.status.in_(counted)).order_by(AttendanceEvent.server_time)).all()
    m={}
    for e in rows:
        if e.event_type==AttendanceType.CHECK_IN and e.status!=AttendanceStatus.REJECTED: m[e.project_id]=m.get(e.project_id,0)+1
        elif e.event_type==AttendanceType.CHECK_OUT and e.status==AttendanceStatus.VALID: m[e.project_id]=max(0,m.get(e.project_id,0)-1)
    return {k:v for k,v in m.items() if v>0}


def _require_active_device(ctx,db) -> DeviceBinding:
    """Kembalikan DeviceBinding aktif; tolak bila belum ada (anti titip absen)."""
    binding=db.scalar(select(DeviceBinding).where(DeviceBinding.tenant_id==ctx.tenant_id,DeviceBinding.worker_id==ctx.worker.id,DeviceBinding.status==DeviceStatus.ACTIVE))
    if not binding: raise HTTPException(409,detail={"code":"NO_ACTIVE_DEVICE","message":"Perangkat belum didaftarkan/disetujui supervisor"})
    return binding


@router.post("/challenge")
def web_challenge(body: AttendanceChallengeRequest,request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    _require_active_device(ctx,db)
    tenant=db.get(Tenant,ctx.tenant_id); work_date=work_date_for(tenant)
    event_type=AttendanceType(body.event_type)
    counted=[AttendanceStatus.VALID,AttendanceStatus.REVIEW]
    open_checkin=db.scalar(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.tenant_id,AttendanceEvent.worker_id==ctx.worker.id,AttendanceEvent.work_date==work_date,AttendanceEvent.event_type==AttendanceType.CHECK_IN,AttendanceEvent.status.in_(counted)).order_by(AttendanceEvent.server_time.desc()))
    if event_type==AttendanceType.CHECK_IN:
        open_counts=_open_project_counts(db,ctx,work_date,counted)
        already=(body.project_id in open_counts) or (open_checkin and body.project_id==open_checkin.project_id and not tenant.allow_multi_checkin)
        if already: raise HTTPException(409,detail={"code":"ATTENDANCE_ALREADY_RECORDED"})
    elif not _open_project_counts(db,ctx,work_date,counted): raise HTTPException(409,detail={"code":"CHECK_IN_REQUIRED"})
    target=body.project_id or (open_checkin.project_id if open_checkin else None)
    if not target: raise HTTPException(409,detail={"code":"PROJECT_REQUIRED"})
    project=db.get(Project,target)
    if not project or project.tenant_id!=ctx.tenant_id: raise HTTPException(404,detail={"code":"PROJECT_NOT_FOUND"})
    raw=secrets.token_bytes(32)
    item=AttendanceChallenge(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,project_id=project.id,event_type=event_type,challenge_hash=hashlib.sha256(raw).hexdigest(),work_date=work_date,expires_at=datetime.now(timezone.utc)+timedelta(minutes=2))
    db.add(item); db.commit()
    return envelope({"challenge_id":item.id,"challenge":b64url_encode(raw),"expires_in":120,"event_type":event_type.value,
                     "project":{"id":project.id,"name":project.name,"latitude":project.latitude,"longitude":project.longitude,"radius_m":project.geofence_radius_m}},request)


@router.post("/events")
def web_submit(body: WebSubmitRequest,request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    tenant=db.get(Tenant,ctx.tenant_id); work_date=work_date_for(tenant)
    ch=db.scalar(select(AttendanceChallenge).where(AttendanceChallenge.id==body.challenge_id,AttendanceChallenge.tenant_id==ctx.tenant_id,AttendanceChallenge.worker_id==ctx.worker.id))
    if not ch or ch.used_at or ch.expires_at.replace(tzinfo=timezone.utc)<datetime.now(timezone.utc): raise HTTPException(409,detail={"code":"ATTENDANCE_CHALLENGE_INVALID"})
    if ch.event_type.value!=body.event_type or ch.project_id!=body.project_id or hashlib.sha256(b64url_decode(body.challenge)).hexdigest()!=ch.challenge_hash: raise HTTPException(409,detail={"code":"ATTENDANCE_PAYLOAD_MISMATCH"})
    try: captured=datetime.fromisoformat(body.captured_at_client.replace("Z","+00:00"))
    except ValueError: raise HTTPException(422,detail={"code":"INVALID_CLIENT_TIME"})
    binding=_require_active_device(ctx,db)
    try: verify_signature(json.loads(binding.public_key_jwk),signed_payload(body),body.signature)
    except ValueError: raise HTTPException(422,detail={"code":"INVALID_ATTENDANCE_SIGNATURE"})
    counted=[AttendanceStatus.VALID,AttendanceStatus.REVIEW]
    if ch.event_type==AttendanceType.CHECK_IN and tenant.allow_multi_checkin:
        for shift in db.scalars(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.tenant_id,AttendanceEvent.worker_id==ctx.worker.id,AttendanceEvent.work_date==work_date,AttendanceEvent.event_type==AttendanceType.CHECK_IN,AttendanceEvent.status.in_(counted))).all():
            if shift.project_id!=ch.project_id and shift.project_id in _open_project_counts(db,ctx,work_date,counted):
                db.add(AttendanceEvent(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,project_id=shift.project_id,device_binding_id=binding.id,challenge_id=None,event_type=AttendanceType.CHECK_OUT,status=AttendanceStatus.VALID,reason_code="AUTO_CHECKOUT_MOVE_SITE",work_date=work_date,captured_at_client=captured,latitude=body.latitude,longitude=body.longitude,accuracy_m=body.accuracy_m,distance_m=0.0,signature="AUTO:"+body.signature))
    status,reason,distance=attendance_status(db.get(Project,ch.project_id),body.latitude,body.longitude,body.accuracy_m)
    ch.used_at=datetime.now(timezone.utc)
    ev=AttendanceEvent(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,project_id=ch.project_id,device_binding_id=binding.id,challenge_id=ch.id,event_type=ch.event_type,status=status,reason_code=reason,work_date=work_date,captured_at_client=captured,latitude=body.latitude,longitude=body.longitude,accuracy_m=body.accuracy_m,distance_m=distance,signature=body.signature,site_note=(body.site_note or None))
    db.add(ev); db.commit()
    return envelope({"event_id":ev.id,"status":ev.status.value,"reason_code":ev.reason_code,"distance_m":round(ev.distance_m,1),"project_name":db.get(Project,ch.project_id).name,"server_time":ev.server_time.isoformat()},request)
