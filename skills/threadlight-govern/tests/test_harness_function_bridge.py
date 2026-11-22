"""Governed writes for Copilot harness agents via client function tools (#230).

Real ACS/OPA, real control plane receipts and approvals, real governed gateway
dispatcher (``test_gateway.GatewayHarness``); only the Foundry Responses endpoint
is a local double that reproduces the tested harness wire behaviour:
harness-internal tools also surface as ``function_call`` items, answering an
unknown ``call_id`` is HTTP 400, and a function-output turn can come back
``failed`` with or without the output recorded.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_gateway import GatewayHarness

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "skills/threadlight-govern/references/harness/function_bridge.py"


def bridge_module():
    spec = importlib.util.spec_from_file_location("harness_function_bridge", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(name, call_id, arguments=None):
    return SimpleNamespace(type="function_call", name=name, call_id=call_id,
                           arguments=json.dumps(arguments if arguments is not None else {"amount": 5}))


def message(text):
    return SimpleNamespace(type="message", content=[SimpleNamespace(text=text)])


class HarnessDouble:
    """Foundry harness Responses endpoint as observed live (Nov 2026)."""

    def __init__(self, first_output, *, fail=()):
        self.first_output = first_output
        self.fail = list(fail)  # per function-output turn: None | "recorded" | "lost"
        self.items, self.requests = [], []
        self.responses = SimpleNamespace(create=self.create)
        self.conversations = SimpleNamespace(items=SimpleNamespace(list=self.list))

    async def list(self, conversation):
        return SimpleNamespace(data=list(self.items))

    async def create(self, *, conversation, input):
        self.requests.append(input)
        if isinstance(input, str):
            self.items += self.first_output
            return SimpleNamespace(status="completed", output=list(self.first_output))
        issued = {i.call_id for i in self.items if getattr(i, "type", None) == "function_call"}
        answered = {i.call_id for i in self.items if getattr(i, "type", None) == "function_call_output"}
        ids = [o["call_id"] for o in input]
        unknown = [i for i in ids if i not in issued or i in answered]
        if unknown:
            raise RuntimeError(f"HTTP 400 Invalid function_call_output batch; unknown call IDs {unknown}")
        mode = self.fail.pop(0) if self.fail else None
        if mode == "lost":
            return SimpleNamespace(status="failed", output=[])
        self.items += [SimpleNamespace(type="function_call_output", call_id=o["call_id"]) for o in input]
        if mode == "recorded":
            return SimpleNamespace(status="failed", output=[])
        receipt = json.loads(input[-1]["output"])
        return SimpleNamespace(status="completed", output=[message(f"refund {receipt['status']}")])


FIRST_TURN = [call("skill", "call_skill_1", {"name": "return-decision"}), call("view", "call_view_1", {"path": "x"}),
              call("issue_refund", "call_write_1")]


async def run(h, double, conversation="conv_1"):
    m = bridge_module()
    tools = {"issue_refund": m.gateway_executor(h.dispatcher, lambda: "Bearer " + h.cp.token(),
                                                {"issue_refund": "refund"})}
    bridge = m.HarnessFunctionBridge(double, tools)
    return bridge, await bridge.run(conversation, "refund order 42")


@pytest.mark.governance_runtime
def test_harness_denied_write_returns_a_denial_receipt_with_no_effect(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path, {"decision": "deny"})
        try:
            double = HarnessDouble(FIRST_TURN)
            _, response = await run(h, double)
            sent = json.loads(double.requests[1][0]["output"])
            assert sent["status"] == "DENIED_BY_POLICY" and sent["effect"] == "none"
            assert sent["reason_code"] == "policy_deny"
            assert response.status == "completed"
            assert h.calls == []
            assert [r["decision"] for r in h.receipt_bodies()] == ["deny"]
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_harness_approved_write_executes_once_and_a_replay_is_idempotent(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            double = HarnessDouble(FIRST_TURN)
            await run(h, double)
            sent = json.loads(double.requests[1][0]["output"])
            assert sent == {"effect": "applied", "result": {"status": "refunded"}, "status": "EXECUTED"}
            assert len(h.calls) == 1
            # Process restart: a new bridge replays the same conversation/call_id.
            h.dispatcher = h.new_dispatcher()
            replay = HarnessDouble(FIRST_TURN)
            await run(h, replay)
            assert json.loads(replay.requests[1][0]["output"]) == sent
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_harness_internal_function_calls_are_never_answered(tmp_path):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            double = HarnessDouble(FIRST_TURN)
            await run(h, double)
            answered = [o["call_id"] for batch in double.requests[1:] for o in batch]
            assert answered == ["call_write_1"]
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("mode", ["recorded", "lost"])
def test_harness_failed_turn_is_reconciled_before_replay(tmp_path, mode):
    async def case():
        h = await GatewayHarness().initialize(tmp_path)
        try:
            double = HarnessDouble(FIRST_TURN, fail=[mode])
            m = bridge_module()
            tools = {"issue_refund": m.gateway_executor(h.dispatcher, lambda: "Bearer " + h.cp.token(),
                                                        {"issue_refund": "refund"})}
            bridge = m.HarnessFunctionBridge(double, tools)
            if mode == "recorded":
                # The output is already in the conversation: never resent (that is a 400), never re-executed.
                with pytest.raises(m.HarnessTurnFailed) as failure:
                    await bridge.run("conv_1", "refund order 42")
                assert failure.value.recorded == ["call_write_1"] and failure.value.pending == []
                assert len(double.requests) == 2
            else:
                response = await bridge.run("conv_1", "refund order 42")
                assert response.status == "completed"
                assert [len(r) for r in double.requests[1:]] == [1, 1]
                assert double.requests[1] == double.requests[2]
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


def test_gateway_results_map_to_model_receipts():
    m = bridge_module()
    assert m.receipt_from_gateway({"status": "blocked", "reason_code": "approval_denied"})["status"] == "DENIED_BY_POLICY"
    assert m.receipt_from_gateway({"status": "unavailable", "reason_code": "outcome_unknown"})["effect"] == "unknown"
    assert m.receipt_from_gateway({"status": "pending_approval"})["status"] == "PENDING_APPROVAL"
    assert m.receipt_from_gateway({"status": "unavailable", "reason_code": "gateway_unavailable"})["effect"] == "none"


def test_harness_route_documents_which_governance_claims_apply():
    readme = (ROOT / "skills/threadlight-govern/references/harness/README.md").read_text()
    for needle in ("different PEP placement", "LOCAL-14", "CTK", "does not apply", "applies",
                   "function_call_output", "call_id", "require_approval"):
        assert needle in readme, needle
