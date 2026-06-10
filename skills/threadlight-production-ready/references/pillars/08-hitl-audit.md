# Pillar 8 — `hitl-audit`

> **What this pillar answers.** If SPEC § 8 declares human-in-the-loop
> gates: are they wired, persistent (audit trail), reachable
> (escalation channel), and idempotent (so a retry doesn't double-fire
> the action)?

This pillar **only applies when SPEC § 8 declares HITL gates**. For
read-only / suggestion-only agents it is `not-applicable`.

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `HITL-001` | SPEC § 8 lists action gates and identifies channel (Teams, Slack, custom) | `not-applicable` if § 8 empty |
| `HITL-002` | Adaptive Card / channel template present (`src/bot/cards/`, `src/agent/cards/`, or referenced from `threadlight-hitl-patterns` template) | `must-fix` if HITL declared |
| `HITL-003` | Audit-trail storage declared (Cosmos collection / table / KV / dedicated AppIn custom event) | `must-fix` if HITL declared |
| `HITL-004` | Idempotency key pattern visible in the gate handler (correlation ID / request ID stored before action) | `should-fix` if absent |
| `HITL-005` | Escalation contact declared (fallback if approver unavailable) | `should-fix` if absent |

### Live (tier 1)

| ID | Check | Default status |
|---|---|---|
| `HITL-101` | Audit-trail storage resource exists (Cosmos container / table / KV / dedicated AppIn workspace named per declaration) | `must-fix` if missing |
| `HITL-102` | If channel = Teams: Bot Service / Teams app registration present in RG | `must-fix` if Teams declared |
| `HITL-103` | KQL `customEvents | where name == "HITL.approval"` (or similar declared name) returns > 0 if pilot has been exercised | `should-fix` if zero with hint |

## Common gaps

- HITL is "designed" in § 8 but no actual approval card is sent — the
  agent fires the action regardless because the gate-handler defaults
  to "auto-approve" when no channel is set.
- Audit trail is a `print()` to AppIn console traces, not a structured
  custom event. Auditor can't query it.
- Approver clicks Approve, network blips, retry, action fires twice.
  No idempotency key.
- The named approver is on holiday; no escalation route declared.

## Remediation

| Finding | Skill |
|---|---|
| Wire Teams approval gate | `threadlight-hitl-patterns`, `foundry-teams-bot` |
| Author audit-trail schema | `threadlight-hitl-patterns` |
| Add idempotency keys | `threadlight-hitl-patterns` |

## Why this pillar matters

A pilot with HITL declared in § 8 but not wired is the worst kind: it
looks "responsibly governed" in the deck and isn't. A double-firing
HITL is the worst kind 2.0: the audit log shows "approved" and then
two actions and no one knows which "Approve" click did what.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
