---
name: threadlight-auto
description: >
  Full-auto driver for the Threadlight pilot pipeline. One freeform prompt
  ("Build me an auto-claim triage agent for Contoso Mutual") drives
  threadlight-design → (optional) threadlight-local-test → threadlight-deploy
  → threadlight-safe-check → live invoke → (optional, advisory)
  threadlight-production-ready. Auto-continues at every gate;
  HARD STOPS on tenant assertion failure or quota exhaustion. Resumes from
  `.threadlight/auto-state.json`. Smart-recovers from quota, RBAC race, and
  ImagePull deploy failures. Cost projection always runs; reconciled Azure
  cost actuals are an opt-in, advisory subphase. Wraps existing threadlight-* skills.
  USE FOR: full-auto pilot drive, one-prompt threadlight, resume failed deploy,
  demo-in-one-session, autopilot, threadlight orchestrator, start from Kratos export.
  DO NOT USE FOR: per-stage control (use threadlight-design / -deploy /
  -safe-check directly), production CI/CD, single-stage iteration.
metadata:
  version: "1.2.0"
---

# `threadlight-auto` — Full-auto Threadlight driver

## Purpose

Replace the manual chain `threadlight-design → threadlight-deploy → threadlight-safe-check → invoke`
with a single invocation. Designed for:

- **First-timers** who don't yet know which skill fires when
- **Demos** where the whole arc has to complete in one Copilot session
- **Resumption** when a deploy failed and the operator wants to retry without re-doing earlier stages
- **Pilots-from-templates** where the operator just wants to pick `auto-claim-triage` /
  `credit-memo` / `prior-auth-healthcare` and have the system fill in the boilerplate

SEs who already know the per-skill chain should keep invoking those directly —
`threadlight-auto` is a wrapper, not a replacement.

> **Design.** The orchestrator pattern, smart-recovery table, and HARD-STOP
> gates are the load-bearing reliability contract of this skill. Threadlight's
> stage labels + artifact paths are canonical: the design stage emits
> `specs/SPEC.md` + `specs/manifest.json`, and every downstream stage keys off
> their hashes.

## Position in the SKILL hierarchy

```
                   ┌─────────────────────────────────┐
                   │  threadlight-auto (THIS SKILL)  │   ← single entry point
                   │  • parses input prompt          │
                   │  • runs orchestrator.py         │
                   │  • drives sub-skills in         │
                   │    sequence with smart          │
                   │    recovery + resumption        │
                   └────────────┬────────────────────┘
                                │ via Skill tool
       ┌─────────────────┬──────┴────────┬──────────────────┐
       │                 │               │                  │
       ▼                 ▼               ▼                  ▼
threadlight-      threadlight-     threadlight-       threadlight-
design            local-test       deploy             safe-check
(SKILL)           (OPTIONAL)       (SKILL — runs      (gates phase=
                                    azd up)            post-deploy)
       │                 │               │                  │
       └────────┬────────┴───────────────┴──────────────────┘
                │ each stage benefits from the deploy-time
                │ failure-mode index F-01..F-22 in
                │ threadlight-deploy/SKILL.md
                ▼
       ┌──────────────────────────────────────────┐
       │ awesome-gbb companion SKILLs             │
       │ (foundry-hosted-agents, azd-patterns,    │
       │  foundry-observability, …)               │
       └──────────────────────────────────────────┘
```

> **Legs auto does _not_ drive.** Two production-handoff steps
> (**`threadlight-cicd`**, **`threadlight-customize`**) and the offline
> **`threadlight-router-bench`** *Improve* leg run outside this orchestrator.
> `threadlight-auto` is a pilot driver — after a CI run finishes, reach for
> `threadlight-router-bench` to harvest a grounded learnings digest (failure
> taxonomy + recommendations) and, optionally, a model-router cost/quality
> scorecard. It never drives prod-pipeline, customer-onboarding, or offline
> self-improvement legs.

## Input parsing

`threadlight-auto` accepts two input shapes; freeform is the default.

### Freeform (default)

A single natural-language prompt. Examples:

- `"Build me an auto-claim triage agent for Contoso Mutual in acme"`
- `"Run threadlight-auto with the credit-memo scenario, customer=Contoso Financial, env=dev"`
- `"Scaffold a prior-auth pilot for Northwind Health, tenant=acme, region=westus3"`

Parsing rules:
- **Scenario template** — look for `with the <name> scenario` or `scenario=<name>` (default: `auto-claim-triage`)
- **Customer name** — look for `for <Name>` or `customer=<Name>` (default: derived from scenario)
- **Tenant alias** — look for `tenant=<alias>` or `in <alias>`, default to `~/.azure-tenants/index.json` `default_alias`
- **AZD env** — look for `env=<name>`, default `dev`
- **Region** — look for `region=<name>` or `in <region>`, default `westus3` (auto-fallback to `eastus2` → `northcentralus` on quota fail)
- **Workspace dir** — derived from `<customer-slug>-<scenario>`, written to `~/Repos/<slug>/` if not specified

### Structured (power-user override)

```
Use threadlight-auto with:
  scenario: auto-claim-triage
  customer: Contoso Mutual
  tenant: acme
  env: dev
  region: westus3
  workspace: ~/Repos/contoso-claim-triage
```

### Kratos-export entry path (start from an exported bundle)

`threadlight-auto` has a **second entry path** alongside the freeform/structured
"from-scratch" flow above: starting from a **Kratos-exported project**. It is
selected automatically when the workspace already contains a Kratos export
(`src/hosted-agent/` **and** `use-cases/<x>/` — see
[`docs/KRATOS-BRIDGE.md`](../../docs/KRATOS-BRIDGE.md)), or explicitly:

```
Use threadlight-auto with:
  mode: kratos-export
  workspace: ~/Repos/wealth-management-agent   # unzipped <use-case>-foundry-agent.zip
  tenant: acme
  env: wealth-management-prod
```

In this mode the orchestrator **does not run Design (stage 1)** and **does not
regenerate runtime files in Deploy (stage 3)** — Kratos already shipped a
deployable project. The chain becomes:

```
Stage 0 Preflight
  → (deploy: enrich/validate only + backfill evals/ — threadlight-deploy Kratos-export mode)
  → azd up (if not already deployed)
  → Safe-check (post-deploy, accepts trimmed infra)
  → Cost-projection (discover from export infra/)
  → Invoke
  → Production-ready (advisory; trimmed infra = informational)
```

The skills root resolves to `use-cases/<x>/skills/` for every stage. Optional
extension skills (`threadlight-hitl-patterns`, `threadlight-event-triggers`,
`threadlight-workspace-ui`, `citadel-spoke-onboarding`) run on demand, writing
next to the existing use-case skills.

## Stage 0 — Preflight

**Always runs** as the bootstrap preflight. Checks:

1. Tenant + subscription match `~/.azure-tenants/index.json` for the alias (azure-tenant-isolation rule 4a)
2. Tool versions: `az ≥ 2.86`, `azd ≥ 1.25.4`, `bicep ≥ 0.43`, `uv ≥ 0.7`, `node ≥ 22`, `python ≥ 3.12`
3. `azd ai agent` extension installed in the alias's `AZD_CONFIG_DIR`
4. The `../threadlight-design/references/runtime-policy.json` dependency must
   **always be readable**. If `threadlight-design` is not installed/enabled,
   **HARD STOP** and tell the operator to install or enable it — never fall
   back to a remembered default. Selector validation then depends on the input
   path:
   - **Resume / hand-crafted path (complete foundation):** when `specs/foundation.md` exists,
     validate the record using the **same complete-foundation rules as Deploy
     Runtime-policy pre-flight step 5**. The tuple must be compatible; a
     concrete `policy_route` must exactly match its declared selectors and be
     the first matching route for the current signals; and
     `explicit-supported-choice` requires operator provenance
     `source: provided`, no active `blocked_when` signals, and an empty
     `unresolved_signals` list (`requires_resolved_signals`). If Foundation
     exists but SPEC § 11e is **not yet present** because Design stopped between
     those writes, **do not hard-stop or reject** the resume: validate the
     Foundation internally using `runtime_shape` plus its capability signals,
     and **defer only the mirror cross-check** until Design resumes or Deploy
     runs after SPEC exists. A **legacy
     foundation.md** that is missing `protocol`,
     `policy_route`, or `capability_signals` is not complete: do not reject or
     default it in Stage 0; **defer migration and final validation to Stage 3
     Deploy** (Runtime-policy pre-flight steps 2–3).
   - **Greenfield path:** `specs/foundation.md` does not exist yet; Stage 1
     Design creates it. Do not hard-stop in Stage 0. **Defer** selector
     resolution to Design and enforce validation again at Stage 3 Deploy.
   - **Kratos-export mode:** **skip foundation/selector validation** because
     the exported runtime is preserved verbatim and deploy Phase 2 is skipped;
     only the unconditional policy-dependency readability check above applies.
   `threadlight-auto` owns **no separate framework or protocol default**.
