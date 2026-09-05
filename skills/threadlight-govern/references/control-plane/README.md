# Governance control plane (Task 8 reference)

Independent Python 3.12/FastAPI service; **no AGT, ACS, MAF or OPA dependency**.
Its storage implementations use the asynchronous Azure SDKs, not a production
in-memory fallback. No resources, app registrations, human invitations or cloud
deployments are created. Task 9 gateway interception, Task 10 infrastructure and
Task 11 live proof remain separate.

## Trust boundary

The API validates RSA/RS256 Entra signatures against a bounded, cached JWKS fetched
from the **configured tenant's** `login.microsoftonline.com` endpoint. It checks
exact issuer, audience, tenant, token version, `exp`, `nbf`, `iat`, `oid` and
authorized client (`azp` for v2, `appid` for v1). Configure the API registration's
access-token version and optional **`idtyp`** claim. Workloads require `idtyp=app`,
no delegated `scp`, `Governance.Workload`, and an exact configured `(oid, client)`
binding. Each workload has one agent ID and an explicit policy-ID allowlist.

Human decisions require a **delegated** token from a configured UI client, an
allowlisted human object ID, scope `Governance.Approve`, and an app role present
in all three places: the human's signed token, `approver_roles`, and the immutable
intent's `allowed_roles`. Self-approval is forbidden. App-only tokens cannot
become humans by supplying approver fields. The service derives the approver
object ID and tenant from validated claims. Auditors require an allowlisted human
object ID, delegated `Governance.Read`, and `Governance.Auditor`.

There is no API-key, connection-string, unsigned-claims, Easy Auth
`x-ms-client-principal`, forwarded-header, or development authentication bypass.
Unexpected key-location JWT headers are rejected. TLS termination and the
workload's HTTPS trust store are part of the trusted deployment boundary.
Workload client credentials and HTTP transports are host-owned, never model-owned.

**Task7 `provider.principal` must be the authenticated workload's Entra `oid`.**
Its `tenant` must match that credential, and `agent.id` must match the configured
workload agent ID. A model user name or end-user subject is not a substitute.
End-user delegation/obo is **not supported** without a separate verified
delegation design. Merely putting a user ID in the intent does not delegate.

One service configuration accepts one tenant. Multiple tenant deployments may
share a container, but all document partition keys and blob prefixes are selected
from validated identity/configuration, not unverified request fields. Workload
receipt reads require the owner object ID; authorized auditors can read within
their own tenant. Workload bundle reads require the policy allowlist.

## Exact Task7 contract and human state machine

`models.ApprovalRequest` is exactly the portable `runtime/approval.schema.json`
**intent** shape, with additional size/type/UTC constraints; `ApprovalGrant`
preserves the complete grant shape. No second incompatible approval contract:

| Concept | Task7 wire field |
|---|---|
| Action/policy digest | `action_hash`, `policy_hash` |
| Requesting subject/tenant | `principal`, `tenant` |
| Execution scope | `agent_id`, `session_id`, `context_identity` |
| One-use binding | `nonce` (32 lowercase hex), `expires_at` |
| Policy lifetime/role constraints | `policy_expires_at`, `allowed_roles` |
| Actual human decision | grant `approved`, `approver`, `approver_tenant`, `approver_role` |
| Durable decision provenance | grant `provenance` (server-created random identifier) |

All intent fields are immutable. Expirations are UTC RFC3339 strings, evaluated
against the server's current clock before and after durable writes. Approval
expiry must not exceed the signed policy expiry or the configured maximum
approval lifetime. Exact digest and policy expiry must match a verified,
published envelope. IDs contain only bounded identifier characters; models are
strict, forbid extra fields and do not coerce strings to booleans/numbers.

Only these five routes exist (no OpenAPI/docs/publishing route):

