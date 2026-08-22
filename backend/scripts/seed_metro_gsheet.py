#!/usr/bin/env python3
"""
Metro Mining Seed Script — Google Sheets Version
Reads master data from a public Google Sheet and seeds the database.

Usage:
    1. Upload METRO_MASTER_DATA.xlsx to Google Sheets
    2. Make it public (Anyone with link can view)
    3. Copy the Sheet ID from URL
    4. Run: python scripts/seed_metro_gsheet.py --sheet-id YOUR_SHEET_ID
    
Or set environment variable:
    export METRO_GSHEET_ID=YOUR_SHEET_ID
    python scripts/seed_metro_gsheet.py
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, date, time, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import (
    Base, Tenant, User, Membership, RoleCode,
    Site, SiteType, SiteStatus,
    ShiftTemplate, Role, Crew,
    Worker, Equipment, EmployeeMeta,
    Competency, CompetencyStatus,
    RosterPolicy, CheckpointPolicy, RuleVersion,
    RosterAssignment, WorkStatus, SiteStatusEnum, ValidationStatus,
    CanonicalAttendanceEvent, CanonicalEventType,
    EquipmentAssignmentActual, ActualAssignmentStatus,
    ExceptionCase, ExceptionStatus, ExceptionSeverity, ExceptionSourceType,
    uid, now,
)
from app.security import hash_password


# Google Sheets CSV export URL pattern
GSHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={sheet_name}"


def fetch_sheet(sheet_id: str, sheet_name: str) -> pd.DataFrame:
    """Fetch a single sheet as DataFrame."""
    url = GSHEET_CSV_URL.format(sheet_id=sheet_id, sheet_name=sheet_name)
    print(f"  Fetching: {sheet_name}...", end=" ")
    try:
        df = pd.read_csv(url)
        print(f"OK ({len(df)} rows)")
        return df
    except Exception as e:
        print(f"FAILED: {e}")
        return pd.DataFrame()


def load_all_sheets(sheet_id: str) -> dict:
    """Load all sheets from Google Sheets."""
    sheets = {
        "Tenant": "Tenant",
        "Users": "Users",
        "Sites": "Sites",
        "Shifts": "Shifts",
        "Roles": "Roles",
        "Crews": "Crews",
        "Workers": "Workers",
        "Equipment": "Equipment",
        "Competencies": "Competencies",
        "Roster Policies": "Roster Policies",
        "Checkpoint Policies": "Checkpoint Policies",
    }
    
    data = {}
    for key, sheet_name in sheets.items():
        df = fetch_sheet(sheet_id, sheet_name)
        if not df.empty:
            data[key] = df
        else:
            print(f"  WARNING: Sheet '{sheet_name}' is empty or not found")
    
    return data


def seed_from_gsheet(sheet_id: str, database_url: str = None):
    """Seed database from Google Sheets data."""
    
    if not database_url:
        database_url = os.getenv(
            "FAZTRACK_DATABASE_URL",
            "postgresql+psycopg://faztrack_metro:MetroPilot2026Secure@localhost:5436/faztrack_attendance_metro"
        )
    
    print(f"\n{'='*60}")
    print(f"METRO MINING SEED — Google Sheets Mode")
    print(f"{'='*60}")
    print(f"Sheet ID: {sheet_id}")
    print(f"Database: {database_url.split('@')[-1] if '@' in database_url else database_url}")
    print(f"{'='*60}\n")
    
    # Fetch all sheets
    print("[1/4] Fetching Google Sheets data...")
    data = load_all_sheets(sheet_id)
    
    if not data:
        print("ERROR: No data fetched from Google Sheets")
        return False
    
    print(f"\n[2/4] Connecting to database...")
    engine = create_engine(database_url)
    
    print(f"\n[3/4] Seeding data...")
    
    with Session(engine) as session:
        # Clear existing data (order matters for FK constraints)
        print("  Clearing existing data...")
        for table in [
            "equipment_comparison_results", "equipment_discrepancies",
            "canonical_attendance_events", "equipment_assignments_actual",
            "exception_cases", "rule_evaluations",
            "roster_assignments", "checkpoint_policies", "roster_policies",
            "competency_statuses", "employee_meta",
            "equipment", "workers", "crews", "roles",
            "shift_templates", "sites",
            "memberships", "users", "tenants",
        ]:
            try:
                session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass
        session.commit()
        
        tenant_id = data["Tenant"].iloc[0]["tenant_id"]
        
        # --- TENANT ---
        if "Tenant" in data:
            print("  Seeding tenant...")
            for _, row in data["Tenant"].iterrows():
                tenant = Tenant(
                    id=row["tenant_id"],
                    tenant_name=row["tenant_name"],
                    slug=row["slug"],
                    timezone=row["timezone"],
                    status=row["status"],
                )
                session.add(tenant)
            session.commit()
        
        # --- USERS ---
        if "Users" in data:
            print("  Seeding users...")
            for _, row in data["Users"].iterrows():
                user = User(
                    id=uid(),
                    email=row["email"],
                    password_hash=hash_password(row["password"]),
                    full_name=row["full_name"],
                    phone=row.get("phone", ""),
                )
                session.add(user)
                session.flush()
                
                membership = Membership(
                    id=uid(),
                    user_id=user.id,
                    tenant_id=tenant_id,
                    role_code=RoleCode(row["role_code"]),
                )
                session.add(membership)
            session.commit()
        
        # --- SITES ---
        site_map = {}
        if "Sites" in data:
            print("  Seeding sites...")
            for _, row in data["Sites"].iterrows():
                site = Site(
                    id=uid(),
                    tenant_id=tenant_id,
                    site_code=row["site_code"],
                    site_name=row["site_name"],
                    site_type=SiteType(row["site_type"]),
                    status=SiteStatus(row["status"]),
                    latitude=float(row["latitude"]) if pd.notna(row.get("latitude")) else None,
                    longitude=float(row["longitude"]) if pd.notna(row.get("longitude")) else None,
                    geofence_radius_m=float(row["geofence_radius_m"]) if pd.notna(row.get("geofence_radius_m")) else None,
                    timezone=row.get("timezone", "Asia/Makassar"),
                )
                session.add(site)
                site_map[row["site_code"]] = site.id
            session.commit()
        
        # --- SHIFTS ---
        shift_map = {}
        if "Shifts" in data:
            print("  Seeding shifts...")
            for _, row in data["Shifts"].iterrows():
                shift = ShiftTemplate(
                    id=uid(),
                    tenant_id=tenant_id,
                    shift_name=row["shift_name"],
                    shift_code=row["shift_code"],
                    start_time=datetime.strptime(row["start_time"], "%H:%M").time(),
                    end_time=datetime.strptime(row["end_time"], "%H:%M").time(),
                    break_start=datetime.strptime(row["break_start"], "%H:%M").time(),
                    break_end=datetime.strptime(row["break_end"], "%H:%M").time(),
                    handover_start=datetime.strptime(row["handover_start"], "%H:%M").time(),
                    handover_end=datetime.strptime(row["handover_end"], "%H:%M").time(),
                    is_night_shift=row["is_night_shift"].upper() == "TRUE",
                )
                session.add(shift)
                shift_map[row["shift_name"]] = shift.id
            session.commit()
        
        # --- ROLES ---
        role_map = {}
        if "Roles" in data:
            print("  Seeding roles...")
            for _, row in data["Roles"].iterrows():
                role = Role(
                    id=uid(),
                    tenant_id=tenant_id,
                    role_name=row["role_name"],
                    description=row.get("description", ""),
                    is_active=row["is_active"].upper() == "TRUE",
                )
                session.add(role)
                role_map[row["role_name"]] = role.id
            session.commit()
        
        # --- CREWS ---
        crew_map = {}
        if "Crews" in data:
            print("  Seeding crews...")
            for _, row in data["Crews"].iterrows():
                crew = Crew(
                    id=uid(),
                    tenant_id=tenant_id,
                    crew_name=row["crew_name"],
                    description=row.get("description", ""),
                    is_active=row["is_active"].upper() == "TRUE",
                )
                session.add(crew)
                crew_map[row["crew_name"]] = crew.id
            session.commit()
        
        # --- WORKERS ---
        worker_map = {}
        if "Workers" in data:
            print("  Seeding workers...")
            for _, row in data["Workers"].iterrows():
                worker = Worker(
                    id=uid(),
                    tenant_id=tenant_id,
                    worker_code=row["worker_code"],
                    full_name=row["full_name"],
                    role_id=role_map.get(row["role_name"]),
                    crew_id=crew_map.get(row["crew_name"]),
                    phone=row.get("phone", ""),
                    is_active=row["is_active"].upper() == "TRUE",
                )
                session.add(worker)
                worker_map[row["worker_code"]] = worker.id
            session.commit()
        
        # --- EQUIPMENT ---
        equip_map = {}
        if "Equipment" in data:
            print("  Seeding equipment...")
            for _, row in data["Equipment"].iterrows():
                equip = Equipment(
                    id=uid(),
                    tenant_id=tenant_id,
                    equipment_code=row["equipment_code"],
                    equipment_type=row["equipment_type"],
                    description=row.get("description", ""),
                    is_active=row["is_active"].upper() == "TRUE",
                )
                session.add(equip)
                equip_map[row["equipment_code"]] = equip.id
            session.commit()
        
        # --- EMPLOYEE META ---
        if "Workers" in data:
            print("  Seeding employee meta...")
            for _, row in data["Workers"].iterrows():
                worker_id = worker_map.get(row["worker_code"])
                if worker_id:
                    meta = EmployeeMeta(
                        id=uid(),
                        tenant_id=tenant_id,
                        worker_id=worker_id,
                        hire_date=date(2024, 1, 1),
                        employment_status="ACTIVE",
                    )
                    session.add(meta)
            session.commit()
        
        # --- COMPETENCIES ---
        if "Competencies" in data:
            print("  Seeding competencies...")
            for _, row in data["Competencies"].iterrows():
                worker_id = worker_map.get(row["worker_code"])
                if worker_id:
                    comp = CompetencyStatus(
                        id=uid(),
                        tenant_id=tenant_id,
                        worker_id=worker_id,
                        competency_type=row["competency_type"],
                        certification_number=row["certification_number"],
                        issued_date=datetime.strptime(row["issued_date"], "%Y-%m-%d").date(),
                        expiry_date=datetime.strptime(row["expiry_date"], "%Y-%m-%d").date(),
                        status=CompetencyStatus.Status(row["status"]) if hasattr(CompetencyStatus, 'Status') else row["status"],
                    )
                    session.add(comp)
            session.commit()
        
        # --- ROSTER POLICIES ---
        if "Roster Policies" in data:
            print("  Seeding roster policies...")
            for _, row in data["Roster Policies"].iterrows():
                policy = RosterPolicy(
                    id=uid(),
                    tenant_id=tenant_id,
                    policy_name=row["policy_name"],
                    policy_type=row["policy_type"],
                    value=str(row["value"]),
                    status=row["status"],
                    notes=row.get("notes", ""),
                )
                session.add(policy)
            session.commit()
        
        # --- CHECKPOINT POLICIES ---
        if "Checkpoint Policies" in data:
            print("  Seeding checkpoint policies...")
            for _, row in data["Checkpoint Policies"].iterrows():
                shift_id = shift_map.get(row["shift_name"])
                if shift_id:
                    cp = CheckpointPolicy(
                        id=uid(),
                        tenant_id=tenant_id,
                        checkpoint_type=row["checkpoint_type"],
                        shift_id=shift_id,
                        time_window_start=datetime.strptime(row["time_window_start"], "%H:%M").time(),
                        time_window_end=datetime.strptime(row["time_window_end"], "%H:%M").time(),
                        tolerance_minutes=int(row["tolerance_minutes"]),
                        is_mandatory=row["is_mandatory"].upper() == "TRUE",
                    )
                    session.add(cp)
            session.commit()
        
        # --- RULE VERSION ---
        print("  Seeding rule version...")
        rule = RuleVersion(
            id=uid(),
            tenant_id=tenant_id,
            version_label="METRO-RULE-v0.1",
            description="Initial Metro Mining rule set",
            is_active=True,
        )
        session.add(rule)
        session.commit()
        
        # --- ROSTER ASSIGNMENTS (demo: 7 days) ---
        print("  Seeding roster assignments...")
        start_date = date(2026, 9, 1)
        site_id = list(site_map.values())[0]
        
        for day_offset in range(7):
            op_date = start_date + timedelta(days=day_offset)
            for worker_code, worker_id in worker_map.items():
                # Determine shift based on crew
                worker_row = data["Workers"][data["Workers"]["worker_code"] == worker_code].iloc[0]
                crew_name = worker_row["crew_name"]
                shift_name = "Day Shift" if "Alpha" in crew_name else "Night Shift"
                shift_id = shift_map.get(shift_name)
                crew_id = crew_map.get(crew_name)
                
                roster = RosterAssignment(
                    id=uid(),
                    tenant_id=tenant_id,
                    roster_code=f"ROSTER-{op_date.strftime('%Y%m%d')}",
                    operating_date=op_date,
                    employee_id=worker_id,
                    crew_id=crew_id,
                    shift_id=shift_id,
                    site_id=site_id,
                    site_cycle_day=0,
                    site_status=SiteStatusEnum.ONSITE,
                    work_status=WorkStatus.WORK,
                    validation_status=ValidationStatus.DRAFT,
                    rule_version_id=rule.id,
                    effective_rule_version=rule.version_label,
                )
                session.add(roster)
        session.commit()
        
        # --- DEMO OPERATIONAL DATA ---
        print("  Seeding demo operational data...")
        demo_date = date(2026, 9, 1)
        
        # Canonical events for W001
        w001_id = worker_map.get("W001")
        if w001_id:
            events = [
                (CanonicalEventType.CHECK_IN, time(6, 55), "START_OF_SHIFT"),
                (CanonicalEventType.CHECK_OUT, time(19, 5), "END_OF_SHIFT"),
            ]
            for event_type, event_time, checkpoint in events:
                event = CanonicalAttendanceEvent(
                    id=uid(),
                    tenant_id=tenant_id,
                    worker_id=w001_id,
                    event_type=event_type,
                    event_date=demo_date,
                    event_time=event_time,
                    checkpoint_type=checkpoint,
                    site_id=site_id,
                    source_type="MANUAL",
                    validation_status=ValidationStatus.VALID,
                )
                session.add(event)
        
        # Equipment assignment
        ex025_id = equip_map.get("EX-025")
        day_shift_id = shift_map.get("Day Shift")
        if w001_id and ex025_id:
            assignment = EquipmentAssignmentActual(
                id=uid(),
                tenant_id=tenant_id,
                worker_id=w001_id,
                equipment_id=ex025_id,
                assignment_date=demo_date,
                shift_id=day_shift_id,
                status=ActualAssignmentStatus.ACTIVE,
            )
            session.add(assignment)
        
        # Exception case
        exception = ExceptionCase(
            id=uid(),
            tenant_id=tenant_id,
            exception_type="EQUIPMENT_MISMATCH",
            severity=ExceptionSeverity.WARNING,
            status=ExceptionStatus.OPEN,
            source_type=ExceptionSourceType.EQUIPMENT_DISCREPANCY,
            source_id=ex025_id or "demo",
            employee_id=w001_id,
            operating_date=demo_date,
            shift_id=day_shift_id,
            rule_version_id=rule.id,
            detected_at=datetime(2026, 9, 1, 8, 0, 0),
            opened_at=datetime(2026, 9, 1, 8, 0, 0),
        )
        session.add(exception)
        
        session.commit()
    
    print(f"\n[4/4] Verification...")
    
    with Session(engine) as session:
        counts = {
            "tenants": session.execute(text("SELECT COUNT(*) FROM tenants")).scalar(),
            "users": session.execute(text("SELECT COUNT(*) FROM users")).scalar(),
            "memberships": session.execute(text("SELECT COUNT(*) FROM memberships")).scalar(),
            "sites": session.execute(text("SELECT COUNT(*) FROM sites")).scalar(),
            "shifts": session.execute(text("SELECT COUNT(*) FROM shift_templates")).scalar(),
            "roles": session.execute(text("SELECT COUNT(*) FROM roles")).scalar(),
            "crews": session.execute(text("SELECT COUNT(*) FROM crews")).scalar(),
            "workers": session.execute(text("SELECT COUNT(*) FROM workers")).scalar(),
            "equipment": session.execute(text("SELECT COUNT(*) FROM equipment")).scalar(),
            "employee_meta": session.execute(text("SELECT COUNT(*) FROM employee_meta")).scalar(),
            "roster_assignments": session.execute(text("SELECT COUNT(*) FROM roster_assignments")).scalar(),
            "checkpoint_policies": session.execute(text("SELECT COUNT(*) FROM checkpoint_policies")).scalar(),
            "roster_policies": session.execute(text("SELECT COUNT(*) FROM roster_policies")).scalar(),
            "exception_cases": session.execute(text("SELECT COUNT(*) FROM exception_cases")).scalar(),
        }
        
        print(f"\n{'='*60}")
        print(f"SEED COMPLETE — Verification")
        print(f"{'='*60}")
        all_ok = True
        for table, count in counts.items():
            status = "✅" if count > 0 else "❌"
            if count == 0:
                all_ok = False
            print(f"  {status} {table}: {count}")
        
        print(f"\n{'='*60}")
        if all_ok:
            print("✅ ALL CHECKS PASSED")
        else:
            print("⚠️  SOME TABLES ARE EMPTY — check warnings above")
        print(f"{'='*60}")
    
    return all_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Metro Mining from Google Sheets")
    parser.add_argument("--sheet-id", help="Google Sheet ID", default=os.getenv("METRO_GSHEET_ID"))
    parser.add_argument("--database-url", help="Database URL", default=os.getenv("FAZTRACK_DATABASE_URL"))
    
    args = parser.parse_args()
    
    if not args.sheet_id:
        print("ERROR: --sheet-id or METRO_GSHEET_ID env var required")
        print("\nUsage:")
        print("  python scripts/seed_metro_gsheet.py --sheet-id YOUR_SHEET_ID")
        print("  export METRO_GSHEET_ID=YOUR_SHEET_ID && python scripts/seed_metro_gsheet.py")
        sys.exit(1)
    
    success = seed_from_gsheet(args.sheet_id, args.database_url)
    sys.exit(0 if success else 1)
