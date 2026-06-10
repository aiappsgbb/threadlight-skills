# Pillar 1 — `network-posture`

> **v0.3.0:** Adds `POS-001` (declared `target_posture` must match
> detected evidence — flags pilots that claim citadel-spoke but show
> no APIM access contract) and wires `NET-501` as a live probe
> (Citadel APIM Access Contract in `TL_CITADEL_HUB_RG`).

> **What this pillar answers.** Is the network shape of this pilot
> aligned with the customer's declared production posture? And — as a
> sub-scored section — is **data residency** correct across model,
> gateway, data-plane, backups, and support access?

Posture targets:

| Target | What it means |
|---|---|
| `citadel-spoke` | Pilot is a spoke onboarded to a Citadel Governance Hub (APIM AI Gateway + Foundry control plane + access contracts) |
| `agt` | Pilot relies on in-process AGT middleware as the governance layer (no shared hub) |
| `standard-ai-gateway` | Pilot routes through a customer-owned APIM AI Gateway that isn't the Citadel reference implementation |
| `hybrid` | Some workloads go through Citadel, some go AGT-only (e.g., batch jobs) |

## Checks

### Static (no Azure auth required)

| ID | Check | Default status | Source |
|---|---|---|---|
| `NET-001` | SPEC § 12 declares `target_posture` | `must-fix` if missing | `specs/SPEC.md` |
| `NET-002` | SPEC § 11b `governance_hub` block consistent with declared target | `should-fix` if drift | `specs/SPEC.md` |
| `NET-003` | If `target_posture=citadel-spoke`, repo references the access-contract pattern (looks for `citadel`, `accessContract`, `apim` strings in infra/) | `should-fix` if no references | `infra/**/*.bicep`, `azure.yaml` |
| `NET-004` | If `target_posture=agt`, AGT middleware visible in `src/agent/` (looks for `agt`, `Agent Governance Toolkit`, AGT policy file) | `must-fix` if missing | `src/agent/` |

### Live (tier 1 — `Reader`)

| ID | Check | Default status |
|---|---|---|
| `NET-101` | Foundry account VNet integration status matches target (private vs public endpoint) | `must-fix` for `citadel-spoke`/`hybrid`/`vnet` if public |
| `NET-102` | No public-facing ACA without explicit allowlist (private ingress vs public + restrictions) | `should-fix` |
| `NET-103` | All RG resources tagged with `env`, `costCenter`, `dataClassification` (at minimum) | `should-fix` |

### Live (tier 5 — `Reader` on Citadel hub RG)

Only runs when `resolved.posture = citadel-spoke` or `hybrid`.

| ID | Check | Default status |
|---|---|---|
| `NET-501` | APIM Foundry connection exists in hub | `must-fix` if missing |
| `NET-502` | Access Contract is published (APIM product+subscription matches spoke) | `must-fix` if missing |
| `NET-503` | Spoke calls go through APIM (verified by checking spoke ACA outbound config + APIM logs sampling) | `should-fix` if uncertain → `not-verified` if APIM Reader missing |

## Sub-section: Data residency

Scored as its own sub-block; reflected in the report as a residency
table. Reads SPEC § 12 `residency` block as the source of truth.

| Field | What we check | Default status |
|---|---|---|
| `model_region` | Foundry account location matches `residency.model_region` | `must-fix` if drift |
| `gateway_region` | APIM (if applicable) location matches `residency.gateway_region` | `must-fix` if drift |
| `cosmos_region` | Cosmos account write region matches `residency.data_plane_region` | `must-fix` if drift |
| `search_region` | AI Search location matches `residency.data_plane_region` | `must-fix` if drift |
| `appin_region` | App Insights workspace location matches `residency.telemetry_region` | `should-fix` if drift |
| `backup_region` | Backups stay in `residency.backup_region` (or paired region of the data-plane region per `residency.allow_paired_region: yes`) | `must-fix` if drift |
| `cross_border_support` | If `residency.cross_border_support: no` — no support exfil paths configured (no Premier-style log shipping, etc.) | `should-fix` (manual review noted) |

> The residency sub-section is **always** scored, regardless of posture
> target. Even a `standard-ai-gateway` deployment in an EU-only customer
> can fail residency if the AppIn workspace landed in `eastus`.

## Common gaps

- SPEC declares `citadel-spoke` but the spoke was deployed without ever
  invoking `citadel-spoke-onboarding`. The Access Contract is missing.
- AGT is "declared" in § 12 but the middleware file lives only in
  `src/agent/skills/` (skill-level) and never wraps the agent at the
  container boundary. AGT-as-skill ≠ AGT-as-middleware.
- Customer is in EU but App Insights landed in `westus` because the
  Bicep used `location` from a `kv.bicep` default that wasn't updated.
- "Public endpoint" gets normalized to "private VNet" once Citadel
  onboards, but legacy outbound rules still allow `0.0.0.0/0` from the
  spoke (defense-in-depth gap).

## Remediation

Map findings to skills:

| Finding pattern | Skill |
|---|---|
| Access Contract / APIM product missing | `citadel-spoke-onboarding` |
| Need to deploy / shape the hub | `citadel-hub-deploy` |
| Need to inject Foundry into a VNet | `foundry-vnet-deploy` |
| Network connectivity / probe failures | `foundry-network-runbook` |
| Tagging convention | `azd-patterns` |

## Why this pillar matters

A pilot can ship green safe-check and still violate residency or
governance posture. The customer architecture review will catch it; the
customer's CISO will catch it. This pillar catches it first.
