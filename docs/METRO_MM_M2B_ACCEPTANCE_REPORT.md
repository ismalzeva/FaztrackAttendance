# MM-M2B — Checkpoint Engine — FINAL ACCEPTANCE REPORT

**Date:** 2026-08-21
**Status:** FINAL PASS
**Commit:** (see git log)
**Migration:** `8c07e14aa3d8` (m2b_checkpoint_engine)

---

## 1. Scope

MM-M2B builds a generic checkpoint engine on top of the M2A Canonical Attendance Event foundation.

**Objective:** Reusable checkpoint validation for all Faztrack Attendance tenants — Metro Mining, Lumin Park, future tenants. Configuration-driven, no hard-coded client-specific logic.

**In scope:**
- CheckpointPolicy extension (enabled, effective dating, sequence, rule version)
- CheckpointEventMapping (config-driven event→checkpoint mapping)
- CheckpointValidationResult (generic validation outcome)
- MissingCheckpointResult (expected but not received)
- Checkpoint engine service (resolve, validate, detect)
- Sequence awareness (expected/received/missing/out-of-order)
- Cross-midnight NIGHT shift support
- TBC-safe behavior (CONFIG_INCOMPLETE, BLOCKED_POLICY_DECISION)
- Idempotency (duplicate canonical→same result)
- Tenant isolation (strict tenant-scoped queries)
- Full auditability (raw→canonical→checkpoint traceability)

**Out of scope (explicitly excluded):**
- MM-M2C: Planned vs Actual Equipment Comparison
- MM-M2D: Full attendance rule validation
- MM-M2E: Final simulation
- MM-M3, MM-M4
- Payroll/disciplinary/HSE consequences
- Geofence PASS/FAIL validation (coordinates TBC)
- Equipment substitution workflow
- Supervisor approval workflow

---

## 2. Architecture

```
Source Event (PWA/Excel/QR/NFC/GPS/API/telematics)
  │
  ▼
Raw Event (immutable, M2A)
  │
  ▼
Canonical Attendance Event (M2A)
  │
  ▼
┌─────────────────────────────────────────────┐
│         CHECKPOINT ENGINE (M2B)             │
│                                             │
│  1. resolve_checkpoint_type()               │
│     source + event_type → checkpoint_type   │
│     (via CheckpointEventMapping)            │
│                                             │
│  2. get_policy() / get_active_policy()      │
│     tenant + type + shift + date → policy   │
│                                             │
│  3. validate_checkpoint()                   │
│     canonical_event + policy → result       │
│     - PASS / FAIL / CONFIG_INCOMPLETE       │
│     - BLOCKED_POLICY_DECISION               │
│     - NOT_APPLICABLE                        │
│                                             │
│  4. detect_missing_checkpoints()            │
│     roster + policy - received → missing    │
│                                             │
│  5. detect_sequence_violations()            │
│     expected order vs actual order          │
└─────────────────────────────────────────────┘
  │
  ▼
CheckpointValidationResult (audit trail)
```

---

## 3. Schema Changes

### 3.1 CheckpointPolicy Extended (existing table)

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | BOOLEAN | True | Policy active/inactive |
| `effective_from` | DATE | NOT NULL | Policy start date |
| `effective_to` | DATE | NULL | Policy end date (open-ended) |
| `sequence_order` | INTEGER | NULL | Expected checkpoint sequence position |
| `rule_version_id` | VARCHAR(64) | NULL | FK→rule_versions for traceability |
| `default_validation_behavior` | VARCHAR(32) | NULL | TBC behavior override |

### 3.2 CheckpointEventMapping (new table)

| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR(64) PK | Unique ID |
| `tenant_id` | VARCHAR(64) FK | Tenant scope |
| `source` | VARCHAR(64) | Event source (PWA, QR, GPS, etc.) |
| `event_type` | VARCHAR(64) | Canonical event type string |
| `checkpoint_type` | VARCHAR(64) | Target checkpoint type |
| `enabled` | BOOLEAN | Mapping active/inactive |
| `effective_from` | DATE | Mapping start date |
| `effective_to` | DATE | Mapping end date |

Unique constraint: `(tenant_id, source, event_type)`

### 3.3 CheckpointValidationResult (new table)

| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR(64) PK | UUID |
| `tenant_id` | VARCHAR(64) FK | Tenant scope |
| `canonical_event_id` | VARCHAR(64) FK | Source canonical event |
| `employee_id` | VARCHAR(64) FK | Worker |
| `checkpoint_type` | VARCHAR(32) | Checkpoint type |
| `operating_date` | DATE | Operating date |
| `shift_id` | VARCHAR(64) FK | Shift template |
| `policy_id` | VARCHAR(64) FK | Policy used |
| `rule_version_id` | VARCHAR(64) | Rule version |
| `validation_status` | ENUM | PASS/FAIL/CONFIG_INCOMPLETE/NOT_APPLICABLE/BLOCKED_POLICY_DECISION |
| `detected_timestamp` | DATETIME | When event occurred |
| `evidence_json` | TEXT | JSON evidence (GPS, device, etc.) |
| `reason_code` | VARCHAR(64) | Validation reason |
| `metadata_json` | TEXT | Additional metadata |
| `created_at` | DATETIME | Record creation |

