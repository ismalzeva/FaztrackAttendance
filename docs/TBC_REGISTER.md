# TBC Register — Metro Mining MM-M0/M1

**Last Updated:** 2026-08-21
**Owner:** Project Owner (Ismal)

---

## DECIDED BY PROJECT OWNER

| # | Item | Decision | Date | Evidence |
|---|------|----------|------|----------|
| D1 | Tenant timezone | Metro Mining = **Asia/Makassar / WITA / UTC+08:00** | 2026-08-21 | Tenant.timezone, Site.timezone, test_timezone_configuration |
| D2 | Lumin Park timezone | Lumin Park = **Asia/Jakarta / WIB / UTC+07:00** | 2026-08-21 | Existing config, test_lumin_park_timezone_jakarta |
| D3 | Architecture rule | Timezone = tenant/site configurable, NOT hard-coded global | 2026-08-21 | No `if tenant == Metro` in core engine |

---

## STILL TBC — AWAITING PROJECT OWNER DECISION

| # | Item | Current Status | Impact | Notes |
|---|------|----------------|--------|-------|
| T1 | Pickup time | TBC | Roster scheduling | No production-semantic default |
| T2 | Briefing opening/tolerance | TBC | Checkpoint window | — |
| T3 | Geofence latitude | TBC (nullable) | Checkpoint validation | Site.latitude = NULL |
| T4 | Geofence longitude | TBC (nullable) | Checkpoint validation | Site.longitude = NULL |
| T5 | Geofence radius_m | TBC (nullable) | Checkpoint validation | Site.radius_m = NULL |
| T6 | Minimum rest hours | TBC | Roster validator | — |
| T7 | 12-day counting definition | TBC | Consecutive work streak | Calendar days vs shift days |
| T8 | 12-week counting definition | TBC | Onsite/offsite cycle | Calendar weeks vs shift weeks |
| T9 | Streak reset after REST | TBC | Consecutive work counter | Does REST day 13 reset counter? |
| T10 | Handover window 18:45 final meaning | TBC | Shift transition | Hard cutoff or soft warning? |
| T11 | Payroll consequence | BLOCKED_POLICY_DECISION | Exception handling | — |
| T12 | Disciplinary consequence | BLOCKED_POLICY_DECISION | Exception handling | — |
| T13 | HSE consequence | BLOCKED_POLICY_DECISION | Exception handling | — |
| T14 | Authorization/override details | TBC | Override workflow | Who can authorize, limits |
| T15 | Mess coordinates | TBC (SIMULATION) | Geofence | Dummy values in seed |
| T16 | Briefing point coordinates | TBC (SIMULATION) | Geofence | Dummy values in seed |
| T17 | Operating area coordinates | TBC (SIMULATION) | Geofence | Dummy values in seed |

---

## NOTES

- Geofence fields (latitude, longitude, radius_m) are **nullable** in schema. Seed values are labeled `[SIMULATION/NON_PRODUCTION]`.
- Rule/checkpoint that requires geofence is **NOT production-ready** until geofence data is provided.
- All TBC items require explicit Project Owner decision before M1 FINAL ACCEPTED.
