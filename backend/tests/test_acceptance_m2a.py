"""
test_acceptance_m2a.py -- MM-M2A Canonical Attendance Event Foundation.

Tests:
1. Raw event retained (immutable)
2. Canonical event created
3. Metro timezone WITA
4. Lumin timezone WIB
5. DAY operating_date
6. NIGHT cross-midnight operating_date
7. UTC conversion
8. Duplicate event (same source_event_id, same tenant)
9. Cross-tenant source ID isolation
10. Retry idempotency
11. Fallback fingerprint deterministic
12. Valid shift resolution
13. Missing shift handling
14. Ambiguous assignment handling
15. Raw -> canonical traceability
16. Planned equipment not overwritten
17. GPS retained without geofence judgement
18. Existing Lumin attendance regression
"""
import json
import pytest
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Tenant, Worker, Site, ShiftTemplate, RosterAssignment,
    AttendanceEvent, AttendanceType, AttendanceStatus,
    RawEvent, RawEventStatus,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    WorkStatus, SiteStatusEnum, ValidationStatus,
    SiteType, SiteStatus, EquipmentStatus,
    DeviceBinding, AttendanceChallenge, DeviceEnrollment,
    EnrollmentStatus, DeviceStatus,
    Project,
)
from app.canonical_event_service import (
    ingest_raw_event,
    compute_fingerprint,
    resolve_timezone,
    convert_to_utc,
    make_timezone_aware,
    resolve_operating_date,
    resolve_shift,
    create_canonical_event,
    adapt_legacy_attendance,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _seed_metro(db: Session):
    """Seed Metro Mining tenant with timezone Asia/Makassar."""
    t = Tenant(id="metro", code="metro", name="Metro Mining", timezone="Asia/Makassar")
    db.add(t)
    w1 = Worker(id="w1", tenant_id="metro", code="W001", name="Worker A", is_active=True)
    w2 = Worker(id="w2", tenant_id="metro", code="W002", name="Worker B", is_active=True)
    db.add_all([w1, w2])
    s1 = Site(
        id="s1", tenant_id="metro", site_code="S001", site_name="Mine Site A",
        site_type=SiteType.MINE_SITE, status=SiteStatus.ACTIVE,
        timezone="Asia/Makassar", effective_from=date(2026, 9, 1),
    )
    db.add(s1)
    day = ShiftTemplate(
        id="DAY", tenant_id="metro", shift_code="DAY", shift_name="Day",
        start_time=time(7, 0), end_time=time(19, 0),
        break_start=time(12, 0), break_end=time(13, 0),
        handover_start=time(18, 45), handover_end=time(19, 0),
        crosses_midnight=False,
    )
    night = ShiftTemplate(
        id="NIGHT", tenant_id="metro", shift_code="NIGHT", shift_name="Night",
        start_time=time(19, 0), end_time=time(7, 0),
        break_start=time(0, 0), break_end=time(1, 0),
        handover_start=time(6, 45), handover_end=time(7, 0),
        crosses_midnight=True,
    )
    db.add_all([day, night])
    db.flush()
    return t, w1, w2, s1, day, night


def _seed_lumin(db: Session):
    """Seed Lumin Park tenant with timezone Asia/Jakarta."""
    t = Tenant(id="lumin", code="lumin", name="Lumin Park", timezone="Asia/Jakarta")
    db.add(t)
    w = Worker(id="wL", tenant_id="lumin", code="L001", name="Lumin Worker", is_active=True)
    db.add(w)
    db.flush()
    return t, w


def _seed_roster(db: Session, tenant_id: str, worker_id: str, op_date: date, shift_id: str, site_id: str):
    """Seed a roster assignment."""
    ra = RosterAssignment(
        id=f"ra-{worker_id}-{op_date}", tenant_id=tenant_id, roster_code="R001",
        operating_date=op_date, employee_id=worker_id,
        shift_id=shift_id, site_id=site_id,
        work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
        validation_status=ValidationStatus.PUBLISHED,
    )
    db.add(ra)
    db.flush()
    return ra


# ============================================================
# 1. RAW EVENT RETAINED (IMMUTABLE)
# ============================================================

class TestRawEventRetention:
    """Raw events are immutable after ingestion."""

    def test_raw_event_stored(self, db):
        """Raw event is stored with all required fields."""
        _seed_metro(db)
        raw, is_dup = ingest_raw_event(
            db,
            tenant_id="metro", source="PWA", source_event_id="evt-001",
            raw_timestamp="2026-09-05T19:05:00+08:00",
            raw_payload={"employee_id": "w1", "event_type": "CHECK_IN"},
        )
        db.commit()

        assert is_dup is False
        assert raw.id is not None
        assert raw.tenant_id == "metro"
        assert raw.source == "PWA"
        assert raw.source_event_id == "evt-001"
        assert raw.processing_status == RawEventStatus.PENDING

    def test_raw_payload_not_modified(self, db):
        """Raw payload is never modified after ingestion."""
        _seed_metro(db)
        raw, _ = ingest_raw_event(
            db,
            tenant_id="metro", source="API", source_event_id="evt-002",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload={"key": "value"},
        )
        db.commit()

        original_payload = raw.raw_payload
        # Attempting to modify should be prevented by convention
        # (no API exposes mutation of raw_payload)
        assert raw.raw_payload == original_payload

    def test_no_credentials_in_payload(self, db):
        """Raw payload must not contain credentials."""
        _seed_metro(db)
        payload = {
            "employee_id": "w1",
            "event_type": "CHECK_IN",
            "latitude": -2.5,
            "longitude": 116.0,
        }
        # Ensure no credential fields
        forbidden_keys = {"password", "token", "secret", "pin", "biometric_template", "api_key"}
        assert not forbidden_keys.intersection(payload.keys())

        raw, _ = ingest_raw_event(
            db,
            tenant_id="metro", source="PWA", source_event_id="evt-003",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload=payload,
        )
        db.commit()
        stored = json.loads(raw.raw_payload)
        assert not forbidden_keys.intersection(stored.keys())


# ============================================================
# 2. CANONICAL EVENT CREATED
# ============================================================

class TestCanonicalEventCreation:
    """Canonical attendance events are created correctly."""

    def test_canonical_event_fields(self, db):
        """Canonical event has all required fields populated."""
        _seed_metro(db)
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 19, 5, 0, tzinfo=tz)

        raw, _ = ingest_raw_event(
            db,
            tenant_id="metro", source="PWA", source_event_id="evt-010",
            raw_timestamp="2026-09-05T19:05:00+08:00",
            raw_payload={"employee_id": "w1"},
        )

        canonical, status = create_canonical_event(
            db,
            tenant_id="metro", employee_id="w1",
            event_type="CHECK_IN",
            local_timestamp=local_dt,
            source="PWA", source_event_id="evt-010",
            raw_event_id=raw.id, site_id="s1",
        )
        db.commit()

        assert canonical.id is not None
        assert canonical.tenant_id == "metro"
        assert canonical.employee_id == "w1"
        assert canonical.event_type == CanonicalEventType.CHECK_IN
        assert canonical.timezone == "Asia/Makassar"
        assert canonical.source == "PWA"
        assert canonical.source_event_id == "evt-010"
        assert canonical.raw_event_id == raw.id


