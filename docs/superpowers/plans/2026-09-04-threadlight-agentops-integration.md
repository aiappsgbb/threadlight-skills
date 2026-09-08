# Threadlight AgentOps Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, per-agent `threadlight-agentops` lifecycle step that validates AgentOps evidence and integrates it with existing Threadlight domain owners without duplicate execution, findings, or workflows.

**Architecture:** A new stdlib-only assessor discovers agent roots containing `agentops.yaml`, validates native AgentOps artifacts, and emits `specs/agentops-manifest.json`. Existing Threadlight eval, red-team, govern, CI/CD, and production-ready surfaces consume only the parts they own; Citadel and Safe Check remain unchanged.

**Tech Stack:** Python 3 stdlib, subprocess-safe AgentOps CLI calls, JSON/JSON Schema, pytest/unittest, GitHub Actions and Azure DevOps templates, Node lifecycle-canvas tests.

**Prerequisite:** `foundry-agentops` must be available from `aiappsgbb/awesome-gbb` at an immutable reviewed commit before implementing remediation links. This is satisfied by merged commit `2db28d1f52bf288f2d0fd40b7c8beb913ceeee09`; a merge is not a separate release or production certification.

## Implementation reconciliation (2026-09-08)

The snippets below preserve the original plan, not the final native API. The
implementation uses the actual AgentOps 0.14.0 artifact contract and current
Threadlight main after PR #127:

- Binding-scoped `threadlight-governance-manifest/v1` stays authoritative;
  AgentOps cannot satisfy its policy, runtime or deployed-proof requirements.
- The shared AgentOps validator rechecks native sources and same-process local
  receipts. Imported old files alone are not verified. Existing signatures are
  optional; no new PKI is an adoption prerequisite.
- Auto normalizes read-only evidence after invoke and **before** eval/red-team
  consumption to support reuse in the same pass. No opt-in skips without changing
  the existing pilot flow or initiating native operations.
- CI and explicit Doctor refresh use one packaged, approval-gated observer.
  Only normalized metadata may be published, never the raw native artifacts
  mentioned in the earlier workflow sketch.
- Canvas is advisory metadata observation, not another readiness gate.
- Plugin/marketplace candidate is 2.1.0, following current 2.0.0, rather than the
  superseded 1.14.0 version sketch. New tests are wired into the existing local CI
  suite; no new Azure run or resource authorization is implied by this change.

---

## File map

| Path | Responsibility |
|---|---|
| `skills/threadlight-agentops/SKILL.md` | Lifecycle-step contract and invocation |
| `skills/threadlight-agentops/scripts/agentops_check.py` | Discovery, validation, optional Doctor refresh, manifest emission |
| `skills/threadlight-agentops/references/agentops-manifest.schema.json` | Public manifest contract |
| `skills/threadlight-agentops/references/artifact-mapping.md` | Native-to-Threadlight mapping and dedup rules |
| `skills/threadlight-agentops/references/fixtures/*` | Mono-agent, multi-agent, invalid, stale, conflict, and privacy fixtures |
| `skills/threadlight-agentops/tests/test_agentops_check.py` | Assessor unit tests |
| `skills/threadlight-evals/scripts/evals_check.py` | AgentOps eval-result adapter |
| `skills/threadlight-evals/tests/test_evals_check.py` | Eval adapter tests |
| `skills/threadlight-redteam/scripts/redteam_check.py` | AgentOps normalized red-team adapter |
| `skills/threadlight-redteam/tests/test_redteam_check.py` | Red-team adapter tests |
| `skills/threadlight-govern/scripts/govern_check.py` | ASSERT/ACS supplemental evidence |
| `skills/threadlight-govern/tests/test_govern_check.py` | Governance precedence tests |
| `skills/threadlight-cicd/scripts/generate_pipeline.py` | AgentOps workflow-composition context |
| `skills/threadlight-cicd/references/*/*.tmpl` | GitHub/Azure DevOps AgentOps jobs |
| `skills/threadlight-cicd/tests/test_agentops_gate.py` | Pipeline ownership and secret-free tests |
| `skills/threadlight-production-ready/scripts/production_ready.py` | `AOPS-001` aggregate |
| `skills/threadlight-production-ready/tests/test_agentops_manifest.py` | Aggregate, dedup, and backward-compat tests |
| `skills/threadlight-production-ready/references/remediation-recipes/AOPS-001.md` | Sibling-skill remediation |
| `skills/threadlight-auto/references/orchestrator.py` | Optional resumable AgentOps stage |
| `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py` | Auto-stage behavior |
| `.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs` | Lifecycle canvas registration |
| `tests/canvas/lifecycle-registry.test.mjs` | Canvas registry contract |
| `plugin.json`, `README.md`, `THREADLIGHT.md`, `CHANGELOG.md` | Catalog and release surfaces |

### Task 1: Scaffold the manifest contract and RED fixtures

