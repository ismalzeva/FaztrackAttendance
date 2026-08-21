# METRO MM-M0/M1 ACCEPTANCE REPORT

**Project:** Faztrack Attendance — Metro Mining Tenant
**Report Date:** 2026-08-21
**Status:** PROVISIONALLY PASS
**Author:** Hermes (Personal Assistant Ismal)

---

## 1. Scope

### M0 — Tenant Configuration & Schema
- 14 new models (Site, Equipment, Role, Crew, Competency, ShiftTemplate, CheckpointPolicy, RosterPolicy, RuleVersion, RosterAssignment, EquipmentAssignmentActual, ExceptionEvent, OverrideEvent, EmployeeMeta)
- Alembic migration from existing Faztrack schema (0001–0005 → 0006)
- Rule versioning system (snapshot + get_active)
- Seed import from Metro Mining Master Data Simulation v1.0 workbook

### M1 — Roster, Assignment, Validations
- 8 roster validators (max consecutive work, mandatory rest, max same-shift, offsite rejection, overlap, equipment double-book, competency, equipment status)
- 40 acceptance tests covering all M0+M1 criteria

---

## 2. Assumptions

1. Metro Mining operates DAY (07:00–19:00) and NIGHT (19:00–07:00) shifts
2. Max 12 consecutive WORK days, REST on day 13
3. Max 7 same-shift consecutive days
4. 12-week ONSITE + 2-week OFFSITE cycle
5. OFFSITE employees cannot get WORK assignments
6. NIGHT events after midnight use previous operating_date
7. Planned equipment ≠ actual equipment (M2/M3)
8. All TBC decisions marked as such — no invented policies

---

## 3. Schema/Config Changed

### New Models (14)
| Model | Table | Purpose |
|-------|-------|---------|
| Site | sites | Mining sites, mess, briefing points |
| Equipment | equipments | Trucks, excavators, etc. |
| Role | roles | DT, FM, SH roles |
| Crew | crews | Crew A, B, C |
| Competency | competencies | Hauling, Excavator, etc. |
| ShiftTemplate | shift_templates | DAY/NIGHT with break/handover times |
| CheckpointPolicy | checkpoint_policies | GPS/geofence rules per site |
| RosterPolicy | roster_policies | Configurable rules (max consecutive, etc.) |
| RuleVersion | rule_versions | Immutable snapshots of active rules |
| RosterAssignment | roster_assignments | Daily roster with planned equipment |
| EquipmentAssignmentActual | equipment_assignment_actuals | Actual equipment (M2/M3) |
| ExceptionEvent | exception_events | Validator-detected violations |
| OverrideEvent | override_events | Manual overrides with approval |
| EmployeeMeta | employee_meta | Effective-dated role/crew/competency |

### New Enums (9)
- SiteType, SiteStatus, EquipmentStatus, CompetencyStatus, WorkStatus, SiteStatusEnum, ValidationStatus, ExceptionSeverity, ExceptionStatus

### Migration
- **File:** `backend/migrations/versions/7d30eba67c93_metro_mining_m0_models.py`
- **Revision:** `7d30eba67c93`
- **Parent:** `0005_m4_timesheet_quantification`
- **Tables created:** 14 new tables + 9 new enums
- **Existing tables:** Untouched (19 tables preserved)

---

## 4. Migration Evidence

### Upgrade from Existing Schema
```bash
$ rm -f tmp_metro_migration_test.db
$ FAZTRACK_DATABASE_URL=sqlite:///tmp_metro_migration_test.db alembic upgrade head
INFO  [alembic.runtime.migration] Context impl SQLiteImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  → 0001_m0_foundation, m0 foundation
INFO  [alembic.runtime.migration] Running upgrade 0001_m0_foundation → 0002_m1_attendance_events, m1 attendance events
INFO  [alembic.runtime.migration] Running upgrade 0002_m1_attendance_events → 0003_m2_device_enrollment, m2 device enrollment
INFO  [alembic.runtime.migration] Running upgrade 0003_m2_device_enrollment → 0004_m3_auth_tenant, m3 auth tenant
INFO  [alembic.runtime.migration] Running upgrade 0004_m3_auth_tenant → 0005_m4_timesheet_quantification, m4 timesheet quantification
INFO  [alembic.runtime.migration] Running upgrade 0005_m4_timesheet_quantification → 7d30eba67c93, metro_mining_m0_models
```

