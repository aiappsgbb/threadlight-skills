# Pillar 6 — `continuous-evals`

> **What this pillar answers.** Are SPEC § 9 eval scenarios scheduled
> (not just runnable on demand)? Are threshold alerts wired? Is the
> last run fresh? Are eval datasets stored where they can be re-run?

The customer's risk team will ask: "what catches a regression after we
swap models?" The answer is **continuous evals**.

## Eval plan A vs Plan B

`foundry-evals` documents two plans:

- **Plan A (preferred):** Foundry Continuous Evaluation — scheduled
  runs against named datasets, results in AppIn, alerts on threshold.
- **Plan B (fallback):** Scheduled GitHub Action (or ACA Job) that
  invokes evals on a cron and emits results.

Either is acceptable for production; **neither being present is a
hard-fail**.

## Checks

### Static

| ID | Check | Default status |
|---|---|---|
| `EVAL-001` | SPEC § 9 lists eval scenarios (count > 0) | `must-fix` if zero |
| `EVAL-002` | Eval datasets stored under `evals/` or `specs/evals/` (referenced from § 9 or `evals/README.md`) | `must-fix` if absent |
| `EVAL-003` | Eval dataset shape includes `tool_calls` + `tool_outputs` (required for `tool_output_utilization` grader to score correctly) | `should-fix` if `tool_calls` absent for grounded answers |
| `EVAL-004` | Threshold values declared (per-scenario pass/fail thresholds) | `should-fix` if absent |
| `EVAL-005` | Either Plan A (Foundry CE config under `infra/` / `evals/` referencing schedule) OR Plan B (GH Action / ACA Job for evals) present | `must-fix` if neither |
| `EVAL-006` | Latest eval run output stored (`evals/runs/*.json` or referenced from `docs/eval-runs/`) | `should-fix` if no history |

### Live (tier 2 — `Monitoring Reader`)

| ID | Check | Default status |
|---|---|---|
| `EVAL-101` | If Plan A: Foundry continuous-evaluation resource present in RG | `must-fix` if Plan A declared but missing |
| `EVAL-102` | If Plan A: latest scheduled run within freshness window (default 7d) | `should-fix` if stale |
| `EVAL-103` | If Plan B: GH Action workflow file present and enabled in repo (or ACA Job last-run < 7d) | `should-fix` if stale |
| `EVAL-104` | Alert rule wired to fire on eval threshold breach | `must-fix` if no alert |
| `EVAL-105` | Latest eval pass rate ≥ § 9 declared minimum | `should-fix` if drift > 5% |

## Common gaps

- Evals exist but only ever run manually on a dev machine.
- Eval dataset is the agent's training data, not held-out scenarios.
- No alert wired → eval regression goes unnoticed for weeks.
- `tool_output_utilization` grader returns "fabricated" on every row
  because the dataset shape is missing `tool_calls` / `tool_outputs`.
- Thresholds aren't declared, so "pass/fail" is a subjective call by the
  next reviewer.

## Remediation

| Finding | Skill |
|---|---|
| Schedule continuous evals | `foundry-evals` (Plan A or Plan B) |
| Fix eval dataset shape | `foundry-evals` (enriched dataset shape) |
| Wire threshold alert | `foundry-observability` + `foundry-evals` |
| Add eval scenarios | `threadlight-design` (SPEC § 9) |

## Why this pillar matters

A pilot that's "working today" without continuous evals will quietly
regress the first time anything upstream changes: model version, system
prompt, retrieval index, tool schema, anything. The customer's data
science team will demand this conversation. The skill ships the
artefact that answers it.

---
**v0.4.0 — remediation recipes:** Each must-fix finding above has a step-by-step recipe at `references/remediation-recipes/{FINDING_ID}.md`. See the parent SKILL.md for the 3-phase onboarding flow.
