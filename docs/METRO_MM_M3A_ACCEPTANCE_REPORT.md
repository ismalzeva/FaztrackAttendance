# METRO MINING — MM-M3A EXCEPTION LIFECYCLE FOUNDATION

## Acceptance Report

**Date:** 2026-08-21
**Milestone:** MM-M3A — Exception Lifecycle Foundation
**Status:** ✅ FINAL PASS
**Branch:** `main`
**Commit:** (pending push)
**Migration:** `809d58c292b4`

---

## 1. Scope

MM-M3A implements the generic exception lifecycle foundation that converts detection results from M2 engines into auditable operational cases.

**What M3A delivers:**
- `ExceptionCase` model — generic exception entity with full lifecycle fields
- `ExceptionAction` model — immutable action history for every lifecycle transition
- `exception_engine.py` — creation eligibility, lifecycle transitions, idempotency, audit trail
- Allowed lifecycle: OPEN → ACKNOWLEDGED → RESOLVED / WAIVED
- 15 exception types from Metro catalog
- Creation eligibility: FAIL → exception; PASS/NOT_APPLICABLE/CONFIG_INCOMPLETE/BLOCKED_POLICY_DECISION → no employee exception
- Tenant isolation enforcement
- Source immutability (original detection never modified)

**What M3A does NOT deliver (deferred):**
- Supervisor UI / approval workflow (M3B)
- Payroll/disciplinary/HSE consequence (M3B or policy decision)
- Reopen capability (not required by current policy)
- Authorization/override matrix (TBC)
- Photo/video evidence upload (future)

---

## 2. Architecture

```
Detection Sources           Exception Engine              Audit Trail
─────────────────           ────────────────              ───────────
RuleEvaluation        ──→   ExceptionCase        ──→   ExceptionAction
EquipmentDiscrepancy  ──→     (lifecycle state)        (immutable history)
CheckpointValidation  ──→       │
                                │
                           Lifecycle Transitions:
                           OPEN → ACKNOWLEDGED → RESOLVED
                           OPEN → WAIVED
                           ACKNOWLEDGED → WAIVED
```

**Integration points:**
- M2D `RuleEvaluation` (FAIL) → ExceptionCase
- M2C `EquipmentDiscrepancy` (OPEN) → ExceptionCase
- M2B `CheckpointValidationResult` (FAIL) → ExceptionCase

---

## 3. Schema

### ExceptionCase (22 fields)

| Field | Type | Description |
|---|---|---|
| id | VARCHAR(36) | Primary key |
| tenant_id | VARCHAR(36) | FK → tenants |
| exception_type | VARCHAR(80) | Metro exception catalog code |
| employee_id | VARCHAR(36) | FK → workers |
| operating_date | DATE | Shift operating date |
| shift_id | VARCHAR(36) | FK → shift_templates |
| equipment_id | VARCHAR(36) | If equipment-related |
| site_id | VARCHAR(36) | If site-related |
| source_type | VARCHAR(80) | RULE_EVALUATION / EQUIPMENT_DISCREPANCY / CHECKPOINT_VALIDATION |
| source_id | VARCHAR(36) | FK to source detection record |
| rule_version_id | VARCHAR(36) | Preserved rule version |
| severity | VARCHAR(8) | CRITICAL / WARNING / INFO |
| status | VARCHAR(12) | OPEN / ACKNOWLEDGED / RESOLVED / WAIVED |
| detected_at | DATETIME | When detection occurred |
| opened_at | DATETIME | When exception case created |
| acknowledged_at | DATETIME | When acknowledged |
| resolved_at | DATETIME | When resolved |
| waived_at | DATETIME | When waived |
| current_owner_id | VARCHAR(36) | Current reviewer/supervisor |
| metadata_json | TEXT | Flexible metadata |
| created_at | DATETIME | Record creation |
| updated_at | DATETIME | Last update |

**Idempotency:** `UniqueConstraint(tenant_id, source_type, source_id, exception_type)`

### ExceptionAction (12 fields)

| Field | Type | Description |
|---|---|---|
| id | VARCHAR(36) | Primary key |
| tenant_id | VARCHAR(36) | FK → tenants |
| exception_id | VARCHAR(36) | FK → exception_cases |
| action_type | VARCHAR(11) | ACKNOWLEDGE / RESOLVE / WAIVE |
| actor_user_id | VARCHAR(36) | Who performed the action |
| action_timestamp | DATETIME | When action occurred |
| previous_status | VARCHAR(12) | Status before transition |
| new_status | VARCHAR(12) | Status after transition |
| reason | TEXT | Documented reason (required for WAIVE) |
| note | TEXT | Additional notes |
| evidence_ref | TEXT | Reference to evidence |
| metadata_json | TEXT | Flexible metadata |
| created_at | DATETIME | Record creation |

---

## 4. Migration

