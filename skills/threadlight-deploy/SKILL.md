---
name: threadlight-deploy
description: >-
  Generate deployment artifacts for a designed Threadlight project on
  Microsoft Foundry Hosted Agents: container.py, Dockerfile, pyproject.toml,
  an azd project and deploy-notes.md for one-command `azd up`. Kratos-export
  mode enriches/validates a Kratos export and backfills evals/. USE FOR:
  deploy a Threadlight project to Foundry, make this deployable, generate
  deployment files, Threadlight hosted-agent packaging, azd up for a
  Threadlight pilot, Kratos export, foundry-agent.zip, backfill evals. DO NOT
  USE FOR: generic Foundry hosted-agent deploy, invoke or troubleshoot, azd ai
  agent, model deployment or agent evaluation outside a Threadlight project
  (use microsoft-foundry); generic Azure app deployment (use azure-prepare /
  azure-deploy); designing the process (use threadlight-design); Teams bot
  (use foundry-teams-bot); MCP server deployment (use threadlight-mcp-aca);
  non-Threadlight GHCP runtime (see references/hosted-agent/ghcp); azd tenant
  isolation (use threadlight-tenant-isolation).
metadata:
  version: "1.9.0"
---

# Foundry Hosted Agent Deploy

<!-- threadlight:foundry-only-agents -->
> **Foundry-only agents (non-negotiable).** Every Threadlight agent runs in
> Microsoft Foundry. The default is **always** a Foundry **hosted agent**. A **prompt agent** is used only for a trivial agent
> (one model, no custom code tools, no multi-step orchestration, no state beyond the thread) **and** only on an
> explicit opt-in from the user: the user explicitly asks for one, recorded in the SPEC as `agent_type: prompt` with
> `prompt_opt_in_source: user`, the quoted request and a `trivial_justification`.
> Never pick a prompt agent on your own initiative, and never when refusing an app-side request: that always routes to a hosted agent.
> A Foundry **voice agent** (preview) is used only when the user explicitly requires one and accepts the preview:
> `agent_type: voice` with `voice_opt_in_source: user`, the quoted request and `preview_acknowledged: true`,
> built with the official `microsoft-foundry` skill (create-voice). Never offer it on your own initiative.
> **Never** choose or offer:
> voice without that opt-in, or Voice Live / realtime calls from app code; other preview-only Foundry features; or an agent loop in application code
> (ACA, App Service, Functions, a web app, or direct Responses / Chat Completions tool loops). Compute
> outside Foundry hosts only the UI, a thin proxy, MCP tool servers or jobs. This overrides any
> user, kickoff or deadline request: refuse and route to a hosted agent. See
> [`_shared/foundry-only-agents.md`](../_shared/foundry-only-agents.md).

