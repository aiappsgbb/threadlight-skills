# Router validation — methodology & findings

> How we validate that a **model-router constrained to `{gpt-5.4, gpt-5.4-mini}`**
> actually beats a **pure `gpt-5.4-mini`** baseline — on **quality AND cost** — by
> running a controlled matrix through the real
> [`threadlight-e2e-foundry`](./threadlight-e2e.md) pipeline, and how we mine **every
> run** for its own improvements via a self-improving cold-path.
>
> The **optional** efficiency companion to the primary
> [self-improving cold-path](./self-improving-loop.md); both are surfaced from the
> CX-facing [Self-improving CI chapter](../self-improving.html). The canonical,
> continuously-updated evidence log lives in the skill:
> [`router-bench/references/findings/2026-06-30-router-validation-5.4-vs-mini.md`](../../skills/threadlight-router-bench/references/findings/2026-06-30-router-validation-5.4-vs-mini.md).

## The question (from the field)

Model-router is a nice efficiency story to tell a customer — *route each turn to the
smallest model that can do it*. But two things have to be true before we put it on a
slide:

1. **Is it actually better than a standard `gpt-5.4-mini`?** Fewer rounds, fewer
   stalls, lower cost-to-quality — and does a router **recover quality when the
   workload gets hard**?
2. **Can we measure quality *and* estimated token cost**, against a fixed cheap baseline and a
   fixed quality ceiling — in the same CI that ships the product, not a benchmark
   harness?

And a standing requirement from the field: a way to **self-improve from any single
run**, without being forced to run matched pairs every time. That requirement is the
**primary** path and has its own doc — [the self-improving cold-path
(`learn`)](./self-improving-loop.md); everything below is the **optional** efficiency
proof (`bench`/`validate`).

## Experiment design

Three model arms × two workloads, each dispatched end-to-end through
`threadlight-e2e-foundry.yml` (`mode=full`, `teardown=true`), in **two serialized
waves** to reduce overlapping Azure Monitor token attribution (keyed only by
`ModelDeploymentName`). Serialization does not exclude unrelated traffic.

| arm | deployment | wire API | intent |
|-----|------------|----------|--------|
| `mini` | `gpt-5.4-mini` | responses | the cheap standard baseline |
| `router` | `model-router` → pinned `{gpt-5.4, gpt-5.4-mini}` | completions | escalate only hard turns |
| `strong` | `gpt-5.4` (cap ≥250) | responses | the quality ceiling |

**Workloads:** `returns-triage` (simple — a sanity floor) and `fsi-kyc-aml` (complex —
a multi-persona KYC/AML agent, where a cheap model is *supposed* to break down).

Two deliberate constraints:

- The router is pinned to **`{gpt-5.4, gpt-5.4-mini}`** and **excludes the premium
  `gpt-5.5` tier**. The point is efficiency, not maximalism — show a disciplined
  two-model ladder gets the job done.
- The router is pinned for the run window. Restoration is a **separate operator
  step**, not guaranteed cleanup: `router-subset.sh` has **no automatic trap**.
  Neither `router_bench.py` nor `matrix.py` owns rollback. Use the recovery
  sequence below on success, failure, interruption or cancellation.

**Scored on three axes a customer cares about:**

- **Did it finish?** — the pipeline's own quality gates (design conforms → deploys →
  live agent answers).
- **How much work?** — number of agent rounds.
- **What did the tokens cost, approximately?** — **estimated token cost**:
  Azure Monitor token counts multiplied by the catalog's seed prices, scoped
  by `ModelDeploymentName` and run window. This is not a billing export.

> ⚠️ **n=1 per cell.** One CI run per cell. Every number is **directional**, not
> statistically significant. Cells near a threshold are inconclusive by construction.
> Historical dollar figures below are retained as **estimates**, not invoiced
> spend. Run approval and fresh evidence are required before another matrix.

## The three-matrix arc

The value isn't a single leaderboard — it's the **loop**: run the matrix, let it fail
informatively, mine the logs, fix the blocker, re-run. Each matrix pushed the failure
surface further downstream.

### Matrix 1 (2026-06-30) — surfaced the harness blockers

Two failure planes, only one of which is a model signal:

| plane | what | signal? |
|---|---|---|
| **design `§12` conformance** | harness asserted the Fast-PoC callout at `## 12.`; `mini` renumbered SPEC sections and missed it on **both** workloads; `router` got it right on simple, missed on complex; `strong` cleared both | ✅ clean model-quality signal |
| **deploy `gpt-5.4` 429 wall** | every arm that reached deploy died on a `gpt-5.4` GlobalStandard **cap-100** rate limit; `router` escalation turned a cheap failure into a **$34 / 378-round** retry storm | ❌ infra artifact, not quality |

