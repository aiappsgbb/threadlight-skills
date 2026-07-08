"""ACA Consumer (KEDA) trigger receiver — Shape #4 (threadlight-event-triggers).

A Container App with **no ingress** that pulls from a Service Bus queue and
invokes the Foundry-hosted agent per message, behind an idempotency gate keyed
on the Service Bus ``MessageId``. KEDA scales it 0->N on queue depth (see
``receiver.bicep``). On failure the message is dead-lettered natively (Service
Bus DLQ); on success/skip it is completed.

Swap the source (Event Grid / Event Hubs / Cosmos change feed / Kafka) by
changing the pull loop below **and** the KEDA scaler rule in ``receiver.bicep``
(see README).

Design:
  * ``handle()`` is a PURE, injectable core — unit-testable, imports no Azure SDK.
  * All Azure wiring lazy-imports inside ``main()`` / factory helpers.

Env vars (injected by ``receiver.bicep``):
  AZURE_CLIENT_ID / PROJECT_ENDPOINT / AGENT_NAME / COSMOS_ENDPOINT
  SERVICEBUS_NAMESPACE   FQDN host, no https://, no trailing slash
  SERVICEBUS_QUEUE       queue name to consume
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "")
SERVICEBUS_NAMESPACE = os.environ.get("SERVICEBUS_NAMESPACE", "")
SERVICEBUS_QUEUE = os.environ.get("SERVICEBUS_QUEUE", "")

# Streaming source: events retry fast, so a short dedup window is enough.
DEDUP_WINDOW = timedelta(minutes=5)


def _hash_key(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return "h-" + hashlib.sha256(blob).hexdigest()[:16]


def derive_key(payload: dict) -> str:
    """Service Bus idempotency key = ``MessageId`` (unique per send). Falls back
    to a canonical payload hash when a message carries no id."""
    message_id = payload.get("message_id")
    return f"sb-{message_id}" if message_id else _hash_key(payload)


def format_input(payload: dict) -> str:
    """Build the agent input string. CUSTOMIZE for your agent (spec § 10b)."""
    return json.dumps(payload, sort_keys=True, default=str)


async def handle(payload, *, store, invoke, dead_letter, key_fn=derive_key) -> dict:
    """Idempotent process-one-message core. Pure + injectable → unit-testable."""
    key = key_fn(payload)
    if key and await store.is_already_processed(key):
        return {"status": "skipped", "reason": "duplicate", "key": key}
    try:
        result = await invoke(payload)
    except Exception as exc:  # routed to the Service Bus DLQ by the caller
        await dead_letter(payload, exc)
        return {"status": "dead_lettered", "key": key, "error": str(exc)}
    if key:
        await store.mark_processed(key)
    return {"status": "processed", "key": key, "result": result}


# --------------------------------------------------------------------------
# Production wiring — lazy Azure imports keep the pure core import-clean.
# --------------------------------------------------------------------------

async def invoke_agent(payload):
    """Only supported path to invoke a Foundry-hosted agent from a container
    (``azure-ai-projects>=2.0.0``)."""
    from azure.ai.projects.aio import AIProjectClient
    from azure.identity.aio import DefaultAzureCredential

    async with DefaultAzureCredential() as cred:
        async with AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=cred, allow_preview=True
        ) as project:
            openai_client = project.get_openai_client(agent_name=AGENT_NAME)
            return await openai_client.responses.create(
                input=format_input(payload), stream=False
            )


def _make_store(cred):
    """Wire the verified Cosmos ``IdempotencyStore`` on a shared credential."""
    from azure.cosmos.aio import CosmosClient

    from _shared.idempotency import IdempotencyStore

    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    container = client.get_database_client("threadlight").get_container_client(
        "trigger_idempotency"
    )
    return IdempotencyStore(container, DEDUP_WINDOW), client


def _parse_message(message) -> dict:
    """Parse a Service Bus message body to a payload dict and stamp its
    ``MessageId`` for the idempotency key. CUSTOMIZE the body schema."""
    raw = str(message)  # ServiceBusReceivedMessage.__str__ returns the body
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            payload = {"body": payload}
    except json.JSONDecodeError:
        payload = {"raw": raw}
    payload["message_id"] = message.message_id
    return payload


async def main() -> None:
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus.aio import ServiceBusClient

    async with DefaultAzureCredential() as cred:
        store, cosmos_client = _make_store(cred)
        try:
            async with ServiceBusClient(
                fully_qualified_namespace=SERVICEBUS_NAMESPACE, credential=cred
            ) as servicebus:
                receiver = servicebus.get_queue_receiver(
                    queue_name=SERVICEBUS_QUEUE, max_wait_time=30
                )
                async with receiver:
                    async for message in receiver:
                        payload = _parse_message(message)

                        async def _dead_letter(_payload, exc, _message=message):
                            await receiver.dead_letter_message(
                                _message,
                                reason="agent-failure",
                                error_description=str(exc)[:4096],
                            )

                        result = await handle(
                            payload,
                            store=store,
                            invoke=invoke_agent,
                            dead_letter=_dead_letter,
                        )
                        if result["status"] in ("processed", "skipped"):
                            await receiver.complete_message(message)
                        # dead_lettered → already dead-lettered above; do NOT complete
        finally:
            await cosmos_client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
