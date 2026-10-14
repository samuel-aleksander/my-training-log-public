PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS weeks (
  id           INTEGER PRIMARY KEY,
  number       INTEGER NOT NULL UNIQUE,
  start_date   TEXT NOT NULL,
  end_date     TEXT NOT NULL,
  planned_km   REAL NOT NULL,          -- seeded weekly total from the plan
  notes        TEXT                     -- "Complementary running work" column
);

CREATE TABLE IF NOT EXISTS sessions (
  id                     INTEGER PRIMARY KEY,
  week_id                INTEGER NOT NULL REFERENCES weeks(id),
  label                  TEXT NOT NULL,      -- A | B | C | L
  planned_date           TEXT NOT NULL,
  seeded_km              REAL NOT NULL,      -- original value from the plan, never changed
  override_km            REAL,               -- optional user edit
  seeded_duration_min    REAL,               -- NULL: plan has no duration target
  override_duration_min  REAL,
  notes                  TEXT,
  UNIQUE (week_id, label)
);

CREATE TABLE IF NOT EXISTS activities (
  id                  INTEGER PRIMARY KEY,
  file_hash           TEXT NOT NULL UNIQUE,     -- sha256 of the original .fit bytes
  original_filename   TEXT NOT NULL,
  stored_path         TEXT NOT NULL,
  start_time          TEXT NOT NULL,            -- recorded date/time, never modified
  distance_m          REAL,
  timer_time_s        REAL,
  elapsed_time_s      REAL,
  duration_source     TEXT NOT NULL,            -- 'timer' | 'fallback_samples' | 'fallback_elapsed'
  avg_hr              INTEGER,
  max_hr              INTEGER,
  has_hr              INTEGER NOT NULL DEFAULT 0,
  sport               TEXT,
  ascent_m            REAL,                     -- NULL: no altitude in the file
  descent_m           REAL,
  elevation_source    TEXT,                     -- 'device' (watch totals) | 'samples' (computed)
  parser_version      INTEGER NOT NULL DEFAULT 1,
  is_demo             INTEGER NOT NULL DEFAULT 0,
  -- assignment
  week_id             INTEGER REFERENCES weeks(id),
  session_id          INTEGER REFERENCES sessions(id),
  is_extra            INTEGER NOT NULL DEFAULT 0,
  extra_type          TEXT,
  -- user annotations
  rpe                 INTEGER,
  note                TEXT,
  created_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_activity_per_session
  ON activities(session_id) WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS samples (
  activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  ts          INTEGER NOT NULL,     -- epoch seconds, as recorded
  hr          INTEGER,              -- NULL = unknown, never 0
  distance_m  REAL,
  speed_mps   REAL,
  altitude_m  REAL                  -- NULL = not recorded
);
CREATE INDEX IF NOT EXISTS idx_samples_activity ON samples(activity_id, ts);

CREATE TABLE IF NOT EXISTS laps (
  activity_id    INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  idx            INTEGER NOT NULL,
  start_time     TEXT,
  distance_m     REAL,
  timer_time_s   REAL,
  elapsed_time_s REAL,
  avg_hr         INTEGER,
  max_hr         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_laps_activity ON laps(activity_id, idx);

CREATE TABLE IF NOT EXISTS timer_events (
  activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  ts          INTEGER NOT NULL,
  event       TEXT,
  event_type  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_activity ON timer_events(activity_id, ts);

-- Derived values, kept separate from recorded values.
CREATE TABLE IF NOT EXISTS zone_results (
  activity_id   INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  zone_name     TEXT NOT NULL,
  seconds       REAL NOT NULL,
  PRIMARY KEY (activity_id, zone_name)
);

CREATE TABLE IF NOT EXISTS zone_runs (
  activity_id       INTEGER PRIMARY KEY REFERENCES activities(id) ON DELETE CASCADE,
  zones_json        TEXT NOT NULL,   -- the exact zone definitions used
  computed_at       TEXT NOT NULL,
  measured_active_s REAL NOT NULL,
  unknown_hr_s      REAL NOT NULL,
  paused_s          REAL NOT NULL,
  gap_s             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
