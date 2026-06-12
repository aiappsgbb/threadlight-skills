"""
Tests for cosmos.project.

Mocks pricing_client so hardcoded fallback prices are used predictably.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.cosmos import (  # noqa: E402
    AUTOSCALE_UTILIZATION_FACTOR,
    AUTOSCALE_RU_PRICE_PER_HOUR,
    HOURS_PER_MONTH,
    RU_PRICE_PER_HOUR,
    SERVERLESS_RU_PRICE_PER_MILLION,
    STORAGE_PRICE_PER_GB_MONTH,
    project,
)


class FakePricing:
    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback"}


def _lp(rps=10, storage_gb=100, business_hours=False):
    return {
        "peak_requests_per_second": rps,
        "business_hours_only": business_hours,
        "storage_gb_year_one": storage_gb,
    }


# ---------------------------------------------------------------------------
# test_cosmos_provisioned_baseline
# ---------------------------------------------------------------------------

def test_cosmos_provisioned_baseline():
    sku = {"tier": "provisioned", "capacity": 400, "extra": {}}
    lp = _lp(rps=10, storage_gb=100)
    result = project(sku, lp, FakePricing())

    # 400 RU/s × $0.00008/hr × 730h + 100 GB × $0.25/GB/month
    expected = 400 * RU_PRICE_PER_HOUR * HOURS_PER_MONTH + 100 * STORAGE_PRICE_PER_GB_MONTH
    assert abs(result["monthly_cost_usd"] - expected) < 0.01
    assert result["monthly_units_consumed"]["ru_provisioned"] == 400
    assert result["monthly_units_consumed"]["storage_gb"] == 100
    assert result["price_source"] == "fallback"
    assert result["current_sku"] is sku


def test_cosmos_provisioned_redundancy_defaults_to_none():
    sku = {"tier": "provisioned", "capacity": 1000, "extra": {}}
    result = project(sku, _lp(), FakePricing())
    for alt in result["alternatives"]:
        assert alt["sku"]["redundancy"] == "none"


def test_cosmos_provisioned_multi_write_sets_zone_redundant():
    sku = {"tier": "provisioned", "capacity": 1000, "extra": {"multi_write": True}}
    result = project(sku, _lp(), FakePricing())
    for alt in result["alternatives"]:
        assert alt["sku"]["redundancy"] == "zone-redundant"


# ---------------------------------------------------------------------------
# test_cosmos_serverless_recommended_for_low_rps
# ---------------------------------------------------------------------------

def test_cosmos_serverless_recommended_for_low_rps():
    # A provisioned 10k RU account running at 1 rps should be beaten by serverless.
    sku = {"tier": "provisioned", "capacity": 10_000, "extra": {}}
    lp = _lp(rps=1, storage_gb=10)
    result = project(sku, lp, FakePricing())

    serverless_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "serverless"]
    assert len(serverless_alts) == 1, "exactly one serverless alternative expected"
    assert serverless_alts[0]["monthly_cost_usd"] < result["monthly_cost_usd"]


def test_cosmos_serverless_current_skips_serverless_alt():
    sku = {"tier": "serverless", "capacity": None, "extra": {}}
    result = project(sku, _lp(rps=5), FakePricing())
    serverless_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "serverless"]
    assert serverless_alts == [], "should not recommend serverless when already serverless"


def test_cosmos_serverless_cost_math():
    sku = {"tier": "serverless", "capacity": None, "extra": {"ru_per_op": 5}}
    lp = _lp(rps=10, storage_gb=50, business_hours=False)
    result = project(sku, lp, FakePricing())

    seconds = 24 * 3600 * 30
    monthly_requests = 10 * seconds
    monthly_ru = monthly_requests * 5
    expected = monthly_ru * SERVERLESS_RU_PRICE_PER_MILLION / 1_000_000 + 50 * STORAGE_PRICE_PER_GB_MONTH
    assert abs(result["monthly_cost_usd"] - expected) < 0.01
    assert result["monthly_units_consumed"]["ru_consumed"] == monthly_ru


# ---------------------------------------------------------------------------
# test_cosmos_autoscale_alternative_present
# ---------------------------------------------------------------------------

def test_cosmos_autoscale_alternative_present():
    sku = {"tier": "provisioned", "capacity": 400, "extra": {}}
    lp = _lp(rps=5, storage_gb=50)
    result = project(sku, lp, FakePricing())

    autoscale_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "autoscale"]
    assert len(autoscale_alts) == 3, "expected autoscale at 1k/4k/10k max RU"
    max_rus = sorted(a["sku"]["capacity"] for a in autoscale_alts)
    assert max_rus == [1000, 4000, 10000]


def test_cosmos_autoscale_cost_math():
    sku = {"tier": "autoscale", "capacity": 4000, "extra": {}}
    lp = _lp(rps=5, storage_gb=20)
    result = project(sku, lp, FakePricing())

    expected = (
        4000 * AUTOSCALE_RU_PRICE_PER_HOUR * HOURS_PER_MONTH * AUTOSCALE_UTILIZATION_FACTOR
        + 20 * STORAGE_PRICE_PER_GB_MONTH
    )
    assert abs(result["monthly_cost_usd"] - expected) < 0.01
    assert result["monthly_units_consumed"]["ru_provisioned"] == 4000


def test_cosmos_alternatives_all_carry_redundancy():
    sku = {"tier": "provisioned", "capacity": 400, "extra": {}}
    result = project(sku, _lp(), FakePricing())
    for alt in result["alternatives"]:
        assert "redundancy" in alt["sku"], f"alternative missing redundancy: {alt}"
