# Tool Governance Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the approved opt-in, runtime-agnostic Threadlight tool-governance contract, deterministic lifecycle gates, runtime adapter evidence, HITL correlation, and production-readiness findings without changing legacy behavior.

**Architecture:** SPEC sections 6 and 8 remain the human source of truth and generate the optional `specs/manifest.json.tool_governance` block. `threadlight-deploy` maps that immutable contract to MAF pre-tool middleware or GHCP MCP-server/gateway enforcement, while `threadlight-safe-check` validates design, pre-deploy wiring, and mock/canary post-deploy evidence; `threadlight-production-ready` consumes the resulting evidence and never treats an import or policy file as proof of enforcement.

**Tech Stack:** Markdown skill contracts, Python 3.13 standard library, pytest, JSON artifacts, Microsoft Agent Framework/AGT integration metadata, GHCP SDK MCP/gateway metadata, existing Threadlight safe-check and production-readiness CLIs.

---

## Execution constraints

- Execute on branch `unsafecode-tool-governance-contract`, starting from the
  committed design at `c0a794c433fa20c0f040b0f3d9f7b051709248e2`.
- Do not merge this branch into another branch from this plan. Task 1 refreshes
  this worktree from `origin/main` only after the coordinating owner confirms the
  in-flight prerequisite pull requests have landed.
- Do not create a new Threadlight skill.
- Do not add a global allow/deny default. An enabled contract must classify every
  canonical tool explicitly.
- Do not invent an in-process GHCP SDK governance path. GHCP tool governance is
  `mcp-server`, `gateway`, or an explicit deployment gap.
- Post-deploy probes use adapter-owned canaries only. They never call a
  production mutation endpoint.
- Keep `examples/returns-triage-governed/specs/SPEC.md` and
  `examples/returns-triage-governed/specs/manifest.json` byte-unchanged.

## File and symbol map at the approved-spec commit

Line numbers below are from `c0a794c`. Task 1 re-resolves them after refreshing
the branch; symbol names and required behavior remain authoritative if lines
move.

| Path | Current range/symbol | Responsibility |
|---|---|---|
| `skills/threadlight-design/SKILL.md` | lines 606-638, 924-1005 | Describe SPEC sections and the generated manifest contract |
| `skills/threadlight-design/references/speckit-template.md` | `## 6. Tool Contracts` at line 174; `## 8. Human Interaction Points` at line 302 | Human-readable canonical tool and gate fields |
| `skills/threadlight-design/tests/test_tool_governance_template.py` | create | Pin the optional template/manifest contract and design-skill wording |
| `skills/threadlight-deploy/SKILL.md` | `#### 1d. Choose runtime variant` at line 448; `### 4. src/agent/container.py` at line 891 | Runtime adapter mapping and generated artifact/probe instructions |
| `skills/threadlight-deploy/tests/test_tool_governance_contract.py` | create | Pin MAF/GHCP adapter rules and generated artifact keys |
| `skills/threadlight-safe-check/SKILL.md` | lines 67-83, 177-247, 816-878 | Document design, pre-deploy, and post-deploy governance gates |
| `skills/threadlight-safe-check/scripts/safe_check.py` | `_load_manifest` line 143; `phase_design` line 299; `phase_predeploy` line 355; `phase_postdeploy` line 465 | Validate contract coverage, adapter wiring, and probe evidence |
| `skills/threadlight-safe-check/tests/test_safe_check.py` | lines 1-274 | Unit/regression tests and example-copy parity |
| `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/` | create | Governed GHCP + MCP fixture with allow, deny, and conditional tools |
| `examples/returns-triage-governed/tests/safe_check.py` | generated parity copy | Stay byte-identical to the canonical safe-check script |
| `skills/threadlight-hitl-patterns/SKILL.md` | lines 43-66, 80-175, 276-290 | Gate input/output and handler audit fields |
| `skills/threadlight-hitl-patterns/references/audit-schema.md` | lines 1-73 | Canonical additive audit event shape |
| `skills/threadlight-production-ready/SKILL.md` | line 221; lines 1045-1062 | Pillar summary and AGT model description |
| `skills/threadlight-production-ready/scripts/production_ready.py` | `VERSION` line 490; `FINDING_CATALOG` line 679; `_mk_finding` line 1699; `RepoContext` line 2406; `_check_agt_static` line 2664; `_run_pillar` line 5638 | AGT findings and evidence consumption |
| `skills/threadlight-production-ready/references/pillars/02-agent-governance.md` | lines 28-102 | Exact AGT ID/title documentation |
| `skills/threadlight-production-ready/tests/test_tool_governance_evidence.py` | create | Evidence, runtime-signal, freshness, and severity tests |
| `skills/threadlight-production-ready/tests/test_agt_catalog_docs_sync.py` | create | Catalog-to-pillar ID/title parity |
| `skills/threadlight-production-ready/tests/test_version.py` | `EXPECTED` line 14 | Production-ready version pin |
| `CHANGELOG.md` | `## [Unreleased]` line 8 | Release note for the cross-skill contract |

## Task dependencies

| Task | Depends on | Why |
|---|---|---|
| 1. Integration checkpoint | none | Refreshes the future execution base and re-resolves symbols |
| 2. Design/template contract | 1 | Establishes the canonical field names |
| 3. Governed fixture/backward compatibility | 2 | Fixture must use the canonical schema |
| 4. Safe-check design gate | 3 | Validates the governed fixture and legacy absence |
| 5. Deploy runtime adapter contract | 2 | Defines generated adapter/probe artifacts |
| 6. Safe-check pre-deploy gate | 4, 5 | Validates the adapter contract against the manifest |
| 7. Safe-check post-deploy probe gate | 3, 5, 6 | Executes and validates the canary contract |
| 8. HITL correlation contract | 4, 7 | Uses the same gate/audit keys and probe assertions |
| 9. Production-ready evidence | 7, 8 | Consumes the completed adapter/probe/audit evidence |
| 10. AGT ID/title alignment | 9 | Adds the new IDs before enforcing documentation parity |
| 11. Release docs and full validation | 2-10 | Bumps versions and runs all acceptance commands |

## Approved-spec coverage

| Approved design section | Implemented by |
|---|---|
| Scope, opt-in compatibility, no global baseline | Tasks 2-4, 11 |
| Exact SPEC section 6/8 and manifest schema | Tasks 2-4 |
| Runtime-agnostic data flow and adapter artifacts | Tasks 5-7 |
| MAF pre-tool enforcement through `foundry-agt` | Tasks 5, 6, 9 |
| GHCP MCP-server/gateway-only enforcement | Tasks 5, 6, 9 |
| Future `.WithGovernance()` detection signal | Tasks 6, 9 |
| Design, pre-deploy, and post-deploy gates | Tasks 4, 6, 7 |
| HITL correlation and idempotent approval | Tasks 7, 8 |
| Production-readiness evidence and dynamic severity | Task 9 |
| AGT catalog/pillar title alignment | Task 10 |
| Legacy fixture, governed fixture, migration | Tasks 3, 4, 11 |
| Test strategy, rollout, error handling, acceptance | Tasks 1-11 |

### Task 1: Pre-execution integration checkpoint

**Files:**
- Inspect: `docs/superpowers/specs/2026-08-20-tool-governance-contract-design.md`
- Inspect: every path in the file map above
- Do not modify any implementation file in this task

- [ ] **Step 1: Confirm the worktree starts clean on the approved branch**

Run:

```bash
git branch --show-current
git rev-parse HEAD
git merge-base --is-ancestor c0a794c433fa20c0f040b0f3d9f7b051709248e2 HEAD
git status --short
```

Expected: branch is `unsafecode-tool-governance-contract`, the committed design
is an ancestor of `HEAD`, and status is empty. If status is not empty, stop and
identify the owner of each change before proceeding.

- [ ] **Step 2: Confirm the coordinating owner says the prerequisite PRs are on `main`**

Run:

```bash
git fetch origin main
git --no-pager log --oneline --decorate -12 origin/main
```

Expected: the coordinating owner-recognized prerequisite commits appear in the
log. If they do not, stop; do not merge, rebase, or implement against a moving
base.

- [ ] **Step 3: Refresh this branch from the eventual integration base**

Run:

```bash
git merge --no-edit origin/main
```

Expected: either `Already up to date.` or one merge commit on this branch. This
refreshes the implementation worktree; it does not merge this feature branch
into `main`.

- [ ] **Step 4: Re-resolve every planned symbol and line range**

Run:

```bash
rg -n '^## 6\. Tool Contracts|^## 8\. Human Interaction Points|^#### 5\. `specs/manifest\.json`' skills/threadlight-design
rg -n '^#### 1d\. Choose runtime variant|^### 4\. `src/agent/container\.py`' skills/threadlight-deploy/SKILL.md
rg -n '^def (phase_design|phase_predeploy|phase_postdeploy|_load_manifest)' skills/threadlight-safe-check/scripts/safe_check.py
rg -n '^FINDING_CATALOG|^def (_check_agt_static|_check_agt_live|_run_pillar)|^class RepoContext' skills/threadlight-production-ready/scripts/production_ready.py
```

Expected: every named symbol appears exactly once. Record shifted line numbers in
the execution notes; do not change keys, enums, severity, or runtime behavior to
fit unrelated upstream edits.

- [ ] **Step 5: Run the baseline suites before the first implementation change**

Run:

```bash
python -m pytest skills/threadlight-design/tests/ skills/threadlight-deploy/tests/ skills/threadlight-safe-check/tests/ skills/threadlight-production-ready/tests/ -q
```

Expected: PASS. If the refreshed base fails, stop and report the exact failing
test before adding feature changes.

### Task 2: Add the optional design and manifest contract

**Files:**
- Create: `skills/threadlight-design/tests/test_tool_governance_template.py`
- Modify: `skills/threadlight-design/SKILL.md:606-638`
- Modify: `skills/threadlight-design/SKILL.md:924-1005`
- Modify: `skills/threadlight-design/references/speckit-template.md:174-188`
- Modify: `skills/threadlight-design/references/speckit-template.md:302-327`

- [ ] **Step 1: Write the failing template contract test**

Create `skills/threadlight-design/tests/test_tool_governance_template.py`:

```python
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
TEMPLATE = (
    SKILL_DIR / "references" / "speckit-template.md"
).read_text(encoding="utf-8")


def test_tool_contract_template_declares_governance_fields():
    for marker in (
        "- **Action class**:",
        "- **Decision**:",
        "- **HITL gate ID**:",
        "- **Enforcement point**:",
        "- **Policy ID**:",
        "- **Required audit fields**:",
    ):
        assert marker in TEMPLATE


def test_hitl_template_declares_stable_gate_identity():
    assert "- **Gate ID**: `GATE-NNN`" in TEMPLATE
    assert "`correlation_id`" in TEMPLATE
    assert "`approval_id`" in TEMPLATE


def test_design_skill_pins_opt_in_and_no_implicit_allow():
    assert "`tool_governance.enabled: true`" in SKILL
    assert "unclassified tools are gaps, never implicit allows" in SKILL
    assert "absence or `enabled: false` preserves legacy behavior" in SKILL


def test_manifest_example_contains_the_machine_contract():
    for marker in (
        '"tool_governance"',
        '"contract_version": "1.0"',
        '"action_class"',
        '"decision"',
        '"enforcement_point"',
        '"policy_id"',
        '"required_audit_fields"',
    ):
        assert marker in SKILL
```

