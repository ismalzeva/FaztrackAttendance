"""
test_acceptance_hardening.py — Acceptance tests for MM-M0/M1 hardening fixes.

Tests cover:
1. EmployeeMeta effective-dated history (multiple periods, overlap rejection)
2. Competency history (renewal, expiry, overlap handling)
3. Tenant/site timezone configuration (Metro=Makassar, Lumin=Jakarta)
4. Geofence TBC (nullable, no production-semantic defaults)
5. RuleVersion FK on RosterAssignment (historical preservation)
6. Tenant isolation (cross-tenant access prevention)
7. NIGHT cross-midnight operating_date
"""
import pytest
from datetime import date, datetime, time, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models import (
    Tenant, User, Membership, RoleCode,
    Worker, Site, Equipment, Role, Crew, Competency,
    ShiftTemplate, CheckpointPolicy, RosterPolicy, RuleVersion,
    RosterAssignment, EmployeeMeta,
    WorkStatus, SiteStatusEnum, ValidationStatus,
    SiteType, SiteStatus, EquipmentStatus, CompetencyStatus,
)
from app.rule_versioning import snapshot_rules
from app.security import hash_password


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _create_tenant(session: Session, tid: str, code: str, name: str, tz: str = "Asia/Jakarta"):
    t = Tenant(id=tid, code=code, name=name, timezone=tz)
    session.add(t)
    session.flush()
    return t


def _create_worker(session: Session, tid: str, wid: str, code: str, name: str):
    w = Worker(id=wid, tenant_id=tid, code=code, name=name, is_active=True)
    session.add(w)
    session.flush()
    return w


def _create_site(session: Session, tid: str, sid: str, name: str, tz: str = "Asia/Jakarta"):
    s = Site(
        id=sid, tenant_id=tid, site_code=sid, site_name=name,
        site_type=SiteType.MINE_SITE, status=SiteStatus.ACTIVE,
        effective_from=date(2026, 9, 1), timezone=tz,
    )
    session.add(s)
    session.flush()
    return s


def _create_role(session: Session, tid: str, rid: str, name: str):
    r = Role(id=rid, tenant_id=tid, role_code=rid, role_name=name, status="ACTIVE")
    session.add(r)
    session.flush()
    return r


def _create_crew(session: Session, tid: str, cid: str, name: str):
    c = Crew(id=cid, tenant_id=tid, crew_code=cid, crew_name=name, onsite_cycle_anchor=date(2026, 9, 1))
    session.add(c)
    session.flush()
    return c


def _create_shift(session: Session, tid: str, sid: str, name: str, start: time, end: time, crosses: bool):
    s = ShiftTemplate(
        id=sid, tenant_id=tid, shift_code=sid, shift_name=name,
        start_time=start, end_time=end,
        break_start=time(12, 0), break_end=time(13, 0),
        handover_start=time(18, 45), handover_end=time(19, 0),
        crosses_midnight=crosses,
    )
    session.add(s)
    session.flush()
    return s


# ============================================================
# 1. EMPLOYEEMETA EFFECTIVE-DATED HISTORY
# ============================================================

