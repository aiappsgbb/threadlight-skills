# Threadlight AGT + SAFE Runtime Governance

- **Status:** Approved for implementation planning
- **Date:** 2026-09-04
- **Repository:** `aiappsgbb/threadlight-skills`
- **Delivery shape:** one implementation pull request
- **Primary outcome:** generated agents can opt into real, selective runtime
  governance. Threadlight proves the selected controls execute on the deployed
  path and never equates a policy file or assessment with enforcement.

## 1. Problem

Threadlight currently has two incompatible governance stories:

1. `threadlight-govern` can emit `verdict: governed` from policy files, CI
   workflows, and an `agt verify` artefact even when the generated agent has no
   governance middleware.
2. `threadlight-governed-actions` assesses repository evidence and synthetic
   paths, but deliberately does not install or operate a runtime control.

The standard generated agent also has no production enforcement contract:

- MAF agents are constructed without Agent Hooks or an ACS policy engine.
- GHCP agents can execute MCP tools without an equivalent full lifecycle hook
  boundary.
- the current AGT policy templates target the retired v4 rule model and do not
  load in the current ACS runtime;
- deployment and readiness checks can verify artefacts without proving a denied
  deployed action executed zero side effects.

The result is evidence about governance rather than governed execution.

## 2. Corrected governance model

The implementation uses the following layers and does not collapse them into
one feature name.

| Layer | Responsibility | Not responsible for |
| --- | --- | --- |
| **SAFE** | Method for defining the required invariants: Scope, Anchored Decisions, Flow Integrity, Escalation | Executing policy or blocking tools |
| **AGT 5** | Microsoft toolkit umbrella for policy, audit, identity, runtime integration, and governance tooling | Automatically governing an arbitrary host that is not wired |
| **ACS** | AGT 5 policy layer and deterministic policy decision point (PDP); evaluates manifests and Rego policy at lifecycle intervention points | Enforcing its verdict |
| **Agent Hooks** | Framework-neutral host/interceptor contract and policy enforcement point (PEP) surface | Supplying business policy |
| **Host adapter** | Builds trusted snapshots, invokes interceptors, applies transforms, blocks denied actions, gates output/persistence | Replacing downstream authorization |
| **Tool service** | Repeats identity, tenant, scope, schema, idempotency, and transaction checks before the side effect | Trusting the model or caller merely because a hook allowed it |
| **ASSERT** | Tests complete trajectories and regressions | Runtime enforcement |
| **Observability** | Records spans, metrics, decisions, and operational health | Making allow/deny decisions |

Current upstream baseline:

- AGT `5.0.0` is Public Preview.
- ACS Python package is `agent-control-specification==0.3.1b0`.
- Agent Hooks SDK is alpha; current package line is `0.1.0a5`.
- Agent Hooks integration in Microsoft Agent Framework Python is experimental.
- MAF hosted-agent container deployment is GA, while some hosting and governance
  SDK packages remain beta/experimental.

These statuses are disclosed. They do not weaken the technical gates for a
selected control.

Primary references:

- <https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/build-a-safe-agent-on-microsoft-foundry/4547570>
- <https://github.com/placerda/safe-agent-on-foundry>
- <https://learn.microsoft.com/en-us/agent-framework/agents/agent-hooks>
- <https://github.com/responsibleai/agent-hooks>
- <https://github.com/microsoft/agent-governance-toolkit/tree/main/policy-engine>

## 3. Design principles

1. **Governance is selective.** A user chooses which tools and intervention
   points receive policy. A safe read-only tool is not forced through ACS.
2. **A selected binding is load-bearing.** If a tool is bound to policy, a
   missing or unhealthy control cannot silently allow that tool.
3. **Coverage replaces labels.** Reports state what is enforced, observed,
   unbound, unsupported, or unverified. They do not label the whole agent
   `governed` from partial evidence.
