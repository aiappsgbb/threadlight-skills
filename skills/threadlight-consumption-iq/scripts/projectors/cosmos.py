"""
Cosmos DB (NoSQL) projector.

Math (see references/consumption-formulas.md § Cosmos):

  Provisioned throughput:
    monthly_cost = provisioned_ru * ru_price_per_hour_usd * 730
                 + storage_gb * storage_price_per_gb_month_usd

  Serverless:
    monthly_ru_consumed = peak_rps * 3600 * hours_per_day * days_per_month * ru_per_op
    monthly_cost = monthly_ru_consumed * serverless_ru_price_per_million_usd / 1_000_000
                 + storage_gb * storage_price_per_gb_month_usd

  Autoscale:
    monthly_cost = max_ru * autoscale_ru_price_per_hour_usd * 730 * utilization_factor
                 + storage_gb * storage_price_per_gb_month_usd

Alternatives compared:
  * Provisioned @ 1k / 4k / 10k RU
  * Serverless
  * Autoscale @ 1k / 4k / 10k max RU
"""
from __future__ import annotations

from typing import Any


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): cosmos.project — see references/consumption-formulas.md § Cosmos.
    raise NotImplementedError("cosmos.project is scaffolded but not yet implemented")
