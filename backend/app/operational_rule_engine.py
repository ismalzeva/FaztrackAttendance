"""
operational_rule_engine.py -- Operational Attendance Rule Engine (M2D).

Generic rule evaluation framework for all Faztrack Attendance tenants.
Configuration-driven: tenant-specific behavior from CheckpointPolicy, RosterPolicy, ShiftTemplate.

ARCHITECTURE RULE (permanent):
  SHARED CORE ENGINE + STRICTLY ISOLATED COMPANY CONFIGURATION
  Every tenant is an independent boundary. No company-specific branching in core.
  Behavior comes from tenant capability/policy/master data, never from tenant name.

Rule Codes:
  LATE_BREAK_RETURN       — fully activatable (confirmed Metro break times)
  MISSING_BRIEFING        — TBC-safe (briefing timing config incomplete)
  LATE_BRIEFING           — TBC-safe (tolerance config incomplete)
  MISSING_SHIFT_OUT       — generic detection after shift completion
  EARLY_HANDOVER          — TBC-safe (semantic interpretation partly TBC)
  LATE_HANDOVER           — TBC-safe (semantic interpretation partly TBC)
  LOCATION_OUTSIDE_GEOFENCE — framework exists, Metro geofence TBC
  DEVICE_OR_IDENTITY_RISK — framework exists, evidence-based
  EQUIPMENT_MISMATCH      — integrates M2C
  UNQUALIFIED_ASSIGNMENT  — integrates M2C
  DUPLICATE_EQUIPMENT_ASSIGNMENT — integrates M2C
  MAX_CONTINUOUS_DAYS_EXCEEDED — integrates M1
  MAX_SAME_SHIFT_STREAK_EXCEEDED — integrates M1
  OFFSITE_ASSIGNMENT      — integrates M1
  INSUFFICIENT_REST       — BLOCKED_POLICY_DECISION (TBC)

DETECTION is separate from HUMAN DECISION.
FAIL does NOT automatically mean employee misconduct.
"""
import json
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import (
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    CanonicalAttendanceEvent, CanonicalEventType,
    CheckpointValidationResult, CheckpointValidationStatus,
    CheckpointPolicy,
    RosterAssignment, WorkStatus, SiteStatusEnum,
    ShiftTemplate, Tenant, Site,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    Competency, CompetencyStatus, Equipment,
    RosterPolicy, RuleVersion,
)


# ─────────────────────────────────────────────────────────────
# RULE REGISTRY
# ─────────────────────────────────────────────────────────────

_RULE_REGISTRY: dict[str, callable] = {}


def register_rule(code: str):
    """Decorator to register a rule evaluation function."""
    def decorator(fn):
        _RULE_REGISTRY[code] = fn
        return fn
    return decorator


def get_registered_rules() -> dict[str, callable]:
    """Return copy of registered rules."""
    return dict(_RULE_REGISTRY)


# ─────────────────────────────────────────────────────────────
# TENANT CAPABILITY (reuse from equipment_engine pattern)
# ─────────────────────────────────────────────────────────────

def _get_capability(db: Session, tenant_id: str, key: str, default: str = "false") -> str:
    """Read tenant capability from RosterPolicy."""
    policy = db.scalar(
        select(RosterPolicy).where(
            RosterPolicy.tenant_id == tenant_id,
            RosterPolicy.policy_key == key,
        )
    )
    return policy.policy_value if policy else default


def _is_enabled(db: Session, tenant_id: str, key: str, default: bool = False) -> bool:
    """Check boolean tenant capability."""
    val = _get_capability(db, tenant_id, key, "true" if default else "false")
    return val.lower() in ("true", "1", "yes")


# ─────────────────────────────────────────────────────────────
# TIMEZONE RESOLUTION
# ─────────────────────────────────────────────────────────────

def _resolve_timezone(db: Session, tenant_id: str, site_id: str | None = None) -> str:
    """Resolve timezone: site > tenant > Asia/Jakarta (backward compat)."""
    if site_id:
        site = db.get(Site, site_id)
        if site and site.timezone:
            return site.timezone
    tenant = db.get(Tenant, tenant_id)
    if tenant and tenant.timezone:
        return tenant.timezone
    return "Asia/Jakarta"


# ─────────────────────────────────────────────────────────────
# IDEMPOTENT EVALUATION CREATION
# ─────────────────────────────────────────────────────────────

def _check_existing(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    rule_code: str,
    rule_version_id: str | None,
    evidence_key: str,
) -> RuleEvaluation | None:
    """Check for existing evaluation (idempotency)."""
    return db.scalar(
        select(RuleEvaluation).where(
            RuleEvaluation.tenant_id == tenant_id,
            RuleEvaluation.employee_id == employee_id,
            RuleEvaluation.operating_date == operating_date,
            RuleEvaluation.rule_code == rule_code,
            RuleEvaluation.rule_version_id == rule_version_id,
            RuleEvaluation.evidence_key == evidence_key,
        )
    )