5. Writes `.threadlight/preflight-passed.json` with
   `foundation_sha256: <sha256>` when Foundation exists, or
   `foundation_sha256: null` before it exists. The marker has 24h maximum
   validity, but Foundation creation, editing, or removal invalidates it
   immediately.

> **🛑 HARD STOP #1 — Tenant assertion failure.** If tenant verification fails (wrong
> tenant or wrong subscription active), `threadlight-auto` STOPS IMMEDIATELY. No
> auto-recovery. Money is about to be spent in the wrong place — operator must
> fix isolation before retrying.

> **Runtime-policy contract.** `threadlight-design` and `threadlight-deploy`
> both inherit `../threadlight-design/references/runtime-policy.json`.
> `threadlight-auto` never invents a competing default. A missing/unavailable
> `threadlight-design` dependency is a Stage-0 hard stop; greenfield selector
> validation begins after Design creates the foundation, Kratos-export mode
> preserves its supplied runtime, and Deploy remains the final policy gate.
> Canonical default tuple: `github-copilot-sdk` + `agent` + `invocations` (`policy_route: default-agent`).

## Resumption — read `.threadlight/auto-state.json` first

`.threadlight/auto-state.json` is owned by the `threadlight-auto` guidance
contract. The Python planner (`references/orchestrator.py`) reads it (if
present) to compute which stages are already done; it does **not** write or
migrate that file. With `--commit`, the planner writes
`.threadlight/auto-next.json` for the coding agent instead. Stages are skipped
when ALL conditions hold:

| Stage | Skip when |
|---|---|
| Preflight | `.threadlight/preflight-passed.json` exists AND is `< 24 h` old AND its `foundation_sha256` matches the current Foundation (including `null` while absent) |
| Design | `specs/SPEC.md` exists AND `sha256(SPEC.md) == auto-state.json[design].artifact_hash` AND no `[NEEDS CLARIFICATION:` markers |
| Local-test | `specs/SPEC.md` exists AND `src/agent/main.py` runs locally (optional stage; skipped on freshness if SPEC unchanged) |
| Deploy | `azure.yaml` + `infra/main.bicep` exist AND `azd env get-values \| grep -q AGENT_FQDN` AND first-listed agent `status: active` via `azd ai agent show` |
| Safe-check | `docs/safe-check-post.md` exists AND `< 24 h` old AND `tests/postdeploy-manifest.json` is valid JSON with `phase=post-deploy` and `gaps=[]` |
| Cost-projection | SPEC § 12 `load_profile{}` is complete (all required keys filled, no `TBD` placeholders) AND `specs/cost-manifest.json.schema_version` starts with `1.` AND `generated_at > AZURE_LAST_DEPLOY_AT` (the planner trusts `specs/cost-manifest.json.generated_at` vs `AZURE_LAST_DEPLOY_AT`; `.threadlight/auto-state.json[cost_projection].passed_at` is recorded for audit/echo only and is not used as a skip gate) |
| Evals (Discover) | `specs/evals-manifest.json` has schema `threadlight-evals-manifest/v1`, a parseable `captured_at`, and a known verdict (`comprehensive` / `partial` / `offline-only` / `none`) captured `< 24 h` ago (re-runs when a fresh deploy/invoke cascades) |
| Red-team (Discover) | `specs/redteam-manifest.json` has schema `threadlight-redteam-manifest/v1`, a parseable `captured_at`, and a known verdict (`hardened` / `partial` / `vulnerable`) captured `< 24 h` ago |
| Govern (Protect) | `specs/govern-manifest.json` has schema `threadlight-govern-manifest/v2`, a parseable `captured_at`, and a known verdict (`governed` / `partial` / `ungoverned`) captured `< 24 h` ago |
| Sell (optional) | `docs/{seller-prep.md,demo-rehearsal.md}` exist |

