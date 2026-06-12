"""
Phase 3 — pricing client.

Calls the public, no-auth Azure Retail Prices API directly via stdlib
urllib (https://prices.azure.com/api/retail/prices).  MCP dependency
removed so the skill works offline-detectably (timeout → fixture fallback).

Each `get_price` call returns:

    {
      "unit_price_usd": 0.005,
      "unit": "per-1k-input-tokens",
      "price_source": "live" | "fixture" | "fallback",
      "fetched_at": "2026-06-12T14:00:00Z",
      "azure_meter_id": "abc-123",   # None for fixture / fallback
      "raw": { ... }
    }

When no live price AND no fixture covers a required SKU, callers should
raise `PricingUnavailableError` (CLI exits 3).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CACHE_TTL = timedelta(hours=24)
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "references" / "pricing-fixtures"
_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"


class PricingUnavailableError(RuntimeError):
    """Azure Retail Prices API unavailable AND no fixture fallback for a required SKU."""


# NOTE: OData filter lambdas map (resource_kind, sku) to an API filter string.
# Filters are intentionally loose (contains()) so the projector can pick from
# returned Items; exact MeterName matching changes too often to hard-code.
_ODATA_FILTERS: dict[str, Any] = {
    "Microsoft.CognitiveServices/accounts/deployments": lambda sku: (
        f"serviceName eq 'Cognitive Services'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(meterName, '{sku.get('name', '')}')"
        f" and contains(meterName, '{sku.get('meter_substring', 'Input')}')"
    ),
    "Microsoft.MachineLearningServices/workspaces": lambda sku: (
        f"serviceName eq 'Azure Machine Learning'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(skuName, '{sku.get('name', 'Basic')}')"
    ),
    "Microsoft.App/containerApps": lambda sku: (
        f"serviceName eq 'Container Apps'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(meterName, '{sku.get('meter_substring', 'vCPU')}')"
        f" and contains(skuName, '{sku.get('tier', 'Consumption')}')"
    ),
    "Microsoft.DocumentDB/databaseAccounts": lambda sku: (
        f"serviceName eq 'Azure Cosmos DB'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(meterName, '{sku.get('meter_substring', 'RU')}')"
    ),
    "Microsoft.Storage/storageAccounts": lambda sku: (
        f"serviceName eq 'Storage'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(skuName, '{sku.get('name', 'LRS')}')"
    ),
    "Microsoft.ApiManagement/service": lambda sku: (
        f"serviceName eq 'API Management'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(skuName, '{sku.get('name', 'Developer')}')"
    ),
    "Microsoft.Search/searchServices": lambda sku: (
        f"serviceName eq 'Azure Cognitive Search'"
        f" and armRegionName eq '{sku.get('region', 'eastus2')}'"
        f" and contains(skuName, '{sku.get('name', 'Basic')}')"
    ),
}


class PricingClient:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self._cache: dict[str, Any] = self._load_cache()

    def get_price(
        self,
        resource_kind: str,
        sku: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a {unit_price_usd, unit, price_source, ...} dict for sku."""
        key = self._cache_key(resource_kind, sku)
        cached = self._cache.get(key)
        if cached and not self._is_stale(cached):
            return cached

        live = self._fetch_live(resource_kind, sku)
        if live is not None:
            self._cache[key] = live
            self._save_cache()
            return live

        fixture = self._lookup_fixture(resource_kind, sku)
        if fixture is not None:
            return fixture

        # Caller decides whether this is a hard error.
        return {
            "unit_price_usd": None,
            "unit": None,
            "price_source": "fallback",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "error": f"no live price and no fixture for {resource_kind} sku={sku}",
        }

    def warm(self, resource: dict[str, Any]) -> None:
        """Prime the cache for resource's current_sku.

        Projectors call get_price for each alternative SKU; warm() only
        handles the *current* SKU so the price phase is idempotent and fast.
        """
        resource_kind = resource.get("resource_kind", "")
        sku = resource.get("current_sku", {})
        if os.environ.get("THREADLIGHT_VERBOSE"):
            print(
                f"[pricing_client] warming {resource_kind} sku={sku}",
                file=sys.stderr,
            )
        self.get_price(resource_kind, sku)

    # ---------- internals ---------------------------------------------------

    def _fetch_live(
        self,
        resource_kind: str,
        sku: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call Azure Retail Prices API and return a price envelope, or None on failure."""
        filter_fn = _ODATA_FILTERS.get(resource_kind)
        if filter_fn is None:
            return None

        odata_filter = filter_fn(sku)
        encoded = urllib.parse.quote(odata_filter)
        url = f"{_RETAIL_PRICES_URL}?$filter={encoded}&currencyCode=USD"

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            if os.environ.get("THREADLIGHT_VERBOSE"):
                print(
                    f"[pricing_client] live fetch failed for {resource_kind}: {exc}",
                    file=sys.stderr,
                )
            return None

        items = data.get("Items") or []
        if not items:
            if os.environ.get("THREADLIGHT_VERBOSE"):
                print(
                    f"[pricing_client] no items from live fetch for {resource_kind} sku={sku}",
                    file=sys.stderr,
                )
            return None

        item = items[0]
        # NOTE: API may return non-USD if currencyCode param is ignored; guard here.
        currency = item.get("currencyCode", "USD")
        unit_price = item["unitPrice"] if currency == "USD" else item.get("retailPrice", item["unitPrice"])

        return {
            "unit_price_usd": unit_price,
            "unit": item.get("unitOfMeasure"),
            "price_source": "live",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "azure_meter_id": item.get("meterId"),
            "raw": item,
            "all_items": items,  # projector may inspect all_items to pick best match
        }

    def _lookup_fixture(
        self,
        resource_kind: str,
        sku: dict[str, Any],
    ) -> dict[str, Any] | None:
        fixture_file = FIXTURES_DIR / f"{_slugify(resource_kind)}.json"
        if not fixture_file.exists():
            return None

        try:
            data = json.loads(fixture_file.read_text())
        except Exception:
            return None

        skus = data.get("skus") or []
        for entry in skus:
            match_spec = entry.get("match", {})
            if _match_sku(match_spec, sku):
                mtime = datetime.fromtimestamp(
                    fixture_file.stat().st_mtime, tz=timezone.utc
                ).isoformat()
                return {
                    "unit_price_usd": entry["unit_price_usd"],
                    "unit": entry["unit"],
                    "price_source": "fixture",
                    "fetched_at": mtime,
                    "azure_meter_id": None,
                    "raw": entry,
                }

        return None

    def _cache_key(self, resource_kind: str, sku: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"kind": resource_kind, "sku": sku},
            sort_keys=True,
            separators=(",", ":"),
        )
        return canonical

    def _is_stale(self, cached: dict[str, Any]) -> bool:
        try:
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
        except Exception:
            return True
        return datetime.now(timezone.utc) - fetched_at > CACHE_TTL

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text())
        except Exception:
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2, sort_keys=True))


def _slugify(resource_kind: str) -> str:
    # Microsoft.CognitiveServices/accounts/deployments -> microsoft-cognitiveservices-accounts-deployments
    return resource_kind.lower().replace("/", "-").replace(".", "-")


def _match_sku(match_spec: dict[str, Any], sku: dict[str, Any]) -> bool:
    """Return True if every key in match_spec is satisfied by sku.

    Match rule:
      - Keys ending in ``_substring``: match_spec value must be a case-insensitive
        substring of sku[key].
      - All other keys: case-insensitive equality.

    If sku is missing a key the check fails.
    """
    for key, expected in match_spec.items():
        actual = str(sku.get(key, ""))
        if key.endswith("_substring"):
            if str(expected).lower() not in actual.lower():
                return False
        else:
            if str(expected).lower() != actual.lower():
                return False
    return True
