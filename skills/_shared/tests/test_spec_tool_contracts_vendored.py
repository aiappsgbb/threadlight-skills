"""Pattern 0 classifies SPEC § 6 tools exactly like threadlight-deploy.

threadlight-local-test's quickstart is a standalone package and cannot import
skills/_shared, so it ships a byte-identical copy of spec_tool_contracts.py.
Update both together (copy the shared file over the vendored one).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "skills" / "_shared" / "spec_tool_contracts.py"
VENDORED = (REPO / "skills" / "threadlight-local-test" / "references" / "quickstart" / "threadlight_quickstart"
            / "_spec_tool_contracts.py")


def test_vendored_spec_tool_contracts_is_byte_identical():
    assert VENDORED.is_file(), f"missing vendored copy {VENDORED.relative_to(REPO)}"
    assert VENDORED.read_bytes() == SHARED.read_bytes(), (
        f"{VENDORED.relative_to(REPO)} diverged from {SHARED.relative_to(REPO)}: copy the shared file over it")
