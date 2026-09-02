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
import stat
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
    residual_risks: Sequence[Dict[str, object]] = (),
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
        residual_risks=tuple(residual_risks),
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
# Two trustworthy instants 48 hours apart -- more than FRESHNESS_VALID_FOR_HOURS
# (24h) -- so the *newest* (captured_at) instant exceeds the *oldest*
# (oldest_source_at) instant plus valid_for_hours, and freshness.status must
# be "expired" (not because the oldest timestamp itself looks stale, but
# because the newest evidence was captured too long after the oldest one).
_OLDEST_TRUSTWORTHY_TIMESTAMP = "2026-01-01T00:00:00Z"
_NEWEST_TRUSTWORTHY_TIMESTAMP_EXPIRED = "2026-01-03T00:00:00Z"


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


def _overclaiming_evidence(evidence_id: str, *, collected_at: Optional[str]) -> contracts.EvidenceRef:
    # Deliberately bypasses ``_evidence()``'s own derivation of
    # freshness_seconds/live_verified from collected_at, to construct the
    # exact adversarial-input shape this module must never trust: a
    # source claiming a fresh, live-verified timestamp even though
    # collected_at itself is missing or unparseable.
    return contracts.EvidenceRef(
        evidence_id=evidence_id,
        kind="static-file-hash",
        source=f"{evidence_id}.json",
        sha256=_sha256_of(evidence_id),
        collected_at=collected_at,
        freshness_seconds=42,
        live_verified=True,
        phase="design",
        repository=_REPOSITORY,
        source_commit=_COMMIT,
        target_environment=None,
        policy_set_sha256=None,
    )