If a stage's freshness check fails, that stage AND all downstream stages re-run
(Design change invalidates Deploy; Deploy change invalidates Safe-check; etc.).

The orchestrator's `--dry-run` mode prints the full skip/run decision tree without
invoking sub-skills.

## Sub-stages — what each one calls

Each stage invocation goes through the `Skill` tool. `threadlight-auto` reads each
sub-skill's closing report; if a report indicates failure, the smart-recovery table fires.

| # | Stage | Invokes via Skill tool | Closing report we parse |
|---|---|---|---|
| 1 | Design | `threadlight-design` | `specs/SPEC.md` + `specs/manifest.json` + `AGENTS.md` + `skills/*` + `docs/{demo-deck,prep-guide}` — **skipped in Kratos-export mode** (the bundle is already designed) |
| 2 | Local-test (OPTIONAL) | `threadlight-local-test` | `src/agent/main.py` runs via Pattern 0; smoke test passes |
| 3 | Deploy | `threadlight-deploy` | `infra/main.bicep` + `azure.yaml` + `src/agent/{main.py,container.py,Dockerfile,pyproject.toml}` + `.azure/<env>/` + `azd up` exits 0 + agent `status: active`. **In Kratos-export mode** `threadlight-deploy` runs enrich/validate only (no regen) + backfills `use-cases/<x>/evals/` |
| 4 | Safe-check (post-deploy) | `threadlight-safe-check` `phase=post-deploy` | `docs/safe-check-post.md` + `tests/postdeploy-manifest.json` (`phase=post-deploy`, `gaps=[]`) + behavioral gates green |
| 5 | Cost-projection (**new**, advisory) | `threadlight-consumption-iq` (`scripts/consumption_iq.py run --all`) | `docs/cost-projection.md` + `specs/cost-manifest.json`. Exit 4 (load profile incomplete) → sets `cost-projection: needs-wizard` in state, surfaces wizard prompt to operator; does NOT block chain. Exit 3 (pricing unavailable, no fixture) → sets `cost-projection: degraded-no-pricing`, warns, continues. Exit 2 (missing prereq, e.g. no SPEC) → same as other missing-prereq cases. Reconciled actuals are an opt-in subphase of this stage — see [§ Reconciled actuals](#cost-projection-stage--optional-reconciled-actuals-subphase-opt-in). |
| 6 | Invoke | direct `azd ai agent invoke` ×2 | Both demo scenarios from `specs/SPEC.md § Demo Scenarios` succeed |
| 7 | Evals — Discover (advisory) | `threadlight-evals` (`scripts/evals_check.py`) | `specs/evals-manifest.json` — offline batch (delegates to `foundry-evals`), Foundry Continuous Evaluation wiring on live threads, + A/B champion–challenger gate. Consumed by production-ready pillar 6 (EVAL-001..004). Advisory — degrades to `not-verified`, never blocks. |
| 8 | Red-team — Discover (advisory) | `threadlight-redteam` (`scripts/redteam_check.py`) | `docs/redteam-report.md` + `specs/redteam-manifest.json` — AI Red Teaming Agent adversarial scan (jailbreak / prompt-injection / exfiltration / harmful-content). Mapped to production-ready pillar 7 (SAFE-101..106). Advisory — never blocks. |
| 9 | Govern — Protect (advisory) | `threadlight-govern` (`scripts/govern_check.py`) | verifier report + `specs/govern-manifest.json` — wraps `foundry-agt`: policy artefact + in-process middleware at the container boundary. Consumed by production-ready pillar 2 (AGT-001..005) + pillar 7 (RAI-002/003). Advisory — never blocks. |
| 10 | Production-ready (OPTIONAL, advisory) | `threadlight-production-ready` (file-path CLI) | `docs/production-readiness-report.md` + `tests/production-readiness-manifest.json` — never blocks. Run when the customer asked for a paved-path / architecture-review artifact alongside the demo. Skip for pure throwaway demos. |
| 11 | Sell (OPTIONAL) | `threadlight-design` regenerates seller-prep | `docs/{seller-prep.md,demo-rehearsal.md}` |

### Cost-projection stage — optional reconciled-actuals subphase (opt-in)

`threadlight-consumption-iq` can also reconcile the **forecast** against
**observed Azure cost actuals** (`actuals` → `reconcile`, or the combined
`run --all --with-actuals`). In `threadlight-auto` that capability is an
**optional subphase of the existing `cost_projection` stage** — it is
**not a new stage**. `STAGES`, `.threadlight/auto-state.json`'s stage keys, and
the resumption table are unchanged; there is no `cost_actuals` stage to resume
from, and nothing about resumability keys off actuals.

**1 — Projection runs unchanged; a malformed actuals request cannot silently
take it down with it.** When the operator has not asked for actuals, Stage 5
always executes exactly `scripts/consumption_iq.py run --all` as it does
today, and always produces `docs/cost-projection.md` +
`specs/cost-manifest.json`; the projection's own exit semantics (2 / 3 / 4
above) are untouched. `consumption_iq.py`'s `_resolve_scope_or_exit` validates
every `--with-actuals` precondition — the `--pre-deploy` / `--pre-sales`
conflicts, `--start` / `--end`, `--subscription`, `--resource-group` —
**before it runs the projection at all**, so one malformed combined
invocation produces exit 2 for the whole process and writes no cost-projection artefacts
on that call. `threadlight-auto` never claims `--with-actuals`
simply "can't defer" the projection: it prevalidates every precondition
itself first (rule 3, below) so it never hands the CLI a combined command it
expects to fail; if a combined invocation still produces exit 2 despite that,
`threadlight-auto` immediately re-issues the plain `run --all` (no
`--with-actuals`) so the pilot is never left without a projection because of
an actuals-argument problem.

