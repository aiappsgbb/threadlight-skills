"""Azure Functions v2 Event Grid receiver — Shape #7 (escape hatch).

Flex Consumption, Python v2 programming model. Idempotency + agent-invoke live
in ``receiver_core``; a failed invoke re-raises so Event Grid retries and then
delivers to the subscription's dead-letter destination.
"""
import azure.functions as func

import receiver_core as core

app = func.FunctionApp()


@app.event_grid_trigger(arg_name="event")
async def trigger(event: func.EventGridEvent) -> None:
    payload = core.build_payload(event.id, event.get_json())

    cred, store, cosmos = core.open_store()
    try:
        # raise_to_platform re-raises on failure → Event Grid retries → the
        # subscription's dead-letter destination. Success/duplicate return.
        await core.handle(
            payload,
            store=store,
            invoke=core.invoke_agent,
            dead_letter=core.raise_to_platform,
        )
    finally:
        await cosmos.close()
        await cred.close()
