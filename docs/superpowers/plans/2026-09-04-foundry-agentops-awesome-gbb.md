# Foundry AgentOps Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `foundry-agentops` skill to `aiappsgbb/awesome-gbb` that adopts and operates Azure AgentOps `v0.14.0` for one selected agent while preserving Citadel and Threadlight ownership boundaries.

**Architecture:** This is an instruction-and-runbook skill over the upstream `agentops-accelerator` CLI, not a new wrapper executable. It resolves one agent, pins AgentOps exactly, configures native AgentOps artifacts, supports standalone and Threadlight-aware workflow modes, and delegates deep eval, observability, and AGT work to the existing awesome-gbb skills.

**Tech Stack:** Copilot skill Markdown, AgentOps CLI `v0.14.0`, Python 3.11+, Foundry/azd metadata, GitHub Actions/Azure DevOps guidance, awesome-gbb catalog validators.

**Dependency:** Complete this plan before starting `docs/superpowers/plans/2026-09-04-threadlight-agentops-integration.md`.

---

## File map

| Path | Responsibility |
|---|---|
| `skills/foundry-agentops/SKILL.md` | User-facing adoption, diagnostics, and day-2 workflow |
| `skills/foundry-agentops/references/upstream-pin.md` | Machine-readable upstream release pin and validation contract |
| `skills/foundry-agentops/references/artifact-contract.md` | Supported native inputs, outputs, versions, and status semantics |
| `skills/foundry-agentops/references/workflow-modes.md` | Standalone versus Threadlight-aware workflow behavior |
| `skills/foundry-agentops/references/threadlight-boundary.md` | Ownership matrix and Citadel non-overlap |
| `skills/foundry-agentops/references/day2-runbook.md` | Eval, baseline, trace promotion, Doctor, and release-evidence procedures |
| `skills/foundry-agentops/test-fixture/consumer_prompt.md` | Live Copilot CLI smoke contract |
| `.github/skill-deps.yml` | Change-gated fixture dependency graph |
| `scripts/build-site.py` | Catalog category entry |
| `plugin.json` | Plugin version, skill count, description, and keywords |
| `README.md` | Catalog row and usage entry |
| `CHANGELOG.md` | Release note |
| `skills/foundry-evals/SKILL.md` | Cross-reference; remains eval domain owner |
| `skills/foundry-observability/SKILL.md` | Cross-reference; remains telemetry-wiring owner |
| `skills/foundry-agt/SKILL.md` | Cross-reference; remains runtime-governance owner |

### Task 1: Add the acceptance fixture and dependency edge

**Files:**
- Create: `skills/foundry-agentops/test-fixture/consumer_prompt.md`
- Modify: `.github/skill-deps.yml`

- [ ] **Step 1: Write the live acceptance fixture**

Create `consumer_prompt.md` with this contract:

```markdown
# Customer goal - `foundry-agentops` smoke

Use the `foundry-agentops` skill to onboard one temporary Foundry prompt agent
into an isolated temporary workspace.

Required proof:
1. Read `skills/foundry-agentops/SKILL.md` before acting.
2. Create the prompt agent with `foundry-prompt-agents`; use a UUID suffix.
3. Install exactly `agentops-accelerator==0.14.0`.
4. Create a minimal valid `agentops.yaml` for that one agent and one JSONL row.
5. Run `agentops eval analyze --format json`; require `version == 1`.
6. Run one eval and require `.agentops/results/latest/results.json` with
   `version == 1` and a boolean `summary.overall_passed`.
7. Run `agentops doctor --evidence-pack`; require
   `.agentops/release/latest/evidence.json` with `version == 1`.
8. Delete the temporary prompt agent and workspace.

Do not generate a Threadlight manifest and do not modify APIM, Citadel, RBAC,
networking, branch protection, or environment approvals.

Your final action must write exactly one marker:

Success:
`printf 'SMOKE_RESULT=PASS\n' > /tmp/foundry-agentops-smoke-result`

Failure:
`printf 'SMOKE_RESULT=FAIL %s\n' "$failure_reason" > /tmp/foundry-agentops-smoke-result`
```