**2 — The default run never collects actuals.** On a **first-time** deploy —
and on every run where the operator did not ask for actuals — `threadlight-auto`
runs projection only. This is not caution for its own sake: Azure **Cost
Management** usage data refreshes on its own cadence (roughly every 4 hours,
daily granularity, and a window only settles after the fact), so a pilot
deployed minutes ago cannot have a mature window to reconcile against. The rule
is therefore mechanical: **do not poll**, do not sleep, do not retry in a
loop, and never hold Invoke behind billing ingestion. If numbers are not there
yet, the operator re-runs the subphase later — the chain moves on now.

**3 — Actuals run only on an explicit, scoped request, prevalidated before the
flag is ever appended.** Append `--with-actuals` only when **all** of these
hold on a **resumed, mature** pilot:

| Precondition | How it is satisfied |
|---|---|
| The operator explicitly asked for reconciled actuals on this run | Stated in the prompt (e.g. "reconcile last month's actuals"); never inferred from a deploy |
| A **settled** cost window | Operator supplies or approves `--start` / `--end` (`YYYY-MM-DD`, `--end` exclusive) over a closed period. **Never guess** dates and never widen a window on the operator's behalf |
| Scope | `--subscription` / `--resource-group` derived from the active `azd env` (`azd env get-values`) and asserted against `az account show` before the first query |
| Tenant isolation | Per-tenant `AZURE_CONFIG_DIR` / `AZD_CONFIG_DIR` active and the tenant + subscription assertion green (Stage 0 rules apply — a mismatch is the usual HARD STOP, not an actuals warning) |
| Optional evidence | `--monitor-resource-id` / `--workspace-resource-id` (full ARM resource IDs) only when the operator supplied them; absent, token/interaction rows degrade to `not-verified` and the run still proceeds |

