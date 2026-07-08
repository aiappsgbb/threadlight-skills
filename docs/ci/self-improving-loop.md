# The self-improving cold-path — learn from any CI run

> A finished CI run is a wasted asset if nobody reads it. The **self-improving
> cold-path** (`learn <run_id>`) turns **one** GitHub Actions run — green or red,
> **no matched-pair baseline** — into a grounded, deduplicated learnings digest:
> phase parity, a reality-tuned failure taxonomy, and ranked fixes mapped back to
> evidence. It is the primary offline leg for threadlight pilots driven by the
> GitHub Copilot CLI on Microsoft Foundry.
>
> Skill: [`threadlight-router-bench`](../../skills/threadlight-router-bench/SKILL.md).
> Companion (optional efficiency proof): [Router validation](./router-validation.md).
> Pipeline it reads: [`threadlight-e2e-foundry`](./threadlight-e2e.md).

## Why it exists

Two needs collided in the field:

1. **Every run carries learnings — even a green one.** Re-reading raw Actions logs
   by hand is slow, noisy, and forgettable. The signal (a dependency drift, a
   rate-limit wall, a wire-protocol mismatch) is buried under echoed shell source,
   prompt text, and the workflow's own grep-based error detectors.
2. **You should never be forced to run *pairs* of jobs just to learn something.**
   A/B cost benchmarking needs a baseline run; *learning what to fix* does not. The
   cold-path deliberately works on a **single** run so a retro is one command, not a
   re-dispatch.

```
… → BUILD/DEPLOY → e2e CI run → [ learn <run_id> ] → learnings digest + ranked fixes
```

## Three modes (learn is primary)

| Mode | Command | Needs a baseline? | Output |
|---|---|---|---|
| **learn** (primary) | `learn <run_id>` | **No** — any single run | `learnings-<run_id>.json` + `.md` |
| bench (optional) | `bench <cand> <base>` | Yes — paired runs | `scorecard-<cand>-vs-<base>.{json,md}` |
| validate (optional) | `validate --dispatch` / `--ingest` | No — runs its own 3-arm matrix | `router-validation.{json,md}` |

`bench` and `validate` are the heavier, customer-facing **efficiency** paths and are
documented separately in [Router validation](./router-validation.md). This page is
about `learn` — the loop the team actually asked for.

## How the loop works

### 1. Deterministic harvest (no LLM)

`learn` reads the run with `gh` and derives structure before any model is involved:

```bash
python skills/threadlight-router-bench/scripts/router_bench.py learn <RUN_ID> \
  --repo aiappsgbb/threadlight-skills \
  --out router-bench-out \
  --deployment model-router        # label only, for the digest header
```

1. `gh run view <id> --json …` → conclusion, branch, title, and the **metric
   window** (`startedAt`→`updatedAt`).
2. `gh run view <id> --json jobs` → **phase parity**: the worst step conclusion per
   phase across smoke / design / pattern / deploy / invoke / legs / teardown.
3. **Logs with precision:** failing runs are scoped with `gh run view <id>
   --log-failed` (failing steps only); green runs use the full `--log` but are
   scanned **warnings-only**.
4. Classify anomalies into the fixed taxonomy, **dedup by category** with a `count`
   and the first evidence line, and write the `threadlight-router-learnings/v1`
   digest (JSON + Markdown).

### 2. Precision rules (learned against real runs — do not soften)

These rules are the hard-won part. They are what keep green runs clean and red runs
actionable:

- **Source scope, not full logs.** A naive full-`--log` scan of the **green** run
  `28437323962` produced **10/10 false positives** — echoed shell source, prompt
  text, and the workflow's own grep-based error detectors. Scoping failures to
  `--log-failed` gave **100% precision** on the real failures `28435017341`
  (rate-limit cascade) and `28389162228` (agent-framework 1.4 dependency drift).
- **Classify the message, not the line.** Only the message column is classified; the
  `<job>\t<step>` prefix is stripped first, so a step *named* `threadlight-deploy +
  azd up` can't manufacture a phantom `deploy` finding.
- **Drop command-echo.** Lines carrying the `[36;1m` ANSI token (GitHub's cyan-bold
  "Run" echo) and `##[group]`/`##[command]` control lines are noise — never
  classified.
