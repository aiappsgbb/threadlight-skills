"""Fail explicitly when an unattended host cannot resolve native user input."""
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agent_framework import (
    AgentContext, AgentMiddleware, AgentResponse, AgentResponseUpdate, ResponseStream,
)

ResponseT = TypeVar("ResponseT", AgentResponse, AgentResponseUpdate)


class ApprovalRequiredError(RuntimeError):
    """The run is incomplete; no approval has been granted by this host."""


def require_resolved_approvals(response: ResponseT) -> ResponseT:
    if response.user_input_requests:
        raise ApprovalRequiredError(
            "approval_required: this host cannot resolve pending user approval. "
            "Use an approval-capable host or an explicitly reviewed tool policy; "
            "do not treat this turn as a completed report."
        )
    return response


class RequireResolvedApprovals(AgentMiddleware):
    async def process(
        self, context: AgentContext, call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()
        if isinstance(context.result, ResponseStream):
            context.result.with_transform_hook(require_resolved_approvals)
            context.result.with_result_hook(require_resolved_approvals)
        elif context.result is not None:
            require_resolved_approvals(context.result)
