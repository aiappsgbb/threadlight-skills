# 1-Hour Workshop — Threadlight Quickstart

> **Outcome by minute 60:** both Copilot plugins installed, a retail
> returns-triage PoC **designed from scratch** in the room, running locally
> against your own Azure OpenAI deployment, killer demo prompts answered live
> on screen, and `threadlight-deploy` + `azd up` kicked off to Azure —
> the longest single step, but still inside the hour, with provisioning
> progress visible in the Azure Portal while you wrap up Q&A.

The workshop runs through **Copilot CLI prompts** — the skills do the work
(installs, env files, process management, deploys). You watch the tool calls
fly past; you don't type shell commands.

---

## Contents

1. [What we'll build](#1-what-well-build)
2. [Pre-flight checklists](#2-pre-flight-checklists)
3. [Minute-by-minute runbook](#3-minute-by-minute-runbook)
4. [Prompt appendix](#4-prompt-appendix)
5. [Recovery prompts](#5-recovery-prompts)
6. [Deploy to Azure: `azd up`](#6-deploy-to-azure-azd-up)

---

## 1. What we'll build

This hour walks the **engineering chain** end-to-end:
`threadlight-design` → `SPEC.md` → Pattern 0 local boot → `threadlight-deploy`
→ `azd up` → governed Foundry agent. You'll spec a retail returns-triage
process from scratch with Copilot CLI, watch that markdown specification
become an MCP-backed agent answering live triage prompts on `localhost:8501`,
and leave with a one-prompt recipe to stand the same agent up in Azure.

A heads-up: the skills carry a few labels from the team that authored them —
**GBB**, **seller** / **SE**, **pilot** / **pre-pilot**, **prep guide**.
They show up in CLI output and skill prompts. Treat them as flavor text, not
configuration you have to fill in. The engineering they describe — a solution
engineer building and shipping a PoC — is what you're doing in this hour.

---

## 2. Pre-flight checklists

### T-24h — verify before the workshop

You'll need the following installed and signed in on the laptop you'll use
in the session:

- **Python 3.13.** (3.10+ works for the Pattern 0 package per its
  `pyproject.toml`, but the rest of the chain pins 3.13.)
- **GitHub CLI** and signed in.
- **Azure CLI** and signed in to a tenant where you have an AOAI deployment.
- **Copilot CLI** — this is the primary surface for the workshop.
- **Azure OpenAI deployment** you can reach: have the **endpoint URL** and the
  **deployment name** copy-pastable. The skill uses `DefaultAzureCredential`
  (Entra), not an API key — no secret handling.
- **Git** + an editor of your choice.

### T-1h — first thing in the room

Open Copilot CLI on the demo laptop and prompt:

> verify python 3.13, gh, az and copilot CLI are installed and signed in, and tell me which tenant az is pointed at

If anything's red, fix it now — don't try to fix it inside the runbook.

---

## 3. Minute-by-minute runbook

| Minute | Block | What we do | Outcome on screen |
|---|---|---|---|
| **0–5** | Welcome + frame | Walk §1 together: what we'll build and the vocabulary heads-up. | Slide-less; just the doc. |
| **5–10** | Validate prereqs | Run the T-1h prompt (§2). | Green tool output in Copilot CLI. |
| **10–15** | Install both plugins | Prompt §4.1. | Plugin list shows both. |
| **15–30** | Design the use case live | Prompt §4.2. Watch Copilot CLI invoke `threadlight-design` Fast-PoC, answer its setup questions live, and watch `specs/SPEC.md`, `AGENTS.md`, `src/agent/skills/`, `specs/sample-data/`, and `tests/killer-prompts.md` stream to disk. Read them together as they land. *If it's still in demo-deck / sales-kit generation past ~minute 25, interrupt with "skip demo and sales kit, move on" to protect the budget.* | A fresh `returns-triage` PoC: SPEC + AGENTS.md, agent skill folders, JSON sample-data, killer demo prompts. Deployment artifacts (Dockerfile, `azure.yaml`, `infra/main.bicep`) come later from `threadlight-deploy` — §6. |
| **30–40** | Pattern 0 bootstrap | Prompt §4.3 with your AOAI endpoint + deployment. | Two clean exits (`--info`, `--check`). The `--info` table shows the auto-discovered entities and CRUD tools. |
| **40–55** | Live demo on `localhost:8501` | Prompt §4.4, then paste each killer prompt from `tests/killer-prompts.md` (§4.5) into the browser in order. Watch the trace: SPEC-derived `list_*` / `get_*` / `update_*` tools fire without anyone writing them. | Streamlit chat. One answer per killer prompt — same agent, different signals in, different outcomes per branch. |
| **55–60** | Kick off `threadlight-deploy` + `azd up` + Q&A | Run §6 — `threadlight-deploy` then `azd up`. This is the longest single step but still fits the hour. While it runs, keep the **Azure Portal resource group view** visible alongside the Copilot CLI: the resource group fills in (Foundry account + project, Container App for MCP, hosted-agent container build, Cosmos) as `azd` provisions. Point at `threadlight-safe-check`, `threadlight-production-ready`, `threadlight-auto`, `citadel-spoke-onboarding` for the production path. | Resource group populating live; "OK, one prompt stands all of that up. Got it." |

`threadlight-deploy` + `azd up` is the **longest single step** (15–25 min
depending on region and remote-build cache state) but still fits the hour.
The Copilot CLI shows tool calls; the **Azure Portal resource group view**
shows provisioning progress in real time — keep both visible. The agent
is reachable for cloud-side smoke tests (§6.4) once the hosted-agent
container finishes building.

---

## 4. Prompt appendix

Type each prompt verbatim into Copilot CLI. The skills handle the mechanics
(plugin installs, `pip install`, env files, process launch). Copilot CLI shows
every tool call so you can see what's running.

### 4.1 Install both Copilot plugins

> install the awesome-gbb and threadlight-skills plugins from the aiappsgbb org on github, then list my installed copilot plugins to confirm

### 4.2 Design the use case live

> use the threadlight-design skill in fast-PoC mode to design a retail returns-triage process in ~/Repos/workshop/returns-triage — answer its setup questions interactively as it asks them, and skip browser previews of any generated demo or sales-kit HTML

Fast-PoC asks 2–3 essentials, assumes sensible defaults, and produces
everything in one pass. It's the right mode here because returns-triage is a
**basic scenario** (read-only routing, no heavy compliance weight) — for a
regulated, multi-phase, or consequential use case the skill triages first and
steers you to Full mode instead. The live design conversation **is** the demo —
attendees see the branches and entities get picked, not handed pre-baked.
Design stops at the **spec + agent code**; the azd scaffold (Dockerfile,
azure.yaml, infra/main.bicep) is `threadlight-deploy`'s job in §6, when there's
time for `azd up` to also run.

### 4.3 Bootstrap Pattern 0 against your Azure OpenAI

> use the threadlight-local-test skill to set up pattern 0 quickstart for ~/Repos/workshop/returns-triage with LLM_BACKEND=aoai, AZURE_OPENAI_ENDPOINT=https://<your-aoai-resource>.openai.azure.com, and AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>, then run --info and --check

Auth is `DefaultAzureCredential` — no API key. The `az login` from §2 is what
gets used.

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

> use the azure-tenant-isolation skill to isolate the azure cli to tenant <tenant-id> and switch to subscription <sub-id>, then confirm with az account show

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

Kick this off in the closing minutes of the workshop (or run standalone
after the session). The Copilot CLI surface is the same as the rest of
the workshop — prompts in, tool calls flying past. **Keep the Azure Portal
resource group view open in a second tab** so you can watch provisioning
fill in live while `azd` runs.

### 6.1 Prereqs

- Azure CLI + Azure Developer CLI installed
- `az login` and `azd auth login` completed for the target tenant
- An AOAI-capable region picked (`eastus2`, `swedencentral`, etc.)

### 6.2 Configure the azd environment

> in ~/Repos/workshop/returns-triage, install the azd ai agent extension, create a new azd environment named returns-triage, and set AZURE_LOCATION=<region>, AZURE_RESOURCE_GROUP=rg-returns-triage, AZUREAI_ACCOUNT_NAME=ai-returns-triage

### 6.3 Deploy

> use the threadlight-deploy skill to azd up ~/Repos/workshop/returns-triage to azure

Provisions the resource group, creates the Foundry account + project, builds
and deploys the MCP Container App, builds the hosted-agent container remotely,
and resolves `${SERVICE_MCP_FQDN}` into the agent environment.

Expect ~3 min of `azure-tenant-isolation` handshake (tenant index lookup,
subscription assertion, `AZURE_CONFIG_DIR` setup) **before** provisioning
actually starts. That's the safety gate, not a hang.

### 6.4 Smoke test

> run the killer prompts from ~/Repos/workshop/returns-triage/tests/killer-prompts.md against the deployed returns-triage agent — same prompts, now hitting the cloud agent over the Invocations protocol

### 6.5 Production-grade follow-ups

When you're ready to harden:

- **Production-readiness hand-off** — `threadlight-production-ready` is the
  bridge between a green `threadlight-safe-check --phase post-deploy` and a
  customer architecture / CISO review. Walks the pilot across 13 pillars
  (network, AGT, IAM, secrets, observability, evals, RAI, HITL, supply chain,
  cost, reliability, SRE handover, model lifecycle), defaulting to AI Citadel
  spoke posture, and produces an advisory scorecard + uplift plan +
  customer-facing hand-off package. Soft-gate — never fails the deploy.
  - **AGT v4 detection is automatic** (`--agt-profile auto`): if the pilot
    declares `agent-governance-toolkit-{core,runtime,sre,cli}` deps or uses
    the v4 ACS `intervention_points:` policy schema, six v4-specific deep
    checks fire (distribution names, ACS schema, dynamic policy conditions,
    `toolkit-version:` Action pin, audit-field shape). v3.7-shape pilots
    keep the version-agnostic check set — no false-fails.
  - **Per-evidence freshness** is stamped on every live probe and surfaced
    in the executive summary when the oldest evidence is older than
    `--freshness-hours` (default 24h). Run the skill again before the
    customer call if your safe-check evidence is stale from a previous day.
- **Governance / model routing** — `citadel-spoke-onboarding` skill in
  `awesome-gbb`. Routes model traffic through a shared APIM AI Gateway and
  flips `AZURE_AI_MODEL_DEPLOYMENT_NAME` to the `connectionName/deploymentName`
  form during spoke onboarding.
- **Pre-deploy gates** — `threadlight-safe-check` validates every resource
  selector before go-live.
- **Continuous eval** — `foundry-evals` consumes `tests/quickstart.jsonl`
  (which Pattern 0 has been quietly writing the entire workshop).
- **Observability** — `foundry-observability` wires App Insights + OTel into
  hosted-agent + MCP without changing app code.
- **End-to-end automation** — `threadlight-auto` orchestrates the full
  design → local-test → deploy → safe-check → evals pipeline as one prompt.
