# Governed MCP action gateway — Task 9 reference

**A real PEP, not the read-only assessor.** Official `mcp==1.29.1` FastMCP serves
Streamable HTTP `initialize`, `tools/list`, and `tools/call`. Each signed registry
action becomes one tool, preserving its JSON input schema. There is no URL, shell,
resource, prompt, provider-hosted-tool or generic HTTP executor.

Architecture: host-owned SAFE facts → **local native ACS 0.3.1b0 / OPA 1.18.2**
→ this MCP PEP → a **separately authorizing downstream service**. The package
includes the unchanged Task6 integrity algorithm and shared pin JSON, not another
policy language or copied JWT implementation. It imports the installed Task8
control-plane package. AGT core 5.0.0 and Hooks 0.1.0a5 match the shared pin; MAF
is not required. A gateway does **not** govern the Copilot internal loop, arbitrary
unbound MCP servers, direct credentials, built-in tools, or Invocations output.

## Signed registry and bundle creation

Create `gateway-registry.json` **inside the source bundle before Task6 builds it**.
It must contain:

* `schema: "threadlight-gateway-registry/v1"`, exact `tenant_id`, `gateway_url`
  (canonical HTTPS authority and fixed path, for example `https://gateway.example/mcp`);
* `deployment`: `agent_id`, `agent_version`, `image_digest` (`sha256:` plus 64 hex),
  `environment` (`staging`, `preproduction`, or `production`), subscription **GUID**,
  `resource_group`;
* `actions`: one to 64 entries with unique `name`, non-`none` `policy_binding`,
  nullable `post_policy_binding`, allowed workload **object IDs** in `workloads`,
  application `scope`, `approval_roles` (zero to 16 unique roles; empty unless required), fixed HTTPS
  `endpoint` and `outcome_endpoint`, `credential_scope` (`api://.../.default`),
  `input_schema`, and `output_schema`.

Both schemas are strict JSON objects with `additionalProperties: false`.
Supported keywords: `type`, `properties`, `required`, `additionalProperties`,
`items`, `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum`, `maximum`,
`enum`, `description`. Strings need an enum or bounded length; arrays need
`maxItems <= 128`; depth ≤ 8 and object properties ≤ 32. No remote `$ref`,
defaults/coercion, arbitrary validators or schema-generated code.

The native manifest must bind `pre_tool_call` to the specified policy ID with
`policy_target: $.tool_call.args`. If output gating is declared, it must also bind
`post_tool_call` to its policy ID with `policy_target: $.tool_result`.

Build locally in a Linux amd64 environment with the published pinned packages:

```python
from pathlib import Path
from govern_bundle.policy_bundle import build_bundle, verify_bundle

bundle = build_bundle(source=Path("source"), destination=Path("release"),
                      policy_id="safe", version="1")
assert any(item["path"] == "gateway-registry.json" for item in bundle.files)
verify_bundle(bundle.root, expected_digest=bundle.bundle_digest)
```

Use Task8's **separate publisher** identity and
`ControlPlane.publish(BundleEnvelope(...))` to sign this digest with the configured,
versioned Key Vault key. Follow `../control-plane/README.md`; publishing is not an
HTTP endpoint. The envelope must bind the same policy ID/version, tenant and digest,
and a future UTC expiry. Copy the immutable **entire** release directory into the
gateway's read-only policy mount. Never edit metadata or insert a signature into
it; standalone YAML `signature_verified: true` is not authority.

At startup the gateway fetches the real signed envelope through authenticated
Task8 `GET /bundles/{id}/{version}`, performs Key Vault RSA verification, verifies
the exact Task6 file set/digests (including registry), and loads `AgentControl`.
It rejects mismatched tenant/version/expiry/content, endpoints outside the
host-configured exact allowlist, and missing/mismatched native bindings.
Already-issued snapshots remain valid only to their signed expiry; immediate
revocation/automatic bundle refresh is not implemented. Roll to a newly signed
version to change policy. Missing business SAFE evidence is not invented:
`host_evidence` supplies only authenticated tenant/principal/client, scope and
deployment. A domain-specific host evidence adapter must independently verify
business evidence; models must never supply it or approval/audit anchors.

