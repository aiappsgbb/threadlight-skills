"""
AOAI model deployment projector.

Math (see references/consumption-formulas.md § AOAI for citations):

  monthly_input_tokens  = peak_rps * seconds_per_month * avg_tokens * input_share
  monthly_output_tokens = peak_rps * seconds_per_month * avg_tokens * output_share

  PAYG cost = (input_tokens/1k * input_price_per_1k_usd)
            + (output_tokens/1k * output_price_per_1k_usd)
  PTU cost  = ptu_units * ptu_price_per_unit_month_usd

Alternatives compared:
  * PTU @ {1, 4, 10, 25, 50, 100} units (same model, same region)
  * Same-model PAYG in alternative regions (eastus2, swedencentral, northcentralus)
  * Model swap: gpt-4o ↔ gpt-4o-mini (PAYG, same region)

Contract decisions:
  * pricing_client is called with meter_substring="Input"/"Output" (matches fixture schema).
  * price_source reflects the worst source across the two current-SKU pricing calls.
  * If current-SKU price is unavailable (unit_price_usd=None), returns monthly_cost_usd=None
    and a single sentinel alternative in alternatives[] explaining the gap.
"""
from __future__ import annotations

from typing import Any


PTU_UNIT_LADDER = (1, 4, 10, 25, 50, 100)
ALTERNATIVE_REGIONS = ("eastus2", "swedencentral", "northcentralus")
MODEL_SWAPS = {
    "gpt-4o": "gpt-4o-mini",
    "gpt-4o-mini": "gpt-4o",
}

_AOAI_KIND = "Microsoft.CognitiveServices/accounts/deployments"
# Rank used to bubble up the worst source (fallback > fixture > live).
_SOURCE_RANK: dict[str, int] = {"live": 0, "fixture": 1, "fallback": 2}


