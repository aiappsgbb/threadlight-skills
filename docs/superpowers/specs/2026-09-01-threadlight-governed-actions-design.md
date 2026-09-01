# Threadlight Governed Actions - Design

- **Status:** Approved
- **Date:** 2026-09-01
- **Repository:** `aiappsgbb/threadlight-skills`
- **Feature:** new standalone skill `threadlight-governed-actions`
- **Delivery shape:** one focused implementation plan and one implementation pull request

## 1. Context and problem

Threadlight can document human interaction points, assess production posture,
author Agent Governance Toolkit (AGT) policy artifacts, and run offline
evaluations. Those capabilities do not prove that every consequential action an
agent can take is mediated on every execution path.

A policy file, approval card, conformance claim, or green evaluation can coexist
with an unmediated batch worker, a provider-hosted side-effecting tool, a
replayed approval, a fail-open interceptor, or a GitHub Copilot coding-agent
change that bypasses protected review and deployment identities. Customers need
a reviewable answer to a stronger question:

> Can the project prove that consequential runtime actions and agent-produced
> code changes are governed by enforceable controls, with negative-path
> evidence, rather than merely described?

`threadlight-governed-actions` fills that gap. It is an assessor and evidence
producer that inventories consequential actions, traces every supported path to
an enforceable mediation seam, runs deterministic application-path contract
probes, assesses the GitHub Copilot change supply chain, and emits a
customer-facing Governance Evidence Pack.

## 2. Goals

1. Produce a complete, classified inventory of agent tools and actions.
2. Prove that every consequential runtime path reaches pre-action mediation.
3. Exercise deny, transform, fail-closed, approval anti-replay, output
   mediation, and payload-free audit behavior through the application path.
4. Assess the separate GitHub Copilot change plane through repository and
   deployment controls.
5. Emit deterministic machine-readable evidence, a customer-facing pass/fail
   matrix, a residual-risk register, and an ordered remediation plan.
6. Preserve truthful evidence semantics: missing permissions or live proof is
   `not-verified`, never an implicit pass.
7. Remain read-only with respect to customer source and policy except for an
   explicit, bounded scaffold mode.
8. Integrate with `threadlight-production-ready` without duplicating the
   detailed governed-action checks.

## 3. Scope and non-goals

### 3.1 In scope

- Microsoft Agent Framework (MAF) first, using Agent Hooks/Agent Control
  Specification (ACS) or an evidenced equivalent pre-action seam.
- Runtime paths including interactive, batch, background, subagent, direct-tool,
  and provider-hosted-tool execution.
- Consequential action classes: `write`, `external-egress`, and `irreversible`.
- Local deterministic contract probes and optional non-destructive staging
  canaries.
- GitHub repository, CI, and deployment-identity controls for changes produced
  by GitHub Copilot or other coding agents.
- Evidence freshness, source and policy binding, upstream dependency drift, and
  residual-risk reporting.
- An optional MAF scaffold that is safe by default and never invents customer
  policy.

### 3.2 Non-goals

- Certifying a system, organization, or regulatory posture.
- Treating Agent Hooks, ACS, SAFE, ASSERT, AGT, or eval results as a security
  boundary by themselves.
- Intercepting GitHub Copilot's internal reasoning or tool loop. Agent Hooks
  cannot do that; the GitHub Copilot plane governs the resulting code-change
  and deployment supply chain.
- Replacing tool-service authorization, idempotency, validation, transaction
  constraints, or compensating controls.
- Authoring business thresholds, authorization rules, approvers, exception
  policy, or customer risk appetite.
- Automatically applying remediations, changing branch protection, granting
  Azure roles, deploying infrastructure, or promoting to production.
- Running destructive, cost-bearing, or production canaries.
- Cross-framework-first support. Later frameworks use the adapter contract in
  section 8 after MAF behavior is stable.

## 4. Boundary with existing skills

| Concern | Existing owner | `threadlight-governed-actions` boundary |
|---|---|---|
| Overall readiness scorecard and uplift plan | `threadlight-production-ready` | Produces the detailed governed-actions manifest that governance, HITL, and supply-chain pillars consume. |
| Deployment contract and resource/channel reachability | `threadlight-safe-check` | Uses safe-check evidence as context; adds action mediation and negative-path proof, not deployment completeness checks. |
| Teams/workspace approval UX | `threadlight-hitl-patterns` | Verifies approval binding, one-time redemption, mutation resistance, and runtime enforcement; does not generate the UX. |
| AGT policy, lint, replay, attestation, and `govern-manifest.json` | `threadlight-govern` | Treats these as useful inputs, but does not accept policy/attestation presence as application-path proof. |
| Deep AGT/ACS authoring | `foundry-agt` | Reuses upstream expertise and artifacts; independently verifies runtime-path enforcement evidence. |
| Quality and offline assurance | `threadlight-evals`, `foundry-evals`, ASSERT-style suites | Consumes results as offline assurance; does not confuse them with runtime enforcement. |
| CI/CD generation | `threadlight-cicd` | Assesses PR, workflow, identity, and pinning posture; delegates full pipeline generation/remediation. |
| Runtime-action governance evidence | none at required depth | New standalone owner. |

