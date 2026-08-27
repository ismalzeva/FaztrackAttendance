"""
Acceptance tests for the deterministic roster generator + policy-driven config.

Covers the four operating-rule decisions (D4–D8, docs/TBC_REGISTER.md):
  - D8: mandatory rest (1 day) resets the consecutive-work counter,
        12-on / 1-off pattern holds across the whole on-site block.
  - max_same_shift: shift rotates DAY → NIGHT every 7 calendar days.
  - on/off cycle: 12 weeks ON (84 days) then 2 weeks OFF (14 days).
  - D4: minimum_rest_hours surfaced through read_roster_policy.

All generator assertions are pure (no DB); policy assertions use the shared
`db` fixture to verify CONFIRMED override / TBC fallback semantics.
"""
from datetime import date, timedelta

from app.models import RosterPolicy
from app.roster_generator import (
    DEFAULT_POLICY,
    compute_cycle_pos,
    generate_assignments,
    read_roster_policy,
)

A = date(2026, 9, 1)  # cycle anchor


def _by_date(plans):
    return {p["date"]: p for p in plans}


# ── Generator: 12-on / 1-off (D8 reset counter) ──────────────

def test_generator_12_work_then_rest_then_reset():
    plans = generate_assignments(A, 0, A, A + timedelta(days=27))
    by = _by_date(plans)

    # days 1..12 (offset 0..11) = WORK
    for i in range(12):
        assert by[A + timedelta(days=i)]["work_status"] == "WORK"
        assert by[A + timedelta(days=i)]["site_status"] == "ONSITE"

    # day 13 = REST (mandatory), still ON-SITE
    assert by[A + timedelta(days=12)]["work_status"] == "REST"
    assert by[A + timedelta(days=12)]["site_status"] == "ONSITE"

    # day 14 = WORK again → REST day RESET the counter (D8)
    assert by[A + timedelta(days=13)]["work_status"] == "WORK"

    # second block: day 25 = REST (13*2 - 1 → offset 25)
    assert by[A + timedelta(days=25)]["work_status"] == "REST"


def test_generator_never_exceeds_12_consecutive_work():
    plans = generate_assignments(A, 0, A, A + timedelta(days=97))
    by = _by_date(plans)
    streak = 0
    max_streak = 0
    for i in range(98):
        d = A + timedelta(days=i)
        if by[d]["work_status"] == "WORK":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    assert max_streak == 12


# ── Generator: shift rotation every 7 days ──────────────────

def test_generator_shift_rotates_every_7_days():
    plans = generate_assignments(A, 0, A, A + timedelta(days=20))
    by = _by_date(plans)

    # days 1..7 → DAY
    for i in range(7):
        assert by[A + timedelta(days=i)]["shift_key"] == "DAY"
        assert by[A + timedelta(days=i)]["work_status"] == "WORK"

    # day 8 → NIGHT (7 days of DAY completed)
    assert by[A + timedelta(days=7)]["shift_key"] == "NIGHT"

    # day 13 → REST (no shift)
    assert by[A + timedelta(days=12)]["shift_key"] is None

    # day 14 → NIGHT (shift block continues: cp=13 → spos=13 → NIGHT)
    assert by[A + timedelta(days=13)]["shift_key"] == "NIGHT"


def test_generator_never_exceeds_7_consecutive_same_shift():
    plans = generate_assignments(A, 0, A, A + timedelta(days=97))
    by = _by_date(plans)
    streak = 0
    max_streak = 0
    cur = None
    for i in range(84):  # only on-site block may carry shifts
        d = A + timedelta(days=i)
        sh = by[d]["shift_key"]
        if sh is None:
            streak = 0
            cur = None
            continue
        if sh == cur:
            streak += 1
        else:
            cur = sh
            streak = 1
        max_streak = max(max_streak, streak)
    assert max_streak == 7


# ── Generator: 12-week-on / 2-week-off cycle ────────────────

