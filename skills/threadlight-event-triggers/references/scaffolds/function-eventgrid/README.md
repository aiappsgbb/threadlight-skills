# Function Event Grid receiver — Shape #7 (escape hatch)

Azure Functions **v2** (Python programming model: `function_app.py` +
decorators, **no** legacy `function.json`) on **Flex Consumption**, managed
identity only. Handles Event Grid events and invokes the Foundry-hosted agent
behind an idempotency gate.

> ⚠️ **Escape hatch.** Prefer an **ACA App (#3)** subscribed via an Event Grid
> → Service Bus / webhook path when you want the handler in your own container.
> Use an Event Grid *Function* only when a documented Functions justification
> applies (see SKILL § "When to choose Functions anyway").

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Thin v2 Event Grid trigger — builds the payload, calls the core. |
| `receiver_core.py` | Pure, unit-tested `handle()` + idempotency + agent invoke. |
| `host.json` | Functions host config (v2 extension bundle). |
| `requirements.txt` | Deps (Functions installs from this at deploy). |
| `local.test.py` | Offline smoke test of the core. |

Copy this directory to `src/triggers/{trigger-name}/`, then copy the verified
`IdempotencyStore` from [`../../idempotency-patterns.md`](../../idempotency-patterns.md)
into `src/triggers/_shared/idempotency.py`.

## Idempotency

Key = the Event Grid event `id` (`eg-{id}`) — **required**. Event Grid delivers
**at-least-once**, so the same `id` can arrive more than once; the gate makes
processing exactly-once.

## Dead-letter

On agent failure the core **re-raises** → Event Grid retries with backoff, then
delivers the event to the subscription's configured **dead-letter destination**
(a Storage blob container). Configure it on the event subscription. **Alert on
that container.**

## Replay

Re-publish (or replay from the dead-letter container) with the same event `id`
to safely retry (deduped).

## RBAC (function's UAMI)

Foundry project → `Azure AI User`; Cosmos → `Cosmos DB Built-in Data
Contributor`. The Event Grid subscription's dead-letter destination needs a
Storage blob container + the delivery identity granted **Storage Blob Data
Contributor**. Keyless only.

## Test locally

```bash
python3 local.test.py
func start                     # real Functions host (needs local.settings.json)
```
