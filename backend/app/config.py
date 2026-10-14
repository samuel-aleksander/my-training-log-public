"""Configurable, private data paths.

Nothing here is served as a public static asset. Override with env vars:
  MARATHON_DATA_DIR  -> root folder for database + original files
  MARATHON_PLAN_PATH -> training plan JSON (defaults to docs/private/plan.json,
                        falling back to the committed sample plan)
  MARATHON_INBOX_DIR -> folder watched for new .FIT/ZIP files (default data/inbox;
                        ~/Downloads works too: files there are only read, never moved)
  MARATHON_INBOX_POLL_SECONDS -> how often to look in it; 0 turns watching off
  MARATHON_DOCS_DIR  -> private plan prose and athlete profile the MCP server reads
                        (default docs/private)
"""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PRIVATE_DOCS_DIR = Path(os.environ.get("MARATHON_DOCS_DIR", APP_DIR.parents[1] / "docs" / "private"))
PRIVATE_PLAN_PATH = APP_DIR.parents[1] / "docs" / "private" / "plan.json"
SAMPLE_PLAN_PATH = APP_DIR / "sample_plan.json"
PLAN_PATH = Path(
    os.environ.get(
        "MARATHON_PLAN_PATH",
        PRIVATE_PLAN_PATH if PRIVATE_PLAN_PATH.exists() else SAMPLE_PLAN_PATH,
    )
)

DATA_DIR = Path(os.environ.get("MARATHON_DATA_DIR", APP_DIR.parent / "data"))
DB_PATH = Path(os.environ.get("MARATHON_DB_PATH", DATA_DIR / "marathon.sqlite3"))
FILES_DIR = Path(os.environ.get("MARATHON_FILES_DIR", DATA_DIR / "original_files"))
STAGING_DIR = Path(os.environ.get("MARATHON_STAGING_DIR", DATA_DIR / "staging"))
DEFAULT_INBOX_DIR = DATA_DIR / "inbox"
INBOX_DIR = Path(os.path.expanduser(os.environ.get("MARATHON_INBOX_DIR", DEFAULT_INBOX_DIR)))
INBOX_POLL_SECONDS = int(os.environ.get("MARATHON_INBOX_POLL_SECONDS", 15))
INBOX_SETTLE_SECONDS = 3  # skip files modified more recently: maybe still downloading

# ZIP safety limits
MAX_ZIP_ENTRIES = 50
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024  # 80 MB
MAX_UPLOAD_BYTES = 40 * 1024 * 1024  # 40 MB

# Sampling analysis limits
MAX_SAMPLE_GAP_SECONDS = 30  # longer gaps are not counted as training time


def ensure_dirs() -> None:
    for d in (DATA_DIR, FILES_DIR, STAGING_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if INBOX_DIR == DEFAULT_INBOX_DIR:  # a folder you chose is never created for you
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
