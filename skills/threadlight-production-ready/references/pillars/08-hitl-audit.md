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
| `HITL-008` | **Aggregate** roll-up of the `threadlight-governed-actions` approval/output verdict — see below | `not-verified` if no trustworthy manifest |

### `HITL-008` — governed-actions approval/output evidence (aggregate)

`threadlight-governed-actions` owns the detailed approval-and-output assessment.
production-ready never re-runs any of its probes and never imports the assessor;
it reads that skill's `tests/governed-actions-manifest.json` and rolls the
**approval + output** domain up into this single finding.

`HITL-008` owns exactly three child findings — `APR-001` (approval binding:
an approval is bound to the action it approved and cannot be replayed),
`OUT-001` (outputs are payload-free), and `AUD-001` (audit-trail completeness).
None of them is shared with another aggregate, so a governed-actions approval or
output problem shows up here and nowhere else; conversely a runtime or
change-plane child never leaks into this pillar.

The aggregate takes the **worst** status among those three —
`must-fix` > `not-verified` > `should-fix` > `pass` > `not-applicable` — and
names which are open. The child findings are deliberately **not** restated as
production-ready findings: their IDs never enter this skill's catalog, and the
governed-actions manifest remains the single source of truth for the detail.

**Trust limits.** The manifest is untrusted repository content, so before any
child status is believed production-ready re-derives what it can for itself:
schema and exact top-level shape, a supported assessor name and version (an
unreviewed future assessor buys no forward trust), a `pre-deploy`/`post-deploy`
phase, a clean source bound to the repository and commit under assessment,
**recomputed** policy hashes and policy-set digest matched against every
relied-upon evidence entry, a single target environment consistent with the
selected azd environment, an intact freshness window, resolvable evidence
references collected no later than capture, and a `summary` agreeing exactly
with its own findings. Missing, malformed, stale, dirty, or mismatched evidence
makes `HITL-008` `not-verified` with the reason attached — never `pass`, and
never a `must-fix` manufactured from evidence we could not stand behind.
`summary.verdict` alone is never believed: a `governed` verdict over a
`must-fix` child still reports `must-fix`.

**Conformance is not certification.** A trusted, all-`pass` manifest means the
assessor ran against this exact repository state and raised nothing about
approvals, outputs, or the audit trail. That is a scoped, expiring conformance
record — not a certification or a sign-off, and advisory like everything else
this skill reports.

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
