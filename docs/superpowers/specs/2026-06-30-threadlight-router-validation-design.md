# Threadlight Router Validation — Design Spec

**Date:** 2026-06-30
**Status:** Approved (brainstorming) → ready for implementation plan
**Owner:** ricchi
**Related:** PR #60 (`threadlight-router-bench` skill), `docs/superpowers/specs/2026-06-30-threadlight-router-bench-design.md`

---

## 1. Problem & Goal

The first head-to-head (model-router run `28437323962` vs gpt-5.4-mini run
`28435673875`) showed that on the **simple** retail returns-triage workload, pure
gpt-5.4-mini matched model-router's green outcome while being **2.3× faster** and
**~23× cheaper** — model-router was the *wrong* efficiency pitch there. But that
was a single, easy workload, and the managed router's pool included gpt-5.5
(premium) and **never** routed to mini.

**Hypothesis to validate:** *In a tiny/controlled workload, gpt-5.4-mini keeps up;
as the workload gets more demanding, mini falls behind (more rounds, lower build
quality, or outright failure), and a router constrained to {gpt-5.4, gpt-5.4-mini}
recovers quality by escalating the hard turns to 5.4 — at lower cost than running
5.4 for everything.*

**Goal:** Run a controlled **6-run matrix** (3 model arms × 2 workloads of
increasing complexity) through the full CI e2e, and produce a **composite
scorecard** that issues a per-workload verdict on whether mini keeps up and
whether the router is worth it.

This is a **directional probe** (n=1 per cell), not a statistical power study. The
matrix is designed to be repeatable (×N) later if a cell is close.

---

## 2. Scope

**In scope:**
- Parameterize the existing `threadlight-e2e-foundry.yml` with a `workload` input
  (default `returns-triage`, preserving today's gate behavior).
- A new **complex workload pack** (`fsi-kyc-aml`) with phase prompts, gates, and a
  quality rubric.
- One-time infra: a `gpt-5.4` deployment + a `model-router` custom subset
  constrained to {gpt-5.4, gpt-5.4-mini}.
- A **matrix orchestrator** that dispatches the 6 runs in cost-clean waves.
- Extensions to the shipped `threadlight-router-bench` skill: rounds extraction,
  a quality-rubric scorer, a validation scorecard, a markdown report, and a
  `validate` subcommand.

**Out of scope:**
- Changing the *deployed PoC agent's* model (we stress the **Copilot CLI driver**
  model `COPILOT_PROVIDER_MODEL_ID`, which does the design→build work — that's
  where rounds and build quality live).
- Statistical rigor / repeated trials (future work).
- Tearing down the live model-router deployment (deferred by user).

---

## 3. The Matrix

3 arms × 2 workloads, all `mode=full`, `teardown=true`:

| arm | `model_deployment` | `wire_api` | role |
|---|---|---|---|
| **mini** | `gpt-5.4-mini` | `responses` | the cheap candidate |
| **router** | `model-router` (subset = {gpt-5.4, gpt-5.4-mini}) | `completions` | the efficiency bet |
| **strong** | `gpt-5.4` | `responses` | quality ceiling |

