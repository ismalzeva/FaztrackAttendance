import base64
import json
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.models import Assignment, AttendanceStatus, DeviceBinding, DeviceEnrollment, DeviceStatus, EnrollmentStatus, Project, WorkSchedule, Worker
from app.security import hash_password

def b64(value: bytes) -> str: return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
def device_key():
    private=ec.generate_private_key(ec.SECP256R1()); n=private.public_key().public_numbers(); jwk={"kty":"EC","crv":"P-256","x":b64(n.x.to_bytes(32,"big")),"y":b64(n.y.to_bytes(32,"big"))}; return private,jwk
def sign(private,body):
    payload={key:body[key] for key in ("accuracy_m","captured_at_client","challenge","challenge_id","event_type","latitude","longitude","project_id")}; payload["accuracy_m"]=f"{payload['accuracy_m']:.1f}";payload["latitude"]=f"{payload['latitude']:.6f}";payload["longitude"]=f"{payload['longitude']:.6f}"
    raw=json.dumps(payload,separators=(",",":"),sort_keys=True).encode(); der=private.sign(raw,ec.ECDSA(hashes.SHA256()));r,s=decode_dss_signature(der);return b64(r.to_bytes(32,"big")+s.to_bytes(32,"big"))
def setup(db,seeded):
    tenant,_,_,_=seeded; today=datetime.now(ZoneInfo(tenant.timezone)).date(); project=Project(tenant_id=tenant.id,code="PRJ-TODAY",name="Proyek Hari Ini",latitude=0,longitude=0,geofence_radius_m=100,work_start=time(8),work_end=time(17));worker=Worker(tenant_id=tenant.id,code="EMP-TODAY",name="Pekerja Hari Ini",pin_hash=hash_password("246810"));db.add_all([project,worker]);db.flush();db.add_all([Assignment(tenant_id=tenant.id,worker_id=worker.id,project_id=project.id,work_date=today),WorkSchedule(tenant_id=tenant.id,worker_id=worker.id,work_date=today,start_time=time(8),end_time=time(17),is_working_day=True)]);private,jwk=device_key();enrollment=DeviceEnrollment(tenant_id=tenant.id,worker_id=worker.id,public_key_jwk=json.dumps(jwk),public_key_thumbprint="thumb",device_label="Test Android",status=EnrollmentStatus.APPROVED);db.add(enrollment);db.flush();db.add(DeviceBinding(tenant_id=tenant.id,worker_id=worker.id,enrollment_id=enrollment.id,public_key_jwk=json.dumps(jwk),public_key_thumbprint="thumb",device_label="Test Android",status=DeviceStatus.ACTIVE));db.commit();return tenant,worker,project,private
def token(client,tenant,worker): return client.post("/api/v1/worker/auth/login",json={"tenant_code":tenant.code,"worker_code":worker.code,"pin":"246810"}).json()["data"]["access_token"]
def submit(client,access,private,event_type,lat,lon,accuracy):
    headers={"Authorization":f"Bearer {access}"}; challenge=client.post("/api/v1/worker/attendance/challenge",headers=headers,json={"event_type":event_type});
    if challenge.status_code!=200:return challenge
    data=challenge.json()["data"];body={"challenge_id":data["challenge_id"],"challenge":data["challenge"],"event_type":event_type,"project_id":data["project"]["id"],"latitude":lat,"longitude":lon,"accuracy_m":accuracy,"captured_at_client":datetime.now(timezone.utc).isoformat()};body["signature"]=sign(private,body);return client.post("/api/v1/worker/attendance/events",headers=headers,json=body)

def test_valid_checkin_and_duplicate_are_blocked(client,seeded,db):
    tenant,worker,_,private=setup(db,seeded);access=token(client,tenant,worker);first=submit(client,access,private,"CHECK_IN",0,0,10);assert first.status_code==200;assert first.json()["data"]["status"]=="VALID";duplicate=client.post("/api/v1/worker/attendance/challenge",headers={"Authorization":f"Bearer {access}"},json={"event_type":"CHECK_IN"});assert duplicate.status_code==409
def test_checkout_requires_checkin(client,seeded,db):
    tenant,worker,_,private=setup(db,seeded);access=token(client,tenant,worker);assert submit(client,access,private,"CHECK_OUT",0,0,10).status_code==409
def test_uncertain_geofence_goes_to_review_then_supervisor_approves(client,seeded,db):
    tenant,worker,_,private=setup(db,seeded);access=token(client,tenant,worker);event=submit(client,access,private,"CHECK_IN",0.00108,0,50);assert event.status_code==200;assert event.json()["data"]["status"]=="REVIEW";login=client.post("/api/v1/auth/login",json={"login_id":"ahmad","password":"secret123"});headers={"Authorization":f"Bearer {login.json()['data']['access_token']}","X-Tenant-ID":tenant.id};queue=client.get("/api/v1/attendance/review",headers=headers);assert len(queue.json()["data"])==1;decision=client.post(f"/api/v1/attendance/{event.json()['data']['event_id']}/review",headers=headers,json={"approve":True,"reason":"Posisi terverifikasi di gerbang proyek"});assert decision.json()["data"]["status"]=="VALID"
def test_outside_geofence_is_rejected(client,seeded,db):
    tenant,worker,_,private=setup(db,seeded);event=submit(client,token(client,tenant,worker),private,"CHECK_IN",0.01,0,10);assert event.status_code==200;assert event.json()["data"]["status"]=="REJECTED";assert event.json()["data"]["reason_code"]=="OUTSIDE_GEOFENCE"
