"""Delegate to the current lifecycle gate; never reuse the 2026 capture."""
from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    source = root / "skills/threadlight-safe-check/scripts/safe_check.py"
    sys.path.insert(0, str(root))
    runpy.run_path(str(source), run_name="__main__")
