"""Tests for the threadlight-connect skill (`connect.py`) — the mock-to-real
CONNECT leg: contract extraction, conformance checking, the evidence-driven
state machine, apply-plan/apply, and manifest emission.
"""
from __future__ import annotations

import copy
import json
import os
import re
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

# A well-formed real endpoint the apply-verified tests bind to. It is a real
# (non-mock) https URL with no embedded credentials, so it passes
# _validate_real_endpoint and is the value persisted into servers[tool].url.
REAL_ENDPOINT = "https://api.example.com/mcp"

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


# The connect leg emits EXACTLY these four findings, one each, in order. Fixtures
# that stand in for an emitted manifest reuse this canonical shape; the exact
# statuses only matter where a test asserts them (the validator only enforces the
# id tuple + status enum).
def _int_findings(statuses=("pass", "pass", "pass", "pass")):
    return [
        {"id": fid, "status": status, "detail": ""}
        for fid, status in zip(connect.INT_FINDING_IDS, statuses)
    ]


def _run(tmp_path, **kwargs):
    defaults = dict(
        project_root=tmp_path,
        tool_name="returns_get_case",
        tool_source=TOOL_SOURCE,
        sample=SAMPLE,
        real_response=passing_real_response(),
        generated_at=PINNED,
        real_endpoint=REAL_ENDPOINT,
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
    # The evidence supports the swap, but a dry run has NOT persisted the
    # binding: INT-002 must stay not-verified (never pass) with a static detail
    # pointing at the un-run --apply.
    int_002 = next(
        f for f in result["manifest"]["findings"] if f["id"] == "INT-002"
    )
    assert int_002["status"] == "not-verified"
    assert "--apply has not persisted the binding" in int_002["detail"]


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
    # The core binding: the tool's MCP server entry now points at the real
    # endpoint (not a mock) — this is what INT-002 pass and safe-check's
    # binding check both key on.
    assert mcp_config["servers"]["returns_get_case"]["url"] == REAL_ENDPOINT

    manifest = json.loads((tmp_path / connect.DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8"))
    assert manifest["integration_state"] == "real-verified"
    assert manifest["target_state"] == "real-verified"
    assert set(manifest["changed_paths"]) == {connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH}
    # The URL is persisted ONLY in mcp-config.json — never in the manifest.
    assert REAL_ENDPOINT not in json.dumps(manifest)
    assert manifest["evidence_summary"]["endpoint_configured"] is True
    assert manifest["evidence_summary"]["endpoint_verified"] is True


def test_apply_preserves_prior_unrelated_mcp_config_content(tmp_path):
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(
        json.dumps({
            "servers": {"other-tool": {"type": "http", "url": "https://other.example/mcp"}},
            "unrelated_top_level": {"keep": True},
        }),
        encoding="utf-8",
    )

    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True)

    mcp_config = json.loads(mcp_full.read_text(encoding="utf-8"))
    # Unrelated server + unrelated top-level config survive untouched.
    assert mcp_config["servers"]["other-tool"] == {"type": "http", "url": "https://other.example/mcp"}
    assert mcp_config["unrelated_top_level"] == {"keep": True}
    # The tool's own server entry is added and bound to the real endpoint.
    assert mcp_config["servers"]["returns_get_case"]["url"] == REAL_ENDPOINT
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
# The real-endpoint contract — validate, persist ONLY in mcp-config, gate
# INT-002 pass on the persisted real binding.
# ---------------------------------------------------------------------------
def _seed_mock_mcp_config(tmp_path, *, tool="returns_get_case", url="https://returns-mockserver.internal/mcp"):
    """Seed a starting mcp-config whose tool server still points at a mock."""
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(json.dumps({"servers": {tool: {"type": "http", "url": url}}}), encoding="utf-8")
    return mcp_full


def test_apply_replaces_starting_mock_url_and_binding_is_verified(tmp_path):
    # Requirement 9, bullet 1 — starting mock url + full evidence + real endpoint:
    # servers[tool].url is replaced, integration_state real-verified, INT-002
    # pass, and safe-check sees NO integration-binding gap.
    mcp_full = _seed_mock_mcp_config(tmp_path)
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=True)

    assert result["integration_state"] == "real-verified"
    mcp_config = json.loads(mcp_full.read_text(encoding="utf-8"))
    assert mcp_config["servers"]["returns_get_case"]["url"] == REAL_ENDPOINT
    assert mcp_config["integrations"]["returns_get_case"]["state"] == "real-verified"
    assert _finding_status_map(result)["INT-002"] == "pass"

    # safe-check must now read the persisted binding as real (no gap). Import it
    # lazily so this suite has no hard dependency on the sibling skill layout.
    safe_check = _import_safe_check()
    gaps = safe_check.integration_binding_gaps(
        [{"id": "returns_get_case", "availability": "real"}], mcp_config
    )
    assert gaps == []


def test_dry_run_with_endpoint_does_not_edit_and_int_002_not_verified(tmp_path):
    # Requirement 9, bullet 2 — a dry run with full evidence + a valid endpoint
    # writes no production config and holds INT-002 not-verified (the binding is
    # not persisted), but the plan names the validated-endpoint action.
    mcp_full = _seed_mock_mcp_config(tmp_path)
    prior = mcp_full.read_bytes()
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=False)

    assert result["changed_paths"] == []
    assert mcp_full.read_bytes() == prior  # mock url untouched
    assert _finding_status_map(result)["INT-002"] == "not-verified"
    es = result["manifest"]["evidence_summary"]
    assert es["endpoint_configured"] is True
    assert es["endpoint_verified"] is False
    mcp_step = next(s for s in result["apply_plan"] if s["path"] == connect.DEFAULT_MCP_CONFIG_PATH)
    assert "validated real endpoint" in mcp_step["description"]


def test_dry_run_without_endpoint_still_assesses_and_plan_flags_requirement(tmp_path):
    # Requirement 9, bullet 2 + requirement 5 — a dry run WITHOUT an endpoint
    # still assesses conformance/evidence (target real-verified) but the plan
    # says the endpoint must be supplied before --apply.
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=False,
                  real_endpoint=None)

    assert result["target_state"] == "real-verified"
    assert result["changed_paths"] == []
    assert result["manifest"]["evidence_summary"]["endpoint_configured"] is False
    mcp_step = next(s for s in result["apply_plan"] if s["path"] == connect.DEFAULT_MCP_CONFIG_PATH)
    assert "supply --real-endpoint before --apply" in mcp_step["description"]


def test_apply_verified_without_endpoint_raises_and_writes_nothing(tmp_path):
    # Requirement 9, bullet 3 — apply=True reaching real-verified but no endpoint
    # is a controlled ConnectEvidenceError with nothing written (INT-002 can
    # never pass on an unbound swap).
    obo, role = full_evidence()
    with pytest.raises(ConnectEvidenceError, match="real_endpoint is required"):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True, real_endpoint=None)

    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / "tests" / "threadlight_connect").exists()


@pytest.mark.parametrize(
    "bad_endpoint",
    [
        "https://returns-mock.internal/mcp",       # delimited 'mock'
        "https://mocked-api.example.com/mcp",      # 'mocked'
        "https://mockserver.example.com/mcp",      # 'mockserver'
        "http://api.example.com/mcp",              # non-local http
        "ftp://api.example.com/mcp",               # wrong scheme
        "https:///nohost/mcp",                     # no hostname
        "https://api.example.com/mcp#frag",        # fragment
        "https://user:pass@api.example.com/mcp",   # embedded userinfo
        "https://api.example.com/mcp?token=abc",   # secret query param
        "https://blob.core.windows.net/c?sv=2021-08-06&sig=deadbeef",  # SAS
        "https://api.example.com/ mcp",            # whitespace
        "",                                        # empty
    ],
)
def test_apply_rejects_bad_endpoint_with_nothing_written(tmp_path, bad_endpoint):
    # Requirement 9, bullet 3 — mock / credential / SAS / malformed endpoints are
    # all rejected before any write.
    obo, role = full_evidence()
    with pytest.raises(ConnectEvidenceError):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True, real_endpoint=bad_endpoint)
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()


