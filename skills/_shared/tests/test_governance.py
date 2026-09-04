import importlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "skills" / "_shared" / "governance-manifest.schema.json"
PIN_PATH = REPO_ROOT / "skills" / "_shared" / "governance-upstream-pin.json"
MODULE_NAME = "skills._shared.governance"
MODULE_PATH = REPO_ROOT / "skills" / "_shared" / "governance.py"


def governance_module():
    assert MODULE_PATH.exists(), "shared governance module missing"
    importlib.invalidate_caches()
    return importlib.import_module(MODULE_NAME)


def valid_manifest():
    policy_digest = "sha256:" + ("1" * 64)
    return {
        "schema": "threadlight-governance-manifest/v1",
        "agent": {
            "runtime": "maf-responses",
            "version": "7",
            "image_digest": "sha256:" + ("2" * 64),
        },
        "policy_bundle": {
            "id": "returns-safe",
            "version": "1.2.0",
            "digest": policy_digest,
            "signature_verified": True,
            "expires_at": "2026-09-04T12:00:00Z",
        },
        "enforcement": {
            "adapter": "maf-agent-hooks-acs",
            "mode": "enforce",
            "agent_hooks_distribution": "0.1.0a5",
            "agent_hooks_artifact_sha256": "sha256:" + ("3" * 64),
            "acs_distribution": "0.3.1b0",
            "acs_artifact_sha256": "sha256:" + ("4" * 64),
        },
        "coverage": {
            "tools_total": 1,
            "tools_bound": 1,
            "tools_enforced": 1,
            "tools_observed": 0,
            "tools_unbound": 0,
            "tools_unverified": 0,
            "tools_unsupported": 0,
            "tools_bypassable": 0,
        },
        "bindings": [
            {
                "binding_id": "returns-write-v1",
                "tool_id": "returns_apply_decision",
                "enforcement_path": "local-agent-hooks",
                "intervention_points": ["pre_tool_call", "post_tool_call"],
                "mode": "enforce",
                "safe_principles": ["scope"],
                "status": "enforced",
                "policy_digest": policy_digest,
                "probe_ids": ["deny-returns-write"],
                "evidence_refs": ["EV-receipt-1", "EV-service-1"],
            }
        ],
        "live_probes": [
            {
                "probe_id": "deny-returns-write",
                "binding_id": "returns-write-v1",
                "environment": "preproduction",
                "agent_version": "7",
                "decision": "deny",
                "downstream_effect_delta": 0,
                "decision_receipt_ref": "EV-receipt-1",
                "service_oracle_ref": "EV-service-1",
                "status": "pass",
            }
        ],
        "gaps": [],
    }


_STATUS_TO_COVERAGE_FIELD = {
    "enforced": "tools_enforced",
    "observed": "tools_observed",
    "unbound": "tools_unbound",
    "unverified": "tools_unverified",
    "unsupported": "tools_unsupported",
    "bypassable": "tools_bypassable",
}


def set_primary_binding_status(
    manifest,
    *,
    status,
    mode,
    enforcement_path=None,
    intervention_points=None,
):
    binding = manifest["bindings"][0]
    binding["status"] = status
    binding["mode"] = mode
    if enforcement_path is not None:
        binding["enforcement_path"] = enforcement_path
    if intervention_points is not None:
        binding["intervention_points"] = intervention_points

    for field in _STATUS_TO_COVERAGE_FIELD.values():
        manifest["coverage"][field] = 0
    manifest["coverage"][_STATUS_TO_COVERAGE_FIELD[status]] = 1
    manifest["coverage"]["tools_bound"] = (
        0 if binding["enforcement_path"] == "none" else 1
    )
    manifest["gaps"] = []
    if status != "enforced":
        manifest["gaps"].append(
            {
                "binding_id": binding["binding_id"],
                "status": status,
                "reason_code": "documented-gap",
                "evidence_refs": ["EV-receipt-1"],
            }
        )
    return binding


