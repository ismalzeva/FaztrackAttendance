# METRO MM-M0/M1 ACCEPTANCE REPORT

**Project:** Faztrack Attendance — Metro Mining Tenant
**Report Date:** 2026-08-21
**Status:** CONDITIONAL PASS (pending remaining TBC decisions)
**Author:** Hermes (Personal Assistant Ismal)

---

## 1. Scope

MM-M0 (audit, tenant config, schema, seed import) + MM-M1 (roster, assignment, validations) for Metro Mining as a reusable tenant of Faztrack Attendance.

---

## 2. Schema Changes

### New Models (14 tables)
| Model | Purpose |
|-------|---------|
| RosterPolicy | Tenant-specific config (key-value) |
| Site | Mining sites with geofence |
| ShiftTemplate | DAY/NIGHT shift definitions |
| CheckpointPolicy | Event validation rules |
| Role | Equipment operator roles |
| Crew | Crew groupings with cycle anchor |
| Competency | Employee certification history (multi-record) |
| Equipment | Equipment fleet with effective dates |
| EmployeeMeta | Worker operational metadata (effective-dated) |
| RuleVersion | Immutable rule snapshots |
| RosterAssignment | Daily roster with RuleVersion FK |

### Schema Hardening (commit `0a9c48b007af`)
| Change | Before | After |
|--------|--------|-------|
| EmployeeMeta.worker_id | UNIQUE (1:1) | INDEX + UNIQUE on (tenant_id, worker_id, effective_from) |
| Competency (tenant, emp, type) | UNIQUE (1:1) | INDEX only (allows multiple records) |
| Site.radius_m | NOT NULL, default 150 | NULLABLE (TBC until geofence data) |
| RosterAssignment.rule_version_id | — | FK → rule_versions.id |

---

## 3. Migration Evidence

```
$ alembic upgrade head
→ 7d30eba67c93 → 0a9c48b007af PASS

$ alembic downgrade -1
→ 0a9c48b007af → 7d30eba67c93 PASS

$ alembic upgrade head
→ 7d30eba67c93 → 0a9c48b007af PASS
```

Migration `0a9c48b007af` uses `batch_alter_table` for SQLite compatibility. Existing tenant (Lumin Park) not affected.

---

## 4. Timezone Decision

**DECIDED BY PROJECT OWNER (2026-08-21):**

| Tenant | Timezone | TZ Name | UTC Offset |
|--------|----------|---------|------------|
| Metro Mining | Asia/Makassar | WITA | UTC+08:00 |
| Lumin Park | Asia/Jakarta | WIB | UTC+07:00 |

**Architecture:** Timezone is tenant/site configurable. Core engine has NO hard-coded `if tenant == Metro` logic.

**Evidence:**
- `Tenant.timezone = "Asia/Makassar"` for Metro Mining
- `Site.timezone = "Asia/Makassar"` for all Metro sites
- Tests: `test_metro_mining_timezone_makassar`, `test_lumin_park_timezone_jakarta`, `test_timezone_tenant_isolation`, `test_metro_local_to_utc_conversion`, `test_lumin_local_to_utc_conversion`

---

## 5. Test Results

```
91/91 PASS, 0 FAIL

tests/test_acceptance_hardening.py  30 passed (NEW — hardening)
tests/test_acceptance_metro.py      40 passed (existing Metro)
tests/test_attendance.py             4 passed (existing)
tests/test_auth_tenant.py            6 passed (existing)
tests/test_device_enrollment.py      4 passed (existing)
tests/test_master_data_import.py     3 passed (existing)
tests/test_timesheets.py             4 passed (existing)
```

### Hardening Tests (30)
| Category | Tests | Coverage |
|----------|-------|----------|
| EmployeeMeta effective dating | 4 | Multiple periods, immutability, same-date rejection, different workers |
| Competency history | 4 | Renewal, expiry, suspension, different cert numbers |
| Timezone configuration | 6 | Metro=Makassar, Lumin=Jakarta, site inheritance, isolation, UTC conversion |
| Geofence TBC | 3 | Nullable radius, nullable lat/lon, production readiness flag |
| RuleVersion FK | 3 | Link to V1, historical preserved after V2, active version change |
| Tenant isolation | 7 | Employee, crew, equipment, competency, roster, rule version, site |
| NIGHT cross-midnight | 3 | Crosses midnight flag, operating_date from origin, WITA cross-midnight |

---

## 6. Tenant Isolation Evidence

All Metro Mining data is strictly isolated via `tenant_id`:
- Workers, crews, equipment, competencies, rosters, rule versions, sites — all filtered by `tenant_id`
- No cross-tenant queries in core engine
- Tests verify: Metro employee not visible to Lumin, Lumin employee not visible to Metro

---

## 7. Lumin Park Regression Status

**NO REGRESSION.** Existing 21 tests (attendance, auth, device, master data, timesheets) all pass. Lumin Park data untouched by Metro Mining migration.

---

## 8. Geofence Configuration

| Field | Status | Value |
|-------|--------|-------|
| Site.latitude | TBC (nullable) | NULL |
| Site.longitude | TBC (nullable) | NULL |
| Site.radius_m | TBC (nullable) | NULL |

Seed values labeled `[SIMULATION/NON_PRODUCTION]`. Geofence-dependent checkpoints are NOT production-ready.

---

## 9. Remaining Defects

None. All identified issues fixed.

---

## 10. Remaining TBC Items

See `docs/TBC_REGISTER.md` for full list. Summary:
- **3 DECIDED** (timezone × 3)
- **17 STILL TBC** (pickup, briefing, geofence coordinates, rest hours, streak semantics, handover, consequences, overrides)

---

## 11. Commits

| Commit | Message |
|--------|---------|
| `1042a81` | feat(metro-m0-m1): Metro Mining tenant models, migration, seed, roster validator, 25 acceptance tests |
| `77267ea` | fix(metro-m0-m1): fix migration to only create new tables + fix 4 acceptance tests |
| `daca575` | docs(metro-m0-m1): add acceptance report |
| *(pending)* | hardening: effective dating, timezone, geofence TBC, RuleVersion FK, 30 new tests |

---

## 12. Acceptance Verdict

**CONDITIONAL PASS** — MM-M0/M1 is provisionally accepted pending:
1. Remaining TBC decisions from Project Owner
2. Geofence production data
3. Final push of hardening commit

**M2 is BLOCKED** until all TBC items resolved and FINAL PASS granted.
