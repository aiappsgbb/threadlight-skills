#!/usr/bin/env python3
"""Token price table ($ per 1M tokens) for the bench and workload cost axes.

Two sources, both estimates rather than billing truth:

* ``SEED_PRICES`` — Global Standard, short-context list prices copied from the
  Azure Retail Prices API on ``PRICES_AS_OF`` (Sweden Central). Use them for
  offline reasoning only.
* ``fetch_retail_prices`` + ``parse_retail_prices`` — a live pull from the
  public Azure Retail Prices API (https://prices.azure.com/api/retail/prices)
  with a finite per-request timeout, bounded pages and 429-aware retries.

Each model rate has ``input`` and ``output`` and, where a meter exists,
``cached_input`` and ``cache_write``. A missing ``cache_write`` is billed at
the ``input`` rate; a missing ``cached_input`` is billed at ``input`` too.
The model router routing fee is charged on every input token the router
handles, under the reserved key ``ROUTER_FEE_KEY``.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

PRICES_AS_OF = "2026-11-20"
RETAIL_API = "https://prices.azure.com/api/retail/prices"
RETAIL_API_VERSION = "2023-01-01-preview"
ROUTER_FEE_KEY = "_model_router_fee"

# Model router routing fee, $ per 1M input tokens ("Model Routers GL/DZ" meters).
ROUTER_FEE: dict[str, float] = {"global": 0.14, "datazone": 0.154}

# Global Standard, short context, from the Retail Prices API on PRICES_AS_OF.
SEED_PRICES: dict[str, dict[str, float]] = {
    "gpt-6.1-sol":   {"input": 2.00, "cached_input": 0.10, "cache_write": 2.50, "output": 10.00},
    "gpt-6.1-terra": {"input": 1.00, "cached_input": 0.05, "cache_write": 1.25, "output": 5.00},
    "gpt-6-astra":   {"input": 10.00, "cached_input": 1.00, "cache_write": 12.50, "output": 50.00},
    "gpt-6-sol":     {"input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 10.00},
    "gpt-6-luna":    {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
    "gpt-5.6-sol":   {"input": 4.00, "cached_input": 0.40, "cache_write": 5.00, "output": 20.00},
    "gpt-5.6-terra": {"input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 12.00},
    "gpt-5.6-luna":  {"input": 0.20, "cached_input": 0.02, "cache_write": 0.25, "output": 1.20},
    "gpt-5.5":       {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.4":       {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini":  {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano":  {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-5":         {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5-mini":    {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5-nano":    {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "deepseek-v4.1-flash": {"input": 0.30, "cached_input": 0.006, "output": 1.20},
    "grok-4.6":      {"input": 2.00, "cached_input": 0.50, "output": 6.00},
    "grok-4-1-fast-reasoning": {"input": 0.20, "output": 0.50},
    # No retail meter was published for gpt-6.2-* on PRICES_AS_OF. These are the
    # rates used in the Nov 2026 investigation; treat them as unverified.
    "gpt-6.2-astra": {"input": 8.00, "cached_input": 0.40, "cache_write": 10.00, "output": 40.00},
    "gpt-6.2-sol":   {"input": 2.00, "cached_input": 0.10, "cache_write": 2.50, "output": 10.00},
    ROUTER_FEE_KEY:  {"input": ROUTER_FEE["global"], "output": 0.0},
}
UNVERIFIED_SEED = frozenset({"gpt-6.2-astra", "gpt-6.2-sol"})

# Retail meters are named irregularly, so each model maps explicitly to the
# lower-cased skuName stem of each rate; the scope suffix (" gl"/" glbl" or
# " dz") is appended at lookup. Unmapped models are reported as missing.
METERS: dict[str, dict[str, str]] = {
    "gpt-6.1-sol": {"input": "6.1-sol shortco inp std", "cached_input": "6.1-sol shortco cd inp std",
                    "cache_write": "6.1-sol shortco cd wr std", "output": "6.1-sol shortco opt std"},
    "gpt-6.1-terra": {"input": "61 terra shortco inp std", "cached_input": "61 terra shortco cd inp std",
                      "cache_write": "61 terra shortco cd wr std", "output": "61 terra shortco opt std"},
    **{f"gpt-6-{n}": {"input": f"6-{n} shortco inp std", "cached_input": f"6-{n} shortco cd inp std",
                      "cache_write": f"6-{n} shortco cd wr std", "output": f"6-{n} shortco opt std"}
       for n in ("astra", "sol", "luna")},
    **{f"gpt-5.6-{n}": {"input": f"5.6 {n} shortco inp std", "cached_input": f"5.6 {n} shortco cd inp std",
                        "cache_write": f"5.6 {n} shortco cd wr std", "output": f"5.6 {n} shortco opt std"}
       for n in ("sol", "terra", "luna")},
    "gpt-5.5": {"input": "5.5 shortco inp", "cached_input": "5.5 shortco cd inp", "output": "5.5 shortco opt"},
    "gpt-5.4": {"input": "5.4 inp", "cached_input": "5.4 cd inp", "output": "5.4 opt"},
    **{f"gpt-5.4-{n}": {"input": f"5.4 {n} inp", "cached_input": f"5.4 {n} cd inp", "output": f"5.4 {n} opt"}
       for n in ("mini", "nano")},
    "gpt-5": {"input": "gpt 5 inpt", "cached_input": "gpt 5 cchd inpt", "output": "gpt 5 outpt"},
    **{f"gpt-5-{n}": {"input": f"gpt 5 {n} inpt", "cached_input": f"gpt 5 {n} cchd inpt",
                      "output": f"gpt 5 {n} outpt"} for n in ("mini", "nano")},
    "deepseek-v4.1-flash": {"input": "v4.1 flash input", "cached_input": "v4.1 flash cached",
                            "output": "v4.1 flash output"},
    "grok-4.6": {"input": "4.6 inp", "cached_input": "4.6 cached", "output": "4.6 outp"},
    "grok-4-1-fast-reasoning": {"input": "grok4 fast inp", "output": "grok4 fast outp"},
}
ROUTER_FEE_STEM = "model routers"
_SCOPE_SUFFIX = {"global": ("gl", "glbl"), "datazone": ("dz",)}
_UNIT_PER_1M = {"1m": 1.0, "1k": 1000.0}
_PRODUCTS = ("Azure OpenAI GPT5", "Azure OpenAI GPT6", "Azure Deepseek Models", "Azure Grok Models")

# Opener: (url, timeout) -> (status, json_body, headers)
Opener = Callable[[str, float], tuple[int, dict[str, Any], dict[str, str]]]


def parse_retail_prices(items: list[dict[str, Any]], scope: str = "global") -> dict[str, Any]:
    """Map Retail Prices API items to ``{prices, router_fee, missing}``.

    Rates are normalized to $ per 1M tokens (Grok meters are per 1K).
    ``missing`` lists mapped models with no input/output meter in ``items``.
    """
    if scope not in _SCOPE_SUFFIX:
        raise ValueError(f"scope must be one of {sorted(_SCOPE_SUFFIX)}")
    by_sku: dict[str, float] = {}
    for it in items:
        if it.get("type", "Consumption") != "Consumption":
            continue
        unit = str(it.get("unitOfMeasure", "")).strip().lower()
        if unit not in _UNIT_PER_1M:
            continue
        sku = " ".join(str(it.get("skuName", "")).lower().split())
        by_sku[sku] = round(float(it.get("unitPrice", 0.0)) * _UNIT_PER_1M[unit], 6)

    def lookup(stem: str) -> float | None:
        for suffix in _SCOPE_SUFFIX[scope]:
            if f"{stem} {suffix}" in by_sku:
                return by_sku[f"{stem} {suffix}"]
        return None

    table: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for model, stems in METERS.items():
        rates = {kind: v for kind, stem in stems.items() if (v := lookup(stem)) is not None}
        if "input" in rates and "output" in rates:
            table[model] = rates
        else:
            missing.append(model)
    return {"prices": table, "router_fee": lookup(ROUTER_FEE_STEM), "missing": sorted(missing)}


def _default_opener(url: str, timeout: float) -> tuple[int, dict[str, Any], dict[str, str]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.load(resp), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, {}, dict(exc.headers or {})


def _query(odata_filter: str, opener: Opener, timeout: float, max_pages: int,
           max_retries: int, sleep: Callable[[float], None]) -> list[dict[str, Any]]:
    url: str | None = (f"{RETAIL_API}?api-version={RETAIL_API_VERSION}&$filter="
                       + urllib.parse.quote(odata_filter))
    items: list[dict[str, Any]] = []
    pages = 0
    while url:
        if pages >= max_pages:
            raise RuntimeError(f"Retail Prices API: more than {max_pages} pages for {odata_filter!r}")
        for attempt in range(max_retries + 1):
            status, body, headers = opener(url, timeout)
            if status == 200:
                break
            if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                ra = {k.lower(): v for k, v in headers.items()}.get("retry-after")
                sleep(float(ra) if ra else min(30.0, 2.0 * 2 ** attempt))
                continue
            raise RuntimeError(f"Retail Prices API returned HTTP {status} for {odata_filter!r}")
        items.extend(body.get("Items", []))
        url = body.get("NextPageLink")
        pages += 1
    return items


def fetch_retail_prices(region: str, opener: Opener | None = None, timeout: float = 20.0,
                        max_pages: int = 40, max_retries: int = 4,
                        sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Pull model token meters and the router fee for ``region`` from the
    public Azure Retail Prices API. Every request has a finite timeout and the
    pull is bounded in pages and retries; it never waits indefinitely."""
    op = opener or _default_opener
    products = " or ".join(f"productName eq '{p}'" for p in _PRODUCTS)
    models = f"armRegionName eq '{region}' and serviceName eq 'Foundry Models' and ({products})"
    fee = f"armRegionName eq '{region}' and productName eq 'Model Tools'"
    return (_query(models, op, timeout, max_pages, max_retries, sleep)
            + _query(fee, op, timeout, max_pages, max_retries, sleep))


