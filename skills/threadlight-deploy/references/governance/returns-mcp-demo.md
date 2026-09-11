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

Required: private service reachability/DNS, ACR, a versioned Key Vault RSA key,
immutable signed-policy Blob storage, single-write-region Cosmos, and distinct
control, gateway, downstream, business-writer, agent and operator identities.
The gateway uses its service identity for governance and a different downstream
identity for the business API. The agent has model inference, policy verification,
governance access and the business **read** route, but no Cosmos role or business
write authorization. App roles are Entra grants, not ARM role assignments.

For a VM-first demo, a separate private **inference-only** OpenAI account may
serve the model while hosted Foundry provisioning is blocked. Preserve the
original Foundry account. Do not call that fallback Foundry Agent Identity or
hosted-agent evidence. For eventual hosted deployment, network injection must be
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

Use the same portable image with separate managed identities and read-only
configuration. Terminate HTTPS at private ACA ingress for the three services:

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
or borrow old receipts. Provisioning automation, authentic hosted identity,
Citadel model routing, OBO, dynamic ERP facts, full output governance and the
broader Task15 acceptance gate remain separate work.
