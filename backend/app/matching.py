"""Suggest which planned session a run belongs to.

Runs do not always happen on the planned day: a Tuesday run may be done on
Wednesday. So the week comes from the run's date and, within that week, the
run goes to the free session whose distance and day fit it best. The
suggestion is only that: every run can be reassigned later.

    cost = |km - target| / target  +  DAY_WEIGHT * |run day - planned day|

A day of difference weighs as much as 15 % of the distance, so a 6 km run on
Wednesday goes to the 6 km Thursday session rather than the 5 km Tuesday one.
A run shorter than MIN_SHARE of a session's target is never matched to it (a
3 km jog on Saturday must not take Sunday's 8 km long run): with no session
left that fits, the run is an extra.
"""

from __future__ import annotations

from datetime import date

DAY_WEIGHT = 0.15
MIN_SHARE = 0.6


def suggest_session(free_sessions: list[dict], run_date: date, distance_km: float | None) -> dict | None:
    """free_sessions: [{"id", "label", "planned_date" (ISO), "target_km"}] with no run assigned.

    Returns the best session, or None when the run should be an extra.
    """
    best, best_cost = None, None
    for s in free_sessions:
        target = s["target_km"]
        days = abs((date.fromisoformat(s["planned_date"]) - run_date).days)
        if distance_km is None or not target:
            cost = DAY_WEIGHT * days  # nothing to compare: the date decides
        else:
            if distance_km < MIN_SHARE * target:
                continue
            cost = abs(distance_km - target) / target + DAY_WEIGHT * days
        if best_cost is None or cost < best_cost:
            best, best_cost = s, cost
    return best
