"""Waterfall: client timings joined to the hosted agent's spans, per segment.

Covers the join (invocation id -> trace), per-request segments, the p50/p95
summary by cold/warm, throttle accounting and the rule that a segment we cannot
separate is reported as not separable, never estimated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import waterfall  # noqa: E402

T = "2026-11-11T12:00:00.000Z"


def _rec(inv, *, client_ms, phase="steady", cold=False, ttft=None, status=200, error=None):
    return {"phase": phase, "cold_phase": cold, "invocation_id": inv, "client_ms": client_ms,
            "first_token_ms": ttft, "status": status, "error_class": error, "success": error is None,
            "started_at": T, "worker": 0, "session_request_index": 1}


def _span(table, inv, name, start_ms, duration, **dims):
    base = {"gen_ai.operation.name": dims.pop("op", None)}
    base.update(dims)
    return {"table": table, "invocation_id": inv, "name": name, "start_ms": start_ms,
            "duration_ms": duration, "success": True, "resultCode": "",
            "customDimensions": {k: v for k, v in base.items() if v is not None}}


def _trace(inv, *, server=9000, turn=8800, open_ms=300, cold_client=False, chats=((0, 2000), (3000, 2500)),
           tools=((2100, 600), (2200, 700)), throttles=0):
    rows = [
        _span("requests", inv, "invoke_agent", 0, server),
        _span("dependencies", inv, "threadlight.copilot.turn", 100, turn,
              **{"threadlight.invocation_id": inv, "gen_ai.usage.input_tokens": "9000",
                 "gen_ai.usage.output_tokens": "600", "threadlight.turn.first_token_ms": "4200"}),
        _span("dependencies", inv, "threadlight.copilot.session_open", 100, open_ms,
              **{"threadlight.session.client_cold_start": str(cold_client)}),
    ]
    for i, (start, dur) in enumerate(chats):
        rows.append(_span("dependencies", inv, "chat gpt-5.4", 500 + start, dur, op="chat",
                          **{"gen_ai.usage.input_tokens": "4500", "gen_ai.usage.output_tokens": "300",
                             "threadlight.usage.cache_read_tokens": "4000" if i else "0",
                             "threadlight.time_to_first_token_ms": "800"}))
    for start, dur in tools:
        rows.append(_span("dependencies", inv, "execute_tool returns_get_case", 500 + start, dur,
                          op="execute_tool", **{"threadlight.mcp.server": "mcp"}))
    rows.append(_span("dependencies", inv, "execute_tool skill", 500 + 2950, 10, op="execute_tool"))
    for _ in range(throttles):
        row = _span("dependencies", inv, "chat gpt-5.4", 400, 50, op="chat",
                    **{"http.response.status_code": "429"})
        row["success"] = False
        rows.append(row)
    return rows


def test_segments_split_one_request_into_platform_session_model_tool_and_runtime():
    record = _rec("inv-1", client_ms=10000, ttft=5100)
    seg = waterfall.request_segments(record, _trace("inv-1"))
    assert seg["client_ms"] == 10000
    assert seg["container_ms"] == 9000
    assert seg["platform_ms"] == 1000          # client - container span
    assert seg["session_start_ms"] == 300
    assert seg["model_ms"] == 4500             # two sequential chat calls
    assert seg["tool_ms"] == 810               # parallel tools: union 2100..2900 (+10 ms skill), not 600+700
    assert seg["mcp_ms"] == 800                # MCP server calls only
    assert seg["runtime_ms"] == 9000 - 300 - 4500 - 810
    assert seg["model_calls"] == 2 and seg["tool_calls"] == 3 and seg["mcp_calls"] == 2
    assert seg["client_first_token_ms"] == 5100
    assert seg["model_first_token_ms"] == 800
    assert seg["input_tokens"] == 9000 and seg["output_tokens"] == 600 and seg["cached_input_tokens"] == 4000


def test_unjoined_request_has_no_segments_and_is_never_estimated():
    seg = waterfall.request_segments(_rec("inv-x", client_ms=7000), [])
    assert seg["client_ms"] == 7000 and seg["joined"] is False
    for key in ("container_ms", "platform_ms", "model_ms", "tool_ms", "runtime_ms", "input_tokens"):
        assert seg[key] is None


def test_cold_start_counts_platform_and_client_start_only_for_cold_requests():
    cold = waterfall.request_segments(_rec("c1", client_ms=30000, phase="cold", cold=True),
                                      _trace("c1", server=12000, open_ms=4000, cold_client=True))
    warm = waterfall.request_segments(_rec("w1", client_ms=10000), _trace("w1"))
    assert cold["cold_start_ms"] == (30000 - 12000) + 4000
    assert warm["cold_start_ms"] is None


def test_summary_reports_p50_p95_per_segment_by_cold_and_warm_and_throttles():
    records, spans = [], []
    for i in range(20):
        inv = f"w{i}"
        records.append(_rec(inv, client_ms=10000 + i * 100, ttft=5000 + i))
        spans += _trace(inv, throttles=1 if i == 0 else 0)
    records.append(_rec("c0", client_ms=40000, phase="cold", cold=True))
    spans += _trace("c0", server=15000, open_ms=5000, cold_client=True)
    records.append(_rec("t0", client_ms=200, status=429, error="throttled"))
    summary = waterfall.summarize(records, spans)
    warm = summary["groups"]["warm"]["segments"]
    assert warm["client_ms"]["p50"] == 10900 and warm["client_ms"]["p95"] == 11800
    assert warm["model_ms"]["p50"] == 4500
    assert summary["groups"]["cold"]["segments"]["cold_start_ms"]["p50"] == (40000 - 15000) + 5000
    assert summary["requests"] == 22 and summary["joined"] == 21
    assert summary["errors"]["throttled"] == 1
    assert summary["model_throttles"] == {"requests_with_429": 1, "calls_429": 1, "failed_calls": 1}
    tokens = summary["tokens_per_request"]
    assert tokens["input"]["mean"] == 9000 and tokens["output"]["mean"] == 600
    assert summary["error_rate"] == pytest.approx(1 / 22)


def test_separability_states_what_the_traces_cannot_split():
    notes = {n["segment"]: n for n in waterfall.SEPARABILITY}
    assert notes["platform_ms"]["separated"] is False
    assert "sandbox" in notes["platform_ms"]["why"] and "network" in notes["platform_ms"]["why"]
    assert notes["model_ms"]["separated"] is True
    assert notes["tool_ms"]["separated"] is True
    assert any(n["separated"] is False and "MCP" in n["why"] for n in waterfall.SEPARABILITY)


def test_kql_batches_ids_and_quotes_them_safely():
    queries = waterfall.build_queries([f"id-{i}" for i in range(450)] + ["bad'id"], hours=6, batch=200)
    assert len(queries) == 3
    assert all("threadlight.copilot.turn" in q and "ago(6h)" in q for q in queries)
    assert "bad'id" not in "".join(queries)  # ids outside [A-Za-z0-9_-] are dropped, never interpolated


def test_fetch_spans_parses_app_insights_rows():
    columns = [{"name": n} for n in ("table", "invocation_id", "name", "timestamp", "duration", "success",
                                      "resultCode", "customDimensions")]
    rows = [["requests", "inv-1", "invoke_agent", "2026-11-11T12:00:00.5Z", 9000.0, "True", "200", "{}"],
            ["dependencies", "inv-1", "chat gpt-5.4", "2026-11-11T12:00:01Z", 2000.0, "True", "",
             json.dumps({"gen_ai.operation.name": "chat"})]]

    def run(argv):
        assert argv[:4] == ["az", "monitor", "app-insights", "query"]
        return json.dumps({"tables": [{"columns": columns, "rows": rows}]})

    spans = waterfall.fetch_spans(run, "/subscriptions/x/appi", ["inv-1"], hours=6)
    assert [s["name"] for s in spans] == ["invoke_agent", "chat gpt-5.4"]
    assert spans[1]["start_ms"] - spans[0]["start_ms"] == 500
    assert spans[1]["customDimensions"]["gen_ai.operation.name"] == "chat"
    assert spans[0]["success"] is True


def test_throttles_are_read_from_threadlight_status_and_failed_requests_still_cost_tokens():
    """Live 2026-11-11: the exporter folded http.response.status_code away; 429s were invisible."""
    ok = _trace("ok")
    failed = _trace("bad", chats=((0, 2000),))
    throttled = _span("dependencies", "bad", "chat gpt-5.4", 300, 40, op="chat",
                      **{"threadlight.model.status_code": "429", "threadlight.error_class": "throttled"})
    throttled["success"] = False
    unknown = dict(throttled, customDimensions={"gen_ai.operation.name": "chat"})
    records = [_rec("ok", client_ms=10000), _rec("bad", client_ms=9000, error="agent_error")]
    summary = waterfall.summarize(records, ok + failed + [throttled, unknown])
    assert summary["model_throttles"] == {"requests_with_429": 1, "calls_429": 1, "failed_calls": 2}
    # Successful requests only for latency; every joined request's tokens for cost.
    assert summary["successful_joined"] == 1
    assert summary["token_totals"]["input_tokens"] == 9000
    assert summary["token_totals_all"]["input_tokens"] == 9000 + 4500


def test_first_request_of_a_session_is_not_counted_as_warm():
    """Review: per-user sessions start a sandbox on their first request; that is not a warm request."""
    first = dict(_rec("f", client_ms=30000), session_request_index=0)
    later = dict(_rec("l", client_ms=10000), session_request_index=1)
    summary = waterfall.summarize([first, later], _trace("f") + _trace("l"))
    assert summary["groups"]["warm"]["segments"]["client_ms"]["p50"] == 10000
    assert summary["groups"]["warm"]["successful"] == 1
    assert summary["groups"]["first_in_session"]["segments"]["client_ms"]["p50"] == 30000
