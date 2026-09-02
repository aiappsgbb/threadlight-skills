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
import stat
import uuid
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
            "with collected_at null and live_verified false (never the "
            "input's own claimed live_verified/freshness_seconds), and is "
            "excluded from this manifest's freshness computation entirely. "
            "When no evidence carries a trustworthy timestamp, "
            "freshness.status is reported stale; when the newest "
            "trustworthy timestamp (captured_at) exceeds the oldest "
            "trustworthy timestamp (oldest_source_at) plus this "
            "manifest's valid_for_hours, freshness.status is reported "
            "expired -- neither is ever assumed to still be fresh."
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

#: This module's own reserved, authoritative residual-risk ids -- the
#: mandatory baseline risks plus the catch-all. Their wording is a trust
#: disclaimer this assessor makes about *itself*, never customer data, so
#: an assessment-supplied ``residual_risks`` entry must never be able to
#: silently overwrite one.
_RESERVED_RESIDUAL_RISK_IDS: frozenset = frozenset(_ALL_RISK_IDS_ORDER_HINT)


class ReservedResidualRiskIdError(ValueError):
    """Raised when an :class:`~contracts.AssessmentResult` supplies its own
    ``residual_risks`` entry whose ``residual_risk_id`` collides with one of
    this module's reserved ids (or duplicates another assessment-supplied
    entry's id). Collisions are rejected rather than silently merged or
    renamed: this module's own authoritative trust disclaimers -- and every
    other residual-risk entry's identity -- must never be ambiguous or
    overwritable by assessment/customer data.
    """


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
        "aliases": sorted(action.aliases),
        "owner": action.owner,
        "declaration_refs": sorted(action.declaration_refs),
        "implementation_refs": sorted(action.implementation_refs),
        "input_schema_sha256": action.input_schema_sha256,
        "output_schema_sha256": action.output_schema_sha256,
        "source": action.source,
        "consequence": action.consequence,
        "secondary_consequences": sorted(action.secondary_consequences),
        "reversible": action.reversible,
        "compensation_ref": action.compensation_ref,
        "execution_modes": sorted(action.execution_modes),
        "provider_hosted": action.provider_hosted,
        "approval_required": action.approval_required,
        "policy_ids": sorted(action.policy_ids),
        "known_runtime_paths": sorted(action.known_runtime_paths),
        "inventory_status": action.inventory_status,
    }


def _path_to_dict(path: PathRecord) -> Dict[str, object]:
    return {
        "path_id": path.path_id,
        "action_id": path.action_id,
        "mode": path.mode,
        # ``nodes`` is the path's own ordered node chain, never sorted.
        "nodes": list(path.nodes),
        "pre_action_seam": path.pre_action_seam,
        "equivalent_control_ref": path.equivalent_control_ref,
        "covered": path.covered,
        "status": path.status,
        "evidence_refs": sorted(path.evidence_refs),
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
        "evidence_refs": sorted(probe.evidence_refs),
    }


def _evidence_to_dict(ref: EvidenceRef) -> Dict[str, object]:
    # ``collected_at`` is the only trustworthy signal here: if it is
    # missing entirely, *or* present but does not parse as a genuine
    # RFC 3339 instant (e.g. a calendar-impossible date), the entry is
    # untrustworthy and every field that could otherwise imply a
    # timestamp can be trusted is degraded in lockstep to the schema's
    # own "absent"/"unverified" representation -- ``collected_at`` and
    # ``freshness_seconds`` to ``None``, ``live_verified`` to ``False`` --
    # regardless of what the input itself claimed for
    # ``freshness_seconds``/``live_verified``. An untrustworthy or absent
    # collected_at can never be overridden by an input that separately
    # (accidentally or not) asserts a truthy ``live_verified`` or a
    # nonzero ``freshness_seconds``: those claims are only ever honored
    # once collected_at itself has been proven trustworthy.
    collected_at = ref.collected_at
    freshness_seconds = ref.freshness_seconds
    live_verified = ref.live_verified
    if collected_at is None or _try_parse_rfc3339(collected_at) is None:
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
    seen_extra_ids: Set[str] = set()
    for extra in result.residual_risks:
        risk_id = str(extra["residual_risk_id"])
        if risk_id in _RESERVED_RESIDUAL_RISK_IDS:
            raise ReservedResidualRiskIdError(
                "assessment-supplied residual_risks entry reuses this "
                f"module's reserved, authoritative id {risk_id!r}; this "
                "assessor's own trust disclaimers can never be overwritten "
                "by assessment/customer data"
            )
        if risk_id in seen_extra_ids:
            raise ReservedResidualRiskIdError(
                "assessment-supplied residual_risks entries must have "
                f"distinct residual_risk_id values, got a duplicate: {risk_id!r}"
            )
        seen_extra_ids.add(risk_id)
        merged[risk_id] = dict(extra)
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
    # Whole-second instants render without a fractional part (unchanged,
    # byte-identical to every existing fixture); an instant that carries
    # sub-second precision keeps it, so a computed timestamp such as
    # ``expires_at`` (derived by adding ``valid_for_hours`` to an evidence
    # entry's own fractional ``collected_at``) can never silently disagree
    # with the freshness this manifest itself computes from that same
    # instant.
    moment = moment.astimezone(timezone.utc)
    if moment.microsecond:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
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


