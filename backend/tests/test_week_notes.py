"""Your own notes and reason tags on a week."""

import os
import tempfile

TMP = tempfile.mkdtemp(prefix="marathon-notes-")
os.environ["MARATHON_DATA_DIR"] = TMP
os.environ["MARATHON_DB_PATH"] = os.path.join(TMP, "test.sqlite3")
os.environ["MARATHON_FILES_DIR"] = os.path.join(TMP, "files")
os.environ["MARATHON_STAGING_DIR"] = os.path.join(TMP, "staging")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

client = TestClient(main.app)


def test_week_starts_without_a_note():
    week = client.get("/api/weeks/2").json()
    assert week["journal"] == {"note": None, "reasons": [], "updated_at": None}


def test_save_note_and_reasons_then_read_back_everywhere():
    res = client.put(
        "/api/weeks/2/journal",
        json={"note": "  Cold midweek, skipped B.  ", "reasons": ["Illness", "Work", "Illness", " "]},
    )
    assert res.status_code == 200
    saved = res.json()
    assert saved["note"] == "Cold midweek, skipped B."
    assert saved["reasons"] == ["Illness", "Work"]
    assert saved["updated_at"]

    week = client.get("/api/weeks/2").json()
    assert week["journal"]["reasons"] == ["Illness", "Work"]
    # the plan's own week text is untouched
    assert week["notes"] == client.get("/api/weeks").json()[1]["notes"]

    block_w2 = next(w for w in client.get("/api/block").json()["weeks"] if w["number"] == 2)
    assert block_w2["journal"]["note"] == "Cold midweek, skipped B."


def test_reasons_only_is_allowed_and_empty_clears():
    assert client.put("/api/weeks/3/journal", json={"reasons": ["Travel"]}).json()["note"] is None
    cleared = client.put("/api/weeks/3/journal", json={"note": "", "reasons": []}).json()
    assert cleared == {"note": None, "reasons": [], "updated_at": None}


def test_rejects_bad_input():
    assert client.put("/api/weeks/99/journal", json={"note": "x"}).status_code == 404
    assert client.put("/api/weeks/2/journal", json={"reasons": "Illness"}).status_code == 400
    assert client.put("/api/weeks/2/journal", json={"reasons": ["x" * 41]}).status_code == 400
    assert client.put("/api/weeks/2/journal", json={"note": "x" * 4001}).status_code == 400
