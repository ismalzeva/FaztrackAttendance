import hashlib
import json
import math
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.dependencies import RequestContext, WorkerContext, approval_context, worker_context
from app.device_crypto import b64url_decode, b64url_encode, verify_signature
from app.devices import allowed_worker_ids
from app.models import Assignment, AttendanceChallenge, AttendanceEvent, AttendanceStatus, AttendanceType, DeviceBinding, DeviceStatus, Project, Tenant, WorkSchedule, Worker
from app.schemas import AttendanceChallengeRequest, AttendanceReviewDecision, AttendanceSubmitRequest

router=APIRouter(prefix="/api/v1",tags=["attendance"])

def envelope(data,request): return {"data":data,"meta":{"correlation_id":request.state.correlation_id,"server_time":datetime.now(timezone.utc).isoformat(),"version":"v1"}}
def utc(value: datetime) -> datetime: return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
def work_date_for(tenant: Tenant) -> date: return datetime.now(ZoneInfo(tenant.timezone)).date()
def distance_m(lat1,lon1,lat2,lon2):
    radius=6_371_000; p1=math.radians(lat1); p2=math.radians(lat2); dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return radius*2*math.atan2(math.sqrt(a),math.sqrt(1-a))
def signed_payload(body: AttendanceSubmitRequest) -> bytes:
    data={"accuracy_m":f"{body.accuracy_m:.1f}","captured_at_client":body.captured_at_client,"challenge":body.challenge,"challenge_id":body.challenge_id,"event_type":body.event_type,"latitude":f"{body.latitude:.6f}","longitude":f"{body.longitude:.6f}","project_id":body.project_id}
    return json.dumps(data,separators=(",",":"),sort_keys=True).encode()
def attendance_status(project: Project,lat: float,lon: float,accuracy: float):
    distance=distance_m(lat,lon,project.latitude,project.longitude)
    if accuracy>150: return AttendanceStatus.REJECTED,"GPS_ACCURACY_TOO_LOW",distance
    if distance<=project.geofence_radius_m and accuracy<=50: return AttendanceStatus.VALID,None,distance
    if distance<=project.geofence_radius_m+accuracy: return AttendanceStatus.REVIEW,"GEOFENCE_UNCERTAIN",distance
    return AttendanceStatus.REJECTED,"OUTSIDE_GEOFENCE",distance

def assignment_for_today(ctx: WorkerContext,db: Session,project_id: str | None=None):
    tenant=db.get(Tenant,ctx.tenant_id); work_date=work_date_for(tenant)
    if project_id and tenant.allow_multi_checkin:
        project=db.get(Project,project_id)
        if not project or project.tenant_id!=ctx.tenant_id: raise HTTPException(404,detail={"code":"PROJECT_NOT_FOUND"})
        base=db.scalar(select(Assignment).where(Assignment.tenant_id==ctx.tenant_id,Assignment.worker_id==ctx.worker.id,Assignment.work_date==work_date))
        schedule=db.scalar(select(WorkSchedule).where(WorkSchedule.tenant_id==ctx.tenant_id,WorkSchedule.worker_id==ctx.worker.id,WorkSchedule.work_date==work_date,WorkSchedule.is_working_day.is_(True)))
        if not base or not schedule: raise HTTPException(409,detail={"code":"NOT_SCHEDULED_TODAY"})
        return base,project,work_date
    q=select(Assignment).where(Assignment.tenant_id==ctx.tenant_id,Assignment.worker_id==ctx.worker.id,Assignment.work_date==work_date)
    if project_id: q=q.where(Assignment.project_id==project_id)
    assignment=db.scalar(q)
    schedule=db.scalar(select(WorkSchedule).where(WorkSchedule.tenant_id==ctx.tenant_id,WorkSchedule.worker_id==ctx.worker.id,WorkSchedule.work_date==work_date,WorkSchedule.is_working_day.is_(True)))
    if not assignment or not schedule:
        if not project_id and db.scalar(select(Assignment).where(Assignment.tenant_id==ctx.tenant_id,Assignment.worker_id==ctx.worker.id,Assignment.work_date==work_date)) and schedule:
            raise HTTPException(409,detail={"code":"PROJECT_NOT_ASSIGNED"})
        raise HTTPException(409,detail={"code":"NOT_SCHEDULED_TODAY"})
    return assignment,db.get(Project,assignment.project_id),work_date

