"""
Azure Container Apps projector.

Math (see references/consumption-formulas.md § ACA):

  Consumption tier:
    avg_replicas = max(min_replicas, ceil(peak_rps / rps_per_replica))
    avg_replicas = min(avg_replicas, max_replicas)           # cap at declared max

    total_vcpu_seconds = vcpu * seconds_per_month * avg_replicas
    total_mem_seconds  = memory_gib * seconds_per_month * avg_replicas
    monthly_requests   = peak_rps * seconds_per_month

    billable_vcpu = max(0, total_vcpu_seconds - 180_000)    # free grant
    billable_mem  = max(0, total_mem_seconds  - 360_000)
    billable_req  = max(0, monthly_requests   - 2_000_000)

    monthly_cost = billable_vcpu * vcpu_price_per_second_usd
                 + billable_mem  * mem_price_per_gib_second_usd
                 + billable_req  * request_price_per_million_usd / 1_000_000

  Dedicated tier:
    monthly_cost = workload_profile_price_per_hour * 730    # always-on billing

Alternatives compared:
  * Consumption ↔ Dedicated D4 / D8 / E4 / E8
  * Replica sweep (Consumption only): (1-3), (1-10), (2-20), (3-30)

Contract decisions:
  * current_sku["extra"] must supply: vcpu, memory_gib, min_replicas, max_replicas.
    requests_per_second_per_replica defaults to 10 if absent.
  * Dedicated per-hour prices are always hardcoded (no Azure Retail Prices meter for
    workload profiles at this granularity). price_source="fallback" for Dedicated.
  * Consumption prices are fetched via pricing_client; hardcoded fallback if unavailable.
"""
from __future__ import annotations

import math
from typing import Any


_ACA_KIND = "Microsoft.App/containerApps"

FREE_VCPU_SECONDS = 180_000
FREE_MEM_SECONDS = 360_000
FREE_REQUESTS = 2_000_000

# NOTE: Hardcoded Consumption tier pricing as fallback.
# Source: https://azure.microsoft.com/en-us/pricing/details/container-apps/
# Verified 2026-06-12; update if Azure changes these rates.
CONSUMPTION_PRICES = {
    "vcpu_per_sec": 0.000024,
    "mem_per_gib_sec": 0.000003,
    "req_per_million": 0.40,
}

# NOTE: Dedicated workload profile prices (USD/hour, always-on).
# Source: https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-overview
# Verified 2026-06-12.
DEDICATED_PER_HOUR: dict[str, float] = {"D4": 0.20, "D8": 0.40, "E4": 0.24, "E8": 0.48}
DEDICATED_PROFILES = tuple(DEDICATED_PER_HOUR.keys())

_SOURCE_RANK: dict[str, int] = {"live": 0, "fixture": 1, "fallback": 2}


def _worst_source(*sources: str) -> str:
    return max(sources, key=lambda s: _SOURCE_RANK.get(s, 2))


def _get_consumption_prices(
    region: str,
    pricing_client: Any,
) -> tuple[dict[str, float], str]:
    """
    Fetch Consumption prices from pricing_client.
    Falls back to hardcoded CONSUMPTION_PRICES if any price is unavailable.
    Returns (prices_dict, price_source).
    """
    vcpu_env = pricing_client.get_price(
        _ACA_KIND,
        {"tier": "Consumption", "meter_substring": "vCPU", "region": region},
    )
    mem_env = pricing_client.get_price(
        _ACA_KIND,
        {"tier": "Consumption", "meter_substring": "Memory", "region": region},
    )
    req_env = pricing_client.get_price(
        _ACA_KIND,
        {"tier": "Consumption", "meter_substring": "Requests", "region": region},
    )

    vcpu_p = vcpu_env.get("unit_price_usd")
    mem_p = mem_env.get("unit_price_usd")
    req_p = req_env.get("unit_price_usd")

    prices = {
        "vcpu_per_sec": vcpu_p if vcpu_p is not None else CONSUMPTION_PRICES["vcpu_per_sec"],
        "mem_per_gib_sec": mem_p if mem_p is not None else CONSUMPTION_PRICES["mem_per_gib_sec"],
        "req_per_million": req_p if req_p is not None else CONSUMPTION_PRICES["req_per_million"],
    }

    sources = []
    for env, p in ((vcpu_env, vcpu_p), (mem_env, mem_p), (req_env, req_p)):
        sources.append("fallback" if p is None else env.get("price_source", "fallback"))

    return prices, _worst_source(*sources)


