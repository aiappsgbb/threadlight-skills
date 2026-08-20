#!/usr/bin/env python3
"""Standalone tests for threadlight-safe-check.

Two concerns:

1. The pure ``integration_binding_gaps`` helper — the ONLY new failure the
   post-deploy gate learns to raise: a system integration whose deployment
   snapshot declares ``availability: real`` while the effective MCP server
   endpoint is provably still the scaffolded mock.

2. A byte-for-byte parity assertion between the canonical
   ``scripts/safe_check.py`` and the example copy shipped as
   ``examples/returns-triage-governed/tests/safe_check.py`` — the two must
   never drift, so the helper + behaviour stay in lock-step.

pytest-style (bare ``test_`` functions + ``assert``); also runnable standalone
(``python3 skills/threadlight-safe-check/tests/test_safe_check.py``) because the
skill ships no pytest harness of its own.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SCRIPT = SKILL_DIR / "scripts" / "safe_check.py"
REPO_ROOT = SKILL_DIR.parent.parent
EXAMPLE_COPY = (
    REPO_ROOT / "examples" / "returns-triage-governed" / "tests" / "safe_check.py"
)
HITL_SKILL = REPO_ROOT / "skills" / "threadlight-hitl-patterns" / "SKILL.md"
HITL_AUDIT_SCHEMA = (
    REPO_ROOT
    / "skills"
    / "threadlight-hitl-patterns"
    / "references"
    / "audit-schema.md"
)
GOVERNED_FIXTURE = TEST_DIR / "fixtures" / "tool-governance-enabled"
SCRATCH_ROOT = TEST_DIR / "_scratch"

sys.path.insert(0, str(SCRIPT.parent))
import safe_check as sc  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload, indent=2) + "\n")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json_code_blocks(markdown: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for chunk in markdown.split("```json")[1:]:
        block, fence, _ = chunk.partition("```")
        assert fence, "unterminated ```json block"
        payload = json.loads(block.strip())
        assert isinstance(payload, dict)
        blocks.append(payload)
    return blocks


@contextmanager
def _scratch_dir(name: str) -> Path:
    SCRATCH_ROOT.mkdir(exist_ok=True)
    root = SCRATCH_ROOT / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _governed_repo_copy(name: str) -> Path:
    with _scratch_dir(name) as container:
        repo = container / "repo"
        shutil.copytree(GOVERNED_FIXTURE, repo)
        yield repo


def test_governed_repo_copy_cleans_sibling_artifacts() -> None:
    container: Path | None = None
    with _governed_repo_copy("sibling-cleanup") as repo:
        container = repo.parent
        leaked = container / "outside-artifact.json"
        leaked.write_text("{}", encoding="utf-8")
        assert leaked.exists()
    assert container is not None
    assert not container.exists()


def _seed_predeploy_files(repo: Path) -> None:
    _write(
        repo / "azure.yaml",
        "name: governed-fixture\n"
        "services:\n"
        "  - name: mcp\n"
        "    project: ./src/mcp\n",
    )
    _write(repo / "infra" / "main.bicep", "targetScope = 'resourceGroup'\n")
    _write(repo / "src" / "mcp" / "Dockerfile", "FROM scratch\n")


def _normalize_governance_artifacts(
    repo: Path,
    *,
    runtime: dict[str, str] | None = None,
) -> str:
    manifest = _read_json(repo / "specs" / "manifest.json")
    contract_sha256 = sc.canonical_sha256(manifest["tool_governance"])
    adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
    adapter = _read_json(adapter_path)
    if runtime is not None:
        adapter["runtime"] = runtime
    adapter["contract_sha256"] = contract_sha256
    _write_json(adapter_path, adapter)
    for rel in {
        binding.get("policy_artifact")
        for binding in adapter.get("bindings", [])
        if isinstance(binding, dict)
        and isinstance(binding.get("policy_artifact"), str)
        and binding["policy_artifact"]
    }:
        _write_json(
            repo / rel,
            {
                "schema": "threadlight.tool-governance/test-policy/v1",
                "contract_sha256": contract_sha256,
            },
        )
    return contract_sha256


def _probe_adapter(repo: Path) -> dict[str, object]:
    return _read_json(repo / "policies" / "tool-governance" / "adapter-manifest.json")


def _probe_evidence_path(repo: Path) -> Path:
    adapter = _probe_adapter(repo)
    return repo / str(adapter["probe"]["evidence"])


def _probe_entrypoint_path(repo: Path) -> Path:
    adapter = _probe_adapter(repo)
    return repo / str(adapter["probe"]["entrypoint"])


def _mutate_probe_adapter(repo: Path, mutator) -> None:
    adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
    adapter = _read_json(adapter_path)
    mutator(adapter)
    _write_json(adapter_path, adapter)


def _mutate_probe_evidence(repo: Path, mutator) -> None:
    evidence_path = _probe_evidence_path(repo)
    evidence = _read_json(evidence_path)
    mutator(evidence)
    _write_json(evidence_path, evidence)


def _sync_probe_evidence_metadata(repo: Path) -> None:
    adapter = _probe_adapter(repo)
    _mutate_probe_evidence(
        repo,
        lambda evidence: evidence.update(
            {"adapter_manifest_sha256": sc.canonical_sha256(adapter)}
        ),
    )


def _vector(evidence: dict[str, object], vector_id: str) -> dict[str, object]:
    for item in evidence["vectors"]:
        if item["id"] == vector_id:
            return item
    raise AssertionError(f"missing vector {vector_id}")


def _assert_event_id_array(
    vector: dict[str, object], field: str, *, allow_empty: bool = False
) -> None:
    legacy_field = "decision_events" if field == "decision_event_ids" else "outcome_events"
    assert legacy_field not in vector
    assert field in vector
    value = vector[field]
    assert isinstance(value, list)
    assert all(isinstance(item, str) and item for item in value)
    assert len(value) == len(set(value))
    if not allow_empty:
        assert value


def _assert_exact_probe_vector_schema(
    vector: dict[str, object], *, require_outcome: bool
) -> None:
    _assert_event_id_array(vector, "decision_event_ids")
    _assert_event_id_array(
        vector, "outcome_event_ids", allow_empty=not require_outcome
    )
    if require_outcome:
        assert len(vector["outcome_event_ids"]) == 1
    else:
        assert vector["outcome_event_ids"] == []


# ---------------------------------------------------------------------------
# integration_binding_gaps — the certain contradiction
# ---------------------------------------------------------------------------

def test_real_spec_with_mock_runtime_is_gap() -> None:
    gaps = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"url": "https://mock.example/mcp"}}},
    )
    assert gaps == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_mock_spec_with_mock_runtime_is_allowed() -> None:
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "mock"}],
        mcp_config={"servers": {"erp": {"url": "https://mock.example/mcp"}}},
    ) == []


def test_real_spec_with_real_runtime_is_allowed() -> None:
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"url": "https://erp.contoso.com/mcp"}}},
    ) == []


def test_real_spec_with_missing_server_is_not_invented_as_mock() -> None:
    # A real integration whose server has no entry in mcp-config has *missing*
    # config metadata — that is not evidence of a mock, so no gap is invented.
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {}},
    ) == []


def test_real_spec_with_endpointless_server_is_not_invented_as_mock() -> None:
    # Server exists but declares no url/host/name — absence of endpoint
    # metadata must never be treated as a mock marker.
    assert sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"type": "http"}}},
    ) == []


def test_mock_marker_detected_in_host_or_name() -> None:
    by_host = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"host": "erp-mock.internal"}}},
    )
    by_name = sc.integration_binding_gaps(
        integrations=[{"id": "erp", "availability": "real"}],
        mcp_config={"servers": {"erp": {"name": "erp-mock"}}},
    )
    assert by_host == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]
    assert by_name == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_multiple_integrations_only_flags_the_contradiction() -> None:
    gaps = sc.integration_binding_gaps(
        integrations=[
            {"id": "erp", "availability": "real"},
            {"id": "crm", "availability": "real"},
            {"id": "oms", "availability": "mock"},
        ],
        mcp_config={
            "servers": {
                "erp": {"url": "https://mock.example/mcp"},   # real + mock -> gap
                "crm": {"url": "https://crm.contoso.com/mcp"},  # real + real -> ok
                "oms": {"url": "https://mock.example/mcp"},    # mock + mock -> ok
            }
        },
    )
    assert gaps == [
        "integration erp is declared real but runtime endpoint is still mock"
    ]


def test_malformed_inputs_never_raise() -> None:
    assert sc.integration_binding_gaps(integrations=None, mcp_config=None) == []
    assert sc.integration_binding_gaps(integrations=[], mcp_config={}) == []
    assert sc.integration_binding_gaps(
        integrations=["not-a-dict", {"availability": "real"}],  # no id
        mcp_config={"servers": {"erp": {"url": "mock"}}},
    ) == []


# ---------------------------------------------------------------------------
# Mock marker — a conservative, delimited token, never a substring
# ---------------------------------------------------------------------------

def test_mock_marker_matches_delimited_mock_conventions() -> None:
    # Standalone / delimited mock, mocked, mockserver, and "local mock" all count.
    for endpoint in (
        "https://mock.example/mcp",   # dot-delimited
        "erp-mock.internal",          # hyphen-delimited
        "erp-mock",                   # trailing token
        "svc_mock",                   # underscore-delimited
        "mocked-api.local",           # 'mocked'
        "mockserver.internal",        # 'mockserver' compound convention
        "returns-MockServer",         # case-insensitive
        "local mock",                 # space-delimited
    ):
        gaps = sc.integration_binding_gaps(
            integrations=[{"id": "erp", "availability": "real"}],
            mcp_config={"servers": {"erp": {"url": endpoint}}},
        )
        assert gaps == [
            "integration erp is declared real but runtime endpoint is still mock"
        ], f"expected {endpoint!r} to read as a mock endpoint"


def test_mock_marker_does_not_match_substrings_like_mockingbird() -> None:
    # A real hostname that merely CONTAINS 'mock' as a substring (mockingbird,
    # smock, mockapifactory) is not a mock endpoint and must not trip the gate.
    for endpoint in (
        "https://mockingbird.example.com/mcp",
        "https://smock.contoso.com/mcp",
        "https://mockapifactory.io/mcp",
        "https://erp.contoso.com/mcp",
    ):
        assert sc.integration_binding_gaps(
            integrations=[{"id": "erp", "availability": "real"}],
            mcp_config={"servers": {"erp": {"url": endpoint}}},
        ) == [], f"{endpoint!r} must not be treated as a mock endpoint"


# ---------------------------------------------------------------------------
# Repo-root resolution — prefer the explicit CLI root, else discover the nearest
# marked ancestor; never blindly assume parent.parent for a nested manifest.
# ---------------------------------------------------------------------------

@contextmanager
def _nested_manifest_repo() -> tuple[Path, Path]:
    """Build <root>/{azure.yaml, infra/mcp-config.json} with a manifest nested at
    <root>/a/b/specs/manifest.json (a non-default path). Returns (root, manifest).

    Uses a repo-local scratch directory so the file is exercised identically
    under pytest and the standalone runner below."""
    with _scratch_dir("repo-root-resolution") as scratch:
        root = scratch / "root"
        (root / "infra").mkdir(parents=True)
        (root / "infra" / "mcp-config.json").write_text(
            '{"servers": {"erp": {"url": "https://mock.example/mcp"}}}',
            encoding="utf-8",
        )
        (root / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
        nested = root / "a" / "b" / "specs"
        nested.mkdir(parents=True)
        manifest = nested / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        yield root, manifest


def test_repo_root_prefers_explicit_cli_root() -> None:
    with _nested_manifest_repo() as (root, manifest):
        # An explicit root (what the CLI passes as Path.cwd()) always wins, even for a
        # deeply nested manifest whose parent.parent is NOT the root.
        assert sc._repo_root_for_manifest(manifest, explicit_root=root) == root
        assert manifest.parent.parent != root  # precondition: parent.parent is wrong


def test_repo_root_discovers_nearest_marked_ancestor_for_nested_manifest() -> None:
    with _nested_manifest_repo() as (root, manifest):
        # No explicit root: walk up to the nearest ancestor carrying a repo marker
        # (azure.yaml / infra), NOT the manifest's grandparent (a/b).
        resolved = sc._repo_root_for_manifest(manifest)
        assert resolved == root
        assert resolved != manifest.parent.parent


def test_repo_root_default_layout_unaffected() -> None:
    with _scratch_dir("repo-root-default-layout") as root:
        (root / "infra").mkdir(parents=True)
        (root / "azure.yaml").write_text("name: pilot\n", encoding="utf-8")
        (root / "specs").mkdir()
        manifest = root / "specs" / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        assert sc._repo_root_for_manifest(manifest) == root


def test_repo_root_falls_back_to_grandparent_without_markers() -> None:
    # A bare pilot with no azure.yaml / infra / .git / mcp-config: discovery finds
    # no marker and falls back to the legacy grandparent, which is correct for the
    # default specs layout.
    with _scratch_dir("repo-root-bare-layout") as root:
        (root / "specs").mkdir(parents=True)
        manifest = root / "specs" / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        original = sc._looks_like_repo_root
        sc._looks_like_repo_root = lambda _: False
        try:
            assert sc._repo_root_for_manifest(manifest) == manifest.parent.parent == root
        finally:
            sc._looks_like_repo_root = original


def test_nested_manifest_binding_gap_uses_correct_root() -> None:
    with _nested_manifest_repo() as (root, manifest):
        # End-to-end: with the explicit CLI root, the nested manifest still resolves
        # the effective mcp-config under <root>/infra and flags the mock binding.
        resolved = sc._repo_root_for_manifest(manifest, explicit_root=root)
        mcp = sc._load_effective_mcp_config(resolved)
        gaps = sc.integration_binding_gaps(
            integrations=[{"id": "erp", "availability": "real"}],
            mcp_config=mcp,
        )
        assert gaps == [
            "integration erp is declared real but runtime endpoint is still mock"
        ]


# ---------------------------------------------------------------------------
# Tool governance design validation
# ---------------------------------------------------------------------------

def _governed_inputs() -> tuple[dict[str, object], str]:
    manifest = json.loads(
        (GOVERNED_FIXTURE / "specs" / "manifest.json").read_text(encoding="utf-8")
    )
    spec_text = (GOVERNED_FIXTURE / "specs" / "SPEC.md").read_text(encoding="utf-8")
    return manifest, spec_text


def test_safe_check_and_hitl_skill_versions_are_pinned() -> None:
    assert 'version: "1.2.0"' in _read_text(SKILL_DIR / "SKILL.md")
    assert 'version: "1.2.0"' in _read_text(HITL_SKILL)


def test_hitl_contract_governed_fields_and_legacy_shape_are_documented() -> None:
    skill_text = _read_text(HITL_SKILL)
    audit_schema_text = _read_text(HITL_AUDIT_SCHEMA)
    audit_blocks = _json_code_blocks(audit_schema_text)
    governed_fields = {
        "gate_id",
        "correlation_id",
        "approval_id",
        "policy_id",
        "tool_name",
        "contract_sha256",
    }

    assert "## Document shape" in audit_schema_text
    assert "## Governed conditional extension" in audit_schema_text
    assert "audit-{uuid4}" not in audit_schema_text
    assert "audit-{case_id}-{gate}-{activity_id}" in skill_text
    assert "audit-{case_id}-{gate}-{activity_id}" in audit_schema_text
    assert len(audit_blocks) == 2

    for marker in governed_fields:
        assert marker in skill_text
        assert marker in audit_schema_text

    for marker in ("case_id", "gate", "decision", "actor", "timestamp", "linked_rules"):
        assert marker in skill_text
        assert marker in audit_schema_text

    base_document, governed_extension = audit_blocks
    assert base_document["id"] == "audit-{case_id}-{gate}-{activity_id}"
    assert governed_fields.isdisjoint(base_document)
    assert set(governed_extension) == governed_fields
    assert "merge" in audit_schema_text
    assert "governed conditional release" in audit_schema_text

    assert "persist approval audit event before releasing the governed tool" in skill_text
    assert "same approval_id cannot execute twice" in skill_text
    assert "replay returns the prior outcome" in skill_text


def test_tool_governance_design_contract_passes_fixture() -> None:
    manifest, spec_text = _governed_inputs()
    result, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert gaps == []
    assert result["enabled"] is True
    assert result["status"] == "pass"
    assert result["tools_count"] == 3
    assert result["contract_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"tool_governance": {"enabled": False}},
        {
            "tool_governance": {
                "contract_version": 99,
                "source": {"unexpected": True},
                "tools": "ignored when disabled is absent",
            }
        },
    ],
)
def test_disabled_or_absent_contract_is_not_applicable(
    manifest: dict[str, object],
) -> None:
    _, spec_text = _governed_inputs()
    result, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert result == {"enabled": False, "status": "not-applicable"}
    assert gaps == []


def test_non_boolean_enabled_is_invalid() -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["enabled"] = "yes"
    result, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert result == {"enabled": False, "status": "invalid"}
    assert gaps == ["tool_governance.enabled must be boolean"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("action_class", "unknown", "inventory_read has invalid action_class"),
        ("decision", "sometimes", "inventory_read has invalid decision"),
        (
            "enforcement_point",
            "prompt",
            "inventory_read has invalid enforcement_point",
        ),
    ],
)
def test_tool_governance_schema_rejects_unknown_enums(
    field: str, value: str, expected: str
) -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"][0][field] = value
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert expected in gaps


def test_tool_governance_schema_rejects_unknown_top_source_and_tool_keys() -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["implicit_allow"] = True
    broken["tool_governance"]["source"]["notes"] = "extra"
    broken["tool_governance"]["tools"][0]["alias"] = "inventory"
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "tool_governance unknown keys: implicit_allow" in gaps
    assert "tool_governance.source unknown keys: notes" in gaps
    assert "inventory_read unknown keys: alias" in gaps


def test_tool_governance_schema_rejects_missing_common_audit_fields() -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"][0]["required_audit_fields"].remove("actor_id")
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "inventory_read missing audit fields: actor_id" in gaps


def test_conditional_tool_requires_gate_id_and_conditional_audit_fields() -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    tool = broken["tool_governance"]["tools"][2]
    del tool["gate_id"]
    tool["required_audit_fields"].remove("gate_id")
    tool["required_audit_fields"].remove("approval_id")
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "returns_apply_decision gate_id is required for conditional decision" in gaps
    assert "returns_apply_decision conditional audit fields are incomplete" in gaps


def test_tool_governance_schema_rejects_duplicate_and_extra_tools() -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"].append(
        copy.deepcopy(broken["tool_governance"]["tools"][0])
    )
    broken["tool_governance"]["tools"].append(
        {
            "name": "inventory_shadow",
            "action_class": "read",
            "decision": "allow",
            "enforcement_point": "mcp-server",
            "policy_id": "TG-EXTRA",
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
            ],
        }
    )
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "duplicate governed tool: inventory_read" in gaps
    assert "contract tool absent from SPEC section 6: inventory_shadow" in gaps


def test_new_unclassified_tool_is_a_design_gap() -> None:
    manifest, spec_text = _governed_inputs()
    spec_text = spec_text.replace(
        "## 7. Knowledge Sources",
        "### `new_unclassified_tool`\n"
        "- **Action class**: `read`\n"
        "- **Decision**: `allow`\n"
        "- **Enforcement point**: `mcp-server`\n"
        "- **Policy ID**: `TG-NEW`\n"
        "- **Required audit fields**: `event_id`\n\n"
        "## 7. Knowledge Sources",
    )
    _, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert "unclassified canonical tool: new_unclassified_tool" in gaps


def test_grouped_tool_headings_are_invalid() -> None:
    manifest, spec_text = _governed_inputs()
    spec_text = spec_text.replace(
        "### `inventory_read`",
        "### `inventory_read` / `inventory_list`",
    )
    _, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert (
        "grouped canonical tool heading is invalid: `inventory_read` / `inventory_list`"
        in gaps
    )


def test_unquoted_tool_heading_is_still_canonical() -> None:
    manifest, spec_text = _governed_inputs()
    spec_text = spec_text.replace("### `inventory_read`", "### inventory_read")
    result, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert gaps == []
    assert result["status"] == "pass"


def test_case_sensitive_tool_names_must_match_exactly() -> None:
    manifest, spec_text = _governed_inputs()
    spec_text = spec_text.replace("### `inventory_read`", "### `Inventory_read`")
    _, gaps = sc.validate_tool_governance_design(manifest, spec_text)
    assert "contract tool absent from SPEC section 6: inventory_read" in gaps
    assert "unclassified canonical tool: Inventory_read" in gaps


def test_conditional_tool_requires_existing_gate() -> None:
    manifest, spec_text = _governed_inputs()
    broken = copy.deepcopy(manifest)
    broken["tool_governance"]["tools"][2]["gate_id"] = "GATE-999"
    _, gaps = sc.validate_tool_governance_design(broken, spec_text)
    assert "unknown gate_id GATE-999 for returns_apply_decision" in gaps


def test_duplicate_and_missing_spec_gates_are_gaps() -> None:
    manifest, spec_text = _governed_inputs()
    duplicated = spec_text.replace(
        "## 9. Success Criteria",
        "### Duplicate gate\n"
        "- **Gate ID**: `GATE-001`\n"
        "- **Action gate**: `approve`\n"
        "## 9. Success Criteria",
    )
    _, duplicated_gaps = sc.validate_tool_governance_design(manifest, duplicated)
    assert "duplicate SPEC section 8 gate_id: GATE-001" in duplicated_gaps

    missing = spec_text.replace("- **Gate ID**: `GATE-001` (stable and unique within the SPEC)\n", "")
    _, missing_gaps = sc.validate_tool_governance_design(manifest, missing)
    assert "unknown gate_id GATE-001 for returns_apply_decision" in missing_gaps


def test_contract_digest_is_stable() -> None:
    manifest, spec_text = _governed_inputs()
    reordered = json.loads(json.dumps(manifest))
    reordered["tool_governance"]["tools"] = list(
        reversed(reordered["tool_governance"]["tools"])
    )
    reordered["tool_governance"]["tools"] = list(
        reversed(reordered["tool_governance"]["tools"])
    )
    result, gaps = sc.validate_tool_governance_design(reordered, spec_text)
    assert gaps == []
    payload = json.dumps(
        reordered["tool_governance"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    import hashlib

    expected = "sha256:" + hashlib.sha256(payload).hexdigest()
    assert result["contract_sha256"] == expected


def test_phase_design_emits_tool_governance_result() -> None:
    manifest_path = GOVERNED_FIXTURE / "specs" / "manifest.json"
    out_path = GOVERNED_FIXTURE / "tests" / "safe-check-design-manifest.json"
    try:
        rc = sc.phase_design(manifest_path, out_path)
        assert rc == 0
        emitted = json.loads(out_path.read_text(encoding="utf-8"))
        assert emitted["gaps"] == []
        assert emitted["tool_governance"]["status"] == "pass"
        assert emitted["tool_governance"]["contract_sha256"].startswith("sha256:")
    finally:
        if out_path.exists():
            out_path.unlink()


# ---------------------------------------------------------------------------
# Tool governance pre-deploy validation
# ---------------------------------------------------------------------------

def test_validate_tool_governance_predeploy_passes_governed_fixture() -> None:
    with _governed_repo_copy("predeploy-pass") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        result, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert gaps == []
        assert result["enabled"] is True
        assert result["status"] == "pass"
        assert result["contract_sha256"] == sc.canonical_sha256(
            manifest["tool_governance"]
        )
        assert result["adapter_manifest"] == (
            "policies/tool-governance/adapter-manifest.json"
        )
        assert result["bindings_count"] == 3


def test_phase_predeploy_emits_tool_governance_payload() -> None:
    with _governed_repo_copy("phase-predeploy-payload") as repo:
        _seed_predeploy_files(repo)
        out_path = repo / "tests" / "safe-check-predeploy-manifest.json"
        rc = sc.phase_predeploy(repo, repo / "specs" / "manifest.json", out_path)
        emitted = _read_json(out_path)
        assert rc == 0
        assert emitted["gaps"] == []
        assert emitted["tool_governance"]["status"] == "pass"
        assert emitted["tool_governance"]["bindings_count"] == 3


def test_predeploy_disabled_tool_governance_is_not_applicable() -> None:
    with _governed_repo_copy("predeploy-disabled") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        manifest["tool_governance"].pop("enabled")
        result, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert result == {"enabled": False, "status": "not-applicable"}
        assert gaps == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda repo: (repo / "policies" / "tool-governance" / "adapter-manifest.json").unlink(),
            "tool governance adapter manifest missing",
        ),
        (
            lambda repo: _write(
                repo / "policies" / "tool-governance" / "adapter-manifest.json",
                "{not json}\n",
            ),
            "tool governance adapter manifest is not valid JSON",
        ),
        (
            lambda repo: _write_json(
                repo / "policies" / "tool-governance" / "adapter-manifest.json",
                [],
            ),
            "tool governance adapter manifest must be a JSON object",
        ),
    ],
)
def test_predeploy_missing_or_invalid_adapter_manifest_is_gap(
    mutation, expected: str
) -> None:
    with _governed_repo_copy(expected.replace(" ", "-")) as repo:
        _seed_predeploy_files(repo)
        mutation(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        result, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert result["status"] == "fail"
        assert any(expected in gap for gap in gaps)


def test_adapter_hash_mismatch_is_gap() -> None:
    with _governed_repo_copy("adapter-hash-mismatch") as repo:
        _seed_predeploy_files(repo)
        adapter = _read_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json"
        )
        adapter["contract_sha256"] = "sha256:deadbeef"
        _write_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json",
            adapter,
        )
        manifest = _read_json(repo / "specs" / "manifest.json")
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert (
            "tool governance adapter contract_sha256 must match the canonical manifest digest"
            in gaps
        )


def test_predeploy_runtime_tuple_mismatch_is_gap() -> None:
    with _governed_repo_copy("runtime-mismatch") as repo:
        _seed_predeploy_files(repo)
        adapter = _read_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json"
        )
        adapter["runtime"] = {
            "framework": "microsoft-agent-framework",
            "runtime_shape": "agent",
            "protocol": "responses",
        }
        _write_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json",
            adapter,
        )
        manifest = _read_json(repo / "specs" / "manifest.json")
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert (
            "tool governance adapter runtime must match specs/foundation.md framework/runtime_shape/protocol"
            in gaps
        )


def test_unknown_runtime_is_unsupported_even_when_dotnet_signal_is_present() -> None:
    with _governed_repo_copy("unknown-runtime") as repo:
        _seed_predeploy_files(repo)
        _write(
            repo / "specs" / "foundation.md",
            "# Foundation\n\n```yaml\nframework: dotnet-harness\nruntime_shape: agent\nprotocol: invocations\n```\n",
        )
        manifest = _read_json(repo / "specs" / "manifest.json")
        adapter = _read_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json"
        )
        adapter["runtime"] = {
            "framework": "dotnet-harness",
            "runtime_shape": "agent",
            "protocol": "invocations",
        }
        for binding in adapter["bindings"]:
            binding["wire_signals"] = [
                {
                    "path": "src/mcp/Program.cs",
                    "kind": "dotnet-with-governance",
                }
            ]
        _write(repo / "src" / "mcp" / "Program.cs", "builder.WithGovernance();\n")
        _write_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json",
            adapter,
        )
        _normalize_governance_artifacts(
            repo,
            runtime={
                "framework": "dotnet-harness",
                "runtime_shape": "agent",
                "protocol": "invocations",
            },
        )
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert "tool governance runtime framework not yet supported: dotnet-harness" in gaps
        assert not any("unknown wire signal kind" in gap for gap in gaps)


def test_ghcp_agent_middleware_binding_is_rejected() -> None:
    with _governed_repo_copy("ghcp-agent-middleware") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        for tool in manifest["tool_governance"]["tools"]:
            tool["enforcement_point"] = "agent-middleware"
        _write_json(repo / "specs" / "manifest.json", manifest)
        adapter = _read_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json"
        )
        for binding in adapter["bindings"]:
            binding["enforcement_point"] = "agent-middleware"
            binding["wire_signals"] = [
                {
                    "path": "src/agent/container.py",
                    "kind": "pre-tool-policy-binding",
                }
            ]
        _write(
            repo / "src" / "agent" / "container.py",
            "agent_os.integrations = []\npre_tool_call = 'governed'\n",
        )
        _write_json(
            repo / "policies" / "tool-governance" / "adapter-manifest.json",
            adapter,
        )
        _normalize_governance_artifacts(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert (
            "github-copilot-sdk tools must use mcp-server or gateway, not agent-middleware"
            in gaps
        )


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda repo, adapter: adapter["bindings"].pop(),
            "missing adapter binding for governed tool returns_apply_decision",
        ),
        (
            lambda repo, adapter: adapter["bindings"].append(copy.deepcopy(adapter["bindings"][0])),
            "duplicate adapter binding for governed tool inventory_read",
        ),
        (
            lambda repo, adapter: adapter["bindings"].append(
                {
                    "tool_name": "inventory_shadow",
                    "enforcement_point": "mcp-server",
                    "adapter_id": "shadow-adapter",
                    "policy_artifact": "policies/tool-governance/generated/mcp-policy.json",
                    "wire_signals": [
                        {
                            "path": "src/mcp/server.py",
                            "kind": "mcp-server-policy-binding",
                        }
                    ],
                }
            ),
            "adapter binding tool is not governed by the contract: inventory_shadow",
        ),
    ],
)
def test_predeploy_binding_coverage_requires_exactly_one_binding_per_tool(
    mutator, expected: str
) -> None:
    with _governed_repo_copy(expected.split()[-1]) as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        mutator(repo, adapter)
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert expected in gaps


def test_predeploy_binding_requires_nonempty_adapter_id() -> None:
    with _governed_repo_copy("binding-missing-adapter-id") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        adapter["bindings"][0]["adapter_id"] = ""
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert "inventory_read binding adapter_id must be a non-empty string" in gaps


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda repo, adapter: adapter["bindings"][0].update({"wire_signals": []}),
            "inventory_read binding must declare at least one wire signal",
        ),
        (
            lambda repo, adapter: adapter["bindings"][0].update(
                {"wire_signals": [{"path": "src/mcp/missing.py", "kind": "mcp-server-policy-binding"}]}
            ),
            "inventory_read wire signal path does not exist: src/mcp/missing.py",
        ),
        (
            lambda repo, adapter: adapter["bindings"][0].update(
                {"wire_signals": [{"path": "src/mcp/server.py", "kind": "unknown-binding"}]}
            ),
            "inventory_read has unknown wire signal kind: unknown-binding",
        ),
    ],
)
def test_missing_wire_signal_and_unknown_kinds_are_gaps(
    mutator, expected: str
) -> None:
    with _governed_repo_copy(expected.split(":")[0].replace(" ", "-")) as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        mutator(repo, adapter)
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert expected in gaps


def test_predeploy_unresolved_pretool_policy_binding_markers_are_gap() -> None:
    with _governed_repo_copy("pretool-unresolved") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        for tool in manifest["tool_governance"]["tools"]:
            tool["enforcement_point"] = "agent-middleware"
        _write_json(repo / "specs" / "manifest.json", manifest)
        _write(
            repo / "specs" / "foundation.md",
            "# Foundation\n\n```yaml\nframework: microsoft-agent-framework\nruntime_shape: agent\nprotocol: responses\n```\n",
        )
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        adapter["runtime"] = {
            "framework": "microsoft-agent-framework",
            "runtime_shape": "agent",
            "protocol": "responses",
        }
        for binding in adapter["bindings"]:
            binding["enforcement_point"] = "agent-middleware"
            binding["wire_signals"] = [
                {
                    "path": "src/agent/container.py",
                    "kind": "pre-tool-policy-binding",
                }
            ]
        _write(repo / "src" / "agent" / "container.py", "pre_tool_call = 'governed'\n")
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(
            repo,
            runtime={
                "framework": "microsoft-agent-framework",
                "runtime_shape": "agent",
                "protocol": "responses",
            },
        )
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert (
            "inventory_read pre-tool-policy-binding markers unresolved in src/agent/container.py"
            in gaps
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda repo: (repo / "policies" / "tool-governance" / "generated" / "mcp-policy.json").unlink(),
            "inventory_read policy artifact missing: policies/tool-governance/generated/mcp-policy.json",
        ),
        (
            lambda repo: _write(
                repo / "policies" / "tool-governance" / "generated" / "mcp-policy.json",
                "",
            ),
            "inventory_read policy artifact is empty: policies/tool-governance/generated/mcp-policy.json",
        ),
        (
            lambda repo: _write_json(
                repo / "policies" / "tool-governance" / "generated" / "mcp-policy.json",
                {"schema": "threadlight.tool-governance/test-policy/v1", "contract_sha256": "sha256:wrong"},
            ),
            "inventory_read policy artifact must contain contract_sha256 sha256:",
        ),
    ],
)
def test_predeploy_policy_artifact_must_exist_be_nonempty_and_match_hash(
    mutation, expected: str
) -> None:
    with _governed_repo_copy("policy-artifact-check") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        expected_hash = _normalize_governance_artifacts(repo)
        mutation(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        if expected.endswith("sha256:"):
            assert any(expected_hash in gap for gap in gaps)
        else:
            assert expected in gaps


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda adapter: adapter["audit"].update({"schema": "threadlight.tool-governance-audit/v2"}),
            "tool governance audit.schema must be 'threadlight.tool-governance-audit/v1'",
        ),
        (
            lambda adapter: adapter["audit"].update({"sink": ""}),
            "tool governance audit.sink must be a non-empty string",
        ),
        (
            lambda adapter: adapter["probe"].update({"entrypoint": ""}),
            "tool governance probe.entrypoint must be a non-empty string",
        ),
        (
            lambda adapter: adapter["probe"].update({"evidence": ""}),
            "tool governance probe.evidence must be a non-empty string",
        ),
    ],
)
def test_predeploy_bad_audit_and_probe_config_are_gaps(
    mutator, expected: str
) -> None:
    with _governed_repo_copy("audit-probe-check") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        mutator(adapter)
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert expected in gaps


def test_predeploy_gateway_policy_binding_signal_passes() -> None:
    with _governed_repo_copy("gateway-signal-pass") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        for tool in manifest["tool_governance"]["tools"]:
            tool["enforcement_point"] = "gateway"
        _write_json(repo / "specs" / "manifest.json", manifest)
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        for binding in adapter["bindings"]:
            binding["enforcement_point"] = "gateway"
            binding["wire_signals"] = [
                {
                    "path": "src/gateway/app.py",
                    "kind": "gateway-policy-binding",
                }
            ]
        _write(
            repo / "src" / "gateway" / "app.py",
            'GATEWAY_BINDING = "threadlight.tool-governance/gateway/v1"\n',
        )
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(repo)
        result, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert gaps == []
        assert result["status"] == "pass"


def test_predeploy_rejects_gateway_binding_backed_only_by_mcp_signal() -> None:
    with _governed_repo_copy("gateway-signal-mismatch") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        for tool in manifest["tool_governance"]["tools"]:
            tool["enforcement_point"] = "gateway"
        _write_json(repo / "specs" / "manifest.json", manifest)
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        for binding in adapter["bindings"]:
            binding["enforcement_point"] = "gateway"
            binding["wire_signals"] = [
                {
                    "path": "src/mcp/server.py",
                    "kind": "mcp-server-policy-binding",
                }
            ]
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(repo)
        _, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert (
            "inventory_read binding enforcement evidence must match gateway"
            in gaps
        )


def test_predeploy_maf_agent_middleware_pretool_signal_passes() -> None:
    with _governed_repo_copy("maf-agent-middleware-pass") as repo:
        _seed_predeploy_files(repo)
        manifest = _read_json(repo / "specs" / "manifest.json")
        for tool in manifest["tool_governance"]["tools"]:
            tool["enforcement_point"] = "agent-middleware"
        _write_json(repo / "specs" / "manifest.json", manifest)
        _write(
            repo / "specs" / "foundation.md",
            "# Foundation\n\n```yaml\nframework: microsoft-agent-framework\nruntime_shape: agent\nprotocol: responses\n```\n",
        )
        adapter_path = repo / "policies" / "tool-governance" / "adapter-manifest.json"
        adapter = _read_json(adapter_path)
        adapter["runtime"] = {
            "framework": "microsoft-agent-framework",
            "runtime_shape": "agent",
            "protocol": "responses",
        }
        for binding in adapter["bindings"]:
            binding["enforcement_point"] = "agent-middleware"
            binding["wire_signals"] = [
                {
                    "path": "src/agent/container.py",
                    "kind": "pre-tool-policy-binding",
                }
            ]
        _write(
            repo / "src" / "agent" / "container.py",
            "agent_os.integrations = ['foundry']\npre_tool_call = 'governed'\n",
        )
        _write_json(adapter_path, adapter)
        _normalize_governance_artifacts(
            repo,
            runtime={
                "framework": "microsoft-agent-framework",
                "runtime_shape": "agent",
                "protocol": "responses",
            },
        )
        result, gaps = sc.validate_tool_governance_predeploy(repo, manifest)
        assert gaps == []
        assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# Tool governance post-deploy probe validation
# ---------------------------------------------------------------------------

def test_probe_fixture_passes_without_running_probe() -> None:
    with _governed_repo_copy("probe-pass-without-run") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        result, gaps = sc.validate_tool_governance_probe(
            repo, manifest, run_probe=False
        )
        assert gaps == []
        assert result["enabled"] is True
        assert result["status"] == "pass"
        assert result["evidence"] == "tests/tool-governance-probe-manifest.json"
        assert result["contract_sha256"] == sc.canonical_sha256(
            manifest["tool_governance"]
        )


def test_probe_executes_actual_fixture_probe_when_requested() -> None:
    with _governed_repo_copy("probe-run-pass") as repo:
        evidence_path = _probe_evidence_path(repo)
        if evidence_path.exists():
            evidence_path.unlink()
        manifest = _read_json(repo / "specs" / "manifest.json")
        result, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=True)
        assert gaps == []
        assert result["status"] == "pass"
        assert evidence_path.exists()


def test_committed_probe_fixture_vectors_use_exact_event_id_arrays() -> None:
    evidence = _read_json(GOVERNED_FIXTURE / "tests" / "tool-governance-probe-manifest.json")
    vectors = {vector["id"]: vector for vector in evidence["vectors"]}
    _assert_exact_probe_vector_schema(vectors["allow-canary"], require_outcome=True)
    _assert_exact_probe_vector_schema(vectors["conditional-canary"], require_outcome=True)
    _assert_exact_probe_vector_schema(vectors["deny-canary"], require_outcome=False)


def test_probe_script_emits_exact_event_id_arrays() -> None:
    with _governed_repo_copy("probe-script-schema") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        result, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=True)
        assert gaps == []
        assert result["status"] == "pass"
        evidence = _read_json(_probe_evidence_path(repo))
        vectors = {vector["id"]: vector for vector in evidence["vectors"]}
        _assert_exact_probe_vector_schema(vectors["allow-canary"], require_outcome=True)
        _assert_exact_probe_vector_schema(
            vectors["conditional-canary"], require_outcome=True
        )
        _assert_exact_probe_vector_schema(
            vectors["deny-canary"], require_outcome=False
        )


def test_probe_disabled_or_missing_enabled_is_not_applicable() -> None:
    with _governed_repo_copy("probe-disabled") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        manifest["tool_governance"].pop("enabled")
        result, gaps = sc.validate_tool_governance_probe(
            repo, manifest, run_probe=False
        )
        assert result == {"enabled": False, "status": "not-applicable"}
        assert gaps == []


def test_deny_execution_count_must_stay_zero() -> None:
    with _governed_repo_copy("probe-deny-executed") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: _vector(evidence, "deny-canary").update(
                {
                    "observed_execution_count": 1,
                    "outcome_event_ids": ["outcome-deny-001"],
                }
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "deny-canary observed_execution_count must be 0" in gaps
        assert "deny-canary must not record outcome_event_ids" in gaps


@pytest.mark.parametrize("count", [0, 2])
def test_probe_allow_execution_count_must_be_exactly_one(count: int) -> None:
    with _governed_repo_copy(f"probe-allow-count-{count}") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: _vector(evidence, "allow-canary").update(
                {"observed_execution_count": count}
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "allow-canary observed_execution_count must be 1" in gaps


def test_probe_decision_mismatch_is_gap() -> None:
    with _governed_repo_copy("probe-decision-mismatch") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: _vector(evidence, "allow-canary").update(
                {"observed_decision": "deny"}
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "allow-canary observed_decision must be 'allow'" in gaps


def test_probe_audit_correlation_fields_are_required() -> None:
    with _governed_repo_copy("probe-audit-correlation") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: _vector(evidence, "allow-canary").update(
                {"correlation_id": "", "decision_event_ids": []}
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "allow-canary must record correlation_id" in gaps
        assert (
            "allow-canary decision_event_ids must be a non-empty list of unique "
            "non-empty strings"
        ) in gaps


def test_probe_hash_mismatches_are_gaps() -> None:
    with _governed_repo_copy("probe-hash-mismatch") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: evidence.update(
                {
                    "contract_sha256": "sha256:deadbeef",
                    "adapter_manifest_sha256": "sha256:badc0de",
                }
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert (
            "tool governance probe contract_sha256 must match the canonical manifest digest"
            in gaps
        )
        assert (
            "tool governance probe adapter_manifest_sha256 must match the canonical adapter digest"
            in gaps
        )


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda evidence: evidence.update(
                {"schema": "threadlight.tool-governance-probe/v2"}
            ),
            "tool governance probe schema must be 'threadlight.tool-governance-probe/v1'",
        ),
        (
            lambda evidence: evidence.update({"status": "fail"}),
            "tool governance probe status must be 'pass'",
        ),
    ],
)
def test_probe_schema_and_status_must_match_contract(mutator, expected: str) -> None:
    with _governed_repo_copy(expected.split()[3]) as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(repo, mutator)
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert expected in gaps


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda evidence: evidence.pop("audit_field_results"),
            "tool governance probe audit_field_results must be a list",
        ),
        (
            lambda evidence: evidence.update(
                {
                    "audit_field_results": evidence["audit_field_results"]
                    + [copy.deepcopy(evidence["audit_field_results"][0])]
                }
            ),
            "duplicate audit_field_results vector_id: allow-canary",
        ),
        (
            lambda evidence: evidence["audit_field_results"].pop(),
            "tool governance probe audit_field_results must cover exactly: allow-canary, conditional-canary, deny-canary",
        ),
    ],
)
def test_probe_audit_field_results_must_be_complete(mutator, expected: str) -> None:
    with _governed_repo_copy("probe-audit-results") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(repo, mutator)
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert expected in gaps


def test_probe_incomplete_audit_field_result_is_gap() -> None:
    with _governed_repo_copy("probe-audit-result-incomplete") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: evidence["audit_field_results"][0].update(
                {"status": "fail", "missing": ["correlation_id"]}
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "audit_field_results allow-canary must have status 'pass'" in gaps
        assert "audit_field_results allow-canary missing must be []" in gaps


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda evidence: _vector(evidence, "conditional-canary").update(
                {"gate_id": ""}
            ),
            "conditional-canary must record gate_id",
        ),
        (
            lambda evidence: _vector(evidence, "conditional-canary").update(
                {"approval_id": ""}
            ),
            "conditional-canary must record approval_id",
        ),
        (
            lambda evidence: _vector(evidence, "conditional-canary").update(
                {"correlation_id": ""}
            ),
            "conditional-canary must record correlation_id",
        ),
        (
            lambda evidence: _vector(evidence, "conditional-canary").update(
                {"observed_execution_count": 0}
            ),
            "conditional-canary observed_execution_count must be 1",
        ),
    ],
)
def test_probe_conditional_requirements_are_enforced(mutator, expected: str) -> None:
    with _governed_repo_copy("probe-conditional-requirements") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(repo, mutator)
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert expected in gaps


def test_probe_missing_conditional_outcome_is_gap() -> None:
    with _governed_repo_copy("probe-conditional-no-outcome") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: _vector(evidence, "conditional-canary").update(
                {"outcome_event_ids": []}
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "conditional-canary must record exactly one outcome_event_id" in gaps


def test_probe_allow_requires_outcome_event() -> None:
    with _governed_repo_copy("probe-allow-no-outcome") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(
            repo,
            lambda evidence: _vector(evidence, "allow-canary").update(
                {"outcome_event_ids": []}
            ),
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert "allow-canary must record exactly one outcome_event_id" in gaps


@pytest.mark.parametrize(
    ("name", "mutator", "expected"),
    [
        (
            "missing-decision-event-ids",
            lambda vector: vector.pop("decision_event_ids", None),
            "allow-canary decision_event_ids must be a non-empty list of unique non-empty strings",
        ),
        (
            "decision-event-ids-not-list",
            lambda vector: vector.update({"decision_event_ids": "decision-allow-001"}),
            "allow-canary decision_event_ids must be a non-empty list of unique non-empty strings",
        ),
        (
            "decision-event-ids-empty",
            lambda vector: vector.update({"decision_event_ids": []}),
            "allow-canary decision_event_ids must be a non-empty list of unique non-empty strings",
        ),
        (
            "decision-event-ids-non-string",
            lambda vector: vector.update({"decision_event_ids": ["decision-allow-001", ""]}),
            "allow-canary decision_event_ids must be a non-empty list of unique non-empty strings",
        ),
        (
            "decision-event-ids-duplicate",
            lambda vector: vector.update({"decision_event_ids": ["decision-allow-001", "decision-allow-001"]}),
            "allow-canary decision_event_ids must be a non-empty list of unique non-empty strings",
        ),
        (
            "legacy-decision-events",
            lambda vector: vector.update({"decision_events": [{"event_id": "decision-allow-legacy"}]}),
            "allow-canary must not include legacy decision_events",
        ),
        (
            "missing-outcome-event-ids",
            lambda vector: vector.pop("outcome_event_ids", None),
            "allow-canary must record exactly one outcome_event_id",
        ),
        (
            "outcome-event-ids-not-list",
            lambda vector: vector.update({"outcome_event_ids": "outcome-allow-001"}),
            "allow-canary outcome_event_ids must be a list of unique non-empty strings",
        ),
        (
            "outcome-event-ids-empty",
            lambda vector: vector.update({"outcome_event_ids": []}),
            "allow-canary must record exactly one outcome_event_id",
        ),
        (
            "outcome-event-ids-non-string",
            lambda vector: vector.update({"outcome_event_ids": ["outcome-allow-001", ""]}),
            "allow-canary outcome_event_ids must be a list of unique non-empty strings",
        ),
        (
            "outcome-event-ids-duplicate",
            lambda vector: vector.update({"outcome_event_ids": ["outcome-allow-001", "outcome-allow-001"]}),
            "allow-canary outcome_event_ids must be a list of unique non-empty strings",
        ),
        (
            "legacy-outcome-events",
            lambda vector: vector.update({"outcome_events": [{"event_id": "outcome-allow-legacy"}]}),
            "allow-canary must not include legacy outcome_events",
        ),
    ],
)
def test_probe_rejects_invalid_event_id_arrays(name: str, mutator, expected: str) -> None:
    with _governed_repo_copy(f"probe-id-arrays-{name}") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _mutate_probe_evidence(repo, lambda evidence: mutator(_vector(evidence, "allow-canary")))
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)
        assert expected in gaps


def test_probe_canary_only_guard_blocks_unsafe_entrypoint(monkeypatch) -> None:
    with _governed_repo_copy("probe-canary-only-guard") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        evidence_path = _probe_evidence_path(repo)
        assert evidence_path.exists()
        _write(
            _probe_entrypoint_path(repo),
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = False\n",
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=True)
        assert (
            "tool governance probe entrypoint must contain module-level canary guard "
            "THREADLIGHT_CANARY_ONLY = True"
        ) in gaps
        assert not evidence_path.exists()

        result, rerun_gaps = sc.validate_tool_governance_probe(
            repo, manifest, run_probe=False
        )
        assert result["status"] == "fail"
        assert "tool governance probe evidence missing" in rerun_gaps[0]


def test_probe_failed_run_cannot_reuse_stale_evidence() -> None:
    with _governed_repo_copy("probe-stale-evidence") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        evidence_path = _probe_evidence_path(repo)
        assert evidence_path.exists()
        _write(
            _probe_entrypoint_path(repo),
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\nraise SystemExit(7)\n",
        )

        result, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=True)

        assert result["status"] == "fail"
        assert "evidence" not in result
        assert not evidence_path.exists()
        assert (
            "tool governance probe failed: tests/tool_governance_probe.py exited 7"
            in gaps
        )


def test_probe_canary_guard_accepts_module_level_annassign(monkeypatch) -> None:
    with _governed_repo_copy("probe-canary-annassign") as repo:
        _write(
            _probe_entrypoint_path(repo),
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY: bool = True\n",
        )
        calls: list[tuple[object, ...]] = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return sc.subprocess.CompletedProcess(args[0], 0)

        monkeypatch.setattr(sc.subprocess, "run", fake_run)
        gaps: list[str] = []

        entrypoint, evidence = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert entrypoint == "tests/tool_governance_probe.py"
        assert evidence == "tests/tool-governance-probe-manifest.json"
        assert gaps == []
        assert calls


def test_probe_canary_guard_accepts_utf8_bom_prefixed_source(monkeypatch) -> None:
    with _governed_repo_copy("probe-canary-bom") as repo:
        _write(
            _probe_entrypoint_path(repo),
            "\ufeff#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\n",
        )
        calls: list[tuple[object, ...]] = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return sc.subprocess.CompletedProcess(args[0], 0)

        monkeypatch.setattr(sc.subprocess, "run", fake_run)
        gaps: list[str] = []

        entrypoint, evidence = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert entrypoint == "tests/tool_governance_probe.py"
        assert evidence == "tests/tool-governance-probe-manifest.json"
        assert gaps == []
        assert calls


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "#!/usr/bin/env python3\n# THREADLIGHT_CANARY_ONLY = True\n",
            "tool governance probe entrypoint must contain module-level canary "
            "guard THREADLIGHT_CANARY_ONLY = True",
        ),
        (
            '#!/usr/bin/env python3\n"""THREADLIGHT_CANARY_ONLY = True"""\n',
            "tool governance probe entrypoint must contain module-level canary "
            "guard THREADLIGHT_CANARY_ONLY = True",
        ),
        (
            "#!/usr/bin/env python3\nif True:\n    THREADLIGHT_CANARY_ONLY = True\n",
            "tool governance probe entrypoint must contain module-level canary "
            "guard THREADLIGHT_CANARY_ONLY = True",
        ),
    ],
)
def test_probe_canary_guard_rejects_spoofed_or_nested_assignment(
    monkeypatch, body: str, expected: str,
) -> None:
    with _governed_repo_copy("probe-canary-spoof") as repo:
        _write(_probe_entrypoint_path(repo), body)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, _ = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert expected in gaps


def test_probe_canary_guard_reports_syntax_error(monkeypatch) -> None:
    with _governed_repo_copy("probe-canary-syntax-error") as repo:
        _write(
            _probe_entrypoint_path(repo),
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\nif (\n",
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, _ = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert any(
            gap.startswith(
                "tool governance probe entrypoint has invalid Python syntax: "
                "tests/tool_governance_probe.py"
            )
            for gap in gaps
        )


def test_probe_canary_guard_reports_invalid_encoding(monkeypatch) -> None:
    with _governed_repo_copy("probe-canary-invalid-encoding") as repo:
        _write(
            _probe_entrypoint_path(repo),
            "# coding: definitely-not-an-encoding\nTHREADLIGHT_CANARY_ONLY = True\n",
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, _ = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert any(
            gap.startswith(
                "tool governance probe entrypoint has invalid Python syntax: "
                "tests/tool_governance_probe.py"
            )
            for gap in gaps
        )


def test_probe_canary_guard_reports_invalid_utf8_bytes(monkeypatch) -> None:
    with _governed_repo_copy("probe-canary-invalid-utf8") as repo:
        path = _probe_entrypoint_path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"THREADLIGHT_CANARY_ONLY = True\n\xff\n")

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, _ = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert any(
            gap.startswith(
                "tool governance probe entrypoint has invalid Python syntax: "
                "tests/tool_governance_probe.py"
            )
            for gap in gaps
        )


def test_probe_nonzero_exit_is_visible_gap() -> None:
    with _governed_repo_copy("probe-nonzero-exit") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        _write(
            _probe_entrypoint_path(repo),
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\nraise SystemExit(7)\n",
        )
        _, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=True)
        assert (
            "tool governance probe failed: tests/tool_governance_probe.py exited 7"
            in gaps
        )


def test_probe_entrypoint_traversal_is_rejected(monkeypatch) -> None:
    with _governed_repo_copy("probe-entrypoint-traversal") as repo:
        outside_entrypoint = repo.parent / "outside-probe.py"
        _write(
            outside_entrypoint,
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\n",
        )
        _mutate_probe_adapter(
            repo,
            lambda adapter: adapter["probe"].update({"entrypoint": "../outside-probe.py"}),
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, _ = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert (
            "tool governance probe entrypoint must resolve within repo root: "
            "../outside-probe.py"
        ) in gaps


def test_probe_entrypoint_traversal_is_rejected_without_execution() -> None:
    with _governed_repo_copy("probe-entrypoint-traversal-noexec") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        outside_entrypoint = repo.parent / "outside-noexec-probe.py"
        _write(
            outside_entrypoint,
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\n",
        )
        _mutate_probe_adapter(
            repo,
            lambda adapter: adapter["probe"].update(
                {"entrypoint": "../outside-noexec-probe.py"}
            ),
        )
        _sync_probe_evidence_metadata(repo)

        result, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)

        assert result["status"] == "fail"
        assert (
            "tool governance probe entrypoint must resolve within repo root: "
            "../outside-noexec-probe.py"
        ) in gaps


def test_probe_evidence_traversal_is_rejected_before_loading() -> None:
    with _governed_repo_copy("probe-evidence-traversal") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        outside_evidence = repo.parent / "outside-evidence.json"
        _write_json(outside_evidence, _read_json(_probe_evidence_path(repo)))
        _mutate_probe_adapter(
            repo,
            lambda adapter: adapter["probe"].update({"evidence": "../outside-evidence.json"}),
        )

        result, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)

        assert result["status"] == "fail"
        assert "evidence" not in result
        assert (
            "tool governance probe evidence must resolve within repo root: "
            "../outside-evidence.json"
        ) in gaps


def test_probe_evidence_symlink_is_rejected_before_cleanup(monkeypatch) -> None:
    with _governed_repo_copy("probe-evidence-symlink") as repo:
        evidence_path = _probe_evidence_path(repo)
        target_path = repo / "tests" / "symlink-target.json"
        _write_json(target_path, {"preserve": True})
        evidence_path.unlink()
        evidence_path.symlink_to(target_path)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, evidence = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert evidence is None
        assert target_path.exists()
        assert evidence_path.is_symlink()
        assert (
            "tool governance probe evidence must not be a symlink: "
            "tests/tool-governance-probe-manifest.json"
        ) in gaps


def test_probe_entrypoint_symlink_escape_is_rejected(monkeypatch) -> None:
    with _governed_repo_copy("probe-entrypoint-symlink-escape") as repo:
        outside_entrypoint = repo.parent / "outside-symlink-probe.py"
        _write(
            outside_entrypoint,
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\n",
        )
        symlink_path = repo / "tests" / "symlinked-probe.py"
        try:
            symlink_path.symlink_to(outside_entrypoint)
        except OSError as exc:
            pytest.skip(f"symlink creation not supported: {exc}")
        _mutate_probe_adapter(
            repo,
            lambda adapter: adapter["probe"].update({"entrypoint": "tests/symlinked-probe.py"}),
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(sc.subprocess, "run", fail_if_called)
        gaps: list[str] = []

        _, _ = sc._run_governance_probe(repo, _probe_adapter(repo), gaps)

        assert (
            "tool governance probe entrypoint must resolve within repo root: "
            "tests/symlinked-probe.py"
        ) in gaps


def test_probe_entrypoint_symlink_escape_is_rejected_without_execution() -> None:
    with _governed_repo_copy("probe-entrypoint-symlink-noexec") as repo:
        manifest = _read_json(repo / "specs" / "manifest.json")
        outside_entrypoint = repo.parent / "outside-symlink-noexec-probe.py"
        _write(
            outside_entrypoint,
            "#!/usr/bin/env python3\nTHREADLIGHT_CANARY_ONLY = True\n",
        )
        symlink_path = repo / "tests" / "symlinked-noexec-probe.py"
        try:
            symlink_path.symlink_to(outside_entrypoint)
        except OSError as exc:
            pytest.skip(f"symlink creation not supported: {exc}")
        _mutate_probe_adapter(
            repo,
            lambda adapter: adapter["probe"].update(
                {"entrypoint": "tests/symlinked-noexec-probe.py"}
            ),
        )
        _sync_probe_evidence_metadata(repo)

        result, gaps = sc.validate_tool_governance_probe(repo, manifest, run_probe=False)

        assert result["status"] == "fail"
        assert (
            "tool governance probe entrypoint must resolve within repo root: "
            "tests/symlinked-noexec-probe.py"
        ) in gaps


def test_phase_postdeploy_emits_tool_governance_probe_summary(monkeypatch) -> None:
    with _governed_repo_copy("phase-postdeploy-tool-governance") as repo:
        manifest_path = repo / "specs" / "manifest.json"
        out_path = repo / "tests" / "postdeploy-manifest.json"

        def fake_az(*args: str, capture: bool = True) -> str:
            if args[:2] == ("resource", "list") and "--resource-type" not in args:
                return json.dumps(
                    [{"type": "Microsoft.App/containerApps", "name": "fixture-mcp"}]
                )
            if args[:2] == ("containerapp", "list"):
                return json.dumps(
                    [
                        {
                            "name": "fixture-mcp",
                            "fqdn": "mcp.contoso.example",
                            "image": "contoso/mcp:latest",
                            "state": "Running",
                        }
                    ]
                )
            if args[:3] == ("containerapp", "job", "list"):
                return "[]"
            if args[:2] == ("resource", "list") and "--resource-type" in args:
                return "[]"
            raise AssertionError(f"unexpected az args: {args}")

        monkeypatch.setattr(sc, "_az", fake_az)
        rc = sc.phase_postdeploy(manifest_path, out_path, "fixture-rg", repo_root=repo)
        emitted = _read_json(out_path)
        assert rc == 0
        assert emitted["gaps"] == []
        assert emitted["tool_governance"]["status"] == "pass"
        assert emitted["tool_governance"]["evidence"] == (
            "tests/tool-governance-probe-manifest.json"
        )
        assert emitted["tool_governance"]["contract_sha256"].startswith("sha256:")
        assert "vectors" not in emitted["tool_governance"]
        assert "audit_field_results" not in emitted["tool_governance"]


# ---------------------------------------------------------------------------
# Canonical <-> example parity: the two safe_check.py copies never drift.
# ---------------------------------------------------------------------------

def test_example_safe_check_is_byte_identical_to_canonical() -> None:
    assert EXAMPLE_COPY.exists(), f"example copy missing at {EXAMPLE_COPY}"
    canonical = SCRIPT.read_bytes()
    example = EXAMPLE_COPY.read_bytes()
    assert canonical == example, (
        "examples/returns-triage-governed/tests/safe_check.py has drifted from "
        "skills/threadlight-safe-check/scripts/safe_check.py — the example copy "
        "must be re-synchronized byte-for-byte."
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as exc:
                failures += 1
                print(f"[FAIL] {name}: {exc}")
    print(f"\n=== {failures} failure(s) ===")
    sys.exit(1 if failures else 0)