**Files:**
- Create: `skills/threadlight-agentops/SKILL.md`
- Create: `skills/threadlight-agentops/references/agentops-manifest.schema.json`
- Create: `skills/threadlight-agentops/references/artifact-mapping.md`
- Create: `skills/threadlight-agentops/references/fixtures/no-opt-in/`
- Create: `skills/threadlight-agentops/references/fixtures/mono-agent/`
- Create: `skills/threadlight-agentops/references/fixtures/multi-agent/`
- Create: `skills/threadlight-agentops/references/fixtures/invalid-evidence/`
- Create: `skills/threadlight-agentops/tests/test_agentops_check.py`

- [ ] **Step 1: Write failing no-opt-in and discovery tests**

```python
import importlib.util
import json
import shutil
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agentops_check.py"
SPEC = importlib.util.spec_from_file_location("agentops_check", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["agentops_check"] = mod
SPEC.loader.exec_module(mod)


class FakeRunner:
    def __call__(self, argv, *, cwd, timeout=120):
        return mod.CommandResult(
            tuple(argv),
            0,
            json.dumps({"version": 1, "readiness": "ready"}),
            "",
        )


def write_manifest_services(root: Path, services: list[dict]) -> None:
    path = root / "specs" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"deployment_manifest": {"services": services}}),
        encoding="utf-8",
    )


def write_agentops_workspace(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "agentops.yaml").write_text(
        "version: 1\nagent: support-agent:1\ndataset: ./qa.jsonl\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text(
        "agentops-accelerator==0.14.0\n",
        encoding="utf-8",
    )


def copy_fixture(name: str, destination: Path) -> Path:
    fixture = Path(__file__).resolve().parents[1] / "references" / "fixtures" / name
    shutil.copytree(fixture, destination, dirs_exist_ok=True)
    return destination


def test_no_agentops_yaml_is_not_applicable(tmp_path):
    result = mod.assess(tmp_path, runner=FakeRunner())
    assert result["verdict"] == "not-applicable"
    assert result["agents"] == []


def test_opt_in_is_per_agent(tmp_path):
    write_manifest_services(tmp_path, [
        {"name": "a", "host": "azure.ai.agent", "src": "src/a"},
        {"name": "b", "host": "azure.ai.agent", "src": "src/b"},
    ])
    write_agentops_workspace(tmp_path / "src/a")
    (tmp_path / "src/b").mkdir(parents=True)
    roots = mod.discover_agent_roots(tmp_path)
    assert [r.agent_key for r in roots] == ["a"]
```

- [ ] **Step 2: Write failing schema/privacy tests**

```python
def test_manifest_contains_only_relative_artifact_paths(tmp_path):
    copy_fixture("mono-agent", tmp_path)
    result = mod.assess(tmp_path, runner=FakeRunner())
    for agent in result["agents"]:
        for artifact in agent["artifacts"]:
            assert not Path(artifact["path"]).is_absolute()
            assert ".." not in Path(artifact["path"]).parts


def test_manifest_never_copies_sensitive_rows(tmp_path):
    copy_fixture("mono-agent", tmp_path)
    result = mod.assess(tmp_path, runner=FakeRunner())
    encoded = json.dumps(result)
    assert "secret prompt fixture" not in encoded
    assert "secret response fixture" not in encoded
    assert '"tool_calls"' not in encoded
```

- [ ] **Step 3: Add the initial schema**

Require:

```json
{
  "required": [
    "schema",
    "tool_version",
    "captured_at",
    "repository",
    "verdict",
    "agents",
    "summary"
  ]
}
```

Pin:

```text
schema = threadlight-agentops-manifest/v1
verdict = operational | partial | blocked | not-applicable
capability status = pass | should-fix | must-fix | not-verified | not-applicable
```

- [ ] **Step 4: Add minimal skill frontmatter**

```yaml
---
name: threadlight-agentops
description: >-
  Optional per-agent AgentOps lifecycle step. Detects agent roots that opt in
  with agentops.yaml, validates AgentOps version, target/environment binding,
  artifact integrity and freshness, Doctor and release evidence, and workflow
  composition, then emits specs/agentops-manifest.json for Threadlight. USE FOR:
  AgentOps lifecycle evidence, per-agent operations, AOPS-001, Doctor evidence,
  release evidence. DO NOT USE FOR: installing or deeply configuring AgentOps
  (use foundry-agentops), Citadel, deployment completeness, or final readiness.
metadata:
  version: "0.1.0"
---
```

- [ ] **Step 5: Run tests to verify RED**

```bash
python -m pytest skills/threadlight-agentops/tests/test_agentops_check.py -q
```

Expected: import failure for missing `scripts/agentops_check.py`.

- [ ] **Step 6: Commit contract and RED tests**

```bash
git add skills/threadlight-agentops
git commit -m "test: define Threadlight AgentOps contract"
```

### Task 2: Implement per-agent discovery and version/config validation

**Files:**
- Create: `skills/threadlight-agentops/scripts/agentops_check.py`
- Modify: `skills/threadlight-agentops/tests/test_agentops_check.py`

- [ ] **Step 1: Add constants and typed records**

```python
MANIFEST_SCHEMA = "threadlight-agentops-manifest/v1"
TOOL_VERSION = "0.1.0"
SUPPORTED_AGENTOPS_VERSIONS = frozenset({"0.14.0"})
CAPABILITY_ORDER = (
    "configuration_valid",
    "version_pinned",
    "binding_valid",
    "artifact_integrity_valid",
    "doctor_recent",
    "release_evidence_consistent",
    "workflow_composed",
)


@dataclass(frozen=True)
class AgentRoot:
    agent_key: str
    root: Path
    service: str | None
```

