# Threadlight — Technical Briefing

> **Engineering reference for the nine-skill pilot pipeline.**
> The narrative / pitch version of this material lives in
> [`threadlight-experience.html`](threadlight-experience.html). This file is
> the chain map: what each skill takes in, what it produces, what it
> depends on, and what fails silently if you skip it.

Threadlight is a **chain of nine `threadlight-*` skills** that take a
customer engagement from a one-paragraph brief through to a deployed,
evaluated, observable, **production-ready** Microsoft Foundry hosted agent
— runnable on the customer's tenant in a single working session, then
walked to production go-live without ending up in lab graveyard. It is
intentionally opinionated about ordering, cross-skill contracts (SPEC §
sections, kebab-case selectors, the three-lifecycle gate), and the seller
→ SE persona split. The contracts are markdown, not code; the runtime is
GitHub Copilot CLI, Cowork, Cursor, or Coding Agent.

The nine skills (alphabetical, but the canonical flow order is given in
the next section):

```
threadlight-design              threadlight-hitl-patterns
threadlight-demo-data-factory   threadlight-local-test
threadlight-deploy              threadlight-production-ready
threadlight-event-triggers      threadlight-safe-check
                                threadlight-workspace-ui
```

---

## When to invoke (entry-skill picker)

Pick the entry skill by what the customer has handed you, not by which
skill sounds most exciting.

| You start with… | Entry skill | Then chain into… |
|---|---|---|
| Vague brief, no spec yet | `threadlight-design` | demo-data-factory → local-test → deploy |
| A spec exists but no mock data / no Cosmos seed | `threadlight-demo-data-factory` | local-test → deploy |
| Spec + data exist, you need a screen-shareable PoC in <30 min | `threadlight-local-test` | (iterate; deploy when ready) |
| Spec + data exist, ready to ship to a customer sandbox | `threadlight-deploy` | safe-check (post-deploy) |
| You inherited an existing deploy and need to know what's broken | `threadlight-safe-check --phase post-deploy` | deploy (re-run) → safe-check |
| Safe-check is green and the customer is about to take this to architecture review / CISO sign-off | `threadlight-production-ready` (run `foundry-evals` first if you want continuous-evals scored as `pass` rather than `not-verified`) | (advisory; reads SPEC § 12, produces hand-off report) |
| SPEC § 8 declares HITL action gates | `threadlight-hitl-patterns` | (paired with `foundry-teams-bot`) |
| SPEC § 8b declares a workspace UI | `threadlight-workspace-ui` | (paired with deploy) |
| SPEC § 10 declares scheduled / event-driven triggers | `threadlight-event-triggers` | (paired with deploy) |

> **Rule of thumb.** Every other skill in the chain assumes
> `specs/SPEC.md` exists and is well-formed. If it doesn't, start with
> `threadlight-design` — every other skill reads SPEC sections as its
> input contract.

---

## The chain

Per-skill summary in canonical flow order. Authoritative source for each
skill is the linked `SKILL.md`; this section is a stable surface map and
should not duplicate skill internals.

### 1. `threadlight-design` ([SKILL.md](skills/threadlight-design/SKILL.md))

**Purpose.** Turn a vague brief into a durable SpecKit specification
(`specs/SPEC.md`) and derive the agent surface (`AGENTS.md` +
`src/agent/skills/`).

**Inputs.**
- A free-text brief (chat, transcript, slide bullet) describing the
  process, the customer industry, and the regulatory frame.
- Optional: a domain primer from
  `skills/threadlight-design/references/domain-primers/` (e.g.
  `fsi-kyc-aml.md`) that pre-loads typical entities, rules and vocabulary.

**Outputs.**
- `specs/SPEC.md` — numbered business rules (BR-XXX), data models with
  system-of-record tracking, tool contracts, system integrations (each
  marked `availability: real | mock`), § 8 human interaction points,
  § 8b workspace UX, § 10/10b triggers, § 11c tech-stack selectors,
  § 11d demo-data realism, § 9 eval scenarios.
