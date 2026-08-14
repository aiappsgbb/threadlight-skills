"""Shared helper: run the assessor against a throwaway copy of a fixture.

Why this exists
---------------
`references/fixtures/<name>/` doubles as a committed exemplar (the design
docs treat refreshing its `production-readiness-*.json|md|csv` outputs as a
deliberate act tied to a behaviour change). Tests that pointed `--root`
straight at a fixture rewrote those exemplars as a side effect: the manifest
and report drifted on every run and `production-readiness-trend.csv` grew a
row each time, so a clean checkout went dirty just by running the suite.

Copying first keeps the exemplars pristine and makes each run hermetic.

`checked_at` is also restamped here. The committed safe-check manifests carry
fixed timestamps, so anything driving the default CLI path starts failing the
24h freshness pre-flight a day after the fixture is written — a clock
failure, not a code failure. Restamping the *copy* keeps tests
time-independent without weakening the gate for real callers.

Not named `test_*`, so pytest ignores it.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from datetime import datetime, timezone

TESTS_DIR = pathlib.Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR.parent / "references" / "fixtures"


def freshen_safe_check(workdir: pathlib.Path) -> None:
    """Restamp the copied safe-check manifest's `checked_at` to now."""
    manifest = workdir / "tests" / "postdeploy-manifest.json"
    if not manifest.exists():
        return
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def fixture_workdir(name: str, *, freshen: bool = True) -> pathlib.Path:
    """Copy `references/fixtures/<name>` to a temp dir and return its path.

    Caller owns the directory; it is left on disk for post-mortem inspection
    the same way `tempfile.mkdtemp()` callers elsewhere in this suite do.
    """
    src = FIXTURES_DIR / name
    if not src.is_dir():
        raise FileNotFoundError(f"fixture not found: {src}")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"tl-{name}-"))
    for child in src.iterdir():
        if child.is_file():
            shutil.copy(child, tmp / child.name)
        else:
            shutil.copytree(child, tmp / child.name)
    if freshen:
        freshen_safe_check(tmp)
    return tmp
