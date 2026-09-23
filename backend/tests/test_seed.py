from app import config
from app.db import connect, init_db
from app.seed import load_plan, seed

PLAN = load_plan()["weeks"]


def test_tests_use_the_sample_plan():
    assert config.PLAN_PATH == config.SAMPLE_PLAN_PATH


def make(tmp_path):
    conn = connect(tmp_path / "t.sqlite3")
    init_db(conn)
    return conn


def test_seeds_exact_plan(tmp_path):
    conn = make(tmp_path)
    seed(conn)
    weeks = conn.execute("SELECT * FROM weeks ORDER BY number").fetchall()
    assert len(weeks) == 13
    for wk, w in zip(PLAN, weeks):
        assert w["number"] == wk["number"]
        assert w["start_date"] == wk["start"]
        assert w["planned_km"] == wk["total"]
        assert w["notes"] == wk["note"]
        sessions = conn.execute(
            "SELECT label, seeded_km, seeded_duration_min FROM sessions WHERE week_id=? ORDER BY label",
            (w["id"],),
        ).fetchall()
        assert [s["label"] for s in sessions] == ["A", "B", "C", "L"]
        assert [s["seeded_km"] for s in sessions] == [wk[k] for k in ("A", "B", "C", "L")]
        # the plan sets no duration targets
        assert all(s["seeded_duration_min"] is None for s in sessions)
        assert round(sum(s["seeded_km"] for s in sessions), 2) == wk["total"]


def test_week_dates_and_session_days(tmp_path):
    conn = make(tmp_path)
    seed(conn)
    w1 = conn.execute("SELECT * FROM weeks WHERE number=1").fetchone()
    assert (w1["start_date"], w1["end_date"]) == ("2026-01-05", "2026-01-11")
    dates = dict(
        conn.execute("SELECT label, planned_date FROM sessions WHERE week_id=?", (w1["id"],)).fetchall()
    )
    assert dates == {"A": "2026-01-06", "B": "2026-01-08", "C": "2026-01-10", "L": "2026-01-11"}


def test_seed_is_idempotent_and_keeps_user_edits(tmp_path):
    conn = make(tmp_path)
    seed(conn)
    sid = conn.execute("SELECT id FROM sessions WHERE label='A' AND week_id=1").fetchone()["id"]
    conn.execute("UPDATE sessions SET override_km=5.5, override_duration_min=45 WHERE id=?", (sid,))
    conn.commit()

    result = seed(conn)  # restart
    assert result == {"weeks_created": 0, "sessions_created": 0}
    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 52
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    assert row["override_km"] == 5.5
    assert row["override_duration_min"] == 45
    assert row["seeded_km"] == 5  # original target preserved