## Authentication and host configuration

`GATEWAY_CONFIG_FILE` names bounded, host-owned JSON (mount read-only). The
`server.Configuration` schema extends Task8 `auth.Settings`, reusing its real
Entra JWT verifier: exact configured issuer/tenant/audience/token version,
RS256/JWKS, expiry, `Governance.Workload` app role, `idtyp=app`, no delegated
`scp`, and `(object ID, client ID)` allowlist. Human/delegated tokens do not call
gateway tools. Do not trust forwarded claims/EasyAuth headers as authentication.

Additional required fields:

| Fields | Meaning |
|---|---|
| `gateway_url` | Exact external HTTPS MCP route; also restricts Host/Origin |
| `control_plane_url`, `control_plane_scope` | Task8 origin and its distinct `/.default` audience |
| `service_client_id`, `service_principal`, `service_agent_id` | Gateway managed identity client/object IDs and its Task8 workload agent ID |
| `downstream_client_id` | **Different** managed identity, never an incoming agent's client ID |
| `cosmos_url`, `cosmos_database`, `cosmos_container` | Existing idempotency container with `/scope`, no TTL, single writable region |
| `bundle_path`, `policy_id`, `policy_version`, `policy_digest` | Read-only release and exact signed selection |
| `allowed_endpoints` | Exact downstream POST and GET HTTPS URLs; no redirects/query/userinfo/non-443 port |

All Task8 base fields are required as specified in its README, including pinned
Key Vault `key_id`. Its human/auditor settings are reused for schema compatibility;
gateway HTTP accepts workloads only. Task8 must authorize the **gateway service
principal** for bundle, receipt and approval APIs. Approval intents use that
authenticated gateway identity; `context_identity`/`action_hash` bind the original
verified requesting workload and exact deployment, not a model-supplied user.
There is no implicit end-user delegation or OBO.

Production constructs `azure.identity.aio.DefaultAzureCredential` with only the
selected managed identity enabled. Developer, environment-secret, broker and
interactive fallbacks are disabled. Its service identity needs scoped Cosmos
data access and Key Vault verification/read permissions; downstream identity
needs only the registered service operations. Do not grant the calling agent
these identities, direct credentials, or network/IAM routes to the downstream.
Task10 implements infrastructure; this reference never changes cloud settings.

Every MCP HTTP request is authenticated, including initialize/list/stream/session
methods; there is no initialized-session auth bypass. This service deliberately
uses stateless, JSON-response transport: no resumable cross-request authorization
or background effects. `Idempotency-Key` is a bounded identifier HTTP header on
each call; the host integration must provide it, not add fields to tool arguments.
Only `/health` is anonymous.

### Readiness

`GET /health` returns 200 `status: ready` only when the selected bundle is fresh,
the gateway authentication authority and idempotency store are available, and
**every registered tool's required dependencies** pass. All registered tools require
a receipt client; only tools with nonempty signed `approval_roles` require an
approval resolver and configured approval principal/agent ID. An unused resolver
is not checked. A missing client, missing health/operation method, or failed check
returns 503 `status: unavailable`, with payload-free `dependencies` and `bindings`:

```json
{
  "dependencies": {
    "approvals": {"healthy": false, "reason_code": "approval_unavailable"}
  },
  "bindings": {
    "refund": {"healthy": false, "reason_codes": ["approval_unavailable"]}
  }
}
```

The response also retains `policy_digest`, `registry_loaded`, and `scope`.
Stable failure codes are `policy_unavailable`, `auth_unavailable`,
`idempotency_unavailable`, `receipt_unavailable`, and `approval_unavailable`.
Bindings unaffected by an approval failure remain healthy even though aggregate
readiness is 503. Errors never include exception messages, tokens or business payloads.

