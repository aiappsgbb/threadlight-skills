# ACA Job (cron) receiver — Shape #1

Scheduled Container Apps Job that runs the receiver **once per cron tick**,
fetches the work due this run, and invokes the Foundry-hosted agent once per
item — behind an idempotency gate.

Use for periodic batch: nightly KPI rollup, hourly reconciliation, an SLA
watcher that escalates stale approvals (pairs with `threadlight-hitl-patterns`).

## Files

| File | Purpose |
|------|---------|
| `receiver.py` | Entry point. Pure `handle()` core + lazy-wired Azure `main()`. |
| `receiver.bicep` | `Microsoft.App/jobs` with `triggerType: 'Schedule'`. |
| `pyproject.toml` | uv-managed deps. |
| `Dockerfile` | Container build (ACR remote build). |
| `local.test.py` | Offline smoke test with synthetic input. |

Copy this directory to `src/triggers/{trigger-name}/`, then copy the verified
`IdempotencyStore` from [`../../idempotency-patterns.md`](../../idempotency-patterns.md)
into `src/triggers/_shared/idempotency.py`.

## Idempotency

Key = the run's ISO date (`cron-2026-07-03[-{item_id}]`), so **re-running the
job today is a no-op**. Backed by the Cosmos `trigger_idempotency` container
(`partitionKey: /id`, per-item TTL = 2× the dedup window). The default dedup
window is 24h — set it from spec § 10b `dedup_window`.

## Dead-letter

A failed agent invocation is persisted to a Storage Queue (`DLQ_QUEUE_URL`) and
the item is **not** marked processed, so the next run retries it. If no DLQ is
configured the receiver re-raises so the job execution surfaces the failure.
**Alert on queue depth > 0.**

## Replay

- `run_date` in the payload lets you replay a specific day; a fresh date is a
  fresh key so it re-processes.
- To force a full re-process, temporarily shrink `DEDUP_WINDOW` so old keys age
  out, or clear the matching rows from `trigger_idempotency`.

## RBAC (assign to the receiver UAMI at provisioning)

| Resource | Role |
|----------|------|
| Foundry project | `Azure AI User` |
| Cosmos (idempotency + audit) | `Cosmos DB Built-in Data Contributor` |
| Storage (DLQ) | `Storage Queue Data Message Sender` |

Keyless only — `DefaultAzureCredential` picks up the UAMI via `AZURE_CLIENT_ID`.
Never connection strings or shared keys.

## Test locally

```bash
python3 local.test.py          # offline: proves processed / skipped / dead-lettered
uv run python receiver.py       # against real Azure (needs env vars + az login)
```
