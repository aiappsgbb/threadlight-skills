# Threadlight Readiness Closure - Design

- **Status:** Approved
- **Date:** 2026-08-20
- **Repository:** `aiappsgbb/threadlight-skills`
- **Delivery shape:** two sequential pull requests

## 1. Context

Recent changes added lifecycle legs, a value-model contract, Azure cost actuals,
forecast-to-actual reconciliation, and stronger deployment checks. The repository
implementation moved faster than its public narrative and proof surfaces:

- root documentation still describes older versions and lifecycle boundaries;
- GitHub Pages is deployed from the current source but does not expose the latest
  value and cost evidence;
- the live workflow proves design, deployment, and invocation, but its current
  success state does not prove semantic readiness;
- `threadlight-auto` is a lifecycle planner, while some public text implies an
  executable worker;
- the self-improving flow produces diagnostics and recommendations, not automatic
  remediation and verified reruns.

The work must make the public repository accurate and decision-ready without
starting a broad redesign or building new orchestration systems.

## 2. Goals

1. Make public documentation and GitHub Pages accurately describe what the
   repository proves today.
2. Put the end-to-end value journey, value-model contract, and cost evidence in
   the primary narrative.
3. Separate live deployment smoke evidence from semantic readiness evidence.
4. Tighten the highest-risk evidence handoffs without rebuilding the lifecycle.
5. Ensure every identified gap is either fixed or represented by a narrower,
   accurate public claim.

## 3. Delivery principles

- Use public product terminology and generic user roles only.
- All repository content, branch names, commits, tests, fixtures, pull request
  titles, and pull request descriptions must be suitable for a public audience.
- Do not modify another repository.
- Do not add new Threadlight skills.
- Do not redesign the GitHub Pages visual system.
- Do not build an automatic remediation worker.
- Do not require a multi-day live pilot to merge either pull request.
- Prefer truthful names and explicit limitations over additional automation.
- Keep existing workflow inputs backward-compatible where practical.

## 4. Pull request 1 - Align product narrative and Pages with current evidence

### 4.1 Purpose

Make the public entry points understandable and internally consistent before
changing workflow semantics. This pull request describes the current product
truth; it does not advertise pull request 2 as already delivered.

### 4.2 Root documentation

Update `README.md`, `THREADLIGHT.md`, `CHANGELOG.md`, and the main example
documentation to:

- describe the output of one working session as a governed working pilot plus an
  evidence-backed path to production, not production certification;
- distinguish the default production-readiness assessment from optional
  remediation and deployment actions;
- publish one canonical lifecycle matrix showing automated stages, manual or
  cost-bearing evidence legs, optional assessment, and later-pilot activities;
- bring skill versions, counts, entry routes, artifact names, and ownership
  boundaries into agreement with the implementation;
- present `threadlight-qualify`, `threadlight-design`, and existing-project entry
  routes without implying every user starts at the same stage;
- explain SPEC section 14 as an explicit value contract with baseline, target,
  owner, timeframe, measurement source, and maturity policy;
- show the evidence chain:

  ```text
  forecast
    -> settled Azure actuals
    -> scope-bound reconciliation
    -> cost per successful interaction
    -> production-readiness evidence
  ```

- describe cost actuals as a later-pilot activity rather than a first-session
  output;
- make the example snapshot internally consistent in capture date, scores, SPEC
  section count, and evidence inventory;
- summarize the repository's trust controls without exposing workflow internals.

### 4.3 Public positioning

Public copy may describe Threadlight as an idea-to-production playbook that can
be used within a broader Agentic Loop motion. It must not claim ownership,
official incorporation, or cross-repository integration that cannot be verified
from public sources.

Names such as Threadlight, Citadel, and individual skills must be accompanied by
the outcome they provide. The public story leads with:

1. bring a business process;
2. produce a working agent and auditable evidence;
3. identify the remaining production gaps;
4. measure value and cost as the pilot matures.

### 4.4 GitHub Pages

Update the existing pages and assets without changing the visual language:

- make time-to-value, governed handoff, and measurable outcomes visible in the
  first screen;
- add role-neutral calls to action such as "Bring a process", "Review the
  architecture", and "Inspect the evidence";
- surface the SPEC section 14 value contract;
- distinguish modeled forecast, Azure actuals, reconciliation status, billing
  window, scope coverage, variance, and unallocated cost;
- rename any modeled figure currently presented as an actual;
- label the demo reel as an evidence-backed recreation before playback and link
  directly to the live-run case study;