4. **Enforcement and policy management are separate.** Policy is managed
   centrally but evaluated close to the action.
5. **The host is trusted but verified.** Agent Hooks is cooperative, so
   production-path tests and live denial receipts are mandatory.
6. **The service repeats authorization.** In-process allow is never accepted as
   sufficient authorization for a side effect.
7. **No silent fallback.** Failure affects only the bound tool or intervention
   point, not unrelated native tools or the entire agent.

## 4. User-facing governance contract

### 4.1 Agent mode

The Foundation and deployment manifest add:

```yaml
governance:
  mode: off | selective | comprehensive
  environment_mode:
    development: evaluate_only
    staging: evaluate_only
    preproduction: enforce
    production: enforce
```

- `off`: no policy bindings. Threadlight makes no runtime-governance claim.
- `selective`: only explicitly bound tools or lifecycle points are governed.
- `comprehensive`: every supported lifecycle point and tool must have an
  explicit binding or an explicit allow policy.

There is no silent default when the design discovers a write, external-egress,
or irreversible action. Threadlight recommends controls and asks the operator.
The operator may still select `off` or leave a specific tool unbound.

In a production-target profile, a tool with `consequence: unknown` is
`unverified`. A write, external-egress, or irreversible tool may remain
unbound only with an explicit acceptance record containing the tool ID, owner,
justification, review date, and expiry. This does not make the tool enforced;
it makes the uncovered risk visible and reviewable.

### 4.2 Per-tool binding

Tool declarations accept structured records:

```yaml
tools:
  - id: returns_get_case
    consequence: read
    policy_binding: none

  - id: returns_apply_decision
    consequence: write
    policy_binding: returns-write-v1
    enforcement_path: local-agent-hooks
    intervention_points:
      - pre_tool_call
      - post_tool_call
    safe_principles:
      - scope
      - anchored_decisions
      - flow_integrity
    requires:
      approval: false
      durable_audit: true
      idempotency: true
```

`enforcement_path` is one of:

- `local-agent-hooks`: MAF Agent Hooks invokes local ACS before the action;
- `governed-tool-gateway`: the ACA gateway invokes ACS before downstream
  dispatch;
- `none`: no runtime policy binding.

Compatibility is explicit:

| Runtime | `local-agent-hooks` | `governed-tool-gateway` | `none` |
| --- | --- | --- | --- |
| MAF / Responses | supported | supported | supported |
| GHCP / Invocations | unsupported | supported for remote MCP actions | supported |

`policy_binding` resolves to a binding ID inside the one signed bundle pinned by
the agent version. It is not a path, URL, floating alias, or second policy
document.

Legacy string tool entries remain readable and normalize to:

```yaml
policy_binding: none
consequence: unknown
```

They do not crash assessment, and they do not receive inferred governance.
Before a production promotion they must be classified or covered by the
explicit unbound acceptance record above.

### 4.3 Lifecycle bindings

SAFE controls that do not belong to one tool are explicit:

```yaml
governance:
  lifecycle_bindings:
    input: scope-input-v1
    pre_model_call: anchored-context-v1
    post_model_call: model-output-v1
    output: escalation-output-v1
```

Lifecycle bindings may target `agent_startup`, `input`, `pre_model_call`,
`post_model_call`, `output`, or `agent_shutdown`. Tool points remain on the
per-tool record because their policy target is the concrete tool call/result.
An omitted lifecycle point is unbound, not a failure, unless `mode:
comprehensive` or the SPEC explicitly requires that point.

## 5. Deployment topology

### 5.1 Central governance services on ACA

Threadlight generates two separately deployable services:

1. `src/governance-control-plane/`
   - policy bundle catalog and metadata;
   - signing and verification metadata;
   - approval resolver;
   - audit receipt ingestion and query;
   - active policy/version inventory;
   - health and operational signals.
