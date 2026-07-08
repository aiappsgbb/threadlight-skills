"""
End-to-end golden-file test for the pre-sales phased estimate.

Mirrors `test_e2e.py`: it drives the in-process orchestrator
(`estimate.emit_presales`) against the `sample-presales-rollout` fixture with a
deterministic mock pricing client and a pinned timestamp, then compares the
phased manifest, the markdown report, and the seller one-pager against golden
files.

`discover` is intentionally bypassed (it needs `az bicep build`); the resource
topology is the synthetic list below — one entry per projector that a pre-sales
estimate exercises, including the Log Analytics workspace so the observability
projector is covered end-to-end.

To refresh the golden files after an intentional change:

    CONSUMPTION_IQ_REGENERATE_GOLDEN=1 \
        python3 -m pytest skills/threadlight-consumption-iq/tests/test_e2e_presales.py -v

Then `git diff` the fixture and commit if the new output is correct.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURE_DIR = SKILL_ROOT / "references" / "fixtures" / "sample-presales-rollout"
EXPECTED_DIR = FIXTURE_DIR / "expected"

sys.path.insert(0, str(SCRIPTS_DIR))

from estimate import emit_presales  # noqa: E402
from rollout import load_rollout_profile  # noqa: E402


PINNED_TIMESTAMP = "2026-06-22T12:00:00+00:00"
PINNED_DEPLOY_REF = "sample-presales-rollout/estimate-test"
REGENERATE = os.environ.get("CONSUMPTION_IQ_REGENERATE_GOLDEN") == "1"


# Synthetic resource topology — one per projector a pre-sales estimate touches.
SYNTHETIC_RESOURCES = [
    {
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "resource_id": "/synthetic/aoai/gpt-4o",
        "logical_name": "aoai-chat",
        "region": "eastus2",
        "current_sku": {"name": "gpt-4o", "tier": "PAYG", "region": "eastus2", "capacity": None},
    },
    {
        "resource_kind": "Microsoft.App/containerApps",
        "resource_id": "/synthetic/bot",
        "logical_name": "bot",
        "region": "eastus2",
        "current_sku": {
            "name": "Consumption", "tier": "Consumption", "region": "eastus2", "capacity": None,
            "extra": {"vcpu": 0.5, "memory_gib": 1.0, "min_replicas": 1, "max_replicas": 10},
        },
    },
    {
        "resource_kind": "Microsoft.DocumentDB/databaseAccounts",
        "resource_id": "/synthetic/cosmos",
        "logical_name": "cosmos",
        "region": "eastus2",
        "current_sku": {
            "name": "Standard", "tier": "provisioned", "region": "eastus2", "capacity": 4000,
            "extra": {"multi_write": False},
        },
    },
    {
        "resource_kind": "Microsoft.Search/searchServices",
        "resource_id": "/synthetic/search",
        "logical_name": "ai-search",
        "region": "eastus2",
        "current_sku": {
            "name": "basic", "tier": "basic", "region": "eastus2", "capacity": 1,
            "extra": {"replicas": 1, "partitions": 1},
        },
    },
    {
        "resource_kind": "Microsoft.OperationalInsights/workspaces",
        "resource_id": "/synthetic/logs",
        "logical_name": "logs",
        "region": "eastus2",
        "current_sku": {
            "name": "PerGB2018", "tier": "PerGB2018", "region": "eastus2", "capacity": None,
            "extra": {"content_recording": True},
        },
    },
]


class DeterministicPricingClient:
    """Always returns (None, fallback) so projectors use hardcoded matrices."""

    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback",
                "fetched_at": None, "azure_meter_id": None, "raw": {}}

    def warm(self, resource):
        return None


def _run(tmp_path: Path) -> dict[str, Path]:
    rollout = load_rollout_profile(FIXTURE_DIR / "rollout.json")
    report = tmp_path / "cost-estimate.md"
    manifest = tmp_path / "cost-estimate-manifest.json"
    onepager = tmp_path / "estimate-onepager.html"
    emit_presales(
        rollout,
        SYNTHETIC_RESOURCES,
        DeterministicPricingClient(),
        report_path=report,
        manifest_path=manifest,
        onepager_path=onepager,
        audience="internal",
        pdf=False,
        deploy_ref=PINNED_DEPLOY_REF,
        generated_at=PINNED_TIMESTAMP,
    )
    return {"manifest": manifest, "report": report, "onepager": onepager}


def _check(actual_text: str, golden_name: str):
    expected_path = EXPECTED_DIR / golden_name
    if REGENERATE:
        EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(actual_text)
        pytest.skip(f"regenerated {expected_path} (REGENERATE=1)")
    assert actual_text == expected_path.read_text(), (
        f"{golden_name} drifted from golden. If intentional, regenerate via: "
        "CONSUMPTION_IQ_REGENERATE_GOLDEN=1 python3 -m pytest "
        f"{Path(__file__).relative_to(SKILL_ROOT.parent.parent)}"
    )


def test_presales_manifest_matches_golden(tmp_path):
    paths = _run(tmp_path)
    _check(paths["manifest"].read_text(), "cost-estimate-manifest.json")


def test_presales_markdown_matches_golden(tmp_path):
    paths = _run(tmp_path)
    _check(paths["report"].read_text(), "cost-estimate.md")


def test_presales_onepager_matches_golden(tmp_path):
    paths = _run(tmp_path)
    _check(paths["onepager"].read_text(), "estimate-onepager.html")


def test_presales_manifest_schema_is_1_1(tmp_path):
    paths = _run(tmp_path)
    data = json.loads(paths["manifest"].read_text())
    assert data["schema_version"] == "1.1"
    assert data["pre_sales"] is True
    # Top-level totals must mirror the current phase (production-ready COST gates).
    current = next(p for p in data["phases"] if p["id"] == data["current_phase"])
    assert data["totals"]["monthly_cost_current_usd"] == current["totals"]["monthly_cost_current_usd"]
