# Opt-in Runtime-Agnostic Tool-Governance Contract - Design

- **Status:** Approved
- **Date:** 2026-08-20
- **Repository:** `aiappsgbb/threadlight-skills`
- **Delivery shape:** one implementation plan and one pull request in this repository
- **Primary owners:** `threadlight-design`, `threadlight-deploy`,
  `threadlight-safe-check`, `threadlight-hitl-patterns`, and
  `threadlight-production-ready`

## 1. Context and motivation

Threadlight already has the right lifecycle boundaries, but it does not yet carry
one explicit contract for tool-level governance across those boundaries:

- `threadlight-design` defines abstract tools in `specs/SPEC.md` section 6 and
  human action gates in section 8, then projects deployment data into
  `specs/manifest.json`.
- `threadlight-deploy` resolves the runtime from `specs/foundation.md` and
  `skills/threadlight-design/references/runtime-policy.json`. The current supported
  routes are `github-copilot-sdk` + agent + Invocations, Microsoft Agent Framework
  (MAF) agent + Responses, and MAF workflow + Responses.
- `threadlight-safe-check` enforces design, pre-deploy, and post-deploy
  completeness through `skills/threadlight-safe-check/scripts/safe_check.py`.
- `threadlight-hitl-patterns` derives seven canonical gate experiences from SPEC
  section 8 and writes the canonical gate audit shape documented in
  `skills/threadlight-hitl-patterns/references/audit-schema.md`.
- `threadlight-production-ready` checks AGT policy and evidence signals in
  `skills/threadlight-production-ready/scripts/production_ready.py`, but it does
  not prove that an allowed tool executed once, a denied tool executed zero
  times, or either decision emitted correlatable audit evidence.

The runtime split makes prompt-only guidance especially unsafe. The default
GitHub Copilot SDK (GHCP SDK) route binds tools through `mcp_servers`; it does not
support the MAF custom Python `@tool` surface. MAF supports in-process tools and
framework middleware. A future .NET Harness may expose `.WithGovernance()`.
Threadlight needs one policy contract that preserves the same decision semantics
across those runtimes without pretending that they share an implementation
mechanism.

## 2. Goals

1. Add one optional, machine-readable `tool_governance` contract to
   `specs/manifest.json`, derived from SPEC sections 6 and 8.
2. Keep SPEC human-readable and authoritative; the manifest is a generated
   downstream contract, not a second policy source.
3. Require an explicit policy decision for every canonical tool when governance
   is enabled.
4. Let `threadlight-deploy` select a supported runtime adapter without changing
   action classification, policy decision, HITL semantics, or audit obligations.
5. Enforce current Python runtimes where feasible:
   - MAF through deterministic in-process pre-tool enforcement supplied by
     `foundry-agt`;
   - GHCP SDK at the MCP server or gateway, never by claiming an in-process tool
     interceptor.
6. Fail visibly when no supported adapter can enforce the declared contract.
7. Prove post-deploy behavior with non-production canaries: allow executes once,
   deny executes zero times, and both decisions produce correlatable audit
   evidence.
8. Let `threadlight-production-ready` consume behavioral probe evidence instead
   of inferring enforcement from imports or policy-file presence alone.
9. Preserve every legacy project, fixture, and generated manifest unless it
   explicitly opts in.

## 3. Non-goals

- Creating a new Threadlight skill.
- Imposing a repository-wide allowlist, denylist, default-deny posture, or action
  classification. Threadlight spans broad domains; policy remains case-by-case.
- Replacing `foundry-agt`, MCP-server policy engines, APIM/gateway policies, or a
  future .NET governance library.
- Treating prompt instructions, AGENTS.md prose, or tool descriptions as
  enforcement.
- Reclassifying existing tools automatically from names such as `get`, `create`,
  or `delete`.
- Governing arbitrary workflow executor code that is not exposed as a canonical
  SPEC section 6 tool.
- Sending mutating probes to production systems.
- Implementing the future .NET Harness adapter in the first delivery.
- Changing the canonical runtime-selection authority in
  `skills/threadlight-design/references/runtime-policy.json`.

## 4. Design invariants

### 4.1 Opt-in compatibility

The feature is enabled only by:

```json
{
  "tool_governance": {
    "enabled": true
  }
}
```

If the block is absent, omits `enabled`, or has `"enabled": false`, validators
ignore all other governance fields and all existing generation, deployment,
safe-check, fixtures, and readiness behavior remains unchanged. If `enabled` is
present but is not a boolean, validation fails as malformed input rather than
guessing whether the project opted in. Validators do not infer opt-in from a
policy file, an AGT import, a section 8 gate, or the word "governance" elsewhere.

