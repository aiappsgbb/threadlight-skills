"""
Tests for the pre-sales convergence in `emitter.py`.

The post-deploy emitter builds a single-load `cost-manifest.json` (schema 1.0).
The pre-sales path builds a **phased** manifest (schema 1.1) that:

  * carries `pre_sales: true`, `phases[]`, top-level `price_basis` + `discount{}`;
  * recomputes each phase's totals from its projected resources + hardening delta
    (never trusts upstream numbers — same discipline as the v1 emitter);
  * mirrors the **current phase**'s totals into top-level `totals.*` so the
    downstream `threadlight-production-ready` COST-005/006 gates still read a
    meaningful number;
  * stays additive/backward-compatible (no v1 field removed).

The exact golden bytes are pinned by `test_e2e_presales.py`; this suite pins the
*structure* and the discipline invariants.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from emitter import (  # noqa: E402
    build_presales_manifest,
    emit_presales_artefacts,
    render_presales_markdown,
)


# ---------------------------------------------------------------------------
# Synthetic, already-projected phase inputs (no pricing/projector needed here).
# ---------------------------------------------------------------------------

def _resource(name: str, kind: str, cost: float) -> dict:
    return {
        "resource_kind": kind,
        "resource_id": f"/synthetic/{name}",
        "logical_name": name,
        "region": "eastus2",
        "current_sku": {"name": "Standard", "tier": "Standard"},
        "monthly_cost_usd": cost,
        "monthly_units_consumed": {},
        "price_source": "fallback",
        "alternatives": [],
    }


def _hardening_line(component: str, cost: float, shared: bool = False) -> dict:
    return {
        "component": component,
        "category": "network",
        "monthly_cost_usd": cost,
        "shared_platform_billed": shared,
        "price_source": "fallback",
        "rationale": "test",
        "posture": "production",
    }


def _phases() -> list[dict]:
    return [
        {
            "id": "poc",
            "label": "Phase 1 - Proof of concept",
            "posture": "demo",
            "audience": "internal",
            "resources": [_resource("aoai", "Microsoft.CognitiveServices/accounts/deployments", 300.0),
                          _resource("aca", "Microsoft.App/containerApps", 120.0)],
            "hardening_delta": [],
            "recommendations": [],
        },
        {
            "id": "expansion",
            "label": "Phase 2 - Expansion",
            "posture": "production",
            "audience": "internal",
            "resources": [_resource("aoai", "Microsoft.CognitiveServices/accounts/deployments", 1500.0),
                          _resource("aca", "Microsoft.App/containerApps", 200.0)],
            "hardening_delta": [_hardening_line("Private Endpoints", 150.0)],
            "recommendations": [
                {"logical_name": "aoai", "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
                 "current_sku": {"name": "PAYG"}, "recommended_sku": {"name": "PTU"},
                 "monthly_savings_usd": 100.0, "monthly_savings_pct": 0.06, "priority": "med",
                 "rationale": "reserve"}
            ],
        },
        {
            "id": "business-wide",
            "label": "Phase 3 - Business-wide",
            "posture": "production-hardened",
            "audience": "internal",
            "resources": [_resource("aoai", "Microsoft.CognitiveServices/accounts/deployments", 7000.0),
                          _resource("aca", "Microsoft.App/containerApps", 800.0)],
            "hardening_delta": [_hardening_line("Sentinel", 600.0, shared=True),
                                _hardening_line("Front Door", 300.0)],
            "recommendations": [],
        },
    ]


def _rollout(discount: dict | None = None) -> dict:
    profile = {
        "customer": "Generic Pilot",
        "currency": "USD",
        "current_phase": "expansion",
        "benchmark": {"metric": "queries_per_day", "value": 5000},
        "phases": [],  # phases passed separately to the builder
    }
    if discount is not None:
        profile["discount"] = discount
    return profile


PINNED = "2026-06-22T12:00:00+00:00"


# ---------------------------------------------------------------------------
# build_presales_manifest
# ---------------------------------------------------------------------------

def test_manifest_is_schema_1_1_and_pre_sales():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    assert m["schema_version"] == "1.1"
    assert m["pre_sales"] is True
    assert m["generated_at"] == PINNED
    assert m["customer"] == "Generic Pilot"
    assert m["current_phase"] == "expansion"


def test_phase_totals_include_resources_plus_hardening():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    by_id = {p["id"]: p for p in m["phases"]}
    # poc: 300 + 120, no hardening
    assert by_id["poc"]["totals"]["monthly_cost_current_usd"] == pytest.approx(420.0)
    # expansion: 1500 + 200 + 150 hardening
    assert by_id["expansion"]["totals"]["monthly_cost_current_usd"] == pytest.approx(1850.0)
    # business-wide: 7000 + 800 + 600 + 300
    assert by_id["business-wide"]["totals"]["monthly_cost_current_usd"] == pytest.approx(8700.0)


def test_top_level_totals_mirror_current_phase():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    expansion = next(p for p in m["phases"] if p["id"] == "expansion")
    assert m["totals"]["monthly_cost_current_usd"] == expansion["totals"]["monthly_cost_current_usd"]


def test_recommendations_only_applied_on_current_phase():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    by_id = {p["id"]: p for p in m["phases"]}
    # current phase recommended = current - savings (1850 - 100)
    assert by_id["expansion"]["totals"]["monthly_cost_recommended_usd"] == pytest.approx(1750.0)
    # non-current phases: recommended == current (no recs scored)
    assert by_id["poc"]["totals"]["monthly_cost_recommended_usd"] == pytest.approx(420.0)


def test_discount_applied_to_every_phase_and_top_level():
    m = build_presales_manifest(
        _phases(), _rollout({"basis": "ea", "multiplier": 0.85}),
        deploy_ref="pre-sales", generated_at=PINNED,
    )
    assert m["price_basis"] == "ea"
    assert m["discount"]["applied"] is True
    by_id = {p["id"]: p for p in m["phases"]}
    assert by_id["poc"]["totals"]["monthly_cost_current_discounted_usd"] == pytest.approx(357.0)
    assert by_id["expansion"]["totals"]["monthly_cost_current_discounted_usd"] == pytest.approx(1572.5)
    assert m["totals"]["monthly_cost_current_discounted_usd"] == pytest.approx(1572.5)


def test_no_discount_block_means_retail_only():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    assert m["price_basis"] == "retail"
    assert m["discount"]["applied"] is False
    for p in m["phases"]:
        assert "monthly_cost_current_discounted_usd" not in p["totals"]


def test_hardening_delta_preserved_on_phases():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    bw = next(p for p in m["phases"] if p["id"] == "business-wide")
    components = {ln["component"] for ln in bw["hardening_delta"]}
    assert {"Sentinel", "Front Door"}.issubset(components)


# ---------------------------------------------------------------------------
# render_presales_markdown
# ---------------------------------------------------------------------------

def test_markdown_frames_estimates_and_lists_every_phase():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    md = render_presales_markdown(m).lower()
    assert "estimate" in md
    for label in ("proof of concept", "expansion", "business-wide"):
        assert label in md


def test_markdown_has_hardening_section():
    m = build_presales_manifest(_phases(), _rollout(), deploy_ref="pre-sales", generated_at=PINNED)
    md = render_presales_markdown(m).lower()
    assert "hardening" in md or "estate" in md


# ---------------------------------------------------------------------------
# emit_presales_artefacts
# ---------------------------------------------------------------------------

def test_emit_writes_both_artefacts(tmp_path):
    report = tmp_path / "cost-estimate.md"
    manifest = tmp_path / "cost-estimate-manifest.json"
    emit_presales_artefacts(
        phases=_phases(),
        rollout_profile=_rollout({"basis": "ea", "multiplier": 0.85}),
        report_path=report,
        manifest_path=manifest,
        deploy_ref="pre-sales",
        generated_at=PINNED,
    )
    assert report.exists() and manifest.exists()
    data = json.loads(manifest.read_text())
    assert data["schema_version"] == "1.1"
    assert data["pre_sales"] is True
