"""ACA Job (cron) trigger receiver — Shape #1 (threadlight-event-triggers).

Runs once per scheduled tick (Container Apps Job, ``triggerType: 'Schedule'``).
Fetches the work that is due, then invokes the Foundry-hosted agent once per
item behind an idempotency gate so a same-day re-run (or a retried job
execution) never double-processes.

Design:
  * ``handle()`` is a PURE, injectable core — it takes the idempotency store,
    the agent-invoke coroutine and the dead-letter sink as arguments, so it is
    unit-testable with fakes and imports no Azure SDK.
  * All Azure wiring is lazy-imported inside ``main()`` / the factory helpers,
    so ``import receiver`` stays dependency-free (local tests + CI need no
    Azure packages installed).

Env vars (injected by ``receiver.bicep``):
  AZURE_CLIENT_ID    UAMI client id (DefaultAzureCredential picks it up)
  PROJECT_ENDPOINT   Foundry project endpoint
  AGENT_NAME         hosted agent name to bind the OpenAI client to
  COSMOS_ENDPOINT    Cosmos account endpoint (idempotency table)
  DLQ_QUEUE_URL      Storage Queue url for dead-lettered payloads (optional)
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "")

# Dedup window: for a daily cron a 24h window makes a same-day re-run a no-op.
# Set from spec § 10b `dedup_window`; the Cosmos row TTL is 2x this window.
DEDUP_WINDOW = timedelta(hours=24)
MAX_CONCURRENT = 4


def derive_key(payload: dict) -> str:
    """Cron idempotency key = the run's ISO date (+ item id when present), so
    re-running *today* is a no-op (idempotency decision tree in
    references/idempotency-patterns.md). Falls back to today's UTC date when
    the payload carries no explicit ``run_date``.
    """
    run_date = payload.get("run_date") or datetime.now(timezone.utc).date().isoformat()
    item_id = payload.get("id")
    return f"cron-{run_date}-{item_id}" if item_id else f"cron-{run_date}"


def format_input(payload: dict) -> str:
    """Build the agent input string. CUSTOMIZE: shape this to your agent's
    expected prompt / structured input for the process (spec § 10b)."""
    return json.dumps(payload, sort_keys=True, default=str)


async def handle(payload, *, store, invoke, dead_letter, key_fn=derive_key) -> dict:
    """Idempotent process-one-item core. Pure + injectable → unit-testable.

    Returns a small status dict:
      ``{"status": "skipped"|"processed"|"dead_lettered", "key": ..., ...}``
    """
    key = key_fn(payload)
    if key and await store.is_already_processed(key):
        return {"status": "skipped", "reason": "duplicate", "key": key}
    try:
        result = await invoke(payload)
    except Exception as exc:  # receiver must never crash the whole job run
        await dead_letter(payload, exc)
        # NOT marked processed → the next run can retry this item.
        return {"status": "dead_lettered", "key": key, "error": str(exc)}
    if key:
        await store.mark_processed(key)
    return {"status": "processed", "key": key, "result": result}


# --------------------------------------------------------------------------
# Production wiring — everything below lazy-imports Azure SDKs so the pure
# core above imports clean in tests / CI with no Azure packages installed.
# --------------------------------------------------------------------------

async def invoke_agent(payload):
    """The ONLY runtime-supported path to invoke a Foundry-hosted agent from a
    containerized receiver (``azure-ai-projects>=2.0.0``). Do NOT use
    ``agent_framework.foundry.FoundryAgent`` / ``AzureAIAgentClient`` (both
    removed) — see foundry-hosted-agents SKILL for the canonical pattern."""
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
    """Wire the verified Cosmos ``IdempotencyStore`` (copied verbatim from
    references/idempotency-patterns.md into ``_shared/idempotency.py`` by the
    skill's Step 3). Returns ``(store, cosmos_client, credential)`` so ``main``
    can close them."""
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
    """Persist the failed payload to a Storage Queue so nothing is lost. Alert
    on queue depth > 0 (see README). If no DLQ is configured, re-raise so the
    job execution surfaces the failure instead of swallowing it."""
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


async def fetch_due_items() -> list[dict]:
    """CUSTOMIZE: return the batch of items due this tick (query your source of
    truth — DB, queue snapshot, API). Each dict is one ``payload`` for
    ``handle()``. The default returns a single tick marker so the scaffold runs
    end-to-end out of the box."""
    return [{"run_date": datetime.now(timezone.utc).date().isoformat()}]


async def main() -> None:
    store, cosmos_client, cred = _make_store()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _one(item):
        async with sem:
            return await handle(
                item, store=store, invoke=invoke_agent, dead_letter=_dead_letter
            )

    try:
        items = await fetch_due_items()
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
