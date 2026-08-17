"""
Per-resource consumption projectors.

Each module under this package exports `project(current_sku, load_profile,
pricing_client) -> {monthly_cost_usd, monthly_units_consumed{}, alternatives[]}`.

The `project_resource()` dispatcher looks up the right module by
`resource["resource_kind"]` and forwards.
"""
from __future__ import annotations

from typing import Any

from . import (
    aca,
    ai_search,
    aoai,
    apim,
    cosmos,
    foundry_hosted_agent,
    observability,
    storage,
)
from . import (
    content_understanding,
    content_contextualization,
    document_intelligence,
    speech,
    embeddings,
    search_agentic,
    search_semantic,
    web_grounding,
)

PROJECTOR_REGISTRY: dict[str, Any] = {
    "Microsoft.CognitiveServices/accounts/deployments": aoai,
    "Microsoft.MachineLearningServices/workspaces": foundry_hosted_agent,
    "Microsoft.App/containerApps": aca,
    "Microsoft.DocumentDB/databaseAccounts": cosmos,
    "Microsoft.Storage/storageAccounts": storage,
    "Microsoft.ApiManagement/service": apim,
    "Microsoft.Search/searchServices": ai_search,
    "Microsoft.OperationalInsights/workspaces": observability,
}

# Consumption-meter projectors, keyed by the exact METER_KIND each declares.
# Every one of the eight new meter modules is registered here; a detected meter
# with no entry is emitted as not-priceable (reason 'no projector registered'),
# never silently dropped.
METER_PROJECTOR_REGISTRY: dict[str, Any] = {
    module.METER_KIND: module
    for module in (
        content_understanding,
        content_contextualization,
        document_intelligence,
        speech,
        embeddings,
        search_agentic,
        search_semantic,
        web_grounding,
    )
}


class UnsupportedResourceKind(RuntimeError):
    pass


def project_resource(
    resource: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    kind = resource.get("resource_kind")
    projector = PROJECTOR_REGISTRY.get(kind)
    if projector is None:
        raise UnsupportedResourceKind(
            f"no projector registered for resource_kind={kind!r}; "
            f"v1 supports: {sorted(PROJECTOR_REGISTRY)}"
        )
    projection = projector.project(
        current_sku=resource["current_sku"],
        load_profile=load_profile,
        pricing_client=pricing_client,
    )
    # Preserve discover() metadata so the emitter can render per-resource
    # sections without a second lookup.
    return {
        "resource_kind": kind,
        "resource_id": resource.get("resource_id"),
        "logical_name": resource.get("logical_name"),
        "region": resource.get("region"),
        **projection,
    }


def project_meter_demand(
    demand: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    """Dispatch a meter demand to its registered projector.

    An unregistered ``meter_kind`` is NOT dropped: it is returned as a
    ``not-priceable`` line with reason ``'no projector registered'`` so the cost
    engine still sees (and reports) the coverage gap.
    """
    meter_kind = demand.get("meter_kind")
    module = METER_PROJECTOR_REGISTRY.get(meter_kind)
    if module is None:
        return {
            "meter_kind": meter_kind,
            "source": demand.get("source"),
            "driver": demand.get("volume_driver")
            or {"unit": None, "monthly_quantity": None},
            "selector": demand.get("selector") or {},
            "verified": bool(demand.get("verified", False)),
            "unit_price_usd": None,
            "price_unit": None,
            "price_source": "fallback",
            "pricing_status": "not-priceable",
            "monthly_cost_usd": None,
            "reason": "no projector registered",
            "alternatives": [],
        }
    return module.project(demand, pricing_client)