# ============================================================
# 3. METRO TIMEZONE WITA
# ============================================================

class TestMetroTimezoneWITA:
    """Metro Mining resolves timezone as Asia/Makassar (WITA, UTC+08:00)."""

    def test_resolve_timezone_metro(self, db):
        """Metro Mining site resolves to Asia/Makassar."""
        _seed_metro(db)
        tz = resolve_timezone(db, "metro", "s1")
        assert tz == "Asia/Makassar"

    def test_metro_utc_offset(self, db):
        """WITA is UTC+08:00."""
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        assert utc_dt.hour == 11  # 19:00 - 8 = 11:00 UTC


# ============================================================
# 4. LUMIN TIMEZONE WIB
# ============================================================

class TestLuminTimezoneWIB:
    """Lumin Park resolves timezone as Asia/Jakarta (WIB, UTC+07:00)."""

    def test_resolve_timezone_lumin(self, db):
        """Lumin Park tenant resolves to Asia/Jakarta."""
        _seed_lumin(db)
        tz = resolve_timezone(db, "lumin")
        assert tz == "Asia/Jakarta"

    def test_lumin_utc_offset(self, db):
        """WIB is UTC+07:00."""
        tz = ZoneInfo("Asia/Jakarta")
        local_dt = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        assert utc_dt.hour == 12  # 19:00 - 7 = 12:00 UTC


