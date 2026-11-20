#!/usr/bin/env python3
"""router-bench `workload` mode: model router vs fixed models on a deterministic task mix.

Each arm sends the same tasks through the Responses API. Router arms send the
``Foundry-Features: ModelRouterControls=V1Preview`` header and a
``routing_config`` with the arm's mode and ``session_affinity.strategy = none``
so repetitions are comparable. Every call records the served model (``model``,
falling back to the last ``routing_trace`` attempt), token usage including
cached input, the routing trace and the ``x-ratelimit-key`` header. HTTP 429
and 5xx are retried with ``retry-after`` and a bounded attempt count; 429s are
counted per call because router-managed capacity can throttle.

Cost is an ESTIMATE: tokens x list price from ``prices.py`` (seed or Retail
Prices API) plus the routing fee on router arms. It is not billed spend.

Docs: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-routing-controls
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import http.client
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prices as prices_mod  # noqa: E402
import score as score_mod  # noqa: E402
import workload_tasks as tasks_mod  # noqa: E402

SCHEMA = "threadlight-router-workload/v1"
ROUTER_HEADER = {"Foundry-Features": "ModelRouterControls=V1Preview"}
RETRYABLE = (429, 500, 502, 503, 504)
DOMINANT_SHARE = 0.9  # a router arm sending >= 90% of calls to one model gains little


class HttpResult:
    """Minimal transport result: status, parsed JSON body, response headers."""

    def __init__(self, status: int, body: dict[str, Any], headers: dict[str, str]):
        self.status, self.body, self.headers = status, body, headers


Post = Callable[[dict[str, Any], dict[str, str]], Any]


def router_arm(name: str, deployment: str, mode: str = "balanced",
               models: list[str] | None = None) -> dict[str, Any]:
    if not deployment or not deployment.strip():
        raise ValueError("router arm needs the model router deployment name (or 'auto')")
    rc: dict[str, Any] = {"mode": mode, "session_affinity": {"strategy": "none"}}
    if models:
        rc["models"] = list(models)
    return {"arm": name, "model": deployment.strip(), "router": True, "routing_config": rc}


def fixed_arm(deployment: str, name: str | None = None) -> dict[str, Any]:
    return {"arm": name or deployment, "model": deployment, "router": False}


def build_request(arm: dict[str, Any], task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    body: dict[str, Any] = {"model": arm["model"], "input": task["input"], "store": False}
    if task.get("system"):
        body["instructions"] = task["system"]
    if task.get("tools"):
        body["tools"] = task["tools"]
    headers: dict[str, str] = {}
    if arm.get("router"):
        headers.update(ROUTER_HEADER)
        body["routing_config"] = json.loads(json.dumps(arm["routing_config"]))
    return body, headers


def _router_details(resp: dict[str, Any]) -> dict[str, Any]:
    return ((resp.get("model_selection_details") or {}).get("model_router_details")) or {}


def _trace_attempts(resp: dict[str, Any]) -> list[str]:
    return [a.get("model") for e in _router_details(resp).get("routing_trace") or []
            for a in e.get("attempts") or [] if a.get("model")]


def served_model(resp: dict[str, Any]) -> tuple[str, str]:
    """(served model, evidence source). ``model`` is authoritative unless it
    names the router itself; then the last ``routing_trace`` attempt is used."""
    model = str(resp.get("model") or "")
    if model and not model.lower().startswith(("model-router", "auto")):
        return prices_mod.base_name(model), "model"
    attempts = _trace_attempts(resp)
    if attempts:
        return prices_mod.base_name(attempts[-1]), "routing_trace"
    return "model-router", "unattributed"


def call_with_retries(post: Post, body: dict[str, Any], headers: dict[str, str],
                      max_attempts: int = 8, sleep: Callable[[float], None] = time.sleep,
                      max_backoff: float = 65.0) -> dict[str, Any]:
    """POST with bounded retries on 429/5xx; never retries other client errors."""
    keys: list[str] = []
    throttled = retries = 0
    res: Any = None
    for attempt in range(max_attempts):
        res = post(body, headers)
        hdrs = {k.lower(): v for k, v in (res.headers or {}).items()}
        if hdrs.get("x-ratelimit-key"):
            keys.append(hdrs["x-ratelimit-key"])
        if res.status == 429:
            throttled += 1
        if res.status in RETRYABLE and attempt < max_attempts - 1:
            retries += 1
            ra = hdrs.get("retry-after-ms")
            wait = float(ra) / 1000 if ra else float(hdrs.get("retry-after") or 0) or 2.0 * 2 ** attempt
            sleep(min(max_backoff, wait))
            continue
        break
    out = {"status": res.status, "body": res.body, "retries": retries, "throttled": throttled,
           "ratelimit_key": keys[-1] if keys else None, "ratelimit_keys_seen": keys}
    if res.status != 200:
        out["error"] = (res.body or {}).get("error") or {"code": res.status}
    return out


def _parse_output(resp: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text, tools = [], []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            text += [c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text"]
        elif item.get("type") == "function_call":
            tools.append({"name": item.get("name"), "arguments": item.get("arguments")})
    return "".join(text), tools


def run_task(arm: dict[str, Any], task: dict[str, Any], rep: int, post: Post,
             sleep: Callable[[float], None] = time.sleep, exec_code: bool = True,
             max_attempts: int = 8) -> dict[str, Any]:
    body, headers = build_request(arm, task)
    t0 = time.perf_counter()
    call = call_with_retries(post, body, headers, max_attempts=max_attempts, sleep=sleep)
    rec: dict[str, Any] = {"arm": arm["arm"], "task": task["id"], "cat": task["cat"], "rep": rep,
                           "router": bool(arm.get("router")), "status": call["status"],
                           "retries": call["retries"], "throttled": call["throttled"],
                           "ratelimit_key": call["ratelimit_key"],
                           "latency_ms": round((time.perf_counter() - t0) * 1000)}
    if call["status"] != 200:
        rec["error"] = call["error"]
        return rec
    resp = call["body"] or {}
    usage = resp.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    model, source = served_model(resp)
    trace = _router_details(resp).get("routing_trace") or []
    text, tools = _parse_output(resp)
    rec.update({
        "model_field": resp.get("model"), "model": model, "served_from": source,
        "attempts": _trace_attempts(resp),
        "route_ms": sum(e.get("latency_ms", 0) for e in trace) if trace else None,
        "mode": _router_details(resp).get("mode"),
        "in": usage.get("input_tokens", 0), "cached": details.get("cached_tokens", 0) or 0,
        "cache_write": details.get("cache_write_tokens", 0) or 0,
        "out": usage.get("output_tokens", 0),
        "reasoning": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
        "score": tasks_mod.grade(task, text, tools, exec_code=exec_code),
    })
    return rec


def entra_token_provider(resource: str = "https://ai.azure.com") -> Callable[[], str]:
    """Azure CLI Entra token for Foundry, cached for 30 minutes."""
    state: dict[str, Any] = {"v": None, "t": 0.0}
    lock = threading.Lock()

    def get() -> str:
        with lock:
            if not state["v"] or time.time() - state["t"] > 1800:
                state["v"] = subprocess.check_output(
                    ["az", "account", "get-access-token", "--resource", resource,
                     "--query", "accessToken", "-o", "tsv"], text=True).strip()
                state["t"] = time.time()
            return state["v"]
    return get


def http_post(endpoint: str, token: Callable[[], str], timeout: float = 300.0) -> Post:
    """Responses API transport (stdlib) with a finite per-request timeout."""
    def post(body: dict[str, Any], headers: dict[str, str]) -> HttpResult:
        h = {"Content-Type": "application/json", "Authorization": f"Bearer {token()}", **headers}
        req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return HttpResult(r.status, json.load(r), dict(r.headers))
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="ignore")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {"error": {"code": e.code, "message": raw[:300]}}
            return HttpResult(e.code, parsed, dict(e.headers or {}))
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            # Connection resets, truncated bodies and timeouts are transient:
            # report them as 503 so call_with_retries retries them.
            return HttpResult(503, {"error": {"code": "network", "message": str(e)[:300]}}, {})
    return post


def run_workload(arms: list[dict[str, Any]], tasks: list[dict[str, Any]], reps: int, post: Post,
                 concurrency: int = 4, exec_code: bool = True,
                 on_record: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    lock = threading.Lock()

    def one(job: tuple[dict[str, Any], dict[str, Any], int]) -> None:
        rec = run_task(job[0], job[1], job[2], post, exec_code=exec_code)
        with lock:
            records.append(rec)
            if on_record:
                on_record(rec)

    jobs = [(a, t, r) for a in arms for r in range(reps) for t in tasks]
    with cf.ThreadPoolExecutor(max(1, concurrency)) as ex:
        list(ex.map(one, jobs))
    return records


def load_records(path: Path | str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _served(rec: dict[str, Any]) -> str:
    if rec.get("model"):
        return rec["model"]
    fake = {"model": rec.get("model_field"), "model_selection_details": {"model_router_details": {
        "routing_trace": [{"attempts": [{"model": m} for m in rec.get("attempts") or []]}]}}}
    return served_model(fake)[0]


def summarize(records: list[dict[str, Any]], prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Per-arm quality, estimated cost per 1k tasks, served-model mix and throttling."""
    by_arm: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in records:
        by_arm[r["arm"]].append(r)
    fee_rate = prices.get(prices_mod.ROUTER_FEE_KEY, {}).get("input", 0.0)
    arms: dict[str, Any] = {}
    for arm, rows in sorted(by_arm.items()):
        ok = [r for r in rows if "error" not in r]
        router = any(r.get("router") for r in rows)
        cost, fee, unpriced = 0.0, 0.0, set()
        served = collections.Counter()
        per_task: dict[str, set[str]] = collections.defaultdict(set)
        for r in ok:
            model = _served(r)
            served[model] += 1
            per_task[r["task"]].add(model)
            usage = {model: {"input": r.get("in", 0), "cached_input": r.get("cached", 0),
                             "cache_write": r.get("cache_write", 0), "output": r.get("out", 0)}}
            unpriced.update(score_mod.unpriced(usage, prices))
            cost += score_mod.cost_of(usage, prices, router=bool(r.get("router")))
            if r.get("router"):
                fee += r.get("in", 0) / 1_000_000 * fee_rate
        graded = [r["score"] for r in ok if r.get("score") is not None]
        top_model, top_n = (served.most_common(1)[0] if served else (None, 0))
        top_share = round(top_n / len(ok), 4) if ok else 0.0
        arms[arm] = {
            "router": router, "calls": len(rows), "ok": len(ok), "errors": len(rows) - len(ok),
            "throttled_calls": sum(1 for r in rows if r.get("throttled")),
            "quality": round(sum(graded) / len(graded), 4) if graded else None,
            "cost_usd": round(cost, 6),
            "cost_per_1k_usd": round(1000 * cost / len(ok), 4) if ok else None,
            "router_fee_usd": round(fee, 6),
            "served_models": dict(served.most_common()),
            "top_model": top_model, "top_share": top_share,
            "dominant_model": top_model if router and top_share >= DOMINANT_SHARE else None,
            "tasks_switching_model": sum(1 for v in per_task.values() if len(v) > 1),
            "model_field_fallbacks": sum(1 for r in ok if r.get("served_from") == "routing_trace"
                                         or str(r.get("model_field") or "").startswith("model-router")),
            "ratelimit_keys": dict(collections.Counter(r["ratelimit_key"] for r in rows
                                                       if r.get("ratelimit_key"))),
            "unpriced_models": sorted(unpriced),
            "unverified_price_models": sorted(m for m in served if m in prices_mod.UNVERIFIED_SEED
                                             and prices.get(m) == prices_mod.SEED_PRICES.get(m)),
            "cost_complete": not unpriced,
        }
    order = sorted((a for a in arms if arms[a]["cost_per_1k_usd"] is not None),
                   key=lambda a: arms[a]["cost_per_1k_usd"])
    return {"schema": SCHEMA, "prices_as_of": prices_mod.PRICES_AS_OF,
            "cost_basis": "estimate: tokens x list price (+ routing fee on router arms); not billed spend",
            "dominant_share_threshold": DOMINANT_SHARE, "arms": arms, "cost_order": order}


