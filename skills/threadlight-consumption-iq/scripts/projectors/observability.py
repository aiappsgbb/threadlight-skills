"""
Azure observability ingestion projector — Log Analytics / Application Insights.

Math (see references/consumption-formulas.md § Observability):

  seconds_per_month = (8h×22d if business_hours_only else 24h×30d) × 3600
  monthly_traces    = peak_requests_per_second × seconds_per_month
  bytes_per_trace    = extra.bytes_per_trace            (default 3 KB)
                     + extra.content_bytes_per_trace    (default 12 KB, only
                       counted when extra.content_recording is true)
  ingested_gb       = monthly_traces × bytes_per_trace / 1e9
  monthly_cost      = ingested_gb × analytics_per_gb_usd

Why this projector exists: GenAI agents emit OpenTelemetry spans on *every*
request (one per model call + one per tool call). At 100% capture with content
recording on, log ingestion is frequently the second- or third-largest line
item and the one teams forget. Making it explicit lets the seller right-size
sampling and content recording before the bill lands.

Alternatives compared:
  * sampling bands (50%, 10%) — linear reduction in ingest
  * content-recording toggle — drop prompt/completion capture

Stdlib only; no pricing-dimension lookups beyond per-GB analytics ingestion.
"""
from __future__ import annotations

from typing import Any

RESOURCE_KIND = "Microsoft.OperationalInsights/workspaces"

# Fallback price: Azure Monitor "Analytics Logs" pay-as-you-go ingestion.
ANALYTICS_PER_GB = 2.76

# Default GenAI span sizes (bytes). Conservative public estimates; override via
# extra.bytes_per_trace / extra.content_bytes_per_trace.
DEFAULT_BYTES_PER_TRACE = 3_000
DEFAULT_CONTENT_BYTES_PER_TRACE = 12_000

SAMPLING_BANDS = (0.5, 0.1)


def _seconds_per_month(load_profile: dict[str, Any]) -> int:
    business_hours = bool(load_profile.get("business_hours_only"))
    hours_per_day = 8 if business_hours else 24
    days_per_month = 22 if business_hours else 30
    return hours_per_day * 3600 * days_per_month


def _bytes_per_trace(extra: dict[str, Any]) -> int:
    base = extra.get("bytes_per_trace", DEFAULT_BYTES_PER_TRACE)
    if extra.get("content_recording"):
        base += extra.get("content_bytes_per_trace", DEFAULT_CONTENT_BYTES_PER_TRACE)
    return base


def project(
    current_sku: dict[str, Any],
    load_profile: dict[str, Any],
    pricing_client: Any,
) -> dict[str, Any]:
    extra = current_sku.get("extra") or {}

    price = pricing_client.get_price(RESOURCE_KIND, current_sku)
    unit = price.get("unit_price_usd")
    if unit is None:
        unit = ANALYTICS_PER_GB
        price_source = "fallback"
    else:
        price_source = price.get("price_source", "live")

    seconds = _seconds_per_month(load_profile)
    rps = float(load_profile.get("peak_requests_per_second", 0.0))
    # DELIBERATE upper bound: we treat the declared PEAK rps as if sustained for
    # every second of the month. Real ingest is lower (peaks are not 24x7), but
    # for a pre-deploy estimate of GenAI OTel — where 100%-capture, no-sampling,
    # content-recording is the worst case the customer should budget for — we
    # quote the ceiling, then offer sampling/no-content bands as alternatives
    # below. Do NOT "average this down" without an explicit avg-rps input.
    monthly_traces = rps * seconds

    per_trace = _bytes_per_trace(extra)
    ingested_gb = monthly_traces * per_trace / 1e9
    current_cost = ingested_gb * unit

    alternatives: list[dict[str, Any]] = []

    # -- sampling bands --------------------------------------------------
    for rate in SAMPLING_BANDS:
        alt_cost = current_cost * rate
        alternatives.append(
            _alt(
                {
                    "name": current_sku.get("name", "PerGB2018"),
                    "tier": current_sku.get("tier", "PerGB2018"),
                    "extra": {**extra, "sampling_rate": rate},
                },
                alt_cost,
                current_cost,
                rationale=(
                    f"Sample {int(rate * 100)}% of traces — ingest (and cost) "
                    f"scale linearly with sampling rate."
                ),
            )
        )

    # -- content-recording toggle ---------------------------------------
    recording_on = bool(extra.get("content_recording"))
    toggled = {**extra, "content_recording": not recording_on}
    toggled_per_trace = _bytes_per_trace(toggled)
    toggled_cost = monthly_traces * toggled_per_trace / 1e9 * unit
    alternatives.append(
        _alt(
            {
                "name": current_sku.get("name", "PerGB2018"),
                "tier": current_sku.get("tier", "PerGB2018"),
                "extra": toggled,
            },
            toggled_cost,
            current_cost,
            rationale=(
                "Disable prompt/completion content recording — keeps span "
                "skeletons, drops the largest per-trace payload."
                if recording_on
                else "Enable content recording — adds prompt/completion capture."
            ),
        )
    )

    return {
        "current_sku": current_sku,
        "monthly_cost_usd": current_cost,
        "monthly_units_consumed": {
            "ingested_gb": ingested_gb,
            "monthly_traces": monthly_traces,
            "bytes_per_trace": per_trace,
        },
        "price_source": price_source,
        "alternatives": alternatives,
    }


def _alt(sku: dict[str, Any], alt_cost: float, current_cost: float, rationale: str) -> dict[str, Any]:
    return {
        "sku": sku,
        "monthly_cost_usd": alt_cost,
        "delta_usd": alt_cost - current_cost,
        "delta_pct": (alt_cost - current_cost) / current_cost if current_cost else 0.0,
        "satisfies_constraints": True,
        "caveats": [],
        "rationale": rationale,
    }