| Method/path | Authorization and result |
|---|---|
| `GET /health` | Anonymous readiness only, 200 healthy or 503 unhealthy |
| `GET /bundles/{policy_id}/{version}` | Allowed workload or tenant auditor; signed envelope |
| `POST /approvals/resolve` | Discriminated operation described below |
| `POST /receipts` | Workload only; durable receipt acknowledgement |
| `GET /receipts/{receipt_id}` | Owning workload or authorized tenant auditor |

The approval endpoint accepts these bounded JSON bodies:

```json
{"operation":"request","intent":{"...":"complete Task7 intent"}}
{"operation":"resolve","intent":{"...":"same complete intent"}}
{"operation":"decide","intent":{"...":"same complete intent"},"approved":true,"approving_role":"Approver"}
{"operation":"consume","intent":{"...":"same complete intent"},"grant":{"...":"complete returned grant"}}
```

The ellipses above are explanatory, **not valid wire fields**.

1. Authenticated workload `request` creates `pending`, returns
   `202 {"status":"pending"}`. Identical requests are idempotent while pending or
   decided; changed intent or a consumed nonce returns 409.
2. An independently authenticated human submits `decide` for that exact intent.
   One ETag-guarded update wins; concurrent/repeated decisions return 409.
   The result is `200 {"grant":...}`. `approved=false` is a real denial.
3. Requester `resolve` returns pending (202) or the durable grant (200).
   There is **no automatic approval**, default approver or invented human intent.
4. Requester `consume` sends the entire grant and intent. The server compares them
   to the exact durable decision and rechecks scope, role authority and freshness.
   Only a successful ETag-guarded `decided → consumed` acknowledgement returns
   `200 {"consumed":true,"grant":...}`. Denials are also consumed exactly once.
   Replay/conflicting consumption returns 409. Backend failure/timeouts never
   permit an effect. A lost successful write acknowledgement burns the nonce;
   retry is not treated as approval.

Human notification/UI delivery is intentionally external. That workflow must
present the exact payload-free intent for an explicit human decision and call
the same endpoint with its delegated token. It must not use workload credentials
to impersonate a human. Task7's configured approval timeout still applies: absent
a timely decision, the current operation is denied. A later operation needs a
new intent/nonce and a new human decision.

## Immutable bundles and signing

Task6 integrity-covered files and unsigned `bundle.json` remain **unchanged**.
CI/admin code obtains the digest from Task6 `verify_bundle` and invokes
`ControlPlane.publish(BundleEnvelope(...))`; publication is a backend method,
not an HTTP endpoint. Use a separate authorized publisher managed identity.

`BundleEnvelope` contains exactly `policy_id`, `version`, `content_digest`,
`expires_at`, `tenant_id`, `key_id`. The key ID must pin a 32-hex Key Vault key
version. Canonicalization is sorted-key, compact UTF-8 JSON; UTC timestamps use
`datetime.isoformat()`. `envelope_digest` takes SHA-256 of those bytes.
`KeyVaultSigner` passes those **32 digest bytes** to
`CryptographyClient.sign/verify(SignatureAlgorithm.rs256, ...)`. Only the base64
signature and envelope are stored; no private key material is accepted.

Blob publication uses `overwrite=False` for both
`{tenant}/policies/{id}/{version}.json` and `{tenant}/digests/{hash}.json`.
Readers require both objects to agree. A partial two-object publication fails
closed and needs operator reconciliation; no overwrite/repair endpoint exists.
An existing policy/version can never be silently replaced by this application.
Substituted IDs, tenant, key version, digest, expiry or signature fail verification.
Revocation/expiry of the configured key is checked before bundle authentication
and by readiness using fresh
`KeyClient.get_key`; do not rely on Crypto SDK's cached public-key verification
as a live key-health check. Key rotation requires explicit configuration and
republishing under new versions, not an unversioned `"latest"` key.

## Runtime client