- [ ] **Step 2: Implement discovery from the Threadlight JSON contract**

```python
def discover_agent_roots(repo: Path) -> list[AgentRoot]:
    services = _manifest_agent_services(repo / "specs" / "manifest.json")
    found: dict[Path, AgentRoot] = {}
    for service in services:
        root = (repo / service["src"]).resolve()
        if _inside(repo, root) and (root / "agentops.yaml").is_file():
            found[root] = AgentRoot(service["name"], root, service["name"])
    if (repo / "agentops.yaml").is_file():
        found[repo.resolve()] = AgentRoot("agent", repo.resolve(), None)
    for config in repo.rglob("agentops.yaml"):
        if _excluded(config, repo):
            continue
        root = config.parent.resolve()
        found.setdefault(root, AgentRoot(root.name, root, None))
    return sorted(found.values(), key=lambda item: item.agent_key)
```

Exclude `.git`, `.agentops`, `.azure`, `.venv`, `node_modules`, generated output, and symlink escapes.

- [ ] **Step 3: Implement safe CLI runner**

```python
def run_json(argv: list[str], *, cwd: Path, timeout: int = 120) -> CommandResult:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if len(proc.stdout) > 1_000_000 or len(proc.stderr) > 200_000:
        raise EvidenceError("AgentOps command output exceeded safety limit")
    return CommandResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)
```

- [ ] **Step 4: Validate config without parsing YAML**

Invoke:

```python
["agentops", "eval", "analyze", "--dir", str(agent.root), "--format", "json"]
```

Require JSON object `version == 1`. Exit 1/config error maps to `must-fix`; unavailable executable or unsupported output version maps to `not-verified`.

- [ ] **Step 5: Detect the exact package pin**

Recognize only exact pins such as:

```text
agentops-accelerator==0.14.0
"agentops-accelerator==0.14.0"
uv pip install --system "agentops-accelerator==0.14.0"
```

Search dependency manifests and Threadlight-owned workflow install steps. A range (`~=`, `>=`, unversioned) is `must-fix`.

- [ ] **Step 6: Run targeted tests**

```bash
python -m pytest skills/threadlight-agentops/tests/test_agentops_check.py \
  -k "opt_in or discover or version or configuration" -q
```

Expected: pass.

- [ ] **Step 7: Commit discovery**

```bash
git add skills/threadlight-agentops
git commit -m "feat: discover opted-in AgentOps agents"
```

### Task 3: Implement artifact trust, freshness, and Doctor refresh

**Files:**
- Modify: `skills/threadlight-agentops/scripts/agentops_check.py`
- Modify: `skills/threadlight-agentops/tests/test_agentops_check.py`
- Add fixtures under: `skills/threadlight-agentops/references/fixtures/`

- [ ] **Step 1: Add failing artifact tests**

Cover exact outer contracts:

```python
def test_unknown_results_version_is_not_verified(tmp_path):
    copy_fixture("mono-agent", tmp_path)
    path = tmp_path / ".agentops/results/latest/results.json"
    data = json.loads(path.read_text())
    data["version"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    result = mod.assess(tmp_path, runner=FakeRunner())
    assert result["agents"][0]["capabilities"]["artifact_integrity_valid"]["status"] == "not-verified"


def test_latest_results_must_match_timestamped_result(tmp_path):
    copy_fixture("mono-agent", tmp_path)
    latest = tmp_path / ".agentops/results/latest/results.json"
    data = json.loads(latest.read_text())
    data["summary"]["overall_passed"] = False
    latest.write_text(json.dumps(data), encoding="utf-8")
    result = mod.assess(tmp_path, runner=FakeRunner())
    assert result["agents"][0]["capabilities"]["artifact_integrity_valid"]["status"] == "must-fix"


def test_release_latest_without_same_run_receipt_is_not_verified(tmp_path):
    copy_fixture("mono-agent", tmp_path)
    result = mod.assess(tmp_path, runner=FakeRunner())
    assert result["agents"][0]["capabilities"]["release_evidence_consistent"]["status"] == "not-verified"


def test_clock_skew_is_not_verified(tmp_path):
    copy_fixture("mono-agent", tmp_path)
    path = tmp_path / ".agentops/results/latest/results.json"
    data = json.loads(path.read_text())
    data["finished_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = mod.assess(tmp_path, runner=FakeRunner())
    assert result["agents"][0]["capabilities"]["artifact_integrity_valid"]["status"] == "not-verified"
```

Add separate assertions in the same file for a tampered recorded hash
(`must-fix`) and for a repository commit/dirty-state mismatch (`must-fix`).

- [ ] **Step 2: Validate `results.json` version 1**

Require:

```python
RESULT_KEYS = {
    "version", "started_at", "finished_at", "duration_seconds", "target",
    "dataset_path", "evaluators", "rows", "aggregate_metrics", "thresholds",
    "summary", "comparison", "config",
}
```

Read summary/metrics only. Never copy `rows`.