When enabled, validation is fail-closed:

- every canonical SPEC section 6 tool appears exactly once in the contract;
- no contract tool is absent from SPEC section 6;
- every tool has one explicit action class and one explicit decision;
- an unclassified or newly added tool is a gap, never an implicit allow;
- every referenced section 8 gate exists;
- every declared enforcement point is wired to a supported adapter;
- prompt-only guidance never satisfies a gate.

### 4.2 Authority and derivation

Authority order is:

```text
SPEC section 6 tool contracts + SPEC section 8 action gates
  -> specs/manifest.json tool_governance
  -> deploy-generated runtime adapter and policy artifacts
  -> runtime audit events
  -> tests/tool-governance-probe-manifest.json
  -> tests/postdeploy-manifest.json
  -> tests/production-readiness-manifest.json
```

SPEC remains the only place where a human defines the tool's meaning, action
class, decision, optional gate, intended enforcement point, policy ID, and audit
requirements. The manifest is regenerated from SPEC. Deploy artifacts carry a
hash of the manifest contract and must not add or weaken policy semantics.

### 4.3 Exact canonical names

For a governed SPEC, section 6 uses one `### <tool-name>` entry per canonical
tool. Grouped headings such as `returns_get_case / returns_list_open` are invalid
when governance is enabled because they cannot be mapped unambiguously.

The canonical name set comes from SPEC section 6. `AGENTS.md` remains a
downstream implementation projection and is checked separately by the existing
skill-contract gate. Name matching is byte-exact and case-sensitive after
removing Markdown code delimiters around the heading token. Aliases and wildcard
patterns are not accepted in `tool_governance.tools[]`.

## 5. Contract schema

### 5.1 SPEC section 6 additions

Each canonical tool gains the following human-readable fields:

| Field | Required when enabled | Allowed values / rule |
|---|---:|---|
| `Action class` | yes | `read`, `reversible-write`, `irreversible-write`, `external-side-effect` |
| `Decision` | yes | `allow`, `deny`, `conditional` |
| `HITL gate ID` | conditional only | Stable `GATE-NNN` defined in SPEC section 8; required when `Decision` is `conditional`, otherwise omitted |
| `Enforcement point` | yes | `agent-middleware`, `mcp-server`, `gateway` |
| `Policy ID` | yes | Non-empty stable identifier; may be shared by multiple tools |
| `Required audit fields` | yes | Unique field names including the common minimum in section 5.4 |

Action classes are mutually exclusive:

- `read`: no persistent state change and no external action;
- `reversible-write`: changes state and has a tested idempotent undo or
  compensation path;
- `irreversible-write`: changes state without a reliable undo;
- `external-side-effect`: communicates or acts outside the controlled data store,
  such as sending a message or invoking an external transaction.

If a tool could fit more than one class, the SPEC author chooses the class that
best describes the operational consequence and records the rationale in the
existing tool description. Threadlight does not infer or override the choice.

### 5.2 SPEC section 8 additions

Every section 8 interaction referenced by a governed tool gains:

- **Gate ID:** unique stable `GATE-NNN`;
- the existing **Action gate** taxonomy value (`approve`,
  `edit-and-approve`, `reject`, `escalate`, `signoff`, `audit-view`, or
  `request-info`);
- **Approval ID propagation:** the approval result returns an `approval_id` and
  the original `correlation_id`.

`gate_id` identifies the SPEC interaction. The existing `gate` field identifies
the canonical UX taxonomy. They are different fields and both are retained in
HITL audit events.

### 5.3 `specs/manifest.json` schema

`tool_governance` is an optional top-level sibling of `deployment_manifest`.
When `enabled` is true, `contract_version` and a non-empty `tools` array are
required.

```json
{
  "tool_governance": {
    "enabled": true,
    "contract_version": "1.0",
    "source": {
      "tool_contracts": "specs/SPEC.md#6-tool-contracts",
      "action_gates": "specs/SPEC.md#8-human-interaction-points"
    },
    "tools": [
      {
        "name": "returns_apply_decision",
        "action_class": "reversible-write",
        "decision": "conditional",
        "gate_id": "GATE-001",
        "enforcement_point": "mcp-server",
        "policy_id": "TG-RETURNS-001",
        "required_audit_fields": [
          "event_id",
          "event_type",
          "timestamp",
          "correlation_id",
          "contract_sha256",
          "policy_id",
          "tool_name",
          "action_class",
          "decision",
          "enforcement_point",
          "adapter_id",
          "actor_id",
          "gate_id",
          "approval_id"
        ]
      }
    ]
  }
}
```

Schema rules:

1. `contract_version` is `1.0` for the initial contract.
2. `tools[].name` is unique and equals one canonical SPEC section 6 tool.
3. Every canonical SPEC section 6 tool has one matching contract entry.
4. The three enums are closed to the values shown above.
5. `policy_id` is required but not required to be unique; one policy may cover
   several tools.
6. `gate_id` is required exactly when `decision` is `conditional`, otherwise it
   is omitted. It must resolve to exactly one SPEC section 8 interaction.
7. `required_audit_fields` contains unique strings and includes the common
   minimum.
8. A `conditional` tool requires `correlation_id`, `approval_id`, and `gate_id`.
9. Unknown keys are rejected while enabled so misspellings do not silently
   remove an obligation.

The contract digest is the SHA-256 of the `tool_governance` object serialized as
UTF-8 canonical JSON with sorted keys and no insignificant whitespace. Derived
artifacts store it as `sha256:<hex>`.

### 5.4 Common audit minimum

Every policy-decision event contains:

- `event_id`
- `event_type` (`policy-decision`)
- `timestamp`
- `correlation_id`
- `contract_sha256`
- `policy_id`
- `tool_name`
- `action_class`
- `decision`
- `enforcement_point`
- `adapter_id`
- `actor_id`

Tools may require more fields through `required_audit_fields`. A tool linked to a
HITL gate also requires `gate_id` and `approval_id`. An allowed execution emits
a correlatable `tool-outcome` event with the same `correlation_id`; a denied
decision emits no tool-outcome event because the tool body must not run.

Raw tool arguments and results are excluded by default. A SPEC may require
classified hashes or selected non-sensitive fields, but must not make sensitive
payload capture an implicit consequence of enabling governance.

## 6. Architecture and components

### 6.1 `threadlight-design`

`threadlight-design` owns authoring and derivation:

- update `skills/threadlight-design/SKILL.md` so SPEC section 6 documents the
  optional governance fields and section 8 documents stable gate IDs;
- update `skills/threadlight-design/references/speckit-template.md` with the same
  fields and the one-tool-per-heading rule for enabled contracts;
- extend the `specs/manifest.json` example with an optional
  `tool_governance` block;
- retain the current section 6 abstract-tool boundary and section 8 gate
  taxonomy;
- generate no block by default;
- when enabled, project values mechanically from SPEC instead of asking deploy
  to reinterpret prose.

Design does not pick a global decision baseline. The author must classify each
tool. An empty `tools` array is invalid when governance is enabled.

### 6.2 `threadlight-deploy`

`threadlight-deploy` reads:

- the runtime tuple from `specs/foundation.md`;
- the supported combinations and routes from
  `skills/threadlight-design/references/runtime-policy.json`;
- the intended per-tool enforcement points from
  `specs/manifest.json.tool_governance`.

It then selects an adapter that supports both the selected runtime and every
declared enforcement point. It may translate representation, but it must preserve
tool name, action class, decision, gate ID, policy ID, and audit requirements
exactly.

Deploy generates the adapter manifest, policy artifact, and probe entrypoint.
The post-deploy probe writes the evidence manifest at the declared path:

```text
policies/tool-governance/
├── adapter-manifest.json
└── generated/
    └── <adapter-specific-policy-artifact>
tests/
├── tool_governance_probe.py
└── tool-governance-probe-manifest.json  # written by the post-deploy probe
```

`policies/tool-governance/adapter-manifest.json` has this minimum shape:

```json
{
  "schema": "threadlight.tool-governance-adapter/v1",
  "contract_sha256": "sha256:<hex>",
  "runtime": {
    "framework": "github-copilot-sdk",
    "runtime_shape": "agent",
    "protocol": "invocations"
  },
  "bindings": [
    {
      "tool_name": "returns_apply_decision",
      "enforcement_point": "mcp-server",
      "adapter_id": "mcp-tool-governance",
      "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
      "wire_signals": [
        {
          "path": "src/mcp/server.py",
          "kind": "pre-tool-policy-binding"
        }
      ]
    }
  ],
  "audit": {
    "schema": "threadlight.tool-governance-audit/v1",
    "sink": "application-insights"
  },
  "probe": {
    "entrypoint": "tests/tool_governance_probe.py",
    "evidence": "tests/tool-governance-probe-manifest.json"
  }
}
```

`wire_signals` are adapter-defined, inspectable source or configuration
bindings. Their recognized kinds are versioned with the adapter. A file's mere
existence is not a wire signal.

For MAF, deploy invokes `foundry-agt` to generate or wire the supported AGT
integration at a deterministic pre-tool boundary. This design does not invent an
AGT import or API name; the adapter records the actual integration surface
selected by the installed `foundry-agt` version.

