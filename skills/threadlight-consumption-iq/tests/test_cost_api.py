"""Tests for the stable no-discovery cost API (`cost_api.py`)."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cost_api import project_profile, build_cost_manifest  # noqa: E402
from pricing_client import PricingClient  # noqa: E402

PINNED = "2026-06-12T12:00:00+00:00"


def _client(tmp_path, meters_fixture=None):
    return PricingClient(cache_path=tmp_path / "cache.json", meters_fixture=meters_fixture)


def _profile():
    return {
        "workload_class": "chat-agent",
        "business_hours_only": False,
        "peak_requests_per_second": 1.0,
        "avg_tokens_per_request": 1000,
        "pages_per_month": 10000,
        "embedding_tokens_per_month": 5_000_000,
        "storage_gb_year_one": 20.0,
    }


def test_complete_projection_exposes_cost_per_transaction(tmp_path):
    client = _client(tmp_path)
    selectors = {
        "content_understanding": {"enabled": True, "tier": "standard"},
        "embeddings": {"enabled": True, "model": "text-embedding-3-small"},
    }
    manifest = project_profile(
        load_profile=_profile(),
        resources=[],  # meters only → all priced+verified
        selectors=selectors,
        pricing=client,
        transaction_unit="document",
        monthly_transactions=10000,
        generated_at=PINNED,
    )
    assert manifest["status"] == "complete"
    assert manifest["totals"]["complete"] is True
    assert manifest["totals"]["monthly_cost_current_usd"] is not None
    assert manifest["totals"]["cost_per_transaction_usd"] is not None
    assert manifest["meter_coverage"]["status"] == "complete"
    assert manifest["schema_version"] == "2.0"


def test_not_priceable_line_forces_partial_and_no_cpt(tmp_path):
    # Empty meters fixture → the embeddings meter is not-priceable.
    empty = tmp_path / "empty.json"
    empty.write_text('{"meters": {}}', encoding="utf-8")
    client = _client(tmp_path, meters_fixture=empty)
    selectors = {"embeddings": {"enabled": True, "model": "text-embedding-3-small"}}
    manifest = project_profile(
        load_profile=_profile(),
        resources=[],
        selectors=selectors,
        pricing=client,
        transaction_unit="document",
        monthly_transactions=10000,
        generated_at=PINNED,
    )
    assert manifest["status"] == "partial"
    assert manifest["totals"]["complete"] is False
    assert manifest["totals"]["monthly_cost_current_usd"] is None
    assert manifest["totals"]["cost_per_transaction_usd"] is None
    # exactly one meter, not-priceable
    assert manifest["meter_coverage"]["not_priceable"] == 1


def test_selected_meter_without_volume_marks_coverage_not_verified(tmp_path):
    client = _client(tmp_path)
    selectors = {"speech": {"enabled": True}}  # no media_hours_per_month in profile
    manifest = project_profile(
        load_profile={"workload_class": "chat-agent"},
        resources=[],
        selectors=selectors,
        pricing=client,
        transaction_unit="call",
        monthly_transactions=1000,
        generated_at=PINNED,
    )
    assert manifest["meter_coverage"]["status"] == "not-verified"
    assert manifest["totals"]["complete"] is False
    assert manifest["totals"]["cost_per_transaction_usd"] is None
    # the meter is still present (not dropped)
    assert len(manifest["meters"]) == 1


def test_unknown_projector_line_retained_in_manifest(tmp_path):
    client = _client(tmp_path)
    # Build directly with an unregistered meter line to prove it survives.
    unknown = {
        "meter_kind": "brand-new-meter",
        "source": "spec.selector.brand_new",
        "volume_driver": {"unit": "widgets", "monthly_quantity": 5},
        "verified": True,
        "selector": {},
        "pricing_status": "not-priceable",
        "monthly_cost_usd": None,
        "reason": "no projector registered",
        "alternatives": [],
    }
    manifest = build_cost_manifest(
        resources=[],
        meters=[unknown],
        load_profile={},
        transaction_unit="x",
        monthly_transactions=None,
        pricing=client,
        generated_at=PINNED,
    )
    kinds = [m["meter_kind"] for m in manifest["meters"]]
    assert "brand-new-meter" in kinds
    assert manifest["status"] == "partial"
    assert manifest["meter_coverage"]["not_priceable"] == 1


def test_resource_line_gets_pricing_status(tmp_path):
    client = _client(tmp_path)
    priced = {"resource_kind": "X", "monthly_cost_usd": 12.5}
    unpriced = {"resource_kind": "Y", "monthly_cost_usd": None}
    manifest = build_cost_manifest(
        resources=[priced, unpriced],
        meters=[],
        load_profile={},
        transaction_unit="x",
        monthly_transactions=100,
        pricing=client,
        generated_at=PINNED,
    )
    statuses = {r["resource_kind"]: r["pricing_status"] for r in manifest["resources"]}
    assert statuses["X"] == "priced"
    assert statuses["Y"] == "not-priceable"
    assert manifest["totals"]["complete"] is False  # Y is not priceable


def test_ptu_scenarios_present_with_break_even(tmp_path):
    client = _client(tmp_path)
    manifest = project_profile(
        load_profile={"ptu_model": "gpt-4o", "pinned_region": "eastus2", "ptu_units": 25},
        resources=[],
        selectors={},
        pricing=client,
        transaction_unit="x",
        monthly_transactions=100,
        generated_at=PINNED,
    )
    ptu = manifest.get("ptu_scenarios")
    assert ptu is not None
    commitments = [s["commitment"] for s in ptu["scenarios"]]
    assert commitments == ["hourly", "one-month", "one-year"]
    assert isinstance(ptu["break_even"], str) and ptu["break_even"]


def test_deterministic_given_pinned_generated_at(tmp_path):
    client = _client(tmp_path)
    kwargs = dict(
        load_profile=_profile(),
        resources=[],
        selectors={"content_understanding": {"enabled": True}},
        pricing=client,
        transaction_unit="document",
        monthly_transactions=10000,
        generated_at=PINNED,
    )
    a = project_profile(**kwargs)
    b = project_profile(**kwargs)
    assert a == b
