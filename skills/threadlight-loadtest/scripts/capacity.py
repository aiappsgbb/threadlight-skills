"""Capacity facts and cost per 1,000 requests for a hosted-agent load test.

Three kinds of facts, kept apart:

* ``observed``   read from Azure Resource Manager at run time (model deployment
                 SKU and rate limits, MCP Container App scale rule and replicas);
* ``measured``   from the load run itself (peak concurrency, sandboxes started,
                 tokens, throttles);
* ``documented`` from current Microsoft Learn pages, cited by URL.

Cost per 1,000 requests is ``calculated`` from measured tokens and Azure Retail
Prices API meters (https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices).
Hosted-agent compute is an ``estimate``: billing is per vCPU and GiB across
active sessions, but session active time is not measured here.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional

HOSTED_AGENTS = "https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#scaling-and-right-sizing"
HOSTED_PLATFORM = "https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#platform-details"
AGENT_LIMITS = "https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions#default-service-limits"
SESSION_IDLE = "https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-sessions#manage-session-idleness"
MODEL_QUOTAS = "https://learn.microsoft.com/azure/foundry/openai/quotas-limits"
PROMPT_CACHING = "https://learn.microsoft.com/azure/foundry/openai/how-to/prompt-caching"
ACA_SCALE = "https://learn.microsoft.com/azure/container-apps/scale-app"
RATE_LIMITS = "https://learn.microsoft.com/microsoft-cloud/dev/dev-proxy/concepts/llm-rate-limits"
RETAIL_PRICES = "https://prices.azure.com/api/retail/prices"

DOCUMENTED = (
    {"label": "Hosted agent scaling unit", "kind": "documented", "source": HOSTED_AGENTS,
     "value": "One VM-isolated sandbox per session, created on demand; no replica count and no warm pool to size."},
    {"label": "Session idle timeout", "kind": "documented", "source": SESSION_IDLE,
     "value": "2 to 240 minutes per agent version, default 15 minutes; compute is deprovisioned after it, state kept."},
    {"label": "Concurrent hosted agent sessions per subscription and region", "kind": "documented",
     "source": AGENT_LIMITS,
     "value": "2,000 in Canada Central, East US 2, Japan East, North Central US, South Africa North, Southeast Asia "
              "and Sweden Central; 1,000 in other regions (default, increase by support request)."},
    {"label": "Request rate limit errors", "kind": "documented",
     "source": "https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions#limit-error-reference",
     "value": "HTTP 429 rate_limit_exceeded: retry with exponential backoff; 429 session_quota_exceeded when the "
              "concurrent session quota is reached. The numeric request-rate limit is not published."},
    {"label": "Session sizes", "kind": "documented", "source": HOSTED_PLATFORM,
     "value": "0.5 vCPU / 1 GiB, 1 vCPU / 2 GiB or 2 vCPU / 4 GiB per session; billing is CPU and memory across "
              "all active sessions."},
    {"label": "Model rate limits", "kind": "documented", "source": MODEL_QUOTAS,
     "value": "Each model deployment has its own TPM and RPM limit (capacity units); the agent's model calls "
              "share it with every other caller of that deployment."},
    {"label": "How model throttling is enforced", "kind": "documented", "source": RATE_LIMITS,
     "value": "Azure OpenAI checks limits over 1- or 10-second windows and its token estimate includes max_tokens, "
              "so a burst gets HTTP 429 even when the per-minute total looks fine."},
    {"label": "MCP server scaling", "kind": "documented", "source": ACA_SCALE,
     "value": "Container Apps scale between the app's min and max replicas; with min 0 the first request after "
              "idle waits for a replica to start."},
)


# ---------------------------------------------------------------------------
# ARM reads (az CLI runner injected)
# ---------------------------------------------------------------------------

def _load(run: Callable[[list], str], argv: list) -> Any:
    try:
        text = run(argv)
        return json.loads(text) if text and text.strip() else None
    except Exception:  # noqa: BLE001 - a missing fact is reported as not read
        return None


def model_deployment(run: Callable[[list], str], *, resource_group: str, account: str, deployment: str) -> dict:
    data = _load(run, ["az", "cognitiveservices", "account", "deployment", "show", "-g", resource_group,
                       "-n", account, "--deployment-name", deployment, "-o", "json"]) or {}
    sku = data.get("sku") or {}
    limits = {}
    for item in (data.get("properties") or {}).get("rateLimits") or []:
        key = {"request": "rpm", "token": "tpm"}.get(str(item.get("key")))
        period = float(item.get("renewalPeriod") or 60)
        if key and item.get("count") is not None:
            limits[key] = float(item["count"]) * (60.0 / period)
    model = (data.get("properties") or {}).get("model") or {}
    return {"read": bool(data), "sku": sku.get("name"), "capacity": sku.get("capacity"),
            "model": model.get("name"), "version": model.get("version"),
            "tpm": limits.get("tpm"), "rpm": limits.get("rpm")}


def container_app(run: Callable[[list], str], *, resource_group: str, name: str) -> dict:
    data = _load(run, ["az", "containerapp", "show", "-g", resource_group, "-n", name, "-o", "json"]) or {}
    scale = ((data.get("properties") or {}).get("template") or {}).get("scale") or {}
    replicas = _load(run, ["az", "containerapp", "replica", "list", "-g", resource_group, "-n", name, "-o", "json"])
    return {"read": bool(data), "min_replicas": scale.get("minReplicas"), "max_replicas": scale.get("maxReplicas"),
            "replicas_now": len(replicas) if isinstance(replicas, list) else None}


def model_requests_by_status(run: Callable[[list], str], *, account_id: str, start: str, end: str) -> dict:
    """Azure OpenAI requests by HTTP status in the run window (Azure Monitor metric ``AzureOpenAIRequests``)."""
    data = _load(run, ["az", "monitor", "metrics", "list", "--resource", account_id, "--metric", "AzureOpenAIRequests",
                       "--dimension", "StatusCode", "--start-time", start, "--end-time", end, "--interval", "PT1M",
                       "--aggregation", "Total", "-o", "json"])
    out: dict[str, int] = {}
    for metric in (data or {}).get("value") or []:
        for series in metric.get("timeseries") or []:
            code = next((m.get("value") for m in series.get("metadatavalues") or []
                         if (m.get("name") or {}).get("value", "").lower() == "statuscode"), None)
            if code is not None:
                out[str(code)] = out.get(str(code), 0) + int(sum(p.get("total") or 0 for p in series.get("data") or []))
    return {"read": data is not None, "by_status": out}


# ---------------------------------------------------------------------------
# Retail prices
# ---------------------------------------------------------------------------

def _meter_prefix(model: str) -> str:
    """``gpt-5.4`` -> ``5.4``; ``gpt-5.4-mini`` -> ``5.4 mini`` (Retail Prices meter naming)."""
    return re.sub(r"^gpt-", "", model.strip().lower()).replace("-", " ")


def _http_get_json(url: str, timeout: float = 20.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed public HTTPS API
        return json.loads(response.read().decode("utf-8"))


def model_prices(model: str, region: str, deployment_sku: str = "GlobalStandard", *,
                 get: Callable[[str], dict] = _http_get_json, max_pages: int = 5) -> dict:
    """USD per 1M tokens for input, cached input and output; ``None`` when no meter matched."""
    prefix = _meter_prefix(model)
    zone = "dz" if deployment_sku.lower().startswith("datazone") else "gl"
    flt = (f"serviceName eq 'Foundry Models' and armRegionName eq '{region}' "
           f"and contains(meterName, '{prefix}') and type eq 'Consumption'")
    url: Optional[str] = f"{RETAIL_PRICES}?{urllib.parse.urlencode({'$filter': flt})}"
    wanted = {"input": f"{prefix} inp {zone}", "cached_input": f"{prefix} cd inp {zone}",
              "output": f"{prefix} opt {zone}"}
    found: dict[str, Any] = {k: None for k in wanted}
    meters: dict[str, str] = {}
    pages = 0
    while url and pages < max_pages:
        data = get(url)
        pages += 1
        for item in data.get("Items", []):
            sku = re.sub(r"\s+", " ", str(item.get("skuName", "")).lower()).strip()
            unit = str(item.get("unitOfMeasure", ""))
            for key, name in wanted.items():
                if sku == name and found[key] is None:
                    per = 1.0 if unit == "1M" else 0.001 if unit == "1K" else None
                    if per is not None:
                        found[key] = float(item["retailPrice"]) / per
                        meters[key] = item.get("meterName")
        url = data.get("NextPageLink")
    return {"usd_per_1m": found, "meters": meters, "region": region, "source": RETAIL_PRICES}


def hosted_compute_prices(region: str, *, get: Callable[[str], dict] = _http_get_json) -> dict:
    flt = f"armRegionName eq '{region}' and productName eq 'Foundry Agents' and skuName eq 'Hosted'"
    data = get(f"{RETAIL_PRICES}?{urllib.parse.urlencode({'$filter': flt})}")
    out = {"vcpu_hour": None, "gib_hour": None}
    for item in data.get("Items", []):
        name = str(item.get("meterName", "")).lower()
        if "vcpu" in name:
            out["vcpu_hour"] = float(item["retailPrice"])
        elif "memory" in name:
            out["gib_hour"] = float(item["retailPrice"])
    return out


def cost_per_1000(tokens: Mapping[str, Any], prices: Mapping[str, Any], requests: int) -> dict:
    """Calculated model cost per 1,000 successful requests from measured token totals."""
    p = prices.get("usd_per_1m") or {}
    if not requests or p.get("input") is None or p.get("output") is None:
        return {"kind": "not-measured", "usd": None,
                "why": "no priced meter or no successful joined requests"}
    cached = float(tokens.get("cached_input_tokens") or 0)
    uncached = max(0.0, float(tokens.get("input_tokens") or 0) - cached)
    cached_price = p.get("cached_input") if p.get("cached_input") is not None else p["input"]
    total = (uncached * p["input"] + cached * cached_price + float(tokens.get("output_tokens") or 0) * p["output"]) / 1e6
    return {"kind": "calculated", "usd": round(total / requests * 1000, 2), "run_usd": round(total, 4),
            "formula": "((input - cached) x input price + cached x cached-input price + output x output price) "
                       f"/ {requests} requests x 1,000",
            "assumption": "Cached input tokens are a subset of input tokens and billed at the cached-input meter "
                          f"({PROMPT_CACHING}). Reasoning tokens are counted as reported in output tokens."}


def compute_estimate(*, sessions: int, active_minutes: float, idle_timeout_minutes: float, vcpu: float, gib: float,
                     prices: Mapping[str, Any], requests: int) -> dict:
    if not requests or prices.get("vcpu_hour") is None or prices.get("gib_hour") is None:
        return {"kind": "not-measured", "usd": None, "why": "no hosted compute meter"}
    hours = sessions * (active_minutes + idle_timeout_minutes) / 60.0
    usd = hours * (vcpu * prices["vcpu_hour"] + gib * prices["gib_hour"])
    return {"kind": "estimate", "usd": round(usd / requests * 1000, 2), "run_usd": round(usd, 4),
            "formula": f"{sessions} sessions x (run time + {idle_timeout_minutes:g} min idle timeout) x "
                       f"({vcpu:g} vCPU x vCPU-hour + {gib:g} GiB x GiB-hour) / {requests} requests x 1,000",
            "why": "Upper bound: assumes every sandbox stays billed until its idle timeout. Session active time "
                   "is not measured by this run."}


def headroom(measured_per_min: Optional[float], limit_per_min: Optional[float]) -> Optional[float]:
    if not measured_per_min or not limit_per_min:
        return None
    return round(measured_per_min / limit_per_min, 3)
