# Local ACS enforcement through MAF Agent Hooks

Copy this **whole runtime package** into an application. It has no repository
paths. Install the exact runtime distributions from
`skills/_shared/governance-upstream-pin.json`; no execution dependency was changed
for this adapter. AGT core remains 5.0.0, ACS 0.3.1b0, Agent Hooks SDK 0.1.0a5,
MAF core 1.14.0 / Foundry 1.11.0 / hosting 1.0.0b260813, OPA 1.18.2.

## Host wiring

Construct `AcsGovernanceProvider` with these **host-owned** dependencies:

- `contract` and `contract_validator`: the shared governance contract validator.
- `bundle_path`, `expected_digest`, `bundle_verifier`: the Task6 bundle and
  `policy_bundle.verify_bundle`. Digest integrity is not a signature.
- `signature_verifier.verify(bundle) -> VerifiedPolicy`: an external verifier
  authenticating **both** the exact bundle digest and timezone-aware expiry.
  No signing key, fake signature, or healthy unsigned fallback is provided.
- `safe_provider(identity) -> dict`: bounded synchronous trusted evidence lookup,
  returning SAFE evidence/escalations. Identity includes action hash, policy hash,
  principal, agent and session. Never copy model arguments/results into this map.
- `principal`: the authenticated caller, never a model-supplied identity.
- `approval_resolver`: an implementation of the async `ApprovalService` protocol.
- `audit=DurableSpool(host_owned_directory)` when durable audit is required.

Use `create_governed_agent(provider, client=client, tools=local_tools,
middleware=application_middleware)`. This creates an actual pinned `Agent` with
exactly one official hooks bundle first. Duplicate/foreign bundles and middleware
placed before an explicitly supplied hooks bundle are rejected. A provider/bundle
cannot be reused by nested agents: construct a separate provider for each child.
Tools should be real MAF `FunctionTool`s; choose `result_parser=SKIP_PARSING` when
policies need structured native results rather than the default parsed text.

The host and application middleware are trusted. This cooperative boundary does
not prevent application code from calling a function directly, swapping the
agent/client, using unregistered external effects, or mutating middleware later.
Do not add per-run middleware ahead of the factory-installed boundary. Run-level
options cannot override `store=False`; chat middleware also sets it immediately
before the provider call. Clients must honor that supported provider option.

## Selection and failure semantics

- Unbound tools return native `ALLOW` without ACS evaluation. A consequential
  tool being present does not authorize labeling the entire agent “enforced.”
- Selected local tool `pre_tool_call` and `post_tool_call` bindings use the real
  `AgentControl.evaluate_intervention_point`. Native MAF writes transforms into
  arguments, messages, results and output, not just records.
- Preflight checks all of a selected tool's bindings before invocation, including
  post-only bindings. Missing/tampered/expired/unauthenticated policies leave the
  registered tool surface intact but unavailable. Other unbound tools continue.
- Selected lifecycle `input`, `pre_model_call`, `post_model_call`, `output`, and
  `startup` run through the native bundle. `shutdown` is evaluated but
  **observational**: native shutdown denies cannot roll back effects or egress.
  Constructor-only tool inventory is not fully projected at native startup;
  supply tools per-run if a startup policy needs `tools_registered`.
- Tool-specific non-tool seams are unsupported, unhealthy and denied at
  preflight; use explicit lifecycle bindings instead. Provider-hosted tool
  configurations are rejected rather than claimed intercepted. Gateway/GHCP
  bindings are not implemented by this local adapter.
- Engine faults/timeouts/malformed decisions become stable
  `threadlight:engine_failure` denies, without raw exception details. Actual
  native host failures may terminate the run (`tool_seam_host_error: terminate`);
  they are not relabeled as successful continuation.
- `health()` describes configuration and last evaluation, never deployment
  evidence or status `enforced`. Production/preproduction enforce; only the
  contract-permitted development/staging environments evaluate without enforcing.

Selected output uses native fail-closed buffering. Inner chat and agent stream
hooks bound serialized updates before accumulation; final transformed output
has the same byte cap. The default is 1 MiB, including serialization overhead.
Nothing is released on deny/overflow. This is **buffered** output, not incremental
segment mediation, and cannot limit memory already allocated by an external
transport for a single oversized chunk. Transforms replace the original before
release. A blocked post-tool result never enters the next model request.

## Approval and audit contracts

