"""Fixture agent module for the unmediated-background mediation-graph fixture.

Declares one action, ``payments.refund`` (irreversible), with one dispatch
function per declared execution mode so ``mediation.py``'s static call-
evidence scanner can find each mode's real dispatch path by name convention:
``<mode-with-underscores>_<action-id-with-underscores>``.

Interactive, subagent, and direct-tool dispatch route every refund through
the pre-action mediation seam (``agent_hooks.pre_tool_call``) *before* the
governed ``tool_service`` executes it. Batch and background dispatch call
the raw payment-provider client directly instead — bypassing both the seam
and ``tool_service`` entirely — which is exactly the unmediated background/
batch path Task 4's fixture must exhibit. Background dispatch also proves
"post-model observation alone never counts": it still records to
``audit_sink`` *after* the direct provider call, but that trailing audit
record is not a pre-action control and must not make the path covered.

This module is never imported or executed by the assessor — it only needs
to be valid Python source for static AST inspection.
"""
from __future__ import annotations

from typing import Any, Callable


def tool(*, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Minimal stand-in for a framework tool-registration decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__tool_name__ = name
        return func

    return decorator


class _AgentHooks:
    """Stand-in for the Agent Hooks pre/post-tool interceptor seam."""

    def pre_tool_call(self, action_id: str, mode: str) -> None:
        ...

    def require_approval(self, action_id: str) -> None:
        ...

    def post_tool_call(self, action_id: str) -> None:
        ...


class _ToolService:
    """Stand-in for the governed tool-service that actually executes a refund."""

    def payments_refund(self, payment_id: str, amount: float) -> dict[str, Any]:
        return {"payment_id": payment_id, "amount": amount}


class _ProviderClient:
    """Stand-in for the raw payment-processor API client (no mediation)."""

    def charge_refund(self, payment_id: str, amount: float) -> dict[str, Any]:
        return {"payment_id": payment_id, "amount": amount}


class _OutputMediator:
    def mediate(self, result: dict[str, Any]) -> dict[str, Any]:
        return result


class _AuditSink:
    def record(self, action_id: str, mode: str) -> None:
        ...


agent_hooks = _AgentHooks()
tool_service = _ToolService()
provider = _ProviderClient()
output_mediator = _OutputMediator()
audit_sink = _AuditSink()


@tool(name="payments.refund")
def interactive_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Interactive refund: mediated by Agent Hooks before tool-service executes."""
    agent_hooks.pre_tool_call("payments.refund", "interactive")
    agent_hooks.require_approval("payments.refund")
    result = tool_service.payments_refund(payment_id, amount)
    agent_hooks.post_tool_call("payments.refund")
    mediated = output_mediator.mediate(result)
    audit_sink.record("payments.refund", "interactive")
    return mediated


def batch_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Batch refund: calls the payment provider directly, bypassing mediation."""
    result = provider.charge_refund(payment_id, amount)
    audit_sink.record("payments.refund", "batch")
    return result


def background_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Background refund: calls the payment provider directly, bypassing mediation.

    The trailing ``audit_sink.record`` call below is a post-model
    observation only; it must never be treated as pre-action mediation.
    """
    result = provider.charge_refund(payment_id, amount)
    audit_sink.record("payments.refund", "background")
    return result


def subagent_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Subagent-delegated refund: still mediated before tool-service executes."""
    agent_hooks.pre_tool_call("payments.refund", "subagent")
    result = tool_service.payments_refund(payment_id, amount)
    agent_hooks.post_tool_call("payments.refund")
    audit_sink.record("payments.refund", "subagent")
    return result


def direct_tool_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Direct-tool refund: still mediated before tool-service executes."""
    agent_hooks.pre_tool_call("payments.refund", "direct-tool")
    result = tool_service.payments_refund(payment_id, amount)
    agent_hooks.post_tool_call("payments.refund")
    audit_sink.record("payments.refund", "direct-tool")
    return result