Install this package alongside the separately copied Task7 runtime package.
`client.ApprovalClient` implements Task7's async `resolve`/`verify` protocol.
Pass the actual runtime `ApprovalIntent` and `ApprovalGrant` classes through
`intent_type` and `grant_type`. The client obtains a fresh bearer token using
the caller's async `TokenCredential.get_token("api://<api-id>/.default")`, uses
HTTPS without redirects, polls pending within a bounded timeout, and validates
typed responses. `verify` always calls the service's atomic `consume`; it is not
a local boolean/claim echo. Never add HTTP retries to consume.

`client.PolicyClient` takes the host's tenant, pinned key ID, and actual runtime
`VerifiedPolicy` class as `verified_policy_type`. Call
`await client.load(policy_id, version, expected_digest=task6_digest)` **before**
constructing the provider. The returned `PolicySnapshot` supplies the synchronous
`signature_verifier.verify(bundle)` required by Task7, binding the exact local
Task6 digest and signed expiry. Its authority is the configured HTTPS service,
which verifies the Key Vault signature; it is not an independent offline Key
Vault verifier. A host must not substitute a model-controlled URL or HTTP client.
The snapshot expires at signed expiry and cannot extend itself; refresh/recreate
it explicitly. Immediate revocation of already-issued snapshots is not provided.

Use `async with` or `aclose()` for client-owned HTTP pools. Injected HTTP clients
and credentials remain caller-owned and must also close. Use an async
`DefaultAzureCredential` restricted to managed identity, as in `app.production`;
do not enable environment/client-secret or developer credential fallbacks.
Caller cancellation propagates; transport/validation failures expose only stable
errors. Test transports never constitute evidence of live Entra authorization.

## Configuration, runtime and minimum permissions

Mount a host-owned JSON file and set `GOV_CONFIG_FILE` to its path:

```json
{
  "tenant_id": "<tenant-guid>",
  "audience": "api://<api-application-id>",
  "token_version": "2.0",
  "key_id": "https://<vault>.vault.azure.net/keys/<key-name>/<32-hex-version>",
  "workloads": {
    "<workload-object-id>": {
      "client_id": "<workload-client-id>",
      "agent_id": "agent-1",
      "policies": ["safe"]
    }
  },
  "human_clients": ["<ui-client-id>"],
  "approver_subjects": ["<human-object-id>"],
  "auditor_subjects": ["<auditor-object-id>"],
  "approver_roles": ["Approver"],
  "request_timeout": 5.0,
  "approval_max_seconds": 300,
  "blob_url": "https://<account>.blob.core.windows.net",
  "blob_container": "bundles",
  "cosmos_url": "https://<account>.documents.azure.com:443/",
  "cosmos_database": "governance",
  "cosmos_container": "records"
}
```

Replace every placeholder with trusted configuration; unknown/missing/malformed
configuration fails readiness, never an in-memory or anonymous fallback. This
reference targets Azure public cloud; sovereign authorities/endpoints are not
implicitly accepted. DefaultAzureCredential is restricted to managed identity
(optional `AZURE_CLIENT_ID` selects a user-assigned identity, not a secret).
All async SDK resources close even when Cosmos initialization fails.

- API identity: container-scoped **Storage Blob Data Reader**.
- Publisher only: container-scoped **Storage Blob Data Contributor** (or a narrower
  custom read/create role), plus Key Vault sign permission.
- API: Key Vault key-scoped public-key read and verify permissions; no private-key
  export, creation, deletion or signing permission is required. A narrowly scoped
  custom role is preferable to a broader crypto role.
- Cosmos: data-plane `readMetadata` and container-scoped item read/create/replace;
  **no delete**, management-plane provisioning or account-key access needed.
  Configure partition key **`/scope`**, exactly **one writable region**, and
  **omit `defaultTtl`**. Keep nonce tombstones permanently. The adapter checks
  these properties before writes and in readiness. Azure Cosmos' multi-write
  conflict resolution is not a global one-winner approval mechanism.
  The pinned aio 4.16.1 SDK exposes account metadata through
  `_get_database_account`; this one documented-in-source compatibility dependency
  must be retested on upgrades. No SDK code is patched.

