"""Tests for the threadlight-connect skill (`connect.py`) — the mock-to-real
CONNECT leg: contract extraction, conformance checking, the evidence-driven
state machine, apply-plan/apply, and manifest emission.
"""
from __future__ import annotations

import json
import os
import stat
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
CURRENT_IDENTITY = "agent-123"

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
    assert result == {"passed": True, "evaluated": True, "item_count": 1, "differences": []}


def test_check_conformance_empty_items_is_unevaluated_not_a_pass():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    empty = check_conformance(contract, {"items": []})
    assert empty == {"passed": False, "evaluated": False, "item_count": 0, "differences": []}

    missing = check_conformance(contract, {})  # no items key at all
    assert missing == {"passed": False, "evaluated": False, "item_count": 0, "differences": []}


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
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=False,
    )

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


def test_apply_with_missing_current_identity_stays_real_unverified_and_makes_no_edits(tmp_path):
    # Evidence names an agent identity and claims revalidated roles, but the
    # caller supplied NO current_agent_identity — revalidation cannot be
    # opt-in, so even with apply=True nothing may be written.
    obo, role = full_evidence()
    assert role["agent_identity"] == "agent-123"  # evidence DOES name an identity
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role, apply=True)

    assert result["integration_state"] == "mock"
    assert result["target_state"] == "real-unverified"
    assert result["changed_paths"] == []
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()


def test_apply_with_stale_current_identity_stays_real_unverified_and_makes_no_edits(tmp_path):
    # current identity is supplied but the evidence names a DIFFERENT (stale)
    # identity -> unverified; apply=True must still make no edits.
    obo, role = full_evidence()
    role["agent_identity"] = "agent-OLD"
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity="agent-123", apply=True,
    )

    assert result["target_state"] == "real-unverified"
    assert result["changed_paths"] == []
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()


# ---------------------------------------------------------------------------
# Requirement 8, bullet 5 — apply=True + full evidence: verified + recorded
# ---------------------------------------------------------------------------
def test_apply_true_with_full_evidence_updates_state_and_records_changed_paths(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )

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
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True)

    mcp_config = json.loads(mcp_full.read_text(encoding="utf-8"))
    assert mcp_config["servers"] == {"other-tool": {"type": "http"}}
    assert "returns_get_case" in mcp_config["integrations"]


def test_second_apply_persists_integration_state_across_runs(tmp_path):
    obo, role = full_evidence()
    first = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                 current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
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
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
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


