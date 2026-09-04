from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = REPO_ROOT / "skills" / "threadlight-design" / "SKILL.md"
TEMPLATE_PATH = (
    REPO_ROOT / "skills" / "threadlight-design" / "references" / "speckit-template.md"
)
POLICY_PATH = (
    REPO_ROOT / "skills" / "threadlight-design" / "references" / "runtime-policy.json"
)
SCHEMA_PATH = (
    REPO_ROOT
    / "skills"
    / "threadlight-design"
    / "references"
    / "governance-contract.schema.json"
)
MODULE_NAME = "skills._shared.governance"
MODULE_PATH = REPO_ROOT / "skills" / "_shared" / "governance.py"


def governance_module():
    assert MODULE_PATH.exists(), "shared governance module missing"
    importlib.invalidate_caches()
    return importlib.import_module(MODULE_NAME)


def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def template_text() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def runtime_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def governance_schema() -> dict:
    assert SCHEMA_PATH.exists(), "governance contract schema missing"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def build_jsonschema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = governance_schema()
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def valid_contract() -> dict:
    return {
        "framework": "microsoft-agent-framework",
        "governance": {
            "mode": "selective",
            "environment_modes": {
                "development": "evaluate_only",
                "staging": "evaluate_only",
                "preproduction": "enforce",
                "production": "enforce",
            },
            "lifecycle_bindings": [
                {
                    "lifecycle_point": "input",
                    "policy_binding": "request-ingress-v1",
                    "enforcement_path": "governed-tool-gateway",
                    "safe_principles": ["scope"],
                    "requires": ["signed-policy-bundle"],
                }
            ],
        },
        "tools": [
            {
                "id": "returns_apply_decision",
                "consequence": "write",
                "policy_binding": "returns-write-v1",
                "enforcement_path": "governed-tool-gateway",
                "intervention_points": ["pre_tool_call", "post_tool_call"],
                "safe_principles": ["scope", "audit"],
                "requires": ["human-approval-record"],
            }
        ],
    }


def validate_governance_contract(document: dict, *, deployment_target: str = "customer-pilot"):
    governance = governance_module()
    build_jsonschema_validator().validate(document)

    mode = document["governance"]["mode"]
    if mode == "off" and document["governance"]["lifecycle_bindings"]:
        raise governance.GovernanceContractError(
            "off mode forbids bound lifecycle bindings"
        )
    for binding in document["governance"]["lifecycle_bindings"]:
        governance.normalize_tool(
            {
                "id": f"lifecycle.{binding['lifecycle_point']}",
                "consequence": "read",
                "policy_binding": binding["policy_binding"],
                "enforcement_path": binding["enforcement_path"],
                "intervention_points": [binding["lifecycle_point"]],
            },
            runtime=document["framework"],
        )

    normalized_tools = []
    seen_ids = set()
    consequential_or_unknown = {
        "write",
        "external-egress",
        "irreversible",
        "unknown",
    }

    for raw_tool in document["tools"]:
        if isinstance(raw_tool, str):
            normalized = governance.normalize_tool(
                raw_tool, runtime=document["framework"]
            )
            acceptance_record = None
        else:
            normalized = governance.normalize_tool(
                {
                    "id": raw_tool["id"],
                    "consequence": raw_tool["consequence"],
                    "policy_binding": raw_tool["policy_binding"],
                    "enforcement_path": raw_tool["enforcement_path"],
                    "intervention_points": raw_tool["intervention_points"],
                },
                runtime=document["framework"],
            )
            acceptance_record = raw_tool.get("acceptance_record")

        if normalized["id"] in seen_ids:
            raise governance.GovernanceContractError("tools.id values must be unique")
        seen_ids.add(normalized["id"])

        if mode == "off" and normalized["enforcement_path"] != "none":
            raise governance.GovernanceContractError(
                "off mode forbids bound tools"
            )

        if (
            mode == "comprehensive"
            and normalized["consequence"] in consequential_or_unknown
            and normalized["enforcement_path"] == "none"
        ):
            raise governance.GovernanceContractError(
                "comprehensive governance rejects unsupported provider-hosted consequential or unknown tools"
            )

        if (
            deployment_target == "production-bound"
            and normalized["consequence"] in consequential_or_unknown
            and normalized["enforcement_path"] == "none"
            and acceptance_record is None
        ):
            raise governance.GovernanceContractError(
                "production-bound unbound consequential or unknown tools require acceptance_record"
            )

        normalized_tools.append(normalized)

    return normalized_tools


def test_template_declares_governance_modes_and_contract_tokens():
    text = template_text()

    for fragment in (
        "governance.mode",
        "off | selective | comprehensive",
        "development: evaluate_only",
        "staging: evaluate_only",
        "preproduction: enforce",
        "production: enforce",
        "policy_binding",
        "enforcement_path",
        "intervention_points",
        "safe_principles",
        "requires",
        "acceptance_record",
        "review_date",
        "expiry",
        "Legacy string tools remain valid",
        "unknown/unbound",
    ):
        assert fragment in text