def test_manifest_never_trusts_overclaimed_freshness_for_missing_collected_at():
    # collected_at is the *only* trustworthy signal; freshness_seconds=42
    # and live_verified=True on the input must never leak through just
    # because collected_at happens to be entirely absent.
    findings = [_finding("MED-001", "pass")]
    evidence = [_overclaiming_evidence("EVID-overclaim-missing", collected_at=None)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-overclaim-missing")
    assert entry["collected_at"] is None
    assert entry["freshness_seconds"] is None
    assert entry["live_verified"] is False
    assert manifest["freshness"]["status"] == "stale"


def test_manifest_never_trusts_overclaimed_freshness_for_invalid_collected_at():
    # Same adversarial shape, but collected_at is present and looks like a
    # timestamp yet fails to parse as a real instant -- freshness_seconds
    # and live_verified must degrade exactly as for the missing case.
    findings = [_finding("MED-001", "pass")]
    evidence = [
        _overclaiming_evidence("EVID-overclaim-invalid", collected_at=_MALFORMED_COLLECTED_AT)
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-overclaim-invalid")
    assert entry["collected_at"] is None
    assert entry["freshness_seconds"] is None
    assert entry["live_verified"] is False
    assert manifest["freshness"]["status"] == "stale"


def test_evidence_index_never_shows_overclaimed_live_verified_for_missing_timestamp():
    findings = [_finding("MED-001", "pass")]
    evidence = [_overclaiming_evidence("EVID-overclaim-missing", collected_at=None)]
    result = _base_result(findings=findings, evidence=evidence)
    text = render.render_evidence_pack(result)
    index_start = text.index("## Evidence index")
    index_section = text[index_start : text.index("## Pass/fail matrix")]
    assert "EVID-overclaim-missing" in index_section
    # The index row for this evidence must render the degraded, truthful
    # live_verified=False -- never the overclaimed True the input supplied.
    row = next(line for line in index_section.splitlines() if "EVID-overclaim-missing" in line)
    assert "False" in row
    assert "True" not in row
    assert "42" not in row


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
    # trustworthy timestamp at all -> stale. Expiration is never described
    # as the *oldest* timestamp itself "falling outside a window that
    # starts there" (that framing is circular/false); it must instead say
    # the newest trustworthy instant (captured_at) exceeds the oldest
    # trustworthy instant (oldest_source_at) plus valid_for_hours.
    assert "stale" in description
    assert "expired" in description
    assert "captured_at" in description
    assert "oldest_source_at" in description
    assert "valid_for_hours" in description
    assert "falls outside" not in description


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


def test_residual_risk_live_evidence_freshness_wording_matches_expired_case():
    findings = [_finding("MED-001", "pass")]
    evidence = [
        _evidence("EVID-oldest", collected_at=_OLDEST_TRUSTWORTHY_TIMESTAMP),
        _evidence("EVID-newest", collected_at=_NEWEST_TRUSTWORTHY_TIMESTAMP_EXPIRED),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    # Exercise the real mechanics this wording claims: the newest
    # trustworthy instant (captured_at) is 48 hours after the oldest
    # (oldest_source_at), which exceeds valid_for_hours (24) -- so the
    # manifest is truthfully "expired", not because oldest_source_at
    # itself looks old in isolation.
    assert manifest["captured_at"] == _NEWEST_TRUSTWORTHY_TIMESTAMP_EXPIRED
    assert manifest["freshness"]["oldest_source_at"] == _OLDEST_TRUSTWORTHY_TIMESTAMP
    assert manifest["freshness"]["status"] == "expired"
    description = _live_evidence_freshness_risk(manifest)["description"]
    assert "expired" in description


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


# ---------------------------------------------------------------------------
# Hardening round: preflight rejection (issue 1)
# ---------------------------------------------------------------------------


def test_write_artifacts_rejects_root_as_destination(tmp_path):
    result = _full_result()
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            Path("."),
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    assert not (tmp_path / "docs").exists()


def test_write_artifacts_rejects_existing_directory_at_destination(tmp_path):
    result = _full_result()
    (tmp_path / "tests" / "governed-actions-manifest.json").mkdir(parents=True)
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    # Preflight rejects before any staging: no other artifact was written.
    assert not (tmp_path / "docs").exists()
    assert not (tmp_path / "tests" / "governed-actions-apply-plan.json").exists()


def test_write_artifacts_rejects_existing_fifo_at_destination(tmp_path):
    result = _full_result()
    (tmp_path / "tests").mkdir()
    fifo_path = tmp_path / "tests" / "governed-actions-manifest.json"
    os.mkfifo(fifo_path)
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    # The FIFO itself is left completely untouched.
    assert stat.S_ISFIFO(fifo_path.lstat().st_mode)
    assert not (tmp_path / "docs").exists()


def test_write_artifacts_rejects_duplicate_destinations(tmp_path):
    result = _full_result()
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    assert not (tmp_path / "tests").exists()


def test_write_artifacts_rejects_nested_destinations(tmp_path):
    result = _full_result()
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            Path("tests"),
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    assert not (tmp_path / "tests").exists()
    assert not (tmp_path / "docs").exists()


# ---------------------------------------------------------------------------
# Hardening round: symlink-swap TOCTOU (issue 2)
# ---------------------------------------------------------------------------


def test_write_artifacts_detects_parent_symlink_swap_mid_transaction(tmp_path):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )

    other_findings = [_finding("MED-001", "pass")]
    other_result = _base_result(findings=other_findings, dirty=False)

    tests_dir = manifest_path.parent
    assert tests_dir == apply_plan_path.parent  # both artifacts share this parent
    tests_dir_saved = tests_dir.parent / f"{tests_dir.name}-saved-by-attacker"
    attacker_target = tmp_path.parent / f"{tmp_path.name}-attacker-target"
    attacker_target.mkdir()

    calls = {"count": 0}
    swap_done = {"value": False}

    def _replace(src: Path, dst: Path) -> None:
        calls["count"] += 1
        os.replace(src, dst)
        # Immediately after the manifest's own backup-aside step lands
        # (the very first replace() call), an attacker swaps the shared
        # tests/ directory for a symlink pointing outside the assessed
        # root, before the apply-plan artifact -- which lives under that
        # very same directory -- is ever touched.
        if calls["count"] == 1 and not swap_done["value"]:
            tests_dir.rename(tests_dir_saved)
            tests_dir.symlink_to(attacker_target, target_is_directory=True)
            swap_done["value"] = True

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            other_result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
            replace=_replace,
        )

    # Revalidation immediately before every step must have caught the
    # swap and failed closed -- no artifact bytes were ever written
    # through the symlink into the attacker-controlled directory.
    assert not list(attacker_target.iterdir())


def test_write_artifacts_rejects_destination_that_is_itself_a_symlink(tmp_path):
    # Even when a destination leaf symlink points at an otherwise
    # harmless, in-root regular file, the destination itself is rejected:
    # this assessor only ever replaces a plain file it previously wrote,
    # never a symlink at the destination's own final path component.
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    innocuous_target = manifest_path.parent / "innocuous-regular-file.json"
    innocuous_target.write_bytes(b"{}\n")
    manifest_path.symlink_to(innocuous_target)

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )

    # Rejected before any staging occurred: the destination symlink and
    # the in-root file it points at are both left completely untouched.
    assert manifest_path.is_symlink()
    assert innocuous_target.read_bytes() == b"{}\n"
    assert not evidence_path.exists()
    assert not apply_plan_path.exists()