@pytest.mark.parametrize(
    "secret_name",
    [
        "subscription-key",
        "subscription_key",
        "SubscriptionKey",
        "api-key",
        "API_KEY",
        "access-key",
        "access_key",
        "shared-access-key",
        "shared_access_key",
        "SharedAccessKey",
    ],
)
def test_apply_rejects_separator_and_case_variants_of_secret_query_names(
    tmp_path, secret_name
):
    obo, role = full_evidence()
    endpoint = f"https://api.example.com/mcp?{secret_name}=deadbeef"

    with pytest.raises(ConnectEvidenceError, match="secret/SAS/token"):
        _run(
            tmp_path,
            obo_evidence=obo,
            role_evidence=role,
            current_agent_identity=CURRENT_IDENTITY,
            apply=True,
            real_endpoint=endpoint,
        )

    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()


@pytest.mark.parametrize("param_name", ["trace-id", "api-version", "access-mode"])
def test_apply_allows_ordinary_hyphenated_nonsecret_query_names(tmp_path, param_name):
    obo, role = full_evidence()
    endpoint = f"https://api.example.com/mcp?{param_name}=ordinary"

    result = _run(
        tmp_path,
        obo_evidence=obo,
        role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY,
        apply=True,
        real_endpoint=endpoint,
    )

    assert result["integration_state"] == "real-verified"
    mcp = json.loads(
        (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_text(encoding="utf-8")
    )
    assert mcp["servers"]["returns_get_case"]["url"] == endpoint


def test_apply_allows_mockingbird_substring_endpoint(tmp_path):
    # Requirement 9, bullet 3 — a real host that merely CONTAINS 'mock' as a
    # substring (mockingbird) is NOT a mock endpoint and must be accepted.
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=True,
                  real_endpoint="https://mockingbird.example.com/mcp")
    assert result["integration_state"] == "real-verified"
    mcp_config = json.loads((tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_text(encoding="utf-8"))
    assert mcp_config["servers"]["returns_get_case"]["url"] == "https://mockingbird.example.com/mcp"


def test_apply_allows_localhost_http_endpoint(tmp_path):
    # http is permitted ONLY for localhost / 127.0.0.1 (local testing).
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=True,
                  real_endpoint="http://127.0.0.1:8080/mcp")
    assert result["integration_state"] == "real-verified"


def test_apply_preserves_unrelated_server_fields_and_strips_mock_transport(tmp_path):
    # Requirement 9, bullet 4 + bullet on preservation — the tool's own server
    # entry keeps safe unrelated fields (headers, type) while the mutually
    # exclusive mock/stdio transport fields (command/args/mock_url) are dropped.
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(json.dumps({"servers": {"returns_get_case": {
        "type": "http",
        "headers": {"x-trace": "keep"},
        "command": "python",
        "args": ["mock_server.py"],
        "mock_url": "https://returns-mock.internal/mcp",
    }}}), encoding="utf-8")

    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True)

    entry = json.loads(mcp_full.read_text(encoding="utf-8"))["servers"]["returns_get_case"]
    assert entry["url"] == REAL_ENDPOINT
    assert entry["headers"] == {"x-trace": "keep"}  # safe unrelated field preserved
    assert entry["type"] == "http"                  # safe unrelated field preserved
    assert "command" not in entry                   # stdio transport dropped
    assert "args" not in entry
    assert "mock_url" not in entry                  # mock-marker field dropped


@pytest.mark.parametrize(
    "mock_fields",
    [
        {"url": "https://returns-mock.internal/mcp"},
        {"host": "returns-mock.internal"},
        {"name": "returns mock server"},
        {"endpoint": "https://mockserver.internal/mcp"},
        {
            "url": "https://returns-mock.internal/mcp",
            "host": "returns-mock.internal",
            "name": "returns mock server",
            "endpoint": "https://mockserver.internal/mcp",
        },
    ],
)
def test_apply_removes_every_safe_check_mock_binding_alias(tmp_path, mock_fields):
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(
        json.dumps({"servers": {"returns_get_case": mock_fields}}),
        encoding="utf-8",
    )

    obo, role = full_evidence()
    _run(
        tmp_path,
        obo_evidence=obo,
        role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY,
        apply=True,
    )

    mcp = json.loads(mcp_full.read_text(encoding="utf-8"))
    entry = mcp["servers"]["returns_get_case"]
    assert entry["url"] == REAL_ENDPOINT
    assert "host" not in entry
    assert "endpoint" not in entry
    assert not connect._server_is_provably_mock(entry)
    safe_check = _import_safe_check()
    assert safe_check.integration_binding_gaps(
        integrations=[{"id": "returns_get_case", "availability": "real"}],
        mcp_config=mcp,
    ) == []


def test_apply_preserves_non_mock_mockingbird_descriptive_name(tmp_path):
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(
        json.dumps(
            {
                "servers": {
                    "returns_get_case": {
                        "name": "Mockingbird Returns Connector",
                        "host": "old-transport.internal",
                        "endpoint": "https://old-transport.internal/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    obo, role = full_evidence()
    _run(
        tmp_path,
        obo_evidence=obo,
        role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY,
        apply=True,
    )

    entry = json.loads(mcp_full.read_text(encoding="utf-8"))["servers"][
        "returns_get_case"
    ]
    assert entry["name"] == "Mockingbird Returns Connector"
    assert "host" not in entry
    assert "endpoint" not in entry


@pytest.mark.parametrize(
    "malformed",
    [
        {"servers": []},                                   # servers not an object
        {"servers": {"returns_get_case": "not-a-dict"}},   # tool entry not an object
        {"servers": {"returns_get_case": ["x"]}},          # tool entry a list
        {"integrations": 5},                               # integrations not an object
    ],
)
def test_apply_fails_closed_on_malformed_mcp_shape(tmp_path, malformed):
    # Requirement 9, bullet on malformed shapes — a malformed servers/tool/
    # integrations shape fails CLOSED (raises, nothing written) rather than
    # silently overwriting it.
    mcp_full = tmp_path / connect.DEFAULT_MCP_CONFIG_PATH
    mcp_full.parent.mkdir(parents=True, exist_ok=True)
    mcp_full.write_text(json.dumps(malformed), encoding="utf-8")
    prior = mcp_full.read_bytes()

    obo, role = full_evidence()
    with pytest.raises(ConnectEvidenceError):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True)

    assert mcp_full.read_bytes() == prior  # malformed file untouched
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    # the fail-closed check runs before the conformance-test scaffold is written
    assert not (tmp_path / "tests" / "threadlight_connect").exists()


def test_failed_apply_restores_prior_mock_url_byte_identical(tmp_path, monkeypatch):
    # Requirement 9, bullet on transaction failures — a second-destination write
    # failure rolls the whole transaction back, restoring the STARTING mock url
    # (and SPEC + manifest) byte-for-byte.
    mcp_full = _seed_mock_mcp_config(tmp_path)
    spec_full = tmp_path / connect.DEFAULT_SPEC_PATH
    spec_full.parent.mkdir(parents=True, exist_ok=True)
    spec_full.write_text("# Prior SPEC\n", encoding="utf-8")
    # a prior manifest so all three destinations have prior bytes
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=False, generated_at=PINNED)
    # reset the mcp to a mock url (the dry run above did not touch it)
    mcp_full.write_text(json.dumps({"servers": {"returns_get_case": {"type": "http", "url": "https://returns-mockserver.internal/mcp"}}}), encoding="utf-8")
    prior_mcp = mcp_full.read_bytes()
    prior_spec = spec_full.read_bytes()
    prior_manifest = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()

    real_replace = connect.os.replace
    manifest_name = Path(connect.DEFAULT_MANIFEST_PATH).name

    def _boom(source, destination):
        # Let SPEC + mcp-config commit, then fail the manifest replace (third
        # destination) so both earlier commits roll back.
        if Path(destination).name == manifest_name:
            raise OSError("simulated failure on manifest destination")
        return real_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError) as excinfo:
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True,
             generated_at="2026-08-19T10:00:00+00:00")
    assert not isinstance(excinfo.value, connect.ConnectInconsistentStateError)

    # the STARTING mock url is restored, not the real endpoint
    assert mcp_full.read_bytes() == prior_mcp
    assert json.loads(mcp_full.read_text(encoding="utf-8"))["servers"]["returns_get_case"]["url"] == "https://returns-mockserver.internal/mcp"
    assert spec_full.read_bytes() == prior_spec
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest


def test_post_apply_binding_mismatch_rolls_back_all_three(tmp_path, monkeypatch):
    # Requirement 9 + requirement 4 — if the persisted mcp-config's effective
    # endpoint does NOT match the validated real endpoint after commit (a
    # tampered/racey write), the post-apply postcondition rolls back all three
    # and surfaces an error without a pass.
    mcp_full = _seed_mock_mcp_config(tmp_path)
    spec_full = tmp_path / connect.DEFAULT_SPEC_PATH
    spec_full.parent.mkdir(parents=True, exist_ok=True)
    spec_full.write_text("# Prior SPEC\n", encoding="utf-8")
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=False, generated_at=PINNED)
    mcp_full.write_text(json.dumps({"servers": {"returns_get_case": {"type": "http", "url": "https://returns-mockserver.internal/mcp"}}}), encoding="utf-8")
    prior_mcp = mcp_full.read_bytes()
    prior_spec = spec_full.read_bytes()
    prior_manifest = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()

    # The predicted-binding pre-check and the post-apply re-read both call
    # _effective_mcp_endpoint. Tamper ONLY the second call (the post-commit
    # re-read) so the pre-check passes and the postcondition is what trips.
    real_effective = connect._effective_mcp_endpoint
    calls = {"n": 0}

    def _tampered(mcp_data, tool_name):
        calls["n"] += 1
        value = real_effective(mcp_data, tool_name)
        if calls["n"] >= 2 and value == REAL_ENDPOINT:
            return "https://api.tampered.example.com/mcp"
        return value

    monkeypatch.setattr(connect, "_effective_mcp_endpoint", _tampered)

    with pytest.raises(ConnectApplyError, match="post-apply verification failed"):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True,
             generated_at="2026-08-19T10:00:00+00:00")

    # all three rolled back to their prior bytes; no pass persisted
    assert mcp_full.read_bytes() == prior_mcp
    assert spec_full.read_bytes() == prior_spec
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest


def test_manifest_never_persists_the_real_endpoint_url(tmp_path):
    # Privacy — the validated URL lives ONLY in mcp-config.json, never in the
    # connect manifest (or its evidence_summary).
    _seed_mock_mcp_config(tmp_path)
    obo, role = full_evidence()
    result = _run(tmp_path, obo_evidence=obo, role_evidence=role,
                  current_agent_identity=CURRENT_IDENTITY, apply=True,
                  real_endpoint="https://secret-host.example.com/mcp")
    on_disk = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8")
    assert "secret-host.example.com" not in on_disk
    assert "secret-host.example.com" not in json.dumps(result["manifest"])


# ---------------------------------------------------------------------------
# Requirement 6 — the connect mock-endpoint predicate is byte-identical to
# threadlight-safe-check's. Rather than cross-import, pin them with a parity
# corpus (mock / mocked / mockserver match; mockingbird does not).
# ---------------------------------------------------------------------------
def _import_safe_check():
    import importlib.util

    sc_path = REPO_ROOT / "skills" / "threadlight-safe-check" / "scripts" / "safe_check.py"
    spec = importlib.util.spec_from_file_location("threadlight_safe_check", sc_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("candidate", "is_mock"),
    [
        ("https://mock.example/mcp", True),
        ("erp-mock.internal", True),
        ("svc_mock", True),
        ("https://mocked-api.local/mcp", True),
        ("https://mockserver.internal/mcp", True),
        ("returns-MockServer", True),
        ("local mock", True),
        ("https://mockingbird.example.com/mcp", False),
        ("https://smock.contoso.com/mcp", False),
        ("https://mockapifactory.io/mcp", False),
        ("https://erp.contoso.com/mcp", False),
    ],
)
def test_mock_marker_parity_with_safe_check(candidate, is_mock):
    safe_check = _import_safe_check()
    connect_says_mock = connect._endpoint_is_mock(candidate)
    safe_check_says_mock = bool(safe_check.MOCK_ENDPOINT_MARKER.search(candidate))
    assert connect_says_mock == is_mock
    assert connect_says_mock == safe_check_says_mock, (
        f"connect and safe-check disagree on {candidate!r}"
    )


@pytest.mark.parametrize("field", ["url", "host", "name", "endpoint"])
@pytest.mark.parametrize(
    ("candidate", "is_mock"),
    [
        ("https://mock.example/mcp", True),
        ("returns-MockServer", True),
        ("local mock", True),
        ("Mockingbird Returns Connector", False),
        ("https://erp.contoso.com/mcp", False),
    ],
)
def test_mock_server_field_corpus_parity_with_safe_check(field, candidate, is_mock):
    safe_check = _import_safe_check()
    server = {field: candidate}

    connect_says_mock = connect._server_is_provably_mock(server)
    safe_check_says_mock = safe_check._endpoint_is_provably_mock(server)

    assert connect_says_mock == is_mock
    assert connect_says_mock == safe_check_says_mock



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
            "description": (
                "Point the returns_get_case MCP server entry at the real endpoint "
                "(supply --real-endpoint before --apply)"
            ),
        },
    ]


def test_build_apply_plan_with_endpoint_names_validated_binding_without_url(tmp_path):
    # When a validated real endpoint is in hand the plan says so — but never
    # leaks the URL itself into the (manifest-persisted) plan.
    plan = build_apply_plan(
        tmp_path, "specs/SPEC.md", "infra/mcp-config.json", "returns_get_case",
        real_endpoint_present=True,
    )
    mcp_step = next(s for s in plan if s["path"] == "infra/mcp-config.json")
    assert mcp_step["description"] == (
        "Point the returns_get_case MCP server entry at the validated real endpoint"
    )
    assert "http" not in mcp_step["description"]  # no URL leaked


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
        findings=_int_findings(),
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
        status="complete", findings=_int_findings(), generated_at=PINNED,
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
    # unevaluated conformance -> INT-001/002 not-verified (never a vacuous pass)
    assert finding_ids["INT-001"] == "not-verified"
    assert finding_ids["INT-002"] == "not-verified"
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
        findings=_int_findings(),
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
    with pytest.raises(ManifestValidationError, match=re.escape("contract.fields[0]")):
        connect.validate_connect_manifest(manifest)


def _manifest_with_all_nested_items():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    conformance = check_conformance(contract, drifting_real_response())
    manifest = _valid_manifest(conformance=conformance)
    manifest["findings"] = _int_findings(("must-fix", "must-fix", "pass", "pass"))
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
        (("evidence_summary",), "endpoint_configured"),
        (("evidence_summary",), "endpoint_verified"),
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


# ---------------------------------------------------------------------------
# Task 3 — hand-validator / jsonschema parity for every nested value constraint
#
# validate_connect_manifest() is a stdlib-only mirror of
# references/connect-manifest.schema.json (+ the referenced
# data-contract.schema.json). These tests pin that mirror to the real schemas:
# every malformed instance below must be rejected by BOTH the hand validator
# and jsonschema, and every representative valid manifest accepted by BOTH.
# jsonschema is a test-only dependency — the shipping skill never imports it.
# ---------------------------------------------------------------------------
_REFERENCES_DIR = SKILL_ROOT / "references"


@pytest.fixture(scope="module")
def jsonschema_validator():
    """A Draft-07 validator over the on-disk manifest schema, with the
    ``$ref``-ed data-contract schema resolvable from an in-memory registry and
    jsonschema's *standard* ``date-time`` format checker.

    The manifest schemas declare ``format: date-time`` on ``generated_at``,
    ``freshness.source_oldest_at``, and the contract's ``generated_at``.
    jsonschema only enforces ``date-time`` when an RFC-3339 backend (e.g.
    ``rfc3339-validator``) is installed; without it the check is a silent no-op
    that would ACCEPT the malformed timestamps the shared envelope rejects,
    defeating the parity suite. We deliberately use the stock ``FormatChecker``
    — NOT one wired to the shared helper — so every timestamp accept/reject
    below is proven against a real, independent RFC-3339 implementation, and we
    skip when no backend is present rather than assert a false parity."""
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")

    manifest_schema = json.loads(
        (_REFERENCES_DIR / "connect-manifest.schema.json").read_text(encoding="utf-8")
    )
    contract_schema = json.loads(
        (_REFERENCES_DIR / "data-contract.schema.json").read_text(encoding="utf-8")
    )
    registry = referencing.Registry().with_resources(
        [
            (manifest_schema["$id"], referencing.Resource.from_contents(manifest_schema)),
            (contract_schema["$id"], referencing.Resource.from_contents(contract_schema)),
        ]
    )

    format_checker = jsonschema.FormatChecker()
    if "date-time" not in format_checker.checkers:
        pytest.skip(
            "jsonschema's standard 'date-time' format check requires an RFC-3339 "
            "backend (e.g. rfc3339-validator); without it the parity suite cannot "
            "prove timestamp accept/reject against an independent validator"
        )

    return jsonschema.Draft7Validator(
        manifest_schema, registry=registry, format_checker=format_checker
    )


