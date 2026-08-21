# METRO MM-M2C ACCEPTANCE REPORT
## Planned vs Actual Equipment Assignment

| Field | Value |
|-------|-------|
| **Milestone** | MM-M2C |
| **Status** | ✅ FINAL PASS |
| **Date** | 2026-08-21 |
| **Migration** | `0b4d5c9f09b8` |
| **Tests** | 200/200 PASS (46 M2C + 154 prior) |
| **Branch** | main |

---

## 1. Scope

Build generic planned-versus-actual equipment assignment engine for Faztrack Attendance.

**In scope:**
- Planned assignment retention (roster immutable)
- Actual assignment recording (interval-based, multiple per shift)
- Planned vs actual comparison
- Discrepancy detection (equipment mismatch, operator substitution)
- Competency validation (capability-driven)
- Equipment status validation
- Interval integrity (worker overlap, equipment overlap)
- Idempotency (duplicate event handling)
- Tenant isolation (cross-tenant rejection)
- Timezone isolation

**Out of scope:**
- MM-M2D (full attendance rule validation)
- MM-M2E (final simulation)
- MM-M3 (supervisor approval/waiver/resolution)
- MM-M4 (dashboard/integration)

---

## 2. Architecture

### 2.1 Shared Core Engine + Strictly Isolated Company Configuration

**Permanent Architecture Rule:**

```
FAZTRACK ATTENDANCE MULTI-COMPANY PRINCIPLE

One shared reusable core.

Each client/company has isolated:
- configuration
- master data
- input mapping
- policies
- capabilities
- rules
- operational data
- tests

No company-specific branching in core.
No cross-company fallback.
No cross-company master-data inference.
Tenant context must be explicit throughout processing.
```

### 2.2 Processing Pipeline

```
Equipment Check-in Event
  → Tenant Context (explicit, never inferred)
  → Worker Validation (tenant boundary)
  → Equipment Validation (tenant boundary, ACTIVE status)
  → Idempotency Check (same event → DUPLICATE)
  → Competency Validation (capability-driven)
  → Interval Integrity (worker overlap, equipment overlap)
  → Create Actual Assignment
  → Compare Planned vs Actual
  → Detect Discrepancies
```

### 2.3 Tenant Capability Mechanism

Uses existing `RosterPolicy` model as generic tenant capability store.

**Capability keys:**
- `equipment_assignment_enabled` (boolean)
- `competency_validation_enabled` (boolean)

**Behavior:**
- `competency_validation_enabled=false` → `COMPETENCY_NOT_APPLICABLE` (passes, not validated)
- `competency_validation_enabled=true` + valid → `COMPETENCY_VALID`
- `competency_validation_enabled=true` + missing → `COMPETENCY_MISSING` (fails)
- `competency_validation_enabled=true` + expired → `COMPETENCY_EXPIRED` (fails)

**Absence of records is NOT equivalent to a policy decision.**

---

## 3. Schema Changes

### 3.1 Extended Model: `EquipmentAssignmentActual`

New fields:
- `operating_date` (Date, NOT NULL) — operating date for the assignment
- `shift_id` (String, nullable) — shift reference
- `site_id` (String, nullable) — site reference
- `canonical_event_id` (String, nullable) — traceability to canonical event
- `status` (ActualAssignmentStatus enum) — ACTIVE/CLOSED/TRANSFERRED
- `rule_version_id` (String, nullable) — rule version reference
- `created_at` (DateTime) — creation timestamp

### 3.2 New Enums

- `ActualAssignmentStatus`: ACTIVE, CLOSED, TRANSFERRED
- `ComparisonResult`: MATCH, MISMATCH, NO_PLANNED_EQUIPMENT, NO_ACTUAL_EQUIPMENT, CONFIG_INCOMPLETE, NOT_APPLICABLE
- `DiscrepancyType`: MISMATCH, UNPLANNED, MISSING_ACTUAL, COMPETENCY_FAIL, EQUIPMENT_STATUS_FAIL, OPERATOR_SUBSTITUTION, EQUIPMENT_MISMATCH
- `DiscrepancyStatus`: OPEN, PENDING_REVIEW, RESOLVED, OVERRIDDEN, EXPIRED

### 3.3 New Model: `EquipmentComparisonResult`

One per actual assignment comparison event. Idempotent via unique constraint on `actual_assignment_id`.

Fields: tenant_id, actual_assignment_id, employee_id, operating_date, shift_id, planned_equipment_id, actual_equipment_id, comparison_result, planned_worker_id, actual_worker_id, reason_code, rule_version_id, created_at.

### 3.4 New Model: `EquipmentDiscrepancy`

Substitution/mismatch candidate. Lifecycle: OPEN → PENDING_REVIEW → RESOLVED/OVERRIDDEN/EXPIRED.

Fields: tenant_id, actual_assignment_id, employee_id, operating_date, shift_id, planned_equipment_id, actual_equipment_id, planned_worker_id, actual_worker_id, discrepancy_type, source, canonical_event_id, status, reason, rule_version_id, created_at.

