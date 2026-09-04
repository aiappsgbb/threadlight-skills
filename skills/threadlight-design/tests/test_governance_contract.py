from __future__ import annotations

import importlib
import json
import re
from datetime import datetime, timezone
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


def governance_yaml_block() -> str:
    match = re.search(
        r"### 11a\. Runtime Governance Contract[\s\S]*?```yaml\n([\s\S]*?)\n```",
        template_text(),
    )
    assert match, "expected SPEC § 11a governance YAML block"
    return match.group(1)


def build_jsonschema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = governance_schema()
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def error_paths(errors) -> set[tuple[object, ...]]:
    paths: set[tuple[object, ...]] = set()

    def visit(error) -> None:
        if error.context:
            for child in error.context:
                visit(child)
            return
        paths.add(tuple(error.absolute_path))

    for error in errors:
        visit(error)
    return paths


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
        "environment_modes",
        "policy_binding",
        "enforcement_path",
        "intervention_points",
        "safe_principles",
        "requires",
        "acceptance_record",
        "review_date",
        "expiry",
        "environment_modes only matter when mode != off",
        "does not make an enforcement claim",
        "Legacy string tools remain valid",
        "unknown/unbound",
    ):
        assert fragment in text


def test_skill_declares_governance_interview_and_no_silent_runtime_switch():
    text = skill_text()

    for fragment in (
        "write / external-egress / irreversible",
        "one question at a time",
        "do not silently change framework/runtime",
        "retain the selected runtime unless the operator changes it",
        "production-bound",
        "acceptance record",
        "environment_modes only matter when mode != off",
        "off` is still allowed in `production-bound`",
    ):
        assert fragment in text


