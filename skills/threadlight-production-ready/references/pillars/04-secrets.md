# Pillar 4 — `secrets`

> **v0.3.0:** Wires `SEC-106` as a live probe (KV diagnostic settings
> shipping to a Log Analytics workspace — control plane, not data
> plane) and adds `GOV-102` (Defender for Key Vault plan enabled on
> the subscription, configurable via SPEC § 12 `defender_plans_required`).

> **What this pillar answers.** Is Key Vault configured for production
> (soft-delete, purge protection, RBAC)? Are there hardcoded secrets in
> the repo? Is there a rotation policy declared?

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `SEC-001` | KV resource declared in `infra/` with `enableSoftDelete: true` | `must-fix` if false / missing |
| `SEC-002` | KV declared with `enablePurgeProtection: true` | `must-fix` if false / missing |
| `SEC-003` | KV declared with `enableRbacAuthorization: true` | `must-fix` if access-policies used |
| `SEC-004` | KV `softDeleteRetentionInDays >= 90` (production minimum) | `should-fix` if < 90 |
| `SEC-005` | No hardcoded secret-looking strings in repo (regex over tracked files: AWS keys, Azure connection strings, PEM, etc.) | `must-fix` if found |
| `SEC-006` | Rotation policy declared somewhere (KV rotation policy resource, or `docs/secrets-rotation.md`, or SPEC § 12 references it) | `should-fix` if absent |
| `SEC-007` | Container images pull credentials via managed identity (no `imagePullSecret` referencing a KV secret value) | `should-fix` if found |

### Live (tier 4 — `Key Vault Reader` control plane)

> **The skill NEVER reads secret values, even with sufficient permission.**

| ID | Check | Default status |
|---|---|---|
| `SEC-101` | Deployed KV has `enableSoftDelete: true` | `must-fix` if false |
| `SEC-102` | Deployed KV has `enablePurgeProtection: true` | `must-fix` if false |
| `SEC-103` | Deployed KV uses RBAC (`enableRbacAuthorization: true`) | `must-fix` if false |
| `SEC-104` | KV diagnostic settings present (data-plane audit to Log Analytics or Storage) | `should-fix` if absent |
| `SEC-105` | No data-plane permissions granted via legacy access policies (count == 0) | `should-fix` if > 0 |
| `SEC-106` | KV firewall configured (not "all networks"); private endpoint if `target_posture` ∈ `{citadel-spoke, hybrid, vnet}` | `should-fix` if "all networks"; `must-fix` if private-network posture & public |

## Common gaps

- KV exists, but `enablePurgeProtection: false`. A misconfigured pipeline
  permanently deletes a vault and the secret with it. Production = on.
- Soft-delete retention left at 7 days (default for some templates).
- Connection strings or PATs committed in `azd env` or sample data.
- "Rotation policy" exists in someone's head, not in the repo.
- KV diagnostic settings off → data-plane access has no audit trail.

## Remediation

| Finding | Skill |
|---|---|
| KV config patterns | `azd-patterns` |
| Use managed identity for secret retrieval | `foundry-hosted-agents` |
| Rotate hardcoded secrets out of repo | (manual; document in the report) |

## Why this pillar matters

Production = "soft-delete on, purge protection on, RBAC, diag on,
rotation declared, no hardcoded secrets". Any one of those missing is a
finding the customer's risk team will block on. KV existence alone is
not the same as KV configured.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
