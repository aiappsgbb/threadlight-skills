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

import dataclasses
import json
import os
import random
import stat
import subprocess
import textwrap
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import jsonschema
import pytest

import canonical
import contracts
import governed_actions
import render

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - environment without markdown-it-py
    MarkdownIt = None


def _iter_all_tokens(tokens):
    """Yield every token in *tokens*, recursing into each token's
    ``children`` (inline tokens are only ever exposed this way)."""
    for token in tokens:
        yield token
        if token.children:
            yield from _iter_all_tokens(token.children)


def _parse_with_commonmark(markdown_text: str):
    """Parse *markdown_text* with a genuine CommonMark-compliant parser
    (``markdown-it-py``'s ``commonmark`` preset, with GFM's ``table`` and
    ``strikethrough`` extensions enabled) and return its full, flattened
    token stream, so a test can assert directly against the parser's own
    AST rather than merely against substring presence in the raw
    Markdown source.

    ``linkify`` -- markdown-it-py's closest analog to GFM's *extended
    autolink* extension -- is deliberately left disabled: the
    ``linkify-it-py`` package it requires is not installed in this
    environment, and installing it would add a new dependency this
    project does not otherwise declare or require. This does not weaken
    what these tests actually prove, though. The property under test --
    that a CommonMark code span's content is a hard AST boundary that no
    later positional inline rule (any autolink/linkify extension
    included) ever re-scans, and that text on either side of a code span
    is never stitched back together into one contiguous run for such a
    rule to match against -- is a *structural* guarantee of the
    CommonMark code-span algorithm itself, not something specific to
    ``linkify`` or to GFM's own autolink extension. It is reproducible,
    and is reproduced here, using only the ``table``/``strikethrough``
    extensions: this project's bare-URL/``www.``/email neutralization is
    proven correct by showing the wrapped trigger substring becomes a
    dedicated ``code_inline`` token -- never left merged into a plain
    ``text`` token, which is the only kind of token any positional
    autolink/linkify rule (this engine's or GFM's) ever scans.
    """
    parser = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    return list(_iter_all_tokens(parser.parse(markdown_text)))


def _matrix_markdown_table_source(text: str) -> str:
    """Extract just the raw ``| ... |`` table lines of the rendered
    Pass/fail matrix section, so they can be fed to a real Markdown
    parser as a syntactically complete, standalone table."""
    start = text.index("## Pass/fail matrix")
    end = text.index("## Residual-risk register")
    lines = text[start:end].splitlines()
    return "\n".join(line for line in lines if line.startswith("|"))


REFERENCES = Path(__file__).resolve().parent.parent / "references"

_REPOSITORY = "aiappsgbb/threadlight-skills"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_COLLECTED_AT_EARLY = "2026-01-01T00:00:00Z"
_COLLECTED_AT_LATE = "2026-01-01T06:00:00Z"
# The deterministic, fixed assessment-capture instant every test fixture
# uses unless it explicitly overrides ``captured_at`` -- this is never
# derived from evidence, so it is chosen independently of
# ``_COLLECTED_AT_EARLY``/``_COLLECTED_AT_LATE`` while still landing
# within one freshness window (24h) of them, so existing fixtures that
# expect an overall "fresh" verdict continue to get one without any
# fixture needing to be touched purely because of this new field's
# introduction.
_CAPTURED_AT_DEFAULT = _COLLECTED_AT_EARLY


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
    repository: str = _REPOSITORY,
    source_commit: str = _COMMIT,
    phase: str = "design",
    policy_set_sha256: Optional[str] = None,
) -> contracts.EvidenceRef:
    return contracts.EvidenceRef(
        evidence_id=evidence_id,
        kind=kind,
        source=f"{evidence_id}.json",
        sha256=_sha256_of(evidence_id),
        collected_at=collected_at,
        freshness_seconds=0 if collected_at else None,
        live_verified=bool(collected_at),
        phase=phase,
        repository=repository,
        source_commit=source_commit,
        target_environment=None,
        policy_set_sha256=policy_set_sha256,
    )


def _finding(
    finding_id: str,
    status: contracts.Status,
    *,
    plane: str = "runtime",
    phase: str = "design",
    reason_code: str = "reason",
    affected_actions: Tuple[str, ...] = (),
    affected_paths: Tuple[str, ...] = (),
    evidence_refs: Tuple[str, ...] = (),
    residual_risk_ref: Optional[str] = None,
) -> contracts.Finding:
    return contracts.Finding(
        finding_id=finding_id,
        status=status,
        phase=phase,
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
    captured_at: Optional[str] = _CAPTURED_AT_DEFAULT,
    phase: Optional[str] = None,
    live_github_selected: bool = False,
    live_azure_selected: bool = False,
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
        captured_at=captured_at,
        phase=phase,
        live_github_selected=live_github_selected,
        live_azure_selected=live_azure_selected,
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


def test_manifest_never_contains_probe_payload_values():
    # ``build_manifest``'s in-memory dict (and therefore the JSON bytes
    # ultimately written to disk) must never carry a probe's raw
    # ``expected``/``observed`` comparison payload -- only a stable
    # sha256 digest of each, so the manifest can still prove which
    # expected/observed pair a probe's status was decided against without
    # ever smuggling the payload value itself.
    manifest = render.build_manifest(_full_result())
    serialized = json.dumps(manifest)
    assert "SENTINEL-EXPECTED-PAYLOAD" not in serialized
    assert "SENTINEL-OBSERVED-PAYLOAD" not in serialized
    probes = manifest["conformance"]["application_probes"]
    delete_record_probe = next(p for p in probes if p["probe_id"] == "probe-delete-record")
    assert delete_record_probe["expected_sha256"] == _sha256_of("SENTINEL-EXPECTED-PAYLOAD")
    assert delete_record_probe["observed_sha256"] == _sha256_of("SENTINEL-OBSERVED-PAYLOAD")
    assert "expected" not in delete_record_probe
    assert "observed" not in delete_record_probe


def test_apply_plan_never_contains_probe_payload_values():
    apply_plan = render.build_apply_plan(_full_result())
    serialized = json.dumps(apply_plan)
    assert "SENTINEL-EXPECTED-PAYLOAD" not in serialized
    assert "SENTINEL-OBSERVED-PAYLOAD" not in serialized


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
        captured_at=result.captured_at,
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
#
# Freshness is a claim about the assessment's own trusted capture instant
# (``result.captured_at`` -- never derived from evidence, never the newest
# evidence timestamp this manifest happens to carry) measured against the
# oldest trustworthy timestamp among only the evidence a finding or
# application-path probe actually *requires* (``_required_evidence_ids``) --
# never unrelated evidence nothing here needed.
# ---------------------------------------------------------------------------


def test_freshness_valid_for_hours_is_24():
    manifest = render.build_manifest(_full_result())
    assert manifest["freshness"]["valid_for_hours"] == 24


def test_freshness_derives_oldest_source_at_from_required_evidence():
    manifest = render.build_manifest(_full_result())
    freshness = manifest["freshness"]
    assert freshness["oldest_source_at"] == _COLLECTED_AT_EARLY
    assert freshness["expires_at"] is not None


def test_freshness_no_trustworthy_required_timestamp_is_not_fresh():
    evidence = [_evidence("EVID-untimed", collected_at=None)]
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-untimed",))]
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
# (24h) -- so an assessment *captured* at the later instant, whose own
# required evidence was only collected as of the earlier one, must report
# "expired" (not because the oldest timestamp itself looks stale, but
# because the assessment's own capture instant is too long after the
# oldest evidence it actually required).
_OLDEST_TRUSTWORTHY_TIMESTAMP = "2026-01-01T00:00:00Z"
_CAPTURED_AT_EXPIRED = "2026-01-03T00:00:00Z"  # 48h after _OLDEST_TRUSTWORTHY_TIMESTAMP


def test_freshness_oldest_source_at_orders_by_actual_instant_not_lexical_string():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-a", "EVID-b"))]
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


def test_freshness_oldest_source_at_is_independent_of_evidence_list_order():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-a", "EVID-b"))]
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


def test_manifest_tolerates_malformed_required_collected_at_without_raising():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-bad",))]
    evidence = [_evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT)]
    result = _base_result(findings=findings, evidence=evidence)
    # Must not raise ValueError (or any other exception) building the manifest.
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # The malformed timestamp is untrustworthy for freshness purposes, so it
    # is excluded exactly as if collected_at had been absent/None; since
    # this finding's own required evidence is untrustworthy, the overall
    # verdict is truthfully "stale".
    assert manifest["freshness"]["status"] == "stale"
    assert manifest["freshness"]["oldest_source_at"] is None
    assert manifest["freshness"]["expires_at"] is None
    # A calendar-impossible collected_at can never validate against the
    # manifest schema's timestamp format, so it is degraded to the
    # schema's own "absent" representation (None) -- never raw garbage,
    # and never a fabricated/normalized real timestamp either.
    evidence_entry = next(e for e in manifest["evidence"] if e["evidence_id"] == "EVID-bad")
    assert evidence_entry["collected_at"] is None
    assert evidence_entry["freshness_seconds"] is None
    assert evidence_entry["live_verified"] is False


def test_manifest_required_evidence_mixed_valid_and_invalid_uses_only_valid_for_oldest():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-bad", "EVID-good"))]
    evidence = [
        _evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT),
        _evidence("EVID-good", collected_at=_COLLECTED_AT_EARLY),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # The invalid entry is excluded from the oldest-instant computation
    # entirely; the valid one still anchors oldest_source_at.
    assert manifest["freshness"]["oldest_source_at"] == _COLLECTED_AT_EARLY
    # But this finding's own required evidence set still contains one
    # entry (EVID-bad) that cannot be trusted, so the overall verdict is
    # truthfully "stale" even though a window could otherwise be computed.
    assert manifest["freshness"]["status"] == "stale"
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
    findings = [
        _finding("MED-001", "pass", evidence_refs=("EVID-overclaim-missing",))
    ]
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
    findings = [
        _finding("MED-001", "pass", evidence_refs=("EVID-overclaim-invalid",))
    ]
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
    # Precisely distinguishes the two possible non-fresh outcomes: no
    # trustworthy timestamp at all -> stale. Expiration is never described
    # as the *oldest* timestamp itself "falling outside a window that
    # starts there" (that framing is circular/false); it must instead say
    # the assessment's own trusted capture instant (captured_at) exceeds
    # the oldest trustworthy required-evidence instant (oldest_source_at)
    # plus valid_for_hours.
    assert "stale" in description
    assert "expired" in description
    assert "captured_at" in description
    assert "oldest_source_at" in description
    assert "valid_for_hours" in description
    assert "falls outside" not in description