- **Green runs are warnings-only.** A successful run emits **no** high/medium
  findings; at most low-severity `retry` / `slow_turn` / `router_fallback` surface.
- **Keep the copilot glyphs.** Do **not** strip `● │ └` — in failing-step scope
  `● Request failed due to a transient API error` is a real `model_unavailable`
  signal.

### 3. The failure taxonomy (ordered; first match wins)

```
dependency (high) → skill_loader (high) → wire_protocol (high) →
rate_limit (med) → model_unavailable (high) → auth (high) →
quota (med) → deploy (high) → tool_failure (med) →
router_fallback (low) → retry (low) → slow_turn (low)
```

Order matters: `rate_limit` precedes `model_unavailable` so `exceeded rate limit`
classifies as a rate limit, while `transient API error` classifies as
model-unavailable.

### 4. The single LLM step: digest → recommendations

Only **after** the deterministic digest is written does a model get involved — and it
reads **`learnings-<run_id>.md`, never the raw logs**:

> For each finding in the digest, name the most likely root cause and the smallest
> durable fix (pin/constraint, retry/backoff, capacity/quota change, wire-API switch,
> RBAC). Map every recommendation back to a finding `id`. If the run is green with
> zero findings, say so and surface at most the low-sev warnings (slow turns /
> retries) worth watching. **Do not invent issues that are not in the digest.**

Anything reusable — a new taxonomy signature, a recurring root cause — is captured as
a follow-up, so the cold-path keeps improving across runs.

## What it caught (and taught itself)

The loop earned its keep on this very exercise. Pointed at the failing matrix runs,
it turned logs into fixes with **no guesswork and no re-running pairs**:

- **A missing skill dependency** (a `skill_loader` finding) that was quietly stalling
  the hard workload before the agent could reach its later phases.
- **A protocol-contract gap** (a `wire_protocol` finding) surfaced by a Phase-4
  protocol probe added to the workload pack.

Three fixes shipped straight from those digests. And the taxonomy itself got sharper:

- Added a **`protocol_contract`** classification rule so wire-contract mismatches are
  caught by category, not by chance.
- Bumped **`skill_loader`** from medium to **high** severity — a missing skill is a
  hard blocker, not a warning.
- The skill's own test suite grew **51 → 53** to lock the new behavior in.

That is the compounding property: the loop doesn't just report this run, it improves
how it reads the *next* one.

## Reproduce

```bash
# Any single run — green or red, no baseline:
python skills/threadlight-router-bench/scripts/router_bench.py learn <RUN_ID> \
  --repo aiappsgbb/threadlight-skills --out router-bench-out
# → router-bench-out/learnings-<RUN_ID>.json + .md
```

`learn` and `bench` are **read-only** (they only *read* finished runs); only
`validate` mutates live infra. Stdlib-only Python; the skill's tests run under the
repo's `python-pytest.yml`.

## Reference runs

| Run | Kind | What `learn` produced |
|---|---|---|
| `28437323962` | green | warnings-only, **zero** high/medium findings (precision floor) |
| `28435017341` | red | `rate_limit` cascade — capacity cap on `gpt-5.4-mini` |
| `28389162228` | red | `dependency` — agent-framework 1.4 drift |

## Limitations

- **The digest is deterministic; the recommendations are not.** The classifier is
  fixed-taxonomy and evidence-scoped, but the root-cause → fix mapping is the one LLM
  step. It is constrained to the digest, but treat its fixes as proposals to verify.
- **Taxonomy is closed by design.** New failure shapes are *added deliberately* (as a
  new rule + tests), not inferred at runtime — that's what keeps precision high.
- **`learn` reads; it never provisions.** Teardown stays forced in the e2e workflow;
  this cold-path touches no Azure resources.

## Cross-refs

- [`threadlight-router-bench` skill](../../skills/threadlight-router-bench/SKILL.md) — the cold-path implementation (learn + bench + validate).
- [Router validation](./router-validation.md) — the **optional** efficiency proof (model-router vs `gpt-5.4-mini`, quality + cost).
- [`threadlight-e2e-foundry` runbook](./threadlight-e2e.md) — the pipeline whose runs the loop reads.
- Canonical evidence log: [`router-bench/references/findings/2026-06-30-router-validation-5.4-vs-mini.md`](../../skills/threadlight-router-bench/references/findings/2026-06-30-router-validation-5.4-vs-mini.md).