- [ ] **Step 3: Validate release evidence version 1**

Require status in:

```python
{"ready", "ready_with_warnings", "blocked"}
```

Require each check status in:

```python
{"ready", "warning", "blocked", "unknown"}
```

- [ ] **Step 4: Anchor mutable aliases**

For `results/latest/results.json`, require SHA-256 equality with one timestamped result.

For `release/latest/evidence.json`, require a same-process receipt generated by `--refresh-doctor`. Record only:

```json
{
  "argv": ["agentops", "doctor", "--workspace", ".", "--evidence-pack"],
  "started_at": "2026-09-04T12:00:00+00:00",
  "finished_at": "2026-09-04T12:00:42+00:00",
  "exit_code": 0,
  "artifact_sha256": "d6fbe6aeb8d339f56016d2f3f89d5124529934b696239b401ee1c6572aa8d5af",
  "source_commit": "0123456789abcdef0123456789abcdef01234567"
}
```

- [ ] **Step 5: Implement `--refresh-doctor`**

The command runs:

```python
["agentops", "doctor", "--workspace", str(agent.root), "--evidence-pack"]
```

Do not pass `--no-preflight`. The refresh is opt-in; default assessment remains read-only.

- [ ] **Step 6: Apply capability status rules**

```text
invalid opt-in config, unpinned/mismatched version, wrong binding,
integrity failure, unmapped release blocker -> must-fix

stale Doctor, ready_with_warnings, missing scheduled workflow -> should-fix

missing executable/permissions, future schema, unprovable provenance -> not-verified
```

- [ ] **Step 7: Run targeted tests**

```bash
python -m pytest skills/threadlight-agentops/tests/test_agentops_check.py \
  -k "artifact or latest or receipt or freshness or dirty or binding" -q
```

Expected: pass.

- [ ] **Step 8: Commit trust validation**

```bash
git add skills/threadlight-agentops
git commit -m "feat: validate AgentOps evidence provenance"
```

### Task 4: Finish manifest emission and CLI behavior

**Files:**
- Modify: `skills/threadlight-agentops/scripts/agentops_check.py`
- Modify: `skills/threadlight-agentops/tests/test_agentops_check.py`
- Modify: `skills/threadlight-agentops/SKILL.md`

- [ ] **Step 1: Implement worst-status rollup**

```python
STATUS_RANK = {
    "not-applicable": 0,
    "pass": 0,
    "should-fix": 1,
    "not-verified": 2,
    "must-fix": 3,
}


def worst_status(statuses: Iterable[str]) -> str:
    return max(statuses, key=STATUS_RANK.__getitem__, default="not-applicable")
```

Map repository verdict:

```text
no opted-in agents -> not-applicable
worst pass -> operational
worst should-fix/not-verified -> partial
worst must-fix -> blocked
```

- [ ] **Step 2: Add CLI**

```text
--target PATH
--emit
--json
--gate
--refresh-doctor
--freshness-hours N
--agentops-bin PATH
```

`--emit` writes only `specs/agentops-manifest.json`. `--gate` exits 2 only for opted-in `must-fix`; no opt-in exits 0.

- [ ] **Step 3: Validate emitted manifest against the schema in tests**

Assert every capability exists, all paths are relative, hashes are lowercase SHA-256, and summary counts equal agent rollups.

- [ ] **Step 4: Complete SKILL.md**

Document:

```text
foundry-agentops owns adoption/remediation
threadlight-agentops owns evidence validation
Citadel is unchanged
no agentops.yaml means not-applicable
assessment is read-only unless --refresh-doctor
```

- [ ] **Step 5: Run the skill suite**

```bash
python -m pytest skills/threadlight-agentops/tests -q
```

Expected: pass.

- [ ] **Step 6: Commit the lifecycle step**

```bash
git add skills/threadlight-agentops
git commit -m "feat: emit per-agent AgentOps lifecycle evidence"
```

### Task 5: Adapt `threadlight-evals` without duplicate execution

**Files:**
- Modify: `skills/threadlight-evals/scripts/evals_check.py`
- Modify: `skills/threadlight-evals/tests/test_evals_check.py`
- Modify: `skills/threadlight-evals/SKILL.md`

- [ ] **Step 1: Write failing AgentOps eval tests**