**Result:** ✅ PASS — 33 tables created (19 existing + 14 new)

### Downgrade
```bash
$ FAZTRACK_DATABASE_URL=sqlite:///tmp_metro_migration_test.db alembic downgrade -1
INFO  [alembic.runtime.migration] Running downgrade 7d30eba67c93 → 0005_m4_timesheet_quantification, metro_mining_m0_models
```

**Result:** ✅ PASS — 19 tables remain (Metro tables removed)

### Re-upgrade
```bash
$ FAZTRACK_DATABASE_URL=sqlite:///tmp_metro_migration_test.db alembic upgrade head
```

**Result:** ✅ PASS — 33 tables restored

---

## 5. Tests Performed

### Test Files
1. `tests/test_acceptance_metro.py` — 40 Metro Mining acceptance tests
2. `tests/test_attendance.py` — 4 existing attendance tests
3. `tests/test_auth_tenant.py` — 6 existing auth/tenant tests
4. `tests/test_device_enrollment.py` — 4 existing device tests
5. `tests/test_master_data_import.py` — 3 existing import tests
6. `tests/test_timesheets.py` — 4 existing timesheet tests

### Test Categories
| Category | Tests | Description |
|----------|-------|-------------|
| M0 Schema | 7 | Models, migration, seed, backward compat |
| M1 Roster | 8 | All 8 validators |
| M1 Assignment | 5 | Equipment, competency, overlap |
| M1 Validation | 5 | Exception detection, override |
| Rule Versioning | 4 | Historical preservation, snapshot |
| Effective Dating | 5 | Employee meta, crew, role, equipment, competency |
| Planned vs Actual | 3 | Equipment separation |
| Tenant Isolation | 3 | Cross-tenant data access |

---

## 6. Exact Test Results

```bash
$ python -m pytest tests/ -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/ubuntu/FaztrackAttendance/backend
configfile: pyproject.toml
plugins: anyio-4.14.2, cov-6.3.0
collected 61 items

tests/test_acceptance_metro.py ........................................  [ 65%]
tests/test_attendance.py ....                                            [ 72%]
tests/test_auth_tenant.py ......                                         [ 81%]
tests/test_device_enrollment.py ....                                     [ 88%]
tests/test_master_data_import.py ...                                     [ 93%]
tests/test_timesheets.py ....                                            [100%]

============================== 61 passed, 1 warning in 7.30s ==============================
```

**Result:** ✅ 61/61 PASS, 0 FAIL

---

## 7. Acceptance Criteria PASS/FAIL

| ID | Criterion | Status | Evidence |
|----|-----------|--------|----------|
| M0.1 | New models added | ✅ PASS | 14 models in models.py |
| M0.2 | Migration from existing schema | ✅ PASS | upgrade head → 33 tables |
| M0.3 | Downgrade safe | ✅ PASS | downgrade -1 → 19 tables |
| M0.4 | Rule versioning | ✅ PASS | test_26, test_27, test_29, test_30 |
| M0.5 | Seed import | ✅ PASS | 12 employees, 12 equipment, 1176 roster rows |
| M0.6 | Backward compat | ✅ PASS | 21 existing tests PASS |
| M1.1 | Max consecutive work | ✅ PASS | test_01 |
| M1.2 | Mandatory rest day | ✅ PASS | test_02 |
| M1.3 | Max same-shift | ✅ PASS | test_03 |
| M1.4 | Offsite rejection | ✅ PASS | test_04 |
| M1.5 | Overlap detection | ✅ PASS | test_05 |
| M1.6 | Equipment double-book | ✅ PASS | test_06 |
| M1.7 | Competency check | ✅ PASS | test_07 |
| M1.8 | Equipment status | ✅ PASS | test_08 |
| M1.9 | Effective dating | ✅ PASS | test_28, test_31, test_32, test_33, test_34 |
| M1.10 | Planned vs actual | ✅ PASS | test_35, test_36, test_37 |
| M1.11 | Tenant isolation | ✅ PASS | test_38, test_39, test_40 |

