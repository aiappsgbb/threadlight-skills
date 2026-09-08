"""Keep scratch in this worktree; native tests are run by the exact-pin gate."""
import os
from pathlib import Path
import tempfile
import uuid

import pytest


def pytest_configure(config):
    scratch = Path(__file__).resolve().parents[3] / ".governance-validation"
    (scratch / "tmp").mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(scratch / "tmp")
    tempfile.tempdir = str(scratch / "tmp")
    config.addinivalue_line(
        "markers", "governance_runtime: real pinned ACS loader and OPA contract"
    )
    if not config.option.basetemp:
        config.option.basetemp = str(
            scratch / f"pytest-{uuid.uuid4().hex}"
        )


def pytest_collection_modifyitems(items):
    if os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") == "1":
        return
    for item in items:
        if item.get_closest_marker("governance_runtime"):
            item.add_marker(pytest.mark.skip(
                reason="Run scripts/ci/run-governance-pin-tests.py; skips are not runtime proof"
            ))
