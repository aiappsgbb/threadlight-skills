"""GHCP hosted runtime: per-turn OpenTelemetry spans for the latency waterfall.

The Invocations protocol library exports one server span per invocation to the
project's Application Insights. The Copilot CLI makes the model and MCP calls
in its own process, so without these spans a load test cannot separate model
latency, tool latency and session start from runtime overhead. The container
turns the SDK's session events (``assistant.usage``, ``model.call_failure``,
``tool.execution_*``) into child spans: metadata only, never prompt, answer,
tool arguments or results.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import types

import pytest

pytest.importorskip("starlette")
sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

import test_ghcp_session_isolation as base  # noqa: E402
from test_ghcp_session_isolation import runtime  # noqa: E402,F401  (fixture)

SECRET_PROMPT = "Triage RMA-2026-000451 for customer Jane Example"
SECRET_ANSWER = "Approve the refund for Jane Example"
SECRET_ARGS = {"rma": "RMA-2026-000451", "customer": "Jane Example"}

T0 = dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=dt.timezone.utc)


def _ev(kind, seconds, **data):
    event = types.SimpleNamespace(type=base._Type(kind), data=types.SimpleNamespace(**data),
                                  timestamp=T0 + dt.timedelta(seconds=seconds))
    event.to_dict = lambda: {"type": kind, "data": {"content": data.get("content")}}
    return event


def _script(fail_status=None):
    events = [
        _ev("assistant.turn_start", 0.0, turn_id="0"),
        _ev("tool.execution_start", 1.0, tool_call_id="call-1", tool_name="mcp-returns_get_case",
            mcp_server_name="mcp", mcp_tool_name="returns_get_case", arguments=SECRET_ARGS),
        _ev("tool.execution_complete", 1.4, tool_call_id="call-1", success=True,
            result=types.SimpleNamespace(content=SECRET_ANSWER)),
    ]
    if fail_status:
        events.append(_ev("model.call_failure", 2.0, model="gpt-5.4", status_code=fail_status,
                          duration=dt.timedelta(milliseconds=300), error_message=SECRET_PROMPT,
                          source="top_level"))
    events += [
        _ev("assistant.usage", 3.0, model="gpt-5.4", input_tokens=1200, output_tokens=150,
            reasoning_tokens=40, cache_read_tokens=1000, duration=dt.timedelta(milliseconds=900),
            time_to_first_token=dt.timedelta(milliseconds=450), finish_reason="stop"),
        _ev("assistant.message_delta", 3.1, delta_content=SECRET_ANSWER[:7], content=None),
        _ev("assistant.message", 3.2, content=SECRET_ANSWER),
    ]
    return events


@pytest.fixture
def traced(runtime, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = sdk_trace.TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(runtime, "_TRACER", provider.get_tracer("test"))
    return runtime, exporter


def _drive(module, script, *, stream=True):
    async def send(self, text):
        self.history.append(text)
        await asyncio.sleep(0)
        for event in script + [base._Event(base.IDLE)]:
            for handler in list(self.handlers):
                handler(event)

    base.FakeSession.send, original = send, base.FakeSession.send
    try:
        return base._invoke(module, SECRET_PROMPT, stream=stream)
    finally:
        base.FakeSession.send = original


def _by_name(exporter):
    return {span.name: span for span in exporter.get_finished_spans()}


@pytest.mark.parametrize("stream", [True, False])
def test_turn_emits_session_model_and_tool_spans(traced, stream):
    module, exporter = traced
    _drive(module, _script(), stream=stream)
    spans = _by_name(exporter)
    assert {"threadlight.copilot.session_open", "threadlight.copilot.turn", "chat gpt-5.4",
            "execute_tool returns_get_case"} <= set(spans)
    turn, chat, tool = spans["threadlight.copilot.turn"], spans["chat gpt-5.4"], spans["execute_tool returns_get_case"]
    # Model and tool spans hang off the turn, all in one trace.
    assert chat.parent.span_id == turn.context.span_id
    assert tool.parent.span_id == turn.context.span_id
    assert len({s.context.trace_id for s in spans.values()}) == 1
    # Model call: SDK-reported duration and time to first token, tokens per call.
    assert (chat.end_time - chat.start_time) == 900_000_000
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert chat.attributes["gen_ai.usage.input_tokens"] == 1200
    assert chat.attributes["gen_ai.usage.output_tokens"] == 150
    assert chat.attributes["threadlight.usage.reasoning_tokens"] == 40
    assert chat.attributes["threadlight.usage.cache_read_tokens"] == 1000
    assert chat.attributes["threadlight.time_to_first_token_ms"] == 450
    # Tool call: start/complete event timestamps, MCP server named.
    assert (tool.end_time - tool.start_time) == 400_000_000
    assert tool.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool.attributes["gen_ai.tool.name"] == "returns_get_case"
    assert tool.attributes["threadlight.mcp.server"] == "mcp"
    assert tool.attributes["threadlight.tool.success"] is True
    # Turn totals for the waterfall.
    assert turn.attributes["threadlight.turn.model_calls"] == 1
    assert turn.attributes["threadlight.turn.tool_calls"] == 1
    assert turn.attributes["gen_ai.usage.input_tokens"] == 1200
    assert turn.attributes["gen_ai.usage.output_tokens"] == 150
    assert spans["threadlight.copilot.session_open"].attributes["threadlight.session.kind"] == "fresh"
    if stream:
        # The streamed path names its invocation so a load test can join client and server.
        assert turn.attributes["threadlight.invocation_id"] == "inv-1"


def test_model_call_failure_records_status_code_not_message(traced):
    module, exporter = traced
    _drive(module, _script(fail_status=429))
    failures = [s for s in exporter.get_finished_spans()
                if s.name == "chat gpt-5.4" and s.attributes.get("http.response.status_code") == 429]
    assert len(failures) == 1
    assert failures[0].status.status_code.name == "ERROR"
    assert failures[0].attributes["error.type"] == "429"
    turn = _by_name(exporter)["threadlight.copilot.turn"]
    assert turn.attributes["threadlight.turn.model_failures"] == 1
    assert turn.attributes["threadlight.turn.throttled"] == 1


def test_spans_never_carry_prompt_answer_arguments_or_results(traced):
    module, exporter = traced
    _drive(module, _script(fail_status=500))
    for span in exporter.get_finished_spans():
        blob = repr(dict(span.attributes)) + span.name + repr([e.attributes for e in span.events])
        for secret in (SECRET_PROMPT, SECRET_ANSWER, "Jane Example", "RMA-2026-000451"):
            assert secret not in blob, (span.name, secret)


def test_client_start_is_marked_cold_once(traced):
    module, exporter = traced
    _drive(module, _script())
    _drive(module, _script())
    opens = [s for s in exporter.get_finished_spans() if s.name == "threadlight.copilot.session_open"]
    assert [s.attributes["threadlight.session.client_cold_start"] for s in opens] == [True, False]


def test_telemetry_failure_never_breaks_the_answer(traced, monkeypatch):
    module, _ = traced

    class Broken:
        def start_span(self, *a, **k):
            raise RuntimeError("exporter down")

        def start_as_current_span(self, *a, **k):
            raise RuntimeError("exporter down")

    monkeypatch.setattr(module, "_TRACER", Broken())
    out = _drive(module, _script(), stream=False)
    assert SECRET_ANSWER in out


def test_failure_without_status_code_is_classified_from_the_message_without_copying_it(traced):
    """Live 2026-11-11: model.call_failure carried status_code None; the 429 was only in the message."""
    module, exporter = traced
    script = _script()
    script.insert(3, _ev("model.call_failure", 2.0, model="gpt-5.4", status_code=None,
                         duration=dt.timedelta(milliseconds=200), source="top_level",
                         error_message=f"Request failed with status 429 Too Many Requests: {SECRET_PROMPT}"))
    _drive(module, script)
    failed = [s for s in exporter.get_finished_spans()
              if s.name == "chat gpt-5.4" and s.status.status_code.name == "ERROR"]
    assert len(failed) == 1
    assert failed[0].attributes["threadlight.model.status_code"] == 429
    assert failed[0].attributes["threadlight.error_class"] == "throttled"
    assert SECRET_PROMPT not in repr(dict(failed[0].attributes))
    assert _by_name(exporter)["threadlight.copilot.turn"].attributes["threadlight.turn.throttled"] == 1
