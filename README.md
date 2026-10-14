# Base block — training log (personal use, local)

Single-user local app to view the 13-week plan, upload **original .FIT files** from Garmin,
assign them to a planned session and compare planned against actual.

- `docs/private/` — gitignored, stays on your machine:
  - `plan.json` — the schedule the app seeds (weeks, dates, km per session, notes).
  - `plan-base-13-semanas.md` — **source of truth** for the plan, in prose.
  - `athlete-profile.md` — personal details (starting point, injury history, watch settings).
  - `seguimiento-base.md` — weekly tracking template.
- `backend/` — FastAPI + SQLite + Garmin FIT Python SDK.
- `frontend/` — React + TypeScript + Vite + Tailwind + Recharts.

The plan is seeded into the database from `docs/private/plan.json`, exactly as written there.
Without that file the app uses `backend/app/sample_plan.json`, a made-up 13-week plan; copy it to
`docs/private/plan.json` and edit it to use your own (or point `MARATHON_PLAN_PATH` at another
file). The app does not generate or modify workouts.

## Getting started

Requirements: Python 3.11+ and Node 18+.

```bash
./dev.sh
```

A single command: it installs whatever is missing (virtualenv, Python and Node dependencies),
starts the backend, waits for it to answer and only then brings up the frontend. Ctrl-C stops
both.

Installation only happens the first time; after that startup is immediate. Python dependencies
are reinstalled automatically if `requirements.txt` changes.

Open <http://localhost:5173>. The first request creates the database and seeds the plan's
weeks. Seeding never overwrites weeks that already exist, so edit the plan file before the
first start (or start from an empty database).
Vite listens on the whole local network (`host: true`), so from your phone on the same Wi-Fi you
can open `http://<laptop-ip>:5173`. The interface is responsive.

Ports can be changed with environment variables:

```bash
BACKEND_PORT=8001 FRONTEND_PORT=5174 ./dev.sh
```

### Manual start (two terminals)

```bash
# terminal 1
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend && npm run dev
```

If this is the first time and you are not using `./dev.sh`, install the dependencies first:

```bash
# backend
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# frontend (another terminal)
cd frontend
npm install
```

## Where the data is stored

Everything is private and stays outside the frontend's public assets:

```
backend/data/marathon.sqlite3     database
backend/data/original_files/      original .FIT files, named by their sha256 hash
backend/data/staging/             files awaiting confirmation
```

Paths are configurable through environment variables: `MARATHON_DATA_DIR`, `MARATHON_DB_PATH`,
`MARATHON_FILES_DIR`, `MARATHON_STAGING_DIR`.

### Backup and restore

```bash
# backup (with the app stopped, for a clean copy)
tar czf backup-$(date +%F).tar.gz -C backend data

# restore
cd backend && rm -rf data && tar xzf ../backup-2026-09-20.tar.gz
```

The database uses WAL: if you copy it while running, include `marathon.sqlite3-wal` and `-shm`
as well (they are already inside `data/`). The original files and the database must be restored
together: the database stores the path and hash of each file.

## Screens

1. **Week** — previous/next navigation, planned and actual km, signed difference, percentage
   completed, completed/planned sessions, running time. Each session shows target, actual and
   difference, with an expandable detail (HR, pace and elevation charts, climb and descent,
   heart-rate drift, laps, zones, effort and note). Extra runs are added to the total, always
   identified as extra.
2. **Block** — all 13 weeks at once: km so far against the plan to date, weekly planned vs. ran
   km, long-run progression, climb per week, time in your HR zones per week, and heart-rate drift
   per run. Clicking a week in a chart or the table opens it in the Week screen.
3. **Upload** — one .FIT or a ZIP with a single activity; preview with date, distance, duration
   and HR; week suggested from the date; week and session selection, or "Extra / unplanned" with
   a type; confirmation before saving; later reassignment; deletion with confirmation.
4. **Settings** — km and min/km; the real limits of your HR zones (entered by you, never derived
   from age, max HR or percentages); perceived effort and a note per run.

