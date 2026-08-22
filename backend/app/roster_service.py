"""
roster_service.py — MM-M4B Roster & Attendance Operational View.

Tenant-scoped aggregation service for the Roster Board and Worker Detail views.
Aggregates existing M0–M3 data into structured roster board snapshots.
No new database tables — pure read model.

Product principle: ACTIONABILITY > INFORMATION > ANALYTICS > DECORATION.

Roster context: tenant + operating_date (+ optional shift/site/crew filters)
Timezone: resolved from site/tenant configuration, NEVER from browser/server.

NIGHT shift: operating_date = date when shift started.
Events before 07:00 for cross-midnight shifts belong to the same operating_date.
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
    RosterPolicy,
)

from app.dashboard_service import OperationalState, _resolve_timezone, _derive_operational_state


# ── Data Classes ─────────────────────────────────────────────

@dataclass(frozen=True)
class RosterBoardContext:
    tenant_id: str
    tenant_name: str
    site_id: str | None
    site_name: str | None
    operating_date: date
    shift_id: str | None
    shift_name: str | None
    timezone_str: str
    generated_at: datetime
    total_count: int
    filtered_count: int


@dataclass
class RosterBoardItem:
    employee_id: str
    employee_name: str
    employee_code: str
    role_name: str | None
    crew_name: str | None
    work_status: str          # WORK/REST/OFFSITE/LEAVE/SICK/TRAINING/STANDBY
    shift_id: str | None
    shift_name: str | None
    site_status: str | None   # ONSITE/OFFSITE
    planned_equipment_code: str | None
    actual_equipment_code: str | None  # current active actual
    operational_state: str    # from OperationalState
    checkpoint_status_summary: str  # e.g. "3/4 PASS, 1 FAIL"
    active_exception_count: int
    has_pending_decision: bool
    decision_status: str | None
    attention_badge: str | None  # ATTENTION_REQUIRED / CONFIG_INCOMPLETE / None


@dataclass
class RosterBoardResult:
    context: RosterBoardContext
    items: list[RosterBoardItem]


@dataclass
class WorkerIdentity:
    employee_id: str
    employee_name: str
    employee_code: str
    employee_no: str | None
    role_name: str | None
    role_code: str | None
    crew_name: str | None
    crew_code: str | None
    is_active: bool


@dataclass
class WorkerRoster:
    operating_date: date
    shift_id: str | None
    shift_name: str | None
    work_status: str
    site_status: str | None
    planned_equipment_code: str | None
    planned_equipment_id: str | None
    rule_version: str | None


@dataclass
class EquipmentInterval:
    equipment_id: str
    equipment_code: str
    equipment_type: str
    started_at: datetime
    ended_at: datetime | None
    is_current: bool
    source: str


@dataclass
class EquipmentHistory:
    planned_equipment_id: str | None
    planned_equipment_code: str | None
    planned_equipment_type: str | None
    actual_intervals: list[EquipmentInterval]
    comparison_results: list[dict]  # [{comparison_result, actual_eq_code, planned_eq_code}]
    has_mismatch: bool


@dataclass
class TimelineEntry:
    timestamp: datetime
    event_type: str        # canonical event type or checkpoint type
    display_label: str     # human-readable: "BRIEFING_IN", "EQUIPMENT_CHECK_IN", etc.
    validation_status: str | None  # PASS/FAIL/CONFIG_INCOMPLETE
    source: str | None
    equipment_code: str | None
    site_name: str | None
    reason_code: str | None
    evidence_available: bool


@dataclass
class CheckpointDetailItem:
    checkpoint_type: str
    timestamp: datetime | None
    validation_status: str
    rule_version: str | None
    source: str | None
    evidence_available: bool
    site_name: str | None
    equipment_code: str | None
    reason_code: str | None
    is_missing: bool
    expected_window_start: datetime | None
    expected_window_end: datetime | None


@dataclass
class ExceptionContextItem:
    exception_id: str
    exception_type: str
    severity: str
    status: str
    detected_at: datetime
    current_owner_id: str | None
    equipment_code: str | None


@dataclass
class DecisionContextItem:
    decision_id: str
    decision_type: str
    status: str
    planned_display: str
    actual_display: str
    decided_by: str | None
    decided_at: datetime | None
    reason_text: str | None
    authorization_status: str


@dataclass
class CompetencyState:
    equipment_type: str
    status: str  # VALID/EXPIRED/SUSPENDED/MISSING
    valid_from: date | None
    valid_to: date | None


@dataclass
class WorkerDetail:
    identity: WorkerIdentity
    roster: WorkerRoster
    operational_state: str
    equipment_history: EquipmentHistory
    timeline: list[TimelineEntry]
    checkpoint_details: list[CheckpointDetailItem]
    exceptions: list[ExceptionContextItem]
    decisions: list[DecisionContextItem]
    competencies: list[CompetencyState]


# ── Non-attendance work statuses ─────────────────────────────

_NON_ATTENDANCE_STATUSES = {
    WorkStatus.REST,
    WorkStatus.OFFSITE,
    WorkStatus.LEAVE,
    WorkStatus.SICK,
    WorkStatus.TRAINING,
    WorkStatus.STANDBY,
}


# ── Bulk loader helpers ──────────────────────────────────────

def _load_workers(db: Session, ids: list[str]) -> dict[str, Worker]:
    if not ids:
        return {}
    return {w.id: w for w in db.scalars(select(Worker).where(Worker.id.in_(ids))).all()}


def _load_metas(db: Session, tenant_id: str, ids: list[str]) -> dict[str, EmployeeMeta]:
    if not ids:
        return {}
    result: dict[str, EmployeeMeta] = {}
    for em in db.scalars(
        select(EmployeeMeta).where(
            EmployeeMeta.tenant_id == tenant_id,
            EmployeeMeta.worker_id.in_(ids),
        )
    ).all():
        existing = result.get(em.worker_id)
        if existing is None or (em.effective_from and existing.effective_from and em.effective_from > existing.effective_from):
            result[em.worker_id] = em
    return result


def _load_roles(db: Session, ids: list[str]) -> dict[str, Role]:
    if not ids:
        return {}
    return {r.id: r for r in db.scalars(select(Role).where(Role.id.in_(ids))).all()}


def _load_crews(db: Session, ids: list[str]) -> dict[str, Crew]:
    if not ids:
        return {}
    return {c.id: c for c in db.scalars(select(Crew).where(Crew.id.in_(ids))).all()}


def _load_equipment(db: Session, ids: list[str]) -> dict[str, Equipment]:
    if not ids:
        return {}
    return {e.id: e for e in db.scalars(select(Equipment).where(Equipment.id.in_(ids))).all()}


def _load_sites(db: Session, ids: list[str]) -> dict[str, Site]:
    if not ids:
        return {}
    return {s.id: s for s in db.scalars(select(Site).where(Site.id.in_(ids))).all()}


def _load_shifts(db: Session, ids: list[str]) -> dict[str, ShiftTemplate]:
    if not ids:
        return {}
    return {s.id: s for s in db.scalars(select(ShiftTemplate).where(ShiftTemplate.id.in_(ids))).all()}


def _eq_code(eq_map: dict[str, Equipment], eq_id: str | None) -> str | None:
    if not eq_id:
        return None
    eq = eq_map.get(eq_id)
    return eq.equipment_code if eq else None


# ── Helper: checkpoint status summary ────────────────────────

def _checkpoint_summary(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, employee_ids: list[str],
) -> dict[str, str]:
    """Build per-employee checkpoint summary string."""
    if not employee_ids:
        return {}

    counts: dict[str, dict[str, int]] = {
        eid: {"PASS": 0, "FAIL": 0, "MISSING": 0, "CONFIG_INCOMPLETE": 0}
        for eid in employee_ids
    }

    cv_q = select(CheckpointValidationResult).where(
        CheckpointValidationResult.tenant_id == tenant_id,
        CheckpointValidationResult.operating_date == operating_date,
        CheckpointValidationResult.employee_id.in_(employee_ids),
    )
    if shift_id:
        cv_q = cv_q.where(CheckpointValidationResult.shift_id == shift_id)

    for cv in db.scalars(cv_q).all():
        c = counts.get(cv.employee_id)
        if c is None:
            continue
        st = cv.validation_status.value if cv.validation_status else "CONFIG_INCOMPLETE"
        if st in c:
            c[st] += 1

    mc_q = select(MissingCheckpointResult).where(
        MissingCheckpointResult.tenant_id == tenant_id,
        MissingCheckpointResult.operating_date == operating_date,
        MissingCheckpointResult.employee_id.in_(employee_ids),
    )
    if shift_id:
        mc_q = mc_q.where(MissingCheckpointResult.shift_id == shift_id)

    for mc in db.scalars(mc_q).all():
        c = counts.get(mc.employee_id)
        if c:
            c["MISSING"] += 1

    result: dict[str, str] = {}
    for eid, c in counts.items():
        parts = []
        if c["PASS"]:
            parts.append(f"{c['PASS']} PASS")
        if c["FAIL"]:
            parts.append(f"{c['FAIL']} FAIL")
        if c["MISSING"]:
            parts.append(f"{c['MISSING']} MISSING")
        if c["CONFIG_INCOMPLETE"]:
            parts.append(f"{c['CONFIG_INCOMPLETE']} CONFIG_INCOMPLETE")
        result[eid] = ", ".join(parts) if parts else "No checkpoints"
    return result


# ── Helper: active exception count per employee ──────────────

def _active_exception_count(
    db: Session, tenant_id: str, operating_date: date, employee_ids: list[str],
) -> dict[str, int]:
    if not employee_ids:
        return {}
    rows = db.execute(
        select(ExceptionCase.employee_id, func.count(ExceptionCase.id)).where(
            ExceptionCase.tenant_id == tenant_id,
            ExceptionCase.operating_date == operating_date,
            ExceptionCase.employee_id.in_(employee_ids),
            ExceptionCase.status.in_([ExceptionStatus.OPEN, ExceptionStatus.ACKNOWLEDGED]),
        ).group_by(ExceptionCase.employee_id)
    ).all()
    return {row[0]: row[1] for row in rows}


# ── Helper: pending decision status per employee ─────────────

def _decision_status_for_employee(
    db: Session, tenant_id: str, employee_ids: list[str],
) -> dict[str, tuple[bool, str | None]]:
    """Returns {employee_id: (has_pending, status_str)}."""
    if not employee_ids:
        return {}

    result: dict[str, tuple[bool, str | None]] = {
        eid: (False, None) for eid in employee_ids
    }

    for d in db.scalars(
        select(ExceptionDecision).where(
            ExceptionDecision.tenant_id == tenant_id,
            or_(
                ExceptionDecision.planned_worker_id.in_(employee_ids),
                ExceptionDecision.actual_worker_id.in_(employee_ids),
            ),
        )
    ).all():
        for wid in [d.planned_worker_id, d.actual_worker_id]:
            if wid and wid in result:
                if d.status == DecisionStatus.PENDING:
                    result[wid] = (True, "PENDING")
                elif d.status == DecisionStatus.APPROVED:
                    if not result[wid][0]:
                        result[wid] = (False, "APPROVED")

    return result


# ── Helper: current active actual equipment per employee ─────

def _current_actual_equipment(
    db: Session, tenant_id: str, operating_date: date,
    shift_id: str | None, employee_ids: list[str],
) -> dict[str, EquipmentAssignmentActual]:
    if not employee_ids:
        return {}
    q = select(EquipmentAssignmentActual).where(
        EquipmentAssignmentActual.tenant_id == tenant_id,
        EquipmentAssignmentActual.operating_date == operating_date,
        EquipmentAssignmentActual.employee_id.in_(employee_ids),
        EquipmentAssignmentActual.status == ActualAssignmentStatus.ACTIVE,
    )
    if shift_id:
        q = q.where(EquipmentAssignmentActual.shift_id == shift_id)

    result: dict[str, EquipmentAssignmentActual] = {}
    for a in db.scalars(q).all():
        existing = result.get(a.employee_id)
        if existing is None or (a.started_at and existing.started_at and a.started_at > existing.started_at):
            result[a.employee_id] = a
    return result


# ── Helper: capability check ─────────────────────────────────

def _equipment_capability_enabled(db: Session, tenant_id: str) -> bool:
    policy = db.scalar(
        select(RosterPolicy).where(
            RosterPolicy.tenant_id == tenant_id,
            RosterPolicy.policy_key == "equipment_assignment_enabled",
        )
    )
    if policy is None:
        return True
    return policy.policy_value.lower() in ("true", "1", "yes")


# ── Main Entry: Roster Board ─────────────────────────────────

def get_roster_board(
    db: Session,
    tenant_id: str,
    operating_date: date,
    shift_id: str | None = None,
    site_id: str | None = None,
    crew_id: str | None = None,
    role_id: str | None = None,
    work_status: str | None = None,
    operational_state_filter: str | None = None,
    has_exception: bool | None = None,
    equipment_search: str | None = None,
    employee_search: str | None = None,
    sort_by: str = "employee_name",
    sort_dir: str = "asc",
    offset: int = 0,
    limit: int = 50,
) -> RosterBoardResult:
    """Build roster board view with all operational context."""
    generated_at = datetime.now(timezone.utc)

    # ── Context ──────────────────────────────────────────────
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} not found")

    site = db.get(Site, site_id) if site_id else None
    shift = db.get(ShiftTemplate, shift_id) if shift_id else None
    tz_str = _resolve_timezone(db, tenant_id, site_id)
    eq_enabled = _equipment_capability_enabled(db, tenant_id)

    # ── Base roster query ────────────────────────────────────
    roster_q = select(RosterAssignment).where(
        RosterAssignment.tenant_id == tenant_id,
        RosterAssignment.operating_date == operating_date,
    )
    if shift_id:
        roster_q = roster_q.where(RosterAssignment.shift_id == shift_id)
    if site_id:
        roster_q = roster_q.where(RosterAssignment.site_id == site_id)
    roster_items = list(db.scalars(roster_q).all())

    # ── Filter by crew_id (needs EmployeeMeta join) ──────────
    if crew_id:
        all_eids = [r.employee_id for r in roster_items]
        if all_eids:
            crew_eids = {
                em.worker_id for em in db.scalars(
                    select(EmployeeMeta).where(
                        EmployeeMeta.tenant_id == tenant_id,
                        EmployeeMeta.worker_id.in_(all_eids),
                        EmployeeMeta.crew_id == crew_id,
                    )
                ).all()
            }
            roster_items = [r for r in roster_items if r.employee_id in crew_eids]
        else:
            roster_items = []

    # ── Filter by role_id ────────────────────────────────────
    if role_id:
        all_eids = [r.employee_id for r in roster_items]
        if all_eids:
            role_eids = {
                em.worker_id for em in db.scalars(
                    select(EmployeeMeta).where(
                        EmployeeMeta.tenant_id == tenant_id,
                        EmployeeMeta.worker_id.in_(all_eids),
                        EmployeeMeta.role_id == role_id,
                    )
                ).all()
            }
            roster_items = [r for r in roster_items if r.employee_id in role_eids]
        else:
            roster_items = []

    # ── Filter by work_status ────────────────────────────────
    if work_status:
        try:
            ws_enum = WorkStatus(work_status)
            roster_items = [r for r in roster_items if r.work_status == ws_enum]
        except ValueError:
            pass

    total_count = len(roster_items)

    # ── Bulk load metadata ───────────────────────────────────
    employee_ids = list({r.employee_id for r in roster_items})
    workers = _load_workers(db, employee_ids)
    metas = _load_metas(db, tenant_id, employee_ids)
    role_ids = list({m.role_id for m in metas.values() if m.role_id})
    roles = _load_roles(db, role_ids)
    crew_ids_m = list({m.crew_id for m in metas.values() if m.crew_id})
    crews = _load_crews(db, crew_ids_m)

    # Planned equipment codes
    planned_eq_ids = [r.planned_equipment_id for r in roster_items if r.planned_equipment_id]
    eq_map = _load_equipment(db, planned_eq_ids)

    # ── Separate WORK vs non-WORK ────────────────────────────
    work_eids = [r.employee_id for r in roster_items if r.work_status == WorkStatus.WORK]

    # ── WORK-only enrichments ────────────────────────────────
    op_state_map: dict[str, str] = {}
    for r in roster_items:
        if r.work_status == WorkStatus.WORK:
            op_state_map[r.employee_id] = _derive_operational_state(
                db, r.employee_id, operating_date, shift_id or r.shift_id
            )

    cp_summary = _checkpoint_summary(db, tenant_id, operating_date, shift_id, work_eids)

    actual_equip: dict[str, EquipmentAssignmentActual] = {}
    if eq_enabled and work_eids:
        actual_equip = _current_actual_equipment(db, tenant_id, operating_date, shift_id, work_eids)
        extra_eq_ids = [a.equipment_id for a in actual_equip.values()
                        if a.equipment_id and a.equipment_id not in eq_map]
        if extra_eq_ids:
            eq_map.update(_load_equipment(db, extra_eq_ids))

    exc_counts = _active_exception_count(db, tenant_id, operating_date, employee_ids)
    dec_status = _decision_status_for_employee(db, tenant_id, employee_ids)

    # ── Build board items ────────────────────────────────────
    # Resolve shift names in bulk
    shift_ids_needed = list({r.shift_id for r in roster_items if r.shift_id})
    shifts_map = _load_shifts(db, shift_ids_needed)

    all_items: list[RosterBoardItem] = []
    for r in roster_items:
        w = workers.get(r.employee_id)
        em = metas.get(r.employee_id)
        role_name = roles[em.role_id].role_name if em and em.role_id and em.role_id in roles else None
        crew_name = crews[em.crew_id].crew_name if em and em.crew_id and em.crew_id in crews else None
        ws = r.work_status.value if r.work_status else "WORK"
        is_work = r.work_status == WorkStatus.WORK

        # Operational state
        if is_work:
            op_state = op_state_map.get(r.employee_id, OperationalState.NOT_STARTED)
        else:
            op_state = ws

        # Checkpoint summary
        cp = cp_summary.get(r.employee_id, "No checkpoints") if is_work else ""

        # Equipment
        planned_code = _eq_code(eq_map, r.planned_equipment_id) if eq_enabled else None
        actual_code = None
        if eq_enabled and is_work:
            act = actual_equip.get(r.employee_id)
            if act:
                actual_code = _eq_code(eq_map, act.equipment_id)

        # Exceptions
        exc_count = exc_counts.get(r.employee_id, 0)

        # Decisions
        has_pending, decision_s = dec_status.get(r.employee_id, (False, None))

        # Attention badge
        badge = None
        if is_work:
            if op_state == OperationalState.ATTENTION_REQUIRED:
                badge = "ATTENTION_REQUIRED"
            elif "CONFIG_INCOMPLETE" in cp:
                badge = "CONFIG_INCOMPLETE"

        # Shift name
        shift_name = None
        if r.shift_id:
            st = shifts_map.get(r.shift_id)
            shift_name = st.shift_name if st else None

        all_items.append(RosterBoardItem(
            employee_id=r.employee_id,
            employee_name=w.name if w else r.employee_id,
            employee_code=w.code if w else "",
            role_name=role_name,
            crew_name=crew_name,
            work_status=ws,
            shift_id=r.shift_id,
            shift_name=shift_name,
            site_status=r.site_status.value if r.site_status else None,
            planned_equipment_code=planned_code,
            actual_equipment_code=actual_code,
            operational_state=op_state,
            checkpoint_status_summary=cp,
            active_exception_count=exc_count,
            has_pending_decision=has_pending,
            decision_status=decision_s,
            attention_badge=badge,
        ))

    # ── Post-build filters ───────────────────────────────────
    filtered = all_items

    if operational_state_filter:
        filtered = [i for i in filtered if i.operational_state == operational_state_filter]

    if has_exception is True:
        filtered = [i for i in filtered if i.active_exception_count > 0]
    elif has_exception is False:
        filtered = [i for i in filtered if i.active_exception_count == 0]

    if equipment_search:
        s = equipment_search.lower()
        filtered = [i for i in filtered
                    if (i.planned_equipment_code and s in i.planned_equipment_code.lower())
                    or (i.actual_equipment_code and s in i.actual_equipment_code.lower())]

    if employee_search:
        s = employee_search.lower()
        filtered = [i for i in filtered
                    if s in i.employee_name.lower() or s in i.employee_code.lower()]

    filtered_count = len(filtered)

    # ── Sort ─────────────────────────────────────────────────
    key_funcs = {
        "employee_name": lambda i: (i.employee_name or "").lower(),
        "employee_code": lambda i: (i.employee_code or "").lower(),
        "work_status": lambda i: i.work_status,
        "operational_state": lambda i: i.operational_state,
        "role_name": lambda i: (i.role_name or "").lower(),
        "crew_name": lambda i: (i.crew_name or "").lower(),
        "active_exception_count": lambda i: i.active_exception_count,
    }
    filtered.sort(key=key_funcs.get(sort_by, key_funcs["employee_name"]),
                  reverse=(sort_dir.lower() == "desc"))

    # ── Paginate ─────────────────────────────────────────────
    paginated = filtered[offset:offset + limit]

    ctx = RosterBoardContext(
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        site_id=site_id,
        site_name=site.site_name if site else None,
        operating_date=operating_date,
        shift_id=shift_id,
        shift_name=shift.shift_name if shift else None,
        timezone_str=tz_str,
        generated_at=generated_at,
        total_count=total_count,
        filtered_count=filtered_count,
    )

    return RosterBoardResult(context=ctx, items=paginated)


# ── Main Entry: Worker Detail ────────────────────────────────

def get_worker_detail(
    db: Session,
    tenant_id: str,
    worker_id: str,
    operating_date: date,
    shift_id: str | None = None,
) -> WorkerDetail:
    """Build single worker detail view with full operational context."""
    worker = db.scalar(
        select(Worker).where(Worker.id == worker_id, Worker.tenant_id == tenant_id)
    )
    if not worker:
        raise ValueError(f"Worker {worker_id} not found in tenant {tenant_id}")

    # ── Identity ─────────────────────────────────────────────
    meta = db.scalar(
        select(EmployeeMeta).where(
            EmployeeMeta.tenant_id == tenant_id, EmployeeMeta.worker_id == worker_id,
        ).order_by(EmployeeMeta.effective_from.desc()).limit(1)
    )
    role = db.get(Role, meta.role_id) if meta and meta.role_id else None
    crew = db.get(Crew, meta.crew_id) if meta and meta.crew_id else None

    identity = WorkerIdentity(
        employee_id=worker_id,
        employee_name=worker.name,
        employee_code=worker.code,
        employee_no=meta.employee_no if meta else None,
        role_name=role.role_name if role else None,
        role_code=role.role_code if role else None,
        crew_name=crew.crew_name if crew else None,
        crew_code=crew.crew_code if crew else None,
        is_active=worker.is_active,
    )

    # ── Roster ───────────────────────────────────────────────
    roster_q = select(RosterAssignment).where(
        RosterAssignment.tenant_id == tenant_id,
        RosterAssignment.operating_date == operating_date,
        RosterAssignment.employee_id == worker_id,
    )
    if shift_id:
        roster_q = roster_q.where(RosterAssignment.shift_id == shift_id)
    roster = db.scalar(roster_q)

    if roster:
        st = db.get(ShiftTemplate, roster.shift_id) if roster.shift_id else None
        peq = db.get(Equipment, roster.planned_equipment_id) if roster.planned_equipment_id else None
        worker_roster = WorkerRoster(
            operating_date=operating_date,
            shift_id=roster.shift_id,
            shift_name=st.shift_name if st else None,
            work_status=roster.work_status.value if roster.work_status else "WORK",
            site_status=roster.site_status.value if roster.site_status else None,
            planned_equipment_code=peq.equipment_code if peq else None,
            planned_equipment_id=roster.planned_equipment_id,
            rule_version=roster.effective_rule_version,
        )
    else:
        worker_roster = WorkerRoster(
            operating_date=operating_date, shift_id=shift_id, shift_name=None,
            work_status="", site_status=None,
            planned_equipment_code=None, planned_equipment_id=None, rule_version=None,
        )

    is_work = roster and roster.work_status == WorkStatus.WORK
    eff_shift_id = shift_id or (roster.shift_id if roster else None)

    # ── Operational state ────────────────────────────────────
    if is_work:
        operational_state = _derive_operational_state(db, worker_id, operating_date, eff_shift_id)
    else:
        operational_state = worker_roster.work_status or "NOT_SCHEDULED"

    # ── Equipment history ────────────────────────────────────
    equipment_history = _build_equipment_history(db, tenant_id, worker_id, operating_date, eff_shift_id, roster)

    # ── Timeline ─────────────────────────────────────────────
    timeline = _build_timeline(db, tenant_id, worker_id, operating_date, eff_shift_id)

    # ── Checkpoint details (WORK only) ───────────────────────
    checkpoint_details: list[CheckpointDetailItem] = []
    if is_work:
        checkpoint_details = _build_checkpoint_details(db, tenant_id, worker_id, operating_date, eff_shift_id)

    # ── Exceptions & decisions ───────────────────────────────
    exceptions = _build_exception_context(db, tenant_id, worker_id, operating_date)
    decisions = _build_decision_context(db, tenant_id, worker_id)
    competencies = _build_competency_states(db, tenant_id, worker_id)

    return WorkerDetail(
        identity=identity, roster=worker_roster, operational_state=operational_state,
        equipment_history=equipment_history, timeline=timeline,
        checkpoint_details=checkpoint_details, exceptions=exceptions,
        decisions=decisions, competencies=competencies,
    )


# ── Main Entry: Worker Timeline ──────────────────────────────

def get_worker_timeline(
    db: Session,
    tenant_id: str,
    worker_id: str,
    operating_date: date,
    shift_id: str | None = None,
) -> list[TimelineEntry]:
    """Chronological timeline for one worker. Subset of worker detail."""
    worker = db.scalar(
        select(Worker).where(Worker.id == worker_id, Worker.tenant_id == tenant_id)
    )
    if not worker:
        raise ValueError(f"Worker {worker_id} not found in tenant {tenant_id}")

    if not shift_id:
        roster = db.scalar(
            select(RosterAssignment).where(
                RosterAssignment.tenant_id == tenant_id,
                RosterAssignment.operating_date == operating_date,
                RosterAssignment.employee_id == worker_id,
            )
        )
        if roster and roster.shift_id:
            shift_id = roster.shift_id

    return _build_timeline(db, tenant_id, worker_id, operating_date, shift_id)


# ── Section Builders ─────────────────────────────────────────

_EVENT_LABELS = {
    CanonicalEventType.CHECK_IN: "CHECK_IN",
    CanonicalEventType.CHECK_OUT: "CHECK_OUT",
    CanonicalEventType.BREAK_IN: "BREAK_IN",
    CanonicalEventType.BREAK_OUT: "BREAK_OUT",
    CanonicalEventType.BRIEFING_IN: "BRIEFING_IN",
    CanonicalEventType.BRIEFING_OUT: "BRIEFING_OUT",
    CanonicalEventType.EQUIPMENT_CHECK_IN: "EQUIPMENT_CHECK_IN",
    CanonicalEventType.EQUIPMENT_CHECK_OUT: "EQUIPMENT_CHECK_OUT",
    CanonicalEventType.HANDOVER_START: "HANDOVER_START",
    CanonicalEventType.HANDOVER_END: "HANDOVER_END",
    CanonicalEventType.SUPERVISOR_OVERRIDE: "SUPERVISOR_OVERRIDE",
}


def _build_timeline(
    db: Session, tenant_id: str, employee_id: str,
    operating_date: date, shift_id: str | None,
) -> list[TimelineEntry]:
    """Merge canonical events + checkpoint results chronologically."""
    events_q = select(CanonicalAttendanceEvent).where(
        CanonicalAttendanceEvent.tenant_id == tenant_id,
        CanonicalAttendanceEvent.employee_id == employee_id,
        CanonicalAttendanceEvent.operating_date == operating_date,
    )
    if shift_id:
        events_q = events_q.where(CanonicalAttendanceEvent.shift_id == shift_id)
    events = list(db.scalars(events_q).all())

    eq_ids = list({e.equipment_id for e in events if e.equipment_id})
    eq_map = _load_equipment(db, eq_ids)
    site_ids = list({e.site_id for e in events if e.site_id})
    site_map = _load_sites(db, site_ids)

    entries: list[TimelineEntry] = []
    for e in events:
        entries.append(TimelineEntry(
            timestamp=e.local_timestamp or e.utc_timestamp,
            event_type=e.event_type.value if e.event_type else "UNKNOWN",
            display_label=_EVENT_LABELS.get(e.event_type, e.event_type.value if e.event_type else "UNKNOWN"),
            validation_status=None,
            source=e.source,
            equipment_code=_eq_code(eq_map, e.equipment_id),
            site_name=site_map[e.site_id].site_name if e.site_id and e.site_id in site_map else None,
            reason_code=None,
            evidence_available=bool(e.evidence_json),
        ))

    # Checkpoint results
    cv_q = select(CheckpointValidationResult).where(
        CheckpointValidationResult.tenant_id == tenant_id,
        CheckpointValidationResult.employee_id == employee_id,
        CheckpointValidationResult.operating_date == operating_date,
    )
    if shift_id:
        cv_q = cv_q.where(CheckpointValidationResult.shift_id == shift_id)

    for cv in db.scalars(cv_q).all():
        entries.append(TimelineEntry(
            timestamp=cv.detected_timestamp,
            event_type=cv.checkpoint_type,
            display_label=cv.checkpoint_type,
            validation_status=cv.validation_status.value if cv.validation_status else None,
            source=None, equipment_code=None, site_name=None,
            reason_code=cv.reason_code,
            evidence_available=bool(cv.evidence_json),
        ))

    entries.sort(key=lambda t: t.timestamp)
    return entries


def _build_equipment_history(
    db: Session, tenant_id: str, employee_id: str,
    operating_date: date, shift_id: str | None,
    roster: RosterAssignment | None,
) -> EquipmentHistory:
    """Planned + all actual intervals + comparison results."""
    planned_eq_id = roster.planned_equipment_id if roster else None
    planned_eq = db.get(Equipment, planned_eq_id) if planned_eq_id else None

    actual_q = select(EquipmentAssignmentActual).where(
        EquipmentAssignmentActual.tenant_id == tenant_id,
        EquipmentAssignmentActual.operating_date == operating_date,
        EquipmentAssignmentActual.employee_id == employee_id,
    )
    if shift_id:
        actual_q = actual_q.where(EquipmentAssignmentActual.shift_id == shift_id)
    actual_q = actual_q.order_by(EquipmentAssignmentActual.started_at.asc())
    actuals = list(db.scalars(actual_q).all())

    all_eq_ids = list({a.equipment_id for a in actuals} | ({planned_eq_id} if planned_eq_id else set()))
    eq_map = _load_equipment(db, all_eq_ids)

    intervals: list[EquipmentInterval] = []
    for a in actuals:
        eq = eq_map.get(a.equipment_id)
        intervals.append(EquipmentInterval(
            equipment_id=a.equipment_id,
            equipment_code=eq.equipment_code if eq else a.equipment_id,
            equipment_type=eq.equipment_type if eq else "",
            started_at=a.started_at,
            ended_at=a.ended_at,
            is_current=a.status == ActualAssignmentStatus.ACTIVE and a.ended_at is None,
            source=a.source or "UNKNOWN",
        ))

    comparison_results: list[dict] = []
    has_mismatch = False
    actual_ids = [a.id for a in actuals]
    if actual_ids:
        for comp in db.scalars(
            select(EquipmentComparisonResult).where(
                EquipmentComparisonResult.actual_assignment_id.in_(actual_ids),
            )
        ).all():
            aeq = eq_map.get(comp.actual_equipment_id)
            peq = eq_map.get(comp.planned_equipment_id) if comp.planned_equipment_id else None
            comparison_results.append({
                "comparison_result": comp.comparison_result.value,
                "actual_eq_code": aeq.equipment_code if aeq else comp.actual_equipment_id,
                "planned_eq_code": peq.equipment_code if peq else None,
            })
            if comp.comparison_result in (ComparisonResult.MISMATCH, ComparisonResult.NO_ACTUAL_EQUIPMENT):
                has_mismatch = True

    return EquipmentHistory(
        planned_equipment_id=planned_eq_id,
        planned_equipment_code=planned_eq.equipment_code if planned_eq else None,
        planned_equipment_type=planned_eq.equipment_type if planned_eq else None,
        actual_intervals=intervals,
        comparison_results=comparison_results,
        has_mismatch=has_mismatch,
    )


def _build_checkpoint_details(
    db: Session, tenant_id: str, employee_id: str,
    operating_date: date, shift_id: str | None,
) -> list[CheckpointDetailItem]:
    """Detailed checkpoint info including missing checkpoints."""
    details: list[CheckpointDetailItem] = []

    # Canonical event map for source/equipment/site enrichment
    cv_q = select(CheckpointValidationResult).where(
        CheckpointValidationResult.tenant_id == tenant_id,
        CheckpointValidationResult.employee_id == employee_id,
        CheckpointValidationResult.operating_date == operating_date,
    )
    if shift_id:
        cv_q = cv_q.where(CheckpointValidationResult.shift_id == shift_id)
    cv_results = list(db.scalars(cv_q).all())

    ce_ids = [cv.canonical_event_id for cv in cv_results if cv.canonical_event_id]
    ce_map: dict[str, CanonicalAttendanceEvent] = {}
    all_eq_ids: set[str] = set()
    all_site_ids: set[str] = set()
    if ce_ids:
        for ce in db.scalars(
            select(CanonicalAttendanceEvent).where(CanonicalAttendanceEvent.id.in_(ce_ids))
        ).all():
            ce_map[ce.id] = ce
            if ce.equipment_id:
                all_eq_ids.add(ce.equipment_id)
            if ce.site_id:
                all_site_ids.add(ce.site_id)

    eq_map = _load_equipment(db, list(all_eq_ids))
    site_map = _load_sites(db, list(all_site_ids))

    for cv in cv_results:
        ce = ce_map.get(cv.canonical_event_id) if cv.canonical_event_id else None
        eq_code = _eq_code(eq_map, ce.equipment_id) if ce and ce.equipment_id else None
        site_name = site_map[ce.site_id].site_name if ce and ce.site_id and ce.site_id in site_map else None
        details.append(CheckpointDetailItem(
            checkpoint_type=cv.checkpoint_type,
            timestamp=cv.detected_timestamp,
            validation_status=cv.validation_status.value if cv.validation_status else "CONFIG_INCOMPLETE",
            rule_version=cv.rule_version_id,
            source=ce.source if ce else None,
            evidence_available=bool(cv.evidence_json),
            site_name=site_name,
            equipment_code=eq_code,
            reason_code=cv.reason_code,
            is_missing=False,
            expected_window_start=None,
            expected_window_end=None,
        ))

    mc_q = select(MissingCheckpointResult).where(
        MissingCheckpointResult.tenant_id == tenant_id,
        MissingCheckpointResult.employee_id == employee_id,
        MissingCheckpointResult.operating_date == operating_date,
    )
    if shift_id:
        mc_q = mc_q.where(MissingCheckpointResult.shift_id == shift_id)

    for mc in db.scalars(mc_q).all():
        details.append(CheckpointDetailItem(
            checkpoint_type=mc.checkpoint_type,
            timestamp=None,
            validation_status="MISSING",
            rule_version=None, source=None, evidence_available=False,
            site_name=None, equipment_code=None, reason_code=None,
            is_missing=True,
            expected_window_start=mc.expected_window_start,
            expected_window_end=mc.expected_window_end,
        ))

    details.sort(key=lambda d: d.checkpoint_type)
    return details


def _build_exception_context(
    db: Session, tenant_id: str, employee_id: str, operating_date: date,
) -> list[ExceptionContextItem]:
    exceptions = list(db.scalars(
        select(ExceptionCase).where(
            ExceptionCase.tenant_id == tenant_id,
            ExceptionCase.employee_id == employee_id,
            ExceptionCase.operating_date == operating_date,
        ).order_by(ExceptionCase.severity.desc(), ExceptionCase.detected_at.asc())
    ).all())
    if not exceptions:
        return []

    eq_ids = list({e.equipment_id for e in exceptions if e.equipment_id})
    eq_map = _load_equipment(db, eq_ids)

    return [
        ExceptionContextItem(
            exception_id=e.id,
            exception_type=e.exception_type,
            severity=e.severity.value if e.severity else "WARNING",
            status=e.status.value if e.status else "OPEN",
            detected_at=e.detected_at,
            current_owner_id=e.current_owner_id,
            equipment_code=_eq_code(eq_map, e.equipment_id),
        )
        for e in exceptions
    ]


def _build_decision_context(
    db: Session, tenant_id: str, employee_id: str,
) -> list[DecisionContextItem]:
    decisions = list(db.scalars(
        select(ExceptionDecision).where(
            ExceptionDecision.tenant_id == tenant_id,
            or_(
                ExceptionDecision.planned_worker_id == employee_id,
                ExceptionDecision.actual_worker_id == employee_id,
            ),
        ).order_by(ExceptionDecision.requested_at.desc())
    ).all())
    if not decisions:
        return []

    wids: set[str] = set()
    eq_ids: set[str] = set()
    for d in decisions:
        for wid in [d.planned_worker_id, d.actual_worker_id, d.decided_by]:
            if wid:
                wids.add(wid)
        for eid in [d.planned_equipment_id, d.actual_equipment_id]:
            if eid:
                eq_ids.add(eid)

    workers = _load_workers(db, list(wids))
    eq_map = _load_equipment(db, list(eq_ids))

    result: list[DecisionContextItem] = []
    for d in decisions:
        pw = workers.get(d.planned_worker_id) if d.planned_worker_id else None
        aw = workers.get(d.actual_worker_id) if d.actual_worker_id else None
        pe_code = _eq_code(eq_map, d.planned_equipment_id)
        ae_code = _eq_code(eq_map, d.actual_equipment_id)

        plan_parts = []
        if pw:
            plan_parts.append(pw.name)
        if pe_code:
            plan_parts.append(f"→ {pe_code}")

        actual_parts = []
        if aw:
            actual_parts.append(aw.name)
        if ae_code:
            actual_parts.append(f"→ {ae_code}")

        auth = "BLOCKED_POLICY_DECISION" if d.authorization_policy == "BLOCKED_POLICY_DECISION" else "OK"
        decider = workers.get(d.decided_by) if d.decided_by else None

        result.append(DecisionContextItem(
            decision_id=d.id,
            decision_type=d.decision_type.value if d.decision_type else "UNKNOWN",
            status=d.status.value if d.status else "PENDING",
            planned_display=" ".join(plan_parts) or "—",
            actual_display=" ".join(actual_parts) or "—",
            decided_by=decider.name if decider else d.decided_by,
            decided_at=d.decided_at,
            reason_text=d.reason_text,
            authorization_status=auth,
        ))
    return result


def _build_competency_states(
    db: Session, tenant_id: str, employee_id: str,
) -> list[CompetencyState]:
    """Build competency state list for this worker."""
    today = date.today()
    comps = list(db.scalars(
        select(Competency).where(
            Competency.tenant_id == tenant_id,
            Competency.employee_id == employee_id,
        )
    ).all())

    # Group by equipment_type, keep latest
    by_type: dict[str, Competency] = {}
    for c in comps:
        existing = by_type.get(c.equipment_type)
        if existing is None or (c.valid_from and existing.valid_from and c.valid_from > existing.valid_from):
            by_type[c.equipment_type] = c

    result: list[CompetencyState] = []
    for eq_type, c in by_type.items():
        if c.status == CompetencyStatus.SUSPENDED:
            status = "SUSPENDED"
        elif c.valid_to and c.valid_to < today:
            status = "EXPIRED"
        elif c.status == CompetencyStatus.VALID:
            status = "VALID"
        else:
            status = c.status.value
        result.append(CompetencyState(
            equipment_type=eq_type, status=status,
            valid_from=c.valid_from, valid_to=c.valid_to,
        ))

    result.sort(key=lambda c: c.equipment_type)
    return result


# ── Serialization for API response ───────────────────────────

def roster_board_to_dict(result: RosterBoardResult) -> dict[str, Any]:
    """Convert RosterBoardResult to API response dict."""
    def _dt(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    return {
        "context": {
            "tenant_id": result.context.tenant_id,
            "tenant_name": result.context.tenant_name,
            "site_id": result.context.site_id,
            "site_name": result.context.site_name,
            "operating_date": result.context.operating_date.isoformat(),
            "shift_id": result.context.shift_id,
            "shift_name": result.context.shift_name,
            "timezone": result.context.timezone_str,
            "generated_at": _dt(result.context.generated_at),
            "total_count": result.context.total_count,
            "filtered_count": result.context.filtered_count,
        },
        "items": [
            {
                "employee_id": i.employee_id,
                "employee_name": i.employee_name,
                "employee_code": i.employee_code,
                "role_name": i.role_name,
                "crew_name": i.crew_name,
                "work_status": i.work_status,
                "shift_id": i.shift_id,
                "shift_name": i.shift_name,
                "site_status": i.site_status,
                "planned_equipment_code": i.planned_equipment_code,
                "actual_equipment_code": i.actual_equipment_code,
                "operational_state": i.operational_state,
                "checkpoint_status_summary": i.checkpoint_status_summary,
                "active_exception_count": i.active_exception_count,
                "has_pending_decision": i.has_pending_decision,
                "decision_status": i.decision_status,
                "attention_badge": i.attention_badge,
            }
            for i in result.items
        ],
    }


def worker_detail_to_dict(detail: WorkerDetail) -> dict[str, Any]:
    """Convert WorkerDetail to API response dict."""
    def _dt(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    def _d(d: date | None) -> str | None:
        return d.isoformat() if d else None

    return {
        "identity": {
            "employee_id": detail.identity.employee_id,
            "employee_name": detail.identity.employee_name,
            "employee_code": detail.identity.employee_code,
            "employee_no": detail.identity.employee_no,
            "role_name": detail.identity.role_name,
            "role_code": detail.identity.role_code,
            "crew_name": detail.identity.crew_name,
            "crew_code": detail.identity.crew_code,
            "is_active": detail.identity.is_active,
        },
        "roster": {
            "operating_date": _d(detail.roster.operating_date),
            "shift_id": detail.roster.shift_id,
            "shift_name": detail.roster.shift_name,
            "work_status": detail.roster.work_status,
            "site_status": detail.roster.site_status,
            "planned_equipment_code": detail.roster.planned_equipment_code,
            "planned_equipment_id": detail.roster.planned_equipment_id,
            "rule_version": detail.roster.rule_version,
        },
        "operational_state": detail.operational_state,
        "equipment_history": {
            "planned_equipment_id": detail.equipment_history.planned_equipment_id,
            "planned_equipment_code": detail.equipment_history.planned_equipment_code,
            "planned_equipment_type": detail.equipment_history.planned_equipment_type,
            "actual_intervals": [
                {
                    "equipment_id": iv.equipment_id,
                    "equipment_code": iv.equipment_code,
                    "equipment_type": iv.equipment_type,
                    "started_at": _dt(iv.started_at),
                    "ended_at": _dt(iv.ended_at),
                    "is_current": iv.is_current,
                    "source": iv.source,
                }
                for iv in detail.equipment_history.actual_intervals
            ],
            "comparison_results": detail.equipment_history.comparison_results,
            "has_mismatch": detail.equipment_history.has_mismatch,
        },
        "timeline": [
            {
                "timestamp": _dt(t.timestamp),
                "event_type": t.event_type,
                "display_label": t.display_label,
                "validation_status": t.validation_status,
                "source": t.source,
                "equipment_code": t.equipment_code,
                "site_name": t.site_name,
                "reason_code": t.reason_code,
                "evidence_available": t.evidence_available,
            }
            for t in detail.timeline
        ],
        "checkpoint_details": [
            {
                "checkpoint_type": c.checkpoint_type,
                "timestamp": _dt(c.timestamp),
                "validation_status": c.validation_status,
                "rule_version": c.rule_version,
                "source": c.source,
                "evidence_available": c.evidence_available,
                "site_name": c.site_name,
                "equipment_code": c.equipment_code,
                "reason_code": c.reason_code,
                "is_missing": c.is_missing,
                "expected_window_start": _dt(c.expected_window_start),
                "expected_window_end": _dt(c.expected_window_end),
            }
            for c in detail.checkpoint_details
        ],
        "exceptions": [
            {
                "exception_id": e.exception_id,
                "exception_type": e.exception_type,
                "severity": e.severity,
                "status": e.status,
                "detected_at": _dt(e.detected_at),
                "current_owner_id": e.current_owner_id,
                "equipment_code": e.equipment_code,
            }
            for e in detail.exceptions
        ],
        "decisions": [
            {
                "decision_id": d.decision_id,
                "decision_type": d.decision_type,
                "status": d.status,
                "planned_display": d.planned_display,
                "actual_display": d.actual_display,
                "decided_by": d.decided_by,
                "decided_at": _dt(d.decided_at),
                "reason_text": d.reason_text,
                "authorization_status": d.authorization_status,
            }
            for d in detail.decisions
        ],
        "competencies": [
            {
                "equipment_type": c.equipment_type,
                "status": c.status,
                "valid_from": _d(c.valid_from),
                "valid_to": _d(c.valid_to),
            }
            for c in detail.competencies
        ],
    }
