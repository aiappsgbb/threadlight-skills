#!/usr/bin/env python3
"""Performance evidence for a deployed Threadlight GHCP pilot (Foundry hosted agent).

One guarded command: budget and production gates (``loadtest.run_loadtest``),
a phased load through the real Invocations endpoint (``hosted_agent``), the
latency waterfall from the agent's own Application Insights spans
(``waterfall``), capacity facts and cost (``capacity``), then:

* ``specs/load-manifest.json``  -- ``threadlight.load/v1`` (unchanged contract);
* ``specs/perf-evidence.json``  -- ``threadlight-perf-evidence/v1`` (everything below);
* ``docs/perf-evidence.md`` and ``docs/perf-evidence.html`` -- customer-ready report
  in the pilot value page style. Measured, calculated, estimated and documented
  values are labelled; anything not measured says so.

Run from the pilot root (an azd project deployed by threadlight-deploy)::

    python <threadlight-skills>/skills/threadlight-loadtest/scripts/perf_evidence.py \\
        --workspace . --agent returns-triage --budget-ceiling-usd 25 \\
        --tokens-per-request-estimate 60000 --max-concurrency 10 --steady-requests 200

Re-render the report from existing evidence without sending requests::

    python .../perf_evidence.py --workspace . --render-only

It spends money (model tokens, hosted sessions). It never deploys, never
retries a run and never writes prompts, answers or tokens into any output.
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import capacity  # noqa: E402
import hosted_agent  # noqa: E402
import waterfall  # noqa: E402
from loadtest import run_loadtest, write_load_manifest  # noqa: E402

SCHEMA = "threadlight-perf-evidence/v1"
EVIDENCE = "specs/perf-evidence.json"
MANIFEST = "specs/load-manifest.json"
REPORT_MD = "docs/perf-evidence.md"
REPORT_HTML = "docs/perf-evidence.html"
RAW = "specs/perf-run-raw.json"

KIND_LABELS = {"measured": "Measured", "calculated": "Calculated", "estimate": "Estimate",
               "observed": "Observed in Azure", "documented": "Documented", "not-measured": "Not measured"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _az(argv: list) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=180, check=True).stdout


def azd_env(workspace: Path, run: Callable[[list], str] = _az) -> dict:
    def in_ws(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=True, cwd=workspace).stdout
    return json.loads(in_ws(["azd", "env", "get-values", "--output", "json"]) or "{}")


def load_prompts(path: Path) -> list[str]:
    prompts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            prompts.append(line)
            continue
        text = row.get("input") or row.get("query") if isinstance(row, dict) else None
        if isinstance(text, str) and text.strip():
            prompts.append(text)
    return prompts


# ---------------------------------------------------------------------------
# Evidence model
# ---------------------------------------------------------------------------

def _phase_stats(phases: Mapping[str, Any], per_request: list) -> list:
    rows = []
    for name, window in phases.items():
        mine = [s for s in per_request if s["phase"] == name]
        ok = [s for s in mine if s["success"]]
        duration = window.get("duration_s") or 0
        rows.append({"phase": name, **window, "successful": len(ok), "failed": len(mine) - len(ok),
                     "client_ms": waterfall._stats(s["client_ms"] for s in ok),
                     "requests_per_min": round(len(mine) / duration * 60, 2) if duration else None})
    return rows


def _steady_rates(phases: list, per_request: list) -> dict:
    steady = max(phases, key=lambda p: (p["concurrency"], p["requests"]), default=None)
    if not steady or not steady.get("duration_s"):
        return {}
    mine = [s for s in per_request if s["phase"] == steady["phase"] and s["joined"]]
    minutes = steady["duration_s"] / 60.0
    tokens = sum((s["input_tokens"] or 0) + (s["output_tokens"] or 0) for s in mine)
    calls = sum(s["model_calls"] or 0 for s in mine)
    return {"phase": steady["phase"], "concurrency": steady["concurrency"],
            "requests_per_min": steady.get("requests_per_min"),
            "model_tokens_per_min": round(tokens / minutes, 1) if minutes else None,
            "model_calls_per_min": round(calls / minutes, 1) if minutes else None}


def build_evidence(*, generated_at: str, agent: str, region: str, profile: Mapping[str, Any], phases: list,
                   runner: hosted_agent.PhaseRunner, summary: Mapping[str, Any], manifest: Mapping[str, Any],
                   model: Mapping[str, Any], mcp_before: Mapping[str, Any], mcp_after: Mapping[str, Any],
                   prices: Mapping[str, Any], compute_prices: Mapping[str, Any], cpu: float, memory_gib: float,
                   idle_timeout_minutes: float, model_status: Optional[Mapping[str, Any]] = None) -> dict:
    per_request = summary["per_request"]
    phase_rows = _phase_stats(runner.phase_windows, per_request)
    steady = _steady_rates(phase_rows, per_request)
    sessions = len({r.session_id for r in runner.records})
    ok = sum(1 for s in per_request if s["success"])
    by_status = (model_status or {}).get("by_status") or {}
    status_429 = by_status.get("429") if (model_status or {}).get("read") else None
    run_minutes = sum(p["duration_s"] for p in runner.phase_windows.values()) / 60.0
    facts = [
        {"label": "Model deployment", "kind": "observed", "source": "Azure Resource Manager",
         "value": f"{model.get('model')} {model.get('version')} {model.get('sku')}, capacity {model.get('capacity')}"
                  if model.get("read") else None},
        {"label": "Model rate limit (tokens per minute)", "kind": "observed", "source": "Azure Resource Manager",
         "value": model.get("tpm")},
        {"label": "Model rate limit (requests per minute)", "kind": "observed", "source": "Azure Resource Manager",
         "value": model.get("rpm")},
        {"label": "MCP server replicas (min-max)", "kind": "observed", "source": "Azure Resource Manager",
         "value": f"{mcp_before.get('min_replicas')}-{mcp_before.get('max_replicas')}" if mcp_before.get("read") else None},
        {"label": "MCP replicas running before / after the run", "kind": "observed", "source": "Azure Resource Manager",
         "value": f"{mcp_before.get('replicas_now')} / {mcp_after.get('replicas_now')}"},
        {"label": "Agent session size", "kind": "observed", "source": "azure.yaml (agent version)",
         "value": f"{cpu:g} vCPU / {memory_gib:g} GiB per session"},
        {"label": "Highest concurrency tested", "kind": "measured", "source": "load run",
         "value": max((p["concurrency"] for p in phase_rows), default=None)},
        {"label": "Hosted agent sessions (sandboxes) started", "kind": "measured", "source": "load run",
         "value": sessions},
        {"label": "Model calls answered HTTP 429 during the run", "kind": "observed",
         "source": "Azure Monitor metric AzureOpenAIRequests by StatusCode (whole deployment)", "value": status_429},
        {"label": "Model calls that failed inside agent turns", "kind": "measured", "source": "chat spans",
         "value": summary["model_throttles"]["failed_calls"]},
        {"label": "Requests rejected with HTTP 429", "kind": "measured", "source": "client",
         "value": summary["errors"].get("throttled", 0)},
        {"label": "Steady phase: model tokens per minute used", "kind": "calculated",
         "source": "chat spans / phase duration", "value": steady.get("model_tokens_per_min")},
        {"label": "Steady phase: model tokens per minute (input incl. cached, plus output) / deployment TPM limit",
         "kind": "calculated", "source": "tokens per minute / TPM limit", "unit": "percent",
         "value": capacity.headroom(steady.get("model_tokens_per_min"), model.get("tpm"))},
        {"label": "Steady phase: model calls per minute / deployment RPM limit", "kind": "calculated",
         "source": "model calls per minute / RPM limit", "unit": "percent",
         "value": capacity.headroom(steady.get("model_calls_per_min"), model.get("rpm"))},
        *capacity.DOCUMENTED,
    ]
    # Per 1,000 successful requests, charging the tokens that failed requests consumed too.
    model_cost = capacity.cost_per_1000(summary.get("token_totals_all") or summary["token_totals"], prices, ok)
    compute = capacity.compute_estimate(sessions=sessions, active_minutes=run_minutes,
                                        idle_timeout_minutes=idle_timeout_minutes, vcpu=cpu, gib=memory_gib,
                                        prices=compute_prices, requests=len(per_request))
    slim = {k: v for k, v in summary.items() if k != "per_request"}
    return {
        "schema": SCHEMA, "generated_at": generated_at,
        "pilot": {"agent": agent, "region": region, "model": model.get("model")},
        "load_manifest": {"path": MANIFEST, "status": manifest["status"],
                          "findings": {f["id"]: f["status"] for f in manifest["findings"]},
                          "budget": manifest["budget"]},
        "profile": {"name": profile.get("name"), "phases": phases, "run_id": runner.run_id,
                    "prompts": len(runner.prompts)},
        "phases": phase_rows,
        "steady": steady,
        "waterfall": slim,
        "segments": [{"key": k, "label": label, "kind": kind, "how": how} for k, label, kind, how in waterfall.SEGMENTS],
        "separability": list(waterfall.SEPARABILITY),
        "capacity": facts,
        "model_status": dict(model_status or {}),
        "cost": {"model_per_1000": model_cost, "compute_per_1000": compute,
                 "model_prices": prices, "compute_prices": compute_prices},
        "per_request": per_request,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _s(ms: Any) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "Not read"
    if unit == "percent" and isinstance(value, (int, float)):
        return f"{value * 100:,.0f}%"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".") if value < 1 else f"{value:,.1f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _short_answers(ev: Mapping[str, Any]) -> list:
    warm = ev["waterfall"]["groups"]["warm"]["segments"]
    cold = ev["waterfall"]["groups"]["cold"]["segments"]
    tok = ev["waterfall"]["tokens_per_request"]
    cost = ev["cost"]["model_per_1000"]
    compute = ev["cost"]["compute_per_1000"]
    err = ev["waterfall"]["error_rate"]
    return [
        ("How long does a request take (warm)?",
         f"p50 {_s(warm['client_ms']['p50'])}, p95 {_s(warm['client_ms']['p95'])}", "measured"),
        ("How long until the first words appear?",
         f"p50 {_s(warm['client_first_token_ms']['p50'])}, p95 {_s(warm['client_first_token_ms']['p95'])}",
         "measured"),
        ("What does a cold start add?",
         f"p50 {_s(cold['cold_start_ms']['p50'])} (cold requests p50 {_s(cold['client_ms']['p50'])} end to end)"
         if cold["cold_start_ms"]["n"] else "Not measured", "calculated" if cold["cold_start_ms"]["n"] else "not-measured"),
        ("Where does the time go (warm, mean)?",
         f"model {_s(warm['model_ms']['mean'])}, tools {_s(warm['tool_ms']['mean'])}, "
         f"runtime {_s(warm['runtime_ms']['mean'])}, endpoint and network {_s(warm['platform_ms']['mean'])}",
         "measured"),
        ("How many fail, and is the model throttling?",
         f"{(err or 0) * 100:.1f}% of requests failed; {_fmt(_429(ev))} model calls answered HTTP 429 "
         f"(Azure Monitor), {_fmt(ev['waterfall']['model_throttles']['failed_calls'])} model calls failed in agent turns",
         "measured"),
        ("Tokens per request",
         f"{_fmt(tok['input']['mean'])} input ({_fmt(tok['cached_input']['mean'])} cached), "
         f"{_fmt(tok['output']['mean'])} output; {_fmt(tok['model_calls']['mean'])} model calls",
         "measured"),
        ("Model cost per 1,000 successful requests",
         f"USD {cost['usd']:,.2f}" if cost.get("usd") is not None else "Not measured", cost["kind"]),
        ("Hosted compute per 1,000 requests (upper bound)",
         f"USD {compute['usd']:,.2f}" if compute.get("usd") is not None else "Not measured", compute["kind"]),
    ]


def _429(ev: Mapping[str, Any]) -> Any:
    status = ev.get("model_status") or {}
    return (status.get("by_status") or {}).get("429", 0) if status.get("read") else None


def render_markdown(ev: Mapping[str, Any]) -> str:
    groups = ev["waterfall"]["groups"]
    lines = [f"# {ev['pilot']['agent']}: performance evidence", "",
             f"Generated {ev['generated_at']} from a load run against the deployed hosted agent "
             f"({ev['pilot']['region']}, model {ev['pilot']['model']}). Labels: **Measured** (observed in this run), "
             "**Calculated** (arithmetic on measured values, formula shown), **Estimate** (not observed), "
             "**Observed in Azure** (read from the resources), **Documented** (Microsoft Learn).", "",
             f"Load manifest: `{ev['load_manifest']['path']}` status **{ev['load_manifest']['status']}** "
             f"({', '.join(f'{k} {v}' for k, v in ev['load_manifest']['findings'].items())}).", "",
             "## The short answer", "", "| Question | Result | Evidence |", "|---|---|---|"]
    lines += [f"| {q} | {v} | {KIND_LABELS[k]} |" for q, v, k in _short_answers(ev)]
    lines += ["", "## Latency waterfall", "",
              f"{groups['warm']['successful']} warm and {groups['cold']['successful']} cold successful requests, "
              f"{ev['waterfall']['joined']} of {ev['waterfall']['requests']} joined to server spans.", "",
              "| Segment | Warm p50 | Warm p95 | Cold p50 | Cold p95 | Evidence | How |", "|---|---|---|---|---|---|---|"]
    for seg in ev["segments"]:
        w, c = groups["warm"]["segments"][seg["key"]], groups["cold"]["segments"][seg["key"]]
        lines.append(f"| {seg['label']} | {_s(w['p50'])} | {_s(w['p95'])} | {_s(c['p50'])} | {_s(c['p95'])} | "
                     f"{KIND_LABELS[seg['kind']]} | {seg['how']} |")
    fm, fmc = groups["warm"]["segments"]["first_mcp_call_ms"], groups["cold"]["segments"]["first_mcp_call_ms"]
    lines.append(f"| First MCP call of the request | {_s(fm['p50'])} | {_s(fm['p95'])} | {_s(fmc['p50'])} | "
                 f"{_s(fmc['p95'])} | Measured | First execute_tool span with an MCP server. |")
    lines += ["", "p50 and p95 are nearest-rank percentiles per segment; they do not add up across segments.", "",
              "## What the traces cannot separate", ""]
    lines += [f"- **{n['segment']}**: {'separated' if n['separated'] else 'not separated'}. {n['why']}"
              for n in ev["separability"]]
    lines += ["", "## Phases", "", "| Phase | Concurrency | Sessions | Requests | Failed | p50 | p95 | Requests/min |",
              "|---|---|---|---|---|---|---|---|"]
    for p in ev["phases"]:
        lines.append(f"| {p['phase']} | {p['concurrency']} | {p['session_mode']}{' (cold)' if p['cold'] else ''} | "
                     f"{p['requests']} | {p['failed']} | {_s(p['client_ms']['p50'])} | {_s(p['client_ms']['p95'])} | "
                     f"{_fmt(p['requests_per_min'])} |")
    lines += ["", "## Capacity", "", "| Fact | Value | Evidence | Source |", "|---|---|---|---|"]
    for f in ev["capacity"]:
        lines.append(f"| {f['label']} | {_fmt(f['value'], f.get('unit', ''))} | {KIND_LABELS[f['kind']]} | {f['source']} |")
    cost, compute = ev["cost"]["model_per_1000"], ev["cost"]["compute_per_1000"]
    prices = ev["cost"]["model_prices"].get("usd_per_1m") or {}
    lines += ["", "## Cost", "",
              f"- **Model, per 1,000 successful requests** ({KIND_LABELS[cost['kind']]}, tokens of failed requests "
              "included): "
              + (f"USD {cost['usd']:,.2f} (this run USD {cost['run_usd']:,.2f}). {cost['formula']}. {cost['assumption']}"
                 if cost.get("usd") is not None else cost.get("why", "")),
              f"- Prices (Retail Prices API, {ev['cost']['model_prices'].get('region')}, USD per 1M tokens): input "
              f"{_fmt(prices.get('input'))}, cached input {_fmt(prices.get('cached_input'))}, output "
              f"{_fmt(prices.get('output'))}.",
              f"- **Hosted compute, per 1,000 requests** ({KIND_LABELS[compute['kind']]}): "
              + (f"USD {compute['usd']:,.2f}. {compute['formula']}. {compute['why']}"
                 if compute.get("usd") is not None else compute.get("why", "")),
              "- Not included: MCP Container App, Application Insights ingestion, storage and network.", "",
              "## Method", "",
              f"Real endpoint, real model deployment and real MCP server; prompts are the pilot's "
              f"{ev['profile']['prompts']} evaluation inputs in rotation, each with `new_conversation: true`. "
              "Cold phase: a new `agent_session_id` per request (new sandbox). Other phases: one session per "
              "virtual user. Segment timings come from the agent's own spans in Application Insights; the client "
              "measures end to end and first token. This is a modest-scale test of one pilot, not a production "
              "SLA or a benchmark of the platform.", ""]
    return "\n".join(lines)


def _pilot_value_style() -> tuple[str, Callable[[str], str]]:
    sys.path.insert(0, str(HERE.parents[1] / "threadlight-production-ready" / "scripts"))
    import pilot_value  # noqa: PLC0415

    css = pilot_value._CSS + """
