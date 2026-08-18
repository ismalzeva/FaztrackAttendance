import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.dependencies import RequestContext, WorkerContext, approval_context, worker_context
from app.device_crypto import b64url_decode, b64url_encode, canonical_jwk, thumbprint, verify_signature
from app.models import Assignment, DeviceBinding, DeviceChallenge, DeviceEnrollment, DeviceStatus, EnrollmentStatus, Project, RoleCode, SupervisorProject, Tenant, Worker
from app.schemas import EnrollmentDecision, EnrollmentRequest, WorkerLoginRequest
from app.security import create_worker_token, verify_password

router=APIRouter(prefix="/api/v1",tags=["devices"])

def envelope(data,request): return {"data":data,"meta":{"correlation_id":request.state.correlation_id,"server_time":datetime.now(timezone.utc).isoformat(),"version":"v1"}}
def utc(value: datetime) -> datetime: return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

@router.post("/worker/auth/login")
def worker_login(body: WorkerLoginRequest,request: Request,db: Session=Depends(get_db)):
    worker=db.scalar(select(Worker).join(Tenant,Worker.tenant_id==Tenant.id).where(Tenant.code==body.tenant_code,Worker.code==body.worker_code,Worker.is_active.is_(True)))
    if not worker or not worker.pin_hash or not verify_password(body.pin,worker.pin_hash): raise HTTPException(401,detail={"code":"INVALID_WORKER_CREDENTIALS"})
    record_audit(db,tenant_id=worker.tenant_id,actor_user_id=None,action="WORKER_LOGIN",entity_type="worker",entity_id=worker.id,correlation_id=request.state.correlation_id); db.commit()
    return envelope({"access_token":create_worker_token(worker.id,worker.tenant_id),"token_type":"bearer","worker":{"id":worker.id,"code":worker.code,"name":worker.name}},request)

@router.post("/worker/device-enrollment/challenge")
def challenge(request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    raw=secrets.token_bytes(32); item=DeviceChallenge(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,challenge_hash=hashlib.sha256(raw).hexdigest(),expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)); db.add(item); db.commit()
    return envelope({"challenge_id":item.id,"challenge":b64url_encode(raw),"expires_in":300},request)

@router.post("/worker/device-enrollment/requests")
def request_enrollment(body: EnrollmentRequest,request: Request,ctx: WorkerContext=Depends(worker_context),db: Session=Depends(get_db)):
    item=db.scalar(select(DeviceChallenge).where(DeviceChallenge.id==body.challenge_id,DeviceChallenge.tenant_id==ctx.tenant_id,DeviceChallenge.worker_id==ctx.worker.id))
    if not item or item.used_at or utc(item.expires_at)<datetime.now(timezone.utc): raise HTTPException(409,detail={"code":"CHALLENGE_INVALID_OR_EXPIRED"})
    raw=b64url_decode(request.headers.get("X-Device-Challenge", ""))
    if hashlib.sha256(raw).hexdigest()!=item.challenge_hash: raise HTTPException(409,detail={"code":"CHALLENGE_MISMATCH"})
    try: verify_signature(body.public_key_jwk,raw,body.signature); key_json=canonical_jwk(body.public_key_jwk); key_thumbprint=thumbprint(body.public_key_jwk)
    except ValueError as exc: raise HTTPException(422,detail={"code":"INVALID_DEVICE_PROOF","message":str(exc)})
    item.used_at=datetime.now(timezone.utc)
    existing=db.scalar(select(DeviceEnrollment).where(DeviceEnrollment.tenant_id==ctx.tenant_id,DeviceEnrollment.worker_id==ctx.worker.id,DeviceEnrollment.public_key_thumbprint==key_thumbprint,DeviceEnrollment.status==EnrollmentStatus.PENDING))
    if existing: enrollment=existing
    else:
        enrollment=DeviceEnrollment(tenant_id=ctx.tenant_id,worker_id=ctx.worker.id,public_key_jwk=key_json,public_key_thumbprint=key_thumbprint,device_label=body.device_label,status=EnrollmentStatus.PENDING); db.add(enrollment); db.flush()
    record_audit(db,tenant_id=ctx.tenant_id,actor_user_id=None,action="DEVICE_ENROLLMENT_REQUESTED",entity_type="device_enrollment",entity_id=enrollment.id,correlation_id=request.state.correlation_id,payload={"worker_id":ctx.worker.id,"thumbprint":key_thumbprint}); db.commit()
    return envelope({"enrollment_id":enrollment.id,"status":enrollment.status.value},request)