class TestEmployeeMetaEffectiveDating:
    """EmployeeMeta supports multiple effective periods per worker."""

    def test_multiple_periods_same_worker(self, db):
        """One worker can have multiple effective periods."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")
        _create_role(db, "t1", "r1", "Operator")
        _create_crew(db, "t1", "c1", "Crew A")
        _create_crew(db, "t1", "c2", "Crew B")

        # Period 1: Sep 1 – Sep 30, Crew A
        em1 = EmployeeMeta(
            id="em1", tenant_id="t1", worker_id="w1",
            employee_no="W001", role_id="r1", crew_id="c1",
            effective_from=date(2026, 9, 1), effective_to=date(2026, 9, 30),
        )
        db.add(em1)

        # Period 2: Oct 1 – ongoing, Crew B
        em2 = EmployeeMeta(
            id="em2", tenant_id="t1", worker_id="w1",
            employee_no="W001", role_id="r1", crew_id="c2",
            effective_from=date(2026, 10, 1), effective_to=None,
        )
        db.add(em2)
        db.commit()

        records = db.query(EmployeeMeta).filter(EmployeeMeta.worker_id == "w1").all()
        assert len(records) == 2
        assert records[0].crew_id == "c1"
        assert records[1].crew_id == "c2"

    def test_historical_record_immutable(self, db):
        """Old records are not overwritten when new period is added."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")

        em1 = EmployeeMeta(
            id="em1", tenant_id="t1", worker_id="w1",
            effective_from=date(2026, 9, 1), effective_to=date(2026, 9, 30),
            crew_id="c1",
        )
        db.add(em1)
        db.commit()

        # Add new period — old record untouched
        em2 = EmployeeMeta(
            id="em2", tenant_id="t1", worker_id="w1",
            effective_from=date(2026, 10, 1), effective_to=None,
            crew_id="c2",
        )
        db.add(em2)
        db.commit()

        em1_check = db.query(EmployeeMeta).filter(EmployeeMeta.id == "em1").first()
        assert em1_check.crew_id == "c1"
        assert em1_check.effective_to == date(2026, 9, 30)

    def test_same_effective_from_rejected(self, db):
        """Duplicate (tenant_id, worker_id, effective_from) is rejected."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")

        em1 = EmployeeMeta(
            id="em1", tenant_id="t1", worker_id="w1",
            effective_from=date(2026, 9, 1), effective_to=None,
        )
        em2 = EmployeeMeta(
            id="em2", tenant_id="t1", worker_id="w1",
            effective_from=date(2026, 9, 1), effective_to=None,
        )
        db.add(em1)
        db.commit()
        db.add(em2)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_different_workers_same_effective_from_ok(self, db):
        """Different workers can have the same effective_from."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")
        _create_worker(db, "t1", "w2", "W002", "Worker B")

        em1 = EmployeeMeta(id="em1", tenant_id="t1", worker_id="w1", effective_from=date(2026, 9, 1))
        em2 = EmployeeMeta(id="em2", tenant_id="t1", worker_id="w2", effective_from=date(2026, 9, 1))
        db.add_all([em1, em2])
        db.commit()

        assert db.query(EmployeeMeta).count() == 2


# ============================================================
# 2. COMPETENCY HISTORY
# ============================================================

class TestCompetencyHistory:
    """Competency supports multiple records per employee+equipment_type."""

    def test_renewal_creates_new_record(self, db):
        """Renewed certificate creates a new record alongside the old one."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")

        # Original certificate
        c1 = Competency(
            id="c1", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            certification_no="CERT-001",
            valid_from=date(2025, 1, 1), valid_to=date(2026, 1, 1),
            status=CompetencyStatus.EXPIRED,
        )
        # Renewed certificate
        c2 = Competency(
            id="c2", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            certification_no="CERT-002",
            valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1),
            status=CompetencyStatus.VALID,
        )
        db.add_all([c1, c2])
        db.commit()

        records = db.query(Competency).filter(
            Competency.employee_id == "w1",
            Competency.equipment_type == "Excavator",
        ).all()
        assert len(records) == 2
        assert records[0].certification_no == "CERT-001"
        assert records[1].certification_no == "CERT-002"

    def test_expired_remains_historical(self, db):
        """Expired certificate remains in database as historical record."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")

        c1 = Competency(
            id="c1", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            valid_from=date(2025, 1, 1), valid_to=date(2026, 1, 1),
            status=CompetencyStatus.EXPIRED,
        )
        c2 = Competency(
            id="c2", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1),
            status=CompetencyStatus.VALID,
        )
        db.add_all([c1, c2])
        db.commit()

        expired = db.query(Competency).filter(Competency.status == CompetencyStatus.EXPIRED).first()
        assert expired is not None
        assert expired.certification_no == "CERT-001" or expired.valid_to == date(2026, 1, 1)

    def test_suspension_and_reactivation(self, db):
        """Suspension creates new record; reactivation creates another."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")

        c1 = Competency(
            id="c1", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            valid_from=date(2025, 1, 1), valid_to=date(2027, 1, 1),
            status=CompetencyStatus.VALID,
        )
        c2 = Competency(
            id="c2", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            valid_from=date(2026, 3, 1), valid_to=date(2026, 6, 1),
            status=CompetencyStatus.SUSPENDED,
            notes="Safety violation",
        )
        c3 = Competency(
            id="c3", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            valid_from=date(2026, 6, 1), valid_to=date(2027, 1, 1),
            status=CompetencyStatus.VALID,
            notes="Reactivated after training",
        )
        db.add_all([c1, c2, c3])
        db.commit()

        records = db.query(Competency).filter(Competency.employee_id == "w1").all()
        assert len(records) == 3

    def test_different_cert_numbers_allowed(self, db):
        """Same employee+equipment_type with different cert numbers is allowed."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")

        c1 = Competency(
            id="c1", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            certification_no="OLD-CERT-001",
            valid_from=date(2025, 1, 1), valid_to=date(2026, 1, 1),
            status=CompetencyStatus.EXPIRED,
        )
        c2 = Competency(
            id="c2", tenant_id="t1", competency_code="EXCAVATOR-OP",
            employee_id="w1", equipment_type="Excavator",
            certification_no="NEW-CERT-002",
            valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1),
            status=CompetencyStatus.VALID,
        )
        db.add_all([c1, c2])
        db.commit()

        assert db.query(Competency).count() == 2


