"""
Shared, pure parser for Azure Monitor Cognitive Services token-metric
evidence (`az monitor metrics list` on `InputTokens`/`OutputTokens`
[/`CachedInputTokens`]) — the single source of truth `cost_actuals.py`
(`threadlight-consumption-iq`) and `metrics.py`
(`threadlight-router-bench`) both build on.

## One source of truth, not two copies

`threadlight-router-bench` imports this module by repository-relative path
(see `metrics.py`'s `_load_shared_parser`) rather than vendoring its own
copy, because the two skills are always installed together from the same
plugin and are never independently versioned — this repository is the
deployment unit. A divergent copy of this parser would let router-bench and
consumption-iq silently disagree about the exact same Azure Monitor
payload.

## No Azure calls, ever

Every function here is a pure, offline parser: it consumes the `dict`
already returned by an `az monitor metrics list` call (or a fixture
standing in for one) and never performs I/O, network access, or subprocess
execution itself.

## Fail closed on totals, but ignore what we don't recognize

Dimension/metric *names* are matched case-insensitively (Azure Monitor
returns dimension keys lowercased, e.g. `modelname`, but metric names in
whatever casing the metric definition used, e.g. `InputTokens`) and an
unrecognized metric name is silently ignored — this module only knows about
input/output/cached-input token counters, and a caller adding an unrelated
metric to the same `az monitor metrics list` call must not break parsing.

A `total` data-point value is a different story: `None` is a genuine "no
data for this interval" and is treated as `0`, but `bool` (a `bool` is an
`int` subclass in Python and must never be silently coerced into a token
count), a negative number, or a non-integral float (a token count that is
not a whole number is evidence of a caller/response bug, not a sub-token
observation to truncate) all raise `TokenEvidenceError` rather than being
silently truncated or dropped.

## Missing cached-input evidence is `None`, never `0`

`cached_input_tokens` is `None` for a deployment/model pair for which no
`CachedInputTokens`-family metric was ever observed, and only becomes an
integer (including a genuinely observed `0`) once at least one such metric
was. Collapsing "never measured" into "measured as zero" would silently
manufacture a 0% cache-hit rate out of thin air.
"""
from __future__ import annotations

from typing import Any, Optional

# Matched case-insensitively against `value[].name.value` (already
# casefolded here; callers casefold the observed name before matching).
INPUT_METRICS = frozenset({"inputtokens", "prompttokens"})
OUTPUT_METRICS = frozenset({"outputtokens", "completiontokens", "generatedtokens"})
# Accepted only if actually observed in the response — see the module
# docstring on why absence must stay `None`, never `0`.
CACHED_INPUT_METRICS = frozenset({"cachedinputtokens", "cachedprompttokens"})

_AXES = {
    name: "input" for name in INPUT_METRICS
} | {
    name: "output" for name in OUTPUT_METRICS
} | {
    name: "cached_input" for name in CACHED_INPUT_METRICS
}


class TokenEvidenceError(RuntimeError):
    """Azure Monitor token-metric evidence does not parse or does not
    validate. Always raised, never swallowed into a silent zero or a
    silently truncated count."""


def _axis_for(metric_name: str) -> Optional[str]:
    return _AXES.get(metric_name.casefold())


def _dim_name(entry: dict[str, Any]) -> str:
    name_obj = entry.get("name")
    if isinstance(name_obj, dict):
        value = name_obj.get("value")
        if isinstance(value, str):
            return value.casefold()
    return ""


def _sum_totals(data_points: Any, *, metric_name: str) -> int:
    total = 0
    for point in data_points or []:
        raw = point.get("total") if isinstance(point, dict) else None
        if raw is None:
            continue
        if isinstance(raw, bool):
            raise TokenEvidenceError(
                f"{metric_name} data point 'total' must be a number, not a "
                f"bool: {raw!r}"
            )
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, float):
            if not raw.is_integer():
                raise TokenEvidenceError(
                    f"{metric_name} data point 'total' is not an integral "
                    f"token count: {raw!r}"
                )
            value = int(raw)
        else:
            raise TokenEvidenceError(
                f"{metric_name} data point 'total' must be numeric, got "
                f"{raw!r}"
            )
        if value < 0:
            raise TokenEvidenceError(
                f"{metric_name} data point 'total' must not be negative: "
                f"{value!r}"
            )
        total += value
    return total


