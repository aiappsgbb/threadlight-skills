---
name: threadlight-router-bench
description: >-
  Offline self-improvement cold-path for threadlight CI/GHCP runs on Microsoft
  Foundry. `learn <run_id>` harvests ONE GitHub Actions run (green or red, no
  baseline needed) and emits a grounded learnings digest: phase parity, a
  reality-tuned failure taxonomy (dependency drift, rate-limit cascade, wire
  protocol, model-unavailable, auth, quota, deploy), and recommendations — using
  `--log-failed` for high precision so green runs stay clean. `bench <candidate>
  <baseline>` is an OPTIONAL paired cost/efficiency scorecard of a model-router
  run vs a baseline model (gpt-5.4-mini) from Azure Monitor token metrics. USE
  FOR: learn from CI run, self-improving cold-path,
  inspect GHCP logs, CI failure taxonomy, why did my e2e fail, router efficiency,
  model-router cost, token cost vs baseline, cost scorecard,
  learnings digest, run retro, router quality matrix. DO NOT USE FOR: dispatching
  or fixing the e2e workflow — use threadlight-cicd; running
  evals/redteam/govern legs — use those skills; live agent runtime monitoring.
metadata:
  version: "0.1.0"
---

# Threadlight Router-Bench — learn from any CI run, then (optionally) price it

The **offline self-improvement leg** for threadlight pilots driven by the GitHub
Copilot CLI on Microsoft Foundry. It turns a finished CI run into durable,
grounded learnings — and, when you want the efficiency story for customers, a
cost/quality scorecard of model-router vs a standard baseline.

```
… → BUILD/DEPLOY → e2e CI run → [ threadlight-router-bench ] → learnings + (optional) cost scorecard
```

> **Why this skill exists.** Two needs collided. (1) Every CI/GHCP run — even a
> green one — carries learnings, and re-reading raw logs by hand is slow and
> noisy. (2) model-router is a great efficiency story, but "efficiency" only
> means something if you can show **quality AND cost** against a standard model.
> This skill does both **offline**, from runs that already happened, so you are
> never forced to run pairs of jobs just to learn something.

## Three independent modes

| Mode | Command | Needs a baseline? | Output |
|---|---|---|---|
| **learn** (primary) | `learn <run_id>` | **No** — any single run | `learnings-<run_id>.json` + `.md` |
| **bench** (optional) | `bench <cand> <base>` | Yes — paired runs | `scorecard-<cand>-vs-<base>.json` + `.md` |
| **validate** (optional) | `validate --dispatch` / `--ingest <manifest>` | No — runs its own 3-arm matrix | `router-validation.json` + `.md` |

`learn` is the self-improvement cold-path the team actually asked for: point it at
**one** run and it tells you what to fix or keep. `bench` and `validate` are the
heavier optional paths; `bench` is the customer-facing efficiency proof, and
`validate` is the controlled quality matrix.

---

## Mode 1 — `learn` (single-run learnings, the primary path)

```bash
python skills/threadlight-router-bench/scripts/router_bench.py learn <RUN_ID> \
  --repo aiappsgbb/threadlight-skills \
  --out router-bench-out \
  --deployment model-router        # label only, for the digest header
  # --with-legs                    # also download artifacts to attach Phase-5 KPI legs
```

What it does, deterministically (no LLM in this step):

1. `gh run view <id> --json …` → conclusion, branch, title, and the **metric
   window** (startedAt→updatedAt).
2. `gh run view <id> --json jobs` → **phase parity** (per-phase worst step
   conclusion across smoke/design/pattern/deploy/invoke/legs/teardown).
3. **Logs with precision:** failures use `gh run view <id> --log-failed`
   (failing steps only); successes use the full `--log` but are scanned
   **warnings-only**. This is the hard-won precision rule — see below.
4. Classify anomalies into the fixed taxonomy, dedup **by category** with a
   `count` + first evidence line, and write the `threadlight-router-learnings/v1`
   digest (JSON + Markdown).

### Precision rules (learned against real runs — do not soften)

