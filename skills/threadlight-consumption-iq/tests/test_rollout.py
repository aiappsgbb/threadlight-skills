"""
Tests for scripts/rollout.py — the pre-sales phased rollout profile parser.

A rollout profile models N adoption phases (e.g. POC -> expansion ->
business-wide), each a *different* topology + load + hardening posture.
It is the pre-sales front-end the post-deploy `load_profile{}` lacks.

Parsing is fail-fast: an incomplete phase must raise RolloutProfileError
(never silently project on guessed numbers — mirrors the load-profile
wizard's exit-4 discipline).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rollout import (  # noqa: E402
    POSTURES,
    RolloutProfileError,
    load_rollout_profile,
    parse_rollout_yaml,
    validate_rollout_profile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _complete_load_profile(**overrides):
    base = {
        "workload_class": "chat-agent",
        "peak_concurrent_sessions": 50,
        "avg_requests_per_session": 8,
        "avg_tokens_per_request": 1500,
        "peak_requests_per_second": 12,
        "business_hours_only": True,
        "cosmos_gb_year_one": 50,
        "storage_gb_year_one": 100,
        "ai_search_documents": 50000,
        "monthly_growth_rate": 0.15,
        "declared_constraints": {
            "max_p95_latency_ms": 2500,
            "min_redundancy": "zone-redundant",
        },
    }
    base.update(overrides)
    return base


def _valid_profile():
    return {
        "customer": "Generic Pilot",
        "currency": "USD",
        "discount": {"basis": "ea", "multiplier": 0.85},
        "benchmark": {"metric": "queries_per_day", "value": 5000},
        "current_phase": "poc",
        "phases": [
            {
                "id": "poc",
                "label": "Phase 1 - Proof of concept",
                "audience": "internal",
                "posture": "demo",
                "load_profile": _complete_load_profile(peak_requests_per_second=2),
            },
            {
                "id": "expansion",
                "label": "Phase 2 - Expansion",
                "posture": "production",
                "load_profile": _complete_load_profile(peak_requests_per_second=12),
            },
            {
                "id": "business-wide",
                "label": "Phase 3 - Business-wide",
                "posture": "production-hardened",
                "load_profile": _complete_load_profile(
                    peak_requests_per_second=40, business_hours_only=False
                ),
            },
        ],
    }


# ---------------------------------------------------------------------------
# Tests — validation
# ---------------------------------------------------------------------------

def test_postures_are_the_three_canonical_values():
    assert POSTURES == {"demo", "production", "production-hardened"}


def test_validate_accepts_complete_profile():
    out = validate_rollout_profile(_valid_profile())
    assert [p["id"] for p in out["phases"]] == ["poc", "expansion", "business-wide"]
    assert out["current_phase"] == "poc"
    assert out["discount"] == {"basis": "ea", "multiplier": 0.85}
    assert out["benchmark"]["value"] == 5000


def test_validate_defaults_current_phase_to_first():
    prof = _valid_profile()
    del prof["current_phase"]
    out = validate_rollout_profile(prof)
    assert out["current_phase"] == "poc"


def test_validate_defaults_audience_to_internal():
    prof = _valid_profile()
    assert "audience" not in prof["phases"][1]  # no audience declared
    out = validate_rollout_profile(prof)
    assert out["phases"][1]["audience"] == "internal"


def test_validate_rejects_empty_phases():
    prof = _valid_profile()
    prof["phases"] = []
    with pytest.raises(RolloutProfileError, match="phases"):
        validate_rollout_profile(prof)


def test_validate_rejects_unknown_posture():
    prof = _valid_profile()
    prof["phases"][0]["posture"] = "ultra-hardened"
    with pytest.raises(RolloutProfileError, match="posture"):
        validate_rollout_profile(prof)


def test_validate_rejects_incomplete_phase_load_profile():
    prof = _valid_profile()
    del prof["phases"][0]["load_profile"]["avg_tokens_per_request"]
    with pytest.raises(RolloutProfileError) as exc:
        validate_rollout_profile(prof)
    # Error must point at the exact phase + field so the seller can fix it.
    assert "poc" in str(exc.value)
    assert "avg_tokens_per_request" in str(exc.value)


def test_validate_rejects_current_phase_not_in_phases():
    prof = _valid_profile()
    prof["current_phase"] = "nonexistent"
    with pytest.raises(RolloutProfileError, match="current_phase"):
        validate_rollout_profile(prof)


def test_validate_rejects_duplicate_phase_ids():
    prof = _valid_profile()
    prof["phases"][1]["id"] = "poc"
    with pytest.raises(RolloutProfileError, match="duplicate"):
        validate_rollout_profile(prof)


# ---------------------------------------------------------------------------
# Tests — loading (JSON + YAML)
# ---------------------------------------------------------------------------

def test_load_json_rollout(tmp_path):
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps(_valid_profile()))
    out = load_rollout_profile(path)
    assert len(out["phases"]) == 3
    assert out["phases"][2]["posture"] == "production-hardened"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_rollout_profile(tmp_path / "nope.json")


def test_yaml_parser_handles_nested_phases():
    yaml_text = """
