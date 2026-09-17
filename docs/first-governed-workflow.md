# Your first governed workflow

Build a returns assistant that can read a case, record an allowed recommendation,
and ask a real person in Outlook before recording a supervisor handoff.
The useful outcome is **a business decision with a traceable authorization and
audit**, not payment settlement.

Coming from [Agent governance](agent-governance.html)? Start here, then use the
linked engineering runbooks when you reach an implementation handoff. You do not
need to learn the entire skill catalog first. You do need a platform owner
before connecting Azure services.

## One example, one supported profile

This guide uses **MAF / Foundry hosted / Responses + governed MCP + Cosmos
decision/audit + native Outlook approval**. In the foundation, that means
`microsoft-agent-framework`, `runtime_shape=agent`, `responses`, and
`governed-tool-gateway`. Here, *workflow* means the business journey:
this is **not a deterministic MAF workflow**. The supplied agent host has the
same-session deferred-review seam; do not assume any arbitrary workflow has it.
See [runtime support](runtime-support.md).

The [two-tool reference](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md)
exposes only:

| Tool | What it does | Boundary |
|---|---|---|
| `returns_get_case` | Reads an authorized synthetic case and its current revision | Unbound to ACS; backend authentication and data access still apply |
| `returns_apply_decision` | Records a recommendation or supervisor handoff with a Cosmos decision/audit transaction | Selected gateway authorization before the independent business API writes |

`approve_refund` records a recommendation; it does not send money. The richer
[canonical returns example](../examples/returns-triage-governed/README.md)
also needs OMS/CRM data, policy evidence and additional tools. Do not copy that
application's four-skill business process into this smaller example or reuse the
two-tool reference's historical receipts to claim the canonical binding works.

