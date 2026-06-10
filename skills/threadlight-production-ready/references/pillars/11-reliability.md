# Pillar 11 — `reliability`

> **What this pillar answers.** Does the deployment shape match the
> declared RTO/RPO? Is the backup actually tested (not just
> "configured")? Is there a runbook? Has any chaos test been run?

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `REL-001` | SPEC § 12 declares `rto` and `rpo` targets | `must-fix` if absent |
| `REL-002` | SPEC § 12 declares `multi_region` plan (`none`, `active-passive`, `active-active`) consistent with RTO | `should-fix` if RTO < 1h and `multi_region: none` |
| `REL-003` | Backup plan exists for stateful resources (Cosmos, KV, AI Search index, Storage). For each: `backup_type` declared in Bicep or `docs/backup.md` | `should-fix` if absent for any stateful resource |
| `REL-004` | Restore drill evidence: `tests/restore-drill-*.md` or `docs/restore-drill.md` with date < freshness window (default 90 days) | `must-fix` if RTO < 24h and no drill |
| `REL-005` | Runbook exists (`docs/runbook.md` or `docs/operations.md`) covering: incident triage, common alerts, restore steps, rollback | `should-fix` if absent |
| `REL-006` | Chaos test evidence: any documented failure-injection test (Chaos Studio, manual ACA stop, etc.) | `should-fix` if absent for production-targeted pilot |

### Live (tier 1 — `Reader`)

| ID | Check | Default status |
|---|---|---|
| `REL-101` | Cosmos: continuous backup enabled if RPO < 24h | `must-fix` if RPO < 24h & PITR off |
| `REL-102` | Cosmos: backup region matches SPEC residency or is the paired region | `must-fix` if drift |
| `REL-103` | If `multi_region: active-passive`: secondary region resources exist (account-level replicas) | `must-fix` if absent |
| `REL-104` | KV: backups configured (export schedule OR replication) for production | `should-fix` if absent |
| `REL-105` | ACA: zone redundancy enabled if RTO < 1h | `should-fix` if absent |

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
