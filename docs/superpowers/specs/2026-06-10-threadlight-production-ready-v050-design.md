# `threadlight-production-ready` v0.5.0 — cleanup + closure design

**Date:** 2026-06-10
**Prior version:** [v0.4.0 PR #28](https://github.com/aiappsgbb/threadlight-skills/pull/28) — "production onboarding (3-phase)"
**Status:** **Design approved; implementation plan pending (`docs/superpowers/plans/2026-06-10-threadlight-production-ready-v050.md`).**

---

## TL;DR

v0.4.0 flipped the skill from advisory-only to a 3-phase executor and shipped 61 must-fix recipes, a framing wizard, and a CI/CD scaffolder. The PR rubber-duck review surfaced 5 small follow-ups; v0.4.0's "Deferred to v0.5.0+" section named 5 larger pieces of unfinished work. v0.5.0 is the **cleanup + closure** release: it lands the 5 follow-ups, promotes 5 high-signal experimental findings, introduces a small but well-defined per-customer override mechanism, hardens the sibling-skill landing gate, and creates an aspirational field-test phase — **without adding a new pillar or changing the contract shape**.

The big-ticket item from v0.4.0's deferred list — the brand-new `gateway-resilience` pillar — **slides intact to v0.6.0**. Bundling a new pillar with the cleanup work would dilute v0.5.0's message and double the QA surface for no compounding benefit. v0.6.0 will be the "new pillar" release; v0.5.0 is the "pay down the v0.4.0 debt" release.

The seven phases:

| Phase | What it does | Touches |
|---|---|---|
| **A. PR #28 doc-only fixes** | SKILL.md + CHANGELOG SACRED-RULE reword (#29); REL-102 ADO-paragraph strip (#32) | `SKILL.md`, `CHANGELOG.md`, `references/remediation-recipes/REL-102.md` |
| **B. Idempotency + framing tenant-id** | `EXCLUDE_GLOBS` for `docs_text` glob (#30); 8th framing Q `azure_tenant_id` + template plumbing (#33) | `scripts/production_ready.py`, fixture framing JSON files, CI/CD template |
| **C. Sibling-skill gate + flip protocol** | IAM-101/OBS-106 `kind: repo-edit → manual` (#31); extend `test_sibling_skill_map.py` to gate future drift; author flip-protocol runbook | 2 recipes, 1 test, 1 new runbook |
| **D. `customer-overrides.yaml` loader** | Status-flips only; `--customer-overrides PATH` flag; must-fix override → exit 2 loud reject (Bucket 4) | script (~150 LOC), 1 new test, manifest schema bump |
| **E. 5 must-fix experimental promotions** | NET-502 (sibling-skill), EVAL-101/102 (manual), SUP-101 (repo-edit), SRE-103 (repo-edit). Flip + recipe + fixture refresh (Bucket 3) | catalog flip (~5 LOC), 5 new recipes, **5 fixture golden refreshes** |
| **F. SKILL.md + VERSION + CHANGELOG** | Version `0.4.0 → 0.5.0`; SKILL.md mentions `--customer-overrides` + 8th framing-Q; CHANGELOG entry with truthful "Deferred to v0.6.0+" section | SKILL.md, VERSION constant, `CHANGELOG.md` |
| **G. Aspirational field test** | Plan describes a real-customer dogfood run; ship may proceed without it; CHANGELOG notes "deferred to v0.5.1" if skipped | process-only — no shipping code |

---

## Motivation — why "cleanup + closure" deserves its own release

v0.4.0 was a contract-flip release: it changed *what* the skill does. v0.5.0 is the second beat of a two-beat dance — it pays down the small debts that the v0.4.0 PR review surfaced, promotes the experimental findings that have proven high-signal in fixture testing, and adds the smallest defensible per-customer-policy override mechanism that the original SPEC §12 design called for. **None of these are new contracts; they are corrections, promotions, and one additive feature.**

Bundling them with a brand-new pillar (`gateway-resilience`) would mean shipping two releases worth of work as one, with proportionally worse risk. The rule we set in v0.4.0 — "v0.4.0 should be ~70-80% the size of v0.3.0" — held, and the resulting release was reviewable in one sitting. v0.5.0 deliberately targets **~50-60% the size of v0.4.0** so it can land quickly, the cleanup compounds before the next big surface-area addition, and v0.6.0 (gateway-resilience) gets a clean baseline to land against.

The five v0.4.0 follow-up issues (#29-#33) are the strongest argument for this shape: each is small individually, but they each touch a foundation (SKILL.md, the script's `RepoContext`, the recipe-catalog gate, the framing wizard, a specific recipe), and shipping them as a batch before adding new surface area means future v0.6.0 work doesn't inherit the inconsistencies.

---

## Locked invariants (from v0.4.0; v0.5.0 must NOT break)

These are reproduced from the v0.4.0 spec/CHANGELOG and from the user's v0.5.0 brief. Every Phase below has been checked against them; any conflict is called out in the Phase's body.

1. **SACRED ARCHITECTURAL RULE.** The Python script never edits the user's repo for *remediation findings*. The agent does, via apply-plan dispatch. The `--scaffold-cicd` carve-out remains a documented exception, not a precedent. **v0.5.0 adds no new mutation surface to the script.** (Phases A and F explicitly correct the wording so this exception is visible in SKILL.md and CHANGELOG.)
2. **stdlib-only tests.** No pytest, no third-party deps. All 19 existing test files follow this; the ~6 new v0.5.0 test files do too.
3. **`kind` taxonomy stays at 4 values:** `repo-edit`, `sibling-skill`, `manual`, `deferred-to-pipeline`. v0.5.0 does not invent new kinds. (Phase D's `customer-overrides.yaml` flips a finding's *status*, not its recipe's *kind*.)
4. **Recipe markdown shape stays.** YAML front-matter + 4 required `##` sections per `_template.md` + the "Stale-plan check" 5th section added in v0.4.0. Phase C and E author new recipes; Phase A only strips one paragraph from an existing recipe body. No structural change.
5. **GitHub Actions only for `--scaffold-cicd`.** ADO/GitLab CI scaffolding is explicitly deferred to v0.6.0+. (Phase A removes the REL-102 paragraph that violated this in v0.4.0; Phase F's CHANGELOG re-asserts the deferral.)
6. **CHANGELOG "Deferred" section must remain truthful.** Phase F authors v0.5.0's deferred section explicitly.

---

## In scope (the 6 buckets, locked)

### Bucket 1 — PR #28 follow-up issues (Phases A, B, C cover #29-#33)

All five issues land in v0.5.0. Mapping:

| Issue | Phase | Surface |
|---|---|---|
| **#29** SKILL.md self-contradiction on script mutation | A | `SKILL.md` L130-134 reword + `CHANGELOG.md` v0.4.0 entry L16-19 reword |
| **#30** `docs_text` glob self-reference | B | `scripts/production_ready.py` `RepoContext.from_root` adds `EXCLUDE_GLOBS`; new `tests/test_idempotent_assess.py` |
| **#31** sibling-skill map rule violation | C | `IAM-101.md` + `OBS-106.md` flip to `kind: manual`; extend `tests/test_sibling_skill_map.py` to assert the rule; **note in commit message** that issue #31's body had wrong sibling-skill names (correct names: `foundry-rbac-audit` #268, `azure-resource-diagnostics` #271) |
| **#32** REL-102 ADO/GitLab paragraph | A | `references/remediation-recipes/REL-102.md` strip paragraph |
| **#33** Runbook tenant-id placeholder | B | `scripts/production_ready.py` adds 8th framing question `azure_tenant_id`; `_scaffold_cicd` substitutes it into the runbook template; fixture framing JSONs gain the field |

### Bucket 3 — Experimental → committed promotion (Phase E)

All 5 must-fix experimentals promoted:

| ID | Pillar | Tier | Recipe `kind` | Rationale for the kind |
|---|---|---|---|---|
| **NET-502** | network-posture | 5 | `sibling-skill` → `citadel-spoke-onboarding` | Mirrors NET-501 (same sibling skill); already-shipped skill; no new dependency. |
| **EVAL-101** | continuous-evals | 2 | `manual` | Blocks on awesome-gbb#247 (`foundry-evals: last-run introspection API`). Until #247 lands, the recipe describes the manual procedure (locate latest `eval-results-*.json` in repo; confirm `checked_at` within freshness window). |
| **EVAL-102** | continuous-evals | 2 | `manual` | Same upstream block (#247). Recipe describes manually comparing latest eval results against SPEC §9 thresholds. |
| **SUP-101** | supply-chain | 1 | `repo-edit` | Pure static check + repo-edit fix: pin container images by digest in Bicep + `azure.yaml`. Recipe is concrete enough for the agent to apply without an upstream skill. |
| **SRE-103** | sre-handover | 1 | `repo-edit` | Extends OBS-106's pattern (add `Microsoft.Insights/diagnosticSettings` to each critical resource). Concrete repo-edit pattern; well-understood Bicep. |

Each promotion = 5 changes: (1) flip `experimental=True→False` in `FINDING_CATALOG`, (2) remove from `test_experimental_excluded.py` allowlist, (3) author recipe at `references/remediation-recipes/{ID}.md`, (4) refresh fixture goldens (`production-readiness-manifest.json` + `production-readiness-report.md`) for all 5 fixtures (`sample-pilot`, `sample-pilot-citadel`, `sample-pilot-broken`, `sample-pilot-restricted`, `sample-pilot-v4`), (5) extend `test_recipe_catalog.py` (it auto-includes any non-experimental must-fix, so this falls out for free).

### Bucket 4 — SPEC §12 per-customer enforcement (Phase D)

Smallest defensible surface: **status-flips only**.

**New artifact:** `references/customer-overrides-schema.yaml` (documents the override schema) + accepted example at `tests/fixtures/customer-overrides-example.yaml`.

**Override file shape** (lives next to SPEC.md in the customer's repo by convention; passed via `--customer-overrides PATH`):

```yaml
schema: production-readiness-customer-overrides/v1
customer_id: contoso-fsi  # free-form string; logged in manifest for audit
generated_at: 2026-06-15T09:00:00Z
overrides:
  - finding_id: SEC-007
    new_status: pass
    justification: "Customer uses HashiCorp Vault not Azure KV; SEC-007 doesn't apply"
    compensating_control: "Same control via HV; doc at docs/security/hv-secrets.md"
  - finding_id: REL-003
    new_status: pass
    justification: "Customer's RTO is 4h not the threadlight-default 1h"
    compensating_control: "Multi-region cutover validated weekly; runbook at docs/runbooks/cutover.md"
```

**Rules (enforced by the loader + tests):**

- `new_status` must be one of `pass`, `should-fix`, `not-applicable` — explicitly NOT `must-fix`. Attempting to set `must-fix` exits 2 with a clear error citing the original finding ID (a "loud reject"; would-be-attackers can't smuggle in a false-pass).
- The override may only flip a finding whose current status is `must-fix`, `should-fix`, or `fail` to a *less* severe one. You cannot override a `pass` to `must-fix` (out of scope for v0.5.0's mechanism — overrides are *escape valves*, not *demotions*).
- Every override MUST have non-empty `justification` and `compensating_control`. Empty strings or missing keys → exit 2.
- The override is recorded in `production-readiness-manifest.json` under a new top-level key `customer_overrides_applied[]` with the full row + `applied_at` timestamp.
- The markdown report's "Waivers register" section is renamed "Waivers + customer overrides" and includes both as distinct sub-tables.

**Why YAML instead of JSON (matching waivers' JSON):** YAML is friendlier for the customer engineer authoring the override (multi-line justifications, comments allowed). The script loads it via stdlib `tomllib` for `.toml` interpretation? No — there's no stdlib YAML parser. We adopt the same approach as v0.3.0's `references/cicd-templates/` (which are YAML files manipulated as strings, not parsed). For overrides we *do* need to parse, so the implementation will write a **minimal stdlib YAML loader** scoped to the override schema (only the small subset of YAML the schema uses: top-level mapping, list of mappings, string values). This avoids adding a third-party `pyyaml` dependency. The minimal loader is gated by `tests/test_customer_overrides_loader.py` against ~10 inputs.

**CLI surface:** new flag `--customer-overrides PATH`. Absent → no overrides applied (v0.4.0 behavior). Present → load, apply, record in manifest, refuse must-fix override loudly.

### Bucket 5 — Sibling-skill landing flip protocol (covered inside Phase C + Phase F)

Most of this work is subsumed by issue #31's fix (extending `test_sibling_skill_map.py`). The remaining piece is:

- **New runbook:** `references/sibling-skill-flip-protocol.md` — step-by-step for "an awesome-gbb sibling skill landed; how do I flip the corresponding recipe from `kind: manual` to `kind: sibling-skill`?" Includes the test-extension verification and the CHANGELOG note format.
- **CHANGELOG language:** Phase F's "Deferred to v0.6.0+" section explicitly enumerates the 6 awesome-gbb upstream issues (#267-#272) and the recipes they unblock, with the condition: "if shipped by v0.6.0 cut-date, flip via the protocol; else keep as `manual` and re-evaluate v0.6.1."

### Bucket 6 — Aspirational field test (Phase G)

Same shape as v0.4.0's Module G. The plan describes a real-customer dogfood run as Phase G. If it happens, surprises become bug-fix commits inside Phases A-E (or follow-up issues in the v0.5.0 milestone). **If it does not happen by the cut-date, v0.5.0 ships from fixtures alone and the CHANGELOG includes a "Field test deferred to v0.5.1" note.**

This shape is borrowed wholesale from v0.4.0 because it has the property the user explicitly asked for: real-customer hardening is encouraged but not gating; the release ships when the in-scope work passes its tests; field-test surprises flow into v0.5.1 patches.

---

## Out of scope (slides to v0.6.0+)

The "Deferred to v0.6.0+" section the v0.5.0 CHANGELOG will publish (Phase F):

| Item | Why deferred from v0.5.0 |
|---|---|
| `gateway-resilience` pillar (`GW-001..103`, ~25-40 recipes) | A new pillar is net coverage addition, orthogonal to v0.5.0's cleanup theme. v0.6.0 lands the full pillar; v0.5.0 stays focused. |
| 19 remaining experimental should-fix promotions (`NET-103`, `NET-503`, `AGT-102`, `AGT-V4-101`, `IAM-103`, `OBS-103`, `EVAL-103/104/105`, `RAI-102`, `HITL-103`, `SUP-103`, `COST-102/103/104`, `REL-101/104/105`, `MDL-104`) | Most depend on upstream awesome-gbb skills not yet shipped (#247, #248). v0.5.0 promotes only the 5 must-fix; the should-fix set follows once the upstreams land. |
| ADO/GitLab CI scaffold targets (`--scaffold-cicd-target ado`/`gitlab`) | v0.4.0 invariant retained. v0.6.0+ may add `--scaffold-cicd-target {github-actions,azure-devops,gitlab-ci}` if there's pull. |
| Awesome-gbb sibling-skill landings #267-#272 | Tracked separately; conditional flip via the protocol in Phase C's runbook once each lands. |
| Field test of v0.5.0 against a real customer pilot | Aspirational in Phase G; if skipped, deferred to v0.5.1. |
| SPEC §12 surface beyond status-flips (severity downgrades, `applicable_findings` filter, per-customer `defender_plans_required` and `required_policy_ids`) | v0.5.0 ships the smallest defensible mechanism. v0.6.0 may extend if customer demand surfaces. |

---

## Architecture — what changes vs v0.4.0

```
┌─────────────────────────────────────────────────────────────────────┐
│  scripts/production_ready.py        (Python, stdlib only, ASSESSOR)│
│  ───────────────────────────────────────────────────────────────────│
│  • v0.4.0 logic UNCHANGED                                          │
│  • PHASE B: EXCLUDE_GLOBS for docs_text (idempotency)              │
│  • PHASE B: FRAMING_QUESTIONS gains 8th entry (azure_tenant_id)    │
│  • PHASE B: _scaffold_cicd substitutes <tenant-id>                 │
│  • PHASE D: --customer-overrides PATH flag                         │
│  • PHASE D: _load_customer_overrides(path) -> list[dict]           │
│  • PHASE D: _apply_customer_overrides(manifest, overrides) -> None │
│  • PHASE D: _validate_no_must_fix_override(overrides) -> None      │
│  • PHASE E: 5 catalog flips experimental=True→False                │
│  • PHASE F: VERSION = "0.5.0"                                      │
│  • NEVER mutates the user's repo (--scaffold-cicd carve-out only). │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ emits structured artifacts
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  skills/.../SKILL.md                          (AGENT GUIDANCE)     │
│  ───────────────────────────────────────────────────────────────────│
│  • PHASE A: SACRED RULE wording corrected (script-vs-scaffold)     │
│  • PHASE F: mentions --customer-overrides + 8th framing Q          │
│  • PHASE F: VERSION 0.4.0 → 0.5.0 in metadata block                │
│  • 3-phase workflow description UNCHANGED                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ delegates Azure provisioning to
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  awesome-gbb sibling skills          (AZURE PROVISIONING)          │
│  ───────────────────────────────────────────────────────────────────│
│  • No invocation contract changes                                  │
│  • Conditional flip protocol documented (Phase C runbook)          │
│  • PHASE C: IAM-101 and OBS-106 recipes now `kind: manual`         │
│    until #268 and #271 land upstream                               │
└─────────────────────────────────────────────────────────────────────┘
```

**Three layers, three responsibilities, no overlap — same as v0.4.0.** The cleanup/closure shape of v0.5.0 means no architectural diagram changes; the additions are tactical.

---

## Phase A — PR #28 doc-only fixes (`#29`, `#32`)

**Trigger:** baseline cleanup before any other work, so Phases B-G build on consistent docs.

**Flow:**

1. **Issue #29 fix.** Edit `skills/threadlight-production-ready/SKILL.md` lines 130-134 to replace the absolute "never mutates your repo" claim with the carve-out wording from issue #29's "Fix" section. Apply the same correction to `CHANGELOG.md` v0.4.0 entry L16-19.
2. **Issue #32 fix.** Edit `skills/threadlight-production-ready/references/remediation-recipes/REL-102.md` to remove the paragraph (around line 20) describing ADO/GitLab cross-provider porting. Replace with a single sentence: "v0.4.0 scaffolds GitHub Actions only; ADO/GitLab targets deferred to v0.6.0+."
3. **Grep-gate:** add a one-liner assertion to a new test file `tests/test_no_ado_gitlab_in_recipes.py` that greps all `references/remediation-recipes/*.md` for `azure-devops|gitlab-ci|gitlab\.yml` (case-insensitive) and asserts zero matches. This is the catalog gate that prevents future drift back to the v0.4.0 problem.

**No fixture impact, no script change. Pure markdown + 1 small grep test.**

---

## Phase B — Idempotency + framing tenant-id (`#30`, `#33`)

**Trigger:** after Phase A, before recipes/promotions land (so Phase E's fixture refreshes happen with the new idempotency property already in place).

**Flow:**

1. **Issue #30 fix — `EXCLUDE_GLOBS`.**
   - Edit `scripts/production_ready.py` `RepoContext.from_root` (around line 1597, the `docs_text` comprehension) to filter out filenames matching the patterns:

     ```python
     EXCLUDE_GLOBS = (
         "production-readiness-report.md",
         "production-readiness-manifest.json",
         "production-readiness-trend.csv",
         "production-readiness-*.md",
         "production-readiness-*.json",
         "production-readiness-*.csv",
         "production-readiness-apply-plan.json",
         "production-readiness-framing.json",
     )
     ```
   - Hoist `EXCLUDE_GLOBS` to module level so other future globs (`tests_text`, `report_text`) can reuse it.

2. **New test `tests/test_idempotent_assess.py`** — runs `production_ready --target-rg <RG> --target-sub <SUB>` against `sample-pilot-broken` twice in a row; computes `sha256` of the resulting `production-readiness-manifest.json` after each run; asserts the two hashes are identical. Same test against `sample-pilot` (negative-control idempotency). Uses stdlib `subprocess` + `hashlib`; no pytest.

3. **Issue #33 fix — 8th framing question.**
   - Edit `scripts/production_ready.py` `FRAMING_QUESTIONS` (around line 488) to append:

     ```python
     {
         "id": "azure_tenant_id",
         "prompt": "Azure tenant ID for the prod subscription? (GUID; from `az account show --query tenantId -o tsv`)",
         "kind": "text",
         "required": True,
     }
     ```
   - Update `_scaffold_cicd` template substitution (around line 424) so the `<tenant-id>` placeholder in `uami-fedcred-setup.md` is replaced with `framing["azure_tenant_id"]`.
   - Update `test_framing_wizard.py` assertions: `len(qs) == 8` (was 7); ID set now includes `azure_tenant_id`.
   - Update fixture framing JSON files (`sample-pilot/tests/production-readiness-framing.json`, `sample-pilot-citadel/...`, `sample-pilot-broken/...`, `sample-pilot-restricted/...`) to include `azure_tenant_id: "00000000-0000-0000-0000-000000000001"` (canonical zeros-test-GUID).

4. **New assertion in `test_cicd_scaffold.py`:** the rendered `uami-fedcred-setup.md` contains no `<…>` placeholders (regex `<[a-z][a-z0-9-]*-id>` matches zero times).

**Script delta:** ~30 LOC. Test delta: 1 new file + 2 augmented files.

---

## Phase C — Sibling-skill gate + flip protocol (`#31` + Bucket 5)

**Trigger:** after Phase B (recipe edits don't conflict with framing/script work).

**Flow:**

1. **Flip IAM-101 to `kind: manual`.**
   - Edit `skills/threadlight-production-ready/references/remediation-recipes/IAM-101.md`:
     - Front-matter: `kind: repo-edit` → `kind: manual`. Remove `target_file` (manual procedures don't have target files), remove `edit_type`.
     - Add a new top-of-body block:

       ```markdown
       ## Status

       **Blocked on awesome-gbb sibling skill `foundry-rbac-audit` (issue #268).**
       Until that skill ships, this recipe ships the manual procedure below.
       The agent should surface this finding to the operator for explicit
       acknowledgement before any change.

       When `foundry-rbac-audit` ships, follow `references/sibling-skill-flip-protocol.md`
       to convert this recipe to `kind: sibling-skill`.
       ```
     - Preserve the existing body content (the `az role assignment list` bash, the verification block) under the existing `## Target file` (renamed `## Manual procedure`) — it remains useful as the manual fallback.

2. **Flip OBS-106 to `kind: manual`** — same pattern, but the upstream is `azure-resource-diagnostics` (issue #271). Preserve the existing Bicep diagnostic-settings example as the manual fallback (rename `## Target file` → `## Manual procedure` and keep the Bicep verbatim).

3. **Extend `tests/test_sibling_skill_map.py`** — add a new test function:

   ```python
   def test_recipes_for_planned_siblings_must_be_manual():
       """Any recipe whose mapped sibling skill is marked '(planned)' in
       sibling-skills-map.md MUST be kind: manual until the skill ships."""
       # Parse sibling-skills-map.md, find rows with '(planned)'
       # For each, open references/remediation-recipes/{ID}.md
       # Assert front-matter kind == 'manual'
       # If kind != 'manual', fail with: "{ID}: sibling skill {name} is planned (awesome-gbb#{N}) but recipe is kind={kind}. Set kind: manual until the skill ships."
   ```

4. **Author `references/sibling-skill-flip-protocol.md`** — a ~60-line runbook:
   - When to use: an awesome-gbb sibling skill listed as `(planned)` in `sibling-skills-map.md` has shipped.
   - Step-by-step:
     1. Open the recipe for the finding (find via the table).
     2. Front-matter: `kind: manual` → `kind: sibling-skill`. Add `sibling_skill: <skill-name>`.
     3. Replace the `## Manual procedure` section with `## Edit recipe` containing the JSON input contract from the sibling skill's documentation.
     4. Remove `(planned)` tag from `sibling-skills-map.md`.
     5. Run `python3 tests/test_sibling_skill_map.py` — must pass.
     6. Add a row to the active changelog version's "Changed" block: `- {ID} recipe flipped from manual to sibling-skill (awesome-gbb#{N}).`
   - Worked example using a hypothetical NET-503 + a hypothetical newly-landed sibling skill.

5. **Commit message note** for Phase C's commit (or its CHANGELOG fragment): "Issue #31's body cited incorrect sibling-skill names (`azure-uami-bootstrap` #267, `azure-monitor-otel-baseline` #270). The correct names per `sibling-skills-map.md` are `foundry-rbac-audit` #268 and `azure-resource-diagnostics` #271. The underlying rule violation (recipes `kind: repo-edit` instead of `manual`) is real and is fixed by this commit. The issue body should be edited (or this commit's message linked) for archival accuracy."

**Recipe delta:** 2 edits. Test delta: 1 new test function. New file: 1 runbook (~60 lines).

---

## Phase D — `customer-overrides.yaml` loader

**Trigger:** after Phase C (sibling-skill gate work doesn't touch the script's CLI surface; this phase does).

**Flow (script side):**

1. **CLI flag.** Add `--customer-overrides PATH` to the argparse block (around line 4249, alongside `--include-experimental`).

2. **Minimal stdlib YAML loader** — new function `_load_customer_overrides(path: pathlib.Path) -> list[dict]`. Hand-rolled, scoped to the override schema. Parses:
   - Top-level `schema:` (string) — must equal `production-readiness-customer-overrides/v1`.
   - Top-level `customer_id:` (string).
   - Top-level `generated_at:` (ISO-8601 string).
   - Top-level `overrides:` (list of mappings).
   - Each override mapping: `finding_id`, `new_status`, `justification`, `compensating_control`.
   - Comments (`#`) and blank lines tolerated.
   - Multi-line values via `|` (literal) or `>` (folded) — only `|` supported in v0.5.0 (one less ambiguity); document this restriction in `references/customer-overrides-schema.yaml`.
   - Any other YAML feature (anchors, references, flow style, nested mappings beyond depth 2) → exit 2 with a clear "unsupported YAML feature" error.

3. **Validation function** — new `_validate_customer_overrides(overrides: list[dict]) -> None`:
   - Each override has all required keys (`finding_id`, `new_status`, `justification`, `compensating_control`).
   - `new_status` in `{pass, should-fix, not-applicable}` only.
   - **`new_status == "must-fix"` → exit 2 loudly** with: `Customer override for {finding_id} attempts to set new_status=must-fix. Per the loud-reject rule, overrides may only LOWER severity; raising to must-fix is rejected to prevent silent false-pass on the deploy gate. Remove the override or use a different new_status.`
   - `justification` and `compensating_control` non-empty.
   - `finding_id` exists in `FINDING_CATALOG`.

4. **Application function** — new `_apply_customer_overrides(manifest: dict, overrides: list[dict]) -> None`:
   - For each override, find the finding in `manifest["pillars"][].findings[]`.
   - If found, set `finding["status"] = override["new_status"]`.
   - Append a row to `manifest["customer_overrides_applied"]` with the full override + `applied_at: <ISO-8601>`.
   - If not found (override for a finding ID not in this run's manifest), log a warning to stderr and skip; do NOT exit 2 (overrides may be authored against older catalog versions and gradually fall out of use).
   - **Recompute the scorecard** after applying overrides (the apply changes pass/should-fix/must-fix counts).

5. **Markdown report update** — add a new section under "Waivers register" titled "Customer overrides applied" listing `finding_id`, `original_status`, `new_status`, `justification`, `compensating_control` from `customer_overrides_applied[]`.

**Tests (new `tests/test_customer_overrides.py`):**

- `test_loader_parses_minimal_yaml`: 5-line YAML with 1 override → loader returns list of 1 dict with correct keys.
- `test_loader_rejects_missing_schema`: YAML without `schema:` → exit 2.
- `test_loader_rejects_wrong_schema_value`: `schema: customer-overrides/v2` → exit 2 with clear "unknown schema" error.
- `test_loader_rejects_unsupported_yaml_features`: YAML with `&anchor` / `*ref` / `[flow, style]` → exit 2 with "unsupported YAML feature" error.
- `test_validator_rejects_must_fix_loudly`: override with `new_status: must-fix` → exit 2; error message contains the finding ID and the words "loud reject".
- `test_validator_rejects_empty_justification`: override with empty `justification` → exit 2.
- `test_validator_rejects_unknown_finding_id`: override for `XYZ-999` → exit 2 with "not in FINDING_CATALOG".
- `test_apply_flips_status`: finding with `status: should-fix` overridden to `pass` → manifest shows `pass`.
- `test_apply_records_in_manifest`: after apply, `manifest["customer_overrides_applied"]` has 1 row with all override fields + `applied_at` ISO-8601.
- `test_apply_recomputes_scorecard`: 3 should-fix findings overridden to pass → `score.raw.pass` increases by 3, `score.raw.should_fix` decreases by 3.
- `test_apply_skips_missing_finding_with_warning`: override for finding not in current manifest → warning to stderr, no exit; manifest unchanged for other findings.

**Script delta:** ~150 LOC. Test delta: 1 new file with ~10 test functions.

**Documentation:**

- New `references/customer-overrides-schema.yaml` — annotated schema example.
- New `tests/fixtures/customer-overrides-example.yaml` — used by the test file and as a copy-paste starting point for customers.
- SKILL.md gets a short "Customer overrides" section under "How to invoke" (Phase F).

---

## Phase E — 5 must-fix experimental promotions

**Trigger:** after Phase D (overrides land first so promoted findings *can* be overridden if a customer has compensating controls).

**Per-promotion sub-task (all 5 follow this pattern):**

1. **Flip catalog.** Edit `scripts/production_ready.py` line for the finding (lines 657, 725, 726, 759, 799) to set `experimental=True → experimental=False`.

2. **Remove from `test_experimental_excluded.py` allowlist.** The test currently asserts that ~24-26 specific IDs are excluded; after promotion, the count drops by 1 (so the test's expected-list shrinks).

3. **Author recipe** at `references/remediation-recipes/{ID}.md` following the recipe template + Phase C's flip-protocol shape if applicable.

4. **Refresh 5 fixture goldens** — for each of `sample-pilot`, `sample-pilot-citadel`, `sample-pilot-broken`, `sample-pilot-restricted`, `sample-pilot-v4`:
   - `cd references/fixtures/<fixture>`
   - `python3 ../../../scripts/production_ready.py --skip-live --no-rights-probe`
   - `git diff` — should show only the expected changes (1 new finding row in `production-readiness-manifest.json` `pillars[].findings[]`, scoring deltas, 1 new row in the markdown report's pillar section).
   - Commit the refreshed goldens with a clear message: `test: refresh sample-pilot* goldens for {ID} promotion`.

5. **Verify `test_recipe_catalog.py` passes** — it auto-asserts every non-experimental must-fix has a recipe; the new recipe satisfies that. No test code change needed.

**5 promotion sub-tasks (one per ID):**

| ID | New file | Fixture impact |
|---|---|---|
| NET-502 | `references/remediation-recipes/NET-502.md` (kind: sibling-skill → `citadel-spoke-onboarding`) | 5 fixture goldens refreshed (NET-502 will be `not-verified` in non-Citadel fixtures, `must-fix` in `sample-pilot-citadel`) |
| EVAL-101 | `references/remediation-recipes/EVAL-101.md` (kind: manual; "Blocked on awesome-gbb#247") | 5 fixture goldens refreshed |
| EVAL-102 | `references/remediation-recipes/EVAL-102.md` (kind: manual; same upstream block) | 5 fixture goldens refreshed |
| SUP-101 | `references/remediation-recipes/SUP-101.md` (kind: repo-edit; pin Docker images by digest in Bicep + `azure.yaml`) | 5 fixture goldens refreshed; SUP-101 will be `must-fix` in fixtures with un-pinned images |
| SRE-103 | `references/remediation-recipes/SRE-103.md` (kind: repo-edit; add diagnostic settings to each critical resource) | 5 fixture goldens refreshed |

**Total fixture refreshes:** 5 fixtures × 5 promotions = 25 golden-file updates. These can be batched into 5 commits (one per ID), each touching 5 fixture dirs + 1 catalog flip + 1 recipe + 1 test allowlist update.

**Idempotency check (post-Phase B):** after Phase B's `EXCLUDE_GLOBS` fix, re-running the assessor against a fixture twice in a row produces identical manifests. Phase E's fixture refreshes happen with this property already in place, so the goldens are stable across reviewer runs.

---

## Phase F — SKILL.md + VERSION + CHANGELOG (closure)

**Trigger:** after Phases A-E. The closing commits.

**Flow:**

1. **VERSION bump.** Edit `scripts/production_ready.py` line 44 (or wherever VERSION lives) `"0.4.0" → "0.5.0"`. Edit `skills/threadlight-production-ready/SKILL.md` metadata block (line 24-26 area) `version: "0.4.0" → "0.5.0"`. Update `tests/test_version.py` expected value.

2. **SKILL.md additions:**
   - New short section under "How to invoke" titled "Customer overrides" pointing at `--customer-overrides PATH` + the schema file.
   - Update the "Framing-question minimum set" table to show 8 questions (add `azure_tenant_id` row).
   - Update the H1 v0.4.0 callout to point at the v0.5.0 CHANGELOG entry.

3. **CHANGELOG entry** — full v0.5.0 entry following the v0.4.0 entry's format. Sections:
   - **Added (major):** customer-overrides loader + flag; 5 experimental promotions; sibling-skill-flip-protocol runbook.
   - **Added (minor):** 8th framing question; `EXCLUDE_GLOBS` idempotency; `test_idempotent_assess.py`; extended `test_sibling_skill_map.py`; new `test_customer_overrides.py`; new `test_no_ado_gitlab_in_recipes.py`.
   - **Changed (major):** SKILL.md SACRED RULE wording (issue #29); CHANGELOG v0.4.0 entry SACRED RULE wording.
   - **Changed (minor):** IAM-101 and OBS-106 recipes flipped to `kind: manual` (#31); REL-102 ADO/GitLab paragraph removed (#32).
   - **Tests:** ~6 new test files / ~20-30 new test functions; ~5 fixture-golden refreshes per promotion.
   - **Deferred to v0.6.0+:** gateway-resilience pillar; 19 remaining experimental should-fix promotions; ADO/GitLab CI scaffold targets; awesome-gbb sibling-skill landings #267-#272 (conditional flip per protocol); field test if Phase G was skipped.
   - **Migration notes for v0.4.0 users:** `--customer-overrides PATH` is new and opt-in; absent → v0.4.0 behavior. Existing framing JSONs gain `azure_tenant_id` (template will fall back to `<tenant-id>` placeholder for files written before this field exists, so old framings still work but produce un-substituted runbooks — re-author the framing JSON to fix).

---

## Phase G — Aspirational field test

**Trigger:** anytime during Phases A-E; ideally before Phase F's CHANGELOG entry is finalized.

**Flow (process, not code):**

1. Pick 1-2 awesome-gbb pilot customers (user nominates at execution time; spec doesn't name them to keep this doc shareable).
2. Run `python3 scripts/production_ready.py --onboard --framing-file <real-framing.json> --apply-plan-out /tmp/apply-plan.json --customer-overrides <real-overrides.yaml>` against each pilot.
3. Capture surprises (Bicep parse failures, unexpected sibling-skill invocation outcomes, override loader edge cases, fixture-vs-real gap reports) as comments in a shared notes doc.
4. For each surprise:
   - If small (one-line fix, no scope expansion) → fold into the appropriate Phase A-E commit before Phase F.
   - If medium (new test case, recipe tweak, doc clarification) → fold as a follow-up commit in the same phase.
   - If large (new scoping question, schema change, contract revision) → file as a v0.5.1 or v0.6.0 issue; do NOT expand v0.5.0 scope.
5. If Phase G doesn't happen by the cut-date, Phase F's CHANGELOG entry includes a "Field test deferred to v0.5.1" note in the Migration section.

**No new code, no new tests. The Module G shape borrowed from v0.4.0.**

---

## Open questions — answered

The user's v0.5.0 brief explicitly asks me to resolve open questions inline rather than leaving any for the executor. The four questions that surfaced during brainstorming, with their answers:

### Q1 — Is `customer-overrides.yaml` YAML or JSON?

**Answer: YAML, with a minimal stdlib hand-rolled loader scoped to a deliberately small subset.**

Rationale: the customer engineer authoring an override needs multi-line `justification` and `compensating_control` strings, and a comment-friendly format helps with team review. JSON would force escaped newlines and forbid comments. The stdlib lacks a YAML parser, so the Phase D loader is hand-rolled (~80 LOC, ~10 tests) and explicitly rejects all unsupported YAML features (anchors, flow-style, deep nesting) with a clear error so customers can't surprise themselves by writing valid-YAML-but-unsupported-here input. This is the same trade-off `safe-check` made for its `references/` template files — single-purpose, fully tested, no third-party dep.

### Q2 — Where do `customer-overrides.yaml` files live?

**Answer: next to `SPEC.md` in the customer's repo, by convention.**

Rationale: SPEC.md is already the canonical "this is the policy intent for THIS customer" file; overrides are policy extensions and live alongside. The CLI flag `--customer-overrides PATH` is absolute / relative-to-cwd so the operator can put it wherever they want; documentation in `references/customer-overrides-schema.yaml` recommends `specs/customer-overrides.yaml` as the canonical path.

### Q3 — Does the `must-fix` loud-reject also apply when a framing-Q wizard collects similar intent?

**Answer: no — framing answers cannot trigger overrides. The two mechanisms are independent.**

Rationale: the framing wizard collects *posture intent* (which pillars apply at all, who has rights). Overrides flip *individual findings*. There is no cross-talk between them: a framing answer that says "skip the SRE-handover pillar" doesn't override SRE-104; it changes which findings get assessed. A customer-overrides.yaml file is the only way to flip a specific finding's status, and the must-fix loud-reject applies only there.

### Q4 — Do the EVAL-101 and EVAL-102 promotions block on awesome-gbb#247 (foundry-evals last-run API)?

**Answer: no. They ship as `kind: manual` recipes in v0.5.0 and convert to `kind: sibling-skill` via the flip protocol once #247 lands.**

Rationale: `kind: manual` is the correct expression of "this finding is real and must-fix, but the agent shouldn't auto-edit; surface to the operator for explicit action." Operators can manually inspect their latest `tests/eval-results-*.json` file or run `foundry-evals` themselves. When #247 lands, the recipe becomes a `kind: sibling-skill` invocation that drives the inspection automatically. The promotion is decoupled from the upstream landing; the only cost of #247 not having shipped yet is that the agent surfaces a manual action instead of running it. That's the right trade-off per the v0.4.0 sibling-skill contract.

---

## Test strategy

Stdlib only — same as v0.3.0 and v0.4.0. All new tests are `python3 tests/test_*.py`-invokable.

**New test files (6):**

1. `tests/test_idempotent_assess.py` — Phase B (#30): assess twice, manifest sha256 identical.
2. `tests/test_no_ado_gitlab_in_recipes.py` — Phase A (#32): grep all recipes for ADO/GitLab keywords, assert zero hits.
3. `tests/test_customer_overrides.py` — Phase D: ~10 functions covering loader, validator, applier, manifest recording.
4. `tests/fixtures/customer-overrides-example.yaml` — Phase D: shared input for `test_customer_overrides.py` and as a copy-paste customer starter.

**Extended existing test files (3):**

5. `tests/test_framing_wizard.py` — Phase B (#33): 7 → 8 questions, ID set includes `azure_tenant_id`.
6. `tests/test_sibling_skill_map.py` — Phase C (#31): new `test_recipes_for_planned_siblings_must_be_manual` function.
7. `tests/test_cicd_scaffold.py` — Phase B (#33): no `<…>` placeholders remain in rendered runbook.
8. `tests/test_experimental_excluded.py` — Phase E: allowlist shrinks by 5 (NET-502, EVAL-101/102, SUP-101, SRE-103 removed).
9. `tests/test_version.py` — Phase F: expected `"0.5.0"`.

**Existing 19 test files:** all remain green. The cleanup shape means no regression surface — the existing assessment behavior is unchanged except for the 5 newly-scored must-fix promotions, which only affects the 5 fixtures' goldens (refreshed in Phase E).

**Total new + extended test functions:** ~25-30. v0.4.0 added ~30; v0.5.0 is roughly the same test surface, biased toward small extensions rather than new files.

---

## Effort estimate

Comparison to v0.4.0 and v0.3.0:

| Metric | v0.3.0 | v0.4.0 | v0.5.0 (planned) | v0.5.0 ratio vs v0.4.0 |
|---|---|---|---|---|
| New script LOC | ~2,100 | ~600 | ~250 | 0.42× |
| New stdlib tests (files) | 8 | 9 | 4 (+ 5 extended) | 0.4× |
| New test functions | ~47 | ~30 | ~25 | 0.83× |
| New reference docs | ~3 | ~66 (61 recipes + templates) | ~8 (5 recipes + 1 runbook + 1 schema + 1 example) | 0.12× |
| SKILL.md rewrite scope | partial | substantial | small (2 new sections + table extension) | 0.25× |
| New fixtures | 2 | 1 + 4 framing files | 0 new fixtures + 5 fixture-golden refreshes × 5 promotions | similar (refresh cost) |
| Live-Azure surface (new probes) | 5 | 1 | 0 | 0× |
| Net-new finding IDs in catalog | 15 + 24 experimental | 0 | 0 (5 promotions, not new IDs) | 0× |
| CLI flags added | 5 | 5 (`--onboard`, `--framing-file`, `--apply-plan-out`, `--scaffold-cicd`, `--no-rights-probe`, `--repo-full-name`) | 1 (`--customer-overrides`) | 0.2× |

**Overall: substantially smaller code lift, modest test lift, small documentation lift, similar fixture-refresh cost (5 promotions × 5 fixtures = the highest single-task cost). Targets ~50-60% of v0.4.0's total work.** The work centre-of-mass is in Phase E (the 5 fixture-golden refreshes) and Phase D (the YAML loader + ~10 tests).

Phase E's fixture refreshes are mechanical but high-volume; phase E can be parallelized one-promotion-per-subagent if desired (5 promotions × 5 fixture refreshes each).

---

## Cross-references

- v0.4.0 design spec: `docs/superpowers/specs/2026-06-10-threadlight-production-ready-v040-design.md`
- v0.4.0 implementation plan: `docs/superpowers/plans/2026-06-10-threadlight-production-ready-v040.md`
- v0.4.0 PR: [aiappsgbb/threadlight-skills#28](https://github.com/aiappsgbb/threadlight-skills/pull/28)
- v0.4.0 follow-up issues addressed in v0.5.0:
  - [#29](https://github.com/aiappsgbb/threadlight-skills/issues/29) — SKILL.md SACRED RULE contradiction (Phase A)
  - [#30](https://github.com/aiappsgbb/threadlight-skills/issues/30) — `docs_text` self-reference idempotency bug (Phase B)
  - [#31](https://github.com/aiappsgbb/threadlight-skills/issues/31) — sibling-skill map rule violation (Phase C; **issue body has incorrect sibling-skill names — corrected in Phase C commit message**)
  - [#32](https://github.com/aiappsgbb/threadlight-skills/issues/32) — REL-102 ADO/GitLab paragraph (Phase A)
  - [#33](https://github.com/aiappsgbb/threadlight-skills/issues/33) — runbook tenant-id placeholder (Phase B)
- Awesome-gbb upstream issues tracked for conditional flip (Phase C + Phase F):
  - [#267](https://github.com/aiappsgbb/awesome-gbb/issues/267) `azure-backup-readiness` → REL-007
  - [#268](https://github.com/aiappsgbb/awesome-gbb/issues/268) `foundry-rbac-audit` → IAM-101
  - [#269](https://github.com/aiappsgbb/awesome-gbb/issues/269) `foundry-iq` knowledge-index PE → MDL-010 (already shipped, conditional)
  - [#270](https://github.com/aiappsgbb/awesome-gbb/issues/270) `foundry-memory`/`foundry-hosted-agents` retention → MDL-011
  - [#271](https://github.com/aiappsgbb/awesome-gbb/issues/271) `azure-resource-diagnostics` → OBS-106 (+ SEC-106)
  - [#272](https://github.com/aiappsgbb/awesome-gbb/issues/272) `azure-monitor-alert-baseline` → SRE-104
- v0.4.0 sibling-skills map: `skills/threadlight-production-ready/references/sibling-skills-map.md`
- v0.4.0 recipe template: `skills/threadlight-production-ready/references/remediation-recipes/_template.md`
- v0.4.0 fixtures: `skills/threadlight-production-ready/references/fixtures/sample-pilot{,-citadel,-broken,-restricted,-v4}/`
- v0.5.0 implementation plan: `docs/superpowers/plans/2026-06-10-threadlight-production-ready-v050.md` (written after this spec is approved)
