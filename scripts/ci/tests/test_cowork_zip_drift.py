"""Unit tests for the Cowork archive drift checker's content-signature logic.

These are git-independent: they build synthetic in-memory zips and assert the
signature is robust to timestamp churn, detects real content changes, and
recurses into inner ``.zip`` members. The end-to-end "committed archives are in
sync" guard is the script's own ``main()`` (wired as a CI step); here we prove
the comparison primitive it relies on is correct.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_cowork_zip_drift.py"

_spec = importlib.util.spec_from_file_location("check_cowork_zip_drift", SCRIPT)
drift = importlib.util.module_from_spec(_spec)
sys.modules["check_cowork_zip_drift"] = drift
_spec.loader.exec_module(drift)


def _zip(members: dict[str, bytes], date_time=(1980, 1, 1, 0, 0, 0)) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=date_time)
            zf.writestr(info, data)
    return buf.getvalue()


def test_signature_ignores_timestamp_churn():
    a = _zip({"SKILL.md": b"hello", "references/x.json": b"{}"}, date_time=(1980, 1, 1, 0, 0, 0))
    b = _zip({"SKILL.md": b"hello", "references/x.json": b"{}"}, date_time=(2030, 6, 15, 12, 30, 0))
    assert drift.zip_content_signature(a) == drift.zip_content_signature(b)


def test_signature_detects_changed_member():
    a = _zip({"SKILL.md": b"hello"})
    b = _zip({"SKILL.md": b"HELLO - changed"})
    assert drift.zip_content_signature(a) != drift.zip_content_signature(b)
    problems = drift.diff_signatures(
        drift.zip_content_signature(a), drift.zip_content_signature(b)
    )
    assert any("SKILL.md" in p and "changed" in p for p in problems)


def test_signature_detects_added_and_removed_member():
    committed = drift.zip_content_signature(_zip({"SKILL.md": b"x"}))
    fresh = drift.zip_content_signature(_zip({"SKILL.md": b"x", "references/new.md": b"y"}))
    problems = drift.diff_signatures(committed, fresh)
    assert any("references/new.md" in p for p in problems)


def test_signature_recurses_into_inner_zip():
    """A change buried inside a nested ``.zip`` member is detected even though
    the outer archive still has the same member name."""
    inner_v1 = _zip({"cost_api.py": b"def f():\n    return 1\n"})
    inner_v2 = _zip({"cost_api.py": b"def f():\n    return 2\n"})
    outer_v1 = _zip({"SKILL.md": b"s", "vendor/cost-runtime.zip": inner_v1})
    outer_v2 = _zip({"SKILL.md": b"s", "vendor/cost-runtime.zip": inner_v2})

    sig_v1 = drift.zip_content_signature(outer_v1)
    sig_v2 = drift.zip_content_signature(outer_v2)
    assert sig_v1 != sig_v2

    problems = drift.diff_signatures(sig_v1, sig_v2)
    assert any("cost-runtime.zip::cost_api.py" in p for p in problems), problems


def test_directory_entries_are_ignored():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("references/"), b"")
        zf.writestr("references/x.json", b"{}")
    flat = drift.flatten_signature(drift.zip_content_signature(buf.getvalue()))
    assert list(flat) == ["references/x.json"]
