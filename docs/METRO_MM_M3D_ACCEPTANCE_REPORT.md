# MM-M3D Acceptance Report

## End-to-End Supervisor Control & M3 Closure

| Field | Value |
|---|---|
| **Milestone** | MM-M3D |
| **Status** | ✅ FINAL PASS |
| **Date** | 2026-08-22 |
| **Branch** | `main` |
| **Baseline** | 380/380 PASS |
| **M3D Tests** | 32/32 PASS |
| **Final Total** | **412/412 PASS** |
| **Regressions** | 0 |

---

## 1. Scope

MM-M3D is integration validation and M3 closure. No new business functionality was added. The milestone proves that M3A + M3B + M3C work together as one complete, auditable supervisor control workflow for Metro Mining.

### In Scope
- End-to-end integration tests (scenarios A–O)
- Mandatory invariant tests (approval≠resolution, rejection≠resolution, waiver≠deletion, planned/actual/decision separation)
- Integration defect discovery and fix
- Tenant isolation verification
- Authorization boundary enforcement
- TBC-safe behavior verification

### Out of Scope (per directive)
- M4 dashboard
- Payroll/disciplinary/HSE consequences
- Notifications
- Invented Metro authority hierarchy

---

## 2. Integration Defect Found & Fixed

### DEFECT-001: Missing Authorization Blocking in `approve_decision`

**Root Cause:** `approve_decision()` called `_check_authorization()` but ignored the return value. When no authorization policy was provided, `_check_authorization()` returned `"BLOCKED_POLICY_DECISION"` but the approval proceeded anyway — silently violating the M3C specification.

**Impact:** Critical — unauthorized approval could succeed. A decision requiring authorization could be approved without any valid policy.

**Fix:** Added explicit check after `_check_authorization()` returns. If auth result starts with `"BLOCKED"`, raise `AuthorizationBlocked`. Only `SIM_APPROVER` and `SIM_OVERRIDE` are allowed for test/simulation.

**File Changed:** `backend/app/decision_engine.py` — 7 lines added.

**Regression Impact:** M3C test `test_m3c_19_missing_authorization` was updated to match correct behavior (now expects `AuthorizationBlocked` instead of silent success).

**Verification:** All 404 tests pass. No regression.

---

## 3. Scenarios Covered

### A — Late Break Review and Resolution
**Tests:** `test_m3d_scenario_a_late_break_resolution`, `test_m3d_scenario_a_queue_shows_open_only`

Full lifecycle: RuleEvaluation FAIL → ExceptionCase OPEN → review queue → review detail → ACKNOWLEDGED → review note + human evidence → explicit RESOLVED.

**Verified:**
- Original BREAK_IN time preserved (RuleEvaluation unchanged)
- All lifecycle transitions recorded in action history
- Actor/reason/timestamps retained
- Resolution does not rewrite detection
- Review queue shows only OPEN exceptions

### B — Equipment Substitution Approved
**Test:** `test_m3d_scenario_b_equipment_substitution_approved`

Equipment mismatch → exception → acknowledge → evidence → EQUIPMENT_SUBSTITUTION decision → SIMULATION authorization → APPROVED → explicit resolve.

**Verified:**
- Planned remains EX-025
- Actual remains EX-031
- Decision records approval with reason
- Nothing overwritten
- Approval ≠ auto-resolution (exception stays ACKNOWLEDGED until explicit resolve)

### C — Operator Substitution Approved
**Test:** `test_m3d_scenario_c_operator_substitution_approved`

Different worker on same equipment → exception → acknowledge → decision → SIMULATION authorization → APPROVED.

**Verified:**
- Planned Worker A preserved
- Actual Worker B preserved
- Decision records substitution
- Worker B competency valid (w2 has VALID competency)

### D — Substitution Rejected
**Test:** `test_m3d_scenario_d_substitution_rejected`

Decision request → reject with reason.

**Verified:**
- Decision PENDING → REJECTED
- Rejection reason recorded
- Original discrepancy preserved
- Exception stays OPEN (rejection ≠ resolution)