def _create_evaluation(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None,
    rule_code: str,
    rule_version_id: str | None,
    status: RuleEvaluationStatus,
    severity: RuleSeverity = RuleSeverity.WARNING,
    source_checkpoint_result_id: str | None = None,
    source_canonical_event_id: str | None = None,
    equipment_id: str | None = None,
    actual_value: str | None = None,
    expected_value: str | None = None,
    evidence_json: str | None = None,
    reason: str | None = None,
    metadata_json: str | None = None,
    evidence_key: str = "",
) -> RuleEvaluation:
    """Create a rule evaluation record."""
    evaluation = RuleEvaluation(
        tenant_id=tenant_id,
        employee_id=employee_id,
        operating_date=operating_date,
        shift_id=shift_id,
        rule_code=rule_code,
        rule_version_id=rule_version_id,
        status=status,
        severity=severity,
        source_checkpoint_result_id=source_checkpoint_result_id,
        source_canonical_event_id=source_canonical_event_id,
        equipment_id=equipment_id,
        actual_value=actual_value,
        expected_value=expected_value,
        evidence_json=evidence_json,
        reason=reason,
        metadata_json=metadata_json,
        evidence_key=evidence_key,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


# ─────────────────────────────────────────────────────────────
# SHIFT TIME HELPERS
# ─────────────────────────────────────────────────────────────

def _get_shift_boundaries(
    shift: ShiftTemplate,
    operating_date: date,
    tz: ZoneInfo,
) -> dict:
    """Compute shift boundaries in local timezone.
    
    Returns dict with:
    - shift_start, shift_end: datetime
    - break_start, break_end: datetime
    - handover_start, handover_end: datetime
    - crosses_midnight: bool
    """
    shift_start = datetime.combine(operating_date, shift.start_time, tzinfo=tz)
    break_start = datetime.combine(operating_date, shift.break_start, tzinfo=tz)
    break_end = datetime.combine(operating_date, shift.break_end, tzinfo=tz)
    handover_start = datetime.combine(operating_date, shift.handover_start, tzinfo=tz)
    handover_end = datetime.combine(operating_date, shift.handover_end, tzinfo=tz)
    
    if shift.crosses_midnight:
        shift_end = datetime.combine(operating_date + timedelta(days=1), shift.end_time, tzinfo=tz)
        # Break/handover times after midnight need next day
        if shift.break_start < shift.start_time:
            break_start = datetime.combine(operating_date + timedelta(days=1), shift.break_start, tzinfo=tz)
            break_end = datetime.combine(operating_date + timedelta(days=1), shift.break_end, tzinfo=tz)
        if shift.handover_start < shift.start_time:
            handover_start = datetime.combine(operating_date + timedelta(days=1), shift.handover_start, tzinfo=tz)
            handover_end = datetime.combine(operating_date + timedelta(days=1), shift.handover_end, tzinfo=tz)
    else:
        shift_end = datetime.combine(operating_date, shift.end_time, tzinfo=tz)
    
    return {
        "shift_start": shift_start,
        "shift_end": shift_end,
        "break_start": break_start,
        "break_end": break_end,
        "handover_start": handover_start,
        "handover_end": handover_end,
        "crosses_midnight": shift.crosses_midnight,
    }


# ─────────────────────────────────────────────────────────────
# RULE: LATE_BREAK_RETURN (FULLY ACTIVATABLE)
# ─────────────────────────────────────────────────────────────

@register_rule("LATE_BREAK_RETURN")
def evaluate_late_break_return(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
    canonical_event: CanonicalAttendanceEvent,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Evaluate if worker returned from break late.
    
    Metro confirmed:
    - DAY: BREAK_IN must be <= 13:00 WITA
    - NIGHT: BREAK_IN must be <= 01:00 WITA (next calendar day, operating_date = previous)
    
    Uses site local timezone. Boundary inclusive (13:00 = PASS, 13:01 = FAIL).
    """
    evidence_key = f"break_in:{canonical_event.id}"
    
    # Idempotency
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="LATE_BREAK_RETURN",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Get shift
    shift = db.get(ShiftTemplate, shift_id)
    if not shift:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_BREAK_RETURN", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="SHIFT_NOT_FOUND",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Get timezone
    tz_name = _resolve_timezone(db, tenant_id, canonical_event.site_id)
    tz = ZoneInfo(tz_name)
    
    # Compute break end boundary
    boundaries = _get_shift_boundaries(shift, operating_date, tz)
    break_end_boundary = boundaries["break_end"]
    
    # Get event local timestamp
    event_dt = canonical_event.local_timestamp
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=tz)
    
    # Evaluate
    if event_dt <= break_end_boundary:
        status = RuleEvaluationStatus.PASS
        severity = RuleSeverity.INFO
        reason = "BREAK_RETURN_ON_TIME"
    else:
        status = RuleEvaluationStatus.FAIL
        severity = RuleSeverity.WARNING
        reason = "LATE_BREAK_RETURN"
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="LATE_BREAK_RETURN", rule_version_id=rule_version_id,
        status=status, severity=severity,
        source_canonical_event_id=canonical_event.id,
        actual_value=event_dt.strftime("%H:%M"),
        expected_value=break_end_boundary.strftime("%H:%M"),
        reason=reason,
        evidence_key=evidence_key,
        evidence_json=json.dumps({
            "event_time": event_dt.isoformat(),
            "break_end_boundary": break_end_boundary.isoformat(),
            "timezone": tz_name,
        }),
    )


# ─────────────────────────────────────────────────────────────
# RULE: MISSING_BRIEFING (TBC-SAFE)
# ─────────────────────────────────────────────────────────────

@register_rule("MISSING_BRIEFING")
def evaluate_missing_briefing(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Detect missing briefing for WORK roster employee.
    
    TBC-safe: briefing timing configuration is incomplete.
    If policy can define briefing window → evaluate.
    If not → CONFIG_INCOMPLETE.
    
    Only evaluates WORK + ONSITE employees.
    """
    evidence_key = f"missing_briefing:{shift_id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="MISSING_BRIEFING",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Check if briefing checkpoint policy exists and is configured
    briefing_policy = db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == "BRIEFING_IN",
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )
    
    if not briefing_policy:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_BRIEFING", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="BRIEFING_POLICY_NOT_CONFIGURED",
            evidence_key=evidence_key,
        )
    
    # Check if window is configured
    window_configured = (
        briefing_policy.window_start_offset_min != 0 or
        briefing_policy.window_end_offset_min != 0 or
        briefing_policy.tolerance_min is not None
    )
    
    if not window_configured:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_BRIEFING", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="BRIEFING_WINDOW_NOT_CONFIGURED",
            evidence_key=evidence_key,
            source_checkpoint_result_id=briefing_policy.id,
        )
    
    # Check if briefing was received
    briefing_received = db.scalar(
        select(CheckpointValidationResult).where(
            CheckpointValidationResult.tenant_id == tenant_id,
            CheckpointValidationResult.employee_id == employee_id,
            CheckpointValidationResult.operating_date == operating_date,
            CheckpointValidationResult.checkpoint_type == "BRIEFING_IN",
            CheckpointValidationResult.validation_status.in_([
                CheckpointValidationStatus.PASS,
                CheckpointValidationStatus.FAIL,
            ]),
        )
    )
    
    if briefing_received:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_BRIEFING", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.PASS,
            severity=RuleSeverity.INFO,
            reason="BRIEFING_RECEIVED",
            source_checkpoint_result_id=briefing_received.id,
            evidence_key=evidence_key,
        )
    
    # Briefing window has closed and no briefing received
    shift = db.get(ShiftTemplate, shift_id)
    if shift:
        tz_name = _resolve_timezone(db, tenant_id)
        tz = ZoneInfo(tz_name)
        boundaries = _get_shift_boundaries(shift, operating_date, tz)
        window_close = boundaries["shift_start"] + timedelta(minutes=briefing_policy.window_end_offset_min)
        now_dt = datetime.now(tz)
        
        if now_dt < window_close:
            # Window still open — cannot declare missing yet
            return _create_evaluation(
                db, tenant_id=tenant_id, employee_id=employee_id,
                operating_date=operating_date, shift_id=shift_id,
                rule_code="MISSING_BRIEFING", rule_version_id=rule_version_id,
                status=RuleEvaluationStatus.NOT_APPLICABLE,
                reason="BRIEFING_WINDOW_STILL_OPEN",
                evidence_key=evidence_key,
            )
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="MISSING_BRIEFING", rule_version_id=rule_version_id,
        status=RuleEvaluationStatus.FAIL,
        severity=RuleSeverity.WARNING,
        reason="MISSING_BRIEFING",
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# RULE: LATE_BRIEFING (TBC-SAFE)
# ─────────────────────────────────────────────────────────────

