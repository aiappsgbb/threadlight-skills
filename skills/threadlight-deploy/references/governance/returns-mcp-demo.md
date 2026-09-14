# Real returns agent through governed MCP

This bounded demo uses a **real Azure model -> native MAF -> authenticated MCP
gateway -> separately authenticated business API -> Cosmos case/audit transaction**.
It records recommendations and supervisor handoffs, **never financial settlement**.
It is a smaller vertical than the canonical native-hook returns example; it does
not claim that example's OMS/CRM correlation, policy citations or Foundry IQ.

The first execution used an operator VM, **not Foundry hosted**, with private ACA
control-plane/gateway/business services and a private Azure OpenAI inference endpoint.
An actual model-selected ordinary return was recorded. A direct native MAF
FunctionTool invocation was denied without a case mutation. Another native
FunctionTool invocation requested review; an actual delegated human approved it,
and exact resume consumed that grant before recording a supervisor handoff.
Independent Cosmos reads joined business audits, central allow/deny receipts,
completed gateway operations and the consumed human grant. These are different
scenarios, not a claim that all three were initiated through the model.

No private deployment identifiers or captured receipts are distributed here.
These source files and their package manifest are **not live proof for another
deployment**, whole-agent governance, OBO or production readiness.

## Public authenticated hosted path (September 13)