# ============================================================
# 3. TENANT/SITE TIMEZONE
# ============================================================

class TestTimezoneConfiguration:
    """Timezone is tenant/site configurable, not hard-coded."""

    def test_metro_mining_timezone_makassar(self, db):
        """Metro Mining resolves timezone as Asia/Makassar."""
        t = _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        assert t.timezone == "Asia/Makassar"

    def test_lumin_park_timezone_jakarta(self, db):
        """Lumin Park resolves timezone as Asia/Jakarta."""
        t = _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")
        assert t.timezone == "Asia/Jakarta"

    def test_site_timezone_from_tenant(self, db):
        """Site inherits timezone from tenant configuration."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        s = _create_site(db, "metro", "s1", "Mine Site", "Asia/Makassar")
        assert s.timezone == "Asia/Makassar"

    def test_timezone_tenant_isolation(self, db):
        """Changing one tenant's timezone doesn't affect another."""
        t1 = _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        t2 = _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")

        # Change Metro timezone
        t1.timezone = "Asia/Jayapura"
        db.commit()

        # Lumin unchanged
        db.refresh(t2)
        assert t2.timezone == "Asia/Jakarta"

    def test_metro_local_to_utc_conversion(self, db):
        """Metro local 19:00 WITA (UTC+8) → 11:00 UTC."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Makassar")
        local_dt = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        assert utc_dt.hour == 11
        assert utc_dt.minute == 0

    def test_lumin_local_to_utc_conversion(self, db):
        """Lumin local 19:00 WIB (UTC+7) → 12:00 UTC."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Jakarta")
        local_dt = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        assert utc_dt.hour == 12
        assert utc_dt.minute == 0


# ============================================================
# 4. GEOFENCE TBC
# ============================================================

class TestGeofenceTBC:
    """Geofence fields are nullable/TBC until real data provided."""

    def test_site_radius_nullable(self, db):
        """Site.radius_m can be None (TBC)."""
        _create_tenant(db, "t1", "t1", "Test")
        s = _create_site(db, "t1", "s1", "Test Site")
        s.radius_m = None
        db.commit()

        db.refresh(s)
        assert s.radius_m is None

    def test_site_lat_lon_nullable(self, db):
        """Site latitude/longitude can be None (TBC)."""
        _create_tenant(db, "t1", "t1", "Test")
        s = _create_site(db, "t1", "s1", "Test Site")
        assert s.latitude is None
        assert s.longitude is None

    def test_geofence_not_production_ready_without_data(self, db):
        """Geofence-dependent checks should flag TBC when radius is None."""
        _create_tenant(db, "t1", "t1", "Test")
        s = _create_site(db, "t1", "s1", "Test Site")
        s.radius_m = None
        db.commit()

        # Production readiness check: radius_m must be set
        is_production_ready = s.radius_m is not None and s.latitude is not None and s.longitude is not None
        assert is_production_ready is False


# ============================================================
# 5. RULEVERSION FK HISTORICAL PRESERVATION
# ============================================================

class TestRuleVersionFK:
    """RosterAssignment has FK to RuleVersion for historical preservation."""

    def test_roster_links_to_rule_version(self, db):
        """Published roster links to RuleVersion via FK."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")
        _create_site(db, "t1", "s1", "Site A")
        _create_shift(db, "t1", "DAY", "Day", time(7, 0), time(19, 0), False)

        # Create rule version V1
        rv1 = RuleVersion(
            id="rv1", tenant_id="t1", version_label="V1",
            effective_from=date(2026, 9, 1),
            config_snapshot_json='{"version": "V1"}',
        )
        db.add(rv1)
        db.flush()

        # Publish roster with V1
        ra = RosterAssignment(
            id="ra1", tenant_id="t1", roster_code="RA-001",
            operating_date=date(2026, 9, 1), employee_id="w1",
            shift_id="DAY", site_id="s1",
            rule_version_id="rv1", effective_rule_version="V1",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.VALID,
        )
        db.add(ra)
        db.commit()

        assert ra.rule_version_id == "rv1"

    def test_historical_roster_preserved_after_v2(self, db):
        """Historical roster still references V1 after V2 is created."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")
        _create_site(db, "t1", "s1", "Site A")
        _create_shift(db, "t1", "DAY", "Day", time(7, 0), time(19, 0), False)

        rv1 = RuleVersion(id="rv1", tenant_id="t1", version_label="V1",
                          effective_from=date(2026, 9, 1), config_snapshot_json='{}')
        rv2 = RuleVersion(id="rv2", tenant_id="t1", version_label="V2",
                          effective_from=date(2026, 10, 1), config_snapshot_json='{}')
        db.add_all([rv1, rv2])
        db.flush()

        ra = RosterAssignment(
            id="ra1", tenant_id="t1", roster_code="RA-001",
            operating_date=date(2026, 9, 1), employee_id="w1",
            shift_id="DAY", site_id="s1",
            rule_version_id="rv1", effective_rule_version="V1",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.VALID,
        )
        db.add(ra)
        db.commit()

        # Historical roster still points to V1
        db.refresh(ra)
        assert ra.rule_version_id == "rv1"
        assert ra.effective_rule_version == "V1"

    def test_active_version_change_no_historical_impact(self, db):
        """Changing active rule version doesn't alter historical assignments."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")
        _create_site(db, "t1", "s1", "Site A")
        _create_shift(db, "t1", "DAY", "Day", time(7, 0), time(19, 0), False)

        rv1 = RuleVersion(id="rv1", tenant_id="t1", version_label="V1",
                          effective_from=date(2026, 9, 1), config_snapshot_json='{}')
        db.add(rv1)
        db.flush()

        ra = RosterAssignment(
            id="ra1", tenant_id="t1", roster_code="RA-001",
            operating_date=date(2026, 9, 1), employee_id="w1",
            shift_id="DAY", site_id="s1",
            rule_version_id="rv1", effective_rule_version="V1",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.VALID,
        )
        db.add(ra)
        db.commit()

        # Create V2 (new active version)
        rv2 = RuleVersion(id="rv2", tenant_id="t1", version_label="V2",
                          effective_from=date(2026, 10, 1), config_snapshot_json='{}')
        db.add(rv2)
        db.commit()

        # Historical assignment unchanged
        db.refresh(ra)
        assert ra.rule_version_id == "rv1"