The new skill does not invalidate `threadlight-govern`. That skill remains the
thin AGT policy/attestation producing leg. Its `specs/govern-manifest.json` is
one evidence input. `threadlight-governed-actions` adds the missing inventory,
path-coverage, application-probe, approval, output, audit, and GitHub Copilot
change-plane proof.

## 5. Trust and terminology model

The evidence pack must state these limitations prominently:

- **SAFE is a design framework.** It helps define required safety and governance
  properties; it does not enforce runtime behavior.
- **ACS is a policy decision runtime/interceptor.** A valid policy decision is
  useful only when every supported application path invokes it at the required
  intervention point.
- **Agent Hooks is the host/interceptor contract.** It is cooperative and is not
  a security boundary against a compromised host, bypassing code path, or tool
  service.
- **ASSERT/evals are offline assurance.** They can find regressions and exercise
  scenarios; they are not runtime enforcement.
- **Conformance is not certification.** A passing report proves only the tested
  tuple, paths, fixtures, and observation window.
- **Tool services remain authoritative.** Every side-effecting service must
  repeat authorization, tenant/scope checks, schema validation, idempotency, and
  transaction or compensation constraints.

An **equivalent enforceable seam** may replace Agent Hooks/ACS only when evidence
shows that it:

1. executes before the side effect;
2. covers every supported invocation path;
3. can deny and, where declared, transform the action;
4. fails closed on timeout, crash, or malformed verdict;
5. binds approval to the exact action and policy context;
6. emits correlation-safe, payload-free audit evidence; and
7. cannot be bypassed within the application's declared support boundary.

## 6. Architecture

The design has two independent planes and one evidence normalizer.

```text
Customer project
  |
  +-- Runtime plane
  |     SPEC section 8 + tool registry + runtime/middleware + policies
  |       -> action inventory
  |       -> mediation graph
  |       -> deterministic application-path probes
  |       -> optional non-destructive staging canaries
  |
  +-- GitHub Copilot change plane
        workflows + CODEOWNERS + branch rules + required checks
        + identity configuration + optional live GitHub/Azure evidence
          -> change-supply-chain assessment

Both planes
  -> normalized findings and evidence references
  -> tests/governed-actions-manifest.json
  -> docs/governance/evidence-pack.md
  -> tests/governed-actions-apply-plan.json
```

The planes do not share a pass condition. Strong runtime mediation cannot
compensate for an unprotected code-change path, and strong repository controls
cannot compensate for an unmediated runtime action.

### 6.1 Components

| Component | Responsibility |
|---|---|
| Input resolver | Locate required and optional inputs, capture source commit, and record missing capabilities without guessing. |
| Action inventory builder | Discover tools/actions from SPEC and code, normalize aliases, and classify consequences. |
| Runtime adapter | Describe framework/runtime entry points and emit normalized execution paths. MAF is the first adapter. |
| Mediation graph builder | Map entry points through agents, workers, subagents, tool routers, providers, interceptors, approvals, outputs, and audit sinks. |
| Contract probe runner | Execute deterministic local probes through real application dispatch seams, not only an interceptor unit API. |
| GitHub Copilot plane assessor | Evaluate pull-request, ownership, required-check, workflow pinning, and deployment-identity controls. |
| Evidence normalizer | Canonicalize statuses, hashes, evidence references, freshness, and residual risks. |
| Renderer | Atomically write the manifest, evidence pack, and namespaced apply plan. |
| Optional scaffold renderer | Write only the fixed safe-by-default MAF skeleton set after explicit opt-in. |

### 6.2 Inputs

Required assessment inputs:

- `specs/SPEC.md` section 8 for `design` and `pre-deploy`; its absence is a
  missing prerequisite, not an empty action inventory;
- tool/action registry and agent tool declarations;
- agent runtime, middleware, workers, and dispatch code;
- policy files and policy schemas;
- approval issue/consume handlers;
- local tests, conformance reports, CTK reports, and eval results;
- `.github/workflows/**`, `CODEOWNERS`, and repository configuration evidence;
- dependency manifests and lock files.

Optional live evidence:

- GitHub branch protection or ruleset state and required-check state;
- GitHub environment protection and workflow identity claims;
- Azure role assignments and federated identity configuration;
- non-destructive staging canary results;
- deployed telemetry queries for denial, replay, interceptor failure, and audit
  delivery.

