"""Tests for threadlight-governed-actions artifact rendering (Task 9, design
sections 12-15).

Exercises ``render.build_manifest``, ``render.render_evidence_pack``,
``render.build_apply_plan``, and ``render.write_artifacts`` against complete,
hand-built :class:`contracts.AssessmentResult` fixtures. ``jsonschema`` is a
hard dependency here, not optional: schema conformance is the whole point of
these tests, matching ``tests/test_contracts.py``'s own convention.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q
    python3 -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q \
        -k 'manifest or evidence_pack or rendering or remediation'
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

import jsonschema
import pytest

import canonical
import contracts
import render


REFERENCES = Path(__file__).resolve().parent.parent / "references"

_REPOSITORY = "aiappsgbb/threadlight-skills"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_COLLECTED_AT_EARLY = "2026-01-01T00:00:00Z"
_COLLECTED_AT_LATE = "2026-01-01T06:00:00Z"


def _manifest_schema() -> Dict[str, object]:
    return json.loads((REFERENCES / "governed-actions-manifest.schema.json").read_text())


def _apply_plan_schema() -> Dict[str, object]:
    return json.loads((REFERENCES / "governed-actions-apply-plan.schema.json").read_text())


_FORMAT_CHECKER = jsonschema.FormatChecker()


def _validator_for(schema: Dict[str, object]) -> jsonschema.Draft202012Validator:
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _assert_valid_manifest(manifest: Dict[str, object]) -> None:
    errors = list(_validator_for(_manifest_schema()).iter_errors(manifest))
    assert not errors, "\n".join(str(error) for error in errors)


def _assert_valid_apply_plan(plan: Dict[str, object]) -> None:
    errors = list(_validator_for(_apply_plan_schema()).iter_errors(plan))
    assert not errors, "\n".join(str(error) for error in errors)


def _sha256_of(text: str) -> str:
    return f"sha256:{canonical.sha256_hex(text.encode('utf-8'))}"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _source(dirty: bool = False) -> contracts.SourceRef:
    return contracts.SourceRef(repository=_REPOSITORY, commit=_COMMIT, dirty=dirty)


def _action(
    action_id: str,
    *,
    owner: Optional[str] = None,
    consequence: contracts.Consequence = "write",
) -> contracts.ActionRecord:
    return contracts.ActionRecord(
        action_id=action_id,
        display_name=f"Action {action_id}",
        aliases=(),
        owner=owner,
        declaration_refs=(f"src/{action_id}.py",),
        implementation_refs=(f"src/{action_id}_impl.py",),
        input_schema_sha256=_sha256_of(f"{action_id}-input-schema"),
        output_schema_sha256=_sha256_of(f"{action_id}-output-schema"),
        source="declared",
        consequence=consequence,
        secondary_consequences=(),
        reversible=False,
        compensation_ref=None,
        execution_modes=("direct-tool",),
        provider_hosted=False,
        approval_required=None,
        inventory_status="pass",
    )


def _path(path_id: str, action_id: str, *, mode: str = "runtime") -> contracts.PathRecord:
    return contracts.PathRecord(
        path_id=path_id,
        action_id=action_id,
        mode=mode,
        nodes=("entrypoint", "handler"),
        pre_action_seam="hook:pre",
        equivalent_control_ref=None,
        covered=True,
        status="pass",
        evidence_refs=(f"EVID-{path_id}",),
    )


def _probe(
    probe_id: str,
    action_id: str,
    path_id: str,
    *,
    expected: str = "expected-value",
    observed: str = "observed-value",
) -> contracts.ProbeResult:
    return contracts.ProbeResult(
        probe_id=probe_id,
        action_id=action_id,
        path_id=path_id,
        status="pass",
        reason_code="probe-ok",
        expected=expected,
        observed=observed,
        evidence_refs=(f"EVID-{probe_id}",),
    )


def _evidence(
    evidence_id: str,
    *,
    collected_at: Optional[str] = _COLLECTED_AT_EARLY,
    kind: str = "static-file-hash",
) -> contracts.EvidenceRef:
    return contracts.EvidenceRef(
        evidence_id=evidence_id,
        kind=kind,
        source=f"{evidence_id}.json",
        sha256=_sha256_of(evidence_id),
        collected_at=collected_at,
        freshness_seconds=0 if collected_at else None,
        live_verified=bool(collected_at),
        phase="design",
        repository=_REPOSITORY,
        source_commit=_COMMIT,
        target_environment=None,
        policy_set_sha256=None,
    )


def _finding(
    finding_id: str,
    status: contracts.Status,
    *,
    plane: str = "runtime",
    reason_code: str = "reason",
    affected_actions: Tuple[str, ...] = (),
    affected_paths: Tuple[str, ...] = (),
    evidence_refs: Tuple[str, ...] = (),
    residual_risk_ref: Optional[str] = None,
) -> contracts.Finding:
    return contracts.Finding(
        finding_id=finding_id,
        status=status,
        phase="design",
        plane=plane,
        reason_code=reason_code,
        summary=f"summary for {finding_id}",
        details=f"details for {finding_id}",
        affected_actions=affected_actions,
        affected_paths=affected_paths,
        evidence_refs=evidence_refs,
        remediation_ids=(),
        residual_risk_ref=residual_risk_ref,
    )


def _base_result(
    *,
    actions: Sequence[contracts.ActionRecord] = (),
    paths: Sequence[contracts.PathRecord] = (),
    probes: Sequence[contracts.ProbeResult] = (),
    findings: Sequence[contracts.Finding] = (),
    evidence: Sequence[contracts.EvidenceRef] = (),
    dirty: bool = False,
) -> contracts.AssessmentResult:
    return contracts.AssessmentResult(
        source=_source(dirty=dirty),
        actions=tuple(actions),
        paths=tuple(paths),
        probes=tuple(probes),
        findings=tuple(findings),
        evidence=tuple(evidence),
        policy_hashes=(
            {"path": "governance/policy.json", "sha256": _sha256_of("policy")},
        ),
        pins={
            "dependencies": ({"name": "agent-framework", "version": "1.2.3"},),
            "specifications": ({"name": "governed-actions-spec", "version": "1.0.0"},),
            "probe_suite": {"name": "governed-actions-probe-suite", "version": "0.1.0"},
        },
        conformance_claims=(
            {
                "claim_id": "CLAIM-001",
                "description": "all consequential actions are mediated",
                "status": "pass",
                "evidence_refs": ("EVID-claim-001",),
            },
        ),
        conformance_reports=(
            {
                "report_id": "REPORT-001",
                "tool": "governed-actions-ctk",
                "version": "0.1.0",
                "generated_at": _COLLECTED_AT_EARLY,
                "summary": "conformance test kit run",
                "evidence_refs": ("EVID-report-001",),
            },
        ),
        change_plane={
            "repository": _REPOSITORY,
            "workflows": (
                {"path": ".github/workflows/deploy.yml", "sha256": _sha256_of("deploy.yml")},
            ),
            "identities": ({"identity": "deploy-identity", "kind": "managed-identity"},),
        },
        residual_risks=(),
    )


def _full_result(**overrides) -> contracts.AssessmentResult:
    """A complete, schema-representative assessment: two actions, two
    paths, two probes, one finding per non-passing status plus one pass,
    and evidence both with and without a collection timestamp."""
    actions = [
        _action("send-email", owner="team-comms"),
        _action("delete-record", owner=None),
    ]
    paths = [
        _path("path-send-email", "send-email"),
        _path("path-delete-record", "delete-record"),
    ]
    probes = [
        _probe("probe-send-email", "send-email", "path-send-email"),
        _probe(
            "probe-delete-record",
            "delete-record",
            "path-delete-record",
            expected="SENTINEL-EXPECTED-PAYLOAD",
            observed="SENTINEL-OBSERVED-PAYLOAD",
        ),
    ]
    findings = [
        _finding(
            "ACT-001",
            "must-fix",
            affected_actions=("delete-record",),
            affected_paths=("path-delete-record",),
            evidence_refs=("EVID-finding-act-001",),
        ),
        _finding(
            "MED-003",
            "should-fix",
            affected_actions=("delete-record",),
            evidence_refs=("EVID-finding-med-003",),
        ),
        _finding(
            "GHCP-002",
            "not-verified",
            plane="change",
            affected_actions=(),
            evidence_refs=("EVID-finding-ghcp-002",),
        ),
        _finding(
            "MED-001",
            "pass",
            affected_actions=("send-email",),
            affected_paths=("path-send-email",),
            evidence_refs=("EVID-finding-med-001",),
        ),
        _finding(
            "OPS-001",
            "not-applicable",
            evidence_refs=(),
        ),
    ]
    evidence = [
        _evidence("EVID-finding-act-001", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-finding-med-003", collected_at=_COLLECTED_AT_LATE),
        _evidence("EVID-finding-ghcp-002", collected_at=None),
        _evidence("EVID-finding-med-001", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-path-send-email", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-path-delete-record", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-probe-send-email", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-probe-delete-record", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-claim-001", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-report-001", collected_at=_COLLECTED_AT_EARLY),
    ]
    result = _base_result(
        actions=actions,
        paths=paths,
        probes=probes,
        findings=findings,
        evidence=evidence,
    )
    if overrides:
        result = contracts.AssessmentResult(**{**result.__dict__, **overrides})
    return result


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------


def test_manifest_schema_assessor_and_source_exact():
    manifest = render.build_manifest(_full_result())
    _assert_valid_manifest(manifest)
    assert manifest["schema"] == "threadlight-governed-actions-manifest/v1"
    assert manifest["assessor"] == {
        "name": "threadlight-governed-actions",
        "version": "0.1.0",
        "adapter": "maf/v1",
    }
    assert manifest["source"]["repository"] == _REPOSITORY
    assert manifest["source"]["commit"] == _COMMIT
    assert len(manifest["source"]["commit"]) == 40
    assert manifest["source"]["dirty"] is False


def test_manifest_every_evidence_ref_has_sha256():
    manifest = render.build_manifest(_full_result())
    assert manifest["evidence"], "fixture must supply at least one evidence entry"
    for entry in manifest["evidence"]:
        assert entry["sha256"].startswith("sha256:")
        assert len(entry["sha256"]) == len("sha256:") + 64


def test_manifest_residual_risks_nonempty_and_findings_reference_valid_ids():
    manifest = render.build_manifest(_full_result())
    assert manifest["residual_risks"], "residual_risks must never be empty"
    risk_ids = {risk["residual_risk_id"] for risk in manifest["residual_risks"]}
    for finding in manifest["findings"]:
        assert finding["residual_risk_ref"] in risk_ids


def test_manifest_residual_risks_cover_mandatory_categories():
    manifest = render.build_manifest(_full_result())
    descriptions = " ".join(risk["description"] for risk in manifest["residual_risks"]).lower()
    for keyword in (
        "cooperative",
        "certification",
        "provider-hosted",
        "freshness",
        "alpha",
        "retention",
    ):
        assert keyword in descriptions


def test_manifest_pins_never_carry_sha256():
    manifest = render.build_manifest(_full_result())
    for pin in manifest["pins"]["dependencies"] + manifest["pins"]["specifications"]:
        assert set(pin.keys()) == {"name", "version"}
    assert set(manifest["pins"]["probe_suite"].keys()) == {"name", "version"}


def test_manifest_summary_ungoverned_when_must_fix_present():
    manifest = render.build_manifest(_full_result())
    assert manifest["summary"]["verdict"] == "ungoverned"
    assert "ACT-001" in manifest["summary"]["must_fix"]


def test_manifest_summary_partial_when_only_should_fix_or_not_verified():
    findings = [
        _finding("MED-003", "should-fix", evidence_refs=("EVID-x",)),
        _finding("MED-001", "pass"),
    ]
    result = _base_result(
        findings=findings,
        evidence=[_evidence("EVID-x", collected_at=_COLLECTED_AT_EARLY)],
    )
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_governed_when_everything_passes_and_clean():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(findings=findings, dirty=False)
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] == "governed"


def test_manifest_dirty_source_prevents_governed_without_new_finding():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(findings=findings, dirty=True)
    manifest = render.build_manifest(result)
    assert manifest["summary"]["verdict"] != "governed"
    # No finding beyond what the assessment itself supplied was invented.
    assert [f["finding_id"] for f in manifest["findings"]] == ["MED-001"]


# ---------------------------------------------------------------------------
# render_evidence_pack
# ---------------------------------------------------------------------------

_REQUIRED_HEADINGS = (
    "# Governance Evidence Pack",
    "## Scope and trust model",
    "## Architecture and data flow",
    "## Runtime action inventory",
    "## Runtime mediation graph",
    "## Application-path probe evidence",
    "## GitHub Copilot change plane",
    "## Pass/fail matrix",
    "## Residual-risk register",
    "## Remediation plan",
)


def test_evidence_pack_has_required_customer_sections_in_order():
    text = render.render_evidence_pack(_full_result())
    positions = [text.index(heading) for heading in _REQUIRED_HEADINGS]
    assert positions == sorted(positions)


def test_evidence_pack_matrix_columns_exact():
    text = render.render_evidence_pack(_full_result())
    assert "| ID | Plane | Control | Status | Reason | Evidence | Remediation |" in text


def test_evidence_pack_never_contains_probe_payload_values():
    text = render.render_evidence_pack(_full_result())
    assert "SENTINEL-EXPECTED-PAYLOAD" not in text
    assert "SENTINEL-OBSERVED-PAYLOAD" not in text


def test_evidence_pack_never_contains_banned_payload_keys_as_json():
    # The pack is Markdown, not JSON, but must still never smuggle a raw
    # payload-shaped blob anywhere in its text.
    text = render.render_evidence_pack(_full_result())
    for banned in ("\"secret\"", "\"password\"", "\"api_key\"", "\"token\""):
        assert banned not in text


# ---------------------------------------------------------------------------
# Rendering is order-independent
# ---------------------------------------------------------------------------


def _shuffled(result: contracts.AssessmentResult, seed: int) -> contracts.AssessmentResult:
    rng = random.Random(seed)
    actions = list(result.actions)
    paths = list(result.paths)
    probes = list(result.probes)
    findings = list(result.findings)
    evidence = list(result.evidence)
    rng.shuffle(actions)
    rng.shuffle(paths)
    rng.shuffle(probes)
    rng.shuffle(findings)
    rng.shuffle(evidence)
    return contracts.AssessmentResult(
        source=result.source,
        actions=tuple(actions),
        paths=tuple(paths),
        probes=tuple(probes),
        findings=tuple(findings),
        evidence=tuple(evidence),
        policy_hashes=tuple(result.policy_hashes),
        pins=result.pins,
        conformance_claims=tuple(result.conformance_claims),
        conformance_reports=tuple(result.conformance_reports),
        change_plane=result.change_plane,
        residual_risks=tuple(result.residual_risks),
    )


def test_rendering_is_independent_of_input_ordering():
    base = _full_result()
    baseline_bytes = canonical.canonical_bytes(render.build_manifest(base))
    for seed in (1, 2, 3, 4, 5):
        shuffled = _shuffled(base, seed)
        shuffled_bytes = canonical.canonical_bytes(render.build_manifest(shuffled))
        assert shuffled_bytes == baseline_bytes


def test_apply_plan_is_independent_of_input_ordering():
    base = _full_result()
    baseline_bytes = canonical.canonical_bytes(render.build_apply_plan(base))
    for seed in (10, 20, 30):
        shuffled = _shuffled(base, seed)
        shuffled_bytes = canonical.canonical_bytes(render.build_apply_plan(shuffled))
        assert shuffled_bytes == baseline_bytes


# ---------------------------------------------------------------------------
# build_apply_plan
# ---------------------------------------------------------------------------


def test_apply_plan_schema_and_manifest_hash_binding():
    result = _full_result()
    manifest = render.build_manifest(result)
    plan = render.build_apply_plan(result)
    _assert_valid_apply_plan(plan)
    recomputed = f"sha256:{canonical.sha256_hex(canonical.canonical_bytes(manifest))}"
    assert plan["manifest_sha256"] == recomputed
    assert plan["self_applying"] is False
    assert plan["source_commit"] == _COMMIT


def test_apply_plan_creates_exactly_one_item_per_remediable_finding():
    result = _full_result()
    plan = render.build_apply_plan(result)
    remediable_ids = {"ACT-001", "MED-003", "GHCP-002"}
    plan_ids = [item["finding_id"] for item in plan["items"]]
    assert sorted(plan_ids) == sorted(remediable_ids)
    assert len(plan_ids) == len(set(plan_ids))  # exactly one each, never duplicated


def test_apply_plan_excludes_pass_and_not_applicable_findings():
    result = _full_result()
    plan = render.build_apply_plan(result)
    plan_ids = {item["finding_id"] for item in plan["items"]}
    assert "MED-001" not in plan_ids  # pass
    assert "OPS-001" not in plan_ids  # not-applicable


def test_apply_plan_unknown_customer_policy_is_manual_with_null_owner():
    # MED-003 is affected only by "delete-record", which declares no owner
    # in this fixture -- an unknown customer policy/ownership must never be
    # guessed.
    result = _full_result()
    plan = render.build_apply_plan(result)
    med_003 = next(item for item in plan["items"] if item["finding_id"] == "MED-003")
    assert med_003["remediation_kind"] == "manual"
    assert med_003["owner"] is None


def test_apply_plan_never_invents_an_owner_when_declared():
    result = _full_result()
    plan = render.build_apply_plan(result)
    act_001 = next(item for item in plan["items"] if item["finding_id"] == "ACT-001")
    # ACT-001 only affects "delete-record" (owner=None in fixture) so no
    # owner should be invented for it either.
    assert act_001["owner"] is None


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_freshness_valid_for_hours_is_24():
    manifest = render.build_manifest(_full_result())
    assert manifest["freshness"]["valid_for_hours"] == 24


def test_freshness_derives_oldest_source_at_and_expires_at():
    manifest = render.build_manifest(_full_result())
    freshness = manifest["freshness"]
    assert freshness["oldest_source_at"] == _COLLECTED_AT_EARLY
    assert freshness["expires_at"] is not None


def test_freshness_no_trustworthy_timestamp_is_not_fresh():
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-untimed", collected_at=None)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["freshness"]["oldest_source_at"] is None


# Two RFC 3339 strings whose *lexical* order disagrees with their actual
# chronological order: "A" names an earlier UTC instant (2025-12-31T22:30:00Z)
# than "B" (2025-12-31T23:00:00Z), yet the plain strings sort the other way
# round ("2025-12-31T23:00:00Z" < "2026-01-01T00:30:00+02:00" lexically).
_INSTANT_EARLIER_BUT_LEXICALLY_LATER = "2026-01-01T00:30:00+02:00"  # == 2025-12-31T22:30:00Z
_INSTANT_LATER_BUT_LEXICALLY_EARLIER = "2025-12-31T23:00:00Z"  # == 2025-12-31T23:00:00Z
# A schema-``pattern``-valid but calendar-impossible timestamp: no such date
# as February 30th exists, so ``datetime.fromisoformat`` raises ``ValueError``.
_MALFORMED_COLLECTED_AT = "2026-02-30T10:00:00Z"


def test_freshness_orders_by_actual_instant_not_lexical_string():
    findings = [_finding("MED-001", "pass")]
    evidence = [
        _evidence("EVID-a", collected_at=_INSTANT_EARLIER_BUT_LEXICALLY_LATER),
        _evidence("EVID-b", collected_at=_INSTANT_LATER_BUT_LEXICALLY_EARLIER),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    # The chronologically *earliest* instant is EVID-a's timestamp, even
    # though its string sorts lexically after EVID-b's -- oldest_source_at
    # must reflect the real instant, not the raw string order.
    assert manifest["freshness"]["oldest_source_at"] == _INSTANT_EARLIER_BUT_LEXICALLY_LATER
    # The chronologically *latest* instant is EVID-b's timestamp -- captured_at
    # must likewise reflect the real instant, not the raw string order.
    assert manifest["captured_at"] == _INSTANT_LATER_BUT_LEXICALLY_EARLIER


def test_freshness_instant_ordering_is_independent_of_evidence_list_order():
    findings = [_finding("MED-001", "pass")]
    forward = _base_result(
        findings=findings,
        evidence=[
            _evidence("EVID-a", collected_at=_INSTANT_EARLIER_BUT_LEXICALLY_LATER),
            _evidence("EVID-b", collected_at=_INSTANT_LATER_BUT_LEXICALLY_EARLIER),
        ],
    )
    reversed_ = _base_result(
        findings=findings,
        evidence=[
            _evidence("EVID-b", collected_at=_INSTANT_LATER_BUT_LEXICALLY_EARLIER),
            _evidence("EVID-a", collected_at=_INSTANT_EARLIER_BUT_LEXICALLY_LATER),
        ],
    )
    manifest_forward = render.build_manifest(forward)
    manifest_reversed = render.build_manifest(reversed_)
    assert manifest_forward["freshness"] == manifest_reversed["freshness"]
    assert manifest_forward["captured_at"] == manifest_reversed["captured_at"]


def test_manifest_tolerates_malformed_collected_at_without_raising():
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT)]
    result = _base_result(findings=findings, evidence=evidence)
    # Must not raise ValueError (or any other exception) building the manifest.
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # The malformed timestamp is untrustworthy for freshness purposes, so it
    # is excluded exactly as if collected_at had been absent/None.
    assert manifest["freshness"]["status"] == "stale"
    assert manifest["freshness"]["oldest_source_at"] is None
    assert manifest["freshness"]["expires_at"] is None
    assert manifest["captured_at"] == render.FALLBACK_TIMESTAMP
    # A calendar-impossible collected_at can never validate against the
    # manifest schema's timestamp format, so it is degraded to the
    # schema's own "absent" representation (None) -- never raw garbage,
    # and never a fabricated/normalized real timestamp either.
    evidence_entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-bad")
    assert evidence_entry["collected_at"] is None
    assert evidence_entry["freshness_seconds"] is None
    assert evidence_entry["live_verified"] is False


def test_manifest_mixed_valid_and_invalid_collected_at_uses_only_valid():
    findings = [_finding("MED-001", "pass")]
    evidence = [
        _evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT),
        _evidence("EVID-good", collected_at=_COLLECTED_AT_EARLY),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] != "stale"
    assert manifest["freshness"]["oldest_source_at"] == _COLLECTED_AT_EARLY
    assert manifest["captured_at"] == _COLLECTED_AT_EARLY
    bad_entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-bad")
    good_entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-good")
    assert bad_entry["collected_at"] is None
    assert good_entry["collected_at"] == _COLLECTED_AT_EARLY


def _live_evidence_freshness_risk(manifest: Dict[str, object]) -> Dict[str, object]:
    return next(
        risk
        for risk in manifest["residual_risks"]
        if risk["residual_risk_id"] == "RISK-LIVE-EVIDENCE-FRESHNESS"
    )


def test_residual_risk_live_evidence_freshness_wording_is_precise():
    description = _live_evidence_freshness_risk(render.build_manifest(_full_result()))["description"]
    # The freshness status enum is strictly fresh/stale/expired -- this
    # description must never claim a status the schema cannot emit.
    assert "not-verified" not in description
    # Precisely names the per-entry degradation (never overclaims a
    # single vague "not fresh" outcome): a missing/invalid collected_at
    # becomes collected_at=null, live_verified=false, and is excluded from
    # the freshness computation entirely.
    assert "collected_at" in description
    assert "null" in description
    assert "live_verified" in description
    assert "false" in description
    assert "excluded" in description
    # Precisely distinguishes the two possible non-fresh outcomes: no
    # trustworthy timestamp at all -> stale; a trustworthy timestamp that
    # falls outside the freshness window -> expired.
    assert "stale" in description
    assert "expired" in description


def test_residual_risk_live_evidence_freshness_wording_matches_stale_case():
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    assert manifest["freshness"]["status"] == "stale"
    # The description's claims are exercised, not just asserted in the
    # abstract: this fixture's only evidence has an invalid collected_at,
    # is excluded from freshness, and the manifest is truthfully "stale".
    description = _live_evidence_freshness_risk(manifest)["description"]
    assert "stale" in description


# ---------------------------------------------------------------------------
# write_artifacts
# ---------------------------------------------------------------------------


def _artifact_paths(root: Path) -> Tuple[Path, Path, Path]:
    return (
        root / render.DEFAULT_MANIFEST_RELATIVE_PATH,
        root / render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        root / render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )


def test_write_artifacts_creates_default_paths_under_root(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    returned = render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )
    assert returned == (manifest_path, evidence_path, apply_plan_path)
    assert manifest_path.is_file()
    assert evidence_path.is_file()
    assert apply_plan_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    _assert_valid_manifest(manifest)
    plan = json.loads(apply_plan_path.read_text())
    _assert_valid_apply_plan(plan)


def test_write_artifacts_rejects_absolute_destination(tmp_path):
    result = _full_result()
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            Path("/etc/passwd"),
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )


def test_write_artifacts_rejects_traversal_escape(tmp_path):
    result = _full_result()
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            Path("../escape.json"),
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )


def test_write_artifacts_rejects_symlink_escape(tmp_path):
    result = _full_result()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "tests").symlink_to(outside, target_is_directory=True)
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            root,
            result,
            Path("tests/governed-actions-manifest.json"),
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    assert not list(outside.iterdir())


def _make_failing_replace(fail_on_call: int) -> Callable[[Path, Path], None]:
    calls = {"count": 0}

    def _replace(src: Path, dst: Path) -> None:
        calls["count"] += 1
        if calls["count"] == fail_on_call:
            raise OSError(f"synthetic failure on call {calls['count']}")
        os.replace(src, dst)

    return _replace


def test_write_artifacts_transaction_restores_prior_set_on_replace_failure(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)

    # Seed a complete, valid prior artifact set.
    render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )
    prior_manifest_bytes = manifest_path.read_bytes()
    prior_evidence_bytes = evidence_path.read_bytes()
    prior_apply_plan_bytes = apply_plan_path.read_bytes()

    # A second, different assessment (different finding set) that would
    # produce different artifact bytes if the write succeeded.
    other_findings = [_finding("MED-001", "pass")]
    other_result = _base_result(findings=other_findings, dirty=False)

    # Fail on the 2nd replace() call inside the commit phase (each of the 3
    # backup-aside calls and 3 commit calls goes through the same
    # injectable replace, so failing partway through must still leave the
    # complete original set behind byte-for-byte).
    failing_replace = _make_failing_replace(fail_on_call=5)

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            other_result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
            replace=failing_replace,
        )

    assert manifest_path.read_bytes() == prior_manifest_bytes
    assert evidence_path.read_bytes() == prior_evidence_bytes
    assert apply_plan_path.read_bytes() == prior_apply_plan_bytes

    # No leftover backup/temp files should remain in any artifact directory.
    for path in (manifest_path, evidence_path, apply_plan_path):
        leftovers = [
            entry
            for entry in path.parent.iterdir()
            if entry != path and entry.name.startswith(f".{path.name}.")
        ]
        assert leftovers == [], f"leftover staging files: {leftovers}"


def test_write_artifacts_transaction_restores_on_every_failing_call_index(tmp_path):
    # Exhaustively try failing on each of the 6 replace() invocations (3
    # backup-aside + 3 commit) to prove restoration is correct regardless
    # of which phase the failure lands in.
    result = _full_result()
    other_findings = [_finding("MED-001", "pass")]
    other_result = _base_result(findings=other_findings, dirty=False)

    for fail_on_call in range(1, 7):
        case_root = tmp_path / f"case-{fail_on_call}"
        case_root.mkdir()
        manifest_path, evidence_path, apply_plan_path = _artifact_paths(case_root)
        render.write_artifacts(
            case_root,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
        prior_bytes = {
            path: path.read_bytes() for path in (manifest_path, evidence_path, apply_plan_path)
        }

        failing_replace = _make_failing_replace(fail_on_call=fail_on_call)
        with pytest.raises(render.ArtifactWriteError):
            render.write_artifacts(
                case_root,
                other_result,
                render.DEFAULT_MANIFEST_RELATIVE_PATH,
                render.DEFAULT_EVIDENCE_RELATIVE_PATH,
                render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
                replace=failing_replace,
            )

        for path, expected in prior_bytes.items():
            assert path.read_bytes() == expected, (
                f"case fail_on_call={fail_on_call}: {path} was not restored"
            )


def test_write_artifacts_no_prior_artifacts_leaves_nothing_on_failure(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    failing_replace = _make_failing_replace(fail_on_call=3)

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
            replace=failing_replace,
        )

    assert not manifest_path.exists()
    assert not evidence_path.exists()
    assert not apply_plan_path.exists()


def test_write_artifacts_never_writes_probe_payload_values(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )
    evidence_text = evidence_path.read_text()
    assert "SENTINEL-EXPECTED-PAYLOAD" not in evidence_text
    assert "SENTINEL-OBSERVED-PAYLOAD" not in evidence_text


def test_write_artifacts_tolerates_malformed_collected_at_without_raising(tmp_path):
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    # Must complete the full three-artifact transaction without raising
    # ArtifactWriteError (or any other exception): a calendar-impossible
    # (but regex-pattern-valid) collected_at is degraded to an absent
    # timestamp before schema validation, so the write always succeeds.
    render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )
    assert manifest_path.exists()
    assert evidence_path.exists()
    assert apply_plan_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["freshness"]["status"] == "stale"


def test_write_artifacts_json_files_end_with_exactly_one_trailing_newline(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )
    manifest = render.build_manifest(result)
    apply_plan = render.build_apply_plan(result)
    # The on-disk bytes must be exactly the newline-free canonical JSON
    # bytes plus a single trailing b"\n" -- never zero, never two or more.
    manifest_bytes = manifest_path.read_bytes()
    apply_plan_bytes = apply_plan_path.read_bytes()
    assert manifest_bytes == canonical.canonical_bytes(manifest) + b"\n"
    assert apply_plan_bytes == canonical.canonical_bytes(apply_plan) + b"\n"
    assert manifest_bytes.count(b"\n") == 1
    assert apply_plan_bytes.count(b"\n") == 1
    assert not manifest_bytes.endswith(b"\n\n")
    assert not apply_plan_bytes.endswith(b"\n\n")


def test_write_artifacts_trailing_newline_does_not_change_manifest_sha256(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )
    manifest_bytes_with_newline = manifest_path.read_bytes()
    assert manifest_bytes_with_newline.endswith(b"\n")
    # The hash bound into the apply plan is over the canonical (newline-
    # free) manifest bytes -- stripping the single trailing newline this
    # function appended at emission time must recover exactly that value.
    canonical_manifest_bytes = manifest_bytes_with_newline[:-1]
    recomputed_hash = f"sha256:{canonical.sha256_hex(canonical_manifest_bytes)}"
    apply_plan_bytes_with_newline = apply_plan_path.read_bytes()
    assert apply_plan_bytes_with_newline.endswith(b"\n")
    apply_plan = json.loads(apply_plan_bytes_with_newline[:-1])
    assert apply_plan["manifest_sha256"] == recomputed_hash
    # Also equals the in-memory manifest_sha256 computed straight from
    # build_manifest/build_apply_plan (no round trip through disk at all).
    assert apply_plan["manifest_sha256"] == render.build_apply_plan(result)["manifest_sha256"]


# ---------------------------------------------------------------------------
# Evidence index
# ---------------------------------------------------------------------------


def test_evidence_pack_has_evidence_index_heading_in_required_order():
    text = render.render_evidence_pack(_full_result())
    # The eleventh heading must sit logically before the pass/fail matrix
    # (whose own Evidence column cites these same IDs) without disturbing
    # the required order of the ten mandatory headings around it.
    headings_with_index = _REQUIRED_HEADINGS[:7] + ("## Evidence index",) + _REQUIRED_HEADINGS[7:]
    positions = [text.index(heading) for heading in headings_with_index]
    assert positions == sorted(positions)
    # All ten originally-required headings remain, in their original order.
    original_positions = [text.index(heading) for heading in _REQUIRED_HEADINGS]
    assert original_positions == sorted(original_positions)


def test_evidence_pack_evidence_index_lists_every_evidence_id_sorted():
    result = _full_result()
    manifest = render.build_manifest(result)
    text = render.render_evidence_pack(result)
    index_start = text.index("## Evidence index")
    index_section = text[index_start : text.index("## Pass/fail matrix")]
    expected_ids = sorted(entry["evidence_id"] for entry in manifest["evidence"])
    assert expected_ids  # sanity: the fixture has evidence
    found_ids = [line for line in expected_ids if line in index_section]
    assert found_ids == expected_ids
    # Row order in the rendered section must match manifest's sorted order.
    positions = [index_section.index(evidence_id) for evidence_id in expected_ids]
    assert positions == sorted(positions)


def test_evidence_pack_evidence_index_has_only_the_five_allowed_fields():
    result = _full_result()
    manifest = render.build_manifest(result)
    text = render.render_evidence_pack(result)
    index_start = text.index("## Evidence index")
    index_section = text[index_start : text.index("## Pass/fail matrix")]
    for entry in manifest["evidence"]:
        assert entry["kind"] in index_section
        assert entry["source"] in index_section
        assert entry["sha256"] in index_section
        if entry["collected_at"] is not None:
            assert entry["collected_at"] in index_section
    # Fields excluded from the customer-facing index must never appear
    # anywhere in the evidence-index section (no repository/source_commit/
    # phase/target_environment/policy_set_sha256/freshness_seconds).
    assert _REPOSITORY not in index_section
    assert _COMMIT not in index_section
    assert "design" not in index_section  # phase value, never rendered here
    assert "freshness_seconds" not in index_section
    assert "policy_set_sha256" not in index_section
    assert "target_environment" not in index_section


def test_evidence_pack_evidence_index_never_contains_probe_payload_values():
    text = render.render_evidence_pack(_full_result())
    index_start = text.index("## Evidence index")
    index_section = text[index_start : text.index("## Pass/fail matrix")]
    assert "SENTINEL-EXPECTED-PAYLOAD" not in index_section
    assert "SENTINEL-OBSERVED-PAYLOAD" not in index_section

