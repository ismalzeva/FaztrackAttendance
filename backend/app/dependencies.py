from dataclasses import dataclass
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
import jwt
from app.database import get_db
from app.models import Membership, MembershipStatus, ProjectScope, RoleCode, TenantStatus, User, Worker
from app.security import decode_access_token, decode_worker_token

bearer=HTTPBearer(auto_error=False)
@dataclass(frozen=True)
class RequestContext:
    user: User; membership: Membership; project_ids: tuple[str,...]
@dataclass(frozen=True)
class WorkerContext:
    worker: Worker; tenant_id: str

def current_user(credentials: HTTPAuthorizationCredentials | None=Depends(bearer),db: Session=Depends(get_db)) -> User:
    if not credentials: raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"SESSION_EXPIRED"})
    try: user_id=decode_access_token(credentials.credentials)
    except jwt.PyJWTError: raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"SESSION_EXPIRED"})
    user=db.get(User,user_id)
    if not user or not user.is_active: raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"SESSION_EXPIRED"})
    return user

def tenant_context(x_tenant_id: str=Header(alias="X-Tenant-ID"),user: User=Depends(current_user),db: Session=Depends(get_db)) -> RequestContext:
    membership=db.scalar(select(Membership).where(Membership.user_id==user.id,Membership.tenant_id==x_tenant_id,Membership.status==MembershipStatus.ACTIVE))
    if not membership or membership.tenant.status!=TenantStatus.ACTIVE: raise HTTPException(status.HTTP_403_FORBIDDEN,detail={"code":"FORBIDDEN_SCOPE"})
    projects=tuple(db.scalars(select(ProjectScope.project_id).where(ProjectScope.tenant_id==x_tenant_id,ProjectScope.membership_id==membership.id)).all())
    return RequestContext(user,membership,projects)

def admin_context(ctx: RequestContext=Depends(tenant_context)) -> RequestContext:
    if ctx.membership.role not in (RoleCode.OWNER,RoleCode.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN,detail={"code":"ADMIN_REQUIRED"})
    return ctx

def approval_context(ctx: RequestContext=Depends(tenant_context)) -> RequestContext:
    if ctx.membership.role not in (RoleCode.OWNER,RoleCode.ADMIN,RoleCode.SUPERVISOR):
        raise HTTPException(status.HTTP_403_FORBIDDEN,detail={"code":"APPROVER_REQUIRED"})
    return ctx

def worker_context(credentials: HTTPAuthorizationCredentials | None=Depends(bearer),db: Session=Depends(get_db)) -> WorkerContext:
    if not credentials: raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"WORKER_SESSION_EXPIRED"})
    try: worker_id,tenant_id=decode_worker_token(credentials.credentials)
    except jwt.PyJWTError: raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"WORKER_SESSION_EXPIRED"})
    worker=db.scalar(select(Worker).where(Worker.id==worker_id,Worker.tenant_id==tenant_id,Worker.is_active.is_(True)))
    if not worker: raise HTTPException(status.HTTP_401_UNAUTHORIZED,detail={"code":"WORKER_SESSION_EXPIRED"})
    return WorkerContext(worker,tenant_id)
