from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import config, zones as zonelib
from .db import connect, init_db
from .fit_parser import PARSER_VERSION, FitError, ParsedActivity, extract_fit_bytes, parse_fit, sha256
from .matching import suggest_session
from .metrics import hr_drift
from .plan_edits import PlanError, session_date, target_duration, target_km, write_session
from .seed import effort_context, seed

log = logging.getLogger("marathon")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_conn()
    stop = threading.Event()
    if config.INBOX_POLL_SECONDS > 0:
        threading.Thread(target=watch_inbox, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Marathon base-block tracker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = connect()
        init_db(_conn)
        seed(_conn)
        reparse_outdated(_conn)
    return _conn


# --------------------------------------------------------------------------- helpers


def get_zones(conn) -> list[zonelib.Zone]:
    row = conn.execute("SELECT value FROM settings WHERE key='zones'").fetchone()
    return zonelib.parse_zones(json.loads(row["value"])) if row else []


def activity_dict(a: sqlite3.Row) -> dict:
    return {
        "id": a["id"],
        "original_filename": a["original_filename"],
        "start_time": a["start_time"],
        "distance_km": round(a["distance_m"] / 1000, 3) if a["distance_m"] is not None else None,
        "timer_time_s": a["timer_time_s"],
        "elapsed_time_s": a["elapsed_time_s"],
        "duration_source": a["duration_source"],
        "avg_hr": a["avg_hr"],
        "max_hr": a["max_hr"],
        "has_hr": bool(a["has_hr"]),
        "ascent_m": round(a["ascent_m"]) if a["ascent_m"] is not None else None,
        "descent_m": round(a["descent_m"]) if a["descent_m"] is not None else None,
        "elevation_source": a["elevation_source"],
        "is_extra": bool(a["is_extra"]),
        "extra_type": a["extra_type"],
        "rpe": a["rpe"],
        "note": a["note"],
        "is_demo": bool(a["is_demo"]),
        "auto_assigned": bool(a["auto_assigned"]),
        "week_id": a["week_id"],
        "session_id": a["session_id"],
    }


def week_payload(conn, week: sqlite3.Row) -> dict:
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE week_id=? ORDER BY COALESCE(override_date, planned_date), label",
        (week["id"],),
    ).fetchall()
    today = date.today().isoformat()

    session_items = []
    planned_total = 0.0
    actual_total = 0.0
    completed = 0
    duration_total = 0.0
    for s in sessions:
        tkm = target_km(s)
        skipped = s["status"] == "skipped"  # skipped on purpose: not part of the week's plan
        if not skipped:
            planned_total += tkm
        day = session_date(s)
        act = conn.execute("SELECT * FROM activities WHERE session_id=?", (s["id"],)).fetchone()
        actual_km = None
        if act:
            actual_km = round(act["distance_m"] / 1000, 3) if act["distance_m"] is not None else None
            actual_total += actual_km or 0
            duration_total += act["timer_time_s"] or 0
            completed += 1
            status = "completed"
        else:
            status = "skipped" if skipped or day < today else "planned"
        tdur = target_duration(s)
        session_items.append(
            {
                "id": s["id"],
                "label": s["label"],
                "planned_date": day,
                "original_date": s["planned_date"],
                "plan_status": s["status"],
                "seeded_km": s["seeded_km"],
                "target_km": tkm,
                "target_duration_min": tdur,
                "target_duration_display": f"{tdur:g} min" if tdur is not None else "Not set",
                "notes": s["notes"],
                "user_note": s["user_note"],
                "status": status,
                "actual_km": actual_km,
                "diff_km": round(actual_km - tkm, 3) if actual_km is not None else None,
                "diff_duration_min": (
                    round((act["timer_time_s"] or 0) / 60 - tdur, 1)
                    if act and tdur is not None
                    else None
                ),
                "activity": activity_dict(act) if act else None,
            }
        )

    extras = conn.execute(
        "SELECT * FROM activities WHERE week_id=? AND is_extra=1 ORDER BY start_time", (week["id"],)
    ).fetchall()
    for e in extras:
        actual_total += (e["distance_m"] or 0) / 1000
        duration_total += e["timer_time_s"] or 0

    return {
        "id": week["id"],
        "number": week["number"],
        "start_date": week["start_date"],
        "end_date": week["end_date"],
        "notes": week["notes"],
        "effort_context": effort_context(),
        "planned_km": round(planned_total, 2),
        "seeded_planned_km": week["planned_km"],
        "actual_km": round(actual_total, 2),
        "diff_km": round(actual_total - planned_total, 2),
        "completion_pct": round(actual_total / planned_total * 100, 1) if planned_total else None,
        "sessions_completed": completed,
        "sessions_planned": sum(1 for s in sessions if s["status"] != "skipped"),
        "actual_duration_s": round(duration_total),
        "sessions": session_items,
        "extras": [activity_dict(e) for e in extras],
        "journal": week_journal(conn, week["id"]),
    }


