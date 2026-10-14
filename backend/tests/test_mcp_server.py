"""The read-only MCP server Claude Desktop uses to look at the plan and runs."""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from app import config, main, mcp_server as srv
from app.db import connect, init_db
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


def test_every_tool_is_marked_read_only():
    tools = asyncio.run(srv.mcp.list_tools())
    assert {t.name for t in tools} == {
        "get_plan_overview", "get_week", "get_run", "list_runs", "get_block_trends", "get_notes",
        "get_athlete_profile", "get_plan_document",
    }
    assert all(t.annotations.read_only_hint for t in tools)


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