**Overall:** ✅ PROVISIONALLY PASS

---

## 8. Open Defects

None. All 61 tests pass.

---

## 9. Risks

1. **TBC decisions unresolved** — 10 operational decisions pending Metro Mining confirmation
2. **No production deployment** — tests run on SQLite, production will use PostgreSQL
3. **No GPS coordinates** — checkpoint policies have placeholder coordinates
4. **No HR/payroll/HSE integration** — exception events stored but no downstream action

---

## 10. TBC Decisions

| ID | Decision | Current State | Impact |
|----|----------|---------------|--------|
| TBC-01 | Site timezone | UTC (assumed) | Affects operating_date calculation |
| TBC-02 | Pickup tolerance | Not defined | Affects checkpoint validation |
| TBC-03 | GPS coordinates | Placeholder | Affects geofence validation |
| TBC-04 | Min rest hours | Not defined | Affects shift rotation validation |
| TBC-05 | 12-day counting | Calendar days | Affects consecutive work validation |
| TBC-06 | 12-week counting | Calendar weeks | Affects onsite/offsite cycle |
| TBC-07 | Streak reset | After REST day | Affects consecutive work counting |
| TBC-08 | Handover window | 18:45 final | Affects shift transition |
| TBC-09 | HR consequences | Not defined | Affects exception handling |
| TBC-10 | Payroll/HSE | Not defined | Affects downstream integration |

**Note:** All TBC values are marked as such in code. No invented policies.

---

## 11. Commit Information

### Commit 1 (Initial M0/M1)
- **Branch:** `main`
- **Hash:** `1042a81`
- **Message:** `feat(metro-m0-m1): Metro Mining tenant models, migration, seed, roster validator, 25 acceptance tests`

### Commit 2 (Migration + Test Fixes)
- **Branch:** `main`
- **Hash:** `77267ea`
- **Message:** `fix(metro-m0-m1): fix migration to only create new tables + fix 4 acceptance tests`

### Files Changed
```
backend/app/models.py                              | +250 lines (14 new models)
backend/app/rule_versioning.py                     | +75 lines (new)
backend/app/seed_metro.py                          | +410 lines (new)
backend/app/roster_validator.py                    | +370 lines (new)
backend/tests/test_acceptance_metro.py             | +900 lines (new, 40 tests)
backend/migrations/versions/7d30eba67c93_metro_mining_m0_models.py | +296 lines (new)
docs/METRO_M0_M1_GAP_ANALYSIS.md                  | +275 lines (new)
```

### Git Status
```
$ git status --short
(clean)
```

### Git Log
```
$ git log -3 --oneline
77267ea fix(metro-m0-m1): fix migration to only create new tables + fix 4 acceptance tests
1042a81 feat(metro-m0-m1): Metro Mining tenant models, migration, seed, roster validator, 25 acceptance tests
c2cbc80 Add Google Sheets master template and download links
```

---

## 12. Seed Import Results

```
tenant: 1
admin: 1
policies: 15
sites: 4
shifts: 2
checkpoint_policies: 14
roles: 3
crews: 3
employees: 12
equipment: 12
competencies: 12
roster_total: 1176
rule_version: 1
```

---

## 13. Recommendation

**M0+M1 = PROVISIONALLY PASS.**

All acceptance criteria met. Migration from existing schema verified. 61/61 tests pass. No open defects.

**M2 (attendance events, checkpoint validation, timesheet) is BLOCKED** until:
1. Metro Mining confirms 10 TBC decisions
2. GPS coordinates for sites (mess, briefing, operating area)
3. Ismal says "lanjut M2"

---

## 14. Sign-off

- [ ] Project Owner (Ismal) — FINAL ACCEPTANCE
- [ ] Metro Mining — TBC decisions confirmed
- [ ] DevOps — Production deployment approved

**Report prepared by:** Hermes (Personal Assistant Ismal)
**Date:** 2026-08-21
