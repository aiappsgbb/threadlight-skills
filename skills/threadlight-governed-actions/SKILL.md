---
name: threadlight-governed-actions
description: >-
  Proves whether every *consequential* action a pilot agent can take is
  inventoried, mediated, enforced, approved, and auditable — then emits a
  customer-facing Governance Evidence Pack. Read-only by default. Inventories
  declared vs implemented actions, traces mediation across all six execution
  modes, runs hermetic application-path probes (deny, transform, crash,
  timeout, malformed verdict, approval replay, output gating, payload-free
  audit), and assesses the GitHub Copilot change plane (PR-only, CODEOWNERS,
  required checks, SHA pins, OIDC/WIF, identity separation). USE FOR:
  governance evidence, evidence pack, consequential actions, action inventory,
  Agent Hooks mediation, interceptor coverage, fail-closed enforcement,
  approval anti-replay, output mediation, payload-free audit, GHCP
  change-plane governance, governed-actions manifest. DO NOT USE FOR: policy
  authoring (foundry-agt), readiness scoring (threadlight-production-ready),
  HITL cards (threadlight-hitl-patterns).
metadata:
  version: "0.1.0"
---

# Threadlight Governed Actions — prove the consequential actions are governed

An agent that can only read is a demo risk. An agent that can *write*, *egress*,
or do something *irreversible* is a production risk, and the question a customer
actually asks is narrow: **when this agent takes a consequential action, what
stops it, who approved it, and where is the record?**

This skill answers that question with evidence rather than prose. It assesses a
pilot repository and renders one deterministic manifest, one customer-facing
evidence pack, and one never-self-applying remediation plan. It is an
**assessor**, not a runtime: it never becomes the interceptor, never writes a
policy, and never deploys anything.

```
declared actions ─┐
implemented code ─┼─► inventory ─► mediation graph ─► application probes ─┐
policy/approval  ─┘                                                       ├─► manifest
workflows/CODEOWNERS ─────────────► change-plane assessment ──────────────┤   evidence pack
alert catalog ────────────────────► alert posture ────────────────────────┘   apply plan
```

## When to invoke

| Phase | Invoke | What it proves |
| --- | --- | --- |
| `design` | after `threadlight-design`, before code exists | the SPEC § 8 action declaration is complete and every action has an explicit consequence class |
| `pre-deploy` | before `threadlight-deploy` | declared and implemented actions agree, every consequential path is mediated, and the application probes actually enforce deny/transform/fail-closed, approval anti-replay, output gating, and payload-free audit |
| `post-deploy` | **staging only** | the same assessment against a deployed non-production environment; `--phase post-deploy` refuses to run without `--staging-resource-group` naming an explicit, non-production staging resource group |

`post-deploy` is deliberately staging-only. This skill never runs, not even
read-only, against an unnamed or production environment.

## Inputs

Everything is repository-relative and read-only. Nine categories, each an
explicit rule — discovery never widens into "anything under the repository":

| Input | Where | Used by |
| --- | --- | --- |
| SPEC action declaration | `specs/SPEC.md` § 8 (required for `design`/`pre-deploy`) | `ACT-001`, `ACT-002` |
| Action registries | `agent.yaml`, `agent.yml`, `tool-registry.json`, `tool_registry.json` (target root) | `ACT-001`, `ACT-002` |
| Runtime + middleware source | every non-vendored `*.py` under the target | `MED-001`..`MED-003`, `ENF-001`, `ENF-002` |
| Policy files | `governance/**/*.{json,yaml,yml}`, `policies/**/*.{json,yaml,yml}` | `MED-001`, `ENF-001` |
| Approval handling | runtime files whose AST references `issue_approval`, `consume_approval`, `redeem_approval`, or `ApprovalHandler` | `APR-001` |
| Tests, conformance, evals | `tests/**`, `**/conformance/**`, `**/evals/**` | `ENF-001`, `GHCP-003`, `PIN-001` |
| Workflows | `.github/workflows/*.yml`, `*.yaml` (direct children) | `GHCP-001`, `GHCP-003`..`GHCP-006` |
| Ownership | `CODEOWNERS` and `.github/CODEOWNERS` | `GHCP-002` |
| Dependency pins | `pyproject.toml`, `requirements*.txt`, lockfiles | `PIN-001` |
| Alert catalog | `governance/alerts.json` | `OPS-001` |
| Optional live evidence | `--live-github` (`gh api`), `--subscription`/`--deploy-identity` (`az ... list`) | `GHCP-002`, `GHCP-006` |