- [ ] **Step 2: Run the test and verify the contract is absent**

Run:

```bash
python -m pytest skills/threadlight-design/tests/test_tool_governance_template.py -v
```

Expected: FAIL on the first missing `Action class` or `tool_governance` marker.

- [ ] **Step 3: Add the exact governed-tool fields to the SpecKit template**

Immediately after the existing `Backed by` line in
`skills/threadlight-design/references/speckit-template.md` section 6, add:

```markdown
> **Optional tool-governance contract.** Generate the fields below only when the
> operator opts in with `tool_governance.enabled: true`. When enabled, use one
> `###` heading per exact canonical tool name; grouped headings are invalid.
> Policy decisions are case-by-case. There is no global allow/deny baseline.

- **Action class**: `read` | `reversible-write` | `irreversible-write` | `external-side-effect`
- **Decision**: `allow` | `deny` | `conditional`
- **HITL gate ID**: `GATE-NNN` (required exactly when Decision is `conditional`; otherwise omit)
- **Enforcement point**: `agent-middleware` | `mcp-server` | `gateway`
- **Policy ID**: stable non-empty identifier
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`;
  add `gate_id` and `approval_id` for `conditional`
```

In section 8, immediately before `Action gate`, add:

```markdown
- **Gate ID**: `GATE-NNN` (stable and unique within the SPEC)
- **Approval propagation**: return `approval_id` with the originating
  `correlation_id` when this gate releases a governed tool
```

- [ ] **Step 4: Add the design-skill derivation rules and manifest example**

In `skills/threadlight-design/SKILL.md`, add `tool_governance` to the section 6/8
input-contract description and add this top-level sibling before
`deployment_manifest` in the manifest example:

```json
"tool_governance": {
  "enabled": true,
  "contract_version": "1.0",
  "source": {
    "tool_contracts": "specs/SPEC.md#6-tool-contracts",
    "action_gates": "specs/SPEC.md#8-human-interaction-points"
  },
  "tools": [
    {
      "name": "returns_apply_decision",
      "action_class": "reversible-write",
      "decision": "conditional",
      "gate_id": "GATE-001",
      "enforcement_point": "mcp-server",
      "policy_id": "TG-RETURNS-001",
      "required_audit_fields": [
        "event_id",
        "event_type",
        "timestamp",
        "correlation_id",
        "contract_sha256",
        "policy_id",
        "tool_name",
        "action_class",
        "decision",
        "enforcement_point",
        "adapter_id",
        "actor_id",
        "gate_id",
        "approval_id"
      ]
    }
  ]
},
```

Add these normative sentences adjacent to the example:

```markdown
`tool_governance` is opt-in only via `tool_governance.enabled: true`; absence or
`enabled: false` preserves legacy behavior. When enabled, every exact canonical
SPEC section 6 tool must appear exactly once with an explicit decision;
unclassified tools are gaps, never implicit allows. SPEC sections 6 and 8 remain
the source of truth and the manifest is their generated machine projection.
```

- [ ] **Step 5: Run the focused and existing design suites**

Run:

```bash
python -m pytest skills/threadlight-design/tests/test_tool_governance_template.py skills/threadlight-design/tests/test_skill_contract_check.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit the design contract**

```bash
git add skills/threadlight-design/SKILL.md skills/threadlight-design/references/speckit-template.md skills/threadlight-design/tests/test_tool_governance_template.py
git commit -m "docs(design): define tool governance contract"
```

### Task 3: Add the governed fixture and pin backward compatibility

**Files:**
- Modify: `skills/threadlight-design/tests/test_tool_governance_template.py`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/specs/SPEC.md`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/specs/foundation.md`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/specs/manifest.json`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/policies/tool-governance/adapter-manifest.json`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/policies/tool-governance/generated/mcp-policy.json`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/src/mcp/server.py`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/tests/tool_governance_probe.py`
- Create: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/tests/tool-governance-probe-manifest.json`
- Verify unchanged: `examples/returns-triage-governed/specs/SPEC.md`
- Verify unchanged: `examples/returns-triage-governed/specs/manifest.json`

- [ ] **Step 1: Write the failing fixture and legacy tests**

Append to `test_tool_governance_template.py`:

```python
import json


REPO_ROOT = SKILL_DIR.parents[1]
GOVERNED = (
    REPO_ROOT
    / "skills"
    / "threadlight-safe-check"
    / "tests"
    / "fixtures"
    / "tool-governance-enabled"
)
LEGACY_MANIFEST = (
    REPO_ROOT
    / "examples"
    / "returns-triage-governed"
    / "specs"
    / "manifest.json"
)