2. `src/governed-tool-gateway/`
   - MCP/tool-facing policy enforcement for bound remote actions;
   - service identity and tenant validation;
   - approval verification;
   - idempotency and transaction keys;
   - durable pre-execution decision receipt;
   - dispatch to the real downstream tool service only after allow.

They may share one Container Apps environment but remain distinct services and
trust boundaries.

The gateway and control plane authenticate with workload identities and
short-lived Entra tokens; no shared API key is accepted. The gateway evaluates
its local signed bundle for ordinary tool decisions. It calls the control plane
only for approval resolution and durable receipt delivery, so a control-plane
outage cannot silently convert a governed action into allow.

### 5.2 Signed policy distribution

The control plane manages policy centrally, but the MAF request path does not
depend on a remote PDP call for every hook:

1. Policy source is validated as a native ACS manifest plus Rego bundle.
2. CI produces a content-addressed, signed bundle.
3. The bundle is published to an immutable OCI or Blob artefact.
4. The agent build retrieves an exact digest, verifies the signature and expiry,
   and embeds the bundle in the image.
5. Agent version, image digest, and policy digest are promoted together.

No `latest` policy reference is permitted in a promoted environment. A changed
policy creates a new agent version and uses normal canary/rollback controls.

The ACA control plane remains the source of inventory, approval, and audit. It
is not a per-token network dependency for the MAF policy decision.

## 6. Runtime adapters

### 6.1 MAF + Responses: full supported path

The MAF adapter is the primary full-governance implementation.

It:

- installs exactly one Agent Hooks bundle as the first `Agent.middleware`
  element;
- registers an ACS interceptor backed by the embedded signed bundle;
- runs `enforce` in production and `evaluate_only` only where declared;
- builds host-verified snapshots, never snapshots supplied by the model;
- supports `input`, `pre_model_call`, `post_model_call`, `pre_tool_call`,
  `post_tool_call`, `output`, startup, and shutdown where selected;
- applies transform write-back to the actual native arguments/messages/results;
- buffers output until the selected output policy allows it;
- binds each nested MAF agent to its own Agent Hooks bundle;
- sends payload-free interception records to the audit path;
- removes the old AGT v4 `agent_os.policies` and parallel middleware policy
  engine instead of stacking two PDPs.

The ACS interceptor evaluates only selected bindings. An unbound tool returns a
native allow without running policy rules for that tool.

Provider-hosted tools cannot be blocked at `pre_tool_call`. A consequential
provider-hosted tool is unsupported for full SAFE coverage and must be replaced
with a framework-executed tool or routed through the governed gateway.

### 6.2 GHCP + Invocations: action-governed path

There is no official full Agent Hooks adapter for the GHCP runtime today.
Threadlight therefore supports a narrower but real posture:

- every bound consequential action must be a remote MCP/tool call through the
  governed tool gateway;
- local, built-in, provider-hosted, or direct side-effecting tools cannot be
  marked enforced;
- the gateway performs ACS evaluation and service-side controls before the
  effect;
- GHCP pre-MCP hooks may add correlation metadata but are not the authoritative
  enforcement boundary;
- the Invocations output stream is not claimed as SAFE output-gated;
- reports use `action-governed`, never `safe-complete`.

For each bound action, the generated identity and network posture must also
prove **effect closure**: the agent has no alternate credential, endpoint,
built-in tool, provider-hosted tool, shell path, or unbound MCP tool that can
reach the same downstream effect outside the gateway. A binding without
effect-closure evidence is `bypassable`, never `enforced`.

Unbound GHCP tools continue to operate normally. This preserves runtime choice
without overstating coverage.

The provider contract leaves room for a future GHCP Agent Hooks host adapter,
but that adapter is explicitly outside this pull request.

## 7. Selective failure semantics

Failure is scoped to the selected binding.

