"""Changes to the plan proposed in a chat with Claude.

A change is proposed (checked and logged as pending), shown to you, applied only
after you say yes, and can be reverted. Every step is recorded in plan_changes.

Each change touches one session: its km or duration target, its day (within
its week), skipping it, your note on it, or adding a session outside the plan.
Writes go through plan_edits.write_session, the same code the web app's PATCH
uses.

The rules run on every proposal, again on apply, and on revert:

- Data rules (block): dates inside the block; one session per day; sessions in
  the past or with a run attached don't change; plan sessions are skipped,
  never deleted; km and duration within sane bounds.
- Knee rule (block): if a note from the last KNEE_WINDOW_DAYS mentions the
  knee, only changes that keep or reduce load go through.
- Training rules (warn): weekly km up ~10% at most, long run up 2 km a week at
  most and no more than ~35% of the week, no long runs on back-to-back days,
  5 running days a week at most, review weeks kept as light as planned. A
  warning only fires when the change makes that number worse, so the plan's
  own shape (e.g. the rebound after a review week) doesn't trip it.

Load for a week is what it will actually be: km run for done sessions and
extra runs, plus the targets of sessions still to come.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, timedelta, timezone

from .plan_edits import (
    PlanError,
    add_session,
    check_note,
    parse_date,
    remove_added_session,
    session_date,
    target_duration,
    target_km,
    write_session,
)
from .seed import load_plan

KINDS = {
    "set_km": "override_km",
    "set_duration": "override_duration_min",
    "move": "override_date",
    "skip": "status",
    "add": None,
    "note": "user_note",
}
MAX_REASON_CHARS = 500

KNEE_WINDOW_DAYS = 7
KNEE = re.compile(r"\b(knees?|rodillas?)\b", re.IGNORECASE)

MAX_WEEKLY_INCREASE = 0.10
MAX_LONG_RUN_STEP_KM = 2.0
MAX_LONG_RUN_SHARE = 0.35
MAX_RUNNING_DAYS = 5
LONG_SHARE_OF_LONGEST = 0.8  # a session this close to the week's longest counts as a long run


def today() -> date:
    return date.today()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChangeRefused(PlanError):
    """The rules stop this change. `blocks` holds the reasons."""

    def __init__(self, change_id: int | None, blocks: list[dict]):
        self.change_id = change_id
        self.blocks = blocks
        logged = f" (logged as change {change_id}, rejected)" if change_id else ""
        lines = "\n".join(f"- {b['message']}" for b in blocks)
        super().__init__(f"Not allowed{logged}:\n{lines}")


class NewWarnings(PlanError):
    """Things changed since the proposal and new warnings came up: ask again."""

    def __init__(self, change_id: int, warnings: list[dict]):
        self.warnings = warnings
        lines = "\n".join(f"- {w['message']}" for w in warnings)
        super().__init__(
            f"Change {change_id} was not applied: new warnings since it was proposed:\n{lines}\n"
            "Show them to the athlete and call apply_change again only if they still say yes."
        )


# --------------------------------------------------------------------------- the plan as it stands


def load_state(conn) -> dict:
    """Every session (with its week and any run) and every extra run, as plain dicts."""
    sessions = {}
    for r in conn.execute(
        """SELECT s.*, w.number AS week, a.id AS run_id, a.distance_m AS run_m
           FROM sessions s JOIN weeks w ON w.id = s.week_id
           LEFT JOIN activities a ON a.session_id = s.id"""
    ):
        s = dict(r)
        s["has_run"] = s["run_id"] is not None
        s["actual_km"] = (s["run_m"] or 0) / 1000 if s["has_run"] else None
        sessions[s["id"]] = s
    extras = [
        {"week": r["week"], "km": (r["distance_m"] or 0) / 1000,
         "date": datetime.fromisoformat(r["start_time"]).astimezone().date().isoformat()}
        for r in conn.execute(
            """SELECT a.start_time, a.distance_m, w.number AS week FROM activities a
               JOIN weeks w ON w.id = a.week_id WHERE a.is_extra = 1"""
        )
    ]
    weeks = {r["number"]: dict(r) for r in conn.execute("SELECT * FROM weeks")}
    return {"sessions": sessions, "extras": extras, "weeks": weeks}


def week_of(state, iso_date: str) -> int | None:
    return next((n for n, w in state["weeks"].items() if w["start_date"] <= iso_date <= w["end_date"]), None)


def simulate(state, change) -> dict:
    """The state with `change` made. Validation is not repeated here (see check)."""
    after = copy.deepcopy(state)
    sessions = after["sessions"]
    if change["kind"] == "add":
        to = change["to"]
        sessions["new"] = {
            "id": "new", "week": week_of(state, to["date"]), "label": "X", "planned_date": to["date"],
            "override_date": None, "status": "added", "seeded_km": to["km"], "override_km": None,
            "seeded_duration_min": to["duration_min"], "override_duration_min": None,
            "has_run": False, "actual_km": None,
        }
    elif change["kind"] == "remove":
        sessions.pop(change["session_id"], None)
    else:
        sessions[change["session_id"]][change["field"]] = change["to"]
    return after


def expected_km(s, t: str) -> float:
    if s["status"] == "skipped":
        return 0.0
    if s["has_run"]:
        return s["actual_km"]
    return target_km(s) if session_date(s) >= t else 0.0


def week_load(state, n: int, t: str) -> dict | None:
    if n not in state["weeks"]:
        return None
    sessions = [s for s in state["sessions"].values() if s["week"] == n]
    kms = [(session_date(s), expected_km(s, t)) for s in sessions]
    kms += [(e["date"], e["km"]) for e in state["extras"] if e["week"] == n]
    return {
        "km": sum(k for _, k in kms),
        "long": max((k for _, k in kms), default=0.0),
        "days": len({d for d, k in kms if k > 0}),
    }


def long_runs(state, t: str) -> dict:
    """{session id: date} for every session that is a long run."""
    longest = {n: week_load(state, n, t)["long"] for n in state["weeks"]}
    out = {}
    for sid, s in state["sessions"].items():
        k = expected_km(s, t)
        if k > 0 and (s["label"] == "L" or k >= LONG_SHARE_OF_LONGEST * longest[s["week"]]):
            out[sid] = session_date(s)
    return out


def load_of(s) -> tuple[float, float]:
    if s is None or s["status"] == "skipped":
        return (0.0, 0.0)
    return (target_km(s), target_duration(s) or 0.0)


def raises_load(before, after, change) -> bool:
    sid = "new" if change["kind"] == "add" else change["session_id"]
    b, a = load_of(before["sessions"].get(sid)), load_of(after["sessions"].get(sid))
    return a[0] > b[0] or a[1] > b[1]


# --------------------------------------------------------------------------- rules


def block(rule: str, message: str) -> dict:
    return {"level": "block", "rule": rule, "message": message}


def warn(rule: str, week: int, message: str) -> dict:
    return {"level": "warn", "rule": rule, "week": week, "message": message}


def perform(conn, change) -> int | None:
    """Write the change. Returns the id of an added session."""
    if change["kind"] == "add":
        to = change["to"]
        return add_session(conn, to["date"], to["km"], to["duration_min"], to["note"])
    if change["kind"] == "remove":
        remove_added_session(conn, change["session_id"])
        return None
    write_session(conn, change["session_id"], {change["field"]: change["to"]})
    return None


def dry_run(conn, change) -> str | None:
    """Run the write and undo it: the same checks apply will meet."""
    conn.execute("SAVEPOINT dry_run")
    try:
        perform(conn, change)
    except PlanError as exc:
        return str(exc)
    finally:
        conn.execute("ROLLBACK TO dry_run")
        conn.execute("RELEASE dry_run")
    return None


def data_blocks(conn, state, change, t: str) -> list[dict]:
    out = []
    kind = change["kind"]
    if kind == "add":
        if change["to"]["date"] < t:
            out.append(block("past", f"{change['to']['date']} is in the past."))
    else:
        s = state["sessions"][change["session_id"]]
        name = f"Week {s['week']} {s['label']}"
        if session_date(s) < t:
            out.append(block("past", f"{name} was on {session_date(s)}, in the past: past sessions don't change."))
        if s["has_run"]:
            out.append(block("has_run", f"{name} already has a run assigned: it doesn't change."))
        if kind == "skip" and s["status"] == "added":
            out.append(block("added", f"{name} was added outside the plan: revert the change that added it instead."))
        elif kind in ("set_km", "set_duration", "move") and s["status"] == "skipped":
            out.append(block("skipped", f"{name} is skipped: revert the skip first."))
        if kind.removeprefix("revert_") == "move" and (change["to"] or s["planned_date"]) < t:
            out.append(block("past", f"{change['to'] or s['planned_date']} is in the past."))
    if not out:
        err = dry_run(conn, change)
        if err:
            out.append(block("data", err))
    return out


def knee_mentions(conn, t: date) -> list[str]:
    """Your notes from the last KNEE_WINDOW_DAYS that mention the knee."""
    since = (t - timedelta(days=KNEE_WINDOW_DAYS)).isoformat()
    until = t.isoformat()
    found = []
    for r in conn.execute("SELECT start_time, note FROM activities WHERE note IS NOT NULL"):
        d = datetime.fromisoformat(r["start_time"]).astimezone().date().isoformat()
        if since <= d <= until and KNEE.search(r["note"]):
            found.append(f"run on {d}: \"{r['note']}\"")
    for r in conn.execute(
        """SELECT w.number, n.note FROM week_notes n JOIN weeks w ON w.id = n.week_id
           WHERE n.note IS NOT NULL AND w.end_date >= ? AND w.start_date <= ?""",
        (since, until),
    ):
        if KNEE.search(r["note"]):
            found.append(f"week {r['number']} note: \"{r['note']}\"")
    for r in conn.execute(
        """SELECT s.label, w.number, s.user_note FROM sessions s JOIN weeks w ON w.id = s.week_id
           WHERE s.user_note IS NOT NULL AND COALESCE(s.override_date, s.planned_date) BETWEEN ? AND ?""",
        (since, until),
    ):
        if KNEE.search(r["user_note"]):
            found.append(f"week {r['number']} {r['label']} note: \"{r['user_note']}\"")
    return found


def pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def training_warnings(before, after, change, t: str) -> list[dict]:
    sid = "new" if change["kind"] == "add" else change["session_id"]
    s = after["sessions"].get(sid) or before["sessions"][sid]
    n = s["week"]
    out = []

    for p, q in ((n - 1, n), (n, n + 1)):
        bp, bq, ap, aq = (week_load(x, w, t) for x in (before, after) for w in (p, q))
        if not (ap and aq):
            continue
        if ap["km"] > 0:
            rise_a = aq["km"] / ap["km"] - 1
            rise_b = bq["km"] / bp["km"] - 1 if bp["km"] > 0 else float("inf")
            if rise_a > MAX_WEEKLY_INCREASE and rise_a > rise_b + 1e-9:
                out.append(warn("weekly_increase", q,
                    f"Week {q} would be {aq['km']:.1f} km, {pct(rise_a)} on week {p} ({ap['km']:.1f} km). "
                    f"Keep weekly increases to about {MAX_WEEKLY_INCREASE:.0%}."))
        step_a, step_b = aq["long"] - ap["long"], bq["long"] - bp["long"]
        if ap["long"] > 0 and step_a > MAX_LONG_RUN_STEP_KM and step_a > step_b + 1e-9:
            out.append(warn("long_run_step", q,
                f"The long run of week {q} would be {aq['long']:.1f} km, {step_a:+.1f} km on week {p}. "
                f"Add {MAX_LONG_RUN_STEP_KM - 0.5:g}–{MAX_LONG_RUN_STEP_KM:g} km a week at most."))

    b, a = week_load(before, n, t), week_load(after, n, t)
    share_a = a["long"] / a["km"] if a["km"] else 0
    share_b = b["long"] / b["km"] if b["km"] else 0
    if share_a > MAX_LONG_RUN_SHARE and share_a > share_b + 1e-9:
        out.append(warn("long_run_share", n,
            f"The long run would be {share_a:.0%} of week {n} ({a['long']:.1f} of {a['km']:.1f} km). "
            f"Keep it to about {MAX_LONG_RUN_SHARE:.0%}."))
    if a["days"] > MAX_RUNNING_DAYS and a["days"] > b["days"]:
        out.append(warn("running_days", n, f"Week {n} would have {a['days']} running days; {MAX_RUNNING_DAYS} at most."))

    long_b, long_a = long_runs(before, t), long_runs(after, t)
    if sid in long_a:
        d = date.fromisoformat(long_a[sid])
        for other, od in long_a.items():
            if other == sid or abs((date.fromisoformat(od) - d).days) != 1:
                continue
            if sid in long_b and other in long_b and abs(
                (date.fromisoformat(long_b[other]) - date.fromisoformat(long_b[sid])).days
            ) == 1:
                continue  # already back to back
            o = after["sessions"][other]
            out.append(warn("back_to_back", n,
                f"Long runs on back-to-back days: {long_a[sid]} and {od} (week {o['week']} {o['label']}). "
                "Leave a day between them."))

    if n in load_plan().get("review_weeks", []):
        if a["km"] > b["km"] + 1e-9 or a["long"] > b["long"] + 1e-9:
            out.append(warn("review_week", n,
                f"Week {n} is a review week: keep it as light as planned "
                f"({b['km']:.1f} -> {a['km']:.1f} km, long run {b['long']:.1f} -> {a['long']:.1f} km)."))
        if change["kind"] == "skip" and s["label"] == "A":
            out.append(warn("review_week", n,
                f"Run A of review week {n} carries the review (10 min very gentle, then 20 min "
                "comfortable on a familiar flat stretch); skipping it drops the review."))
    return out


def check(conn, change) -> tuple[list[dict], list[dict]]:
    """(blocks, warnings) for making `change` now."""
    t = today()
    state = load_state(conn)
    blocks = data_blocks(conn, state, change, t.isoformat())
    if blocks:
        return blocks, []
    after = simulate(state, change)
    if raises_load(state, after, change):
        mentions = knee_mentions(conn, t)
        if mentions:
            blocks.append(block("knee", "A recent note mentions the knee, so only changes that keep or "
                                "reduce load are allowed: " + "; ".join(mentions)))
    return blocks, training_warnings(state, after, change, t.isoformat())


# --------------------------------------------------------------------------- proposals and the log


def build_change(conn, kind, session_id=None, km=None, duration_min=None, date_=None, note=None) -> dict:
    if kind not in KINDS:
        raise PlanError(f"Unknown kind {kind!r}; use one of {', '.join(KINDS)}.")

    def need(value, name):
        if value is None:
            raise PlanError(f"A {kind} change needs {name}.")
        return value

    if kind == "add":
        return {"kind": kind, "session_id": None, "field": None, "from": None,
                "to": {"date": parse_date(need(date_, "a date")).isoformat(), "km": need(km, "km"),
                       "duration_min": duration_min, "note": check_note(note)}}
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (need(session_id, "a session_id"),)).fetchone()
    if not s:
        raise PlanError(f"There is no session {session_id}. get_week shows the session ids.")
    field = KINDS[kind]
    if kind == "set_km":
        to = None if need(km, "km") == s["seeded_km"] else km
    elif kind == "set_duration":
        to = None if need(duration_min, "duration_min") == s["seeded_duration_min"] else duration_min
    elif kind == "move":
        to = parse_date(need(date_, "a date")).isoformat()
        to = None if to == s["planned_date"] else to
    elif kind == "skip":
        to = "skipped"
    else:
        to = check_note(note)
    if to == s[field]:
        raise PlanError("That is how the session already is: nothing to change.")
    return {"kind": kind, "session_id": s["id"], "field": field, "from": s[field], "to": to}


def change_from_row(row) -> dict:
    return {"kind": row["kind"], "session_id": row["session_id"], "field": row["field"],
            "from": json.loads(row["from_value"]), "to": json.loads(row["to_value"])}


def propose(conn, kind, reason, *, session_id=None, km=None, duration_min=None, date=None, note=None) -> dict:
    """Check a change and log it: pending, or rejected when a rule blocks it (ChangeRefused)."""
    reason = (reason or "").strip()
    if not reason:
        raise PlanError("Give the reason for the change.")
    if len(reason) > MAX_REASON_CHARS:
        raise PlanError(f"Keep the reason under {MAX_REASON_CHARS} characters.")
    change = build_change(conn, kind, session_id, km, duration_min, date, note)
    blocks, warnings = check(conn, change)
    if not blocks and change["session_id"] is not None:
        # A newer proposal for the same thing replaces any still waiting.
        conn.execute(
            "UPDATE plan_changes SET status='rejected' WHERE status='pending' AND session_id=? AND field=?",
            (change["session_id"], change["field"]),
        )
    cur = conn.execute(
        """INSERT INTO plan_changes (kind, session_id, field, from_value, to_value, reason, status,
                                     warnings, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (kind, change["session_id"], change["field"], json.dumps(change["from"]), json.dumps(change["to"]),
         reason, "rejected" if blocks else "pending", json.dumps(blocks + warnings), now()),
    )
    conn.commit()
    if blocks:
        raise ChangeRefused(cur.lastrowid, blocks)
    return describe(conn, get_row(conn, cur.lastrowid))


