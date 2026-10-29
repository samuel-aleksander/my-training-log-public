"""Download new runs from Garmin Connect into the app's import folder.

Kept apart from the backend on purpose: the backend, the MCP server and Claude never
see Garmin credentials, and this script never touches the database. It only lists
your recent activities and downloads the original file of each new run; nothing it
calls writes to your Garmin account. The app's import folder does the rest.

  .venv/bin/python garmin_sync.py login     sign in once (password and MFA typed here, never saved)
  .venv/bin/python garmin_sync.py           download new runs from the last 7 days
  .venv/bin/python garmin_sync.py logout    remove the saved sign-in from the Keychain

The sign-in token is kept in your login Keychain (service "marathon-garmin-sync").
It gives full access to your Garmin account, so it is never printed or logged.
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

KEYCHAIN_SERVICE = "marathon-garmin-sync"
KEYCHAIN_ACCOUNT = "garmin-connect"

REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INBOX_DIR = REPO_DIR / "backend" / "data" / "inbox"

MAX_FILE_BYTES = 40 * 1024 * 1024  # the backend refuses anything larger
MAX_DOWNLOADS = 20  # per run, so a bad date range can't hammer Garmin
PAUSE_SECONDS = 2.0  # between downloads, for the same reason


class SyncError(Exception):
    """A problem worth one line in the log, with what to do about it."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


# ------------------------------------------------------------------ Keychain


def read_token() -> str | None:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\n") or None


def save_token(token: str) -> None:
    # Passed as an argument: `security -i` and the -w prompt both truncate long
    # values. macOS hides a process's arguments from other users, and your own
    # processes can read this item anyway, so nothing is exposed that wasn't.
    r = subprocess.run(
        ["security", "add-generic-password", "-U",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", token],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SyncError("Could not save the sign-in to the Keychain.")
    if read_token() != token:
        raise SyncError("The Keychain did not keep the sign-in intact.")


def delete_token() -> bool:
    r = subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True, text=True,
    )
    return r.returncode == 0


# ------------------------------------------------------------------ sync


def inbox_dir() -> Path:
    """The same folder the backend watches (MARATHON_INBOX_DIR, else backend/data/inbox)."""
    chosen = os.environ.get("MARATHON_INBOX_DIR")
    inbox = Path(os.path.expanduser(chosen)) if chosen else DEFAULT_INBOX_DIR
    if not inbox.is_dir():
        if chosen:  # like the backend, a folder you chose is never created for you
            raise SyncError(f"The import folder {inbox} does not exist.")
        inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def is_run(activity: dict) -> bool:
    # running, trail_running, treadmill_running, track_running, virtual_run, ultra_run...
    # Anything that slips through is skipped by the backend, which checks the FIT sport.
    return "run" in ((activity.get("activityType") or {}).get("typeKey") or "")


def looks_like_activity_file(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04" or data[8:12] == b".FIT"


def sync(api, inbox: Path, since: date, original_format, dry_run: bool = False,
         missing_errors: tuple = (), max_downloads: int = MAX_DOWNLOADS,
         pause: float = PAUSE_SECONDS) -> list[Path]:
    """Download each run since `since` that is not in the inbox yet. Returns the new files.

    Only two library calls are made: listing activities and downloading one.
    `missing_errors` are the library's "not found" errors: that run is skipped, not the rest.
    """
    activities = api.get_activities_by_date(since.isoformat(), sortorder="asc")
    runs = [a for a in activities if is_run(a)]
    todo = []
    for a in runs:
        aid = int(a["activityId"])  # also refuses anything odd before it reaches a file name
        if aid <= 0:
            continue
        target = inbox / f"garmin-{aid}.zip"
        if not target.exists():
            todo.append((aid, target, a))

    log(f"{len(runs)} run(s) since {since}, {len(todo)} new.")
    if len(todo) > max_downloads:
        log(f"Downloading the first {max_downloads}; the rest come next time.")
        todo = todo[:max_downloads]

    written = []
    for i, (aid, target, a) in enumerate(todo):
        label = f"{(a.get('startTimeLocal') or '')[:16]} {a.get('activityName') or ''}".strip()
        if dry_run:
            log(f"would download {target.name} ({label})")
            continue
        if i:
            time.sleep(pause)
        try:
            data = api.download_activity(aid, dl_fmt=original_format)
        except missing_errors:
            log(f"skipped {aid} ({label}): Garmin no longer has it.")
            continue
        if not data or len(data) > MAX_FILE_BYTES or not looks_like_activity_file(data):
            log(f"skipped {aid} ({label}): Garmin did not send a FIT or ZIP file.")
            continue
        # Write under a dot-name (the inbox ignores those) and rename, so the
        # backend never reads half a file.
        part = inbox / f".{target.name}.part"
        part.write_bytes(data)
        os.replace(part, target)
        written.append(target)
        log(f"downloaded {target.name} ({label})")
    return written


# ------------------------------------------------------------------ commands


def _garmin():
    try:
        import garminconnect
    except ImportError:
        raise SyncError("garminconnect is not installed. See garmin_sync/README.md.") from None
    return garminconnect


def cmd_login(_args) -> None:
    gc = _garmin()
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not saved): ")
    api = gc.Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    try:
        api.login()
    except gc.GarminConnectAuthenticationError:
        raise SyncError("Garmin did not accept the sign-in.") from None
    except gc.GarminConnectTooManyRequestsError:
        raise SyncError("Garmin is limiting sign-ins right now. Try again in an hour.") from None
    save_token(api.client.dumps())
    log("Signed in. The token is in the Keychain; your password was not saved.")


def cmd_logout(_args) -> None:
    log("Removed the saved sign-in." if delete_token() else "There was no saved sign-in.")


def cmd_sync(args) -> None:
    gc = _garmin()
    token = read_token()
    if not token:
        raise SyncError("No saved sign-in. Run: .venv/bin/python garmin_sync.py login", exit_code=2)
    try:
        json.loads(token)
    except ValueError:
        raise SyncError("The saved sign-in is damaged. Run login again.", exit_code=2) from None

    inbox = inbox_dir()
    api = gc.Garmin()  # no credentials: if the token stops working, we stop too
    try:
        api.login(tokenstore=token)
        sync(api, inbox, date.today() - timedelta(days=args.days),
             gc.Garmin.ActivityDownloadFormat.ORIGINAL, dry_run=args.dry_run,
             missing_errors=(gc.GarminConnectNotFoundError,))
    except gc.GarminConnectAuthenticationError:
        raise SyncError("The saved sign-in no longer works. Run login again.", exit_code=2) from None
    except gc.GarminConnectTooManyRequestsError:
        raise SyncError("Garmin is rate-limiting; nothing downloaded. Next run will retry.", exit_code=3) from None
    except gc.GarminConnectConnectionError as exc:
        raise SyncError(f"Could not reach Garmin Connect ({type(exc).__name__}).") from None
    finally:
        # Tokens refresh themselves now and then; keep the newest one.
        refreshed = api.client.dumps() if api.client.is_authenticated else None
        if refreshed and refreshed != token:
            save_token(refreshed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", nargs="?", default="sync", choices=["sync", "login", "logout"])
    parser.add_argument("--days", type=int, default=7, help="how far back to look (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")

    commands = {"sync": cmd_sync, "login": cmd_login, "logout": cmd_logout}
    try:
        commands[args.command](args)
    except SyncError as exc:
        log(str(exc))
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