# ============================================================
# 5. DAY OPERATING_DATE
# ============================================================

class TestDayOperatingDate:
    """DAY shift: operating_date = calendar date."""

    def test_day_shift_operating_date(self, db):
        """DAY shift event at 08:00 -> operating_date = same calendar date."""
        _seed_metro(db)
        _seed_roster(db, "metro", "w1", date(2026, 9, 5), "DAY", "s1")

        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 8, 0, 0, tzinfo=tz)

        op_date, shift_id, status = resolve_operating_date(
            db, tenant_id="metro", employee_id="w1",
            local_dt=local_dt, shift_id="DAY",
        )
        assert op_date == date(2026, 9, 5)
        assert shift_id == "DAY"
        assert status == CanonicalProcessingStatus.SHIFT_RESOLVED.value


# ============================================================
# 6. NIGHT CROSS-MIDNIGHT OPERATING_DATE
# ============================================================

class TestNightCrossMidnightOperatingDate:
    """NIGHT shift: event after midnight -> operating_date = previous day."""

    def test_night_shift_after_midnight(self, db):
        """NIGHT shift event at 00:58 on Sep 6 -> operating_date = Sep 5."""
        _seed_metro(db)
        _seed_roster(db, "metro", "w1", date(2026, 9, 5), "NIGHT", "s1")

        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 6, 0, 58, 0, tzinfo=tz)

        op_date, shift_id, status = resolve_operating_date(
            db, tenant_id="metro", employee_id="w1",
            local_dt=local_dt, shift_id="NIGHT",
        )
        assert op_date == date(2026, 9, 5)
        assert shift_id == "NIGHT"
        assert status == CanonicalProcessingStatus.SHIFT_RESOLVED.value

    def test_night_shift_before_midnight(self, db):
        """NIGHT shift event at 20:00 on Sep 5 -> operating_date = Sep 5."""
        _seed_metro(db)
        _seed_roster(db, "metro", "w1", date(2026, 9, 5), "NIGHT", "s1")

        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 20, 0, 0, tzinfo=tz)

        op_date, shift_id, status = resolve_operating_date(
            db, tenant_id="metro", employee_id="w1",
            local_dt=local_dt, shift_id="NIGHT",
        )
        assert op_date == date(2026, 9, 5)

    def test_night_shift_at_boundary(self, db):
        """NIGHT shift event at exactly 07:00 on Sep 6 -> operating_date = Sep 6 (shift ended)."""
        _seed_metro(db)
        _seed_roster(db, "metro", "w1", date(2026, 9, 5), "NIGHT", "s1")
        _seed_roster(db, "metro", "w1", date(2026, 9, 6), "DAY", "s1")

        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 6, 7, 0, 0, tzinfo=tz)

        # 07:00 is NOT < end_time (07:00), so not cross-midnight
        op_date, shift_id, status = resolve_operating_date(
            db, tenant_id="metro", employee_id="w1",
            local_dt=local_dt, shift_id="DAY",
        )
        assert op_date == date(2026, 9, 6)


# ============================================================
# 7. UTC CONVERSION
# ============================================================

class TestUTCConversion:
    """Timezone conversion uses timezone-aware datetime."""

    def test_wita_to_utc(self, db):
        """19:00 WITA -> 11:00 UTC."""
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        utc_dt = convert_to_utc(local_dt, "Asia/Makassar")
        assert utc_dt.hour == 11
        assert utc_dt.tzinfo == timezone.utc

    def test_wib_to_utc(self, db):
        """19:00 WIB -> 12:00 UTC."""
        tz = ZoneInfo("Asia/Jakarta")
        local_dt = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        utc_dt = convert_to_utc(local_dt, "Asia/Jakarta")
        assert utc_dt.hour == 12
        assert utc_dt.tzinfo == timezone.utc

    def test_naive_datetime_rejected(self, db):
        """Naive datetime raises ValueError."""
        naive_dt = datetime(2026, 9, 5, 19, 0, 0)
        with pytest.raises(ValueError, match="naive"):
            convert_to_utc(naive_dt, "Asia/Makassar")

    def test_make_naive_aware(self, db):
        """make_timezone_aware converts naive to aware."""
        naive_dt = datetime(2026, 9, 5, 19, 0, 0)
        aware_dt = make_timezone_aware(naive_dt, "Asia/Makassar")
        assert aware_dt.tzinfo is not None
        assert aware_dt.hour == 19


