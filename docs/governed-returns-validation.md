# Governed returns: scenario and execution record

**First execution: 2026-09-11.** This is the PR-facing record of what was built,
what actually ran, and what each observation establishes. It is not a deployment
certificate, a production-readiness verdict or an assertion that every agent
action is governed. Preserve this first scenario when adding subsequent ones.

Times below are **UTC**; Italian local time on this date is UTC+02:00. Tenant,
subscription, resource hostnames, principal IDs, operator addresses and login
artifacts are retained in the private operational capture, not in this document.
Resource aliases below identify responsibilities, not deployable example targets.
The separate delegated-token workstream is outside this record.

## Scenario status

| ID | Scenario | Last recorded status | Meaning |
|---|---|---|---|
| S1 | Native MAF in a Docker container on an operator VM; real private model, governed MCP, separate business API and Cosmos | Executed, with the bounded evidence below | Working functional baseline; not a Foundry hosted deployment |
| S2 | The same business boundary called by a real Foundry hosted agent and its observed platform identity | S2-REGISTERED-NOT-RUNNING: governed versions 1-3 failed; canonical startup-control versions 1-3 failed September 13 and unchanged version 4 failed September 14 | Project-identity registry access is observed on both days; registration, image acquisition and signed associations are not hosted business execution |
| S3 | Separate public-authenticated Foundry hosted MAF with governed MCP and real Cosmos business writer | Hosted v5 allow, deny, pending approval, expired attempt and durable read/reconciliation executed | Real hosted business proof, not private-network proof; genuine human completion remains blocked |
| S4 | Platform-managed prompt agent using an equivalent external governed action boundary | Applicability assessment only; not implemented or tested | Not interchangeable with the MAF client used in S1/S3 |

**Reference direction:** S3 is the working hosted vertical. S2 remains a preserved
private-network investigation, not a prerequisite to crediting S3. S1 is a useful
functional and diagnostic baseline, not the final hosting architecture.

## S1: VM-hosted native MAF and governed MCP

### Use case and scope

A retail returns assistant reads a synthetic case and records one recommendation:
`approve_refund`, `deny_refund`, `escalate_to_supervisor`, or `request_more_info`.
The consequential effect is a **Cosmos case replacement plus a decision-audit
creation in one partition transaction**. There is no payment or settlement API.

This is deliberately smaller than the
[canonical returns example](../examples/returns-triage-governed/README.md).
There are no live OMS/CRM integrations, order/customer correlation, Foundry IQ,
policy-document retrieval or citation verification in this scenario. No claim is
made that a model executed a named prose skill. The four synthetic cases carry
operator-seeded eligibility/risk facts; this is real infrastructure and real
model execution over synthetic business data.

### Deployed setup

```mermaid
flowchart LR
    Operator["Operator: SSH / private review client"]
    VM["Preserved VM: native MAF Agent + ResponsesHostServer"]
    Model["Private Azure OpenAI: real model"]
    Gateway["Private ACA: governed MCP PEP + ACS / OPA"]
    Control["Private ACA: governance control plane"]
    Business["Private ACA: returns business API"]
    Cosmos["Cosmos: cases / business audits / governance records"]
    Policy["Blob signed catalog + versioned Key Vault key"]
    Human["Human: delegated browser login + Approver role"]
    Operator --> VM
    VM --> Model
    VM -->|"unbound authenticated case read"| Business
    VM -->|"selected FunctionTool / Streamable HTTP"| Gateway
    Gateway -->|"different downstream identity"| Business
    Gateway -->|"approval / durable receipt ACK"| Control
    VM -->|"current policy authority"| Control
    Human -->|"decide only, not execute"| Control
    Control --> Policy
    Control --> Cosmos
    Gateway -->|"operation ledger"| Cosmos
    Business -->|"separate DB writer / conditional batch"| Cosmos
```

All three ACA services run in the same **internal** environment. They are
separate processes and identities, not three names for a monolithic application.
External ACA ingress on that internal environment admits VNet clients; it does
not make the service internet-public.

| Component | Configuration used or observed | Role in the experiment |
|---|---|---|
| Dedicated resource group | Sweden Central; `preserve=true`, `cleanup=disabled`; `CanNotDelete` preservation lock observed | Contains the dedicated demonstration resources |
| Operator VM | Existing Ubuntu 24.04, `Standard_D2as_v5`; Linux amd64, Python 3.12, Docker | Build host, operator access and the initial agent host |
| VNet | Dedicated agent `/24`, ACA services `/24`, private-endpoint `/24`, operations `/27` subnets | Private service and data connectivity |
| Agent subnet | Delegated to `Microsoft.App/environments`; NAT associated with the preserved egress public IP | Prepared for Foundry network injection; does not prove hosted execution |
| Operator access | SSH restricted to the operator's `/32`; task-specific known-hosts file verified against the ARM-observed host key | No disabled host-key checking or broad SSH exposure |
| ACR | Premium; admin authentication off; public access disabled; private endpoint | Stores the actual runtime images |
| ACA control plane | 0.5 CPU, 1 GiB; one minimum/maximum replica; dedicated control identity | Signed bundle lookup, human decisions, one-use consumption and receipt ACKs |
| ACA gateway | 0.5 CPU, 1 GiB; one minimum/maximum replica; gateway plus downstream identities | Actual MCP PEP and native ACS/OPA evaluation |
| ACA business API | 0.5 CPU, 1 GiB; one minimum/maximum replica; separate business identity | Authorizes business operations and owns Cosmos effects |
| Key Vault | RBAC; purge protection; public access disabled; private endpoint; actual versioned RSA-3072 key | Signing and verification authority; no local signing-key substitute |
| Blob policy store | Standard LRS; no shared-key or public blob access; private endpoint | Immutable/create-only policy and digest indexes |
| Cosmos | Strong consistency; one writable region; local auth disabled; public access disabled; private endpoint | Durable governance and business state |
| Cosmos database | Shared 400 RU/s declared by the deployed foundation | Contains three separate containers below |
| Private inference account | `OpenAI`, S0; public access and local auth disabled; private endpoint | Model-only fallback, not an agent hosting resource |
| Model deployment | `gpt-4.1-mini`, version `2025-04-14`, GlobalStandard capacity 10 | Actual inference through Azure `/openai/v1/` Responses |
| Log Analytics | Existing dedicated workspace; ACA environment configured for Azure Monitor | Provisioned telemetry dependency, not the source of the business verdict |
| Original Foundry account | `AIServices`, S0; network injection declared on first creation; keyless/private | Preserved hosted attempt; see S2 |

Private DNS was established for ACR, Key Vault, Blob, Cosmos, the internal ACA
domain and the inference endpoint. The attempted original Foundry private
endpoint must not be counted as operational solely because its DNS zones exist.
No ingress bypass or public data-plane fallback was introduced for the working
three-service path.

The Blob retention policy declaration used 365 days. **A locked immutability
retention policy was not verified** in this experiment; create-only publication
by the application is not equivalent to locked retention.

### Identity and authorization boundaries

There are three distinct Entra API audiences: control, gateway and business.
API registrations use v2 access tokens and the optional `idtyp` claim.
Workload validation requires signed Entra claims, the correct tenant/audience,
`idtyp=app`, `Governance.Workload`, and an exact object-ID/client-ID allowlist.
Decoded diagnostic claims are not used as authentication.

| Identity / principal role | Authority exercised in S1 | Authority not given to that role |
|---|---|---|
| Agent UAMI, attached to the VM | Model inference; control/gateway app roles; key verification; business case reads | No Cosmos role; no authorized POST business writer |
| Control UAMI | Blob catalog read, key read/verify, governance-record item access and account metadata | No policy publishing or business-case writer role |
| Gateway UAMI | Control API workload calls, key verification, gateway operation-ledger access | Not the credential used to authorize the downstream business POST |
| Downstream UAMI, attached to the gateway service | Business API app role; exact POST decision / GET outcome routes | No direct Cosmos write role |
| Business UAMI | Cosmos metadata and case-container item read/create/replace; image pull | Does not act as the human approver |
| Publisher UAMI | Created by the existing foundation with publication rights | Its use was **not** the publication path exercised here |
| Operator VM system identity | Actual policy publication, create-only seed, image push and independent evidence reads | Not presented as the agent's application identity |
| Human reviewer | Real browser login from the dedicated review client; delegated `Governance.Approve`; allowlisted subject; `Approver` app role | No business execution or grant consumption in the review client |

The operator VM system identity had key-vault-scoped **Key Vault Crypto Officer**,
container-scoped catalog publication rights, registry-scoped image push rights,
case seed/write permissions and later database-scoped read-only evidence access.
This is broader operational authority than a production publisher-only identity.
It is an explicit limitation, not a claim of final least-privilege operator design.

**The VM is a trusted host boundary.** Distinct Entra principals do not establish
hostile-process isolation when the operator and agent share a VM with managed
identities available through IMDS. The model was exposed only to the two declared
FunctionTools, with no shell or direct database tool. No hostile-container escape,
IMDS credential isolation or comprehensive network/IAM bypass test was performed.

### Runtime and artifact association

The runtime used the repository's current exact pins:

| Package / engine | Version |
|---|---|
| `agent-framework-core` | `1.14.0` |
| `agent-framework-foundry` | `1.11.0` |
| `agent-framework-foundry-hosting` | `1.0.0b260813` |
| `agent-control-specification` | `0.3.1b0` |
| `agent-governance-toolkit-core` | `5.0.0` |
| `agent-hooks-sdk` | `0.1.0a5` |
| `mcp` | `1.29.1` |
| `azure-ai-projects` | `2.3.0` |
| `azure-ai-agentserver-core` | `2.1.0` |
| `azure-ai-agentserver-responses` | `2.2.0b1` |
| `azure-ai-agentserver-invocations` | `1.2.0b1` |
| Control plane / gateway packages | `0.2.0` / `0.2.0` |
| OPA | `1.18.2`, Linux amd64 static binary checked against the shared SHA-256 pin |

Source of truth:
[governance upstream pins](../skills/_shared/governance-upstream-pin.json) and
[MAF dependency project](../skills/threadlight-deploy/references/governance/pyproject-maf.toml).
Installing/importing these packages is not itself native CTK or live proof.
No installed SDK or native verifier was patched.

| Artifact | Observed digest / identifier |
|---|---|
| Inherited repository baseline | `4427c2d67a1c12abf3288e7ec8ca7ab928235b8e` |
| First runtime image, retained and used by control plane | `sha256:205fb424159e975a25ba6099410fee78e973f14fe2e88485d596f49bf6cadb0c` |
| OPA-corrected runtime image, used by gateway, business and VM execution | `sha256:b78c838b1a6205cb3bb49268243c46c416d63fe8da378216a9fbef4013c928d2` |
| Selected bundle | `returns-write-v1`, version `1` |
| Native intervention policy | `returns-safe`, `pre_tool_call`, target `$.tool_call.args` |
| Signed bundle content digest | `sha256:29eb9710becbe74767d05d9bfee16d955da301e3101c42d610bd4ccfb5951291` |
| Declared agent/version in the S1 registry | `returns-demo` / `vm-demo-1` |
| Signed policy expiry | `2026-09-12T11:29:11.420855+00:00` |

`vm-demo-1` is an **operator-declared VM version**, not a Foundry-observed agent
version. The first VM run used a digest-pinned runtime image with read-only
mounted runner/configuration files. Those mounts are outside that image digest.
Do not call this a self-contained hosted-image attestation or infer that the
inherited Git baseline contains all subsequent uncommitted demo additions.
The later source packager includes the runner in its standalone image; that
new build was checked separately and was not substituted into S1's live binding.

### Policy, approval and storage semantics

The signed bundle contains an exact gateway registry and operator-observed
case revision/eligibility/risk snapshots. An argument carrying an `_etag` is not
trusted by itself: the policy compares it with the signed snapshot, and the
backend separately reads current Cosmos state and applies ETag CAS.

This is **static, signed case-snapshot governance**, not a dynamic ERP evidence
adapter. Case changes require a new authorized snapshot/policy path. It does not
prove the richer ordered-read evidence contract of the canonical returns example.

The selected write tool accepts only `case_id`, `expected_etag`, `decision`,
`reason`. It returns only `case_id`, `decision`, `audit_id`. It fixes the business
POST `/decisions`, GET `/outcomes`, tenant, requesting agent, application scope,
policy digest and deployment hash. The separate business API reconstructs the
action hash and independently authenticates the downstream caller.

`returns_get_case` is an unbound local MAF FunctionTool that calls the authenticated
business read endpoint. That read does not invoke ACS. The actual PEP for the
selected write is the remote gateway; installing Agent Hooks in the common
image does not mean a local Hooks bundle mediated this VM agent's internal loop.

| Store | Partition / operation | What was actually retained |
|---|---|---|
| `governance-records` | `/scope`; conditional create/replace | Payload-free decision receipts and immutable-intent approval state |
| `gateway-idempotency` | `/scope`; scoped operation reservation and CAS | Completed operation linkage, hashes and downstream outcome reference |
| `returns-cases` | `/case_id`; conditional replace + audit create batch | Current synthetic cases and business decision audits |
| Blob signed catalog | Create-only policy/version and digest indexes | The actual Key Vault-signed envelope |
| VM response/evidence files | Operator files and container-local SDK response store | Native responses and operational captures; not central audit durability |

The registry declares `approval_mode: deferred`, `approval_requirement: policy`,
role `Approver`, timeout 3600 seconds. Both services allow that window and the
intent is also bounded by policy expiry. An ACS `allow` can execute autonomously;
an ACS escalation requests human approval and returns without a business effect.
The operation selector is not consent.

The human client validates the displayed action hash and requires explicit
terminal confirmation. It obtains a separate delegated browser token and calls
the existing `review.decide` function through a loopback SSH HTTPS proxy. It does
not persist/forward that token to MCP or execute the business action. The first
incomplete confirmation (`APPROVE` without the nonce) submitted no decision.

Resume uses the original operation ID and arguments. The control plane atomically
consumes the real grant; the gateway reserves execution and requires a durable
pre-effect receipt ACK. The business backend then checks current facts and uses
the existing `CosmosEffectTransport` for the exact conditional transaction.
Authorization rechecks at credential/transport boundaries are implemented by
the reused runtime. This live run did **not** inject every possible expiry,
revocation, retry, TLS wait or concurrent-writer race.

### Executed cases

Initial cases were explicitly seeded by the operator with create-only semantics:

| Case | Amount (synthetic units) | Eligible | High-risk flag | Initial state | Intended exercise |
|---|---:|---|---|---|---|
| `RMA-ALLOW` | 40 | true | false | `in_triage` | Model-driven ordinary recommendation |
| `RMA-DENY` | 80 | false | false | `in_triage` | Forbidden refund-approval attempt |
| `RMA-SUPERVISOR` | 1200 | true | false | `in_triage` | Amount threshold triggers human review |
| `RMA-REJECT` | 900 | true | true | `in_triage` | Reserved but not executed as a human-rejection case |

The threshold was amount **greater than 500**, or `high_risk=true`.
The seed did not define a currency. The model's final prose used a dollar sign;
that is not authoritative currency data or proof of output-policy enforcement.

| Execution ID | Driver and observed interval | Observed outcome | What it establishes |
|---|---|---|---|
| S1-MCP-DENY | Direct native MAF FunctionTool, 11:34:09-11:34:15 | Attempted `approve_refund` for ineligible `RMA-DENY`; `threadlight:gateway_denied`; central deny receipt; original case ETag and zero business audits retained | Real MCP/ACS denial, not merely model refusal; not a model-initiated negative test |
| S1-HUMAN-REQUEST | Direct native MAF FunctionTool, 11:34:18-11:34:25 | `pending_approval` for supervisor handoff; operation `363c754aa12a47768333c1d443105c4c`; no immediate effect | Real durable deferred request, not a blocking fake approval |
| S1-HUMAN-RESUME | Actual delegated browser decision, then native FunctionTool resume at 11:46:10-11:46:18 | Grant approved by allowlisted human and consumed; case becomes `escalated`; one business audit | Genuine human authority and exact resume into a real Cosmos effect; request/resume were not initiated by a model |
| S1-MODEL-ALLOW | Real model + native MAF Agent, 11:50:06-11:50:15 | Model selected `returns_get_case`, then `returns_apply_decision`; case becomes `closed` with `approve_refund` recommendation and one audit | Full model-to-business vertical; no settlement |
| S1-SERVED-READ | Later HTTP POST to the running native `/responses` server | Native response `completed`; real model called only `returns_get_case` for `RMA-DENY` and reported ineligible/in-triage | The preserved server is usable over its Responses interface; not a second served write proof |
| S1-COMPLETED-REPLAY | Native FunctionTool resume at 11:58:33-11:58:38; independent read at 11:58:40 | Same supervisor audit returned; still one audit for that case, same post-effect case revision | Completed-result replay without another business effect; not a concurrent exactly-once test |

The model-run window was approximately 8.794 seconds, excluding agent
construction/initial readiness. Native usage reported 1687 input and 185 output
tokens, 1872 total. This single short run is not a latency, cost or quality benchmark.

### Evidence chain

The model response was not accepted as proof on its own. The operator used real
Cosmos SDK reads, with read-only evidence permissions, to inspect the stores
independently of the model and the tool's returned JSON.

For each successful business write the check joined:

1. The current case's `audit_id` to exactly one decision-audit document.
2. The audit's original expected ETag to the operator's pre-effect case capture.
3. The audit's provenance receipt ID to a central `allow`/`execution_authorized`
   receipt for the same policy and action hash.
4. That receipt and audit to one `completed` gateway operation whose
   `outcome_reference` is the same audit ID.
5. For the supervisor case, the exact pending intent to one stored approval in
   `consumed` state, with `approved=true`, the allowlisted human and `Approver`.
6. For the model case, actual native function-call/result messages to the
   independently observed business audit.

| Association | Actual captured identifier |
|---|---|
| Model response | `resp_03a911d8372bf570016aa3eaf5a71c81938e3b1dce499c5fc9` |
| Model case-read call | `call_BiGS2FHV6GtuWrTWNdet0oJB` |
| Model business-write call | `call_MhPP5jaExzEwkBrCkJMPVnIg` |
| Model-case business audit | `decision-6a46b83114ebbe273af558bfebcf791b7b3c2b32fadb188d39cd4f46fb3db58a` |
| Supervisor business audit | `decision-c2879e3e127c579dd31bfea44f5b10fd0b591ac350269ed605dbdda276ff4af5` |
| Central deny receipt | `4a78ef84a6de4d12aa4b254111d4386e` |
| Central supervisor allow receipt | `e4cb9f2b492b4252940117c5467f5aa2` |
| Central model-case allow receipt | `c9ea21f43255483c8bb828fd9c0f9335` |
| Served read response | `caresp_f8e21eb0830a324d005aFtK4Bq2hI2xjNLXYj54c0ERQ6GNPGS` |

