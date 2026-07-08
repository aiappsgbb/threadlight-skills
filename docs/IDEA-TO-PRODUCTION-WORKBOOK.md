# Idea → Production Workbook

> **"How do I test it?"** — this is the answer. A self-paced runbook that
> walks the **exact chain** from the [case study](./case-study.html): one
> paragraph of intent → a working agent on **your own Azure subscription**
> → the documented path to **governed production** behind a Citadel hub.
>
> You drive it the way the real run did — **prompts into Copilot CLI**, the
> skills do the work. You watch the tool calls, read the artifacts, and
> re-derive the numbers yourself. No copy-pasting shell commands.

The workbook splits into two halves:

| | What you get | Where it runs | Time | Cost |
|---|---|---|---|---|
| **Part 1 — Hands-on to MVP** | A live, governed-ready agent you invoked yourself | **Your own Azure subscription** | ~60 min | A few $ of model + a Container App |
| **Part 2 — The production track** | The real prompts that took the pilot to a governed Citadel spoke | Needs a **Citadel hub** + budget | A few hours | Real Azure spend |

Part 1 is fully reproducible solo today. Part 2 is **documented with the
real prompts** from the run, flagged where it needs a Citadel governance hub
you don't control. The [case study](./case-study.html) is what Part 2 looks
like when it's done.

The running example is the case study's own: an **SMB credit-memo analyst**
for a (fictional) commercial bank. Swap the brief in Step 2 for your own
process — every later prompt stays the same.

---

## Contents

