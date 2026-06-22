"""
EA / MCA discount multiplier (`discount.py`).

Pre-sales estimates are usually shown net of the customer's enterprise
agreement. This module applies a single discount multiplier to retail figures,
records `price_basis` (retail | ea | mca), and injects a caveat so an internal
planning number is never mistaken for a contractual quote.

Discipline:
  * The retail number is NEVER discarded — both retail and discounted totals are
    kept so a reviewer can see the list price the multiplier was applied to.
  * `basis: retail` (multiplier 1.0) is a no-op: no discounted totals are
    invented, `discount.applied` is false.
  * The multiplier is the seller's own EA assumption — the skill does not look
    up real EA pricing. The caveat says so.

The schema reserved `price_basis: ea | mca` and `discounts: { ea_multiplier }`
for exactly this. Stdlib only.
"""
from __future__ import annotations

import copy
from typing import Any

VALID_BASES = ("retail", "ea", "mca")

_CAVEAT = (
    "Discounted figures apply a flat {pct:.0f}% {basis} multiplier as an "
    "internal planning assumption — they are an ESTIMATE, not a quote. Real "
    "EA/MCA pricing depends on the customer's agreement, commitment, and "
    "negotiated rate card."
)


class DiscountError(RuntimeError):
    """Invalid discount multiplier or price basis."""


def apply_discount(amount: float, multiplier: float) -> float:
    """Scale `amount` by `multiplier`. Multiplier must be in (0, 1]."""
    if not isinstance(multiplier, (int, float)) or multiplier <= 0 or multiplier > 1:
        raise DiscountError(
            f"discount multiplier must be in (0, 1]; got {multiplier!r}"
        )
    return amount * multiplier


def discount_manifest(
    manifest: dict[str, Any],
    basis: str,
    multiplier: float,
) -> dict[str, Any]:
    """Return a copy of `manifest` with discount applied to its totals.

    Retail totals are preserved; `*_discounted_usd` siblings are added. The
    input manifest is never mutated.
    """
    if basis not in VALID_BASES:
        raise DiscountError(
            f"unknown price basis {basis!r}; must be one of {list(VALID_BASES)}"
        )

    out = copy.deepcopy(manifest)
    out["price_basis"] = basis

    applied = not (basis == "retail" and multiplier == 1.0)
    if applied:
        # Validate the multiplier only when we actually apply it.
        multiplier = float(multiplier)
        if multiplier <= 0 or multiplier > 1:
            raise DiscountError(
                f"discount multiplier must be in (0, 1]; got {multiplier!r}"
            )
        totals = out.get("totals") or {}
        for key in list(totals.keys()):
            if key.endswith("_usd") and not key.endswith("_discounted_usd"):
                disc_key = key.replace("_usd", "_discounted_usd")
                totals[disc_key] = round(apply_discount(totals[key], multiplier), 2)
        out["totals"] = totals
        caveats = [_CAVEAT.format(pct=(1 - multiplier) * 100, basis=basis.upper())]
    else:
        caveats = []

    out["discount"] = {
        "basis": basis,
        "multiplier": multiplier,
        "applied": applied,
        "caveats": caveats,
    }
    return out
