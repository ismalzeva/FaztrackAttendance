"""
checkpoint_engine.py -- Generic Checkpoint Engine (M2B).

Reusable checkpoint validation for all Faztrack Attendance tenants.
Configuration-driven: tenant-specific behavior from CheckpointPolicy.

Handles:
1. Event-to-checkpoint mapping (config-driven)
2. Checkpoint validation against policy
3. Sequence awareness (expected/received/missing)
4. Missing checkpoint detection
5. Idempotent processing
6. TBC-safe behavior (CONFIG_INCOMPLETE, not silent PASS/FAIL)
7. Tenant isolation
"""
import json
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import (
    CheckpointPolicy,
    CheckpointEventMapping,
    CheckpointValidationResult,
    CheckpointValidationStatus,
    MissingCheckpointResult,
    CanonicalAttendanceEvent,
    CanonicalEventType,
    RosterAssignment,
    ShiftTemplate,
    WorkStatus,
    SiteStatusEnum,
    Tenant,
    Site,
)


# ─────────────────────────────────────────────────────────────
# EVENT-TO-CHECKPOINT MAPPING
# ─────────────────────────────────────────────────────────────

def resolve_checkpoint_type(
    db: Session,
    *,
    tenant_id: str,
    source: str,
    event_type: str,
    operating_date: date | None = None,
) -> str | None:
    """Map canonical event to checkpoint type via config.
    
    Uses CheckpointEventMapping table. Returns None if no mapping found.
    No silent discard -- caller handles unmapped events.
    """
    query = select(CheckpointEventMapping).where(
        CheckpointEventMapping.tenant_id == tenant_id,
        CheckpointEventMapping.source == source,
        CheckpointEventMapping.event_type == event_type,
        CheckpointEventMapping.enabled == True,
    )
    if operating_date:
        query = query.where(
            CheckpointEventMapping.effective_from <= operating_date,
        )
        query = query.where(
            (CheckpointEventMapping.effective_to.is_(None)) |
            (CheckpointEventMapping.effective_to >= operating_date)
        )
    
    mapping = db.scalar(query)
    if mapping:
        return mapping.checkpoint_type
    return None


# ─────────────────────────────────────────────────────────────
# CHECKPOINT POLICY LOOKUP
# ─────────────────────────────────────────────────────────────

def get_policy(
    db: Session,
    *,
    tenant_id: str,
    checkpoint_type: str,
    shift_id: str,
    operating_date: date,
) -> CheckpointPolicy | None:
    """Get checkpoint policy (any enabled state) for tenant/checkpoint/shift/date.
    
    Returns None if no policy found at all.
    """
    return db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == checkpoint_type,
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )


def get_active_policy(
    db: Session,
    *,
    tenant_id: str,
    checkpoint_type: str,
    shift_id: str,
    operating_date: date,
) -> CheckpointPolicy | None:
    """Get active (enabled) checkpoint policy for tenant/checkpoint/shift/date.
    
    Returns None if no enabled policy found (not an error -- checkpoint may not apply).
    """
    return db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == checkpoint_type,
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )


# ─────────────────────────────────────────────────────────────
# CHECKPOINT VALIDATION
# ─────────────────────────────────────────────────────────────

