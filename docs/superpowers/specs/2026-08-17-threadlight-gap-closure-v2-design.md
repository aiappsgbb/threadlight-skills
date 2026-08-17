# Threadlight Gap Closure v2 - Design

- **Status:** Approved
- **Date:** 2026-08-17
- **Repository:** `aiappsgbb/threadlight-skills`
- **Delivery shape:** one pull request in this repository
- **Out of scope:** changes, issues, or pull requests in `aiappsgbb/agentic-loop`

## 1. Context

The proposed gap-closure pack identifies useful product gaps, but it was written
against an older repository baseline. The current repository already contains 17
skills, plugin version `1.11.0`, a tested local runtime-policy authority, and
`threadlight-consumption-iq` pre-sales estimation without a deployed pilot.

The implementation must therefore close the remaining gaps without recreating
features that already exist or adopting conventions that conflict with the
repository:

- skill descriptions follow the enforced 1024-character loader limit, not a
  500-character limit;
- there is no repository-wide 5000-character `SKILL.md` body limit;
- the existing pre-sales projector path is extended and reused;
- `runtime-policy.json` remains a Threadlight-local authority;
- no change is made to `agentic-loop`, even where its defaults differ;
- deterministic tests remain offline and secret-free.

The work is delivered as one PR because the features share manifests, production
readiness checks, lifecycle UI, documentation, and release metadata. The PR is
organized as independently reviewable commits so each subsystem can be validated
before integration.

## 2. Goals

1. Add a Cowork-friendly qualification entry point that reuses the existing cost
   engine.
2. Make cost projections complete-by-construction: every detected cost meter is
   either priced or explicitly not priceable.
3. Add an evidence-based mock-to-real integration workflow.
4. Add grounding verification as a pipeline leg without duplicating Foundry IQ
   or evaluation engines.
5. Replace declared-only load assumptions with measured load evidence.
6. Add compatibility and preview-drift scanning with reviewable migration plans.
7. Strengthen the local runtime-policy contract with lifecycle metadata and
   local drift protection.
8. Update repository documentation, GitHub Pages, downloadable Cowork assets,
   and the Threadlight Lifecycle Canvas in the same PR.

## 3. Non-goals

- Modifying `aiappsgbb/agentic-loop`.
- Claiming that another repository consumes Threadlight's runtime policy.
- Automatically applying upgrade migration plans.
- Writing customer-specific integration mappings.
- Automatically running customer-facing, live, or cost-bearing legs from
  `threadlight-auto`.
- Redesigning the GitHub Pages visual system.
- Adding a new test runner.
- Persisting prompts, completions, access tokens, customer payloads, or other
  sensitive content in evidence manifests.

## 4. Delivery and repository coordination

The implementation PR uses these logical commits:

1. Runtime-policy and shared manifest contracts.
2. Shared cost API, qualification flow, and Consumption IQ completion.
3. Connect leg.
4. Ground leg.
5. Load-test leg.
6. Upgrade leg.
7. Safe-check, production-ready, and auto integration.
8. Documentation, GitHub Pages, Canvas, Cowork packages, and release metadata.

PR #111 is active in parallel and owns `skills/threadlight-deploy/**`.
This work does not modify functional deploy files. `CHANGELOG.md` is the only
known shared file; the branch must integrate PR #111's final base before the
release-metadata commit.

## 5. Shared architecture

### 5.1 Pipeline

```text
qualification profile
  -> qualification/sizing-manifest.json
  -> SPEC load_profile and selectors
  -> deploy/runtime configuration
  -> cost/connect/ground/load/upgrade evidence
  -> production-ready findings
  -> lifecycle canvas and GitHub Pages guidance
```

The JSON manifests are the integration boundary. Skills do not reach into each
other's internal state.

### 5.2 Shared manifest envelope

Every new manifest has a JSON Schema and the following top-level envelope:

```json
{
  "schema": "threadlight.<leg>/v1",
  "tool_version": "0.1.0",
  "generated_at": "2026-08-17T00:00:00Z",
  "freshness": {
    "valid_for_hours": 720,
    "source_oldest_at": "2026-08-17T00:00:00Z"
  },
  "status": "complete",
  "findings": []
}
```

`status` is one of:

- `complete`: all required work ran and the manifest passed schema validation;
- `partial`: one or more checks are `not-verified`, but no unsafe operation was
  represented as successful;
- `aborted`: execution stopped because of a safety, budget, permission, or input
  guard.

Writers validate the complete payload and use an atomic replace. A malformed
manifest never replaces the prior valid evidence.

