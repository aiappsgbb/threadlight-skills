---
name: threadlight-router-bench
description: >-
  Self-improvement cold-path for threadlight CI/GHCP runs on Foundry.
  `learn <run_id>` harvests ONE GitHub Actions run (green or red, no baseline)
  into a learnings digest: phase parity, failure taxonomy (dependency,
  rate-limit, wire, model, auth, quota, deploy), recommendations, using
  `--log-failed` so green runs stay clean. OPTIONAL: `bench` scores a
  model-router run vs a baseline from Azure Monitor tokens by served model;
  `workload` runs a deterministic task mix through model router modes vs fixed
  models for $ per 1k tasks at list price plus routing fee; `pool` lists the
  router pool. USE FOR: learn from CI run, self-improving cold-path, inspect
  GHCP logs, CI failure taxonomy, why did my e2e fail, router efficiency,
  model-router cost, router vs fixed model, token cost vs baseline, cost
  scorecard, learnings digest, run retro, router quality matrix. DO NOT USE
  FOR: dispatching or fixing the e2e workflow — use threadlight-cicd; running
  evals/redteam/govern legs — use those skills; live agent runtime monitoring.
metadata:
  version: "0.2.0"
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

## Independent modes

| Mode | Command | Needs a baseline? | Output |
|---|---|---|---|
| **learn** (primary) | `learn <run_id>` | **No** — any single run | `learnings-<run_id>.json` + `.md` |
| **bench** (optional) | `bench <cand> <base>` | Yes — paired runs | `scorecard-<cand>-vs-<base>.json` + `.md` |
| **validate** (optional) | `validate --dispatch` / `--ingest <manifest>` | No — runs its own 3-arm matrix | `router-validation.json` + `.md` |
| **workload** (optional) | `workload --endpoint <url>` / `--ingest <records>` | No — router and fixed arms side by side | `router-workload.json` + `.md` (+ records) |
| **pool** (pre-flight) | `pool <subscription> <region>` | — | router pool listing (stdout) |

`learn` is the self-improvement cold-path the team actually asked for: point it at
**one** run and it tells you what to fix or keep. `bench` and `validate` are the
heavier optional paths; `bench` is the customer-facing efficiency proof, and
`validate` is the controlled quality matrix. `workload` is the fast, direct
router-vs-fixed comparison on the Responses API that pilots use as go-live
evidence for their model configuration.

> **Current evidence:** [2026-11-20 post-Ignite workload finding](references/findings/2026-11-20-router-workload-post-ignite.md).
> The [2026-06-30 finding](references/findings/2026-06-30-router-validation-5.4-vs-mini.md)
> is superseded.

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
  --baseline-model gpt-6-luna \
  --candidate-deployment model-router \
  --candidate-serves-via gpt-6-luna gpt-6.1-terra gpt-6.1-sol \
  # --baseline-deployment gpt-6-luna     # also fetch the baseline run's actual usage
  # --prices retail.json                 # Retail Prices API export or override JSON
```

Each run's metric window bounds an `az monitor metrics list` query for
`InputTokens`/`OutputTokens` with `ModelDeploymentName eq '*' and ModelName eq '*'`.
Routed tokens land under the router deployment **and** under your own
same-resource deployments that serve routed requests first, so a router-only
filter undercounts. `bench` then keeps the candidate deployment plus
`--candidate-serves-via`, attributes tokens by the **served** `ModelName`
(versions normalized), reports router-named series as `unattributed`, and adds the
routing fee. The scorecard reports candidate estimated $ against a
**counterfactual** (the same token volume repriced entirely at the baseline model)
and a plain `router-premium` / `router-savings` / `neutral` verdict.

> **Honest efficiency framing.** In the 2026-06 matrix the router routed the hard
> agentic workload entirely to gpt-5.4/gpt-5.5 — a **premium**. In Nov 2026 the full
> pool in Balanced cost about 2.7× fixed gpt-6-luna on an easy mix, and Quality
> about 34×. "Efficiency" means per-turn right-sizing, not a guaranteed lower bill;
> the scorecard says so out loud.

### Prices

`scripts/prices.py` holds `SEED_PRICES`: Global Standard, short-context list
prices copied from the Azure Retail Prices API on `PRICES_AS_OF` (2026-11-20),
with `cached_input` and `cache_write` rates where meters exist and the routing
fee ($0.14 per 1M input tokens Global, $0.154 Data Zone). `gpt-6.2-*` had no
retail meter on that date and is flagged **unverified**. For current figures,
pull live prices: `fetch_retail_prices(region)` queries
`https://prices.azure.com/api/retail/prices` with a finite per-request timeout,
bounded pages and 429-aware retries, and `parse_retail_prices` maps meters
through an explicit per-model table (`METERS`; Grok meters are per 1K tokens and
are normalized). Unmapped models are reported as missing, never guessed. All
costs are **estimates** (tokens × list price), not billed spend.