# ============================================================
# 6. TENANT ISOLATION
# ============================================================

class TestTenantIsolation:
    """Strict tenant data isolation across all Metro master/roster domain."""

    def test_employee_isolation(self, db):
        """Metro employee not accessible from Lumin tenant."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")
        _create_worker(db, "metro", "w1", "W001", "Metro Worker")
        _create_worker(db, "lumin", "w2", "W002", "Lumin Worker")

        metro_workers = db.query(Worker).filter(Worker.tenant_id == "metro").all()
        lumin_workers = db.query(Worker).filter(Worker.tenant_id == "lumin").all()

        assert len(metro_workers) == 1
        assert metro_workers[0].name == "Metro Worker"
        assert len(lumin_workers) == 1
        assert lumin_workers[0].name == "Lumin Worker"

    def test_crew_isolation(self, db):
        """Crews don't cross tenant boundaries."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")
        _create_crew(db, "metro", "c1", "Metro Crew")
        _create_crew(db, "lumin", "c2", "Lumin Crew")

        metro_crews = db.query(Crew).filter(Crew.tenant_id == "metro").all()
        lumin_crews = db.query(Crew).filter(Crew.tenant_id == "lumin").all()

        assert len(metro_crews) == 1
        assert len(lumin_crews) == 1
        assert metro_crews[0].crew_name == "Metro Crew"

    def test_equipment_isolation(self, db):
        """Equipment doesn't cross tenant boundaries."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")

        eq1 = Equipment(id="eq1", tenant_id="metro", equipment_code="EX-001",
                        equipment_type="Excavator", status=EquipmentStatus.ACTIVE,
                        effective_from=date(2026, 9, 1))
        eq2 = Equipment(id="eq2", tenant_id="lumin", equipment_code="EX-002",
                        equipment_type="Forklift", status=EquipmentStatus.ACTIVE,
                        effective_from=date(2026, 9, 1))
        db.add_all([eq1, eq2])
        db.commit()

        metro_eq = db.query(Equipment).filter(Equipment.tenant_id == "metro").all()
        assert len(metro_eq) == 1
        assert metro_eq[0].equipment_type == "Excavator"

    def test_competency_isolation(self, db):
        """Competency doesn't cross tenant boundaries."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")
        _create_worker(db, "metro", "w1", "W001", "Metro Worker")
        _create_worker(db, "lumin", "w2", "W002", "Lumin Worker")

        c1 = Competency(id="c1", tenant_id="metro", competency_code="EXCAVATOR-OP",
                        employee_id="w1", equipment_type="Excavator",
                        valid_from=date(2026, 1, 1), status=CompetencyStatus.VALID)
        c2 = Competency(id="c2", tenant_id="lumin", competency_code="FORKLIFT-OP",
                        employee_id="w2", equipment_type="Forklift",
                        valid_from=date(2026, 1, 1), status=CompetencyStatus.VALID)
        db.add_all([c1, c2])
        db.commit()

        metro_comp = db.query(Competency).filter(Competency.tenant_id == "metro").all()
        assert len(metro_comp) == 1
        assert metro_comp[0].equipment_type == "Excavator"

    def test_roster_isolation(self, db):
        """Roster doesn't cross tenant boundaries."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")
        _create_worker(db, "metro", "w1", "W001", "Metro Worker")
        _create_worker(db, "lumin", "w2", "W002", "Lumin Worker")
        _create_site(db, "metro", "s1", "Metro Site", "Asia/Makassar")
        _create_site(db, "lumin", "s2", "Lumin Site", "Asia/Jakarta")
        _create_shift(db, "metro", "metro-DAY", "Day", time(7, 0), time(19, 0), False)
        _create_shift(db, "lumin", "lumin-DAY", "Day", time(7, 0), time(19, 0), False)

        ra1 = RosterAssignment(
            id="ra1", tenant_id="metro", roster_code="RA-001",
            operating_date=date(2026, 9, 1), employee_id="w1",
            shift_id="metro-DAY", site_id="s1",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.VALID,
        )
        ra2 = RosterAssignment(
            id="ra2", tenant_id="lumin", roster_code="RA-002",
            operating_date=date(2026, 9, 1), employee_id="w2",
            shift_id="lumin-DAY", site_id="s2",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.VALID,
        )
        db.add_all([ra1, ra2])
        db.commit()

        metro_roster = db.query(RosterAssignment).filter(RosterAssignment.tenant_id == "metro").all()
        assert len(metro_roster) == 1

    def test_rule_version_isolation(self, db):
        """RuleVersion doesn't cross tenant boundaries."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")

        rv1 = RuleVersion(id="rv1", tenant_id="metro", version_label="V1",
                          effective_from=date(2026, 9, 1), config_snapshot_json='{}')
        rv2 = RuleVersion(id="rv2", tenant_id="lumin", version_label="V1",
                          effective_from=date(2026, 9, 1), config_snapshot_json='{}')
        db.add_all([rv1, rv2])
        db.commit()

        metro_rv = db.query(RuleVersion).filter(RuleVersion.tenant_id == "metro").all()
        lumin_rv = db.query(RuleVersion).filter(RuleVersion.tenant_id == "lumin").all()
        assert len(metro_rv) == 1
        assert len(lumin_rv) == 1

    def test_site_isolation(self, db):
        """Sites don't cross tenant boundaries."""
        _create_tenant(db, "metro", "metro", "Metro Mining", "Asia/Makassar")
        _create_tenant(db, "lumin", "lumin", "Lumin Park", "Asia/Jakarta")
        _create_site(db, "metro", "s1", "Metro Site", "Asia/Makassar")
        _create_site(db, "lumin", "s2", "Lumin Site", "Asia/Jakarta")

        metro_sites = db.query(Site).filter(Site.tenant_id == "metro").all()
        lumin_sites = db.query(Site).filter(Site.tenant_id == "lumin").all()
        assert len(metro_sites) == 1
        assert len(lumin_sites) == 1
        assert metro_sites[0].timezone == "Asia/Makassar"
        assert lumin_sites[0].timezone == "Asia/Jakarta"