The subsequent **Foundry hosted v5** run demonstrated model-driven allow, a
real Cosmos decision/audit, policy denial, durable pending approval and an
audited unbound read. This is not the failed private hosted attempt: it used a
separate, explicitly authorized public-authenticated environment. Human
approve/reject and successful hosted resume remain unproved there; an expired
resume was observed without a second business write. See the
[scenario-specific execution record](../../../../docs/governed-returns-validation.md#s3-public-authenticated-foundry-hosted-execution).

Use the complete `foundry-hosted-agents` baseline from the reviewed
`aiappsgbb/awesome-gbb` commit `2ef44f6b47803a0166956cc668e5f429c1c1f8cb`:
`references/python/main.py`, `references/python/pyproject.toml`,
`references/docker/Dockerfile`, and `references/yaml/azure.yaml`.
Apply **azure-tenant-isolation** first: derive both CLI configuration directories
from the personal index, verify the selected tenant and allowed subscription,
and assert the exact intended subscription immediately before each operation.
Setting only `AZURE_CONFIG_DIR` is not that contract. Do not copy login caches.

1. Establish native hosted/model operation before adding governance. Use the
   canonical guided initialization or its documented greenfield YAML adaptation
   and native `azd ai agent init --infra` / `azd provision`. Keep platform-injected
   runtime variables out of the agent environment block. Observe two direct
   version GETs and an actual endpoint response/session, not just LIST status.
2. Generate the separate governance services using the
   [public-authenticated-proof opt-in](README.md#explicit-public-authenticated-proof-networking).
   For a preserved demo use `preserve_resources: true, cleanup_required: false`
   in both configurations; no cleanup or shutdown is authorized by policy expiry.
   This proves authenticated runtime behavior, **not private network isolation**.
   Organization policy may override declared PNA. Observe actual resources and
   policy operations; stop for an authorized decision rather than silently
   changing policy. The S3 operator-authorized `SecurityControl=Ignore` tag was
   local to that new resource group, not a generator default or general recipe.
3. Materialize the source closure below. For the generated MAF agent, optionally
   select the retained canonical dependency file in `package.json`:

   ```json
   {
     "environment": "preproduction",
     "hosted_cohort": {
       "file": "/operator/retained/canonical/pyproject.toml",
       "sha256": "sha256:<actual-64-hex-file-digest>"
     }
   }
   ```

   This bounded, hash-checked option replaces only the three exact Agent Server
   pins and validates the MAF/Projects family against the supported cohort.
   It is MAF staging/preproduction only, not an independent SDK upgrade or a
   production-support claim. The frozen configuration records the hash and pins,
   not the operator file path. Global pins stay unchanged. The live S3 image
   used these three pins by an explicit packaging adjustment; this new generator
   option makes that adjustment reproducible, but was not used to rebuild v5.
4. Select `bootstrap_scope: selected-tools` and frozen `gateway_descriptors`
   before building the gateway-only MAF image. The host can answer native
   readiness before registration has supplied its identity and signed binding.
   First selected dispatch activates the original signed bootstrap and performs
   authenticated discovery; descriptors must match exactly. Readiness is not
   authorization. The default whole-host gate remains unchanged.
5. Build once and record the actual registry digest. Run `generate.py agent-image`
   with that digest. Generated native `docker.imagePassthrough: true` with
   `remoteBuild: false` requires validated **azd 1.34.0** or newer; ordinary
   `azd deploy` then registers the frozen image without rebuilding it.
   Reconcile uncertain acknowledgements before any retry. Observe the actual
   agent identity/version/image before granting narrow external app roles and
   publishing a new matching policy/bootstrap binding. Never redeploy the
   agent after signing just to change its own embedded identity/digest.
6. Deploy control/gateway/business as separate identities and images. The business
   image needs the packaged `runtime` and `cosmos_effect.py`, not only the control
   plane wheel. If configuration is mounted as a secret file and loaded once,
   set a nonsecret `RETURNS_CONFIG_VERSION` revision marker from its public
   configuration (for example Bicep `uniqueString(string(publicBusinessConfig))`).
   This forces a new ACA revision on configuration change; it is not a
   cryptographic configuration digest. Verify actual policy/version readiness
   after routing propagation. Preserve prior revisions and failed images.

These are operator steps, not a single automatic deployment. The retained
foundation outputs, versioned Key Vault signing key, app-role assignments, exact
registry, policy publication and observed binding still require real authorized
inputs. Do not create a local demo signer or give the agent Cosmos/sign rights.
Private hosted networking remains a separate first-account-creation contract.

### Private BASIC startup prerequisite and image comparison

The [September 14 private BASIC result](../../../../docs/governed-returns-validation.md#september-14-private-basic-model-smoke-after-registry-binding-and-image-comparison)
establishes private hosted/model operation, **not private governed returns**.
It did not use the generated governance image or reuse a signed business binding.
The first gate was the official
[container deployment precheck](https://github.com/microsoft/GitHub-Copilot-for-Azure/blob/91b451609306a490e84854c7c2c1fd79c62398a4/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/deploy/references/container-deploy.md):
an existing ACR needs a **project-scoped ContainerRegistry connection**.
**AcrPull alone** or a matching login-server environment variable is insufficient.
Read the project connection inventory and exact resource without credentials;
verify category, target, `metadata.ResourceId` and supported authentication.
The Basic no-BYO-store contract does not exclude this registry connection.

If absent, use the native azd-ejected connection module and the native ACR
module's `ManagedIdentity` mapping for the observed project principal and
registry resource ID. Preview only the connection resource and preserve the
existing registry, network, keyless settings and role assignments. Do not
invent an SDK `connections.create` call, create keys, deploy the whole ACR
module over an existing registry, or grant unrelated roles.
The active azd environment's `AZURE_CONTAINER_REGISTRY_NAME`,
`AZURE_CONTAINER_REGISTRY_ENDPOINT`, `AZURE_CONTAINER_REGISTRY_RESOURCE_ID`
and `AZURE_AI_PROJECT_ACR_CONNECTION_NAME` must identify those actual resources.

The correction alone did not make the original private image run. A subsequent
controlled comparison used native ACR import to copy the identified working
public **BASIC v1** image into private ACR without rebuild, force overwrite,
source writes/grants or PNA changes. Compare **source and destination** raw
manifest bytes/digest and referenced config/layer descriptors before registration.
A mutable source tag is not provenance: retain its observed metadata and use
the resolved digest. Preserve the failed image and attempt.

Use service-level `image: <private-registry>/<repository>@sha256:<digest>` with
native `docker.imagePassthrough: true`, `remoteBuild: false`, and the verified
azd 1.34.0-or-newer binary. Confirm package output and actual version readback,
not only YAML acceptance. Hold connection/network/model/runtime settings fixed;
require two direct active GETs and a real native model response/session.
This is an image-level comparison, not proof that manifest media type alone
caused a previous failure.

For any future local or remote build, inspect the actual context and require
`.dockerignore` exclusions for `.env`/credentials, `.azure`, virtual environments
and caches without excluding needed application files. The inspected historical
contexts lacked that file; their full uploaded archives were not retained.
Matching Dockerfiles do not prove image identity or safe context exclusion.
The prebuilt comparison sent no new build context and did not retrofit an ignore
file by silently rebuilding either frozen image.

### Durable unbound read audit and response reconciliation

Set the optional backend `read_audit_container` to a dedicated `/scope` Cosmos
container without TTL. Give the **business writer**, not the agent, its narrow
metadata/item-create permissions. The backend appends a payload-minimized
`case-read` record and waits for the real Cosmos ACK before returning case data
with `read_audit_id`. Failed persistence fails the read. Its create-only
`CosmosEffectTransport.append_audit` does not broaden the existing two-operation
case-replace/decision-audit transaction. The read remains unbound to ACS; setting
this option does not turn it into a selected policy action.

For the two exposed tools, use `returns_reconcile.py` under an independently
authorized operator. Supply protected JSON with:

| Field | Required value |
|---|---|
| `expected_binding` | Independently pinned `tenant_id`, full versioned `key_id`, `agent_id`, `agent_version`, `image_digest`, `policy_digest`, `config_digest`, `project_endpoint` |
| `binding_file` | Original signed bootstrap envelope; never a fabricated local receipt |
| `gateway_principal` | Observed gateway subject, not the agent subject |
| `cosmos_url`, `cosmos_database` | Actual matching stores with independent read permissions |
| `responses` | Explicit array of `{ "id": "<native-response-id>", "session_id": "<native-session-id>" }` |
| Optional `containers` | Complete role-to-name mapping for `governance-records`, `gateway-idempotency`, `returns-cases`, `runner-activity`; all four names must be valid and distinct |

```sh
python skills/threadlight-deploy/references/governance/returns_reconcile.py \
  --configuration /operator/protected/reconciliation.json \
  --output /operator/evidence/new-reconciliation --persist
```

The collector verifies the signed binding and native version identity/image,
retrieves each response through the authenticated native API, then independently
reads the four demo containers (`governance-records`, `gateway-idempotency`,
`returns-cases`, `runner-activity`). `--persist` needs narrow create/read
authority on `runner-activity`; it creates deterministic call records and reads
them back. Existing mismatches stop execution, never overwrite prior evidence.
It makes no model call, grants no role and executes no business action.
The caller must perform the full paired tenant/subscription assertions above;
the collector's environment-presence check is not a replacement for them.

For a private deployment with prefixed stores, select all four explicitly,
for example `s2-governance-records`, `s2-gateway-idempotency`, `s2-returns-cases`
and `s2-runner-activity`. Omitting `containers` preserves the original default
names; **missing is not the same as invalid**. Null, partial, duplicate or
unsafe mappings are rejected before cloud access/output creation. The selected
names drive both independent reads and ledger persistence; evidence retains the
mapping. Never silently read S1 stores while claiming S2 proof.

The September 14 private operator path obtained native response readbacks under
the existing operator context, then used the existing **operator managed identity**
to verify the private signed binding and independently read/write the scoped
Cosmos ledger through the same reconciliation/store helpers. This is distinct
from the generic CLI's Azure-CLI credential mode; it does not copy CLI caches
into the private VM, bypass signature/freshness checks or manufacture responses.
It verified private allow/deny and four call records; unavailable human review
left private pending/resume/replay/email unproved.

This is **post-run reconciliation of an explicit response set**, not universal
agent attestation. Historical reads lacking backend ACK are labeled post-run;
later reads with matching independent audit are
`backend-acknowledged-before-return`. Denials without a returned receipt use the
action hash and native response time window; repeated identical calls can match
the same receipt set, not an invented one-to-one mapping. An expired-selector
observation preserves generic failure status and correlates the exact operation
hash, unchanged intent and absent grant; it does not invent a timeout receipt
or prove which guard caused a generic FunctionTool failure.

### Optional native email notification

`review-notification.bicep` creates an Office 365 connection and **Disabled**
Logic App with SAS authentication disabled, exact Entra issuer/audience/operator
OID policies, a fixed recipient and bounded request fields. It is not wired to
automatically poll/resolve approvals. **Office 365 consent** must be completed
by the real mailbox user before the operator explicitly enables and tests it.
`connectionState: Enabled` alone is not consent: inspect authentication status.
The S3 connection remained unauthenticated; no email was sent.

An email is **not a grant**. The workflow cannot resolve an approval or execute
the business write. Review still uses the genuine delegated Task8 client, then
the original hosted session resumes with unchanged arguments and operation ID.
For native CLI resume pass `--session-id` without `--version` (mutually exclusive);
verify the response's actual session/version. Expired intents remain preserved:
a future positive demonstration needs a new explicitly requested operation and
real human decision, never silent renewal. Keep the workflow disabled until
consent and an actual notification test are authorized.

### Human continuation after an expired request

First, in Azure Portal open the **specific demo resource group -> API connections
-> configured Office 365 connection -> Edit API connection -> Authorize**.
The actual mailbox user signs in and saves. Verify the connection no longer
reports `Unauthenticated`; an Enabled flag alone is insufficient. Keep the
notification workflow disabled until the operator explicitly enables it for a
real test. That enablement does not automatically send a notification: a separate
operator-authenticated request with the new operation/case/action hash is still
required. Do not enable SAS or weaken the trigger's Entra policy.

When the reviewer is present, the operator verifies current signed bootstrap
and policy freshness, unchanged deployed version/image/identity, and the
supervisor case's current revision. If any authority has expired or changed,
stop: use the existing fresh publication/binding lifecycle first, preserving the
old association. Do not edit timestamps or re-sign an old reference in place.

Start a new native hosted session on the observed version and ask for the
supervisor handoff. This is a **new business request**: omit
`governance_operation_id`, so trusted client code creates a new operation.
Preserve the actual `pending_approval` tool output (not the whole native response)
as a new protected `new-pending.json`; check that its operation and nonce differ
from the expired ones, that it is unexpired, and that its proposed arguments
match the intended case/revision. Do not fabricate this file or reuse an expired
capture. Record the new native session ID for resume.

From a real interactive terminal with the portable control-plane package
installed, the human runs:

```sh
threadlight-review-action --pending /operator/protected/new-pending.json \
  --control-plane-url https://<actual-control-service> \
  --scope api://<actual-control-api-client-id>/Governance.Approve \
  --tenant <actual-tenant-id> --client-id <actual-review-public-client-id> \
  --role Approver --decision approve
```

The client displays the exact proposal/hash/expiry and asks the human to type
`APPROVE <nonce>` (or `REJECT <nonce>` with `--decision reject`), then opens
the genuine Entra browser login. Use the already allowlisted reviewer account
with the Approver role and delegated consent; no agent credential or unattended
confirmation is accepted. Successful review reports `execution: not-started`.

Only afterwards resume in that **new request's original hosted session** with
its exact `resume_arguments` and new operation ID. Verify the consumed grant,
central receipt and real business audit independently. A successful positive
resume and a subsequent identical replay remain separate demonstrations; neither
can be claimed from a pending record, notification, CLI exit code or assistant text.

## Materialize the executable sources

From the catalog:

```sh
python skills/threadlight-deploy/references/governance/package_returns_mcp.py \
  --output /absolute/new/returns-mcp-source
```

The command copies the actual control plane, gateway, native runtime, shared
validators/pins, MAF client/host, and canonical `CosmosEffectTransport`. It adds
`returns_mcp_backend.py` and `returns_mcp_agent.py`, not alternate approval/JWT
protocols or SDK patches. Existing destinations are refused. It makes no Azure
calls and includes no credentials, automatic seed/reset, deployment configuration
or invented signatures.

Build that directory under Linux amd64 using an approved Python 3.12 **digest**:

```sh
docker build --platform linux/amd64 \
  --build-arg PYTHON_IMAGE="$APPROVED_PYTHON_312_IMAGE_DIGEST" \
  -t returns-mcp:local /absolute/new/returns-mcp-source
docker run --rm --entrypoint /usr/local/bin/opa returns-mcp:local version
```

OPA is installed by a checksum-checking script, not a Docker heredoc: the legacy
builder encountered during the demo silently skipped an inline Python heredoc.
The MAF dependency project and service packages come from the existing pins.
Keep the returned real registry digest before creating a final signed registry.

## Supply existing, isolated Azure dependencies

Use the current [foundation/service runbook](README.md), existing
[`AzureConfiguration`](../../../threadlight-govern/references/control-plane/README.md)
and [gateway `Configuration`/registry](../../../threadlight-govern/references/gateway/README.md).
Do not run a blanket redeployment over preserved resources.

Required: service reachability/DNS in the explicitly selected posture, ACR, a versioned Key Vault RSA key,
immutable signed-policy Blob storage, single-write-region Cosmos, and distinct
control, gateway, downstream, business-writer, agent and operator identities.
The gateway uses its service identity for governance and a different downstream
identity for the business API. The agent has model inference, policy verification,
governance access and the business **read** route, but no Cosmos role or business
write authorization. App roles are Entra grants, not ARM role assignments.

For a VM-first demo, a separate private **inference-only** OpenAI account may
serve the model while hosted Foundry provisioning is blocked. Preserve the
original Foundry account. Do not call that fallback Foundry Agent Identity or
hosted-agent evidence. For private hosted deployment, network injection must be
declared at the Foundry account's first creation; follow the existing create-once
operator contract rather than creating versions after signing a binding.

### Business authority

Create a separate `returns-cases` container partitioned by `/case_id`, without TTL.
Give only the business writer metadata access plus item read/create/replace on
that container. A separate operator may seed synthetic cases **create-only**:

```json
{
  "id": "RMA-EXAMPLE",
  "case_id": "RMA-EXAMPLE",
  "kind": "case",
  "status": "in_triage",
  "amount": 40,
  "eligible": true,
  "high_risk": false
}
```

Read the real `_etag` back. This demo's policy must bind each allowed case to that
observed revision and trusted eligibility/risk snapshot. Never substitute a
model-supplied amount, boolean or fabricated revision. Ordinary eligible approval
can allow; an attempted ineligible approval denies; known high risk/value above
500 permits only an authenticated supervisor handoff (`escalate`).
Use `pre_tool_call`, `$.tool_call.args`, native policy ID `returns-safe` and
control-plane bundle ID `returns-write-v1`. Publish through the real Task8
`ControlPlane.publish(BundleEnvelope(...))` operator API and retain both Blob
indexes and the original signed envelope outside the digest-covered bundle.

Register only `returns_apply_decision`, scope `returns`, with strict input
`case_id`, `expected_etag`, `decision`, `reason`; output is exactly
`case_id`, `decision`, `audit_id`. Decisions are `approve_refund`, `deny_refund`,
`escalate_to_supervisor`, `request_more_info`. Fix POST `/decisions` and GET
`/outcomes` to the business service. The read endpoint is GET `/cases/{case_id}`.
Declare signed `approval_mode: deferred`, `approval_requirement: policy`, role
`Approver`; keep its timeout at or below both services' `approval_max_seconds`
and the policy expiry.

`returns_mcp_backend.Configuration` extends the shared authentication settings
with `cosmos_url`, `cosmos_database`, `cosmos_container`, `service_client_id`,
`writer_subject`, `agent_subject`, `cases`, `policy_digest`, `deployment`.
Its workload map must contain exactly the agent and downstream callers.
The API verifies caller authorization, fixed scope/provenance, action hash,
schema, current business facts and ETag. It atomically replaces the case and
creates a decision audit, and retrieves a prior outcome on an identical replay.
The original Cosmos transport rechecks caller authorization after credential,
retry, pool and TLS waits. No signing/approval/audit policy is delegated to the model.

## Start the four actual services

For the VM-first variant, use the same portable image with separate managed
identities and read-only configuration. Terminate HTTPS at private ACA ingress:

| Process | Entrypoint / configuration |
|---|---|
| Control plane | `uvicorn govern_control_plane.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers`; `GOV_CONFIG_FILE`, selected `AZURE_CLIENT_ID` |
| MCP gateway | `threadlight-govern-gateway`; `GATEWAY_CONFIG_FILE`, read-only final bundle |
| Business API | `uvicorn returns_mcp_backend:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers`; `RETURNS_CONFIG_FILE` |
| MAF agent | Default image entrypoint with `--configuration /config/agent.json --serve --port 8088` |

The agent config includes `tenant_id`, `key_id`, `policy_id`, `policy_version`,
`policy_digest`, `agent_id`, `agent_client_id`, `environment`, `control_plane_url`,
`control_plane_scope`, `gateway_url`, `gateway_scope`, `business_url`,
`business_scope`, `cases`, `model_endpoint`, `model_deployment`, and
`agentserver_state_root`. Scope values select the actual distinct API audiences.
The model endpoint is the private `https://<name>.openai.azure.com` authority;
the client uses the real `/openai/v1/` Responses API and managed identity, not an
API key or an old dated Chat Completions route.

Set `agentserver_state_root` to an explicitly writable operator-owned directory
for the container UID (10001 in the package). The native SDK honors
`AGENTSERVER_STATE_ROOT`; its default `~/.agentserver` failed for a UID with no
home in the first demo. Local response storage is not the centrally durable
governance receipt store. The VM entrypoint binds loopback, not a public endpoint;
use an authenticated operator SSH tunnel. Native server `/readiness` alone is
not selected-governance readiness: also verify gateway per-binding health and
authenticated control-plane health. Actual tool dispatch reauthorizes independently.

For one bounded model-driven run, use `--prompt "Read and triage RMA-EXAMPLE"`
with `--output /evidence/new-response.json`. The output must not already exist.
Unknown effects require reconciliation; never blindly retry a failed invocation.

## Demonstrate, inspect, preserve

1. Ask the real agent to read and record an ordinary eligible case. Preserve
   native model/FunctionTool call IDs and the returned audit ID.
2. Attempt an ineligible approval through the real MAF FunctionTool/MCP route.
   Inspect its central deny receipt, unchanged case ETag and zero business audits.
3. Request a high-value supervisor handoff. Save the exact `pending_approval`
   result. Use the existing `threadlight-review-action` with a real delegated
   browser identity, explicit human confirmation, correct client consent and
   `Approver` role. On a private operator network, a trusted review frontend may
   call the same `review.decide(..., http=...)` with an explicit HTTPS proxy;
   do not forward the human token to MCP or use app-only consent.
4. Resume using exactly its `governance_operation_id` and `resume_arguments`.
   Inspect the consumed approval, completed gateway operation, pre-effect central
   receipt, and matching Cosmos case/audit transaction. A completed replay must
   retain the same audit and case revision. Noop receipts are not business proof.

Do not delete resources, identities, grants, keys, images, cases, receipts,
pending operations or source packages to reset the demo. Preserve failed attempts.
When cases change or policy expires, create new synthetic cases and a new signed
policy version with fresh observed revisions; never overwrite an old envelope
or borrow old receipts. Public S3 establishes authentic hosted identity use for
this selected action, not the private S2 deployment. Full provisioning automation,
Citadel model routing, OBO, dynamic ERP facts, full output governance and the
broader Task15 acceptance gate remain separate work.
