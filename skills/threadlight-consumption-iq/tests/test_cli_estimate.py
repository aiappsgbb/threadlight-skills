"""
CLI tests for the `estimate` subcommand and `run --all --pre-sales`.

These exercise `consumption_iq.main(...)` with a stubbed discover phase (so no
`az bicep build` is needed) and a rollout profile written to disk, verifying the
CLI threads the rollout through the orchestrator and writes the phased
artefacts + one-pager.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import consumption_iq  # noqa: E402


_SYNTHETIC = [
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


def _rollout_dict() -> dict:
    lp = {
        "workload_class": "chat-agent",
        "peak_concurrent_sessions": 20,
        "avg_requests_per_session": 8,
        "avg_tokens_per_request": 1500,
        "peak_requests_per_second": 2.0,
        "business_hours_only": False,
        "cosmos_gb_year_one": 50.0,
        "storage_gb_year_one": 25.0,
        "ai_search_documents": 100000,
        "monthly_growth_rate": 0.1,
        "declared_constraints": {"max_p95_latency_ms": 2000, "min_redundancy": "zonal"},
    }
    small = {**lp, "peak_requests_per_second": 0.5, "peak_concurrent_sessions": 5}
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
        ],
    }


def _write_rollout(tmp_path: Path) -> Path:
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps(_rollout_dict()))
    return path


def test_parser_accepts_estimate_subcommand():
    parser = consumption_iq.build_parser()
    args = parser.parse_args(["estimate", "--rollout", "x.json"])
    assert args.phase == "estimate"
    assert str(args.rollout) == "x.json"


def test_estimate_cli_writes_artefacts(tmp_path, monkeypatch):
    monkeypatch.setattr(consumption_iq, "_phase_discover", lambda args: list(_SYNTHETIC))
    # PricingClient must not hit the network — stub it to the fallback shape.
    monkeypatch.setattr(
        consumption_iq, "PricingClient",
        lambda *a, **k: type("P", (), {
            "get_price": lambda self, rk, sku: {"unit_price_usd": None, "unit": None,
                                                "price_source": "fallback", "fetched_at": None,
                                                "azure_meter_id": None, "raw": {}},
            "warm": lambda self, r: None,
        })(),
    )
    rollout = _write_rollout(tmp_path)
    report = tmp_path / "cost-estimate.md"
    manifest = tmp_path / "cost-estimate-manifest.json"
    onepager = tmp_path / "estimate-onepager.html"

    rc = consumption_iq.main([
        "estimate", "--rollout", str(rollout),
        "--report", str(report), "--manifest", str(manifest),
        "--onepager", str(onepager), "--pre-deploy",
    ])
    assert rc == 0
    assert report.exists() and manifest.exists() and onepager.exists()
    data = json.loads(manifest.read_text())
    assert data["schema_version"] == "1.1"
    assert data["pre_sales"] is True
    assert data["price_basis"] == "ea"


def test_run_all_pre_sales_routes_to_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(consumption_iq, "_phase_discover", lambda args: list(_SYNTHETIC))
    monkeypatch.setattr(
        consumption_iq, "PricingClient",
        lambda *a, **k: type("P", (), {
            "get_price": lambda self, rk, sku: {"unit_price_usd": None, "unit": None,
                                                "price_source": "fallback", "fetched_at": None,
                                                "azure_meter_id": None, "raw": {}},
            "warm": lambda self, r: None,
        })(),
    )
    rollout = _write_rollout(tmp_path)
    manifest = tmp_path / "m.json"
    rc = consumption_iq.main([
        "run", "--all", "--pre-sales", "--rollout", str(rollout),
        "--report", str(tmp_path / "r.md"), "--manifest", str(manifest),
        "--pre-deploy",
    ])
    assert rc == 0
    assert json.loads(manifest.read_text())["pre_sales"] is True


def test_run_all_pre_sales_requires_rollout(tmp_path, monkeypatch):
    rc = consumption_iq.main(["run", "--all", "--pre-sales"])
    assert rc == 2


def _rollout_with_topology() -> dict:
    r = _rollout_dict()
    # Declare the topology IN the rollout — the pre-sales promise: no repo.
    r["resources"] = list(_SYNTHETIC)
    return r


def test_estimate_cli_uses_declared_topology_without_discovery(tmp_path, monkeypatch):
    """The headline pre-sales guarantee: estimate runs with NO repo discovery
    when the rollout declares its own topology. We prove it by making discovery
    explode — the run must still succeed and project the declared resources."""
    def _boom(args):
        raise AssertionError("discover_resources must NOT be called for a declared topology")

    monkeypatch.setattr(consumption_iq, "_phase_discover", _boom)
    monkeypatch.setattr(
        consumption_iq, "PricingClient",
        lambda *a, **k: type("P", (), {
            "get_price": lambda self, rk, sku: {"unit_price_usd": None, "unit": None,
                                                "price_source": "fallback", "fetched_at": None,
                                                "azure_meter_id": None, "raw": {}},
            "warm": lambda self, r: None,
        })(),
    )
    rollout = tmp_path / "rollout.json"
    rollout.write_text(json.dumps(_rollout_with_topology()))
    manifest = tmp_path / "m.json"
    rc = consumption_iq.main([
        "estimate", "--rollout", str(rollout),
        "--report", str(tmp_path / "r.md"), "--manifest", str(manifest),
        "--pre-deploy",
    ])
    assert rc == 0
    data = json.loads(manifest.read_text())
    # Each phase projected the two declared resources.
    for phase in data["phases"]:
        kinds = {r["resource_kind"] for r in phase["resources"]}
        assert "Microsoft.App/containerApps" in kinds
        assert "Microsoft.OperationalInsights/workspaces" in kinds


def test_estimate_cli_invalid_discount_exits_4_not_traceback(tmp_path, monkeypatch):
    """An out-of-range discount is a fail-fast input error, not a bug. It must
    exit 4 (like RolloutProfileError) with a message — never escape as an
    uncaught DiscountError traceback (exit 1)."""
    monkeypatch.setattr(consumption_iq, "_phase_discover", lambda args: list(_SYNTHETIC))
    monkeypatch.setattr(
        consumption_iq, "PricingClient",
        lambda *a, **k: type("P", (), {
            "get_price": lambda self, rk, sku: {"unit_price_usd": None, "unit": None,
                                                "price_source": "fallback", "fetched_at": None,
                                                "azure_meter_id": None, "raw": {}},
            "warm": lambda self, r: None,
        })(),
    )
    rollout = tmp_path / "rollout.json"
    rollout.write_text(json.dumps(_rollout_dict()))
    rc = consumption_iq.main([
        "estimate", "--rollout", str(rollout),
        "--report", str(tmp_path / "r.md"), "--manifest", str(tmp_path / "m.json"),
        "--discount", "2.0",  # > 1.0 is invalid
    ])
    assert rc == 4