def validate_checkpoint(
    db: Session,
    *,
    canonical_event: CanonicalAttendanceEvent,
    checkpoint_type: str,
) -> CheckpointValidationResult:
    """Validate a canonical event against checkpoint policy.
    
    TBC-safe: if policy parameters are incomplete, returns CONFIG_INCOMPLETE
    or BLOCKED_POLICY_DECISION -- never silent PASS/FAIL.
    
    Idempotent: if result already exists for this canonical_event_id + checkpoint_type,
    returns existing result.
    """
    tenant_id = canonical_event.tenant_id
    operating_date = canonical_event.operating_date
    shift_id = canonical_event.shift_id
    
    # Idempotency check
    existing = db.scalar(
        select(CheckpointValidationResult).where(
            CheckpointValidationResult.canonical_event_id == canonical_event.id,
            CheckpointValidationResult.checkpoint_type == checkpoint_type,
        )
    )
    if existing:
        return existing
    
    # Get policy
    policy = None
    if shift_id:
        policy = get_active_policy(
            db,
            tenant_id=tenant_id,
            checkpoint_type=checkpoint_type,
            shift_id=shift_id,
            operating_date=operating_date,
        )
    
    # No policy -> CONFIG_INCOMPLETE
    if not policy:
        result = CheckpointValidationResult(
            tenant_id=tenant_id,
            canonical_event_id=canonical_event.id,
            employee_id=canonical_event.employee_id,
            checkpoint_type=checkpoint_type,
            operating_date=operating_date,
            shift_id=shift_id,
            policy_id=None,
            rule_version_id=None,
            validation_status=CheckpointValidationStatus.CONFIG_INCOMPLETE,
            detected_timestamp=canonical_event.local_timestamp,
            reason_code="NO_POLICY_FOUND",
            evidence_json=canonical_event.evidence_json,
        )
        db.add(result)
        db.flush()
        return result
    
    # Check if policy is TBC
    if policy.default_validation_behavior == "BLOCKED_POLICY_DECISION":
        result = CheckpointValidationResult(
            tenant_id=tenant_id,
            canonical_event_id=canonical_event.id,
            employee_id=canonical_event.employee_id,
            checkpoint_type=checkpoint_type,
            operating_date=operating_date,
            shift_id=shift_id,
            policy_id=policy.id,
            rule_version_id=policy.rule_version_id,
            validation_status=CheckpointValidationStatus.BLOCKED_POLICY_DECISION,
            detected_timestamp=canonical_event.local_timestamp,
            reason_code="POLICY_TBC",
            evidence_json=canonical_event.evidence_json,
        )
        db.add(result)
        db.flush()
        return result
    
    # Check if tolerance/window are configured
    # If window_start_offset_min and window_end_offset_min are both 0 AND
    # tolerance_min is None, this is likely a TBC configuration
    window_configured = (
        policy.window_start_offset_min != 0 or
        policy.window_end_offset_min != 0 or
        policy.tolerance_min is not None
    )
    
    # Validate against window if configured
    validation_status = CheckpointValidationStatus.PASS
    reason_code = "VALIDATED"
    
    if window_configured and shift_id:
        shift = db.get(ShiftTemplate, shift_id)
        if shift:
            # Compute expected window from shift start + offsets
            tz_name = _resolve_tenant_timezone(db, tenant_id, canonical_event.site_id)
            tz = ZoneInfo(tz_name)
            
            shift_start_dt = datetime.combine(operating_date, shift.start_time, tzinfo=tz)
            window_open = shift_start_dt + timedelta(minutes=policy.window_start_offset_min)
            window_close = shift_start_dt + timedelta(minutes=policy.window_end_offset_min)
            
            tolerance = timedelta(minutes=policy.tolerance_min or 0)
            
            event_dt = canonical_event.local_timestamp
            
            if event_dt < window_open - tolerance:
                validation_status = CheckpointValidationStatus.FAIL
                reason_code = "BEFORE_WINDOW"
            elif event_dt > window_close + tolerance:
                validation_status = CheckpointValidationStatus.FAIL
                reason_code = "AFTER_WINDOW"
    
    # Build evidence metadata
    evidence_meta = {}
    if canonical_event.latitude is not None:
        evidence_meta["latitude"] = canonical_event.latitude
    if canonical_event.longitude is not None:
        evidence_meta["longitude"] = canonical_event.longitude
    if canonical_event.accuracy_m is not None:
        evidence_meta["accuracy_m"] = canonical_event.accuracy_m
    if canonical_event.equipment_id:
        evidence_meta["equipment_id"] = canonical_event.equipment_id
    
    result = CheckpointValidationResult(
        tenant_id=tenant_id,
        canonical_event_id=canonical_event.id,
        employee_id=canonical_event.employee_id,
        checkpoint_type=checkpoint_type,
        operating_date=operating_date,
        shift_id=shift_id,
        policy_id=policy.id,
        rule_version_id=policy.rule_version_id,
        validation_status=validation_status,
        detected_timestamp=canonical_event.local_timestamp,
        reason_code=reason_code,
        evidence_json=json.dumps(evidence_meta) if evidence_meta else None,
    )
    db.add(result)
    db.flush()
    return result


# ─────────────────────────────────────────────────────────────
# SEQUENCE AWARENESS
# ─────────────────────────────────────────────────────────────

