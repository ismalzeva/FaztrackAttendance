# Metro Mining Attendance — Gap Analysis & Implementation Plan
## M0–M1 Scope Only

**Date:** 2026-08-21
**Base:** Faztrack Attendance M0–M5 (commit c2cbc80)
**Target:** Metro Mining as reusable tenant, not hard-coded fork

---

## 1. Current State (Faztrack M0–M5)

### Models that exist
| Model | Key fields | Limitation for Metro |
|-------|-----------|---------------------|
| Tenant | code, timezone, status | ✅ Reusable as-is |
| User | login_id, password_hash | ✅ Reusable |
| Membership | tenant_id, user_id, role | ✅ Reusable |
| Project | code, name, lat/lon, radius, work_start/end | ❌ Single location; no site_type, no mess/briefing separation |
| Worker | code, name, phone, pin_hash | ❌ No role, crew, status lifecycle, effective dates |
| Assignment | tenant_id, worker_id, project_id, **work_date** | ❌ Date-per-row explosion; no shift, equipment, work_status |
| WorkSchedule | worker_id, work_date, start/end, is_working_day | ❌ Per-date; no shift template, no cross-midnight, no break/handover |
| ImportBatch | Google Sheets 5-tab format | ⚠️ Needs extension for new tabs or CSV/Excel import |
| Device* | Enrollment, Binding, Challenge | ✅ Reusable |
| AttendanceEvent | CHECK_IN/CHECK_OUT, geofence | ❌ No briefing/equipment/handover event types |
| AttendancePolicy | late_grace, early_leave_grace | ❌ No checkpoint policy, no shift-aware tolerances |
| AuditEvent | Full audit trail | ✅ Reusable |

### What does NOT exist (must be created)
| Entity | Purpose |
|--------|---------|
| Site | Multi-type locations (MINE_SITE, MESS, BRIEFING_POINT, OPERATING_AREA) |
| Equipment | Dump trucks, excavators with type, status, effective dates |
| Role | Maps to equipment_type_required |
| Crew | Groups with onsite cycle anchor and stagger offsets |
| Competency | Employee × equipment_type certification with validity dates |
| ShiftTemplate | DAY/NIGHT with break, handover, crosses_midnight flag |
| CheckpointPolicy | 8 checkpoint types per shift with windows and evidence |
| RosterPolicy | 12-day max, 7 same-shift, onsite/offsite cycle rules |
| RuleVersion | Versioned config snapshots attached to roster validations |
| RosterAssignment | Effective-dated roster: operating_date × employee × shift × equipment |
| EquipmentAssignmentActual | Planned vs actual equipment tracking |
| ExceptionEvent | Machine-readable rule violations with evidence |
| OverrideEvent | Supervisor approvals with reason and audit |
| HandoverEvent | Shift-to-shift equipment handover tracking |

---

## 2. Gap Classification

### G1 — Critical (blocks M1 tests)
| # | Gap | Current → Required | Priority |
|---|-----|-------------------|----------|
| G1.1 | Assignment model | work_date-only → effective_from/to + shift_id + work_status + equipment | P0 |
| G1.2 | No Site entity | Project (single type) → Site (multi-type: mine, mess, briefing, operating) | P0 |
| G1.3 | No Equipment entity | — → equipment_id, code, type, status, effective dates | P0 |
| G1.4 | No ShiftTemplate | WorkSchedule per-date → reusable DAY/NIGHT templates with break/handover | P0 |
| G1.5 | No RosterPolicy | hardcoded → configurable 12-day, 7-shift, onsite/offsite cycle | P0 |
| G1.6 | No Competency | — → employee × equipment_type with validity | P0 |
| G1.7 | No Crew | — → crew with onsite cycle anchor | P0 |
| G1.8 | No ExceptionEvent | — → rule_code, severity, evidence, status | P0 |
| G1.9 | No RuleVersion | — → versioned config snapshots | P0 |

### G2 — Important (blocks M1 quality)
| # | Gap | Priority |
|---|-----|----------|
| G2.1 | No roster validation engine (12-day, 7-shift, overlap, competency) | P1 |
| G2.2 | No planned-vs-actual equipment tracking | P1 |
| G2.3 | No EquipmentAssignmentActual | P1 |
| G2.4 | Import only supports Google Sheets 5-tab → need Excel workbook import | P1 |

### G3 — Deferred to M2+
| # | Gap | Target |
|---|-----|--------|
| G3.1 | 8 checkpoint types (briefing, equipment, work start, break-out/in, handover, shift-out) | M2 |
| G3.2 | Cross-midnight NIGHT operating_date resolution | M2 |
| G3.3 | Dashboard and daily compliance view | M4 |
| G3.4 | Excel/CSV adapter abstraction | M4 |

---

## 3. Implementation Plan — M0