For GHCP SDK, deploy binds policy at the MCP server or gateway named by each
tool's contract. It must not add an in-process governance claim to
`CopilotClient` or `InvocationAgentServerHost`.

### 6.3 Runtime adapters

| Runtime/tool shape | Supported initial point | Initial status | Required proof |
|---|---|---|---|
| Python MAF agent, in-process tool | `agent-middleware` | supported where `foundry-agt` exposes the MAF pre-tool integration | adapter manifest, generated AGT policy artifact, pre-tool wire signal, probe evidence |
| Python MAF agent, MCP tool | declared `agent-middleware`, `mcp-server`, or `gateway` | supported only when the selected adapter can intercept that exact boundary | one binding per tool; no substitution without a SPEC change |
| Python MAF workflow, tool inside executor | `agent-middleware` | supported only if every executor that can invoke the tool registers the same pre-tool adapter | binding evidence for every invoking executor |
| GHCP SDK agent, MCP tool | `mcp-server` or `gateway` | supported | MCP/gateway policy binding and probe evidence |
| GHCP SDK agent, in-process Python `@tool` | none | unsupported because the current GHCP route does not support that tool shape | explicit deployment gap |
| Future .NET Harness | future `.WithGovernance()` mapping | planned, not implemented | adapter manifest plus `.WithGovernance()` wire signal and the same probe schema |
| Unknown runtime or boundary | none | unsupported | explicit deployment gap |

An adapter cannot silently move enforcement from the declared point. If a GHCP
tool declares `agent-middleware`, or a MAF tool declares a point the installed
adapter cannot enforce, pre-deploy fails and tells the operator to revise the
SPEC or add a supported adapter.

Kratos-export mode preserves its runtime as it does today. If an exported bundle
opts in, deploy detects the existing runtime and must still find a supported
adapter. It does not rewrite the runtime or ignore the contract.

### 6.4 `threadlight-safe-check`

`skills/threadlight-safe-check/scripts/safe_check.py` remains the lifecycle CLI
and adds governance checks only when the contract is enabled.

#### Design phase

`--phase design` validates:

1. the enabled schema and enums;
2. exact, one-to-one coverage of canonical SPEC section 6 tools;
3. no duplicate or unknown tool names;
4. no grouped section 6 tool headings;
5. required policy and audit fields;
6. every referenced `GATE-NNN` exists exactly once in SPEC section 8;
7. gate-linked audit fields include `gate_id` and `approval_id`;
8. canonical contract digest generation.

The result is added under `tool_governance` in
`tests/safe-check-design-manifest.json`; the existing top-level `gaps` contract
remains unchanged.

#### Pre-deploy phase

`--phase pre-deploy` validates:

1. `policies/tool-governance/adapter-manifest.json` exists;
2. its runtime tuple matches `specs/foundation.md`;
3. its `contract_sha256` matches the current manifest contract;
4. every governed tool has exactly one binding;
5. every binding uses the declared enforcement point;
6. every policy artifact exists and is non-empty;
7. every adapter-specific wire signal resolves;
8. GHCP SDK uses only `mcp-server` or `gateway`;
9. MAF `agent-middleware` bindings identify deterministic pre-tool
   enforcement;
10. no tool is declared but unwired.

An unsupported runtime or boundary is a gap. Prompt instructions and a policy
file without a wire signal do not pass.

#### Post-deploy phase

`--phase post-deploy` runs only mock or adapter-owned canary operations. The
canaries are not exposed as agent tools and do not modify a production system.
The probe must prove:

- allow decision: observed tool-body execution count is exactly `1`;
- deny decision: observed tool-body execution count is exactly `0`;
- both decisions emit a `policy-decision` event with the probe correlation ID;
- the allow path emits one correlatable `tool-outcome` event;
- required audit fields are present;
- each governed HITL decision exercised by the fixture carries `gate_id`,
  `approval_id`, and the original `correlation_id`.

The detailed evidence is written to
`tests/tool-governance-probe-manifest.json`. `tests/postdeploy-manifest.json`
adds a hash and status reference under `tool_governance` while preserving the
existing top-level `gaps` array.

The probe manifest has this minimum shape:

```json
{
  "schema": "threadlight.tool-governance-probe/v1",
  "contract_sha256": "sha256:<hex>",
  "adapter_manifest_sha256": "sha256:<hex>",
  "generated_at": "2026-08-20T00:00:00Z",
  "vectors": [
    {
      "id": "allow-canary",
      "expected_decision": "allow",
      "observed_decision": "allow",
      "expected_execution_count": 1,
      "observed_execution_count": 1,
      "correlation_id": "probe-allow-001",
      "decision_event_ids": ["audit-allow-001"],
      "outcome_event_ids": ["outcome-allow-001"],
      "status": "pass"
    },
    {
      "id": "deny-canary",
      "expected_decision": "deny",
      "observed_decision": "deny",
      "expected_execution_count": 0,
      "observed_execution_count": 0,
      "correlation_id": "probe-deny-001",
      "decision_event_ids": ["audit-deny-001"],
      "outcome_event_ids": [],
      "status": "pass"
    }
  ],
  "audit_field_results": [
    {
      "vector_id": "allow-canary",
      "missing": [],
      "status": "pass"
    },
    {
      "vector_id": "deny-canary",
      "missing": [],
      "status": "pass"
    }
  ],
  "status": "pass"
}
```

The adapter-owned allow and deny canaries test wiring without imposing allow or
deny decisions on the pilot's canonical tools. If the pilot declares
`conditional` tools, the probe also exercises the gate-backed approval path and
verifies approval/correlation propagation.

### 6.5 `threadlight-hitl-patterns`

`skills/threadlight-hitl-patterns/SKILL.md` and
`skills/threadlight-hitl-patterns/references/audit-schema.md` gain additive
support for:

- stable SPEC `gate_id`;
- request `correlation_id`;
- decision `approval_id`;
- `policy_id`, `tool_name`, and `contract_sha256` when an action gate releases a
  governed tool.

Existing fields (`case_id`, `gate`, `decision`, `actor`, `timestamp`,
`linked_rules`, proposal, edits, rationale, SLA) remain unchanged. Existing
ungoverned cards and handlers do not need the new fields.

A gate-backed tool may execute only after the approval event is persisted and
the runtime adapter receives the matching `approval_id` and `correlation_id`.
Replays with the same approval ID must be idempotent and must not execute the
tool body a second time.

### 6.6 `threadlight-production-ready`

The agent-governance pillar remains the owner. It gains additive findings:

| ID | Title | Disabled | Enabled failure |
|---|---|---|---|
| `AGT-007` | Enabled tool-governance contract covers every canonical tool | `not-applicable` | `must-fix` |
| `AGT-008` | Declared tool-governance runtime adapter is wired | `not-applicable` | `must-fix` |
| `AGT-103` | Tool-governance allow/deny probe evidence is current and correlatable | `not-applicable` | dynamic severity below |

`AGT-008` recognizes adapter evidence for:

- Python MAF pre-tool middleware generated through `foundry-agt`;
- MCP-server enforcement;
- gateway enforcement;
- the future .NET `.WithGovernance()` signal.

Detection requires the adapter manifest, matching contract hash, policy artifact,
and resolved wire signals. Import or filename presence alone is insufficient.

`AGT-103` consumes `tests/tool-governance-probe-manifest.json`, verifies its
schema and hashes, and checks the allow/deny execution counts and audit
correlation. Missing, stale, malformed, hash-mismatched, or failing evidence is:

- `must-fix` if the enabled contract contains an `irreversible-write` tool;
- `should-fix` for every other enabled contract;
- `not-applicable` when the contract is disabled.

This keeps the current advisory readiness model while making the result
evidence-based.

#### AGT catalog and pillar cleanup

The same implementation must close the currently documented AGT drift:

- `FINDING_CATALOG` in
  `skills/threadlight-production-ready/scripts/production_ready.py` remains the
  source of truth for finding IDs, titles, default severity, pillar, and tier.
- `skills/threadlight-production-ready/references/pillars/02-agent-governance.md`
  must use the exact catalog title for every documented `AGT-*` row. Longer
  explanation belongs in a separate details column or prose, not in a competing
  title.
- The current `AGT-101` pillar text about a sidecar must be aligned with the
  implemented catalog/check, `Workload identity scoped to AGT-required RBAC`.
- The stale pillar summary in
  `skills/threadlight-production-ready/SKILL.md` that says "AGT module imported
  in app code" must be replaced with policy, adapter, and probe-evidence wording.
- A regression test must parse the pillar table and compare its ID/title pairs
  to `FINDING_CATALOG`, including the new IDs. Any future title drift fails CI.

This cleanup is tightly coupled: the new readiness findings are not trustworthy
if the customer-facing pillar documentation gives the same IDs different names
or semantics.

### 6.7 File-level implementation surface

The subsequent implementation plan is limited to these existing and proposed
surfaces:

