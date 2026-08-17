"""
Stable, no-discovery cost projection API (`cost_api.py`).

This is the single projection entry point shared by:

  * the ``estimate --from-profile`` CLI path (post-Bicep-free presales), and
  * the Cowork-safe ``threadlight-qualify`` skill.

Neither caller touches Azure / Bicep / azd — they hand in a fully-declared
``load_profile``, a resource topology, and meter ``selectors``. The API:

  1. discovers normalized meter demands (``meter_demand.discover_meter_demands``);
  2. projects every resource (v1 per-resource projectors) and every meter demand
     (the eight meter projectors);
  3. assembles a **vNext 2.0** cost manifest that stays backward-readable for the
     v1 consumers (``schema_version``, ``generated_at``, ``resources``,
     ``recommendations``, ``totals``) while adding the meter coverage + PTU
     scenario surfaces.

Incomplete-total invariant (never sum unknown as zero):
  * a resource/meter line with no cost (``not-priceable`` rate, or selected but
    unverified volume) forces ``status='partial'``, ``totals.complete=False`` and
    ``cost_per_transaction_usd=None``;
  * only a fully priced + verified projection exposes a per-transaction cost.

Stdlib only, no network, deterministic given a pinned ``generated_at``.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from meter_demand import discover_meter_demands  # noqa: E402
from projectors import project_meter_demand, project_resource  # noqa: E402

COST_MANIFEST_SCHEMA_VERSION = "2.0"
COST_MANIFEST_SCHEMA_ID = "threadlight.cost-manifest/v2"


def _require_positive_number(name: str, value: Any) -> float:
    """Return ``value`` as a float, or raise ``ValueError`` if it is not a
    finite, strictly-positive real number.

    Guards a cost/PTU denominator before any division: booleans (``True`` is an
    ``int`` in Python), strings and other non-numerics, non-finite floats
    (``nan``/``inf``) and non-positive values are all rejected up front so a
    later projection never divides by a bogus quantity or presents a nonsensical
    per-transaction figure.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive number, not a bool (got {value!r})")
    if not isinstance(value, (int, float)):
        raise ValueError(
            f"{name} must be a positive number, got {type(value).__name__} ({value!r})"
        )
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {value!r}")
    return float(value)


def project_profile(
    *,
    load_profile: dict[str, Any],
    resources: list[dict[str, Any]] | None,
    selectors: dict[str, Any] | None,
    pricing: Any,
    transaction_unit: str,
    monthly_transactions: float | int | None,
    generated_at: str | None = None,
    model_catalog_path: str | Path | None = None,
) -> dict[str, Any]:
    """Project a fully-declared profile into a vNext cost manifest.

    No discovery of any kind is performed — every input is supplied by the
    caller. When ``model_catalog_path`` is given (e.g. the Cowork-vendored
    ``vendor/model-catalog.json``) it is loaded and attached to the pricing
    client so the same projectors read model-swap comparisons from it.
    Returns the manifest dict (also written to disk by callers).
    """
    resources = resources or []
    selectors = selectors or {}

    # Reject a bogus denominator BEFORE any projection/total is computed. An
    # absent (None) volume stays allowed for backward-compatible manifests — the
    # per-transaction cost is then simply reported as unavailable downstream —
    # but a bool / non-numeric / non-positive value is a hard error.
    if monthly_transactions is not None:
        _require_positive_number("monthly_transactions", monthly_transactions)

    if model_catalog_path is not None:
        from model_catalog import load_model_catalog

        try:
            pricing.model_catalog = load_model_catalog(model_catalog_path)
        except Exception:
            pricing.model_catalog = None

    demands = discover_meter_demands(resources, load_profile, selectors)

    projected_resources = [
        project_resource(resource, load_profile, pricing) for resource in resources
    ]
    meter_lines = [project_meter_demand(demand, pricing) for demand in demands]

    return build_cost_manifest(
        resources=projected_resources,
        meters=meter_lines,
        load_profile=load_profile,
        transaction_unit=transaction_unit,
        monthly_transactions=monthly_transactions,
        pricing=pricing,
        generated_at=generated_at,
    )


def _resource_pricing_status(line: dict[str, Any]) -> str:
    if "pricing_status" in line:
        return line["pricing_status"]
    return "priced" if line.get("monthly_cost_usd") is not None else "not-priceable"


def _line_is_complete(status: str, verified: bool, cost: Any) -> bool:
    return status == "priced" and verified and cost is not None


