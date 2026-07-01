# Worked example — router validation matrix (2026-06-30)

**Question (from the field):** model-router is a nice efficiency story, but is it
actually *better* than a standard `gpt-5.4-mini` — fewer rounds, fewer
hallucinations, lower cost-to-quality — and does a router constrained to
`{gpt-5.4, gpt-5.4-mini}` recover quality when a workload gets hard? We
**validated** with a controlled 6-cell matrix run through the full e2e CI.

> ⚠️ **n=1 per cell.** One CI run per cell. Treat every number here as
> *directional*, not statistically significant. Cells near a threshold
> (rubric≈0.8, rounds≈1.5×) are inconclusive by construction.

## Design

3 arms × 2 workloads, each dispatched through `threadlight-e2e-foundry.yml`
(`mode=full`, `teardown=true`), 2 serialized waves so Azure Monitor token
attribution (keyed only by `ModelDeploymentName`) stays clean.

| arm | deployment | wire API | intent |
|-----|------------|----------|--------|
| `mini` | `gpt-5.4-mini` | responses | the cheap standard baseline |
| `router` | `model-router` → pinned `{gpt-5.4, gpt-5.4-mini}` | completions | escalate hard turns |
| `strong` | `gpt-5.4` (cap 100) | responses | quality ceiling |

Workloads: `returns-triage` (simple) and `fsi-kyc-aml` (complex). The shared
model-router was pinned to the 2-model subset for the run window and **restored
to the full pool on exit** via a trap (verified live).

## What happened — phase reached × cost

The e2e has 8 phases: `install → smoke → design → pattern (local-test) →
deploy → invoke → legs → teardown`. **No cell completed end-to-end.** Where each
cell died is the signal:

| arm | workload | last phase OK | died at | rounds | cost (USD) |
|-----|----------|---------------|---------|-------:|-----------:|
| mini | returns-triage | smoke | **design** (§12 assert) | 32 | $0.13 |
| router | returns-triage | pattern | **deploy** (gpt-5.4 429) | 378 | **$34.34** |
| strong | returns-triage | pattern | **deploy** (gpt-5.4 429) | 112 | $7.66 |
| mini | fsi-kyc-aml | smoke | **design** (§12 assert) | 36 | $0.19 |
| router | fsi-kyc-aml | smoke | **design** (§12 assert) | 19 | $1.94 |
| strong | fsi-kyc-aml | pattern | **deploy** (gpt-5.4 429) | 245 | $13.78 |

Two failure planes, with completely different meaning:

### Plane 1 — design `§12` conformance (the one clean model-quality signal)

The e2e asserts that `threadlight-design (≥1.7.0)` emits a `## 12.` **Assumptions**
section carrying a Fast-PoC silent-defaults callout (`Fast-PoC`, `not collected`,
`neutral/demo defaults`). Result:

| arm | returns-triage | fsi-kyc-aml |
|-----|----------------|-------------|
| mini | ❌ wrote `## 12. Production Readiness` | ❌ same |
| router | ✅ correct §12 callout | ❌ same wrong §12 |
| strong | ✅ correct | ✅ correct |

- **mini fails the §12 contract on *both* workloads** — even the simple one. It
  renumbered the SPEC sections (put "Production Readiness" at §12), so the
  Assumptions/Fast-PoC callout the harness requires never landed at §12. A real,
  reproducible instruction-following miss under an identical prompt.
- **router beats mini on the simple workload** (it got §12 right) but **does NOT
  close the gap on the complex one** — it fell back to mini-like behavior and
  missed §12 too.
- **strong is the only arm that clears design on both.**

### Plane 2 — deploy is contaminated by a `gpt-5.4` cap bottleneck (NOT a quality signal)

Every arm that *reached* deploy died there — but the cause is infra, not the
model's reasoning:

- **strong** (both workloads): `CAPIError: Your requests to gpt-5.4 ... have
  exceeded rate limit`. The strong-arm `gpt-5.4` deployment is **GlobalStandard
  cap 100** — too small for the deploy phase's tool-call burst. `learn` flagged
  **34× `[high] model_unavailable`** ("transient API error. Retrying…") + a
  `rate_limit` on the strong/returns-triage run.