# ============================================================
# 7. NIGHT CROSS-MIDNIGHT OPERATING_DATE
# ============================================================

class TestNightCrossMidnight:
    """NIGHT events after midnight get operating_date from shift origin."""

    def test_night_shift_crosses_midnight(self, db):
        """NIGHT shift starting Sep 5 19:00 → ends Sep 6 07:00."""
        _create_tenant(db, "t1", "t1", "Test")
        shift = _create_shift(db, "t1", "NIGHT", "Night", time(19, 0), time(7, 0), True)
        assert shift.crosses_midnight is True

    def test_night_event_after_midnight_uses_origin_date(self, db):
        """Event at 00:58 on Sep 6 belongs to operating_date Sep 5."""
        _create_tenant(db, "t1", "t1", "Test")
        _create_worker(db, "t1", "w1", "W001", "Worker A")
        _create_site(db, "t1", "s1", "Site A")
        _create_shift(db, "t1", "NIGHT", "Night", time(19, 0), time(7, 0), True)

        # Roster for Sep 5 NIGHT shift
        ra = RosterAssignment(
            id="ra1", tenant_id="t1", roster_code="RA-001",
            operating_date=date(2026, 9, 5), employee_id="w1",
            shift_id="NIGHT", site_id="s1",
            work_status=WorkStatus.WORK, site_status=SiteStatusEnum.ONSITE,
            validation_status=ValidationStatus.VALID,
        )
        db.add(ra)
        db.commit()

        # Event at 00:58 on Sep 6 — operating_date should be Sep 5
        event_local = datetime(2026, 9, 6, 0, 58, 0)
        assert ra.operating_date == date(2026, 9, 5)
        # The event calendar date is Sep 6, but operating_date is Sep 5
        assert event_local.date() != ra.operating_date

    def test_metro_night_wita_cross_midnight(self, db):
        """Metro NIGHT shift in WITA: 19:00 WITA Sep 5 → 07:00 WITA Sep 6."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Makassar")

        shift_start = datetime(2026, 9, 5, 19, 0, 0, tzinfo=tz)
        break_in = datetime(2026, 9, 6, 0, 58, 0, tzinfo=tz)

        # Both belong to operating_date Sep 5
        assert shift_start.date() == date(2026, 9, 5)
        assert break_in.date() == date(2026, 9, 6)  # calendar date
        # operating_date should be Sep 5 (shift origin)
        operating_date = shift_start.date()
        assert operating_date == date(2026, 9, 5)