def test_write_failure_on_first_destination_preserves_prior_manifest_and_config(tmp_path, monkeypatch):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
    prior_manifest_bytes = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()
    prior_mcp_bytes = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_spec_bytes = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()

    real_os_replace = connect.os.replace

    def _boom(source, destination):
        # Fail BOTH SPEC.md / mcp-config.json replaces — SPEC is committed
        # first, so this fails on the FIRST destination (nothing replaced yet,
        # so no rollback is needed). Leave the unrelated conformance-test file
        # write untouched.
        if Path(destination).name in (Path(connect.DEFAULT_SPEC_PATH).name, Path(connect.DEFAULT_MCP_CONFIG_PATH).name):
            raise OSError("simulated disk failure")
        return real_os_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError):
        _run(
            tmp_path,
            obo_evidence=obo,
            role_evidence=role,
            current_agent_identity=CURRENT_IDENTITY,
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


def test_write_failure_on_second_destination_rolls_back_first_byte_identical(tmp_path, monkeypatch):
    # Prior verified apply establishes both destinations + a manifest on disk.
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
    prior_manifest_bytes = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()
    prior_mcp_bytes = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_spec_bytes = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()

    real_os_replace = connect.os.replace
    mcp_name = Path(connect.DEFAULT_MCP_CONFIG_PATH).name

    def _boom(source, destination):
        # Let the FIRST destination (SPEC.md) commit, then fail ONLY the second
        # (mcp-config.json) replace — forcing the SPEC write to be rolled back.
        if Path(destination).name == mcp_name:
            raise OSError("simulated disk failure on second destination")
        return real_os_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError) as excinfo:
        _run(
            tmp_path,
            obo_evidence=obo,
            role_evidence=role,
            current_agent_identity=CURRENT_IDENTITY,
            apply=True,
            generated_at="2026-08-19T10:00:00+00:00",
            real_response={"items": [{"id": "R-1002", "status": "closed"}]},
        )
    # A recoverable failure (rollback succeeded) is a plain ConnectApplyError,
    # NOT the inconsistent-state escalation.
    assert not isinstance(excinfo.value, connect.ConnectInconsistentStateError)

    # SPEC.md was committed then rolled back to its exact prior bytes; the MCP
    # config was never replaced. Neither diverges; the manifest is untouched.
    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec_bytes
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp_bytes
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest_bytes
    # the prior MCP config remains valid JSON with its prior integration entry
    prior_mcp = json.loads(prior_mcp_bytes)
    assert json.loads((tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()) == prior_mcp
    # no leaked temp files from either the failed commit or the rollback
    assert list((tmp_path / "infra").glob(".*.tmp")) == []
    assert list((tmp_path / "specs").glob(".*.tmp")) == []


def test_rollback_failure_raises_inconsistent_state_naming_paths(tmp_path, monkeypatch):
    # Prior verified apply establishes both destinations on disk.
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)

    real_os_replace = connect.os.replace
    spec_name = Path(connect.DEFAULT_SPEC_PATH).name
    mcp_name = Path(connect.DEFAULT_MCP_CONFIG_PATH).name

    def _boom(source, destination):
        name = Path(destination).name
        # Let the first SPEC commit land, fail the mcp commit, then ALSO fail
        # the SPEC rollback replace — leaving SPEC.md unreconciled.
        if name == mcp_name:
            raise OSError("simulated disk failure on second destination")
        if name == spec_name and Path(source).name.startswith("." + spec_name):
            # SPEC replaces (both the forward commit and the rollback) use a
            # dot-prefixed temp in specs/. Fail the SECOND such call (rollback).
            _boom.spec_calls += 1
            if _boom.spec_calls >= 2:
                raise OSError("simulated rollback failure")
        return real_os_replace(source, destination)

    _boom.spec_calls = 0
    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(connect.ConnectInconsistentStateError) as excinfo:
        _run(
            tmp_path,
            obo_evidence=obo,
            role_evidence=role,
            current_agent_identity=CURRENT_IDENTITY,
            apply=True,
            generated_at="2026-08-19T10:00:00+00:00",
            real_response={"items": [{"id": "R-1002", "status": "closed"}]},
        )
    # The error must name the unreconciled destination, and must NOT claim success.
    assert connect.DEFAULT_SPEC_PATH.split("/")[-1] in str(excinfo.value)


def test_write_failure_before_any_prior_manifest_leaves_nothing_on_disk(tmp_path, monkeypatch):
    obo, role = full_evidence()

    real_os_replace = connect.os.replace

    def _boom(source, destination):
        if Path(destination).name in (Path(connect.DEFAULT_SPEC_PATH).name, Path(connect.DEFAULT_MCP_CONFIG_PATH).name):
            raise OSError("simulated disk failure")
        return real_os_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True)

    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()