def test_write_artifacts_held_open_parent_fd_survives_ancestor_symlink_swap(
    tmp_path, monkeypatch
):
    # With no custom ``replace`` injected, every stage/backup/commit step
    # for a destination goes through one verified, no-follow
    # parent-directory fd that is opened once and held open for that
    # destination's entire lifetime in this call -- an fd is bound to the
    # directory's inode, not to a path string, so it stays valid (and
    # keeps operating against the *original* directory) even if something
    # else renames that directory out of the way and replaces its old
    # path with an attacker-controlled symlink partway through the call.
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    tests_dir = manifest_path.parent
    assert tests_dir == apply_plan_path.parent  # both artifacts share this parent

    tests_dir_saved = tests_dir.parent / f"{tests_dir.name}-saved-by-attacker"
    attacker_target = tmp_path.parent / f"{tmp_path.name}-attacker-target"
    attacker_target.mkdir()

    original_stage = render._stage_temp_file
    swap_done = {"value": False}

    def _spy_stage(parent_fd, dest, leaf_name, data):
        staged_name = original_stage(parent_fd, dest, leaf_name, data)
        # Immediately after the *first* artifact is staged (into the
        # already-held-open parent fd), an attacker swaps the shared
        # tests/ ancestor directory for a symlink pointing outside the
        # assessed root, before the remaining artifacts under that same
        # directory are staged, backed up, or committed.
        if not swap_done["value"]:
            tests_dir.rename(tests_dir_saved)
            tests_dir.symlink_to(attacker_target, target_is_directory=True)
            swap_done["value"] = True
        return staged_name

    monkeypatch.setattr(render, "_stage_temp_file", _spy_stage)

    returned = render.write_artifacts(
        tmp_path,
        result,
        render.DEFAULT_MANIFEST_RELATIVE_PATH,
        render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
    )

    # The whole transaction must still succeed, and every byte must have
    # landed in the *original* directory (now sitting at the renamed
    # path) via the held-open fd -- never through the attacker's symlink.
    assert returned == (manifest_path, evidence_path, apply_plan_path)
    assert not list(attacker_target.iterdir())
    restored_manifest = tests_dir_saved / render.DEFAULT_MANIFEST_RELATIVE_PATH.name
    restored_apply_plan = tests_dir_saved / render.DEFAULT_APPLY_PLAN_RELATIVE_PATH.name
    assert restored_manifest.is_file()
    assert restored_apply_plan.is_file()
    manifest = json.loads(restored_manifest.read_text())
    _assert_valid_manifest(manifest)


# ---------------------------------------------------------------------------
# Hardening round: rollback failures are never swallowed (issue 3)
# ---------------------------------------------------------------------------


def test_write_artifacts_rollback_failure_is_not_swallowed(tmp_path, monkeypatch):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
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

    other_findings = [_finding("MED-001", "pass")]
    other_result = _base_result(findings=other_findings, dirty=False)

    original_fsync_dir = render._fsync_dir
    fsync_calls = []

    def _spy_fsync_dir(dest: Path, parent_fd: int) -> None:
        fsync_calls.append(Path(dest).parent)
        original_fsync_dir(dest, parent_fd)

    monkeypatch.setattr(render, "_fsync_dir", _spy_fsync_dir)

    backup_of_manifest: Dict[str, Optional[Path]] = {"path": None}
    calls = {"count": 0}

    def _replace(src: Path, dst: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            # This is the manifest's backup-aside call: replace(dest, backup).
            backup_of_manifest["path"] = Path(dst)
        if calls["count"] == 6:
            # Fail the very last (apply-plan) commit replace, so all three
            # backups already exist and every one of them enters rollback.
            raise OSError("synthetic forward failure on apply-plan commit")
        recorded = backup_of_manifest["path"]
        if recorded is not None and src == recorded and dst == manifest_path:
            # Force the manifest's own backup restoration to fail too, so
            # rollback of the whole transaction cannot fully succeed.
            raise OSError("synthetic restore failure for manifest backup")
        os.replace(src, dst)

    with pytest.raises(render.ArtifactWriteError) as excinfo:
        render.write_artifacts(
            tmp_path,
            other_result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
            replace=_replace,
        )

    message = str(excinfo.value)
    assert "ROLLBACK DID NOT FULLY RESTORE" in message

    # Evidence and apply-plan rollbacks succeeded: restored byte-for-byte.
    assert evidence_path.read_bytes() == prior_evidence_bytes
    assert apply_plan_path.read_bytes() == prior_apply_plan_bytes

    # Manifest's own rollback failed: the newer (partially-applied)
    # content is left live rather than silently discarded, and its
    # backup -- the only remaining copy of the prior content -- is
    # preserved on disk for manual recovery, never deleted.
    assert manifest_path.read_bytes() != prior_manifest_bytes
    backup_path = backup_of_manifest["path"]
    assert backup_path is not None
    assert backup_path.exists(), "backup for the failed restore must be preserved"
    assert backup_path.read_bytes() == prior_manifest_bytes

    # _fsync_dir is still invoked for every artifact whose rollback
    # succeeded, despite the manifest's own rollback failing.
    assert Path(evidence_path.parent) in fsync_calls
    assert Path(apply_plan_path.parent) in fsync_calls


# ---------------------------------------------------------------------------
# Hardening round: staging lives inside cleanup scope (issue 4)
# ---------------------------------------------------------------------------


def test_write_artifacts_cleans_up_earlier_staged_files_when_a_later_stage_fails(
    tmp_path, monkeypatch
):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)

    original_stage = render._stage_temp_file
    staged_locations = []
    call_count = {"value": 0}

    def _spy_stage(parent_fd, dest, leaf_name, data):
        call_count["value"] += 1
        if call_count["value"] == 2:
            raise OSError("synthetic staging failure for the second artifact")
        staged_name = original_stage(parent_fd, dest, leaf_name, data)
        staged_locations.append(dest.parent / staged_name)
        return staged_name

    monkeypatch.setattr(render, "_stage_temp_file", _spy_stage)

    with pytest.raises(render.ArtifactWriteError) as excinfo:
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )

    assert "synthetic staging failure" in str(excinfo.value)
    assert len(staged_locations) == 1  # only the first artifact staged before the failure
    assert not staged_locations[0].exists(), "earlier staged temp file must be cleaned up"
    assert not manifest_path.exists()
    assert not evidence_path.exists()
    assert not apply_plan_path.exists()
    for path in (manifest_path, evidence_path, apply_plan_path):
        if path.parent.exists():
            leftovers = [entry for entry in path.parent.iterdir() if entry.name.startswith(".")]
            assert leftovers == [], f"leftover staging files: {leftovers}"