- **router/returns-triage**: also a `rate_limit` (429) finding. The model-router
  **escalates hard deploy turns to gpt-5.4**, so it hit the **same cap-100
  ceiling**. Its 30-min timeout and **$34 / 378-round** blow-up is largely a
  429 **retry storm** — the escalation made the *failure* ~4× more expensive
  than the strong arm's own failure.

So deploy can't differentiate the arms until the gpt-5.4 cap is raised: both
`strong` and `router` are bottlenecked on the same undersized deployment.

## Answers to the original questions

- **Is router "better" than `gpt-5.4-mini`?** *Partially, and only on quality —
  not (yet) on cost.* On the **design** gate it strictly beat mini on the simple
  workload, but it could **not** recover quality on the complex one. On **cost**
  it carries real downside risk: when the agent goes off-path, router escalation
  to gpt-5.4 turns a cheap failure into an expensive one ($34 vs mini's $0.13).
  mini "keeps up" only in the sense of failing **fast and cheap** at design.
- **Does mini keep up on the simple workload?** **No** — it failed the §12
  design contract even on `returns-triage`. The original hypothesis ("mini is
  fine on tiny, struggles on complex") was *too generous to mini*: mini missed
  the design contract on **both**.
- **Did the matrix prove the router's end-to-end value?** **Inconclusive** — the
  deploy bottleneck blocked every arm before the `invoke` phase, so there is no
  clean end-to-end quality/cost comparison yet.

> The automated `validation_scorecard` marks **all** cells `falls-behind`
> (`phases_ok=False` everywhere) and its `router_verdict` ("mixed" / "closes-the
> -gap") is a **degenerate artifact** — "closes-the-gap" on fsi only means router
> failed *cheaply/early* at design while strong failed *expensively* at deploy.
> **Do not quote the auto-verdict as a quality win.** The signal is the
> phase-reached × cost table above plus the `learn` digests.

## Cold-path validation (`learn` on real runs)

`router-bench learn <run_id>` (the self-improvement cold-path) was run on three
cells and behaved correctly:

- `strong/returns-triage` → `[high] model_unavailable ×34`, `[medium] rate_limit`,
  `[low] retry` ("deploy Attempt 1 failed after 905s") — pinpoints the cap-100 storm.
- `router/returns-triage` → `[medium] rate_limit` (429s via gpt-5.4 escalation).
- `mini/fsi-kyc-aml` → **0 findings, "looks clean"** — correct: its failure was a
  design *assert* (quality), not an infra/model error, so it's outside the infra
  taxonomy. (Quality misses are caught by the e2e assert, not by `learn`.)

## Actionable improvements (the self-improving loop)

1. **Raise the `gpt-5.4` deployment cap** (100 → ≥250) in `scripts/ci/foundry-strong-arm.sh`.
   It bottlenecks both the `strong` arm and the `router` arm (escalation target).
   Until then, no end-to-end deploy comparison is possible.
2. **Fix deploy-phase agent confusion.** Every arm that reached deploy spent its
   rounds *hunting for / re-scaffolding* `azure.yaml` / `agent.yaml` / `infra/`
   instead of running a clean `azd up` ("the repo only has design artifacts, so
   I'm locating the generator/templates…"). Either pre-seed the deploy scaffold
   in the workload pack or sharpen the `threadlight-deploy` skill's "artifacts
   already exist — just provision" contract. This burned the router's $34.
3. **Anchor the §12 template for weaker models.** `gpt-5.4-mini` renumbers SPEC
   sections and drops the Fast-PoC §12 callout. If mini is in the routing pool,
   the `threadlight-design` prompt needs a harder §12 anchor (explicit heading +
   required phrases) so the cheap model conforms.
4. **Re-run after (1)+(2)** to get a clean `invoke`-phase quality/cost verdict.
   Until then, this matrix validates the *method* and surfaces the *blockers* —
   it does not yet crown a winner.

## Reproduce

```bash
# 1. infra + pin (restores shared router on exit via trap)
bash scripts/ci/foundry-strong-arm.sh
bash scripts/ci/router-subset.sh record && bash scripts/ci/router-subset.sh set
# 2. dispatch the 6-cell matrix (2 serialized waves, ~1.5–2h)
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --dispatch --workloads returns-triage fsi-kyc-aml \
  --ref <branch> --repo aiappsgbb/threadlight-skills --out <outdir>
bash scripts/ci/router-subset.sh restore        # always
# 3. score + cold-path
RID="/subscriptions/<sub>/resourceGroups/rg-shared-gbb-ci/providers/Microsoft.CognitiveServices/accounts/aif-shared-gbb-ci"
python3 skills/threadlight-router-bench/scripts/router_bench.py validate \
  --ingest <outdir>/matrix-manifest.json --resource "$RID" --out <outdir>
python3 skills/threadlight-router-bench/scripts/router_bench.py learn <run_id> \
  --repo aiappsgbb/threadlight-skills --deployment <deployment> --out <outdir>/learn-<arm>
```

**Run IDs (2026-06-30):** returns-triage mini=28455528786 router=28455539255
strong=28455549408 · fsi-kyc-aml mini=28458233210 router=28458244350
strong=28458248682.

---

# Matrix 2 — after the fixes (2026-07-01)

The three matrix-1 blockers were fixed and the matrix re-run on the same
`{mini, router{5.4,5.4-mini}, strong}` × `{returns-triage, fsi-kyc-aml}` grid:

1. `gpt-5.4` deployment cap 100 → 250 (`scripts/ci/foundry-strong-arm.sh`).
2. Deploy **anti-hunt** contract in both packs (let `threadlight-deploy` generate
   `azure.yaml`/`infra/`; stop hand-scaffolding).
3. The real skill bug: SPEC **§12 = Production Readiness**, **§13 = Assumptions &
   Open Questions** (Fast-PoC callout). The CI assert + design prompts were flipped
   §12 → §13 to match the canonical `speckit-template`.

**The fixes worked.** The failure surface moved a *long* way downstream — from
matrix-1's Phase 1/3 (design §12 gate + deploy 429 wall) all the way to **Phase 4
(invoke)**. Crucially, **the design gate is now GREEN for every arm** (mini
included): the §12→§13 fix erased matrix-1's mini design miss. Deploy's 429 wall
is also gone.

## What happened — phase reached × cost (matrix 2)

| arm | workload | last phase OK | died at | rounds | cost (USD) |
|-----|----------|---------------|---------|-------:|-----------:|
| mini | returns-triage | pattern/deploy | **invoke** (timeout, no clean invoke) | 273 | $2.21 |
| router | returns-triage | **deploy** | **invoke** (protocol 400) | **142** | $12.05 |
| strong | returns-triage | **deploy** | **invoke** (protocol 400) | 232 | $23.85 |
| mini | fsi-kyc-aml | pattern | **deploy** (missing skill → retry-loop timeout) | 174 | $1.77 |
| router | fsi-kyc-aml | **deploy** | **invoke** (protocol 400) | 174 | $12.56 |
| strong | fsi-kyc-aml | **deploy** | **invoke** (protocol 400) | 187 | $15.15 |

No cell completed end-to-end, but **two brand-new, non-quality blockers** now cap
everyone — and the *way* each arm meets them is the signal.

### Blocker A — missing `threadlight-workflow` skill (Phase 3, complex only)

`fsi-kyc-aml` correctly classifies as `workflow_model: "workflow"` (§11e), so
`threadlight-deploy` Phase 2 delegates container generation to a
**`threadlight-workflow` skill that does not exist in the repo**
(`threadlight-deploy/SKILL.md` L127, L500-504 reference it; it was never authored).
Every arm hits it:

```
✗ skill(threadlight-workflow) Skill not found: threadlight-workflow
```

strong even narrates it — *"the spec declares `workflow_model: workflow`, so I'm
loading the workflow-specific skill"* → `Skill not found`. **The differentiator is
recovery:** strong and router improvise the workflow container by hand and reach
Phase 4; **mini cannot recover** — it falls into the deploy retry loop
(mis-resolving `FOUNDRY_PROJECT_ENDPOINT`) and times out a **full phase earlier**
at Phase 3. This is the cleanest capability signal in the whole exercise: an
*identical* missing-dependency obstacle is a recoverable bump for router/strong and
a terminal failure for mini.

### Blocker B — Phase-4 protocol contract mismatch (invoke, all workloads)

The Phase-4 pack prompt hardcodes *"use the Invocations protocol"*, but
`threadlight-deploy` may legitimately scaffold a **Responses**-protocol runtime
(MAF / workflow orchestrator — see deploy SKILL.md L280 protocol table). So
`azd ai agent invoke --protocol invocations` returns
`HTTP 400: Invoke API requires ... 'invocations' protocol version '2.0.0'`. router
and strong made **clean** invoke attempts, then **correctly refused to fabricate**
a transcript (anti-fabrication guard) → the "invoke-results.md" assert fails.
**Even strong cannot pass** — this is a harness contract bug, not a model failure.

## Quality read (matrix 2)

- **Design: now GREEN for all arms.** The §12→§13 fix closed mini's only clean
  matrix-1 quality miss. mini no longer fails design on either workload.
- **Complex-workload recovery = the headline signal.** Same Phase-3 missing-skill
  wall: **router tracks strong** (both improvise → Phase 4); **mini falls behind a
  full phase** (can't recover → Phase-3 timeout). Router recovers quality under
  complexity exactly as intended.
- **Simple workload:** all three reach the same Phase-4 protocol wall, but **mini
  burns the MOST rounds (273) hunting `SERVICE_MCP_FQDN`** and still never makes a
  clean invoke; **router uses the FEWEST (142)**. Router is more efficient even
  where all arms fail at the same wall.
- **Cost:** mini is cheapest in raw dollars but it's a **false economy** — it's
  cheap *because* it gives up / times out (and on the simple workload it burned the
  most rounds yet still failed). Router reaches strong's phase at **~half strong's
  cost on simple ($12 vs $24)** and **~17 % cheaper on complex ($12.6 vs $15.2)**.

> ⚠️ **Degenerate verdict, again.** All 6 cells are `phases_ok=False` → rubric
> `0.00`, per-arm verdict `falls-behind`, `router_verdict` `closes-the-gap`. These
> are **meaningless artifacts** of the shared Phase-4 wall. Do **not** quote them.
> The real signals are (a) phase-reached depth, (b) rounds/cost, (c) `learn`.

## Cold-path (`learn`) — the self-improving loop earned its keep

Run on the two most instructive red cells, **single-run, no baseline** (the whole
point — self-improve from *any* run, not forced pairs):

- `learn 28509910952` (fsi mini) → **`[medium] skill_loader:
  Skill not found: threadlight-workflow`**. The cold-path **found Blocker A from
  one red run** — a genuine, actionable harness gap. ✅ This is the self-improvement
  win the field asked for.
- `learn 28507036414` (rt router) → **0 findings, "looks clean"** — *wrong*: the
  invoke genuinely failed on Blocker B. The classifier has **no `protocol_contract`
  rule**, so it can't see the HTTP-400 protocol mismatch. A concrete
  **self-improvement TODO for the tool itself**.

**Calibration note:** `skill_loader` was graded `[medium]`, but on a
workflow-classified workload it is universal and blocking → should be `[high]`.

## Actionable improvements (matrix 2)

1. **Author/vendor the `threadlight-workflow` skill**, OR make `threadlight-deploy`'s
   `workflow_model: "workflow"` path resilient (inline the workflow-container
   generation / graceful fallback with guidance). Unblocks Phase 3 for **every arm**
   on complex/workflow workloads. Highest value.
2. **Fix the Phase-4 protocol contract.** Phase 4 should **probe the declared
   protocol** (read `azure.yaml` / the runtime deploy chose) instead of hardcoding
   Invocations — or force the GHCP-SDK/Invocations runtime for these packs. Unblocks
   `invoke` for all arms.
3. **Teach `learn` the protocol-contract failure mode.** Add a `protocol_contract`
   anomaly rule (match `HTTP 400` + `invocations protocol version` /
   `responses protocol not declared`) and bump missing-skill (`skill_loader`) to
   `[high]`. Directly improves the self-improving cold-path.
4. **Re-run after (1)+(2)** for the first clean rubric-scored `invoke`-phase verdict.

**Run IDs (2026-07-01):** returns-triage mini=28507031234 router=28507036414
strong=28507041703 · fsi-kyc-aml mini=28509910952 router=28509920177
strong=28509928583.
