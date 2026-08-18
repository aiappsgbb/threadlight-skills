"""Tests for the threadlight-upgrade skill (`upgrade.py`) — the UPGRADE leg:
turning a normalized project description (dependency pins, hosted-agent
runtime policy, governance profile, model families) plus a versioned,
dated compatibility matrix into a `threadlight.upgrade/v1` manifest
(UPG-001..003) and one ordered, de-duplicated migration `plan`.

`upgrade.py` is PLAN-ONLY: it never edits a project, never implements
`--apply` (there is no such flag), and never makes a network call —
official-source corroboration (`UPG-003`) is entirely fixture-driven via an
injectable `source_results` mapping supplied by the caller/test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
REFERENCES = SKILL_ROOT / "references"
sys.path.insert(0, str(SCRIPTS))

import upgrade  # noqa: E402
from upgrade import (  # noqa: E402
    DEFAULT_ARTIFACT_PATHS,
    SOURCE_UNAVAILABLE_MESSAGE,
    UpgradeMatrixError,
    UpgradeProjectError,
    ManifestValidationError,
    compare_versions,
    parse_pyproject_dependencies,
    parse_package_json_dependencies,
    parse_runtime_policy_file,
    parse_version,
    scan_project,
    validate_matrix,
    validate_upgrade_manifest,
    write_upgrade_manifest,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def entry(
    *,
    surface,
    target,
    state="stable",
    source="https://learn.microsoft.com/example",
    last_reviewed="2026-06-01",
    review_window_days=120,
    **extra,
):
    record = {
        "surface": surface,
        "target": target,
        "state": state,
        "source": source,
        "last_reviewed": last_reviewed,
        "review_window_days": review_window_days,
    }
    record.update(extra)
    return record


def matrix(entries, *, version="1.0", date="2026-06-01",
           source="https://learn.microsoft.com/matrix"):
    return {
        "schema": "threadlight-upgrade-compatibility-matrix/v1",
        "version": version,
        "date": date,
        "source": source,
        "entries": entries,
    }


def by_id(manifest):
    return {f["id"]: f for f in manifest["findings"]}


AGENT_FRAMEWORK_ENTRY = entry(
    surface="agent-framework", target="agent-framework", state="stable",
    stable="2.0.0", last_reviewed="2026-06-01", review_window_days=120,
)


# ---------------------------------------------------------------------------
# Version parsing / comparison — stdlib-only, numeric, never lexical/guessed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected_release,expected_prerelease",
    [
        ("2.0.0", (2, 0, 0), False),
        ("v2.0.0", (2, 0, 0), False),
        ("2.0.0b1", (2, 0, 0), True),
        ("2.0.0-beta.1", (2, 0, 0), True),
        ("1.2.3rc1", (1, 2, 3), True),
        ("1.0.0.dev0", (1, 0, 0), True),
        ("1.0", (1, 0), False),
        ("1", (1,), False),
        ("2.0.0a", (2, 0, 0), True),
    ],
)
def test_parse_version_accepts_semver_and_python_prereleases(
    text, expected_release, expected_prerelease
):
    parsed = parse_version(text)
    assert parsed is not None
    assert parsed["release"] == expected_release
    assert parsed["is_prerelease"] is expected_prerelease


@pytest.mark.parametrize(
    "text",
    [">=1.0,<2.0", "latest", "", "   ", "*", "git+https://example/repo.git", None, 123],
)
def test_parse_version_returns_none_for_ambiguous_input(text):
    assert parse_version(text) is None


def test_compare_versions_is_numeric_never_lexical():
    # Lexically "10.0.0" < "2.0.0", but numerically 10.0.0 > 2.0.0.
    assert compare_versions("10.0.0", "2.0.0") == 1
    assert compare_versions("2.0.0", "10.0.0") == -1
    assert compare_versions("2.0.0", "2.0.0") == 0


def test_compare_versions_orders_prerelease_stages_below_final():
    assert compare_versions("2.0.0.dev0", "2.0.0a1") == -1
    assert compare_versions("2.0.0a1", "2.0.0b1") == -1
    assert compare_versions("2.0.0b1", "2.0.0rc1") == -1
    assert compare_versions("2.0.0rc1", "2.0.0") == -1


def test_compare_versions_returns_none_when_either_side_unparseable():
    assert compare_versions("2.0.0", "latest") is None
    assert compare_versions(">=1.0", "2.0.0") is None
    assert compare_versions(None, "2.0.0") is None


# ---------------------------------------------------------------------------
# validate_matrix — malformed shapes raise before any scan
# ---------------------------------------------------------------------------
def test_validate_matrix_accepts_shipped_reference_matrix():
    shipped = json.loads((REFERENCES / "compatibility-matrix.json").read_text(encoding="utf-8"))
    validate_matrix(shipped)  # must not raise
    surfaces = {e["surface"] for e in shipped["entries"]}
    assert surfaces == {
        "hosted-agent-protocol", "agent-framework", "toolbox",
        "skill-publication", "governance-profile", "model-family",
    }


def test_validate_matrix_rejects_non_object():
    with pytest.raises(UpgradeMatrixError):
        validate_matrix("not-a-matrix")


def test_validate_matrix_rejects_missing_top_level_key():
    bad = matrix([])
    del bad["source"]
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(bad)


def test_validate_matrix_rejects_unknown_top_level_key():
    bad = matrix([])
    bad["extra"] = "nope"
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(bad)


def test_validate_matrix_rejects_wrong_schema_const():
    bad = matrix([])
    bad["schema"] = "something-else/v1"
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(bad)


def test_validate_matrix_rejects_unknown_surface():
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(matrix([entry(surface="not-a-surface", target="x")]))


def test_validate_matrix_rejects_unknown_state():
    bad_entry = entry(surface="agent-framework", target="agent-framework")
    bad_entry["state"] = "beta"
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(matrix([bad_entry]))


def test_validate_matrix_rejects_non_positive_review_window_days():
    for bad_value in (0, -1, 1.5, True):
        bad_entry = entry(surface="toolbox", target="t", review_window_days=bad_value)
        with pytest.raises(UpgradeMatrixError):
            validate_matrix(matrix([bad_entry]))


def test_validate_matrix_rejects_bad_date_shapes():
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(matrix([entry(surface="toolbox", target="t", last_reviewed="06-01-2026")]))


def test_validate_matrix_rejects_duplicate_target_globally():
    with pytest.raises(UpgradeMatrixError, match="duplicate"):
        validate_matrix(matrix([
            entry(surface="toolbox", target="shared-name"),
            entry(surface="skill-publication", target="shared-name"),
        ]))


def test_validate_matrix_rejects_empty_expiry_triggers():
    bad_entry = entry(surface="model-family", target="gpt-4", expiry_triggers=[])
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(matrix([bad_entry]))


def test_validate_matrix_rejects_credential_bearing_source():
    bad_entry = entry(
        surface="toolbox", target="t",
        source="https://user:hunter2@learn.microsoft.com/example",
    )
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(matrix([bad_entry]))


def test_validate_matrix_rejects_credential_bearing_matrix_source():
    bad = matrix([entry(surface="toolbox", target="t")])
    bad["source"] = "https://learn.microsoft.com/x?api_key=AKIAABCDEFGHIJKLMNOP"
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(bad)


# ---------------------------------------------------------------------------
# Required scenario: matrix staleness -> UPG-001 should-fix, no latest_version
# ---------------------------------------------------------------------------
def test_matrix_staleness_produces_should_fix_and_never_a_latest_version():
    stale_entry = entry(
        surface="agent-framework", target="agent-framework", state="stable",
        last_reviewed="2026-01-01", review_window_days=90,
    )
    manifest = scan_project({}, matrix([stale_entry]), "2026-08-17")
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "should-fix"
    assert upg001["detail"]["reason"] == "matrix-stale"
    assert upg001["detail"]["stale_entries"][0] == {
        "surface": "agent-framework",
        "target": "agent-framework",
        "last_reviewed": "2026-01-01",
        "review_window_days": 90,
        "age_days": 228,
    }
    assert "latest_version" not in json.dumps(manifest)


def test_matrix_freshness_pass_when_within_review_window():
    fresh_entry = entry(
        surface="agent-framework", target="agent-framework",
        last_reviewed="2026-08-01", review_window_days=90,
    )
    manifest = scan_project({}, matrix([fresh_entry]), "2026-08-17")
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "pass"
    assert upg001["detail"]["reason"] == "fresh"


# ---------------------------------------------------------------------------
# Required scenario: prerelease dependency -> exact ordered plan item
# ---------------------------------------------------------------------------
def test_prerelease_dependency_produces_exact_plan_item():
    project = {"dependencies": {"agent-framework": "2.0.0b1"}}
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    assert manifest["plan"] == [{
        "order": 1,
        "path": "pyproject.toml",
        "reason": "agent-framework is pinned to prerelease 2.0.0b1",
        "from": "2.0.0b1",
        "to": "2.0.0",
    }]
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "should-fix"
    assert upg001["detail"]["dependencies_prerelease_pinned"] == [
        {"name": "agent-framework", "from": "2.0.0b1", "to": "2.0.0"}
    ]


def test_dependency_behind_stable_matrix_produces_deterministic_plan_item():
    project = {"dependencies": {"agent-framework": "1.9.0"}}
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "should-fix"
    assert upg001["detail"]["dependencies_behind_stable"] == [
        {"name": "agent-framework", "from": "1.9.0", "to": "2.0.0"}
    ]
    assert manifest["plan"] == [{
        "order": 1,
        "path": "pyproject.toml",
        "reason": "agent-framework is behind the matrix stable release 2.0.0",
        "from": "1.9.0",
        "to": "2.0.0",
    }]


def test_dependency_at_or_above_stable_is_a_pass():
    project = {"dependencies": {"agent-framework": "2.0.0"}}
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "pass"
    assert manifest["plan"] == []


def test_dependency_with_unparseable_version_is_not_verified_never_guessed():
    project = {"dependencies": {"agent-framework": ">=1.0,<3.0"}}
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "not-verified"
    assert upg001["detail"]["reason"] == "dependency-version-not-verified"
    assert upg001["detail"]["dependencies_not_verified"] == ["agent-framework"]
    assert manifest["status"] == "partial"


def test_dependency_outside_matrix_is_silently_skipped():
    project = {"dependencies": {"totally-unrelated-package": "0.0.1"}}
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "pass"
    assert manifest["plan"] == []


# ---------------------------------------------------------------------------
# Required scenario: runtime_policy + triggered_expiry_conditions -> UPG-002
# ---------------------------------------------------------------------------
INVOCATIONS_ENTRY = entry(
    surface="hosted-agent-protocol", target="invocations", state="preview",
    stable="responses", replacement="responses",
    expiry_triggers=["responses-end-to-end"],
)
RESPONSES_ENTRY = entry(
    surface="hosted-agent-protocol", target="responses", state="stable",
)


def test_runtime_policy_preview_with_fired_trigger_is_should_fix():
    project = {
        "runtime_policy": {"default-agent": "invocations"},
        "triggered_expiry_conditions": ["responses-end-to-end"],
    }
    manifest = scan_project(
        project, matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]), "2026-06-15"
    )
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "should-fix"
    assert upg002["detail"]["preview_usages"] == [{
        "label": "default-agent",
        "target": "invocations",
        "state": "preview",
        "fired_triggers": ["responses-end-to-end"],
    }]


def test_runtime_policy_preview_without_fired_trigger_is_still_should_fix():
    project = {"runtime_policy": {"default-agent": "invocations"}}
    manifest = scan_project(
        project, matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]), "2026-06-15"
    )
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "should-fix"
    assert "fired_triggers" not in upg002["detail"]["preview_usages"][0]


def test_runtime_policy_targeting_stable_surface_is_pass():
    project = {"runtime_policy": {"default-agent": "responses"}}
    manifest = scan_project(
        project, matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]), "2026-06-15"
    )
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "pass"
    assert upg002["detail"]["reason"] == "no-drift"


def test_deprecated_governance_profile_is_should_fix():
    legacy = entry(
        surface="governance-profile", target="legacy-manual-review",
        state="deprecated", replacement="adaptive-guardrails", expiry="2026-12-01",
    )
    project = {"governance_profile": "legacy-manual-review"}
    manifest = scan_project(project, matrix([legacy]), "2026-06-15")
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "should-fix"
    assert upg002["detail"]["deprecated_usages"] == [{
        "label": "governance_profile",
        "target": "legacy-manual-review",
        "state": "deprecated",
    }]


def test_expired_decision_is_should_fix_with_expiry_recorded():
    expired = entry(
        surface="model-family", target="gpt-4", state="deprecated",
        replacement="gpt-5", expiry="2026-09-01",
    )
    project = {"model_families": ["gpt-4"]}
    manifest = scan_project(project, matrix([expired]), "2026-09-02")
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "should-fix"
    assert upg002["detail"]["expired_decisions"] == [{
        "label": "gpt-4", "target": "gpt-4", "state": "deprecated", "expiry": "2026-09-01",
    }]
    plan_reasons = [item["reason"] for item in manifest["plan"]]
    assert any("expired on 2026-09-01" in reason for reason in plan_reasons)


def test_expired_preview_decision_uses_preview_wording_not_deprecated():
    # A preview surface past its expiry must be reported using its actual
    # state ("preview decision expired"), never the deprecated wording.
    expired_preview = entry(
        surface="model-family", target="gpt-4o-preview", state="preview",
        replacement="gpt-5", expiry="2026-09-01",
    )
    project = {"model_families": ["gpt-4o-preview"]}
    manifest = scan_project(project, matrix([expired_preview]), "2026-09-02")
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "should-fix"
    assert upg002["detail"]["expired_decisions"] == [{
        "label": "gpt-4o-preview", "target": "gpt-4o-preview",
        "state": "preview", "expiry": "2026-09-01",
    }]
    plan_reasons = [item["reason"] for item in manifest["plan"]]
    assert any(
        "whose preview decision expired on 2026-09-01" in reason
        for reason in plan_reasons
    )
    assert not any("was deprecated and expired" in reason for reason in plan_reasons)


def test_expired_preview_and_deprecated_wordings_are_deterministically_distinct():
    expired_preview = entry(
        surface="model-family", target="preview-fam", state="preview",
        replacement="ga-fam", expiry="2026-09-01",
    )
    expired_deprecated = entry(
        surface="model-family", target="deprecated-fam", state="deprecated",
        replacement="ga-fam", expiry="2026-09-01",
    )
    project = {"model_families": ["preview-fam", "deprecated-fam"]}
    manifest = scan_project(
        project, matrix([expired_preview, expired_deprecated]), "2026-09-02"
    )
    reasons = {item["reason"] for item in manifest["plan"]}
    assert any("preview-fam, whose preview decision expired on 2026-09-01" in r for r in reasons)
    assert any("deprecated-fam, which was deprecated and expired on 2026-09-01" in r for r in reasons)


def test_usage_target_not_in_matrix_is_not_verified():
    project = {"runtime_policy": {"default-agent": "some-unknown-mode"}}
    manifest = scan_project(project, matrix([RESPONSES_ENTRY]), "2026-06-15")
    upg002 = by_id(manifest)["UPG-002"]
    assert upg002["status"] == "not-verified"
    assert upg002["detail"]["reason"] == "usage-target-not-in-matrix"
    assert upg002["detail"]["not_in_matrix"] == ["default-agent:some-unknown-mode"]
    assert manifest["status"] == "partial"


# ---------------------------------------------------------------------------
# Malformed usage/model-family/trigger inputs -> controlled UpgradeProjectError
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model_families", [["gpt-4", 1], ["gpt-4", ""], ["gpt-4", None], ["gpt-4", ["nested"]]])
def test_non_string_or_empty_model_family_raises_controlled_error(model_families):
    project = {"model_families": model_families}
    with pytest.raises(UpgradeProjectError, match=r"model_families\[1\] must be a non-empty string"):
        scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")


def test_model_families_not_a_list_raises_controlled_error():
    project = {"model_families": {"gpt-4": True}}
    with pytest.raises(UpgradeProjectError, match="model_families must be an array"):
        scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")


@pytest.mark.parametrize("policy_value", [123, "", None, ["responses"]])
def test_non_string_or_empty_runtime_policy_value_raises_controlled_error(policy_value):
    project = {"runtime_policy": {"default-agent": policy_value}}
    with pytest.raises(UpgradeProjectError, match="runtime_policy"):
        scan_project(project, matrix([RESPONSES_ENTRY]), "2026-06-15")


@pytest.mark.parametrize("triggers", [["ok", 2], ["ok", ""], ["ok", None]])
def test_non_string_or_empty_trigger_raises_controlled_error(triggers):
    project = {
        "runtime_policy": {"default-agent": "invocations"},
        "triggered_expiry_conditions": triggers,
    }
    with pytest.raises(
        UpgradeProjectError,
        match=r"triggered_expiry_conditions\[1\] must be a non-empty string",
    ):
        scan_project(
            project, matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]), "2026-06-15"
        )


def test_triggered_expiry_conditions_not_a_list_raises_controlled_error():
    project = {
        "runtime_policy": {"default-agent": "invocations"},
        "triggered_expiry_conditions": "responses-end-to-end",
    }
    with pytest.raises(UpgradeProjectError, match="triggered_expiry_conditions must be an array"):
        scan_project(
            project, matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]), "2026-06-15"
        )


# ---------------------------------------------------------------------------
# Required scenario: UPG-003 unavailable source -> exact not-verified detail
# ---------------------------------------------------------------------------
def test_unavailable_source_is_exact_not_verified_with_no_latest_version():
    manifest = scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    upg003 = by_id(manifest)["UPG-003"]
    assert upg003["status"] == "not-verified"
    assert upg003["detail"]["message"] == SOURCE_UNAVAILABLE_MESSAGE
    assert upg003["detail"]["message"] == (
        "Official source unavailable; no latest version was inferred."
    )
    assert "latest_version" not in json.dumps(manifest)


def test_unavailable_source_is_default_when_source_results_omitted():
    manifest_a = scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    manifest_b = scan_project(
        {}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15", source_results=None
    )
    assert by_id(manifest_a)["UPG-003"] == by_id(manifest_b)["UPG-003"]


def test_preview_to_ga_source_transition_is_should_fix_with_plan_item():
    project = {"runtime_policy": {"default-agent": "invocations"}}
    source_results = {
        "hosted-agent-protocol:invocations": {"state": "deprecated"},
        "hosted-agent-protocol:responses": {"state": "stable"},
    }
    manifest = scan_project(
        project,
        matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]),
        "2026-06-15",
        source_results=source_results,
    )
    upg003 = by_id(manifest)["UPG-003"]
    assert upg003["status"] == "should-fix"
    assert upg003["detail"]["reason"] == "preview-to-ga-transition"
    assert {
        "surface": "hosted-agent-protocol", "target": "invocations",
        "from_state": "preview", "to_state": "deprecated",
    } in upg003["detail"]["transitions"]
    reasons = [item["reason"] for item in manifest["plan"]]
    assert any("moved from preview to deprecated" in reason for reason in reasons)


def test_source_results_with_missing_check_is_not_verified_but_preserves_transitions():
    project = {"runtime_policy": {"default-agent": "invocations"}}
    source_results = {
        "hosted-agent-protocol:invocations": {"state": "deprecated"},
        # "hosted-agent-protocol:responses" intentionally omitted -> a gap
    }
    manifest = scan_project(
        project,
        matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]),
        "2026-06-15",
        source_results=source_results,
    )
    upg003 = by_id(manifest)["UPG-003"]
    assert upg003["status"] == "not-verified"
    assert upg003["detail"]["unverified_checks"] == ["hosted-agent-protocol:responses"]
    assert upg003["detail"]["transitions"] == [{
        "surface": "hosted-agent-protocol", "target": "invocations",
        "from_state": "preview", "to_state": "deprecated",
    }]
    assert manifest["status"] == "partial"


def test_source_result_with_latest_version_is_recorded_only_when_confirmed():
    project = {"dependencies": {"agent-framework": "1.9.0"}}
    source_results = {
        "agent-framework:agent-framework": {
            "state": "stable", "latest_version": "2.1.0", "checked_at": "2026-06-10",
        },
    }
    manifest = scan_project(
        project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15",
        source_results=source_results,
    )
    upg003 = by_id(manifest)["UPG-003"]
    # source's state (stable) matches the matrix's recorded state -> no
    # transition, no fabricated latest_version anywhere in this finding.
    assert upg003["status"] == "pass"
    assert "latest_version" not in json.dumps(upg003)


# ---------------------------------------------------------------------------
# Status semantics: partial iff not-verified anywhere; should-fix stays complete
# ---------------------------------------------------------------------------
def test_should_fix_only_scan_is_complete_not_partial():
    project = {"dependencies": {"agent-framework": "1.9.0"}}
    source_results = {"agent-framework:agent-framework": {"state": "stable"}}
    manifest = scan_project(
        project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15",
        source_results=source_results,
    )
    assert all(f["status"] != "not-verified" for f in manifest["findings"])
    assert any(f["status"] == "should-fix" for f in manifest["findings"])
    assert manifest["status"] == "complete"


def test_any_not_verified_finding_makes_manifest_partial():
    manifest = scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    assert any(f["status"] == "not-verified" for f in manifest["findings"])
    assert manifest["status"] == "partial"


def test_all_pass_scan_with_source_results_is_complete():
    source_results = {"agent-framework:agent-framework": {"state": "stable"}}
    manifest = scan_project(
        {}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15", source_results=source_results
    )
    assert all(f["status"] == "pass" for f in manifest["findings"])
    assert manifest["status"] == "complete"


# ---------------------------------------------------------------------------
# freshness.source_oldest_at — matrix/source dates, or None honestly
# ---------------------------------------------------------------------------
def test_source_oldest_at_is_earliest_matrix_last_reviewed_date():
    older = entry(surface="toolbox", target="toolbox-a", last_reviewed="2026-01-01")
    newer = entry(surface="skill-publication", target="skill-a", last_reviewed="2026-05-01")
    manifest = scan_project({}, matrix([older, newer]), "2026-06-15")
    assert manifest["freshness"]["source_oldest_at"] == "2026-01-01T00:00:00+00:00"


def test_source_oldest_at_is_none_when_matrix_has_no_entries():
    manifest = scan_project({}, matrix([]), "2026-06-15")
    assert manifest["freshness"]["source_oldest_at"] is None


def test_source_oldest_at_considers_source_results_checked_at():
    only_entry = entry(surface="toolbox", target="toolbox-a", last_reviewed="2026-05-01")
    source_results = {"toolbox:toolbox-a": {"state": "stable", "checked_at": "2026-01-15"}}
    manifest = scan_project(
        {}, matrix([only_entry]), "2026-06-15", source_results=source_results
    )
    assert manifest["freshness"]["source_oldest_at"] == "2026-01-15T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Plan finalization: ordered, deterministic, no duplicate (path, reason)
# ---------------------------------------------------------------------------
def test_plan_is_ordered_deterministic_and_has_no_duplicate_path_reason():
    project = {
        "dependencies": {"agent-framework": "1.9.0", "toolbox-pkg": "0.5.0"},
        "runtime_policy": {"agent-a": "invocations", "agent-b": "invocations"},
        "triggered_expiry_conditions": ["responses-end-to-end"],
    }
    toolbox_entry = entry(
        surface="toolbox", target="toolbox-pkg", stable="1.0.0",
    )
    m = matrix([AGENT_FRAMEWORK_ENTRY, toolbox_entry, INVOCATIONS_ENTRY, RESPONSES_ENTRY])
    manifest = scan_project(project, m, "2026-06-15")
    plan = manifest["plan"]

    keys = [(item["path"], item["reason"]) for item in plan]
    assert len(keys) == len(set(keys)), "duplicate (path, reason) pair found"

    orders = [item["order"] for item in plan]
    assert orders == list(range(1, len(plan) + 1))

    sorted_keys = sorted(keys)
    assert keys == sorted_keys


def test_scan_project_is_deterministic_across_repeated_calls():
    project = {
        "dependencies": {"agent-framework": "1.9.0"},
        "runtime_policy": {"default-agent": "invocations"},
        "triggered_expiry_conditions": ["responses-end-to-end"],
    }
    m = matrix([AGENT_FRAMEWORK_ENTRY, INVOCATIONS_ENTRY, RESPONSES_ENTRY])
    first = scan_project(project, m, "2026-06-15")
    second = scan_project(project, m, "2026-06-15")
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


# ---------------------------------------------------------------------------
# project shape validation
# ---------------------------------------------------------------------------
def test_scan_project_rejects_non_dict_project():
    with pytest.raises(UpgradeProjectError):
        scan_project("not-a-project", matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")


def test_scan_project_rejects_non_dict_dependencies():
    with pytest.raises(UpgradeProjectError):
        scan_project(
            {"dependencies": ["not", "a", "dict"]},
            matrix([AGENT_FRAMEWORK_ENTRY]),
            "2026-06-15",
        )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        *[
            (field, bad_value)
            for field in (
                "dependencies",
                "runtime_policy",
                "artifact_paths",
                "dependency_paths",
            )
            for bad_value in ("", 0, False, [])
        ],
        *[
            (field, bad_value)
            for field in ("model_families", "triggered_expiry_conditions")
            for bad_value in ("", 0, False, {})
        ],
    ],
)
def test_normalized_project_falsey_wrong_types_are_rejected(field, bad_value):
    with pytest.raises(UpgradeProjectError, match=field):
        scan_project(
            {field: bad_value},
            matrix([AGENT_FRAMEWORK_ENTRY]),
            "2026-06-15",
        )


@pytest.mark.parametrize(
    "field,empty_value",
    [
        ("dependencies", {}),
        ("runtime_policy", {}),
        ("model_families", []),
        ("triggered_expiry_conditions", []),
        ("artifact_paths", {}),
        ("dependency_paths", {}),
    ],
)
def test_normalized_project_absent_none_and_semantic_empty_use_defaults(
    field, empty_value
):
    baseline = scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    explicit_none = scan_project(
        {field: None}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15"
    )
    explicit_empty = scan_project(
        {field: empty_value}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15"
    )
    for manifest in (baseline, explicit_none, explicit_empty):
        manifest.pop("generated_at")
    assert baseline == explicit_none == explicit_empty


@pytest.mark.parametrize(
    "field,bad_mapping",
    [
        ("dependencies", {"": "1.0.0"}),
        ("dependencies", {"pkg": ""}),
        ("dependencies", {1: "1.0.0"}),
        ("dependencies", {"pkg": False}),
        ("runtime_policy", {"": "responses"}),
        ("runtime_policy", {"agent": ""}),
        ("runtime_policy", {1: "responses"}),
        ("runtime_policy", {"agent": False}),
        ("artifact_paths", {"": "config.json"}),
        ("artifact_paths", {"agent-framework": ""}),
        ("dependency_paths", {"": "package.json"}),
        ("dependency_paths", {"pkg": ""}),
    ],
)
def test_normalized_project_mapping_keys_and_values_are_nonempty_strings(
    field, bad_mapping
):
    with pytest.raises(UpgradeProjectError, match=field):
        scan_project(
            {field: bad_mapping},
            matrix([AGENT_FRAMEWORK_ENTRY]),
            "2026-06-15",
        )


def test_scan_project_rejects_bad_today_shape():
    with pytest.raises(UpgradeProjectError):
        scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), "17-08-2026")


def test_scan_project_accepts_date_and_datetime_today():
    import datetime as dt
    manifest_str = scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    manifest_date = scan_project({}, matrix([AGENT_FRAMEWORK_ENTRY]), dt.date(2026, 6, 15))
    manifest_datetime = scan_project(
        {}, matrix([AGENT_FRAMEWORK_ENTRY]), dt.datetime(2026, 6, 15, 12, 30)
    )
    for m in (manifest_str, manifest_date, manifest_datetime):
        m.pop("generated_at")
    assert manifest_str == manifest_date == manifest_datetime


def test_scan_project_rejects_bad_artifact_path_override():
    project = {"artifact_paths": {"agent-framework": "../escape.toml"}}
    with pytest.raises(UpgradeProjectError):
        scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")


def test_artifact_path_override_is_honored_in_plan():
    project = {
        "dependencies": {"agent-framework": "1.9.0"},
        "artifact_paths": {"agent-framework": "requirements/base.txt"},
    }
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    assert manifest["plan"][0]["path"] == "requirements/base.txt"


def test_dependency_paths_provenance_maps_plan_item_to_actual_artifact():
    # A dependency parsed from package.json must map its plan item to that
    # package.json, never the pyproject.toml surface default.
    project = {
        "dependencies": {"agent-framework": "1.9.0"},
        "dependency_paths": {"agent-framework": "package.json"},
    }
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    assert manifest["plan"][0]["path"] == "package.json"


def test_explicit_artifact_path_override_beats_dependency_provenance():
    # Explicit operator override wins over parsed-from provenance.
    project = {
        "dependencies": {"agent-framework": "1.9.0"},
        "dependency_paths": {"agent-framework": "package.json"},
        "artifact_paths": {"agent-framework": "requirements/base.txt"},
    }
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    assert manifest["plan"][0]["path"] == "requirements/base.txt"


def test_dependency_provenance_beats_surface_default_but_not_override_order():
    # With no override, provenance beats the pyproject.toml default.
    project = {
        "dependencies": {"agent-framework": "2.0.0b1"},
        "dependency_paths": {"agent-framework": "package.json"},
    }
    manifest = scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")
    assert manifest["plan"][0]["path"] == "package.json"
    assert manifest["plan"][0]["reason"].startswith("agent-framework is pinned to prerelease")


def test_scan_project_rejects_bad_dependency_path():
    project = {
        "dependencies": {"agent-framework": "1.9.0"},
        "dependency_paths": {"agent-framework": "../escape.json"},
    }
    with pytest.raises(UpgradeProjectError):
        scan_project(project, matrix([AGENT_FRAMEWORK_ENTRY]), "2026-06-15")


# ---------------------------------------------------------------------------
# validate_upgrade_manifest — schema strictness, uniqueness, no writes
# ---------------------------------------------------------------------------
def _base_manifest():
    return scan_project(
        {"dependencies": {"agent-framework": "1.9.0"}},
        matrix([AGENT_FRAMEWORK_ENTRY]),
        "2026-06-15",
    )


def test_validate_upgrade_manifest_accepts_scan_project_output():
    validate_upgrade_manifest(_base_manifest())  # must not raise


def test_validate_upgrade_manifest_rejects_wrong_schema_const():
    manifest = _base_manifest()
    manifest["schema"] = "wrong/v1"
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_requires_all_three_finding_ids():
    manifest = _base_manifest()
    manifest["findings"] = [f for f in manifest["findings"] if f["id"] != "UPG-002"]
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_duplicate_finding_id():
    manifest = _base_manifest()
    manifest["findings"].append(dict(manifest["findings"][0]))
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_unknown_finding_status():
    manifest = _base_manifest()
    manifest["findings"][0]["status"] = "not-a-status"
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_unknown_detail_reason():
    manifest = _base_manifest()
    manifest["findings"][0]["detail"]["reason"] = "made-up-reason"
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_free_form_detail_key():
    manifest = _base_manifest()
    manifest["findings"][0]["detail"]["free_form_note"] = "nope"
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_duplicate_plan_path_reason():
    manifest = _base_manifest()
    manifest["plan"] = [
        {"order": 1, "path": "pyproject.toml", "reason": "same", "from": "1", "to": "2"},
        {"order": 2, "path": "pyproject.toml", "reason": "same", "from": "1", "to": "2"},
    ]
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_non_consecutive_order():
    manifest = _base_manifest()
    manifest["plan"] = [
        {"order": 1, "path": "a.toml", "reason": "r1", "from": None, "to": None},
        {"order": 3, "path": "b.toml", "reason": "r2", "from": None, "to": None},
    ]
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_unsafe_plan_path():
    manifest = _base_manifest()
    manifest["plan"] = [
        {"order": 1, "path": "../escape.toml", "reason": "r1", "from": None, "to": None},
    ]
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_unexpected_top_level_key():
    manifest = _base_manifest()
    manifest["unexpected"] = "nope"
    with pytest.raises(ManifestValidationError):
        validate_upgrade_manifest(manifest)


# ---------------------------------------------------------------------------
# Privacy/secret rejection — never credentials/tokens/customer payloads
# ---------------------------------------------------------------------------
def test_validate_upgrade_manifest_rejects_forbidden_key_before_shape_check():
    manifest = _base_manifest()
    manifest["findings"][0]["detail"]["access_token"] = "sneaky"
    with pytest.raises(UpgradeProjectError):
        validate_upgrade_manifest(manifest)


def test_validate_upgrade_manifest_rejects_secret_shaped_value():
    manifest = _base_manifest()
    manifest["findings"][0]["detail"]["message"] = (
        "see AKIAABCDEFGHIJKLMNOPQR for details"
    )
    with pytest.raises(UpgradeProjectError):
        validate_upgrade_manifest(manifest)


def test_validate_matrix_rejects_bearer_token_in_source_reference():
    bad_entry = entry(
        surface="toolbox", target="t",
        source="Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz",
    )
    with pytest.raises(UpgradeMatrixError):
        validate_matrix(matrix([bad_entry]))


# ---------------------------------------------------------------------------
# Atomic, schema-validated writer — never automatic writes, preserves prior
# ---------------------------------------------------------------------------
def test_write_upgrade_manifest_round_trips(tmp_path):
    manifest = _base_manifest()
    path = tmp_path / "specs" / "upgrade-manifest.json"
    write_upgrade_manifest(path, manifest)
    assert json.loads(path.read_text(encoding="utf-8")) == manifest


def test_write_upgrade_manifest_rejects_invalid_manifest_without_writing(tmp_path):
    manifest = _base_manifest()
    manifest["status"] = "not-a-real-status"
    path = tmp_path / "specs" / "upgrade-manifest.json"
    with pytest.raises(ManifestValidationError):
        write_upgrade_manifest(path, manifest)
    assert not path.exists()


def test_write_upgrade_manifest_preserves_prior_valid_file_on_validation_failure(tmp_path):
    path = tmp_path / "specs" / "upgrade-manifest.json"
    original = _base_manifest()
    write_upgrade_manifest(path, original)
    original_bytes = path.read_bytes()

    broken = _base_manifest()
    broken["findings"][0]["status"] = "not-a-status"
    with pytest.raises(ManifestValidationError):
        write_upgrade_manifest(path, broken)

    assert path.read_bytes() == original_bytes
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_write_upgrade_manifest_cleans_temp_file_on_interrupted_replace(tmp_path, monkeypatch):
    path = tmp_path / "specs" / "upgrade-manifest.json"
    original = _base_manifest()
    write_upgrade_manifest(path, original)
    original_bytes = path.read_bytes()

    def interrupt_replace(source, destination):
        raise KeyboardInterrupt

    monkeypatch.setattr(upgrade.os, "replace", interrupt_replace)

    updated = scan_project(
        {"dependencies": {"agent-framework": "2.0.0"}},
        matrix([AGENT_FRAMEWORK_ENTRY]),
        "2026-06-15",
    )
    with pytest.raises(KeyboardInterrupt):
        write_upgrade_manifest(path, updated)

    assert path.read_bytes() == original_bytes
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_write_upgrade_manifest_is_deterministic_for_identical_inputs(tmp_path):
    path_a = tmp_path / "a" / "upgrade-manifest.json"
    path_b = tmp_path / "b" / "upgrade-manifest.json"
    manifest = _base_manifest()
    write_upgrade_manifest(path_a, manifest)
    write_upgrade_manifest(path_b, manifest)
    assert path_a.read_bytes() == path_b.read_bytes()


# ---------------------------------------------------------------------------
# CLI: no --apply flag, root-escape/symlink rejection, read-only fixtures
# ---------------------------------------------------------------------------
def test_cli_rejects_apply_flag():
    with pytest.raises(SystemExit) as exc_info:
        upgrade.main(["--apply"])
    assert exc_info.value.code == 2


def test_main_source_never_declares_an_apply_argument():
    # Behavioral coverage lives in test_cli_rejects_apply_flag above; this
    # additionally asserts main() never wires up an actual `--apply`
    # add_argument() call under a different spelling that argparse would
    # still accept (e.g. an abbreviation-friendly alias). A mention of
    # "--apply" inside a --help description string is fine — only an actual
    # add_argument("--apply", ...) call would matter.
    import inspect
    import re

    source = inspect.getsource(upgrade.main)
    add_argument_calls = re.findall(r'add_argument\(\s*"(--[a-zA-Z0-9-]+)"', source)
    assert "--apply" not in add_argument_calls


def _write_matrix(root: Path, m: dict) -> Path:
    path = root / "compatibility-matrix.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    return path


def test_cli_emits_manifest_within_root(tmp_path):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--today", "2026-06-15",
        "--emit",
    ])
    assert code == 0
    manifest_path = tmp_path / "specs" / "upgrade-manifest.json"
    assert manifest_path.exists()
    validate_upgrade_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))


def test_cli_does_not_write_without_emit(tmp_path):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 0
    assert not (tmp_path / "specs" / "upgrade-manifest.json").exists()


def test_cli_rejects_manifest_path_escaping_root(tmp_path):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--manifest-path", "../escape.json",
        "--today", "2026-06-15",
        "--emit",
    ])
    assert code == 1
    assert not (tmp_path.parent / "escape.json").exists()


def test_cli_rejects_absolute_manifest_path_outside_root(tmp_path):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    outside = tmp_path.parent / "outside.json"
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--manifest-path", str(outside),
        "--today", "2026-06-15",
        "--emit",
    ])
    assert code == 1
    assert not outside.exists()


def test_cli_rejects_symlinked_manifest_parent_escape_without_outside_write(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    (project / "specs").symlink_to(outside, target_is_directory=True)
    matrix_path = _write_matrix(project, matrix([AGENT_FRAMEWORK_ENTRY]))

    code = upgrade.main([
        "--project-root", str(project),
        "--matrix-path", str(matrix_path),
        "--today", "2026-06-15",
        "--emit",
    ])

    assert code == 1
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not (outside / "upgrade-manifest.json").exists()


def test_resolve_within_root_rejects_traversal_through_missing_parent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(UpgradeProjectError, match="escapes the project root"):
        upgrade._resolve_within_root(str(project), "missing/../../../escape.json")


def test_resolve_within_root_rejects_symlink_escape(tmp_path):
    project = tmp_path / "project"
    inside = project / "inside"
    inside.mkdir(parents=True)
    (project / "link").symlink_to(inside, target_is_directory=True)
    with pytest.raises(UpgradeProjectError, match="escapes the project root"):
        upgrade._resolve_within_root(str(project), "link/missing/../../../escape.json")


def test_cli_rejects_project_file_escaping_root(tmp_path):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    outside_project = tmp_path.parent / "outside-project.json"
    outside_project.write_text("{}", encoding="utf-8")
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--project-file", "../outside-project.json",
        "--today", "2026-06-15",
    ])
    assert code == 1


# ---------------------------------------------------------------------------
# Read-only convenience fixture parsers
# ---------------------------------------------------------------------------
def test_parse_pyproject_dependencies_extracts_exact_pins():
    text = (
        "[project]\n"
        'name = "demo"\n'
        'dependencies = ["agent-framework==2.0.0b1", "requests>=2.0", "toolbox-pkg==1.0.0"]\n'
    )
    deps = parse_pyproject_dependencies(text)
    # Exact `==` pins (including an exact prerelease) reduce to a bare,
    # comparable version; a `>=` range is represented verbatim so it surfaces
    # as not-verified downstream rather than being silently omitted or guessed.
    assert deps == {
        "agent-framework": "2.0.0b1",
        "requests": ">=2.0",
        "toolbox-pkg": "1.0.0",
    }


@pytest.mark.parametrize(
    "requirement, name, expected",
    [
        # Exact pins reduce to a bare comparable version.
        ("pkg==1.2.3", "pkg", "1.2.3"),
        ("pkg===1.2.3", "pkg", "1.2.3"),
        ("pkg==2.0.0b1", "pkg", "2.0.0b1"),
        ("pkg==1.2.3-beta.1", "pkg", "1.2.3-beta.1"),
        # Every non-exact constraint is represented verbatim (not-verified).
        ("pkg>=1.2", "pkg", ">=1.2"),
        ("pkg>=1.2,<2", "pkg", ">=1.2,<2"),
        ("pkg<=1.2", "pkg", "<=1.2"),
        ("pkg>1.2", "pkg", ">1.2"),
        ("pkg<1.2", "pkg", "<1.2"),
        ("pkg~=1.2", "pkg", "~=1.2"),
        ("pkg==1.2.*", "pkg", "==1.2.*"),
    ],
)
def test_parse_pyproject_dependencies_only_exact_is_reduced(requirement, name, expected):
    text = "[project]\ndependencies = [" + json.dumps(requirement) + "]\n"
    assert parse_pyproject_dependencies(text) == {name: expected}


def test_parse_pyproject_dependencies_skips_bare_name_and_reduces_after_marker():
    text = (
        "[project]\n"
        'dependencies = ["barepkg", "markerpkg==1.0.0 ; python_version < \\"3.12\\""]\n'
    )
    # A bare name has no constraint at all (skipped); an env marker is stripped
    # before the exact pin is reduced.
    assert parse_pyproject_dependencies(text) == {"markerpkg": "1.0.0"}


@pytest.mark.parametrize(
    "text,expected_error",
    [
        ("project = []\n", "project must be a table"),
        ('tool = "not-a-table"\n', "tool must be a table"),
        ("[project]\ndependencies = {}\n", "project.dependencies must be an array"),
        (
            "[project]\ndependencies = [1]\n",
            r"project.dependencies\[0\] must be a non-empty string",
        ),
        (
            '[project]\ndependencies = [""]\n',
            r"project.dependencies\[0\] must be a non-empty string",
        ),
        (
            "[project]\noptional-dependencies = []\n",
            "project.optional-dependencies must be a table",
        ),
        (
            "[project.optional-dependencies]\ntest = [1]\n",
            r"project.optional-dependencies\['test'\]\[0\] must be a non-empty string",
        ),
        (
            'tool = { poetry = "not-a-table" }\n',
            "tool.poetry must be a table",
        ),
        (
            "[tool.poetry]\ndependencies = []\n",
            "tool.poetry.dependencies must be a table",
        ),
        (
            "[tool.poetry.dependencies]\npkg = 1\n",
            r"tool.poetry.dependencies\['pkg'\] must be a non-empty string",
        ),
    ],
)
def test_parse_pyproject_dependencies_rejects_syntactically_valid_bad_shapes(
    text, expected_error
):
    with pytest.raises(UpgradeProjectError, match=expected_error):
        parse_pyproject_dependencies(text)


def test_parse_pyproject_dependencies_supports_poetry_dependency_table():
    text = (
        "[tool.poetry]\n"
        'name = "demo"\n'
        "[tool.poetry.dependencies]\n"
        'python = "^3.11"\n'
        'agent-framework = "==2.0.0"\n'
        'toolbox = "1.2.3"\n'
    )
    assert parse_pyproject_dependencies(text) == {
        "python": "^3.11",
        "agent-framework": "2.0.0",
        "toolbox": "1.2.3",
    }


def test_parse_pyproject_dependencies_supports_optional_and_poetry_group_tables():
    text = (
        "[project.optional-dependencies]\n"
        'test = ["pytest==9.0.0"]\n'
        "[tool.poetry.group.dev.dependencies]\n"
        'ruff = "^0.12"\n'
    )
    assert parse_pyproject_dependencies(text) == {
        "pytest": "9.0.0",
        "ruff": "^0.12",
    }


@pytest.mark.parametrize(
    "spec, expected",
    [
        # Exact literals (optionally a single leading `=`) reduce.
        ("1.2.3", "1.2.3"),
        ("=1.2.3", "1.2.3"),
        ("v1.2.3", "v1.2.3"),
        ("2.0.0b1", "2.0.0b1"),
        ("1.2.3-beta.1", "1.2.3-beta.1"),
        # Ranges / compound / OR / hyphen / caret / tilde / wildcard.
        ("^2.0.0", "^2.0.0"),
        ("~1.0.0", "~1.0.0"),
        (">=1.0.0", ">=1.0.0"),
        (">=1.2", ">=1.2"),
        (">=1.2 <2", ">=1.2 <2"),
        ("<=1.2", "<=1.2"),
        (">1.2", ">1.2"),
        ("<1.2", "<1.2"),
        ("1.2.3 - 2.3.4", "1.2.3 - 2.3.4"),
        ("^1 || ^2", "^1 || ^2"),
        ("*", "*"),
        ("1.x", "1.x"),
        ("latest", "latest"),
        # Protocol / git / url / file specs.
        ("workspace:*", "workspace:*"),
        ("workspace:^1.0.0", "workspace:^1.0.0"),
        ("file:../local-pkg", "file:../local-pkg"),
        ("link:../local-pkg", "link:../local-pkg"),
        ("git+https://example.com/x.git", "git+https://example.com/x.git"),
        ("https://example.com/x.tgz", "https://example.com/x.tgz"),
        ("npm:other@1.2.3", "npm:other@1.2.3"),
    ],
)
def test_parse_package_json_dependencies_only_exact_is_reduced(spec, expected):
    text = json.dumps({"dependencies": {"pkg": spec}})
    # Only a confidently exact literal reduces to a bare version; every range,
    # compound, OR, hyphen, wildcard, dist-tag, and protocol/url/git spec is
    # represented verbatim so it surfaces as not-verified downstream.
    assert parse_package_json_dependencies(text) == {"pkg": expected}


def test_parse_package_json_dependencies_ranges_are_represented_not_stripped():
    text = json.dumps({
        "dependencies": {"agent-framework": "^2.0.0", "left-pad": "~1.0.0"},
        "devDependencies": {"toolbox-pkg": ">=1.0.0"},
    })
    deps = parse_package_json_dependencies(text)
    # Range operators are NOT stripped to a fake exact pin; they are kept
    # verbatim so `parse_version` later yields not-verified rather than a guess.
    assert deps == {
        "agent-framework": "^2.0.0",
        "left-pad": "~1.0.0",
        "toolbox-pkg": ">=1.0.0",
    }


@pytest.mark.parametrize("spec", [">=", "<=", "^", "~", ">", "<", ">= <", "||", "-", "*"])
def test_parse_package_json_dependencies_all_operator_specs_never_raise(spec):
    text = json.dumps({"dependencies": {"pkg": spec}})
    # An all-operator / malformed spec must be represented verbatim (surfaces
    # as not-verified) rather than raising IndexError on an empty split.
    assert parse_package_json_dependencies(text) == {"pkg": spec}


def test_parse_package_json_dependencies_empty_spec_is_skipped():
    text = json.dumps({"dependencies": {"pkg": "", "real": "1.2.3"}})
    # An empty spec carries no constraint at all and is skipped, not emitted
    # as a bogus empty pin.
    assert parse_package_json_dependencies(text) == {"real": "1.2.3"}


@pytest.mark.parametrize("bad_top_level", [None, [], "", 0, False])
def test_parse_package_json_top_level_must_be_an_object(bad_top_level):
    with pytest.raises(UpgradeProjectError, match="JSON object"):
        parse_package_json_dependencies(json.dumps(bad_top_level))


@pytest.mark.parametrize(
    "section,bad_value",
    [
        (section, bad_value)
        for section in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
        )
        for bad_value in (None, [], "", 0, False)
    ],
)
def test_parse_package_json_dependency_sections_must_be_objects(section, bad_value):
    with pytest.raises(UpgradeProjectError, match=section):
        parse_package_json_dependencies(json.dumps({section: bad_value}))


@pytest.mark.parametrize(
    "section",
    [
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ],
)
def test_parse_package_json_supports_all_dependency_sections(section):
    assert parse_package_json_dependencies(
        json.dumps({section: {"pkg": "1.2.3"}})
    ) == {"pkg": "1.2.3"}


@pytest.mark.parametrize(
    "dependencies",
    [
        {"": "1.2.3"},
        {"pkg": None},
        {"pkg": 1},
        {"pkg": False},
        {"pkg": []},
        {"pkg": {}},
    ],
)
def test_parse_package_json_dependency_names_and_specs_have_exact_types(dependencies):
    with pytest.raises(UpgradeProjectError, match="dependencies"):
        parse_package_json_dependencies(json.dumps({"dependencies": dependencies}))


def test_parse_package_json_rejects_conflicting_specs_across_sections():
    text = json.dumps({
        "dependencies": {"pkg": "1.2.3"},
        "peerDependencies": {"pkg": "^1.2.3"},
    })
    with pytest.raises(UpgradeProjectError, match="conflicting specs.*pkg"):
        parse_package_json_dependencies(text)


def test_parse_package_json_accepts_duplicate_matching_specs_across_sections():
    text = json.dumps({
        "dependencies": {"pkg": "1.2.3"},
        "optionalDependencies": {"pkg": "=1.2.3"},
    })
    assert parse_package_json_dependencies(text) == {"pkg": "1.2.3"}


def test_parse_runtime_policy_file_returns_plain_mapping():
    text = json.dumps({"default-agent": "invocations"})
    assert parse_runtime_policy_file(text) == {"default-agent": "invocations"}


def test_parse_runtime_policy_file_rejects_non_object():
    with pytest.raises(UpgradeProjectError):
        parse_runtime_policy_file(json.dumps(["not", "an", "object"]))


def test_cli_merges_pyproject_and_runtime_policy_fixtures(tmp_path):
    matrix_path = _write_matrix(
        tmp_path, matrix([AGENT_FRAMEWORK_ENTRY, INVOCATIONS_ENTRY, RESPONSES_ENTRY])
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["agent-framework==1.9.0"]\n', encoding="utf-8"
    )
    (tmp_path / "runtime-policy.json").write_text(
        json.dumps({"default-agent": "invocations"}), encoding="utf-8"
    )
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--pyproject-path", "pyproject.toml",
        "--runtime-policy-path", "runtime-policy.json",
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 0


TOOLBOX_ENTRY = entry(
    surface="toolbox", target="toolbox", state="stable", stable="3.0.0",
)


def test_cli_pure_js_dependency_plan_item_points_at_package_json(tmp_path, capsys):
    # A dependency parsed only from package.json must have its plan item
    # attributed to package.json, never the pyproject.toml surface default.
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"agent-framework": "1.9.0"}}), encoding="utf-8"
    )
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--package-json-path", "package.json",
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 0
    manifest = json.loads(capsys.readouterr().out)
    behind = [item for item in manifest["plan"] if "behind the matrix" in item["reason"]]
    assert behind and all(item["path"] == "package.json" for item in behind)
    assert not any(item["path"] == "pyproject.toml" for item in manifest["plan"])


def test_cli_mixed_sources_attribute_each_dependency_to_its_own_artifact(tmp_path, capsys):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY, TOOLBOX_ENTRY]))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["agent-framework==1.9.0"]\n', encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"toolbox": "1.0.0"}}), encoding="utf-8"
    )
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--pyproject-path", "pyproject.toml",
        "--package-json-path", "package.json",
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 0
    manifest = json.loads(capsys.readouterr().out)
    paths = {
        item["reason"].split(" ")[0]: item["path"]
        for item in manifest["plan"]
    }
    assert paths["agent-framework"] == "pyproject.toml"
    assert paths["toolbox"] == "package.json"


def test_cli_conflicting_dependency_in_both_artifacts_is_rejected(tmp_path, capsys):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["agent-framework==1.9.0"]\n', encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"agent-framework": "1.8.0"}}), encoding="utf-8"
    )
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--pyproject-path", "pyproject.toml",
        "--package-json-path", "package.json",
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "declared in both" in out and "agent-framework" in out


@pytest.mark.parametrize("spec", [">=", "^", "~", ">= <", "||", "*", "latest", "workspace:*"])
def test_cli_all_operator_or_ambiguous_spec_is_not_verified_no_traceback(tmp_path, capsys, spec):
    # An all-operator / ambiguous package.json spec surfaces as a controlled
    # not-verified UPG-001 (never an IndexError / traceback).
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"agent-framework": spec}}), encoding="utf-8"
    )
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--package-json-path", "package.json",
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    manifest = json.loads(out)
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "not-verified"
    assert upg001["detail"]["dependencies_not_verified"] == ["agent-framework"]


def test_cli_malformed_all_operator_pyproject_spec_is_not_verified(tmp_path, capsys):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["agent-framework>=1.2 <2"]\n', encoding="utf-8"
    )
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        "--pyproject-path", "pyproject.toml",
        "--today", "2026-06-15",
        "--json",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    manifest = json.loads(out)
    upg001 = by_id(manifest)["UPG-001"]
    assert upg001["status"] == "not-verified"
    assert upg001["detail"]["dependencies_not_verified"] == ["agent-framework"]


@pytest.mark.parametrize(
    "artifact_flag,filename,contents",
    [
        ("--pyproject-path", "pyproject.toml", "project = []\n"),
        (
            "--package-json-path",
            "package.json",
            json.dumps({"dependencies": []}),
        ),
    ],
)
def test_cli_malformed_artifact_is_controlled_and_never_writes_manifest(
    tmp_path, capsys, artifact_flag, filename, contents
):
    matrix_path = _write_matrix(tmp_path, matrix([AGENT_FRAMEWORK_ENTRY]))
    (tmp_path / filename).write_text(contents, encoding="utf-8")
    code = upgrade.main([
        "--project-root", str(tmp_path),
        "--matrix-path", str(matrix_path),
        artifact_flag, filename,
        "--today", "2026-06-15",
        "--emit",
        "--json",
    ])
    assert code == 1
    output = capsys.readouterr()
    assert "error:" in output.out
    assert "Traceback" not in output.out + output.err
    assert not (tmp_path / "specs" / "upgrade-manifest.json").exists()


# ---------------------------------------------------------------------------
# Schema parity — hand validator vs. jsonschema Draft-07
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def jsonschema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    manifest_schema = json.loads(
        (REFERENCES / "upgrade-manifest.schema.json").read_text(encoding="utf-8")
    )
    format_checker = jsonschema.FormatChecker()
    if "date-time" not in format_checker.checkers:
        pytest.skip(
            "jsonschema's standard 'date-time' format check requires an RFC-3339 "
            "backend; without it the parity suite cannot prove timestamp "
            "accept/reject against an independent validator"
        )
    return jsonschema.Draft7Validator(manifest_schema, format_checker=format_checker)


def test_valid_manifest_accepted_by_both_validators(jsonschema_validator):
    manifest = scan_project(
        {
            "dependencies": {"agent-framework": "1.9.0"},
            "runtime_policy": {"default-agent": "invocations"},
            "triggered_expiry_conditions": ["responses-end-to-end"],
        },
        matrix([AGENT_FRAMEWORK_ENTRY, INVOCATIONS_ENTRY, RESPONSES_ENTRY]),
        "2026-06-15",
    )
    validate_upgrade_manifest(manifest)
    errors = list(jsonschema_validator.iter_errors(manifest))
    assert errors == [], [e.message for e in errors]


def test_source_transition_manifest_accepted_by_both_validators(jsonschema_validator):
    manifest = scan_project(
        {"runtime_policy": {"default-agent": "invocations"}},
        matrix([INVOCATIONS_ENTRY, RESPONSES_ENTRY]),
        "2026-06-15",
        source_results={
            "hosted-agent-protocol:invocations": {
                "state": "deprecated", "latest_version": "n/a",
            },
            "hosted-agent-protocol:responses": {"state": "stable"},
        },
    )
    validate_upgrade_manifest(manifest)
    assert jsonschema_validator.is_valid(manifest)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(schema="wrong/v1"),
        lambda m: m["findings"][0].update(status="not-a-status"),
        lambda m: m["findings"][0]["detail"].update(reason="made-up-reason"),
        lambda m: m["findings"][0]["detail"].update(free_form_note="nope"),
        lambda m: m["plan"].append(
            {"order": 1, "path": "x", "reason": "y"}  # missing required from/to
        ),
        lambda m: m["plan"].append(
            {"order": 1, "path": "../escape.toml", "reason": "y", "from": None, "to": None}
        ),
        lambda m: m.update(unexpected_top_level_key="nope"),
        lambda m: m["freshness"].update(valid_for_hours=0),
        lambda m: m["findings"].pop(),
    ],
)
def test_malformed_manifest_rejected_by_both_validators(mutate, jsonschema_validator):
    manifest = scan_project(
        {"dependencies": {"agent-framework": "1.9.0"}},
        matrix([AGENT_FRAMEWORK_ENTRY]),
        "2026-06-15",
    )
    mutate(manifest)
    with pytest.raises((ManifestValidationError, UpgradeProjectError)):
        validate_upgrade_manifest(manifest)
    assert not jsonschema_validator.is_valid(manifest), (
        "hand validator rejected this manifest but jsonschema accepted it"
    )