## Mode 3 — `validate` (router quality matrix, optional)

Tests whether a small fixed model "keeps up" on a simple workload and whether a
curated model-router subset recovers quality on a complex one — via a
controlled matrix (3 arms × 2 workloads) through the e2e CI.

### Infra prerequisites

```bash
# Approved target only (the helpers embed none):
export ROUTER_SUBSCRIPTION_ID=<approved-subscription-id> ROUTER_RESOURCE_GROUP=<approved-rg> ROUTER_ACCOUNT=<approved-account>
# Stand up own deployments of gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol (idempotent):
bash scripts/ci/foundry-strong-arm.sh

# Snapshot the model-router subset, then pin it to {gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol}:
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
| `small` (baseline) | `gpt-6-luna` | `responses` |
| `router` | `model-router` (subset: gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol, each with its own deployment) | `completions` |
| `strong` (quality ceiling) | `gpt-6.1-sol` | `responses` |

Workloads: `returns-triage` (simple) and `fsi-kyc-aml` (complex), defined as
packs under `.github/workloads/<name>/{phases,rubric,meta}.yml`.

Runs dispatch in **waves**. Within a wave no two cells can produce tokens on
the same deployment — including the own deployments that serve the router arm's
routed requests (`serves_via`) — so the router arm runs in its own wave after the
fixed arms. Azure Monitor token metrics carry no run-id dimension.

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

## Mode 4 — `workload` (router vs fixed models, optional)

```bash
# Live: Entra ID auth via `az account get-access-token --resource https://ai.azure.com`
python skills/threadlight-router-bench/scripts/router_bench.py workload \
  --endpoint "https://<resource>.services.ai.azure.com/openai/v1/responses" \
  --router-deployment model-router --modes balanced quality \
  --fixed gpt-6-luna gpt-6.1-sol \
  --taskset easy --reps 2 --concurrency 4 \
  --live-prices --region <region> --scope global \
  --out router-workload-out

# Offline: score recorded records (this reproduces the Nov 2026 finding)
python skills/threadlight-router-bench/scripts/router_bench.py workload \
  --ingest skills/threadlight-router-bench/references/fixtures/workload-recorded-easy.jsonl \
  --prices skills/threadlight-router-bench/references/fixtures/retail-prices-swedencentral.json
