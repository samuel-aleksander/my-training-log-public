"""Heart-rate drift (aerobic decoupling) for a single run.

The run's moving time is split into two equal halves. For each half, the
efficiency is average speed divided by average heart rate. Drift is how much
that efficiency drops in the second half:

    drift % = (efficiency 1st half - efficiency 2nd half) / efficiency 1st half * 100

A positive value means the heart rate rose relative to pace as the run went
on. It describes what happened; no threshold is applied here.

Rules, matching zones.py:
  * pauses and recording gaps longer than MAX_SAMPLE_GAP_SECONDS are skipped
  * intervals without HR or while standing still are skipped, never counted as zero
  * the first WARMUP_S of moving time are left out, while the heart rate settles
"""

from __future__ import annotations

from .config import MAX_SAMPLE_GAP_SECONDS
from .zones import _is_paused, paused_intervals

WARMUP_S = 10 * 60
MIN_ANALYSED_S = 20 * 60  # after the warm-up
MIN_SPEED_MPS = 0.5  # below this the runner is standing still


def _half(intervals: list[tuple[float, float, float]]) -> dict:
    """intervals: (seconds, hr, speed). Time-weighted averages."""
    secs = sum(dt for dt, _, _ in intervals)
    hr = sum(dt * h for dt, h, _ in intervals) / secs
    speed = sum(dt * v for dt, _, v in intervals) / secs
    return {
        "seconds": round(secs),
        "avg_hr": round(hr, 1),
        "pace_min_km": round(1000 / speed / 60, 2),
        "efficiency": speed / hr,
    }


def hr_drift(samples: list[dict], events: list[dict]) -> dict:
    """Return {"drift_pct", "first", "second", ...} or {"drift_pct": None, "reason"}."""
    ordered = sorted(samples, key=lambda s: s["ts"])
    pauses = paused_intervals(events)

    moving: list[tuple[float, float, float]] = []
    for prev, cur in zip(ordered, ordered[1:]):
        dt = float(cur["ts"]) - float(prev["ts"])
        if dt <= 0 or dt > MAX_SAMPLE_GAP_SECONDS:
            continue
        if _is_paused((prev["ts"] + cur["ts"]) / 2, pauses):
            continue
        hr, speed = prev.get("hr"), prev.get("speed_mps")
        if hr is None or speed is None or speed < MIN_SPEED_MPS:
            continue
        moving.append((dt, float(hr), float(speed)))

    # Drop the warm-up.
    elapsed = 0.0
    analysed = []
    for iv in moving:
        if elapsed >= WARMUP_S:
            analysed.append(iv)
        elapsed += iv[0]

    total = sum(dt for dt, _, _ in analysed)
    base = {"warmup_excluded_s": WARMUP_S, "analysed_s": round(total)}
    if total < MIN_ANALYSED_S:
        return {
            **base,
            "drift_pct": None,
            "reason": "Not enough moving time with heart rate: needs at least "
            f"{(WARMUP_S + MIN_ANALYSED_S) // 60} minutes.",
        }

    first, second, acc = [], [], 0.0
    for iv in analysed:
        (first if acc < total / 2 else second).append(iv)
        acc += iv[0]
    h1, h2 = _half(first), _half(second)
    drift = (h1["efficiency"] - h2["efficiency"]) / h1["efficiency"] * 100
    for h in (h1, h2):
        del h["efficiency"]
    return {**base, "drift_pct": round(drift, 1), "first": h1, "second": h2}
