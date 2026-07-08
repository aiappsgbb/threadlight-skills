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
  ImagePull deploy failures. Wraps existing threadlight-* skills.
  USE FOR: full-auto pilot drive, one-prompt threadlight, resume failed deploy,
  demo-in-one-session, autopilot, threadlight orchestrator, start from Kratos export.
  DO NOT USE FOR: per-stage control (use threadlight-design / -deploy /
  -safe-check directly), production CI/CD, single-stage iteration.
metadata:
  version: "1.1.0"
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
4. Writes `.threadlight/preflight-passed.json` marker (24h validity)

> **🛑 HARD STOP #1 — Tenant assertion failure.** If tenant verification fails (wrong
> tenant or wrong subscription active), `threadlight-auto` STOPS IMMEDIATELY. No
> auto-recovery. Money is about to be spent in the wrong place — operator must
> fix isolation before retrying.

## Resumption — read `.threadlight/auto-state.json` first

Before any work, `threadlight-auto` reads `.threadlight/auto-state.json` (if present)
and computes which stages are already done. Stages are skipped when ALL conditions hold:

| Stage | Skip when |
|---|---|
| Preflight | `.threadlight/preflight-passed.json` exists AND `< 24 h` old |
| Design | `specs/SPEC.md` exists AND `sha256(SPEC.md) == auto-state.json[design].artifact_hash` AND no `[NEEDS CLARIFICATION:` markers |
| Local-test | `specs/SPEC.md` exists AND `src/agent/main.py` runs locally (optional stage; skipped on freshness if SPEC unchanged) |
| Deploy | `azure.yaml` + `infra/main.bicep` exist AND `azd env get-values \| grep -q AGENT_FQDN` AND first-listed agent `status: active` via `azd ai agent show` |
| Safe-check | `docs/safe-check-post.md` exists AND `< 24 h` AND post-deploy gate exit was 0 |
| Cost-projection | SPEC § 12 `load_profile{}` is complete (all required keys filled, no `TBD` placeholders) AND `specs/cost-manifest.json.generated_at > AZURE_LAST_DEPLOY_AT` (or `auto-state.json[cost_projection].passed_at` recorded on a prior run) |
| Evals (Discover) | `specs/evals-manifest.json` exists AND `< 24 h` old (re-runs when a fresh deploy/invoke cascades) |
| Red-team (Discover) | `specs/redteam-manifest.json` exists AND `< 24 h` old |
| Govern (Protect) | `specs/govern-manifest.json` exists AND `< 24 h` old |
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
| 4 | Safe-check (post-deploy) | `threadlight-safe-check` `phase=post-deploy` | `docs/safe-check-post.md` + behavioral gates green |
| 5 | Cost-projection (**new**, advisory) | `threadlight-consumption-iq` (`scripts/consumption_iq.py run --all`) | `docs/cost-projection.md` + `specs/cost-manifest.json`. Exit 4 (load profile incomplete) → sets `cost-projection: needs-wizard` in state, surfaces wizard prompt to operator; does NOT block chain. Exit 3 (pricing unavailable, no fixture) → sets `cost-projection: degraded-no-pricing`, warns, continues. Exit 2 (missing prereq, e.g. no SPEC) → same as other missing-prereq cases. |
| 6 | Invoke | direct `azd ai agent invoke` ×2 | Both demo scenarios from `specs/SPEC.md § Demo Scenarios` succeed |
| 7 | Evals — Discover (advisory) | `threadlight-evals` (`scripts/evals_check.py`) | `specs/evals-manifest.json` — offline batch (delegates to `foundry-evals`), Foundry Continuous Evaluation wiring on live threads, + A/B champion–challenger gate. Consumed by production-ready pillar 6 (EVAL-001..004). Advisory — degrades to `not-verified`, never blocks. |
| 8 | Red-team — Discover (advisory) | `threadlight-redteam` (`scripts/redteam_check.py`) | `docs/redteam-report.md` + `specs/redteam-manifest.json` — AI Red Teaming Agent adversarial scan (jailbreak / prompt-injection / exfiltration / harmful-content). Mapped to production-ready pillar 7 (SAFE-101..106). Advisory — never blocks. |
| 9 | Govern — Protect (advisory) | `threadlight-govern` (`scripts/govern_check.py`) | verifier report + `specs/govern-manifest.json` — wraps `foundry-agt`: policy artefact + in-process middleware at the container boundary. Consumed by production-ready pillar 2 (AGT-001..005) + pillar 7 (RAI-002/003). Advisory — never blocks. |
| 10 | Production-ready (OPTIONAL, advisory) | `threadlight-production-ready` (file-path CLI) | `docs/production-readiness-report.md` + `tests/production-readiness-manifest.json` — never blocks. Run when the customer asked for a paved-path / architecture-review artifact alongside the demo. Skip for pure throwaway demos. |
| 11 | Sell (OPTIONAL) | `threadlight-design` regenerates seller-prep | `docs/{seller-prep.md,demo-rehearsal.md}` |

### Per-stage HARD STOPs (in addition to global tenant + quota)

| Stage | HARD STOP signature | Why no auto-recover |
|---|---|---|
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
