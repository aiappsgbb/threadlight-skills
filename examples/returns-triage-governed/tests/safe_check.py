"""Delegate to the current lifecycle gate; never reuse the 2026 capture."""
from pathlib import Path
import runpy
import sys


root = Path(__file__).resolve().parents[3]
source = root / "skills/threadlight-safe-check/scripts/safe_check.py"
sys.path.insert(0, str(root))
implementation = runpy.run_path(str(source))
globals().update({name: value for name, value in implementation.items() if not name.startswith("__")})

if __name__ == "__main__":
    sys.exit(main())
