# Threadlight — Pilot Pipeline Skills

> **A business process becomes a working pilot with selected runtime governance and an evidence-backed path to production.**
>
> Twenty-two pipeline skills plus one agent-guided lifecycle planner (23 total) take a brief into a working pilot. Governance is evidenced per selected binding, not asserted for the whole agent. A working session produces the pilot and auditable evidence; production certification, settled Azure actuals, and customer-environment onboarding each have their own timelines.
>
> SPEC § 14 is the value-model contract: baseline, target, owner, timeframe,
> measurement source, and maturity policy. Its public arc is forecast →
> settled Azure actuals → reconciliation → cost per successful interaction.

The paid live workflow has two evidence meanings. **Live smoke** proves the
design, deployment, invocation, and assurance producers executed; it does not
assert production readiness. **Readiness proof** additionally requires a green
post-deploy safe-check, current per-binding runtime evidence, comprehensive evals,
hardened red-team evidence, a ready production scorecard, and measured outcome KPIs.
The `local-native-contract` CI job is local proof only. The separate protected
`readiness-proof` job fails without actual signed policy, registered services,
explicit preproduction probe opt-in and fresh hosted evidence; it does not
substitute a noop result for an unverified business binding.

## Runtime governance: method, enforcement and evidence

**SAFE is the method** for defining business invariants. **ACS is the PDP**
(policy decision point), evaluating native Rego through OPA locally.
**Agent Hooks is the host/interceptor contract SDK**; the **native host or
governed-tool gateway is the PEP** (policy enforcement point) at the selected
action lifecycle boundary. **AGT is the toolkit**, not automatic all-tools
protection. **ASSERT is assurance**, not an enforcement engine.

Select tool, intervention point and execution path explicitly. Report each as
`enforced`, `observed`, `unbound`, `unverified`, `unsupported` or `bypassable`.
A policy file, green CI, or one enforced binding never means whole-agent
`governed` or SAFE-complete. Unbound read tools remain usable without ACS;
unbound consequential actions require current scoped risk acceptance for
readiness, not an arbitrary boolean. Invalid selected configuration is not `off`.

Required controls fail closed **before effects**: a trusted, signed, fresh
policy; authenticated human approval bound to tenant, roles, full action scope
and a one-use CAS nonce; trusted target/schema and dynamic backend facts; and
centrally durable audit ACK. Local spool fsync is retry safety, not hosted
durability. Host facts, not model claims, supply SAFE evidence; authorization
must survive waits and be rechecked at terminal dispatch.

- **Native MAF:** selected local hooks support tool/lifecycle and buffered-output
  controls. Selection is not automatic full SAFE. Compaction, provider-hosted
  tools and incremental streaming/custom clients remain unsupported.
- **GHCP:** only registered gateway action paths can claim effect closure.
  No arbitrary URL/shell routing, full lifecycle or full-output coverage.
- **Distribution:** actual registered ACA control-plane/gateway services use
  Cosmos, Blob, Key Vault and separate UAMIs/permissions. ACS stays the local
  policy engine; signing/publishing is distinct from runtime verify/read.

The authoritative [shared pin](skills/_shared/governance-upstream-pin.json)
uses `agent-governance-toolkit-core==5.0.0` (not the old umbrella installer),
ACS `0.3.1b0`, Agent Hooks `0.1.0a5`, MAF core `1.14.0`, Foundry `1.11.0`,
hosting `1.0.0b260813`, OPA `1.18.2`. Preview/alpha integrations and explicit
experimental defaults require review. The immutable official runtime is not
patched: the corrected CTK **test-only oracle** covers 47 declared vectors;
four incremental-output vectors are undeclared, not “51 passed”.

Evidence remains separated: offline inventory → executed **LOCAL-14** proof and
exact-published native/CTK tests (local only) → ARM/Foundry-observed deployment
and fresh hosted invocation. The Task11 collector registers fresh allow/deny
nonces, observes tenant/subscription/RG/version/image/principal and closed config
before/after, and joins authenticated producer/fixture counters with control-plane
receipts. Only reserved `governance_probe_noop` is probed; business bindings stay
unverified. The current canonical [returns example](examples/returns-triage-governed/)
records a Cosmos decision/audit, not payment settlement; its business binding
has local proof, not live proof.

