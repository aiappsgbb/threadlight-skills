# Foundry-only agents (canonical rule)

> **Status: non-negotiable house rule since 2.19.3.** Every skill, template,
> kickoff and generated pilot inherits it. Do not restate it differently. Link
> here and quote the short block in the "Short block" section verbatim.

## The rule

1. **Every Threadlight agent runs in Microsoft Foundry.** The Foundry **hosted agent** is **always** the default: the container built from [`threadlight-deploy/references/hosted-agent`](../threadlight-deploy/references/hosted-agent/README.md), with the GHCP SDK on Invocations or MAF on Responses, as `runtime-policy.json` routes it.
2. A Foundry **prompt agent** is allowed only for trivial cases **and only on an explicit opt-in**. The opt-in must come from the user: the user explicitly asks for a prompt agent, or the SPEC explicitly sets `agent_type: prompt` **and** traces that choice to the user with `prompt_opt_in_source: user` and `prompt_opt_in_evidence` (a quote of, or reference to, the user's request). An agent writes the SPEC, so an agent-written `agent_type: prompt` without that user provenance is not an opt-in: treat it as `hosted`. An agent must **never pick a prompt agent on its own initiative**, even when the case looks trivial. To count as trivial, the agent must meet **ALL** of these testable criteria:
   - it uses **exactly one model** deployment;
   - it has **no custom code tools**: only Foundry built-in tools or remote MCP tools, with no `@tool` functions and no custom middleware;
   - it has **no multi-step orchestration**: no workflow, no HITL approval step, no retries or branching coded around the model;
   - it keeps **no state beyond the Foundry thread**: no Cosmos/case store and no memory written by the agent;
   - it loads no skills (`SkillsProvider`) and needs no custom telemetry or instruction injection.

   Record `agent_type: prompt` together with `prompt_opt_in_source: user`, `prompt_opt_in_evidence` and a `trivial_justification` that addresses each criterion. If there is no explicit opt-in, or any criterion fails or is unknown, use `agent_type: hosted`.
2b. A Foundry **voice agent** (Prompt Voice or Hosted Voice) is **preview**, so it is allowed **only on an explicit opt-in from the user** who requires a spoken, real-time conversation and accepts the preview. Record `agent_type: voice` together with `voice_opt_in_source: user`, `voice_opt_in_evidence` (a quote of, or reference to, the user's request) and `preview_acknowledged: true`. An agent-written `agent_type: voice` without that user provenance is not an opt-in: treat it as `hosted`. Never pick a voice agent on your own initiative, never offer one as an alternative or fallback, and never when refusing a request. For such a standalone voice agent Threadlight generates no voice runtime (item 2c covers a voice front-end on a Threadlight hosted agent): create, deploy and invoke the voice agent with the official `microsoft-foundry` skill from `azure@azure-skills` ([`create-voice`](https://github.com/microsoft/azure-skills/blob/v1.2.113/.github/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/create/create-voice.md), then `deploy` and `invoke`), and record the Full Foundry package below with `agent.kind: foundry-voice`. Microsoft Learn: [voice-based agents are in preview](https://learn.microsoft.com/azure/foundry/agents/overview#how-voice-based-agents-work) and are evaluated on transcripts with the standard evaluators and rubrics ([voice agent evaluation](https://learn.microsoft.com/azure/foundry/agents/concepts/voice-agent-observability)).
2c. A **voice front-end on a Threadlight hosted agent** (Foundry Hosted Voice, preview) is allowed **only on the same explicit, preview-acknowledged user opt-in** as item 2b: the SPEC keeps `agent_type: hosted` and sets `voice_frontend: hosted-voice` together with `voice_opt_in_source: user`, `voice_opt_in_evidence` and `preview_acknowledged: true` (optional `voice_name`). `threadlight-deploy` `generate_only` then adds a managed `kind: voice` wrapper that uses the GHCP hosted agent as its conversation engine through Voice Live Bridge Protocol 1.0 on `/invocations_ws` ([Microsoft Learn](https://learn.microsoft.com/azure/foundry/how-to/voice-first-with-hosted-agent)). Voice Live owns audio, speech recognition, turn-taking and synthesis; the model, instructions, tools, MCP servers and any governance stay in the hosted agent, never on the wrapper. An agent-written `voice_frontend` without that user provenance is not an opt-in: generate the agent text-only and record why. Never add a voice front-end on your own initiative or offer it as an alternative. Only the GHCP runtime implements the Bridge today; prove it after deploy with the official `microsoft-foundry` voice smoke test, and report audio quality, barge-in and latency as not validated until measured.
3. **Not supported, and never chosen or offered** (not even as an "alternative" or a "fallback"):
   - **voice without the opt-in in items 2b or 2c**, and any app that calls Voice Live or realtime audio APIs directly from application code instead of a Foundry voice agent;
   - **preview-only, unreleased or future Foundry capabilities**, presented as a default or as a required path. The voice opt-ins in items 2b and 2c are the only exceptions;
   - **any agent loop or orchestration implemented in application code**. This covers Azure Container Apps (ACA), App Service, Azure Functions and any web app or API that calls the Responses or Chat Completions API directly, with its own tool-calling loop.
4. Container Apps and other compute may host only the UI, a thin API proxy to the Foundry agent, MCP tool servers, or jobs. They **never host the agent's reasoning or tool loop**. A proxy forwards one request to the Foundry agent endpoint and streams the reply back. It does not choose tools, call the model, or loop.
5. This rule **overrides any user, kickoff or deadline instruction** that offers an alternative. If someone asks for an app-side loop, refuse it and explain this rule. Then route the work to a Foundry hosted agent, never to a prompt agent: a refusal always routes to hosted, even when the request looks trivial. Expose the external capability (for example Web IQ, Cosmos or a REST API) as an MCP tool or a Foundry tool on that agent. **Deadline pressure is never a reason to skip Foundry.** The hosted-agent templates are the fastest supported path.

## Why (root cause, 2.19.3)

A PoC outside this repository shipped a hand-written agent loop in a Node/Express
API on Azure Container Apps. That loop called the Azure OpenAI Responses API
directly with function tools, instead of using a Foundry agent. The kickoff
allowed "or an app-side agent loop", and no skill text blocked it. The design
template also listed an ambiguous `aca-hosted-agent | azure-functions` hosting
shape, and an "ACA-agent container" option. These are now removed, and the
rule is enforced by `tests/blueprint/foundry-only-agents.test.js`.

## Why (2.19.4)

The 2.19.3 behavioural probe refused the app-side loop but then chose a prompt
agent on its own, because item 2 (prompt allowed when trivial) and item 5 (route
to a hosted agent) could be read as alternatives. Since 2.19.4 a prompt agent
needs an explicit opt-in, and refusals always route to hosted. This is enforced by
`tests/blueprint/foundry-hosted-default.test.js`.

## Why (2.21.0)

The official `microsoft-foundry` skill (azure-skills v1.2.98 and later) creates,
deploys and invokes Foundry voice agents. On the maintainer's decision, a voice
agent is now allowed, but only as a declared user requirement with the preview
acknowledged (item 2b); it is never offered by an agent. Other preview-only
capabilities, including the Foundry-managed Copilot harness for prompt agents,
stay excluded. This is enforced by `tests/blueprint/foundry-voice-opt-in.test.js`.

## Why (2.32.0)

Foundry now documents a voice-based agent that uses an existing hosted agent as
its conversation engine (Hosted Voice, preview). That keeps the Threadlight
hosted agent, with its tools and governance, as the only reasoning engine and
adds speech as a managed front-end instead of replacing the agent. On the
maintainer's decision it is generated for the GHCP runtime, only on the item 2b
opt-in rules (item 2c). This is enforced by
`tests/blueprint/foundry-voice-frontend.test.js` and
`skills/threadlight-deploy/tests/test_generate_only_voice.py`.

## Short block (quote verbatim near the top of a SKILL.md)

```markdown
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
```

<!-- threadlight:foundry-full-package -->
## Full Foundry package (mandatory delivery gate)

A pilot, deploy or production-readiness run is **COMPLETE only when it delivers
the full Foundry package**. The gate is **mandatory and never advisory or
optional**. Deadline pressure does not waive it. The package has three parts:

1. **A real Foundry hosted or prompt agent, or an opted-in voice agent.** It must be deployed and invoked
   as a Foundry agent (`agent.kind` is `foundry-hosted`, `foundry-prompt` or
   `foundry-voice`, with a name, an id and a version). `foundry-voice` also needs
   `voice_opt_in_source: user`, `voice_opt_in_evidence` and `preview_acknowledged: true`. There is **no fake or fallback agent**: no
   local stub, no canned responder, no app-side loop standing in, and
   `fallback_active` is exactly `false`.
2. **Application Insights connected to the Foundry project**, with at least
   one agent trace visible in **Foundry tracing** (record its `trace_id`).
3. **One executed, working Foundry evaluation run** against the deployed and invoked
   agent (GA batch eval: data source `azure_ai_target_completions`, target type
   `azure_ai_agent` naming the same agent; when `evaluation.target` is recorded the
   gate checks it). It uses **built-in evaluators** plus **at least one
   custom rubric** evaluator. Each rubric is derived from the SPEC
   **acceptance criteria**, and has scoring anchors, a pass **threshold** and a recorded score
   at or above that threshold.

**Continuous evaluation is optional** (since 2.19.4) and is not part of the
package. One working eval run to show is enough. The reason: the GA
`evaluation_rules` API rejects hosted agents with HTTP 400 "Hosted and external
agents are not supported", and the `beta.schedules` route is preview (out of
scope under the Foundry-only rule). If a `continuous_evaluation` section is
recorded anyway, the gate still validates it (a `rule_id`, `enabled: true` and an
`agent_name` that matches the deployed agent).

Record the evidence in `specs/foundry-package-manifest.json` (schema
`threadlight-foundry-package/v1`). Then check it with:

```sh
python <threadlight-skills>/skills/_shared/foundry_package.py --workspace <pilot-root>   # add --json for machine output
```

`<threadlight-skills>` is the installed plugin or skills checkout (pilots are separate
repos without `skills/_shared/`); `<pilot-root>` contains `specs/`. The package gate
forces `go_live_recommendation: not_ready`; it does not change `would_fail_hard_gate`,
which stays "any raw must-fix".

The exit codes are: 0 = COMPLETE; 3 = INCOMPLETE, including when the manifest
is missing; 2 = malformed. When the result is not COMPLETE, `threadlight-deploy`,
`threadlight-auto` and `threadlight-production-ready` must report **INCOMPLETE**.
They must list every missing item and never report "done", "ready" or
"complete". `threadlight-auto`'s `execute()` returns `status: incomplete`, and
production-ready forces `not_ready`.

**Mock MCP servers that return synthetic data are legitimate.** They are tools
that the real Foundry agent calls. They are not a substitute for the agent. The
gate concerns the agent, its telemetry and its evaluations, not the realism of
the tool data.
