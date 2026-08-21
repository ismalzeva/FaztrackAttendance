# METRO MM-M2E ACCEPTANCE REPORT

**Milestone:** MM-M2E — End-to-End Operational Simulation & M2 Closure
**Status:** ✅ FINAL PASS
**Date:** 2026-08-21
**Branch:** main
**Commit:** (pending)
**Migration:** 726fa9455e03 (unchanged from M2D)

---

## 1. Scope

Prove that M2A + M2B + M2C + M2D work together as one end-to-end operational attendance engine for Metro Mining. This is the final M2 milestone.

**In scope:**
- 13 Metro E2E scenarios (A–M)
- Lumin Park E2E regression (technical isolation)
- M1 rules regression (protections preserved)
- Full audit trace verification
- Boundary tests (break return timing)
- Rule registry completeness
- Integration defect discovery and fixes
- Full shared-core regression suite

**Out of scope:**
- New business logic beyond integration proof
- MM-M3 / MM-M4 (not started)
- Cross-client business rule comparison

---

## 2. Test Results

| Category | Tests | PASS | FAIL |
|---|---|---|---|
| M2E Scenarios (A–M) | 13 | 13 | 0 |
| Lumin E2E Regression | 1 | 1 | 0 |
| M1 Rules Regression | 1 | 1 | 0 |
| Audit Trace | 1 | 1 | 0 |
| Rule Registry | 1 | 1 | 0 |
| Prior Smoke (M0–M2D) | 1 | 1 | 0 |
| Boundary Tests | 3 | 3 | 0 |
| **M2E Total** | **21** | **21** | **0** |
| Full Suite (all milestones) | 256 | 256 | 0 |

---

## 3. Scenario Coverage

### 3.1 Metro Mining E2E Scenarios

| # | Scenario | Status | Evidence |
|---|---|---|---|
| A | Normal DAY Shift | ✅ PASS | Full journey: BRIEFING_IN → EQUIPMENT_IN → WORK_START → BREAK_OUT → BREAK_IN → HANDOVER → SHIFT_OUT. No inappropriate FAIL. |
| B | Normal NIGHT Cross-Midnight | ✅ PASS | Events after midnight retain previous operating_date. BREAK_IN at 00:58 → operating_date = shift-origin date. |
| C | Late Briefing / TBC-Safe | ✅ PASS | TBC tolerance → BLOCKED_POLICY_DECISION. No invented values. |
| D | Equipment Mismatch | ✅ PASS | Planned EX-025, Actual EX-031 → MISMATCH discrepancy. Planned unchanged. |
| E | Operator Substitution | ✅ PASS | Worker B on Worker A's equipment → discrepancy created. No auto-approval. |
| F | Late DAY Break Return | ✅ PASS | BREAK_IN at 13:05 WITA → LATE_BREAK_RETURN FAIL. |
| G | Late NIGHT Break Return | ✅ PASS | BREAK_IN at 01:05 WITA → LATE_BREAK_RETURN FAIL. |
| H | Missing Briefing | ✅ PASS | Policy required, window closed, no event → MISSING_BRIEFING. |
| I | Missing SHIFT_OUT | ✅ PASS | CHECK_OUT policy configured, no CHECK_OUT received, shift ended → FAIL. REST excluded. |
| J | Geofence Config Missing | ✅ PASS | No geofence coordinates → CONFIG_INCOMPLETE. GPS evidence retained. |
| K | Equipment Change During Shift | ✅ PASS | New actual replaces old. Discrepancy candidate created for mismatch. |
| L | Duplicate/Retry Idempotency | ✅ PASS | Replayed events return same results at every layer (checkpoint, equipment, comparison, rule evaluation). |
| M | Technical Tenant Isolation | ✅ PASS | Metro events invisible to Lumin. Timezone independence verified. Cross-tenant isolation at canonical, equipment, and rule evaluation layers. |

### 3.2 Cross-Tenant Regression

| Test | Status | Evidence |
|---|---|---|
| Lumin E2E Journey | ✅ PASS | Lumin worker CHECK_IN → CHECK_OUT through compatible path. No Metro rules forced onto Lumin. All Lumin data isolated with Asia/Jakarta timezone. Geofence returns CONFIG_INCOMPLETE (TBC-safe). |

