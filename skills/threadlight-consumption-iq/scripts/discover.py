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

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


# Per-kind extractor registry. Each extractor receives the compiled ARM
# resource dict and returns a normalized current_sku dict.
_EXTRACTORS: dict[str, Any] = {}

_TEMPLATE_EXPR_RE = re.compile(r"^\[.*\]$", re.DOTALL)
# Matches a quoted string inside a template expression, e.g. 'gpt4o' or "gpt4o".
_QUOTED_IN_EXPR_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def register_extractor(resource_kind: str):
    def decorator(fn):
        _EXTRACTORS[resource_kind] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_template_expr(value: str) -> str:
    """Return a best-effort logical name from an ARM name field.

    If the value is a template expression like `[concat(parameters('prefix'),
    '-myname')]`, extract the last quoted string literal inside it (which is
    usually the human-readable resource suffix). If no quoted string is found,
    return the raw value stripped of outer brackets.

    NOTE: ARM expressions from `az bicep build --stdout` may still contain
    bracket expressions even for symbolic names — we do best-effort here
    rather than evaluating the expression at runtime.
    """
    if not _TEMPLATE_EXPR_RE.match(value):
        return value
    # Extract all quoted literals; the last one is usually the resource-specific suffix.
    matches = _QUOTED_IN_EXPR_RE.findall(value)
    if matches:
        return matches[-1]
    # No quoted string found — strip outer brackets and return.
    return value[1:-1].strip()


def _resolve_location(location: str) -> str:
    """Return the location string, replacing template expressions with a placeholder.

    NOTE: Template expressions like `[resourceGroup().location]` cannot be
    evaluated at static-analysis time. We use "resourceGroup-location" as a
    sentinel so downstream projectors know they need a live lookup.
    # TODO: If needed in a future phase, resolve this via `az group show`.
    """
    if _TEMPLATE_EXPR_RE.match(location):
        return "resourceGroup-location"
    return location or "unknown"


def _as_resource_list(resources) -> list[dict]:
    """Normalize an ARM template `resources` block to a list of resource dicts.

    ARM `resources` comes in two shapes:
      * classic list  — `"resources": [ {...}, {...} ]`
      * symbolic-name object — `"resources": { "fooBar": {...}, ... }`
        which modern Bicep emits for nested module templates
        (languageVersion 2.0). Iterating that dict directly yields the
        symbolic *names* (strings), which then crash on `.get(...)`.
    """
    if isinstance(resources, dict):
        return list(resources.values())
    return resources or []


def _flatten_resources(resources) -> list[dict]:
    """Recursively flatten nested ARM deployment templates (from Bicep modules).

    Accepts either the classic list shape or the symbolic-name object shape
    (see `_as_resource_list`) at every level — Bicep modules compile to nested
    deployments whose inner `template.resources` is frequently the object form.
    """
    out: list[dict] = []
    for r in _as_resource_list(resources):
        rtype = (r.get("type") or "").lower()
        if rtype == "microsoft.resources/deployments":
            # Bicep modules compile to nested deployment resources; recurse.
            nested = (
                (r.get("properties") or {})
                .get("template", {})
                .get("resources")
            )
            out.extend(_flatten_resources(nested))
        else:
            out.append(r)
    return out