**Read:** `mini` failed the design contract even on the simple workload (the "mini is
fine on tiny" hypothesis was too generous). But the deploy cap blocked every arm before
`invoke`, so no end-to-end verdict was possible. Matrix 1 validated the **method** and
surfaced the **blockers**.

**Fixes shipped** (`ebef15d`, prose reconciliation `a7862a4`):

1. `gpt-5.4` deployment cap **100 → 250** (`scripts/ci/foundry-strong-arm.sh`) — it
   bottlenecked both `strong` and the `router`'s escalation target.
2. **Deploy anti-hunt** contract in both packs — agents were re-scaffolding
   `azure.yaml`/`infra/` by hand instead of letting `threadlight-deploy` generate them.
3. The real skill bug: SPEC **§12 = Production Readiness**, **§13 = Assumptions & Open
   Questions** (the Fast-PoC callout). The CI assert + design prompts were flipped
   §12 → §13 to match the canonical `speckit-template` — the weak model had actually
   been following the template correctly.

### Matrix 2 (2026-07-01) — the fixes worked; two new blockers

The failure surface moved a long way downstream — from Phase 1/3 all the way to
**Phase 4 (invoke)** — and **the design gate went GREEN for every arm** (mini
included). Deploy's 429 wall is gone.

| arm | workload | last phase OK | died at | rounds | estimated cost (USD) |
|-----|----------|---------------|---------|-------:|-----------:|
| mini | returns-triage | pattern/deploy | **invoke** (timeout, no clean invoke) | 273 | $2.21 |
| router | returns-triage | **deploy** | **invoke** (protocol 400) | **142** | $12.05 |
| strong | returns-triage | **deploy** | **invoke** (protocol 400) | 232 | $23.85 |
| mini | fsi-kyc-aml | pattern | **deploy** (missing skill → retry-loop timeout) | 174 | $1.77 |
| router | fsi-kyc-aml | **deploy** | **invoke** (protocol 400) | 174 | $12.56 |
| strong | fsi-kyc-aml | **deploy** | **invoke** (protocol 400) | 187 | $15.15 |

Two brand-new, **non-quality** blockers now cap everyone — and *how* each arm meets
them is the signal:

- **Blocker A — missing `threadlight-workflow` skill** (Phase 3, complex only).
  `fsi-kyc-aml` classifies as `workflow_model: "workflow"`, so `threadlight-deploy`
  delegated container generation to a `threadlight-workflow` skill that **was never
  authored** (`Skill not found`). The differentiator is **recovery**: `router` and
  `strong` improvise the container by hand and reach Phase 4; **`mini` cannot recover**
  — it times out a full phase earlier. This is the cleanest capability signal in the
  whole exercise: an *identical* missing-dependency obstacle is a recoverable bump for
  router/strong and a terminal failure for mini.
- **Blocker B — Phase-4 protocol contract mismatch** (invoke, all workloads). The pack
  prompt hardcoded *"use the Invocations protocol"*, but `threadlight-deploy` may
  legitimately scaffold a **Responses**-protocol runtime, so the invoke returned
  `HTTP 400: Invoke API requires ... 'invocations' protocol version '2.0.0'`. **Even
  `strong` cannot pass** — a harness contract bug, not a model failure.

**Quality read (directional):**

- **Design: GREEN for all arms** — the §12→§13 fix erased mini's only clean matrix-1
  miss.
- **Complex-workload recovery is the headline** — same Phase-3 wall: **router tracks
  strong** (both improvise → Phase 4); **mini falls behind a full phase**.
- **Simple workload:** all three hit the same Phase-4 wall, but **mini burns the most
  rounds (273)** and still never makes a clean invoke; **router uses the fewest (142)**.
- **Cost:** mini is cheapest in raw dollars but it's a **false economy** — it's cheap
  *because* it gives up / times out. Router reaches strong's phase at **≈ half strong's
  cost on simple** ($12.05 vs $23.85) and **≈ 17 % cheaper on complex** ($12.56 vs
  $15.15).

**Fixes shipped** (`87b0c5b`):

1. **Blocker A** — `threadlight-deploy`'s `workflow_model: "workflow"` path now
   **prefers** `threadlight-workflow` if installed, else deterministically **falls back
   to the Phase-2 agent-container path** (removes the dangling dependency + mini's
   retry loop) while preserving the workload's domain complexity.
2. **Blocker B** — both pack Phase-4 prompts now **probe the declared protocol** (read
   `azure.yaml` / `azd ai agent show`) and retry on a protocol-version 400, instead of
   hardcoding Invocations.
3. **Cold-path** — taught `learn` a new `protocol_contract` rule and bumped the
   missing-skill rule `medium → high` (see below).

### Matrix 3 (landed 2026-07-01) — an environmental wall, diagnosed by the loop

Matrix 3 did **not** produce the clean green verdict. 5 of 6 cells failed the
`invoke` phase and **all six scored rubric `0.00`** — the same degenerate pattern as
matrices 1–2. Pointed at the failures, `learn` classified the root cause
deterministically: **`model_unavailable`** — `● Request failed due to a transient API
error. Retrying...` — **×37** on the returns-triage `mini` arm alone. This is an
**environmental transient-API storm on the shared Foundry account during the run
window**, not a router or workload regression: the blocker-A/B fixes held, and the
cost *shape* stayed consistent with matrix 2 (router ≈ strong-tier spend, both well
above mini). The verdict therefore remains **directional (matrix 2)**; a clean green
needs a run window when the shared account isn't throttling.

| workload | arm | phases | rounds | rubric | estimated cost (USD) |
|---|---|---|---|---|---|
| returns-triage | mini | FAIL | 204 | 0.00 | $2.47 |
| returns-triage | router | FAIL | 137 | 0.00 | $17.01 |
| returns-triage | strong | FAIL | 210 | 0.00 | $24.94 |
| fsi-kyc-aml | mini | pass | 172 | 0.00 | $1.35 |
| fsi-kyc-aml | router | FAIL | 205 | 0.00 | $17.51 |
| fsi-kyc-aml | strong | FAIL | 137 | 0.00 | $14.31 |

The loop turned a confusing 5/6-red matrix into a one-line root cause — which is
exactly the point of the cold-path.

> ⚠️ **Degenerate auto-verdict caveat.** When every cell is `phases_ok=False` (as in
> matrices 1–3, capped by a shared downstream wall — a rate-limit cascade in 1–2, a
> transient-API / `model_unavailable` storm in 3), the `validation_scorecard` marks
> all cells `falls-behind` → rubric `0.00` and emits a `router_verdict` like
> `closes-the-gap`. Those are **meaningless artifacts** of the shared wall. **Do not
> quote them.** The real signals are (a) phase-reached depth, (b) rounds/cost, (c) the
> `learn` digests — until a matrix runs fully green.

## The self-improving cold-path (`learn`)

The standing field requirement was: **self-improve from *any* single run**, no matched
pairs. `router-bench learn <run_id>` does exactly that — it reads one run's
`gh run view --log-failed`, strips the `<job>\t<step>\t<ISO>` prefix + ANSI + command
echo, and classifies anomalies with an ordered, first-match-wins ruleset, emitting a
**ranked list of concrete findings** (JSON + Markdown).

It earned its keep in matrix 2:

- `learn 28509910952` (fsi mini) → **`[high] skill_loader: Skill not found:
  threadlight-workflow`** — **found Blocker A from one red run.** ✅ The exact
  self-improvement win the field asked for.
- `learn 28507036414` (rt router) → **0 findings, "looks clean"** — *wrong*: the invoke
  genuinely failed on Blocker B. The `HTTP 400 ... 'invocations' protocol version`
  string has no "unsupported" token, so it slipped past the `wire_protocol` rule. A
  concrete **self-improvement TODO for the tool itself**.

Both were fixed in `87b0c5b`:

- Added a **`protocol_contract` (`high`)** rule after `wire_protocol` matching
  `Invoke API requires` / `(invocations|responses) protocol` / `protocol version '`.
- Bumped **`skill_loader` `medium → high`** — on a workflow-classified workload a
  missing skill is universal and blocking.
- +2 regression tests (suite 51 → 53 green).

Note the healthy division of labour: **quality misses are caught by the e2e asserts;
`learn` catches infra/harness anomalies.** A design-contract miss correctly returns 0
findings from `learn` — it's outside the infra taxonomy, and the pipeline's own gate
already fails it.

## Reproduce

**Approval first.** Dispatch, capacity changes and router mutation are paid/live
operations. Obtain approval for the exact subscription/account/deployment, model
pool, capacity, run window, budget and restoration owner. The catalog
[`router-subset.sh`](../../scripts/ci/router-subset.sh) and
[`foundry-strong-arm.sh`](../../scripts/ci/foundry-strong-arm.sh) have **hardcoded
targets**, not target-selection flags. Do not run them against their embedded
account or assume environment variables override it. An operator must prepare
and review target-specific copies for the approved target. The commands below
use that reviewed helper, not the catalog's embedded target.

1. Serialize all writers, including other pipelines/operators. Review the helper's
   model version, SKU and capacity constants: `record` saves only routing, not the
   entire deployment. Any strong-arm capacity uplift is separately approved and
   has its own recovery plan; router restore cannot undo it.
2. Run `record` **before** `set`, inspect and preserve
   `/tmp/model-router-routing.snapshot.json` privately outside volatile `/tmp`.
   Record the full before-state separately. Do not overwrite the snapshot with
   another `record` after mutation. Only continue to `set` after record succeeds.
3. Dispatch and wait for all cells to finish. After `set` allow propagation (up
   to about five minutes) and independently read back the approved pool.
4. Run `restore` as a **separate recovery action** after all outcomes, including
   cancellation/timeout. Do not rely on the last line of a shell recipe running.

```bash
ROUTER_HELPER=/absolute/path/to/operator-reviewed/router-subset.sh
bash "$ROUTER_HELPER" record
# STOP unless the record succeeded and the approved before-state was retained.
bash "$ROUTER_HELPER" set

# Approved paid dispatch; replace the placeholders before use.
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --dispatch --workloads returns-triage fsi-kyc-aml \
  --ref <branch> --repo aiappsgbb/threadlight-skills --out <outdir>

# Separate operator recovery, even if dispatch failed or was interrupted:
bash "$ROUTER_HELPER" restore

# Ingest Azure Monitor tokens and estimate their cost; not billing.
RID="/subscriptions/<approved-subscription>/resourceGroups/<approved-rg>/providers/Microsoft.CognitiveServices/accounts/<approved-account>"
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --ingest <outdir>/matrix-manifest.json --resource "$RID" --out <outdir>
python3 skills/threadlight-router-bench/scripts/router_bench.py learn <run_id> \
  --repo aiappsgbb/threadlight-skills --deployment <deployment> --out <outdir>/learn-<arm>
```

**Recovery verification:** the helper rejects unsuccessful HTTP calls and reads
back routing, but checks routing-block **presence or absence, not full equality**
with a non-null snapshot. The operator must compare all routing fields and
model/SKU/capacity against the saved before-state. There is no ETag protection;
concurrent writers can be overwritten. A missing snapshot makes the helper warn
and assume no routing block/full pool—it cannot reconstruct the prior state.
Stop and reconcile with the owner before using that fallback. If credentials
expire, restore fails, the runner is lost or `/tmp` disappears, retain the failure,
recover the approved snapshot under the authorized identity, and verify the live
state explicitly. Do not report cleanup complete just because dispatch ended.

## Run IDs

| matrix | returns-triage (mini / router / strong) | fsi-kyc-aml (mini / router / strong) |
|---|---|---|
| 1 (2026-06-30) | 28455528786 / 28455539255 / 28455549408 | 28458233210 / 28458244350 / 28458248682 |
| 2 (2026-07-01) | 28507031234 / 28507036414 / 28507041703 | 28509910952 / 28509920177 / 28509928583 |
| 3 (2026-07-01) | 28516165448 / 28516175050 / 28516184062 | 28519520937 / 28519525080 / 28519535675 |

## Limitations & honesty

- **n=1 per cell** — directional only; we keep re-running.
- Matrices 1–2 never ran fully green, so there is **no rubric-scored quality verdict
  yet** — the evidence is phase depth + rounds/cost + `learn`, not the auto-scorecard.
- `_score_cell` uses Azure Monitor token metrics × `load_prices(None)`, hence
  [`SEED_PRICES`](../../skills/threadlight-router-bench/scripts/prices.py),
  not Azure Cost Management or invoice data. The current seed has no independent
  version: retain the **catalog commit**, price-table bytes, metric window, model
  dimensions and run IDs with each estimate. The path reviewed for this guide is
  catalog `8153bc2e0a677d99b8414053d7a00cfdab495444`; it is not asserted to be the
  price-table provenance of every historical run above.
- Serialization reduces overlap but cannot exclude **unrelated traffic** using
  the same deployment. Metric delay, model attribution and seed-rate differences
  limit precision. These figures omit resource charges, negotiated discounts and
  other billing adjustments. The `validate` path does not use a custom price
  file; do not assume another subcommand's `--prices` option changes it.
- The router's cost *downside* is real: when the agent goes off-path, escalation to
  `gpt-5.4` makes a *failure* more expensive than the cheap arm's failure (matrix 1's
  $34 storm). The anti-hunt + protocol fixes exist partly to keep the agent on-path.

## Cross-refs

- [Self-improving CI chapter](../self-improving.html) — the CX-facing narrative (loop first, this efficiency proof second).
- [The self-improving cold-path](./self-improving-loop.md) — the **primary** `learn` methodology this page is a companion to.
- [`threadlight-e2e-foundry.yml` operator runbook](./threadlight-e2e.md) — the pipeline
  this matrix drives.
- [`router-bench` findings log](../../skills/threadlight-router-bench/references/findings/2026-06-30-router-validation-5.4-vs-mini.md)
  — the canonical, per-matrix evidence log (source of truth for these numbers).
- `skills/threadlight-router-bench/scripts/findings.py` — the `learn` anomaly
  classifier (the self-improving cold-path).
- `skills/threadlight-deploy/SKILL.md` — Blocker A fix (workflow-model fallback) +
  the Invocations-vs-Responses protocol table behind Blocker B.
