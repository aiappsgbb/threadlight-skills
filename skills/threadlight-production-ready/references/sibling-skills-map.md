# Sibling-skill invocation map

When threadlight's apply-plan emits `kind: sibling-skill` for a finding, the
agent should consult this table to learn (a) which awesome-gbb skill to invoke,
(b) the input contract that skill expects, and (c) which threadlight finding
the invocation is expected to flip green.

## Currently mapped (v0.4.0)

| Finding ID | Sibling skill (awesome-gbb) | Input contract | Notes |
| --- | --- | --- | --- |
| NET-501 | `citadel-spoke-onboarding` | `spoke_subscription_id`, `spoke_resource_group`, `hub_subscription_id`, `hub_apim_resource_id`, `access_contract_product` | Hub coordinates may need user confirmation if not in framing. |
| NET-502 | `citadel-spoke-onboarding` | (same as NET-501) | Same skill handles reachability — single invocation closes both. |
| IAM-101 | `foundry-rbac-audit` *(planned — awesome-gbb#268)* | `subscription_id`, `resource_group`, `target_principal_types` | Skill not yet released; until then this finding stays `kind: manual`. |
| MDL-010 | `foundry-iq` | `subscription_id`, `resource_group`, `private_endpoint_required: true` | Existing skill — confirm version ≥ 0.3.0. |
| MDL-011 | `foundry-hosted-agents` | `subscription_id`, `resource_group`, `retention_days` (from framing) | Thread retention policy. |
| OBS-106 | `azure-resource-diagnostics` *(planned — awesome-gbb#271)* | `subscription_id`, `resource_group`, `target_resource_types` | Not yet released — fall back to `kind: manual`. |
| REL-007 | `azure-backup-readiness` *(planned — awesome-gbb#267)* | `subscription_id`, `resource_group`, `protected_item_types` | Not yet released — fall back to `kind: manual`. |
| SRE-104 | `azure-monitor-alert-baseline` *(planned — awesome-gbb#272)* | `subscription_id`, `resource_group`, `alert_baseline_kind` | Not yet released — fall back to `kind: manual`. |

## Planned but not implemented (placeholders — recipes are `kind: manual` until skill ships)

The four rows above marked *(planned)* reference awesome-gbb upstream issues
#267, #268, #271, #272. Until those skills ship, the corresponding recipes
MUST declare `kind: manual` and explain the manual procedure. When a sibling
skill ships:

1. Update its recipe's front-matter from `kind: manual` to `kind: sibling-skill`.
2. Add `sibling_skill: <skill-name>` to the front-matter.
3. Update this table to remove the *(planned)* tag.
4. Add a row to the v0.4.x changelog.

## Invocation contract for the agent

When the agent sees `kind: sibling-skill` in `apply-plan.json`:

1. Open the recipe markdown at `references/remediation-recipes/{ID}.md`.
2. Read the `## Edit recipe` section — it contains a JSON block with the
   exact inputs to pass.
3. Substitute placeholders that reference `framing.*` from the framing file
   that produced the apply-plan (path is recorded in `apply-plan.json` under
   `framing_path`).
4. Invoke the sibling skill via the Skill tool.
5. After the sibling skill completes, re-run threadlight (assessor-only,
   no `--scaffold-cicd`) to confirm the finding flipped to `pass`.
6. If still `fail`, do NOT loop — report the partial state to the operator.

## When a sibling skill is missing or fails

- **Skill not installed:** the agent surfaces a prompt to the operator asking
  whether to install it (links to its awesome-gbb tile) or fall back to the
  recipe's manual instructions.
- **Skill fails mid-run:** the agent marks the apply-plan item as
  `status: sibling-skill-failed`, records the error, and continues with the
  rest of the apply-plan. No automatic retry.
