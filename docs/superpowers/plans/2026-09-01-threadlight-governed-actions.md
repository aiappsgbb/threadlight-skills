# Threadlight Governed Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `threadlight-governed-actions` assessor that emits deterministic governance evidence proving consequential runtime actions and GitHub Copilot-produced changes are governed by enforceable controls.

**Architecture:** A read-only Python assessor normalizes project evidence into an action inventory, a runtime mediation graph, deterministic application-path probe results, and a separately assessed GitHub Copilot change plane. Focused modules render a versioned manifest, customer-facing evidence pack, and remediation apply plan; the only mutation beyond explicitly emitted reports is a fixed, double-opt-in, no-overwrite scaffold. `threadlight-production-ready` consumes aggregate claims from the manifest, while `threadlight-auto` only recommends explicit lifecycle handoffs and never applies enforcement or production rollout.

**Tech Stack:** Python 3.13 standard library, PyYAML already used by the repository, JSON Schema Draft 2020-12, pytest, Node.js built-in test runner, GitHub Actions, optional `gh` and Azure CLI evidence collectors

---

## File structure and single responsibilities

### New standalone skill

| Path | Responsibility |
| --- | --- |
| `skills/threadlight-governed-actions/SKILL.md` | User-facing contract, lifecycle commands, trust boundaries, read-only/scaffold rules, and output meanings. |
| `skills/threadlight-governed-actions/scripts/__init__.py` | Package marker only; no behavior. |
| `skills/threadlight-governed-actions/scripts/contracts.py` | Typed statuses, lifecycle/options, normalized actions/paths/evidence/findings, and result containers. |
| `skills/threadlight-governed-actions/scripts/canonical.py` | RFC 8785-compatible constrained canonical JSON, SHA-256 source/policy/evidence binding, payload-free audit validation, and atomic output writes. |
| `skills/threadlight-governed-actions/scripts/inputs.py` | Required/optional input resolution, repository-relative evidence allowlisting, and missing-capability attribution. |
| `skills/threadlight-governed-actions/scripts/inventory.py` | Deterministic discovery and classification of actions from SPEC section 8, registries, MAF tools, and declared provider-hosted tools. |
| `skills/threadlight-governed-actions/scripts/maf_adapter.py` | MAF-first adapter implementing a framework-neutral evidence contract and verifying the complete pinned Agent Hooks/SDK/MAF/CTK tuple. |
| `skills/threadlight-governed-actions/scripts/mediation.py` | Runtime execution-path graph construction and pre-action mediation coverage analysis. |
| `skills/threadlight-governed-actions/scripts/probes.py` | Hermetic application-path probes for deny, transform, fail-closed, approval binding/replay, output mediation, and payload-free audit behavior. |
| `skills/threadlight-governed-actions/scripts/ghcp.py` | Static and optional live evidence assessment for the GitHub Copilot code-change/deployment supply chain. |
| `skills/threadlight-governed-actions/scripts/alerts.py` | Static and optional live assessment of required governance alert definitions and delivery posture. |
| `skills/threadlight-governed-actions/scripts/render.py` | Deterministic manifest, Markdown evidence pack, pass/fail matrix, residual-risk register, and remediation-plan rendering. |
| `skills/threadlight-governed-actions/scripts/scaffold.py` | Exact five-file, double-opt-in, no-overwrite scaffold renderer with no customer policy inference. |
| `skills/threadlight-governed-actions/scripts/governed_actions.py` | CLI parsing, phase orchestration, exit semantics, error reporting, and artifact emission. |

### New reference contracts

| Path | Responsibility |
| --- | --- |
| `skills/threadlight-governed-actions/references/governed-actions-manifest.schema.json` | Versioned JSON Schema for `tests/governed-actions-manifest.json`. |
| `skills/threadlight-governed-actions/references/governed-actions-apply-plan.schema.json` | Versioned JSON Schema for `tests/governed-actions-apply-plan.json`. |
| `skills/threadlight-governed-actions/references/finding-catalog.json` | Canonical finding IDs, severities, planes, gates, and remediation text. |
| `skills/threadlight-governed-actions/references/upstream-pin.json` | Machine-readable tested Agent Hooks, SDK, CTK, MAF, Python, and conformance-report tuple. |
| `skills/threadlight-governed-actions/references/upstream-pin.md` | Human-readable alpha/experimental status, drift rules, and upgrade procedure. |
| `skills/threadlight-governed-actions/references/scaffold/agent_hooks_interceptor.py.tmpl` | Fixed fail-closed interceptor skeleton. |
| `skills/threadlight-governed-actions/references/scaffold/governed-actions.policy.yaml.tmpl` | Empty policy shape with explicit customer-owned fields. |
| `skills/threadlight-governed-actions/references/scaffold/approval-binding-fixture.json.tmpl` | Deterministic approval subject/action/arguments/expiry/nonce binding fixture. |
| `skills/threadlight-governed-actions/references/scaffold/test_governed_actions_contract.py.tmpl` | Local deny/transform/crash/replay contract-test skeleton. |
| `skills/threadlight-governed-actions/references/scaffold/governed-actions.yml.tmpl` | Least-privilege, SHA-pinned governance CI skeleton. |

### New tests and deterministic fixtures

| Path | Responsibility |
| --- | --- |
| `skills/threadlight-governed-actions/tests/conftest.py` | Fixture-root and module-loading helpers. |
| `skills/threadlight-governed-actions/tests/test_contracts.py` | Schema, finding-catalog, canonicalization, hashing, source binding, and atomic-write contracts. |
| `skills/threadlight-governed-actions/tests/test_inputs.py` | Required input failures, optional capability attribution, report/eval/dependency discovery, and evidence-path rejection. |
| `skills/threadlight-governed-actions/tests/test_inventory.py` | Action discovery, explicit classification, ambiguity, and stable ordering. |
| `skills/threadlight-governed-actions/tests/test_maf_adapter.py` | MAF adapter contract, complete version tuple, conformance references, and drift behavior. |
| `skills/threadlight-governed-actions/tests/test_mediation.py` | Interactive/batch/background/subagent/direct/provider path graph coverage. |
| `skills/threadlight-governed-actions/tests/test_probes.py` | Application-path deny/transform/fail-closed/approval/output/audit probes. |
| `skills/threadlight-governed-actions/tests/test_ghcp.py` | Static/live PR, CODEOWNERS, branch rules, CI, SHA, OIDC/WIF, privilege, and deploy identity assessment. |
| `skills/threadlight-governed-actions/tests/test_alerts.py` | Required alert classes, stable reason/correlation fields, live delivery evidence, and payload-free alert checks. |
| `skills/threadlight-governed-actions/tests/test_render_cli.py` | Artifact determinism, Markdown matrix/risk/remediation rendering, read-only operation, phases, and exit codes. |
| `skills/threadlight-governed-actions/tests/test_scaffold.py` | Double opt-in, exact file set, no-overwrite, and no invented policy values. |
| `skills/threadlight-governed-actions/tests/test_golden_fixtures.py` | Eight required scenarios and byte-for-byte golden outputs. |
| `skills/threadlight-governed-actions/tests/fixtures/conformant-maf/` | Governed MAF app with explicit action registry and production dispatch seam. |
| `skills/threadlight-governed-actions/tests/fixtures/unmediated-background/` | Background and batch paths that bypass pre-action mediation. |
| `skills/threadlight-governed-actions/tests/fixtures/provider-hosted-side-effect/` | Side-effecting provider-hosted tool without equivalent server-side enforcement. |
| `skills/threadlight-governed-actions/tests/fixtures/approval-replay/` | Unbound, replayed, expired, and mutated approval records. |
| `skills/threadlight-governed-actions/tests/fixtures/interceptor-failure/` | Crashing, timing-out, and malformed-verdict interceptors. |
| `skills/threadlight-governed-actions/tests/fixtures/output-streaming/` | Buffered and unmediated incremental-output declarations. |
| `skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp/` | Direct-push workflow, absent ownership/rules, floating actions, secrets, and shared deploy identity. |
| `skills/threadlight-governed-actions/tests/fixtures/upstream-version-drift/` | Installed tuple that differs from the pinned conformance tuple. |
| `skills/threadlight-governed-actions/tests/golden/conformant-manifest.json` | Canonical passing manifest. |
| `skills/threadlight-governed-actions/tests/golden/nonconformant-manifest.json` | Canonical must-fix/not-verified manifest. |
| `skills/threadlight-governed-actions/tests/golden/conformant-evidence-pack.md` | Canonical customer-facing evidence pack. |
| `skills/threadlight-governed-actions/tests/golden/nonconformant-apply-plan.json` | Canonical remediation plan. |

Each fixture directory contains only the exact files named in its task below; no fixture includes credentials, access tokens, production identifiers, payload bodies, or customer data.

The exact fixture file set is:

| Scenario | Files |
| --- | --- |
| `conformant-maf` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |
| `unmediated-background` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |
| `provider-hosted-side-effect` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |
| `approval-replay` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |
| `interceptor-failure` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |
| `output-streaming` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |
| `unprotected-ghcp` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/installed-packages.json`, `.github/CODEOWNERS.absent`, `.github/workflows/deploy.yml`, `.github/workflows/tests.yml`, `governance/change-plane.json` |
| `upstream-version-drift` | `specs/SPEC.md`, `agent.yaml`, `app/agent.py`, `governance/probe-contract.json`, `governance/alerts.json`, `governance/change-plane.json`, `governance/installed-packages.json`, `.github/CODEOWNERS`, `.github/workflows/governed-actions.yml` |

The seven runtime-focused scenarios keep their non-target runtime, alert, and change-plane controls conformant so each negative golden isolates only the declared defect. `CODEOWNERS.absent` is inert test data and is never treated as a real ownership file.

### Existing integration surfaces to modify

| Path | Responsibility of change |
| --- | --- |
| `skills/threadlight-production-ready/scripts/production_ready.py` | Load and validate the governed-actions manifest and emit three aggregate findings without rerunning detailed checks. |
| `skills/threadlight-production-ready/tests/test_governed_actions_manifest.py` | Pin governed-actions aggregation, stale/missing/invalid evidence, and must-fix propagation. |
| `skills/threadlight-production-ready/tests/test_version.py` | Pin the production-ready version bump from `0.11.0` to `0.12.0`. |
| `skills/threadlight-production-ready/SKILL.md` | Document manifest consumption under governance, HITL, and supply-chain pillars. |
| `skills/threadlight-production-ready/references/02-agent-governance.md` | Document `AGT-007` aggregate semantics. |
| `skills/threadlight-production-ready/references/08-hitl-governance.md` | Document `HITL-008` aggregate semantics. |
| `skills/threadlight-production-ready/references/09-supply-chain-security.md` | Document `SUP-014` aggregate semantics. |
| `skills/threadlight-auto/references/orchestrator.py` | Add schema-validated recommendation-only design/pre-deploy/post-deploy handoffs. |
| `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py` | Prove no automatic governed-actions rollout and validate handoff recommendations. |
| `skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py` | Pin the auto version bump from `1.2.0` to `1.3.0` where the existing test freezes versions. |
| `skills/threadlight-auto/SKILL.md` | Document explicit governed-actions handoffs and manual enforcement ownership. |
| `.github/workflows/python-pytest.yml` | Register the new pytest suite explicitly. |
| `tests/ci/check-test-dirs-wired.test.js` | Prove the CI registration checker requires the new suite. |
| `tests/blueprint/published-surfaces.test.js` | Pin 23 total skills, 22 pipeline skills plus auto, and plugin version `1.13.0`. |
| `plugin.json` | Publish the new skill and bump plugin version to `1.13.0`. |
| `.github/plugin/marketplace.json` | Mirror plugin version and governed-actions description. |
| `README.md` | Add the skill to the catalog and lifecycle examples. |
| `THREADLIGHT.md` | Add governed-actions to lifecycle guidance without making auto its rollout owner. |
| `CHANGELOG.md` | Record the new skill, integrations, trust boundaries, and version changes. |

## Fixed public contracts used by every task

The implementation must use these values verbatim:

```python
SCHEMA_VERSION = "1.0.0"
ASSESSOR_VERSION = "0.1.0"
SKILL_VERSION = "0.1.0"
SUPPORTED_PHASES = ("design", "pre-deploy", "post-deploy")
CONSEQUENCE_CLASSES = ("read", "write", "external-egress", "irreversible")
EXECUTION_MODES = (
    "interactive",
    "batch",
    "background",
    "subagent",
    "direct-tool",
    "provider-hosted-tool",
)
STATUSES = ("pass", "must-fix", "should-fix", "not-verified", "not-applicable")
VERDICTS = ("governed", "partial", "ungoverned")
```

CLI exit semantics are:

| Exit | Meaning |
| --- | --- |
| `0` | Assessment completed; when `--gate` is set, selected-phase requirements passed. |
| `1` | Assessment completed and a `must-fix` or selected-phase required `not-verified` finding remains; valid requested artifacts are still emitted. |
| `2` | Invalid arguments, invalid schema, missing required local inputs, unsafe post-deploy target, or refused scaffold request. |
| `3` | Assessor/tooling failure or atomic artifact-write failure. |

The complete finding catalog is the approved specification taxonomy:

| ID | Plane | Default negative status | Condition |
| --- | --- | --- | --- |
| `ACT-001` | runtime | must-fix | SPEC/code inventory is incomplete or an action has no explicit consequence. |
| `ACT-002` | runtime | must-fix | Declared and implemented actions, aliases, or provider tools drift. |
| `MED-001` | runtime | must-fix | Consequential path lacks evidenced pre-action mediation. |
| `MED-002` | runtime | must-fix or not-verified | Interactive, batch, background, subagent, or direct-tool coverage is absent or indeterminate. |
| `MED-003` | runtime | must-fix | Provider-hosted side effect is not pre-interceptable and lacks equivalent server-side proof; reason is `unsupported`. |
| `ENF-001` | runtime | must-fix | Deny or transform is not enforced on the application path. |
| `ENF-002` | runtime | must-fix | Crash, timeout, or malformed verdict does not fail closed. |
| `APR-001` | runtime | must-fix | Approval is not actor-, tenant-, policy-, action-, argument-, expiry-, and nonce-bound with atomic one-time redemption. |
| `OUT-001` | runtime | must-fix | Protected output can egress before mediation. |
| `AUD-001` | runtime | must-fix | Audit is incomplete, uncorrelated, undelivered, or contains payload data. |
| `PIN-001` | both | must-fix or not-verified | Tested tuple drifts without rerun or pinned evidence is inaccessible. |
| `GHCP-001` | change | must-fix | Protected branch accepts agent changes outside pull requests. |
| `GHCP-002` | change | must-fix or not-verified | CODEOWNERS, ruleset/branch protection, or required-check coverage is missing or unavailable. |
| `GHCP-003` | change | must-fix | Required CI omits CTK, application probes, or relevant evals. |
| `GHCP-004` | change | must-fix | Action SHA floats or workflow permission is excessive. |
| `GHCP-005` | change | must-fix | Azure deployment uses a long-lived secret instead of OIDC/WIF. |
| `GHCP-006` | change | must-fix or not-verified | Build/test/deploy identities are shared, over-broad, or not evidenced. |
| `OPS-001` | both | should-fix or must-fix | Required alert posture is missing, or mandatory governance events are proven to be silently dropped. |

`not-verified` is never converted to `pass`. `unsupported` is a stable reason on a `must-fix` finding, not a status. A consequential path without pre-action mediation always emits `MED-001` with status `must-fix`.

### Task 1: Establish schemas, typed contracts, canonical hashing, and atomic writes

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/__init__.py`
- Create: `skills/threadlight-governed-actions/scripts/contracts.py`
- Create: `skills/threadlight-governed-actions/scripts/canonical.py`
- Create: `skills/threadlight-governed-actions/references/governed-actions-manifest.schema.json`
- Create: `skills/threadlight-governed-actions/references/governed-actions-apply-plan.schema.json`
- Create: `skills/threadlight-governed-actions/references/finding-catalog.json`
- Create: `skills/threadlight-governed-actions/tests/conftest.py`
- Create: `skills/threadlight-governed-actions/tests/test_contracts.py`