def _compute_consumption_cost(
    load_profile: dict[str, Any],
    current_sku: dict[str, Any],
    prices: dict[str, float],
    min_replicas: int,
    max_replicas: int,
) -> tuple[float, dict[str, Any]]:
    """
    Compute Consumption tier monthly cost for the given replica bounds.
    Returns (monthly_cost_usd, monthly_units_consumed).
    """
    business_hours_only = load_profile.get("business_hours_only", False)
    hours_per_day = 8 if business_hours_only else 24
    days_per_month = 22 if business_hours_only else 30
    seconds_per_month = hours_per_day * 3600 * days_per_month

    peak_rps = load_profile.get("peak_requests_per_second", 1)
    monthly_requests = peak_rps * seconds_per_month

    extra = current_sku.get("extra", {})
    vcpu = extra.get("vcpu", 0.5)
    memory_gib = extra.get("memory_gib", 1.0)
    # NOTE: requests_per_second_per_replica defaults to 10 if not specified in extra.
    rps_per_replica = extra.get("requests_per_second_per_replica", 10)

    if rps_per_replica > 0:
        avg_replicas = max(min_replicas, math.ceil(peak_rps / rps_per_replica))
    else:
        avg_replicas = min_replicas
    avg_replicas = min(avg_replicas, max_replicas)  # cap at declared max

    total_vcpu_seconds = vcpu * seconds_per_month * avg_replicas
    total_mem_seconds = memory_gib * seconds_per_month * avg_replicas

    billable_vcpu = max(0.0, total_vcpu_seconds - FREE_VCPU_SECONDS)
    billable_mem = max(0.0, total_mem_seconds - FREE_MEM_SECONDS)
    billable_req = max(0.0, monthly_requests - FREE_REQUESTS)

    cost = (
        billable_vcpu * prices["vcpu_per_sec"]
        + billable_mem * prices["mem_per_gib_sec"]
        + billable_req * prices["req_per_million"] / 1_000_000
    )

    units: dict[str, Any] = {
        "vcpu_seconds": int(total_vcpu_seconds),
        "memory_gib_seconds": int(total_mem_seconds),
        "requests": int(monthly_requests),
    }

    return cost, units


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    tier = current_sku.get("tier", "Consumption")
    region = current_sku.get("region", "eastus2")
    extra = current_sku.get("extra", {})

    alternatives: list[dict[str, Any]] = []

    if tier == "Consumption":
        min_replicas = extra.get("min_replicas", 1)
        max_replicas = extra.get("max_replicas", 10)
        prices, price_src = _get_consumption_prices(region, pricing_client)
        current_cost, monthly_units_consumed = _compute_consumption_cost(
            load_profile, current_sku, prices, min_replicas, max_replicas
        )

        # Dedicated profile alternatives
        for profile in DEDICATED_PROFILES:
            d_cost = DEDICATED_PER_HOUR[profile] * 730
            delta = d_cost - current_cost
            delta_pct = delta / current_cost if current_cost else 0.0
            alternatives.append({
                "sku": {
                    "name": "Dedicated",
                    "tier": profile,
                    "region": region,
                    "capacity": None,
                    "extra": {},
                },
                "monthly_cost_usd": round(d_cost, 4),
                "delta_usd": round(delta, 4),
                "delta_pct": round(delta_pct, 6),
                "satisfies_constraints": True,
                "rationale": (
                    f"Dedicated {profile} at ${DEDICATED_PER_HOUR[profile]:.2f}/h × 730 h/month."
                ),
                "caveats": [
                    f"Dedicated profile {profile} is always-on; billed continuously.",
                    "Workload profiles require a managed environment.",
                ],
            })

        # Replica min/max sweep — informational, satisfies_constraints=True
        for min_r, max_r in ((1, 3), (1, 10), (2, 20), (3, 30)):
            if min_r == min_replicas and max_r == max_replicas:
                continue
            sweep_cost, _ = _compute_consumption_cost(
                load_profile, current_sku, prices, min_r, max_r
            )
            delta = sweep_cost - current_cost
            delta_pct = delta / current_cost if current_cost else 0.0
            alternatives.append({
                "sku": {
                    "name": "Consumption",
                    "tier": "Consumption",
                    "region": region,
                    "capacity": None,
                    "extra": {"min_replicas": min_r, "max_replicas": max_r},
                },
                "monthly_cost_usd": round(sweep_cost, 4),
                "delta_usd": round(delta, 4),
                "delta_pct": round(delta_pct, 6),
                "satisfies_constraints": True,
                "rationale": f"Replica bounds ({min_r}–{max_r}) change.",
                "caveats": [],
            })

    else:
        # Dedicated tier — per-hour × 730 h/month (always-on)
        d_per_hour = DEDICATED_PER_HOUR.get(tier, 0.20)
        current_cost = d_per_hour * 730
        monthly_units_consumed = {"workload_profile_hours": 730}
        # NOTE: Dedicated prices are always from the hardcoded table; no retail API meter.
        price_src = "fallback"

        # Consumption alternative
        c_prices, _ = _get_consumption_prices(region, pricing_client)
        min_r = extra.get("min_replicas", 1)
        max_r = extra.get("max_replicas", 10)
        cons_cost, _ = _compute_consumption_cost(load_profile, current_sku, c_prices, min_r, max_r)
        delta = cons_cost - current_cost
        delta_pct = delta / current_cost if current_cost else 0.0
        alternatives.append({
            "sku": {
                "name": "Consumption",
                "tier": "Consumption",
                "region": region,
                "capacity": None,
                "extra": {"min_replicas": min_r, "max_replicas": max_r},
            },
            "monthly_cost_usd": round(cons_cost, 4),
            "delta_usd": round(delta, 4),
            "delta_pct": round(delta_pct, 6),
            "satisfies_constraints": True,
            "rationale": "Consumption tier billed on actual usage; may reduce cost for variable load.",
            "caveats": [
                "Cold-start latency increases if min_replicas=0.",
                "Consumption tier shares infrastructure; dedicated provides stronger isolation.",
            ],
        })

        # Other dedicated profiles
        for profile in DEDICATED_PROFILES:
            if profile == tier:
                continue
            p_cost = DEDICATED_PER_HOUR[profile] * 730
            delta = p_cost - current_cost
            delta_pct = delta / current_cost if current_cost else 0.0
            alternatives.append({
                "sku": {
                    "name": "Dedicated",
                    "tier": profile,
                    "region": region,
                    "capacity": None,
                    "extra": {},
                },
                "monthly_cost_usd": round(p_cost, 4),
                "delta_usd": round(delta, 4),
                "delta_pct": round(delta_pct, 6),
                "satisfies_constraints": True,
                "rationale": f"Alternative dedicated profile {profile}.",
                "caveats": [f"Ensure workload fits {profile} vCPU/memory limits."],
            })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": round(current_cost, 4),
        "monthly_units_consumed": monthly_units_consumed,
        "price_source": price_src,
        "alternatives": alternatives,
    }