The supervisor receipt is timestamped `11:46:17.452030`; its business audit is
timestamped `11:46:18.849242`. The model receipt is timestamped
`11:50:13.299457`; its business audit is timestamped `11:50:13.467116`.
These timestamps support the observed sequence but are **not an atomicity or
timing proof by themselves**. Before-effect ACK and batch atomicity also depend
on the executed protocol/code and Cosmos transaction semantics; no adversarial
distributed-clock experiment was conducted.

Independent captures at 11:53:53 and 11:58:40 observed exactly two business
audits, two completed gateway operations, one consumed approval and three
central receipts (one deny, two allow). `RMA-DENY` and `RMA-REJECT` had no business
audit. The latter is unused, not a tested rejection.

The full private capture includes tenant/principal associations and therefore
is not committed. These hashes identify the retained bytes; hashes alone do
not let a PR reader inspect those private artifacts or authenticate their origin.

| Retained artifact | SHA-256 |
|---|---|
| `model-agent-allow-1.json` | `f3d6bf5c9c386f12ca7fd5ac779ebf80e9c220c64f42dc9994ac3306cf056ac4` |
| `human-review-result.json` | `bb042e4cb809315b8f51938f55c9b143162de72f4e868001f8d608013cd8b5e7` |
| `served-responses-read.json` | `61889ce2a2781474261c71b816ef6beb108e3a40b96a5a499d148957e045b662` |
| `live-business-proof.json` | `9fffd6f5881dea9138a7a86ecbf455d22b418cdac0b838e5116911c81b103436` |
| `live-business-replay-proof.json` | `03ce6b83708193e8d15510098a39fd2b09cbc463cb3a34ff7431ded85943edba` |

Private access/configuration records, original signing envelope, case snapshots,
review result, build logs and source packages are preserved alongside these
captures and on the operator VM. No previous noop receipts or archived v2 green
verdicts were imported as evidence for this write.

### Failures encountered and corrections

| Observed failure | Correction actually made | Evidence / boundary |
|---|---|---|
| Missing operator Bicep compiler | Installed Bicep in the isolated CLI context | Existing foundation compiled and completed; not a new framework |
| Missing Python service dependencies | Isolated Python 3.12 environments; actual portable packages installed | No test-only protocol replacements |
| Private Cosmos DNS not ready at initial service startup | Completed actual private endpoint/DNS deployment; reinitialized the same control revision | Subsequent authenticated control health and real Cosmos operations succeeded |
| Legacy Docker builder silently skipped a Python heredoc; OPA file absent despite build success | Explicit installer script with shared release checksum; a new image retained alongside the old one | Actual binary presence/version checked before policy execution |
| Initial native MCP client returned a generic availability error | Diagnosed actual token claims, authenticated health, initialize and key access; explicitly primed current policy authority before the test client connected | Subsequent native client worked; initial underlying exception was not conclusively isolated |
| First review confirmation omitted the nonce | Submitted nothing; reran exact confirmation and genuine browser login | No auto-approval or weakened confirmation |
| Initial model attempt returned 404 using an old dated Azure API with the Responses client | Selected Azure `/openai/v1/` through the actual SDK with managed identity | Successful model/function-call transcript; old attempt caused no credited business effect |
| Native server tried to write `/.agentserver` under a UID without a home | Explicit operator-owned state path via public `AGENTSERVER_STATE_ROOT` contract | Same preserved server recovered and completed an HTTP Responses request |
| Foundry account reads and dependent writes reported inconsistent provisioning state | Kept the account intact; used a separate private inference-only account and VM runtime for S1 | Detailed hosted limitation below, not reported as hosted success |

### Code and focused local checks

The existing gateway/control-plane/MAF adapters were reused. New source files:

| File | Purpose |
|---|---|
| [returns_mcp_backend.py](../skills/threadlight-deploy/references/governance/returns_mcp_backend.py) | Strict business boundary and conditional Cosmos case/audit transaction |
| [returns_mcp_agent.py](../skills/threadlight-deploy/references/governance/returns_mcp_agent.py) | Native model-driven MAF agent, selected gateway tool and unbound case read |
| [package_returns_mcp.py](../skills/threadlight-deploy/references/governance/package_returns_mcp.py) | Source-closure materializer; refuses overwriting an existing package |
| [returns-mcp.Dockerfile](../skills/threadlight-deploy/references/governance/returns-mcp.Dockerfile) | Standalone Linux amd64/Python 3.12 image build |
| [install_opa.py](../skills/threadlight-deploy/references/governance/install_opa.py) | Explicit hash-checked native OPA installation |
| [returns-mcp-demo.md](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md) | Reproduction and preservation instructions linked from the deployment skill |

The new [test module](../skills/threadlight-deploy/tests/test_returns_mcp_backend.py)
ran **14 focused tests** in the Linux environment. RED was observed before the
corresponding implementation/wording changes, then the focused suite passed:

| Area | Coverage | Scope |
|---|---|---|
| Conditional business batch | Replace/create shape, exact `if_match_etag`, unchanged input object, case/audit association | Local function contract |
| Invalid/stale case | Changed ETag, foreign case ID/partition, closed case, high amount, ineligible refund, risk flag | Seven parametrized local negative cases |
| Supervisor disposition | High value records escalation, not refund finalization | Local business invariant |
| Strict schema | Settlement decision, model approval flag and model amount rejected | Local Pydantic validation |
| Agent contract | Only the selected gateway write and unbound case read | Real shared contract validator |
| Model client | Correct Azure v1 Responses base URL, retries disabled | Actual SDK construction; no model call in this unit test |
| Native server state | Public state-root configuration resolves to the selected directory | Actual hosting SDK helper |
| Materializer | Real source copies, import dependencies present, overwrite refusal, no heredoc installer, scoped documentation | Local packaging contract |

A standalone package was then materialized and Docker-built on Linux. Its actual
runtime/backend/MAF imports and OPA binary were checked. **That build was not
deployed as a new live scenario.** It does not replace S1's image/mount history.
The broad native/CTK suite and Task15 were not rerun or credited by these tests.

### Positive consequences and remaining boundaries

The experiment moved the selected business path beyond a checklist or noop:
actual model tool selection reached an authenticated independent writer, denial
prevented the selected mutation, genuine human authority unlocked a scoped
handoff, and replay recovered a durable prior outcome without duplication.
The reusable source package captures concrete fixes found during execution.

It also separates problems that had been conflated: application/protocol
behavior can work while the intended hosting resource is not usable. That
reduces the unresolved work for S2, but does not eliminate the need to exercise
platform identity, network reachability, immutable hosted configuration and
public agent-endpoint routing.

Not established by S1: production readiness, hostile-host resistance,
whole-agent/output governance, exhaustive bypass testing, rejection/expiry
and concurrent-resume live matrices, disaster recovery, durable agent session
storage, immutable retention lock, load/latency SLOs, cost forecasts or automatic
policy refresh. Native server `/readiness` is not a substitute for gateway
per-binding and authenticated control-plane health.

Preservation remains mandatory: do not remove the lock, delete/reset cases,
consume a new approval on behalf of a human, remove grants/keys/images, or stop
the working demo to make a new scenario easier. Policy expiry at the time above
ends the selected authorization lease, not resource preservation.

## S2: Foundry hosted agent

### Why S1 did not use the intended hosted runtime

The intended account was created as `AIServices` S0, with project management,
local auth disabled, public access disabled and the agent subnet supplied in
`networkInjections` **on its first creation**:
`scenario=agent`, `useMicrosoftManagedNetwork=false`.

The first create operation was accepted around 11:13 UTC. Subsequent operations
returned these concrete errors, at different stages:

| Operation | Actual observed response | What may be concluded |
|---|---|---|
| Foundry model deployment PUT | `AccountIsNotSucceeded`: current state `Accepted` | That request could not create the model at that time |
| Foundry project PUT | `BadRequest`: parent account did not provision correctly; retry creating account | That request did not establish a usable project |
| Foundry private endpoint creation | `AccountProvisioningStateInvalid`, first `Creating`, later an empty state in the message | The attempted private connection was not verified operational |
| Same-account reconciliation PUT using the skill's API version | `InternalServerError` | Reconciliation did not establish success; no deletion/recreation followed |

ARM reads eventually reported `Succeeded`, and a model catalog read also worked,
while dependent writes still returned `Accepted`/invalid-state failures.
The later read-only documentation check again observed `Succeeded` and **an
empty project list**. Read success alone did not establish that a subsequent
write or hosted deployment would succeed.

**No hosted agent version was registered in S1.** There is therefore no observed
Foundry agent identity, hosted cold start, hosted business invocation or signed
hosted-bootstrap acceptance to claim from this experiment. The VM UAMI cannot
stand in for that missing evidence.

The provider-side root cause was **not established**. These observations do not
prove regional lack of support, a MAF incompatibility, a permanent platform
restriction or a required role/network change. Do not remove the preservation
lock, relax private networking or change identity design based on speculation.

### Hosted reference: what must change and what stays

The user selected S2 as the next reference scenario after this record was written.
It must preserve S1 and use the existing
[signed remote bootstrap/create-once contract](../skills/threadlight-deploy/references/governance/README.md)
and [hosted operator CLI](../scripts/ci/hosted_bootstrap.py).