Current artifacts are `specs/governance-manifest.json`
(`threadlight-governance-manifest/v1`), `tests/governed-actions-manifest.json`,
and `.threadlight/governance-live.json`. Legacy v2 verdicts are historical
provenance only. A new deployment attempt needs new **after-deployment** proof;
file mtime, a prior empty gaps array or a changed signing envelope cannot renew it.
See [production guidance and CI inputs](docs/production-readiness.md#runtime-governance-lifecycle)
and [coding-agent guidance](AGENTS.md).

| Skill | What it does |
|-------|-------------|
| [`threadlight-qualify`](skills/threadlight-qualify/) | **No-repo / Cowork qualification & sizing entry — *before* Design.** Turns a declared interview (no Azure, `az`, `azd`, Bicep, Docker, or customer credentials) into a deterministic sizing package — `qualification/sizing.md`, `sizing-manifest.json`, `discovery.md`, optional `roi.md` — and seeds SPEC § 12 `load_profile{}`. **Not a deployed runtime skill.** |
| [`threadlight-design`](skills/threadlight-design/) | Locks the technical foundation (framework, model, hosting, identity, observability) up front, then produces SPEC.md, demo deck, prep guide, experience page from a brief |
| [`threadlight-local-test`](skills/threadlight-local-test/) | Boots the agent locally for rapid iteration (Pattern 0 quickstart) |
| [`threadlight-deploy`](skills/threadlight-deploy/) | 7-phase `azd up` orchestration — ACR, Bicep, hooks, Foundry, Citadel |
| [`threadlight-safe-check`](skills/threadlight-safe-check/) | Pre/post-deploy gate — resource selectors plus mandatory scoped collector evidence for selected governance |
| [`threadlight-demo-data-factory`](skills/threadlight-demo-data-factory/) | Generates industry-realistic seed data for demos |
| [`threadlight-event-triggers`](skills/threadlight-event-triggers/) | Wires ACA Jobs, Event Grid, and cron receivers into the deploy lifecycle |
| [`threadlight-connect`](skills/threadlight-connect/) | **NEW v0.1.0** — the **CONNECT leg** (manual hand-off). Evidence-based swap of a scaffolded **mock** Foundry tool for a **real** endpoint — extracts the contract the tool source actually reads, generates conformance tests, and gates `mock → real` on conformance **AND** OBO user-scoped evidence **AND** required-role revalidation vs the current identity. Config writes need `--apply` + a validated `--real-endpoint`; emits `specs/connect-manifest.json` (INT-001..004). `threadlight-auto` does **not** run it. |
| [`threadlight-ground`](skills/threadlight-ground/) | **NEW v0.1.0** — the **GROUND leg** (manual hand-off). Assesses caller-supplied ACL / citation / refusal probe evidence into `specs/ground-manifest.json` (GRD-001..004) — a proven ACL leak is `must-fix`, missing/ambiguous evidence is `not-verified` (never guessed). A **coordinator**, not a retrieval engine: it never calls Foundry IQ or runs a live probe. `threadlight-auto` never runs probes. |
| [`threadlight-hitl-patterns`](skills/threadlight-hitl-patterns/) | Human-in-the-loop gates via Teams Adaptive Cards + audit trail |
| [`threadlight-workspace-ui`](skills/threadlight-workspace-ui/) | Framework-agnostic vanilla HTML/JavaScript reference patterns behind Easy Auth (not React) |
| [`threadlight-consumption-iq`](skills/threadlight-consumption-iq/) | Current forecast + read-only actuals + reconciliation + measured cost per successful interaction; actuals are later-pilot. Walks Bicep + `azd env`, reads SPEC § 12 `load_profile{}` (wizard writes it if absent), hits Azure Retail Prices for current SKUs + 2–3 alternatives per resource (AOAI, Foundry, ACA, Cosmos, Storage, APIM, AI Search), emits `docs/cost-projection.md` + `specs/cost-manifest.json`. Soft-advisory; consumed by `production-ready`'s tightened COST-005 + new COST-006. |
| [`threadlight-evals`](skills/threadlight-evals/) | **NEW v0.1.0** — the **DISCOVER/GOVERN evals leg**. Runs offline batch quality evals (delegates invoke+score to `foundry-evals`), wires **Foundry Continuous Evaluation** on live threads (`create_agent_evaluation` → App Insights), and an **A/B champion–challenger** comparison gate before a model/prompt swap. Emits `specs/evals-manifest.json` that `production-ready` pillar 6 (EVAL-001..004) consumes as leg-verified evidence. |
| [`threadlight-redteam`](skills/threadlight-redteam/) | **NEW v0.1.0** — the **DISCOVER safety leg**. Runs the **AI Red Teaming Agent** (PyRIT-based) adversarial scan for jailbreak / prompt-injection / data-exfiltration / harmful-content, emits `docs/redteam-report.md` + `specs/redteam-manifest.json`. Maps attack-success-rate to `production-ready` pillar 7 SAFE-101..106 findings. |
| [`threadlight-govern`](skills/threadlight-govern/) | Authors and validates native ACS/Rego bundles, routes explicit host/service generation, emits offline per-binding `specs/governance-manifest.json`; never declares runtime enforcement from policy/CI. |
| [`threadlight-governed-actions`](skills/threadlight-governed-actions/) | Produces selected runtime templates through the real generator and assesses inventory, executed LOCAL-14/native path proof, approvals, audit and the change plane. `--emit --gate` writes `tests/governed-actions-manifest.json` and the Governance Evidence Pack. Local conformance is not live enforcement or certification. |
| [`threadlight-production-ready`](skills/threadlight-production-ready/) | Advisory scorecard/handoff, not certification; remediation and deployment are explicit separate choices. BicepGraph parser, 13 pillars, Defender / Policy / quota / restore-drill checks, `--gate-preview`, `--diff`, `--remediate`, `--trend-csv`, OIDC CI. Hard dep on `bicep` CLI; no regex fallback. Pillars 2/6/7 consume the govern/evals/red-team leg manifests when present + fresh, plus the connect/ground/load/upgrade gap-evidence legs. |
| [`threadlight-loadtest`](skills/threadlight-loadtest/) | **NEW v0.1.0** — the **LOAD leg** (manual, live, cost-bearing). Runs one budget-capped load profile through **k6 / locust** (or an injected adapter) and emits `specs/load-manifest.json` (`threadlight.load/v1`, LOAD-001..003) with real p50/p95/p99 latency, error-rate, and tokens/request evidence. Aborts before any run if the projected cost exceeds `budget_ceiling_usd`, or if a production endpoint lacks explicit `allow_production`; never installs k6/locust; never loops. `threadlight-auto` does **not** run it. |
| [`threadlight-upgrade`](skills/threadlight-upgrade/) | **NEW v0.1.0** — the **UPGRADE leg** (plan-only). Scans dependency pins, hosted-agent runtime policy, governance profile, and model families against a dated `compatibility-matrix.json` and emits `specs/upgrade-manifest.json` (UPG-001..003) + **one ordered migration plan**. No network calls, no `--apply` — it **never edits the project**. Acting on the plan is a manual, human-driven step. |
| [`threadlight-cicd`](skills/threadlight-cicd/) | **NEW v0.1.0** — production deploy pipeline + env-setup runbooks for locked-down customer envs (no direct `azd up`). Onboarding-path gate (standalone / spoke-onboard / hub-deploy-then-spoke), then generates **GitHub Actions or Azure DevOps** OIDC/WIF pipelines + UAMI/federated-credential, least-privilege RBAC, and private-VNet runner runbooks. Secret-free; ships a `central-platform-boundary.md` that keeps the pilot pipeline **separate** from `citadel-hub-deploy`. |
| [`threadlight-customize`](skills/threadlight-customize/) | **NEW v0.1.0** — the **fork-and-customize final leg**. Instructions/runbooks (not automation) for forking the Threadlight pipeline and onboarding it into **one customer's environment** — landing zones, RBAC, pipelines, governance — with **production onboarding priority #1**. Four moves: intake gate (customer-profile workbook), customization map (fork-vs-keep), test-in-customer-env runbook (private-VNet via **Azure ML VS Code** / **GH Codespaces**), and an explicit non-coverage boundary. Ships a fork-runbook (`upstream-pin` + overlay). Manual handoff — `threadlight-auto` does **not** drive it. |
| [`threadlight-router-bench`](skills/threadlight-router-bench/) | **NEW v0.1.0** — the **IMPROVE leg**. Offline self-improvement cold-path: `learn <run_id>` harvests ONE finished CI run (green *or* red) into a grounded learnings digest — phase parity, a reality-tuned failure taxonomy, and recommendations; optional `bench <candidate> <baseline>` is a paired model-router **cost/quality scorecard** from Azure Monitor token metrics. Offline — `threadlight-auto` does **not** drive it. |
| [`threadlight-auto`](skills/threadlight-auto/) | **Agent-guided lifecycle planner** — `orchestrator.py` decides, coding agent executes; manual/live/cost-bearing/plan-only legs are handoffs. Reads evidence, chooses the next stage, resumes from `.threadlight/auto-state.json`, and smart-recovers quota/RBAC/ImagePull failures. Does **not** drive the manual legs (qualify, connect, ground, loadtest, upgrade, cicd, customize) or the offline router-bench. |
| **Threadlight Lifecycle Canvas** | **GitHub Copilot App enhancement** - an outcome-oriented cockpit for all 23 skills. Starts a pilot from a brief, projects progress from canonical artifacts, and sends safe next-action intents back to chat. Optional; existing CLI/Cowork/Coding Agent flows are unchanged. |

### Canonical lifecycle classification

| Stage class | What runs | Evidence boundary |
|---|---|---|
| No-repo entry | qualify; declared evidence, no Azure | sizing only; no runtime artifacts |
| Agent-guided pilot path | design, optional local test, deploy, safe-check, forecast, invoke, evals, red-team, govern, governed-actions; Auto plans, coding agent executes | pilot evidence and review artifacts |
| Manual live evidence | connect, ground, load-test | live, customer, and cost-bearing evidence captured by handoff |
| Optional handoff | production-ready, CI/CD, customize | advisory or deployment/runbook handoffs |
| Later-pilot evidence | settled actuals and reconciliation | post-pilot value evidence and cost reconciliation |
| Offline improvement | router-bench and upgrade | finished-run learning and compatibility scans |

## Pipeline flow

```
threadlight-qualify (no-repo / Cowork sizing — before any repo exists) →
threadlight-design → threadlight-local-test → threadlight-deploy →
threadlight-safe-check (gate) → threadlight-consumption-iq (cost) →
CONNECT: threadlight-connect (mock→real tool swap — manual, evidence-gated) →
DISCOVER: threadlight-evals (offline + online CE) + threadlight-redteam (adversarial scan) +
          threadlight-ground (ACL / citation / refusal grounding — manual) →
PROTECT: threadlight-govern (AGT runtime governance) +
         threadlight-governed-actions (selected runtime generation + scoped evidence) →
foundry-observability →
threadlight-loadtest (budget-capped, production-confirmed load evidence — manual) →
threadlight-production-ready (advisory; verifies the legs ran) → customer architecture review →
threadlight-cicd (prod deploy pipeline, when the customer env is locked down) →
threadlight-customize (fork + onboard into the customer's own environment)

  ↻ IMPROVE / LIFECYCLE (offline / plan-only, after any CI run):
    threadlight-router-bench — learnings digest (failure taxonomy + recommendations)
                               + optional model-router cost/quality scorecard
    threadlight-upgrade      — plan-only compatibility / preview-drift lifecycle scan (no --apply)
```

The spine maps to the Microsoft Responsible-AI-for-Foundry operating loop —
**Design → Build/Deploy → Discover → Protect → Govern → Improve**. The
**Discover** legs (`threadlight-evals`, `threadlight-redteam`, and the
`threadlight-ground` grounding leg) and the **Protect** legs
(`threadlight-govern` for runtime governance policy, and
`threadlight-governed-actions` for consequential-action mediation, enforcement,
approval, and audit evidence) run *before* the readiness gate so that
`threadlight-production-ready` verifies each control-plane leg actually ran and
its artefact is fresh, rather than only scoring whether one was declared. The
loop's **Improve** phase is `threadlight-router-bench` — an offline leg that
turns a finished CI run into a grounded learnings digest (failure taxonomy +
recommendations) and, optionally, a model-router cost/quality scorecard, so each
pilot feeds the next.

`threadlight-qualify` sits *ahead* of the spine as the **no-repo entry**: it is
Cowork-safe qualification & sizing that runs before any repository exists and
seeds SPEC § 12 for `threadlight-design`. It is **not a deployed runtime skill**.
`threadlight-auto` is the **agent-guided lifecycle planner**. It reads the
latest evidence, chooses the next stage, and hands execution to the coding
agent. Manual / live / cost-bearing / plan-only legs are explicit handoffs, not
auto-run steps: **`threadlight-connect`** (mock→real tool swap),
**`threadlight-ground`** (grounding evidence), **`threadlight-loadtest`**
(budget-capped load evidence), **`threadlight-cicd`** (pipeline handoff), and
**`threadlight-customize`** (customer onboarding) stay human-led. The offline
**`threadlight-router-bench`** improve leg and the plan-only
**`threadlight-upgrade`** lifecycle scan sit outside the pilot loop entirely.

The full technical briefing is in [`THREADLIGHT.md`](THREADLIGHT.md).

## Starting from a Kratos export

Threadlight skills also compose on a **Kratos-exported agent project**. An SE
can run the Kratos `Agent Manager → Deploy tab` export, `azd up` the bundle, then
layer in Threadlight production-hardening — no rewrite, additive to the
`threadlight-design` flow above.

```bash
unzip <use-case>-foundry-agent.zip && cd <use-case>-agent
azd auth login
azd up -e <use-case>-prod
```

Then invoke, in order: `threadlight-safe-check` → `threadlight-deploy`
(Kratos-export mode: enrich/validate + backfill `evals/`) → `foundry-evals` →
`threadlight-consumption-iq` → `threadlight-production-ready`, plus on-demand
`threadlight-hitl-patterns` / `threadlight-event-triggers` /
`threadlight-workspace-ui`. The canonical reference — detection signal,
skills-root convention, what's intentionally trimmed, and the full invocation
order — is in [`docs/KRATOS-BRIDGE.md`](docs/KRATOS-BRIDGE.md).

## Quickstart in GitHub Codespaces

Want to try the skills without installing anything? Open this repo in a
Codespace and you get **GitHub Copilot CLI with all 23 threadlight skills
pre-wired** from the checkout.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/aiappsgbb/threadlight-skills)