`threadlight-auto` runs this table's checks **before** ever writing
`--with-actuals` onto the command line — this is the CLI-side prevalidation
rule 1 depends on. If any required argument is missing or unverifiable,
**skip the subphase** and issue the plain `run --all` for this run instead,
telling the operator exactly what to supply. Never assemble a partial or
guessed command; a malformed invocation both spends RBAC and money against the
wrong scope and, per rule 1, would block that call's own projection.

Read-only throughout — `Cost Management Reader`, `Monitoring Reader`,
`Log Analytics Reader`. Nothing is provisioned, mutated, or torn down.

**4 — An explicit actuals request still runs the subphase when the
projection stage itself is skipped.** The Resumption table's Cost-projection
skip rule decides only whether Stage 5 needs to **re-run**
`scripts/consumption_iq.py run --all` to refresh a stale
`specs/cost-manifest.json` — it says nothing about the actuals subphase, and
it must never be read as one. On a **resumed** run where `specs/cost-manifest.json.generated_at > AZURE_LAST_DEPLOY_AT` makes the orchestrator's `_check_cost_projection` return `skip`. `.threadlight/auto-state.json[cost_projection].passed_at` is recorded for auditing/echo purposes only and is not consulted by the planner to decide skip/run. If the operator has explicitly asked for actuals under rule 3, `threadlight-auto` still runs the actuals subphase as described below,
`threadlight-auto` still runs the subphase — it just does so with
`scripts/consumption_iq.py actuals ...` followed by
`scripts/consumption_iq.py reconcile` (both read-only, reusing the existing,
already-fresh `specs/cost-manifest.json` as `reconcile`'s default
`--forecast`) instead of `run --all --with-actuals`, because there is
nothing left to re-project. A skipped projection is never a reason to skip an
explicitly requested reconciliation, and an explicit reconciliation request
is never a reason to force a fresh forecast nobody asked for.

**5 — Exit semantics of the subphase (all advisory).** Every outcome is recorded
inside the existing `cost_projection` stage entry of
`.threadlight/auto-state.json` — no new stage key is introduced, and none of
these values change a skip/run decision on a later resume.

| Exit | Meaning | What `threadlight-auto` records | Chain |
|---|---|---|---|
| **exit 0** | Reconciliation verified | `cost-reconciliation: pass` | continue |
| **exit 3** | A cost source could not be collected/published | `cost-reconciliation: degraded-source` + the warning verbatim | continue |
| **exit 5** | Reconciliation is `not-verified` (typically an immature window) | `cost-reconciliation: not-verified` | continue |

Exit 5 is **advisory** and is always returned **after** every artefact is
**already written** (`specs/cost-actuals-manifest.json`,
`specs/cost-reconciliation-manifest.json`, the reconciliation report), so the
evidence needed to close the gap is on disk regardless of the verdict. In every
case the orchestrator continues to **Invoke → Evals → Red-team → Govern** and
the closing report carries the recorded status. A degraded or `not-verified`
reconciliation is a fact to report, never a reason to stop, retry, or wait.

**Exit 3 must be disambiguated by the exact stderr prefix, never assumed from
the exit code alone.** `run --all --with-actuals` is one process: an exit 3 it
returns can come from the **projection** step or from the **actuals /
reconcile** step, and the two are never conflated into the same state:

| stderr prefix (verbatim) | Step | State recorded |
|---|---|---|
| `pricing unavailable: ...` | Projection — Azure-pricing MCP unreachable, no fixture | `cost-projection: degraded-no-pricing` (Sub-stages row 5) |
| `cost evidence unavailable: ...` | Actuals — a mandatory Cost Management / Monitor / Log Analytics source could not be collected | `cost-reconciliation: degraded-source` |
| `cost evidence unusable: ...` | Actuals — evidence was collected but failed shape/consistency checks | `cost-reconciliation: degraded-source` |
| `token evidence unusable: ...` | Actuals — token/interaction metrics were collected but unusable | `cost-reconciliation: degraded-source` |
| `cost history conflict: ...` | Reconcile — the local cost-history snapshot disagrees with a newly observed value | `cost-reconciliation: degraded-source` |
| `artefact rejected before publication: ...` | Emit — an artefact failed validation before it was written | `cost-reconciliation: degraded-source` |
| `I/O failure: ...` | Either step | Read the surrounding output to tell which step failed; never default it to one bucket |