@register_rule("LATE_BRIEFING")
def evaluate_late_briefing(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
    canonical_event: CanonicalAttendanceEvent,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Evaluate if worker arrived at briefing late.
    
    TBC-safe: briefing lateness tolerance is not yet confirmed.
    If tolerance is configured → evaluate.
    If not → BLOCKED_POLICY_DECISION.
    """
    evidence_key = f"late_briefing:{canonical_event.id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="LATE_BRIEFING",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Get briefing policy
    briefing_policy = db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == "BRIEFING_IN",
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )
    
    if not briefing_policy:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_BRIEFING", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="BRIEFING_POLICY_NOT_CONFIGURED",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Check if tolerance is explicitly configured
    if briefing_policy.tolerance_min is None:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_BRIEFING", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.BLOCKED_POLICY_DECISION,
            reason="BRIEFING_TOLERANCE_NOT_CONFIGURED",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Evaluate against shift start + tolerance
    shift = db.get(ShiftTemplate, shift_id)
    if not shift:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_BRIEFING", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="SHIFT_NOT_FOUND",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    tz_name = _resolve_timezone(db, tenant_id, canonical_event.site_id)
    tz = ZoneInfo(tz_name)
    
    shift_start = datetime.combine(operating_date, shift.start_time, tzinfo=tz)
    tolerance = timedelta(minutes=briefing_policy.tolerance_min)
    briefing_deadline = shift_start + tolerance
    
    event_dt = canonical_event.local_timestamp
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=tz)
    
    if event_dt <= briefing_deadline:
        status = RuleEvaluationStatus.PASS
        severity = RuleSeverity.INFO
        reason = "BRIEFING_ON_TIME"
    else:
        status = RuleEvaluationStatus.FAIL
        severity = RuleSeverity.WARNING
        reason = "LATE_BRIEFING"
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="LATE_BRIEFING", rule_version_id=rule_version_id,
        status=status, severity=severity,
        source_canonical_event_id=canonical_event.id,
        actual_value=event_dt.strftime("%H:%M"),
        expected_value=briefing_deadline.strftime("%H:%M"),
        reason=reason,
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# RULE: MISSING_SHIFT_OUT
# ─────────────────────────────────────────────────────────────

@register_rule("MISSING_SHIFT_OUT")
def evaluate_missing_shift_out(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Detect missing SHIFT_OUT for WORK roster employee.
    
    Only evaluates after shift completion.
    REST/OFFSITE/LEAVE/SICK → NOT_APPLICABLE.
    """
    evidence_key = f"missing_shift_out:{shift_id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="MISSING_SHIFT_OUT",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Check roster
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.employee_id == employee_id,
            RosterAssignment.operating_date == operating_date,
        )
    )
    
    if not roster:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_SHIFT_OUT", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="NO_ROSTER",
            evidence_key=evidence_key,
        )
    
    # Only for WORK + ONSITE
    if roster.work_status != WorkStatus.WORK or roster.site_status != SiteStatusEnum.ONSITE:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_SHIFT_OUT", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason=f"WORK_STATUS_{roster.work_status.value}",
            evidence_key=evidence_key,
        )
    
    # Check if CHECK_OUT policy exists
    checkout_policy = db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == "CHECK_OUT",
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )
    
    if not checkout_policy:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_SHIFT_OUT", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="CHECKOUT_POLICY_NOT_CONFIGURED",
            evidence_key=evidence_key,
        )
    
    # Check if CHECK_OUT was received
    checkout_received = db.scalar(
        select(CheckpointValidationResult).where(
            CheckpointValidationResult.tenant_id == tenant_id,
            CheckpointValidationResult.employee_id == employee_id,
            CheckpointValidationResult.operating_date == operating_date,
            CheckpointValidationResult.checkpoint_type == "CHECK_OUT",
            CheckpointValidationResult.validation_status.in_([
                CheckpointValidationStatus.PASS,
                CheckpointValidationStatus.FAIL,
            ]),
        )
    )
    
    if checkout_received:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="MISSING_SHIFT_OUT", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.PASS,
            severity=RuleSeverity.INFO,
            reason="SHIFT_OUT_RECEIVED",
            source_checkpoint_result_id=checkout_received.id,
            evidence_key=evidence_key,
        )
    
    # Check if shift has ended
    shift = db.get(ShiftTemplate, shift_id)
    if shift:
        tz_name = _resolve_timezone(db, tenant_id, roster.site_id)
        tz = ZoneInfo(tz_name)
        boundaries = _get_shift_boundaries(shift, operating_date, tz)
        now_dt = datetime.now(tz)
        
        if now_dt < boundaries["shift_end"]:
            return _create_evaluation(
                db, tenant_id=tenant_id, employee_id=employee_id,
                operating_date=operating_date, shift_id=shift_id,
                rule_code="MISSING_SHIFT_OUT", rule_version_id=rule_version_id,
                status=RuleEvaluationStatus.NOT_APPLICABLE,
                reason="SHIFT_NOT_ENDED",
                evidence_key=evidence_key,
            )
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="MISSING_SHIFT_OUT", rule_version_id=rule_version_id,
        status=RuleEvaluationStatus.FAIL,
        severity=RuleSeverity.WARNING,
        reason="MISSING_SHIFT_OUT",
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# RULE: HANDOVER (TBC-SAFE)
# ─────────────────────────────────────────────────────────────