---

## 4. Engine Functions (`equipment_engine.py`)

| Function | Purpose |
|----------|---------|
| `get_tenant_capability()` | Read capability from RosterPolicy |
| `is_capability_enabled()` | Check boolean capability |
| `validate_competency()` | Capability-driven competency validation |
| `validate_equipment_status()` | Equipment ACTIVE check with effective dates |
| `_ensure_tz()` | Normalize naive/aware datetime for SQLite |
| `check_worker_overlap()` | Worker interval overlap detection |
| `check_equipment_overlap()` | Equipment double-operator detection |
| `create_actual_assignment()` | Full validation + creation pipeline |
| `close_actual_assignment()` | Close active assignment (equipment change) |
| `compare_planned_vs_actual()` | Planned vs actual comparison |
| `detect_substitution()` | Operator substitution detection |
| `create_mismatch_discrepancy()` | Equipment mismatch discrepancy |
| `process_equipment_checkin()` | Full check-in pipeline |
| `get_actual_assignments()` | Query assignments by tenant/date/worker |
| `get_discrepancies()` | Query discrepancies by tenant/date/status |

---

## 5. Company Isolation

### 5.1 Data Isolation

All queries are scoped by `tenant_id`. Cross-tenant data is never visible.

**Verified isolation:**
- Workers: Metro workers ≠ Lumin workers
- Equipment: Metro equipment ≠ Lumin equipment (same code "EX-025" in both = no collision)
- Competency: Metro competency cannot validate Lumin worker
- Roster: Metro planned ≠ Lumin planned
- Actual assignments: Metro actual ≠ Lumin actual
- Discrepancies: Metro discrepancies ≠ Lumin discrepancies

### 5.2 Capability Isolation

- Metro: `competency_validation_enabled=true` → validates competency
- Lumin: `competency_validation_enabled` not set (default=false) → NOT_APPLICABLE
- Changing Metro capability does not affect Lumin
- Changing Lumin capability does not affect Metro

### 5.3 Timezone Isolation

- Metro Mining: `Asia/Makassar` (WITA, UTC+08:00)
- Lumin Park: `Asia/Jakarta` (WIB, UTC+07:00)
- Changing Metro timezone does not affect Lumin
- Changing Lumin timezone does not affect Metro

---

## 6. Planned vs Actual Behavior

**Rule: ACTUAL MUST NEVER OVERWRITE PLANNED.**

Example:
- Planned: Tono → EX-025
- Actual: Tono → EX-031
- Result: Planned remains EX-025, Actual stored separately as EX-031
- Comparison: MISMATCH
- Discrepancy: OPEN / PENDING_REVIEW

**No auto-approve. No roster modification. M3 handles supervisor review.**

---

## 7. Interval Integrity

### 7.1 Worker Overlap

A worker cannot have two active assignments on the same operating date with overlapping time intervals.

### 7.2 Equipment Overlap

Equipment cannot have two different operators at the same time.

### 7.3 Equipment Change

Supported via: close first assignment → open second assignment. Both retained historically.

### 7.4 Cross-Midnight NIGHT Shift

NIGHT shift (19:00–07:00) crosses midnight. Events after midnight use previous operating_date.

---

## 8. Idempotency

- Same event retry → DUPLICATE (not new assignment)
- Idempotency check BEFORE overlap validation (prevents OVERLAP_WORKER on retry)
- Duplicate evidence does not create duplicate discrepancy

---

## 9. Test Evidence

### 9.1 M2C Tests (46 tests)

| # | Test | Status |
|---|------|--------|
| 1 | Planned = actual → MATCH | ✅ |
| 2 | Planned EX-025, actual EX-031 → MISMATCH | ✅ |
| 3 | Mismatch does not modify planned | ✅ |
| 4 | Actual retains source event | ✅ |
| 5 | Valid competency PASS | ✅ |
| 6 | Expired competency detected | ✅ |
| 7 | Missing competency detected | ✅ |
| 8 | ACTIVE equipment accepted | ✅ |
| 9 | OUT_OF_SERVICE equipment rejected | ✅ |
| 10 | Worker overlap detected | ✅ |
| 11 | Equipment double-operator overlap | ✅ |
| 12 | Adjacent non-overlapping intervals OK | ✅ |
| 13 | Equipment change retained | ✅ |
| 14 | NIGHT cross-midnight retains date | ✅ |
| 15 | Operator substitution detected | ✅ |
| 16 | Substitute competency validated | ✅ |
| 17 | Actual assignment idempotency | ✅ |
| 18 | Discrepancy idempotency | ✅ |
| 19 | Same code different tenant isolated | ✅ |
| 20 | Cross-tenant worker-equipment rejected | ✅ |
| 21 | Cross-tenant comparison rejected | ✅ |
| 22 | Telematics ≠ operator identity | ✅ |
| 23 | Raw → canonical → actual traceability | ✅ |
| 24 | Discrepancy on mismatch | ✅ |
| 25 | MATCH no discrepancy | ✅ |
| 26 | No planned handled explicitly | ✅ |
| 27 | Historical competency validity | ✅ |
| 28 | Lumin regression PASS | ✅ |
| 29 | Equipment change close+open | ✅ |
| 30 | Discrepancy status OPEN | ✅ |
| 31 | Comparison has rule_version_id | ✅ |
| 32 | Lumin competency → NOT_APPLICABLE | ✅ |
| 33 | Metro competency valid → PASS | ✅ |
| 34 | Metro competency missing → FAIL | ✅ |
| 35 | Metro competency expired → FAIL | ✅ |
| 36 | No records ≠ disable validation | ✅ |
| 37 | Metro capability isolation | ✅ |
| 38 | Lumin capability isolation | ✅ |
| 39 | Metro worker ≠ Lumin equipment | ✅ |
| 40 | Lumin worker ≠ Metro equipment | ✅ |
| 41 | Metro competency ≠ Lumin worker | ✅ |
| 42 | Lumin competency ≠ Metro worker | ✅ |
| 43 | Cross-tenant comparison rejected | ✅ |
| 44 | Retry idempotent | ✅ |
| 45 | No duplicate discrepancy | ✅ |
| 46 | Timezone isolation | ✅ |

