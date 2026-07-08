"""Pure core for the HTTP webhook Function receiver — Shape #5 (escape hatch).

No ``azure.functions`` import → unit-testable under a stdlib-only CI. The thin
``function_app.py`` wraps the trigger and injects the wiring below. Prefer an
ACA App (#3) with ``minReplicas: 1`` for anything that matters; use a Function
HTTP webhook only when a documented Functions justification applies (see SKILL
§ "When to choose Functions anyway").
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
    """HTTP idempotency key = the sender's ``X-Request-Id`` (REQUIRED — the
    function rejects requests without it). Carried on the payload as
    ``request_id``."""
    request_id = payload.get("request_id")
    if not request_id:
        raise KeyError("X-Request-Id header is required for idempotency")
    return f"http-{request_id}"


def format_input(payload: dict) -> str:
    """Build the agent input string. CUSTOMIZE for your agent (spec § 10b)."""
    return json.dumps(payload, sort_keys=True, default=str)


async def handle(payload, *, store, invoke, dead_letter, key_fn=derive_key) -> dict:
    """Idempotent process-one-request core. Pure + injectable → unit-testable."""
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
    """Open the Cosmos ``IdempotencyStore``. Returns ``(cred, store, client)``
    so the caller can close ``client`` and ``cred`` after ``handle``."""
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from _shared.idempotency import IdempotencyStore

    cred = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    container = client.get_database_client("threadlight").get_container_client(
        "trigger_idempotency"
    )
    return cred, IdempotencyStore(container, DEDUP_WINDOW), client


async def dead_letter_to_queue(payload, exc):
    """Persist a failed request to a Storage Queue poison store. The function
    also returns 502 so the sender retries; the idempotency gate makes that
    retry safe. Reconcile/replay the queue for requests that never succeed."""
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.queue.aio import QueueClient

    queue_url = os.environ.get("DLQ_QUEUE_URL", "")
    if not queue_url:
        raise exc
    async with DefaultAzureCredential() as cred:
        async with QueueClient.from_queue_url(queue_url, credential=cred) as queue:
            await queue.send_message(
                json.dumps({"payload": payload, "error": str(exc)}, default=str)
            )