def test_stage_temp_file_removes_partial_temp_on_write_failure(tmp_path, monkeypatch):
    root_resolved = tmp_path.resolve()
    dest = root_resolved / "tests" / "governed-actions-manifest.json"

    def _failing_fsync(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", _failing_fsync)
    root_fd = os.open(root_resolved, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent_fd, leaf_name = render._open_verified_parent(root_fd, root_resolved, dest)
        try:
            with pytest.raises(render.ArtifactWriteError):
                render._stage_temp_file(parent_fd, dest, leaf_name, b"{}\n")
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)

    tests_dir = root_resolved / "tests"
    assert tests_dir.is_dir()  # created by the fd-based mkdir before the failure
    leftovers = list(tests_dir.glob(".*"))
    assert leftovers == [], f"leftover staging files: {leftovers}"




# ---------------------------------------------------------------------------
# Hardening round: fractional RFC 3339 precision preserved (issue 5)
# ---------------------------------------------------------------------------


def test_freshness_expires_at_preserves_fractional_seconds():
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-frac", collected_at="2026-01-01T00:00:00.123456Z")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    freshness = manifest["freshness"]
    assert freshness["oldest_source_at"] == "2026-01-01T00:00:00.123456Z"
    # 24h (FRESHNESS_VALID_FOR_HOURS) later, fractional precision retained
    # rather than being silently truncated to whole seconds -- so the
    # rendered timestamp can never contradict the freshness computation
    # actually performed against the full-precision instant.
    assert freshness["expires_at"] == "2026-01-02T00:00:00.123456Z"


# ---------------------------------------------------------------------------
# Hardening round: RFC 3339 arbitrary precision is never silently truncated
# into a false-fresh reading (issue 2, 2nd rereview)
# ---------------------------------------------------------------------------


def test_freshness_six_fractional_digits_is_the_supported_precision_boundary():
    # Exactly six digits (RFC 3339's/this module's maximum supported
    # precision) must still parse and be treated as trustworthy -- the
    # degradation below only begins strictly beyond this boundary.
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-6digit", collected_at="2026-01-01T00:00:00.123456Z")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-6digit")
    assert entry["collected_at"] == "2026-01-01T00:00:00.123456Z"
    assert entry["live_verified"] is True


@pytest.mark.parametrize("fractional_digits", [7, 8, 9])
def test_freshness_degrades_collected_at_beyond_six_fractional_digits(fractional_digits):
    # Python's datetime.fromisoformat silently *truncates* (never rejects)
    # fractional-second digits beyond six -- naively parsing one of these
    # values would produce a plausible-looking (but wrong) 6-digit instant
    # and let it through as trustworthy. This module must instead treat a
    # timestamp whose precision it cannot exactly represent as
    # untrustworthy/absent, never a silently truncated false-fresh instant.
    fractional = "1" * fractional_digits
    collected_at = f"2026-01-01T00:00:00.{fractional}Z"
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-boundary", collected_at=collected_at)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-boundary")
    assert entry["collected_at"] is None
    assert entry["freshness_seconds"] is None
    assert entry["live_verified"] is False
    assert manifest["freshness"]["status"] == "stale"
    assert manifest["freshness"]["oldest_source_at"] is None