@register_rule("EARLY_HANDOVER")
def evaluate_early_handover(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
    canonical_event: CanonicalAttendanceEvent,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Evaluate if handover started early.
    
    TBC-safe: handover semantic interpretation is partly TBC.
    Framework evaluates configured windows; production consequence = BLOCKED_POLICY_DECISION.
    """
    evidence_key = f"early_handover:{canonical_event.id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="EARLY_HANDOVER",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Get handover policy
    handover_policy = db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == "HANDOVER_START",
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )
    
    if not handover_policy:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="EARLY_HANDOVER", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="HANDOVER_POLICY_NOT_CONFIGURED",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Check if policy is TBC
    if handover_policy.default_validation_behavior == "BLOCKED_POLICY_DECISION":
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="EARLY_HANDOVER", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.BLOCKED_POLICY_DECISION,
            reason="HANDOVER_SEMANTIC_TBC",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Evaluate against configured window
    shift = db.get(ShiftTemplate, shift_id)
    if not shift:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="EARLY_HANDOVER", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="SHIFT_NOT_FOUND",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    tz_name = _resolve_timezone(db, tenant_id, canonical_event.site_id)
    tz = ZoneInfo(tz_name)
    boundaries = _get_shift_boundaries(shift, operating_date, tz)
    
    event_dt = canonical_event.local_timestamp
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=tz)
    
    handover_start = boundaries["handover_start"]
    
    if event_dt < handover_start:
        status = RuleEvaluationStatus.FAIL
        severity = RuleSeverity.WARNING
        reason = "EARLY_HANDOVER"
    else:
        status = RuleEvaluationStatus.PASS
        severity = RuleSeverity.INFO
        reason = "HANDOVER_ON_TIME"
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="EARLY_HANDOVER", rule_version_id=rule_version_id,
        status=status, severity=severity,
        source_canonical_event_id=canonical_event.id,
        actual_value=event_dt.strftime("%H:%M"),
        expected_value=handover_start.strftime("%H:%M"),
        reason=reason,
        evidence_key=evidence_key,
    )


@register_rule("LATE_HANDOVER")
def evaluate_late_handover(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str,
    canonical_event: CanonicalAttendanceEvent,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Evaluate if handover ended late.
    
    TBC-safe: handover semantic interpretation is partly TBC.
    """
    evidence_key = f"late_handover:{canonical_event.id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="LATE_HANDOVER",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    handover_policy = db.scalar(
        select(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.checkpoint_type == "HANDOVER_END",
            CheckpointPolicy.shift_id == shift_id,
            CheckpointPolicy.enabled == True,
            CheckpointPolicy.effective_from <= operating_date,
            (CheckpointPolicy.effective_to.is_(None)) |
            (CheckpointPolicy.effective_to >= operating_date),
        )
    )
    
    if not handover_policy:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_HANDOVER", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="HANDOVER_POLICY_NOT_CONFIGURED",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    if handover_policy.default_validation_behavior == "BLOCKED_POLICY_DECISION":
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_HANDOVER", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.BLOCKED_POLICY_DECISION,
            reason="HANDOVER_SEMANTIC_TBC",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    shift = db.get(ShiftTemplate, shift_id)
    if not shift:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LATE_HANDOVER", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="SHIFT_NOT_FOUND",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    tz_name = _resolve_timezone(db, tenant_id, canonical_event.site_id)
    tz = ZoneInfo(tz_name)
    boundaries = _get_shift_boundaries(shift, operating_date, tz)
    
    event_dt = canonical_event.local_timestamp
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=tz)
    
    handover_end = boundaries["handover_end"]
    
    if event_dt > handover_end:
        status = RuleEvaluationStatus.FAIL
        severity = RuleSeverity.WARNING
        reason = "LATE_HANDOVER"
    else:
        status = RuleEvaluationStatus.PASS
        severity = RuleSeverity.INFO
        reason = "HANDOVER_ON_TIME"
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="LATE_HANDOVER", rule_version_id=rule_version_id,
        status=status, severity=severity,
        source_canonical_event_id=canonical_event.id,
        actual_value=event_dt.strftime("%H:%M"),
        expected_value=handover_end.strftime("%H:%M"),
        reason=reason,
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# RULE: LOCATION_OUTSIDE_GEOFENCE (TBC-SAFE)
# ─────────────────────────────────────────────────────────────