def _rich_connect_manifest(**overrides):
    """A schema-complete connect manifest built through the real emitter, with
    every nested array populated (a drift difference, a finding, an apply-plan
    item, a changed path, a required role) so a single mutation can exercise any
    leaf the schema constrains."""
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    conformance = check_conformance(contract, drifting_real_response())
    params = dict(
        tool_name="returns_get_case",
        integration_state="real-unverified",
        target_state="real-drift",
        contract=contract,
        conformance=conformance,
        evidence_summary={
            "obo_present": True,
            "obo_user_scoped": True,
            "roles_revalidated": True,
            "required_roles": ["Case.Read"],
        },
        apply_plan=[
            {"path": "specs/SPEC.md", "action": "create", "description": "scaffold spec"},
        ],
        changed_paths=["specs/SPEC.md"],
        apply=True,
        status="complete",
        findings=_int_findings(("must-fix", "must-fix", "pass", "pass")),
        generated_at=PINNED,
    )
    params.update(overrides)
    return connect.build_connect_manifest(**params)


def _partial_connect_manifest():
    """A representative *partial* manifest: real response had no items to check,
    so conformance is unevaluated (not a vacuous pass) and evidence is absent."""
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    conformance = check_conformance(contract, {"items": []})
    return connect.build_connect_manifest(
        tool_name="returns_get_case",
        integration_state="mock",
        target_state="real-unverified",
        contract=contract,
        conformance=conformance,
        evidence_summary={
            "obo_present": True,
            "obo_user_scoped": False,
            "roles_revalidated": False,
            "required_roles": [],
        },
        apply_plan=[
            {"path": "specs/SPEC.md", "action": "create", "description": "scaffold spec"},
        ],
        changed_paths=[],
        apply=False,
        status="partial",
        findings=_int_findings(
            ("not-verified", "not-verified", "not-verified", "not-verified")
        ),
        generated_at=PINNED,
    )


def _set_path(manifest, path, value):
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return manifest


# --- Schema-valid boundary manifests: every one must be accepted by BOTH the
# hand validator and Draft7Validator + FormatChecker. These pin the tricky
# edges — an integral float for an integer field, empty arrays, and null
# nullable fields — where a naive mirror could drift from Draft-07 semantics.
def _manifest_integral_float_item_count():
    """item_count as 1.0: Draft-07 treats a zero-fraction float as an integer
    (1 and 1.0 are the same JSON value), so both validators must accept it."""
    manifest = copy.deepcopy(_rich_connect_manifest())
    manifest["conformance"]["item_count"] = 1.0
    return manifest


def _manifest_all_empty_arrays():
    """Every EMPTY-able schema array emptied at once — differences,
    required_roles, apply_plan, changed_paths, and contract.fields declare no
    minItems, so all-empty is valid under both validators. ``findings`` is the
    one exception: it is a fixed four-element tuple (INT-001..004), so it keeps
    its four entries rather than being emptied."""
    manifest = copy.deepcopy(_valid_manifest())
    manifest["conformance"]["differences"] = []
    manifest["evidence_summary"]["required_roles"] = []
    manifest["apply_plan"] = []
    manifest["changed_paths"] = []
    manifest["contract"]["fields"] = []
    return manifest


def _manifest_null_nullable_fields():
    """Every nullable leaf set to null: freshness.source_oldest_at plus each
    contract field's type/cardinality (declared ``["string", "null"]``)."""
    manifest = copy.deepcopy(_rich_connect_manifest())
    manifest["freshness"]["source_oldest_at"] = None
    for field in manifest["contract"]["fields"]:
        field["type"] = None
        field["cardinality"] = None
    return manifest


def _manifest_with_two_element_arrays():
    """A schema-valid manifest whose every constrained array carries at least two
    valid elements, so a single mutation of the SECOND element (index 1)
    exercises the validator's array-index path reporting. ``findings`` already
    holds the fixed four-element INT tuple, so index 1 (INT-002) is present
    without appending a fifth (which the tuple schema forbids)."""
    manifest = copy.deepcopy(_rich_connect_manifest())
    manifest["conformance"]["differences"].append(
        {"field": "f2", "expected": "string|required", "actual": "missing", "path": "$.items[1].f2"}
    )
    manifest["evidence_summary"]["required_roles"].append("Case.Write")
    manifest["contract"]["fields"].append(
        {"name": "second", "required": False, "type": "string", "cardinality": "single"}
    )
    manifest["apply_plan"].append(
        {"path": "specs/OTHER.md", "action": "update", "description": "second file"}
    )
    manifest["changed_paths"].append("specs/OTHER.md")
    return manifest


# (label, path-into-manifest, replacement-value) — each mutates exactly one leaf
# of an otherwise-valid manifest into a schema violation.
_TIMESTAMP_WHITESPACE_CASES = [
    (f"{location} {whitespace}", path, value)
    for location, path in [
        ("generated_at", ("generated_at",)),
        ("source_oldest_at", ("freshness", "source_oldest_at")),
        ("contract.generated_at", ("contract", "generated_at")),
    ]
    for whitespace, value in [
        ("trailing newline", "2026-08-17T10:00:00Z\n"),
        ("trailing CRLF", "2026-08-17T10:00:00Z\r\n"),
        ("leading space", " 2026-08-17T10:00:00Z"),
        ("internal tab", "2026-08-17T10:00:00\tZ"),
    ]
]


_MALFORMED_MANIFEST_CASES = [
    ("required='yes' (boolean is a string)", ("contract", "fields", 0, "required"), "yes"),
    ("passed='yes' (boolean is a string)", ("conformance", "passed"), "yes"),
    ("obo_present='yes' (boolean is a string)", ("evidence_summary", "obo_present"), "yes"),
    ("apply action 'delete' outside enum", ("apply_plan", 0, "action"), "delete"),
    ("changed_paths [42] (int item)", ("changed_paths",), [42]),
    ("difference.field wrong item type", ("conformance", "differences", 0, "field"), 42),
    ("required_roles wrong item type", ("evidence_summary", "required_roles"), [1]),
    ("apply_plan.path wrong item type", ("apply_plan", 0, "path"), 5),
    ("apply_plan.description wrong type", ("apply_plan", 0, "description"), 9),
    ("item_count below minimum (-1)", ("conformance", "item_count"), -1),
    ("valid_for_hours below minimum (0)", ("freshness", "valid_for_hours"), 0),
    ("status invalid enum", ("status",), "passed"),
    ("integration_state invalid enum", ("integration_state",), "bogus-state"),
    ("target_state invalid enum", ("target_state",), "sideways"),
    ("finding status invalid enum", ("findings", 0, "status"), "broken"),
    ("contract field type invalid enum", ("contract", "fields", 0, "type"), "weird"),
    ("contract field cardinality invalid enum", ("contract", "fields", 0, "cardinality"), "many"),
    ("apply not a boolean", ("apply",), "yes"),
    ("evaluated is int 1 not a boolean", ("conformance", "evaluated"), 1),
    ("item_count is a boolean not an int", ("conformance", "item_count"), True),
    ("changed_paths not an array", ("changed_paths",), "specs/SPEC.md"),
    ("apply_plan not an array", ("apply_plan",), {}),
    ("tool_name empty violates minLength", ("tool_name",), ""),
    ("contract.tool_name empty violates minLength", ("contract", "tool_name"), ""),
    ("contract field name empty violates minLength", ("contract", "fields", 0, "name"), ""),
    ("unknown top-level key", ("surprise",), "nope"),
    ("unknown conformance key", ("conformance", "extra"), 1),
    ("unknown evidence_summary key", ("evidence_summary", "leak"), "x"),
    ("unknown difference key", ("conformance", "differences", 0, "unexpected"), "x"),
    ("manifest schema wrong const", ("schema",), "wrong-manifest/v1"),
    ("contract schema wrong const", ("contract", "schema"), "wrong-contract/v1"),
    # Shared-envelope scalar parity — the manifest schema must be as strict as the
    # shared envelope: non-empty strings and RFC-3339 timestamps, enforced by
    # jsonschema's minLength + the date-time FormatChecker registered above.
    ("tool_version empty violates minLength", ("tool_version",), ""),
    ("generated_at empty violates minLength", ("generated_at",), ""),
    ("generated_at not a timestamp", ("generated_at",), "not-a-timestamp"),
    ("generated_at date without time", ("generated_at",), "2026-08-17"),
    ("generated_at timezone-less date-time", ("generated_at",), "2026-08-17T10:00:00"),
    ("source_oldest_at not a timestamp", ("freshness", "source_oldest_at"), "nope"),
    ("source_oldest_at empty string", ("freshness", "source_oldest_at"), ""),
    (
        "source_oldest_at timezone-less date-time",
        ("freshness", "source_oldest_at"),
        "2026-08-17T10:00:00",
    ),
    ("contract.generated_at not a timestamp", ("contract", "generated_at"), "bad-ts"),
    (
        "contract.generated_at timezone-less date-time",
        ("contract", "generated_at"),
        "2026-08-17T10:00:00",
    ),
    ("valid_for_hours 1.5 is not integral", ("freshness", "valid_for_hours"), 1.5),
    ("item_count 1.5 is not integral", ("conformance", "item_count"), 1.5),
] + _TIMESTAMP_WHITESPACE_CASES