- [ ] **Step 2: Register fixture dependencies**

Add this entry under the cross-skill dependency section:

```yaml
  foundry-agentops:
    depends_on:
      - foundry-prompt-agents
      - foundry-evals
      - foundry-observability
      - foundry-agt
```

- [ ] **Step 3: Verify matrix discovery initially fails because the skill is absent**

Run:

```bash
python scripts/build-test-matrix.py --repo-root .
```

Expected: non-zero or validation output identifying `foundry-agentops` as an unresolved skill dependency/fixture.

- [ ] **Step 4: Commit the RED fixture**

```bash
git add skills/foundry-agentops/test-fixture/consumer_prompt.md .github/skill-deps.yml
git commit -m "test: define foundry AgentOps smoke contract"
```

### Task 2: Pin AgentOps `v0.14.0`

**Files:**
- Create: `skills/foundry-agentops/references/upstream-pin.md`

- [ ] **Step 1: Add machine-readable pin frontmatter**

Use these exact upstream values:

```yaml
---
schema_version: 2
freshness_tier: A
automation_tier: auto

upstream:
  type: github_repo
  repo: Azure/agentops
  ref: refs/tags/v0.14.0
  pinned_sha: fb5c93eee489c71ef4084fa209adae24f762e3d7
  pinned_commit_message: "Release v0.14.0"
  license: MIT
  notes: |
    This skill wraps the agentops-accelerator 0.14.0 CLI contract.

docs_to_revalidate:
  - "https://github.com/Azure/agentops/tree/v0.14.0"
  - "https://github.com/Azure/agentops/blob/v0.14.0/docs/concepts.md"
  - "https://github.com/Azure/agentops/blob/v0.14.0/docs/doctor-explained.md"
  - "https://pypi.org/project/agentops-accelerator/0.14.0/"

known_issues:
  - "Pre-1.0 contract: revalidate every minor release."
  - "Release evidence is unsigned and release/latest is mutable."
  - "Doctor sources may fail open and must not be interpreted as healthy."

validation:
  requires:
    - pypi
  runnable: true
  script: |
    #!/usr/bin/env bash
    set -euo pipefail
    python -m venv .agentops-pin-venv
    . .agentops-pin-venv/bin/activate
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet "agentops-accelerator==0.14.0"
    agentops --help >/dev/null
    agentops eval analyze --help >/dev/null
    agentops workflow analyze --help >/dev/null
    agentops doctor --help >/dev/null
    python - <<'PY'
    from agentops.core.results import RunResult
    from agentops.core.release_evidence import ReleaseEvidence
    assert RunResult.model_fields["version"].default == 1
    assert ReleaseEvidence.model_fields["version"].default == 1
    print("AGENTOPS_CONTRACT_VALIDATION_PASS")
    PY
  expected_output:
    - "AGENTOPS_CONTRACT_VALIDATION_PASS"
  failure_signatures: []

last_validated: 2026-09-04
validated_by: ricchi
known_issues_count: 3
---
```

- [ ] **Step 2: Add the human audit sections**

Document the tag, SHA, install command, supported Python range, native artifact paths, re-pin procedure, and the three known issues from the frontmatter. Do not copy upstream source code.

- [ ] **Step 3: Run pin validation**

```bash
python scripts/run-pin-validation.py --skill foundry-agentops
```

Expected: output includes `AGENTOPS_CONTRACT_VALIDATION_PASS`.

- [ ] **Step 4: Commit the pin**

```bash
git add skills/foundry-agentops/references/upstream-pin.md
git commit -m "docs: pin AgentOps v0.14.0"
```

### Task 3: Author the skill contract

**Files:**
- Create: `skills/foundry-agentops/SKILL.md`

- [ ] **Step 1: Add frontmatter**

