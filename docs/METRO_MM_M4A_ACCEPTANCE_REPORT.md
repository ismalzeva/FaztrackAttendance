# MM-M4A ACCEPTANCE REPORT — FIELD SUPERVISOR OPERATIONAL DASHBOARD

**Milestone:** MM-M4A  
**Status:** FINAL PASS  
**Date:** 2026-08-22  
**Baseline before M4A:** 412/412 ALL PASS  
**After M4A:** 469/469 ALL PASS (412 prior + 57 M4A)  

---

## 1. DELIVERABLES

| File | Lines | Purpose |
|---|---|---|
| `app/dashboard_service.py` | ~1085 | Core aggregation service — read-only, no new tables |
| `app/dashboard.py` | ~54 | FastAPI router: `GET /dashboard/snapshot` |
| `app/main.py` | updated | Router registration |
| `app/decision_engine.py` | +3 lines | DEFECT FIX: `authorization_policy` set before `AuthorizationBlocked` raise |
| `tests/test_acceptance_m4a.py` | ~1530 | 57 acceptance tests |

---

## 2. API ENDPOINT

```
GET /dashboard/snapshot?site_id={id}&operating_date={YYYY-MM-DD}&shift_type={DAY|NIGHT}
```

**Returns:**
```json
{
  "context": { "tenant_id", "timezone", "generated_at", "last_event_at" },
  "shift_summary": { "scheduled_work", "scheduled_rest", "present_operational", ... },
  "roster_status": [ { "employee_id", "operational_state", "planned_equipment_code", ... } ],
  "checkpoint_status": [ { "employee_id", "checkpoint_type", "status", ... } ],
  "equipment_status": [ { "employee_id", "plan_display", "actual_display", "has_pending_decision", ... } ],
  "active_exceptions": [ { "exception_id", "exception_type", "severity", ... } ],
  "action_required": [ { "action_type", "employee_id", "description", "severity" } ],
  "pending_decisions": [ { "decision_id", "authorization_status", ... } ],
  "configuration_warnings": [ { "warning_type", "message" } ]
}
```

---

## 3. TEST COVERAGE (57 scenarios)

| Section | Tests | IDs |
|---|---|---|
| A. Context | 4 | M4A-01 – M4A-04 |
| B. Shift Summary | 5 | M4A-05 – M4A-09 |
| C. Roster Status | 8 | M4A-10 – M4A-17 |
| D. Equipment Display | 3 | M4A-18 – M4A-20 |
| E. Checkpoint Status | 4 | M4A-21 – M4A-24 |
| F. Pending Decisions | 4 | M4A-25 – M4A-28 |
| G. Configuration Warnings | 3 | M4A-29 – M4A-31 |
| H. Action Required | 4 | M4A-32 – M4A-35 |
| I. Site Isolation | 2 | M4A-36 – M4A-37 |
| J. Tenant Isolation | 2 | M4A-38 – M4A-39 |
| K. Night Shift | 3 | M4A-40 – M4A-42 |
| L. Empty/Pristine State | 3 | M4A-43 – M4A-45 |
| M. Data Correctness | 3 | M4A-46 – M4A-48 |
| N. API Endpoint | 2 | M4A-49 – M4A-50 |
| O. JSON Serialization | 1 | M4A-51 |
| P. Metadata | 1 | M4A-52 |
| Q. Attention Required | 1 | M4A-53 |
| R. Equipment Pending Decision | 1 | M4A-54 |
| S. Multiple Checkpoints | 1 | M4A-55 |
| T. Shift Boundary | 1 | M4A-56 |
| U. No Shift Specified | 1 | M4A-57 |

---

## 4. ARCHITECTURE DECISIONS

1. **Pure read aggregation** — dashboard is a thin read-only layer over M0–M3 data. No new tables, no migration.
2. **Tenant-scoped** — every query is filtered by `tenant_id`. No cross-tenant leakage.
3. **WITA timezone enforced** — `Asia/Makassar` (UTC+08:00).
4. **NIGHT cross-midnight** — `operating_date` = date when shift started (hour < 07:00 → previous day).
5. **Planned ≠ actual** — approved substitution displayed alongside plan; plan never rewritten.
6. **Configuration warnings vs violations** — config warnings separate from employee-level violations.
7. **TBC-safe** — doesn't invent missing policy values; marks `BLOCKED_POLICY_DECISION` when no policy.
8. **No business logic** — all operational rules delegate to existing M0–M3 engines.
9. **DEFECT FIX** — `decision_engine.py`: `authorization_policy` set on decision BEFORE raising `AuthorizationBlocked` (previously field stayed NULL on blocked decisions).

---

## 5. OPERATIONAL STATE DERIVATION (PRIORITY ORDER)

Per Ismal's directive (2026-08-22):

1. `ATTENTION_REQUIRED` — FAIL checkpoint validation (highest priority)
2. `SHIFT_COMPLETE` — CHECK_OUT event
3. `HANDOVER` — HANDOVER_START or HANDOVER_END event
4. `RETURNED_FROM_BREAK` — BREAK_OUT event (returned from break)
5. `ON_BREAK` — BREAK_IN event (on break)
6. `WORKING` — WORK_START checkpoint or CHECK_IN event
7. `AT_EQUIPMENT` — EQUIPMENT_IN checkpoint or EQUIPMENT_CHECK_IN event
8. `BRIEFING_COMPLETE` — BRIEFING_IN checkpoint or event
9. `NOT_STARTED` — no events

---

## 6. DEFECT FIXED

**DEFECT-002 (decision_engine.py):** `authorization_policy` field was never set on `ExceptionDecision` when `AuthorizationBlocked` was raised during `approve_decision`. The raise happened at line 203 before the field was set at line 230. Fixed by setting `decision.authorization_policy = auth_result` and flushing before the raise.

---

## 7. TBC ITEMS REMAINING (9 items)

Pre-existing from M0–M3. Not blocking M4A:

1. Pickup time (TBC)
2. Briefing opening/tolerance (TBC)
3. Geofence coordinates (TBC)
4. Geofence radius (TBC)
5. Minimum rest hours (TBC)
6. Streak reset semantics (TBC)
7. Handover semantics (TBC)
8. Payroll/disciplinary/HSE consequence (TBC)
9. Authorization/override details (TBC)

---

## 8. REGRESSION SUMMARY

| Suite | Tests | Status |
|---|---|---|
| Hardening | 30 | ✅ PASS |
| M2A | 32 | ✅ PASS |
| M2B | 31 | ✅ PASS |
| M2C | 45 | ✅ PASS |
| M2D | 35 | ✅ PASS |
| M2E | 21 | ✅ PASS |
| M3A | 36 | ✅ PASS |
| M3B | 43 | ✅ PASS |
| M3C | 45 | ✅ PASS |
| M3D | 32 | ✅ PASS |
| **M4A** | **57** | **✅ PASS** |
| Metro | 44 | ✅ PASS |
| Other | 18 | ✅ PASS |
| **TOTAL** | **469** | **✅ ALL PASS** |

---

**Verdict:** MM-M4A FINAL PASS — 57/57 M4A tests, 469/469 full regression.
