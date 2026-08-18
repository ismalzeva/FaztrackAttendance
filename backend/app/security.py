import base64, hashlib, hmac, os
from datetime import datetime, timedelta, timezone
import jwt
from app.config import get_settings

ALGORITHM="HS256"
def hash_password(password: str) -> str:
    salt=os.urandom(16); rounds=210_000
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,rounds)
    return f"pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme,rounds,salt,digest=encoded.split("$",3)
        if scheme!="pbkdf2_sha256": return False
        actual=hashlib.pbkdf2_hmac("sha256",password.encode(),base64.b64decode(salt),int(rounds))
        return hmac.compare_digest(actual,base64.b64decode(digest))
    except (ValueError,TypeError): return False
def create_access_token(user_id: str) -> str:
    settings=get_settings(); now=datetime.now(timezone.utc)
    return jwt.encode({"sub":user_id,"iat":now,"exp":now+timedelta(minutes=settings.access_token_minutes),"type":"access"},settings.jwt_secret,algorithm=ALGORITHM)
def create_worker_token(worker_id: str,tenant_id: str) -> str:
    settings=get_settings(); now=datetime.now(timezone.utc)
    return jwt.encode({"sub":worker_id,"tenant_id":tenant_id,"iat":now,"exp":now+timedelta(minutes=settings.access_token_minutes),"type":"worker"},settings.jwt_secret,algorithm=ALGORITHM)
def decode_access_token(token: str) -> str:
    payload=jwt.decode(token,get_settings().jwt_secret,algorithms=[ALGORITHM],options={"require":["sub","exp","iat"]})
    if payload.get("type")!="access": raise jwt.InvalidTokenError("invalid token type")
    return str(payload["sub"])
def decode_worker_token(token: str) -> tuple[str,str]:
    payload=jwt.decode(token,get_settings().jwt_secret,algorithms=[ALGORITHM],options={"require":["sub","tenant_id","exp","iat"]})
    if payload.get("type")!="worker": raise jwt.InvalidTokenError("invalid token type")
    return str(payload["sub"]),str(payload["tenant_id"])
