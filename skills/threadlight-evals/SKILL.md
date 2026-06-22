---
name: threadlight-evals
description: >
  DISCOVER + GOVERN + IMPROVE evals leg for threadlight pilots on Microsoft
  Foundry. Runs and verifies offline batch quality evals, Foundry Continuous
  Evaluation on live threads, and champion-challenger comparison gates, then
  emits `specs/evals-manifest.json` for pillar 6 production-readiness scoring.
  USE FOR: continuous evals, offline eval gate, eval schedule, Foundry
  Continuous Evaluation, create_agent_evaluation, Application Insights eval
  results, eval threshold alert, eval run freshness, eval dataset shape,
  tool_calls tool_outputs, champion challenger, A/B eval gate, model swap gate,
  prompt swap gate, foundry-evals pipeline leg, continuous-evals pillar,
  EVAL-001, EVAL-002, EVAL-003, EVAL-004, EVAL-005, EVAL-006, EVAL-101,
  EVAL-102, EVAL-103, EVAL-104, EVAL-105, evals-manifest.
  DO NOT USE FOR: token-level content filtering at the model edge — use the
  model guardrail / Azure AI Content Safety; adversarial scanning — use
  threadlight-redteam; agent-runtime action governance — use threadlight-govern;
  deep evaluator or dataset authoring — use foundry-evals.
metadata:
  version: "0.1.0"
---

# Threadlight Evals — run continuous evals, then prove they ran

The **DISCOVER + GOVERN + IMPROVE** evals leg of `path2production` for AI agents
on Microsoft Foundry. `threadlight-production-ready` pillar 6 scores whether
continuous evals are scheduled, fresh, and alerting; this skill is the executable
leg that owns running and verifying that posture in the pipeline.

```
DESIGN → BUILD/DEPLOY → DISCOVER → PROTECT → [ GOVERN / IMPROVE ] → PRODUCTION-READY
                                            threadlight-evals
```

> **Why this skill exists.** A pilot can have strong unit tests, local smoke
> tests, and a green deployment gate, yet still regress silently after a model,
> prompt, retrieval, or tool-schema change. Previously the chain delegated eval
> work entirely to `foundry-evals`, and pillar 6 could only score whether a
> schedule appeared to exist. This skill makes evals a threadlight-owned leg:
> it detects offline datasets, live continuous evaluation wiring, threshold
> alerts, run freshness, and champion-challenger gates; then emits a manifest the
> scorecard can verify.

## What this skill covers

| Feature | Purpose | Output signal |
|---|---|---|
| **F1 Offline batch quality eval** | Thin-wrap the `foundry-evals` invoke+score path in the threadlight spine. Heavy evaluator authoring still belongs to `foundry-evals`; this leg owns running it and producing evidence. | `eval_scenarios_present`, `eval_datasets_present`, `run_history_present`, `latest_pass_rate_ok` |
| **F2 Online / continuous eval** | Wire Foundry Continuous Evaluation on live threads using `create_agent_evaluation(thread, run, evaluators=[...], app_insights_connection_string=...)`. Results land in Application Insights. | `schedule_present`, `online_eval_wired`, `latest_eval_run_fresh`, `alert_wired` |
| **F3 A/B champion–challenger** | Gate a model/prompt/tool swap by running the same eval dataset against champion and challenger before the swap. | `ab_comparison_present` |

## What this skill does NOT replace

| Concern | Use instead |
|---|---|
| Token-level content filtering, prompt shields, content safety at the model edge | Model guardrail / Azure AI Content Safety |
| Adversarial scanning and jailbreak probe campaigns | `threadlight-redteam` |
| Agent-runtime action policy, tool allow/deny, excessive-agency controls | `threadlight-govern` |
| Deep evaluator authoring, grading rubric design, dataset enrichment work | `foundry-evals` |
| Overall production-readiness scorecard | `threadlight-production-ready` |

## The contract — `specs/evals-manifest.json`

`scripts/evals_check.py` walks a pilot repo and emits a manifest whose
capability keys map directly to pillar 6 (`continuous-evals`) IDs:

