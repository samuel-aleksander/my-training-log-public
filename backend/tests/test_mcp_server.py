"""The read-only MCP server Claude Desktop uses to look at the plan and runs."""

import asyncio
import json
import sqlite3
from datetime import date, datetime, timezone

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from app import config, main, mcp_server as srv, plan_changes
from app.db import connect, init_db
from app.plan_edits import PlanError, remove_added_session
from app.fit_parser import parse_fit, sha256
from app.seed import seed
from fit_fixture import steady_run

ZONES = [{"name": "Z1", "min_hr": 100, "max_hr": 125}, {"name": "Z2", "min_hr": 126, "max_hr": 145}]
# Never sent to Claude: recorded samples, file names and paths.
PRIVATE_KEYS = {"series", "samples", "stored_path", "original_filename", "file_hash"}


def add_run(conn, tmp_path, start, km, pace, week, label=None, hr=131, **kw):
    fit = steady_run(start, km, pace, hr=hr, laps=2)
    digest = sha256(fit)
    stored = tmp_path / f"{digest}.fit"
    stored.write_bytes(fit)
    w = conn.execute("SELECT id FROM weeks WHERE number=?", (week,)).fetchone()
    sid = None
    if label:
        sid = conn.execute("SELECT id FROM sessions WHERE week_id=? AND label=?", (w["id"], label)).fetchone()["id"]
    return main.store_activity(conn, parse_fit(fit), digest, "run.fit", stored, w["id"], sid,
                               extra_type=None if label else "Extra run", **kw)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(config, "PRIVATE_DOCS_DIR", tmp_path / "docs")
    conn = connect()
    init_db(conn)
    seed(conn)
    conn.execute("UPDATE settings SET value=? WHERE key='zones'", (json.dumps(ZONES),))
    conn.commit()
    ids = {
        "w1a": add_run(conn, tmp_path, datetime(2026, 1, 6, 7, tzinfo=timezone.utc), 6.4, 6.95, 1, "A",
                       rpe=3, note="Easy, left knee a bit stiff at the start"),
        "w1x": add_run(conn, tmp_path, datetime(2026, 1, 8, 7, tzinfo=timezone.utc), 3.0, 7.0, 1),
        "w2l": add_run(conn, tmp_path, datetime(2026, 1, 18, 8, tzinfo=timezone.utc), 9.0, 7.1, 2, "L", rpe=4),
    }
    conn.execute(
        "INSERT INTO week_notes (week_id, note, reasons, updated_at) VALUES (1, 'Busy week', '[\"Work\"]', 'now')"
    )
    conn.commit()
    conn.close()
    return ids


def keys_in(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys_in(v)


READ_TOOLS = {
    "get_plan_overview", "get_week", "get_run", "list_runs", "get_block_trends", "get_notes",
    "get_athlete_profile", "get_plan_document", "get_changelog",
}


def test_only_the_change_tools_can_write():
    tools = {t.name: t.annotations for t in asyncio.run(srv.mcp.list_tools())}
    assert set(tools) == READ_TOOLS | {"propose_change", "apply_change", "revert_change"}
    assert all(tools[n].read_only_hint for n in READ_TOOLS)
    assert tools["propose_change"].read_only_hint is None and tools["propose_change"].destructive_hint is False
    for name in ("apply_change", "revert_change"):
        assert tools[name].read_only_hint is None and tools[name].destructive_hint is True


def test_connection_refuses_writes(db):
    with srv.read_db() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE sessions SET override_km = 99")


def test_missing_database_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "none.sqlite3")
    with pytest.raises(ToolError, match="dev.sh"):
        srv.get_plan_overview()
    assert not (tmp_path / "none.sqlite3").exists()


