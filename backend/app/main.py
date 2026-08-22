import uuid
from datetime import datetime, timezone
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import RequestContext, current_user, tenant_context
from app.models import Membership, MembershipStatus, ProjectScope, User
from app.schemas import LoginRequest
from app.security import create_access_token, verify_password
from app.master_data import router as master_data_router
from app.devices import router as devices_router
from app.attendance import router as attendance_router
from app.timesheets import router as timesheets_router
from app.dashboard import router as dashboard_router
from app.roster import router as roster_router

settings=get_settings(); app=FastAPI(title="Faztrack Attendance API",version="0.1.0",docs_url="/docs" if settings.env!="production" else None)
app.include_router(master_data_router)
app.include_router(devices_router)
app.include_router(attendance_router)
app.include_router(timesheets_router)
app.include_router(dashboard_router)
app.include_router(roster_router)
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

@app.middleware("http")
async def correlation(request: Request, call_next):
    cid=request.headers.get("X-Correlation-ID") or str(uuid.uuid4()); request.state.correlation_id=cid
    response=await call_next(request); response.headers["X-Correlation-ID"]=cid; return response

def envelope(data,request: Request): return {"data":data,"meta":{"correlation_id":request.state.correlation_id,"server_time":datetime.now(timezone.utc).isoformat(),"version":"v1"}}

@app.get("/health/live")
def live(): return {"status":"ok"}
@app.get("/health/ready")
def ready(db: Session=Depends(get_db)):
    db.execute(select(1)); return {"status":"ready"}

@app.post("/api/v1/auth/login")
def login(body: LoginRequest,request: Request,db: Session=Depends(get_db)):
    user=db.scalar(select(User).where(User.login_id==body.login_id))
    if not user or not user.is_active or not verify_password(body.password,user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"INVALID_CREDENTIALS"})
    token=create_access_token(user.id); record_audit(db,tenant_id=None,actor_user_id=user.id,action="AUTH_LOGIN",entity_type="user",entity_id=user.id,correlation_id=request.state.correlation_id); db.commit()
    return envelope({"access_token":token,"token_type":"bearer","expires_in":settings.access_token_minutes*60},request)

@app.get("/api/v1/me")
def me(request: Request,user: User=Depends(current_user),db: Session=Depends(get_db)):
    memberships=db.scalars(select(Membership).where(Membership.user_id==user.id,Membership.status==MembershipStatus.ACTIVE)).all(); items=[]
    for m in memberships:
        scopes=list(db.scalars(select(ProjectScope.project_id).where(ProjectScope.membership_id==m.id)).all())
        items.append({"tenant_id":m.tenant_id,"tenant_name":m.tenant.name,"role":m.role.value,"project_ids":scopes})
    return envelope({"user_id":user.id,"login_id":user.login_id,"display_name":user.display_name,"memberships":items},request)

@app.get("/api/v1/context")
def context(request: Request,ctx: RequestContext=Depends(tenant_context)):
    return envelope({"tenant_id":ctx.membership.tenant_id,"role":ctx.membership.role.value,"project_ids":ctx.project_ids},request)
