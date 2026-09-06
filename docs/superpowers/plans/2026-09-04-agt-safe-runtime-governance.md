# AGT + SAFE Runtime Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate Microsoft Foundry agents with selective, real runtime
governance: SAFE requirements expressed as ACS policy, enforced through MAF
Agent Hooks or a governed ACA tool gateway, and proven on the deployed path.

**Architecture:** A shared governance contract normalizes agent/tool bindings
and produces `specs/governance-manifest.json`. MAF agents install one Agent
Hooks bundle as the outermost middleware and evaluate signed ACS/Rego bundles
locally; GHCP agents can mark remote MCP actions `action-governed` only when the
ACA gateway is the exclusive route to the protected effect. An ACA control
plane owns immutable bundle metadata, approvals, and receipts, while lifecycle
gates validate selected bindings instead of assigning a whole-agent
`governed` label.

**Tech Stack:** Python 3.12+, Microsoft Agent Framework
`agent-framework-core~=1.14.0`, `agent-framework-foundry~=1.11.0`, Agent Hooks
SDK `0.1.0a5`, AGT `5.0.0`, ACS `0.3.1b0`, OPA `1.18.2`, FastAPI, FastMCP,
Azure Container Apps, managed identity, Azure Blob Storage, Cosmos DB, Key
Vault cryptography, Bicep, pytest/unittest, Azure CLI/azd.

---

## File map

**2026-09-06 status:** signed remote bootstrap implementation adds
`control-plane/bootstrap.py`, `control-plane/hosted_lifecycle.py` and
`scripts/ci/hosted_bootstrap.py`, gates both generated server entrypoints, and
carries the signed chain into current-readiness. The local RED/GREEN suites do
not close Task14/15. Native assets now use bounded authenticated Blob delivery,
frozen policy/endpoint constraints, and the real platform credential. Both native
protocols support a no-inference bootstrap check. The protected workflow accepts
only the explicit SDK-resume contract, not legacy register/bind/start.
Actual AgentIdentity access to the native Cosmos producer and hosted controller
reachability still require live evidence. No live evidence or business-write
acceptance is inherited from older artifacts.

### Shared contracts

- Create `skills/_shared/governance.py` — typed normalization and manifest
  validation shared by producers and consumers.
- Create `skills/_shared/governance-manifest.schema.json` — canonical v1
  machine contract.
- Create `skills/_shared/governance-upstream-pin.json` — exact AGT/ACS/Agent
  Hooks/MAF/OPA/SAFE tuple.
- Create `skills/_shared/tests/test_governance.py` — contract and migration
  tests.

### Design and generation contract

- Modify `skills/threadlight-design/SKILL.md` — selective governance interview
  and output requirements.
- Modify `skills/threadlight-design/references/speckit-template.md` —
  `governance{}` schema.
- Create `skills/threadlight-design/references/governance-contract.schema.json`.
- Create `skills/threadlight-design/tests/test_governance_contract.py`.
- Modify `skills/threadlight-design/references/runtime-policy.json` — runtime
  compatibility facts, without silently switching frameworks.

### Runtime governance producer

- Rewrite `skills/threadlight-govern/scripts/govern_check.py` — producer and
  validator for binding-level evidence.
- Create `skills/threadlight-govern/scripts/policy_bundle.py` — canonical
  bundle build, digest, signature metadata, and verification.
- Create `skills/threadlight-govern/references/runtime/governance_provider.py`
  — provider interface.
- Create `skills/threadlight-govern/references/runtime/maf_agent_hooks_acs.py`
  — MAF Agent Hooks + ACS adapter.
- Create `skills/threadlight-govern/references/runtime/evidence.py` — snapshot,
  receipt, and health records.
- Replace `skills/threadlight-govern/references/policy-templates/*.policy.yaml`
  with native ACS manifest/Rego templates.
- Create `skills/threadlight-govern/references/policy-templates/safe.rego`.
- Create `skills/threadlight-govern/references/policy-templates/manifest.yaml`.
- Expand `skills/threadlight-govern/tests/`.

### ACA services

- Create `skills/threadlight-govern/references/control-plane/app.py`.
- Create `skills/threadlight-govern/references/control-plane/models.py`.
- Create `skills/threadlight-govern/references/control-plane/storage.py`.
- Create `skills/threadlight-govern/references/control-plane/pyproject.toml`.
- Create `skills/threadlight-govern/references/control-plane/Dockerfile`.
- Create `skills/threadlight-govern/references/gateway/server.py`.
- Create `skills/threadlight-govern/references/gateway/dispatcher.py`.
- Create `skills/threadlight-govern/references/gateway/receipts.py`.
- Create `skills/threadlight-govern/references/gateway/pyproject.toml`.
- Create `skills/threadlight-govern/references/gateway/Dockerfile`.
- Create `skills/threadlight-govern/references/infra/governance.bicep`.
- Create `skills/threadlight-govern/tests/test_control_plane.py`.
- Create `skills/threadlight-govern/tests/test_gateway.py`.

### Existing verifier and lifecycle consumers

- Modify `skills/threadlight-governed-actions/scripts/contracts.py`.
- Modify `skills/threadlight-governed-actions/scripts/inventory.py`.
- Modify `skills/threadlight-governed-actions/scripts/mediation.py`.
- Modify `skills/threadlight-governed-actions/scripts/probes.py`.
- Modify `skills/threadlight-governed-actions/scripts/governed_actions.py`.
- Modify `skills/threadlight-governed-actions/scripts/render.py`.
- Replace `skills/threadlight-governed-actions/references/upstream-pin.json`
  with a consumer reference to the shared pin.
- Update its tests and goldens.
- Modify `skills/threadlight-deploy/SKILL.md` and add governance reference
  templates.
- Modify `skills/threadlight-safe-check/scripts/safe_check.py` and tests.
- Modify `skills/threadlight-production-ready/scripts/production_ready.py`,
  `evidence_gate.py`, `ai_act_evidence.py`, tests, recipes, and pillar docs.
- Modify `skills/threadlight-auto/references/orchestrator.py`, `SKILL.md`, and
  tests.

### Reference pilot and public docs

- Migrate `examples/returns-triage-governed/` to the MAF SAFE path.
- Modify `README.md`, `docs/IDEA-TO-PRODUCTION-WORKBOOK.md`,
  `docs/production-readiness.md`, `docs/index.html`, `docs/production.html`,
  and `docs/assets/process-library.json`.
- Modify `.github/workflows/python-pytest.yml` and
  `.github/workflows/threadlight-e2e-foundry.yml`.

---

### Task 1: Establish the shared governance contract and upstream pin

**Files:**
- Create: `skills/_shared/governance.py`
- Create: `skills/_shared/governance-manifest.schema.json`
- Create: `skills/_shared/governance-upstream-pin.json`
- Create: `skills/_shared/tests/test_governance.py`
- Modify: `skills/_shared/tests/test_manifest.py`

