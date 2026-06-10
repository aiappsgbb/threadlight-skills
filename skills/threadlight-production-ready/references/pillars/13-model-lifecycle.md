# Pillar 13 — `model-lifecycle`

> **What this pillar answers.** Are model deployment names and versions
> **pinned** (no `latest`)? Is there a fallback model? Is there a
> retirement-notice owner? Is there an A/B or rollback strategy? Are
> region/capacity constraints documented?

Models retire. Capacity caps trip. Content-safety policies drift.
Production must plan for all three.

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `MDL-001` | SPEC § 12 lists the model deployments used (per agent/skill, name + version + region) | `must-fix` if absent |
| `MDL-002` | `infra/` Bicep model-deployment resources declare a specific `version` (not `latest`) | `must-fix` if `latest` found |
| `MDL-003` | `infra/` Bicep model-deployment resources declare a specific deployment `name` (not auto-generated each deploy) | `should-fix` if name churns |
| `MDL-004` | SPEC § 12 declares a fallback model deployment (different model or different region) for the primary | `should-fix` if absent |
| `MDL-005` | SPEC § 12 declares a `retirement_notice_owner` (who watches Microsoft's deprecation announcements) | `should-fix` if absent |
| `MDL-006` | A/B or rollback strategy documented (`docs/model-rollback.md` or referenced from § 12: e.g., dual deployment + traffic switch) | `should-fix` if absent |
| `MDL-007` | Region/capacity constraints documented (`docs/model-capacity.md` or in § 12 — e.g., "GPT-4o capacity in westeurope: 50K TPM committed") | `should-fix` if absent |
| `MDL-008` | Container/agent code references the model **by deployment name**, not by model name string (so a pin swap is a Bicep change, not a code change) | `should-fix` if model name hardcoded in `container.py` |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `MDL-101` | Deployed model deployments match SPEC § 12 declarations (count + names + versions) | `must-fix` if drift |
| `MDL-102` | No deployed model is on a `latest` version pin | `must-fix` if found |
| `MDL-103` | Fallback model (if declared) is actually deployed (not just documented) | `must-fix` if missing |
| `MDL-104` | KQL `traces | where customDimensions.modelName == "<declared>"` returns > 0 (proves the pinned model is the one being called) | `should-fix` if zero |

## Common gaps

- Bicep uses `properties.model.version: 'latest'` and Microsoft retires
  the version → pilot quietly switches to a newer version with
  different behaviour.
- Deployment name is `gpt-4o-{uniqueString(...)}` so every redeploy
  changes the name and the agent code needs an update or a env-var.
- No fallback declared → if capacity trips, pilot is down.
- No retirement-notice owner → Microsoft announces deprecation in the
  Foundry portal, nobody reads it for 6 months.
- No A/B strategy → swapping models in production = downtime.
- Agent code: `azure_openai.ChatCompletions.create(model="gpt-4o")` —
  hardcoded model name, can't swap without a code release.

## Remediation

| Finding | Skill |
|---|---|
| Pin model versions in Bicep | `azd-patterns`, manual Bicep edit |
| Add fallback deployment | `foundry-hosted-agents` |
| Choose PAYG vs PTU + plan capacity | `paygo-ptu-cost-analyzer` |
| Document rollback strategy | (manual; ties to pillar 11 runbook) |
| Refactor agent to call deployment-name, not model-name | `foundry-hosted-agents` |

## Why this pillar matters

The pilot launched with a model that just shipped. 18 months later
that model is in deprecation notice; the team forgot which versions
they pinned; the agent quietly upgrades to a new version mid-quarter
and starts answering customer prompts differently. The post-mortem is
"why didn't we know?". Pillar 13 is the answer.