def test_plan_overview(db):
    o = srv.get_plan_overview()
    assert len(o["weeks"]) == 13
    w1 = o["weeks"][0]
    assert w1["planned_km"] == 23
    assert w1["actual_km"] == pytest.approx(9.4, abs=0.05)
    assert w1["sessions_completed"] == 1
    assert w1["your_note"] == "Busy week" and w1["reasons"] == ["Work"]
    assert o["weeks"][1]["long_run_actual_km"] == pytest.approx(9.0, abs=0.05)
    assert o["hr_zones"][1]["name"] == "Z2"
    assert set(o["session_types"]) == {"A", "B", "C", "L"}


def test_week_detail(db):
    w = srv.get_week(1)
    a = next(s for s in w["sessions"] if s["label"] == "A")
    assert a["weekday"] == "Tuesday"
    assert a["status"] == "completed"
    assert a["run"]["pace_per_km"] == "6:57"
    assert a["run"]["rpe"] == 3 and "knee" in a["run"]["note"]
    assert a["run"]["zones"]["Z2"]["pct"] == 100.0
    assert next(s for s in w["sessions"] if s["label"] == "B")["status"] == "skipped"
    assert [e["distance_km"] for e in w["extra_runs"]] == [pytest.approx(3.0, abs=0.05)]
    assert not PRIVATE_KEYS & set(keys_in(w))
    with pytest.raises(ToolError, match="weeks 1 to 13"):
        srv.get_week(99)


def test_run_detail(db):
    r = srv.get_run(db["w1a"])
    assert r["week"] == 1 and r["session"] == "A" and r["session_target_km"] == 5
    assert len(r["laps"]) == 2
    assert r["hr_drift_detail"]["drift_pct"] == pytest.approx(0, abs=0.5)
    assert srv.get_run(db["w1x"])["session"] == "extra"
    assert not PRIVATE_KEYS & set(keys_in(r))
    with pytest.raises(ToolError):
        srv.get_run(12345)


def test_list_runs_filters_by_week(db):
    assert [r["run_id"] for r in srv.list_runs()] == [db["w1a"], db["w1x"], db["w2l"]]
    only2 = srv.list_runs(from_week=2, to_week=2)
    assert [(r["week"], r["session"]) for r in only2] == [(2, "L")]


def test_block_trends(db):
    t = srv.get_block_trends()
    w1, w2 = t["weeks"][0], t["weeks"][1]
    assert w1["runs"] == 2 and w1["extra_runs"] == 1
    assert w1["avg_rpe"] == 3.0 and w1["runs_with_rpe"] == 1
    assert w2["change_vs_prev_week_pct"] == pytest.approx((9.0 - 9.4) / 9.4 * 100, abs=1)
    assert w2["zones_pct"] == {"Z1": 0.0, "Z2": 100.0}
    assert t["longest_run_km"] == pytest.approx(9.0, abs=0.05)


def test_notes(db):
    notes = srv.get_notes()
    assert [n["week"] for n in notes] == [1, 2]
    assert notes[0]["your_note"] == "Busy week"
    assert [r["session"] for r in notes[0]["runs"]] == ["A"]  # the extra run has no note or RPE
    assert srv.get_notes(from_week=3) == []


def test_private_docs(db, tmp_path):
    with pytest.raises(ToolError, match="athlete-profile.md"):
        srv.get_athlete_profile()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "athlete-profile.md").write_text("# Profile\nLeft knee history.")
    assert "knee" in srv.get_athlete_profile()


def test_tools_work_through_an_mcp_client(db):
    async def go():
        async with Client(srv.mcp) as client:
            week = await client.call_tool("get_week", {"week": 1})
            missing = await client.call_tool("get_week", {"week": 99})
            prompt = await client.get_prompt("discuss_week", {"week": "3"})
        return week, missing, prompt

    week, missing, prompt = asyncio.run(go())
    assert not week.is_error
    assert json.loads(week.content[0].text)["week"] == 1
    assert missing.is_error and "weeks 1 to 13" in missing.content[0].text
    assert "get_week(3)" in prompt.messages[0].content.text