Unique constraint: `(tenant_id, canonical_event_id, checkpoint_type)` — idempotency

### 3.4 MissingCheckpointResult (new table)

| Column | Type | Description |
|--------|------|-------------|
| `id` | VARCHAR(64) PK | UUID |
| `tenant_id` | VARCHAR(64) FK | Tenant scope |
| `employee_id` | VARCHAR(64) FK | Worker |
| `checkpoint_type` | VARCHAR(32) | Missing checkpoint type |
| `operating_date` | DATE | Operating date |
| `shift_id` | VARCHAR(64) FK | Shift template |
| `policy_id` | VARCHAR(64) FK | Policy that expected it |
| `detection_timestamp` | DATETIME | When detected |
| `reason_code` | VARCHAR(64) | Why flagged |
| `created_at` | DATETIME | Record creation |

---

## 4. Migration

| Item | Value |
|------|-------|
| Revision | `8c07e14aa3d8` |
| Parent | `af69ae8e38de` (M2A) |
| Tables created | 3 (checkpoint_event_mappings, checkpoint_validation_results, missing_checkpoint_results) |
| Tables altered | 1 (checkpoint_policies — 6 columns added via batch_alter_table) |
| Indexes | 6 (tenant_id + unique constraints) |

**Verification:**
```
upgrade:   af69ae8e38de → 8c07e14aa3d8 PASS
downgrade: 8c07e14aa3d8 → af69ae8e38de PASS
re-upgrade: af69ae8e38de → 8c07e14aa3d8 PASS
```

---

## 5. Checkpoint Engine Service

**File:** `backend/app/checkpoint_engine.py` (~550 lines)

### 5.1 Core Functions

| Function | Purpose |
|----------|---------|
| `resolve_checkpoint_type()` | Event source+type → checkpoint type via CheckpointEventMapping |
| `get_policy()` | Get policy (any enabled state) for tenant/type/shift/date |
| `get_active_policy()` | Get enabled-only policy |
| `validate_checkpoint()` | Canonical event + checkpoint type → validation result |
| `process_canonical_event()` | Full pipeline: resolve→check enabled→validate |
| `get_expected_sequence()` | Get ordered checkpoint types for tenant/shift |
| `detect_sequence_violations()` | Compare expected vs actual order |
| `detect_missing_checkpoints()` | Find expected but unreceived checkpoints |

### 5.2 Validation Statuses

| Status | Meaning |
|--------|---------|
| `PASS` | Checkpoint valid within policy |
| `FAIL` | Checkpoint violates policy rule |
| `CONFIG_INCOMPLETE` | Policy not found or missing parameters |
| `NOT_APPLICABLE` | Policy disabled for this checkpoint |
| `BLOCKED_POLICY_DECISION` | Policy explicitly marked TBC |

### 5.3 TBC-Safe Behavior