```python
def write_agentops_results(root, *, passed=True, pass_rate=0.9):
    write_agentops_workspace(root)
    payload = {
        "version": 1,
        "started_at": "2026-09-04T11:59:00+00:00",
        "finished_at": "2026-09-04T12:00:00+00:00",
        "duration_seconds": 60.0,
        "target": {"kind": "prompt-agent", "raw": "support-agent:1"},
        "dataset_path": "qa.jsonl",
        "evaluators": ["task_adherence"],
        "rows": [{"row_index": 0, "input": "private", "response": "private"}],
        "aggregate_metrics": {"task_adherence": pass_rate},
        "thresholds": [{
            "metric": "task_adherence",
            "criteria": ">=",
            "expected": "0.8",
            "actual": str(pass_rate),
            "passed": passed,
        }],
        "summary": {
            "items_total": 1,
            "items_passed_all": int(passed),
            "items_pass_rate": pass_rate,
            "thresholds_total": 1,
            "thresholds_passed": int(passed),
            "threshold_pass_rate": float(passed),
            "overall_passed": passed,
        },
        "comparison": None,
        "config": {},
    }
    path = root / ".agentops/results/latest/results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_agentops_results_supply_run_threshold_and_pass_rate(tmp_path):
    write_agentops_results(tmp_path)
    caps = ec.evaluate(str(tmp_path), freshness_days=3650)
    assert caps["run_history_present"]["status"] == "pass"
    assert caps["thresholds_declared"]["status"] == "pass"
    assert caps["latest_pass_rate_ok"]["status"] == "pass"


def test_agentops_adapter_does_not_mutate_results(tmp_path):
    path = write_agentops_results(tmp_path)
    before = path.read_bytes()
    ec.evaluate(str(tmp_path), freshness_days=3650)
    assert path.read_bytes() == before


def test_conflicting_valid_results_keep_worse_status(tmp_path):
    write_agentops_results(tmp_path, passed=False, pass_rate=0.4)
    caps = ec.evaluate(str(tmp_path), freshness_days=3650)
    assert caps["latest_pass_rate_ok"]["status"] == "should-fix"


def test_sensitive_agentops_rows_do_not_enter_evals_manifest(tmp_path):
    write_agentops_results(tmp_path)
    manifest = ec.manifest(str(tmp_path), ec.evaluate(str(tmp_path), 3650), 3650)
    encoded = json.dumps(manifest)
    assert '"input": "private"' not in encoded
    assert '"response": "private"' not in encoded
```

- [ ] **Step 2: Add minimal version-1 result loader**

Load only opted-in `.agentops/results/latest/results.json` files and require:

```text
version == 1
parseable started_at/finished_at
target object
thresholds list
summary.overall_passed boolean
summary.items_pass_rate number
```

- [ ] **Step 3: Map AgentOps evidence**

Map:

```text
dataset_path -> eval_datasets_present
evaluators -> eval_scenarios_present support
thresholds -> thresholds_declared
finished_at -> latest_eval_run_fresh
summary.items_pass_rate -> metrics.pass_rate
summary.overall_passed -> latest_pass_rate_ok
comparison -> ab_comparison_present
```

Do not claim schedule, online wiring, or alert coverage from `results.json`.

- [ ] **Step 4: Merge with existing evidence**

Use worst-status precedence only when both sources contain valid evidence for the same capability. AgentOps fills a previously absent run-history signal; it does not erase a valid Threadlight failure.

- [ ] **Step 5: Run eval tests**

```bash
python -m pytest skills/threadlight-evals/tests/test_evals_check.py -q
```

Expected: pass and no subprocess call to `agentops eval run`.

- [ ] **Step 6: Commit eval adapter**

```bash
git add skills/threadlight-evals
git commit -m "feat: consume AgentOps eval evidence"
```

### Task 6: Adapt red-team and governance evidence

**Files:**
- Modify: `skills/threadlight-redteam/scripts/redteam_check.py`
- Modify: `skills/threadlight-redteam/tests/test_redteam_check.py`
- Modify: `skills/threadlight-redteam/SKILL.md`
- Modify: `skills/threadlight-govern/scripts/govern_check.py`
- Modify: `skills/threadlight-govern/tests/test_govern_check.py`
- Modify: `skills/threadlight-govern/SKILL.md`

- [ ] **Step 1: Write failing normalized red-team tests**

Use the AgentOps `v0.14.0` shape:

```json
{
  "target": {"agent": "name:1"},
  "risk_categories": ["violence", "hate_unfairness"],
  "attack_strategies": ["base64"],
  "num_objectives": 10,
  "total_attempts": 20,
  "successful_attacks": 1,
  "attack_success_rate": 0.05,
  "per_category": {},
  "per_strategy": {},
  "generated_at": "2026-09-04T12:00:00+00:00",
  "target_fingerprint": "sha256:d6fbe6aeb8d339f56016d2f3f89d5124529934b696239b401ee1c6572aa8d5af"
}
```

Add this complete assertion after writing the object above to
`.agentops/redteam/latest.json`:

```python
result = rt.evaluate(str(tmp_path), override=None, freshness_days=30, max_asr=0.10)
assert result["scan_path"].endswith(".agentops/redteam/latest.json")
assert result["asr"]["harmful_content"] == 0.05
assert result["must_fix"] == []
```

- [ ] **Step 2: Implement red-team source normalization**

Search order:

```text
explicit --scan-result
native Threadlight scan paths
opted-in agent .agentops/redteam/latest.json
```

Translate AgentOps aggregate/category fields into the existing Threadlight scan-result model. Missing Threadlight core categories becomes `not-verified` or thin coverage; it never silently passes.

- [ ] **Step 3: Write failing governance-precedence tests**