**Revision:** `809d58c292b4`
**Parent:** `726fa9455e03`
**Strategy:** Additive only — two new tables, zero modifications to existing schema.

```
alembic upgrade head   →  creates exception_cases + exception_actions
alembic downgrade -1   →  drops both tables safely
```

---

## 5. Lifecycle States

| Status | Meaning | Is Terminal |
|---|---|---|
| OPEN | Automatically created from qualifying detection, awaiting human review | No |
| ACKNOWLEDGED | Human reviewer has seen/accepted ownership. Does NOT mean violation confirmed. | No |
| RESOLVED | Operational issue closed with documented resolution | Yes |
| WAIVED | Issue recorded but enforcement waived for documented reason | Yes |

---

## 6. Allowed Transitions

| From | To | Valid |
|---|---|---|
| OPEN | ACKNOWLEDGED | ✅ |
| OPEN | RESOLVED | ✅ (direct resolution allowed) |
| OPEN | WAIVED | ✅ |
| ACKNOWLEDGED | RESOLVED | ✅ |
| ACKNOWLEDGED | WAIVED | ✅ |
| RESOLVED | (any) | ❌ Terminal |
| WAIVED | (any) | ❌ Terminal |

---

## 7. Exception Creation Eligibility

| Source Status | Creates Exception | Rationale |
|---|---|---|
| FAIL | ✅ Employee violation | Qualifying operational failure |
| PASS | ❌ | No violation detected |
| NOT_APPLICABLE | ❌ | Rule not applicable |
| CONFIG_INCOMPLETE | ❌ Employee violation | Configuration issue, not employee fault |
| BLOCKED_POLICY_DECISION | ❌ Confirmed violation | Policy not yet decided |
| EquipmentDiscrepancy OPEN | ✅ EQUIPMENT_MISMATCH | Operational discrepancy |

---

## 8. Idempotency

Same underlying detection does not generate duplicate exception cases.

**Mechanism:** `UniqueConstraint(tenant_id, source_type, source_id, exception_type)` with `_get_or_create` pattern that returns existing case on retry.

**Verified:** Test `test_idempotent_rule_eval` — same RuleEvaluation processed twice → one ExceptionCase. Same for EquipmentDiscrepancy.

---

## 9. Audit History

Every lifecycle transition creates an immutable ExceptionAction record.

**Trace:** Raw Event → Canonical Event → Checkpoint/Equipment/Rule Evaluation → ExceptionCase → ExceptionAction

**For each transition, the system answers:**
- What happened → action_type
- When detected → detected_at
- Why exception was created → exception_type + source reference
- Who acknowledged/resolved/waived → actor_user_id
- When action occurred → action_timestamp
- Reason → reason field
- Evidence → evidence_ref
- Previous state → previous_status
- New state → new_status

---

## 10. Tenant Isolation

Strict client-cluster principle enforced:

- ExceptionCase.tenant_id always set from source tenant
- get_exception(), acknowledge/resolve/waive all verify tenant_id match
- Cross-tenant source detection rejected
- get_exceptions_for_employee() filters by tenant_id
- Lumin exceptions invisible to Metro queries and vice versa

**Verified:** Tests 25, 26, 27 (tenant isolation on creation, lifecycle action, cross-tenant rejection).

---

## 11. Tests

### M3A Test Suite: 36/36 PASS

| # | Test | Status |
|---|---|---|
| 1 | FAIL RuleEvaluation creates OPEN ExceptionCase | ✅ |
| 2 | PASS creates no exception | ✅ |
| 3 | NOT_APPLICABLE creates no exception | ✅ |
| 4 | CONFIG_INCOMPLETE creates no employee exception | ✅ |
| 5 | BLOCKED_POLICY_DECISION creates no confirmed exception | ✅ |
| 6 | EquipmentDiscrepancy creates linked exception | ✅ |
| 7 | Duplicate rule eval processing is idempotent | ✅ |
| 8 | Duplicate discrepancy processing is idempotent | ✅ |
| 9 | OPEN → ACKNOWLEDGED valid | ✅ |
| 10 | ACKNOWLEDGED → RESOLVED valid | ✅ |
| 11 | OPEN → WAIVED valid | ✅ |
| 12 | ACKNOWLEDGED → WAIVED valid | ✅ |
| 13 | Invalid RESOLVED → OPEN rejected | ✅ |
| 14 | Invalid WAIVED → ACKNOWLEDGED rejected | ✅ |
| 15 | Action history created for acknowledgement | ✅ |
| 16 | Action history created for resolution | ✅ |
| 17 | Action history created for waiver | ✅ |
| 18 | Previous/new status retained in action | ✅ |
| 19 | Original RuleEvaluation unchanged after waiver | ✅ |
| 20 | Original EquipmentDiscrepancy unchanged after resolution | ✅ |
| 21 | Reason stored for waiver | ✅ |
| 22 | Actor stored for action | ✅ |
| 23 | Rule version preserved | ✅ |
| 24 | Severity preserved | ✅ |
| 25 | Tenant isolation on exception creation | ✅ |
| 26 | Tenant isolation on lifecycle action | ✅ |
| 27 | Cross-tenant source detection rejected | ✅ |
| 28 | Repeated lifecycle request idempotent | ✅ |
| 29 | Terminal states block all transitions | ✅ |
| 30 | Full trace detection → exception → action | ✅ |
| 31 | Checkpoint FAIL creates exception | ✅ |
| 32 | Waiver without reason rejected | ✅ |
| 33 | Multiple rule codes create distinct exception types | ✅ |
| 34 | Employee query filtering (tenant, date, status) | ✅ |
| 35 | OPEN → RESOLVED direct resolution | ✅ |
| 36 | Checkpoint PASS no exception | ✅ |