### M0.1 — New Models + Migration
**File:** `backend/app/models.py`

Add these models (all tenant-scoped, UUID PK):

```
Site(id, tenant_id, site_id, site_name, site_type[MINE_SITE|MESS|BRIEFING_POINT|OPERATING_AREA], latitude, longitude, radius_m, status, effective_from, effective_to)

Equipment(id, tenant_id, equipment_id, equipment_code, equipment_type, status, effective_from, effective_to)

Role(id, tenant_id, role_id, role_name, equipment_type_required, status)

Crew(id, tenant_id, crew_id, crew_name, onsite_cycle_anchor, cycle_offset_days)

Competency(id, tenant_id, competency_id, employee_id, equipment_type, certification_no, valid_from, valid_to, status)

ShiftTemplate(id, tenant_id, shift_id, shift_name, start_time, end_time, break_start, break_end, handover_start, handover_end, crosses_midnight)

CheckpointPolicy(id, tenant_id, checkpoint_type, shift_id, window_start_offset_min, window_end_offset_min, required_evidence, severity)

RosterPolicy(id, tenant_id, policy_key, policy_value, data_type, confirmation_status)

RuleVersion(id, tenant_id, version_id, version_label, effective_from, config_snapshot_json, created_at)

RosterAssignment(id, tenant_id, roster_id, operating_date, employee_id, crew_id, site_cycle_day, site_status, work_status[WORK|REST|OFFSITE|...], shift_id, site_id, planned_equipment_id, effective_rule_version, validation_status)

EquipmentAssignmentActual(id, tenant_id, roster_id, employee_id, equipment_id, started_at, ended_at, source, supervisor_id)

ExceptionEvent(id, tenant_id, exception_id, operating_date, employee_id, equipment_id, rule_code, severity, detected_at, status[OPEN|ACKNOWLEDGED|RESOLVED|WAIVED], evidence_ref, rule_version)

OverrideEvent(id, tenant_id, override_id, exception_id, action, reason, approved_by, approved_at)
```

### M0.2 — Tenant Config + Seed
**File:** `backend/app/seed_metro.py` (new)

- Create tenant `METRO-MINING` with timezone `Asia/Jakarta` (TBC)
- Load Config sheet → RosterPolicy rows
- Load Sites → Site rows
- Load Shifts & Checkpoints → ShiftTemplate + CheckpointPolicy rows
- Load Roles & Crews → Role + Crew rows
- Load Employees → Worker rows with role_id, crew_id
- Load Equipment → Equipment rows
- Load Competencies → Competency rows
- Load Roster Simulation (1176 rows) → RosterAssignment rows
- Load Events & Exceptions → ExceptionEvent seed rows
- Load Exception Catalog → reference data
- Load Acceptance Tests → test catalog

### M0.3 — Rule Versioning
**File:** `backend/app/rule_versioning.py` (new)

- `create_rule_version(tenant_id, label, config_snapshot)` → returns version_id
- `get_active_rule_version(tenant_id)` → latest effective version
- Attach version_id to every roster validation and exception

### M0.4 — Alembic Migration
**File:** `backend/migrations/versions/0003_metro_mining.py` (new)

- Add all new tables
- Backward compatible: existing M0–M2 tables untouched
- Verify: `alembic upgrade head` on fresh DB

### M0 Acceptance Criteria
| # | Test | Evidence |
|---|------|----------|
| AT-00 | Alembic migration upgrades cleanly | Migration log |
| AT-00b | Existing M0–M2 tests still pass | pytest output |
| AT-00c | Metro tenant created with config | API /summary |
| AT-00d | 1176 roster rows imported | DB count query |
| AT-00e | Shift templates DAY/NIGHT configured | API query |
| AT-00f | Equipment × employee competencies loaded | DB count |
| AT-00g | Rule version attached to roster rows | Sample query |

---

## 4. Implementation Plan — M1

### M1.1 — Roster Validation Engine
**File:** `backend/app/roster_validator.py` (new)

Functions:
- `validate_consecutive_workdays(employee_id, roster)` → block day 13 after 12 WORK
- `validate_same_shift_streak(employee_id, roster)` → block 8th consecutive same-shift WORK
- `validate_onsite_offsite_cycle(employee_id, crew, roster)` → block WORK during OFFSITE
- `validate_no_overlap(employee_id, roster)` → no two WORK on same operating_date
- `validate_equipment_double_booking(equipment_id, roster)` → no two employees same equipment same shift
- `validate_competency(employee_id, equipment_id)` → check Competency table
- `validate_equipment_status(equipment_id)` → check not OUT_OF_SERVICE
- `validate_rest_between_shifts(employee_id, roster)` → min rest hours (TBC)

### M1.2 — Roster Import
**File:** `backend/app/roster_import.py` (new)