```python
def test_agentops_ready_cannot_flip_missing_agt_policy_to_pass(tmp_path):
    write_agentops_governance(tmp_path, status="ready")
    caps = gc.evaluate(str(tmp_path), freshness_days=90)
    assert caps["policy_artefact_present"]["status"] == "must-fix"
    assert caps["policy_schema_valid"]["status"] == "must-fix"


def test_assert_acs_is_supplemental_when_policy_exists(tmp_path):
    write_valid_agt_policy(tmp_path)
    write_agentops_governance(tmp_path, status="ready")
    caps = gc.evaluate(str(tmp_path), freshness_days=90)
    assert caps["policy_artefact_present"]["status"] == "pass"
    assert "AgentOps" in caps["attestation_present"]["evidence"]


def test_conflicting_governance_sources_keep_worse_status(tmp_path):
    write_invalid_agt_policy(tmp_path)
    write_agentops_governance(tmp_path, status="ready")
    caps = gc.evaluate(str(tmp_path), freshness_days=90)
    assert caps["policy_schema_valid"]["status"] == "must-fix"
```

Define `write_agentops_governance`, `write_valid_agt_policy`, and
`write_invalid_agt_policy` in the test file as small fixture writers before
these tests.

- [ ] **Step 4: Implement supplemental governance references**

AgentOps ASSERT/ACS may add evidence text to attestation/test capabilities only when the canonical AGT artifact is present. It cannot satisfy:

```text
policy_artefact_present
policy_schema_valid
policy_default_deny
sensitive_action_rules_present
runtime enforcement
```

- [ ] **Step 5: Run both suites**

```bash
python -m pytest \
  skills/threadlight-redteam/tests/test_redteam_check.py \
  skills/threadlight-govern/tests/test_govern_check.py -q
```

Expected: pass.

- [ ] **Step 6: Commit domain adapters**

```bash
git add skills/threadlight-redteam skills/threadlight-govern
git commit -m "feat: normalize AgentOps safety evidence"
```

### Task 7: Compose AgentOps jobs in `threadlight-cicd`

**Files:**
- Modify: `skills/threadlight-cicd/scripts/generate_pipeline.py`
- Modify: `skills/threadlight-cicd/references/github-actions/azd-deploy-prod.yml.tmpl`
- Modify: `skills/threadlight-cicd/references/azure-devops/azure-pipelines.yml.tmpl`
- Create: `skills/threadlight-cicd/tests/test_agentops_gate.py`
- Modify: `skills/threadlight-cicd/SKILL.md`

- [ ] **Step 1: Write failing workflow-ownership tests**

```python
def test_github_composes_agentops_matrix_for_opted_in_agents():
    workflow = _gh_workflow(_gh_framing(agentops="auto"), opted_in=["src/a", "src/b"])
    assert "agentops-eval" in workflow
    assert "threadlight-agentops" in workflow
    assert "src/a" in workflow and "src/b" in workflow


def test_no_opt_in_emits_no_agentops_jobs():
    workflow = _gh_workflow(_gh_framing(agentops="auto"), opted_in=[])
    assert "agentops-eval" not in workflow
    assert "threadlight-agentops" not in workflow


def test_threadlight_mode_never_runs_agentops_workflow_generate():
    for rendered in (
        _gh_workflow(_gh_framing(agentops="auto"), opted_in=["src/a"]),
        _ado_pipeline(_ado_framing(agentops="auto"), opted_in=["src/a"]),
    ):
        assert "agentops workflow generate" not in rendered


def test_agentops_jobs_remain_secret_free_and_spoke_scoped():
    workflow = _gh_workflow(_gh_framing(agentops="auto"), opted_in=["src/a"])
    assert "AZURE_CREDENTIALS" not in workflow
    assert "client-secret" not in workflow
    assert "permissions:" in workflow and "id-token: write" in workflow
    assert "rg-pilot-prod" in workflow
    assert "hub" not in workflow.lower()
```

Update `_gh_workflow` and `_ado_pipeline` test helpers to materialize the
listed `agentops.yaml` files under their temporary output roots before calling
`generate`.

- [ ] **Step 2: Add generator input**

Add:

```text
--agentops auto|off
```

`auto` discovers repository-relative agent roots containing `agentops.yaml`; `off` suppresses composition. Do not add a separate registry file.

- [ ] **Step 3: Add GitHub job sequence**

For each opted-in agent:

```text
PR: agentops eval run with baseline, then threadlight-evals --emit
post-deploy: threadlight-agentops --refresh-doctor --emit
schedule: threadlight-agentops --refresh-doctor --emit
```

Upload native AgentOps artifacts and normalized Threadlight manifests without committing sensitive rows.

- [ ] **Step 4: Add Azure DevOps equivalent**

Use WIF service connection and the existing pool/environment context. Do not introduce secrets or widen RBAC.

- [ ] **Step 5: Preserve existing gates**

Existing eval/red-team gates remain. They consume normalized artifacts and do not rerun equivalent AgentOps work.

- [ ] **Step 6: Run CI/CD tests**

```bash
python -m pytest \
  skills/threadlight-cicd/tests/test_agentops_gate.py \
  skills/threadlight-cicd/tests/test_eval_gate.py \
  skills/threadlight-cicd/tests/test_central_boundary.py \
  skills/threadlight-cicd/tests/test_no_secrets_in_templates.py -q
```

Expected: pass.

- [ ] **Step 7: Commit workflow composition**

```bash
git add skills/threadlight-cicd
git commit -m "feat: compose AgentOps lifecycle jobs"
```