### 5.3 Orchestration

`threadlight-auto` recognizes each new manifest and recommends the next action.
It does not automatically run:

- real integration probes;
- OBO verification;
- ACL probes with multiple principals;
- load tests;
- production endpoint tests;
- compatibility checks that require a live external source.

These legs remain manual handoffs because they can mutate customer configuration,
consume budget, require customer identity, or depend on external availability.

### 5.4 Production-readiness integration

New checks are added to the existing appropriate pillars. No new pillar is added,
because additional pillars dilute the score and duplicate existing ownership.

The only new mandatory safe-check behavior is a verified contradiction:

```text
SPEC integration availability = real
AND effective runtime MCP endpoint = mock
```

Missing evidence remains `not-verified`. A failed executed security or contract
probe can be `must-fix`.

## 6. Component design

### 6.1 `threadlight-qualify`

`threadlight-qualify` is a new conversational entry skill for early customer
qualification. It does not introduce a second cost engine.

#### Inputs

- free-text customer brief;
- workload class;
- annual transaction volume and named transaction unit;
- pages per transaction and document origin;
- turns per conversation and estimated tokens per turn;
- peak concurrency and business-hours profile;
- number of sites or entities;
- data residency and pinned region;
- optional current cost and handling time.

The runtime constraint is no Azure context, no deployment, no Bicep discovery,
and no `az`/`azd`. The existing repository's Cowork guidance permits a Python
stdlib helper, so "no shell" is interpreted as "no infrastructure shell
dependency", not "no deterministic computation".

#### Reuse boundary

Consumption IQ exposes a stable importable API for:

- load-profile validation;
- normalized resource topology;
- projection;
- discount application;
- manifest rendering.

Both the existing `estimate` command and `threadlight-qualify` call this API.
Cowork packaging includes the required shared cost modules so a standalone
download does not depend on a repository checkout.

#### Outputs

- `qualification/sizing.md`;
- `qualification/sizing-manifest.json`;
- `qualification/discovery.md`;
- `qualification/roi.md` only when current-cost inputs are supplied.

Every assumption carries provenance:

- `user-supplied`;
- `derived`;
- `fixture`;
- `live`.

The sizing manifest includes a normalized load profile that
`threadlight-design` can import into SPEC section 12 without repeating the
interview.

Citadel hub sizing is produced from a versioned, dated reference fixture that
records its source. The output distinguishes hub sizing from application sizing.

### 6.2 `threadlight-consumption-iq` vNext

#### Meter coverage registry

Discovery produces normalized `meter_demands`, not only ARM resources. This is
necessary because semantic ranker, embeddings, web grounding, and document
processing can be billable without appearing as distinct ARM resources.

Each demand contains:

```json
{
  "meter_kind": "content-understanding-extraction",
  "source": "spec.selector",
  "source_ref": "SPEC section 7b",
  "volume_driver": {
    "unit": "pages",
    "monthly_quantity": 100000
  }
}
```

The registry maps each supported `meter_kind` to one projector. A detected meter
with no projector is emitted, never dropped.

#### Projectors

Add independently tested projectors for:

- Content Understanding extraction;
- Content Understanding contextualization;
- Document Intelligence classic;
- Speech/audio/video;
- embeddings;
- AI Search agentic retrieval;
- AI Search semantic ranker;
- grounding with Bing or web-search tools.

AI Search serverless is a supported variant. If Azure Retail Prices does not
return a usable rate, the projector emits `not-priceable` with the reason. It
does not substitute a guessed rate.

#### Pricing and model catalog

Projector formulas contain no prices. Pricing comes from:

1. Azure Retail Prices live data;
2. a dated, versioned fixture;
3. `not-priceable`.

The hard-coded `gpt-4o` to `gpt-4o-mini` swap map is removed. A local dated model
catalog describes supported comparison families, cached-input rates, batch
discounts, throughput planning values, source, and review date. The catalog
emits a warning after 90 days.

No claim is made that `agentic-loop` supplies this catalog, because no such
catalog was verified on its default branch during design.

#### Additional behavior

- `--from-profile <file>` skips Bicep and Azure discovery.
- PTU hourly, one-month reservation, and one-year reservation scenarios are
  represented separately.
- Break-even output is an explicit inequality.
- Every complete projection exposes cost per declared transaction unit.
- A total that contains `not-priceable` lines is marked `incomplete`; it is not
  presented as the complete monthly bill.

#### `COST-007`

`threadlight-production-ready` adds `COST-007`:

- detected meter without a projector or usable price: `must-fix`;
- insufficient selectors to decide whether a meter exists: `not-verified`;
- every detected meter priced: `pass`.

### 6.3 `threadlight-connect`

`threadlight-connect` owns the evidence and mechanics of replacing a mock
integration with a real one. It does not own customer-specific field mapping.

#### Phases

1. `inspect`: find SPEC integrations marked `mock`, effective MCP configuration,
   tool implementations, and sample data.
2. `contract`: derive fields, types, cardinality, and requiredness into
   `specs/data-contracts/<system>.json`.
3. `generate-tests`: create executable conformance tests in the generated
   project.
4. `verify`: execute tests against the real endpoint when credentials are
   available.
5. `plan`: emit a file-by-file apply plan.
6. `apply`: only under `--apply`, update SPEC and MCP configuration after a
   successful conformance result.
7. `emit`: write `specs/connect-manifest.json`.

#### States

- `mock`;
- `real-unverified`;
- `real-verified`;
- `real-drift`.

A conformance failure prevents the swap. OBO verification must demonstrate
user-scoped behavior before the integration can become `real-verified`.
Publishing or republishing an agent triggers revalidation of required roles
against the current agent identity.

### 6.4 `threadlight-ground`

`threadlight-ground` owns grounding evidence, not the underlying retrieval or
evaluation engines.

#### Composition

- provisioning and retrieval remain delegated to `foundry-iq`;
- quality scoring remains delegated to `threadlight-evals`;
- the new leg coordinates probes, records evidence, and translates results into
  production-readiness findings.

#### SPEC contract

The SPEC gains a knowledge-source section containing:

- source name and type;
- permission model;
- refresh cadence;
- expected citation behavior;
- expected refusal behavior;
- principals used for ACL verification.

#### Evidence

`specs/ground-manifest.json` records:

- source inventory;
- ACL probe results for at least two principals;
- citation-to-retrieved-source validation;
- unsupported-answer refusal behavior;
- retrieval quality baseline reference;
- subqueries per query;
- tokens per retrieval.

Missing principals or permissions produce `not-verified`. An executed probe that
returns unauthorized content, or returns an identical protected set to
incompatible principals, produces `must-fix`.

Fan-out and token metrics are passed to Consumption IQ through the manifest
contract, not through direct module coupling.

### 6.5 `threadlight-loadtest`

`threadlight-loadtest` converts a declared load profile into measured evidence.

#### Safety guards

- a declared spend ceiling is mandatory;
- predicted token spend above the ceiling aborts before traffic starts;
- a production endpoint requires an explicit production confirmation flag;
- generated temporary credentials are forbidden;
- temporary scaffolding is removed on success, failure, and interruption.

#### Measurements

- achieved requests per second;
- p50, p95, and p99 latency;
- error rate;
- first-request latency after scale-to-zero;
- time to scale under ramp;
- observed tokens per request.

The selected load adapter is recorded in the manifest. The skill uses an
available supported adapter and emits `not-verified` when none is available
instead of installing a surprise dependency.

Declared and observed values are both retained in SPEC section 12. The delta is
the finding. Observed token usage can replace the estimate in the cost projection
only when the load manifest is fresh and complete.

### 6.6 `threadlight-upgrade`

`threadlight-upgrade` detects compatibility drift and produces a mechanical
migration plan.

#### Compatibility matrix

A versioned reference matrix records:

- hosted-agent protocol;
- agent framework;
- toolbox;
- skill publication;
- governance profile;
- model families;
- version or preview state;
- source;
- last reviewed date;
- review window.

#### Scanner

The scanner reports:

- dependencies behind the matrix;
- prerelease pins;
- preview surfaces;
- matrix staleness;
- preview-to-GA review triggers;
- runtime-policy decisions whose expiry condition has fired.

Live checks use source adapters and are fixture-driven in tests. An unavailable
source produces `not-verified`, not a guessed latest version.

The output is an ordered file-by-file migration plan. The skill never edits the
project automatically.

### 6.7 Local runtime policy vNext

`skills/threadlight-design/references/runtime-policy.json` remains the sole
runtime-selector authority for this repository.

The contract adds:

- semantic contract version;
- decision date;
- rationale;
- review-by date or explicit expiry condition;
- permanent marker where appropriate;
- default and EU-residency region policy;
- source lineage for borrowed decisions or text.

Tests assert that:

- every route uses supported selectors;
- every non-permanent decision has a review or expiry condition;
- design, deploy, and auto reference the canonical local path;
- no Threadlight skill states a contradictory default;
- generated examples match the selected tuple;
- lineage claims name an existing source and pinned revision, or are removed.

Cross-repository alignment is explicitly not an acceptance criterion for this PR.

## 7. Error and severity model

### 7.1 Input errors

Required qualification or volume fields fail fast. The skill does not generate
a projection with defaulted required values.

### 7.2 Missing permissions

Permission failures in advisory legs produce `not-verified` with the missing
capability and remediation. They do not crash the pipeline and do not become
success-shaped fallbacks.

### 7.3 Negative evidence

An executed probe that proves an unsafe or contradictory condition can produce
`must-fix`, including:

- SPEC says real while runtime remains mock;
- a detected billable meter is absent from the projection;
- ACL-protected grounding returns unauthorized content;
- integration conformance fails after a proposed real swap;
- measured behavior materially violates an explicit required constraint.

### 7.4 Aborted live operations

Budget, production-confirmation, and safety guards produce an `aborted`
manifest. Partial measurements are retained only as diagnostics and are not fed
into cost or readiness scoring.

## 8. Documentation and GitHub Pages

Documentation is part of the feature definition, not follow-up work.

Update:

- `README.md`: skills table, count, and pipeline flow;
- `THREADLIGHT.md`: chain, entry-skill picker, manual-handoff rules, count;
- `CHANGELOG.md`: one coherent Added/Changed entry;
- `plugin.json`: version `1.12.0`, description, keywords, count;
- GitHub Pages funnel/pre-sales content for `threadlight-qualify`;
- production content for connect, ground, and load testing;
- lifecycle/maintenance content for upgrade;
- generated process-library assets rather than a parallel manual catalog;
- Threadlight Lifecycle Canvas registry, artifact projection, and next actions;
- Cowork download packaging for qualification and its shared cost modules.

No visual redesign is included. Existing styles and interaction patterns remain.

## 9. Testing

### 9.1 Unit tests

Add focused tests for:

- every new projector and price-unavailable path;
- meter discovery and registry completeness;
- profile validation and assumption provenance;
- contract extraction and conformance-state transitions;
- ACL, citation, and refusal result mapping;
- load budget and production guards;
- compatibility-matrix parsing and staleness;
- runtime-policy lifecycle validation;
- manifest schema validation and atomic writes.

### 9.2 Golden and cross-skill tests

Add deterministic golden tests for:

- a no-repo pre-sales qualification;
- a document pipeline whose extraction meter is the top line when priced;
- the same pipeline becoming explicitly incomplete when the meter cannot be
  priced;
- connect, ground, load, and upgrade manifests consumed by
  production-ready;
- safe-check detecting real-vs-mock contradiction;
- Lifecycle Canvas projecting all new evidence and next actions.

### 9.3 CI

Every new pytest suite gets a hard-fail step in
`.github/workflows/python-pytest.yml`. Existing Node, Blueprint, Canvas, and
Playwright suites are extended. Tests are deterministic and do not call Azure,
customer endpoints, or external documentation.

Existing v1 cost manifests remain readable. Schema changes are additive where
possible, and compatibility tests pin that behavior.

## 10. Acceptance criteria

1. The repository exposes 22 Threadlight skills and all counts agree.
2. Qualification runs without Azure, deployment, Bicep, `az`, or `azd`.
3. Qualification and post-deploy cost paths use the same projector API.
4. Every detected billable meter is priced or explicitly `not-priceable`.
5. A document workload never silently omits its extraction meter.
6. Static model-swap pairs are removed in favor of a dated local catalog.
7. `COST-007` reports missing coverage without changing unrelated gates.
8. A real integration cannot be marked verified without conformance and
   identity evidence.
9. Grounding evidence distinguishes missing proof from an executed ACL failure.
10. Load evidence is budget-capped and cannot target production accidentally.
11. Upgrade output is plan-only and reports stale compatibility data.
12. Every local runtime-policy decision has lifecycle metadata or is permanent.
13. No Threadlight-local consumer contradicts the canonical runtime policy.
14. README, THREADLIGHT, changelog, plugin metadata, GitHub Pages, generated
    process-library data, Cowork packages, and Lifecycle Canvas are current.
15. CI remains offline, deterministic, secret-free, and green.

## 11. Known residual risk

`agentic-loop` may continue to state defaults that differ from Threadlight. This
PR intentionally does not change or govern that repository. Threadlight's local
contract, tests, generated artifacts, and documentation must remain internally
consistent regardless of that external divergence.
