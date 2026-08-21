"""
canonical_event_service.py -- Canonical Attendance Event Foundation.

Handles:
1. Raw event ingestion (immutable)
2. Timezone-aware canonicalization
3. Operating date resolution (cross-midnight aware)
4. Shift resolution (roster-based)
5. Deduplication (source_event_id + fallback fingerprint)
6. Legacy attendance adapter (CHECK_IN/CHECK_OUT -> canonical)
"""
import hashlib
import json
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    RawEvent, RawEventStatus,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    AttendanceEvent, AttendanceType, AttendanceStatus,
    Tenant, Site, ShiftTemplate, RosterAssignment, Worker,
)


# ─────────────────────────────────────────────────────────────
# FINGERPRINT (fallback dedup when source_event_id unavailable)
# ─────────────────────────────────────────────────────────────

def compute_fingerprint(
    tenant_id: str,
    source: str,
    employee_id: str,
    event_type: str,
    raw_timestamp: str,
    site_id: str | None = None,
) -> str:
    """Deterministic fingerprint for fallback dedup.
    SHA-256 of: tenant_id|source|employee_id|event_type|raw_timestamp|site_id
    """
    parts = [tenant_id, source, employee_id, event_type, raw_timestamp, site_id or ""]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────
# RAW EVENT INGESTION
# ─────────────────────────────────────────────────────────────

def ingest_raw_event(
    db: Session,
    *,
    tenant_id: str,
    source: str,
    source_event_id: str,
    raw_timestamp: str,
    raw_payload: dict,
    schema_version: str | None = None,
    fingerprint: str | None = None,
) -> tuple[RawEvent, bool]:
    """Ingest a raw event. Returns (raw_event, is_duplicate).
    
    Dedup strategy:
    1. Check (tenant_id, source, source_event_id) unique constraint
    2. If source_event_id not reliable, check fingerprint
    
    Raw payload must NOT contain credentials, tokens, or biometric templates.
    """
    # Check for existing by source_event_id
    existing = db.scalar(
        select(RawEvent).where(
            RawEvent.tenant_id == tenant_id,
            RawEvent.source == source,
            RawEvent.source_event_id == source_event_id,
        )
    )
    if existing:
        return existing, True

    # Check fingerprint if provided
    if fingerprint:
        existing_fp = db.scalar(
            select(RawEvent).where(
                RawEvent.tenant_id == tenant_id,
                RawEvent.fingerprint == fingerprint,
                RawEvent.processing_status != RawEventStatus.DUPLICATE,
            )
        )
        if existing_fp:
            return existing_fp, True

    raw_event = RawEvent(
        tenant_id=tenant_id,
        source=source,
        source_event_id=source_event_id,
        raw_timestamp=raw_timestamp,
        raw_payload=json.dumps(raw_payload, ensure_ascii=False, separators=(",", ":")),
        processing_status=RawEventStatus.PENDING,
        schema_version=schema_version,
        fingerprint=fingerprint,
    )
    db.add(raw_event)
    db.flush()
    return raw_event, False


# ─────────────────────────────────────────────────────────────
# TIMEZONE RESOLUTION
# ─────────────────────────────────────────────────────────────

def resolve_timezone(db: Session, tenant_id: str, site_id: str | None = None) -> str:
    """Resolve timezone: site > tenant > Asia/Jakarta (safe default).
    Never hard-coded by tenant name.
    """
    if site_id:
        site = db.get(Site, site_id)
        if site and site.timezone:
            return site.timezone

    tenant = db.get(Tenant, tenant_id)
    if tenant and tenant.timezone:
        return tenant.timezone

    return "Asia/Jakarta"


def convert_to_utc(local_dt: datetime, tz_name: str) -> datetime:
    """Convert timezone-aware local datetime to UTC.
    Raises ValueError if local_dt is naive.
    """
    if local_dt.tzinfo is None:
        raise ValueError(
            "Cannot convert naive datetime to UTC. "
            "Datetime must be timezone-aware."
        )
    return local_dt.astimezone(timezone.utc)


def make_timezone_aware(dt: datetime, tz_name: str) -> datetime:
    """Make a naive datetime timezone-aware using the given timezone.
    If already timezone-aware, convert to the given timezone.
    """
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


# ─────────────────────────────────────────────────────────────
# OPERATING DATE RESOLUTION (cross-midnight aware)
# ─────────────────────────────────────────────────────────────

