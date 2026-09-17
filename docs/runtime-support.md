# Runtime, action enforcement and human review

Select the execution profile before promising a channel or long-lived review.
An Adaptive Card, an email notification or an `approved` input field is
**not an approval grant**. Only the configured authority can issue a current,
scoped, one-use decision.

## Framework and enforcement

| Profile | Supplied enforcement | Human review |
|---|---|---|
| GitHub Copilot SDK / Invocations | Selected tools through the governed MCP gateway; no local Agent Hooks mode | Inline gateway approval. Deferred native-session resume is rejected with `ghcp_deferred_approval_resume_unsupported` |
| MAF / Responses, local hooks | Selected native tool/output boundaries through Agent Hooks and ACS | Inline native approval; do not infer deferred gateway resume from this profile |
| MAF / Responses, governed gateway | Selected MCP actions through the authenticated gateway | Supplied deferred review can resume the **same native session** and operation; current authority is consumed once |
| No enforcement selected | Existing unbound behavior | A UI or release gate does not add action authorization |

MAF workflows are an explicit design route, not evidence that every custom
workflow has the supplied agent-host pause/resume seam. Verify the application's
actual host and session persistence. Mixed local/gateway selected bindings are
not automatically generated. Invalid selected configuration never means off.

Sources: [runtime selector](../skills/threadlight-design/references/runtime-policy.json),
[governance generator](../skills/threadlight-deploy/references/governance/generate.py),
[MAF gateway host](../skills/threadlight-deploy/references/governance/maf-gateway-container.py).

## Channel and authority

| Channel | Implemented component | Required authority boundary |
|---|---|---|
| Native Outlook approval | Logic Apps `SendApprovalMail`, independent ARM workflow/run witness, configured responder mapping and control-plane decision | Exact configured workflow, recipient/responder, role, intent and expiry; not an arbitrary email reply |
| Delegated operator review | Authenticated control-plane review/decision protocol and operator client | Real Entra delegated token, allowed UI client/subject/scope/role; never a workload token with pasted human fields |
| Teams Adaptive Cards | Seven templates and an executable approve/reject decision bridge | Application-owned verified SSO/OBO credential and protected review loader. The bridge calls the control plane; it does not supply bot login or native resume |
| Workspace panels | Reusable action/decision/audit UX | Application-owned authenticated handler; browser fields are not trusted evidence |

Edits create a **new intent**, not a modification of an approved request.
Signoff is an acknowledgement, escalation selects another review path, and
request-info can itself send data outside the application: none should silently
be translated into a business-action grant.

## Time and operations

The default deferred review limit is **300 seconds**. Explicit configuration
supports up to **3,600 seconds**, further bounded by signed policy expiry.
This is not multi-day case management. Expiry requires fresh policy/intent and
review; timestamps and previous grants must not be extended in place.

The identity owner maintains the entitlement source. Delegated decisions use
validated token claims and configured allowlists. Native Outlook uses the
trusted configured responder-to-role map, not a fresh Graph membership query.
The operations owner maintains signing keys, rotation and service availability.
Signed snapshots can remain usable until expiry; stronger remote-bootstrap/key
health checks must not be generalized into universal instantaneous revocation.

The opt-in gateway [operator recovery and admission profile](../skills/threadlight-govern/references/gateway/README.md#operator-recovery-and-admission)
adds authenticated outcome reconciliation and a durable stop with open leases
bounded to 300 seconds. It requires Strong single-writer Cosmos and fresh
post-wait admission/key checks. Completed operations recover their prior result;
proven-not-executed operations are terminal fenced records, **not retry authority**.
Missing/404 outcomes stay unknown. This does not add automatic failover,
long-lived review, undo or a universal all-runtime kill switch.

The September 15 private Outlook proof covered its selected reference
application and native session. It is not acceptance for the richer
[canonical returns application](../examples/returns-triage-governed/README.md),
another channel, another runtime, or a subsequent image.

Details: [action-governance architecture](agent-governance-deep-dive.md),
[native Outlook protocol](native-outlook-approval-architecture.md),
[card pack](../skills/threadlight-hitl-patterns/references/cards/README.md).
