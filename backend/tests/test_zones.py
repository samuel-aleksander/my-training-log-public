from app.zones import Zone, compute

ZONES = [Zone("Z1", 100, 119), Zone("Z2", 120, 139), Zone("Z3", 140, 154)]


def s(ts, hr):
    return {"ts": ts, "hr": hr}


def test_irregular_intervals_use_timestamp_deltas():
    samples = [s(0, 125), s(3, 125), s(10, 145), s(11, 145)]
    r = compute(samples, [], ZONES)
    assert r.per_zone["Z2"] == 10  # 3 + 7 seconds at 125 bpm
    assert r.per_zone["Z3"] == 1
    assert r.measured_active_s == 11


def test_pause_is_not_counted():
    samples = [s(0, 130), s(10, 130), s(310, 130), s(320, 130)]
    events = [
        {"ts": 10, "event": "timer", "event_type": "stop_all"},
        {"ts": 310, "event": "timer", "event_type": "start"},
    ]
    r = compute(samples, events, ZONES)
    assert r.per_zone["Z2"] == 20
    assert r.paused_s == 300
    assert r.gap_s == 0


def test_long_recording_gap_is_not_extended():
    samples = [s(0, 130), s(10, 130), s(4000, 130), s(4010, 130)]
    r = compute(samples, [], ZONES)
    assert r.per_zone["Z2"] == 20
    assert r.gap_s == 3990


def test_missing_hr_is_unknown_not_zero():
    samples = [s(0, None), s(10, None), s(20, 130), s(30, 130)]
    r = compute(samples, [], ZONES)
    assert r.unknown_hr_s == 20
    assert r.per_zone["Z2"] == 10
    assert r.per_zone["Z1"] == 0
    assert r.measured_active_s == 10  # percentages use measured time only


def test_hr_outside_configured_zones_is_unclassified():
    samples = [s(0, 175), s(10, 175)]
    r = compute(samples, [], ZONES)
    assert r.unclassified_s == 10
    assert sum(r.per_zone.values()) == 0


def test_no_zones_configured_yields_nothing():
    r = compute([s(0, 130), s(10, 130)], [], [])
    assert r.per_zone == {}
    assert r.measured_active_s == 0
