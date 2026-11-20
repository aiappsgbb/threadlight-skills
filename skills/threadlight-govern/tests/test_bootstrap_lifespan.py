"""A bootstrap-activated host is started in one task and closed by another.

AgentServer core 2.3 binds a ContextVar in its lifespan and resets it on exit,
so entering ``router.lifespan_context`` in ``activate()`` and leaving it in
``aclose()`` raises ``ValueError: ... was created in a different Context``.
Runs against the real pinned AgentServer host, not a model of it.
"""
import asyncio
from contextlib import AsyncExitStack

import pytest

invocations = pytest.importorskip("azure.ai.agentserver.invocations")

from govern_control_plane.bootstrap import owned_lifespan


def test_host_lifespan_survives_cross_task_start_and_close():
    events = []

    async def scenario():
        host = invocations.InvocationAgentServerHost()
        stack = AsyncExitStack()

        async def activate():
            await stack.enter_async_context(owned_lifespan(host))
            events.append("started")

        await asyncio.create_task(activate())
        await asyncio.create_task(stack.aclose())
        events.append("closed")

    asyncio.run(scenario())
    assert events == ["started", "closed"]


def test_host_startup_failure_propagates_to_the_activating_task():
    class Broken:
        class router:
            @staticmethod
            def lifespan_context(app):
                raise RuntimeError("startup failed")

    async def scenario():
        async with owned_lifespan(Broken()):
            pass

    with pytest.raises(RuntimeError, match="startup failed"):
        asyncio.run(scenario())