def resolve_operating_date(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    local_dt: datetime,
    shift_id: str | None = None,
    site_id: str | None = None,
) -> tuple[date, str | None, str]:
    """Resolve operating_date from event timestamp + shift.
    
    Returns: (operating_date, shift_id, processing_status)
    
    Cross-midnight rule:
    - If shift crosses midnight and event time is between 00:00 and shift end,
      operating_date = previous calendar date (shift origin date).
    - Example: NIGHT shift Sep 5 19:00 -> Sep 6 07:00
      Event at Sep 6 00:58 -> operating_date = Sep 5
    
    If shift_id not provided, attempts to resolve from roster.
    If ambiguous, returns AMBIGUOUS_SHIFT status.
    """
    event_date = local_dt.date()
    event_time = local_dt.time()

    # If shift_id provided, use it directly
    if shift_id:
        shift = db.get(ShiftTemplate, shift_id)
        if shift:
            op_date = _compute_operating_date(event_date, event_time, shift)
            return op_date, shift_id, CanonicalProcessingStatus.SHIFT_RESOLVED.value

    # Try to resolve from roster assignment
    # Check event_date and event_date - 1 (for cross-midnight)
    candidates = []
    for check_date in [event_date, event_date - timedelta(days=1)]:
        ra = db.scalar(
            select(RosterAssignment).where(
                RosterAssignment.tenant_id == tenant_id,
                RosterAssignment.employee_id == employee_id,
                RosterAssignment.operating_date == check_date,
                RosterAssignment.shift_id.isnot(None),
            )
        )
        if ra and ra.shift_id:
            shift = db.get(ShiftTemplate, ra.shift_id)
            if shift:
                computed = _compute_operating_date(event_date, event_time, shift)
                if computed == check_date:
                    candidates.append((check_date, ra.shift_id, ra.id))

    if len(candidates) == 1:
        op_date, sid, ra_id = candidates[0]
        return op_date, sid, CanonicalProcessingStatus.SHIFT_RESOLVED.value
    elif len(candidates) > 1:
        # Ambiguous -- multiple shifts could claim this event
        return event_date, None, CanonicalProcessingStatus.AMBIGUOUS_SHIFT.value
    else:
        return event_date, None, CanonicalProcessingStatus.MISSING_SHIFT.value


def _compute_operating_date(event_date: date, event_time: time, shift: ShiftTemplate) -> date:
    """Compute operating_date given event date/time and shift template.
    
    Cross-midnight: if shift.crosses_midnight and event_time < shift.end_time,
    the event belongs to the previous day's operating date.
    """
    if shift.crosses_midnight:
        # Shift starts on day D, ends on day D+1
        # Event between 00:00 and end_time belongs to day D
        if event_time < shift.end_time:
            return event_date - timedelta(days=1)
    return event_date


# ─────────────────────────────────────────────────────────────
# SHIFT RESOLUTION
# ─────────────────────────────────────────────────────────────

def resolve_shift(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    operating_date: date,
) -> tuple[str | None, str]:
    """Resolve shift from roster assignment for a given operating_date.
    
    Returns: (shift_id, processing_status)
    """
    ra = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == tenant_id,
            RosterAssignment.employee_id == employee_id,
            RosterAssignment.operating_date == operating_date,
        )
    )
    if ra and ra.shift_id:
        return ra.shift_id, CanonicalProcessingStatus.SHIFT_RESOLVED.value
    elif ra:
        return None, CanonicalProcessingStatus.MISSING_SHIFT.value
    else:
        return None, CanonicalProcessingStatus.MISSING_SHIFT.value


# ─────────────────────────────────────────────────────────────
# CANONICAL EVENT CREATION
# ─────────────────────────────────────────────────────────────

def create_canonical_event(
    db: Session,
    *,
    tenant_id: str,
    employee_id: str,
    event_type: str,  # CanonicalEventType value
    local_timestamp: datetime,  # timezone-aware
    source: str,
    source_event_id: str,
    raw_event_id: str | None = None,
    site_id: str | None = None,
    shift_id: str | None = None,
    equipment_id: str | None = None,
    location_id: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    accuracy_m: float | None = None,
    evidence_json: str | None = None,
    legacy_attendance_id: str | None = None,
) -> tuple[CanonicalAttendanceEvent, str]:
    """Create a canonical attendance event.
    
    Returns: (canonical_event, processing_status)
    
    Steps:
    1. Resolve timezone (site > tenant > default)
    2. Ensure local_timestamp is timezone-aware
    3. Convert to UTC
    4. Resolve operating_date (cross-midnight aware)
    5. Resolve shift if not provided
    6. Create canonical event
    """
    # 1. Resolve timezone
    tz_name = resolve_timezone(db, tenant_id, site_id)

    # 2. Make timezone-aware
    local_aware = make_timezone_aware(local_timestamp, tz_name)

    # 3. Convert to UTC
    utc_dt = convert_to_utc(local_aware, tz_name)

    # 4. Resolve operating_date
    op_date, resolved_shift_id, op_status = resolve_operating_date(
        db,
        tenant_id=tenant_id,
        employee_id=employee_id,
        local_dt=local_aware,
        shift_id=shift_id,
        site_id=site_id,
    )

    # 5. Use resolved shift if not provided
    if not shift_id and resolved_shift_id:
        shift_id = resolved_shift_id

    # 6. Find roster assignment
    roster_id = None
    if shift_id:
        ra = db.scalar(
            select(RosterAssignment).where(
                RosterAssignment.tenant_id == tenant_id,
                RosterAssignment.employee_id == employee_id,
                RosterAssignment.operating_date == op_date,
            )
        )
        if ra:
            roster_id = ra.id

    # 7. Determine final processing status
    if op_status == CanonicalProcessingStatus.SHIFT_RESOLVED.value:
        final_status = CanonicalProcessingStatus.VALID
    elif op_status == CanonicalProcessingStatus.AMBIGUOUS_SHIFT.value:
        final_status = CanonicalProcessingStatus.AMBIGUOUS_SHIFT
    elif op_status == CanonicalProcessingStatus.MISSING_SHIFT.value:
        final_status = CanonicalProcessingStatus.MISSING_SHIFT
    else:
        final_status = CanonicalProcessingStatus.PENDING

    canonical = CanonicalAttendanceEvent(
        tenant_id=tenant_id,
        employee_id=employee_id,
        event_type=CanonicalEventType(event_type),
        local_timestamp=local_aware,
        utc_timestamp=utc_dt,
        timezone=tz_name,
        operating_date=op_date,
        shift_id=shift_id,
        site_id=site_id,
        location_id=location_id,
        equipment_id=equipment_id,
        source=source,
        source_event_id=source_event_id,
        raw_event_id=raw_event_id,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        processing_status=final_status,
        roster_assignment_id=roster_id,
        legacy_attendance_id=legacy_attendance_id,
        evidence_json=evidence_json,
    )
    db.add(canonical)
    db.flush()

    # Link raw event to canonical
    if raw_event_id:
        raw = db.get(RawEvent, raw_event_id)
        if raw:
            raw.canonical_event_id = canonical.id
            raw.processing_status = RawEventStatus.PROCESSED

    return canonical, final_status.value if isinstance(final_status, enum.Enum) else final_status


