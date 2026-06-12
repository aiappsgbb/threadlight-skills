"""
Storage account projector.

Math (see references/consumption-formulas.md § Storage):

  monthly_cost = stored_gb * tier_price_per_gb_month_usd[access_tier]
               + transactions * transaction_price_per_10k_usd[access_tier] / 10_000
               + egress_gb * egress_price_per_gb_usd

Where:
  * access_tier ∈ {hot, cool, cold, archive}
  * redundancy ∈ {LRS, ZRS, GRS, GZRS}  (multiplies tier_price)

Alternatives compared:
  * Each (redundancy × access_tier) combination where tier is compatible
    with the declared write/read pattern in load_profile.
"""
from __future__ import annotations

from typing import Any


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): storage.project — see references/consumption-formulas.md § Storage.
    raise NotImplementedError("storage.project is scaffolded but not yet implemented")