@pytest.mark.parametrize(
    ("path", "value"),
    [(case[1], case[2]) for case in _MALFORMED_MANIFEST_CASES],
    ids=[case[0] for case in _MALFORMED_MANIFEST_CASES],
)
def test_malformed_manifest_rejected_by_both_validators(path, value, jsonschema_validator):
    manifest = _set_path(copy.deepcopy(_rich_connect_manifest()), path, value)

    with pytest.raises(ManifestValidationError):
        connect.validate_connect_manifest(manifest)

    assert not jsonschema_validator.is_valid(manifest), (
        "jsonschema unexpectedly ACCEPTED a manifest the hand validator rejected "
        f"(mutation at {path} -> {value!r})"
    )


@pytest.mark.parametrize(
    ("label", "builder"),
    [
        ("complete-verified", _valid_manifest),
        ("complete-drift", _rich_connect_manifest),
        ("partial-unevaluated", _partial_connect_manifest),
        ("integral-float-item-count", _manifest_integral_float_item_count),
        ("all-empty-arrays", _manifest_all_empty_arrays),
        ("null-nullable-fields", _manifest_null_nullable_fields),
        ("two-element-arrays", _manifest_with_two_element_arrays),
    ],
)
def test_valid_manifest_accepted_by_both_validators(label, builder, jsonschema_validator):
    manifest = builder()

    connect.validate_connect_manifest(manifest)  # must not raise

    errors = list(jsonschema_validator.iter_errors(manifest))
    assert not errors, (
        f"jsonschema REJECTED the {label} manifest the hand validator accepted: "
        + "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
    )


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-17T10:00:00Z",
        "2026-08-17T10:00:00+05:30",
        "2026-08-17T10:00:00-07:00",
    ],
)
def test_rfc3339_z_and_offsets_accepted_at_all_timestamp_locations(
    timestamp, jsonschema_validator
):
    manifest = copy.deepcopy(_rich_connect_manifest())
    manifest["generated_at"] = timestamp
    manifest["freshness"]["source_oldest_at"] = timestamp
    manifest["contract"]["generated_at"] = timestamp

    connect.validate_connect_manifest(manifest)
    assert jsonschema_validator.is_valid(manifest)


@pytest.mark.parametrize(
    ("path", "value", "expected_fragment"),
    [
        (("conformance", "passed"), "yes", "conformance.passed"),
        (("conformance", "item_count"), -1, "conformance.item_count"),
        (("evidence_summary", "obo_present"), "yes", "evidence_summary.obo_present"),
        (("evidence_summary", "required_roles"), [1], "evidence_summary.required_roles[0]"),
        (("apply_plan", 0, "action"), "delete", "apply_plan[0].action"),
        (("changed_paths",), [42], "changed_paths[0]"),
        (("contract", "fields", 0, "required"), "yes", "contract.fields[0].required"),
        (("contract", "fields", 0, "type"), "weird", "contract.fields[0].type"),
    ],
)
def test_validation_error_names_offending_path(path, value, expected_fragment):
    manifest = _set_path(copy.deepcopy(_rich_connect_manifest()), path, value)

    with pytest.raises(ManifestValidationError, match=re.escape(expected_fragment)):
        connect.validate_connect_manifest(manifest)


# Every array leaf must report the offending INDEX, not just the array name — a
# malformed second element (index 1) must surface a ``[1]`` path so a large
# manifest points straight at the bad row instead of the whole collection.
_SECOND_ELEMENT_CASES = [
    ("findings[1].status", ("findings", 1, "status"), "broken", "findings[1]"),
    (
        "conformance.differences[1].field",
        ("conformance", "differences", 1, "field"),
        42,
        "conformance.differences[1]",
    ),
    (
        "evidence_summary.required_roles[1]",
        ("evidence_summary", "required_roles", 1),
        99,
        "evidence_summary.required_roles[1]",
    ),
    (
        "contract.fields[1].type",
        ("contract", "fields", 1, "type"),
        "weird",
        "contract.fields[1]",
    ),
    ("apply_plan[1].action", ("apply_plan", 1, "action"), "delete", "apply_plan[1]"),
    ("changed_paths[1]", ("changed_paths", 1), 42, "changed_paths[1]"),
]


def test_second_element_manifest_is_itself_valid():
    # Precondition: the fixture manifest (before mutation) must validate, so each
    # case below fails ONLY because of its deliberate second-element mutation.
    connect.validate_connect_manifest(_manifest_with_two_element_arrays())


@pytest.mark.parametrize(
    ("path", "value", "expected_fragment"),
    [(case[1], case[2], case[3]) for case in _SECOND_ELEMENT_CASES],
    ids=[case[0] for case in _SECOND_ELEMENT_CASES],
)
def test_invalid_second_array_element_names_indexed_path(path, value, expected_fragment):
    manifest = _set_path(_manifest_with_two_element_arrays(), path, value)

    with pytest.raises(ManifestValidationError) as excinfo:
        connect.validate_connect_manifest(manifest)

    message = str(excinfo.value)
    assert "[1]" in message, f"error should name the second element's index: {message!r}"
    assert expected_fragment in message, (
        f"error should name the full indexed path {expected_fragment!r}: {message!r}"
    )


@pytest.mark.parametrize(
    ("path", "value", "expected_fragment"),
    [(case[1], case[2], case[3]) for case in _SECOND_ELEMENT_CASES],
    ids=[case[0] for case in _SECOND_ELEMENT_CASES],
)
def test_invalid_second_array_element_rejected_by_both_validators(
    path, value, expected_fragment, jsonschema_validator
):
    manifest = _set_path(_manifest_with_two_element_arrays(), path, value)

    with pytest.raises(ManifestValidationError):
        connect.validate_connect_manifest(manifest)

    assert not jsonschema_validator.is_valid(manifest), (
        "jsonschema unexpectedly ACCEPTED a manifest the hand validator rejected "
        f"(second-element mutation at {path} -> {value!r})"
    )


# ---------------------------------------------------------------------------
# Task 3.1 — role/OBO evidence: no `or []` coercion; absent vs supplied;
# non-empty unique string lists; non-empty-string-or-null identity; real bools.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["required_roles", "validated_roles", "granted_roles"])
@pytest.mark.parametrize("bad", ["", 0, {}, False, None])
def test_role_list_falsey_non_list_value_raises_not_coerced(key, bad):
    # The old `evidence.get(key) or []` silently turned each of these falsey
    # non-lists into an accepted empty list. They must now raise instead.
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence({"revalidated": True, key: bad})


@pytest.mark.parametrize(
    "bad_list",
    [["Case.Read", 5], [True], [{}], [["nested"]], ["Case.Read", None], [1, 2]],
)
def test_role_list_mixed_or_nonstring_items_raise(bad_list):
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence({"revalidated": True, "required_roles": bad_list})


@pytest.mark.parametrize("bad_list", [[""], ["Case.Read", ""]])
def test_role_list_empty_role_name_raises(bad_list):
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence({"revalidated": True, "required_roles": bad_list})


@pytest.mark.parametrize("key", ["required_roles", "validated_roles", "granted_roles"])
def test_role_list_duplicate_role_names_raise(key):
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence(
            {"revalidated": True, key: ["Case.Read", "Case.Read"]}
        )


