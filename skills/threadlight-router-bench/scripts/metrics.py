#!/usr/bin/env python3
"""Azure Monitor token-metric harvest + parse for the bench cost axis.

`parse_metrics` turns an `az monitor metrics list` JSON document into per-model
token usage. Dimension keys come back LOWERCASE (`modelname`), and the per-model
total is the sum of `data[].total` across the timespan.

Caveat (design constraint): Cognitive Services token metrics carry NO run-id
dimension, so benches must be serialized on a shared deployment and bounded by the
run's start/end window to attribute usage correctly.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

_INPUT_METRICS = {"inputtokens", "prompttokens"}
_OUTPUT_METRICS = {"outputtokens", "completiontokens", "generatedtokens"}


def parse_metrics(doc: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Aggregate az-monitor metrics JSON into {model: {input, output}} token totals."""
    usage: dict[str, dict[str, int]] = {}
    for metric in doc.get("value", []):
        mname = (metric.get("name", {}).get("value") or "").lower()
        if mname in _INPUT_METRICS:
            axis = "input"
        elif mname in _OUTPUT_METRICS:
            axis = "output"
        else:
            continue
        for ts in metric.get("timeseries", []):
            dims = {(d.get("name", {}).get("value") or "").lower(): d.get("value")
                    for d in ts.get("metadatavalues", [])}
            model = dims.get("modelname") or dims.get("modeldeploymentname") or "unknown"
            total = sum(int(p.get("total") or 0) for p in ts.get("data", []))
            slot = usage.setdefault(model, {"input": 0, "output": 0})
            slot[axis] += total
    return usage


def fetch_metrics(resource_id: str, start_iso: str, end_iso: str,
                  deployment: str = "model-router", interval: str = "PT1H",
                  runner: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """`az monitor metrics list` for InputTokens+OutputTokens, split by model."""
    run = runner or _default_runner
    out = run([
        "monitor", "metrics", "list", "--resource", resource_id,
        "--metrics", "InputTokens", "OutputTokens",
        "--start-time", start_iso, "--end-time", end_iso,
        "--interval", interval, "--aggregation", "Total",
        "--filter", f"ModelDeploymentName eq '{deployment}' and ModelName eq '*'",
        "-o", "json",
    ])
    return json.loads(out)


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["az", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("az " + " ".join(args) + " failed:\n" + proc.stderr.strip())
    return proc.stdout