Keep the separate MCP PEP, control plane, business authorization boundary, real
Cosmos effect, authenticated human review and exact resume semantics. Replace
the VM-specific execution assumptions: explicit VM UAMI selection, loopback-only
operator entrypoint, mounted executable runner and operator-declared VM version
are not the target hosted identity/packaging model.

The hosted attempt needs a frozen self-contained agent image; a usable private
project/model endpoint; a single recorded create intent; independently observed
hosted version, image, principal and client; scoped app-role/service allowlists;
a newly signed registry/bootstrap association; and a verified Entra-only public
agent route pinned to that version. Do not invent reserved `FOUNDRY_*` values or
recreate agent versions after binding merely to resolve a configuration cycle.

Use new synthetic cases and fresh evidence for the actual hosted identity.
S1's receipts, completed operations, case revisions and human grant cannot certify
S2. Add a dated S2 execution section only as real operations complete; record
failures with the same precision rather than promoting provisioning to success.

### S2 attempt 1: parent resource still inconsistent

After completing the S1 record on 2026-09-11, the operator attempted a **separate
hosted-reference project** in the preserved, originally injected Foundry account.
No VM case, S1 policy binding or running S1 application was changed.

| Check / operation | API / result |
|---|---|
| Read original account and project collection | `2025-06-01` account read: `Succeeded`; project collection: empty |
| Create separate hosted-reference project | `2025-06-01`: `BadRequest`, parent account did not provision correctly |
| Create model inside that Foundry account | `2025-06-01`: `AccountIsNotSucceeded`, internal state `Accepted` |
| Discover currently supported account API versions | Provider listed stable `2026-07-01` and preview `2026-07-15-preview` |
| Read the original account through `2026-07-01` | `Succeeded`; original agent network-injection configuration still present |
| One same-account PUT through `2026-07-01`, after 12:11 UTC | `InternalServerError`; original private/keyless/injection configuration was submitted unchanged |

The same-resource recovery did not establish a usable parent. **The create-once
hosted-agent SDK operation has not been called:** no usable project was observed,
so its parent-observation precondition was not satisfied. No hosted version,
platform identity or hosted business receipt is claimed.

At that boundary the operator was asked whether to authorize a second isolated
Foundry account or investigate the original provider state without new resources.
A fresh account is not assumed to fix the underlying issue; it is a separate
attempt, not a destructive reset or a relabeling of the VM.

### S2 attempt 2: explicitly authorized separate account

The user explicitly approved the second private account and a separate injection
subnet. The original account, original subnet, inference-only fallback and all
working S1 services/data remain preserved.

At `2026-09-11T12:55:18.528837+00:00`, the new account creation returned HTTP 201,
`provisioningState=Creating`. The new `/24` subnet was delegated to
`Microsoft.App/environments` and associated with the existing NAT gateway.
Account creation used supported stable API `2026-07-01`, `AIServices` S0,
system-assigned identity, project management enabled, local auth disabled, public
access disabled, and **first-create** agent injection into the new subnet.

The operator persisted an exclusive create-intent record before the PUT and
captured the actual ARM asynchronous-operation header and request/correlation
identifiers. The signed operation URL is private operational data, not included
here. Its status was `Creating` at 12:56:28 UTC. Following that exact operation,
rather than inferring readiness from an unrelated GET, is part of this attempt.

At that point this was **account provisioning in progress**, not a created or
running hosted agent. The subsequent observations below are additional evidence;
they do not turn the initial `Creating` response into execution proof.

### S2 version 1: registration recovered, provisioning failed

**Status: S2-REGISTERED-NOT-RUNNING.** The separate account operation reached
`Succeeded` at `12:59:55.980852` UTC. Its project and model were then successfully
created, and its private endpoint/DNS configuration completed. This overcame
the original account's project/model blocker without deleting or changing it.

S2 uses separate control, gateway, downstream and business UAMIs, separate ACA
service names, and separate `s2-governance-records`, `s2-gateway-idempotency` and
`s2-returns-cases` containers. It reuses the dedicated VNet, registry and signing
authority. The original S1 services, configurations, cases and receipts were not
replaced. Three new synthetic cases (`S2-RMA-ALLOW`, `S2-RMA-DENY`,
`S2-RMA-SUPERVISOR`) were seeded create-only and their actual ETags captured.

#### Build, create and recovery sequence

The existing `generate.py generate` path produced the real
`maf-gateway-container.py` factory, portable dependencies, frozen remote-bootstrap
configuration and service source contexts. An extracted `build_read_tools`
helper uses the credential supplied by that host; it does not select the VM
UAMI. Its added local regression uses an HTTP fixture to verify credential
threading and the absence of a policy dependency on the unbound read. That test
is not hosted token proof.

The self-contained agent image was built and pushed before registration. The
create input fixed its source/image, CPU `1`, memory `2Gi`, Responses protocol
`2.0.0` and allowed environment values. No reserved `FOUNDRY_*` variable or
invented hosted identity was supplied.

| Step | Actual observation / action |
|---|---|
| First native SDK create | HTTP 403, `Traffic is not from an approved private endpoint`; protected attempt retained |
| Private-path diagnosis | VM and explicit SSH proxy resolved/reached the private project; a subsequent authenticated native agent inventory was empty |
| Reconciliation before retry | Authenticated GET of the selected agent returned 404; retained a reconciliation record and left the failed attempt intact |
| Second create with unchanged frozen input | SDK read timeout at 30 seconds; the timeout was not treated as cancellation or proof of absence |
| Independent recovery | SDK `list_versions` returned exactly one actual version, `returns-hosted-reference:1`, initially `active`, with instance identity and the expected image |
| Protected state reconciliation | Compared actual version/image/environment/CPU/memory with the frozen input; preserved the pre-reconciliation attempt and recorded the returned version/identity; no additional create was issued |
| Identity check | Microsoft Graph independently returned `@odata.type=#microsoft.graph.agentIdentity`, `servicePrincipalType=ServiceIdentity`; in this observation its app ID equaled its object ID |
| Scoped external permissions | Assigned the observed identity the control/gateway/business application roles and key verification; no Cosmos role assigned to it |
| Final policy and bootstrap | Signed new case snapshots and registry against that actual image/version/identity; staged and built the gateway; the separate publisher UAMI published the real hosted bootstrap binding |
| Endpoint configuration gate | Refused with `observed_hosted_definition_mismatch`; a fresh version read showed `status=failed` |

The first network rejection's underlying timing/registration cause was not
conclusively established. The successful authenticated reads do not retroactively
make that first request successful. Likewise, the second create's lost ACK did
not justify blindly creating another version.

Graph and RBAC assignments demonstrate configuration of the actual Agent
Identity, but **container token use was not observed**. They must not be reported
as a successful hosted call to a custom API or Key Vault.

#### Final associations and the runtime failure

| Artifact | Recorded value |
|---|---|
| Shared-reader source commit | `081762a2147ec21840112f3af56dc46775a230af` |
| Actual agent source-tree digest | `sha256:f13de22da6c7119380f1c6f9bcf33c8b3ddfa96ca7a6ff60164dcae756b8d42f` |
| Hosted agent version | `returns-hosted-reference:1` |
| Actual hosted image digest | `sha256:b5affd8ee4deaa1971fdbb129057e9d80bb899d7ca5ddcf6a0bdc41e42044e78` |
| Bootstrap policy | `returns-hosted-v1`, version `1`; digest `sha256:7b021ac19246ff8e44dc69f88257e0f85452e4c7bad67affdedfc9fd4636084d` |
| Final gateway policy | `returns-hosted-v1`, version `2`; digest `sha256:32d5c44d43d4bb1d89ec347aa56ac1e743ea4a65fcc33f67f02383a271b24994` |
| Staged gateway source digest | `sha256:b29235f3652c547694423aa631e3685bd185492cf1ade21d260c0fadbaa28222` |
| Actual gateway image digest | `sha256:4489b09ed56e2311129e5cd44ff7919a034a3054ef7a25e659e7431e8338ff67` |
| Signed hosted reference | `returns-hosted-s2-20260911` |
| Signed hosted binding expiry | `2026-09-12T13:06:05.433720+00:00` |

The later native version response contained:

```json
{
  "status": "failed",
  "error": {
    "code": "ProvisioningError",
    "message": "Agent version provisioning failed. Please retry. For troubleshooting, see https://aka.ms/hostedagents/tsg/provisioning"
  }
}
```

The endpoint validator was not weakened to accept a failed version. Image,
environment, CPU/memory and the typed `protocol_versions` still matched; the
version's failed status was sufficient to block configuration. The response
also retained an unmodeled `container_protocol_versions: []` beside the typed
protocol declaration. This observation was retained, but was **not** established
as the provisioning cause and did not justify patching the SDK or changing the
protocol contract speculatively.

All three S2 service health endpoints subsequently returned 200, including
gateway per-binding readiness for the final digest and its real receipt/approval
dependencies. S2 control had initially returned 503; it recovered after the
observed hosted workload was added and its configured initialization/request
window increased from 5 to 15 seconds. Those changes were not isolated in a
controlled experiment, so the original startup cause is not claimed as proven.

These healthy services and the signed binding do **not** prove that the failed
hosted container started, loaded the binding, minted credentials or invoked a
tool. No S2 business invocation is credited.

#### Bounded diagnostics and evidence of non-execution