def test_role_lists_absent_default_to_empty_without_coercion():
    # ABSENT keys default to [] (a normal unverified finding). A SUPPLIED empty
    # list is also valid — a list of zero non-empty strings.
    absent = connect._validate_role_evidence({"revalidated": True})
    assert absent["required_roles"] == []
    assert absent["validated_roles"] == []
    assert absent["agent_identity"] is None

    supplied_empty = connect._validate_role_evidence(
        {"revalidated": True, "required_roles": [], "validated_roles": []}
    )
    assert supplied_empty["required_roles"] == []
    assert supplied_empty["validated_roles"] == []


def test_granted_roles_alias_is_honored_for_verification(tmp_path):
    obo = {"present": True, "user_scoped": True}
    role = {
        "revalidated": True,
        "required_roles": ["Case.Read"],
        "granted_roles": ["Case.Read", "Case.Write"],  # alias for validated_roles
        "agent_identity": "agent-123",
    }
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY,
    )
    assert result["target_state"] == "real-verified"


def test_granted_roles_alias_missing_required_yields_unverified(tmp_path):
    obo = {"present": True, "user_scoped": True}
    role = {
        "revalidated": True,
        "required_roles": ["Case.Read", "Case.Escalate"],
        "granted_roles": ["Case.Read"],  # not a superset of required
        "agent_identity": "agent-123",
    }
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY,
    )
    assert result["target_state"] == "real-unverified"


@pytest.mark.parametrize("bad_identity", ["", 5, [], {}, True])
def test_agent_identity_must_be_nonempty_string_or_null(bad_identity):
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence(
            {"revalidated": True, "agent_identity": bad_identity}
        )


def test_agent_identity_null_or_absent_is_allowed():
    assert connect._validate_role_evidence(
        {"revalidated": True, "agent_identity": None}
    )["agent_identity"] is None
    assert connect._validate_role_evidence({"revalidated": True})["agent_identity"] is None


@pytest.mark.parametrize("key", ["present", "user_scoped"])
@pytest.mark.parametrize("bad", ["true", 1, 0, None, [], {}])
def test_obo_flags_must_be_actual_bool(key, bad):
    evidence = {"present": True, "user_scoped": True}
    evidence[key] = bad
    with pytest.raises(ConnectEvidenceError):
        connect._validate_obo_evidence(evidence)


def test_obo_absent_flags_default_to_false():
    assert connect._validate_obo_evidence({}) == {"present": False, "user_scoped": False}
    assert connect._validate_obo_evidence({"present": True}) == {
        "present": True, "user_scoped": False,
    }


def test_revalidated_flag_must_be_actual_bool():
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence({"revalidated": "yes"})
    with pytest.raises(ConnectEvidenceError):
        connect._validate_role_evidence({"revalidated": None})  # supplied null, not a bool
    # absent -> False (a normal unverified finding, not an error)
    assert connect._validate_role_evidence({})["revalidated"] is False


def test_malformed_role_list_raises_before_any_write(tmp_path):
    # `required_roles: ''` used to be coerced to [] and pass; now it is malformed
    # and must abort before ANY artifact is produced.
    obo, _ = full_evidence()
    with pytest.raises(ConnectEvidenceError):
        _run(
            tmp_path, obo_evidence=obo,
            role_evidence={"revalidated": True, "required_roles": ""},
            current_agent_identity=CURRENT_IDENTITY, apply=True,
        )
    assert not (tmp_path / connect.DEFAULT_MANIFEST_PATH).exists()
    assert not (tmp_path / connect.DEFAULT_SPEC_PATH).exists()
    assert not (tmp_path / "tests" / "threadlight_connect").exists()


# ---------------------------------------------------------------------------
# Task 3.2 — conformance: every item must be an object; a non-object row is a
# structured difference at $.items[i] and never a vacuous pass.
# ---------------------------------------------------------------------------
def test_non_object_item_optional_only_contract_does_not_pass():
    # An optional-only contract has no required field to "miss" — a scalar row
    # would previously coerce to {} and pass vacuously. It must now fail.
    contract = {"fields": [{"name": "status", "required": False, "type": "string", "cardinality": "single"}]}
    result = check_conformance(contract, {"items": [42]})
    assert result["passed"] is False
    assert result["evaluated"] is True
    assert result["item_count"] == 1
    assert result["differences"] == [
        {"field": "$", "expected": "object", "actual": "integer", "path": "$.items[0]"}
    ]


@pytest.mark.parametrize(
    ("bad_item", "actual"),
    [(None, "unknown"), ([1, 2], "array"), ("scalar", "string"), (5, "integer"), (True, "boolean"), (1.5, "number")],
)
def test_non_object_items_report_their_type(bad_item, actual):
    contract = {"fields": [{"name": "status", "required": False, "type": "string", "cardinality": "single"}]}
    result = check_conformance(contract, {"items": [bad_item]})
    assert result["passed"] is False
    assert result["differences"] == [
        {"field": "$", "expected": "object", "actual": actual, "path": "$.items[0]"}
    ]


def test_non_object_item_in_bare_list_is_flagged():
    contract = {"fields": [{"name": "id", "required": True, "type": "string", "cardinality": "single"}]}
    result = check_conformance(contract, [42, {"id": "R-1"}])  # bare list, first row scalar
    assert result["passed"] is False
    assert {d["path"] for d in result["differences"]} == {"$.items[0]"}
    assert result["differences"][0]["field"] == "$"


def test_bare_list_of_objects_still_conforms():
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    result = check_conformance(contract, [{"id": "R-1001", "status": "open"}])
    assert result == {"passed": True, "evaluated": True, "item_count": 1, "differences": []}


def test_run_with_non_object_row_targets_real_drift_and_manifest_is_valid(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path,
        real_response={"items": [{"id": "R-1", "status": "open"}, 42]},
        obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    assert result["target_state"] == "real-drift"
    assert result["integration_state"] == "mock"
    assert result["changed_paths"] == []
    connect.validate_connect_manifest(result["manifest"])  # $-field difference is schema-valid
    findings = {f["id"]: f["status"] for f in result["manifest"]["findings"]}
    # The non-object row is a conformance difference -> INT-001 + INT-002 must-fix;
    # the field-level detail stays in conformance.differences, not a finding id.
    assert findings["INT-001"] == "must-fix"
    assert findings["INT-002"] == "must-fix"
    diff_paths = {d["path"] for d in result["manifest"]["conformance"]["differences"]}
    assert "$.items[1]" in diff_paths


# ---------------------------------------------------------------------------
# The stable INT-001..004 live-leg gap-evidence contract. The connect leg emits
# EXACTLY these four findings, one each, in order — the same IDs the consumer
# (threadlight-production-ready) projects 1:1. Detailed conformance differences
# stay in conformance.differences, never fanned out into dynamic finding IDs.
# ---------------------------------------------------------------------------
def _finding_status_map(result):
    return {f["id"]: f["status"] for f in result["manifest"]["findings"]}


def test_findings_are_exactly_int_001_004_in_order(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    ids = [f["id"] for f in result["manifest"]["findings"]]
    assert ids == ["INT-001", "INT-002", "INT-003", "INT-004"]
    # no dynamic / unknown IDs ever
    assert all(fid.startswith("INT-00") for fid in ids)
    connect.validate_connect_manifest(result["manifest"])


def test_findings_present_even_when_no_evidence_supplied(tmp_path):
    # No OBO/role evidence and an empty real response: still exactly the four
    # findings (all not-verified), never a variable-length array.
    result = _run(tmp_path, real_response={"items": []}, apply=False)
    ids = [f["id"] for f in result["manifest"]["findings"]]
    assert ids == ["INT-001", "INT-002", "INT-003", "INT-004"]
    statuses = _finding_status_map(result)
    assert statuses["INT-001"] == "not-verified"
    assert statuses["INT-002"] == "not-verified"
    assert statuses["INT-003"] == "not-verified"
    assert statuses["INT-004"] == "not-verified"
    assert result["manifest"]["status"] == "partial"


def test_findings_full_success_all_pass_and_manifest_complete(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    statuses = _finding_status_map(result)
    assert statuses == {
        "INT-001": "pass", "INT-002": "pass",
        "INT-003": "pass", "INT-004": "pass",
    }
    # complete when fully evaluated, even though the manifest also records a
    # successful verified apply
    assert result["manifest"]["status"] == "complete"


def test_findings_int_002_pass_requires_applied_binding_not_just_evidence(tmp_path):
    # INT-002 pass keys on the PERSISTED integration_state, not the evidence
    # alone. A dry run (apply=False) with full evidence computes target_state
    # real-verified, yet integration_state stays mock — so INT-002 must remain
    # not-verified with the static "evidence supports the swap but --apply has
    # not persisted the binding" detail, while INT-001/003/004 legitimately pass.
    obo, role = full_evidence()
    dry = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=False,
    )
    assert dry["target_state"] == "real-verified"
    assert dry["integration_state"] == "mock"
    dry_statuses = _finding_status_map(dry)
    assert dry_statuses["INT-001"] == "pass"
    assert dry_statuses["INT-002"] == "not-verified"
    assert dry_statuses["INT-003"] == "pass"
    assert dry_statuses["INT-004"] == "pass"
    int_002 = next(f for f in dry["manifest"]["findings"] if f["id"] == "INT-002")
    assert int_002["detail"] == (
        "evidence supports the swap to real-verified, but --apply has not "
        "persisted the binding"
    )
    # The SAME evidence, now applied, persists the binding and flips INT-002 to
    # pass — the only path to pass is a successful apply transaction.
    applied = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    assert applied["integration_state"] == "real-verified"
    assert _finding_status_map(applied)["INT-002"] == "pass"


def test_findings_int_002_pass_holds_on_reconfirming_dry_run_after_apply(tmp_path):
    # Once a successful apply persists real-verified, a LATER dry run (apply=
    # False) that re-confirms the same full evidence keeps INT-002 pass — the
    # binding is genuinely persisted at real-verified, so re-confirmation must
    # not regress it to not-verified.
    obo, role = full_evidence()
    first = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED,
    )
    assert first["integration_state"] == "real-verified"
    second = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=False,
        generated_at="2026-08-18T10:00:00+00:00",
    )
    assert second["integration_state"] == "real-verified"
    assert second["target_state"] == "real-verified"
    assert _finding_status_map(second)["INT-002"] == "pass"