def test_freshness_mixed_valid_and_over_precision_collected_at_uses_only_valid():
    findings = [_finding("MED-001", "pass")]
    evidence = [
        _evidence("EVID-overprecise", collected_at="2026-01-01T00:00:00.1234567Z"),
        _evidence("EVID-precise", collected_at=_COLLECTED_AT_EARLY),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["oldest_source_at"] == _COLLECTED_AT_EARLY
    overprecise_entry = next(
        e for e in manifest["evidence"] if e["evidence_id"] == "EVID-overprecise"
    )
    precise_entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-precise")
    assert overprecise_entry["collected_at"] is None
    assert overprecise_entry["live_verified"] is False
    assert precise_entry["collected_at"] == _COLLECTED_AT_EARLY


# ---------------------------------------------------------------------------
# Hardening round: production schema validation uses FormatChecker (issue 6)
# ---------------------------------------------------------------------------


def test_write_artifacts_rejects_impossible_date_in_conformance_report(tmp_path):
    result = _full_result(
        conformance_reports=(
            {
                "report_id": "REPORT-001",
                "tool": "governed-actions-ctk",
                "version": "0.1.0",
                # Shape-valid (matches the timestamp regex) but calendar-
                # impossible -- only a format-aware validator catches this.
                "generated_at": _MALFORMED_COLLECTED_AT,
                "summary": "conformance test kit run",
                "evidence_refs": ("EVID-report-001",),
            },
        ),
    )
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)
    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )
    assert not manifest_path.exists()
    assert not evidence_path.exists()
    assert not apply_plan_path.exists()


# ---------------------------------------------------------------------------
# Hardening round: set-like nested fields are sorted (issue 7)
# ---------------------------------------------------------------------------


def _action_with_sets(action_id: str, **overrides) -> contracts.ActionRecord:
    defaults = dict(
        action_id=action_id,
        display_name=f"Action {action_id}",
        aliases=(),
        owner=None,
        declaration_refs=(),
        implementation_refs=(),
        input_schema_sha256=_sha256_of(f"{action_id}-input-schema"),
        output_schema_sha256=_sha256_of(f"{action_id}-output-schema"),
        source="declared",
        consequence="write",
        secondary_consequences=(),
        reversible=False,
        compensation_ref=None,
        execution_modes=(),
        provider_hosted=False,
        approval_required=None,
        policy_ids=(),
        known_runtime_paths=(),
        inventory_status="pass",
    )
    defaults.update(overrides)
    return contracts.ActionRecord(**defaults)


def test_manifest_action_set_like_fields_sorted_and_order_independent():
    forward = dict(
        aliases=("beta", "alpha", "gamma"),
        secondary_consequences=("write", "external-egress"),
        execution_modes=("interactive", "direct-tool", "batch"),
        policy_ids=("policy-b", "policy-a"),
        known_runtime_paths=("path/b.py", "path/a.py"),
        declaration_refs=("src/b.py", "src/a.py"),
        implementation_refs=("src/b_impl.py", "src/a_impl.py"),
    )
    reordered = dict(
        aliases=("gamma", "alpha", "beta"),
        secondary_consequences=("external-egress", "write"),
        execution_modes=("batch", "interactive", "direct-tool"),
        policy_ids=("policy-a", "policy-b"),
        known_runtime_paths=("path/a.py", "path/b.py"),
        declaration_refs=("src/a.py", "src/b.py"),
        implementation_refs=("src/a_impl.py", "src/b_impl.py"),
    )
    findings = [_finding("MED-001", "pass")]
    result_forward = _base_result(actions=[_action_with_sets("act-1", **forward)], findings=findings)
    result_reordered = _base_result(
        actions=[_action_with_sets("act-1", **reordered)], findings=findings
    )
    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    rendered = manifest_forward["action_inventory"][0]
    assert rendered["aliases"] == sorted(rendered["aliases"])
    assert rendered["execution_modes"] == sorted(rendered["execution_modes"])
    assert rendered["policy_ids"] == sorted(rendered["policy_ids"])
    assert rendered["known_runtime_paths"] == sorted(rendered["known_runtime_paths"])
    assert rendered["declaration_refs"] == sorted(rendered["declaration_refs"])
    assert rendered["implementation_refs"] == sorted(rendered["implementation_refs"])
    assert rendered["secondary_consequences"] == sorted(rendered["secondary_consequences"])


def test_manifest_path_evidence_refs_sorted_and_nodes_stay_ordered():
    action = _action("act-1")
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-a"), _evidence("EVID-b")]

    def _make_path(evidence_refs: Tuple[str, ...]) -> contracts.PathRecord:
        return contracts.PathRecord(
            path_id="path-1",
            action_id="act-1",
            mode="runtime",
            nodes=("entrypoint", "handler"),
            pre_action_seam="hook:pre",
            equivalent_control_ref=None,
            covered=True,
            status="pass",
            evidence_refs=evidence_refs,
        )

    result_forward = _base_result(
        actions=[action],
        paths=[_make_path(("EVID-b", "EVID-a"))],
        findings=findings,
        evidence=evidence,
    )
    result_reordered = _base_result(
        actions=[action],
        paths=[_make_path(("EVID-a", "EVID-b"))],
        findings=findings,
        evidence=evidence,
    )
    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    rendered_path = manifest_forward["mediation_paths"][0]
    assert rendered_path["evidence_refs"] == ["EVID-a", "EVID-b"]
    # nodes is an ORDERED path chain and must never be sorted.
    assert rendered_path["nodes"] == ["entrypoint", "handler"]