@router.post("/worker/attendance/challenge")
def challenge(body: AttendanceChallengeRequest,request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    tenant=db.get(Tenant,ctx.tenant_id); work_date=work_date_for(tenant)
    binding=db.scalar(select(DeviceBinding).where(DeviceBinding.tenant_id==ctx.tenant_id,DeviceBinding.worker_id==ctx.worker.id,DeviceBinding.status==DeviceStatus.ACTIVE))
    if not binding: raise HTTPException(409,detail={"code":"NO_ACTIVE_DEVICE"})
    event_type=AttendanceType(body.event_type)
    counted=[AttendanceStatus.VALID,AttendanceStatus.REVIEW]
    open_checkin=db.scalar(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.tenant_id,AttendanceEvent.worker_id==ctx.worker.id,AttendanceEvent.work_date==work_date,AttendanceEvent.event_type==AttendanceType.CHECK_IN,AttendanceEvent.status.in_(counted)).order_by(AttendanceEvent.server_time.desc()))
    if event_type==AttendanceType.CHECK_IN:
        if open_checkin and not tenant.allow_multi_checkin: raise HTTPException(409,detail={"code":"ATTENDANCE_ALREADY_RECORDED","event_id":open_checkin.id})
        if open_checkin and body.project_id and body.project_id==open_checkin.project_id: raise HTTPException(409,detail={"code":"ATTENDANCE_ALREADY_RECORDED","event_id":open_checkin.id})
        assignment,project,_=assignment_for_today(ctx,db,body.project_id)
    else:
        if not open_checkin: raise HTTPException(409,detail={"code":"CHECK_IN_REQUIRED"})
        target=body.project_id or open_checkin.project_id
        if open_checkin.project_id!=target and not tenant.allow_multi_checkin: raise HTTPException(409,detail={"code":"CHECK_OUT_PROJECT_MISMATCH"})
        assignment,project,_=assignment_for_today(ctx,db,target)
    raw=secrets.token_bytes(32); item=AttendanceChallenge(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,project_id=project.id,event_type=event_type,challenge_hash=hashlib.sha256(raw).hexdigest(),work_date=work_date,expires_at=datetime.now(timezone.utc)+timedelta(minutes=2)); db.add(item); db.commit()
    return envelope({"challenge_id":item.id,"challenge":b64url_encode(raw),"expires_in":120,"event_type":event_type.value,"project":{"id":project.id,"code":project.code,"name":project.name,"latitude":project.latitude,"longitude":project.longitude,"radius_m":project.geofence_radius_m},"open_shift":{"project_id":open_checkin.project_id if open_checkin else None,"since":open_checkin.server_time.isoformat() if open_checkin else None}},request)

