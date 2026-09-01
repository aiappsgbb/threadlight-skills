"""Fixture agent module for the conformant-maf inventory test fixture.

Defines two governed actions using a minimal MAF-style ``tool`` decorator so
``discover_python_tools`` can find them by static AST inspection alone. This
module is never imported or executed by the inventory builder — it only
needs to be valid Python source.
"""
from __future__ import annotations

from typing import Any, Callable


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
