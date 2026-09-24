# Citadel action governance: local execution profile

This is the opt-in `threadlight-citadel-binding/v1` profile, not a replacement
Citadel hub, a deployment receipt, or a certification. **W2-I remains a separate
authorized live gate.** Local policy, protocol and container checks are not
evidence that an APIM route, Entra role assignment or business write exists.

The [September 24 execution record](governed-returns-validation.md#s7-citadel-public-route-and-isolated-producer-ack-loss)
separates the observed public MCP/REST route from the distinct live job-only
producer ACK-loss test. It preserves the still-open genuine requesting-user gate;
neither local tests nor that dated record certify a new deployment.

## ADR: position of authority and ownership

The first vertical is one tenant, one producer, one consumer, one registered
operation and one exact deployment/image/version binding:

```text
consumer -- original workload JWT --> Citadel APIM
         -- proxy MI + original JWT --> ACA execution PEP
         -- local native producer ACS + consumer ACS --> decision
         -- all required authorities + central audit ACK --> registered HTTP producer
```

APIM never receives a reusable `allow=true` ticket to execute later. Native
ACS/Rego is the PDP; the gateway dispatcher is the execution PEP. The producer
independently authorizes its own fixed operation and deduplicates effects. SAFE
is a method, AGT the toolkit, and Agent Hooks the interceptor contract.

Threadlight owns `references/gateway/citadel.py`, `citadel_package.py`, tests,
the portable service image and this runbook. The Citadel maintainer owns hub
infrastructure, fragments and approval of the overlay. Application owners own
producer authorization, action schema, trusted business facts and reconciliation.
No upstream Bicep is forked or vendored. Public upstream evidence is frozen at
`Azure-Samples/ai-hub-gateway-solution-accelerator`,
`23fbc8fe7f2f068de3ed3c80da2344760faac1e6` on `citadel-v1`. The installed
`citadel-hub-deploy` skill's older pin remains historical and unchanged.

### Contracts and canonical joins

`Producer`, `Consumer` and `Binding` are strict versioned wire models reusing
the control plane's canonical serializer, GUID/digest/key types and gateway
`Action`, bounded JSON schemas and `Deployment`. Unknown or missing fields,
cross-tenant references, different targets/schemas, extra workloads or selected
unsupported protocols are errors, not disabled governance.

The producer contract contains its independent policy selector, action and
`effect_protocol: registered-http-v1`. The consumer references the exact producer
ID/version and its own policy; principal/client/audience are explicit. The
effective generation signs both contract digests and both policy digests, and
preserves subscription, resource group, environment, agent version and image.
These values are included in request facts, action hashes, user-confirmation
and reviewer intents, terminal wire checks and audit's effective policy digest.
`X-Citadel-Binding`, `X-Citadel-Producer-Policy` and `X-Citadel-Consumer-Policy`
are protected outbound provenance, never incoming identity.

Each independent native engine receives a deep copy of the **same canonical
action and trusted facts**. Deny overrides. Transformations and post-output
policies are explicitly unsupported in this first profile. Escalation without
an authority declared by that owner fails closed. Confirmation, independent
review and signed business evidence remain distinct AND obligations. When an
owner escalates, the effective requirements can conservatively require the
other owner's selected conditional authority too; this never removes one.

Two independent reviews cannot be expressed by the current one-reviewer
authority protocol: selecting reviewer roles on both sides is rejected, even
if their role names match. A role list means one reviewer may hold any allowed
role, not two required reviewers. Conflicting confirmation/evidence profiles are
also rejected. One side's review plus the other's confirmation is representable
and retains Wave1 one-use/separation rules. Inline human waits are unsupported.

### Trusted facts and producer effects

`host-identity-v1` provides only authenticated identity, scope and deployment.
It does not invent order eligibility, evidence authenticity, balances or business
revisions. Signed evidence uses the existing selected provider protocol. A
business requiring additional mutable facts needs a reviewed adapter and
revision control at the producer; do not encode model booleans as host facts.

`registered-http-v1` is the existing fixed HTTPS POST operation / GET outcome
protocol. The producer receives a **distinct** downstream MI credential, never
the consumer or APIM bearer. It must independently validate issuer, tenant,
audience, allowed PEP principal/client, action/schema and generation provenance,
enforce business permissions and atomically bind the idempotency key to action
hash and effect. The response is exactly `{receipt_id, result}`. The GET
reconciliation route must return the same immutable outcome for that key.
The existing returns producer writes a decision/audit, not payment settlement.
Its historical receipts are not Citadel evidence.
The [executable synthetic example](../examples/citadel-governance/README.md)
builds all three bundles and reuses that producer with `citadel_binding`;
the backend independently reconstructs the same effective request facts.

## Identity, endpoints and protocols

The original caller is initially **Entra v2 app-only** with `Governance.Workload`,
exact issuer/tenant/audience and principal/client allowlist, no delegated `scp`.
The original user, when needed, remains the Wave1 authenticated context registered
by the application; a workload token is never a human identity.

The consumer sends its ordinary Authorization header to public APIM. The overlay
rejects caller-supplied `x-threadlight-consumer-authorization` and spoofed
attribution/provenance headers, captures the original token, keeps Citadel
`security-handler` and `mcp-usage`, validates the caller JWT, then authenticates
APIM's selected MI to the **different** PEP audience. The PEP independently
verifies both JWTs using the existing `EntraAuth`; neither a subscription key,
product name nor APIM's MI identifies the original consumer. Both token expiries
bound final effects after credential and transport waits.
The generated product sets the pinned fragment's `jwtAudience`, `jwtIssuer`,
`jwtOpenIdConfigUrl` and `requiredRoles` overrides, rather than accidentally
requiring the hub's unrelated default audience.

The signed `public_url` is the APIM route; `gateway_url` is the exact internal
ACA HTTPS route. The overlay sets internal Host/Origin and preserves the business
body, reserved metadata and Idempotency-Key. A browser Origin, if sent, must
equal the signed public origin before rewriting. ACA still verifies exact
internal Host/Origin; direct callers without both admitted identities fail.
No discovery-driven OAuth flow is advertised: provision the explicit consumer
audience/client and token out of band; this is not OBO, arbitrary OAuth discovery
or an EasyAuth-header trust profile.

| Path | Initial support |
|---|---|
| `mcp-existing` | Remote FastMCP Streamable HTTP initialize/list/call, protocol `2025-06-18`; APIM uses the path as-is, **no appended `/mcp`** |
| REST `/operations/{registered-name}` | Same authenticated dispatcher, `POST {"arguments": {...}, "_meta": {...}}`; requires original operation Idempotency-Key |
| Producer transport | Registered HTTP JSON operation/outcome only; no arbitrary URL, shell or MCP executor |
| MCP streams | Current PEP returns finite JSON results; overlay does not buffer SSE or inspect response bodies; no long-lived human-wait stream |
| Resources, prompts, A2A, MCP workspaces | Unsupported, not inherited from a producer |
| API-to-MCP (`mcp-from-api`) | Not enabled by this overlay; requires a separate verified source-API auth/key-forwarding contract and direct-source bypass test |

The MCP result has existing explicit `completed`, `blocked`, `unavailable`,
`pending_approval` or `pending_confirmation` state. REST maps those to
200/403/503/202, with unknown outcomes and conflicting operation reuse as 409.
Pending returns promptly with the exact original operation reference; no HTTP
request remains open awaiting a person. Resume that operation, not a new one.
An ambiguous send or lost ACK never automatically retries an effect. Completed
outcomes do not consume user/reviewer authority again; expiry of that authority
does not undo a durable effect. Fresh policy/authentication remain required for
retrieval. Reconciliation requires producer evidence, not a locally assumed success.

## Build, activate and integrate

Use the native-pinned Linux amd64 environment from
`scripts/ci/run-governance-pin-tests.py`; ordinary native skips do not count.
The repository example and tests use synthetic identifiers, never deployment
credentials or existing S2/S3 resources.

1. Build producer and consumer bundles independently with the existing
   `build_bundle`. Each includes its owner's `gateway-registry.json` and native
   `manifest.yaml`/Rego. Policy bindings/action shape and the full deployment
   must match; only supported obligations can differ.
2. Populate a `Binding` with their exact metadata policy IDs, versions, content
   digests and versioned Key Vault key IDs. Run `threadlight-citadel-package`
   with `--binding`, `--producer`, `--consumer`, `--destination`, `--policy-id`,
   `--version`, `--asset-id`. It verifies both owner inventories and builds a
   third immutable generation; stdout contains unsigned digest and policy XML.
   It performs no Azure operations and signs nothing.
   Exported policy XML uses APIM **`rawxml`** expression syntax for the upstream
   `policyXml` hook. The Python `policies()` helper returns ordinary encoded XML:
   use APIM `format: xml` for that representation, or `raw_policy_xml()` for a
   rawxml hook. Passing encoded C# quotes/operators to rawxml can fail compilation;
   do not fix that by deleting authentication or request-binding expressions.
3. Have the separate authorized publisher publish **all three** envelopes
   through the existing Blob/Key Vault control-plane mechanism. Runtime readers
   have verify/read permissions, not publishing/signing authority. Mount all
   three immutable directories read-only. Supply ordinary `Configuration` with
   `bundle_path`, `policy_id`, `policy_version`, `policy_digest` selecting the
   generation, plus `citadel: {producer_path, consumer_path}`. Null, partial or
   one-sided Citadel selection fails. The initial profile uses the same pinned
   signing key authority for all three; independently administered key rotation
   requires a newly reviewed complete generation.
4. Runtime authenticates/retrieves all envelopes and verifies digest, key, native
   manifest, owner registry, identity and expiry **before atomic activation**.
   Effective lease is the earliest of all three. No hot refresh or immediate
   revocation is claimed. App Configuration, if used, selects immutable versions;
   it does not supply executable adapters or signing authority. No last-known-good
   generation survives its valid lease.
5. The Citadel owner supplies generated publish XML via the existing asset
   `policyXml` input and access XML via a dedicated TOOL product. Unknown APIs
   fail instead of falling through the mixed product's LLM default. The sample
   retains upstream's 60 calls/minute and 10,000 calls/month; these are example
   quotas, not agreed performance/cost limits. Existing nonempty custom XML is
   rejected for manual composition rather than overwritten. No selection
   preserves the original policy inputs byte-for-byte.
   For direct publication, the package emits the observed native API property
   shape and `apim_api_version: 2025-09-01-preview`. Use `mcpProperties` (not the
   older pinned module's ignored `mcpPropperties`) and the endpoint dictionary
   `{"message":{"uriTemplate":"/"}}`. The API/backend service URL is the **origin**;
   the policy owns the exact internal `/mcp` path. A backend URL already ending
   in `/mcp` combined with that rewrite can produce `/mcp/mcp` and a backend 404.
   Read actual deployed properties; source/template acceptance alone is not a
   working MCP route. This is a local overlay, not an upstream module update.
6. Verify inherited policies do not transform effects after the PEP decision,
   read `context.Response.Body` or enable MCP response-body logging (global
   diagnostic bytes must be zero). The overlay retains baseline auth, usage,
   alerts and product quotas, and explicitly owns the single non-retrying
   backend forward with `buffer-response=false`.

Build the existing gateway Dockerfile from the repository root. Its dependency
pins come from `skills/_shared/governance-upstream-pin.json` and service manifests;
do not upgrade ACS/AGT to avoid an advisory. The runtime is UID/GID 10001, has no
embedded configuration/keys and uses Cosmos external durable state. Run with
read-only root and a bounded `/app/scratch` tmpfs; policy/config are read-only
mounts. Native loader scratch is not an operation store. `/health` checks real
policy, dual auth, Cosmos, receipt and selected authority dependencies per binding.

For release, use existing container tooling to retain immutable image digest,
SBOM, provenance, scan output and licenses. A local Docker `--load` is not a
registry signature or guaranteed attestation retention. Base-image review,
registry publishing/signing, vulnerability disposition, production network/IAM,
multi-replica and latency/load validation remain separately owned gates, **not
certification**.

## Update, rollback and the external gate

Rebuild owner policy changes and assemble a new signed generation. Do not edit
mounted files, reuse policy ID/version for different bytes or mix generations
between replicas. Deploy an immutable image/config revision, check readiness
then route traffic explicitly; pending operations from an older generation are
not migrated or silently rebound. Retain their exact operation IDs and
reconciliation custody. Roll back only to a still-valid, explicitly authorized
complete generation and matching image/config; expired leases cannot be extended
by rollback. Drain/reconcile uncertain operations before removing a revision.

Before W2-I, agree hub/target/profile, producer operation, permitted mutations,
budget and p95/p99/throughput bounds. Collect fresh original-caller/proxy/backend
identity and signed-generation evidence for allow, both denies, exact human
resume, audit outage, wrong audience/spoof/direct ACA calls, lost ACK and replay.
No local/noop test substitutes for business-write proof. Maintainer review,
merge, registry publication, Pages and live acceptance are distinct gates.
Wave1 email is not MFA; its same-operation proof does not become same-session
effect or live Conditional Access proof here.