- [ ] **Step 1: Write failing normalization and schema tests**

```python
def test_string_tool_normalizes_without_claiming_governance():
    tool = normalize_tool("returns_get_case")
    assert tool == {
        "id": "returns_get_case",
        "consequence": "unknown",
        "policy_binding": None,
        "enforcement_path": "none",
        "intervention_points": [],
    }


def test_bound_tool_requires_supported_runtime_path():
    with pytest.raises(GovernanceContractError, match="local-agent-hooks"):
        normalize_tool(
            {
                "id": "returns_apply_decision",
                "consequence": "write",
                "policy_binding": "returns-write-v1",
                "enforcement_path": "local-agent-hooks",
            },
            runtime="github-copilot-sdk",
        )


def test_manifest_rejects_coverage_mismatch():
    manifest = valid_manifest()
    manifest["coverage"]["tools_enforced"] = 99
    with pytest.raises(GovernanceContractError, match="coverage"):
        validate_governance_manifest(manifest)
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
python3 -m pytest skills/_shared/tests/test_governance.py -q
```

Expected: collection fails because `skills/_shared/governance.py` does not
exist.

- [ ] **Step 3: Implement the shared types and validator**

Create immutable enums and normalization:

```python
GOVERNANCE_MODES = frozenset({"off", "selective", "comprehensive"})
BINDING_STATUSES = frozenset(
    {"enforced", "observed", "unbound", "unsupported", "unverified", "bypassable"}
)
ENFORCEMENT_PATHS = frozenset(
    {"none", "local-agent-hooks", "governed-tool-gateway"}
)
CONSEQUENCES = frozenset(
    {"read", "write", "external-egress", "irreversible", "unknown"}
)


class GovernanceContractError(ValueError):
    pass


def normalize_tool(raw, runtime=None):
    if isinstance(raw, str):
        return {
            "id": raw,
            "consequence": "unknown",
            "policy_binding": None,
            "enforcement_path": "none",
            "intervention_points": [],
        }
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise GovernanceContractError("tool must be a string or mapping with id")
    result = {
        "id": raw["id"],
        "consequence": raw.get("consequence", "unknown"),
        "policy_binding": raw.get("policy_binding"),
        "enforcement_path": raw.get("enforcement_path", "none"),
        "intervention_points": list(raw.get("intervention_points", [])),
    }
    if (
        runtime == "github-copilot-sdk"
        and result["enforcement_path"] == "local-agent-hooks"
    ):
        raise GovernanceContractError(
            "local-agent-hooks is unsupported for github-copilot-sdk"
        )
    return result
```

Add validators that enforce exact top-level keys, binding status vocabulary,
binding/coverage count agreement, evidence-reference resolution, exact package
versions, and RFC3339 timestamps.

- [ ] **Step 4: Write the exact shared pin**

Use:

```json
{
  "schema": "threadlight-governance-upstream-pin/v1",
  "agt": {"distribution": "agent-governance-toolkit", "version": "5.0.0"},
  "acs": {"distribution": "agent-control-specification", "version": "0.3.1b0"},
  "agent_hooks": {"distribution": "agent-hooks-sdk", "version": "0.1.0a5"},
  "maf": {
    "agent-framework-core": "1.14.0",
    "agent-framework-foundry": "1.11.0",
    "agent-framework-foundry-hosting": "1.0.0b260813"
  },
  "opa": {
    "version": "1.18.2",
    "linux_amd64_static_sha256": "9903e5125ac281104f2c4b7371d10cc3b74a98933743fcbfc174f9bf0ab20de8"
  },
  "safe_reference": {
    "repository": "placerda/safe-agent-on-foundry",
    "commit": "f9d2a55954d447554907686d59135489c393e826"
  }
}
```

- [ ] **Step 5: Run shared tests**

Run:

```bash
python3 -m pytest skills/_shared/tests/test_governance.py \
  skills/_shared/tests/test_manifest.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add skills/_shared
git commit -m "feat: define runtime governance contract"
```

---

### Task 2: Add selective governance to the design contract

**Files:**
- Modify: `skills/threadlight-design/SKILL.md`
- Modify: `skills/threadlight-design/references/speckit-template.md`
- Modify: `skills/threadlight-design/references/runtime-policy.json`
- Create: `skills/threadlight-design/references/governance-contract.schema.json`
- Create: `skills/threadlight-design/tests/test_governance_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
def test_speckit_declares_governance_modes_and_bindings():
    text = SPECKIT.read_text(encoding="utf-8")
    assert "mode: off | selective | comprehensive" in text
    assert "policy_binding:" in text
    assert "enforcement_path:" in text


def test_runtime_policy_does_not_silently_switch_ghcp_to_maf():
    policy = json.loads(RUNTIME_POLICY.read_text())
    assert policy["governance_compatibility"]["github-copilot-sdk"] == [
        "none",
        "governed-tool-gateway",
    ]
    assert policy["governance_compatibility"]["microsoft-agent-framework"] == [
        "none",
        "local-agent-hooks",
        "governed-tool-gateway",
    ]
```

- [ ] **Step 2: Run the test and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-design/tests/test_governance_contract.py -q
```

Expected: missing governance block and compatibility map.

- [ ] **Step 3: Add the design interview and schema**

Add one question at a time:

1. choose `off`, `selective`, or `comprehensive`;
2. classify each tool consequence;
3. choose bindings and SAFE principles;
4. record explicit unbound acceptance for unknown/consequential production
   tools;
5. report runtime incompatibility without changing the selected framework.

The JSON Schema must require:

```json
{
  "governance": {
    "mode": "selective",
    "bindings": [
      {
        "tool_id": "returns_apply_decision",
        "policy_binding": "returns-write-v1",
        "enforcement_path": "local-agent-hooks",
        "intervention_points": ["pre_tool_call", "post_tool_call"],
        "safe_principles": ["scope", "anchored_decisions", "flow_integrity"]
      }
    ]
  }
}
```

- [ ] **Step 4: Add design negative cases**

Assert that:

- GHCP plus `local-agent-hooks` is rejected;
- a legacy string tool parses but remains `unknown`;
- comprehensive mode rejects an unsupported provider-hosted consequential tool;
- selective mode permits `policy_binding: none`;
- an unbound write in a production-bound profile requires owner,
  justification, review date, and expiry.

- [ ] **Step 5: Run design tests**

```bash
python3 -m pytest skills/threadlight-design/tests -q
```

Expected: all design tests pass.

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-design
git commit -m "feat: add selective governance design contract"
```

---

### Task 3: Fix destructive and incomplete governed-actions probes

**Files:**
- Modify: `skills/threadlight-governed-actions/scripts/probes.py`
- Modify: `skills/threadlight-governed-actions/scripts/governed_actions.py`
- Modify: `skills/threadlight-governed-actions/tests/test_probes.py`
- Modify: `skills/threadlight-governed-actions/tests/test_render_cli.py`

- [ ] **Step 1: Add a RED test for target-file deletion**

