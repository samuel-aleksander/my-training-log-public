"""Read-only MCP server: lets Claude Desktop look at the plan and your runs.

Run from backend/ (Claude Desktop starts it for you, see the README):

    .venv/bin/python -m app.mcp_server

Every tool only reads. Each call opens its own connection with
PRAGMA query_only, so nothing here can change the plan or your runs.

What these tools return is what leaves your machine: per-run and per-week
summaries, laps, zones, notes, and the two private docs. Never the recorded
samples, the original files or any position.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import config
from .db import connect
from .main import activity_dict, block_payload, drift_for, get_zones, run_summary, target_km, week_detail, week_payload
from .seed import SESSION_DAYS, load_plan

INSTRUCTIONS = """\
Training log for one athlete (the user) following a 13-week marathon base block.

- Get every number from these tools; never estimate or invent one. Distances are km,
  paces min:sec per km, heart rate bpm, times minutes.
- Sessions per week: A (Tuesday, easy), B (Thursday, longer easy), C (Saturday, short
  and comfortable), L (Sunday, easy long run). Extra runs are unplanned and count in the
  week's total.
- "skipped" means the planned date has passed with no run assigned to the session.
- HR zones are the athlete's own limits. HR drift % is how much pace per heartbeat drops
  from the first to the second half of a run (after a 10-minute warm-up): positive means
  the heart rate crept up. The app applies no threshold to it.
- The athlete profile records past left-knee pain: watch the notes for knee mentions.
- These tools are read-only: you cannot change the plan. When you recommend a change,
  state it precisely (week, session, from -> to, reason); the athlete applies it.