def test_runtime_policy_declares_exact_governance_compatibility_map_and_framework_keys():
    governance = governance_module()
    policy = runtime_policy()

    assert policy["selectors"]["frameworks"] == sorted(governance.CONTRACT_FRAMEWORKS)
    assert sorted(policy["governance_compatibility"]) == sorted(
        governance.CONTRACT_RUNTIME_PATHS
    )
    assert policy["governance_compatibility"] == {
        framework: list(paths)
        for framework, paths in governance.CONTRACT_RUNTIME_PATHS.items()
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
    assert governance.validate_governance_contract(
        document,
        deployment_target="customer-pilot",
    ) == {
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


def test_validation_helper_accepts_current_requirement_vocabulary_list_forms():
    governance = governance_module()
    document = valid_contract()
    document["governance"]["lifecycle_bindings"][0]["requires"] = ["signed-policy-bundle"]
    document["tools"][0]["requires"] = [
        "human-approval-record",
        "output-mediation",
        "decision-receipt",
        "operator-review",
        "authorization",
        "idempotency-or-transaction",
    ]

    assert governance.validate_governance_contract(
        document,
        deployment_target="customer-pilot",
    )["tools"][0]["requires"] == [
        "human-approval-record",
        "output-mediation",
        "decision-receipt",
        "operator-review",
        "authorization",
        "idempotency-or-transaction",
    ]


def test_validation_helper_rejects_unknown_requires_token():
    governance = governance_module()
    document = valid_contract()
    document["tools"][0]["requires"] = ["mystery-proof"]

    with pytest.raises(governance.GovernanceContractError, match="requires"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


def test_governance_schema_accepts_lifecycle_only_contract_without_tools():
    governance = governance_module()
    document = valid_contract()
    document["tools"] = []
    validator = build_jsonschema_validator()

    assert list(validator.iter_errors(document)) == []
    assert governance.validate_governance_contract(
        document,
        deployment_target="customer-pilot",
    ) == {
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


def test_validate_governance_contract_requires_explicit_deployment_target_keyword():
    governance = governance_module()

    with pytest.raises(TypeError):
        governance.validate_governance_contract(valid_contract())


def test_governance_schema_rejects_off_mode_with_bound_lifecycle_or_tool():
    document = valid_contract()
    document["governance"]["mode"] = "off"
    validator = build_jsonschema_validator()

    paths = error_paths(list(validator.iter_errors(document)))
    assert ("governance", "lifecycle_bindings") in paths
    assert ("tools", 0, "enforcement_path") in paths


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

    paths = error_paths(list(validator.iter_errors(document)))
    assert ("tools", 0, "enforcement_path") in paths
    assert ("tools", 1) in paths


def test_validation_helper_rejects_ghcp_local_agent_hooks():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"][0]["enforcement_path"] = "local-agent-hooks"

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


def test_validation_helper_rejects_off_mode_with_bound_contract():
    governance = governance_module()
    document = valid_contract()
    document["governance"]["mode"] = "off"

    with pytest.raises(governance.GovernanceContractError, match="off"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


def test_validation_helper_rejects_ghcp_local_agent_hooks_for_lifecycle_binding():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["governance"]["lifecycle_bindings"][0]["enforcement_path"] = (
        "local-agent-hooks"
    )

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


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

    assert governance.validate_governance_contract(
        document,
        deployment_target="customer-pilot",
    ) == {
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
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


def test_validation_helper_rejects_comprehensive_provider_hosted_consequential_tool():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["governance"]["mode"] = "comprehensive"
    document["tools"][0]["policy_binding"] = "none"
    document["tools"][0]["enforcement_path"] = "none"
    document["tools"][0]["intervention_points"] = []

    with pytest.raises(governance.GovernanceContractError, match="comprehensive"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


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
            document,
            deployment_target="production-bound",
            as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )

    document["tools"][0]["acceptance_record"] = valid_acceptance_record()

    assert governance.validate_governance_contract(
        document,
        deployment_target="production-bound",
        as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
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
            document,
            deployment_target="production-bound",
            as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
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
            document,
            deployment_target="production-bound",
            as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
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
            document,
            deployment_target="production-bound",
            as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )


def test_legacy_string_tool_remains_allowed_but_normalizes_to_unknown_unbound():
    governance = governance_module()
    document = valid_contract()
    document["framework"] = "github-copilot-sdk"
    document["tools"] = ["legacy_export"]

    assert governance.validate_governance_contract(
        document,
        deployment_target="customer-pilot",
    ) == {
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


def test_template_governance_yaml_block_safe_loads_and_validates_for_production_bound():
    governance = governance_module()
    yaml = pytest.importorskip("yaml")
    validator = build_jsonschema_validator()
    document = yaml.safe_load(
        governance_yaml_block().replace(
            "framework: github-copilot-sdk | microsoft-agent-framework",
            "framework: microsoft-agent-framework",
            1,
        )
    )

    acceptance_record = next(
        tool["acceptance_record"]
        for tool in document["tools"]
        if tool.get("id") == "legacy_export"
    )
    assert isinstance(acceptance_record["review_date"], str)
    assert isinstance(acceptance_record["expiry"], str)
    assert list(validator.iter_errors(document)) == []
    normalized = governance.validate_governance_contract(
        document,
        deployment_target="production-bound",
        as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    assert normalized["framework"] == "microsoft-agent-framework"
    assert normalized["deployment_target"] == "production-bound"


def test_governance_schema_rejects_null_policy_binding_in_structured_tool():
    governance = governance_module()
    document = valid_contract()
    document["tools"][0] = {
        "id": "returns_get_case",
        "consequence": "read",
        "policy_binding": None,
        "enforcement_path": "none",
        "intervention_points": [],
        "safe_principles": ["scope"],
        "requires": [],
    }
    validator = build_jsonschema_validator()

    assert ("tools", 0, "policy_binding") in error_paths(
        list(validator.iter_errors(document))
    )
    with pytest.raises(governance.GovernanceContractError, match="policy_binding"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


def test_governance_schema_rejects_lifecycle_binding_none_policy_and_blank_lists():
    document = valid_contract()
    document["governance"]["lifecycle_bindings"] = [
        {
            "lifecycle_point": "input",
            "policy_binding": "none",
            "enforcement_path": "governed-tool-gateway",
            "safe_principles": [],
            "requires": ["   "],
        },
        {
            "lifecycle_point": "input",
            "policy_binding": "request-ingress-v2",
            "enforcement_path": "governed-tool-gateway",
            "safe_principles": ["scope"],
            "requires": [],
        },
    ]
    validator = build_jsonschema_validator()
    paths = error_paths(list(validator.iter_errors(document)))

    assert ("governance", "lifecycle_bindings", 0, "policy_binding") in paths
    assert ("governance", "lifecycle_bindings", 0, "safe_principles") in paths
    assert ("governance", "lifecycle_bindings", 0, "requires", 0) in paths


def test_validation_helper_rejects_duplicate_lifecycle_points():
    governance = governance_module()
    document = valid_contract()
    document["governance"]["lifecycle_bindings"].append(
        {
            "lifecycle_point": "input",
            "policy_binding": "request-ingress-v2",
            "enforcement_path": "governed-tool-gateway",
            "safe_principles": ["scope"],
            "requires": [],
        }
    )

    with pytest.raises(governance.GovernanceContractError, match="lifecycle_point"):
        governance.validate_governance_contract(
            document,
            deployment_target="customer-pilot",
        )


def test_validation_helper_rejects_acceptance_record_expiry_before_as_of():
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
            "acceptance_record": valid_acceptance_record(),
        }
    ]

    with pytest.raises(governance.GovernanceContractError, match="acceptance_record.expiry"):
        governance.validate_governance_contract(
            document,
            deployment_target="production-bound",
            as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )


def test_validation_helper_snapshots_current_utc_once_for_multiple_acceptance_records(
    monkeypatch,
):
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
            "acceptance_record": valid_acceptance_record(),
        },
        {
            "id": "legacy_export_2",
            "consequence": "unknown",
            "policy_binding": "none",
            "enforcement_path": "none",
            "intervention_points": [],
            "safe_principles": ["audit"],
            "requires": [],
            "acceptance_record": valid_acceptance_record(),
        },
    ]

    class FakeDateTime(datetime):
        _now_values = iter(
            (
                (2026, 12, 31, 0, 0, timezone.utc),
                (2027, 1, 1, 0, 0, timezone.utc),
            )
        )

        @classmethod
        def now(cls, tz=None):
            year, month, day, hour, minute, value_tz = next(cls._now_values)
            value = cls(year, month, day, hour, minute, tzinfo=value_tz)
            return value if tz is None else value.astimezone(tz)

        @classmethod
        def fromisoformat(cls, value):
            return datetime.fromisoformat(value)

    monkeypatch.setattr(governance, "datetime", FakeDateTime)

    assert governance.validate_governance_contract(
        document,
        deployment_target="production-bound",
    )["deployment_target"] == "production-bound"


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
