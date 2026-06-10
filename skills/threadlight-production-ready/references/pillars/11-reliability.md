# Pillar 11 — `reliability`

> **v0.3.0:** Adds `REL-007` (restore-drill artefact freshness — fails
> if `tests/restore-drill-*.md` is older than 90 days, regardless of
> whether `REL-004` static evidence exists) and `REL-008` (live
> Recovery Services Vault must contain at least one restore point).
> Together these close the "configured backups, never tested them"
> gap that motivated the v0.3.0 overhaul.

> **What this pillar answers.** Does the deployment shape match the
> declared RTO/RPO? Is the backup actually tested (not just
> "configured")? Is there a runbook? Has any chaos test been run?

## Checks

### Static (tier 0)

| ID | Check | Default status |
|---|---|---|
| `REL-001` | SPEC § 12 declares `rto` and `rpo` targets | `must-fix` if absent |
| `REL-002` | Multi-region plan documented in SPEC § 12 if RTO < 4h | `must-fix` if RTO < 4h and `multi_region: none` |
| `REL-003` | Backup / restore runbook present (`docs/backup.md` or `docs/runbook.md` covers restore steps) | `must-fix` if absent |
| `REL-004` | Capacity host lifecycle understood (SPEC § 12 names the cap host / model-host owner + day-2 swap process) | `should-fix` if absent |
| `REL-005` | Failure modes catalogued in SPEC § 12 (top-3 likely outage modes with detection + mitigation) | `should-fix` if absent |
| `REL-006` | Health probes configured for ACA / Functions / Container Apps (Bicep declares liveness + readiness) | `should-fix` if absent |
| `REL-007` | Restore drill artefact present and dated within freshness window (default 90 days): `tests/restore-drill-*.md` or `docs/restore-drill.md` | `must-fix` |
| `MDL-008` | Knowledge index refresh cadence declared in SPEC § 12 (cross-listed here because index restore is part of the runbook surface — primary owner is pillar 13) | `should-fix` if absent |

### Live (tier 1 — `Reader`)

| ID | Check | Default status |
|---|---|---|
| `REL-008` | Live Recovery Services Vault contains at least one restore point (proves the configured backup is actually running) | `should-fix` if zero |
| `REL-101` | Zone redundancy enabled where supported (ACA, AI Search, Storage) | `should-fix` if absent — **experimental** |
| `REL-102` | Backup vault present if SPEC § 12 declares backups (Recovery Services Vault discoverable in the target RG / sub) | `must-fix` if declared & missing |
| `REL-103` | ACA `min-replica >= 1` in prod (cold-start avoidance) | `should-fix` if zero |
| `REL-104` | Multi-region resources present if SPEC § 12 declares `active-passive` or `active-active` | `should-fix` if drift — **experimental** |
| `REL-105` | Capacity host status healthy (Foundry cap host shows green provisioning state) | `should-fix` if degraded — **experimental** |

## RTO / RPO worked examples

| Customer target | Implications |
|---|---|
| RTO 1h, RPO 15m | Cosmos PITR + multi-region active-active OR active-passive with hot standby; backup region = paired region; restore drill within 30 days |
| RTO 4h, RPO 1h | Cosmos PITR + active-passive; restore drill within 90 days; runbook covers failover step |
| RTO 24h, RPO 24h | Single-region acceptable; daily backup tested quarterly; runbook covers restore-from-backup |
| "Best effort" | Skill warns: § 12 cannot say "best effort"; pick a target |

## Common gaps

- "Backup is on" because Cosmos has the default 8h backup. Customer
  RPO is 1h. Mismatch.
- Restore drill: never. The first restore happens during the first
  incident. Runtime extends 3x.
- Active-passive declared but the standby is in `westus2` and the data
  is GDPR-restricted to EU.
- ACA has no zone redundancy → one AZ outage takes the pilot down.
- Runbook is a Confluence page from 2023 that's wrong about resource
  names.

## Remediation

| Finding | Skill |
|---|---|
| Multi-region Foundry | `foundry-vnet-deploy` (with multi-region patterns) |
| Cap-host / Day-2 lifecycle | `foundry-caphost-lifecycle` |
| Runbook authoring | (manual; `azure-sre-agent` `threadlight-pilot-handover` recipe ships a template) |
| Restore drill | (manual; operationally led) |

## Why this pillar matters

A pilot's reliability promise lives in SPEC § 12 as targets, in
Bicep as configuration, and in `tests/restore-drill-*.md` as evidence.
A production pilot has all three. A "lab graveyard" pilot has the
targets and maybe the Bicep, but the evidence column is blank — and
the first incident proves it.