def get_row(conn, change_id: int):
    row = conn.execute("SELECT * FROM plan_changes WHERE id=?", (change_id,)).fetchone()
    if not row:
        raise PlanError(f"There is no change {change_id}. get_changelog lists them.")
    return row


def set_status(conn, change_id: int, status: str, **cols) -> None:
    cols["status"] = status
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE plan_changes SET {sets} WHERE id=?", (*cols.values(), change_id))


def apply(conn, change_id: int) -> dict:
    row = get_row(conn, change_id)
    if row["status"] != "pending":
        raise PlanError(f"Change {change_id} is {row['status']}; only pending changes can be applied.")
    change = change_from_row(row)
    if change["session_id"] is not None:
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (change["session_id"],)).fetchone()
        if s is None or s[change["field"]] != change["from"]:
            set_status(conn, change_id, "rejected")
            conn.commit()
            raise PlanError(f"The session changed after change {change_id} was proposed; propose it again.")

    blocks, warnings = check(conn, change)
    if blocks:
        set_status(conn, change_id, "rejected", warnings=json.dumps(blocks + warnings))
        conn.commit()
        raise ChangeRefused(None, blocks)
    shown = {(w["rule"], w.get("week")) for w in json.loads(row["warnings"])}
    new = [w for w in warnings if (w["rule"], w["week"]) not in shown]
    if new:
        conn.execute("UPDATE plan_changes SET warnings=? WHERE id=?", (json.dumps(warnings), change_id))
        conn.commit()
        raise NewWarnings(change_id, new)

    try:
        added = perform(conn, change)
        set_status(conn, change_id, "applied", applied_at=now(), warnings=json.dumps(warnings),
                   session_id=added or change["session_id"])
    except PlanError:
        conn.rollback()
        raise
    conn.commit()
    return describe(conn, get_row(conn, change_id))


