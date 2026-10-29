"""Tests for the Garmin fetcher, with a fake Garmin: nothing here touches the network or the Keychain.

  cd backend && .venv/bin/pytest -q ../garmin_sync
"""

import io
import zipfile
from datetime import date

import pytest

import garmin_sync as gs

ORIGINAL = object()  # stands in for Garmin.ActivityDownloadFormat.ORIGINAL


def zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("123_ACTIVITY.fit", b"\x0e\x10" + b"\x00" * 6 + b".FIT" + b"\x00" * 20)
    return buf.getvalue()


class NotFound(Exception):
    pass


class FakeGarmin:
    def __init__(self, activities, files=None, missing=()):
        self.activities = activities
        self.files = files or {}
        self.missing = set(missing)
        self.calls = []

    def get_activities_by_date(self, startdate, sortorder=None):
        self.calls.append(("list", startdate, sortorder))
        return self.activities

    def download_activity(self, activity_id, dl_fmt=None):
        self.calls.append(("download", activity_id, dl_fmt))
        if activity_id in self.missing:
            raise NotFound
        return self.files.get(activity_id, zip_bytes())

    # Anything else would be a call this script must never make.
    def __getattr__(self, name):
        raise AssertionError(f"unexpected Garmin call: {name}")


def act(aid, type_key="running"):
    return {"activityId": aid, "activityType": {"typeKey": type_key},
            "startTimeLocal": "2026-10-26 07:30:00", "activityName": "Easy run"}


def run_sync(api, inbox, **kw):
    return gs.sync(api, inbox, date(2026, 10, 21), ORIGINAL, pause=0, **kw)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(gs, "log", lambda _m: None)


def test_downloads_new_runs_only(tmp_path):
    api = FakeGarmin([act(1), act(2, "cycling"), act(3, "treadmill_running"), act(4, "strength_training")])
    written = run_sync(api, tmp_path)
    assert sorted(p.name for p in written) == ["garmin-1.zip", "garmin-3.zip"]
    assert ("list", "2026-10-21", "asc") in api.calls
    assert {c[1] for c in api.calls if c[0] == "download"} == {1, 3}
    assert all(c[2] is ORIGINAL for c in api.calls if c[0] == "download")
    assert zipfile.is_zipfile(tmp_path / "garmin-1.zip")


def test_files_already_in_the_inbox_are_not_downloaded_again(tmp_path):
    (tmp_path / "garmin-1.zip").write_bytes(b"kept as it was")
    api = FakeGarmin([act(1), act(2)])
    written = run_sync(api, tmp_path)
    assert [p.name for p in written] == ["garmin-2.zip"]
    assert (tmp_path / "garmin-1.zip").read_bytes() == b"kept as it was"


def test_dry_run_writes_and_downloads_nothing(tmp_path):
    api = FakeGarmin([act(1)])
    assert run_sync(api, tmp_path, dry_run=True) == []
    assert list(tmp_path.iterdir()) == []
    assert not [c for c in api.calls if c[0] == "download"]


def test_refuses_what_is_not_a_fit_or_zip(tmp_path):
    api = FakeGarmin([act(1), act(2)], files={1: b"<html>login page</html>", 2: b""})
    assert run_sync(api, tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_refuses_oversized_files(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "MAX_FILE_BYTES", 10)
    assert run_sync(FakeGarmin([act(1)]), tmp_path) == []


def test_a_missing_activity_skips_only_that_run(tmp_path):
    api = FakeGarmin([act(1), act(2)], missing={1})
    written = run_sync(api, tmp_path, missing_errors=(NotFound,))
    assert [p.name for p in written] == ["garmin-2.zip"]


def test_caps_downloads_per_run(tmp_path):
    api = FakeGarmin([act(i) for i in range(1, 6)])
    assert len(run_sync(api, tmp_path, max_downloads=2)) == 2
    assert len(run_sync(api, tmp_path, max_downloads=2)) == 2  # the rest come next time


def test_odd_activity_ids_never_reach_a_file_name(tmp_path):
    with pytest.raises(ValueError):
        run_sync(FakeGarmin([act("../../etc/x")]), tmp_path)
    assert run_sync(FakeGarmin([act(-5), act(0)]), tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_no_partial_files_are_left_behind(tmp_path):
    run_sync(FakeGarmin([act(1)]), tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["garmin-1.zip"]


def test_inbox_follows_the_backend_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("MARATHON_INBOX_DIR", str(tmp_path))
    assert gs.inbox_dir() == tmp_path
    monkeypatch.setenv("MARATHON_INBOX_DIR", str(tmp_path / "missing"))
    with pytest.raises(gs.SyncError):
        gs.inbox_dir()  # a folder you chose is never created
    assert not (tmp_path / "missing").exists()


def test_sync_without_a_saved_sign_in_says_to_log_in(monkeypatch):
    monkeypatch.setattr(gs, "_garmin", lambda: None)
    monkeypatch.setattr(gs, "read_token", lambda: None)
    assert gs.main([]) == 2
