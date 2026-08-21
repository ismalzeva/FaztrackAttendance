# FAZTRACK ATTENDANCE MULTI-COMPANY ARCHITECTURE

## Permanent Architectural Decision

**Effective:** 2026-08-21 (MM-M2C, reinforced MM-M2E)
**Status:** PERMANENT — applies to all current and future Faztrack Attendance development

---

## 1. Core Principle

```
FAZTRACK ATTENDANCE IS A MULTI-CLIENT PLATFORM.
THE ENGINE IS SHARED.
BUSINESS RULES ARE CLIENT-SPECIFIC.
```

Each client is an isolated configuration and data cluster. No business-rule inheritance between clients unless explicitly configured. No cross-client fallback. No assumption that companies in the same industry share attendance rules. Client requirements are authoritative for that client's configured behavior, subject to technical integrity and approved safety/security constraints.

---

## 2. Client-Cluster Architecture

```
FAZTRACK ATTENDANCE CORE PLATFORM
│
├── CLIENT CLUSTER A
│   ├── own master data
│   ├── own configuration
│   ├── own attendance rules
│   ├── own shift rules
│   ├── own roster rules
│   ├── own checkpoint rules
│   ├── own equipment rules
│   ├── own competency rules
│   ├── own geofence rules
│   ├── own approval rules
│   ├── own input mappings
│   ├── own rule versions
│   └── own operational data
│
├── CLIENT CLUSTER B
│   └── completely independent business configuration
│
└── FUTURE CLIENT CLUSTERS
    └── each independently configured
```

The platform provides reusable technical capabilities. Each client provides its own operational rulebook. **Do NOT assume that attendance rules should be standardized across clients.**

### 2.1 Shared Core (Technical Primitives)

The shared core provides generic technical capabilities:
- Tenant isolation
- Authentication
- Event ingestion
- Canonical event storage
- Timezone-aware timestamps
- Operating date resolution
- Rule execution framework
- Checkpoint framework
- Equipment assignment framework
- Audit trail
- Idempotency
- Effective dating
- Configuration engine

### 2.2 Client-Specific (Business Rules)

Each client configures its own:
- Attendance rules (tolerance, grace periods)
- Shift rules (timing, breaks, handovers)
- Roster rules (max consecutive days, streak limits)
- Checkpoint rules (which checkpoints required, windows)
- Equipment rules (assignment policies, competency requirements)
- Geofence rules (coordinates, radius)
- Approval rules (who can override, authorization levels)
- Consequence rules (payroll, disciplinary, HSE)

**No core change should be required when a new client configures different values.**

---

## 3. Client-Cluster Boundary

Each client cluster must independently own:

| Category | Examples |
|----------|----------|
| Tenant configuration | timezone, geofence, shift/schedule |
| Master data | employees, roles, crews, equipment, sites/locations |
| Employee data | worker records, employment status |
| Role data | job roles, skill requirements |
| Crew data | team assignments, crew composition |
| Equipment data | equipment catalog, status, maintenance |
| Competency data | certifications, validity dates, equipment types |
| Site/location data | site boundaries, timezone, geofence |
| Timezone | site-specific timezone resolution |
| Geofence | coordinates, radius, inclusion/exclusion zones |
| Shift templates | timing, breaks, handovers |
| Roster policies | max days, streak limits, cycle patterns |
| Site-cycle policies | onsite/offsite duration |
| Checkpoint policies | required checkpoints, windows, tolerance |
| Attendance policies | late tolerance, early tolerance |
| Rule versions | versioned configuration snapshots |
| Capabilities | feature flags per tenant |
| Source/input mappings | canonical input source mappings |
| Canonical enrichment mappings | event → roster → equipment resolution |
| Seed/simulation data | test data, pilot data |
| Operational data | roster, actual assignments, checkpoint results, discrepancies |
| Exception data | discrepancy candidates, resolution status |
| Acceptance tests | tenant-specific test suites |

**No cross-client fallback is allowed.**

---

## 4. No Cross-Client Fallback

If a client's configuration is missing:

**DO NOT:**
- Read another tenant's configuration
- Copy another client's default
- Infer from another client's data
- Use another company's policy because it exists
- Silently apply a global business default

**Instead:**
- `CONFIG_INCOMPLETE` — when required configuration is missing
- `BLOCKED_POLICY_DECISION` — when human decision is needed

---

## 5. Same Business Code Across Clients

Technical isolation must support identical business codes across clients:

```
Metro Mining:   employee_no = OP-001
Client B:       employee_no = OP-001

Metro Mining:   equipment_code = EQ-001
Client B:       equipment_code = EQ-001
```

These identical business codes must remain independently resolvable because tenant context is different. Internal PKs must remain unique according to schema design.

---

## 6. No Generalization of Client Rules

**Never conclude:**
- "Metro uses 12 days, therefore Faztrack Attendance uses 12 days."