| Owner | Path | Change |
|---|---|---|
| Design | `skills/threadlight-design/SKILL.md` | Add optional SPEC section 6/8 authoring and manifest-derivation rules |
| Design | `skills/threadlight-design/references/speckit-template.md` | Add the human-readable fields, stable gate ID, and one-tool-per-heading governed shape |
| Design | `skills/threadlight-design/tests/test_tool_governance_template.py` | New template/schema/backward-compatibility tests |
| Deploy | `skills/threadlight-deploy/SKILL.md` | Add adapter selection, artifact generation, probe generation, and unsupported-runtime behavior |
| Deploy | `skills/threadlight-deploy/tests/test_tool_governance_contract.py` | New runtime mapping and policy-preservation tests |
| Safe check | `skills/threadlight-safe-check/SKILL.md` | Document the three governance lifecycle gates and evidence outputs |
| Safe check | `skills/threadlight-safe-check/scripts/safe_check.py` | Add opt-in design, pre-deploy, and post-deploy validation |
| Safe check | `skills/threadlight-safe-check/tests/test_safe_check.py` | Add schema, coverage, binding, probe, and backward-compatibility tests |
| Safe check | `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/` | Add the focused governed fixture |
| Example parity | `examples/returns-triage-governed/tests/safe_check.py` | Synchronize only if required by the existing byte-parity invariant; do not opt in the example |
| HITL | `skills/threadlight-hitl-patterns/SKILL.md` | Add gate/correlation/approval propagation for governed tools |
| HITL | `skills/threadlight-hitl-patterns/references/audit-schema.md` | Add optional governed-tool audit fields without breaking existing events |
| Readiness | `skills/threadlight-production-ready/SKILL.md` | Replace stale import wording and document evidence-based governance findings |
| Readiness | `skills/threadlight-production-ready/scripts/production_ready.py` | Add `AGT-007`, `AGT-008`, `AGT-103`, adapter detection, and probe consumption |
| Readiness | `skills/threadlight-production-ready/references/pillars/02-agent-governance.md` | Add new findings and align all AGT titles with the catalog |
| Readiness | `skills/threadlight-production-ready/tests/test_tool_governance_evidence.py` | New evidence/severity/detector tests |
| Readiness | `skills/threadlight-production-ready/tests/test_agt_catalog_docs_sync.py` | New catalog-to-pillar drift test |

No implementation file outside this list is required except normal release
metadata and any generated example-safe-check parity update named above.

## 7. Data flow

1. The author classifies every SPEC section 6 tool and references any section 8
   gate by stable ID.
2. `threadlight-design` generates `specs/manifest.json.tool_governance`.
3. `threadlight-safe-check --phase design` proves exact coverage and gate
   integrity.
4. `threadlight-deploy` reads `specs/foundation.md`, resolves the supported
   adapter, invokes `foundry-agt` for MAF where appropriate, and emits the
   hash-bound adapter manifest plus policy artifacts.
5. `threadlight-safe-check --phase pre-deploy` proves the policy and adapter are
   present, consistent, and wired.
6. The adapter evaluates the policy before the tool body and emits policy
   decision audit events. Allowed executions emit a correlated outcome event.
   Denied executions never enter the body.
7. HITL gates carry `correlation_id` through approval and return `approval_id`.
8. `threadlight-safe-check --phase post-deploy` runs non-production canaries and
   emits `tests/tool-governance-probe-manifest.json`.
9. `tests/postdeploy-manifest.json` references the probe evidence.
10. `threadlight-production-ready` validates the evidence and emits
    `AGT-007`, `AGT-008`, and `AGT-103` into
    `tests/production-readiness-manifest.json`.

No stage may silently convert a missing artifact, unsupported adapter, failed
probe, or missing audit event into a success-shaped result.

## 8. Error handling and severity

| Condition | Lifecycle behavior | Readiness behavior |
|---|---|---|
| Block absent, `enabled` absent, or `enabled: false` | Skip governance checks; legacy output and exit behavior | New findings `not-applicable` |
| Enabled schema incomplete | Design phase gap; exit 1 with exact field/tool | `AGT-007` `must-fix` if assessment is run anyway |
| Canonical tool missing or extra | Design phase gap; no implicit allow | `AGT-007` `must-fix` |
| Referenced gate missing or duplicated | Design phase gap | `AGT-007` `must-fix` |
| Runtime or enforcement point unsupported | Pre-deploy gap; stop before deploy | `AGT-008` `must-fix` |
| Policy artifact missing or contract hash differs | Pre-deploy gap | `AGT-008` `must-fix` |
| Adapter declared but wire signal absent | Pre-deploy gap | `AGT-008` `must-fix` |
| Probe tooling cannot run | Post-deploy tooling error if the runner itself fails; otherwise a visible gap with evidence status | `AGT-103` severity by action class |
| Denied canary executes | Post-deploy gap | `AGT-103` severity by action class |
| Allowed canary executes zero or more than once | Post-deploy gap | `AGT-103` severity by action class |
| Audit event missing or not correlatable | Post-deploy gap | `must-fix` with any `irreversible-write`; `should-fix` otherwise |
| HITL approval ID missing | Post-deploy gap for gate-backed fixture/vector | same as missing audit evidence |

