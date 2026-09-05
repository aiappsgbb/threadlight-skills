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
- `principal`, `tenant`: the authenticated requesting identity and its tenant,
  supplied by trusted host authentication, never model arguments.
- `allowed_approval_roles`: nonempty host-selected role identifiers when approval
  is selected; no customer role is hardcoded or inferred from model output.
- `approval_resolver`: an implementation of the async `ApprovalService` protocol.
- `audit=DurableSpool(host_owned_directory)` when durable audit is required.

Use `create_governed_agent(provider, client=client, tools=local_tools,
middleware=application_middleware)`. This creates an actual pinned `Agent` with
exactly one official hooks bundle outside all application middleware. A host-only
execution scope surrounds that bundle for failure bookkeeping and final release.
Duplicate/foreign bundles, detached native hook members, and middleware placed
before an explicitly supplied hooks bundle are rejected. Preconfigured client
agent/chat/function middleware is rejected **before any bundle or application
effect**; supply application middleware through this constructor instead. Extra
per-run/option bundles are rejected before native middleware execution, including
otherwise-shadowed `client_kwargs` entries. A provider/bundle
cannot be reused by nested agents: construct a separate provider for each child.
Tools should be real MAF `FunctionTool`s; choose `result_parser=SKIP_PARSING` when
policies need structured native results rather than the default parsed text.

The host and application middleware are trusted. This cooperative boundary does
not prevent application code from calling a function directly, swapping the
agent/client, using unregistered external effects, or mutating middleware later.
Do not replace the installed middleware or client. Run-level
options cannot override `store=False`; chat middleware also sets it immediately
before the provider call, including native `extra_body` overrides. Clients must
honor that supported provider option. The last chat boundary checks the effective
tool list after constructor defaults, call-time options, progressive tool exposure,
and middleware merges; native option/`client_kwargs`/`extra_body` tool overrides
and Chat Completions `web_search_options` cannot hide provider-hosted execution.
Unbound local functions remain permitted. This assumes the pinned client pipeline.
The model-dispatch guard runs at `BaseChatClient._inner_get_response`, after native
preparation. Native OpenAI clients additionally use a copied SDK/HTTP
client with guarded default **and mounted** `AsyncBaseTransport`s: authorization is
checked after awaited HTTP request hooks/authentication and on each retry/redirect,
immediately before transport dispatch. Original clients, hooks and connection pools
are not mutated; the caller owns pool shutdown. There is no public SDK getter for
the HTTP client/mounts, so this pin-specific adapter reads those private references
on host-owned copies; it does not modify installed SDK code or published pins.
Selecting lifecycle `startup`, `input`, or `pre_model_call` requires a supported
native model transport: the pinned `OpenAIChatClient`, `OpenAIChatCompletionClient`,
or `FoundryChatClient`, backed by `AsyncOpenAI` (including `AsyncAzureOpenAI`) and
an `httpx.AsyncClient`. Other client classes/subclasses are **rejected before
application middleware/effects**, even if they expose an OpenAI SDK instance or
self-assert a guard capability. Capability is checked at construction and run
entry; unsupported bindings report `threadlight:unsupported_model_transport`.
There is no custom terminal-adapter protocol or blanket custom-client coverage.
Tool-only bindings do not impose unrelated model-egress requirements; other
lifecycle selections retain only their own documented tool/output boundaries.
The pinned Foundry project client obtains its OpenAI client normally; this adapter
copies and guards that HTTP path without changing the project or its credentials.

### Selected pre-model scope: invariant targeting only

In enforce mode, `pre_model_call` binds the **native hook's message projection**
to its successful ACS decision / exact approval emission. Immediately before
provider preparation, the adapter compares the actual messages with that authorized
target using MAF's owning codec, not provider wire bytes. ACS message transforms
are compared with their authorized, write-back-canonical target, not the original.
The options and local tool definitions present when that hook begins must also
remain invariant through application middleware. Those settings are host-owned
configuration constraints: the pinned hook exposes messages (and model identity),
**not a policy evaluation of every provider option or tool inventory**.

Any later unauthorized message/configuration change fails closed with
`threadlight:model_target_changed`; a configured spool receives a payload-free
denial linked to the original authorization, and no model dispatch occurs.
There is no automatic reevaluation/reapproval loop. The copied HTTP client snapshots
the pinned provider's generated JSON before request hooks/authentication and checks
both cached content and the actual replayable `httpx.ByteStream` at final transport,
including retries. Other request body producers are unsupported in this scope.
JSON whitespace/key ordering is
irrelevant; native role/content/instruction serialization happens before that
snapshot and is not mistaken for a hook-target mutation.

