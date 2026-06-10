# Pillar 3 — `identity-access`

> **What this pillar answers.** Do the workloads use managed identity?
> Are there any client secrets in source / Bicep / `azd env`? Is RBAC
> scoped to least privilege?

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `IAM-001` | `User-Assigned Managed Identity` declared in `infra/` for the agent / ACA workloads | `must-fix` if missing |
| `IAM-002` | No `client_secret`, `clientSecret`, `password=`, `pwd=`, or `BEGIN PRIVATE KEY` in repo (regex over tracked files, excluding fixtures and `.git`) | `must-fix` if found |
| `IAM-003` | No client-secret-style env vars in `azd env get-values` (looks for `*_SECRET`, `*_KEY` patterns and warns if non-empty and looks like a secret) | `should-fix` if found |
| `IAM-004` | Bicep role assignments use built-in roles, not custom roles built ad-hoc inside the pilot | `should-fix` if custom roles found |
| `IAM-005` | KV access pattern is **RBAC**, not legacy access-policies (`enableRbacAuthorization: true` in KV Bicep) | `must-fix` if access-policies used |

### Live (tier 1 — `Reader`)

| ID | Check | Default status |
|---|---|---|
| `IAM-101` | Each deployed ACA / container has an assigned identity (system or user) | `must-fix` if missing |
| `IAM-102` | Role assignments at subscription scope are NOT broader than necessary (warn if `Owner` or `Contributor` granted to a workload identity) | `must-fix` if `Owner`, `should-fix` if `Contributor` |
| `IAM-103` | No service principals with passwords (looks for SPs in tenant referenced from Bicep / azd env) | `must-fix` if found |

## Common gaps

- Workload uses MSAL with a client secret because "managed identity didn't
  work locally" and the secret was never removed for the deployed
  workload.
- Bicep grants `Contributor` to the workload identity at subscription
  scope (instead of `Foundry-Agent` + `KV Secrets User` + `Cosmos Data
  Contributor` at the specific resource scope).
- KV uses access policies (not RBAC) so the audit trail is partial.
- An `AZURE_CLIENT_SECRET` value sits in `.azure/<env>/.env` because of
  an older auth chain that's no longer needed.

## Remediation

| Finding | Skill |
|---|---|
| Migrate to managed identity | `foundry-hosted-agents` (Container/UAMI patterns) |
| Tighten role scope | `azure-tenant-isolation`, `azd-patterns` |
| Switch KV to RBAC | `azd-patterns` |
| Remove client secrets | (manual; document in the report) |

## Why this pillar matters

Client secrets in source = breach in waiting. Over-broad RBAC = blast
radius the size of the subscription. KV access-policies = no auditable
data-plane trail. None of these get caught by safe-check; all of them
will be caught by the customer's IAM review.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