def _read_azd_env_values() -> dict[str, str]:
    """Run `azd env get-values` and parse KEY="value" lines into a dict.

    NOTE: Mirrors the subprocess + parsing pattern from threadlight-safe-check's
    `azd env get-value` invocation. Returns {} on any failure — caller decides
    whether to warn.
    """
    try:
        cp = subprocess.run(
            "azd env get-values",
            shell=True,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return {}
    env: dict[str, str] = {}
    # Lines are in the form KEY="value" (azd uses double-quote wrapping).
    for line in cp.stdout.splitlines():
        line = line.strip()
        m = re.match(r'^([A-Z_][A-Z0-9_]*)="?(.*?)"?$', line)
        if m:
            env[m.group(1)] = m.group(2)
    return env


def _get_rg_and_sub(azd_env: dict[str, str]) -> tuple[str | None, str | None]:
    """Resolve AZURE_RESOURCE_GROUP (or AZURE_RG) and AZURE_SUBSCRIPTION_ID.

    Prefers OS environment; falls back to the parsed azd env dict.
    """
    rg = (
        os.environ.get("AZURE_RESOURCE_GROUP")
        or os.environ.get("AZURE_RG")
        or azd_env.get("AZURE_RESOURCE_GROUP")
        or azd_env.get("AZURE_RG")
    )
    sub = (
        os.environ.get("AZURE_SUBSCRIPTION_ID")
        or azd_env.get("AZURE_SUBSCRIPTION_ID")
    )
    return rg or None, sub or None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def discover_resources(
    bicep_entrypoint: Path,
    deployment_manifest: Path,
    use_azd_env: bool = True,
) -> list[dict[str, Any]]:
    """Walk Bicep + (optionally) azd env + deployment manifest -> normalized list."""

    # ------------------------------------------------------------------
    # 1. Verify prerequisites: az CLI. The bicep compiler is invoked below
    # via `az bicep build`, which uses az's own bundled bicep (under
    # ~/.azure/bin) — there is NO requirement for a standalone `bicep`
    # binary on PATH. A genuinely-missing bicep is handled by the
    # `az bicep build` error branch (which prints the `az bicep install`
    # hint), so we deliberately do NOT pre-gate on `shutil.which("bicep")`:
    # doing so spuriously aborted on machines where `az bicep` works fine
    # but no standalone `bicep` is on PATH.
    # ------------------------------------------------------------------
    if shutil.which("az") is None:
        msg = (
            "az CLI not found on PATH; "
            "install Azure CLI from https://learn.microsoft.com/cli/azure/install-azure-cli"
        )
        print(msg, file=sys.stderr)
        raise FileNotFoundError(msg)

    # ------------------------------------------------------------------
    # 2. Compile Bicep → ARM JSON via `az bicep build --stdout`.
    # Mirrors threadlight-production-ready v0.3.0 BicepGraph.from_repo().
    # ------------------------------------------------------------------
    try:
        cp = subprocess.run(
            ["az", "bicep", "build", "--file", str(bicep_entrypoint), "--stdout"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        msg = (
            "az CLI not found on PATH; "
            "install Azure CLI from https://learn.microsoft.com/cli/azure/install-azure-cli"
        )
        print(msg, file=sys.stderr)
        raise FileNotFoundError(msg) from exc

    if cp.returncode != 0:
        stderr_lower = (cp.stderr or "").lower()
        if "bicep cli not found" in stderr_lower or "az bicep install" in stderr_lower:
            msg = (
                "bicep CLI not found; install via `az bicep install` "
                "and re-run the consumption-iq skill"
            )
            print(msg, file=sys.stderr)
            raise FileNotFoundError(msg)
        raise RuntimeError(
            f"az bicep build failed (exit {cp.returncode}): {(cp.stderr or '').strip()[:400]}"
        )

    try:
        arm = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"az bicep build produced non-JSON output: {(cp.stdout or '')[:200]}"
        ) from exc

    # ------------------------------------------------------------------
    # 3. Walk resources[] — flatten nested templates from Bicep modules.
    # ------------------------------------------------------------------
    raw_resources = arm.get("resources")
    arm_resources = _flatten_resources(raw_resources)

    results: list[dict[str, Any]] = []
    for res in arm_resources:
        rtype = res.get("type", "")
        extractor = _EXTRACTORS.get(rtype)
        if extractor is None:
            continue
        try:
            sku = extractor(res)
        except Exception as exc:  # noqa: BLE001
            print(
                f"warning: extractor for {rtype!r} raised {exc!r}; skipping resource",
                file=sys.stderr,
            )
            continue

        logical_name = _strip_template_expr(res.get("name", "") or "")
        region = _resolve_location(res.get("location", "") or "")

        results.append(
            {
                "resource_kind": rtype,
                "resource_id": None,
                "logical_name": logical_name,
                "current_sku": sku,
                "region": region,
                "source": "bicep",
            }
        )

    # ------------------------------------------------------------------
    # 4. Optionally attach resource_id via `az resource list`.
    # Never raises — failures degrade gracefully to resource_id=None.
    # ------------------------------------------------------------------
    if use_azd_env:
        try:
            _attach_resource_ids(results)
        except Exception as exc:  # noqa: BLE001
            print(
                f"warning: azd env walk failed ({exc!r}); continuing with resource_id=None",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # 5. Cross-reference deployment_manifest.expected_resource_types[].
    # Emits warnings (never raises) for drift.
    # ------------------------------------------------------------------
    _check_manifest_drift(results, deployment_manifest)

    return results


def _attach_resource_ids(results: list[dict[str, Any]]) -> None:
    """Query `az resource list` per unique type and best-effort match by name.

    Modifies `results` in-place, setting `resource_id` where a match is found.
    Falls back silently on any subprocess or parse failure.

    NOTE: Bicep symbolic names often map to deployed names as a derived suffix
    (e.g. symbol `gpt4o` → deployed name `gpt-4o-deployment` or similar).
    We do a best-effort suffix match: the deployed resource name endswith the
    logical_name (case-insensitive), or the logical_name contains a substring
    of the deployed name.
    """
    # Read env / azd env for RG and subscription.
    azd_env: dict[str, str] = {}
    rg, sub = _get_rg_and_sub(azd_env)
    if not rg:
        azd_env = _read_azd_env_values()
        rg, sub = _get_rg_and_sub(azd_env)
    if not rg:
        print(
            "warning: AZURE_RESOURCE_GROUP / AZURE_RG not set and `azd env get-values` "
            "did not supply it; skipping resource_id attachment",
            file=sys.stderr,
        )
        return

    # Collect unique resource types that appear in results.
    unique_types: set[str] = {r["resource_kind"] for r in results}

    # For each type, fetch the deployed list once and build a name→id map.
    deployed_map: dict[str, list[dict]] = {}  # rtype → list of {name, id}
    for rtype in unique_types:
        cmd_parts = [
            "az", "resource", "list",
            "-g", rg,
            "--resource-type", rtype,
            "-o", "json",
        ]
        if sub:
            cmd_parts += ["--subscription", sub]
        try:
            cp = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if cp.returncode != 0:
                continue
            items = json.loads(cp.stdout or "[]")
            deployed_map[rtype] = [
                {"name": item.get("name", ""), "id": item.get("id", "")}
                for item in (items if isinstance(items, list) else [])
            ]
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            continue

    # Attach resource_id by fuzzy suffix match.
    for entry in results:
        rtype = entry["resource_kind"]
        logical = (entry.get("logical_name") or "").lower()
        candidates = deployed_map.get(rtype, [])
        for cand in candidates:
            cname = (cand.get("name") or "").lower()
            # Match if deployed name ends with logical name, or vice-versa,
            # or one is a substring of the other.
            if (
                cname.endswith(logical)
                or logical.endswith(cname)
                or logical in cname
                or cname in logical
            ):
                entry["resource_id"] = cand.get("id") or None
                break


def _check_manifest_drift(
    results: list[dict[str, Any]],
    deployment_manifest: Path,
) -> None:
    """Warn on types listed in deployment_manifest but absent from Bicep results."""
    try:
        data = json.loads(Path(deployment_manifest).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        print(
            f"warning: could not read deployment_manifest at {deployment_manifest}: {exc!r}",
            file=sys.stderr,
        )
        return

    dm = data.get("deployment_manifest") or data
    expected_types: list[str] = dm.get("expected_resource_types") or []
    found_kinds = {r["resource_kind"] for r in results}

    for expected in expected_types:
        if expected not in found_kinds:
            print(
                f"drift warning: expected {expected!r} not found in compiled Bicep",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Per-kind extractors
# ---------------------------------------------------------------------------


@register_extractor("Microsoft.CognitiveServices/accounts/deployments")
def _extract_aoai_deployment(arm_resource: dict[str, Any]) -> dict[str, Any]:
    props = arm_resource.get("properties") or {}
    sku = arm_resource.get("sku") or {}
    model = props.get("model") or {}

    sku_name = sku.get("name") or ""
    # NOTE: PAYG deployments use sku.name == "Standard"; PTU deployments use
    # "ProvisionedManaged". Any other sku.name is kept as-is.
    if sku_name == "Standard":
        tier = "PAYG"
    elif sku_name == "ProvisionedManaged":
        tier = "PTU"
    else:
        tier = sku_name or None

    return {
        "name": model.get("name") or None,
        "tier": tier,
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": sku.get("capacity"),
        "extra": {
            "model_version": model.get("version"),
            "model_format": model.get("format"),
        },
    }


@register_extractor("Microsoft.App/containerApps")
def _extract_aca(arm_resource: dict[str, Any]) -> dict[str, Any]:
    props = arm_resource.get("properties") or {}
    template = props.get("template") or {}
    scale = template.get("scale") or {}
    containers = template.get("containers") or []
    first_container_resources = (
        (containers[0].get("resources") or {}) if containers else {}
    )

    profile_name = props.get("workloadProfileName") or "Consumption"
    sku_name = "consumption" if profile_name == "Consumption" else profile_name

    max_replicas = scale.get("maxReplicas") or 3
    min_replicas = scale.get("minReplicas") or 0

    # Parse memory like "0.5Gi" → 0.5 (GiB as float).
    raw_memory = first_container_resources.get("memory") or ""
    memory_gib = _parse_gib(raw_memory)

    return {
        "name": sku_name,
        "tier": sku_name,
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": max_replicas,
        "extra": {
            "min_replicas": min_replicas,
            "max_replicas": max_replicas,
            "vcpu": first_container_resources.get("cpu"),
            "memory_gib": memory_gib,
        },
    }


def _parse_gib(value: str) -> float | None:
    """Parse a memory string like '0.5Gi' or '2Gi' into a float (GiB).

    Returns None if the value is absent or unparseable.
    """
    if not value:
        return None
    m = re.match(r"^([\d.]+)\s*Gi$", value.strip())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


@register_extractor("Microsoft.DocumentDB/databaseAccounts")
def _extract_cosmos(arm_resource: dict[str, Any]) -> dict[str, Any]:
    props = arm_resource.get("properties") or {}
    capabilities: list[dict] = props.get("capabilities") or []

    is_serverless = any(
        (c.get("name") or "") == "EnableServerless" for c in capabilities
    )
    tier = "serverless" if is_serverless else "provisioned"

    capacity = None
    cap_block = props.get("capacity") or {}
    if "totalThroughputLimit" in cap_block:
        capacity = cap_block["totalThroughputLimit"]

    return {
        "name": "cosmos-noSQL",
        "tier": tier,
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": capacity,
        "extra": {
            "api_kind": props.get("databaseAccountOfferType"),
            "multi_write": props.get("enableMultipleWriteLocations"),
        },
    }


@register_extractor("Microsoft.Storage/storageAccounts")
def _extract_storage(arm_resource: dict[str, Any]) -> dict[str, Any]:
    sku = arm_resource.get("sku") or {}
    props = arm_resource.get("properties") or {}
    sku_name = sku.get("name") or ""

    # Redundancy is the suffix after the last underscore: LRS, ZRS, GRS, etc.
    # NOTE: Names like Standard_LRS, Premium_LRS, Standard_GRS, Standard_RAGRS.
    parts = sku_name.split("_")
    redundancy = parts[-1] if len(parts) > 1 else None

    return {
        "name": sku_name or None,
        "tier": sku.get("tier"),
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": None,
        "extra": {
            "redundancy": redundancy,
            "access_tier": props.get("accessTier"),
        },
    }


@register_extractor("Microsoft.ApiManagement/service")
def _extract_apim(arm_resource: dict[str, Any]) -> dict[str, Any]:
    sku = arm_resource.get("sku") or {}
    sku_name = sku.get("name") or None

    return {
        "name": sku_name,
        "tier": sku_name,
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": sku.get("capacity"),
        "extra": {},
    }


@register_extractor("Microsoft.Search/searchServices")
def _extract_ai_search(arm_resource: dict[str, Any]) -> dict[str, Any]:
    sku = arm_resource.get("sku") or {}
    props = arm_resource.get("properties") or {}
    sku_name = sku.get("name") or None

    replicas = props.get("replicaCount") or 1
    partitions = props.get("partitionCount") or 1
    capacity = replicas * partitions

    return {
        "name": sku_name,
        "tier": sku_name,
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": capacity,
        "extra": {
            "replicas": replicas,
            "partitions": partitions,
        },
    }


@register_extractor("Microsoft.MachineLearningServices/workspaces")
def _extract_foundry_hosted_agent(arm_resource: dict[str, Any]) -> dict[str, Any]:
    props = arm_resource.get("properties") or {}
    sku = arm_resource.get("sku") or {}
    kind = props.get("kind") or None

    return {
        "name": kind,
        "tier": sku.get("tier") or "Standard",
        "region": _resolve_location(arm_resource.get("location") or ""),
        "capacity": None,
        "extra": {
            "hub_kind": kind,
        },
    }
