"""Fixture agent module proving a *post-hoc* Agent Hooks call never counts.

Declares one action, ``notifications.send`` (external-egress), with a
single ``direct-tool`` dispatch function that calls the raw notification
provider *first* and only calls ``agent_hooks.pre_tool_call`` *afterward*.
This is the "critical false-pass" regression case: naively checking only
whether ``pre-action-seam`` and ``tool-service`` both *appear* somewhere in
a path's canonical node list — without checking their real relative call
order in the source — would wrongly treat this as covered just because
``pre-action-seam`` precedes ``tool-service`` in ``CANONICAL_NODE_ORDER``.
The scanner must instead read the two calls' actual source positions and
recognize that the seam call happened after the state change already ran,
so this path must be reported exactly like any other unmediated bypass:
``must-fix``, with no ``pre-action-seam`` node credited to it at all.

This module is never imported or executed by the assessor — it only needs
to be valid Python source for static AST inspection.
"""
from __future__ import annotations

from typing import Any


class _AgentHooks:
    """Stand-in for the Agent Hooks pre/post-tool interceptor seam."""

    def pre_tool_call(self, action_id: str, mode: str) -> None:
        ...


class _ProviderClient:
    """Stand-in for the raw notification-provider API client."""

    def send(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


agent_hooks = _AgentHooks()
provider = _ProviderClient()


def direct_tool_notifications_send(**kwargs: Any) -> dict[str, Any]:
    """Direct-tool send: calls the provider *before* the seam, not after.

    The ``agent_hooks.pre_tool_call`` call below is real code that really
    runs — but only *after* the notification has already been sent, so it
    is a post-hoc observation, not pre-action mediation. It must never be
    credited as covering this path.
    """
    result = provider.send(**kwargs)
    agent_hooks.pre_tool_call("notifications.send", "direct-tool")
    return result