Existing safe-check exit codes remain:

- `0`: no gaps;
- `1`: validated lifecycle gap;
- `2`: prerequisite missing;
- `3`: tooling failure.

Unsupported enforcement is a validated gap, not a tooling crash. A parser or
runtime probe crash is a tooling failure and must not leave a prior evidence file
appearing fresh.

## 9. Validation and test strategy

### 9.1 Fixtures

The legacy example manifest at
`examples/returns-triage-governed/specs/manifest.json` remains unchanged and
continues to exercise the disabled/absent path. Its expected legacy behavior is
unchanged. If the mirrored `examples/returns-triage-governed/tests/safe_check.py`
must change to preserve the repository's existing byte-parity test, that is a
test-harness synchronization only; the legacy SPEC and manifest do not opt in.

Add a focused governed fixture at:

```text
skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/
├── specs/
│   ├── SPEC.md
│   ├── foundation.md
│   └── manifest.json
├── policies/tool-governance/
│   ├── adapter-manifest.json
│   └── generated/mcp-policy.json
└── tests/
    └── tool-governance-probe-manifest.json
```

The fixture has three canonical tools: one `allow`, one `deny`, and one
gate-backed `conditional`. It uses the current GHCP SDK + MCP-server route so the
default runtime is covered end to end. MAF mapping is covered by focused adapter
unit tests without requiring a live deployment.

### 9.2 Unit tests

Add focused tests for:

- optional block absent and `enabled: false`;
- schema enums, required keys, unknown-key rejection, and digest stability;
- exact canonical-name coverage, duplicate names, extra names, grouped headings,
  and a newly added unclassified SPEC tool;
- valid, missing, and duplicate section 8 gate references;
- common audit-field minimum and conditional/HITL additions;
- MAF agent, MAF workflow, GHCP MCP-server, GHCP gateway, unsupported GHCP
  in-process, and future .NET signal mapping;
- adapter-manifest runtime, hash, binding, policy-artifact, and wire-signal
  consistency;
- AGT catalog-to-pillar ID/title synchronization.

### 9.3 Contract probe tests

The governed fixture and adapter tests prove:

1. allow canary executes exactly once;
2. deny canary executes zero times;
3. both emit a policy-decision event with matching `correlation_id`;
4. allow emits one tool-outcome event;
5. all declared audit fields are present;
6. gate-backed conditional evidence contains `gate_id` and `approval_id`;
7. replaying one approval ID does not execute twice;
8. no vector performs a production mutation.

### 9.4 Regression coverage

The required regression is:

1. start from a governed SPEC and passing manifest;
2. add one canonical section 6 tool;
3. do not add it to `tool_governance.tools[]`;
4. assert `threadlight-safe-check --phase design` exits 1 and names that exact
   tool as unclassified.

This test prevents the most dangerous compatibility regression: an expanding
tool surface silently inheriting allow behavior.

## 10. Migration and backward compatibility

1. Existing projects do not migrate automatically.
2. Absence and `enabled: false` are permanent compatibility paths, not warnings.
3. Existing `specs/manifest.json` shapes remain valid.
4. Existing `tests/safe-check-*-manifest.json`,
   `tests/postdeploy-manifest.json`, and
   `tests/production-readiness-manifest.json` gain only optional fields or new
   findings that are `not-applicable` while disabled.
5. Existing SPEC section 6 grouped headings remain valid while disabled. A
   project must split them into canonical one-tool headings before opting in.
6. Existing HITL audit events remain readable. New correlation and approval
   fields are required only when a governed tool references the gate.
7. No generated project is considered governed merely because it already has
   `policy.yaml`, AGT CI checks, Citadel routing, or MCP supply-chain evidence.
8. Opt-in migration is deliberate: classify tools in SPEC, add gate IDs, generate
   the manifest block, run design safe-check, then deploy adapters and probes.

## 11. Rollout phases

All implementation phases belong to one plan and one pull request; each phase is
independently testable.

### Phase 1 - contract and compatibility

- Add SPEC/template fields and manifest schema.
- Add enabled/disabled design validation.
- Pin the legacy absent path.
- Add the governed fixture.

### Phase 2 - runtime adapter mapping

- Add MAF adapter selection through `foundry-agt`.
- Add GHCP MCP-server and gateway selection.
- Emit adapter manifest, policy artifacts, and inspectable wire signals.
- Fail unsupported runtime/boundary combinations.