The operator checked the concrete prerequisites named by the platform's
[provisioning troubleshooting link](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent#troubleshooting):

| Check | Observation / limit |
|---|---|
| Private-registry eligibility | The current first-party page says projects created after June 25, 2026 support private registries; this project was created September 11. The older blanket restriction in a cached companion reference was not treated as current evidence. |
| Registry permission mode | `LegacyRegistryPermissions`; project managed identity has registry-scoped `AcrPull` |
| ARM-token authentication | Dedicated `az acr config authentication-as-arm show` returned `enabled` |
| Registry network setting | Public access remained disabled; it was not opened to work around an unproven diagnosis |
| Native session diagnostics | Authenticated GET of the documented `.../endpoint/sessions` API returned `data=[]`, `has_more=false`; no session ID existed from which to fetch container session logs |
| Diagnostic CLI | Installed/used the agents extension in an isolated azd configuration. Its session command required a separate azd login; this diagnostic-tool authentication failure was not treated as the cause of the hosted failure. Existing authenticated SDK credentials successfully performed the session read. |
| Current version | Repeated authenticated reads still reported `failed`; no endpoint-check or business-run success was substituted |

At `2026-09-11T13:42:24.681282+00:00`, independent Cosmos queries found:

| S2 store / query | Count |
|---|---:|
| `s2-governance-records` documents | 0 |
| `s2-gateway-idempotency` documents | 0 |
| `s2-returns-cases` documents with `kind=decision-audit` | 0 |

The last row counts business audits, **not the three seeded cases**. This is
consistent with registration and policy publication but no hosted business
execution. No approval, noop or S1 receipt is borrowed to fill the gap.

| Private retained artifact | SHA-256 |
|---|---|
| `s2-reconciled-versions.json` | `ea35caf962cc5db4aa2b0c018a9f055c855ce6f106ba51f8e1e826459637c278` |
| `s2-signed-hosted-binding.json` | `d9fab81ee482fd894b84ad211aa9c8d1fe57fc9639ee63c11ded64a20bbd2c3f` |
| `s2-final-signed-policy.json` | `a92123e24987c2491aa87ef36cd63edff07f2fe3f7af84f21c1d836f252594b0` |
| `s2-failed-version.json` | `f933161f7d2f2e3c7e3ac34853998a14316da7d50a9bdaed40073c6eb28729cc` |
| `s2-store-observation.json` | `b234c8211892f043d901b90cb6f139d4ff3839fb7f549c6975ab97b068cf1c68` |

The provisioning root cause remains unresolved. A further attempt must preserve
version 1, both create-attempt records, all roles/resources and this failed
association. If a new version/image/reference is used, it requires a new exact
observation and signed association; the existing binding is not overwritten or
reused as if it covered that version. Neither a prompt agent nor a VM is silently
substituted for S2.

### Comparison with the user's working hosted deployment

The user identified a separately tested Hosted/Toolbox deployment in
`aiappsgbb/awesome-gbb`. Its current source at comparison time was commit
`06400fd7`; its maintained validation record reports fresh successful Hosted
identity output on September 11. That repository/worktree and its Azure
environment were inspected read-only, not modified.

The comparison exposed two distinct omissions rather than proving that private
hosted agents are unsupported:

| Difference / comparison | Evidence and action |
|---|---|
| Basic project capability host | The working private setup explicitly added an `Agents` Basic project host with platform-managed stores. Our S2 project returned `capabilityHosts: []`. |
| Conflicting guidance | The generic `foundry-hosted-agents` warning against manually creating Capability Hosts was applied without reconciling it with `foundry-vnet-deploy`'s Basic **project** host module. This was an operator interpretation/preflight error as well as an instruction conflict. |
| Minimal setup correction | Deployed the exact working `add-project-capability-host.bicep` module against S2 only. The missing-host check failed before creation and passed after the host reported `Agents`/`Succeeded`. No existing host, account or store was recreated. |
| Result of that correction alone | Version 1 remained failed. A controlled version 2 using unchanged application/runtime code and SDKs, with a fresh binding reference and policy version 3, also failed with the same `ProvisioningError`. The missing project host was therefore not established as the sole provisioning cause. |
| Protocol/image shape | The working service readback also contains `container_protocol_versions: []`, typed `protocol_versions` with Responses `2.0.0`, and nested `container_configuration.image`. Those fields were not changed speculatively. |
| Container identity | The working image uses the default container identity. The generated S2 image likewise has no fixed non-root UID; the other session's historical UID permission failure is a different signature. |
| SDK cohort | The working deployment uses a different tested runtime cohort. S2's existing exact runtime pins were retained while investigating the creation contract; no blanket SDK upgrade/downgrade was performed. |
| Registration metadata | The working deployed version has `metadata.enableVnextExperience: "true"`; both of our failed versions were created without it. |

The other session did **not** retain an outbound azd HTTP request body. Its
YAML is a creation input and its saved JSON is service readback, not proof of
every field azd sent. The missing metadata was independently traced to the
public azd implementation:
[`service_target_agent.go`](https://github.com/Azure/azure-dev/blob/main/cli/azd/extensions/azure.ai.agents/internal/project/service_target_agent.go)
unconditionally calls `applyAgentMetadata(request)`, whose comment identifies
`enableVnextExperience` as a **server-side API contract**, then sets its value
to the string `"true"`. The call is not conditional on a Toolbox.

The direct SDK operator did not match that contract. Its actual SDK
serialization regression first failed because the outgoing JSON had no
`metadata` object. The helper now supplies the same metadata through the
SDK's supported `create_version` argument, and independent observation rejects
missing, false or non-string values. It does not patch the installed SDK,
loosen the endpoint validator or mark a failed version active.

Seven targeted RED failures covered emission, lost-ACK behavior, missing/changed
metadata and the declared operator SDK extra. After correction, the complete
two-file lifecycle/credential selection ran **97 passing tests** with the real
pinned SDK and explicit external HTTP fixtures. These are local contract checks,
not a successful hosted business run.

The corrected enrollment created actual version `3`, with
`metadata.enableVnextExperience: "true"` independently read back. Its image was
`sha256:65698a5c959915ccd3de6e9a32ff6bab131a3f3b9abbf34ee91a71d6097740d1`;
the new reference was `returns-hosted-s2-native-20260911`, and its final policy
version `4` had digest
`sha256:b88353e1c02f2912bfb2937ba5dc8dcf516f3a23b8bcadabf3634326fc2d05b9`.
The old images, versions and bindings were retained.

**This did not establish a working hosted runtime.** A direct version GET
transitioned from `creating` to `failed` at `16:03:23.823606` UTC with the same
generic `ProvisioningError`. The missing creation metadata was a confirmed
contract divergence, but its correction was not sufficient to resolve the
observed failure.

A further diagnostic found a material service-read discrepancy: three raw
authenticated LIST `/versions` responses at 16:11:54, 16:11:59 and 16:12:04
reported all versions `active`, while the raw authenticated GET of version 3
still returned `failed` with `ProvisioningError`. These were response-body
values, not SDK defaults. The native CLI polls the direct version GET and
requires repeated terminal observations. LIST is not accepted as readiness
evidence here. Instrumenting every endpoint guard showed that all configuration
checks passed except the direct version status; that guard was not bypassed.

The actual generated version-3 image was also started in a network-disabled
local container, using the observed configuration values solely as test inputs.
It served `/liveness` with `alive` and correctly returned 503 for readiness with
no reachable authority. This excludes an immediate import/listen failure in
that local setup; it does not reproduce platform credential injection, image
pulling or hosted activation, and is not identity or business proof.

The remaining comparison includes the actual private image-pull route, image
reference handling and the difference between an ordinary protocol host and
this externally authorized bootstrap gate. Those are hypotheses to investigate,
not grounds to relax authentication, expose the registry, swap SDK cohorts or
declare provisioning successful.

### September 13: complete canonical private startup control

The published canonical baseline is awesome-gbb commit
`2ef44f6b47803a0166956cc668e5f429c1c1f8cb`
([aiappsgbb/awesome-gbb#489](https://github.com/aiappsgbb/awesome-gbb/pull/489)).
Its preflight/publication checks did not certify this private environment.
The separate reviewed bootstrap-deferral candidate `067d7e` is unpublished;
it was not needed for this control, which has **no governance bootstrap and
no remote/business tools**.

The complete canonical `Dockerfile`, `container.py`, `pyproject.toml` and
`azure.yaml` were used, not just a replacement entrypoint on the governed image.
The only manifest adaptations were the diagnostic agent's name and the documented
private build transport: `docker.remoteBuild: false`, `platform: linux/amd64`.
Ordinary native `azd deploy` built through the preserved VM's Docker daemon over
an owner-only, strictly verified SSH Unix socket and pushed to the private ACR.
No public Docker listener, shared Docker context change or SDK create override
was used. The earlier `--from-package` attempt with an image digest failed during
local Docker tagging, before registration; that failure is not a Foundry verdict
on digest references. A local keychain-helper stall was also resolved before the
complete native deployment.

Actual image inspection, imports and network-disabled native startup confirmed
the canonical Agent Server cohort `2.1.0b1 / 2.1.0b1 / 1.1.0b1`, alongside
MAF core `1.14.0`, Foundry `1.11.0` and hosting `1.0.0b260813`.
The local `/readiness` response was 200. This is a local-candidate observation,
not a hosted session or its mounted home. Global Threadlight pins were unchanged.
The final tags emitted by both ordinary azd deployments independently resolved
to the same image:
`sha256:53e02ab62b0be348717de32f4df755aef919f1eb90019374cbd1e23602a7cd5d`.

| Controlled observation | Actual result and limit |
|---|---|
| Canonical startup control, version 1 | Native azd registered the version with `enableVnextExperience: "true"`, then failed after 2m55 with `ProvisioningError`; request `48d5f42c591742f4aaa99f159f08cbd6`. Direct version GET confirmed failure. |
| Exact Basic-private account difference | The canonical `ai-account-identity.bicep` sets `networkAcls.bypass=AzureServices`; this account had `None`. A conditional PATCH changed only that field. GET subsequently confirmed `Succeeded`, with public network access still disabled, local authentication still disabled, and the same account identity and injection subnet. |
| Meaning of the network change | This enables the template's trusted-Azure-services network exception on the Foundry account, not data-plane permission. Neither the account nor ACR had public access enabled. ACR already had its separate trusted-service exception. No role, caller allowlist or registry access mode changed. |
| Same-image control, version 2 | After fresh setup observations and the actual account change, ordinary azd registered version 2 with the same image content and agent identity. It failed after 2m54; request `22916e9c9d55fd30d5bf313d9c8dd891`. Thus this alignment was **not sufficient to resolve provisioning**. |
| Pull versus publication | ACR metrics during 08:26-08:29 showed successful token exchanges, manifest reads and 67 successful `GetBlob` operations. These registry metrics do not identify the caller; they are not yet an independently attributed project-MI platform-pull receipt. Publishing from the VM is also not such a receipt. |
| Session versus registration | A diagnostic native invocation of failed control version 1 returned HTTP 409 `agent_version_failed` during session creation, request `6ae2b83c0ffb14e0066aa79c36a44e76`. The successful session-list response was empty. No model response or runtime log was fabricated from this failure. |
| Native diagnostic limitation | The failed deployment did not persist the agent name into azd's local environment: `show`/`monitor` reported name-resolution failure, and `doctor` called the registered agent undeployed. Direct authenticated version GET, not that local cache or LIST `active`, supplies the recorded terminal state. Native monitor additionally requires a real session ID; none was created. |
| Retained infrastructure diagnostics | Enabled only registry login/repository diagnostics into the existing Log Analytics workspace. No historical events can be inferred from newly enabled logs, and no event is credited until actually retrieved. |

The working Hosted/Toolbox comparator was also clarified: its Foundry account
and authenticated ACR had public network access enabled, while its agent
injection and MCP path were private. It is therefore **not an equivalent
fully private account/image-pull experiment**. Its build used native ACR remote
build; no retained image manifest established a Docker-versus-OCI difference.
The control here has an OCI manifest with gzip layers; format alone is not a
demonstrated failure cause.

Both index-derived CLI state directories and the exact tenant/subscription
assertions were used for this experiment. A diagnostic azd command outside the
project initially selected a home-tenant token despite the isolated cache.
Running it in the complete project's context corrected that diagnostic 403;
no login cache was copied and no role was granted to the wrong principal.
The actual deployments already used the complete project context. This
diagnostic correction does not explain their provisioning failure.

The result rules out the claim that our governance bootstrap or our different
Agent Server cohort is the sole explanation: the complete canonical control
fails without either. It does not yet distinguish platform snapshot preparation
from another private provisioning dependency. The supported session diagnostic
cannot expose runtime logs before a session exists.
**No hosted business result or native session-home success is claimed.**
S1's earlier authority has expired; its preserved successful receipts remain
historical proof, not a current authorization lease.

| Private retained artifact | SHA-256 |
|---|---|
| `canonical-full-startup-control/native-direct-readback.json` | `a3240dd1d086825e91e518ba22fc6f2f8465ae40f00d9cc35320260ce06ba034` |
| `canonical-bypass-attempt-0913/direct-version-2.json` | `318b349171df38f040abfca1237d8c6a8d4e9e83f8883c3f9a86f979343b119b` |
| `canonical-bypass-attempt-0913/acr-transactions.json` | `fb95ab856bd2e5ed3397a3ea18ab62c39ab788bce0cfeb70a272f43bb1751b99` |
| `canonical-pull-diagnostics-0913/account-alignment-poll.json` | `6e73e95de0030064310fec4e5f2790d8bf2c072fbf4f25fc6eff0398a6a893e4` |

The private capture also retains both native deploy logs, fresh preflight inputs
and results, registry manifests, the actual account PATCH and its before/after
states, and the failed diagnostic request. No old artifact, failed version,
signed association, image, identity or resource was removed. The remaining
manual work is private operational evidence collection and the unresolved hosted
provisioning diagnosis, followed by a fresh exact business binding and real
hosted invocation. Repeated registration without a justified delta is not a
reproduction procedure.

### Instrumented retry: project-identity registry access observed

After registry diagnostic ingestion was verified with real operator events,
the user authorized **one instrumentation-only repeat**, not a proposed fix.
The prior failed version was reconciled first. Ordinary azd deployment ran from
`2026-09-13T08:38:38Z` to `08:41:44Z`, registering control version `3`.
The image digest, application, dependencies, model, identity, network and roles
were unchanged. The direct GET again returned `failed`/`ProvisioningError`;
native request ID: `aa794464706678e9b2d477a30886bedb`.

Actual registry events now separate three callers:

| Caller | Independently observed registry activity |
|---|---|
| Operator publisher | VM-addressed Docker `Push` HTTP 201 at `08:38:47.293482`, with the new native tag and the same frozen digest |
| Container security scanner | A different identity with `MDCContainersSecurity/1.0` performed HTTP 200 pulls; these are explicitly excluded from Foundry acquisition proof |
| Foundry project managed identity | At least six paired `Login`/`Pull` HTTP 200 events, `08:39:16.298236` through `08:41:15.581495`, for the exact native tag/repository/digest; caller identity matches the ARM project's managed identity, UserAgent is `Buildah/1.42.1`, and each source address belongs to the selected injected subnet |

This is an attributed service-side registry observation, unlike the previous
aggregate metrics or VM publication. It demonstrates that the project's actual
acquisition component reaches and authenticates to the private registry and
obtains the registered image manifest. It is **not proof of successful layer
unpacking**, complete image preparation, snapshot creation or application startup.
The same-window metrics include successful `GetManifest` and `GetBlob`
operations, but do not provide **per-caller blob completion**. No stronger claim
is inferred by joining aggregate counters to a single identity.

Remote manifest bytes matched the frozen digest. The separately downloaded
config blob also matched its descriptor hash and declared Linux/amd64, `/app`
working directory and the canonical CMD. There was no image index, and the
referrer-list response was empty; no unobserved provenance/attestation artifact
is assumed. A successful local Docker start remains different from the
platform's Buildah acquisition/preparation path.

A native post-attempt session list was empty. The only observed ARM failure
in the window was a separate diagnostics-policy deployment targeting a
**missing organization-managed Log Analytics workspace**. Its exact deployment
error was retained; it is not established as the hosted provisioning cause,
and neither that external scope nor the organization policy was changed.

The new private evidence is under
`canonical-instrumented-attempt-0913/registry-events-final.json`,
`direct-version.json`, `registration-summary.json`, `remote-config.json`,
`remote-manifest-before.json`, `acr-transactions.json` and
`diagnostic-policy-deployment-error.json`. A private minimal reproduction pack
contains the canonical source, compared configuration, timestamps, request IDs
and original evidence. It contains no CLI caches, registry tokens or SSH keys.
No support request or public issue was submitted. The remaining blocker is
the unexposed internal provisioning/snapshot error: **backend involvement is
suspected with evidence, not a proven backend defect**. No additional unchanged
private registration or private business execution followed on September 13.
The separately authorized public S3 below did not change S2 resources.

### September 14: one unchanged private transient-control retry

The user authorized **one** fresh morning diagnostic to test whether the
pre-session failure had cleared overnight. This used the existing canonical
startup control, with no business tools, new account or parallel environment.
Paired tenant isolation and explicit private subscription/resource-group/account/
project/model/registry assertions preceded native deployment. Public S3 was not
modified, and no expired business binding was reused.

Fresh account, project, model and direct capability-host reads matched the
previous selected configuration: private/keyless Foundry account, original
injection/subnet, existing successful Basic project host, private ACR, existing
project-MI pull permission and enabled registry diagnostics. The existing
account `networkAcls.bypass=AzureServices` remained unchanged. No new roles,
network opening or `SecurityControl=Ignore` tag was applied to the private group.
The bounded pre-attempt Activity Log read exposed no intervening relevant
configuration write; that is not proof of complete internal platform history.

The byte-exact canonical preflight helper from `2ef44f6b` returned
`READY_FOR_REGISTRATION` / `NOT_TESTED`, using freshly collected private-route
operator/model observations and a network-disabled local native startup check.
Those operator/local checks are **not hosted identity or session-home proof**.
The same canonical source, azd `1.27.0`, Docker engine and cached build produced
the same immutable candidate; no SDK or application change was introduced.

| Observation | September 14 result (UTC) |
|---|---|
| Single native deployment window | `08:12:19` to `08:15:13`; native exit 1 |
| Actual created version | version 4; original three versions preserved |
| Native deployment request ID | `3ccff821812154762ffa14ad7270f9a0` |
| Independent direct GET | `08:15:38.338868`; HTTP 200 with body `status=failed`, `ProvisioningError`; no deeper cause exposed |
| Actual native session inventory | Empty; no container-session logs or successful invocation available |
| Frozen definition comparison | Same CPU, memory, model environment, protocols, experience metadata and instance identity; only normal publication tag/version/time changed |
| Remote image bytes | New tag resolves to the **same raw manifest**, independently SHA-256 checked as `53e02ab62b0be348717de32f4df755aef919f1eb90019374cbd1e23602a7cd5d` |
| Registry acquisition | Project MI emitted **six Login and six Pull** events with `Buildah/1.42.1`, all 200, for the new tag and exact digest |
| Current attempt Activity Logs | No events returned in the bounded attempt window; no actionable native provisioning detail was exposed there |

The operator's image Push 201 at `08:12:32.284465` and the security scanner's
`MDCContainersSecurity/1.0` pulls were separated from the actual project-MI
platform pulls (`08:13:05.676538` through `08:14:56.843329`). Initial ingestion
showed only four pulls; the retained final query contains all six with distinct
correlations. Aggregate success or scanner activity was not used as a
substitute for caller attribution.

The actual remote descriptor is a single OCI image manifest for Linux/amd64,
not an image index. Its config digest remains
`sha256:69d18068699407b21972cb601c92ae8c7746837de1bd98d1359d3682271f0c80`.
The OCI manifest contains the same OCI and Docker gzip layer media types;
that observation is not established as the failure cause. Successful manifest
acquisition still does not establish per-caller blob completion, layer unpacking,
snapshot creation or startup of the hosted runtime.

**Result: no overnight recovery was observed for this frozen configuration.**
The repeat narrows the transient explanation but **does not prove a backend
defect**, nor exclude a persistent, still-unidentified configuration interaction.
There was no new business invocation, no second registration attempt and no
public/private configuration repair. Further attempts require a new observed
difference or a justified correction, not another unchanged retry. No external
support request was submitted.

The private reproduction directory `private-morning-0914/` retains the approved
experiment, canonical helper hash, fresh setup inputs/results, unchanged source
hashes, original native deploy log, direct version/session responses and registry
timeline. It contains operational targets and is **not committed as raw logs**.

| Retained artifact | SHA-256 |
|---|---|
| `private-morning-0914/outcome.json` | `5da4d0d6f7ee98659a33ebd252064f1a8bbf9f31d8817bbe7cb1cac9ed56ff4a` |
| `private-morning-0914/after/version.json` | `cec9f5409f4cb6088c4b8695d8c6329e230c79ddc695eaa376661332046d46de` |
| `private-morning-0914/registry-events-final.json` | `e9b32c9a134e6d787dff2dba1fd8f2e19fa7fb78168d93aee1c8ef4c006cb980` |
| `private-morning-0914/remote-created-manifest.json` | `b10fca642766de3db669ca5c07a0fb4220bc42dcd19d58953c1d41f83c72a3ef` |

## S3: Public authenticated Foundry hosted execution

**Executed September 13, 2026.** The user authorized a separate public,
authenticated vertical after S2's instrumented private provisioning failure.
This run reached a real **Foundry hosted agent -> native MAF model/tool call ->
governed MCP -> separately authenticated business API -> Cosmos transaction**.
It is a return decision/audit, **not financial settlement**, and is still the
bounded synthetic-case use case, not the full canonical returns application.

### Setup and actual identity

The complete four-file hosted baseline and paired tenant-isolation contract
came from `aiappsgbb/awesome-gbb` commit
`2ef44f6b47803a0166956cc668e5f429c1c1f8cb`. Native azd provisioning created a
separate Sweden Central resource group, public/keyless Foundry account/project,
public authenticated ACR with admin access off, and the same small
`gpt-4.1-mini` model deployment. No VNet injection or private endpoint was
created for S3. Hosted v1 returned `billing` from the canonical model example
with two direct active GETs and a real active session. That was baseline
host/model proof, not business proof.

The generated governance foundation added an ACA environment and three
externally reachable HTTPS services, separate control/gateway/downstream/business
identities, Blob signed catalog, Key Vault and Cosmos. Authentication remained
mandatory: three separate Entra API audiences, exact app-role and subject/client
checks, no agent database authority, no shared-key Cosmos/Blob fallback.
The actual Foundry **Agent Identity**, not the operator VM identity, called the
model, control/gateway and business read route. The gateway's distinct downstream
identity authorized the business POST; only the separate business writer owned
the decision transaction. The operator published policy using a real versioned,
non-exportable RSA-3072 Key Vault key. Actual sign/verify and tampered-digest
rejection were observed; no local/demo signer was implemented.

Organization policy synchronously changed newly declared public KV/Blob/Cosmos
resources to PNA Disabled. Activity Logs identified the three network Modify
rules under `MCAPSGovDeployPolicies`. After explicit user authorization, only
the **new S3 resource group** received `SecurityControl=Ignore`; the operator
then restored PNA on those three resources. The policy definitions supported
that tag exclusion; reading the management-group assignment itself returned
403 and is not claimed as verified. Cosmos converged after several minutes,
without a repeated forcing loop. Entra/RBAC, keyless data access and HTTPS
settings remained unchanged. The tag is not a catalog default, a general
production recommendation or evidence of private isolation.

`preserve=true`, `cleanup=disabled` and a `CanNotDelete` lock preserve the demo.
Old/private resources, failed hosted versions, images, grants and keys remain.
No hybrid network or scheduled cleanup was created.

### Frozen runtime and corrections actually needed

| Association | Observed value |
|---|---|
| Hosted agent / version | `governed-returns-public` / `5` |
| Agent image digest | `sha256:3e46f91052d3f22bcaa1897c394075caed767f54585be57725fa93b13096c69d` |
| Final policy | `returns-public-v1`, version `5` |
| Final policy digest | `sha256:c44746f2cafb96065cd9f18727e51f9774e469860c61bf08f196bc9359dafafb` |
| Signed bootstrap reference | `returns-public-s3-wire5-20260913` |
| Signed bootstrap expiry | `2026-09-14T10:26:48.991423+00:00` (12:26 Italy) |
| Final signed policy expiry | `2026-09-14T12:03:41.859581+00:00` (14:03 Italy) |
| Control image digest | `sha256:f6ac63620bf4ddd67129067d119f6d8848f3091f8116ed3a35ef394c198d282f` |
| Gateway image digest | `sha256:d2bec104be33824036f523b0098cfb3baca12076330904f21651d27f8277af1f` |
| Corrected read-audit business image | `sha256:986370c2c0eb52d5d6ce3edbb2cdbf419f8508b194db6c1cca52fdfdd96615ff` |

**Historical successful receipts are not current executable authorization.**
These are separate leases: the earlier bootstrap expiry cannot be extended by
the later policy expiry. Fresh signature, binding, identity, policy and action
checks remain necessary even before either timestamp. No lease, old reference
or human intent was renewed by retaining this evidence. Expiry revokes
authority, not the instruction to preserve the resources.

The MAF/core/foundry/hosting versions remained `1.14.0` / `1.11.0` /
`1.0.0b260813`; the canonical hosted cohort used Agent Server core/responses
`2.1.0b1` and invocations `1.1.0b1`, Projects `2.3.0`, MCP `1.29.1`,
HTTPcore `1.0.9`, AGT `5.0.0`, ACS `0.3.1b0`, Hooks `0.1.0a5`, OPA `1.18.2`.
The Agent Server trio was explicitly restored during S3 packaging, not changed
globally. The later hash-checked `hosted_cohort` generator option packages that
selection; it was not retroactively used to generate the already-running v5.

| Observed failure | Correction / evidence boundary |
|---|---|
| Guided init overlapped source and destination | Native init stopped locally; documented greenfield YAML plus ejected infrastructure used instead |
| Operator agents/read 403 before first registration | Verified project-scoped operator roles, added canonical project manager and account Foundry User to the operator only, then verified authenticated empty inventory. Role propagation versus each addition was not isolated |
| Bare `image:` still caused rebuild and missing `PYTHON_IMAGE` | Native `docker.imagePassthrough: true`, `remoteBuild: false`, verified session-local azd `1.34.0`; ordinary deploy registered the frozen image |
| Selected authority unavailable before platform identity/binding exists | Explicit nonproduction `selected-tools` bootstrap starts native readiness first; selected dispatch still activates signed authority and authenticated exact tool discovery |
| Backend lacked `runtime` import | Packaged full governed source closure, not control-plane-only image |
| Duplicate names for identical existing key-verify grant | Private deployment source adopted the existing grant name; no grant removed |
| Mounted configuration loaded only at startup | Nonsecret configuration version marker forces a new ACA revision; not called a cryptographic hash |
| MCP transport rejected SDK-added headers | Native console isolated `headers:baggage,traceparent`. v5 accepts only exact active SDK W3C trace/baggage values; authorization, idempotency, body/target and post-wait checks remain strict |
| First read-audit implementation used a two-operation adapter for one create | Real execution caught the unpack error. Separate create-only `append_audit` added; original business replace/create path unchanged |

No installed SDK was patched and live telemetry was not disabled. Earlier v2-v4
images, bindings and failures remain preserved; each new version received a new
observed, signed association. Local generation/readiness alone was not promoted
to business success.

### Executed cases and independent observations

| ID | Actual native execution | Independent result |
|---|---|---|
| S3-HOSTED-ALLOW | Model read `S3-RMA-ALLOW`, then called `returns_apply_decision` | Case closed; one real decision-audit; completed gateway operation; central allow receipt |
| S3-HOSTED-DENY | Model attempted ineligible `approve_refund` with the exact quoted Cosmos ETag | Central `policy_deny`, case unchanged, no business audit for that case |
| S3-PENDING-APPROVAL | Model requested `escalate_to_supervisor` for the high-value/risk case | Durable gateway `awaiting_approval` and central pending intent; no grant or second write |
| S3-AUDITED-READ | Model called the unbound read with audit enabled | Cosmos `case-read` persisted before data returned; `read_audit_id` independently matched |
| S3-EXPIRED-RESUME | Original hosted session attempted exact original args and operation selector after expiry | Generic `Error: Function failed.`; unchanged expired intent, grant null, only the original business audit |

The successful allow is response
`caresp_0cea8b0368866d9000tAtCuaJxDaI4Jk1c38d9LmI3u2LR4lVn`,
write call `call_0ZFgn4cJEsACJnEqCvmCsxA0`, central receipt
`23d32ac944364eb89ff502a5e86a1b6b` at `12:07:33.589815` UTC, and business audit
`decision-24b13a1968a7cbc1a0d4105b30c968bb3a52b437d51f56411aba13c7b3ae1cda`.
Independent reads matched exact arguments/result and completed gateway
correlation `22f86900074410db850c7970d905cd83f201f41d54d602b638f5cbd24abdf135`.

The clean domain-denial response is
`caresp_09249b81651d818300CPswXOk1xr7qiQNS3ROmdwFXjhQj1bTq`,
write call `call_5BZPMqg6lEBZ0T1KWt1eQTgD`, receipt
`53c7b11c83a4436b94732ad1517d8ebe`. Two earlier attempts are **distinct**:
the first model altered the ETag and retried (two denials), then another stripped
the quote characters (one denial). Those are invalid-revision evidence, not
isolated eligibility proof. Explicit escaped JSON finally preserved the real
ETag and produced the clean ineligible-case denial. Model compliance is not
assumed merely from instructions.

The pending response is
`caresp_0ed0f5b7175de32e00z0j3ztdD8slhUxWBXEoZZZhRu77G3l3D`,
call `call_mCwVgzARxGyQohl4G6XB2Y6G`. Its operation
`eb17031249d54551b4d258ca1e3b870b` and nonce
`7c7344acc8d94610ad497729a66760eb` expired at
`2026-09-13T13:10:42.782214+00:00`. The later response
`caresp_05147fdd1da9feb300NWAHoLTIa5wV99DUXzRgoHuedQpzQkhZ`
used the original session and exact arguments plus that operation selector.
Gateway source raises `approval_expired` on an unchanged expired intent, but
the native output exposed only a generic FunctionTool failure: the **particular
guard that caused that returned failure is not independently proven**.
No new central receipt was emitted. Pending/awaiting states are retained expired
tombstones, not renewed authority or a human rejection.

The audited read response is
`caresp_05147fdd1da9feb3001Lqmcsq7QfL0zgoeLQQnl5f7go0DPVai`,
call `call_4RwAqklcaShPLNvEc1TsJq8e`,
audit `read-c6926d0a9cc04a3a9944f6a0d04ff0a3`.
It is `backend-acknowledged-before-return`, remains unbound to ACS, and records
actor/client, case revision, deployment and result hash without a raw case body.

### Durable reconciliation and human boundary

The reusable authenticated collector retrieved **8 native responses / 14 tool
calls**, verified the expected signed binding and native identity/image, joined
independent stores, and created/read back **14/14** minimized call records.
The original 13 records were unchanged when the expired attempt was added.
This is **post-run** reconciliation of that selected response set, not universal
agent attestation or proof that every possible model/provider operation is
intercepted. Historical reads without inline audit are labeled post-run, not
retroactively credited with backend ACK. Identical earlier retry calls can share
an action-hash/time-window receipt set; no exact one-to-one receipt is invented.

**Human approve/reject and successful hosted resume remain unproved.**
The reviewer was unavailable; no token, confirmation or grant was simulated.
S1's human approval is not transferred to S3. A future positive test requires
a fresh request, real delegated login/decision and exact resume; it must not
silently renew this expired operation. Successful post-approval replay in S3 is
also unproved.

The native Office 365 connection and notification-only Logic App exist.
The workflow is **Disabled**, SAS authentication **Disabled**, and exact
Entra issuer/audience/operator OID policies were independently read back.
**Office 365 consent is missing**: the connection's Enabled flag coexists with
Error/Unauthenticated/token and an empty authenticated user. No workflow was
invoked and no email was sent. Email/reply/click cannot issue a grant; only the
existing delegated Task8 review protocol can record the human decision.
No Graph application mail permission, OBO or Teams workflow is claimed.

### Retained evidence and reproducibility

Private evidence is under `public-governance-wire5-0913/`; paths below are relative
to it. These hashes identify retained captures, not deployable authority for a
different environment.

| Artifact | SHA-256 |
|---|---|
| `signed-hosted-binding.json` | `266c032c6f9a38254837c6ac10f0819d5002fb2e9c48d4255492fab34201987a` |
| `final-signed-policy.json` | `3055a4a226ec3c67a5d6523ad96c60c7c9c501e8e27bf4468f6ed9c96a518fa7` |
| `evidence/live-allow-proof.json` | `49e8f8d101dac9d1b7554b84c066ab009b8adadb56dafc9d81315e36f9d71f50` |
| `evidence/deny-quoted-response.json` | `e393c0462b83df8c56aeef484ac570a5afa28b220dbf0c74dd850e2cdeb8bee8` |
| `evidence/live-deny-pending-proof.json` | `1b0c9a03c045c73b85f4977ac83aa570991af1a8f20dc608aaf215d05ec6f35e` |
| `evidence/live-expired-resume-proof.json` | `d08f3d70df096fdc326d252a5a5e8beef8e26c1a1bed7987a79b577b9fa2faac` |
| `evidence/reconciliation-expiry-verified/reconciliation.json` | `967ca2b3103d84980a08a1ec319b2549457daab08392adb1c98eb7943a04215a` |
| `evidence/notification-observed.json` | `e7ada9b4a223340d9be409c2c48b60461494cd24d6d39d2bbf0295fff1d8b184` |
| `evidence/office365-connection-observed.json` | `780e2222477fc477d3ef6101f126b967361afdb50034b69fe87abd4744e5ba79` |

The [returns MCP runbook](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md)
packages source materialization, canonical hosted cohort selection, native image
passthrough, selected-tool startup, read audit, collector and disabled notification
module. Commit `2cbd77e` contains the public preservation/native transport startup
delta; the read-audit/collector/cohort/report delta follows it locally.
No release or Threadlight PR is implied by this record.

Still operator-owned: concrete infrastructure composition, Entra apps/consent and
scoped roles, case seeding, real key/policy publication, immutable image/version
observation, signed association, deployment parameters and actual human login.
Existing private operator scripts record those steps but are not a general
single-command product. S3's manually adopted existing key-grant name and
organization-specific tag are not portable defaults.

Targeted checks exercised the actual native telemetry/transport boundary
(36 passing Linux amd64 cases), preservation/passthrough (30 passing selected
cases, one native generation case deselected), and corrected read-audit/Cosmos
boundary (19 passing native cases, three unrelated deselected). Earlier native
skips without the required runtime flag are not counted as passed. The collector
also executed against live stores as described above. These checks do not
replace CTK/LOCAL-14/Task15 or establish production readiness.

The final packaging delta additionally passed 30 selected local tests
(one native test deliberately excluded) and that one create-only Cosmos test
separately in the retained Linux amd64 runtime. Local RED runs first exposed
the missing expiry association, cross-partition audit/operation joins, report
claims and native-runner wiring; those were corrected. The final scoped collector
reproduced all 14 retained live records byte-for-byte offline without another
model invocation or Azure write. The full deployment runner is wired to the
new native case but was not rerun wholesale.

## S4: Prompt-agent applicability

**Assessment only:** a prompt agent is not a drop-in replacement for this MAF
container. It does not execute this repository's Python `GovernedMCPTools`,
`GatewayAuthority`, local read FunctionTool or Responses host inside a container
owned by this implementation.

An external MCP PEP could still be useful to a prompt-agent architecture. Whether
the complete **same contract** is realizable must be demonstrated separately:

| Required behavior in S1/S2 | Question for a prompt-agent scenario |
|---|---|
| Refresh app-only credentials on initialize/list/call and enforce exact caller identity | Which platform identity/authentication mechanism reaches this gateway, with which actual token audience and roles? |
| Per-operation `Idempotency-Key` supplied by trusted client code | What trusted platform/client mechanism generates, retains and reuses it for the exact action? Static copied headers are not equivalent. |
| Deferred operation selector removed before business arguments are sent | Where does the current MAF-specific `governance_operation_id` adaptation live? |
| Same identity/arguments/policy/facts on resume; authentic one-use grant | Can the prompt-agent path resume that exact operation rather than issue a new unrelated call? |
| Unbound case read implemented as a local MAF FunctionTool | Which separately authorized read tool replaces it, without accidentally binding or dropping the read? |
| Declared tool inventory and no alternate business effects | Which other platform/provider tools exist, and can any bypass the selected external boundary? |

These are integration/evidence requirements, **not a claim that prompt agents
can never support governed external actions**. No prompt agent was created and
no prompt-agent enforcement, approval or identity behavior was tested here.
S4 must not delay or dilute the working S3 hosted reference.
