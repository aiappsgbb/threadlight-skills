"""
Per-resource consumption projectors.

Each module under this package exports `project(current_sku, load_profile,
pricing_client) -> {monthly_cost_usd, monthly_units_consumed{}, alternatives[]}`.

The `project_resource()` dispatcher looks up the right module by
`resource["resource_kind"]` and forwards.
"""
from __future__ import annotations

from typing import Any

from . import aca, ai_search, aoai, apim, cosmos, foundry_hosted_agent, storage

PROJECTOR_REGISTRY: dict[str, Any] = {
    "Microsoft.CognitiveServices/accounts/deployments": aoai,
    "Microsoft.MachineLearningServices/workspaces": foundry_hosted_agent,
    "Microsoft.App/containerApps": aca,
    "Microsoft.DocumentDB/databaseAccounts": cosmos,
    "Microsoft.Storage/storageAccounts": storage,
    "Microsoft.ApiManagement/service": apim,
    "Microsoft.Search/searchServices": ai_search,
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
