import os
os.environ["FAZTRACK_DATABASE_URL"]="sqlite://"
os.environ["FAZTRACK_JWT_SECRET"]="test-secret-that-is-long-enough-for-tests"
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app
from app.models import Membership, ProjectScope, RoleCode, Tenant, User
from app.security import hash_password

engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
TestingSession=sessionmaker(bind=engine,expire_on_commit=False)
@pytest.fixture()
def db():
    Base.metadata.create_all(engine); session=TestingSession()
    yield session
    session.close(); Base.metadata.drop_all(engine)
@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db]=lambda: db
    with TestClient(app) as c: yield c
    app.dependency_overrides.clear()
@pytest.fixture()
def seeded(db):
    ta=Tenant(code="A",name="Tenant A"); tb=Tenant(code="B",name="Tenant B")
    u=User(login_id="ahmad",display_name="Ahmad",password_hash=hash_password("secret123"))
    supervisor=User(login_id="supervisor.1",display_name="Supervisor Satu",password_hash=hash_password("secret123"))
    db.add_all([ta,tb,u,supervisor]); db.flush(); m=Membership(tenant_id=ta.id,user_id=u.id,role=RoleCode.ADMIN); db.add_all([m,Membership(tenant_id=ta.id,user_id=supervisor.id,role=RoleCode.SUPERVISOR)]); db.flush(); db.add(ProjectScope(tenant_id=ta.id,membership_id=m.id,project_id="project-a")); db.commit()
    return ta,tb,u,m