- [ ] **Step 1: Write failing contract tests**

Create `conftest.py` to prepend `scripts/` to `sys.path`. In `test_contracts.py`, assert all of these exact behaviors:

```python
def test_canonical_json_sorts_keys_and_rejects_non_json_numbers():
    assert canonical_bytes({"z": 1, "a": ["x", True]}) == b'{"a":["x",true],"z":1}'
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalizationError, match="finite"):
            canonical_bytes({"value": value})

def test_policy_hash_binds_path_and_content(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"effect":"deny"}\n', encoding="utf-8")
    assert hash_files(tmp_path, [Path("policy.json")]) == {
        "algorithm": "sha256",
        "files": [{
            "path": "policy.json",
            "sha256": "sha256:" + hashlib.sha256(b'{"effect":"deny"}\n').hexdigest(),
        }],
        "set_sha256": "sha256:" + hashlib.sha256(
            canonical_bytes([{
                "path": "policy.json",
                "sha256": (
                    "sha256:"
                    + hashlib.sha256(b'{"effect":"deny"}\n').hexdigest()
                ),
            }])
        ).hexdigest(),
    }

def test_audit_record_rejects_payload_fields():
    validate_payload_free_audit({
        "schema": "threadlight-governed-action-audit/v1",
        "event_id": "evt-1",
        "timestamp": "2026-09-01T12:00:00Z",
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "deployment_id": "deployment:synthetic-001",
        "action_id": "payments.refund",
        "path_id": "path-001",
        "policy_id": "policy:refund-v1",
        "rule_id": "rule:deny-synthetic",
        "policy_hash": "sha256:" + "a" * 64,
        "action_hash": "sha256:" + "b" * 64,
        "decision": "deny",
        "reason_code": "policy_deny",
        "approval_redemption_hash": None,
        "correlation_id": "trace-synthetic-001",
        "interceptor_duration_ms": 4,
        "error_class": None,
        "delivery_status": "delivered",
    })
    with pytest.raises(PayloadExposureError, match="arguments"):
        validate_payload_free_audit({
            "event_id": "evt-2",
            "action_id": "payments.refund",
            "decision": "deny",
            "arguments": {"account": "customer-data"},
        })

def test_atomic_write_replaces_complete_file(tmp_path):
    target = tmp_path / "manifest.json"
    atomic_write_bytes(target, b'{"schema_version":"1.0.0"}\n')
    assert target.read_bytes() == b'{"schema_version":"1.0.0"}\n'
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []
```

Load both schemas and assert Draft 2020-12 metadata, `additionalProperties: false`, all required fields, and that a minimal valid manifest/apply plan passes the repository's existing JSON Schema validation helper. Assert the finding catalog contains exactly the 18 IDs listed above and no duplicate IDs.

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_contracts.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'canonical'`.

- [ ] **Step 3: Implement the minimal contracts and schemas**

Define these exact public types in `contracts.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

Phase = Literal["design", "pre-deploy", "post-deploy"]
Status = Literal["pass", "must-fix", "should-fix", "not-verified", "not-applicable"]
Consequence = Literal["read", "write", "external-egress", "irreversible"]

@dataclass(frozen=True)
class SourceRef:
    repository: str
    commit: str
    dirty: bool

@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    kind: str
    source: str
    sha256: str
    collected_at: str | None
    freshness_seconds: int | None
    live_verified: bool
    phase: Phase
    repository: str
    source_commit: str
    target_environment: str | None
    policy_set_sha256: str | None

@dataclass(frozen=True)
class Finding:
    finding_id: str
    status: Status
    phase: Phase
    plane: Literal["runtime", "change", "both"]
    reason_code: str
    summary: str
    details: str
    affected_actions: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    remediation_ids: tuple[str, ...] = ()
    residual_risk_ref: str | None = None

@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    display_name: str
    aliases: tuple[str, ...]
    owner: str | None
    declaration_refs: tuple[str, ...]
    implementation_refs: tuple[str, ...]
    input_schema_sha256: str | None
    output_schema_sha256: str | None
    source: str
    consequence: Consequence | None
    secondary_consequences: tuple[Consequence, ...]
    reversible: bool | None
    compensation_ref: str | None
    execution_modes: tuple[str, ...]
    provider_hosted: bool
    approval_required: bool | None
    policy_ids: tuple[str, ...] = ()
    known_runtime_paths: tuple[str, ...] = ()
    inventory_status: Status = "not-verified"

@dataclass(frozen=True)
class PathRecord:
    path_id: str
    action_id: str
    mode: str
    nodes: tuple[str, ...]
    pre_action_seam: str | None
    equivalent_control_ref: str | None
    covered: bool
    status: Status
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class DetectionEvidence:
    detected: bool
    references: tuple[str, ...]
    ambiguity: str | None

@dataclass(frozen=True)
class AdapterObservations:
    entry_points: tuple[dict[str, object], ...]
    actions: tuple[ActionRecord, ...]
    mediation: tuple[PathRecord, ...]
    probe_cases: tuple[dict[str, object], ...]

@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    action_id: str | None
    path_id: str | None
    status: Status
    reason_code: str
    expected: str
    observed: str
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class AssessmentOptions:
    root: Path
    phase: Phase
    emit: bool = False
    gate: bool = False
    live_github: bool = False
    live_azure: bool = False
    staging: bool = False
    repository: str | None = None
    default_branch: str | None = None
    subscription: str | None = None
    staging_resource_group: str | None = None
    deploy_identity: str | None = None
    now: str = "1970-01-01T00:00:00Z"

@dataclass(frozen=True)
class AssessmentResult:
    source: SourceRef
    actions: tuple[ActionRecord, ...]
    paths: tuple[PathRecord, ...]
    probes: tuple[ProbeResult, ...]
    findings: tuple[Finding, ...]
    evidence: tuple[EvidenceRef, ...]
    policy_hashes: tuple[dict[str, object], ...] = field(default_factory=tuple)
    pins: Mapping[str, object] = field(default_factory=dict)
    conformance_claims: tuple[dict[str, object], ...] = ()
    conformance_reports: tuple[dict[str, object], ...] = ()
    change_plane: Mapping[str, object] = field(default_factory=dict)
    residual_risks: tuple[dict[str, object], ...] = ()
```

Define `UnsafeTargetError(ValueError)` in `contracts.py` so CLI, live collectors, and staging-canary code share one refusal type.

Implement `canonical.py` with:

```text
CanonicalizationError(ValueError)
PayloadExposureError(ValueError)

canonical_bytes(value: object) -> bytes
sha256_hex(data: bytes) -> str
hash_files(root: Path, paths: Iterable[Path]) -> dict[str, object]
validate_payload_free_audit(record: Mapping[str, object]) -> None
atomic_write_bytes(path: Path, data: bytes) -> None
atomic_write_json(path: Path, value: object) -> None
```

`canonical_bytes` recursively rejects non-finite floats, then serializes UTF-8 JSON with `sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`, and `separators=(",", ":")`. `validate_payload_free_audit` rejects these case-insensitive keys anywhere in the object: `prompt`, `messages`, `arguments`, `args`, `input`, `output`, `result`, `secret`, `token`, `authorization`, `body`, `payload`. Hash fields such as `input_hash` and `output_hash` are allowed. `atomic_write_bytes` creates the parent, writes and `fsync`s a same-directory `NamedTemporaryFile`, uses `os.replace`, then `fsync`s the parent directory; on any error it removes only the temporary file and re-raises.

The manifest schema must require:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://threadlight.dev/schemas/governed-actions-manifest-1.0.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema", "assessor", "phase", "captured_at", "source", "pins",
    "policy_hashes", "action_inventory", "mediation_paths", "conformance",
    "change_plane", "findings", "evidence", "freshness",
    "residual_risks", "summary"
  ]
}
```

Use `$defs` for `sha256` (`^sha256:[0-9a-f]{64}$`), `status`, `evidenceRef`, `finding`, `action`, `path`, and `probe`. Every object sets `additionalProperties: false`. `schema` is fixed to `threadlight-governed-actions-manifest/v1`; `assessor` requires `name`, `version`, and `adapter`; `source` requires `repository`, `commit` matching `^[0-9a-f]{40}$`, and `dirty`; `pins` requires `dependencies`, `specifications`, and `probe_suite`; `policy_hashes` are `{path, sha256}`; `conformance` requires `claims`, `reports`, and `application_probes`; `change_plane` requires `repository`, `workflows`, and `identities`; `freshness` requires `status`, `valid_for_hours`, `oldest_source_at`, and `expires_at`; `summary` requires `verdict` plus `pass`, `must_fix`, `should_fix`, `not_verified`, and `not_applicable`.

The apply-plan schema must require:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://threadlight.dev/schemas/governed-actions-apply-plan-1.0.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema", "assessor_version", "source_commit", "captured_at",
    "manifest_sha256", "self_applying", "items"
  ]
}
```

`schema` is fixed to `threadlight-governed-actions-apply-plan/v1` and `self_applying` is fixed to `false`. Each item requires `finding_id`, `status`, `plane`, `affected_actions`, `affected_paths`, `remediation_kind`, `evidence_required`, `owner`, and `depends_on`; `remediation_kind` is one of `repo-edit`, `sibling-skill`, `manual`, or `deferred-to-pipeline`. `owner` is null unless customer evidence declared it. Every item carries the top-level `manifest_sha256` binding indirectly; a consumer rejects the plan when it differs from the current manifest. Neither schema permits secret or payload properties.

- [ ] **Step 4: Run contract tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_contracts.py -q
```

