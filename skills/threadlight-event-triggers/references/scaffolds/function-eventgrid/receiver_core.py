"""Pure core for the Event Grid Function receiver — Shape #7 (escape hatch).

No ``azure.functions`` import → unit-testable under a stdlib-only CI.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "")
DEDUP_WINDOW = timedelta(minutes=5)


def derive_key(payload: dict) -> str:
    """Event Grid idempotency key = the CloudEvent/EventGridEvent ``id``
    (``eg-{id}``), carried on the payload as ``event_id``. Event Grid guarantees
    at-least-once delivery, so the same ``id`` can arrive more than once."""
    event_id = payload.get("event_id")
    if not event_id:
        raise KeyError("event id is required for idempotency")
    return f"eg-{event_id}"


def build_payload(event_id: str, data) -> dict:
    """Assemble the receiver payload from an Event Grid event's id + data."""
    payload = dict(data) if isinstance(data, dict) else {"data": data}
    payload["event_id"] = event_id
    return payload


def format_input(payload: dict) -> str:
    """Build the agent input string. CUSTOMIZE for your agent (spec § 10b)."""
    return json.dumps(payload, sort_keys=True, default=str)


async def handle(payload, *, store, invoke, dead_letter, key_fn=derive_key) -> dict:
    """Idempotent process-one-event core. Pure + injectable → unit-testable."""
    key = key_fn(payload)
    if key and await store.is_already_processed(key):
        return {"status": "skipped", "reason": "duplicate", "key": key}
    try:
        result = await invoke(payload)
    except Exception as exc:
        await dead_letter(payload, exc)
        return {"status": "dead_lettered", "key": key, "error": str(exc)}
    if key:
        await store.mark_processed(key)
    return {"status": "processed", "key": key, "result": result}


async def raise_to_platform(payload, exc):
    """Dead-letter strategy for Event Grid: re-raise so the Functions host
    reports failure. Event Grid retries with backoff, then delivers to the
    subscription's configured **dead-letter destination** (a Storage blob
    container). Configure it on the event subscription. Alert on that container."""
    raise exc


# --------------------------------------------------------------------------
# Production wiring — lazy Azure imports keep this module import-clean.
# --------------------------------------------------------------------------

async def invoke_agent(payload):
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


def open_store():
    """Open the Cosmos ``IdempotencyStore``. Returns ``(cred, store, client)``."""
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from _shared.idempotency import IdempotencyStore

    cred = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    container = client.get_database_client("threadlight").get_container_client(
        "trigger_idempotency"
    )
    return cred, IdempotencyStore(container, DEDUP_WINDOW), client
