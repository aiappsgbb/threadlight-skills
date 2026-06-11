# Changelog

All notable changes to this repository are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions on individual skills live in their `SKILL.md` `metadata.version`
field.

## [Unreleased]

## 2026-06-11 — `threadlight-production-ready` v0.5.0 — "production-ready cleanup"

Closes the v0.5.0 cleanup buckets on top of v0.4.0's 3-phase onboarding
flow: customer-specific policy overrides, an 8-question framing wizard,
idempotent assessor input discovery, GitHub-Actions-only CI/CD scope, and a
truthful v0.6.0+ deferral boundary.

### Added

- **Per-customer overrides** (`--customer-overrides PATH`) with
  `references/customer-overrides-schema.md`,
  `references/customer-overrides.example.yaml`, status-flip audit fields
  (`override_customer`, `override_reason`), and must-fix bypass rejection.
- **8th framing question**: `azure_tenant_id`, a required UUID tying the
  production subscription to its tenant (closes #33).
- **Assessor-output exclusions** via `EXCLUDE_GLOBS`, so generated
  production-readiness manifests/reports/trends are not re-ingested on the
  next run.
- **Sibling-skill flip runbook** at
  `references/runbooks/sibling-skill-flip-protocol.md` for promoting manual
  recipes once upstream awesome-gbb skills land.
- **New stdlib tests**:
  `tests/test_customer_overrides.py`, `tests/test_idempotent_assess.py`,
  `tests/test_no_ado_gitlab_in_recipes.py`,
  `tests/test_sacred_rule_wording.py`, and `tests/test_script_strings.py`.
- **5 experimental promotions** to must-fix coverage: `NET-502`,
  `EVAL-101`, `EVAL-102`, `SUP-101`, and `SRE-103`.

### Changed

- `IAM-101` and `OBS-106` recipes now declare `kind: manual` until their
  upstream sibling-skill contracts land.
- SKILL.md and this CHANGELOG now acknowledge the sacred architectural rule
  with the documented `--scaffold-cicd` exception while preserving
  agent-driven remediation (closes #29).
- `REL-102` and the catalog gate were stripped of ADO/GitLab CI/CD guidance;
  v0.5.0 remains GitHub Actions only (closes #32).
- Stale `deferred to v0.5.0` wording now points to `v0.6.0+`.
- `SUP-101` and `SRE-103` catalog titles were re-aimed at the new repo-edit
  gates (`SUPPORT.md` and `docs/sre/runbook.md`).
- `_load_customer_overrides` is now strict-mode: rejects tab indentation,
  block scalars (`|`/`>`), unquoted `<space>#` in values, duplicate
  top-level keys, duplicate `recipe_id` entries, and unknown top-level keys.
  `_validate_customer_overrides` rejects unknown per-override keys.
  Rationale: silent text loss on an override's `reason` field would corrupt
  the audit trail.
- `--customer-overrides` now rejects combinations where the overrides would
  be silently dropped (`--remediate`, `--onboard`, standalone
  `--scaffold-cicd` without a manifest). Exits 2 with a loud error.

### Deferred to v0.6.0+

- **Bucket 2 / `gateway-resilience` pillar** — cross-region failover scoring,
  ~25-40 new recipes, and a new framing question; deferred to ship as its
  own themed release.
- **ADO and GitLab `--scaffold-cicd` targets** — v0.5.0 remains GitHub
  Actions only pending field-test demand.
- **4 remaining sibling-skill flips**: awesome-gbb#267 (`REL-007`), #269,
  #270, and #272 (`SRE-104`); gated on upstream landings, then the
  sibling-skill flip protocol applies.
- **~19 remaining experimental recipes** still marked `"experimental": True`
  in `FINDING_CATALOG`; promote one-by-one as field signal arrives.
- **Real-customer field-test execution** — Phase G is protocol-only
  (`references/field-test-protocol.md`); actual customer engagement is
  post-v0.5.0 follow-up work.

## 2026-06-10 — `threadlight-production-ready` v0.4.0 — "production onboarding (3-phase)"

Flips the skill from a pure assessor into a 3-phase production-onboarding
executor: Phase 1 (Assess, Python script) → Phase 2 (Refine + Deploy,
agent-driven via Edit/Write + sibling skills) → Phase 3 (CI/CD Handoff,
scaffolded GitHub Actions workflow + central-team UAMI runbook). The
sacred architectural rule from v0.3.0 holds for remediation findings:
fixes are dispatched through the Copilot agent so `git diff` + PR review
remain the audit trail. The lone Python write exception is the
`--scaffold-cicd` opt-in flag, which writes 2 deterministic template files
into the customer repo so the production-onboarding pipeline can run. No
`--apply FINDING_ID` flag was added or planned.

### Added — major
- **3-phase onboarding flow.** Phase 1 (Assess) emits an `apply-plan.json`
  describing what needs to change and how. Phase 2 (the Copilot agent)
  consumes the plan and dispatches by `kind`: `repo-edit` → Edit/Write,
  `sibling-skill` → invoke another skill via the Skill tool, `manual` →
  surface to the user, `deferred-to-pipeline` → leave for Phase 3.
  Phase 3 renders `.github/workflows/azd-deploy-prod.yml` plus a
  central-team UAMI/FedCred runbook from templates.
- **Apply-plan output** (`--apply-plan-out PATH`) with 4 `kind` values:
  `repo-edit`, `sibling-skill`, `manual`, `deferred-to-pipeline`.
  Includes `manifest_sha256` so Phase 2 detects a stale plan.
- **Framing wizard** (`--onboard` for TTY, `--framing-file PATH` for
  headless) — 7 questions covering target subscription, RG, posture,
  provisioning rights, central platform team, restricted-environment
  flag, and CI/CD target.
- **61 must-fix remediation recipes** under
  `references/remediation-recipes/{ID}.md` (one per non-experimental
  finding ID). Each declares `kind` and provides exact edit /
  sibling-skill / manual / pipeline instructions. Catalog drift gated
  by `tests/test_recipe_catalog.py`.
- **Provisioning-rights probe + phase-decision banner.** Live
  `az role assignment list` against the target RG; classifies operator
  rights as `full | constrained | none | unknown` and decides Phase-2
  mode (`self-service` vs `central-team handoff` vs `blocked`). Skippable
  via `--no-rights-probe`. Restricted-environment flag (Framing-Q6)
  overrides rights class and forces central-team handoff.
- **CI/CD scaffold** (`--scaffold-cicd`) — renders
  `.github/workflows/azd-deploy-prod.yml` (deploy + smoke-failover +
  threadlight-postdeploy jobs) and
  `docs/threadlight-cicd/central-team-uami-readme.md` (UAMI provisioning
  runbook) from templates under `references/cicd-templates/`. Federated
  credentials only — no long-lived secrets in GitHub.
- **Sibling-skill invocation map** at `references/sibling-skills-map.md`
  — single source of truth for which awesome-gbb skill the agent
  invokes when a recipe is `kind: sibling-skill`.
- **Restricted-environment demotion.** When framing declares
  `restricted_environment: true`, `build_apply_plan` demotes any
  `kind: repo-edit` recipe to `kind: manual` so the agent surfaces the
  task to the user instead of editing the repo — honoring the
  central-team-owns-onboarding contract.
- **`sample-pilot-restricted` fixture** modeling a central-team-owned
  citadel-spoke environment (Reader-only rights, restricted=true,
  provisioning_rights=false).

### Changed — major
- **SKILL.md contract flipped.** v0.3.0's "recommends, never executes"
  → v0.4.0's "Phase 1 assesses, Phase 2 (agent) executes, Phase 3
  (script) scaffolds." The Python script remains assessor-only by
  design — execution flows through Copilot agent tools so `git diff`
  stays the audit trail.

### Added — minor
- `VERSION = "0.4.0"` pin (`tests/test_version.py`).
- CLI flags: `--onboard`, `--framing-file`, `--apply-plan-out`,
  `--scaffold-cicd`, `--no-rights-probe`, `--repo-full-name`.
- Per-pillar cross-reference footer from each
  `references/pillars/{N}-*.md` to its remediation recipes.
- `phase_decision`, `rights_probe`, and `version` keys added to
  `production-readiness-manifest.json` when `--framing-file` is set.
- `build_apply_plan` walks `pillars[].findings[]` when top-level
  `findings` is absent (back-compat preserved); includes
  `not-verified` items alongside `fail`/`warn` (real gaps in
  restricted environments).

### Tests
- 9 new stdlib test files (~30 new test functions). Continues v0.3.0's
  stdlib-only commitment — no pytest, no third-party deps. Total now
  19 test files covering apply-plan schema, bicep graph, CI/CD
  scaffold, diff mode, end-to-end, evidence freshness, experimental
  exclusion, framing wizard, gate preview, phase decision, recipe
  catalog, remediate renderer, rights probe, scoring,
  secure-score-floor, sibling-skill map, smoking-gun regression, trend
  CSV, version pin.

### Deferred to v0.5.0+
- `gateway-resilience` pillar (`GW-001..103`) — not yet authored.
- Full SPEC §12 per-customer enforcement of
  `defender_plans_required` + `required_policy_ids`.
- 24 experimental stub closures (continuous-evals EVAL-101..105, NSG
  flow logs, cost actuals, AppInsights replay).
- Awesome-gbb upstream landings for #267–272 (recipes currently
  `kind: manual` until each sibling skill ships).
- Real-customer field test of the 3-phase flow (currently exercised
  only via the 4 fixtures).

### Migration notes for v0.3.0 users
- No breaking script-CLI changes for existing
  `--target-sub`/`--target-rg`/`--static`/`--gate-preview` usage; v0.3.0
  invocations continue to work unchanged. To opt into the v0.4.0 flow,
  add `--onboard` (TTY) or `--framing-file <path>` (headless), plus
  `--apply-plan-out` to capture the agent dispatch plan and
  `--scaffold-cicd` to render the Phase 3 templates.
- Existing fixtures (`sample-pilot`, `sample-pilot-citadel`,
  `sample-pilot-broken`) score identically under v0.3.0 invocation; the
  new `version`/`rights_probe`/`phase_decision` keys only appear when
  `--framing-file` is set.

## 2026-06-10 — `threadlight-production-ready` v0.3.0 — "the real way to land in prod"

The first end-to-end overhaul of the production-readiness skill since
its initial drop. Replaces the v0.2.0 regex-over-Bicep-text parser with
a real ARM-graph (`BicepGraph` compiles via `az bicep build` and walks
the resulting JSON), wires 5 long-stubbed live probes, marks 24
unimplemented stubs as `experimental: true` (excluded from scoring
unless `--include-experimental`), adds 15 new non-experimental finding
IDs covering Defender / Policy / Foundry RBAC / quota / restore-drill
surface area, fixes the scoring bug that gave `not-verified` 50%
credit, and ships new operator surface area (`--diff`,
`--gate-preview`, `--remediate`, `--trend-csv`).

Closes the regression that motivated the rewrite:

> A pilot whose `infra/main.bicep` mentions `Microsoft.Network/virtualNetworks`
> only inside a comment scored as **`READY WITH WAIVERS`** under v0.2.0. The
> regex parser matched substrings; the comment-only fixture passed
> `NET-001` (vnet exists), `NET-002`, and `SEC-001`. Under v0.3.0
> the same fixture exits with `🔴 NOT READY`, `raw=31%`,
> `verification_debt=42`, and 12 of 16 critical IDs in a non-pass state.
> Pinned by `tests/test_smoking_gun_regression.py`.

### Added

- **`BicepGraph` parser** (`scripts/production_ready.py:878-1020`) — compiles
  every top-level `infra/main.bicep` via `az bicep build --stdout`, walks
  `Microsoft.Resources/deployments` to flatten module-nested resources,
  exposes `by_type / has_type / count / property_values`.
- **`PrerequisiteError`** — raised when the `az` or `bicep` CLI is missing
  or every top-level main fails to compile. `main()` catches it and exits 2
  with an `az bicep install` hint. **There is no regex fallback.**
- **5 live probes wired** (were no-op stubs in v0.2.0):
  `OBS-106` (Foundry diag settings → LA), `OBS-102` (KQL trace freshness),
  `SEC-106` (KV diag settings → LA), `SRE-104` (RG activity-log alerts),
  `NET-501` (Citadel APIM Access Contract via `TL_CITADEL_HUB_RG`).
- **15 new non-experimental finding IDs** for governance + capacity surface area:
  `REL-007` / `REL-008` (restore-drill freshness + live recovery-point sampling),
  `GOV-101..105` (Defender for AI Services / KV / Servers + Secure Score floor + top recommendations),
  `GOV-201..203` (required Policy assignments + compliance + ASB-v3 initiative),
  `MDL-009/010/011` (project-level RBAC + private-endpointed knowledge index + thread retention),
  `MDL-110` / `MDL-111` (TPM headroom + Foundry account quota).
  Plus 23 additional IDs flagged `experimental: True` (24 total experimental
  in catalog: 23 newly retired in v0.3.0 + 1 inherited from v0.2.0; all
  excluded from scoring unless `--include-experimental` — see Changed
  section below).
- **`--diff <prior.json>`** — prints a markdown diff of pillar / finding deltas
  between two manifests; useful in PR comments. Pinned by `test_diff_mode.py`.
- **`--gate-preview`** — exit 2 if any must-fix would block go-live. The flag
  the v0.3.0 GitHub Actions workflow uses on `push`. Pinned by `test_gate_preview.py`.
- **`--remediate <FINDING_ID>`** — prints the bash recipe from
  `references/remediation-recipes.yaml` to stdout. 12 recipe entries covering
  the highest-frequency findings. Pinned by `test_remediate_renderer.py`.
- **`--trend-csv`** (default `tests/production-readiness-trend.csv`) — appends
  a single row per run for trending score / verification_debt / posture /
  recommendation. Header on first write, append after. Pinned by `test_trend_csv.py`.
- **OIDC GitHub Actions recipe** (`references/ci-github-actions.yml`) —
  rewritten to use `azure/login@v2` federated credentials, `az bicep install`,
  `--gate-preview` on push, `--trend-csv` upload, `TL_CITADEL_HUB_RG` passthrough.
- **`references/remediation-recipes.yaml`** — 12 bash recipes (one per high-impact
  finding ID).
- **`references/azd-hooks/install-azd-hook.sh`** — idempotent installer that
  registers the skill as an `azd` postdeploy hook.
- **Two fixture directories**:
  - `references/fixtures/sample-pilot-broken/` — the smoking-gun regression
    fixture (comment-only Bicep) the suite asserts must NEVER be reported
    as production-ready.
  - `references/fixtures/sample-pilot-citadel/` — Citadel-spoke happy-path
    fixture for posture-resolution coverage.
- **Test suite** (9 files, 57 test functions, stdlib-only):
  - `test_bicep_graph.py` — 7 BicepGraph contract tests
  - `test_smoking_gun_regression.py` — 5 end-to-end regression tests
  - `test_scoring_no_verification_inflation.py` — 10 scoring tests
  - `test_experimental_excluded.py` — 3 experimental-filter tests
  - `test_diff_mode.py` — 6 `--diff` renderer tests
  - `test_gate_preview.py` — 3 `--gate-preview` tests
  - `test_remediate_renderer.py` — 5 `--remediate` tests
  - `test_trend_csv.py` — 5 trend-csv tests
  - (`test_evidence_freshness.py` — 13 pre-existing tests, still green)

### Changed

- **Scoring contract** — `not-verified` is now `+0` (was `+2` in v0.2.0, which
  inflated unverifiable pilots to ~50%). The unverified count is surfaced as
  a first-class `verification_debt {total, by_pillar}` block in the manifest
  + exec-summary banner in the markdown report. Pin: `test_scoring_no_verification_inflation.py`.
- **24 finding IDs** flagged `experimental: True` in `FINDING_CATALOG` and
  filtered from both the score and the pillars block by default (23 newly
  added or re-flagged in v0.3.0, 1 inherited from v0.2.0). Opt in with
  `--include-experimental`. The manifest carries the flag as a top-level field
  so SE-side trends can tell "with" from "without".
- **`SKILL.md` over-claims** — 6 sentences that said the skill _does_ a thing
  it actually only _plans to_ rewritten to match the catalog.
- **`docs/production-readiness.md`** — H1 callout summarising the v0.3.0
  contract changes, scoring delta, smoking-gun regression.
- **`skills/threadlight-production-ready/SKILL.md` `metadata.version`** —
  `1.0.0` → `0.3.0` (semver reset: the skill was self-tagged 1.0.0 at the
  v0.2.0 cut even though scoring was demonstrably wrong; v0.3.0 is the
  first version with a tested scoring contract).

### Fixed

- The comment-only-Bicep smoking-gun regression (see above).
- `not-verified` 50% credit (scoring contract above).
- `_build_manifest` no longer leaks experimental findings into
  `pillars[].findings` when the flag is off — they were excluded from
  the *score* but still surfaced, misleading operators.
- `_build_manifest` now records `include_experimental` as a top-level
  manifest field so trend rows can be partitioned by it.

### Cross-references

Closes / progresses the following [`awesome-gbb`](https://github.com/aiappsgbb/awesome-gbb) issues. Each upstream issue is the canonical owner of a helper or skill this PR's tier-1 checks vendor a stand-in for until the upstream lands:

- [#245 — foundry-observability: reusable KQL probe helpers](https://github.com/aiappsgbb/awesome-gbb/issues/245) — `OBS-102` is a vendored copy of the trace-freshness probe
- [#246 — citadel-spoke-onboarding: hub-side Access Contract probe helper](https://github.com/aiappsgbb/awesome-gbb/issues/246) — consumed by `NET-501` via `TL_CITADEL_HUB_RG`
- [#247 — foundry-evals: last-run introspection API](https://github.com/aiappsgbb/awesome-gbb/issues/247) — `EVAL-101..105` are experimental stubs waiting on this
- [#248 — foundry-agt: canonical capability-detector](https://github.com/aiappsgbb/awesome-gbb/issues/248) — `AGT-102`, `AGT-V4-101` are experimental stubs waiting on this
- [#249 — NEW SKILL azure-policy-compliance](https://github.com/aiappsgbb/awesome-gbb/issues/249) — `GOV-201/202/203` are tier-1 stand-ins until the dedicated skill ships
- [#250 — azure-sre-agent: threadlight-production-handover recipe](https://github.com/aiappsgbb/awesome-gbb/issues/250) — consumed by `SRE-102/103/104` paths
- [#251 — NEW SKILL azure-quota-preflight](https://github.com/aiappsgbb/awesome-gbb/issues/251) — `MDL-110/111` are tier-1 stand-ins until the dedicated skill ships
- [#252 — NEW SKILL azure-defender-posture](https://github.com/aiappsgbb/awesome-gbb/issues/252) — `GOV-101..105` are tier-1 stand-ins until the dedicated skill ships
