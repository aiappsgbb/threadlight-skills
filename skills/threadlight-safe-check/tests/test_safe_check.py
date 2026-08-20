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

import copy
import json
import sys
import tempfile
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

sys.path.insert(0, str(SCRIPT.parent))
import safe_check as sc  # noqa: E402


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

def _make_repo_with_nested_manifest() -> tuple[Path, Path]:
    """Build <root>/{azure.yaml, infra/mcp-config.json} with a manifest nested at
    <root>/a/b/specs/manifest.json (a non-default path). Returns (root, manifest).

    Uses ``tempfile.mkdtemp`` (not the pytest ``tmp_path`` fixture) so the file
    is exercised identically under pytest and the standalone runner below."""
    root = Path(tempfile.mkdtemp()) / "root"
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
    return root, manifest


def test_repo_root_prefers_explicit_cli_root() -> None:
    root, manifest = _make_repo_with_nested_manifest()
    # An explicit root (what the CLI passes as Path.cwd()) always wins, even for a
    # deeply nested manifest whose parent.parent is NOT the root.
    assert sc._repo_root_for_manifest(manifest, explicit_root=root) == root
    assert manifest.parent.parent != root  # precondition: parent.parent is wrong


def test_repo_root_discovers_nearest_marked_ancestor_for_nested_manifest() -> None:
    root, manifest = _make_repo_with_nested_manifest()
    # No explicit root: walk up to the nearest ancestor carrying a repo marker
    # (azure.yaml / infra), NOT the manifest's grandparent (a/b).
    resolved = sc._repo_root_for_manifest(manifest)
    assert resolved == root
    assert resolved != manifest.parent.parent


def test_repo_root_default_layout_unaffected() -> None:
    root = Path(tempfile.mkdtemp()) / "root"
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
    root = Path(tempfile.mkdtemp()) / "bare"
    (root / "specs").mkdir(parents=True)
    manifest = root / "specs" / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    assert sc._repo_root_for_manifest(manifest) == manifest.parent.parent == root


def test_nested_manifest_binding_gap_uses_correct_root() -> None:
    # End-to-end: with the explicit CLI root, the nested manifest still resolves
    # the effective mcp-config under <root>/infra and flags the mock binding.
    root, manifest = _make_repo_with_nested_manifest()
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