- expose the self-improving page from the primary narrative while describing it
  as diagnostics-to-backlog, not closed-loop remediation;
- normalize skill, industry, scenario, and lifecycle-stage counts;
- use one current Microsoft Foundry product name;
- repair broken fragments and provide usable mobile navigation;
- keep the strongest existing live-run evidence and limitations visible.

### 4.5 Pages safeguards

Extend existing repository checks rather than adding a new test framework:

- include every composer page and script in relevant workflow triggers;
- validate internal links and fragments;
- smoke-test primary navigation, Blueprint composition, industry loading, and
  the case-study evidence link;
- include all deployed JavaScript and CSS assets in cache-version validation;
- fail when public counts or canonical terminology drift from their repository
  source.

### 4.6 Acceptance criteria

1. Root documentation contains no stale versions, retired artifact names, or
   contradictory lifecycle counts.
2. One canonical lifecycle matrix defines automated, manual, optional, and
   later-pilot work.
3. SPEC section 14 and the forecast-to-actual evidence chain are visible from
   both README and the Pages home journey.
4. No modeled cost is labeled as an actual.
5. The reel and live-run case study are clearly distinguished.
6. The principal example is a coherent point-in-time receipt.
7. Every public page is reachable on desktop and mobile.
8. Internal links, fragments, composer behavior, and cache versions are covered
   by existing CI runners.
9. Every added or updated artifact is suitable for a public audience.

## 5. Pull request 2 - Make lifecycle evidence semantics explicit

### 5.1 Purpose

Make workflow success mean exactly what its name claims. This pull request does
not attempt to turn the existing gap-oriented workshop pilot into a production
certification environment.

### 5.2 Workflow modes

The live workflow exposes three clear semantic tiers:

- `design-only`: deterministic design-to-deploy contract validation;
- `live-smoke`: live deployment, invocation, anti-fabrication assertions, and
  assurance-manifest shape validation;
- `readiness-proof`: all `live-smoke` requirements plus semantic assurance and
  production-readiness requirements.

The existing `full` input remains as a temporary compatibility alias for
`live-smoke` and emits a deprecation notice in the workflow summary. Public
documentation no longer calls this mode a full readiness gate.

### 5.3 Semantic readiness policy

`readiness-proof` must:

1. run safe-check before downstream assurance;
2. require each assurance manifest to pass schema validation;
3. require `governed`, `comprehensive`, and `hardened` verdicts from the govern,
   evals, and red-team manifests respectively;
4. require a production-readiness manifest;
5. require the outcome KPI scorecard and its mandatory value-model fields;
6. fail closed when any required artifact, verdict, or scorecard field is
   missing;
7. print a concise evidence summary showing which requirement failed.

`live-smoke` may complete when assurance reports gaps, but its job name and
summary must state that readiness was not asserted.

### 5.4 Cost evidence scope binding

Reconciliation and production-readiness consumption must compare actual-cost
scope with the target pilot:

- subscription and resource-group scope are explicit inputs or are derived from
  the deployment evidence;
- the reconciled bundle records the expected scope and the observed scope;
- a mismatch fails reconciliation and cannot be presented as valid pilot
  evidence;
- existing digest, freshness, SPEC hash, and unit-cost re-derivation checks
  remain mandatory;
- tests use synthetic identifiers and remain offline.

### 5.5 `threadlight-auto` contract

Keep `threadlight-auto` as an agent-guided lifecycle planner:

- its public description must not imply that `orchestrator.py` executes stages;
- `--commit` is documented and tested as writing `auto-next.json`;
- the state-schema document must not claim that the orchestrator writes
  `auto-state.json`;
- resume checks for safe-check, assurance, and cost evidence validate required
  schemas, freshness, semantic status, and source hashes where the artifact
  contract provides them instead of trusting file age alone;
- invalid or manually fabricated artifacts cause rerun or hard-stop decisions,
  never a completed stage;
- no worker, scheduler, or automatic migration is introduced.

### 5.6 Lifecycle Canvas

Update the existing Canvas contract to:

- discover actuals and reconciliation artifacts;
- distinguish forecast-only, actuals-collected, reconciled, and scope-mismatch
  states;
- require the production-readiness manifest for production-readiness completion;
- use the same schema fixtures and verdict expectations as the workflow contract
  tests for assurance and cost artifacts;
- show missing evidence as incomplete rather than success-shaped.

### 5.7 Self-improving claim boundary