def test_declares_shared_vocabularies():
    governance = governance_module()

    assert governance.GOVERNANCE_MODES == {"off", "selective", "comprehensive"}
    assert governance.BINDING_STATUSES == {
        "enforced",
        "observed",
        "unbound",
        "unsupported",
        "unverified",
        "bypassable",
    }
    assert governance.ENFORCEMENT_PATHS == {
        "none",
        "local-agent-hooks",
        "governed-tool-gateway",
    }
    assert governance.CONSEQUENCES == {
        "read",
        "write",
        "external-egress",
        "irreversible",
        "unknown",
    }


def test_normalize_tool_preserves_legacy_string_exactly():
    governance = governance_module()

    assert governance.normalize_tool("returns_apply_decision") == {
        "id": "returns_apply_decision",
        "consequence": "unknown",
        "policy_binding": None,
        "enforcement_path": "none",
        "intervention_points": [],
    }


def test_normalize_tool_rejects_ghcp_local_agent_hooks():
    governance = governance_module()

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        governance.normalize_tool(
            {
                "id": "returns_apply_decision",
                "consequence": "write",
                "policy_binding": "returns-write-v1",
                "enforcement_path": "local-agent-hooks",
                "intervention_points": ["pre_tool_call"],
            },
            runtime="github-copilot-sdk",
        )


def test_normalize_tool_accepts_none_sentinel_for_unbound_mapping():
    governance = governance_module()

    assert governance.normalize_tool(
        {
            "id": "returns_get_case",
            "consequence": "read",
            "policy_binding": "none",
            "enforcement_path": "none",
            "intervention_points": [],
        }
    ) == {
        "id": "returns_get_case",
        "consequence": "read",
        "policy_binding": None,
        "enforcement_path": "none",
        "intervention_points": [],
    }


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            {
                "id": "returns_apply_decision",
                "consequence": "explode",
                "policy_binding": None,
                "enforcement_path": "none",
                "intervention_points": [],
            },
            "consequence",
        ),
        (
            {
                "id": "returns_apply_decision",
                "consequence": "write",
                "policy_binding": None,
                "enforcement_path": "governed-tool-gateway",
                "intervention_points": ["pre_tool_call"],
            },
            "policy_binding",
        ),
        (
            {
                "id": "returns_apply_decision",
                "consequence": "read",
                "policy_binding": "returns-read-v1",
                "enforcement_path": "none",
                "intervention_points": [],
            },
            "binding",
        ),
        (
            {
                "id": "returns_apply_decision",
                "consequence": "write",
                "policy_binding": "returns-write-v1",
                "enforcement_path": "local-agent-hooks",
                "intervention_points": ["pre_tool_call", 3],
            },
            "intervention_points",
        ),
    ],
)
def test_normalize_tool_rejects_invalid_combinations(raw, message):
    governance = governance_module()

    with pytest.raises(governance.GovernanceContractError, match=message):
        governance.normalize_tool(raw)


def test_normalize_tool_returns_fresh_json_ready_values():
    governance = governance_module()
    raw = {
        "id": "returns_apply_decision",
        "consequence": "write",
        "policy_binding": "returns-write-v1",
        "enforcement_path": "governed-tool-gateway",
        "intervention_points": ["pre_tool_call"],
    }

    normalized = governance.normalize_tool(raw)
    normalized["intervention_points"].append("post_tool_call")

    assert raw["intervention_points"] == ["pre_tool_call"]
    assert normalized == {
        "id": "returns_apply_decision",
        "consequence": "write",
        "policy_binding": "returns-write-v1",
        "enforcement_path": "governed-tool-gateway",
        "intervention_points": ["pre_tool_call", "post_tool_call"],
    }
    json.dumps(normalized)


def test_validate_governance_manifest_accepts_valid_minimal_manifest():
    governance = governance_module()
    manifest = valid_manifest()

    assert governance.validate_governance_manifest(manifest) is manifest


