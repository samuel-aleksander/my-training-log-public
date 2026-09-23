"""Heart-rate zone time, computed from timestamp intervals.

Rules applied here:
  * zone boundaries come only from user settings (never inferred from age/max HR)
  * pauses (timer stop -> start) are not counted
  * recording gaps longer than MAX_SAMPLE_GAP_SECONDS are not counted
  * samples without HR are "unknown", never zero or low effort
"""

from dataclasses import dataclass, field

from .config import MAX_SAMPLE_GAP_SECONDS


@dataclass
class Zone:
    name: str
    min_hr: int
    max_hr: int


@dataclass
class ZoneResult:
    per_zone: dict[str, float] = field(default_factory=dict)
    unclassified_s: float = 0.0
    unknown_hr_s: float = 0.0
    paused_s: float = 0.0
    gap_s: float = 0.0

    @property
    def measured_active_s(self) -> float:
        """Time with a usable HR reading, excluding pauses and gaps."""
        return sum(self.per_zone.values()) + self.unclassified_s


def parse_zones(raw: list[dict]) -> list[Zone]:
    return [Zone(str(z["name"]), int(z["min_hr"]), int(z["max_hr"])) for z in raw]


def paused_intervals(events: list[dict]) -> list[tuple[int, int | None]]:
    """Build (stop_ts, start_ts) pairs from timer events."""
    intervals: list[tuple[int, int | None]] = []
    open_stop: int | None = None
    for ev in sorted(events, key=lambda e: e["ts"]):
        if ev.get("event") not in (None, "timer"):
            continue
        et = (ev.get("event_type") or "").lower()
        if et.startswith("stop") and open_stop is None:
            open_stop = ev["ts"]
        elif et.startswith("start") and open_stop is not None:
            intervals.append((open_stop, ev["ts"]))
            open_stop = None
    if open_stop is not None:
        intervals.append((open_stop, None))
    return intervals


def _is_paused(ts: float, intervals: list[tuple[int, int | None]]) -> bool:
    for stop, start in intervals:
        if ts >= stop and (start is None or ts < start):
            return True
    return False


def zone_of(hr: int, zones: list[Zone]) -> str | None:
    for z in zones:
        if z.min_hr <= hr <= z.max_hr:
            return z.name
    return None


def compute(
    samples: list[dict],
    events: list[dict],
    zones: list[Zone],
    max_gap_s: int = MAX_SAMPLE_GAP_SECONDS,
) -> ZoneResult:
    result = ZoneResult(per_zone={z.name: 0.0 for z in zones})
    if not zones:
        return result
    ordered = sorted(samples, key=lambda s: s["ts"])
    pauses = paused_intervals(events)

    for prev, cur in zip(ordered, ordered[1:]):
        dt = float(cur["ts"]) - float(prev["ts"])
        if dt <= 0:
            continue
        midpoint = (float(prev["ts"]) + float(cur["ts"])) / 2
        if _is_paused(midpoint, pauses):
            result.paused_s += dt
            continue
        if dt > max_gap_s:
            result.gap_s += dt
            continue
        hr = prev.get("hr")
        if hr is None:
            result.unknown_hr_s += dt
            continue
        name = zone_of(int(hr), zones)
        if name is None:
            result.unclassified_s += dt
        else:
            result.per_zone[name] += dt
    return result
