# ACA Job (manual) receiver — Shape #2

Container Apps Job with a **manual** trigger — started on demand via the REST
`start` API. Use for a workflow step, an operator-driven replay ("re-enrich this
SKU range"), or a webhook receiver that starts the job and returns immediately.

## Files

| File | Purpose |
|------|---------|
| `receiver.py` | Entry point. Pure `handle()` core + lazy-wired Azure `main()`. |
| `receiver.bicep` | `Microsoft.App/jobs` with `triggerType: 'Manual'`. |
| `pyproject.toml` | uv-managed deps. |
| `Dockerfile` | Container build (ACR remote build). |
| `local.test.py` | Offline smoke test with synthetic input. |

Copy this directory to `src/triggers/{trigger-name}/`, then copy the verified
`IdempotencyStore` from [`../../idempotency-patterns.md`](../../idempotency-patterns.md)
into `src/triggers/_shared/idempotency.py`.

## Invocation

The `start` call supplies the payload as the `TRIGGER_PAYLOAD` env override
(JSON). A batch replay may carry many items under `items`:

```bash
az containerapp job start -n {prefix}-{trigger} -g {rg} \
  --env-vars TRIGGER_PAYLOAD='{"request_id":"replay-2026-07-03","items":[{"id":"sku-1"},{"id":"sku-2"}]}'
```

## Idempotency

Key = the caller's `request_id` (scoped by item `id` for batches), so a retried
`start` with the same request id is a no-op. **Require callers to pass a stable
`request_id`** — without one the receiver falls back to a canonical payload hash.
Default dedup window is 1h (set from spec § 10b `dedup_window`).

## Dead-letter

Failed items are persisted to a Storage Queue (`DLQ_QUEUE_URL`) and **not** marked
processed, so a re-run retries them. No DLQ configured → re-raise. Alert on queue
depth > 0.

## Replay

Re-issue `start` with the same `request_id` to safely retry (deduped), or a new
`request_id` to force a fresh run.

## RBAC (receiver UAMI)

Foundry project → `Azure AI User`; Cosmos → `Cosmos DB Built-in Data Contributor`;
Storage (DLQ) → `Storage Queue Data Message Sender`. Keyless only.

## Test locally

```bash
python3 local.test.py
TRIGGER_PAYLOAD='{"request_id":"r1"}' uv run python receiver.py   # real Azure
```
