# TBC Register — Metro Mining MM-M0/M1

**Last Updated:** 2026-08-27
**Owner:** Project Owner (Ismal)

---

## DECIDED BY PROJECT OWNER

| # | Item | Decision | Date | Evidence |
|---|------|----------|------|----------|
| D1 | Tenant timezone | Metro Mining = **Asia/Makassar / WITA / UTC+08:00** | 2026-08-21 | Tenant.timezone, Site.timezone, test_timezone_configuration |
| D2 | Lumin Park timezone | Lumin Park = **Asia/Jakarta / WIB / UTC+07:00** | 2026-08-21 | Existing config, test_lumin_park_timezone_jakarta |
| D3 | Architecture rule | Timezone = tenant/site configurable, NOT hard-coded global | 2026-08-21 | No `if tenant == Metro` in core engine |
| D4 | Minimum rest hours | = **1 jam (60 menit)** antar rotasi shift | 2026-08-27 | Owner decision |
| D5 | 12-day counting definition | **Calendar days**. Hari libur kalender TETAP dihitung bila memang masuk jadwal kerja dia (tidak di-skip) | 2026-08-27 | Owner decision |
| D6 | 12-week counting definition | **Roster week** = 7 hari roster kontinu, TIDAK mengikuti hari libur kalender | 2026-08-27 | Owner decision |
| D7 | Handover 18:45 meaning | **Warning** (soft). Jangan sebelum 18:45 → sebelum 18:45 = EARLY_HANDOVER | 2026-08-27 | Owner decision |
| D8 | Streak reset after REST | **Libur 1 hari = reset counter** (streak balik ke 0 setelah 1 hari REST) | 2026-08-27 | Owner decision |

---

## IMPLEMENTATION STATUS (D4–D8 → code)

| Decision | Implemented in | Detail |
|----------|----------------|--------|
| D4 min rest 1h | `seed_metro_standalone.py` | policy key `minimum_rest_hours = "1"` (CONFIRMED) |
| D5 calendar-day count | `roster_generator.py` | 12-day streak & 7-day shift rotation count calendar days within the on-site window |
| D6 roster week (non-holiday) | `roster_generator.py` | cycle = `(onsite_weeks + offsite_weeks) * 7` continuous roster days |
| D8 REST resets counter | `roster_generator.py` | 12 WORK → day-13 REST → counter resets to 0 (WORK again on day 14) |
| Policy-driven limits | `roster_validator.py` | V1–V4 now read `max_consecutive_workdays`, `max_same_shift_streak`, `onsite_weeks`, `offsite_weeks` from `RosterPolicy` (CONFIRMED only, fallback 12/7/12/2/1) |

### New roster policy keys (seed idempoten)

| Key | Value | Status | Meaning |
|-----|-------|--------|---------|
| `max_consecutive_workdays` | 12 | CONFIRMED | max 12 hari kerja nonstop |
| `mandatory_rest_days` | 1 | CONFIRMED | libur wajib (reset counter) |
| `max_same_shift_streak` | 7 | CONFIRMED | max 7 hari shift sama |
| `onsite_weeks` | 12 | CONFIRMED | 12 minggu on-site |
| `offsite_weeks` | 2 | CONFIRMED | 2 minggu off-site |
| `minimum_rest_hours` | 1 | CONFIRMED | D4 (sebelumnya TBC) |

### Design assumption (perlu konfirmasi Owner bila menyimpang)

- **Rotasi shift** dihitung per **hari kalender** (blok 7 DAY → 7 NIGHT), konsisten dengan D5/D6.
  Artinya hari REST bisa jatuh di tengah blok NIGHT (mis. 5 NIGHT → REST → 1 NIGHT), tetap ≤ 7 shift sama berturut-turut.
  Bila Owner mau shift dihitung per **hari kerja** (skip REST), generator cukup diubah satu baris (`shift_cycle`).



---

## STILL TBC — AWAITING PROJECT OWNER DECISION

| # | Item | Current Status | Impact | Notes |
|---|------|----------------|--------|-------|
| T1 | Pickup time | TBC | Roster scheduling | No production-semantic default |
| T2 | Briefing opening/tolerance | TBC | Checkpoint window | — |
| T3 | Geofence latitude | TBC (nullable) | Checkpoint validation | Site.latitude = NULL |
| T4 | Geofence longitude | TBC (nullable) | Checkpoint validation | Site.longitude = NULL |
| T5 | Geofence radius_m | TBC (nullable) | Checkpoint validation | Site.radius_m = NULL |
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