If an input cannot be read, queried, or correlated, affected checks become
`not-verified`. The assessor does not infer success from adjacent artifacts.

## 7. Runtime plane

### 7.1 Action inventory

The inventory is built from both SPEC and implementation. A declared action
without an implementation and an implemented action absent from SPEC are both
visible findings.

Each normalized action records:

- stable `action_id`;
- display name and aliases;
- provider/tool-service owner;
- declaration and implementation references;
- input/output schema hashes;
- execution modes;
- consequence class;
- reversibility and compensation metadata;
- declared approval and policy requirements;
- known runtime paths; and
- inventory status.

Classification is exactly one primary class:

| Class | Meaning | Governance expectation |
|---|---|---|
| `read` | No state mutation and no data leaves the declared trust boundary. | Authorization and output controls still apply; pre-action mediation may be policy-dependent. |
| `write` | Mutates durable or externally visible state. | Pre-action mediation required. |
| `external-egress` | Sends data or communication outside the declared trust boundary, whether or not state changes locally. | Pre-action mediation and output/egress controls required. |
| `irreversible` | Cannot be reliably rolled back, or creates legal, financial, safety, identity, or destructive effect. | Pre-action mediation, explicit approval policy, service-side controls, and strongest audit evidence required. |

When an action fits more than one class, precedence is `irreversible`,
`external-egress`, `write`, then `read`. Secondary flags preserve all applicable
properties. Unknown consequence is not treated as `read`; it is `must-fix`
until classified.

### 7.2 Mediation graph

The graph must enumerate these path families even when the project claims they
are unused:

- interactive request;
- batch job;
- background task or queue consumer;
- subagent delegation;
- direct tool invocation;
- provider-hosted tool invocation.

Each path is an ordered graph:

```text
entry -> host/worker -> agent/subagent -> tool router -> pre-action seam
      -> approval check (when required) -> tool service -> post-action seam
      -> output mediator -> caller -> audit sink
```

Edges cite code or configuration evidence. A graph node may be
`not-applicable`, but only with evidence that the corresponding execution mode
is disabled or absent. A consequential path that reaches a tool service without
pre-action mediation is `must-fix`.

Provider-hosted side-effecting tools are `must-fix` with reason `unsupported`
when the
host cannot intercept before execution. They may pass only when equivalent
server-side pre-action control is evidenced against the criteria in section 5.
Post-hoc logs or output filters are not equivalent.

### 7.3 Deterministic application-path probes

A Conformance Test Kit (CTK) claim or upstream conformance report is useful
dependency evidence, but is insufficient by itself. The probe runner must drive
the target application's dispatch path with synthetic fixtures and prove:

| Probe | Required result |
|---|---|
| Deny | A denied consequential action never reaches the tool service; denial is correlated in audit evidence. |
| Transform | The tool service receives exactly the authorized transformed arguments, and the original/transformed hashes are recorded without payloads. |
| Interceptor crash | The action is blocked and the failure is visible; no success-shaped fallback occurs. |
| Interceptor timeout | The action is blocked after a bounded timeout and produces an operational signal. |
| Malformed verdict | Unknown, incomplete, or schema-invalid verdicts deny by default. |
| Approval replay | A redeemed approval cannot authorize a second execution. |
| Mutated action | An approval for one canonical action hash cannot authorize changed arguments, target, tenant, actor, policy version, or expiry. |
| Output buffering | Protected output is not released before post-action/output mediation completes. |
| Payload-free audit | Required metadata is present while prompts, arguments, outputs, secrets, tokens, and customer records are absent. |

Approval evidence binds a one-time token or record to:

- canonical action hash;
- action identifier and target scope;
- requesting subject and approving subject/role;
- tenant or customer boundary;
- policy identifier and policy hash;
- issued-at and expires-at values;
- nonce/redemption identifier; and
- one-time atomic redemption state.

For streaming output, `pass` requires mediation before each protected chunk is
released. Buffering the complete output is the default reference posture.
Streaming that releases protected content before a verdict, or that cannot stop
after mediator failure, is `must-fix`.

### 7.4 Audit record minimum

Audit evidence must contain metadata sufficient to reconstruct the decision
without storing customer payloads:

- event schema/version;
- timestamp;
- source commit and runtime build/deployment identifier;
- action, path, policy, and rule identifiers;
- policy and canonical action hashes;
- verdict and stable reason code;
- approval redemption hash when applicable;
- correlation/trace identifier;
- interceptor duration and error class; and
- audit sink delivery status.

Raw prompts, model output, tool arguments, tool output, access tokens, secrets,
attachments, and customer records are prohibited from the governance evidence
artifacts. Evidence references point to protected systems rather than copying
sensitive records.

