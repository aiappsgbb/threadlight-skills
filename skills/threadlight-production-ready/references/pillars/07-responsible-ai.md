# Pillar 7 — `responsible-ai`

> **What this pillar answers.** Are content filters configured? Are
> jailbreak / prompt-injection shields in place? Is PII redaction
> declared? Is there a grounded-language eval that runs?

This pillar overlaps with `agent-governance` (AGT) but is scored
independently because RAI requirements apply even to pilots without
AGT (e.g., `target_posture: standard-ai-gateway`).

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `RAI-001` | Content filter declared for the model deployment (in Bicep or referenced from `infra/foundry/*.bicep`) — `low|medium|high` settings present | `must-fix` if absent or all `off` |
| `RAI-002` | If `agent-governance` profile != `none`: AGT policy artefact contains a `content_safety` or `rai_policy` block | `must-fix` if AGT enabled but no RAI block |
| `RAI-003` | Jailbreak / prompt-shield enabled in Foundry content filter (the `jailbreak` and `indirect_attack` filters) | `should-fix` if disabled |
| `RAI-004` | PII redaction strategy declared (`docs/pii-redaction.md`, AGT redaction block, or SPEC § 12 reference) | `should-fix` if absent |
| `RAI-005` | Grounded-language eval scenario present in `evals/` (one that scores `groundedness` or equivalent) | `should-fix` if absent |
| `RAI-006` | Allow-list / deny-list tested (block-list scenario in evals that proves the filter trips) | `should-fix` if absent |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `RAI-101` | Foundry content filter resource present and bound to the model deployment | `must-fix` if not bound |
| `RAI-102` | KQL `traces | where customDimensions.contains "content_filtered"` returns > 0 for the last 30 days (proves the filter is exercised, not just configured) | `should-fix` if zero |

## What good looks like

- Content filter settings ≥ `medium` for `hate`, `sexual`, `selfHarm`, `violence`.
- Jailbreak + indirect-attack shields ON.
- PII redaction either upstream of the model (AGT, gateway) or
  downstream in the response handler — documented as a policy.
- A "deny" eval scenario that submits an off-policy prompt and expects
  refusal.
- A grounded-language eval scenario that submits a question requiring
  retrieval and asserts the answer cites sources.

## Common gaps

- Content filter is at the model-deployment level but jailbreak shield
  was left `off`.
- PII redaction is "we trust the model" — i.e., not actually a control.
- No eval ever exercises the filter, so "configured" stays untested
  until a customer audit prompt finds the gap.
- AGT skipped → RAI policies don't apply → the only RAI defence is the
  Foundry-level filter.

## Remediation

| Finding | Skill |
|---|---|
| Configure / tighten content filter | `foundry-agt`, manual Foundry portal |
| Add jailbreak / prompt-shield | `foundry-agt` |
| Author PII redaction policy | `foundry-agt` |
| Author RAI evals | `foundry-evals` |

## Why this pillar matters

"Responsible AI" is the customer's RAI champion checking a box. They
will not ship a pilot they can't defend. This pillar gives them the
defensible answer in one screenshot: filter settings, shield status,
PII policy, eval evidence.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
