"""
roster_validator.py — M1 roster validation engine for Metro Mining.
Implements 8 validators that check roster assignments against operating rules.

Each validator returns a list of ExceptionEvent objects (empty = pass).
"""
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.models import (
    RosterAssignment, ExceptionEvent, EquipmentAssignmentActual,
    Competency, Equipment, EmployeeMeta, Worker, ShiftTemplate,
    WorkStatus, SiteStatusEnum, ExceptionSeverity, ExceptionStatus,
    CompetencyStatus, EquipmentStatus,
)
from app.models import uid
from app.roster_generator import read_roster_policy


# ─────────────────────────────────────────────────────────────
# Helper: create exception
# ─────────────────────────────────────────────────────────────
def _exc(
    tenant_id: str,
    operating_date: date,
    employee_id: str,
    rule_code: str,
    severity: ExceptionSeverity = ExceptionSeverity.CRITICAL,
    equipment_id: str | None = None,
    rule_version: str | None = None,
    notes: str | None = None,
) -> ExceptionEvent:
    return ExceptionEvent(
        id=uid(),
        tenant_id=tenant_id,
        operating_date=operating_date,
        employee_id=employee_id,
        equipment_id=equipment_id,
        rule_code=rule_code,
        rule_version=rule_version,
        severity=severity,
        status=ExceptionStatus.OPEN,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────
# V1: Max 12 consecutive WORK days → day 13 must be REST
# ─────────────────────────────────────────────────────────────
def validate_max_consecutive_work(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    work_status: WorkStatus, rule_version: str | None = None,
    max_work: int = 12,
) -> list[ExceptionEvent]:
    """
    Test 1-3: If employee has worked max_work consecutive days ending at
    operating_date-1, then operating_date MUST be REST.
    """
    exceptions = []
    if work_status != WorkStatus.WORK:
        return exceptions

    # Count consecutive WORK days ending at operating_date - 1
    check_date = operating_date - timedelta(days=1)
    streak = 0
    while streak < max_work:
        prev = (
            db.query(RosterAssignment)
            .filter(
                RosterAssignment.tenant_id == tenant_id,
                RosterAssignment.employee_id == employee_id,
                RosterAssignment.operating_date == check_date,
                RosterAssignment.work_status == WorkStatus.WORK,
            )
            .first()
        )
        if not prev:
            break
        streak += 1
        check_date -= timedelta(days=1)

    if streak >= max_work:
        exceptions.append(_exc(
            tenant_id, operating_date, employee_id,
            "MAX_CONSECUTIVE_WORK",
            rule_version=rule_version,
            notes=f"Attempted WORK after {streak} consecutive WORK days (max {max_work}). Day {max_work+1} must be REST.",
        ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V2: Day 13 must be REST (explicit check)
# ─────────────────────────────────────────────────────────────
def validate_mandatory_rest(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    work_status: WorkStatus, rule_version: str | None = None,
    max_work: int = 12,
) -> list[ExceptionEvent]:
    """
    Test 2: Reject WORK on day 13 (the day after max_work consecutive WORK days).
    This is the inverse of V1 — catches the exact boundary.
    """
    exceptions = []
    if work_status != WorkStatus.WORK:
        return exceptions

    # Count exactly max_work consecutive WORK days ending at operating_date - 1
    check_date = operating_date - timedelta(days=1)
    streak = 0
    while streak < max_work:
        prev = (
            db.query(RosterAssignment)
            .filter(
                RosterAssignment.tenant_id == tenant_id,
                RosterAssignment.employee_id == employee_id,
                RosterAssignment.operating_date == check_date,
                RosterAssignment.work_status == WorkStatus.WORK,
            )
            .first()
        )
        if not prev:
            break
        streak += 1
        check_date -= timedelta(days=1)

    if streak == max_work:
        exceptions.append(_exc(
            tenant_id, operating_date, employee_id,
            "MANDATORY_REST_DAY",
            rule_version=rule_version,
            notes=f"Day {max_work+1} after {max_work} consecutive WORK days must be REST.",
        ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V3: Max 7 consecutive worked days on same shift
# ─────────────────────────────────────────────────────────────
def validate_max_same_shift(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    shift_id: str | None, work_status: WorkStatus,
    rule_version: str | None = None, max_same_shift: int = 7,
) -> list[ExceptionEvent]:
    """
    Test 4-5: Reject 8th consecutive worked day on the same shift.
    """
    exceptions = []
    if work_status != WorkStatus.WORK or not shift_id:
        return exceptions

    check_date = operating_date - timedelta(days=1)
    streak = 0
    while streak < max_same_shift:
        prev = (
            db.query(RosterAssignment)
            .filter(
                RosterAssignment.tenant_id == tenant_id,
                RosterAssignment.employee_id == employee_id,
                RosterAssignment.operating_date == check_date,
                RosterAssignment.work_status == WorkStatus.WORK,
                RosterAssignment.shift_id == shift_id,
            )
            .first()
        )
        if not prev:
            break
        streak += 1
        check_date -= timedelta(days=1)

    if streak >= max_same_shift:
        exceptions.append(_exc(
            tenant_id, operating_date, employee_id,
            "MAX_SAME_SHIFT_WORK",
            rule_version=rule_version,
            notes=f"Attempted WORK on {shift_id} after {streak} consecutive same-shift days (max {max_same_shift}).",
        ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V4: Onsite/offsite cycle
# ─────────────────────────────────────────────────────────────
def validate_onsite_offsite_cycle(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    site_status: SiteStatusEnum, work_status: WorkStatus,
    rule_version: str | None = None,
    onsite_weeks: int = 12, offsite_weeks: int = 2,
) -> list[ExceptionEvent]:
    """
    Test 6: Reject WORK during OFFSITE period.
    The cycle is onsite_weeks ON + offsite_weeks OFF.
    """
    exceptions = []
    if site_status != SiteStatusEnum.OFFSITE:
        return exceptions
    if work_status != WorkStatus.WORK:
        return exceptions

    exceptions.append(_exc(
        tenant_id, operating_date, employee_id,
        "OFFSITE_WORK_REJECTED",
        rule_version=rule_version,
        notes=f"WORK assignment during OFFSITE period (cycle: {onsite_weeks}w ON + {offsite_weeks}w OFF).",
    ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V5: No overlapping assignments for one employee
# ─────────────────────────────────────────────────────────────
def validate_no_overlap(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    shift_id: str | None, rule_version: str | None = None,
) -> list[ExceptionEvent]:
    """
    Test 8: Reject overlapping assignments for the same employee on the same date.
    """
    exceptions = []
    existing = (
        db.query(RosterAssignment)
        .filter(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.employee_id == employee_id,
            RosterAssignment.operating_date == operating_date,
        )
        .count()
    )
    # If there's already an assignment (and we're about to add another), flag it
    if existing > 0:
        exceptions.append(_exc(
            tenant_id, operating_date, employee_id,
            "OVERLAPPING_ASSIGNMENT",
            rule_version=rule_version,
            notes=f"Employee already has {existing} assignment(s) on {operating_date}.",
        ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V6: No two employees on same equipment for overlapping intervals
# ─────────────────────────────────────────────────────────────
def validate_equipment_double_book(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    equipment_id: str | None, rule_version: str | None = None,
) -> list[ExceptionEvent]:
    """
    Test 9: Reject two employees on the same equipment for overlapping intervals.
    """
    exceptions = []
    if not equipment_id:
        return exceptions

    conflict = (
        db.query(RosterAssignment)
        .filter(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.operating_date == operating_date,
            RosterAssignment.planned_equipment_id == equipment_id,
            RosterAssignment.employee_id != employee_id,
        )
        .first()
    )
    if conflict:
        exceptions.append(_exc(
            tenant_id, operating_date, employee_id,
            "EQUIPMENT_DOUBLE_BOOK",
            equipment_id=equipment_id,
            rule_version=rule_version,
            notes=f"Equipment {equipment_id} already assigned to {conflict.employee_id} on {operating_date}.",
        ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V7: Competency check
# ─────────────────────────────────────────────────────────────
def validate_competency(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
    equipment_id: str | None, rule_version: str | None = None,
) -> list[ExceptionEvent]:
    """
    Test 16-17: Employee must have valid competency for equipment type.
    """
    exceptions = []
    if not equipment_id:
        return exceptions

    eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not eq:
        return exceptions

    comp = (
        db.query(Competency)
        .filter(
            Competency.tenant_id == tenant_id,
            Competency.employee_id == employee_id,
            Competency.equipment_type == eq.equipment_type,
            Competency.status == CompetencyStatus.VALID,
            Competency.valid_from <= operating_date,
        )
        .filter(
            (Competency.valid_to.is_(None)) | (Competency.valid_to >= operating_date)
        )
        .first()
    )
    if not comp:
        exceptions.append(_exc(
            tenant_id, operating_date, employee_id,
            "UNQUALIFIED_ASSIGNMENT",
            equipment_id=equipment_id,
            rule_version=rule_version,
            notes=f"No valid competency for {eq.equipment_type} on {operating_date}.",
        ))
    return exceptions


# ─────────────────────────────────────────────────────────────
# V8: Equipment status check
# ─────────────────────────────────────────────────────────────
def validate_equipment_status(
    db: Session, tenant_id: str, operating_date: date,
    equipment_id: str | None, rule_version: str | None = None,
) -> list[ExceptionEvent]:
    """
    Equipment must be ACTIVE on the operating date.
    """
    exceptions = []
    if not equipment_id:
        return exceptions

    eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not eq:
        exceptions.append(_exc(
            tenant_id, operating_date, "UNKNOWN",
            "EQUIPMENT_NOT_FOUND",
            equipment_id=equipment_id,
            rule_version=rule_version,
            notes=f"Equipment {equipment_id} not found.",
        ))
        return exceptions

    if eq.status != EquipmentStatus.ACTIVE:
        exceptions.append(_exc(
            tenant_id, operating_date, "UNKNOWN",
            "EQUIPMENT_INACTIVE",
            equipment_id=equipment_id,
            rule_version=rule_version,
            notes=f"Equipment {equipment_id} status is {eq.status.value}, not ACTIVE.",
        ))

    if eq.effective_from and operating_date < eq.effective_from:
        exceptions.append(_exc(
            tenant_id, operating_date, "UNKNOWN",
            "EQUIPMENT_NOT_YET_EFFECTIVE",
            equipment_id=equipment_id,
            rule_version=rule_version,
            notes=f"Equipment {equipment_id} effective from {eq.effective_from}, not yet available on {operating_date}.",
        ))

    if eq.effective_to and operating_date > eq.effective_to:
        exceptions.append(_exc(
            tenant_id, operating_date, "UNKNOWN",
            "EQUIPMENT_EXPIRED",
            equipment_id=equipment_id,
            rule_version=rule_version,
            notes=f"Equipment {equipment_id} expired on {eq.effective_to}.",
        ))

    return exceptions


# ─────────────────────────────────────────────────────────────
# Full validation pipeline
# ─────────────────────────────────────────────────────────────
def validate_roster_assignment(
    db: Session,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
    work_status: WorkStatus,
    shift_id: str | None = None,
    site_status: SiteStatusEnum = SiteStatusEnum.ONSITE,
    equipment_id: str | None = None,
    rule_version: str | None = None,
    skip_existing_check: bool = False,
) -> list[ExceptionEvent]:
    """
    Run all applicable validators for a single roster assignment.
    Returns list of ExceptionEvent objects (empty = all pass).
    """
    all_exceptions = []

    # Pull cycle/rest/shift limits from the RosterPolicy table (CONFIRMED only),
    # falling back to the documented defaults (12/7/12/2/1) when unset.
    policy = read_roster_policy(db, tenant_id)
    max_work = policy["max_consecutive_workdays"]
    max_same_shift = policy["max_same_shift_streak"]
    onsite_weeks = policy["onsite_weeks"]
    offsite_weeks = policy["offsite_weeks"]

    # V1: Max consecutive work
    all_exceptions.extend(validate_max_consecutive_work(
        db, tenant_id, employee_id, operating_date, work_status, rule_version,
        max_work=max_work))

    # V2: Mandatory rest
    all_exceptions.extend(validate_mandatory_rest(
        db, tenant_id, employee_id, operating_date, work_status, rule_version,
        max_work=max_work))

    # V3: Max same shift
    all_exceptions.extend(validate_max_same_shift(
        db, tenant_id, employee_id, operating_date, shift_id, work_status, rule_version,
        max_same_shift=max_same_shift))

    # V4: Onsite/offsite cycle
    all_exceptions.extend(validate_onsite_offsite_cycle(
        db, tenant_id, employee_id, operating_date, site_status, work_status, rule_version,
        onsite_weeks=onsite_weeks, offsite_weeks=offsite_weeks))

    # V5: No overlap (skip if we're validating an existing row)
    if not skip_existing_check:
        all_exceptions.extend(validate_no_overlap(
            db, tenant_id, employee_id, operating_date, shift_id, rule_version))

    # V6: Equipment double-book
    all_exceptions.extend(validate_equipment_double_book(
        db, tenant_id, employee_id, operating_date, equipment_id, rule_version))

    # V7: Competency
    all_exceptions.extend(validate_competency(
        db, tenant_id, employee_id, operating_date, equipment_id, rule_version))

    # V8: Equipment status
    all_exceptions.extend(validate_equipment_status(
        db, tenant_id, operating_date, equipment_id, rule_version))

    return all_exceptions


def validate_full_roster(
    db: Session, tenant_id: str, rule_version: str | None = None,
) -> dict:
    """
    Validate all roster assignments for a tenant.
    Returns summary dict with counts.
    """
    assignments = (
        db.query(RosterAssignment)
        .filter(RosterAssignment.tenant_id == tenant_id)
        .order_by(RosterAssignment.operating_date)
        .all()
    )

    total_exceptions = 0
    by_rule = {}
    for a in assignments:
        excs = validate_roster_assignment(
            db, tenant_id, a.employee_id, a.operating_date,
            a.work_status, a.shift_id, a.site_status,
            a.planned_equipment_id, a.effective_rule_version or rule_version,
            skip_existing_check=True,
        )
        for exc in excs:
            db.add(exc)
            total_exceptions += 1
            by_rule[exc.rule_code] = by_rule.get(exc.rule_code, 0) + 1

    db.commit()
    return {
        "total_assignments": len(assignments),
        "total_exceptions": total_exceptions,
        "by_rule": by_rule,
    }