def build_cost_manifest(
    *,
    resources: list[dict[str, Any]],
    meters: list[dict[str, Any]],
    load_profile: dict[str, Any],
    transaction_unit: str,
    monthly_transactions: float | int | None,
    pricing: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the vNext cost manifest with strict incomplete-total semantics."""
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()

    # Annotate resource lines with an explicit pricing_status (v1 projectors
    # only emit monthly_cost_usd / price_source).
    resource_lines: list[dict[str, Any]] = []
    for line in resources:
        annotated = dict(line)
        annotated["pricing_status"] = _resource_pricing_status(line)
        annotated.setdefault("verified", annotated.get("monthly_cost_usd") is not None)
        resource_lines.append(annotated)

    # Coverage: verification is about *volume evidence*; priceability is about
    # *rate availability*. They are tracked separately (COST-007 keys on both).
    meter_total = len(meters)
    meter_priced = sum(1 for m in meters if m.get("pricing_status") == "priced")
    meter_not_priceable = sum(
        1 for m in meters if m.get("pricing_status") == "not-priceable"
    )
    meter_not_verified = sum(1 for m in meters if not m.get("verified", False))

    any_not_priceable = any(
        _resource_pricing_status(r) == "not-priceable" for r in resources
    ) or meter_not_priceable > 0
    any_not_verified = meter_not_verified > 0

    # A line contributes to the complete bill only if priced + verified + costed.
    known_costs: list[float] = []
    complete = True
    for r in resource_lines:
        status = r["pricing_status"]
        verified = bool(r.get("verified", r.get("monthly_cost_usd") is not None))
        cost = r.get("monthly_cost_usd")
        if _line_is_complete(status, verified, cost):
            known_costs.append(float(cost))
        else:
            complete = False
    for m in meters:
        status = m.get("pricing_status")
        verified = bool(m.get("verified", False))
        cost = m.get("monthly_cost_usd")
        if _line_is_complete(status, verified, cost):
            known_costs.append(float(cost))
        else:
            complete = False

    known_subtotal = round(sum(known_costs), 4)
    if complete:
        monthly_cost_current: float | None = known_subtotal
        cost_per_transaction: float | None = None
        if monthly_transactions:
            cost_per_transaction = round(known_subtotal / float(monthly_transactions), 6)
    else:
        # Never present a complete bill or sum unknowns as zero.
        monthly_cost_current = None
        cost_per_transaction = None

    meter_coverage = {
        "status": "complete" if not any_not_verified else "not-verified",
        "total": meter_total,
        "priced": meter_priced,
        "not_priceable": meter_not_priceable,
        "not_verified": meter_not_verified,
    }

    status = "complete" if complete and not any_not_priceable else "partial"

    manifest: dict[str, Any] = {
        "schema": COST_MANIFEST_SCHEMA_ID,
        "schema_version": COST_MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at,
        "currency": "USD",
        "price_basis": "retail",
        "status": status,
        "transaction_unit": transaction_unit,
        "monthly_transactions": monthly_transactions,
        "resources": resource_lines,
        "meters": meters,
        "recommendations": [],
        "meter_coverage": meter_coverage,
        "totals": {
            "monthly_cost_current_usd": monthly_cost_current,
            "monthly_cost_known_usd": known_subtotal,
            "complete": complete,
            "cost_per_transaction_usd": cost_per_transaction,
        },
    }

    ptu = build_ptu_scenarios(load_profile, pricing)
    if ptu is not None:
        manifest["ptu_scenarios"] = ptu
    return manifest


# ---------------------------------------------------------------------------
# PTU commitment scenarios
# ---------------------------------------------------------------------------

_AOAI_KIND = "Microsoft.CognitiveServices/accounts/deployments"
_HOURS_PER_MONTH = 730
_PTU_ANNUAL_DISCOUNT = 0.15  # one-year commitment vs monthly list (planning assumption)


def build_ptu_scenarios(
    load_profile: dict[str, Any],
    pricing: Any,
) -> dict[str, Any] | None:
    """Return hourly / one-month / one-year PTU commitment scenarios + break-even.

    Uses the PTU per-unit-month retail rate (via ``pricing.get_price``) as the
    anchor and derives the hourly and yearly commitments from it. Returns
    ``None`` when no PTU rate is available (never guesses a rate).
    """
    if pricing is None:
        return None
    model = load_profile.get("ptu_model") or "gpt-4o"
    region = load_profile.get("pinned_region") or load_profile.get("region") or "eastus2"
    # Validate an EXPLICIT ptu_units up front (non-positive / bool / non-numeric
    # => ValueError); an absent value defaults to a single provisioned unit.
    raw_units = load_profile.get("ptu_units")
    if raw_units is None:
        units: float | int = 1
    else:
        validated = _require_positive_number("ptu_units", raw_units)
        # PTU capacity is provisioned in whole units; keep an int when the caller
        # gave a whole number, otherwise preserve the validated fractional value.
        units = int(validated) if validated.is_integer() else validated

    try:
        env = pricing.get_price(
            _AOAI_KIND, {"name": model, "region": region, "tier": "PTU"}
        )
    except Exception:
        return None
    per_ptu_month = env.get("unit_price_usd")
    if per_ptu_month is None:
        return None

    monthly = per_ptu_month * units
    hourly = monthly / _HOURS_PER_MONTH
    yearly_monthly_equivalent = monthly * (1 - _PTU_ANNUAL_DISCOUNT)

    scenarios = [
        {
            "commitment": "hourly",
            "unit_price_usd_per_ptu_hour": round(hourly / units, 6) if units else None,
            "monthly_usd": round(hourly * _HOURS_PER_MONTH, 4),
            "note": "No commitment; billed per PTU-hour.",
        },
        {
            "commitment": "one-month",
            "unit_price_usd_per_ptu_month": round(per_ptu_month, 4),
            "monthly_usd": round(monthly, 4),
            "note": "Monthly reservation.",
        },
        {
            "commitment": "one-year",
            "unit_price_usd_per_ptu_month": round(per_ptu_month * (1 - _PTU_ANNUAL_DISCOUNT), 4),
            "monthly_usd": round(yearly_monthly_equivalent, 4),
            "annual_usd": round(yearly_monthly_equivalent * 12, 4),
            "note": f"One-year commitment applies a {_PTU_ANNUAL_DISCOUNT:.0%} planning discount.",
        },
    ]

    break_even = (
        f"choose PTU when monthly PAYG token cost > ${round(monthly, 2)} "
        f"(= {units} PTU × ${round(per_ptu_month, 2)}/PTU-month); "
        "otherwise PAYG is cheaper."
    )

    return {
        "model": model,
        "region": region,
        "ptu_units": units,
        "scenarios": scenarios,
        "break_even": break_even,
    }
