# Live-probe permissions — tier-by-check map

> **What this is.** The minimum Azure RBAC role required for each live
> probe in the skill. The skill is **graceful** — missing the role
> means the check is marked `not-verified` and the report tells the
> operator what role would unlock the check.

## Permission tier model

The skill uses 5 tiers, ascending in privilege. The operator can hold
any subset; checks that need tiers the operator doesn't have are
skipped (not failed).

| Tier | Role | Scope | What it unlocks |
|---|---|---|---|
| 1 | `Reader` | Subscription or RG containing the pilot | Resource inventory, types, names, tags, network config, role assignments, App Insights existence, container app config, model deployments |
| 2 | `Monitoring Reader` + `Log Analytics Reader` | Subscription or RG | KQL queries for trace freshness, alert rule presence, workbook count, eval-run output queries |
| 3 | `Cost Management Reader` | Subscription | Budget presence, anomaly alerts, PAYG/PTU split inspection, idle-resource detection |
| 4 | `Key Vault Reader` (**control plane**) | KV resource(s) | Vault config — soft-delete, purge protection, RBAC vs access-policies, diagnostic settings, firewall. **Never reads secret values.** |
| 5 | `Reader` on Citadel hub RG + `API Management Service Reader` on hub APIM | Hub RG and APIM | APIM Access Contract presence, Foundry connection status, hub-spoke wiring |

## How to grant the minimum set

For a typical spoke pilot:

```bash
# Tier 1 — required for everything
az role assignment create \
  --assignee <user-or-sp-id> \
  --role "Reader" \
  --scope "/subscriptions/<sub>/resourceGroups/<spoke-rg>"

# Tier 2 — for observability + eval checks
az role assignment create \
  --assignee <user-or-sp-id> \
  --role "Monitoring Reader" \
  --scope "/subscriptions/<sub>/resourceGroups/<spoke-rg>"

az role assignment create \
  --assignee <user-or-sp-id> \
  --role "Log Analytics Reader" \
  --scope "/subscriptions/<sub>/resourceGroups/<spoke-rg>"

# Tier 3 — for cost pillar
az role assignment create \
  --assignee <user-or-sp-id> \
  --role "Cost Management Reader" \
  --scope "/subscriptions/<sub>"

# Tier 4 — for secrets pillar
az role assignment create \
  --assignee <user-or-sp-id> \
  --role "Key Vault Reader" \
  --scope "/subscriptions/<sub>/resourceGroups/<spoke-rg>/providers/Microsoft.KeyVault/vaults/<vault>"

# Tier 5 — for Citadel-spoke posture only
az role assignment create \
  --assignee <user-or-sp-id> \
  --role "Reader" \
  --scope "/subscriptions/<hub-sub>/resourceGroups/<hub-rg>"

az role assignment create \
  --assignee <user-or-sp-id> \
  --role "API Management Service Reader Role" \
  --scope "/subscriptions/<hub-sub>/resourceGroups/<hub-rg>/providers/Microsoft.ApiManagement/service/<hub-apim>"
```

> **Tier 5 caveat.** "API Management Service Reader" may not be a built-in
> role in every tenant; in that case use `Reader` on the APIM resource
> as the next-best, and document the actual role name used. The skill
> identifies the probe failure cleanly either way and surfaces a
> `not-verified` hint.

## Per-check tier map

This table is the authoritative source for what tier each finding ID
needs. The skill carries this internally so it never asks for more
permission than necessary.

