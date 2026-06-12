"""
AOAI model deployment projector.

Math (see references/consumption-formulas.md § AOAI for citations):

  monthly_input_tokens  = peak_rps * 3600 * hours_per_day * days_per_month * input_share
  monthly_output_tokens = peak_rps * 3600 * hours_per_day * days_per_month * output_share

  PAYG cost = (input_tokens/1k * input_price_per_1k_usd)
            + (output_tokens/1k * output_price_per_1k_usd)
  PTU cost  = ptu_units * ptu_price_per_unit_month_usd

Alternatives compared:
  * PAYG (current model)
  * PTU @ {1, 4, 10, 25, 50, 100} units
  * Same-model in alternative region (e.g. eastus2 -> sweden)
  * Model swap: gpt-4o ↔ gpt-4o-mini
"""
from __future__ import annotations

from typing import Any


PTU_UNIT_LADDER = (1, 4, 10, 25, 50, 100)
ALTERNATIVE_REGIONS = ("eastus2", "swedencentral", "northcentralus")
MODEL_SWAPS = {
    "gpt-4o": "gpt-4o-mini",
    "gpt-4o-mini": "gpt-4o",
}


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): compute monthly_input/output tokens from load_profile.
    # TODO(projectors): price current_sku via pricing_client.get_price().
    # TODO(projectors): for each alternative (PTU ladder, region swap, model swap),
    #                   price and compute monthly_cost_usd + satisfies_constraints.
    # TODO(projectors): return shape:
    #     {
    #       "current_sku": current_sku,
    #       "monthly_cost_usd": float,
    #       "monthly_units_consumed": {"input_tokens": int, "output_tokens": int},
    #       "price_source": "live" | "fixture" | "fallback",
    #       "alternatives": [{
    #         "sku": {...},
    #         "monthly_cost_usd": float,
    #         "delta_usd": float,
    #         "delta_pct": float,
    #         "satisfies_constraints": bool,
    #         "caveats": [str, ...]
    #       }, ...]
    #     }
    raise NotImplementedError(
        "aoai.project is scaffolded but not yet implemented; "
        "see todos 'projectors' in plan.md and references/consumption-formulas.md § AOAI"
    )