.k-observed{color:#1d5b8a;background:#e3eef8}.k-documented{color:#2c3a8f;background:#e7eafb}
.bar{display:flex;height:28px;border-radius:6px;overflow:hidden;margin:12px 0 6px;max-width:760px}
.bar span{display:block;height:100%}.legend-bar{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.88rem;color:var(--ink-2)}
.legend-bar i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}
"""

    def badge(kind: str) -> str:
        known = kind if kind in pilot_value.KIND_LABELS else None
        icon = pilot_value._icon(known) if known else pilot_value._icon("target")
        return f'<span class="kind k-{html.escape(kind)}">{icon}{html.escape(KIND_LABELS[kind])}</span>'

    return css, badge


_BAR = (("platform_ms", "Endpoint, sandbox, network", "#9aa3b5"), ("session_start_ms", "Session start", "#c9a227"),
        ("model_ms", "Model", "#3a55e0"), ("tool_ms", "Tools", "#1f7a43"), ("runtime_ms", "Runtime", "#6a4bc2"))


def render_html(ev: Mapping[str, Any]) -> str:
    css, badge = _pilot_value_style()
    e = lambda t: html.escape(str(t), quote=True)  # noqa: E731
    groups = ev["waterfall"]["groups"]
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f"<title>{e(ev['pilot']['agent'])}: performance evidence</title>",
           f"<style>{css}</style></head><body><main class=\"wrap\">", '<header class="top">',
           f"<h1>{e(ev['pilot']['agent'])}: how it performs under load</h1>",
           f'<p class="lede">A load run against the deployed hosted agent in {e(ev["pilot"]["region"])} '
           f"(model {e(ev['pilot']['model'])}), with the time of every request split by the agent's own traces.</p>",
           f'<p class="meta">Generated {e(ev["generated_at"])}. Load manifest status '
           f"<strong>{e(ev['load_manifest']['status'])}</strong>. Every value carries its evidence label; "
           "anything the traces cannot split says so.</p></header>",
           '<section><h2>The short answer</h2><table class="answer"><thead><tr><th>Question</th><th>Result</th>'
           "<th>Evidence</th></tr></thead><tbody>"]
    for q, v, k in _short_answers(ev):
        out.append(f'<tr><td class="q">{e(q)}</td><td class="a"><span class="val">{e(v)}</span></td><td>{badge(k)}</td></tr>')
    out.append("</tbody></table></section>")
    warm = groups["warm"]["segments"]
    total = sum((warm[k]["mean"] or 0) for k, *_ in _BAR) or 1
    out.append('<section><h2>Where a warm request spends its time</h2>'
               '<p class="intro">Mean of each segment over warm successful requests.</p><div class="bar" role="img" '
               'aria-label="Share of request time per segment">')
    for key, label, color in _BAR:
        share = (warm[key]["mean"] or 0) / total * 100
        out.append(f'<span style="width:{share:.2f}%;background:{color}" title="{e(label)}"></span>')
    out.append('</div><div class="legend-bar">')
    for key, label, color in _BAR:
        out.append(f'<span><i style="background:{color}"></i>{e(label)} {e(_s(warm[key]["mean"]))}</span>')
    out.append("</div>")
    out.append('<table class="items"><thead><tr><th>Segment</th><th>Warm p50 / p95</th><th>Cold p50 / p95</th>'
               "<th>Evidence and how</th></tr></thead><tbody>")
    for seg in ev["segments"]:
        w, c = groups["warm"]["segments"][seg["key"]], groups["cold"]["segments"][seg["key"]]
        out.append(f'<tr><td class="label">{e(seg["label"])}</td><td class="num">{e(_s(w["p50"]))} / {e(_s(w["p95"]))}</td>'
                   f'<td class="num">{e(_s(c["p50"]))} / {e(_s(c["p95"]))}</td><td>{badge(seg["kind"])}'
                   f'<span class="detail">{e(seg["how"])}</span></td></tr>')
    out.append("</tbody></table></section>")
    out.append('<section><h2>What the traces cannot separate</h2><ol class="open">')
    for n in ev["separability"]:
        out.append(f"<li><div><strong>{e(n['segment'])}: {'separated' if n['separated'] else 'not separated'}</strong>"
                   f"<span>{e(n['why'])}</span></div></li>")
    out.append("</ol></section>")
    out.append('<section><h2>Phases</h2><table class="items"><thead><tr><th>Phase</th><th>Load</th>'
               "<th>Result</th><th>Latency p50 / p95</th></tr></thead><tbody>")
    for p in ev["phases"]:
        out.append(f'<tr><td class="label">{e(p["phase"])}</td><td>{p["concurrency"]} concurrent, {p["requests"]} requests, '
                   f'{e(p["session_mode"])} sessions{" (cold)" if p["cold"] else ""}</td>'
                   f'<td>{p["successful"]} ok, {p["failed"]} failed</td>'
                   f'<td class="num">{e(_s(p["client_ms"]["p50"]))} / {e(_s(p["client_ms"]["p95"]))}</td></tr>')
    out.append("</tbody></table></section>")
    out.append('<section><h2>Capacity</h2><table class="items"><thead><tr><th>Fact</th><th>Value</th>'
               "<th>Evidence and source</th></tr></thead><tbody>")
    for f in ev["capacity"]:
        src = f["source"]
        link = f'<a href="{e(src)}">{e(src)}</a>' if src.startswith("https://") else e(src)
        out.append(f'<tr><td class="label">{e(f["label"])}</td><td>{e(_fmt(f["value"], f.get("unit", "")))}</td>'
                   f'<td>{badge(f["kind"])}<span class="src">{link}</span></td></tr>')
    out.append("</tbody></table></section>")
    cost, compute = ev["cost"]["model_per_1000"], ev["cost"]["compute_per_1000"]
    out.append('<section><h2>Cost per 1,000 requests</h2><table class="items"><tbody>')
    for label, item, extra in (("Model tokens (per 1,000 successful requests)", cost, cost.get("assumption", "")), ("Hosted compute", compute, compute.get("why", ""))):
        value = f"USD {item['usd']:,.2f}" if item.get("usd") is not None else "Not measured"
        out.append(f'<tr><td class="label">{e(label)}</td><td><span class="val">{e(value)}</span></td>'
                   f'<td>{badge(item["kind"])}<span class="detail">{e(item.get("formula", ""))} {e(extra)}</span></td></tr>')
    out.append('</tbody></table><p class="intro">Not included: MCP Container App, Application Insights ingestion, '
               "storage and network. Prices: Azure Retail Prices API.</p></section>")
    out.append("<footer>Produced by threadlight-loadtest (perf_evidence.py) from the run's own records and the "
               "agent's traces. A modest-scale test of one pilot, not a production SLA.</footer></main></body></html>")
    return "\n".join(out) + "\n"


def write_outputs(workspace: Path, evidence: Mapping[str, Any]) -> list[Path]:
    paths = {workspace / EVIDENCE: json.dumps(evidence, indent=2) + "\n",
             workspace / REPORT_MD: render_markdown(evidence) + "\n", workspace / REPORT_HTML: render_html(evidence)}
    for path, text in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(paths)


# ---------------------------------------------------------------------------
# Live run
# ---------------------------------------------------------------------------

def wait_and_join(fetch: Callable[[list], list], records: list, *, wait_s: float, poll_s: float = 30.0,
                  sleep: Callable[[float], None] = time.sleep) -> list:
    """Poll until every successful request's spans arrived, growth stops, or ``wait_s`` passes."""
    wanted = {r.invocation_id for r in records if r.invocation_id and r.success}
    spans, last, waited = [], -1, 0.0
    while True:
        spans = fetch(sorted({r.invocation_id for r in records if r.invocation_id}))
        joined = {s["invocation_id"] for s in spans if s.get("name") == waterfall.TURN}
        if wanted <= joined or waited >= wait_s or (len(joined) == last and last > 0 and waited >= 2 * poll_s):
            return spans
        last = len(joined)
        sleep(poll_s)
        waited += poll_s


