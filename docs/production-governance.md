# Production governance: technical companion to the Production chapter

For the **L200/L300** visual explanation, read the
[Production chapter on Pages](https://aiappsgbb.github.io/threadlight-skills/production.html).
This **L400** companion follows the same six parts, in the same order, and adds
the ownership, evidence categories and limits that the page summarizes.
It routes to the **L500** contracts; it does not repeat them, authorize a
deployment or certify production readiness. For the release part alone, the
[AgentOps guide](agentops-deep-dive.md) remains the detailed explanation.

| Part on Pages | Question | Detailed source of truth |
|---|---|---|
| [1. Starting point](#1-a-working-pilot-is-the-starting-point) | Which decisions remain after a working pilot? | This document; [production-readiness.md](production-readiness.md) |
| [2. Foundation](#2-choose-the-foundation-you-will-operate) | Which platform will operate the agent? | [production-readiness.md § 3](production-readiness.md#3-the-three-postures), [citadel-governance.md](citadel-governance.md) |
| [3. Validate](#3-validate-the-candidate-before-promotion) | Is this candidate fit to promote? | [agentops-deep-dive.md](agentops-deep-dive.md), [release contract](../skills/threadlight-cicd/references/release-contract.md) |
| [4. Authorize](#4-authorize-selected-actions-before-they-change-records) | Who allows a selected business change? | [agent-governance-deep-dive.md](agent-governance-deep-dive.md), [runtime-support.md](runtime-support.md) |
| [5. Evidence](#5-review-the-evidence-name-the-owners) | What does each proof establish, and who owns it? | [agent-operations.md](agent-operations.md), [signed-evidence.md](signed-evidence.md) |
| [6. Continue](#6-continue-from-the-pilot-you-already-have) | What is the next step from here? | [agent-governance.html](https://aiappsgbb.github.io/threadlight-skills/agent-governance.html), [IDEA-TO-PRODUCTION-WORKBOOK.md](IDEA-TO-PRODUCTION-WORKBOOK.md) |

The order is didactic. It is not a mandatory pipeline and does not imply that
every component depends on every other one. Evaluation starts before a release;
privacy and operations apply throughout.

## Terms used throughout

| Term | Role | Not to be confused with |
|---|---|---|
| **SAFE** | Method for defining business invariants and selecting bindings | A runtime component |
| **ACS/Rego** | Policy decision point (PDP), evaluated locally through OPA | Enforcement |
| **Agent Hooks** | Host/interceptor contract SDK | A policy |
| **Native host or governed-tool gateway** | Policy enforcement point (PEP) for the selected binding | The model gateway (Citadel/APIM) |
| **AGT** | Toolkit that packages the governance pieces | Whole-agent governance |
| **ASSERT** | Assurance: tests and evidence about what was enforced | Certification |

Protection is **per selected binding** (tool, intervention point, execution path).
A policy file, a green CI run or one enforced binding never establishes
whole-agent governance. Report `enforced`, `observed`, `unbound`, `unverified`,
`unsupported` and `bypassable` per binding; see
[agent-operations.md](agent-operations.md#required-controls-precede-effects).

## 1. A working pilot is the starting point

A successful pilot proves that an agent can help. Three decisions remain, and
they belong to different people:

| Decision | Typical owner | Established by | Does not establish |
|---|---|---|---|
| **Release** — may this candidate be promoted? | Application team with DevSecOps | Required checks bound to the observed candidate, plus configured environment approval | Runtime authority for business actions, or business acceptance |
| **Action authorization** — may this specific change happen now? | Business/policy owner; runtime enforces | Signed current policy, trusted facts, required human decision and audit acknowledgement at the effect boundary | Release fitness or general agent quality |
| **Business acceptance** — do we go live with this residual risk? | Business owner with security and operations | Review of current evidence, open gaps and explicit, scoped acceptances | Technical proof; an assessment is advisory |

A release result is **not automatic go-live approval**. The
[production-readiness assessment](production-readiness.md#2-what-production-ready-means-here)
identifies gaps, owners and freshness; it does not certify production or
authorize a deployment.

## 2. Choose the foundation you will operate

The governed agent always runs in Microsoft Foundry and is a **hosted agent by
default**; a prompt agent requires explicit user opt-in. Container Apps hosts only
UI, thin proxies, MCP tool servers or jobs; app-side agent loops are not a
governance target and voice agents are out of scope
([foundry-only-agents.md](../skills/_shared/foundry-only-agents.md)).

What changes is the foundation around that agent. The readiness skill resolves
one of three postures from SPEC § 12
([production-readiness.md § 3](production-readiness.md#3-the-three-postures)):

| Posture | Right when | Model gateway | Action boundary |
|---|---|---|---|
| **Citadel spoke** | The customer runs or is provisioning a Citadel hub (GBB-recommended in that case) | Citadel/APIM Access Contracts | Separate selected PEP with its own evidence |
| **Standard AI gateway / VNet** | An existing customer APIM or NetSec-owned perimeter must be respected | Customer gateway | Separate selected PEP |
| **Native selected runtime enforcement** | No central model gateway is available yet | None | Native host path with real distribution, approval and audit services |

**Citadel is optional.** It is an external accelerator
(`Azure-Samples/ai-hub-gateway-solution-accelerator`, branch `citadel-v1`),
not maintained by Threadlight. It governs configured model traffic, registered
consumers and consumption attribution. It does not authorize business actions
or discover unregistered agents. The opt-in
[Citadel action execution profile](citadel-governance.md) keeps the native PEP
behind APIM and is a local vertical, not live Citadel evidence.

Network, identity and model controls need explicit configuration in every
posture. Data access and privacy stay with application and data owners
([part 5](#5-review-the-evidence-name-the-owners)).

## 3. Validate the candidate before promotion

Release sequence generated by `threadlight-cicd`
([agentops-deep-dive.md](agentops-deep-dive.md)):

1. Reviewed source and policy (commit or manual dispatch).
2. Preflight: policy and adapter configuration.
3. Candidate prepared in a separate preproduction target.
4. Observation of the actual version and image.
5. **One validation job** producing three required evidence domains —
   evaluation, red-team and MCP supply-chain checks. They are grouped by
   purpose, not executed as parallel branches.
6. Production environment approval and current-authority recheck, then
   promotion of the **same image**.
7. Observation of production afterward.

A validation record binds results to the observed image, reviewed inputs and the
current run. Missing, expired or changed evidence stops promotion. Required checks
cannot become warn-only. A failed check does not roll back resources or undo a
business effect.

**Who supplies what.** Threadlight executes the reviewed release sequence and
required checks. The application team supplies real deployment,
target-observation, evaluation and scanner integrations, environment reviewers,
scoped validation/production identities, runners with verified DNS/routes/egress,
and promotion/recovery behavior. The workload pipeline does not deploy the shared
hub. Optional native AgentOps runs a bound evaluation; it does not supply every
integration ([agent-operations.md](agent-operations.md#agentops-preview-explicit-opt-in-bounded-assessment)).

Templates and the operator contract:
[GitHub Actions](../skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl),
[Azure DevOps](../skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl),
[release contract and recovery](../skills/threadlight-cicd/references/release-contract.md).
Generator output and local verification are **not cloud acceptance**.

## 4. Authorize selected actions before they change records

A validated release does not authorize every action. During operation, selected
business changes still need their own policy checks and any required human
decision.

**Two different gateways.** Citadel/APIM is the **model gateway**. Threadlight's
governed MCP gateway is the PEP for **selected business effects**. Authenticated
unbound reads go directly to the business API without ACS; the backend rejects
unauthorized direct writes. Consequential unbound acceptance must be explicit,
current and scoped to owner, risk, tool, commit and environment.

| Actor | Responsibility | Never holds |
|---|---|---|
| Agent (hosted Foundry) | Obtains evidence, proposes the exact action | Database-write or signing credentials |
| Governed MCP gateway (PEP) | Identity, trusted facts, evidence and ACS decision before dispatch | Business correctness |
| Business API | Independent authorization; its writer identity commits decision and audit | Trust in the agent's claims |
| Control plane | Approval intents, one-use grants, central audit records | Business execution |
| Human reviewer / requesting user | Decide one exact request | A general override |

Before an effect: signed/fresh policy, authenticated one-use approval where
required, trusted backend target/schema/facts and central audit ACK. Authorization
is rechecked after credential or transport waits.

**The returns example.** The canonical
[returns example](../examples/returns-triage-governed/README.md) records a Cosmos
decision and its audit — **not payment settlement**. `returns_apply_decision`
has local evidence but remains **live-unverified**. The scenario player on Pages
(allowed, blocked, human review, signed evidence, requesting-user confirmation)
is an illustration with no model, mailbox or backend connected.

Implementation detail, by topic:

- Signed evidence JWT fields, MCP `_meta["threadlight/evidence"]` transport and
  `X-Evidence-Fingerprint`: [agent-governance-deep-dive.md § 5](agent-governance-deep-dive.md#a-tool-policy-can-require-signed-input-evidence)
  and [signed-evidence.md](signed-evidence.md). The page's Loan fixture is a
  synthetic source example, not a lending workflow.
- Reviewer grant scope, expiry (default 5 minutes, configurable up to 60, capped by
  signed policy) and one-use CAS consumption:
  [§ 6](agent-governance-deep-dive.md#third-party-reviewer-approval).
- Requesting-user confirmation (native Outlook Approve/Reject, matching
  authenticated user, email is not MFA, Entra step-up needs verified Conditional
  Access, customer/B2C providers not all implemented, MAF gateway first, GHCP
  deferred): [§ 6](agent-governance-deep-dive.md#requesting-user-confirmation).
- Framework, channel and time support: [runtime-support.md](runtime-support.md).
- State, audit and recovery: [§ 7](agent-governance-deep-dive.md#7-state-audit-and-recovery).

## 5. Review the evidence. Name the owners.

| Question | Owner | Evidence | Limit |
|---|---|---|---|
| Who is the agent acting as? | Platform and identity team | Managed identity and role assignments observed on the deployed candidate | Identity proves who calls, not whether an action is allowed |
| Can it read this record? | Application and data owners | Caller/tenant permission checks at the source, minimization and retention settings | Not supplied by the new action-governance runtime |
| Did the candidate regress? | Application team; business owner sets thresholds | Evaluation of the observed candidate against the baseline | Execution success is not quality success; offline results are not live health |
| Can it be manipulated? | Security | Adversarial run on the candidate; MCP supply-chain checks | Covers the tested attack set only |
| May this change happen? | Business/policy owner; PEP enforces | Signed policy and per-binding enforcement evidence | One enforced binding is not whole-agent governance |
| What happened, when, for whom? | Operations | Central audit ACK before the effect; traces and alerts | Local spool durability is not hosted audit proof |

### Five kinds of proof

| Kind | Produced by | Establishes | Does not establish |
|---|---|---|---|
| **Generated artifact** | Generators (`threadlight-deploy`, `threadlight-cicd`) | Expected configuration and wiring exist | That anything ran |
| **Offline inventory** | `threadlight-govern` | Declared policy and bindings | Enforcement |
| **Executed LOCAL-14** | `threadlight-governed-actions` | Selected paths behave locally | Hosted behavior |
| **Native/CTK** | Pinned native tests (47 declared CTK vectors) | Native contract conformance on this host | Azure or customer-environment behavior |
| **Live evidence** | Approved deployment and post-deploy collector | Fresh after-deployment observations bound to signed envelope, bundle, key, environment, image, identities and parent scope | Anything beyond what was observed |

The deployed collector proves only the reserved `governance_probe_noop` binding,
not business writes. Historical receipts are not inherited: a new deployment,
scope, key, image or identity change requires fresh evidence. Current consumers
reject legacy v2 green verdicts. Commands and stop/recovery rules for each stage:
[agent-operations.md](agent-operations.md#operative-path-configuration-to-current-evidence).

## 6. Continue from the pilot you already have

- **Existing Threadlight pilot:** follow the
  [existing-pilot guide](https://aiappsgbb.github.io/threadlight-skills/agent-governance.html#overview)
  (five prompts and checkpoints), then the detailed
  [first governed workflow](first-governed-workflow.md).
- **Readiness requirements:** run the advisory
  [production-readiness assessment](production-readiness.md#9-cli-cheatsheet).
- **Implementation and evidence commands:** [agent-operations.md](agent-operations.md).
- **Already released:** [Operate](https://aiappsgbb.github.io/threadlight-skills/operate.html)
  covers change, fresh evidence and unknown outcomes after go-live.
- **No pilot yet:** start with [Build](https://aiappsgbb.github.io/threadlight-skills/funnel.html).
- **End-to-end workbook:** [IDEA-TO-PRODUCTION-WORKBOOK.md](IDEA-TO-PRODUCTION-WORKBOOK.md).