def _worst_source(*sources: str) -> str:
    return max(sources, key=lambda s: _SOURCE_RANK.get(s, 2))


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    business_hours_only = load_profile.get("business_hours_only", False)
    hours_per_day = 8 if business_hours_only else 24
    days_per_month = 22 if business_hours_only else 30
    seconds_per_month = hours_per_day * 3600 * days_per_month

    peak_rps = load_profile.get("peak_requests_per_second", 1)
    avg_tokens = load_profile.get("avg_tokens_per_request", 1000)

    # io_split: override via current_sku["extra"].io_split; default [0.65, 0.35].
    io_split = current_sku.get("extra", {}).get("io_split", [0.65, 0.35])
    input_share, output_share = io_split[0], io_split[1]

    monthly_requests = peak_rps * seconds_per_month
    monthly_input_tokens = monthly_requests * avg_tokens * input_share
    monthly_output_tokens = monthly_requests * avg_tokens * output_share

    model_name = current_sku["name"]
    region = current_sku.get("region", "eastus2")
    tier = current_sku.get("tier", "PAYG")
    capacity = current_sku.get("capacity")

    # --- Price current SKU ---
    if tier == "PTU":
        ptu_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": model_name, "region": region, "tier": "PTU"},
        )
        pp = ptu_env.get("unit_price_usd")
        price_src = ptu_env.get("price_source", "fallback")
        current_cost = None if pp is None else (capacity or 0) * pp
        monthly_units_consumed: dict[str, Any] = {"ptu_units": capacity}
    else:
        # PAYG (default)
        input_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": model_name, "region": region, "tier": "PAYG", "meter_substring": "Input"},
        )
        output_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": model_name, "region": region, "tier": "PAYG", "meter_substring": "Output"},
        )
        ip = input_env.get("unit_price_usd")
        op = output_env.get("unit_price_usd")
        price_src = _worst_source(
            input_env.get("price_source", "fallback"),
            output_env.get("price_source", "fallback"),
        )
        if ip is None or op is None:
            current_cost = None
        else:
            current_cost = (monthly_input_tokens / 1000) * ip + (monthly_output_tokens / 1000) * op
        monthly_units_consumed = {
            "input_tokens": int(monthly_input_tokens),
            "output_tokens": int(monthly_output_tokens),
        }

    # --- Missing price guard ---
    if current_cost is None:
        return {
            "current_sku": current_sku,
            "monthly_cost_usd": None,
            "monthly_units_consumed": monthly_units_consumed,
            "price_source": "fallback",
            "alternatives": [
                {
                    "sku": {
                        "name": model_name, "tier": tier, "region": region,
                        "capacity": capacity, "extra": {},
                    },
                    "monthly_cost_usd": None,
                    "delta_usd": None,
                    "delta_pct": None,
                    "satisfies_constraints": False,
                    "rationale": "Pricing unavailable for current SKU.",
                    "caveats": [
                        f"No live or fixture price found for {model_name} {tier} in {region}. "
                        "Update pricing fixtures or check model/region availability."
                    ],
                }
            ],
        }

    # --- Alternatives ---
    alternatives: list[dict[str, Any]] = []

    # 1. PTU ladder — same model, same region
    ptu_env = pricing_client.get_price(
        _AOAI_KIND,
        {"name": model_name, "region": region, "tier": "PTU"},
    )
    ptu_pp = ptu_env.get("unit_price_usd")
    if ptu_pp is not None:
        for units in PTU_UNIT_LADDER:
            if tier == "PTU" and units == capacity:
                continue
            alt_cost = units * ptu_pp
            delta = alt_cost - current_cost
            delta_pct = delta / current_cost if current_cost else 0.0
            alternatives.append({
                "sku": {
                    "name": model_name, "tier": "PTU", "region": region,
                    "capacity": units, "extra": {},
                },
                "monthly_cost_usd": round(alt_cost, 4),
                "delta_usd": round(delta, 4),
                "delta_pct": round(delta_pct, 6),
                "satisfies_constraints": True,
                "rationale": f"Crosses PTU break-even at {units} units.",
                "caveats": [
                    "PTU commitment is monthly; no overflow declared.",
                    f"Requires PTU quota in {region}.",
                ],
            })

    # 2. Region alternatives — same model, PAYG, different region
    for alt_region in ALTERNATIVE_REGIONS:
        if alt_region == region:
            continue
        r_in_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": model_name, "region": alt_region, "tier": "PAYG", "meter_substring": "Input"},
        )
        r_out_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": model_name, "region": alt_region, "tier": "PAYG", "meter_substring": "Output"},
        )
        rip = r_in_env.get("unit_price_usd")
        rop = r_out_env.get("unit_price_usd")
        if rip is None or rop is None:
            continue
        alt_cost = (monthly_input_tokens / 1000) * rip + (monthly_output_tokens / 1000) * rop
        delta = alt_cost - current_cost
        delta_pct = delta / current_cost if current_cost else 0.0
        pct_display = abs(delta_pct) * 100
        alternatives.append({
            "sku": {
                "name": model_name, "tier": "PAYG", "region": alt_region,
                "capacity": None, "extra": {},
            },
            "monthly_cost_usd": round(alt_cost, 4),
            "delta_usd": round(delta, 4),
            "delta_pct": round(delta_pct, 6),
            "satisfies_constraints": True,
            "rationale": f"Regional pricing differs by ~{pct_display:.0f}%.",
            "caveats": [
                f"Data residency requirements may prevent region swap to {alt_region}."
            ],
        })

    # 3. Model swap — gpt-4o ↔ gpt-4o-mini (PAYG, same region)
    # satisfies_constraints=True: recommender surfaces these; caveat warns on quality.
    swap_model = MODEL_SWAPS.get(model_name)
    if swap_model:
        sw_in_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": swap_model, "region": region, "tier": "PAYG", "meter_substring": "Input"},
        )
        sw_out_env = pricing_client.get_price(
            _AOAI_KIND,
            {"name": swap_model, "region": region, "tier": "PAYG", "meter_substring": "Output"},
        )
        swip = sw_in_env.get("unit_price_usd")
        swop = sw_out_env.get("unit_price_usd")
        if swip is not None and swop is not None:
            swap_cost = (monthly_input_tokens / 1000) * swip + (monthly_output_tokens / 1000) * swop
            delta = swap_cost - current_cost
            delta_pct = delta / current_cost if current_cost else 0.0
            alternatives.append({
                "sku": {
                    "name": swap_model, "tier": "PAYG", "region": region,
                    "capacity": None, "extra": {},
                },
                "monthly_cost_usd": round(swap_cost, 4),
                "delta_usd": round(delta, 4),
                "delta_pct": round(delta_pct, 6),
                "satisfies_constraints": True,
                "rationale": f"Model swap to {swap_model} may reduce cost; validate quality.",
                "caveats": [
                    f"Swapping from {model_name} to {swap_model} may affect response quality.",
                    "Benchmark both models against your workload before switching.",
                ],
            })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": round(current_cost, 4),
        "monthly_units_consumed": monthly_units_consumed,
        "price_source": price_src,
        "alternatives": alternatives,
    }