| Capability key | Pillar ID | Meaning | Severity when missing |
|---|---:|---|---|
| `eval_scenarios_present` | `EVAL-001` | SPEC § 9 lists eval scenarios or `evals/` contains scenario files | must-fix |
| `eval_datasets_present` | `EVAL-002` | Held-out datasets are stored under `evals/` or `specs/evals/` | must-fix |
| `dataset_shape_ok` | `EVAL-003` | At least one JSON/JSONL row includes `tool_calls` and `tool_outputs` | should-fix |
| `thresholds_declared` | `EVAL-004` | Per-scenario `threshold`, `min_score`, or pass-rate threshold is declared | should-fix |
| `schedule_present` | `EVAL-005` | Plan A Foundry CE schedule or Plan B cron/ACA Job eval runner exists | must-fix |
| `run_history_present` | `EVAL-006` | Latest run output is committed under `evals/runs/*.json` or `docs/eval-runs/` | should-fix |
| `online_eval_wired` | `EVAL-101` | Code calls `create_agent_evaluation(...)` with Application Insights connection | should-fix; must-fix when Plan A is declared but unwired |
| `latest_eval_run_fresh` | `EVAL-102/103` | Latest scheduled run is within the freshness window (default 7 days) | should-fix if stale; not-verified if no history |
| `alert_wired` | `EVAL-104` | Alert or notification exists for eval threshold breach | must-fix |
| `latest_pass_rate_ok` | `EVAL-105` | Latest run pass rate meets the declared minimum | should-fix if below threshold |
| `ab_comparison_present` | `F3` | Champion-challenger comparison config/script exists before swaps | should-fix |

Status taxonomy is exactly: `pass`, `must-fix`, `should-fix`, `not-verified`,
`not-applicable`.

Verdict roll-up:

| Verdict | Meaning |
|---|---|
| `comprehensive` | No `must-fix`, `should-fix`, or `not-verified` findings. Offline, online, alerting, freshness, pass-rate, and A/B gate are all verified. |
| `partial` | No `must-fix`, but one or more advisory or unverified capabilities remain. |
| `offline-only` | Offline basics are present, but at least one hard requirement for continuous/online operation is missing. |
| `none` | Evals are absent or too incomplete to trust. |

The emitted manifest includes:
```json
{
  "schema": "threadlight-evals-manifest/v1",
  "tool_version": "0.1.0",
  "captured_at": "2026-06-22T16:00:00+00:00",
  "freshness_window_days": 7,
  "verdict": "comprehensive",
  "must_fix": [],
  "should_fix": [],
  "not_verified": [],
  "metrics": {
    "pass_rate": 0.91,
    "threshold": 0.8,
    "latest_run": "evals/runs/2026-01-01.json"
  },
  "capabilities": {
    "eval_scenarios_present": {
      "check_id": "EVAL-001",
      "status": "pass",
      "evidence": "specs/spec.md",
      "hint": null
    }
  }
}
```

The top-level `metrics` block surfaces the latest run's `pass_rate`, the
declared `threshold`, and the relative path to the `latest_run`. This is the
join key `threadlight-production-ready` reads (`metrics.pass_rate`) to render
the eval-quality column of its outcome-KPI scorecard — so eval quality flows
into the business-KPI view instead of staying locked inside the evals leg.
`pass_rate`/`latest_run` are `null` when no machine-readable run history exists.

## Usage

```bash
# 1. Assess an existing pilot (read-only) — prints the evals report
python3 scripts/evals_check.py --target ../my-pilot

# 2. Emit the manifest + human report the scorecard consumes
python3 scripts/evals_check.py --target ../my-pilot --emit
#   → writes specs/evals-manifest.json + docs/evals-report.md

# 3. CI gate — exit 2 on any must-fix capability
python3 scripts/evals_check.py --target ../my-pilot --gate

# 4. JSON for automation
python3 scripts/evals_check.py --target ../my-pilot --json

# 5. Override freshness window (pillar default is 7 days)
python3 scripts/evals_check.py --target ../my-pilot --freshness-days 14
```

Flags:

| Flag | Meaning |
|---|---|
| `--target PATH` | Pilot repository root. Defaults to `.`. |
| `--emit` | Writes `specs/evals-manifest.json` and `docs/evals-report.md` under the target. |
| `--gate` | Returns exit code `2` when any capability is `must-fix`. |
| `--json` | Prints the manifest JSON instead of markdown. |
| `--freshness-days N` | Max age for latest eval run. Default: `7`. |

## The leg, end-to-end

This is a **producing** leg. It emits artefacts. It does not silently mutate the
user's repo for findings; remediation is performed by the agent after reviewing
manifest gaps.

1. **Discover offline eval assets.** Confirm SPEC § 9 scenarios, held-out
   datasets, dataset shape (`tool_calls` + `tool_outputs`), thresholds, and run
   history.
2. **Govern continuous wiring.** Confirm Plan A Foundry Continuous Evaluation or
   Plan B scheduled fallback, plus Application Insights result flow and an alert
   for threshold breach.
3. **Improve safely.** Confirm a champion-challenger comparison gate exists so
   prompt/model/tool swaps are evaluated before promotion.
4. **Emit evidence.** Write `specs/evals-manifest.json` and
   `docs/evals-report.md`; the scorecard consumes the manifest.

## Plan A — Foundry Continuous Evaluation

Preferred production wiring uses Foundry Continuous Evaluation on live threads:

```python
client.evaluations.create_agent_evaluation(
    thread=thread,
    run=run,
    evaluators=[groundedness, relevance, tool_output_utilization],
    app_insights_connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"],
)
```