The [`.devcontainer`](.devcontainer/) installs Copilot CLI and registers the
skills automatically. Once it boots:

```bash
copilot          # start Copilot CLI
/login           # first launch only — sign in via device flow
```

Then just prompt, e.g. *"use threadlight-design to draft a SPEC from this
brief: …"*.

Prefer the released plugin over the local checkout? Swap in the marketplace
version any time:

```bash
copilot plugin marketplace add aiappsgbb/threadlight-skills
copilot plugin install threadlight-skills@threadlight-skills
```

### In a GitHub cloud sandbox

Just enabled **[cloud sandboxes](https://docs.github.com/en/copilot/how-tos/cloud-and-local-sandboxes)**
for your org? You can run the skills in a fully isolated, **ephemeral Linux box
hosted by GitHub** — nothing installed locally, and you can pick the session back
up from any machine:

```bash
copilot --cloud    # launch an ephemeral cloud sandbox (public preview)
```

A cloud sandbox **does not read `.devcontainer/`**, so the auto-wiring above
doesn't apply — install the skills from the marketplace the same way you would
anywhere:

```bash
copilot plugin marketplace add aiappsgbb/threadlight-skills
copilot plugin install threadlight-skills@threadlight-skills
```

A few things to know:

- **Governance is inherited.** Each session runs under your org's existing
  **Copilot cloud agent policies** — the firewall/allow-list your admins already
  trust — with no extra setup. For the deploy and cost skills to reach Azure,
  that policy needs to allow the hosts they call: `management.azure.com`,
  `*.services.ai.azure.com`, `ai.azure.com`, `login.microsoftonline.com`,
  `sts.windows.net`, `prices.azure.com`, `github.com`, `ghcr.io`,
  `mcr.microsoft.com` and `learn.microsoft.com`.
- **No Azure deploy tooling.** Like the Codespace, a cloud sandbox has no
  `az` / `azd` / `bicep` / Docker or subscription credentials preloaded, so the
  deploy and production-hardening legs still need a full local or in-VNet box.
- **Preview + usage-billed.** Cloud sandboxes are in **public preview** and
  billed by usage — stopping a session snapshots it; deleting it frees the
  storage.

### Limitations

The Codespace is a **thin, consumer-focused** box for authoring and exploring
skills — not a full deploy environment:

- **Auth:** the first `copilot` launch needs `/login`. Codespaces injects a
  repo-scoped `GITHUB_TOKEN` that lacks the *Copilot Requests* permission; if it
  interferes with sign-in, run `unset GITHUB_TOKEN` in the terminal and retry
  `/login`.
- **No Azure deploy tooling** (`azd`, `az`, `bicep`, Docker) — the deploy and
  production-hardening legs (`threadlight-deploy`, `threadlight-safe-check`,
  `threadlight-production-ready`, …) need a full local or in-VNet environment.
  See [`threadlight-customize`](skills/threadlight-customize/) for private-env
  testing patterns.
- Some MCP/agent tools (e.g. workiq) may not function in a Codespace.

> **Tip:** to let anyone clone this setup with **Use this template**, a repo
> admin can enable *Settings → Template repository*. That is independent of the
> devcontainer above — no code change required.

## Install

### As a plugin (recommended)

```bash
copilot plugin marketplace add aiappsgbb/threadlight-skills
copilot plugin install threadlight-skills@threadlight-skills
```

### Individual skills

```bash
gh skill install aiappsgbb/threadlight-skills threadlight-design
gh skill install aiappsgbb/threadlight-skills threadlight-deploy
# ... etc
```

### Companion skills (in awesome-gbb)

Threadlight skills cross-reference foundry-*, azd-patterns, citadel-*, and
other skills from [awesome-gbb](https://github.com/aiappsgbb/awesome-gbb).

Threadlight is deliberately **thin where the foundry-\* family is already deep** —
it composes with those skills rather than reimplementing them:

| Companion (awesome-gbb) | Threadlight composes with it for |
|---|---|
| [`foundry-skill-catalog`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-skill-catalog) | Publishing skills/tools as **versioned, immutable Foundry artifacts** — pin a version, promote `default_version` in stages, download at deploy. This is the lifecycle `threadlight-production-ready`'s supply-chain pillar checks (SUP-008/009). |
| [`foundry-toolbox`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-toolbox) | Curating the **tool set** an agent binds to, versioned alongside its skills. |
| [`foundry-evals`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-evals) | Offline batch invoke + score behind `threadlight-evals`. |
| [`foundry-agt`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-agt) | Agent-runtime governance policy behind `threadlight-govern`. |
| [`foundry-hosted-agents`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-hosted-agents) · [`azd-patterns`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/azd-patterns) · [`foundry-observability`](https://github.com/aiappsgbb/awesome-gbb/tree/main/skills/foundry-observability) | Hosting, deploy hooks, and OTel wiring the deploy leg builds on. |

Install both plugins for the full pipeline:

```bash
copilot plugin marketplace add aiappsgbb/awesome-gbb
copilot plugin install awesome-gbb@awesome-gbb

copilot plugin marketplace add aiappsgbb/threadlight-skills
copilot plugin install threadlight-skills@threadlight-skills
```

## Live experience

The [Threadlight experience page](https://aiappsgbb.github.io/threadlight-skills/)
showcases what the pipeline produces.

## License

[MIT](LICENSE)
