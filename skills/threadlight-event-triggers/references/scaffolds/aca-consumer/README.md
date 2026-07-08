# ACA Consumer (KEDA) receiver — Shape #4

A Container App with **no ingress** that pulls from **Service Bus** and invokes
the Foundry-hosted agent per message. KEDA scales it **0 → 30** on queue depth,
so you pay nothing between events. Use for high-throughput event streams.

## Files

| File | Purpose |
|------|---------|
| `receiver.py` | Pull loop + pure `handle()` core + lazy-wired Azure `main()`. |
| `receiver.bicep` | `Microsoft.App/containerApps` + `azure-servicebus` KEDA scaler. |
| `pyproject.toml` | uv-managed deps. |
| `Dockerfile` | Container build (ACR remote build). |
| `local.test.py` | Offline smoke test with synthetic input. |

Copy this directory to `src/triggers/{trigger-name}/`, then copy the verified
`IdempotencyStore` from [`../../idempotency-patterns.md`](../../idempotency-patterns.md)
into `src/triggers/_shared/idempotency.py`.

## Idempotency

Key = the Service Bus `MessageId` (`sb-{id}`), so a redelivered message is a
no-op. Default dedup window is 5m (streaming sources retry fast). Backed by the
Cosmos `trigger_idempotency` container.

## Dead-letter

Native **Service Bus DLQ**: on agent failure the message is dead-lettered
(`dead_letter_message`, reason `agent-failure`) and **not** completed; on
success/skip it is completed. Set `maxDeliveryCount` on the queue and **alert on
DLQ depth > 0**.

## Other sources (Event Grid / Event Hubs / Cosmos change feed / Kafka)

This scaffold ships the **Service Bus** example. To use another source:

1. Swap the dependency in `pyproject.toml` (e.g. `azure-eventhub`).
2. Replace the pull loop in `receiver.py:main()` with that client's consumer.
3. Change the KEDA `custom.type` + `metadata` in `receiver.bicep` (e.g.
   `azure-eventhub`, `azure-servicebus` topic, `azure-cosmosdb`).

The `aca-app-http` variant (Shape #3, HTTP webhook with `minReplicas: 1`) is
this scaffold **with an `ingress` block added and the KEDA rule replaced by an
`http` scale rule** — see SKILL § Step 6 Shape #3 for the exact ingress + scale
block.

## RBAC (receiver UAMI)

Foundry project → `Azure AI User`; Service Bus → `Azure Service Bus Data
Receiver`; Cosmos → `Cosmos DB Built-in Data Contributor`. Keyless only —
KEDA needs the UAMI **resource id** (not client id) in the scaler `identity`.

## Test locally

```bash
python3 local.test.py
uv run python receiver.py    # real Azure (needs SERVICEBUS_* + PROJECT_* env)
```