def test_manifest_probe_evidence_refs_sorted_and_order_independent():
    action = _action("act-1")
    path = _path("path-1", "act-1")
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-a"), _evidence("EVID-b"), _evidence(f"EVID-{path.path_id}")]

    def _make_probe(evidence_refs: Tuple[str, ...]) -> contracts.ProbeResult:
        return contracts.ProbeResult(
            probe_id="probe-1",
            action_id="act-1",
            path_id="path-1",
            status="pass",
            reason_code="ok",
            expected="e",
            observed="o",
            evidence_refs=evidence_refs,
        )

    result_forward = _base_result(
        actions=[action],
        paths=[path],
        probes=[_make_probe(("EVID-b", "EVID-a"))],
        findings=findings,
        evidence=evidence,
    )
    result_reordered = _base_result(
        actions=[action],
        paths=[path],
        probes=[_make_probe(("EVID-a", "EVID-b"))],
        findings=findings,
        evidence=evidence,
    )
    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    rendered_probe = manifest_forward["conformance"]["application_probes"][0]
    assert rendered_probe["evidence_refs"] == ["EVID-a", "EVID-b"]


# ---------------------------------------------------------------------------
# Hardening round: canonical tie-breaker for tied primary sort keys (issue 3,
# 2nd rereview) -- two records that share the exact same *primary* sort key
# but differ in other fields must still resolve to the same final order (and
# therefore byte-identical manifests) no matter which order they appear in
# the unsorted input, because each ``_sorted_*`` helper now appends a
# canonical-bytes secondary key after its documented primary key.
# ---------------------------------------------------------------------------


def test_manifest_actions_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    action_a = _action("act-tied", owner="team-a")
    action_b = _action("act-tied", owner="team-b")

    result_forward = _base_result(actions=[action_a, action_b], findings=findings)
    result_reordered = _base_result(actions=[action_b, action_a], findings=findings)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["action_inventory"]) == 2


def test_manifest_paths_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    action = _action("act-1")

    def _tied_path(status: str) -> contracts.PathRecord:
        return contracts.PathRecord(
            path_id="path-tied",
            action_id="act-1",
            mode="runtime",
            nodes=("entrypoint", "handler"),
            pre_action_seam="hook:pre",
            equivalent_control_ref=None,
            covered=True,
            status=status,
            evidence_refs=(),
        )

    path_a = _tied_path("pass")
    path_b = _tied_path("not-applicable")

    result_forward = _base_result(actions=[action], paths=[path_a, path_b], findings=findings)
    result_reordered = _base_result(actions=[action], paths=[path_b, path_a], findings=findings)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["mediation_paths"]) == 2


def test_manifest_probes_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    action = _action("act-1")
    path = _path("path-1", "act-1")

    probe_a = _probe("probe-tied", "act-1", "path-1", observed="observed-a")
    probe_b = _probe("probe-tied", "act-1", "path-1", observed="observed-b")

    result_forward = _base_result(
        actions=[action], paths=[path], probes=[probe_a, probe_b], findings=findings
    )
    result_reordered = _base_result(
        actions=[action], paths=[path], probes=[probe_b, probe_a], findings=findings
    )

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["conformance"]["application_probes"]) == 2


def test_manifest_evidence_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]

    def _tied_evidence(source: str) -> contracts.EvidenceRef:
        return contracts.EvidenceRef(
            evidence_id="EVID-tied",
            kind="static-file-hash",
            source=source,
            sha256=_sha256_of(source),
            collected_at=_COLLECTED_AT_EARLY,
            freshness_seconds=0,
            live_verified=True,
            phase="design",
            repository=_REPOSITORY,
            source_commit=_COMMIT,
            target_environment=None,
            policy_set_sha256=None,
        )

    evidence_a = _tied_evidence("a-source.json")
    evidence_b = _tied_evidence("b-source.json")

    result_forward = _base_result(findings=findings, evidence=[evidence_a, evidence_b])
    result_reordered = _base_result(findings=findings, evidence=[evidence_b, evidence_a])

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["evidence"]) == 2


def test_manifest_findings_with_tied_primary_key_still_order_independent():
    def _tied_finding(summary: str) -> contracts.Finding:
        return contracts.Finding(
            finding_id="MED-001",
            status="pass",
            phase="design",
            plane="runtime",
            reason_code="reason",
            summary=summary,
            details="details",
            affected_actions=(),
            affected_paths=(),
            evidence_refs=(),
            remediation_ids=(),
            residual_risk_ref=None,
        )

    finding_a = _tied_finding("summary A")
    finding_b = _tied_finding("summary B")

    result_forward = _base_result(findings=[finding_a, finding_b])
    result_reordered = _base_result(findings=[finding_b, finding_a])

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["findings"]) == 2


