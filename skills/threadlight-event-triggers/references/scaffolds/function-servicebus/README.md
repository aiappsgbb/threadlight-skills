# Function Service Bus receiver — Shape #6 (escape hatch)

Azure Functions **v2** (Python programming model: `function_app.py` +
decorators, **no** legacy `function.json`) on **Flex Consumption**, managed
identity only. Drains a Service Bus queue and invokes the Foundry-hosted agent
behind an idempotency gate.

> ⚠️ **Escape hatch.** Prefer the **ACA Consumer (#4, KEDA)** for queue work —
> it scales to zero, keeps the pull loop in your own container, and avoids
> Functions cold-start/binding quirks. Use a Service Bus *Function* only when a
> documented Functions justification applies (see SKILL § "When to choose
> Functions anyway").

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Thin v2 Service Bus trigger — parses the message, calls the core. |
| `receiver_core.py` | Pure, unit-tested `handle()` + idempotency + agent invoke. |
| `host.json` | Functions host config (v2 extension bundle). |
| `requirements.txt` | Deps (Functions installs from this at deploy). |
| `local.test.py` | Offline smoke test of the core. |

Copy this directory to `src/triggers/{trigger-name}/`, then copy the verified
`IdempotencyStore` from [`../../idempotency-patterns.md`](../../idempotency-patterns.md)
into `src/triggers/_shared/idempotency.py`.

## Connection (managed identity, no keys)

Set the app setting `ServiceBusConnection__fullyQualifiedNamespace` =
`<namespace>.servicebus.windows.net` and grant the function's UAMI **Azure
Service Bus Data Receiver**. The `%SERVICEBUS_QUEUE%` binding reads the queue
name from app settings.

## Idempotency

Key = the broker `MessageId` (`sb-{id}`); content-hash fallback when a producer
omits it. Service Bus **at-least-once** delivery means duplicates are expected —
the gate makes processing exactly-once.

## Dead-letter

On agent failure the core **re-raises** → the host abandons the message → after
`maxDeliveryCount` Service Bus moves it to the queue's **native** dead-letter
sub-queue. No custom DLQ code. **Alert on DLQ depth > 0.**

## Replay

Re-submit from the DLQ sub-queue (same `MessageId` → safely deduped) after
fixing the cause.

## RBAC (function's UAMI)

Service Bus → `Azure Service Bus Data Receiver`; Foundry project → `Azure AI
User`; Cosmos → `Cosmos DB Built-in Data Contributor`. Keyless only.

## Test locally

```bash
python3 local.test.py
func start                     # real Functions host (needs local.settings.json)
```
