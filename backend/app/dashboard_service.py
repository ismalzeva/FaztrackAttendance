"""
dashboard_service.py — MM-M4A Field Supervisor Operational Dashboard.

Tenant-scoped aggregation service for the Field Supervisor command view.
Aggregates existing M0–M3 data into a single dashboard snapshot.
No new database tables — pure read model.

Product principle: ACTIONABILITY > INFORMATION > ANALYTICS > DECORATION.

Dashboard context: tenant + site + operating_date + shift
Timezone: resolved from tenant/site configuration, NEVER from browser/server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone, timedelta
from typing import Any

from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import Session

from app.models import (
    Tenant, Site, SiteStatus, ShiftTemplate, RosterAssignment,
    Worker, Equipment, EquipmentStatus, EmployeeMeta, Role, Crew,
    WorkStatus, SiteStatusEnum, ValidationStatus,
    CanonicalAttendanceEvent, CanonicalEventType,
    CheckpointValidationResult, CheckpointValidationStatus,
    MissingCheckpointResult,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    EquipmentComparisonResult, ComparisonResult,
    EquipmentDiscrepancy, DiscrepancyType, DiscrepancyStatus,
    ExceptionCase, ExceptionStatus, ExceptionSeverity,
    ExceptionDecision, DecisionStatus, DecisionType,
    Competency, CompetencyStatus,
    CheckpointPolicy,
)


# ── Operational Presentation States ──────────────────────────
# Derived from canonical events + checkpoint results.
# This is NOT a second source of truth — presentation layer only.

class OperationalState:
    NOT_STARTED = "NOT_STARTED"
    BRIEFING_COMPLETE = "BRIEFING_COMPLETE"
    AT_EQUIPMENT = "AT_EQUIPMENT"
    WORKING = "WORKING"
    ON_BREAK = "ON_BREAK"
    RETURNED_FROM_BREAK = "RETURNED_FROM_BREAK"
    HANDOVER = "HANDOVER"
    SHIFT_COMPLETE = "SHIFT_COMPLETE"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"


# ── Exceptions pagination limit ──────────────────────────────
ACTIVE_EXCEPTIONS_LIMIT = 50
ACTION_REQUIRED_LIMIT = 50


# ── Data Classes ─────────────────────────────────────────────

@dataclass(frozen=True)
class DashboardContext:
    tenant_id: str
    tenant_name: str
    site_id: str | None
    site_name: str | None
    operating_date: date
    shift_id: str | None
    shift_name: str | None
    timezone_str: str
    generated_at: datetime
    last_event_at: datetime | None = None


@dataclass
class ShiftSummary:
    scheduled_work: int = 0
    scheduled_rest: int = 0
    scheduled_offsite: int = 0
    scheduled_leave: int = 0
    scheduled_other: int = 0
    present_operational: int = 0
    not_yet_confirmed: int = 0
    unresolved_exceptions: int = 0
    pending_decisions: int = 0


@dataclass
class RosterStatusItem:
    employee_id: str
    employee_name: str
    employee_code: str
    role_name: str | None
    crew_name: str | None
    work_status: str
    shift_id: str | None
    site_status: str | None
    planned_equipment_code: str | None
    operational_state: str = OperationalState.NOT_STARTED


@dataclass
class CheckpointStatusItem:
    employee_id: str
    employee_name: str
    checkpoint_type: str
    status: str  # PASS, FAIL, MISSING, CONFIG_INCOMPLETE, NOT_APPLICABLE
    detected_timestamp: datetime | None = None
    is_late: bool = False
    late_reason: str | None = None


@dataclass
class EquipmentStatusItem:
    employee_id: str
    employee_name: str
    planned_equipment_id: str | None
    planned_equipment_code: str | None
    actual_equipment_id: str | None
    actual_equipment_code: str | None
    comparison_result: str  # MATCH, MISMATCH, NO_PLANNED, NO_ACTUAL
    has_pending_decision: bool = False
    decision_status: str | None = None
    # Plan/Actual/Decision invariant — never rewrite plan
    plan_display: str = ""
    actual_display: str = ""
    decision_display: str = ""


@dataclass
class ActiveExceptionItem:
    exception_id: str
    exception_type: str
    severity: str
    status: str
    employee_id: str
    employee_name: str
    equipment_code: str | None
    detected_at: datetime
    current_owner_id: str | None


@dataclass
class ActionRequiredItem:
    category: str  # REVIEW_REQUIRED, ACKNOWLEDGEMENT_REQUIRED, PENDING_DECISION, etc.
    description: str
    exception_id: str | None = None
    decision_id: str | None = None
    employee_id: str | None = None
    employee_name: str | None = None
    severity: str | None = None


@dataclass
class PendingDecisionItem:
    decision_id: str
    decision_type: str
    status: str
    exception_id: str
    employee_id: str
    employee_name: str
    planned_display: str
    actual_display: str
    requested_at: datetime
    authorization_status: str  # OK, BLOCKED_POLICY_DECISION


@dataclass
class ConfigurationWarning:
    warning_type: str
    message: str
    related_entity: str | None = None


@dataclass
class DashboardSnapshot:
    context: DashboardContext
    shift_summary: ShiftSummary
    roster_status: list[RosterStatusItem]
    checkpoint_status: list[CheckpointStatusItem]
    equipment_status: list[EquipmentStatusItem]
    active_exceptions: list[ActiveExceptionItem]
    action_required: list[ActionRequiredItem]
    pending_decisions: list[PendingDecisionItem]
    configuration_warnings: list[ConfigurationWarning]


# ── Helper: resolve timezone ─────────────────────────────────

def _resolve_timezone(db: Session, tenant_id: str, site_id: str | None) -> str:
    """Resolve timezone from site first, then tenant. Never from browser."""
    if site_id:
        site = db.get(Site, site_id)
        if site and site.timezone:
            return site.timezone
    tenant = db.get(Tenant, tenant_id)
    if tenant and tenant.timezone:
        return tenant.timezone
    return "Asia/Makassar"  # Metro Mining default


# ── Helper: equipment code lookup ────────────────────────────

def _equipment_code(db: Session, eq_id: str | None) -> str | None:
    if not eq_id:
        return None
    eq = db.get(Equipment, eq_id)
    return eq.equipment_code if eq else None


# ── Helper: worker name lookup ───────────────────────────────

def _worker_names(db: Session, employee_ids: list[str]) -> dict[str, str]:
    """Bulk lookup worker names."""
    if not employee_ids:
        return {}
    workers = db.scalars(select(Worker).where(Worker.id.in_(employee_ids))).all()
    return {w.id: w.name for w in workers}


# ── Helper: derive operational state ─────────────────────────

def _derive_operational_state(
    db: Session, employee_id: str, operating_date: date, shift_id: str | None
) -> str:
    """Derive presentation operational state from canonical events + checkpoint results.

    This reads from existing source-of-truth tables. NOT a second source of truth.
    """
    # Get canonical events for this employee on this operating date
    events = list(db.scalars(
        select(CanonicalAttendanceEvent).where(
            CanonicalAttendanceEvent.employee_id == employee_id,
            CanonicalAttendanceEvent.operating_date == operating_date,
        )
    ).all())

    # Filter by shift if provided
    if shift_id:
        events = [e for e in events if e.shift_id == shift_id]

    if not events:
        return OperationalState.NOT_STARTED

    event_types = {e.event_type for e in events}

    # Check checkpoint results for this employee
    checkpoints = db.scalars(
        select(CheckpointValidationResult).where(
            CheckpointValidationResult.employee_id == employee_id,
            CheckpointValidationResult.operating_date == operating_date,
        )
    ).all()
    if shift_id:
        checkpoints = [c for c in checkpoints if c.shift_id == shift_id]

    passed_types = {c.checkpoint_type for c in checkpoints
                    if c.validation_status == CheckpointValidationStatus.PASS}

    # Check for failed/attention-required checkpoints — HIGHEST PRIORITY
    has_fail = any(c.validation_status == CheckpointValidationStatus.FAIL for c in checkpoints)
    if has_fail:
        return OperationalState.ATTENTION_REQUIRED

    # Derive state from progressive checkpoint completion
    if CanonicalEventType.CHECK_OUT in event_types:
        return OperationalState.SHIFT_COMPLETE
    if CanonicalEventType.HANDOVER_START in event_types or CanonicalEventType.HANDOVER_END in event_types:
        return OperationalState.HANDOVER
    if CanonicalEventType.BREAK_IN in event_types and CanonicalEventType.BREAK_OUT not in event_types:
        return OperationalState.ON_BREAK
    if CanonicalEventType.BREAK_OUT in event_types:
        return OperationalState.RETURNED_FROM_BREAK
    if CanonicalEventType.CHECK_IN in event_types or "WORK_START" in passed_types:
        return OperationalState.WORKING
    if CanonicalEventType.EQUIPMENT_CHECK_IN in event_types or "EQUIPMENT_IN" in passed_types:
        return OperationalState.AT_EQUIPMENT
    if CanonicalEventType.BRIEFING_IN in event_types or "BRIEFING_IN" in passed_types:
        return OperationalState.BRIEFING_COMPLETE

    return OperationalState.NOT_STARTED


# ── Main Dashboard Query ─────────────────────────────────────

def get_dashboard_snapshot(
    db: Session,
    tenant_id: str,
    operating_date: date,
    shift_id: str | None = None,
    site_id: str | None = None,
) -> DashboardSnapshot:
    """Build complete dashboard snapshot for Field Supervisor.

    All queries are tenant-scoped. No cross-tenant data leakage.
    """
    generated_at = datetime.now(timezone.utc)

    # ── Context ──────────────────────────────────────────────
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    site = db.get(Site, site_id) if site_id else None
    shift = db.get(ShiftTemplate, shift_id) if shift_id else None
    tz_str = _resolve_timezone(db, tenant_id, site_id)

    # Find last event timestamp for data freshness
    last_event = db.scalar(
        select(CanonicalAttendanceEvent.created_at)
        .where(
            CanonicalAttendanceEvent.tenant_id == tenant_id,
            CanonicalAttendanceEvent.operating_date == operating_date,
        )
        .order_by(CanonicalAttendanceEvent.created_at.desc())
        .limit(1)
    )

    ctx = DashboardContext(
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        site_id=site_id,
        site_name=site.site_name if site else None,
        operating_date=operating_date,
        shift_id=shift_id,
        shift_name=shift.shift_name if shift else None,
        timezone_str=tz_str,
        generated_at=generated_at,
        last_event_at=last_event,
    )

    # ── Roster query (base) ──────────────────────────────────
    roster_q = select(RosterAssignment).where(
        RosterAssignment.tenant_id == tenant_id,
        RosterAssignment.operating_date == operating_date,
    )
    if shift_id:
        roster_q = roster_q.where(RosterAssignment.shift_id == shift_id)
    if site_id:
        roster_q = roster_q.where(RosterAssignment.site_id == site_id)

    roster_items = list(db.scalars(roster_q).all())

    # ── Shift Summary ────────────────────────────────────────
    shift_summary = _build_shift_summary(db, tenant_id, operating_date,
                                          shift_id, site_id, roster_items)

    # ── Roster Status ────────────────────────────────────────
    roster_status = _build_roster_status(db, tenant_id, operating_date,
                                          shift_id, roster_items)

    # ── Checkpoint Status ────────────────────────────────────
    checkpoint_status = _build_checkpoint_status(db, tenant_id, operating_date,
                                                   shift_id, roster_items)

    # ── Equipment Status ─────────────────────────────────────
    equipment_status = _build_equipment_status(db, tenant_id, operating_date,
                                                 shift_id, roster_items)

    # ── Active Exceptions ────────────────────────────────────
    active_exceptions = _build_active_exceptions(db, tenant_id, operating_date,
                                                   shift_id, site_id)

    # ── Pending Decisions ────────────────────────────────────
    pending_decisions = _build_pending_decisions(db, tenant_id, operating_date,
                                                   shift_id)

    # ── Action Required ──────────────────────────────────────
    action_required = _build_action_required(db, tenant_id, operating_date,
                                              shift_id, active_exceptions,
                                              pending_decisions, checkpoint_status)

    # ── Configuration Warnings ───────────────────────────────
    config_warnings = _build_configuration_warnings(db, tenant_id, site_id)

    return DashboardSnapshot(
        context=ctx,
        shift_summary=shift_summary,
        roster_status=roster_status,
        checkpoint_status=checkpoint_status,
        equipment_status=equipment_status,
        active_exceptions=active_exceptions,
        action_required=action_required,
        pending_decisions=pending_decisions,
        configuration_warnings=config_warnings,
    )


# ── Section Builders ─────────────────────────────────────────

def _build_shift_summary(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, site_id: str | None,
    roster_items: list[RosterAssignment],
) -> ShiftSummary:
    """Build shift overview metrics from roster."""
    summary = ShiftSummary()

    for r in roster_items:
        ws = r.work_status.value if r.work_status else "WORK"
        if ws == "WORK":
            summary.scheduled_work += 1
        elif ws == "REST":
            summary.scheduled_rest += 1
        elif ws == "OFFSITE":
            summary.scheduled_offsite += 1
        elif ws in ("LEAVE", "SICK"):
            summary.scheduled_leave += 1
        else:
            summary.scheduled_other += 1

    # Count present/operational (WORK employees who have at least one canonical event)
    work_employee_ids = [r.employee_id for r in roster_items
                          if r.work_status == WorkStatus.WORK]
    if work_employee_ids:
        confirmed = db.scalars(
            select(CanonicalAttendanceEvent.employee_id).where(
                CanonicalAttendanceEvent.tenant_id == tenant_id,
                CanonicalAttendanceEvent.operating_date == operating_date,
                CanonicalAttendanceEvent.employee_id.in_(work_employee_ids),
            ).distinct()
        ).all()
        summary.present_operational = len(confirmed)
        summary.not_yet_confirmed = summary.scheduled_work - summary.present_operational

    # Unresolved exceptions
    summary.unresolved_exceptions = db.scalar(
        select(func.count()).select_from(ExceptionCase).where(
            ExceptionCase.tenant_id == tenant_id,
            ExceptionCase.operating_date == operating_date,
            ExceptionCase.status.in_([ExceptionStatus.OPEN, ExceptionStatus.ACKNOWLEDGED]),
        )
    ) or 0

    # Pending decisions
    summary.pending_decisions = db.scalar(
        select(func.count()).select_from(ExceptionDecision).where(
            ExceptionDecision.tenant_id == tenant_id,
            ExceptionDecision.status == DecisionStatus.PENDING,
        )
    ) or 0

    return summary


def _build_roster_status(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, roster_items: list[RosterAssignment],
) -> list[RosterStatusItem]:
    """Build roster status with employee metadata and operational state."""
    if not roster_items:
        return []

    employee_ids = [r.employee_id for r in roster_items]

    # Bulk load worker names
    workers = {w.id: w for w in db.scalars(
        select(Worker).where(Worker.id.in_(employee_ids))
    ).all()}

    # Bulk load employee meta
    metas = {}
    for em in db.scalars(
        select(EmployeeMeta).where(
            EmployeeMeta.tenant_id == tenant_id,
            EmployeeMeta.worker_id.in_(employee_ids),
        )
    ).all():
        metas[em.worker_id] = em

    # Bulk load roles
    role_ids = [m.role_id for m in metas.values() if m.role_id]
    roles = {}
    if role_ids:
        for r in db.scalars(select(Role).where(Role.id.in_(role_ids))).all():
            roles[r.id] = r

    # Bulk load crews
    crew_ids = [m.crew_id for m in metas.values() if m.crew_id]
    crews = {}
    if crew_ids:
        for c in db.scalars(select(Crew).where(Crew.id.in_(crew_ids))).all():
            crews[c.id] = c

    # Bulk load equipment codes
    eq_ids = [r.planned_equipment_id for r in roster_items if r.planned_equipment_id]
    eq_map = {}
    if eq_ids:
        for e in db.scalars(select(Equipment).where(Equipment.id.in_(eq_ids))).all():
            eq_map[e.id] = e.equipment_code

    result = []
    for r in roster_items:
        w = workers.get(r.employee_id)
        em = metas.get(r.employee_id)
        role_name = roles[em.role_id].role_name if em and em.role_id and em.role_id in roles else None
        crew_name = crews[em.crew_id].crew_name if em and em.crew_id and em.crew_id in crews else None
        eq_code = eq_map.get(r.planned_equipment_id) if r.planned_equipment_id else None

        op_state = _derive_operational_state(db, r.employee_id, operating_date, shift_id)

        result.append(RosterStatusItem(
            employee_id=r.employee_id,
            employee_name=w.name if w else r.employee_id,
            employee_code=w.code if w else "",
            role_name=role_name,
            crew_name=crew_name,
            work_status=r.work_status.value if r.work_status else "WORK",
            shift_id=r.shift_id,
            site_status=r.site_status.value if r.site_status else None,
            planned_equipment_code=eq_code,
            operational_state=op_state,
        ))

    return result


def _build_checkpoint_status(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, roster_items: list[RosterAssignment],
) -> list[CheckpointStatusItem]:
    """Build checkpoint visibility for WORK employees."""
    work_roster = [r for r in roster_items if r.work_status == WorkStatus.WORK]
    if not work_roster:
        return []

    employee_ids = [r.employee_id for r in work_roster]
    workers = {w.id: w for w in db.scalars(
        select(Worker).where(Worker.id.in_(employee_ids))
    ).all()}

    result = []

    # Get checkpoint validation results
    cv_q = select(CheckpointValidationResult).where(
        CheckpointValidationResult.tenant_id == tenant_id,
        CheckpointValidationResult.operating_date == operating_date,
        CheckpointValidationResult.employee_id.in_(employee_ids),
    )
    if shift_id:
        cv_q = cv_q.where(CheckpointValidationResult.shift_id == shift_id)

    cv_results = list(db.scalars(cv_q).all())
    for cv in cv_results:
        w = workers.get(cv.employee_id)
        is_late = False
        late_reason = None

        # Check if FAIL with late-related reason
        if cv.validation_status == CheckpointValidationStatus.FAIL:
            if cv.reason_code and "LATE" in cv.reason_code.upper():
                is_late = True
                late_reason = cv.reason_code

        result.append(CheckpointStatusItem(
            employee_id=cv.employee_id,
            employee_name=w.name if w else cv.employee_id,
            checkpoint_type=cv.checkpoint_type,
            status=cv.validation_status.value,
            detected_timestamp=cv.detected_timestamp,
            is_late=is_late,
            late_reason=late_reason,
        ))

    # Get missing checkpoint results
    mc_q = select(MissingCheckpointResult).where(
        MissingCheckpointResult.tenant_id == tenant_id,
        MissingCheckpointResult.operating_date == operating_date,
        MissingCheckpointResult.employee_id.in_(employee_ids),
    )
    if shift_id:
        mc_q = mc_q.where(MissingCheckpointResult.shift_id == shift_id)

    mc_results = list(db.scalars(mc_q).all())
    for mc in mc_results:
        w = workers.get(mc.employee_id)
        result.append(CheckpointStatusItem(
            employee_id=mc.employee_id,
            employee_name=w.name if w else mc.employee_id,
            checkpoint_type=mc.checkpoint_type,
            status="MISSING",
        ))

    return result


def _build_equipment_status(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, roster_items: list[RosterAssignment],
) -> list[EquipmentStatusItem]:
    """Build operator × equipment status preserving plan/actual/decision invariant."""
    work_roster = [r for r in roster_items if r.work_status == WorkStatus.WORK]
    if not work_roster:
        return []

    employee_ids = [r.employee_id for r in work_roster]
    workers = {w.id: w for w in db.scalars(
        select(Worker).where(Worker.id.in_(employee_ids))
    ).all()}

    # Planned equipment mapping
    eq_ids = set()
    for r in work_roster:
        if r.planned_equipment_id:
            eq_ids.add(r.planned_equipment_id)
    eq_map = {}
    if eq_ids:
        for e in db.scalars(select(Equipment).where(Equipment.id.in_(eq_ids))).all():
            eq_map[e.id] = e

    # Actual assignments
    actual_q = select(EquipmentAssignmentActual).where(
        EquipmentAssignmentActual.tenant_id == tenant_id,
        EquipmentAssignmentActual.operating_date == operating_date,
        EquipmentAssignmentActual.employee_id.in_(employee_ids),
        EquipmentAssignmentActual.status == ActualAssignmentStatus.ACTIVE,
    )
    if shift_id:
        actual_q = actual_q.where(EquipmentAssignmentActual.shift_id == shift_id)

    actuals = {}
    for a in db.scalars(actual_q).all():
        actuals[a.employee_id] = a

    # Comparison results
    actual_ids = [a.id for a in actuals.values()]
    comparisons = {}
    if actual_ids:
        for c in db.scalars(
            select(EquipmentComparisonResult).where(
                EquipmentComparisonResult.actual_assignment_id.in_(actual_ids),
            )
        ).all():
            comparisons[c.actual_assignment_id] = c

    # Pending decisions for these employees
    decisions_q = select(ExceptionDecision).where(
        ExceptionDecision.tenant_id == tenant_id,
        ExceptionDecision.status == DecisionStatus.PENDING,
    )
    pending_decisions_map: dict[str, ExceptionDecision] = {}
    for d in db.scalars(decisions_q).all():
        if d.planned_worker_id and d.planned_worker_id in employee_ids:
            pending_decisions_map[d.planned_worker_id] = d

    # Approved decisions (to show decision status without rewriting plan)
    approved_decisions_map: dict[str, ExceptionDecision] = {}
    approved_q = select(ExceptionDecision).where(
        ExceptionDecision.tenant_id == tenant_id,
        ExceptionDecision.status == DecisionStatus.APPROVED,
    )
    for d in db.scalars(approved_q).all():
        if d.planned_worker_id and d.planned_worker_id in employee_ids:
            approved_decisions_map[d.planned_worker_id] = d

    result = []
    for r in work_roster:
        w = workers.get(r.employee_id)
        planned_eq = eq_map.get(r.planned_equipment_id) if r.planned_equipment_id else None
        planned_code = planned_eq.equipment_code if planned_eq else None

        actual = actuals.get(r.employee_id)
        actual_eq = eq_map.get(actual.equipment_id) if actual else None
        actual_code = actual_eq.equipment_code if actual else None
        if actual and not actual_eq:
            # Load actual equipment if not in planned set
            aeq = db.get(Equipment, actual.equipment_id)
            actual_code = aeq.equipment_code if aeq else None

        # Determine comparison result
        comp_result = "NO_ACTUAL"
        if actual:
            comp = comparisons.get(actual.id)
            if comp:
                comp_result = comp.comparison_result.value
            elif planned_eq and actual.equipment_id == r.planned_equipment_id:
                comp_result = "MATCH"
            elif planned_eq:
                comp_result = "MISMATCH"

        has_pending = r.employee_id in pending_decisions_map
        decision_status = None
        if has_pending:
            decision_status = "PENDING"
        elif r.employee_id in approved_decisions_map:
            decision_status = "APPROVED"

        # Plan/Actual/Decision invariant display
        plan_display = f"{w.name if w else r.employee_id} → {planned_code or '—'}"
        actual_display = actual_code or "—"
        decision_display = ""
        if decision_status == "APPROVED":
            d = approved_decisions_map.get(r.employee_id)
            decision_display = f"{d.decision_type.value} APPROVED" if d else "APPROVED"
        elif decision_status == "PENDING":
            d = pending_decisions_map.get(r.employee_id)
            decision_display = f"{d.decision_type.value} PENDING" if d else "PENDING"

        result.append(EquipmentStatusItem(
            employee_id=r.employee_id,
            employee_name=w.name if w else r.employee_id,
            planned_equipment_id=r.planned_equipment_id,
            planned_equipment_code=planned_code,
            actual_equipment_id=actual.equipment_id if actual else None,
            actual_equipment_code=actual_code,
            comparison_result=comp_result,
            has_pending_decision=has_pending,
            decision_status=decision_status,
            plan_display=plan_display,
            actual_display=actual_display,
            decision_display=decision_display,
        ))

    return result


def _build_active_exceptions(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, site_id: str | None,
) -> list[ActiveExceptionItem]:
    """Build active exception list (OPEN + ACKNOWLEDGED only)."""
    q = select(ExceptionCase).where(
        ExceptionCase.tenant_id == tenant_id,
        ExceptionCase.operating_date == operating_date,
        ExceptionCase.status.in_([ExceptionStatus.OPEN, ExceptionStatus.ACKNOWLEDGED]),
    )
    if site_id:
        q = q.where(ExceptionCase.site_id == site_id)

    q = q.order_by(
        ExceptionCase.severity.desc(),
        ExceptionCase.detected_at.asc(),
    ).limit(ACTIVE_EXCEPTIONS_LIMIT)

    exceptions = list(db.scalars(q).all())
    if not exceptions:
        return []

    employee_ids = list({e.employee_id for e in exceptions})
    workers = {w.id: w for w in db.scalars(
        select(Worker).where(Worker.id.in_(employee_ids))
    ).all()}

    # Resolve equipment codes
    eq_ids = list({e.equipment_id for e in exceptions if e.equipment_id})
    eq_map = {}
    if eq_ids:
        for e in db.scalars(select(Equipment).where(Equipment.id.in_(eq_ids))).all():
            eq_map[e.id] = e.equipment_code

    result = []
    for exc in exceptions:
        w = workers.get(exc.employee_id)
        result.append(ActiveExceptionItem(
            exception_id=exc.id,
            exception_type=exc.exception_type,
            severity=exc.severity.value if exc.severity else "WARNING",
            status=exc.status.value if exc.status else "OPEN",
            employee_id=exc.employee_id,
            employee_name=w.name if w else exc.employee_id,
            equipment_code=eq_map.get(exc.equipment_id) if exc.equipment_id else None,
            detected_at=exc.detected_at,
            current_owner_id=exc.current_owner_id,
        ))

    return result


def _build_pending_decisions(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None,
) -> list[PendingDecisionItem]:
    """Build pending decision list."""
    q = select(ExceptionDecision).where(
        ExceptionDecision.tenant_id == tenant_id,
        ExceptionDecision.status == DecisionStatus.PENDING,
    )

    decisions = list(db.scalars(q).all())
    if not decisions:
        return []

    employee_ids = list({d.requested_by for d in decisions})
    employee_ids.extend([d.planned_worker_id for d in decisions if d.planned_worker_id])
    employee_ids.extend([d.actual_worker_id for d in decisions if d.actual_worker_id])
    employee_ids = list(set(employee_ids))

    workers = {w.id: w for w in db.scalars(
        select(Worker).where(Worker.id.in_(employee_ids))
    ).all()}

    eq_ids = set()
    for d in decisions:
        if d.planned_equipment_id:
            eq_ids.add(d.planned_equipment_id)
        if d.actual_equipment_id:
            eq_ids.add(d.actual_equipment_id)
    eq_map = {}
    if eq_ids:
        for e in db.scalars(select(Equipment).where(Equipment.id.in_(eq_ids))).all():
            eq_map[e.id] = e.equipment_code

    result = []
    for d in decisions:
        pw = workers.get(d.planned_worker_id) if d.planned_worker_id else None
        aw = workers.get(d.actual_worker_id) if d.actual_worker_id else None
        pe_code = eq_map.get(d.planned_equipment_id) if d.planned_equipment_id else None
        ae_code = eq_map.get(d.actual_equipment_id) if d.actual_equipment_id else None

        plan_parts = []
        if pw:
            plan_parts.append(pw.name)
        if pe_code:
            plan_parts.append(f"→ {pe_code}")
        plan_display = " ".join(plan_parts) or "—"

        actual_parts = []
        if aw:
            actual_parts.append(aw.name)
        if ae_code:
            actual_parts.append(f"→ {ae_code}")
        actual_display = " ".join(actual_parts) or "—"

        auth_status = "OK"
        if d.authorization_policy == "BLOCKED_POLICY_DECISION":
            auth_status = "BLOCKED_POLICY_DECISION"

        result.append(PendingDecisionItem(
            decision_id=d.id,
            decision_type=d.decision_type.value,
            status=d.status.value,
            exception_id=d.exception_id,
            employee_id=d.planned_worker_id or d.requested_by,
            employee_name=pw.name if pw else d.requested_by,
            planned_display=plan_display,
            actual_display=actual_display,
            requested_at=d.requested_at,
            authorization_status=auth_status,
        ))

    return result


def _build_action_required(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None,
    active_exceptions: list[ActiveExceptionItem],
    pending_decisions: list[PendingDecisionItem],
    checkpoint_status: list[CheckpointStatusItem],
) -> list[ActionRequiredItem]:
    """Build action-required queue. Not all exceptions → action required.
    Only items requiring supervisor action.
    """
    actions: list[ActionRequiredItem] = []

    # OPEN exceptions → review required
    for exc in active_exceptions:
        if exc.status == ExceptionStatus.OPEN.value:
            actions.append(ActionRequiredItem(
                category="REVIEW_REQUIRED",
                description=f"{exc.exception_type}: {exc.employee_name}",
                exception_id=exc.exception_id,
                employee_id=exc.employee_id,
                employee_name=exc.employee_name,
                severity=exc.severity,
            ))
        elif exc.status == ExceptionStatus.ACKNOWLEDGED.value:
            actions.append(ActionRequiredItem(
                category="ACKNOWLEDGEMENT_RECEIVED",
                description=f"{exc.exception_type}: {exc.employee_name} — awaiting resolution",
                exception_id=exc.exception_id,
                employee_id=exc.employee_id,
                employee_name=exc.employee_name,
                severity=exc.severity,
            ))

    # Pending decisions
    for dec in pending_decisions:
        auth_desc = ""
        if dec.authorization_status == "BLOCKED_POLICY_DECISION":
            auth_desc = " — AUTHORIZATION POLICY NOT CONFIGURED"
        actions.append(ActionRequiredItem(
            category="PENDING_DECISION",
            description=f"{dec.decision_type}: {dec.employee_name}{auth_desc}",
            decision_id=dec.decision_id,
            employee_id=dec.employee_id,
            employee_name=dec.employee_name,
            severity="WARNING",
        ))

    # Missing checkpoints for WORK employees
    for cp in checkpoint_status:
        if cp.status == "MISSING":
            actions.append(ActionRequiredItem(
                category="MISSING_CHECKPOINT",
                description=f"MISSING {cp.checkpoint_type}: {cp.employee_name}",
                employee_id=cp.employee_id,
                employee_name=cp.employee_name,
                severity="WARNING",
            ))

    # Sort: CRITICAL first, then WARNING, then INFO
    severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    actions.sort(key=lambda a: severity_order.get(a.severity or "INFO", 3))

    return actions[:ACTION_REQUIRED_LIMIT]


def _build_configuration_warnings(
    db: Session, tenant_id: str, site_id: str | None,
) -> list[ConfigurationWarning]:
    """Detect configuration incomplete states vs employee violations."""
    warnings = []

    # Check geofence configuration
    if site_id:
        site = db.get(Site, site_id)
        if site and (site.latitude is None or site.longitude is None or site.radius_m is None):
            warnings.append(ConfigurationWarning(
                warning_type="GEOFENCE_NOT_CONFIGURED",
                message="Geofence coordinates not configured for this site. Location validation unavailable.",
                related_entity=site_id,
            ))

    # Check checkpoint policies
    checkpoint_count = db.scalar(
        select(func.count()).select_from(CheckpointPolicy).where(
            CheckpointPolicy.tenant_id == tenant_id,
            CheckpointPolicy.enabled == True,
        )
    ) or 0
    if checkpoint_count == 0:
        warnings.append(ConfigurationWarning(
            warning_type="NO_CHECKPOINT_POLICIES",
            message="No checkpoint policies configured. Checkpoint validation unavailable.",
        ))

    # Check rule version
    from app.models import RuleVersion
    rv_count = db.scalar(
        select(func.count()).select_from(RuleVersion).where(
            RuleVersion.tenant_id == tenant_id,
        )
    ) or 0
    if rv_count == 0:
        warnings.append(ConfigurationWarning(
            warning_type="NO_RULE_VERSION",
            message="No rule version configured. Operational rule evaluation unavailable.",
        ))

    return warnings


# ── Serialization for API response ───────────────────────────

def snapshot_to_dict(snapshot: DashboardSnapshot) -> dict[str, Any]:
    """Convert DashboardSnapshot to API response dict."""
    def _dt(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.isoformat()

    return {
        "context": {
            "tenant_id": snapshot.context.tenant_id,
            "tenant_name": snapshot.context.tenant_name,
            "site_id": snapshot.context.site_id,
            "site_name": snapshot.context.site_name,
            "operating_date": snapshot.context.operating_date.isoformat(),
            "shift_id": snapshot.context.shift_id,
            "shift_name": snapshot.context.shift_name,
            "timezone": snapshot.context.timezone_str,
            "generated_at": _dt(snapshot.context.generated_at),
            "last_event_at": _dt(snapshot.context.last_event_at),
        },
        "shift_summary": {
            "scheduled_work": snapshot.shift_summary.scheduled_work,
            "scheduled_rest": snapshot.shift_summary.scheduled_rest,
            "scheduled_offsite": snapshot.shift_summary.scheduled_offsite,
            "scheduled_leave": snapshot.shift_summary.scheduled_leave,
            "scheduled_other": snapshot.shift_summary.scheduled_other,
            "present_operational": snapshot.shift_summary.present_operational,
            "not_yet_confirmed": snapshot.shift_summary.not_yet_confirmed,
            "unresolved_exceptions": snapshot.shift_summary.unresolved_exceptions,
            "pending_decisions": snapshot.shift_summary.pending_decisions,
        },
        "roster_status": [
            {
                "employee_id": r.employee_id,
                "employee_name": r.employee_name,
                "employee_code": r.employee_code,
                "role_name": r.role_name,
                "crew_name": r.crew_name,
                "work_status": r.work_status,
                "shift_id": r.shift_id,
                "site_status": r.site_status,
                "planned_equipment_code": r.planned_equipment_code,
                "operational_state": r.operational_state,
            }
            for r in snapshot.roster_status
        ],
        "checkpoint_status": [
            {
                "employee_id": c.employee_id,
                "employee_name": c.employee_name,
                "checkpoint_type": c.checkpoint_type,
                "status": c.status,
                "detected_timestamp": _dt(c.detected_timestamp),
                "is_late": c.is_late,
                "late_reason": c.late_reason,
            }
            for c in snapshot.checkpoint_status
        ],
        "equipment_status": [
            {
                "employee_id": e.employee_id,
                "employee_name": e.employee_name,
                "planned_equipment_id": e.planned_equipment_id,
                "planned_equipment_code": e.planned_equipment_code,
                "actual_equipment_id": e.actual_equipment_id,
                "actual_equipment_code": e.actual_equipment_code,
                "comparison_result": e.comparison_result,
                "has_pending_decision": e.has_pending_decision,
                "decision_status": e.decision_status,
                "plan_display": e.plan_display,
                "actual_display": e.actual_display,
                "decision_display": e.decision_display,
            }
            for e in snapshot.equipment_status
        ],
        "active_exceptions": [
            {
                "exception_id": a.exception_id,
                "exception_type": a.exception_type,
                "severity": a.severity,
                "status": a.status,
                "employee_id": a.employee_id,
                "employee_name": a.employee_name,
                "equipment_code": a.equipment_code,
                "detected_at": _dt(a.detected_at),
                "current_owner_id": a.current_owner_id,
            }
            for a in snapshot.active_exceptions
        ],
        "action_required": [
            {
                "category": a.category,
                "description": a.description,
                "exception_id": a.exception_id,
                "decision_id": a.decision_id,
                "employee_id": a.employee_id,
                "employee_name": a.employee_name,
                "severity": a.severity,
            }
            for a in snapshot.action_required
        ],
        "pending_decisions": [
            {
                "decision_id": p.decision_id,
                "decision_type": p.decision_type,
                "status": p.status,
                "exception_id": p.exception_id,
                "employee_id": p.employee_id,
                "employee_name": p.employee_name,
                "planned_display": p.planned_display,
                "actual_display": p.actual_display,
                "requested_at": _dt(p.requested_at),
                "authorization_status": p.authorization_status,
            }
            for p in snapshot.pending_decisions
        ],
        "configuration_warnings": [
            {
                "warning_type": w.warning_type,
                "message": w.message,
                "related_entity": w.related_entity,
            }
            for w in snapshot.configuration_warnings
        ],
    }
