import sqlite3
from pathlib import Path

from . import config

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS leaves
# existing databases untouched, so they are added here instead.
MIGRATIONS = {
    "activities": [
        ("ascent_m", "REAL"),
        ("descent_m", "REAL"),
        ("elevation_source", "TEXT"),
        ("parser_version", "INTEGER NOT NULL DEFAULT 1"),
        ("auto_assigned", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "samples": [("altitude_m", "REAL")],
}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()