```python
def test_output_probe_never_deletes_declared_repository_file(tmp_path):
    root = copy_fixture(tmp_path, "conformant-maf")
    victim = root / ".github/workflows/governed-actions.yml"
    contract = load_json(root / "governance/probe-contract.json")
    contract["observation_ledger"] = ".github/workflows/governed-actions.yml"
    write_json(root / "governance/probe-contract.json", contract)
    before = victim.read_bytes()

    result = run_output_probe(root, "deny")

    assert result.status == "pass"
    assert victim.read_bytes() == before
```

- [ ] **Step 2: Run the test and confirm the file is deleted**

```bash
python3 -m pytest \
  skills/threadlight-governed-actions/tests/test_probes.py::test_output_probe_never_deletes_declared_repository_file -q
```

Expected: FAIL because the victim no longer exists.

- [ ] **Step 3: Use a private output ledger**

Replace direct use of the declared ledger path with:

```python
with tempfile.TemporaryDirectory(
    prefix=".threadlight-output-probe-",
    dir=root_path / "governance",
) as private_dir:
    ledger_path = Path(private_dir) / "observation.jsonl"
    _dispatch_task6_child(
        root_path,
        str(contract["dispatch"]),
        str(contract["audit_sink"]),
        (verdict, str(ledger_path)),
    )
    events = _read_ledger_events(ledger_path)
```

Keep the declared path only as provenance metadata.

- [ ] **Step 4: Add RED tests for unprobed bound actions**

```python
def test_bound_action_missing_from_probe_contract_fails_gate(tmp_path):
    root = copy_fixture(tmp_path, "conformant-maf")
    add_bound_irreversible_action(root, "payments.wire")
    result = assess(predeploy_options(root))
    findings = {(f.finding_id, f.status, f.affected_actions) for f in result.findings}
    assert ("ENF-001", "not-verified", ("payments.wire",)) in findings
    assert exit_code(result, gate=True) == 1
```

- [ ] **Step 5: Reconcile inventory against probe scope**

After loading probes, calculate:

```python
bound_actions = {
    action.action_id
    for action in inv.actions
    if action.policy_ids
}
probed_actions = {
    probe.action_id for probe in probe_results if probe.action_id is not None
}
for action_id in sorted(bound_actions - probed_actions):
    findings.append(
        Finding(
            finding_id="ENF-001",
            status="not-verified",
            phase=phase,
            plane="runtime",
            reason_code="bound-action-not-probed",
            summary=f"Bound action {action_id} has no application-path probe.",
            details="A selected policy binding has no deny/transform/failure proof.",
            affected_actions=(action_id,),
        )
    )
```

Add equivalent coverage checks for approval, output, and audit requirements.

- [ ] **Step 6: Run focused probe and CLI tests**

```bash
python3 -m pytest \
  skills/threadlight-governed-actions/tests/test_probes.py \
  skills/threadlight-governed-actions/tests/test_render_cli.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-governed-actions/scripts \
  skills/threadlight-governed-actions/tests
git commit -m "fix: make governance probes read-only and complete"
```

---

### Task 4: Replace AST-marker passes with executed-path evidence

**Files:**
- Modify: `skills/threadlight-governed-actions/scripts/contracts.py`
- Modify: `skills/threadlight-governed-actions/scripts/mediation.py`
- Modify: `skills/threadlight-governed-actions/scripts/render.py`
- Modify: `skills/threadlight-governed-actions/tests/test_mediation.py`
- Modify: `skills/threadlight-governed-actions/tests/test_static_source_evidence.py`

- [ ] **Step 1: Write the decoy regression test**

```python
def test_unreachable_convention_named_function_cannot_pass_mediation(tmp_path):
    root = copy_fixture(tmp_path, "unmediated-background")
    rename_real_bypass_functions(root)
    append_unreachable_mediated_decoys(root)

    graph = build_mediation_graph(
        root,
        build_action_inventory(root).actions,
        MAFAdapter(),
    )

    refund_paths = [p for p in graph.paths if p.action_id == "payments.refund"]
    assert all(path.status != "pass" for path in refund_paths)
```

- [ ] **Step 2: Run and observe the false passes**

```bash
python3 -m pytest \
  skills/threadlight-governed-actions/tests/test_mediation.py::test_unreachable_convention_named_function_cannot_pass_mediation -q
```

Expected: FAIL with `pass` paths.

- [ ] **Step 3: Split discovery from proof**

Change `PathRecord` to carry:

```python
@dataclass(frozen=True)
class PathRecord:
    path_id: str
    action_id: str
    mode: str
    nodes: Tuple[str, ...]
    pre_action_seam: Optional[str]
    equivalent_control_ref: Optional[str]
    discovered: bool
    executed: bool
    status: Literal[
        "pass", "must-fix", "should-fix", "not-verified", "not-applicable"
    ]
    evidence_refs: Tuple[str, ...]
```

Static AST can set `discovered=True`; only a correlated application probe or
live trace can set `executed=True`. `_recompute_coverage` returns `pass` only
when both are true and the executed receipt precedes the tool receipt.

- [ ] **Step 4: Bind probe path IDs to mediation records**

Require each application probe to name the real `path_id`. If it cannot, keep
the path `not-verified`. A direct downstream dispatch without a matching
pre-action decision is `must-fix`.

- [ ] **Step 5: Run mediation and golden tests**

```bash
python3 -m pytest \
  skills/threadlight-governed-actions/tests/test_mediation.py \
  skills/threadlight-governed-actions/tests/test_golden_fixtures.py -q
```

Expected: all pass after regenerating goldens through the normal CLI.

- [ ] **Step 6: Commit**

```bash
git add skills/threadlight-governed-actions
git commit -m "fix: require executed mediation evidence"
```

---

### Task 5: Complete approval, output, audit, and live-target semantics

**Files:**
- Modify: `skills/threadlight-governed-actions/scripts/probes.py`
- Modify: `skills/threadlight-governed-actions/scripts/governed_actions.py`
- Modify: `skills/threadlight-governed-actions/scripts/contracts.py`
- Modify: `skills/threadlight-governed-actions/tests/test_probes.py`
- Modify: `skills/threadlight-governed-actions/tests/test_probe_evidence_binding.py`
- Modify: `skills/threadlight-governed-actions/tests/test_render_cli.py`

- [ ] **Step 1: Add approval positive-control tests**

```python
def test_approval_sequence_requires_second_valid_fresh_nonce(tmp_path):
    root = approval_store_that_rejects_everything_after_first_use(tmp_path)
    results = run_approval_probe_sequence(root, binding(), NOW)
    assert any(
        r.status == "must-fix"
        and r.reason_code == "APR-001"
        and r.observed == "fresh_nonce_rejected"
        for r in results
    )
```

Also add a fresh nonce with expired `expires_at` that must be rejected.

- [ ] **Step 2: Extend the sequence**

Drive:

```python
attempts = (
    original_binding,
    original_binding,
    *mutated_same_nonce_bindings,
    replace(original_binding, nonce=f"{original_binding.nonce}-fresh"),
    replace(
        original_binding,
        nonce=f"{original_binding.nonce}-expired",
        expires_at="2000-01-01T00:00:00Z",
    ),
)
```

Require accepted, rejected, rejected mutations, accepted fresh nonce, rejected
expired nonce.

- [ ] **Step 3: Make output and audit passes evidence-bearing**

Persist canonical payload-free records to private files, hash them before
cleanup, and populate `ProbeEvidence`. Audit validation requires:

```python
REQUIRED_AUDIT_KEYS = frozenset(
    {
        "audit_id",
        "correlation_id",
        "decision",
        "action_hash",
        "policy_hash",
        "delivery_status",
    }
)
```

Reject missing keys and any raw prompt, arguments, output, token, secret, or
credential field.

- [ ] **Step 4: Validate staging target identity**

Replace `staging=bool(resource_group)` with a function that rejects:

```python
PRODUCTION_MARKERS = re.compile(r"(^|[-_])(prod|production)([-_]|$)", re.I)
```

and verifies subscription/resource-group equality against the selected azd
environment. A non-empty string alone is never sufficient.

- [ ] **Step 5: Add deployed revision fields**

Extend `AssessmentOptions` with `agent_name`, `agent_version`,
`image_digest`, `policy_digest`, and `environment`. Post-deploy evidence must
bind all five and may not reuse local-only probe evidence as live proof.

- [ ] **Step 6: Run the verifier suite**

```bash
python3 -m pytest skills/threadlight-governed-actions/tests -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-governed-actions
git commit -m "fix: harden governance evidence semantics"
```

---

### Task 6: Build native ACS policy bundles and the new manifest producer

**Files:**
- Modify: `skills/threadlight-govern/scripts/govern_check.py`
- Create: `skills/threadlight-govern/scripts/policy_bundle.py`
- Create: `skills/threadlight-govern/references/policy-templates/manifest.yaml`
- Create: `skills/threadlight-govern/references/policy-templates/safe.rego`
- Delete: `skills/threadlight-govern/references/policy-templates/default.policy.yaml`
- Delete: `skills/threadlight-govern/references/policy-templates/hitl.policy.yaml`
- Delete: `skills/threadlight-govern/references/policy-templates/pii-deny.policy.yaml`
- Modify: `skills/threadlight-govern/tests/test_govern_check.py`
- Create: `skills/threadlight-govern/tests/test_policy_bundle.py`

- [ ] **Step 1: Write RED tests against the real ACS loader**

```python
def test_generated_bundle_loads_in_real_acs_runtime(tmp_path):
    bundle = build_bundle(
        source=TEMPLATES,
        destination=tmp_path / "bundle",
        policy_id="threadlight-safe",
        version="1.0.0",
    )
    control = AgentControl.from_path(str(bundle.manifest_path))
    assert control is not None


def test_unwired_fixture_cannot_emit_enforced(tmp_path):
    result = evaluate(FIXTURES / "sample-wired")
    assert result["coverage"]["tools_enforced"] == 0
    assert result["deployment_status"] != "enforced"
```

Mark the real-runtime test with a dependency marker used by the exact-pin venv.

- [ ] **Step 2: Run tests and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-govern/tests/test_policy_bundle.py \
  skills/threadlight-govern/tests/test_govern_check.py -q
```

Expected: old v4 templates cannot load and the old fixture reports governed.

- [ ] **Step 3: Implement canonical bundle creation**

`build_bundle` must:

1. validate the ACS manifest;
2. normalize and hash every file;
3. reject symlinks and path traversal;
4. write `bundle.json` with policy ID/version/files/digests;
5. write atomically;
6. never sign locally with an embedded private key.

Expose:

```python
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from agent_control_specification import validate_manifest


@dataclass(frozen=True)
class PolicyBundle:
    root: Path
    manifest_path: Path
    bundle_digest: str
    files: tuple[dict[str, str], ...]


