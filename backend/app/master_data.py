import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.dependencies import RequestContext, admin_context
from app.imports import read_google_sheet, serializable, spreadsheet_id, validate
from app.models import Assignment, ImportBatch, ImportStatus, Membership, Project, RoleCode, SupervisorProject, User, Worker, WorkSchedule
from app.schemas import SheetsPreviewRequest

router=APIRouter(prefix="/api/v1/master-data",tags=["master-data"])

def _envelope(data,request):
    return {"data":data,"meta":{"correlation_id":request.state.correlation_id,"server_time":datetime.now(timezone.utc).isoformat(),"version":"v1"}}

@router.get("/summary")
def summary(request: Request,ctx: RequestContext=Depends(admin_context),db: Session=Depends(get_db)):
    tenant_id=ctx.membership.tenant_id
    return _envelope({
        "projects":len(db.scalars(select(Project).where(Project.tenant_id==tenant_id)).all()),
        "workers":len(db.scalars(select(Worker).where(Worker.tenant_id==tenant_id)).all()),
        "assignments":len(db.scalars(select(Assignment).where(Assignment.tenant_id==tenant_id)).all()),
        "schedules":len(db.scalars(select(WorkSchedule).where(WorkSchedule.tenant_id==tenant_id)).all()),
    },request)

@router.get("/projects")
def projects(request: Request,ctx: RequestContext=Depends(admin_context),db: Session=Depends(get_db)):
    rows=db.scalars(select(Project).where(Project.tenant_id==ctx.membership.tenant_id).order_by(Project.code)).all()
    return _envelope([{"id":row.id,"code":row.code,"name":row.name,"latitude":row.latitude,"longitude":row.longitude,"radius_m":row.geofence_radius_m,"work_start":row.work_start.isoformat(timespec="minutes"),"work_end":row.work_end.isoformat(timespec="minutes"),"is_active":row.is_active} for row in rows],request)

@router.get("/workers")
def workers(request: Request,ctx: RequestContext=Depends(admin_context),db: Session=Depends(get_db)):
    rows=db.scalars(select(Worker).where(Worker.tenant_id==ctx.membership.tenant_id).order_by(Worker.code)).all()
    return _envelope([{"id":row.id,"code":row.code,"name":row.name,"phone":row.phone,"is_active":row.is_active} for row in rows],request)

@router.post("/imports/google-sheets/preview")
def preview(body: SheetsPreviewRequest,request: Request,ctx: RequestContext=Depends(admin_context),db: Session=Depends(get_db)):
    try:
        book_id=spreadsheet_id(body.spreadsheet_url); clean,errors=validate(read_google_sheet(book_id))
    except (ValueError,httpx.HTTPError) as exc:
        raise HTTPException(422,detail={"code":"SHEET_READ_FAILED","message":str(exc)})
    counts={tab:len(rows) for tab,rows in clean.items()}; payload=serializable(clean)
    batch=ImportBatch(tenant_id=ctx.membership.tenant_id,spreadsheet_id=book_id,payload_json=json.dumps(payload),summary_json=json.dumps({"counts":counts,"errors":errors}),created_by=ctx.user.id,status=ImportStatus.PREVIEW if not errors else ImportStatus.REJECTED)
    db.add(batch); db.flush(); record_audit(db,tenant_id=ctx.membership.tenant_id,actor_user_id=ctx.user.id,action="SHEETS_IMPORT_PREVIEW",entity_type="import_batch",entity_id=batch.id,correlation_id=request.state.correlation_id,payload={"counts":counts,"error_count":len(errors)}); db.commit()
    return _envelope({"batch_id":batch.id,"status":batch.status.value,"counts":counts,"errors":errors},request)

def _upsert(db,model,tenant_id,code_field,code,values):
    item=db.scalar(select(model).where(model.tenant_id==tenant_id,getattr(model,code_field)==code))
    if item:
        for key,value in values.items(): setattr(item,key,value)
    else:
        item=model(tenant_id=tenant_id,**{code_field:code},**values); db.add(item)
    db.flush(); return item

