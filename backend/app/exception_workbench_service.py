"""
exception_workbench_service.py — MM-M4C Exception & Decision Workbench.

Operational supervisor workbench for reviewing and acting on attendance
exceptions and decisions. Wraps M3 engines (exception_engine, decision_engine,
review_service) with workbench-specific aggregation, filtering, and context.

Architecture:
    M3 exception_engine → lifecycle (acknowledge, resolve, waive)
    M3 decision_engine  → decisions (request, approve, reject, cancel)
    M3 review_service   → evidence, notes, ownership, timeline
    M4C workbench       → enriched case list, full case detail, combined timeline

Principles:
    - Pure read-only aggregation + delegation to M3 engines
    - No duplicated business logic
    - All queries tenant-scoped
    - Approval ≠ Resolution (separate actions)
    - Waiver ≠ Event Deletion (history preserved)
    - Authorization cannot be bypassed
    - No payroll/disciplinary/HSE consequences
"""

from datetime import date, datetime, timezone
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from app.models import (
    ExceptionCase,
    ExceptionAction,
    ExceptionActionType,
    ExceptionEvidence,
    ExceptionEvidenceType,
    ExceptionStatus,
    ExceptionSeverity,
    ExceptionSourceType,
    ExceptionDecision,
    ExceptionDecisionAction,
    DecisionType,
    DecisionStatus,
    EmployeeMeta,
    Worker,
    Equipment,
    EquipmentStatus,
    Crew,
    Role,
    RosterAssignment,
    ShiftTemplate,
    RuleVersion,
    uid,
)