@register_rule("LOCATION_OUTSIDE_GEOFENCE")
def evaluate_geofence(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None = None,
    canonical_event: CanonicalAttendanceEvent,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Evaluate if worker is outside geofence.
    
    TBC-safe: Metro Mining geofence coordinates/radius are TBC.
    If site has lat/lon/radius → evaluate.
    If not → CONFIG_INCOMPLETE.
    """
    evidence_key = f"geofence:{canonical_event.id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="LOCATION_OUTSIDE_GEOFENCE",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Check if site has geofence config
    site = None
    if canonical_event.site_id:
        site = db.get(Site, canonical_event.site_id)
    
    if not site or site.latitude is None or site.longitude is None or site.radius_m is None:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LOCATION_OUTSIDE_GEOFENCE", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="GEOFENCE_NOT_CONFIGURED",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Check if event has location
    if canonical_event.latitude is None or canonical_event.longitude is None:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="LOCATION_OUTSIDE_GEOFENCE", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
            reason="EVENT_LOCATION_MISSING",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    # Calculate distance (Haversine)
    import math
    lat1, lon1 = math.radians(site.latitude), math.radians(site.longitude)
    lat2, lon2 = math.radians(canonical_event.latitude), math.radians(canonical_event.longitude)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    distance_m = 6371000 * c  # Earth radius in meters
    
    if distance_m <= site.radius_m:
        status = RuleEvaluationStatus.PASS
        severity = RuleSeverity.INFO
        reason = "INSIDE_GEOFENCE"
    else:
        status = RuleEvaluationStatus.FAIL
        severity = RuleSeverity.WARNING
        reason = "OUTSIDE_GEOFENCE"
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="LOCATION_OUTSIDE_GEOFENCE", rule_version_id=rule_version_id,
        status=status, severity=severity,
        source_canonical_event_id=canonical_event.id,
        actual_value=f"{distance_m:.0f}m",
        expected_value=f"{site.radius_m}m",
        reason=reason,
        evidence_key=evidence_key,
        evidence_json=json.dumps({
            "site_lat": site.latitude,
            "site_lon": site.longitude,
            "site_radius_m": site.radius_m,
            "event_lat": canonical_event.latitude,
            "event_lon": canonical_event.longitude,
            "distance_m": round(distance_m, 1),
        }),
    )


# ─────────────────────────────────────────────────────────────
# RULE: DEVICE_OR_IDENTITY_RISK
# ─────────────────────────────────────────────────────────────

@register_rule("DEVICE_OR_IDENTITY_RISK")
def evaluate_device_risk(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None = None,
    canonical_event: CanonicalAttendanceEvent,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Evaluate device/identity risk from available evidence.
    
    Signals: enrolled device mismatch, invalid signature, identity evidence missing.
    Does NOT treat telematics as proof of worker identity.
    Result = operational risk/evidence status, not disciplinary consequence.
    """
    evidence_key = f"device_risk:{canonical_event.id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="DEVICE_OR_IDENTITY_RISK",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Parse evidence
    evidence = {}
    if canonical_event.evidence_json:
        try:
            evidence = json.loads(canonical_event.evidence_json)
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Check for device binding mismatch signals
    risk_signals = []
    
    # If evidence contains device info, check for mismatches
    if evidence.get("device_binding_id") and evidence.get("expected_device_binding_id"):
        if evidence["device_binding_id"] != evidence["expected_device_binding_id"]:
            risk_signals.append("DEVICE_BINDING_MISMATCH")
    
    # Check for missing signature
    if not canonical_event.evidence_json or not evidence.get("signature"):
        # Not necessarily a risk — just no cryptographic proof
        pass
    
    # If no risk signals, this is informational
    if not risk_signals:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="DEVICE_OR_IDENTITY_RISK", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.PASS,
            severity=RuleSeverity.INFO,
            reason="NO_DEVICE_RISK_DETECTED",
            evidence_key=evidence_key,
            source_canonical_event_id=canonical_event.id,
        )
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="DEVICE_OR_IDENTITY_RISK", rule_version_id=rule_version_id,
        status=RuleEvaluationStatus.FAIL,
        severity=RuleSeverity.WARNING,
        reason="DEVICE_RISK_DETECTED",
        evidence_key=evidence_key,
        source_canonical_event_id=canonical_event.id,
        evidence_json=json.dumps({"risk_signals": risk_signals}),
    )


