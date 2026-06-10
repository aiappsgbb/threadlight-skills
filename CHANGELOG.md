# Changelog

All notable changes to this repository are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions on individual skills live in their `SKILL.md` `metadata.version`
field.

## [Unreleased]

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
- **14 new non-experimental finding IDs** for governance + capacity surface area:
  `REL-007` / `REL-008` (restore-drill freshness + live recovery-point sampling),
  `GOV-101..105` (Defender for AI Services / KV / Servers + Secure Score floor + top recommendations),
  `GOV-201..203` (required Policy assignments + compliance + ASB-v3 initiative),
  `MDL-009/010/011` (project-level RBAC + private-endpointed knowledge index + thread retention),
  `MDL-110` / `MDL-111` (TPM headroom + Foundry account quota).
  Plus 23 additional IDs flagged `experimental: True` (excluded from scoring
  by default — see Changed section below).
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