× workloads:
- **returns-triage** — simple retail Fast-PoC (today's CI scenario).
- **fsi-kyc-aml** — regulated KYC/AML onboarding (materially harder; see §5).

Wire-API rationale (empirically established in prior work): model-router's
Responses v1 route returns HTTP 400 "operation unsupported", so the router arm
must use `completions`; the GPT-5-series direct deployments use `responses`.

---

## 4. Composite Scorecard — 4 Axes

Per run, captured by the extended `threadlight-router-bench` skill:

1. **Outcome** — phase parity: do all 8 phases pass
   (install/smoke/design/pattern/deploy/invoke/legs/teardown)? Source:
   `harvest.parse_phase_parity(jobs_doc)` (already shipped).
2. **Rounds-to-done** — total `●` agent-step markers across the phase logs
   (`phase-design.log`, `phase-local-test.log`, `phase-deploy.log`,
   `phase-invoke.log`) **plus** retry-attempt count (the
   `Copilot CLI attempt N of 3` loop). Headline effort signal. A weak model that
   thrashes emits more `●` steps and/or triggers retries.
   - *Empirical baseline* (run 28437323962): design 30 / local-test 5 / deploy
     136 / invoke 62 `●` steps.
   - *Richer option:* upload `/tmp/copilot-logs` as an artifact for exact
     turn counts (`assistant.turn_start` events). Safe, additive workflow change.
3. **Quality rubric** — per-workload hard-point coverage scored 0–1 against the
   built artifacts (SPEC.md, AGENTS.md, sample-data, killer-prompts). See §5.
4. **Cost** — Azure Monitor tokens × price per arm, via the shipped
   `metrics.py` + `score.py` (`InputTokens`/`OutputTokens`, filtered by
   `ModelDeploymentName`, windowed to the run's started→updated interval).

### Verdict logic (explicit thresholds)

- An arm **"keeps up"** on a workload iff **all** of:
  - all phases green, **AND**
  - rubric ≥ **0.8**, **AND**
  - rounds ≤ **1.5×** the strong arm's rounds.
- **"Falls behind"** if **any** of: a phase fails, rubric < 0.8, or rounds > 1.5×
  strong.
- **Router verdict:**
  - **"closes the gap"** if router rubric ≈ strong (within 0.1) **AND** router
    cost < strong cost.
  - **"not worth it"** if router cost ≈ strong (within 20%) but router rubric ≈
    mini (within 0.1).
- **Headline per workload**, e.g.:
  *"simple → mini keeps up (rubric 0.9, 1.1× rounds, ~23× cheaper);
  complex → mini falls behind (rubric 0.6, misses GDPR/tipping-off, 1.8× rounds);
  router recovers rubric 0.85 at 0.4× strong cost."*

---

## 5. Workload Packs

Location: `.github/workloads/<name>/` (CI-owned, repo-relative — one source of
truth for both the workflow and the skill's rubric scorer).

Each pack contains:
- `phases.yml` — per-phase `id → { prompt, gates[] }`. The returns-triage pack is
  populated by **extracting today's inline prompts unchanged** (no behavior
  change). The fsi-kyc-aml pack is authored new from
  `skills/threadlight-design/references/domains/fsi-kyc-aml.md`.
- `rubric.yml` — hard-point checks (id, description, match strategy, weight).
- `meta.yml` — difficulty label, expected hard-points (documentation).

The workflow's phase helper is parameterized by the `workload` input and loads
prompts + gates from the selected pack.

### fsi-kyc-aml rubric hard-points (the "be more demanding" core)

These are reasoning traps a weak model is likely to paper over with plausible
boilerplate — i.e., the "hallucinated completeness" signal:

| id | hard-point | why it's hard |
|---|---|---|
| `retention-tension` | SPEC addresses **AML 5–7yr retention vs GDPR erasure** | requires resolving a genuine regulatory conflict, not templating |
| `beneficial-ownership` | data model encodes **≥25% beneficial owners (10% in some EU states)**, incl. nested entity-owns-entity | recursive modelling + jurisdiction nuance |
| `tipping-off` | SAR flow encodes the **do-not-notify-customer** guardrail | easy to omit; a correctness/safety constraint |
| `structuring-ctr` | **CTR $10K** threshold + **structuring** (split-transaction) detection | aggregation logic, not a single field |
| `edd-approval` | **EDD escalation** with a senior-approval gate (HITL state) | a stateful approval workflow |
| `multi-jurisdiction` | US (BSA/FinCEN) vs EU (AMLD) **different thresholds** | the agent must not hard-code one regime |

`returns-triage` rubric is a low bar that mirrors the existing CI gates (SPEC
mentions returns, sample-data present, ≥2 killer-prompts) so both workloads score
on the same 0–1 scale.

---

## 6. One-Time Infra (prerequisites)

Scripted and documented; idempotent where possible.

1. **`gpt-5.4` deployment** on `aif-shared-gbb-ci` (`rg-shared-gbb-ci`):
   - Quota-check first (premium model; capacity may be constrained, like the
     mini cap of 500 in swedencentral). If quota denies, surface clearly and mark
     the **strong arm unavailable** (the mini-vs-router comparison still runs).
2. **`model-router` custom subset** constrained to {gpt-5.4, gpt-5.4-mini}:
   - The deployed router is version `2025-11-18`, which **supports custom
     subsets** ("specify which underlying models to include in routing
     decisions"). Exact API/portal mechanism resolved in the implementation plan.
   - **Restore the prior subset after the matrix completes** — it is a shared
     deployment that other CI may use.
3. **RBAC:** Cognitive Services Contributor (create deployments / edit subset) +
   Monitoring Reader (already held for cost metrics).

---

## 7. Orchestration — `scripts/matrix.py`

Dispatches the 6 runs in **2 waves** to keep Azure Monitor cost windows clean.

**Key insight:** metrics filter by `ModelDeploymentName`, and each arm is a
*distinct* deployment (mini / gpt-5.4 / model-router), each with its own capacity
and its own metric stream. So the 3 arms can run **concurrently** without metric
bleed — but **never 2 runs on the same deployment at once**.

- **Wave 1:** {mini, router, strong} on `returns-triage`.
- **Wave 2:** {mini, router, strong} on `fsi-kyc-aml`.
- ~2 × ~40min ≈ **1.5h** (vs ~4h fully serial).

The orchestrator:
- dispatches each arm via `gh workflow run threadlight-e2e-foundry.yml` with the
  arm's inputs;
- resolves run IDs (dispatch returns none → poll `gh run list`);
- polls each wave to completion;
- records run IDs + started/updated windows + arm/workload labels into a
  `matrix-manifest.json`.

A `--serial` flag forces fully-serial execution; a `--ingest <manifest>` mode
skips dispatch and scores an existing manifest (so we can re-score without
re-running).

---

## 8. Skill Extensions (TDD)

Extend the shipped `threadlight-router-bench` skill (keep its 30 tests green):

| module | change |
|---|---|
| `scripts/harvest.py` | add `count_rounds(phase_log_paths) → {phase: steps, attempts}`; sum = rounds-to-done. |
| `scripts/rubric.py` *(new)* | `load_rubric(path)`, `score_rubric(artifacts_dir, rubric) → {score, checks[]}`. Match strategies: `contains`, `regex`, `all_of`, `any_of` over the built artifacts. |
| `scripts/score.py` | add `validation_scorecard(arms, workload) → threadlight-router-validation/v1` combining outcome+rounds+rubric+cost + verdict per §4. |
| `scripts/report.py` | `render_validation_matrix(scorecards) → markdown` (workload × arm × axes + headline verdicts). |
| `scripts/router_bench.py` | add `validate` subcommand: `--dispatch` (run the matrix) or `--ingest <manifest>` (score existing runs) → scorecard + report. |
| `.github/workflows/threadlight-e2e-foundry.yml` | add `workload` input (default `returns-triage`); phase helper loads the selected pack; upload `/tmp/copilot-logs` artifact. |
| `SKILL.md` | document the `validate` mode + the matrix + verdict thresholds. |

### Data flow

```
matrix.py (dispatch, 2 waves)
   → 6 runs (gh workflow run threadlight-e2e-foundry.yml)
   → matrix-manifest.json (run IDs + windows + labels)
harvest.py  → per run: phase parity, rounds (● + attempts), artifacts
rubric.py   → per run: quality 0–1 + per-check
metrics+score → per run: cost; per workload: validation_scorecard (3-arm compare + verdict)
report.py   → markdown matrix + headline verdicts
```

---

## 9. Testing (TDD)

Keep the existing 30 router-bench tests green. Add:
- `test_rubric.py` — fixture SPEC.md passing all hard-points → 1.0; fixture
  missing `tipping-off` + `retention-tension` → lower score with those checks
  failing.
- `test_rounds.py` — fixture phase log with N `●` lines + an attempt header →
  `count_rounds` returns N steps + attempts.
- `test_validation_score.py` — synthetic 3-arm inputs exercising each verdict
  branch (keeps-up, falls-behind-on-rubric, falls-behind-on-rounds,
  router-closes-gap, router-not-worth-it).
- `test_matrix.py` — injected runner verifies wave grouping (no 2 same-deployment
  concurrent), dispatch sequence, and manifest shape.

---

## 10. Error Handling & Guardrails

- **Same-deployment concurrency guard:** `matrix.py` never schedules 2 concurrent
  runs on the same deployment (enforced by wave construction).
- **gpt-5.4 quota failure:** surface clearly; mark strong arm `unavailable`;
  continue mini-vs-router.
- **Shared router subset:** record the prior subset before mutating; **restore on
  completion** (and on abort).
- **Existing gate safety:** `workload` defaults to `returns-triage`; the PR /
  scheduled triggers are unchanged; returns-triage prompts are extracted
  verbatim.
- **Cost attribution caveat:** windows are per-deployment; waves keep each
  deployment single-tenant for the run. Documented as a known limitation
  (no run-id dimension in Azure Monitor).
- **n=1 caveat:** the scorecard labels itself a directional probe; close cells
  (verdict margin within thresholds) are flagged "re-run recommended".

---

## 11. Risks

- gpt-5.4 quota/capacity in-region (premium model).
- Mutating the shared `model-router` subset affects other consumers — mitigated
  by record-and-restore + running deliberately.
- ~6 full-e2e runs: ~$6–25 in tokens + ~$3–6 Azure infra + ~1.5h wall-clock.
- Router-subset config API surface may differ portal vs CLI — resolved in plan.

---

## 12. Success Criteria

- All 6 runs complete (or strong arm cleanly marked unavailable on quota).
- A committed `threadlight-router-validation/v1` scorecard + markdown matrix with
  a defensible per-workload verdict.
- The headline answer to the user's question: *does mini keep up as complexity
  rises, and is the 5.4+5.4-mini router worth it?* — grounded in real run data,
  with explicit caveats.
- Existing CI gate (returns-triage) demonstrably unchanged (default path green).