| Pillar | Check ID | Tier | Notes |
|---|---|---|---|
| `network-posture` | `NET-101` | 1 | Foundry VNet status |
| `network-posture` | `NET-102` | 1 | ACA ingress |
| `network-posture` | `NET-103` | 1 | Tagging |
| `network-posture` | `NET-501` | 5 | Hub APIM Foundry connection |
| `network-posture` | `NET-502` | 5 | Hub APIM Access Contract |
| `network-posture` | `NET-503` | 5 | Hub APIM call routing |
| `agent-governance` | `AGT-101` | 1 | AGT sidecar in deployed ACA |
| `agent-governance` | `AGT-102` | 2 | AGT span in AppIn traces |
| `identity-access` | `IAM-101` | 1 | Workload identity assignment |
| `identity-access` | `IAM-102` | 1 | RBAC scope analysis |
| `identity-access` | `IAM-103` | 1 | Service principals with passwords |
| `secrets` | `SEC-101..106` | 4 | KV control-plane config |
| `observability` | `OBS-101` | 1 | Foundry account → AppIn connection |
| `observability` | `OBS-102..103` | 2 | KQL trace queries |
| `observability` | `OBS-104..106` | 1 | Alert rule / workbook count |
| `continuous-evals` | `EVAL-101..103` | 2 | CE resource / KQL |
| `continuous-evals` | `EVAL-104` | 1 | Alert rule presence |
| `continuous-evals` | `EVAL-105` | 2 | Latest run pass rate |
| `responsible-ai` | `RAI-101` | 1 | Content filter binding |
| `responsible-ai` | `RAI-102` | 2 | KQL content-filtered traces |
| `hitl-audit` | `HITL-101..102` | 1 | Audit-trail store presence |
| `hitl-audit` | `HITL-103` | 2 | KQL HITL custom events |
| `supply-chain` | `SUP-101..103` | 1 | ACA image digests / ACR config |
| `cost` | `COST-101..105` | 3 | Budget / anomaly / forecast |
| `reliability` | `REL-101..105` | 1 | Cosmos / KV / ACA config |
| `sre-handover` | `SRE-101..104` | 1 | Alert action groups, SRE Agent resource |
| `model-lifecycle` | `MDL-101..103` | 1 | Model deployment inventory |
| `model-lifecycle` | `MDL-104` | 2 | KQL model-call traces |
| `model-lifecycle` | `MDL-110` | 3 | TPM headroom — needs `Cognitive Services Usages Reader` |
| `model-lifecycle` | `MDL-111` | 1 | Foundry account capacity in target region |
| `model-lifecycle` | `GOV-101` | 1 | Defender for AI Services plan enabled |
| `secrets` | `SEC-106` | 1 | KV diagnostic settings → LA (control-plane) |
| `secrets` | `GOV-102` | 1 | Defender for Key Vault plan enabled |
| `observability` | `OBS-106` | 1 | Foundry account diagnostic settings → LA |
| `observability` | `OBS-102` | 2 | KQL trace-freshness probe (24h window) |
| `supply-chain` | `GOV-103` | 1 | Defender for Servers / Containers plan enabled |
| `reliability` | `REL-007` | 0 | Restore-drill artefact freshness (static, no Azure) |
| `reliability` | `REL-008` | 1 | Live Recovery Services Vault restore-point sampling |
| `sre-handover` | `SRE-104` | 2 | RG activity-log alerts presence |
| `sre-handover` | `GOV-104` | 1 | Defender Secure Score floor |
| `sre-handover` | `GOV-201..203` | 1 | Azure Policy assignments + compliance |
| `network-posture` | `POS-001` | 1 | Declared posture matches detected evidence |
| `network-posture` | `NET-501` | 5 | Citadel APIM Access Contract — needs `TL_CITADEL_HUB_RG` env |

## What the skill does when a tier is missing

For each check the skill tries to run, the failure is captured and
catalogued in the report's `not_verified[]` section:

```json
{
  "id": "NV-001",
  "pillar": "cost",
  "check_id": "COST-101",
  "reason": "Cost Management Reader role missing on subscription",
  "permission_tier_required": 3,
  "permission_role_required": "Cost Management Reader",
  "remediation_hint": "Grant 'Cost Management Reader' at the subscription scope and re-run with --pillar cost"
}
```

The summary at the end of the markdown report includes:

> **What you missed:** 6 checks across 2 pillars were skipped due to
> missing permissions. Grant the roles listed in
> `not_verified[].permission_role_required` and re-run for full coverage.

## Why never read secret values

Even with Key Vault `Reader` or `Secrets User` data-plane permission,
the skill **never reads secret values**. This is a hard rule:

1. The skill produces an artefact (the JSON manifest + the markdown
   report) that lands in `docs/` and ends up committed to the repo.
   A read of a secret value risks landing it in git.
2. The skill is read-only for **control-plane only**. Vault config is
   the only thing it asks about.
3. The skill prints `[ctx]` at the top with subscription/tenant ID so
   the operator can audit what the skill saw — but secret values are
   never in scope.

If a check would require a secret value to succeed (e.g., "does this
connection string actually connect?"), the skill instead checks that
the secret reference exists in the workload config and marks the
liveness check `not-verified` with a hint to test out-of-band.