## 8. Runtime adapter contract and MAF-first posture

The first implementation supports MAF. Framework-specific discovery is isolated
behind an adapter that emits the same normalized model:

```text
adapter_id
detect(target) -> detection evidence
resolved_tuple(target) -> complete dependency/spec tuple
discover_entry_points(target) -> entry points and execution modes
discover_actions(target) -> normalized action candidates
discover_mediation(target) -> seams and evidence references
build_probe_cases(target, inventory, graph) -> deterministic probe cases
run_local_probe(case) -> normalized probe result
```

Adapters cannot mark checks `pass`; they emit evidence and normalized
observations. The core assessor owns status decisions.

MAF is recommended first because it provides the narrowest useful integration
surface for the current Agent Hooks work. Cross-framework-first was rejected
because it would multiply unstable adapters before the evidence contract is
proven.

## 9. Upstream maturity, pins, and drift policy

The design treats the current upstream surface as pre-stable:

- Agent Hooks `AGENT-HOOKS-0.1` is Draft/alpha.
- The currently observed `agent-hooks-sdk` version is `0.1.0a5`.
- MAF integration is experimental.
- The published conformance report observed during design was for
  `agent-framework-core` `1.13.0`; newer package versions may exist.

The implementation must not convert those observations into floating support
claims. Every assessment records the complete tested tuple:

- Agent Hooks specification identifier and immutable revision/digest;
- `agent-hooks-sdk` package version and resolved artifact hash;
- `agent-framework-core` and all relevant integration package versions;
- ACS/policy schema version;
- CTK package version or source commit;
- application probe-suite version;
- Python runtime version; and
- conformance claim/report reference and hash.

Passing evidence is valid only for that tuple. Any tuple change, missing pin,
open dependency range, changed report hash, or upstream specification drift
sets `PIN-001` to `must-fix` or `not-verified` as defined in section 13 and
requires CTK plus application-path probes to rerun. No compatibility is inferred
from semantic version ordering.

## 10. GitHub Copilot change plane

Agent Hooks cannot intercept GitHub Copilot's internal loop. This plane governs
the outputs of coding agents through the code-change and deployment supply
chain.

The assessor verifies:

1. agent-produced changes enter protected branches through pull requests only;
2. CODEOWNERS covers governance policy, runtime mediation, approval logic,
   workflows, infrastructure, and evidence-schema paths;
3. branch protection or rulesets require reviews, CODEOWNER approval where
   applicable, current required checks, and no unauthorized bypass;
4. CI runs CTK and explicit application-path probes, plus relevant eval suites;
5. third-party and first-party GitHub Actions are pinned to full commit SHAs;
6. workflow permissions are least-privilege;
7. Azure authentication uses OIDC/Workload Identity Federation (WIF), not
   long-lived deployment secrets;
8. build/test identities cannot deploy, and production deployment identities
   are separated from development/staging identities and scoped to the narrowest
   practical target;
9. environments protect production deployment with explicit reviewers or
   equivalent policy; and
10. required-check, workflow, CODEOWNERS, ruleset, and identity evidence is
    fresh and bound to the assessed repository and commit.

Repository files alone cannot prove live branch protection, required checks, or
Azure role assignments. Without live read permissions or a signed/exported
evidence snapshot, those checks are `not-verified`.

## 11. Lifecycle

### 11.1 `design`

- Validate SPEC section 8 and SAFE-derived governance requirements.
- Build and classify the action inventory from SPEC and source.
- Identify unknown, undeclared, or contradictory consequential actions.
- Establish the intended execution modes, approval semantics, output posture,
  and audit minimization requirements.
- Emit preliminary evidence with runtime and GitHub Copilot live checks marked
  `not-verified`.

### 11.2 `pre-deploy`

- Build the static mediation graph.
- Verify policy and dependency/spec pins.
- Verify approval binding and fail-closed implementation evidence.
- Run all deterministic local application-path probes.
- Assess workflow, CODEOWNERS, action SHA pinning, required-check declarations,
  OIDC/WIF configuration, least privilege, and deployment identity separation.
- Emit a gate-ready manifest and remediation plan.

### 11.3 `post-deploy`

- Require a non-production staging target.
- Run only non-destructive canary probes: deny, dry-run/no-op transform,
  synthetic approval replay against a non-side-effecting fixture, output hold,
  and audit-delivery checks.
- Query optional live GitHub and Azure evidence when permissions are available.
- Never create a real irreversible effect to prove governance.
- Finalize the evidence pack, freshness window, and residual-risk register.

The recommended pipeline position is:

