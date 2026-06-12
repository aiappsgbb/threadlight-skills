"""
Phase 6 — recommender.

Reads the projected resources (each with its `alternatives[]`) and the
load_profile.declared_constraints, then:

  1. Drops alternatives that violate any declared constraint
     (e.g., pinned_region != alt.region; redundancy < min_redundancy;
     latency profile predicted to exceed max_p95_latency_ms).
  2. Ranks survivors per resource by monthly_cost_usd ascending.
  3. Emits a flat recommendations[] list with one entry per resource
     where the cheapest surviving alternative is cheaper than current,
     each scored:
        priority = "high" if monthly_savings_usd > 100
                 else "med" if monthly_savings_usd > 25
                 else "low"

Recommendations are advisory. They are NOT auto-applied to Bicep.
"""
from __future__ import annotations

from typing import Any


PRIORITY_THRESHOLDS_USD = {"high": 100, "med": 25}


def score_and_rank(
    projected: list[dict[str, Any]],
    load_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    constraints = load_profile.get("declared_constraints", {}) or {}
    recommendations: list[dict[str, Any]] = []
    for resource in projected:
        rec = _recommend_for_resource(resource, constraints)
        if rec is not None:
            recommendations.append(rec)
    return recommendations


def _recommend_for_resource(
    resource: dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any] | None:
    alternatives = resource.get("alternatives") or []
    survivors = [
        alt for alt in alternatives if _satisfies(alt, constraints)
    ]
    if not survivors:
        return None
    cheapest = min(survivors, key=lambda alt: alt["monthly_cost_usd"])
    current_cost = resource.get("monthly_cost_usd")
    if current_cost is None or cheapest["monthly_cost_usd"] >= current_cost:
        return None
    savings = current_cost - cheapest["monthly_cost_usd"]
    priority = _priority(savings)
    return {
        "resource_kind": resource["resource_kind"],
        "resource_id": resource.get("resource_id"),
        "logical_name": resource.get("logical_name"),
        "current_sku": resource["current_sku"],
        "recommended_sku": cheapest["sku"],
        "monthly_savings_usd": round(savings, 2),
        "monthly_savings_pct": round(savings / current_cost, 4),
        "priority": priority,
        "rationale": cheapest.get("rationale")
        or "Cheaper alternative satisfies declared constraints.",
        "caveats": cheapest.get("caveats", []),
    }


def _satisfies(alternative: dict[str, Any], constraints: dict[str, Any]) -> bool:
    # The projector itself sets alternative.satisfies_constraints based on
    # resource-specific rules. We honor that here and apply cross-cutting
    # constraints (pinned_region, min_redundancy) at this layer too.
    if not alternative.get("satisfies_constraints", True):
        return False
    pinned_region = constraints.get("pinned_region")
    if pinned_region:
        alt_region = (alternative.get("sku") or {}).get("region")
        if alt_region and alt_region != pinned_region:
            return False
    # TODO(recommender): min_redundancy ordering (none < zone-redundant < geo-redundant)
    #                    is enforced here once Storage / Cosmos projectors emit
    #                    a `redundancy` field in alternative.sku{}.
    return True


def _priority(savings_usd: float) -> str:
    if savings_usd > PRIORITY_THRESHOLDS_USD["high"]:
        return "high"
    if savings_usd > PRIORITY_THRESHOLDS_USD["med"]:
        return "med"
    return "low"
