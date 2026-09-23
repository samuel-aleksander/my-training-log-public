"""Seeds the training plan from a JSON file (see config.PLAN_PATH).

Your own plan lives in docs/private/plan.json, which is gitignored; without it
the committed sample plan is used.

Seeding is idempotent: existing weeks/sessions are never overwritten, so user
edits (overrides, notes) and assignments survive every restart.
"""

from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from . import config

# Day offset from Monday, and the session description from "Week organization".
SESSION_DAYS = {
    "A": (1, "Tuesday — Easy run. On the weeks indicated, short strides within the total distance."),
    "B": (3, "Thursday — Slightly longer easy run; gradual introduction of rolling terrain."),
    "C": (5, "Saturday — Short run, and especially comfortable."),
    "L": (6, "Sunday — Easy long run, no fast finish. Practical limit of 2 h (a cap, not a target)."),
}

REVIEW_NOTE = (
    " Review week: within run A, 10 min very gentle and then 20 min comfortable "
    "on a familiar flat stretch, without chasing a time."
)


@lru_cache
def load_plan(path: Path = config.PLAN_PATH) -> dict:
    """Plan file: {"effort_context": str, "review_weeks": [int],
    "weeks": [{"number", "start", "A", "B", "C", "L", "total", "note"}]}."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def effort_context() -> str:
    """Plan-level context shown in the UI. No target numbers are invented here."""
    return load_plan()["effort_context"]


def seed(conn: sqlite3.Connection) -> dict:
    plan = load_plan()
    review_weeks = set(plan["review_weeks"])
    created_weeks = 0
    created_sessions = 0
    for wk in plan["weeks"]:
        number, start, total, note = wk["number"], wk["start"], wk["total"], wk["note"]
        start_d = date.fromisoformat(start)
        end_d = start_d + timedelta(days=6)
        row = conn.execute("SELECT id FROM weeks WHERE number = ?", (number,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO weeks (number, start_date, end_date, planned_km, notes) VALUES (?,?,?,?,?)",
                (number, start_d.isoformat(), end_d.isoformat(), float(total), note),
            )
            week_id = cur.lastrowid
            created_weeks += 1
        else:
            week_id = row["id"]

        for label in ("A", "B", "C", "L"):
            km = wk[label]
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE week_id = ? AND label = ?", (week_id, label)
            ).fetchone()
            if exists:
                continue
            offset, desc = SESSION_DAYS[label]
            session_note = desc
            if label == "A" and number in review_weeks:
                session_note += REVIEW_NOTE
            conn.execute(
                """INSERT INTO sessions
                   (week_id, label, planned_date, seeded_km, seeded_duration_min, notes)
                   VALUES (?,?,?,?,NULL,?)""",
                (week_id, label, (start_d + timedelta(days=offset)).isoformat(), float(km), session_note),
            )
            created_sessions += 1

    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('zones', '[]')",
    )
    conn.commit()
    return {"weeks_created": created_weeks, "sessions_created": created_sessions}