### Task 8: Add `AOPS-001` to production-ready

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`
- Create: `skills/threadlight-production-ready/tests/test_agentops_manifest.py`
- Create: `skills/threadlight-production-ready/references/remediation-recipes/AOPS-001.md`
- Modify: `skills/threadlight-production-ready/references/pillars/12-sre-handover.md`
- Modify: `skills/threadlight-production-ready/SKILL.md`

- [ ] **Step 1: Write failing aggregate tests**

```python
def test_no_opt_in_is_not_applicable_and_non_scoring():
    ctx = _make_ctx()
    finding = pr._check_agentops_static(ctx)
    assert finding.id == "AOPS-001"
    assert finding.status == "not-applicable"


def test_multi_agent_rollup_uses_worst_operational_status():
    manifest = agentops_manifest(
        agents=[
            agent_entry("a", "pass"),
            agent_entry("b", "must-fix", blockers=["doctor.runtime_unreachable"]),
        ]
    )
    ctx = _make_ctx(manifests={"agentops-manifest.json": manifest})
    assert pr._check_agentops_static(ctx).status == "must-fix"


def test_mapped_blockers_are_not_duplicated():
    manifest = agentops_manifest(
        agents=[agent_entry("a", "must-fix", blockers=["eval.threshold_failed"])]
    )
    ctx = _make_ctx(manifests={"agentops-manifest.json": manifest})
    finding = pr._check_agentops_static(ctx)
    assert finding.status == "pass"
    assert "delegated to EVAL findings" in finding.detail


def test_future_manifest_is_not_verified():
    manifest = agentops_manifest(agents=[agent_entry("a", "pass")])
    manifest["schema"] = "threadlight-agentops-manifest/v2"
    ctx = _make_ctx(manifests={"agentops-manifest.json": manifest})
    assert pr._check_agentops_static(ctx).status == "not-verified"
```

Define `agentops_manifest` and `agent_entry` in the test module with fixed
RFC3339 timestamps and complete capability maps.

- [ ] **Step 2: Add catalog entry**

```python
"AOPS-001": {
    "title": "Opted-in agents have current AgentOps operational evidence",
    "pillar": "sre-handover",
    "severity": "should-fix",
    "tier": 0,
},
```

- [ ] **Step 3: Implement independent consumer validation**

Validate schema, exact allowed top-level keys, relative paths, hashes, summary counts, capability statuses, and freshness. Do not import the producer.

- [ ] **Step 4: Add aggregate to `_check_sre_static`**

Behavior:

```text
no manifest + no agentops.yaml -> not-applicable
no manifest + opt-in -> not-verified
manifest valid -> worst unmapped operational status
mapped domain blocker -> reference only, no duplicate severity
```

- [ ] **Step 5: Add sibling-skill remediation**

Set recipe frontmatter:

```yaml
---
kind: sibling-skill
skill: foundry-agentops
---
```

Name the released awesome-gbb version/commit from the prerequisite plan. Include stale-plan check and instruct re-running `threadlight-agentops`.

- [ ] **Step 6: Run production-ready tests**

```bash
python -m pytest \
  skills/threadlight-production-ready/tests/test_agentops_manifest.py \
  skills/threadlight-production-ready/tests/test_recipe_catalog.py \
  skills/threadlight-production-ready/tests/test_leg_manifests.py -q
```

Expected: pass with existing finding IDs unchanged except additive `AOPS-001`.

- [ ] **Step 7: Commit readiness integration**

```bash
git add skills/threadlight-production-ready
git commit -m "feat: aggregate AgentOps operations evidence"
```

### Task 9: Add the optional lifecycle stage to auto and canvas

**Files:**
- Modify: `skills/threadlight-auto/references/orchestrator.py`
- Modify: `skills/threadlight-auto/SKILL.md`
- Modify: `skills/threadlight-auto/references/state-schema.md`
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
- Modify: `.github/extensions/threadlight-lifecycle/lib/lifecycle-registry.mjs`
- Modify: `tests/canvas/lifecycle-registry.test.mjs`

- [ ] **Step 1: Write failing auto-stage tests**

```python
def test_agentops_stage_skips_when_no_agentops_yaml(tmp_path):
    decision = auto._check_agentops(tmp_path, {})
    assert decision.decision == "skip"
    assert "not opted in" in decision.reason


def test_agentops_stage_runs_when_opted_in_manifest_missing(tmp_path):
    write_agentops_workspace(tmp_path / "src/agent")
    decision = auto._check_agentops(tmp_path, {})
    assert decision.decision == "run"
    assert decision.artifacts_missing == ["specs/agentops-manifest.json"]


def test_agentops_stage_skips_fresh_valid_nonpassing_manifest(tmp_path):
    write_agentops_workspace(tmp_path / "src/agent")
    write_agentops_manifest(tmp_path, verdict="partial")
    decision = auto._check_agentops(tmp_path, {})
    assert decision.decision == "skip"
    assert "verdict=partial" in decision.reason


def test_agentops_stage_reruns_malformed_manifest(tmp_path):
    write_agentops_workspace(tmp_path / "src/agent")
    path = tmp_path / "specs/agentops-manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert auto._check_agentops(tmp_path, {}).decision == "run"