```

- **Task mix** (`scripts/workload_tasks.py`): `easy` (40) or `hard` (20) tasks over
  simple Q&A, RAG, function tools, numeric reasoning and Python code, each with a
  deterministic grader (no LLM judge).
- **Router arms** send `Foundry-Features: ModelRouterControls=V1Preview` and
  `routing_config` with the arm's `mode` and `session_affinity.strategy: none` so
  repetitions are comparable ([Learn: routing controls](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-routing-controls)).
  `--router-deployment auto` measures Auto. Fixed arms call a deployment by name.
- **Recorded per call:** served model (`model`, falling back to the last
  `routing_trace` attempt when `model` names the router), tokens incl. cached
  input and cache writes, `routing_trace` latency, `x-ratelimit-key`, retries and
  429s. HTTP 429/5xx are retried with `retry-after` and a bounded attempt count.
- **Report:** per arm quality, estimated $ per 1k tasks, routing fee, served-model
  mix, tasks that switched model across reps, throttled calls, unpriced or
  unverified-price models. A router arm that sends **≥ 90 %** of calls to one model
  is flagged `dominant_model`: call that model directly and drop the routing fee.
- **Code execution:** code tasks run model-written Python locally (`python -I`,
  temp dir, 10 s timeout). Use `--no-code-exec` where that is not acceptable.

## Mode 5 — `pool` (pre-flight)

```bash
python skills/threadlight-router-bench/scripts/router_bench.py pool <subscription-id> <region>
```

Lists the models model router `2025-11-18` can route to in the region from the
ARM models API (`capabilities.models`), instead of hard-coding the pool size
([Learn: model router models](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-models)).
Some pool models (for example Claude, or paid Marketplace models) may not be
deployable in a given subscription.

---

## Guardrails

- **Wire API.** Model router serves Responses, Chat Completions and Messages
  ([Learn: model router APIs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-apis)).
  GitHub Copilot BYOK reaches it over Chat Completions
  ([Learn: model router with agents](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router-agents)),
  so GHCP e2e runs keep the **`completions`** wire for the router arm; `workload`
  uses Responses.
- **Deploy subset models in the same resource.** Router-managed capacity was
  throttled at about 12–15 RPM in Nov 2026 (`x-ratelimit-key:
  model-router-2025-11-18`); own deployments serve routed requests first and
  removed the throttle ([Learn: quotas](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-quotas)).
- **Monitoring Reader** on the AI Services account is required for `bench`
  metrics; token metrics carry **no run-id dimension**, so serialize benches on a
  shared deployment and rely on the time window for attribution.
- **Teardown stays forced** in the e2e workflow; this skill only *reads* finished
  runs — it never provisions or deletes Azure resources.
- **`validate` MUTATES shared infra** — it creates own deployments of
  `gpt-6-luna`, `gpt-6.1-terra` and `gpt-6.1-sol` and repins the model-router
  subset to them. Always run `bash scripts/ci/router-subset.sh restore`
  afterward (even on failure). `learn`, `bench` and `pool` are read-only;
  `workload` only sends inference requests (it costs tokens, it changes no
  deployment).
- Stdlib-only Python; tests run under the repo's `python-pytest.yml`
  (`actions/setup-python` 3.13).

## Outputs

| File | Schema | Mode |
|---|---|---|
| `learnings-<run_id>.json` / `.md` | `threadlight-router-learnings/v1` | learn |
| `scorecard-<cand>-vs-<base>.json` / `.md` | `threadlight-router-scorecard/v1` | bench |
| `router-validation.json` / `.md` | `threadlight-router-validation/v1` | validate |
| `router-workload.json` / `.md` (+ `-records.jsonl`) | `threadlight-router-workload/v1` | workload |

## Layout

```
skills/threadlight-router-bench/
  SKILL.md
  scripts/
    router_bench.py     # CLI dispatcher: learn (primary) + bench + validate + workload + pool
    harvest.py          # gh run metadata, phase parity, leg manifests, --log-failed
    findings.py         # ordered taxonomy, message-scoped, category dedup
    report.py           # learnings/v1 digest + Markdown
    prices.py           # seed $/1M table, Retail Prices API fetch/parse, routing fee
    metrics.py          # az monitor metrics fetch + served-model attribution
    workload.py         # router vs fixed arms on the Responses API, 429-aware
    workload_tasks.py   # deterministic task mix + graders
    pool.py             # ARM models API router pool pre-flight
    score.py            # cost rollup + counterfactual scorecard + validation_scorecard
    matrix.py           # 3-arm × N-workload matrix orchestrator, 2-wave dispatch
    rubric.py           # keyword-rubric scorer over PoC artifacts (phases/rubric/meta)
  references/fixtures/  # real-log, az-metrics, retail-price, recorded-workload, pool fixtures
  references/findings/  # dated evidence logs (latest supersedes earlier)
  tests/                # pytest, stdlib-only
# Workload packs (phases, rubric, meta) live at:
#   .github/workloads/<name>/{phases,rubric,meta}.yml
```