### 9.2 Full Suite

| Test File | Tests | Status |
|-----------|-------|--------|
| test_acceptance_hardening.py | 30 | ✅ |
| test_acceptance_m2a.py | 32 | ✅ |
| test_acceptance_m2b.py | 31 | ✅ |
| test_acceptance_m2c.py | 46 | ✅ |
| test_acceptance_metro.py | 44 | ✅ |
| test_attendance.py | 4 | ✅ |
| test_auth_tenant.py | 6 | ✅ |
| test_device_enrollment.py | 4 | ✅ |
| test_master_data_import.py | 3 | ✅ |
| test_timesheets.py | 4 | ✅ |
| **TOTAL** | **200** | **✅ ALL PASS** |

---

## 10. Technical Debt

| ID | Description | Status |
|----|-------------|--------|
| TD-01 | Global Asia/Jakarta fallback for missing tenant timezone | OPEN — backward compat, future: CONFIG_INCOMPLETE |
| TD-02 | SQLite strips timezone info from datetime columns | MITIGATED — `_ensure_tz()` normalization |

---

## 11. Remaining TBC

| ID | Description | Impact |
|----|-------------|--------|
| TBC-01 | Geofence coordinates/radius | Equipment assignment does not use geofence yet |
| TBC-02 | Minimum rest hours between shifts | Not enforced in M2C |
| TBC-03 | Payroll/disciplinary/HSE consequences | Deferred to M3+ |
| TBC-04 | Authorization/override details | Deferred to M3 |

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SQLite timezone stripping | High (dev) | Low | `_ensure_tz()` normalization; PostgreSQL in prod preserves tz |
| Cross-tenant data leak | Low | Critical | All queries scoped by tenant_id; 12 isolation tests |
| Competency bypass via missing records | Low | High | Capability-driven: missing records = COMPETENCY_MISSING, not PASS |

---

## 13. Files Changed

| File | Change |
|------|--------|
| `backend/app/models.py` | +4 enums, extended EquipmentAssignmentActual, +2 new models |
| `backend/app/equipment_engine.py` | NEW — 650 lines, 15 functions |
| `backend/app/seed_metro.py` | +24 lines (capability policies) |
| `backend/tests/test_acceptance_m2c.py` | NEW — 1150 lines, 46 tests |
| `backend/tests/test_acceptance_metro.py` | +3 lines (operating_date fix) |
| `backend/migrations/versions/0b4d5c9f09b8_...py` | NEW — M2C migration |
| `docs/METRO_MM_M2C_ACCEPTANCE_REPORT.md` | NEW — this report |
| `docs/FAZTRACK_MULTI_COMPANY_ARCHITECTURE.md` | NEW — architecture decision |

---

## 14. Quality Gate

| Criterion | Status |
|-----------|--------|
| All M2C tests PASS | ✅ 46/46 |
| All prior tests PASS | ✅ 154/154 |
| Timezone-aware overlap fixed | ✅ `_ensure_tz()` |
| Idempotency before overlap | ✅ Step 3 in pipeline |
| Competency uses capability/policy | ✅ `competency_validation_enabled` |
| Metro missing competency ≠ PASS | ✅ Returns COMPETENCY_MISSING |
| Lumin not forced into Metro workflow | ✅ NOT_APPLICABLE |
| Planned never overwritten | ✅ |
| Actual history retained | ✅ |
| Overlap protection | ✅ |
| Cross-tenant equipment protection | ✅ |
| Cross-tenant competency protection | ✅ |
| Company capability isolation | ✅ |
| Timezone isolation | ✅ |
| No Lumin regression | ✅ |
| No Metro regression | ✅ |
| No invented TBC | ✅ |
| Migration PASS | ✅ |

**VERDICT: ✅ FINAL PASS**
