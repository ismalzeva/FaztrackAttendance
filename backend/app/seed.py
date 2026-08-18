from sqlalchemy import select
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Membership, RoleCode, Tenant, User
from app.security import hash_password

def main():
    password=get_settings().demo_seed_password
    if not password:
        raise RuntimeError("Set FAZTRACK_DEMO_SEED_PASSWORD before running the demo seed")
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.login_id=="admin.demo")): return
        tenant=Tenant(code="LUMIN-PILOT",name="Lumin Park Property")
        user=User(login_id="admin.demo",display_name="Admin Demo",password_hash=hash_password(password))
        db.add_all([tenant,user]); db.flush(); db.add(Membership(tenant_id=tenant.id,user_id=user.id,role=RoleCode.ADMIN))
if __name__=="__main__": main()