```text
threadlight-design
  -> threadlight-governed-actions --phase design
  -> build/deploy preparation
  -> threadlight-governed-actions --phase pre-deploy --gate
  -> threadlight-deploy
  -> threadlight-safe-check --phase post-deploy
  -> threadlight-governed-actions --phase post-deploy --gate
  -> threadlight-production-ready
```

`threadlight-auto` may recognize the artifacts and recommend the next explicit
step. It must not automatically apply enforcement changes or roll the pilot into
production. Those changes are high impact and require a manual, reviewable
handoff.

## 12. Outputs

### 12.1 Primary artifacts

| Path | Purpose |
|---|---|
| `docs/governance/evidence-pack.md` | Customer-facing scope, architecture, pass/fail matrix, evidence index, limitations, residual risk, and remediation summary. |
| `tests/governed-actions-manifest.json` | Canonical machine-readable assessment and integration contract. |
| `tests/governed-actions-apply-plan.json` | Namespaced, ordered remediation plan. The namespaced path avoids collision with production-ready's apply plan. |

Normal assessment is read-only with respect to source, policy, GitHub, and Azure.
`--emit` may write only these three evidence artifacts using atomic replacement
after schema validation.

### 12.2 Manifest contract

The v1 shape is:

```jsonc
{
  "schema": "threadlight-governed-actions-manifest/v1",
  "assessor": {
    "name": "threadlight-governed-actions",
    "version": "0.1.0",
    "adapter": "maf/v1"
  },
  "phase": "pre-deploy",
  "captured_at": "2026-09-01T13:00:00Z",
  "source": {
    "repository": "owner/repository",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "dirty": false
  },
  "pins": {
    "dependencies": [],
    "specifications": [],
    "probe_suite": {
      "version": "0.1.0",
      "sha256": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  },
  "policy_hashes": [
    {
      "path": "policies/governance.yaml",
      "sha256": "sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
    }
  ],
  "action_inventory": [],
  "mediation_paths": [],
  "conformance": {
    "claims": [],
    "reports": [],
    "application_probes": []
  },
  "change_plane": {
    "repository": {},
    "workflows": {},
    "identities": {}
  },
  "findings": [],
  "evidence": [],
  "freshness": {
    "status": "fresh",
    "valid_for_hours": 24,
    "oldest_source_at": "2026-09-01T13:00:00Z",
    "expires_at": "2026-09-02T13:00:00Z"
  },
  "residual_risks": [],
  "summary": {
    "verdict": "partial",
    "pass": 0,
    "must_fix": 0,
    "should_fix": 0,
    "not_verified": 0,
    "not_applicable": 0
  }
}
```

Required manifest content includes schema and assessor version, source commit,
complete dependency/specification pins, policy hashes, action inventory,
mediation-path coverage, conformance claim/report references, explicit
application-probe results, findings, evidence references, freshness, and
residual risk.

All paths are repository-relative. Lists have stable sort keys. JSON uses
canonical key ordering and UTF-8 with a trailing newline. Tests inject a fixed
clock and source commit so golden outputs are byte-for-byte deterministic.

### 12.3 Apply-plan contract

Each plan item records:

- finding ID and current status;
- runtime or GitHub Copilot plane;
- affected actions and paths;
- remediation kind: `repo-edit`, `sibling-skill`, `manual`, or
  `deferred-to-pipeline`;
- exact evidence required to close the finding;
- owner only when declared by the customer;
- dependencies on earlier plan items; and
- manifest SHA-256.

The plan is never self-applying. A consumer must reject it when the current
manifest hash differs from the plan's `manifest_sha256`.

## 13. Finding model

### 13.1 Status taxonomy

Status values match Threadlight conventions:

| Status | Meaning |
|---|---|
| `pass` | Required evidence exists, is fresh, is source-bound, and proves the check for the tested scope. |
| `must-fix` | Negative evidence proves an unsafe path or a mandatory static/runtime control is absent. |
| `should-fix` | The control works, but assurance, maintainability, or operational depth is below the recommended posture. |
| `not-verified` | The assessor lacks permissions, live evidence, correlation, or a runnable proof. This is never counted as pass. |
| `not-applicable` | The capability is outside the evidenced project scope. A reason and supporting evidence are required. |

The overall verdict is:

- `governed`: no `must-fix`, `should-fix`, or `not-verified` findings;
- `partial`: no `must-fix`, but advisory or unverified evidence remains;
- `ungoverned`: one or more `must-fix` findings.

### 13.2 Finding IDs

