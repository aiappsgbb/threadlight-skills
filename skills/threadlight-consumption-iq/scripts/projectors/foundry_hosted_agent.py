"""
Foundry hosted-agent tier projector.

Math (see references/consumption-formulas.md § Foundry hosted-agent):

  agent_message_count = peak_concurrent_sessions * avg_requests_per_session * days_per_month
  monthly_cost = tier_base_per_month + agent_message_count * per_message_price_usd

Alternatives compared: all other tiers (Free, Standard, Premium).

Contract decisions:
  * pricing_client is called for per-message price. If unavailable (unit_price_usd=None),
    the hardcoded TIER_PER_MESSAGE fallback is used. The tier base fee is always hardcoded
    because the Azure Retail Prices API returns per-message rates, not the flat base fee.
  * price_source="fallback" when hardcoded table is used for per-message price.
"""
from __future__ import annotations

from typing import Any


_FHA_KIND = "Microsoft.MachineLearningServices/workspaces"

# NOTE: Hardcoded tier prices as fallback when the live pricing API is unavailable.
# Source: https://learn.microsoft.com/en-us/azure/ai-foundry/
# The base fee is always hardcoded (not returned by the retail prices API meter dimension).
# Verified 2026-06-12; update if Microsoft changes these tiers.
TIER_BASE_PER_MONTH: dict[str, float] = {"Free": 0.0, "Standard": 0.0, "Premium": 200.0}
TIER_PER_MESSAGE: dict[str, float] = {"Free": 0.0, "Standard": 0.0012, "Premium": 0.001}

TIERS = ("Free", "Standard", "Premium")

_SOURCE_RANK: dict[str, int] = {"live": 0, "fixture": 1, "fallback": 2}


def _worst_source(*sources: str) -> str:
    return max(sources, key=lambda s: _SOURCE_RANK.get(s, 2))


def _price_tier(
    tier: str,
    region: str,
    agent_message_count: float,
    pricing_client: Any,
) -> tuple[float, str]:
    """Return (monthly_cost_usd, price_source) for a given tier."""
    env = pricing_client.get_price(_FHA_KIND, {"tier": tier, "region": region})
    pp = env.get("unit_price_usd")
    src = env.get("price_source", "fallback")

    if pp is not None:
        per_message = pp
    else:
        # Fall back to hardcoded table when live/fixture unavailable.
        per_message = TIER_PER_MESSAGE[tier]
        src = "fallback"

    base = TIER_BASE_PER_MONTH[tier]
    cost = base + agent_message_count * per_message
    return (cost, src)


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    business_hours_only = load_profile.get("business_hours_only", False)
    days_per_month = 22 if business_hours_only else 30

    peak_concurrent_sessions = load_profile.get("peak_concurrent_sessions", 1)
    avg_requests_per_session = load_profile.get("avg_requests_per_session", 1)

    # Formula: peak_concurrent_sessions * avg_requests_per_session * days_per_month
    # (derived from consumption-formulas.md: ... * (days_per_month/30) * 30 = days_per_month)
    agent_message_count = peak_concurrent_sessions * avg_requests_per_session * days_per_month

    tier = current_sku.get("tier", "Standard")
    region = current_sku.get("region", "eastus2")

    current_cost, price_src = _price_tier(tier, region, agent_message_count, pricing_client)
    monthly_units_consumed = {"agent_messages": int(agent_message_count)}

    # --- Alternatives: all non-current tiers ---
    alternatives: list[dict[str, Any]] = []
    for alt_tier in TIERS:
        if alt_tier == tier:
            continue
        alt_cost, _ = _price_tier(alt_tier, region, agent_message_count, pricing_client)
        delta = alt_cost - current_cost
        delta_pct = delta / current_cost if current_cost else 0.0
        direction = "savings" if delta < 0 else "cost increase"
        alternatives.append({
            "sku": {
                "name": "hosted-agent",
                "tier": alt_tier,
                "region": region,
                "capacity": None,
                "extra": {},
            },
            "monthly_cost_usd": round(alt_cost, 4),
            "delta_usd": round(delta, 4),
            "delta_pct": round(delta_pct, 6),
            "satisfies_constraints": True,
            "rationale": (
                f"Switch to {alt_tier} tier for {abs(delta_pct) * 100:.0f}% {direction}."
            ),
            "caveats": [
                f"{alt_tier} tier feature set differs from {tier}; "
                "verify SLA, quota limits, and included capabilities."
            ],
        })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": round(current_cost, 4),
        "monthly_units_consumed": monthly_units_consumed,
        "price_source": price_src,
        "alternatives": alternatives,
    }
