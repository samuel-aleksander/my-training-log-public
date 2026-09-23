"""End-to-end: upload -> assignment -> weekly comparison, plus persistence."""

import os
import tempfile
from datetime import datetime, timezone

import pytest

TMP = tempfile.mkdtemp(prefix="marathon-test-")
os.environ["MARATHON_DATA_DIR"] = TMP
os.environ["MARATHON_DB_PATH"] = os.path.join(TMP, "test.sqlite3")
os.environ["MARATHON_FILES_DIR"] = os.path.join(TMP, "files")
os.environ["MARATHON_STAGING_DIR"] = os.path.join(TMP, "staging")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from fit_fixture import steady_run  # noqa: E402

client = TestClient(main.app)


def upload(data: bytes, name="activity.fit"):
    return client.post("/api/uploads/preview", files={"file": (name, data, "application/octet-stream")})


@pytest.fixture(scope="module", autouse=True)
def _boot():
    client.get("/api/health")


def test_week_totals_match_the_plan():
    weeks = client.get("/api/weeks").json()
    assert [w["planned_km"] for w in weeks] == [23, 24, 27, 22, 29, 31, 33, 25, 35, 37, 39, 29, 32]
    w1 = weeks[0]
    assert w1["sessions_planned"] == 4
    assert all(s["target_duration_display"] == "Not set" for s in w1["sessions"])


def test_full_upload_assign_compare_flow():
    fit = steady_run(datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc), 6.4, 6.95, hr=131)
    prev = upload(fit).json()
    assert prev["duplicate"] is False
    assert prev["suggested_week"] == 1
    assert prev["distance_km"] == pytest.approx(6.4, abs=0.02)
    assert prev["has_hr"] is True
    assert prev["duration_source"] == "timer"

    week = client.get("/api/weeks/1").json()
    session_a = next(s for s in week["sessions"] if s["label"] == "A")

    created = client.post(
        "/api/activities/confirm",
        json={"token": prev["token"], "week_number": 1, "session_id": session_a["id"], "rpe": 3},
    )
    assert created.status_code == 200
    aid = created.json()["id"]

    week = client.get("/api/weeks/1").json()
    s_a = next(s for s in week["sessions"] if s["label"] == "A")
    assert s_a["status"] == "completed"
    assert s_a["actual_km"] == pytest.approx(6.4, abs=0.02)
    assert s_a["diff_km"] == pytest.approx(1.4, abs=0.02)  # actual minus planned
    assert s_a["diff_duration_min"] is None  # no duration target
    assert week["actual_km"] == pytest.approx(6.4, abs=0.02)
    assert week["diff_km"] == pytest.approx(6.4 - 23, abs=0.02)
    assert week["sessions_completed"] == 1
    assert week["actual_duration_s"] > 0

    # duplicate detection
    assert upload(fit).json()["duplicate"] is True
    assert client.post(
        "/api/activities/confirm",
        json={"token": prev["token"], "week_number": 1, "session_id": session_a["id"]},
    ).status_code in (400, 409)

    # session already taken cannot be silently replaced
    other = steady_run(datetime(2026, 1, 8, 7, 0, tzinfo=timezone.utc), 7.1, 7.1, hr=128)
    p2 = upload(other, "second.fit").json()
    clash = client.post(
        "/api/activities/confirm",
        json={"token": p2["token"], "week_number": 1, "session_id": session_a["id"]},
    )
    assert clash.status_code == 409

    session_b = next(s for s in week["sessions"] if s["label"] == "B")
    a2 = client.post(
        "/api/activities/confirm",
        json={"token": p2["token"], "week_number": 1, "session_id": session_b["id"]},
    ).json()["id"]

    # reassignment to another week keeps the recorded date
    before = client.get(f"/api/activities/{a2}").json()["start_time"]
    moved = client.post(f"/api/activities/{a2}/assign", json={"week_number": 2, "is_extra": True,
                                                              "extra_type": "Extra run"}).json()
    assert moved["start_time"] == before
    w2 = client.get("/api/weeks/2").json()
    assert len(w2["extras"]) == 1
    assert w2["actual_km"] == pytest.approx(7.1, abs=0.05)

    # move it back onto session B
    client.post(f"/api/activities/{a2}/assign", json={"week_number": 1, "session_id": session_b["id"]})
    assert client.get("/api/weeks/2").json()["extras"] == []

    # removing a run does not delete the plan
    assert client.delete(f"/api/activities/{a2}").status_code == 400  # confirmation required
    assert client.delete(f"/api/activities/{a2}?confirm=true").status_code == 200
    week = client.get("/api/weeks/1").json()
    assert week["sessions_planned"] == 4
    assert next(s for s in week["sessions"] if s["label"] == "B")["status"] in ("planned", "skipped")


def test_overrides_keep_seeded_values():
    week = client.get("/api/weeks/3").json()
    sid = next(s for s in week["sessions"] if s["label"] == "C")["id"]
    client.patch(f"/api/sessions/{sid}", json={"override_km": 6.5, "override_duration_min": 40})
    week = client.get("/api/weeks/3").json()
    s_c = next(s for s in week["sessions"] if s["label"] == "C")
    assert s_c["seeded_km"] == 4
    assert s_c["target_km"] == 6.5
    assert s_c["target_duration_display"] == "40 min"
    assert week["planned_km"] == 29.5
    assert week["seeded_planned_km"] == 27
    client.patch(f"/api/sessions/{sid}", json={"override_km": None, "override_duration_min": None})
    assert client.get("/api/weeks/3").json()["planned_km"] == 27


def test_zones_are_computed_only_after_configuration():
    acts = client.get("/api/activities").json()
    aid = acts[0]["id"]
    assert client.get(f"/api/activities/{aid}").json()["zones"] is None

    client.put("/api/settings/zones", json={"zones": [
        {"name": "Z1", "min_hr": 100, "max_hr": 119},
        {"name": "Z2", "min_hr": 120, "max_hr": 139},
        {"name": "Z3", "min_hr": 140, "max_hr": 154},
    ]})
    client.post("/api/zones/recalculate")
    z = client.get(f"/api/activities/{aid}").json()["zones"]
    assert z is not None
    z2 = next(b for b in z["buckets"] if b["name"] == "Z2")
    assert z2["pct"] == pytest.approx(100, abs=0.5)
    assert z["zones_used"][1]["max_hr"] == 139  # definitions used are recorded


def test_persistence_after_restart():
    main._conn.close()
    main._conn = None
    weeks = client.get("/api/weeks").json()
    assert sum(w["sessions_completed"] for w in weeks) == 1
    assert len(client.get("/api/activities").json()) == 1
    assert len(weeks) == 13  # no duplicate seeding
