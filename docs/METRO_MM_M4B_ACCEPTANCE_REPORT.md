# METRO MINING — MM-M4B ACCEPTANCE REPORT

**Milestone:** MM-M4B — Roster & Attendance Operational View
**Date:** 2026-08-22
**Status:** ✅ FINAL PASS
**Regression:** 528/528 ALL PASS (469 prior + 59 M4B)

---

## 1. Scope

MM-M4B provides the Field Supervisor with a detailed operational view behind M4A dashboard data.

**M4A answered:** "What needs my attention?"
**M4B answers:** "What exactly is happening with this worker / roster / shift?"

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/roster/operational` | GET | Roster board — tabular view of all workers for a shift |
| `/api/v1/roster/operational/{worker_id}` | GET | Worker detail — identity, roster, plan vs actual, timeline, exceptions, decisions, competencies |
| `/api/v1/roster/operational/{worker_id}/timeline` | GET | Timeline only — chronological event list |

---

## 2. Deliverables

| File | Lines | Description |
|---|---|---|
| `app/roster_service.py` | 1,324 | M4B core service — 13 dataclasses, 3 main functions, 2 serialization helpers |
| `app/roster.py` | 291 | FastAPI router — 3 endpoints, tenant-scoped, response envelope |
| `app/main.py` | 61 | Updated — roster router registered |
| `tests/test_acceptance_m4b.py` | 1,345 | 59 acceptance tests |

**Total new code:** 2,960 lines

---

## 3. Architecture Compliance

| Rule | Status |
|---|---|
| Pure read-only aggregation — no new tables | ✅ |
| Every query tenant-scoped | ✅ |
| No duplicated business logic | ✅ Uses `_derive_operational_state` from dashboard_service |
| Capability-aware rendering | ✅ Checks RosterPolicy for equipment_assignment_enabled |
| Work status separation | ✅ REST/OFFSITE/LEAVE/SICK/TRAINING/STANDBY skip checkpoint queries |
| Equipment history preserved | ✅ Multiple actual intervals shown, not collapsed |
| Planned ≠ actual never overwritten | ✅ Plan and actual shown separately |
| Generic role/equipment masters | ✅ Uses Role.role_name, Equipment.equipment_type — no hardcoded labels |
| NIGHT cross-midnight operating_date | ✅ Events before 07:00 belong to original operating_date |

---

## 4. Dataclasses

1. `RosterBoardContext` — tenant, site, date, shift, timezone, pagination info
2. `RosterBoardItem` — per-worker board row with operational state, equipment, exceptions, decisions
3. `RosterBoardResult` — context + items
4. `WorkerIdentity` — employee_id, name, code, no, role, crew
5. `WorkerRoster` — operating_date, shift, work_status, planned_equipment, rule_version
6. `EquipmentInterval` — single actual equipment interval (start/end/current/source)
7. `EquipmentHistory` — planned + actual intervals + comparison results + mismatch flag
8. `TimelineEntry` — timestamp, event_type, display_label, validation_status, evidence
9. `CheckpointDetailItem` — checkpoint type, timestamp, validation status, missing flag, window
10. `ExceptionContextItem` — exception_id, type, severity, status, owner
11. `DecisionContextItem` — decision_id, type, status, planned/actual display, authorization
12. `CompetencyState` — equipment_type, status (VALID/EXPIRED/SUSPENDED/MISSING), dates
13. `WorkerDetail` — identity + roster + state + equipment + timeline + checkpoints + exceptions + decisions + competencies

---

## 5. Service Functions

### `get_roster_board()` (line 434)
- Loads roster for tenant + operating_date + optional filters
- 17 filter parameters: crew_id, role_id, work_status, operational_state, has_exception, equipment_search, employee_search, sort_by, sort_dir, offset, limit, site_id, shift_id
- Bulk-loads related data (metas, workers, roles, crews, equipment, shifts) to avoid N+1
- Derives operational state for WORK employees via `_derive_operational_state`
- Non-WORK employees show work_status as operational state directly
- Checkpoint summary: "X PASS, Y FAIL, Z MISSING"
- Exception count + decision status per employee

### `get_worker_detail()` (line 687)
- Single worker drill-down with all operational context
- Equipment history with multiple actual intervals
- Timeline: merged CanonicalAttendanceEvent + checkpoint results
- Checkpoint details: validation results + missing checkpoints
- Exceptions + decisions + competencies

### `get_worker_timeline()` (line 787)
- Chronological event list only (subset of worker detail)

---

## 6. Test Results

```
528 passed, 0 failed, 1 warning in 44.64s
```

### M4B Tests (59 tests)

| Category | Tests | Description |
|---|---|---|
| Roster Board | 1–28 | Load, tenant/site/shift/date scope, all 7 work statuses, filters, equipment, exceptions, decisions, operational states, pagination, sorting |
| Worker Detail | 29–41 | Identity, roster, equipment history (multiple intervals), timeline, checkpoints, exceptions, decisions, competencies, OUT_OF_SERVICE, CONFIG_INCOMPLETE |
| Timeline | 42–43 | DAY shift chronological, NIGHT cross-midnight |
| Work Status | 44–47 | REST/OFFSITE/LEAVE/SICK — no false violations |
| Isolation | 48–51 | Tenant isolation (worker/equipment/roster), site isolation |
| Operator Substitution | 52–54 | Planned vs actual, approved/rejected decisions |
| Night Shift | 55 | operating_date preserved across midnight |
| Generic Masters | 56–57 | Role name from Role master, equipment type from Equipment master |
| Regression | 58–59 | No false violations for non-WORK, no duplicate business logic |

### Prior Milestones (469 tests)

| Test File | Count | Status |
|---|---|---|
| M0 | 36 | PASS |
| M2A | 32 | PASS |
| M2B | 31 | PASS |
| M2C | 45 | PASS |
| M2D | 35 | PASS |
| M2E | 21 | PASS |
| M3A | 36 | PASS |
| M3B | 43 | PASS |
| M3C | 47 | PASS |
| M3D | 32 | PASS |
| M4A | 57 | PASS |
| Metro seed | 36 | PASS |
| Auth/Attendance/Device/Master/Timesheets | 18 | PASS |
| **Total prior** | **469** | **ALL PASS** |

---

## 7. What M4B Does NOT Do

- No new database tables (pure read-only)
- No business rule recalculation (delegates to existing engines)
- No write operations
- No push notifications
- No payroll/HSE integration
- No geofence validation
- Does not duplicate dashboard_service logic

---

## 8. Remaining TBC Items

9 TBC items from prior milestones remain unchanged (see `docs/TBC_REGISTER.md`).

---

## 9. Quality Gate

| Gate | Criteria | Status |
|---|---|---|
| M4B-G1 | All 469 prior tests PASS | ✅ 469/469 |
| M4B-G2 | All M4B tests PASS | ✅ 59/59 |
| M4B-G3 | Roster board returns correct context | ✅ |
| M4B-G4 | All 7 work statuses visible correctly | ✅ |
| M4B-G5 | Work status separation (non-WORK skip checkpoints) | ✅ |
| M4B-G6 | Operational state derived correctly | ✅ |
| M4B-G7 | Equipment history with multiple intervals | ✅ |
| M4B-G8 | Plan/actual separation preserved | ✅ |
| M4B-G9 | Operator substitution + decision context | ✅ |
| M4B-G10 | NIGHT operating_date preserved across midnight | ✅ |
| M4B-G11 | Capability-aware rendering | ✅ |
| M4B-G12 | Tenant/site isolation | ✅ |
| M4B-G13 | Generic role/equipment master support | ✅ |
| M4B-G14 | No duplicated business logic | ✅ |

**VERDICT: M4B FINAL PASS ✅**