def revert(conn, change_id: int) -> dict:
    row = get_row(conn, change_id)
    if row["status"] != "applied":
        raise PlanError(f"Change {change_id} is {row['status']}; only applied changes can be reverted.")
    later = conn.execute(
        "SELECT MAX(id) FROM plan_changes WHERE session_id=? AND status='applied' AND id>?",
        (row["session_id"], change_id),
    ).fetchone()[0]
    if later:
        raise PlanError(f"Change {later} changed the same session later: revert it first.")
    change = change_from_row(row)
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (row["session_id"],)).fetchone()
    if s is None:
        raise PlanError(f"The session of change {change_id} no longer exists.")
    if change["kind"] == "add":
        inverse = {"kind": "remove", "session_id": s["id"], "field": None, "from": None, "to": None}
    else:
        if s[change["field"]] != change["to"]:
            raise PlanError(f"The session was edited in the app after change {change_id}; it can't be reverted.")
        inverse = {**change, "kind": f"revert_{change['kind']}", "from": change["to"], "to": change["from"]}

    blocks, warnings = check(conn, inverse)
    if blocks:
        raise ChangeRefused(None, blocks)
    try:
        perform(conn, inverse)
        set_status(conn, change_id, "reverted", reverted_at=now())
    except PlanError:
        conn.rollback()
        raise
    conn.commit()
    return {**describe(conn, get_row(conn, change_id)), "warnings": [w["message"] for w in warnings]}


