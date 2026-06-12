"""
Targeted tests for the emitter's markdown rendering.

The manifest layer (_build_manifest, schema_version) is covered in
test_scaffold.py. This file covers the rich markdown rendering added
on top.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from emitter import (  # noqa: E402
    _render_markdown,
    _short_kind,
    _sku_short,
    emit_artefacts,
)


def _projected_aoai():
    return {
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "resource_id": "/subscriptions/x/.../gpt4o",
        "logical_name": "gpt4o",
        "region": "eastus2",
        "current_sku": {"name": "gpt-4o", "tier": "PAYG", "region": "eastus2",
                        "capacity": 100, "extra": {"model_version": "2024-08-06"}},
        "monthly_cost_usd": 1240.50,
        "monthly_units_consumed": {"input_tokens": 22_000_000, "output_tokens": 11_000_000},
        "price_source": "live",
        "alternatives": [
            {
                "sku": {"name": "gpt-4o", "tier": "PTU", "units": 25, "region": "eastus2"},
                "monthly_cost_usd": 980.0,
                "delta_usd": -260.5,
                "delta_pct": -0.21,
                "satisfies_constraints": True,
                "caveats": ["Requires PTU quota in eastus2"],
                "rationale": "Crosses PTU break-even at 25 units.",
            },
            {
                "sku": {"name": "gpt-4o-mini", "tier": "PAYG", "region": "eastus2"},
                "monthly_cost_usd": 80.0,
                "delta_usd": -1160.50,
                "delta_pct": -0.93,
                "satisfies_constraints": False,
                "caveats": ["Model swap may regress quality"],
            },
        ],
    }


def _projected_storage():
    return {
        "resource_kind": "Microsoft.Storage/storageAccounts",
        "logical_name": "storage1",
        "region": "eastus2",
        "current_sku": {"name": "Standard_GRS", "tier": "Standard", "region": "eastus2",
                        "capacity": None, "extra": {"redundancy": "GRS"}, "redundancy": "GRS"},
        "monthly_cost_usd": 42.10,
        "monthly_units_consumed": {"stored_gb_avg": 50.0},
        "price_source": "fixture",
        "alternatives": [
            {
                "sku": {"name": "Standard_LRS", "redundancy": "LRS", "region": "eastus2"},
                "monthly_cost_usd": 18.40,
                "delta_usd": -23.70,
                "delta_pct": -0.56,
                "satisfies_constraints": True,
            },
        ],
    }


def _recommendation_aoai():
    return {
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "logical_name": "gpt4o",
        "current_sku": {"name": "gpt-4o", "tier": "PAYG"},
        "recommended_sku": {"name": "gpt-4o", "tier": "PTU", "units": 25},
        "monthly_savings_usd": 260.50,
        "monthly_savings_pct": 0.21,
        "priority": "high",
        "rationale": "Crosses PTU break-even at 25 units.",
        "caveats": ["Requires PTU quota in eastus2"],
    }


def test_render_markdown_has_all_sections(tmp_path):
    report = tmp_path / "docs" / "cost-projection.md"
    manifest = tmp_path / "specs" / "cost-manifest.json"
    emit_artefacts(
        projected=[_projected_aoai(), _projected_storage()],
        recommendations=[_recommendation_aoai()],
        load_profile={"declared_constraints": {}},
        report_path=report,
        manifest_path=manifest,
        deploy_ref="fixture/abc",
        pre_deploy=False,
    )
    text = report.read_text()
    assert "# Cost projection" in text
    assert "## Totals" in text
    assert "## Cost share by resource kind" in text
    assert "```mermaid" in text and "pie title" in text
    assert "## Recommendations" in text
    assert "## Per-resource breakdown" in text
    assert "### `gpt4o`" in text
    assert "### `storage1`" in text


def test_render_markdown_no_recs_explains_state(tmp_path):
    report = tmp_path / "r.md"
    manifest = tmp_path / "m.json"
    emit_artefacts(
        projected=[_projected_aoai()],
        recommendations=[],
        load_profile={"declared_constraints": {}},
        report_path=report,
        manifest_path=manifest,
        deploy_ref="fixture/0",
        pre_deploy=False,
    )
    text = report.read_text()
    assert "_None._" in text


def test_render_markdown_pre_deploy_banner(tmp_path):
    report = tmp_path / "r.md"
    manifest = tmp_path / "m.json"
    emit_artefacts(
        projected=[], recommendations=[], load_profile={"declared_constraints": {}},
        report_path=report, manifest_path=manifest,
        deploy_ref="pre-deploy", pre_deploy=True,
    )
    assert "pre-deploy preview" in report.read_text()


def test_render_markdown_recommendations_sorted_by_savings():
    big = dict(_recommendation_aoai())
    big["monthly_savings_usd"] = 500.0
    small = dict(_recommendation_aoai())
    small["logical_name"] = "storage1"
    small["monthly_savings_usd"] = 10.0
    md = _render_markdown({
        "generated_at": "x", "deploy_ref": "x", "load_profile_ref": "x",
        "currency": "USD", "price_basis": "retail", "pre_deploy": False,
        "resources": [],
        "recommendations": sorted(
            [big, small], key=lambda r: r["monthly_savings_usd"], reverse=True,
        ),
        "totals": {"monthly_cost_current_usd": 0, "monthly_cost_recommended_usd": 0,
                   "monthly_savings_potential_usd": 0},
    })
    assert md.index("$500.00") < md.index("$10.00")


def test_short_kind_strips_microsoft_prefix():
    assert _short_kind("Microsoft.CognitiveServices/accounts/deployments") == "CognitiveServices/deployments"
    assert _short_kind("Microsoft.Storage/storageAccounts") == "Storage/storageAccounts"
    assert _short_kind("") == "?"


def test_sku_short_compact():
    assert _sku_short({"name": "gpt-4o", "tier": "PAYG", "capacity": 100}) == "gpt-4o tier=PAYG capacity=100"
    assert _sku_short({"name": "Consumption", "tier": "Consumption"}) == "Consumption"
    assert _sku_short(None) == "?"


def test_render_markdown_escapes_pipes_in_caveats(tmp_path):
    rec = dict(_recommendation_aoai())
    rec["rationale"] = "uses | pipes | a lot"
    report = tmp_path / "r.md"
    manifest = tmp_path / "m.json"
    emit_artefacts(
        projected=[_projected_aoai()],
        recommendations=[rec],
        load_profile={"declared_constraints": {}},
        report_path=report, manifest_path=manifest,
        deploy_ref="x", pre_deploy=False,
    )
    text = report.read_text()
    assert "uses \\| pipes \\| a lot" in text


def test_manifest_totals_reconcile_with_recommendations(tmp_path):
    report = tmp_path / "r.md"
    manifest = tmp_path / "m.json"
    emit_artefacts(
        projected=[_projected_aoai(), _projected_storage()],
        recommendations=[_recommendation_aoai()],
        load_profile={"declared_constraints": {}},
        report_path=report, manifest_path=manifest,
        deploy_ref="x", pre_deploy=False,
    )
    parsed = json.loads(manifest.read_text())
    expected_current = 1240.50 + 42.10
    assert parsed["totals"]["monthly_cost_current_usd"] == round(expected_current, 2)
    assert parsed["totals"]["monthly_savings_potential_usd"] == 260.50
    assert parsed["totals"]["monthly_cost_recommended_usd"] == round(expected_current - 260.50, 2)