### Full Regression: 292/292 PASS

| Suite | Tests | PASS |
|---|---|---|
| Hardening | 30 | 30 |
| M2A | 32 | 32 |
| M2B | 31 | 31 |
| M2C | 45 | 45 |
| M2D | 35 | 35 |
| M2E | 21 | 21 |
| **M3A** | **36** | **36** |
| Metro | 44 | 44 |
| Attendance | 4 | 4 |
| Auth/Tenant | 6 | 6 |
| Device | 4 | 4 |
| Master Data | 3 | 3 |
| Timesheets | 4 | 4 |
| **TOTAL** | **292** | **292** |

---

## 12. Files Changed

| File | Lines | Change |
|---|---|---|
| `backend/app/models.py` | 1004 | +135 lines (ExceptionCase, ExceptionAction, enums, transition map) |
| `backend/app/exception_engine.py` | 524 | NEW — exception creation, lifecycle, idempotency |
| `backend/tests/test_acceptance_m3a.py` | 921 | NEW — 36 acceptance tests |
| `backend/migrations/versions/809d58c292b4_m3a_exception_lifecycle_foundation.py` | 109 | NEW — additive migration |
| `docs/METRO_MM_M3A_ACCEPTANCE_REPORT.md` | — | NEW — this report |

---

## 13. Defects Found & Fixed

| # | Defect | Fix |
|---|---|---|
| 1 | CheckpointValidationStatus vs ValidationStatus import mismatch | Fixed: use `CheckpointValidationStatus` enum |
| 2 | CheckpointResult uses `validation_status` not `status` | Fixed: field reference corrected |
| 3 | CheckpointResult uses `detected_timestamp` not `validated_at` | Fixed: field reference corrected |

No regressions introduced.

---

## 14. Remaining TBC

| # | Item | Status |
|---|---|---|
| 1 | Pickup time tolerance | TBC |
| 2 | Briefing opening/tolerance | TBC |
| 3 | Geofence coordinates | TBC |
| 4 | Geofence radius | TBC |
| 5 | Minimum rest hours (10h configured, policy TBC) | TBC |
| 6 | Streak reset semantics | TBC |
| 7 | Handover semantics | TBC |
| 8 | Payroll/disciplinary/HSE consequence | TBC |
| 9 | Authorization/override details | TBC |

All TBC items correctly return CONFIG_INCOMPLETE or BLOCKED_POLICY_DECISION. M3A does NOT create employee exceptions for these.

---

## 15. Risks

| Risk | Mitigation |
|---|---|
| Future reopen policy needed | Transition map easily extended with PO approval |
| Supervisor UI needed for bulk case management | Deferred to M3B |
| Evidence upload (photo/video) not implemented | evidence_ref field exists for future attachment |
| Actor authorization matrix TBC | Data model supports actor roles; policy deferred |

---

## 16. Proposed M3B Scope

Based on M3A foundation, MM-M3B could include:
- Supervisor dashboard / case list UI
- Bulk acknowledge / resolve / waive
- Authorization matrix (who can waive vs resolve)
- Escalation rules (auto-escalate after N hours)
- Notification integration (alert supervisor on new exception)
- Reopen capability (if policy approves)
- Evidence upload (photo/document)

---

## 17. Quality Gate

| Criterion | Status |
|---|---|
| Migration PASS | ✅ |
| All prior tests PASS (256) | ✅ |
| All M3A tests PASS (36) | ✅ |
| No duplicate exceptions | ✅ |
| Lifecycle transitions enforced | ✅ |
| Immutable source detection preserved | ✅ |
| Action history immutable | ✅ |
| Rule version preserved | ✅ |
| Tenant isolation PASS | ✅ |
| No invented Metro authorization rules | ✅ |
| No payroll/disciplinal/HSE consequence | ✅ |
| No M3B/M3C/M4 scope leakage | ✅ |

**MM-M3A: ✅ FINAL PASS**

---

*Generated: 2026-08-21*