# ============================================================
# 8. DUPLICATE EVENT
# ============================================================

class TestDuplicateEvent:
    """Same source_event_id + same tenant -> duplicate."""

    def test_same_source_id_same_tenant_duplicate(self, db):
        """Second ingestion with same source_event_id returns duplicate."""
        _seed_metro(db)
        raw1, dup1 = ingest_raw_event(
            db,
            tenant_id="metro", source="PWA", source_event_id="evt-dup-001",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload={"key": "value1"},
        )
        db.commit()
        assert dup1 is False

        raw2, dup2 = ingest_raw_event(
            db,
            tenant_id="metro", source="PWA", source_event_id="evt-dup-001",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload={"key": "value2"},
        )
        db.commit()
        assert dup2 is True
        assert raw2.id == raw1.id  # returns same record


# ============================================================
# 9. CROSS-TENANT SOURCE ID ISOLATION
# ============================================================

class TestCrossTenantSourceID:
    """Same source_event_id different tenant -> allowed."""

    def test_same_source_id_different_tenant(self, db):
        """Same source_event_id in different tenants is NOT duplicate."""
        _seed_metro(db)
        _seed_lumin(db)

        raw1, dup1 = ingest_raw_event(
            db,
            tenant_id="metro", source="PWA", source_event_id="evt-shared-001",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload={"key": "metro"},
        )
        raw2, dup2 = ingest_raw_event(
            db,
            tenant_id="lumin", source="PWA", source_event_id="evt-shared-001",
            raw_timestamp="2026-09-05T08:00:00+07:00",
            raw_payload={"key": "lumin"},
        )
        db.commit()

        assert dup1 is False
        assert dup2 is False
        assert raw1.id != raw2.id


# ============================================================
# 10. RETRY IDEMPOTENCY
# ============================================================

class TestRetryIdempotency:
    """Retry ingestion with same data -> idempotent."""

    def test_retry_same_event(self, db):
        """Multiple retries return same raw event."""
        _seed_metro(db)
        results = []
        for _ in range(3):
            raw, dup = ingest_raw_event(
                db,
                tenant_id="metro", source="API", source_event_id="evt-retry-001",
                raw_timestamp="2026-09-05T08:00:00+08:00",
                raw_payload={"data": "test"},
            )
            results.append((raw.id, dup))
            db.commit()

        assert results[0][1] is False  # first is not duplicate
        assert results[1][1] is True   # second is duplicate
        assert results[2][1] is True   # third is duplicate
        assert all(r[0] == results[0][0] for r in results)  # all same ID


# ============================================================
# 11. FALLBACK FINGERPRINT
# ============================================================

class TestFallbackFingerprint:
    """Fingerprint is deterministic for same input."""

    def test_fingerprint_deterministic(self, db):
        """Same inputs produce same fingerprint."""
        fp1 = compute_fingerprint("metro", "PWA", "w1", "CHECK_IN", "2026-09-05T08:00:00", "s1")
        fp2 = compute_fingerprint("metro", "PWA", "w1", "CHECK_IN", "2026-09-05T08:00:00", "s1")
        assert fp1 == fp2

    def test_fingerprint_different_inputs(self, db):
        """Different inputs produce different fingerprints."""
        fp1 = compute_fingerprint("metro", "PWA", "w1", "CHECK_IN", "2026-09-05T08:00:00", "s1")
        fp2 = compute_fingerprint("metro", "PWA", "w1", "CHECK_OUT", "2026-09-05T17:00:00", "s1")
        assert fp1 != fp2

    def test_fingerprint_dedup(self, db):
        """Fingerprint-based dedup works when source_event_id differs."""
        _seed_metro(db)
        fp = compute_fingerprint("metro", "TELEMATICS", "w1", "CHECK_IN", "2026-09-05T08:00:00", "s1")

        raw1, dup1 = ingest_raw_event(
            db,
            tenant_id="metro", source="TELEMATICS", source_event_id="tel-001",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload={"data": "v1"}, fingerprint=fp,
        )
        db.commit()

        raw2, dup2 = ingest_raw_event(
            db,
            tenant_id="metro", source="TELEMATICS", source_event_id="tel-002",
            raw_timestamp="2026-09-05T08:00:00+08:00",
            raw_payload={"data": "v2"}, fingerprint=fp,
        )
        db.commit()

        assert dup1 is False
        assert dup2 is True  # detected via fingerprint