Every row except the first is a reconciliation-side failure and is recorded
under `cost-reconciliation`, never under `cost-projection` — by the time any
of them can fire, the projection has already succeeded and its own artefacts
are already on disk. The first row is the reverse: it is a projection-side
failure and is never recorded as a reconciliation status, even though it can
surface inside the same `run --all --with-actuals` invocation.

**6 — The API shape is proven, the run is still opt-in.** The Cost Management /
Monitor / Log Analytics parsing was probed read-only against a real isolated
demo subscription (`../threadlight-consumption-iq/references/live-actuals-probe.md`,
sanitized result in
`../threadlight-consumption-iq/references/fixtures/sample-cost-actuals/live-shape.json`),
so this path is not speculative. It stays opt-in for the reasons above — cost,
RBAC breadth, and pilot maturity — not because the shape is in doubt.

### Per-stage HARD STOPs (in addition to global tenant + quota)

| Stage | HARD STOP signature | Why no auto-recover |
|---|---|---|
| Preflight | `../threadlight-design/references/runtime-policy.json` unreadable (`threadlight-design` not installed/enabled) | Cross-skill contract missing — install/enable `threadlight-design`, never fall back to a remembered default |
| Design | `[NEEDS CLARIFICATION:` markers remain in `specs/SPEC.md` after design | Spec is ambiguous; agent should not guess on operator's behalf |
| Deploy | `az bicep build` exits non-zero with a real syntax error (not just warnings) | Bicep malformed; would fail in ARM validate anyway |
| Deploy | `az deployment sub validate` returns `ValidationError` other than `InsufficientQuota` | Resource shape / RBAC scope / API version error — needs operator review |

## Smart-recovery table — auto-retry these failures

