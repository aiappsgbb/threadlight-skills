"""
Shared consumption-meter projector (`project_usage_meter`).

Every meter projector under this package is a thin module that declares an exact
``METER_KIND`` and delegates here. The contract:

  * request the price via ``PricingClient.get_meter_price(meter_kind, selector)``;
  * ``unit_price_usd is None`` → ``pricing_status='not-priceable'``,
    ``monthly_cost_usd=None``, carry ``driver`` / ``source`` / ``reason`` and an
    empty ``alternatives`` list — **never** invent a rate or bill $0;
  * otherwise ``pricing_status='priced'`` and
    ``monthly_cost_usd = quantity / divisor * unit_price``;
  * a selected meter with no volume evidence (``monthly_quantity is None``) stays
    ``verified=False`` with ``monthly_cost_usd=None`` — priceable but not
    verified, so the total is still incomplete.
"""
from __future__ import annotations

from typing import Any


def project_usage_meter(
    demand: dict[str, Any],
    pricing_client: Any,
    meter_kind: str,
) -> dict[str, Any]:
    selector = demand.get("selector") or {}
    driver = demand.get("volume_driver") or {"unit": None, "monthly_quantity": None}
    quantity = driver.get("monthly_quantity")
    source = demand.get("source")
    verified = bool(demand.get("verified", quantity is not None))

    price_env = pricing_client.get_meter_price(meter_kind, selector)
    unit_price = price_env.get("unit_price_usd")

    line: dict[str, Any] = {
        "meter_kind": meter_kind,
        "source": source,
        "driver": driver,
        "selector": selector,
        "verified": verified,
        "unit_price_usd": unit_price,
        "price_unit": price_env.get("unit"),
        "price_source": price_env.get("price_source"),
        "alternatives": [],
    }

    if unit_price is None:
        line["pricing_status"] = "not-priceable"
        line["monthly_cost_usd"] = None
        line["reason"] = price_env.get("error") or f"no rate for meter {meter_kind!r}"
        return line

    line["pricing_status"] = "priced"
    if quantity is None:
        # Priceable, but volume unknown → cannot compute a cost; keep it not
        # verified so the engine marks the total incomplete.
        line["monthly_cost_usd"] = None
        line["reason"] = demand.get("reason") or "no volume evidence for this meter"
        return line

    divisor = price_env.get("quantity_divisor", 1) or 1
    line["monthly_cost_usd"] = round(quantity / divisor * unit_price, 6)
    return line