# ---------------------------------------------------------------------------
# transition_integration() as a pure function (no I/O)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("conformance", "obo", "role", "current_identity", "expected"),
    [
        # field-level drift (records checked, diverge) — short-circuits regardless of identity
        ({"passed": False, "evaluated": True, "differences": [{"field": "x"}]},
         {"present": True, "user_scoped": True}, {"revalidated": True}, "agent-123", "real-drift"),
        # unevaluated conformance (no real items) can never be a pass -> unverified
        ({"passed": False, "evaluated": False, "differences": []},
         {"present": True, "user_scoped": True},
         {"revalidated": True, "agent_identity": "agent-123"}, "agent-123", "real-unverified"),
        # OBO evidence missing -> unverified
        ({"passed": True, "evaluated": True, "differences": []}, None,
         {"revalidated": True, "agent_identity": "agent-123"}, "agent-123", "real-unverified"),
        # role evidence missing -> unverified
        ({"passed": True, "evaluated": True, "differences": []},
         {"present": True, "user_scoped": True}, None, "agent-123", "real-unverified"),
        # current identity NOT supplied (revalidation cannot be opt-in) -> unverified
        ({"passed": True, "evaluated": True, "differences": []},
         {"present": True, "user_scoped": True},
         {"revalidated": True, "agent_identity": "agent-123"}, None, "real-unverified"),
        # current identity supplied but evidence names a STALE identity -> unverified
        ({"passed": True, "evaluated": True, "differences": []},
         {"present": True, "user_scoped": True},
         {"revalidated": True, "agent_identity": "agent-OLD"}, "agent-123", "real-unverified"),
        # everything aligned incl. exact current identity -> verified
        ({"passed": True, "evaluated": True, "differences": []},
         {"present": True, "user_scoped": True},
         {"revalidated": True, "agent_identity": "agent-123"}, "agent-123", "real-verified"),
    ],
)
def test_transition_integration_matrix(conformance, obo, role, current_identity, expected):
    assert transition_integration(
        conformance, obo, role, current_agent_identity=current_identity
    ) == expected


def test_transition_integration_raises_on_malformed_evidence_shape():
    with pytest.raises(ConnectEvidenceError):
        transition_integration({"passed": True, "evaluated": True, "differences": []}, 42, {"revalidated": True})


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
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=True)
    manifest = result["manifest"]

    assert manifest["schema"] == "threadlight-connect-manifest/v1"
    assert manifest["status"] in ("complete", "partial", "aborted")
    assert manifest["tool_version"] == connect.TOOL_VERSION
    assert manifest["integration_state"] == "real-verified"
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


@pytest.mark.parametrize(
    ("real_response", "label"),
    [({"items": []}, "empty-list"), ({}, "missing-items")],
)
def test_empty_or_missing_real_response_is_unverified_and_makes_no_edits(tmp_path, real_response, label):
    # Even with full OBO + role evidence, the exact current identity AND
    # apply=True, an empty/missing real response must NOT vacuously verify or
    # apply — conformance is unevaluated, so the target is held at
    # real-unverified and nothing is written.
    obo, role = full_evidence()
    result = _run(
        tmp_path,
        obo_evidence=obo,
        role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY,
        real_response=real_response,
        apply=True,
    )

    assert result["target_state"] == "real-unverified"
    assert result["integration_state"] == "mock"
    assert result["changed_paths"] == []

    # conformance is recorded as non-vacuous: unevaluated, not a pass
    conformance = result["conformance"]
    assert conformance["evaluated"] is False
    assert conformance["passed"] is False
    assert conformance["item_count"] == 0

    # apply=True made NO edits to either destination
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()

    # the persisted manifest is partial / not-verified rather than a success
    manifest = result["manifest"]
    assert manifest["status"] == "partial"
    assert manifest["integration_state"] == "mock"
    assert manifest["target_state"] == "real-unverified"
    finding_ids = {f["id"]: f["status"] for f in manifest["findings"]}
    assert finding_ids.get("CONNECT-EVIDENCE-EMPTY") == "not-verified"
    connect.validate_connect_manifest(manifest)  # non-vacuous conformance still schema-valid


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
            "--current-agent-identity", CURRENT_IDENTITY,
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


# ---------------------------------------------------------------------------
# Helpers for the hardening tests below
# ---------------------------------------------------------------------------
def _current_umask() -> int:
    value = os.umask(0)
    os.umask(value)
    return value


def _apply_run(tmp_path, **kwargs):
    obo, role = full_evidence()
    return _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True, **kwargs,
    )


def _typed_contract(field_type):
    return {
        "schema": connect.DATA_CONTRACT_SCHEMA,
        "tool_name": "typed_tool",
        "generated_at": PINNED,
        "fields": [
            {"name": "amount", "required": True, "type": field_type, "cardinality": "single"},
        ],
    }