Both real Task8 clients validate their configuration and obtain a service token
to call **authenticated `GET /health`**. The control plane authenticates/authorizes
that workload and checks its actual durable store, signing key and authentication
authority. Clients require the authenticated readiness response, not the anonymous
health response: deploy the matching updated control-plane package with the gateway.
For required approvals, the gateway sends its configured service principal/agent ID,
the signed registry tenant and required approval roles through the read-only
`approval_context` health query contract **separately for each bound tool**, using
the same validated context constructor as dispatch. Role order is preserved;
duplicates are rejected at signed-registry load and runtime validation, not silently
deduplicated. The registry caps these concurrent, time-bounded checks at 64 tools.
`dependencies.approvals` summarizes all approval checks, but each binding uses only
its own result: one unauthorized role configuration cannot poison another tool's
readiness. Shared backend failures still affect every dependent binding.
The control plane reuses its actual
approval requester authorization against authenticated claims and its configured
workload agent mapping/approver roles. Syntactically valid but mismatched IDs or
roles produce 503 here with `approval_unavailable` on approval-required bindings;
they cannot pass by supplying a client claim or an echoed identity. The client
requires `approval_context_validated: true` in addition to authenticated health,
so older servers ignoring the query fail closed.
Authentication denial, bad endpoint/audience, invalid responses, backend failures
and timeouts fail closed. No synthetic receipt, approval request, nonce consumption,
policy evaluation or downstream call is used to test readiness.

