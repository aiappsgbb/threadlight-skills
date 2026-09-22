# Signed evidence for selected MCP actions

This is an opt-in extension of the existing MCP PEP, ACS/Rego decision, Task8
approval/receipt protocol and backend transaction. It is not a second policy
engine, a document certification service or whole-agent governance.
The agent remains the user's process interface; the model is a holder, not an issuer.

```mermaid
sequenceDiagram
    actor User
    participant Agent
    participant Provider as Evidence Provider
    participant Source as Authorized business source
    participant PEP as MCP gateway / PEP
    participant ACS as ACS / Rego
    participant Approval as Existing Task8 approval
    participant Backend
    User->>Agent: Request a return or loan decision
    Agent->>Provider: Named verification, proposed action arguments
    Provider->>Provider: Authenticate workload; authorize tenant, case, subject
    Provider->>Source: Resolve configured source and version
    Source-->>Provider: Corroboration or insufficient evidence
    Provider-->>Agent: Signed evidence JWT or insufficient_evidence
    Agent->>PEP: Business arguments + attestation metadata
    PEP->>PEP: Verify signature, profile, scope, time and argument binding
    PEP->>ACS: Actual arguments + verified claims only
    ACS-->>PEP: Deny / allow / escalate
    opt Human review required
        PEP->>Approval: Existing intent, including semantic evidence fingerprint
        Approval-->>PEP: Authenticated one-use decision
        Agent->>PEP: Exact resume where supported
    end
    PEP->>PEP: Audit ACK; recheck evidence and authorization after waits
    PEP->>Backend: Authenticated action, idempotency key, fingerprint (no JWT)
    Backend->>Backend: Current source fingerprint + revision CAS / transaction
    Backend-->>Agent: Recorded outcome through PEP
```

## Trust and supported scope

`govern_control_plane.attestations` is a portable contract in the existing
control-plane package, not an added control-plane HTTP signing endpoint.
`EvidenceProvider.issue` accepts an already authenticated `Identity` at its
host-only seam. The returns HTTP route obtains that identity through the existing
`EntraAuth`; constructing an Identity from model input is never authentication.
Explicit grants bind tenant/workload principal/client, case and business subject
**before source access**. The signed gateway requirement independently maps
allowed cases to expected subjects. This bounded initial profile uses an explicit
case map (maximum 128), not arbitrary cross-customer lookup.

`VerificationAdapter.verify` is trusted application code, selected by the host.
It receives untrusted proposed arguments, resolves only configured sources and
returns a typed `VerificationResult` or no corroboration. There is no model-
selectable signer, key, issuer, verifier URL, profile, assurance level or claim
map. The provider assembles the claims itself. A supplied positive boolean is
never copied into a verification result.

This is workload authentication, **not OBO**. `holder`/`holder_client` bind the
verified workload; `sub` is the business subject independently authorized in the
provider grants and signed registry. It is not the chatting user's identity.
Do not give the agent the provider's identity, signing permissions, direct
business writer credential or a bypass route.

| Path | Support |
|---|---|
| Selected MCP gateway actions | JWT verification before native ACS and before transmission |
| MAF gateway-only client | Automatic, inline approval and existing deferred exact resume |
| GHCP registered gateway relay | Automatic and existing inline approval; metadata bound to the host ticket |
| GHCP deferred approval | Still rejected by generation; no resume implementation is implied |
| Canonical returns native local hooks | Existing `trusted_context_provider`, read receipts and Cosmos controls unchanged; no new JWT seam |
| Unbound reads / tools without the requirement | Existing behavior; no ACS added to reads |
| Arbitrary URLs, provider-hosted tools, Copilot internal loop | Not covered |

## Selection, configuration and transport

Add `signed-evidence` to the selected tool's governance-contract `requires`.
It is accepted only on `governed-tool-gateway` + `pre_tool_call`.
The matching **signed** `gateway-registry.json` action must contain
`evidence_requirement` with these fields:

| Field | Meaning |
|---|---|
| `issuer`, `audience` | Exact evidence authority and relying-party audience, distinct from Entra authentication |
| `profile`, `purpose` | Fixed named verification version and action purpose |
| `keys` | One to four `{kid, public_key}` entries: versioned Key Vault key ID and RSA public PEM; never private keys |
| `subjects` | Exact `{case_id: business_subject}` scope |
| `case_field`, `revision_field` | Required string fields in the original business input schema |
| `max_age_seconds` | Explicit positive token lifetime ceiling, at most 3600; a technical limit, not a retention rule |

