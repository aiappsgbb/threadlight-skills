"""
Phase 3 — pricing client.

Thin wrapper around the `Azure-pricing` MCP tool with:
  * 24h on-disk cache at `.threadlight/cost-cache.json`
  * Versioned fixture fallback at `references/pricing-fixtures/<resource>.json`
  * `price_source` tagging on every returned price (live | fixture | fallback)

Each `get_price` call returns:

    {
      "unit_price_usd": 0.005,
      "unit": "per-1k-input-tokens",
      "price_source": "live",
      "fetched_at": "2026-06-12T14:00:00Z",
      "azure_meter_id": "abc-123",                    # only for live
      "raw": { ... }                                   # passthrough MCP payload
    }

The pricing client is intentionally dumb about what each `unit` means —
the per-resource projector knows that the AOAI unit is `per-1k-input-tokens`
vs `per-PTU-month`, etc. This keeps the cache uniform.

When `Azure-pricing` MCP is unavailable AND no fixture covers a required
SKU, callers should raise `PricingUnavailableError` (CLI exits 3).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CACHE_TTL = timedelta(hours=24)
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "references" / "pricing-fixtures"


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
        """Pre-fetch current_sku + alternatives so subsequent phases are fast."""
        # TODO(pricing-client): expand resource to its alternatives list and
        #                       call get_price for each. The alternatives list
        #                       comes from per-projector ALTERNATIVES tables.
        raise NotImplementedError

    # ---------- internals ---------------------------------------------------

    def _fetch_live(
        self,
        resource_kind: str,
        sku: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Invoke the Azure-pricing MCP and translate to our envelope."""
        # TODO(pricing-client): subprocess-invoke Azure-pricing MCP `pricing get`
        #                       with a query that maps (resource_kind, sku) ->
        #                       (Azure meter family, region, sku name). On any
        #                       failure (network, MCP missing, no rows), return None.
        return None

    def _lookup_fixture(
        self,
        resource_kind: str,
        sku: dict[str, Any],
    ) -> dict[str, Any] | None:
        fixture_file = FIXTURES_DIR / f"{_slugify(resource_kind)}.json"
        if not fixture_file.exists():
            return None
        # TODO(pricing-client): match (sku.name, sku.region, sku.tier) inside
        #                       the fixture JSON and return an envelope with
        #                       price_source="fixture" and fetched_at = file mtime.
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