**All post-policy native compaction is unsupported for selected pre-model enforce
bindings**, even a no-op strategy: client, agent, per-run and middleware-supplied
strategies fail with `threadlight:unsupported_model_compaction`, before the strategy
can summarize, truncate or dispatch. This is not a blacklist of named strategies.
Constructor/run configuration is checked before application effects; late injected
configuration is rejected at the chat/preparation boundary with a denial receipt.
Native token-only annotation remains supported. Custom message preparers and
`input`/`messages` option-body replacements are likewise rejected
(`threadlight:unsupported_model_preparer` / `threadlight:unsupported_model_override`).
Do any desired compaction **before** entering this governed model scope, so the
actual compacted messages receive their own policy decision and approval.
Tool-only bindings do not impose these model-scope restrictions.

Startup/input decisions authorize their own native seam projections; their later
lifetime checks do **not** mean the full eventual model request was evaluated there.
Instructions, history and later authorized input/pre-model transforms can legitimately
change the request. Select `pre_model_call` to authorize the final message scope;
this adapter does not claim every earlier lifecycle target remains identical.

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
- Selected local callables are copied and wrapped before SDK invocation: synchronous,
  asynchronous, and awaitable-returning failures become typed
  `GovernedToolUnavailable("threadlight:tool_unavailable")` errors without retaining
  the private exception as context. Native post-tool error evaluation/audit still
  runs, and the invocation remains an error, not a successful substitute. Unbound
  callables and the original caller-owned tool objects are unchanged. Framework
  termination and human-input control flow remain native.
- Selected tools also guard the **whole pre-hook** native argument pipeline:
  a host-owned input-model subclass retains field types, constraints, validators
  and schema, delegates validation once to the original model (also JSON/string
  variants), and returns a guarded serialization holder. This also covers root/wrap
  validators returning a different model or arbitrary object. Serialization lookup,
  field/model serializers, and the pinned SDK's actual downstream lightweight
  schema checker run inside the private boundary. Only a successfully checked plain
  argument dictionary reaches the native schema check outside hooks. Schema-only
  tools take this same path through a dictionary root model; unbound tools remain
  untouched. This reuses the actual native schema/enum/type rules, not a replacement
  validator or an additional validation package. User validators/serializers are
  not repeated on the unchanged execution path. Failures become
  `TypeError("threadlight:invalid_arguments")` without exception context or Pydantic
  input fields. MAF returns a failed argument-parsing result, including with detailed
  errors enabled, not a successful replacement. The shared input-model class is
  untouched; parameter caches are copied before native provider projection.
  This failure precedes native pre/post-tool emissions, so no synthetic hook
  record or successful authorization receipt is invented for it.
- Native automatic tool dispatch normalizes original arguments **once**, before
  `pre_tool_call`. A separate schema-only execution copy avoids repeating validators,
  serializers or `model_post_init` on unchanged canonical arguments. Each native
  emission retains both original and authorized argument hashes. An ACS transform
  that changes the canonical arguments must pass the **original Pydantic model**
  before dispatch, and its validated/dumped value must have the exact authorized
  hash. Invalid constraints, coercion, or non-idempotent normalization that changes
  the transformed target fail closed with `threadlight:invalid_transform` and a
  bounded denial receipt when a spool is configured. A constraint-preserving
  transform writes back normally; an identical-to-canonical target is not revalidated.
  No differently normalized value is silently executed or automatically reapproved.
  Later application-middleware or invoke-time argument changes fail closed with
  `threadlight:arguments_changed`; no revalidation/approval loop is attempted.
  Dispatch tickets are single-use, including middleware that calls its continuation
  twice. Bound `self`, injected native invocation contexts, result parsing and
  invocation budgets retain their native semantics.
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
`BoundApprovalResolver` uses the **actual** Agent Hooks approval API. The service must return an `ApprovalGrant` containing the exact `ApprovalIntent`:
action hash, policy digest **and signed expiry**, requesting principal/tenant,
agent/session identity, native context identity, fresh nonce, allowed roles and
grant expiry. The grant also carries authenticated approving identity, approving
tenant, approving role and a provenance receipt reference.

