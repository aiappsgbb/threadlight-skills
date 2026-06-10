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

### Static (tier 0)

| ID | Check | Default status |
|---|---|---|
| `SRE-001` | SPEC § 12 names `incident_owner` / on-call (email / team / group) | `must-fix` if absent |
| `SRE-002` | Runbook present in `docs/` (e.g., `docs/runbook.md`) | `must-fix` if absent |
| `SRE-003` | Azure SRE Agent integration considered in SPEC § 12 (adopted / explicitly deferred — not silently skipped) | `should-fix` if unmentioned |
| `SRE-004` | Severity matrix documented (`docs/severity.md` or in § 12: Sev1..Sev3 with response-time targets) | `should-fix` if absent |
| `SRE-005` | Postmortem template referenced (`docs/postmortem-template.md` or link in § 12) | `should-fix` if absent |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `SRE-101` | Action group routes to on-call rotation (at least one non-noreply destination — Teams channel, PagerDuty, real team alias) | `must-fix` if zero usable destinations |
| `SRE-102` | If § 12 declares SRE Agent: `Microsoft.App/agents` resource exists in the configured RG | `should-fix` if declared & missing |
| `SRE-103` | Diagnostic settings cover all critical resources (Foundry, KV, ACA, Cosmos → Log Analytics) | `must-fix` if any critical resource has none — **experimental** |
| `SRE-104` | Activity log alerts on the target RG (any alert at all — proves someone wired baseline platform-event detection) | `should-fix` if zero |
| `GOV-104` | Defender Secure Score above floor (default 60%; configurable via `--secure-score-floor` or SPEC § 12 `secure_score_floor`) | `should-fix` if below floor |
| `GOV-105` | Top 3 Defender recommendations enumerated in manifest (informational — surfaces the next 3 things to do) | `informational` |
| `GOV-201` | Required Azure Policy assignments present at sub/RG scope (v0.3.0: checks "any assignments exist"; v0.4.0 will check declared IDs) | `must-fix` if zero |
| `GOV-202` | No non-compliant resources for required policies (sampled via `az policy state list`) | `should-fix` if any |
| `GOV-203` | Sane-default initiatives assigned (ASB-v3 or customer equivalent) | `should-fix` if absent |

## The `threadlight-pilot-handover` recipe

`azure-sre-agent` ships a recipe specifically for this lifecycle moment:
SRE Agent picks up the pilot, runs daily health checks against the
pilot's resources, posts findings to the incident owner channel, and
maintains the runbook references.

If `target_posture` includes SRE Agent adoption, this pillar:
- Verifies the agent resource is provisioned (`SRE-102`)
- Verifies activity-log alerts exist on the target RG so the agent has
  baseline platform-event signal to react to (`SRE-104`)
- Surfaces Defender Secure Score, top recommendations, and required
  Policy posture as governance gates (`GOV-104..105`, `GOV-201..203`)

If SRE Agent **is not** part of the posture, this pillar is satisfied
with the runbook + alert-route + activity-log-alerts trio. SRE Agent is
optional but **strongly recommended** for any pilot heading to
production — the report surfaces this as a non-blocking callout.

> **Note** (v0.3.0): handoff-acceptance artefact (`docs/handoff-acceptance.md`)
> and dedicated `webhook-secret-in-KV` checks are planned for v0.4.0.
> The current evidence trio (alerts route to humans + RG activity-log
> alerts + Defender Secure Score above floor) covers the most-broken
> dimensions in customer pilots.

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
