import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.dependencies import RequestContext, admin_context, approval_context
from app.models import Assignment, AttendanceEvent, AttendancePolicy, AttendanceStatus, AttendanceType, Project, RoleCode, SupervisorProject, Tenant, TimesheetPeriod, TimesheetPeriodStatus, Worker, WorkSchedule
from app.schemas import AttendancePolicyUpdate, TimesheetCloseRequest

router=APIRouter(prefix="/api/v1",tags=["timesheets"])
def envelope(data,request): return {"data":data,"meta":{"correlation_id":request.state.correlation_id,"server_time":datetime.now(timezone.utc).isoformat(),"version":"v1"}}
def aware(value: datetime) -> datetime: return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
def project_scope(ctx: RequestContext,db: Session) -> set[str] | None:
    if ctx.membership.role in (RoleCode.OWNER,RoleCode.ADMIN): return None
    return set(db.scalars(select(SupervisorProject.project_id).where(SupervisorProject.tenant_id==ctx.membership.tenant_id,SupervisorProject.membership_id==ctx.membership.id)).all())
def policy_for(tenant_id: str,db: Session) -> AttendancePolicy:
    policy=db.get(AttendancePolicy,tenant_id)
    return policy or AttendancePolicy(tenant_id=tenant_id,late_grace_minutes=0,early_leave_grace_minutes=0)