def test_validate_governance_manifest_accepts_draft7_integral_floats():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["coverage"] = {
        "tools_total": 1.0,
        "tools_bound": 1.0,
        "tools_enforced": 1.0,
        "tools_observed": 0.0,
        "tools_unbound": 0.0,
        "tools_unverified": 0.0,
        "tools_unsupported": 0.0,
        "tools_bypassable": 0.0,
    }
    manifest["live_probes"][0]["downstream_effect_delta"] = 0.0

    assert governance.validate_governance_manifest(manifest) is manifest


def test_validate_governance_manifest_rejects_ghcp_local_agent_hooks_binding():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["agent"]["runtime"] = "github-copilot-sdk"

    with pytest.raises(governance.GovernanceContractError, match="local-agent-hooks"):
        governance.validate_governance_manifest(manifest)


@pytest.mark.parametrize(("status", "mode"), [("enforced", "enforce"), ("observed", "evaluate_only")])
def test_validate_governance_manifest_rejects_binding_citing_failing_probe(
    status, mode
):
    governance = governance_module()
    manifest = valid_manifest()
    set_primary_binding_status(manifest, status=status, mode=mode)
    manifest["live_probes"][0]["status"] = "fail"

    with pytest.raises(governance.GovernanceContractError, match="passing live probes"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_enforced_deny_probe_with_downstream_effect():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["live_probes"][0]["downstream_effect_delta"] = 1

    with pytest.raises(
        governance.GovernanceContractError,
        match="downstream_effect_delta",
    ):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_accepts_enforced_allow_probe_with_expected_effect():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["live_probes"][0]["decision"] = "allow"
    manifest["live_probes"][0]["downstream_effect_delta"] = 1

    assert governance.validate_governance_manifest(manifest) is manifest


@pytest.mark.parametrize(("status", "mode"), [("enforced", "enforce"), ("observed", "evaluate_only")])
def test_validate_governance_manifest_rejects_unsigned_live_binding(status, mode):
    governance = governance_module()
    manifest = valid_manifest()
    set_primary_binding_status(manifest, status=status, mode=mode)
    manifest["policy_bundle"]["signature_verified"] = False

    with pytest.raises(
        governance.GovernanceContractError,
        match="signature_verified",
    ):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_accepts_expired_policy_bundle_shape_without_clock_check():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["policy_bundle"]["expires_at"] = "2000-01-01T00:00:00Z"

    assert governance.validate_governance_manifest(manifest) is manifest


def test_validate_governance_manifest_rejects_coverage_mismatch():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["coverage"]["tools_bound"] = 0

    with pytest.raises(governance.GovernanceContractError, match="coverage"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_unresolved_probe_reference():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["bindings"][0]["probe_ids"] = ["missing-probe"]

    with pytest.raises(governance.GovernanceContractError, match="probe_ids"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_duplicate_gap_binding_entry():
    governance = governance_module()
    manifest = valid_manifest()
    set_primary_binding_status(manifest, status="unverified", mode="evaluate_only")
    manifest["gaps"].append(dict(manifest["gaps"][0]))

    with pytest.raises(governance.GovernanceContractError, match="duplicate"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_duplicate_gap_tool_entry():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["gaps"] = [
        {
            "tool_id": "shell.exec",
            "status": "unsupported",
            "reason_code": "provider-hosted-bypass",
            "evidence_refs": ["EV-receipt-1"],
        },
        {
            "tool_id": "shell.exec",
            "status": "unsupported",
            "reason_code": "provider-hosted-bypass-dup",
            "evidence_refs": ["EV-service-1"],
        },
    ]

    with pytest.raises(governance.GovernanceContractError, match="duplicate"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_unresolved_evidence_reference():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["bindings"][0]["evidence_refs"] = ["EV-missing"]

    with pytest.raises(governance.GovernanceContractError, match="evidence_refs"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_evaluate_only_enforced_binding():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["enforcement"]["mode"] = "evaluate_only"

    with pytest.raises(governance.GovernanceContractError, match="evaluate_only"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_accepts_gap_for_unclassified_tool():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["gaps"] = [
        {
            "tool_id": "shell.exec",
            "status": "unsupported",
            "reason_code": "provider-hosted-bypass",
            "evidence_refs": ["EV-receipt-1"],
        }
    ]

    assert governance.validate_governance_manifest(manifest) is manifest


def test_validate_governance_manifest_rejects_enforced_binding_without_live_proof():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["bindings"][0]["probe_ids"] = []
    manifest["bindings"][0]["evidence_refs"] = []
    manifest["live_probes"] = []

    with pytest.raises(governance.GovernanceContractError, match="live proof"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_tool_gap_for_bound_tool():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["gaps"] = [
        {
            "tool_id": "returns_apply_decision",
            "status": "unsupported",
            "reason_code": "contradictory-gap",
            "evidence_refs": ["EV-receipt-1"],
        }
    ]

    with pytest.raises(governance.GovernanceContractError, match="gaps"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_binding_gap_status_mismatch():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["bindings"][0]["status"] = "unverified"
    manifest["bindings"][0]["mode"] = "evaluate_only"
    manifest["coverage"]["tools_enforced"] = 0
    manifest["coverage"]["tools_unverified"] = 1
    manifest["gaps"] = [
        {
            "binding_id": "returns-write-v1",
            "status": "unsupported",
            "reason_code": "contradictory-gap",
            "evidence_refs": ["EV-receipt-1"],
        }
    ]

    with pytest.raises(governance.GovernanceContractError, match="gaps"):
        governance.validate_governance_manifest(manifest)


def test_validate_governance_manifest_rejects_binding_claiming_another_bindings_probe():
    governance = governance_module()
    manifest = valid_manifest()
    manifest["bindings"].append(
        {
            "binding_id": "returns-read-v1",
            "tool_id": "returns_get_case",
            "enforcement_path": "local-agent-hooks",
            "intervention_points": ["pre_tool_call"],
            "mode": "enforce",
            "safe_principles": ["scope"],
            "status": "enforced",
            "policy_digest": manifest["policy_bundle"]["digest"],
            "probe_ids": ["allow-returns-read"],
            "evidence_refs": ["EV-receipt-2", "EV-service-2"],
        }
    )
    manifest["live_probes"].append(
        {
            "probe_id": "allow-returns-read",
            "binding_id": "returns-read-v1",
            "environment": "preproduction",
            "agent_version": "7",
            "decision": "allow",
            "downstream_effect_delta": 0,
            "decision_receipt_ref": "EV-receipt-2",
            "service_oracle_ref": "EV-service-2",
            "status": "pass",
        }
    )
    manifest["coverage"]["tools_total"] = 2
    manifest["coverage"]["tools_bound"] = 2
    manifest["coverage"]["tools_enforced"] = 2
    manifest["bindings"][0]["probe_ids"] = ["allow-returns-read"]

    with pytest.raises(governance.GovernanceContractError, match="probe_ids"):
        governance.validate_governance_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unexpected", True, "unsupported top-level keys"),
        ("governed", True, "whole-agent"),
        ("verdict", "governed", "whole-agent"),
    ],
)
def test_validate_governance_manifest_rejects_extra_top_level_keys(
    field, value, message
):
    governance = governance_module()
    manifest = valid_manifest()
    manifest[field] = value

    with pytest.raises(governance.GovernanceContractError, match=message):
        governance.validate_governance_manifest(manifest)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("agent", "image_digest"), "sha256:not-a-digest", "image_digest"),
        (("policy_bundle", "expires_at"), "2026-09-04", "expires_at"),
        (("policy_bundle", "version"), "1 two", "version"),
    ],
)
def test_validate_governance_manifest_rejects_malformed_digest_timestamp_and_version(
    path, value, message
):
    governance = governance_module()
    manifest = valid_manifest()
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(governance.GovernanceContractError, match=message):
        governance.validate_governance_manifest(manifest)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest["policy_bundle"].__setitem__("id", "returns-safe\n"),
            "policy_bundle.id",
        ),
        (
            lambda manifest: manifest["policy_bundle"].__setitem__("version", "1.2.0\n"),
            "policy_bundle.version",
        ),
        (
            lambda manifest: manifest["agent"].__setitem__(
                "image_digest", manifest["agent"]["image_digest"] + "\n"
            ),
            "agent.image_digest",
        ),
        (
            lambda manifest: manifest["bindings"][0]["evidence_refs"].__setitem__(
                0, "EV-receipt-1\n"
            ),
            "evidence reference",
        ),
    ],
)
def test_validate_governance_manifest_rejects_trailing_newlines_in_exact_match_fields(
    mutate, message
):
    governance = governance_module()
    manifest = valid_manifest()
    mutate(manifest)

    with pytest.raises(governance.GovernanceContractError, match=message):
        governance.validate_governance_manifest(manifest)


def test_normalize_tool_rejects_legacy_string_id_with_trailing_newline():
    governance = governance_module()

    with pytest.raises(governance.GovernanceContractError, match="identifier"):
        governance.normalize_tool("returns_apply_decision\n")


def build_jsonschema_validator():
    assert SCHEMA_PATH.exists(), "shared governance manifest schema missing"
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    format_checker = jsonschema.FormatChecker()
    assert "date-time" in format_checker.checkers, (
        "installed jsonschema lacks a 'date-time' format checker; install "
        '"jsonschema[format]" so governance schema format assertions stay active'
    )

    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema, format_checker=format_checker)


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "enforced requires enforce mode",
            lambda manifest: set_primary_binding_status(
                manifest, status="enforced", mode="evaluate_only"
            ),
        ),
        (
            "observed requires evaluate_only mode",
            lambda manifest: set_primary_binding_status(
                manifest, status="observed", mode="enforce"
            ),
        ),
        (
            "unbound requires path none",
            lambda manifest: set_primary_binding_status(
                manifest,
                status="unbound",
                mode="evaluate_only",
                enforcement_path="local-agent-hooks",
            ),
        ),
        (
            "bypassable requires non-none path",
            lambda manifest: set_primary_binding_status(
                manifest,
                status="bypassable",
                mode="evaluate_only",
                enforcement_path="none",
                intervention_points=[],
            ),
        ),
        (
            "path none requires empty intervention points",
            lambda manifest: set_primary_binding_status(
                manifest,
                status="unbound",
                mode="evaluate_only",
                enforcement_path="none",
                intervention_points=["pre_tool_call"],
            ),
        ),
        (
            "github copilot sdk rejects local hooks",
            lambda manifest: manifest["agent"].__setitem__(
                "runtime", "github-copilot-sdk"
            ),
        ),
        (
            "unsigned live binding rejected",
            lambda manifest: manifest["policy_bundle"].__setitem__(
                "signature_verified", False
            ),
        ),
        (
            "identifier trailing newline rejected",
            lambda manifest: manifest["policy_bundle"].__setitem__(
                "id", "returns-safe\n"
            ),
        ),
    ],
)
def test_schema_and_hand_validator_reject_expressible_binding_invariants(label, mutate):
    governance = governance_module()
    jsonschema_validator = build_jsonschema_validator()
    manifest = valid_manifest()
    if label == "unsigned live binding rejected":
        set_primary_binding_status(manifest, status="observed", mode="evaluate_only")
    mutate(manifest)

    with pytest.raises(governance.GovernanceContractError):
        governance.validate_governance_manifest(manifest)
    assert not jsonschema_validator.is_valid(manifest), (
        f"jsonschema unexpectedly accepted manifest where {label}"
    )


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "referential probe id resolution",
            lambda manifest: manifest["bindings"][0].__setitem__(
                "probe_ids", ["missing-probe"]
            ),
        ),
        (
            "digest coherence",
            lambda manifest: manifest["bindings"][0].__setitem__(
                "policy_digest", "sha256:" + ("9" * 64)
            ),
        ),
        (
            "coverage arithmetic",
            lambda manifest: manifest["coverage"].__setitem__("tools_bound", 0),
        ),
        (
            "gap uniqueness",
            lambda manifest: (
                set_primary_binding_status(
                    manifest, status="unverified", mode="evaluate_only"
                ),
                manifest["gaps"].append(dict(manifest["gaps"][0])),
            ),
        ),
    ],
)
def test_documented_intentional_schema_gaps_remain_hand_validator_only(label, mutate):
    governance = governance_module()
    jsonschema_validator = build_jsonschema_validator()
    manifest = valid_manifest()
    mutate(manifest)

    with pytest.raises(governance.GovernanceContractError):
        governance.validate_governance_manifest(manifest)
    assert jsonschema_validator.is_valid(manifest), (
        f"jsonschema unexpectedly rejected documented gap case: {label}"
    )


