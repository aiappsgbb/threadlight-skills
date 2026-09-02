"""Fixture agent module for the conformant-maf inventory test fixture.

Defines two governed actions using a minimal MAF-style ``tool`` decorator so
``discover_python_tools`` can find them by static AST inspection alone. This
module is never imported or executed by the inventory builder — it only
needs to be valid Python source.
"""
from __future__ import annotations

from typing import Any, Callable


class _Namespace:
    """Minimal stand-in for a mediation seam namespace.

    mediation.py recognizes mediation evidence by statically inspecting the
    *shape* of calls in dispatch function bodies (bare ``name.attr(...)``
    call expressions); it never imports or executes this module, so these
    stand-ins only need to exist to keep the module free of unresolved-name
    lint noise -- they are never actually invoked at runtime.
    """

    def __getattr__(self, _name: str) -> Callable[..., Any]:
        def _call(*args: Any, **kwargs: Any) -> None:
            return None

        return _call


agent_hooks = _Namespace()
tool_service = _Namespace()
output_mediator = _Namespace()
audit_sink = _Namespace()


def tool(*, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Minimal stand-in for a framework tool-registration decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__tool_name__ = name
        return func

    return decorator


@tool(name="customer.lookup")
def customer_lookup(customer_id: str) -> dict[str, Any]:
    """Look up a customer record. Read-only; no state mutation."""
    return {"customer_id": customer_id}


@tool(name="payments.refund")
def payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    """Issue a refund. Irreversible; requires approval per SPEC section 8."""
    return {"payment_id": payment_id, "amount": amount}


# ---------------------------------------------------------------------------
# Mediation dispatch functions.
#
# mediation.py recognizes these functions by static AST inspection using the
# convention ``_mode_action_function_name(action_id, mode)`` ==
# ``"{mode}_{action_id}"`` (hyphens and dots normalized to underscores). Every
# non-exclusively-provider-hosted action is assessed across all five
# REQUIRED_NON_PROVIDER_MODES, so both ``customer.lookup`` and
# ``payments.refund`` need one dispatch function per mode below. Each
# function is never imported or executed -- mediation.py only inspects the
# call shapes in its body: a bare ``agent_hooks.pre_tool_call(...)`` call
# that source-precedes a bare ``tool_service.*``/``provider.*`` call proves a
# pre-action seam mediates the tool-service invocation.


def interactive_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="interactive")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="interactive")
    return result


def batch_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="batch")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="batch")
    return result


def background_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="background")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="background")
    return result


def subagent_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="subagent")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="subagent")
    return result


def direct_tool_customer_lookup(customer_id: str) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="customer.lookup", mode="direct-tool")
    result = tool_service.invoke("customer.lookup", customer_id=customer_id)
    agent_hooks.post_tool_call(action_id="customer.lookup", mode="direct-tool")
    return result


def interactive_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="interactive")
    agent_hooks.require_approval(action_id="payments.refund", mode="interactive")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="interactive")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="interactive")
    return result


def batch_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="batch")
    agent_hooks.require_approval(action_id="payments.refund", mode="batch")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="batch")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="batch")
    return result


def background_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="background")
    agent_hooks.require_approval(action_id="payments.refund", mode="background")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="background")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="background")
    return result


def subagent_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="subagent")
    agent_hooks.require_approval(action_id="payments.refund", mode="subagent")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="subagent")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="subagent")
    return result


def direct_tool_payments_refund(payment_id: str, amount: float) -> dict[str, Any]:
    agent_hooks.pre_tool_call(action_id="payments.refund", mode="direct-tool")
    agent_hooks.require_approval(action_id="payments.refund", mode="direct-tool")
    result = tool_service.invoke("payments.refund", payment_id=payment_id, amount=amount)
    audit_sink.record(action_id="payments.refund", mode="direct-tool")
    agent_hooks.post_tool_call(action_id="payments.refund", mode="direct-tool")
    return result
