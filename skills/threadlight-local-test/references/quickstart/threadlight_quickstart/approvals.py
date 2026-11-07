"""Resolve native MAF approvals explicitly instead of dead-ending a turn.

Pattern 0 keeps human approval for SPEC write tools and skill scripts. A host
either asks a human (Streamlit buttons: the human may approve or reject any
shown call, including a skill script) or, when headless, applies an explicit
operator policy: every pending request is REJECTED unless the operator named
that tool with ``--approve-tool``, and that up-front policy can never approve
skill tools (``NEVER_AUTO_APPROVE``). A rejection performs no effect; the model
sees the rejection and finishes its answer, and each decision is reported with
the turn.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import AgentResponse, Message

NEVER_AUTO_APPROVE = frozenset({"run_skill_script", "load_skill", "read_skill_resource"})


@dataclass
class TurnResult:
    text: str
    decisions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    pending: "PendingApproval | None" = None


@dataclass
class PendingApproval:
    """A turn paused on native approval requests, with the history to resume it."""

    messages: list[Any]
    requests: list[Any]

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for request in self.requests:
            call = request.function_call
            arguments = call.arguments if call is not None else None
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            out.append({"id": request.id, "tool": call.name if call is not None else "?", "arguments": arguments})
        return out


def _user(prompt: str | list[Any]) -> list[Any]:
    return list(prompt) if isinstance(prompt, list) else [Message("user", [prompt])]


def pending_from_error(prompt: str | list[Any], updates: Iterable[Any], exc: BaseException) -> PendingApproval | None:
    """Rebuild the paused turn from streamed updates plus the update that raised."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    collected = [*updates, response] if not isinstance(response, AgentResponse) else None
    full = AgentResponse.from_updates(collected) if collected is not None else response
    requests = [r for r in full.user_input_requests if r.type == "function_approval_request"]
    if not requests:
        return None
    return PendingApproval(messages=[*_user(prompt), *full.messages], requests=requests)


def _responses(pending: PendingApproval, decide) -> tuple[Message, list[dict[str, Any]]]:
    contents, decisions = [], []
    for request, info in zip(pending.requests, pending.describe()):
        approved = bool(decide(info))
        contents.append(request.to_function_approval_response(approved=approved))
        decisions.append({**info, "approved": approved})
    return Message("user", contents), decisions


async def _drive(agent, messages: list[Any], decide, max_rounds: int) -> TurnResult:
    decisions: list[dict[str, Any]] = []
    for _ in range(max_rounds):
        try:
            response = await agent.run(messages)
        except RuntimeError as exc:
            pending = pending_from_error(messages, [], exc)
            if pending is None:
                return TurnResult(text="", decisions=decisions, error=f"{type(exc).__name__}: {exc}")
            if decide is None:
                return TurnResult(text="", decisions=decisions, error=str(exc), pending=pending)
            answer, made = _responses(pending, decide)
            decisions.extend(made)
            messages = [*pending.messages, answer]
            continue
        return TurnResult(text=response.text, decisions=decisions)
    return TurnResult(text="", decisions=decisions, error="approval_loop: too many approval rounds")


async def run_turn(agent, prompt: str, *, approve_tools: Iterable[str] = (), max_rounds: int = 4) -> TurnResult:
    """Headless turn: reject every pending approval unless the operator named the tool."""
    allowed = set(approve_tools) - NEVER_AUTO_APPROVE
    return await _drive(
        agent, _user(prompt),
        lambda info: info["tool"] in allowed and info["tool"] not in NEVER_AUTO_APPROVE, max_rounds)


async def continue_turn(agent, pending: PendingApproval, *, approved: bool, max_rounds: int = 4) -> TurnResult:
    """Resume a paused turn with the human's decision for the shown requests.

    This is the human path: the decision applies as given, skill scripts included.
    """
    answer, decisions = _responses(pending, lambda _info: approved)
    # A later request in the same turn is returned as a new pending decision.
    result = await _drive(agent, [*pending.messages, answer], None, max_rounds)
    return TurnResult(text=result.text, decisions=[*decisions, *result.decisions], error=result.error,
                      pending=result.pending)