def build_bundle(
    *,
    source: Path,
    destination: Path,
    policy_id: str,
    version: str,
) -> PolicyBundle:
    source = source.resolve()
    destination = destination.resolve()
    manifest_path = source / "manifest.yaml"
    validate_manifest(manifest_path.read_text(encoding="utf-8"))

    entries = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"policy bundle cannot contain symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"path": relative, "sha256": f"sha256:{digest}"})

    metadata = {
        "schema": "threadlight-policy-bundle/v1",
        "policy_id": policy_id,
        "version": version,
        "files": entries,
    }
    canonical = json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    bundle_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()

    if destination.exists():
        raise FileExistsError(f"policy bundle destination exists: {destination}")
    shutil.copytree(source, destination)
    (destination / "bundle.json").write_text(
        json.dumps(
            {**metadata, "bundle_digest": bundle_digest},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return PolicyBundle(
        root=destination,
        manifest_path=destination / "manifest.yaml",
        bundle_digest=bundle_digest,
        files=tuple(entries),
    )
```

- [ ] **Step 4: Author native SAFE policy inputs**

Use a valid ACS manifest with `pre_tool_call`, `post_tool_call`, and `output`.
The Rego policy must read:

- `input.tool.name`;
- `input.policy_target.value`;
- host-owned `input.snapshot.safe.evidence`;
- host-owned `input.snapshot.safe.escalations`.

No retired `conditions[]` AGT v4 policy remains.

- [ ] **Step 5: Rewrite the CLI output**

Emit `threadlight-governance-manifest/v1` using the shared validator. Remove
`governed|partial|ungoverned`. An offline run may emit `observed` or
`unverified`, never `enforced`.

- [ ] **Step 6: Run govern tests in both base and exact-pin environments**

```bash
python3 -m pytest skills/threadlight-govern/tests -q
python3 scripts/ci/run-governance-pin-tests.py
```

Expected: base tests pass; pin runner creates an isolated venv and prints
`GOVERNANCE_RUNTIME_CONTRACT=PASS`.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-govern skills/_shared
git commit -m "feat: produce native ACS governance bundles"
```

---

### Task 7: Implement the MAF Agent Hooks + ACS provider

**Files:**
- Create: `skills/threadlight-govern/references/runtime/governance_provider.py`
- Create: `skills/threadlight-govern/references/runtime/maf_agent_hooks_acs.py`
- Create: `skills/threadlight-govern/references/runtime/evidence.py`
- Create: `skills/threadlight-govern/tests/test_runtime_provider.py`
- Create: `skills/threadlight-govern/tests/test_agent_hooks_ctk.py`

- [ ] **Step 1: Write RED provider tests**

```python
@pytest.mark.asyncio
async def test_bound_deny_never_calls_tool():
    calls = 0

    async def tool():
        nonlocal calls
        calls += 1
        return "executed"

    provider = provider_for(binding="deny-delete", mode="enforce")
    result = await provider.invoke_tool("delete_account", {}, tool)

    assert result.status == "blocked_by_policy"
    assert calls == 0


@pytest.mark.asyncio
async def test_unbound_tool_bypasses_policy_and_executes():
    calls = 0

    async def tool():
        nonlocal calls
        calls += 1
        return "ok"

    provider = provider_for(binding=None, mode="enforce")
    assert await provider.invoke_tool("safe_lookup", {}, tool) == "ok"
    assert calls == 1
```

- [ ] **Step 2: Run and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-govern/tests/test_runtime_provider.py -q
```

Expected: missing provider modules.

- [ ] **Step 3: Define the provider boundary**

```python
class GovernanceProvider(Protocol):
    def middleware(self) -> object:
        raise NotImplementedError

    def health(self) -> Mapping[str, object]:
        raise NotImplementedError

    def policy_digest(self) -> str:
        raise NotImplementedError
```

Implement `AcsInterceptor.intercept(context)`:

1. resolve the selected binding;
2. return `ALLOW` immediately when unbound;
3. build a host-owned ACS snapshot;
4. evaluate ACS;
5. map allow/deny/escalate/transform to Agent Hooks verdicts;
6. sanitize exceptions to stable reason codes;
7. record only payload-free evidence.

- [ ] **Step 4: Install one outermost bundle**

```python
hooks = create_agent_hooks_middleware(
    {"acs": AcsInterceptor(control, bindings, evidence)},
    mode=environment_mode,
    resolver=approval_resolver,
    record_sink=evidence.record,
)
agent = Agent(
    client=client,
    instructions=instructions,
    tools=tools,
    middleware=[hooks, *application_middleware],
    context_providers=context_providers,
    default_options={"store": False},
)
```

Reject zero interceptors, multiple bundles, or any middleware preceding the
bundle.

- [ ] **Step 5: Add binding-scoped failure tests**

Cover engine exception, timeout, malformed verdict, unavailable approval,
transform write-back, output buffering, nested agents, and stable
governed-unavailable tool results.

- [ ] **Step 6: Run Agent Hooks CTK against the production adapter**

```bash
python3 -m pytest skills/threadlight-govern/tests/test_agent_hooks_ctk.py \
  --agent-hooks-harness=threadlight_govern.maf_ctk:Harness -q
```

Expected: 100% of declared vectors pass; skips match undeclared surfaces only.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-govern
git commit -m "feat: enforce ACS through MAF Agent Hooks"
```

---

### Task 8: Implement the ACA governance control plane

**Files:**
- Create: `skills/threadlight-govern/references/control-plane/app.py`
- Create: `skills/threadlight-govern/references/control-plane/models.py`
- Create: `skills/threadlight-govern/references/control-plane/storage.py`
- Create: `skills/threadlight-govern/references/control-plane/pyproject.toml`
- Create: `skills/threadlight-govern/references/control-plane/Dockerfile`
- Create: `skills/threadlight-govern/tests/test_control_plane.py`

- [ ] **Step 1: Write RED API tests**

```python
def test_bundle_lookup_returns_immutable_digest(client, bundle_store):
    bundle_store.put(bundle_record())
    response = client.get("/bundles/returns-safe/1.2.0")
    assert response.status_code == 200
    assert response.json()["digest"] == "sha256:" + "a" * 64


def test_approval_replay_is_rejected(client, approval_store):
    request = approval_request(nonce="approval-1")
    assert client.post("/approvals/resolve", json=request).json()["permit"] is True
    replay = client.post("/approvals/resolve", json=request)
    assert replay.status_code == 409


def test_receipt_requires_workload_identity(client_without_identity):
    assert client_without_identity.post("/receipts", json=receipt()).status_code == 401


def test_tampered_bundle_signature_is_rejected(client, bundle_store):
    bundle_store.put(bundle_record(signature_verified=False))
    response = client.get("/bundles/returns-safe/1.2.0")
    assert response.status_code == 409
```

- [ ] **Step 2: Run and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-govern/tests/test_control_plane.py -q
```

Expected: missing control-plane modules.

- [ ] **Step 3: Implement bounded models**

Use Pydantic models with:

```python
class ApprovalRequest(BaseModel):
    nonce: str
    action_hash: str
    policy_digest: str
    requesting_subject: str
    approving_subject: str
    approving_role: str
    tenant_id: str
    expires_at: datetime


class DecisionReceipt(BaseModel):
    receipt_id: str
    correlation_id: str
    action_id: str
    action_hash: str
    policy_digest: str
    decision: Literal["allow", "deny", "escalate", "transform"]
    reason_code: str
    agent_version: str
    image_digest: str
    recorded_at: datetime
```

Reject unknown fields and raw prompt/argument/output fields.

- [ ] **Step 4: Implement storage adapters**

- immutable bundle metadata in Blob;
- approval nonce records and receipts in Cosmos;
- bundle digest signing and verification through a Key Vault key;
- managed identity via `DefaultAzureCredential`;
- optimistic concurrency for one-time approval;
- no shared keys or connection strings.

Tests use in-memory stores implementing the same protocols.

The signing boundary is:

```python
class BundleSigner(Protocol):
    async def sign(self, digest: bytes) -> bytes:
        raise NotImplementedError

    async def verify(self, digest: bytes, signature: bytes) -> bool:
        raise NotImplementedError
```

The production implementation delegates both operations to
`azure.keyvault.keys.crypto.CryptographyClient`; no private key bytes enter the
container or repository.

- [ ] **Step 5: Implement FastAPI endpoints**

Expose only:

- `GET /health`;
- `GET /bundles/{policy_id}/{version}`;
- `POST /approvals/resolve`;
- `POST /receipts`;
- `GET /receipts/{receipt_id}`.

Validate Entra identity claims at the boundary and return stable failure codes.

- [ ] **Step 6: Run tests**

```bash
python3 -m pytest \
  skills/threadlight-govern/tests/test_control_plane.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-govern/references/control-plane \
  skills/threadlight-govern/tests/test_control_plane.py
git commit -m "feat: add governance control plane reference"
```

---

### Task 9: Implement the governed MCP/tool gateway and GHCP adapter

**Files:**
- Create: `skills/threadlight-govern/references/gateway/server.py`
- Create: `skills/threadlight-govern/references/gateway/dispatcher.py`
- Create: `skills/threadlight-govern/references/gateway/receipts.py`
- Create: `skills/threadlight-govern/references/gateway/pyproject.toml`
- Create: `skills/threadlight-govern/references/gateway/Dockerfile`
- Create: `skills/threadlight-govern/tests/test_gateway.py`
- Modify: `skills/threadlight-governed-actions/scripts/ghcp.py`
- Modify: `skills/threadlight-governed-actions/tests/test_ghcp.py`

- [ ] **Step 1: Write RED gateway tests**

```python
@pytest.mark.asyncio
async def test_deny_writes_receipt_and_skips_downstream():
    downstream = FakeDownstream()
    gateway = gateway_with(decision="deny", downstream=downstream)
    result = await gateway.dispatch(request_for("returns_apply_decision"))
    assert result.status == "blocked_by_policy"
    assert downstream.calls == 0
    assert gateway.receipts[0].decision == "deny"


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_creates_one_effect():
    downstream = FakeDownstream()
    gateway = gateway_with(decision="allow", downstream=downstream)
    request = request_for("returns_apply_decision", idempotency_key="same")
    first = await gateway.dispatch(request)
    second = await gateway.dispatch(request)
    assert first == second
    assert downstream.calls == 1
```

- [ ] **Step 2: Run and observe RED**

```bash
python3 -m pytest skills/threadlight-govern/tests/test_gateway.py -q
```

Expected: missing gateway modules.

- [ ] **Step 3: Implement dispatch order**

`GovernedDispatcher.dispatch` must execute exactly:

```text
authenticate workload
-> validate tenant/scope/schema
-> compute canonical action hash
-> resolve binding
-> evaluate local ACS
-> resolve approval when required
-> reserve idempotency key
-> persist pre-execution receipt
-> call downstream
-> persist outcome reference
-> return sanitized result
```

Any failure before downstream dispatch returns a stable blocked/unavailable
result. Never catch and convert a failure into success.

- [ ] **Step 4: Expose registered tools through FastMCP**

Load structured tool definitions from a signed registry and create one MCP tool
per configured downstream action. Do not expose a generic arbitrary URL or
shell tool.

- [ ] **Step 5: Add GHCP effect-closure checks**

The GHCP assessor passes `action-governed` only when:

- the bound tool URL is the gateway URL;
- the agent has no direct downstream credential;
- network/IAM evidence blocks direct downstream access;
- no built-in/provider-hosted tool maps to the same effect;
- the live denial receipt binds the deployed Invocations agent version.

- [ ] **Step 6: Run gateway and GHCP tests**

```bash
python3 -m pytest \
  skills/threadlight-govern/tests/test_gateway.py \
  skills/threadlight-governed-actions/tests/test_ghcp.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-govern/references/gateway \
  skills/threadlight-govern/tests/test_gateway.py \
  skills/threadlight-governed-actions
git commit -m "feat: govern remote actions through ACA gateway"
```

---

### Task 10: Wire governance into generated deployments

**Files:**
- Modify: `skills/threadlight-deploy/SKILL.md`
- Create: `skills/threadlight-deploy/references/governance/maf-container.py`
- Create: `skills/threadlight-deploy/references/governance/ghcp-container.py`
- Create: `skills/threadlight-deploy/references/governance/pyproject-maf.toml`
- Create: `skills/threadlight-deploy/references/governance/governance.bicep`
- Create: `skills/threadlight-deploy/references/governance/azure-services.yaml`
- Create: `skills/threadlight-deploy/tests/test_governance_wiring.py`
- Modify: `.github/workflows/python-pytest.yml`

- [ ] **Step 1: Write RED generation-contract tests**

```python
def test_maf_template_installs_hooks_first():
    module = ast.parse(MAF_TEMPLATE.read_text())
    call = find_agent_constructor(module)
    assert source_segment(call, "middleware").startswith("[hooks,")


def test_ghcp_template_routes_bound_mcp_to_gateway():
    text = GHCP_TEMPLATE.read_text()
    assert "GOVERNED_TOOL_GATEWAY_URL" in text
    assert "policy_binding" in text


def test_governance_dependencies_match_shared_pin():
    deps = parse_dependencies(MAF_PYPROJECT)
    assert deps["agent-hooks-sdk"] == "0.1.0a5"
    assert deps["agent-control-specification"] == "0.3.1b0"
```

- [ ] **Step 2: Run and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-deploy/tests/test_governance_wiring.py -q
```

Expected: reference templates missing.

- [ ] **Step 3: Add MAF generation rules**

For selected `local-agent-hooks` bindings:

- copy the runtime provider;
- copy the signed policy bundle;
- add exact governance dependencies;
- instantiate hooks before application middleware;
- expose binding health;
- remove the diagnostic healthy fallback on governance initialization failure.

- [ ] **Step 4: Add GHCP generation rules**

For selected `governed-tool-gateway` bindings:

- rewrite only the selected MCP URLs to the gateway;
- retain unbound MCP URLs unchanged;
- propagate correlation metadata with GHCP's pre-MCP hook when available;
- never claim full Agent Hooks coverage.

- [ ] **Step 5: Add ACA/Bicep composition**

The module provisions:

- two Container Apps;
- UAMIs and least-privilege RBAC;
- Blob container for bundles;
- Cosmos containers for approvals/receipts;
- Key Vault key access for bundle signing/verification;
- private service-to-service ingress where the target posture requires it;
- outputs consumed by `azure.yaml`.

- [ ] **Step 6: Run deploy tests**

```bash
python3 -m pytest skills/threadlight-deploy/tests -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-deploy .github/workflows/python-pytest.yml
git commit -m "feat: generate governed agent deployments"
```

---

### Task 11: Add pre-deploy and live enforcement gates

**Files:**
- Modify: `skills/threadlight-safe-check/scripts/safe_check.py`
- Modify: `skills/threadlight-safe-check/tests/test_safe_check.py`
- Modify: `skills/threadlight-safe-check/SKILL.md`
- Create: `skills/threadlight-safe-check/references/governance_probe.py`

- [ ] **Step 1: Write RED pre-deploy tests**

```python
def test_selected_binding_missing_runtime_adapter_is_gap(tmp_path):
    repo = governed_repo(tmp_path, adapter_present=False)
    result = phase_predeploy(repo, repo / "specs/manifest.json", out(repo))
    assert result == 1
    assert "selected binding has no runtime adapter" in read_gaps(repo)


def test_intentionally_unbound_read_tool_is_not_gap(tmp_path):
    repo = governed_repo(tmp_path, unbound_read=True)
    assert phase_predeploy(repo, manifest(repo), out(repo)) == 0
```

- [ ] **Step 2: Write RED post-deploy tests**

Use an injected command runner and HTTP client:

```python
def test_live_deny_requires_zero_external_effect_and_receipt():
    state = fake_live_state(
        decision="deny",
        gateway_dispatch_delta=0,
        downstream_effect_delta=0,
        receipt_policy_digest=POLICY_DIGEST,
    )
    assert governance_postdeploy_gaps(state) == []


def test_evaluate_only_never_counts_as_enforced():
    state = fake_live_state(mode="evaluate_only", decision="deny")
    assert "evaluate_only" in " ".join(governance_postdeploy_gaps(state))
```

- [ ] **Step 3: Implement static governance checks**

Validate exact:

- agent runtime versus enforcement path;
- selected binding versus generated adapter;
- signed bundle digest versus image metadata;
- gateway/control-plane services and source directories;
- comprehensive-mode coverage;
- effect-closure declarations.

- [ ] **Step 4: Implement the live no-op denial probe**

`governance_probe.py`:

1. resolves exact agent version and image digest;
2. snapshots gateway/downstream counters;
3. invokes a declared no-op denial action;
4. re-reads counters;
5. fetches correlated decision receipt;
6. emits payload-free JSON.

The probe refuses any action not explicitly marked `probe_safe: true`.

- [ ] **Step 5: Add governance health to postdeploy manifest**

Write `governance_health`, `governance_probes`, and governance gaps into the
existing postdeploy manifest without changing the meaning of `gaps: []`.

- [ ] **Step 6: Run safe-check tests**

```bash
python3 -m pytest skills/threadlight-safe-check/tests -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-safe-check
git commit -m "feat: gate deployed governance enforcement"
```

---

### Task 12: Migrate readiness, evidence, and orchestration consumers

**Files:**
- Modify: `skills/threadlight-production-ready/scripts/production_ready.py`
- Modify: `skills/threadlight-production-ready/scripts/evidence_gate.py`
- Modify: `skills/threadlight-production-ready/scripts/ai_act_evidence.py`
- Modify: `skills/threadlight-production-ready/references/pillars/02-agent-governance.md`
- Modify: `skills/threadlight-production-ready/references/pillars/07-responsible-ai.md`
- Modify: `skills/threadlight-production-ready/references/remediation-recipes/AGT-001.md`
- Modify: `skills/threadlight-production-ready/tests/test_leg_manifests.py`
- Modify: `skills/threadlight-production-ready/tests/test_governed_actions_manifest.py`
- Modify: `skills/threadlight-production-ready/tests/test_evidence_gate.py`
- Modify: `skills/threadlight-production-ready/tests/test_ai_act_evidence.py`
- Modify: `skills/threadlight-auto/references/orchestrator.py`
- Modify: `skills/threadlight-auto/SKILL.md`
- Modify: `skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py`
- Modify: `skills/threadlight-auto/tests/test_e2e_control_plane.py`

- [ ] **Step 1: Write RED consumer tests**

```python
def test_v2_govern_manifest_is_legacy_not_enforcement(tmp_path):
    write_json(tmp_path / "specs/govern-manifest.json", legacy_green_v2())
    findings = governance_findings(context(tmp_path))
    assert findings["AGT-001"].status == "not-verified"


def test_selected_binding_without_live_proof_is_must_fix(tmp_path):
    write_json(
        tmp_path / "specs/governance-manifest.json",
        manifest_with_binding(status="unverified"),
    )
    assert governance_findings(context(tmp_path))["AGT-001"].status == "must-fix"


def test_unbound_read_tool_does_not_fail_readiness(tmp_path):
    manifest = manifest_with_unbound_read_tool()
    assert governance_findings_for(manifest)["AGT-001"].status == "pass"
```

- [ ] **Step 2: Run focused tests and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-production-ready/tests/test_leg_manifests.py \
  skills/threadlight-production-ready/tests/test_evidence_gate.py \
  skills/threadlight-auto/tests/test_threadlight_auto_orchestrator.py -q
```

Expected: consumers still require v2 and whole-agent verdicts.

- [ ] **Step 3: Replace the assurance predicate**

Production readiness passes the governance pillar only when:

```python
def production_bindings_satisfied(manifest):
    return (
        manifest["enforcement"]["mode"] == "enforce"
        and manifest["coverage"]["tools_unverified"] == 0
        and manifest["coverage"]["tools_bypassable"] == 0
        and not manifest["gaps"]
        and all(
            binding["status"] in {"enforced", "unbound"}
            for binding in manifest["bindings"]
        )
    )
```

Unbound consequential tools additionally require a current acceptance record.

- [ ] **Step 4: Migrate Auto**

Change the `govern` leg contract to
`specs/governance-manifest.json`. When bindings exist, Auto schedules:

1. governance producer before deploy;
2. pre-deploy governed-actions gate;
3. live governance probe after deploy.

It no longer treats governed-actions as recommendation-only when bindings are
selected.

- [ ] **Step 5: Migrate AI Act evidence**

Article 9 maps to binding coverage, policy provenance, and live receipts.
Legacy v2 remains provenance with status `partial`, never `covered`.

- [ ] **Step 6: Run consumer suites**

```bash
python3 -m pytest \
  skills/threadlight-production-ready/tests \
  skills/threadlight-auto/tests -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add skills/threadlight-production-ready skills/threadlight-auto
git commit -m "feat: consume binding-level governance evidence"
```

---

### Task 13: Migrate the canonical returns-triage agent

**Files:**
- Modify: `examples/returns-triage-governed/specs/foundation.md`
- Modify: `examples/returns-triage-governed/specs/SPEC.md`
- Modify: `examples/returns-triage-governed/specs/manifest.json`
- Delete: `examples/returns-triage-governed/specs/govern-manifest.json`
- Create: `examples/returns-triage-governed/specs/governance-manifest.json`
- Modify: `examples/returns-triage-governed/agent.yaml`
- Modify: `examples/returns-triage-governed/azure.yaml`
- Modify: `examples/returns-triage-governed/src/agent/container.py`
- Modify: `examples/returns-triage-governed/src/agent/pyproject.toml`
- Create: `examples/returns-triage-governed/src/agent/governance/`
- Create: `examples/returns-triage-governed/src/governance-control-plane/`
- Create: `examples/returns-triage-governed/src/governed-tool-gateway/`
- Modify: `examples/returns-triage-governed/tests/safe_check.py`
- Create: `examples/returns-triage-governed/tests/test_governance_runtime.py`

- [ ] **Step 1: Write RED example assertions**

```python
def test_returns_apply_decision_is_selectively_bound():
    agent = yaml.safe_load(AGENT_YAML.read_text())
    tool = next(t for t in agent["tools"] if t["id"] == "returns_apply_decision")
    assert tool["policy_binding"] == "returns-write-v1"
    assert tool["enforcement_path"] == "local-agent-hooks"


def test_agent_constructor_installs_hooks_first():
    source = CONTAINER.read_text()
    assert "middleware=[hooks" in source
```

- [ ] **Step 2: Run and observe RED**

```bash
python3 -m pytest \
  examples/returns-triage-governed/tests/test_governance_runtime.py -q
```

Expected: string tool entries and no hooks.

- [ ] **Step 3: Migrate the runtime**

- pin the current MAF stack and governance packages;
- install the MAF provider;
- bind only `returns_apply_decision`;
- keep read-only tools unbound;
- add a no-op denied probe action that cannot settle or mutate a real return;
- remove obsolete AGT v4 policy files.

- [ ] **Step 4: Add SAFE policy**

Implement:

- Scope: only declared return cases/actions;
- Anchored Decisions: decision write requires verified order/return/customer
  evidence;
- Flow Integrity: intake, eligibility, and fraud checks precede disposition;
- Escalation: supervisor handoff when the declared gates fire.

- [ ] **Step 5: Generate the governance manifest**

Offline evidence is `observed` until the live pre-production probe is attached.
Do not commit fabricated live receipts.

- [ ] **Step 6: Run example static and local tests**

```bash
python3 -m pytest examples/returns-triage-governed/tests -q
python3 skills/threadlight-governed-actions/scripts/governed_actions.py \
  --target examples/returns-triage-governed \
  --phase pre-deploy --gate
```

Expected: tests pass; the parser accepts the canonical agent; selected bindings
have complete local evidence.

- [ ] **Step 7: Commit**

```bash
git add examples/returns-triage-governed
git commit -m "feat: govern the returns triage runtime"
```

---

### Task 14: Update public guidance and CI semantics

**Files:**
- Modify: `README.md`
- Modify: `docs/IDEA-TO-PRODUCTION-WORKBOOK.md`
- Modify: `docs/production-readiness.md`
- Modify: `docs/index.html`
- Modify: `docs/production.html`
- Modify: `docs/assets/process-library.json`
- Modify: `skills/threadlight-govern/SKILL.md`
- Modify: `skills/threadlight-governed-actions/SKILL.md`
- Modify: `skills/threadlight-safe-check/SKILL.md`
- Modify: `skills/threadlight-production-ready/SKILL.md`
- Modify: `.github/workflows/threadlight-e2e-foundry.yml`
- Modify: `CHANGELOG.md`
- Modify: `plugin.json`
- Modify: `.github/plugin/marketplace.json`

- [ ] **Step 1: Write wording regression tests**

Add assertions that public docs:

- define SAFE as method;
- define ACS as PDP;
- define Agent Hooks as host/interceptor contract;
- define host/gateway as PEP;
- define AGT as toolkit;
- define ASSERT as assurance;
- contain no claim that policy/CI alone governs runtime;
- contain no whole-agent `governed` success claim.

- [ ] **Step 2: Run wording tests and observe RED**

```bash
python3 -m pytest \
  skills/threadlight-production-ready/tests/test_script_strings.py \
  skills/threadlight-auto/tests/test_threadlight_auto_cost_contract.py -q
```

Expected: stale policy/middleware/whole-verdict wording fails assertions.

- [ ] **Step 3: Rewrite skill and public documentation**

Document:

- selective bindings;
- MAF full SAFE and GHCP action-governed limits;
- preview/experimental status;
- policy distribution and ACA components;
- evidence chain and live denial proof;
- migration from v2.

- [ ] **Step 4: Make readiness-proof run the real governance gates**

In `.github/workflows/threadlight-e2e-foundry.yml`:

1. build and validate the governance bundle;
2. run pre-deploy governed-actions with `--gate`;
3. deploy;
4. run safe-check post-deploy governance probe;
5. validate `governance-manifest/v1`;
6. never accept legacy v2 as passing.

- [ ] **Step 5: Run docs and workflow tests**

```bash
python3 -m pytest tests/blueprint tests/ci -q
node --test tests/blueprint/*.test.mjs tests/ci/*.test.mjs
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add README.md docs skills/threadlight-govern/SKILL.md \
  skills/threadlight-governed-actions/SKILL.md \
  skills/threadlight-safe-check/SKILL.md \
  skills/threadlight-production-ready/SKILL.md \
  .github/workflows/threadlight-e2e-foundry.yml \
  CHANGELOG.md plugin.json .github/plugin/marketplace.json
git commit -m "docs: publish runtime governance lifecycle"
```

---

### Task 15: Run the full acceptance gate and prepare the single PR

**Files:**
- Modify only files required to fix failures directly caused by Tasks 1-14.

- [ ] **Step 1: Run targeted governance suites**

```bash
python3 -m pytest \
  skills/_shared/tests \
  skills/threadlight-design/tests \
  skills/threadlight-govern/tests \
  skills/threadlight-governed-actions/tests \
  skills/threadlight-deploy/tests \
  skills/threadlight-safe-check/tests \
  skills/threadlight-production-ready/tests \
  skills/threadlight-auto/tests \
  examples/returns-triage-governed/tests -q
```

Expected: zero failures.

- [ ] **Step 2: Run exact-pin runtime and CTK gates**

```bash
python3 scripts/ci/run-governance-pin-tests.py
```

Expected markers:

```text
AGT_VERSION=5.0.0
ACS_VERSION=0.3.1b0
AGENT_HOOKS_VERSION=0.1.0a5
OPA_SHA256=PASS
AGENT_HOOKS_CTK=PASS
BOUND_DENY_EXECUTIONS=0
UNBOUND_ALLOW_EXECUTIONS=1
GOVERNANCE_RUNTIME_CONTRACT=PASS
```

- [ ] **Step 3: Run repository CI commands**

Use the commands already wired in `.github/workflows/python-pytest.yml` and the
Node/docs workflows. Do not replace them with a new runner.

Expected: every required job passes; no `continue-on-error` is added to
governance tests.

- [ ] **Step 4: Run static acceptance checks**

```bash
git --no-pager diff --check
rg -n "threadlight-govern-manifest/v2|verdict: governed|governance still enforced at CI" \
  README.md docs skills examples
python3 scripts/ci/check-test-dirs-wired.py
```

Expected: only explicitly labelled migration-history references to v2; no false
runtime-enforcement claims; all test directories are CI-wired.

- [ ] **Step 5: Run a staging deployment proof**

Against an isolated pre-production environment:

```bash
python3 skills/threadlight-safe-check/scripts/safe_check.py \
  --phase post-deploy \
  --manifest examples/returns-triage-governed/specs/manifest.json \
  --out examples/returns-triage-governed/tests
```

Expected:

- exact candidate image/policy digests;
- bound deny with zero gateway/downstream delta;
- unbound read action succeeds;
- receipt correlation passes;
- `gaps: []`.

- [ ] **Step 6: Request independent review**

Run a code-review pass over the full branch, focusing on:

- fail-open paths;
- unbound/bound confusion;
- alternate-effect bypasses;
- production evidence spoofing;
- policy bundle supply chain;
- accidental whole-agent claims.

Address only high-confidence correctness findings.

- [ ] **Step 7: Commit final directly-related fixes**

```bash
git add skills/_shared skills/threadlight-design skills/threadlight-deploy \
  skills/threadlight-govern skills/threadlight-governed-actions \
  skills/threadlight-safe-check skills/threadlight-production-ready \
  skills/threadlight-auto examples/returns-triage-governed \
  README.md docs .github/workflows CHANGELOG.md plugin.json \
  .github/plugin/marketplace.json
git commit -m "fix: close runtime governance review gaps"
```

Skip this commit when review finds nothing.

- [ ] **Step 8: Create the pull request**

The PR body must state:

- SAFE/ACS/Agent Hooks/AGT layer model;
- selective binding semantics;
- MAF and GHCP coverage boundaries;
- exact preview dependencies;
- staging evidence and zero-effect oracle;
- migration impact;
- full verification command/results.

Use one PR targeting `main`.
