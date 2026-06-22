"""
Tests for scripts/discount.py — EA / MCA discount multiplier.

Pre-sales estimates are usually shown net of the customer's enterprise
agreement. This module applies a single discount multiplier to retail figures,
records the `price_basis` (retail | ea | mca), and injects a caveat so nobody
mistakes an internal planning number for a contractual quote.

It NEVER discards the retail number — both retail and discounted are kept so a
reviewer can always see the list price the multiplier was applied to.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from discount import (  # noqa: E402
    DiscountError,
    apply_discount,
    discount_manifest,
)


# ---------------------------------------------------------------------------
# apply_discount (scalar)
# ---------------------------------------------------------------------------

def test_apply_discount_scales_amount():
    assert apply_discount(100.0, 0.85) == pytest.approx(85.0)


def test_apply_discount_identity_at_one():
    assert apply_discount(100.0, 1.0) == pytest.approx(100.0)


def test_apply_discount_rejects_zero_or_negative():
    with pytest.raises(DiscountError):
        apply_discount(100.0, 0.0)
    with pytest.raises(DiscountError):
        apply_discount(100.0, -0.2)


def test_apply_discount_rejects_above_one():
    with pytest.raises(DiscountError, match="multiplier"):
        apply_discount(100.0, 1.2)


# ---------------------------------------------------------------------------
# discount_manifest
# ---------------------------------------------------------------------------

def _manifest():
    return {
        "schema_version": "1.1",
        "currency": "USD",
        "price_basis": "retail",
        "totals": {
            "monthly_cost_current_usd": 1000.0,
            "monthly_cost_recommended_usd": 800.0,
            "monthly_savings_potential_usd": 200.0,
        },
    }


def test_discount_manifest_sets_price_basis():
    out = discount_manifest(_manifest(), basis="ea", multiplier=0.85)
    assert out["price_basis"] == "ea"
    assert out["discount"]["multiplier"] == 0.85
    assert out["discount"]["basis"] == "ea"
    assert out["discount"]["applied"] is True


def test_discount_manifest_keeps_retail_and_adds_discounted():
    out = discount_manifest(_manifest(), basis="ea", multiplier=0.85)
    totals = out["totals"]
    # Retail preserved.
    assert totals["monthly_cost_current_usd"] == pytest.approx(1000.0)
    # Discounted added alongside.
    assert totals["monthly_cost_current_discounted_usd"] == pytest.approx(850.0)
    assert totals["monthly_cost_recommended_discounted_usd"] == pytest.approx(680.0)


def test_discount_manifest_injects_caveat():
    out = discount_manifest(_manifest(), basis="ea", multiplier=0.85)
    caveats = " ".join(out["discount"]["caveats"]).lower()
    assert "estimate" in caveats or "not a quote" in caveats


def test_discount_manifest_rejects_unknown_basis():
    with pytest.raises(DiscountError, match="basis"):
        discount_manifest(_manifest(), basis="spot", multiplier=0.85)


def test_discount_manifest_retail_basis_is_noop_passthrough():
    """basis=retail with multiplier 1.0 must not invent discounted totals."""
    out = discount_manifest(_manifest(), basis="retail", multiplier=1.0)
    assert out["price_basis"] == "retail"
    assert "monthly_cost_current_discounted_usd" not in out["totals"]
    assert out["discount"]["applied"] is False


def test_discount_manifest_does_not_mutate_input():
    original = _manifest()
    discount_manifest(original, basis="ea", multiplier=0.85)
    assert "discount" not in original
    assert "monthly_cost_current_discounted_usd" not in original["totals"]
