"""
Tests for projectors/aca.py.

All tests use a FakePricing that returns unit_price_usd=None so the projector
falls back to the hardcoded CONSUMPTION_PRICES. This isolates the formula.

Key free-grant constants:
  FREE_VCPU_SECONDS = 180_000
  FREE_MEM_SECONDS  = 360_000
  FREE_REQUESTS     = 2_000_000
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.aca import (  # noqa: E402
    CONSUMPTION_PRICES,
    DEDICATED_PER_HOUR,
    FREE_MEM_SECONDS,
    FREE_REQUESTS,
    FREE_VCPU_SECONDS,
    project,
)


# ---------------------------------------------------------------------------
# Fake pricing client
# ---------------------------------------------------------------------------

class FakePricing:
    """Always returns None → forces projector to use hardcoded fallback prices."""

    def get_price(self, resource_kind: str, sku: dict) -> dict:
        return {"unit_price_usd": None, "price_source": "fallback"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _consumption_sku(
    vcpu=0.5,
    memory_gib=1.0,
    min_replicas=1,
    max_replicas=10,
    rps_per_replica=10,
    region="eastus2",
):
    return {
        "name": "Consumption",
        "tier": "Consumption",
        "region": region,
        "capacity": None,
        "extra": {
            "vcpu": vcpu,
            "memory_gib": memory_gib,
            "min_replicas": min_replicas,
            "max_replicas": max_replicas,
            "requests_per_second_per_replica": rps_per_replica,
        },
    }


def _dedicated_sku(profile="D4", region="eastus2", min_replicas=1, max_replicas=3):
    return {
        "name": "Dedicated",
        "tier": profile,
        "region": region,
        "capacity": None,
        "extra": {"min_replicas": min_replicas, "max_replicas": max_replicas},
    }


def _load(rps=1.0, business_hours=False):
    return {
        "peak_requests_per_second": rps,
        "business_hours_only": business_hours,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_aca_consumption_within_free_grant_is_zero():
    """
    With business_hours_only=True (8h × 22d = 633,600 s/month), a 0.25 vCPU /
    0.5 GiB single-replica deployment stays within all three free grants.

      vcpu_seconds = 0.25 × 633,600 × 1 = 158,400  < 180,000 ✓
      mem_seconds  = 0.50 × 633,600 × 1 = 316,800  < 360,000 ✓
      requests     = 1  × 633,600       = 633,600   < 2,000,000 ✓
    """
    sku = _consumption_sku(vcpu=0.25, memory_gib=0.5, min_replicas=1, max_replicas=1)
    result = project(sku, _load(rps=1.0, business_hours=True), FakePricing())

    assert result["monthly_cost_usd"] == pytest.approx(0.0)
    assert result["monthly_units_consumed"]["vcpu_seconds"] < FREE_VCPU_SECONDS
    assert result["monthly_units_consumed"]["memory_gib_seconds"] < FREE_MEM_SECONDS
    assert result["monthly_units_consumed"]["requests"] < FREE_REQUESTS


def test_aca_consumption_beyond_free_grant():
    """
    With 24/7 (30d), 0.5 vCPU, 1 GiB, 1 min replica at 2 RPS:

      seconds/month = 2,592,000
      vcpu_seconds  = 0.5 × 2,592,000 = 1,296,000  → billable = 1,116,000
      mem_seconds   = 1.0 × 2,592,000 = 2,592,000  → billable = 2,232,000
      requests      = 2   × 2,592,000 = 5,184,000  → billable = 3,184,000

      cost = 1,116,000 × 0.000024
           + 2,232,000 × 0.000003
           + 3,184,000 × 0.40 / 1,000,000
           = 26.784 + 6.696 + 1.2736 ≈ $34.75
    """
    sku = _consumption_sku(vcpu=0.5, memory_gib=1.0, min_replicas=1, max_replicas=10)
    result = project(sku, _load(rps=2.0, business_hours=False), FakePricing())

    seconds_per_month = 24 * 3600 * 30  # 2,592,000
    total_vcpu = 0.5 * seconds_per_month
    total_mem = 1.0 * seconds_per_month
    total_req = 2.0 * seconds_per_month

    billable_vcpu = max(0, total_vcpu - FREE_VCPU_SECONDS)
    billable_mem = max(0, total_mem - FREE_MEM_SECONDS)
    billable_req = max(0, total_req - FREE_REQUESTS)

    expected = (
        billable_vcpu * CONSUMPTION_PRICES["vcpu_per_sec"]
        + billable_mem * CONSUMPTION_PRICES["mem_per_gib_sec"]
        + billable_req * CONSUMPTION_PRICES["req_per_million"] / 1_000_000
    )

    assert result["monthly_cost_usd"] == pytest.approx(expected, rel=1e-5)
    assert result["monthly_cost_usd"] > 0


def test_aca_dedicated_d4_costs_730_hours():
    """
    Dedicated D4 is billed at $0.20/h × 730 h/month = $146.
    monthly_units_consumed must be {"workload_profile_hours": 730}.
    """
    result = project(_dedicated_sku("D4"), _load(), FakePricing())

    assert result["monthly_cost_usd"] == pytest.approx(DEDICATED_PER_HOUR["D4"] * 730)
    assert result["monthly_cost_usd"] == pytest.approx(146.0)
    assert result["monthly_units_consumed"] == {"workload_profile_hours": 730}
    assert result["price_source"] == "fallback"


def test_aca_alternatives_include_dedicated_when_consumption():
    """
    A Consumption-tier current SKU must generate alternatives for
    D4, D8, E4, and E8 dedicated profiles.
    """
    sku = _consumption_sku()
    result = project(sku, _load(rps=1.0), FakePricing())

    dedicated_alts = [
        a for a in result["alternatives"] if a["sku"]["tier"] in ("D4", "D8", "E4", "E8")
    ]
    profiles = {a["sku"]["tier"] for a in dedicated_alts}
    assert profiles == {"D4", "D8", "E4", "E8"}


def test_aca_alternatives_include_consumption_when_dedicated():
    """
    A Dedicated D4 current SKU must include a Consumption alternative.
    """
    result = project(_dedicated_sku("D4"), _load(), FakePricing())

    consumption_alts = [
        a for a in result["alternatives"] if a["sku"]["tier"] == "Consumption"
    ]
    assert len(consumption_alts) == 1


def test_aca_dedicated_alternatives_exclude_current_profile():
    """
    A Dedicated D4 current SKU must NOT include D4 in its alternatives.
    Other dedicated profiles (D8, E4, E8) must be present.
    """
    result = project(_dedicated_sku("D4"), _load(), FakePricing())

    tiers = {a["sku"]["tier"] for a in result["alternatives"]}
    assert "D4" not in tiers, "Current profile D4 must not appear in alternatives"
    assert {"D8", "E4", "E8"}.issubset(tiers)


def test_aca_replica_sweep_generates_multiple_options():
    """
    Consumption tier: replica sweep (1-3, 1-10, 2-20, 3-30) must appear,
    minus any combo that matches current (min_replicas, max_replicas).
    """
    sku = _consumption_sku(min_replicas=1, max_replicas=5)  # not in sweep list
    result = project(sku, _load(), FakePricing())

    sweep_alts = [
        a for a in result["alternatives"]
        if a["sku"]["tier"] == "Consumption"
    ]
    # All four sweep combos should appear since (1,5) matches none of (1-3),(1-10),(2-20),(3-30)
    sweep_combos = {
        (a["sku"]["extra"]["min_replicas"], a["sku"]["extra"]["max_replicas"])
        for a in sweep_alts
    }
    assert (1, 3) in sweep_combos
    assert (1, 10) in sweep_combos
    assert (2, 20) in sweep_combos
    assert (3, 30) in sweep_combos


def test_aca_dedicated_d8_is_twice_d4():
    """D8 costs exactly 2× D4 by the hardcoded price table."""
    d4 = project(_dedicated_sku("D4"), _load(), FakePricing())
    d8 = project(_dedicated_sku("D8"), _load(), FakePricing())

    assert d8["monthly_cost_usd"] == pytest.approx(d4["monthly_cost_usd"] * 2)


# ---------------------------------------------------------------------------
# Test: projector is defensive about a non-numeric vCPU
#
# Discovery resolves the common `json('x')` idiom, but the projector must never
# crash on a stray string/None vCPU (hand-authored rollout topology, an exotic
# ARM expression discovery can't resolve, etc.). It coerces to float and falls
# back to the 0.5 default rather than raising TypeError.
# ---------------------------------------------------------------------------

def test_aca_consumption_coerces_numeric_string_vcpu():
    """vcpu given as the string "0.5" must compute the same cost as float 0.5."""
    str_sku = _consumption_sku(vcpu="0.5", memory_gib=1.0, min_replicas=1, max_replicas=10)
    float_sku = _consumption_sku(vcpu=0.5, memory_gib=1.0, min_replicas=1, max_replicas=10)
    load = _load(rps=2.0, business_hours=False)

    str_result = project(str_sku, load, FakePricing())
    float_result = project(float_sku, load, FakePricing())

    assert str_result["monthly_cost_usd"] == pytest.approx(float_result["monthly_cost_usd"])


def test_aca_consumption_unresolved_vcpu_falls_back_to_default():
    """An unresolvable vCPU expression must not crash; falls back to 0.5 default."""
    bad_sku = _consumption_sku(vcpu="[json('1.0')]", memory_gib=1.0, min_replicas=1, max_replicas=10)
    default_sku = _consumption_sku(vcpu=0.5, memory_gib=1.0, min_replicas=1, max_replicas=10)
    load = _load(rps=2.0, business_hours=False)

    bad_result = project(bad_sku, load, FakePricing())  # must not raise
    default_result = project(default_sku, load, FakePricing())

    assert bad_result["monthly_cost_usd"] == pytest.approx(default_result["monthly_cost_usd"])


def test_aca_consumption_none_vcpu_falls_back_to_default():
    """vcpu=None must not crash; falls back to 0.5 default."""
    none_sku = _consumption_sku(vcpu=None, memory_gib=1.0, min_replicas=1, max_replicas=10)
    default_sku = _consumption_sku(vcpu=0.5, memory_gib=1.0, min_replicas=1, max_replicas=10)
    load = _load(rps=2.0, business_hours=False)

    none_result = project(none_sku, load, FakePricing())  # must not raise
    default_result = project(default_sku, load, FakePricing())

    assert none_result["monthly_cost_usd"] == pytest.approx(default_result["monthly_cost_usd"])