| Failure | Bound tool/point | Unbound tools |
| --- | --- | --- |
| Missing, invalid, or expired bundle | unavailable/denied | unaffected |
| Policy timeout or exception | denied with stable reason | unaffected |
| Approval service unavailable | approval-bound action denied | unaffected |
| Governed gateway unavailable | gateway-bound action unavailable | unaffected |
| Required audit spool cannot write | audit-required action denied | unaffected |
| Audit exporter temporarily unavailable | local durable spool retries | unaffected |
| `evaluate_only` in production | promotion gate fails | runtime is not labelled enforced |

The agent may start when some bindings are unavailable, but the affected tools
return a stable governed-unavailable result before their implementation is
called. The registered surface remains stable in every mode so policy health
does not silently change model planning. Readiness exposes:

- agent available;
- selected bindings healthy/unhealthy;
- policy digest loaded;
- enforcement mode;
- gateway and approval dependencies.

A diagnostic fallback server must never return a healthy readiness result after
governance initialization failure.

## 8. Evidence model

The canonical output becomes `specs/governance-manifest.json`.
`specs/govern-manifest.json` v2 is accepted only as legacy input and never
proves enforcement.

The new manifest records:

```json
{
  "schema": "threadlight-governance-manifest/v1",
  "agent": {
    "runtime": "maf-responses",
    "version": "7",
    "image_digest": "sha256:..."
  },
  "policy_bundle": {
    "id": "returns-safe",
    "version": "1.2.0",
    "digest": "sha256:...",
    "signature_verified": true,
    "expires_at": "..."
  },
  "enforcement": {
    "adapter": "maf-agent-hooks-acs",
    "mode": "enforce",
    "agent_hooks_distribution": "0.1.0a5",
    "agent_hooks_artifact_sha256": "sha256:...",
    "acs_distribution": "0.3.1b0",
    "acs_artifact_sha256": "sha256:..."
  },
  "coverage": {
    "tools_total": 5,
    "tools_bound": 2,
    "tools_enforced": 2,
    "tools_observed": 0,
    "tools_unbound": 3,
    "tools_unverified": 0,
    "tools_unsupported": 0,
    "tools_bypassable": 0
  },
  "bindings": [
    {
      "binding_id": "returns-write-v1",
      "tool_id": "returns_apply_decision",
      "enforcement_path": "local-agent-hooks",
      "intervention_points": ["pre_tool_call", "post_tool_call"],
      "mode": "enforce",
      "safe_principles": ["scope", "anchored_decisions", "flow_integrity"],
      "status": "enforced",
      "policy_digest": "sha256:...",
      "probe_ids": ["deny-returns-write"],
      "evidence_refs": ["EV-..."]
    }
  ],
  "live_probes": [
    {
      "probe_id": "deny-returns-write",
      "binding_id": "returns-write-v1",
      "environment": "preproduction",
      "agent_version": "7",
      "decision": "deny",
      "downstream_effect_delta": 0,
      "decision_receipt_ref": "EV-...",
      "service_oracle_ref": "EV-...",
      "status": "pass"
    }
  ],
  "gaps": []
}
```

The manifest has no whole-agent `governed` boolean. Consumers reason from
declared bindings and coverage.

`bindings.status` is exactly one of `enforced`, `observed`, `unbound`,
`unsupported`, `unverified`, or `bypassable`. Coverage counts are derived from
the binding records and must agree exactly. `live_probes` records only probes
that actually ran against a deployed candidate. `gaps` records every selected
binding that is not enforced and every unclassified production tool; it is not
an optional notes field. Each gap carries `binding_id` or `tool_id`, `status`,
`reason_code`, and `evidence_refs`.

Every decision receipt carries only bounded metadata:

- agent and session identity;
- invocation and tool-call identity;
- tool/action ID;
- argument hash, not raw arguments;
- policy ID, version, and digest;
- decision and stable reason code;
- approval reference when applicable;
- image/agent version;
- timestamp and correlation ID;
- pre-execution receipt ID and post-execution outcome reference.

