"""
End-to-end golden-file test for `threadlight-consumption-iq`.

Exercises the in-process pipeline (load-profile → project → recommend
→ emit) against the `sample-pilot-consumption` fixture, using a
deterministic mock pricing client so the output is reproducible.

The `discover` phase is excluded because it requires `az bicep build`
against a real Bicep file — discover is independently covered by
`tests/test_discover.py`.

To refresh the golden files after an intentional change:

    CONSUMPTION_IQ_REGENERATE_GOLDEN=1 \
        python3 -m pytest skills/threadlight-consumption-iq/tests/test_e2e.py -v

Then `git diff` the fixture and commit if the new output is correct.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURE_DIR = SKILL_ROOT / "references" / "fixtures" / "sample-pilot-consumption"
EXPECTED_DIR = FIXTURE_DIR / "expected"

sys.path.insert(0, str(SCRIPTS_DIR))

from emitter import emit_artefacts  # noqa: E402
from load_profile_wizard import load_or_prompt_profile  # noqa: E402
from projectors import project_resource  # noqa: E402
from recommender import score_and_rank  # noqa: E402


PINNED_TIMESTAMP = "2026-06-12T12:00:00+00:00"
PINNED_DEPLOY_REF = "sample-pilot-consumption/deployment-test"
REGENERATE = os.environ.get("CONSUMPTION_IQ_REGENERATE_GOLDEN") == "1"


# Synthetic discover() output for the fixture. One resource per v1
# projector — mirrors `specs/manifest.json:expected_resource_types[]`
# minus the standalone CognitiveServices/accounts wrapper (no projector)
# and Microsoft.App/jobs (covered by ACA projector via containerApps).
SYNTHETIC_RESOURCES = [
    {
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.CognitiveServices/accounts/aoai-pilot/deployments/gpt-4o",
        "logical_name": "aoai-chat",
        "region": "eastus2",
        "current_sku": {
            "name": "gpt-4o",
            "tier": "PAYG",
            "region": "eastus2",
            "capacity": None,
        },
    },
    {
        "resource_kind": "Microsoft.MachineLearningServices/workspaces",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.MachineLearningServices/workspaces/foundry-pilot",
        "logical_name": "foundry-agent",
        "region": "eastus2",
        "current_sku": {
            "name": "Standard",
            "tier": "Standard",
            "region": "eastus2",
            "capacity": 1,
        },
    },
    {
        "resource_kind": "Microsoft.App/containerApps",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.App/containerApps/bot-pilot",
        "logical_name": "bot",
        "region": "eastus2",
        "current_sku": {
            "name": "Consumption",
            "tier": "Consumption",
            "region": "eastus2",
            "capacity": None,
            "extra": {
                "vcpu": 0.5,
                "memory_gib": 1.0,
                "min_replicas": 1,
                "max_replicas": 10,
            },
        },
    },
    {
        "resource_kind": "Microsoft.DocumentDB/databaseAccounts",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.DocumentDB/databaseAccounts/cosmos-pilot",
        "logical_name": "cosmos",
        "region": "eastus2",
        "current_sku": {
            "name": "Standard",
            "tier": "provisioned",
            "region": "eastus2",
            "capacity": 4000,
            "extra": {"multi_write": False},
        },
    },
    {
        "resource_kind": "Microsoft.Storage/storageAccounts",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.Storage/storageAccounts/stpilot",
        "logical_name": "storage",
        "region": "eastus2",
        "current_sku": {
            "name": "Standard_LRS",
            "tier": "Standard",
            "region": "eastus2",
            "capacity": None,
            "redundancy": "LRS",
            "extra": {"access_tier": "hot"},
        },
    },
    {
        "resource_kind": "Microsoft.ApiManagement/service",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.ApiManagement/service/apim-pilot",
        "logical_name": "apim",
        "region": "eastus2",
        "current_sku": {
            "name": "BasicV2",
            "tier": "BasicV2",
            "region": "eastus2",
            "capacity": 1,
        },
    },
    {
        "resource_kind": "Microsoft.Search/searchServices",
        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-pilot/providers/Microsoft.Search/searchServices/search-pilot",
        "logical_name": "ai-search",
        "region": "eastus2",
        "current_sku": {
            "name": "basic",
            "tier": "basic",
            "region": "eastus2",
            "capacity": 1,
            "extra": {"replicas": 1, "partitions": 1},
        },
    },
]


class DeterministicPricingClient:
    """Always returns (None, fallback) so projectors use their hardcoded matrices.

    This gives us reproducible numbers across environments without
    requiring network access or fixture freshness.
    """

    def get_price(self, resource_kind, sku):
        return {
            "unit_price_usd": None,
            "unit": None,
            "price_source": "fallback",
            "fetched_at": None,
            "azure_meter_id": None,
            "raw": {},
        }

    def warm(self, resource):
        return None


class _PinnedDatetime(datetime):
    """datetime subclass with .now() pinned to PINNED_TIMESTAMP."""

    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat(PINNED_TIMESTAMP)


def _run_pipeline(tmp_path: Path) -> tuple[Path, Path]:
    profile = load_or_prompt_profile(
        spec_path=FIXTURE_DIR / "specs" / "SPEC.md",
        non_interactive=True,
    )
    pricing = DeterministicPricingClient()
    projected = [
        project_resource(r, profile, pricing) for r in SYNTHETIC_RESOURCES
    ]
    recs = score_and_rank(projected, profile)

    manifest_path = tmp_path / "cost-manifest.json"
    report_path = tmp_path / "cost-projection.md"

    with patch("emitter.datetime", _PinnedDatetime):
        emit_artefacts(
            projected=projected,
            recommendations=recs,
            load_profile=profile,
            report_path=report_path,
            manifest_path=manifest_path,
            deploy_ref=PINNED_DEPLOY_REF,
            pre_deploy=False,
        )

    return manifest_path, report_path


def _strip_volatile(manifest: dict) -> dict:
    """Drop fields that are intentionally not part of the golden snapshot."""
    out = dict(manifest)
    out.pop("_comment", None)
    return out


def test_e2e_full_pipeline_matches_golden_manifest(tmp_path):
    manifest_path, _ = _run_pipeline(tmp_path)
    actual = json.loads(manifest_path.read_text())

    expected_path = EXPECTED_DIR / "cost-manifest.json"
    if REGENERATE:
        EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"regenerated {expected_path} (REGENERATE=1)")

    expected = _strip_volatile(json.loads(expected_path.read_text()))
    assert actual == expected, (
        "e2e manifest drifted from golden. If the change is intentional, "
        "regenerate via: CONSUMPTION_IQ_REGENERATE_GOLDEN=1 python3 -m pytest "
        f"{Path(__file__).relative_to(SKILL_ROOT.parent.parent)}"
    )


def test_e2e_full_pipeline_matches_golden_markdown(tmp_path):
    _, report_path = _run_pipeline(tmp_path)
    actual = report_path.read_text()

    expected_path = EXPECTED_DIR / "cost-projection.md"
    if REGENERATE:
        EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(actual)
        pytest.skip(f"regenerated {expected_path} (REGENERATE=1)")

    expected = expected_path.read_text()
    assert actual == expected, (
        "e2e markdown drifted from golden. If the change is intentional, "
        "regenerate via: CONSUMPTION_IQ_REGENERATE_GOLDEN=1 python3 -m pytest "
        f"{Path(__file__).relative_to(SKILL_ROOT.parent.parent)}"
    )


def test_e2e_manifest_schema_version_is_v1():
    """Cheap sanity check that the schema_version contract hasn't drifted."""
    expected = json.loads((EXPECTED_DIR / "cost-manifest.json").read_text())
    assert expected.get("schema_version", "").startswith("1.")


def test_e2e_pricing_client_fallback_is_safe():
    """Projectors must not crash when the pricing client returns no prices.

    This is critical for offline / first-deploy scenarios where the
    Retail Prices API is unreachable and the fixture is empty.
    """
    profile = load_or_prompt_profile(
        spec_path=FIXTURE_DIR / "specs" / "SPEC.md",
        non_interactive=True,
    )
    pricing = DeterministicPricingClient()
    for resource in SYNTHETIC_RESOURCES:
        result = project_resource(resource, profile, pricing)
        assert "monthly_cost_usd" in result, (
            f"projector for {resource['resource_kind']} must always return "
            "monthly_cost_usd, even when pricing client falls back"
        )
