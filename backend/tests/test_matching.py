"""Which planned session a run goes to when it is not on the planned day."""

from datetime import date

from app.matching import suggest_session

# Week 1 of the sample plan: Tue A 5 km, Thu B 6 km, Sat C 4 km, Sun L 8 km.
WEEK = [
    {"id": 1, "label": "A", "planned_date": "2026-01-06", "target_km": 5},
    {"id": 2, "label": "B", "planned_date": "2026-01-08", "target_km": 6},
    {"id": 3, "label": "C", "planned_date": "2026-01-10", "target_km": 4},
    {"id": 4, "label": "L", "planned_date": "2026-01-11", "target_km": 8},
]


def label(sessions, day, km):
    s = suggest_session(sessions, date.fromisoformat(day), km)
    return s["label"] if s else None


def test_run_on_the_planned_day_goes_to_that_session():
    assert label(WEEK, "2026-01-06", 5.0) == "A"
    assert label(WEEK, "2026-01-11", 8.2) == "L"


def test_run_a_day_early_or_late_still_finds_its_session():
    assert label(WEEK, "2026-01-07", 6.1) == "B"  # Thursday's run done on Wednesday
    assert label(WEEK, "2026-01-05", 5.0) == "A"  # Tuesday's run done on Monday
    assert label(WEEK, "2026-01-10", 8.0) == "L"  # long run on Saturday


def test_distance_decides_between_nearby_days():
    # Wednesday sits between A (Tue) and B (Thu): a 6 km run is B, a 5 km run is A
    assert label(WEEK, "2026-01-07", 6.0) == "B"
    assert label(WEEK, "2026-01-07", 5.0) == "A"


def test_only_free_sessions_are_considered():
    free = [s for s in WEEK if s["label"] != "A"]
    assert label(free, "2026-01-06", 5.0) == "B"


def test_a_much_shorter_run_never_takes_a_session():
    only_long = [WEEK[3]]
    assert label(only_long, "2026-01-10", 3.0) is None  # extra, not the 8 km long run
    assert label(only_long, "2026-01-11", 4.8) == "L"   # 60 % of it still counts


def test_no_free_session_means_extra():
    assert label([], "2026-01-06", 5.0) is None


def test_without_distance_the_date_decides():
    assert label(WEEK, "2026-01-09", None) in ("B", "C")
    assert label(WEEK, "2026-01-11", None) == "L"