# --------------------------------------------------------------------------- changing the plan
#
# Sample plan, weeks used below (review weeks 1, 4, 8, 13):
#   week 4 (review) A5 B5 C4 L8 = 22 · week 5 A6 B7 C5 L11 = 29 · week 6 A6 B8 C5 L12 = 31
#   week 7 A7 B8 C5 L13 = 33. Week 6: Mon 9 Feb, A Tue 10, B Thu 12, C Sat 14, L Sun 15.
# "Today" is Wednesday 14 Jan (week 2) unless a test says otherwise.

TODAY = date(2026, 1, 14)


@pytest.fixture
def plan(db, monkeypatch):
    monkeypatch.setattr(plan_changes, "today", lambda: TODAY)
    return db


def rows(sql, *args):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def sid(week, label):
    return rows("SELECT s.id FROM sessions s JOIN weeks w ON w.id = s.week_id WHERE w.number=? AND s.label=?",
                week, label)[0]["id"]


def session(week, label):
    return rows("SELECT * FROM sessions WHERE id=?", sid(week, label))[0]


def propose(kind, week=None, label=None, reason="Because.", **kw):
    if week and label:
        kw["session_id"] = sid(week, label)
    return srv.propose_change(kind, reason, **kw)


def refused(kind, week=None, label=None, **kw) -> str:
    with pytest.raises(ToolError) as e:
        propose(kind, week, label, **kw)
    return str(e.value)


def web_client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "_conn", connect())
    return TestClient(main.app)


def test_propose_changes_nothing_until_applied(plan):
    c = propose("set_km", 6, "B", km=8.5, reason="Bit more volume midweek.")
    assert c["status"] == "pending"
    assert c["summary"] == "Week 6 B (Thu 12 Feb): 8 km -> 8.5 km"
    assert (c["from"], c["to"], c["warnings"]) == (8, 8.5, [])
    assert session(6, "B")["override_km"] is None

    applied = srv.apply_change(c["change_id"])
    assert applied["status"] == "applied" and applied["applied_at"]
    assert session(6, "B")["override_km"] == 8.5
    assert srv.get_week(6)["planned_km"] == 31.5
    with pytest.raises(ToolError, match="only pending"):
        srv.apply_change(c["change_id"])

    reverted = srv.revert_change(c["change_id"])
    assert reverted["status"] == "reverted" and reverted["reverted_at"]
    assert session(6, "B")["override_km"] is None
    assert [e["status"] for e in srv.get_changelog()] == ["reverted"]


def test_every_kind_round_trips(plan):
    before = dict(session(6, "B"))
    changes = [
        propose("set_duration", 6, "B", duration_min=50),
        propose("move", 6, "B", date="2026-02-11"),
        propose("note", 6, "B", note="  Hilly route  "),
    ]
    for c in changes:
        srv.apply_change(c["change_id"])
    s = session(6, "B")
    assert (s["override_duration_min"], s["override_date"], s["user_note"]) == (50, "2026-02-11", "Hilly route")
    assert changes[1]["summary"] == "Week 6 B: Thu 12 Feb -> Wed 11 Feb"
    w = srv.get_week(6)
    b = next(x for x in w["sessions"] if x["label"] == "B")
    assert (b["planned_date"], b["original_date"], b["your_note"]) == ("2026-02-11", "2026-02-12", "Hilly route")

    for c in reversed(changes):
        srv.revert_change(c["change_id"])
    assert dict(session(6, "B")) == before


def test_add_then_revert_removes_the_session(plan):
    c = propose("add", date="2026-02-09", km=5, note="Shakeout", reason="An extra easy day.")
    assert c["summary"] == "Week 6: add 5 km on Mon 09 Feb"
    srv.apply_change(c["change_id"])
    added = rows("SELECT * FROM sessions WHERE status='added'")
    assert [(r["label"], r["planned_date"], r["seeded_km"], r["user_note"]) for r in added] == [
        ("X1", "2026-02-09", 5, "Shakeout")]
    w = srv.get_week(6)
    assert w["planned_km"] == 36 and w["sessions_planned"] == 5
    assert w["sessions"][0]["plan_status"] == "added"

    srv.revert_change(c["change_id"])
    assert rows("SELECT * FROM sessions WHERE status='added'") == []
    assert srv.get_week(6)["planned_km"] == 31
    assert srv.get_changelog()[0]["summary"] == "Week 6: add 5 km on Mon 09 Feb"