```

Define `write_agentops_workspace` and `write_agentops_manifest` beside the
existing orchestrator test helpers.

- [ ] **Step 2: Add the auto stage after govern**

```python
STAGES = [
    "preflight", "design", "deploy", "safe_check", "cost_projection",
    "invoke", "evals", "redteam", "govern", "agentops",
]
```

The custom `_check_agentops` first detects opt-in. No opt-in returns `skip` with a not-applicable reason and does not cascade.

- [ ] **Step 3: Register the canvas skill**

Add under `improve`:

```javascript
skill(
  "threadlight-agentops",
  "improve",
  "Verify per-agent operations",
  [artifactGroup("specs/agentops-manifest.json")],
  {
    applicability: "agentops-opt-in",
    freshnessHours: 24,
    prerequisiteSkills: ["threadlight-safe-check"],
  },
)
```

- [ ] **Step 4: Update exact registry counts and prerequisites**

Change registry expectations from 22 to 23 lifecycle skills and include `threadlight-agentops`.

- [ ] **Step 5: Run auto/canvas tests**

```bash
python -m pytest skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py -q
node --test tests/canvas/lifecycle-registry.test.mjs tests/canvas/projector.test.mjs
```

Expected: pass.

- [ ] **Step 6: Commit lifecycle registration**

```bash
git add skills/threadlight-auto .github/extensions/threadlight-lifecycle tests/canvas
git commit -m "feat: add AgentOps lifecycle stage"
```

### Task 10: Publish catalog and documentation surfaces

**Files:**
- Modify: `plugin.json`
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/production-readiness.md`

- [ ] **Step 1: Update plugin metadata**

Change plugin version from `1.13.0` to `1.14.0` and update counts:

```text
23 pipeline skills + threadlight-auto planner = 24 total
```

Add keywords:

```text
agentops, agent-operations, doctor, release-evidence,
regression-baseline, per-agent-ops
```

- [ ] **Step 2: Add catalog and chain documentation**

Document:

```text
foundry-agentops -> installs/configures/remediates
threadlight-agentops -> validates/normalizes evidence
threadlight-production-ready -> aggregates AOPS-001
Citadel -> unchanged platform control plane
```

- [ ] **Step 3: Document opt-in behavior**

State prominently that `agentops.yaml` is per-agent opt-in and absence is not a gap.

- [ ] **Step 4: Document overlap rules**

Include the owner matrix for evals, red-team, govern, CI/CD, safe-check, and Citadel.

- [ ] **Step 5: Commit docs**

```bash
git add plugin.json README.md THREADLIGHT.md CHANGELOG.md docs/production-readiness.md
git commit -m "docs: publish Threadlight AgentOps lifecycle"
```

### Task 11: Run full validation and field fixtures

**Files:**
- Verify only

- [ ] **Step 1: Run all targeted Python tests in one invocation**

```bash
python -m pytest \
  skills/threadlight-agentops/tests \
  skills/threadlight-evals/tests/test_evals_check.py \
  skills/threadlight-redteam/tests/test_redteam_check.py \
  skills/threadlight-govern/tests/test_govern_check.py \
  skills/threadlight-cicd/tests/test_agentops_gate.py \
  skills/threadlight-production-ready/tests/test_agentops_manifest.py \
  skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py -q
```

Expected: pass.

- [ ] **Step 2: Run repository-wide standalone and Node gates**

```bash
python scripts/ci/run-standalone-tests.py
node --test tests/blueprint/*.test.js tests/canvas/*.test.mjs tests/ci/*.test.js
```

Expected: pass.

- [ ] **Step 3: Verify privacy invariants**

```bash
rg -n '"(input|response|context|tool_calls)"|/Users/|/home/|AZURE_CREDENTIALS|clientSecret' \
  skills/threadlight-agentops/references/fixtures \
  skills/threadlight-cicd/references
```

Expected: no unexpected sensitive payloads, absolute paths, or long-lived credentials.

- [ ] **Step 4: Run an offline fixture matrix**

Execute:

```text
no opt-in
mono-agent pass
multi-agent mixed opt-in
stale Doctor
future result version
latest/timestamp mismatch
domain conflict
unmapped release blocker
```

Expected: statuses match the design and no duplicate domain finding is emitted.

- [ ] **Step 5: Run opt-in live validation**

Against one non-production Foundry agent:

```bash
python skills/threadlight-agentops/scripts/agentops_check.py \
  --target skills/threadlight-agentops/references/fixtures/mono-agent \
  --refresh-doctor \
  --emit
```

Expected:

```text
specs/agentops-manifest.json
```

with one opted-in agent, supported version `0.14.0`, same-run Doctor receipt, relative artifact paths, and no copied prompt/response rows.

- [ ] **Step 6: Verify Citadel and deployment invariants**

Run existing `threadlight-cicd` central-boundary tests and `threadlight-safe-check` tests unchanged. Expected: no new hub write, RBAC scope, APIM, access-contract, image, channel, or deployment behavior.

- [ ] **Step 7: Commit any generated deterministic fixtures**

```bash
git add skills/threadlight-agentops/references/fixtures
git commit -m "test: add AgentOps integration fixtures"
```
