"""
Tests for apim.project.

Mocks pricing_client so hardcoded fallback prices are used predictably.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.apim import (  # noqa: E402
    CONSUMPTION_PRICE_PER_10K,
    FREE_GRANT_CALLS,
    HOURS_PER_MONTH,
    MAX_MONTHLY_REQUESTS,
    TIER_PER_UNIT_HOUR,
    project,
)


class FakePricing:
    def get_price(self, resource_kind, sku):
        return {"unit_price_usd": None, "unit": None, "price_source": "fallback"}


def _lp(rps=10, business_hours=False):
    return {"peak_requests_per_second": rps, "business_hours_only": business_hours}


# ---------------------------------------------------------------------------
# test_apim_consumption_within_free_grant_is_zero
# ---------------------------------------------------------------------------

def test_apim_consumption_within_free_grant_is_zero():
    # 0.1 rps × 86400 × 30 = 259,200 requests — well below the 1M free grant.
    sku = {"tier": "Consumption", "capacity": None, "extra": {}}
    result = project(sku, _lp(rps=0.1), FakePricing())

    assert result["monthly_cost_usd"] == 0.0
    assert result["monthly_units_consumed"]["requests"] < FREE_GRANT_CALLS


def test_apim_consumption_overage_math():
    # 5 rps × 86400 × 30 = 12,960,000 requests → ~11.96M overage
    sku = {"tier": "Consumption", "capacity": None, "extra": {}}
    lp = _lp(rps=5, business_hours=False)
    result = project(sku, lp, FakePricing())

    seconds = 24 * 3600 * 30
    monthly_requests = min(5 * seconds, MAX_MONTHLY_REQUESTS)
    expected = max(0.0, monthly_requests - FREE_GRANT_CALLS) * CONSUMPTION_PRICE_PER_10K / 10_000
    assert abs(result["monthly_cost_usd"] - expected) < 0.01


def test_apim_consumption_request_cap_applied():
    # Very high RPS should be capped at MAX_MONTHLY_REQUESTS.
    sku = {"tier": "Consumption", "capacity": None, "extra": {}}
    result = project(sku, _lp(rps=9999), FakePricing())
    assert result["monthly_units_consumed"]["requests"] == MAX_MONTHLY_REQUESTS


# ---------------------------------------------------------------------------
# test_apim_premium_per_unit_hourly
# ---------------------------------------------------------------------------

def test_apim_premium_per_unit_hourly():
    sku = {"tier": "Premium", "capacity": 2, "extra": {}}
    result = project(sku, _lp(rps=10), FakePricing())

    # 2 units × $3.83/unit/hr × 730h
    expected = 2 * TIER_PER_UNIT_HOUR["Premium"] * HOURS_PER_MONTH
    assert abs(result["monthly_cost_usd"] - expected) < 0.01
    assert result["monthly_units_consumed"]["tier_unit_hours"] == 2 * HOURS_PER_MONTH


def test_apim_basicv2_1_unit_cost():
    sku = {"tier": "BasicV2", "capacity": 1, "extra": {}}
    result = project(sku, _lp(rps=10), FakePricing())

    expected = 1 * TIER_PER_UNIT_HOUR["BasicV2"] * HOURS_PER_MONTH
    assert abs(result["monthly_cost_usd"] - expected) < 0.01


def test_apim_consumption_alternative_present_for_tier_based():
    sku = {"tier": "Premium", "capacity": 4, "extra": {}}
    result = project(sku, _lp(rps=1), FakePricing())

    consumption_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "Consumption"]
    assert len(consumption_alts) == 1


def test_apim_consumption_no_consumption_alt_for_consumption():
    sku = {"tier": "Consumption", "capacity": None, "extra": {}}
    result = project(sku, _lp(rps=1), FakePricing())

    consumption_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "Consumption"]
    assert consumption_alts == []


def test_apim_alternatives_cover_all_tiers_and_units():
    sku = {"tier": "Consumption", "capacity": None, "extra": {}}
    result = project(sku, _lp(rps=1), FakePricing())

    tier_unit_pairs = {
        (a["sku"]["tier"], a["sku"]["capacity"])
        for a in result["alternatives"]
        if a["sku"]["tier"] != "Consumption"
    }
    for tier in ("BasicV2", "StandardV2", "Premium"):
        for units in (1, 2, 4):
            assert (tier, units) in tier_unit_pairs, f"Missing {tier}×{units}"