"""

mcp = MCPServer("marathon-training-log", instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


@contextmanager
def read_db():
    if not config.DB_PATH.exists():
        raise ToolError("There is no database yet. Start the app once with ./dev.sh so the plan is seeded.")
    conn = connect()
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def find_week(conn, number: int):
    w = conn.execute("SELECT * FROM weeks WHERE number=?", (number,)).fetchone()
    if not w:
        last = conn.execute("SELECT MAX(number) FROM weeks").fetchone()[0]
        raise ToolError(f"There is no week {number}. The block has weeks 1 to {last}.")
    return w


# --------------------------------------------------------------------------- formatting


def pace(min_per_km: float | None) -> str | None:
    """6.97 -> "6:58". Decimal minutes are easy to misread as min:sec."""
    if not min_per_km:
        return None
    m, s = divmod(round(min_per_km * 60), 60)
    return f"{m}:{s:02d}"


def minutes(seconds: float | None) -> float | None:
    return round(seconds / 60, 1) if seconds else None


def zone_minutes(seconds: dict[str, float], unknown_s: float) -> dict | None:
    """{zone: {"min", "pct"}} with pct of the time that had a heart rate."""
    measured = sum(seconds.values())
    if not measured:
        return None
    out = {n: {"min": minutes(s) or 0, "pct": round(s / measured * 100, 1)} for n, s in seconds.items()}
    if unknown_s:
        out["no heart rate"] = {"min": minutes(unknown_s), "pct": None}
    return out


def run_brief(r: dict) -> dict:
    """A run_summary trimmed to what a review needs."""
    z = r.get("zones")
    return {
        "run_id": r["id"],
        "start_time": r["start_time"],
        "distance_km": r["distance_km"],
        "moving_time_min": minutes(r["timer_time_s"]),
        "pace_per_km": pace(r["pace_min_km"]),
        "avg_hr": r["avg_hr"],
        "max_hr": r["max_hr"],
        "ascent_m": r["ascent_m"],
        "descent_m": r["descent_m"],
        "hr_drift_pct": r["hr_drift_pct"],
        "zones": zone_minutes({b["name"]: b["seconds"] for b in z["buckets"]}, z["unknown_hr_s"]) if z else None,
        "rpe": r["rpe"],
        "note": r["note"],
        **({"extra_type": r["extra_type"]} if r["is_extra"] else {}),
    }


def week_status(w: dict) -> str:
    if w["current"]:
        return "current"
    return "done" if w["started"] else "upcoming"


# --------------------------------------------------------------------------- tools


@mcp.tool(annotations=READ_ONLY)
def get_plan_overview() -> dict:
    """Start here. Today's date, the current week, the plan's guidance, your HR zones, and
    one line per week: planned vs actual km, sessions done, long run, the plan's note for
    the week and your own note and reason tags."""
    plan = load_plan()
    with read_db() as conn:
        block = block_payload(conn)
        zones = [z.__dict__ for z in get_zones(conn)]
    weeks = block["weeks"]
    totals = block["totals"]
    return {
        "today": date.today().isoformat(),
        "current_week": next((w["number"] for w in weeks if w["current"]), None),
        "block_start": weeks[0]["start_date"] if weeks else None,
        "block_end": weeks[-1]["end_date"] if weeks else None,
        "effort_guidance": plan["effort_context"],
        "review_weeks": plan.get("review_weeks", []),
        "session_types": {label: desc for label, (_, desc) in SESSION_DAYS.items()},
        "hr_zones": zones,
        "totals": {
            "planned_to_date_km": totals["planned_to_date_km"],
            "actual_km": totals["actual_km"],
            "planned_block_km": totals["planned_block_km"],
            "runs": totals["runs"],
            "moving_time_h": round(totals["time_s"] / 3600, 1),
            "ascent_m": totals["ascent_m"],
        },
        "weeks": [
            {
                "week": w["number"],
                "start_date": w["start_date"],
                "end_date": w["end_date"],
                "status": week_status(w),
                "planned_km": w["planned_km"],
                "actual_km": w["actual_km"],
                "sessions_completed": w["sessions_completed"],
                "sessions_planned": w["sessions_planned"],
                "long_run_planned_km": w["long_run"]["planned_km"],
                "long_run_actual_km": w["long_run"]["actual_km"],
                "plan_note": w["notes"],
                "your_note": w["journal"]["note"],
                "reasons": w["journal"]["reasons"],
            }
            for w in weeks
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def get_week(week: int) -> dict:
    """One week in detail: each planned session (target, status and the run done for it,
    with pace, HR, drift, zones, RPE and note), extra runs, week totals and your note."""
    with read_db() as conn:
        w = find_week(conn, week)
        d = week_detail(conn, w, week_payload(conn, w))
    z = d["zones"]
    return {
        "week": d["number"],
        "start_date": d["start_date"],
        "end_date": d["end_date"],
        "plan_note": d["notes"],
        "planned_km": d["planned_km"],
        "plan_original_km": d["seeded_planned_km"],
        "actual_km": d["actual_km"],
        "diff_km": d["diff_km"],
        "completion_pct": d["completion_pct"],
        "sessions_completed": d["sessions_completed"],
        "sessions_planned": d["sessions_planned"],
        "moving_time_min": minutes(d["actual_duration_s"]),
        "avg_hr": d["avg_hr"],
        "avg_pace_per_km": pace(d["avg_pace_min_km"]),
        "ascent_m": d["ascent_m"],
        "zones": zone_minutes(z["seconds"], z["unknown_hr_s"]) if z else None,
        "your_note": d["journal"]["note"],
        "reasons": d["journal"]["reasons"],
        "sessions": [
            {
                "session_id": s["id"],
                "label": s["label"],
                "planned_date": s["planned_date"],
                "weekday": date.fromisoformat(s["planned_date"]).strftime("%A"),
                "target_km": s["target_km"],
                "plan_original_km": s["seeded_km"],
                "target_duration_min": s["target_duration_min"],
                "description": s["notes"],
                "status": s["status"],
                "diff_km": s["diff_km"],
                "run": run_brief(s["activity"]) if s["activity"] else None,
            }
            for s in d["sessions"]
        ],
        "extra_runs": [run_brief(e) for e in d["extras"]],
    }


@mcp.tool(annotations=READ_ONLY)
def get_run(run_id: int) -> dict:
    """One run in depth: the summary, the session it was for and that session's target,
    lap splits and both halves of the HR drift calculation."""
    with read_db() as conn:
        a = conn.execute(
            """SELECT a.*, w.number AS week_number FROM activities a
               LEFT JOIN weeks w ON w.id = a.week_id WHERE a.id=?""",
            (run_id,),
        ).fetchone()
        if not a:
            raise ToolError(f"There is no run {run_id}. list_runs shows the run ids.")
        r = run_brief(run_summary(conn, activity_dict(a)))
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (a["session_id"],)).fetchone()
        drift = drift_for(conn, run_id) if a["has_hr"] else None
        laps = conn.execute(
            "SELECT idx, distance_m, timer_time_s, avg_hr, max_hr FROM laps WHERE activity_id=? ORDER BY idx",
            (run_id,),
        ).fetchall()
    return {
        **r,
        "week": a["week_number"],
        "session": s["label"] if s else "extra",
        "session_target_km": target_km(s) if s else None,
        "hr_drift_detail": drift and {
            "drift_pct": drift["drift_pct"],
            "analysed_min": minutes(drift["analysed_s"]),
            "first_half": drift.get("first"),
            "second_half": drift.get("second"),
            "reason": drift.get("reason"),
        },
        "laps": [
            {
                "lap": l["idx"] + 1,
                "km": round(l["distance_m"] / 1000, 2) if l["distance_m"] else None,
                "time_min": minutes(l["timer_time_s"]),
                "pace_per_km": pace(l["timer_time_s"] / 60 / (l["distance_m"] / 1000))
                if l["timer_time_s"] and l["distance_m"]
                else None,
                "avg_hr": l["avg_hr"],
                "max_hr": l["max_hr"],
            }
            for l in laps
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def list_runs(from_week: int | None = None, to_week: int | None = None) -> list[dict]:
    """Every run in date order, optionally limited to a range of weeks: date, week,
    session (or extra), distance, time, pace, HR, drift, climb, RPE and note.
    Use get_run with a run_id for laps and zones."""
    with read_db() as conn:
        rows = conn.execute(
            """SELECT a.*, w.number AS week_number, s.label FROM activities a
               LEFT JOIN weeks w ON w.id = a.week_id LEFT JOIN sessions s ON s.id = a.session_id
               WHERE (? IS NULL OR w.number >= ?) AND (? IS NULL OR w.number <= ?)
               ORDER BY a.start_time""",
            (from_week, from_week, to_week, to_week),
        ).fetchall()
        out = []
        for a in rows:
            r = activity_dict(a)
            km_ = r["distance_km"]
            out.append({
                "run_id": a["id"],
                "start_time": a["start_time"],
                "week": a["week_number"],
                "session": "extra" if a["is_extra"] else a["label"],
                "distance_km": km_,
                "moving_time_min": minutes(a["timer_time_s"]),
                "pace_per_km": pace(a["timer_time_s"] / 60 / km_) if km_ and a["timer_time_s"] else None,
                "avg_hr": a["avg_hr"],
                "hr_drift_pct": drift_for(conn, a["id"])["drift_pct"] if a["has_hr"] else None,
                "ascent_m": r["ascent_m"],
                "rpe": a["rpe"],
                "note": a["note"],
            })
    return out


@mcp.tool(annotations=READ_ONLY)
def get_block_trends() -> dict:
    """Week-by-week trends, calculated by the app: km planned vs run and the change from
    the previous week, long run, average pace and HR, average HR drift, share of time in
    each zone and average RPE. Upcoming weeks only show their planned km."""
    with read_db() as conn:
        block = block_payload(conn)
        rpe = {
            r["number"]: (r["avg_rpe"], r["rated"])
            for r in conn.execute(
                """SELECT w.number, AVG(a.rpe) AS avg_rpe, COUNT(a.rpe) AS rated FROM activities a
                   JOIN weeks w ON w.id = a.week_id GROUP BY w.number"""
            )
        }
    runs_by_week: dict[int, list[dict]] = {}
    for r in block["runs"]:
        runs_by_week.setdefault(r["week_number"], []).append(r)

    weeks = []
    prev_actual = None
    for w in block["weeks"]:
        row = {"week": w["number"], "status": week_status(w), "planned_km": w["planned_km"]}
        if not w["started"]:
            weeks.append(row)
            continue
        runs = runs_by_week.get(w["number"], [])
        timed = [r for r in runs if r["distance_km"] and r["timer_time_s"]]
        km_ = sum(r["distance_km"] for r in timed)
        secs = sum(r["timer_time_s"] for r in timed)
        with_hr = [r for r in timed if r["avg_hr"]]
        hr_secs = sum(r["timer_time_s"] for r in with_hr)
        drifts = [r["hr_drift_pct"] for r in runs if r["hr_drift_pct"] is not None]
        avg_rpe, rated = rpe.get(w["number"], (None, 0))
        row.update({
            "actual_km": w["actual_km"],
            "completion_pct": round(w["actual_km"] / w["planned_km"] * 100, 1) if w["planned_km"] else None,
            "change_vs_prev_week_pct": round((w["actual_km"] - prev_actual) / prev_actual * 100, 1)
            if prev_actual
            else None,
            "runs": len(runs),
            "extra_runs": sum(1 for r in runs if r["label"] == "extra"),
            "sessions_completed": w["sessions_completed"],
            "sessions_planned": w["sessions_planned"],
            "long_run_planned_km": w["long_run"]["planned_km"],
            "long_run_actual_km": w["long_run"]["actual_km"],
            "moving_time_min": minutes(w["actual_duration_s"]),
            "avg_pace_per_km": pace(secs / 60 / km_) if km_ else None,
            "avg_hr": round(sum(r["avg_hr"] * r["timer_time_s"] for r in with_hr) / hr_secs) if hr_secs else None,
            "avg_hr_drift_pct": round(sum(drifts) / len(drifts), 1) if drifts else None,
            "runs_with_drift": len(drifts),
            "zones_pct": {n: v["pct"] for n, v in (zone_minutes(w["zone_seconds"], 0) or {}).items()},
            "avg_rpe": round(avg_rpe, 1) if avg_rpe is not None else None,
            "runs_with_rpe": rated,
            "ascent_m": w["ascent_m"],
            "reasons": w["journal"]["reasons"],
        })
        prev_actual = w["actual_km"]
        weeks.append(row)

    distances = [r["distance_km"] for r in block["runs"] if r["distance_km"]]
    totals = block["totals"]
    return {
        "planned_to_date_km": totals["planned_to_date_km"],
        "actual_km": totals["actual_km"],
        "pct_of_plan_to_date": round(totals["actual_km"] / totals["planned_to_date_km"] * 100, 1)
        if totals["planned_to_date_km"]
        else None,
        "longest_run_km": max(distances) if distances else None,
        "zone_names": block["zone_names"],
        "weeks": weeks,
    }


@mcp.tool(annotations=READ_ONLY)
def get_notes(from_week: int | None = None, to_week: int | None = None) -> list[dict]:
    """Everything you wrote, week by week: your week note and reason tags, and each run's
    RPE and note. Weeks with nothing written are left out."""
    with read_db() as conn:
        weeks = conn.execute(
            """SELECT w.id, w.number, n.note, n.reasons FROM weeks w
               LEFT JOIN week_notes n ON n.week_id = w.id
               WHERE (? IS NULL OR w.number >= ?) AND (? IS NULL OR w.number <= ?)
               ORDER BY w.number""",
            (from_week, from_week, to_week, to_week),
        ).fetchall()
        out = []
        for w in weeks:
            runs = conn.execute(
                """SELECT a.id, a.start_time, a.distance_m, a.rpe, a.note, a.is_extra, s.label
                   FROM activities a LEFT JOIN sessions s ON s.id = a.session_id
                   WHERE a.week_id=? AND (a.rpe IS NOT NULL OR COALESCE(a.note, '') <> '')
                   ORDER BY a.start_time""",
                (w["id"],),
            ).fetchall()
            reasons = json.loads(w["reasons"]) if w["reasons"] else []
            if not (w["note"] or reasons or runs):
                continue
            out.append({
                "week": w["number"],
                "your_note": w["note"],
                "reasons": reasons,
                "runs": [
                    {
                        "run_id": r["id"],
                        "start_time": r["start_time"],
                        "session": "extra" if r["is_extra"] else r["label"],
                        "distance_km": round(r["distance_m"] / 1000, 2) if r["distance_m"] else None,
                        "rpe": r["rpe"],
                        "note": r["note"],
                    }
                    for r in runs
                ],
            })
    return out


def private_doc(name: str) -> str:
    path = config.PRIVATE_DOCS_DIR / name
    if not path.exists():
        raise ToolError(f"{name} is not in {config.PRIVATE_DOCS_DIR}.")
    return path.read_text(encoding="utf-8")


@mcp.tool(annotations=READ_ONLY)
def get_athlete_profile() -> str:
    """The athlete's starting point, availability, injury history and watch settings."""
    return private_doc("athlete-profile.md")


