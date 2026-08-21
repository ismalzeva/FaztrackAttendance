"""
equipment_engine.py -- Planned vs Actual Equipment Assignment Engine (M2C).

Generic comparison engine for all Faztrack Attendance tenants.
Configuration-driven: tenant-specific behavior from roster/equipment data.
ACTUAL MUST NEVER OVERWRITE PLANNED.

ARCHITECTURE RULE (permanent):
  SHARED CORE ENGINE + STRICTLY ISOLATED COMPANY CONFIGURATION
  Every tenant is an independent boundary. No company-specific branching in core.
  Behavior comes from tenant capability/policy/master data, never from tenant name.
"""
import json
from datetime import date, datetime, timezone
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Equipment, EquipmentStatus,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    RosterAssignment, RosterPolicy,
    Competency, CompetencyStatus,
    Worker,
)


# ─────────────────────────────────────────────────────────────
# TENANT CAPABILITY
# ─────────────────────────────────────────────────────────────

def get_tenant_capability(
    db: Session,
    *,
    tenant_id: str,
    capability_key: str,
    default: str = "false",
) -> str:
    """Read a tenant capability from RosterPolicy.
    
    Returns policy_value if set, otherwise default.
    Capability keys are tenant-scoped and auditable via RosterPolicy.
    """
    policy = db.scalar(
        select(RosterPolicy).where(
            RosterPolicy.tenant_id == tenant_id,
            RosterPolicy.policy_key == capability_key,
        )
    )
    if policy:
        return policy.policy_value
    return default


def is_capability_enabled(
    db: Session,
    *,
    tenant_id: str,
    capability_key: str,
    default: bool = False,
) -> bool:
    """Check if a boolean tenant capability is enabled."""
    val = get_tenant_capability(
        db, tenant_id=tenant_id, capability_key=capability_key,
        default="true" if default else "false",
    )
    return val.lower() in ("true", "1", "yes")


# ─────────────────────────────────────────────────────────────
# COMPETENCY VALIDATION
# ─────────────────────────────────────────────────────────────

def validate_competency(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    equipment_type: str,
    at_date: date,
) -> tuple[bool, str]:
    """Validate worker has valid competency for equipment type at given date.
    
    Capability-driven:
    - competency_validation_enabled=false → (True, "COMPETENCY_NOT_APPLICABLE")
    - competency_validation_enabled=true + valid → (True, "COMPETENCY_VALID")
    - competency_validation_enabled=true + missing → (False, "COMPETENCY_MISSING")
    - competency_validation_enabled=true + expired → (False, "COMPETENCY_EXPIRED")
    
    Uses effective-dated validity (valid_from <= at_date <= valid_to).
    Historical validity matters: cert valid through Sep 10, assignment Sep 9 = valid.
    """
    # Check tenant capability first
    competency_enabled = is_capability_enabled(
        db, tenant_id=tenant_id, capability_key="competency_validation_enabled",
    )
    if not competency_enabled:
        return True, "COMPETENCY_NOT_APPLICABLE"
    
    competencies = db.scalars(
        select(Competency).where(
            Competency.tenant_id == tenant_id,
            Competency.employee_id == employee_id,
            Competency.equipment_type == equipment_type,
            Competency.valid_from <= at_date,
        )
    ).all()
    
    if not competencies:
        return False, "COMPETENCY_MISSING"
    
    # Check if any competency is valid at the date
    for comp in competencies:
        if comp.status == CompetencyStatus.SUSPENDED:
            continue
        if comp.status == CompetencyStatus.EXPIRED:
            # Check if it was expired at the date
            if comp.valid_to and comp.valid_to < at_date:
                continue
        if comp.valid_to is None or comp.valid_to >= at_date:
            if comp.status == CompetencyStatus.VALID:
                return True, "COMPETENCY_VALID"
    
    return False, "COMPETENCY_EXPIRED"


# ─────────────────────────────────────────────────────────────
# EQUIPMENT STATUS VALIDATION
# ─────────────────────────────────────────────────────────────

