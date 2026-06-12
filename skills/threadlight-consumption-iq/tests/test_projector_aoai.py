"""
Tests for projectors/aoai.py.

All tests mock the pricing_client so no network calls are made.

FakePricing routing:
  - tier="PTU"              → ptu_per_unit  (per PTU-unit per month)
  - tier="PAYG", "Output"   → payg_output   (per 1k output tokens)
  - tier="PAYG", otherwise  → payg_input    (per 1k input tokens)
  - swap_model prices same as payg_* but keyed on name
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.aoai import project  # noqa: E402


# ---------------------------------------------------------------------------
# Fake pricing client
# ---------------------------------------------------------------------------

class FakePricing:
    """Returns deterministic prices indexed by tier and meter."""

    def __init__(
        self,
        payg_input: float = 0.01,
        payg_output: float = 0.03,
        ptu_per_unit: float = 100.0,
        # Optional per-model overrides for model-swap tests
        model_prices: dict | None = None,
    ):
        self.payg_input = payg_input
        self.payg_output = payg_output
        self.ptu_per_unit = ptu_per_unit
        self.model_prices = model_prices or {}

    def get_price(self, resource_kind: str, sku: dict) -> dict:
        tier = sku.get("tier", "PAYG")
        meter = sku.get("meter_substring", "")
        name = sku.get("name", "")

        if tier == "PTU":
            price = self.ptu_per_unit
            unit = "per-PTU-month"
        elif "Output" in meter:
            price = self.model_prices.get(name, {}).get("output", self.payg_output)
            unit = "per-1k-output-tokens"
        else:
            price = self.model_prices.get(name, {}).get("input", self.payg_input)
            unit = "per-1k-input-tokens"

        return {"unit_price_usd": price, "unit": unit, "price_source": "live"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payg_sku(name="gpt-4o", region="eastus2", extra=None):
    return {
        "name": name, "tier": "PAYG", "region": region,
        "capacity": None, "extra": extra or {},
    }


def _ptu_sku(name="gpt-4o", region="eastus2", capacity=25):
    return {
        "name": name, "tier": "PTU", "region": region,
        "capacity": capacity, "extra": {},
    }


def _load(rps=1.0, tokens=1000, business_hours=False):
    return {
        "peak_requests_per_second": rps,
        "avg_tokens_per_request": tokens,
        "business_hours_only": business_hours,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_aoai_payg_to_ptu_recommendation_at_high_load():
    """
    At high load (100 RPS × 2000 tokens/req, 24/7), PTU@25 must be cheaper.

    Math with payg_input=$0.01/1k, payg_output=$0.03/1k, ptu=$100/unit:
      seconds/month = 2,592,000
      monthly_input  = 100 × 2,592,000 × 2000 × 0.65 = 337,152,000,000 tokens
      monthly_output = 100 × 2,592,000 × 2000 × 0.35 = 181,440,000,000 tokens
      PAYG_cost ≈ $3,371,520 + $5,443,200 = ~$8.8 M
      PTU@25   = 25 × $100 = $2,500   ← much cheaper
    """
    fake = FakePricing(payg_input=0.01, payg_output=0.03, ptu_per_unit=100.0)
    result = project(_payg_sku(), _load(rps=100, tokens=2000), fake)

    assert result["monthly_cost_usd"] > 0

    ptu_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "PTU"]
    ptu25 = next((a for a in ptu_alts if a["sku"]["capacity"] == 25), None)
    assert ptu25 is not None, "PTU@25 alternative must be present"
    assert ptu25["delta_usd"] < 0, "PTU@25 should be cheaper than PAYG at high load"
    assert ptu25["monthly_cost_usd"] == pytest.approx(25 * 100.0)


def test_aoai_payg_stays_payg_at_low_load():
    """
    At very low load (0.001 RPS × 100 tokens), all PTU alternatives cost more.

    Math with payg_input=$0.01/1k, payg_output=$0.03/1k, ptu=$100/unit:
      monthly_input  ≈ 168 tokens → PAYG ≈ $0.004
      PTU@1          = $100  ← all PTU units are vastly more expensive
    """
    fake = FakePricing(payg_input=0.01, payg_output=0.03, ptu_per_unit=100.0)
    result = project(_payg_sku(), _load(rps=0.001, tokens=100), fake)

    ptu_alts = [a for a in result["alternatives"] if a["sku"]["tier"] == "PTU"]
    assert len(ptu_alts) > 0, "Must still enumerate PTU alternatives"
    assert all(
        a["delta_usd"] > 0 for a in ptu_alts
    ), "All PTU alternatives should be more expensive than PAYG at low load"


def test_aoai_io_split_override():
    """
    extra.io_split = [0.5, 0.5] must produce equal input/output token counts,
    versus the default [0.65, 0.35] which skews toward input.
    """
    fake = FakePricing(payg_input=0.01, payg_output=0.03, ptu_per_unit=100.0)

    sku_equal_split = _payg_sku(extra={"io_split": [0.5, 0.5]})
    sku_default = _payg_sku()
    load = _load(rps=1, tokens=1000)

    result_eq = project(sku_equal_split, load, fake)
    result_def = project(sku_default, load, fake)

    units_eq = result_eq["monthly_units_consumed"]
    units_def = result_def["monthly_units_consumed"]

    assert units_eq["input_tokens"] == units_eq["output_tokens"], \
        "50/50 split should produce equal input and output token counts"
    assert units_def["input_tokens"] > units_def["output_tokens"], \
        "Default 65/35 split should produce more input tokens than output"


def test_aoai_alternatives_include_region_swaps():
    """
    For a PAYG sku in eastus2, alternatives must include swedencentral and
    northcentralus but NOT a duplicate eastus2 entry.
    """
    fake = FakePricing()
    result = project(_payg_sku(region="eastus2"), _load(), fake)

    region_alts = [
        a for a in result["alternatives"]
        if a["sku"]["tier"] == "PAYG" and a["sku"]["name"] == "gpt-4o"
    ]
    regions = {a["sku"]["region"] for a in region_alts}
    assert "swedencentral" in regions
    assert "northcentralus" in regions
    assert "eastus2" not in regions, "Current region must not appear in region alternatives"


def test_aoai_handles_missing_price_gracefully():
    """
    When pricing_client returns unit_price_usd=None for every call,
    the projector must return monthly_cost_usd=None, price_source="fallback",
    and a single sentinel alternative with caveats explaining the gap.
    """
    class NoPricing:
        def get_price(self, resource_kind, sku):
            return {"unit_price_usd": None, "price_source": "fallback"}

    result = project(_payg_sku(), _load(), NoPricing())

    assert result["monthly_cost_usd"] is None
    assert result["price_source"] == "fallback"
    assert len(result["alternatives"]) == 1
    sentinel = result["alternatives"][0]
    assert sentinel["monthly_cost_usd"] is None
    assert sentinel["satisfies_constraints"] is False
    assert len(sentinel["caveats"]) > 0


def test_aoai_ptu_current_sku_computes_correctly():
    """
    PTU current SKU: cost = capacity × ptu_price_per_unit.
    Units consumed must have ptu_units and NOT input/output tokens.
    """
    fake = FakePricing(ptu_per_unit=260.0)
    sku = _ptu_sku(capacity=25)
    result = project(sku, _load(rps=50, tokens=2000), fake)

    assert result["monthly_cost_usd"] == pytest.approx(25 * 260.0)
    assert "ptu_units" in result["monthly_units_consumed"]
    assert "input_tokens" not in result["monthly_units_consumed"]


def test_aoai_model_swap_alternative_present():
    """gpt-4o alternatives must include a gpt-4o-mini model-swap entry."""
    fake = FakePricing(
        payg_input=0.01,
        payg_output=0.03,
        ptu_per_unit=100.0,
        model_prices={
            "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        },
    )
    result = project(_payg_sku(name="gpt-4o"), _load(rps=1, tokens=1000), fake)

    swap_alts = [
        a for a in result["alternatives"] if a["sku"]["name"] == "gpt-4o-mini"
    ]
    assert len(swap_alts) == 1, "Must include exactly one model-swap alternative"
    assert swap_alts[0]["delta_usd"] < 0, "gpt-4o-mini should be cheaper"
    assert swap_alts[0]["satisfies_constraints"] is True
    assert any("quality" in c.lower() for c in swap_alts[0]["caveats"])