## Comparison rules implemented

- Distance difference = actual − target.
- Duration difference only if a duration target exists. The plan defines none: it shows
  "Not set" until you add one.
- You can edit the target distance and duration of each session; the original seeded value is
  kept and can be restored.
- Any reference pace in the plan is a reference, not a target. The two hours on the long run
  are a cap.
- Effort context (easy, ~2–3/10, Z2 as a guide) visible on every week.
- With zones configured, minutes and percentage are shown **relative to the time with measured
  heart rate**. There is no target for minutes in Z2 nor any invented compliance percentage.
- No HR data = unknown, never zero nor low effort.
- States: completed, planned (future date) and not recorded (past date with no run). Future
  sessions are never presented as failures. More distance or intensity is not rewarded.
- Weekly totals follow the assigned week, not the file's date.
- Climb and descent come from the watch's own session totals when the file has them; otherwise
  they are computed from the altitude samples, ignoring changes under 3 m (sensor noise). No
  altitude in the file = unknown, never flat.
- Heart-rate drift compares speed ÷ heart rate between the two halves of the moving time,
  leaving out the first 10 minutes, pauses, stops and gaps. It needs at least 30 minutes and is
  shown as a description, with no threshold. Hills, heat and wind also move it.

## File processing

- The content is validated as FIT (header + the SDK's integrity check); corrupt or unsupported
  files are rejected with a clear message.
- ZIP: at most 50 entries and 80 MB uncompressed; absolute paths or paths with `..` are
  rejected; exactly one activity is required.
- The original file is stored as is, with its sha256 hash, which also prevents duplicates.
- In SQLite: summary, timestamped samples (HR, distance, speed, altitude), laps, timer events, assignments, notes, zones and
  plan targets. What was recorded is stored separately from what was calculated.
- Main duration = the file's **timer time**. Elapsed time is shown separately. If the file has
  no timer, the fallback calculation is labelled explicitly.
- Time per zone is calculated from intervals between timestamps, discounting pauses (stop/start
  events) and without extending readings across gaps longer than 30 s.
- The exact zones used and the calculation date are recorded; recalculation is explicit when you
  save new zones.
- When the app learns to read something new from FIT files, runs imported earlier are re-read
  from their stored original on the next start (only the new fields change; dates, assignments,
  notes and zones are kept).

## Tests

```bash
cd backend && source .venv/bin/activate && pytest -q     # 38 tests
```

They always run against the sample plan, never your private one. They cover: exact seeding of
the plan and weekly totals; seeding idempotency preserving edits;
duplicate detection; reassignment; persistence after a restart; actual − target and missing
targets; zones with irregular samples, pauses, gaps and missing HR; FIT reading, ZIP with one
and with two activities, unsafe paths and corrupt files; altitude, watch vs. computed climb and
noise; heart-rate drift with warm-up, pauses and missing HR; the block overview; re-reading old
imports; and adding the new columns to an existing database.

Synthetic fixture: `backend/tests/fit_fixture.py` generates valid FIT files. To create a demo
one: `python tests/fit_fixture.py demo-activity.fit`. It is test data, not real history; import
it only if you want to explore the app, and delete it afterwards.

## Known limitations

- Verified with synthetic FIT files (generated by the fixture) and with the full upload →
  assignment → comparison flow in the browser. **Still to be verified with a real .FIT from a
  Garmin watch and with a real Garmin export ZIP**; real files carry many more messages and
  some unhandled field may show up.
- No connection to Garmin/Strava, no automatic sync, no accounts and no deployment.
- No predictions, no training advice and no medical recommendations.
- One file per planned session; anything else goes in as an extra run.
- No version history for the plan: only original values plus simple overrides.
- The whole file is assumed to be a running activity; intervals are not recognised.
- Position, cadence and running dynamics data are neither stored nor analysed (altitude is).
- Drift and climb are not adjusted for each other: a hilly second half shows up as drift.