### E — Authorization Missing
**Test:** `test_m3d_scenario_e_authorization_missing`

Decision request → approval without authorization_policy.

**Verified:**
- `AuthorizationBlocked` raised
- Decision remains PENDING
- No auto-approval
- No fabricated authority

### F — Critical Validation Blocks Approval
**Tests:** `test_m3d_scenario_f_inactive_worker_blocks`, `test_m3d_scenario_f_out_of_service_equipment_blocks`, `test_m3d_scenario_f_expired_competency_blocks`

**Verified:**
- Inactive worker (w3, is_active=False) blocks operator substitution approval
- OUT_OF_SERVICE equipment blocks equipment substitution approval
- Expired competency blocks substitution approval
- `DecisionValidationFailed` with descriptive failure messages

### G — Waiver
**Test:** `test_m3d_scenario_g_waiver`

Exception OPEN → WAIVED with actor/reason/timestamp.

**Verified:**
- Original RuleEvaluation unchanged (status remains FAIL)
- Waive action recorded with actor and reason
- Waived exception retains full history
- Waiver ≠ "event never happened"

### H — Review Ownership
**Tests:** `test_m3d_scenario_h_review_ownership`, `test_m3d_scenario_h_cross_tenant_assign_blocked`

**Verified:**
- Ownership assignment and reassignment work
- Previous owner retained in audit (via ASSIGN_REVIEWER action)
- Current owner correct
- Cross-tenant assign reviewer rejected (ValueError)

### I — Evidence Chain
**Tests:** `test_m3d_scenario_i_evidence_chain`, `test_m3d_scenario_i_cross_tenant_evidence_blocked`

Multiple evidence types: system-generated and human-attributed.

**Verified:**
- System evidence immutable (`system_evidence_is_immutable` returns True)
- Human evidence actor-attributed (added_by set)
- Review detail exposes all evidence
- Cross-tenant evidence rejected
- Evidence persists after exception resolution

### J — Concurrent Acknowledgement
**Test:** `test_m3d_scenario_j_concurrent_ack`

Two actors attempt OPEN → ACKNOWLEDGED.

**Verified:**
- First transition succeeds
- Second raises ValueError ("Invalid transition")
- Only one ACKNOWLEDGE action in history

### K — Concurrent Decision
**Test:** `test_m3d_scenario_k_concurrent_decision`

Approve then reject attempt on same PENDING decision.

**Verified:**
- First decision (APPROVE) wins
- Second (REJECT) raises `InvalidDecisionTransition`
- Decision never has contradictory APPROVED + REJECTED

### L — Duplicate / Retry Idempotency
**Test:** `test_m3d_scenario_l_idempotency`

Replay: exception creation, evidence, decision request, duplicate approval.

**Verified:**
- Same RuleEvaluation → same exception (idempotent)
- Same evidence → same record (idempotent)
- Same decision type → same PENDING (idempotent)
- Duplicate approval fails (`InvalidDecisionTransition`)
- Independent human notes NOT incorrectly deduplicated

### M — Full Timeline
**Tests:** `test_m3d_scenario_m_full_timeline`, `test_m3d_scenario_m_decision_history_chain`

Complete chronological timeline for equipment substitution case.

**Verified:**
- Timeline entries chronologically ordered
- All action types present: ACKNOWLEDGE, REVIEW_NOTE, ADD_EVIDENCE, RESOLVE
- Timestamps, actors, source references, previous/new statuses all present
- Decision audit trail: REQUEST + APPROVE
- Decision history chain independently verifiable

### N — Technical Tenant Isolation
**Tests:** `test_m3d_scenario_n_cross_tenant_read`, `test_m3d_scenario_n_cross_tenant_modifications`, `test_m3d_scenario_n_cross_tenant_decision`, `test_m3d_scenario_n_queue_and_decisions_isolated`

Metro vs CLIENT_B isolation across all M3 operations.

