from sqlalchemy import select

from app.models import AuditEvent


def token(client,seeded):
    response=client.post("/api/v1/auth/login",json={"login_id":"ahmad","password":"secret123"}); assert response.status_code==200; return response.json()["data"]["access_token"]
def test_login_and_me(client,seeded):
    auth=token(client,seeded); response=client.get("/api/v1/me",headers={"Authorization":f"Bearer {auth}"}); assert response.status_code==200; assert response.json()["data"]["display_name"]=="Ahmad"
def test_wrong_password_is_rejected(client,seeded):
    assert client.post("/api/v1/auth/login",json={"login_id":"ahmad","password":"wrongxx"}).status_code==401
def test_tenant_scope_allows_membership(client,seeded):
    ta,_,_,_=seeded; auth=token(client,seeded); response=client.get("/api/v1/context",headers={"Authorization":f"Bearer {auth}","X-Tenant-ID":ta.id}); assert response.status_code==200; assert response.json()["data"]["project_ids"]==["project-a"]
def test_cross_tenant_context_is_forbidden(client,seeded):
    _,tb,_,_=seeded; auth=token(client,seeded); response=client.get("/api/v1/context",headers={"Authorization":f"Bearer {auth}","X-Tenant-ID":tb.id}); assert response.status_code==403
def test_missing_token_is_unauthorized(client,seeded):
    assert client.get("/api/v1/me").status_code==401


def test_successful_login_is_audited_and_correlated(client,seeded,db):
    correlation_id="test-correlation-001"
    response=client.post(
        "/api/v1/auth/login",
        headers={"X-Correlation-ID":correlation_id},
        json={"login_id":"ahmad","password":"secret123"},
    )
    assert response.status_code==200
    assert response.headers["X-Correlation-ID"]==correlation_id
    assert response.json()["meta"]["correlation_id"]==correlation_id
    event=db.scalar(select(AuditEvent).where(AuditEvent.correlation_id==correlation_id))
    assert event is not None
    assert event.action=="AUTH_LOGIN"
