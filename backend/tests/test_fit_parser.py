import io
import zipfile
from datetime import datetime, timezone

import pytest

from app.fit_parser import FitError, extract_fit_bytes, parse_fit
from fit_fixture import make_fit, steady_run

START = datetime(2026, 9, 22, 7, 0, tzinfo=timezone.utc)


def test_parses_summary_and_samples():
    p = parse_fit(steady_run(START, 5.0, 7.0, hr=132))
    assert p.distance_m == pytest.approx(5000, abs=5)
    assert p.timer_time_s == pytest.approx(2100, abs=2)
    assert p.duration_source == "timer"
    assert p.has_hr and p.avg_hr == 132
    assert len(p.samples) > 100
    assert p.laps


def test_missing_hr_stays_null():
    p = parse_fit(steady_run(START, 2.0, 7.0, hr=None))
    assert p.has_hr is False
    assert all(s["hr"] is None for s in p.samples)
    assert p.avg_hr is None


def test_fallback_duration_is_labelled():
    p = parse_fit(steady_run(START, 2.0, 7.0, include_session_timer=False))
    assert p.duration_source == "fallback_samples"
    assert p.timer_time_s > 0


def test_pause_events_are_kept():
    speed = 1000 / (7 * 60)
    samples = [(t, 130, speed * t, speed) for t in range(0, 100)]
    samples += [(t, 130, speed * 99, 0.0) for t in range(100, 400, 10)]
    p = parse_fit(make_fit(START, samples, pauses=[(100, 400)]))
    assert any((e["event_type"] or "").startswith("stop") for e in p.events)


def test_corrupt_file_reports_clearly():
    with pytest.raises(FitError):
        parse_fit(b"not a fit file at all")


def test_zip_with_one_activity():
    fit = steady_run(START, 3.0, 7.0)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("12345_ACTIVITY.fit", fit)
    name, data = extract_fit_bytes("export.zip", buf.getvalue())
    assert name.endswith(".fit") and data == fit


def test_zip_with_two_activities_is_rejected():
    fit = steady_run(START, 3.0, 7.0)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.fit", fit)
        z.writestr("b.fit", fit)
    with pytest.raises(FitError):
        extract_fit_bytes("export.zip", buf.getvalue())


def test_zip_with_unsafe_path_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.fit", b"x")
    with pytest.raises(FitError):
        extract_fit_bytes("export.zip", buf.getvalue())