def get_expected_sequence(
    db: Session,
    *,
    tenant_id: str,
    shift_id: str,
    operating_date: date,
) -> list[CheckpointPolicy]:
    """Get expected checkpoint sequence for a shift on an operating date.
    
    Returns ordered list of enabled policies. Config-driven, not hard-coded.
    Lumin Park won't get mining sequence unless explicitly configured.
    """
    policies = db.scalars(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        ).order_by(CheckpointPolicy.sequence_order)
    ).all()
    return list(policies)


def detect_sequence_violations(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
) -> list[dict]:
    """Detect obvious invalid checkpoint order.
    
    Returns list of violations: [{checkpoint_type, expected_after, actual_order, ...}]
    Only detects clear out-of-order, not full disciplinary enforcement.
    """
    expected = get_expected_sequence(
        db, tenant_id=tenant_id, shift_id=shift_id, operating_date=operating_date
    )
    if len(expected) < 2:
        return []  # Need at least 2 checkpoints to detect order
    
    # Get received checkpoints for this employee/date
    received = db.scalars(
        select(CheckpointValidationResult).where(
            CheckpointValidationResult.tenant_id == tenant_id,
            CheckpointValidationResult.employee_id == employee_id,
            CheckpointValidationResult.operating_date == operating_date,
            CheckpointValidationResult.validation_status.in_([
                CheckpointValidationStatus.PASS,
                CheckpointValidationStatus.FAIL,
            ]),
        ).order_by(CheckpointValidationResult.detected_timestamp)
    ).all()
    
    if len(received) < 2:
        return []
    
    # Build expected order map
    expected_order = {p.checkpoint_type: i for i, p in enumerate(expected)}
    
    # Check if received order violates expected order
    violations = []
    received_types = [r.checkpoint_type for r in received]
    
    for i in range(len(received_types) - 1):
        current_type = received_types[i]
        next_type = received_types[i + 1]
        
        current_order = expected_order.get(current_type)
        next_order = expected_order.get(next_type)
        
        if current_order is not None and next_order is not None:
            if next_order < current_order:
                violations.append({
                    "checkpoint_type": next_type,
                    "expected_after": current_type,
                    "actual_order": f"{next_type} received after {current_type}",
                    "expected_order_index": next_order,
                    "actual_order_index": current_order,
                })
    
    return violations


# ─────────────────────────────────────────────────────────────
# MISSING CHECKPOINT DETECTION
# ─────────────────────────────────────────────────────────────

def detect_missing_checkpoints(
    db: Session,
    *,
    tenant_id: str,
    operating_date: date,
    shift_id: str | None = None,
) -> list[MissingCheckpointResult]:
    """Detect expected checkpoints that did not arrive.
    
    Only for WORK status employees with applicable checkpoint policies.
    REST/OFFSITE/LEAVE/SICK/TRAINING do NOT generate missing checkpoints.
    
    Returns list of MissingCheckpointResult created.
    """
    # Get roster assignments for this date where work_status = WORK
    query = select(RosterAssignment).where(
        RosterAssignment.tenant_id == tenant_id,
        RosterAssignment.operating_date == operating_date,
        RosterAssignment.work_status == WorkStatus.WORK,
        RosterAssignment.site_status == SiteStatusEnum.ONSITE,
        RosterAssignment.shift_id.isnot(None),
    )
    if shift_id:
        query = query.where(RosterAssignment.shift_id == shift_id)
    
    rosters = db.scalars(query).all()
    created = []
    
    for roster in rosters:
        if not roster.shift_id:
            continue
        
        # Get expected checkpoints for this shift
        expected = get_expected_sequence(
            db,
            tenant_id=tenant_id,
            shift_id=roster.shift_id,
            operating_date=operating_date,
        )
        
        # Get received checkpoints
        received_types = set()
        received_results = db.scalars(
            select(CheckpointValidationResult.checkpoint_type).where(
                CheckpointValidationResult.tenant_id == tenant_id,
                CheckpointValidationResult.employee_id == roster.employee_id,
                CheckpointValidationResult.operating_date == operating_date,
            )
        ).all()
        received_types = set(received_results)
        
        # Find missing
        for policy in expected:
            if policy.checkpoint_type not in received_types:
                # Idempotency check
                existing = db.scalar(
                    select(MissingCheckpointResult).where(
                        MissingCheckpointResult.tenant_id == tenant_id,
                        MissingCheckpointResult.employee_id == roster.employee_id,
                        MissingCheckpointResult.operating_date == operating_date,
                        MissingCheckpointResult.checkpoint_type == policy.checkpoint_type,
                    )
                )
                if existing:
                    created.append(existing)
                    continue
                
                # Compute expected window
                shift = db.get(ShiftTemplate, roster.shift_id)
                tz_name = _resolve_tenant_timezone(db, tenant_id, roster.site_id)
                tz = ZoneInfo(tz_name)
                
                window_start = None
                window_end = None
                if shift:
                    shift_start_dt = datetime.combine(operating_date, shift.start_time, tzinfo=tz)
                    window_start = shift_start_dt + timedelta(minutes=policy.window_start_offset_min)
                    window_end = shift_start_dt + timedelta(minutes=policy.window_end_offset_min)
                
                missing = MissingCheckpointResult(
                    tenant_id=tenant_id,
                    employee_id=roster.employee_id,
                    operating_date=operating_date,
                    shift_id=roster.shift_id,
                    checkpoint_type=policy.checkpoint_type,
                    policy_id=policy.id,
                    expected_window_start=window_start,
                    expected_window_end=window_end,
                    detection_status="MISSING",
                )
                db.add(missing)
                db.flush()
                created.append(missing)
    
    return created


