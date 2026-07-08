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
| `IAM-006` | If the repo declares an agent (non-human) identity, it is **passwordless** — a user-assigned managed identity or federated credential, not a client-secret app registration | `must-fix` if a standing secret binds the identity; `not-applicable` if no identity declared |
| `IAM-007` | Each declared agent identity names a **responsible owner** (`owner`/`ownerEmail`/`ManagedBy` tag or `agent-identity.governance.json`) | `should-fix` if unowned; `not-applicable` if no identity |
| `IAM-008` | Agent identities are scoped **least-privilege** — not Owner/Contributor/User Access Administrator, no wildcard `*.ReadWrite.All` Graph permission | `must-fix` if over-privileged; `not-applicable` if no identity |
| `IAM-009` | Each standing agent identity declares a **lifecycle** — a `reviewBy`/`expiresOn` signal or manifest review entry (federated identities pass automatically) | `should-fix` if none; `not-applicable` if no identity |

### Live (tier 1 — `Reader`)

| ID | Check | Default status |
|---|---|---|
| `IAM-101` | Each deployed ACA / container has an assigned identity (system or user) | `must-fix` if missing |
| `IAM-102` | Role assignments at subscription scope are NOT broader than necessary (warn if `Owner` or `Contributor` granted to a workload identity) | `must-fix` if `Owner`, `should-fix` if `Contributor` |
| `IAM-103` | No service principals with passwords (looks for SPs in tenant referenced from Bicep / azd env) | `must-fix` if found |

## Agent-identity binding — NHI governance (IAM-006 / IAM-007 / IAM-008 / IAM-009)

An agent is a **non-human identity (NHI)**: it authenticates, holds roles, and
acts on its own. IAM-001..003 catch secrets in source; this block governs the
identity the agent *is*. A sibling producer, `scripts/agent_identity.py`,
inventories every declared identity (UAMI / federated / app-secret) from compiled
ARM, Bicep, and source signals, then emits **`agent-identity.json`** alongside the
report — a portable record of how each agent authenticates, who owns it, how it is
scoped, and when it is reviewed. The four checks are static (tier 0, fully offline):

- **IAM-006 — passwordless binding (must-fix).** A client-secret app registration
  is a standing credential to store, rotate, and leak. Bind to a managed identity
  or a federated credential so no secret exists.
- **IAM-007 — responsible owner (should-fix).** An unowned NHI is orphaned the day
  its author moves teams. Tag an owner or record one in the governance manifest.
- **IAM-008 — least-privilege scope (must-fix).** A workload identity holding
  Owner / Contributor / User Access Administrator, or a wildcard `*.ReadWrite.All`
  Graph permission, has a blast radius far larger than its job. Grant only scoped
  data-plane roles.
- **IAM-009 — lifecycle (should-fix).** A standing identity with no review date
  lives forever. Declare a `reviewBy`/`expiresOn` signal, or federate to remove the
  standing secret entirely.

Optionally declare `agent-identity.governance.json` at the repo root to supplement
owner / review metadata per subject id — the analog of `mcp-lock.json` for the
supply-chain gate. This **amplifies** the platform: remediation points at
`entra-agent-id`, `foundry-agt`, `azure-rbac`, and Entra access reviews / PIM — it
never replaces them.

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
| Bind a passwordless agent identity (IAM-006) | `entra-agent-id`, `foundry-agt` |
| Scope agent identity least-privilege (IAM-008) | `azure-rbac`, `foundry-agt` |
| Attach owner + lifecycle review (IAM-007 / IAM-009) | `entra-agent-id` (access reviews / PIM) |

## Why this pillar matters

Client secrets in source = breach in waiting. Over-broad RBAC = blast
radius the size of the subscription. KV access-policies = no auditable
data-plane trail. None of these get caught by safe-check; all of them
will be caught by the customer's IAM review.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