@mcp.tool(annotations=READ_ONLY)
def get_plan_document() -> str:
    """The training plan in prose: its goals, principles and week-by-week intent. This is
    the source of truth for why each week looks the way it does."""
    return private_doc("plan-base-13-semanas.md")


# --------------------------------------------------------------------------- prompts


@mcp.prompt(title="Review my block")
def review_block() -> str:
    """Review the block so far and suggest adjustments for the coming weeks."""
    return (
        "Review my training block so far. First call get_plan_overview, get_block_trends, "
        "get_notes and get_athlete_profile, and read get_plan_document for the plan's intent. "
        "Then tell me:\n"
        "1. How the block is going against the plan.\n"
        "2. What the trends (HR drift, HR, zones, pace, long run) say about my aerobic base.\n"
        "3. Warning signs: missed sessions, jumps in load, high RPE, any knee mentions.\n"
        "4. Concrete adjustments for the coming weeks, each as week, session, from -> to, and why.\n"
        "Quote the numbers the tools return; don't estimate them."
    )


@mcp.prompt(title="Discuss a week")
def discuss_week(week: int) -> str:
    """Talk through one week of the plan."""
    return (
        f"Let's talk about week {week}. Call get_week({week}) and get_plan_overview for "
        "context, summarise in a few lines how the week went against the plan, then ask me "
        "what I want to discuss."
    )


if __name__ == "__main__":
    mcp.run()