ACS escalation (or a required-approval allow) becomes a native liftable deny.
`BoundApprovalResolver` uses the **actual** Agent Hooks approval API. The service
must authenticate its response and return `ApprovalGrant` containing the exact
`ApprovalIntent`: action hash, policy hash, principal, agent/session identity,
native context identity, fresh nonce and expiry. Outage, rejection, mismatch,
expiry or replay cannot permit an effect. There is no model-boolean override.
The policy is reverified after waiting. Task8 owns persistent approval requests,
authentication, cross-worker nonce consumption and human workflows; none is
simulated here. A transform requiring separate approval is conservatively denied,
not approved for different arguments.

Required audit (including the shared aliases `audit` and `decision-receipt`) writes
a payload-free authorization receipt **before** an allowed effect or approval.
`DurableSpool` writes, flushes and fsyncs a file, publishes it, then fsyncs the
directory. Failure blocks the selected action. The receipt contains `audit_id`,
`correlation_id`, `decision`, `action_hash`, `policy_hash`, `delivery_status`, and
optional host-selected `agent_version`/`image_digest`; no prompt, arguments,
result, secret or raw exception text. It is an authorization receipt, **not proof
that the side effect completed**. This is not a tamper-proof cloud audit store.

`spool.retry(exporter)` works after process restart; exporter failure leaves
pending receipts on disk. Delivery is at-least-once: deduplicate by `audit_id`.
Approval or pre-effect durable-audit requirements need a pre-tool binding;
a post-only requirement cannot retroactively authorize an already executed tool.
Callbacks, signature authority, approval service and spool are trusted host
dependencies, not model tools.

## Corrected test-only CTK, unchanged execution bytes

The old published a5 CTK does not express MAF's permitted terminate-on-host-error
posture and AH-CTK-100 assumes the reference host's transcript layout.
[Upstream PR72](https://github.com/responsibleai/agent-hooks/pull/72) corrected
those oracles after the a5 wheel was published. The user approved a separate
testkit pin, not a runtime upgrade.

`skills/_shared/governance-ctk-pin.json` pins official source commit
`569b7c408091f65115755d2a0ef992efb88dc1e6` (contains correction
`4f7af786c2757e26711b141e69144b6a336f403b`), archive SHA256
`7a394ee2741ec164bc9436d869d36f9bd6ac5103661f4b3b425e1d9478ecbae1`.
The official MAF harness SHA256 is
`4cc30decc127e380dd18448ed6935ac4a8c9f12d2603c4b8369744ef5a7d7cbd`.

Only `agent_hooks.ctk` is loaded from that source. The corrected runner needs
a newer `_core.ctk_assert` oracle, so `scripts/ci/ctk-oracle` builds a **separate
test-only executable** against the official Rust CTK assertion implementation.
The tiny stdin/stdout bridge exposes only `assert_vector`; it never hosts,
dispatches or enforces an agent. Its Cargo lock, build image digest and binary
hash are recorded. The runner's local assertion reference goes to this process:
`agent_hooks._core`, emitter, codecs and all installed runtime APIs remain the
published wheel bytes. No source-built Python SDK is installed, no wheel is
edited, no new execution API is borrowed. The old auto-loaded CTK pytest plugin
is disabled so it cannot import the superseded oracle before this explicit test
fixture activates the corrected testkit.

The official harness drives **our production bundle and agent factory**.
Scripted CTK interceptors/resolvers remain official test controls; they are not
misrepresented as ACS policies. Separate provider tests evaluate actual ACS/OPA.
The harness's official final-message presenter runs inside the output boundary,
before evaluation; no observations or verdict records are rewritten afterward.
All 47 declared vectors run and pass. This later official source also includes
four optional incremental-output vectors (110–113); that capability is not
declared, and their explicit skips/reasons are recorded separately. They are not
counted as passing controls. No declared vector is skipped.

Run `python3 scripts/ci/run-governance-pin-tests.py`. It builds the isolated CTK
oracle, verifies published runtime wheel provenance, installed bytes **before
and after tests**, `pip check`, OPA version/hash, then runs native bundle,
provider and CTK tests in Linux amd64 Docker with read-only source and a dedicated
writable `.governance-validation` mount. A zero-skip JUnit gate is mandatory.
Ordinary base tests stay available without native dependencies.

Evidence lives in `.governance-validation/runtime-proof.json`, wheel provenance,
JUnit and `ctk/{source-provenance,build-provenance,undeclared-capabilities}.json`.
CI uploads these, not the venv or model payloads. No live Azure call, selected
tenant change, Foundry deployment, production attestation or remote PR is part
of this local proof.
