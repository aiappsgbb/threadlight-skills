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
with runtime governance are the Production path (§6.5). A facilitator can show
one governed write live on a pre-deployed kit (§6.6).
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

### 2.1 T-24h — on every laptop that will drive the demo

- **Tools signed in:** Python 3.13 (3.10+ works for the Pattern 0 package per
  its `pyproject.toml`, but the rest of the chain pins 3.13), Git, GitHub CLI,
  Copilot CLI, Azure CLI with `az login` and Azure Developer CLI with
  `azd auth login`, both in the tenant you will deploy to.
- **Plugins installed** in the right order (§4.1): threadlight-skills first,
  then the official Azure plugins, and the preflight (§2.2) reports `OK`.
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

### 2.2 Preflight: skill order (mandatory first step)

Run this right after installing the plugins, and again first thing in the
room. Copilot CLI describes plugin skills within a prompt budget, in install
order; when other skill-heavy plugins come first, the `threadlight-*` skills
are listed by name only and workshop prompts can route to other skills
(observed by Threadlight on CLI 1.0.91, see the README
[install order](../README.md#companion-skills-official-first)). The installed
plugin ships a checker. This one-liner finds it through the skills Copilot
actually loads, whatever the install location
(`~/.copilot/installed-plugins/<marketplace>/<plugin>` or
`~/.copilot/installed-plugins/_direct/<source-id>`, per the
[CLI plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference#file-locations)):

```bash
python3 "$(copilot skill list --json | python3 -c 'import json, pathlib, sys
rows = [r for r in json.load(sys.stdin) if r["name"].split(":")[-1] == "threadlight-auto"]
print(pathlib.Path(rows[0]["path"]).parents[1] / "skills/_shared/official_skills.py" if rows else "threadlight-skills-not-installed")')"
```

If Python reports `can't open file '.../threadlight-skills-not-installed'`,
threadlight-skills is not installed: do §4.1 first. Otherwise act on what the
report says:

| Status | Meaning | What to do |
|---|---|---|
| **OK** (exit 0) | Threadlight skills keep their descriptions; the pinned official skills resolve. | Continue. |
| **ORDER** | Official plugins, or other plugins and personal skills listed before threadlight-skills, fill the description budget. | Run the printed fix: reinstall the named plugins **after** threadlight-skills, or disable the ones the workshop does not need with `copilot plugin disable <name>` (re-enable later with `copilot plugin enable <name>`). Personal skills in `~/.copilot/skills` count too. Start a new Copilot CLI session and re-run until ORDER is gone. |
| **MISSING** | A pinned official skill is not installed or is disabled. | Run the printed `copilot plugin install` command. |
| **DRIFT** | A different copy (for example an old personal copy) shadows the pinned official skill. | Update or remove that copy as printed. |
| **NOTE** | Pinned content, resolved from a non-plugin copy. | Informational. |
| **UNCHECKED** (exit 2) | `copilot skill list --json` could not run. | Check that `copilot` is on PATH and signed in, then re-run. |

ORDER, MISSING and DRIFT make the report `DEGRADED` (exit 1). Do not start the
design until it is OK, or the prompts may not reach the Threadlight skills.

### 2.3 Model choice

The timings above were measured with Claude Opus 5.5 at medium reasoning. You
may use **Copilot Fusion** instead of pinning one model:
[Copilot Fusion](https://docs.github.com/en/copilot/concepts/models/copilot-fusion)
is a runtime router that picks the models and execution pattern for each
request. It is generally available in Copilot CLI and the GitHub Copilot app.
**Fusion Standard** is available on every Copilot plan; **Fusion High** (and
Fusion Max) are available on Copilot Pro+, Copilot Max, Copilot Business and
Copilot Enterprise. The comparison below shows where each fits this chain.

- **Copilot CLI:** enter `/model` and choose **Copilot Fusion** with an effort
  level, or start with `copilot --model fusion --fusion-tier standard`
  ([Choosing a model in Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/choose-a-model)).
  If your CLI version does not accept `--fusion-tier`, use `/model`.
- **Copilot app:** click the model below the prompt box, turn the
  **Copilot Fusion** switch on and choose the effort level
  ([Choosing a model in the Copilot app](https://docs.github.com/en/copilot/how-tos/github-copilot-app/choose-a-model)).

We ran a small comparison on the returns-triage brief in November 2026: two
design runs per setting and one live deploy run per setting, all on the same
laptop. It is not a benchmark, and AI credit totals depend on your plan and
the models Fusion picks, so treat the numbers as an order of magnitude.

| Step | Claude Opus 5.5, medium | Fusion Standard | Fusion High |
|---|---|---|---|
| Design | ~17 min, ~600 credits; both designs clean | ~18 min, ~20 credits; thinner evals, some wrong expected outcomes, deploy-owned files that stop generate-only | ~60 min, ~1,800–2,750 credits; deploy-owned files |
| Live deploy of the same design | 33 min, ~400 credits | 48 min, ~25 credits | not run |

- **Design:** use Claude Opus 5.5 at medium. This is where the pilot's quality is
  decided, and the Fusion Standard designs needed rework before deploy.
- **Deploy:** Fusion Standard is a reasonable choice when credits are short: it
  completed the same live chain (killer prompts, Foundry evaluation, package
  gate) with no hand edits, at the cost of a longer run.
- Avoid Fusion High for this chain: it was slower and used more AI credits than Opus.

Fusion bills each model it uses at that model's rate, and Fusion High or Max can use
more than one model per task. Per the docs, Copilot Fusion works separately from
subagents and doesn't start them, and edits made by a draft it discards are not
undone automatically, so review the workspace before you commit it.

### 2.4 T-1h — first thing in the room

Run the preflight (§2.2) on the demo laptop, then open Copilot CLI and prompt:

> verify python 3.13, gh, az, azd and copilot CLI are installed and signed in, and tell me which tenant az is pointed at

If anything's red, fix it now — don't try to fix it inside the runbook.

If you deploy the cloud agent ahead of the session (fallback workspace), its MCP
Container App scales to zero by default, with no always-on cost. A few minutes
before showing cloud answers, warm it:

> in ~/Repos/workshop/returns-triage run azd env set MCP_MIN_REPLICAS 1 and then azd provision

After the session, set it back with `azd env set MCP_MIN_REPLICAS 0` and run
`azd provision` again, so no replica stays on.

---

## 3. Minute-by-minute runbook

The budget below uses the measured times, with the upper bound for design.

| Minute | Block | What we do | Outcome on screen |
|---|---|---|---|
| **0–5** | Welcome + frame | Walk §1 together: what we'll build and the vocabulary heads-up. | Slide-less; just the doc. |
| **5–8** | Preflight + check the setup | Run the skill-order preflight (§2.2), then the T-1h prompt (§2.4). Plugins, logins and the AOAI deployment were prepared before the session. | `OK` from the preflight; green tool output in Copilot CLI. |
| **8–27** | Design the use case live | Prompt §4.2. Watch Copilot CLI invoke `threadlight-design` Fast-PoC, answer its setup questions live, and watch `specs/SPEC.md`, `AGENTS.md`, `src/agent/skills/`, `specs/sample-data/`, and `tests/killer-prompts.md` land. *If it's still in demo-deck / sales-kit generation past ~minute 22, interrupt with "skip demo and sales kit, move on".* **Fallback:** at minute 27, if the design is not finished, switch to the pre-designed workspace (§2.1) and continue. | A fresh `returns-triage` PoC: SPEC + AGENTS.md, agent skill folders, JSON sample-data, killer demo prompts. |
| **27–33** | Pattern 0 + live demo | Prompts §4.3 and §4.4, then paste the killer prompts (§4.5) into `localhost:8501`. Watch the SPEC's own read tools (for example `returns_get_case`, `return_policy_search`) fire against the sample data and policy text. The agent proposes the decision with citations; recording it is your action, not the agent's (recommend-only pilot). | Streamlit chat: one cited recommendation per killer prompt. |
| **33–37** | Generate the deploy package | Prompt §6.3 up to generate-only (Dockerfiles, `azure.yaml`, `infra/main.bicep`, evals). | `DEPLOY.md` and `specs/deferred-steps.md` on disk. |
| **37–42** | Start `azd up` before Q&A | After scope/budget approval (§6.1), run `azd up` (§6.3). Keep the **Azure Portal resource group view** alongside Copilot CLI. | Provisioning and remote build progress. |
| **42–50** | Governance segment (optional, facilitator) | While `azd up` runs, show ONE governed write on the facilitator's pre-deployed governed-write pilot (§6.6): allow, gateway deny, pending approval with no effect, Approver decision, resumed write, replay with no extra effect. Participants' pilots stay recommend-only. | Four results on screen: `audit_id`, `policy_deny`, `pending_approval`, same `audit_id` on replay. |
| **50–60** | Q&A, then cloud smoke test | When `azd up` finishes, run the killer prompts against the cloud agent (§6.4). Point to the Production path (§6.5) for governing your own pilot. | Cloud answers on screen; the Foundry eval (15–20 min) and the evidence record run **after the session**. |

If the hour is shorter than planned, cut in this order: the governance segment
(it needs the facilitator's pre-deployed kit), the cloud smoke test
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
visible; see the README's [Install](../README.md#install) section. Then run the
preflight (§2.2) and act on its report. The optional
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

### 5.6 `azd up` seems stuck on the App Insights connection

Symptom: provisioning lists every resource as done but does not finish, and the
portal deployment shows the project's App Insights connection still running. On
2026-11-10 in eastus2 every Foundry connection write answered
`InternalServerError` even though the service saved it, so ARM kept retrying.
Packages generated by threadlight-skills 2.37.0 or later create this connection
in `hooks/postprovision.sh` instead. If the service errors but saves the
connection, the hook logs it and `azd up` continues. If nothing was saved, the
hook stops `azd up` with the service error. For a package generated before
2.37.0:

> cancel the running deployment in my resource group, update the threadlight-skills plugin, regenerate the package into a fresh copy of ~/Repos/workshop/returns-triage with generate-only, and run azd up again on the same azd environment

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

The MCP Container App scales to zero by default (`MCP_MIN_REPLICAS=0`), so
nothing stays on between demos. The first killer prompt after idle waits for a
replica to start. In a live run on 2026-11-10, K1 took 68 s cold against 43–55 s
warm, and every tool call succeeded. To avoid that wait on screen, run
`azd env set MCP_MIN_REPLICAS 1` and `azd provision` a few minutes before the
demo. Afterwards, run `azd env set MCP_MIN_REPLICAS 0` and `azd provision` again.

> run the killer prompts from ~/Repos/workshop/returns-triage/tests/killer-prompts.md against the deployed returns-triage agent — same prompts, now hitting the cloud agent over the Invocations protocol

After the session, run the Foundry evaluation (28 rows measured at 15–20 min)
and record the evidence:

> in ~/Repos/workshop/returns-triage, follow DEPLOY.md to record the deploy evidence (scripts/record_deploy_evidence.py with the trace id) and run python evals/run_evals.py, then summarise the eval run id, the rubric score and anything still missing

When you are done, delete the resource group you created (or hand it to its
cleanup owner); do not leave the pilot running unattended.

### 6.4b Optional: talk to the agent (voice front-end, preview)

Only when a participant **explicitly asks** for a spoken demo and accepts that
Foundry voice agents are **preview**: the deployed text agent can get a managed
voice wrapper ([Microsoft Learn](https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent)).
Voice Live does speech in and out; the same hosted agent, with the same MCP
tools, does the reasoning. Check that the region supports
[voice-based agents](https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions#supported-regions).
Budget about 10 minutes after the text smoke test.

> in ~/Repos/workshop/returns-triage, the user explicitly wants to talk to the agent and accepts the voice preview: record in specs/SPEC.md § 11e voice_frontend: hosted-voice with voice_opt_in_source: user, voice_opt_in_evidence quoting this request and preview_acknowledged: true, regenerate with threadlight-deploy generate_only (--force), export AZD_ALPHA_ENABLE_AGENTS_VOICE=true and azd deploy, then run the official microsoft-foundry smoke_test_voice.py against returns-triage-agent-voice and summarise transcript and audio evidence

Then open the voice playground for `returns-triage-agent-voice` in the Foundry
portal and ask a killer prompt out loud. The smoke test proves the wrapper reaches
the agent and returns transcript and audio; what you hear (recognition quality,
barge-in, latency) is your own observation, not validated evidence.

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

### 6.5b Path to production: performance evidence (follow-up)

The first production question after the demo is usually "how fast is it at
our volume, and where does the time go?". Answer it on the deployed pilot,
after the workshop, with a budget-capped load run through the real hosted
agent endpoint, model and MCP server:

> in my pilot folder, use threadlight-loadtest to run the performance evidence
> load test for agent returns-triage with a 80 USD ceiling, up to 10 concurrent
> requests and 200 steady requests, then open docs/perf-evidence.html

It runs a cold phase (new sessions, so new sandboxes; an MCP server scaled to
zero starts too), a ramp and steady concurrency, then splits every request
using the agent's own spans in Application Insights: endpoint and sandbox,
session start, model calls, tool and MCP calls, runtime overhead, time to first
token, plus errors, model throttling, tokens per request, the deployment's
TPM/RPM limits and the model cost per 1,000 requests (calculated from measured
tokens and Retail Prices). What the traces cannot separate is stated, not
estimated. Expect real spend and size the model deployment first: in our
2026-11-11 runs one returns-triage request used 100,000-120,000 input tokens
(about 90% cached) across about 10 model calls, a 300K TPM deployment throttled
at 10 concurrent requests (73% of requests failed) and 800K TPM served 4
concurrent with no throttling (warm p50 34 s, p95 54 s). See the
[load-test skill](../skills/threadlight-loadtest/SKILL.md#hosted-agent-performance-evidence-latency-waterfall).
The pilot value page (§6.7) then shows a **Performance under load** line.

### 6.6 Governance segment: one governed write live (facilitator)

The [governed-write workshop kit](../examples/governed-write-ghcp/README.md) is
a GHCP pilot in the `governed-write` action mode: reads stay unbound, and one
write (`returns_apply_decision`) goes only through the authenticated gateway
with four Rego business rules and deferred human approval. The facilitator
deploys the shared governance foundation and this pilot **once, before** the
workshop (its README has the runbook), then during the session:

1. `demo_capacity.py warm` (services and a tool-free agent ping).
2. Run the four beats with `invoke.py`; the Approver decides with
   `threadlight-review-action` signed in as themselves.
3. `demo_capacity.py cool`, so nothing stays always on.

It was executed live on 2026-11-10 ([S8](governed-returns-validation.md#s8-governed-write-on-ghcp-workshop-kit)).
That evidence is for that deployment and that one binding; it is not a
governance pass for participants' pilots or whole-agent governance.

### 6.6b Advanced (optional): text, voice and the governed write on one pilot

One pilot, three entry points, one governed write: the facilitator speaks to
the governed agent, the high-value decision waits for a person, the Approver
decides, and the caller completes it in the same call. Voice is **preview**:
run this only when the facilitator explicitly wants it. Prepare it before the
session; it is not a participant exercise.

**Before the session.** In the kit's `specs/SPEC.md` § 11e set
`voice_frontend: hosted-voice` with `voice_opt_in_source: user`,
`voice_opt_in_evidence` and `preview_acknowledged: true`, then follow the
[kit runbook](../examples/governed-write-ghcp/README.md) with
`export AZD_ALPHA_ENABLE_AGENTS_VOICE=true` for every `azd` command (the
`azure.ai.agents` extension refuses `kind: voice` without it). The governance
package keeps the voice front-end: the governed GHCP host serves the Bridge on
`invocations_ws`, the `<agent>-voice` wrapper uses it as conversation engine.
Every time the agent gets a new version (a new signed policy), **redeploy the
voice wrapper** too: `conversationEngine` pins the version deployed with it,
and the old version fails with `startup_failed`. Check with the official
`smoke_test_voice.py` against `<agent>-voice`.

**During the session** (measured live on 2026-11-10, eastus2, three spoken
calls on two agent versions; spoken input was synthetic text-to-speech (TTS)
audio from a script, no human voice; ranges are min-max across those calls):

| Beat | What the room sees | Measured |
|---|---|---|
| `demo_capacity.py warm ... --agent <agent>` | services and agent ready (pass `--agent`, the default names the kit agent; `--gateway-url` ends in `/mcp`, otherwise the gateway check waits on a wrong URL) | about 3 min (190 s) |
| Speak: "Triage return case R-M-A dash W zero four dash C and record your decision." | the agent says one moment, then that nothing is recorded and a human Approver must decide | hold message 4.4-28 s, answer complete 17-61 s after the end of speech (the slow call waited 20 s before Voice Live opened the response) |
| Pending record | the client receives the host's exact `pending_approval` record as unspoken data (`response.invocation.delta`); show it in the [case-list workspace](../skills/threadlight-workspace-ui/references/shapes/case-list/README.md) (pending card, reviewer command, no Approve button) | arrived 11-54 s after the end of speech |
| Approver | `threadlight-review-action --pending <saved record> ...` signed in as themselves | 1.2 s for the recorded decision itself (sign-in time not measured) |
| Speak: "The supervisor has approved it. Please complete that decision now." | the host gives the exact original arguments and operation id back as a host record; the agent says it was recorded | 10-12 s after the end of speech; whole call 53-92 s |
| `demo_capacity.py cool` | everything back to zero replicas | - |

What to point at: the escalation is decided by the gateway, not by the voice;
nothing is written until the Approver decides; the resume re-checks policy,
facts and identity and consumes the grant once. If you ask to complete before
the Approver decides, the agent answers that it is still pending.

**Spell the case id.** Spoken identifiers can lose their dashes in speech
recognition (live: "RMA, W05C"). The agent then finds no case, records
nothing and asks for the exact id; it does not guess a nearby case. Say the id
letter by letter with "dash", and repeat it the same way if asked.

**Interrupting the agent.** Live, the caller interrupted the escalation turn
after the pending record had been sent; the next answer carried a pending
record again, the governance store held one new approval request for one
operation, and nothing was written. If the interrupted answer could not send
the record, the host sends it on the caller's next response (unit-tested).

**Unknown outcome: never retry.** If the agent says the outcome is unknown (live: the business API was cold
because a redeploy had reset it to zero replicas, and it committed after the
gateway's bounded call), the write may have happened. Reconcile
from the business case and audit records, then warm and use the next case set.

Not validated here: speech recognition of real human voices, accents and noisy
rooms, an interruption before the pending record was sent (covered by unit
tests only), audio quality, perceived latency on venue networks.

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

### 6.7 Close with the pilot value page

End the session with the page the process owner keeps: what the pilot showed,
in business language. From the pilot folder, run:

```bash
python <threadlight-skills>/skills/threadlight-production-ready/scripts/pilot_value.py --workspace .
```

Open `docs/pilot-value.html`. It reads only the evidence the pilot already
produced (Foundry eval score against the business rules, showcase-scenario
results recorded in `docs/invoke-results.md`, post-deploy safety check, cost,
business case, SPEC targets) and labels each item Measured, Calculated,
Estimate, Target or **Not measured yet**. Right after a one-hour workshop most
cost and business lines will say "Not measured yet"; that list is the agenda
for the follow-up, not a gap to fill by hand. The page is a decision aid, not a
go-live verdict.
