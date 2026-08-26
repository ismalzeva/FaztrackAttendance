#!/usr/bin/env python3
"""
Standalone Lumin seed script — PROPERTY vertical (v2, corrected).

Sumber kebenaran: Google Sheet "Lumin" (Projects / Workers / Assignments /
Schedules / Supervisors / Lokasi Absen Karyawan).

Sistem kerja Lumin (dari sheet, BUKAN karangan):
  - Tidak ada shift. Jam kerja seragam Senin–Sabtu 08:00–17:00, Minggu libur.
  - Karyawan absen di lokasi tempat mereka bekerja (kantor pemasaran project)
    dengan validasi GPS geofence (radius per project).
  - Penugasan harian worker -> project (tabel Assignment per tanggal).
  - Supervisor punya scope project tertentu.

Catatan fidelitas sheet:
  - PRJ-04..07 ada kode+radiosnya tapi BELUM ada nama/koordinat -> tidak
    di-seed; worker yang di-sheet ter-mapping ke sana dibuat TANPA assignment
    harian (kondisi nyata: penugasan menunggu definisi project).
  - EMP-025..050 di sheet tanpa nama/HP -> tidak di-seed.
  - Jabatan hanya untuk 18 nama yang muncul di sheet "Lokasi Absen Karyawan";
    sisanya dibiarkan tanpa jabatan (tidak mengarang).

Usage:
  cd /home/ubuntu/FaztrackAttendance/backend
  .venv/bin/python scripts/seed_lumin_standalone.py

Credentials read from .env.lumin (FAZTRACK_DATABASE_URL /
FAZTRACK_DEMO_SEED_PASSWORD) so no secret lives in this file.
Idempotent — safe to re-run.
"""

import os
import sys
from datetime import date, datetime, time, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _load_env_file(path):
    """Minimal .env loader (KEY=VALUE lines) without overriding existing env."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out

_env = _load_env_file(os.path.join(BACKEND_DIR, ".env.lumin"))

DB_URL = os.environ.get(
    "FAZTRACK_DATABASE_URL",
    _env.get(
        "FAZTRACK_DATABASE_URL",
        "postgresql+psycopg://faztrack_lumin@localhost:5437/faztrack_attendance_lumin",
    ),
)
PASSWORD = os.environ.get(
    "FAZTRACK_DEMO_SEED_PASSWORD", _env.get("FAZTRACK_DEMO_SEED_PASSWORD", "LuminDemo2026!")
)

engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# App imports (after sys.path fix)
from app import models  # noqa: E402,F401
from app.models import Base  # noqa: E402
from app.models import (  # noqa: E402
    Tenant, TenantStatus,
    User, Membership, RoleCode, MembershipStatus,
    Worker,
    Project, Assignment, WorkSchedule, SupervisorProject,
    Role, EmployeeMeta,
    RuleVersion,
    RosterAssignment, WorkStatus,
    CanonicalAttendanceEvent, CanonicalEventType, CanonicalProcessingStatus,
    RuleEvaluation, RuleEvaluationStatus, RuleSeverity,
    ExceptionCase, ExceptionSeverity, ExceptionStatus,
)
from app.security import hash_password  # noqa: E402
from app.rule_versioning import snapshot_rules  # noqa: E402


def bootstrap_schema():
    """Replicate metro bootstrap path: Base.metadata.create_all + enum sync."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TYPE sitetype ADD VALUE IF NOT EXISTS 'ATTRACTION_SITE'"
        ))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANT_ID = "lumin-park-001"

# Minggu demo: Senin 31 Agu – Sabtu 5 Sep 2026 (Minggu 6 Sep libur).
WEEK_MONDAY = date(2026, 8, 31)
DEMO_DATE = date(2026, 9, 1)          # Selasa — hari operasional demo
WEEK_SUNDAY = WEEK_MONDAY + timedelta(days=6)

WORK_START = time(8, 0)
WORK_END = time(17, 0)

# WIB = UTC+7
WIB_OFFSET = timedelta(hours=7)


def wib_to_utc(dt_local: datetime) -> datetime:
    return dt_local.replace(tzinfo=timezone(WIB_OFFSET)).astimezone(timezone.utc)