| ID | Plane | Requirement | Default failure |
|---|---|---|---|
| `ACT-001` | Runtime | SPEC/code action inventory is complete and every action is classified. | Unknown or unclassified consequential action: `must-fix`. |
| `ACT-002` | Runtime | Declared and implemented action sets agree, including aliases and provider tools. | Drift involving a consequential action: `must-fix`; non-consequential stale docs: `should-fix`. |
| `MED-001` | Runtime | Every consequential path reaches evidenced pre-action mediation. | Any bypass path: `must-fix`. |
| `MED-002` | Runtime | Interactive, batch, background, subagent, and direct-tool paths are explicitly covered or evidenced absent. | Known consequential bypass: `must-fix`; inability to determine whether a path exists: `not-verified`. |
| `MED-003` | Runtime | Provider-hosted side-effecting tools are pre-interceptable or have equivalent server-side controls. | Not pre-interceptable without equivalent proof: `must-fix` with reason `unsupported`. |
| `ENF-001` | Runtime | Deny and transform application-path probes pass. | Tool reached after deny or transform mismatch: `must-fix`. |
| `ENF-002` | Runtime | Crash, timeout, and malformed verdict fail closed. | Any fail-open behavior: `must-fix`. |
| `APR-001` | Runtime | Approval is action-, actor-, tenant-, policy-, expiry-, and nonce-bound with atomic one-time redemption. | Replay or mutation succeeds: `must-fix`. |
| `OUT-001` | Runtime | Protected output is mediated before release, including streaming posture. | Pre-verdict release or unstoppable stream: `must-fix`. |
| `AUD-001` | Runtime | Decision audit is complete, correlated, delivered, and payload-free. | Missing decision evidence or sensitive payload: `must-fix`. |
| `PIN-001` | Both | Complete tested tuple and evidence hashes are pinned; upgrades rerun CTK and application probes. | Drift without rerun: `must-fix`; evidence inaccessible: `not-verified`. |
| `GHCP-001` | Change | Protected branches accept agent changes through pull requests only. | Direct/unprotected change path: `must-fix`. |
| `GHCP-002` | Change | CODEOWNERS, branch/ruleset protection, and current required checks cover governance-critical paths. | Missing protection/coverage: `must-fix`; live state unavailable: `not-verified`. |
| `GHCP-003` | Change | CI runs CTK, application probes, and relevant evals as required checks. | CTK-only or non-required checks: `must-fix`. |
| `GHCP-004` | Change | Actions use full SHA pins and workflow permissions are least-privilege. | Floating action or excessive permission: `must-fix`. |
| `GHCP-005` | Change | Azure deployment uses OIDC/WIF without long-lived secrets. | Secret-based deployment identity: `must-fix`. |
| `GHCP-006` | Change | Build/test/deploy identities are separated and deploy scope is least-privilege. | Shared or over-broad production identity: `must-fix`; role evidence unavailable: `not-verified`. |
| `OPS-001` | Both | Alerts cover bypass, fail-closed events, replay, audit failure, and protection/pin drift. | Missing alert on a governed production path: `should-fix`; alert failure evidence: `must-fix`. |

Each finding records its lifecycle phase, affected actions/paths, evidence
references, stable reason code, remediation, and residual-risk link.

## 14. CLI and exit semantics

Suggested interface:

```bash
# Read-only assessment; prints report, writes nothing.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase design

# Emit the three evidence artifacts.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase pre-deploy --emit

# Gate pre-deploy evidence.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase pre-deploy --emit --gate

# Optional live evidence. Missing permissions remain not-verified.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase post-deploy --emit --gate \
  --repo owner/repository --staging-resource-group rg-staging

# Explicit bounded scaffold; no business policy is inferred.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --scaffold maf --confirm-scaffold
```

| Code | Meaning |
|---|---|
| `0` | Assessment completed; when `--gate` is set, selected-phase gate requirements passed. |
| `1` | Gate failed because a `must-fix` or selected-phase required `not-verified` finding remains. Valid artifacts are still emitted. |
| `2` | Invalid input or missing prerequisite prevents a valid assessment. |
| `3` | Assessor, adapter, probe-runner, or artifact-write failure. No partial artifact replaces prior valid evidence. |

Without `--gate`, findings do not change the successful assessment exit code.
With `--gate`, design requires no inventory `must-fix`; pre-deploy requires all
mandatory static checks and local probes to pass; post-deploy additionally
requires selected live evidence, while explicitly optional evidence may remain
`not-verified` and keeps the overall verdict `partial`.

## 15. Error handling and evidence integrity

- Required inputs are validated before probes run.
- Parse errors identify the file and affected checks; they never produce empty,
  success-shaped observations.
- Adapter detection ambiguity is `not-verified`, not an arbitrary adapter choice.
- A probe timeout, runner crash, malformed result, or lost child process is a
  valid negative result for fail-closed checks and a tooling error only when the
  application outcome cannot be observed.
- Permission failures record the missing GitHub/Azure capability and the exact
  checks affected.
