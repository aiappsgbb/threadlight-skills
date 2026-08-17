"""Tests for the threadlight-ground skill (`ground.py`) — the GROUND leg:
turning already-produced ACL/citation/refusal probe evidence into the
`threadlight.ground/v1` manifest (GRD-001..004).

`ground.py` is a coordinator, not a retrieval/evaluation engine: it never
calls Foundry IQ and never runs an evaluator. Every test below supplies
already-produced evidence (the shape a manual live handoff would capture) and
asserts the resulting findings/manifest.

The `sources` argument is the authoritative SPEC-derived inventory: every
declared source's enabled controls must be covered by evidence carrying that
source's `source_id`, coverage is never inferred across sources, and missing
source/control evidence is `not-verified` (→ manifest `partial`), never a
guessed `pass`.
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
    GroundEvidenceError,
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
FRESH = "2026-08-17T09:00:00+00:00"
STALE = "2026-08-01T00:00:00+00:00"
BASELINE = "specs/baselines/retrieval-quality.json"
SHA1_DOCUMENT_ID = "0123456789abcdef0123456789abcdef01234567"
SHA256_DOCUMENT_ID = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
BASE64URL_DOCUMENT_ID = "QXp1cmVfU2VhcmNoLWRvY3VtZW50LWtleS0wMTIzNDU2Nzg5YWJjZGVm"


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


def acl_run(principal, document_ids, source_id="policy-library", expected_entitled=None,
            captured_at=FRESH, **extra):
    run = {
        "principal": principal,
        "document_ids": list(document_ids),
        "source_id": source_id,
        "captured_at": captured_at,
    }
    if expected_entitled is not None:
        run["expected_entitled"] = expected_entitled
    run.update(extra)
    return run


def entitled(principal="entitled-analyst", document_ids=("doc-1",), **overrides):
    return acl_run(principal, document_ids, expected_entitled=True, **overrides)


def unentitled(principal="unentitled-guest", document_ids=(), **overrides):
    return acl_run(principal, document_ids, expected_entitled=False, **overrides)


def cite_run(citations=("doc-1",), retrieved_ids=("doc-1", "doc-2"),
             source_id="policy-library", captured_at=FRESH, **extra):
    run = {
        "citations": list(citations),
        "retrieved_ids": list(retrieved_ids),
        "source_id": source_id,
        "captured_at": captured_at,
    }
    run.update(extra)
    return run


def refuse_run(query_id="q1", refused=True, source_id="policy-library",
               captured_at=FRESH, **extra):
    run = {
        "query_id": query_id,
        "refused": refused,
        "source_id": source_id,
        "captured_at": captured_at,
    }
    run.update(extra)
    return run


def by_id(manifest: dict) -> dict:
    return {finding["id"]: finding for finding in manifest["findings"]}


def ground_manifest(
    sources=None,
    acl_runs=None,
    citation_runs=None,
    refusal_runs=None,
    generated_at=PINNED,
    retrieval_quality_baseline=BASELINE,
):
    return assess_grounding(
        sources=sources if sources is not None else [source()],
        acl_runs=acl_runs or [],
        citation_runs=citation_runs or [],
        refusal_runs=refusal_runs or [],
        generated_at=generated_at,
        retrieval_quality_baseline=retrieval_quality_baseline,
    )


def covered(**overrides):
    """A fully-covering, fresh evidence bundle for the default single ACL
    source — every control satisfied so the manifest is `complete`."""
    kwargs = dict(
        sources=[source()],
        acl_runs=[entitled(), unentitled()],
        citation_runs=[cite_run()],
        refusal_runs=[refuse_run()],
    )
    kwargs.update(overrides)
    return ground_manifest(**kwargs)


# ---------------------------------------------------------------------------
# GRD-001 — ACL enforcement: coverage + not-verified (never guessed)
# ---------------------------------------------------------------------------
def test_no_acl_protected_sources_is_pass_without_runs():
    manifest = ground_manifest(sources=[source(permission_model="public")], acl_runs=[])
    assert by_id(manifest)["GRD-001"]["status"] == "pass"
    assert by_id(manifest)["GRD-001"]["detail"]["reason"] == "no-acl-protected-sources"


def test_acl_source_with_no_runs_is_not_verified_and_partial():
    manifest = ground_manifest(sources=[source()], acl_runs=[])
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["reason"] == "acl-source-uncovered"
    assert manifest["status"] == "partial"


def test_single_principal_only_is_not_verified():
    result = assess_acl([source()], [entitled(document_ids=["A"])])
    assert result["status"] == "not-verified"
    assert result["detail"]["reason"] == "insufficient-principals"


def test_only_entitled_principals_is_not_verified():
    result = assess_acl(
        [source()],
        [entitled("entitled-a", ["A"]), entitled("entitled-b", ["B"])],
    )
    assert result["status"] == "not-verified"
    assert result["detail"]["reason"] == "missing-entitled-or-unentitled-probe"


def test_ambiguous_expectation_stays_not_verified_never_guessed():
    # No explicit `expected_entitled` on either run — entitlement must NOT be
    # guessed from the principal name.
    result = assess_acl(
        [source()],
        [
            acl_run("entitled-sounding", ["A"]),
            acl_run("unentitled-sounding", ["B"]),
        ],
    )
    assert result["status"] == "not-verified"
    assert result["detail"]["reason"] == "ambiguous-entitlement"


# ---------------------------------------------------------------------------
# GRD-001 — strict evidence shapes raise before any output (Requirement 4)
# ---------------------------------------------------------------------------
def test_acl_run_missing_source_id_raises():
    with pytest.raises(GroundEvidenceError, match="source_id"):
        assess_acl([source()], [{"principal": "e", "document_ids": ["A"], "expected_entitled": True}])


def test_acl_run_unknown_source_id_raises():
    with pytest.raises(GroundEvidenceError, match="not a declared"):
        assess_acl(
            [source()],
            [acl_run("e", ["A"], source_id="ghost", expected_entitled=True)],
        )


def test_acl_run_missing_document_ids_raises():
    with pytest.raises(GroundEvidenceError, match="document_ids"):
        assess_acl(
            [source()],
            [{"principal": "e", "source_id": "policy-library", "expected_entitled": True}],
        )


def test_acl_run_document_id_must_be_nonempty_string():
    with pytest.raises(GroundEvidenceError, match="document_ids"):
        assess_acl([source()], [acl_run("e", ["A", ""], expected_entitled=True)])


def test_acl_expected_entitled_must_be_bool_not_numeric():
    with pytest.raises(GroundEvidenceError, match="expected_entitled must be a boolean"):
        assess_acl([source()], [acl_run("e", ["A"], expected_entitled=1)])


def test_acl_run_principal_must_be_nonempty_string():
    with pytest.raises(GroundEvidenceError, match="principal"):
        assess_acl(
            [source()],
            [{"principal": "", "document_ids": ["A"], "source_id": "policy-library",
              "expected_entitled": True}],
        )


# ---------------------------------------------------------------------------
# GRD-001 — allowlist / subset leaks (Requirement 2)
# ---------------------------------------------------------------------------
def test_unentitled_receiving_any_document_without_allowlist_is_must_fix():
    # No allowlist ⇒ nothing is allowed ⇒ any received document is unauthorized.
    result = assess_acl(
        [source()],
        [entitled("e", ["doc-1"]), unentitled("u", ["doc-3"])],
    )
    assert result["status"] == "must-fix"
    assert result["detail"]["reason"] == "unauthorized-documents"
    assert result["detail"]["leaked_document_ids"] == ["doc-3"]


def test_subset_leak_single_unauthorized_document_is_must_fix():
    # The unentitled principal is allowed doc-1 but also received doc-2 — a
    # subset outside the allowlist is still a leak.
    result = assess_acl(
        [source()],
        [
            entitled("e", ["doc-1", "doc-2"]),
            unentitled("u", ["doc-1", "doc-2"], allowed_document_ids=["doc-1"]),
        ],
    )
    assert result["status"] == "must-fix"
    assert result["detail"]["leaked_document_ids"] == ["doc-2"]


def test_allowlisted_public_documents_are_not_a_leak():
    # Both principals receive only the public doc, which is explicitly allowed
    # for the unentitled principal — legitimate, not a leak.
    result = assess_acl(
        [source()],
        [
            entitled("e", ["public-doc", "protected-doc"]),
            unentitled("u", ["public-doc"], allowed_document_ids=["public-doc"]),
        ],
    )
    assert result["status"] == "pass"
    assert result["detail"]["reason"] == "acl-enforced"


def test_identical_protected_set_for_incompatible_principals_stays_must_fix():
    result = assess_acl(
        [source()],
        [
            entitled("e", ["doc-1", "doc-2"]),
            unentitled("u", ["doc-1", "doc-2"]),
        ],
    )
    assert result["status"] == "must-fix"
    assert result["detail"]["leaked_document_ids"] == ["doc-1", "doc-2"]


def test_distinct_results_with_empty_unentitled_set_is_pass():
    result = assess_acl([source()], [entitled("e", ["doc-1"]), unentitled("u", [])])
    assert result["status"] == "pass"


def test_explicit_expected_entitled_overrides_name_heuristics():
    # Names alone would suggest the opposite classification ("admin" sounds
    # entitled) — the explicit field is authoritative, and the distinct doc
    # sets prove it drove the classification.
    result = assess_acl(
        [source()],
        [
            acl_run("svc-admin-probe", ["doc-1"], expected_entitled=False,
                    allowed_document_ids=["doc-1"]),
            acl_run("svc-guest-probe", ["doc-2"], expected_entitled=True),
        ],
    )
    assert result["status"] == "pass"


def test_explicit_expected_entitled_still_catches_a_leak():
    result = assess_acl(
        [source()],
        [
            acl_run("svc-guest-probe", ["doc-1"], expected_entitled=True),
            acl_run("svc-admin-probe", ["doc-1"], expected_entitled=False),
        ],
    )
    assert result["status"] == "must-fix"


def test_declared_principal_coverage_is_required():
    # The source declares two principals to probe; only one appears in the runs.
    result = assess_acl(
        [source(acl_probe_principals=["entitled-analyst", "unentitled-guest", "svc-audit"])],
        [entitled(), unentitled()],
    )
    assert result["status"] == "not-verified"
    assert result["detail"]["reason"] == "declared-principal-uncovered"
    assert result["detail"]["missing_principals"] == ["svc-audit"]


def test_acl_runs_grouped_by_source_id_worst_case_wins():
    sources_list = [source(id="policy-library"), source(id="hr-handbook")]
    acl_runs = [
        # policy-library: proven leak (no allowlist, unentitled got a doc)
        entitled("e", ["p1"], source_id="policy-library"),
        unentitled("u", ["p1"], source_id="policy-library"),
        # hr-handbook: clean (unentitled got nothing)
        entitled("e", ["h1"], source_id="hr-handbook"),
        unentitled("u", [], source_id="hr-handbook"),
    ]
    manifest = ground_manifest(sources=sources_list, acl_runs=acl_runs)
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["worst_source"] == "policy-library"
    assert finding["detail"]["by_source"] == {
        "policy-library": "must-fix",
        "hr-handbook": "pass",
    }


def test_acl_cross_source_hole_forces_partial_even_with_a_leak_elsewhere():
    # policy-library proves a leak (must-fix); hr-handbook has NO runs at all.
    # The must-fix surfaces, but the uncovered source still forces `partial`.
    sources_list = [source(id="policy-library"), source(id="hr-handbook")]
    manifest = ground_manifest(
        sources=sources_list,
        acl_runs=[
            entitled("e", ["p1"], source_id="policy-library"),
            unentitled("u", ["p1"], source_id="policy-library"),
        ],
        citation_runs=[cite_run(source_id="policy-library"), cite_run(source_id="hr-handbook")],
        refusal_runs=[refuse_run(source_id="policy-library"), refuse_run(source_id="hr-handbook")],
    )
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["by_source"]["hr-handbook"] == "not-verified"
    assert finding["detail"]["uncovered_sources"] == ["hr-handbook"]
    assert manifest["status"] == "partial"


# ---------------------------------------------------------------------------
# GRD-002 — citation grounding (source-scoped)
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


def test_citation_required_source_with_no_runs_is_not_verified():
    manifest = ground_manifest(citation_runs=[])
    finding = by_id(manifest)["GRD-002"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["reason"] == "citation-source-uncovered"


def test_citation_outside_retrieval_is_must_fix_at_manifest_level():
    manifest = ground_manifest(
        citation_runs=[cite_run(citations=["doc-9"], retrieved_ids=["doc-1", "doc-2"])]
    )
    finding = by_id(manifest)["GRD-002"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["missing_from_retrieval"] == ["doc-9"]


def test_citation_must_fix_is_complete_evidence_when_fully_covered():
    manifest = covered(
        citation_runs=[cite_run(citations=["doc-9"], retrieved_ids=["doc-1"])],
    )
    assert by_id(manifest)["GRD-002"]["status"] == "must-fix"
    assert manifest["status"] == "complete"


def test_valid_citation_run_passes_at_manifest_level():
    manifest = ground_manifest(citation_runs=[cite_run()])
    assert by_id(manifest)["GRD-002"]["status"] == "pass"


def test_citation_run_missing_fields_raises():
    with pytest.raises(GroundEvidenceError, match="retrieved_ids"):
        assess_citations([source()], [{"source_id": "policy-library", "citations": ["doc-1"]}])


def test_citation_run_unknown_source_id_raises():
    with pytest.raises(GroundEvidenceError, match="not a declared"):
        assess_citations([source()], [cite_run(source_id="ghost")])


def test_no_citation_required_source_is_trivial_pass():
    manifest = ground_manifest(
        sources=[source(permission_model="public", citation_required=False,
                        refuse_when_unsupported=False)],
        citation_runs=[],
        refusal_runs=[],
        acl_runs=[],
    )
    finding = by_id(manifest)["GRD-002"]
    assert finding["status"] == "pass"
    assert finding["detail"]["reason"] == "no-citation-required"


def test_citation_cross_source_hole_is_not_verified():
    manifest = ground_manifest(
        sources=[source(id="a", permission_model="public"),
                 source(id="b", permission_model="public")],
        citation_runs=[cite_run(source_id="a")],
        refusal_runs=[refuse_run(source_id="a"), refuse_run(source_id="b")],
    )
    finding = by_id(manifest)["GRD-002"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["uncovered_sources"] == ["b"]


# ---------------------------------------------------------------------------
# GRD-003 — refusal behavior (source-scoped)
# ---------------------------------------------------------------------------
def test_refusal_pass_when_every_unsupported_query_is_refused():
    manifest = ground_manifest(
        refusal_runs=[refuse_run("q1", True), refuse_run("q2", True)]
    )
    assert by_id(manifest)["GRD-003"]["status"] == "pass"


def test_refusal_must_fix_when_unsupported_query_is_answered():
    manifest = ground_manifest(
        refusal_runs=[refuse_run("q1", True), refuse_run("q2", False)]
    )
    finding = by_id(manifest)["GRD-003"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["unsupported_queries_answered"] == ["q2"]


def test_refusal_source_with_no_runs_is_not_verified():
    manifest = ground_manifest(refusal_runs=[])
    finding = by_id(manifest)["GRD-003"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["reason"] == "refusal-source-uncovered"


def test_refusal_run_missing_boolean_raises():
    with pytest.raises(GroundEvidenceError, match="refused"):
        assess_refusal([source()], [{"source_id": "policy-library", "query_id": "q1"}])


def test_refusal_run_missing_query_id_raises():
    with pytest.raises(GroundEvidenceError, match="query_id"):
        assess_refusal([source()], [{"source_id": "policy-library", "refused": True}])


def test_refusal_never_persists_raw_query_text():
    manifest = ground_manifest(
        refusal_runs=[refuse_run("q1", True, query="what is the CEO's salary?")]
    )
    assert "salary" not in json.dumps(manifest)


# ---------------------------------------------------------------------------
# Requirement 7 (freshness `oldest_timestamp`) and telemetry
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


def test_oldest_timestamp_ignores_malformed_values_including_space_separator():
    runs = [
        {"captured_at": "not-a-timestamp"},
        {"captured_at": "2026-08-17 09:00:00+00:00"},  # space separator — not RFC3339
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
    assert aggregate_telemetry(runs) == {"retrieval_count": 3, "subqueries": 5, "tokens": 350}


def test_aggregate_telemetry_rejects_boolean_values():
    with pytest.raises(GroundEvidenceError):
        aggregate_telemetry([{"subqueries": True}])


def test_aggregate_telemetry_rejects_non_numeric_values():
    with pytest.raises(GroundEvidenceError):
        aggregate_telemetry([{"tokens": "a lot"}])


def test_aggregate_telemetry_rejects_non_finite_values():
    with pytest.raises(GroundEvidenceError):
        aggregate_telemetry([{"tokens": float("nan")}])


def test_aggregate_telemetry_rejects_negative_values():
    with pytest.raises(GroundEvidenceError):
        aggregate_telemetry([{"subqueries": -1}])


# ---------------------------------------------------------------------------
# GRD-004 — freshness / coverage (per-source) + baseline (Req 3 & 8)
# ---------------------------------------------------------------------------
def test_freshness_pass_when_no_sources_declared():
    result = assess_freshness_coverage([], [], generated_at=PINNED)
    assert result["status"] == "pass"
    assert result["detail"]["reason"] == "no-knowledge-sources"


def test_freshness_not_verified_when_sources_declared_but_no_evidence():
    manifest = ground_manifest(sources=[source()])
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "not-verified"


def test_freshness_not_verified_when_a_declared_source_is_uncovered():
    manifest = covered(
        sources=[source(id="policy-library"), source(id="hr-handbook")],
    )
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["reason"] == "sources-uncovered"
    assert finding["detail"]["uncovered_sources"] == ["hr-handbook"]


def test_freshness_should_fix_when_evidence_is_stale_for_cadence():
    manifest = ground_manifest(
        sources=[source(id="policy-library", refresh_cadence="hourly")],
        acl_runs=[
            entitled("e", ["A"], captured_at=STALE),
            unentitled("u", [], captured_at=STALE),
        ],
        citation_runs=[cite_run(captured_at=STALE)],
        refusal_runs=[refuse_run(captured_at=STALE)],
    )
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "should-fix"
    assert finding["detail"]["stale_sources"] == ["policy-library"]


def test_freshness_is_computed_independently_per_source():
    # policy-library is hourly and old (stale); hr-handbook is hourly and fresh.
    # One source's old run must NOT stale the other.
    manifest = ground_manifest(
        sources=[
            source(id="policy-library", permission_model="public", refresh_cadence="hourly"),
            source(id="hr-handbook", permission_model="public", refresh_cadence="hourly"),
        ],
        citation_runs=[
            cite_run(source_id="policy-library", captured_at=STALE),
            cite_run(source_id="hr-handbook", captured_at=FRESH),
        ],
        refusal_runs=[
            refuse_run(source_id="policy-library", captured_at=STALE),
            refuse_run(source_id="hr-handbook", captured_at=FRESH),
        ],
    )
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "should-fix"
    assert finding["detail"]["stale_sources"] == ["policy-library"]


def test_freshness_unverifiable_when_covered_source_has_no_valid_timestamp():
    manifest = ground_manifest(
        sources=[source(id="policy-library", permission_model="public",
                        citation_required=True, refuse_when_unsupported=False)],
        citation_runs=[cite_run(captured_at="2026-08-17 09:00:00")],  # space sep — malformed
    )
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["reason"] == "freshness-unverifiable"
    assert finding["detail"]["unverifiable_freshness_sources"] == ["policy-library"]


def test_freshness_pass_when_covered_and_fresh():
    manifest = covered()
    assert by_id(manifest)["GRD-004"]["status"] == "pass"


def test_envelope_source_oldest_at_is_global_oldest_valid_timestamp():
    manifest = ground_manifest(
        sources=[
            source(id="policy-library", permission_model="public"),
            source(id="hr-handbook", permission_model="public"),
        ],
        citation_runs=[
            cite_run(source_id="policy-library", captured_at="2026-08-17T09:00:00+00:00"),
            cite_run(source_id="hr-handbook", captured_at="2026-08-16T00:00:00+00:00"),
        ],
        refusal_runs=[
            refuse_run(source_id="policy-library", captured_at=FRESH),
            refuse_run(source_id="hr-handbook", captured_at=FRESH),
        ],
    )
    assert manifest["freshness"]["source_oldest_at"] == "2026-08-16T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Requirement 8 — retrieval-quality baseline reference
# ---------------------------------------------------------------------------
def test_baseline_reference_is_persisted_in_payload():
    manifest = covered()
    assert manifest["retrieval_quality_baseline"] == BASELINE


def test_missing_baseline_makes_grd004_not_verified_and_partial():
    manifest = covered(retrieval_quality_baseline=None)
    finding = by_id(manifest)["GRD-004"]
    assert finding["status"] == "not-verified"
    assert finding["detail"]["reason"] == "retrieval-quality-baseline-missing"
    assert manifest["status"] == "partial"
    assert manifest["retrieval_quality_baseline"] is None


@pytest.mark.parametrize(
    "bad_baseline",
    ["../secrets.json", "/etc/passwd", "https://evil.example/x", "a b c",
     "..", "C:\\\\secrets", "path/../escape"],
)
def test_unsafe_baseline_reference_is_rejected(bad_baseline):
    with pytest.raises(GroundEvidenceError, match="retrieval_quality_baseline"):
        ground_manifest(retrieval_quality_baseline=bad_baseline)


def test_non_string_baseline_reference_is_rejected():
    with pytest.raises(GroundEvidenceError):
        ground_manifest(retrieval_quality_baseline=123)


def test_secret_shaped_baseline_reference_is_rejected():
    with pytest.raises(GroundEvidenceError):
        ground_manifest(retrieval_quality_baseline="AKIAIOSFODNN7EXAMPLE0000")


def test_dotless_long_baseline_reference_is_accepted():
    baseline_ref = "retrievalqualitybaseline20260817abcdef0123456789abcdef0123456789"
    manifest = covered(retrieval_quality_baseline=baseline_ref)
    assert manifest["retrieval_quality_baseline"] == baseline_ref


# ---------------------------------------------------------------------------
# Requirement 6 — persistence: allowlisted detail, forbidden keys/values
# ---------------------------------------------------------------------------
def test_manifest_never_persists_retrieved_document_content():
    manifest = ground_manifest(
        citation_runs=[cite_run(content="the confidential text of doc-1")]
    )
    assert "confidential" not in json.dumps(manifest)


def test_manifest_payload_contains_only_curated_keys():
    manifest = covered()
    assert set(manifest) == {
        "schema", "tool_version", "generated_at", "freshness", "status", "findings",
        "sources", "acl_evidence", "citation_evidence", "refusal_evidence", "telemetry",
        "retrieval_quality_baseline",
    }


@pytest.mark.parametrize(
    "forbidden_key",
    ["access_token", "api_key", "password", "secret", "credential", "authorization",
     "content", "prompt"],
)
def test_forbidden_keys_are_rejected_wherever_they_appear(forbidden_key):
    manifest = covered()
    # Inject into a finding's `detail` to prove the recursive scan, not just
    # top-level shape checks, is what catches this.
    manifest["findings"][0]["detail"] = {forbidden_key: "leaked-value"}
    with pytest.raises(GroundEvidenceError, match="credential/content/prompt-shaped"):
        validate_ground_manifest(manifest)


@pytest.mark.parametrize("forbidden_key", ["access_token", "password", "client_secret"])
def test_forbidden_secret_key_names_are_rejected_recursively(forbidden_key):
    manifest = covered()
    manifest["findings"][0]["detail"]["by_source"] = {forbidden_key: "pass"}
    with pytest.raises(GroundEvidenceError, match="credential/content/prompt-shaped"):
        validate_ground_manifest(manifest)


@pytest.mark.parametrize(
    "secret_value",
    [
        "api_key=sk-abcdefghijklmnopqrstuvwxyz012345",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEyMyJ9.signature012345",
        "Bearer opaque-access-token-value",
        "-----BEGIN RSA PRIVATE KEY-----",
        (
            "DefaultEndpointsProtocol=https;AccountName=storageacct;"
            "AccountKey=QWxwaGFCZXRhMTIzNDU2Nzg5MA==;"
            "EndpointSuffix=core.windows.net"
        ),
    ],
)
def test_explicit_secret_values_are_rejected_under_allowlisted_id_fields(secret_value):
    manifest = covered(
        acl_runs=[entitled("e", ["doc-1"]), unentitled("u", [])],
    )
    manifest["acl_evidence"][0]["document_ids"][0] = secret_value
    with pytest.raises(GroundEvidenceError, match="secret-shaped"):
        validate_ground_manifest(manifest)


@pytest.mark.parametrize(
    "document_id",
    [
        SHA1_DOCUMENT_ID,
        SHA256_DOCUMENT_ID,
        BASE64URL_DOCUMENT_ID,
        "550e8400-e29b-41d4-a716-446655440000-550e8400-e29b-41d4-a716-446655440000",
    ],
)
def test_long_opaque_document_ids_are_accepted(document_id):
    manifest = covered(
        acl_runs=[entitled("e", [document_id]), unentitled("u", [])],
        citation_runs=[cite_run(citations=[document_id], retrieved_ids=[document_id])],
    )
    validate_ground_manifest(manifest)
    assert manifest["acl_evidence"][0]["document_ids"] == [document_id]


@pytest.mark.parametrize(
    "document_id",
    [
        "QXp1cmUvU2VhcmNoK2RvY3VtZW50S2V5MDEyMzQ1Njc4OWFiY2RlZg==",
        BASE64URL_DOCUMENT_ID,
    ],
)
def test_base64_and_base64url_azure_search_document_keys_are_accepted(document_id):
    manifest = covered(
        acl_runs=[entitled("e", [document_id]), unentitled("u", [])],
    )
    assert manifest["acl_evidence"][0]["document_ids"] == [document_id]


def test_acl_leak_with_long_hash_id_is_must_fix_and_emits_manifest(tmp_path):
    manifest = covered(
        acl_runs=[
            entitled("e", [SHA256_DOCUMENT_ID]),
            unentitled("u", [SHA256_DOCUMENT_ID]),
        ],
    )
    finding = by_id(manifest)["GRD-001"]
    assert finding["status"] == "must-fix"
    assert finding["detail"]["leaked_document_ids"] == [SHA256_DOCUMENT_ID]

    path = tmp_path / "specs" / "ground-manifest.json"
    write_ground_manifest(path, manifest)
    emitted = json.loads(path.read_text(encoding="utf-8"))
    assert by_id(emitted)["GRD-001"]["status"] == "must-fix"
    assert emitted["status"] == "complete"


def test_finding_detail_rejects_free_form_keys():
    manifest = covered()
    manifest["findings"][0]["detail"]["note"] = "some free-form explanation"
    with pytest.raises(ManifestValidationError, match="unknown key"):
        validate_ground_manifest(manifest)


def test_finding_detail_rejects_unknown_reason():
    manifest = covered()
    manifest["findings"][0]["detail"]["reason"] = "made-up-reason"
    with pytest.raises(ManifestValidationError, match="reason"):
        validate_ground_manifest(manifest)


def test_legitimate_tokens_metric_is_not_treated_as_forbidden():
    manifest = ground_manifest(
        sources=[source(permission_model="public", citation_required=True,
                        refuse_when_unsupported=False)],
        citation_runs=[cite_run(citations=[], retrieved_ids=[], tokens=42, subqueries=1)],
    )
    validate_ground_manifest(manifest)  # must not raise
    assert manifest["telemetry"]["tokens"] == 42
    assert manifest["telemetry"]["subqueries"] == 1


def test_assess_grounding_itself_rejects_a_forbidden_key_in_a_source():
    with pytest.raises(GroundEvidenceError):
        assess_grounding(
            sources=[source(access_token="leak-me")],
            acl_runs=[],
            citation_runs=[],
            refusal_runs=[],
            generated_at=PINNED,
            retrieval_quality_baseline=BASELINE,
        )


def test_assess_grounding_validates_before_returning():
    # A secret smuggled through a source id must be caught by the built-in
    # validate step, so `--json` can never emit invalid/oversharing data.
    with pytest.raises(GroundEvidenceError, match="secret-shaped"):
        assess_grounding(
            sources=[source(id="AKIAIOSFODNN7EXAMPLE0", permission_model="public",
                            citation_required=False, refuse_when_unsupported=False)],
            acl_runs=[],
            citation_runs=[cite_run(source_id="AKIAIOSFODNN7EXAMPLE0")],
            refusal_runs=[],
            generated_at=PINNED,
            retrieval_quality_baseline=BASELINE,
        )


# ---------------------------------------------------------------------------
# Atomic, schema-validated writer
# ---------------------------------------------------------------------------
def test_write_ground_manifest_round_trips(tmp_path):
    manifest = covered()
    path = tmp_path / "specs" / "ground-manifest.json"
    write_ground_manifest(path, manifest)
    assert json.loads(path.read_text(encoding="utf-8")) == manifest


def test_write_ground_manifest_rejects_invalid_manifest_without_writing(tmp_path):
    manifest = covered()
    manifest["status"] = "not-a-real-status"
    path = tmp_path / "specs" / "ground-manifest.json"
    with pytest.raises(ManifestValidationError):
        write_ground_manifest(path, manifest)
    assert not path.exists()


def test_write_ground_manifest_preserves_prior_valid_file_on_validation_failure(tmp_path):
    path = tmp_path / "specs" / "ground-manifest.json"
    original = covered()
    write_ground_manifest(path, original)
    original_bytes = path.read_bytes()

    broken = covered()
    broken["telemetry"]["tokens"] = "not-a-number"
    with pytest.raises(ManifestValidationError):
        write_ground_manifest(path, broken)

    assert path.read_bytes() == original_bytes
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_write_ground_manifest_cleans_temp_file_on_interrupted_replace(tmp_path, monkeypatch):
    path = tmp_path / "specs" / "ground-manifest.json"
    original = covered()
    write_ground_manifest(path, original)
    original_bytes = path.read_bytes()

    def interrupt_replace(source, destination):
        raise KeyboardInterrupt

    monkeypatch.setattr(ground.os, "replace", interrupt_replace)

    updated = covered(acl_runs=[entitled("e", ["doc-1", "doc-2"]), unentitled("u", [])])
    with pytest.raises(KeyboardInterrupt):
        write_ground_manifest(path, updated)

    assert path.read_bytes() == original_bytes
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_write_ground_manifest_is_deterministic_for_identical_inputs(tmp_path):
    path_a = tmp_path / "a" / "ground-manifest.json"
    path_b = tmp_path / "b" / "ground-manifest.json"
    manifest = covered()
    write_ground_manifest(path_a, manifest)
    write_ground_manifest(path_b, manifest)
    assert path_a.read_bytes() == path_b.read_bytes()


# ---------------------------------------------------------------------------
# assess_grounding() input validation + manifest status
# ---------------------------------------------------------------------------
def test_assess_grounding_rejects_non_list_sources():
    with pytest.raises(GroundEvidenceError):
        assess_grounding(
            sources="not-a-list",
            acl_runs=[],
            citation_runs=[],
            refusal_runs=[],
            generated_at=PINNED,
            retrieval_quality_baseline=BASELINE,
        )


def test_assess_grounding_requires_rfc3339_generated_at():
    with pytest.raises(ManifestValidationError):
        assess_grounding(
            sources=[],
            acl_runs=[],
            citation_runs=[],
            refusal_runs=[],
            generated_at="2026-08-17 10:00:00",  # space separator — not RFC3339
            retrieval_quality_baseline=BASELINE,
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
        citation_runs=[cite_run(citations=["doc-1"], retrieved_ids=["doc-1"])],
        refusal_runs=[refuse_run()],
    )
    assert manifest["status"] == "complete"


def test_status_complete_when_acl_leg_is_an_executed_must_fix():
    # An EXECUTED must-fix with COMPLETE coverage is complete evidence — it
    # must never downgrade `status` to partial on its own.
    manifest = covered(
        acl_runs=[entitled("e", ["doc-1", "doc-2"]), unentitled("u", ["doc-1", "doc-2"])],
    )
    assert by_id(manifest)["GRD-001"]["status"] == "must-fix"
    assert manifest["status"] == "complete"


def test_status_partial_when_any_leg_is_not_verified():
    manifest = ground_manifest(
        sources=[source(permission_model="public")],
        citation_runs=[cite_run(citations=["doc-1"], retrieved_ids=["doc-1"])],
        refusal_runs=[],  # no refusal runs -> GRD-003 not-verified
    )
    assert manifest["status"] == "partial"


# ---------------------------------------------------------------------------
# CLI — root as output/project boundary, clean error handling (Req 4, 5, 9)
# ---------------------------------------------------------------------------
def _write_evidence(tmp_path, **evidence):
    evidence.setdefault("retrieval_quality_baseline", BASELINE)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def _covered_evidence():
    return dict(
        sources=[source()],
        acl_runs=[entitled(), unentitled()],
        citation_runs=[cite_run()],
        refusal_runs=[refuse_run()],
        generated_at=PINNED,
    )


def test_cli_emits_manifest_within_root(tmp_path, capsys):
    _write_evidence(tmp_path, **_covered_evidence())
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--emit",
    ])
    assert code == 0
    manifest_path = tmp_path / "specs" / "ground-manifest.json"
    assert manifest_path.exists()
    validate_ground_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))


def test_cli_rejects_manifest_path_escaping_root(tmp_path, capsys):
    _write_evidence(tmp_path, **_covered_evidence())
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--manifest-path", "../escape.json",
        "--emit",
    ])
    assert code == 1
    assert "escapes the project root" in capsys.readouterr().out
    assert not (tmp_path.parent / "escape.json").exists()


def test_cli_rejects_absolute_manifest_path_outside_root(tmp_path):
    outside = tmp_path.parent / "outside.json"
    _write_evidence(tmp_path, **_covered_evidence())
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--manifest-path", str(outside),
        "--emit",
    ])
    assert code == 1
    assert not outside.exists()


def test_cli_reports_unreadable_evidence_cleanly(tmp_path, capsys):
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "does-not-exist.json",
    ])
    assert code == 1
    assert "could not read evidence file" in capsys.readouterr().out


def test_cli_reports_invalid_json_cleanly(tmp_path, capsys):
    (tmp_path / "evidence.json").write_text("{not json", encoding="utf-8")
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
    ])
    assert code == 1
    assert "could not read evidence file" in capsys.readouterr().out


def test_cli_reports_non_object_evidence_cleanly(tmp_path, capsys):
    (tmp_path / "evidence.json").write_text("[1, 2, 3]", encoding="utf-8")
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
    ])
    assert code == 1
    assert "must contain a JSON object" in capsys.readouterr().out


def test_cli_reports_malformed_evidence_and_writes_nothing(tmp_path, capsys):
    _write_evidence(
        tmp_path,
        sources=[source()],
        # malformed ACL run — missing source_id
        acl_runs=[{"principal": "e", "document_ids": ["A"], "expected_entitled": True}],
        generated_at=PINNED,
    )
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--emit",
    ])
    assert code == 1
    assert "error:" in capsys.readouterr().out
    assert not (tmp_path / "specs" / "ground-manifest.json").exists()


def test_cli_emit_preserves_prior_manifest_when_new_evidence_is_malformed(tmp_path):
    manifest_path = tmp_path / "specs" / "ground-manifest.json"
    # First: a valid emit.
    _write_evidence(tmp_path, **_covered_evidence())
    assert ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--emit",
    ]) == 0
    original_bytes = manifest_path.read_bytes()

    # Then: malformed evidence must fail without touching the prior manifest.
    _write_evidence(
        tmp_path,
        sources=[source()],
        acl_runs=[{"principal": "e", "document_ids": ["A"], "expected_entitled": True}],
        generated_at=PINNED,
    )
    assert ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--emit",
    ]) == 1
    assert manifest_path.read_bytes() == original_bytes


def test_cli_json_only_emits_valid_data(tmp_path, capsys):
    _write_evidence(tmp_path, **_covered_evidence())
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--json",
    ])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    validate_ground_manifest(printed)
    assert printed["status"] == "complete"


def test_cli_gate_returns_2_on_must_fix(tmp_path):
    evidence = _covered_evidence()
    evidence["acl_runs"] = [entitled("e", ["doc-1"]), unentitled("u", ["doc-1"])]
    _write_evidence(tmp_path, **evidence)
    code = ground.main([
        "--project-root", str(tmp_path),
        "--evidence-file", "evidence.json",
        "--gate",
    ])
    assert code == 2


# ---------------------------------------------------------------------------
# Requirement 7 — schema parity (hand validator vs. jsonschema Draft-07)
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
        sources=[source(acl_probe_principals=["entitled-analyst", "unentitled-guest"])],
        acl_runs=[
            entitled("entitled-analyst", ["doc-1"], captured_at=PINNED),
            unentitled("unentitled-guest", ["doc-2"], allowed_document_ids=["doc-2"],
                       captured_at=PINNED),
        ],
        citation_runs=[cite_run(citations=["doc-1"], retrieved_ids=["doc-1"],
                                subqueries=2, tokens=50, captured_at=PINNED)],
        refusal_runs=[refuse_run(captured_at=PINNED)],
    )


def test_valid_manifest_accepted_by_both_validators(jsonschema_validator):
    manifest = _rich_ground_manifest()
    validate_ground_manifest(manifest)  # must not raise
    errors = list(jsonschema_validator.iter_errors(manifest))
    assert errors == [], [e.message for e in errors]


def test_multi_source_manifest_with_by_source_detail_accepted_by_both(jsonschema_validator):
    manifest = ground_manifest(
        sources=[source(id="policy-library"), source(id="hr-handbook")],
        acl_runs=[
            entitled("e", ["p1"], source_id="policy-library"),
            unentitled("u", ["p1"], source_id="policy-library"),
            entitled("e", ["h1"], source_id="hr-handbook"),
            unentitled("u", [], source_id="hr-handbook"),
        ],
        citation_runs=[cite_run(source_id="policy-library"), cite_run(source_id="hr-handbook")],
        refusal_runs=[refuse_run(source_id="policy-library"), refuse_run(source_id="hr-handbook")],
    )
    assert by_id(manifest)["GRD-001"]["detail"]["by_source"]  # exercises the map
    validate_ground_manifest(manifest)
    assert jsonschema_validator.is_valid(manifest)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(schema="wrong/v1"),
        lambda m: m["findings"][0].update(status="not-a-status"),
        lambda m: m["findings"][0]["detail"].update(reason="made-up-reason"),
        lambda m: m["findings"][0]["detail"].update(free_form_note="nope"),
        lambda m: m["findings"][0]["detail"].update(by_source={"s": "weird-status"}),
        lambda m: m["findings"][0]["detail"]["leaked_document_ids"].append(""),
        lambda m: m["sources"][0].pop("permission_model"),
        lambda m: m["sources"][0].update(extra_field="nope"),
        lambda m: m["sources"][0]["acl_probe_principals"].append(""),
        lambda m: m["acl_evidence"].append({"principal": "x"}),  # missing keys
        lambda m: m["acl_evidence"][0]["document_ids"].append(""),  # empty id
        lambda m: m["citation_evidence"][0].update(citation_count="not-a-number"),
        lambda m: m["refusal_evidence"][0].update(query_id=123),
        lambda m: m["telemetry"].update(tokens=-1),
        lambda m: m.update(retrieval_quality_baseline="../escape"),
        lambda m: m.update(retrieval_quality_baseline="https://evil.example/x"),
        lambda m: m.pop("retrieval_quality_baseline"),
        lambda m: m.update(unexpected_top_level_key="nope"),
    ],
)
def test_malformed_manifest_rejected_by_both_validators(mutate, jsonschema_validator):
    manifest = _rich_ground_manifest()
    # Ensure the finding chosen for detail mutations carries a detail dict.
    manifest["findings"][0].setdefault("detail", {"reason": "acl-enforced"})
    manifest["findings"][0]["detail"].setdefault("leaked_document_ids", ["doc-1"])
    mutate(manifest)
    with pytest.raises((ManifestValidationError, GroundEvidenceError)):
        validate_ground_manifest(manifest)
    assert not jsonschema_validator.is_valid(manifest), (
        "hand validator rejected this manifest but jsonschema accepted it"
    )
