"""The "Sync from Garmin" button: the app only starts the launchd job and reads its log.

launchctl is faked, so nothing here starts the real job or reaches Garmin.
"""

import os
import subprocess
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="marathon-test-")
os.environ.setdefault("MARATHON_DATA_DIR", TMP)
os.environ.setdefault("MARATHON_DB_PATH", os.path.join(TMP, "test.sqlite3"))

from fastapi.testclient import TestClient  # noqa: E402

from app import garmin_job, main  # noqa: E402

client = TestClient(main.app)


class FakeLaunchd:
    def __init__(self, installed=True):
        self.installed = installed
        self.running = False
        self.exit_code = None
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if not self.installed:
            return subprocess.CompletedProcess(args, 113, "", "Could not find service")
        if args[0] == "print":
            state = "running" if self.running else "not running"
            last = "(never exited)" if self.exit_code is None else self.exit_code
            out = f"\tstate = {state}\n\tlast exit code = {last}\n"
            return subprocess.CompletedProcess(args, 0, out, "")
        if args[0] == "kickstart":
            self.running = True
            return subprocess.CompletedProcess(args, 0, "4242\n", "")
        raise AssertionError(f"unexpected launchctl call: {args}")

    def finish(self, exit_code, log_path, lines):
        with log_path.open("a") as f:
            f.writelines(f"2026-10-28 18:00:0{i} {line}\n" for i, line in enumerate(lines))
        self.running = False
        self.exit_code = exit_code


@pytest.fixture
def launchd(monkeypatch, tmp_path):
    fake = FakeLaunchd()
    log = tmp_path / "sync.log"
    log.write_text("2026-10-28 12:30:00 an older run, not shown\n")
    monkeypatch.setattr(garmin_job, "_launchctl", fake)
    monkeypatch.setattr(garmin_job, "LOG_PATH", log)
    monkeypatch.setattr(garmin_job, "_alive", lambda pid: pid == 4242 and fake.running)
    monkeypatch.setattr(garmin_job, "_run", {"pid": None, "started_at": None, "log_offset": None})
    fake.log = log
    return fake


def start():
    return client.post("/api/garmin/sync", json={})


def test_not_installed_hides_the_button(launchd):
    launchd.installed = False
    assert client.get("/api/garmin/sync").json() == {"installed": False}
    r = start()
    assert r.status_code == 404
    assert "garmin_sync/README.md" in r.json()["detail"]


def test_start_runs_the_job_and_reports_only_this_runs_log(launchd):
    r = start()
    assert r.status_code == 200
    assert r.json()["running"] is True
    assert ("kickstart", "-p", garmin_job._target()) in launchd.calls

    launchd.finish(0, launchd.log, ["2 run(s) since 2026-10-21, 1 new.", "downloaded garmin-1.zip (Easy run)"])
    s = client.get("/api/garmin/sync").json()
    assert s["running"] is False
    assert s["lines"] == ["2 run(s) since 2026-10-21, 1 new.", "downloaded garmin-1.zip (Easy run)"]
    assert s["exit_code"] == 0 and s["problem"] is None


def test_a_failed_sign_in_says_what_to_do(launchd):
    start()
    launchd.finish(2, launchd.log, ["The saved sign-in no longer works. Run login again."])
    s = client.get("/api/garmin/sync").json()
    assert s["exit_code"] == 2
    assert "login" in s["problem"]


def test_pressing_again_while_running_does_not_start_a_second_run(launchd):
    start()
    start()
    assert sum(c[0] == "kickstart" for c in launchd.calls) == 1


def test_pressing_again_within_a_minute_is_refused(launchd):
    start()
    launchd.finish(0, launchd.log, ["0 run(s) since 2026-10-21, 0 new."])
    r = start()
    assert r.status_code == 429
    assert sum(c[0] == "kickstart" for c in launchd.calls) == 1


def test_a_scheduled_run_in_progress_is_not_restarted(launchd):
    launchd.running = True
    assert start().status_code == 409
    assert not [c for c in launchd.calls if c[0] == "kickstart"]


def test_another_site_cannot_start_it_with_a_plain_form_post(launchd):
    r = client.post("/api/garmin/sync", data={"x": "1"})
    assert r.status_code == 422
    assert not [c for c in launchd.calls if c[0] == "kickstart"]


def test_the_app_never_reads_the_token():
    src = open(garmin_job.__file__).read()
    assert "find-generic-password" not in src and "garminconnect" not in src