<!-- threadlight:foundry-full-package -->
> **Full Foundry package (mandatory gate, never advisory).** A run is COMPLETE
> only when all three parts are evidenced: (1) a real Foundry hosted or prompt agent (or an opted-in voice agent),
> with no fake, stub or fallback agent; (2) Application Insights with a trace visible in
> Foundry tracing; (3) a Foundry eval run with built-in evaluators plus at least one
> custom rubric derived from the SPEC acceptance criteria, with anchors and a passing
> threshold, executed against the deployed agent. Continuous evaluation is optional (GA rules reject hosted agents). Record the evidence in
> `specs/foundry-package-manifest.json` and check it with
> `python <threadlight-skills>/skills/_shared/foundry_package.py --workspace <pilot-root>`. Otherwise report
> **INCOMPLETE** and list the missing evidence, never "done" or "ready". Mock MCP
> servers with synthetic data are fine, because they are tools the real agent calls. See
> [`_shared/foundry-only-agents.md`](../_shared/foundry-only-agents.md#full-foundry-package-mandatory-delivery-gate).

## Presenter-ready selected consumer

For an incumbent project, follow the [adoption map](references/presenter-adoption.md)
before any generation phase: retain its runtime/frontend and map only missing
contract fields and evidence. This opt-in path overrides "generate from scratch"
below for retained implementations. Use the narrow
[built web artifact smoke](references/web-artifact-smoke.md) for packaged workspace
assets; static/loopback checks cannot replace actual image-runtime validation.

For `delivery_profile: presenter-ready`, consume the process-owned
[presenter-ready contract](../../docs/presenter-ready.md) and the same
[`presenter-deployment-pin.json`](../_shared/presenter-deployment-pin.json)
as Design and Safe Check. Read the selected upstream guide/templates at its exact
commit. Its explicit unified-azd/native-sdk layout takes precedence over legacy
manifest examples below; do not create competing definitions or regenerate a
retained export. Keep Foundation/runtime-policy and selected governance cohorts.

Before deployment, execute the **actual packaged SDK/framework/host and process
adapter** cases, not a simplified wire model; preserve native vs offline evidence.
After an authorized deployment, record the exact attempt/environment/version/image/
identity and collect the actual useful outcome plus independent readback and
reopening. A running container or setup-ready preflight is not backend-verified.
Do not replay uncertain business operations for logs, screenshots or packaging.
Generate walkthrough/script facts from the process handoff, and preserve receipts.

> ⚠️ **Azure Tenant Isolation (mandatory).** Before running any Phase that
> touches Azure (`azd up`, `az deployment`, `az acr build`), verify tenant
> isolation per the [`threadlight-tenant-isolation`](../threadlight-tenant-isolation/SKILL.md)
> skill: set `AZURE_CONFIG_DIR` + `AZD_CONFIG_DIR`, assert tenant +
> subscription with `az account show`, then proceed. Check token validity
> first — only prompt `az login` if `az account show` fails.

Take a project folder (containing AGENTS.md, `src/agent/skills/`, config/, etc.) and enrich
it with all files needed to deploy as a **Microsoft Foundry Hosted Agent**.

## Platform steps owned by `microsoft-foundry`

Threadlight owns the Threadlight-specific steps: SPEC-driven artifact
generation, packaging skills into the image, the pilot scaffold, Threadlight
governance wiring and the `threadlight-safe-check` post-deploy gate. Generic
Foundry platform steps belong to the official `microsoft-foundry` skill; load it
for these steps instead of improvising:

- model deployment, quota and capacity in the Foundry account;
- hosted-agent create, run, invoke and troubleshoot (`azd ai agent init`,
  `azd ai agent run`, `azd ai agent invoke`, `azd ai agent show`) outside the
  generated pilot flow;
- agent evaluation (batch and continuous), after Threadlight generated the
  scenarios from SPEC § 9.

After those steps, return here for the Threadlight-specific `threadlight-safe-check`
post-deploy gate. Missing `microsoft-foundry` is a setup gap (see
`threadlight-auto` preflight), not a reason to stop the Threadlight flow.

## Selected governance — before choosing a container template

Read the explicit governance contract first. **Off means no changes and no
governance infrastructure.** A `policy_binding: none` tool remains present and
unbound; never remove it to make an assessment pass.

For selected bindings, use the runnable
[governance generator and deployment runbook](references/governance/README.md):

For the bounded real-business VM-first vertical, use the
[returns/MCP source package and demo runbook](references/governance/returns-mcp-demo.md).
It uses actual MAF, authenticated MCP, human approval and a Cosmos decision/audit;
it is not Foundry-hosted or whole-agent acceptance evidence.

```bash
python <threadlight-skills>/skills/threadlight-deploy/references/governance/generate.py \
  generate --project <pilot> --contract <contract.json> --configuration <package.json>
```

Read [`references/selected-governance.md`](references/selected-governance.md)
before wiring any selected binding. It holds the MAF local-hook, MAF
gateway-only and GHCP wiring rules, identity separation, the
foundation → package/build → bind order and the bounded `--mcp-prepared` inner
loop. **Selected governance pins override all legacy/default pins below and stale
installed companions** (`../_shared/governance-upstream-pin.json`, exactly).
Missing/unsigned/expired policy, approval or audit dependencies must not produce
healthy readiness; a diagnostic fallback is **not** a healthy agent.

**Canonical runtime policy:**
`../threadlight-design/references/runtime-policy.json`. The locked default
route remains `github-copilot-sdk` + `agent` + `invocations`
(`policy_route: default-agent`). Exception routes are
`deterministic-workflow` → MAF + Responses (Phase 2 generates the MAF Agent
container; workflow-graph generation is not shipped — see the Workflow model gate),
`maf-agent-capabilities` → MAF agent + Responses, and
`explicit-supported-choice` for operator overrides listed in
`compatible_combinations` **and** free of that route's `blocked_when`
capability signals. Canonical default tuple: `github-copilot-sdk` +
`agent` + `invocations` (`policy_route: default-agent`). `specs/foundation.md`
is the selector authority (see Phase 0 Runtime-policy pre-flight below); SPEC
only supplies capability signals, never a competing selector.

Uses the **`azd ai agent` extension** for declarative deployment — `azure.yaml` defines
agent configuration, model deployments, and container resources; `azd up` handles everything.

## When to Use

- User has a designed agent project and wants to deploy it to Foundry
- User asks to "make this deployable" or "package for Foundry"
- User wants to containerize their agent for hosted deployment
- User asks for Dockerfile, container runtime, or deployment files
- User asks about MCP tools in Foundry hosted agents
- User hands you a **Kratos-exported project** and wants to harden it for production

## Input Modes

This skill accepts **two starting points**. Detect which one you're in **before
Phase 1** and branch accordingly — they are additive, with no change to the
existing design-driven path.

| Mode | Detection signal | What this skill does |
|------|------------------|----------------------|
| **threadlight-design mode** (default) | `AGENTS.md` + `src/agent/skills/` present; no `src/hosted-agent/` | Generate all deploy artifacts from scratch (Phases 0–7, unchanged). |
| **Kratos-export mode** | `src/hosted-agent/` **and** `use-cases/<x>/` both present | **Enrich/validate only.** The runtime (`Dockerfile`, `main.py`, `pyproject.toml`, `agent.yaml`, `azure.yaml`) already exists — do **not** regenerate it. Backfill `evals/`, validate, and hand off to the production-hardening skills. |

> [!IMPORTANT]
> **Kratos-export mode skips Phase 2 (Generate Deployment Artifacts).** Kratos's
> exporter already ships a deployable full-clone project; re-running the
> generators would clobber `src/hosted-agent/main.py`, `Dockerfile`, and
> `azure.yaml`. In this mode the skill enriches and validates in place. See the
> full runbook in [`references/kratos-export-mode.md`](references/kratos-export-mode.md)
> and the bridge overview in [`docs/KRATOS-BRIDGE.md`](../../docs/KRATOS-BRIDGE.md).

**Skills root.** Kratos puts skills under `use-cases/<x>/skills/`;
`threadlight-design` puts them under `src/agent/skills/`. Resolve the skills root
with this precedence: (1) explicit `--skills-root <path>` override, (2)
`use-cases/<x>/skills/` if present (Kratos-export mode), (3) `src/agent/skills/`
(design mode). No symlinks, no moving files.

## Why Hosted Agents (not Prompt/Declarative Agents)

Foundry offers simpler agent types (`PromptAgentDefinition`, `DeclarativeAgentDefinition`)
that run on Foundry's servers with no custom container. However, these **cannot** support:

- **SkillsProvider** — progressive skill discovery and on-demand loading
- **Custom tools** — `@tool(approval_mode="never_require")` Python functions
- **Complex orchestration** — multi-step workflows, custom error handling
- **Custom telemetry** — OpenTelemetry instrumentation with Azure Monitor
- **Instruction injection** — runtime `COSMOS_DATABASE` substitution, tool-use discipline

For any agent that uses **skills, custom middleware, or complex logic**, you MUST use
`HostedAgentDefinition` with a custom container.

**The hosted agent is always the default.** A prompt agent (`PromptAgentDefinition`) is
allowed only on an explicit opt-in (the SPEC records `agent_type: prompt` plus a
`trivial_justification`, or the user explicitly asks for one) and only when **all**
of these trivial criteria hold. Never pick a prompt agent on your own initiative:
1. exactly one model;
2. no custom code tools (only Foundry built-in or MCP tools);
3. no multi-step orchestration, HITL or workflow;
4. no state beyond the Foundry thread;
5. no skills or SkillsProvider;
6. no custom middleware or telemetry.

If any criterion fails or is unknown, deploy a hosted agent. Never replace either
type with an agent loop in application code, on ACA, App Service, Functions or a
web app, or by calling Responses / Chat Completions directly with tools. That is
not supported. See
[`_shared/foundry-only-agents.md`](../_shared/foundry-only-agents.md).

**Voice agent (preview, explicit user opt-in only).** When the SPEC records
`agent_type: voice` with `voice_opt_in_source: user`, `voice_opt_in_evidence` and
`preview_acknowledged: true`, Threadlight generates no voice runtime. Hand the
agent to the official `microsoft-foundry` skill from `azure@azure-skills`:
[`create-voice.md`](https://github.com/microsoft/azure-skills/blob/v1.2.113/.github/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/create/create-voice.md)
(Prompt Voice by default, Hosted Voice when the user needs custom code), then its
`deploy` and `invoke` guides and the voice smoke test. Keep the Threadlight
evidence path: record `specs/foundry-package-manifest.json` with
`agent.kind: foundry-voice`, the three opt-in fields, the App Insights trace and a
transcript-based Foundry eval run with a SPEC-derived rubric. If any opt-in field
is missing or agent-written, deploy a hosted agent instead. `generate_only` refuses
`agent_type: voice` with exit 4. Voice Live calls from application code stay
unsupported.

**Voice front-end on the hosted agent (preview, explicit user opt-in only).**
When the SPEC keeps `agent_type: hosted` and records `voice_frontend: hosted-voice`
with the same three opt-in fields, `generate_only` adds a managed `kind: voice`
wrapper (`<agent>-voice`) that uses the GHCP hosted agent as its conversation
engine over Voice Live Bridge 1.0 on `invocations_ws`; tools and governance stay
in the hosted agent. Export `AZD_ALPHA_ENABLE_AGENTS_VOICE=true` before deploying
(azure.ai.agents 1.0.0 gates `kind: voice`). After `azd up`, run the official `microsoft-foundry`
`smoke_test_voice.py` against the wrapper. Barge-in, audio quality and latency are
not validated by it. Details: [GHCP README](references/hosted-agent/ghcp/README.md#optional-voice-front-end-foundry-hosted-voice-preview).

## Prerequisites

> **Kratos-export mode (alternative input).** When starting from a Kratos export,
> the inputs below live under `use-cases/<x>/` instead: `SYSTEM_PROMPT.md` (in
> place of `AGENTS.md`), `use-cases/<x>/skills/*/SKILL.md`, `apm.yml`, and
> `.mcp.json`. The hosted-agent runtime is under `src/hosted-agent/`. See
> [`references/kratos-export-mode.md`](references/kratos-export-mode.md).

The input folder MUST have:
- `AGENTS.md` — agent identity, skills, tools, behavioral guidelines
- `src/agent/skills/*/SKILL.md` — one or more skill definitions

Recommended (from `threadlight-design`):
- `specs/SPEC.md` — SpecKit specification (business rules, data models, integrations, compliance)
- `specs/manifest.json` — checkpoint metadata (process name, phase, status)
- `specs/sample-data/*.json` — mock data for inaccessible systems
- `specs/manifest.json` — machine-readable deployment contract
- `src/agent/config/*.json` — process configuration

> [!IMPORTANT]
> **Dependency skills.** This skill references content from other skills instead of
> duplicating it. Check that companion skills are available:
>
> | Skill or reference | When Needed |
> |-------|------------|
> | [`references/hosted-agent/maf`](references/hosted-agent/maf/README.md) (Threadlight-owned) | **Always** — RBAC, identity model, agent.yaml schema, dependency versions, troubleshooting |
> | `threadlight-design` | **Always** — produces SPEC.md sections this skill consumes (§ 5b, § 7b, § 8, § 8b, § 9, § 10b, § 11b, § 11c, § 11d) |
> | [`references/azd-modules`](references/azd-modules/README.md) (Threadlight-owned) | **Always** — Bicep module library that Phase 6 (Module Composer) reads from |
> | `foundry-iq` (official, `foundry-iq-skills@azure-skills`) | **Default for every process** — provisions the Knowledge Agent + AI Search index for SPEC § 7 knowledge sources |
> | `foundry-teams-bot` (optional GBB pattern skill in awesome-gbb; not a Microsoft product skill) | If Teams integration is needed |
> | `threadlight-mcp-aca` | If deploying custom MCP servers as ACA or Azure Functions |
> | `threadlight-workspace-ui` | If SPEC § 8b specifies an operator workspace |
> | `threadlight-hitl-patterns` | If SPEC § 8 declares any human action gate (approve/edit-and-approve/reject/escalate/signoff/audit-view/request-info) |
> | `threadlight-event-triggers` | If SPEC § 10b declares any event-driven, scheduled, or webhook trigger |
> | `threadlight-demo-data-factory` | If SPEC § 5 marks any system as `mock` (almost always true for pilots) |
> | `azure-ai` | If SPEC § 7b selects any vision / DocIntel / Speech model |
> | `microsoft-foundry` | For post-deployment evaluation AND continuous evaluation: **Plan A** (default) — Foundry's built-in scheduled evaluations (no extra infra). **Plan B** (fallback) — ACA Job cron eval that reads from App Insights and writes to Workbook (use only when Plan A doesn't yet support hosted-agent eval kinds you need). Phase 6 includes the ACA Job ONLY when SPEC § 9 sets `continuous_eval.plan: "B"` |
> | `threadlight-citadel-spoke` | **Phase 7 (opt-in)** — runs ONLY when SPEC § 11b sets `governance_hub.required: yes` |
>
> Use `/skills list` to check availability. If missing, install the official skills from
> `microsoft/azure-skills` (`azure@azure-skills`, `foundry-iq-skills@azure-skills`) and the
> Threadlight skills from `aiappsgbb/threadlight-skills`. Only the optional Teams row needs awesome-gbb.

## Workflow

```
Phase 0  →  Phase 1   →  Phase 2  →  Phase 3   →  Phase 4   →  Phase 5  →  Phase 6  →  Phase 6.5 →  Phase 6.7    →  Phase 7
Poly-repo   Analyze      Generate    Validate     Teams Bot    azd        Module      Demo data     Prep-guide       Citadel
guard       SPEC +       runtime     scaffold     (optional)   project    composer    seed (when    live walkthrough handoff
            AGENTS.md    files                                  scaffold   (Bicep)     mocks exist)  back-fill        (opt-in)
```

---

## Generate-only mode

`generate_only: true` produces a **complete, deploy-ready repository with no
subscription, no login and no network**. It serves Studio-hosted generators and
any user who will deploy later with their own credentials. It runs offline from
vendored templates and covers Phase 0 (poly-repo guard), Phase 1 (workspace
inputs), Phase 2 (repo layout), Phase 3 (validation checklist), Phase 5 (hosted
agent definition, generated from the pilot instead of `azd ai agent init`) and
Phase 6 (`infra/`). Phase 4 and every Azure step are deferred. It never runs `azd ai agent init`,
because that command needs a login and writes nothing without one. It never
calls az, azd or docker, and creates no Azure resources.

```bash
python3 skills/threadlight-deploy/scripts/generate_only.py --project <workspace> [--force]
python3 skills/threadlight-deploy/scripts/generate_only_selfcheck.py \
  --project <workspace> --report <workspace>/tests/generate-only-selfcheck.json
python3 skills/threadlight-local-test/scripts/project_tools.py \
  --project <workspace> --report <workspace>/tests/project-tools-report.json
```

Read [`references/generate-only-mode.md`](references/generate-only-mode.md)
for the canonical source, the GHCP pilot scaffold under
`references/hosted-agent/ghcp/references/pilot` (`references/pilot`: hosted agent
**inline** in `azure.yaml`, no `agent.yaml`, no `infra/core/`), the design/deploy
ownership and adoption rules (exit 4, 5 and 6), the generated contracts
(`specs/deployment-posture.md`, `specs/deferred-steps.md`, pins, model and
capacity, pilot RBAC and telemetry, recommend-only, governed-write
(`"action_mode": "governed-write"`: the one bound write is routed to the governed
gateway, never served by the mock MCP server), selected governance, evals,
deploy evidence, Foundry package gate exit 3) and the offline self-check.

Deployment is then the user's job, run with their own credentials:
`azd auth login`, `azd up`, `python evals/run_evals.py`. After that, run the
deferred steps.

---

## Phase 0: Poly-Repo Guard (mandatory pre-flight)

**Rule**: each threadlight process gets ONE repo. ONE repo = ONE process = ONE
`azd up`. **Never multi-process repos.**

### Pre-flight checklist (run this FIRST, before Phase 1)

Inspect the input folder. These are **repo-shape signals only**. If ANY of
these are true, **stop and ask the user to split the repo before
proceeding**:

- More than one `specs/SPEC.md` exists at any depth
- More than one `AGENTS.md` exists at any depth
- The folder name contains a plural / catalog noun (`processes/`, `catalog/`, `pilots/`)
- The folder contains nested `specs/<process-slug>/SPEC.md` siblings
- A previous run produced an `azure.yaml` with multiple `services:` entries that
  point to different agent containers

A runtime-policy mismatch is **never** one of these signals and is **never**
remediated by splitting the repo — see the mandatory **Runtime-policy
pre-flight** subsection below, which runs independently of this checklist.

### How to split

Read [`references/poly-repo-guard.md`](references/poly-repo-guard.md) for why
multi-process repos are rejected and how to split one.

### Runtime-policy pre-flight (mandatory — separate from the Poly-Repo checklist above)

`specs/foundation.md` is the **selector authority** for the runtime tuple
(`framework` + `runtime_shape` + `protocol` + `policy_route`) — this is a
cross-skill contract check, not a repo-shape signal, and it is **never**
remediated by asking the user to split the repo.

1. **Missing dependency — HARD STOP, never fall back to a remembered
   default.** If
   `../threadlight-design/references/runtime-policy.json` cannot be read
   (the skill isn't installed/enabled), stop immediately and tell the
   operator to install or enable **`threadlight-design`** (`/skills list`; if
   missing, install from `aiappsgbb/threadlight-skills`). `threadlight-deploy`
   already declares `threadlight-design` an **Always** dependency (see
   Prerequisites above), so this file is expected to exist whenever
   `threadlight-design` is present — its absence is a broken installation,
   not a signal to guess.
   **Kratos-export mode gate:** after this dependency check, if
   `src/hosted-agent/` + `use-cases/<x>/` identify **Kratos-export mode**,
   **skip selector migration and validation steps 2–5 below**. The policy-file
   dependency check in step 1 still applies, but Kratos's runtime is preserved
   verbatim and **Phase 2 is skipped entirely**, so there is no Threadlight
   runtime selector to resolve or `specs/foundation.md` to create.
2. **Legacy-foundation migration (partial tuple).** If a pre-existing
   `specs/foundation.md` has `framework` + `runtime_shape` but is **missing
   `protocol` and/or `policy_route`**: inspect the recorded framework
   decision's `source` before trusting the pair.
   - `source`: `provided` is an explicit operator choice: preserve the recorded
     pair **and keep its framework decision provenance as `source:
     provided`**. Resolve its unique `compatible_combinations` entry to recover
     the missing `protocol`, and set `policy_route:
     explicit-supported-choice`. **Defer** that route's
     `requires_resolved_signals` and `blocked_when` gates until step 3 has
     recovered `capability_signals`; enforce both in step 5. If step 3 cannot
     recover the signals, it hard-stops rather than guessing, and step 5 never
     runs.
   - `source`: `defaulted`, `defaulted-after-skip`, or `inferred`
     is **not** an explicit runtime choice. In particular,
     `microsoft-agent-framework` with a defaulted source is the pre-contract
     MAF house default and **must not be migrated, promoted, or preserved** as
     an intentional MAF route. Require current SPEC § 11e `workflow_model` /
     `capability_signals`, apply the first matching policy route, and
     **re-resolve** the complete tuple from those signals. If the signals are
     absent, HARD STOP to `threadlight-design`.
   - A missing or unknown `source` is untrustworthy provenance → **HARD STOP**
     to `threadlight-design`; never switch runtime or protocol by inference.
   Write the completed tuple to `specs/foundation.md`. For the `provided`
   branch, preserve `source: provided` and add a **separate migration note**
   (`migration_note: migrated-from-legacy-foundation via <route-id>`). For a
   re-resolved defaulted/inferred branch, set `source:
   migrated-from-legacy-foundation` and cite the route id used.
   Then continue to step 3 (this record predates `protocol` / `policy_route`
   entirely, so it almost certainly predates `capability_signals` too).
   - **Zero or multiple matching compatible combinations** for that
     explicitly provided `framework` + `runtime_shape` pair → **HARD STOP**
     and send the operator back to `threadlight-design` to re-resolve the
     foundation. This skill must never guess between ambiguous or unsupported
     routes.
3. **Legacy-foundation migration (missing `capability_signals`).** A distinct
   gap from step 2 above — not a fallthrough of it: a pre-existing
   `specs/foundation.md` may already carry the **full** `framework` +
   `runtime_shape` + `protocol` + `policy_route` tuple (step 2 does not apply)
   but still **predate the `capability_signals` contract entirely**. When
   `specs/foundation.md` has no `capability_signals` block (whether it
   started that way or step 2 just migrated the tuple), take
   `specs/SPEC.md § 11e`'s `capability_signals` mirror — the only other place
   it could already be recorded — and **copy the block verbatim** into
   `specs/foundation.md`, including preserving its `source`. Record a
   **separate migration note** (`capability_signals_migration_note:
   migrated-from-legacy-foundation`). If the SPEC block is incomplete or
   missing required fields (including `source`), **HARD STOP** to
   `threadlight-design`. If **neither** `specs/foundation.md` **nor**
   `specs/SPEC.md § 11e` carries a `capability_signals` block, **HARD STOP** to
   `threadlight-design` — an unread signal is not a confirmed-absent one, so
   this skill must never default the four booleans to `false` on its own
   authority. Then continue to step 5.
4. **Foundation entirely absent — hand-crafted deploy mode.** If
   `specs/foundation.md` does not exist at all (no `threadlight-design` run
   preceded this deploy), `specs/SPEC.md § 11e` is the **sole signal source**
   for this path — apply the **first matching policy route** using capability
   signals read directly from it (`workflow_model`, `requires_toolbox`,
   `requires_custom_python_tools`, `requires_file_generation`,
   `latency_sensitive_data_queries`) per
   `../threadlight-design/references/runtime-policy.json`, then **create a
   minimal foundation selection record** (`framework`, `runtime_shape`,
   `protocol`, `policy_route`, `capability_signals`,
   `source: hand-crafted-deploy-inferred`) at `specs/foundation.md` before
   generation. Once written, `specs/foundation.md` **becomes the selector
   authority** for every subsequent read, exactly as it would for a normal
   Step-0-designed project. This preserves documented hand-crafted-deploy
   support. Auto and Design flows should normally already have a
   `specs/foundation.md` with its own `capability_signals`, so this path is
   the exception, not the default. **If `specs/SPEC.md § 11e` itself has no
   `capability_signals` block** (a hand-crafted SPEC predating the contract,
   with no prior `threadlight-design` run), there is no signal source left to
   read at all — **HARD STOP** to `threadlight-design` instead of applying
   any policy route, and **never default the four booleans to `false`** on
   this skill's own authority — an unread signal is not a confirmed-absent
   one.
5. **Complete foundation — validate.** Otherwise (or once steps 2–4 above
   have finished migrating/creating the record), **before any cross-check or
   route evaluation**, validate the complete `capability_signals` schema:
   all four named capability fields must be present and actual booleans;
   `unresolved_signals` must be an array/list containing only known capability
   signal names (with no duplicates); and `source` must be present and belong
   to the taxonomy declared in `foundation-template.md`. An incomplete or
   malformed existing block → **HARD STOP** to `threadlight-design`; omitted
   values are never treated as `false`.
   **Only when** this complete `capability_signals` block exists and passes the
   schema check, **cross-check** the Foundation block against SPEC § 11e's
   `workflow_model` / `capability_signals` mirror — a mismatch between the two
   is drift and must be surfaced, not silently resolved in either direction.
   Then validate the
   `framework` + `runtime_shape` + `protocol` tuple from
   `specs/foundation.md` against
   `../threadlight-design/references/runtime-policy.json`'s
   `compatible_combinations`, and validate `policy_route` separately:
   - For a **concrete `policy_route`** (a `routes[]` entry that declares
     selectors), require the foundation tuple to **exactly match** that route's
     `framework` + `runtime_shape` + `protocol`. Then apply the **first matching
     policy route** in priority order using the current `workflow_model` /
     `capability_signals`, and require its id to equal the recorded
     `policy_route`. A compatible tuple paired with the wrong or inactive route
     id is drift, not a valid selection.
   - For `explicit-supported-choice`, require the corresponding framework
     decision's provenance to be `source: provided`, require none of its
     `blocked_when` signals to be active, and **refuse** it while
     `unresolved_signals` is non-empty (`requires_resolved_signals`).
   **Stop before Phase 1** on an unknown route/selector, a tuple absent from
   `compatible_combinations`, a concrete-route mismatch, or a failed explicit
   choice gate — this skill must not invent or relabel a route. Remediation for
   any failure in this subsection is
   always **return to `threadlight-design` to re-resolve the foundation, or
   install the missing dependency** — never split the repo, and never fall
   back to a remembered default.

Selector values (`framework` / `runtime_shape` / `protocol` / `policy_route`)
always come from `specs/foundation.md` — SPEC never carries a competing
selector of its own. Capability signals (`workflow_model`, `requires_toolbox`,
`requires_custom_python_tools`, `requires_file_generation`,
`latency_sensitive_data_queries`, `unresolved_signals`) are read from
`specs/foundation.md`'s `capability_signals` block and, only when that block
exists, **cross-checked** against SPEC § 11e's `workflow_model` /
`capability_signals` mirror before route validation (step 5 above) — and
`specs/SPEC.md § 11e` is read directly instead whenever `capability_signals` is
still missing from `specs/foundation.md` (steps 3–4 above) —
`specs/foundation.md` remains the sole selector authority once it carries the
block.

---

## MCP in Foundry

Hosted agents reach MCP servers only over **remote HTTP** (no stdio/local); the
container loads `mcp-config.json` (or `MCP_SERVER_URL`) at startup. A custom MCP
server must return HTTP 200 for all six protocol calls (`initialize`,
`notifications/initialized`, `tools/list`, `prompts/list`, `resources/list`,
`logging/setlevel`). Read [`references/mcp-in-foundry.md`](references/mcp-in-foundry.md)
when wiring or debugging MCP servers (ACA vs Functions, transport, timeouts,
private MCP).

---

## Phase 1: Analyze the Design

> **Phase 1.0 — Detect input mode (do this first).** Check for
> `src/hosted-agent/` **and** `use-cases/<x>/`. If both exist, you are in
> **Kratos-export mode**: the runtime is already generated. Read
> [`references/kratos-export-mode.md`](references/kratos-export-mode.md), then
> **skip Phase 2 entirely** and resume at Phase 3 (Validate) — but first run the
> Kratos-mode enrichment steps in that reference (skills-root resolution, evals
> backfill, `azd env` cross-check). If only `AGENTS.md` + `src/agent/skills/`
> are present (no `src/hosted-agent/`), you are in **design mode** — proceed with
> Phase 1 below as usual.

Read all available input files in this priority order:

#### 1a. Read `specs/manifest.json` (if exists)
Machine-readable deployment contract from `threadlight-design`. Provides:
- Process name, traits, business rule count
- Mock systems list → flag for deploy-notes warnings
- Compliance constraints → inform model/region selection

#### 1b. Read `specs/SPEC.md` (if exists)
SpecKit specification from `threadlight-design`. Extract:
- **§ 5 System Integrations** → which are mock vs real → drives MCP config
- **§ 6 Tool Contracts** → map to Foundry tools or MCP servers
- **§ 8 Human Interaction Points** → Teams bot needed? Which channels?
- **§ 9 Success Criteria** → eval scenarios for post-deploy validation (→ `microsoft-foundry`)
- **§ 10 Trigger & Run Model** → model capacity, container resources
- **§ 11 Security/Compliance** → regulatory constraints, data retention
- **`specs/foundation.md` selectors** → already resolved and validated by the
  Phase 0 Runtime-policy pre-flight (`framework`, `runtime_shape`,
  `protocol`, `policy_route` against
  `../threadlight-design/references/runtime-policy.json`). Do not re-resolve
  them here.
- **§ 11e Workflow Model** → `agent` (default) or `workflow` — a **capability
  signal** read back only to confirm `runtime_shape` / the resolved
  `policy_route` still matches current signals; it also drives Phase 1d
  variant selection and Phase 2 container shape. SPEC never carries its own
  competing `framework` / `protocol` / `policy_route` selector.

#### 1c. Read `AGENTS.md` and all skills (always)
Core deployment inputs:

1. **Which Foundry tools are needed** (from the "Foundry Tools Required" table)
2. **Which MCP servers are needed** (custom tools beyond built-ins)
3. **Storage strategy** (Cosmos via MCP, AI Search, Blob, etc.)
5. **Model requirements** (which model deployment, TPM needs)
6. **Skills list** (for SkillsProvider registration)

#### 1d. Choose runtime variant

Read [`references/runtime-variants.md`](references/runtime-variants.md) for the
side-by-side comparison of the **github-copilot-sdk** (`default-agent`), **MAF
Agent** (`maf-agent-capabilities`) and **MAF Workflow** (`deterministic-workflow`)
variants. The MAF Workflow column is a target design: workflow-graph generation
is **not shipped**, and Phase 2 generates the MAF Agent container for that route.

**Decision rules:**
- **Trust the resolved foundation tuple first.** `specs/foundation.md` is the
  selector authority — the Phase 0 Runtime-policy pre-flight already resolved
  and validated `framework` / `runtime_shape` / `protocol` / `policy_route`
  against
  `../threadlight-design/references/runtime-policy.json`'s
  `compatible_combinations`. SPEC supplies capability **signals** only
  (`workflow_model`, `requires_toolbox`, `requires_custom_python_tools`,
  `requires_file_generation`, `latency_sensitive_data_queries`) — it never
  carries a competing selector. If a SPEC signal implies a different route
  than the one recorded in foundation, stop and send the operator back to
  `threadlight-design` to re-resolve.
- **Default route** — `github-copilot-sdk` + `agent` + `invocations`
  (`policy_route: default-agent`). This is still the preferred hosted-agent
  baseline until GHCP Responses works end to end.
- **Use MAF Agent when**: the resolved route is `maf-agent-capabilities`, i.e.
  the agent needs Foundry Toolbox (web_search, code_interpreter), custom
  `@tool` functions, **file generation** (save_report → XLSX/PDF/CSV), or
  latency-sensitive data queries. MAF is also 10-20x faster for these query
  patterns (19s vs 220s+). The 20-34 extra `load_skill`-shaped calls per query
  are **`CopilotClient` runtime overhead**, NOT `SkillsProvider` overhead —
  `SkillsProvider` itself only adds +1 `load_skill` per skill the agent
  activates per query, and works on both runtimes (see
  `threadlight-deploy/references/hosted-agent/maf` § Skill Loading).
- **Use MAF Workflow when**: the resolved route is
  `deterministic-workflow` / SPEC § 11e `workflow_model: "workflow"` —
  deterministic multi-phase processes where the phase order is fixed, persona
  gates control progression, and the orchestrator (not the LLM) decides what
  runs next. This is the MAF equivalent of Zava-style durable orchestration.
  The generated pilot container is still the MAF Agent container (see the
  note under the table); the fixed-order graph is recorded as deferred.
- **Use explicit-supported-choice only when** the operator picked a supported,
  compatible route recorded by design **and** none of that route's
  `blocked_when` capability signals (`workflow_model=workflow`,
  `requires_toolbox`, `requires_custom_python_tools`,
  `requires_file_generation`, `latency_sensitive_data_queries`) apply.
  Otherwise the capability route that owns that signal wins, or the selection
  hard-stops if the operator's explicit choice directly conflicts with an
  active `blocked_when` signal. **Refuse** the route while `unresolved_signals`
  is non-empty (`runtime-policy.json`'s `requires_resolved_signals` gate) —
  resolve the remaining signals, or hard-stop back to `threadlight-design`,
  before honoring the operator's choice.

#### 1e. Choose model access pattern

Direct deployment in your own Foundry project (`azure.yaml`
`config.deployments`) is the default. Read
[`references/model-access.md`](references/model-access.md) when the agent must
use an existing model through an AI Gateway (APIM) connection. The full runtime
references are `references/hosted-agent/ghcp` and `references/hosted-agent/maf`.

---

## Phase 1.5: Deployment-Posture Gate (interactive when SPEC § 11f is silent)

Record an explicit posture before generating files; never silently equate
"deploy" with pilot defaults, especially when the operator said
"production-grade". Read
[`references/deployment-posture.md`](references/deployment-posture.md) for the
posture table, prompt wording, posture-file format and re-run contract.

- **Authority order:** SPEC § 11f → `specs/deployment-posture.md` → `azd env`
  vars (materialized only, never authoritative). Phase 1.5 writes
  `specs/deployment-posture.md`; it does NOT write `azd env` vars.
- **Path 1:** SPEC § 11f sets `deployment_target` (`demo-sandbox`,
  `customer-pilot`, `production-bound`) or a valid posture file already exists:
  take its defaults silently. This is the CI / non-interactive override
  pattern; never overwrite an existing posture file with a fresh prompt.
- **Path 2:** SPEC § 11f absent and no posture file: ask the operator once
  (`demo-sandbox` is the safe default).
- For `customer-pilot` and `production-bound`, foreground the T-0
  operator-blocking checks and walk the production-grade checklist. `deferred`
  rows become `<!-- TODO(posture): ... -->` comments in `main.bicep`.

---

## Phase 2: Generate Deployment Artifacts

> **Kratos-export mode gate.** If `src/hosted-agent/` and `use-cases/<x>/` are
> present (see Phase 1.0), **skip Phase 2 entirely** — the Dockerfile,
> `main.py`, `pyproject.toml`, `agent.yaml`, and `azure.yaml` were shipped by
> Kratos's exporter and must not be regenerated. Run the enrichment steps in
> [`references/kratos-export-mode.md`](references/kratos-export-mode.md)
> (resolve the skills root under `use-cases/<x>/skills/`, backfill
> `use-cases/<x>/evals/`, validate selectors) and resume at Phase 3.
>
> **Workflow model gate.** If SPEC § 11e sets `workflow_model: "workflow"`
> (route `deterministic-workflow`), generate the **MAF Agent** container in
> Phase 2 exactly as for the `maf-agent-capabilities` route: framework
> `microsoft-agent-framework`, protocol **Responses**, `Agent` +
> `FoundryChatClient` + `ResponsesHostServer`, same `src/agent/` layout that
> Phase 5-6 expect. Threadlight does not ship MAF Workflow templates:
> deterministic workflow-graph generation (fixed executor order, durable pause
> points) is **not shipped** and is not provided by any other skill. Do not stop,
> retry-loop or search for another generator. Encode the SPEC phase order and
> persona gates in the agent instructions, skills and HITL tools, and record in
> the deploy report one line:
> `workflow_model=workflow → MAF Agent container (Responses); deterministic workflow graph deferred (not shipped)`.
>
> If `workflow_model` is absent or `"agent"`, proceed with Phase 2 below
> (the existing agent container path — unchanged).

Create these files in the project root. Read
[`references/phase-2-artifacts.md`](references/phase-2-artifacts.md) for the
template and rules of each one:

1. `src/agent/copilot-instructions.md`: runtime system prompt from AGENTS.md
   (500-1500 words).
2. `src/agent/skills/`: generated by `threadlight-design` and read in place.
   Every generated MAF agent/executor applies the
   [autonomous skill-read contract](../_shared/maf-skill-approval.md).
3. `src/agent/mcp-config.json`: remote HTTP MCP servers only, with `${ENV_VAR}`
   placeholders.
4. `src/agent/container.py`: copy the reference template (GHCP default from
   `references/hosted-agent/ghcp`, starting the azd project from the pilot
   scaffold in `references/hosted-agent/ghcp/references/pilot`; MAF from
   `references/hosted-agent/maf`). **Do NOT write the container runtime from
   scratch.**
5. `src/agent/pyproject.toml` (or the hashed `requirements.lock`): proven pins.
6. `src/agent/Dockerfile`: MCR base image, port 8088.
7. `deploy-notes.md`: deploy steps and RBAC, monitoring (the guarded
   OpenTelemetry init is MANDATORY) and the generated eval files.

For content filters (guardrails / RAI policies) on the model deployment, use the
official `microsoft-foundry` skill (`foundry-agent/create/references/guardrails/`).

---

## Phase 3: Validate & Auto-Review (mandatory)

After generating all files, walk the
[output checklist](references/validation-checklist.md) item by item; every
generated file must be accounted for. Run
`python3 tests/safe_check.py --phase pre-deploy` (`threadlight-safe-check`,
wired as an `azd hooks predeploy`). The SPEC § 11c module-selector cross-check,
the Bicep-module orphan check and the `src/`-folder orphan check are MANDATORY.
**If any check fails:** fix it before presenting.

---

## Phase 3.5: Post-deploy completeness gate (MANDATORY)

- If `azd up` fails or the container doesn't respond, check logs first
  (`az containerapp logs show -g <rg> -n <app> --type system`, then
  `--type console`). Never retry blind.
- Run `python3 tests/safe_check.py --phase post-deploy` right after `azd up`
  returns 0 (wired as `azd hooks postdeploy`).
- "PoC complete" is NOT "`azd up` returned 0": every SPEC § 11c resource is
  deployed, every SPEC § 8 channel is reachable and every scheduled job runs.
  **`gaps` MUST be empty** in `tests/postdeploy-manifest.json`; otherwise
  report the deploy as partial with the gap list.

Read [`references/postdeploy-gate.md`](references/postdeploy-gate.md) for the
step-by-step gate and the anti-pattern list.

---

## Situational references

Read these only when the case applies:

- [`references/architecture-reference.md`](references/architecture-reference.md):
  when explaining design choices (hosted vs prompt, SkillsProvider, GHCP vs MAF,
  tool-use discipline) or looking up the container runtime, environment
  variables, identity & RBAC, Bicep parameters, region availability or the
  mock-system MCP ACA.
- [`references/showroom-static-site.md`](references/showroom-static-site.md):
  when deploying a static showroom or site on nginx ACA behind Easy Auth.
- [`references/azd-extension-sdk.md`](references/azd-extension-sdk.md): when you
  need the `azd ai agent` extension `azure.yaml` shape or a manual agent
  deployment.
- [`references/gotchas.md`](references/gotchas.md): when an error does not match
  the failure-mode index below.

---

## Phase 4: Teams Bot (optional)

Only when SPEC § 8 names Teams, the user asks for it, or `specs/manifest.json`
has conversational traits. Use the `foundry-teams-bot` skill and start from
`bot-streaming.py` (MANDATORY), not `bot.py`. Read
[`references/teams-bot.md`](references/teams-bot.md) when Teams is in scope.

---

## Phase 5: Generate azd Project (Extension-Based Scaffold)

Generate an `azd up`-ready project for the `azd ai agent` extension. In
[generate-only mode](#generate-only-mode) and the canonical GHCP pilot layout
the hosted agent is defined **inline** in `azure.yaml` with a self-contained
`infra/main.bicep`; there is no `agent.yaml` and no `infra/core/`. Never mix
the two layouts in one repository. Before running any `azd ai agent` command
non-interactively, read [`references/azd-cli-contract.md`](references/azd-cli-contract.md).
Read [`references/azd-project.md`](references/azd-project.md) for the steps,
the placeholder-token table, the `azd up` flow and the output structure.

---

## Phase 6: Module Composer (Bicep) — read SPEC § 11c, include exactly the right modules

Compose `infra/main.bicep` from only the modules SPEC § 11c selects plus the
always-on baseline `uami → acr → app-insights → foundry-account` (`key-vault`
only when explicitly selected: pilots are keyless). Apply SPEC implications,
thread **supported-now** posture rows into module parameters (`deferred` rows
stay TODO comments), merge hook scripts without overwriting them, then validate:

```bash
az bicep build --file infra/main.bicep
azd provision --preview
azd ai agent doctor   # NOT `azd ai agent validate`, which does not exist
```

Read [`references/module-composer.md`](references/module-composer.md) for each
step; the module catalog is [`references/azd-modules/README.md`](references/azd-modules/README.md).

---

## Phase 6.5: Demo Data Seed (MANDATORY when SPEC § 5 marks any system as `mock`)

An empty store makes a correct agent look broken. Verify that
`threadlight-demo-data-factory` produced `specs/sample-data/*.json` and
`scripts/seed_data.py`, grant the deployer Cosmos data-plane RBAC, wire the seed
into the postdeploy hook chain and verify it. Read
[`references/demo-data-seed.md`](references/demo-data-seed.md) for the steps.

---

## Phase 6.7: Update Seller Prep-Guide with Live MVP Walkthrough (when applicable)

Only when `specs/prep-guide.html` exists **and** the Phase 3.5 gate passed
(`gaps: []`); otherwise skip silently (additive-only, never fails the deploy).
Read [`references/prep-guide-walkthrough.md`](references/prep-guide-walkthrough.md)
when it applies.

---

## Phase 7: Citadel Handoff (opt-in — interactive when SPEC is silent)

When SPEC § 11b sets `governance_hub.required: yes`, invoke
`threadlight-citadel-spoke` after the base deployment. When § 11b is absent or
`no`, ask the operator once (default: no). Read
[`references/citadel-handoff.md`](references/citadel-handoff.md) for both paths.

---

## Deploy-time failure-mode index (signature → action)

**Lookup BEFORE running `az logs` / `azd ai agent monitor` blindly.** Most azd / agent errors have a known root cause and a known fix; matching the error signature here saves the 10-20 min of log-spelunking that re-derives a documented fix.

This index distills the deploy-time failure modes seen across 10 from-scratch pilots (17 + 9 MIDs captured). Threadlight's own deeper Gotchas table is in [`references/gotchas.md`](references/gotchas.md) — when both apply, prefer the row here for the **fast lookup**, then jump to Gotchas for the **full forensic**.

| # | Error signature (in az/azd output, container log, or portal) | 1-line action |
|---|---|---|
| **F-01** | `azd provision` fails with `BCP186: Unable to parse literal JSON value` or `invalid character '\\'` | Hardcode the array/object as a literal in `infra/main.bicepparam`; do NOT use `readEnvironmentVariable() + json()` (triple-escapes through shell → azd → Bicep → ARM). |
| **F-02** | `UserError: Foundry Account capabilityHost Not Found` during provision | Set `ENABLE_CAPABILITY_HOST=false` in azd env. Refreshed-preview platform manages capabilityHost automatically; manual creation removed. |
| **F-03** | `InsufficientQuota` for `gpt-5.4-mini` (or any model) on `azd provision` | Preflight via `az cognitiveservices usage list --location <region>`; default `capacity: 30` for shared-sub pilots (not 100). Try alt regions: `westus3`, `eastus2`, `northcentralus`. |
| **F-04** | ACA app stuck on `mcr.microsoft.com/azuredocs/containerapps-helloworld:latest` after `azd deploy` | Verify `azd-service-name: <service>` tag on the ACA module + matching `services.<service>` in `azure.yaml`. azd will swap the placeholder only when both match. |
| **F-05** | `Image pull failed` < 60s after RBAC granted (per-ACA UAMI side) | RBAC propagation race; add `dependsOn: [rbac]` to the ACA module; wait 60-300s. |
| **F-06** | `[ImageError] Failed to pull container image` (Foundry project MI side, after deploy step) | Foundry **project MI** (separate from per-ACA UAMI) needs `AcrPull` + `Container Registry Repository Reader` on the ACR. Wait 5-15 min if just granted. |
| **F-07** | Agent invoke returns `DeploymentNotFound (404)` for a model whose container log shows a literal `{{...}}` mustache | Mustache not substituted. If you hand-edited `agent.yaml` directly, the manifest-time `{{VAR}}` is no longer a placeholder. Replace with the literal model name OR edit `agent.manifest.yaml` and let the scaffold re-substitute. |
| **F-08** | `ValidationError authType:AAD` on `Microsoft.CognitiveServices/accounts/connections` PUT (App Insights connection) | Switch to ApiKey path. AAD authType is silently rejected on most Foundry account RP versions; ApiKey is the only working flow. |
| **F-09** | Foundry AppIn connection PUT returns 200 but GET returns `credentials: null`; spans don't reach AppIn | Set `metadata.ConnectionString` explicitly on the AppIn connection in Bicep; OR use env-var passthrough workaround (underscored `APPLICATION_INSIGHTS_CONNECTION_STRING`). |
| **F-10** | Agent container exits within 5s of startup with `ValueError: connection_string is required`; agent `status: error` | `container.py` is calling `configure_azure_monitor()` raw at module/main scope. Wrap in `_init_telemetry()` helper that no-ops on missing env / ImportError / SDK exception. The agent runs fine without telemetry — don't let telemetry init kill startup. |
| **F-11** | Agent container log: `WARN APPLICATIONINSIGHTS_CONNECTION_STRING present but malformed (no '=')` | Same as F-10. Foundry runtime sometimes injects a malformed env var (missing the `InstrumentationKey=...;...` shape); validate `=` is present before `configure_azure_monitor()`. |
| **F-12** | Foundry project MI / agent instance MI / blueprint MI all show 0 role assignments via `az role assignment list --assignee <pid> --all` after deploy | Deployer MI lacks `Microsoft.Authorization/roleAssignments/write`. Grant `User Access Administrator` (`18d7d88d-d35e-4fb5-a5c3-7773c20a72d9`) to the deployer MI, scoped to the Foundry account. |
| **F-13** | Agent invoke returns `server_error` after manual RBAC grant; SDK calls fail with 401 | Agent instance MI needs `Cognitive Services OpenAI User` on the Foundry account (extension does NOT auto-assign). Pull MI from `azd ai agent show -o json \| jq -r '.instance_identity.principal_id'`. |
| **F-14** | Backend `/chat` returns 503 "AGENT_ID is not set" | Bicep set ACA env var at provision time, when agent didn't exist yet → empty value stays baked. Switch to postdeploy hook: `az containerapp update --set-env-vars <AGENT>_ID=$(azd ai agent show -o json \| jq -r .name)`. |
| **F-15** | Backend `/chat` returns 502 / 400 `API version not supported` | Don't use `AzureOpenAI(api_version="2025-04-01-preview")` against the project endpoint. Use direct `httpx.post` against `/api/projects/<p>/agents/<a>/endpoint/protocols/openai/responses?api-version=2025-11-15-preview`. |
| **F-16** | `azd deploy <agent-service>` 404 with **double-slash URL**: `POST https://<acct>.services.ai.azure.com//agents/<name>/versions` | Bare account endpoint is the wrong target for `azd ai agent` extension AND for `FoundryChatClient`. Output BOTH forms from Bicep: bare account as `AZURE_AI_PROJECT_ENDPOINT` (for `azd ai agent` ext auto-derivation) AND project-scoped `'<acct>/api/projects/<projectName>'` as `FOUNDRY_PROJECT_ENDPOINT` (for SDK + direct REST). The double-slash signature means the consumer treated the bare-account string as already project-scoped. |
| **F-17** | `azd ai agent` v0.1.34+ postdeploy hook fails: `AZURE_TENANT_ID is not set in the environment` | azd hooks run with cleaned env. Run `azd env set AZURE_TENANT_ID <id>` before `azd deploy` so postdeploy hooks see it. |
| **F-18** | `azd ai agent show -o json` fields missing under `.identity.principalId` or `.id` doesn't match downstream service URLs | Extension v0.1.34 changed shape: use `.name` (bare agent name for URL construction) and `.instance_identity.principal_id` (NOT `.identity.principalId`). |
| **F-19** | `azd ai agent` extension rejects manifest with `protocols` schema error | Schema requires `protocols:` as a plural list of `{protocol, version}`, NOT singular `protocol:`. |
| **F-20** | `azd hook run postdeploy <service>` fails with `AZURE_TENANT_ID is not set` (or any other azd env var) | Same as F-17 — hooks get a cleaned env. `azd env set` whatever the hook reads BEFORE `azd deploy` triggers it. |
| **F-21** | Agent invoke returns `session_not_ready` after 60s timeout; `azd ai agent show` says `status: active`; container logs show no errors | `from azure.identity import DefaultAzureCredential` (sync) passed to `FoundryChatClient(credential=...)` → SDK is async-only, sync credential's `get_token` doesn't satisfy `get_token_async` → first request hangs until session-ready timeout fires. **MUST** use `from azure.identity.aio import DefaultAzureCredential`. |
| **F-22** | `azd deploy <agent-service>` succeeds Foundry `Create agent` + `Polling agent status`, then `failed invoking event handlers for 'postdeploy', failed to fetch agent version for <azd-service-name>/<version>: GET .../agents/<azd-service-name>/versions/<v> 404` | The agent IS deployed (`azd ai agent show -o json` confirms `status: active`). Extension's internal postdeploy event handler looks up by **azd service name** instead of `agent.yaml .name:` — 404. **User's own postdeploy hook never gets a chance to run.** Set `azure.yaml` `services.<service>` == `agent.yaml .name:` as workaround until upstream fix lands. |

> **Sourcing.** The 22 rows came from 10 from-scratch pilots over 6 days (May 2026): weather-agent, learn-assistant ×2, hybrid-mcp-agent, smb-credit-memo, contoso-claim-triage. When threadlight's own from-scratch runs surface NEW failure modes, add a row here.

---

## Gotchas & Hard-Won Lessons

The full forensic table lives in [`references/gotchas.md`](references/gotchas.md).

## See Also

| Skill | Use When |
|-------|----------|
| [**threadlight-design**](../threadlight-design/) | Spec out the business process first (produces specs/ + AGENTS.md + skills that this skill consumes) |
| [**threadlight-deploy/references/hosted-agent/maf**](references/hosted-agent/maf/README.md) | Reference for RBAC, identity model, agent.yaml schema, dependencies, troubleshooting |
| [**foundry-iq**](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-iq/) | **Default for every process** — provisions the AI Search index + Knowledge Agent (consumed in Phase 6 via `foundry-iq-index.bicep`) |
| [**azure-ai**](https://github.com/microsoft/azure-skills/tree/v1.2.113/.github/plugins/azure-skills/skills/azure-ai) | Vision / Document Intelligence / Speech models — consumed in Phase 6 when SPEC § 7b selects them |
| [**foundry-teams-bot**](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-teams-bot/) | Deep dive on Teams bot integration (bot.py, manifest, Bicep, sideloading) |
| [**threadlight-mcp-aca**](../threadlight-mcp-aca/SKILL.md) | Deploy custom MCP servers as ACA or Azure Functions |
| [**microsoft-foundry**](https://github.com/microsoft/azure-skills/tree/v1.2.113/.github/plugins/azure-skills/skills/microsoft-foundry) | Evaluate agent quality + **continuous evaluation**: Plan A (default) Foundry built-in scheduled evals, Plan B (fallback) ACA Job (reads SPEC § 9 KPI table) |
| [**threadlight-safe-check**](../threadlight-safe-check/) | **Mandatory post-deploy completeness gate** — invoked from `predeploy` / `postdeploy` hooks; verifies every SPEC § 11c selector maps to deployed resources, all channels reach, all jobs are wired. Run this before declaring victory or kicking off `microsoft-foundry` |
| [**threadlight-production-ready**](../threadlight-production-ready/) | **Advisory production-readiness gate** — runs *after* a green `--phase post-deploy`. Walks 13 cross-cutting pillars (network/AGT/IAM/secrets/observability/evals/RAI/HITL/supply-chain/cost/reliability/SRE/model-lifecycle), resolves Citadel-spoke vs AGT vs standard AI gateway from SPEC § 12, and produces a customer-facing hand-off report + JSON scorecard. Its scorecard never fails the deploy, but it forces `not_ready` while the mandatory full Foundry package is INCOMPLETE |
| [**threadlight-deploy/references/observability**](https://github.com/aiappsgbb/threadlight-skills/blob/main/skills/threadlight-deploy/references/observability/README.md) | **Always layered into deploy from day one.** Owns the 3-layer telemetry pattern (Bicep substrate → Foundry account-level AppIn connection → `configure_azure_monitor()` in each ACA workload). Without this, `azd up` returns 0 but App Insights stays empty for the entire pilot lifetime — the silent gap observed in recent pilots. The post-deploy hook MUST call `connect_foundry_appinsights.py` and every ACA workload entry point MUST start with `init_telemetry(role=...)` |
| [**threadlight-local-test**](../threadlight-local-test/) | **For SEs.** Optional fast inner-loop before `azd up` — run the designed agent locally (FoundryChatClient + FastMCP + workspace + sample data) in Copilot CLI / Cowork / Clawpilot. Skip when the spec is a one-shot demo or already-deployed pilot |
| [**threadlight-workspace-ui**](../threadlight-workspace-ui/) | Generates the operator workspace from SPEC § 8b (case-list, inbox, dashboard, console, kanban, map) |
| [**threadlight-hitl-patterns**](../threadlight-hitl-patterns/) | Generates Adaptive Cards + audit trail for SPEC § 8 action gates |
| [**threadlight-event-triggers**](../threadlight-event-triggers/) | Generates trigger receivers from SPEC § 10b (ACA Job cron/manual, Functions, ACA consumer) |
| [**threadlight-demo-data-factory**](../threadlight-demo-data-factory/) | Generates realistic demo data when SPEC § 5 marks any system as `mock` |
| [**threadlight-deploy/references/hosted-agent/ghcp**](references/hosted-agent/ghcp/README.md) | Canonical runtime implementation companion — deep reference for the `default-agent` route (GHCP SDK + Invocations, container.py template, long-running agents >120s) |
| [**threadlight-citadel-spoke**](../threadlight-citadel-spoke/SKILL.md) | **Phase 7 (opt-in)** — onboards as a spoke under an AI Governance Hub when SPEC § 11b sets `governance_hub.required: yes` |
| [**microsoft-foundry**](https://github.com/microsoft/azure-skills/tree/v1.2.113/.github/plugins/azure-skills/skills/microsoft-foundry) | AI Gateway (APIM) — use models from another Foundry resource or shared pool |
| [**threadlight-tenant-isolation**](../threadlight-tenant-isolation/SKILL.md) | Per-tenant `AZURE_CONFIG_DIR` / `AZD_CONFIG_DIR` so `azd up` always lands in the right tenant + subscription |
| [**threadlight-deploy/references/azd-modules**](references/azd-modules/README.md) | `azd` hooks, ACA job deployment, **Composable Bicep Module Library** (the source of every module Phase 6 includes) |

## See also — official Azure Skills

Threadlight exists to make Microsoft's own platform **trivial to adopt** — never
to replace it. For first-party depth behind this deploy leg, reach for the official
**[Azure Skills](https://github.com/microsoft/azure-skills)** catalog. *Further
reading, not a dependency* — Threadlight's guidance stays the source of truth for
the pilot flow:

- **[`microsoft-foundry`](https://github.com/microsoft/azure-skills/blob/v1.2.113/.github/plugins/azure-skills/skills/microsoft-foundry/SKILL.md)** — first-party `azd`-based **model deployment + hosted-agent create/run**; the platform surface Phase 5's `azd ai agent` scaffold builds on.
- **[`azure-quotas`](https://github.com/microsoft/azure-skills/blob/v1.2.113/.github/plugins/azure-skills/skills/azure-quotas/SKILL.md)** — subscription **quota and usage** checks by region before `azd provision` (Container Apps, Cosmos DB, compute); Foundry model TPM/PTU quota stays with `microsoft-foundry`.