Optional live evidence is exactly that: **optional**. When it is not selected, or
when a permission is missing, the affected control is reported `not-verified`.
A `not-verified` finding is never silently converted to `pass`.

## Outputs

| Artifact | Default path | Shape |
| --- | --- | --- |
| Conformance manifest | `tests/governed-actions-manifest.json` | `threadlight-governed-actions-manifest/v1`, schema `1.0.0` |
| Governance Evidence Pack | `docs/governance/evidence-pack.md` | customer-facing markdown |
| Remediation plan | `tests/governed-actions-apply-plan.json` | `threadlight-governed-actions-apply-plan/v1`, always `self_applying: false` |

Paths are overridable with `--manifest-path`, `--evidence-path`, and
`--apply-plan-path`. Writes are atomic: a partial artifact never replaces a
previously valid one.

## Running it

```bash
# Assess only — nothing is written.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase design

# Assess, write the three artifacts, and fail the build on an open requirement.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase pre-deploy --emit --gate

# Staging-only post-deploy, with optional live GitHub evidence.
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . --phase post-deploy \
  --staging-resource-group rg-pilot-staging --live-github --emit
```

The assessor is **read-only by default**: without `--emit` it writes no file at
all, and it never edits the assessed project's source, policy, or workflows.
The only writing mode beyond `--emit` is the bounded scaffold, which requires a
**double opt-in**: `--scaffold maf` *and* `--confirm-scaffold` together. Either
flag alone refuses and writes nothing. The scaffold places exactly five starter
files (interceptor, empty deny-by-default policy, approval-binding fixture,
contract test, workflow); its `rules` and `approvers` are always empty. Turning
that skeleton into a real policy belongs to `foundry-agt`, and turning the
workflow into a real deploy pipeline belongs to `threadlight-cicd`.

### Exit codes

| Exit | Meaning |
| --- | --- |
| `0` | Assessment completed; when `--gate` is set, the selected phase's requirements passed. |
| `1` | Assessment completed and a `must-fix` — or a selected-phase required `not-verified` — finding remains; valid requested artifacts are still emitted. |
| `2` | Invalid arguments, invalid schema, missing required local inputs, unsafe post-deploy target, or a refused scaffold request. |
| `3` | Assessor/tooling failure or atomic artifact-write failure. |

Without `--gate`, a completed assessment always exits `0` regardless of
findings — the findings are the product, not the exit code.

## The finding taxonomy

Exactly 18 IDs, two planes. `unsupported` is a stable *reason* on a `must-fix`
finding, never a status.

| ID | Plane | Condition |
| --- | --- | --- |
| `ACT-001` | runtime | SPEC/code inventory is incomplete or an action has no explicit consequence class. |
| `ACT-002` | runtime | Declared and implemented actions, aliases, or provider tools drift. |
| `MED-001` | runtime | A consequential path lacks evidenced pre-action mediation. |
| `MED-002` | runtime | Interactive, batch, background, subagent, or direct-tool coverage is absent or indeterminate. |
| `MED-003` | runtime | A provider-hosted side effect is not pre-interceptable and lacks equivalent server-side proof. |
| `ENF-001` | runtime | Deny or transform is not enforced on the application path. |
| `ENF-002` | runtime | Crash, timeout, or malformed verdict does not fail closed. |
| `APR-001` | runtime | Approval is not actor-, tenant-, policy-, action-, argument-, expiry-, and nonce-bound with atomic one-time redemption. |
| `OUT-001` | runtime | Protected output can egress before mediation. |
| `AUD-001` | runtime | Audit is incomplete, uncorrelated, undelivered, or contains payload data. |
| `PIN-001` | both | The tested upstream tuple drifted without a rerun, or pinned evidence is inaccessible. |
| `GHCP-001` | change | A protected branch accepts agent changes outside pull requests. |
| `GHCP-002` | change | CODEOWNERS, ruleset/branch protection, or required-check coverage is missing or unavailable. |
| `GHCP-003` | change | Required CI omits CTK, application probes, or relevant evals. |
| `GHCP-004` | change | An action SHA floats, or a workflow permission is excessive. |
| `GHCP-005` | change | Azure deployment uses a long-lived secret instead of OIDC/WIF. |
| `GHCP-006` | change | Build/test/deploy identities are shared, over-broad, or not evidenced. |
| `OPS-001` | both | The required alert posture is missing, or mandatory governance events are proven to be silently dropped. |