Checks run concurrently, bounded to five seconds per dependency (and each HTTP
client's configured `request_timeout`), and policy freshness is rechecked afterwards.
Each request probes current dependencies; failures are not permanently latched,
so recovery can return 200 without restarting. This does **not** reopen pending
idempotency records or automatically retry unknown effects.

Readiness is a read-only availability check, not proof of a future durable write,
downstream authorization, live effect closure, or whole-agent success. Dispatch
still requires the actual durable receipt ACK and exact approval consumption.

## Exact dispatch and durable unknown outcomes

1. Authenticate workload; validate tenant, workload/action/application scope and schema.
2. Canonical action hash; verify binding and current bundle.
3. Evaluate native local ACS with host SAFE facts.
4. Resolve required approval using Task8 request/poll/human-decision/exact atomic
   consume protocol. Transformations are revalidated and approved under the
   **transformed** hash. Missing, rejected, expired or replayed approval fails closed.
5. Conditional-create a durable pending idempotency record.
6. Obtain durable Task8 **pre-execution receipt ACK**; persist its linkage.
7. Acquire a distinct short-lived downstream token; recheck expiry/policy/audit
   facts before transport and at pinned httpcore HTTP/1 header/body-send events
   (after pool wait/TLS). No retries, caller hooks, redirects or forwarded tokens.
8. POST only canonical validated/transformed arguments. Persist the returned
   **outcome reference**, not arguments or result payload.
9. Apply declared native post-tool policy, validate bounded explicit output schema,
   then return sanitized structured MCP output. A post-tool deny suppresses payload,
   but cannot undo an already-authorized effect.

Denials write a payload-free Task8 `DecisionReceipt` and make zero downstream calls.
Receipts contain only strict Task8 fields: IDs, hashes, decision/reason, timestamp,
agent version and image digest. The action hash covers requester/tenant/action,
scope, policy and full deployment. Correlation ID is the hash of the request's
idempotency key; a live collector must establish its relationship to a real
Invocations operation, not merely echo that selector.

Partition is tenant + action + requesting principal; a key also binds input,
policy and deployment facts. A competing conditional create never redispatches.
Changed arguments/policy/deployment on the same key conflict (`status_code: 409`
inside an error tool result; successful MCP transport still uses HTTP 200).
Pending keys **never
expire or reopen** automatically. Lost network ACK, cancellation, restart, or a
post-effect store failure returns unavailable/unknown and requires reconciliation.
This is **not exactly-once execution**. The independently authenticated downstream
must also transactionally enforce idempotency, scope authorization and schema.

Completed replay uses durable facts without consuming a new approval. ACS
reconstructs transformed arguments and checks their original enforced hash.
An authorized **GET** at `outcome_endpoint` retrieves the bounded result by the
same scoped idempotency header and validates its exact persisted receipt ID;
output policy runs again. Results are never cached in Cosmos/audit payloads.
No reconciliation API is supplied; operators must verify the downstream's durable
transaction before resolving an unknown key. Never delete pending records to retry.

### Required downstream contract

Only registered `POST endpoint` and `GET outcome_endpoint` are supported:
`Authorization: Bearer <downstream-audience-token>`, a requester/tenant/action-scoped
`Idempotency-Key`, `X-Action-Hash`, `X-Governance-Provenance` (pre-receipt ID),
`X-Tenant-ID`, `X-Requester-ID`, `X-Action-ID`, `X-Policy-Digest`, and
`X-Deployment-Hash`. POST body is the schema-validated argument object; GET has
no body. The service must independently authenticate/authorize the gateway
identity and operation; these headers are provenance, **not authorization tokens**.

Both responses must be HTTP 200 with exactly
`{"receipt_id": "<bounded durable operation ID>", "result": {...}}`.
No 202/asynchronous jobs, redirects, generic URLs, API keys or raw error bodies.
Timeouts are bounded, responses ≤ 16 KiB, input arguments ≤ 16 KiB, MCP body
≤ 32 KiB. Output must satisfy the explicit registry schema after any native
post-tool transformation. The downstream implementation is separately owned;
this gateway cannot prove that service's transaction/authorization semantics.

## Packaging, tests and evidence limits

From repository root, a local build (no push/deploy):

```sh
docker build --platform linux/amd64 \
  -f skills/threadlight-govern/references/gateway/Dockerfile -t governed-gateway:local .
```

The image compiles/installs actual portable packages, pins OPA by shared release
checksum, runs non-root, and exposes `threadlight-govern-gateway` on port 8000.
Mount host configuration and the immutable bundle read-only. Never mount calling
agent credentials or a model-writable policy directory.

`scripts/ci/run-governance-pin-tests.py` now includes the gateway native suite in
an **isolated service venv**, leaving published MAF/ACS validation bytes untouched.
Missing/skipped native controls cannot pass. Tests use real ACS/Rego, RSA-signed
bundles/JWTs, the Task8 approval API and official MCP ASGI; only external HTTP and
durable storage are doubles. This is local reference evidence, not Azure proof.

The read-only GHCP assessor returns/renders `action_posture` using shared binding
statuses. `action-governed` requires an exact gateway URL, authenticated API-observed
credential inventory, network **and** IAM direct-access blocking, complete built-in/
provider equivalent-effect coverage, and a fresh correlated deployed-Invocations
deny receipt/zero counters plus a distinct successful positive control. Positive
bypass dominates a local deny. Unbound MCP remains unbound; missing coverage stays
unverified. `AssessmentOptions.effect_observation_verifier` is a trusted in-process
collector seam, not a JSON `verified` flag: Task11 must implement authenticated
collection, verify nested receipt origin/integrity, and independently observe the
exact agent version/image/policy/environment/subscription/resource group. CLI/static
URL rewriting alone never instantiates this authority. No whole-agent-governed or
SAFE-complete claim is made.
# Optional staging probe telemetry

Task 11a adds an opt-in signed noop action, authenticated registration/status
routes outside MCP, server-observed durable phases and direct denial receipt
metadata. It does not add a live collector or automatically deploy a fixture.
See [the producer contract and operator instructions](../../../threadlight-safe-check/references/probe-fixture/README.md).

`ProbeConfiguration.credential_mode="platform-noop"` is an explicit native-only
option for `open_runtime(config, platform_credential=...)`. The caller owns the
actual platform credential; the service confirms its Entra claims before using
it. `service_client_id` is the observed agent client, **not** a UAMI selector.
No `downstream_client_id` or fixture writer credential is accepted in this mode.
The signed registry must contain exactly one probe-safe `governance_probe_noop`
for that authenticated subject. The independent fixture remains the only writer
of its counter store. Default/native local-file and business gateway identity
separation are unchanged. This option does not implement remote native assets or
prove that hosted AgentIdentity can access the native Cosmos producer.
