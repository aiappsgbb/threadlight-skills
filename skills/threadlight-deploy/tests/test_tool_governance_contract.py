from __future__ import annotations

import json
import re
from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"
SKILL = SKILL_PATH.read_text(encoding="utf-8")
NORMALIZED_SKILL = re.sub(r"\s+", " ", SKILL).strip()


def _adapter_manifest() -> dict[str, object]:
    blocks = re.findall(
        r"```json\n(\{\n"
        r'  "schema": "threadlight\.tool-governance-adapter/v1".*?'
        r"\n\})\n```",
        SKILL,
        re.S,
    )
    assert blocks, "SKILL.md must include one adapter-manifest JSON example"
    assert len(blocks) == 1, (
        "Parse only the adapter-manifest JSON block. Found multiple matching "
        "tool-governance adapter examples."
    )
    return json.loads(blocks[0])


def _assert_phrase_present(text: str, *, label: str) -> None:
    assert text in NORMALIZED_SKILL, f"Missing {label}: {text}"


def test_skill_version_is_pinned() -> None:
    assert 'version: "1.7.0"' in SKILL


def test_ghcp_governance_contract_is_explicitly_mcp_only() -> None:
    required = (
        "GHCP SDK tools are MCP-bound",
        "only at the declared `mcp-server` or `gateway` boundary",
        "GHCP + `agent-middleware` is an explicit deployment gap",
        "never claim that `CopilotClient` or `InvocationAgentServerHost` provides in-process enforcement",
        "unsupported point stops deployment",
        "no prompt fallback",
    )
    for text in required:
        _assert_phrase_present(text, label="GHCP governance rule")


def test_maf_rules_pin_real_foundry_agt_boundaries() -> None:
    required = (
        "invoke/use `foundry-agt`",
        "installed skill's real deterministic pre-tool boundary",
        "Do not invent AGT import or API names",
        "every executor capable of invoking the tool",
        "MAF MCP tools only bind at the exact declared supported boundary",
    )
    for text in required:
        _assert_phrase_present(text, label="MAF governance rule")


def test_contract_semantics_are_preserved_verbatim() -> None:
    for field in (
        "`tool_name`",
        "`action_class`",
        "`decision`",
        "`gate_id`",
        "`enforcement_point`",
        "`policy_id`",
    ):
        _assert_phrase_present(field, label="preserved contract field")

    audit_fields = (
        "`event_id`",
        "`event_type`",
        "`timestamp`",
        "`correlation_id`",
        "`contract_sha256`",
        "`policy_id`",
        "`tool_name`",
        "`action_class`",
        "`decision`",
        "`enforcement_point`",
        "`adapter_id`",
        "`actor_id`",
    )
    for field in audit_fields:
        _assert_phrase_present(field, label="required audit field")


def test_runtime_capability_matrix_lists_supported_boundaries() -> None:
    matrix_rows = (
        "| MAF Agent in-process tool |",
        "| MAF Workflow executor tool |",
        "| MAF MCP tool |",
        "| GHCP SDK MCP tool |",
        "| GHCP SDK in-process tool | unsupported |",
    )
    for row in matrix_rows:
        _assert_phrase_present(row, label="runtime capability matrix row")


def test_adapter_manifest_example_is_machine_parseable_and_complete() -> None:
    manifest = _adapter_manifest()
    assert set(manifest) == {
        "schema",
        "contract_sha256",
        "runtime",
        "bindings",
        "audit",
        "probe",
    }
    assert manifest["schema"] == "threadlight.tool-governance-adapter/v1"
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["contract_sha256"])

    runtime = manifest["runtime"]
    assert runtime == {
        "framework": "github-copilot-sdk",
        "runtime_shape": "agent",
        "protocol": "invocations",
    }

    bindings = manifest["bindings"]
    assert isinstance(bindings, list) and bindings, "bindings must be a non-empty list"
    binding = bindings[0]
    assert binding["tool_name"] == "returns_apply_decision"
    assert binding["enforcement_point"] == "mcp-server"
    assert binding["adapter_id"]
    assert binding["policy_artifact"].startswith("policies/tool-governance/generated/")
    assert binding["wire_signals"]
    assert any(
        signal["kind"] == "mcp-server-policy-binding"
        for signal in binding["wire_signals"]
    )

    assert manifest["audit"] == {
        "schema": "threadlight.tool-governance-audit/v1",
        "sink": "application-insights",
    }
    assert manifest["probe"] == {
        "entrypoint": "tests/tool_governance_probe.py",
        "evidence": "tests/tool-governance-probe-manifest.json",
    }


def test_generated_artifact_tree_and_hash_requirements_are_documented() -> None:
    for path in (
        "policies/tool-governance/adapter-manifest.json",
        "generated/<adapter policy>",
        "tests/tool_governance_probe.py",
        "tests/tool-governance-probe-manifest.json",
    ):
        _assert_phrase_present(path, label="generated artifact path")

    _assert_phrase_present(
        "Every generated policy artifact contains the same canonical `contract_sha256` as `adapter-manifest.json`",
        label="policy hash propagation rule",
    )
    _assert_phrase_present("file path alone is not proof", label="policy hash proof rule")


def test_probe_contract_is_canary_only_and_non_mutating() -> None:
    required = (
        "THREADLIGHT_CANARY_ONLY = True",
        '"id": "allow-canary"',
        '"expected_execution_count": 1',
        '"observed_execution_count": 1',
        '"id": "deny-canary"',
        '"expected_execution_count": 0',
        '"observed_execution_count": 0',
        '"correlation_id": "probe-allow-001"',
        '"correlation_id": "probe-deny-001"',
        '"decision_event_ids"',
        '"outcome_event_ids"',
        "conditional vector if the contract declares one",
        "production mutation endpoints are forbidden",
    )
    for text in required:
        _assert_phrase_present(text, label="probe contract text")


def test_reserved_wire_signals_include_future_dotnet_detection_only() -> None:
    required = (
        "pre-tool-policy-binding",
        "mcp-server-policy-binding",
        "gateway-policy-binding",
        "dotnet-with-governance",
        ".WithGovernance()",
        "Do not implement the .NET adapter in this task",
    )
    for text in required:
        _assert_phrase_present(text, label="wire-signal rule")