def test_skip_keeps_the_session_and_drops_its_km(plan):
    c = propose("skip", 6, "C")
    assert c["summary"] == "Week 6 C (Sat 14 Feb): skip (5 km)"
    srv.apply_change(c["change_id"])
    assert session(6, "C")["status"] == "skipped"
    w = srv.get_week(6)
    assert w["planned_km"] == 26 and w["sessions_planned"] == 3
    assert next(x for x in w["sessions"] if x["label"] == "C")["status"] == "skipped"
    srv.revert_change(c["change_id"])
    assert session(6, "C")["status"] == "planned"


# --- data rules (block)

def test_past_sessions_and_dates_are_blocked(plan):
    assert "in the past" in refused("set_km", 2, "A", km=4)          # Tue 13 Jan
    assert "in the past" in refused("skip", 1, "B")
    assert "in the past" in refused("move", 2, "B", date="2026-01-12")  # B is Thu 15, Mon 12 is past
    assert "in the past" in refused("add", date="2026-01-13", km=5)
    assert "in the past" in refused("note", 1, "C", note="x")


def test_sessions_with_a_run_are_blocked(plan):
    assert "already has a run" in refused("set_km", 2, "L", km=8)  # the 18 Jan run is assigned to it


def test_dates_must_stay_in_the_week_and_the_block(plan):
    assert "within its own week" in refused("move", 6, "B", date="2026-02-17")
    assert "outside the block" in refused("move", 6, "B", date="2027-01-01")
    assert "outside the block" in refused("add", date="2026-05-01", km=5)
    with pytest.raises(ToolError, match="not a date"):
        propose("move", 6, "B", date="next friday")


def test_one_session_per_day(plan):
    assert "already has a session" in refused("move", 6, "B", date="2026-02-14")  # C's day
    assert "already has a session" in refused("add", date="2026-02-15", km=5)     # L's day
    # a skipped session frees its day
    srv.apply_change(propose("skip", 6, "C")["change_id"])
    srv.apply_change(propose("move", 6, "B", date="2026-02-14")["change_id"])


def test_km_and_duration_bounds(plan):
    assert "between 1 and 42.2 km" in refused("set_km", 6, "B", km=0)
    assert "between 1 and 42.2 km" in refused("set_km", 6, "B", km=100)
    assert "between 1 and 42.2 km" in refused("add", date="2026-02-09", km=-3)
    assert "minutes" in refused("set_duration", 6, "B", duration_min=1000)
    with pytest.raises(ToolError, match="nothing to change"):
        propose("set_km", 6, "B", km=8)


def test_plan_sessions_are_skipped_never_deleted(plan):
    srv.apply_change(propose("skip", 6, "C")["change_id"])
    with pytest.raises(ToolError, match="nothing to change"):
        propose("skip", 6, "C")
    assert "revert the skip first" in refused("set_km", 6, "C", km=4)
    add = propose("add", date="2026-02-09", km=5)
    srv.apply_change(add["change_id"])
    x1 = rows("SELECT id FROM sessions WHERE status='added'")[0]["id"]
    with pytest.raises(ToolError, match="revert the change that added it"):
        srv.propose_change("skip", "x", session_id=x1)
    conn = connect()
    with pytest.raises(PlanError, match="never deleted"):
        remove_added_session(conn, sid(6, "C"))
    conn.close()
    assert len(rows("SELECT id FROM sessions WHERE status <> 'added'")) == 52


def test_blocked_proposals_are_logged_as_rejected(plan):
    msg = refused("set_km", 2, "A", km=4)
    assert "logged as change" in msg
    (entry,) = srv.get_changelog(status="rejected")
    assert entry["blocked_by"] and "in the past" in entry["blocked_by"][0]
    assert srv.get_changelog(status="pending") == []