# ─────────────────────────────────────────────────────────────
# FULL PIPELINE: CANONICAL EVENT -> CHECKPOINT
# ─────────────────────────────────────────────────────────────

def process_canonical_event(
    db: Session,
    *,
    canonical_event: CanonicalAttendanceEvent,
) -> tuple[CheckpointValidationResult | None, str]:
    """Process a canonical event through the checkpoint engine.
    
    Steps:
    1. Resolve checkpoint type from mapping
    2. Validate against policy
    3. Return result
    
    Returns: (result, status_string)
    status_string: "VALIDATED", "UNMAPPED", "NOT_APPLICABLE"
    """
    # 1. Resolve checkpoint type
    checkpoint_type = resolve_checkpoint_type(
        db,
        tenant_id=canonical_event.tenant_id,
        source=canonical_event.source,
        event_type=canonical_event.event_type.value
            if hasattr(canonical_event.event_type, 'value')
            else str(canonical_event.event_type),
        operating_date=canonical_event.operating_date,
    )
    
    if not checkpoint_type:
        return None, "UNMAPPED"
    
    # 2. Check if checkpoint is enabled for this shift
    if canonical_event.shift_id:
        policy = get_policy(
            db,
            tenant_id=canonical_event.tenant_id,
            checkpoint_type=checkpoint_type,
            shift_id=canonical_event.shift_id,
            operating_date=canonical_event.operating_date,
        )
        if policy and not policy.enabled:
            # Create NOT_APPLICABLE result
            result = CheckpointValidationResult(
                tenant_id=canonical_event.tenant_id,
                canonical_event_id=canonical_event.id,
                employee_id=canonical_event.employee_id,
                checkpoint_type=checkpoint_type,
                operating_date=canonical_event.operating_date,
                shift_id=canonical_event.shift_id,
                policy_id=policy.id,
                rule_version_id=policy.rule_version_id,
                validation_status=CheckpointValidationStatus.NOT_APPLICABLE,
                detected_timestamp=canonical_event.local_timestamp,
                reason_code="POLICY_DISABLED",
            )
            db.add(result)
            db.flush()
            return result, "NOT_APPLICABLE"
    
    # 3. Validate
    result = validate_checkpoint(
        db,
        canonical_event=canonical_event,
        checkpoint_type=checkpoint_type,
    )
    
    return result, "VALIDATED"


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _resolve_tenant_timezone(db: Session, tenant_id: str, site_id: str | None = None) -> str:
    """Resolve timezone: site > tenant > Asia/Jakarta."""
    if site_id:
        site = db.get(Site, site_id)
        if site and site.timezone:
            return site.timezone
    tenant = db.get(Tenant, tenant_id)
    if tenant and tenant.timezone:
        return tenant.timezone
    return "Asia/Jakarta"
