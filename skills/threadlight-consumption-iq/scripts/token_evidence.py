"""
Shared, pure parser for Azure Monitor Cognitive Services token-metric
evidence (`az monitor metrics list` on `InputTokens`/`OutputTokens`
[/`CachedInputTokens`]) — the single source of truth `metrics.py`
(`threadlight-router-bench`) builds on today. `cost_actuals.py`
(`threadlight-consumption-iq`) does not import this module directly yet;
the planned `actuals_sources.py` (joining Cost Management evidence from
`cost_actuals.py` with this module's token evidence into one reconciled
source) is what will bring token-metric evidence into
`threadlight-consumption-iq` when it lands.

## One source of truth, not two copies

`threadlight-router-bench` loads this module by repository-relative file
path (see `metrics.py`'s `_load_shared_parser`) rather than vendoring its
own copy, because the two skills are always installed together from the
same plugin and are never independently versioned — this repository is the
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


def _require_list(value: Any, label: str) -> list:
    """Return `value` unchanged if it is a `list`; raise `TokenEvidenceError`
    naming `label` otherwise. `value`/`timeseries`/`data`/`metadatavalues`
    are always list-shaped in a well-formed `az monitor metrics list`
    response — a document that reports one of them as e.g. a `dict` or a
    bare string is malformed evidence, and parsing it must fail loudly
    rather than silently degrade to an empty result (which would make a
    caller under-count usage without any indication why)."""
    if not isinstance(value, list):
        raise TokenEvidenceError(f"token metrics {label} must be a list, got {value!r}")
    return value


def _parse_buckets(
    doc: dict[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, object]], dict[tuple[str, str], set[str]]]:
    """Shared bucketing pass behind both `parse_token_series` and
    `parse_token_metrics`. Returns `(buckets, axes_seen)`: `buckets` maps
    `(deployment, model)` to the same row shape `parse_token_series`
    returns, and `axes_seen` maps that same key to the set of `{"input",
    "output", "cached_input"}` axes actually observed for it — the latter
    is what lets `parse_token_metrics` tell a model that only ever reported
    `CachedInputTokens` (no real input/output evidence at all) apart from
    one that genuinely observed a zero input/output total, without
    exposing this internal bookkeeping on the public row dicts themselves.
    """
    if not isinstance(doc, dict):
        raise TokenEvidenceError("token metrics document must be a mapping")
    metrics = _require_list(doc.get("value", []), "'value'")

    buckets: dict[tuple[str, str], dict[str, object]] = {}
    axes_seen: dict[tuple[str, str], set[str]] = {}

    for metric in metrics:
        if not isinstance(metric, dict):
            raise TokenEvidenceError(
                f"token metrics 'value' entries must be objects, got {metric!r}"
            )
        name_obj = metric.get("name")
        metric_name = name_obj.get("value") if isinstance(name_obj, dict) else None
        if not isinstance(metric_name, str):
            continue
        axis = _axis_for(metric_name)
        if axis is None:
            continue  # unrecognized metric: ignored, never guessed at

        timeseries = _require_list(
            metric.get("timeseries", []), f"{metric_name!r} 'timeseries'"
        )
        for series in timeseries:
            if not isinstance(series, dict):
                raise TokenEvidenceError(
                    f"{metric_name} timeseries entries must be objects, "
                    f"got {series!r}"
                )
            metadatavalues = _require_list(
                series.get("metadatavalues", []),
                f"{metric_name!r} 'metadatavalues'",
            )
            dims: dict[str, object] = {}
            for entry in metadatavalues:
                if not isinstance(entry, dict):
                    raise TokenEvidenceError(
                        f"{metric_name} metadatavalues entries must be "
                        f"objects, got {entry!r}"
                    )
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

            key = (deployment, model)
            row = buckets.setdefault(
                key,
                {
                    "deployment": deployment,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": None,
                },
            )
            axes_seen.setdefault(key, set()).add(axis)

            data = _require_list(series.get("data", []), f"{metric_name!r} 'data'")
            total = _sum_totals(data, metric_name=metric_name)
            if axis == "input":
                row["input_tokens"] = row["input_tokens"] + total
            elif axis == "output":
                row["output_tokens"] = row["output_tokens"] + total
            else:
                row["cached_input_tokens"] = (row["cached_input_tokens"] or 0) + total

    return buckets, axes_seen


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

    `value`/`timeseries`/`data`/`metadatavalues` must each actually be a
    list (with dict entries, where entries are expected) wherever present;
    a malformed document raises `TokenEvidenceError` naming the offending
    field rather than silently degrading to an empty/partial result.

    Output is sorted by `(deployment, model)`, casefolded, for a
    deterministic, diff-friendly result — Azure Monitor's own JSON ordering
    is not a contract this module should depend on or expose. The
    *original*, non-casefolded `(deployment, model)` is used as a
    tiebreaker so two rows that only differ in casing (e.g. `"Router"` vs
    `"router"`) still sort deterministically instead of falling back to
    whatever order they happened to appear in the source document.
    """
    buckets, _axes_seen = _parse_buckets(doc)
    return sorted(
        buckets.values(),
        key=lambda row: (
            str(row["deployment"]).casefold(),
            str(row["model"]).casefold(),
            str(row["deployment"]),
            str(row["model"]),
        ),
    )


def parse_token_metrics(doc: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Backward-compatible router-bench collapse: `{model: {input, output}}`,
    summed across every deployment that reported on that model. This is
    exactly the shape (and, on the same evidence, the exact same values)
    the pre-refactor `threadlight-router-bench` `parse_metrics` produced —
    `parse_token_series`'s richer per-deployment rows are new, additive
    evidence, not a replacement for this contract.

    A (deployment, model) pair that was observed *only* via
    `CachedInputTokens`-family evidence (no `InputTokens`/`OutputTokens`
    metric ever reported for it) is skipped here — the pre-refactor parser
    never recognized cached-input metrics at all, so it would never have
    produced an entry for such a model, and this collapse must stay exactly
    backward compatible rather than manufacturing a phantom
    `{"input": 0, "output": 0}` row out of cached-only evidence. A pair that
    did report input/output (even a genuinely observed zero total) is still
    included; only zero axes observed at all is a phantom.
    """
    buckets, axes_seen = _parse_buckets(doc)
    usage: dict[str, dict[str, int]] = {}
    for key, row in buckets.items():
        if not (axes_seen.get(key, set()) & {"input", "output"}):
            continue  # cached-only evidence: no phantom zero model
        model = str(row["model"])
        slot = usage.setdefault(model, {"input": 0, "output": 0})
        slot["input"] += int(row["input_tokens"])
        slot["output"] += int(row["output_tokens"])
    return usage
