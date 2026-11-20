"""`workload` mode: deterministic task mix through model router vs fixed models.

The recorded fixture `workload-recorded-easy.jsonl` is real Responses API usage
from the Nov 2026 investigation behind issue #216 (Sweden Central, model router
2025-11-18, 40 easy tasks x 2 reps per arm). Only usage, routing and grading
fields are kept; prompts and answers are dropped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent.parent / "references" / "fixtures"

import prices as prices_mod  # noqa: E402
import pool as pool_mod  # noqa: E402
import workload as wl  # noqa: E402
import workload_tasks as tasks_mod  # noqa: E402
import router_bench  # noqa: E402

RECORDED = FIXTURES / "workload-recorded-easy.jsonl"
RETAIL = FIXTURES / "retail-prices-swedencentral.json"

# Issue #216 headline: $ per 1k easy tasks.
ISSUE_COST_PER_1K = {
    "gpt-6-luna": 0.054,
    "router-subset-balanced": 0.082,
    "router-full-balanced": 0.147,
    "router-full-quality": 1.83,
}


def _summary():
    records = wl.load_records(RECORDED)
    table = prices_mod.load_prices(RETAIL)
    return wl.summarize(records, table)


# ---- request shape ----

def test_router_arm_sends_controls_header_and_no_affinity():
    arm = wl.router_arm("router-balanced", deployment="model-router", mode="balanced")
    task = {"id": "qa0", "cat": "simple_qa", "input": "hi", "expect": ["x"]}
    body, headers = wl.build_request(arm, task)
    assert headers["Foundry-Features"] == "ModelRouterControls=V1Preview"
    assert body["model"] == "model-router"
    assert body["routing_config"]["mode"] == "balanced"
    assert body["routing_config"]["session_affinity"] == {"strategy": "none"}
    assert body["store"] is False


def test_fixed_arm_sends_no_router_controls():
    arm = wl.fixed_arm("gpt-6-luna")
    body, headers = wl.build_request(arm, {"id": "t", "cat": "tool", "input": "x",
                                           "tools": [{"type": "function", "name": "f"}],
                                           "system": "sys"})
    assert "Foundry-Features" not in headers
    assert "routing_config" not in body
    assert body["tools"] and body["instructions"] == "sys"


# ---- served model ----

def test_served_model_prefers_model_field_and_normalizes_version():
    resp = {"model": "gpt-6.1-terra-2026-10-26"}
    assert wl.served_model(resp) == ("gpt-6.1-terra", "model")


def test_served_model_falls_back_to_routing_trace_when_model_is_router():
    # 2 of 360 calls in the investigation returned the router name in `model`.
    resp = {"model": "model-router-2025-11-18", "model_selection_details": {
        "model_router_details": {"routing_trace": [
            {"latency_ms": 25, "attempts": [{"model": "gpt-6.1-sol", "result": {"status": 429}},
                                            {"model": "gpt-6-luna", "result": {"status": 200}}]}]}}}
    assert wl.served_model(resp) == ("gpt-6-luna", "routing_trace")


def test_served_model_unknown_when_no_evidence():
    assert wl.served_model({"model": "model-router-2025-11-18"}) == ("model-router", "unattributed")


# ---- 429-aware call ----

class _Resp:
    def __init__(self, status, body, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}


def test_call_retries_on_429_honours_retry_after_and_records_ratelimit_key():
    seq = [
        _Resp(429, {"error": {"code": "429"}}, {"retry-after": "3",
                                                 "x-ratelimit-key": "model-router-2025-11-18"}),
        _Resp(200, {"model": "gpt-6-luna", "usage": {}}, {"x-ratelimit-key": "gpt-6-luna"}),
    ]
    slept: list[float] = []
    out = wl.call_with_retries(lambda body, headers: seq.pop(0), {}, {},
                               sleep=slept.append, max_attempts=4)
    assert out["status"] == 200
    assert out["throttled"] == 1 and out["retries"] == 1
    assert slept == [3.0]
    assert out["ratelimit_key"] == "gpt-6-luna"
    assert out["ratelimit_keys_seen"] == ["model-router-2025-11-18", "gpt-6-luna"]


def test_call_gives_up_after_max_attempts_and_reports_error():
    def always_429(body, headers):
        return _Resp(429, {"error": {"code": "429"}}, {"x-ratelimit-key": "model-router-2025-11-18"})
    out = wl.call_with_retries(always_429, {}, {}, sleep=lambda s: None, max_attempts=3)
    assert out["status"] == 429 and out["throttled"] == 3
    assert out["error"]


def test_call_does_not_retry_client_errors():
    calls = []

    def bad(body, headers):
        calls.append(1)
        return _Resp(400, {"error": {"code": "invalid"}})
    out = wl.call_with_retries(bad, {}, {}, sleep=lambda s: None, max_attempts=5)
    assert out["status"] == 400 and len(calls) == 1


def test_run_task_records_usage_routing_and_score():
    resp = {"model": "gpt-6-luna", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": "Canberra"}]}],
        "usage": {"input_tokens": 20, "output_tokens": 6,
                  "input_tokens_details": {"cached_tokens": 4},
                  "output_tokens_details": {"reasoning_tokens": 2}},
        "model_selection_details": {"model_router_details": {
            "mode": "balanced", "routing_trace": [{"latency_ms": 25, "attempts": [
                {"model": "gpt-6-luna", "result": {"status": 200}}]}]}}}
    post = lambda body, headers: _Resp(200, resp, {"x-ratelimit-key": "gpt-6-luna"})  # noqa: E731
    arm = wl.router_arm("router-balanced", deployment="model-router", mode="balanced")
    task = {"id": "qa0", "cat": "simple_qa", "input": "capital?", "expect": ["canberra"]}
    rec = wl.run_task(arm, task, 0, post, sleep=lambda s: None)
    assert rec["model"] == "gpt-6-luna" and rec["served_from"] == "model"
    assert rec["in"] == 20 and rec["cached"] == 4 and rec["out"] == 6
    assert rec["attempts"] == ["gpt-6-luna"] and rec["route_ms"] == 25
    assert rec["ratelimit_key"] == "gpt-6-luna"
    assert rec["score"] == 1.0 and rec["router"] is True


# ---- deterministic task mix ----

def test_task_mix_is_deterministic_and_balanced():
    easy, hard = tasks_mod.build("easy"), tasks_mod.build("hard")
    assert len(easy) == 40 and len(hard) == 20
    assert [t["id"] for t in easy] == [t["id"] for t in tasks_mod.build("easy")]
    assert {t["cat"] for t in easy} == {"simple_qa", "rag", "tool", "reasoning", "code"}


def test_graders_are_deterministic():
    qa = next(t for t in tasks_mod.build("easy") if t["id"] == "qa0")
    assert tasks_mod.grade(qa, "Canberra", []) == 1.0
    assert tasks_mod.grade(qa, "Sydney", []) == 0.0
    tool = next(t for t in tasks_mod.build("easy") if t["id"] == "tool0")
    call = [{"name": "get_order_status", "arguments": json.dumps({"order_id": "A-77812"})}]
    assert tasks_mod.grade(tool, "", call) == 1.0
    rsn = next(t for t in tasks_mod.build("easy") if t["id"] == "rsn0")
    assert tasks_mod.grade(rsn, "work...\nANSWER: 5", []) == 1.0


# ---- recorded workload reproduces the issue's cost ordering ----

def test_recorded_workload_reproduces_issue_costs_within_10_percent():
    arms = _summary()["arms"]
    for arm, expected in ISSUE_COST_PER_1K.items():
        got = arms[arm]["cost_per_1k_usd"]
        assert abs(got - expected) <= 0.10 * expected, (arm, got, expected)
        assert arms[arm]["quality"] == 1.0


def test_recorded_workload_cost_ordering_matches_issue():
    arms = _summary()["arms"]
    order = sorted(ISSUE_COST_PER_1K, key=lambda a: arms[a]["cost_per_1k_usd"])
    assert order == ["gpt-6-luna", "router-subset-balanced",
                     "router-full-balanced", "router-full-quality"]


def test_router_fee_is_charged_only_on_router_arms():
    arms = _summary()["arms"]
    assert arms["router-full-balanced"]["router_fee_usd"] > 0
    assert arms["gpt-6-luna"]["router_fee_usd"] == 0


def test_dominant_model_flag_on_subset_router():
    # The subset router (balanced) sent every easy task to gpt-6-luna: the
    # router fee buys nothing, so the decision rule says call it directly.
    arm = _summary()["arms"]["router-subset-balanced"]
    assert arm["top_model"] == "gpt-6-luna" and arm["top_share"] == 1.0
    assert arm["dominant_model"] == "gpt-6-luna"
    full = _summary()["arms"]["router-full-balanced"]
    assert full["dominant_model"] is None


def test_routing_is_deterministic_across_reps_in_recording():
    assert _summary()["arms"]["router-full-balanced"]["tasks_switching_model"] == 0


def test_unpriced_models_are_reported_not_silently_zero():
    records = [{"arm": "x", "task": "t", "cat": "c", "rep": 0, "router": False,
                "model_field": "brand-new-model", "attempts": [], "in": 10,
                "cached": 0, "cache_write": 0, "out": 5, "score": 1.0}]
    arm = wl.summarize(records, prices_mod.load_prices(RETAIL))["arms"]["x"]
    assert arm["unpriced_models"] == ["brand-new-model"]
    assert arm["cost_complete"] is False


# ---- pool pre-flight ----

def test_pool_lists_models_for_router_version():
    doc = json.loads((FIXTURES / "arm-models-router-pool.json").read_text())
    pool = pool_mod.parse_router_pool(doc, version="2025-11-18")
    assert pool["count"] == 45
    names = {m["name"] for m in pool["models"]}
    assert {"gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"} <= names
    assert pool["formats"]["Anthropic"] >= 1


def test_pool_fetch_uses_arm_models_api():
    seen = {}

    def runner(args):
        seen["args"] = args
        return json.dumps({"value": []})
    pool_mod.fetch_pool("sub-id", "swedencentral", runner=runner)
    url = seen["args"][seen["args"].index("--url") + 1]
    assert url.endswith("/subscriptions/sub-id/providers/Microsoft.CognitiveServices/"
                        "locations/swedencentral/models?api-version=2025-06-01")


# ---- CLI ----

def test_cli_workload_ingest_writes_report(tmp_path):
    rc = router_bench.main(["workload", "--ingest", str(RECORDED), "--prices", str(RETAIL),
                            "--out", str(tmp_path)])
    assert rc == 0
    doc = json.loads((tmp_path / "router-workload.json").read_text())
    assert doc["schema"] == "threadlight-router-workload/v1"
    assert "router-subset-balanced" in doc["arms"]
    md = (tmp_path / "router-workload.md").read_text()
    assert "Estimated token cost" in md and "router-full-quality" in md


def test_cli_workload_requires_endpoint_or_ingest(tmp_path):
    assert router_bench.main(["workload", "--out", str(tmp_path)]) == 2


def test_validate_dispatch_uses_modern_arms(monkeypatch, tmp_path):
    seen = {}

    def fake_dispatch(workloads, arms, **kw):
        seen["arms"] = arms
        return []
    monkeypatch.setattr(router_bench.matrix_mod, "dispatch_matrix", fake_dispatch)
    router_bench.main(["validate", "--dispatch", "--out", str(tmp_path)])
    deps = {a["arm"]: (a["model_deployment"], a["wire_api"]) for a in seen["arms"]}
    assert deps == {"small": ("gpt-6-luna", "responses"),
                    "router": ("model-router", "completions"),
                    "strong": ("gpt-6.1-sol", "responses")}


@pytest.mark.parametrize("bad", ["", "   "])
def test_router_arm_requires_deployment(bad):
    with pytest.raises(ValueError):
        wl.router_arm("r", deployment=bad, mode="balanced")


def test_models_without_retail_meter_are_flagged_unverified():
    arms = _summary()["arms"]
    assert arms["gpt-6.2-astra"]["unverified_price_models"] == ["gpt-6.2-astra"]
    assert arms["gpt-6-luna"]["unverified_price_models"] == []