## 9. Lifecycle integration

### 9.1 `threadlight-design`

- discovers action consequence classes;
- asks for `off`, `selective`, or `comprehensive`;
- asks which tools and lifecycle points are bound;
- records SAFE principles and service-side requirements;
- records whether the selected runtime can satisfy the requested coverage;
- never silently changes GHCP to MAF.

### 9.2 `threadlight-deploy`

- generates the provider contract and chosen runtime adapter;
- updates to the current MAF hosted-agent dependency stack;
- adds AGT 5, ACS, and Agent Hooks exact pins for the MAF governed path;
- generates ACS manifest and policy structure without inventing customer rules;
- generates control-plane and tool-gateway services only when selected;
- packages and verifies the signed policy bundle;
- refuses unsupported bindings rather than dropping them.

### 9.3 `threadlight-govern`

`threadlight-govern` changes from a policy/CI assessor into the producer and
runtime wirer:

- authors the selected binding structure;
- builds the ACS bundle;
- wires the adapter;
- validates runtime loading, not only `agt lint-policy`;
- emits the canonical governance manifest;
- never emits `governed` solely from policy and CI files.

### 9.4 `threadlight-governed-actions`

It remains an independent verifier and receives these fixes:

- output probes use private temporary ledgers and never delete target files;
- every declared bound action must have matching probe coverage;
- static/AST markers may discover candidates but can never contribute to an
  `enforced` status; reachability requires an executed-path receipt or trace
  correlated to the probe;
- approval tests include a second valid fresh nonce and a stale-expiry negative;
- audit and output passes bind to persisted evidence;
- post-deploy verifies a deployed revision rather than rerunning only local code;
- legacy string tool declarations are parsed safely;
- the staging resource group guard validates the target rather than checking
  only that a string was supplied.

### 9.5 `threadlight-safe-check`

Pre-deploy verifies:

- every selected binding is generated;
- runtime/profile compatibility;
- exact dependencies and policy bundle digest;
- governed services and routes are wired;
- no selected consequential tool bypasses its adapter/gateway.
- effect closure: the agent identity, registered tools, credentials, and network
  routes cannot reach the same protected effect outside the declared path.

Post-deploy verifies:

- exact agent version and image digest;
- loaded policy digest and enforcement mode;
- binding health;
- a safe denial canary;
- zero downstream effect for the denied action;
- correlated decision and audit receipts;
- gateway and approval health when selected.

### 9.6 `threadlight-production-ready` and `threadlight-auto`

- consume binding coverage rather than a whole-agent verdict;
- a selected but unproved production binding is `must-fix`;
- an intentionally unbound tool is reported as unbound, not silently passed or
  automatically failed;
- production requirements declared in SPEC section 12 can require a minimum
  coverage posture;
- Auto drives the governance producer/verifier whenever bindings exist instead
  of treating it as a recommendation-only side leg.

The existing `evidence_gate.py` govern predicate is replaced, not extended. A
production readiness assertion requires every production binding to be
`enforced` with live evidence, no `unverified` or `bypassable` production tool,
and every unbound consequential tool to carry a valid acceptance record.
`govern-manifest/v2` alone resolves to `unverified`. `ai_act_evidence.py` maps
Article 9 evidence to `governance-manifest/v1` binding coverage and retains v2
only as legacy provenance.

`threadlight-governed-actions` also drops the whole-agent
`governed|partial|ungoverned` verdict in its next manifest schema. Consumers use
its binding/path coverage and findings; an assessment verdict is never reused as
an enforcement claim.

## 10. Verification strategy

### 10.1 Dependency and API contract

Run live contract probes against exact pins:

- AGT 5 package set;
- ACS Python SDK;
- Agent Hooks SDK;
- MAF core/foundry/hosting;
- OPA binary digest;
- SAFE reference sample commit.

The probe validates imports, signatures, policy loading, hook composition,
middleware ordering, mode semantics, and generated-agent construction.