def evidence_from_raw(raw: Mapping[str, Any], run: Callable[[list], str]) -> dict:
    """Rebuild the evidence from ``specs/perf-run-raw.json`` (records, spans and run inputs)."""
    inputs = raw["inputs"]
    records = [hosted_agent.RequestRecord(**{k: v for k, v in r.items() if k != "success"}) for r in raw["records"]]
    runner = hosted_agent.PhaseRunner(invoke=lambda p, s: {}, prompts=["-"] * int(inputs["prompts"]),
                                      run_id=inputs["run_id"], records=records,
                                      phase_windows=raw["phase_windows"])
    summary = waterfall.summarize([r.as_dict() for r in records], raw["spans"])
    stamps = sorted(r.started_at for r in records)
    end = max(s["start_ms"] + s["duration_ms"] for s in raw["spans"]) if raw["spans"] else None
    window_end = (datetime.fromtimestamp(end / 1000, timezone.utc).replace(microsecond=0).isoformat()
                  if end else stamps[-1])
    model_status = (capacity.model_requests_by_status(run, account_id=inputs["account_id"], start=stamps[0],
                                                      end=window_end)
                    if inputs.get("account_id") and stamps else {"read": False})
    return build_evidence(generated_at=_now(), agent=inputs["agent"], region=inputs["region"],
                          profile=inputs["profile"], phases=inputs["phases"], runner=runner, summary=summary,
                          manifest=inputs["manifest"], model=inputs["model"], mcp_before=inputs["mcp_before"],
                          mcp_after=inputs["mcp_after"], prices=inputs["prices"],
                          compute_prices=inputs["compute_prices"], cpu=inputs["cpu"],
                          memory_gib=inputs["memory_gib"], idle_timeout_minutes=inputs["idle_timeout_minutes"],
                          model_status=model_status)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--agent")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--from-raw", action="store_true",
                        help="rebuild evidence and report from specs/perf-run-raw.json (reads Azure Monitor, no requests)")
    parser.add_argument("--prompts", type=Path, help="JSONL with an 'input' per line (default tests/eval_dataset.jsonl)")
    parser.add_argument("--budget-ceiling-usd", type=float)
    parser.add_argument("--tokens-per-request-estimate", type=float,
                        help="for the budget projection, e.g. from a single smoke request")
    parser.add_argument("--max-concurrency", type=int, default=10)
    parser.add_argument("--steady-requests", type=int, default=200)
    parser.add_argument("--cold-requests", type=int, default=3)
    parser.add_argument("--ramp-requests", type=int, default=10)
    parser.add_argument("--endpoint-class", default="non-production", choices=("non-production", "production"))
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--wait-telemetry-s", type=float, default=600)
    parser.add_argument("--request-deadline-s", type=float, default=600,
                        help="client stops reading a request after this many seconds (error class 'deadline')")
    parser.add_argument("--idle-timeout-minutes", type=float, default=15.0)
    parser.add_argument("--cpu", type=float, default=1.0)
    parser.add_argument("--memory-gib", type=float, default=2.0)
    args = parser.parse_args(argv)
    ws = args.workspace.resolve()
    if args.render_only:
        write_outputs(ws, json.loads((ws / EVIDENCE).read_text(encoding="utf-8")))
        print(f"rendered {REPORT_MD} and {REPORT_HTML}")
        return 0
    if args.from_raw:
        write_outputs(ws, evidence_from_raw(json.loads((ws / RAW).read_text(encoding="utf-8")), _az))
        print(f"rebuilt {EVIDENCE}, {REPORT_MD} and {REPORT_HTML} from {RAW}")
        return 0
    if not args.agent or args.budget_ceiling_usd is None or args.tokens_per_request_estimate is None:
        parser.error("--agent, --budget-ceiling-usd and --tokens-per-request-estimate are required for a run")

    env = azd_env(ws)
    endpoint = env["FOUNDRY_PROJECT_ENDPOINT"]
    rg, region = env["AZURE_RESOURCE_GROUP"], env.get("AZURE_LOCATION", "")
    account = endpoint.split("//", 1)[1].split(".", 1)[0]
    deployment = env.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "")
    mcp_app = (env.get("MCP_SERVER_FQDN") or "").split(".", 1)[0]
    appi = env["APPLICATIONINSIGHTS_RESOURCE_ID"]

    model = capacity.model_deployment(_az, resource_group=rg, account=account, deployment=deployment)
    mcp_before = capacity.container_app(_az, resource_group=rg, name=mcp_app) if mcp_app else {}
    prices = capacity.model_prices(model.get("model") or deployment, region, model.get("sku") or "GlobalStandard")
    compute_prices = capacity.hosted_compute_prices(region)

    phases = hosted_agent.default_phases(max_concurrency=args.max_concurrency, steady_requests=args.steady_requests,
                                         cold_requests=args.cold_requests, ramp_requests=args.ramp_requests)
    blended = (prices["usd_per_1m"].get("input") or 0) / 1000.0
    profile = {"name": f"{args.agent}-perf", "endpoint": {"url": endpoint, "credential_ref": "entra:azure-cli"},
               "request_count": hosted_agent.phase_request_count(phases),
               "tokens_per_request_estimate": args.tokens_per_request_estimate,
               "price_per_1k_tokens_usd": blended, "virtual_users": args.max_concurrency}

    import requests  # noqa: PLC0415
    from azure.identity import AzureCliCredential  # noqa: PLC0415

    tokens = hosted_agent.TokenProvider(AzureCliCredential())
    url = hosted_agent.invocations_url(endpoint, args.agent)
    local = __import__("threading").local()

    def invoke(prompt: str, session_id: str) -> dict:
        if not hasattr(local, "http"):
            local.http = requests.Session()
        return hosted_agent.invoke_once(local.http, url, tokens(), prompt, session_id,
                                        deadline_s=args.request_deadline_s)

    runner = hosted_agent.PhaseRunner(invoke=invoke, prompts=load_prompts(args.prompts or ws / "tests" / "eval_dataset.jsonl"))
    holder: dict[str, Any] = {}

    def join(records: list) -> dict:
        spans = wait_and_join(lambda ids: waterfall.fetch_spans(_az, appi, ids), records, wait_s=args.wait_telemetry_s)
        # Raw join input (timings, ids and span metadata only) so a report can be rebuilt without a rerun.
        raw = ws / RAW
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(json.dumps({"records": [r.as_dict() for r in records], "spans": spans,
                                   "phase_windows": runner.phase_windows}) + "\n", encoding="utf-8")
        holder["summary"] = waterfall.summarize([r.as_dict() for r in records], spans)
        return waterfall.join_tokens(holder["summary"])

    adapter = hosted_agent.HostedAgentAdapter(runner, phases, join=join)
    manifest = run_loadtest(profile=profile, budget_ceiling_usd=args.budget_ceiling_usd,
                            endpoint_class=args.endpoint_class, allow_production=args.allow_production,
                            adapter=adapter, generated_at=_now())
    write_load_manifest(ws / MANIFEST, manifest)
    print(f"load manifest: {manifest['status']} ({budget_line(manifest)})")
    if "summary" not in holder:
        print("no requests were sent; see the load manifest findings")
        return 1
    mcp_after = capacity.container_app(_az, resource_group=rg, name=mcp_app) if mcp_app else {}
    raw = json.loads((ws / RAW).read_text(encoding="utf-8"))
    raw["inputs"] = {"agent": args.agent, "region": region, "profile": profile, "phases": phases,
                     "run_id": runner.run_id, "prompts": len(runner.prompts), "manifest": manifest, "model": model,
                     "mcp_before": mcp_before, "mcp_after": mcp_after, "prices": prices,
                     "compute_prices": compute_prices, "cpu": args.cpu, "memory_gib": args.memory_gib,
                     "idle_timeout_minutes": args.idle_timeout_minutes,
                     "account_id": f"/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{rg}/providers/"
                                   f"Microsoft.CognitiveServices/accounts/{account}"}
    (ws / RAW).write_text(json.dumps(raw) + "\n", encoding="utf-8")
    evidence = evidence_from_raw(raw, _az)
    for path in write_outputs(ws, evidence):
        print(f"wrote {path.relative_to(ws)}")
    return 0 if manifest["status"] == "complete" else 3


def budget_line(manifest: Mapping[str, Any]) -> str:
    b = manifest["budget"]
    return f"projected USD {b['projected_usd']} vs ceiling USD {b['ceiling_usd']}"


if __name__ == "__main__":
    raise SystemExit(main())
