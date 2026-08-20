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
    with _scratch_dir(name) as root:
        shutil.copytree(GOVERNED_FIXTURE, root, dirs_exist_ok=True)
        yield root


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