Expected: PASS, including schema validation, stable hashes, payload rejection, and atomic writes.

- [ ] **Step 5: Commit the contracts**

```bash
git add skills/threadlight-governed-actions/scripts/__init__.py skills/threadlight-governed-actions/scripts/contracts.py skills/threadlight-governed-actions/scripts/canonical.py skills/threadlight-governed-actions/references/governed-actions-manifest.schema.json skills/threadlight-governed-actions/references/governed-actions-apply-plan.schema.json skills/threadlight-governed-actions/references/finding-catalog.json skills/threadlight-governed-actions/tests/conftest.py skills/threadlight-governed-actions/tests/test_contracts.py
git commit -m "feat: add governed actions contracts" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 2: Build the explicit action inventory

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/inventory.py`
- Create: `skills/threadlight-governed-actions/tests/test_inventory.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/conformant-maf/specs/SPEC.md`
- Create: `skills/threadlight-governed-actions/tests/fixtures/conformant-maf/agent.yaml`
- Create: `skills/threadlight-governed-actions/tests/fixtures/conformant-maf/app/agent.py`

- [ ] **Step 1: Write failing inventory tests**

Use a registry with exactly:

```yaml
tools:
  - id: customer.lookup
    consequence: read
    execution_modes: [interactive, batch]
    provider_hosted: false
  - id: payments.refund
    consequence: irreversible
    execution_modes: [interactive, background, subagent, direct-tool]
    provider_hosted: false
```

SPEC section 8 must declare `payments.refund` as requiring approval and must mention the exact action ID. The fixture Python file defines `@tool(name="customer.lookup")` and `@tool(name="payments.refund")`.

Assert:

```python
def test_inventory_merges_spec_registry_and_python_tools(fixture_root):
    result = build_action_inventory(fixture_root / "conformant-maf")
    assert [action.action_id for action in result.actions] == [
        "customer.lookup", "payments.refund"
    ]
    refund = result.actions[1]
    assert refund.consequence == "irreversible"
    assert refund.execution_modes == (
        "background", "direct-tool", "interactive", "subagent"
    )
    assert result.findings == ()

def test_undeclared_or_unclassified_action_is_must_fix(tmp_path):
    write_fixture_with_python_tool(tmp_path, "mail.send")
    result = build_action_inventory(tmp_path)
    assert {(f.finding_id, f.status) for f in result.findings} == {
        ("ACT-001", "must-fix"),
        ("ACT-002", "must-fix"),
    }
```

Also assert duplicate IDs with conflicting metadata raise `InventoryError`, duplicate aliases emit `ACT-002`, unknown consequence strings raise `InventoryError`, precedence is `irreversible > external-egress > write > read` while lower classes remain in `secondary_consequences`, input/output schema hashes bind canonical schemas, owner/reversibility/compensation/approval fields remain explicit, and action ordering is byte-stable regardless of filesystem enumeration order.

- [ ] **Step 2: Run the inventory tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_inventory.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'inventory'`.

- [ ] **Step 3: Implement deterministic discovery and classification**

Define:

```python
@dataclass(frozen=True)
class InventoryResult:
    actions: tuple[ActionRecord, ...]
    findings: tuple[Finding, ...]
    spec_section_sha256: str
    safe_requirements: Mapping[str, Status]
    policy_paths: tuple[Path, ...]
```

```text
InventoryError(ValueError)

parse_spec_section_8(path: Path) -> tuple[set[str], str]
parse_action_registries(root: Path) -> dict[str, ActionRecord]
discover_python_tools(root: Path) -> set[str]
discover_policy_files(root: Path) -> tuple[Path, ...]
build_action_inventory(root: Path) -> InventoryResult
```

Rules are exact:

1. Read only `specs/SPEC.md` section beginning with a level-2 heading whose normalized text starts with `8`; stop at the next level-2 heading.
2. Extract action IDs only from backtick-delimited tokens matching `^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$`.
3. Read `agent.yaml`, `agent.yml`, `tool-registry.json`, and `tool_registry.json` when present. Registry metadata is authoritative; do not infer consequence from verbs or names.
4. Parse Python with `ast`; recognize decorators named `tool`, `function_tool`, and `kernel_function`, plus calls to `register_tool`. Accept only literal `name=` strings or the decorated function name.
5. Union all sources and aliases. A source-set mismatch emits `ACT-002`; absent explicit consequence emits `ACT-001`. Resolve every alias to one canonical action and reject an alias claimed by two actions.
6. Normalize modes and policy IDs by sorting and deduplicating; normalize action IDs to lowercase without altering punctuation.
7. Report section-8 SAFE declarations for `authorization`, `approval`, `idempotency-or-transaction`, `output-mediation`, and `audit` as `pass` or `not-verified`; a missing declaration sets `ACT-001` to `not-verified`, never an inferred pass.
8. Discover policy evidence only at `governance/**/*.json`, `governance/**/*.yaml`, `governance/**/*.yml`, `policies/**/*.json`, `policies/**/*.yaml`, and `policies/**/*.yml`; return sorted paths for canonical hashing in Task 10.
9. Never invent authorization rules, thresholds, approvers, or consequences.

- [ ] **Step 4: Run inventory tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_inventory.py -q
```

Expected: PASS with the two-action fixture ordered deterministically and ambiguity reported as must-fix.

- [ ] **Step 5: Commit the inventory**

```bash
git add skills/threadlight-governed-actions/scripts/inventory.py skills/threadlight-governed-actions/tests/test_inventory.py skills/threadlight-governed-actions/tests/fixtures/conformant-maf
git commit -m "feat: inventory governed actions" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 3: Add the MAF-first adapter and complete upstream pin

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/inputs.py`
- Create: `skills/threadlight-governed-actions/tests/test_inputs.py`
- Create: `skills/threadlight-governed-actions/scripts/maf_adapter.py`
- Create: `skills/threadlight-governed-actions/references/upstream-pin.json`
- Create: `skills/threadlight-governed-actions/references/upstream-pin.md`
- Create: `skills/threadlight-governed-actions/tests/test_maf_adapter.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/upstream-version-drift/installed-packages.json`

- [ ] **Step 1: Write failing adapter and drift tests**

In `test_inputs.py`, assert missing `specs/SPEC.md` raises `InputResolutionError`, unreadable/malformed files identify their repository-relative path and affected checks, and optional GitHub/Azure evidence is recorded as an unavailable capability rather than omitted. Assert deterministic discovery of action registries, Python runtime/worker files, policies/schemas, approval handlers, tests, CTK/conformance/eval reports, workflows/CODEOWNERS, dependency manifests, and lock files. Reject absolute evidence paths, `..` traversal, symlinks escaping the repository, and payload-bearing evidence JSON.

Assert this adapter protocol and behavior in `test_maf_adapter.py`:

```text
class RuntimeAdapter(Protocol):
    adapter_id: str
    detect(target: Path) -> DetectionEvidence
    resolved_tuple(target: Path) -> Mapping[str, str]
    discover_entry_points(target: Path) -> tuple[dict[str, object], ...]
    discover_actions(target: Path) -> tuple[ActionRecord, ...]
    discover_mediation(target: Path) -> tuple[PathRecord, ...]
    build_probe_cases(
        target: Path,
        inventory: tuple[ActionRecord, ...],
        graph: MediationGraph,
    ) -> tuple[dict[str, object], ...]
    run_local_probe(case: Mapping[str, object]) -> ProbeResult
```

```python
def test_maf_adapter_accepts_only_complete_tested_tuple(fixture_root):
    pin = load_upstream_pin(REFERENCE_ROOT / "upstream-pin.json")
    observed = {
        "agent-hooks-spec": (
            "0.1.0-alpha@0821ebbae252c45cd225304a464d1130963b82a8"
        ),
        "agent-hooks-sdk": (
            "0.1.0a5@sha256:"
            "4ae452b0a1d51540a1b74b0005b0a51f75fd4b80e9aca1a7403dece4dd6f9e46"
        ),
        "agent-framework-core": (
            "1.13.0@4b1afd90520310547cb0e9cdc70f644d80161e82"
        ),
        "ctk-vectors": "4f7af786c2757e26711b141e69144b6a336f403b",
        "conformance-python": "3.12.3",
        "acs-policy-schema": "not-applicable",
    }
    assert compare_upstream_tuple(observed, pin).status == "pass"

def test_any_tuple_drift_requires_ctk_and_application_probe_rerun(fixture_root):
    observed = json.loads(
        (fixture_root / "upstream-version-drift/installed-packages.json").read_text()
    )
    result = compare_upstream_tuple(observed, load_upstream_pin(PIN_PATH))
    assert result.status == "must-fix"
    assert result.finding.finding_id == "PIN-001"
    assert "rerun CTK and all application-path probes" in result.finding.details
```

Also assert `MAFAdapter.detect` requires an import/call under `agent_framework`, not merely a dependency file; provider-hosted tools are disclosed; and an unknown framework returns no adapter rather than claiming MAF conformance.

- [ ] **Step 2: Run adapter tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_inputs.py skills/threadlight-governed-actions/tests/test_maf_adapter.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'inputs'` and `ModuleNotFoundError: No module named 'maf_adapter'`.

- [ ] **Step 3: Implement the adapter and exact pin**

Define:

```python
@dataclass(frozen=True)
class PinComparison:
    status: Status
    finding: Finding | None
    observed: Mapping[str, str]
    expected: Mapping[str, str]

class MAFAdapter:
    adapter_id = "maf/v1"
```

```text
MAFAdapter.detect(self, target: Path) -> DetectionEvidence
MAFAdapter.resolved_tuple(self, target: Path) -> Mapping[str, str]
MAFAdapter.discover_entry_points(
    self, target: Path
) -> tuple[dict[str, object], ...]
MAFAdapter.discover_actions(
    self, target: Path
) -> tuple[ActionRecord, ...]
MAFAdapter.discover_mediation(
    self, target: Path
) -> tuple[PathRecord, ...]
MAFAdapter.build_probe_cases(
    self,
    target: Path,
    inventory: tuple[ActionRecord, ...],
    graph: MediationGraph,
) -> tuple[dict[str, object], ...]
MAFAdapter.run_local_probe(
    self, case: Mapping[str, object]
) -> ProbeResult
load_upstream_pin(path: Path) -> Mapping[str, object]
compare_upstream_tuple(
    observed: Mapping[str, str], pin: Mapping[str, object]
) -> PinComparison
```

Define input resolution as:

```text
InputResolutionError(ValueError)

ResolvedInputs:
    spec: Path
    registries: tuple[Path, ...]
    runtime_files: tuple[Path, ...]
    policy_files: tuple[Path, ...]
    approval_files: tuple[Path, ...]
    test_and_report_files: tuple[Path, ...]
    workflow_files: tuple[Path, ...]
    ownership_files: tuple[Path, ...]
    dependency_files: tuple[Path, ...]
    missing_capabilities: Mapping[str, tuple[str, ...]]

