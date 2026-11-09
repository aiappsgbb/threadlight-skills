# 1-Hour Workshop — Threadlight Quickstart

> **Outcome by minute 60:** a retail returns-triage PoC **designed live** in
> the room (or the prepared design if that overruns), running locally against
> your own Azure OpenAI deployment with killer prompts answered on screen, the
> deploy package generated, and `azd up` running or finished. The Foundry
> evaluation and the evidence record finish **after the session**.

The workshop runs through **Copilot CLI prompts** — the skills do the work
(installs, env files, process management, deploys). You watch the tool calls
fly past; you don't type shell commands.

---

## Contents

1. [What we'll build](#1-what-well-build)
2. [Before the session](#2-before-the-session)
3. [Minute-by-minute runbook](#3-minute-by-minute-runbook)
4. [Prompt appendix](#4-prompt-appendix)
5. [Recovery prompts](#5-recovery-prompts)
6. [Deploy to Azure: `azd up`](#6-deploy-to-azure-azd-up)

---

## 1. What we'll build

This hour walks the **engineering chain** end-to-end:
`threadlight-design` → `SPEC.md` → Pattern 0 local boot → `threadlight-deploy`
→ `azd up` → deployed, smoke-tested pilot (after deployment completes).
This is **not automatically a governed Foundry agent**. You'll spec a retail returns-triage
process from scratch with Copilot CLI, watch that markdown specification
become an MCP-backed agent answering live triage prompts on `localhost:8501`,
and leave with a one-prompt recipe to stand the same agent up in Azure.
The pilot is **recommend-only**: the agent proposes the decision with its
policy citations, and recording or applying it stays a human action. It has no
write tool, so there is no unenforced write to govern in the hour; agent writes
with runtime governance are the Production path (§6.5).
Model-backed local demos and cloud smoke tests require approved identity,
target and spend; neither proves production readiness or selected-action enforcement.

A heads-up: the skills carry a few labels from the team that authored them —
**GBB**, **seller** / **SE**, **pilot** / **pre-pilot**, **prep guide**.
They show up in CLI output and skill prompts. Treat them as flavor text, not
configuration you have to fill in. The engineering they describe — a solution
engineer building and shipping a PoC — is what you're doing in this hour.

---

## 2. Before the session

The whole chain does not fit in one hour. Measured on 2026-11-06/07 with
Copilot CLI 1.0.105, Claude Opus 5.5 (medium reasoning) and this
returns-triage scenario:

| Step | Measured |
|---|---|
| `threadlight-design` Fast-PoC (questions, SPEC, skills, data, prompts) | 16–19 min |
| `threadlight-local-test` Pattern 0 setup + killer prompts | 4–6 min |
| `threadlight-deploy` generate-only | 1–4 min |
| Live: `azd up` (~5 min) + cloud killer prompts + Foundry eval of 28 rows (15–20 min) + evidence record | ~33 min |

These are single measurements. Your timings will vary with the model,
network, region, quota and how long the design conversation runs. So prepare
everything that is not the demo **before** the session.

### T-24h — on every laptop that will drive the demo

- **Tools signed in:** Python 3.13 (3.10+ works for the Pattern 0 package per
  its `pyproject.toml`, but the rest of the chain pins 3.13), Git, GitHub CLI,
  Copilot CLI, Azure CLI with `az login` and Azure Developer CLI with
  `azd auth login`, both in the tenant you will deploy to.
- **Plugins installed** in the right order (§4.1): threadlight-skills first,
  then the official Azure plugins.
- **Azure OpenAI deployment** you can reach: have the **endpoint URL** and the
  **deployment name** copy-pastable. Pattern 0 uses `DefaultAzureCredential`
  (Entra), not an API key.
- **Python packages reachable.** Pattern 0 installs `agent-framework` and
  `streamlit` from PyPI. Corporate laptops often block public PyPI: test one
  install the day before, and if it is blocked use the approved package
  mirror or proxy your organisation provides, for that install only. Do not
  change global pip/uv settings or disable security controls in the room.
- **Deployment approval** for the subscription, resource group, identity, model
  cost and cleanup owner (§6.1).
- **Fallback workspace.** Run the design once (§4.2) and commit the result as a
  **pre-designed workspace** (for example a branch or a zip of
  `~/Repos/workshop/returns-triage`). If the live design overruns, the room
  continues from it.
- **Optional: pre-provision** the Azure environment the day before (§6.2, then
  `azd provision` on the fallback workspace) so the in-room `azd up` only
  builds and deploys. Delete it when the workshop is over.

### T-1h — first thing in the room

Open Copilot CLI on the demo laptop and prompt:

> verify python 3.13, gh, az, azd and copilot CLI are installed and signed in, and tell me which tenant az is pointed at

If anything's red, fix it now — don't try to fix it inside the runbook.

---

## 3. Minute-by-minute runbook

The budget below uses the measured times, with the upper bound for design.

| Minute | Block | What we do | Outcome on screen |
|---|---|---|---|
| **0–5** | Welcome + frame | Walk §1 together: what we'll build and the vocabulary heads-up. | Slide-less; just the doc. |
| **5–8** | Check the setup | Run the T-1h prompt (§2). Plugins, logins and the AOAI deployment were prepared before the session. | Green tool output in Copilot CLI. |
| **8–27** | Design the use case live | Prompt §4.2. Watch Copilot CLI invoke `threadlight-design` Fast-PoC, answer its setup questions live, and watch `specs/SPEC.md`, `AGENTS.md`, `src/agent/skills/`, `specs/sample-data/`, and `tests/killer-prompts.md` land. *If it's still in demo-deck / sales-kit generation past ~minute 22, interrupt with "skip demo and sales kit, move on".* **Fallback:** at minute 27, if the design is not finished, switch to the pre-designed workspace (§2) and continue. | A fresh `returns-triage` PoC: SPEC + AGENTS.md, agent skill folders, JSON sample-data, killer demo prompts. |
| **27–33** | Pattern 0 + live demo | Prompts §4.3 and §4.4, then paste the killer prompts (§4.5) into `localhost:8501`. Watch the SPEC's own read tools (for example `returns_get_case`, `return_policy_search`) fire against the sample data and policy text. The agent proposes the decision with citations; recording it is your action, not the agent's (recommend-only pilot). | Streamlit chat: one cited recommendation per killer prompt. |
| **33–37** | Generate the deploy package | Prompt §6.3 up to generate-only (Dockerfiles, `azure.yaml`, `infra/main.bicep`, evals). | `DEPLOY.md` and `specs/deferred-steps.md` on disk. |
| **37–42** | Start `azd up` before Q&A | After scope/budget approval (§6.1), run `azd up` (§6.3). Keep the **Azure Portal resource group view** alongside Copilot CLI. | Provisioning and remote build progress. |
| **42–60** | Q&A, then cloud smoke test | Q&A while `azd up` runs (~5 min measured). When it finishes, run the killer prompts against the cloud agent (§6.4). Point to the Production path (§6.5): governance is a separate track. | Cloud answers on screen; the Foundry eval (15–20 min) and the evidence record run **after the session**. |

If the hour is shorter than planned, cut in this order: the cloud smoke test
(run it after the session), then the live design (use the pre-designed
workspace from minute 8).

---

## 4. Prompt appendix

Type each prompt verbatim into Copilot CLI. The skills handle the mechanics
(plugin installs, `pip install`, env files, process launch). Copilot CLI shows
every tool call so you can see what's running.

### 4.1 Install the Copilot plugins

> install the threadlight-skills plugin from aiappsgbb/threadlight-skills first, then the official azure, azure-cost and foundry-iq-skills plugins from microsoft/azure-skills, then list my installed copilot plugins to confirm

Install threadlight-skills before other skill-heavy plugins so its skills stay
visible; see the README's [Install](../README.md#install) section. The optional
`awesome-gbb` patterns are not needed for this workshop.

### 4.2 Design the use case live

> use the threadlight-design skill in fast-PoC mode to design a retail returns-triage process in ~/Repos/workshop/returns-triage — answer its setup questions interactively as it asks them, and skip browser previews of any generated demo or sales-kit HTML

Fast-PoC asks 2–3 essentials, assumes sensible defaults, and produces
everything in one pass. Returns triage touches refunds, so the skill may name
that signal and offer Full mode; for the workshop, keep Fast-PoC. A Fast-PoC is
**recommend-only**: SPEC § 6 lists read tools only, SPEC § 11a records
governance as not selected (`mode: off`, reads unbound), and the would-be
"record the recommendation" write becomes a human step in SPEC § 8. The killer
prompts and evals expect a cited recommendation, not a stored record. The live design conversation **is** the demo —
attendees see the branches and entities get picked, not handed pre-baked.
Design stops at the **spec + agent code**; the azd scaffold (Dockerfile,
azure.yaml, infra/main.bicep) is `threadlight-deploy`'s job in §6, when there's
time for `azd up` to also run.

### 4.3 Bootstrap Pattern 0 against your Azure OpenAI

> use the threadlight-local-test skill to set up pattern 0 quickstart for ~/Repos/workshop/returns-triage with LLM_BACKEND=aoai, AZURE_OPENAI_ENDPOINT=https://<your-aoai-resource>.openai.azure.com, and AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>, then run --info and --check

Auth is `DefaultAzureCredential` — no API key. The `az login` from §2 is what
gets used.

Pattern 0 runs the design's `AGENTS.md`, the SPEC § 6 tool names (the
design's mock server functions, with their safeguards) and a read-only local
search over `specs/knowledge/` for the policy tool. In a recommend-only pilot
there is no write tool to approve: `--check` lists read tools only. To rehearse
without the browser:

> run the killer prompts from ~/Repos/workshop/returns-triage/tests/killer-prompts.md headlessly with python -m threadlight_quickstart --ask, one --ask per prompt, and summarise the proposed decisions and their citations

Each answer should end with a proposed decision, the business rule and the
policy clause, and say that a person records it.

### 4.4 Launch the local agent

> launch the threadlight pattern 0 streamlit quickstart for ~/Repos/workshop/returns-triage

### 4.5 The killer demo prompts (paste into Streamlit, not Copilot CLI)

> open ~/Repos/workshop/returns-triage/tests/killer-prompts.md and show me the killer prompts — I'll paste them into Streamlit one at a time

The killer prompts reference IDs from **your own** freshly designed
`specs/sample-data/` (not someone else's seed data), so they resolve cleanly
against the just-booted agent. Run them in the order the file lists — the
arc lands the message: same agent, different signals in the input, different
outcomes per branch.

---

## 5. Recovery prompts

Four failure modes you're statistically likely to hit live. Each one is a
single prompt to Copilot CLI — the right skill handles the fix.

### 5.1 `az login` lands in the wrong tenant

Symptom: Copilot CLI reports `az account show` is pointed at a different tenant
than the AOAI deployment lives in.

> use the threadlight-tenant-isolation skill to isolate the azure cli to tenant <tenant-id> and switch to subscription <sub-id>, then confirm with az account show

### 5.2 AOAI quota / HTTP 429 mid-demo

Symptom: Streamlit shows a `RateLimitError` from the LLM call.

> switch the running pattern 0 backend from aoai to copilot github models — refresh gh auth with scope `models`, export GITHUB_TOKEN, set LLM_BACKEND=copilot in .env.local, and relaunch streamlit

Recovery path only — answers will look slightly different from what you'll see
on your own AOAI later.

### 5.3 Port 8501 already in use

> relaunch the pattern 0 streamlit quickstart on port 8511 instead of 8501

### 5.4 Python is older than 3.13

> create a uv 3.13 venv inside ~/Repos/workshop/returns-triage, activate it, and reinstall the threadlight-local-test pattern 0 quickstart package into it

### 5.5 Copilot CLI asks to install Playwright mid-design

Symptom: at the end of `threadlight-design`, Copilot CLI tries to render
`specs/demo-deck.html` in a browser, can't find Playwright, and offers to
install it (~3 min). Say no — the artifact doesn't need a real render.

> skip the playwright install and the browser preview — just `cat` or `view` any generated HTML instead, and continue

---

## 6. Deploy to Azure: `azd up`

Generate the package at minute 33 and start `azd up` before Q&A (§3); the
Foundry evaluation runs after the session. The Copilot CLI surface is the same
as the rest of the workshop — prompts in, tool calls flying past. **Keep the Azure Portal
resource group view open in a second tab** so you can watch provisioning
fill in live while `azd` runs.

### 6.1 Prereqs

- Azure CLI + Azure Developer CLI installed
- `az login` and `azd auth login` completed for the target tenant
- An AOAI-capable region picked (`eastus2`, `swedencentral`, etc.)
- Explicit approval for the selected tenant/subscription/resource group,
  identity, model costs, resource creation and cleanup owner. Stop on missing
  quota or permissions; do not broaden scope or grant roles as a workshop fix.

### 6.2 Configure the azd environment

> in ~/Repos/workshop/returns-triage, install the azd ai agent extension, create a new azd environment named returns-triage, and set AZURE_LOCATION=<region>, AZURE_RESOURCE_GROUP=rg-returns-triage, AZUREAI_ACCOUNT_NAME=ai-returns-triage

### 6.3 Deploy

Generate the package first (measured 1–4 min):

> use the threadlight-deploy skill in generate-only mode on ~/Repos/workshop/returns-triage, without --force, then stop and show me DEPLOY.md

Then start the deployment (measured `azd up` ~5 min):

> use the threadlight-deploy skill to azd up ~/Repos/workshop/returns-triage to azure

The skill runs generate-only on the design workspace, without `--force`.
It keeps the design's `src/mcp/server.py` byte-identical and adds only the
pinned packaging around it. A recommend-only pilot is packaged without a write tool and without a
governance gap; generate-only refuses (exit 6) a recommend-only SPEC that still
exposes a write. SPEC § 11c modules the pilot infrastructure does
not create (for example AI Search and the Foundry IQ knowledge base) are listed
as not provisioned in `specs/deferred-steps.md`; until `threadlight-ground`
runs, the policy tool answers from the packaged `specs/knowledge/` text.

Provisions the resource group, creates the Foundry account + project, builds
and deploys the MCP Container App, builds the hosted-agent container remotely,
and resolves `${SERVICE_MCP_FQDN}` into the agent environment.

If the skill first runs the `threadlight-tenant-isolation` handshake (tenant
index lookup, subscription assertion, `AZURE_CONFIG_DIR` setup) before
provisioning starts, that's the safety gate, not a hang.

### 6.4 Smoke test

> run the killer prompts from ~/Repos/workshop/returns-triage/tests/killer-prompts.md against the deployed returns-triage agent — same prompts, now hitting the cloud agent over the Invocations protocol

After the session, run the Foundry evaluation (28 rows measured at 15–20 min)
and record the evidence:

> in ~/Repos/workshop/returns-triage, follow DEPLOY.md to record the deploy evidence (scripts/record_deploy_evidence.py with the trace id) and run python evals/run_evals.py, then summarise the eval run id, the rubric score and anything still missing

When you are done, delete the resource group you created (or hand it to its
cleanup owner); do not leave the pilot running unattended.

### 6.5 Selected-binding governance and production follow-ups

The deployed/smoke-tested pilot is the workshop endpoint, not a governance pass.
Use the [L400/L500 operative guide](agent-operations.md) for the separate
**selected-binding governance** track:

1. Configure an explicit current governance contract and signed/fresh policy,
   trusted backend facts, required approval and central audit services.
2. Generate the selected native host/gateway with the
   [current generator](../skills/threadlight-deploy/references/governance/README.md)
   and [published pins](../skills/_shared/governance-upstream-pin.json).
   Dependency/import detection alone does not establish enforcement.
3. Validate executed **LOCAL-14** and exact native/CTK contracts locally.
   Local success is not hosted evidence or authorization to deploy.
4. Obtain separate deployment/collector approval and satisfy registered
   fixture, identity, network, immutable image and signed-bootstrap prerequisites.
   Collect fresh deployment-bound evidence; `governance_probe_noop` proves only
   its reserved noop. Business bindings remain live-unverified until separately
   proved. Do not use another `azd up` after binding as a readiness shortcut.

Then review the broader readiness work:

- **Production-readiness hand-off** — `threadlight-production-ready` is the
  bridge between a green `threadlight-safe-check --phase post-deploy` and a
  customer architecture / CISO review. Walks the pilot across 13 pillars
  (network, governance, IAM, secrets, observability, evals, RAI, HITL, supply chain,
  cost, reliability, SRE handover, model lifecycle), resolves posture from the
  declared target, and produces an advisory scorecard. Missing SPEC §12 falls
  back to `standard-ai-gateway` with `RDY-002`, not an implicit Citadel pass.
  Use the [pinned complete-catalog invocation](production-readiness.md#9-cli-cheatsheet).
  Prerequisite failures are nonzero; `--gate-preview` also exits `2` on raw
  must-fix findings. Legacy `--agt-profile` diagnostics are not current v1 proof.
  - **Per-evidence freshness** is stamped on every live probe and surfaced
    in the executive summary when the oldest evidence is older than
    `--freshness-hours` (default 24h). Run the skill again before the
    customer call if your safe-check evidence is stale from a previous day.
- **Ship to prod through a pipeline** — `threadlight-cicd` generates the
  production deploy pipeline (GitHub Actions / Azure DevOps) plus the
  env-setup runbooks the customer's platform team runs: a UAMI with
  federated credentials (OIDC/WIF — no client secret), least-privilege RBAC
  scoped to the spoke RG, and managed or self-hosted private-VNet runners.
  For real customer tenants where the agent can't run `azd up` itself. A
  deliberate **manual handoff** after a green scorecard, and a **separate
  repo/pipeline** from the central platform — it never touches the Citadel
  hub, shared APIM, or platform networking (those stay `threadlight-citadel-hub`).
- **Governance / model routing** — the Threadlight-owned
  `threadlight-citadel-spoke` skill (optional). Routes model traffic through a shared APIM AI Gateway and
  flips `AZURE_AI_MODEL_DEPLOYMENT_NAME` to the `connectionName/deploymentName`
  form during spoke onboarding.
- **Pre-deploy gates** — `threadlight-safe-check` validates every resource
  selector before go-live.
- **Continuous eval** — `microsoft-foundry` consumes `tests/quickstart.jsonl`
  (which Pattern 0 has been quietly writing the entire workshop).
- **Observability** — `threadlight-deploy/references/observability` wires App Insights + OTel into
  hosted-agent + MCP without changing app code.
- **End-to-end automation** — `threadlight-auto` orchestrates the full
  design → local-test → deploy → safe-check → evals pipeline as one prompt.
