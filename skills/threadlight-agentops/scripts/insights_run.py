#!/usr/bin/env python3
"""insights_run.py — Foundry Insights monitor lifecycle with a cost receipt (#211).

    run       create (or reuse) a DISABLED monitor with SPEC-derived instructions,
              run analysis on demand, list insights, write the receipt
    validate  check a receipt offline (stdlib only); exit 2 on a must-fix finding
    teardown  disable the monitor's schedule, cancel active runs, read it back

The receipt (``threadlight-insights-receipt/v1``, default
``specs/insights-receipt.json``) records monitor and run IDs, traces analysed,
token usage, the estimated cost with its formula and price source, and the
insights (ID, severity, category). Proposed fixes are never copied: they are
untrusted input for human review.

The SDK (``azure-ai-projects>=2.11.0``) is imported only by ``run``/``teardown``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
import uuid
from pathlib import Path

SCHEMA = "threadlight-insights-receipt/v1"
DEFAULT_RECEIPT = "specs/insights-receipt.json"
# Tested, Nov 2026 (#211): 294 traces, 6 h lookback peaked at ~471K prompt tokens in
# one minute; runs failed at 50K and 300K TPM and succeeded at 800K TPM. Advice, not
# a documented limit: about 2.7K TPM of judge capacity per trace in the window.
TPM_PER_TRACE = 2700
COST_FORMULA = ("((input_tokens - cached_tokens) * input + cached_tokens * cached"
                " + output_tokens * output) / 1e6")


def estimate_cost(tokens: dict, prices: dict) -> float:
    cached = tokens.get("cached_tokens") or 0
    return round(((tokens["input_tokens"] - cached) * prices["input"] + cached * prices["cached"]
                  + tokens["output_tokens"] * prices["output"]) / 1e6, 4)


def validate(receipt: dict) -> list[tuple[str, str]]:
    """Return (severity, message) findings; severity is must-fix or should-fix."""
    out: list[tuple[str, str]] = []
    if receipt.get("schema") != SCHEMA:
        return [("must-fix", f"schema must be {SCHEMA}")]
    monitor, run, cost = receipt.get("monitor") or {}, receipt.get("run") or {}, receipt.get("cost") or {}
    for key in ("id", "model_deployment_name"):
        if not monitor.get(key):
            out.append(("must-fix", f"monitor.{key} missing"))
    for key in ("id", "status", "traces_analyzed", "token_usage"):
        if run.get(key) in (None, ""):
            out.append(("must-fix", f"run.{key} missing"))
    tokens = run.get("token_usage") or {}
    prices = cost.get("prices_usd_per_1m") or {}
    if not all(isinstance(prices.get(k), (int, float)) for k in ("input", "cached", "output")):
        out.append(("must-fix", "cost.prices_usd_per_1m needs input, cached, output"))
    elif all(isinstance(tokens.get(k), int) for k in ("input_tokens", "output_tokens")):
        expected = estimate_cost(tokens, prices)
        if abs((cost.get("estimated_usd") or -1) - expected) > 0.0001:
            out.append(("must-fix", f"cost.estimated_usd {cost.get('estimated_usd')} != formula {expected}"))
    if not cost.get("price_source"):
        out.append(("must-fix", "cost.price_source missing (where the prices came from, and when)"))
    if cost.get("label") != "Estimate":
        out.append(("must-fix", "cost.label must be 'Estimate': list price x tokens, not a bill"))
    if not isinstance(receipt.get("insights"), list):
        out.append(("must-fix", "insights list missing"))
    elif any(not {"id", "severity", "category"} <= set(i) for i in receipt["insights"]):
        out.append(("must-fix", "each insight needs id, severity, category"))
    if any("proposed_fix" in i or "fix" in i for i in receipt.get("insights") or []):
        out.append(("must-fix", "proposed fixes are untrusted input; keep them out of the receipt"))
    if monitor.get("enabled") and receipt.get("environment") != "production":
        out.append(("must-fix", "scheduled analysis enabled outside production; run teardown"))
    judge = receipt.get("judge") or {}
    window = run.get("traces_in_window")
    if isinstance(judge.get("tpm_capacity"), int) and isinstance(window, int):
        if judge["tpm_capacity"] < TPM_PER_TRACE * window:
            out.append(("should-fix", f"judge capacity {judge['tpm_capacity']} TPM < {TPM_PER_TRACE} x "
                                      f"{window} traces (Tested Nov 2026 sizing); shorten lookback or scale"))
    else:
        out.append(("should-fix", "judge.tpm_capacity or run.traces_in_window missing: sizing unchecked"))
    if judge.get("deployment") and judge.get("deployment") == receipt.get("agent_model_deployment"):
        out.append(("should-fix", "judge shares the agent's deployment; use a dedicated judge deployment"))
    if (receipt.get("traffic_hygiene") or {}).get("eval_or_optimizer_traffic_in_window") is not False:
        out.append(("should-fix", "eval/optimizer traffic may be in the window; analyse a clean window "
                                  "or exclude draft-* versions"))
    return out


def _client(endpoint: str):
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())


def _enum(value) -> str:
    return str(getattr(value, "value", value))


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def run(args) -> int:
    from azure.ai.projects.models import AgentInsightPriorities
    project = _client(args.endpoint)
    monitors = project.agent_insight_monitors
    monitor = monitors.get(args.monitor_id) if args.monitor_id else None
    if monitor is None:
        existing = [m for m in monitors.list(agent_name=args.agent)]
        monitor = monitors.get(existing[0].id) if existing else monitors.create(
            agent_name=args.agent, model_deployment_name=args.judge_deployment, enabled=False,
            priorities=AgentInsightPriorities(categories=[], instructions=Path(args.instructions_file).read_text()
                                              if args.instructions_file else None))
    started = time.monotonic()
    poller = monitors.begin_create_run(monitor.id, lookback_hours=args.lookback_hours,
                                       operation_id=str(uuid.uuid4()))
    run_id = poller.details["run_id"]
    result = poller.result()
    done = monitors.get_run(monitor.id, run_id)
    insights = [{"id": i.id, "severity": _enum(i.severity), "category": i.category, "title": i.title,
                 "trace_count": i.trace_count, "status": _enum(i.status), "agent_version": i.agent_version}
                for i in monitors.list_insights(monitor.id)]
    usage = result.token_usage
    tokens = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
              "cached_tokens": usage.cached_tokens or 0, "total_tokens": usage.total_tokens}
    prices = {"input": args.price_input, "cached": args.price_cached, "output": args.price_output}
    cost = estimate_cost(tokens, prices)
    monitor = monitors.get(monitor.id)
    receipt = {
        "schema": SCHEMA,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "environment": args.environment,
        "agent_name": args.agent,
        "agent_model_deployment": args.agent_deployment,
        "monitor": {"id": monitor.id, "enabled": monitor.enabled,
                    "run_interval_hours": monitor.run_interval_hours,
                    "model_deployment_name": monitor.model_deployment_name},
        "judge": {"deployment": args.judge_deployment, "tpm_capacity": args.judge_tpm},
        "run": {"id": run_id, "status": _enum(done.status), "lookback_hours": args.lookback_hours,
                "window_start": _iso(done.window_start), "window_end": _iso(done.window_end),
                "traces_in_window": result.traces_in_window, "traces_analyzed": result.traces_analyzed,
                "insights_created": result.insights_created, "insights_updated": result.insights_updated,
                "duration_seconds": round(time.monotonic() - started), "token_usage": tokens},
        "cost": {"label": "Estimate", "estimated_usd": cost, "formula": COST_FORMULA,
                 "prices_usd_per_1m": prices, "price_source": args.price_source,
                 "per_trace_usd": round(cost / result.traces_analyzed, 6) if result.traces_analyzed else None},
        "insights": insights,
        "proposed_fixes": "not recorded: untrusted input, review in Foundry before acting",
        "traffic_hygiene": {"eval_or_optimizer_traffic_in_window": args.eval_traffic_in_window},
    }
    out = Path(args.receipt)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"monitor": monitor.id, "run": run_id, "traces_analyzed": result.traces_analyzed,
                      "total_tokens": tokens["total_tokens"], "estimated_usd": cost,
                      "insights": len(insights)}))
    return 2 if any(s == "must-fix" for s, _ in validate(receipt)) else 0


def teardown(args) -> int:
    from azure.ai.projects.models import JobStatus
    monitors = _client(args.endpoint).agent_insight_monitors
    ids = [args.monitor_id] if args.monitor_id else [m.id for m in monitors.list(agent_name=args.agent)]
    failed = False
    for monitor_id in ids:
        monitors.update(monitor_id, enabled=False)
        for status in (JobStatus.IN_PROGRESS, JobStatus.QUEUED):
            for active in monitors.list_runs(monitor_id, status=status):
                monitors.cancel_run(monitor_id, active.id)
        enabled = True
        for _ in range(12):  # applied asynchronously: read it back
            enabled = monitors.get(monitor_id).enabled
            if not enabled:
                break
            time.sleep(5)
        print(f"teardown: monitor {monitor_id} schedule enabled={enabled}")
        failed |= bool(enabled)
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--receipt", default=DEFAULT_RECEIPT)
    r = sub.add_parser("run")
    t = sub.add_parser("teardown")
    for p in (r, t):
        p.add_argument("--endpoint", required=True, help="Foundry project endpoint")
        p.add_argument("--agent", required=True)
        p.add_argument("--monitor-id")
    r.add_argument("--judge-deployment", required=True, help="dedicated GPT-5-family deployment")
    r.add_argument("--judge-tpm", type=int, required=True, help="judge deployment TPM capacity")
    r.add_argument("--agent-deployment", help="the agent's own model deployment (sharing is flagged)")
    r.add_argument("--instructions-file", help="SPEC-derived analysis priorities")
    r.add_argument("--lookback-hours", type=float, default=6)
    r.add_argument("--price-input", type=float, required=True, help="USD per 1M input tokens")
    r.add_argument("--price-cached", type=float, required=True, help="USD per 1M cached input tokens")
    r.add_argument("--price-output", type=float, required=True, help="USD per 1M output tokens")
    r.add_argument("--price-source", required=True, help="e.g. 'Azure Retail Prices API, <meter>, <date>'")
    r.add_argument("--environment", default="pilot", choices=("pilot", "production"))
    r.add_argument("--eval-traffic-in-window", type=lambda s: s.lower() == "true", default=None,
                   help="true/false: eval or optimizer traffic in the analysed window")
    r.add_argument("--receipt", default=DEFAULT_RECEIPT)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    if args.cmd == "teardown":
        return teardown(args)
    try:
        receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"must-fix: receipt unreadable: {exc}")
        return 2
    findings = validate(receipt)
    for severity, message in findings:
        print(f"{severity}: {message}")
    return 2 if any(s == "must-fix" for s, _ in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