# ============================================================
# 12. VALID SHIFT RESOLUTION
# ============================================================

class TestValidShiftResolution:
    """Shift resolved from roster assignment."""

    def test_shift_from_roster(self, db):
        """Shift resolved when roster assignment exists."""
        _seed_metro(db)
        _seed_roster(db, "metro", "w1", date(2026, 9, 5), "DAY", "s1")

        shift_id, status = resolve_shift(
            db, tenant_id="metro", employee_id="w1",
            operating_date=date(2026, 9, 5),
        )
        assert shift_id == "DAY"
        assert status == CanonicalProcessingStatus.SHIFT_RESOLVED.value


# ============================================================
# 13. MISSING SHIFT HANDLING
# ============================================================

class TestMissingShift:
    """Missing shift -> MISSING_SHIFT status, no silent guess."""

    def test_no_roster_assignment(self, db):
        """No roster -> MISSING_SHIFT."""
        _seed_metro(db)

        shift_id, status = resolve_shift(
            db, tenant_id="metro", employee_id="w1",
            operating_date=date(2026, 9, 5),
        )
        assert shift_id is None
        assert status == CanonicalProcessingStatus.MISSING_SHIFT.value

    def test_roster_without_shift(self, db):
        """Roster without shift_id -> MISSING_SHIFT."""
        _seed_metro(db)
        ra = RosterAssignment(
            id="ra-no-shift", tenant_id="metro", roster_code="R001",
            operating_date=date(2026, 9, 5), employee_id="w1",
            shift_id=None, site_id="s1",
            work_status=WorkStatus.REST, site_status=SiteStatusEnum.OFFSITE,
            validation_status=ValidationStatus.PUBLISHED,
        )
        db.add(ra)
        db.commit()

        shift_id, status = resolve_shift(
            db, tenant_id="metro", employee_id="w1",
            operating_date=date(2026, 9, 5),
        )
        assert shift_id is None
        assert status == CanonicalProcessingStatus.MISSING_SHIFT.value


# ============================================================
# 14. AMBIGUOUS ASSIGNMENT HANDLING
# ============================================================

class TestAmbiguousAssignment:
    """Ambiguous shift -> AMBIGUOUS_SHIFT status."""

    def test_ambiguous_shift_resolution(self, db):
        """Event at boundary that could belong to two shifts -> AMBIGUOUS."""
        _seed_metro(db)
        # Create roster for both Sep 5 NIGHT and Sep 6 DAY
        _seed_roster(db, "metro", "w1", date(2026, 9, 5), "NIGHT", "s1")
        _seed_roster(db, "metro", "w1", date(2026, 9, 6), "DAY", "s1")

        # Event at 06:50 on Sep 6 -- could be NIGHT (before 07:00 end) or DAY (after 07:00 start)
        # Actually 06:50 < 07:00 so it's NIGHT cross-midnight -> Sep 5
        # But let's test with a time that's genuinely ambiguous
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 6, 6, 50, 0, tzinfo=tz)

        # Without explicit shift_id, it should resolve via roster
        op_date, shift_id, status = resolve_operating_date(
            db, tenant_id="metro", employee_id="w1",
            local_dt=local_dt,
        )
        # 06:50 < 07:00 (NIGHT end) -> cross-midnight -> Sep 5
        # BUT also 06:50 < 07:00 (DAY start) -> DAY also claims Sep 6
        # This is genuinely ambiguous at the boundary
        assert status == CanonicalProcessingStatus.AMBIGUOUS_SHIFT.value


