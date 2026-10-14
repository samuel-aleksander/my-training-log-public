"""Decode a .FIT file (or a Garmin ZIP containing exactly one activity)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from garmin_fit_sdk import Decoder, Stream

FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z

from .config import MAX_UNCOMPRESSED_BYTES, MAX_ZIP_ENTRIES, MAX_SAMPLE_GAP_SECONDS

# Bump when parse_fit starts storing something new, so files already imported
# are re-read from their original bytes (see main.reparse_outdated).
PARSER_VERSION = 2

# Altitude changes smaller than this are treated as sensor noise when the file
# has no ascent/descent totals and they have to be computed from the samples.
ELEVATION_NOISE_M = 3.0


class FitError(Exception):
    """Raised with a plain-language message for corrupt/unsupported uploads."""


@dataclass
class ParsedActivity:
    start_time: datetime
    distance_m: float | None
    timer_time_s: float | None
    elapsed_time_s: float | None
    duration_source: str
    avg_hr: int | None
    max_hr: int | None
    has_hr: bool
    sport: str | None
    ascent_m: float | None = None
    descent_m: float | None = None
    elevation_source: str | None = None  # 'device' | 'samples' | None (no altitude)
    samples: list[dict] = field(default_factory=list)
    laps: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_fit_bytes(filename: str, data: bytes) -> tuple[str, bytes]:
    """Return (fit_filename, fit_bytes). Accepts .fit or a ZIP with one activity."""
    lower = (filename or "").lower()
    if data[8:12] == b".FIT":
        return filename or "activity.fit", data
    if lower.endswith(".zip") or data[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise FitError("The ZIP file is damaged or cannot be read.") from exc
        entries = zf.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise FitError("The ZIP contains too many files.")
        total = sum(e.file_size for e in entries)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise FitError("The ZIP contents exceed the allowed size limit.")
        fits = []
        for e in entries:
            name = e.filename
            if name.endswith("/"):
                continue
            if name.startswith("/") or ".." in name.replace("\\", "/").split("/") or "\\" in name:
                raise FitError(f"Unsafe path inside the ZIP: {name}")
            if name.lower().endswith(".fit") and not name.split("/")[-1].startswith("."):
                fits.append(e)
        if len(fits) == 0:
            raise FitError("The ZIP does not contain any .FIT file.")
        if len(fits) > 1:
            raise FitError("The ZIP contains more than one activity. Upload a single activity.")
        with zf.open(fits[0]) as fh:
            content = fh.read(MAX_UNCOMPRESSED_BYTES + 1)
        if len(content) > MAX_UNCOMPRESSED_BYTES:
            raise FitError("The extracted file exceeds the allowed size limit.")
        return fits[0].filename.split("/")[-1], content
    raise FitError("Unsupported format. Upload an original .FIT file or a Garmin ZIP.")


def _ts(value) -> int | None:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(value, (int, float)):
        v = int(value)
        # FIT date_time values >= 0x10000000 are seconds since 1989-12-31 UTC.
        return v + FIT_EPOCH if v >= 0x10000000 else v
    return None


def parse_fit(data: bytes) -> ParsedActivity:
    try:
        stream = Stream.from_byte_array(bytearray(data))
        decoder = Decoder(stream)
        if not decoder.is_fit():
            raise FitError("The file is not a valid FIT file.")
        if not decoder.check_integrity():
            raise FitError("The FIT file is incomplete or damaged (integrity check failed).")
        # is_fit()/check_integrity() consume the stream: decode from a fresh one.
        decoder = Decoder(Stream.from_byte_array(bytearray(data)))
        messages, errors = decoder.read(convert_datetimes_to_dates=False)
    except FitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FitError(f"Could not decode the FIT file: {exc}") from exc

    if errors:
        raise FitError(f"The FIT file contains decoding errors: {errors[0]}")

    records = messages.get("record_mesgs", []) or []
    sessions = messages.get("session_mesgs", []) or []
    laps_raw = messages.get("lap_mesgs", []) or []
    events_raw = messages.get("event_mesgs", []) or []

    samples: list[dict] = []
    for r in records:
        ts = _ts(r.get("timestamp"))
        if ts is None:
            continue
        hr = r.get("heart_rate")
        speed = r.get("enhanced_speed", r.get("speed"))
        alt = r.get("enhanced_altitude", r.get("altitude"))
        samples.append(
            {
                "ts": ts,
                "hr": int(hr) if isinstance(hr, (int, float)) and hr > 0 else None,
                "distance_m": float(r["distance"]) if r.get("distance") is not None else None,
                "speed_mps": float(speed) if speed is not None else None,
                "altitude_m": float(alt) if isinstance(alt, (int, float)) else None,
            }
        )
    if not samples and not sessions:
        raise FitError("The file contains no recognizable activity data.")

    events: list[dict] = []
    for e in events_raw:
        ts = _ts(e.get("timestamp"))
        if ts is None:
            continue
        events.append({"ts": ts, "event": e.get("event"), "event_type": e.get("event_type")})

    sess = sessions[0] if sessions else {}
    start_ts = _ts(sess.get("start_time")) or (samples[0]["ts"] if samples else None)
    if start_ts is None:
        raise FitError("The file has no start date.")

    distance = sess.get("total_distance")
    if distance is None:
        dists = [s["distance_m"] for s in samples if s["distance_m"] is not None]
        distance = max(dists) if dists else None

    timer = sess.get("total_timer_time")
    elapsed = sess.get("total_elapsed_time")
    if timer is not None:
        duration_source = "timer"
    else:
        moving = _sum_moving_seconds(samples, events)
        if moving > 0:
            timer, duration_source = moving, "fallback_samples"
        elif elapsed is not None:
            timer, duration_source = elapsed, "fallback_elapsed"
        else:
            duration_source = "fallback_samples"
    if elapsed is None and samples:
        elapsed = samples[-1]["ts"] - samples[0]["ts"]

    hrs = [s["hr"] for s in samples if s["hr"] is not None]
    avg_hr = sess.get("avg_heart_rate") or (round(sum(hrs) / len(hrs)) if hrs else None)
    max_hr = sess.get("max_heart_rate") or (max(hrs) if hrs else None)

    ascent, descent = sess.get("total_ascent"), sess.get("total_descent")
    if ascent is not None or descent is not None:
        elevation_source = "device"
    else:
        ascent, descent = elevation_from_samples(samples)
        elevation_source = "samples" if ascent is not None else None

    laps = []
    for i, lp in enumerate(laps_raw):
        lts = _ts(lp.get("start_time"))
        laps.append(
            {
                "idx": i + 1,
                "start_time": datetime.fromtimestamp(lts, tz=timezone.utc).isoformat() if lts else None,
                "distance_m": lp.get("total_distance"),
                "timer_time_s": lp.get("total_timer_time"),
                "elapsed_time_s": lp.get("total_elapsed_time"),
                "avg_hr": lp.get("avg_heart_rate"),
                "max_hr": lp.get("max_heart_rate"),
            }
        )

    return ParsedActivity(
        start_time=datetime.fromtimestamp(start_ts, tz=timezone.utc),
        distance_m=float(distance) if distance is not None else None,
        timer_time_s=float(timer) if timer is not None else None,
        elapsed_time_s=float(elapsed) if elapsed is not None else None,
        duration_source=duration_source,
        avg_hr=int(avg_hr) if avg_hr else None,
        max_hr=int(max_hr) if max_hr else None,
        has_hr=bool(hrs),
        sport=sess.get("sport"),
        ascent_m=float(ascent) if ascent is not None else None,
        descent_m=float(descent) if descent is not None else None,
        elevation_source=elevation_source,
        samples=samples,
        laps=laps,
        events=events,
    )


def elevation_from_samples(samples: list[dict]) -> tuple[float | None, float | None]:
    """Total climb and descent, ignoring wiggles under ELEVATION_NOISE_M.

    Only used when the watch did not write its own totals in the session.
    """
    alts = [s["altitude_m"] for s in sorted(samples, key=lambda s: s["ts"])
            if s.get("altitude_m") is not None]
    if len(alts) < 2:
        return None, None
    ascent = descent = 0.0
    ref = alts[0]
    for a in alts[1:]:
        if a - ref >= ELEVATION_NOISE_M:
            ascent += a - ref
            ref = a
        elif ref - a >= ELEVATION_NOISE_M:
            descent += ref - a
            ref = a
    return ascent, descent


def _sum_moving_seconds(samples: list[dict], events: list[dict]) -> float:
    from .zones import paused_intervals, _is_paused

    pauses = paused_intervals(events)
    total = 0.0
    ordered = sorted(samples, key=lambda s: s["ts"])
    for prev, cur in zip(ordered, ordered[1:]):
        dt = cur["ts"] - prev["ts"]
        if dt <= 0 or dt > MAX_SAMPLE_GAP_SECONDS:
            continue
        if _is_paused((prev["ts"] + cur["ts"]) / 2, pauses):
            continue
        total += dt
    return total