A consequential path without pre-action mediation always emits `MED-001` as
`must-fix`. A Conformance Test Kit claim alone never satisfies `ENF-001` or
`ENF-002`: those require this target's own application probes.

`APR-001` is proven by an actual redemption *sequence*, never a single
redemption — one accepted first use proves only that the approval worked once.
Within a single assessment the target's declared binding is redeemed once,
replayed byte-for-byte, and re-presented once per bound dimension (action,
arguments, target scope, tenant, both subjects, approving role, policy id,
policy hash, expiry) reusing the same, already-consumed nonce. Any accepted
replay or mutated binding — and any rejection the target still reaches the
protected tool for — is `must-fix`. The whole sequence runs against an
assessment-private nonce ledger that is created and deleted inside that one
run, so the target's own declared ledger is never read or written and two
consecutive assessments at the same commit are byte-identical and residue-free.

## Operational alerts (`OPS-001`)

A control that cannot report its own failure leaves an operator silently
unprotected. `governance/alerts.json` must declare, enable, and give a stable
`reason_code`/`correlation_id` pair (and never a payload field) for exactly
these eight classes:

| Alert class | Fires on |
| --- | --- |
| `unmediated-action` | repeated fail-closed denials or a consequential action reaching a tool without mediation |
| `interceptor-failure` | interceptor timeout or crash |
| `approval-replay` | a redeemed approval presented again |
| `output-mediator-failure` | the output gate failing or being bypassed |
| `audit-delivery-failure` | an audit record that cannot be written or delivered |
| `tuple-drift` | the tested upstream pin tuple drifting |
| `repository-protection-drift` | branch protection, ruleset, or required-check drift |
| `deployment-identity-drift` | evidence staleness or change in deployment identity/federation |

The skill asserts the *definitions* exist. It never invents a threshold, an
on-call rotation, a severity policy, or an approver.

## Privacy: payload-free evidence

Every artifact this skill produces is payload-free. It **never records raw
prompts, tool arguments, model output, or secrets** — only identifiers, hashes,
decisions, counts, statuses, and repository-relative paths. Probe children
report an invocation count, an argument *hash*, a decision, an exception class,
and audit event IDs, and nothing more. `AUD-001` fires when the assessed
target's own audit trail carries payload data.

## Trust boundaries and known limits

Read these as *published limits*, not caveats to be softened later.

- **Agent Hooks is a cooperative, alpha host/interceptor contract — it is not a
  security boundary.** A caller that skips the hook is not stopped by the hook;
  `ENF-002` exists precisely to name that bypass surface and demand a
  compensating control (allow-list, network policy, provider-side restriction).
- **The Microsoft Agent Framework (MAF) integration is experimental.** The
  pinned tuple in `references/upstream-pin.json` records the exact spec,
  SDK, CTK, MAF, and conformance-report versions that were tested; drift is
  `PIN-001` and requires rerunning both the CTK and the application probes.
- **Conformance is evidence, not certification.** A green manifest records what
  was proven against a pinned tuple at a commit. Nothing here certifies a
  product, a framework, or a customer's compliance posture.
- **Provider-hosted tools are a real limitation.** When a side effect happens
  inside a provider's own hosted tool, there is no pre-action interception
  point; absent equivalent server-side proof the finding is `MED-003`/`MED-001`
  with reason `unsupported` — never an inferred pass.
