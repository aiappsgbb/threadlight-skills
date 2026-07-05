"""Azure Functions v2 Service Bus queue receiver — Shape #6 (escape hatch).

Flex Consumption, Python v2 programming model. Managed-identity Service Bus
connection (``ServiceBusConnection__fullyQualifiedNamespace`` app setting — no
connection strings). Idempotency + agent-invoke live in ``receiver_core``; a
failed invoke re-raises so the host abandons the message → native DLQ.
"""
import azure.functions as func

import receiver_core as core

app = func.FunctionApp()


@app.service_bus_queue_trigger(
    arg_name="msg",
    queue_name="%SERVICEBUS_QUEUE%",
    connection="ServiceBusConnection",
)
async def trigger(msg: func.ServiceBusMessage) -> None:
    payload = core.parse_body(msg.get_body(), msg.message_id)

    cred, store, cosmos = core.open_store()
    try:
        # raise_to_platform re-raises on failure → host abandons → native DLQ
        # after maxDeliveryCount. Success/duplicate complete the message.
        await core.handle(
            payload,
            store=store,
            invoke=core.invoke_agent,
            dead_letter=core.raise_to_platform,
        )
    finally:
        await cosmos.close()
        await cred.close()
