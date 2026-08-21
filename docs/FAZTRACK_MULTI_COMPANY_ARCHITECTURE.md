# FAZTRACK ATTENDANCE MULTI-COMPANY ARCHITECTURE

## Permanent Architectural Decision

**Effective:** 2026-08-21 (MM-M2C)
**Status:** PERMANENT — applies to all current and future Faztrack Attendance development

---

## 1. Core Principle

```
SHARED CORE ENGINE
+
STRICTLY ISOLATED COMPANY CONFIGURATION
```

One shared reusable core repository. Each client/company (tenant) has completely isolated configuration, master data, policies, capabilities, rules, operational data, and tests.

---

## 2. Tenant Boundary

Every Faztrack Attendance client/company is an independent tenant boundary.

**Current tenants:**
- Lumin Park
- Metro Mining

**Future tenants:**
- Client C
- Client D
- Any future client

**No tenant may silently inherit another tenant's operational configuration.**

---

## 3. What Each Tenant Owns

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
