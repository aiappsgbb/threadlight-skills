"""Inspect the BUILT + committed Cowork design bundle.

The seller-installable ``docs/downloads/threadlight-design.zip`` must match the
current ``skills/threadlight-design`` source. After the rebase that landed the
SPEC § 14 value-model contract (``feat(design): add SPEC §14 value-model
contract``), a stale bundle would ship a ``references/speckit-template.md``
without § 14 and would omit ``references/value-model-schema.md`` entirely.

This test lives under ``scripts/ci/tests/`` rather than
``skills/threadlight-design/tests/`` on purpose: the design Cowork zip is a
WHOLE-FOLDER archive, so anything under the skill's ``tests/`` ships inside the
published bundle. Keeping this publishing/CI guard out of the skill folder keeps
it out of the shipped archive (and off the Cowork per-skill companion budget).

These tests read the COMMITTED archive (they never rebuild it), so they are RED
against a stale bundle and GREEN once ``scripts/build-cowork-zips.sh`` has
regenerated it from current source.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ZIP = REPO_ROOT / "docs" / "downloads" / "threadlight-design.zip"
SRC = REPO_ROOT / "skills" / "threadlight-design"

# The § 14 value-model contract markers (mirror of check_pilot_contract.py's
# VALUE_MODEL_MARKERS plus the section heading the template emits).
SPECKIT_SECTION14_MARKERS = (
    "## 14. Value Model",
    "value_model:",
    "maturity_policy:",
    "success_event:",
    "baseline:",
    "accounting:",
)

pytestmark = pytest.mark.skipif(
    not ZIP.exists(),
    reason="run scripts/build-cowork-zips.sh first to produce the Cowork bundle",
)


def _read_member(name: str) -> str:
    with zipfile.ZipFile(ZIP) as archive:
        return archive.read(name).decode("utf-8")


def _member_names() -> set[str]:
    with zipfile.ZipFile(ZIP) as archive:
        return {n for n in archive.namelist() if not n.endswith("/")}


def test_design_zip_speckit_template_has_section14_value_model():
    text = _read_member("references/speckit-template.md")
    missing = [m for m in SPECKIT_SECTION14_MARKERS if m not in text]
    assert not missing, (
        "committed threadlight-design.zip ships a stale speckit-template.md "
        f"missing SPEC § 14 value-model markers {missing} "
        "(rebuild scripts/build-cowork-zips.sh)"
    )


def test_design_zip_ships_value_model_schema_reference():
    names = _member_names()
    assert "references/value-model-schema.md" in names, (
        "committed threadlight-design.zip is missing references/value-model-schema.md "
        "(rebuild scripts/build-cowork-zips.sh)"
    )


def test_design_zip_speckit_template_matches_current_source():
    """The published § 14 shape must be byte-for-byte the current source, so a
    seller who installs the bundle gets the same blank contract CI enforces."""
    published = _read_member("references/speckit-template.md")
    source = (SRC / "references" / "speckit-template.md").read_text(encoding="utf-8")
    assert published == source, (
        "committed threadlight-design.zip speckit-template.md drifted from source "
        "(rebuild scripts/build-cowork-zips.sh)"
    )


def test_design_zip_value_model_schema_matches_current_source():
    published = _read_member("references/value-model-schema.md")
    source = (SRC / "references" / "value-model-schema.md").read_text(encoding="utf-8")
    assert published == source, (
        "committed threadlight-design.zip value-model-schema.md drifted from source "
        "(rebuild scripts/build-cowork-zips.sh)"
    )
