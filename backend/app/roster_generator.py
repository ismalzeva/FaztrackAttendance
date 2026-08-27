"""
roster_generator.py — Deterministic Metro Mining roster generator.

Generates per-worker roster plans directly from the operating rules
(Decision register D4–D8, docs/TBC_REGISTER.md):

  - Onsite/offsite cycle: `onsite_weeks` ON + `offsite_weeks` OFF (roster weeks).
  - Within ON: `max_consecutive_workdays` WORK days then `mandatory_rest_days`
    REST day(s); a REST day resets the consecutive-work counter (D8).
  - Within ON: shift rotates every `max_same_shift_streak` calendar days
    (DAY → NIGHT → DAY …).

All rules are calendar-day based (D5 "12 hari = kalender", D6 "roster week"
tidak mengikuti hari libur): the 12-day streak and the 7-day shift rotation
count calendar days within the on-site window, not "worked" days only.

The cycle is computed CLOSED-FORM from a global anchor + a per-worker offset,
so any operating date can be resolved independently of the generation start
date. This is what makes "output = persis input": the same anchor + offset
always yields the same plan for a given date.

The generator is pure (no DB writes). `read_roster_policy` loads the configured
values from the `roster_policies` table and falls back to the documented
defaults when a key is absent, unconfirmed, or unparseable.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import RosterPolicy


# Policy key names (must match docs/TBC_REGISTER.md + seed_metro_standalone.py).
DEFAULT_POLICY: dict[str, int] = {
    "onsite_weeks": 12,
    "offsite_weeks": 2,
    "max_consecutive_workdays": 12,
    "mandatory_rest_days": 1,
    "max_same_shift_streak": 7,
    "minimum_rest_hours": 1,
}

_INT_KEYS = frozenset(DEFAULT_POLICY)


def read_roster_policy(db: Session, tenant_id: str) -> dict[str, int]:
    """Load roster-policy integers for a tenant.

    Only CONFIRMED keys override the documented defaults; anything TBC / missing
    / unparseable falls back so the system never silently breaks on a partial
    configuration.
    """
    cfg: dict[str, int] = dict(DEFAULT_POLICY)
    rows = db.query(RosterPolicy).filter(RosterPolicy.tenant_id == tenant_id).all()
    for r in rows:
        if r.policy_key not in _INT_KEYS:
            continue
        if r.confirmation_status != "CONFIRMED":
            continue
        try:
            cfg[r.policy_key] = int(r.policy_value)
        except (TypeError, ValueError):
            continue
    return cfg


def compute_cycle_pos(
    anchor: date, offset_days: int, operating_date: date, cycle_length: int,
) -> int:
    """0-based position within the cycle for a worker on the given date."""
    return ((operating_date - anchor).days + offset_days) % cycle_length


def generate_assignments(
    anchor: date,
    offset_days: int,
    start: date,
    end: date,
    *,
    onsite_weeks: int = 12,
    offsite_weeks: int = 2,
    max_consecutive_workdays: int = 12,
    mandatory_rest_days: int = 1,
    max_same_shift_streak: int = 7,
) -> list[dict]:
    """Generate roster plans for [start, end] inclusive.

    Returns a list of dicts, one per calendar day:
      {
        "date": date,
        "site_status":  "ONSITE" | "OFFSITE",
        "work_status":  "WORK" | "REST" | "OFFSITE",
        "shift_key":    "DAY" | "NIGHT" | None,
        "site_cycle_day": int,   # 1-based position within the full cycle
      }

    Rules encoded:
      * cycle = (onsite_weeks + offsite_weeks) * 7 days.
      * cycle_pos >= onsite_weeks*7  →  OFFSITE.
      * else work block = max_consecutive_workdays + mandatory_rest_days;
        the trailing mandatory_rest_days positions are REST, the rest WORK.
      * shift rotates every max_same_shift_streak calendar days (DAY first).
    """
    onsite_days = onsite_weeks * 7
    cycle_length = (onsite_weeks + offsite_weeks) * 7
    block_length = max_consecutive_workdays + mandatory_rest_days
    shift_cycle = max_same_shift_streak * 2

    plans: list[dict] = []
    d = start
    while d <= end:
        cp = compute_cycle_pos(anchor, offset_days, d, cycle_length)
        site_cycle_day = cp + 1

        if cp >= onsite_days:
            plans.append({
                "date": d,
                "site_status": "OFFSITE",
                "work_status": "OFFSITE",
                "shift_key": None,
                "site_cycle_day": site_cycle_day,
            })
        else:
            pos = cp % block_length
            if pos >= max_consecutive_workdays:
                # mandatory REST — resets the consecutive-work counter (D8)
                plans.append({
                    "date": d,
                    "site_status": "ONSITE",
                    "work_status": "REST",
                    "shift_key": None,
                    "site_cycle_day": site_cycle_day,
                })
            else:
                spos = cp % shift_cycle
                shift_key = "NIGHT" if spos >= max_same_shift_streak else "DAY"
                plans.append({
                    "date": d,
                    "site_status": "ONSITE",
                    "work_status": "WORK",
                    "shift_key": shift_key,
                    "site_cycle_day": site_cycle_day,
                })

        d += timedelta(days=1)

    return plans