def _md_escape_inline(value: object) -> str:
    """Sanitize *value* for inclusion in a single rendered Markdown table
    cell, list item, or heading-adjacent line.

    Collapses any embedded newline/carriage-return -- which could
    otherwise inject a forged extra table row or a spurious new heading
    line into the rendered pack -- to a single space, escapes ``|`` (which
    would otherwise split a table row into extra cells), and replaces any
    backtick with a plain apostrophe and any ``<``/``>`` with their HTML
    entities (which would otherwise let an assessment-/customer-
    influenceable string break out of an inline code span or inject raw
    HTML), all while keeping the value fully human-readable. Applied to
    every assessment-influenceable string this module ever interpolates
    into a table cell, list item, or heading-adjacent line; this module
    never renders a probe's raw ``expected``/``observed`` payload value
    here at all, so there is nothing to leak through this escaping.
    """
    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "\\|")
    text = text.replace("`", "'")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text


def _md_code_span(value: object) -> str:
    """Wrap an escaped, identifier-like *value* in a Markdown inline code
    span. :func:`_md_escape_inline` already replaces any embedded
    backtick, so the delimiters this function adds can never be broken out
    of by the interpolated value.
    """
    return f"`{_md_escape_inline(value)}`"


_MATRIX_HEADER = "| ID | Plane | Control | Status | Reason | Evidence | Remediation |"
_MATRIX_DIVIDER = "| --- | --- | --- | --- | --- | --- | --- |"


def _matrix_row(finding: Dict[str, object], remediation: Optional[Dict[str, object]]) -> str:
    remediation_text = remediation["remediation_kind"] if remediation else "none"
    evidence_text = ", ".join(_md_escape_inline(ref) for ref in finding["evidence_refs"]) or "none"
    return "| {} | {} | {} | {} | {} | {} | {} |".format(
        _md_escape_inline(finding["finding_id"]),
        _md_escape_inline(finding["plane"]),
        _md_escape_inline(finding["summary"]),
        _md_escape_inline(finding["status"]),
        _md_escape_inline(finding["reason_code"]),
        evidence_text,
        _md_escape_inline(remediation_text),
    )


def _action_row(action: Dict[str, object]) -> str:
    return (
        "- {action_id} ({display_name}): consequence={consequence}, "
        "reversible={reversible}, inventory_status={inventory_status}".format(
            action_id=_md_code_span(action["action_id"]),
            display_name=_md_escape_inline(action["display_name"]),
            consequence=_md_escape_inline(action["consequence"]),
            reversible=action["reversible"],
            inventory_status=_md_escape_inline(action["inventory_status"]),
        )
    )


def _path_row(path: Dict[str, object]) -> str:
    return "- {path_id} action={action_id} mode={mode} covered={covered} status={status}".format(
        path_id=_md_code_span(path["path_id"]),
        action_id=_md_code_span(path["action_id"]),
        mode=_md_escape_inline(path["mode"]),
        covered=path["covered"],
        status=_md_escape_inline(path["status"]),
    )


def _probe_row(probe: Dict[str, object]) -> str:
    # Never include ``expected``/``observed`` free text here: this section
    # is customer-facing evidence, and this assessor never surfaces a raw
    # probe payload value in a rendered artifact -- only the hash-bound
    # status/evidence trail.
    return (
        "- {probe_id} action={action_id} path={path_id} status={status} "
        "reason={reason_code} evidence={evidence}".format(
            probe_id=_md_code_span(probe["probe_id"]),
            action_id=_md_code_span(probe["action_id"]),
            path_id=_md_code_span(probe["path_id"]),
            status=_md_escape_inline(probe["status"]),
            reason_code=_md_escape_inline(probe["reason_code"]),
            evidence=(
                ", ".join(_md_escape_inline(ref) for ref in probe["evidence_refs"]) or "none"
            ),
        )
    )


