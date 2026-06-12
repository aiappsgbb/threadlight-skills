"""
Phase 1 — discover.

Walks `infra/main.bicep` + `azd env get-values` + `specs/manifest.json`
deployment_manifest{} → normalized list of resource selectors:

    [
      {
        "resource_kind": "Microsoft.CognitiveServices/accounts/deployments",
        "resource_id": "/subscriptions/.../...",      # may be None pre-deploy
        "logical_name": "gpt4o",                       # Bicep symbol
        "current_sku": {
          "name": "gpt-4o",
          "tier": "PAYG",                              # PAYG | PTU | <azure-sku-name>
          "region": "eastus2",
          "capacity": 100,                             # tokens/min for PAYG; PTU units for PTU; vCPU/replica for ACA; etc.
          "extra": { ... }                              # resource-specific (model_version, replicas, etc.)
        },
        "region": "eastus2",
        "source": "bicep" | "azd-env" | "deployment-manifest"
      },
      ...
    ]

When `use_azd_env=False` (the `--pre-deploy` flag), the azd env walk is
skipped and `resource_id` is `None` for every entry. This lets the skill
run against a Bicep-only pre-deploy review.

Implementation strategy (matches threadlight-safe-check):
- Prefer `az bicep build` -> compiled ARM JSON; walk `resources[]`.
  Fall back to a friendly error if `bicep` CLI is missing (no regex
  fallback — see threadlight-production-ready v0.3.0 precedent).
- For each resource, derive (resource_kind, sku, region) using a small
  per-kind extractor table.
- Cross-reference with `specs/manifest.json → deployment_manifest{}` for
  `expected_resource_types` so we can warn on Bicep ↔ manifest drift.
- For live runs, cross-reference with `az resource list -g $AZURE_RG`
  to attach `resource_id` and confirm the resource actually deployed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# Per-kind extractor registry. Each extractor receives the compiled ARM
# resource dict and returns a normalized current_sku dict.
_EXTRACTORS: dict[str, Any] = {}


def register_extractor(resource_kind: str):
    def decorator(fn):
        _EXTRACTORS[resource_kind] = fn
        return fn

    return decorator


def discover_resources(
    bicep_entrypoint: Path,
    deployment_manifest: Path,
    use_azd_env: bool = True,
) -> list[dict[str, Any]]:
    """Walk Bicep + (optionally) azd env + deployment manifest -> normalized list."""
    # TODO(discover): wire `az bicep build` subprocess + walk compiled JSON.
    # TODO(discover): wire `az resource list` cross-check when use_azd_env=True.
    # TODO(discover): cross-reference deployment_manifest.expected_resource_types[]
    #                 and emit warnings (not errors) on drift.
    raise NotImplementedError(
        "discover_resources is scaffolded but not yet implemented; "
        "see todos 'discover' in plan.md"
    )


@register_extractor("Microsoft.CognitiveServices/accounts/deployments")
def _extract_aoai_deployment(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@register_extractor("Microsoft.App/containerApps")
def _extract_aca(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@register_extractor("Microsoft.DocumentDB/databaseAccounts")
def _extract_cosmos(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@register_extractor("Microsoft.Storage/storageAccounts")
def _extract_storage(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@register_extractor("Microsoft.ApiManagement/service")
def _extract_apim(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@register_extractor("Microsoft.Search/searchServices")
def _extract_ai_search(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@register_extractor("Microsoft.MachineLearningServices/workspaces")
def _extract_foundry_hosted_agent(arm_resource: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError
