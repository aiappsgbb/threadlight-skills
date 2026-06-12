"""
APIM projector.

Math (see references/consumption-formulas.md § APIM):

  Consumption tier:
    monthly_cost = max(0, calls - free_grant) * consumption_price_per_10k_calls / 10_000

  Basic v2 / Standard v2 / Premium:
    monthly_cost = tier_units * tier_price_per_unit_per_hour_usd * 730

Alternatives compared:
  * Consumption ↔ Basic v2 ↔ Standard v2 ↔ Premium
  * For Premium, also compare 1 vs 2 vs 4 unit configurations
"""
from __future__ import annotations

from typing import Any


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): apim.project — see references/consumption-formulas.md § APIM.
    raise NotImplementedError("apim.project is scaffolded but not yet implemented")
