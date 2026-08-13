# Run Durability for Super-Agents — Deep Dive

- **Status:** Proposed — analysis only, no implementation
- **Date:** 2026-08-13
- **Author:** Brainstormed with Copilot CLI
- **Decides:** whether a pillar 15 `run-durability` is justified, and what it
  would check
- **Related:** pillar 8 `hitl-audit`, pillar 11 `reliability`,
  `threadlight-event-triggers`, `examples/returns-triage-governed`

## 1. Why the existing pillars do not already cover this

Pillar 11 `reliability` asks infrastructure questions: multi-region plan against
RTO/RPO, tested backup/restore, runbook, chaos test. Those are questions about
**the service being available**.

This document is about a different unit of failure: **one unit of work, halfway
through a skill chain, when the process hosting it stops existing.** The service
can be perfectly available and that unit of work can still be silently lost,
silently duplicated, or silently marked complete having never completed.

Nothing in the current 13 pillars asks that question.

## 2. The architecture this applies to

Threadlight pilots are **one Foundry hosted agent with N skills** — markdown
instruction modules under `src/agent/skills/<name>/SKILL.md`, sequenced by
behavioral guidelines in `AGENTS.md`. There is no orchestrator process and no
inter-agent protocol. `examples/returns-triage-governed` is the reference shape:

```
intake-validation → policy-eligibility → fraud-escalation → disposition-decision
                                              ↓ (gate fires)
                                     supervisor gate (SPEC § 8)
```

State is deliberately externalized to Cosmos DB; the example states plainly that
there is no local filesystem to rely on. That is the right call — and it is
precisely what makes resume semantics **application-owned** rather than
platform-provided. Foundry gives per-session isolation and stateful resume; it
does not give you *your* definition of "this return has already been decided".

## 3. Five failure questions

### Q1 — Compounding error is invisible because evals score the outcome

The reference chain has four skills plus a conditional gate: roughly five to six
decision points per unit of work. Per-step reliability compounds:

| Per-step | 4 steps | 6 steps | 10 steps |
|---:|---:|---:|---:|
| 99% | 96% | 94% | 90% |
| 95% | 81% | 74% | 60% |
| 90% | 66% | 53% | 35% |

The uncomfortable part is not the arithmetic — it is that **today's evals cannot
locate the weak step.** `threadlight-evals` scores SPEC § 9 scenarios, which are
end-to-end outcomes. A skill that is right 80% of the time in the middle of the
chain is invisible until the end-to-end number drops, and when it does drop
there is no evidence pointing at which skill moved.

*Implication:* per-skill trajectory evaluation is a prerequisite for reasoning
about chain reliability at all. This is the hand-off to the skill-composition
work, not something a durability pillar can fix on its own.

### Q2 — Idempotency is declared, never verified

`threadlight-design` requires every skill to publish an operational contract
that includes an idempotency statement. Pillar 8 `hitl-audit` lists "idempotent"
in its definition of good. And `threadlight-event-triggers` generates receivers
that wire idempotency keys.

So the vocabulary is everywhere. The **verification is nowhere**: the only
idempotency actually asserted anywhere in the repo is the assessor's own
(`production_ready.py`, issue #30). No check confirms that a pilot's terminal
write — `returns_apply_decision` in the reference example — is safe to replay.

That is the highest-value gap in this document, because the failure it permits
is a *double side effect on a real customer*, not a degraded score.

The check is cheap in principle: a terminal write needs a deterministic
idempotency key derived from the unit of work, and a conditional write
(`if-not-exists` / ETag) rather than a blind insert. Both are statically
observable.

### Q3 — A HITL gate can outlive any session

SPEC § 8 gates are human approvals. A returns supervisor may take three days.
No agent session survives three days, and it should not try to.

Therefore the correct shape is **not** "hold the run" but:

1. Persist the pending decision plus everything needed to resume it.
2. Exit.
3. Resume on an **inbound trigger** (approval webhook, queue message) — a fresh
   invocation that reconstructs state from the store.

Every component for this already exists: `threadlight-hitl-patterns` produces
the gate and audit trail, `threadlight-event-triggers` produces ACA HTTP / queue
receivers with idempotency wiring, Cosmos holds the state.

**What is missing is the statement that these compose into the resume path.**
This is a documentation and contract gap, not a component gap — which makes it
unusually cheap to close.

### Q4 — "Declared done" without independent verification

Two independent observations converge here: multi-agent failure taxonomies
identify *task verification failure* as its own cluster, and practitioners
report that agents asked to assess their own output reliably rate it well.

In a super-agent, the analogue is a skill whose contract says it emitted a
terminal decision, in a chain where nothing re-reads the store to confirm the
write landed and is well-formed.

The rule that follows is the same one that governs judge calibration in
`threadlight-evals`: **the component that produces must not be the component
that certifies.** Verification is a separate read-back step, or it is not
verification.

### Q5 — Draw the line between platform and application

To avoid over-scoping a future pillar, the split is:

| Concern | Owner |
|---|---|
| Session isolation, sandbox, scale-to-zero, stateful resume of the *runtime* | Foundry |
| Region failover, backup/restore, chaos | pillar 11 |
| Idempotency of the terminal write | **application** |
| Resume-from-gate path and its trigger | **application** |
| Read-back verification of terminal state | **application** |
| Replay safety of the skill chain | **application** |

A durability pillar should only ever ask about the application rows.

## 4. What a pillar 15 would check

Deliberately small; each item is either statically observable or a declared
artifact:

| Check | Question | Severity if absent |
|---|---|---|
| `terminal_write_idempotent` | Terminal write derives a deterministic key and uses a conditional write | must-fix |
| `resume_path_declared` | The § 8 gate names its resume trigger and the state it rehydrates | must-fix when § 8 declares gates |
| `state_externalized` | No unit-of-work state depends on process-local storage | should-fix |
| `terminal_state_verified` | A read-back confirms the terminal write, performed outside the producing skill | should-fix |
| `chain_length_declared` | Number of sequential decision points is recorded, so compounding is visible | should-fix |

`not-applicable` is the correct status for a single-skill pilot with no terminal
side effect. `not-verified` — never a silent `pass` — is correct when the pilot
uses a store the validator cannot inspect.

## 5. Open questions before implementing

1. **Is a pillar the right container?** Q1 belongs to skill composition and Q2
   arguably belongs to pillar 8, which already claims idempotency in its
   definition of good. A defensible alternative is: strengthen pillar 8, and
   drop the standalone pillar.
2. **Can idempotency be detected without false confidence?** A regex for
   `if-none-match` proves very little. If the check cannot be made honest, it
   should be a declared-and-attested artifact rather than a probe.
3. **Cost of a 14th/15th pillar.** Every pillar added dilutes the score and
   lengthens the report. The bar for a new one is high and should stay high.

## 6. Byproduct worth noting

An append-only record of unit-of-work state transitions — the thing Q2 and Q4
both require — is also the automatic-logging evidence the EU AI Act evidence
pack maps to Art 12. One artifact, two obligations. That improves the
cost/benefit of this work independently of whether it lands as a pillar.

## 7. Recommendation

Land Q2 (idempotency verification) and Q3 (resume path declaration) **first**,
and land them inside pillar 8 rather than as a new pillar. Re-open the question
of a standalone pillar 15 only if Q1 and Q4 survive the skill-composition work
as genuinely separate concerns.