These are the 3 most common deploy failures we see in from-scratch runs (also
covered in [`threadlight-deploy` § Deploy-time failure-mode index](../threadlight-deploy/SKILL.md#deploy-time-failure-mode-index-signature--action)).
`threadlight-auto` retries each ONCE, then HARD STOPs if recovery fails.

| Signature | Recovery | Retry limit | Logged to |
|---|---|---|---|
| `azd provision` → `InsufficientQuota for "gpt-5.4-mini"` (F-03) | Probe `westus3`, `eastus2`, `northcentralus` via `az cognitiveservices usage list`. Pick first with `currentValue < limit - 30`. Async-delete partial RG (`az group delete --no-wait`). Re-write `specs/SPEC.md § Region`. Set `azd env set AZURE_LOCATION <new>`. Retry `azd provision`. | 1 | `docs/auto-run.md` |
| `azd provision` → `[ImageError]` on ACA app first revision (F-05) | Wait 90 s (RBAC propagation). Verify ACA UAMI has AcrPull on ACR via `az role assignment list`. Retry `azd provision`. | 1 | `docs/auto-run.md` |
| `azd deploy <agent>` → `[ImageError] Failed to pull container image` (Foundry project MI side, F-06) | Pull project MI principal_id from Foundry account. Verify `AcrPull` on ACR. If missing, `az role assignment create`. Wait 60 s. Retry `azd deploy <agent>`. | 1 | `docs/auto-run.md` |

### Signatures we surface as HARD STOPs (no auto-fix attempt)

| Signature | Why no auto-recover | Pointer |
|---|---|---|
| `azd deploy <agent>` 404 with double-slash URL (F-16) | The Bicep `foundry-account.bicep` is outputting the bare account endpoint instead of project-scoped form. `threadlight-auto` can't safely auto-patch operator infra. | [threadlight-deploy § F-16](../threadlight-deploy/SKILL.md#deploy-time-failure-mode-index-signature--action) |
| Agent invoke returns `session_not_ready` after 60 s, `status=active` (F-21) | `main.py` uses sync `DefaultAzureCredential`. `threadlight-auto` can't safely auto-edit operator code. | [threadlight-deploy § F-21](../threadlight-deploy/SKILL.md#deploy-time-failure-mode-index-signature--action) |
| Foundry `403 preview_feature_required` on agent invoke (F-23 — new from threadlight CI run #1) | Region / SKU now requires `Foundry-Features: HostedAgents=V1Preview` header on session-create. Operator-side header injection needed. | [threadlight-deploy § F-23](../threadlight-deploy/SKILL.md#deploy-time-failure-mode-index-signature--action) |

## Closing report

After Invoke completes (or after early termination), `threadlight-auto` emits a one-shot summary:

```
✅ threadlight-auto complete — Contoso Mutual auto-claim triage pilot

Workspace:    ~/Repos/contoso-claim-triage
Tenant:       acme (<tenant-guid>)
Subscription: MCAPS-Subscription-Acme-1 (<sub-guid>)
RG:           rg-contoso-claim-triage-dev-westus3
Agent:        contoso-claim-triage-agent v1 (status: active)
Endpoint:     https://aif-xxx.services.ai.azure.com/api/projects/proj-xxx

Stage wallclock:
  Stage 0 preflight       0m 32s
  Design                  6m 12s   (SPEC.md + manifest.json + AGENTS.md)
  Deploy                 18m 41s   (azd up; 1 region-fallback retry)
  Safe-check post-deploy  1m 17s   (all gates green)
  Invoke                  1m 28s   (2/2 demo scenarios passed)
  TOTAL                 ~28 min

Demo scenarios run:
  1. Rear-end FNOL    ✅ in-force, low fraud, $3.9k estimate
  2. Parked-vehicle   ✅ in-force, PII masked, $2.3k estimate

Production-ready scorecard:
  ⚠️  Not run (skip flag set / opt-in only).
  To run: cp ../threadlight-skills/skills/threadlight-production-ready/scripts/production_ready.py tests/
          python tests/production_ready.py
  Output: docs/production-readiness-report.md + tests/production-readiness-manifest.json
  Soft-advisory only — never blocks the pilot; produces the customer-review
  artifact that turns a demo into a hand-off package.

Auto-recovery events:
  [1] InsufficientQuota in swedencentral → switched to westus3
  (full log in docs/auto-run.md)

Next steps:
  - Tear down: azd down -e dev --purge --force
  - Iterate: edit specs/SPEC.md and re-run threadlight-auto (will skip fresh stages)
```

## When to use vs. NOT use

| Use threadlight-auto | DO NOT use threadlight-auto |
|---|---|
| First-time SE; doesn't yet know the per-skill chain | You want fine-grained control over each stage (use `threadlight-*` directly) |
| Demo: working pilot end-to-end in one Copilot session | You're iterating on a single stage (just call that skill directly) |
| Resuming a failed deploy and want to skip earlier stages | Production CI/CD — this is a pilot driver, not a production pipeline orchestrator |
| Customer demo where the operator just picks a scenario template | You need a skill that isn't on the orchestrator's list (e.g. `threadlight-hitl-patterns`) |

## References

- `references/orchestrator.py` — Python state machine (`--dry-run` for the decision tree, `--state-file <path>` to override default)
- `references/state-schema.md` — `.threadlight/auto-state.json` shape
- `references/scenarios/auto-claim-triage.md` — canned template (3-tool insurance triage)
- `references/scenarios/credit-memo.md` — canned template (multi-business-SKILL credit memo)
- `references/scenarios/prior-auth-healthcare.md` — canned template (healthcare prior-auth)
- Cross-refs:
  - `threadlight-deploy` SKILL § Deploy-time failure-mode index F-01..F-22 (smart-recovery table cribs from here)
  - `threadlight-safe-check` SKILL `phase=post-deploy` (invoked at Safe-check stage)
  - `azure-tenant-isolation` SKILL (Stage 0 HARD STOP enforcer)
