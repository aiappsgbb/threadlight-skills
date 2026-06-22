"""
Tests for scripts/estimate.py — the pre-sales orchestrator.

`estimate` is the front-end the post-deploy chain lacks: it takes a rollout
profile (N phases, each its own load + posture) and a resource topology, then —
per phase — projects every resource at that phase's load, appends the
hardening/estate delta for that phase's posture, scores recommendations on the
**current** phase only, and folds it all into the phased manifest + artefacts.

These tests drive the importable in-process API (mirroring `test_e2e.py`'s
pattern) so the golden e2e can call it directly without a CLI shell-out.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import estimate  # noqa: E402


class _FallbackPricing:
    """Returns (None, fallback) so projectors use their hardcoded matrices."""

    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback",
                "fetched_at": None, "azure_meter_id": None, "raw": {}}

    def warm(self, resource):
        return None


def _resources() -> list[dict]:
    return [
        {
            "resource_kind": "Microsoft.App/containerApps",
            "resource_id": "/synthetic/bot",
            "logical_name": "bot",
            "region": "eastus2",
            "current_sku": {"name": "Consumption", "tier": "Consumption", "region": "eastus2",
                            "extra": {"vcpu": 0.5, "memory_gib": 1.0, "min_replicas": 1, "max_replicas": 10}},
        },
        {
            "resource_kind": "Microsoft.OperationalInsights/workspaces",
            "resource_id": "/synthetic/logs",
            "logical_name": "logs",
            "region": "eastus2",
            "current_sku": {"name": "PerGB2018", "tier": "PerGB2018", "region": "eastus2",
                            "extra": {"content_recording": True}},
        },
    ]


def _load_profile() -> dict:
    return {
        "peak_requests_per_second": 2.0,
        "avg_tokens_per_request": 1500,
        "requests_per_day": 50000,
        "data_volume_gb": 25,
        "business_hours_only": False,
    }


def _rollout() -> dict:
    lp = _load_profile()
    small = {**lp, "peak_requests_per_second": 0.5, "requests_per_day": 5000}
    big = {**lp, "peak_requests_per_second": 8.0, "requests_per_day": 400000}
    return {
        "customer": "Generic Pilot",
        "currency": "USD",
        "current_phase": "expansion",
        "discount": {"basis": "ea", "multiplier": 0.85},
        "phases": [
            {"id": "poc", "label": "Phase 1 - Proof of concept", "posture": "demo",
             "audience": "internal", "load_profile": small},
            {"id": "expansion", "label": "Phase 2 - Expansion", "posture": "production",
             "audience": "internal", "load_profile": lp},
            {"id": "business-wide", "label": "Phase 3 - Business-wide",
             "posture": "production-hardened", "audience": "customer", "load_profile": big},
        ],
    }


# ---------------------------------------------------------------------------
# run_presales
# ---------------------------------------------------------------------------

def test_run_presales_builds_schema_1_1_manifest():
    m = estimate.run_presales(_rollout(), _resources(), _FallbackPricing(), generated_at="PINNED")
    assert m["schema_version"] == "1.1"
    assert m["pre_sales"] is True
    assert m["generated_at"] == "PINNED"
    assert [p["id"] for p in m["phases"]] == ["poc", "expansion", "business-wide"]


def test_each_phase_projects_all_resources():
    m = estimate.run_presales(_rollout(), _resources(), _FallbackPricing(), generated_at="PINNED")
    for phase in m["phases"]:
        kinds = {r["resource_kind"] for r in phase["resources"]}
        assert "Microsoft.App/containerApps" in kinds
        assert "Microsoft.OperationalInsights/workspaces" in kinds
        for r in phase["resources"]:
            assert "monthly_cost_usd" in r


def test_hardening_delta_scales_with_posture():
    m = estimate.run_presales(_rollout(), _resources(), _FallbackPricing(), generated_at="PINNED")
    by_id = {p["id"]: p for p in m["phases"]}
    # demo posture has no hardening; hardened has the most lines.
    assert by_id["poc"]["hardening_delta"] == []
    assert len(by_id["business-wide"]["hardening_delta"]) >= len(by_id["expansion"]["hardening_delta"])


def test_recommendations_scored_on_current_phase_only():
    m = estimate.run_presales(_rollout(), _resources(), _FallbackPricing(), generated_at="PINNED")
    by_id = {p["id"]: p for p in m["phases"]}
    assert by_id["poc"]["recommendations"] == []
    assert by_id["business-wide"]["recommendations"] == []
    # current phase (expansion) is allowed to have recs (list, may be empty)
    assert isinstance(by_id["expansion"]["recommendations"], list)


def test_observability_cost_grows_with_load():
    m = estimate.run_presales(_rollout(), _resources(), _FallbackPricing(), generated_at="PINNED")
    by_id = {p["id"]: p for p in m["phases"]}

    def obs_cost(phase):
        return next(r["monthly_cost_usd"] for r in phase["resources"]
                    if r["resource_kind"] == "Microsoft.OperationalInsights/workspaces")

    assert obs_cost(by_id["business-wide"]) > obs_cost(by_id["poc"])


def test_discount_threaded_through():
    m = estimate.run_presales(_rollout(), _resources(), _FallbackPricing(), generated_at="PINNED")
    assert m["price_basis"] == "ea"
    assert m["discount"]["applied"] is True
    for phase in m["phases"]:
        assert "monthly_cost_current_discounted_usd" in phase["totals"]


# ---------------------------------------------------------------------------
# emit_presales
# ---------------------------------------------------------------------------

def test_emit_presales_writes_all_artefacts(tmp_path):
    report = tmp_path / "cost-estimate.md"
    manifest = tmp_path / "cost-estimate-manifest.json"
    onepager = tmp_path / "estimate-onepager.html"
    result = estimate.emit_presales(
        _rollout(), _resources(), _FallbackPricing(),
        report_path=report, manifest_path=manifest, onepager_path=onepager,
        generated_at="PINNED",
    )
    assert report.exists() and manifest.exists() and onepager.exists()
    assert result["manifest"]["schema_version"] == "1.1"
    assert result["onepager"]["html_path"] == str(onepager)
    data = json.loads(manifest.read_text())
    assert data["pre_sales"] is True


def test_emit_presales_onepager_audience_defaults_to_current_phase(tmp_path):
    onepager = tmp_path / "op.html"
    estimate.emit_presales(
        _rollout(), _resources(), _FallbackPricing(),
        report_path=tmp_path / "r.md", manifest_path=tmp_path / "m.json",
        onepager_path=onepager, generated_at="PINNED",
    )
    # current phase (expansion) audience is internal -> classification strip present.
    html = onepager.read_text().lower()
    assert "do not share" in html