The single source of truth is
`skills/_shared/governance-upstream-pin.json`. This pull request replaces the
old governed-actions pin with that shared contract, adds AGT and ACS artefacts,
and makes policy-engine construction a tested premise rather than a prose
assumption. The implementation plan pins the exact coherent MAF package set
resolved by this probe.

### 10.2 Agent Hooks conformance

Run the official Agent Hooks CTK against the MAF adapter. The claim includes:

- supported interception points;
- composition profile;
- identity provider;
- buffered-output posture;
- production dispatch-path confirmation.

CTK conformance remains evidence, not certification.

### 10.3 Application-path negative tests

For every selected binding:

- denied action executes zero tool effects;
- policy timeout/crash/invalid verdict denies only that binding;
- transformed arguments reaching the tool match the transformed hash;
- an approval accepts first use, rejects replay/mutation/stale expiry, and
  accepts a second valid binding with a fresh nonce;
- selected output gating releases zero bytes before allow;
- required audit receipt exists and correlates to the decision;
- an unbound tool still executes normally;
- a direct or provider-hosted bypass cannot be reported as enforced.

Zero side effect is measured outside the agent process: the gateway dispatch
counter and the downstream service's own durable ledger/row count are sampled
before and after the probe. An exception, missing function-entry marker, or
mock invocation count inside the agent is supporting evidence only.

For gateway-bound actions, a negative test attempts the same downstream effect
using the agent identity outside the gateway and requires authorization or
network rejection.

### 10.4 Gateway tests

- ACS deny prevents downstream dispatch;
- identity, tenant, scope, and action hashes are bound;
- duplicate idempotency keys create at most one effect;
- approval is action/argument/policy/expiry/subject bound;
- audit receipt is written before dispatch when required;
- downstream authorization still rejects an otherwise policy-allowed request.

### 10.5 Live pre-production proof

Deploy an immutable candidate to a pre-production environment in `enforce`
mode and run:

1. one allowed bound action;
2. one denied bound action;
3. one unbound safe action;
4. one engine/gateway failure injection.

Evidence must show:

- correct agent version, image digest, and policy digest;
- denied tool side-effect count equals zero;
- unbound tool remains available;
- failure affects only the selected binding;
- decision/audit/gateway records correlate;
- App Insights contains bounded governance spans.

Evidence produced under `evaluate_only` can never satisfy this section or mark
a binding `enforced`. Production promotion verifies the exact same candidate
image and policy digests, the production `enforce` mode, and binding health. It
uses a declared no-op deny probe in production when the environment policy
permits it; otherwise it inherits the pre-production denial receipt from the
byte-identical candidate and performs no destructive production canary.

### 10.6 ASSERT trajectory tests

SAFE-bound workflows ship ASSERT scenarios that prove:

- out-of-scope requests are refused;
- decisions require valid host-issued evidence;
- steps cannot be skipped or reordered;
- escalation occurs only when the anchored state requires it;
- selected denial and handoff behavior remains stable after model, prompt,
  policy, or tool changes.

ASSERT results remain regression evidence and never substitute for Agent Hooks
or gateway runtime receipts.

## 11. One-PR implementation scope

The pull request includes one reviewable sequence of commits:

1. contract/schema, shared upstream pin, migration, and consumer predicates;
2. governed-actions correctness fixes;
3. MAF Agent Hooks + ACS runtime adapter and tests;
4. ACA control plane, governed gateway, and GHCP gateway adapter;
5. deployment, safe-check, production-ready, auto, and AI Act integration;
6. returns-triage migration, docs, and complete verification.

Every commit keeps its targeted tests green; the final pull request must satisfy
all acceptance criteria together. The single-PR choice does not permit a
partially implemented profile to be documented as available.

The pull request does not:

- claim Agent Hooks, ACS, or AGT preview surfaces are GA;
- implement a full Agent Hooks host adapter inside GHCP;
- force policy onto every tool;
- invent customer business policy, approvers, or thresholds;
- treat ASSERT, CTK, `agt verify`, or an assessment as runtime enforcement;
- claim legal or regulatory certification.

## 12. Migration

1. Existing `govern-manifest/v2` files become legacy assessment evidence.
2. Existing v4 YAML policies are migrated to native ACS manifest plus
   Rego policy; runtime loading is mandatory.
3. Existing agents default to `governance.mode: off` until the operator chooses
   bindings. Migration never silently activates enforcement. For a
   production-target profile, unknown tools and unbound consequential tools
   become gaps unless they carry the explicit acceptance record from section
   4.1.
4. Existing GHCP agents may adopt `action-governed` by moving selected side
   effects behind the governed gateway.
5. Existing MAF agents may adopt selective or comprehensive Agent Hooks
   enforcement.
6. Reports clearly distinguish legacy assessment, evaluate-only observation,
   action-governed coverage, and full supported SAFE coverage.
7. `threadlight-govern` stops emitting `govern-manifest/v2`; Auto,
   production-ready, evidence-gate, and AI Act consumers migrate atomically to
   `governance-manifest/v1`.

## 13. Acceptance criteria

1. A generated MAF agent with one bound and one unbound tool runs both tools
   normally when policy allows.
2. Denying the bound tool prevents its function body and downstream service from
   executing, proven by gateway and downstream service oracles; the unbound tool
   still works.
3. The same target in `evaluate_only` records the deny but executes and cannot
   produce production-enforced evidence.
4. A broken policy engine disables only bound surfaces.
5. The generated SAFE policy enforces Scope, Anchored Decisions, Flow Integrity,
   and Escalation against host-verified evidence.
6. Agent Hooks is the first and only hook bundle on every governed MAF agent and
   nested agent.
7. A governed GHCP tool executes only through the ACA gateway; the report does
   not claim full SAFE coverage, and an attempted direct path with the agent
   identity is rejected.
8. Policy source, signed bundle, agent image, deployed version, denial receipt,
   and audit record share verifiable hashes and correlation IDs.
9. `threadlight-govern` cannot emit an enforced status for
   `skills/threadlight-govern/references/fixtures/sample-wired/`, whose
   `src/app.py` imports no in-process enforcement.
10. `threadlight-governed-actions` cannot delete repository files, cannot pass
    unprobed bound actions, and cannot use unreachable AST markers as runtime
    proof.
11. The canonical generated returns-triage agent passes the new parser and
    produces an honest coverage report.
12. Production readiness blocks selected bindings without exact deployed
    evidence but does not block intentionally unbound tools merely for being
    unbound; unbound consequential or unknown tools require the explicit,
    current acceptance record.
13. Documentation consistently describes SAFE as methodology, ACS as PDP,
    Agent Hooks as the host/interceptor contract, the host adapter and gateway
    as PEPs, AGT as toolkit, and ASSERT as assurance.
14. No promoted deployment references a floating policy bundle; policy and
    image digests are promoted together.
15. Governance initialization failure cannot produce a healthy readiness result;
    binding-level health and enforcement mode are visible.
16. In comprehensive mode, an unsupported or provider-hosted consequential tool
    prevents promotion.
17. The staging target validator rejects production-named or scope-mismatched
    targets rather than accepting any non-empty resource-group string.
18. The new manifest coverage counts, binding records, live probes, and gaps
    agree exactly under schema validation.

## 14. Success metric

The feature succeeds when a reviewer can choose any selected tool binding and
trace one immutable chain:

```text
SPEC decision
  -> signed policy bundle
  -> generated host/gateway enforcement
  -> exact deployed image + agent version
  -> live allow/deny decision
  -> zero side effect on deny
  -> correlated durable receipt
```

If any link is absent, that binding is not reported as enforced.