# --- knee rule (block)

def write_knee_note(tmp_path, where="run"):
    conn = connect()
    if where == "run":
        add_run(conn, tmp_path, datetime(2026, 1, 13, 7, tzinfo=timezone.utc), 5, 7.0, 2,
                note="Left knee sore on the downhill")
    else:
        conn.execute("UPDATE week_notes SET note='Rodilla molesta el martes' WHERE week_id=1")
        conn.execute("INSERT INTO week_notes (week_id, note, reasons, updated_at) "
                     "VALUES (2, 'Rodilla molesta el martes', '[]', 'now')")
        conn.commit()
    conn.close()


@pytest.mark.parametrize("where", ["run", "week"])
def test_knee_note_blocks_only_more_load(plan, tmp_path, where):
    # The fixture's knee note is from 6 Jan: outside the last 7 days, so no effect.
    srv.apply_change(propose("set_km", 6, "A", km=6.5)["change_id"])
    write_knee_note(tmp_path, where)

    msg = refused("set_km", 6, "B", km=9)
    assert "knee" in msg and ("sore" in msg or "Rodilla" in msg)
    assert "knee" in refused("add", date="2026-02-09", km=4)
    assert "knee" in refused("set_duration", 6, "B", duration_min=60)
    # keeping or reducing load is fine
    for c in (propose("set_km", 6, "B", km=7), propose("skip", 6, "C"),
              propose("move", 6, "L", date="2026-02-13"), propose("note", 6, "B", note="easy")):
        assert c["status"] == "pending"
    # undoing an earlier reduction adds load too
    down = propose("set_km", 6, "L", km=10)
    srv.apply_change(down["change_id"])
    with pytest.raises(ToolError, match="knee"):
        srv.revert_change(down["change_id"])


def test_knee_note_after_the_proposal_blocks_the_apply(plan, tmp_path):
    c = propose("set_km", 6, "B", km=8.5)
    write_knee_note(tmp_path)
    with pytest.raises(ToolError, match="knee"):
        srv.apply_change(c["change_id"])
    assert srv.get_changelog()[0]["status"] == "rejected"
    assert session(6, "B")["override_km"] is None


# --- training rules (warn only)

def warnings_of(c):
    return " | ".join(c["warnings"])


def test_weekly_increase_warns(plan):
    c = propose("set_km", 6, "B", km=11)
    assert "Week 6 would be 34.0 km, +17% on week 5" in warnings_of(c)
    # a warning doesn't stop the change once the athlete says yes
    assert srv.apply_change(c["change_id"])["status"] == "applied"


def test_the_plans_own_rebound_is_not_flagged(plan):
    # Week 5 is already +32% on review week 4 in the plan: changes that don't make that
    # jump bigger don't mention it.
    assert propose("move", 5, "B", date="2026-02-06")["warnings"] == []
    assert "Week 5 would be" not in warnings_of(propose("set_km", 5, "B", km=6.5))


def test_long_run_step_and_share_warn(plan):
    w = warnings_of(propose("set_km", 6, "L", km=14.5))
    assert "long run of week 6 would be 14.5 km, +3.5 km on week 5" in w
    assert "43% of week 6" in w


def test_skipping_raises_the_long_run_share_and_next_weeks_jump(plan):
    w = warnings_of(propose("skip", 6, "C"))
    assert "46% of week 6" in w
    assert "Week 7 would be 33.0 km, +27% on week 6" in w


def test_back_to_back_long_runs_warn(plan):
    w = warnings_of(propose("add", date="2026-02-16", km=11))  # Monday after week 6's long run
    assert "back-to-back days: 2026-02-16 and 2026-02-15" in w


def test_more_than_five_running_days_warns(plan):
    srv.apply_change(propose("add", date="2026-02-09", km=3)["change_id"])
    assert "6 running days" in warnings_of(propose("add", date="2026-02-11", km=3))