def test_findings_drift_conformance_and_binding_must_fix(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path, real_response=drifting_real_response(),
        obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    statuses = _finding_status_map(result)
    assert statuses["INT-001"] == "must-fix"  # conformance diverged
    assert statuses["INT-002"] == "must-fix"  # real-drift binding
    assert statuses["INT-003"] == "pass"      # OBO still user-scoped
    assert statuses["INT-004"] == "pass"      # roles still revalidated
    # must-fix on drift keeps the manifest complete (it WAS evaluated)
    assert result["manifest"]["status"] == "complete"
    # detail is carried by conformance.differences, not a dynamic finding id
    assert result["manifest"]["conformance"]["differences"]


def test_findings_obo_missing_is_int_003_not_verified(tmp_path):
    _, role = full_evidence()
    result = _run(
        tmp_path,
        obo_evidence={"present": False, "user_scoped": False},
        role_evidence=role, current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    statuses = _finding_status_map(result)
    assert statuses["INT-001"] == "pass"          # conformance still passes
    assert statuses["INT-002"] == "not-verified"  # target held at real-unverified
    assert statuses["INT-003"] == "not-verified"  # OBO absent
    assert statuses["INT-004"] == "pass"          # roles revalidated vs identity


def test_findings_explicit_missing_role_is_int_004_must_fix(tmp_path):
    obo, _ = full_evidence()
    role = {
        "revalidated": True,
        "required_roles": ["Case.Read", "Case.Admin"],
        "validated_roles": ["Case.Read"],  # Case.Admin missing -> explicit failure
        "agent_identity": CURRENT_IDENTITY,
    }
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    statuses = _finding_status_map(result)
    assert statuses["INT-003"] == "pass"
    assert statuses["INT-004"] == "must-fix"


def test_findings_stale_role_identity_is_int_004_not_verified(tmp_path):
    obo, role = full_evidence()
    # Roles revalidated + granted, but no --current-agent-identity supplied: the
    # grant is stale, not an explicit failure -> not-verified (never pass).
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=None, apply=True,
    )
    statuses = _finding_status_map(result)
    assert statuses["INT-004"] == "not-verified"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: [{"id": "CONNECT-DRIFT-status", "status": "must-fix"}] + f[1:],
        lambda f: f[:3],                                      # only three
        lambda f: f + [{"id": "INT-005", "status": "pass"}],  # a fifth
        lambda f: [f[1], f[0]] + f[2:],                       # wrong order
        lambda f: [f[0], f[0], f[2], f[3]],                   # duplicate INT-001
        lambda f: [],                                          # empty
    ],
    ids=["dynamic-id", "only-three", "extra-fifth", "wrong-order", "duplicate", "empty"],
)
def test_validate_rejects_findings_that_are_not_the_int_tuple(mutate):
    manifest = _valid_manifest()
    manifest["findings"] = mutate(copy.deepcopy(manifest["findings"]))
    with pytest.raises(ManifestValidationError, match="findings must be exactly"):
        connect.validate_connect_manifest(manifest)


def test_valid_int_tuple_manifest_round_trips(tmp_path):
    # A real emitted manifest is written and re-validated intact.
    obo, role = full_evidence()
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    on_disk = json.loads(
        (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_text(encoding="utf-8")
    )
    assert [f["id"] for f in on_disk["findings"]] == [
        "INT-001", "INT-002", "INT-003", "INT-004",
    ]
    connect.validate_connect_manifest(on_disk)


# ---------------------------------------------------------------------------
# Task 3.3 — persisted state: absent manifest => mock; a JSON object missing
# integration_state or carrying an invalid one => ConnectEvidenceError, no write.
# ---------------------------------------------------------------------------
def test_load_current_state_absent_manifest_defaults_to_mock(tmp_path):
    assert connect.load_current_state(tmp_path / "nope" / "connect-manifest.json") == "mock"


def test_load_current_state_valid_object_returns_state(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"integration_state": "real-verified"}), encoding="utf-8")
    assert connect.load_current_state(path) == "real-verified"


def test_prior_manifest_missing_integration_state_aborts_and_preserves_bytes(tmp_path):
    manifest_full = tmp_path / connect.DEFAULT_MANIFEST_PATH
    manifest_full.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema": "threadlight-connect-manifest/v1", "target_state": "mock"}).encode()
    manifest_full.write_bytes(payload)

    with pytest.raises(ConnectEvidenceError, match="integration_state"):
        _run(tmp_path)

    assert manifest_full.read_bytes() == payload  # preserved, not reset to mock
    assert not (tmp_path / "tests" / "threadlight_connect").exists()  # nothing written


@pytest.mark.parametrize("bogus", ["totally-bogus", "mocked", 42, None, "real-verifie"])
def test_prior_manifest_invalid_integration_state_aborts_and_preserves_bytes(tmp_path, bogus):
    manifest_full = tmp_path / connect.DEFAULT_MANIFEST_PATH
    manifest_full.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"integration_state": bogus}).encode()
    manifest_full.write_bytes(payload)

    with pytest.raises(ConnectEvidenceError, match="integration_state"):
        _run(tmp_path)

    assert manifest_full.read_bytes() == payload


# ---------------------------------------------------------------------------
# Task 3.4 — apply is transactional across SPEC + mcp-config + connect-manifest.
# The manifest is committed inside the transaction (not atomic_write_json after
# the config commit); every failure leaves all three at prior bytes/existence.
# ---------------------------------------------------------------------------
def test_apply_commits_manifest_in_transaction_records_only_production_paths(tmp_path):
    obo, role = full_evidence()
    result = _run(
        tmp_path, obo_evidence=obo, role_evidence=role,
        current_agent_identity=CURRENT_IDENTITY, apply=True,
    )
    # changed_paths are EXACTLY the two production files — never the manifest or
    # the regenerated conformance-test scaffold.
    assert result["changed_paths"] == [connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH]
    assert connect.DEFAULT_MANIFEST_PATH not in result["changed_paths"]

    manifest_full = tmp_path / connect.DEFAULT_MANIFEST_PATH
    on_disk = json.loads(manifest_full.read_text(encoding="utf-8"))
    assert on_disk["integration_state"] == "real-verified"
    assert on_disk["changed_paths"] == [connect.DEFAULT_SPEC_PATH, connect.DEFAULT_MCP_CONFIG_PATH]
    connect.validate_connect_manifest(on_disk)