def render_markdown(summary: dict[str, Any]) -> str:
    out = ["# Router-bench workload", "",
           f"Estimated token cost ({summary['cost_basis']}; prices as of {summary['prices_as_of']}).", "",
           "| Arm | Router | Quality | $ / 1k tasks | Routing fee $ | Errors | 429 calls | Top model (share) |",
           "|---|---|---|---|---|---|---|---|"]
    for arm in summary["cost_order"] + [a for a in summary["arms"] if a not in summary["cost_order"]]:
        a = summary["arms"][arm]
        q = "n/a" if a["quality"] is None else f"{100 * a['quality']:.0f}%"
        c = "n/a" if a["cost_per_1k_usd"] is None else f"{a['cost_per_1k_usd']:.3f}"
        flag = "" if a["cost_complete"] else " (unpriced: " + ", ".join(a["unpriced_models"]) + ")"
        if a["unverified_price_models"]:
            flag += " (unverified price: " + ", ".join(a["unverified_price_models"]) + ")"
        out.append(f"| {arm} | {'yes' if a['router'] else 'no'} | {q} | {c}{flag} | "
                   f"{a['router_fee_usd']:.5f} | {a['errors']} | {a['throttled_calls']} | "
                   f"{a['top_model']} ({100 * a['top_share']:.0f}%) |")
    dominant = {k: v["dominant_model"] for k, v in summary["arms"].items() if v["dominant_model"]}
    if dominant:
        out += ["", "## Decision rule", ""]
        for arm, model in dominant.items():
            out.append(f"- `{arm}` sent >= {100 * summary['dominant_share_threshold']:.0f}% of calls to "
                       f"`{model}`: call `{model}` directly and drop the routing fee.")
    return "\n".join(out) + "\n"


RECORDS_FILE = "router-workload-records.jsonl"


def record_appender(outdir: Path) -> Callable[[dict[str, Any]], None]:
    """Append each record to RECORDS_FILE as soon as it completes, so an
    interrupted live run keeps everything already paid for."""
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / RECORDS_FILE
    path.write_text("", encoding="utf-8")

    def append(rec: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return append


def write_outputs(summary: dict[str, Any], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "router-workload.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (outdir / "router-workload.md").write_text(render_markdown(summary), encoding="utf-8")