`ApprovalService.verify(grant, *, intent) -> bool` is a **required trusted host
contract**, not a boolean from the grant/model. It must authenticate the entire
service receipt, authenticate the requesting and approving identities/tenant,
verify the approver's role authority for this exact action, and atomically consume
the nonce. An approver name, role string, provenance identifier or echoed intent
alone proves nothing. The local adapter additionally requires exact intent equality,
same-tenant approval, a nonblank approver/provenance and membership in the selected
allowed roles. A differently named approver with an unverified receipt is rejected.
There is no default role, self-asserted `identity_verified` flag or approval fallback.

Host usage: pass `principal=authenticated_subject`, `tenant=authenticated_tenant`,
`allowed_approval_roles=selected_role_ids`, and an authenticated service client
implementing **both** `resolve` and `verify`. Do not expose either service method as
an agent tool. Copy `approval.schema.json` with this package: it specifies the wire
grant/intent and `definitions.hostIdentity` configuration. Shape validation is
separate from authentication and exact-scope validation.

Outage, rejection, mismatch, expiry or replay cannot permit an effect. The adapter
reverifies policy authorization after ACS evaluation, resolver/verification waits
and synchronous audit, then immediately before the actual selected callable
(including after application middleware awaits and thread scheduling). Each provider
pins the first authenticated digest/expiry: renewal requires a new provider.
UTC high-water time plus a monotonic deadline prevent clock rollback or a slow
engine/approver from extending authorization. Approval deadlines are capped by the
policy expiry and timeout; per-run, single-use tickets bind the approved call and
arguments through final dispatch, including streams and nested agents.
Selected startup/input authorizations also remain fresh through later awaits up
to model/tool dispatch, including otherwise tool-unbound functions. Lifecycle
approval deadlines are checked as well as signed policy expiry. Selected output
is rechecked after the native output/shutdown awaits, at actual caller release;
an outer stream wrapper performs this check **after** native buffered output
gating, not through an inner transform hook that runs before the verdict.

Task8 supplies the real Entra/service authentication, persistent approvals,
cross-worker nonce consumption and human workflows; local tests use explicitly
trusted service doubles, not live identity proof. A transform requiring separate
approval is conservatively denied, not approved for different arguments.

Required audit (including the shared aliases `audit` and `decision-receipt`) writes
a payload-free authorization receipt **before** an allowed effect or approval.
`DurableSpool` writes, flushes and fsyncs a file, publishes it, then fsyncs the
directory. Failure blocks the selected action. The receipt contains `audit_id`,
`correlation_id`, `decision`, `action_hash`, `policy_hash`, `delivery_status`, and
optional host-selected `agent_version`/`image_digest`; no prompt, arguments,
result, secret or raw exception text. It is an authorization receipt, **not proof
that the side effect completed**. This is not a tamper-proof cloud audit store.

The production bundle also installs a native `record_sink`. It consumes typed
`InterceptionRecord` fields, never `to_wire()`/SDK payloads or verdict messages,
and appends missing deny/failure receipts with bounded `reason_code` and
`interception_point` fields. Native faults use `threadlight:native_failure`;
ACS faults use `threadlight:engine_failure`. A post-tool error is recorded as
`decision: error`, not a successful result even if its policy permits continuation.
Already-receipted emissions are not duplicated. A later final-boundary denial is
a separate receipt from an earlier pre-action authorization. Sink failures mark
the affected bindings unhealthy and latch required-audit failure within the run,
because the native emitter itself intentionally swallows sink exceptions. This
adapter does not mistake native sink delivery for durable authorization.

`spool.retry(exporter)` works after process restart; exporter failure leaves
pending receipts on disk. Delivery is at-least-once: deduplicate by `audit_id`.
Approval or pre-effect durable-audit requirements at `post_tool_call` need covering
`pre_tool_call` bindings **with the same required controls**, not just an unrelated
pre-tool binding. A tool-specific requirement may be covered by its own pre-tool
binding or a global lifecycle pre-tool binding. A lifecycle post-tool requirement
needs lifecycle pre-tool coverage because it applies to every local tool. Missing
coverage is unhealthy and blocks affected tools at native preflight, before any
effect (including otherwise tool-unbound functions under that lifecycle selection).
A post-only requirement cannot retroactively authorize an already executed tool.
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
