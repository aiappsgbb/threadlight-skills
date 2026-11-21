"""Background-only Responses agent WITHOUT the crash-recovery opt-in (control).

Same phase shape and lifecycle lines as the long-running template, but the host
keeps the SDK default `resilient_background=False` and has no recovery branch.
"""
import asyncio
import json
import os
import sys

from azure.ai.agentserver.responses import (
    CreateResponse, ResponseContext, ResponseEventStream, ResponsesAgentServerHost)

PHASES = (("analyze", ""), ("plan", ""), ("execute", ""), ("report", ""))
app = ResponsesAgentServerHost()


def lifecycle(event, **fields):
    print("threadlight.long_running " + json.dumps({"event": event, **fields}, sort_keys=True),
          file=sys.stdout, flush=True)


async def run_phase(phase, instruction, input_text, prior):
    raise RuntimeError("the crash harness replaces run_phase with offline phases")


@app.response_handler
async def handler(request: CreateResponse, context: ResponseContext, cancellation_signal: asyncio.Event):
    stream = ResponseEventStream(response_id=context.response_id, request=request)
    lifecycle("entry", response_id=context.response_id, is_recovery=bool(context.is_recovery),
              start_phase=0, pid=os.getpid())
    yield stream.emit_created()
    yield stream.emit_in_progress()
    input_text = await context.get_input_text()
    prior = []
    for index, (phase, instruction) in enumerate(PHASES):
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
        # Without the opt-in a checkpoint is not a recovery point; the event is
        # emitted so the harness can crash at the same place as the template.
        lifecycle("checkpointed", response_id=context.response_id, phase=index)
        prior.append(result)
    yield stream.emit_completed()


def main():
    app.run()


if __name__ == "__main__":
    main()
