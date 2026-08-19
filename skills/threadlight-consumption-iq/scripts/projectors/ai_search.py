"""
Azure AI Search projector.

Math (see references/consumption-formulas.md § AI Search):

  monthly_cost = sku_unit_price_per_hour_usd * replicas * partitions * 730
               # TODO(v2): add image_extraction_ops and semantic_ranker_ops pricing.

Alternatives compared:
  * free, basic, S1, S2, S3 — each at replica×partition sweeps (1×1, 2×1, 2×2, 3×3).

Constraint hook (ai_search_documents):
  * Alternatives where tier_doc_cap < load_profile["ai_search_documents"] are
    marked satisfies_constraints=False with an explanatory caveat.
"""
from __future__ import annotations

from typing import Any

from ._fallback import section as _section

# Fallback per-hour prices sourced from the dated projectors/fallback-rates.json.
TIER_PER_HOUR: dict[str, float] = _section("ai_search")["tier_per_hour"]
TIER_DOC_CAP: dict[str, int] = {
    "free": 10_000,
    "basic": 1_000_000,
    "S1": 15_000_000,
    "S2": 60_000_000,
    "S3": 120_000_000,
}
HOURS_PER_MONTH = 730
# Replica × partition sweep per tier per the spec.
REPLICA_PARTITION_SWEEPS = [(1, 1), (2, 1), (2, 2), (3, 3)]


def _compute_cost(tier: str, replicas: int, partitions: int) -> float:
    # TODO(v2): add image_extraction_ops and semantic_ranker_ops pricing.
    return TIER_PER_HOUR.get(tier, 0.0) * replicas * partitions * HOURS_PER_MONTH


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    tier = current_sku.get("tier", "basic")
    capacity = current_sku.get("capacity", 1)  # replicas * partitions from discover
    extra = current_sku.get("extra") or {}

    # NOTE: capacity = replicas * partitions from discover(); if the exact split
    # is not stored in extra, assume 1 replica and capacity partitions.
    replicas: int = extra.get("replicas", 1)
    partitions: int = extra.get("partitions", capacity)

    ai_search_docs: int = load_profile.get("ai_search_documents", 0)

    # Serverless tier is billed on consumption, not replica×partition hours.
    # There is no fixed fallback rate for it — if the retail rate is unavailable
    # we return not-priceable rather than guessing a number.
    if str(tier).lower() == "serverless":
        env = pricing_client.get_price(
            "Microsoft.Search/searchServices",
            {"name": "serverless", "tier": "serverless", "region": current_sku.get("region", "eastus2")},
        )
        unit_price = env.get("unit_price_usd")
        if unit_price is None:
            return {
                "current_sku": current_sku,
                "monthly_cost_usd": None,
                "pricing_status": "not-priceable",
                "monthly_units_consumed": {"indexed_documents": ai_search_docs},
                "price_source": env.get("price_source", "fallback"),
                "reason": "AI Search serverless retail rate unavailable — no guessed rate.",
                "alternatives": [],
            }
        # A serverless rate exists: bill it per the declared query volume.
        monthly_requests = load_profile.get("search_requests_per_month", 0)
        serverless_cost = monthly_requests * unit_price
        return {
            "current_sku": current_sku,
            "monthly_cost_usd": round(serverless_cost, 4),
            "pricing_status": "priced",
            "monthly_units_consumed": {
                "search_requests": monthly_requests,
                "indexed_documents": ai_search_docs,
            },
            "price_source": env.get("price_source", "fixture"),
            "alternatives": [],
        }

    current_cost = _compute_cost(tier, replicas, partitions)

    # -- alternatives: all tiers × (r, p) sweeps minus exact current config --
    alternatives: list[dict[str, Any]] = []
    for alt_tier in TIER_PER_HOUR:
        for (r, p) in REPLICA_PARTITION_SWEEPS:
            if alt_tier == tier and r == replicas and p == partitions:
                continue  # skip exact current configuration

            alt_cost = _compute_cost(alt_tier, r, p)
            tier_cap = TIER_DOC_CAP.get(alt_tier, 0)

            satisfies = True
            caveats: list[str] = []
            if tier_cap < ai_search_docs:
                satisfies = False
                caveats.append(
                    f"Tier doc cap ({tier_cap:,}) below declared "
                    f"ai_search_documents ({ai_search_docs:,})."
                )

            alternatives.append({
                "sku": {
                    "name": alt_tier,
                    "tier": alt_tier,
                    "capacity": r * p,
                    "extra": {"replicas": r, "partitions": p},
                },
                "monthly_cost_usd": alt_cost,
                "delta_usd": alt_cost - current_cost,
                "delta_pct": (alt_cost - current_cost) / current_cost if current_cost else 0.0,
                "satisfies_constraints": satisfies,
                "caveats": caveats,
                "rationale": (
                    f"{alt_tier} tier × {r}R × {p}P at "
                    f"${TIER_PER_HOUR[alt_tier]}/hr."
                ),
            })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": current_cost,
        "monthly_units_consumed": {
            "replica_partition_hours": replicas * partitions * HOURS_PER_MONTH,
            "indexed_documents": ai_search_docs,
        },
        "price_source": "fallback",
        "alternatives": alternatives,
    }