Repository documentation and Pages describe the current flow as:

```text
workflow run -> deterministic diagnostics -> ranked backlog
```

Automatic remediation, issue ownership, rerun linkage, and measured
before-versus-after improvement remain out of scope. No public text calls the
current implementation a closed autonomous loop.

### 5.8 Error handling

- Strict readiness requirements fail closed.
- Smoke-mode gaps remain visible in job summaries.
- Missing permissions identify the required capability.
- Malformed or scope-mismatched evidence never replaces valid evidence.
- Compatibility aliases emit warnings but preserve existing callers during the
  transition.

### 5.9 Testing

Use existing Python, Node, workflow, and Playwright test surfaces:

- workflow contract tests for mode normalization and verdict policy;
- negative tests proving that ungoverned, unevaluated, vulnerable, missing, or
  malformed evidence cannot pass `readiness-proof`;
- a test proving the same evidence remains a visible non-passing result in
  `live-smoke`;
- scope-match and scope-mismatch tests for cost reconciliation;
- Auto resume tests for stale, malformed, hash-mismatched, and non-passing
  artifacts;
- Canvas projection tests for forecast, actuals, reconciliation, mismatch, and
  readiness states;
- documentation assertions for the smoke/readiness and planner/worker wording.

No pull-request test calls Azure or requires secrets. A manually dispatched live
smoke run is the post-merge operational check.

### 5.10 Acceptance criteria

1. A green `live-smoke` run cannot be mistaken for production readiness.
2. `readiness-proof` fails when safe-check, assurance verdicts, scorecard, or
   required KPI evidence do not pass policy.
3. Actual-cost evidence from another target scope is rejected.
4. Auto documentation and `--commit` behavior agree.
5. Auto resume cannot accept malformed or semantically failed priority evidence.
6. Canvas exposes actuals and reconciliation state without inferring completion
   from file presence alone.
7. Public self-improving claims stop at diagnostics-to-backlog.
8. Existing deterministic CI remains green and no new runner is introduced.
9. Every added or updated artifact is suitable for a public audience.

## 6. Sequencing and review boundaries

Pull request 1 merges first. Pull request 2 branches from the updated default
branch so that it can make only the small documentation adjustments required by
the final workflow semantics.

Pull request 1 primarily owns:

- root documentation;
- example documentation;
- `docs/` HTML, JavaScript, CSS, and data;
- Pages validation workflows and existing browser tests.

Pull request 2 primarily owns:

- the live E2E workflow;
- cost reconciliation and production-readiness scope validation;
- Auto planner and state-contract files;
- Lifecycle Canvas evidence projection;
- narrowly related public wording.

Shared-file edits are minimized. Each pull request uses focused commits and can
be reverted independently.

## 7. Rapid-delivery guardrails

- Pull request 1 is limited to one focused implementation session.
- Pull request 2 is limited to one or two focused implementation sessions.
- If a proposed change requires a new worker, scheduler, evidence service, test
  framework, or multi-day pilot, narrow the public claim instead.
- Existing compatibility is preferred over migrations.
- Optional polish is dropped before an acceptance criterion is weakened.

## 8. Coverage map

| Audit gap | Resolution |
|---|---|
| Stale root documentation | Fix in pull request 1 |
| Pages omit current value and cost evidence | Fix in pull request 1 |
| Example receipt is inconsistent | Fix in pull request 1 |
| Public counts, links, and naming drift | Fix and guard in pull request 1 |
| Full E2E succeeds without semantic readiness | Split smoke and readiness semantics in pull request 2 |
| Scorecard is soft-gated | Require it in `readiness-proof` in pull request 2 |
| Auto is described as a worker | Reframe and align its contract in pull request 2 |
| Resume trusts weak artifact signals | Tighten priority evidence checks in pull request 2 |
| Cost bundle is not target-scope bound | Bind scope in pull request 2 |
| Canvas overstates completion | Add semantic cost and readiness states in pull request 2 |
| Self-improving flow is not closed-loop | Narrow the public claim; autonomous closure remains out of scope |
| Settled live-cost proof needs elapsed time | Keep it as an operational proof activity, not a merge dependency |

## 9. Exit condition

After both pull requests:

- a public reader can understand the value journey and evidence boundaries
  without repository archaeology;
- GitHub Pages and root documentation agree with current behavior;
- workflow names and success states accurately communicate what was proven;
- the highest-risk evidence handoffs fail closed;
- unresolved ambitions are explicit non-goals rather than implied capabilities.