```yaml
---
name: foundry-agentops
description: >-
  Adopt and operate Azure AgentOps for one Microsoft Foundry or HTTP agent:
  pin agentops-accelerator, resolve the agent and environment, initialize
  agentops.yaml, configure eval thresholds and regression baseline, validate
  telemetry, run Doctor, produce release evidence, and prepare CI/CD integration.
  USE FOR: AgentOps, agent operations, release evidence, AgentOps Doctor,
  results.json, evidence.json, trace-to-regression, AgentOps workflow analysis,
  per-agent operational readiness. DO NOT USE FOR: Citadel APIM or access
  contracts, tenant isolation, network/RBAC provisioning, final Threadlight
  production-readiness scoring, AGT policy authoring, or deep evaluator design.
metadata:
  version: "0.1.0"
---
```

- [ ] **Step 2: Add the mandatory selection gate**

State that the skill handles exactly one agent per invocation. Resolve the root in this order:

```text
1. Explicit agent root from the user
2. One azure.yaml service with host: azure.ai.agent
3. One .foundry/agent-metadata*.yaml root
4. Existing agentops.yaml root
5. Ask the user when more than one candidate remains
```

Never scan or modify sibling agent roots after selection.

- [ ] **Step 3: Add installation and bootstrap**

Document these exact invariants:

```text
python >= 3.11
agentops-accelerator == 0.14.0
agentops.yaml is the opt-in marker
.agentops/ contains generated/cache evidence
existing config and datasets are never overwritten without approval
```

Require `agentops eval analyze --format json` after initialization and require JSON `version: 1`.

- [ ] **Step 4: Add operational workflows**

Cover:

```text
eval run -> results.json -> threshold/regression review
explicit baseline promotion -> .agentops/baseline/results.json
trace promotion -> trace-regression.jsonl + manifest
doctor --evidence-pack -> evidence.json/evidence.md
redteam and ASSERT/ACS as optional modules
```

State that baseline promotion always requires an explicit user decision.

- [ ] **Step 5: Add failure behavior**

Use this classification:

```text
configuration/runtime failure -> stop and surface the AgentOps error
threshold or finding gate -> preserve AgentOps exit 2
missing telemetry/permissions -> unavailable, never healthy
unknown AgentOps version -> stop and re-pin before continuing
```

- [ ] **Step 6: Run skill validation**

```bash
python scripts/validate-skills.py
python scripts/build-test-matrix.py --repo-root .
```

Expected: validation passes and matrix JSON contains `foundry-agentops`.

- [ ] **Step 7: Commit the skill**

```bash
git add skills/foundry-agentops/SKILL.md
git commit -m "feat: add foundry AgentOps adoption skill"
```

### Task 4: Add artifact, workflow, boundary, and day-2 references

**Files:**
- Create: `skills/foundry-agentops/references/artifact-contract.md`
- Create: `skills/foundry-agentops/references/workflow-modes.md`
- Create: `skills/foundry-agentops/references/threadlight-boundary.md`
- Create: `skills/foundry-agentops/references/day2-runbook.md`

- [ ] **Step 1: Document the artifact contract**

Pin these supported outer contracts:

```text
results.json: version 1
  required: started_at, finished_at, target, dataset_path, thresholds,
            summary, aggregate_metrics

evidence.json: version 1
  status: ready | ready_with_warnings | blocked
  check status: ready | warning | blocked | unknown

redteam/latest.json:
  required: target, risk_categories, attack_strategies, num_objectives,
            total_attempts, successful_attacks, attack_success_rate,
            per_category, generated_at, target_fingerprint
```

Call out sensitive `results.json` fields (`rows[].input`, `response`, `context`, `tool_calls`) and forbid copying them into downstream evidence.

- [ ] **Step 2: Document workflow modes**

Use this decision:

```text
Threadlight project detected:
  prepare AgentOps config/prerequisites only
  do not run agentops workflow generate
  hand workflow composition to threadlight-cicd

Non-Threadlight project:
  run agentops workflow analyze --format json
  review recommendation
  allow agentops workflow generate only with user approval
```

- [ ] **Step 3: Document ownership boundaries**