customer: Generic Pilot
currency: USD
discount:
  basis: ea
  multiplier: 0.85
current_phase: poc
phases:
  - id: poc
    label: Phase 1 - Proof of concept
    posture: demo
    load_profile:
      workload_class: chat-agent
      peak_requests_per_second: 2
      business_hours_only: true
  - id: expansion
    label: Phase 2 - Expansion
    posture: production
    load_profile:
      workload_class: chat-agent
      peak_requests_per_second: 12
      business_hours_only: false
"""
    parsed = parse_rollout_yaml(yaml_text)
    assert parsed["customer"] == "Generic Pilot"
    assert parsed["discount"]["multiplier"] == 0.85
    assert len(parsed["phases"]) == 2
    assert parsed["phases"][0]["id"] == "poc"
    assert parsed["phases"][0]["load_profile"]["peak_requests_per_second"] == 2
    assert parsed["phases"][0]["load_profile"]["business_hours_only"] is True
    assert parsed["phases"][1]["load_profile"]["business_hours_only"] is False


def test_load_yaml_rollout(tmp_path):
    path = tmp_path / "rollout.yaml"
    path.write_text(
        "customer: Generic Pilot\n"
        "phases:\n"
        "  - id: poc\n"
        "    label: P1\n"
        "    posture: demo\n"
        "    load_profile:\n"
        + "".join(
            f"      {k}: {json.dumps(v) if not isinstance(v, str) else v}\n"
            for k, v in _complete_load_profile(peak_requests_per_second=2).items()
            if k != "declared_constraints"
        )
        + "      declared_constraints:\n"
        "        max_p95_latency_ms: 2500\n"
        "        min_redundancy: zone-redundant\n"
    )
    out = load_rollout_profile(path)
    assert out["phases"][0]["id"] == "poc"
    assert out["phases"][0]["posture"] == "demo"


# ---------------------------------------------------------------------------
# Declared topology (pre-sales needs no deployed repo)
# ---------------------------------------------------------------------------

def _res(kind="Microsoft.App/containerApps", name="bot"):
    return {
        "resource_kind": kind,
        "logical_name": name,
        "region": "eastus2",
        "current_sku": {"name": "Consumption", "tier": "Consumption",
                        "region": "eastus2", "extra": {"vcpu": 0.5, "memory_gib": 1.0}},
    }


def test_top_level_resources_topology_is_preserved():
    prof = _valid_profile()
    prof["resources"] = [_res(), _res("Microsoft.Search/searchServices", "search")]
    norm = validate_rollout_profile(prof)
    assert "resources" in norm
    assert [r["resource_kind"] for r in norm["resources"]] == [
        "Microsoft.App/containerApps",
        "Microsoft.Search/searchServices",
    ]


def test_per_phase_resources_override_is_preserved():
    prof = _valid_profile()
    # POC runs AI Search Basic; business-wide swaps to S2 — a real topology step.
    prof["phases"][0]["resources"] = [
        {"resource_kind": "Microsoft.Search/searchServices", "logical_name": "search",
         "region": "eastus2", "current_sku": {"name": "basic", "tier": "Basic", "region": "eastus2"}}
    ]
    prof["phases"][2]["resources"] = [
        {"resource_kind": "Microsoft.Search/searchServices", "logical_name": "search",
         "region": "eastus2", "current_sku": {"name": "standard2", "tier": "S2", "region": "eastus2"}}
    ]
    norm = validate_rollout_profile(prof)
    assert norm["phases"][0]["resources"][0]["current_sku"]["tier"] == "Basic"
    assert norm["phases"][2]["resources"][0]["current_sku"]["tier"] == "S2"
    # A phase without its own topology keeps no `resources` key (falls back later).
    assert "resources" not in norm["phases"][1]


def test_invalid_resource_entry_is_rejected():
    prof = _valid_profile()
    prof["resources"] = [{"logical_name": "oops"}]  # missing resource_kind + current_sku
    with pytest.raises(RolloutProfileError):
        validate_rollout_profile(prof)


def test_resources_must_be_a_list():
    prof = _valid_profile()
    prof["resources"] = {"resource_kind": "x"}  # a dict, not a list
    with pytest.raises(RolloutProfileError):
        validate_rollout_profile(prof)
