"""Deterministic artifact rendering for threadlight-governed-actions
(Task 9, design sections 12-15).

This module turns one already-computed, read-only :class:`AssessmentResult`
into the three evidence artifacts threadlight-governed-actions may ever
write: the canonical machine-readable manifest
(``tests/governed-actions-manifest.json``), the customer-facing Markdown
evidence pack (``docs/governance/evidence-pack.md``), and the namespaced,
never-self-applying remediation plan
(``tests/governed-actions-apply-plan.json``).

Nothing here re-assesses the target, calls a live GitHub/Azure API, or
invents customer-specific policy: every value rendered either comes
straight from the supplied :class:`AssessmentResult` or is one of this
module's own fixed, documented constants (schema name, assessor identity,
the mandatory residual-risk register, the finding-catalog-derived
remediation-kind table). An owner is only ever rendered when the target
repository's own action inventory declared one; otherwise the rendered
remediation item is ``manual`` with ``owner: null`` rather than a guess.

Rendering is a pure function of the assessment's own data: the exact same
logical assessment (in any input ordering -- actions, paths, probes,
findings, evidence all shuffled) must produce byte-identical canonical
manifest/apply-plan JSON, so every collection is explicitly, stably sorted
before it is rendered. No wall clock is ever read here; ``captured_at`` and
freshness are derived entirely from the evidence timestamps already present
on the assessment (falling back to the same fixed epoch
``AssessmentOptions.now`` defaults to elsewhere in this project), so tests
that inject a fixed assessment always get byte-for-byte deterministic
output.

Run with:
    python3 -m pytest skills/threadlight-governed-actions/tests/test_render_cli.py -q
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import canonical
import contracts
from contracts import (
    ActionRecord,
    AssessmentResult,
    EvidenceRef,
    Finding,
    PathRecord,
    ProbeResult,
    Status,
)


class ArtifactWriteError(RuntimeError):
    """Raised when :func:`write_artifacts` cannot durably replace the
    complete three-artifact set.

    Whenever this is raised, the on-disk artifact set is guaranteed to be
    exactly what it was before :func:`write_artifacts` was called -- never
    a mix of old and new generations -- because every backup made during
    the attempt is restored (and every newly created, previously
    nonexistent file removed) before this is raised.
    """


# ---------------------------------------------------------------------------
# Fixed identity/schema constants
# ---------------------------------------------------------------------------

MANIFEST_SCHEMA = "threadlight-governed-actions-manifest/v1"
APPLY_PLAN_SCHEMA = "threadlight-governed-actions-apply-plan/v1"
ADAPTER_NAME = "maf/v1"
ASSESSOR_NAME = "threadlight-governed-actions"

#: Same fallback the rest of this project uses (``AssessmentOptions.now``'s
#: own default) when no evidence carries a trustworthy collection
#: timestamp -- never a real wall-clock read, so golden output stays
#: byte-for-byte reproducible in tests that never populate every
#: ``collected_at``.
FALLBACK_TIMESTAMP = "1970-01-01T00:00:00Z"

#: Design section 12.2: every manifest's evidence window is valid for
#: exactly one day before it must be recollected.
FRESHNESS_VALID_FOR_HOURS = 24

#: Default artifact locations, repository-relative, exactly as design
#: section 12.1 names them. Callers may override any of the three paths
#: passed to :func:`write_artifacts`, but every resolved destination must
#: still land under the assessed root.
DEFAULT_MANIFEST_RELATIVE_PATH = Path("tests/governed-actions-manifest.json")
DEFAULT_EVIDENCE_RELATIVE_PATH = Path("docs/governance/evidence-pack.md")
DEFAULT_APPLY_PLAN_RELATIVE_PATH = Path("tests/governed-actions-apply-plan.json")

#: Ordering used to rank finding/summary status severity, worst first.
#: Findings sort by ``(status rank, finding_id, reason_code)``.
STATUS_ORDER: Tuple[Status, ...] = (
    "must-fix",
    "should-fix",
    "not-verified",
    "pass",
    "not-applicable",
)
_STATUS_RANK: Dict[str, int] = {status: index for index, status in enumerate(STATUS_ORDER)}


# ---------------------------------------------------------------------------
# Mandatory residual-risk register
#
# These are always present in every rendered manifest/evidence pack,
# regardless of what the assessed target looks like: they describe this
# assessor's own inherent, structural limitations (a cooperative/alpha
# interception seam, conformance that is never certification, provider-hosted
# tools it cannot equivalently prove, live evidence that goes stale, pinned
# upstream alpha/experimental drift, and audit retention/deletion/residency/
# legal-hold policy it never observes) -- never a customer-specific risk.
# ---------------------------------------------------------------------------

_MANDATORY_RESIDUAL_RISKS: Tuple[Dict[str, object], ...] = (
    {
        "residual_risk_id": "RISK-COOPERATIVE-HOST-TRUST",
        "finding_id": "ENF-002",
        "description": (
            "Agent Hooks is a cooperative, alpha-stage interception seam and "
            "is never treated as a security boundary by this assessor: a "
            "caller that skips the hook is only caught when an independent, "
            "verified compensating control exists for that bypass surface."
        ),
    },
    {
        "residual_risk_id": "RISK-NON-CERTIFICATION",
        "finding_id": "PIN-001",
        "description": (
            "Conformance claims, conformance reports, and pinned "
            "probe-suite/CTK results recorded in this manifest are this "
            "assessor's own read-only observations -- they are never a "
            "certification of the assessed system by threadlight or any "
            "third party."
        ),
    },
    {
        "residual_risk_id": "RISK-PROVIDER-HOSTED-LIMITATIONS",
        "finding_id": "MED-003",
        "description": (
            "Provider-hosted tool side effects without an equivalent, "
            "independently verified server-side control are outside what "
            "this assessor's mediation model can prove; a passing result "
            "here never implies the provider itself was audited."
        ),
    },
    {
        "residual_risk_id": "RISK-LIVE-EVIDENCE-FRESHNESS",
        "finding_id": "GHCP-002",
        "description": (
            "Live evidence (branch protection, required checks, identity "
            "separation) is only as current as its own trustworthy "
            "collection timestamp. An evidence entry whose collected_at "
            "is missing or does not parse as a real instant is rendered "
            "with collected_at null and live_verified false, and is "
            "excluded from this manifest's freshness computation entirely. "
            "When no evidence carries a trustworthy timestamp, "
            "freshness.status is reported stale; when the oldest "
            "trustworthy timestamp falls outside this manifest's freshness "
            "window, freshness.status is reported expired -- neither is "
            "ever assumed to still be fresh."
        ),
    },
    {
        "residual_risk_id": "RISK-UPSTREAM-ALPHA-DRIFT",
        "finding_id": "PIN-001",
        "description": (
            "Agent Hooks, its SDK, and the Model/Agent Framework "
            "integration this manifest pins are alpha/experimental "
            "upstream artifacts; drift from the pinned tuple without "
            "rerunning the Conformance Test Kit and application probes "
            "invalidates every finding that depended on it."
        ),
    },
    {
        "residual_risk_id": "RISK-AUDIT-RETENTION-UNEVIDENCED",
        "finding_id": "AUD-001",
        "description": (
            "This assessor observes only that a payload-free audit record "
            "is emitted per consequential-action decision; audit "
            "retention, deletion, data-residency, and legal-hold policy "
            "are the target deployment's own operational responsibility "
            "and are never evidenced or inferred here."
        ),
    },
)

#: The catch-all every finding without its own explicit
#: ``residual_risk_ref`` (or with one that does not resolve to any entry in
#: the assembled residual-risk register) falls back to, so every rendered
#: finding always references a valid residual-risk id without this module
#: ever inventing a bespoke risk statement per finding.
_CATCH_ALL_RESIDUAL_RISK: Dict[str, object] = {
    "residual_risk_id": "RISK-BOUNDED-ASSESSMENT-SCOPE",
    "finding_id": "ACT-001",
    "description": (
        "This finding reflects only this assessor's own bounded, "
        "read-only observation of the assessed repository/deployment at "
        "the recorded source commit; it does not extend to scope, time "
        "windows, or systems this assessment never observed."
    ),
}

_ALL_RISK_IDS_ORDER_HINT = tuple(
    entry["residual_risk_id"] for entry in _MANDATORY_RESIDUAL_RISKS
) + (_CATCH_ALL_RESIDUAL_RISK["residual_risk_id"],)


# ---------------------------------------------------------------------------
# Remediation-kind table
#
# One fixed, documented remediation kind per finding-catalog id. A finding
# whose remediation genuinely depends on a customer's own, otherwise-unknown
# policy decision (who approves a consequential action, what a
# provider-hosted tool's own compensating control looks like, how a GitHub
# org's branch-protection/identity separation is administered) is "manual"
# rather than a code edit this assessor could plausibly author on the
# customer's behalf.
# ---------------------------------------------------------------------------

_REMEDIATION_KIND_BY_FINDING_ID: Dict[str, str] = {
    "ACT-001": "repo-edit",
    "ACT-002": "repo-edit",
    "MED-001": "repo-edit",
    "MED-002": "repo-edit",
    "MED-003": "manual",
    "ENF-001": "repo-edit",
    "ENF-002": "manual",
    "APR-001": "manual",
    "OUT-001": "repo-edit",
    "AUD-001": "repo-edit",
    "PIN-001": "repo-edit",
    "GHCP-001": "repo-edit",
    "GHCP-002": "manual",
    "GHCP-003": "repo-edit",
    "GHCP-004": "repo-edit",
    "GHCP-005": "repo-edit",
    "GHCP-006": "manual",
    "OPS-001": "repo-edit",
}

#: Statuses whose findings need exactly one apply-plan remediation item.
#: ``pass``/``not-applicable`` need none.
_REMEDIATION_REQUIRED_STATUSES: Tuple[Status, ...] = (
    "must-fix",
    "should-fix",
    "not-verified",
)


# ---------------------------------------------------------------------------
# Sorting helpers
# ---------------------------------------------------------------------------


def _finding_sort_key(finding: Finding) -> Tuple[int, str, str]:
    return (
        _STATUS_RANK.get(finding.status, len(STATUS_ORDER)),
        finding.finding_id,
        finding.reason_code,
    )


def _sorted_actions(actions: Sequence[ActionRecord]) -> List[ActionRecord]:
    return sorted(actions, key=lambda action: action.action_id)


def _sorted_paths(paths: Sequence[PathRecord]) -> List[PathRecord]:
    return sorted(paths, key=lambda path: (path.action_id, path.mode, path.path_id))


def _sorted_probes(probes: Sequence[ProbeResult]) -> List[ProbeResult]:
    return sorted(probes, key=lambda probe: probe.probe_id)


def _sorted_findings(findings: Sequence[Finding]) -> List[Finding]:
    return sorted(findings, key=_finding_sort_key)


def _sorted_evidence(evidence: Sequence[EvidenceRef]) -> List[EvidenceRef]:
    return sorted(evidence, key=lambda ref: ref.evidence_id)


def _sorted_mappings(items: Sequence[Mapping[str, object]], key: str) -> List[Dict[str, object]]:
    return sorted((dict(item) for item in items), key=lambda item: str(item[key]))


# ---------------------------------------------------------------------------
# Record -> schema-shaped dict converters
#
# Every converter below emits *exactly* the keys the corresponding manifest
# schema $def declares (each of those $defs sets "additionalProperties":
# false) -- tuple fields become plain lists so the rendered value is
# actually a JSON array under ``jsonschema``'s default type checker, which
# only recognizes ``list`` for ``"type": "array"``, not ``tuple``.
# ---------------------------------------------------------------------------


def _action_to_dict(action: ActionRecord) -> Dict[str, object]:
    return {
        "action_id": action.action_id,
        "display_name": action.display_name,
        "aliases": list(action.aliases),
        "owner": action.owner,
        "declaration_refs": list(action.declaration_refs),
        "implementation_refs": list(action.implementation_refs),
        "input_schema_sha256": action.input_schema_sha256,
        "output_schema_sha256": action.output_schema_sha256,
        "source": action.source,
        "consequence": action.consequence,
        "secondary_consequences": list(action.secondary_consequences),
        "reversible": action.reversible,
        "compensation_ref": action.compensation_ref,
        "execution_modes": list(action.execution_modes),
        "provider_hosted": action.provider_hosted,
        "approval_required": action.approval_required,
        "policy_ids": list(action.policy_ids),
        "known_runtime_paths": list(action.known_runtime_paths),
        "inventory_status": action.inventory_status,
    }


def _path_to_dict(path: PathRecord) -> Dict[str, object]:
    return {
        "path_id": path.path_id,
        "action_id": path.action_id,
        "mode": path.mode,
        "nodes": list(path.nodes),
        "pre_action_seam": path.pre_action_seam,
        "equivalent_control_ref": path.equivalent_control_ref,
        "covered": path.covered,
        "status": path.status,
        "evidence_refs": list(path.evidence_refs),
    }


def _probe_to_dict(probe: ProbeResult) -> Dict[str, object]:
    return {
        "probe_id": probe.probe_id,
        "action_id": probe.action_id,
        "path_id": probe.path_id,
        "status": probe.status,
        "reason_code": probe.reason_code,
        "expected": probe.expected,
        "observed": probe.observed,
        "evidence_refs": list(probe.evidence_refs),
    }


def _evidence_to_dict(ref: EvidenceRef) -> Dict[str, object]:
    # A ``collected_at`` that is present but does not parse as a genuine
    # RFC 3339 instant (e.g. a calendar-impossible date) is untrustworthy:
    # it is degraded to the schema's own "absent" representation (``None``)
    # rather than passed through raw, which would otherwise leave a
    # manifest that can never validate against the timestamp format. The
    # same degradation also clears the fields that would otherwise imply a
    # timestamp could be trusted (``freshness_seconds``, ``live_verified``).
    collected_at = ref.collected_at
    freshness_seconds = ref.freshness_seconds
    live_verified = ref.live_verified
    if collected_at is not None and _try_parse_rfc3339(collected_at) is None:
        collected_at = None
        freshness_seconds = None
        live_verified = False
    return {
        "evidence_id": ref.evidence_id,
        "kind": ref.kind,
        "source": ref.source,
        "sha256": ref.sha256,
        "collected_at": collected_at,
        "freshness_seconds": freshness_seconds,
        "live_verified": live_verified,
        "phase": ref.phase,
        "repository": ref.repository,
        "source_commit": ref.source_commit,
        "target_environment": ref.target_environment,
        "policy_set_sha256": ref.policy_set_sha256,
    }


def _finding_to_dict(finding: Finding, risk_ids: Set[str]) -> Dict[str, object]:
    return {
        "finding_id": finding.finding_id,
        "status": finding.status,
        "phase": finding.phase,
        "plane": finding.plane,
        "reason_code": finding.reason_code,
        "summary": finding.summary,
        "details": finding.details,
        "affected_actions": sorted(finding.affected_actions),
        "affected_paths": sorted(finding.affected_paths),
        "evidence_refs": sorted(finding.evidence_refs),
        "remediation_ids": sorted(finding.remediation_ids),
        "residual_risk_ref": _resolve_residual_risk_ref(finding, risk_ids),
    }


def _resolve_residual_risk_ref(finding: Finding, risk_ids: Set[str]) -> str:
    ref = finding.residual_risk_ref
    if ref and ref in risk_ids:
        return ref
    return str(_CATCH_ALL_RESIDUAL_RISK["residual_risk_id"])


def _assemble_residual_risks(result: AssessmentResult) -> List[Dict[str, object]]:
    merged: Dict[str, Dict[str, object]] = {
        str(entry["residual_risk_id"]): dict(entry) for entry in _MANDATORY_RESIDUAL_RISKS
    }
    merged[str(_CATCH_ALL_RESIDUAL_RISK["residual_risk_id"])] = dict(_CATCH_ALL_RESIDUAL_RISK)
    for extra in result.residual_risks:
        merged[str(extra["residual_risk_id"])] = dict(extra)
    return sorted(merged.values(), key=lambda entry: str(entry["residual_risk_id"]))


def _normalize_pin(pin: Mapping[str, object]) -> Dict[str, object]:
    return {"name": pin.get("name"), "version": pin.get("version")}


def _normalize_pins(pins: Mapping[str, object]) -> Dict[str, object]:
    dependencies = sorted(
        (_normalize_pin(pin) for pin in pins.get("dependencies", ())),
        key=lambda pin: str(pin["name"]),
    )
    specifications = sorted(
        (_normalize_pin(pin) for pin in pins.get("specifications", ())),
        key=lambda pin: str(pin["name"]),
    )
    probe_suite = pins.get("probe_suite") or {
        "name": f"{ASSESSOR_NAME}-probe-suite",
        "version": contracts.ASSESSOR_VERSION,
    }
    return {
        "dependencies": dependencies,
        "specifications": specifications,
        "probe_suite": _normalize_pin(probe_suite),
    }


def _normalize_policy_hashes(policy_hashes: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    return [
        {"path": str(entry["path"]), "sha256": str(entry["sha256"])}
        for entry in _sorted_mappings(policy_hashes, "path")
    ]


def _normalize_change_plane_workflow(entry: Mapping[str, object]) -> Dict[str, object]:
    return {"path": str(entry["path"]), "sha256": str(entry["sha256"])}


def _normalize_change_plane_identity(entry: Mapping[str, object]) -> Dict[str, object]:
    return {"identity": str(entry["identity"]), "kind": str(entry["kind"])}


def _normalize_change_plane(change_plane: Mapping[str, object], default_repository: str) -> Dict[str, object]:
    repository = change_plane.get("repository", default_repository)
    workflows = sorted(
        (_normalize_change_plane_workflow(entry) for entry in change_plane.get("workflows", ())),
        key=lambda entry: str(entry["path"]),
    )
    identities = sorted(
        (_normalize_change_plane_identity(entry) for entry in change_plane.get("identities", ())),
        key=lambda entry: str(entry["identity"]),
    )
    return {"repository": repository, "workflows": workflows, "identities": identities}


def _normalize_claim(entry: Mapping[str, object]) -> Dict[str, object]:
    return {
        "claim_id": str(entry["claim_id"]),
        "description": str(entry["description"]),
        "status": entry["status"],
        "evidence_refs": sorted(str(item) for item in entry.get("evidence_refs", ())),
    }


def _normalize_report(entry: Mapping[str, object]) -> Dict[str, object]:
    return {
        "report_id": str(entry["report_id"]),
        "tool": str(entry["tool"]),
        "version": str(entry["version"]),
        "generated_at": entry["generated_at"],
        "summary": str(entry["summary"]),
        "evidence_refs": sorted(str(item) for item in entry.get("evidence_refs", ())),
    }


def _sorted_claims(claims: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    return sorted((_normalize_claim(entry) for entry in claims), key=lambda entry: entry["claim_id"])


def _sorted_reports(reports: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    return sorted((_normalize_report(entry) for entry in reports), key=lambda entry: entry["report_id"])


# ---------------------------------------------------------------------------
# Timestamp / freshness derivation -- never a wall-clock read.
#
# Every RFC 3339 timestamp is parsed to an aware ``datetime`` before it is
# ever compared, sorted, or added to: two evidence timestamps written in
# different UTC offsets (or with different fractional-second precision)
# must order by the actual instant they name, never by an accidental
# lexical string comparison that can silently disagree with it. A raw,
# unparseable ``collected_at`` (one that matches the schema's own
# ``pattern`` but is not a real calendar instant, e.g. a nonexistent
# 30th of February, or one that fails to parse for any other reason) is
# never allowed to escape as an uncaught ``ValueError`` here -- it is
# instead treated exactly like a *missing* ``collected_at``: excluded from
# every timestamp aggregation so freshness degrades conservatively (to the
# schema's own ``stale``/``null`` vocabulary) rather than crashing or
# silently trusting an untrustworthy value.
# ---------------------------------------------------------------------------


def _parse_rfc3339(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _try_parse_rfc3339(value: Optional[str]) -> Optional[datetime]:
    """Parse *value* to an aware ``datetime``, or ``None`` if it is absent
    or cannot be parsed as a real RFC 3339 instant.

    Never raises: any parsing failure (``ValueError`` for a nonexistent
    calendar date/time such as a leap-day-31st, ``OverflowError`` for a
    year outside the platform's representable range, or ``TypeError`` for
    a non-string) is caught and reported as ``None`` -- an untrustworthy or
    absent timestamp look identical to every caller downstream.
    """
    if not value:
        return None
    try:
        return _parse_rfc3339(value)
    except (ValueError, OverflowError, TypeError):
        return None


def _format_rfc3339(moment: datetime) -> str:
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _add_hours(moment: datetime, hours: int) -> datetime:
    return moment + timedelta(hours=hours)


def _trustworthy_timestamp_pairs(result: AssessmentResult) -> List[Tuple[datetime, str]]:
    """Every evidence ``collected_at`` that parses to a real instant,
    paired with its own original (never reformatted) string, sorted by
    ``(instant, original string)``.

    Sorting by the parsed instant first (not the raw string) is what makes
    ``fresh``/``expired``/``oldest_source_at`` correct regardless of
    per-entry UTC-offset or fractional-second spelling; the original
    string is kept as a deterministic tiebreaker for entries that name the
    identical instant in different but equivalent spellings, so which
    input ordering the caller happened to supply evidence in never changes
    which exact string is chosen to render.
    """
    pairs: List[Tuple[datetime, str]] = []
    for ref in result.evidence:
        instant = _try_parse_rfc3339(ref.collected_at)
        if instant is not None:
            pairs.append((instant, ref.collected_at))
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return pairs


def _captured_at_from_pairs(pairs: Sequence[Tuple[datetime, str]]) -> str:
    return pairs[-1][1] if pairs else FALLBACK_TIMESTAMP


def _phase_for(result: AssessmentResult) -> str:
    phases = {finding.phase for finding in result.findings} | {ref.phase for ref in result.evidence}
    if not phases:
        return "design"
    return max(phases, key=contracts.SUPPORTED_PHASES.index)


def _freshness_from_pairs(
    pairs: Sequence[Tuple[datetime, str]], captured_instant: Optional[datetime]
) -> Dict[str, object]:
    if not pairs:
        # No required evidence carries a trustworthy collection timestamp
        # (either none was ever supplied, or every supplied value failed to
        # parse as a real instant): freshness cannot be proven, so this is
        # recorded as conservatively as the schema's fixed status
        # vocabulary allows -- "stale" rather than an invented "fresh" --
        # instead of a fabricated window.
        return {
            "status": "stale",
            "valid_for_hours": FRESHNESS_VALID_FOR_HOURS,
            "oldest_source_at": None,
            "expires_at": None,
        }
    oldest_instant, oldest_source_at = pairs[0]
    expires_instant = _add_hours(oldest_instant, FRESHNESS_VALID_FOR_HOURS)
    status = (
        "fresh" if captured_instant is not None and captured_instant <= expires_instant else "expired"
    )
    return {
        "status": status,
        "valid_for_hours": FRESHNESS_VALID_FOR_HOURS,
        "oldest_source_at": oldest_source_at,
        "expires_at": _format_rfc3339(expires_instant),
    }


# ---------------------------------------------------------------------------
# Summary / verdict
# ---------------------------------------------------------------------------


def _summary(findings: Sequence[Dict[str, object]], dirty: bool) -> Dict[str, object]:
    by_status: Dict[str, List[str]] = {status: [] for status in contracts.STATUSES}
    for finding in findings:
        by_status[str(finding["status"])].append(str(finding["finding_id"]))
    for status in by_status:
        by_status[status].sort()

    must_fix = by_status["must-fix"]
    should_fix = by_status["should-fix"]
    not_verified = by_status["not-verified"]

    if must_fix:
        verdict = "ungoverned"
    elif should_fix or not_verified or dirty:
        # A dirty source tree can never bind evidence to a unique commit,
        # so it can never earn "governed" -- without inventing a finding
        # beyond what the assessor actually observed, the truthful verdict
        # is "partial".
        verdict = "partial"
    else:
        verdict = "governed"

    return {
        "verdict": verdict,
        "pass": by_status["pass"],
        "must_fix": must_fix,
        "should_fix": should_fix,
        "not_verified": not_verified,
        "not_applicable": by_status["not-applicable"],
    }


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------


def build_manifest(result: AssessmentResult) -> Dict[str, object]:
    """Render *result* into the canonical ``governed-actions-manifest/v1``
    shape.

    Every nested collection is explicitly, stably sorted (actions by
    ``action_id``; paths by ``(action_id, mode, path_id)``; probes by
    ``probe_id``; findings by ``(status rank, finding_id, reason_code)``;
    evidence by ``evidence_id``) so the same logical assessment always
    renders byte-identical canonical JSON regardless of the order its
    fields were populated in. The residual-risk register always includes
    this module's mandatory baseline risks (cooperative/alpha host trust,
    non-certification, provider-hosted limitations, live-evidence
    freshness, upstream alpha/experimental drift, and unevidenced audit
    retention/deletion/residency/legal-hold policy) in addition to any the
    assessment itself supplied, so ``residual_risks`` is never empty and
    every finding's ``residual_risk_ref`` always resolves to an entry in
    it.
    """
    residual_risks = _assemble_residual_risks(result)
    risk_ids = {str(entry["residual_risk_id"]) for entry in residual_risks}
    findings = [_finding_to_dict(finding, risk_ids) for finding in _sorted_findings(result.findings)]
    timestamp_pairs = _trustworthy_timestamp_pairs(result)
    captured_at = _captured_at_from_pairs(timestamp_pairs)
    captured_instant = timestamp_pairs[-1][0] if timestamp_pairs else None

    return {
        "schema": MANIFEST_SCHEMA,
        "assessor": {
            "name": ASSESSOR_NAME,
            "version": contracts.ASSESSOR_VERSION,
            "adapter": ADAPTER_NAME,
        },
        "phase": _phase_for(result),
        "captured_at": captured_at,
        "source": {
            "repository": result.source.repository,
            "commit": result.source.commit,
            "dirty": result.source.dirty,
        },
        "pins": _normalize_pins(result.pins),
        "policy_hashes": _normalize_policy_hashes(result.policy_hashes),
        "action_inventory": [_action_to_dict(action) for action in _sorted_actions(result.actions)],
        "mediation_paths": [_path_to_dict(path) for path in _sorted_paths(result.paths)],
        "conformance": {
            "claims": _sorted_claims(result.conformance_claims),
            "reports": _sorted_reports(result.conformance_reports),
            "application_probes": [_probe_to_dict(probe) for probe in _sorted_probes(result.probes)],
        },
        "change_plane": _normalize_change_plane(result.change_plane, result.source.repository),
        "findings": findings,
        "evidence": [_evidence_to_dict(ref) for ref in _sorted_evidence(result.evidence)],
        "freshness": _freshness_from_pairs(timestamp_pairs, captured_instant),
        "residual_risks": residual_risks,
        "summary": _summary(findings, result.source.dirty),
    }


# ---------------------------------------------------------------------------
# Apply-plan items (shared by build_apply_plan and render_evidence_pack's
# remediation sections)
# ---------------------------------------------------------------------------


def _declared_owner(result: AssessmentResult, affected_actions: Sequence[str]) -> Optional[str]:
    affected = set(affected_actions)
    owners = {
        action.owner
        for action in result.actions
        if action.action_id in affected and action.owner
    }
    if len(owners) == 1:
        return next(iter(owners))
    # Zero or conflicting declared owners: never guess -- unknown customer
    # ownership renders as ``null``, not an invented name.
    return None


def _remediation_kind(finding_id: str) -> str:
    return _REMEDIATION_KIND_BY_FINDING_ID.get(finding_id, "manual")


def _apply_plan_item(finding: Dict[str, object], result: AssessmentResult) -> Dict[str, object]:
    affected_actions = list(finding["affected_actions"])  # already sorted
    return {
        "finding_id": finding["finding_id"],
        "status": finding["status"],
        "plane": finding["plane"],
        "affected_actions": affected_actions,
        "affected_paths": list(finding["affected_paths"]),
        "remediation_kind": _remediation_kind(str(finding["finding_id"])),
        "evidence_required": list(finding["evidence_refs"]),
        "owner": _declared_owner(result, affected_actions),
        "depends_on": [],
    }


def _apply_plan_items(result: AssessmentResult, findings: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    return [
        _apply_plan_item(finding, result)
        for finding in findings
        if finding["status"] in _REMEDIATION_REQUIRED_STATUSES
    ]


# ---------------------------------------------------------------------------
# build_apply_plan
# ---------------------------------------------------------------------------


def build_apply_plan(result: AssessmentResult) -> Dict[str, object]:
    """Render *result* into the namespaced, never-self-applying
    ``governed-actions-apply-plan/v1`` shape.

    Exactly one item is created per ``must-fix``/``should-fix``/
    ``not-verified`` finding (in the same ``(status rank, finding_id,
    reason_code)`` order :func:`build_manifest` sorts findings in);
    ``pass``/``not-applicable`` findings create none. ``manifest_sha256``
    is the SHA-256 of the *complete* canonical manifest bytes -- a
    consumer must reject this plan whenever that no longer matches the
    manifest's own recomputed hash.
    """
    manifest = build_manifest(result)
    manifest_hash = f"sha256:{canonical.sha256_hex(canonical.canonical_bytes(manifest))}"
    items = _apply_plan_items(result, manifest["findings"])
    return {
        "schema": APPLY_PLAN_SCHEMA,
        "assessor_version": contracts.ASSESSOR_VERSION,
        "source_commit": result.source.commit,
        "captured_at": manifest["captured_at"],
        "manifest_sha256": manifest_hash,
        "self_applying": False,
        "items": items,
    }


# ---------------------------------------------------------------------------
# render_evidence_pack
# ---------------------------------------------------------------------------

_MATRIX_HEADER = "| ID | Plane | Control | Status | Reason | Evidence | Remediation |"
_MATRIX_DIVIDER = "| --- | --- | --- | --- | --- | --- | --- |"


def _matrix_row(finding: Dict[str, object], remediation: Optional[Dict[str, object]]) -> str:
    remediation_text = remediation["remediation_kind"] if remediation else "none"
    evidence_text = ", ".join(str(ref) for ref in finding["evidence_refs"]) or "none"
    return "| {} | {} | {} | {} | {} | {} | {} |".format(
        finding["finding_id"],
        finding["plane"],
        str(finding["summary"]).replace("|", "/"),
        finding["status"],
        finding["reason_code"],
        evidence_text,
        remediation_text,
    )


def _action_row(action: Dict[str, object]) -> str:
    return "- `{action_id}` ({display_name}): consequence={consequence}, reversible={reversible}, inventory_status={inventory_status}".format(
        **action
    )


def _path_row(path: Dict[str, object]) -> str:
    return "- `{path_id}` action=`{action_id}` mode={mode} covered={covered} status={status}".format(**path)


def _probe_row(probe: Dict[str, object]) -> str:
    # Never include ``expected``/``observed`` free text here: this section
    # is customer-facing evidence, and this assessor never surfaces a raw
    # probe payload value in a rendered artifact -- only the hash-bound
    # status/evidence trail.
    return "- `{probe_id}` action=`{action_id}` path=`{path_id}` status={status} reason={reason_code} evidence={evidence}".format(
        probe_id=probe["probe_id"],
        action_id=probe["action_id"],
        path_id=probe["path_id"],
        status=probe["status"],
        reason_code=probe["reason_code"],
        evidence=", ".join(str(ref) for ref in probe["evidence_refs"]) or "none",
    )


def _residual_risk_row(risk: Dict[str, object]) -> str:
    return "- `{residual_risk_id}` (ref: {finding_id}): {description}".format(**risk)


_EVIDENCE_INDEX_HEADER = "| Evidence ID | Kind | Source | SHA-256 | Collected At | Live Verified |"
_EVIDENCE_INDEX_DIVIDER = "| --- | --- | --- | --- | --- | --- |"


def _evidence_index_row(entry: Dict[str, object]) -> str:
    # Exactly evidence_id/kind/source/sha256/collected_at/live_verified --
    # never repository/source_commit/phase/target_environment/
    # policy_set_sha256/freshness_seconds, and never a probe or action
    # payload value: this is a customer-facing evidence *index*, not a
    # re-export of the full internal evidence record.
    collected_at = entry["collected_at"] if entry["collected_at"] is not None else "unknown"
    return "| {} | {} | {} | {} | {} | {} |".format(
        entry["evidence_id"],
        entry["kind"],
        entry["source"],
        entry["sha256"],
        collected_at,
        entry["live_verified"],
    )


def _remediation_row(item: Dict[str, object]) -> str:
    owner = item["owner"] if item["owner"] else "unassigned"
    return (
        "- `{finding_id}` [{status}/{plane}] kind={remediation_kind} owner={owner} "
        "evidence_required={evidence_required}".format(
            finding_id=item["finding_id"],
            status=item["status"],
            plane=item["plane"],
            remediation_kind=item["remediation_kind"],
            owner=owner,
            evidence_required=", ".join(str(ref) for ref in item["evidence_required"]) or "none",
        )
    )


def render_evidence_pack(result: AssessmentResult) -> str:
    """Render *result* into the customer-facing Markdown evidence pack.

    Contains, in order, the ten required section headings design section
    12.1 calls for, plus an additional ``## Evidence index`` immediately
    before the pass/fail matrix (whose own ``Evidence`` column cites these
    same evidence IDs) that lists every manifest evidence entry sorted by
    ``evidence_id`` with exactly its ``kind``/``source``/``sha256``/
    ``collected_at``/``live_verified`` -- never any other field, and never
    a payload. The pass/fail matrix's columns are exactly ``ID | Plane |
    Control | Status | Reason | Evidence | Remediation``, with runtime
    (``plane: runtime``) and GitHub Copilot change-plane (``plane:
    change``) rows independently gated by their own status. No probe's raw
    ``expected``/``observed`` free text is ever rendered here -- only its
    status, reason code, and evidence references.
    """
    manifest = build_manifest(result)
    apply_plan = build_apply_plan(result)
    # ``build_apply_plan`` derives its items by filtering ``manifest["findings"]``
    # to the remediation-required statuses while preserving order (see
    # ``_apply_plan_items``), so zipping the two in lockstep here recovers
    # the exact finding <-> remediation-item pairing without re-matching on
    # content -- correct even if two findings happen to share both the same
    # ``finding_id`` and ``reason_code``.
    remediation_by_finding_index: Dict[int, Dict[str, object]] = {}
    item_iter = iter(apply_plan["items"])
    for index, finding in enumerate(manifest["findings"]):
        if finding["status"] in _REMEDIATION_REQUIRED_STATUSES:
            remediation_by_finding_index[index] = next(item_iter)

    lines: List[str] = []
    lines.append("# Governance Evidence Pack")
    lines.append("")
    lines.append("## Scope and trust model")
    lines.append("")
    lines.append(
        "- Assessor: {name} v{version} (adapter: {adapter}), phase `{phase}`.".format(
            name=manifest["assessor"]["name"],
            version=manifest["assessor"]["version"],
            adapter=manifest["assessor"]["adapter"],
            phase=manifest["phase"],
        )
    )
    lines.append(
        "- Source: `{repository}` @ `{commit}` (dirty: {dirty}).".format(**manifest["source"])
    )
    lines.append(
        "- Agent Hooks is cooperative/alpha and is never treated as this "
        "assessment's security boundary."
    )
    lines.append("- Conformance recorded in this pack is never a certification.")
    lines.append(
        "- GitHub Copilot coding agent's own internal reasoning/tool-calling "
        "loop is never intercepted by this assessment; only the PR/CI/"
        "deployment supply chain a change travels through is assessed."
    )
    lines.append(
        "- Provider-hosted tool side effects without an equivalent, "
        "independently verified server-side control are not supported by "
        "this assessment's mediation model."
    )
    lines.append(
        "- Tool services are expected to independently re-check "
        "authorization, idempotency, and transaction boundaries; this "
        "assessment never substitutes for that."
    )
    lines.append("")

    lines.append("## Architecture and data flow")
    lines.append("")
    lines.append(
        "- {actions} declared consequential action(s) across {paths} "
        "traced mediation path(s).".format(
            actions=len(manifest["action_inventory"]),
            paths=len(manifest["mediation_paths"]),
        )
    )
    lines.append(
        "- {probes} application-path probe result(s) recorded.".format(
            probes=len(manifest["conformance"]["application_probes"])
        )
    )
    lines.append("")

    lines.append("## Runtime action inventory")
    lines.append("")
    if manifest["action_inventory"]:
        lines.extend(_action_row(action) for action in manifest["action_inventory"])
    else:
        lines.append("- No declared consequential actions were observed.")
    lines.append("")

    lines.append("## Runtime mediation graph")
    lines.append("")
    if manifest["mediation_paths"]:
        lines.extend(_path_row(path) for path in manifest["mediation_paths"])
    else:
        lines.append("- No mediation paths were observed.")
    lines.append("")

    lines.append("## Application-path probe evidence")
    lines.append("")
    probes = manifest["conformance"]["application_probes"]
    if probes:
        lines.extend(_probe_row(probe) for probe in probes)
    else:
        lines.append("- No application-path probes were recorded.")
    lines.append("")

    lines.append("## GitHub Copilot change plane")
    lines.append("")
    change_plane = manifest["change_plane"]
    lines.append("- Repository: `{}`.".format(change_plane["repository"]))
    lines.append(
        "- {} required workflow file(s) evidenced, {} identity/identities "
        "recorded.".format(len(change_plane["workflows"]), len(change_plane["identities"]))
    )
    lines.append("")

    lines.append("## Evidence index")
    lines.append("")
    lines.append(_EVIDENCE_INDEX_HEADER)
    lines.append(_EVIDENCE_INDEX_DIVIDER)
    if manifest["evidence"]:
        lines.extend(_evidence_index_row(entry) for entry in manifest["evidence"])
    else:
        lines.append("| none | none | none | none | none | none |")
    lines.append("")

    lines.append("## Pass/fail matrix")
    lines.append("")
    lines.append(_MATRIX_HEADER)
    lines.append(_MATRIX_DIVIDER)
    for index, finding in enumerate(manifest["findings"]):
        lines.append(_matrix_row(finding, remediation_by_finding_index.get(index)))
    lines.append("")

    lines.append("## Residual-risk register")
    lines.append("")
    for risk in manifest["residual_risks"]:
        lines.append(_residual_risk_row(risk))
    lines.append("")

    lines.append("## Remediation plan")
    lines.append("")
    if apply_plan["items"]:
        lines.extend(_remediation_row(item) for item in apply_plan["items"])
    else:
        lines.append("- No outstanding remediation is required.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schema validation + payload-free guard (in-memory, before any writes)
# ---------------------------------------------------------------------------

_REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"


def _load_schema(name: str) -> Mapping[str, object]:
    return json.loads((_REFERENCES_DIR / name).read_text(encoding="utf-8"))


def _validate_against_schema(instance: Mapping[str, object], schema_file: str) -> None:
    import jsonschema

    schema = _load_schema(schema_file)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise ArtifactWriteError(
            f"{schema_file}: rendered artifact failed schema validation at "
            f"{list(first.path)}: {first.message}"
        )


def _validate_manifest(manifest: Mapping[str, object]) -> None:
    _validate_against_schema(manifest, "governed-actions-manifest.schema.json")
    canonical.validate_payload_free_audit(manifest)


def _validate_apply_plan(apply_plan: Mapping[str, object]) -> None:
    _validate_against_schema(apply_plan, "governed-actions-apply-plan.schema.json")
    canonical.validate_payload_free_audit(apply_plan)


# ---------------------------------------------------------------------------
# write_artifacts: atomic, all-or-nothing, three-file transaction.
# ---------------------------------------------------------------------------


def _resolve_destination(root: Path, relative: Path) -> Path:
    relative = Path(relative)
    if relative.is_absolute():
        raise ArtifactWriteError(
            f"artifact path must be repository-relative, not absolute: {relative}"
        )
    root_resolved = root.resolve()
    candidate = root_resolved
    for part in relative.parts:
        if part in ("", "."):
            continue
        candidate = candidate / part
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ArtifactWriteError(
            f"artifact path escapes the assessed root: {relative}"
        ) from error
    return resolved


def _stage_temp_file(directory: Path, name_hint: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=directory,
        prefix=f".{name_hint}.",
        suffix=".stage",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return temp_path


def _unique_backup_path(dest: Path) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=dest.parent,
        prefix=f".{dest.name}.",
        suffix=".bak",
        delete=False,
    ) as handle:
        backup_path = Path(handle.name)
    backup_path.unlink()
    return backup_path


def _fsync_dir(directory: Path) -> None:
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_artifacts(
    root: Path,
    result: AssessmentResult,
    manifest_path: Path,
    evidence_path: Path,
    apply_plan_path: Path,
    replace: Callable[[Path, Path], None] = os.replace,
) -> Tuple[Path, Path, Path]:
    """Render and atomically write the three governed-actions artifacts.

    Every value is rendered and schema-validated (and checked payload-free)
    entirely in memory before any file on disk is touched. The two JSON
    artifacts (manifest, apply plan) are written as their newline-free
    canonical JSON bytes with exactly one trailing ``b"\\n"`` appended at
    this emission step only -- ``manifest_sha256`` is always computed and
    bound over the newline-free canonical value, never the on-disk bytes,
    so appending this single POSIX-friendly trailing newline for file
    emission never changes any hash. Bytes for all three artifacts are
    then staged as temp files in their own destination directories (same
    filesystem, so the later replace is atomic), any existing artifact at
    each destination is moved aside to a unique backup name, and only then
    are all three temp files replaced onto their destinations. If any step
    of the backup or replace phase fails, every backup already made is
    restored and every destination this call itself newly created is
    removed, before :class:`ArtifactWriteError` is raised -- the on-disk
    artifact set is left exactly as it was found, never a mix of old and
    new generations. Backups are deleted only once
    every replacement has succeeded.
    """
    root = Path(root).resolve()
    manifest = build_manifest(result)
    evidence_pack = render_evidence_pack(result)
    apply_plan = build_apply_plan(result)

    _validate_manifest(manifest)
    _validate_apply_plan(apply_plan)

    destinations = (
        (
            _resolve_destination(root, manifest_path),
            canonical.canonical_bytes(manifest) + b"\n",
        ),
        (_resolve_destination(root, evidence_path), evidence_pack.encode("utf-8")),
        (
            _resolve_destination(root, apply_plan_path),
            canonical.canonical_bytes(apply_plan) + b"\n",
        ),
    )

    staged: List[Tuple[Path, Path]] = []
    for dest, data in destinations:
        temp = _stage_temp_file(dest.parent, dest.name, data)
        staged.append((dest, temp))

    backups: List[Tuple[Path, Path]] = []
    created_without_backup: List[Path] = []
    try:
        for dest, _temp in staged:
            if dest.exists():
                backup = _unique_backup_path(dest)
                replace(dest, backup)
                backups.append((dest, backup))

        for dest, temp in staged:
            replace(temp, dest)
            if not any(existing_dest == dest for existing_dest, _ in backups):
                created_without_backup.append(dest)
            _fsync_dir(dest.parent)
    except BaseException as error:
        for dest in created_without_backup:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
        for dest, backup in backups:
            try:
                replace(backup, dest)
            except OSError:
                pass
        for _dest, temp in staged:
            try:
                Path(temp).unlink(missing_ok=True)
            except OSError:
                pass
        raise ArtifactWriteError(
            f"failed to write governed-actions artifacts: {error}"
        ) from error

    for _dest, backup in backups:
        Path(backup).unlink(missing_ok=True)

    return tuple(dest for dest, _ in staged)  # type: ignore[return-value]