**Two agents:** Copilot is your **construction agent**: it reads Threadlight
skills to help design, implement and check the project. The **business agent**
is the application you construct: model, host instructions, any domain skills,
registered tools and runtime. Installing a construction skill does not give the
business agent a tool, permission or enforcement. The two-tool reference supplies
its own instructions and executable tools; additional domain Markdown must be
deliberately loaded by the host. See [the two libraries](skill-based-agents.md#two-agents-and-two-libraries).

**How to use the prompts:** these are instruction examples, not guaranteed
automation or new slash commands. Work in a separate pilot repository with
Copilot and a reviewed complete catalog available; do not generate a customer
application inside this catalog. Refer to [installation](../README.md#install)
instead of copying individual scripts without their dependencies. Use one
reviewed revision consistently. Copilot usage/billing still applies when the
project steps are offline.

## Phase 1: Describe the decision you want to improve

**Cost and safety:** local authoring only; no Azure or business-system calls.
Use synthetic cases, not customer records.

**Goal:** agree on a small, useful business outcome before generating a host.

**Skills:** [threadlight-design](../skills/threadlight-design/SKILL.md), Full mode
with the reviewed SPEC checkpoint.

**Inputs:** a process owner, the proposed read/write, an ordinary eligible case,
an ineligible case, and a high-value or high-risk case needing supervisor review.
The reference treats amounts above 500 or known high risk as supervisor-only.
That is a sample rule, not a recommended business threshold: the owner must
accept it for synthetic rehearsal or commission a separately reviewed change.

**Copilot prompt:**

```text
Use threadlight-design in Full mode for a small returns decision assistant.
Use the two-tool returns MCP reference: returns_get_case and
returns_apply_decision. Record recommendations and supervisor handoffs,
never payments. Use synthetic data and capture the owner's acceptance rules,
reviewer, success measure and unresolved prerequisites.
Select microsoft-agent-framework, runtime_shape=agent, protocol=responses,
Foundry hosted and governed-tool-gateway. Resolve the actual capability
signals through the catalog runtime policy; do not force a routing override.
Stop at the foundation/SPEC review checkpoint. No Azure discovery, deployment,
tool calls against business systems or optional presentation work.
```

**Assistant:** interviews you, records the foundation and SPEC, and distinguishes
read access from the selected consequential write. It identifies missing
decisions rather than inventing permissions, a reviewer or financial authority.

**Human:** the process owner approves the synthetic rules, escalation behavior
and a measurable outcome, such as correctly recorded dispositions on the agreed
case set. Review the SPEC before continuing.

**Platform owner:** identifies who can later provide the approved model,
environment and identity integration. No platform change is needed yet.

**Result:** reviewed `specs/foundation.md`, `specs/SPEC.md` and
`specs/manifest.json` at the Phase A checkpoint, with explicit open prerequisites.
SPEC section 8 names the actions; section 14 records the value baseline, target,
owner, timeframe and measurement source.

**Verify:** explain each of the three synthetic cases back to the process owner.
The selected framework/protocol must agree with the
[runtime policy](../skills/threadlight-design/references/runtime-policy.json);
an unresolved capability is not silently false.

**If blocked:** if Design defaults to another runtime or the intended effect
becomes a real payment, stop and revisit
[Foundation](../skills/threadlight-design/SKILL.md#step-0-foundation-from-scratch-path-only).
Do not rename an unsupported application into this profile.

## Phase 2: Prepare the people, inputs and access handoff

**Cost and safety:** offline preparation. Reading actual Azure configuration,
signing in, consenting or provisioning is a separate authorized operation.
Do not begin those operations just to fill in this table.

**Goal:** know what you can do locally and what must come from a platform owner.

**Skills:** [threadlight-design](../skills/threadlight-design/SKILL.md) for the
approved continuation; the companion
[azure-tenant-isolation](https://github.com/aiappsgbb/awesome-gbb/blob/2ef44f6b47803a0166956cc668e5f429c1c1f8cb/skills/azure-tenant-isolation/SKILL.md)
contract for later Azure access.

**Inputs:** the approved SPEC and the following ownership decisions. Platform
resources may already exist; this is not a request to recreate them.

| Needed when | Minimum handoff |
|---|---|
| Local construction | Copilot with repository/shell access, reviewed catalog, Git and an isolated Python 3.12+ environment for the governed reference. Native packaging uses Linux amd64; another host needs the documented container route. A thin Codespace is not automatically an Azure deployment workstation. |
| Model rehearsal | One approved model deployment and authorized inference access, plus a small call budget; otherwise remain offline |
| Hosted preparation | Foundry project, model, registry/project connection and operator workstation or runner with the required reachability, approved Azure CLI/azd versions and deployment authority |
| Governance services | Separate control-plane, gateway and business API services; versioned Key Vault signing key, signed-policy Blob storage, single-write-region Cosmos, scoped identities and service app roles |
| Human review | Existing authorized Office 365 connection, real available reviewer, approved responder-to-role mapping and an owner for the Logic App and its sensitive run history |
| Evidence and operations | Independent observer access, approved retention location, budget, recovery owner and explicit resource-preservation/cleanup scope |

**Copilot prompt:**

```text
Use threadlight-design to continue the approved SPEC into the minimum
two-tool application instructions, synthetic inputs and tests. Reuse the
existing executable reference; do not add OMS/CRM, a new UI or extra tools.
Prepare the missing-prerequisite handoff for the platform owner.
Read azure-tenant-isolation for the selected personal tenant index entry and
paired az/azd isolation requirements. Do not read token caches, sign in,
grant access, consent, provision or select a default subscription for me.
Separate local work I can do now from work awaiting an authorized operator.
```

**Assistant:** prepares application instructions and tests derived from the
SPEC, and records missing prerequisites in the existing SPEC. It does not
supply permissions by generating files.

**Human:** identifies the intended tenant/index entry, subscription and business
reviewer. The actual mailbox user owns Office 365 OAuth consent; the reviewer
later owns each specific action approval. Neither is delegable to model text.
Never paste keys, tokens, callback URLs or login caches into a prompt.

**Platform owner:** chooses the networking posture with you. For this path,
prefer an approved existing `private-required` environment and a reachable
operator host. Private hosted networking is a first-account-creation contract;
a laptop outside the VNet may not reach it. A
`public-authenticated-proof` environment is a separate, explicit nonproduction
exception, not the default or a workaround for a private-network failure.
Do not adopt an old policy-exemption tag or broaden access to make it work.
The Outlook companion's Entra-authenticated trigger is not proven private merely
because the business services are private.

**Result:** reviewed implementation inputs plus named owners for the missing
model, platform, signing, review and observation requirements. No resources or
consents are implied.

**Verify:** before every Azure operation, the operator derives both
`AZURE_CONFIG_DIR` and `AZD_CONFIG_DIR` from the **personal tenant index**,
checks the selected tenant and `allowed_subscriptions`, and asserts the exact
intended subscription immediately before acting. Use the matching isolated azd
environment as well. A default subscription hint is not your explicit selection;
one CLI login does not authenticate the other. Never copy caches or change a
global context. Keep identifiers in protected operator configuration, not this
public guide.

**If blocked:** if the operator cannot provide the registry connection, private
route, approver or signing authority, stay local. Use
[existing isolated dependency requirements](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md#supply-existing-isolated-azure-dependencies)
for the handoff; do not ask for broad Owner/Contributor grants.

## Phase 3: Rehearse locally without calling it Azure proof

**Cost and safety:** source packaging and fixture checks are local/offline,
with no model call or Azure effect. Optional conversational rehearsal calls
the approved model and can incur inference cost; ask for that scope and budget
first. Package downloads/builds also need the approved local toolchain.

**Goal:** make the example understandable and catch contract mistakes cheaply.

**Skills:** [threadlight-local-test](../skills/threadlight-local-test/SKILL.md)
for local rehearsal; [threadlight-deploy](../skills/threadlight-deploy/SKILL.md)
only for its offline source packager at this phase.

**Inputs:** the reviewed synthetic cases, separate unused output directory,
application instructions and installed test dependencies. No model access is
required to inspect or package sources.

**Copilot prompt:**

```text
Use threadlight-deploy's offline two-tool source packaging reference only:
skills/threadlight-deploy/references/governance/package_returns_mcp.py.
Show the output destination before writing; never overwrite an existing one.
Use threadlight-local-test to prepare a synthetic local rehearsal and check
the instructions, case schema and tool contracts. Start with offline checks.
Do not call a model until I approve the endpoint and call budget.
Keep any quickstart CRUD stubs explicitly separate from the real two-tool
gateway. Report what executed, what is simulated and what remains unverified.
Do not deploy, seed Cosmos or fabricate a signature or approval.
```

**Assistant:** materializes the real source closure and inspects the
[backend contract tests](../skills/threadlight-deploy/tests/test_returns_mcp_backend.py).
It checks allowed decision values, the original `expected_etag`, rejection of
model-supplied authority, and the conditional case/audit transaction. It runs
the applicable local checks only with the documented dependencies available,
not fake replacement SDKs.

**Human:** reviews ordinary, denied and escalated outcomes. Separately approves
any model rehearsal; a prompt instructing the model to escalate is not human
action authorization.

**Platform owner:** supplies approved inference access only if model rehearsal
is requested. No new Azure services are required for the offline portion.

**Result:** an unused-directory source package with `source-package.json`,
status `source-only-not-deployment-proof`, plus the actual local test results.
The packager supplies no deployment configuration, credentials or automatic
seed/reset. Its VM-first entrypoint is not the Foundry hosted deployment;
Phase 4 uses the native hosted host.

**Verify:** inspect the packaged file hashes and actual check output. A local
quickstart is not the governed gateway or hosted route: its generated CRUD
tools mutate in-memory data. Inspect loaded instructions, tools and skill
loading warnings, not just a running UI. If enabled, `tests/quickstart.jsonl`
contains raw local queries/responses; Git-ignore it and apply the agreed
retention rules. It is not a governance receipt.

**If blocked:** a missing package or empty skill loading result is a stop,
not a pass. Follow [materialization commands](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md#materialize-the-executable-sources)
and [local loading limits](skill-based-agents.md#local-quickstart-is-a-different-test-surface).
The executable source is [package_returns_mcp.py](../skills/threadlight-deploy/references/governance/package_returns_mcp.py).

## Phase 4: Opt in to governance and review the hosted setup

**Cost and safety:** authoring/generation and native local tests are separate
from cloud deployment. The latter creates or changes billable services and
requires explicit target, identity, network, budget and preservation approval.
This prompt prepares that handoff; it does not authorize it.

**Goal:** connect the selected write to real authorization and a reviewed
native host, without giving the agent direct effect or signing authority.

**Skills:** [threadlight-govern](../skills/threadlight-govern/SKILL.md),
[threadlight-governed-actions](../skills/threadlight-governed-actions/SKILL.md)
and [threadlight-deploy](../skills/threadlight-deploy/SKILL.md). For the native
host baseline, use the companion
[foundry-hosted-agents](https://github.com/aiappsgbb/awesome-gbb/blob/2ef44f6b47803a0166956cc668e5f429c1c1f8cb/skills/foundry-hosted-agents/SKILL.md)
revision identified by the two-tool runbook, not an independently upgraded SDK.

**Inputs:** explicit selected-governance consent, `specs/governance-contract.json`,
approved policy rules and the real operator-supplied inputs from Phase 2.

**Copilot prompt:**

```text
I select governance for returns_apply_decision through governed-tool-gateway;
leave returns_get_case unbound to ACS. Use threadlight-govern,
threadlight-governed-actions and threadlight-deploy to prepare the real
policy, host and separate services from the reviewed two-tool reference.
Use foundry-hosted-agents for the documented MAF hosted baseline.
Select deferred, policy-required review and the native Outlook channel.
Prepare the generator inputs and required local validation; stop when a real
publisher, observed deployment binding, permission or consent is missing.
Before any cloud action, present its exact scope, cost and human/platform
handoffs. Do not deploy, grant roles, sign policy or enable/send email yet.
```

**Assistant:** uses the [existing generator and input order](../skills/threadlight-deploy/references/governance/README.md#order-and-required-inputs),
not an assessment-only skeleton. It prepares the
[MAF gateway host](../skills/threadlight-deploy/references/governance/maf-gateway-container.py)
and exact two-tool registration. It preserves unbound reads; invalid selected
configuration fails closed rather than becoming off. The gateway is the PEP;
ACS/Rego is its PDP. Agent Hooks is a host/interceptor contract, not an extra
security boundary automatically attached to this gateway path.

**Human:** approves the specific deployment separately from opting in to
governance. The mailbox user completes OAuth only for the named connection.
If browser interaction is needed, use the user's **Edge Work** profile; the
actual human performs sign-in/consent and later Approve/Reject. No automated
consent or fabricated human token.

**Platform owner:** follows the
[create-once signed-bootstrap contract](../skills/threadlight-deploy/references/governance/README.md#signed-remote-bootstrap-operator-contract)
and the two-tool hosted runbook, with reviewed
[runtime pins](../skills/_shared/governance-upstream-pin.json).
First establish native hosted/model operation; retain the actual observed
version, immutable image digest and identities. Build/register once, then
configure the separate services and publish matching fresh signed policy/bindings;
do not redeploy the agent after signing merely to update its own identity/digest.
The publisher and private key remain outside the agent. The agent gets neither
Cosmos write nor signing authority; service app roles are not ARM role grants.
Required signed/fresh policy, trusted backend facts, authenticated one-use
approval when selected, and **central audit ACK before effects** must survive
credential/transport waits and be rechecked at dispatch.

For Outlook, the operator uses the
[native approval template](../skills/threadlight-deploy/references/governance/review-approval.bicep),
which references the existing connection and defaults to Disabled. It does not
grant permissions or supply consent. Explicit enablement, observed workflow
version/digest, `outlook_approval`, gateway `approval_channel: "outlook"`,
fixed recipient/responder mapping and control-identity workflow-scoped Reader
are all required. Reader can expose sensitive run output. Notification-only
email is not this authority channel.

**Result:** actual generated host/service/infra files and scoped
`specs/governance-manifest.json` inventory; executed local evidence only where
the relevant tests ran. A subsequent authorized deployment adds its own
observed binding and service readiness, not a blanket governance verdict.

**Verify:** use the existing [native validation gate](agent-operations.md#3-validate).
Distinguish offline inventory, executed LOCAL-14, native/CTK and hosted evidence;
none substitutes for another. Unrun or skipped required tests remain unverified.
Use the current `threadlight-governance-manifest/v1` contract, not archived v2
green reports. A healthy hosted endpoint does not authorize a selected tool.
Preview/alpha dependencies still require platform review.

**If blocked:** an ambiguous registration reply is not permission to create a
second version. Preserve the attempt and reconcile through the
[signed-bootstrap operator contract](../skills/threadlight-deploy/references/governance/README.md#signed-remote-bootstrap-operator-contract).
Missing Outlook authority must stop at
[workflow prerequisites](native-outlook-approval-architecture.md#4-workflow-contract-and-permissions),
not silently switch to delegated CLI approval.

## Phase 5: Demonstrate allow, deny and a real human decision

**Cost and safety:** live, potentially billable model calls, synthetic Cosmos
writes and approval email. Proceed only under a separately approved
nonproduction target, scenario set and budget, with the actual reviewer present.
No automatic retries or unbounded prompt loops.

**Goal:** observe what the selected path permits, blocks and records in this
deployment, including exact human-approved resume and replay.

**Skills:** [threadlight-governed-actions](../skills/threadlight-governed-actions/SKILL.md)
for selected-path evidence;
[threadlight-safe-check](../skills/threadlight-safe-check/SKILL.md) for the
separately approved hosted collector. Neither invents the human or business proof.

**Inputs:** fresh signed policy/bootstrap, unchanged observed deployment,
create-only synthetic cases with independently read revisions, native session
access, reviewer availability and independent evidence-reader permissions.
Enable the reference's optional read audit if you intend to demonstrate
backend-acknowledged reads.

**Copilot prompt:**

```text
Use threadlight-governed-actions to prepare the bounded two-tool acceptance
matrix for this deployment: allow, deny, pending, actual native Outlook
Approve, exact resume, replay, and a separate Reject case.
Execute only after the operator approves the target, synthetic cases and
budget and the reviewer confirms availability. No borrowed evidence.
Keep session_id, previous_response_id, original arguments and
governance_operation_id for the pending request. Do not paraphrase a resume,
manufacture approval or silently start a new operation after a failure.
Use threadlight-safe-check only for its separately authorized noop scope.
Independently reconcile the business effects and audit; report unexecuted
scenarios as unverified, not successful.
```

**Assistant:** prepares the matrix, then helps execute only the approved calls.
It retains the original protected pending tool output and native response IDs.
Model output is **nondeterministic**: a polite answer, a particular category
word or a model promise of approval is not the acceptance criterion. If the
model does not choose the scenario's tool, that scenario has not run. Any
separately authorized direct native-tool check must be labelled as such, not
reported as model-driven.

**Human:** checks the exact case, proposed action, reason and expiry in Outlook,
then chooses **Approve** or **Reject**. Approval of a supervisor handoff never
authorizes a refund. Confirm connection authentication from current readback;
do not repeat OAuth solely because a historical capture was unauthenticated.
Native Outlook decisions are witnessed through ARM and trusted responder
mapping, not OBO or a model-supplied `approved` field.

**Platform owner:** verifies current signed bindings and independent store
access, seeds only new approved cases create-only, and retains the raw evidence
privately. Set the review window deliberately: deferred review defaults to
300 seconds, supports up to 3,600, and is bounded by policy expiry; the native
mail action waits at most 15 minutes. This is not multi-day case management.

**Result:** the following observations, **only if each scenario actually ran**.

| Scenario | Stable acceptance evidence |
|---|---|
| Read | Native tool result matches the case/revision; when configured, `read_audit_id` joins an independently read durable backend ACK |
| Allow | `case_id`, `decision`, `audit_id` join the real case change and decision audit, gateway completed operation and earlier central authorization receipt |
| Deny | Actual policy denial and independently unchanged case/no decision-audit creation for that operation; a `deny_refund` recommendation is not itself a policy denial |
| Pending | Real `pending_approval` and immutable intent; no business effect while the person has not decided |
| Approve and resume | Verified `outlook-native/v1` authority, grant `consumed`, central receipt and one business audit; same native session, original selector and unchanged arguments |
| Replay | Matching stable `audit_id`, with no second business effect or second grant consumption |
| Reject, separate case | Verified human rejection, rejected operation and no business effect; do not reuse the approved case to imply this test |

**Verify:** resume with the exact original `expected_etag`, decision, reason and
`governance_operation_id` in the **same native session**.
`previous_response_id` preserves conversational continuity; `session_id` is
not the gateway operation key. Never fetch a newer ETag and attach the old
approval. Use [exact resume](native-outlook-approval-architecture.md#7-resume-authorization-ack-and-effect)
and independent [response reconciliation](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md#durable-unbound-read-audit-and-response-reconciliation).
The [returns_reconcile.py source](../skills/threadlight-deploy/references/governance/returns_reconcile.py)
reads an explicit response set and joins the four configured stores; optional
ledger persistence itself needs narrow approved write access.

Retain before/after/replay observations and binding hashes, not just chat text.
Sanitize shared evidence; business audits and workflow runs can contain review
data even when governance receipts are payload-minimized. This is selected-path
evidence, **not whole-agent governance**. The collector's
`governance_probe_noop` cannot prove `returns_apply_decision`.
A new deployment attempt needs fresh after-deployment evidence bound to that
same envelope, policy, key, environment, image/version and identities.

**If blocked:** expired intent, changed facts, missing reviewer or missing ACK
means stop. For an unknown outcome, retain the original operation and reconcile
authoritative stores; do not resend email, change operation IDs or renew a
timestamp. Follow [current failure handling](native-outlook-approval-architecture.md#9-failure-handling-and-operating-ownership).
Lost authorization ACKs may leave no safe automatic continuation.

## Phase 6: Hand off a controlled release and safe operations

**Cost and safety:** local release-artifact preparation is not release execution.
Candidate deployments, evaluations, red-team scans and promotion are separately
authorized, cost-bearing operations. Cleanup also needs its own exact scope.

**Goal:** make the pilot reviewable and operable without treating a successful
demonstration as production acceptance.

**Skills:** [threadlight-cicd](../skills/threadlight-cicd/SKILL.md) for the existing
verified-release profile, and
[threadlight-production-ready](../skills/threadlight-production-ready/SKILL.md)
for an evidence-based gap/handoff report.

**Inputs:** the reviewed application commit, immutable image, current selected
binding evidence, chosen CI platform, representative datasets/thresholds,
application-owned adapters, owners for operations and retention, and the
platform's protected validation/production environments.

**Copilot prompt:**

```text
Use threadlight-cicd to prepare the existing verified-release profile for
our chosen CI platform, not a new pipeline implementation. Identify missing
application-owned deployment, observation, evaluation, red-team and promotion
adapters plus platform-owned identities, runners and environment approvals.
Use threadlight-production-ready to report current evidence and open gaps.
Keep release approval, runtime action approval and business go-live separate.
Do not start CI, deploy, merge, release, run paid checks or delete resources.
Prepare the operations and exact test-owned cleanup handoff after retaining
sanitized evidence; preserve all pre-existing and shared resources.
```

**Assistant:** generates the existing delivery artifacts and explains missing
integrations. It uses [AgentOps: controlled release](agentops-deep-dive.md)
for the mental model and the
[verified release contract](../skills/threadlight-cicd/references/release-contract.md)
for exact commands, without duplicating the pipeline here. Optional native
AgentOps adoption is not required merely to read this guide.

**Human:** makes three separate decisions: **release approval**, **runtime
action approval**, and **business go-live**. Accept only named, current,
scoped risks. A green scorecard does not certify production readiness.

**Platform owner:** supplies distinct validation/production identities,
federation, protected environments and reachable runners. The application team
supplies real adapters, observations, evaluator and scanner execution.
Generation does not configure customer permissions or reviewers. The approved
pipeline validates a preproduction candidate, then promotes the **same immutable
image** after its receipt and approval checks; production-specific bindings and
business behavior still need their own fresh verification.

**Result:** reviewed pipeline and `specs/release-policy.example.json` awaiting
real configuration as `specs/release-policy.json`, plus
`docs/production-readiness-report.md` and
`tests/production-readiness-manifest.json` when the assessor actually runs.
An accepted `.threadlight-release/candidate.json` exists only after executed
candidate validation, not after generating YAML.

**Verify:** follow the current [operations spine](agent-operations.md) and
[static rescore handoff](agent-operations.md#6-rescore). Name owners for workflow
entitlements, signing/rotation, service monitoring, spend and response to
uncertain effects. Neither a static score nor catalog/local tests prove a
customer pipeline or live production Doctor ran.

**If blocked:** a promotion timeout can leave an unknown outcome. Keep business
traffic closed and follow [Stop and recover](../skills/threadlight-cicd/references/release-contract.md#stop-and-recover);
there is **no automatic rollback**, universal recovery command or permission to
retry by deleting the attempt record. Application rollback does not undo a
committed Cosmos decision.

Retain sanitized evidence and protected originals under the agreed retention
policy **before cleanup**. Inventory exact resources and records with their owner;
remove **only new temporaries owned by this test** after specific approval.
Preserve **pre-existing, shared and preserved demo** environments, images,
attempts and audit records. Never tear down an unknown whole environment.
Policy expiry or a failed test does not authorize deletion.

## Where to go next

Return to [the commercial governance page](agent-governance.html) for the
business framing. For implementation, use the
[action-governance deep dive](agent-governance-deep-dive.md),
[native Outlook contract](native-outlook-approval-architecture.md), and
[two-tool runbook](../skills/threadlight-deploy/references/governance/returns-mcp-demo.md).
Their dated captures are historical examples, not credentials or receipts for
your project.

This path was checked against catalog `main` at
`c8de52e6652ef0e7830cf7774aa8deb2fffc6f2f`. It needs no unmerged runtime work.
Pinned older explanations remain useful references, but select the complete
reviewed catalog and actual installed versions before using their commands.
Publishing this guide performs no deployment, consent, human approval or cloud
acceptance.
