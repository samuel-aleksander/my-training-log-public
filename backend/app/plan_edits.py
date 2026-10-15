"""Writing to a planned session: the one place edits to the plan go through.

Used by the web app (PATCH /api/sessions/{id}) and by apply/revert in
plan_changes, so both check the same things. Only value-level rules live here
(bounds, dates inside the session's week, one session per day); the rules
about what a chat may change (past sessions, the knee, training load) are in
plan_changes.

The plan's own values (seeded_km, planned_date, notes) are never written.
Nothing here commits: the caller does, so an edit and its log entry land together.
"""

from __future__ import annotations

import sqlite3
from datetime import date

MIN_KM, MAX_KM = 1.0, 42.2
MIN_DURATION_MIN, MAX_DURATION_MIN = 5.0, 300.0
MAX_SESSION_NOTE_CHARS = 2000

# Columns an edit may write, and the plan_status values a session can have.
EDITABLE = ("override_km", "override_duration_min", "override_date", "status", "user_note")
STATUSES = ("planned", "skipped", "added")
ADDED_NOTE = "Added session (not in the plan)."


class PlanError(ValueError):
    """An edit that breaks a data rule. The message is shown as is."""


def target_km(s) -> float:
    return s["override_km"] if s["override_km"] is not None else s["seeded_km"]


def target_duration(s) -> float | None:
    if s["override_duration_min"] is not None:
        return s["override_duration_min"]
    return s["seeded_duration_min"]


def session_date(s) -> str:
    """The day the session is on now: moved, or as planned."""
    return s["override_date"] or s["planned_date"]


def check_km(km) -> float:
    if isinstance(km, bool) or not isinstance(km, (int, float)) or not MIN_KM <= km <= MAX_KM:
        raise PlanError(f"Distance must be between {MIN_KM:g} and {MAX_KM:g} km.")
    return float(km)


def check_duration(minutes) -> float:
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not (
        MIN_DURATION_MIN <= minutes <= MAX_DURATION_MIN
    ):
        raise PlanError(f"Duration must be between {MIN_DURATION_MIN:g} and {MAX_DURATION_MIN:g} minutes.")
    return float(minutes)


def check_note(note) -> str | None:
    if note is not None and not isinstance(note, str):
        raise PlanError("The note must be text.")
    note = (note or "").strip() or None
    if note and len(note) > MAX_SESSION_NOTE_CHARS:
        raise PlanError(f"The note is limited to {MAX_SESSION_NOTE_CHARS} characters.")
    return note


def parse_date(value) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise PlanError(f"{value!r} is not a date (YYYY-MM-DD).") from None


def week_for(conn, iso_date: str):
    return conn.execute(
        "SELECT * FROM weeks WHERE start_date <= ? AND end_date >= ?", (iso_date, iso_date)
    ).fetchone()


def check_day_free(conn, iso_date: str, except_id: int | None = None) -> None:
    """One session per day. Skipped sessions don't hold their day."""
    other = conn.execute(
        """SELECT s.label, w.number FROM sessions s JOIN weeks w ON w.id = s.week_id
           WHERE COALESCE(s.override_date, s.planned_date) = ? AND s.status <> 'skipped'
             AND s.id IS NOT ?""",
        (iso_date, except_id),
    ).fetchone()
    if other:
        raise PlanError(f"{iso_date} already has a session (week {other['number']}, {other['label']}).")


def check_date_in_block(conn, iso_date: str):
    w = week_for(conn, iso_date)
    if not w:
        first, last = conn.execute("SELECT MIN(start_date), MAX(end_date) FROM weeks").fetchone()
        raise PlanError(f"{iso_date} is outside the block ({first} to {last}).")
    return w


def write_session(conn: sqlite3.Connection, session_id: int, fields: dict) -> sqlite3.Row:
    """Validate and write `fields` (a subset of EDITABLE) on one session.

    None clears an override or the note. Raises PlanError, writing nothing, if
    any field breaks a rule.
    """
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        raise PlanError(f"There is no session {session_id}.")
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise PlanError(f"These fields can't be edited: {', '.join(sorted(unknown))}.")

    clean = {}
    for k, v in fields.items():
        if k == "override_km":
            clean[k] = None if v is None else check_km(v)
        elif k == "override_duration_min":
            clean[k] = None if v is None else check_duration(v)
        elif k == "user_note":
            clean[k] = check_note(v)
        elif k == "override_date":
            if v is None or v == s["planned_date"]:
                clean[k] = None
            else:
                d = parse_date(v).isoformat()
                w = check_date_in_block(conn, d)
                if w["id"] != s["week_id"]:
                    raise PlanError(
                        "A session can only move within its own week. To run it in another "
                        "week, skip it here and add one there."
                    )
                clean[k] = d
        elif k == "status":
            if v not in STATUSES:
                raise PlanError(f"Status must be one of {', '.join(STATUSES)}.")
            if (v == "added") != (s["status"] == "added"):
                raise PlanError("Only sessions added outside the plan are 'added'; plan sessions stay in the plan.")
            clean[k] = v

    # One session per day, checked against the session as it will be.
    new = {**dict(s), **clean}
    if new["status"] != "skipped" and (
        "override_date" in clean or ("status" in clean and s["status"] == "skipped")
    ):
        check_day_free(conn, session_date(new), except_id=session_id)

    for k, v in clean.items():
        conn.execute(f"UPDATE sessions SET {k}=? WHERE id=?", (v, session_id))
    return conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()


def add_session(conn, iso_date: str, km, duration_min=None, note=None) -> int:
    """A session outside the plan. It counts toward the week's planned km."""
    d = parse_date(iso_date).isoformat()
    w = check_date_in_block(conn, d)
    km = check_km(km)
    duration_min = None if duration_min is None else check_duration(duration_min)
    note = check_note(note)
    check_day_free(conn, d)
    taken = {r["label"] for r in conn.execute("SELECT label FROM sessions WHERE week_id=?", (w["id"],))}
    label = next(f"X{i}" for i in range(1, 100) if f"X{i}" not in taken)
    cur = conn.execute(
        """INSERT INTO sessions (week_id, label, planned_date, seeded_km, seeded_duration_min,
                                 notes, status, user_note)
           VALUES (?,?,?,?,?,?, 'added', ?)""",
        (w["id"], label, d, km, duration_min, ADDED_NOTE, note),
    )
    return cur.lastrowid


def remove_added_session(conn, session_id: int) -> None:
    """Only sessions added outside the plan can go; plan sessions are skipped instead."""
    s = conn.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        raise PlanError(f"There is no session {session_id}.")
    if s["status"] != "added":
        raise PlanError("Plan sessions are never deleted; skip them instead.")
    if conn.execute("SELECT 1 FROM activities WHERE session_id=?", (session_id,)).fetchone():
        raise PlanError("This session has a run assigned; move the run first.")
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