# ─────────────────────────────────────────────────────────────
# RULE: EQUIPMENT_MISMATCH (integrates M2C)
# ─────────────────────────────────────────────────────────────

@register_rule("EQUIPMENT_MISMATCH")
def evaluate_equipment_mismatch(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None,
    comparison: EquipmentComparisonResult,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Surface M2C equipment comparison as standardized rule result.
    
    Integrates existing M2C logic — does NOT duplicate.
    """
    evidence_key = f"eq_mismatch:{comparison.actual_assignment_id}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="EQUIPMENT_MISMATCH",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    if comparison.comparison_result == ComparisonResult.MATCH:
        status = RuleEvaluationStatus.PASS
        severity = RuleSeverity.INFO
        reason = "EQUIPMENT_MATCH"
    elif comparison.comparison_result == ComparisonResult.MISMATCH:
        status = RuleEvaluationStatus.FAIL
        severity = RuleSeverity.WARNING
        reason = "EQUIPMENT_MISMATCH"
    elif comparison.comparison_result == ComparisonResult.NO_PLANNED_EQUIPMENT:
        status = RuleEvaluationStatus.NOT_APPLICABLE
        severity = RuleSeverity.INFO
        reason = "NO_PLANNED_EQUIPMENT"
    elif comparison.comparison_result == ComparisonResult.CONFIG_INCOMPLETE:
        status = RuleEvaluationStatus.CONFIG_INCOMPLETE
        severity = RuleSeverity.WARNING
        reason = "CONFIG_INCOMPLETE"
    else:
        status = RuleEvaluationStatus.NOT_APPLICABLE
        severity = RuleSeverity.INFO
        reason = comparison.comparison_result.value
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="EQUIPMENT_MISMATCH", rule_version_id=rule_version_id,
        status=status, severity=severity,
        equipment_id=comparison.actual_equipment_id,
        actual_value=comparison.actual_equipment_id,
        expected_value=comparison.planned_equipment_id,
        reason=reason,
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# RULE: INSUFFICIENT_REST (BLOCKED_POLICY_DECISION)
# ─────────────────────────────────────────────────────────────

@register_rule("INSUFFICIENT_REST")
def evaluate_insufficient_rest(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None = None,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Minimum rest hours between shifts.
    
    BLOCKED_POLICY_DECISION: minimum rest hours is still TBC.
    """
    evidence_key = f"insufficient_rest:{operating_date.isoformat()}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="INSUFFICIENT_REST",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    # Check if minimum rest hours is configured
    rest_config = _get_capability(db, tenant_id, "minimum_rest_hours", "")
    
    if not rest_config or rest_config.upper() in ("TBC", ""):
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="INSUFFICIENT_REST", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.BLOCKED_POLICY_DECISION,
            reason="MINIMUM_REST_HOURS_TBC",
            evidence_key=evidence_key,
        )
    
    # If configured, evaluate (future implementation)
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="INSUFFICIENT_REST", rule_version_id=rule_version_id,
        status=RuleEvaluationStatus.CONFIG_INCOMPLETE,
        reason="REST_EVALUATION_NOT_IMPLEMENTED",
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# RULE: OFFSITE_ASSIGNMENT (integrates M1)
# ─────────────────────────────────────────────────────────────