def test_build_jsonschema_validator_fails_loudly_without_date_time_backend(
    monkeypatch,
):
    jsonschema = pytest.importorskip("jsonschema")

    class CheckerWithoutDateTime:
        checkers = {}

    monkeypatch.setattr(jsonschema, "FormatChecker", lambda: CheckerWithoutDateTime())

    with pytest.raises(AssertionError, match="date-time"):
        build_jsonschema_validator()


def test_json_schema_accepts_valid_manifest():
    jsonschema_validator = build_jsonschema_validator()
    manifest = valid_manifest()
    errors = list(jsonschema_validator.iter_errors(manifest))

    assert errors == []


def test_json_schema_rejects_representative_invalid_manifest():
    jsonschema_validator = build_jsonschema_validator()
    jsonschema = pytest.importorskip("jsonschema")
    manifest = valid_manifest()
    manifest["bindings"][0]["status"] = "governed"

    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema_validator.validate(manifest)


def test_json_schema_rejects_evaluate_only_enforced_binding():
    jsonschema_validator = build_jsonschema_validator()
    jsonschema = pytest.importorskip("jsonschema")
    manifest = valid_manifest()
    manifest["enforcement"]["mode"] = "evaluate_only"

    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema_validator.validate(manifest)


def test_json_schema_rejects_enforced_binding_without_live_proof():
    jsonschema_validator = build_jsonschema_validator()
    jsonschema = pytest.importorskip("jsonschema")
    manifest = valid_manifest()
    manifest["bindings"][0]["probe_ids"] = []
    manifest["bindings"][0]["evidence_refs"] = []

    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema_validator.validate(manifest)


