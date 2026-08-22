# MM-M4D Acceptance Report — Reports & Export

**Date:** 2026-08-22
**Milestone:** MM-M4D — Reports & Export
**Status:** ✅ FINAL PASS

---

## 1. Scope

Read-only reporting layer over existing M0–M4C data. No new tables. No new business rules.

### Deliverables
| # | Deliverable | Lines | Status |
|---|-------------|-------|--------|
| 1 | `app/report_service.py` — 3 report families + CSV/XLSX export | 1528 | ✅ |
| 2 | `app/reports.py` — API router (6 endpoints) | 298 | ✅ |
| 3 | Router registered in `main.py` | — | ✅ |
| 4 | `tests/test_acceptance_m4d.py` — 100 test scenarios | 2343 | ✅ |

---

## 2. Architecture

### Report Families
1. **Shift Attendance** — per-employee per-shift: checkpoint timestamps, operational state, planned equipment, exception counts
2. **Exception & Decision** — exception lifecycle (OPEN→ACKNOWLEDGED→RESOLVED/WAIVED) + decision status (PENDING→APPROVED/REJECTED), plan/actual/decision separation preserved
3. **Roster vs Actual** — planned vs actual equipment, comparison result (MATCH/MISMATCH), operator substitution, linked decisions

### Export Formats
- **CSV** — UTF-8 BOM, formula injection protection (`'=+`-@` prefix → apostrophe), deterministic column ordering
- **XLSX** — openpyxl 3.1.5, freeze panes (A2), Report_Info metadata sheet, formula injection protection, date/number cell types

### API Endpoints
| Method | Path | Format |
|--------|------|--------|
| GET | `/api/v1/reports/shift-attendance` | JSON |
| GET | `/api/v1/reports/shift-attendance/export?format=csv\|xlsx` | CSV/XLSX |
| GET | `/api/v1/reports/exceptions` | JSON |
| GET | `/api/v1/reports/exceptions/export?format=csv\|xlsx` | CSV/XLSX |
| GET | `/api/v1/reports/roster-vs-actual` | JSON |
| GET | `/api/v1/reports/roster-vs-actual/export?format=csv\|xlsx` | CSV/XLSX |

### Filters
Date (single/range), site, shift, crew, role, employee, equipment, work_status, exception_type, severity, exception_status, decision_status.

---

## 3. Test Results

### M4D Tests: 100/100 PASS

| Category | Tests | Status |
|----------|-------|--------|
| Report A — Shift Attendance | 18 | ✅ |
| Report B — Exception & Decision | 18 | ✅ |
| Report C — Roster vs Actual | 14 | ✅ |
| Filters | 14 | ✅ |
| Export — CSV | 8 | ✅ |
| Export — XLSX | 10 | ✅ |
| Metadata & Filename | 4 | ✅ |
| Isolation (tenant/site) | 4 | ✅ |
| Safety (empty/payroll/TBC/bounded) | 6 | ✅ |
| Regression (M4A/M4B/M4C) | 4 | ✅ |

### Full Regression: 718/718 PASS

| Suite | Tests | Status |
|-------|-------|--------|
| M0–M3 (hardening, m2a–m2e, m3a–m3d) | 370 | ✅ |
| M4A (dashboard) | 57 | ✅ |
| M4B (roster) | 59 | ✅ |
| M4C (exceptions) | 90 | ✅ |
| **M4D (reports)** | **100** | **✅** |
| Metro seed + misc | 42 | ✅ |
| **Total** | **718** | **✅** |

---

## 4. Key Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | No new tables | Pure read-only over existing M0–M4C data |
| 2 | `asdict()` for metadata | `export_xlsx` normalizes dataclass to dict at entry for consistent `.get()` access |
| 3 | `work_status == WORK` filter | Roster vs Actual excludes REST/OFFSITE employees (no equipment assignments) |
| 4 | `aliased(Equipment)` for actual | Avoids ambiguous column reference in roster_vs_actual join |
| 5 | Formula injection protection | Apostrophe prefix for cells starting with `=`, `+`, `-`, `@`, `\t`, `\r` |
| 6 | BOM in CSV | Excel compatibility for UTF-8 Indonesian characters |
| 7 | Join chain: disc→exc→dec | `disc_sq.disc_id` → `exc_for_disc_sq.exc_id` → `latest_dec_sq` → `ExceptionDecision` |

---

## 5. Files Changed

| File | Action | Lines |
|------|--------|-------|
| `backend/app/report_service.py` | **Created** | 1528 |
| `backend/app/reports.py` | **Created** | 298 |
| `backend/app/main.py` | **Modified** (+2 lines) | — |
| `backend/tests/test_acceptance_m4d.py` | **Created** | 2343 |
| `docs/METRO_MM_M4D_ACCEPTANCE_REPORT.md` | **Created** | this file |

---

## 6. Verdict

**MM-M4D: FINAL PASS ✅**

- 100/100 M4D acceptance tests PASS
- 718/718 full regression PASS (zero regressions)
- All 3 report families functional
- CSV + XLSX export with formula injection protection
- Tenant isolation verified
- Plan/actual/decision separation preserved
- No new tables, no business logic duplication

**Ready for commit.**