# ─────────────────────────────────────────────────────────────
# LEGACY ATTENDANCE ADAPTER
# ─────────────────────────────────────────────────────────────

import enum

def adapt_legacy_attendance(
    db: Session,
    attendance_event: AttendanceEvent,
) -> tuple[CanonicalAttendanceEvent | None, str]:
    """Adapt existing Lumin Park AttendanceEvent to canonical format.
    
    Preserves original attendance event. Creates canonical link via legacy_attendance_id.
    Returns (canonical_event, status) or (None, reason) if skipped.
    """
    # Only adapt VALID events
    if attendance_event.status != AttendanceStatus.VALID:
        return None, f"SKIPPED_STATUS_{attendance_event.status.value}"

    # Map event type
    event_type_map = {
        AttendanceType.CHECK_IN: CanonicalEventType.CHECK_IN,
        AttendanceType.CHECK_OUT: CanonicalEventType.CHECK_OUT,
    }
    canonical_type = event_type_map.get(attendance_event.event_type)
    if not canonical_type:
        return None, f"SKIPPED_TYPE_{attendance_event.event_type.value}"

    # Resolve timezone from tenant
    tenant = db.get(Tenant, attendance_event.tenant_id)
    tz_name = tenant.timezone if tenant and tenant.timezone else "Asia/Jakarta"

    # Use captured_at_client as local timestamp
    captured = attendance_event.captured_at_client
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)

    local_aware = make_timezone_aware(captured, tz_name)
    utc_dt = convert_to_utc(local_aware, tz_name)

    # For legacy events, operating_date = work_date (already computed by old flow)
    op_date = attendance_event.work_date

    # Find roster assignment
    roster_id = None
    ra = db.scalar(
        select(RosterAssignment).where(
            RosterAssignment.tenant_id == attendance_event.tenant_id,
            RosterAssignment.employee_id == attendance_event.worker_id,
            RosterAssignment.operating_date == op_date,
        )
    )
    if ra:
        roster_id = ra.id

    canonical = CanonicalAttendanceEvent(
        tenant_id=attendance_event.tenant_id,
        employee_id=attendance_event.worker_id,
        event_type=canonical_type,
        local_timestamp=local_aware,
        utc_timestamp=utc_dt,
        timezone=tz_name,
        operating_date=op_date,
        shift_id=ra.shift_id if ra else None,
        site_id=None,  # legacy uses project_id, not site_id
        source="LEGACY_PWA",
        source_event_id=f"legacy:{attendance_event.id}",
        raw_event_id=None,
        latitude=attendance_event.latitude,
        longitude=attendance_event.longitude,
        accuracy_m=attendance_event.accuracy_m,
        processing_status=CanonicalProcessingStatus.VALID,
        roster_assignment_id=roster_id,
        legacy_attendance_id=attendance_event.id,
        evidence_json=json.dumps({
            "legacy_project_id": attendance_event.project_id,
            "legacy_device_binding_id": attendance_event.device_binding_id,
            "legacy_challenge_id": attendance_event.challenge_id,
            "legacy_distance_m": attendance_event.distance_m,
            "legacy_reason_code": attendance_event.reason_code,
        }),
    )
    db.add(canonical)
    db.flush()
    return canonical, "ADAPTED"
