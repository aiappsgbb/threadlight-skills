# Governance probe producer contract (Task 11a)

This is an **explicitly installed staging/preproduction noop fixture**, not a
business connector, live conformance collector, or proof of deployment. There is
no mutation other than recording acceptance of one noop in Cosmos. No probe
changes a policy verdict. Production-environment registries reject probe actions.

## Opt in

1. Operator installs this service with its **own UAMI** and Entra audience.
   Build the existing gateway Dockerfile, then build this Dockerfile from the
   repository root with `--build-arg GATEWAY_IMAGE=<immutable-gateway-image@sha256:...>`.
   The package entry point is `threadlight-govern-probe-fixture`.
2. Copy `config.template.json` into a protected deployment configuration. Replace
   example identities/URLs/digests, explicitly set `enabled: true`, and mount the
   configuration and signed bundle read-only under `/config` or `/mnt`.
   Set `PROBE_CONFIG_FILE` to that configuration. Missing/disabled configuration
   fails startup; there is no anonymous diagnostic or insecure local-auth mode.
3. Register **only** the action below in an operator-published Task 6 signed
   registry. The input/output schemas must equal the constants `PROBE_INPUT` and
   `PROBE_OUTPUT` in `govern_control_plane.probes` (also shown below). Configure
   both fixed HTTPS endpoints in the runtime endpoint allowlist.
4. Gateway: configure `probe_enabled: true`, `probe_container: "probe-gateway"`,
   and `probe_controllers`. Normal configuration defaults to disabled and exposes
   neither probe control methods in MCP nor a control/status API.
5. Native MAF: explicitly select `governance_probe_noop` with a
   `pre_tool_call`, `local-agent-hooks` binding and enforce mode. The generated
   host installs the fixed async implementation only when
   `probe_observability = {"enabled":true,"configuration_file":"/mnt/governance-probe/config.json"}`.
   An application tool with that reserved name is rejected, never replaced.
   Generation also requires explicit `subscription` and `resource_group`.

There is deliberately **no fixture deployment in normal generated Azure YAML**.
For an explicitly approved `public-authenticated-proof` staging/preproduction
run, deploy the fixture using the generated `TL_GOV_SERVICE_INGRESS` HTTPS
settings. Its protected endpoints still require the existing Entra caller/
controller allowlists; do not add anonymous operations, secrets or fixture-store
access for the calling agent. This public mode proves no network isolation.
Use only the dedicated proof resources and clean them up after verification.

Task 10's Bicep emits separate `probe-gateway`, `probe-native`, `probe-fixture`
containers and the fixture UAMI only with the explicit staging opt-in. It grants
each producer write access only to its own container, plus account metadata read.
The fixture identity has signing-key **verify**, not sign, and no approval-store
permission. The agent has no fixture-count write permission. Provisioning,
ingress/private networking, read-only mounts, assigning the separate downstream
UAMI to the native host, and installing the fixture remain operator/Task 15
work. No resources are deployed by this package.

## Registry and policy

Existing registries remain valid; omitted `probe_safe` is strictly `false`.
Integers, strings and null are not booleans. `probe_contract` is forbidden unless
the flag is true. A true action must have this dedicated shape:

```json
{
  "name": "governance_probe_noop",
  "policy_binding": "safe",
  "post_policy_binding": null,
  "workloads": ["22222222-2222-2222-2222-222222222222"],
  "scope": "governance-probe",
  "approval_roles": [],
  "endpoint": "https://fixture.example/governance/noop",
  "outcome_endpoint": "https://fixture.example/governance/outcomes",
  "credential_scope": "api://fixture/.default",
  "probe_safe": true,
  "probe_contract": {
    "protocol": "threadlight-probe/v1",
    "fixture_id": "staging-noop",
    "effect": "noop"
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "probe_run_id": {"type":"string","minLength":36,"maxLength":36},
      "variant": {"type":"string","enum":["allow","deny"]}
    },
    "required":["probe_run_id","variant"],
    "additionalProperties":false
  },
  "output_schema": {
    "type":"object",
    "properties":{"status":{"type":"string","enum":["noop"]}},
    "required":["status"],
    "additionalProperties":false
  }
}
```