### 3.3 M1 Regression

| Test | Status | Evidence |
|---|---|---|
| Max Consecutive Work (12 days) | ✅ PASS | 12 WORK days seeded, Day 13 violation detected. |
| Equipment Double-Booking | ✅ PASS | Same equipment assigned to two workers → violation detected. |
| Worker Overlap Check | ✅ PASS | validate_no_overlap returns valid result. |

---

## 4. Integration Defects Found & Fixed

### 4.1 `compare_planned_vs_actual()` Idempotency (FIXED)

**Location:** `equipment_engine.py`, line ~400
**Defect:** Function docstring claimed "Idempotent via unique constraint on actual_assignment_id" but the code did not check for existing comparison results before INSERT. The unique constraint would catch duplicates at DB level, causing an IntegrityError instead of returning the existing result.
**Fix:** Added idempotency check (SELECT existing by `actual_assignment_id` before INSERT). Returns existing result on retry.
**Impact:** Prevents duplicate comparison results and enables safe event replay.
**Regression:** Verified by Scenario L (duplicate retry idempotency).

### 4.2 Lumin Park ShiftTemplate NOT NULL (FIXED)

**Location:** `test_acceptance_m2e.py`, `_seed_lumin()` helper
**Defect:** Lumin ShiftTemplate created without `break_start`, `break_end`, `handover_start`, `handover_end` — all NOT NULL columns in the schema.
**Fix:** Added required break and handover times to Lumin seed.
**Impact:** Test fixture now matches production schema constraints.

### 4.3 Scenario I Shift ID Contract (FIXED)

**Location:** `test_acceptance_m2e.py`, `test_scenario_i_missing_shift_out`
**Defect:** Test used `shift_id="day1"` but ShiftTemplate ID is `"DAY"`. Also lacked CHECK_OUT CheckpointPolicy seed.
**Fix:** Used correct ShiftTemplate ID `"DAY"`, seeded CHECK_OUT CheckpointPolicy, used past operating_date so shift has ended.
**Impact:** MISSING_SHIFT_OUT now correctly evaluated as FAIL for WORK employees.

### 4.4 M1 Regression Double-Seed (FIXED)

**Location:** `test_acceptance_m2e.py`, `test_m1_rules_regression`
**Defect:** Test seeded `ra-w1-2026-09-05-DAY` twice — once in 12-day loop (i=0), once for equipment double-book test — violating UNIQUE constraint on `(tenant_id, operating_date, employee_id)`.
**Fix:** Removed duplicate seed. w1 already has roster from consecutive work loop. Added `planned_equipment_id` to loop rosters.

---

## 5. Pipeline Proof

Every Metro E2E scenario traces through the complete pipeline:

```
ROSTER PLAN
    ↓
RAW EVENT
    ↓
CANONICAL EVENT (timezone-normalized, operating_date resolved)
    ↓
SHIFT RESOLUTION (DAY/NIGHT, cross-midnight handling)
    ↓
OPERATING DATE (Asia/Makassar, night cross-midnight)
    ↓
CHECKPOINT RESOLUTION (CheckpointPolicy by shift_id + type)
    ↓
CHECKPOINT VALIDATION (CheckpointValidationResult)
    ↓
ACTUAL EQUIPMENT ASSIGNMENT (EquipmentAssignmentActual)
    ↓
PLANNED VS ACTUAL COMPARISON (EquipmentComparisonResult, idempotent)
    ↓
OPERATIONAL RULE EVALUATION (RuleEvaluation, 11 rule codes)
    ↓
EXCEPTION / DISCREPANCY CANDIDATE (EquipmentDiscrepancy)
    ↓
AUDIT TRACE (all IDs chainable)
```

---

## 6. Audit Trace Evidence

Full chain verified in `test_full_audit_trace`:

| Step | Model | Key Fields |
|---|---|---|
| Raw Event | `RawEvent` | tenant_id, source, raw_payload |
| Canonical Event | `CanonicalAttendanceEvent` | operating_date, shift_id, timezone |
| Equipment Assignment | `EquipmentAssignmentActual` | equipment_id, employee_id, status |
| Comparison | `EquipmentComparisonResult` | actual_assignment_id → MATCH |
| Rule Evaluation | `RuleEvaluation` | rule_code, status, severity |
| Trace Link | `EquipmentComparisonResult.actual_assignment_id` | Links back to assignment |

