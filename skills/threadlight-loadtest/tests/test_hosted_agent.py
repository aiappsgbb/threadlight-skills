"""Hosted-agent load driver: phases, SSE timing, error classes and the loadtest gates."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hosted_agent as ha  # noqa: E402
from loadtest import run_loadtest  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Response:
    def __init__(self, status, lines=(), clock=None, steps=()):
        self.status_code, self._lines, self._clock, self._steps = status, list(lines), clock, list(steps)
        self.headers = {"x-agent-invocation-id": "hdr-inv"}
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for line, step in zip(self._lines, self._steps or [0] * len(self._lines)):
            if self._clock:
                self._clock.t += step
            yield line

    def close(self):
        self.closed = True


class _Http:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc:
            raise self.exc
        return self.response


STREAM = [
    'data: {"type": "assistant.turn_start", "data": {}}', "",
    'data: {"type": "tool.execution_start", "data": {"toolName": "returns_get_case"}}', "",
    'data: {"type": "assistant.message_delta", "data": {"deltaContent": "Approve"}}', "",
    'data: {"type": "assistant.message", "data": {"content": "Approve the refund"}}', "",
    "event: done", 'data: {"invocation_id": "inv-42"}', "",
]


def test_invoke_once_measures_first_token_and_reads_invocation_id_without_content():
    clock = _Clock()
    http = _Http(_Response(200, STREAM, clock, steps=[0, 0, 1, 0, 2, 0, 0, 0, 1, 0, 0]))
    out = ha.invoke_once(http, "https://x/agents/a/endpoint/protocols/invocations?api-version=v1",
                         "tok", "Triage RMA-1", "tlperf-s1", clock=clock)
    assert out["status"] == 200 and out["error_class"] is None
    assert out["first_token_ms"] == 3000 and out["client_ms"] == 4000
    assert out["invocation_id"] == "inv-42" and out["tool_events"] == 1
    url, kwargs = http.calls[0]
    assert kwargs["params"] == {"agent_session_id": "tlperf-s1"}
    assert kwargs["json"] == {"input": "Triage RMA-1", "new_conversation": True}
    assert "Approve" not in repr(out) and "Triage" not in repr(out)
    assert http.response.closed


@pytest.mark.parametrize("status,error", [(429, "throttled"), (503, "http_503")])
def test_http_errors_are_classified(status, error):
    out = ha.invoke_once(_Http(_Response(status)), "u", "t", "p", "s")
    assert out["status"] == status and out["error_class"] == error


def test_transport_timeout_and_incomplete_stream_are_errors():
    class ReadTimeout(Exception):
        pass

    assert ha.invoke_once(_Http(exc=ReadTimeout()), "u", "t", "p", "s")["error_class"] == "timeout"
    cut = _Response(200, STREAM[:6])
    assert ha.invoke_once(_Http(cut), "u", "t", "p", "s")["error_class"] == "stream_incomplete"
    agent_error = _Response(200, ['data: {"type": "error", "message": "boom"}', "", "event: done", "data: {}", ""])
    assert ha.invoke_once(_Http(agent_error), "u", "t", "p", "s")["error_class"] == "agent_error"


def test_phase_session_modes_make_cold_and_warm_explicit():
    seen = []

    def invoke(prompt, session_id):
        seen.append(session_id)
        return {"client_ms": 10.0, "status": 200, "error_class": None, "invocation_id": f"i{len(seen)}"}

    runner = ha.PhaseRunner(invoke=invoke, prompts=["a", "b"], run_id="r1")
    records = runner.run([{"name": "cold", "concurrency": 1, "requests": 3, "session_mode": "new"},
                          {"name": "steady", "concurrency": 2, "requests": 6}])
    cold = [r for r in records if r.phase == "cold"]
    steady = [r for r in records if r.phase == "steady"]
    assert len(cold) == 3 and len({r.session_id for r in cold}) == 3 and all(r.cold_phase for r in cold)
    assert len(steady) == 6 and len({r.session_id for r in steady}) <= 2 and not any(r.cold_phase for r in steady)
    assert runner.phase_windows["steady"]["concurrency"] == 2


def test_default_phases_ramp_to_steady_and_validation_rejects_bad_input():
    phases = ha.default_phases(max_concurrency=10, steady_requests=100)
    assert [p["name"] for p in phases] == ["cold", "ramp-1", "ramp-2", "ramp-5", "steady"]
    assert phases[0]["session_mode"] == "new" and phases[0]["cold"] is True
    assert phases[-1]["concurrency"] == 10
    for bad in ([], [{"name": "x", "concurrency": 0, "requests": 1}],
                [{"name": "x", "concurrency": 1, "requests": 1, "rps": 5}],
                [{"name": "x", "concurrency": 1, "requests": ha.MAX_TOTAL_REQUESTS + 1}]):
        with pytest.raises(ha.PhaseError):
            ha.validate_phases(bad)


def _profile():
    return {"name": "perf", "endpoint": {"url": "https://x", "credential_ref": "entra:DefaultAzureCredential"},
            "request_count": 10, "tokens_per_request_estimate": 20000, "price_per_1k_tokens_usd": 0.003}


def test_budget_gate_runs_before_any_request():
    calls = []
    runner = ha.PhaseRunner(invoke=lambda p, s: calls.append(s) or {}, prompts=["p"])
    adapter = ha.HostedAgentAdapter(runner, [{"name": "s", "concurrency": 1, "requests": 10}])
    manifest = run_loadtest(profile=_profile(), budget_ceiling_usd=0.10, endpoint_class="non-production",
                            adapter=adapter, generated_at="2026-11-11T12:00:00Z")
    assert manifest["status"] == "aborted" and calls == []


def test_adapter_is_partial_without_telemetry_and_complete_with_it():
    def invoke(prompt, session_id):
        return {"client_ms": 1000.0, "status": 200, "error_class": None, "invocation_id": session_id}

    phases = [{"name": "cold", "concurrency": 1, "requests": 1, "session_mode": "new"},
              {"name": "s", "concurrency": 1, "requests": 2}]
    bare = ha.HostedAgentAdapter(ha.PhaseRunner(invoke=invoke, prompts=["p"]), phases)
    result = bare.run(_profile())
    assert result["status"] == "partial" and "tokens per request not measured" in result["error"]
    joined = ha.HostedAgentAdapter(
        ha.PhaseRunner(invoke=invoke, prompts=["p"]), phases,
        join=lambda records: {r.invocation_id: {"tokens": 9000, "cold_start_ms": 800.0 if r.cold_phase else None}
                              for r in records})
    result = joined.run(_profile())
    assert result["status"] == "complete"
    assert [s["tokens"] for s in result["samples"]] == [9000, 9000, 9000]
    assert result["samples"][0]["cold_start_latency_ms"] == 800.0
    assert "cold_start_latency_ms" not in result["samples"][1]


def test_a_runaway_turn_hits_the_total_deadline_even_with_keepalives():
    """Live 2026-11-11: a turn ran 648 s; SSE keep-alives reset the read timeout, so only a total deadline ends it."""
    clock = _Clock()
    keepalives = [": keep-alive", ""] * 50
    http = _Http(_Response(200, keepalives, clock, steps=[30] * 100))
    out = ha.invoke_once(http, "u", "t", "p", "s", deadline_s=120, clock=clock)
    assert out["error_class"] == "deadline"
    assert out["client_ms"] <= 150_000
    assert http.response.closed
