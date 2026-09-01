"""Tests for threadlight-governed-actions contracts, canonicalization, and
the two Task-1 JSON Schemas (manifest + apply-plan) plus the finding catalog.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_contracts.py -q
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

import canonical
import contracts


REFERENCES = Path(__file__).resolve().parent.parent / "references"


def _jsonschema():
    try:
        import jsonschema
    except ModuleNotFoundError:
        return None
    return jsonschema


# ---------------------------------------------------------------------------
# Public constants (contracts.py)
# ---------------------------------------------------------------------------


def test_public_constants_match_spec():
    assert contracts.SCHEMA_VERSION == "1.0.0"
    assert contracts.ASSESSOR_VERSION == "0.1.0"
    assert contracts.SKILL_VERSION == "0.1.0"
    assert contracts.SUPPORTED_PHASES == ("design", "pre-deploy", "post-deploy")
    assert contracts.CONSEQUENCE_CLASSES == (
        "read",
        "write",
        "external-egress",
        "irreversible",
    )
    assert contracts.EXECUTION_MODES == (
        "interactive",
        "batch",
        "background",
        "subagent",
        "direct-tool",
        "provider-hosted-tool",
    )
    assert contracts.STATUSES == (
        "pass",
        "must-fix",
        "should-fix",
        "not-verified",
        "not-applicable",
    )
    assert contracts.VERDICTS == ("governed", "partial", "ungoverned")


def test_unsafe_target_error_is_value_error():
    assert issubclass(contracts.UnsafeTargetError, ValueError)


def test_frozen_dataclasses_are_immutable():
    source = contracts.SourceRef(repository="o/r", commit="a" * 40, dirty=False)
    with pytest.raises(Exception):
        source.commit = "b" * 40  # type: ignore[misc]


def test_finding_defaults():
    finding = contracts.Finding(
        finding_id="ACT-001",
        status="not-verified",
        phase="design",
        plane="runtime",
        reason_code="no-inventory",
        summary="s",
        details="d",
    )
    assert finding.affected_actions == ()
    assert finding.affected_paths == ()
    assert finding.evidence_refs == ()
    assert finding.remediation_ids == ()
    assert finding.residual_risk_ref is None


def test_action_record_defaults():
    action = contracts.ActionRecord(
        action_id="a1",
        display_name="Send Email",
        aliases=(),
        owner=None,
        declaration_refs=(),
        implementation_refs=(),
        input_schema_sha256=None,
        output_schema_sha256=None,
        source="declared",
        consequence="external-egress",
        secondary_consequences=(),
        reversible=False,
        compensation_ref=None,
        execution_modes=("direct-tool",),
        provider_hosted=False,
        approval_required=None,
    )
    assert action.policy_ids == ()
    assert action.known_runtime_paths == ()
    assert action.inventory_status == "not-verified"


def test_assessment_result_defaults():
    result = contracts.AssessmentResult(
        source=contracts.SourceRef(repository="o/r", commit="a" * 40, dirty=False),
        actions=(),
        paths=(),
        probes=(),
        findings=(),
        evidence=(),
    )
    assert result.policy_hashes == ()
    assert result.pins == {}
    assert result.conformance_claims == ()
    assert result.conformance_reports == ()
    assert result.change_plane == {}
    assert result.residual_risks == ()


# ---------------------------------------------------------------------------
# canonical_bytes
# ---------------------------------------------------------------------------


def test_canonical_bytes_sorts_keys_and_uses_compact_separators():
    assert canonical.canonical_bytes({"z": 1, "a": ["x", True]}) == (
        b'{"a":["x",true],"z":1}'
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_bytes_rejects_non_finite_floats(bad):
    with pytest.raises(canonical.CanonicalizationError, match="finite"):
        canonical.canonical_bytes({"value": bad})


def test_canonical_bytes_rejects_non_finite_floats_nested_in_list():
    with pytest.raises(canonical.CanonicalizationError, match="finite"):
        canonical.canonical_bytes([1, 2, {"x": [float("nan")]}])


def test_canonical_bytes_is_utf8_and_ascii_preserving():
    assert canonical.canonical_bytes({"name": "café"}) == '{"name":"café"}'.encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# sha256_hex
# ---------------------------------------------------------------------------


def test_sha256_hex_matches_hashlib():
    data = b"hello world"
    assert canonical.sha256_hex(data) == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# hash_files
# ---------------------------------------------------------------------------


def test_hash_files_binds_repo_relative_path_and_exact_bytes(tmp_path):
    content = b'{"effect":"deny"}\n'
    (tmp_path / "policy.json").write_bytes(content)

    result = canonical.hash_files(tmp_path, [Path("policy.json")])

    assert result["algorithm"] == "sha256"
    content_hash = hashlib.sha256(content).hexdigest()
    assert result["files"] == [
        {"path": "policy.json", "sha256": f"sha256:{content_hash}"}
    ]
    expected_set = hashlib.sha256(
        canonical.canonical_bytes(result["files"])
    ).hexdigest()
    assert result["set_sha256"] == f"sha256:{expected_set}"


def test_hash_files_rejects_paths_that_escape_root(tmp_path):
    outside = tmp_path.parent / "outside.json"
    outside.write_bytes(b"{}")
    with pytest.raises(canonical.CanonicalizationError):
        canonical.hash_files(tmp_path, [outside])


# ---------------------------------------------------------------------------
# validate_payload_free_audit
# ---------------------------------------------------------------------------


COMPLETE_AUDIT_RECORD = {
    "schema": "threadlight-governed-actions-audit/v1",
    "event_id": "evt-0001",
    "timestamp": "2026-01-01T00:00:00Z",
    "source_commit": "a" * 40,
    "deployment_id": "dep-0001",
    "action_id": "send_email",
    "path_id": "path-0001",
    "policy_id": "policy-0001",
    "rule_id": "rule-0001",
    "policy_hash": f"sha256:{'0' * 64}",
    "action_hash": f"sha256:{'1' * 64}",
    "decision": "deny",
    "reason_code": "policy-denied",
    "approval_redemption_hash": f"sha256:{'2' * 64}",
    "correlation_id": "corr-0001",
    "interceptor_duration_ms": 12,
    "error_class": None,
    "delivery_status": "delivered",
}


def test_validate_payload_free_audit_accepts_complete_v1_record():
    canonical.validate_payload_free_audit(COMPLETE_AUDIT_RECORD)


@pytest.mark.parametrize(
    "banned_key",
    [
        "prompt",
        "messages",
        "arguments",
        "args",
        "input",
        "output",
        "result",
        "secret",
        "token",
        "authorization",
        "body",
        "payload",
    ],
)
def test_validate_payload_free_audit_rejects_banned_keys_at_top_level(banned_key):
    record = dict(COMPLETE_AUDIT_RECORD)
    record[banned_key] = "should not be here"
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_rejects_banned_keys_recursively():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = {"nested": {"arguments": {"to": "user@example.com"}}}
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_rejects_banned_keys_in_lists():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["context"] = [{"input": "secret prompt text"}]
    with pytest.raises(canonical.PayloadExposureError):
        canonical.validate_payload_free_audit(record)


def test_validate_payload_free_audit_permits_hash_suffixed_fields():
    record = copy.deepcopy(COMPLETE_AUDIT_RECORD)
    record["input_hash"] = f"sha256:{'3' * 64}"
    record["output_hash"] = f"sha256:{'4' * 64}"
    canonical.validate_payload_free_audit(record)


# ---------------------------------------------------------------------------
# atomic_write_bytes / atomic_write_json
# ---------------------------------------------------------------------------


def test_atomic_write_bytes_writes_complete_content_and_leaves_no_tmp(tmp_path):
    destination = tmp_path / "manifest.json"
    data = b'{"hello":"world"}'
    canonical.atomic_write_bytes(destination, data)

    assert destination.read_bytes() == data
    leftovers = list(tmp_path.glob(".manifest.json.*.tmp"))
    assert leftovers == []


def test_atomic_write_bytes_creates_parent_directories(tmp_path):
    destination = tmp_path / "nested" / "dir" / "manifest.json"
    canonical.atomic_write_bytes(destination, b"data")
    assert destination.read_bytes() == b"data"


def test_atomic_write_json_round_trips_canonical_bytes(tmp_path):
    destination = tmp_path / "manifest.json"
    value = {"z": 1, "a": ["x", True]}
    canonical.atomic_write_json(destination, value)
    assert destination.read_bytes() == canonical.canonical_bytes(value)


def test_atomic_write_json_rejects_non_finite_and_leaves_no_tmp(tmp_path):
    destination = tmp_path / "manifest.json"
    with pytest.raises(canonical.CanonicalizationError):
        canonical.atomic_write_json(destination, {"value": float("nan")})
    assert not destination.exists()
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


# ---------------------------------------------------------------------------
# JSON Schema fixtures
# ---------------------------------------------------------------------------


def _minimal_manifest():
    return {
        "schema": "threadlight-governed-actions-manifest/v1",
        "assessor": {
            "name": "threadlight-governed-actions",
            "version": "0.1.0",
            "adapter": "generic",
        },
        "phase": "design",
        "captured_at": "2026-01-01T00:00:00Z",
        "source": {
            "repository": "o/r",
            "commit": "a" * 40,
            "dirty": False,
        },
        "pins": {
            "dependencies": [],
            "specifications": [],
            "probe_suite": {"name": "governed-actions-probes", "version": "0.1.0"},
        },
        "policy_hashes": [],
        "action_inventory": [],
        "mediation_paths": [],
        "conformance": {
            "claims": [],
            "reports": [],
            "application_probes": [],
        },
        "change_plane": {
            "repository": "o/r",
            "workflows": [],
            "identities": [],
        },
        "findings": [],
        "evidence": [],
        "freshness": {
            "status": "fresh",
            "valid_for_hours": 24,
            "oldest_source_at": None,
            "expires_at": None,
        },
        "residual_risks": [],
        "summary": {
            "verdict": "ungoverned",
            "pass": [],
            "must_fix": [],
            "should_fix": [],
            "not_verified": [],
            "not_applicable": [],
        },
    }


def _minimal_apply_plan():
    return {
        "schema": "threadlight-governed-actions-apply-plan/v1",
        "assessor_version": "0.1.0",
        "source_commit": "a" * 40,
        "captured_at": "2026-01-01T00:00:00Z",
        "manifest_sha256": f"sha256:{'0' * 64}",
        "self_applying": False,
        "items": [],
    }


def _validate(document, schema_path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema = _jsonschema()
    if jsonschema is not None:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(document)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False


def test_manifest_schema_is_draft_2020_12_and_accepts_minimal_document():
    _validate(_minimal_manifest(), REFERENCES / "governed-actions-manifest.schema.json")


def test_manifest_schema_rejects_unknown_root_property():
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema = _jsonschema()
    if jsonschema is None:
        pytest.skip("jsonschema not installed")
    document = _minimal_manifest()
    document["unexpected_field"] = "nope"
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(document)


def test_manifest_schema_requires_all_listed_root_fields():
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == {
        "schema",
        "assessor",
        "phase",
        "captured_at",
        "source",
        "pins",
        "policy_hashes",
        "action_inventory",
        "mediation_paths",
        "conformance",
        "change_plane",
        "findings",
        "evidence",
        "freshness",
        "residual_risks",
        "summary",
    }


def test_manifest_schema_defines_required_defs():
    schema = json.loads(
        (REFERENCES / "governed-actions-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for name in ("sha256", "status", "evidenceRef", "finding", "action", "path", "probe"):
        assert name in schema["$defs"], f"missing $defs.{name}"
    assert schema["$defs"]["sha256"]["pattern"] == "^sha256:[0-9a-f]{64}$"


def test_apply_plan_schema_is_draft_2020_12_and_accepts_minimal_document():
    _validate(
        _minimal_apply_plan(), REFERENCES / "governed-actions-apply-plan.schema.json"
    )


def test_apply_plan_schema_requires_all_listed_root_fields():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["required"]) == {
        "schema",
        "assessor_version",
        "source_commit",
        "captured_at",
        "manifest_sha256",
        "self_applying",
        "items",
    }


def test_apply_plan_schema_rejects_self_applying_true():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema = _jsonschema()
    if jsonschema is None:
        pytest.skip("jsonschema not installed")
    document = _minimal_apply_plan()
    document["self_applying"] = True
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(document)


def _validate_document(document, schema):
    jsonschema = _jsonschema()
    if jsonschema is not None:
        jsonschema.Draft202012Validator(schema).validate(document)


def test_apply_plan_schema_item_requires_all_listed_fields():
    schema = json.loads(
        (REFERENCES / "governed-actions-apply-plan.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = _minimal_apply_plan()
    document["items"] = [
        {
            "finding_id": "ACT-001",
            "status": "must-fix",
            "plane": "runtime",
            "affected_actions": ["send_email"],
            "affected_paths": [],
            "remediation_kind": "repo-edit",
            "evidence_required": [],
            "owner": None,
            "depends_on": [],
        }
    ]
    _validate_document(document, schema)

    item_def = schema["$defs"]["item"]
    assert set(item_def["required"]) == {
        "finding_id",
        "status",
        "plane",
        "affected_actions",
        "affected_paths",
        "remediation_kind",
        "evidence_required",
        "owner",
        "depends_on",
    }
    assert set(item_def["properties"]["remediation_kind"]["enum"]) == {
        "repo-edit",
        "sibling-skill",
        "manual",
        "deferred-to-pipeline",
    }
    assert item_def["additionalProperties"] is False


def test_no_schema_or_defs_contain_secret_or_payload_properties():
    for filename in (
        "governed-actions-manifest.schema.json",
        "governed-actions-apply-plan.schema.json",
    ):
        text = (REFERENCES / filename).read_text(encoding="utf-8").lower()
        for banned in ('"secret"', '"payload"', '"prompt"', '"arguments"'):
            assert banned not in text, f"{filename} contains banned property {banned}"


# ---------------------------------------------------------------------------
# finding catalog
# ---------------------------------------------------------------------------

EXPECTED_FINDING_IDS = {
    "ACT-001",
    "ACT-002",
    "MED-001",
    "MED-002",
    "MED-003",
    "ENF-001",
    "ENF-002",
    "APR-001",
    "OUT-001",
    "AUD-001",
    "PIN-001",
    "GHCP-001",
    "GHCP-002",
    "GHCP-003",
    "GHCP-004",
    "GHCP-005",
    "GHCP-006",
    "OPS-001",
}


def test_finding_catalog_has_exactly_18_ids_no_duplicates():
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    findings = catalog["findings"]
    ids = [entry["finding_id"] for entry in findings]
    assert len(ids) == 18
    assert len(set(ids)) == 18, "duplicate finding_id detected"
    assert set(ids) == EXPECTED_FINDING_IDS


def test_finding_catalog_entries_have_required_fields():
    catalog = json.loads(
        (REFERENCES / "finding-catalog.json").read_text(encoding="utf-8")
    )
    for entry in catalog["findings"]:
        assert entry["severity"] in ("must-fix", "should-fix")
        assert isinstance(entry["gate"], bool)
        assert entry["plane"] in ("runtime", "change", "both")
        assert entry["summary"]
        assert entry["remediation"]
