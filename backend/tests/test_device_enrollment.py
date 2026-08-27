import base64
from datetime import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import select

from app.models import DeviceBinding, DeviceStatus, Project, Worker
from app.security import hash_password

def b64(value: bytes) -> str: return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

def key_and_proof(challenge_b64: str):
    private=ec.generate_private_key(ec.SECP256R1()); numbers=private.public_key().public_numbers()
    jwk={"kty":"EC","crv":"P-256","x":b64(numbers.x.to_bytes(32,"big")),"y":b64(numbers.y.to_bytes(32,"big")),"ext":True}
    raw=base64.urlsafe_b64decode(challenge_b64+"="*((4-len(challenge_b64)%4)%4)); der=private.sign(raw,ec.ECDSA(hashes.SHA256())); r,s=decode_dss_signature(der)
    return jwk,b64(r.to_bytes(32,"big")+s.to_bytes(32,"big"))

def setup_worker(db,seeded):
    tenant,_,_,_=seeded; worker=Worker(tenant_id=tenant.id,code="EMP-001",name="Ahmad Demo",pin_hash=hash_password("246810")); db.add(worker); db.commit(); return tenant,worker

def worker_token(client,tenant,worker,pin="246810"):
    response=client.post("/api/v1/worker/auth/login",json={"tenant_code":tenant.code,"worker_code":worker.code,"pin":pin}); return response

def admin_headers(client,tenant):
    response=client.post("/api/v1/auth/login",json={"login_id":"ahmad","password":"secret123"}); return {"Authorization":f"Bearer {response.json()['data']['access_token']}","X-Tenant-ID":tenant.id}

def submit_enrollment(client,token,label="Android Ahmad"):
    headers={"Authorization":f"Bearer {token}"}; challenge=client.post("/api/v1/worker/device-enrollment/challenge",headers=headers).json()["data"]
    jwk,signature=key_and_proof(challenge["challenge"])
    response=client.post("/api/v1/worker/device-enrollment/requests",headers={**headers,"X-Device-Challenge":challenge["challenge"]},json={"challenge_id":challenge["challenge_id"],"public_key_jwk":jwk,"signature":signature,"device_label":label})
    return response,challenge,jwk,signature

def test_worker_wrong_pin_is_rejected(client,seeded,db):
    tenant,worker=setup_worker(db,seeded); assert worker_token(client,tenant,worker,"000000").status_code==401

def test_enrollment_approval_and_challenge_replay(client,seeded,db):
    tenant,worker=setup_worker(db,seeded); login=worker_token(client,tenant,worker); assert login.status_code==200; token=login.json()["data"]["access_token"]
    submitted,challenge,jwk,signature=submit_enrollment(client,token); assert submitted.status_code==200; enrollment_id=submitted.json()["data"]["enrollment_id"]
    replay=client.post("/api/v1/worker/device-enrollment/requests",headers={"Authorization":f"Bearer {token}","X-Device-Challenge":challenge["challenge"]},json={"challenge_id":challenge["challenge_id"],"public_key_jwk":jwk,"signature":signature,"device_label":"Replay"}); assert replay.status_code==409
    pending=client.get("/api/v1/device-enrollments/pending",headers=admin_headers(client,tenant)); assert pending.status_code==200; assert len(pending.json()["data"])==1
    approved=client.post(f"/api/v1/device-enrollments/{enrollment_id}/approve",headers=admin_headers(client,tenant),json={"reason":"HP diperiksa supervisor"}); assert approved.status_code==200
    binding=db.scalar(select(DeviceBinding).where(DeviceBinding.worker_id==worker.id)); assert binding.status==DeviceStatus.ACTIVE

def test_invalid_device_signature_is_rejected(client,seeded,db):
    tenant,worker=setup_worker(db,seeded); token=worker_token(client,tenant,worker).json()["data"]["access_token"]; headers={"Authorization":f"Bearer {token}"}
    challenge=client.post("/api/v1/worker/device-enrollment/challenge",headers=headers).json()["data"]; jwk,_=key_and_proof(challenge["challenge"])
    response=client.post("/api/v1/worker/device-enrollment/requests",headers={**headers,"X-Device-Challenge":challenge["challenge"]},json={"challenge_id":challenge["challenge_id"],"public_key_jwk":jwk,"signature":b64(b"0"*64),"device_label":"Perangkat Palsu"})
    assert response.status_code==422