def _valid_manifest(conformance=None):
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    if conformance is None:
        conformance = check_conformance(contract, passing_real_response())
    return connect.build_connect_manifest(
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


def _write_cli_inputs(tmp_path, *, tool_source=TOOL_SOURCE, sample=SAMPLE, real=None):
    (tmp_path / "tool_source.py").write_text(tool_source, encoding="utf-8")
    (tmp_path / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
    real = passing_real_response() if real is None else real
    (tmp_path / "real.json").write_text(json.dumps(real), encoding="utf-8")


def _base_cli_args(tmp_path):
    return [
        "--project-root", str(tmp_path),
        "--tool-name", "returns_get_case",
        "--tool-source-file", str(tmp_path / "tool_source.py"),
        "--sample-file", str(tmp_path / "sample.json"),
        "--real-response-file", str(tmp_path / "real.json"),
    ]


# ---------------------------------------------------------------------------
# Finding 1 — generated conformance module must be injection-proof
# ---------------------------------------------------------------------------
def test_generated_conformance_source_neutralizes_hostile_tool_name(tmp_path):
    # A tool_name that tries to break out of the generated docstring ("""),
    # start a newline, and run code. The generated module must still COMPILE
    # and EXEC without executing the injected payload.
    marker = "CONNECT_INJECTION_MARKER"
    hostile = (
        'evil"""\n'
        f'import os; os.environ["{marker}"] = "pwned"\n'
        'x = """tool'
    )
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    source = generate_conformance_test_source(
        hostile, contract, default_sample_path=str(tmp_path / "real.json")
    )

    compile(source, "<hostile-generated>", "exec")  # must be valid Python

    os.environ.pop(marker, None)
    namespace: dict = {}
    exec(compile(source, "<hostile-generated>", "exec"), namespace)  # noqa: S102
    assert marker not in os.environ, "injected code must NOT execute at import"

    # Intended behavior preserved: a conforming sample passes, a drift fails.
    test_fns = [v for k, v in namespace.items() if k.startswith("test_") and callable(v)]
    assert len(test_fns) == 1
    conformance_test = test_fns[0]

    (tmp_path / "real.json").write_text(json.dumps(passing_real_response()), encoding="utf-8")
    conformance_test()  # conforms -> no raise

    (tmp_path / "real.json").write_text(
        json.dumps({"items": [{"id": "R-1", "status": 42}]}), encoding="utf-8"
    )
    with pytest.raises(AssertionError):
        conformance_test()
    assert marker not in os.environ


# ---------------------------------------------------------------------------
# Finding 2 — apply must preserve/select sane file permission modes
# ---------------------------------------------------------------------------
def test_apply_new_files_get_0644_under_standard_umask(tmp_path):
    previous = os.umask(0o022)
    try:
        _apply_run(tmp_path)
    finally:
        os.umask(previous)

    for rel in (connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH):
        mode = stat.S_IMODE((tmp_path / rel).stat().st_mode)
        assert mode == 0o644, f"{rel} expected 0644, got {oct(mode)}"
        assert not (mode & 0o111), "generated config must not be executable"


def test_apply_honors_process_umask_for_new_files(tmp_path):
    previous = os.umask(0o027)
    try:
        _apply_run(tmp_path)
    finally:
        os.umask(previous)

    expected = 0o666 & ~0o027  # 0o640
    for rel in (connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH):
        mode = stat.S_IMODE((tmp_path / rel).stat().st_mode)
        assert mode == expected, f"{rel} expected {oct(expected)}, got {oct(mode)}"


def test_apply_preserves_restrictive_prior_mode(tmp_path):
    spec_full = tmp_path / connect.DEFAULT_SPEC_PATH
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    spec_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    spec_full.write_text("# Prior SPEC\n", encoding="utf-8")
    mcp_full.write_text(json.dumps({"servers": {}}), encoding="utf-8")
    os.chmod(spec_full, 0o600)
    os.chmod(mcp_full, 0o600)

    _apply_run(tmp_path)

    assert stat.S_IMODE(spec_full.stat().st_mode) == 0o600
    assert stat.S_IMODE(mcp_full.stat().st_mode) == 0o600


def test_rollback_preserves_prior_mode(tmp_path, monkeypatch):
    spec_full = tmp_path / connect.DEFAULT_SPEC_PATH
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    spec_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    spec_full.write_text("# Prior SPEC\n", encoding="utf-8")
    mcp_full.write_text(json.dumps({"servers": {"keep": {"type": "http"}}}), encoding="utf-8")
    os.chmod(spec_full, 0o640)
    os.chmod(mcp_full, 0o640)
    prior_mcp_bytes = mcp_full.read_bytes()

    real_replace = os.replace
    mcp_name = Path(connect.DEFAULT_MCP_CONFIG_PATH).name

    def failing_replace(src, dst, *args, **kwargs):
        # Let the first (SPEC) replace commit, then fail the mcp-config replace
        # so the transaction rolls SPEC back to its prior bytes + mode.
        if Path(dst).name == mcp_name:
            raise OSError("injected replace failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(connect.os, "replace", failing_replace)

    with pytest.raises(ConnectApplyError):
        _apply_run(tmp_path)

    # SPEC rolled back byte-for-byte AND mode preserved; mcp-config untouched.
    assert spec_full.read_text(encoding="utf-8") == "# Prior SPEC\n"
    assert stat.S_IMODE(spec_full.stat().st_mode) == 0o640
    assert mcp_full.read_bytes() == prior_mcp_bytes
    assert stat.S_IMODE(mcp_full.stat().st_mode) == 0o640


# ---------------------------------------------------------------------------
# Finding 3 — CLI must fail cleanly on unparseable input (no traceback)
# ---------------------------------------------------------------------------
def test_cli_reports_malformed_json_argument_without_writing(tmp_path, capsys):
    _write_cli_inputs(tmp_path)
    (tmp_path / "sample.json").write_text("{ this is not valid json", encoding="utf-8")

    exit_code = connect.main(_base_cli_args(tmp_path))

    assert exit_code == 5
    err = capsys.readouterr().err
    assert "could not parse input" in err
    assert "nothing written" in err
    assert "Traceback" not in err
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()


def test_cli_reports_unparseable_tool_source_without_writing(tmp_path, capsys):
    _write_cli_inputs(tmp_path, tool_source="return (((")  # invalid Python

    exit_code = connect.main(_base_cli_args(tmp_path))

    assert exit_code == 5
    err = capsys.readouterr().err
    assert "could not parse input" in err
    assert "nothing written" in err
    assert "Traceback" not in err
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()


# ---------------------------------------------------------------------------
# Finding 4 — lossless numeric widening (int->number, integral float->integer)
# ---------------------------------------------------------------------------
def test_integer_actual_satisfies_expected_number():
    result = check_conformance(_typed_contract("number"), {"items": [{"amount": 5}]})
    assert result["passed"] is True
    assert result["differences"] == []


def test_integral_float_satisfies_expected_integer():
    result = check_conformance(_typed_contract("integer"), {"items": [{"amount": 3.0}]})
    assert result["passed"] is True
    assert result["differences"] == []


def test_non_integral_float_still_drifts_from_integer():
    result = check_conformance(_typed_contract("integer"), {"items": [{"amount": 3.5}]})
    assert result["passed"] is False
    assert result["differences"][0]["actual"] == "number"
    assert result["differences"][0]["field"] == "amount"


def test_bool_never_widens_into_integer():
    result = check_conformance(_typed_contract("integer"), {"items": [{"amount": True}]})
    assert result["passed"] is False
    assert result["differences"][0]["actual"] == "boolean"


def test_float_actual_does_not_widen_into_string():
    result = check_conformance(_typed_contract("string"), {"items": [{"amount": 1.0}]})
    assert result["passed"] is False
    assert result["differences"][0]["actual"] == "number"


def test_generated_module_applies_the_same_numeric_widening(tmp_path):
    contract = _typed_contract("number")
    source = generate_conformance_test_source(
        "typed_tool", contract, default_sample_path=str(tmp_path / "real.json")
    )
    namespace: dict = {}
    exec(compile(source, "<gen-widening>", "exec"), namespace)  # noqa: S102
    (tmp_path / "real.json").write_text(json.dumps({"items": [{"amount": 7}]}), encoding="utf-8")
    namespace["test_typed_tool_conformance"]()  # int satisfies expected number -> no raise


# ---------------------------------------------------------------------------
# Finding 5 — freshness: thread evidence_captured_at into source_oldest_at
# ---------------------------------------------------------------------------
def test_source_oldest_at_is_none_when_capture_time_unknown(tmp_path):
    result = _run(tmp_path)
    assert result["manifest"]["freshness"]["source_oldest_at"] is None


def test_source_oldest_at_threads_evidence_captured_at(tmp_path):
    captured = "2026-08-10T09:00:00+00:00"
    result = _run(tmp_path, evidence_captured_at=captured)
    freshness = result["manifest"]["freshness"]
    assert freshness["source_oldest_at"] == captured
    # Must NOT be silently borrowed from the run's own generated_at.
    assert freshness["source_oldest_at"] != result["manifest"]["generated_at"]
    connect.validate_connect_manifest(result["manifest"])  # still schema-valid


def test_malformed_evidence_captured_at_aborts_before_any_write(tmp_path):
    with pytest.raises(ConnectEvidenceError):
        _run(tmp_path, evidence_captured_at="not-a-timestamp")
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    # Fails fast: not even the always-written conformance test is produced.
    assert not (tmp_path / "tests" / "threadlight_connect").exists()


def test_cli_threads_evidence_captured_at(tmp_path):
    _write_cli_inputs(tmp_path)
    captured = "2026-08-09T08:30:00+00:00"

    exit_code = connect.main(_base_cli_args(tmp_path) + ["--evidence-captured-at", captured])

    assert exit_code == 0
    manifest = json.loads((tmp_path / connect.DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8"))
    assert manifest["freshness"]["source_oldest_at"] == captured


# ---------------------------------------------------------------------------
# Finding 6 — never silently reset corrupt prior state/config
# ---------------------------------------------------------------------------
def test_corrupt_prior_manifest_aborts_and_preserves_bytes(tmp_path):
    manifest_full = tmp_path / connect.DEFAULT_MANIFEST_PATH
    manifest_full.parent.mkdir(parents=True, exist_ok=True)
    corrupt = b"{ this is not valid json at all"
    manifest_full.write_bytes(corrupt)

    with pytest.raises(ConnectEvidenceError):
        _run(tmp_path)

    assert manifest_full.read_bytes() == corrupt  # preserved, not reset to mock


def test_corrupt_mcp_config_aborts_apply_and_preserves_bytes(tmp_path):
    generated_test = (
        tmp_path
        / "tests"
        / "threadlight_connect"
        / "test_returns_get_case_conformance.py"
    )
    generated_test.unlink(missing_ok=True)

    spec_full = tmp_path / connect.DEFAULT_SPEC_PATH
    spec_full.parent.mkdir(parents=True, exist_ok=True)
    original_spec = b"# Existing specification\n"
    spec_full.write_bytes(original_spec)

    manifest_full = tmp_path / connect.DEFAULT_MANIFEST_PATH
    manifest_full.parent.mkdir(parents=True, exist_ok=True)
    original_manifest = json.dumps(_valid_manifest(), sort_keys=True).encode()
    manifest_full.write_bytes(original_manifest)

    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    corrupt = b"{ broken mcp config"
    mcp_full.write_bytes(corrupt)

    with pytest.raises(ConnectEvidenceError):
        _apply_run(tmp_path)

    assert not generated_test.exists()
    assert spec_full.read_bytes() == original_spec
    assert manifest_full.read_bytes() == original_manifest
    assert mcp_full.read_bytes() == corrupt


def test_non_dict_mcp_config_aborts_apply_and_preserves_bytes(tmp_path):
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([1, 2, 3])  # valid JSON, but not an object
    mcp_full.write_text(payload, encoding="utf-8")

    with pytest.raises(ConnectEvidenceError):
        _apply_run(tmp_path)

    assert json.loads(mcp_full.read_text(encoding="utf-8")) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Finding 7 — validate_connect_manifest enforces additionalProperties: false
# ---------------------------------------------------------------------------
def test_representative_valid_manifest_passes_validation():
    connect.validate_connect_manifest(_valid_manifest())  # must not raise


def test_validate_rejects_unknown_top_level_key():
    manifest = _valid_manifest()
    manifest["surprise"] = "nope"
    with pytest.raises(ManifestValidationError, match="unknown key"):
        connect.validate_connect_manifest(manifest)


def test_validate_rejects_unknown_conformance_key():
    manifest = _valid_manifest()
    manifest["conformance"]["sneaky"] = True
    with pytest.raises(ManifestValidationError, match="conformance"):
        connect.validate_connect_manifest(manifest)


def test_validate_rejects_unknown_difference_key():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    conformance = check_conformance(contract, drifting_real_response())
    assert conformance["differences"], "precondition: a drift difference exists"
    manifest = _valid_manifest(conformance=conformance)
    manifest["conformance"]["differences"][0]["unexpected"] = "x"
    with pytest.raises(ManifestValidationError, match="difference"):
        connect.validate_connect_manifest(manifest)


def test_validate_rejects_unknown_evidence_summary_key():
    manifest = _valid_manifest()
    manifest["evidence_summary"]["leak"] = "token"
    with pytest.raises(ManifestValidationError, match="evidence_summary"):
        connect.validate_connect_manifest(manifest)


def test_validate_rejects_unknown_contract_key():
    manifest = _valid_manifest()
    manifest["contract"]["extra"] = 1
    with pytest.raises(ManifestValidationError, match="contract"):
        connect.validate_connect_manifest(manifest)


def test_validate_rejects_unknown_contract_field_key():
    manifest = _valid_manifest()
    manifest["contract"]["fields"][0]["extra"] = 1
    with pytest.raises(ManifestValidationError, match="contract field"):
        connect.validate_connect_manifest(manifest)


def _manifest_with_all_nested_items():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    conformance = check_conformance(contract, drifting_real_response())
    manifest = _valid_manifest(conformance=conformance)
    manifest["findings"] = [{"id": "CONNECT-DRIFT", "status": "must-fix"}]
    manifest["apply_plan"] = [
        {"path": "SPEC.md", "action": "create", "description": "Update the specification"}
    ]
    return manifest


@pytest.mark.parametrize(
    ("object_path", "required_key"),
    [
        (("evidence_summary",), "obo_present"),
        (("evidence_summary",), "obo_user_scoped"),
        (("evidence_summary",), "roles_revalidated"),
        (("evidence_summary",), "required_roles"),
        (("contract",), "schema"),
        (("contract",), "tool_name"),
        (("contract",), "generated_at"),
        (("contract",), "fields"),
        (("contract", "fields", 0), "name"),
        (("contract", "fields", 0), "required"),
        (("contract", "fields", 0), "type"),
        (("contract", "fields", 0), "cardinality"),
        (("findings", 0), "id"),
        (("findings", 0), "status"),
        (("conformance", "differences", 0), "field"),
        (("conformance", "differences", 0), "expected"),
        (("conformance", "differences", 0), "actual"),
        (("conformance", "differences", 0), "path"),
        (("apply_plan", 0), "path"),
        (("apply_plan", 0), "action"),
        (("apply_plan", 0), "description"),
    ],
)
def test_validate_rejects_missing_nested_required_key(object_path, required_key):
    manifest = _manifest_with_all_nested_items()
    nested = manifest
    for segment in object_path:
        nested = nested[segment]
    del nested[required_key]

    with pytest.raises(ManifestValidationError, match="missing required"):
        connect.validate_connect_manifest(manifest)


@pytest.mark.parametrize(
    "object_path",
    [
        ("evidence_summary",),
        ("contract",),
        ("contract", "fields", 0),
        ("findings", 0),
        ("conformance", "differences", 0),
        ("apply_plan", 0),
    ],
)
def test_validate_rejects_non_object_nested_item(object_path):
    manifest = _manifest_with_all_nested_items()
    parent = manifest
    for segment in object_path[:-1]:
        parent = parent[segment]
    parent[object_path[-1]] = []

    with pytest.raises(ManifestValidationError, match="must be an object"):
        connect.validate_connect_manifest(manifest)