@router.post("/worker/attendance/events")
def submit(body: AttendanceSubmitRequest,request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    challenge=db.scalar(select(AttendanceChallenge).where(AttendanceChallenge.id==body.challenge_id,AttendanceChallenge.tenant_id==ctx.tenant_id,AttendanceChallenge.worker_id==ctx.worker.id))
    if not challenge or challenge.used_at or utc(challenge.expires_at)<datetime.now(timezone.utc): raise HTTPException(409,detail={"code":"ATTENDANCE_CHALLENGE_INVALID"})
    if challenge.event_type.value!=body.event_type or challenge.project_id!=body.project_id or hashlib.sha256(b64url_decode(body.challenge)).hexdigest()!=challenge.challenge_hash: raise HTTPException(409,detail={"code":"ATTENDANCE_PAYLOAD_MISMATCH"})
    try: captured=datetime.fromisoformat(body.captured_at_client.replace("Z","+00:00"))
    except ValueError: raise HTTPException(422,detail={"code":"INVALID_CLIENT_TIME"})
    binding=db.scalar(select(DeviceBinding).where(DeviceBinding.tenant_id==ctx.tenant_id,DeviceBinding.worker_id==ctx.worker.id,DeviceBinding.status==DeviceStatus.ACTIVE))
    if not binding: raise HTTPException(409,detail={"code":"NO_ACTIVE_DEVICE"})
    try: verify_signature(json.loads(binding.public_key_jwk),signed_payload(body),body.signature)
    except ValueError: raise HTTPException(422,detail={"code":"INVALID_ATTENDANCE_SIGNATURE"})
    project=db.get(Project,challenge.project_id); challenge.used_at=datetime.now(timezone.utc)
    if challenge.event_type==AttendanceType.CHECK_IN and db.get(Tenant,ctx.tenant_id).allow_multi_checkin:
        counted=[AttendanceStatus.VALID,AttendanceStatus.REVIEW]
        for shift in db.scalars(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.tenant_id,AttendanceEvent.worker_id==ctx.worker.id,AttendanceEvent.work_date==challenge.work_date,AttendanceEvent.event_type==AttendanceType.CHECK_IN,AttendanceEvent.status.in_(counted))).all():
            if shift.project_id!=project.id:
                db.add(AttendanceEvent(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,project_id=shift.project_id,device_binding_id=binding.id,challenge_id=challenge.id,event_type=AttendanceType.CHECK_OUT,status=AttendanceStatus.VALID,reason_code="AUTO_CHECKOUT_MOVE_SITE",work_date=challenge.work_date,captured_at_client=captured,latitude=body.latitude,longitude=body.longitude,accuracy_m=body.accuracy_m,distance_m=0.0,signature="AUTO:"+body.signature))
    status,reason,distance=attendance_status(project,body.latitude,body.longitude,body.accuracy_m)
    event=AttendanceEvent(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,project_id=project.id,device_binding_id=binding.id,challenge_id=challenge.id,event_type=challenge.event_type,status=status,reason_code=reason,work_date=challenge.work_date,captured_at_client=captured,latitude=body.latitude,longitude=body.longitude,accuracy_m=body.accuracy_m,distance_m=distance,signature=body.signature,site_note=(body.site_note or None)); db.add(event); db.flush()
    record_audit(db,tenant_id=ctx.tenant_id,actor_user_id=None,action=f"ATTENDANCE_{challenge.event_type.value}",entity_type="attendance_event",entity_id=event.id,correlation_id=request.state.correlation_id,payload={"status":status.value,"reason":reason,"distance_m":round(distance,1),"accuracy_m":body.accuracy_m}); db.commit()
    return envelope({"event_id":event.id,"event_type":event.event_type.value,"status":event.status.value,"reason_code":event.reason_code,"server_time":event.server_time.isoformat(),"distance_m":round(event.distance_m,1),"accuracy_m":event.accuracy_m,"project_name":project.name},request)

@router.get("/attendance/review")
def review_queue(request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    rows=db.scalars(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.membership.tenant_id,AttendanceEvent.status==AttendanceStatus.REVIEW).order_by(AttendanceEvent.server_time)).all(); workers={w.id:w for w in db.scalars(select(Worker).where(Worker.tenant_id==ctx.membership.tenant_id)).all()}; projects={p.id:p for p in db.scalars(select(Project).where(Project.tenant_id==ctx.membership.tenant_id)).all()}
    allowed=allowed_worker_ids(ctx,db)
    if allowed is not None: rows=[row for row in rows if row.worker_id in allowed]
    return envelope([{"id":row.id,"worker_code":workers[row.worker_id].code,"worker_name":workers[row.worker_id].name,"project_name":projects[row.project_id].name,"event_type":row.event_type.value,"distance_m":round(row.distance_m,1),"accuracy_m":row.accuracy_m,"server_time":row.server_time.isoformat(),"reason_code":row.reason_code} for row in rows],request)

@router.post("/attendance/{event_id}/review")
def resolve_review(event_id: str,body: AttendanceReviewDecision,request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    event=db.scalar(select(AttendanceEvent).where(AttendanceEvent.id==event_id,AttendanceEvent.tenant_id==ctx.membership.tenant_id,AttendanceEvent.status==AttendanceStatus.REVIEW))
    allowed=allowed_worker_ids(ctx,db)
    if not event or (allowed is not None and event.worker_id not in allowed): raise HTTPException(404,detail={"code":"REVIEW_EVENT_NOT_FOUND"})
    event.status=AttendanceStatus.VALID if body.approve else AttendanceStatus.REJECTED; event.reviewed_at=datetime.now(timezone.utc); event.reviewed_by=ctx.user.id; event.review_reason=body.reason
    record_audit(db,tenant_id=event.tenant_id,actor_user_id=ctx.user.id,action="ATTENDANCE_REVIEW_APPROVED" if body.approve else "ATTENDANCE_REVIEW_REJECTED",entity_type="attendance_event",entity_id=event.id,correlation_id=request.state.correlation_id,reason=body.reason); db.commit()
    return envelope({"event_id":event.id,"status":event.status.value},request)