def test_skill_declares_governance_interview_and_no_silent_runtime_switch():
    text = skill_text()

    for fragment in (
        "write / external-egress / irreversible",
        "one question at a time",
        "off`, `selective`, and `comprehensive`",
        "do not silently change framework/runtime",
        "offer MAF",
        "retain the selected runtime unless the operator changes it",
        "production-bound",
        "acceptance record",
    ):
        assert fragment in text


def test_runtime_policy_declares_exact_governance_compatibility_map_and_routes():
    assert runtime_policy() == {
        "schema": "threadlight.runtime-policy/v1",
        "version": 2,
        "contract_version": "2.0.0",
        "last_reviewed": "2026-08-17",
        "authority": {
            "repository": "aiappsgbb/threadlight-skills",
            "path": "skills/threadlight-design/references/runtime-policy.json",
            "cross_repository_consumers": False,
        },
        "region_policy": {
            "default": "eastus2",
            "eu_residency": ["swedencentral"],
            "selection_rule": "Use an EU region only when the complete required resource set is available there.",
        },
        "selectors": {
            "frameworks": ["github-copilot-sdk", "microsoft-agent-framework"],
            "runtime_shapes": ["agent", "workflow"],
            "protocols": ["invocations", "responses"],
        },
        "governance_compatibility": {
            "github-copilot-sdk": ["none", "governed-tool-gateway"],
            "microsoft-agent-framework": [
                "none",
                "local-agent-hooks",
                "governed-tool-gateway",
            ],
        },
        "default": {
            "framework": "github-copilot-sdk",
            "runtime_shape": "agent",
            "protocol": "invocations",
            "policy_route": "default-agent",
        },
        "compatible_combinations": [
            {
                "framework": "github-copilot-sdk",
                "runtime_shape": "agent",
                "protocol": "invocations",
            },
            {
                "framework": "microsoft-agent-framework",
                "runtime_shape": "agent",
                "protocol": "responses",
            },
            {
                "framework": "microsoft-agent-framework",
                "runtime_shape": "workflow",
                "protocol": "responses",
            },
        ],
        "routes": [
            {
                "id": "explicit-supported-choice",
                "priority": 1,
                "decision_date": "2026-08-17",
                "permanent": True,
                "selection": "operator",
                "allowed_combinations_ref": "compatible_combinations",
                "when": "operator explicitly chooses a framework/runtime_shape/protocol tuple listed in compatible_combinations",
                "requires_resolved_signals": True,
                "blocked_when": [
                    "workflow_model=workflow",
                    "requires_toolbox",
                    "requires_custom_python_tools",
                    "requires_file_generation",
                    "latency_sensitive_data_queries",
                ],
                "rationale": "Honor a compatible explicit operator override before house routing, unless a blocked_when capability signal is active — the capability route that owns that signal takes the case instead, regardless of compatible_combinations membership. requires_resolved_signals is a separate gate: false-vs-unknown is never ambiguous here, so this route also refuses to fire while capability_signals.unresolved_signals is non-empty — an unresolved signal must be resolved (or the case escalated) before an operator's explicit choice can be honored, since it could still turn out to be the one a blocked_when route owns.",
            },
            {
                "id": "deterministic-workflow",
                "priority": 2,
                "decision_date": "2026-08-17",
                "permanent": True,
                "when": "workflow_model=workflow",
                "framework": "microsoft-agent-framework",
                "runtime_shape": "workflow",
                "protocol": "responses",
                "rationale": "Deterministic multi-phase flows need the MAF workflow runtime.",
            },
            {
                "id": "maf-agent-capabilities",
                "priority": 3,
                "decision_date": "2026-08-17",
                "permanent": True,
                "when": "requires_toolbox || requires_custom_python_tools || requires_file_generation || latency_sensitive_data_queries",
                "framework": "microsoft-agent-framework",
                "runtime_shape": "agent",
                "protocol": "responses",
                "rationale": "Toolbox, custom tools, file generation, and fast data queries fit MAF agent best today.",
            },
            {
                "id": "default-agent",
                "priority": 4,
                "decision_date": "2026-08-05",
                "expiry_condition": "Responses works end to end for the generated hosted runtime and every documented channel.",
                "when": "no higher-priority route matches",
                "framework": "github-copilot-sdk",
                "runtime_shape": "agent",
                "protocol": "invocations",
                "rationale": "Keep the proven GHCP Invocations path until Responses works end to end.",
            },
        ],
    }


def test_governance_schema_uses_shared_enums_and_disallows_extra_properties():
    governance = governance_module()
    schema = governance_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["framework"]["enum"] == [
        "github-copilot-sdk",
        "microsoft-agent-framework",
    ]
    assert set(schema["definitions"]["governance"]["properties"]["mode"]["enum"]) == set(
        governance.GOVERNANCE_MODES
    )
    assert schema["definitions"]["governance"]["additionalProperties"] is False
    assert schema["definitions"]["structuredTool"]["additionalProperties"] is False
    assert set(
        schema["definitions"]["structuredTool"]["properties"]["consequence"]["enum"]
    ) == set(governance.CONSEQUENCES)
    assert set(
        schema["definitions"]["structuredTool"]["properties"]["enforcement_path"]["enum"]
    ) == set(governance.ENFORCEMENT_PATHS)
    assert schema["definitions"]["acceptanceRecord"]["additionalProperties"] is False