@router.post("/imports/{batch_id}/confirm")
def confirm(batch_id: str,request: Request,ctx: RequestContext=Depends(admin_context),db: Session=Depends(get_db)):
    tenant_id=ctx.membership.tenant_id
    batch=db.scalar(select(ImportBatch).where(ImportBatch.id==batch_id,ImportBatch.tenant_id==tenant_id))
    if not batch: raise HTTPException(404,detail={"code":"IMPORT_NOT_FOUND"})
    if batch.status==ImportStatus.CONFIRMED: return _envelope({"batch_id":batch.id,"status":"CONFIRMED","idempotent":True},request)
    if batch.status!=ImportStatus.PREVIEW: raise HTTPException(409,detail={"code":"IMPORT_HAS_ERRORS"})
    data=json.loads(batch.payload_json); projects={}; workers={}
    for row in data["Projects"]:
        projects[row["project_code"]]=_upsert(db,Project,tenant_id,"code",row["project_code"],{"name":row["project_name"],"latitude":row["latitude"],"longitude":row["longitude"],"geofence_radius_m":row["radius_m"],"work_start":datetime.strptime(row["work_start"],"%H:%M:%S").time(),"work_end":datetime.strptime(row["work_end"],"%H:%M:%S").time(),"is_active":True})
    for row in data["Workers"]:
        workers[row["worker_code"]]=_upsert(db,Worker,tenant_id,"code",row["worker_code"],{"name":row["worker_name"],"phone":row.get("phone") or None,"is_active":row["is_active"]})
    for row in data["Assignments"]:
        work_date=datetime.fromisoformat(row["work_date"]).date(); worker=workers[row["worker_code"]]; project=projects[row["project_code"]]
        item=db.scalar(select(Assignment).where(Assignment.tenant_id==tenant_id,Assignment.worker_id==worker.id,Assignment.work_date==work_date))
        if item: item.project_id=project.id
        else: db.add(Assignment(tenant_id=tenant_id,worker_id=worker.id,project_id=project.id,work_date=work_date))
    for row in data["Schedules"]:
        work_date=datetime.fromisoformat(row["work_date"]).date(); worker=workers[row["worker_code"]]
        item=db.scalar(select(WorkSchedule).where(WorkSchedule.tenant_id==tenant_id,WorkSchedule.worker_id==worker.id,WorkSchedule.work_date==work_date)); values={"start_time":datetime.strptime(row["start_time"],"%H:%M:%S").time(),"end_time":datetime.strptime(row["end_time"],"%H:%M:%S").time(),"is_working_day":row["is_working_day"]}
        if item:
            for key,value in values.items(): setattr(item,key,value)
        else: db.add(WorkSchedule(tenant_id=tenant_id,worker_id=worker.id,work_date=work_date,**values))
    for row in data["Supervisors"]:
        membership=db.scalar(select(Membership).join(User).where(Membership.tenant_id==tenant_id,Membership.role==RoleCode.SUPERVISOR,User.login_id==row["login_id"]))
        if not membership: raise HTTPException(409,detail={"code":"SUPERVISOR_NOT_FOUND","login_id":row["login_id"]})
        exists=db.scalar(select(SupervisorProject).where(SupervisorProject.tenant_id==tenant_id,SupervisorProject.membership_id==membership.id,SupervisorProject.project_id==projects[row["project_code"]].id))
        if not exists: db.add(SupervisorProject(tenant_id=tenant_id,membership_id=membership.id,project_id=projects[row["project_code"]].id))
    batch.status=ImportStatus.CONFIRMED; batch.confirmed_at=datetime.now(timezone.utc); record_audit(db,tenant_id=tenant_id,actor_user_id=ctx.user.id,action="SHEETS_IMPORT_CONFIRMED",entity_type="import_batch",entity_id=batch.id,correlation_id=request.state.correlation_id); db.commit()
    return _envelope({"batch_id":batch.id,"status":"CONFIRMED","idempotent":False},request)
