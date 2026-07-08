# Trigger receiver scaffolds — shipped index

Each subdirectory is a polished, copy-paste-ready scaffold for one receiver
shape. SKILL.md inlines the essential code shape and the deploy steps; copy the
directory that matches your trigger, then follow that scaffold's `README.md`.

Every scaffold shares one **pure, unit-tested core** — an idempotent
`handle(payload, *, store, invoke, dead_letter)` that derives a dedup key, skips
duplicates, invokes the Foundry-hosted agent, and dead-letters on failure
(without marking the item processed, so it stays retryable). The pieces around
that core (trigger wiring, dead-letter destination, hosting) differ per shape.

| Scaffold | When to use | Status |
|----------|-------------|--------|
| `aca-job-cron/` | Periodic batch (nightly KPI rollup, hourly reconciliation, SLA watcher) | ✅ Shipped |
| `aca-job-manual/` | On-demand / manual-start batch (workflow step, replay a range) | ✅ Shipped |
| `aca-consumer/` | High-throughput Service Bus consumer with a KEDA scaler (scale-to-zero) | ✅ Shipped |
| `function-http/` | Lightweight HTTP webhook receiver — **escape hatch** | ✅ Shipped |
| `function-servicebus/` | Service Bus queue consumer on Functions — **escape hatch** | ✅ Shipped |
| `function-eventgrid/` | Event Grid event handler on Functions — **escape hatch** | ✅ Shipped |

> **Prefer Container Apps.** The three `aca-*` shapes cover the vast majority of
> triggers and keep the handler in your own container. The `function-*` shapes
> are documented **escape hatches** — reach for them only when a Functions
> binding/justification genuinely fits (see SKILL § "When to choose Functions
> anyway").

## What each scaffold ships

**Container Apps shapes** (`aca-job-cron`, `aca-job-manual`, `aca-consumer`):

- `receiver.py` — pure idempotent core + lazy Azure wiring + `main()` entry point
- `pyproject.toml` — uv-managed deps
- `Dockerfile` — container image for ACA
- `receiver.bicep` — the ACA Job / App resource (managed identity, keyless)
- `local.test.py` — offline smoke test (no Azure needed)
- `README.md` — idempotency key, dead-letter wiring, replay, RBAC

**Functions shapes** (`function-http`, `function-servicebus`, `function-eventgrid`)
use the **v2 Python programming model** (`function_app.py` + decorators — **no**
legacy `function.json`) on Flex Consumption:

- `function_app.py` — thin v2 trigger that wires the binding to the core
- `receiver_core.py` — the pure, unit-tested idempotent core (no `azure.functions`
  import, so it is testable under a stdlib-only CI)
- `host.json` — Functions host config (v2 extension bundle)
- `requirements.txt` — deps (Functions installs from this at deploy)
- `local.test.py` — offline smoke test of the core
- `README.md` — idempotency key, dead-letter wiring, replay, RBAC

## Idempotency + dead-letter (per shape)

Every core copies the verified `IdempotencyStore` from
[`../idempotency-patterns.md`](../idempotency-patterns.md) into
`src/triggers/_shared/idempotency.py` at deploy time. Dead-letter strategy
differs by shape:

| Shape | Dead-letter on agent failure |
|-------|------------------------------|
| `aca-job-cron`, `aca-job-manual` | Persist the item to a Storage Queue poison store (`DLQ_QUEUE_URL`) |
| `aca-consumer` | Service-Bus-native `dead_letter_message()` on the message |
| `function-http` | Persist to a Storage Queue + return **502** so the sender retries |
| `function-servicebus` | Re-raise → host abandons → **native** SB dead-letter sub-queue |
| `function-eventgrid` | Re-raise → Event Grid retries → subscription **dead-letter destination** |

Alert on the poison store / dead-letter depth in every case.

## Test locally

```bash
python3 local.test.py          # any scaffold — offline, no Azure
# and the shared behaviour suite for all six cores:
python3 -m pytest ../../tests/ -v
```