def test_schema_root_description_documents_authoritative_hand_validator_gaps():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert "validate_governance_manifest" in schema["description"]
    for fragment in (
        "referential",
        "arithmetic",
        "uniqueness",
        "digest",
        "count",
        "gap invariants",
    ):
        assert fragment in schema["description"]


def test_shared_upstream_pin_exact_values():
    assert PIN_PATH.exists(), "shared governance upstream pin missing"

    assert json.loads(PIN_PATH.read_text(encoding="utf-8")) == {
        "schema": "threadlight-governance-upstream-pin/v1",
        "agt": {"distribution": "agent-governance-toolkit", "version": "5.0.0"},
        "acs": {
            "distribution": "agent-control-specification",
            "version": "0.3.1b0",
        },
        "agent_hooks": {"distribution": "agent-hooks-sdk", "version": "0.1.0a5"},
        "maf": {
            "agent-framework-core": "1.14.0",
            "agent-framework-foundry": "1.11.0",
            "agent-framework-foundry-hosting": "1.0.0b260813",
        },
        "opa": {
            "version": "1.18.2",
            "linux_amd64_static_sha256": (
                "9903e5125ac281104f2c4b7371d10cc3b74a98933743fcbfc174f9bf0ab20de8"
            ),
        },
        "safe_reference": {
            "repository": "placerda/safe-agent-on-foundry",
            "commit": "f9d2a55954d447554907686d59135489c393e826",
        },
    }


def test_schema_file_exists_and_declares_governance_manifest_v1():
    assert SCHEMA_PATH.exists(), "shared governance manifest schema missing"

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "threadlight-governance-manifest/v1"