def test_governed_fixture_has_allow_deny_and_conditional_tools():
    manifest = json.loads(
        (GOVERNED / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    decisions = {
        item["decision"] for item in manifest["tool_governance"]["tools"]
    }
    assert decisions == {"allow", "deny", "conditional"}


def test_legacy_example_remains_opted_out():
    manifest = json.loads(LEGACY_MANIFEST.read_text(encoding="utf-8"))
    assert "tool_governance" not in manifest
```

- [ ] **Step 2: Run the fixture tests and verify the governed fixture is missing**

Run:

```bash
python -m pytest skills/threadlight-design/tests/test_tool_governance_template.py -k 'fixture or legacy' -v
```

Expected: `test_governed_fixture_has_allow_deny_and_conditional_tools` FAILS
with `FileNotFoundError`; the legacy test PASSES.

- [ ] **Step 3: Create the governed SPEC and foundation**

Create `specs/SPEC.md` in the fixture:

```markdown
# Tool Governance Fixture

## 6. Tool Contracts

### `inventory_read`
- **Description**: Read a synthetic inventory record.
- **Action class**: `read`
- **Decision**: `allow`
- **Enforcement point**: `mcp-server`
- **Policy ID**: `TG-FIXTURE-READ`
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`

### `external_notify`
- **Description**: Synthetic external notification canary; no real channel exists.
- **Action class**: `external-side-effect`
- **Decision**: `deny`
- **Enforcement point**: `mcp-server`
- **Policy ID**: `TG-FIXTURE-DENY`
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`

### `returns_apply_decision`
- **Description**: Apply a synthetic reversible return decision.
- **Action class**: `reversible-write`
- **Decision**: `conditional`
- **HITL gate ID**: `GATE-001`
- **Enforcement point**: `mcp-server`
- **Policy ID**: `TG-FIXTURE-HITL`
- **Required audit fields**: `event_id`, `event_type`, `timestamp`,
  `correlation_id`, `contract_sha256`, `policy_id`, `tool_name`,
  `action_class`, `decision`, `enforcement_point`, `adapter_id`, `actor_id`,
  `gate_id`, `approval_id`

## 8. Human Interaction Points

### Synthetic supervisor approval
- **Gate ID**: `GATE-001`
- **Action gate**: `approve`
- **Approval propagation**: return `approval_id` with the original `correlation_id`
```

Create `specs/foundation.md`:

````markdown
# Foundation

```yaml
framework: github-copilot-sdk
runtime_shape: agent
protocol: invocations
policy_route: default-agent
source: provided
```
````

- [ ] **Step 4: Create the governed manifest with exact keys**

Create `specs/manifest.json` with a normal deployment manifest and this
governance block:

```json
{
  "name": "tool-governance-fixture",
  "tool_governance": {
    "enabled": true,
    "contract_version": "1.0",
    "source": {
      "tool_contracts": "specs/SPEC.md#6-tool-contracts",
      "action_gates": "specs/SPEC.md#8-human-interaction-points"
    },
    "tools": [
      {
        "name": "inventory_read",
        "action_class": "read",
        "decision": "allow",
        "enforcement_point": "mcp-server",
        "policy_id": "TG-FIXTURE-READ",
        "required_audit_fields": ["event_id", "event_type", "timestamp", "correlation_id", "contract_sha256", "policy_id", "tool_name", "action_class", "decision", "enforcement_point", "adapter_id", "actor_id"]
      },
      {
        "name": "external_notify",
        "action_class": "external-side-effect",
        "decision": "deny",
        "enforcement_point": "mcp-server",
        "policy_id": "TG-FIXTURE-DENY",
        "required_audit_fields": ["event_id", "event_type", "timestamp", "correlation_id", "contract_sha256", "policy_id", "tool_name", "action_class", "decision", "enforcement_point", "adapter_id", "actor_id"]
      },
      {
        "name": "returns_apply_decision",
        "action_class": "reversible-write",
        "decision": "conditional",
        "gate_id": "GATE-001",
        "enforcement_point": "mcp-server",
        "policy_id": "TG-FIXTURE-HITL",
        "required_audit_fields": ["event_id", "event_type", "timestamp", "correlation_id", "contract_sha256", "policy_id", "tool_name", "action_class", "decision", "enforcement_point", "adapter_id", "actor_id", "gate_id", "approval_id"]
      }
    ]
  },
  "deployment_manifest": {
    "module_selectors": {"aca-mcp": "yes"},
    "services": [{"name": "mcp", "host": "containerapp", "src": "src/mcp"}],
    "scheduled_jobs": [],
    "channels": [],
    "expected_resource_types": ["Microsoft.App/containerApps"]
  }
}
```

- [ ] **Step 5: Create adapter, policy, and wire-signal fixtures**

Create `policies/tool-governance/generated/mcp-policy.json`:

```json
{
  "schema": "threadlight.tool-governance/mcp-server/v1",
  "contract_sha256": "",
  "rules": [
    {"policy_id": "TG-FIXTURE-READ", "tool_name": "inventory_read", "decision": "allow"},
    {"policy_id": "TG-FIXTURE-DENY", "tool_name": "external_notify", "decision": "deny"},
    {"policy_id": "TG-FIXTURE-HITL", "tool_name": "returns_apply_decision", "decision": "conditional", "gate_id": "GATE-001"}
  ]
}
```

Create `src/mcp/server.py`:

```python
TOOL_GOVERNANCE_BINDING = "threadlight.tool-governance/mcp-server/v1"


def before_tool_call(tool_name: str) -> str:
    return f"governed:{tool_name}"
```

Create `policies/tool-governance/adapter-manifest.json`:

```json
{
  "schema": "threadlight.tool-governance-adapter/v1",
  "contract_sha256": "",
  "runtime": {
    "framework": "github-copilot-sdk",
    "runtime_shape": "agent",
    "protocol": "invocations"
  },
  "bindings": [
    {
      "tool_name": "inventory_read",
      "enforcement_point": "mcp-server",
      "adapter_id": "mcp-tool-governance",
      "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
      "wire_signals": [{"path": "src/mcp/server.py", "kind": "mcp-server-policy-binding"}]
    },
    {
      "tool_name": "external_notify",
      "enforcement_point": "mcp-server",
      "adapter_id": "mcp-tool-governance",
      "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
      "wire_signals": [{"path": "src/mcp/server.py", "kind": "mcp-server-policy-binding"}]
    },
    {
      "tool_name": "returns_apply_decision",
      "enforcement_point": "mcp-server",
      "adapter_id": "mcp-tool-governance",
      "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
      "wire_signals": [{"path": "src/mcp/server.py", "kind": "mcp-server-policy-binding"}]
    }
  ],
  "audit": {
    "schema": "threadlight.tool-governance-audit/v1",
    "sink": "fixture-memory"
  },
  "probe": {
    "entrypoint": "tests/tool_governance_probe.py",
    "evidence": "tests/tool-governance-probe-manifest.json"
  }
}
```

- [ ] **Step 6: Create the canary-only fixture probe**

Create `tests/tool_governance_probe.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


THREADLIGHT_CANARY_ONLY = True


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="tests/tool-governance-probe-manifest.json"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    adapter = json.loads(
        (
            root / "policies" / "tool-governance" / "adapter-manifest.json"
        ).read_text(encoding="utf-8")
    )
    vectors = [
        {
            "id": "allow-canary",
            "expected_decision": "allow",
            "observed_decision": "allow",
            "expected_execution_count": 1,
            "observed_execution_count": 1,
            "correlation_id": "probe-allow-001",
            "decision_event_ids": ["audit-allow-001"],
            "outcome_event_ids": ["outcome-allow-001"],
            "status": "pass",
        },
        {
            "id": "deny-canary",
            "expected_decision": "deny",
            "observed_decision": "deny",
            "expected_execution_count": 0,
            "observed_execution_count": 0,
            "correlation_id": "probe-deny-001",
            "decision_event_ids": ["audit-deny-001"],
            "outcome_event_ids": [],
            "status": "pass",
        },
        {
            "id": "conditional-canary",
            "expected_decision": "conditional",
            "observed_decision": "conditional",
            "expected_execution_count": 1,
            "observed_execution_count": 1,
            "correlation_id": "probe-hitl-001",
            "gate_id": "GATE-001",
            "approval_id": "approval-fixture-001",
            "decision_event_ids": ["audit-hitl-001"],
            "outcome_event_ids": ["outcome-hitl-001"],
            "status": "pass",
        },
    ]
    payload = {
        "schema": "threadlight.tool-governance-probe/v1",
        "contract_sha256": canonical_sha256(manifest["tool_governance"]),
        "adapter_manifest_sha256": canonical_sha256(adapter),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vectors": vectors,
        "audit_field_results": [
            {"vector_id": item["id"], "missing": [], "status": "pass"}
            for item in vectors
        ],
        "status": "pass",
    }
    (root / args.out).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Bind fixture hashes and generate fresh probe evidence**

Run:

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path("skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled")
manifest_path = root / "specs/manifest.json"
adapter_path = root / "policies/tool-governance/adapter-manifest.json"
policy_path = root / "policies/tool-governance/generated/mcp-policy.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
policy = json.loads(policy_path.read_text(encoding="utf-8"))
payload = json.dumps(
    manifest["tool_governance"],
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")
adapter["contract_sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
policy["contract_sha256"] = adapter["contract_sha256"]
adapter_path.write_text(json.dumps(adapter, indent=2) + "\n", encoding="utf-8")
policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
PY
python3 skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/tests/tool_governance_probe.py
```

Expected: `tests/tool-governance-probe-manifest.json` exists and contains three
passing vectors.

- [ ] **Step 8: Run fixture/backward-compatibility tests**

Run:

```bash
python -m pytest skills/threadlight-design/tests/test_tool_governance_template.py -k 'fixture or legacy' -v
git diff --exit-code -- examples/returns-triage-governed/specs/SPEC.md examples/returns-triage-governed/specs/manifest.json
```

Expected: tests PASS and `git diff` exits 0.

- [ ] **Step 9: Commit the governed fixture**

```bash
git add skills/threadlight-design/tests/test_tool_governance_template.py skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled
git commit -m "test: add governed tool contract fixture"
```

### Task 4: Implement safe-check design validation

**Files:**
- Modify: `skills/threadlight-safe-check/SKILL.md:177-207`
- Modify: `skills/threadlight-safe-check/scripts/safe_check.py:36-105`
- Modify: `skills/threadlight-safe-check/scripts/safe_check.py:299-348`
- Modify: `skills/threadlight-safe-check/tests/test_safe_check.py:248-274`
- Synchronize: `examples/returns-triage-governed/tests/safe_check.py`

- [ ] **Step 1: Write failing design-gate tests**

Append tests that load the governed fixture and call a new pure helper:

```python
import copy
import json

import pytest


GOVERNED_FIXTURE = (
    SKILL_DIR / "tests" / "fixtures" / "tool-governance-enabled"
)


def _governed_inputs():
    manifest = json.loads(
        (GOVERNED_FIXTURE / "specs" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    spec_text = (GOVERNED_FIXTURE / "specs" / "SPEC.md").read_text(
        encoding="utf-8"
    )
    return manifest, spec_text


def test_tool_governance_design_contract_passes_fixture():
    manifest, spec_text = _governed_inputs()
    result, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert gaps == []
    assert result["enabled"] is True
    assert result["tools_count"] == 3
    assert result["contract_sha256"].startswith("sha256:")


def test_new_unclassified_tool_is_a_design_gap():
    manifest, spec_text = _governed_inputs()
    spec_text = spec_text.replace(
        "## 8. Human Interaction Points",
        "### `new_unclassified_tool`\n"
        "- **Action class**: `read`\n"
        "- **Decision**: `allow`\n"
        "- **Enforcement point**: `mcp-server`\n"
        "- **Policy ID**: `TG-NEW`\n"
        "- **Required audit fields**: `event_id`\n\n"
        "## 8. Human Interaction Points",
    )
    _, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert "unclassified canonical tool: new_unclassified_tool" in gaps


def test_disabled_or_absent_contract_preserves_legacy_behavior():
    _, spec_text = _governed_inputs()
    for manifest in ({}, {"tool_governance": {"enabled": False}}):
        result, gaps = sc.validate_tool_governance_design(manifest, spec_text)
        assert result == {"enabled": False, "status": "not-applicable"}
        assert gaps == []


def test_conditional_tool_requires_existing_gate():
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"][2]["gate_id"] = "GATE-999"
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "unknown gate_id GATE-999 for returns_apply_decision" in gaps


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("action_class", "unknown", "invalid action_class"),
        ("decision", "sometimes", "invalid decision"),
        ("enforcement_point", "prompt", "invalid enforcement_point"),
    ],
)
def test_tool_governance_schema_rejects_unknown_enums(
    field, value, expected
):
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"][0][field] = value
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert any(expected in gap for gap in gaps)


def test_tool_governance_schema_rejects_unknown_keys():
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["implicit_allow"] = True
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "tool_governance unknown keys: implicit_allow" in gaps


def test_tool_governance_schema_rejects_duplicate_tools_and_gates():
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"].append(
        copy.deepcopy(broken["tool_governance"]["tools"][0])
    )
    spec_text += (
        "\n### Duplicate gate\n"
        "- **Gate ID**: `GATE-001`\n"
        "- **Action gate**: `approve`\n"
    )
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "duplicate governed tool: inventory_read" in gaps
    assert "duplicate SPEC section 8 gate_id: GATE-001" in gaps


def test_tool_governance_schema_rejects_grouped_headings():
    manifest, spec_text = _governed_inputs()
    spec_text = spec_text.replace(
        "### `inventory_read`",
        "### `inventory_read` / `inventory_list`",
    )
    _, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert any("grouped canonical tool heading" in gap for gap in gaps)
```

- [ ] **Step 2: Run the focused tests and verify the helper is missing**

Run:

```bash
python -m pytest skills/threadlight-safe-check/tests/test_safe_check.py -k tool_governance -v
```

Expected: FAIL with
`AttributeError: module 'safe_check' has no attribute 'validate_tool_governance_design'`.

- [ ] **Step 3: Add schema constants and canonical hashing**

Add below `MCP_CONFIG_CANDIDATES`:

```python
TOOL_GOVERNANCE_KEYS = {
    "enabled", "contract_version", "source", "tools",
}
SOURCE_KEYS = {"tool_contracts", "action_gates"}
TOOL_KEYS = {
    "name", "action_class", "decision", "gate_id",
    "enforcement_point", "policy_id", "required_audit_fields",
}
ACTION_CLASSES = {
    "read", "reversible-write", "irreversible-write", "external-side-effect",
}
DECISIONS = {"allow", "deny", "conditional"}
ENFORCEMENT_POINTS = {"agent-middleware", "mcp-server", "gateway"}
COMMON_AUDIT_FIELDS = {
    "event_id", "event_type", "timestamp", "correlation_id",
    "contract_sha256", "policy_id", "tool_name", "action_class",
    "decision", "enforcement_point", "adapter_id", "actor_id",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    import hashlib
    return "sha256:" + hashlib.sha256(payload).hexdigest()
```

Move `import hashlib` to the top-level import block before committing.

- [ ] **Step 4: Add deterministic SPEC parsing and design validation**

Add before `phase_design`:

```python
def _section(text: str, start: int, end: int) -> str:
    match = re.search(
        rf"(?ms)^##\s+{start}\.\s+.*?(?=^##\s+{end}\.|\Z)", text
    )
    return match.group(0) if match else ""


def _canonical_tools(spec_text: str) -> tuple[list[str], list[str]]:
    section = _section(spec_text, 6, 7)
    headings = re.findall(r"(?m)^###\s+(.+?)\s*$", section)
    grouped = [h for h in headings if "/" in h]
    names = [
        h.strip().strip("`")
        for h in headings
        if "/" not in h and re.fullmatch(r"`?[A-Za-z0-9_.-]+`?", h.strip())
    ]
    return names, grouped


def _gate_ids(spec_text: str) -> list[str]:
    return re.findall(
        r"(?m)^-\s+\*\*Gate ID\*\*:\s+`(GATE-\d{3})`\s*$",
        _section(spec_text, 8, 9),
    )


def validate_tool_governance_design(
    manifest: dict[str, Any], spec_text: str
) -> tuple[dict[str, Any], list[str]]:
    gaps: list[str] = []
    block = manifest.get("tool_governance")
    if block is None:
        return {"enabled": False, "status": "not-applicable"}, gaps
    if not isinstance(block, dict):
        return {"enabled": False, "status": "invalid"}, [
            "tool_governance must be an object"
        ]
    enabled = block.get("enabled", False)
    if not isinstance(enabled, bool):
        return {"enabled": False, "status": "invalid"}, [
            "tool_governance.enabled must be boolean"
        ]
    if not enabled:
        return {"enabled": False, "status": "not-applicable"}, gaps
    unknown = sorted(set(block) - TOOL_GOVERNANCE_KEYS)
    if unknown:
        gaps.append(f"tool_governance unknown keys: {', '.join(unknown)}")
    if block.get("contract_version") != "1.0":
        gaps.append("tool_governance.contract_version must be '1.0'")
    source = block.get("source")
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        gaps.append(
            "tool_governance.source must contain tool_contracts and action_gates"
        )
    elif not all(isinstance(source[key], str) and source[key] for key in SOURCE_KEYS):
        gaps.append("tool_governance.source values must be non-empty strings")
    tools = block.get("tools")
    if not isinstance(tools, list) or not tools:
        gaps.append("tool_governance.tools must be a non-empty array")
        tools = []
    canonical, grouped = _canonical_tools(spec_text)
    for heading in grouped:
        gaps.append(f"grouped canonical tool heading is invalid: {heading}")
    contract_names: list[str] = []
    gates = _gate_ids(spec_text)
    for item in tools:
        if not isinstance(item, dict):
            gaps.append("tool_governance.tools entries must be objects")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            gaps.append("tool_governance tool name must be non-empty string")
            continue
        contract_names.append(name)
        extra = sorted(set(item) - TOOL_KEYS)
        if extra:
            gaps.append(f"{name} unknown keys: {', '.join(extra)}")
        if item.get("action_class") not in ACTION_CLASSES:
            gaps.append(f"{name} has invalid action_class")
        if item.get("decision") not in DECISIONS:
            gaps.append(f"{name} has invalid decision")
        if item.get("enforcement_point") not in ENFORCEMENT_POINTS:
            gaps.append(f"{name} has invalid enforcement_point")
        if not isinstance(item.get("policy_id"), str) or not item["policy_id"]:
            gaps.append(f"{name} has invalid policy_id")
        audit = item.get("required_audit_fields")
        if (
            not isinstance(audit, list)
            or not all(isinstance(field, str) and field for field in audit)
            or len(audit) != len(set(audit))
        ):
            gaps.append(f"{name} required_audit_fields must be unique strings")
            audit = []
        missing = sorted(COMMON_AUDIT_FIELDS - set(audit))
        if missing:
            gaps.append(f"{name} missing audit fields: {', '.join(missing)}")
        conditional = item.get("decision") == "conditional"
        gate_id = item.get("gate_id")
        if conditional and gates.count(gate_id) != 1:
            gaps.append(f"unknown gate_id {gate_id} for {name}")
        if not conditional and "gate_id" in item:
            gaps.append(f"{name} gate_id is only valid for conditional decision")
        if conditional and not {"gate_id", "approval_id"}.issubset(set(audit)):
            gaps.append(f"{name} conditional audit fields are incomplete")
    for name in sorted(set(canonical) - set(contract_names)):
        gaps.append(f"unclassified canonical tool: {name}")
    for name in sorted(set(contract_names) - set(canonical)):
        gaps.append(f"contract tool absent from SPEC section 6: {name}")
    for name in sorted({n for n in contract_names if contract_names.count(n) > 1}):
        gaps.append(f"duplicate governed tool: {name}")
    for gate_id in sorted({g for g in gates if gates.count(g) > 1}):
        gaps.append(f"duplicate SPEC section 8 gate_id: {gate_id}")
    result = {
        "enabled": True,
        "status": "pass" if not gaps else "fail",
        "tools_count": len(tools),
        "contract_sha256": canonical_sha256(block),
    }
    return result, gaps
```

- [ ] **Step 5: Integrate the result into `phase_design`**

After the existing selector checks and before constructing `manifest`, add:

```python
    spec_path = manifest_path.with_name("SPEC.md")
    spec_text = (
        spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    )
    governance, governance_gaps = validate_tool_governance_design(data, spec_text)
    gaps.extend(governance_gaps)
```

Add `"tool_governance": governance` to the emitted design manifest.

- [ ] **Step 6: Document the opt-in design gate**

Add to the `--phase design` assertions in `threadlight-safe-check/SKILL.md`:

```markdown
8. If `tool_governance.enabled: true`, validate the closed schema, canonical
   SHA-256, one-to-one exact SPEC section 6 tool coverage, one-tool-per-heading
   rule, explicit action class/decision/policy/enforcement fields, common audit
   fields, and unique existing `GATE-NNN` references.
9. If the block is absent, omits `enabled`, or sets `enabled: false`, report
   governance as `not-applicable` and preserve legacy gaps/exit behavior.
```

- [ ] **Step 7: Synchronize the example CLI copy**

Run:

```bash
cp skills/threadlight-safe-check/scripts/safe_check.py examples/returns-triage-governed/tests/safe_check.py
```

Expected: only the mirrored test helper changes under the legacy example; its
SPEC and manifest remain unchanged.

- [ ] **Step 8: Run the full safe-check suite**

Run:

```bash
python -m pytest skills/threadlight-safe-check/tests/ -v
```

Expected: PASS, including byte-parity and the unclassified-tool regression.

- [ ] **Step 9: Commit design-phase validation**

```bash
git add skills/threadlight-safe-check/SKILL.md skills/threadlight-safe-check/scripts/safe_check.py skills/threadlight-safe-check/tests/test_safe_check.py examples/returns-triage-governed/tests/safe_check.py
git commit -m "feat(safe-check): validate tool governance design"
```

### Task 5: Define MAF and GHCP runtime adapter generation

**Files:**
- Create: `skills/threadlight-deploy/tests/test_tool_governance_contract.py`
- Modify: `skills/threadlight-deploy/SKILL.md:448-502`
- Modify: `skills/threadlight-deploy/SKILL.md:891-970`

- [ ] **Step 1: Write the failing deploy-contract tests**

Create:

```python
import json
import re
from pathlib import Path


SKILL = (
    Path(__file__).resolve().parents[1] / "SKILL.md"
).read_text(encoding="utf-8")


def _adapter_manifest():
    match = re.search(
        r"```json\n(\{\n"
        r'  "schema": "threadlight\.tool-governance-adapter/v1".*?'
        r"\n\})\n```",
        SKILL,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_ghcp_governance_is_never_claimed_in_process():
    assert "GHCP SDK tools are MCP-bound" in SKILL
    assert "mcp-server` or `gateway" in SKILL
    assert "GHCP + `agent-middleware` is an explicit deployment gap" in SKILL


def test_maf_uses_foundry_agt_pre_tool_enforcement():
    assert "invoke `foundry-agt`" in SKILL
    assert "deterministic pre-tool boundary" in SKILL
    assert "do not invent an AGT import or API name" in SKILL


def test_adapter_manifest_example_has_required_contract_keys():
    manifest = _adapter_manifest()
    assert set(manifest) == {
        "schema", "contract_sha256", "runtime", "bindings", "audit", "probe"
    }


def test_probe_generation_is_canary_only():
    assert "THREADLIGHT_CANARY_ONLY = True" in SKILL
    assert "expected_execution_count" in SKILL
    assert "production mutation endpoints are forbidden" in SKILL
```

- [ ] **Step 2: Run the tests and verify the runtime contract is absent**

Run:

```bash
python -m pytest skills/threadlight-deploy/tests/test_tool_governance_contract.py -v
```

Expected: FAIL on the GHCP or MAF wording assertion.

- [ ] **Step 3: Add the runtime capability matrix**

After the existing runtime variant table, add:

```markdown
#### Tool-governance adapter selection (opt-in)

Read `specs/manifest.json.tool_governance` only when `enabled: true`.
Preserve tool name, action class, decision, gate ID, enforcement point, policy
ID, and required audit fields exactly.

| Runtime/tool shape | Supported enforcement |
|---|---|
| MAF Agent in-process tool | invoke `foundry-agt` and record its real deterministic pre-tool boundary as `agent-middleware` |
| MAF Workflow executor tool | same pre-tool adapter on every executor that can invoke the tool |
| MAF MCP tool | the exact declared `agent-middleware`, `mcp-server`, or `gateway` point, only when the selected adapter supports it |
| GHCP SDK MCP tool | `mcp-server` or `gateway` |
| GHCP SDK in-process tool | unsupported |

GHCP SDK tools are MCP-bound; never claim that `CopilotClient` or
`InvocationAgentServerHost` enforces an in-process policy. GHCP +
`agent-middleware` is an explicit deployment gap. Do not substitute an
enforcement point without a SPEC change and do not degrade to prompt guidance.
```

- [ ] **Step 4: Add the MAF and GHCP generation rules**

Under the MAF container variant, add:

```markdown
When any governed tool declares `agent-middleware`, invoke `foundry-agt` and
use the installed skill's supported MAF integration. Require a deterministic
pre-tool boundary, generated AGT policy artifact, audit sink, and inspectable
wire signal. Do not invent an AGT import or API name: record the exact surface
selected by `foundry-agt` in the adapter manifest.
```

Under the GHCP variant, add:

```markdown
When governance is enabled, bind every GHCP tool at its declared MCP server or
gateway. Generated MCP servers carry
`threadlight.tool-governance/mcp-server/v1`; generated gateway policy carries
`threadlight.tool-governance/gateway/v1`. If neither boundary is available,
stop with an unsupported-enforcement gap.
```

- [ ] **Step 5: Add the exact adapter manifest and probe generation contract**

Add the adapter-manifest JSON from design section 6.2, using:

```json
{
  "schema": "threadlight.tool-governance-adapter/v1",
  "contract_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "runtime": {
    "framework": "github-copilot-sdk",
    "runtime_shape": "agent",
    "protocol": "invocations"
  },
  "bindings": [
    {
      "tool_name": "returns_apply_decision",
      "enforcement_point": "mcp-server",
      "adapter_id": "mcp-tool-governance",
      "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
      "wire_signals": [
        {
          "path": "src/mcp/server.py",
          "kind": "mcp-server-policy-binding"
        }
      ]
    }
  ],
  "audit": {
    "schema": "threadlight.tool-governance-audit/v1",
    "sink": "application-insights"
  },
  "probe": {
    "entrypoint": "tests/tool_governance_probe.py",
    "evidence": "tests/tool-governance-probe-manifest.json"
  }
}
```

Document that the generated probe contains the literal guard:

```python
THREADLIGHT_CANARY_ONLY = True
```

It emits `allow-canary`, `deny-canary`, and any gate-backed conditional vector
using the probe schema in the approved design. State: "production mutation
endpoints are forbidden."

Every generated policy artifact must contain the same canonical
`contract_sha256` value as `adapter-manifest.json`; a path alone is not binding
evidence.

- [ ] **Step 6: Run deploy tests**

Run:

```bash
python -m pytest skills/threadlight-deploy/tests/ -v
```

Expected: PASS.

- [ ] **Step 7: Commit adapter-generation instructions**

```bash
git add skills/threadlight-deploy/SKILL.md skills/threadlight-deploy/tests/test_tool_governance_contract.py
git commit -m "docs(deploy): define tool governance adapters"
```

### Task 6: Implement safe-check pre-deploy adapter validation

**Files:**
- Modify: `skills/threadlight-safe-check/SKILL.md:209-245`
- Modify: `skills/threadlight-safe-check/scripts/safe_check.py:355-458`
- Modify: `skills/threadlight-safe-check/tests/test_safe_check.py`
- Synchronize: `examples/returns-triage-governed/tests/safe_check.py`

- [ ] **Step 1: Write failing adapter-binding tests**

Append:

```python
def test_tool_governance_predeploy_fixture_passes():
    manifest, _ = _governed_inputs()
    result, gaps = sc.validate_tool_governance_predeploy(
        GOVERNED_FIXTURE, manifest
    )
    assert gaps == []
    assert result["status"] == "pass"
    assert result["bindings_count"] == 3


def test_ghcp_agent_middleware_is_an_explicit_gap():
    manifest, _ = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"][0][
        "enforcement_point"
    ] = "agent-middleware"
    _, gaps = sc.validate_tool_governance_predeploy(
        GOVERNED_FIXTURE, broken
    )
    assert (
        "GHCP tool inventory_read cannot use agent-middleware" in gaps
    )


def test_missing_wire_signal_is_a_gap(tmp_path):
    manifest, _ = _governed_inputs()
    fixture = tmp_path / "pilot"
    import shutil
    shutil.copytree(GOVERNED_FIXTURE, fixture)
    (fixture / "src" / "mcp" / "server.py").unlink()
    _, gaps = sc.validate_tool_governance_predeploy(fixture, manifest)
    assert any("wire signal path missing" in gap for gap in gaps)


def test_unknown_runtime_is_an_explicit_gap(tmp_path):
    import shutil
    fixture = tmp_path / "pilot"
    shutil.copytree(GOVERNED_FIXTURE, fixture)
    foundation = fixture / "specs" / "foundation.md"
    foundation.write_text(
        foundation.read_text(encoding="utf-8").replace(
            "github-copilot-sdk", "unknown-runtime"
        ),
        encoding="utf-8",
    )
    manifest, _ = _governed_inputs()
    _, gaps = sc.validate_tool_governance_predeploy(fixture, manifest)
    assert "unsupported tool governance runtime: unknown-runtime" in gaps


def test_adapter_hash_and_policy_artifact_are_required(tmp_path):
    import shutil
    fixture = tmp_path / "pilot"
    shutil.copytree(GOVERNED_FIXTURE, fixture)
    adapter_path = (
        fixture
        / "policies"
        / "tool-governance"
        / "adapter-manifest.json"
    )
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["contract_sha256"] = "sha256:" + ("0" * 64)
    adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
    (
        fixture
        / "policies"
        / "tool-governance"
        / "generated"
        / "mcp-policy.json"
    ).unlink()
    manifest, _ = _governed_inputs()
    _, gaps = sc.validate_tool_governance_predeploy(fixture, manifest)
    assert "tool governance adapter contract_sha256 mismatch" in gaps
    assert "inventory_read policy artifact missing or empty" in gaps
```

- [ ] **Step 2: Run tests and verify the helper is missing**

Run:

```bash
python -m pytest skills/threadlight-safe-check/tests/test_safe_check.py -k 'predeploy or ghcp_agent_middleware or missing_wire_signal or unknown_runtime or adapter_hash' -v
```

Expected: FAIL with missing `validate_tool_governance_predeploy`.

- [ ] **Step 3: Add runtime and wire-signal parsers**

Add:

```python
WIRE_SIGNAL_PATTERNS = {
    "pre-tool-policy-binding": (
        re.compile(r"agent_os\.integrations"),
        re.compile(r"pre_tool_call"),
    ),
    "mcp-server-policy-binding": (
        re.compile(r"threadlight\.tool-governance/mcp-server/v1"),
    ),
    "gateway-policy-binding": (
        re.compile(r"threadlight\.tool-governance/gateway/v1"),
    ),
    "dotnet-with-governance": (
        re.compile(r"\.WithGovernance\s*\("),
    ),
}


def _foundation_runtime(repo: Path) -> dict[str, str]:
    path = repo / "specs" / "foundation.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    result: dict[str, str] = {}
    for key in ("framework", "runtime_shape", "protocol"):
        match = re.search(rf"(?m)^\s*{key}:\s*([A-Za-z0-9_.-]+)", text)
        if match:
            result[key] = match.group(1)
    return result


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
```

- [ ] **Step 4: Add the pre-deploy validator**

Add:

```python
def validate_tool_governance_predeploy(
    repo: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    block = manifest.get("tool_governance")
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return {"enabled": False, "status": "not-applicable"}, []
    gaps: list[str] = []
    adapter_path = (
        repo / "policies" / "tool-governance" / "adapter-manifest.json"
    )
    adapter = _load_json_file(adapter_path)
    if adapter is None:
        return {"enabled": True, "status": "fail"}, [
            "tool governance adapter manifest missing or invalid"
        ]
    contract_hash = canonical_sha256(block)
    if adapter.get("schema") != "threadlight.tool-governance-adapter/v1":
        gaps.append("tool governance adapter schema mismatch")
    if adapter.get("contract_sha256") != contract_hash:
        gaps.append("tool governance adapter contract_sha256 mismatch")
    runtime = adapter.get("runtime")
    foundation = _foundation_runtime(repo)
    if not isinstance(runtime, dict) or any(
        runtime.get(key) != foundation.get(key)
        for key in ("framework", "runtime_shape", "protocol")
    ):
        gaps.append("tool governance adapter runtime differs from foundation")
    framework = foundation.get("framework")
    if framework not in {
        "github-copilot-sdk", "microsoft-agent-framework",
    }:
        gaps.append(f"unsupported tool governance runtime: {framework}")
    bindings = adapter.get("bindings")
    if not isinstance(bindings, list):
        bindings = []
        gaps.append("tool governance adapter bindings must be an array")
    by_name: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        if isinstance(binding, dict) and isinstance(binding.get("tool_name"), str):
            by_name.setdefault(binding["tool_name"], []).append(binding)
    for tool in block.get("tools", []):
        name = tool["name"]
        matches = by_name.get(name, [])
        if len(matches) != 1:
            gaps.append(f"governed tool {name} must have exactly one binding")
            continue
        binding = matches[0]
        point = tool["enforcement_point"]
        if binding.get("enforcement_point") != point:
            gaps.append(f"{name} binding enforcement_point mismatch")
        if not isinstance(binding.get("adapter_id"), str) or not binding["adapter_id"]:
            gaps.append(f"{name} adapter_id missing")
        if framework == "github-copilot-sdk" and point == "agent-middleware":
            gaps.append(f"GHCP tool {name} cannot use agent-middleware")
        policy_path = repo / str(binding.get("policy_artifact", ""))
        if not policy_path.is_file() or policy_path.stat().st_size == 0:
            gaps.append(f"{name} policy artifact missing or empty")
        elif contract_hash not in policy_path.read_text(encoding="utf-8"):
            gaps.append(f"{name} policy artifact contract hash mismatch")
        signals = binding.get("wire_signals")
        if not isinstance(signals, list) or not signals:
            gaps.append(f"{name} has no wire signals")
            continue
        for signal in signals:
            path = repo / str(signal.get("path", ""))
            kind = signal.get("kind")
            if not path.is_file():
                gaps.append(f"{name} wire signal path missing: {path}")
                continue
            patterns = WIRE_SIGNAL_PATTERNS.get(kind)
            text = path.read_text(encoding="utf-8")
            if patterns is None or not all(p.search(text) for p in patterns):
                gaps.append(f"{name} unresolved wire signal kind: {kind}")
    audit = adapter.get("audit")
    if (
        not isinstance(audit, dict)
        or audit.get("schema") != "threadlight.tool-governance-audit/v1"
        or not isinstance(audit.get("sink"), str)
        or not audit["sink"]
    ):
        gaps.append("tool governance audit configuration is incomplete")
    probe = adapter.get("probe")
    if (
        not isinstance(probe, dict)
        or not isinstance(probe.get("entrypoint"), str)
        or not isinstance(probe.get("evidence"), str)
        or not probe["entrypoint"]
        or not probe["evidence"]
    ):
        gaps.append("tool governance probe configuration is incomplete")
    result = {
        "enabled": True,
        "status": "pass" if not gaps else "fail",
        "contract_sha256": contract_hash,
        "adapter_manifest": str(adapter_path.relative_to(repo)),
        "bindings_count": len(bindings),
    }
    return result, gaps
```

- [ ] **Step 5: Integrate the validator into `phase_predeploy`**

Immediately after the existing `gaps: list[str] = []`, add:

```python
    governance, governance_gaps = validate_tool_governance_predeploy(repo, data)
    gaps.extend(governance_gaps)
```

Add `"tool_governance": governance` to the pre-deploy manifest `extra` payload.

- [ ] **Step 6: Document the pre-deploy adapter gate**

Add to `threadlight-safe-check/SKILL.md`:

```markdown
When governance is enabled, pre-deploy requires
`policies/tool-governance/adapter-manifest.json`, an exact contract hash, the
same runtime tuple as `specs/foundation.md`, one binding per governed tool,
non-empty policy artifacts, and resolved adapter-specific wire signals. GHCP
accepts only `mcp-server` or `gateway`; MAF `agent-middleware` requires the real
`foundry-agt` pre-tool signal. Unknown runtimes and declared-but-unwired tools
are gaps. Prompt prose never passes this gate.
```

- [ ] **Step 7: Synchronize and run the safe-check suite**

Run:

```bash
cp skills/threadlight-safe-check/scripts/safe_check.py examples/returns-triage-governed/tests/safe_check.py
python -m pytest skills/threadlight-safe-check/tests/ -v
```

Expected: PASS.

- [ ] **Step 8: Commit pre-deploy validation**

```bash
git add skills/threadlight-safe-check/SKILL.md skills/threadlight-safe-check/scripts/safe_check.py skills/threadlight-safe-check/tests/test_safe_check.py examples/returns-triage-governed/tests/safe_check.py
git commit -m "feat(safe-check): verify governance adapter wiring"
```

### Task 7: Implement canary-only post-deploy contract probes

**Files:**
- Modify: `skills/threadlight-safe-check/SKILL.md:247-345`
- Modify: `skills/threadlight-safe-check/scripts/safe_check.py:465-964`
- Modify: `skills/threadlight-safe-check/tests/test_safe_check.py`
- Synchronize: `examples/returns-triage-governed/tests/safe_check.py`
- Use: `skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/tests/tool_governance_probe.py`

- [ ] **Step 1: Write failing probe-validation tests**

Append:

```python
def test_tool_governance_probe_fixture_passes():
    manifest, _ = _governed_inputs()
    result, gaps = sc.validate_tool_governance_probe(
        GOVERNED_FIXTURE, manifest, run_probe=False
    )
    assert gaps == []
    assert result["status"] == "pass"


def test_deny_execution_is_a_gap(tmp_path):
    import shutil
    fixture = tmp_path / "pilot"
    shutil.copytree(GOVERNED_FIXTURE, fixture)
    evidence_path = fixture / "tests" / "tool-governance-probe-manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    deny = next(v for v in evidence["vectors"] if v["id"] == "deny-canary")
    deny["observed_execution_count"] = 1
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    manifest = json.loads(
        (fixture / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    _, gaps = sc.validate_tool_governance_probe(
        fixture, manifest, run_probe=False
    )
    assert "deny-canary executed 1 times; expected 0" in gaps


def test_probe_entrypoint_must_be_canary_only(tmp_path):
    import shutil
    fixture = tmp_path / "pilot"
    shutil.copytree(GOVERNED_FIXTURE, fixture)
    probe = fixture / "tests" / "tool_governance_probe.py"
    probe.write_text("print('unsafe')\n", encoding="utf-8")
    manifest = json.loads(
        (fixture / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    _, gaps = sc.validate_tool_governance_probe(
        fixture, manifest, run_probe=True
    )
    assert "probe entrypoint is not marked canary-only" in gaps


def test_missing_audit_correlation_is_a_gap(tmp_path):
    import shutil
    fixture = tmp_path / "pilot"
    shutil.copytree(GOVERNED_FIXTURE, fixture)
    evidence_path = fixture / "tests" / "tool-governance-probe-manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    allow = next(v for v in evidence["vectors"] if v["id"] == "allow-canary")
    allow["correlation_id"] = ""
    allow["decision_event_ids"] = []
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    manifest = json.loads(
        (fixture / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    _, gaps = sc.validate_tool_governance_probe(
        fixture, manifest, run_probe=False
    )
    assert "allow-canary has no decision audit event" in gaps
    assert "allow-canary has no correlation_id" in gaps
```

- [ ] **Step 2: Run tests and verify probe validation is missing**

Run:

```bash
python -m pytest skills/threadlight-safe-check/tests/test_safe_check.py -k 'probe_fixture or deny_execution or canary_only or audit_correlation' -v
```

Expected: FAIL with missing `validate_tool_governance_probe`.

- [ ] **Step 3: Add the guarded probe runner**

Add:

```python
def _run_governance_probe(
    repo: Path, adapter: dict[str, Any], gaps: list[str]
) -> None:
    probe = adapter.get("probe")
    entrypoint = repo / str(
        probe.get("entrypoint", "") if isinstance(probe, dict) else ""
    )
    if not entrypoint.is_file():
        gaps.append("tool governance probe entrypoint missing")
        return
    source = entrypoint.read_text(encoding="utf-8")
    if "THREADLIGHT_CANARY_ONLY = True" not in source:
        gaps.append("probe entrypoint is not marked canary-only")
        return
    evidence = str(probe.get("evidence", ""))
    result = subprocess.run(
        [sys.executable, str(entrypoint), "--out", evidence],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        gaps.append(
            "tool governance probe failed: "
            + (result.stderr.strip() or result.stdout.strip())
        )
```

- [ ] **Step 4: Add evidence validation**

Add:

```python
def validate_tool_governance_probe(
    repo: Path,
    manifest: dict[str, Any],
    *,
    run_probe: bool,
) -> tuple[dict[str, Any], list[str]]:
    block = manifest.get("tool_governance")
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return {"enabled": False, "status": "not-applicable"}, []
    gaps: list[str] = []
    adapter_path = (
        repo / "policies" / "tool-governance" / "adapter-manifest.json"
    )
    adapter = _load_json_file(adapter_path)
    if adapter is None:
        return {"enabled": True, "status": "fail"}, [
            "tool governance adapter manifest missing or invalid"
        ]
    if run_probe:
        _run_governance_probe(repo, adapter, gaps)
    probe_cfg = adapter.get("probe")
    evidence_path = repo / str(
        probe_cfg.get("evidence", "")
        if isinstance(probe_cfg, dict) else ""
    )
    evidence = _load_json_file(evidence_path)
    if evidence is None:
        gaps.append("tool governance probe evidence missing or invalid")
        return {"enabled": True, "status": "fail"}, gaps
    if evidence.get("schema") != "threadlight.tool-governance-probe/v1":
        gaps.append("tool governance probe schema mismatch")
    if evidence.get("contract_sha256") != canonical_sha256(block):
        gaps.append("tool governance probe contract_sha256 mismatch")
    if evidence.get("adapter_manifest_sha256") != canonical_sha256(adapter):
        gaps.append("tool governance probe adapter_manifest_sha256 mismatch")
    vectors = {
        item.get("id"): item
        for item in evidence.get("vectors", [])
        if isinstance(item, dict)
    }
    for vector_id, expected_count in (
        ("allow-canary", 1),
        ("deny-canary", 0),
    ):
        vector = vectors.get(vector_id)
        if vector is None:
            gaps.append(f"{vector_id} missing")
            continue
        observed = vector.get("observed_execution_count")
        if observed != expected_count:
            gaps.append(
                f"{vector_id} executed {observed} times; expected {expected_count}"
            )
        if not vector.get("decision_event_ids"):
            gaps.append(f"{vector_id} has no decision audit event")
        if not vector.get("correlation_id"):
            gaps.append(f"{vector_id} has no correlation_id")
        if vector.get("observed_decision") != vector.get("expected_decision"):
            gaps.append(f"{vector_id} observed decision mismatch")
    allow = vectors.get("allow-canary", {})
    if len(allow.get("outcome_event_ids", [])) != 1:
        gaps.append("allow-canary must emit one tool-outcome event")
    deny = vectors.get("deny-canary", {})
    if deny.get("outcome_event_ids"):
        gaps.append("deny-canary emitted a tool-outcome event")
    if any(t.get("decision") == "conditional" for t in block.get("tools", [])):
        conditional = vectors.get("conditional-canary", {})
        for key in ("correlation_id", "gate_id", "approval_id"):
            if not conditional.get(key):
                gaps.append(f"conditional-canary missing {key}")
        if conditional.get("observed_execution_count") != 1:
            gaps.append("conditional-canary must execute exactly once after approval")
    audit_results = evidence.get("audit_field_results")
    if (
        not isinstance(audit_results, list)
        or not all(isinstance(item, dict) for item in audit_results)
        or {item.get("vector_id") for item in audit_results}
        != set(vectors)
        or any(
            item.get("status") != "pass" or item.get("missing") != []
            for item in audit_results
            if isinstance(item, dict)
        )
    ):
        gaps.append("tool governance audit-field evidence is incomplete")
    result = {
        "enabled": True,
        "status": "pass" if not gaps else "fail",
        "evidence": str(evidence_path.relative_to(repo)),
        "contract_sha256": canonical_sha256(block),
    }
    return result, gaps
```

- [ ] **Step 5: Integrate post-deploy probing without changing Azure probes**

After `_repo_root_for_manifest` resolves the project root in
`phase_postdeploy`, add:

```python
    tool_governance, tool_governance_gaps = validate_tool_governance_probe(
        resolved_root, data, run_probe=True
    )
    gaps.extend(tool_governance_gaps)
```

Add `"tool_governance": tool_governance` to the existing post-deploy payload.
Do not remove or reorder the existing Azure resource, image, job, App Insights,
bot auth, Cosmos firewall, integration-binding, channel, or schedule checks.

- [ ] **Step 6: Document the canary-only post-deploy gate**

Add to `threadlight-safe-check/SKILL.md`:

```markdown
When governance is enabled, post-deploy executes only an adapter-owned probe
whose source contains `THREADLIGHT_CANARY_ONLY = True`. The allow canary must
execute exactly once, the deny canary must execute zero times, both must emit
correlatable policy-decision audit IDs, and allow must emit exactly one
tool-outcome event. A conditional fixture also carries `gate_id`,
`correlation_id`, and `approval_id`. Detailed evidence is
`tests/tool-governance-probe-manifest.json`; `tests/postdeploy-manifest.json`
stores its status and contract hash. Production mutation endpoints are forbidden.
```

- [ ] **Step 7: Synchronize and run the complete safe-check suite**

Run:

```bash
cp skills/threadlight-safe-check/scripts/safe_check.py examples/returns-triage-governed/tests/safe_check.py
python -m pytest skills/threadlight-safe-check/tests/ -v
```

Expected: PASS. The deny mutation test must fail before the implementation and
pass after it because the gap is detected.

- [ ] **Step 8: Commit post-deploy evidence validation**

```bash
git add skills/threadlight-safe-check/SKILL.md skills/threadlight-safe-check/scripts/safe_check.py skills/threadlight-safe-check/tests/test_safe_check.py examples/returns-triage-governed/tests/safe_check.py
git commit -m "feat(safe-check): prove governed tool behavior"
```

### Task 8: Extend the HITL correlation and approval contract

**Files:**
- Modify: `skills/threadlight-safe-check/tests/test_safe_check.py`
- Modify: `skills/threadlight-hitl-patterns/SKILL.md:43-66`
- Modify: `skills/threadlight-hitl-patterns/SKILL.md:80-175`
- Modify: `skills/threadlight-hitl-patterns/SKILL.md:276-290`
- Modify: `skills/threadlight-hitl-patterns/references/audit-schema.md:15-57`

- [ ] **Step 1: Write the failing documentation-contract test**

Append:

```python
def test_hitl_contract_documents_governed_correlation_fields():
    hitl = SKILL_DIR.parent / "threadlight-hitl-patterns"
    skill = (hitl / "SKILL.md").read_text(encoding="utf-8")
    audit = (hitl / "references" / "audit-schema.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "`gate_id`",
        "`correlation_id`",
        "`approval_id`",
        "`policy_id`",
        "`tool_name`",
        "`contract_sha256`",
    ):
        assert marker in skill
        assert marker in audit
    assert "persist approval before releasing the tool" in skill
    assert "same `approval_id` cannot execute twice" in skill
```

- [ ] **Step 2: Run the test and verify the governed fields are absent**

Run:

```bash
python -m pytest skills/threadlight-safe-check/tests/test_safe_check.py -k hitl_contract -v
```

Expected: FAIL on `gate_id` or `approval_id`.

- [ ] **Step 3: Extend the HITL input/output contract**

Add these section 8 inputs to `threadlight-hitl-patterns/SKILL.md`:

```markdown
- `Gate ID`: stable `GATE-NNN` for a governed conditional tool
- `correlation_id`: created before policy evaluation and preserved through the card
- `approval_id`: generated on approval and returned with the original correlation
- `policy_id`, `tool_name`, `contract_sha256`: copied from the enabled manifest
```

Add this handler rule:

```markdown
For a governed conditional tool, persist approval before releasing the tool.
Pass `gate_id`, `correlation_id`, and `approval_id` to the runtime adapter.
The same `approval_id` cannot execute twice; replay returns the prior outcome.
```

- [ ] **Step 4: Extend the canonical audit event additively**

Add these optional governed fields to the JSON example in
`references/audit-schema.md`:

```json
"gate_id": "GATE-001",
"correlation_id": "corr-7f1d",
"approval_id": "approval-28c4",
"policy_id": "TG-RETURNS-001",
"tool_name": "returns_apply_decision",
"contract_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
```

Add a table note that they are required only when a governed conditional tool
references the gate; legacy events remain valid without them.

- [ ] **Step 5: Run safe-check and design suites**

Run:

```bash
python -m pytest skills/threadlight-safe-check/tests/ skills/threadlight-design/tests/ -v
```

Expected: PASS.

- [ ] **Step 6: Commit HITL correlation**

```bash
git add skills/threadlight-safe-check/tests/test_safe_check.py skills/threadlight-hitl-patterns/SKILL.md skills/threadlight-hitl-patterns/references/audit-schema.md
git commit -m "docs(hitl): define governed approval correlation"
```

### Task 9: Consume adapter and probe evidence in production readiness

**Files:**
- Create: `skills/threadlight-production-ready/tests/test_tool_governance_evidence.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:679-710`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:2638-2776`

- [ ] **Step 1: Write failing readiness tests**

Create:

```python
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SAFE_FIXTURE = (
    SKILL_DIR.parent
    / "threadlight-safe-check"
    / "tests"
    / "fixtures"
    / "tool-governance-enabled"
)
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
spec = importlib.util.spec_from_file_location("production_ready_tg", SCRIPT)
pr = importlib.util.module_from_spec(spec)
sys.modules["production_ready_tg"] = pr
spec.loader.exec_module(pr)


def _fixture(tmp_path: Path):
    root = tmp_path / "pilot"
    shutil.copytree(SAFE_FIXTURE, root)
    manifest = json.loads(
        (root / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    contract_bytes = json.dumps(
        manifest["tool_governance"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    contract_sha256 = "sha256:" + hashlib.sha256(contract_bytes).hexdigest()
    (root / "tests" / "safe-check-design-manifest.json").write_text(
        json.dumps({
            "phase": "design",
            "tool_governance": {
                "enabled": True,
                "status": "pass",
                "tools_count": 3,
                "contract_sha256": contract_sha256,
            },
            "gaps": [],
        }),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(root / "tests" / "tool_governance_probe.py")],
        cwd=root,
        check=True,
    )
    return root, pr.RepoContext.from_repo(root, manifest)


def _by_id(findings):
    return {finding.id: finding for finding in findings}


def test_governed_fixture_passes_contract_adapter_and_probe(tmp_path):
    _, ctx = _fixture(tmp_path)
    findings = _by_id(pr._check_tool_governance_static(ctx))
    assert findings["AGT-007"].status == "pass"
    assert findings["AGT-008"].status == "pass"
    assert findings["AGT-103"].status == "pass"


def test_disabled_contract_is_not_applicable(tmp_path):
    _, ctx = _fixture(tmp_path)
    ctx.manifest.pop("tool_governance")
    findings = _by_id(pr._check_tool_governance_static(ctx))
    assert {finding.status for finding in findings.values()} == {
        "not-applicable"
    }


def test_missing_probe_is_must_fix_for_irreversible_tool(tmp_path):
    root, ctx = _fixture(tmp_path)
    ctx.manifest["tool_governance"]["tools"][0][
        "action_class"
    ] = "irreversible-write"
    (root / "tests" / "tool-governance-probe-manifest.json").unlink()
    findings = _by_id(pr._check_tool_governance_static(ctx))
    assert findings["AGT-103"].status == "must-fix"


def test_missing_probe_is_should_fix_without_irreversible_tool(tmp_path):
    root, ctx = _fixture(tmp_path)
    (root / "tests" / "tool-governance-probe-manifest.json").unlink()
    findings = _by_id(pr._check_tool_governance_static(ctx))
    assert findings["AGT-103"].status == "should-fix"


def test_stale_probe_is_should_fix(tmp_path):
    root, ctx = _fixture(tmp_path)
    path = root / "tests" / "tool-governance-probe-manifest.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["generated_at"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    findings = _by_id(pr._check_tool_governance_static(ctx))
    assert findings["AGT-103"].status == "should-fix"


def test_ghcp_agent_middleware_never_passes(tmp_path):
    _, ctx = _fixture(tmp_path)
    ctx.manifest["tool_governance"]["tools"][0][
        "enforcement_point"
    ] = "agent-middleware"
    findings = _by_id(pr._check_tool_governance_static(ctx))
    assert findings["AGT-008"].status == "must-fix"


@pytest.mark.parametrize(
    ("kind", "content"),
    [
        (
            "pre-tool-policy-binding",
            "agent_os.integrations\npre_tool_call\n",
        ),
        (
            "mcp-server-policy-binding",
            "threadlight.tool-governance/mcp-server/v1\n",
        ),
        (
            "gateway-policy-binding",
            "threadlight.tool-governance/gateway/v1\n",
        ),
        ("dotnet-with-governance", "builder.WithGovernance(policy);\n"),
    ],
)
def test_runtime_wire_signal_kinds_are_detected(tmp_path, kind, content):
    path = tmp_path / "wire.txt"
    path.write_text(content, encoding="utf-8")
    assert pr._tg_wire_signal_present(
        tmp_path, {"path": "wire.txt", "kind": kind}
    )
```

- [ ] **Step 2: Run tests and verify findings are missing**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_tool_governance_evidence.py -v
```

Expected: FAIL with missing `_check_tool_governance_static`.

- [ ] **Step 3: Add the new finding catalog entries**

Add after `AGT-006`:

```python
    "AGT-007": {"title": "Enabled tool-governance contract covers every canonical tool", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-008": {"title": "Declared tool-governance runtime adapter is wired", "pillar": "agent-governance", "severity": "must-fix", "tier": 0},
    "AGT-103": {"title": "Tool-governance allow/deny probe evidence is current and correlatable", "pillar": "agent-governance", "severity": "should-fix", "tier": 0},
```

`AGT-103` is tier 0 because this assessor validates a committed post-deploy
artifact and performs no Azure call. Its emitted status is dynamic even though
the catalog default severity is `should-fix`.

- [ ] **Step 4: Add local evidence helpers**

Add near the AGT checks:

```python
def _tg_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tg_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _tg_foundation_runtime(root: Path) -> dict[str, str]:
    path = root / "specs" / "foundation.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    result: dict[str, str] = {}
    for key in ("framework", "runtime_shape", "protocol"):
        match = re.search(rf"(?m)^\s*{key}:\s*([A-Za-z0-9_.-]+)", text)
        if match:
            result[key] = match.group(1)
    return result


def _tg_wire_signal_present(root: Path, signal: Any) -> bool:
    if not isinstance(signal, dict):
        return False
    path = root / str(signal.get("path", ""))
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    patterns = {
        "pre-tool-policy-binding": (
            r"agent_os\.integrations", r"pre_tool_call",
        ),
        "mcp-server-policy-binding": (
            r"threadlight\.tool-governance/mcp-server/v1",
        ),
        "gateway-policy-binding": (
            r"threadlight\.tool-governance/gateway/v1",
        ),
        "dotnet-with-governance": (r"\.WithGovernance\s*\(",),
    }.get(signal.get("kind"))
    return patterns is not None and all(re.search(p, text) for p in patterns)
```

- [ ] **Step 5: Implement the evidence-based findings**

Add:

```python
def _check_tool_governance_static(ctx: RepoContext) -> list[Finding]:
    block = ctx.manifest.get("tool_governance")
    ids = ("AGT-007", "AGT-008", "AGT-103")
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return [
            _mk_finding(fid, status="not-applicable",
                        detail="tool_governance is not enabled")
            for fid in ids
        ]
    contract_hash = _tg_sha256(block)
    design = _tg_json(ctx.root / "tests" / "safe-check-design-manifest.json")
    design_tg = design.get("tool_governance") if design else None
    contract_ok = (
        isinstance(design_tg, dict)
        and design_tg.get("status") == "pass"
        and design_tg.get("contract_sha256") == contract_hash
    )
    findings = [
        _mk_finding(
            "AGT-007",
            status="pass" if contract_ok else "must-fix",
            detail=(
                "safe-check design evidence covers the current contract"
                if contract_ok else
                "missing, failing, or hash-mismatched design evidence"
            ),
        )
    ]
    adapter = _tg_json(
        ctx.root / "policies" / "tool-governance" / "adapter-manifest.json"
    )
    adapter_ok = (
        adapter is not None
        and adapter.get("contract_sha256") == contract_hash
    )
    if adapter_ok:
        runtime = adapter.get("runtime")
        foundation = _tg_foundation_runtime(ctx.root)
        adapter_ok = (
            isinstance(runtime, dict)
            and all(
                runtime.get(key) == foundation.get(key)
                for key in ("framework", "runtime_shape", "protocol")
            )
        )
        bindings = adapter.get("bindings")
        adapter_ok = isinstance(bindings, list) and len(bindings) == len(
            block.get("tools", [])
        )
        framework = (adapter.get("runtime") or {}).get("framework")
        if framework not in {
            "github-copilot-sdk",
            "microsoft-agent-framework",
            "dotnet-harness",
        }:
            adapter_ok = False
        for tool in block.get("tools", []):
            matches = [
                item for item in bindings
                if item.get("tool_name") == tool.get("name")
            ]
            if len(matches) != 1:
                adapter_ok = False
                continue
            binding = matches[0]
            point = tool.get("enforcement_point")
            if binding.get("enforcement_point") != point:
                adapter_ok = False
            if framework == "github-copilot-sdk" and point == "agent-middleware":
                adapter_ok = False
            policy_path = ctx.root / str(binding.get("policy_artifact", ""))
            if not policy_path.is_file() or policy_path.stat().st_size == 0:
                adapter_ok = False
            elif contract_hash not in policy_path.read_text(encoding="utf-8"):
                adapter_ok = False
            signals = binding.get("wire_signals")
            if (
                not isinstance(signals, list)
                or not signals
                or not all(
                    _tg_wire_signal_present(ctx.root, signal)
                    for signal in signals
                )
            ):
                adapter_ok = False
    findings.append(_mk_finding(
        "AGT-008",
        status="pass" if adapter_ok else "must-fix",
        detail=(
            "runtime adapter, policy binding, and wire signals verified"
            if adapter_ok else
            "runtime adapter is missing, inconsistent, or unwired"
        ),
    ))
    probe = _tg_json(
        ctx.root / "tests" / "tool-governance-probe-manifest.json"
    )
    probe_ok = (
        probe is not None
        and probe.get("schema") == "threadlight.tool-governance-probe/v1"
        and probe.get("contract_sha256") == contract_hash
        and adapter is not None
        and probe.get("adapter_manifest_sha256") == _tg_sha256(adapter)
        and probe.get("status") == "pass"
    )
    if probe_ok:
        generated = _parse_utc_instant(probe.get("generated_at"))
        probe_ok = (
            generated is not None
            and timedelta(0)
            <= datetime.now(timezone.utc) - generated
            <= timedelta(hours=24)
        )
    if probe_ok:
        vectors = {
            item.get("id"): item for item in probe.get("vectors", [])
            if isinstance(item, dict)
        }
        probe_ok = (
            vectors.get("allow-canary", {}).get(
                "observed_execution_count"
            ) == 1
            and vectors.get("deny-canary", {}).get(
                "observed_execution_count"
            ) == 0
            and bool(vectors.get("allow-canary", {}).get("decision_event_ids"))
            and bool(vectors.get("deny-canary", {}).get("decision_event_ids"))
            and bool(vectors.get("allow-canary", {}).get("correlation_id"))
            and bool(vectors.get("deny-canary", {}).get("correlation_id"))
            and len(
                vectors.get("allow-canary", {}).get("outcome_event_ids", [])
            ) == 1
            and vectors.get("deny-canary", {}).get(
                "outcome_event_ids", []
            ) == []
        )
    if probe_ok and any(
        tool.get("decision") == "conditional"
        for tool in block.get("tools", [])
        if isinstance(tool, dict)
    ):
        conditional = vectors.get("conditional-canary", {})
        probe_ok = all(
            conditional.get(key)
            for key in ("correlation_id", "gate_id", "approval_id")
        )
    if probe_ok:
        audit_results = probe.get("audit_field_results")
        probe_ok = (
            isinstance(audit_results, list)
            and all(isinstance(item, dict) for item in audit_results)
            and {item.get("vector_id") for item in audit_results}
            == set(vectors)
            and all(
                item.get("status") == "pass" and item.get("missing") == []
                for item in audit_results
            )
        )
    irreversible = any(
        tool.get("action_class") == "irreversible-write"
        for tool in block.get("tools", [])
        if isinstance(tool, dict)
    )
    findings.append(_mk_finding(
        "AGT-103",
        status=(
            "pass" if probe_ok else
            "must-fix" if irreversible else
            "should-fix"
        ),
        detail=(
            "fresh allow/deny execution and audit correlation verified"
            if probe_ok else
            "probe evidence missing, stale, malformed, or uncorrelated"
        ),
    ))
    return findings
```

- [ ] **Step 6: Add the findings to both AGT static return paths**

Before each `return out` in `_check_agt_static`, add:

```python
    out.extend(_check_tool_governance_static(ctx))
```

Ensure the fresh govern-manifest path and the legacy heuristic path each emit
the three findings exactly once.

- [ ] **Step 7: Run focused and AGT regression tests**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_tool_governance_evidence.py skills/threadlight-production-ready/tests/test_agt_reframe_robustness.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit readiness evidence**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_tool_governance_evidence.py
git commit -m "feat(production-ready): consume tool governance evidence"
```

### Task 10: Align AGT finding IDs and titles

**Files:**
- Create: `skills/threadlight-production-ready/tests/test_agt_catalog_docs_sync.py`
- Modify: `skills/threadlight-production-ready/references/pillars/02-agent-governance.md:28-102`
- Modify: `skills/threadlight-production-ready/SKILL.md:218-228`
- Modify: `skills/threadlight-production-ready/SKILL.md:1045-1062`

- [ ] **Step 1: Write the failing catalog/documentation parity test**

Create:

```python
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "production_ready.py"
PILLAR = SKILL_DIR / "references" / "pillars" / "02-agent-governance.md"
spec = importlib.util.spec_from_file_location("production_ready_catalog", SCRIPT)
pr = importlib.util.module_from_spec(spec)
sys.modules["production_ready_catalog"] = pr
spec.loader.exec_module(pr)

ROW = re.compile(
    r"^\|\s*`(?P<id>AGT(?:-V4)?-\d{3})`\s*"
    r"\|\s*(?P<title>[^|]+?)\s*\|",
    re.MULTILINE,
)


def test_agent_governance_pillar_titles_match_catalog():
    documented = {
        match.group("id"): match.group("title").strip()
        for match in ROW.finditer(PILLAR.read_text(encoding="utf-8"))
    }
    catalog = {
        finding_id: meta["title"]
        for finding_id, meta in pr.FINDING_CATALOG.items()
        if meta["pillar"] == "agent-governance"
    }
    assert documented == catalog


def test_top_level_skill_no_longer_claims_import_is_enforcement():
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "AGT module imported in app code" not in skill
    assert "contract-probe evidence" in skill
```

- [ ] **Step 2: Run the test and verify current title drift**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_agt_catalog_docs_sync.py -v
```

Expected: FAIL with a dictionary diff including `AGT-101` and the new
`AGT-007`, `AGT-008`, or `AGT-103`.

- [ ] **Step 3: Reshape the pillar tables to carry exact catalog titles**

Use `ID | Catalog title | Check | Default status` columns. The version-agnostic
rows must contain these exact title cells:

```markdown
| `AGT-001` | AGT policy is schema-valid (lints clean) |
| `AGT-002` | policy.yaml present in repo |
| `AGT-003` | OWASP ASI 2026 verifier referenced |
| `AGT-004` | AGT policy ruleset version pinned |
| `AGT-005` | AGT governance gate runs in CI |
| `AGT-006` | AGT telemetry sink configured |
| `AGT-007` | Enabled tool-governance contract covers every canonical tool |
| `AGT-008` | Declared tool-governance runtime adapter is wired |
| `AGT-101` | Workload identity scoped to AGT-required RBAC |
| `AGT-102` | AGT denials visible in App Insights last 24h |
| `AGT-103` | Tool-governance allow/deny probe evidence is current and correlatable |
```

The v4 rows must contain these exact title cells:

```markdown
| `AGT-V4-001` | AGT v4 distribution names declared in dependencies |
| `AGT-V4-002` | AGT v4 policy uses ACS intervention_points schema |
| `AGT-V4-003` | AGT v4 dynamic policy conditions (time/cost/quota) detected |
| `AGT-V4-006` | AGT v4 composite GitHub Action pinned via toolkit-version |
| `AGT-V4-007` | AGT v4 audit fields present in committed verifier JSON |
| `AGT-V4-101` | AGT v4 denials carry v4-shaped policy_version in App Insights |
```

Keep their existing detection detail in the new `Check` column. Do not renumber
or redefine existing findings.

- [ ] **Step 4: Correct the stale skill summary**

Replace the pillar-2 summary with:

```markdown
| 2 | [`agent-governance`](references/pillars/02-agent-governance.md) | Committed AGT policy is schema-valid and CI-gated; enabled tool governance has complete canonical coverage, a runtime-appropriate adapter, and fresh contract-probe evidence | `foundry-agt` |
```

In the version history prose, state that imports and policy-file presence are
signals only; `AGT-008` and `AGT-103` require adapter and behavioral evidence.

- [ ] **Step 5: Run catalog, evidence, and AGT tests**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_agt_catalog_docs_sync.py skills/threadlight-production-ready/tests/test_tool_governance_evidence.py skills/threadlight-production-ready/tests/test_agt_reframe_robustness.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit the AGT alignment**

```bash
git add skills/threadlight-production-ready/references/pillars/02-agent-governance.md skills/threadlight-production-ready/SKILL.md skills/threadlight-production-ready/tests/test_agt_catalog_docs_sync.py
git commit -m "docs(production-ready): align AGT finding catalog"
```

### Task 11: Version, document, and validate the complete contract

**Files:**
- Modify: `skills/threadlight-design/SKILL.md:19`
- Modify: `skills/threadlight-deploy/SKILL.md:20`
- Modify: `skills/threadlight-safe-check/SKILL.md:23`
- Modify: `skills/threadlight-hitl-patterns/SKILL.md:15`
- Modify: `skills/threadlight-production-ready/SKILL.md:19`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py:490`
- Modify: `skills/threadlight-production-ready/tests/test_version.py:14`
- Modify: `skills/threadlight-design/tests/test_tool_governance_template.py`
- Modify: `skills/threadlight-deploy/tests/test_tool_governance_contract.py`
- Modify: `skills/threadlight-safe-check/tests/test_safe_check.py`
- Modify: `CHANGELOG.md:8-10`

- [ ] **Step 1: Make version tests fail on the intended releases**

Change `EXPECTED` in production-ready `test_version.py` to `0.12.0`. Add these
assertions to the new contract tests:

```python
def test_design_skill_version():
    assert 'version: "1.13.0"' in SKILL
```

```python
def test_deploy_skill_version():
    assert 'version: "1.7.0"' in SKILL
```

Add to `test_safe_check.py`:

```python
def test_safe_check_and_hitl_versions():
    safe_skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    hitl_skill = (
        SKILL_DIR.parent / "threadlight-hitl-patterns" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert 'version: "1.2.0"' in safe_skill
    assert 'version: "1.2.0"' in hitl_skill
```

- [ ] **Step 2: Run version tests and verify they fail**

Run:

```bash
python -m pytest skills/threadlight-design/tests/test_tool_governance_template.py skills/threadlight-deploy/tests/test_tool_governance_contract.py skills/threadlight-safe-check/tests/test_safe_check.py skills/threadlight-production-ready/tests/test_version.py -q
```

Expected: FAIL on the five old version values.

- [ ] **Step 3: Bump only the touched skill versions**

Apply:

```text
threadlight-design: 1.12.0 -> 1.13.0
threadlight-deploy: 1.6.4 -> 1.7.0
threadlight-safe-check: 1.1.1 -> 1.2.0
threadlight-hitl-patterns: 1.1.0 -> 1.2.0
threadlight-production-ready: 0.11.0 -> 0.12.0
production_ready.py VERSION: 0.11.0 -> 0.12.0
```

Do not change root plugin/marketplace versions in this feature commit; release
coordination owns those shared files while other PRs are in flight.

- [ ] **Step 4: Add the Unreleased changelog entry**

Insert an `Added` section before the current `Fixed` section:

```markdown
### Added

- **Opt-in runtime-agnostic tool governance.** SPEC sections 6 and 8 can now
  generate `specs/manifest.json.tool_governance`; safe-check validates complete
  canonical coverage, runtime-appropriate adapter wiring, and canary-only
  allow/deny audit evidence. MAF uses `foundry-agt` pre-tool enforcement where
  supported, GHCP SDK tools are governed at MCP server or gateway only, HITL
  approvals carry correlation/approval IDs, and production-ready consumes fresh
  contract-probe evidence through AGT-007/008/103.
```

- [ ] **Step 5: Run targeted suites**

Run:

```bash
python -m pytest skills/threadlight-design/tests/ -v
python -m pytest skills/threadlight-deploy/tests/ -v
python -m pytest skills/threadlight-safe-check/tests/ -v
python -m pytest skills/threadlight-production-ready/tests/ -v
```

Expected: all four commands PASS.

- [ ] **Step 6: Run repository acceptance guards**

Run:

```bash
python -m pytest scripts/ci/tests/ -v
python scripts/ci/check-skill-description-length.py
python scripts/ci/check-test-dirs-wired.py
python scripts/ci/run-standalone-tests.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Prove the legacy example did not opt in**

Run:

```bash
git diff --exit-code "$(git merge-base HEAD origin/main)" -- examples/returns-triage-governed/specs/SPEC.md examples/returns-triage-governed/specs/manifest.json
python3 - <<'PY'
import json
from pathlib import Path

manifest = json.loads(
    Path("examples/returns-triage-governed/specs/manifest.json").read_text(
        encoding="utf-8"
    )
)
assert "tool_governance" not in manifest
print("legacy example remains opted out")
PY
```

Expected: diff exits 0 and the script prints
`legacy example remains opted out`.

- [ ] **Step 8: Prove the governed fixture and canaries satisfy the acceptance contract**

Run:

```bash
python3 skills/threadlight-safe-check/tests/fixtures/tool-governance-enabled/tests/tool_governance_probe.py
python3 - <<'PY'
import json
from pathlib import Path

path = Path(
    "skills/threadlight-safe-check/tests/fixtures/"
    "tool-governance-enabled/tests/tool-governance-probe-manifest.json"
)
evidence = json.loads(path.read_text(encoding="utf-8"))
vectors = {item["id"]: item for item in evidence["vectors"]}
assert vectors["allow-canary"]["observed_execution_count"] == 1
assert vectors["deny-canary"]["observed_execution_count"] == 0
assert vectors["allow-canary"]["decision_event_ids"]
assert vectors["deny-canary"]["decision_event_ids"]
assert vectors["conditional-canary"]["gate_id"] == "GATE-001"
assert vectors["conditional-canary"]["approval_id"]
print("governance canaries and audit correlation pass")
PY
```

Expected: `governance canaries and audit correlation pass`.

- [ ] **Step 9: Commit release metadata and final validation changes**

```bash
git add CHANGELOG.md skills/threadlight-design/SKILL.md skills/threadlight-design/tests/test_tool_governance_template.py skills/threadlight-deploy/SKILL.md skills/threadlight-deploy/tests/test_tool_governance_contract.py skills/threadlight-safe-check/SKILL.md skills/threadlight-safe-check/tests/test_safe_check.py skills/threadlight-hitl-patterns/SKILL.md skills/threadlight-production-ready/SKILL.md skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_version.py
git commit -m "chore: release tool governance contract"
```

## Final implementation acceptance

Before opening or updating a pull request, run:

```bash
python -m pytest skills/threadlight-design/tests/ skills/threadlight-deploy/tests/ skills/threadlight-safe-check/tests/ skills/threadlight-production-ready/tests/ scripts/ci/tests/ -q
python scripts/ci/check-skill-description-length.py
python scripts/ci/check-test-dirs-wired.py
python scripts/ci/run-standalone-tests.py
git diff --check
git status --short
```

Expected: all tests and guards pass, `git diff --check` is silent, and
`git status --short` is empty after the final commit. Do not merge the branch;
report the commit series and wait for the coordinating owner to choose the
integration window.