Use `DefaultAzureCredential` and managed identity/OIDC; do not store secrets in
repo files. See `references/foundry-ce-wiring.md` for the full keyless snippet.

Plan A detection accepts config or code under `infra/` / `evals/` that references
continuous evaluation and a schedule, and code that calls
`create_agent_evaluation(...)` with an Application Insights connection string.

## Plan B — scheduled fallback

Plan B is acceptable when Foundry Continuous Evaluation is not yet available for
the pilot shape. The validator detects:

- `.github/workflows/*.yml` with `cron` and eval runner markers.
- ACA Job / Container Apps job definitions under `infra/` with schedule markers.
- Run outputs committed under `evals/runs/` or `docs/eval-runs/`.

Plan B still needs threshold alerts or workflow failure notifications. No
schedule is a hard fail.

## F3 — champion-challenger gate

Before changing a model, prompt, tool contract, or retrieval configuration:

1. Run champion and challenger against the same held-out dataset.
2. Compare pass-rate, protected-scenario scores, and regression delta.
3. Promote only if the challenger meets the declared thresholds.

Place config under `evals/ab/` or include `champion`, `challenger`, and
`baseline_vs` markers in an eval script/config. See `references/ab-comparison.md`.

## How `production-ready` consumes this

`threadlight-production-ready` pillar 6 reads `specs/evals-manifest.json`.

| Manifest state | Pillar 6 behavior |
|---|---|
| Present, fresh, and `verdict == comprehensive` | Flip EVAL findings to verified with manifest/report evidence. |
| Present and `verdict == partial` | Verify passed capabilities; keep advisory or unverified findings open. |
| Present and `verdict == offline-only` | Treat offline evals as present, but re-open continuous-evals schedule/alert/live gaps. |
| Present and `verdict == none` | Re-open EVAL must-fix findings. |
| Missing or stale manifest | Fall back to legacy scoring and delegate remediation. |

Freshness is determined by the consuming scorecard and by this validator's
`latest_eval_run_fresh` capability. Default window: 7 days.

## Relationship to `foundry-evals`

`foundry-evals` is the deep upstream skill for evaluator selection, rubric
authoring, dataset expansion, and Foundry eval details. `threadlight-evals` is a
thin threadlight pipeline leg: it makes evals a step that runs in the spine,
checks whether the production posture is wired, and emits the manifest the
scorecard understands.

Use both together:

- Use `foundry-evals` to author or repair evaluator definitions and datasets.
- Use `threadlight-evals` to verify the pilot has offline, online, alerting,
  freshness, and A/B gate evidence.

## Files

```
scripts/evals_check.py                 # stdlib validator → evals-manifest.json
references/foundry-ce-wiring.md        # Plan A keyless CE wiring snippet
references/ab-comparison.md            # champion-challenger recipe + gates
references/dataset-shape.md            # held-out dataset row shape
references/evals-manifest.schema.json  # manifest contract
references/fixtures/sample-scheduled/  # passing scheduled/continuous pilot
references/fixtures/sample-manual/     # manual-only pilot with must-fix gaps
tests/test_evals_check.py              # stdlib unittest (no pytest)
```

## Tests

```bash
cd skills/threadlight-evals
python3 -m unittest discover -s tests -v
```

Expected coverage:

- Scheduled fixture has no `must-fix` findings and gates with exit code `0`.
- Manual fixture reports schedule/alert `must-fix` and gates with exit code `2`.
- Manifest includes schema, required keys, and all capability keys.

## Common gaps and remediations

| Gap | Why it matters | Remediation |
|---|---|---|
| Evals only run manually | Regressions after swaps are not caught. | Add Plan A CE schedule or Plan B cron/ACA Job. |
| Dataset lacks `tool_calls` / `tool_outputs` | Tool-output graders cannot score grounding correctly. | Enrich held-out dataset rows; see `references/dataset-shape.md`. |
| Thresholds absent | Pass/fail becomes subjective. | Add `threshold`, `min_score`, or `min_pass_rate` per scenario. |
| No alert | Failures sit unnoticed in telemetry. | Add Azure Monitor alert or workflow notification for threshold breach. |
| No run history | Reviewers cannot prove evals ran. | Commit latest run JSON under `evals/runs/`. |
| No A/B gate | Model/prompt swaps can regress protected scenarios. | Add `evals/ab/` champion-challenger config. |

## Validator behavior

- stdlib-only; Python 3.10+.
- Graceful degradation: uncheckable capabilities become `not-verified`, never a
  crash.
- Read-only unless `--emit` is provided.
- `--emit` writes only the manifest/report artefacts under the target repo.
- `--gate` returns `2` only for `must-fix`; advisory findings remain non-zero in
  the manifest but do not fail the gate.