def changelog(conn, status: str | None = None, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM plan_changes WHERE ? IS NULL OR status = ? ORDER BY id DESC LIMIT ?",
        (status, status, limit),
    ).fetchall()
    return [describe(conn, r) for r in rows]


# --------------------------------------------------------------------------- how a change reads


def day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %d %b")


def km_text(v) -> str:
    return f"{v:g} km"


def describe(conn, row) -> dict:
    c = change_from_row(row)
    s = conn.execute(
        "SELECT s.*, w.number AS week FROM sessions s JOIN weeks w ON w.id = s.week_id WHERE s.id=?",
        (c["session_id"],),
    ).fetchone()
    old = new = None
    if c["kind"] == "add":
        to = c["to"]
        if s:
            week = s["week"]
        else:  # reverted: the added session is gone
            w = conn.execute(
                "SELECT number FROM weeks WHERE start_date <= ? AND end_date >= ?", (to["date"], to["date"])
            ).fetchone()
            week = w["number"] if w else None
        new = {"date": to["date"], "km": to["km"], "duration_min": to["duration_min"], "note": to["note"]}
        dur = f", {to['duration_min']:g} min" if to["duration_min"] else ""
        summary = f"Week {week}: add {km_text(to['km'])}{dur} on {day(to['date'])}"
        label = s["label"] if s else None
    else:
        week, label = s["week"], s["label"]
        where = f"Week {week} {label} ({day(s['planned_date'])})"
        if c["kind"] == "set_km":
            old, new = c["from"] if c["from"] is not None else s["seeded_km"], c["to"] if c["to"] is not None else s["seeded_km"]
            summary = f"{where}: {km_text(old)} -> {km_text(new)}"
        elif c["kind"] == "set_duration":
            base = s["seeded_duration_min"]
            old, new = c["from"] if c["from"] is not None else base, c["to"] if c["to"] is not None else base
            summary = f"{where}: duration {f'{old:g} min' if old else 'not set'} -> {f'{new:g} min' if new else 'not set'}"
        elif c["kind"] == "move":
            old, new = c["from"] or s["planned_date"], c["to"] or s["planned_date"]
            summary = f"Week {week} {label}: {day(old)} -> {day(new)}"
        elif c["kind"] == "skip":
            old, new = "planned", "skipped"
            summary = f"{where}: skip ({km_text(target_km(s))})"
        else:
            old, new = c["from"], c["to"]
            summary = f"{where}: note -> {new!r}" if new else f"{where}: clear note"
    found = json.loads(row["warnings"])
    return {
        "change_id": row["id"],
        "status": row["status"],
        "kind": c["kind"],
        "week": week,
        "session": label,
        "session_id": c["session_id"],
        "from": old,
        "to": new,
        "summary": summary,
        "reason": row["reason"],
        "warnings": [w["message"] for w in found if w["level"] == "warn"],
        "blocked_by": [w["message"] for w in found if w["level"] == "block"],
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
        "reverted_at": row["reverted_at"],
    }