def _residual_risk_row(risk: Dict[str, object]) -> str:
    return "- {residual_risk_id} (ref: {finding_id}): {description}".format(
        residual_risk_id=_md_code_span(risk["residual_risk_id"]),
        finding_id=_md_escape_inline(risk["finding_id"]),
        description=_md_escape_inline(risk["description"]),
    )


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
        _md_escape_inline(entry["evidence_id"]),
        _md_escape_inline(entry["kind"]),
        _md_escape_inline(entry["source"]),
        _md_escape_inline(entry["sha256"]),
        _md_escape_inline(collected_at),
        entry["live_verified"],
    )


def _remediation_row(item: Dict[str, object]) -> str:
    owner = item["owner"] if item["owner"] else "unassigned"
    return (
        "- {finding_id} [{status}/{plane}] kind={remediation_kind} owner={owner} "
        "evidence_required={evidence_required}".format(
            finding_id=_md_code_span(item["finding_id"]),
            status=_md_escape_inline(item["status"]),
            plane=_md_escape_inline(item["plane"]),
            remediation_kind=_md_escape_inline(item["remediation_kind"]),
            owner=_md_escape_inline(owner),
            evidence_required=(
                ", ".join(_md_escape_inline(ref) for ref in item["evidence_required"]) or "none"
            ),
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
        "- Source: {repository} @ {commit} (dirty: {dirty}).".format(
            repository=_md_code_span(manifest["source"]["repository"]),
            commit=_md_code_span(manifest["source"]["commit"]),
            dirty=manifest["source"]["dirty"],
        )
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
    lines.append("- Repository: {}.".format(_md_code_span(change_plane["repository"])))
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
    # ``format_checker`` must be supplied explicitly: ``jsonschema`` never
    # enforces ``"format": "date-time"`` (or any other format assertion) by
    # default, so without it a calendar-impossible date (e.g. a leap-day
    # 31st) that nonetheless matches the schema's own shape ``pattern``
    # would silently pass production validation -- exactly the same
    # ``jsonschema.FormatChecker()`` this module's own tests already use to
    # assert validity, so production and test validation never disagree.
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
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


def _lstat_or_none(path: Path) -> Optional[os.stat_result]:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _is_nested(outer: Path, inner: Path) -> bool:
    """True if *inner* is *outer* itself or lives underneath it."""
    try:
        inner.relative_to(outer)
        return True
    except ValueError:
        return False


def _preflight_destinations(root_resolved: Path, dests: Sequence[Path]) -> None:
    """Reject unsafe destinations before any staging begins.

    Rejects: the assessed root itself as a destination; a destination that
    already exists but is not a plain regular file (a directory, symlink,
    device, FIFO, or socket -- this assessor only ever replaces a plain
    file it previously wrote, and never no-follow-resolves a leaf symlink
    here so a symlink is never silently written through); exact duplicate
    destinations; and any pair of destinations where one is an ancestor
    directory of the other (which would make "restore the complete prior
    artifact set" ambiguous, since replacing one could delete or recreate
    the other's parent). All of this runs *before* any file is staged, so
    a rejected call never touches disk at all.
    """
    seen: List[Path] = []
    for dest in dests:
        if dest == root_resolved:
            raise ArtifactWriteError(
                f"artifact destination must not be the assessed root itself: {dest}"
            )
        info = _lstat_or_none(dest)
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise ArtifactWriteError(
                "artifact destination already exists and is not a plain "
                f"file (refusing to replace a directory/symlink/special "
                f"file): {dest}"
            )
        for other in seen:
            if dest == other:
                raise ArtifactWriteError(
                    f"artifact destinations must be distinct, got a duplicate: {dest}"
                )
            if _is_nested(dest, other) or _is_nested(other, dest):
                raise ArtifactWriteError(
                    "artifact destinations must not nest inside one another "
                    f"(one is an ancestor directory of the other): {dest} and {other}"
                )
        seen.append(dest)


def _open_verified_dir_fd(root_fd: int, parts: Sequence[str], display: Path) -> int:
    """Walk *parts* (directory-component names only) from *root_fd*,
    opening each with ``O_NOFOLLOW`` so a symlink at any level -- including
    one swapped in after an earlier check ran -- raises instead of being
    silently traversed. A missing directory is created under the
    already-verified parent and then re-opened with the same no-follow
    flags, so a symlink raced into the gap between "not found" and
    "create" is still caught. The caller owns the returned fd and must
    close it.
    """
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, dir_fd=current_fd)
                except FileExistsError:
                    pass
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except OSError as error:
                    raise ArtifactWriteError(
                        "could not create/verify artifact directory under "
                        f"the assessed root: {display} ({error})"
                    ) from error
            except OSError as error:
                raise ArtifactWriteError(
                    "artifact directory path is unsafe (not a plain "
                    f"directory -- e.g. a symlink): {display} ({error})"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _verify_leaf_absent_or_regular(parent_fd: int, name: str, display: Path) -> None:
    """No-follow check that *name* under *parent_fd* is either absent or a
    plain regular file -- never a directory, symlink, device, FIFO, or
    socket.
    """
    try:
        info = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise ArtifactWriteError(
            "artifact destination already exists and is not a plain file "
            f"(refusing to replace a directory/symlink/special file): {display}"
        )


def _revalidate_destination(root_fd: int, root_resolved: Path, dest: Path) -> int:
    """Immediately before every stage/backup/replace step, re-walk *dest*'s
    full path from the anchored *root_fd* with no-follow opens, so a
    parent directory (or the leaf itself) that was swapped to a symlink,
    directory, or other special file after an earlier check is caught and
    this call fails closed rather than silently writing through it.

    Returns an open, verified dir_fd for *dest*'s immediate parent
    directory; the caller owns it and must close it.
    """
    parts = dest.relative_to(root_resolved).parts
    if not parts:
        raise ArtifactWriteError(
            f"artifact destination must not be the assessed root itself: {dest}"
        )
    parent_fd = _open_verified_dir_fd(root_fd, parts[:-1], dest.parent)
    try:
        _verify_leaf_absent_or_regular(parent_fd, parts[-1], dest)
    except BaseException:
        os.close(parent_fd)
        raise
    return parent_fd


def _stage_temp_file(root_fd: int, root_resolved: Path, dest: Path, data: bytes) -> Path:
    """Stage *data* as a new, race-resistant temp file next to *dest*.

    Traverses to *dest*'s parent directory purely through verified,
    no-follow dir-fd opens anchored at *root_fd* (never re-resolving the
    directory by path), then creates a randomly named temp file with
    ``O_CREAT | O_EXCL | O_NOFOLLOW`` under that verified parent fd, so
    staging itself never re-opens a path that could have been swapped to a
    symlink in between. On any write/fsync failure the partial temp file
    is removed and the failure is re-raised as a bounded
    :class:`ArtifactWriteError` (never a raw, unbounded exception).
    """
    parts = dest.relative_to(root_resolved).parts
    if not parts:
        raise ArtifactWriteError(
            f"artifact destination must not be the assessed root itself: {dest}"
        )
    parent_fd = _open_verified_dir_fd(root_fd, parts[:-1], dest.parent)
    try:
        name = parts[-1]
        candidate: Optional[str] = None
        fd: Optional[int] = None
        for _attempt in range(8):
            candidate = f".{name}.{uuid.uuid4().hex}.stage"
            try:
                fd = os.open(
                    candidate,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                break
            except FileExistsError:
                candidate = None
                continue
        if fd is None or candidate is None:
            raise ArtifactWriteError(
                f"could not allocate a unique staging name for {dest}"
            )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException as error:
            try:
                os.unlink(candidate, dir_fd=parent_fd)
            except OSError:
                pass
            raise ArtifactWriteError(
                f"failed to stage artifact bytes for {dest}: {error}"
            ) from error
        return dest.parent / candidate
    finally:
        os.close(parent_fd)


def _unique_backup_path(dest: Path) -> Path:
    # A pure name generator with no filesystem interaction: collision
    # probability with a random UUID4 hex suffix is negligible, and this
    # avoids the create-then-unlink round trip (itself a small window for
    # a race) a filesystem-backed unique-name allocator would otherwise
    # need.
    return dest.parent / f".{dest.name}.{uuid.uuid4().hex}.bak"


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
    emission never changes any hash.

    Before any staging, all three destinations are preflight-checked (not
    the assessed root itself, no existing non-regular-file leaf, no
    duplicates, no ancestor/descendant nesting). Bytes are then staged as
    new, randomly named temp files reached purely through verified,
    no-follow directory-fd traversal anchored at the assessed root (so
    staging itself cannot be tricked into writing through a symlink); any
    existing artifact at each destination is moved aside to a unique
    backup name, and only then are all three temp files replaced onto
    their destinations. Immediately before every backup-aside or
    commit-replace step (the two places this call still must go through
    the injectable, path-based *replace* callable rather than a dir-fd
    operation) the full destination path is re-walked with the same
    no-follow verification, so a parent directory or leaf swapped to a
    symlink/directory/special file after an earlier check still fails
    closed rather than being silently written through.

    If any step of the staging, backup, or replace phase fails, every
    backup already made is restored, every destination this call itself
    newly created is removed, and every staged temp file is cleaned up,
    before :class:`ArtifactWriteError` is raised -- the on-disk artifact
    set is left exactly as it was found, never a mix of old and new
    generations. If restoring a backup itself fails, that failure is never
    swallowed: the backup file is preserved (never deleted) for manual
    recovery, a successfully restored file's parent directory is fsynced,
    and the raised :class:`ArtifactWriteError` explicitly states that
    rollback did not fully restore the prior artifact set rather than
    claiming it did. Backups are deleted only once every replacement in
    the whole transaction has succeeded.
    """
    root_resolved = Path(root).resolve()
    manifest = build_manifest(result)
    evidence_pack = render_evidence_pack(result)
    apply_plan = build_apply_plan(result)

    _validate_manifest(manifest)
    _validate_apply_plan(apply_plan)

    destinations = (
        (
            _resolve_destination(root_resolved, manifest_path),
            canonical.canonical_bytes(manifest) + b"\n",
        ),
        (_resolve_destination(root_resolved, evidence_path), evidence_pack.encode("utf-8")),
        (
            _resolve_destination(root_resolved, apply_plan_path),
            canonical.canonical_bytes(apply_plan) + b"\n",
        ),
    )

    _preflight_destinations(root_resolved, [dest for dest, _data in destinations])

    root_fd = os.open(root_resolved, os.O_RDONLY | os.O_DIRECTORY)
    try:
        staged: List[Tuple[Path, Path]] = []
        try:
            for dest, data in destinations:
                temp = _stage_temp_file(root_fd, root_resolved, dest, data)
                staged.append((dest, temp))
        except BaseException as error:
            for _dest, temp in staged:
                try:
                    Path(temp).unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(error, ArtifactWriteError):
                raise
            raise ArtifactWriteError(
                f"failed to stage governed-actions artifacts: {error}"
            ) from error

        backups: List[Tuple[Path, Path]] = []
        created_without_backup: List[Path] = []
        try:
            for dest, _temp in staged:
                parent_fd = _revalidate_destination(root_fd, root_resolved, dest)
                os.close(parent_fd)
                if dest.exists():
                    backup = _unique_backup_path(dest)
                    replace(dest, backup)
                    backups.append((dest, backup))

            for dest, temp in staged:
                parent_fd = _revalidate_destination(root_fd, root_resolved, dest)
                os.close(parent_fd)
                replace(temp, dest)
                if not any(existing_dest == dest for existing_dest, _ in backups):
                    created_without_backup.append(dest)
                _fsync_dir(dest.parent)
        except BaseException as error:
            rollback_errors: List[str] = []
            for dest in created_without_backup:
                try:
                    dest.unlink(missing_ok=True)
                except OSError as unlink_error:
                    rollback_errors.append(
                        f"could not remove newly created artifact {dest}: {unlink_error}"
                    )
            for dest, backup in backups:
                try:
                    replace(backup, dest)
                except OSError as restore_error:
                    # Never delete the backup when its own restore fails:
                    # it is the only remaining copy of the prior artifact,
                    # and is preserved here for manual recovery.
                    rollback_errors.append(
                        f"could not restore prior artifact at {dest} from "
                        f"backup {backup} (backup preserved for manual "
                        f"recovery): {restore_error}"
                    )
                    continue
                try:
                    _fsync_dir(dest.parent)
                except OSError as fsync_error:
                    rollback_errors.append(
                        f"restored {dest} from backup {backup} but could not "
                        f"fsync its parent directory (backup preserved for "
                        f"manual verification): {fsync_error}"
                    )
                    continue
                try:
                    Path(backup).unlink(missing_ok=True)
                except OSError:
                    pass
            for _dest, temp in staged:
                try:
                    Path(temp).unlink(missing_ok=True)
                except OSError:
                    pass
            if rollback_errors:
                raise ArtifactWriteError(
                    "failed to write governed-actions artifacts AND ROLLBACK "
                    "DID NOT FULLY RESTORE the prior artifact set -- manual "
                    f"recovery required: {error}; rollback errors: "
                    + "; ".join(rollback_errors)
                ) from error
            raise ArtifactWriteError(
                f"failed to write governed-actions artifacts: {error}"
            ) from error

        for _dest, backup in backups:
            Path(backup).unlink(missing_ok=True)

        return tuple(dest for dest, _ in staged)  # type: ignore[return-value]
    finally:
        os.close(root_fd)