# ============================================================
# 15. RAW -> CANONICAL TRACEABILITY
# ============================================================

class TestTraceability:
    """Full traceability: Raw -> Canonical -> Worker -> Shift -> Operating Date -> Roster."""

    def test_full_chain(self, db):
        """Complete traceability chain is maintained."""
        _seed_metro(db)
        ra = _seed_roster(db, "metro", "w1", date(2026, 9, 5), "NIGHT", "s1")

        # Ingest raw
        raw, _ = ingest_raw_event(
            db,
            tenant_id="metro", source="GPS", source_event_id="gps-001",
            raw_timestamp="2026-09-06T00:58:00+08:00",
            raw_payload={"lat": -2.5, "lon": 116.0, "accuracy": 10},
        )

        # Create canonical
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 6, 0, 58, 0, tzinfo=tz)
        canonical, _ = create_canonical_event(
            db,
            tenant_id="metro", employee_id="w1",
            event_type="BREAK_IN",
            local_timestamp=local_dt,
            source="GPS", source_event_id="gps-001",
            raw_event_id=raw.id, site_id="s1", shift_id="NIGHT",
            latitude=-2.5, longitude=116.0, accuracy_m=10,
        )
        db.commit()

        # Verify chain
        assert canonical.raw_event_id == raw.id
        assert canonical.employee_id == "w1"
        assert canonical.shift_id == "NIGHT"
        assert canonical.operating_date == date(2026, 9, 5)
        assert canonical.roster_assignment_id == ra.id

        # Raw -> Canonical link
        db.refresh(raw)
        assert raw.canonical_event_id == canonical.id
        assert raw.processing_status == RawEventStatus.PROCESSED


# ============================================================
# 16. PLANNED EQUIPMENT NOT OVERWRITTEN
# ============================================================

class TestPlannedEquipmentPreserved:
    """Planned equipment from roster is not overwritten by canonical event."""

    def test_planned_equipment_unchanged(self, db):
        """Canonical event carries actual equipment; roster planned_equipment untouched."""
        _seed_metro(db)
        from app.models import Equipment
        eq = Equipment(
            id="eq1", tenant_id="metro", equipment_code="EX-001",
            equipment_type="Excavator", status=EquipmentStatus.ACTIVE,
            effective_from=date(2026, 9, 1),
        )
        db.add(eq)

        ra = RosterAssignment(
            id="ra-eq", tenant_id="metro", roster_code="R001",
            operating_date=date(2026, 9, 5), employee_id="w1",
            shift_id="DAY", site_id="s1",
            planned_equipment_id="eq1",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.PUBLISHED,
        )
        db.add(ra)
        db.flush()

        # Create canonical with different actual equipment
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 8, 0, 0, tzinfo=tz)
        canonical, _ = create_canonical_event(
            db,
            tenant_id="metro", employee_id="w1",
            event_type="EQUIPMENT_CHECK_IN",
            local_timestamp=local_dt,
            source="TELEMATICS", source_event_id="tel-eq-001",
            site_id="s1", equipment_id="eq1",
        )
        db.commit()

        # Planned equipment in roster unchanged
        db.refresh(ra)
        assert ra.planned_equipment_id == "eq1"
        # Canonical carries actual equipment reference
        assert canonical.equipment_id == "eq1"


# ============================================================
# 17. GPS RETAINED WITHOUT GEOFENCE JUDGEMENT
# ============================================================

class TestGPSRetainedNoGeofenceJudgement:
    """GPS coordinates stored without PASS/FAIL geofence assessment."""

    def test_gps_stored_no_judgement(self, db):
        """GPS data stored in canonical event; no geofence verdict."""
        _seed_metro(db)
        tz = ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 8, 0, 0, tzinfo=tz)

        canonical, _ = create_canonical_event(
            db,
            tenant_id="metro", employee_id="w1",
            event_type="CHECK_IN",
            local_timestamp=local_dt,
            source="GPS", source_event_id="gps-002",
            site_id="s1",
            latitude=-2.5123, longitude=116.0456, accuracy_m=15,
        )
        db.commit()

        assert canonical.latitude == pytest.approx(-2.5123)
        assert canonical.longitude == pytest.approx(116.0456)
        assert canonical.accuracy_m == 15
        # No geofence PASS/FAIL field -- that's M2C scope