def retail_table(items: list[dict[str, Any]], scope: str = "global") -> dict[str, dict[str, float]]:
    """Seed table overlaid with retail rates parsed from ``items``."""
    parsed = parse_retail_prices(items, scope=scope)
    table: dict[str, dict[str, float]] = {m: dict(v) for m, v in SEED_PRICES.items()}
    table[ROUTER_FEE_KEY] = {"input": ROUTER_FEE[scope], "output": 0.0}
    for model, rates in parsed["prices"].items():
        table[model] = dict(rates)
    if parsed["router_fee"] is not None:
        table[ROUTER_FEE_KEY] = {"input": parsed["router_fee"], "output": 0.0}
    return table


def load_prices(path: Path | str | None = None,
                scope: str = "global") -> dict[str, dict[str, float]]:
    """Return the seed table, overlaid with an optional file.

    The file is either a Retail Prices API export (a JSON object with
    ``Items``) or an override ``{model: {input, output, ...}}``. An override
    entry replaces that model's rates entirely, so a stale seed cache rate
    never survives next to a fresh input/output pair.
    """
    if path is None:
        table = {m: dict(v) for m, v in SEED_PRICES.items()}
        table[ROUTER_FEE_KEY] = {"input": ROUTER_FEE[scope], "output": 0.0}
        return table
    doc: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(doc, dict) and isinstance(doc.get("Items"), list):
        return retail_table(doc["Items"], scope=scope)
    if isinstance(doc, list):
        return retail_table(doc, scope=scope)
    table = load_prices(None, scope=scope)
    for model, rates in doc.items():
        table[model] = dict(rates)
    return table


def base_name(model: str) -> str:
    """Normalize a served model id to its price key: lower-case, version
    suffix dropped (``gpt-6.1-terra-2026-10-26`` -> ``gpt-6.1-terra``)."""
    m = (model or "").strip().lower()
    for key in sorted(METERS.keys() | SEED_PRICES.keys(), key=len, reverse=True):
        if m == key or (m.startswith(key + "-") and m[len(key) + 1:len(key) + 2].isdigit()):
            return key
    return m
