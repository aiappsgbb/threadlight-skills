# Function HTTP webhook receiver — Shape #5 (escape hatch)

Azure Functions **v2** (Python programming model: `function_app.py` +
decorators, **no** legacy `function.json`) on **Flex Consumption**, managed
identity only. A lightweight HTTP webhook that invokes the Foundry-hosted agent
behind an idempotency gate.

> ⚠️ **Escape hatch.** Cold starts + binding quirks + an ops team that must
> learn Functions usually cost more than the perceived simplicity. Prefer an
> **ACA App (#3)** with `minReplicas: 1` for any webhook that matters. Use this
> only when a documented Functions justification applies (see SKILL § "When to
> choose Functions anyway").

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Thin v2 HTTP trigger — validates the header, calls the core. |
| `receiver_core.py` | Pure, unit-tested `handle()` + idempotency + agent invoke + DLQ. |
| `host.json` | Functions host config (v2 extension bundle). |
| `requirements.txt` | Deps (Functions installs from this at deploy). |
| `local.test.py` | Offline smoke test of the core. |

Copy this directory to `src/triggers/{trigger-name}/`, then copy the verified
`IdempotencyStore` from [`../../idempotency-patterns.md`](../../idempotency-patterns.md)
into `src/triggers/_shared/idempotency.py`.

## Idempotency

Key = the sender's `X-Request-Id` header (`http-{id}`) — **required**; the
function returns **400** if it is missing. This makes the retry below safe.

## Dead-letter

On agent failure the payload is persisted to a Storage Queue poison store
(`DLQ_QUEUE_URL`) and the function returns **502** so the sender retries; the
idempotency gate makes the eventual success exactly-once. Reconcile/replay the
queue for requests that never succeed. **Alert on queue depth > 0.**

## Replay

Re-send with the same `X-Request-Id` to safely retry (deduped), or a new id to
force a fresh run.

## RBAC (function's UAMI)

Foundry project → `Azure AI User`; Cosmos → `Cosmos DB Built-in Data
Contributor`; Storage (DLQ) → `Storage Queue Data Message Sender`. Keyless only.

## Test locally

```bash
python3 local.test.py
func start                     # real Functions host (needs local.settings.json)
```