# ---------------------------------------------------------------------------
# Hardening round: residual-risk id collisions are rejected (issue 8)
# ---------------------------------------------------------------------------


def test_build_manifest_rejects_residual_risk_collision_with_mandatory_id():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(
        findings=findings,
        residual_risks=(
            {
                "residual_risk_id": "RISK-LIVE-EVIDENCE-FRESHNESS",
                "finding_id": "MED-001",
                "description": "a customer tried to overwrite this module's own trust disclaimer",
            },
        ),
    )
    with pytest.raises(render.ReservedResidualRiskIdError):
        render.build_manifest(result)


def test_build_manifest_rejects_residual_risk_collision_with_catch_all_id():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(
        findings=findings,
        residual_risks=(
            {
                "residual_risk_id": "RISK-BOUNDED-ASSESSMENT-SCOPE",
                "finding_id": "MED-001",
                "description": "attempted catch-all overwrite",
            },
        ),
    )
    with pytest.raises(render.ReservedResidualRiskIdError):
        render.build_manifest(result)


def test_build_manifest_rejects_duplicate_extra_residual_risk_ids():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(
        findings=findings,
        residual_risks=(
            {"residual_risk_id": "RISK-CUSTOM-001", "finding_id": "MED-001", "description": "first"},
            {
                "residual_risk_id": "RISK-CUSTOM-001",
                "finding_id": "MED-001",
                "description": "second, duplicate id",
            },
        ),
    )
    with pytest.raises(render.ReservedResidualRiskIdError):
        render.build_manifest(result)


def test_build_manifest_accepts_non_colliding_customer_residual_risk():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(
        findings=findings,
        residual_risks=(
            {
                "residual_risk_id": "RISK-CUSTOM-001",
                "finding_id": "MED-001",
                "description": "a genuinely customer-specific risk",
            },
        ),
    )
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    ids = {entry["residual_risk_id"] for entry in manifest["residual_risks"]}
    assert "RISK-CUSTOM-001" in ids


# ---------------------------------------------------------------------------
# Hardening round: context-aware Markdown escaping (issue 9)
# ---------------------------------------------------------------------------

_HOSTILE_TEXT = "pwn|ed\ninjected `code` <b>bold</b>"


def test_evidence_pack_matrix_escapes_hostile_finding_summary():
    findings = [
        contracts.Finding(
            finding_id="MED-001",
            status="pass",
            phase="design",
            plane="runtime",
            reason_code="reason",
            summary=_HOSTILE_TEXT,
            details="details",
            affected_actions=(),
            affected_paths=(),
            evidence_refs=(),
            remediation_ids=(),
            residual_risk_ref=None,
        )
    ]
    result = _base_result(findings=findings)
    text = render.render_evidence_pack(result)
    matrix_start = text.index("## Pass/fail matrix")
    matrix_section = text[matrix_start : text.index("## Residual-risk register")]
    table_lines = [line for line in matrix_section.splitlines() if line.startswith("|")]
    # Exactly header + divider + one data row: a hostile summary must
    # never inject an extra pipe-delimited column or a forged extra row
    # via an embedded newline.
    assert len(table_lines) == 3
    # Column-delimiter pipes only, excluding any backslash-escaped literal
    # ``|`` that came from the hostile content itself.
    delimiter_pipes = table_lines[2].replace("\\|", "").count("|")
    assert delimiter_pipes == 8  # 7 columns => 8 pipe delimiters
    assert "\ninjected" not in text
    assert "<b>bold</b>" not in text
    assert "&lt;b&gt;bold&lt;/b&gt;" in text
    assert "pwn\\|ed" in text


def test_evidence_pack_action_inventory_escapes_hostile_display_name():
    action = _action_with_sets("act-1", display_name=_HOSTILE_TEXT)
    findings = [_finding("MED-001", "pass")]
    result = _base_result(actions=[action], findings=findings)
    text = render.render_evidence_pack(result)
    inventory_start = text.index("## Runtime action inventory")
    inventory_section = text[inventory_start : text.index("## Runtime mediation graph")]
    assert "\ninjected" not in inventory_section
    assert "<b>bold</b>" not in inventory_section
    assert "&lt;b&gt;bold&lt;/b&gt;" in inventory_section
    assert "pwn\\|ed" in inventory_section


def test_evidence_pack_residual_risk_register_escapes_hostile_description():
    findings = [_finding("MED-001", "pass")]
    result = _base_result(
        findings=findings,
        residual_risks=(
            {
                "residual_risk_id": "RISK-CUSTOM-002",
                "finding_id": "MED-001",
                "description": _HOSTILE_TEXT,
            },
        ),
    )
    text = render.render_evidence_pack(result)
    register_start = text.index("## Residual-risk register")
    register_section = text[register_start : text.index("## Remediation plan")]
    assert "\ninjected" not in register_section
    assert "<b>bold</b>" not in register_section
    assert "&lt;b&gt;bold&lt;/b&gt;" in register_section
    assert "pwn\\|ed" in register_section