Use `EvidenceRequirement.model_json_schema()` and
`govern_gateway.dispatcher.Registry.model_json_schema()` for the authoritative
wire shapes. Explicit null, empty/invalid configuration, private keys, absent
binding fields, or a contract/registry selection mismatch fails closed.
Generation vendors the same real provider/verifier code; it never provisions an
issuer, invents keys, trusts unsigned registry edits or upgrades old evidence.

One named profile can justify several domain claims and source references.
ACS decides their sufficiency and any financial/human-review conditions. A
selected tool requires an attestation even when ACS would otherwise permit its
amount automatically. Required evidence and human approval are separate gates.

The canonical MCP transport is `tools/call.params._meta["threadlight/evidence"]`.
For existing clients that only expose tool arguments, the advertised selected
tool schema adds optional `governance_evidence`; the **business schema does not
change**. It is optional in discovery because completed-outcome retrieval does not
need it; a new effect without it is denied. MAF and GHCP remove that reserved
field and place it in metadata, binding it to the exact call before credential
waits. The gateway also supports the argument form directly, but rejects two
simultaneous transports. Neither form reaches the backend arguments or headers.
Only `X-Evidence-Fingerprint` crosses the separately authenticated backend boundary.
Metadata is not authority until verification succeeds.

The holder necessarily sees the token in its transient tool conversation.
Disable raw tool-content and request-body telemetry in the host, provider,
gateway and proxies; `store=False` is not a guarantee about a platform's
independent logging. The returns CLI disables raw response-file/text capture when
evidence is enabled. Governance receipts and operation records contain hashes,
outcomes and correlation, not the JWT, source documents, amounts or extracted text.

## JWT profile and signing

The profile uses standard compact JWT/JWS through the existing PyJWT dependency:
`typ: threadlight-evidence+jwt`, **RS256 only**, and a configured versioned `kid`.
No algorithm/key URLs supplied by a token are followed. Exact issuer/audience,
signature, integer `iat`/`exp`, bounded lifetime and `jti` are validated with no
clock leeway. Extra header/claim fields and duplicate JSON keys are rejected.
An Entra access token is not an evidence token.

Claims also bind `tid`, business `sub`, authenticated `holder`/`holder_client`,
case and revision, action and purpose, verification profile, versioned source
references/digests and the canonical hash of **all business arguments**.
This deliberately conservative first version does not guess which fields matter.
Argument-changing ACS transforms require a new verification and call; the PEP
does not silently preserve approval or attest transformed values.

Signing uses the existing async `KeyVaultSigner` RS256 digest authority through a
per-instance PyJWT JWS algorithm I/O bridge. PyJWT owns JWS serialization and
verification; Key Vault owns RSA signing. There is no private key in the agent,
new cryptography implementation or global algorithm registration. The provider
checks authority health before/after signing and verifies its own result against
the configured public key. Tests substitute an explicitly local RSA fixture.

The PEP verifies locally without calling the provider again. Its signed public-key
snapshot has **no immediate revocation** capability: change the signed selection/
roll the host to remove a key; unused tokens expire at their bounded lifetime.
Applications requiring immediate key or subject revocation must not claim this
profile supplies it. Current business state is different: the returns backend
recomputes evidence from its current case and enforces the exact case ETag in the
same transaction as the decision audit. Corrections/revocation of that purchase
snapshot update the case revision and block stale evidence. A TTL cannot replace
CAS or a transaction; external mutable OMS/CRM sources need their own atomic
consistency protocol before this guarantee can be extended to them.

## Concrete returns adapter

The existing MCP example's write remains a **recommendation/decision audit, not
a refund settlement**. Its current 500-unit escalation rule is unchanged.
Enable `evidence_enabled: true` in the MAF example configuration to expose the
unbound `returns_verify_purchase` tool and add the matching requirement.
It calls the separately authenticated backend's `/evidence/purchase` route;
the route exists only when backend evidence configuration is valid.

The copyable [returns-evidence.rego](../skills/threadlight-deploy/references/governance/returns-evidence.rego)
uses `data.returns_evidence.pre_tool_call` with native `policy_target: $.tool_call.args`.
Include it in the existing Task6 bundle with the selected signed registry.
It requires corroboration before either automatic decisions or supervisor
escalation and retains this example's 500-unit ceiling. Backend eligibility and
high-risk rules remain independently enforced; these are not new document claims.
Keep the selected gateway's approval roles and `approval_requirement` aligned
with the intended existing inline/deferred review mode.

Configure the backend with the identical `evidence_requirement`:
profile `returns-purchase-v1`, purpose `record-return`, case field `case_id`,
revision field `expected_etag`, and the same case/subject map and key.
Its existing provider service credential, not the agent credential, uses that
configured signing authority. Signing permission is an operator prerequisite;
this change adds no resources or RBAC assignments.