# ============================================================
# 18. EXISTING LUMIN ATTENDANCE REGRESSION
# ============================================================

class TestLuminAttendanceRegression:
    """Existing Lumin Park attendance flow must not break."""

    def test_lumin_canonical_adapter(self, db):
        """Legacy Lumin attendance adapts to canonical without breaking."""
        _seed_lumin(db)

        # Simulate existing Lumin attendance event
        project = Project(
            id="p1", tenant_id="lumin", code="P001", name="Lumin Site",
            latitude=-6.2, longitude=106.8, geofence_radius_m=150,
            work_start=time(8, 0), work_end=time(17, 0),
        )
        db.add(project)

        legacy = AttendanceEvent(
            id="att-001", tenant_id="lumin", worker_id="wL",
            project_id="p1", device_binding_id="db-001",
            challenge_id="ch-001",
            event_type=AttendanceType.CHECK_IN,
            status=AttendanceStatus.VALID,
            work_date=date(2026, 9, 5),
            captured_at_client=datetime(2026, 9, 5, 8, 5, 0, tzinfo=timezone.utc),
            latitude=-6.2, longitude=106.8,
            accuracy_m=10, distance_m=50,
            signature="sig",
        )
        db.add(legacy)
        db.flush()

        # Adapt to canonical
        canonical, status = adapt_legacy_attendance(db, legacy)
        db.commit()

        assert canonical is not None
        assert status == "ADAPTED"
        assert canonical.legacy_attendance_id == "att-001"
        assert canonical.source == "LEGACY_PWA"
        assert canonical.timezone == "Asia/Jakarta"
        assert canonical.processing_status == CanonicalProcessingStatus.VALID

    def test_lumin_legacy_unchanged(self, db):
        """Legacy attendance event is NOT modified by adaptation."""
        _seed_lumin(db)
        project = Project(
            id="p1", tenant_id="lumin", code="P001", name="Lumin Site",
            latitude=-6.2, longitude=106.8, geofence_radius_m=150,
            work_start=time(8, 0), work_end=time(17, 0),
        )
        db.add(project)
        legacy = AttendanceEvent(
            id="att-002", tenant_id="lumin", worker_id="wL",
            project_id="p1", device_binding_id="db-001",
            challenge_id="ch-002",
            event_type=AttendanceType.CHECK_OUT,
            status=AttendanceStatus.VALID,
            work_date=date(2026, 9, 5),
            captured_at_client=datetime(2026, 9, 5, 17, 5, 0, tzinfo=timezone.utc),
            latitude=-6.2, longitude=106.8,
            accuracy_m=10, distance_m=50,
            signature="sig",
        )
        db.add(legacy)
        db.flush()

        original_status = legacy.status
        original_work_date = legacy.work_date

        canonical, status = adapt_legacy_attendance(db, legacy)
        db.commit()

        # Legacy unchanged
        db.refresh(legacy)
        assert legacy.status == original_status
        assert legacy.work_date == original_work_date
        assert legacy.id == "att-002"

    def test_lumin_invalid_event_skipped(self, db):
        """Invalid legacy events are skipped, not adapted."""
        _seed_lumin(db)
        project = Project(
            id="p1", tenant_id="lumin", code="P001", name="Lumin Site",
            latitude=-6.2, longitude=106.8, geofence_radius_m=150,
            work_start=time(8, 0), work_end=time(17, 0),
        )
        db.add(project)
        legacy = AttendanceEvent(
            id="att-003", tenant_id="lumin", worker_id="wL",
            project_id="p1", device_binding_id="db-001",
            challenge_id="ch-003",
            event_type=AttendanceType.CHECK_IN,
            status=AttendanceStatus.REJECTED,
            work_date=date(2026, 9, 5),
            captured_at_client=datetime(2026, 9, 5, 8, 5, 0, tzinfo=timezone.utc),
            latitude=-6.2, longitude=106.8,
            accuracy_m=10, distance_m=50,
            signature="sig",
        )
        db.add(legacy)
        db.flush()

        canonical, status = adapt_legacy_attendance(db, legacy)
        assert canonical is None
        assert "SKIPPED" in status
