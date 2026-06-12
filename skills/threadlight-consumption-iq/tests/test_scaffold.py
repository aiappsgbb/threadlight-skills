"""
Smoke test: every script + projector module is importable, the
projector registry resolves all seven v1 resource kinds, and the CLI's
argparse surface accepts every phase. Real per-phase logic tests live
in test_discover.py, test_projector_*.py, test_recommender.py,
test_emitter.py and arrive with their respective todos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_consumption_iq_module_imports():
    import consumption_iq  # noqa: F401

    assert hasattr(consumption_iq, "main")


def test_projector_registry_covers_v1_resources():
    from projectors import PROJECTOR_REGISTRY

    expected = {
        "Microsoft.CognitiveServices/accounts/deployments",
        "Microsoft.MachineLearningServices/workspaces",
        "Microsoft.App/containerApps",
        "Microsoft.DocumentDB/databaseAccounts",
        "Microsoft.Storage/storageAccounts",
        "Microsoft.ApiManagement/service",
        "Microsoft.Search/searchServices",
    }
    assert expected.issubset(PROJECTOR_REGISTRY.keys())


@pytest.mark.parametrize(
    "phase",
    ["discover", "load-profile", "price", "project", "recommend", "emit"],
)
def test_cli_accepts_phase(phase):
    import consumption_iq

    parser = consumption_iq.build_parser()
    args = parser.parse_args([phase])
    assert args.phase == phase


def test_cli_run_all():
    import consumption_iq

    parser = consumption_iq.build_parser()
    args = parser.parse_args(["run", "--all"])
    assert args.phase == "run"
    assert args.all is True


def test_pricing_client_constructs(tmp_path):
    from pricing_client import PricingClient

    client = PricingClient(cache_path=tmp_path / "cache.json")
    assert client.cache_path == tmp_path / "cache.json"
    assert client._cache == {}


def test_recommender_handles_empty_inputs():
    from recommender import score_and_rank

    assert score_and_rank([], {}) == []


def test_emitter_produces_well_formed_manifest(tmp_path):
    from emitter import emit_artefacts

    report = tmp_path / "docs" / "cost-projection.md"
    manifest = tmp_path / "specs" / "cost-manifest.json"
    emit_artefacts(
        projected=[],
        recommendations=[],
        load_profile={"declared_constraints": {}},
        report_path=report,
        manifest_path=manifest,
        deploy_ref="fixture/0",
        pre_deploy=True,
    )
    assert report.exists() and manifest.exists()

    import json

    parsed = json.loads(manifest.read_text())
    assert parsed["schema_version"] == "1.0"
    assert parsed["pre_deploy"] is True
    assert parsed["totals"] == {
        "monthly_cost_current_usd": 0,
        "monthly_cost_recommended_usd": 0,
        "monthly_savings_potential_usd": 0,
    }