### Phase 3 - behavioral evidence

- Generate the adapter-owned probe.
- Add pre-deploy and post-deploy safe-check validation.
- Extend HITL correlation and approval audit fields.
- Emit detailed probe evidence and postdeploy reference.

### Phase 4 - readiness and documentation consistency

- Add `AGT-007`, `AGT-008`, and `AGT-103`.
- Detect MAF, MCP-server, gateway, and future `.WithGovernance()` signal kinds.
- Consume probe evidence and apply the approved dynamic severity.
- Align AGT catalog, pillar tables, and the top-level skill summary.
- Add catalog/documentation drift regression tests.

The .NET adapter itself remains future work. Its signal kind and contract mapping
are reserved now so adding it does not change SPEC or manifest version 1.0.

## 12. Risks and trade-offs

### 12.1 Markdown parsing is intentionally constrained

SPEC remains human-readable, but exact coverage requires deterministic names.
The one-tool-per-heading and stable `GATE-NNN` rules reduce authoring freedom in
governed specs. This is preferable to fuzzy matching that could silently govern
the wrong tool.

### 12.2 Runtime-specific mechanisms remain runtime-specific

One contract does not mean one interceptor. MAF middleware, MCP-server policy,
gateway policy, and `.WithGovernance()` have different capabilities. The adapter
manifest makes that difference explicit and hash-binds it to common semantics.

### 12.3 No global baseline increases authoring effort

Requiring an explicit decision for every tool costs more than applying a default
allow/deny rule. It avoids importing a policy posture that is wrong for a
specific regulated, operational, or customer domain.

### 12.4 Canaries prove wiring, not every business rule

The allow/deny canaries prove execution suppression, exactly-once behavior, and
audit correlation without mutating production. They do not prove every
domain-specific policy condition. Unit policy vectors and business evals remain
necessary.

### 12.5 Audit evidence can expose sensitive data

The common schema intentionally excludes raw arguments/results. Teams may add
classified hashes or selected fields, but enabling governance must not become a
new data-exfiltration path.

### 12.6 Contract and adapter drift

Hash binding prevents stale generated policy from appearing current. The
trade-off is that any legitimate SPEC policy edit requires regeneration before
pre-deploy can pass.

### 12.7 Readiness severity is deliberately narrow

Missing audit evidence is `must-fix` only when the enabled contract contains an
`irreversible-write`; it is `should-fix` otherwise, exactly as approved. Safe
check still reports any missing enabled evidence as a deployment gap.

## 13. Acceptance criteria

1. No new Threadlight skill is created.
2. `tool_governance` is optional and only activates on `enabled: true`.
3. The unchanged legacy SPEC/manifest path passes with no new gaps or behavior.
4. An enabled contract fails design validation if any canonical tool is missing,
   duplicated, extra, grouped ambiguously, or unclassified.
5. Every contract entry contains exact tool name, action class, decision,
   intended enforcement point, policy ID, and required audit fields.
6. Every referenced HITL gate resolves to one stable SPEC section 8 gate ID.
7. `specs/manifest.json` is demonstrably derived from SPEC sections 6 and 8 and
   is not treated as an independent policy-authoring surface.
8. MAF uses a real `foundry-agt`-selected deterministic pre-tool integration
   where supported.
9. GHCP SDK governance is enforced at MCP server or gateway, with no in-process
   governance claim.
10. An unsupported runtime or enforcement point produces an explicit pre-deploy
    gap and no prompt-only fallback.
11. The adapter manifest and every policy artifact are bound to the current
    canonical contract digest.
12. Pre-deploy rejects every declared-but-unwired tool.
13. Post-deploy canaries are mock/adapter-owned and never mutate production.
14. The allow canary executes exactly once and the deny canary executes zero
    times.
15. Both decisions emit correlatable audit evidence; gate-backed conditional
    evidence includes correlation and approval IDs.
16. `threadlight-production-ready` consumes the probe manifest, recognizes MAF,
    MCP-server, gateway, and future `.WithGovernance()` signals, and does not
    pass on import or policy-file presence alone.
17. Missing post-deploy audit evidence is `must-fix` with any
    `irreversible-write` and `should-fix` otherwise.
18. The governed fixture covers allow, deny, and gate-backed conditional paths.
19. A regression test proves that adding a new unclassified canonical tool fails
    while governance is enabled.
20. `FINDING_CATALOG`, pillar 02 ID/title rows, and the production-ready skill
    summary no longer disagree about AGT findings; CI prevents future drift.
21. All failures are explicit: no silent fallback, no stale evidence reuse, and
    no success-shaped result after an incomplete contract or failed probe.
