# Canonical long-running hosted-agent container.py — opt-in crash-resilient
# Responses runtime (`--long-running` mode).
#
# Source of truth for `../../README.md § Long-running mode (opt-in, crash-resilient)`.
# Copy it verbatim as `src/agent/container.py` when the pilot opts into
# long-running work; edit only PHASES and the phase instructions.
#
# What the mode does (Foundry AgentServer Responses 2.4.0 / core 2.3.0):
#   - `ResponsesServerOptions(resilient_background=True)`: a stored background
#     response (`store=true, background=true`) whose process crashes, is
#     OOM-killed, redeployed or stopped is re-invoked by a later process of the
#     same session instead of being left non-terminal.
#   - One output item per phase and `yield stream.checkpoint()` after each, so
#     a recovered attempt resumes at the first un-checkpointed phase.
#   - A recovery branch seeded from `context.persisted_response`: the
#     checkpointed items keep their original ids and the second
#     `response.in_progress` is the client-visible snapshot reset.
#
# CONTRACT: recovery re-enters this handler from the top (at-least-once handler
# entry, NOT deterministic replay). The phase interrupted by the crash runs
# again from its start. Any non-idempotent effect inside a phase (governed
# write, approval consumption, audit ACK, email, payment) must be fenced with
# an idempotency key or a watermark before dispatch, as
# https://learn.microsoft.com/azure/foundry/agents/how-to/recover-long-running-work
# (§ Fence non-idempotent side effects) requires; Threadlight governed writes
# follow `threadlight-governed-actions`. Without this file, a background
# response is "background only, not crash-resilient".
#
# Pre-conditions (same as main.py): FOUNDRY_PROJECT_ENDPOINT (platform-injected)
# and AZURE_AI_MODEL_DEPLOYMENT_NAME (azure.yaml environmentVariables).
# Lifecycle lines `threadlight.long_running {json}` go to stdout so the local
# crash harness (threadlight-local-test/scripts/crash_recovery.py) and hosted
# container logs show entry, phase and checkpoint events.

import asyncio
import json
import logging
import os
import sys
from typing import Any

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
)

log = logging.getLogger(__name__)

# Fixed phase order; one checkpointed output item per phase. Keep each phase
# short enough that re-running it after a crash is acceptable.
PHASES: tuple[tuple[str, str], ...] = (
    ("analyze", "Analyze the request and list the facts and constraints that matter."),
    ("plan", "Using the analysis, write a short step-by-step plan."),
    ("execute", "Carry out the plan and report the concrete results."),
    ("report", "Write the final answer for the user from the previous phases."),
)

app = ResponsesAgentServerHost(options=ResponsesServerOptions(resilient_background=True))

_agent: Any = None


def lifecycle(event: str, **fields: Any) -> None:
    print("threadlight.long_running " + json.dumps({"event": event, **fields}, sort_keys=True),
          file=sys.stdout, flush=True)


def _get_agent() -> Any:
    """Built on first use so importing this module needs no Azure environment."""
    global _agent
    if _agent is None:
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import DefaultAzureCredential

        _agent = Agent(
            client=FoundryChatClient(
                project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                credential=DefaultAzureCredential(),
            ),
            instructions="You are a helpful assistant working through a fixed sequence of phases.",
            default_options={"store": False},  # Platform manages history
        )
    return _agent


async def run_phase(phase: str, instruction: str, input_text: str, prior: list[str]) -> str:
    """One phase of work. Fence any non-idempotent effect here (see CONTRACT)."""
    context = "\n\n".join(prior) or "(none)"
    prompt = f"Phase: {phase}\n{instruction}\n\nUser request:\n{input_text}\n\nPrevious phases:\n{context}"
    result = await _get_agent().run(prompt)
    return result.text


def _crash_after(phase_index: int, recovering: bool) -> None:
    """Crash-test hook (off unless set): exit hard right after a checkpoint."""
    target = os.environ.get("THREADLIGHT_SIMULATE_CRASH_AFTER_PHASE")
    if target is not None and not recovering and int(target) == phase_index:
        lifecycle("simulated_crash", phase=phase_index)
        os._exit(137)


def _item_text(item: Any) -> str:
    parts = item.get("content") or [] if isinstance(item, dict) else []
    return " ".join(part.get("text", "") for part in parts if isinstance(part, dict))


@app.response_handler
async def handler(request: CreateResponse, context: ResponseContext, cancellation_signal: asyncio.Event):
    recovering = bool(context.is_recovery and context.persisted_response is not None)
    if recovering:
        stream = ResponseEventStream(response_id=context.response_id, response=context.persisted_response)
        start = len(stream.response.get("output") or [])
    else:
        stream = ResponseEventStream(response_id=context.response_id, request=request)
        start = 0
    lifecycle("entry", response_id=context.response_id, is_recovery=bool(context.is_recovery),
              start_phase=start, pid=os.getpid())

    yield stream.emit_created()  # deduplicated in the store on recovery
    if context.shutdown.is_set():
        await context.exit_for_recovery()
    if cancellation_signal.is_set():
        return
    yield stream.emit_in_progress()  # on recovery: the snapshot reset point

    input_text = await context.get_input_text()
    prior = [_item_text(item) for item in (stream.response.get("output") or [])]
    for index, (phase, instruction) in enumerate(PHASES[start:], start):
        if cancellation_signal.is_set() or context.shutdown.is_set():
            break
        lifecycle("phase_start", response_id=context.response_id, phase=index, name=phase)
        message = stream.add_output_item_message()
        yield message.emit_added()
        text = message.add_text_content()
        yield text.emit_added()
        result = await run_phase(phase, instruction, input_text, prior)
        yield text.emit_delta(result)
        yield text.emit_text_done(result)
        yield text.emit_done()
        yield message.emit_done()
        lifecycle("phase_done", response_id=context.response_id, phase=index, name=phase)
        if cancellation_signal.is_set() or context.shutdown.is_set():
            break  # not checkpointed: a recovered attempt re-runs this phase
        yield stream.checkpoint()
        lifecycle("checkpointed", response_id=context.response_id, phase=index)
        prior.append(result)
        _crash_after(index, recovering)

    if context.shutdown.is_set():
        await context.exit_for_recovery()  # stay in_progress for the next lifetime
    if cancellation_signal.is_set():
        return
    yield stream.emit_completed()
    lifecycle("completed", response_id=context.response_id)


def main() -> None:
    app.run()  # Serves on port 8088


if __name__ == "__main__":
    main()
