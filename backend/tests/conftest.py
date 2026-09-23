import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Tests always use the committed sample plan, never a private one.
os.environ["MARATHON_PLAN_PATH"] = str(ROOT / "app" / "sample_plan.json")