def test_evidence_pack_remediation_plan_escapes_hostile_owner():
    action = _action_with_sets("act-1", owner=_HOSTILE_TEXT)
    findings = [_finding("ACT-001", "must-fix", affected_actions=("act-1",))]
    result = _base_result(actions=[action], findings=findings)
    text = render.render_evidence_pack(result)
    plan_start = text.index("## Remediation plan")
    plan_section = text[plan_start:]
    assert "\ninjected" not in plan_section
    assert "<b>bold</b>" not in plan_section
    assert "&lt;b&gt;bold&lt;/b&gt;" in plan_section
    assert "pwn\\|ed" in plan_section


# ---------------------------------------------------------------------------
# Hardening round: broader Markdown neutralization -- active images, links,
# autolinks, and raw HTML must all be defeated, not just table delimiters
# (issue 4, 2nd rereview)
# ---------------------------------------------------------------------------

_HOSTILE_INLINE_LINK = "[click me](javascript:alert(1))"
_HOSTILE_IMAGE = "![alt text](javascript:alert(1))"
_HOSTILE_REFERENCE_LINK = "[click me][evil]"
_HOSTILE_ANGLE_AUTOLINK = "<https://evil.example/steal>"
_HOSTILE_BARE_AUTOLINK = "visit https://evil.example/steal now"
_HOSTILE_WWW_AUTOLINK = "visit www.evil.example now"


def _escaping_finding(summary: str) -> Sequence[contracts.Finding]:
    return [
        contracts.Finding(
            finding_id="MED-001",
            status="pass",
            phase="design",
            plane="runtime",
            reason_code="reason",
            summary=summary,
            details="details",
            affected_actions=(),
            affected_paths=(),
            evidence_refs=(),
            remediation_ids=(),
            residual_risk_ref=None,
        )
    ]


@pytest.mark.parametrize(
    "hostile_text",
    [
        _HOSTILE_INLINE_LINK,
        _HOSTILE_IMAGE,
        _HOSTILE_REFERENCE_LINK,
        _HOSTILE_ANGLE_AUTOLINK,
        _HOSTILE_BARE_AUTOLINK,
        _HOSTILE_WWW_AUTOLINK,
    ],
)
def test_evidence_pack_neutralizes_active_markdown_link_and_image_forms(hostile_text):
    result = _base_result(findings=_escaping_finding(hostile_text))
    text = render.render_evidence_pack(result)
    matrix_start = text.index("## Pass/fail matrix")
    matrix_section = text[matrix_start : text.index("## Residual-risk register")]
    # No unescaped ``](`` (inline-link/image trigger), unescaped ``][``
    # (reference-link trigger), or a live angle-bracket autolink can
    # survive -- every one of these forms requires literal, unescaped
    # syntax to be recognized by a CommonMark/GFM renderer.
    assert "](" not in matrix_section
    assert "][" not in matrix_section
    assert "<https://" not in matrix_section
    assert "<http://" not in matrix_section
    # GitHub Flavored Markdown's extended autolink extension can also turn
    # a bare "scheme://" or "www." run into a live link with no brackets
    # at all -- the literal trigger substrings must not survive either.
    assert "https://evil.example" not in matrix_section
    assert "http://evil.example" not in matrix_section
    assert "www.evil.example" not in matrix_section
    # The value must still be readable, safe literal text rather than
    # being dropped or replaced with a placeholder.
    assert "evil.example" in matrix_section or "click me" in matrix_section or "alt text" in matrix_section


def test_evidence_pack_neutralizes_javascript_url_inline_link():
    result = _base_result(findings=_escaping_finding(_HOSTILE_INLINE_LINK))
    text = render.render_evidence_pack(result)
    assert "[click me](javascript:alert(1))" not in text
    assert "](" not in text  # the live-link trigger substring never survives
    assert "click me" in text  # visible text remains readable


def test_evidence_pack_neutralizes_javascript_url_image():
    result = _base_result(findings=_escaping_finding(_HOSTILE_IMAGE))
    text = render.render_evidence_pack(result)
    assert "![alt text](javascript:alert(1))" not in text
    assert "alt text" in text  # visible text remains readable


def test_evidence_pack_neutralizes_reference_style_link():
    result = _base_result(findings=_escaping_finding(_HOSTILE_REFERENCE_LINK))
    text = render.render_evidence_pack(result)
    assert "[click me][evil]" not in text
    assert "click me" in text


def test_evidence_pack_neutralizes_angle_bracket_autolink():
    result = _base_result(findings=_escaping_finding(_HOSTILE_ANGLE_AUTOLINK))
    text = render.render_evidence_pack(result)
    assert "<https://evil.example/steal>" not in text
    assert "evil.example" in text

