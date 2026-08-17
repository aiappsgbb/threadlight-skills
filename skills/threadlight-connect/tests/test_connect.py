"""Tests for the threadlight-connect skill (`connect.py`) — the mock-to-real
CONNECT leg: contract extraction, conformance checking, the evidence-driven
state machine, apply-plan/apply, and manifest emission.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import connect  # noqa: E402
from connect import (  # noqa: E402
    ConnectApplyError,
    ConnectEvidenceError,
    build_apply_plan,
    check_conformance,
    extract_contract,
    generate_conformance_test_source,
    run_connect,
    transition_integration,
)

REPO_ROOT = SKILL_ROOT.parent.parent
sys.path.insert(0, str(REPO_ROOT))
from skills._shared.manifest import ManifestValidationError  # noqa: E402

PINNED = "2026-08-17T10:00:00+00:00"

TOOL_SOURCE = "return {'id': row['id'], 'status': row.get('status')}"
SAMPLE = {"id": "R-1001", "status": "open", "internal": "do-not-leak"}


def full_evidence(**overrides):
    obo = {"present": True, "user_scoped": True}
    obo.update(overrides.get("obo", {}))
    role = {
        "revalidated": True,
        "required_roles": ["Case.Read"],
        "validated_roles": ["Case.Read", "Case.Write"],
        "agent_identity": "agent-123",
    }
    role.update(overrides.get("role", {}))
    return obo, role


def passing_real_response():
    return {"items": [{"id": "R-1001", "status": "open"}]}


def drifting_real_response():
    # 'status' present with the wrong type -> field-level conformance failure
    return {"items": [{"id": "R-1001", "status": 42}]}


def _run(tmp_path, **kwargs):
    defaults = dict(
        project_root=tmp_path,
        tool_name="returns_get_case",
        tool_source=TOOL_SOURCE,
        sample=SAMPLE,
        real_response=passing_real_response(),
        generated_at=PINNED,
    )
    defaults.update(kwargs)
    return run_connect(**defaults)


# ---------------------------------------------------------------------------
# Requirement 3 — contract extraction: exact fields + requiredness, no leak
# ---------------------------------------------------------------------------
def test_extract_contract_exact_fields_and_requiredness():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)

    assert contract["tool_name"] == "returns_get_case"
    assert contract["fields"] == [
        {"name": "id", "required": True, "type": "string", "cardinality": "single"},
        {"name": "status", "required": False, "type": "string", "cardinality": "single"},
    ]
    field_names = {f["name"] for f in contract["fields"]}
    assert "internal" not in field_names, "un-read sample field must not leak into the contract"


def test_extract_contract_full_function_def_is_also_accepted():
    source = (
        "def get_case(case_id):\n"
        "    row = db.lookup(case_id)\n"
        "    return {'id': row['id'], 'status': row.get('status')}\n"
    )
    contract = extract_contract("returns_get_case", source, SAMPLE, generated_at=PINNED)
    assert [f["name"] for f in contract["fields"]] == ["id", "status"]


def test_extract_contract_field_absent_from_sample_has_no_type_evidence():
    source = "return {'sla_tier': row['sla_tier']}"
    contract = extract_contract("t", source, {"id": "x"}, generated_at=PINNED)
    assert contract["fields"] == [
        {"name": "sla_tier", "required": True, "type": None, "cardinality": None}
    ]


# ---------------------------------------------------------------------------
# Requirement 4 — conformance diffs have the exact {field, expected, actual,
# path} shape, and the generated conformance tests are truly executable.
# ---------------------------------------------------------------------------
def test_check_conformance_missing_required_field_has_exact_diff_shape():
    contract = {"fields": [{"name": "status", "required": True, "type": "string", "cardinality": "single"}]}
    result = check_conformance(contract, {"items": [{}]})

    assert result["passed"] is False
    assert result["differences"] == [
        {"field": "status", "expected": "string|required", "actual": "missing", "path": "$.items[0].status"}
    ]


def test_check_conformance_type_mismatch_reports_observed_type():
    contract = {"fields": [{"name": "status", "required": False, "type": "string", "cardinality": "single"}]}
    result = check_conformance(contract, {"items": [{"status": 42}]})

    assert result["differences"] == [
        {"field": "status", "expected": "string|optional", "actual": "integer", "path": "$.items[0].status"}
    ]


def test_check_conformance_passes_when_fields_match():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    result = check_conformance(contract, passing_real_response())
    assert result == {"passed": True, "differences": []}


def test_generated_conformance_tests_are_executable(tmp_path):
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    real_sample_path = tmp_path / "real.json"
    source = generate_conformance_test_source(
        "returns_get_case", contract, default_sample_path=str(real_sample_path)
    )
    compile(source, "<generated-conformance-test>", "exec")  # must be syntactically valid Python

    module_path = tmp_path / "test_generated_conformance.py"
    module_path.write_text(source, encoding="utf-8")
    namespace: dict = {"__name__": "test_generated_conformance", "__file__": str(module_path)}
    exec(compile(source, str(module_path), "exec"), namespace)  # noqa: S102 - executing our own generated code

    real_sample_path.write_text(json.dumps(passing_real_response()), encoding="utf-8")
    namespace["test_returns_get_case_conformance"]()  # must not raise: conforms

    real_sample_path.write_text(json.dumps({"items": [{"id": "R-1001", "status": 42}]}), encoding="utf-8")
    with pytest.raises(AssertionError, match=r"status.*integer"):
        namespace["test_returns_get_case_conformance"]()


# ---------------------------------------------------------------------------
# Requirement 8, bullet 2 — failed conformance -> real-drift, no edits
# ---------------------------------------------------------------------------
def test_failed_conformance_yields_real_drift_target_with_no_edits(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path,
        real_response=drifting_real_response(),
        obo_evidence=obo,
        role_evidence=role,
        apply=True,
    )

    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-drift"
    assert result["conformance"]["passed"] is False
    assert result["changed_paths"] == []
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()


# ---------------------------------------------------------------------------
# Requirement 8, bullet 3 — apply=False + full evidence: dry-run plan only
# ---------------------------------------------------------------------------
def test_apply_false_with_full_evidence_plans_without_writing(tmp_path):
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=False)

    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-verified"
    assert result["changed_paths"] == []
    assert result["apply_plan"] != []
    assert {step["path"] for step in result["apply_plan"]} == {
        connect.DEFAULT_SPEC_PATH,
        connect.DEFAULT_MCP_CONFIG_PATH,
    }
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()


# ---------------------------------------------------------------------------
# Requirement 8, bullet 4 — missing OBO evidence -> real-unverified, no edits
# ---------------------------------------------------------------------------
def test_missing_obo_evidence_yields_real_unverified_with_no_edits(tmp_path):
    _, role = full_evidence()
    result = _run(
        tmp_path,
        obo_evidence={"present": False, "user_scoped": False},
        role_evidence=role,
        apply=True,
    )

    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-unverified"
    assert result["changed_paths"] == []
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()


def test_obo_present_but_not_user_scoped_yields_real_unverified(tmp_path):
    _, role = full_evidence()
    result = _run(
        tmp_path,
        obo_evidence={"present": True, "user_scoped": False},
        role_evidence=role,
    )
    assert result["target_state"] == "real-unverified"


def test_roles_not_revalidated_yields_real_unverified(tmp_path):
    obo, _ = full_evidence()
    result = _run(
        tmp_path,
        obo_evidence=obo,
        role_evidence={"revalidated": False, "required_roles": ["Case.Read"], "validated_roles": ["Case.Read"]},
    )
    assert result["target_state"] == "real-unverified"


def test_required_roles_not_subset_of_validated_yields_real_unverified(tmp_path):
    obo, _ = full_evidence()
    result = _run(
        tmp_path,
        obo_evidence=obo,
        role_evidence={
            "revalidated": True,
            "required_roles": ["Case.Read", "Case.Escalate"],
            "validated_roles": ["Case.Read"],
        },
    )
    assert result["target_state"] == "real-unverified"


# ---------------------------------------------------------------------------
# Requirement 6 — publish/republish must revalidate roles against the
# CURRENT agent identity, not a stale grant.
# ---------------------------------------------------------------------------
def test_role_revalidation_against_stale_identity_yields_real_unverified(tmp_path):
    obo, role = full_evidence()
    role["agent_identity"] = "agent-OLD"
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, current_agent_identity="agent-123")
    assert result["target_state"] == "real-unverified"


def test_role_revalidation_against_matching_current_identity_yields_real_verified(tmp_path):
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, current_agent_identity="agent-123")
    assert result["target_state"] == "real-verified"


# ---------------------------------------------------------------------------
# Requirement 8, bullet 5 — apply=True + full evidence: verified + recorded
# ---------------------------------------------------------------------------
def test_apply_true_with_full_evidence_updates_state_and_records_changed_paths(tmp_path):
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True)

    assert result["integration_state"] == "real-verified"
    assert result["target_state"] == "real-verified"
    assert set(result["changed_paths"]) == {connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH}

    spec_text = (tmp_path / connect.DEFAULT_SPEC_PATH).read_text(encoding="utf-8")
    assert "returns_get_case" in spec_text
    assert "real, evidence-verified" in spec_text

    mcp_config = json.loads((tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_text(encoding="utf-8"))
    assert mcp_config["integrations"]["returns_get_case"]["state"] == "real-verified"

    manifest = json.loads((tmp_path / connect.DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8"))
    assert manifest["integration_state"] == "real-verified"
    assert manifest["target_state"] == "real-verified"
    assert set(manifest["changed_paths"]) == {connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH}


def test_apply_preserves_prior_unrelated_mcp_config_content(tmp_path):
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(json.dumps({"servers": {"other-tool": {"type": "http"}}}), encoding="utf-8")

    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True)

    mcp_config = json.loads(mcp_full.read_text(encoding="utf-8"))
    assert mcp_config["servers"] == {"other-tool": {"type": "http"}}
    assert "returns_get_case" in mcp_config["integrations"]


def test_second_apply_persists_integration_state_across_runs(tmp_path):
    obo, role = full_evidence()
    first = _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True, generated_at=PINNED)
    assert first["integration_state"] == "real-verified"

    # A later run (e.g. re-checking conformance) reads the PERSISTED state
    # even without re-applying.
    second = _run(tmp_path, obo_evidence=None, role_evidence=None, apply=False, generated_at="2026-08-18T10:00:00+00:00")
    assert second["integration_state"] == "real-verified"
    assert second["target_state"] == "real-unverified"  # no evidence supplied this run


# ---------------------------------------------------------------------------
# Requirement 8, bullet 6 — malformed evidence and write failure preserve
# whatever valid manifest/config already existed on disk.
# ---------------------------------------------------------------------------
def test_malformed_obo_evidence_raises_and_preserves_prior_manifest(tmp_path):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True, generated_at=PINNED)
    prior_manifest_bytes = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()
    prior_mcp_bytes = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_spec_bytes = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()

    with pytest.raises(ConnectEvidenceError):
        _run(
            tmp_path,
            obo_evidence="not-a-dict",  # malformed shape, not merely absent
            role_evidence=role,
            apply=True,
            generated_at="2026-08-19T10:00:00+00:00",
        )

    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest_bytes
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp_bytes
    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec_bytes


def test_malformed_role_evidence_type_raises_and_writes_nothing(tmp_path):
    obo, _ = full_evidence()
    with pytest.raises(ConnectEvidenceError):
        _run(
            tmp_path,
            obo_evidence=obo,
            role_evidence={"revalidated": "yes"},  # wrong type, not a bool
            apply=True,
        )
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()


def test_write_failure_during_apply_preserves_prior_manifest_and_config(tmp_path, monkeypatch):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True, generated_at=PINNED)
    prior_manifest_bytes = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()
    prior_mcp_bytes = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_spec_bytes = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()

    real_os_replace = connect.os.replace

    def _boom(source, destination):
        # Only fail the SPEC.md / mcp-config.json replace calls made inside
        # apply_changes() — leave the always-written conformance-test file
        # (an unrelated scaffolding artifact) unaffected.
        if Path(destination).name in (Path(connect.DEFAULT_SPEC_PATH).name, Path(connect.DEFAULT_MCP_CONFIG_PATH).name):
            raise OSError("simulated disk failure")
        return real_os_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError):
        _run(
            tmp_path,
            obo_evidence=obo,
            role_evidence=role,
            apply=True,
            generated_at="2026-08-19T10:00:00+00:00",
            real_response={"items": [{"id": "R-1002", "status": "closed"}]},
        )

    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest_bytes
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp_bytes
    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec_bytes
    # no leaked temp files from the aborted attempt
    assert list((tmp_path / "infra").glob(".*.tmp")) == []
    assert list((tmp_path / "specs").glob(".*.tmp")) == []


def test_write_failure_before_any_prior_manifest_leaves_nothing_on_disk(tmp_path, monkeypatch):
    obo, role = full_evidence()

    real_os_replace = connect.os.replace

    def _boom(source, destination):
        if Path(destination).name in (Path(connect.DEFAULT_SPEC_PATH).name, Path(connect.DEFAULT_MCP_CONFIG_PATH).name):
            raise OSError("simulated disk failure")
        return real_os_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError):
        _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True)

    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()


# ---------------------------------------------------------------------------
# transition_integration() as a pure function (no I/O)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("conformance", "obo", "role", "expected"),
    [
        ({"passed": False, "differences": [{"field": "x"}]}, {"present": True, "user_scoped": True},
         {"revalidated": True}, "real-drift"),
        ({"passed": True, "differences": []}, None, {"revalidated": True}, "real-unverified"),
        ({"passed": True, "differences": []}, {"present": True, "user_scoped": True}, None, "real-unverified"),
        ({"passed": True, "differences": []}, {"present": True, "user_scoped": True},
         {"revalidated": True}, "real-verified"),
    ],
)
def test_transition_integration_matrix(conformance, obo, role, expected):
    assert transition_integration(conformance, obo, role) == expected


def test_transition_integration_raises_on_malformed_evidence_shape():
    with pytest.raises(ConnectEvidenceError):
        transition_integration({"passed": True, "differences": []}, 42, {"revalidated": True})


# ---------------------------------------------------------------------------
# build_apply_plan() — pure, always non-empty, distinguishes create/update
# ---------------------------------------------------------------------------
def test_build_apply_plan_reports_create_when_targets_absent(tmp_path):
    plan = build_apply_plan(tmp_path, "specs/SPEC.md", "infra/mcp-config.json", "returns_get_case")
    assert plan == [
        {
            "path": "specs/SPEC.md",
            "action": "create",
            "description": "Record returns_get_case as a real, evidence-verified integration",
        },
        {
            "path": "infra/mcp-config.json",
            "action": "update" if (tmp_path / "infra/mcp-config.json").exists() else "create",
            "description": "Point the returns_get_case MCP server entry at the real endpoint",
        },
    ]


def test_build_apply_plan_reports_update_when_targets_exist(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    plan = build_apply_plan(tmp_path, "specs/SPEC.md", "infra/mcp-config.json", "returns_get_case")
    assert plan[0]["action"] == "update"


# ---------------------------------------------------------------------------
# Requirement 7 — manifest hygiene: no credentials/tokens/customer payloads
# ---------------------------------------------------------------------------
def test_manifest_never_contains_secret_shaped_keys_even_if_evidence_does(tmp_path):
    obo = {"present": True, "user_scoped": True, "access_token": "super-secret-value"}
    role = {
        "revalidated": True,
        "required_roles": ["Case.Read"],
        "validated_roles": ["Case.Read"],
        "client_secret": "also-secret",
    }
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True)

    manifest_text = json.dumps(result["manifest"])
    assert "super-secret-value" not in manifest_text
    assert "also-secret" not in manifest_text
    assert "access_token" not in manifest_text
    assert "client_secret" not in manifest_text

    on_disk = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8")
    assert "super-secret-value" not in on_disk
    assert "also-secret" not in on_disk


def test_manifest_contains_no_sample_field_values():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    conformance = check_conformance(contract, passing_real_response())
    manifest = connect.build_connect_manifest(
        tool_name="returns_get_case",
        integration_state="mock",
        target_state="real-verified",
        contract=contract,
        conformance=conformance,
        evidence_summary={
            "obo_present": True, "obo_user_scoped": True,
            "roles_revalidated": True, "required_roles": ["Case.Read"],
        },
        apply_plan=[],
        changed_paths=[],
        apply=False,
        status="complete",
        findings=[],
        generated_at=PINNED,
    )
    manifest_text = json.dumps(manifest)
    assert "do-not-leak" not in manifest_text
    assert "R-1001" not in manifest_text


# ---------------------------------------------------------------------------
# Manifest validation / envelope compliance
# ---------------------------------------------------------------------------
def test_run_connect_emits_shared_envelope_compliant_manifest(tmp_path):
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True)
    manifest = result["manifest"]

    assert manifest["schema"] == "threadlight-connect-manifest/v1"
    assert manifest["status"] in ("complete", "partial", "aborted")
    assert manifest["tool_version"] == connect.TOOL_VERSION
    connect.validate_connect_manifest(manifest)  # must not raise


def test_validate_connect_manifest_rejects_unknown_integration_state():
    obo_summary = {"obo_present": True, "obo_user_scoped": True, "roles_revalidated": True, "required_roles": []}
    manifest = connect.build_connect_manifest(
        tool_name="t", integration_state="mock", target_state="real-verified",
        contract={"fields": []}, conformance={"passed": True, "differences": []},
        evidence_summary=obo_summary, apply_plan=[], changed_paths=[], apply=False,
        status="complete", findings=[], generated_at=PINNED,
    )
    manifest["integration_state"] = "bogus-state"
    with pytest.raises(ManifestValidationError, match="integration_state"):
        connect.validate_connect_manifest(manifest)


def test_manifest_status_is_partial_when_no_real_items_to_verify(tmp_path):
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, real_response={"items": []})
    assert result["manifest"]["status"] == "partial"


# ---------------------------------------------------------------------------
# Conformance test generation writes an artifact regardless of --apply
# ---------------------------------------------------------------------------
def test_conformance_tests_are_written_even_when_apply_is_false(tmp_path):
    result = _run(tmp_path, apply=False)
    test_file = tmp_path / result["test_path"]
    assert test_file.exists()
    assert "def test_" in test_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
def test_cli_main_dry_run_prints_plan_and_writes_manifest(tmp_path, capsys):
    obo, role = full_evidence()
    (tmp_path / "tool_source.py").write_text(TOOL_SOURCE, encoding="utf-8")
    (tmp_path / "sample.json").write_text(json.dumps(SAMPLE), encoding="utf-8")
    (tmp_path / "real.json").write_text(json.dumps(passing_real_response()), encoding="utf-8")
    (tmp_path / "obo.json").write_text(json.dumps(obo), encoding="utf-8")
    (tmp_path / "role.json").write_text(json.dumps(role), encoding="utf-8")

    exit_code = connect.main(
        [
            "--project-root", str(tmp_path),
            "--tool-name", "returns_get_case",
            "--tool-source-file", str(tmp_path / "tool_source.py"),
            "--sample-file", str(tmp_path / "sample.json"),
            "--real-response-file", str(tmp_path / "real.json"),
            "--obo-evidence-file", str(tmp_path / "obo.json"),
            "--role-evidence-file", str(tmp_path / "role.json"),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "target_state: real-verified" in captured.out
    assert "integration_state: mock" in captured.out
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()


def test_cli_main_reports_malformed_evidence_without_writing(tmp_path, capsys):
    (tmp_path / "tool_source.py").write_text(TOOL_SOURCE, encoding="utf-8")
    (tmp_path / "sample.json").write_text(json.dumps(SAMPLE), encoding="utf-8")
    (tmp_path / "real.json").write_text(json.dumps(passing_real_response()), encoding="utf-8")
    (tmp_path / "obo.json").write_text(json.dumps("not-a-dict"), encoding="utf-8")

    exit_code = connect.main(
        [
            "--project-root", str(tmp_path),
            "--tool-name", "returns_get_case",
            "--tool-source-file", str(tmp_path / "tool_source.py"),
            "--sample-file", str(tmp_path / "sample.json"),
            "--real-response-file", str(tmp_path / "real.json"),
            "--obo-evidence-file", str(tmp_path / "obo.json"),
        ]
    )

    assert exit_code == 2
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert "nothing written" in capsys.readouterr().err
