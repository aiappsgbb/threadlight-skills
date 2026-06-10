# `threadlight-production-ready` v0.4.0 — production onboarding (3-phase) design

**Date:** 2026-06-10
**Prior version:** [v0.3.0 PR #27](https://github.com/aiappsgbb/threadlight-skills/pull/27) — "the real way to land in prod"
**Status:** **Design approved; implementation pending.**

---

## TL;DR

v0.3.0 made the assessment honest: 151 finding IDs, real ARM-graph parsing, live probes, scoring contract pinned by 61 stdlib tests. But the contract was advisory-only — *"This skill recommends, never executes"* (SKILL.md line 81). The operator was left to chase ~10 sibling skills by hand to remediate.

**v0.4.0 flips the contract from "recommends" to "guided 3-phase onboarding"**, while preserving the safety property that earned trust in v0.3.0: **the Python script never mutates threadlight artifacts directly.** All edits flow through the Copilot agent (using its native Edit/Write tools, with diffs and per-edit confirmation) or through awesome-gbb sibling skills (using their existing safety machinery). The script's role expands modestly — it adds an interactive framing-question wizard, emits a structured apply-plan, and computes a phase decision — but its discipline (stdlib-only, assess-only, never mutate) is unchanged.

The three phases:

| Phase | What it does | Who executes | When emitted |
|---|---|---|---|
| **1. Assess** | Interactive framing Qs + existing v0.3.0 scorecard + new apply-plan emission | Python script | Always (default behavior when `--onboard` is set) |
| **2. Refine + (optional) Deploy** | Agent applies repo-edit recipes from `references/remediation-recipes/{ID}.md`; for findings needing Azure provisioning AND operator has rights, agent invokes the relevant awesome-gbb sibling skill | Copilot agent + sibling skills | When apply-plan has any actionable findings |
| **3. CI/CD handoff** | Agent writes `.github/workflows/azd-deploy-prod.yml` + central-team onboarding README from templates (UAMI + federated credential pattern) | Copilot agent | When Phase 2 deferred any provisioning (no rights, restricted env, or operator opted out) |

The 5-item "starting menu" from the v0.4.0 planning brief (new `gateway-resilience` pillar, SPEC §12 per-customer enforcement, 24 experimental stubs closure, 6 upstream awesome-gbb landings, real-customer field test) **slides to v0.5.0+** with one exception: the field test is folded into v0.4.0 as Module G. The framing-question wizard partially subsumes §12 enforcement; v0.5.0 can decide whether the remaining §12 surface is still worth a dedicated module.

---

## Motivation — why a contract flip in v0.4.0

v0.3.0's exit interview surfaced a recurring operator complaint:

> "I ran production-ready, got a great scorecard, and then I spent two days figuring out which sibling skill fixes each finding and how to invoke it. The skill knew what was broken; it didn't help me fix it."

The advisory-only contract was defensible in v0.3.0 because the assessment itself wasn't trusted yet — the v0.2.0 regex-over-Bicep-text bug ([smoking-gun regression](../../../skills/threadlight-production-ready/tests/test_smoking_gun_regression.py)) had to be fixed before anyone wanted the skill DOING anything. v0.3.0 fixed the assessment. v0.4.0 closes the gap to action.

The constraint that v0.3.0 earned the right to keep:

> The script's discipline — stdlib-only, single-file (≈3800 LOC), no Azure mutations, never fails a build — is what made it safe to ship. v0.4.0 preserves all four properties.

The contract change is in **SKILL.md**, not in the script. SKILL.md becomes a 3-phase agent-facing workflow: the script remains an assessor that emits structured artifacts (manifest + report + trend + new `apply-plan.json`); the agent (Copilot CLI / Claude / GPT) reads those artifacts and orchestrates the actual remediation using its native Edit/Write tools and the Skill tool to invoke sibling skills.

---

## Architecture — separation of concerns

```
┌─────────────────────────────────────────────────────────────────────┐
│  scripts/production_ready.py        (Python, stdlib only, ASSESSOR)│
│  ───────────────────────────────────────────────────────────────────│
│  • Existing v0.3.0 logic (scorecard, manifest, report, trend)      │
│  • NEW: framing-question collector (TTY or --framing-file)         │
│  • NEW: apply-plan emitter (apply-plan.json)                       │
│  • NEW: provisioning-rights probe (extends _probe_tiers)           │
│  • NEW: phase-decision banner                                      │
│  • NEVER mutates threadlight artifacts. No Bicep edits, no YAML    │
│    rewrites, no markdown patching. Only reads.                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ emits structured artifacts
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  skills/.../SKILL.md                          (AGENT GUIDANCE)     │
│  ───────────────────────────────────────────────────────────────────│
│  3-phase workflow described as instructions the Copilot agent     │
│  reads at runtime:                                                │
│  • Phase 1: invoke script, present scorecard, lock in framing Qs  │
│  • Phase 2: read apply-plan; for each finding, use Edit/Write to  │
│    apply repo-edit recipes; for sibling-skill recipes, invoke     │
│    via Skill tool with confirmation                               │
│  • Phase 3: if any deferred, write CI/CD workflow + UAMI runbook  │
│    from templates using Write tool                                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ delegates Azure provisioning to
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  awesome-gbb sibling skills          (AZURE PROVISIONING)          │
│  ───────────────────────────────────────────────────────────────────│
│  • citadel-spoke-onboarding (Access Contract, APIM, JWT)          │
│  • foundry-observability (App Insights, OTel, diag settings)      │
│  • azure-sre-agent (incident automation)                          │
│  • foundry-agt (AGT middleware)                                   │
│  • citadel-hub-deploy (hub provisioning — rarely from prod-ready) │
│  • ...                                                            │
│  Inherit their own safety machinery; invoked via Skill tool.      │
└─────────────────────────────────────────────────────────────────────┘
```

**Three layers, three responsibilities, no overlap.** The script never tries to be the agent; the agent never tries to be a sibling skill; sibling skills never try to assess. Each layer's safety story is the safety story it was designed for.

---

## Phase 1 — Assess (interactive framing + apply-plan emission)

**Trigger:** `python production_ready.py --onboard` (or, for CI, `--onboard --framing-file tests/production-readiness-framing.json`).

**Flow:**

1. Script reads existing SPEC §12 via the current `_scan_spec_section_12` parser.
2. For each missing or ambiguous framing field, the script asks the operator a framing question (TTY mode) or fails with a clear error citing the missing field (non-TTY, no `--framing-file`).
3. Framing answers are merged with SPEC §12 evidence and written to `tests/production-readiness-framing.json` for re-runs (committed alongside the manifest).
4. Existing v0.3.0 assessment runs unchanged: scorecard, manifest, report, trend.
5. **NEW:** apply-plan emitter walks each finding in the manifest; for every must-fix or operator-elevated should-fix, looks up the per-finding recipe in `references/remediation-recipes/{ID}.md`; emits an entry in `tests/production-readiness-apply-plan.json`.
6. **NEW:** provisioning-rights probe (`_probe_provisioning_rights`) checks whether the operator has `Microsoft.Authorization/roleAssignments/write` (or equivalent) on the target subscription/RG.
7. **NEW:** phase-decision banner prints to stdout, telling the operator which phase comes next.

### Framing-question minimum set (v0.4.0)

Seven questions, ordered by sequencing dependency:

| # | Question | Default if not asked | Used by |
|---|---|---|---|
| 1 | Target Azure subscription ID | `AZURE_SUBSCRIPTION_ID` env, then `az account show --query id` | Tenant gate, live probes |
| 2 | Target resource group name | `azure.yaml` `resourceGroup`, then `RESOURCE_GROUP` env | Live probes, apply-plan scoping |
| 3 | Target posture (citadel-spoke / agt / standard / hybrid) | SPEC §12 `target_posture`; else `standard-ai-gateway` | Pillar scoring + recipe selection |
| 4 | Does the operator have provisioning rights on this RG? (yes/no/unknown — script verifies if `unknown`) | `unknown` (script probes) | Phase 2 vs Phase 3 decision |
| 5 | Is a central platform team responsible for prod deployments? (yes/no) | `no` | Phase 3 README content |
| 6 | Restricted environment (no console access from operator's machine)? (yes/no) | `no` | Phase 2 vs Phase 3 decision |
| 7 | CI/CD target for Phase 3 (gh-actions / azure-devops / none) | `gh-actions` | Phase 3 template selection |

> **Why 7 and not 15+:** Comprehensive framing questions can come in v0.5.0 once we have field feedback on what genuinely matters. Seven is the minimum to decide between the three phase paths.

### Apply-plan JSON schema

Path: `tests/production-readiness-apply-plan.json` (committed — see Open Question 1 below).

```json
{
  "schema": "production-readiness-apply-plan/v1",
  "generated_at": "2026-06-10T18:32:14+00:00",
  "manifest_sha256": "<sha256 of corresponding manifest.json>",
  "framing": { "target_posture": "citadel-spoke", "has_provisioning_rights": true, "restricted_env": false, "ci_cd_target": "gh-actions", "central_team": false },
  "phase_decision": "phase-2-then-3",
  "actions": [
    {
      "finding_id": "NET-002",
      "severity": "must-fix",
      "kind": "repo-edit",
      "recipe_path": "references/remediation-recipes/NET-002.md",
      "target_files": ["infra/main.bicep"],
      "depends_on": [],
      "verification_command": "az bicep build --file infra/main.bicep --stdout > /dev/null"
    },
    {
      "finding_id": "NET-501",
      "severity": "must-fix",
      "kind": "sibling-skill",
      "recipe_path": "references/remediation-recipes/NET-501.md",
      "sibling_skill": "citadel-spoke-onboarding",
      "sibling_invocation_hint": "Run citadel-spoke-onboarding with target_hub_rg=<env:TL_CITADEL_HUB_RG>",
      "depends_on": ["NET-002"],
      "requires_provisioning_rights": true
    },
    {
      "finding_id": "OBS-106",
      "severity": "must-fix",
      "kind": "sibling-skill",
      "recipe_path": "references/remediation-recipes/OBS-106.md",
      "sibling_skill": "foundry-observability",
      "depends_on": [],
      "requires_provisioning_rights": true,
      "deferred_to_phase_3_if": "no_provisioning_rights"
    },
    {
      "finding_id": "SEC-004",
      "severity": "should-fix",
      "kind": "manual",
      "manual_instructions_path": "references/pillars/04-secrets.md#sec-004",
      "reason": "no automated recipe available; document rotation policy in SPEC manually"
    }
  ]
}
```

Four `kind` values:

- `repo-edit` — agent uses Edit/Write tools to mutate `target_files`. Recipe markdown describes the edit precisely (file, anchor, before/after, verification).
- `sibling-skill` — agent invokes `sibling_skill` via Skill tool, passing hints. Requires Copilot CLI runtime.
- `manual` — no recipe; agent surfaces `manual_instructions_path` and asks the user how to proceed.
- `deferred-to-pipeline` (computed at phase-decision time when `sibling-skill` + missing rights) — agent skips for Phase 2; Phase 3 picks it up.

### Phase-decision banner

```
▶ Phase 1 complete — assessment + apply-plan ready.

  Scorecard:     65% raw, 78% with-waivers
  Apply plan:    7 must-fix actions  (3 repo-edit, 3 sibling-skill, 1 manual)
                 4 should-fix actions (2 repo-edit, 0 sibling-skill, 2 manual)
  Rights check:  ✓ Operator has Contributor on target RG
  Posture:       citadel-spoke (declared in SPEC §12)

▶ Recommended next phase: Phase 2 (refine + deploy)

  3 sibling-skill actions need Azure provisioning. You have the rights.
  Run:  invoke the threadlight-production-ready SKILL.md Phase 2 instructions
        with apply-plan path tests/production-readiness-apply-plan.json
  Or:   ask Copilot "apply the production-readiness plan"

  After Phase 2, the skill will scaffold Phase 3 (CI/CD handoff) for any
  actions you opted not to deploy yourself.
```

When the rights probe fails or `restricted_env: yes`:

```
▶ Recommended next phase: Phase 3 (CI/CD handoff)

  Operator lacks Contributor on target RG (or environment is restricted).
  Phase 2 deploys will be skipped; all 3 sibling-skill actions deferred.
  Phase 3 will scaffold a GH Action workflow with UAMI + federated credentials
  for a central platform team to onboard.
```

---

## Phase 2 — Refine and (optionally) deploy

**Trigger:** agent reads SKILL.md Phase 2 instructions when the operator says something like "apply the production-readiness plan" or "fix the must-fix findings."

**Flow per action (agent-driven, from SKILL.md guidance):**

1. Agent reads `tests/production-readiness-apply-plan.json`.
2. Topologically sorts actions by `depends_on`.
3. For each action:
   - **`kind: repo-edit`**:
     - Agent reads `recipe_path` (markdown with structured sections: `## Target file`, `## Edit type`, `## Edit recipe`, `## Verification`).
     - Agent uses Edit/Write tool to apply the edit. Copilot CLI shows the diff to the user (TTY) or proceeds with `--yes` semantics in non-TTY.
     - Agent runs `verification_command`; if non-zero exit, rolls back the edit (git checkout) and surfaces the failure.
   - **`kind: sibling-skill`**:
     - Agent invokes the named sibling skill via Skill tool. The sibling skill inherits its own safety machinery.
     - Agent waits for sibling completion; logs result.
     - If sibling reports "missing rights" or "user declined," agent re-classifies the action as `deferred-to-pipeline` and surfaces to Phase 3.
   - **`kind: manual`**:
     - Agent surfaces the `manual_instructions_path` content; asks the user how to proceed; logs decision.
4. After all actions, agent re-runs `python production_ready.py --onboard --framing-file tests/production-readiness-framing.json` (or the appropriate re-assessment command) and confirms that findings moved from must-fix → pass.

**Per-finding recipe format (markdown for human + agent readability):**

```markdown
# NET-002 — Private endpoints declared for Foundry account

**Pillar:** network-posture
**Severity:** must-fix
**Recipe kind:** repo-edit

## Target file

`infra/main.bicep`

## Edit type

Insert resource block (idempotent: only insert if no `Microsoft.Network/privateEndpoints` resource targets the Foundry account).

## Edit recipe

Locate the Foundry account resource block (`resource ai 'Microsoft.CognitiveServices/accounts@...'`). Immediately after it, insert:

```bicep
resource foundryPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${ai.name}-pe'
  location: location
  properties: {
    subnet: { id: <subnetResourceId> }
    privateLinkServiceConnections: [{
      name: '${ai.name}-pls'
      properties: {
        privateLinkServiceId: ai.id
        groupIds: ['account']
      }
    }]
  }
}
```

Substitute `<subnetResourceId>` with the spoke subnet ID from `network.bicep` module outputs.

## Verification

Run after the edit:
```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
```

Expect exit code 0. Re-run `production_ready --onboard` and confirm NET-002 status moves from `must-fix` to `pass`.

## Related sibling skills

If Foundry account doesn't yet exist in target sub/RG: `foundry-vnet-deploy` skill.
For VNet/spoke subnet authoring: `citadel-spoke-onboarding`.
```

> **Recipe coverage requirement:** every must-fix finding ID in `FINDING_CATALOG` MUST have a corresponding `references/remediation-recipes/{ID}.md` by v0.4.0 ship. `test_recipe_catalog.py` enforces this. Should-fix findings MAY have recipes; if absent, plan emits `kind: manual`.

---

## Phase 3 — CI/CD handoff

**Trigger:** apply-plan contains any `deferred-to-pipeline` action, or operator explicitly runs `--scaffold-cicd`.

**Flow (agent-driven, from SKILL.md guidance):**

1. Agent reads `references/cicd-templates/azd-deploy-prod.yml.template`.
2. Substitutes parameters from framing answers + manifest: target subscription, target RG, model regions, posture-specific deployment notes.
3. Agent uses Write tool to write `.github/workflows/azd-deploy-prod.yml`.
4. Agent reads `references/cicd-templates/uami-onboarding-README.md.template`.
5. Substitutes parameters (UAMI display name suggestion, federated-credential subject for the workflow, required RBAC role assignments).
6. Agent writes `docs/cicd-onboarding-for-central-team.md`.
7. Agent prints the central-team handoff runbook: "share this README with the platform team; once they create the UAMI and register the FedCred, the next push to `main` will provision."

**The workflow template uses:**

- `azure/login@v2` with OIDC federated credentials (no client secret in repo)
- A UAMI in the customer's central management subscription, scoped to the target RG with required RBAC
- `azd up --no-prompt` against the deployment manifest
- Post-deploy: re-runs `production_ready --static` and uploads the resulting manifest + apply-plan as workflow artifacts (so the central team can see what was deployed)

**The README template tells the central team:**

1. Which UAMI to create (suggested display name, location)
2. Federated-credential subject string to register (issuer + subject claim matching the GitHub workflow's `repo:owner/repo:ref:refs/heads/main`)
3. RBAC role assignments to grant on the target RG (Contributor for `azd up`; specific resource provider data roles per posture)
4. How to validate the credential (a one-off `az login` test using the OIDC token)
5. A copy-pasteable `az` script with placeholders for the central team to fill in subscription, RG, and UAMI names

> **Phase 3 only writes files. It never calls `az` itself.** All Azure work happens later, in the CI/CD workflow it scaffolds, executed by a UAMI the central team controls.

---

## CLI surface (v0.4.0 additions)

Existing flags (v0.3.0): `--pillar`, `--static`, `--quick`, `--target`, `--agt-profile`, `--waivers`, `--accept-stale-safe-check`, `--freshness-hours`, `--out`, `--report`, `--in-postdeploy`, `--in-manifest`, `--in-spec`, `--root`, `--quiet`, `--include-experimental`, `--diff`, `--gate-preview`, `--remediate`, `--trend-csv`, `--secure-score-floor`, `--version`. **All retained.**

New flags (v0.4.0):

| Flag | Default | Purpose |
|---|---|---|
| `--onboard` | off | Activate Phase 1 wizard (framing Qs + apply-plan emission + phase-decision banner). Without this flag, v0.4.0 behaves identically to v0.3.0. |
| `--framing-file PATH` | off | Pre-supply framing answers as JSON; lets `--onboard` run non-TTY (CI). |
| `--apply-plan PATH` | `tests/production-readiness-apply-plan.json` | Where to write the apply-plan. |
| `--scaffold-cicd` | off | Skip Phase 1/2; emit the Phase 3 template + handoff README only. |
| `--no-rights-probe` | off | Skip provisioning-rights probe (assume operator has rights — useful for offline planning). |

**No `--apply FINDING_ID` flag.** Reading the cross-session messages carefully: the user's intent is that the *agent* applies recipes (using Edit/Write/Skill tools); the *script* never mutates. A `--apply` flag on the script would re-introduce the mutation surface area we explicitly removed. The apply-plan is the contract between script and agent.

---

## Module breakdown

Seven modules. Names mirror the v0.3.0 module convention (Module A, B, C, …).

### Module A — Framing-Q wizard + apply-plan schema (foundational)

**Surface area:** script-side. New `_run_framing_wizard()`, `_emit_apply_plan()`, apply-plan JSON schema doc.
**New tests:** `test_framing_questions.py` (parse, defaults, missing fields, TTY refusal), `test_apply_plan_emission.py` (finding → recipe lookup, plan grouping by kind, schema validity).
**Touches:** `scripts/production_ready.py` (~300 LOC added), `references/apply-plan-schema.md` (new), `tests/` (2 new test files).
**Depends on:** nothing — pure additive.

### Module B — Per-finding recipe catalog (bulk artifact work)

**Surface area:** `references/remediation-recipes/{ID}.md`, one per must-fix finding. v0.3.0 has **61 must-fix non-experimental IDs** across the 13 pillars (verified by enumerating `FINDING_CATALOG` with `severity == "must-fix" and not experimental`). Largest pillar buckets: observability (8), secrets (8), network-posture (6), agent-governance (5), identity-access (5), reliability (5), model-lifecycle (4), responsible-ai (4), sre-handover (4), continuous-evals (3), cost (3), hitl-audit (3), supply-chain (3).
**New tests:** `test_recipe_catalog.py` — every must-fix in `FINDING_CATALOG` (filtering `experimental=False`) has a recipe; every recipe has required sections (`## Target file`, `## Edit type`, `## Edit recipe`, `## Verification`); recipe `kind` matches expected (repo-edit / sibling-skill / manual).
**Touches:** `references/remediation-recipes/` (~40 new files), `references/remediation-recipes.yaml` (existing — kept for `--remediate` backward compat; document drift policy in SKILL.md).
**Depends on:** Module A (schema definition).

### Module C — Provisioning-rights probe + phase decision (the brains)

**Surface area:** new `_probe_provisioning_rights()` in script (parallels existing `_probe_tiers`); phase-decision computation; banner formatter.
**New tests:** `test_phase_decision.py` — matrix of (rights, restricted_env, has_sibling_actions) → expected banner + plan annotations.
**Touches:** `scripts/production_ready.py` (~150 LOC), 1 new test file.
**Depends on:** Module A (apply-plan structure).

### Module D — CI/CD template + UAMI runbook (Phase 3 deliverables)

**Surface area:** `references/cicd-templates/azd-deploy-prod.yml.template`, `references/cicd-templates/uami-onboarding-README.md.template`, template-substitution function in script (`_render_cicd_template`).
**New tests:** `test_cicd_template.py` — substitution correctness, output is valid YAML / markdown, OIDC fed-cred subject string matches Microsoft's documented format.
**Touches:** `references/cicd-templates/` (2 new files), `scripts/production_ready.py` (~100 LOC), 1 new test file, `tests/` (a sample rendered output fixture).
**Depends on:** Module A (framing answers feed template substitution).

### Module E — SKILL.md 3-phase rewrite + version bump

**Surface area:** SKILL.md rewrite. Document the 3-phase workflow as agent-facing instructions (the script doesn't need to know about this — it's instructions for the Copilot agent at invocation time). Update "What this skill does NOT replace" table (still relevant — sibling skills are *invoked* not *reimplemented*). Update "When to invoke" table (add the `--onboard` mode). Update `metadata.version` from `0.3.0` to `0.4.0`. Add a v0.4.0 deep-link in the H1 callout.
**New tests:** none — SKILL.md is agent guidance, not script logic. Coverage via Module G (real-customer dogfood).
**Touches:** `skills/threadlight-production-ready/SKILL.md` (substantial rewrite, ~200 lines net).
**Depends on:** Modules A–D (must accurately describe the shipped behavior).

### Module F — Awesome-gbb sibling-skill invocation map (integration glue)

**Surface area:** `references/sibling-skill-invocation-map.md` — one markdown table: finding ID → sibling skill name → invocation hint → optional fallback (`az`-direct or `manual`). Replaces the implicit one-skill-per-must-fix mapping in v0.3.0's SKILL.md "What this skill does NOT replace" table with a structured catalog.
**New tests:** `test_sibling_skill_map.py` — every `kind: sibling-skill` recipe in Module B's catalog points to an entry in the map; map entries reference real awesome-gbb skill names (validated against a hard-coded allowlist of skills documented in `awesome-gbb` as of 2026-06-10).
**Touches:** `references/sibling-skill-invocation-map.md` (new), 1 new test file.
**Depends on:** Module B (recipes reference sibling skills).

### Module G — Real-customer field test + bug-fix sprint (validation)

**Surface area:** dogfood run against a real pilot (live customer RG or a curated internal dogfood pilot). Document surprises in a follow-up GitHub Issue. Fix the highest-priority bugs surfaced by the dogfood. Update fixtures if the dogfood revealed catalog gaps. Add a new fixture: `sample-pilot-restricted` (operator with read-only rights → exercises Phase 3 path); add a `framing-answers.json` to `sample-pilot` and `sample-pilot-citadel` for deterministic re-runs.
**New tests:** end-to-end `test_onboard_wizard.py` — dry-run wizard against all fixtures; assert phase-decision matches expected; assert apply-plan kinds are correct.
**Touches:** `tests/`, `references/fixtures/`, bug-fix patches in script.
**Depends on:** Modules A–F (everything has to work).

---

## Open questions — answered

The four open questions raised during brainstorming, locked here per the user's request not to leave TBDs in the spec.

### Q1 — Is `apply-plan.json` committed to the pilot's repo or treated as ephemeral?

**Answer:** **Committed.**

Rationale:
- Precedent: `tests/production-readiness-manifest.json` is already committed (v0.3.0 fixtures include them, e.g. `references/fixtures/sample-pilot/tests/production-readiness-manifest.json`). The apply-plan is in the same class of artifact.
- Reviewability: committed apply-plan shows up in PR diffs; reviewers can see what the agent is about to mutate before approving the PR.
- Audit: combined with git history of subsequent edits, the committed apply-plan IS the audit trail of what was planned vs what was applied.
- Re-runnability: a committed apply-plan + committed framing-answers.json makes re-runs deterministic and shareable across operators.

Caveat: the file represents "the plan AS OF the most recent assessment run." Future runs overwrite it. We add a `manifest_sha256` field to detect "stale plan" (operator ran assess, edited code, didn't re-assess, then tried to apply against a plan that no longer reflects reality). The agent in Phase 2 refuses to apply a stale plan; surfaces the sha mismatch; suggests re-running Phase 1.

### Q2 — For findings without a published recipe (catalog gap), does the script fail or emit "manual"?

**Answer:** **Emit `kind: manual`, with the following constraints enforced by tests:**

- Every **must-fix** finding (non-experimental) MUST have a recipe. `test_recipe_catalog.py` fails the suite if a must-fix lacks a recipe. This forces catalog completeness for the gates that block production.
- **Should-fix** and **informational** findings MAY have a recipe. If absent, the plan emits `kind: manual` with a `manual_instructions_path` pointing to the pillar reference doc (`references/pillars/{N}-{pillar}.md#{ID}` anchor).
- **Experimental** findings are excluded from the apply-plan unless `--include-experimental` is set (mirrors v0.3.0 scoring contract).

Rationale: forcing must-fix completeness ships discipline; allowing should-fix to be incomplete ships pragmatism. The agent handles `manual` gracefully — it tells the user what the finding is, where to read about it, and asks how they want to proceed.

### Q3 — Should the agent run `az bicep build` after each Bicep edit to validate?

**Answer:** **Yes — every recipe specifies a verification command.** The agent runs it after applying; non-zero exit triggers rollback (git checkout of the edited file) and a failure report.

Recipe format (Module B) requires a `## Verification` section. For Bicep edits the canonical command is `az bicep build --file <path> --stdout > /dev/null`. For YAML edits, `python -c "import yaml; yaml.safe_load(open('<path>'))"`. For markdown, no verification (recipe documents this explicitly with `## Verification\n\nNone — markdown content has no compile step.`).

Rationale: verification IS the safety net. Bicep edits can produce subtly broken syntax that wouldn't surface until the next `azd up`; catching it immediately after the edit means the apply-plan never leaves the pilot in a half-broken state.

### Q4 — Framing-question set: minimum or comprehensive?

**Answer:** **Minimum (7 questions) in v0.4.0. Comprehensive set deferred to v0.5.0 once we have field feedback.**

The 7-question set is specified in the Phase 1 section above. v0.5.0 may extend with: residency sub-fields, RTO/RPO numeric values, pricing plan (PAYG/PTU), per-customer Defender plan list, per-customer Policy ID list, model deployment list, retirement-notice owner, rollback strategy.

Rationale: every question adds friction for the operator. The 7 minimum is the smallest set that lets the script make the right phase-decision call. Everything else is currently captured by SPEC §12 anyway; v0.5.0's `must_have_pillars` enforcement work can promote SPEC §12 fields to framing questions on a case-by-case basis once we know which ones operators actually find ambiguous in practice.

---

## What slides to v0.5.0+

From the v0.4.0 planning brief's 5-item starting menu:

| Item | v0.4.0 status | Rationale |
|---|---|---|
| 1. New `gateway-resilience` pillar (GW-001..005, GW-101..103) | **Deferred to v0.5.0** | A new pillar is a net coverage addition, orthogonal to the contract-flip headline. Bundling them dilutes the v0.4.0 message and doubles QA surface. |
| 2. SPEC §12 per-customer enforcement (`defender_plans_required`, `required_policy_ids`, `must_have_pillars`) | **Partially subsumed by framing-Q wizard; rest deferred to v0.5.0** | Framing-Q wizard collects equivalent intent for `target_posture`, rights, and posture variants. Per-customer Defender / Policy lists are still enforced as v0.3.0 fixed defaults; v0.5.0 promotes them to framing-Q-driven configuration. |
| 3. Close 24 experimental stubs | **Deferred to v0.5.0** | Most stubs depend on upstream sibling skills not yet shipped (`foundry-evals` last-run API, `azure-policy-compliance` skill, etc.). v0.4.0's hybrid sibling-skill invocation pattern (Module F) sets up the contract these stubs need anyway. |
| 4. 6 awesome-gbb upstream skill landings (#267-#272) | **Tracked separately; not blocking v0.4.0** | These are awesome-gbb work, not threadlight work. v0.4.0's sibling-skill-invocation-map (Module F) consumes them as they land. |
| 5. Real-customer field test | **Included as Module G** | This is the validation gate v0.4.0 needs to ship credibly. |

---

## Test strategy

Stdlib only (per the existing v0.3.0 convention; no pytest). All new tests run with `python3 tests/test_*.py`.

**New test files (7):**

1. `test_framing_questions.py` — Module A
2. `test_apply_plan_emission.py` — Module A
3. `test_recipe_catalog.py` — Module B (catalog completeness + recipe format)
4. `test_phase_decision.py` — Module C
5. `test_cicd_template.py` — Module D
6. `test_sibling_skill_map.py` — Module F
7. `test_onboard_wizard.py` — Module G (end-to-end against fixtures)

**Existing 61 tests (10 files):** all remain green. The contract flip is additive — existing assessment behavior is unchanged when `--onboard` is not set.

**Fixture updates:**

- `sample-pilot` — add `tests/production-readiness-framing.json` with a known-good answer set
- `sample-pilot-citadel` — add same
- `sample-pilot-broken` — add a framing file that demonstrates the "operator-without-rights" path
- **New:** `sample-pilot-restricted` — pilot with framing answers that force Phase 3 (no rights, restricted env), exercises CI/CD scaffold output
- `sample-pilot-v4` — unchanged (AGT v4 pillar work)

---

## Effort estimate

Comparison to v0.3.0 (the immediately prior release):

| Metric | v0.3.0 | v0.4.0 (planned) | Ratio |
|---|---|---|---|
| New script LOC | ~2,100 | ~600 | 0.29× |
| New stdlib tests | 8 files (~47 functions) | 7 files (~35 functions) | 0.74× |
| New reference docs | ~3 (template, recipes.yaml, hooks) | ~66 (61 recipes + 2 cicd-templates + 1 apply-plan schema + 1 sibling-skill map + 1 framing-Q reference) | 22× |
| SKILL.md rewrite scope | partial (over-claims rewritten) | substantial (3-phase model added) | ≈2× |
| New fixtures | 2 (`sample-pilot-broken`, `sample-pilot-citadel`) | 1 (`sample-pilot-restricted`) + 4 framing-files | similar |
| Live-Azure surface (new probes) | 5 newly wired | 1 (provisioning-rights probe) | 0.2× |
| Net-new finding IDs in catalog | 15 + 24 experimental | 0 | 0× |

**Overall: smaller code lift, dramatically larger documentation/recipe lift, similar overall ship effort.** The work centre-of-mass moves from script logic to per-finding recipe authoring (Module B), which can be parallelized (one recipe per finding is independent work).

---

## Cross-references

- v0.3.0 PR: [aiappsgbb/threadlight-skills#27](https://github.com/aiappsgbb/threadlight-skills/pull/27)
- v0.3.0 SKILL.md contract: `skills/threadlight-production-ready/SKILL.md`
- v0.3.0 FINDING_CATALOG: `skills/threadlight-production-ready/scripts/production_ready.py:97-284`
- v0.3.0 SPEC §12 template: `skills/threadlight-production-ready/references/spec-section-12-template.md`
- v0.3.0 remediation recipes (bash, for `--remediate`): `skills/threadlight-production-ready/references/remediation-recipes.yaml`
- v0.3.0 OIDC GitHub Actions recipe (advisory): `skills/threadlight-production-ready/references/ci-github-actions.yml`
- Awesome-gbb upstream issues (skills consumed by Phase 2 sibling-skill invocations): #245, #246, #247, #248, #249, #250, #251, #252 (v0.3.0 cluster); #267, #268, #269, #270, #271, #272 (v0.4.0 brief's named additions — referenced from Module F's invocation map, not blocking).
- Spec format precedent: `docs/superpowers/specs/2026-06-10-agt-v4-deep-checks-design.md`, `docs/superpowers/specs/2026-06-10-per-evidence-freshness-design.md`
