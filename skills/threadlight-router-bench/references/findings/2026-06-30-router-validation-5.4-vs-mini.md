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
