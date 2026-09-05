"""Native example tests use the same immutable Linux environment as Task 7."""
import os
from pathlib import Path
import sys
import tempfile
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[3]
for path in (
    ROOT, ROOT / "skills/threadlight-govern/tests",
    ROOT / "skills/threadlight-deploy/tests",
    ROOT / "skills/threadlight-govern/references",
    ROOT / "examples/returns-triage-governed/src/agent",
):
    sys.path.insert(0, str(path))


def pytest_configure(config):
    scratch = ROOT / ".governance-validation"
    os.environ["TMPDIR"] = str(scratch / "tmp")
    tempfile.tempdir = os.environ["TMPDIR"]
    config.addinivalue_line("markers", "governance_runtime: published MAF/ACS/OPA integration")
    if not config.option.basetemp:
        config.option.basetemp = str(scratch / ("t13-" + uuid.uuid4().hex))


def pytest_collection_modifyitems(items):
    if os.environ.get("THREADLIGHT_GOVERNANCE_RUNTIME") != "1":
        for item in items:
            if item.get_closest_marker("governance_runtime"):
                item.add_marker(pytest.mark.skip(reason="requires published-pin Linux runtime; not proof"))