def parse_token_series(doc: dict[str, Any]) -> list[dict[str, object]]:
    """Parse an `az monitor metrics list` document into one row per distinct
    (deployment, model) pair actually observed, aggregating every timeseries
    and data point that reports on that pair.

    Each row is `{"deployment": str, "model": str, "input_tokens": int,
    "output_tokens": int, "cached_input_tokens": Optional[int]}`.

    `deployment` comes from the `ModelDeploymentName` metadata dimension
    (case-insensitive lookup), falling back to `"unknown"` when absent.
    `model` comes from `ModelName` first, then falls back to the observed
    deployment, then to `"unknown"` — the same fallback chain
    `parse_token_metrics` (and the router-bench parser it replaces) has
    always used, so a spillover deployment with no `ModelName` dimension
    still gets a stable, non-empty label. Preserving both columns
    separately (rather than collapsing straight to `model`, as
    `parse_token_metrics` does) is what lets a caller tell a spillover
    deployment apart from the primary one for the same model.

    Output is sorted by `(deployment, model)` for a deterministic,
    diff-friendly result — Azure Monitor's own JSON ordering is not a
    contract this module should depend on or expose.
    """
    if not isinstance(doc, dict):
        raise TokenEvidenceError("token metrics document must be a mapping")
    metrics = doc.get("value", [])
    if not isinstance(metrics, list):
        raise TokenEvidenceError("token metrics 'value' must be a list")

    buckets: dict[tuple[str, str], dict[str, object]] = {}

    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        name_obj = metric.get("name")
        metric_name = name_obj.get("value") if isinstance(name_obj, dict) else None
        if not isinstance(metric_name, str):
            continue
        axis = _axis_for(metric_name)
        if axis is None:
            continue  # unrecognized metric: ignored, never guessed at

        for series in metric.get("timeseries", []) or []:
            if not isinstance(series, dict):
                continue
            dims: dict[str, object] = {}
            for entry in series.get("metadatavalues", []) or []:
                if not isinstance(entry, dict):
                    continue
                dims[_dim_name(entry)] = entry.get("value")

            deployment_raw = dims.get("modeldeploymentname")
            model_raw = dims.get("modelname")

            deployment = (
                str(deployment_raw)
                if isinstance(deployment_raw, str) and deployment_raw
                else "unknown"
            )
            if isinstance(model_raw, str) and model_raw:
                model = model_raw
            elif isinstance(deployment_raw, str) and deployment_raw:
                model = str(deployment_raw)
            else:
                model = "unknown"

            row = buckets.setdefault(
                (deployment, model),
                {
                    "deployment": deployment,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": None,
                },
            )

            total = _sum_totals(series.get("data", []), metric_name=metric_name)
            if axis == "input":
                row["input_tokens"] = row["input_tokens"] + total
            elif axis == "output":
                row["output_tokens"] = row["output_tokens"] + total
            else:
                row["cached_input_tokens"] = (row["cached_input_tokens"] or 0) + total

    return sorted(
        buckets.values(),
        key=lambda row: (str(row["deployment"]).casefold(), str(row["model"]).casefold()),
    )


def parse_token_metrics(doc: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Backward-compatible router-bench collapse: `{model: {input, output}}`,
    summed across every deployment that reported on that model. This is
    exactly the shape (and, on the same evidence, the exact same values)
    the pre-refactor `threadlight-router-bench` `parse_metrics` produced —
    `parse_token_series`'s richer per-deployment rows are new, additive
    evidence, not a replacement for this contract.
    """
    usage: dict[str, dict[str, int]] = {}
    for row in parse_token_series(doc):
        model = str(row["model"])
        slot = usage.setdefault(model, {"input": 0, "output": 0})
        slot["input"] += int(row["input_tokens"])
        slot["output"] += int(row["output_tokens"])
    return usage