No business identifiers, URLs, arbitrary schemas, approval flags, output gates,
or business-write target can be inserted into this contract. `probe_run_id` is
also validated as a canonical UUID by the host before ACS evaluation.

The staging policy can use the bounded variant at the **real ACS policy target**:

```rego
package probe
import rego.v1

pre_tool_call := {"decision": "allow"} if {
  input.policy_target.value.variant == "allow"
} else := {"decision": "deny"}
```

Both variants target the **same action, binding, dispatcher, and fixture**. An
allow is not synthesized by telemetry; the actual native ACS/OPA decision must
allow the tool. Transforms/escalations are unsupported for this probe contract
and remain nonterminal UNKNOWN, without effects.

### Native deployment association without an image-hash cycle

The normal native runtime policy stays embedded and independently signed.
**After building the agent image**, publish a separate signed registry bundle
(a different immutable policy ID/version, for example `safe-probe/1`).
Its optional registry-level `native_policy_digest` binds the exact embedded
native runtime policy digest; its deployment binds the built agent image/version.
Only probe actions are permitted in such an association registry. Task 6 covers
this registry in the bundle digest; Key Vault envelope verification is unchanged.

For native `bind`, supply `probe_runtime_configuration`, `probe_bundle` and
`probe_signed_envelope`. Binding checks the signed declarations, native policy
association, tenant, image/version, subscription/RG, controller scope, identities,
endpoints and actual selected binding. It emits `bindings.native_probe_config`
and `probe_observability.status = "declared-unverified"`; it does **not** install
the fixture or mark anything ENFORCED. Mount the emitted configuration, envelope
and registry bundle separately, without modifying the built image.

The native configuration uses `producer: "native"`, `cosmos_container:
"probe-native"`, an empty `fixture_callers`, and a distinct
`downstream_client_id`. At startup the host independently checks its authenticated
principal, native version/image metadata and signed policy association.

## Authentication and APIs

All tokens are RS256 Entra tokens, checked against configured tenant authority,
issuer, audience, lifetime and client/principal allowlists. There are no API keys,
unsigned tokens, delegated probe controllers, model-accessible counter writes,
or arbitrary-URL tools.

`probe_controllers` maps controller object ID to
`{"client_id": "<app-id>", "subjects": ["<requesting-workload>"],
"actions": ["governance_probe_noop"]}`. Controller principals must be distinct
from normal tool callers. Assign `Governance.Probe.Control` for registration and
read, or `Governance.Probe.Read` for read-only access, in each API audience.
Configure this allowlist separately in gateway/native, fixture and Task 8.
Normal tool callers still require the unchanged `Governance.Workload` contract.

| Endpoint | Authority / semantics |
|---|---|
| `POST /governance/probes/{UUID}` | Controller only; single-use registration, 201 |
| `GET /governance/probes/{UUID}?subject={UUID}` | Scoped controller/reader; authoritative point read |
| `POST /governance/noop` | Fixture only; authenticated downstream UAMI allowlist |
| `GET /governance/outcomes` | Fixture only; exact authenticated Task 9 request linkage |
| Task 8 `GET /receipts/{receipt_id}` | Existing route; a scoped probe reader can read only its probe receipts |

Register the **same nonce and expected facts separately** at the gateway/native
producer and the fixture **before** asking the agent to invoke the tool:

```json
{
  "subject":"22222222-2222-2222-2222-222222222222",
  "action":"governance_probe_noop",
  "variant":"deny",
  "deployment":{
    "agent_id":"probe-agent","agent_version":"1",
    "image_digest":"sha256:<64 lowercase hex characters>",
    "environment":"preproduction",
    "subscription":"11111111-1111-1111-1111-111111111111",
    "resource_group":"probe-staging"
  },
  "policy_digest":"sha256:<exact runtime policy digest>"
}
```

