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


def valid_acceptance_record() -> dict:
    return {
        "owner": "risk-owner@contoso.com",
        "justification": "Temporary waiver while the governed gateway is being onboarded.",
        "review_date": "2026-09-04T00:00:00Z",
        "expiry": "2026-12-31T00:00:00Z",
    }


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
    assert schema["properties"]["framework"]["enum"] == sorted(
        governance.CONTRACT_FRAMEWORKS
    )
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
    assert "shared validator is authoritative for cross-record rules not schema-expressible" in schema[
        "description"
    ]


def test_governance_schema_accepts_valid_selective_contract():
    governance = governance_module()
    document = valid_contract()
    validator = build_jsonschema_validator()

    assert list(validator.iter_errors(document)) == []
    assert governance.validate_governance_contract(document) == {
        "framework": "microsoft-agent-framework",
        "deployment_target": "customer-pilot",
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


def test_governance_schema_accepts_lifecycle_only_contract_without_tools():
    governance = governance_module()
    document = valid_contract()
    document["tools"] = []
    validator = build_jsonschema_validator()

    assert list(validator.iter_errors(document)) == []
    assert governance.validate_governance_contract(document) == {
        "framework": "microsoft-agent-framework",
        "deployment_target": "customer-pilot",
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
        "tools": [],
    }


def test_governance_schema_rejects_off_mode_with_bound_lifecycle_or_tool():
    document = valid_contract()
    document["governance"]["mode"] = "off"
    validator = build_jsonschema_validator()

    messages = [error.message for error in validator.iter_errors(document)]
    assert messages
    assert any("lifecycle_bindings" in message or "enforcement_path" in message for message in messages)


def test_governance_schema_rejects_comprehensive_unbound_consequential_and_unknown_tools():
    document = valid_contract()
    document["governance"]["mode"] = "comprehensive"
    document["tools"] = [
        {
            "id": "returns_apply_decision",
            "consequence": "write",
            "policy_binding": "none",
            "enforcement_path": "none",
            "intervention_points": [],
            "safe_principles": ["scope", "audit"],
            "requires": ["human-approval-record"],
        },
        "legacy_export",
    ]
    validator = build_jsonschema_validator()

    messages = [error.message for error in validator.iter_errors(document)]
    assert messages
    assert any("none" in message or "structuredTool" in message for message in messages)


def test_validation_helper_rejects_ghcp_local_agent_hooks():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"][0]["enforcement_path"] = "local-agent-hooks"

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        governance.validate_governance_contract(document)


def test_validation_helper_rejects_off_mode_with_bound_contract():
    governance = governance_module()
    document = valid_contract()
    document["governance"]["mode"] = "off"

    with pytest.raises(governance.GovernanceContractError, match="off"):
        governance.validate_governance_contract(document)


def test_validation_helper_rejects_ghcp_local_agent_hooks_for_lifecycle_binding():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["governance"]["lifecycle_bindings"][0]["enforcement_path"] = (
        "local-agent-hooks"
    )

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        governance.validate_governance_contract(document)


def test_selective_none_allowed_for_read_tool():
    governance = governance_module()
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

    assert governance.validate_governance_contract(document) == {
        "framework": "github-copilot-sdk",
        "deployment_target": "customer-pilot",
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
                "id": "returns_get_case",
                "consequence": "read",
                "policy_binding": None,
                "enforcement_path": "none",
                "intervention_points": [],
                "safe_principles": ["scope"],
                "requires": [],
            }
        ],
    }


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
        governance.validate_governance_contract(document)


def test_validation_helper_rejects_comprehensive_provider_hosted_consequential_tool():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["governance"]["mode"] = "comprehensive"
    document["tools"][0]["policy_binding"] = "none"
    document["tools"][0]["enforcement_path"] = "none"
    document["tools"][0]["intervention_points"] = []

    with pytest.raises(governance.GovernanceContractError, match="comprehensive"):
        governance.validate_governance_contract(document)


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
        governance.validate_governance_contract(
            document, deployment_target="production-bound"
        )

    document["tools"][0]["acceptance_record"] = valid_acceptance_record()

    assert governance.validate_governance_contract(
        document, deployment_target="production-bound"
    ) == {
        "framework": "github-copilot-sdk",
        "deployment_target": "production-bound",
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
                "id": "legacy_export",
                "consequence": "unknown",
                "policy_binding": None,
                "enforcement_path": "none",
                "intervention_points": [],
                "safe_principles": ["audit"],
                "requires": [],
                "acceptance_record": valid_acceptance_record(),
            }
        ],
    }


def test_production_bound_unbound_write_requires_acceptance_record():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"][0]["policy_binding"] = "none"
    document["tools"][0]["enforcement_path"] = "none"
    document["tools"][0]["intervention_points"] = []

    with pytest.raises(governance.GovernanceContractError, match="acceptance_record"):
        governance.validate_governance_contract(
            document, deployment_target="production-bound"
        )


def test_validation_helper_rejects_partial_acceptance_record_missing_expiry():
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
            "acceptance_record": {
                "owner": "risk-owner@contoso.com",
                "justification": "Temporary waiver while the governed gateway is being onboarded.",
                "review_date": "2026-09-04T00:00:00Z",
            },
        }
    ]

    with pytest.raises(governance.GovernanceContractError, match="expiry"):
        governance.validate_governance_contract(
            document, deployment_target="production-bound"
        )


def test_validation_helper_rejects_acceptance_record_expiry_before_review_date():
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
            "acceptance_record": {
                "owner": "risk-owner@contoso.com",
                "justification": "Temporary waiver while the governed gateway is being onboarded.",
                "review_date": "2026-09-05T00:00:00Z",
                "expiry": "2026-09-04T00:00:00Z",
            },
        }
    ]

    with pytest.raises(governance.GovernanceContractError, match="expiry"):
        governance.validate_governance_contract(
            document, deployment_target="production-bound"
        )


def test_legacy_string_tool_remains_allowed_but_normalizes_to_unknown_unbound():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"] = ["legacy_export"]

    assert governance.validate_governance_contract(document) == {
        "framework": "github-copilot-sdk",
        "deployment_target": "customer-pilot",
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
            governance.normalize_tool("legacy_export", runtime="github-copilot-sdk")
        ],
    }


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