- `specs/manifest.json` — machine-readable selector contract
  (`deployment_manifest{}`), the input contract for every downstream skill.
- `AGENTS.md` + `src/agent/skills/<skill>/SKILL.md` — per-process skills.
- `specs/overview.html` — self-contained dark-themed seller pitch page.
- `specs/sample-data/*.json` — initial mock data shells (full generation
  is `threadlight-demo-data-factory`'s job).

**Depends on.** Nothing upstream. The two operating modes are **Full**
(stakeholder review, checkpoint after interview) and **Fast-PoC** (2–3
questions then proceed directly).

**Persona note.** This is the only skill that runs cleanly inside
Microsoft Copilot Cowork. Everything below this line needs a real shell.

---

### 2. `threadlight-demo-data-factory` ([SKILL.md](skills/threadlight-demo-data-factory/SKILL.md))

**Purpose.** Generate per-domain Faker-style synthetic data plus
idempotent Cosmos seed / reset scripts so every demo surface — mock MCP,
workspace UI, eval dataset — reads the same canonical seed.

**Inputs.**
- SPEC § 4 (data models), § 5 (which systems are `availability: mock`),
  § 11d (demo-data realism: per-entity volumes, distributions, named
  golden cases, reset semantics, industry-realism reference).
- The per-industry realism canon in
  `skills/threadlight-design/references/data-realism/{industry}.md`
  (`fsi.md`, `retail.md`, `telco.md`, `mfg.md`).

**Outputs.**
- `specs/sample-data/<entity>.json` — fully populated, with a `_meta`
  block describing the generator run.
- `scripts/seed_data.py`, `scripts/reset_data.py` — Cosmos seed and
  reset for live-demo recovery.
- A `### Anchor pilot` worked-example table folded back into the
  per-industry canon when the pilot revealed new realism rules.

**Depends on.** `threadlight-design` (for the spec). Outputs feed
`foundry-mcp-aca` (Option D mock-server backing) and
`threadlight-workspace-ui` (seed for the SPA).

---

### 3. `threadlight-local-test` ([SKILL.md](skills/threadlight-local-test/SKILL.md))

**Purpose.** Run the designed PoC entirely on the dev box, no `azd up`,
so iteration on tools / prompts / workspace UI happens in seconds rather
than the 20–30 min `azd deploy` round-trip.

**Inputs.** The project as `threadlight-design` left it
(`specs/`, `AGENTS.md`, `src/agent/skills/`).
The skill ships **four patterns**; pick the one that matches the iteration
need:

| # | Pattern | What it runs |
|---|---|---|
| 0 | **Quickstart (default)** | `python -m threadlight_quickstart` — MAF Agent + SkillsProvider + JSON stub tools + Streamlit UI on `localhost:8501`. One LLM dependency (Foundry project OR Azure OpenAI). |
| 1 | MCP-direct | Register the local FastMCP server in `~/.copilot/mcp.json`; iterate tool contracts in the CLI directly. |
| 2 | Smoke-client | `agent.run_async()` — bypasses `ResponsesHostServer`; fastest reasoning-trace loop. |
| 3 | Local-stack | `docker-compose` with the Cosmos emulator. Linux / Windows x86 only — fragile on macOS ARM. |

**Outputs.** No persistent artefacts — this is the inner-loop skill. LLM
calls always hit a real deployment; everything else stays local.

**Depends on.** `threadlight-design` (project structure) and optionally
`threadlight-demo-data-factory` (for non-trivial mock data).

**Skip when.** The pilot is one-shot for an already-deployed sandbox
and the SE just needs a refresh.

---

### 4. `threadlight-hitl-patterns` ([SKILL.md](skills/threadlight-hitl-patterns/SKILL.md))

**Purpose.** Generate Teams Adaptive Card 1.5 flows + bot integration
for the **seven canonical action gates** declared in SPEC § 8
(`approve`, `edit-and-approve`, `reject`, `escalate`, `signoff`,
`audit-view`, `request-info`).

**Inputs.** SPEC § 8 (action gates with linked BR, data presented,
options, timeout/SLA), SPEC § 4 (entity field schemas for card binding),
`AGENTS.md`.

**Outputs.**
- `src/agent/skills/<skill-using-gate>/cards/<gate>.json` — Adaptive
  Card template.
- `src/agent/skills/<skill-using-gate>/cards/<gate>-handler.py` —
  `Action.Submit` handler.
- `src/bot/cards/card_router.py`, `audit_trail.py`, `card_registry.json`.

**Depends on.** `threadlight-design` (gates exist in SPEC § 8) and
`foundry-teams-bot` (bot infrastructure). This skill owns the **gate
UX**; the bot skill owns the **bot itself**.

**Skip when.** The process is fully autonomous or the operator lives
only in the workspace UI.

---

### 5. `threadlight-workspace-ui` ([SKILL.md](skills/threadlight-workspace-ui/SKILL.md))

**Purpose.** Generate ONE polished, framework-agnostic workspace UI
reference (case-list / inbox / dashboard / console / kanban / map shape)
that the customer can rebuild faithfully in their preferred stack.

**Inputs.** SPEC § 8b (workspace shape, primary filters, detail-pane
sections, action toolbar, audit-viewer placement, bulk ops), SPEC § 4
(entity field rendering), SPEC § 8 (action gates), `specs/sample-data/`
(to seed the demo), `specs/manifest.json` (process name + traits).

**Outputs.**
- `src/workspace/index.html` + `workspace.css` + `workspace.js` +
  `seed-data.js` — single-file vanilla-JS reference.
- `src/workspace/components/` — same components broken out for
  copy-paste into React / Angular / Vue / Blazor.
- `src/workspace/README.md` — framework-mapping guide.
- Optional standard HITL panels from `references/hitl-panels/`.

**Depends on.** `threadlight-design` and `threadlight-demo-data-factory`.
The output is **ACA-hosted** in production (not `file://`) and protected
by Easy Auth.

**Skip when.** The operator lives only in Teams cards (use
`threadlight-hitl-patterns` solo).

---

### 6. `threadlight-event-triggers` ([SKILL.md](skills/threadlight-event-triggers/SKILL.md))

**Purpose.** Scaffold non-interactive trigger receivers — **ACA-first**
(jobs, app HTTP receivers, KEDA-scaled consumers), with Azure Functions
only when narrow constraints demand it. Wires idempotency + dead-letter
rules per SPEC.

**Inputs.** SPEC § 10b (receiver contract: trigger source, receiver
type, idempotency key, dedup window, dead-letter rule), § 10 (SLA /
concurrency), § 6 (agent invocation contract — the receiver eventually
calls the agent), § 11c (confirms `event-grid` / `service-bus` /
`aca-job` is selected).

**Outputs.**
- `src/triggers/<trigger-name>/{receiver.py, pyproject.toml, Dockerfile, README.md}`.
- `infra/triggers/<trigger-name>.bicep` + `dead-letter.bicep`.
- Updates to `azure.yaml` to register the new service.

**Depends on.** `threadlight-design`. Complementary to `azd-patterns`
(which teaches *how* to deploy ACA jobs; this skill picks the receiver
shape and writes the code).

**Skip when.** Process is purely chat / on-demand and goes through the
agent directly.

---

### 7. `threadlight-deploy` ([SKILL.md](skills/threadlight-deploy/SKILL.md))

**Purpose.** Take a designed project and generate everything needed to
deploy as a Microsoft Foundry Hosted Agent. **One command —
`azd up` — does the rest.**

**Inputs.** `specs/SPEC.md`, `AGENTS.md`, `src/agent/skills/`,
`specs/manifest.json` (the § 11c selector contract). Reads `foundry-hosted-agents`
for RBAC + identity, `foundry-mcp-aca` for MCP deploy, and
`foundry-observability` for the 3-layer telemetry wiring.

**Outputs.**
- `container.py` — **GHCP SDK runtime by default**
  (`CopilotClient` + `InvocationAgentServerHost`, Invocations protocol).
  Falls back to **MAF** (`Agent` + `FoundryChatClient` +
  `ResponsesHostServer`, Responses protocol) when Toolbox tools or
  custom `@tool` functions are needed.
- `Dockerfile` — uv-based on `python:3.12-slim`.
- `pyproject.toml` — with prerelease handling for hosting packages.
- `agent.yaml` + `azure.yaml` — `azd ai agent` extension scaffold.
- `infra/` — vendored Bicep modules per the SPEC § 11c selectors.
- `mcp-config.json` — wired to mock MCP endpoints (or real ones).
- `copilot-instructions.md` — system prompt derived from `AGENTS.md`.
- `deploy-notes.md` — full deployment guide including mock-system warnings.

Both runtimes support **`SkillsProvider`** progressive skill loading
(`context_providers=[skills_provider]`) — see
`foundry-hosted-agents` § Skill Loading for the canonical defensive
`_build_skills_provider()` helper.

**Depends on.** `threadlight-design`, and `threadlight-demo-data-factory`
if mock systems are present. Outputs are validated by
`threadlight-safe-check` (both phases).

---

### 8. `threadlight-safe-check` ([SKILL.md](skills/threadlight-safe-check/SKILL.md))

**Purpose.** The single mandatory completeness gate. **Catches the
silent failures that `azd up` reports as success.** Three lifecycle
phases, one CLI.

```bash
python -m threadlight.safe_check --phase design        # SPEC ↔ manifest contract
python -m threadlight.safe_check --phase pre-deploy    # manifest ↔ azure.yaml ↔ Bicep ↔ src/
python -m threadlight.safe_check --phase post-deploy   # manifest ↔ deployed resources ↔ channel reach
```

**Inputs.** `specs/manifest.json` (the `deployment_manifest{}` block
written by `threadlight-design`), the current source tree, and (for
post-deploy) `az resource list` of the deployed resource group.

**Outputs.**
- `tests/safe-check-design-manifest.json`
- `tests/safe-check-predeploy-manifest.json`
- `tests/postdeploy-manifest.json`

Each manifest has a top-level `"gaps": []`. **Empty array = pass.**
Exit `0` on pass, `1` on fail.

**Behavioural checks (post-deploy, non-negotiable):**
1. Every `expected_resource_types` entry present in `az resource list`.
2. **No deployed container running the azuredocs `containerapps-helloworld`
   placeholder image.** (Hard-coded Bicep + skipped `azd deploy` slips this in
   silently.)
3. **No ACA Job whose last 5 executions are all `Failed`.** (Cron rot
   ships clean.)
4. App Insights resource exists when SPEC declared it.
5. All `channels` reach HTTP / JWT-OK.

**Depends on.** Everything else. Run it after design, before `azd up`,
and after `azd up` — every time.

---

### 9. `threadlight-production-ready` ([SKILL.md](skills/threadlight-production-ready/SKILL.md))

**Purpose.** **The bridge between a green safe-check and a real customer
architecture review.** The advisory production-readiness gate. Takes a
pilot that has passed `threadlight-safe-check --phase post-deploy` and
walks 13 cross-cutting pillars (network, AGT, IAM, secrets,
observability, evals, RAI, HITL, supply-chain, cost, reliability, SRE
handover, model lifecycle) to produce a customer-facing hand-off
package.

**Posture priority.** Citadel-spoke is the **recommended** enterprise
target. AGT v4 in-process middleware is second. Standard remote AI
gateway / VNet is the third-party fallback. The skill resolves the
actual target from `SPEC § 12 → SPEC § 11b → deployed evidence →
default standard-ai-gateway` and adapts its checks — it does not
shoehorn Citadel where it isn't wanted.

```bash
# default — all 13 pillars, live + static, both outputs
python skills/threadlight-production-ready/scripts/production_ready.py

# static only (no Azure auth required)
python skills/threadlight-production-ready/scripts/production_ready.py --static

# explicit target override
python skills/threadlight-production-ready/scripts/production_ready.py --target citadel-spoke
```

**Inputs.** `specs/SPEC.md` (including the new `§ 12 Production
Readiness` block — target posture, residency, RTO/RPO, SLA, incident
owner), `specs/manifest.json`, `infra/**/*.bicep`,
`tests/postdeploy-manifest.json` (must be fresh — default 24h freshness
window), optional `tests/production-readiness-waivers.json`, and live
Azure (best-effort, tiered per probe — missing permissions degrade to
`not-verified`, never tool failure).

**Outputs.**
- `tests/production-readiness-manifest.json` — machine-readable scorecard
  (posture, score raw + with-waivers, `would_fail_hard_gate`, per-pillar
  findings, evidence register, not-verified list, waivers).
- `docs/production-readiness-report.md` — 10-section customer-facing
  markdown (executive summary, posture diagram, hard-gate preview,
  pillar scorecard, deep-dives, uplift plan, cost projection, eval
  summary, residual risk + RACI + rollout/rollback, appendix).

**Soft-advisory.** Exit `0` even when live probes can't run. Exit `2`
on missing prerequisite (no `tests/postdeploy-manifest.json`, stale
safe-check without `--accept-stale-safe-check`, unknown `--pillar`
id). **Missing SPEC § 12 does not exit 2** — the skill emits an
`RDY-002` warning, falls back to `standard-ai-gateway` posture, and
still produces the report. Exit `3` on I/O failure. This is **a
hand-off package, not a build gate** — it removes every basic /
intermediate excuse to leave the pilot in lab graveyard.

**Recommended ordering.** `threadlight-safe-check --phase post-deploy`
must be green and fresh **before** running this skill. For the
strongest scorecard, also run `foundry-evals` first — otherwise
the `continuous-evals` pillar checks degrade to `not-verified` rather
than `pass`.

**Depends on.** `threadlight-safe-check --phase post-deploy` (must be
green and fresh) + a populated `SPEC § 12` block (the
`threadlight-design` template ships it by default).

**Persona note.** This is the **only** skill in the chain that
explicitly produces a customer-facing artefact for an architecture
review. Treat its markdown report as the deliverable; the JSON manifest
is for your records.

---

## Appendix A — `threadlight-auto` (the orchestrator)

The nine skills above are the **spine**. They're invoked individually
when an SE wants stage-by-stage control: design today, deploy tomorrow,
production-ready next week.

[`threadlight-auto`](skills/threadlight-auto/) is a **separate, optional
wrapper** that drives the chain end-to-end behind one freeform prompt:

```
"Build me an auto-claim triage agent for Contoso Mutual in acme"
   ↓
threadlight-auto → orchestrator.py state machine drives:
   design → (optional) local-test → deploy → safe-check → invoke
        → (optional, advisory) production-ready → (optional) sell
```

It is not a tenth pillar — it's a different shape (a driver). Use it when:

- **First time** running the chain — you don't yet know which skill fires when
- **Demos** — the whole arc has to complete in one Copilot session
- **Resumption** — a deploy failed; you want to retry without re-doing earlier stages
- **Template kickoff** — pick a scenario, fill in `{customer, tenant, region}`

Do NOT use it when:

- You want fine-grained control over each stage (call the spine skills directly)
- You're iterating on a single stage
- This is production CI/CD — `threadlight-auto` is a **pilot driver**, not a
  production pipeline orchestrator. For that, see `azd-patterns` + your CI tool.

The orchestrator persists state in `.threadlight/auto-state.json`, smart-recovers
from the three most common deploy failures (quota, RBAC race, ImagePull), and
HARD STOPs on tenant assertion failure or quota exhaustion. **Stage 6
(production-ready) is opt-in** — the orchestrator never runs it by default,
because demo workspaces shouldn't auto-generate customer-review artefacts.
Set the `--run-production-ready` orchestrator flag (or invoke the skill
directly) when you want the paved-path scorecard alongside the demo.

---

## Templates & substrates

Reusable substrates live in three places. None of them should be edited
in a customer fork — copy them, fork the copy.

| Substrate | Where | Owned by |
|---|---|---|
| SPEC.md skeleton (section numbering, BR-XXX shape, manifest contract) | `skills/threadlight-design/references/spec-template/` | `threadlight-design` |
| Per-industry data realism canons | `skills/threadlight-design/references/data-realism/{industry}.md` | `threadlight-design` + extended by `threadlight-demo-data-factory` |
| Domain primers (FSI-KYC-AML, …) | `skills/threadlight-design/references/domain-primers/` | `threadlight-design` |
| Bicep modules (Foundry account, ACA, ACR, App Insights, Cosmos, AI Search, Service Bus, …) | `skills/threadlight-deploy/templates/infra/` | `threadlight-deploy` |
| `Dockerfile` + `pyproject.toml` + `container.py` runtime templates | `skills/threadlight-deploy/templates/agent/` | `threadlight-deploy` |
| Adaptive Card 1.5 templates for the seven canonical gates | `skills/threadlight-hitl-patterns/templates/cards/` | `threadlight-hitl-patterns` |
| Workspace UI reference per shape | `skills/threadlight-workspace-ui/templates/<shape>/` | `threadlight-workspace-ui` |
| Manifest schema (the cross-skill kebab-case selector vocabulary) | `skills/threadlight-design/references/manifest-schema.json` | `threadlight-design` |
| Production-readiness pillar references, SPEC § 12 template, report skeleton, waiver schema, sample-pilot fixture | `skills/threadlight-production-ready/references/` | `threadlight-production-ready` |

The **selector vocabulary** in `specs/manifest.json` is the contract
shared by every skill in the chain. If you invent a new selector,
register it in the schema first — otherwise `threadlight-safe-check`
will flag it as drift.

---

## How threadlight relates to other GBB skill families

Threadlight is **the wedge** in the broader GBB AI Apps motion. It
delivers a working agent in a working session; the families below extend
that agent over the next weeks and months.

| Family | What it adds after the wedge | Key skills |
|---|---|---|
| **`foundry-*` building blocks** | The Azure-Foundry primitives that threadlight composes — RBAC, agent runtime, MCP deploy, enterprise RAG, document / vision / speech, Teams CEA, evals, telemetry. | `foundry-hosted-agents`, `foundry-mcp-aca`, `foundry-iq`, `foundry-doc-vision-speech`, `foundry-teams-bot`, `foundry-evals`, `foundry-observability`, `foundry-toolbox`, `foundry-vnet-deploy`, `foundry-agt`, `foundry-cross-resource`, `ghcp-hosted-agents` |
| **`citadel-*` governance** | The production landing zone — APIM AI Gateway, Access Contracts, JWT auth, BYO VNet, multi-region hub. | `citadel-hub-deploy`, `citadel-spoke-onboarding` |
| **`gbb-*` content** | Pitch-side artefacts (PowerPoint generators, narrative humanisers). | `gbb-pptx`, `gbb-humanizer` |
| **`auto-demo-producer`** | Records the deployed agent as a narrated video demo (Playwright + edge-tts + ffmpeg). | `auto-demo-producer` |
| **`azd-patterns` / `azure-tenant-isolation`** | Cross-cutting deployment + multi-tenant isolation rules. | `azd-patterns`, `azure-tenant-isolation` |

> **Cross-skill defaults that matter.** `foundry-observability` is
> **always** layered into `threadlight-deploy` (Bicep substrate +
> account-level App Insights connection + `configure_azure_monitor()` in
> each ACA workload). `foundry-evals` runs against SPEC § 9 scenarios
> after every deploy. The pilot ships with telemetry, evals and a
> safe-check gate from day one or it is not a pilot.

---

## Troubleshooting playbook

The top traps the chain warns about. Each one is documented in detail in
the cited `SKILL.md`; this is the index, not the cure.

### 1. `azd up` returned 0 but App Insights is empty

**Cause.** Bicep substrate exists but no Foundry account-level App
Insights connection, or `configure_azure_monitor()` was never called in
the ACA workload.

**Fix.** `foundry-observability` § 3-Layer Wiring (postprovision script
puts the AppIn connection on the **account**, not the project; ACA
workload boots with `configure_azure_monitor()` wrapped for local-dev
safety). `threadlight-safe-check --phase post-deploy` raises a gap.

### 2. Resource group looks clean, but the container is running azuredocs `containerapps-helloworld`

**Cause.** Bicep hard-coded the placeholder; `azd deploy <service>`
was never run after `azd provision`. Eval scores look plausible because
the agent runtime is fine — but the MCP server is the helloworld page.

**Fix.** `threadlight-safe-check --phase post-deploy` image-probe regex
fails the gate. Re-run `azd deploy <service>` and re-check.

### 3. Cron job has 13 consecutive `Failed` executions and nobody noticed

**Cause.** Scheduled jobs don't surface in chat surfaces; nothing pages
on silent rot. Eval pass-rates are unaffected because evals don't invoke
the job.

**Fix.** `threadlight-safe-check --phase post-deploy` job-success
behavioural check (last 5 executions ≠ all `Failed`).

### 4. SPEC § 11c selector exists in the manifest but no module / no `src/<dir>/` / no `azure.yaml` service

**Cause.** Selector added by hand after `threadlight-design`; downstream
skills never re-ran.

**Fix.** `threadlight-safe-check --phase pre-deploy` orphan-selector
check. Re-run `threadlight-deploy` to scaffold the missing module.

### 5. Cowork can't run `threadlight-deploy`

**Cause.** Cowork can't subprocess `azd`, `az`, `docker`, or install
packages. This is by design.

**Fix.** Hand the project off to an SE in a real shell (Copilot CLI,
Coding Agent, Cursor, Clawpilot). The seller→SE handoff happens at
"designed → needs to run". Cowork retains `threadlight-design` end-to-end.

### 6. Eval `tool_output_utilization` FAILs every grounded answer as fabricated

**Cause.** Eval dataset shape missing `tool_calls` + `tool_outputs`.
The evaluator can't see what the agent read, so it scores every cited
fact as fabricated.

**Fix.** `foundry-evals` "enriched dataset shape" — populate
`tool_calls` and `tool_outputs` in each row.

### 7. Demo data isn't reset between live runs and the second take is broken

**Cause.** No `reset_data.py`; demo state carries.

**Fix.** `threadlight-demo-data-factory` ships an idempotent
`scripts/reset_data.py`; the workshop pre-flight runs it.

### 8. `azd ai agent invoke` cold-start > 20 s mid-demo

**Cause.** Hosted agent container is cold; the customer-facing surface
times out before the first token.

**Fix.** Workshop pre-flight: one warm-up call in the last 5 minutes
before the customer joins. Documented in
`threadlight-deploy` deploy-notes.

---

## Quick-reference: invocations

The five most-used incantations, copy-pasteable. Each one assumes a real
shell (not Cowork) for the runtime ones.

```bash
# Spec a brand-new process in Fast-PoC mode
> threadlight-design   # then: "Design a {process} for a {industry} customer. Fast PoC mode."

# Bring up the inner-loop in <30 min
> threadlight-local-test   # Pattern 0 (Quickstart) → http://localhost:8501

# Ship it
> threadlight-deploy   # generates artifacts → run: azd up

# Verify it
> threadlight-safe-check --phase post-deploy   # expects: gaps: []

# Walk it to production-ready (advisory hand-off package)
> threadlight-production-ready   # produces docs/production-readiness-report.md + JSON manifest
```

The full canonical install set lives in `README.md` § "Threadlight".

---

## Where the narrative lives

This file is the engineering reference. The customer / leadership
narrative — manifesto, KPIs, animated chain, flywheel — lives in
[`threadlight-experience.html`](threadlight-experience.html) (open in a
browser).