def test_generator_84_onsite_14_offsite():
    plans = generate_assignments(A, 0, A, A + timedelta(days=97))
    by = _by_date(plans)
    onsite = [p for p in plans if p["site_status"] == "ONSITE"]
    offsite = [p for p in plans if p["site_status"] == "OFFSITE"]
    assert len(onsite) == 84
    assert len(offsite) == 14

    # first off-site day is cycle day 85 (site_cycle_day is 1-based)
    assert by[A + timedelta(days=84)]["site_status"] == "OFFSITE"
    assert by[A + timedelta(days=84)]["site_cycle_day"] == 85

    # last day of the 98-day cycle is still off-site
    assert by[A + timedelta(days=97)]["site_status"] == "OFFSITE"


def test_generator_offsite_never_has_work():
    plans = generate_assignments(A, 0, A, A + timedelta(days=97))
    for p in plans:
        if p["site_status"] == "OFFSITE":
            assert p["work_status"] == "OFFSITE"
            assert p["shift_key"] is None


# ── Generator: demo snapshot parity (same offsets as the seed) ─

def test_generator_reproduces_demo_snapshot():
    # Same offsets the seed uses → same states on the anchor day.
    d = generate_assignments(A, 0, A, A)[0]
    assert d["work_status"] == "WORK" and d["shift_key"] == "DAY"

    n = generate_assignments(A, 8, A, A)[0]
    assert n["work_status"] == "WORK" and n["shift_key"] == "NIGHT"

    r = generate_assignments(A, 12, A, A)[0]
    assert r["work_status"] == "REST" and r["site_status"] == "ONSITE"

    o = generate_assignments(A, 84, A, A)[0]
    assert o["work_status"] == "OFFSITE" and o["site_status"] == "OFFSITE"


def test_compute_cycle_pos_wraps():
    assert compute_cycle_pos(A, 0, A, 98) == 0
    assert compute_cycle_pos(A, 0, A + timedelta(days=1), 98) == 1
    assert compute_cycle_pos(A, 0, A + timedelta(days=98), 98) == 0
    assert compute_cycle_pos(A, 5, A, 98) == 5


# ── Policy reading (D4 minimum_rest_hours + config precedence) ─

def test_read_roster_policy_fallback_when_empty(db):
    assert read_roster_policy(db, "tenant-none") == dict(DEFAULT_POLICY)


def test_read_roster_policy_confirmed_overrides_default(db):
    db.add(RosterPolicy(
        id="rp-1", tenant_id="t1", policy_key="max_consecutive_workdays",
        policy_value="10", data_type="integer", confirmation_status="CONFIRMED",
    ))
    db.commit()
    cfg = read_roster_policy(db, "t1")
    assert cfg["max_consecutive_workdays"] == 10
    # untouched keys keep defaults
    assert cfg["max_same_shift_streak"] == 7
    assert cfg["minimum_rest_hours"] == 1


def test_read_roster_policy_ignores_tbc_and_unparseable(db):
    db.add_all([
        RosterPolicy(id="rp-a", tenant_id="t2", policy_key="onsite_weeks",
                     policy_value="50", data_type="integer",
                     confirmation_status="TBC"),           # TBC → ignored
        RosterPolicy(id="rp-b", tenant_id="t2", policy_key="offsite_weeks",
                     policy_value="not-an-int", data_type="integer",
                     confirmation_status="CONFIRMED"),     # unparseable → ignored
        RosterPolicy(id="rp-c", tenant_id="t2", policy_key="max_same_shift_streak",
                     policy_value="5", data_type="integer",
                     confirmation_status="CONFIRMED"),     # valid → applied
    ])
    db.commit()
    cfg = read_roster_policy(db, "t2")
    assert cfg["onsite_weeks"] == 12          # TBC fell back
    assert cfg["offsite_weeks"] == 2          # unparseable fell back
    assert cfg["max_same_shift_streak"] == 5  # CONFIRMED applied


def test_read_roster_policy_scoped_by_tenant(db):
    db.add(RosterPolicy(id="rp-x", tenant_id="tA", policy_key="onsite_weeks",
                        policy_value="9", data_type="integer",
                        confirmation_status="CONFIRMED"))
    db.commit()
    assert read_roster_policy(db, "tA")["onsite_weeks"] == 9
    assert read_roster_policy(db, "tB")["onsite_weeks"] == 12   # other tenant unaffected