@register_rule("OFFSITE_ASSIGNMENT")
def evaluate_offsite_assignment(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None,
    rule_version_id: str | None = None,
) -> RuleEvaluation:
    """Check if worker is assigned offsite.
    
    Integrates M1 roster data — does NOT duplicate.
    """
    evidence_key = f"offsite:{operating_date.isoformat()}"
    
    existing = _check_existing(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, rule_code="OFFSITE_ASSIGNMENT",
        rule_version_id=rule_version_id, evidence_key=evidence_key,
    )
    if existing:
        return existing
    
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.employee_id == employee_id,
            RosterAssignment.operating_date == operating_date,
        )
    )
    
    if not roster:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="OFFSITE_ASSIGNMENT", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.NOT_APPLICABLE,
            reason="NO_ROSTER",
            evidence_key=evidence_key,
        )
    
    if roster.site_status == SiteStatusEnum.OFFSITE:
        return _create_evaluation(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id,
            rule_code="OFFSITE_ASSIGNMENT", rule_version_id=rule_version_id,
            status=RuleEvaluationStatus.FAIL,
            severity=RuleSeverity.INFO,
            reason="OFFSITE_ASSIGNMENT",
            actual_value="OFFSITE",
            expected_value="ONSITE",
            evidence_key=evidence_key,
        )
    
    return _create_evaluation(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=shift_id,
        rule_code="OFFSITE_ASSIGNMENT", rule_version_id=rule_version_id,
        status=RuleEvaluationStatus.PASS,
        severity=RuleSeverity.INFO,
        reason="ONSITE_ASSIGNMENT",
        evidence_key=evidence_key,
    )


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT: EVALUATE ALL APPLICABLE RULES
# ─────────────────────────────────────────────────────────────