def derive(ctx: RequestContext,db: Session,date_from: date,date_to: date,project_id: str | None):
    if date_to<date_from or (date_to-date_from).days>62: raise HTTPException(422,detail={"code":"INVALID_TIMESHEET_RANGE"})
    allowed=project_scope(ctx,db)
    if allowed is not None and project_id and project_id not in allowed: raise HTTPException(403,detail={"code":"FORBIDDEN_PROJECT"})
    query=select(Assignment).where(Assignment.tenant_id==ctx.membership.tenant_id,Assignment.work_date>=date_from,Assignment.work_date<=date_to)
    if project_id: query=query.where(Assignment.project_id==project_id)
    elif allowed is not None: query=query.where(Assignment.project_id.in_(allowed))
    assignments=db.scalars(query.order_by(Assignment.work_date,Assignment.worker_id)).all(); worker_ids={a.worker_id for a in assignments}; project_ids={a.project_id for a in assignments}
    workers={w.id:w for w in db.scalars(select(Worker).where(Worker.id.in_(worker_ids))).all()} if worker_ids else {}; projects={p.id:p for p in db.scalars(select(Project).where(Project.id.in_(project_ids))).all()} if project_ids else {}
    schedules={(s.worker_id,s.work_date):s for s in db.scalars(select(WorkSchedule).where(WorkSchedule.tenant_id==ctx.membership.tenant_id,WorkSchedule.worker_id.in_(worker_ids),WorkSchedule.work_date>=date_from,WorkSchedule.work_date<=date_to)).all()} if worker_ids else {}
    events=defaultdict(list)
    if worker_ids:
        for event in db.scalars(select(AttendanceEvent).where(AttendanceEvent.tenant_id==ctx.membership.tenant_id,AttendanceEvent.worker_id.in_(worker_ids),AttendanceEvent.work_date>=date_from,AttendanceEvent.work_date<=date_to)).all(): events[(event.worker_id,event.work_date)].append(event)
    tenant=db.get(Tenant,ctx.membership.tenant_id); zone=ZoneInfo(tenant.timezone); today=datetime.now(zone).date(); policy=policy_for(tenant.id,db); rows=[]
    for assignment in assignments:
        schedule=schedules.get((assignment.worker_id,assignment.work_date))
        if not schedule or not schedule.is_working_day: continue
        day_events=events[(assignment.worker_id,assignment.work_date)]; reviews=[e for e in day_events if e.status==AttendanceStatus.REVIEW]; checkin=next((e for e in day_events if e.event_type==AttendanceType.CHECK_IN and e.status==AttendanceStatus.VALID),None); checkout=next((e for e in day_events if e.event_type==AttendanceType.CHECK_OUT and e.status==AttendanceStatus.VALID),None)
        if reviews: state="EXCEPTION"
        elif checkin and checkout: state="PRESENT"
        elif checkin: state="INCOMPLETE"
        elif assignment.work_date<today: state="ABSENT"
        else: state="PENDING"
        late=0; early=0
        if checkin:
            local=aware(checkin.server_time).astimezone(zone); planned=datetime.combine(assignment.work_date,schedule.start_time,tzinfo=zone); late=max(0,int((local-planned).total_seconds()//60)-policy.late_grace_minutes)
        if checkout:
            local=aware(checkout.server_time).astimezone(zone); planned=datetime.combine(assignment.work_date,schedule.end_time,tzinfo=zone); early=max(0,int((planned-local).total_seconds()//60)-policy.early_leave_grace_minutes)
        rows.append({"work_date":assignment.work_date.isoformat(),"worker_id":assignment.worker_id,"worker_code":workers[assignment.worker_id].code,"worker_name":workers[assignment.worker_id].name,"project_id":assignment.project_id,"project_name":projects[assignment.project_id].name,"state":state,"scheduled_start":schedule.start_time.isoformat(timespec="minutes"),"scheduled_end":schedule.end_time.isoformat(timespec="minutes"),"check_in":aware(checkin.server_time).astimezone(zone).isoformat() if checkin else None,"check_out":aware(checkout.server_time).astimezone(zone).isoformat() if checkout else None,"late_minutes":late,"early_leave_minutes":early,"rejected_attempts":sum(e.status==AttendanceStatus.REJECTED for e in day_events)})
    scheduled=len(rows); counts={state:sum(row["state"]==state for row in rows) for state in ("PRESENT","ABSENT","INCOMPLETE","EXCEPTION","PENDING")}; present=counts["PRESENT"]
    return {"date_from":date_from.isoformat(),"date_to":date_to.isoformat(),"project_id":project_id,"policy":{"late_grace_minutes":policy.late_grace_minutes,"early_leave_grace_minutes":policy.early_leave_grace_minutes},"summary":{"scheduled_days":scheduled,"present_days":present,"absent_days":counts["ABSENT"],"incomplete_days":counts["INCOMPLETE"],"exception_days":counts["EXCEPTION"],"pending_days":counts["PENDING"],"late_days":sum(row["late_minutes"]>0 for row in rows),"early_leave_days":sum(row["early_leave_minutes"]>0 for row in rows),"attendance_factor":round(present/scheduled,4) if scheduled else 0},"rows":rows}

@router.get("/timesheets")
def timesheets(request: Request,date_from: date=Query(),date_to: date=Query(),project_id: str | None=None,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    return envelope(derive(ctx,db,date_from,date_to,project_id),request)

@router.get("/timesheets/projects")
def timesheet_projects(request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    query=select(Project).where(Project.tenant_id==ctx.membership.tenant_id,Project.is_active.is_(True))
    allowed=project_scope(ctx,db)
    if allowed is not None: query=query.where(Project.id.in_(allowed))
    rows=db.scalars(query.order_by(Project.code)).all()
    return envelope([{"id":row.id,"code":row.code,"name":row.name} for row in rows],request)

@router.get("/attendance-policy")
def get_policy(request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    policy=policy_for(ctx.membership.tenant_id,db); return envelope({"late_grace_minutes":policy.late_grace_minutes,"early_leave_grace_minutes":policy.early_leave_grace_minutes},request)

@router.put("/attendance-policy")
def update_policy(body: AttendancePolicyUpdate,request: Request,ctx: RequestContext=Depends(admin_context),db: Session=Depends(get_db)):
    policy=db.get(AttendancePolicy,ctx.membership.tenant_id)
    if not policy: policy=AttendancePolicy(tenant_id=ctx.membership.tenant_id); db.add(policy)
    policy.late_grace_minutes=body.late_grace_minutes; policy.early_leave_grace_minutes=body.early_leave_grace_minutes; policy.updated_by=ctx.user.id
    record_audit(db,tenant_id=ctx.membership.tenant_id,actor_user_id=ctx.user.id,action="ATTENDANCE_POLICY_UPDATED",entity_type="attendance_policy",entity_id=ctx.membership.tenant_id,correlation_id=request.state.correlation_id,payload=body.model_dump()); db.commit(); return envelope(body.model_dump(),request)

@router.post("/timesheet-periods/close")
def close_period(body: TimesheetCloseRequest,request: Request,ctx: RequestContext=Depends(approval_context),db: Session=Depends(get_db)):
    tenant=db.get(Tenant,ctx.membership.tenant_id); today=datetime.now(ZoneInfo(tenant.timezone)).date()
    if body.date_to>=today: raise HTTPException(409,detail={"code":"PERIOD_NOT_FINISHED"})
    allowed=project_scope(ctx,db)
    if ctx.membership.role==RoleCode.SUPERVISOR and (not body.project_id or body.project_id not in (allowed or set())): raise HTTPException(403,detail={"code":"SUPERVISOR_PROJECT_REQUIRED"})
    if body.project_id and not db.scalar(select(Project.id).where(Project.id==body.project_id,Project.tenant_id==ctx.membership.tenant_id)): raise HTTPException(404,detail={"code":"PROJECT_NOT_FOUND"})
    report=derive(ctx,db,body.date_from,body.date_to,body.project_id); blockers=report["summary"]["incomplete_days"]+report["summary"]["exception_days"]+report["summary"]["pending_days"]
    if not report["summary"]["scheduled_days"]: raise HTTPException(409,detail={"code":"NO_SCHEDULED_DAYS"})
    if blockers: raise HTTPException(409,detail={"code":"TIMESHEET_HAS_OPEN_EXCEPTIONS","count":blockers})
    scope_key=body.project_id or "ALL"; period=db.scalar(select(TimesheetPeriod).where(TimesheetPeriod.tenant_id==ctx.membership.tenant_id,TimesheetPeriod.date_from==body.date_from,TimesheetPeriod.date_to==body.date_to,TimesheetPeriod.scope_key==scope_key))
    if period and period.status==TimesheetPeriodStatus.CLOSED: return envelope({"period_id":period.id,"status":"CLOSED","idempotent":True,"summary":json.loads(period.snapshot_json)["summary"]},request)
    snapshot=json.dumps(report,separators=(",",":"),sort_keys=True); period=period or TimesheetPeriod(tenant_id=ctx.membership.tenant_id,date_from=body.date_from,date_to=body.date_to,project_id=body.project_id,scope_key=scope_key); db.add(period); period.status=TimesheetPeriodStatus.CLOSED; period.snapshot_json=snapshot; period.snapshot_hash=hashlib.sha256(snapshot.encode()).hexdigest(); period.closed_at=datetime.now(timezone.utc); period.closed_by=ctx.user.id; db.flush()
    record_audit(db,tenant_id=ctx.membership.tenant_id,actor_user_id=ctx.user.id,action="TIMESHEET_PERIOD_CLOSED",entity_type="timesheet_period",entity_id=period.id,correlation_id=request.state.correlation_id,payload={"date_from":body.date_from.isoformat(),"date_to":body.date_to.isoformat(),"project_id":body.project_id,"snapshot_hash":period.snapshot_hash}); db.commit(); return envelope({"period_id":period.id,"status":"CLOSED","idempotent":False,"summary":report["summary"],"snapshot_hash":period.snapshot_hash},request)