MAX_REASONS = 8
MAX_REASON_CHARS = 40
MAX_NOTE_CHARS = 4000


def week_journal(conn, week_id: int) -> dict:
    """Your note and reason tags for a week; empty until you write one."""
    row = conn.execute("SELECT * FROM week_notes WHERE week_id=?", (week_id,)).fetchone()
    if not row:
        return {"note": None, "reasons": [], "updated_at": None}
    return {"note": row["note"], "reasons": json.loads(row["reasons"]), "updated_at": row["updated_at"]}


def insert_samples(conn, activity_id: int, parsed: ParsedActivity) -> None:
    conn.executemany(
        "INSERT INTO samples (activity_id, ts, hr, distance_m, speed_mps, altitude_m) VALUES (?,?,?,?,?,?)",
        [(activity_id, s["ts"], s["hr"], s["distance_m"], s["speed_mps"], s["altitude_m"])
         for s in parsed.samples],
    )


def reparse_outdated(conn) -> int:
    """Re-read activities imported by an older parser from their original file.

    Only the fields the parser has learned since are refreshed (samples and
    elevation); start time, assignment, notes and zones are left as they are.
    """
    rows = conn.execute(
        "SELECT id, stored_path FROM activities WHERE parser_version < ?", (PARSER_VERSION,)
    ).fetchall()
    updated = 0
    for a in rows:
        path = Path(a["stored_path"])
        if not path.exists():
            continue
        try:
            parsed = parse_fit(path.read_bytes())
        except FitError:
            continue
        conn.execute("DELETE FROM samples WHERE activity_id=?", (a["id"],))
        insert_samples(conn, a["id"], parsed)
        conn.execute(
            "UPDATE activities SET ascent_m=?, descent_m=?, elevation_source=?, parser_version=? WHERE id=?",
            (parsed.ascent_m, parsed.descent_m, parsed.elevation_source, PARSER_VERSION, a["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def drift_for(conn, activity_id: int) -> dict:
    samples = [dict(r) for r in conn.execute(
        "SELECT ts, hr, speed_mps FROM samples WHERE activity_id=? ORDER BY ts", (activity_id,))]
    events = [dict(r) for r in conn.execute(
        "SELECT ts, event, event_type FROM timer_events WHERE activity_id=?", (activity_id,))]
    return hr_drift(samples, events)


def run_summary(conn, run: dict) -> dict:
    """An activity_dict plus what the Week screen shows on each run's card."""
    km_ = run["distance_km"]
    return {
        **run,
        "pace_min_km": round(run["timer_time_s"] / 60 / km_, 2) if km_ and run["timer_time_s"] else None,
        "zones": zone_payload(conn, run["id"]),
        "hr_drift_pct": drift_for(conn, run["id"])["drift_pct"] if run["has_hr"] else None,
    }


def week_detail(conn, week: sqlite3.Row, wp: dict) -> dict:
    """The single-week view: per-run summaries, day-by-day km and week-level heart rate.

    Kept out of week_payload so the all-weeks list and the Block screen stay light.
    """
    for s in wp["sessions"]:
        if s["activity"]:
            s["activity"] = run_summary(conn, s["activity"])
    wp["extras"] = [run_summary(conn, e) for e in wp["extras"]]
    runs = [s["activity"] for s in wp["sessions"] if s["activity"]] + wp["extras"]

    # Heart rate: average weighted by each run's duration, over runs that have one.
    with_hr = [r for r in runs if r["avg_hr"] and r["timer_time_s"]]
    hr_time = sum(r["timer_time_s"] for r in with_hr)
    km_total = sum(r["distance_km"] or 0 for r in runs)
    time_total = sum(r["timer_time_s"] or 0 for r in runs)
    climbs = [r["ascent_m"] for r in runs if r["ascent_m"] is not None]

    zone_names = [z.name for z in get_zones(conn)]
    zone_secs = {n: 0.0 for n in zone_names}
    measured = unknown = 0.0
    for r in runs:
        if r["zones"]:
            measured += r["zones"]["measured_active_s"]
            unknown += r["zones"]["unknown_hr_s"]
            for b in r["zones"]["buckets"]:
                zone_secs[b["name"]] = zone_secs.get(b["name"], 0) + b["seconds"]

    start = date.fromisoformat(week["start_date"])
    days = []
    for i in range(7):
        d = (start + timedelta(days=i)).isoformat()
        planned = [s for s in wp["sessions"] if s["planned_date"] == d and s["plan_status"] != "skipped"]
        ran = [r for r in runs if datetime.fromisoformat(r["start_time"]).astimezone().date().isoformat() == d]
        days.append({
            "date": d,
            "planned_km": round(sum(s["target_km"] for s in planned), 2) if planned else None,
            "planned_labels": [s["label"] for s in planned],
            "ran_km": round(sum(r["distance_km"] or 0 for r in ran), 2) if ran else None,
            "runs": len(ran),
        })

    return {
        **wp,
        "avg_hr": round(sum(r["avg_hr"] * r["timer_time_s"] for r in with_hr) / hr_time) if hr_time else None,
        "avg_pace_min_km": round(time_total / 60 / km_total, 2) if km_total and time_total else None,
        "ascent_m": round(sum(climbs)) if climbs else None,
        "zones": {
            "names": zone_names + [n for n in zone_secs if n not in zone_names],
            "seconds": {n: round(v) for n, v in zone_secs.items()},
            "measured_s": round(measured),
            "unknown_hr_s": round(unknown),
        } if zone_names else None,
        "days": days,
    }


def free_sessions(conn, week_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT s.* FROM sessions s WHERE s.week_id=? AND s.status <> 'skipped'
           AND NOT EXISTS (SELECT 1 FROM activities a WHERE a.session_id = s.id)
           ORDER BY COALESCE(s.override_date, s.planned_date)""",
        (week_id,),
    ).fetchall()
    return [{"id": s["id"], "label": s["label"], "planned_date": session_date(s),
             "target_km": target_km(s)} for s in rows]


def store_activity(
    conn,
    parsed: ParsedActivity,
    digest: str,
    filename: str,
    stored: Path,
    week_id: int,
    session_id: int | None,
    *,
    extra_type: str | None = None,
    rpe=None,
    note=None,
    is_demo: bool = False,
    auto_assigned: bool = False,
) -> int:
    """Insert an activity whose original file is already at `stored`. session_id None = extra."""
    is_extra = session_id is None
    cur = conn.execute(
        """INSERT INTO activities
           (file_hash, original_filename, stored_path, start_time, distance_m, timer_time_s,
            elapsed_time_s, duration_source, avg_hr, max_hr, has_hr, sport,
            ascent_m, descent_m, elevation_source, parser_version, auto_assigned, is_demo,
            week_id, session_id, is_extra, extra_type, rpe, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            digest,
            filename,
            str(stored),
            parsed.start_time.isoformat(),
            parsed.distance_m,
            parsed.timer_time_s,
            parsed.elapsed_time_s,
            parsed.duration_source,
            parsed.avg_hr,
            parsed.max_hr,
            int(parsed.has_hr),
            parsed.sport,
            parsed.ascent_m,
            parsed.descent_m,
            parsed.elevation_source,
            PARSER_VERSION,
            int(auto_assigned),
            int(is_demo),
            week_id,
            session_id,
            int(is_extra),
            extra_type if is_extra else None,
            rpe,
            note,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    aid = cur.lastrowid
    insert_samples(conn, aid, parsed)
    conn.executemany(
        """INSERT INTO laps (activity_id, idx, start_time, distance_m, timer_time_s,
                             elapsed_time_s, avg_hr, max_hr) VALUES (?,?,?,?,?,?,?,?)""",
        [(aid, l["idx"], l["start_time"], l["distance_m"], l["timer_time_s"],
          l["elapsed_time_s"], l["avg_hr"], l["max_hr"]) for l in parsed.laps],
    )
    conn.executemany(
        "INSERT INTO timer_events (activity_id, ts, event, event_type) VALUES (?,?,?,?)",
        [(aid, e["ts"], e["event"], e["event_type"]) for e in parsed.events],
    )
    conn.commit()
    recompute_zones(conn, aid)
    return aid


# --------------------------------------------------------------------------- watched folder

RUN_SPORTS = {None, "running"}


def scan_inbox(conn, inbox: Path, settle_s: float = config.INBOX_SETTLE_SECONDS) -> list[dict]:
    """Import every new .fit/.zip in `inbox`. Files are only read, never moved or deleted.

    Runs are handled in the order they were recorded, so sessions fill up the
    way they happened. Returns one entry per file handled in this scan.
    """
    if not inbox.is_dir():
        return []
    now = time.time()
    pending = []
    for p in sorted(inbox.iterdir()):
        if p.name.startswith(".") or not p.is_file() or p.suffix.lower() not in (".fit", ".zip"):
            continue
        st = p.stat()
        if now - st.st_mtime < settle_s:
            continue  # maybe still being written; next scan
        key = (str(p.resolve()), st.st_size, int(st.st_mtime))
        if conn.execute(
            "SELECT 1 FROM inbox_files WHERE path=? AND size=? AND mtime=?", key
        ).fetchone():
            continue
        entry = {"key": key, "filename": p.name, "parsed": None, "fit": None}
        try:
            if st.st_size > config.MAX_UPLOAD_BYTES:
                raise FitError("The file is too large.")
            _, entry["fit"] = extract_fit_bytes(p.name, p.read_bytes())
            entry["parsed"] = parse_fit(entry["fit"])
        except FitError as exc:
            entry["status"], entry["message"] = "skipped", str(exc)
        except OSError as exc:
            entry["status"], entry["message"] = "failed", f"Could not read the file: {exc}"
        pending.append(entry)

    pending.sort(key=lambda e: e["parsed"].start_time.timestamp() if e["parsed"] else 0)
    handled = []
    for e in pending:
        aid = None
        if e["parsed"]:
            e["status"], e["message"], aid = _import_from_inbox(conn, e["filename"], e["fit"], e["parsed"])
        path, size, mtime = e["key"]
        conn.execute(
            """INSERT INTO inbox_files (path, filename, size, mtime, status, message, activity_id, seen_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (path, e["filename"], size, mtime, e["status"], e["message"], aid,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        handled.append({"filename": e["filename"], "status": e["status"], "message": e["message"],
                        "activity_id": aid})
    return handled


def _import_from_inbox(conn, filename: str, fit: bytes, parsed: ParsedActivity):
    if parsed.sport not in RUN_SPORTS:
        return "skipped", f"Not a run ({parsed.sport}). Upload it by hand if it belongs here.", None
    digest = sha256(fit)
    existing = conn.execute("SELECT id FROM activities WHERE file_hash=?", (digest,)).fetchone()
    if existing:
        return "duplicate", "Already imported.", existing["id"]
    local_date = parsed.start_time.astimezone().date()
    week = week_for_date(conn, local_date.isoformat())
    if not week:
        return "skipped", "The run's date is outside the plan's weeks.", None

    km_ = parsed.distance_m / 1000 if parsed.distance_m else None
    session = suggest_session(free_sessions(conn, week["id"]), local_date, km_)
    stored = config.FILES_DIR / f"{digest}.fit"
    config.ensure_dirs()
    stored.write_bytes(fit)
    aid = store_activity(
        conn, parsed, digest, filename, stored, week["id"],
        session["id"] if session else None,
        extra_type=None if session else "Extra run",
        auto_assigned=True,
    )
    where = f"session {session['label']}" if session else "an extra run"
    return "imported", f"Week {week['number']}, {where}.", aid


def watch_inbox(stop: threading.Event) -> None:
    conn = connect()
    while not stop.is_set():
        try:
            for h in scan_inbox(conn, config.INBOX_DIR):
                log.info("inbox: %s %s %s", h["filename"], h["status"], h["message"])
        except Exception:  # noqa: BLE001 - keep watching after an unexpected error
            log.exception("inbox scan failed")
        stop.wait(config.INBOX_POLL_SECONDS)


def week_for_date(conn, iso_date: str):
    return conn.execute(
        "SELECT * FROM weeks WHERE start_date <= ? AND end_date >= ?", (iso_date, iso_date)
    ).fetchone()


def recompute_zones(conn, activity_id: int) -> dict | None:
    zs = get_zones(conn)
    conn.execute("DELETE FROM zone_results WHERE activity_id=?", (activity_id,))
    conn.execute("DELETE FROM zone_runs WHERE activity_id=?", (activity_id,))
    if not zs:
        conn.commit()
        return None
    samples = [dict(r) for r in conn.execute(
        "SELECT ts, hr FROM samples WHERE activity_id=? ORDER BY ts", (activity_id,))]
    events = [dict(r) for r in conn.execute(
        "SELECT ts, event, event_type FROM timer_events WHERE activity_id=?", (activity_id,))]
    res = zonelib.compute(samples, events, zs)
    for name, secs in res.per_zone.items():
        conn.execute(
            "INSERT INTO zone_results (activity_id, zone_name, seconds) VALUES (?,?,?)",
            (activity_id, name, secs),
        )
    if res.unclassified_s:
        conn.execute(
            "INSERT INTO zone_results (activity_id, zone_name, seconds) VALUES (?,?,?)",
            (activity_id, "Outside zones", res.unclassified_s),
        )
    conn.execute(
        """INSERT INTO zone_runs
           (activity_id, zones_json, computed_at, measured_active_s, unknown_hr_s, paused_s, gap_s)
           VALUES (?,?,?,?,?,?,?)""",
        (
            activity_id,
            json.dumps([z.__dict__ for z in zs]),
            datetime.now(timezone.utc).isoformat(),
            res.measured_active_s,
            res.unknown_hr_s,
            res.paused_s,
            res.gap_s,
        ),
    )
    conn.commit()
    return zone_payload(conn, activity_id)


def zone_payload(conn, activity_id: int) -> dict | None:
    run = conn.execute("SELECT * FROM zone_runs WHERE activity_id=?", (activity_id,)).fetchone()
    if not run:
        return None
    rows = conn.execute(
        "SELECT zone_name, seconds FROM zone_results WHERE activity_id=?", (activity_id,)
    ).fetchall()
    active = run["measured_active_s"]
    return {
        "computed_at": run["computed_at"],
        "zones_used": json.loads(run["zones_json"]),
        "measured_active_s": active,
        "unknown_hr_s": run["unknown_hr_s"],
        "paused_s": run["paused_s"],
        "gap_s": run["gap_s"],
        "buckets": [
            {
                "name": r["zone_name"],
                "seconds": r["seconds"],
                "minutes": round(r["seconds"] / 60, 1),
                "pct": round(r["seconds"] / active * 100, 1) if active else None,
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------- routes


@app.get("/api/health")
def health():
    get_conn()
    return {"status": "ok"}


@app.get("/api/weeks")
def list_weeks():
    conn = get_conn()
    weeks = conn.execute("SELECT * FROM weeks ORDER BY number").fetchall()
    return [week_payload(conn, w) for w in weeks]


@app.get("/api/weeks/{number}")
def get_week(number: int):
    conn = get_conn()
    w = conn.execute("SELECT * FROM weeks WHERE number=?", (number,)).fetchone()
    if not w:
        raise HTTPException(404, "Week not found")
    return week_detail(conn, w, week_payload(conn, w))


@app.put("/api/weeks/{number}/journal")
def put_week_journal(number: int, payload: dict = Body(...)):
    """Replace your note and reason tags for a week. Empty both to clear it."""
    conn = get_conn()
    w = conn.execute("SELECT id FROM weeks WHERE number=?", (number,)).fetchone()
    if not w:
        raise HTTPException(404, "Week not found")
    note = (payload.get("note") or "").strip() or None
    raw = payload.get("reasons") or []
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        raise HTTPException(400, "Reasons must be a list of short texts.")
    reasons = list(dict.fromkeys(r.strip() for r in raw if r.strip()))  # dedupe, keep order
    if len(reasons) > MAX_REASONS or any(len(r) > MAX_REASON_CHARS for r in reasons):
        raise HTTPException(400, f"Up to {MAX_REASONS} reasons of {MAX_REASON_CHARS} characters each.")
    if note and len(note) > MAX_NOTE_CHARS:
        raise HTTPException(400, f"The note is limited to {MAX_NOTE_CHARS} characters.")
    if note is None and not reasons:
        conn.execute("DELETE FROM week_notes WHERE week_id=?", (w["id"],))
    else:
        conn.execute(
            """INSERT INTO week_notes (week_id, note, reasons, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(week_id) DO UPDATE SET
                 note=excluded.note, reasons=excluded.reasons, updated_at=excluded.updated_at""",
            (w["id"], note, json.dumps(reasons), datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()
    return week_journal(conn, w["id"])


@app.get("/api/block")
def block_overview():
    return block_payload(get_conn())


def block_payload(conn) -> dict:
    """Whole-block view: per-week totals plus one row per run for trend charts."""
    today = date.today().isoformat()
    zone_order = [z.name for z in get_zones(conn)]

    weeks_out = []
    planned_to_date = 0.0
    for w in conn.execute("SELECT * FROM weeks ORDER BY number").fetchall():
        wp = week_payload(conn, w)
        active = [s for s in wp["sessions"] if s["plan_status"] != "skipped"]
        planned_to_date += sum(s["target_km"] for s in active if s["planned_date"] <= today)
        long = next((s for s in active if s["label"] == "L"), None)
        long_act = long["activity"] if long else None

        zone_secs: dict[str, float] = {}
        for r in conn.execute(
            """SELECT zr.zone_name, SUM(zr.seconds) AS secs FROM zone_results zr
               JOIN activities a ON a.id = zr.activity_id WHERE a.week_id=? GROUP BY zr.zone_name""",
            (w["id"],),
        ):
            zone_secs[r["zone_name"]] = round(r["secs"])
            if r["zone_name"] not in zone_order:
                zone_order.append(r["zone_name"])
        unknown = conn.execute(
            """SELECT COALESCE(SUM(zr.unknown_hr_s), 0) FROM zone_runs zr
               JOIN activities a ON a.id = zr.activity_id WHERE a.week_id=?""",
            (w["id"],),
        ).fetchone()[0]
        ascent = conn.execute(
            "SELECT SUM(ascent_m) FROM activities WHERE week_id=?", (w["id"],)
        ).fetchone()[0]

        weeks_out.append({
            "number": wp["number"],
            "start_date": wp["start_date"],
            "end_date": wp["end_date"],
            "notes": wp["notes"],
            "journal": wp["journal"],
            "started": wp["start_date"] <= today,
            "current": wp["start_date"] <= today <= wp["end_date"],
            "planned_km": wp["planned_km"],
            "actual_km": wp["actual_km"],
            "actual_duration_s": wp["actual_duration_s"],
            "sessions_completed": wp["sessions_completed"],
            "sessions_planned": wp["sessions_planned"],
            "ascent_m": round(ascent) if ascent is not None else None,
            "long_run": {
                "planned_km": long["target_km"] if long else None,
                "actual_km": long_act["distance_km"] if long_act else None,
                "time_s": long_act["timer_time_s"] if long_act else None,
                "ascent_m": long_act["ascent_m"] if long_act else None,
            },
            "zone_seconds": zone_secs,
            "unknown_hr_s": round(unknown),
        })

    week_numbers = {w["id"]: w["number"] for w in conn.execute("SELECT id, number FROM weeks")}
    runs = []
    for a in conn.execute(
        """SELECT a.*, s.label FROM activities a LEFT JOIN sessions s ON s.id = a.session_id
           ORDER BY a.start_time"""
    ).fetchall():
        km_ = a["distance_m"] / 1000 if a["distance_m"] else None
        drift = drift_for(conn, a["id"])["drift_pct"] if a["has_hr"] else None
        runs.append({
            "id": a["id"],
            "start_time": a["start_time"],
            "week_number": week_numbers.get(a["week_id"]),
            "label": "extra" if a["is_extra"] else a["label"],
            "distance_km": round(km_, 2) if km_ else None,
            "timer_time_s": a["timer_time_s"],
            "avg_hr": a["avg_hr"],
            "pace_min_km": round(a["timer_time_s"] / 60 / km_, 2) if km_ and a["timer_time_s"] else None,
            "ascent_m": round(a["ascent_m"]) if a["ascent_m"] is not None else None,
            "hr_drift_pct": drift,
        })

    return {
        "weeks": weeks_out,
        "zone_names": zone_order,
        "runs": runs,
        "totals": {
            "planned_to_date_km": round(planned_to_date, 1),
            "actual_km": round(sum(w["actual_km"] for w in weeks_out), 1),
            "planned_block_km": round(sum(w["planned_km"] for w in weeks_out), 1),
            "runs": len(runs),
            "time_s": sum(w["actual_duration_s"] for w in weeks_out),
            "ascent_m": round(sum(r["ascent_m"] or 0 for r in runs)),
        },
    }


@app.patch("/api/sessions/{session_id}")
def update_session(session_id: int, payload: dict = Body(...)):
    """Edit a session: override_km, override_duration_min, override_date (a day of the
    same week), status ('planned' | 'skipped') and user_note. null clears a value."""
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
        raise HTTPException(404, "Session not found")
    try:
        s = write_session(conn, session_id, payload)
    except PlanError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    conn.commit()
    return {"id": s["id"], "seeded_km": s["seeded_km"], "target_km": target_km(s),
            "target_duration_min": target_duration(s), "planned_date": session_date(s),
            "plan_status": s["status"], "user_note": s["user_note"]}


@app.post("/api/uploads/preview")
async def upload_preview(file: UploadFile = File(...)):
    conn = get_conn()
    config.ensure_dirs()
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(400, "The file is too large.")
    try:
        fit_name, fit_bytes = extract_fit_bytes(file.filename or "", data)
        parsed = parse_fit(fit_bytes)
    except FitError as exc:
        raise HTTPException(400, str(exc))

    digest = sha256(fit_bytes)
    existing = conn.execute("SELECT * FROM activities WHERE file_hash=?", (digest,)).fetchone()
    if existing:
        return {
            "duplicate": True,
            "message": "This activity has already been imported.",
            "existing_activity": activity_dict(existing),
        }

    (config.STAGING_DIR / f"{digest}.fit").write_bytes(fit_bytes)
    local_date = parsed.start_time.astimezone().date().isoformat()
    week = week_for_date(conn, local_date)
    session = None
    if week:
        km_ = parsed.distance_m / 1000 if parsed.distance_m else None
        session = suggest_session(free_sessions(conn, week["id"]), date.fromisoformat(local_date), km_)
    return {
        "duplicate": False,
        "token": digest,
        "filename": fit_name,
        "start_time": parsed.start_time.isoformat(),
        "local_date": local_date,
        "distance_km": round(parsed.distance_m / 1000, 3) if parsed.distance_m else None,
        "timer_time_s": parsed.timer_time_s,
        "elapsed_time_s": parsed.elapsed_time_s,
        "duration_source": parsed.duration_source,
        "has_hr": parsed.has_hr,
        "avg_hr": parsed.avg_hr,
        "max_hr": parsed.max_hr,
        "sample_count": len(parsed.samples),
        "lap_count": len(parsed.laps),
        "suggested_week": week["number"] if week else None,
        "suggested_session_id": session["id"] if session else None,
    }


@app.post("/api/activities/confirm")
def confirm_upload(payload: dict = Body(...)):
    conn = get_conn()
    token = payload.get("token")
    staged = config.STAGING_DIR / f"{token}.fit"
    if not token or not staged.exists():
        raise HTTPException(400, "The preview has expired. Upload the file again.")
    if conn.execute("SELECT 1 FROM activities WHERE file_hash=?", (token,)).fetchone():
        raise HTTPException(409, "This activity has already been imported.")

    week_number = payload.get("week_number")
    session_id = payload.get("session_id")
    is_extra = bool(payload.get("is_extra"))
    week = conn.execute("SELECT * FROM weeks WHERE number=?", (week_number,)).fetchone()
    if not week:
        raise HTTPException(400, "Select a valid week.")
    if not is_extra:
        s = conn.execute("SELECT * FROM sessions WHERE id=? AND week_id=?",
                         (session_id, week["id"])).fetchone()
        if not s:
            raise HTTPException(400, "Select a session from that week.")
        if conn.execute("SELECT 1 FROM activities WHERE session_id=?", (session_id,)).fetchone():
            raise HTTPException(409, "That session already has a run assigned.")

    parsed = parse_fit(staged.read_bytes())
    stored = config.FILES_DIR / f"{token}.fit"
    shutil.move(str(staged), stored)
    aid = store_activity(
        conn, parsed, token, payload.get("filename") or stored.name, stored, week["id"],
        None if is_extra else session_id,
        extra_type=payload.get("extra_type"),
        rpe=payload.get("rpe"),
        note=payload.get("note"),
        is_demo=bool(payload.get("is_demo")),
    )
    return {"id": aid}


@app.get("/api/activities")
def list_activities():
    conn = get_conn()
    rows = conn.execute(
        """SELECT a.*, w.number AS week_number, s.label AS session_label FROM activities a
           LEFT JOIN weeks w ON w.id = a.week_id LEFT JOIN sessions s ON s.id = a.session_id
           ORDER BY a.start_time DESC"""
    ).fetchall()
    return [{**activity_dict(r), "week_number": r["week_number"], "session_label": r["session_label"]}
            for r in rows]


@app.get("/api/activities/{activity_id}")
def get_activity(activity_id: int):
    conn = get_conn()
    a = conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if not a:
        raise HTTPException(404, "Activity not found")
    samples = conn.execute(
        "SELECT ts, hr, distance_m, speed_mps, altitude_m FROM samples WHERE activity_id=? ORDER BY ts",
        (activity_id,),
    ).fetchall()
    start = samples[0]["ts"] if samples else 0
    series = [
        {
            "t": s["ts"] - start,
            "hr": s["hr"],
            "pace_min_km": round(1000 / s["speed_mps"] / 60, 3)
            if s["speed_mps"] and s["speed_mps"] > 0.5
            else None,
            "km": round(s["distance_m"] / 1000, 3) if s["distance_m"] is not None else None,
            "alt": round(s["altitude_m"], 1) if s["altitude_m"] is not None else None,
        }
        for s in samples
    ]
    laps = [dict(r) for r in conn.execute(
        "SELECT * FROM laps WHERE activity_id=? ORDER BY idx", (activity_id,))]
    return {
        **activity_dict(a),
        "series": series,
        "laps": laps,
        "zones": zone_payload(conn, activity_id),
        "hr_drift": drift_for(conn, activity_id) if a["has_hr"] else None,
    }


@app.post("/api/activities/{activity_id}/assign")
def reassign(activity_id: int, payload: dict = Body(...)):
    conn = get_conn()
    a = conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if not a:
        raise HTTPException(404, "Activity not found")
    week = conn.execute("SELECT * FROM weeks WHERE number=?", (payload.get("week_number"),)).fetchone()
    if not week:
        raise HTTPException(400, "Select a valid week.")
    is_extra = bool(payload.get("is_extra"))
    session_id = payload.get("session_id")
    if not is_extra:
        s = conn.execute("SELECT * FROM sessions WHERE id=? AND week_id=?",
                         (session_id, week["id"])).fetchone()
        if not s:
            raise HTTPException(400, "Select a session from that week.")
        taken = conn.execute(
            "SELECT id FROM activities WHERE session_id=? AND id<>?", (session_id, activity_id)
        ).fetchone()
        if taken:
            raise HTTPException(409, "That session already has a run assigned.")
    # start_time is never modified when moving an activity to another week.
    conn.execute(
        "UPDATE activities SET week_id=?, session_id=?, is_extra=?, extra_type=?, auto_assigned=0 WHERE id=?",
        (week["id"], None if is_extra else session_id, int(is_extra),
         payload.get("extra_type") if is_extra else None, activity_id),
    )
    conn.commit()
    return activity_dict(conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone())


@app.patch("/api/activities/{activity_id}")
def annotate(activity_id: int, payload: dict = Body(...)):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM activities WHERE id=?", (activity_id,)).fetchone():
        raise HTTPException(404, "Activity not found")
    if "rpe" in payload:
        conn.execute("UPDATE activities SET rpe=? WHERE id=?", (payload["rpe"], activity_id))
    if "note" in payload:
        conn.execute("UPDATE activities SET note=? WHERE id=?", (payload["note"], activity_id))
    conn.commit()
    return activity_dict(conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone())


@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, confirm: bool = False):
    conn = get_conn()
    a = conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if not a:
        raise HTTPException(404, "Activity not found")
    if not confirm:
        raise HTTPException(400, "Missing confirmation to delete the run.")
    conn.execute("DELETE FROM samples WHERE activity_id=?", (activity_id,))
    conn.execute("DELETE FROM laps WHERE activity_id=?", (activity_id,))
    conn.execute("DELETE FROM timer_events WHERE activity_id=?", (activity_id,))
    conn.execute("DELETE FROM zone_results WHERE activity_id=?", (activity_id,))
    conn.execute("DELETE FROM zone_runs WHERE activity_id=?", (activity_id,))
    conn.execute("DELETE FROM activities WHERE id=?", (activity_id,))
    conn.commit()
    p = Path(a["stored_path"])
    if p.exists():
        p.unlink()
    return {"deleted": activity_id, "plan_untouched": True}


@app.get("/api/inbox")
def inbox_status():
    conn = get_conn()
    rows = conn.execute(
        """SELECT f.filename, f.status, f.message, f.seen_at, f.activity_id,
                  a.start_time, a.distance_m, a.is_extra, a.auto_assigned,
                  w.number AS week_number, s.label AS session_label
           FROM inbox_files f
           LEFT JOIN activities a ON a.id = f.activity_id
           LEFT JOIN weeks w ON w.id = a.week_id
           LEFT JOIN sessions s ON s.id = a.session_id
           ORDER BY f.id DESC LIMIT 20"""
    ).fetchall()
    return {
        "path": str(config.INBOX_DIR),
        "exists": config.INBOX_DIR.is_dir(),
        "is_default": config.INBOX_DIR == config.DEFAULT_INBOX_DIR,
        "poll_seconds": config.INBOX_POLL_SECONDS,
        "recent": [
            {
                "filename": r["filename"],
                "status": r["status"],
                "message": r["message"],
                "seen_at": r["seen_at"],
                "activity_id": r["activity_id"],
                "start_time": r["start_time"],
                "distance_km": round(r["distance_m"] / 1000, 2) if r["distance_m"] else None,
                "week_number": r["week_number"],
                "session_label": "extra" if r["is_extra"] else r["session_label"],
                "auto_assigned": bool(r["auto_assigned"]) if r["activity_id"] else None,
            }
            for r in rows
        ],
    }


@app.post("/api/inbox/scan")
def inbox_scan():
    return {"handled": scan_inbox(get_conn(), config.INBOX_DIR)}


@app.get("/api/settings")
def get_settings():
    conn = get_conn()
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return {
        "units": {"distance": "km", "pace": "min/km"},
        "zones": json.loads(rows.get("zones", "[]")),
    }


@app.put("/api/settings/zones")
def put_zones(payload: dict = Body(...)):
    conn = get_conn()
    raw = payload.get("zones", [])
    try:
        parsed = zonelib.parse_zones(raw)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Invalid zones. Provide a name, minimum and maximum heart rate.")
    for z in parsed:
        if z.min_hr <= 0 or z.max_hr <= z.min_hr:
            raise HTTPException(400, f"Zone '{z.name}': the maximum must be greater than the minimum.")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('zones', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps([z.__dict__ for z in parsed]),),
    )
    conn.commit()
    return {"zones": [z.__dict__ for z in parsed], "note": "Recalculate to apply to runs already imported."}


@app.post("/api/zones/recalculate")
def recalc_all():
    conn = get_conn()
    ids = [r["id"] for r in conn.execute("SELECT id FROM activities")]
    for aid in ids:
        recompute_zones(conn, aid)
    return {"recalculated": len(ids)}