- [Before you start](#before-you-start)
- [The arc at a glance](#the-arc-at-a-glance)
- [Part 1 — Hands-on to a working MVP](#part-1--hands-on-to-a-working-mvp)
  - [Step 1 · Install the plugins, launch on a strong model](#step-1--install-the-plugins-launch-on-a-strong-model)
  - [Step 2 · Design — your paragraph becomes a spec](#step-2--design--your-paragraph-becomes-a-spec)
  - [Step 3 · Local test — run it before you deploy it](#step-3--local-test--run-it-before-you-deploy-it)
  - [Step 4 · Deploy — the idea goes live on your sub](#step-4--deploy--the-idea-goes-live-on-your-sub)
  - [Step 5 · Invoke — the two cases that prove it](#step-5--invoke--the-two-cases-that-prove-it)
- [Part 2 — The production track](#part-2--the-production-track)
- [Recovery prompts](#recovery-prompts)
- [Go deeper](#go-deeper)

---

## Before you start

You need these installed and signed in on the machine you'll use:

- **Copilot CLI** — the primary surface. Run it on a **current-generation
  model**: this workbook assumes **`claude-opus-4.8`** (the default) or
  **`gpt-5.5`**. The model that drives the chain matters — a weaker coding
  agent produces a weaker spec and misses its own bugs.
- **GitHub CLI** (`gh`) — signed in. Used to install the plugins and, in the
  fallback path, to reach GitHub Models.
- **Azure CLI** (`az`) — signed in to a subscription **you can deploy to**.
  The skills use `DefaultAzureCredential` (Entra) — no API keys.
- **Python 3.13** — the local-test harness pins it.
- **An Azure OpenAI deployment** you can reach (endpoint + deployment name),
  *or* accept the GitHub-Models fallback for the local step.
- **Git** + an editor.

**One-prompt pre-flight** — open Copilot CLI and paste:

```
verify python 3.13, gh, az and copilot CLI are installed and signed in,
and tell me which subscription az is pointed at
```

If anything's red, fix it before Step 1 — not inside the runbook.

> **A vocabulary heads-up.** The skills carry labels from the team that wrote
> them — *GBB*, *seller* / *SE*, *pilot* / *pre-pilot*, *prep guide*. They're
> flavor text in the output, not configuration you fill in. The engineering
> they describe — spec, build, deploy, govern — is exactly what you're doing.

---

## The arc at a glance

```
  ┌─────────────── HANDS-ON TODAY · your subscription ───────────────┐
  IDEA ──▶ DESIGN ──▶ LOCAL TEST ──▶ DEPLOY (azd up) ──▶ INVOKE
  paragraph  spec      runs on a       live MVP on        two golden
             + agent   laptop          your sub           cases proven
  └──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌────────── PRODUCTION TRACK · documented · needs a Citadel hub ─────┐
  SAFE-CHECK ─▶ COST ─▶ DISCOVER ──────▶ PROTECT ─▶ OBSERVE ─▶ READY ─▶ SPOKE ─▶ PROD ─▶ CI/CD ─▶ HANDOFF
   the honest   real    evals + red-team   AGT       ~2k       gap-to-   keyless   no model  pipeline  runbooks
   gate         $/mo    (quality + scan)   runtime   traces    stage     spoke*    of its    holds     + map
                                           governance           map                own*      the keys
  └────────────────────────────────────────────────────────────────────┘
       DISCOVER · PROTECT · GOVERN · IMPROVE — the Responsible-AI-for-Foundry loop, made executable
```

Part 1 is the inner loop — idea to a live, invokable agent you stood up
yourself. Part 2 is everything that turns a working pilot into something a
bank's architecture review board signs off on.

---

## Part 1 — Hands-on to a working MVP

Five steps, each one prompt into Copilot CLI. After each, a **"you'll know it
worked when…"** checkpoint so you can tell green from broken without guessing.

### Step 1 · Install the plugins, launch on a strong model

```
install the awesome-gbb and threadlight-skills plugins from the aiappsgbb
org on github, then list my installed copilot plugins to confirm
```

Then launch Copilot CLI on a current-generation model so the inner agent is
strong enough to spec and self-correct (this is how the real run started):

```
copilot --model claude-opus-4.8 --effort high \
        --plugin-dir ./threadlight-skills --plugin-dir ./awesome-gbb
```

**✅ Worked when:** the plugin list shows **both** `threadlight-skills` and
`awesome-gbb`, and Copilot reports the active model is `claude-opus-4.8`
(or `gpt-5.5`).

### Step 2 · Design — your paragraph becomes a spec

This is the verbatim prompt that started the real run. **Swap the BRIEF** for
your own process and keep the rest:

```
Use the threadlight-design skill to design a pilot agent end-to-end from
this brief.

BRIEF
A commercial bank's SMB lending team wants to compress credit-memo
preparation. For an incoming loan request, the agent should: (1) pull the
borrower's financials and existing exposure, (2) compute standard credit
metrics (DSCR, leverage, liquidity) and score the request against the
bank's lending policy, (3) flag policy exceptions and risk factors, and
(4) draft a structured credit memo that a credit officer reviews and signs
off. A human-in-the-loop approval gate is required before any memo is
finalized, and every step must be auditable.

CONTEXT
- Fictional customer "Meridian Commercial Bank", SMB commercial lending.
- This is a PILOT we will take to production on Azure later in the same
  session, onboarded as a spoke into an existing Citadel AI governance hub.
  Design with that production path in mind (governance, auditability, PII,
  HITL) but keep the pilot itself lean.
- Region: swedencentral.

MODEL POLICY: use a current-generation model (gpt-5 family). The production
deployment will route LLM calls through the Citadel AI gateway.

Produce the full threadlight-design output set: specs/SPEC.md,
specs/manifest.json, AGENTS.md, the per-tool skills/ scaffold, and docs/.
```

If it drifts into generating a demo deck or sales kit and you're short on
time, interrupt with `skip the demo and sales kit, move on` to protect budget.

**✅ Worked when:** `specs/SPEC.md` exists with concrete business rules (the
credit-memo brief yields ~12 rules with real thresholds — DSCR ≥ 1.25×,
leverage ≤ 3.5×, a single-borrower limit, mandatory policy citation, no
self-approve), plus `AGENTS.md`, agent skill folders, and seeded
`specs/sample-data/`. **Zero `[NEEDS CLARIFICATION]` markers** is the bar.

> **Re-derive it yourself.** Open the sample data and recompute one headline
> ratio by hand. In the real run the spec's numbers were *correct, not just
> plausible* — they reproduced to two decimals. That's the test.

### Step 3 · Local test — run it before you deploy it

Prove the generated agent actually boots and produces correct answers **on a
laptop**, before spending a minute on Azure:

```
Use the threadlight-local-test skill to boot this PoC locally (Pattern 0 —
Quickstart) and run a real smoke test on the two golden demo cases. Prefer
GitHub Models (LLM_BACKEND=copilot, auth via gh) with a gpt-5-family model;
if none is offered, use an Azure OpenAI gpt-5-family deployment. Then run
--info and --check.
```

If you'd rather use your own Azure OpenAI directly:

```
Use the threadlight-local-test skill to set up Pattern 0 for this PoC with
LLM_BACKEND=aoai, AZURE_OPENAI_ENDPOINT=https://<your-aoai>.openai.azure.com,
and AZURE_OPENAI_DEPLOYMENT=<your-deployment>, then run --info and --check.
```

**✅ Worked when:** `--info` prints the auto-discovered entities and CRUD
tools, `--check` exits clean, and the smoke test reproduces the golden ratios
**exactly**, with a policy citation on every assessed line and no
self-approval. (This is the step that, in the real run, caught two genuine
bugs in the harness itself — because it actually *builds* the agent.)

### Step 4 · Deploy — the idea goes live on your sub

One `azd up` turns the project into a containerized Microsoft Foundry
**hosted agent** on your subscription:

```
Use the threadlight-deploy skill to azd up this project to Azure as a dev
environment (-e dev) on my current subscription, region swedencentral.
```

`threadlight-deploy` generates the `azd` scaffold (Dockerfile, `azure.yaml`,
`infra/main.bicep`) if it isn't there yet, provisions the resource group,
creates the Foundry account + project, builds and deploys the MCP Container
App, and builds the hosted-agent container remotely. **Keep the Azure Portal
resource-group view open** in a second tab — you'll watch it fill in live.

**✅ Worked when:** `azd up` finishes green and the resource group shows a
Foundry account + project, a Container App, and the hosted-agent container.
This is the longest single step (~15–25 min, region/cache dependent).

> Two real failure modes the run hit here and recovered from: a post-deploy
> **404 name-lookup** (one key renamed in `azure.yaml`) and the **skipped
> role assignments** it caused. If you see either, the [recovery
> prompts](#recovery-prompts) cover them.

### Step 5 · Invoke — the two cases that prove it

Run the agent **live**, from the command line, on two contrasting cases — one
clean, one that trips every alarm:

```
Use azd ai agent invoke to run the live agent on the first golden case (the
clean borrower). Then invoke it again on the hard borrower — start a CLEAN
conversation for the second so it doesn't reuse the first one's context.
Show me both memos and where each was routed.
```

**✅ Worked when:** the clean borrower comes back as a polished memo, every
metric passing and cited, left at **`pending_review`** (a human signs off,
not the agent). The hard borrower trips its policy floors — each flagged with
threshold, value, and clause — and escalates. **That `pending_review` state
is the whole point:** the agent drafts, a person decides.

🎉 **That's the MVP.** You took a paragraph to a governed-ready agent running
on your own Azure, and you proved it yourself. Everything past here is the
road to production.

---

## Part 2 — The production track

> ⚠️ **Read first.** These are the **real prompts** from the run, in order.
> They're reproducible — but several stages **call a Citadel governance hub**
> you probably don't own, and the whole track spends real Azure money over a
> few hours. Treat this as the *documented* path: what to run, in what order,
> and what "good" looks like. The [case study](./case-study.html) is the
> evidence that it lands.

**Tags:** 🟢 runs on your own sub · 🟡 needs time + real spend · 🔵 needs a
Citadel hub you can onboard to.

### 6 · Safe-check — the gate that tells the truth 🟡

```
Use the threadlight-safe-check skill to run all phases against this project
and tell me, gap by gap, what's a real defect versus deliberately-deferred
hardening.
```

The mandatory completeness gate. In the run it came back **red on all three
phases** — and that was the most useful result so far: of 27 gaps, 26 were
hardening deliberately deferred to production, and exactly one was a real
(trivial) defect. **A red safe-check on a lean pilot is correct**, not a
failure — the gate is doing its job.

### 7 · Cost — what will this actually cost? 🟡

```
Use the threadlight-consumption-iq skill to forecast the monthly run cost of
this agent at realistic request volume, and break the bill down by resource.
```

The first headline in the run was **$39,343/mo — and wrong**, the model
priced as if it ran flat-out every second. The skill's own report caught it
and recomputed to **~$920/mo zone-redundant (or ~$125 with a documented
exception)**. The insight worth re-deriving: the **language model is a
rounding error (~$11/mo)** — the bill is driven by whether the policy search
index runs zone-redundant or single-replica.

### 8 · Evals — does it actually work? 🟡

```
Use the threadlight-evals skill to run the evals leg against the live agent:
offline batch (delegating to foundry-evals), online/continuous eval on live
threads (Foundry Continuous Evaluation → App Insights), and an A/B
champion–challenger gate before any model or prompt swap.
```

A real evaluation against the **live** agent. The run scored **14/15**: 100%
of seeded policy exceptions caught, a clause cited on 41/41 measured lines,
never self-finalized, 4.9/5 coherence — each memo ~20s versus 3–4 human
hours. The single "miss" was a **gap in the test, not the agent** (a scenario
with no borrower, where refusing to invent an identifier was correct). The
`threadlight-evals` leg wraps that offline run and adds the two pieces CAF
asks for: **online/continuous eval** on live threads (results to App Insights
with reasoning) and an **A/B comparison gate** so a model swap has to *prove*
uplift before it ships. The leg writes `specs/evals-manifest.json`, which
production-ready reads to confirm the leg actually ran.

### 8a · Red-team — has anyone tried to break it? 🟡

```
Use the threadlight-redteam skill to run the AI Red Teaming Agent adversarial
scan (jailbreak / prompt-injection / exfiltration) against the live agent and
write docs/redteam-report.md + specs/redteam-manifest.json.
```

This is the **Discover** control RAI-for-Foundry expects and the static
"is a jailbreak shield declared?" check can't replace: an automated,
PyRIT-backed adversarial scan that actually probes the deployed agent and
reports an attack-success rate per risk category. Results map straight to
production-ready pillar 7 (`SAFE-1xx`) so an un-scanned agent reads as
*not-verified*, not silently green.

### 8b · Govern — is the runtime actually governed? 🟡

```
Use the threadlight-govern skill to wire foundry-agt at the container
boundary: scaffold/validate the policy artefact, attach the in-process
governance middleware, and emit a committed verifier report
(specs/govern-manifest.json).
```

The **Protect** leg turns pillar 2 from "AGT scored, remediation delegated"
into "AGT runtime governance *ran* and left an artefact." It produces exactly
what pillars 2 and 7 look for — policy + middleware + verifier evidence — so
the scorecard can verify the leg ran rather than just recommending it.

### 9 · Observability — can we see what it's doing? 🟡

```
Use the foundry-observability skill to confirm the agent is emitting traces,
and show me the spans for the eval runs — model calls and tool calls.
```

Point it at the right tables and the picture lights up: ~2,000 trace records,
every eval run accounted for, each model and tool call its own span. **Heads
up:** in the run the stock diagnostic queries returned **zero rows on a
perfectly healthy agent** — they targeted the wrong tables. If your first
queries come back empty, the agent isn't necessarily broken; the query might
be.

### 10 · Production-ready — is it ready? 🟡

```
Use the threadlight-production-ready skill to score this pilot against a
private, gateway-fronted spoke target, and pin every gap to the stage that
closes it.
```

Scored against the bank's declared target, the lean pilot came back **not
ready, 45% — the right answer.** The valuable part: the report pinned each of
34 gaps to the exact later stage that closes it, rather than just listing
them. A 45% here is a correct read of a deliberately-lean pilot, not a fail.

### 11 · Citadel spoke — onboard behind the gateway 🔵

> Needs a Citadel AI governance hub you can onboard a spoke into.

First, probe the hub to choose your onboarding shape:

```
Probe the target Citadel hub: is the AI gateway reachable over the public
internet with private backends, or VNet-only? List which models it exposes
and flag any deprecated ones.
```

Then onboard the spoke as an infrastructure-as-code contract:

```
Use the citadel-spoke-onboarding skill to register this agent as an isolated
spoke product on the hub — keyless managed-identity connection, an allow-list
naming ONLY the current-generation models we approve, and preview the change
before applying. It must be purely additive: nothing modified or deleted on
the shared hub.
```

In the run this was **entirely additive** (6 to create, 0 to modify, 0 to
delete; the hub still listed all 23 existing products afterward). Then the
wall was proven: approved model → **200**; banned deprecated model → **403**,
refused.

> 🔴 **The finding to carry in:** the sample onboarding policy ships an
> allow-list that *includes* deprecated models. Copy the default blindly and
> you onboard with the wrong rule baked in. Name your approved models
> explicitly.

**Private networking:** the spoke here connects to the hub's **public
gateway** (TLS · JWT · allow-list), while the hub's model backends sit
**private behind private endpoints** — the shipped, additive-only posture. A
fully private **VNet-peered** spoke (spoke VNet ↔ hub VNet + Private Link) is
a first-class, **skill-covered** path — `foundry-vnet-deploy` plus
`citadel-spoke-onboarding`'s VNet-isolated (Option B) pattern — when the bank
is ready to peer.

### 12 · Production deploy — routed through the gateway 🔵

> Needs the onboarded spoke from Step 11.

```
Use the threadlight-deploy skill to deploy this agent to a prod environment
that borrows the hub's current-generation model THROUGH the Citadel gateway —
no model of its own. Then run both golden cases against live prod and check
the hub's own logs to confirm our calls are attributed to our product.
```

The production agent deploys **no model of its own** — it borrows the hub's
model through the gateway. That's the governed posture *and* it consumes zero
new model quota. Both golden cases matched to the decimal against live prod.

> 🔴 **The run's headline finding:** the playbook's example pointed the
> connection at the **wrong gateway surface** and the agent returned empty — a
> silent "resource not found." The real shape is an OpenAI-style endpoint with
> the model in the body (`/models`, not `/openai`). Re-pointed, redeployed,
> alive.

### 13 · CI/CD — hand the keys to a pipeline 🟡

```
Use the threadlight-cicd skill to generate a deploy pipeline under a machine
identity with federated OIDC (no passwords), a human approval gate before
prod, and provision/deploy as two auditable steps. Then prove the identity's
permissions are scoped to ONLY the prod resource group.
```

No human and no agent holds standing deploy rights — a pipeline does, under a
passwordless machine identity. In the run, a tenant-wide listing returned
**exactly two permissions, both confined to the prod resource group** —
nothing at the subscription level, nothing near the shared gateway.

> The generated workflow is correct and secret-free, but doesn't yet know the
> gateway-routed model wiring from Step 12 — it needs a small hand-edit to
> reproduce that. Only visible by running the whole chain.

### 14 · Customize — hand it to a real customer 🟢

```
Use the threadlight-customize skill to produce the customer hand-off package:
a customization map marking every tool keep-or-override, runbooks, and
fill-in templates — with confirm-with-customer markers wherever we assumed
something for the demo.
```

The last stage ships **runbooks and fill-in templates, not a generator** — no
two customers share a network or compliance posture. The centerpiece is a
**customization map**: every tool marked "keep" or "override, here's the
knob," each override citing a real moment from the run.

---

## Recovery prompts

The failure modes you're statistically likely to hit. Each is one prompt to
Copilot CLI — the right skill handles the fix.

**`az` is pointed at the wrong subscription / tenant**

```
use the azure-tenant-isolation skill to isolate the azure cli to tenant
<tenant-id> and switch to subscription <sub-id>, then confirm with
az account show
```

**Azure OpenAI quota / HTTP 429 mid-test**

```
switch the running pattern 0 backend from aoai to copilot github models —
refresh gh auth with scope models, export GITHUB_TOKEN, set
LLM_BACKEND=copilot in .env.local, and relaunch
```

**Port 8501 already in use**

```
relaunch the pattern 0 quickstart on port 8511 instead of 8501
```

**Python is older than 3.13**

```
create a uv 3.13 venv in this project, activate it, and reinstall the
threadlight-local-test pattern 0 quickstart package into it
```

**Post-deploy 404 / missing role assignments (Step 4)**

```
the agent 404s on name lookup after azd up — reconcile the service key
names in azure.yaml with the deployed resources, grant the missing role
assignments, and re-invoke
```

**Copilot offers to install Playwright mid-design**

```
skip the playwright install and the browser preview — just cat or view any
generated HTML instead, and continue
```

---

## Go deeper

- **[The case study](./case-study.html)** — the full real run, stage by
  stage, with the findings, the OTel trace, the costs, and the governed
  production architecture. *This is Part 2 when it's done.*
- **[Production-ready chapter](./production.html)** — the 13 pillars in 4
  themes that the Step 10 scorecard runs against.
- **[Technical briefing (THREADLIGHT.md)](https://github.com/aiappsgbb/threadlight-skills/blob/main/THREADLIGHT.md)**
  — every skill in the chain, in depth.
- **[1-hour facilitated workshop](./WORKSHOP-1H-QUICKSTART.md)** — the same
  Part 1, condensed for a room with a minute-by-minute runbook.

---

*Microsoft GBB · AI Apps — `threadlight-skills`. The running example is a
fictional bank; the subscription, gateway, and spend in the case study were
real.*