def test_manifest_destination_replace_failure_rolls_back_all_three(tmp_path, monkeypatch):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
    prior_spec = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()
    prior_mcp = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_manifest = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()

    real_replace = connect.os.replace
    manifest_name = Path(connect.DEFAULT_MANIFEST_PATH).name

    def _boom(source, destination):
        # SPEC + mcp commit, then fail the THIRD (manifest) replace, forcing both
        # committed config files to roll back.
        if Path(destination).name == manifest_name:
            raise OSError("simulated manifest replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(ConnectApplyError) as excinfo:
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True,
             generated_at="2026-08-20T10:00:00+00:00",
             real_response={"items": [{"id": "R-9", "status": "closed"}]})
    # Recoverable (rollback succeeded): plain ConnectApplyError, NOT inconsistent.
    assert not isinstance(excinfo.value, connect.ConnectInconsistentStateError)

    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest
    assert list((tmp_path / "specs").glob(".*.tmp")) == []
    assert list((tmp_path / "infra").glob(".*.tmp")) == []


def test_manifest_rollback_failure_raises_inconsistent_state_naming_path(tmp_path, monkeypatch):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)

    real_replace = connect.os.replace
    manifest_name = Path(connect.DEFAULT_MANIFEST_PATH).name
    mcp_name = Path(connect.DEFAULT_MCP_CONFIG_PATH).name

    def _boom(source, destination):
        name = Path(destination).name
        # Fail the manifest commit, then ALSO fail the mcp-config rollback —
        # leaving mcp-config unreconciled.
        if name == manifest_name:
            raise OSError("simulated manifest replace failure")
        if name == mcp_name:
            _boom.mcp_calls += 1
            if _boom.mcp_calls >= 2:  # forward commit ok; rollback replace fails
                raise OSError("simulated mcp rollback failure")
        return real_replace(source, destination)

    _boom.mcp_calls = 0
    monkeypatch.setattr(connect.os, "replace", _boom)

    with pytest.raises(connect.ConnectInconsistentStateError) as excinfo:
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True,
             generated_at="2026-08-20T10:00:00+00:00",
             real_response={"items": [{"id": "R-9", "status": "closed"}]})
    # Names the unreconciled destination and never claims success.
    assert str(tmp_path / connect.DEFAULT_MCP_CONFIG_PATH) in str(excinfo.value)


def test_manifest_staging_failure_leaves_all_three_unchanged(tmp_path, monkeypatch):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
    prior_spec = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()
    prior_mcp = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_manifest = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()

    real_write_temp = connect._write_temp_file
    manifest_name = Path(connect.DEFAULT_MANIFEST_PATH).name

    def _boom_stage(path, content, mode=None):
        # Fail while STAGING the manifest temp — before any destination is
        # replaced. All three must be left untouched.
        if Path(path).name == manifest_name:
            raise OSError("simulated staging failure")
        return real_write_temp(path, content, mode)

    monkeypatch.setattr(connect, "_write_temp_file", _boom_stage)

    with pytest.raises(ConnectApplyError):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True,
             generated_at="2026-08-20T10:00:00+00:00",
             real_response={"items": [{"id": "R-9", "status": "closed"}]})

    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest
    assert list((tmp_path / "specs").glob(".*.tmp")) == []
    assert list((tmp_path / "infra").glob(".*.tmp")) == []


def test_manifest_validation_failure_aborts_before_mutating_the_three(tmp_path, monkeypatch):
    obo, role = full_evidence()
    _run(tmp_path, obo_evidence=obo, role_evidence=role,
         current_agent_identity=CURRENT_IDENTITY, apply=True, generated_at=PINNED)
    prior_spec = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()
    prior_mcp = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_manifest = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()

    def _boom_validate(manifest):
        raise ManifestValidationError("injected final-manifest validation failure")

    monkeypatch.setattr(connect, "validate_connect_manifest", _boom_validate)

    with pytest.raises(ManifestValidationError):
        _run(tmp_path, obo_evidence=obo, role_evidence=role,
             current_agent_identity=CURRENT_IDENTITY, apply=True,
             generated_at="2026-08-20T10:00:00+00:00",
             real_response={"items": [{"id": "R-9", "status": "closed"}]})

    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest


def test_cli_apply_failure_reports_three_unchanged_not_blanket_claim(tmp_path, capsys, monkeypatch):
    obo, role = full_evidence()
    _write_cli_inputs(tmp_path)
    (tmp_path / "obo.json").write_text(json.dumps(obo), encoding="utf-8")
    (tmp_path / "role.json").write_text(json.dumps(role), encoding="utf-8")
    apply_args = _base_cli_args(tmp_path) + [
        "--obo-evidence-file", str(tmp_path / "obo.json"),
        "--role-evidence-file", str(tmp_path / "role.json"),
        "--current-agent-identity", CURRENT_IDENTITY,
        "--real-endpoint", REAL_ENDPOINT,
        "--apply",
    ]
    assert connect.main(apply_args) == 0  # establish a prior applied state

    prior_spec = (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes()
    prior_mcp = (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes()
    prior_manifest = (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes()

    real_replace = connect.os.replace
    fail_names = {
        Path(connect.DEFAULT_SPEC_PATH).name,
        Path(connect.DEFAULT_MCP_CONFIG_PATH).name,
        Path(connect.DEFAULT_MANIFEST_PATH).name,
    }

    def _boom(source, destination):
        if Path(destination).name in fail_names:
            raise OSError("simulated disk failure")
        return real_replace(source, destination)

    monkeypatch.setattr(connect.os, "replace", _boom)

    exit_code = connect.main(apply_args)

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "unchanged" in err
    assert "no files were changed" not in err  # must NOT over-claim
    assert (tmp_path / connect.DEFAULT_SPEC_PATH).read_bytes() == prior_spec
    assert (tmp_path / connect.DEFAULT_MCP_CONFIG_PATH).read_bytes() == prior_mcp
    assert (tmp_path / connect.DEFAULT_MANIFEST_PATH).read_bytes() == prior_manifest


# ---------------------------------------------------------------------------
# Task 3.5 — the GENERATED conformance module normalizes dict.items and a bare
# list, and flags non-object rows, exactly like the runtime check.
# ---------------------------------------------------------------------------
def _exec_generated(tool_name, contract, sample_path):
    source = generate_conformance_test_source(tool_name, contract, default_sample_path=str(sample_path))
    namespace: dict = {}
    exec(compile(source, "<gen-parity>", "exec"), namespace)  # noqa: S102 - our own generated code
    return namespace


def test_generated_module_handles_bare_list_conforming_and_drifting(tmp_path):
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    sample_path = tmp_path / "real.json"
    namespace = _exec_generated("returns_get_case", contract, sample_path)
    conformance_test = namespace["test_returns_get_case_conformance"]

    sample_path.write_text(json.dumps([{"id": "R-1", "status": "open"}]), encoding="utf-8")
    conformance_test()  # bare list of conforming objects -> no raise

    sample_path.write_text(json.dumps([{"id": "R-1", "status": 42}]), encoding="utf-8")
    with pytest.raises(AssertionError, match=r"status.*integer"):
        conformance_test()  # bare list, drifting type


def test_generated_module_flags_non_object_rows(tmp_path):
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    sample_path = tmp_path / "real.json"
    namespace = _exec_generated("returns_get_case", contract, sample_path)
    conformance_test = namespace["test_returns_get_case_conformance"]

    sample_path.write_text(json.dumps({"items": [42]}), encoding="utf-8")
    with pytest.raises(AssertionError, match=r"\$\.items\[0\]"):
        conformance_test()  # wrapped scalar row

    sample_path.write_text(
        json.dumps([{"id": "R-1", "status": "open"}, "scalar"]), encoding="utf-8"
    )
    with pytest.raises(AssertionError, match=r"\$\.items\[1\]"):
        conformance_test()  # bare list, second row is a scalar


@pytest.mark.parametrize(
    "response",
    [
        [{"id": "R-1", "status": "open"}],          # bare list, conforming
        {"items": [{"id": "R-1", "status": 42}]},   # wrapped, drifting
        {"items": [42]},                            # wrapped, non-object row
        [None],                                     # bare list, null row
        [{"id": "R-1"}, "scalar"],                  # mixed object + scalar rows
        5,                                          # not a dict or list -> no items
    ],
)
def test_generated_check_conformance_matches_runtime(tmp_path, response):
    contract = extract_contract("returns_get_case", TOOL_SOURCE, SAMPLE, generated_at=PINNED)
    namespace = _exec_generated("returns_get_case", contract, tmp_path / "real.json")
    generated_check = namespace["check_conformance"]
    runtime = check_conformance(contract, response)["differences"]
    assert generated_check(response) == runtime