- No policy found → `CONFIG_INCOMPLETE` (not silent PASS/FAIL)
- Policy with `default_validation_behavior = "BLOCKED_POLICY_DECISION"` → `BLOCKED_POLICY_DECISION`
- Window/tolerance not configured → PASS (don't invent rules)
- Geofence coordinates TBC → GPS stored in evidence, no location verdict
- Equipment reference stored, no planned equipment overwrite

### 5.4 Tenant Isolation

All queries scoped by `tenant_id`. Metro policies invisible to Lumin. Cross-tenant test coverage included.

### 5.5 Idempotency

Unique constraint `(tenant_id, canonical_event_id, checkpoint_type)` prevents duplicate results. Re-processing same canonical event returns existing result.

---

## 6. Tests

### 6.1 M2B Acceptance Tests (31 tests)

| # | Test | Status |
|---|------|--------|
| 1 | DAY BRIEFING_IN mapping | ✅ PASS |
| 2 | DAY BRIEFING_IN validated | ✅ PASS |
| 3 | DAY BREAK_OUT validated | ✅ PASS |
| 4 | DAY BREAK_IN at 12:58 | ✅ PASS |
| 5 | DAY BREAK_IN at 13:00 PASS | ✅ PASS |
| 6 | DAY BREAK_IN after 13:00 detected | ✅ PASS |
| 7 | NIGHT BREAK_IN 00:58 operating_date=Sep 5 | ✅ PASS |
| 8 | NIGHT BREAK_IN at 01:00 PASS | ✅ PASS |
| 9 | NIGHT BREAK_IN after 01:00 detected | ✅ PASS |
| 10 | Metro policy not visible to Lumin | ✅ PASS |
| 11 | Cross-tenant event mapping isolation | ✅ PASS |
| 12 | Cross-tenant result isolation | ✅ PASS |
| 13 | Disabled checkpoint NOT_APPLICABLE | ✅ PASS |
| 14 | No policy CONFIG_INCOMPLETE | ✅ PASS |
| 15 | BLOCKED_POLICY_DECISION behavior | ✅ PASS |
| 16 | GPS retained no geofence judgement | ✅ PASS |
| 17 | No false geofence verdict | ✅ PASS |
| 18 | Duplicate canonical idempotent | ✅ PASS |
| 19 | Missing checkpoint detected | ✅ PASS |
| 20 | Received checkpoint not missing | ✅ PASS |
| 21 | REST no missing checkpoint | ✅ PASS |
| 22 | OFFSITE no missing checkpoint | ✅ PASS |
| 23 | Out-of-order sequence detected | ✅ PASS |
| 24 | Raw→canonical→checkpoint traceability | ✅ PASS |
| 25 | Policy/rule-version traceability | ✅ PASS |
| 26 | Equipment evidence preserved | ✅ PASS |
| 27 | Lumin legacy flow regression | ✅ PASS |
| 28 | Metro uses WITA | ✅ PASS |
| 29 | Lumin uses WIB | ✅ PASS |
| 30 | NIGHT HANDOVER operating_date | ✅ PASS |
| 31 | NIGHT SHIFT_OUT operating_date | ✅ PASS |

### 6.2 Full Test Suite

```
154 passed, 0 failed
├── test_acceptance_m2b.py        31 passed (NEW)
├── test_acceptance_m2a.py        32 passed
├── test_acceptance_hardening.py  30 passed
├── test_acceptance_metro.py      40 passed
├── test_attendance.py             4 passed
├── test_auth_tenant.py            6 passed
├── test_device_enrollment.py      4 passed
├── test_master_data_import.py     3 passed
└── test_timesheets.py             4 passed
```

---

## 7. Quality Gate

| Gate | Criteria | Status |
|------|----------|--------|
| G1 | Migration PASS | ✅ |
| G2 | All prior 123 tests remain PASS | ✅ (123/123) |
| G3 | New M2B tests PASS | ✅ (31/31) |
| G4 | No Lumin regression | ✅ |
| G5 | No tenant leakage | ✅ |
| G6 | NIGHT operating_date PASS | ✅ |
| G7 | Checkpoint idempotency PASS | ✅ |
| G8 | TBC-safe behavior PASS | ✅ |
| G9 | Raw/canonical/checkpoint traceability PASS | ✅ |
| G10 | Planned equipment unchanged | ✅ |
| G11 | No invented operational policy | ✅ |

**VERDICT: ALL 11 GATES PASS — MM-M2B = FINAL PASS**

---

## 8. Open Defects

None.

---

## 9. Remaining TBC Items

From TBC Register (carried forward):

| # | Item | Impact on M2B |
|---|------|---------------|
| 1 | Pickup time | No checkpoint policy for MESS_READY/PICKUP_CHECK yet |
| 2 | Briefing opening time | Window offsets = 0 (no invented values) |
| 3 | Briefing lateness tolerance | Tolerance = None (no invented values) |
| 4 | Geofence coordinates/radius | GPS stored, no location verdict |
| 5 | Minimum rest hours | Not applicable to M2B |
| 6 | Streak reset semantics | Not applicable to M2B |
| 7 | Handover semantics | Handover checkpoint mapped, no disciplinary meaning |
| 8 | Payroll/disciplinary/HSE consequence | Explicitly out of scope |
| 9 | Authorization/override details | Not applicable to M2B |

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Window/tolerance rules needed for production | High | Medium | Policy table ready; add values when confirmed |
| Geofence validation needed | Medium | Low | GPS evidence stored; geofence verdict deferred to M2C |
| Sequence enforcement too strict/loose | Low | Medium | Sequence detection is advisory; no disciplinary action |
| Performance with high event volume | Low | Low | Indexed queries; idempotency prevents duplicates |

---

## 11. Proposed MM-M2C Scope

Based on M2B foundation:

1. **Planned vs Actual Equipment Comparison** — compare roster planned_equipment_id with checkpoint EQUIPMENT_IN evidence
2. **EQUIPMENT_MISMATCH exception** — detect and record mismatches
3. **Equipment substitution workflow** — supervisor approval for unplanned equipment
4. **Geofence validation** — when coordinates confirmed, PASS/FAIL location checks
5. **Window/tolerance enforcement** — when TBC items resolved, FAIL for late checkpoints

---

## 12. Files Changed

| File | Action | Lines |
|------|--------|-------|
| `backend/app/models.py` | Modified | +120 (CheckpointPolicy extension + 3 new models) |
| `backend/app/checkpoint_engine.py` | Created | ~550 |
| `backend/migrations/versions/8c07e14aa3d8_m2b_checkpoint_engine.py` | Created | 108 |
| `backend/tests/test_acceptance_m2b.py` | Created | ~800 |
| `docs/METRO_MM_M2B_ACCEPTANCE_REPORT.md` | Created | this file |

---

**MM-M2B = FINAL PASS. Awaiting Project Owner approval for MM-M2C.**
