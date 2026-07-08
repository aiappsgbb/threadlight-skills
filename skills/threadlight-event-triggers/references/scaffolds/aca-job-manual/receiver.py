"""ACA Job (manual) trigger receiver — Shape #2 (threadlight-event-triggers).

A Container Apps Job with a **manual** (REST ``start``) trigger. A webhook
receiver, a workflow step, or an operator invokes it on demand — e.g. "replay
enrichment for this SKU range". The invocation supplies the payload via the
``TRIGGER_PAYLOAD`` env override (JSON); the receiver invokes the Foundry-hosted
agent behind an idempotency gate keyed on the caller's request id, so a retried
``start`` never double-processes.

Design (identical discipline to the cron receiver):
  * ``handle()`` is a PURE, injectable core — unit-testable, imports no Azure SDK.
  * All Azure wiring lazy-imports inside ``main()`` / factory helpers.

Env vars (injected by ``receiver.bicep`` + the ``start`` override):
  AZURE_CLIENT_ID / PROJECT_ENDPOINT / AGENT_NAME / COSMOS_ENDPOINT
  TRIGGER_PAYLOAD    JSON payload for this execution (set per ``start`` call)
  DLQ_QUEUE_URL      Storage Queue url for dead-lettered payloads (optional)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import timedelta

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "")

# Manual replays are usually re-run within an hour; short dedup window.
DEDUP_WINDOW = timedelta(hours=1)
MAX_CONCURRENT = 4


def _hash_key(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return "h-" + hashlib.sha256(blob).hexdigest()[:16]


def derive_key(payload: dict) -> str:
    """Manual idempotency key = the caller-supplied request id (require callers
    to pass a stable one for safe replays), scoped by item id for batches; else
    a canonical payload hash."""
    request_id = payload.get("request_id")
    item_id = payload.get("id")
    if request_id and item_id:
        return f"manual-{request_id}-{item_id}"
    if request_id:
        return f"manual-{request_id}"
    return _hash_key(payload)


def format_input(payload: dict) -> str:
    """Build the agent input string. CUSTOMIZE for your agent (spec § 10b)."""
    return json.dumps(payload, sort_keys=True, default=str)


async def handle(payload, *, store, invoke, dead_letter, key_fn=derive_key) -> dict:
    """Idempotent process-one-item core. Pure + injectable → unit-testable."""
    key = key_fn(payload)
    if key and await store.is_already_processed(key):
        return {"status": "skipped", "reason": "duplicate", "key": key}
    try:
        result = await invoke(payload)
    except Exception as exc:  # receiver must never crash the whole job run
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


def _make_store():
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from _shared.idempotency import IdempotencyStore

    cred = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    container = client.get_database_client("threadlight").get_container_client(
        "trigger_idempotency"
    )
    return IdempotencyStore(container, DEDUP_WINDOW), client, cred


async def _dead_letter(payload, exc):
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


def _load_payload() -> dict:
    """The manual ``start`` call supplies input via the ``TRIGGER_PAYLOAD`` env
    override (JSON). CUSTOMIZE if you mount input differently."""
    return json.loads(os.environ.get("TRIGGER_PAYLOAD", "{}"))


async def main() -> None:
    store, cosmos_client, cred = _make_store()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _one(item):
        async with sem:
            return await handle(
                item, store=store, invoke=invoke_agent, dead_letter=_dead_letter
            )

    try:
        payload = _load_payload()
        request_id = payload.get("request_id")
        # A batch replay may carry many items under "items"; each inherits the
        # caller's request id so keys stay stable + unique per item.
        items = payload.get("items") or [payload]
        for item in items:
            if request_id:
                item.setdefault("request_id", request_id)
        results = await asyncio.gather(*(_one(i) for i in items))
        summary: dict[str, int] = {}
        for result in results:
            summary[result["status"]] = summary.get(result["status"], 0) + 1
        print(json.dumps({"processed_items": len(results), "summary": summary}))
    finally:
        await cosmos_client.close()
        await cred.close()


if __name__ == "__main__":
    asyncio.run(main())
