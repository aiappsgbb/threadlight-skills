"""
APIM projector.

Math (see references/consumption-formulas.md § APIM):

  Time constants:
    seconds_per_month = hours_per_day * 3600 * days_per_month
    monthly_requests  = min(peak_rps * seconds_per_month, 2_592_000)

  Consumption tier:
    free_grant = 1_000_000 calls
    monthly_cost = max(0, monthly_requests - free_grant) * CONSUMPTION_PRICE_PER_10K / 10_000

  BasicV2 / StandardV2 / Premium:
    monthly_cost = units * tier_price_per_unit_per_hour_usd * 730

Alternatives compared:
  * Consumption (skipped if current tier is Consumption)
  * BasicV2, StandardV2, Premium @ 1 / 2 / 4 units
"""
from __future__ import annotations

from typing import Any

# Hardcoded fallback prices — see APIM pricing page.
TIER_PER_UNIT_HOUR: dict[str, float] = {
    "BasicV2": 0.07,
    "StandardV2": 0.21,
    "Premium": 3.83,
}
FREE_GRANT_CALLS = 1_000_000
CONSUMPTION_PRICE_PER_10K = 0.035  # $3.50 per million = $0.035 per 10k
HOURS_PER_MONTH = 730
MAX_MONTHLY_REQUESTS = 2_592_000   # ≈ 30d × 24h × 3600s sanity cap

_UNIT_LADDER = (1, 2, 4)


def _seconds_per_month(load_profile: dict) -> int:
    if load_profile.get("business_hours_only"):
        return 8 * 3600 * 22
    return 24 * 3600 * 30


def _consumption_cost(monthly_requests: float) -> float:
    return max(0.0, monthly_requests - FREE_GRANT_CALLS) * CONSUMPTION_PRICE_PER_10K / 10_000


def _tier_cost(tier: str, units: int) -> float:
    return units * TIER_PER_UNIT_HOUR.get(tier, 0.0) * HOURS_PER_MONTH


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    tier = current_sku.get("tier", "Consumption")
    capacity = current_sku.get("capacity") or 1

    seconds = _seconds_per_month(load_profile)
    peak_rps = load_profile.get("peak_requests_per_second", 0)
    monthly_requests = min(peak_rps * seconds, MAX_MONTHLY_REQUESTS)

    # -- current-SKU cost -----------------------------------------------------
    if tier == "Consumption":
        current_cost = _consumption_cost(monthly_requests)
        monthly_units: dict[str, Any] = {"requests": monthly_requests}
    else:
        current_cost = _tier_cost(tier, capacity)
        monthly_units = {"tier_unit_hours": capacity * HOURS_PER_MONTH}

    # -- alternatives ---------------------------------------------------------
    alternatives: list[dict[str, Any]] = []

    # Consumption (skip if current is Consumption)
    if tier != "Consumption":
        alt_cost = _consumption_cost(monthly_requests)
        alternatives.append({
            "sku": {
                "name": "Consumption",
                "tier": "Consumption",
                "capacity": None,
                "extra": {},
            },
            "monthly_cost_usd": alt_cost,
            "delta_usd": alt_cost - current_cost,
            "delta_pct": (alt_cost - current_cost) / current_cost if current_cost else 0.0,
            "satisfies_constraints": True,
            "caveats": ["No guaranteed capacity; throughput throttled under burst."],
            "rationale": (
                f"Consumption tier: {FREE_GRANT_CALLS // 1_000_000}M free calls then "
                f"${CONSUMPTION_PRICE_PER_10K * 100:.2f}/M overage."
            ),
        })

    # BasicV2, StandardV2, Premium @ 1 / 2 / 4 units
    for alt_tier in ("BasicV2", "StandardV2", "Premium"):
        for units in _UNIT_LADDER:
            if alt_tier == tier and units == capacity:
                continue  # skip exact current config

            alt_cost = _tier_cost(alt_tier, units)
            alternatives.append({
                "sku": {
                    "name": alt_tier,
                    "tier": alt_tier,
                    "capacity": units,
                    "extra": {},
                },
                "monthly_cost_usd": alt_cost,
                "delta_usd": alt_cost - current_cost,
                "delta_pct": (alt_cost - current_cost) / current_cost if current_cost else 0.0,
                "satisfies_constraints": True,
                "caveats": [],
                "rationale": (
                    f"{alt_tier} × {units} unit(s) at "
                    f"${TIER_PER_UNIT_HOUR[alt_tier]}/unit/hr × {HOURS_PER_MONTH}h."
                ),
            })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": current_cost,
        "monthly_units_consumed": monthly_units,
        "price_source": "fallback",
        "alternatives": alternatives,
    }
