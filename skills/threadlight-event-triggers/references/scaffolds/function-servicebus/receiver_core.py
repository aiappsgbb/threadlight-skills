"""Pure core for the Service Bus Function receiver — Shape #6 (escape hatch).

No ``azure.functions`` import → unit-testable under a stdlib-only CI. Prefer the
ACA **Consumer** (#4, KEDA) for queue work; use a Service Bus *Function* only
when a documented Functions justification applies.
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
    """Service Bus idempotency key = the broker ``MessageId`` (``sb-{id}``),
    carried on the payload as ``message_id``. Falls back to a stable content
    hash when a producer omits it."""
    message_id = payload.get("message_id")
    if message_id:
        return f"sb-{message_id}"
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    import hashlib

    return "h-" + hashlib.sha256(blob).hexdigest()[:32]


def parse_body(raw: bytes, message_id: str) -> dict:
    """Decode a Service Bus message body to the receiver payload dict."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        body = {"raw": raw.decode("utf-8", "replace")}
    payload = dict(body) if isinstance(body, dict) else {"body": body}
    payload.setdefault("message_id", message_id)
    return payload


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
    except Exception as exc:
        await dead_letter(payload, exc)
        return {"status": "dead_lettered", "key": key, "error": str(exc)}
    if key:
        await store.mark_processed(key)
    return {"status": "processed", "key": key, "result": result}


async def raise_to_platform(payload, exc):
    """Dead-letter strategy for Service Bus: re-raise so the Functions host
    abandons the message. After ``maxDeliveryCount`` retries Service Bus moves
    it to the queue's **native** dead-letter sub-queue. Alert on DLQ depth."""
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
