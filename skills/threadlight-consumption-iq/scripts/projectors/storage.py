"""
Storage account projector.

Math (see references/consumption-formulas.md § Storage):

  stored_gb_avg  = storage_gb_year_one / 2   (linear-fill assumption)
  monthly_cost   = stored_gb_avg * tier_price_per_gb_month_usd[(redundancy, access_tier)]
                 + monthly_transactions * STORAGE_TXN_PRICE_PER_10K / 10_000
                 + monthly_egress_gb * STORAGE_EGRESS_PRICE_PER_GB

Alternatives compared:
  * {LRS, ZRS, GRS} × {hot, cool, cold, archive} minus current configuration.
  * Archive alternatives: satisfies_constraints=False unless workload_class == "batch".

Constraint hook (min_redundancy):
  * Alternatives populate sku.redundancy so recommender._satisfies() can apply
    the min_redundancy constraint declared in load_profile.declared_constraints.
"""
from __future__ import annotations

from typing import Any

# Hardcoded fallback price matrix keyed on (redundancy, access_tier).
# pricing_client is available for live prices but v1 falls through to these.
STORAGE_PRICE_PER_GB_MONTH: dict[tuple[str, str], float] = {
    ("LRS", "hot"): 0.0184,
    ("ZRS", "hot"): 0.023,
    ("GRS", "hot"): 0.0368,
    ("LRS", "cool"): 0.01,
    ("ZRS", "cool"): 0.0125,
    ("GRS", "cool"): 0.02,
    ("LRS", "cold"): 0.0036,
    ("LRS", "archive"): 0.00099,
}
STORAGE_TXN_PRICE_PER_10K = 0.004
STORAGE_EGRESS_PRICE_PER_GB = 0.0  # first 100 GB free; v2 tiered

_REDUNDANCIES = ("LRS", "ZRS", "GRS")
_ACCESS_TIERS = ("hot", "cool", "cold", "archive")


def _compute_cost(
    stored_gb_avg: float,
    transactions: float,
    egress_gb: float,
    redundancy: str,
    access_tier: str,
) -> float:
    gb_price = STORAGE_PRICE_PER_GB_MONTH.get((redundancy, access_tier))
    if gb_price is None:
        # Combination not in matrix (e.g. ZRS cold, GRS cold, GRS archive):
        # fall back to the same redundancy's hot price as a safe over-estimate.
        gb_price = STORAGE_PRICE_PER_GB_MONTH.get((redundancy, "hot"), 0.023)
    return (
        stored_gb_avg * gb_price
        + transactions * STORAGE_TXN_PRICE_PER_10K / 10_000
        + egress_gb * STORAGE_EGRESS_PRICE_PER_GB
    )


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    extra = current_sku.get("extra") or {}
    storage_gb_year_one = load_profile.get("storage_gb_year_one", 0)
    stored_gb_avg = storage_gb_year_one / 2  # linear-fill assumption; v2 will allow override

    # NOTE: v1 defaults; v2 will pull from observed metrics.
    monthly_transactions: float = extra.get("monthly_transactions", 100_000)
    monthly_egress_gb: float = extra.get("monthly_egress_gb", 50)

    redundancy: str = extra.get("redundancy") or current_sku.get("redundancy") or "LRS"
    access_tier: str = extra.get("access_tier", "hot")

    workload_class = load_profile.get("workload_class", "")

    current_cost = _compute_cost(
        stored_gb_avg, monthly_transactions, monthly_egress_gb, redundancy, access_tier
    )

    # -- alternatives: {LRS, ZRS, GRS} × {hot, cool, cold, archive} minus current --
    alternatives: list[dict[str, Any]] = []
    for red in _REDUNDANCIES:
        for tier in _ACCESS_TIERS:
            if red == redundancy and tier == access_tier:
                continue  # skip current configuration

            # Skip combos absent from the price matrix (ZRS/GRS cold, GRS archive…)
            if (red, tier) not in STORAGE_PRICE_PER_GB_MONTH:
                continue

            alt_cost = _compute_cost(
                stored_gb_avg, monthly_transactions, monthly_egress_gb, red, tier
            )

            satisfies = True
            caveats: list[str] = []
            if tier == "archive":
                if workload_class != "batch":
                    satisfies = False
                    caveats.append("Archive tier has hour-scale retrieval latency.")

            alternatives.append({
                "sku": {
                    "name": f"Standard_{red}",
                    "tier": "Standard",
                    "redundancy": red,   # required by recommender min_redundancy check
                    "extra": {"access_tier": tier, "redundancy": red},
                },
                "monthly_cost_usd": alt_cost,
                "delta_usd": alt_cost - current_cost,
                "delta_pct": (alt_cost - current_cost) / current_cost if current_cost else 0.0,
                "satisfies_constraints": satisfies,
                "caveats": caveats,
                "rationale": (
                    f"{red} {tier} storage at "
                    f"${STORAGE_PRICE_PER_GB_MONTH[(red, tier)]}/GB/month."
                ),
            })

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": current_cost,
        "monthly_units_consumed": {
            "stored_gb_avg": stored_gb_avg,
            "transactions": monthly_transactions,
            "egress_gb": monthly_egress_gb,
        },
        "price_source": "fallback",
        "alternatives": alternatives,
    }
