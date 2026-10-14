from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone, date
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import config, zones as zonelib
from .db import connect, init_db
from .fit_parser import PARSER_VERSION, FitError, ParsedActivity, extract_fit_bytes, parse_fit, sha256
from .metrics import hr_drift
from .seed import effort_context, seed

app = FastAPI(title="Marathon base-block tracker")
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


def target_km(s: sqlite3.Row) -> float:
    return s["override_km"] if s["override_km"] is not None else s["seeded_km"]


def target_duration(s: sqlite3.Row) -> float | None:
    if s["override_duration_min"] is not None:
        return s["override_duration_min"]
    return s["seeded_duration_min"]


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
        "week_id": a["week_id"],
        "session_id": a["session_id"],
    }


def week_payload(conn, week: sqlite3.Row) -> dict:
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE week_id=? ORDER BY planned_date", (week["id"],)
    ).fetchall()
    today = date.today().isoformat()

    session_items = []
    planned_total = 0.0
    actual_total = 0.0
    completed = 0
    duration_total = 0.0
    for s in sessions:
        tkm = target_km(s)
        planned_total += tkm
        act = conn.execute("SELECT * FROM activities WHERE session_id=?", (s["id"],)).fetchone()
        actual_km = None
        if act:
            actual_km = round(act["distance_m"] / 1000, 3) if act["distance_m"] is not None else None
            actual_total += actual_km or 0
            duration_total += act["timer_time_s"] or 0
            completed += 1
            status = "completed"
        else:
            status = "planned" if s["planned_date"] >= today else "skipped"
        tdur = target_duration(s)
        session_items.append(
            {
                "id": s["id"],
                "label": s["label"],
                "planned_date": s["planned_date"],
                "seeded_km": s["seeded_km"],
                "target_km": tkm,
                "target_duration_min": tdur,
                "target_duration_display": f"{tdur:g} min" if tdur is not None else "Not set",
                "notes": s["notes"],
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
        "sessions_planned": len(sessions),
        "actual_duration_s": round(duration_total),
        "sessions": session_items,
        "extras": [activity_dict(e) for e in extras],
    }


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
    return week_payload(conn, w)


@app.get("/api/block")
def block_overview():
    """Whole-block view: per-week totals plus one row per run for trend charts."""
    conn = get_conn()
    today = date.today().isoformat()
    zone_order = [z.name for z in get_zones(conn)]

    weeks_out = []
    planned_to_date = 0.0
    for w in conn.execute("SELECT * FROM weeks ORDER BY number").fetchall():
        wp = week_payload(conn, w)
        planned_to_date += sum(s["target_km"] for s in wp["sessions"] if s["planned_date"] <= today)
        long = next((s for s in wp["sessions"] if s["label"] == "L"), None)
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
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Session not found")
    fields = {}
    if "override_km" in payload:
        fields["override_km"] = payload["override_km"]
    if "override_duration_min" in payload:
        fields["override_duration_min"] = payload["override_duration_min"]
    for k, v in fields.items():
        conn.execute(f"UPDATE sessions SET {k}=? WHERE id=?", (v, session_id))
    conn.commit()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return {"id": s["id"], "seeded_km": s["seeded_km"], "target_km": target_km(s),
            "target_duration_min": target_duration(s)}


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

    cur = conn.execute(
        """INSERT INTO activities
           (file_hash, original_filename, stored_path, start_time, distance_m, timer_time_s,
            elapsed_time_s, duration_source, avg_hr, max_hr, has_hr, sport,
            ascent_m, descent_m, elevation_source, parser_version, is_demo,
            week_id, session_id, is_extra, extra_type, rpe, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            token,
            payload.get("filename") or stored.name,
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
            int(bool(payload.get("is_demo"))),
            week["id"],
            None if is_extra else session_id,
            int(is_extra),
            payload.get("extra_type") if is_extra else None,
            payload.get("rpe"),
            payload.get("note"),
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
    return {"id": aid}


@app.get("/api/activities")
def list_activities():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM activities ORDER BY start_time DESC").fetchall()
    return [activity_dict(r) for r in rows]


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
        "UPDATE activities SET week_id=?, session_id=?, is_extra=?, extra_type=? WHERE id=?",
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