def test_new_device_approval_revokes_previous_binding(client,seeded,db):
    tenant,worker=setup_worker(db,seeded); token=worker_token(client,tenant,worker).json()["data"]["access_token"]; admin=admin_headers(client,tenant)
    first,_,_,_=submit_enrollment(client,token,"Android Lama"); client.post(f"/api/v1/device-enrollments/{first.json()['data']['enrollment_id']}/approve",headers=admin,json={})
    second,_,_,_=submit_enrollment(client,token,"Android Baru"); result=client.post(f"/api/v1/device-enrollments/{second.json()['data']['enrollment_id']}/approve",headers=admin,json={}); assert result.status_code==200; assert result.json()["data"]["replaced_devices"]==1
    bindings=db.scalars(select(DeviceBinding).where(DeviceBinding.worker_id==worker.id)).all(); assert len(bindings)==2; assert sum(item.status==DeviceStatus.ACTIVE for item in bindings)==1

def make_key_signer():
    """Generate ONE ECDSA keypair; return (jwk, sign(challenge_b64)->signature)."""
    private=ec.generate_private_key(ec.SECP256R1()); numbers=private.public_key().public_numbers()
    jwk={"kty":"EC","crv":"P-256","x":b64(numbers.x.to_bytes(32,"big")),"y":b64(numbers.y.to_bytes(32,"big")),"ext":True}
    def sign(challenge_b64):
        raw=base64.urlsafe_b64decode(challenge_b64+"="*((4-len(challenge_b64)%4)%4))
        der=private.sign(raw,ec.ECDSA(hashes.SHA256())); r,s=decode_dss_signature(der)
        return b64(r.to_bytes(32,"big")+s.to_bytes(32,"big"))
    return jwk,sign

def _submit_with_key(client, token, jwk, sign, label):
    headers={"Authorization":f"Bearer {token}"}
    ch=client.post("/api/v1/worker/device-enrollment/challenge",headers=headers).json()["data"]
    resp=client.post("/api/v1/worker/device-enrollment/requests",headers={**headers,"X-Device-Challenge":ch["challenge"]},json={"challenge_id":ch["challenge_id"],"public_key_jwk":jwk,"signature":sign(ch["challenge"]),"device_label":label})
    return resp

def test_shared_device_across_workers_is_flagged(client,seeded,db):
    """D5 — 1 HP (kunci sama) didaftarkan 2 pekerja berbeda → flag risiko ke supervisor."""
    tenant,_,_,_=seeded
    wa=Worker(tenant_id=tenant.id,code="EMP-A",name="Ali",pin_hash=hash_password("246810")); db.add(wa); db.commit()
    wb=Worker(tenant_id=tenant.id,code="EMP-B",name="Budi",pin_hash=hash_password("246810")); db.add(wb); db.commit()
    admin=admin_headers(client,tenant)
    jwk,sign=make_key_signer()

    # A mendaftarkan HP ini (kunci K)
    token_a=worker_token(client,tenant,wa).json()["data"]["access_token"]
    ra=_submit_with_key(client,token_a,jwk,sign,"HP Titipan"); assert ra.status_code==200
    ea=ra.json()["data"]["enrollment_id"]
    client.post(f"/api/v1/device-enrollments/{ea}/approve",headers=admin,json={})

    # B mendaftarkan HP yang SAMA (kunci K identik → thumbprint sama)
    token_b=worker_token(client,tenant,wb).json()["data"]["access_token"]
    rb=_submit_with_key(client,token_b,jwk,sign,"HP Titipan"); assert rb.status_code==200
    eb=rb.json()["data"]["enrollment_id"]
    resp=client.post(f"/api/v1/device-enrollments/{eb}/approve",headers=admin,json={})
    assert resp.status_code==200
    body=resp.json()["data"]
    assert body.get("shared_device_warning"), "harus ada flag shared_device_warning"
    assert body["shared_device_warning"]["count"]==1
    assert body["shared_device_warning"]["other_worker_codes"]==["EMP-A"]
