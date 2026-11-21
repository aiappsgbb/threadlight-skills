"""Governed writes for Foundry Copilot harness agents via client function tools (#230).

The Copilot harness has no Agent Hooks surface, and the service manages approval
for its built-in tools, so the in-process PEP of the hosted runtime cannot run
inside it. When a harness agent calls a **client-executed function tool**, the
turn ends with a ``function_call`` item and the application answers it. This
module is that application side, and nothing more:

1. It answers **only its own declared function names**. Harness-internal tools
   (``skill``, ``view`` ...) also appear as ``function_call`` items in
   ``response.output``; answering one of their ``call_id`` values returns HTTP
   400 ("Invalid function_call_output batch; unknown call IDs"), so they are
   never answered (tested Nov 2026, #230).
2. It executes each declared call through the existing governance PEP, the
   governed tool gateway ``GovernedDispatcher.dispatch``: signed fresh policy,
   authenticated one-use approval, trusted facts and the central audit ACK
   before any effect. The idempotency key is ``harness:<conversation>:<call_id>``,
   so a replay after a crash or a failed turn reaches the same reserved
   operation and never a second effect.
3. It returns the gateway result as ``function_call_output``: a denial is a
   ``DENIED_BY_POLICY`` receipt with ``effect: none``.
4. A transient ``failed`` turn is **reconciled before replay**: the bridge lists
   the conversation items and resends only the outputs the conversation did not
   record. Recorded outputs are never sent twice, and no call is re-executed
   (outputs are cached; the gateway is idempotent anyway).

The agent loop stays in Foundry; this code executes one declared tool per call.
It is a **different PEP placement** from the hosted runtime: the hosted
LOCAL-14/CTK evidence does not carry over (see ``README.md``).

Stdlib only. ``client`` is an ``openai.AsyncOpenAI`` bound to the harness agent
endpoint (``azure.ai.projects.aio.AIProjectClient(...).get_openai_client(agent_name=...)``),
or anything with the same ``responses.create`` and ``conversations.items.list`` coroutines.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable, Mapping

Executor = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


class HarnessTurnFailed(RuntimeError):
    """The harness turn stayed ``failed`` after reconciliation; nothing was re-executed."""

    def __init__(self, message: str, *, recorded: list[str], pending: list[str]):
        super().__init__(message)
        self.recorded, self.pending = recorded, pending


def _get(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def receipt_from_gateway(result: Mapping[str, Any]) -> dict[str, Any]:
    """Map a ``GovernedDispatcher.dispatch`` result to the model-facing receipt."""
    status = result.get("status")
    reason = result.get("reason_code")
    if status == "completed":
        return {"status": "EXECUTED", "effect": "applied", "result": result.get("result"),
                **({"receipt_id": result["receipt_id"]} if result.get("receipt_id") else {})}
    if status == "blocked":
        return {"status": "DENIED_BY_POLICY", "effect": "none", "reason_code": reason,
                "next_step": "do not retry; report that the action was not persisted and needs human review"}
    if status in ("pending_approval", "pending_confirmation"):
        return {"status": "PENDING_APPROVAL", "effect": "none", "reason_code": reason or status,
                "next_step": "not persisted yet; tell the user it waits for an authenticated approver"}
    if reason == "outcome_unknown":
        return {"status": "OUTCOME_UNKNOWN", "effect": "unknown", "reason_code": reason,
                "next_step": "do not retry; an operator reconciles the original operation"}
    return {"status": "NOT_EXECUTED", "effect": "none", "reason_code": reason or "gateway_unavailable",
            "next_step": "do not retry in this turn; report that the governance service is unavailable"}


def gateway_executor(dispatcher: Any, authorization: Callable[[], Any], actions: Mapping[str, str]) -> Executor:
    """Executor that runs a declared function through the governed gateway dispatcher.

    ``actions`` maps the function name the agent declares to the signed registry
    action name. ``authorization`` returns (or awaits) the workload bearer per call.
    """
    async def execute(name: str, arguments: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        bearer = authorization()
        if inspect.isawaitable(bearer):
            bearer = await bearer
        result = await dispatcher.dispatch(authorization=bearer, action=actions[name], arguments=arguments,
                                           idempotency_key=idempotency_key)
        return receipt_from_gateway(result)
    return execute


class HarnessFunctionBridge:
    """Drive one harness conversation turn and answer only the declared function tools."""

    def __init__(self, client: Any, tools: Mapping[str, Executor], *, max_reconcile: int = 2,
                 max_rounds: int = 8):
        self.client, self.tools = client, dict(tools)
        self.max_reconcile, self.max_rounds = max_reconcile, max_rounds
        self.outputs: dict[str, dict[str, Any]] = {}

    def own_calls(self, response: Any) -> list[Any]:
        return [item for item in _get(response, "output", []) or []
                if _get(item, "type") == "function_call" and _get(item, "name") in self.tools]

    async def run(self, conversation: str, input: Any) -> Any:
        response = await self.client.responses.create(conversation=conversation, input=input)
        response = await self._settle(conversation, response, [])
        for _ in range(self.max_rounds):
            pending = [c for c in self.own_calls(response) if _get(c, "call_id") not in self.outputs]
            if not pending:
                return response
            batch = [await self._output(conversation, c) for c in pending]
            response = await self.client.responses.create(conversation=conversation, input=batch)
            response = await self._settle(conversation, response, batch)
        raise HarnessTurnFailed("too many function-call rounds in one turn", recorded=sorted(self.outputs),
                                pending=[])

    async def _output(self, conversation: str, call: Any) -> dict[str, Any]:
        call_id = _get(call, "call_id")
        if call_id not in self.outputs:
            try:
                arguments = json.loads(_get(call, "arguments") or "{}")
            except ValueError:
                arguments = None
            if not isinstance(arguments, dict):
                receipt = {"status": "NOT_EXECUTED", "effect": "none", "reason_code": "invalid_arguments"}
            else:
                name = _get(call, "name")
                receipt = await self.tools[name](name, arguments, f"harness:{conversation}:{call_id}")
            self.outputs[call_id] = {"type": "function_call_output", "call_id": call_id,
                                     "output": json.dumps(receipt, sort_keys=True)}
        return self.outputs[call_id]

    async def _recorded(self, conversation: str) -> set[str]:
        page = await self.client.conversations.items.list(conversation)
        items = _get(page, "data", page)
        return {_get(i, "call_id") for i in items or [] if _get(i, "type") == "function_call_output"}

    async def _settle(self, conversation: str, response: Any, batch: list[dict[str, Any]]) -> Any:
        """Reconcile a transient ``failed`` turn before replaying anything."""
        for _ in range(self.max_reconcile):
            if _get(response, "status") != "failed":
                return response
            recorded = await self._recorded(conversation)
            missing = [o for o in batch if o["call_id"] not in recorded]
            if not missing:
                break
            response = await self.client.responses.create(conversation=conversation, input=missing)
        if _get(response, "status") == "failed":
            recorded = sorted(await self._recorded(conversation))
            raise HarnessTurnFailed(
                "harness turn failed; recorded outputs are not resent and no call is re-executed",
                recorded=recorded, pending=[o["call_id"] for o in batch if o["call_id"] not in recorded])
        return response