def aware_utc(dt_local: datetime) -> datetime:
    return wib_to_utc(dt_local)


def get_or_none(db: Session, model, **kwargs):
    return db.execute(select(model).filter_by(**kwargs)).scalar_one_or_none()


def ensure_tenant(db: Session) -> Tenant:
    t = get_or_none(db, Tenant, id=TENANT_ID)
    if t:
        return t
    t = Tenant(id=TENANT_ID, code="lumin-park", name="Lumin",
               timezone="Asia/Jakarta", status=TenantStatus.ACTIVE)
    db.add(t); db.flush()
    return t


def ensure_user(db: Session, user_id: str, login_id: str, display_name: str) -> User:
    u = get_or_none(db, User, id=user_id)
    if u:
        return u
    u = User(id=user_id, login_id=login_id, display_name=display_name,
             password_hash=hash_password(PASSWORD))
    db.add(u); db.flush()
    return u


def ensure_membership(db: Session, user_id: str, role: RoleCode) -> Membership:
    m = get_or_none(db, Membership, tenant_id=TENANT_ID, user_id=user_id, role=role)
    if m:
        return m
    m = Membership(id=f"mem-{user_id}", tenant_id=TENANT_ID, user_id=user_id,
                   role=role, status=MembershipStatus.ACTIVE)
    db.add(m); db.flush()
    return m


def ensure_project(db: Session, pid: str, code: str, name: str,
                   lat: float, lng: float, radius_m: int) -> Project:
    p = get_or_none(db, Project, id=pid)
    if p:
        return p
    p = Project(id=pid, tenant_id=TENANT_ID, code=code, name=name,
                latitude=lat, longitude=lng, geofence_radius_m=radius_m,
                work_start=WORK_START, work_end=WORK_END,
                is_active=True)
    db.add(p); db.flush()
    return p


