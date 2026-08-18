import json
from sqlalchemy.orm import Session
from app.models import AuditEvent
def record_audit(db: Session, *, tenant_id: str | None, actor_user_id: str | None, action: str, entity_type: str, entity_id: str | None, correlation_id: str, reason: str | None=None, payload: dict | None=None):
    db.add(AuditEvent(tenant_id=tenant_id,actor_user_id=actor_user_id,action=action,entity_type=entity_type,entity_id=entity_id,reason=reason,correlation_id=correlation_id,payload_json=json.dumps(payload,sort_keys=True) if payload else None))

