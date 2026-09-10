# Skill-based agents: construction, runtime and evidence

This is the **L400/L500 engineering foundation** for Threadlight, not a claim
that uploading Markdown creates a production system. The catalog helps an
agent and its operator construct a pilot; the resulting application has its own
instructions, tools, SDK runtime, identity, data and evidence obligations.

**Public learning path:** [Home](https://aiappsgbb.github.io/threadlight-skills/index.html) →
[Basics](https://aiappsgbb.github.io/threadlight-skills/basics.html) →
[Build](https://aiappsgbb.github.io/threadlight-skills/funnel.html) →
[Case study](https://aiappsgbb.github.io/threadlight-skills/case-study.html) →
[Production](https://aiappsgbb.github.io/threadlight-skills/production.html).
Those L200/L300 pages explain the decisions visually. Use this guide to inspect
the implementation, [THREADLIGHT.md](../THREADLIGHT.md) for exhaustive per-skill
reference, and [agent operations](agent-operations.md) for the signed/runtime
governance protocol. None of these documents authorizes a deployment.

## Two agents and two libraries

A **construction agent** reads catalog `skills/threadlight-*/SKILL.md` files.
It may be a coding assistant with repository and shell access, or a compatible
Cowork host for bounded qualification/design work. These skills tell that
assistant how to interview, generate, invoke existing scripts, delegate to
specialized tools, assess artifacts or hand work to a human.

The **generated business agent** is a different application. On the agent
branch it consumes domain `src/agent/skills/<name>/SKILL.md`, host instructions,
registered tools and an SDK runtime. It does not need the 23 construction
skills in its runtime library. Copying the catalog into its skills directory
would confuse construction procedures with business responsibilities.

**Markdown is instruction, not a model or a tool implementation.** It is not
permission, authentication, authorization or enforcement. A skill can describe
when to call a tool and how to interpret its result; executable code must
implement that tool, a host must register it, and identities/network/policies
must independently permit and constrain the operation.

| Layer | Concrete responsibility | What the layer cannot supply by declaration |
|---|---|---|
| Model | Interpret context, select actions, produce an answer | Trusted backend facts or guaranteed rule execution |
| Domain skill | Routing description, business procedure, inputs/outputs, dependencies, failure behavior | An executable API or a new identity |
| Host instructions | Identity, boundaries, coordination and escalation order | A deterministic engine simply by saying “always” |
| Registered tool | Callable implementation and input/output contract | Permission to bypass backend authorization |
| SDK and host | Load context, register tools, manage invocations and protocol | Production readiness merely by starting |
| Runtime governance | Enforce selected action controls using trusted facts | Whole-agent coverage from one selected binding |

Orchestration belongs in agent instructions, runtime code or an explicit
workflow. There is **no domain “orchestrator” skill** to invent.
[Design's derivation recipe](../skills/threadlight-design/SKILL.md) explicitly
forbids one: put behavioral coordination in `AGENTS.md` and the derived
`copilot-instructions.md`, not in a skill that purports to control other skills.

## Authority and the agent versus workflow decision

Resolve the local contract in this order:

1. [`runtime-policy.json`](../skills/threadlight-design/references/runtime-policy.json):
   supported tuples, ordered routes and capability constraints.
2. `specs/foundation.md`: the project's reviewed selector authority.
3. `specs/SPEC.md`: business requirements and matching `capability_signals`;
   these are routing inputs, not a competing framework selector.
4. Generated artifacts: manifest, host, dependencies, tools and deployment files
   must implement the selected contract, not silently pick another stack.

| Framework | Runtime shape | External protocol | Selection |
|---|---|---|---|
| `github-copilot-sdk` | `agent` | `invocations` | Canonical `default-agent` route |
| `microsoft-agent-framework` | `agent` | `responses` | Capability route, or compatible explicit choice |
| `microsoft-agent-framework` | `workflow` | `responses` | Deterministic workflow route, or compatible explicit choice |

The policy is a **local repository authority**, not a claim about what every
external SDK supports. It first honors a supported explicit choice only when
signals are resolved and no capability-owned route blocks that choice.
`workflow_model=workflow` selects deterministic workflow execution.
Toolbox, custom Python tools, file generation or latency-sensitive data queries
select the MAF agent capability route. Otherwise the default remains GHCP.
`unresolved_signals` must be resolved or escalated; unknown is not false.

On the agent branch, skills describe procedures the agent selects and follows.
On the deterministic workflow branch, `WORKFLOW.md` accompanies `AGENTS.md`
with executor/phase definitions; generated workflow code owns ordered execution
and gates. Markdown describing phases is not that executor implementation.
Do not force the workflow branch into a universal skill-routing architecture.
For a route change, reconcile foundation, SPEC signals, manifest and generated
host together, then rerun the relevant contract and runtime checks.

## From brief to evidence

Read paths in the following table relative to the **generated project**, unless
prefixed with a catalog link. Generation instructions are work for a capable
construction agent, not a promise that a single deterministic CLI builds all
of these artifacts.

| Step and producer | Produced artifact or decision | Consumer and stop condition |
|---|---|---|
| Declared interview; optional Qualify | `qualification/sizing.md`, `qualification/sizing-manifest.json`, `qualification/discovery.md`, optional `qualification/roi.md` | Design seeds SPEC § 12 load assumptions; unknown pricing stays partial, not zero |
| Design foundation and Phase A | `specs/foundation.md`, `specs/SPEC.md`, `specs/manifest.json`, sample-data shells | Human/SME review of rules, mock boundaries, identities, value and runtime selection; stop for unresolved consequential requirements |
| Reviewed SPEC checkpoint | Manifest records `status: checkpoint`, `phase_reached: A` | Operator reviews/edits/shares/continues or stops; Phase B is a separate continuation |
| Design Phase B | `AGENTS.md`, domain `src/agent/skills/*/SKILL.md`; `WORKFLOW.md` for workflow shape | Host generation and static contract checks; stop on missing rules, unresolved handoffs or tool names |
| Data factory and tool implementation | `specs/sample-data/*.json`, `scripts/seed_data.py`, `scripts/reset_data.py`, concrete tool code | Local host, MCP service, workspace and tests use the declared schema; mock data is not a real integration |
| Design review and local test | `specs/skill-contract-manifest.json` when emitted; sample-driven tests and local observations | Review findings and provider/tool wiring; local success does not prove hosted-route parity |
| Deploy preparation and selected governance | `src/agent/container.py`, `src/agent/copilot-instructions.md`, `src/agent/skills/`, `src/agent/mcp-config.json`, packaging, infra and control-plane artifacts | Pre-deploy validation; selected bindings require actual local gate evidence before deploy |
| Approved deployment | Registered runtime/version/image and deployed services in an approved environment | Authorized engineering or CI operator; stop without identity, network, quota, signed bootstrap or environment approval |
| Collection and assurance | Fresh post-deploy safe-check, scoped governance evidence, eval/red-team/cost artifacts | Readiness consumers inspect scope, schema, freshness and results; missing business proof stays unverified |

Full Design mode is the reviewed route for regulated, consequential, case-based
and multi-phase work. **Fast-PoC** is restricted to basic scenarios and skips the
checkpoint in the source; do not present it as stakeholder approval.
SPEC § 14 separately records value baseline, target, owner, timeframe,
measurement source and maturity. A forecast is not measured outcome evidence.

### Concrete public trace: returns triage

The canonical [Returns Triage Assistant](../examples/returns-triage-governed/AGENTS.md)
recommends and records a return disposition. It does not settle a payment.
Its [SPEC](../examples/returns-triage-governed/specs/SPEC.md) is the business
contract; its four domain skills live under `src/agent/skills/`:

1. `intake-validation`: read case, order and customer; check completeness.
2. `policy-eligibility`: assess policy and cite the eligibility rationale.
3. `fraud-escalation`: enforce the declared decision precedence in the proposed
   outcome; known risk requires supervisor escalation even with missing details.
4. `disposition-decision`: propose/persist the terminal decision and audit it.

Those instructions name `oms_get_order`, `returns_get_case`, `returns_list_open`,
`customer_get_profile` and `returns_apply_decision`. OMS/customer data is mocked;
case state is operator-seeded synthetic data in Cosmos. The write is a **Cosmos
decision/audit transaction, not settlement**. Its actual tool implementation and
registered selected path must enforce the backend contract independently of
what a model says. A policy citation does not grant write authority.

Follow the executable seam, not just the instruction table:
[`governance_application.py`](../examples/returns-triage-governed/src/agent/governance_application.py)
defines `DecisionArguments`, decorates the callables and populates `tools`
through `ReturnsApplication` initialization.
[`returns_backend.py`](../examples/returns-triage-governed/src/agent/returns_backend.py)
`apply_decision()` requires effect authorization, rereads trusted facts and
checks the case revision before the conditional batch.
[`cosmos_effect.py`](../examples/returns-triage-governed/src/agent/cosmos_effect.py)
`CosmosEffectTransport.batch()` binds the transaction to the guarded transport.
The [terminal-effect tests](../examples/returns-triage-governed/tests/test_terminal_effect.py)
exercise that boundary; reading their source is not running native proof.

The current business-binding live proof remains **unverified**. Executed local
native proof is a different evidence class. The reserved hosted collector
`governance_probe_noop` does not certify `returns_apply_decision`. Consult the
[example README](../examples/returns-triage-governed/README.md) for current
materialization and evidence boundaries; archived receipts cannot be reused.

## Runtime loading

### Project paths versus deployment package paths

Design writes `src/agent/skills/<name>/SKILL.md` in the project. Deploy derives
`src/agent/copilot-instructions.md` from `AGENTS.md` and packages the agent
service. In that deployment package, the host reads a sibling `skills/`
directory and `copilot-instructions.md`, not the catalog's `skills/threadlight-*`.
[Deploy's Dockerfile recipe](../skills/threadlight-deploy/SKILL.md) copies
`container.py`, `copilot-instructions.md`, `skills/` and `mcp-config.json`.
The actual image layout and build context must preserve those relative paths.

Do not assume any file named `AGENTS.md` is automatically read by every SDK.
Instruction loading, skill context loading and tool registration are three
separate wiring decisions. Check the generated host rather than inferring them
from files present on disk or from a successful health endpoint.

### GHCP selected-governance adapter

In [`skills/threadlight-deploy/references/governance/ghcp-container.py`](../skills/threadlight-deploy/references/governance/ghcp-container.py),
the session construction around lines 400–409 supplies:

- `system_message`: replacement instructions read from `base / "copilot-instructions.md"`.
- `skill_directories=[str(base / "skills")]`: the SDK skill directory.
- `mcp_servers=servers`: configured tools, including the selected gateway relay;
  pre-MCP hooks participate in the selected action boundary.
- `enable_config_discovery=False`: this adapter does not rely on ambient
  workspace configuration discovery.

Here `base` is the host file's directory. Distinguish the **two protocol hops**:

| Hop | Contract and implementation |
|---|---|
| Inbound: client → hosted application | Foundation `protocol=invocations` defines the hosted application endpoint contract; this adapter subclasses `InvocationAgentServerHost` |
| Outbound: agent → model provider | `create_session()` receives `ProviderConfig(wire_api="responses", ...)` for the outbound model-provider wire API |

The outbound model wire API **does not change the hosted application protocol**
to Responses. These settings describe different hops, not conflicting selectors.
GHCP skill loading is also **not** MAF's `SkillsProvider` API. A registered MCP
path and its enforced gateway boundary must exist; writing a tool name in a
skill does not create either.

### MAF selected-governance adapter

[`skills/threadlight-deploy/references/governance/maf-container.py`](../skills/threadlight-deploy/references/governance/maf-container.py)
`build_host()` (around lines 143–166) uses `SkillsProvider.from_paths(skills)`
when child `*/SKILL.md` files exist. It passes `context_providers=contexts`,
`tools=tools` from `governance_application`, and instructions read from
`BASE / "copilot-instructions.md"` into `create_governed_agent`.
The host subclasses `ResponsesHostServer` and adds dependency readiness.
Shared validator modules under `skills/_shared` are not domain skills.

These two adapters are concrete **selected-governance agent** implementations,
not proof that every workflow or every generated host uses identical loading.
Review [the generator contract](../skills/threadlight-deploy/references/governance/README.md)
for the application seam, exact pinned SDKs and supported intervention points.

### Local quickstart is a different test surface

[`skills/threadlight-local-test/references/quickstart/threadlight_quickstart/agent_wiring.py`](../skills/threadlight-local-test/references/quickstart/threadlight_quickstart/agent_wiring.py)
constructs a **MAF** agent with JSON stub tools, optional overrides and a
defensive `_build_skills_provider()`. Missing/empty directories or provider
initialization failure log warnings and return no provider. `build_agent()`
then supplies `context_providers=[]`; it can still run with default instructions.

A **runnable local host is not proof that skills were consumed**, and is not
exact hosted-route parity, especially for a project whose foundation selects
GHCP/Invocations. Inspect provider initialization logs, discovered skill count,
effective instructions, registered tools and task-level behavior. Even a
nonzero discovered count is not evidence that the model used the right skill
on a particular case. Check trace and outcome against the rule and tool tests.
Do not patch an installed SDK or silently switch the project's selected tuple
to make the quickstart green.

## Auto is construction lifecycle orchestration

[`skills/threadlight-auto/references/orchestrator.py`](../skills/threadlight-auto/references/orchestrator.py)
is **not the business agent**. `decide()` reads and validates existing artifacts
and returns `run`, `skip` or `hard_stop` stage decisions, reasons and a next
action. It does not implement Design, Deploy or the business tools.

`execute(workspace, worker, ...)` can drive the chosen sequence, but actual work
is delegated to a **caller-supplied worker callback** through `worker(stage)`.
There is no universal built-in worker that can run every catalog capability.
A zero worker exit is **not accepted as gate evidence**: selected gates are
rechecked against their actual artifacts and attempt records.

`_stages_for()` selects ordinary `STAGES` or `GOVERNANCE_STAGES` from the binding
configuration. With no contract it uses the ordinary stages; an explicit
contract with no selected bindings omits `govern`. Invalid or unresolved selected
configuration blocks deployment rather than being interpreted as “off”.
The selected-binding sequence is:

`preflight` → `design` → `govern` → `governed_actions_gate` → `deploy` → `governance_probe` → `safe_check` → `cost_projection` → `invoke` → `evals` → `redteam`

Thus policy and the governed-actions gate precede deployment; fresh hosted
collection follows it. The legacy advisory recommendation projection for
governed actions is separate from this mandatory selected-binding gate.
Design can introduce selections, so `execute()` reevaluates the stage set.
Deployment attempts invalidate prior proof; collection must match the successful
attempt, not merely leave an old manifest on disk.

Qualify, Connect, Ground, Loadtest, Upgrade, CI/CD, Customize and Router-bench
remain explicit manual/offline handoffs. Local-test is available for iteration,
not an extra stage in the current `STAGES` array. Auto state and decision files
help resume construction; they are neither domain memory nor production CI/CD.

## Non-coding use and host capabilities

Use the curated [Qualify ZIP](downloads/threadlight-qualify.zip) and
[Design ZIP](downloads/threadlight-design.zip) for compatible Cowork hosts.
Qualify uses declared inputs, pure Python and vendored runtime code with dated
pricing data; it does not discover a customer environment or require Azure
credentials. Design can perform discovery, SPEC and presentation work in a
compatible host. Neither archive grants deployment capability.

The current [Microsoft upload instructions and product limits](https://learn.microsoft.com/microsoft-365/copilot/cowork/cowork-customize)
(checked 2026-09-10) use **Customize → Skills → Add dropdown → Upload skill**.
Accepted formats are `.md`, `.zip` and `.skill`; archives need `SKILL.md` at the
root, with frontmatter `name` and `description`. Follow that product page for
current size/file limits rather than treating repository limits as universal.

[`scripts/build-cowork-zips.sh`](../scripts/build-cowork-zips.sh) has a generic
Design list **and a separate `build_qualify_zip()` path**. Qualify's absence
from the generic list is not an omitted-package bug. Its curated outer archive
contains the entry script, schemas/data and an importable vendored code archive.
The script's historical companion-count/size rules are **repository packaging
constraints**, not today's universal Cowork product limits.

| Host or environment | What is feasible | What must be checked separately |
|---|---|---|
| Cowork with custom-skill support | Declared qualification and document-oriented design | Skill upload availability, allowed Python/file operations, source trust and tenant policy |
| Coding assistant with repository/shell tools | Construction, source inspection, deterministic validators, generators | Installed dependencies and tool permissions; no automatic cloud authorization |
| Local quickstart | MAF behavior iteration against sample data/stubs | Model access may cost money; provider fallback and hosted parity remain separate |
| Authorized engineering/CI environment | Build, approved deployment, identity/network setup and scoped collection | Exact runtime pins, registered services, target scope, approvals, budgets and fresh evidence |
| Generated hosted business agent | Domain tasks through registered tools and its SDK runtime | Backend authorization and selected runtime enforcement, not catalog installation |

**Host format compatibility is not tools, identity or network compatibility.**
A host accepting a ZIP says nothing about Docker, private connectivity, OBO
identity exchange, cloud permissions or native SDK support. A non-coding user
can own the brief, sizing assumptions, SPEC review and acceptance criteria;
deployment and live evidence still need an authorized engineering environment.

## Complete baseline capability map

This map covers the 23-skill governance baseline at
[`8153bc2e0a677d99b8414053d7a00cfdab495444`](https://github.com/aiappsgbb/threadlight-skills/tree/8153bc2e0a677d99b8414053d7a00cfdab495444).
Inspect the installed revision before applying it to a newer catalog.

These are **capability groups, not one universal execution DAG**. Read the
linked skill contract before invoking a capability. “Instructions” means a
construction agent performs the work; “script” means a shipped deterministic
implementation; delegation requires that dependency in the host.
Artifact paths below are generated-project outputs unless otherwise stated.

### Enter

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-qualify`](../skills/threadlight-qualify/SKILL.md) | Qualification, sizing and optional ROI | Declared interview plus deterministic `scripts/qualify.py`, shared cost engine/vendored code | `qualification/sizing.md`, `qualification/sizing-manifest.json`, `qualification/discovery.md`, optional `qualification/roi.md` | No cloud discovery; dated prices and assumptions, not actual spend |

### Build

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-design`](../skills/threadlight-design/SKILL.md) | Convert brief to reviewed business/technical contract | Construction instructions, foundation/SPEC templates and static skill linter | `specs/foundation.md`, `specs/SPEC.md`, `specs/manifest.json`, `AGENTS.md`, domain skills, optional `WORKFLOW.md`, design presentation | Full-mode checkpoint precedes Phase B; generated text is not executed behavior |
| [`threadlight-demo-data-factory`](../skills/threadlight-demo-data-factory/SKILL.md) | Consistent synthetic data across demo surfaces | Instructions generate domain JSON and seed/reset code from realism rules | `specs/sample-data/*.json`, `scripts/seed_data.py`, `scripts/reset_data.py` | Synthetic data is not customer evidence; running a seed against a backend is a separate operation |
| [`threadlight-local-test`](../skills/threadlight-local-test/SKILL.md) | Iterate on a local pilot | Shipped MAF quickstart, stub tools and UI; alternative CLI patterns | Local host, in-memory sample state, test observations | Can run without skills; model calls are not an offline/source-only check |

### Integrate

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-connect`](../skills/threadlight-connect/SKILL.md) | Replace a mocked tool with an evidenced real integration | `scripts/connect.py` extracts consumed contract, assesses samples/OBO/current roles, optionally applies config | `specs/connect-manifest.json`; reviewed SPEC/MCP config changes with `--apply` and validated endpoint | Caller supplies evidence; script does not call real endpoint; manual swap, not inferred connectivity |
| [`threadlight-ground`](../skills/threadlight-ground/SKILL.md) | Assess ACL, citations and refusal coverage | `scripts/ground.py` checks caller-supplied source-scoped probe results | `specs/ground-manifest.json` | Not a retrieval engine or live probe runner; proven leak fails, missing evidence stays not-verified |
| [`threadlight-hitl-patterns`](../skills/threadlight-hitl-patterns/SKILL.md) | Human action-gate experience | Instructions/reference card patterns plus bot handlers; delivery delegates to Teams building blocks | Domain-skill `cards/` JSON/handlers; router, audit and `card_registry.json` under `src/bot/cards/` | A rendered approval card is not authenticated one-use approval enforcement |
| [`threadlight-workspace-ui`](../skills/threadlight-workspace-ui/SKILL.md) | Operator workspace and audit view | Instructions generate a vanilla HTML/JS reference with auth/backend wiring | `src/workspace/` reference and rebuild guide | UI affordances do not enforce business authorization; mock and real backends remain distinguishable |
| [`threadlight-event-triggers`](../skills/threadlight-event-triggers/SKILL.md) | Noninteractive invocation | Instructions/scaffolds for ACA jobs, HTTP receivers, KEDA consumers; narrow Functions exceptions | `src/triggers/`, receiver config, Bicep and hook updates | Idempotency/dead-letter semantics need execution tests; a trigger scaffold is not a successful delivery |

### Assure

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-consumption-iq`](../skills/threadlight-consumption-iq/SKILL.md) | Forecast, actuals, reconciliation and value economics | Cost engine/pricing plus opt-in read-only Cost Management/Monitor/Log Analytics collection | `docs/cost-projection.md`, `specs/cost-manifest.json`, `specs/cost-actuals-manifest.json`, `specs/cost-reconciliation-manifest.json`, `docs/cost-reconciliation.md` | Declared forecast differs from settled Azure actuals; measured cost per successful interaction needs a valid outcome denominator |
| [`threadlight-safe-check`](../skills/threadlight-safe-check/SKILL.md) | Design/pre/post completeness gates | `scripts/safe_check.py`, resource checks and scoped hosted collector integration | Safe-check report; post-deploy scoped evidence including `.threadlight/governance-live.json` | Static completeness, deployed resources and runtime proof are distinct; noop is not business proof |
| [`threadlight-evals`](../skills/threadlight-evals/SKILL.md) | Batch quality, continuous evaluation and champion/challenger checks | Delegate invocation/scoring to Foundry evaluation tooling; `scripts/evals_check.py` assesses evidence | `specs/evals-manifest.json` and evaluation evidence | “Offline batch” does not mean network-free or free; executed scoring is not necessarily passing quality |
| [`threadlight-redteam`](../skills/threadlight-redteam/SKILL.md) | Adversarial safety evidence | Run/ingest Microsoft AI Red Teaming Agent/PyRIT evidence; `scripts/redteam_check.py` assesses results | `specs/redteam-manifest.json`, `docs/redteam-report.md` | Scan scope and attack-success thresholds matter; not action authorization or certification |
| [`threadlight-govern`](../skills/threadlight-govern/SKILL.md) | Define invariants and implement selected governance | Native ACS/Rego bundle tooling, host/service generation delegation and `govern_check.py` | Native bundle and `specs/governance-manifest.json` | Unsigned integrity is not signing authority; policy inventory alone is not enforcement |
| [`threadlight-governed-actions`](../skills/threadlight-governed-actions/SKILL.md) | Implement/prove consequential action boundaries | Real runtime generator, native LOCAL-14 producer and deterministic assessor | `tests/governed-actions-manifest.json`, `docs/governance/evidence-pack.md`, `tests/governed-actions-apply-plan.json` | Selected-binding gate is mandatory before deployment; local proof and never-self-applying plan are not hosted proof |
| [`threadlight-loadtest`](../skills/threadlight-loadtest/SKILL.md) | Latency, errors, throughput and tokens under load | `scripts/loadtest.py` with installed k6/locust or injected adapter | `specs/load-manifest.json` | Manual/live/cost-bearing; known over-budget or unapproved production run aborts; no engine installation or autonomous loop |

### Ship

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-deploy`](../skills/threadlight-deploy/SKILL.md) | Package and deploy the selected application | Construction instructions, existing `azd`/Foundry building blocks, native selected-governance generator | Host/package, `agent.yaml`, `azure.yaml`, `infra/`, MCP config and deployment notes | Generation is not deployment; selected signed bootstrap has ordered external prerequisites, not blanket one-command completion |
| [`threadlight-production-ready`](../skills/threadlight-production-ready/SKILL.md) | Review cross-leg evidence and handoff gaps | `scripts/production_ready.py`, BicepGraph and shared evidence validators | Readiness scorecard, findings, report and optional remediation plan | Advisory by default; explicit preview hard gate has its own exit contract; no certification or implicit remediation/deployment |
| [`threadlight-cicd`](../skills/threadlight-cicd/SKILL.md) | Production pipeline and environment handoff | `scripts/generate_pipeline.py`, templates and operator runbooks | GitHub Actions/Azure DevOps pipeline and identity/RBAC/private-runner setup docs | Platform owner must authorize/run setup; agent gets no standing deploy rights; central hub is separate |
| [`threadlight-customize`](../skills/threadlight-customize/SKILL.md) | Adapt the catalog to a customer environment | Human-led intake, fork/overlay and test-in-environment runbooks | Customer-profile workbook, customization map, upstream pin and onboarding runbook | Instructions, not automated onboarding; manual ownership of landing-zone and production decisions |

### Improve

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-upgrade`](../skills/threadlight-upgrade/SKILL.md) | Detect compatibility/preview drift | `scripts/upgrade.py` compares normalized project inputs with dated matrix/operator fixtures | `specs/upgrade-manifest.json` and ordered migration plan | No network/latest-version discovery or project edits; applying an upgrade is manual |
| [`threadlight-router-bench`](../skills/threadlight-router-bench/SKILL.md) | Learn from finished CI; compare router efficiency | Instructions invoke existing CI-log/metrics tooling for one run, optionally paired benchmark | Learnings digest and optional cost/quality scorecard | Offline lifecycle work, not necessarily network-free; estimated token cost is not billed spend, no automatic production changes |

### Auto overlay

| Skill | Purpose | Actual mechanism | Artifacts | Execution and evidence boundary |
|---|---|---|---|---|
| [`threadlight-auto`](../skills/threadlight-auto/SKILL.md) | Choose/resume construction stages | Deterministic artifact decisions; optional execution driver delegates worker callback | `.threadlight/auto-next.json`, `.threadlight/auto-state.json`, attempt/gate records | Not business orchestration; manual handoffs and evidence gates remain explicit |

The primary groups match the public Build chapter; they do not constrain when
a capability is revisited. Cost assurance starts with a forecast and returns to
settled actuals and reconciliation after the pilot. It is not a day-zero KPI.

**AgentOps extends this baseline.** PR #128 introduced the optional integration
at [`19610ca8a3b5e3bd9cff16536442cfc2ea69a717`](https://github.com/aiappsgbb/threadlight-skills/tree/19610ca8a3b5e3bd9cff16536442cfc2ea69a717).
Its presence depends on the installed catalog revision, not on this guide's
baseline count. Follow the existing [pinned AgentOps guide](agent-operations.md#agentops-preview-explicit-opt-in-bounded-assessment)
for source, availability and preview boundaries. An adapter that
normalizes evidence is not a second runtime policy enforcement engine.

## Offline inspection and the skill contract validator

Run these **from this catalog repository root**. They read local source and
the committed public example; they do not install packages, start an SDK host,
request tokens, deploy or run paid probes.

```sh
git --no-pager status --short
find skills -maxdepth 2 -name SKILL.md -print
sed -n '1,150p' skills/threadlight-design/references/runtime-policy.json
sed -n '395,412p' skills/threadlight-deploy/references/governance/ghcp-container.py
sed -n '143,169p' skills/threadlight-deploy/references/governance/maf-container.py
python3 skills/threadlight-design/scripts/skill_contract_check.py --target examples/returns-triage-governed --gate --json
node --test tests/blueprint/skill-based-agents.test.js tests/blueprint/technical-guidance.test.js tests/blueprint/published-surfaces.test.js
```

The validator is stdlib-only. `--target` is the **generated pilot root**, not
the catalog skill directory. `--json` prints the manifest. Without `--emit`
the validator does not write to the target. `--emit` writes
`specs/skill-contract-manifest.json` and `docs/skill-contract-report.md`.
Use it only when those writes are intended, preferably in a disposable pilot copy.
For that optional write, set `CATALOG` and `PROJECT` to existing absolute paths;
the following works from any directory and refuses unset variables:

```sh
: "${CATALOG:?Set CATALOG to the existing catalog root}"
: "${PROJECT:?Set PROJECT to an existing disposable generated-pilot copy}"
python3 "$CATALOG/skills/threadlight-design/scripts/skill_contract_check.py" --target "$PROJECT" --emit --gate --json
```

### Schema and exit meaning

`threadlight-skill-contract-manifest/v1` records `tool_version`, `captured_at`,
`verdict`, `must_fix`, `should_fix`, `not_verified`, metrics and capabilities.
SKC-001 through SKC-012 cover presence, parseable frontmatter, directory/name
agreement, description length, routing boundaries, resolvable handoffs,
operational contract, declared tool dependencies, business-rule references and
coverage, and registration in the agent instructions.

For a completed assessment with `--gate`, **exit 2** means nonempty `must_fix`.
Otherwise it returns 0. `not-verified` findings can remain with **exit 0**:
missing SPEC/AGENTS evidence or a degraded check is not automatically a hard
failure. Missing skills themselves are a must-fix. Read the JSON, not only `$?`.
`unsound` means must-fix exists; `partial` means should-fix or not-verified
remains; `sound` means these static checks found neither. None proves SDK
loading, model skill selection, backend execution or runtime enforcement.

Other manifests have different schemas and gates. Govern uses
`threadlight-governance-manifest/v1`; governed-actions has its own selected-path
assessment; hosted collection must be fresh and deployment-bound. Never turn
one producer's exit 0 into another consumer's readiness verdict. See
[production readiness](production-readiness.md) for scoring, `--gate-preview`,
cross-leg evidence and the difference between live smoke and readiness proof.

## Failure and recovery

| Symptom | Inspect and recover | Stop condition |
|---|---|---|
| Unknown/unavailable skill provider | Check pinned SDK API, directory, logs and provider count; compare the concrete adapter, not an old diagram | No skill-consumption claim from quickstart fallback |
| Incompatible runtime or protocol | Reconcile policy tuple, foundation, resolved SPEC signals, generated host and channel protocol | Do not silently change runtime to obtain a green smoke test |
| Tool mentioned but unavailable | Compare skill dependencies, AGENTS tool table, implementation, MCP/native registration and schema | Do not invent a tool response or treat declaration as registration |
| Contract drift | Rerun static linter; trace BR references, renamed skills, handoffs and mock/real contracts | Fix must-fix findings; review partial/unverified separately |
| Environment unavailable | Record missing identity, network, SDK, service, budget or approval prerequisite and hand off | No credential guessing, permission widening or live retries without approval |
| Weak/stale evidence | Read schema, provenance, scope, attempt/image/version and exact binding; collect fresh evidence through the approved path | No borrowed archive receipt, zero-exit shortcut or noop-to-business substitution |

Before changing a consequential tool, preserve the trusted-fact, authorization,
approval and audit boundaries of its current implementation. The
[operative governance guide](agent-operations.md) owns configure → generate →
validate → approved deployment → collect → rescore, including signed bootstrap
and reauthorization after waits. This foundational guide does not replace it.

## Source traceability

Line numbers above are navigation hints, not pins. Inspect symbols in the current
checkout and retain the catalog revision with any generated evidence.

| Claim | Implementation or template | Regression source |
|---|---|---|
| Local selector authority and conditional tuples | [Runtime policy](../skills/threadlight-design/references/runtime-policy.json), [foundation template](../skills/threadlight-design/references/foundation-template.md) | [Runtime-policy tests](../tests/blueprint/runtime-policy.test.js) |
| Reviewed SPEC and domain contracts | [Design Phase A/B](../skills/threadlight-design/SKILL.md), [SPEC template](../skills/threadlight-design/references/speckit-template.md) | [Design contract tests](../skills/threadlight-design/tests/test_skill_contract_check.py) |
| Static checks, emission and exit semantics | [`evaluate`, `manifest`, `main`](../skills/threadlight-design/scripts/skill_contract_check.py) | `test_cli_gate_exits_two_on_must_fix`, `test_cli_gate_ignores_should_fix`, `test_br_checks_not_verified_without_spec`, `test_evaluate_does_not_write_to_the_target` in the Design tests |
| GHCP session versus MAF host loading | [GHCP adapter](../skills/threadlight-deploy/references/governance/ghcp-container.py), [MAF `build_host`](../skills/threadlight-deploy/references/governance/maf-container.py), [generator](../skills/threadlight-deploy/references/governance/generate.py) | [Governance wiring](../skills/threadlight-deploy/tests/test_governance_wiring.py); this guide's source-read tests are not native execution |
| Local provider fallback | [`_build_skills_provider`, `build_agent`](../skills/threadlight-local-test/references/quickstart/threadlight_quickstart/agent_wiring.py) | [Foundational documentation contracts](../tests/blueprint/skill-based-agents.test.js) |
| Selection-aware construction sequencing | [`_stages_for`, `decide`, `execute`](../skills/threadlight-auto/references/orchestrator.py) | [Auto control-plane tests](../skills/threadlight-auto/tests/test_e2e_control_plane.py) |
| Curated Design and Qualify downloads | [`build_qualify_zip` and generic Design loop](../scripts/build-cowork-zips.sh) | [Published surfaces](../tests/blueprint/published-surfaces.test.js) |
| Canonical business scope, not settlement | [Returns AGENTS](../examples/returns-triage-governed/AGENTS.md) and [example evidence boundary](../examples/returns-triage-governed/README.md) | Published-surfaces example checks; no live business-binding certification |
