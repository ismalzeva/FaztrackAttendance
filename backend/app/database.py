from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings

class Base(DeclarativeBase):
    pass

def build_engine(url: str | None = None):
    target = url or get_settings().database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if target.startswith("sqlite") else {"pool_pre_ping": True}
    return create_engine(target, **kwargs)

engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

