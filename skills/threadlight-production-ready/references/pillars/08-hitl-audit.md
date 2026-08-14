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
| `HITL-001` | SPEC § 8 lists action gates and identifies channel (Teams, Slack, custom) | `should-fix` if § 8 absent |
| `HITL-002` | HITL gate implementation referenced in `src/` | `must-fix` if § 8 declares gates |
| `HITL-003` | Audit-trail storage declared in infra (Storage / SQL / Cosmos) | `must-fix` if absent |
| `HITL-004` | Escalation channel referenced (Teams, webhook, email) | `should-fix` if absent |
| `HITL-005` | HITL decision SLA documented | `should-fix` if absent |
| `HITL-006` | Every skill contract declares a **substantive** idempotency statement | `must-fix` if a contract declares none |
| `HITL-007` | SPEC § 8 names the resume trigger and the state it rehydrates | `should-fix` if absent |

### Run durability (HITL-006 / HITL-007)

Both are **declared-and-attested** checks, never runtime probes. A regex for
`if-none-match` proves very little, so neither is allowed to report `pass` on the
strength of a keyword alone.

`HITL-006` reads the `- **Idempotency**:` line of every
`src/agent/skills/<name>/SKILL.md` operational contract. It accepts a statement
that names how replay is made safe (`writing the same decision for the same
`rma_id` is a no-op`) or that disclaims the side effect (`read-only; safe to
re-run`, `pure function of inputs`). It rejects `Yes`, `Idempotent` and `N/A`,
which restate the label and attest nothing. A pilot that publishes no contracts
is `not-verified`, never `must-fix` — the check judges what a pilot declares
about itself, so it must not fail a pilot for a shape it never adopted.

`HITL-007` reads **§ 8 only**, so a pilot that mentions Cosmos in § 9 cannot
satisfy it by accident. A supervisor may take three days; no agent session
survives three days and none should try. The correct shape is persist, exit, and
resume on an inbound trigger — so § 8 has to name that trigger and the state the
fresh invocation rehydrates.

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
- Every skill contract says "Idempotency: yes" and none says what makes
  the replay safe. The vocabulary is everywhere, the verification is
  nowhere.
- § 8 describes the gate but not what resumes it, so the design implies
  holding an open session for a decision that takes three days. The
  first host recycle loses the unit of work.

## Remediation

| Finding | Skill |
|---|---|
| Wire Teams approval gate | `threadlight-hitl-patterns`, `foundry-teams-bot` |
| Author audit-trail schema | `threadlight-hitl-patterns` |
| Add idempotency keys | `threadlight-hitl-patterns` |
| Declare the resume trigger | `threadlight-event-triggers` |

## Why this pillar matters

A pilot with HITL declared in § 8 but not wired is the worst kind: it
looks "responsibly governed" in the deck and isn't. A double-firing
HITL is the worst kind 2.0: the audit log shows "approved" and then
two actions and no one knows which "Approve" click did what.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
