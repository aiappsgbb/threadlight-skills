"""
Cosmos DB (NoSQL) projector.

Math (see references/consumption-formulas.md § Cosmos):

  Provisioned throughput:
    monthly_cost = provisioned_ru * ru_price_per_hour_usd * 730
                 + cosmos_gb_year_one * storage_price_per_gb_month_usd

  Serverless:
    monthly_requests = peak_rps * seconds_per_month
    monthly_ru_consumed = monthly_requests * ru_per_op
    monthly_cost = monthly_ru_consumed * serverless_ru_price_per_million_usd / 1_000_000
                 + cosmos_gb_year_one * storage_price_per_gb_month_usd

  Autoscale:
    monthly_cost = max_ru * autoscale_ru_price_per_hour_usd * 730 * utilization_factor
                 + cosmos_gb_year_one * storage_price_per_gb_month_usd

Alternatives compared:
  * Provisioned @ 1k / 4k / 10k RU
  * Serverless (skipped if current tier is serverless)
  * Autoscale @ 1k / 4k / 10k max RU
"""
from __future__ import annotations

from typing import Any

# Hardcoded fallback prices — US East 2 baseline.
# pricing_client is available for live prices but v1 projectors fall through
# to these constants when the API is unavailable.
RU_PRICE_PER_HOUR = 0.00008              # provisioned, per RU/s per hour
AUTOSCALE_RU_PRICE_PER_HOUR = 0.00012   # autoscale, per max-RU/s per hour
SERVERLESS_RU_PRICE_PER_MILLION = 0.279 # serverless, per million RU consumed
STORAGE_PRICE_PER_GB_MONTH = 0.25       # transactional storage, per GB/month

AUTOSCALE_UTILIZATION_FACTOR = 0.6      # Microsoft's conservative default
HOURS_PER_MONTH = 730                   # always-on billing constant

PROVISIONED_RU_LADDER = (1000, 4000, 10000)
AUTOSCALE_RU_LADDER = (1000, 4000, 10000)


def _seconds_per_month(load_profile: dict) -> int:
    if load_profile.get("business_hours_only"):
        return 8 * 3600 * 22
    return 24 * 3600 * 30


def _redundancy(current_sku: dict) -> str:
    extra = current_sku.get("extra") or {}
    if extra.get("multi_write"):
        return "zone-redundant"
    return "none"


def _cost_provisioned(ru: int, storage_gb: float) -> float:
    return ru * RU_PRICE_PER_HOUR * HOURS_PER_MONTH + storage_gb * STORAGE_PRICE_PER_GB_MONTH


def _cost_serverless(monthly_requests: float, ru_per_op: int, storage_gb: float) -> float:
    monthly_ru = monthly_requests * ru_per_op
    return monthly_ru * SERVERLESS_RU_PRICE_PER_MILLION / 1_000_000 + storage_gb * STORAGE_PRICE_PER_GB_MONTH


def _cost_autoscale(max_ru: int, storage_gb: float) -> float:
    return (
        max_ru * AUTOSCALE_RU_PRICE_PER_HOUR * HOURS_PER_MONTH * AUTOSCALE_UTILIZATION_FACTOR
        + storage_gb * STORAGE_PRICE_PER_GB_MONTH
    )


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    tier = current_sku.get("tier", "provisioned")
    extra = current_sku.get("extra") or {}

    # cosmos_gb_year_one: prefer explicit cosmos key, fall back to generic storage key.
    storage_gb = load_profile.get("cosmos_gb_year_one") or load_profile.get("storage_gb_year_one", 0)

    seconds = _seconds_per_month(load_profile)
    peak_rps = load_profile.get("peak_requests_per_second", 0)
    monthly_requests = peak_rps * seconds

    redundancy = _redundancy(current_sku)

    # -- compute current-SKU cost ---------------------------------------------
    if tier == "serverless":
        ru_per_op = extra.get("ru_per_op", 5)
        monthly_cost = _cost_serverless(monthly_requests, ru_per_op, storage_gb)
        monthly_units: dict[str, Any] = {
            "ru_consumed": monthly_requests * ru_per_op,
            "storage_gb": storage_gb,
        }
    elif tier == "autoscale":
        max_ru = current_sku.get("capacity", 1000)
        monthly_cost = _cost_autoscale(max_ru, storage_gb)
        monthly_units = {
            "ru_provisioned": max_ru,
            "storage_gb": storage_gb,
        }
    else:  # provisioned (default)
        ru = current_sku.get("capacity", 400)
        monthly_cost = _cost_provisioned(ru, storage_gb)
        monthly_units = {
            "ru_provisioned": ru,
            "storage_gb": storage_gb,
        }

    # -- alternatives ---------------------------------------------------------
    alternatives: list[dict[str, Any]] = []

    # Provisioned @ 1k / 4k / 10k RU
    for ru in PROVISIONED_RU_LADDER:
        alt_cost = _cost_provisioned(ru, storage_gb)
        alternatives.append({
            "sku": {
                "name": "provisioned",
                "tier": "provisioned",
                "capacity": ru,
                "redundancy": redundancy,
                "extra": {"ru": ru},
            },
            "monthly_cost_usd": alt_cost,
            "delta_usd": alt_cost - monthly_cost,
            "delta_pct": (alt_cost - monthly_cost) / monthly_cost if monthly_cost else 0.0,
            "satisfies_constraints": True,
            "caveats": [],
            "rationale": f"Provisioned throughput at {ru:,} RU/s; flat billing.",
        })

    # Serverless (skip if current tier is serverless)
    if tier != "serverless":
        ru_per_op_default = 5
        alt_cost = _cost_serverless(monthly_requests, ru_per_op_default, storage_gb)
        alternatives.append({
            "sku": {
                "name": "serverless",
                "tier": "serverless",
                "capacity": None,
                "redundancy": redundancy,
                "extra": {"ru_per_op": ru_per_op_default},
            },
            "monthly_cost_usd": alt_cost,
            "delta_usd": alt_cost - monthly_cost,
            "delta_pct": (alt_cost - monthly_cost) / monthly_cost if monthly_cost else 0.0,
            "satisfies_constraints": True,
            "caveats": [
                "Serverless is limited to 50 GB storage per container.",
                "No multi-region writes in serverless mode.",
            ],
            "rationale": (
                f"Serverless at ${SERVERLESS_RU_PRICE_PER_MILLION}/M RU; "
                "optimal for variable or low-traffic workloads."
            ),
        })

    # Autoscale @ 1k / 4k / 10k max RU
    for max_ru in AUTOSCALE_RU_LADDER:
        alt_cost = _cost_autoscale(max_ru, storage_gb)
        alternatives.append({
            "sku": {
                "name": "autoscale",
                "tier": "autoscale",
                "capacity": max_ru,
                "redundancy": redundancy,
                "extra": {"max_ru": max_ru},
            },
            "monthly_cost_usd": alt_cost,
            "delta_usd": alt_cost - monthly_cost,
            "delta_pct": (alt_cost - monthly_cost) / monthly_cost if monthly_cost else 0.0,
            "satisfies_constraints": True,
            "caveats": [],
            "rationale": (
                f"Autoscale at {max_ru:,} max RU/s with "
                f"{int(AUTOSCALE_UTILIZATION_FACTOR * 100)}% utilization factor applied."
            ),
        })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": monthly_cost,
        "monthly_units_consumed": monthly_units,
        "price_source": "fallback",
        "alternatives": alternatives,
    }