def norm_phone(raw) -> str | None:
    """Sheet menyimpan HP sebagai float (85263340800.0) -> '085263340800'."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s or s.lower() == "none":
        return None
    return s if s.startswith("0") else "0" + s


def ensure_worker(db: Session, wid: str, code: str, name: str, phone=None) -> Worker:
    w = get_or_none(db, Worker, id=wid)
    if w:
        return w
    w = Worker(id=wid, tenant_id=TENANT_ID, code=code, name=name,
               phone=norm_phone(phone), is_active=True)
    db.add(w); db.flush()
    return w


def ensure_role(db: Session, rid: str, code: str, name: str) -> Role:
    r = get_or_none(db, Role, id=rid)
    if r:
        return r
    r = Role(id=rid, tenant_id=TENANT_ID, role_code=code, role_name=name,
             status="ACTIVE")
    db.add(r); db.flush()
    return r


def ensure_assignment(db: Session, aid: str, worker_id: str, project_id: str,
                      work_date: date) -> Assignment:
    a = get_or_none(db, Assignment, id=aid)
    if a:
        return a
    a = Assignment(id=aid, tenant_id=TENANT_ID, worker_id=worker_id,
                   project_id=project_id, work_date=work_date)
    db.add(a); db.flush()
    return a


def ensure_schedule(db: Session, sid: str, worker_id: str, work_date: date,
                    start: time, end: time, working: bool) -> WorkSchedule:
    s = get_or_none(db, WorkSchedule, id=sid)
    if s:
        return s
    s = WorkSchedule(id=sid, tenant_id=TENANT_ID, worker_id=worker_id,
                     work_date=work_date, start_time=start, end_time=end,
                     is_working_day=working)
    db.add(s); db.flush()
    return s


def ensure_supervisor_scope(db: Session, mid: str, sid: str, project_id: str) -> None:
    sp = get_or_none(db, SupervisorProject, id=sid)
    if sp:
        return
    db.add(SupervisorProject(id=sid, tenant_id=TENANT_ID, membership_id=mid,
                             project_id=project_id)); db.flush()


def ensure_employee_meta(db: Session, em_id: str, worker_id: str,
                         role_id: str | None) -> None:
    em = get_or_none(db, EmployeeMeta, id=em_id)
    if em:
        return
    em = EmployeeMeta(id=em_id, tenant_id=TENANT_ID, worker_id=worker_id,
                      role_id=role_id, crew_id=None,
                      effective_from=WEEK_MONDAY)
    db.add(em); db.flush()


def ensure_roster_assignment(db: Session, ra_id: str, op_date: date,
                             employee_id: str, rule_ver) -> None:
    ra = get_or_none(db, RosterAssignment, id=ra_id)
    if ra:
        return
    db.add(RosterAssignment(
        id=ra_id, tenant_id=TENANT_ID,
        roster_code=f"L-{employee_id[:12]}-{op_date.strftime('%Y%m%d')}",
        operating_date=op_date, employee_id=employee_id,
        crew_id=None, work_status=WorkStatus.WORK,
        shift_id=None, site_id=None, planned_equipment_id=None,
        rule_version_id=None, effective_rule_version=rule_ver,
    )); db.flush()


def ensure_canonical_event(db: Session, ce_id: str, emp_id: str,
                           ev_type: CanonicalEventType,
                           local_ts: datetime, utc_ts: datetime,
                           op_date: date, source_ev_id: str) -> None:
    ce = get_or_none(db, CanonicalAttendanceEvent, id=ce_id)
    if ce:
        return
    db.add(CanonicalAttendanceEvent(
        id=ce_id, tenant_id=TENANT_ID, employee_id=emp_id,
        event_type=ev_type,
        local_timestamp=local_ts.replace(tzinfo=None),
        utc_timestamp=utc_ts,
        timezone="Asia/Jakarta", operating_date=op_date,
        shift_id=None, site_id=None, source="SEED",
        source_event_id=source_ev_id,
        processing_status=CanonicalProcessingStatus.VALID,
    )); db.flush()


def ensure_rule_evaluation(db: Session, re_id: str, emp_id: str, op_date: date,
                           rule_code: str, rule_ver,
                           status: RuleEvaluationStatus, severity: RuleSeverity,
                           measured: str, expected: str, evidence_key: str,
                           reason: str) -> RuleEvaluation:
    re_ = get_or_none(db, RuleEvaluation, id=re_id)
    if re_:
        return re_
    re_ = RuleEvaluation(
        id=re_id, tenant_id=TENANT_ID, employee_id=emp_id,
        operating_date=op_date, shift_id=None, rule_code=rule_code,
        rule_version_id=rule_ver.id if rule_ver else None,
        status=status, severity=severity,
        actual_value=measured, expected_value=expected,
        evidence_key=evidence_key, reason=reason,
    )
    db.add(re_); db.flush()
    return re_


def ensure_exception_case(db: Session, ec_id: str, emp_id: str, op_date: date,
                          source_type: str, source_id: str, exc_type: str,
                          severity: ExceptionSeverity, rule_ver,
                          detected_at: datetime, owner_id: str | None) -> None:
    ec = get_or_none(db, ExceptionCase, id=ec_id)
    if ec:
        return
    db.add(ExceptionCase(
        id=ec_id, tenant_id=TENANT_ID, employee_id=emp_id,
        operating_date=op_date, shift_id=None,
        exception_type=exc_type, severity=severity,
        status=ExceptionStatus.OPEN,
        source_type=source_type, source_id=source_id,
        rule_version_id=rule_ver.id if rule_ver else None,
        detected_at=detected_at, opened_at=detected_at,
        current_owner_id=owner_id,
    )); db.flush()


# ---------------------------------------------------------------------------
# Master data — persis dari Google Sheet
# ---------------------------------------------------------------------------
# Projects (hanya yang lengkap nama+koordinat; PRJ-04..07 belum didefinisikan)
PROJECTS_DEF = [
    # pid, code, name, lat, lng, radius_m
    ("proj-prj01", "PRJ-01", "Kantor Pemasaran Lumin",
     -0.842277601826103, 100.37235855582, 150),
    ("proj-prj02", "PRJ-02", "Kantor Pemasaran Laresta Cluster",
     -0.847085850900649, 100.38819505397, 150),
    ("proj-prj03", "PRJ-03", "Kantor Pemasaran Lubeg",
     -0.965583526878328, 100.408184013492, 175),
]

# Workers: (emp_code, nama, hp, project_tujuan) — 24 nama terisi di sheet.
# project None = di-sheet mapping ke PRJ-04..07 yang belum didefinisikan.
WORKERS_DEF = [
    ("EMP-001", "Alfan Suri",           "85263340800",   "PRJ-01"),
    ("EMP-002", "Aneri Chersianova",    "81220497162",   "PRJ-02"),
    ("EMP-003", "Armida",               "85271367816",   "PRJ-03"),
    ("EMP-004", "Azizul Ardhi",         "85375517156",   None),  # PRJ-04
    ("EMP-005", "Defvika Putra",        "81270167344",   None),  # PRJ-05
    ("EMP-006", "Dian Hidayat",         "811663838",     None),  # PRJ-06
    ("EMP-007", "Haniey Fauziah",       "82383001817",   None),  # PRJ-07
    ("EMP-008", "Ihsan Kurnia",         "811616165",     "PRJ-01"),
    ("EMP-009", "Muthiara Firdaus",     "82288523285",   "PRJ-02"),
    ("EMP-010", "Rahayu Fadilah",       "83838069098",   "PRJ-03"),
    ("EMP-011", "Rahmad Afdal",         "85218149978",   None),  # PRJ-04
    ("EMP-012", "Rahmat Ilham",         "83182266592",   None),  # PRJ-05
    ("EMP-013", "Tesia Ramadhani Putri","82123444215",   None),  # PRJ-06
    ("EMP-014", "Tomy Zafera",          "895405415714",  None),  # PRJ-07
    ("EMP-015", "Ummi Arifah",          "81267892749",   "PRJ-01"),
    ("EMP-016", "Vanny Amelia",         "81266450291",   "PRJ-02"),
    ("EMP-017", "Wike Januriati",       "81266343014",   "PRJ-03"),
    ("EMP-018", "Yusuf Maulana",        "82363261951",   None),  # PRJ-04
    ("EMP-019", "Zilham Zikri",         "82173884661",   None),  # PRJ-05
    ("EMP-020", "Apes",                 "83186049749",   None),  # PRJ-06
    ("EMP-021", "Ali",                  "82285088368",   None),  # PRJ-07
    ("EMP-022", "Yus",                  "83182310429",   "PRJ-01"),
    ("EMP-023", "Ferdi",                "85185783200",   "PRJ-02"),
    ("EMP-024", "Rapi",                 "83844174344",   "PRJ-03"),
]

# Jabatan dari sheet "Lokasi Absen Karyawan" (hanya 18 nama yang tercantum)
POSITIONS = {
    "EMP-002": ("Sales Officer", None),
    "EMP-003": ("Finance, Accounting & Tax Manager", None),
    "EMP-004": ("Social Media Specialist", None),
    "EMP-005": ("Project Control Officer", None),
    "EMP-006": ("Project Manager Construction", None),
    "EMP-007": ("Project Control Manager", None),
    "EMP-008": ("Legal Staff", None),
    "EMP-009": ("Social Media Specialist", None),
    "EMP-010": ("Sales Officer", None),
    "EMP-011": ("Pengawas Proyek", None),
    "EMP-012": ("Pengawas Proyek", None),
    "EMP-013": ("Admin Sales", None),
    "EMP-014": ("Pengawas Proyek", None),
    "EMP-015": ("Human Capital Manager", None),
    "EMP-016": ("Sales Officer", None),
    "EMP-017": ("Finance & Accounting Staff", None),
    "EMP-018": ("Logistic Staff", None),
    "EMP-019": ("Architect Engineer", None),
}

RULE_VERSION = "LUMIN-RULE-v0.2"


# ---------------------------------------------------------------------------
# Demo-state reset (--reset): hapus state exception yang bisa dimutasi saat
# rehearsal/demo, supaya reseed menghasilkan kasus fresh status OPEN.
# Idempoten tanpa flag TETAP terjaga (ensure_* return-early).
# ---------------------------------------------------------------------------
RESET_TABLES = [
    "exception_actions",
    "exception_evidence",
    "exception_decisions",
    "exception_cases",
]


def reset_demo_state() -> None:
    with engine.begin() as conn:
        for tbl in RESET_TABLES:
            res = conn.execute(text(f'DELETE FROM {tbl}'))
            print(f"  reset: {tbl}: {res.rowcount} row dihapus")


def seed():
    bootstrap_schema()
    if "--reset" in sys.argv:
        print("== RESET demo state ==")
        reset_demo_state()
    counts = {}
    notes = []
    db = SessionLocal()
    try:
        # ── Tenant ──────────────────────────────────────────────────────
        tenant = ensure_tenant(db)
        counts["tenant"] = 1

        # ── Users & Memberships ─────────────────────────────────────────
        # Login ID mengikuti tombol quick-fill di FE Lumin (@luminpark.id).
        admin = ensure_user(db, "lumin-admin-001", "admin@luminpark.id",
                            "Admin Lumin")
        sup = ensure_user(db, "lumin-sup-001", "supervisor@luminpark.id",
                          "Supervisor Lumin")
        admin_mem = ensure_membership(db, admin.id, RoleCode.OWNER)
        sup_mem = ensure_membership(db, sup.id, RoleCode.SUPERVISOR)
        counts["users"] = 2

        # ── Projects (kantor pemasaran + geofence) ──────────────────────
        proj_by_code = {}
        for pid, code, name, lat, lng, rad in PROJECTS_DEF:
            proj_by_code[code] = ensure_project(db, pid, code, name, lat, lng, rad)
        counts["projects"] = len(proj_by_code)
        notes.append("PRJ-04..PRJ-07 dilewati: sheet belum memuat nama/koordinat")

        # ── Workers ─────────────────────────────────────────────────────
        workers = {}
        for code, name, phone, _pc in WORKERS_DEF:
            workers[code] = ensure_worker(db, f"w-{code.lower()}", code, name, phone)
        counts["workers"] = len(workers)
        notes.append("EMP-025..050 dilewati: sheet belum memuat nama/HP")

        # ── Jabatan (Role) + EmployeeMeta ───────────────────────────────
        role_cache = {}
        meta_count = 0
        for code, (pos_name, _x) in POSITIONS.items():
            slug = pos_name.lower()
            slug = "".join(ch if ch.isalnum() else "_" for ch in slug)
            rid = f"role-{slug[:31]}"  # PK roles varchar(36)
            if rid not in role_cache:
                role_cache[rid] = ensure_role(db, rid, slug, pos_name)
            ensure_employee_meta(db, f"em-{code.lower()}",
                                 workers[code].id, role_cache[rid].id)
            meta_count += 1
        counts["roles"] = len(role_cache)
        counts["employee_meta"] = meta_count

        # ── Supervisor scope: semua project terdefinisi ─────────────────
        seq = 0
        for code, proj in proj_by_code.items():
            seq += 1
            ensure_supervisor_scope(db, sup_mem.id, f"sp-sup-{seq}", proj.id)
        counts["supervisor_scopes"] = seq

        # ── Jadwal & Penugasan harian (Sen–Sab 08:00–17:00, Min libur) ──
        asg_count = 0
        sch_count = 0
        ra_count = 0
        d = WEEK_MONDAY
        while d <= WEEK_SUNDAY:
            is_sunday = (d == WEEK_SUNDAY)
            ds = d.strftime("%Y%m%d")
            for code, _name, _phone, pc in WORKERS_DEF:
                wid = workers[code].id
                sch_count += 1 if ensure_schedule(
                    db, f"sch-{code.lower()}-{ds}", wid, d,
                    WORK_START, WORK_END, not is_sunday,
                ) is not None else 1
                if is_sunday or pc not in proj_by_code:
                    continue
                ensure_assignment(db, f"asg-{code.lower()}-{ds}",
                                  wid, proj_by_code[pc].id, d)
                asg_count += 1
                if d <= DEMO_DATE:  # roster layer utk dashboard s.d. demo date
                    ensure_roster_assignment(db, f"ra-{code.lower()}-{ds}",
                                             d, wid, RULE_VERSION)
                    ra_count += 1
            d += timedelta(days=1)
        counts["assignments"] = asg_count
        counts["work_schedules"] = sch_count
        counts["roster_assignments"] = ra_count

        # ── Rule Version ────────────────────────────────────────────────
        rule_ver_row = snapshot_rules(db, TENANT_ID, RULE_VERSION, WEEK_MONDAY)
        counts["rule_versions"] = 1

        # ── Data operasional demo (2026-09-01, WIB) ─────────────────────
        # Kasus 1 (WARNING): EMP-002 Aneri (PRJ-02) check-in telat 08:14.
        aneri_in = aware_utc(datetime(2026, 9, 1, 8, 14))
        ensure_canonical_event(db, "ce-emp002-in", workers["EMP-002"].id,
                               CanonicalEventType.CHECK_IN,
                               datetime(2026, 9, 1, 8, 14), aneri_in,
                               DEMO_DATE, "seed-emp002-checkin")
        aneri_out = aware_utc(datetime(2026, 9, 1, 17, 5))
        ensure_canonical_event(db, "ce-emp002-out", workers["EMP-002"].id,
                               CanonicalEventType.CHECK_OUT,
                               datetime(2026, 9, 1, 17, 5), aneri_out,
                               DEMO_DATE, "seed-emp002-checkout")
        re_late = ensure_rule_evaluation(
            db, f"re-emp002-late-{DEMO_DATE.strftime('%Y%m%d')}",
            workers["EMP-002"].id, DEMO_DATE, "LATE_CHECK_IN", rule_ver_row,
            RuleEvaluationStatus.FAIL, RuleSeverity.WARNING,
            "08:14", "08:00", f"seed-emp002-checkin",
            "Check-in 08:14 WIB melewati jam mulai kerja 08:00 WIB "
            "(Kantor Pemasaran Laresta Cluster)",
        )
        ensure_exception_case(
            db, "exc-emp002-late-0901", workers["EMP-002"].id, DEMO_DATE,
            "RULE_EVALUATION", re_late.id, "LATE_CHECK_IN",
            ExceptionSeverity.WARNING, rule_ver_row,
            aware_utc(datetime(2026, 9, 1, 8, 20)), sup.id,
        )

        # Kasus 2 (CRITICAL): EMP-008 Ihsan (PRJ-01) check-in 08:03,
        # tidak pernah check-out sampai lewat jam selesai kerja.
        ihsan_in = aware_utc(datetime(2026, 9, 1, 8, 3))
        ensure_canonical_event(db, "ce-emp008-in", workers["EMP-008"].id,
                               CanonicalEventType.CHECK_IN,
                               datetime(2026, 9, 1, 8, 3), ihsan_in,
                               DEMO_DATE, "seed-emp008-checkin")
        ensure_exception_case(
            db, "exc-emp008-missing-checkout-0901", workers["EMP-008"].id,
            DEMO_DATE, "CHECKPOINT_VALIDATION", "ce-emp008-in", "MISSING_CHECKOUT",
            ExceptionSeverity.CRITICAL, rule_ver_row,
            aware_utc(datetime(2026, 9, 1, 17, 35)), sup.id,
        )
        counts["canonical_events"] = 3
        counts["rule_evaluations"] = 1
        counts["exception_cases"] = 2

        # ── Commit ──────────────────────────────────────────────────────
        db.commit()

        # ── Summary ─────────────────────────────────────────────────────
        print("=" * 62)
        print("  Lumin Seed v2 (PROPERTY vertical) — COMPLETE")
        print("=" * 62)
        print(f"  Tenant:              {tenant.code} ({tenant.id})")
        db_host = DB_URL.split('@')[-1] if '@' in DB_URL else DB_URL
        print(f"  Database:            {db_host}")
        print()
        for key, val in counts.items():
            print(f"  {key:25s}: {val}")
        print()
        print(f"  Sistem kerja:        Tanpa shift — Sen–Sab 08:00–17:00 WIB")
        print(f"  Absensi:             Per lokasi project + geofence GPS")
        print(f"  Minggu demo:         {WEEK_MONDAY} → {WEEK_SUNDAY} (Min libur)")
        print(f"  Hari operasional:    {DEMO_DATE}")
        print(f"  Rule version:        {RULE_VERSION}")
        print(f"  Password:            {'*' * len(PASSWORD)}")
        if notes:
            print()
            print("  Catatan fidelitas sheet:")
            for n in notes:
                print(f"   - {n}")
        print("=" * 62)

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