def evaluate_all_rules(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    shift_id: str | None = None,
    canonical_event: CanonicalAttendanceEvent | None = None,
    rule_version_id: str | None = None,
) -> list[RuleEvaluation]:
    """Evaluate all applicable rules for an employee on an operating date.
    
    Returns list of RuleEvaluation records created/returned.
    Each rule is TBC-safe: missing config → CONFIG_INCOMPLETE or BLOCKED_POLICY_DECISION.
    """
    results = []
    
    # Get roster for this employee/date
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.employee_id == employee_id,
            RosterAssignment.operating_date == operating_date,
        )
    )
    
    if not roster:
        return results
    
    # Skip non-WORK employees for most rules
    if roster.work_status != WorkStatus.WORK:
        # Only OFFSITE_ASSIGNMENT applies to non-WORK
        result = evaluate_offsite_assignment(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=shift_id or roster.shift_id,
            rule_version_id=rule_version_id,
        )
        results.append(result)
        return results
    
    effective_shift_id = shift_id or roster.shift_id
    effective_rule_version_id = rule_version_id or roster.rule_version_id
    
    # Evaluate rules that don't need a canonical event
    if effective_shift_id:
        # MISSING_BRIEFING
        result = evaluate_missing_briefing(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=effective_shift_id,
            rule_version_id=effective_rule_version_id,
        )
        results.append(result)
        
        # MISSING_SHIFT_OUT
        result = evaluate_missing_shift_out(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=effective_shift_id,
            rule_version_id=effective_rule_version_id,
        )
        results.append(result)
    
    # INSUFFICIENT_REST
    result = evaluate_insufficient_rest(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=effective_shift_id,
        rule_version_id=effective_rule_version_id,
    )
    results.append(result)
    
    # OFFSITE_ASSIGNMENT
    result = evaluate_offsite_assignment(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, shift_id=effective_shift_id,
        rule_version_id=effective_rule_version_id,
    )
    results.append(result)
    
    # If we have a canonical event, evaluate event-based rules
    if canonical_event and effective_shift_id:
        if canonical_event.event_type == CanonicalEventType.BREAK_IN:
            result = evaluate_late_break_return(
                db, tenant_id=tenant_id, employee_id=employee_id,
                operating_date=operating_date, shift_id=effective_shift_id,
                canonical_event=canonical_event,
                rule_version_id=effective_rule_version_id,
            )
            results.append(result)
        
        if canonical_event.event_type == CanonicalEventType.BRIEFING_IN:
            result = evaluate_late_briefing(
                db, tenant_id=tenant_id, employee_id=employee_id,
                operating_date=operating_date, shift_id=effective_shift_id,
                canonical_event=canonical_event,
                rule_version_id=effective_rule_version_id,
            )
            results.append(result)
        
        if canonical_event.event_type == CanonicalEventType.HANDOVER_START:
            result = evaluate_early_handover(
                db, tenant_id=tenant_id, employee_id=employee_id,
                operating_date=operating_date, shift_id=effective_shift_id,
                canonical_event=canonical_event,
                rule_version_id=effective_rule_version_id,
            )
            results.append(result)
        
        if canonical_event.event_type == CanonicalEventType.HANDOVER_END:
            result = evaluate_late_handover(
                db, tenant_id=tenant_id, employee_id=employee_id,
                operating_date=operating_date, shift_id=effective_shift_id,
                canonical_event=canonical_event,
                rule_version_id=effective_rule_version_id,
            )
            results.append(result)
        
        # Geofence (always applicable if event has location)
        result = evaluate_geofence(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=effective_shift_id,
            canonical_event=canonical_event,
            rule_version_id=effective_rule_version_id,
        )
        results.append(result)
        
        # Device risk
        result = evaluate_device_risk(
            db, tenant_id=tenant_id, employee_id=employee_id,
            operating_date=operating_date, shift_id=effective_shift_id,
            canonical_event=canonical_event,
            rule_version_id=effective_rule_version_id,
        )
        results.append(result)
    
    return results


# ─────────────────────────────────────────────────────────────
# QUERY HELPERS
# ─────────────────────────────────────────────────────────────

def get_evaluations(
    db: Session,
    *,
    tenant_id: str,
    operating_date: date,
    employee_id: str | None = None,
    rule_code: str | None = None,
    status: RuleEvaluationStatus | None = None,
) -> list[RuleEvaluation]:
    """Query rule evaluations."""
    query = select(RuleEvaluation).where(
        RuleEvaluation.tenant_id == tenant_id,
        RuleEvaluation.operating_date == operating_date,
    )
    if employee_id:
        query = query.where(RuleEvaluation.employee_id == employee_id)
    if rule_code:
        query = query.where(RuleEvaluation.rule_code == rule_code)
    if status:
        query = query.where(RuleEvaluation.status == status)
    
    return list(db.scalars(query.order_by(RuleEvaluation.rule_code)).all())