resolve_inputs(target: Path, phase: Phase) -> ResolvedInputs
allowlisted_evidence_path(target: Path, candidate: Path) -> Path
```

`resolve_inputs` requires `specs/SPEC.md` for design/pre-deploy and a nonempty runtime/tool declaration set for pre-deploy. It discovers only repository-relative files matching: registry names from Task 2; `**/*.py` excluding virtual environments; policy globs from Task 2; approval files containing AST symbols `issue_approval`, `consume_approval`, `redeem_approval`, or `ApprovalHandler`; `tests/**`, `**/conformance/**`, `**/evals/**`; `.github/workflows/*.{yml,yaml}`; both CODEOWNERS locations; and dependency/lock names `pyproject.toml`, `requirements*.txt`, `uv.lock`, `poetry.lock`, `pdm.lock`, `package.json`, and lockfiles. Parse failures identify the path and affected finding IDs; they never return an empty success-shaped input set.

Write `upstream-pin.json` with this complete tested tuple:

```json
{
  "pin_schema_version": "1.0.0",
  "status": "alpha-experimental",
  "agent_hooks": {
    "spec": "AGENT-HOOKS-0.1",
    "spec_version": "0.1.0-alpha",
    "repository": "https://github.com/responsibleai/agent-hooks",
    "repository_commit": "0821ebbae252c45cd225304a464d1130963b82a8",
    "spec_blob_sha": "5f13c1c8972d2f0186bcae4b7e585b25824b651b"
  },
  "sdk": {
    "distribution": "agent-hooks-sdk",
    "version": "0.1.0a5",
    "source_commit": "4f7af786c2757e26711b141e69144b6a336f403b",
    "artifact": "agent_hooks_sdk-0.1.0a5.tar.gz",
    "artifact_sha256": "4ae452b0a1d51540a1b74b0005b0a51f75fd4b80e9aca1a7403dece4dd6f9e46"
  },
  "ctk": {
    "vector_source_commit": "4f7af786c2757e26711b141e69144b6a336f403b",
    "total_vectors": 51,
    "applicable_vectors": 47,
    "passed_vectors": 47,
    "skipped_vectors": 4
  },
  "maf": {
    "distribution": "agent-framework-core",
    "version": "1.13.0",
    "source_commit": "4b1afd90520310547cb0e9cdc70f644d80161e82",
    "integration_packages": {
      "agent-framework-core": "1.13.0"
    },
    "integration_status": "experimental"
  },
  "acs": {
    "policy_schema": null,
    "status": "not-applicable"
  },
  "conformance_report": {
    "repository_commit": "0821ebbae252c45cd225304a464d1130963b82a8",
    "path": "conformance/claims/maf/REPORT.md",
    "blob_sha": "e4a97194e7091a15555afbc541da637765d819bf",
    "claim": "section-13.1",
    "certification": false
  },
  "conformance_python": "3.12.3",
  "drift_policy": "exact-tuple-rerun-ctk-and-application-probes"
}
```

`upstream-pin.md` must explicitly state: Agent Hooks is Draft/alpha and cooperative, not a security boundary; MAF integration is experimental; the report attests `agent-framework-core==1.13.0` only; newer versions do not inherit the claim; conformance is not certification; assessment also records the resolved local artifact/lock hashes and Python runtime; all tuple changes require pin review, CTK rerun, application probes, new evidence hashes, and customer sign-off.

- [ ] **Step 4: Run adapter tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_inputs.py skills/threadlight-governed-actions/tests/test_maf_adapter.py -q
```

Expected: PASS, with any version or source-commit difference producing `PIN-001`.

- [ ] **Step 5: Commit the adapter and pin**

```bash
git add skills/threadlight-governed-actions/scripts/inputs.py skills/threadlight-governed-actions/scripts/maf_adapter.py skills/threadlight-governed-actions/references/upstream-pin.json skills/threadlight-governed-actions/references/upstream-pin.md skills/threadlight-governed-actions/tests/test_inputs.py skills/threadlight-governed-actions/tests/test_maf_adapter.py skills/threadlight-governed-actions/tests/fixtures/upstream-version-drift
git commit -m "feat: add maf governed actions adapter" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 4: Construct and assess the runtime mediation graph

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/mediation.py`
- Create: `skills/threadlight-governed-actions/tests/test_mediation.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/unmediated-background/agent.yaml`
- Create: `skills/threadlight-governed-actions/tests/fixtures/unmediated-background/app/agent.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/provider-hosted-side-effect/agent.yaml`
- Create: `skills/threadlight-governed-actions/tests/fixtures/provider-hosted-side-effect/app/agent.py`

- [ ] **Step 1: Write failing graph tests**

Declare a fixture where `payments.refund` has interactive, batch, background, subagent, and direct-tool modes, but background and batch dispatch call the provider directly. Declare `mail.send` as `external-egress`, mode `provider-hosted-tool`, `provider_hosted: true`, with no server control reference.

Assert:

```python
def test_every_consequential_mode_becomes_a_graph_path(fixture_root):
    graph = build_mediation_graph(
        fixture_root / "unmediated-background", inventory.actions, adapter
    )
    assert {(p.action_id, p.mode) for p in graph.paths} == {
        ("payments.refund", "interactive"),
        ("payments.refund", "batch"),
        ("payments.refund", "background"),
        ("payments.refund", "subagent"),
        ("payments.refund", "direct-tool"),
    }
    assert {(f.finding_id, f.summary) for f in graph.findings} == {
        ("MED-001", "batch path for payments.refund lacks pre-action mediation"),
        ("MED-001", "background path for payments.refund lacks pre-action mediation"),
        ("MED-002", "declared mediation coverage is incomplete"),
    }

def test_provider_hosted_side_effect_is_unsupported_without_equivalent_control(
    fixture_root,
):
    graph = assess_provider_paths(fixture_root / "provider-hosted-side-effect", actions)
    finding = graph.findings[0]
    assert finding.finding_id == "MED-003"
    assert finding.status == "must-fix"
    assert finding.reason_code == "unsupported"
```

Also test that a read-only provider-hosted action can be `not-applicable`, and that equivalent control evidence must name the server-side authorization, idempotency, and transaction constraint references.

- [ ] **Step 2: Run graph tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_mediation.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'mediation'`.

- [ ] **Step 3: Implement path graph construction**

Define:

```python
@dataclass(frozen=True)
class MediationGraph:
    nodes: tuple[dict[str, str], ...]
    edges: tuple[dict[str, str], ...]
    paths: tuple[PathRecord, ...]
    findings: tuple[Finding, ...]
```

```text
build_mediation_graph(
    root: Path,
    actions: tuple[ActionRecord, ...],
    adapter: RuntimeAdapter,
) -> MediationGraph

assess_provider_paths(
    root: Path, actions: tuple[ActionRecord, ...]
) -> MediationGraph
```

Path IDs are `sha256(canonical_bytes({"action_id": action_id, "mode": mode, "nodes": nodes}))[:16]`. Build paths from `adapter.discover_entry_points`, `adapter.discover_mediation`, the registry's known runtime paths, and static call evidence. Adapters emit observations only and never assign a passing status. Nodes use the approved order `entry`, `host/worker`, `agent/subagent`, `tool-router`, `pre-action-seam`, optional `approval-check`, `tool-service`, `post-action-seam`, `output-mediator`, `caller`, `audit-sink`. A path is covered only if an Agent Hooks/ACS pre-tool call or a declared equivalent server control occurs before `tool-service`. Post-model observation alone never counts as pre-action control. Emit `MED-002` when any non-provider mode is not explicitly covered or evidenced absent. For side-effecting provider-hosted tools, require evidence references for `authorization`, `idempotency`, and `transaction`; otherwise emit `MED-003` with status `must-fix` and reason code `unsupported`.

- [ ] **Step 4: Run graph tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_mediation.py -q
```

Expected: PASS with exactly two bypass findings and one unsupported provider finding.

- [ ] **Step 5: Commit the mediation graph**

```bash
git add skills/threadlight-governed-actions/scripts/mediation.py skills/threadlight-governed-actions/tests/test_mediation.py skills/threadlight-governed-actions/tests/fixtures/unmediated-background skills/threadlight-governed-actions/tests/fixtures/provider-hosted-side-effect
git commit -m "feat: assess runtime mediation paths" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 5: Probe deny, transform, and fail-closed application behavior

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/probes.py`
- Create: `skills/threadlight-governed-actions/tests/test_probes.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/interceptor-failure/app/agent.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/interceptor-failure/governance/probe-contract.json`

- [ ] **Step 1: Write failing enforcement-probe tests**

The probe contract names an importable application dispatch callable and an audit sink:

```json
{
  "dispatch": "app.agent:dispatch_probe",
  "audit_sink": "app.agent:AUDIT_EVENTS",
  "timeout_ms": 100,
  "side_effect_mode": "synthetic",
  "observation_ledger": "governance/probe-ledger.jsonl",
  "actions": ["payments.refund"]
}
```

Assert:

```python
@pytest.mark.parametrize(
    ("probe_id", "fault", "expected"),
    [
        ("deny", "deny", "tool_not_invoked"),
        ("transform", "transform", "tool_received_transformed_arguments"),
        ("crash", "raise", "tool_not_invoked"),
        ("timeout", "sleep", "tool_not_invoked"),
        ("malformed-verdict", "invalid", "tool_not_invoked"),
    ],
)
def test_application_dispatch_enforces_probe(fixture_root, probe_id, fault, expected):
    result = run_application_probe(
        fixture_root / "interceptor-failure",
        ProbeCase(probe_id, "payments.refund", fault, {"amount": 7}),
    )
    assert result.expected == expected
    assert result.status == "pass"
```

Add one deliberately fail-open fixture assertion that produces `ENF-002`, not a tooling exception. Assert transformed arguments are canonicalized before comparison and raw arguments never appear in `ProbeResult.observed`.

- [ ] **Step 2: Run probe tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q -k 'deny or transform or crash or timeout or malformed'
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'probes'`.

- [ ] **Step 3: Implement hermetic application-path probes**

Define:

```python
@dataclass(frozen=True)
class ProbeCase:
    probe_id: str
    action_id: str
    fault: str
    arguments: Mapping[str, object]
```

```text
ProbeContractError(ValueError)
ProbeToolingError(RuntimeError)

load_probe_contract(root: Path) -> Mapping[str, object]
run_application_probe(root: Path, case: ProbeCase) -> ProbeResult
run_enforcement_probe_set(root: Path) -> tuple[ProbeResult, ...]
findings_from_probes(
    probes: tuple[ProbeResult, ...]
) -> tuple[Finding, ...]
```

Load the fixture module in an isolated subprocess with a sanitized environment and `PYTHONHASHSEED=0`. Reject contracts whose `side_effect_mode` is not `synthetic` or `dry-run`. Send the probe case on stdin as canonical JSON. Enforce `timeout_ms` at the subprocess boundary. The application fixture and synthetic tool service append payload-free start/invocation/decision records to the exclusive temporary observation ledger so the parent can prove whether the tool was reached even after killing a timed-out child. The child reports only invocation count, argument hash, decision, exception class, and audit event IDs. Map nonzero child exit, timeout, malformed output, or malformed verdict to a completed `must-fix` probe and `ENF-002` only when the ledger proves the tool outcome; if the outcome is unobservable, raise `ProbeToolingError` for exit 3. A deny/transform mismatch maps to `ENF-001`. CTK report references are separate evidence and never substitute for these application-path probes.

- [ ] **Step 4: Run enforcement-probe tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q -k 'deny or transform or crash or timeout or malformed'
```

Expected: PASS; each fault blocks invocation, and the fail-open control case yields `ENF-002`.

- [ ] **Step 5: Commit enforcement probes**

```bash
git add skills/threadlight-governed-actions/scripts/probes.py skills/threadlight-governed-actions/tests/test_probes.py skills/threadlight-governed-actions/tests/fixtures/interceptor-failure
git commit -m "feat: probe governed action enforcement" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 6: Probe approval anti-replay, output mediation, and payload-free audit

**Files:**
- Modify: `skills/threadlight-governed-actions/scripts/probes.py`
- Modify: `skills/threadlight-governed-actions/tests/test_probes.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/approval-replay/app/agent.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/approval-replay/governance/probe-contract.json`
- Create: `skills/threadlight-governed-actions/tests/fixtures/output-streaming/app/agent.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/output-streaming/governance/probe-contract.json`

- [ ] **Step 1: Add failing approval/output/audit tests**

Use this exact approval binding:

```python
binding = ApprovalBinding(
    target_scope="account:synthetic-001",
    requesting_subject="subject:pseudonymous-requester",
    approving_subject="subject:pseudonymous-approver",
    approving_role="role:synthetic-reviewer",
    tenant="tenant:synthetic-001",
    policy_id="policy:refund-v1",
    policy_hash="sha256:" + "a" * 64,
    action_id="payments.refund",
    arguments={"amount": 7, "currency": "USD"},
    issued_at="2026-09-01T12:00:00Z",
    expires_at="2026-09-01T12:05:00Z",
    nonce="nonce-0001",
)
```

Assert:

```python
def test_approval_is_single_use_and_bound_to_canonical_action(fixture_root):
    approved = run_approval_probe(root, binding, now="2026-09-01T12:00:00Z")
    replayed = run_approval_probe(root, binding, now="2026-09-01T12:00:01Z")
    mutated = run_approval_probe(
        root, replace(binding, arguments={"amount": 8, "currency": "USD"}),
        now="2026-09-01T12:00:02Z",
    )
    assert [p.status for p in (approved, replayed, mutated)] == [
        "pass", "pass", "pass"
    ]
    assert replayed.observed == "replay_rejected"
    assert mutated.observed == "binding_mismatch_rejected"

def test_output_is_buffered_until_output_verdict(fixture_root):
    result = run_output_probe(fixture_root / "output-streaming", verdict="deny")
    assert result.status == "pass"
    assert result.observed == "zero_bytes_egressed"

def test_audit_probe_rejects_payload_bearing_record(fixture_root):
    findings = findings_from_probes(run_privacy_probe_set(root))
    assert [(f.finding_id, f.status) for f in findings] == [
        ("AUD-001", "must-fix")
    ]
```

Add expired approval, wrong subject, renamed action, argument key-order normalization, transform-before-approval binding, incremental output without declared exposure bound, and a payload-free audit pass case.

- [ ] **Step 2: Run the added tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q -k 'approval or output or audit'
```

Expected: FAIL with `ImportError: cannot import name 'ApprovalBinding' from 'probes'`.

- [ ] **Step 3: Implement approval, output, and audit probes**

Add:

```python
@dataclass(frozen=True)
class ApprovalBinding:
    target_scope: str
    requesting_subject: str
    approving_subject: str
    approving_role: str
    tenant: str
    policy_id: str
    policy_hash: str
    action_id: str
    arguments: Mapping[str, object]
    issued_at: str
    expires_at: str
    nonce: str
```

```text
approval_digest(binding: ApprovalBinding) -> str
run_approval_probe(
    root: Path, binding: ApprovalBinding, now: str
) -> ProbeResult
run_output_probe(root: Path, verdict: str) -> ProbeResult
run_privacy_probe_set(root: Path) -> tuple[ProbeResult, ...]
```

The digest is SHA-256 over canonical JSON with exactly `target_scope`, `requesting_subject`, `approving_subject`, `approving_role`, `tenant`, `policy_id`, `policy_hash`, normalized `action_id`, canonical `arguments`, `issued_at`, `expires_at`, and `nonce`. Acceptance consumes the nonce atomically in the service-side fixture. Expired, replayed, subject/role-mismatched, target-mismatched, tenant-mismatched, policy-mismatched, action-mismatched, and argument-mismatched records must fail before tool invocation; all failures map to `APR-001`. Buffered output must show zero egress before a final verdict. Incremental output passes only when an explicit nonzero exposure bound and chunk-level mediation are both evidenced; otherwise emit `OUT-001`. Audit validation delegates to `validate_payload_free_audit`.

- [ ] **Step 4: Run approval/output/audit tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_probes.py -q -k 'approval or output or audit'
```

Expected: PASS with replay/mutation rejected, deny releasing zero bytes, and raw payload audit records flagged.

- [ ] **Step 5: Commit the remaining runtime probes**

```bash
git add skills/threadlight-governed-actions/scripts/probes.py skills/threadlight-governed-actions/tests/test_probes.py skills/threadlight-governed-actions/tests/fixtures/approval-replay skills/threadlight-governed-actions/tests/fixtures/output-streaming
git commit -m "feat: probe approvals output and audit" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 7: Assess the GitHub Copilot change plane statically

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/ghcp.py`
- Create: `skills/threadlight-governed-actions/tests/test_ghcp.py`
- Create: `skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp/.github/workflows/deploy.yml`
- Create: `skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp/.github/workflows/tests.yml`
- Create: `skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp/.github/CODEOWNERS.absent`
- Create: `skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp/governance/change-plane.json`

- [ ] **Step 1: Write failing static change-plane tests**

The fixture contains a `push` deployment workflow, `permissions: write-all`, `actions/checkout@v4`, secret-based cloud login, no real CODEOWNERS, no CTK/application-probe job, and the same `client-id` for test and deploy.

Assert:

```python
def test_unprotected_change_plane_emits_all_required_findings(fixture_root):
    result = assess_change_plane(
        fixture_root / "unprotected-ghcp", live_github=None, live_azure=None
    )
    assert {f.finding_id for f in result.findings} == {
        "GHCP-001", "GHCP-002", "GHCP-003",
        "GHCP-004", "GHCP-005", "GHCP-006",
    }
    assert result.controls["ghcp_internal_loop_intercepted"] is False

def test_full_sha_and_oidc_are_recognized(tmp_path):
    workflow = pinned_oidc_workflow(
        action_sha="0ad4c47a9e566829e19b6099ee3458ac923f5d3c"
    )
    result = assess_workflow(workflow)
    assert result.sha_pins == "pass"
    assert result.oidc_wif == "pass"
```

Assert a 39-character hash fails, first-party and third-party actions are both held to a 40-character lowercase SHA, `pull_request_target` with untrusted checkout is must-fix, and local static files can never prove branch protection live.

- [ ] **Step 2: Run static GHCP tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_ghcp.py -q -k 'static or unprotected or sha or oidc'
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'ghcp'`.

- [ ] **Step 3: Implement static GHCP assessment**

Define:

```python
@dataclass(frozen=True)
class ChangePlaneResult:
    controls: Mapping[str, Status | bool]
    findings: tuple[Finding, ...]
    evidence: tuple[EvidenceRef, ...]
```

```text
assess_workflow(path: Path) -> Mapping[str, Status]
assess_change_plane(
    root: Path,
    live_github: Mapping[str, object] | None,
    live_azure: Mapping[str, object] | None,
) -> ChangePlaneResult
```

Static rules:

1. PR-only checks reject direct deploy on `push` to protected/default branches and reject bypass commands.
2. Ownership requires a real `.github/CODEOWNERS` or `CODEOWNERS`, not a declared filename, and patterns must cover `src/governance/**`, `policies/**`, governance tests, the governed-actions workflow, and both governed-actions evidence JSON paths.
3. CI requires CTK and governed application-probe commands in a workflow triggered by `pull_request`; when resolved inputs contain an eval suite, its exact runner must also be present. Static presence is `pass`, required-check enforcement stays `not-verified` without live evidence.
4. Every `uses:` reference except local `./` actions must end in exactly 40 lowercase hexadecimal characters.
5. Azure login must use OIDC/WIF (`id-token: write`) and must not read client secrets, passwords, publish profiles, or service-principal secrets.
6. Workflow/job permissions must be explicit and least privilege; test/build and deployment identity identifiers must differ. Live evidence verifies required reviewers, required checks, environment protection, federated subject claims, and bypass restrictions.
7. Set `ghcp_internal_loop_intercepted` to `False` unconditionally and explain that this plane governs PR/CI/deployment supply chain, not GitHub Copilot's internal reasoning or tool loop.

- [ ] **Step 4: Run static GHCP tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_ghcp.py -q -k 'static or unprotected or sha or oidc'
```

Expected: PASS with all six control findings; unavailable live branch/role evidence is represented by `GHCP-002`/`GHCP-006` status `not-verified` when the static fixture does not already prove those controls missing.

- [ ] **Step 5: Commit static GHCP assessment**

```bash
git add skills/threadlight-governed-actions/scripts/ghcp.py skills/threadlight-governed-actions/tests/test_ghcp.py skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp
git commit -m "feat: assess ghcp change controls" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 8: Add optional live GitHub/Azure evidence and staging-only post-deploy guards

**Files:**
- Modify: `skills/threadlight-governed-actions/scripts/ghcp.py`
- Modify: `skills/threadlight-governed-actions/tests/test_ghcp.py`
- Modify: `skills/threadlight-governed-actions/scripts/probes.py`
- Modify: `skills/threadlight-governed-actions/tests/test_probes.py`
- Create: `skills/threadlight-governed-actions/scripts/alerts.py`
- Create: `skills/threadlight-governed-actions/tests/test_alerts.py`

- [ ] **Step 1: Add failing live-evidence tests**

Inject command runners; never access a network in unit tests. Assert exact commands:

```python
def test_live_github_collects_rules_and_required_checks(fake_runner):
    collect_live_github(
        "aiappsgbb/threadlight-skills", "main", run=fake_runner
    )
    assert fake_runner.commands == [
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/rulesets?includes_parents=true"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/branches/main/protection"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/actions/permissions/workflow"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/environments"],
        ["gh", "api", "repos/aiappsgbb/threadlight-skills/actions/oidc/customization/sub"],
    ]

def test_post_deploy_refuses_non_staging_target():
    with pytest.raises(UnsafeTargetError, match="staging"):
        validate_post_deploy_target(
            phase="post-deploy", staging=False, destructive=False
        )

def test_staging_canary_allows_only_nondestructive_https_read(fake_http_runner):
    result = run_staging_canary({
        "environment": "staging",
        "destructive": False,
        "method": "HEAD",
        "url": "https://staging.example.invalid/governance/health",
        "expected_status": 204,
    }, run=fake_http_runner)
    assert result.status == "pass"
    assert fake_http_runner.requests[0]["method"] == "HEAD"

@pytest.mark.parametrize("field,value", [
    ("environment", "production"),
    ("destructive", True),
    ("method", "POST"),
    ("url", "http://staging.example.invalid/governance/health"),
])
def test_staging_canary_rejects_unsafe_contract(field, value, safe_canary):
    with pytest.raises(UnsafeTargetError):
        run_staging_canary({**safe_canary, field: value}, run=lambda request: None)

def test_missing_live_permissions_remain_not_verified(fake_runner_403):
    evidence = collect_live_github("owner/repo", "main", run=fake_runner_403)
    assert evidence.status == "not-verified"
    assert evidence.finding.finding_id == "GHCP-002"
```

Add Azure command assertions for `az identity federated-credential list`, `az role assignment list`, and `az role definition list`; assert no command contains secrets or writes; assert any destructive canary request is rejected. In `test_alerts.py`, create an alert catalog containing the eight classes from design section 17 and assert all pass only when each definition has `reason_code`, `correlation_id`, `enabled`, and no payload field. Missing definitions produce `OPS-001`/`should-fix`; evidence of dropped mandatory events produces `OPS-001`/`must-fix`; unavailable live state produces `OPS-001`/`not-verified`.

- [ ] **Step 2: Run live-evidence tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_ghcp.py skills/threadlight-governed-actions/tests/test_alerts.py skills/threadlight-governed-actions/tests/test_probes.py -q -k 'live or staging or permissions or alert or canary'
```

Expected: FAIL with `ImportError: cannot import name 'collect_live_github' from 'ghcp'` and `ModuleNotFoundError: No module named 'alerts'`.

- [ ] **Step 3: Implement bounded collectors and safety guards**

Add:

```python
@dataclass(frozen=True)
class LiveEvidenceResult:
    status: Status
    data: Mapping[str, object]
    evidence: tuple[EvidenceRef, ...]
    finding: Finding | None
```

```text
UnsafeTargetError imported from contracts

collect_live_github(
    repository: str, default_branch: str, run: CommandRunner
) -> LiveEvidenceResult
collect_live_azure(
    subscription: str,
    resource_group: str,
    deploy_identity: str,
    run: CommandRunner,
) -> LiveEvidenceResult
validate_post_deploy_target(
    phase: Phase, staging: bool, destructive: bool
) -> None
assess_alerts(
    root: Path,
    phase: Phase,
    live_evidence: Mapping[str, object] | None,
) -> tuple[Finding, tuple[EvidenceRef, ...]]
run_staging_canary(
    contract: Mapping[str, object],
    run: HttpReadRunner,
) -> ProbeResult
```

GitHub collection runs the five exact commands asserted in Step 1. Azure collection runs:

```text
az identity federated-credential list --identity-name DEPLOY_IDENTITY --resource-group STAGING_RG --subscription SUBSCRIPTION -o json
az role assignment list --assignee DEPLOY_IDENTITY --resource-group STAGING_RG --subscription SUBSCRIPTION --all -o json
az role definition list --name ROLE_NAME --subscription SUBSCRIPTION -o json
```

The role-definition command runs once for each unique role name returned by the assignment command, in sorted order. Collectors use only these read-only commands, redact command stderr to exit code and error class, canonicalize returned JSON, and hash it before recording evidence. HTTP 401/403, missing CLI, absent subscription, or incomplete results return the affected `GHCP-002`, `GHCP-005`, or `GHCP-006` control as `not-verified`; they never return `pass`.

`assess_alerts` recognizes exactly: `unmediated-action`, `interceptor-failure`, `approval-replay`, `output-mediator-failure`, `audit-delivery-failure`, `tuple-drift`, `repository-protection-drift`, and `deployment-identity-drift`. Every definition must be enabled and declare stable reason/correlation fields without payloads. Missing production definitions are `OPS-001`/`should-fix`; proven mandatory-event loss is `OPS-001`/`must-fix`; inaccessible selected live state is `OPS-001`/`not-verified`.

`run_staging_canary` accepts only HTTPS `GET`/`HEAD`, `environment: staging`, `destructive: false`, no request body, no query string, and no literal authorization headers. It records status, duration, deployment ID header, and response hash only; it discards the response body. All other canary contracts raise `UnsafeTargetError`. Post-deploy runs this optional canary plus hermetic/non-mutating probes only.

- [ ] **Step 4: Run live-evidence tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_ghcp.py skills/threadlight-governed-actions/tests/test_alerts.py skills/threadlight-governed-actions/tests/test_probes.py -q -k 'live or staging or permissions or alert or canary'
```

Expected: PASS with read-only command lists, staging refusal, and permission failures preserved as `not-verified`.

- [ ] **Step 5: Commit live evidence support**

```bash
git add skills/threadlight-governed-actions/scripts/ghcp.py skills/threadlight-governed-actions/scripts/alerts.py skills/threadlight-governed-actions/scripts/probes.py skills/threadlight-governed-actions/tests/test_ghcp.py skills/threadlight-governed-actions/tests/test_alerts.py skills/threadlight-governed-actions/tests/test_probes.py
git commit -m "feat: collect governed actions live evidence" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 9: Render deterministic artifacts and customer evidence

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/render.py`
- Create: `skills/threadlight-governed-actions/tests/test_render_cli.py`

- [ ] **Step 1: Write failing renderer tests**

Assert:

```python
def test_manifest_contains_every_evidence_binding(assessment):
    manifest = build_manifest(assessment)
    assert manifest["schema"] == "threadlight-governed-actions-manifest/v1"
    assert manifest["assessor"] == {
        "name": "threadlight-governed-actions",
        "version": "0.1.0",
        "adapter": "maf/v1",
    }
    assert manifest["source"] == {
        "repository": "aiappsgbb/threadlight-skills",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "dirty": False,
    }
    assert all("sha256" in ref for ref in manifest["evidence"])
    assert manifest["residual_risks"]

def test_evidence_pack_has_required_customer_sections(assessment):
    text = render_evidence_pack(assessment)
    headings = [
        "# Governance Evidence Pack",
        "## Scope and trust model",
        "## Architecture and data flow",
        "## Runtime action inventory",
        "## Runtime mediation graph",
        "## Application-path probe evidence",
        "## GitHub Copilot change plane",
        "## Pass/fail matrix",
        "## Residual-risk register",
        "## Remediation plan",
    ]
    assert all(heading in text for heading in headings)

def test_rendering_is_independent_of_input_order(shuffled_assessments):
    rendered = [canonical_bytes(build_manifest(a)) for a in shuffled_assessments]
    assert len(set(rendered)) == 1

def test_artifact_transaction_restores_prior_set_on_replace_failure(
    tmp_path, assessment, fail_second_replace
):
    seed_prior_valid_artifacts(tmp_path)
    before = artifact_hashes(tmp_path)
    with pytest.raises(ArtifactWriteError):
        write_artifacts(
            tmp_path, assessment, MANIFEST_PATH, EVIDENCE_PATH, APPLY_PLAN_PATH,
            replace=fail_second_replace,
        )
    assert artifact_hashes(tmp_path) == before
```

Also assert every `must-fix`, `should-fix`, and `not-verified` finding appears in `tests/governed-actions-apply-plan.json`, no passing/not-applicable finding creates remediation, unknown customer policy creates a `manual` item with `owner: null`, and no rendered artifact contains probe payload values. Recompute the manifest hash and assert it equals the apply plan's `manifest_sha256`.

- [ ] **Step 2: Run renderer tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q -k 'manifest or evidence_pack or rendering or remediation'
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'render'`.

- [ ] **Step 3: Implement renderers**

Define:

```text
build_manifest(result: AssessmentResult) -> dict[str, object]
render_evidence_pack(result: AssessmentResult) -> str
build_apply_plan(result: AssessmentResult) -> dict[str, object]
write_artifacts(
    root: Path,
    result: AssessmentResult,
    manifest_path: Path,
    evidence_path: Path,
    apply_plan_path: Path,
    replace: Callable[[Path, Path], None] = os.replace,
) -> tuple[Path, Path, Path]
```

Sort actions by `action_id`, paths by `(action_id, mode, path_id)`, probes by `probe_id`, findings by `(status rank, finding_id, reason_code)`, and evidence by `evidence_id`. Compute summary verdict as `ungoverned` for any `must-fix`, `partial` for any `should-fix`/`not-verified`, otherwise `governed`. Dirty source always prevents `governed`. Freshness uses `valid_for_hours: 24`, derives `oldest_source_at` from required evidence, computes `expires_at`, and becomes `not-verified` when required evidence has no trustworthy timestamp. The Markdown matrix columns are `ID | Plane | Control | Status | Reason | Evidence | Remediation`; runtime and change-plane rows are independently gated. Residual risk always includes cooperative host trust, non-certification, provider-hosted limitations, live-evidence freshness, upstream alpha/experimental drift, and unevidenced audit retention/deletion/residency/legal-hold policy. Each finding has a valid residual-risk reference. `build_apply_plan` hashes the complete canonical manifest as `sha256:<hex>` and copies that value to `manifest_sha256`. `write_artifacts` uses `atomic_write_json`/`atomic_write_bytes` for:

```text
tests/governed-actions-manifest.json
docs/governance/evidence-pack.md
tests/governed-actions-apply-plan.json
```

Paths may be overridden, but must resolve under the assessed root. Emit all three only after every render and schema validation succeeds. Stage all bytes in one same-filesystem directory, move existing artifacts to unique backup names, replace all three, and fsync their directories. If any replacement fails, restore every backup and remove every newly placed file before re-raising; only delete backups after all replacements succeed. This preserves the complete prior valid artifact set rather than exposing a mixed generation.

- [ ] **Step 4: Run renderer tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q -k 'manifest or evidence_pack or rendering or remediation'
```

Expected: PASS with order-independent bytes, complete customer sections, and one remediation per failing finding.

- [ ] **Step 5: Commit artifact rendering**

```bash
git add skills/threadlight-governed-actions/scripts/render.py skills/threadlight-governed-actions/tests/test_render_cli.py
git commit -m "feat: render governed actions evidence" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 10: Wire lifecycle CLI, read-only behavior, and exit semantics

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/governed_actions.py`
- Modify: `skills/threadlight-governed-actions/tests/test_render_cli.py`

- [ ] **Step 1: Add failing CLI tests**

Assert:

```python
def test_design_without_emit_does_not_modify_project(conformant_copy):
    before = tree_hash(conformant_copy)
    completed = run_cli([
        "--target", str(conformant_copy), "--phase", "design"
    ])
    assert completed.returncode == 0
    assert tree_hash(conformant_copy) == before

def test_gate_maps_findings_to_exit_one(nonconformant_copy):
    completed = run_cli([
        "--target", str(nonconformant_copy), "--phase", "pre-deploy", "--gate"
    ])
    assert completed.returncode == 1

def test_emit_writes_only_three_artifacts(conformant_copy):
    before = list_files(conformant_copy)
    completed = run_cli([
        "--target", str(conformant_copy), "--phase", "pre-deploy", "--emit"
    ])
    assert completed.returncode == 0
    assert list_files(conformant_copy) - before == {
        "tests/governed-actions-apply-plan.json",
        "docs/governance/evidence-pack.md",
        "tests/governed-actions-manifest.json",
    }
```

Assert invalid phase/options return 2, internal runner failure returns 3, `post-deploy` without `--staging` returns 2, `--live-github` permission failure plus `--gate` returns 1, and dirty source is represented explicitly rather than hidden.

- [ ] **Step 2: Run CLI tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q -k 'cli or emit or gate or design or post_deploy'
```

Expected: FAIL because `scripts/governed_actions.py` does not exist.

- [ ] **Step 3: Implement the CLI orchestration**

Expose:

```text
parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace
resolve_source(root: Path) -> SourceRef
assess(options: AssessmentOptions) -> AssessmentResult
exit_code(result: AssessmentResult, gate: bool) -> int
main(argv: Sequence[str] | None = None) -> int
```

CLI syntax:

```text
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target . \
  --phase {design,pre-deploy,post-deploy} \
  [--emit] [--gate] \
  [--repo owner/repo] \
  [--staging-resource-group STAGING_RG] \
  [--subscription SUBSCRIPTION] \
  [--deploy-identity DEPLOY_IDENTITY] \
  [--manifest-path tests/governed-actions-manifest.json] \
  [--evidence-path docs/governance/evidence-pack.md] \
  [--apply-plan-path tests/governed-actions-apply-plan.json]
```

`resolve_source` runs `git -C TARGET rev-parse --show-toplevel`, `git -C TARGET config --get remote.origin.url`, `git -C TARGET rev-parse HEAD`, and `git -C TARGET status --porcelain --untracked-files=no`; it normalizes the repository to `owner/repository`, requires a 40-character commit, and records dirty state. A non-Git target is invalid input. Dirty state prevents a `governed` verdict. `design` runs inventory and SAFE requirement validation only. `pre-deploy` runs inventory, pins, static graph, policy hashing from `InventoryResult.policy_paths`, approval binding, probes, alerts, and GHCP static/live checks. `post-deploy` is selected by `--phase post-deploy`, requires a staging resource group, and reruns only non-destructive application probes plus selected live evidence. Catch only `ArgumentError`/`ValueError` as exit 2 and declared assessor/tool errors as exit 3; unexpected exceptions print the exception class and return 3 without claiming success. Without `--emit`, no writes occur. `--emit` writes only the three report artifacts.

Without `--gate`, completed assessments return 0 regardless of findings. With `--gate`: design requires no `ACT-001`/`ACT-002` `must-fix`; pre-deploy requires no mandatory static/local `must-fix` or `not-verified`; post-deploy additionally requires every live capability explicitly selected by its CLI arguments. Optional unselected live evidence may remain `not-verified`, keeps verdict `partial`, and does not fail the gate.

- [ ] **Step 4: Run CLI tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q -k 'cli or emit or gate or design or post_deploy'
```

Expected: PASS with exact 0/1/2/3 semantics and no mutation outside explicit artifact output.

- [ ] **Step 5: Commit CLI orchestration**

```bash
git add skills/threadlight-governed-actions/scripts/governed_actions.py skills/threadlight-governed-actions/tests/test_render_cli.py
git commit -m "feat: add governed actions lifecycle cli" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 11: Add the bounded double-opt-in scaffold

**Files:**
- Create: `skills/threadlight-governed-actions/scripts/scaffold.py`
- Create: `skills/threadlight-governed-actions/tests/test_scaffold.py`
- Create: `skills/threadlight-governed-actions/references/scaffold/agent_hooks_interceptor.py.tmpl`
- Create: `skills/threadlight-governed-actions/references/scaffold/governed-actions.policy.yaml.tmpl`
- Create: `skills/threadlight-governed-actions/references/scaffold/approval-binding-fixture.json.tmpl`
- Create: `skills/threadlight-governed-actions/references/scaffold/test_governed_actions_contract.py.tmpl`
- Create: `skills/threadlight-governed-actions/references/scaffold/governed-actions.yml.tmpl`
- Modify: `skills/threadlight-governed-actions/scripts/governed_actions.py`

- [ ] **Step 1: Write failing scaffold tests**

Assert:

```python
EXPECTED_SCAFFOLD = {
    "src/governance/agent_hooks_interceptor.py",
    "policies/governed-actions.policy.yaml",
    "tests/governance/approval-binding-fixture.json",
    "tests/governance/test_governed_actions_contract.py",
    ".github/workflows/governed-actions.yml",
}

def test_scaffold_requires_both_opt_ins(tmp_path):
    for argv in (
        ["--target", str(tmp_path), "--scaffold", "maf"],
        ["--target", str(tmp_path), "--confirm-scaffold"],
    ):
        assert run_cli(argv).returncode == 2
        assert list_files(tmp_path) == set()

def test_scaffold_writes_exact_fixed_set_without_policy_invention(tmp_path):
    completed = run_cli([
        "--target", str(tmp_path), "--scaffold", "maf", "--confirm-scaffold",
    ])
    assert completed.returncode == 0
    assert list_files(tmp_path) == EXPECTED_SCAFFOLD
    policy = yaml.safe_load(
        (tmp_path / "policies/governed-actions.policy.yaml").read_text()
    )
    assert policy["rules"] == []
    assert policy["approvers"] == []
    assert policy["authorization"] == {"customer_owned": True}
    assert build_manifest(assess(scaffolded_options(tmp_path)))["summary"][
        "verdict"
    ] != "governed"
```

Assert a second run returns 2 and changes no bytes, symlink targets are refused, templates contain no real identities/thresholds, and the workflow action references are exact 40-character SHAs.

- [ ] **Step 2: Run scaffold tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_scaffold.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scaffold'`.

- [ ] **Step 3: Implement exact, no-overwrite scaffolding**

Define:

```python
SCAFFOLD_FILES: Mapping[str, str] = {
    "src/governance/agent_hooks_interceptor.py": "agent_hooks_interceptor.py.tmpl",
    "policies/governed-actions.policy.yaml": "governed-actions.policy.yaml.tmpl",
    "tests/governance/approval-binding-fixture.json": "approval-binding-fixture.json.tmpl",
    "tests/governance/test_governed_actions_contract.py": "test_governed_actions_contract.py.tmpl",
    ".github/workflows/governed-actions.yml": "governed-actions.yml.tmpl",
}

```

```text
ScaffoldRefusedError(ValueError)
scaffold(
    root: Path, scaffold_kind: str | None, confirm_scaffold: bool
) -> tuple[Path, ...]
```

Preflight all destinations before writing. Require both `scaffold_kind == "maf"` and `confirm_scaffold is True`. Refuse existing paths, symlink parents/targets, roots outside the project, or missing opt-ins. Stage all files in a temporary directory, then atomically place them with exclusive creation. The policy template is versioned, deny-by-default for consequential actions, and has `rules: []`, `approvers: []`, and `authorization.customer_owned: true`; the interceptor denies when no customer rule matches; the approval fixture contains only deterministic synthetic values. The workflow uses `actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c` and `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`, runs CTK plus local application probes with `contents: read`, and has no deploy permission. The normal phase path never invokes scaffold.

- [ ] **Step 4: Run scaffold tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_scaffold.py -q
```

Expected: PASS with exactly five files, two required opt-ins, empty customer policy, and no-overwrite behavior.

- [ ] **Step 5: Commit bounded scaffolding**

```bash
git add skills/threadlight-governed-actions/scripts/scaffold.py skills/threadlight-governed-actions/scripts/governed_actions.py skills/threadlight-governed-actions/references/scaffold skills/threadlight-governed-actions/tests/test_scaffold.py
git commit -m "feat: add bounded governance scaffold" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 12: Complete fixtures and lock golden deterministic outputs

**Files:**
- Create: `skills/threadlight-governed-actions/tests/test_golden_fixtures.py`
- Create: `skills/threadlight-governed-actions/tests/golden/conformant-manifest.json`
- Create: `skills/threadlight-governed-actions/tests/golden/nonconformant-manifest.json`
- Create: `skills/threadlight-governed-actions/tests/golden/conformant-evidence-pack.md`
- Create: `skills/threadlight-governed-actions/tests/golden/nonconformant-apply-plan.json`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/conformant-maf/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/unmediated-background/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/provider-hosted-side-effect/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/approval-replay/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/interceptor-failure/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/output-streaming/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/unprotected-ghcp/`
- Modify: `skills/threadlight-governed-actions/tests/fixtures/upstream-version-drift/`

- [ ] **Step 1: Write the failing fixture matrix test**

Parameterize the exact expectations:

```python
SCENARIOS = {
    "conformant-maf": {"exit": 0, "must_fix": set()},
    "unmediated-background": {
        "exit": 1, "must_fix": {"MED-001", "MED-002"}
    },
    "provider-hosted-side-effect": {"exit": 1, "must_fix": {"MED-003"}},
    "approval-replay": {"exit": 1, "must_fix": {"APR-001"}},
    "interceptor-failure": {"exit": 1, "must_fix": {"ENF-002"}},
    "output-streaming": {"exit": 1, "must_fix": {"OUT-001"}},
    "unprotected-ghcp": {
        "exit": 1, "must_fix": {
            "GHCP-001", "GHCP-002", "GHCP-003",
            "GHCP-004", "GHCP-005", "GHCP-006",
        },
    },
    "upstream-version-drift": {"exit": 1, "must_fix": {"PIN-001"}},
}

@pytest.mark.parametrize(("scenario", "expected"), SCENARIOS.items())
def test_scenario_findings_are_exact(fixture_root, scenario, expected):
    result = assess_fixture(fixture_root / scenario)
    assert gate_exit(result) == expected["exit"]
    assert {
        f.finding_id for f in result.findings if f.status == "must-fix"
    } == expected["must_fix"]
```

Freeze time at `2026-09-01T12:00:00Z` and source commit at `0123456789abcdef0123456789abcdef01234567`. Compare the four declared golden files byte-for-byte. Recursively scan fixture/golden files for common token, key, email, IP, account, and payload patterns; assert no secrets/customer data.

Add this exact invalidation matrix:

```python
@pytest.mark.parametrize(
    ("mutation", "finding_id"),
    [
        ("stale", "PIN-001"),
        ("wrong-commit", "PIN-001"),
        ("wrong-repository", "PIN-001"),
        ("wrong-environment", "GHCP-006"),
        ("wrong-policy-hash", "PIN-001"),
        ("malformed-policy", "ACT-001"),
        ("malformed-verdict", "ENF-002"),
        ("malformed-report", "PIN-001"),
        ("payload-in-evidence", "AUD-001"),
    ],
)
def test_invalid_evidence_never_passes(base_evidence, mutation, finding_id):
    result = assess_mutated_evidence(base_evidence, mutation)
    finding = next(f for f in result.findings if f.finding_id == finding_id)
    assert finding.status in {"must-fix", "not-verified"}
    assert build_manifest(result)["summary"]["verdict"] != "governed"
```

- [ ] **Step 2: Run the golden suite and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-governed-actions/tests/test_golden_fixtures.py -q
```

Expected: FAIL because golden files and the remaining exact fixture declarations are absent.

- [ ] **Step 3: Complete fixtures and generate reviewed golden files**

Create every path in the exact fixture file-set table. `assess_fixture` loads `governance/change-plane.json` and passes it as injected live GitHub/Azure evidence; production code never auto-trusts that fixture path. Generate goldens with:

```bash
PYTHONHASHSEED=0 python3 skills/threadlight-governed-actions/scripts/governed_actions.py --target skills/threadlight-governed-actions/tests/fixtures/conformant-maf --phase pre-deploy --emit
PYTHONHASHSEED=0 python3 skills/threadlight-governed-actions/scripts/governed_actions.py --target skills/threadlight-governed-actions/tests/fixtures/unmediated-background --phase pre-deploy --emit
```

Copy the three conformant outputs and the required nonconformant outputs into the four exact golden paths, then remove emitted report files from fixture directories. Review that every evidence reference resolves, all hashes use `sha256:` plus 64 lowercase hexadecimal characters, findings match the table above, the conformant pre-deploy summary verdict is `governed`, and payload values do not appear.

- [ ] **Step 4: Run the golden suite twice and confirm stable passes**

Run:

```bash
PYTHONHASHSEED=0 python -m pytest skills/threadlight-governed-actions/tests/test_golden_fixtures.py -q
PYTHONHASHSEED=123 python -m pytest skills/threadlight-governed-actions/tests/test_golden_fixtures.py -q
```

Expected: both runs PASS with byte-identical outputs.

- [ ] **Step 5: Commit fixture and golden coverage**

```bash
git add skills/threadlight-governed-actions/tests/test_golden_fixtures.py skills/threadlight-governed-actions/tests/fixtures skills/threadlight-governed-actions/tests/golden
git commit -m "test: lock governed actions evidence fixtures" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 13: Consume aggregate claims in production-ready

**Files:**
- Create: `skills/threadlight-production-ready/tests/test_governed_actions_manifest.py`
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`
- Modify: `skills/threadlight-production-ready/tests/test_version.py`
- Modify: `skills/threadlight-production-ready/SKILL.md`
- Modify: `skills/threadlight-production-ready/references/02-agent-governance.md`
- Modify: `skills/threadlight-production-ready/references/08-hitl-governance.md`
- Modify: `skills/threadlight-production-ready/references/09-supply-chain-security.md`

- [ ] **Step 1: Write failing production-ready aggregation tests**

Add exactly three catalog entries:

```python
EXPECTED_AGGREGATES = {
    "AGT-007": ("runtime", {
        "ACT-001", "ACT-002", "MED-001", "MED-002", "MED-003",
        "ENF-001", "ENF-002", "PIN-001", "OPS-001",
    }),
    "HITL-008": ("approval-output", {
        "APR-001", "OUT-001", "AUD-001",
    }),
    "SUP-014": ("change", {
        "GHCP-001", "GHCP-002", "GHCP-003",
        "GHCP-004", "GHCP-005", "GHCP-006", "OPS-001",
    }),
}
```

Assert a valid governed manifest makes all three pass. A child `must-fix`, `should-fix`, or `not-verified` maps only to its owning aggregate, except `OPS-001`, which maps to governance and supply chain. Missing, malformed, stale, dirty-source-mismatched, or source-commit-mismatched manifest is `not-verified`, never pass. Assert production-ready does not invoke governed-actions probes or copy child findings into its own detailed catalog.

- [ ] **Step 2: Run the aggregation tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_governed_actions_manifest.py skills/threadlight-production-ready/tests/test_version.py -q
```

Expected: FAIL because `AGT-007`, `HITL-008`, and `SUP-014` are absent and version remains `0.11.0`.

- [ ] **Step 3: Implement semantic manifest consumption**

Add:

```text
load_governed_actions_manifest(root: Path) -> dict[str, object] | None
aggregate_governed_actions(
    manifest: Mapping[str, object] | None,
    source_commit: str,
    now: datetime,
) -> tuple[Finding, Finding, Finding]
```

Validate schema `threadlight-governed-actions-manifest/v1`, assessor version, phase (`pre-deploy` or `post-deploy`), clean matching repository/source commit, recomputed current policy hashes, selected target environment, evidence freshness, manifest hash binding, referenced findings, and summary counts. Aggregate statuses using `must-fix > not-verified > should-fix > pass > not-applicable`. Map findings using `EXPECTED_AGGREGATES`; malformed or unmatched mandatory findings make all applicable aggregates `not-verified`. Add the aggregate findings to existing agent governance, HITL, and supply-chain pillars without rerunning governed-actions detail. Bump production-ready to `0.12.0` and update the three references and SKILL input/output descriptions.

- [ ] **Step 4: Run production-ready tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-production-ready/tests/test_governed_actions_manifest.py skills/threadlight-production-ready/tests/test_version.py -q
```

Expected: PASS with three aggregates, strict stale/source validation, and version `0.12.0`.

- [ ] **Step 5: Commit production-ready integration**

```bash
git add skills/threadlight-production-ready/scripts/production_ready.py skills/threadlight-production-ready/tests/test_governed_actions_manifest.py skills/threadlight-production-ready/tests/test_version.py skills/threadlight-production-ready/SKILL.md skills/threadlight-production-ready/references/02-agent-governance.md skills/threadlight-production-ready/references/08-hitl-governance.md skills/threadlight-production-ready/references/09-supply-chain-security.md
git commit -m "feat: consume governed actions evidence" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 14: Add recommendation-only Threadlight Auto handoffs

**Files:**
- Modify: `skills/threadlight-auto/references/orchestrator.py`
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py`
- Modify: `skills/threadlight-auto/SKILL.md`

- [ ] **Step 1: Write failing recommendation-only tests**

Assert:

```python
def test_auto_recommends_explicit_governed_actions_lifecycle_handoffs(tmp_path):
    decision = decide(make_context(tmp_path, consequential_actions=True))
    assert decision["governed_actions"] == {
        "execution": "manual-explicit",
        "design": (
            "run threadlight-governed-actions --phase design "
            "after threadlight-design"
        ),
        "pre_deploy": (
            "run threadlight-governed-actions --phase pre-deploy before deploy"
        ),
        "post_deploy": (
            "run threadlight-governed-actions --phase post-deploy "
            "against staging only"
        ),
        "manifest": "tests/governed-actions-manifest.json",
    }

def test_auto_never_schedules_governed_actions_or_rollout():
    assert "governed_actions" not in STAGES
    assert all(
        leg.get("skill") != "threadlight-governed-actions"
        for leg in LEG_CONTRACTS.values()
    )
```

Add tests that a schema-valid manifest is summarized, a stale/invalid manifest produces a recommendation to rerun, and no auto output contains scaffold, policy application, production canary, merge, or deploy commands.

- [ ] **Step 2: Run auto tests and confirm the expected failure**

Run:

```bash
python -m pytest skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py -q -k 'governed or version'
```

Expected: FAIL because `governed_actions` recommendation is absent and auto version remains `1.2.0`.

- [ ] **Step 3: Implement explicit recommendations without adding a stage**

Add a `GOVERNED_ACTIONS_HANDOFF` constant with the exact dictionary above. Add `summarize_governed_actions_manifest(path, source_commit)` that validates schema version, source commit, freshness, and summary counts without executing the skill. Include the handoff only when consequential actions are detected or a manifest exists. Do not alter `STAGES` or `LEG_CONTRACTS`; do not invoke scaffold, enforcement, deploy, or post-deploy automatically. Bump auto to `1.3.0` and update SKILL.md with manual ownership.

- [ ] **Step 4: Run auto tests and confirm they pass**

Run:

```bash
python -m pytest skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py -q -k 'governed or version'
```

Expected: PASS with recommendations present and no governed-actions execution stage.

- [ ] **Step 5: Commit recommendation-only integration**

```bash
git add skills/threadlight-auto/references/orchestrator.py skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py skills/threadlight-auto/SKILL.md
git commit -m "feat: recommend governed actions lifecycle" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 15: Publish skill documentation, CI wiring, and release metadata

**Files:**
- Create: `skills/threadlight-governed-actions/SKILL.md`
- Modify: `.github/workflows/python-pytest.yml`
- Modify: `tests/ci/check-test-dirs-wired.test.js`
- Modify: `tests/blueprint/published-surfaces.test.js`
- Modify: `plugin.json`
- Modify: `.github/plugin/marketplace.json`
- Modify: `README.md`
- Modify: `THREADLIGHT.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write failing publication and CI assertions**

Update `published-surfaces.test.js` first to expect:

```javascript
const expectedVersion = "1.13.0";
const expectedSkillCount = 23;
const expectedPipelineSkillCount = 22;
```

Add `threadlight-governed-actions` to the exact expected skill list. Extend `check-test-dirs-wired.test.js` with a fixture proving omission of:

```text
skills/threadlight-governed-actions/tests
```

causes the checker to fail. Add a governed-actions version test in `test_contracts.py` asserting `SKILL.md` declares version `0.1.0` and the CLI reports the same assessor version.

- [ ] **Step 2: Run publication checks and confirm the expected failure**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js tests/ci/check-test-dirs-wired.test.js
python -m pytest skills/threadlight-governed-actions/tests/test_contracts.py -q -k version
```

Expected: FAIL because the skill is unpublished, plugin version is `1.12.0`, the workflow lacks the suite, and SKILL.md is absent.

- [ ] **Step 3: Add exact skill contract and public metadata**

`SKILL.md` frontmatter must declare `name: threadlight-governed-actions`, version `0.1.0`, and trigger phrases for governance evidence, consequential actions, Agent Hooks mediation, approval anti-replay, and GHCP change-plane governance. Document:

- inputs (`specs/SPEC.md` section 8, registries, runtime/middleware, policy/approval/test/workflow files, optional live evidence);
- outputs and default paths;
- `design`, `pre-deploy`, and staging-only `post-deploy`;
- read-only operation, `--emit`, and double-opt-in scaffold;
- exact exit codes;
- the exact 18-ID finding taxonomy from the fixed public contracts;
- operational alerts for repeated fail-closed denials, interceptor timeout/crash, approval replay, output gate failure, audit write failure, evidence staleness, and pin drift;
- privacy prohibition on raw prompt/argument/output/secret evidence;
- Agent Hooks cooperative alpha status, MAF experimental status, conformance-not-certification, provider-hosted limitation, repeated service authorization/idempotency/transaction constraints, and no GHCP internal-loop interception;
- SAFE as design framework, ACS as policy decision runtime/interceptor, Agent Hooks as host/interceptor contract, and ASSERT/evals as offline assurance rather than runtime enforcement.

Add an explicit pytest step to `.github/workflows/python-pytest.yml`:

```yaml
- name: Test threadlight-governed-actions
  run: python -m pytest skills/threadlight-governed-actions/tests -q
```

Bump `plugin.json` and marketplace metadata to `1.13.0`; publish exactly 23 skills. Update README/THREADLIGHT lifecycle diagrams and examples. Add changelog heading `## [1.13.0] - 2026-09-01` recording new skill `0.1.0`, production-ready `0.12.0`, auto `1.3.0`, plugin `1.13.0`, the tested upstream tuple, and the explicit non-ownership of production rollout.

- [ ] **Step 4: Run publication checks and confirm they pass**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js tests/ci/check-test-dirs-wired.test.js
python scripts/ci/check-test-dirs-wired.py
python -m pytest skills/threadlight-governed-actions/tests/test_contracts.py -q -k version
```

Expected: PASS with the new suite registered, 23 skills published, and all version surfaces consistent.

- [ ] **Step 5: Commit publication surfaces**

```bash
git add skills/threadlight-governed-actions/SKILL.md .github/workflows/python-pytest.yml tests/ci/check-test-dirs-wired.test.js tests/blueprint/published-surfaces.test.js plugin.json .github/plugin/marketplace.json README.md THREADLIGHT.md CHANGELOG.md skills/threadlight-governed-actions/tests/test_contracts.py
git commit -m "docs: publish threadlight governed actions" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

### Task 16: Validate acceptance criteria from narrow suites through full repository checks

**Files:**
- Modify only if a validation failure identifies a defect in files already listed in Tasks 1-15

- [ ] **Step 1: Run focused governed-actions unit and golden suites**

Run:

```bash
python -m pytest \
  skills/threadlight-governed-actions/tests/test_contracts.py \
  skills/threadlight-governed-actions/tests/test_inventory.py \
  skills/threadlight-governed-actions/tests/test_maf_adapter.py \
  skills/threadlight-governed-actions/tests/test_mediation.py \
  skills/threadlight-governed-actions/tests/test_probes.py \
  skills/threadlight-governed-actions/tests/test_ghcp.py \
  skills/threadlight-governed-actions/tests/test_alerts.py \
  skills/threadlight-governed-actions/tests/test_render_cli.py \
  skills/threadlight-governed-actions/tests/test_scaffold.py \
  skills/threadlight-governed-actions/tests/test_golden_fixtures.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run both consumer integration suites**

Run:

```bash
python -m pytest \
  skills/threadlight-production-ready/tests/test_governed_actions_manifest.py \
  skills/threadlight-production-ready/tests/test_version.py \
  skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py \
  skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py \
  -q
```

Expected: PASS with aggregate consumption and recommendation-only integration.

- [ ] **Step 3: Run static publication, CI, and placeholder checks**

Run:

```bash
node --test tests/blueprint/published-surfaces.test.js tests/ci/check-test-dirs-wired.test.js
python scripts/ci/check-test-dirs-wired.py
! rg -n 'TBD|TODO|implement later|similar to|fill in' \
  skills/threadlight-governed-actions \
  skills/threadlight-production-ready \
  skills/threadlight-auto
```

Expected: all Node/CI checks PASS and `rg` returns no matches.

- [ ] **Step 4: Run full existing repository validation**

Run:

```bash
python -m pytest -q
node --test tests/**/*.test.js
```

Expected: all existing Python and Node tests PASS; no new test tool is installed.

- [ ] **Step 5: Exercise measurable CLI acceptance criteria in a temporary copy**

Run:

```bash
tmp_dir="$(mktemp -d)"
cp -R skills/threadlight-governed-actions/tests/fixtures/conformant-maf "$tmp_dir/project"
git -C "$tmp_dir/project" init -q
git -C "$tmp_dir/project" config user.name "Governed Actions Test"
git -C "$tmp_dir/project" config user.email "governed-actions@example.invalid"
git -C "$tmp_dir/project" remote add origin https://github.com/example/governed-actions-fixture.git
git -C "$tmp_dir/project" add .
git -C "$tmp_dir/project" commit -q -m "test fixture"
python3 skills/threadlight-governed-actions/scripts/governed_actions.py --target "$tmp_dir/project" --phase design --gate
python3 skills/threadlight-governed-actions/scripts/governed_actions.py --target "$tmp_dir/project" --phase pre-deploy --emit --gate
test -f "$tmp_dir/project/tests/governed-actions-manifest.json"
test -f "$tmp_dir/project/docs/governance/evidence-pack.md"
test -f "$tmp_dir/project/tests/governed-actions-apply-plan.json"
python -m json.tool "$tmp_dir/project/tests/governed-actions-manifest.json" >/dev/null
python -m json.tool "$tmp_dir/project/tests/governed-actions-apply-plan.json" >/dev/null
rm -rf "$tmp_dir"
```

Expected: both assessments exit 0, exactly three artifacts exist, and both JSON files parse. The temporary directory variable is resolved by `mktemp`; removal targets only that resolved directory.

- [ ] **Step 6: Review the final diff for scope and commit validation fixes**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: only files mapped in this plan are changed; no generated evidence remains in fixture roots; `git diff --check` reports no whitespace errors.

If validation required corrections, commit only those corrections:

```bash
git add skills/threadlight-governed-actions skills/threadlight-production-ready skills/threadlight-auto .github/workflows/python-pytest.yml tests/ci/check-test-dirs-wired.test.js tests/blueprint/published-surfaces.test.js plugin.json .github/plugin/marketplace.json README.md THREADLIGHT.md CHANGELOG.md
git commit -m "fix: complete governed actions acceptance" -m "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 73f7f4df-ded5-4cc4-9a8d-8744c5a9f902"
```

If no correction was needed, do not create an empty commit.

## Inline self-review against the approved design

### Requirement coverage

| Approved design requirement | Implemented by |
| --- | --- |
| Standalone assessor and customer-facing Governance Evidence Pack | Tasks 9, 10, 15 |
| Runtime and GHCP planes kept separate | Tasks 4-8, schemas in Task 1 |
| SPEC section 8, registry, runtime, policy, approval, test, workflow, and optional live inputs | Tasks 2-8, 10 |
| Manifest fields: versions, source, pins, policy hashes, inventory, paths, conformance, findings, evidence, freshness, residual risk | Tasks 1, 3, 9 |
| Consequence classes, aliases, and all execution modes | Tasks 1, 2, 4 |
| Deterministic application-path deny, transform, crash, timeout, malformed verdict probes | Task 5 |
| Approval replay/mutation, output buffering/streaming, payload-free audit | Task 6 |
| CTK claim insufficient without application probes | Tasks 3, 5, 9 |
| PR-only, CODEOWNERS, branch/required checks, CTK/eval CI, SHA pins, OIDC/WIF, privilege, identity separation | Tasks 7, 8 |
| GHCP internal loop not intercepted | Tasks 7, 15 |
| Design, pre-deploy, staging-only post-deploy lifecycle | Tasks 8, 10 |
| Missing permissions/live evidence is `not-verified` | Tasks 7, 8, 10 |
| Unmediated consequential path is must-fix | Task 4 |
| Provider-hosted side effect unsupported absent equivalent proof | Task 4 |
| Explicit-only bounded scaffold with no policy invention | Task 11 |
| Production-ready consumes aggregates without duplication | Task 13 |
| Auto recommendation-only; no automatic rollout | Task 14 |
| Eight required fixtures and deterministic golden outputs | Task 12 |
| Complete upstream tuple and exact drift policy | Task 3 |
| Agent Hooks trust model, conformance non-certification, repeated service constraints | Tasks 3, 4, 15 |
| Finding IDs, CLI exits, errors, architecture/data flow, alerts, security/privacy, scope/non-goals | Fixed contracts; Tasks 1, 4-10, 15 |
| MAF-first with adapter contract for later frameworks | Task 3 |
| Implementation phases and measurable acceptance criteria | Tasks 1-16 |
| Version, changelog, docs, plugin metadata, and CI wiring | Task 15 |

### Rejected alternatives preserved

The implementation documentation in Task 15 must record these decisions:

1. Extending `threadlight-production-ready` was rejected because its broad posture assessment should consume aggregate evidence, not own runtime conformance probes.
2. Splitting the feature across `threadlight-safe-check` and `threadlight-hitl-patterns` was rejected because it fragments one customer evidence chain and pass/fail matrix.
3. A cross-framework-first implementation was rejected because alpha host integrations multiply unstable surface area; MAF is first, behind the explicit `RuntimeAdapter` contract.

### Scope and consistency checks

- Every code-producing task starts with a failing test, names an exact command and expected failure, gives concrete types/rules/content, reruns the exact targeted test, and commits a self-contained change.
- Public names remain consistent: `ActionRecord`, `PathRecord`, `ProbeResult`, `Finding`, `AssessmentOptions`, `AssessmentResult`, `RuntimeAdapter`, `build_action_inventory`, `build_mediation_graph`, `run_application_probe`, `assess_change_plane`, `assess_alerts`, `build_manifest`, `build_apply_plan`, and `assess`.
- Status values, verdict values, manifest/apply-plan v1 schemas, assessor/skill version `0.1.0`, production-ready `0.12.0`, auto `1.3.0`, and plugin `1.13.0` are used consistently.
- No feature task gives `threadlight-auto` authority to scaffold, alter policy, deploy, merge, run a production canary, or roll out enforcement.
- No task adds a business threshold, authorization rule, approver, or customer identity.
- No task treats SAFE, ASSERT/evals, CTK, documentation, or post-action observation as runtime pre-action enforcement.
- No task claims Agent Hooks is a security boundary or that conformance is certification.
- The implementation remains focused on one skill plus two narrow consumers and required publication surfaces; it does not refactor unrelated Threadlight skills.

### Placeholder and ambiguity scan

The plan contains no implementation placeholders. Commands, paths, versions, schema fields, status/exit semantics, finding IDs, fixtures, probe cases, adapter methods, scaffold files, integration findings, and release numbers are fixed. Customer-owned policy values are intentionally empty and produce an explicit customer-decision remediation rather than an inferred value.
