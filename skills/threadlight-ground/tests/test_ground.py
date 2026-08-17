"""Tests for the threadlight-ground skill (`ground.py`) — the GROUND leg:
turning already-produced ACL/citation/refusal probe evidence into the
`threadlight.ground/v1` manifest (GRD-001..004).

`ground.py` is a coordinator, not a retrieval/evaluation engine: it never
calls Foundry IQ and never runs an evaluator. Every test below supplies
already-produced evidence (the shape a manual live handoff would capture)
and asserts the resulting findings/manifest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ground  # noqa: E402
from ground import (  # noqa: E402
    GroundValidationError,
    aggregate_telemetry,
    assess_acl,
    assess_citations,
    assess_freshness_coverage,
    assess_grounding,
    assess_refusal,
    oldest_timestamp,
    validate_citations,
    validate_ground_manifest,
    write_ground_manifest,
)

REPO_ROOT = SKILL_ROOT.parent.parent
sys.path.insert(0, str(REPO_ROOT))
from skills._shared.manifest import ManifestValidationError  # noqa: E402

PINNED = "2026-08-17T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------
def source(
    id="policy-library",
    type="documents",
    permission_model="acl",
    refresh_cadence="daily",
    citation_required=True,
    refuse_when_unsupported=True,
    **overrides,
):
    payload = {
        "id": id,
        "type": type,
        "permission_model": permission_model,
        "refresh_cadence": refresh_cadence,
        "citation_required": citation_required,
        "refuse_when_unsupported": refuse_when_unsupported,
    }
    payload.update(overrides)
    return payload


def by_id(manifest: dict) -> dict:
    return {finding["id"]: finding for finding in manifest["findings"]}


def ground_manifest(
    sources=None,
    acl_runs=None,
    citation_runs=None,
    refusal_runs=None,
    generated_at=PINNED,
):
    return assess_grounding(
        sources=sources if sources is not None else [source()],
        acl_runs=acl_runs or [],
        citation_runs=citation_runs or [],
        refusal_runs=refusal_runs or [],
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# Requirement 3 / literal plan test — GRD-001 missing principals/ACL runs
# ---------------------------------------------------------------------------
def test_missing_principals_is_not_verified():
    manifest = ground_manifest(sources=[source()], acl_runs=[])
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "not-verified"
    assert manifest["status"] == "partial"


def test_no_acl_protected_sources_is_pass_without_runs():
    manifest = ground_manifest(sources=[source(permission_model="public")], acl_runs=[])
    assert by_id(manifest)["GRD-001"]["status"] == "pass"


def test_single_principal_only_is_not_verified():
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[{"principal": "entitled", "document_ids": ["A"]}],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "not-verified"


def test_only_entitled_principals_is_not_verified():
    # Two distinct principals, but neither is classifiable as "unentitled" —
    # there is no incompatible-permission pair to probe.
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "entitled-a", "document_ids": ["A"]},
            {"principal": "entitled-b", "document_ids": ["B"]},
        ],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "not-verified"


def test_ambiguous_principal_names_stay_not_verified_never_guessed():
    # "team-a" / "team-b" carry no entitlement signal at all — this must NOT
    # be silently classified either way.
    result = assess_acl(
        [source()],
        [
            {"principal": "team-a", "document_ids": ["A"]},
            {"principal": "team-b", "document_ids": ["B"]},
        ],
    )
    assert result["status"] == "not-verified"
    assert "expected_entitled" in result["detail"]


def test_missing_document_ids_is_not_verified():
    result = assess_acl(
        [source()],
        [
            {"principal": "entitled"},
            {"principal": "unentitled", "document_ids": ["A"]},
        ],
    )
    assert result["status"] == "not-verified"


# ---------------------------------------------------------------------------
# Requirement 3 / literal plan test — GRD-001 proven ACL leak
# ---------------------------------------------------------------------------
def test_identical_protected_results_for_incompatible_principals_is_must_fix():
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "entitled", "document_ids": ["doc-1", "doc-2"]},
            {"principal": "unentitled", "document_ids": ["doc-1", "doc-2"]},
        ],
    )
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "must-fix"


def test_distinct_results_for_incompatible_principals_is_pass():
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "entitled", "document_ids": ["doc-1", "doc-2"]},
            {"principal": "unentitled", "document_ids": ["doc-3"]},
        ],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "pass"


def test_partial_overlap_is_not_flagged_as_a_proven_leak():
    # Deliberately conservative: only an EXACT identical set is a proven leak.
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "entitled", "document_ids": ["doc-1", "doc-2"]},
            {"principal": "unentitled", "document_ids": ["doc-1"]},
        ],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "pass"


def test_explicit_expected_entitled_overrides_name_heuristics():
    # Requirement 3: "Support explicit expected entitlements to avoid naive
    # false positives." Names alone would suggest the opposite classification
    # here ("admin" contains an entitled-marker) — the explicit field wins.
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "svc-admin-probe", "document_ids": ["doc-1"], "expected_entitled": False},
            {"principal": "svc-guest-probe", "document_ids": ["doc-2"], "expected_entitled": True},
        ],
    )
    # Distinct document sets -> pass, proving the explicit fields (not the
    # "admin"/"guest" substrings) drove the entitled/unentitled classification.
    assert by_id(manifest)["GRD-001"]["status"] == "pass"


def test_explicit_expected_entitled_still_catches_a_leak():
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "svc-guest-probe", "document_ids": ["doc-1"], "expected_entitled": True},
            {"principal": "svc-admin-probe", "document_ids": ["doc-1"], "expected_entitled": False},
        ],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "must-fix"


def test_acl_runs_grouped_by_source_id_worst_case_wins():
    sources_list = [
        source(id="policy-library"),
        source(id="hr-handbook"),
    ]
    acl_runs = [
        # policy-library: proven leak
        {"source_id": "policy-library", "principal": "entitled", "document_ids": ["p1"]},
        {"source_id": "policy-library", "principal": "unentitled", "document_ids": ["p1"]},
        # hr-handbook: clean
        {"source_id": "hr-handbook", "principal": "entitled", "document_ids": ["h1"]},
        {"source_id": "hr-handbook", "principal": "unentitled", "document_ids": ["h2"]},
    ]
    manifest = ground_manifest(sources=sources_list, acl_runs=acl_runs)
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["worst_source"] == "policy-library"
    assert finding["detail"]["by_source"] == {
        "policy-library": "must-fix",
        "hr-handbook": "pass",
    }


# ---------------------------------------------------------------------------
# Requirement 4 / literal plan test — GRD-002 citation grounding
# ---------------------------------------------------------------------------
def test_citation_must_exist_in_retrieved_set():
    assert validate_citations(["doc-9"], ["doc-1", "doc-2"]) == {
        "status": "must-fix",
        "missing_from_retrieval": ["doc-9"],
    }


def test_valid_citation_set_passes():
    assert validate_citations(["doc-1", "doc-2"], ["doc-1", "doc-2", "doc-3"]) == {
        "status": "pass",
        "missing_from_retrieval": [],
    }


def test_missing_from_retrieval_is_sorted_and_deduplicated():
    result = validate_citations(["doc-9", "doc-2", "doc-9"], ["doc-1"])
    assert result["missing_from_retrieval"] == ["doc-2", "doc-9"]


def test_no_citation_runs_is_not_verified():
    manifest = ground_manifest(citation_runs=[])
    assert by_id(manifest)["GRD-002"]["status"] == "not-verified"


def test_citation_outside_retrieval_is_must_fix_at_manifest_level():
    manifest = ground_manifest(
        citation_runs=[{"citations": ["doc-9"], "retrieved_ids": ["doc-1", "doc-2"]}]
    )
    finding = by_id(manifest)["GRD-002"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["missing_from_retrieval"] == ["doc-9"]
    # executed must-fix is complete evidence (other legs may still be partial,
    # so pin the other findings to something non-partial to isolate this)
    manifest_complete = ground_manifest(
        sources=[source(permission_model="public")],
        acl_runs=[],
        citation_runs=[{"citations": ["doc-9"], "retrieved_ids": ["doc-1"]}],
        refusal_runs=[{"query_id": "q1", "refused": True}],
    )
    assert manifest_complete["status"] == "complete"


def test_valid_citation_run_passes_at_manifest_level():
    manifest = ground_manifest(
        citation_runs=[{"citations": ["doc-1"], "retrieved_ids": ["doc-1", "doc-2"]}]
    )
    assert by_id(manifest)["GRD-002"]["status"] == "pass"


def test_citation_run_missing_fields_is_not_verified():
    assert assess_citations([{"citations": ["doc-1"]}])["status"] == "not-verified"


# ---------------------------------------------------------------------------
# Requirement 5 — GRD-003 refusal behavior
# ---------------------------------------------------------------------------
def test_refusal_pass_when_every_unsupported_query_is_refused():
    manifest = ground_manifest(
        refusal_runs=[
            {"query_id": "q1", "refused": True},
            {"query_id": "q2", "refused": True},
        ]
    )
    assert by_id(manifest)["GRD-003"]["status"] == "pass"


def test_refusal_must_fix_when_unsupported_query_is_answered():
    manifest = ground_manifest(
        refusal_runs=[
            {"query_id": "q1", "refused": True},
            {"query_id": "q2", "refused": False},
        ]
    )
    finding = by_id(manifest)["GRD-003"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["unsupported_queries_answered"] == ["q2"]


def test_refusal_not_verified_when_no_runs_supplied():
    manifest = ground_manifest(refusal_runs=[])
    assert by_id(manifest)["GRD-003"]["status"] == "not-verified"


def test_refusal_run_missing_boolean_is_not_verified():
    assert assess_refusal([{"query_id": "q1"}])["status"] == "not-verified"


def test_refusal_never_persists_raw_query_text():
    manifest = ground_manifest(
        refusal_runs=[
            {"query_id": "q1", "query": "what is the CEO's salary?", "refused": True}
        ]
    )
    dumped = json.dumps(manifest)
    assert "salary" not in dumped


# ---------------------------------------------------------------------------
# Requirement 7 — freshness (`oldest_timestamp`) and aggregate telemetry
# ---------------------------------------------------------------------------
def test_oldest_timestamp_is_none_when_unknown():
    assert oldest_timestamp([]) is None
    assert oldest_timestamp([{"principal": "entitled"}]) is None


def test_oldest_timestamp_picks_earliest_across_mixed_offsets():
    runs = [
        {"captured_at": "2026-08-17T12:00:00+00:00"},
        {"captured_at": "2026-08-17T05:00:00-05:00"},  # 10:00 UTC
        {"captured_at": "2026-08-17T09:00:00Z"},  # 09:00 UTC — earliest
    ]
    assert oldest_timestamp(runs) == "2026-08-17T09:00:00Z"


def test_oldest_timestamp_ignores_malformed_values():
    runs = [
        {"captured_at": "not-a-timestamp"},
        {"captured_at": "2026-08-17T09:00:00Z"},
        {"captured_at": None},
        "not-even-a-dict",
    ]
    assert oldest_timestamp(runs) == "2026-08-17T09:00:00Z"


def test_aggregate_telemetry_sums_numeric_fields_and_counts_runs():
    runs = [
        {"subqueries": 2, "tokens": 100},
        {"subqueries": 3, "tokens": 250},
        {},  # missing metrics contribute 0, not an error
    ]
    result = aggregate_telemetry(runs)
    assert result == {"retrieval_count": 3, "subqueries": 5, "tokens": 350}


def test_aggregate_telemetry_rejects_boolean_values():
    with pytest.raises(GroundValidationError):
        aggregate_telemetry([{"subqueries": True}])


def test_aggregate_telemetry_rejects_non_numeric_values():
    with pytest.raises(GroundValidationError):
        aggregate_telemetry([{"tokens": "a lot"}])


def test_aggregate_telemetry_rejects_non_finite_values():
    with pytest.raises(GroundValidationError):
        aggregate_telemetry([{"tokens": float("nan")}])


def test_freshness_pass_when_no_sources_declared():
    assert assess_freshness_coverage([], [], generated_at=PINNED)["status"] == "pass"


def test_freshness_not_verified_when_sources_declared_but_no_evidence():
    manifest = ground_manifest(sources=[source()])
    assert by_id(manifest)["GRD-004"]["status"] == "not-verified"


def test_freshness_should_fix_when_a_declared_source_is_uncovered():
    manifest = ground_manifest(
        sources=[source(id="policy-library"), source(id="hr-handbook")],
        acl_runs=[
            {"source_id": "policy-library", "principal": "entitled", "document_ids": ["A"]},
            {"source_id": "policy-library", "principal": "unentitled", "document_ids": ["B"]},
        ],
    )
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "should-fix"
    assert finding["detail"]["uncovered_sources"] == ["hr-handbook"]


def test_freshness_should_fix_when_evidence_is_stale_for_cadence():
    manifest = ground_manifest(
        sources=[source(id="policy-library", refresh_cadence="hourly")],
        acl_runs=[
            {
                "source_id": "policy-library",
                "principal": "entitled",
                "document_ids": ["A"],
                "captured_at": "2026-08-01T00:00:00+00:00",
            },
            {
                "source_id": "policy-library",
                "principal": "unentitled",
                "document_ids": ["B"],
                "captured_at": "2026-08-01T00:00:00+00:00",
            },
        ],
        generated_at=PINNED,
    )
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "should-fix"
    assert finding["detail"]["stale_sources"] == ["policy-library"]


def test_freshness_pass_when_covered_and_fresh():
    manifest = ground_manifest(
        sources=[source(id="policy-library", refresh_cadence="daily")],
        acl_runs=[
            {
                "source_id": "policy-library",
                "principal": "entitled",
                "document_ids": ["A"],
                "captured_at": "2026-08-17T09:00:00+00:00",
            },
            {
                "source_id": "policy-library",
                "principal": "unentitled",
                "document_ids": ["B"],
                "captured_at": "2026-08-17T09:30:00+00:00",
            },
        ],
        generated_at=PINNED,
    )
    assert by_id(manifest)["GRD-004"]["status"] == "pass"


# ---------------------------------------------------------------------------
# Requirement 6 — persistence: safe fields only, forbidden keys rejected
# ---------------------------------------------------------------------------
def test_manifest_never_persists_retrieved_document_content():
    manifest = ground_manifest(
        citation_runs=[
            {
                "citations": ["doc-1"],
                "retrieved_ids": ["doc-1"],
                "content": "the confidential text of doc-1",
            }
        ]
    )
    dumped = json.dumps(manifest)
    assert "confidential" not in dumped


def test_manifest_payload_contains_only_curated_keys():
    manifest = ground_manifest()
    assert set(manifest) >= {
        "schema", "tool_version", "generated_at", "freshness", "status", "findings",
        "sources", "acl_evidence", "citation_evidence", "refusal_evidence", "telemetry",
    }


@pytest.mark.parametrize(
    "forbidden_key",
    ["access_token", "api_key", "password", "secret", "credential", "authorization", "content", "prompt"],
)
def test_forbidden_keys_are_rejected_wherever_they_appear(forbidden_key):
    manifest = ground_manifest()
    # Inject into a normally-unconstrained free-form location (a finding's
    # `detail`) to prove the recursive scan, not just top-level shape checks,
    # is what catches this.
    manifest["findings"][0]["detail"] = {forbidden_key: "leaked-value"}
    with pytest.raises(GroundValidationError, match="credential/content/prompt-shaped"):
        validate_ground_manifest(manifest)


def test_legitimate_tokens_metric_is_not_treated_as_forbidden():
    # "tokens" (a token COUNT) must never be confused with a credential-shaped
    # "access_token" / "api_token" key.
    manifest = ground_manifest(
        citation_runs=[{"citations": [], "retrieved_ids": [], "tokens": 42, "subqueries": 1}]
    )
    validate_ground_manifest(manifest)  # must not raise
    assert manifest["telemetry"]["tokens"] == 42
    assert manifest["telemetry"]["subqueries"] == 1


def test_assess_grounding_itself_rejects_a_forbidden_key_in_a_source():
    with pytest.raises(GroundValidationError):
        assess_grounding(
            sources=[source(access_token="leak-me")],
            acl_runs=[],
            citation_runs=[],
            refusal_runs=[],
            generated_at=PINNED,
        )


# ---------------------------------------------------------------------------
# Requirement 8 — atomic, schema-validated writer
# ---------------------------------------------------------------------------
def test_write_ground_manifest_round_trips(tmp_path):
    manifest = ground_manifest(
        acl_runs=[
            {"principal": "entitled", "document_ids": ["A"]},
            {"principal": "unentitled", "document_ids": ["B"]},
        ]
    )
    path = tmp_path / "specs" / "ground-manifest.json"
    write_ground_manifest(path, manifest)
    assert json.loads(path.read_text(encoding="utf-8")) == manifest


def test_write_ground_manifest_rejects_invalid_manifest_without_writing(tmp_path):
    manifest = ground_manifest()
    manifest["status"] = "not-a-real-status"
    path = tmp_path / "specs" / "ground-manifest.json"
    with pytest.raises(ManifestValidationError):
        write_ground_manifest(path, manifest)
    assert not path.exists()


def test_write_ground_manifest_preserves_prior_valid_file_on_validation_failure(tmp_path):
    path = tmp_path / "specs" / "ground-manifest.json"
    original = ground_manifest()
    write_ground_manifest(path, original)
    original_bytes = path.read_bytes()

    broken = ground_manifest()
    broken["telemetry"]["tokens"] = "not-a-number"
    with pytest.raises(ManifestValidationError):
        write_ground_manifest(path, broken)

    assert path.read_bytes() == original_bytes
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_write_ground_manifest_cleans_temp_file_on_interrupted_replace(tmp_path, monkeypatch):
    path = tmp_path / "specs" / "ground-manifest.json"
    original = ground_manifest()
    write_ground_manifest(path, original)
    original_bytes = path.read_bytes()

    def interrupt_replace(source, destination):
        raise KeyboardInterrupt

    monkeypatch.setattr(ground.os, "replace", interrupt_replace)

    updated = ground_manifest(
        acl_runs=[
            {"principal": "entitled", "document_ids": ["A"]},
            {"principal": "unentitled", "document_ids": ["A"]},
        ]
    )
    with pytest.raises(KeyboardInterrupt):
        write_ground_manifest(path, updated)

    assert path.read_bytes() == original_bytes
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_write_ground_manifest_is_deterministic_for_identical_inputs(tmp_path):
    path_a = tmp_path / "a" / "ground-manifest.json"
    path_b = tmp_path / "b" / "ground-manifest.json"
    manifest = ground_manifest(
        acl_runs=[
            {"principal": "entitled", "document_ids": ["A", "B"]},
            {"principal": "unentitled", "document_ids": ["C"]},
        ]
    )
    write_ground_manifest(path_a, manifest)
    write_ground_manifest(path_b, manifest)
    assert path_a.read_bytes() == path_b.read_bytes()


# ---------------------------------------------------------------------------
# assess_grounding() input validation
# ---------------------------------------------------------------------------
def test_assess_grounding_rejects_non_list_sources():
    with pytest.raises(GroundValidationError):
        assess_grounding(
            sources="not-a-list",
            acl_runs=[],
            citation_runs=[],
            refusal_runs=[],
            generated_at=PINNED,
        )


def test_assess_grounding_requires_rfc3339_generated_at():
    with pytest.raises(ManifestValidationError):
        assess_grounding(
            sources=[],
            acl_runs=[],
            citation_runs=[],
            refusal_runs=[],
            generated_at="2026-08-17 10:00:00",  # space separator — not RFC3339
        )


def test_assess_grounding_findings_always_include_all_four_ids_in_order():
    manifest = ground_manifest()
    assert [f["id"] for f in manifest["findings"]] == [
        "GRD-001", "GRD-002", "GRD-003", "GRD-004",
    ]


def test_status_complete_when_every_required_leg_is_verified():
    manifest = ground_manifest(
        sources=[source(permission_model="public")],
        acl_runs=[],
        citation_runs=[{"citations": ["doc-1"], "retrieved_ids": ["doc-1"]}],
        refusal_runs=[{"query_id": "q1", "refused": True}],
    )
    assert manifest["status"] == "complete"


def test_status_complete_when_acl_leg_is_an_executed_must_fix():
    # Requirement 2: an EXECUTED must-fix is still complete evidence — it must
    # never downgrade `status` to partial on its own.
    manifest = ground_manifest(
        sources=[source()],
        acl_runs=[
            {"principal": "entitled", "document_ids": ["doc-1", "doc-2"]},
            {"principal": "unentitled", "document_ids": ["doc-1", "doc-2"]},
        ],
        citation_runs=[{"citations": ["doc-1"], "retrieved_ids": ["doc-1"]}],
        refusal_runs=[{"query_id": "q1", "refused": True}],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "must-fix"
    assert manifest["status"] == "complete"


def test_status_partial_when_any_leg_is_not_verified():
    manifest = ground_manifest(
        sources=[source(permission_model="public")],
        citation_runs=[{"citations": ["doc-1"], "retrieved_ids": ["doc-1"]}],
        refusal_runs=[],  # no refusal runs -> GRD-003 not-verified
    )
    assert manifest["status"] == "partial"


# ---------------------------------------------------------------------------
# Requirement 11 — schema parity (hand validator vs. jsonschema)
# ---------------------------------------------------------------------------
_REFERENCES_DIR = SKILL_ROOT / "references"


@pytest.fixture(scope="module")
def jsonschema_validator():
    """A Draft-07 validator over the on-disk ground-manifest schema, using
    jsonschema's stock ``date-time`` format checker. Skips (rather than
    asserting a false parity) when no RFC-3339 format backend is installed —
    matches the threadlight-connect parity-suite convention.
    """
    jsonschema = pytest.importorskip("jsonschema")

    manifest_schema = json.loads(
        (_REFERENCES_DIR / "ground-manifest.schema.json").read_text(encoding="utf-8")
    )
    format_checker = jsonschema.FormatChecker()
    if "date-time" not in format_checker.checkers:
        pytest.skip(
            "jsonschema's standard 'date-time' format check requires an RFC-3339 "
            "backend (e.g. rfc3339-validator); without it the parity suite cannot "
            "prove timestamp accept/reject against an independent validator"
        )
    return jsonschema.Draft7Validator(manifest_schema, format_checker=format_checker)


def _rich_ground_manifest():
    return ground_manifest(
        sources=[source(acl_probe_principals=["entitled", "unentitled"])],
        acl_runs=[
            {"principal": "entitled", "document_ids": ["doc-1"], "captured_at": PINNED},
            {"principal": "unentitled", "document_ids": ["doc-2"], "captured_at": PINNED},
        ],
        citation_runs=[{"citations": ["doc-1"], "retrieved_ids": ["doc-1"], "subqueries": 2, "tokens": 50}],
        refusal_runs=[{"query_id": "q1", "refused": True}],
    )


def test_valid_manifest_accepted_by_both_validators(jsonschema_validator):
    manifest = _rich_ground_manifest()
    validate_ground_manifest(manifest)  # must not raise
    errors = list(jsonschema_validator.iter_errors(manifest))
    assert errors == [], [e.message for e in errors]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(schema="wrong/v1"),
        lambda m: m["findings"][0].update(status="not-a-status"),
        lambda m: m["sources"][0].pop("permission_model"),
        lambda m: m["sources"][0].update(extra_field="nope"),
        lambda m: m["acl_evidence"].append({"principal": "x"}),  # missing keys
        lambda m: m["citation_evidence"][0].update(citation_count="not-a-number"),
        lambda m: m["refusal_evidence"][0].update(query_id=123),
        lambda m: m["telemetry"].update(tokens=-1),
        lambda m: m.update(unexpected_top_level_key="nope"),
    ],
)
def test_malformed_manifest_rejected_by_both_validators(mutate, jsonschema_validator):
    manifest = _rich_ground_manifest()
    mutate(manifest)
    with pytest.raises((ManifestValidationError, GroundValidationError)):
        validate_ground_manifest(manifest)
    assert not jsonschema_validator.is_valid(manifest), (
        "hand validator rejected this manifest but jsonschema accepted it"
    )