def validate_equipment_status(
    db: Session,
    *,
    tenant_id: str,
    equipment_id: str,
    at_date: date,
) -> tuple[bool, str]:
    """Validate equipment is ACTIVE at given date.
    
    Returns: (is_valid, reason_code)
    """
    equipment = db.get(Equipment, equipment_id)
    if not equipment:
        return False, "EQUIPMENT_NOT_FOUND"
    
    if equipment.tenant_id != tenant_id:
        return False, "CROSS_TENANT_EQUIPMENT"
    
    if equipment.status == EquipmentStatus.OUT_OF_SERVICE:
        return False, "EQUIPMENT_OUT_OF_SERVICE"
    
    if equipment.status == EquipmentStatus.INACTIVE:
        return False, "EQUIPMENT_INACTIVE"
    
    # Check effective dates
    if equipment.effective_from > at_date:
        return False, "EQUIPMENT_NOT_YET_EFFECTIVE"
    
    if equipment.effective_to and equipment.effective_to < at_date:
        return False, "EQUIPMENT_EXPIRED"
    
    return True, "EQUIPMENT_ACTIVE"


# ─────────────────────────────────────────────────────────────
# INTERVAL INTEGRITY
# ─────────────────────────────────────────────────────────────

def _ensure_tz(dt: datetime) -> datetime:
    """Normalize datetime to ensure timezone-aware comparison.
    SQLite strips tz info; if naive, assume UTC for comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def check_worker_overlap(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    started_at: datetime,
    ended_at: datetime | None,
    exclude_assignment_id: str | None = None,
) -> list[dict]:
    """Check if worker has overlapping actual assignments.
    
    Returns list of overlapping assignments (empty = no overlap).
    """
    query = select(EquipmentAssignmentActual).where(
        EquipmentAssignmentActual.tenant_id == tenant_id,
        EquipmentAssignmentActual.employee_id == employee_id,
        EquipmentAssignmentActual.operating_date == operating_date,
        EquipmentAssignmentActual.status == ActualAssignmentStatus.ACTIVE,
    )
    
    if exclude_assignment_id:
        query = query.where(EquipmentAssignmentActual.id != exclude_assignment_id)
    
    existing = db.scalars(query).all()
    overlaps = []
    
    for ex in existing:
        # Interval overlap: start1 < end2 AND start2 < end1
        ex_start = _ensure_tz(ex.started_at)
        ex_end = _ensure_tz(ex.ended_at) if ex.ended_at else datetime.max.replace(tzinfo=timezone.utc)
        new_start = _ensure_tz(started_at)
        new_end = _ensure_tz(ended_at) if ended_at else datetime.max.replace(tzinfo=timezone.utc)
        
        if ex_start < new_end and new_start < ex_end:
            overlaps.append({
                "assignment_id": ex.id,
                "equipment_id": ex.equipment_id,
                "started_at": ex.started_at.isoformat(),
                "ended_at": ex.ended_at.isoformat() if ex.ended_at else None,
            })
    
    return overlaps


def check_equipment_overlap(
    db: Session,
    *,
    tenant_id: str,
    equipment_id: str,
    operating_date: date,
    started_at: datetime,
    ended_at: datetime | None,
    exclude_assignment_id: str | None = None,
) -> list[dict]:
    """Check if equipment has overlapping operators.
    
    Returns list of overlapping assignments (empty = no overlap).
    """
    query = select(EquipmentAssignmentActual).where(
        EquipmentAssignmentActual.tenant_id == tenant_id,
        EquipmentAssignmentActual.equipment_id == equipment_id,
        EquipmentAssignmentActual.operating_date == operating_date,
        EquipmentAssignmentActual.status == ActualAssignmentStatus.ACTIVE,
    )
    
    if exclude_assignment_id:
        query = query.where(EquipmentAssignmentActual.id != exclude_assignment_id)
    
    existing = db.scalars(query).all()
    overlaps = []
    
    for ex in existing:
        ex_start = _ensure_tz(ex.started_at)
        ex_end = _ensure_tz(ex.ended_at) if ex.ended_at else datetime.max.replace(tzinfo=timezone.utc)
        new_start = _ensure_tz(started_at)
        new_end = _ensure_tz(ended_at) if ended_at else datetime.max.replace(tzinfo=timezone.utc)
        
        if ex_start < new_end and new_start < ex_end:
            overlaps.append({
                "assignment_id": ex.id,
                "employee_id": ex.employee_id,
                "started_at": ex.started_at.isoformat(),
                "ended_at": ex.ended_at.isoformat() if ex.ended_at else None,
            })
    
    return overlaps


# ─────────────────────────────────────────────────────────────
# ACTUAL ASSIGNMENT CREATION
# ─────────────────────────────────────────────────────────────

def create_actual_assignment(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    equipment_id: str,
    operating_date: date,
    shift_id: str | None,
    site_id: str | None,
    started_at: datetime,
    ended_at: datetime | None = None,
    source: str = "MANUAL",
    canonical_event_id: str | None = None,
    supervisor_id: str | None = None,
    reason: str | None = None,
    rule_version_id: str | None = None,
) -> tuple[EquipmentAssignmentActual | None, str]:
    """Create actual equipment assignment with full validation.
    
    Returns: (assignment, status_string)
    status_string: "CREATED", "DUPLICATE", "INVALID_WORKER", "INVALID_EQUIPMENT",
                   "COMPETENCY_INVALID", "EQUIPMENT_UNAVAILABLE", "OVERLAP_WORKER",
                   "OVERLAP_EQUIPMENT", "CROSS_TENANT"
    """
    # 1. Tenant isolation: worker must belong to tenant
    worker = db.get(Worker, employee_id)
    if not worker or worker.tenant_id != tenant_id:
        return None, "INVALID_WORKER"
    
    # 2. Tenant isolation: equipment must belong to tenant
    equipment = db.get(Equipment, equipment_id)
    if not equipment or equipment.tenant_id != tenant_id:
        return None, "CROSS_TENANT_EQUIPMENT"
    
    # 3. Idempotency: check for duplicate BEFORE validation/overlap
    #    Same evidence retry must return existing, not OVERLAP_WORKER
    existing = db.scalar(
        select(EquipmentAssignmentActual).where(
            EquipmentAssignmentActual.tenant_id == tenant_id,
            EquipmentAssignmentActual.employee_id == employee_id,
            EquipmentAssignmentActual.equipment_id == equipment_id,
            EquipmentAssignmentActual.started_at == started_at,
        )
    )
    if existing:
        return existing, "DUPLICATE"
    
    # 4. Equipment status validation
    eq_valid, eq_reason = validate_equipment_status(
        db, tenant_id=tenant_id, equipment_id=equipment_id, at_date=operating_date,
    )
    if not eq_valid:
        return None, "EQUIPMENT_UNAVAILABLE"
    
    # 5. Competency validation (capability-driven)
    comp_valid, comp_reason = validate_competency(
        db, tenant_id=tenant_id, employee_id=employee_id,
        equipment_type=equipment.equipment_type, at_date=operating_date,
    )
    if not comp_valid:
        return None, comp_reason  # COMPETENCY_INVALID or COMPETENCY_NOT_APPLICABLE
    
    # 6. Interval integrity: worker overlap
    worker_overlaps = check_worker_overlap(
        db, tenant_id=tenant_id, employee_id=employee_id,
        operating_date=operating_date, started_at=started_at, ended_at=ended_at,
    )
    if worker_overlaps:
        return None, "OVERLAP_WORKER"
    
    # 7. Interval integrity: equipment overlap
    equip_overlaps = check_equipment_overlap(
        db, tenant_id=tenant_id, equipment_id=equipment_id,
        operating_date=operating_date, started_at=started_at, ended_at=ended_at,
    )
    if equip_overlaps:
        return None, "OVERLAP_EQUIPMENT"
    
    # 8. Create assignment
    assignment = EquipmentAssignmentActual(
        tenant_id=tenant_id,
        employee_id=employee_id,
        equipment_id=equipment_id,
        operating_date=operating_date,
        shift_id=shift_id,
        site_id=site_id,
        started_at=started_at,
        ended_at=ended_at,
        source=source,
        canonical_event_id=canonical_event_id,
        supervisor_id=supervisor_id,
        reason=reason,
        status=ActualAssignmentStatus.ACTIVE,
        rule_version_id=rule_version_id,
    )
    db.add(assignment)
    db.flush()
    
    return assignment, "CREATED"


def close_actual_assignment(
    db: Session,
    *,
    assignment_id: str,
    ended_at: datetime,
) -> EquipmentAssignmentActual | None:
    """Close an active actual assignment (equipment change or shift end)."""
    assignment = db.get(EquipmentAssignmentActual, assignment_id)
    if not assignment or assignment.status != ActualAssignmentStatus.ACTIVE:
        return None
    
    assignment.ended_at = ended_at
    assignment.status = ActualAssignmentStatus.CLOSED
    db.flush()
    return assignment


# ─────────────────────────────────────────────────────────────
# PLANNED VS ACTUAL COMPARISON
# ─────────────────────────────────────────────────────────────

def compare_planned_vs_actual(
    db: Session,
    *,
    actual_assignment: EquipmentAssignmentActual,
) -> EquipmentComparisonResult:
    """Compare planned roster equipment with actual assignment.
    
    Creates comparison result. Idempotent via unique constraint on actual_assignment_id.
    ACTUAL MUST NEVER OVERWRITE PLANNED.
    """
    tenant_id = actual_assignment.tenant_id
    employee_id = actual_assignment.employee_id
    operating_date = actual_assignment.operating_date
    
    # Find planned assignment
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.employee_id == employee_id,
            RosterAssignment.operating_date == operating_date,
        )
    )
    
    planned_equipment_id = roster.planned_equipment_id if roster else None
    planned_worker_id = employee_id  # Planned worker is the roster employee
    
    # Determine comparison result
    if planned_equipment_id is None:
        result = ComparisonResult.NO_PLANNED_EQUIPMENT
        reason_code = "NO_PLANNED_EQUIPMENT"
    elif planned_equipment_id == actual_assignment.equipment_id:
        result = ComparisonResult.MATCH
        reason_code = "MATCH"
    else:
        result = ComparisonResult.MISMATCH
        reason_code = "EQUIPMENT_MISMATCH"
    
    # Check for operator substitution (actual worker != planned worker)
    # This is handled separately in detect_substitution
    
    comparison = EquipmentComparisonResult(
        tenant_id=tenant_id,
        actual_assignment_id=actual_assignment.id,
        employee_id=employee_id,
        operating_date=operating_date,
        shift_id=actual_assignment.shift_id,
        planned_equipment_id=planned_equipment_id,
        actual_equipment_id=actual_assignment.equipment_id,
        comparison_result=result,
        planned_worker_id=planned_worker_id,
        actual_worker_id=employee_id,
        reason_code=reason_code,
        rule_version_id=actual_assignment.rule_version_id,
    )
    db.add(comparison)
    db.flush()
    
    return comparison


def detect_substitution(
    db: Session,
    *,
    actual_assignment: EquipmentAssignmentActual,
) -> EquipmentDiscrepancy | None:
    """Detect operator substitution: actual worker != planned worker for equipment.
    
    Returns discrepancy if substitution detected, None otherwise.
    """
    tenant_id = actual_assignment.tenant_id
    operating_date = actual_assignment.operating_date
    equipment_id = actual_assignment.equipment_id
    
    # Find planned assignment for this equipment on this date
    roster = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.operating_date == operating_date,
            RosterAssignment.planned_equipment_id == equipment_id,
        )
    )
    
    if not roster:
        return None  # No planned assignment for this equipment
    
    if roster.employee_id == actual_assignment.employee_id:
        return None  # Same worker, no substitution
    
    # Check for existing discrepancy (idempotency)
    existing = db.scalar(
        select(EquipmentDiscrepancy).where(
            EquipmentDiscrepancy.tenant_id == tenant_id,
            EquipmentDiscrepancy.actual_assignment_id == actual_assignment.id,
            EquipmentDiscrepancy.discrepancy_type == DiscrepancyType.OPERATOR_SUBSTITUTION,
        )
    )
    if existing:
        return existing
    
    # Create substitution discrepancy
    discrepancy = EquipmentDiscrepancy(
        tenant_id=tenant_id,
        actual_assignment_id=actual_assignment.id,
        employee_id=actual_assignment.employee_id,
        operating_date=operating_date,
        shift_id=actual_assignment.shift_id,
        planned_equipment_id=equipment_id,
        actual_equipment_id=equipment_id,
        planned_worker_id=roster.employee_id,
        actual_worker_id=actual_assignment.employee_id,
        discrepancy_type=DiscrepancyType.OPERATOR_SUBSTITUTION,
        source=actual_assignment.source,
        canonical_event_id=actual_assignment.canonical_event_id,
        status=DiscrepancyStatus.OPEN,
        reason=f"Planned operator {roster.employee_id} replaced by {actual_assignment.employee_id}",
        rule_version_id=actual_assignment.rule_version_id,
    )
    db.add(discrepancy)
    db.flush()
    
    return discrepancy


def create_mismatch_discrepancy(
    db: Session,
    *,
    comparison: EquipmentComparisonResult,
) -> EquipmentDiscrepancy | None:
    """Create discrepancy for equipment mismatch.
    
    Returns discrepancy if mismatch, None if match or no planned.
    """
    if comparison.comparison_result != ComparisonResult.MISMATCH:
        return None
    
    # Idempotency check
    existing = db.scalar(
        select(EquipmentDiscrepancy).where(
            EquipmentDiscrepancy.tenant_id == comparison.tenant_id,
            EquipmentDiscrepancy.actual_assignment_id == comparison.actual_assignment_id,
            EquipmentDiscrepancy.discrepancy_type == DiscrepancyType.EQUIPMENT_MISMATCH,
        )
    )
    if existing:
        return existing
    
    discrepancy = EquipmentDiscrepancy(
        tenant_id=comparison.tenant_id,
        actual_assignment_id=comparison.actual_assignment_id,
        employee_id=comparison.employee_id,
        operating_date=comparison.operating_date,
        shift_id=comparison.shift_id,
        planned_equipment_id=comparison.planned_equipment_id,
        actual_equipment_id=comparison.actual_equipment_id,
        planned_worker_id=comparison.planned_worker_id,
        actual_worker_id=comparison.actual_worker_id,
        discrepancy_type=DiscrepancyType.EQUIPMENT_MISMATCH,
        status=DiscrepancyStatus.OPEN,
        reason=f"Planned {comparison.planned_equipment_id} vs Actual {comparison.actual_equipment_id}",
        rule_version_id=comparison.rule_version_id,
    )
    db.add(discrepancy)
    db.flush()
    
    return discrepancy


# ─────────────────────────────────────────────────────────────
# EQUIPMENT CHECK-IN PROCESSING
# ─────────────────────────────────────────────────────────────

def process_equipment_checkin(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    equipment_id: str,
    operating_date: date,
    shift_id: str | None,
    site_id: str | None,
    started_at: datetime,
    source: str = "EQUIPMENT_IN",
    canonical_event_id: str | None = None,
    rule_version_id: str | None = None,
) -> dict:
    """Full equipment check-in pipeline:
    1. Create actual assignment (with validation)
    2. Compare with planned
    3. Detect discrepancies
    
    Returns dict with assignment, comparison, discrepancies, status.
    """
    # 1. Create actual assignment
    assignment, create_status = create_actual_assignment(
        db,
        tenant_id=tenant_id,
        employee_id=employee_id,
        equipment_id=equipment_id,
        operating_date=operating_date,
        shift_id=shift_id,
        site_id=site_id,
        started_at=started_at,
        source=source,
        canonical_event_id=canonical_event_id,
        rule_version_id=rule_version_id,
    )
    
    if create_status == "DUPLICATE":
        # Idempotent: return existing
        comparison = db.scalar(
            select(EquipmentComparisonResult).where(
                EquipmentComparisonResult.actual_assignment_id == assignment.id,
            )
        )
        discrepancies = db.scalars(
            select(EquipmentDiscrepancy).where(
                EquipmentDiscrepancy.actual_assignment_id == assignment.id,
            )
        ).all()
        
        return {
            "assignment": assignment,
            "create_status": "DUPLICATE",
            "comparison": comparison,
            "discrepancies": list(discrepancies),
        }
    
    if assignment is None:
        return {
            "assignment": None,
            "create_status": create_status,
            "comparison": None,
            "discrepancies": [],
        }
    
    # 2. Compare planned vs actual
    comparison = compare_planned_vs_actual(db, actual_assignment=assignment)
    
    # 3. Detect discrepancies
    discrepancies = []
    
    # Equipment mismatch
    disc = create_mismatch_discrepancy(db, comparison=comparison)
    if disc:
        discrepancies.append(disc)
    
    # Operator substitution
    disc = detect_substitution(db, actual_assignment=assignment)
    if disc:
        discrepancies.append(disc)
    
    return {
        "assignment": assignment,
        "create_status": create_status,
        "comparison": comparison,
        "discrepancies": discrepancies,
    }


# ─────────────────────────────────────────────────────────────
# QUERY HELPERS
# ─────────────────────────────────────────────────────────────

def get_actual_assignments(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
) -> list[EquipmentAssignmentActual]:
    """Get all actual assignments for worker on date."""
    return list(db.scalars(
        select(EquipmentAssignmentActual).where(
            EquipmentAssignmentActual.tenant_id == tenant_id,
            EquipmentAssignmentActual.employee_id == employee_id,
            EquipmentAssignmentActual.operating_date == operating_date,
        ).order_by(EquipmentAssignmentActual.started_at)
    ).all())


def get_discrepancies(
    db: Session,
    *,
    tenant_id: str,
    operating_date: date,
    employee_id: str | None = None,
    status: DiscrepancyStatus | None = None,
) -> list[EquipmentDiscrepancy]:
    """Get discrepancies for tenant/date/employee."""
    query = select(EquipmentDiscrepancy).where(
        EquipmentDiscrepancy.tenant_id == tenant_id,
        EquipmentDiscrepancy.operating_date == operating_date,
    )
    if employee_id:
        query = query.where(EquipmentDiscrepancy.employee_id == employee_id)
    if status:
        query = query.where(EquipmentDiscrepancy.status == status)
    
    return list(db.scalars(query).all())