- Evidence must match repository, commit, phase, target environment, policy
  hashes, and tested tuple. A mismatch is stale or invalid, not reusable.
- Artifact payloads are schema-validated in memory and written with atomic
  replacement. Failure preserves the prior valid artifacts.
- Evidence references are allowlisted repository-relative paths or opaque
  protected-system references. Absolute local paths, credentials, and query
  results containing customer data are rejected.
- A dirty source tree is recorded and prevents a `governed` verdict because the
  evidence cannot bind to a unique source commit.

## 16. Optional bounded scaffold

Scaffolding requires both `--scaffold maf` and `--confirm-scaffold`. It refuses
to overwrite any existing path and writes only:

```text
src/governance/agent_hooks_interceptor.py
policies/governed-actions.policy.yaml
tests/governance/approval-binding-fixture.json
tests/governance/test_governed_actions_contract.py
.github/workflows/governed-actions.yml
```

The generated policy is syntactically valid, versioned, and deny-by-default for
all consequential actions. It contains no allow rules, business thresholds,
named approvers, tenant identifiers, role assignments, or deployment target.
The approval fixture uses synthetic identifiers. The test and workflow skeletons
exercise only local fixtures and are SHA-pinned.

The scaffold does not make the project pass. Until the customer explicitly
defines and reviews its policy and wiring, affected findings remain
`must-fix`/`not-verified`. Full policy authoring delegates to `foundry-agt`;
pipeline expansion delegates to `threadlight-cicd`.

## 17. Operational alerts

The evidence pack checks for alert definitions and, post-deploy when permitted,
their live state:

- consequential action observed on an unknown or unmediated path;
- interceptor crash, timeout, malformed verdict, or fail-closed denial;
- approval replay, expired approval, or action-hash mismatch;
- output-mediator failure or protected streaming release;
- audit sink delivery failure or schema rejection;
- policy, specification, dependency, CTK, or application-probe tuple drift;
- required-check, CODEOWNERS, workflow SHA pin, branch/ruleset, or environment
  protection drift; and
- deployment identity, federated credential, or role-assignment drift.

Alerts must include stable reason codes and correlation identifiers, not
customer payloads. Missing production alerting is `should-fix`; evidence that
the audit/alert path silently drops mandatory governance events is `must-fix`.

## 18. Security and privacy

- Default deny for unknown consequential actions and unknown verdicts.
- No production or irreversible probes.
- No secrets, tokens, prompts, tool arguments, tool outputs, customer records,
  or raw approver personal data in committed artifacts. Approval evidence uses
  a stable pseudonymous subject identifier or protected-system reference.
- Evidence uses hashes and stable identifiers; hashes are integrity references,
  not a claim that low-entropy sensitive values are safe to publish.
- Live access is read-only and least-privilege. The assessor never changes
  GitHub, Azure, or runtime policy state.
- Approval redemption must be atomic and service-side; client/UI state is not
  authoritative.
- Tool services re-authorize every request and enforce idempotency and
  transaction boundaries even after a runtime approval.
- Audit access, retention, deletion, regional storage, and legal hold remain
  customer policy inputs and are reported as residual risk when not evidenced.

## 19. Testing and fixtures

Tests are offline, deterministic, secret-free, and contain no customer data.
Golden tests use a fixed clock, source commit, repository identity, and sorted
filesystem traversal.

Required fixtures:

| Fixture | Expected result |
|---|---|
| Conformant MAF runtime | All declared consequential paths mediated; local probes pass. |
| Unmediated background/batch path | `MED-001` and `MED-002` `must-fix`. |
| Provider-hosted side-effecting tool | `MED-003` `must-fix` unless equivalent server-side pre-action evidence is present. |
| Unbound/replayed approval | `APR-001` `must-fix` for replay and mutated-action variants. |
| Interceptor crash/timeout | Tool service is not reached; fail-closed probe passes. A fail-open variant produces `ENF-002` `must-fix`. |
| Output streaming posture | Buffered/chunk-mediated variant passes; pre-verdict release produces `OUT-001` `must-fix`. |
| Unprotected GitHub Copilot branch/change path | `GHCP-001`/`GHCP-002` `must-fix`. |
| Upstream version drift | `PIN-001` blocks prior conformance reuse until CTK and application probes rerun. |

Additional tests cover:

- duplicate aliases and SPEC/code inventory drift;
- malformed policy, verdict, report, and evidence JSON;
- deterministic canonical hashes and golden artifacts;
- stale, wrong-commit, wrong-repository, wrong-environment, and wrong-policy
  evidence;
- no-permission GitHub/Azure paths becoming `not-verified`;
- payload rejection from manifests, reports, and audit fixtures;
- atomic writes and preservation of the prior valid artifact;
- scaffold explicit-confirmation, fixed path allowlist, no overwrite, deny-all
  default, and no customer-specific values;
