# Garmin sync

Downloads your new runs from Garmin Connect into the app's import folder, twice a day. The app
then imports them like any file you drop there (see "Import folder" in the main README).

It uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect), an
unofficial library: it signs in the way Garmin's own app does, which Garmin does not officially
allow and can break at any time. If it stops working, uploading files by hand still works.

## How it is kept safe

- **Separate from the app.** Its own folder and virtualenv. The backend, the MCP server and
  Claude never import it and never see your Garmin sign-in.
- **Password typed once, never saved.** `login` asks for it (and the MFA code) in your terminal.
  Only the resulting token is kept, in your macOS login Keychain (service
  `marathon-garmin-sync`). It is never printed or logged. `logout` deletes it.
- **Read-only use.** The script calls only "list activities" and "download original file".
  Nothing it calls changes your Garmin account.
- **Pinned code.** `requirements.txt` pins every package with hashes, so pip refuses anything
  that differs from what was reviewed. Dependencies were at least two weeks old when pinned.
  garminconnect 0.3.20 is newer, but its sign-in, token storage and download code was read first.
  It only contacts `*.garmin.com`. Nothing upgrades on its own.
- **Gentle on Garmin.** Two runs a day, at most 20 downloads per run with a pause in between,
  and no retry loop when Garmin is rate-limiting.
- **Checked files.** Only FIT or ZIP data under 40 MB is written. It is written under a hidden
  name first and then renamed, so the app never reads half a file.

## Setup

```bash
cd garmin_sync
python3 -m venv .venv
.venv/bin/pip install --require-hashes --only-binary :all: --no-deps -r requirements.txt
.venv/bin/python -I garmin_sync.py login
.venv/bin/python -I garmin_sync.py --dry-run     # lists what it would download
.venv/bin/python -I garmin_sync.py               # downloads runs from the last 7 days
./schedule.sh install                            # then every day at 12:30 and 21:30
```

`--days 30` looks further back. Files are named `garmin-<activity id>.zip`, and a run already in
the folder is not downloaded again. The app skips anything that is not a run, falls outside the
plan, or is already imported.

`./schedule.sh run` runs the job now, `./schedule.sh log` shows its log
(`~/Library/Logs/marathon-garmin-sync.log`) and `./schedule.sh remove` stops it. If you set
`MARATHON_INBOX_DIR` for the app, set it when you run `install` too.

If the log says "Run login again", the token expired or Garmin revoked it: run `login` again.

## Upgrading garminconnect

Only when it breaks. Read what changed in the new version (and its dependencies), then pin it in
`requirements.txt` with its hashes from PyPI and reinstall into a fresh `.venv`.

## Tests

```bash
cd backend && .venv/bin/pytest -q ../garmin_sync
```

They use a fake Garmin that fails on any call other than listing and downloading. They cover:
only runs downloaded, files already in the folder skipped, dry run, non-FIT and oversized
responses, a deleted activity, the per-run cap, odd activity ids, no half-written files, and the
import folder setting.