Do not grant the API identity blob overwrite/delete or nonce deletion rights.
Storage-admin access, infrastructure changes and process/host compromise are
outside this cooperative boundary. Do not enable TTL/delete old nonce records
or change replication topology while serving traffic; infrastructure changes
require draining and revalidation. Receipt retention, tenant partition capacity,
immutable-storage policies, private networking, HTTPS ingress, authentication
registration, abuse/rate limits and production RBAC are Task10 deployment work.

Build with this directory as Docker context. The image runs as UID 10001 and
starts `uvicorn govern_control_plane.app:create_app --factory --port 8080`
without access logs/proxy-header trust. Missing configuration intentionally
produces 503 readiness. Mount configuration read-only; terminate HTTPS at the
trusted ACA ingress. The application's local HTTP port is not a public bypass.

Receipts are payload-free, immutable by `(tenant, receipt_id)` and idempotent only
for the same canonical body **and owner**. They include `receipt_id`,
`correlation_id`, `action_id`, action/policy digests, decision, reason code,
agent version, image digest and UTC `recorded_at`. `error` is accepted solely for
Task7 failed-native-operation compatibility, not as an authorization verdict.
Task7's local spool uses different names and lacks action/time metadata: a
host-owned exporter must explicitly map `audit_id → receipt_id`,
`policy_hash → policy_digest`, and supply trusted action/time/deployment fields;
never forward a raw spool object or invent missing provenance.

HTTP errors are bounded `{"error":"unauthorized|forbidden|conflict|not_found|
invalid_request|unavailable"}` responses (401/403/409/404/422/503). Actual request
streams, including chunked bodies, are capped at 16 KiB (413), with a receive
deadline (408). Duplicate JSON keys, nonfinite numbers, unknown fields and raw
payload fields are rejected without input/exception/traceback echo. Production
suppresses Azure/HTTP dependency logging (credential failures can include remote
error bodies even at WARNING); Docker disables access logs. Do not re-enable
SDK HTTP-content/debug logging or add access logs containing unvalidated paths.

## Offline verification and limits

Run `pytest skills/threadlight-govern/tests/test_control_plane.py` from the repo
in an isolated environment installed from `pyproject.toml[test]`. Ordinary service
tests use cryptographically signed test JWTs and public JWKS; Blob/Cosmos protocol
doubles model immutable creation and CAS across independent app instances.
SDK autospec tests verify ETag/partition parameters, create-only blob writes,
Key Vault digest algorithms and async lifecycle. No Azure resource is contacted.

Six native integration cases require the existing exact-pin Linux amd64 runtime
validation environment (`THREADLIGHT_GOVERNANCE_RUNTIME=1`, `ACS_OPA_PATH` pointing
at the pinned OPA) **plus isolated service dependencies**. They exercise actual
Task7 MAF/ACS effects, signed policy snapshots and the HTTP approval client with
approve, deny, pending, mutated grant, outage and replay. Skips in a service-only
environment are **not native runtime proof**. No claim of live Entra, Cosmos,
Blob, Key Vault, ACA deployment, image build or production readiness is made.

### SDK and identity sources

- [Entra access-token validation](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens#validate-tokens)
- [Claims and authorization](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation)
- [Async DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.defaultazurecredential)
- [Blob uploads and async examples](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python)
- [Async Cosmos replace_item / ETag](https://learn.microsoft.com/en-us/python/api/azure-cosmos/azure.cosmos.aio.containerproxy)
- [Cosmos transactions and multi-region OCC](https://learn.microsoft.com/en-us/azure/cosmos-db/database-transactions-optimistic-concurrency)
- [Async Key Vault sign/verify](https://learn.microsoft.com/en-us/python/api/azure-keyvault-keys/azure.keyvault.keys.crypto.aio.cryptographyclient)
- [Async KeyClient / key properties](https://learn.microsoft.com/en-us/python/api/azure-keyvault-keys/azure.keyvault.keys.aio.keyclient)
