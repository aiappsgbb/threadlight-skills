"""Structural guard: the six receiver scaffolds are actually shipped.

The SKILL's "Reference files" table marks six scaffold directories ``shipped``
and Step-1 says "Copy from ``references/scaffolds/{receiver-type}/``". This test
makes that claim self-verifying: every directory exists, contains its required
files, and every Python file compiles. If someone lists a new scaffold as
shipped without building it, this test fails.
"""
import py_compile
from pathlib import Path

import pytest

SCAFFOLDS = Path(__file__).resolve().parent.parent / "references" / "scaffolds"

# Two shapes: ACA (container image + Bicep) and Functions (v2 code, no Dockerfile).
ACA_FILES = {
    "receiver.py",
    "pyproject.toml",
    "Dockerfile",
    "receiver.bicep",
    "local.test.py",
    "README.md",
}
FUNCTION_FILES = {
    "function_app.py",
    "receiver_core.py",
    "host.json",
    "requirements.txt",
    "local.test.py",
    "README.md",
}

SHIPPED = {
    "aca-job-cron": ACA_FILES,
    "aca-job-manual": ACA_FILES,
    "aca-consumer": ACA_FILES,
    "function-http": FUNCTION_FILES,
    "function-servicebus": FUNCTION_FILES,
    "function-eventgrid": FUNCTION_FILES,
}


@pytest.mark.parametrize("scaffold,required", sorted(SHIPPED.items()))
def test_scaffold_ships_required_files(scaffold, required):
    directory = SCAFFOLDS / scaffold
    assert directory.is_dir(), f"missing scaffold directory: {scaffold}"
    present = {p.name for p in directory.iterdir() if p.is_file()}
    missing = required - present
    assert not missing, f"{scaffold} is missing required files: {sorted(missing)}"


@pytest.mark.parametrize("scaffold", sorted(SHIPPED))
def test_scaffold_python_compiles(scaffold):
    directory = SCAFFOLDS / scaffold
    for py_file in sorted(directory.glob("*.py")):
        py_compile.compile(str(py_file), doraise=True)


def test_no_legacy_v1_function_json():
    """Functions scaffolds must use the v2 programming model (no function.json)."""
    for legacy in SCAFFOLDS.rglob("function.json"):
        raise AssertionError(f"legacy v1 binding file found: {legacy}")


def test_readme_index_lists_every_shipped_scaffold():
    index = (SCAFFOLDS / "README.md").read_text(encoding="utf-8")
    for scaffold in SHIPPED:
        assert scaffold in index, f"scaffolds/README.md does not mention {scaffold}"