**Verified:**
- Metro cannot read CLIENT_B exception
- Metro cannot acknowledge CLIENT_B exception
- Metro cannot add note to CLIENT_B case
- Metro cannot attach evidence to CLIENT_B case
- Metro cannot assign reviewer to CLIENT_B case
- Metro cannot request decision on CLIENT_B exception
- Review queue returns only Metro cases
- Cross-tenant decision list returns empty

### O — TBC-Safe Decision
**Test:** `test_m3d_scenario_o_tbc_safe`

Decision with nonexistent authorization policy.

**Verified:**
- `AuthorizationBlocked` raised
- Decision remains PENDING
- No auto-approval
- No fabricated approver

---

## 4. Invariants

### Approval ≠ Resolution
**Test:** `test_m3d_invariant_approval_ne_resolution`

APPROVED decision leaves exception ACKNOWLEDGED until explicit resolve. Separate RESOLVE action created.

### Rejection ≠ Resolution
**Test:** `test_m3d_invariant_rejection_ne_resolution`

REJECTED decision leaves exception OPEN. Discrepancy preserved. Exception not deleted.

### Waiver ≠ Data Deletion
**Test:** `test_m3d_invariant_waiver_ne_deletion`

WAIVED exception retains: source RuleEvaluation, all history, all evidence.

### Planned / Actual / Decision Separation
**Test:** `test_m3d_invariant_planned_actual_decision_separation`

Three independent facts: PLAN (ex25), ACTUAL (ex31), DECISION (APPROVED). Each stored in separate DB rows. Never collapsed.

### No Payroll Consequence
**Test:** `test_m3d_no_payroll_consequence`

No payroll, disciplinary, or HSE tables populated. Only expected M3 tables have data.

### Full Audit Trace
**Test:** `test_m3d_full_audit_trace`

Complete chain: Discrepancy → Exception → Evidence → Acknowledge → Human Evidence → Decision → Approve → Resolve. All actions have: id, tenant_id, exception_id, action_type, actor_user_id, action_timestamp, previous_status, new_status. Status transitions form valid chain.

### Source Immutability
**Test:** `test_m3d_source_immutability`

Source objects (RuleEvaluation, EquipmentDiscrepancy) never modified by M3 lifecycle operations.

### Evidence Persistence
**Test:** `test_m3d_evidence_persistence`

Evidence survives exception resolution/waiver. All evidence records still queryable after lifecycle completion.

---

## 5. Authorization Boundary

| Scenario | Policy | Expected |
|---|---|---|
| Valid SIM_APPROVER | `SIM_APPROVER` | APPROVED |
| No policy | `None` | `AuthorizationBlocked` |
| Nonexistent policy | `NONEXISTENT_METRO_POLICY` | `AuthorizationBlocked` |

Production Metro authorization remains **TBC** until Metro confirms authority configuration.

---

## 6. Remaining TBC

All 9 TBC items from `TBC_REGISTER.md` remain unresolved. M3D did not invent any authority, policy, or operational parameter to resolve them. TBC-safe behavior verified by Scenario O.

---

## 7. Remaining Defects

None. DEFECT-001 discovered and fixed within M3D scope.

---

## 8. Known Limitations

1. SQLite in-memory tests — true concurrent write testing not possible. Sequential determinism verified instead.
2. SIMULATION authorization (`SIM_APPROVER`, `SIM_OVERRIDE`) used for test approvals — clearly separated from production.
3. No production Metro authorization policy exists — this is by design (TBC-safe).

---

## 9. Files Changed

| File | Change |
|---|---|
| `backend/app/decision_engine.py` | +7 lines: authorization blocking in `approve_decision` |
| `backend/tests/test_acceptance_m3c.py` | Updated M3C-19 to match correct behavior |
| `backend/tests/test_acceptance_m3d.py` | 1843 | NEW: 32 integration tests |
| `docs/METRO_MM_M3D_ACCEPTANCE_REPORT.md` | This report |

No migration needed.