- Read Excel workbook (simulation file format)
- Map columns to RosterAssignment fields
- Run all validations before insert
- Return validation report (pass/fail per row)
- Confirm → upsert RosterAssignment rows
- Reject → hold errors for review

### M1.3 — Equipment Assignment API
**File:** `backend/app/equipment_assignment.py` (new)

- `POST /api/v1/equipment-assignments` → create planned assignment
- `GET /api/v1/equipment-assignments?date=&employee_id=` → query
- Validate: competency, equipment status, no double-booking
- Store planned separately from actual

### M1.4 — Roster API
**File:** `backend/app/roster.py` (new)

- `GET /api/v1/roster?from=&to=&employee_id=&crew_id=` → query roster
- `POST /api/v1/roster/validate` → run all validators, return report
- `POST /api/v1/roster/publish` → lock roster after validation passes

### M1 Acceptance Criteria
| # | Test | Source | Evidence |
|---|------|--------|----------|
| AT-01 | 12 WORK then day-13 REST | Acceptance #1 | Test output |
| AT-02 | Reject WORK on day 13 | Acceptance #2 | Test output |
| AT-03 | Accept return day 14 | Acceptance #3 | Test output |
| AT-04 | 7 DAY streak then NIGHT ok | Acceptance #4 | Test output |
| AT-05 | Reject 8th consecutive same-shift | Acceptance #5 | Test output |
| AT-06 | Reject WORK during OFFSITE | Acceptance #6 | Test output |
| AT-07 | 12 weeks onsite + 2 weeks offsite | Acceptance #7 | Test output |
| AT-08 | Reject overlapping assignments | Acceptance #8 | Test output |
| AT-09 | Reject double-booked equipment | Acceptance #9 | Test output |
| AT-10 | Competency check passes/fails | Acceptance #18 | Test output |
| AT-11 | Equipment out-of-service rejected | Acceptance #19 | Test output |

---

## 5. Open TBC Decisions (must mark, not guess)

| # | Decision | Default | Status |
|---|----------|---------|--------|
| D1 | Site timezone | Asia/Jakarta | TBC |
| D2 | Briefing check-in window | 30 min before shift | TBC |
| D3 | Geofence coordinates (mess, briefing, operating) | Placeholder | TBC |
| D4 | Minimum rest hours between shift rotation | — | BLOCKED_POLICY_DECISION |
| D5 | 12-day count: calendar days or worked shifts? | Worked shifts | TBC |
| D6 | Same-shift streak reset: 1 rest day or true rotation? | 1 rest day | TBC |
| D7 | 12 weeks onsite: 84 calendar days or 12 roster weeks? | 12 roster weeks | TBC |
| D8 | Handover 18:45: earliest permitted or target latest start? | Earliest permitted | TBC |
| D9 | Payroll/HSE consequences per exception | — | BLOCKED_POLICY_DECISION |
| D10 | Briefing late tolerance (minutes) | — | TBC |

---

## 6. Risk Register

| # | Risk | Mitigation |
|---|------|-----------|
| R1 | Existing M0–M5 tests break after migration | Run full regression before any commit |
| R2 | Roster 1176 rows import slowly | Batch insert, verify count |
| R3 | Cross-midnight NIGHT logic deferred to M2 but roster needs operating_date | Store operating_date on roster now; resolve event time in M2 |
| R4 | Assignment model change breaks existing attendance flow | Keep old Assignment table; add RosterAssignment as new table; bridge later |
| R5 | TBC decisions block certain validations | Implement with configurable defaults, mark clearly |

---

## 7. Files to Change

| File | Action | Description |
|------|--------|-------------|
| `backend/app/models.py` | MODIFY | Add 12 new models |
| `backend/migrations/versions/0003_metro_mining.py` | CREATE | New migration |
| `backend/app/seed_metro.py` | CREATE | Seed from Excel workbook |
| `backend/app/rule_versioning.py` | CREATE | Rule version management |
| `backend/app/roster_validator.py` | CREATE | 8 validation functions |
| `backend/app/roster_import.py` | CREATE | Excel roster import |
| `backend/app/roster.py` | CREATE | Roster API router |
| `backend/app/equipment_assignment.py` | CREATE | Equipment assignment API |
| `backend/app/main.py` | MODIFY | Register new routers |
| `backend/tests/test_roster_validation.py` | CREATE | M1 acceptance tests |
| `backend/tests/test_seed_metro.py` | CREATE | M0 seed tests |

---

## 8. Execution Order

1. M0.1: Models + Migration (verify existing tests pass)
2. M0.2: Seed from workbook
3. M0.3: Rule versioning
4. M0.4: Verify all M0 acceptance criteria
5. M1.1: Roster validation engine
6. M1.2: Roster import
7. M1.3: Equipment assignment API
8. M1.4: Roster API
9. M1.5: Run all M1 acceptance tests
10. STOP — wait for gate pass before M2
