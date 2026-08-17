"""Tests for the threadlight-qualify skill (`qualify.py`)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import qualify  # noqa: E402
from qualify import QualificationError, run_qualification, PROVENANCE_VALUES  # noqa: E402

FIXTURES = SKILL_ROOT / "references" / "fixtures" / "sample-qualification"
PINNED = "2026-06-12T12:00:00+00:00"


def _profile() -> dict:
    return json.loads((FIXTURES / "profile.json").read_text())


def _profile_with_roi() -> dict:
    return json.loads((FIXTURES / "profile-with-roi.json").read_text())


def _run(profile, tmp_path, generated_at=PINNED):
    return run_qualification(profile, output_dir=tmp_path, generated_at=generated_at)


# ---------------------------------------------------------------------------
# Validation — writes nothing on failure
# ---------------------------------------------------------------------------

def test_missing_annual_volume_writes_no_files(tmp_path):
    profile = _profile()
    del profile["annual_transaction_volume"]
    with pytest.raises(QualificationError):
        _run(profile, tmp_path)
    assert not (tmp_path / "qualification").exists()


def test_missing_any_required_field_writes_nothing(tmp_path):
    for field in qualify.REQUIRED_FIELDS:
        profile = _profile()
        profile.pop(field, None)
        target = tmp_path / field
        with pytest.raises(QualificationError):
            _run(profile, target)
        assert not (target / "qualification").exists()


def test_non_positive_volume_rejected(tmp_path):
    profile = _profile()
    profile["annual_transaction_volume"] = 0
    with pytest.raises(QualificationError):
        _run(profile, tmp_path)
    assert not (tmp_path / "qualification").exists()


# ---------------------------------------------------------------------------
# Happy path — files written
# ---------------------------------------------------------------------------

def test_writes_core_artifacts(tmp_path):
    result = _run(_profile(), tmp_path)
    outdir = tmp_path / "qualification"
    assert (outdir / "sizing.md").exists()
    assert (outdir / "sizing-manifest.json").exists()
    assert (outdir / "discovery.md").exists()
    # No ROI inputs → no roi.md
    assert not (outdir / "roi.md").exists()
    assert result["roi_written"] is False


def test_discovery_declares_no_live_probe(tmp_path):
    _run(_profile(), tmp_path)
    text = (tmp_path / "qualification" / "discovery.md").read_text()
    assert "no live discovery" in text.lower()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_sizing_manifest_bytes_deterministic(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _run(_profile(), a)
    _run(_profile(), b)
    bytes_a = (a / "qualification" / "sizing-manifest.json").read_bytes()
    bytes_b = (b / "qualification" / "sizing-manifest.json").read_bytes()
    assert bytes_a == bytes_b


# ---------------------------------------------------------------------------
# Provenance + hub/app separation
# ---------------------------------------------------------------------------

def test_every_assumption_has_allowed_provenance(tmp_path):
    result = _run(_profile(), tmp_path)
    assumptions = result["sizing_manifest"]["assumptions"]
    assert assumptions, "expected a non-empty assumptions ledger"
    for a in assumptions:
        assert a["provenance"] in PROVENANCE_VALUES, a


def test_hub_and_application_sizings_are_separate(tmp_path):
    result = _run(_profile(), tmp_path)
    sizings = result["sizing_manifest"]["sizings"]
    kinds = [s["kind"] for s in sizings]
    assert "citadel-hub" in kinds
    assert "threadlight-application" in kinds
    hub = [s for s in sizings if s["kind"] == "citadel-hub"]
    app = [s for s in sizings if s["kind"] == "threadlight-application"]
    assert len(hub) == 1
    # MVP + production application sizings
    assert {s["stage"] for s in app} == {"mvp", "production"}
    # Hub cost is estate-billed and separate (not folded into an app manifest)
    assert hub[0]["estate_billed"] is True
    assert "cost_manifest" not in hub[0]


def test_normalized_load_profile_present(tmp_path):
    result = _run(_profile(), tmp_path)
    lp = result["sizing_manifest"]["load_profile"]
    assert lp["monthly_transactions"] == pytest.approx(1200000 / 12)
    assert lp["workload_class"] == "chat-agent"
    assert lp["peak_requests_per_second"] > 0


# ---------------------------------------------------------------------------
# Optional ROI — positive and negative
# ---------------------------------------------------------------------------

def test_roi_written_only_with_both_inputs(tmp_path):
    result = _run(_profile_with_roi(), tmp_path)
    assert result["roi_written"] is True
    assert (tmp_path / "qualification" / "roi.md").exists()


def test_roi_positive(tmp_path):
    profile = _profile_with_roi()
    profile["current_annual_cost_usd"] = 8000000
    profile["current_handling_minutes_per_transaction"] = 15
    _run(profile, tmp_path)
    text = (tmp_path / "qualification" / "roi.md").read_text()
    assert "ROI is positive" in text


def test_roi_negative(tmp_path):
    profile = _profile_with_roi()
    profile["current_annual_cost_usd"] = 1000
    profile["current_handling_minutes_per_transaction"] = 0.1
    _run(profile, tmp_path)
    text = (tmp_path / "qualification" / "roi.md").read_text()
    assert "ROI is negative" in text


def test_roi_absent_without_handling_minutes(tmp_path):
    profile = _profile_with_roi()
    del profile["current_handling_minutes_per_transaction"]
    result = _run(profile, tmp_path)
    assert result["roi_written"] is False
    assert not (tmp_path / "qualification" / "roi.md").exists()


# ---------------------------------------------------------------------------
# No discovery — qualify must not import azd/bicep/discover
# ---------------------------------------------------------------------------

def test_no_discovery_imports():
    src = (SCRIPTS / "qualify.py").read_text()
    # Guard against real discovery — imports/calls, not docstring prose.
    for banned in ("\nimport subprocess", "from discover", "\nimport discover", "discover_resources("):
        assert banned not in src, f"qualify.py must not reference {banned!r}"
