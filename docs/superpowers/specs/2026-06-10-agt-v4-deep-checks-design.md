# AGT v4-preview deep checks — design note

**Date:** 2026-06-10
**Issue:** [aiappsgbb/threadlight-skills#23](https://github.com/aiappsgbb/threadlight-skills/issues/23)
**Status:** **Implemented** in [PR …](#) (Closes #23). 5 static + 1 live findings shipped; 2 informational checks deferred.

---

## TL;DR

`threadlight-production-ready` had a `--agt-profile v4_preview` CLI flag wired since PR #21, but no v4-specific check logic — `v4_preview` silently emitted the same findings as `v3_7`. Issue #23 asks for v4-specific deep checks gated on the upstream AGT v4 surface stabilizing.

This design note captures (a) the upstream recon that proved AGT v4 is shipped, (b) the rationale for which v4 capability checks were implemented and which were deferred, and (c) reproducibility commands so a future implementer can re-verify against post-v4 AGT releases (v5 etc.) without re-doing the recon work.

---

## Upstream recon evidence

Verified 2026-06-10 against `microsoft/agent-governance-toolkit` and `aiappsgbb/awesome-gbb`:

| # | Source | Finding | Citation |
|---|---|---|---|
| 1 | `microsoft/agent-governance-toolkit` git tags | `v4.1.0` exists at commit `0de71ca`; HEAD `730ffbb` (`chore: widen structlog upper bound to <27.0`) | Local clone `git tag -l` |
| 2 | `microsoft/agent-governance-toolkit/CHANGELOG.md` | `## [4.0.0] - 2026-06-01` entry shipped 2026-06-01 with extensive deltas (5-distribution reorg, dynamic policy conditions, TEE keystore, wire-protocol-aware policy, credential injection/offload, sandbox/shell governance, LangGraph adapter, test replay engine, expanded audit fields, Entra-signed JWT for AgentMesh, LotL prompt prevention rules) | `CHANGELOG.md` lines 14-80 |
| 3 | `microsoft/agent-governance-toolkit/agent-governance-python/**/pyproject.toml` | **All 30+ Python distributions** pinned at `version = "4.1.0"` — not aspirational | `find agent-governance-python -name pyproject.toml \| xargs grep '^version'` |
| 4 | `microsoft/agent-governance-toolkit/policy-engine/core/tests/fixtures/manifests/canonical-all-interventions.yaml` | Canonical v4 policy shape uses `agent_control_specification_version: "0.3.1-beta"` + `intervention_points:` block with 7 named points (`agent_startup`, `input`, `pre_model_call`, `post_model_call`, `pre_tool_call`, `post_tool_call`, `output`) | File content |
| 5 | `aiappsgbb/awesome-gbb` PR #242 | Body explicitly says: *"AGT 4.0.0 (45-package → 5-distribution structural reorg). Issue #190 stays open per coordinator decision option B."* — refers to the **awesome-gbb `foundry-agt` wrapper skill** still pinned at AGT 3.7.0, NOT to v4's existence | PR body text |
| 6 | `aiappsgbb/awesome-gbb` repo grep | Zero matches for `v4_preview\|AGTv4\|agt-v4\|"AGT v4"` across the awesome-gbb wrapper. Every "v4" appearance traces back to threadlight itself | GitHub code search |

**Interpretation:** AGT v4 is shipped upstream as concrete Python distributions and CI artefacts. The awesome-gbb wrapper skill `foundry-agt` is intentionally lagging at 3.7.0 — but the threadlight pillar doc (`references/pillars/02-agent-governance.md` line 35-44) explicitly says *"Rather than asserting version == '3.7.x', infer capabilities from artefacts present"*. So threadlight can detect AGT v4 directly in customer code without waiting for the wrapper to absorb v4.

---

## Verified v4 surface (each check anchors on one of these)

| Signal | Found at | Notes |
|---|---|---|
| **v4 distribution names** | `requirements*.txt`, `pyproject.toml`, `package.json` in customer repo | Five canonical names: `agent-governance-toolkit-{core,runtime,sre,cli}` + `agent-governance-toolkit[full]` (meta) |
| **ACS policy schema** | `**/policy*.y*ml`, `**/policies/**/*.y*ml` in customer repo | Top-of-file `agent_control_specification_version:` key + `intervention_points:` block with at least one of seven canonical intervention keys |
| **Dynamic policy conditions** | Same scope as above, OR `src/**/*.py` import of `agent_os.policies.dynamic_context` | Keys `time_window`, `day_of_week`, `cost_per_window`, `token_count_per_window` (commit `3218c2e`, PR #2870) |
| **Composite GitHub Action pin** | `.github/workflows/*.yml` | `uses: microsoft/agent-governance-toolkit/action@vX` MUST include `toolkit-version:` input in v4 per `BREAKING_CHANGES.md` |
| **Expanded audit fields** | `tests/**/*.json`, `docs/**/*.json` verifier artefacts in customer repo | `arguments_hash`, `approver_did`, `policy_version`, `issued_at`, `completed_at` (per CHANGELOG line 34) |

---

## What was implemented

Six new finding IDs gated to `agt_profile == "v4_preview"` — all live entirely inside the new `_check_agt_static_v4` / `_check_agt_live_v4` functions and are never emitted by `v3_7` or `none` paths.

| ID | Severity | Tier | Gate |
|---|---|---|---|
| `AGT-V4-001` | must-fix (tri-state) | 0 static | `not-applicable` if no AGT deps at all; `pass` if any v4 distribution name found; `must-fix` if AGT deps declared but only v3.7-shape names |
| `AGT-V4-002` | should-fix (tri-state) | 0 static | `not-applicable` if no policy files; `pass` if ACS keys + intervention_points + at least 1 canonical intervention; `should-fix` if policy files exist but lack ACS schema |
| `AGT-V4-003` | informational | 0 static | Always `pass` / `not-applicable`; never `must-fix`. Records "detected" or "not detected" for dynamic policy conditions (time/cost/quota) |
| `AGT-V4-006` | conditional (tri-state) | 0 static | `not-applicable` if no AGT composite action used; `pass` if action used WITH `toolkit-version:` pin; `must-fix` if action used WITHOUT pin |
| `AGT-V4-007` | should-fix (tri-state) | 0 static | `not-verified` if no verifier JSON files exist; `pass` if JSON has ≥3 of 5 v4 audit fields; `should-fix` if JSON exists but lacks v4 fields |
| `AGT-V4-101` | should-fix | 2 live | `_not_verified` stub: tier 2 KQL probe over `policy_version` field in App Insights denial events (parallel to AGT-102 stub) |

**Detection regex changes** in `_detect_agt_profile`: replaced the speculative `AGTv4|agt\.v4|stream_policy|structured_rai` regex (PR #21 guesses — `stream_policy` and `structured_rai` do not appear anywhere in the AGT v4 CHANGELOG or source code) with three grounded regexes:

```python
V4_DIST_REGEX = re.compile(r"agent-governance-toolkit(?:-(?:core|runtime|sre|cli)|\[full\])", re.IGNORECASE)
V4_POLICY_REGEX = re.compile(r"^agent_control_specification_version\s*:|^intervention_points\s*:", re.MULTILINE)
V4_DYNAMIC_REGEX = re.compile(r"\btime_window\b|\bcost_per_window\b|\btoken_count_per_window\b|agent_os\.policies\.dynamic_context")
```

Applied to scoped file globs only: `requirements*.txt`, `pyproject.toml`, `package.json`, `**/policy*.y*ml`, `**/policies/**/*.y*ml`, `.github/workflows/*.yml`, `src/**/*.py`. **Explicitly excluded:** `docs/**`, `README.md`, `*.md`, `specs/SPEC.md` — to prevent docs prose from flipping `auto` detection.

---

## What was deferred

Two v4 capability checks were scoped out of this first cut per rubber-duck critique:

| Deferred check | Why |
|---|---|
| `AGT-V4-004` (modernized PII regex `\b\d{3}[\s.-]?\d{2}[\s.-]?\d{4}\b`) | Too narrow to ship as `should-fix` — customers using first-party PII detectors (Presidio, AGT built-in detectors, etc.) would fail the check despite having compliant PII handling. Needs a more sophisticated "legacy dash-only regex detected" lint that's symmetric with declarative-detector detection. Defer to a follow-up PR. |
| `AGT-V4-005` (first-party CLI integration packages `agent-governance-{copilot-cli,claude-code,opencode,antigravity-cli}`) | Informational-only and niche — only relevant for customers shipping a custom CLI on top of AGT. Adds finding-table noise without action value for most pilots. Defer. |

Both are tracked as TODO comments in `_check_agt_static_v4` next to where they would slot in.

---

## Detection regex rationale (replacing PR #21's speculative anchors)

The original `_detect_agt_profile` regex was:

```python
v4_regex = re.compile(r"AGTv4|agt\.v4|stream_policy|structured_rai", re.IGNORECASE)
```

This was added in PR #21 as a placeholder. Cross-referencing against the actual AGT v4 CHANGELOG and source:

| PR #21 anchor | In v4 CHANGELOG? | In v4 source? | Status |
|---|---|---|---|
| `AGTv4` | No | No | Speculative — never used as a literal |
| `agt.v4` | No | No | Speculative — actual Python module path is `agent_governance_toolkit_core` etc. |
| `stream_policy` | No | No | Speculative — does not exist as a policy field or API |
| `structured_rai` | No | No | Speculative — does not exist; RAI is part of `output_policy` intervention point |

All four anchors were guesses. Replacing them with the three grounded regexes above eliminates the risk of false-positive v4 detection on PR #21's placeholder strings (which a customer might use in their own scratch code or docs).

---

## Fixture design — sibling vs variant decision

**Chosen: sibling directory** `skills/threadlight-production-ready/references/fixtures/sample-pilot-v4/`.

Rejected alternative: extending `sample-pilot/` with a `policies-v4/` variant subdir.

Reasons:

1. **v4 requires its own `requirements.txt` shape** (different distribution names). Sharing a `requirements.txt` across v3.7 / v4 fixtures couples test outcomes — flaky and hard to reason about.
2. **v4 adds new file types** (`.github/workflows/agt-verify.yml` with composite action, `policies/governance.yaml` with ACS schema, `tests/verifier-report.json` with audit fields) that don't exist in v3.7. Coupling them risks v3.7 checks accidentally picking up v4 artefacts.
3. **Smoke validation** is cleaner with separate fixtures: run the CLI against `sample-pilot` and `sample-pilot-v4` independently and compare manifests file-by-file. With a variant subdir we'd need `--policy-dir` flags or env overrides that don't exist in the CLI today.
4. **The existing `sample-pilot` fixture has no AGT artefacts at all** (it's intentionally a failing pilot per its own README) — so it's a baseline for "all AGT-001..006 fail". `sample-pilot-v4` complements it as a "v4 baseline where AGT-001..006 + AGT-V4-* pass".

The new fixture contains 11 files mirroring `sample-pilot/`'s skeleton plus the 5 v4-specific artefacts (see `references/fixtures/sample-pilot-v4/README.md` for the file inventory).

---

## Recon reproducibility

A future implementer working on v5-preview checks (or re-verifying these v4 checks against a later AGT release) should re-run:

```bash
# 1. Clone upstream AGT (HTTPS works even when GitHub API SAML-blocks the org token)
git clone --depth 50 https://github.com/microsoft/agent-governance-toolkit.git /tmp/agt-recon
cd /tmp/agt-recon

# 2. Confirm latest tag
git tag -l | grep -E '^v[0-9]+' | tail -10

# 3. Verify per-distribution Python versions (should all match the latest tag)
find agent-governance-python -name pyproject.toml | xargs grep -H '^version' | sort -u

# 4. Find canonical policy fixtures
find . -name "*.yaml" -path "*manifests*" -o -name "canonical*.yaml" | head

# 5. Find dynamic-condition source (or its successor in a later release)
git --no-pager log --oneline --all -- 'agent-governance-python/agent-os/src/agent_os/policies/dynamic_context.py'

# 6. Check awesome-gbb wrapper pin (lags by design)
gh api repos/aiappsgbb/awesome-gbb/contents/skills/foundry-agt/references/upstream-pin.md --jq .content | base64 -d

# 7. Sanity-check that threadlight regex doesn't match anything new in upstream
cd /tmp/agt-recon
grep -rn "agent_control_specification_version" --include="*.yaml" | head
grep -rn "intervention_points:" --include="*.yaml" | head
grep -rn "agent-governance-toolkit-core\|agent-governance-toolkit-runtime" --include="*.toml" | head
```

If a v5 release adds a new policy schema marker (e.g., `acs_version: 2.x`), repeat the recon, add the new signal to `V4_POLICY_REGEX` (or introduce a `V5_POLICY_REGEX` sibling), and bump the catalog with `AGT-V5-*` entries gated on a new `--agt-profile v5_preview` choice.

---

## Pre-existing issues NOT addressed by this PR

These were observed during recon but are intentionally out of scope for issue #23:

1. **`FINDING_CATALOG` ↔ `references/pillars/02-agent-governance.md` title drift** for AGT-003 / 004 / 005 / 006 / 101:
   - Catalog says `AGT-003 = "OWASP ASI 2026 verifier referenced"`, pillar doc says `AGT-003 = "Policy artefact has a version field"`
   - Catalog says `AGT-004 = "AGT version pinned (not floating)"`, pillar doc says `AGT-004 = "Verifier output artefact present"`
   - Catalog says `AGT-005 = "AGT policy covers tool calls + prompt shields"`, pillar doc says `AGT-005 = "OWASP / ASI 2026 evidence"`
   - Catalog says `AGT-006 = "AGT telemetry sink configured"`, pillar doc says `AGT-006 = "--agt-profile auto control flag"`
   - Catalog says `AGT-101 = "Workload identity scoped to AGT-required RBAC"`, pillar doc says `AGT-101 = "AGT sidecar present in deployed ACA"`
   - Worth a separate cleanup issue. The PR for #23 adds the v4 deltas section without renumbering the v3.7 entries.

2. **`sample-pilot/` fixture has zero AGT artefacts** so AGT-001..006 always fail against it. Intentional today (the fixture's README says it's "intentionally a failing pilot") but worth a coverage-fixture issue separate from #23.

3. **`production_ready.py:44 VERSION = "0.1.0"` vs `SKILL.md:25 metadata.version: "1.0.0"`** drift. Pre-existing.

4. **Pillar doc lines 86-94 "AGT v3.7 → v4 transition" prose** says "Awesome-gbb is mid-update for AGT v4" — was accurate at PR #21 time but stale today. This PR rewrites that section as part of the in-scope pillar-doc update (only because it sits adjacent to the new v4 deltas section).

5. **CLI flag name `v4_preview`** — defensible to keep because (a) backward-compat with PR #21, (b) AGT pyproject classifier is `Development Status :: 4 - Beta`, (c) ACS spec version is `0.3.1-beta`. Could rename to `v4` in a follow-up once ACS goes stable.