- **Mediation is not a substitute for server-side controls.** Even with a
  perfect interceptor, the called service still owns its own authorization,
  input validation, idempotency, and transactional integrity. This skill
  repeats that constraint rather than absorbing it.
- **The GitHub Copilot change plane is not the Copilot loop.** This assessor
  governs how an agent's changes reach a branch and an environment; it
  **never claims, and never intercepts, GitHub Copilot's own internal loop**
  — its reasoning and tool-calling stay outside this boundary — and it
  evaluates only the declared, static change-plane controls (branch
  protection, required checks, pinning, OIDC, identity separation) that the
  manifest's `change_plane` object records.
- **Customer policy stays with the customer.** No business threshold,
  authorization rule, named approver, tenant identity, or risk appetite is ever
  invented here; a missing one produces an explicit customer-decision
  remediation item.

## How this relates to the surrounding frameworks

| Framework | Role here |
| --- | --- |
| SAFE | a **design framework** for declaring actions and consequences — it is the source of the SPEC § 8 declaration this skill assesses, not an enforcement mechanism |
| Agent Constraint Service (ACS) | the **policy-decision** runtime and interceptor that can answer allow/deny/transform at call time |
| Agent Hooks | the **host/interceptor contract** the runtime seam is expressed through — cooperative, alpha |
| ASSERT / evals | **offline assurance** that measures behaviour before and after release; they are never pre-action runtime enforcement |

## Integration with the threadlight chain

- `threadlight-production-ready` **consumes** `tests/governed-actions-manifest.json`
  and rolls it up into the three aggregates `AGT-007` / `HITL-008` / `SUP-014`.
  It never re-runs a probe; the detail stays here.
- `threadlight-auto` is **recommendation-only** for this skill. It may surface a
  handoff and summarize an already-committed manifest; it never runs the
  assessor, never scaffolds, and never rolls out enforcement.
- `foundry-agt` authors the policy. `threadlight-hitl-patterns` designs the
  human approval experience. `threadlight-cicd` builds the production pipeline.
  This skill only assesses and evidences.
- **Production rollout is not owned here.** Deciding to enforce in production is
  an explicit, human, customer-owned choice.

## Rejected alternatives

Recorded so the boundary is not silently re-litigated:

1. **Extending `threadlight-production-ready`** was rejected: its broad posture
   assessment should **consume** aggregate evidence, not own runtime
   conformance probes.
2. **Splitting the feature across `threadlight-safe-check` and
   `threadlight-hitl-patterns`** was rejected: it fragments one customer
   evidence chain and one pass/fail matrix across two skills.
3. **A cross-framework-first implementation** was rejected: alpha host
   integrations multiply unstable surface area. MAF is first, behind the
   explicit `RuntimeAdapter` contract, so a second framework is an adapter
   rather than a rewrite.

## Files in this skill

| Path | What it is |
| --- | --- |
| `scripts/governed_actions.py` | CLI entry point: `parse_args`, `assess`, `exit_code`, `main` |
| `scripts/contracts.py` | typed contracts, fixed constants, and schema version |
| `scripts/inventory.py` | `build_action_inventory` |
| `scripts/mediation.py` | `build_mediation_graph` |
| `scripts/probes.py` | `run_application_probe` (hermetic, subprocess-isolated) and `run_approval_probe_sequence` (isolated, self-cleaning anti-replay) |
| `scripts/ghcp.py` | `assess_change_plane` + optional read-only live collectors |
| `scripts/alerts.py` | `assess_alerts` |
| `scripts/maf_adapter.py` | the `RuntimeAdapter` contract and its MAF implementation |
| `scripts/render.py` | `build_manifest`, `build_apply_plan`, evidence-pack rendering, atomic writes |
| `scripts/scaffold.py` | the bounded, double-opt-in scaffold |
| `references/governed-actions-manifest.schema.json` | manifest v1 schema |
| `references/governed-actions-apply-plan.schema.json` | apply-plan v1 schema |
| `references/finding-catalog.json` | the 18-ID catalog |
| `references/upstream-pin.json` | the exact tested upstream tuple |
| `tests/` | contracts, inventory, mediation, probes, GHCP, alerts, render/CLI, scaffold, and golden-fixture suites |