This body is bounded, rejects unknown fields, and accepts **no counters, events,
timestamps or receipt IDs**. Re-registration is 409, not reset. Registrations
expire for invocation after ten minutes and are never deleted/reopened by TTL.
Unknown state is 404. Dependency/storage failure is 503 `UNKNOWN`, never zero.
Cross-workload/action reads are forbidden even for otherwise valid readers.

Task 9 headers include the tenant, requester/client, action, exact policy and
deployment hash, action hash, idempotency key and pre-action receipt provenance.
For probes they also include `X-Probe-Run-ID`. Outcome fetch uses those same
headers and no body. The fixture recomputes the canonical action hash and checks
the registered nonce/scope; duplicate identical requests return one outcome and
one effect. The effect itself is the atomic Cosmos CAS committing the noop,
not a second untracked operation after a counter increment.

## Observation schema and interpretation

`State`, `Event`, `Registration` and `ProbeContext` in
`govern_control_plane.probes` / `models` are the strict wire schema. States carry
tenant, requesting subject, action/binding, fixture ID, run UUID, policy digest,
full deployment, registration/expiry timestamps, native session/call hashes,
events and counters. No business input, output, messages or exception text is
stored. Counts are strict nonnegative integers, bounded to one for this single-use
protocol; booleans and inconsistent event/count records are rejected.

`deployment_provenance: "signed-registry-declaration"` distinguishes declared
deployment facts from `observation_provenance: "service-boundary"` events.
This does not independently attest Azure deployment metadata or a hostile host.

Native interception also commits its actual hook-context `action_hash` into the
producer state before releasing the denial or tool body. The collector compares
that hash with the authenticated receipt; it is not the different canonical
Task 9 facts/arguments hash that the fixture records for its HTTP effect.

| Phase | Actual writer / boundary |
|---|---|
| `received` | Authenticated gateway call or actual native pre-tool interception |
| `intercepted` | Real gateway ACS verdict + durable Task 8 receipt; native typed `record_sink` + receipt |
| `dispatch` | One-use authorized HTTP transport, after credential acquisition, before handing off the wire request |
| `effect` | Fixture atomic durable noop acceptance, in its separate writer scope |
| `completed` | Awaited dispatch/output completion, or actual terminal pre-tool denial |

The native sink records only the emitted typed record for the selected
session/sequence/binding. Its async durable flush runs before the fixed tool
dispatch or before the native run/stream releases a denial. No observer tool,
model JSON, generic after-return guess, or unrelated/nested agent emission can
create proof. The fixed tool awaits the actual downstream request.

Registration with zeros and `terminal: null` is **not** success. An unreached tool
has no interception. A failed/late invocation remains nonterminal, even if its
dispatcher has begun. The collector must wait for `terminal: "denied"`
with a deny interception/receipt (zero dispatcher and fixture effects), or
`terminal: "completed"` for a separate fresh allow nonce (one dispatch and one
fixture effect). It must compare both independent producer states and the exact
receipt context. Gateway MCP returns the server's receipt ID in structured
content and `_meta["threadlight.probe.receipt_id"]`; native status supplies the
same direct receipt association, never a model-transcript receipt.

Cosmos uses the existing conditional-create/ETag protocol, `/scope` partitions,
single write region, no TTL, **Strong** consistency and authoritative point
reads. Conflicts are bounded/retried only for the idempotent noop CAS. Lost ACKs,
timeouts and unavailable/corrupt stores cannot create a successful zero report.
Approval and normal idempotency keys/containers are untouched.

## Local validation (no Azure)

The existing `scripts/ci/run-governance-pin-tests.py --deployment` runner includes
the producer tests and rejects skipped/missing required cases. It checks every
installed AGT/ACS/Hooks/MAF execution file against the published pinned wheels
before and after execution, builds portable packages, and compiles Bicep locally.
The tests use actual FastMCP, ResponsesHostServer, Foundry SDK SSE, native hooks,
ACS/OPA, Task 8 ASGI and this fixture's ASGI app. Only external signing/JWKS/model
HTTP and Cosmos SDK storage are local seams. This is **local producer validation,
not live Azure proof**. The [Task 11 collector](../governance-probe.md) runs these
APIs against independently observed deployments; Task 15 installation remains
separate.