The operator-controlled case must contain `customer_id`, `currency` and a
`purchase` snapshot with exactly `reference`, `revision`, `customer_id`, `amount`
and `currency`. The snapshot represents an admitted business purchase ledger,
not arbitrary uploaded/OCR data. Amount/currency and customer must match the
case. Corrections to it must change the owning case ETag. Source ingestion and
trust in the original ledger remain deployment responsibilities; sample data is
not proof of that provenance.

`purchase_verified` means that correlation and amount were corroborated by that
configured backend snapshot. `defect_declared` records a declaration only;
`defect_verified` is never emitted by this adapter. A forged model flag, unmatched
amount, malformed/unavailable source or unsupported document cannot become
positive evidence. Missing corroboration returns structured
`insufficient_evidence`, without an attestation. Provider failure is explicit and
cannot authorize the action. A reviewer approving an action cannot fill this gap.

`PurchaseAdapter` is reusable from other host-owned provider tools. The
`evidence_content` helper lets this backend recompute exactly the same semantic
fingerprint before its existing case-replace/audit-create transaction. The
backend still independently authenticates its writer and checks its business
rules. Matching a PEP header is not backend authentication.

Configure an **existing business-owned** `evidence_container` with `/scope` and
`defaultTtl` exactly equal to the explicitly supplied `evidence_retention_seconds`.
Also supply a `business_retention_policy` identifier for the operator's case/
source retention procedure. There is no default or invented legal duration.
Verification outcomes, source references/versions and justified claims are stored
there before the token is returned, using the owned Cosmos transport's guarded
append. It contains no raw JWT. Original documents/snapshots remain in business
storage under the selected procedure; token expiry neither deletes nor retains
them. Existing approval/effect ledgers keep their anti-replay constraints; do not
apply evidence TTL to those ledgers to reopen old operations.

## Consent, replay and uncertain completion

The semantic fingerprint includes issuer/audience, holder, subject, case/revision,
action/purpose, verification profile, sources, claims and argument hash. It excludes
only `iat`, `exp`, `jti` and signature/key rotation within the configured trust set.
It enters the existing action/context hashes, review metadata and durable receipt.
JWT renewal with identical semantics does not extend the approval's own expiry
or create new consent. Material source/claim/argument changes cannot consume an old
approval. Deferred resume re-evaluates ACS before one-use consumption; evidence
and trusted context are rechecked at transmission after credential/transport waits.

An attestation is reusable evidence, not a universal one-use capability.
The existing operation reservation, approval nonce CAS and backend idempotency
prevent duplicate effects. The same durable operation and exact original arguments
retrieve an already completed result even if the JWT has since expired or the
provider is unavailable. That path does not execute another POST or claim fresh
evidence. Existing output policy still applies; on completed replay it receives
only host facts/fingerprint, not a fabricated fresh attestation.
On the initial response it receives refreshed host facts and the statement
verified for the effect, not a new claim that its sources are current after execution.

`outcome_unknown` retains the existing pending reservation. Do not retry with a
new operation identifier or renewed JWT to escape it: reconcile the original
operation with the backend outcome and durable audit. Rejection, expiry and
approval do not imply a completed effect; only the actual backend outcome does.

## Loan fixture and acceptance boundaries

The loan fixtures in `test_evidence_attestations.py` and
`test_evidence_gateway.py` reuse the provider contract, JWS signer/verifier,
MCP dispatcher, audit and native Rego plumbing. Only the source check/profile and
domain claim change: extracted document income or an OCR confidence alone is not
corroboration; matching an admitted **fixture payroll record** can justify
`income_corroborated`. These are synthetic sources, not real lender integrations,
authenticated human document review or a universal forged-document detector.

Local tests cover bad signatures/issuers/audiences/algorithms, token confusion,
expiry, holder/tenant/subject/case/action binding, changed amount/revision,
invented claims, insufficient sources, provider failure, approval renewal/change,
credential-wait expiry, completed replay, concurrent effects, unknown outcomes,
payload-free governance records, both client transports and generation.
The pinned native suite exercises real ACS/OPA and MCP with external authority/
storage fixtures. Existing native local and CTK evidence remains separately scoped.

The assessors report `signed-evidence-proof-required` for a selected target.
Generic probes, LOCAL-14, old receipts and the hosted `governance_probe_noop`
cannot certify this new business contract. No evidence-specific target acceptance
producer is supplied, so this requirement remains `not-verified` in the
governed-actions gate rather than inheriting a green verdict.

**Not performed or proven:** Azure deployment/RBAC, live issuer key custody,
real source ingestion/entitlements, hosted token/log handling, actual retention,
live Cosmos effect closure or Task15 business acceptance. Those require a new,
explicitly authorized deployment attempt and fresh evidence for its exact binding.
