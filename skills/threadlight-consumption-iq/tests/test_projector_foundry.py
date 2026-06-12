"""
Tests for projectors/foundry_hosted_agent.py.

All tests use a FakePricing that returns unit_price_usd=None so the projector
falls back to the hardcoded TIER_BASE_PER_MONTH / TIER_PER_MESSAGE tables.
This isolates the formula from any live/fixture pricing changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from projectors.foundry_hosted_agent import (  # noqa: E402
    TIER_BASE_PER_MONTH,
    TIER_PER_MESSAGE,
    project,
)


# ---------------------------------------------------------------------------
# Fake pricing client (always returns None → forces hardcoded fallback)
# ---------------------------------------------------------------------------

class FakePricing:
    def get_price(self, resource_kind: str, sku: dict) -> dict:
        return {"unit_price_usd": None, "price_source": "fallback"}


class LiveFakePricing:
    """Returns a specific per-message price to test live-price branch."""

    def __init__(self, per_message: float = 0.0015):
        self.per_message = per_message

    def get_price(self, resource_kind: str, sku: dict) -> dict:
        return {
            "unit_price_usd": self.per_message,
            "unit": "per-message",
            "price_source": "live",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sku(tier="Standard", region="eastus2"):
    return {
        "name": "hosted-agent",
        "tier": tier,
        "region": region,
        "capacity": None,
        "extra": {},
    }


def _load(sessions=10, requests=8, business_hours=False):
    return {
        "peak_concurrent_sessions": sessions,
        "avg_requests_per_session": requests,
        "business_hours_only": business_hours,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_foundry_standard_baseline():
    """
    Standard tier at moderate load uses hardcoded PAYG-style rates.

    10 sessions × 8 requests × 30 days = 2,400 messages
    Standard base  = $0.00
    Per message    = $0.0012
    Expected cost  = 0 + 2400 × 0.0012 = $2.88
    """
    result = project(_sku("Standard"), _load(sessions=10, requests=8), FakePricing())

    expected_messages = 10 * 8 * 30
    expected_cost = TIER_BASE_PER_MONTH["Standard"] + expected_messages * TIER_PER_MESSAGE["Standard"]

    assert result["monthly_units_consumed"]["agent_messages"] == expected_messages
    assert result["monthly_cost_usd"] == pytest.approx(expected_cost, rel=1e-6)
    assert result["price_source"] == "fallback"


def test_foundry_premium_alternative_when_message_volume_high():
    """
    At high message volume (>1M), Premium tier break-even flips: Standard costs more.

    Break-even: Standard (n × 0.0012) = Premium (200 + n × 0.001) → n = 1,000,000
    At 50 sessions × 1000 requests × 30 days = 1,500,000 messages:
      Standard = 1,500,000 × 0.0012         = $1,800
      Premium  = 200 + 1,500,000 × 0.001    = $1,700  ← cheaper
    """
    result = project(_sku("Standard"), _load(sessions=50, requests=1000), FakePricing())

    messages = result["monthly_units_consumed"]["agent_messages"]
    assert messages == 50 * 1000 * 30

    premium_alt = next(
        (a for a in result["alternatives"] if a["sku"]["tier"] == "Premium"), None
    )
    assert premium_alt is not None, "Premium tier alternative must be present"
    assert premium_alt["delta_usd"] < 0, \
        "Premium should be cheaper than Standard at 1.5M messages/month"
    assert premium_alt["monthly_cost_usd"] == pytest.approx(
        TIER_BASE_PER_MONTH["Premium"] + messages * TIER_PER_MESSAGE["Premium"],
        rel=1e-6,
    )


def test_foundry_alternatives_include_all_other_tiers():
    """Standard current → alternatives must include Free and Premium."""
    result = project(_sku("Standard"), _load(), FakePricing())

    tiers = {a["sku"]["tier"] for a in result["alternatives"]}
    assert "Free" in tiers
    assert "Premium" in tiers
    assert "Standard" not in tiers


def test_foundry_free_tier_is_zero_cost():
    """Free tier always costs $0 regardless of message volume (hardcoded table)."""
    result = project(_sku("Free"), _load(sessions=100, requests=50), FakePricing())
    assert result["monthly_cost_usd"] == pytest.approx(0.0)


def test_foundry_business_hours_reduces_message_count():
    """business_hours_only=True uses 22 days/month instead of 30."""
    result_24_7 = project(_sku(), _load(), FakePricing())
    result_biz = project(_sku(), _load(business_hours=True), FakePricing())

    m_24_7 = result_24_7["monthly_units_consumed"]["agent_messages"]
    m_biz = result_biz["monthly_units_consumed"]["agent_messages"]
    assert m_biz == pytest.approx(m_24_7 * 22 / 30, rel=1e-6)


def test_foundry_live_price_overrides_hardcoded():
    """When pricing_client returns a live per-message price, it must be used."""
    live_per_message = 0.002
    result = project(_sku("Standard"), _load(sessions=10, requests=10), LiveFakePricing(live_per_message))

    messages = 10 * 10 * 30
    expected_cost = TIER_BASE_PER_MONTH["Standard"] + messages * live_per_message
    assert result["monthly_cost_usd"] == pytest.approx(expected_cost, rel=1e-6)
    assert result["price_source"] == "live"
