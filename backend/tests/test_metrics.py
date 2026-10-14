"""Elevation from the FIT file and heart-rate drift."""

from datetime import datetime, timezone

import pytest

from app.fit_parser import elevation_from_samples, parse_fit
from app.metrics import hr_drift
from fit_fixture import make_fit, steady_run

START = datetime(2026, 9, 22, 7, 0, tzinfo=timezone.utc)
SPEED = 1000 / (6.5 * 60)


def run(seconds: int, hr_at, alt_at=None, **kw) -> bytes:
    samples = [(t, hr_at(t), SPEED * t, SPEED) for t in range(0, seconds + 1)]
    alts = [alt_at(t) for t in range(0, seconds + 1)] if alt_at else None
    return make_fit(START, samples, altitudes=alts, **kw)


def as_samples(p):
    return p.samples, p.events


# ------------------------------------------------------------------ elevation


def test_altitude_is_read_per_sample():
    p = parse_fit(run(600, lambda t: 130, lambda t: 20 + t / 10))
    assert p.samples[0]["altitude_m"] == pytest.approx(20, abs=0.2)
    assert p.samples[-1]["altitude_m"] == pytest.approx(80, abs=0.2)


def test_watch_totals_are_preferred():
    p = parse_fit(run(600, lambda t: 130, lambda t: 20 + t / 10, session_elevation=(75, 12)))
    assert (p.ascent_m, p.descent_m, p.elevation_source) == (75, 12, "device")


def test_totals_computed_from_samples_without_watch_totals():
    # up 60 m, then down 40 m
    p = parse_fit(run(1000, lambda t: 130, lambda t: 20 + min(t, 600) / 10 - max(0, t - 600) / 10))
    assert p.elevation_source == "samples"
    assert p.ascent_m == pytest.approx(60, abs=3)
    assert p.descent_m == pytest.approx(40, abs=3)


def test_altitude_noise_is_not_counted_as_climb():
    wobble = [{"ts": t, "altitude_m": 50 + (1 if t % 2 else -1)} for t in range(600)]
    assert elevation_from_samples(wobble) == (0, 0)


def test_no_altitude_means_unknown_not_flat():
    p = parse_fit(steady_run(START, 3.0, 6.5))
    assert (p.ascent_m, p.descent_m, p.elevation_source) == (None, None, None)
    assert all(s["altitude_m"] is None for s in p.samples)


# ------------------------------------------------------------------ HR drift


def test_steady_heart_rate_has_no_drift():
    d = hr_drift(*as_samples(parse_fit(run(50 * 60, lambda t: 135))))
    assert d["drift_pct"] == pytest.approx(0, abs=0.1)
    assert d["first"]["avg_hr"] == d["second"]["avg_hr"] == 135


def test_rising_heart_rate_at_same_pace_is_positive_drift():
    # after the warm-up: 130 bpm for the first half, 140 for the second
    d = hr_drift(*as_samples(parse_fit(run(50 * 60, lambda t: 130 if t < 30 * 60 else 140))))
    assert d["drift_pct"] == pytest.approx((1 - 130 / 140) * 100, abs=0.3)
    assert d["analysed_s"] == pytest.approx(40 * 60, abs=2)


def test_warm_up_is_excluded():
    # very low HR only during the first 10 minutes must not create drift
    d = hr_drift(*as_samples(parse_fit(run(50 * 60, lambda t: 100 if t < 600 else 135))))
    assert d["drift_pct"] == pytest.approx(0, abs=0.1)


def test_short_runs_have_no_drift_value():
    d = hr_drift(*as_samples(parse_fit(run(25 * 60, lambda t: 135))))
    assert d["drift_pct"] is None
    assert "30 minutes" in d["reason"]


def test_missing_heart_rate_is_skipped_not_zero():
    d = hr_drift(*as_samples(parse_fit(run(50 * 60, lambda t: None if 1800 <= t < 2100 else 135))))
    assert d["drift_pct"] == pytest.approx(0, abs=0.1)
    assert d["analysed_s"] == pytest.approx(35 * 60, abs=2)


def test_pauses_are_skipped():
    samples = [(t, 135, SPEED * t, SPEED) for t in range(0, 1800)]
    samples += [(t, 170, SPEED * 1800, 0.0) for t in range(1800, 2400, 5)]  # standing, HR spikes
    samples += [(t, 135, SPEED * (t - 600), SPEED) for t in range(2400, 3600)]
    p = parse_fit(make_fit(START, samples, pauses=[(1800, 2400)]))
    assert hr_drift(p.samples, p.events)["drift_pct"] == pytest.approx(0, abs=0.1)