- **Source scope, not full logs.** A naive full-`--log` scan of the GREEN run
  `28437323962` produced 10/10 false positives (echoed shell source, prompt
  text, the workflow's own grep-based error detectors). Scoping failures to
  `--log-failed` gave **100% precision** on real failures `28435017341`
  (rate-limit cascade) and `28389162228` (agent-framework 1.4 dependency drift).
- **Classify the message, not the line.** Only the message column is classified;
  the `gh` `<job>\t<step>` prefix is stripped first, so a step *name* like
  `threadlight-deploy + azd up` can't manufacture a `deploy` finding.
- **Drop command-echo.** Lines containing the `[36;1m` ANSI token (GitHub's
  "Run" block echoing step source in cyan-bold) and `##[group]`/`##[command]`
  control lines are noise — never classified.
- **Green runs are warnings-only.** A successful run emits **no** high/medium
  findings; only low-sev `retry`/`slow_turn`/`router_fallback` may surface.
- **Keep copilot glyphs.** Do **not** strip `● │ └` — in failing-step scope
  `● Request failed due to a transient API error` is a real `model_unavailable`
  signal.

### Failure taxonomy (ordered; first match wins)

`dependency` (high) → `skill_loader` (med) → `wire_protocol` (high) →
`rate_limit` (med) → `model_unavailable` (high) → `auth` (high) →
`quota` (med) → `deploy` (high) → `tool_failure` (med) →
`router_fallback` (low) → `retry` (low) → `slow_turn` (low).

Order matters: `rate_limit` precedes `model_unavailable` so
`exceeded rate limit` classifies as a rate limit, while `transient API error`
classifies as model-unavailable.

### Then: turn the digest into recommendations (the only LLM step)

After `learn` writes the digest, read **`learnings-<run_id>.md`** (never the raw
logs) and produce recommendations grounded **only** in the structured findings:

> For each finding in the digest, name the most likely root cause and the
> smallest durable fix (pin/constraint, retry/backoff, capacity/quota change,
> wire-API switch, RBAC). Map every recommendation back to a finding `id`. If the
> run is green with zero findings, say so and surface at most the low-sev
> warnings (slow turns / retries) worth watching. Do not invent issues that are
> not in the digest.

Capture anything reusable (a new taxonomy signature, a recurring root cause) as a
follow-up so the cold-path keeps improving across runs.

---

## Mode 2 — `bench` (optional paired cost/efficiency scorecard)

```bash
python skills/threadlight-router-bench/scripts/router_bench.py bench <CAND_RUN> <BASE_RUN> \
  --repo aiappsgbb/threadlight-skills --out router-bench-out \
  --resource "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<acct>" \
  --baseline-model gpt-5.4-mini \
  --candidate-deployment model-router \
  # --baseline-deployment gpt-5.4-mini   # also fetch the baseline run's actual usage
  # --prices prices.json                 # override the seed price table with real $
```

Each run's metric window bounds an `az monitor metrics list` query for
`InputTokens`/`OutputTokens` split by `ModelName`; the scorecard reports candidate
actual $ against a **counterfactual** (the same token volume repriced entirely at
the baseline model) and a plain `router-premium` / `router-savings` / `neutral`
verdict.

> **Honest efficiency framing.** On the hard agentic workload, model-router routed
> entirely to gpt-5.4/gpt-5.5 and **zero** to gpt-5.4-mini — a **premium**, not a
> saving. "Efficiency" here means per-turn right-sizing, not a guaranteed lower
> bill. The scorecard says so out loud. (Ironically, the rate-limited baseline in
> the failures *was* gpt-5.4-mini at its capacity cap.)

Prices in `scripts/prices.py` are **seed values for relative reasoning, not
billing truth** — override with a current Azure price export for real figures.

## Mode 3 — `validate` (router quality matrix, optional)

Tests the hypothesis that `gpt-5.4-mini` "keeps up" on a simple workload but
a complex workload needs the model-router to recover quality — via a controlled
6-run matrix (3 arms × 2 workloads).

### Infra prerequisites

```bash
# Stand up the gpt-5.4 strong-arm deployment (idempotent):
bash scripts/ci/foundry-strong-arm.sh

# Snapshot the model-router subset, then pin it to {gpt-5.4, gpt-5.4-mini}:
bash scripts/ci/router-subset.sh record
bash scripts/ci/router-subset.sh set
```

> **Restore is a guardrail.** `bash scripts/ci/router-subset.sh restore` MUST
> run when the matrix finishes — or on any failure. The shared router must be
> put back exactly as found. See Guardrails below.

### Running the matrix

```bash
# Dispatch a fresh 6-run matrix (one wave per workload, runs in background):
python skills/threadlight-router-bench/scripts/router_bench.py validate \
  --dispatch \
  --workloads returns-triage fsi-kyc-aml \
  --ref unsafecode-automatic-fiesta \
  --repo aiappsgbb/threadlight-skills \
  --resource "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<acct>" \
  --out router-validation-out

# After runs complete, score the manifest:
python skills/threadlight-router-bench/scripts/router_bench.py validate \
  --ingest router-validation-out/matrix-manifest.json \
  --resource "/subscriptions/<sub>/resourceGroups/<rg>/providers/..." \
  --out router-validation-out
```

### Arms, workloads, and 2-wave orchestration

| Arm | Deployment | Wire API |
|---|---|---|
| `mini` (baseline) | `gpt-5.4-mini` | `responses` |
| `router` | `model-router` (subset: gpt-5.4, gpt-5.4-mini) | `completions` |
| `strong` (quality ceiling) | `gpt-5.4` | `responses` |

Workloads: `returns-triage` (simple) and `fsi-kyc-aml` (complex), defined as
packs under `.github/workloads/<name>/{phases,rubric,meta}.yml`.

Runs dispatch in **waves** — one wave per workload; within a wave every cell
targets a distinct model deployment, so Azure Monitor token metrics don't bleed
across arms (metrics carry no run-id dimension).

### Scoring axes

Each cell is scored on 4 axes: **phases_ok** (phase parity), **rubric**
(keyword-rubric score over produced artifacts), **rounds** (agent turns), and
**cost** ($).

### Verdict thresholds

An arm `keeps-up` iff `phases_ok AND rubric ≥ 0.8 AND rounds ≤ 1.5 × strong_rounds`;
otherwise `falls-behind` (with reasons listed). The router additionally gets a
three-way verdict: `closes-the-gap` / `not-worth-it` / `mixed`.

> **n=1 caveat.** Each matrix is a single sample per cell. Treat close cells
> (rubric near 0.8 or rounds near 1.5×) as inconclusive and re-run before acting.

### Restore infra

```bash
bash scripts/ci/router-subset.sh restore   # MUST run — even on failure
```

---

## Guardrails

- **completions wire API** for model-router e2e runs — the `responses` wire
  returns HTTP 400 "operation unsupported" on this deployment.
- **Monitoring Reader** on the AI Services account is required for `bench`
  metrics; token metrics carry **no run-id dimension**, so serialize benches on a
  shared deployment and rely on the time window for attribution.
- **Teardown stays forced** in the e2e workflow; this skill only *reads* finished
  runs — it never provisions or deletes Azure resources.
- **`validate` MUTATES shared infra** — it creates the `gpt-5.4` strong-arm
  deployment and repins the model-router subset to `{gpt-5.4, gpt-5.4-mini}`.
  Always run `bash scripts/ci/router-subset.sh restore` afterward (even on
  failure). `learn` and `bench` are read-only; only `validate` touches live
  deployments.
- Stdlib-only Python; tests run under the repo's `python-pytest.yml`
  (`actions/setup-python` 3.13).

## Outputs

| File | Schema | Mode |
|---|---|---|
| `learnings-<run_id>.json` / `.md` | `threadlight-router-learnings/v1` | learn |
| `scorecard-<cand>-vs-<base>.json` / `.md` | `threadlight-router-scorecard/v1` | bench |
| `router-validation.json` / `.md` | `threadlight-router-validation/v1` | validate |

## Layout

```
skills/threadlight-router-bench/
  SKILL.md
  scripts/
    router_bench.py     # CLI dispatcher: learn (primary) + bench + validate
    harvest.py          # gh run metadata, phase parity, leg manifests, --log-failed
    findings.py         # ordered taxonomy, message-scoped, category dedup
    report.py           # learnings/v1 digest + Markdown
    prices.py           # seed $/1M token table (+ override)
    metrics.py          # az monitor metrics parse (lowercase dims)
    score.py            # cost rollup + counterfactual scorecard + validation_scorecard
    matrix.py           # 3-arm × N-workload matrix orchestrator, 2-wave dispatch
    rubric.py           # keyword-rubric scorer over PoC artifacts (phases/rubric/meta)
  references/fixtures/  # real-log + az-metrics + leg-manifest fixtures
  tests/                # pytest, stdlib-only
# Workload packs (phases, rubric, meta) live at:
#   .github/workloads/<name>/{phases,rubric,meta}.yml
```
