"""Start the Garmin sync job (garmin_sync/) on demand and report how it went.

The app still never touches Garmin or the sign-in token: it only asks launchd to
run the job garmin_sync/schedule.sh installed, and reads that job's log. The job
writes into the import folder, which the app imports as usual.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LABEL = "com.marathon.garmin-sync"
LOG_PATH = Path.home() / "Library" / "Logs" / "marathon-garmin-sync.log"
MIN_INTERVAL_S = 60  # one press a minute is plenty, and keeps Garmin happy
MAX_LOG_BYTES = 8 * 1024

# Exit codes of garmin_sync.py
EXIT_MESSAGES = {
    0: None,
    2: "The Garmin sign-in needs renewing: run garmin_sync.py login in a terminal.",
    3: "Garmin is rate-limiting. The scheduled sync will try again later.",
}


class GarminJobError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


_lock = threading.Lock()
_run: dict = {"pid": None, "started_at": None, "log_offset": None}


def _target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)


def _job() -> dict | None:
    """launchd's view of the job, or None when schedule.sh has not installed it."""
    r = _launchctl("print", _target())
    if r.returncode != 0:
        return None
    exit_code = re.search(r"last exit code = (-?\d+)", r.stdout)
    return {
        "running": re.search(r"^\s*state = running", r.stdout, re.M) is not None,
        "exit_code": int(exit_code.group(1)) if exit_code else None,
    }


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _log_lines(offset: int | None) -> list[str]:
    """What the job logged since `offset`, without the timestamps."""
    if offset is None or not LOG_PATH.exists():
        return []
    with LOG_PATH.open("rb") as f:
        f.seek(offset)
        text = f.read(MAX_LOG_BYTES).decode("utf-8", "replace")
    return [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", "", line) for line in text.splitlines() if line.strip()]


def status() -> dict:
    job = _job()
    if job is None:
        return {"installed": False}
    with _lock:
        run = dict(_run)
    running = _alive(run["pid"]) or job["running"]
    exit_code = None if running or run["started_at"] is None else job["exit_code"]
    return {
        "installed": True,
        "running": running,
        "started_at": run["started_at"],
        "lines": _log_lines(run["log_offset"]),
        "exit_code": exit_code,
        "problem": EXIT_MESSAGES.get(exit_code, "The sync failed. See the log.") if exit_code else None,
    }


def start() -> dict:
    """Run the job now. Pressing again while it runs just reports on that run."""
    job = _job()
    if job is None:
        raise GarminJobError("Garmin sync is not set up. See garmin_sync/README.md.", 404)
    with _lock:
        if not _alive(_run["pid"]):
            if job["running"]:
                raise GarminJobError("The scheduled Garmin sync is running right now.", 409)
            last = _run["started_at"]
            if last and time.time() - datetime.fromisoformat(last).timestamp() < MIN_INTERVAL_S:
                raise GarminJobError("A sync ran less than a minute ago. Try again shortly.", 429)
            offset = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0
            # -p prints the new process id, so we can tell when this run is over.
            r = _launchctl("kickstart", "-p", _target())
            if r.returncode != 0:
                raise GarminJobError("launchd did not start the Garmin sync.", 502)
            pid = re.search(r"\d+", r.stdout)
            _run.update(
                pid=int(pid.group()) if pid else None,
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                log_offset=offset,
            )
    return status()