def allowed_worker_ids(ctx: RequestContext,db: Session) -> set[str] | None:
    if ctx.membership.role in (RoleCode.OWNER,RoleCode.ADMIN): return None
    project_ids=db.scalars(select(SupervisorProject.project_id).where(SupervisorProject.tenant_id==ctx.membership.tenant_id,SupervisorProject.membership_id==ctx.membership.id)).all()
    return set(db.scalars(select(Assignment.worker_id).where(Assignment.tenant_id==ctx.membership.tenant_id,Assignment.project_id.in_(project_ids),Assignment.work_date>=date.today())).all())

@router.get("/device-enrollments/pending")
def pending(request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    rows=db.scalars(select(DeviceEnrollment).where(DeviceEnrollment.tenant_id==ctx.membership.tenant_id,DeviceEnrollment.status==EnrollmentStatus.PENDING).order_by(DeviceEnrollment.requested_at)).all(); allowed=allowed_worker_ids(ctx,db)
    if allowed is not None: rows=[row for row in rows if row.worker_id in allowed]
    workers={row.id:row for row in db.scalars(select(Worker).where(Worker.tenant_id==ctx.membership.tenant_id)).all()}
    return envelope([{"id":row.id,"worker_code":workers[row.worker_id].code,"worker_name":workers[row.worker_id].name,"device_label":row.device_label,"requested_at":row.requested_at.isoformat()} for row in rows],request)

def enrollment_for_approver(enrollment_id: str,ctx: RequestContext,db: Session) -> DeviceEnrollment:
    enrollment=db.scalar(select(DeviceEnrollment).where(DeviceEnrollment.id==enrollment_id,DeviceEnrollment.tenant_id==ctx.membership.tenant_id,DeviceEnrollment.status==EnrollmentStatus.PENDING))
    allowed=allowed_worker_ids(ctx,db)
    if not enrollment or (allowed is not None and enrollment.worker_id not in allowed): raise HTTPException(404,detail={"code":"ENROLLMENT_NOT_FOUND"})
    return enrollment

@router.post("/device-enrollments/{enrollment_id}/approve")
def approve(enrollment_id: str,body: EnrollmentDecision,request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    enrollment=enrollment_for_approver(enrollment_id,ctx,db); now=datetime.now(timezone.utc)
    active=db.scalars(select(DeviceBinding).where(DeviceBinding.tenant_id==ctx.membership.tenant_id,DeviceBinding.worker_id==enrollment.worker_id,DeviceBinding.status==DeviceStatus.ACTIVE)).all()
    for binding in active: binding.status=DeviceStatus.REVOKED; binding.revoked_at=now; binding.revoked_by=ctx.user.id; binding.revoke_reason="DEVICE_REPLACED"
    others=db.scalars(select(DeviceEnrollment).where(DeviceEnrollment.tenant_id==ctx.membership.tenant_id,DeviceEnrollment.worker_id==enrollment.worker_id,DeviceEnrollment.status==EnrollmentStatus.PENDING,DeviceEnrollment.id!=enrollment.id)).all()
    for other in others: other.status=EnrollmentStatus.SUPERSEDED; other.reviewed_at=now; other.reviewed_by=ctx.user.id
    enrollment.status=EnrollmentStatus.APPROVED; enrollment.reviewed_at=now; enrollment.reviewed_by=ctx.user.id
    db.add(DeviceBinding(tenant_id=enrollment.tenant_id,worker_id=enrollment.worker_id,enrollment_id=enrollment.id,public_key_jwk=enrollment.public_key_jwk,public_key_thumbprint=enrollment.public_key_thumbprint,device_label=enrollment.device_label,status=DeviceStatus.ACTIVE))
    record_audit(db,tenant_id=enrollment.tenant_id,actor_user_id=ctx.user.id,action="DEVICE_ENROLLMENT_APPROVED",entity_type="device_enrollment",entity_id=enrollment.id,correlation_id=request.state.correlation_id,reason=body.reason); db.commit()
    return envelope({"enrollment_id":enrollment.id,"status":"APPROVED","replaced_devices":len(active)},request)

@router.post("/device-enrollments/{enrollment_id}/reject")
def reject(enrollment_id: str,body: EnrollmentDecision,request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    if not body.reason: raise HTTPException(422,detail={"code":"REJECTION_REASON_REQUIRED"})
    enrollment=enrollment_for_approver(enrollment_id,ctx,db); enrollment.status=EnrollmentStatus.REJECTED; enrollment.reviewed_at=datetime.now(timezone.utc); enrollment.reviewed_by=ctx.user.id; enrollment.rejection_reason=body.reason
    record_audit(db,tenant_id=enrollment.tenant_id,actor_user_id=ctx.user.id,action="DEVICE_ENROLLMENT_REJECTED",entity_type="device_enrollment",entity_id=enrollment.id,correlation_id=request.state.correlation_id,reason=body.reason); db.commit()
    return envelope({"enrollment_id":enrollment.id,"status":"REJECTED"},request)