def test_review_weeks_stay_light(plan):
    assert "review week" in warnings_of(propose("set_km", 4, "C", km=5))
    assert "carries the review" in warnings_of(propose("skip", 4, "A"))
    assert "review week" not in warnings_of(propose("set_km", 4, "C", km=3))


# --- apply and revert keep the log honest

def test_new_warnings_at_apply_need_a_fresh_yes(plan, monkeypatch):
    c = propose("set_km", 6, "B", km=8.5)
    assert c["warnings"] == []
    # meanwhile the athlete raises C in the web app
    client = web_client(monkeypatch)
    assert client.patch(f"/api/sessions/{sid(6, 'C')}", json={"override_km": 8}).status_code == 200
    with pytest.raises(ToolError, match="new warnings"):
        srv.apply_change(c["change_id"])
    assert session(6, "B")["override_km"] is None
    assert srv.get_changelog()[0]["status"] == "pending"
    assert srv.apply_change(c["change_id"])["status"] == "applied"  # shown and confirmed again


def test_a_session_edited_since_the_proposal_is_not_overwritten(plan, monkeypatch):
    c = propose("set_km", 6, "B", km=9)
    web_client(monkeypatch).patch(f"/api/sessions/{sid(6, 'B')}", json={"override_km": 7})
    with pytest.raises(ToolError, match="propose it again"):
        srv.apply_change(c["change_id"])
    assert session(6, "B")["override_km"] == 7
    assert srv.get_changelog()[0]["status"] == "rejected"


def test_a_newer_proposal_replaces_a_pending_one(plan):
    first = propose("set_km", 6, "B", km=9)
    second = propose("set_km", 6, "B", km=8.5)
    assert {e["change_id"]: e["status"] for e in srv.get_changelog()} == {
        first["change_id"]: "rejected", second["change_id"]: "pending"}


def test_reverts_go_newest_first_and_not_over_app_edits(plan, monkeypatch):
    km = propose("set_km", 6, "B", km=8.5)
    srv.apply_change(km["change_id"])
    move = propose("move", 6, "B", date="2026-02-11")
    srv.apply_change(move["change_id"])
    with pytest.raises(ToolError, match=f"Change {move['change_id']} .* revert it first"):
        srv.revert_change(km["change_id"])
    srv.revert_change(move["change_id"])

    web_client(monkeypatch).patch(f"/api/sessions/{sid(6, 'B')}", json={"override_km": 7.5})
    with pytest.raises(ToolError, match="edited in the app"):
        srv.revert_change(km["change_id"])
    with pytest.raises(ToolError, match="only applied"):
        srv.revert_change(move["change_id"])


def test_a_change_that_has_become_past_cant_be_reverted(plan, monkeypatch):
    c = propose("set_km", 6, "B", km=8.5)
    srv.apply_change(c["change_id"])
    monkeypatch.setattr(plan_changes, "today", lambda: date(2026, 2, 20))
    with pytest.raises(ToolError, match="in the past"):
        srv.revert_change(c["change_id"])


# --- the web app shows what was applied