All references remain within Metro tenant cluster.

---

## 7. Technical Tenant Isolation Evidence

| Isolation Layer | Verification |
|---|---|
| Canonical Events | Metro query returns 0 Lumin events. Lumin query returns 0 Metro events. |
| Timezone | Metro = Asia/Makassar, Lumin = Asia/Jakarta. Independent resolution. |
| Equipment | Metro actual assignment has tenant_id="metro". Cannot access Lumin equipment. |
| Rule Evaluation | Metro evaluation has tenant_id="metro". Lumin evaluation isolated. |
| Worker IDs | Same business code (e.g., "w1") can exist in different tenants. PK + tenant_id isolation. |

---

## 8. Boundary Tests

| Test | Input | Expected | Actual |
|---|---|---|---|
| DAY break 12:58 | BREAK_IN 12:58 WITA | PASS | PASS ✅ |
| DAY break 13:00 | BREAK_IN 13:00 WITA | PASS | PASS ✅ |
| DAY break 13:01 | BREAK_IN 13:01 WITA | LATE_BREAK_RETURN | LATE_BREAK_RETURN ✅ |
| NIGHT break 00:58 | BREAK_IN 00:58 WITA | PASS | PASS ✅ |
| NIGHT break 01:00 | BREAK_IN 01:00 WITA | PASS | PASS ✅ |
| NIGHT break 01:01 | BREAK_IN 01:01 WITA | LATE_BREAK_RETURN | LATE_BREAK_RETURN ✅ |

Boundary semantics: ≤ boundary = PASS, > boundary = FAIL.

---

## 9. TBC Register

No new TBC items added in M2E. Existing TBC items unchanged:

| # | Item | Status |
|---|---|---|
| 1 | Pickup time | TBC |
| 2 | Briefing opening/tolerance | TBC |
| 3 | Geofence coordinates/radius | TBC |
| 4 | Geofence radius | TBC |
| 5 | Minimum rest hours | TBC |
| 6 | Streak reset semantics | TBC |
| 7 | Handover semantics | TBC |
| 8 | Payroll/disciplinary/HSE consequence | TBC |
| 9 | Authorization/override details | TBC |

All TBC items correctly return CONFIG_INCOMPLETE or BLOCKED_POLICY_DECISION.

---

## 10. Known Limitations

1. **Shift has ended check is time-dependent:** `evaluate_missing_shift_out` uses `datetime.now(tz)` to check if shift has ended. Tests use past dates to ensure shift completion.
2. **No production data:** All scenarios use synthetic test data. Real-world integration will require actual telematics/equipment feeds.
3. **SQLite in-memory:** Tests run on SQLite. PostgreSQL production behavior should be verified independently.

---

## 11. Quality Gate

| Criterion | Status |
|---|---|
| All Metro E2E scenarios PASS | ✅ |
| Integration defects resolved correctly | ✅ |
| Planned-vs-actual idempotency fixed | ✅ |
| Roster fixture identity preserved | ✅ |
| Shift ID/code contract resolved | ✅ |
| Metro DAY journey PASS | ✅ |
| Metro NIGHT journey PASS | ✅ |
| Metro operating_date PASS | ✅ |
| Metro equipment mismatch PASS | ✅ |
| Metro operator substitution PASS | ✅ |
| Metro late-break rules PASS | ✅ |
| Metro missing checkpoint behavior PASS | ✅ |
| Metro geofence remains TBC-safe | ✅ |
| Metro M1 protections active | ✅ |
| Technical tenant isolation PASS | ✅ |
| Full shared-core regression PASS (256/256) | ✅ |
| No other client's business rules modified | ✅ |
| No invented Metro TBC values | ✅ |
| No M3 functionality implemented | ✅ |

---

## 12. Verdict

**MM-M2E = ✅ FINAL PASS**

All 21 M2E tests pass. Full regression suite 256/256 pass. Zero regressions.4 integration defects found and fixed. No new TBC items. No cross-client business rule contamination.

---

*Report generated: 2026-08-21*