def test_residual_risk_live_evidence_freshness_wording_matches_stale_case():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-bad",))]
    evidence = [_evidence("EVID-bad", collected_at=_MALFORMED_COLLECTED_AT)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    assert manifest["freshness"]["status"] == "stale"
    # The description's claims are exercised, not just asserted in the
    # abstract: this fixture's only required evidence has an invalid
    # collected_at, is excluded from freshness, and the manifest is
    # truthfully "stale".
    description = _live_evidence_freshness_risk(manifest)["description"]
    assert "stale" in description


def test_residual_risk_live_evidence_freshness_wording_matches_expired_case():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-oldest",))]
    evidence = [_evidence("EVID-oldest", collected_at=_OLDEST_TRUSTWORTHY_TIMESTAMP)]
    result = _base_result(findings=findings, evidence=evidence, captured_at=_CAPTURED_AT_EXPIRED)
    manifest = render.build_manifest(result)
    # Exercise the real mechanics this wording claims: the assessment's
    # own trusted capture instant (captured_at) is 48 hours after the
    # oldest trustworthy required-evidence instant (oldest_source_at),
    # which exceeds valid_for_hours (24) -- so the manifest is truthfully
    # "expired", not because oldest_source_at itself looks old in
    # isolation.
    assert manifest["captured_at"] == _CAPTURED_AT_EXPIRED
    assert manifest["freshness"]["oldest_source_at"] == _OLDEST_TRUSTWORTHY_TIMESTAMP
    assert manifest["freshness"]["status"] == "expired"
    description = _live_evidence_freshness_risk(manifest)["description"]
    assert "expired" in description


# ---------------------------------------------------------------------------
# Hardening round: a finding/probe's own *required* evidence must never be
# masked by unrelated, incidental evidence that happens to be fresh (issue 1)
# ---------------------------------------------------------------------------


def test_freshness_required_finding_evidence_invalid_is_not_masked_by_unrelated_fresh_evidence():
    findings = [
        _finding(
            "GHCP-002",
            "not-verified",
            plane="change",
            evidence_refs=("EVID-required",),
        )
    ]
    evidence = [
        _evidence("EVID-required", collected_at=None),
        _evidence("EVID-unrelated-fresh", collected_at=_COLLECTED_AT_EARLY),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # The unrelated evidence entry alone carries a perfectly trustworthy
    # timestamp and would otherwise make the whole manifest look "fresh"
    # -- but the one finding actually citing evidence never got a
    # trustworthy timestamp for it, so the overall freshness must never
    # claim "fresh" on the strength of evidence nothing here needed.
    assert manifest["freshness"]["status"] != "fresh"
    # Never invents a status the schema's fixed vocabulary does not have.
    assert manifest["freshness"]["status"] in ("stale", "expired")


def test_freshness_required_finding_evidence_dangling_reference_is_not_masked_by_unrelated_fresh_evidence():
    findings = [
        _finding(
            "GHCP-002",
            "not-verified",
            plane="change",
            evidence_refs=("EVID-does-not-exist",),
        )
    ]
    evidence = [_evidence("EVID-unrelated-fresh", collected_at=_COLLECTED_AT_EARLY)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # The required reference does not even resolve to an evidence entry
    # at all -- this must be treated exactly as untrustworthy as a
    # present-but-invalid timestamp would be, never silently ignored.
    assert manifest["freshness"]["status"] != "fresh"


def test_freshness_required_probe_evidence_invalid_is_not_masked_by_unrelated_fresh_evidence():
    findings = [_finding("MED-001", "pass")]
    action = _action("act-1")
    path = _path("path-1", "act-1")
    probe = contracts.ProbeResult(
        probe_id="probe-1",
        action_id="act-1",
        path_id="path-1",
        status="pass",
        reason_code="probe-ok",
        expected="expected-value",
        observed="observed-value",
        evidence_refs=("EVID-required",),
    )
    evidence = [
        _evidence("EVID-required", collected_at=None),
        _evidence("EVID-unrelated-fresh", collected_at=_COLLECTED_AT_EARLY),
    ]
    result = _base_result(
        actions=[action], paths=[path], probes=[probe], findings=findings, evidence=evidence
    )
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # An application-path probe's own required evidence is exactly as
    # authoritative here as a finding's -- an unrelated fresh evidence
    # entry must not mask an untrustworthy one a probe actually needed.
    assert manifest["freshness"]["status"] != "fresh"


def test_freshness_required_evidence_trustworthy_ignores_unrelated_invalid_evidence():
    findings = [_finding("GHCP-002", "pass", evidence_refs=("EVID-required",))]
    evidence = [
        _evidence("EVID-required", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-unrelated-invalid", collected_at=None),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # Control case: every required reference IS trustworthy, so an
    # unrelated evidence entry's own untrustworthy timestamp must never
    # drag the overall freshness down -- this guard is one-directional,
    # never a blanket "any invalid evidence anywhere" rule.
    assert manifest["freshness"]["status"] == "fresh"


def test_freshness_with_no_findings_or_probes_has_no_required_evidence_to_prove_freshness():
    # No finding or probe references any evidence at all: there is
    # nothing this manifest can point to as proof of freshness, so the
    # conservative, schema-compatible verdict is "stale" -- never a
    # fabricated "fresh" just because unrelated evidence this manifest
    # merely happens to carry looks fresh in isolation.
    findings = [_finding("MED-001", "pass")]
    evidence = [_evidence("EVID-unrelated-fresh", collected_at=_COLLECTED_AT_EARLY)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "stale"
    assert manifest["freshness"]["oldest_source_at"] is None


# ---------------------------------------------------------------------------
# Hardening round: required evidence must also be *bound* to this
# assessment's own identity -- repository, commit, a future-relative-to-
# capture timestamp, an unambiguous finding phase, and (when declared) the
# assessment's own canonical policy-set digest -- never merely a parseable
# timestamp. Any such mismatch is exactly as untrustworthy as a missing/
# unparseable ``collected_at``, and can never yield a ``governed`` summary
# verdict (issue 2, "close renderer trust gaps" round).
# ---------------------------------------------------------------------------


def test_manifest_summary_partial_when_required_evidence_repository_mismatches_source():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-foreign",))]
    evidence = [_evidence("EVID-foreign", repository="someone-else/other-repo")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    # Foreign-repository evidence can never be trusted for *this*
    # assessment, so freshness degrades and the verdict is truthfully
    # "partial" -- never a fabricated "governed" on the strength of
    # evidence that does not even belong to this repository.
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_partial_when_required_evidence_commit_mismatches_source():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-wrong-commit",))]
    evidence = [_evidence("EVID-wrong-commit", source_commit="f" * 40)]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_partial_when_required_evidence_timestamp_is_in_the_future():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-future",))]
    # _CAPTURED_AT_DEFAULT (== _COLLECTED_AT_EARLY) is "2026-01-01T00:00:00Z";
    # this evidence claims to have been collected an hour *after* the
    # assessment's own trusted capture instant, which can never be a real
    # collection event relative to that capture.
    evidence = [_evidence("EVID-future", collected_at="2026-01-01T01:00:00Z")]
    result = _base_result(findings=findings, evidence=evidence, captured_at=_CAPTURED_AT_DEFAULT)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_partial_when_required_evidence_phase_mismatches_referencing_finding():
    findings = [
        _finding("MED-001", "pass", phase="post-deploy", evidence_refs=("EVID-wrong-phase",))
    ]
    evidence = [_evidence("EVID-wrong-phase", phase="design")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_partial_when_required_evidence_phase_matches_none_of_multiple_citers():
    # Quality review: two findings with distinct phases both cite the
    # same evidence entry, whose own phase matches *neither* of them --
    # this must not launder into "governed" just because more than one
    # finding cites it; it is exactly as untrustworthy as a single-citer
    # mismatch.
    findings = [
        _finding("MED-001", "pass", phase="design", evidence_refs=("EVID-shared",)),
        _finding("MED-002", "pass", phase="post-deploy", evidence_refs=("EVID-shared",)),
    ]
    evidence = [_evidence("EVID-shared", phase="pre-deploy")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_governed_when_required_evidence_phase_matches_one_of_multiple_citers():
    # The complementary case: an evidence entry cited by findings with two
    # distinct phases remains trusted as long as its own phase matches
    # *any one* of them -- matching any citing finding's phase is
    # sufficient, never requiring agreement across every citer.
    findings = [
        _finding("MED-001", "pass", phase="design", evidence_refs=("EVID-shared",)),
        _finding("MED-002", "pass", phase="post-deploy", evidence_refs=("EVID-shared",)),
    ]
    evidence = [_evidence("EVID-shared", phase="design")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "fresh"
    assert manifest["summary"]["verdict"] == "governed"


def test_manifest_summary_partial_when_required_evidence_policy_set_sha256_mismatches():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-wrong-policy",))]
    evidence = [_evidence("EVID-wrong-policy", policy_set_sha256=_sha256_of("not-the-real-policy-set"))]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] != "fresh"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_partial_when_freshness_expired():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-oldest",))]
    evidence = [_evidence("EVID-oldest", collected_at=_OLDEST_TRUSTWORTHY_TIMESTAMP)]
    result = _base_result(findings=findings, evidence=evidence, captured_at=_CAPTURED_AT_EXPIRED)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "expired"
    assert manifest["summary"]["verdict"] == "partial"


def test_manifest_summary_ignores_unrelated_evidence_identity_mismatch():
    # An unrelated evidence entry -- one nothing here required -- carries
    # a foreign repository, a mismatched commit, and a mismatched phase.
    # None of that may drag down a required, correctly-bound evidence
    # entry's own freshness or the overall verdict.
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-required",))]
    evidence = [
        _evidence("EVID-required", collected_at=_COLLECTED_AT_EARLY),
        _evidence(
            "EVID-unrelated-mismatched",
            repository="someone-else/other-repo",
            source_commit="f" * 40,
            phase="post-deploy",
        ),
    ]
    result = _base_result(findings=findings, evidence=evidence, dirty=False)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "fresh"
    assert manifest["summary"]["verdict"] == "governed"
    # The unrelated evidence's own "post-deploy" label must never leak into
    # the assessment-level phase claim: nothing here found or asserted
    # anything at post-deploy.
    assert manifest["phase"] == "design"


# ---------------------------------------------------------------------------
# Hardening round: the manifest/evidence-pack's assessment-level "phase"
# claim is never derived from evidence labels -- only from the assessment's
# own assertions (``Finding.phase``), or from an explicit, authoritative
# ``AssessmentResult.phase`` when an orchestrator has supplied one. Evidence
# describes when it was collected, not what phase the assessment itself
# claims to be judging: an uncited, probe-only, or explicitly distrusted
# evidence record must never be able to escalate that claim (quality
# rereview).
# ---------------------------------------------------------------------------


def test_manifest_phase_ignores_uncited_post_deploy_evidence():
    # A design-only finding cites no evidence at all; a wholly uncited
    # evidence entry happens to carry a "post-deploy" label. That label
    # must never escalate the assessment's own phase claim.
    findings = [_finding("MED-001", "pass", phase="design")]
    evidence = [_evidence("EVID-uncited", phase="post-deploy")]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["phase"] == "design"


def test_manifest_phase_ignores_probe_only_evidence():
    # Evidence cited only by a probe (never by any finding) still must not
    # define the assessment's lifecycle claim: ``ProbeResult`` carries no
    # ``phase`` field at all, so a probe-only evidence citation is exactly
    # as irrelevant to the phase claim as an uncited one.
    findings = [_finding("MED-001", "pass", phase="design")]
    probes = [_probe("PROBE-001", None, None)]
    evidence = [_evidence("EVID-PROBE-001", phase="post-deploy")]
    result = _base_result(findings=findings, probes=probes, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["phase"] == "design"


def test_manifest_phase_ignores_mismatched_distrusted_evidence_label():
    # Evidence cited by a design-phase finding, but the evidence record is
    # itself untrustworthy (foreign repository) *and* carries a
    # "post-deploy" label. Neither the mismatch nor the label may escalate
    # the assessment's phase claim -- only ``Finding.phase`` may.
    findings = [_finding("MED-001", "pass", phase="design", evidence_refs=("EVID-distrusted",))]
    evidence = [
        _evidence(
            "EVID-distrusted",
            phase="post-deploy",
            repository="someone-else/other-repo",
            source_commit="f" * 40,
        )
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["phase"] == "design"


def test_manifest_phase_derives_from_latest_finding_phase_when_no_explicit_phase():
    # Regression: with no explicit ``AssessmentResult.phase``, the phase
    # claim is still the latest lifecycle stage among the assessment's own
    # findings -- this is a legitimate assertion, unlike an evidence label.
    findings = [
        _finding("MED-001", "pass", phase="design"),
        _finding("MED-002", "pass", phase="post-deploy"),
    ]
    result = _base_result(findings=findings)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["phase"] == "post-deploy"


def test_manifest_phase_defaults_to_design_with_no_findings_and_no_explicit_phase():
    # An empty-finding assessment (for example, a probe-only run with no
    # findings recorded yet) must still conservatively default to
    # "design" -- unchanged compatibility behavior.
    result = _base_result(findings=(), evidence=())
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["phase"] == "design"


def test_manifest_phase_uses_explicit_result_phase_over_findings_and_evidence():
    # An explicit, orchestrator-supplied ``AssessmentResult.phase`` is
    # authoritative: it wins over both the derived finding-phase maximum
    # and any evidence label.
    findings = [_finding("MED-001", "pass", phase="design")]
    evidence = [_evidence("EVID-post-deploy", phase="post-deploy")]
    result = _base_result(findings=findings, evidence=evidence, phase="pre-deploy")
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["phase"] == "pre-deploy"



# ---------------------------------------------------------------------------


def test_freshness_unrelated_old_evidence_does_not_expire_current_required_evidence():
    # A required evidence entry is current and trustworthy; a separate,
    # unrelated evidence entry that nothing requires is far older -- the
    # unrelated old entry must never drag oldest_source_at (or the
    # overall freshness verdict) down.
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-required",))]
    evidence = [
        _evidence("EVID-required", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-unrelated-ancient", collected_at="2020-01-01T00:00:00Z"),
    ]
    result = _base_result(findings=findings, evidence=evidence, captured_at=_COLLECTED_AT_EARLY)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["oldest_source_at"] == _COLLECTED_AT_EARLY
    assert manifest["freshness"]["status"] == "fresh"


def test_freshness_old_required_evidence_expires_at_a_later_captured_at():
    # A required evidence entry was collected in the past; the
    # assessment's own trusted capture instant is well past that entry's
    # freshness window -- the manifest must truthfully report "expired",
    # bound to the assessment's own capture instant, never to any
    # evidence timestamp.
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-required",))]
    evidence = [_evidence("EVID-required", collected_at=_OLDEST_TRUSTWORTHY_TIMESTAMP)]
    result = _base_result(findings=findings, evidence=evidence, captured_at=_CAPTURED_AT_EXPIRED)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "expired"


def test_freshness_missing_captured_at_degrades_to_stale():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-required",))]
    evidence = [_evidence("EVID-required", collected_at=_COLLECTED_AT_EARLY)]
    result = _base_result(findings=findings, evidence=evidence, captured_at=None)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "stale"
    # oldest_source_at is still derivable from the trustworthy required
    # evidence even though captured_at itself is untrustworthy; only the
    # verdict, not the window computation, degrades.
    assert manifest["freshness"]["oldest_source_at"] == _COLLECTED_AT_EARLY
    # The manifest schema requires captured_at to be a non-null, valid
    # timestamp: a missing trusted capture instant degrades to this
    # module's own placeholder, never a fabricated real instant.
    assert manifest["captured_at"] == render.FALLBACK_TIMESTAMP
    # Quality review: a missing captured_at must never let an
    # all-trustworthy set of required evidence launder into "governed" on
    # the strength of a capture instant this manifest cannot actually
    # vouch for -- the truthful verdict is "partial".
    assert manifest["summary"]["verdict"] == "partial"


def test_freshness_invalid_captured_at_degrades_to_stale():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-required",))]
    evidence = [_evidence("EVID-required", collected_at=_COLLECTED_AT_EARLY)]
    result = _base_result(
        findings=findings, evidence=evidence, captured_at=_MALFORMED_COLLECTED_AT
    )
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "stale"
    assert manifest["captured_at"] == render.FALLBACK_TIMESTAMP
    assert manifest["summary"]["verdict"] == "partial"


def test_freshness_missing_captured_at_with_no_required_evidence_still_governed():
    # The no-required-evidence carveout is unaffected by a missing/
    # unparseable captured_at: with nothing here needing evidentiary
    # proof of freshness, the legitimate "stale" freshness status over an
    # empty window must never prevent "governed" -- exactly the same
    # carveout as when captured_at is present and valid.
    findings = [_finding("MED-001", "pass")]
    result = _base_result(findings=findings, captured_at=None)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["freshness"]["status"] == "stale"
    assert manifest["summary"]["verdict"] == "governed"


def test_freshness_captured_at_is_never_derived_from_newest_evidence():
    # The assessment's trusted capture instant is deliberately *earlier*
    # than the newest required evidence timestamp -- captured_at must
    # reflect only ``result.captured_at``, never the newest evidence
    # entry this manifest happens to carry.
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-a", "EVID-b"))]
    evidence = [
        _evidence("EVID-a", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-b", collected_at=_COLLECTED_AT_LATE),
    ]
    result = _base_result(findings=findings, evidence=evidence, captured_at=_COLLECTED_AT_EARLY)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    assert manifest["captured_at"] == _COLLECTED_AT_EARLY
    assert manifest["captured_at"] != _COLLECTED_AT_LATE


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
    # All three rendered artifacts -- not just the Markdown evidence pack
    # -- must never carry a probe's raw comparison payload.
    evidence_text = evidence_path.read_text()
    manifest_text = manifest_path.read_text()
    apply_plan_text = apply_plan_path.read_text()
    for text in (evidence_text, manifest_text, apply_plan_text):
        assert "SENTINEL-EXPECTED-PAYLOAD" not in text
        assert "SENTINEL-OBSERVED-PAYLOAD" not in text


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
# Hardening round: the assessed root itself must be race-resistant -- a
# symlink root is rejected outright, and a mid-transaction rename/symlink
# swap of the root is explicitly detected and rejected (issue 3, 4th
# rereview)
# ---------------------------------------------------------------------------


def test_write_artifacts_rejects_root_that_is_itself_a_symlink(tmp_path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    root_symlink = tmp_path / "root-symlink"
    root_symlink.symlink_to(real_root, target_is_directory=True)

    result = _full_result()

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            root_symlink,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )

    # Rejected before any write occurred through the symlink into the
    # real, otherwise-legitimate directory it points at.
    assert not list(real_root.iterdir())


def test_write_artifacts_detects_root_rename_and_symlink_swap_mid_transaction(
    tmp_path, monkeypatch
):
    # Unlike an *ancestor* swap (e.g. the shared ``tests/`` directory),
    # which stays safely tolerated purely because every mutating
    # operation is already descriptor-relative, a swap of the *root*
    # itself -- the one path this call resolves and opens by name, up
    # front, before any fd exists to anchor against -- is explicitly
    # checked for and rejected at each transaction checkpoint.
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)

    root_saved = tmp_path.parent / f"{tmp_path.name}-saved-by-attacker"
    attacker_target = tmp_path.parent / f"{tmp_path.name}-attacker-target"
    attacker_target.mkdir()

    original_stage = render._stage_temp_file
    swap_done = {"value": False}

    def _spy_stage(parent_fd, dest, leaf_name, data):
        staged_name = original_stage(parent_fd, dest, leaf_name, data)
        # Immediately after the *first* artifact is staged, an attacker
        # renames the assessed root itself out of the way and replaces
        # its old path with a symlink pointing outside the assessed
        # root, before the remaining artifacts are staged or committed.
        if not swap_done["value"]:
            tmp_path.rename(root_saved)
            tmp_path.symlink_to(attacker_target, target_is_directory=True)
            swap_done["value"] = True
        return staged_name

    monkeypatch.setattr(render, "_stage_temp_file", _spy_stage)

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        )

    # No outside write ever landed in the attacker's symlink target,
    # proving the checkpoint caught the swap before any commit.
    assert not list(attacker_target.iterdir())
    # And nothing was left half-written in the original (now-renamed)
    # root directory either -- staged temp files were cleaned up too.
    original_root_contents = list(root_saved.rglob("*"))
    assert not any(path.name.endswith(".stage") for path in original_root_contents)


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
# Hardening round: rollback of a newly created (no-prior-backup) artifact
# must fsync its parent directory too, not only a successfully restored
# backup's (issue 4, 4th rereview)
# ---------------------------------------------------------------------------


def test_write_artifacts_fsyncs_parent_dir_after_removing_newly_created_artifact_on_rollback(
    tmp_path, monkeypatch
):
    # A completely fresh root: every destination is newly created (no
    # prior artifact to back up), so a forced failure on the very last
    # commit must roll back the first two via the "created without
    # backup" removal path -- and that removal's own parent-directory
    # fsync must actually happen, not be skipped.
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)

    original_fsync_dir = render._fsync_dir
    fsync_calls_after_removal: List[Path] = []

    def _spy_fsync_dir(dest: Path, parent_fd: int) -> None:
        # A fsync that happens *after* dest's own file has already been
        # removed from disk can only be this rollback step's own
        # unlink-then-fsync sequence -- the ordinary forward-commit fsync
        # always runs while the just-created file is still present.
        if not Path(dest).exists():
            fsync_calls_after_removal.append(Path(dest))
        original_fsync_dir(dest, parent_fd)

    monkeypatch.setattr(render, "_fsync_dir", _spy_fsync_dir)

    calls = {"count": 0}

    def _replace(src: Path, dst: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 3:
            # Fail the third (apply-plan) commit -- manifest and
            # evidence were both already newly created by this point.
            raise OSError("synthetic forward failure on third commit")
        os.replace(src, dst)

    with pytest.raises(render.ArtifactWriteError):
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
            replace=_replace,
        )

    # Both newly created artifacts removed during rollback also had
    # their parent directory fsynced afterward.
    assert len(fsync_calls_after_removal) >= 2
    assert not manifest_path.exists()
    assert not evidence_path.exists()
    assert not apply_plan_path.exists()


def test_write_artifacts_rollback_reports_incomplete_when_fsync_after_removal_fails(
    tmp_path, monkeypatch
):
    result = _full_result()
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)

    original_fsync_dir = render._fsync_dir

    def _failing_fsync_dir(dest: Path, parent_fd: int) -> None:
        if not Path(dest).exists():
            raise OSError("synthetic fsync failure after removal")
        original_fsync_dir(dest, parent_fd)

    monkeypatch.setattr(render, "_fsync_dir", _failing_fsync_dir)

    calls = {"count": 0}

    def _replace(src: Path, dst: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("synthetic forward failure on third commit")
        os.replace(src, dst)

    with pytest.raises(render.ArtifactWriteError) as excinfo:
        render.write_artifacts(
            tmp_path,
            result,
            render.DEFAULT_MANIFEST_RELATIVE_PATH,
            render.DEFAULT_EVIDENCE_RELATIVE_PATH,
            render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
            replace=_replace,
        )

    message = str(excinfo.value)
    assert "ROLLBACK DID NOT FULLY RESTORE" in message
    # The unlink itself still succeeded despite the fsync failure -- the
    # newly created artifacts are gone from disk regardless of whether
    # their removal's own durability fsync could be confirmed.
    assert not manifest_path.exists()
    assert not evidence_path.exists()


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
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-frac",))]
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
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-boundary",))]
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
    findings = [
        _finding("MED-001", "pass", evidence_refs=("EVID-overprecise", "EVID-precise"))
    ]
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


def test_manifest_rejects_evidence_with_tied_primary_key_regardless_of_order():
    # Evidence identity is now uniqueness-constrained by this module (see
    # DuplicateEvidenceIdError): two entries sharing an evidence_id are
    # never tolerated and tie-broken deterministically the way every
    # other record-list sort is -- they are rejected outright, in either
    # input order, before any manifest is even assembled.
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

    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result_forward)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result_reordered)


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


def test_manifest_pin_dependencies_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    pins_forward = {
        **base.pins,
        "dependencies": (
            {"name": "tied-dep", "version": "1.0.0"},
            {"name": "tied-dep", "version": "2.0.0"},
        ),
    }
    pins_reordered = {
        **base.pins,
        "dependencies": (
            {"name": "tied-dep", "version": "2.0.0"},
            {"name": "tied-dep", "version": "1.0.0"},
        ),
    }
    result_forward = dataclasses.replace(base, pins=pins_forward)
    result_reordered = dataclasses.replace(base, pins=pins_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["pins"]["dependencies"]) == 2


def test_manifest_pin_specifications_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    pins_forward = {
        **base.pins,
        "specifications": (
            {"name": "tied-spec", "version": "1.0.0"},
            {"name": "tied-spec", "version": "2.0.0"},
        ),
    }
    pins_reordered = {
        **base.pins,
        "specifications": (
            {"name": "tied-spec", "version": "2.0.0"},
            {"name": "tied-spec", "version": "1.0.0"},
        ),
    }
    result_forward = dataclasses.replace(base, pins=pins_forward)
    result_reordered = dataclasses.replace(base, pins=pins_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["pins"]["specifications"]) == 2


def test_manifest_policy_hashes_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    hashes_forward = (
        {"path": "governance/tied.json", "sha256": _sha256_of("policy-a")},
        {"path": "governance/tied.json", "sha256": _sha256_of("policy-b")},
    )
    hashes_reordered = (
        {"path": "governance/tied.json", "sha256": _sha256_of("policy-b")},
        {"path": "governance/tied.json", "sha256": _sha256_of("policy-a")},
    )
    result_forward = dataclasses.replace(base, policy_hashes=hashes_forward)
    result_reordered = dataclasses.replace(base, policy_hashes=hashes_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["policy_hashes"]) == 2


def test_manifest_change_plane_workflows_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    change_plane_forward = {
        **base.change_plane,
        "workflows": (
            {"path": ".github/workflows/tied.yml", "sha256": _sha256_of("workflow-a")},
            {"path": ".github/workflows/tied.yml", "sha256": _sha256_of("workflow-b")},
        ),
    }
    change_plane_reordered = {
        **base.change_plane,
        "workflows": (
            {"path": ".github/workflows/tied.yml", "sha256": _sha256_of("workflow-b")},
            {"path": ".github/workflows/tied.yml", "sha256": _sha256_of("workflow-a")},
        ),
    }
    result_forward = dataclasses.replace(base, change_plane=change_plane_forward)
    result_reordered = dataclasses.replace(base, change_plane=change_plane_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["change_plane"]["workflows"]) == 2


def test_manifest_change_plane_identities_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    change_plane_forward = {
        **base.change_plane,
        "identities": (
            {"identity": "tied-identity", "kind": "managed-identity"},
            {"identity": "tied-identity", "kind": "service-principal"},
        ),
    }
    change_plane_reordered = {
        **base.change_plane,
        "identities": (
            {"identity": "tied-identity", "kind": "service-principal"},
            {"identity": "tied-identity", "kind": "managed-identity"},
        ),
    }
    result_forward = dataclasses.replace(base, change_plane=change_plane_forward)
    result_reordered = dataclasses.replace(base, change_plane=change_plane_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["change_plane"]["identities"]) == 2


def test_manifest_conformance_claims_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    claims_forward = (
        {
            "claim_id": "CLAIM-tied",
            "description": "description A",
            "status": "pass",
            "evidence_refs": (),
        },
        {
            "claim_id": "CLAIM-tied",
            "description": "description B",
            "status": "not-applicable",
            "evidence_refs": (),
        },
    )
    claims_reordered = tuple(reversed(claims_forward))
    result_forward = dataclasses.replace(base, conformance_claims=claims_forward)
    result_reordered = dataclasses.replace(base, conformance_claims=claims_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["conformance"]["claims"]) == 2


def test_manifest_conformance_reports_with_tied_primary_key_still_order_independent():
    findings = [_finding("MED-001", "pass")]
    base = _base_result(findings=findings)
    reports_forward = (
        {
            "report_id": "REPORT-tied",
            "tool": "tool-a",
            "version": "1.0.0",
            "generated_at": _COLLECTED_AT_EARLY,
            "summary": "summary A",
            "evidence_refs": (),
        },
        {
            "report_id": "REPORT-tied",
            "tool": "tool-b",
            "version": "2.0.0",
            "generated_at": _COLLECTED_AT_EARLY,
            "summary": "summary B",
            "evidence_refs": (),
        },
    )
    reports_reordered = tuple(reversed(reports_forward))
    result_forward = dataclasses.replace(base, conformance_reports=reports_forward)
    result_reordered = dataclasses.replace(base, conformance_reports=reports_reordered)

    manifest_forward = render.build_manifest(result_forward)
    manifest_reordered = render.build_manifest(result_reordered)
    _assert_valid_manifest(manifest_forward)
    assert canonical.canonical_bytes(manifest_forward) == canonical.canonical_bytes(
        manifest_reordered
    )
    assert len(manifest_forward["conformance"]["reports"]) == 2


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
# Final rereview: duplicate evidence_id values are rejected outright (issue
# 1) -- a trustworthy entry must never mask an untrustworthy duplicate of
# the identical id through plain set membership.
# ---------------------------------------------------------------------------


def test_build_manifest_rejects_duplicate_evidence_id_valid_and_invalid():
    # Without this rejection, a finding requiring "EVID-DUP" would look
    # trustworthy (the set of trustworthy ids would contain "EVID-DUP"
    # because *one* of the two same-id entries parses), even though the
    # *other* same-id entry -- which could just as easily be the one this
    # finding actually meant -- has no trustworthy timestamp at all.
    findings = [_finding("MED-001", "must-fix", evidence_refs=("EVID-DUP",))]
    evidence = [
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-DUP", collected_at=None),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result)


def test_build_manifest_rejects_duplicate_evidence_id_both_invalid():
    findings = [_finding("MED-001", "must-fix", evidence_refs=("EVID-DUP",))]
    evidence = [
        _evidence("EVID-DUP", collected_at=None),
        _evidence("EVID-DUP", collected_at="not-a-timestamp"),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result)


def test_build_manifest_rejects_duplicate_evidence_id_both_valid():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-DUP",))]
    evidence = [
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_LATE),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result)


def test_build_manifest_rejects_exact_duplicate_evidence_entries():
    # Even a byte-for-byte identical duplicate (not merely a colliding id
    # with differing fields) is rejected -- there is still no way to know
    # which of the two identical entries a finding's reference means, and
    # rendering must never silently collapse them into one.
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-DUP",))]
    one_evidence = _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY)
    evidence = [one_evidence, one_evidence]
    result = _base_result(findings=findings, evidence=evidence)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result)


def test_build_manifest_rejects_duplicate_evidence_id_regardless_of_input_order():
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-DUP",))]
    forward = [
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-DUP", collected_at=None),
        _evidence("EVID-OTHER", collected_at=_COLLECTED_AT_LATE),
    ]
    reordered = list(reversed(forward))
    result_forward = _base_result(findings=findings, evidence=forward)
    result_reordered = _base_result(findings=findings, evidence=reordered)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result_forward)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_manifest(result_reordered)


def test_build_manifest_accepts_distinct_evidence_ids():
    # Control case: distinct ids -- even ones that share every other
    # field -- are never rejected.
    findings = [_finding("MED-001", "pass", evidence_refs=("EVID-A", "EVID-B"))]
    evidence = [
        _evidence("EVID-A", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-B", collected_at=None),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest = render.build_manifest(result)
    _assert_valid_manifest(manifest)
    ids = [entry["evidence_id"] for entry in manifest["evidence"]]
    assert ids == ["EVID-A", "EVID-B"]


def test_render_evidence_pack_rejects_duplicate_evidence_id():
    # render_evidence_pack builds its own manifest internally -- the
    # rejection must surface there too, not only from build_manifest
    # directly.
    findings = [_finding("MED-001", "must-fix", evidence_refs=("EVID-DUP",))]
    evidence = [
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-DUP", collected_at=None),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.render_evidence_pack(result)


def test_build_apply_plan_rejects_duplicate_evidence_id():
    findings = [_finding("MED-001", "must-fix", evidence_refs=("EVID-DUP",))]
    evidence = [
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-DUP", collected_at=None),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    with pytest.raises(render.DuplicateEvidenceIdError):
        render.build_apply_plan(result)


def test_write_artifacts_rejects_duplicate_evidence_id_before_any_write(tmp_path):
    findings = [_finding("MED-001", "must-fix", evidence_refs=("EVID-DUP",))]
    evidence = [
        _evidence("EVID-DUP", collected_at=_COLLECTED_AT_EARLY),
        _evidence("EVID-DUP", collected_at=None),
    ]
    result = _base_result(findings=findings, evidence=evidence)
    manifest_path, evidence_path, apply_plan_path = _artifact_paths(tmp_path)

    with pytest.raises(render.DuplicateEvidenceIdError):
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
_HOSTILE_WWW_AUTOLINK_UPPERCASE = "visit WWW.evil.example now"
_HOSTILE_WWW_AUTOLINK_MIXED_CASE = "visit Www.Evil.Example now"
# Quality review (critical): Python's ``\b`` word-boundary anchor requires a
# transition between a ``\w`` and a non-``\w`` character, so it wrongly
# refuses to match a scheme/``www.`` trigger immediately preceded by
# another word character -- a digit, an underscore, or a letter --
# treating it as "not a boundary". cmark-gfm's own scanner has no such
# requirement at all: its scheme rewind and ``www_match`` recognizer fire
# regardless of what precedes the trigger. Each of these is a real, live
# GFM autolink despite starting mid-"word" by Python's definition.
_HOSTILE_BARE_AUTOLINK_DIGIT_PREFIX = "visit 4https://evil.example now"
_HOSTILE_BARE_AUTOLINK_UNDERSCORE_PREFIX = "visit _https://evil.example now"
_HOSTILE_BARE_AUTOLINK_DIGIT_PREFIX_WITH_PATH = "visit 0https://evil.example/pwn now"
_HOSTILE_BARE_AUTOLINK_LETTER_UNDERSCORE_PREFIX = "visit x_https://e.x now"
_HOSTILE_WWW_AUTOLINK_UNDERSCORE_PREFIX = "visit _www.evil.example now"


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
        _HOSTILE_WWW_AUTOLINK_UPPERCASE,
        _HOSTILE_WWW_AUTOLINK_MIXED_CASE,
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
    # a bare "scheme://" or "www." run (in any letter case) into a live
    # link with no brackets at all. This module defends against that by
    # wrapping just the trigger substring (e.g. "https://" or "WWW.") in
    # its own double-backtick code span, which fragments the token stream
    # so the scheme/host (or "www."/host) can never again appear as one
    # contiguous run of characters for a downstream autolink/linkify rule
    # to match against -- so the *exact original* contiguous trigger
    # substring must never survive rendering, even though the individual
    # characters remain fully present (and readable) on either side of
    # the inserted code-span boundary.
    assert "https://evil.example" not in matrix_section
    assert "http://evil.example" not in matrix_section
    assert "www.evil.example" not in matrix_section
    assert "WWW.evil.example" not in matrix_section
    assert "Www.Evil.Example" not in matrix_section
    # The value must still be readable, safe literal text rather than
    # being dropped or replaced with a placeholder.
    assert "evil.example" in matrix_section.lower() or "click me" in matrix_section or "alt text" in matrix_section


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


# ---------------------------------------------------------------------------
# Hardening round: GFM bare-email autolinks must also be neutralized, in all
# case variants, not just bare "http(s)://"/"www." runs (issue 5, 3rd
# rereview) -- and, per the 5th rereview, GFM's extended email autolink
# grammar permits underscores anywhere in the domain (unlike its separate
# www./scheme:// domain grammar, which forbids underscores in the last two
# segments), so the neutralizing regex must not miss an underscore-domain
# trigger such as ``foo@bar_baz.example``.
# ---------------------------------------------------------------------------

_HOSTILE_BARE_EMAIL_AUTOLINK = "contact user@evil.example for access"
_HOSTILE_BARE_EMAIL_AUTOLINK_UPPERCASE = "contact User@Evil.EXAMPLE for access"
_HOSTILE_BARE_EMAIL_AUTOLINK_COMPLEX_LOCAL = "contact first.last+tag@evil.example for access"
_HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_DOMAIN = "contact user@bar_baz.evil.example for access"
_HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_DOMAIN_UPPERCASE = (
    "contact User@BAR_BAZ.EVIL.EXAMPLE for access"
)
_HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_LAST_SEGMENT = "contact user@evil.exam_ple for access"


@pytest.mark.parametrize(
    "hostile_text",
    [
        _HOSTILE_BARE_EMAIL_AUTOLINK,
        _HOSTILE_BARE_EMAIL_AUTOLINK_UPPERCASE,
        _HOSTILE_BARE_EMAIL_AUTOLINK_COMPLEX_LOCAL,
        _HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_DOMAIN,
        _HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_DOMAIN_UPPERCASE,
        _HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_LAST_SEGMENT,
    ],
)
def test_evidence_pack_neutralizes_bare_email_autolink(hostile_text):
    result = _base_result(findings=_escaping_finding(hostile_text))
    text = render.render_evidence_pack(result)
    matrix_start = text.index("## Pass/fail matrix")
    matrix_section = text[matrix_start : text.index("## Residual-risk register")]
    # GFM's extended-autolink extension recognizes a bare
    # ``local@domain.tld``-shaped run (case-insensitively, and -- unlike
    # its separate www./scheme:// domain grammar -- with underscores
    # permitted anywhere in the domain, including its last segments) with
    # no brackets at all and turns it into a live ``mailto:`` link. This
    # module defends against that by wrapping the *entire* trigger
    # substring in its own double-backtick code span -- unlike the
    # scheme/www defenses above, the whole "local@domain" run is wrapped
    # together (rather than just a short prefix) since there is no
    # separate literal "domain" text left outside the span to remain
    # readable on its own. The trigger substring therefore does still
    # appear (fully readable), but it must only ever appear wrapped
    # inside its own double-backtick code span -- never as bare,
    # unwrapped text that a downstream autolink/linkify rule could scan.
    local_part, domain = hostile_text.split("@", 1)
    domain = domain.split(" ", 1)[0]
    local_part = local_part.rsplit(" ", 1)[-1]
    trigger = f"{local_part}@{domain}"
    assert f"``{trigger}``" in matrix_section
    assert matrix_section.count(trigger) == matrix_section.count(f"``{trigger}``")
    # The value must still be readable, safe literal text (case preserved).
    assert domain in matrix_section


def test_evidence_pack_bare_email_autolink_does_not_affect_non_email_at_signs():
    # An "@" with no dotted domain after it (e.g. an unqualified handle)
    # is not a GFM autolink trigger at all -- it must be left untouched
    # rather than over-escaped.
    result = _base_result(findings=_escaping_finding("cc @some-handle for review"))
    text = render.render_evidence_pack(result)
    assert "@some-handle" in text


# ---------------------------------------------------------------------------
# Quality review: backslash-escaping a bare "scheme://"/"www."/email
# autolink trigger does not actually defeat GitHub Flavored Markdown's
# extended autolink extension -- a compliant renderer strips the backslash
# (as an ordinary backslash escape) while assembling the plain-text content
# handed to that extension, so the exact same trigger substring reforms and
# still renders as a live link regardless. The fix wraps just the trigger
# substring in its own inline code span instead: a code span's content is a
# genuine CommonMark AST boundary that is never re-scanned by any inline
# construct (autolink extensions included), and text on either side of it
# is never stitched back into one contiguous run either. These tests prove
# that structural property directly against a real, independent
# CommonMark-compliant parser's own token stream (not merely against
# substring presence in the raw Markdown source), using markdown-it-py's
# ``commonmark`` preset with only the ``table``/``strikethrough``
# extensions enabled (see ``_parse_with_commonmark`` for why a disabled
# ``linkify`` does not weaken what is being proven here). markdown-it-py is
# already present in this environment (a transitive dependency of `rich`,
# itself already relied upon elsewhere); no new dependency is added to
# exercise it, and every test below degrades to a skip if it is absent.
# ---------------------------------------------------------------------------

_REQUIRES_MARKDOWN_IT = pytest.mark.skipif(
    MarkdownIt is None, reason="markdown-it-py is not available in this environment"
)


@_REQUIRES_MARKDOWN_IT
@pytest.mark.parametrize(
    "hostile_text,expected_code_span_content",
    [
        (_HOSTILE_BARE_AUTOLINK, "https://"),
        ("visit HTTP://evil.example now", "HTTP://"),
        (_HOSTILE_WWW_AUTOLINK, "www."),
        (_HOSTILE_WWW_AUTOLINK_UPPERCASE, "WWW."),
        (_HOSTILE_WWW_AUTOLINK_MIXED_CASE, "Www."),
        (_HOSTILE_BARE_EMAIL_AUTOLINK, "user@evil.example"),
        (_HOSTILE_BARE_EMAIL_AUTOLINK_UPPERCASE, "User@Evil.EXAMPLE"),
        (_HOSTILE_BARE_EMAIL_AUTOLINK_UNDERSCORE_DOMAIN, "user@bar_baz.evil.example"),
        (_HOSTILE_BARE_AUTOLINK_DIGIT_PREFIX, "https://"),
        (_HOSTILE_BARE_AUTOLINK_UNDERSCORE_PREFIX, "https://"),
        (_HOSTILE_BARE_AUTOLINK_DIGIT_PREFIX_WITH_PATH, "https://"),
        (_HOSTILE_BARE_AUTOLINK_LETTER_UNDERSCORE_PREFIX, "https://"),
        (_HOSTILE_WWW_AUTOLINK_UNDERSCORE_PREFIX, "www."),
    ],
)
def test_evidence_pack_autolink_defense_verified_by_real_commonmark_parser(
    hostile_text, expected_code_span_content
):
    result = _base_result(findings=_escaping_finding(hostile_text))
    text = render.render_evidence_pack(result)
    matrix_source = _matrix_markdown_table_source(text)
    tokens = _parse_with_commonmark(matrix_source)
    # No autolink/linkify extension of any kind can ever fire for a live
    # link here -- assert this directly against the real parser's token
    # stream as a standing regression guard.
    assert not any(token.type == "link_open" for token in tokens)
    # The table itself must still parse into exactly the header's 7
    # columns -- the defense must never corrupt table structure.
    assert sum(1 for token in tokens if token.type == "th_open") == 7
    code_span_contents = [token.content for token in tokens if token.type == "code_inline"]
    assert expected_code_span_content in code_span_contents


@_REQUIRES_MARKDOWN_IT
def test_evidence_pack_autolink_defense_survives_touching_scheme_and_email_triggers():
    # Two independently-wrapped code spans landing directly adjacent, with
    # nothing between them in the original hostile text (the "https://"
    # scheme trigger immediately followed by a "user@evil.example" email
    # trigger), must never merge their double-backtick delimiters into one
    # ambiguous, longer backtick run -- which would desynchronize both
    # spans' intended open/close pairing and could leave raw, broken
    # Markdown syntax (or an unintended live autolink) in the output.
    hostile_text = "leak https://user@evil.example now"
    result = _base_result(findings=_escaping_finding(hostile_text))
    text = render.render_evidence_pack(result)
    matrix_start = text.index("## Pass/fail matrix")
    matrix_section = text[matrix_start : text.index("## Residual-risk register")]
    assert "```" not in matrix_section  # no ambiguous 3+ backtick run anywhere
    matrix_source = _matrix_markdown_table_source(text)
    tokens = _parse_with_commonmark(matrix_source)
    assert not any(token.type == "link_open" for token in tokens)
    assert sum(1 for token in tokens if token.type == "th_open") == 7
    code_span_contents = [token.content for token in tokens if token.type == "code_inline"]
    assert "https://" in code_span_contents
    assert "user@evil.example" in code_span_contents


@_REQUIRES_MARKDOWN_IT
def test_evidence_pack_autolink_defense_within_full_table_row_context():
    # A full, realistic Pass/fail matrix row (not just an isolated
    # fragment) embedding a hostile bare-URL value must still parse as a
    # single, well-formed table row with exactly 7 cells and no live link,
    # confirming the defense composes correctly with this module's other
    # per-cell escaping (evidence refs, remediation kind, etc.) rather
    # than only working in isolation.
    findings = [
        contracts.Finding(
            finding_id="MED-001",
            status="pass",
            phase="design",
            plane="runtime",
            reason_code="reason",
            summary=_HOSTILE_BARE_AUTOLINK,
            details="details",
            affected_actions=(),
            affected_paths=(),
            evidence_refs=("ev-1",),
            remediation_ids=(),
            residual_risk_ref=None,
        )
    ]
    evidence = [_evidence("ev-1")]
    result = _base_result(findings=findings, evidence=evidence)
    text = render.render_evidence_pack(result)
    matrix_source = _matrix_markdown_table_source(text)
    tokens = _parse_with_commonmark(matrix_source)
    row_th_count = sum(1 for token in tokens if token.type == "th_open")
    row_td_count = sum(1 for token in tokens if token.type == "td_open")
    assert row_th_count == 7
    assert row_td_count % 7 == 0  # every data row still has exactly 7 cells
    assert not any(token.type == "link_open" for token in tokens)


# ---------------------------------------------------------------------------
# Quality review: ``_md_code_span`` used to compose badly with
# ``_md_escape_inline``'s own inserted double-backtick spans. An identifier
# such as ``user@evil.example/x`` round-trips through ``_md_escape_inline``
# as ```` ``user@evil.example``/x ```` (only the bare-email trigger
# substring wrapped); wrapping *that whole string* in one more pair of
# single backticks -- the old ``_md_code_span`` -- produced
# ```` ```user@evil.example``/x` ````, whose leading backtick lands
# directly adjacent (zero characters between them) to the following
# double backtick, merging into one ambiguous triple-backtick run with no
# matching triple-backtick run anywhere else in the string. CommonMark
# then fails to parse *any* code span there, so ``user@evil.example``
# falls back to being scanned as ordinary text -- exactly what GitHub
# Flavored Markdown's extended autolink extension turns into a live
# ``mailto:`` link.
#
# The fix makes ``_md_code_span`` wrap an identifier field's own *raw*
# value once, using a delimiter sized from that value's own backtick
# content, and makes it fully independent of (never composed with)
# ``_md_escape_inline``. These tests prove the redesigned function both in
# isolation and at every one of its real rendering call sites, the latter
# against a genuine CommonMark-compliant parser's own token stream (not
# merely against substring presence in the raw Markdown source).
# ---------------------------------------------------------------------------


def test_md_code_span_round_trips_a_plain_identifier():
    assert render._md_code_span("plain-id-123") == "`plain-id-123`"


def test_md_code_span_round_trips_a_non_string_value():
    assert render._md_code_span(True) == "`True`"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("no-backticks", "`no-backticks`"),
        ("one`tick", "``one`tick``"),
        ("two``ticks", "```two``ticks```"),
        ("three```ticks", "````three```ticks````"),
    ],
)
def test_md_code_span_delimiter_is_one_longer_than_longest_internal_run(value, expected):
    # The delimiter is sized from *value*'s own longest consecutive
    # backtick run (never fixed at one backtick), so no substring inside
    # the content can ever be mistaken by CommonMark's maximal-run
    # matching for a same-length closing delimiter.
    assert render._md_code_span(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("`leading-tick", "`` `leading-tick``"),
        ("trailing-tick`", "``trailing-tick` ``"),
        ("`both-ticks`", "`` `both-ticks` ``"),
    ],
)
def test_md_code_span_pads_a_literal_space_next_to_an_edge_backtick(value, expected):
    # A single literal space -- never another backtick -- separates the
    # delimiter from a leading/trailing backtick *in the content*, so the
    # delimiter's own backticks can never land directly adjacent to a
    # content backtick and merge into one longer, ambiguous run.
    assert render._md_code_span(value) == expected


def test_md_code_span_of_empty_value_is_a_single_space_code_span():
    assert render._md_code_span("") == "` `"


def test_md_code_span_never_reintroduces_escape_inline_substitutions():
    # Wrapping a value with metacharacters ``_md_escape_inline`` would
    # otherwise handle one at a time (&, [, ], (, ), |, <, >) must never
    # insert any backslash escape or entity reference of its own here: a
    # code span's own delimiters are already sufficient on their own, and
    # inserting escape-inline's substitutions would corrupt the code
    # span's literal content instead of merely being redundant.
    value = "a[b](c)|d&e<f>g"
    assert render._md_code_span(value) == f"`{value}`"


def test_md_code_span_collapses_embedded_newlines():
    assert render._md_code_span("line1\nline2\r\nline3\rline4") == "`line1 line2 line3 line4`"


_HOSTILE_CODE_SPAN_IDENTIFIER = "user@evil.example/x"
_HOSTILE_CODE_SPAN_TOUCHING_TRIGGERS = "https://user@evil.example"
_HOSTILE_CODE_SPAN_EMBEDDED_BACKTICKS = "id-`with`-backticks"
_HOSTILE_CODE_SPAN_WWW_UPPERCASE = "WWW.evil.example/x"


def _section_text(text: str, heading: str, next_heading: Optional[str]) -> str:
    """Extract the rendered lines belonging to *heading*, up to (but not
    including) *next_heading*, or to the end of the document if
    *next_heading* is ``None``."""
    start = text.index(heading)
    end = text.index(next_heading) if next_heading is not None else len(text)
    return text[start:end]


def _hostile_action_id_result(hostile: str):
    result = _base_result(actions=[_action(hostile)])
    return result, "## Runtime action inventory", "## Runtime mediation graph"


def _hostile_path_id_result(hostile: str):
    result = _base_result(
        actions=[_action("act-1")],
        paths=[_path(hostile, "act-1")],
    )
    return result, "## Runtime mediation graph", "## Application-path probe evidence"


def _hostile_probe_id_result(hostile: str):
    result = _base_result(
        actions=[_action("act-1")],
        paths=[_path("path-1", "act-1")],
        probes=[_probe(hostile, "act-1", "path-1")],
    )
    return result, "## Application-path probe evidence", "## GitHub Copilot change plane"


def _hostile_residual_risk_id_result(hostile: str):
    result = _base_result(
        residual_risks=[
            {"residual_risk_id": hostile, "finding_id": "n/a", "description": "test risk"}
        ]
    )
    return result, "## Residual-risk register", "## Remediation plan"


def _hostile_remediation_finding_id_result(hostile: str):
    result = _base_result(findings=[_finding(hostile, "must-fix")])
    return result, "## Remediation plan", None


def _hostile_source_repository_result(hostile: str):
    base = _base_result()
    result = dataclasses.replace(
        base, source=contracts.SourceRef(repository=hostile, commit=_COMMIT, dirty=False)
    )
    return result, "## Scope and trust model", "## Architecture and data flow"


def _hostile_source_commit_result(hostile: str):
    base = _base_result()
    result = dataclasses.replace(
        base, source=contracts.SourceRef(repository=_REPOSITORY, commit=hostile, dirty=False)
    )
    return result, "## Scope and trust model", "## Architecture and data flow"


def _hostile_change_plane_repository_result(hostile: str):
    base = _base_result()
    change_plane = dict(base.change_plane)
    change_plane["repository"] = hostile
    result = dataclasses.replace(base, change_plane=change_plane)
    return result, "## GitHub Copilot change plane", "## Evidence index"


_CODE_SPAN_CALL_SITES = [
    ("action_id", _hostile_action_id_result),
    ("path_id", _hostile_path_id_result),
    ("probe_id", _hostile_probe_id_result),
    ("residual_risk_id", _hostile_residual_risk_id_result),
    ("remediation_finding_id", _hostile_remediation_finding_id_result),
    ("source_repository", _hostile_source_repository_result),
    ("source_commit", _hostile_source_commit_result),
    ("change_plane_repository", _hostile_change_plane_repository_result),
]


@_REQUIRES_MARKDOWN_IT
@pytest.mark.parametrize("label,build", _CODE_SPAN_CALL_SITES)
def test_md_code_span_call_site_survives_bare_email_autolink_trigger_touching_slash(
    label, build
):
    result, heading, next_heading = build(_HOSTILE_CODE_SPAN_IDENTIFIER)
    text = render.render_evidence_pack(result)
    section = _section_text(text, heading, next_heading)
    tokens = _parse_with_commonmark(section)
    assert not any(token.type == "link_open" for token in tokens)
    code_span_contents = [token.content for token in tokens if token.type == "code_inline"]
    assert _HOSTILE_CODE_SPAN_IDENTIFIER in code_span_contents


@_REQUIRES_MARKDOWN_IT
@pytest.mark.parametrize("label,build", _CODE_SPAN_CALL_SITES)
def test_md_code_span_call_site_survives_touching_scheme_and_email_triggers(label, build):
    # A single identifier value combining two hostile triggers with
    # nothing between them (a bare ``https://`` scheme immediately
    # followed by a bare ``user@domain`` email, as in
    # ``https://user@evil.example``) is wrapped whole, in one code span,
    # by this module's design -- there is no separate per-trigger wrap
    # inside an identifier field to ever merge with another.
    result, heading, next_heading = build(_HOSTILE_CODE_SPAN_TOUCHING_TRIGGERS)
    text = render.render_evidence_pack(result)
    section = _section_text(text, heading, next_heading)
    tokens = _parse_with_commonmark(section)
    assert not any(token.type == "link_open" for token in tokens)
    code_span_contents = [token.content for token in tokens if token.type == "code_inline"]
    assert _HOSTILE_CODE_SPAN_TOUCHING_TRIGGERS in code_span_contents


@_REQUIRES_MARKDOWN_IT
@pytest.mark.parametrize("label,build", _CODE_SPAN_CALL_SITES)
def test_md_code_span_call_site_survives_www_trigger_in_uppercase(label, build):
    result, heading, next_heading = build(_HOSTILE_CODE_SPAN_WWW_UPPERCASE)
    text = render.render_evidence_pack(result)
    section = _section_text(text, heading, next_heading)
    tokens = _parse_with_commonmark(section)
    assert not any(token.type == "link_open" for token in tokens)
    code_span_contents = [token.content for token in tokens if token.type == "code_inline"]
    assert _HOSTILE_CODE_SPAN_WWW_UPPERCASE in code_span_contents


@_REQUIRES_MARKDOWN_IT
@pytest.mark.parametrize("label,build", _CODE_SPAN_CALL_SITES)
def test_md_code_span_call_site_survives_embedded_backticks(label, build):
    # An identifier containing its own literal backticks must still
    # isolate into exactly one well-formed code span (the delimiter is
    # sized from the value's own longest run), never leaving a stray,
    # unmatched backtick as plain text alongside it.
    result, heading, next_heading = build(_HOSTILE_CODE_SPAN_EMBEDDED_BACKTICKS)
    text = render.render_evidence_pack(result)
    section = _section_text(text, heading, next_heading)
    tokens = _parse_with_commonmark(section)
    assert not any(token.type == "link_open" for token in tokens)
    code_span_contents = [token.content for token in tokens if token.type == "code_inline"]
    assert _HOSTILE_CODE_SPAN_EMBEDDED_BACKTICKS in code_span_contents


# ---------------------------------------------------------------------------
# Quality rereview: HTML character-entity references must be neutralized
# too, or every character/literal-syntax escape above can be bypassed by an
# assessment string spelling its trigger characters as an entity reference
# instead of typing them literally. GitHub Flavored Markdown's extended
# autolink extension in particular is applied to *already entity-decoded*
# text -- so a decimal (``&#64;``), hexadecimal (``&#x40;``/``&#X40;``), or
# named (``&commat;``) reference for "@" placed ahead of a dotted domain
# reconstitutes a live bare-email autolink trigger even though the literal
# input string never contains an unescaped "@" byte. The fix must neutralize
# every "&"-led reference in the *input* before this module introduces any
# "&"-led escape of its own (``&lt;``/``&gt;``), so the entities this module
# itself inserts are never re-escaped (double-escaped).
#
# Because this module escapes every literal "&" in the input to "&amp;",
# any well-formed entity reference the input carried (e.g. "&#64;") can
# only ever survive *prefixed* by that inserted "amp;" (i.e. as
# "&amp;#64;") -- the bare, unprefixed reference "&#64;" can never appear
# on its own, since the input's only "&" byte was consumed rewriting it
# to "&amp;". These tests assert directly on that substring invariant,
# which flips from failing to passing exactly when the ampersand
# neutralization fix is present (unlike simulating a full, generic
# entity-decode, which is unnecessary to prove the fix and would not
# discriminate the untreated named entities from the treated ones).
# ---------------------------------------------------------------------------

_HOSTILE_ENTITY_DECIMAL_EMAIL = "contact user&#64;bar_baz.evil.example for access"
_HOSTILE_ENTITY_HEX_LOWER_EMAIL = "contact user&#x40;bar_baz.evil.example for access"
_HOSTILE_ENTITY_HEX_UPPER_EMAIL = "contact user&#X40;bar_baz.evil.example for access"
_HOSTILE_ENTITY_NAMED_COMMAT_EMAIL = "contact user&commat;bar_baz.evil.example for access"
_HOSTILE_ENTITY_NAMED_COMMAT_EMAIL_UPPERCASE = (
    "contact User&COMMAT;Bar_Baz.EVIL.EXAMPLE for access"
)


@pytest.mark.parametrize(
    "hostile_text,bare_entity",
    [
        (_HOSTILE_ENTITY_DECIMAL_EMAIL, "&#64;"),
        (_HOSTILE_ENTITY_HEX_LOWER_EMAIL, "&#x40;"),
        (_HOSTILE_ENTITY_HEX_UPPER_EMAIL, "&#X40;"),
        (_HOSTILE_ENTITY_NAMED_COMMAT_EMAIL, "&commat;"),
        (_HOSTILE_ENTITY_NAMED_COMMAT_EMAIL_UPPERCASE, "&COMMAT;"),
    ],
)
def test_evidence_pack_neutralizes_html_entity_email_autolink_bypass(
    hostile_text, bare_entity
):
    result = _base_result(findings=_escaping_finding(hostile_text))
    text = render.render_evidence_pack(result)
    matrix_start = text.index("## Pass/fail matrix")
    matrix_section = text[matrix_start : text.index("## Residual-risk register")]
    # The rendered pack must never contain the bare, well-formed entity
    # reference for "@" the input carried -- a downstream renderer's
    # entity decode would reconstitute a bare-email autolink trigger
    # from it even though this string never contains a literal,
    # unescaped "@" byte. It may only ever appear prefixed by this
    # module's own "&amp;" escape.
    assert bare_entity not in matrix_section
    assert "&amp;" + bare_entity[1:] in matrix_section
    # The value must still be readable, safe literal text.
    assert "bar_baz" in matrix_section.lower()
    assert "evil.example" in matrix_section.lower()


_HOSTILE_ENTITY_DECIMAL_LT_GT = "before &#60;script&#62;danger&#60;/script&#62; after"
_HOSTILE_ENTITY_HEX_LT_GT = "before &#x3c;script&#x3e;danger&#x3c;/script&#x3e; after"
_HOSTILE_ENTITY_HEX_UPPER_LT_GT = (
    "before &#X3C;script&#X3E;danger&#X3C;/script&#X3E; after"
)
_HOSTILE_ENTITY_NAMED_LT_GT = "before &lt;script&gt;danger&lt;/script&gt; after"


@pytest.mark.parametrize(
    "hostile_text,bare_open,bare_close",
    [
        (_HOSTILE_ENTITY_DECIMAL_LT_GT, "&#60;", "&#62;"),
        (_HOSTILE_ENTITY_HEX_LT_GT, "&#x3c;", "&#x3e;"),
        (_HOSTILE_ENTITY_HEX_UPPER_LT_GT, "&#X3C;", "&#X3E;"),
        (_HOSTILE_ENTITY_NAMED_LT_GT, "&lt;", "&gt;"),
    ],
)
def test_evidence_pack_neutralizes_html_entity_raw_html_bypass(
    hostile_text, bare_open, bare_close
):
    result = _base_result(findings=_escaping_finding(hostile_text))
    text = render.render_evidence_pack(result)
    matrix_start = text.index("## Pass/fail matrix")
    matrix_section = text[matrix_start : text.index("## Residual-risk register")]
    # A downstream renderer's entity decode would reconstitute a literal
    # "<script>" open tag from an entity-encoded "<"/">" pair -- the bare
    # reference must never survive, only this module's own "&amp;"-
    # prefixed rewrite of it.
    assert bare_open not in matrix_section
    assert bare_close not in matrix_section
    assert "&amp;" + bare_open[1:] in matrix_section
    assert "&amp;" + bare_close[1:] in matrix_section
    assert "script" in matrix_section.lower()  # visible text remains readable
    assert "danger" in matrix_section.lower()


def test_evidence_pack_escapes_literal_ampersand_as_readable_text():
    # An ordinary, non-hostile literal "&" (e.g. "R&D") must still be
    # escaped the same way -- the fix applies uniformly regardless of
    # whether the "&" happens to precede entity-shaped text -- and once
    # a compliant renderer decodes this module's own "&amp;" escape, it
    # renders back to a plain, readable "&".
    result = _base_result(findings=_escaping_finding("R&D findings only"))
    text = render.render_evidence_pack(result)
    assert "&amp;D findings only" in text
    assert "R&D findings only" not in text


def test_evidence_pack_does_not_double_escape_own_lt_gt_entities():
    # This module's own "&lt;"/"&gt;" escapes for a literal "<"/">" must
    # never be re-escaped into "&amp;lt;"/"&amp;gt;" by the new
    # ampersand-neutralization step -- the ampersand step must run before
    # "<"/">" are turned into entities, not after.
    result = _base_result(findings=_escaping_finding("value <10 and >5"))
    text = render.render_evidence_pack(result)
    assert "&amp;lt;" not in text
    assert "&amp;gt;" not in text
    assert "&lt;10" in text
    assert "&gt;5" in text


# ---------------------------------------------------------------------------
# governed_actions CLI (Task 10)
#
# Exercises ``governed_actions.parse_args``, ``.resolve_source``,
# ``.assess``, ``.exit_code``, and ``.main`` against real, minimal git
# repositories built under ``tmp_path`` -- this module's own
# ``resolve_source`` shells out to real ``git`` subprocesses, so a genuine
# git checkout (not a hand-built ``SourceRef``) is required to exercise it
# honestly.
# ---------------------------------------------------------------------------


def _run_git_command(args: Sequence[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_governed_actions_target(
    tmp_path: Path,
    *,
    remote: str = "git@github.com:acme/widget.git",
    with_registry: bool = True,
    spec_text: str = "# Spec\n\n## 8. Actions\n\nNo required actions declared.\n",
) -> Path:
    """Build a minimal, real git repository satisfying
    ``inputs.resolve_inputs``'s design/pre-deploy prerequisites: a
    committed ``specs/SPEC.md`` and (unless *with_registry* is False) a
    tool registry file."""
    root = tmp_path / "target"
    root.mkdir()
    _run_git_command(["init", "-q"], root)
    _run_git_command(["config", "user.email", "governed-actions-tests@example.com"], root)
    _run_git_command(["config", "user.name", "Governed Actions Tests"], root)
    _run_git_command(["remote", "add", "origin", remote], root)
    (root / "specs").mkdir()
    (root / "specs" / "SPEC.md").write_text(spec_text, encoding="utf-8")
    if with_registry:
        (root / "tool-registry.json").write_text(
            json.dumps({"tools": []}), encoding="utf-8"
        )
    _run_git_command(["add", "-A"], root)
    _run_git_command(["commit", "-q", "-m", "initial commit"], root)
    return root


def _tracked_and_untracked_snapshot(root: Path) -> Dict[str, int]:
    """A ``relative-path -> mtime_ns`` map for every regular file under
    *root* (``.git`` excluded), used to prove a read-only run genuinely
    left the project untouched."""
    return {
        str(path.relative_to(root)): path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _forbidden_command_runner(_command: Sequence[str]) -> subprocess.CompletedProcess:
    """A stub ``CommandRunner`` simulating a permission-denied ``gh``/``az``
    call: a nonzero exit and no usable stdout, without needing a real
    ``gh``/``az`` binary in the test environment."""
    return subprocess.CompletedProcess(
        args=list(_command), returncode=1, stdout="", stderr="HTTP 403: Forbidden"
    )


def _establish_resolvable_origin_default_branch(root: Path, branch: str = "main") -> str:
    """Give *root* a genuinely resolvable ``refs/remotes/origin/HEAD``,
    as a real checkout that had actually run ``git remote set-head
    origin --auto`` (or an equivalent clone/fetch) would have --
    ``_init_governed_actions_target`` alone never creates this ref, so
    ``governed_actions._resolve_default_branch`` returns ``None`` for
    it, and any ``--live-github`` collection a test drives against it
    is short-circuited by :func:`governed_actions._default_branch_unresolved_finding`
    before ``ghcp.collect_live_github`` (or the stubbed runner behind
    it) is ever actually invoked. Renaming the local branch first makes
    the resulting default branch deterministic regardless of whatever
    ``init.defaultBranch`` happens to be configured in the ambient test
    environment.
    """
    _run_git_command(["branch", "-m", branch], root)
    _run_git_command(
        ["symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}"],
        root,
    )
    return branch


def test_design_without_emit_does_not_modify_project_and_returns_0(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    before = _tracked_and_untracked_snapshot(root)
    exit_status = governed_actions.main(["--target", str(root), "--phase", "design"])
    assert exit_status == 0
    assert _tracked_and_untracked_snapshot(root) == before


def test_gate_maps_nonconforming_pre_deploy_findings_to_exit_1(tmp_path):
    # No governance/installed-packages.json exists, so the observed
    # upstream tuple is all "not-verified" and can never match the
    # assessor's own pinned complete tested tuple: PIN-001/must-fix is
    # unavoidable, and --gate must therefore fail this pre-deploy run.
    root = _init_governed_actions_target(tmp_path)
    exit_status = governed_actions.main(
        ["--target", str(root), "--phase", "pre-deploy", "--gate"]
    )
    assert exit_status == 1


def test_emit_writes_only_the_three_artifacts(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    before = _tracked_and_untracked_snapshot(root)
    exit_status = governed_actions.main(
        ["--target", str(root), "--phase", "design", "--emit"]
    )
    assert exit_status == 0
    after = _tracked_and_untracked_snapshot(root)
    new_files = set(after) - set(before)
    assert new_files == {
        render.DEFAULT_MANIFEST_RELATIVE_PATH.as_posix(),
        render.DEFAULT_EVIDENCE_RELATIVE_PATH.as_posix(),
        render.DEFAULT_APPLY_PLAN_RELATIVE_PATH.as_posix(),
    }
    manifest = json.loads(
        (root / render.DEFAULT_MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    _assert_valid_manifest(manifest)
    plan = json.loads(
        (root / render.DEFAULT_APPLY_PLAN_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    _assert_valid_apply_plan(plan)
    assert (root / render.DEFAULT_EVIDENCE_RELATIVE_PATH).is_file()


def test_invalid_phase_returns_2(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    exit_status = governed_actions.main(
        ["--target", str(root), "--phase", "not-a-real-phase"]
    )
    assert exit_status == 2


def test_missing_phase_argument_returns_2(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    exit_status = governed_actions.main(["--target", str(root)])
    assert exit_status == 2


def test_internal_runner_failure_returns_3(tmp_path, monkeypatch, capsys):
    root = _init_governed_actions_target(tmp_path)

    def _boom(_root):
        raise RuntimeError("simulated internal inventory failure")

    monkeypatch.setattr(governed_actions.inventory, "build_action_inventory", _boom)
    exit_status = governed_actions.main(["--target", str(root), "--phase", "design"])
    assert exit_status == 3
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err


def test_post_deploy_without_staging_resource_group_returns_2(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    exit_status = governed_actions.main(["--target", str(root), "--phase", "post-deploy"])
    assert exit_status == 2


def test_live_github_permission_failure_with_gate_returns_1(tmp_path, monkeypatch):
    # Defect 2 (test correctness): the fixture must actually establish a
    # resolvable default branch, or `_collect_selected_live_evidence`
    # never calls `collect_live_github` at all -- the forbidden runner
    # below would sit unused, and exit 1 would come only from PIN-001
    # (no governance/installed-packages.json), an entirely unrelated
    # finding, not from a live-GitHub permission failure. Recording every
    # command the stubbed runner actually receives proves it was truly
    # invoked, and asserting the explicit `github-live-evidence-
    # unavailable` finding (rather than only the exit code) proves the
    # *reason* the gate failed is genuinely the live GitHub permission
    # failure this test is named for.
    root = _init_governed_actions_target(tmp_path)
    _establish_resolvable_origin_default_branch(root)
    calls: List[Sequence[str]] = []

    def _recording_forbidden_command_runner(
        command: Sequence[str],
    ) -> subprocess.CompletedProcess:
        calls.append(list(command))
        return _forbidden_command_runner(command)

    monkeypatch.setattr(
        governed_actions, "_default_command_runner", _recording_forbidden_command_runner
    )
    exit_status = governed_actions.main(
        [
            "--target",
            str(root),
            "--phase",
            "pre-deploy",
            "--gate",
            "--live-github",
            "--repo",
            "acme/widget",
            "--emit",
        ]
    )
    assert exit_status == 1
    assert calls, "the stubbed live GitHub runner was never invoked"
    assert calls[0][:2] == ["gh", "api"]
    manifest = json.loads(
        (root / render.DEFAULT_MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    reason_codes = {finding["reason_code"] for finding in manifest["findings"]}
    assert "github-live-evidence-unavailable" in reason_codes


def test_dirty_source_is_represented_explicitly_not_hidden(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    # Modify a tracked file without committing: a real, git-detectable
    # dirty working tree (never an untracked-only change, which
    # ``git status --porcelain --untracked-files=no`` never reports).
    (root / "specs" / "SPEC.md").write_text(
        "# Spec\n\n## 8. Actions\n\nUpdated without committing.\n", encoding="utf-8"
    )
    exit_status = governed_actions.main(
        ["--target", str(root), "--phase", "design", "--emit"]
    )
    assert exit_status == 0
    manifest = json.loads(
        (root / render.DEFAULT_MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    assert manifest["source"]["dirty"] is True
    assert manifest["summary"]["verdict"] != "governed"


def test_parse_args_defaults():
    namespace = governed_actions.parse_args(["--phase", "design"])
    assert namespace.target == "."
    assert namespace.phase == "design"
    assert namespace.emit is False
    assert namespace.gate is False
    assert namespace.live_github is False
    assert namespace.manifest_path == render.DEFAULT_MANIFEST_RELATIVE_PATH
    assert namespace.evidence_path == render.DEFAULT_EVIDENCE_RELATIVE_PATH
    assert namespace.apply_plan_path == render.DEFAULT_APPLY_PLAN_RELATIVE_PATH


def test_parse_args_post_deploy_without_staging_resource_group_raises_value_error():
    with pytest.raises(ValueError):
        governed_actions.parse_args(["--phase", "post-deploy"])


def test_resolve_source_non_git_target_is_invalid_input(tmp_path):
    non_git_root = tmp_path / "not-a-repo"
    non_git_root.mkdir()
    with pytest.raises(ValueError):
        governed_actions.resolve_source(non_git_root)


def test_resolve_source_reports_clean_and_dirty_state(tmp_path):
    root = _init_governed_actions_target(tmp_path)
    clean_source = governed_actions.resolve_source(root)
    assert clean_source.repository == "acme/widget"
    assert len(clean_source.commit) == 40
    assert clean_source.dirty is False

    (root / "specs" / "SPEC.md").write_text("dirty change\n", encoding="utf-8")
    dirty_source = governed_actions.resolve_source(root)
    assert dirty_source.dirty is True
    assert dirty_source.commit == clean_source.commit


def test_exit_code_without_gate_is_always_0_regardless_of_findings():
    result = _base_result(
        findings=[_finding("ACT-001", "must-fix", phase="design")],
        phase="design",
    )
    assert governed_actions.exit_code(result, gate=False) == 0


def test_exit_code_gate_fails_on_must_fix():
    result = _base_result(
        findings=[_finding("PIN-001", "must-fix", phase="pre-deploy")],
        phase="pre-deploy",
    )
    assert governed_actions.exit_code(result, gate=True) == 1


def test_exit_code_gate_design_ignores_not_verified():
    result = _base_result(
        findings=[_finding("ACT-001", "not-verified", phase="design")],
        phase="design",
    )
    assert governed_actions.exit_code(result, gate=True) == 0


def test_exit_code_gate_pre_deploy_fails_on_not_verified():
    result = _base_result(
        findings=[_finding("ENF-001", "not-verified", phase="pre-deploy")],
        phase="pre-deploy",
    )
    assert governed_actions.exit_code(result, gate=True) == 1


def test_exit_code_gate_pre_deploy_ignores_optional_unselected_live_not_verified():
    result = _base_result(
        findings=[
            _finding(
                "GHCP-002",
                "not-verified",
                phase="pre-deploy",
                reason_code="branch-protection-not-verified-statically",
            )
        ],
        phase="pre-deploy",
    )
    assert governed_actions.exit_code(result, gate=True) == 0
    assert governed_actions.exit_code(result, gate=False) == 0


# ---------------------------------------------------------------------------
# Task 10 spec-compliance fixes ("complete lifecycle assessment coverage"):
#
# 1. design must emit explicit not-verified markers for runtime and GHCP
#    live coverage instead of staying silent about them.
# 2. pre-deploy must never silently skip APR-001/OUT-001 coverage.
# 3. pre-deploy must populate pins/change_plane/conformance_claims instead
#    of leaving them empty.
# 4. --gate must fail on an unresolved GHCP-002 branch-protection finding
#    when --live-github was explicitly selected, not just when it wasn't.
# 5. the default branch must never silently fall back to "main".
# ---------------------------------------------------------------------------


_OUTPUT_STREAMING_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "conformant-maf"


def test_design_marks_runtime_and_ghcp_checks_not_verified(tmp_path):
    # design only ever builds the action inventory and validates
    # SAFE-completeness; it must never execute an application probe or a
    # GHCP static/live change-plane check. But a SAFE-complete,
    # design-only assessment must also never look like every runtime and
    # GHCP live check is already covered -- so it emits an explicit
    # not-verified marker for each instead of staying silent.
    root = _init_governed_actions_target(tmp_path)
    result = governed_actions.assess(
        contracts.AssessmentOptions(root=root, phase="design", now=_CAPTURED_AT_DEFAULT)
    )
    assert result.phase == "design"

    runtime_markers = [
        finding
        for finding in result.findings
        if finding.finding_id == "ENF-001" and finding.phase == "design"
    ]
    assert len(runtime_markers) == 1
    assert runtime_markers[0].status == "not-verified"

    ghcp_markers = [
        finding
        for finding in result.findings
        if finding.finding_id == "GHCP-002" and finding.phase == "design"
    ]
    assert len(ghcp_markers) == 1
    assert ghcp_markers[0].status == "not-verified"
    # Design's own reason code must be distinct from pre-deploy's own
    # "attempted a static check but couldn't confirm live" GHCP-002 reason
    # code -- design never attempts any GHCP check at all, so reusing
    # that exact reason code here would misrepresent what actually ran.
    assert ghcp_markers[0].reason_code != "branch-protection-not-verified-statically"

    # Preserved gate semantics: these new not-verified findings never fail
    # the design gate -- only an ACT-001/ACT-002 must-fix finding can.
    assert governed_actions.exit_code(result, gate=True) == 0


def test_pre_deploy_reports_approval_and_output_not_verified_when_no_probe_contract(tmp_path):
    # Neither the approval anti-replay probe (APR-001) nor the output
    # mediation probe (OUT-001) may ever be silently skipped: pre-deploy
    # must explicitly report both not-verified when no deterministic,
    # non-fabricated input for them exists, rather than never assessing
    # them at all.
    root = _init_governed_actions_target(tmp_path)
    result = governed_actions.assess(
        contracts.AssessmentOptions(root=root, phase="pre-deploy", now=_CAPTURED_AT_DEFAULT)
    )

    approval_findings = [finding for finding in result.findings if finding.finding_id == "APR-001"]
    assert len(approval_findings) == 1
    assert approval_findings[0].status == "not-verified"

    output_findings = [finding for finding in result.findings if finding.finding_id == "OUT-001"]
    assert len(output_findings) == 1
    assert output_findings[0].status == "not-verified"


def test_output_coverage_runs_real_probe_when_output_contract_available():
    # No deterministic, non-business-specific approval binding can ever
    # exist (subject/tenant/policy would have to be fabricated), so
    # APR-001 always stays an explicit not-verified marker. OUT-001,
    # though, is wired for real -- using the same fixed, synthetic "deny"
    # verdict `run_privacy_probe_set` already uses for AUD-001 -- whenever
    # the target actually declares a usable output probe contract.
    probe_results, findings = governed_actions._run_output_coverage(
        _OUTPUT_STREAMING_FIXTURE, "pre-deploy"
    )
    assert len(probe_results) == 1
    assert probe_results[0].status == "pass"
    assert findings == ()


def test_pre_deploy_populates_pins_change_plane_and_conformance_claims(tmp_path):
    # pins/change_plane/conformance_claims must be populated with
    # deterministic, payload-free summaries of data pre-deploy already
    # computes (the observed MAF tuple comparison and the GHCP
    # change-plane assessment), not left empty.
    root = _init_governed_actions_target(tmp_path)
    result = governed_actions.assess(
        contracts.AssessmentOptions(root=root, phase="pre-deploy", now=_CAPTURED_AT_DEFAULT)
    )

    assert result.pins
    assert result.pins["dependencies"]
    assert result.pins["specifications"]
    for entry in tuple(result.pins["dependencies"]) + tuple(result.pins["specifications"]):
        assert set(entry) == {"name", "version"}

    assert result.change_plane
    assert result.change_plane["repository"] == "acme/widget"
    assert result.change_plane["workflows"] == ()
    assert result.change_plane["identities"] == ()

    assert result.conformance_claims
    claim_ids = {claim["claim_id"] for claim in result.conformance_claims}
    # The one boolean GHCP control (never a Status string) must never be
    # coerced into a conformance-claim status.
    assert "ghcp_internal_loop_intercepted" not in claim_ids
    assert "ghcp_codeowners" in claim_ids
    for claim in result.conformance_claims:
        assert set(claim) == {"claim_id", "description", "status", "evidence_refs"}


def test_exit_code_gate_pre_deploy_fails_on_selected_live_github_not_verified():
    # When --live-github was explicitly selected, an unresolved
    # branch-protection finding must fail the gate -- the exemption is
    # only for a live capability that was never even requested.
    result = _base_result(
        findings=[
            _finding(
                "GHCP-002",
                "not-verified",
                phase="pre-deploy",
                reason_code="branch-protection-not-verified-statically",
            )
        ],
        phase="pre-deploy",
        live_github_selected=True,
    )
    assert governed_actions.exit_code(result, gate=True) == 1


@pytest.mark.parametrize(
    "reason_code",
    [
        "azure-login-not-verified-statically",
        "identity-separation-not-verified-statically",
    ],
)
def test_exit_code_gate_pre_deploy_fails_on_selected_live_azure_not_verified(reason_code):
    # Defect 1 (High): a selected-but-unresolved live Azure finding must
    # fail the gate exactly like a selected-but-unresolved live GitHub
    # one does -- these two GHCP reason codes must never be exempted
    # merely because they are *capable* of being "statically
    # unverifiable"; they are exempt only when live Azure evidence was
    # never even requested (see the paired exemption test below).
    result = _base_result(
        findings=[
            _finding(
                "GHCP-005" if reason_code.startswith("azure-login") else "GHCP-006",
                "not-verified",
                phase="pre-deploy",
                reason_code=reason_code,
            )
        ],
        phase="pre-deploy",
        live_azure_selected=True,
    )
    assert governed_actions.exit_code(result, gate=True) == 1


@pytest.mark.parametrize(
    "reason_code",
    [
        "azure-login-not-verified-statically",
        "identity-separation-not-verified-statically",
    ],
)
def test_exit_code_gate_pre_deploy_exempts_unselected_live_azure_not_verified(reason_code):
    # An optional, *unselected* live Azure capability (no --subscription/
    # --staging-resource-group/--deploy-identity given at all) must
    # remain gate-exempt, exactly like an unselected live GitHub
    # branch-protection finding.
    result = _base_result(
        findings=[
            _finding(
                "GHCP-005" if reason_code.startswith("azure-login") else "GHCP-006",
                "not-verified",
                phase="pre-deploy",
                reason_code=reason_code,
            )
        ],
        phase="pre-deploy",
        live_azure_selected=False,
    )
    assert governed_actions.exit_code(result, gate=True) == 0


def test_resolve_default_branch_returns_none_when_local_ref_unavailable(tmp_path):
    # No arbitrary "main" fallback: when the local checkout has no
    # `origin/HEAD` ref recorded (e.g. `git remote set-head origin
    # --auto` was never run), the ambiguity is reported honestly as
    # unresolved rather than silently guessed at.
    root = _init_governed_actions_target(tmp_path)
    assert governed_actions._resolve_default_branch(root) is None


def test_collect_selected_live_evidence_never_calls_github_with_unresolved_default_branch(tmp_path, monkeypatch):
    # An unresolved default branch must never be silently converted into
    # a guessed "main": collect_live_github must never even be called
    # with a fabricated branch name, and the ambiguity itself must
    # surface as an explicit not-verified finding.
    root = _init_governed_actions_target(tmp_path)

    def _fail_if_called(repository, default_branch, run):
        raise AssertionError(
            "collect_live_github must not be called with an unresolved default branch"
        )

    monkeypatch.setattr(governed_actions.ghcp, "collect_live_github", _fail_if_called)
    options = contracts.AssessmentOptions(
        root=root,
        phase="pre-deploy",
        live_github=True,
        repository="acme/widget",
        now=_CAPTURED_AT_DEFAULT,
    )
    live_github, live_azure, findings = governed_actions._collect_selected_live_evidence(
        root, options, None
    )
    assert live_github is None
    assert any(finding.reason_code == "github-live-evidence-unavailable" for finding in findings)


def test_collect_selected_live_evidence_calls_github_when_default_branch_resolved(tmp_path, monkeypatch):
    # Defect 2's other half: once a default branch genuinely *is*
    # resolvable, --live-github must actually invoke live collection --
    # this is the direct, unit-level counterpart to
    # ``test_live_github_permission_failure_with_gate_returns_1``'s
    # end-to-end proof that the stubbed runner is truly exercised.
    root = _init_governed_actions_target(tmp_path)
    calls: List[Tuple[str, str]] = []

    def _record_and_fail(repository, default_branch, run):
        calls.append((repository, default_branch))
        return governed_actions.ghcp.LiveEvidenceResult(
            status="not-verified",
            data={"error_class": "cli-error", "exit_code": 1},
            evidence=(),
            finding=_finding(
                "GHCP-002",
                "not-verified",
                phase="pre-deploy",
                reason_code="github-live-evidence-unavailable",
            ),
        )

    monkeypatch.setattr(governed_actions.ghcp, "collect_live_github", _record_and_fail)
    options = contracts.AssessmentOptions(
        root=root,
        phase="pre-deploy",
        live_github=True,
        repository="acme/widget",
        now=_CAPTURED_AT_DEFAULT,
    )
    live_github, live_azure, findings = governed_actions._collect_selected_live_evidence(
        root, options, "main"
    )
    assert calls == [("acme/widget", "main")]
    assert live_github == {"error_class": "cli-error", "exit_code": 1}
    assert any(finding.reason_code == "github-live-evidence-unavailable" for finding in findings)


def test_selected_live_github_with_unresolvable_default_branch_reports_not_verified(tmp_path):
    # End-to-end: --live-github selected against a checkout with no
    # resolvable default branch must never crash (exit 3) and never
    # silently pass -- it surfaces as an explicit not-verified finding.
    root = _init_governed_actions_target(tmp_path)
    exit_status = governed_actions.main(
        [
            "--target",
            str(root),
            "--phase",
            "pre-deploy",
            "--live-github",
            "--repo",
            "acme/widget",
            "--emit",
        ]
    )
    assert exit_status == 0
    manifest = json.loads(
        (root / render.DEFAULT_MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    reason_codes = {finding["reason_code"] for finding in manifest["findings"]}
    assert "github-live-evidence-unavailable" in reason_codes


# ---------------------------------------------------------------------------
# Defect 1 (High): selected live Azure evidence that is genuinely
# unresolved must fail the gate; unselected optional live Azure evidence
# must remain gate-exempt. End-to-end regressions against the real
# ``assess`` pipeline (real inventory/mediation/GHCP change-plane
# analysis, a stubbed-only-at-the-command-runner-boundary
# ``ghcp.collect_live_azure``), isolating the exit-code assertion to
# exactly the one finding this defect concerns via ``dataclasses.replace``
# so the assertion is never a coincidental side effect of some other,
# unrelated pre-deploy finding (this repo's own pre-deploy gate has
# several always-not-verified findings of its own, e.g. APR-001).
# ---------------------------------------------------------------------------


def _write_separate_deploy_and_test_identity_workflows(root: Path) -> None:
    """Commit two workflow files whose declared identity references are
    two distinct GitHub Actions secrets -- one used only by a
    build/test job, the other only by a deploy job -- so GHCP-006
    (build/test/deploy identity separation) has real, non-empty
    ``deploy_identity_refs``/``non_deploy_identity_refs`` to compare,
    and (absent a live ``identity_principal_ids`` mapping resolving them)
    lands on ``identity-separation-not-verified-statically`` rather than
    ``not-applicable``. Every step here authenticates via OIDC/WIF
    (``client-id``/``tenant-id``/``subscription-id`` from ``secrets.*``,
    no client-secret), so this fixture never itself trips GHCP-004/005.
    """
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "ci.yml").write_text(
        textwrap.dedent(
            """\
            name: CI
            on:
              pull_request:
            permissions:
              contents: read
            jobs:
              test:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.TEST_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - name: Run CTK and application probes
                    run: |
                      python -m ctk run-vectors
                      python -m probes run-application-probe
            """
        ),
        encoding="utf-8",
    )
    (workflow_dir / "deploy.yml").write_text(
        textwrap.dedent(
            """\
            name: Deploy
            on:
              workflow_dispatch:
            permissions:
              contents: read
              id-token: write
            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  id-token: write
                steps:
                  - uses: actions/checkout@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                  - uses: azure/login@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
                    with:
                      client-id: ${{ secrets.DEPLOY_CLIENT_ID }}
                      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
                      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
                  - uses: azure/webapps-deploy@0ad4c47a9e566829e19b6099ee3458ac923f5d3c
            """
        ),
        encoding="utf-8",
    )
    _run_git_command(["add", "-A"], root)
    _run_git_command(["commit", "-q", "-m", "add azure identity workflows"], root)


def _successful_azure_command_runner(command: Sequence[str]) -> subprocess.CompletedProcess:
    """A stub Azure ``CommandRunner`` returning a genuinely successful,
    well-shaped response for every command ``ghcp.collect_live_azure``
    issues -- proving Defect 1's fix operates even when live Azure
    collection itself fully succeeds: the real collector is necessarily
    scoped to the single ``--deploy-identity`` the CLI was given, so its
    returned payload never carries an ``identity_principal_ids``
    mapping resolving *both* the deploy and the non-deploy identity
    reference, and GHCP-006 therefore stays genuinely, honestly
    unresolved rather than a fabricated ``pass``.
    """
    joined = " ".join(command)
    if "federated-credential" in joined:
        return subprocess.CompletedProcess(args=list(command), returncode=0, stdout="[]", stderr="")
    if "role" in joined and "assignment" in joined:
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "roleDefinitionName": "Contributor",
                        "principalId": "11111111-1111-1111-1111-111111111111",
                    }
                ]
            ),
            stderr="",
        )
    if "role" in joined and "definition" in joined:
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout=json.dumps([{"roleName": "Contributor"}]),
            stderr="",
        )
    raise AssertionError(f"unexpected azure command in test stub: {command!r}")


def _identity_separation_finding(result: contracts.AssessmentResult) -> contracts.Finding:
    return next(f for f in result.findings if f.finding_id == "GHCP-006")


def test_selected_live_azure_unresolved_identity_separation_fails_gate(tmp_path, monkeypatch):
    # Defect 1, primary regression: --subscription/--staging-resource-group/
    # --deploy-identity are all selected, and the stubbed 'az' runner
    # succeeds outright -- yet the real collect_live_azure payload still
    # cannot resolve genuinely distinct identities (it only ever knows
    # about the one --deploy-identity it was given), so GHCP-006 stays
    # not-verified. Selected-but-unresolved live Azure evidence must fail
    # the gate.
    root = _init_governed_actions_target(tmp_path)
    _write_separate_deploy_and_test_identity_workflows(root)
    monkeypatch.setattr(
        governed_actions, "_default_command_runner", _successful_azure_command_runner
    )
    options = contracts.AssessmentOptions(
        root=root,
        phase="pre-deploy",
        subscription="SUBSCRIPTION",
        staging_resource_group="STAGING_RG",
        deploy_identity="DEPLOY_IDENTITY",
        live_azure=True,
        now=_CAPTURED_AT_DEFAULT,
    )
    result = governed_actions.assess(options)
    assert result.live_azure_selected is True
    finding = _identity_separation_finding(result)
    assert finding.status == "not-verified"
    assert finding.reason_code == "identity-separation-not-verified-statically"

    isolated = dataclasses.replace(result, findings=(finding,))
    assert governed_actions.exit_code(isolated, gate=True) == 1


def test_unselected_optional_live_azure_identity_separation_exemption_remains(tmp_path):
    # Paired regression: the exact same ambiguous static workflow set,
    # but with no --subscription/--staging-resource-group/--deploy-identity
    # at all -- live Azure was never selected, so GHCP-006 staying
    # not-verified must remain gate-exempt exactly as before this fix.
    root = _init_governed_actions_target(tmp_path)
    _write_separate_deploy_and_test_identity_workflows(root)
    options = contracts.AssessmentOptions(
        root=root,
        phase="pre-deploy",
        now=_CAPTURED_AT_DEFAULT,
    )
    result = governed_actions.assess(options)
    assert result.live_azure_selected is False
    finding = _identity_separation_finding(result)
    assert finding.status == "not-verified"
    assert finding.reason_code == "identity-separation-not-verified-statically"

    isolated = dataclasses.replace(result, findings=(finding,))
    assert governed_actions.exit_code(isolated, gate=True) == 0


@pytest.mark.parametrize("phase", ["design", "pre-deploy", "post-deploy"])
def test_assess_binds_live_azure_selected_from_options_every_phase(tmp_path, monkeypatch, phase):
    # Binding must happen for every phase (design included), never only
    # for the phases that currently happen to read it -- so the field
    # can never silently drift from ``AssessmentOptions.live_azure``.
    root = _init_governed_actions_target(tmp_path)
    monkeypatch.setattr(
        governed_actions, "_default_command_runner", _successful_azure_command_runner
    )
    kwargs: Dict[str, object] = dict(
        root=root,
        phase=phase,
        now=_CAPTURED_AT_DEFAULT,
        live_azure=True,
        subscription="SUBSCRIPTION",
        staging_resource_group="STAGING_RG",
        deploy_identity="DEPLOY_IDENTITY",
    )
    if phase == "post-deploy":
        kwargs["staging"] = True
    result = governed_actions.assess(contracts.AssessmentOptions(**kwargs))
    assert result.live_azure_selected is True