def test_governance_schema_accepts_valid_selective_contract():
    document = valid_contract()
    validator = build_jsonschema_validator()

    assert list(validator.iter_errors(document)) == []
    assert validate_governance_contract(document) == [
        {
            "id": "returns_apply_decision",
            "consequence": "write",
            "policy_binding": "returns-write-v1",
            "enforcement_path": "governed-tool-gateway",
            "intervention_points": ["pre_tool_call", "post_tool_call"],
        }
    ]


def test_governance_schema_accepts_lifecycle_only_contract_without_tools():
    document = valid_contract()
    document["tools"] = []
    validator = build_jsonschema_validator()

    assert list(validator.iter_errors(document)) == []
    assert validate_governance_contract(document) == []


def test_validation_helper_rejects_ghcp_local_agent_hooks():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"][0]["enforcement_path"] = "local-agent-hooks"

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        validate_governance_contract(document)


def test_validation_helper_rejects_off_mode_with_bound_contract():
    governance = governance_module()
    document = valid_contract()
    document["governance"]["mode"] = "off"

    with pytest.raises(governance.GovernanceContractError, match="off"):
        validate_governance_contract(document)


def test_validation_helper_rejects_ghcp_local_agent_hooks_for_lifecycle_binding():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["governance"]["lifecycle_bindings"][0]["enforcement_path"] = (
        "local-agent-hooks"
    )

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        validate_governance_contract(document)


def test_selective_none_allowed_for_read_tool():
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"] = [
        {
            "id": "returns_get_case",
            "consequence": "read",
            "policy_binding": "none",
            "enforcement_path": "none",
            "intervention_points": [],
            "safe_principles": ["scope"],
            "requires": [],
        }
    ]

    assert validate_governance_contract(document) == [
        {
            "id": "returns_get_case",
            "consequence": "read",
            "policy_binding": None,
            "enforcement_path": "none",
            "intervention_points": [],
        }
    ]


def test_validation_helper_rejects_duplicate_tool_ids():
    governance = governance_module()
    document = valid_contract()
    document["tools"].append(
        {
            "id": "returns_apply_decision",
            "consequence": "read",
            "policy_binding": "returns-read-v1",
            "enforcement_path": "governed-tool-gateway",
            "intervention_points": ["pre_tool_call"],
            "safe_principles": ["scope"],
            "requires": [],
        }
    )

    with pytest.raises(governance.GovernanceContractError, match="unique"):
        validate_governance_contract(document)


def test_validation_helper_rejects_comprehensive_provider_hosted_consequential_tool():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["governance"]["mode"] = "comprehensive"
    document["tools"][0]["policy_binding"] = "none"
    document["tools"][0]["enforcement_path"] = "none"
    document["tools"][0]["intervention_points"] = []

    with pytest.raises(governance.GovernanceContractError, match="comprehensive"):
        validate_governance_contract(document)


def test_production_bound_unknown_or_unbound_requires_acceptance_record():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"] = [
        {
            "id": "legacy_export",
            "consequence": "unknown",
            "policy_binding": "none",
            "enforcement_path": "none",
            "intervention_points": [],
            "safe_principles": ["audit"],
            "requires": [],
        }
    ]

    with pytest.raises(governance.GovernanceContractError, match="acceptance_record"):
        validate_governance_contract(document, deployment_target="production-bound")

    document["tools"][0]["acceptance_record"] = {
        "owner": "risk-owner@contoso.com",
        "justification": "Temporary waiver while the governed gateway is being onboarded.",
        "review_date": "2026-09-04",
        "expiry": "2026-12-31",
    }

    assert validate_governance_contract(
        document, deployment_target="production-bound"
    ) == [
        {
            "id": "legacy_export",
            "consequence": "unknown",
            "policy_binding": None,
            "enforcement_path": "none",
            "intervention_points": [],
        }
    ]


def test_legacy_string_tool_remains_allowed_but_normalizes_to_unknown_unbound():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"] = ["legacy_export"]

    assert validate_governance_contract(document) == [
        governance.normalize_tool("legacy_export", runtime="github-copilot-sdk")
    ]


def test_public_wording_uses_correct_governance_layers_and_no_policy_only_enforcement():
    combined = skill_text() + "\n" + template_text()

    for fragment in (
        "SAFE is the methodology",
        "ACS is the PDP",
        "Agent Hooks is the runtime contract",
        "host/gateway is the PEP",
        "AGT is the toolkit",
        "ASSERT is the assurance layer",
        "policy bundles, templates, and CI checks do not enforce runtime behavior on their own",
    ):
        assert fragment in combined