**Correct:**
- "Metro Mining's current rule version uses 12 days."

**Never conclude:**
- "Mining companies use 12 weeks onsite / 2 weeks offsite."

**Correct:**
- "Metro Mining currently provided a 12-week onsite / 2-week offsite policy."

Even another mining company may have completely different rules.

---

Each company must have its own:

| Category | Examples |
|----------|----------|
| Configuration | timezone, geofence, shift/schedule |
| Master Data | employees, roles, crews, equipment, sites/locations |
| Competency Data | certifications, validity dates, equipment types |
| Policies | roster policies, checkpoint policies |
| Capabilities | equipment_assignment_enabled, competency_validation_enabled |
| Rule Versions | versioned configuration snapshots |
| Input Mappings | canonical input source mappings |
| Seed/Simulation Data | test data, pilot data |
| Operational Data | roster, actual assignments, checkpoint results, discrepancies |
| Tests | acceptance tests for tenant-specific behavior |

---

## 4. Core Must Remain Generic

**PROHIBITED:**
```python
if tenant == "Metro Mining":
    ...
if tenant == "Lumin Park":
    ...
```

**REQUIRED:**
```python
# Core reads from tenant context
capability = get_tenant_capability(db, tenant_id=tenant_id, key="competency_validation_enabled")
if capability:
    validate_competency(...)
```

Client behavior must come from:
1. Tenant configuration
2. Tenant capability
3. Tenant policy
4. Tenant master data

**Never from tenant name or code.**

---

## 5. Processing Pipeline

```
Core Engine
    ↓
Tenant Context (explicit, never inferred)
    ↓
Tenant Capability
    ↓
Tenant Policy
    ↓
Tenant Master Data
    ↓
Validation
```

**Not:**
```
Core Engine
    ↓
Check company name
    ↓
Special-case logic
```

---

## 6. Data Isolation Rules

### 6.1 Query Scoping

All database queries MUST be scoped by `tenant_id`. No query may return data from another tenant.

### 6.2 ID Uniqueness

A database ID from another tenant must never be accepted merely because the record exists. Equipment code "EX-025" in Metro Mining is completely independent from "EX-025" in Lumin Park.

### 6.3 Cross-Tenant Rejection

Explicitly reject:
- Metro worker → Lumin equipment
- Lumin worker → Metro equipment
- Metro actual → Lumin planned
- Metro competency → Lumin worker
- Lumin competency → Metro worker

---

## 7. Timezone Isolation

Each tenant resolves its own timezone from tenant configuration.

**Current:**
- Metro Mining: `Asia/Makassar` (WITA, UTC+08:00)
- Lumin Park: `Asia/Jakarta` (WIB, UTC+07:00)

**Rules:**
- Changing Metro timezone must not affect Lumin
- Changing Lumin timezone must not affect Metro
- Do not use one tenant as fallback for another
- Missing required tenant/site timezone → `CONFIG_INCOMPLETE` (future)
- Global `Asia/Jakarta` fallback may remain temporarily for backward compatibility

---

## 8. Capability Configuration

Tenant capabilities are stored in `RosterPolicy` (generic key-value per tenant).

**Current capabilities:**
- `equipment_assignment_enabled` (boolean)
- `competency_validation_enabled` (boolean)

**Behavior:**
- Capability disabled → feature returns NOT_APPLICABLE
- Capability enabled → feature validates normally
- Absence of records is NOT equivalent to a policy decision

**Future capabilities** can be added without schema changes.

---

## 9. Input/Master Data Isolation

Canonical input engine may remain shared. However:

```
Source
  → Tenant Context (explicit)
  → Tenant-specific Mapping
  → Canonical Model
```

**Do not infer company from:**
- Employee name
- Equipment code
- Filename
- Worksheet name

The canonical schema is reusable. The client mapping and master data are isolated.

---

## 10. Repository Structure

```
ONE CORE REPOSITORY
+
ISOLATED COMPANY/TENANT BOUNDARIES
```

Do NOT create separate repositories for Metro Mining or Lumin Park. All tenants share the same codebase with isolated data and configuration.

---

## 11. Testing Requirements

Each tenant should have acceptance tests that verify:
- Tenant-specific behavior works correctly
- Cross-tenant isolation is maintained
- Capability changes in one tenant don't affect another
- Timezone isolation is preserved

---

## 12. Summary

| Principle | Rule |
|-----------|------|
| Shared core | One codebase, reusable engine |
| Tenant isolation | All data/config scoped by tenant_id |
| No company branching | No `if tenant == "X"` in core |
| Explicit context | Tenant resolved through trusted ingestion context |
| Capability-driven | Behavior from tenant capability/policy, not name |
| No cross-tenant fallback | Each tenant stands alone |
| Timezone independence | Each tenant resolves own timezone |
| ID boundary | Same code in different tenants = no collision |
