"""
rule_versioning.py — Snapshots tenant config into RuleVersion for audit trail.
"""
import json
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models import RuleVersion, RosterPolicy, CheckpointPolicy, ShiftTemplate, uid


def snapshot_rules(db: Session, tenant_id: str, version_label: str, effective_from: date) -> RuleVersion:
    """
    Capture current rule state (policies, checkpoints, shifts) into a RuleVersion row.
    Returns the created RuleVersion.
    """
    # Gather current config
    policies = db.query(RosterPolicy).filter(RosterPolicy.tenant_id == tenant_id).all()
    checkpoints = db.query(CheckpointPolicy).filter(CheckpointPolicy.tenant_id == tenant_id).all()
    shifts = db.query(ShiftTemplate).filter(ShiftTemplate.tenant_id == tenant_id).all()

    snapshot = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "policies": [
            {
                "policy_key": p.policy_key,
                "policy_value": p.policy_value,
                "data_type": p.data_type,
                "confirmation_status": p.confirmation_status,
            }
            for p in policies
        ],
        "checkpoints": [
            {
                "checkpoint_type": c.checkpoint_type,
                "shift_id": c.shift_id,
                "window_start_offset_min": c.window_start_offset_min,
                "window_end_offset_min": c.window_end_offset_min,
                "required_evidence": c.required_evidence,
                "severity": c.severity,
            }
            for c in checkpoints
        ],
        "shifts": [
            {
                "shift_code": s.shift_code,
                "shift_name": s.shift_name,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "break_start": s.break_start.isoformat(),
                "break_end": s.break_end.isoformat(),
                "handover_start": s.handover_start.isoformat(),
                "handover_end": s.handover_end.isoformat(),
                "crosses_midnight": s.crosses_midnight,
            }
            for s in shifts
        ],
    }

    rv = RuleVersion(
        id=uid(),
        tenant_id=tenant_id,
        version_label=version_label,
        effective_from=effective_from,
        config_snapshot_json=json.dumps(snapshot, indent=2),
    )
    db.add(rv)
    db.commit()
    db.refresh(rv)
    return rv


def get_active_rule_version(db: Session, tenant_id: str, on_date: date) -> RuleVersion | None:
    """Get the most recent RuleVersion effective on or before on_date."""
    return (
        db.query(RuleVersion)
        .filter(RuleVersion.tenant_id == tenant_id, RuleVersion.effective_from <= on_date)
        .order_by(RuleVersion.effective_from.desc())
        .first()
    )