- production-ready consumption without reimplementing detailed checks; and
- threadlight-auto recommendation-only behavior.

The exact upstream pin and drift policy in section 9 is part of the fixture
contract, not test documentation.

## 20. Integration with production-ready and lifecycle

`threadlight-production-ready` consumes
`tests/governed-actions-manifest.json` in:

- agent-governance for mediation, enforcement, and pin evidence;
- HITL for approval binding and anti-replay evidence; and
- supply-chain for the GitHub Copilot change plane, SHA pins, OIDC/WIF, and
  identity separation.

The consumer validates schema, source commit, policy hashes, phase, freshness,
and verdict. It maps detailed results to existing pillar outcomes without
re-running or duplicating this skill's checks. Missing, stale, mismatched, or
partial manifests retain `not-verified`/open findings.

Existing `specs/govern-manifest.json`, eval manifests, safe-check manifests, CTK
reports, and conformance claims are evidence inputs, not substitutes for the new
manifest.

## 21. Rejected alternatives

### 21.1 Extend `threadlight-production-ready`

Rejected because production-ready is already a broad cross-pillar assessor.
Embedding action discovery, graph construction, probes, runtime adapters, and
GitHub Copilot supply-chain depth there would make the scorecard own a complex
execution leg and duplicate its assessor/remediation boundary.

### 21.2 Split checks across `threadlight-safe-check` and
`threadlight-hitl-patterns`

Rejected because runtime path coverage, approval anti-replay, output mediation,
audit proof, and change-plane evidence would fragment across unrelated
artifacts. Customers need one source-bound evidence pack and one pass/fail
matrix.

### 21.3 Build cross-framework support first

Rejected because Agent Hooks and MAF integration are still experimental.
Multiple framework adapters would expand unstable surface area before the
normalized evidence and probe contracts are proven. MAF-first plus the adapter
contract preserves a deliberate extension path.

## 22. Implementation phases

One implementation plan should deliver these phases in order:

1. Define manifest/apply-plan schemas, finding catalog, canonical hashing, and
   golden fixture harness.
2. Implement input resolution, action inventory, and the MAF adapter.
3. Implement mediation graph construction and deterministic local
   application-path probes.
4. Implement the GitHub Copilot change-plane static assessor and optional
   read-only live evidence adapters.
5. Render the evidence pack, manifest, residual-risk register, and apply plan
   with atomic writes and freshness/source binding.
6. Add the explicit bounded scaffold.
7. Add production-ready consumption and threadlight-auto recommendation-only
   integration.
8. Add all negative fixtures, upstream drift tests, documentation, and release
   metadata.

No phase applies remediations or deploys a pilot.

## 23. Acceptance criteria

1. A conformant MAF fixture produces all three primary artifacts with a
   `governed` pre-deploy verdict.
2. Every implemented and SPEC-declared action appears exactly once in the
   normalized inventory, with aliases and consequence classification.
3. Unknown classification never defaults to `read`.
4. Every consequential interactive, batch, background, subagent, direct-tool,
   and provider-hosted path is either pre-action mediated or explicitly fails.
5. A CTK claim without passing application-path probes cannot produce
   `governed`.
6. Deny, transform, crash, timeout, malformed-verdict, replay, mutation,
   output-mediation, and payload-free-audit probes have deterministic positive
   and negative fixtures.
7. Provider-hosted side-effecting tools without pre-interception or equivalent
   server-side proof are `must-fix`/unsupported.
8. Approval replay or mutation never reaches the tool service.
9. Protected output is not released before mediation.
10. Missing GitHub or Azure permissions produce `not-verified`, never `pass`.
11. An unprotected agent-produced change path, floating action, secret-based
    deploy identity, or shared/over-broad production identity is `must-fix`.
12. The manifest records and binds the complete tested tuple, source commit,
    policy hashes, conformance references, application probes, freshness, and
    residual risk.
13. Any upstream tuple drift invalidates prior passing conformance until CTK and
    application probes rerun.
14. Golden outputs are byte-for-byte deterministic and contain no secrets or
    customer data.
15. Normal assessment changes no customer source, policy, GitHub, Azure, or
    runtime state.
16. Scaffold mode requires explicit double opt-in, writes only the five fixed
    files, refuses overwrite, and introduces no customer policy decisions.
17. Post-deploy probes are staging-only and non-destructive.
18. Production-ready consumes the manifest across governance, HITL, and
    supply-chain pillars without duplicating detailed checks.
19. Threadlight-auto recommends explicit governed-actions lifecycle steps but
    never applies enforcement or production rollout automatically.
20. The evidence pack states the trust limitations in section 5 and does not
    describe conformance as certification.