# Reuse M3 engines — never duplicate their logic
from app.exception_engine import (
    acknowledge_exception,
    resolve_exception,
    waive_exception,
    get_exception,
    get_action_history,
)
from app.decision_engine import (
    request_decision,
    approve_decision,
    reject_decision,
    cancel_decision,
    get_decision,
    get_decisions_for_exception,
    get_decision_history,
    AuthorizationBlocked,
    DecisionValidationFailed,
    DuplicateActiveDecision,
)
from app.review_service import (
    add_evidence,
    add_review_note,
    assign_reviewer,
    get_evidence,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Data Classes ─────────────────────────────────────────────

@dataclass
class WorkbenchContext:
    tenant_id: str
    tenant_name: str
    timezone_str: str
    generated_at: datetime
    total_count: int
    filtered_count: int
    active_count: int
    pending_decision_count: int


@dataclass
class CaseListItem:
    case_id: str
    exception_type: str
    severity: str
    status: str
    employee_id: str
    employee_name: str
    employee_code: str
    role_name: str
    crew_name: str
    equipment_id: str | None
    equipment_code: str | None
    operating_date: date
    shift_id: str | None
    shift_name: str | None
    detected_at: datetime
    current_owner_id: str | None
    owner_name: str | None
    has_pending_decision: bool
    pending_decision_type: str | None
    age_minutes: int | None
    source_type: str


@dataclass
class WorkbenchResult:
    context: WorkbenchContext
    items: list[CaseListItem]


@dataclass
class CaseSummary:
    case_id: str
    exception_type: str
    severity: str
    status: str
    operating_date: date
    shift_id: str | None
    shift_name: str | None
    detected_at: datetime
    opened_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    waived_at: datetime | None
    employee_id: str
    employee_name: str
    employee_code: str
    role_name: str
    crew_name: str
    equipment_id: str | None
    equipment_code: str | None
    current_owner_id: str | None
    owner_name: str | None
    source_type: str
    source_id: str
    rule_version_id: str | None
    rule_version_name: str | None


@dataclass
class PlanVsActual:
    planned_worker_id: str | None
    planned_worker_name: str | None
    planned_equipment_id: str | None
    planned_equipment_code: str | None
    actual_worker_id: str | None
    actual_worker_name: str | None
    actual_equipment_id: str | None
    actual_equipment_code: str | None
    discrepancy_type: str | None


@dataclass
class DecisionInfo:
    decision_id: str
    decision_type: str
    status: str
    requested_by: str
    requested_by_name: str | None
    requested_at: datetime
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    reason_text: str | None
    reason_code: str | None
    authorization_policy: str | None
    authorization_blocked: bool
    planned_worker_id: str | None
    planned_worker_name: str | None
    planned_equipment_id: str | None
    planned_equipment_code: str | None
    actual_worker_id: str | None
    actual_worker_name: str | None
    actual_equipment_id: str | None
    actual_equipment_code: str | None


@dataclass
class TimelineEntry:
    timestamp: datetime
    entry_type: str  # ACTION, EVIDENCE, DECISION, DECISION_ACTION
    description: str
    actor_id: str | None
    actor_name: str | None
    details: dict = field(default_factory=dict)


@dataclass
class CaseDetail:
    summary: CaseSummary
    plan_vs_actual: PlanVsActual | None
    decisions: list[DecisionInfo]
    evidence: list[dict]
    actions: list[dict]
    timeline: list[TimelineEntry]
    available_actions: list[str]


# ── Workbench Case List ──────────────────────────────────────

ACTIVE_STATUSES = [ExceptionStatus.OPEN, ExceptionStatus.ACKNOWLEDGED]
TERMINAL_STATUSES = [ExceptionStatus.RESOLVED, ExceptionStatus.WAIVED]


def get_workbench_queue(
    db: Session,
    tenant_id: str,
    *,
    active_only: bool = True,
    status: str | None = None,
    severity: str | None = None,
    exception_type: str | None = None,
    employee_search: str | None = None,
    crew_id: str | None = None,
    equipment_search: str | None = None,
    operating_date: date | None = None,
    operating_date_from: date | None = None,
    operating_date_to: date | None = None,
    shift_id: str | None = None,
    owner_id: str | None = None,
    decision_status: str | None = None,
    sort_by: str = "severity",
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 50,
) -> WorkbenchResult:
    """Get filtered, paginated exception workbench queue.

    Default: active_only=True returns OPEN + ACKNOWLEDGED cases.
    Set active_only=False or pass specific status to include terminal cases.
    """
    now = _utcnow()

    # Base query — tenant scoped
    q = db.query(ExceptionCase).filter(ExceptionCase.tenant_id == tenant_id)

    # Status filter
    if status:
        try:
            status_enum = ExceptionStatus(status)
            q = q.filter(ExceptionCase.status == status_enum)
        except ValueError:
            pass
    elif active_only:
        q = q.filter(ExceptionCase.status.in_(ACTIVE_STATUSES))

    # Severity filter
    if severity:
        try:
            sev_enum = ExceptionSeverity(severity)
            q = q.filter(ExceptionCase.severity == sev_enum)
        except ValueError:
            pass

    # Exception type filter
    if exception_type:
        q = q.filter(ExceptionCase.exception_type == exception_type)

    # Employee search (name or code)
    if employee_search:
        worker_ids = [
            w.id for w in db.query(Worker.id).filter(
                and_(
                    Worker.tenant_id == tenant_id,
                    or_(
                        Worker.name.ilike(f"%{employee_search}%"),
                        Worker.code.ilike(f"%{employee_search}%"),
                    ),
                )
            ).all()
        ]
        if worker_ids:
            q = q.filter(ExceptionCase.employee_id.in_(worker_ids))
        else:
            # No matching workers — return empty
            return WorkbenchResult(
                context=WorkbenchContext(
                    tenant_id=tenant_id, tenant_name="", timezone_str="Asia/Makassar",
                    generated_at=now, total_count=0, filtered_count=0,
                    active_count=0, pending_decision_count=0,
                ),
                items=[],
            )

    # Equipment search
    if equipment_search:
        equip_ids = [
            e.id for e in db.query(Equipment.id).filter(
                and_(
                    Equipment.tenant_id == tenant_id,
                    Equipment.equipment_code.ilike(f"%{equipment_search}%"),
                )
            ).all()
        ]
        if equip_ids:
            q = q.filter(ExceptionCase.equipment_id.in_(equip_ids))
        else:
            return WorkbenchResult(
                context=WorkbenchContext(
                    tenant_id=tenant_id, tenant_name="", timezone_str="Asia/Makassar",
                    generated_at=now, total_count=0, filtered_count=0,
                    active_count=0, pending_decision_count=0,
                ),
                items=[],
            )

    # Crew filter
    if crew_id:
        crew_worker_ids = [
            w.worker_id for w in db.query(EmployeeMeta).filter(
                and_(
                    EmployeeMeta.tenant_id == tenant_id,
                    EmployeeMeta.crew_id == crew_id,
                )
            ).all()
        ]
        if crew_worker_ids:
            q = q.filter(ExceptionCase.employee_id.in_(crew_worker_ids))

    # Date filters
    if operating_date:
        q = q.filter(ExceptionCase.operating_date == operating_date)
    if operating_date_from:
        q = q.filter(ExceptionCase.operating_date >= operating_date_from)
    if operating_date_to:
        q = q.filter(ExceptionCase.operating_date <= operating_date_to)

    # Shift filter
    if shift_id:
        q = q.filter(ExceptionCase.shift_id == shift_id)

    # Owner filter
    if owner_id:
        q = q.filter(ExceptionCase.current_owner_id == owner_id)

    # Decision status filter
    if decision_status:
        try:
            ds_enum = DecisionStatus(decision_status)
            matching_exception_ids = [
                d.exception_id for d in db.query(ExceptionDecision.exception_id).filter(
                    and_(
                        ExceptionDecision.tenant_id == tenant_id,
                        ExceptionDecision.status == ds_enum,
                    )
                ).all()
            ]
            if matching_exception_ids:
                q = q.filter(ExceptionCase.id.in_(matching_exception_ids))
            else:
                return WorkbenchResult(
                    context=WorkbenchContext(
                        tenant_id=tenant_id, tenant_name="", timezone_str="Asia/Makassar",
                        generated_at=now, total_count=0, filtered_count=0,
                        active_count=0, pending_decision_count=0,
                    ),
                    items=[],
                )
        except ValueError:
            pass

    # Count before pagination
    total_count = db.query(func.count(ExceptionCase.id)).filter(
        ExceptionCase.tenant_id == tenant_id
    ).scalar() or 0

    active_count = db.query(func.count(ExceptionCase.id)).filter(
        and_(
            ExceptionCase.tenant_id == tenant_id,
            ExceptionCase.status.in_(ACTIVE_STATUSES),
        )
    ).scalar() or 0

    pending_decision_count = db.query(func.count(ExceptionDecision.id)).filter(
        and_(
            ExceptionDecision.tenant_id == tenant_id,
            ExceptionDecision.status == DecisionStatus.PENDING,
        )
    ).scalar() or 0

    # Sorting
    sort_column = _get_sort_column(sort_by)
    if sort_dir == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    # Secondary sort for deterministic ordering
    q = q.order_by(ExceptionCase.detected_at.desc())

    # Pagination
    cases = q.offset(offset).limit(limit).all()
    filtered_count = len(cases)  # approximate for this page

    # Build list items with enriched context
    items = []
    for case in cases:
        items.append(_enrich_case_list_item(db, tenant_id, case, now))

    # Get tenant name
    from app.models import Tenant
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    tenant_name = tenant.name if tenant else ""

    return WorkbenchResult(
        context=WorkbenchContext(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            timezone_str="Asia/Makassar",
            generated_at=now,
            total_count=total_count,
            filtered_count=filtered_count,
            active_count=active_count,
            pending_decision_count=pending_decision_count,
        ),
        items=items,
    )


def _enrich_case_list_item(
    db: Session, tenant_id: str, case: ExceptionCase, now: datetime
) -> CaseListItem:
    """Enrich a case with worker, equipment, owner, and decision context."""
    # Worker info
    worker = db.query(Worker).filter(
        and_(Worker.id == case.employee_id, Worker.tenant_id == tenant_id)
    ).first()
    employee_name = worker.name if worker else "Unknown"
    employee_code = worker.code if worker else ""

    # EmployeeMeta for role/crew
    meta = db.query(EmployeeMeta).filter(
        and_(
            EmployeeMeta.tenant_id == tenant_id,
            EmployeeMeta.worker_id == case.employee_id,
        )
    ).first()
    role_name = ""
    crew_name = ""
    if meta:
        if meta.role_id:
            role = db.query(Role).filter(Role.id == meta.role_id).first()
            role_name = role.role_name if role else ""
        if meta.crew_id:
            crew = db.query(Crew).filter(Crew.id == meta.crew_id).first()
            crew_name = crew.crew_name if crew else ""

    # Equipment
    equipment_code = None
    if case.equipment_id:
        equip = db.query(Equipment).filter(
            and_(Equipment.id == case.equipment_id, Equipment.tenant_id == tenant_id)
        ).first()
        equipment_code = equip.equipment_code if equip else None

    # Shift name
    shift_name = None
    if case.shift_id:
        shift = db.query(ShiftTemplate).filter(ShiftTemplate.id == case.shift_id).first()
        shift_name = shift.shift_name if shift else None

    # Owner name
    owner_name = None
    if case.current_owner_id:
        owner = db.query(Worker).filter(Worker.id == case.current_owner_id).first()
        if owner:
            owner_name = owner.name
        else:
            # Might be a User, not Worker
            from app.models import User
            user = db.query(User).filter(User.id == case.current_owner_id).first()
            owner_name = user.display_name if user else None

    # Pending decision
    pending_decision = db.query(ExceptionDecision).filter(
        and_(
            ExceptionDecision.tenant_id == tenant_id,
            ExceptionDecision.exception_id == case.id,
            ExceptionDecision.status == DecisionStatus.PENDING,
        )
    ).first()

    # Age calculation
    age_minutes = None
    if case.detected_at:
        delta = now - case.detected_at
        age_minutes = int(delta.total_seconds() / 60)

    return CaseListItem(
        case_id=case.id,
        exception_type=case.exception_type,
        severity=case.severity.value if hasattr(case.severity, 'value') else str(case.severity),
        status=case.status.value if hasattr(case.status, 'value') else str(case.status),
        employee_id=case.employee_id,
        employee_name=employee_name,
        employee_code=employee_code,
        role_name=role_name,
        crew_name=crew_name,
        equipment_id=case.equipment_id,
        equipment_code=equipment_code,
        operating_date=case.operating_date,
        shift_id=case.shift_id,
        shift_name=shift_name,
        detected_at=case.detected_at,
        current_owner_id=case.current_owner_id,
        owner_name=owner_name,
        has_pending_decision=pending_decision is not None,
        pending_decision_type=(
            pending_decision.decision_type.value
            if pending_decision and hasattr(pending_decision.decision_type, 'value')
            else None
        ),
        age_minutes=age_minutes,
        source_type=case.source_type,
    )


def _get_sort_column(sort_by: str):
    """Map sort_by string to SQLAlchemy column."""
    mapping = {
        "severity": ExceptionCase.severity,
        "detected_at": ExceptionCase.detected_at,
        "employee": ExceptionCase.employee_id,
        "equipment": ExceptionCase.equipment_id,
        "status": ExceptionCase.status,
        "exception_type": ExceptionCase.exception_type,
        "operating_date": ExceptionCase.operating_date,
    }
    return mapping.get(sort_by, ExceptionCase.severity)


# ── Case Detail ──────────────────────────────────────────────

def get_case_detail(
    db: Session,
    tenant_id: str,
    exception_id: str,
) -> CaseDetail | None:
    """Get full case detail for the workbench.

    Returns None if not found or tenant mismatch.
    Includes: summary, plan vs actual, decisions, evidence, actions, timeline,
    and available actions based on current state.
    """
    case = db.query(ExceptionCase).filter(
        and_(
            ExceptionCase.id == exception_id,
            ExceptionCase.tenant_id == tenant_id,
        )
    ).first()
    if not case:
        return None

    now = _utcnow()

    # Build summary
    summary = _build_case_summary(db, tenant_id, case)

    # Build plan vs actual (for equipment/operator exceptions)
    pva = _build_plan_vs_actual(db, tenant_id, case)

    # Build decisions
    decisions = _build_decisions(db, tenant_id, case)

    # Build evidence
    evidence_records = db.query(ExceptionEvidence).filter(
        and_(
            ExceptionEvidence.exception_id == exception_id,
            ExceptionEvidence.tenant_id == tenant_id,
        )
    ).order_by(ExceptionEvidence.created_at.asc()).all()
    evidence = [_evidence_to_dict(ev) for ev in evidence_records]

    # Build actions
    action_records = db.query(ExceptionAction).filter(
        and_(
            ExceptionAction.exception_id == exception_id,
            ExceptionAction.tenant_id == tenant_id,
        )
    ).order_by(ExceptionAction.action_timestamp.asc()).all()
    actions = [_action_to_dict(a) for a in action_records]

    # Build combined timeline
    timeline = _build_combined_timeline(db, tenant_id, case, action_records, decisions)

    # Determine available actions
    available_actions = _determine_available_actions(case, decisions)

    return CaseDetail(
        summary=summary,
        plan_vs_actual=pva,
        decisions=decisions,
        evidence=evidence,
        actions=actions,
        timeline=timeline,
        available_actions=available_actions,
    )


def _build_case_summary(db: Session, tenant_id: str, case: ExceptionCase) -> CaseSummary:
    """Build case summary with enriched context."""
    worker = db.query(Worker).filter(
        and_(Worker.id == case.employee_id, Worker.tenant_id == tenant_id)
    ).first()

    meta = db.query(EmployeeMeta).filter(
        and_(
            EmployeeMeta.tenant_id == tenant_id,
            EmployeeMeta.worker_id == case.employee_id,
        )
    ).first()

    role_name = ""
    crew_name = ""
    if meta:
        if meta.role_id:
            role = db.query(Role).filter(Role.id == meta.role_id).first()
            role_name = role.role_name if role else ""
        if meta.crew_id:
            crew = db.query(Crew).filter(Crew.id == meta.crew_id).first()
            crew_name = crew.crew_name if crew else ""

    equipment_code = None
    if case.equipment_id:
        equip = db.query(Equipment).filter(
            and_(Equipment.id == case.equipment_id, Equipment.tenant_id == tenant_id)
        ).first()
        equipment_code = equip.equipment_code if equip else None

    shift_name = None
    if case.shift_id:
        shift = db.query(ShiftTemplate).filter(ShiftTemplate.id == case.shift_id).first()
        shift_name = shift.shift_name if shift else None

    owner_name = None
    if case.current_owner_id:
        owner = db.query(Worker).filter(Worker.id == case.current_owner_id).first()
        if owner:
            owner_name = owner.name
        else:
            from app.models import User
            user = db.query(User).filter(User.id == case.current_owner_id).first()
            owner_name = user.display_name if user else None

    rule_version_name = None
    if case.rule_version_id:
        rv = db.query(RuleVersion).filter(RuleVersion.id == case.rule_version_id).first()
        rule_version_name = rv.version_label if rv else None

    return CaseSummary(
        case_id=case.id,
        exception_type=case.exception_type,
        severity=case.severity.value if hasattr(case.severity, 'value') else str(case.severity),
        status=case.status.value if hasattr(case.status, 'value') else str(case.status),
        operating_date=case.operating_date,
        shift_id=case.shift_id,
        shift_name=shift_name,
        detected_at=case.detected_at,
        opened_at=case.opened_at,
        acknowledged_at=case.acknowledged_at,
        resolved_at=case.resolved_at,
        waived_at=case.waived_at,
        employee_id=case.employee_id,
        employee_name=worker.name if worker else "Unknown",
        employee_code=worker.code if worker else "",
        role_name=role_name,
        crew_name=crew_name,
        equipment_id=case.equipment_id,
        equipment_code=equipment_code,
        current_owner_id=case.current_owner_id,
        owner_name=owner_name,
        source_type=case.source_type,
        source_id=case.source_id,
        rule_version_id=case.rule_version_id,
        rule_version_name=rule_version_name,
    )


def _build_plan_vs_actual(
    db: Session, tenant_id: str, case: ExceptionCase
) -> PlanVsActual | None:
    """Build plan vs actual for equipment/operator exceptions."""
    # Only relevant for equipment-related exceptions
    equipment_types = {"EQUIPMENT_MISMATCH", "OPERATOR_SUBSTITUTION", "EQUIPMENT_SUBSTITUTION"}
    if case.exception_type not in equipment_types:
        return None

    # Look up related decision for planned/actual references
    decisions = db.query(ExceptionDecision).filter(
        and_(
            ExceptionDecision.tenant_id == tenant_id,
            ExceptionDecision.exception_id == case.id,
        )
    ).all()

    planned_worker_id = None
    planned_worker_name = None
    planned_equipment_id = None
    planned_equipment_code = None
    actual_worker_id = None
    actual_worker_name = None
    actual_equipment_id = None
    actual_equipment_code = None
    discrepancy_type = None

    if decisions:
        d = decisions[0]  # Use first decision for plan/actual
        planned_worker_id = d.planned_worker_id
        planned_equipment_id = d.planned_equipment_id
        actual_worker_id = d.actual_worker_id
        actual_equipment_id = d.actual_equipment_id

    # Fallback: use case equipment_id as actual
    if not actual_equipment_id and case.equipment_id:
        actual_equipment_id = case.equipment_id

    # Resolve names
    if planned_worker_id:
        w = db.query(Worker).filter(Worker.id == planned_worker_id).first()
        planned_worker_name = w.name if w else None
    if planned_equipment_id:
        e = db.query(Equipment).filter(Equipment.id == planned_equipment_id).first()
        planned_equipment_code = e.equipment_code if e else None
    if actual_worker_id:
        w = db.query(Worker).filter(Worker.id == actual_worker_id).first()
        actual_worker_name = w.name if w else None
    if actual_equipment_id:
        e = db.query(Equipment).filter(Equipment.id == actual_equipment_id).first()
        actual_equipment_code = e.equipment_code if e else None

    # If no plan/actual data at all, return None
    if not any([planned_worker_id, planned_equipment_id, actual_worker_id, actual_equipment_id]):
        return None

    return PlanVsActual(
        planned_worker_id=planned_worker_id,
        planned_worker_name=planned_worker_name,
        planned_equipment_id=planned_equipment_id,
        planned_equipment_code=planned_equipment_code,
        actual_worker_id=actual_worker_id,
        actual_worker_name=actual_worker_name,
        actual_equipment_id=actual_equipment_id,
        actual_equipment_code=actual_equipment_code,
        discrepancy_type=discrepancy_type,
    )


def _build_decisions(
    db: Session, tenant_id: str, case: ExceptionCase
) -> list[DecisionInfo]:
    """Build decision info list for a case."""
    decisions = db.query(ExceptionDecision).filter(
        and_(
            ExceptionDecision.tenant_id == tenant_id,
            ExceptionDecision.exception_id == case.id,
        )
    ).order_by(ExceptionDecision.created_at.asc()).all()

    result = []
    for d in decisions:
        # Resolve names
        requested_by_name = _resolve_actor_name(db, d.requested_by)
        decided_by_name = _resolve_actor_name(db, d.decided_by) if d.decided_by else None

        pw_name = None
        pe_code = None
        aw_name = None
        ae_code = None

        if d.planned_worker_id:
            w = db.query(Worker).filter(Worker.id == d.planned_worker_id).first()
            pw_name = w.name if w else None
        if d.planned_equipment_id:
            e = db.query(Equipment).filter(Equipment.id == d.planned_equipment_id).first()
            pe_code = e.equipment_code if e else None
        if d.actual_worker_id:
            w = db.query(Worker).filter(Worker.id == d.actual_worker_id).first()
            aw_name = w.name if w else None
        if d.actual_equipment_id:
            e = db.query(Equipment).filter(Equipment.id == d.actual_equipment_id).first()
            ae_code = e.equipment_code if e else None

        auth_blocked = d.authorization_policy == "BLOCKED_POLICY_DECISION"

        result.append(DecisionInfo(
            decision_id=d.id,
            decision_type=d.decision_type.value if hasattr(d.decision_type, 'value') else str(d.decision_type),
            status=d.status.value if hasattr(d.status, 'value') else str(d.status),
            requested_by=d.requested_by,
            requested_by_name=requested_by_name,
            requested_at=d.requested_at,
            decided_by=d.decided_by,
            decided_by_name=decided_by_name,
            decided_at=d.decided_at,
            reason_text=d.reason_text,
            reason_code=d.reason_code,
            authorization_policy=d.authorization_policy,
            authorization_blocked=auth_blocked,
            planned_worker_id=d.planned_worker_id,
            planned_worker_name=pw_name,
            planned_equipment_id=d.planned_equipment_id,
            planned_equipment_code=pe_code,
            actual_worker_id=d.actual_worker_id,
            actual_worker_name=aw_name,
            actual_equipment_id=d.actual_equipment_id,
            actual_equipment_code=ae_code,
        ))

    return result


def _build_combined_timeline(
    db: Session,
    tenant_id: str,
    case: ExceptionCase,
    actions: list[ExceptionAction],
    decisions: list[DecisionInfo],
) -> list[TimelineEntry]:
    """Build combined chronological timeline from actions, evidence, and decisions."""
    entries: list[TimelineEntry] = []

    # Exception actions
    for a in actions:
        actor_name = _resolve_actor_name(db, a.actor_user_id)
        action_val = a.action_type.value if hasattr(a.action_type, 'value') else str(a.action_type)
        entries.append(TimelineEntry(
            timestamp=a.action_timestamp,
            entry_type="ACTION",
            description=f"Exception {action_val}",
            actor_id=a.actor_user_id,
            actor_name=actor_name,
            details={
                "action_type": action_val,
                "previous_status": a.previous_status.value if hasattr(a.previous_status, 'value') else str(a.previous_status),
                "new_status": a.new_status.value if hasattr(a.new_status, 'value') else str(a.new_status),
                "reason": a.reason,
                "note": a.note,
            },
        ))

    # Decision actions
    for d in decisions:
        decision_actions = db.query(ExceptionDecisionAction).filter(
            and_(
                ExceptionDecisionAction.tenant_id == tenant_id,
                ExceptionDecisionAction.decision_id == d.decision_id,
            )
        ).order_by(ExceptionDecisionAction.action_timestamp.asc()).all()

        for da in decision_actions:
            actor_name = _resolve_actor_name(db, da.actor_user_id)
            entries.append(TimelineEntry(
                timestamp=da.action_timestamp,
                entry_type="DECISION_ACTION",
                description=f"Decision {da.action_type}: {d.decision_type}",
                actor_id=da.actor_user_id,
                actor_name=actor_name,
                details={
                    "decision_id": d.decision_id,
                    "decision_type": d.decision_type,
                    "action_type": da.action_type,
                    "previous_status": da.previous_status.value if hasattr(da.previous_status, 'value') else str(da.previous_status),
                    "new_status": da.new_status.value if hasattr(da.new_status, 'value') else str(da.new_status),
                    "reason": da.reason,
                    "authorization_result": da.authorization_result,
                },
            ))

    # Sort by timestamp
    entries.sort(key=lambda e: e.timestamp)
    return entries


def _determine_available_actions(
    case: ExceptionCase, decisions: list[DecisionInfo]
) -> list[str]:
    """Determine available actions based on case status and decisions."""
    actions = []
    status = case.status

    if status == ExceptionStatus.OPEN:
        actions.append("ACKNOWLEDGE")
        actions.append("RESOLVE")
        actions.append("WAIVE")
        actions.append("ASSIGN")
        actions.append("ADD_NOTE")
        actions.append("REQUEST_DECISION")

    elif status == ExceptionStatus.ACKNOWLEDGED:
        actions.append("RESOLVE")
        actions.append("WAIVE")
        actions.append("ASSIGN")
        actions.append("ADD_NOTE")
        actions.append("REQUEST_DECISION")

    # Decision actions (only if PENDING decisions exist)
    for d in decisions:
        if d.status == "PENDING":
            if not d.authorization_blocked:
                actions.append(f"APPROVE_DECISION:{d.decision_id}")
                actions.append(f"REJECT_DECISION:{d.decision_id}")
            actions.append(f"CANCEL_DECISION:{d.decision_id}")

    # Terminal states — no lifecycle actions
    if status in (ExceptionStatus.RESOLVED, ExceptionStatus.WAIVED):
        pass  # No actions available

    return actions


# ── Serialization Helpers ────────────────────────────────────

def _evidence_to_dict(ev: ExceptionEvidence) -> dict:
    """Convert ExceptionEvidence to dict."""
    return {
        "id": ev.id,
        "evidence_type": ev.evidence_type.value if hasattr(ev.evidence_type, 'value') else str(ev.evidence_type),
        "source_type": ev.source_type,
        "source_id": ev.source_id,
        "captured_at": ev.captured_at.isoformat() if ev.captured_at else None,
        "added_by": ev.added_by,
        "is_system_generated": ev.is_system_generated,
        "note": ev.note,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


def _action_to_dict(a: ExceptionAction) -> dict:
    """Convert ExceptionAction to dict."""
    return {
        "id": a.id,
        "action_type": a.action_type.value if hasattr(a.action_type, 'value') else str(a.action_type),
        "actor_user_id": a.actor_user_id,
        "action_timestamp": a.action_timestamp.isoformat() if a.action_timestamp else None,
        "previous_status": a.previous_status.value if hasattr(a.previous_status, 'value') else str(a.previous_status),
        "new_status": a.new_status.value if hasattr(a.new_status, 'value') else str(a.new_status),
        "reason": a.reason,
        "note": a.note,
        "evidence_ref": a.evidence_ref,
    }


def _resolve_actor_name(db: Session, actor_id: str | None) -> str | None:
    """Resolve actor ID to display name (Worker or User)."""
    if not actor_id:
        return None
    worker = db.query(Worker).filter(Worker.id == actor_id).first()
    if worker:
        return worker.name
    from app.models import User
    user = db.query(User).filter(User.id == actor_id).first()
    if user:
        return user.display_name
    return None


# ── Serialization for API ────────────────────────────────────

def workbench_result_to_dict(result: WorkbenchResult) -> dict:
    """Serialize WorkbenchResult for API response."""
    return {
        "context": {
            "tenant_id": result.context.tenant_id,
            "tenant_name": result.context.tenant_name,
            "timezone": result.context.timezone_str,
            "generated_at": result.context.generated_at.isoformat(),
            "total_count": result.context.total_count,
            "filtered_count": result.context.filtered_count,
            "active_count": result.context.active_count,
            "pending_decision_count": result.context.pending_decision_count,
        },
        "items": [_case_item_to_dict(i) for i in result.items],
    }


def _case_item_to_dict(item: CaseListItem) -> dict:
    return {
        "case_id": item.case_id,
        "exception_type": item.exception_type,
        "severity": item.severity,
        "status": item.status,
        "employee_id": item.employee_id,
        "employee_name": item.employee_name,
        "employee_code": item.employee_code,
        "role_name": item.role_name,
        "crew_name": item.crew_name,
        "equipment_id": item.equipment_id,
        "equipment_code": item.equipment_code,
        "operating_date": str(item.operating_date),
        "shift_id": item.shift_id,
        "shift_name": item.shift_name,
        "detected_at": item.detected_at.isoformat() if item.detected_at else None,
        "current_owner_id": item.current_owner_id,
        "owner_name": item.owner_name,
        "has_pending_decision": item.has_pending_decision,
        "pending_decision_type": item.pending_decision_type,
        "age_minutes": item.age_minutes,
        "source_type": item.source_type,
    }


def case_detail_to_dict(detail: CaseDetail) -> dict:
    """Serialize CaseDetail for API response."""
    result = {
        "summary": {
            "case_id": detail.summary.case_id,
            "exception_type": detail.summary.exception_type,
            "severity": detail.summary.severity,
            "status": detail.summary.status,
            "operating_date": str(detail.summary.operating_date),
            "shift_id": detail.summary.shift_id,
            "shift_name": detail.summary.shift_name,
            "detected_at": detail.summary.detected_at.isoformat() if detail.summary.detected_at else None,
            "opened_at": detail.summary.opened_at.isoformat() if detail.summary.opened_at else None,
            "acknowledged_at": detail.summary.acknowledged_at.isoformat() if detail.summary.acknowledged_at else None,
            "resolved_at": detail.summary.resolved_at.isoformat() if detail.summary.resolved_at else None,
            "waived_at": detail.summary.waived_at.isoformat() if detail.summary.waived_at else None,
            "employee_id": detail.summary.employee_id,
            "employee_name": detail.summary.employee_name,
            "employee_code": detail.summary.employee_code,
            "role_name": detail.summary.role_name,
            "crew_name": detail.summary.crew_name,
            "equipment_id": detail.summary.equipment_id,
            "equipment_code": detail.summary.equipment_code,
            "current_owner_id": detail.summary.current_owner_id,
            "owner_name": detail.summary.owner_name,
            "source_type": detail.summary.source_type,
            "source_id": detail.summary.source_id,
            "rule_version_id": detail.summary.rule_version_id,
            "rule_version_name": detail.summary.rule_version_name,
        },
        "plan_vs_actual": None,
        "decisions": [],
        "evidence": detail.evidence,
        "actions": detail.actions,
        "timeline": [],
        "available_actions": detail.available_actions,
    }

    if detail.plan_vs_actual:
        pva = detail.plan_vs_actual
        result["plan_vs_actual"] = {
            "planned_worker_id": pva.planned_worker_id,
            "planned_worker_name": pva.planned_worker_name,
            "planned_equipment_id": pva.planned_equipment_id,
            "planned_equipment_code": pva.planned_equipment_code,
            "actual_worker_id": pva.actual_worker_id,
            "actual_worker_name": pva.actual_worker_name,
            "actual_equipment_id": pva.actual_equipment_id,
            "actual_equipment_code": pva.actual_equipment_code,
            "discrepancy_type": pva.discrepancy_type,
        }

    for d in detail.decisions:
        result["decisions"].append({
            "decision_id": d.decision_id,
            "decision_type": d.decision_type,
            "status": d.status,
            "requested_by": d.requested_by,
            "requested_by_name": d.requested_by_name,
            "requested_at": d.requested_at.isoformat() if d.requested_at else None,
            "decided_by": d.decided_by,
            "decided_by_name": d.decided_by_name,
            "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            "reason_text": d.reason_text,
            "reason_code": d.reason_code,
            "authorization_policy": d.authorization_policy,
            "authorization_blocked": d.authorization_blocked,
            "planned_worker_id": d.planned_worker_id,
            "planned_worker_name": d.planned_worker_name,
            "planned_equipment_id": d.planned_equipment_id,
            "planned_equipment_code": d.planned_equipment_code,
            "actual_worker_id": d.actual_worker_id,
            "actual_worker_name": d.actual_worker_name,
            "actual_equipment_id": d.actual_equipment_id,
            "actual_equipment_code": d.actual_equipment_code,
        })

    for t in detail.timeline:
        result["timeline"].append({
            "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            "entry_type": t.entry_type,
            "description": t.description,
            "actor_id": t.actor_id,
            "actor_name": t.actor_name,
            "details": t.details,
        })

    return result
