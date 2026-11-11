"""Latency waterfall for a Foundry hosted agent (GHCP runtime) from its own traces.

Joins each client-side request record (``hosted_agent.RequestRecord``) to the
spans the agent exported to the project's Application Insights:

* the host's server span (``requests`` table, Invocations protocol library),
  https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry
* the GHCP container's per-turn spans (``dependencies`` table):
  ``threadlight.copilot.turn`` (carries ``threadlight.invocation_id``),
  ``threadlight.copilot.session_open``, ``chat <model>`` and
  ``execute_tool <tool>``, built from the Copilot SDK's session events.

Per request it derives segments. Measured ones come straight from a span or
the client clock; calculated ones are arithmetic on measured ones and say so.
A segment the traces cannot separate is listed in ``SEPARABILITY`` and never
estimated.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

TURN = "threadlight.copilot.turn"
SESSION_OPEN = "threadlight.copilot.session_open"

SEGMENTS = (
    # key, label, evidence kind, how it is obtained
    ("client_ms", "End to end (client)", "measured", "Client clock: request sent to stream done."),
    ("platform_ms", "Foundry endpoint, sandbox and network", "calculated",
     "client_ms - container_ms: time outside the agent container."),
    ("container_ms", "Agent container (host span)", "measured", "Invocations host server span duration."),
    ("session_start_ms", "Copilot session start", "measured", "threadlight.copilot.session_open span."),
    ("model_ms", "Model calls", "measured", "Wall-clock union of chat spans (SDK-reported call duration)."),
    ("tool_ms", "Tool calls (all)", "measured", "Wall-clock union of execute_tool spans (MCP and built-in)."),
    ("mcp_ms", "MCP server calls", "measured", "Wall-clock union of execute_tool spans with an MCP server."),
    ("runtime_ms", "Agent runtime overhead", "calculated",
     "container_ms - session_start_ms - model_ms - tool_ms (Copilot CLI orchestration, host, streaming)."),
    ("client_first_token_ms", "Time to first token (client)", "measured", "Client clock: first streamed text."),
    ("model_first_token_ms", "Time to first token (first model call)", "measured",
     "SDK time_to_first_token of the first chat span."),
    ("cold_start_ms", "Cold start (cold phase only)", "calculated",
     "platform_ms + session_start_ms when the Copilot client started in this request."),
)

SEPARABILITY = (
    {"segment": "platform_ms", "separated": False,
     "why": "Foundry endpoint routing, sandbox provisioning or resume and client network round trip are one "
            "number: no span covers the time before the container receives the request. The cold phase shows "
            "how much sandbox start adds."},
    {"segment": "model_ms", "separated": True,
     "why": "Each model call is a chat span with the SDK-reported duration, time to first token and tokens."},
    {"segment": "model_queueing", "separated": False,
     "why": "Model service queueing and inference are not split: the chat span is the SDK's call duration."},
    {"segment": "tool_ms", "separated": True,
     "why": "Each tool call is an execute_tool span from the SDK's start and complete events."},
    {"segment": "mcp_cold_start", "separated": False,
     "why": "An MCP Container App cold start (scale from zero) has no span of its own. The Copilot CLI connects "
            "to MCP servers when it opens the session, so a scale-from-zero start lands in session start (or in "
            "the first tool call if the connection is lazy); compare cold and warm session start and first MCP call."},
    {"segment": "runtime_ms", "separated": False,
     "why": "Copilot CLI orchestration, the host framework and SSE streaming are one residual."},
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


# ---------------------------------------------------------------------------
# Fetch (App Insights through az CLI; the runner is injected for tests)
# ---------------------------------------------------------------------------

def build_queries(invocation_ids: Iterable[str], *, hours: int = 6, batch: int = 200) -> list[str]:
    ids = sorted({i for i in invocation_ids if isinstance(i, str) and _SAFE_ID.match(i)})
    queries = []
    for start in range(0, len(ids), batch):
        chunk = json.dumps(ids[start:start + batch])
        queries.append(
            f"let inv = dynamic({chunk});"
            f" let ops = dependencies | where timestamp > ago({int(hours)}h) | where name == '{TURN}'"
            " | extend invocation_id = tostring(customDimensions['threadlight.invocation_id'])"
            " | where invocation_id in (inv) | project operation_Id, invocation_id;"
            f" union withsource=table requests, dependencies | where timestamp > ago({int(hours)}h)"
            " | join kind=inner ops on operation_Id"
            " | project table, invocation_id, name, timestamp, duration, success, resultCode, customDimensions")
    return queries


def _epoch_ms(stamp: str) -> float:
    text = stamp.replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        digits = re.match(r"\d+", rest).group(0)
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest[len(digits):]}"
    return datetime.fromisoformat(text).timestamp() * 1000


def fetch_spans(run: Callable[[list], str], app_insights_id: str, invocation_ids: Sequence[str], *,
                hours: int = 6) -> list[dict]:
    spans: list[dict] = []
    for query in build_queries(invocation_ids, hours=hours):
        payload = json.loads(run(["az", "monitor", "app-insights", "query", "--apps", app_insights_id,
                                  "--offset", f"{int(hours)}h", "--analytics-query", query, "-o", "json"]) or "{}")
        for table in payload.get("tables", []):
            names = [c["name"] for c in table.get("columns", [])]
            for row in table.get("rows", []):
                item = dict(zip(names, row))
                dims = item.get("customDimensions") or {}
                if isinstance(dims, str):
                    try:
                        dims = json.loads(dims) if dims else {}
                    except ValueError:
                        dims = {}
                spans.append({"table": item.get("table"), "invocation_id": item.get("invocation_id"),
                              "name": item.get("name"), "start_ms": _epoch_ms(str(item.get("timestamp"))),
                              "duration_ms": float(item.get("duration") or 0.0),
                              "success": str(item.get("success")).lower() == "true",
                              "resultCode": str(item.get("resultCode") or ""), "customDimensions": dims})
    return spans


# ---------------------------------------------------------------------------
# Per-request segments
# ---------------------------------------------------------------------------

def _int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _union_ms(intervals: Iterable[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for start, stop in sorted(intervals):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def _status(span: Mapping[str, Any]) -> str:
    dims = span.get("customDimensions") or {}
    value = dims.get("threadlight.model.status_code") or dims.get("http.response.status_code") or ""
    return str(value).split(".")[0]


def _op(span: Mapping[str, Any]) -> str:
    return str((span.get("customDimensions") or {}).get("gen_ai.operation.name") or "")


def request_segments(record: Mapping[str, Any], spans: Sequence[Mapping[str, Any]]) -> dict:
    inv = record.get("invocation_id")
    mine = [s for s in spans if inv and s.get("invocation_id") == inv]
    servers = [s for s in mine if s.get("table") == "requests"]
    turns = [s for s in mine if s.get("name") == TURN]
    seg: dict[str, Any] = {"invocation_id": inv, "phase": record.get("phase"), "cold": bool(record.get("cold_phase")),
                           "success": bool(record.get("success")), "client_ms": record.get("client_ms"),
                           "client_first_token_ms": record.get("first_token_ms"), "joined": bool(servers and turns)}
    for key in ("container_ms", "platform_ms", "session_start_ms", "model_ms", "tool_ms", "runtime_ms",
                "model_first_token_ms", "cold_start_ms", "input_tokens", "output_tokens", "cached_input_tokens",
                "reasoning_tokens", "model_calls", "tool_calls", "mcp_ms", "mcp_calls", "model_429", "model_failed",
                "first_tool_ms"):
        seg[key] = None
    if not seg["joined"]:
        return seg
    server = next((s for s in servers if s.get("name") == "invoke_agent"), max(servers, key=lambda s: s["duration_ms"]))
    container = server["duration_ms"]
    opens = [s for s in mine if s.get("name") == SESSION_OPEN]
    chats = sorted((s for s in mine if _op(s) == "chat"), key=lambda s: s["start_ms"])
    ok_chats = [s for s in chats if s.get("success", True) and _status(s) in ("", "200")]
    tools = sorted((s for s in mine if _op(s) == "execute_tool"), key=lambda s: s["start_ms"])
    mcp = [s for s in tools if s["customDimensions"].get("threadlight.mcp.server")]
    session_start = sum(s["duration_ms"] for s in opens)
    model = _union_ms((s["start_ms"], s["start_ms"] + s["duration_ms"]) for s in chats)
    tool = _union_ms((s["start_ms"], s["start_ms"] + s["duration_ms"]) for s in tools)
    client = record.get("client_ms")
    seg.update({
        "container_ms": container,
        "platform_ms": None if client is None else max(0.0, client - container),
        "session_start_ms": session_start,
        "model_ms": model,
        "tool_ms": tool,
        "runtime_ms": max(0.0, container - session_start - model - tool),
        "model_calls": len(ok_chats),
        "tool_calls": len(tools),
        "mcp_ms": _union_ms((s["start_ms"], s["start_ms"] + s["duration_ms"]) for s in mcp),
        "mcp_calls": len(mcp),
        "model_429": sum(1 for s in chats if _status(s) == "429"),
        "model_failed": len(chats) - len(ok_chats),
        "first_tool_ms": mcp[0]["duration_ms"] if mcp else None,
        "model_first_token_ms": _int(ok_chats[0]["customDimensions"].get("threadlight.time_to_first_token_ms"))
        if ok_chats else None,
    })
    for key, attr in (("input_tokens", "gen_ai.usage.input_tokens"), ("output_tokens", "gen_ai.usage.output_tokens"),
                      ("cached_input_tokens", "threadlight.usage.cache_read_tokens"),
                      ("reasoning_tokens", "threadlight.usage.reasoning_tokens")):
        values = [_int(s["customDimensions"].get(attr)) for s in ok_chats]
        seg[key] = sum(v for v in values if v is not None) if ok_chats else 0
    client_cold = any(str(s["customDimensions"].get("threadlight.session.client_cold_start")).lower() == "true"
                      for s in opens)
    if seg["cold"] and seg["platform_ms"] is not None:
        seg["cold_start_ms"] = seg["platform_ms"] + (session_start if client_cold else 0.0)
    return seg


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _rank(values: Sequence[float], p: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(1, min(len(ordered), math.ceil(p * len(ordered)))) - 1]


def _stats(values: Iterable[Any]) -> dict:
    data = [float(v) for v in values if v is not None]
    if not data:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "max": None}
    return {"n": len(data), "p50": round(_rank(data, 0.5), 1), "p95": round(_rank(data, 0.95), 1),
            "mean": round(sum(data) / len(data), 1), "max": round(max(data), 1)}


def summarize(records: Sequence[Mapping[str, Any]], spans: Sequence[Mapping[str, Any]]) -> dict:
    segments = [request_segments(r, spans) for r in records]
    groups = {}
    for name, keep in (("cold", lambda s: s["cold"]), ("warm", lambda s: not s["cold"]), ("all", lambda s: True)):
        chosen = [s for s in segments if keep(s) and s["success"]]
        groups[name] = {"requests": sum(1 for s in segments if keep(s)), "successful": len(chosen),
                        "segments": {key: _stats(s[key] for s in chosen) for key, *_ in SEGMENTS}}
        groups[name]["segments"]["first_mcp_call_ms"] = _stats(s["first_tool_ms"] for s in chosen)
    errors: dict[str, int] = {}
    for r in records:
        if r.get("error_class"):
            errors[r["error_class"]] = errors.get(r["error_class"], 0) + 1
    joined = [s for s in segments if s["joined"] and s["success"]]
    total = len(records)
    return {
        "requests": total,
        "joined": sum(1 for s in segments if s["joined"]),
        "error_rate": (sum(errors.values()) / total) if total else None,
        "errors": errors,
        "model_throttles": {"requests_with_429": sum(1 for s in segments if s["model_429"]),
                            "calls_429": sum(s["model_429"] or 0 for s in segments),
                            "failed_calls": sum(s["model_failed"] or 0 for s in segments)},
        "groups": groups,
        "tokens_per_request": {
            "input": _stats(s["input_tokens"] for s in joined),
            "cached_input": _stats(s["cached_input_tokens"] for s in joined),
            "output": _stats(s["output_tokens"] for s in joined),
            "reasoning": _stats(s["reasoning_tokens"] for s in joined),
            "model_calls": _stats(s["model_calls"] for s in joined),
            "tool_calls": _stats(s["tool_calls"] for s in joined),
            "mcp_calls": _stats(s["mcp_calls"] for s in joined),
        },
        "token_totals": {key: sum(s[key] or 0 for s in joined)
                         for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")},
        # Cost uses every joined request: a failed request still consumed tokens.
        "token_totals_all": {key: sum(s[key] or 0 for s in segments if s["joined"])
                             for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")},
        "successful_joined": len(joined),
        "per_request": segments,
    }


def join_tokens(summary: Mapping[str, Any]) -> dict:
    """``{invocation_id: {"tokens", "cold_start_ms"}}`` for ``HostedAgentAdapter``."""
    out = {}
    for seg in summary["per_request"]:
        if seg["joined"] and seg["invocation_id"]:
            out[seg["invocation_id"]] = {"tokens": (seg["input_tokens"] or 0) + (seg["output_tokens"] or 0),
                                         "cold_start_ms": seg["cold_start_ms"]}
    return out