def test_web_api_reflects_applied_changes(plan, monkeypatch):
    for c in (propose("move", 6, "B", date="2026-02-11"), propose("skip", 6, "C"),
              propose("add", date="2026-02-09", km=4), propose("note", 6, "L", note="Trail")):
        srv.apply_change(c["change_id"])
    client = web_client(monkeypatch)

    w = client.get("/api/weeks/6").json()
    assert [(s["label"], s["planned_date"], s["plan_status"]) for s in w["sessions"]] == [
        ("X1", "2026-02-09", "added"), ("A", "2026-02-10", "planned"), ("B", "2026-02-11", "planned"),
        ("C", "2026-02-14", "skipped"), ("L", "2026-02-15", "planned"),
    ]
    assert next(s for s in w["sessions"] if s["label"] == "C")["status"] == "skipped"
    b = next(s for s in w["sessions"] if s["label"] == "B")
    assert b["original_date"] == "2026-02-12"
    assert next(s for s in w["sessions"] if s["label"] == "L")["user_note"] == "Trail"
    assert w["planned_km"] == 31 - 5 + 4 and w["seeded_planned_km"] == 31
    assert w["sessions_planned"] == 4
    days = {d["date"]: d["planned_km"] for d in w["days"]}
    assert (days["2026-02-09"], days["2026-02-11"], days["2026-02-12"], days["2026-02-14"]) == (4, 8, None, None)

    block_w6 = next(x for x in client.get("/api/block").json()["weeks"] if x["number"] == 6)
    assert block_w6["planned_km"] == 30 and block_w6["sessions_planned"] == 4
    assert client.get("/api/weeks").json()[5]["planned_km"] == 30

    # a run uploaded for week 6 can't land on the skipped session
    conn = connect()
    free = main.free_sessions(conn, rows("SELECT id FROM weeks WHERE number=6")[0]["id"])
    conn.close()
    assert {s["label"]: s["planned_date"] for s in free} == {
        "X1": "2026-02-09", "A": "2026-02-10", "B": "2026-02-11", "L": "2026-02-15"}

    # the plan's own values are untouched
    b_row, c_row = session(6, "B"), session(6, "C")
    assert (b_row["planned_date"], b_row["seeded_km"], c_row["seeded_km"]) == ("2026-02-12", 8, 5)
    assert rows("SELECT planned_km FROM weeks WHERE number=6")[0]["planned_km"] == 31


def test_web_patch_uses_the_same_checks(plan, monkeypatch):
    client = web_client(monkeypatch)
    b = sid(6, "B")
    assert client.patch(f"/api/sessions/{b}", json={"override_km": 0}).status_code == 400
    assert client.patch(f"/api/sessions/{b}", json={"override_date": "2026-02-17"}).status_code == 400
    assert client.patch(f"/api/sessions/{b}", json={"override_date": "2026-02-14"}).status_code == 400
    assert client.patch(f"/api/sessions/{b}", json={"status": "added"}).status_code == 400
    assert client.patch(f"/api/sessions/{b}", json={"seeded_km": 3}).status_code == 400
    ok = client.patch(f"/api/sessions/{b}", json={"override_date": "2026-02-13", "user_note": "Fri instead"})
    assert ok.status_code == 200 and ok.json()["planned_date"] == "2026-02-13"
    assert client.patch(f"/api/sessions/{b}", json={"status": "skipped"}).json()["plan_status"] == "skipped"


def test_change_tools_through_an_mcp_client(plan):
    async def go():
        async with Client(srv.mcp) as client:
            ok = await client.call_tool("propose_change", {
                "kind": "set_km", "session_id": sid(6, "B"), "km": 8.5, "reason": "More midweek."})
            bad = await client.call_tool("propose_change", {
                "kind": "set_km", "session_id": sid(2, "A"), "km": 4, "reason": "x"})
            change_id = json.loads(ok.content[0].text)["change_id"]
            applied = await client.call_tool("apply_change", {"change_id": change_id})
        return ok, bad, applied

    ok, bad, applied = asyncio.run(go())
    assert not ok.is_error and json.loads(ok.content[0].text)["status"] == "pending"
    assert bad.is_error and "in the past" in bad.content[0].text
    assert json.loads(applied.content[0].text)["status"] == "applied"


def test_an_older_database_is_brought_up_to_date(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE weeks (id INTEGER PRIMARY KEY, number INTEGER NOT NULL UNIQUE, start_date TEXT NOT NULL,"
        " end_date TEXT NOT NULL, planned_km REAL NOT NULL, notes TEXT);"
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, week_id INTEGER NOT NULL, label TEXT NOT NULL,"
        " planned_date TEXT NOT NULL, seeded_km REAL NOT NULL, override_km REAL, seeded_duration_min REAL,"
        " override_duration_min REAL, notes TEXT, UNIQUE (week_id, label));"
    )
    conn.close()
    assert srv.get_changelog() == []
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(sessions)")}
    assert {"override_date", "status", "user_note"} <= cols
