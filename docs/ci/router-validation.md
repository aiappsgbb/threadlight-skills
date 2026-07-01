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
2. **Can we measure quality *and* real cost**, against a fixed cheap baseline and a
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
waves** so Azure Monitor token attribution (keyed only by `ModelDeploymentName`) stays
clean.

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
- The shared `model-router` deployment is pinned for the run window and
  **restored to the full pool on exit via a trap** (verified live every run), so the
  experiment never leaves the shared account mutated.

**Scored on three axes a customer cares about:**

- **Did it finish?** — the pipeline's own quality gates (design conforms → deploys →
  live agent answers).
- **How much work?** — number of agent rounds.
- **What did it cost?** — **real billed Azure spend**, read back from the Cognitive
  Services account after the run (`ModelDeploymentName`-scoped).

> ⚠️ **n=1 per cell.** One CI run per cell. Every number is **directional**, not
> statistically significant. Cells near a threshold are inconclusive by construction.
> We run the matrix repeatedly and fix what it surfaces between runs.

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

| arm | workload | last phase OK | died at | rounds | cost (USD) |
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

### Matrix 3 (in flight) — the clean verdict

With all three blockers fixed, matrix 3 is dispatched to get the **first fully-green,
rubric-scored `invoke`-phase verdict**. This section is filled in when it lands; until
then, treat the router's advantage as **directional (matrix 2)**, not a crowned winner.

> ⚠️ **Degenerate auto-verdict caveat.** When every cell is `phases_ok=False` (as in
> matrices 1–2, capped by a shared downstream wall), the `validation_scorecard` marks
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

```bash
# 1. infra + pin (restores the shared router on exit via trap)
bash scripts/ci/foundry-strong-arm.sh                       # gpt-5.4 cap >=250
bash scripts/ci/router-subset.sh record && bash scripts/ci/router-subset.sh set

# 2. dispatch the 6-cell matrix (2 serialized waves, ~1.5-2h)
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --dispatch --workloads returns-triage fsi-kyc-aml \
  --ref <branch> --repo aiappsgbb/threadlight-skills --out <outdir>
bash scripts/ci/router-subset.sh restore                    # always

# 3. score (reads real Azure billing) + cold-path on any run
RID="/subscriptions/<sub>/resourceGroups/rg-shared-gbb-ci/providers/Microsoft.CognitiveServices/accounts/aif-shared-gbb-ci"
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --ingest <outdir>/matrix-manifest.json --resource "$RID" --out <outdir>
python3 skills/threadlight-router-bench/scripts/router_bench.py learn <run_id> \
  --repo aiappsgbb/threadlight-skills --deployment <deployment> --out <outdir>/learn-<arm>
```

## Run IDs

| matrix | returns-triage (mini / router / strong) | fsi-kyc-aml (mini / router / strong) |
|---|---|---|
| 1 (2026-06-30) | 28455528786 / 28455539255 / 28455549408 | 28458233210 / 28458244350 / 28458248682 |
| 2 (2026-07-01) | 28507031234 / 28507036414 / 28507041703 | 28509910952 / 28509920177 / 28509928583 |
| 3 (in flight)  | *filled on completion* | *filled on completion* |

## Limitations & honesty

- **n=1 per cell** — directional only; we keep re-running.
- Matrices 1–2 never ran fully green, so there is **no rubric-scored quality verdict
  yet** — the evidence is phase depth + rounds/cost + `learn`, not the auto-scorecard.
- Cost is **billed Azure spend** attributed by `ModelDeploymentName` on a shared
  account; serialized waves keep attribution clean but it is not a per-request meter.
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
