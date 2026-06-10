# Pillar 12 — `sre-handover`

> **v0.3.0:** Wires `SRE-104` as a live probe (resource-group
> activity-log alerts present), and adds the governance gates
> `GOV-104` (Defender Secure Score floor, default 60%, configurable
> via `--secure-score-floor`), `GOV-201` (required Azure Policy
> assignments present at sub/RG scope), `GOV-202` (no non-compliant
> resources for required policies), `GOV-203` (sane-default initiative
> assigned — ASB v3 or customer equivalent). All Policy/Defender
> requirements come from SPEC § 12 `required_policy_ids` and
> `defender_plans_required`.

> **What this pillar answers.** When this pilot goes to production at
> 09:00 Monday, who is on the pager at 03:00 Tuesday? Where do they
> click? What runbook do they follow? Has the SRE team accepted the
> handoff in writing?

This pillar is **evidence-based**: presence of an SRE Agent resource
or a `threadlight-pilot-handover` recipe is good, but **not sufficient**.
The pillar requires the artefacts that make the handover real.

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `SRE-001` | SPEC § 12 declares `incident_owner` (email / team / group) | `must-fix` if absent |
| `SRE-002` | SPEC § 12 declares `escalation_path` (L1 → L2 → L3 with response-time targets) | `should-fix` if absent |
| `SRE-003` | `docs/runbook.md` (or equivalent) exists and is referenced from § 12 | `should-fix` if absent (also caught by pillar 11) |
| `SRE-004` | Handoff acceptance artefact: `docs/handoff-acceptance.md` (or equivalent) signed by the incident owner with date | `must-fix` if absent for go-live |
| `SRE-005` | If pilot uses Azure SRE Agent: `azure-sre-agent` recipe applied — look for `azuresre/` or `sre-agent/` config in repo or `infra/` Bicep declaring `Microsoft.App/agents` resource | `should-fix` if SRE Agent declared in § 12 but no recipe |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `SRE-101` | Alert rules (from pillar 5) have at least one **destination** that isn't email-to-noreply (action group with an actual team / pager / Teams channel) | `must-fix` if zero usable destinations |
| `SRE-102` | If § 12 declares SRE Agent: `Microsoft.App/agents` resource exists in the configured RG | `must-fix` if declared & missing |
| `SRE-103` | If SRE Agent + `threadlight-pilot-handover` recipe: at least one daily health task scheduled (HTTP trigger or recipe-driven) | `should-fix` if absent |
| `SRE-104` | HTTP trigger / webhook secret (if any) stored in KV, not env var | `must-fix` if env-var |

## The `threadlight-pilot-handover` recipe

`azure-sre-agent` ships a recipe specifically for this lifecycle moment:
SRE Agent picks up the pilot, runs daily health checks against the
pilot's resources, posts findings to the incident owner channel, and
maintains the runbook references.

If `target_posture` includes SRE Agent adoption, this pillar:
- Verifies the agent resource is provisioned
- Verifies the `threadlight-pilot-handover` recipe (or equivalent
  subagent/plugin chain) is applied
- Verifies daily health task scheduling
- Notes if the HTTP trigger secret pattern is in KV (not env-var)

If SRE Agent **is not** part of the posture, this pillar is satisfied
with the runbook + acceptance + alert-route trio. SRE Agent is
optional but **strongly recommended** for any pilot heading to
production — the report surfaces this as a non-blocking callout.

## Handoff acceptance artefact

The skill expects a minimal `docs/handoff-acceptance.md` with:

```markdown
# Pilot Handoff Acceptance

**Pilot:** {name from SPEC}
**Production RG:** {resource group}
**Incident owner:** {email}
**Escalation path:** {L1} → {L2} → {L3}
**Runbook:** {link}
**Alert destinations:** {action group / Teams channel}
**Date accepted:** {ISO date}
**Signed:** {name}

## Scope of operation

- {what SRE team monitors}
- {what is left to the product team}

## Out of scope

- {explicitly excluded; e.g., model fine-tune ops}
```

The skill **doesn't enforce a specific schema** — it looks for
presence + a recent date. The shape above is the recommended template.

## Common gaps

- "Incident owner" is the product manager, who isn't on a pager rota.
- Alerts route to an email distribution list that's been disabled for
  18 months.
- SRE Agent is "set up" but no `threadlight-pilot-handover` recipe → it's
  a generic SRE Agent unaware of pilot specifics.
- HTTP trigger secret is in `azd env` as a plain env var.
- Runbook exists but no one has read it. (The skill can't check this
  — but it can check for presence + last-edit date.)
- Handoff was "verbal in a meeting". No artefact.

## Remediation

| Finding | Skill |
|---|---|
| Provision SRE Agent | `azure-sre-agent` |
| Apply `threadlight-pilot-handover` recipe | `azure-sre-agent` |
| Wire alert action groups | `foundry-observability` |
| Author runbook | (manual; recipe ships template) |
| Get handoff acceptance | (operational; document in report) |

## Why this pillar matters

Production failures don't wait for business hours. The pilot that
ships without a signed handoff is the pilot whose first incident
becomes "who do we call?" — and the answer is "the pilot's author",
which is the wrong answer.
