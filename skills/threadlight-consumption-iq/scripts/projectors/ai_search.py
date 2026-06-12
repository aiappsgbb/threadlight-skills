"""
Azure AI Search projector.

Math (see references/consumption-formulas.md § AI Search):

  monthly_cost = sku_unit_price_per_hour_usd * replicas * partitions * 730
               + image_extraction_ops * extraction_price_per_1k_usd / 1_000        # if used
               + semantic_ranker_ops * semantic_price_per_1k_usd / 1_000           # if used

Alternatives compared:
  * Free / Basic / S1 / S2 / S3
  * Replica × partition sweep within the tier (1×1, 2×1, 2×2, 3×3) capped
    at sku limits
"""
from __future__ import annotations

from typing import Any


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    # TODO(projectors): ai_search.project — see references/consumption-formulas.md § AI Search.
    raise NotImplementedError("ai_search.project is scaffolded but not yet implemented")
