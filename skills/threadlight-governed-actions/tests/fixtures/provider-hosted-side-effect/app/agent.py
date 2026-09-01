"""Fixture agent module for the provider-hosted-side-effect mediation fixture.

Declares two provider-hosted tools purely as MAF-style ``@tool``-decorated
stubs, mirroring the ``conformant-maf`` fixture's convention. Neither
declares any ``equivalent_control`` metadata in ``agent.yaml`` — there is no
authorization, idempotency, or transaction-boundary evidence naming a
server-side control for either tool, so any side-effecting provider-hosted
tool here is genuinely unsupported (``MED-003``/``must-fix``) rather than
merely under-documented.

``mail.send`` is ``external-egress`` (side-effecting): sending mail through
a provider-hosted tool has no pre-tool interception point and no declared
equivalent server-side control, so it cannot be supported.

``search.lookup`` is ``read``-only: a read has no state to protect, so the
assessor reports it ``not-applicable`` rather than ``must-fix`` even though
it is also provider-hosted and also has no declared equivalent control.

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


@tool(name="mail.send")
def mail_send(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send mail via the provider-hosted mail tool. No server-side control."""
    return {"to": to, "subject": subject}


@tool(name="search.lookup")
def search_lookup(query: str) -> dict[str, Any]:
    """Read-only provider-hosted web search lookup."""
    return {"query": query}