Include the approved matrix:

```text
Citadel: gateway, access contract, isolation, routing, rate limits
foundry-evals: deep evaluator and dataset authoring
foundry-observability: OTel/App Insights wiring
foundry-agt: AGT policy and runtime enforcement
foundry-agentops: per-agent operations and native AgentOps evidence
Threadlight: lifecycle normalization and final policy
```

- [ ] **Step 4: Document day-2 procedures**

Provide concrete commands for:

```bash
agentops eval analyze --format json
agentops eval run --baseline .agentops/baseline/results.json
agentops eval promote-traces
agentops doctor --evidence-pack
agentops workflow analyze --format json
agentops redteam run
agentops assert run
```

For each command, name expected artifact paths and rollback/cleanup behavior.

- [ ] **Step 5: Link all references from SKILL.md**

Add a "References" table with all four documents and `upstream-pin.md`.

- [ ] **Step 6: Commit references**

```bash
git add skills/foundry-agentops
git commit -m "docs: define AgentOps operational contracts"
```

### Task 5: Add catalog and cross-skill integration

**Files:**
- Modify: `skills/foundry-evals/SKILL.md`
- Modify: `skills/foundry-observability/SKILL.md`
- Modify: `skills/foundry-agt/SKILL.md`
- Modify: `scripts/build-site.py`
- Modify: `README.md`
- Modify: `plugin.json`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add narrow cross-references**

Add one paragraph to each existing skill:

```text
Use foundry-agentops when the user wants the complete AgentOps adoption and
release-evidence workflow. This skill remains authoritative for its domain and
must not be replaced by an AgentOps aggregate.
```

Bump each touched skill PATCH version.

- [ ] **Step 2: Add `foundry-agentops` to the Foundry operations category**

Add it adjacent to `foundry-evals` and `foundry-observability` in `scripts/build-site.py`.

- [ ] **Step 3: Update plugin metadata**

Change:

```json
{
  "version": "4.31.0"
}
```

Update the description from 36 to 37 skills and add keywords:

```json
"agentops",
"agent-operations",
"release-evidence",
"doctor",
"regression-baseline"
```

- [ ] **Step 4: Update README and changelog**

Add a catalog row describing per-agent AgentOps adoption and explicitly state that Citadel and Threadlight are not replaced.

- [ ] **Step 5: Rebuild/check catalog outputs**

```bash
python scripts/build-site.py
python scripts/build-plugins.py --check
python scripts/validate-skills.py
```

Expected: all validators pass and the generated catalog lists 37 skills.

- [ ] **Step 6: Commit catalog integration**

```bash
git add skills/foundry-evals/SKILL.md \
  skills/foundry-observability/SKILL.md \
  skills/foundry-agt/SKILL.md \
  scripts/build-site.py README.md plugin.json CHANGELOG.md docs/
git commit -m "feat: publish foundry AgentOps skill"
```

### Task 6: Run the upstream and live acceptance gates

**Files:**
- Verify only

- [ ] **Step 1: Run deterministic validation**

```bash
python scripts/validate-skills.py
python scripts/build-plugins.py --check
python3 -m unittest scripts.tests.test_build_test_matrix -v
python scripts/build-test-matrix.py --repo-root .
```

Expected: all commands pass and matrix output contains `foundry-agentops`.

- [ ] **Step 2: Run the pinned upstream contract**

```bash
python scripts/run-pin-validation.py --skill foundry-agentops
```

Expected: `AGENTOPS_CONTRACT_VALIDATION_PASS`.

- [ ] **Step 3: Run the live Copilot CLI fixture**

Trigger the repository's existing `skill-test.yml` matrix for `foundry-agentops`.

Expected marker:

```text
SMOKE_RESULT=PASS
```

- [ ] **Step 4: Record the released awesome-gbb version**

After merge, record the exact awesome-gbb tag/commit and `foundry-agentops`
version in the Threadlight implementation issue. The Threadlight plan must pin
that value before adding a sibling-skill remediation reference.
