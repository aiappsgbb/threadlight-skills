"""
Tests for load_profile_wizard.py — phase 2 of threadlight-consumption-iq.

Covers:
  - Parsing the fixture SPEC (round-trip dict)
  - Non-interactive failure on incomplete SPEC
  - Idempotency on a fully-populated SPEC
  - Write-back round-trip (wizard fills gaps; re-parsed SPEC matches)
  - Canonical field ordering in serialized output
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

FIXTURE_SPEC = (
    Path(__file__).resolve().parent.parent
    / "references/fixtures/sample-pilot-consumption/specs/SPEC.md"
)

from load_profile_wizard import (  # noqa: E402
    ProfileIncompleteError,
    REQUIRED_FIELDS,
    _parse_section_12,
    _serialize_section_12,
    _write_back,
    load_or_prompt_profile,
)


# ---------------------------------------------------------------------------
# test_parses_populated_spec
# ---------------------------------------------------------------------------


def test_parses_populated_spec():
    """Feed the fixture SPEC and verify the round-tripped dict matches the schema."""
    spec_text = FIXTURE_SPEC.read_text(encoding="utf-8")
    profile = _parse_section_12(spec_text)

    assert profile["workload_class"] == "chat-agent"
    assert profile["peak_concurrent_sessions"] == 50
    assert profile["avg_requests_per_session"] == 8
    assert profile["avg_tokens_per_request"] == 1500
    # peak_requests_per_second is a float field; fixture stores "12"
    assert profile["peak_requests_per_second"] == 12
    assert profile["business_hours_only"] is True
    assert profile["cosmos_gb_year_one"] == 50
    assert profile["storage_gb_year_one"] == 100
    assert profile["ai_search_documents"] == 50000
    assert abs(profile["monthly_growth_rate"] - 0.15) < 1e-9

    constraints = profile["declared_constraints"]
    assert isinstance(constraints, dict)
    assert constraints["max_p95_latency_ms"] == 2500
    assert constraints["min_redundancy"] == "zone-redundant"
    assert constraints["pinned_region"] == "eastus2"


# ---------------------------------------------------------------------------
# test_non_interactive_raises_on_missing
# ---------------------------------------------------------------------------


def test_non_interactive_raises_on_missing(tmp_path):
    """Incomplete SPEC with non_interactive=True must raise ProfileIncompleteError
    and list the missing field names in the message."""
    partial_spec = tmp_path / "SPEC.md"
    partial_spec.write_text(
        "## § 12 — Production-readiness declarations\n\n"
        "### `load_profile{}` (consumed by `threadlight-consumption-iq`)\n\n"
        "```yaml\n"
        "load_profile:\n"
        "  workload_class: chat-agent\n"
        "```\n",
        encoding="utf-8",
    )

    with pytest.raises(ProfileIncompleteError) as exc_info:
        load_or_prompt_profile(partial_spec, non_interactive=True)

    msg = str(exc_info.value)
    # workload_class is present; these must all be listed as missing
    assert "peak_concurrent_sessions" in msg
    assert "avg_requests_per_session" in msg
    assert "avg_tokens_per_request" in msg
    assert "business_hours_only" in msg


# ---------------------------------------------------------------------------
# test_idempotent_on_complete_spec
# ---------------------------------------------------------------------------


def test_idempotent_on_complete_spec(tmp_path):
    """Running load_or_prompt_profile on a complete SPEC must not mutate the file."""
    spec = tmp_path / "SPEC.md"
    shutil.copy(FIXTURE_SPEC, spec)
    original_bytes = spec.read_bytes()

    # non_interactive=True is fine here because the spec is complete.
    profile = load_or_prompt_profile(spec, non_interactive=True)

    assert spec.read_bytes() == original_bytes, "SPEC was mutated despite being complete"
    assert profile["workload_class"] == "chat-agent"
    assert profile["peak_concurrent_sessions"] == 50


# ---------------------------------------------------------------------------
# test_write_back_round_trip
# ---------------------------------------------------------------------------


def test_write_back_round_trip(tmp_path, monkeypatch):
    """Start with an incomplete SPEC, feed wizard inputs, verify re-parsed dict."""
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "## § 12 — Production-readiness declarations\n\n"
        "### `load_profile{}` (consumed by `threadlight-consumption-iq`)\n\n"
        "```yaml\n"
        "load_profile:\n"
        "  workload_class: chat-agent\n"
        "```\n",
        encoding="utf-8",
    )

    # workload_class already set; wizard will prompt for everything else.
    # Input order mirrors the REQUIRED_FIELDS iteration (peak_requests_per_second
    # is deferred to after the loop; workload_class skipped as already set):
    #   peak_concurrent_sessions, avg_requests_per_session, avg_tokens_per_request,
    #   business_hours_only, cosmos_gb_year_one, storage_gb_year_one,
    #   ai_search_documents, monthly_growth_rate,
    #   peak_requests_per_second (auto-suggestion, press Enter),
    #   max_p95_latency_ms, min_redundancy, pinned_region (skip)
    inputs = iter([
        "50",             # peak_concurrent_sessions
        "8",              # avg_requests_per_session
        "1500",           # avg_tokens_per_request
        "true",           # business_hours_only
        "50",             # cosmos_gb_year_one
        "100",            # storage_gb_year_one
        "50000",          # ai_search_documents
        "0.15",           # monthly_growth_rate
        "",               # peak_requests_per_second  — accept auto-suggestion
        "2500",           # max_p95_latency_ms
        "zone-redundant", # min_redundancy
        "",               # pinned_region (skip)
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    profile = load_or_prompt_profile(spec, non_interactive=False)

    # Verify the returned profile is complete.
    assert profile["workload_class"] == "chat-agent"
    assert profile["peak_concurrent_sessions"] == 50
    assert profile["business_hours_only"] is True
    assert profile["declared_constraints"]["min_redundancy"] == "zone-redundant"
    assert "pinned_region" not in profile.get("declared_constraints", {})

    # Re-parse the written SPEC and check it is consistent.
    written_text = spec.read_text(encoding="utf-8")
    reparsed = _parse_section_12(written_text)

    assert reparsed["workload_class"] == "chat-agent"
    assert reparsed["peak_concurrent_sessions"] == 50
    assert reparsed["avg_requests_per_session"] == 8
    assert reparsed["avg_tokens_per_request"] == 1500
    assert reparsed["business_hours_only"] is True
    assert reparsed["declared_constraints"]["max_p95_latency_ms"] == 2500
    assert reparsed["declared_constraints"]["min_redundancy"] == "zone-redundant"


# ---------------------------------------------------------------------------
# test_serialize_canonical_order
# ---------------------------------------------------------------------------


def test_serialize_canonical_order():
    """Serialized block must emit fields in REQUIRED_FIELDS order."""
    # Construct profile with keys in deliberately scrambled order.
    profile = {
        "monthly_growth_rate": 0.15,  # last in REQUIRED_FIELDS — put first here
        "workload_class": "batch",
        "ai_search_documents": 50000,
        "peak_concurrent_sessions": 1,
        "avg_requests_per_session": 10000,
        "avg_tokens_per_request": 2000,
        "peak_requests_per_second": 0.12,
        "business_hours_only": False,
        "cosmos_gb_year_one": 50.0,
        "storage_gb_year_one": 100.0,
        "declared_constraints": {
            "max_p95_latency_ms": 2500,
            "min_redundancy": "zone-redundant",
        },
    }

    block = _serialize_section_12(profile)
    lines = block.splitlines()

    # Collect positions of REQUIRED_FIELDS in the output.
    field_positions: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if ":" in stripped:
            key = stripped.split(":")[0].strip()
            if key in REQUIRED_FIELDS and key not in field_positions:
                field_positions[key] = i

    positions = [field_positions[f] for f in REQUIRED_FIELDS if f in field_positions]
    assert positions == sorted(positions), (
        "Fields not emitted in REQUIRED_FIELDS order.\n"
        f"Block:\n{block}"
    )

    # Sanity-check declared_constraints appears after all REQUIRED_FIELDS.
    dc_line = next(
        (i for i, ln in enumerate(lines) if ln.strip().startswith("declared_constraints:")),
        None,
    )
    assert dc_line is not None
    assert dc_line > max(positions), "declared_constraints must come after all scalar fields"
