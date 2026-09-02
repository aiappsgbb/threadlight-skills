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
import re
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
            "collection timestamp, and freshness is judged only against "
            "the evidence a finding or application-path probe actually "
            "requires -- never unrelated evidence this manifest merely "
            "happens to carry. An evidence entry whose collected_at is "
            "missing or does not parse as a real instant is rendered with "
            "collected_at null and live_verified false (never the input's "
            "own claimed live_verified/freshness_seconds), and is "
            "excluded from this manifest's freshness computation "
            "entirely. captured_at is this assessment's own trusted "
            "capture instant -- never derived from evidence, and never "
            "the newest evidence timestamp this manifest happens to "
            "carry. When no required evidence carries a trustworthy "
            "timestamp, or captured_at itself cannot be trusted, "
            "freshness.status is reported stale; when captured_at "
            "exceeds the oldest trustworthy required-evidence timestamp "
            "(oldest_source_at) plus this manifest's valid_for_hours, "
            "freshness.status is reported expired; and when a finding or "
            "application-path probe cites its own required evidence "
            "entry that cannot be trusted, freshness.status is reported "
            "stale even if other, unrelated evidence in this manifest is "
            "itself fresh -- none of these cases is ever assumed to "
            "still be fresh."
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


class DuplicateEvidenceIdError(ValueError):
    """Raised when an :class:`~contracts.AssessmentResult` supplies two or
    more ``evidence`` entries with the same ``evidence_id``.

    The manifest schema permits this (it has no ``uniqueItems``/keyed
    constraint on the ``evidence`` array), but this module's own freshness
    and evidence-index logic both look an evidence id up by identity --
    e.g. ``{ref.evidence_id for ref in result.evidence if trustworthy}`` --
    so a duplicate id where only *one* of the two entries carries a
    trustworthy ``collected_at`` would let that one entry's trustworthiness
    silently mask the other, untrustworthy entry through simple set
    membership. Rather than trying to guess which of two same-id entries
    is authoritative, this is rejected outright, deterministically, before
    any rendering or writing begins.
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
#
# Every explicitly documented primary key below (action_id;
# (action_id, mode, path_id); probe_id; (status rank, finding_id,
# reason_code); evidence_id) is only *usually* unique across a single
# assessment. Two records that happen to share the exact same primary key
# but differ in some other field would otherwise sort in whatever order
# Python's stable sort happened to receive them in -- which depends on the
# caller's own input ordering, not on the records' content -- silently
# reintroducing the exact input-order sensitivity every other sort here
# exists to eliminate. Every sort key below therefore appends a
# fully-normalized canonical-JSON-bytes tiebreaker computed from the
# record's own complete rendered dict, so two records with a tied primary
# key (and only two *genuinely identical* records can ever tie on both the
# primary key and this full-record tiebreaker) always land in the same
# relative order regardless of which order the caller supplied them in.
# ---------------------------------------------------------------------------


def _canonical_tiebreak(rendered: Mapping[str, object]) -> bytes:
    return canonical.canonical_bytes(rendered)


def _finding_sort_key(finding: Finding) -> Tuple[int, str, str]:
    return (
        _STATUS_RANK.get(finding.status, len(STATUS_ORDER)),
        finding.finding_id,
        finding.reason_code,
    )


def _sorted_actions(actions: Sequence[ActionRecord]) -> List[ActionRecord]:
    return sorted(
        actions,
        key=lambda action: (action.action_id, _canonical_tiebreak(_action_to_dict(action))),
    )


def _sorted_paths(paths: Sequence[PathRecord]) -> List[PathRecord]:
    return sorted(
        paths,
        key=lambda path: (
            (path.action_id, path.mode, path.path_id),
            _canonical_tiebreak(_path_to_dict(path)),
        ),
    )


def _sorted_probes(probes: Sequence[ProbeResult]) -> List[ProbeResult]:
    return sorted(
        probes,
        key=lambda probe: (probe.probe_id, _canonical_tiebreak(_probe_to_dict(probe))),
    )


def _sorted_findings(findings: Sequence[Finding], risk_ids: Set[str]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            _finding_sort_key(finding),
            _canonical_tiebreak(_finding_to_dict(finding, risk_ids)),
        ),
    )


def _sorted_evidence(evidence: Sequence[EvidenceRef]) -> List[EvidenceRef]:
    return sorted(
        evidence,
        key=lambda ref: (ref.evidence_id, _canonical_tiebreak(_evidence_to_dict(ref))),
    )


def _reject_duplicate_evidence_ids(evidence: Sequence[EvidenceRef]) -> None:
    """Raise :class:`DuplicateEvidenceIdError` if any two entries in
    *evidence* share the same ``evidence_id``.

    Called before anything else that would look an evidence id up by
    identity (freshness's required-evidence check, the evidence index,
    the pass/fail matrix's ``Evidence`` column) so a duplicate id can
    never let one entry's trustworthiness mask another, distinct entry
    recorded under the identical id -- whether the two are exact
    duplicates or differ in every other field, and regardless of which
    order the caller happened to supply them in.
    """
    seen: Set[str] = set()
    for ref in evidence:
        if ref.evidence_id in seen:
            raise DuplicateEvidenceIdError(
                "assessment-supplied evidence entries must have distinct "
                f"evidence_id values, got a duplicate: {ref.evidence_id!r}"
            )
        seen.add(ref.evidence_id)


def _sorted_mappings_by_canonical(items: Sequence[Mapping[str, object]], key: str) -> List[Dict[str, object]]:
    """Sort already-normalized, schema-shaped dicts by their own primary
    key, with a full canonical-bytes tiebreak so entries that share a
    primary key (a caller-rejected but still defensively handled
    identity collision) still resolve to one deterministic order rather
    than depending on input iteration order.
    """
    normalized = [dict(item) for item in items]
    return sorted(normalized, key=lambda item: (str(item[key]), _canonical_tiebreak(item)))


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
    # ``expected``/``observed`` are a probe's own comparison payload --
    # never rendered verbatim in any artifact. Only their sha256 digests
    # are recorded, so the manifest can still prove *which* expected/
    # observed pair a probe's status was decided against (e.g. for
    # replay/dispute), without ever carrying the payload value itself.
    return {
        "probe_id": probe.probe_id,
        "action_id": probe.action_id,
        "path_id": probe.path_id,
        "status": probe.status,
        "reason_code": probe.reason_code,
        "expected_sha256": f"sha256:{canonical.sha256_hex(probe.expected.encode('utf-8'))}",
        "observed_sha256": f"sha256:{canonical.sha256_hex(probe.observed.encode('utf-8'))}",
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
    dependencies = _sorted_mappings_by_canonical(
        (_normalize_pin(pin) for pin in pins.get("dependencies", ())), "name"
    )
    specifications = _sorted_mappings_by_canonical(
        (_normalize_pin(pin) for pin in pins.get("specifications", ())), "name"
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
    normalized = ({"path": str(entry["path"]), "sha256": str(entry["sha256"])} for entry in policy_hashes)
    return _sorted_mappings_by_canonical(normalized, "path")


def _normalize_change_plane_workflow(entry: Mapping[str, object]) -> Dict[str, object]:
    return {"path": str(entry["path"]), "sha256": str(entry["sha256"])}


def _normalize_change_plane_identity(entry: Mapping[str, object]) -> Dict[str, object]:
    return {"identity": str(entry["identity"]), "kind": str(entry["kind"])}


def _normalize_change_plane(change_plane: Mapping[str, object], default_repository: str) -> Dict[str, object]:
    repository = change_plane.get("repository", default_repository)
    workflows = _sorted_mappings_by_canonical(
        (_normalize_change_plane_workflow(entry) for entry in change_plane.get("workflows", ())), "path"
    )
    identities = _sorted_mappings_by_canonical(
        (_normalize_change_plane_identity(entry) for entry in change_plane.get("identities", ())),
        "identity",
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
    return _sorted_mappings_by_canonical((_normalize_claim(entry) for entry in claims), "claim_id")


def _sorted_reports(reports: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    return _sorted_mappings_by_canonical((_normalize_report(entry) for entry in reports), "report_id")


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
# 30th of February, or one that fails to parse for any other reason, or
# names more fractional-second digits than a ``datetime`` can represent
# without silently truncating them) is never allowed to escape as an
# uncaught ``ValueError`` here -- it is instead treated exactly like a
# *missing* ``collected_at``: excluded from every timestamp aggregation so
# freshness degrades conservatively (to the schema's own ``stale``/``null``
# vocabulary) rather than crashing, silently trusting an untrustworthy
# value, or silently rounding/truncating a value into a false conclusion.
# ---------------------------------------------------------------------------


#: ``datetime`` (and this module's own rendered/compared instants) can
#: only ever represent whole microseconds. Rather than silently truncating
#: any additional fractional-second digits an evidence timestamp names --
#: which could shift the instant actually used for a freshness comparison
#: without any visible sign that precision was lost -- a timestamp naming
#: more than this many fractional digits is rejected outright.
_MAX_SUPPORTED_FRACTIONAL_DIGITS = 6
_FRACTIONAL_SECONDS_RE = re.compile(r"\.(\d+)")


def _parse_rfc3339(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    match = _FRACTIONAL_SECONDS_RE.search(text)
    if match and len(match.group(1)) > _MAX_SUPPORTED_FRACTIONAL_DIGITS:
        raise ValueError(
            "RFC 3339 timestamp names more fractional-second digits than "
            f"this assessor can represent without truncation: {value!r}"
        )
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _try_parse_rfc3339(value: Optional[str]) -> Optional[datetime]:
    """Parse *value* to an aware ``datetime``, or ``None`` if it is absent
    or cannot be parsed as a real RFC 3339 instant.

    Never raises: any parsing failure (``ValueError`` for a nonexistent
    calendar date/time such as a leap-day-31st or for more fractional-
    second digits than a ``datetime`` can represent without truncation,
    ``OverflowError`` for a year outside the platform's representable
    range, or ``TypeError`` for a non-string) is caught and reported as
    ``None`` -- an untrustworthy or absent timestamp look identical to
    every caller downstream.
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


def _required_trustworthy_timestamp_pairs(result: AssessmentResult) -> List[Tuple[datetime, str]]:
    """Every *required* evidence ``collected_at`` that parses to a real
    instant, paired with its own original (never reformatted) string,
    sorted by ``(instant, original string)``.

    "Required" means the evidence is actually cited by a finding's or an
    application-path probe's own ``evidence_refs``
    (:func:`_required_evidence_ids`) -- freshness is a claim about the
    evidence this manifest's findings/probes actually depend on to
    justify their own status, never about incidental evidence nothing
    here needed. An evidence entry that no finding or probe cites can be
    perfectly fresh (or perfectly stale) without changing
    ``oldest_source_at``/``expires_at`` at all.

    Sorting by the parsed instant first (not the raw string) is what makes
    ``oldest_source_at`` correct regardless of per-entry UTC-offset or
    fractional-second spelling; the original string is kept as a
    deterministic tiebreaker for entries that name the identical instant
    in different but equivalent spellings, so which input ordering the
    caller happened to supply evidence in never changes which exact
    string is chosen to render.
    """
    required_ids = _required_evidence_ids(result)
    pairs: List[Tuple[datetime, str]] = []
    for ref in result.evidence:
        if ref.evidence_id not in required_ids:
            continue
        instant = _try_parse_rfc3339(ref.collected_at)
        if instant is not None:
            pairs.append((instant, ref.collected_at))
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return pairs


def _required_evidence_ids(result: AssessmentResult) -> Set[str]:
    """Every evidence id a finding or an application-path probe actually
    relies on to justify its own status.

    A finding/probe that references no evidence at all contributes
    nothing here; this deliberately mirrors the design's own phrase
    "evidence refs needed for findings/probes" rather than every
    ``PathRecord.evidence_refs`` a mediation path happens to cite.
    """
    ids: Set[str] = set()
    for finding in result.findings:
        ids.update(finding.evidence_refs)
    for probe in result.probes:
        ids.update(probe.evidence_refs)
    return ids


def _canonical_policy_set_sha256(result: AssessmentResult) -> str:
    """The canonical policy-set digest this assessment's own
    ``policy_hashes`` collapse to.

    Computed exactly like :func:`canonical.hash_files`'s own
    ``set_sha256`` -- the established representation for a hashed *set*
    of ``{path, sha256}`` entries: normalized, sorted by path, then
    canonically serialized and hashed -- so an evidence entry's own
    declared ``policy_set_sha256`` can be checked against the *actual*
    policy set this assessment observed, never an invented expectation.
    """
    normalized = _normalize_policy_hashes(result.policy_hashes)
    return f"sha256:{canonical.sha256_hex(canonical.canonical_bytes(normalized))}"


def _evidence_finding_phases(result: AssessmentResult) -> Dict[str, Set[str]]:
    """Every evidence id mapped to the set of distinct ``Finding.phase``
    values among findings that cite it in their own ``evidence_refs``.

    Probes are deliberately excluded here -- ``ProbeResult`` carries no
    ``phase`` of its own, so there is nothing authoritative to compare an
    evidence entry's ``phase`` against for a probe-only reference, and
    this module never invents one.
    """
    phases_by_evidence: Dict[str, Set[str]] = {}
    for finding in result.findings:
        for evidence_id in finding.evidence_refs:
            phases_by_evidence.setdefault(evidence_id, set()).add(finding.phase)
    return phases_by_evidence


def _required_evidence_is_untrustworthy(
    result: AssessmentResult, captured_instant: Optional[datetime]
) -> bool:
    """True when at least one finding or probe *requires* an evidence
    entry that fails any of this module's identity/timestamp trust
    checks:

    * the referenced ``evidence_id`` has no matching entry in
      ``result.evidence`` at all;
    * the entry it matches has a missing/unparseable ``collected_at``;
    * ``collected_at`` names an instant strictly after this assessment's
      own trusted ``captured_at`` instant -- evidence can never have been
      collected in the future relative to the assessment that cites it;
    * ``repository``/``source_commit`` does not match this assessment's
      own ``result.source`` -- foreign-repository or wrong-commit
      evidence can never be trusted for *this* assessment;
    * ``phase`` disagrees with every finding that cites it -- an evidence
      entry whose own ``phase`` matches *any one* of the (possibly more
      than one distinct) phases among the findings that cite it in their
      own ``evidence_refs`` remains trusted, but an entry cited only by
      findings whose phases it matches none of is not (a reference cited
      only by probes, which carry no ``phase`` of their own, is left
      unchecked here rather than guessing which phase is authoritative);
    * a non-null ``policy_set_sha256`` does not match the canonical
      digest this assessment's own ``policy_hashes`` collapse to.

    This check is deliberately independent of how many *other*, unrelated
    evidence entries happen to be trustworthy: an assessment that
    supplies plenty of fresh, correctly-bound evidence for findings/
    probes that never cited the untrustworthy entry must never mask the
    one finding or probe whose own required evidence fails one of these
    checks, and must never render an overall ``fresh`` freshness verdict
    -- or a ``governed`` summary verdict -- on the strength of evidence
    nothing actually needed.

    Deliberately does *not* check a "target environment" or "tested
    tuple" expectation: ``AssessmentResult`` carries no authoritative
    expected value for either today, and this module never invents one
    to compare against -- a real check there would need a new, explicit
    assessment-level binding field, not a fabricated expectation.
    """
    required_ids = _required_evidence_ids(result)
    if not required_ids:
        return False
    evidence_by_id = {ref.evidence_id: ref for ref in result.evidence}
    finding_phases_by_evidence = _evidence_finding_phases(result)
    expected_policy_set_sha256 = _canonical_policy_set_sha256(result)
    for evidence_id in required_ids:
        ref = evidence_by_id.get(evidence_id)
        if ref is None:
            return True
        instant = _try_parse_rfc3339(ref.collected_at)
        if instant is None:
            return True
        if captured_instant is not None and instant > captured_instant:
            return True
        if ref.repository != result.source.repository:
            return True
        if ref.source_commit != result.source.commit:
            return True
        finding_phases = finding_phases_by_evidence.get(evidence_id, set())
        if finding_phases and ref.phase not in finding_phases:
            return True
        if ref.policy_set_sha256 is not None and ref.policy_set_sha256 != expected_policy_set_sha256:
            return True
    return False


def _phase_for(result: AssessmentResult) -> str:
    """The manifest/evidence-pack's assessment-level lifecycle phase.

    ``result.phase`` -- when an orchestrator has supplied it, mirroring
    ``AssessmentOptions.phase`` -- is authoritative: it names what phase
    this assessment run was actually judging, never a computed aggregate.

    When it is absent (``None``, the common case before such an
    orchestrator exists), the phase is derived conservatively from the
    assessment's own assertions -- the distinct ``Finding.phase`` values
    present -- taking the latest lifecycle stage among them. Evidence's own
    ``EvidenceRef.phase`` is never consulted here: it describes when a
    piece of evidence was collected, not what phase the assessment itself
    claims to be judging, so an uncited, probe-only, or explicitly
    distrusted evidence record can never escalate (or otherwise redefine)
    the assessment's own lifecycle claim. An assessment with no findings at
    all (for example, a probe-only run) conservatively defaults to
    ``"design"``, the earliest supported phase.
    """
    if result.phase is not None:
        return result.phase
    finding_phases = {finding.phase for finding in result.findings}
    if not finding_phases:
        return "design"
    return max(finding_phases, key=contracts.SUPPORTED_PHASES.index)


def _freshness_from_pairs(
    pairs: Sequence[Tuple[datetime, str]],
    captured_instant: Optional[datetime],
    required_evidence_untrustworthy: bool = False,
) -> Dict[str, object]:
    if not pairs:
        # No *required* evidence carries a trustworthy collection
        # timestamp (either none was ever supplied, or every supplied
        # value failed to parse as a real instant, or no finding/probe
        # required any evidence at all): freshness cannot be proven, so
        # this is recorded as conservatively as the schema's fixed status
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
    if captured_instant is None:
        # The assessment's own trusted capture instant is missing or
        # unparseable: this is exactly as untrustworthy as having no
        # required evidence at all, so it degrades the same way -- never
        # a fabricated "expired" just because ``None`` compares as
        # "not <= expires_instant" -- and never a fabricated "fresh"
        # either.
        status = "stale"
    elif captured_instant <= expires_instant:
        # Otherwise-fresh evidence can still fail to make a finding or
        # probe's own *required* evidence trustworthy: a report crowded
        # with fresh, unrelated evidence must never mask the one required
        # reference this manifest cannot actually vouch for, so that case
        # is reported "stale" -- never a fabricated "fresh" -- even though
        # the raw oldest instant and the assessment's own capture instant
        # pass the freshness window.
        status = "stale" if required_evidence_untrustworthy else "fresh"
    else:
        # The assessment's trusted capture instant exceeds the oldest
        # trustworthy required-evidence instant plus valid_for_hours.
        status = "expired"
    return {
        "status": status,
        "valid_for_hours": FRESHNESS_VALID_FOR_HOURS,
        "oldest_source_at": oldest_source_at,
        "expires_at": _format_rfc3339(expires_instant),
    }


# ---------------------------------------------------------------------------
# Summary / verdict
# ---------------------------------------------------------------------------


def _summary(
    findings: Sequence[Dict[str, object]],
    dirty: bool,
    required_evidence_untrustworthy: bool,
    freshness_unproven: bool,
) -> Dict[str, object]:
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
    elif should_fix or not_verified or dirty or required_evidence_untrustworthy or freshness_unproven:
        # A dirty source tree can never bind evidence to a unique commit,
        # so it can never earn "governed". Exactly the same truthful
        # degradation applies when a finding's or probe's own *required*
        # evidence fails this module's identity/timestamp trust checks
        # (foreign repository, wrong commit, a future timestamp, a
        # mismatched phase or policy-set binding), or when required
        # evidence exists but this manifest's own rendered freshness
        # status is anything other than ``"fresh"``: ``"expired"`` (the
        # assessment's own trusted capture instant exceeds
        # ``oldest_source_at`` plus ``valid_for_hours``), or ``"stale"``
        # -- most commonly a missing/unparseable ``captured_at``, which
        # :func:`_freshness_from_pairs` itself already degrades to
        # ``"stale"`` rather than inventing a trusted instant to compare
        # against, so an otherwise all-trustworthy set of required
        # evidence can never launder into "governed" on the strength of a
        # capture instant this manifest cannot actually vouch for.
        # Without inventing a finding beyond what the assessor actually
        # observed, the truthful verdict is "partial". ``freshness_unproven``
        # is deliberately gated on *required* timestamp pairs actually
        # existing: an assessment with no required evidence at all
        # (nothing here needed evidentiary proof of freshness) renders a
        # legitimate ``"stale"`` freshness status over an empty window,
        # and that alone must never prevent "governed".
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

    Raises :class:`DuplicateEvidenceIdError` before any other processing
    if ``result.evidence`` contains two or more entries sharing the same
    ``evidence_id`` -- the schema itself permits this, but this module's
    identity-keyed freshness/evidence-index logic cannot safely tolerate
    it (see that error's own docstring).
    """
    _reject_duplicate_evidence_ids(result.evidence)
    residual_risks = _assemble_residual_risks(result)
    risk_ids = {str(entry["residual_risk_id"]) for entry in residual_risks}
    findings = [_finding_to_dict(finding, risk_ids) for finding in _sorted_findings(result.findings, risk_ids)]
    # ``captured_at`` is the assessment's own trusted capture instant --
    # never derived from evidence, and never the newest evidence
    # timestamp this manifest happens to carry. A missing or unparseable
    # ``result.captured_at`` degrades conservatively: the rendered field
    # still names a schema-valid timestamp (the field is required and
    # non-null), but ``captured_instant`` used for the freshness
    # comparison below becomes ``None``, which :func:`_freshness_from_pairs`
    # treats as untrustworthy (``stale``), never a fabricated real instant.
    captured_instant = _try_parse_rfc3339(result.captured_at)
    captured_at = result.captured_at if captured_instant is not None else FALLBACK_TIMESTAMP
    required_timestamp_pairs = _required_trustworthy_timestamp_pairs(result)
    required_evidence_untrustworthy = _required_evidence_is_untrustworthy(result, captured_instant)
    freshness = _freshness_from_pairs(required_timestamp_pairs, captured_instant, required_evidence_untrustworthy)
    # Gated on *required* timestamp pairs actually existing -- see
    # ``_summary``'s own docstring comment for why an assessment with no
    # required evidence at all must not be penalized for the legitimate
    # ``"stale"`` freshness status ``_freshness_from_pairs`` renders over
    # an empty window.
    freshness_unproven = bool(required_timestamp_pairs) and freshness["status"] != "fresh"

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
        "freshness": freshness,
        "residual_risks": residual_risks,
        "summary": _summary(
            findings,
            result.source.dirty,
            required_evidence_untrustworthy,
            freshness_unproven,
        ),
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


#: Matches a bare ``scheme://`` run (e.g. ``https://``) so it can be
#: defused even with no surrounding brackets or angle brackets at all --
#: GitHub Flavored Markdown's *extended autolink* extension recognizes a
#: bare run like this (or a bare ``www.``) as a live, clickable link
#: without requiring either syntax this function already escapes.
#:
#: Deliberately has **no** word-boundary anchor before the scheme: cmark-
#: gfm's own scanner finds ``://`` and then rewinds through as many
#: alphabetic/``[a-z0-9+.-]`` scheme characters as it can, with no
#: requirement that whatever precedes the scheme be "not part of the same
#: word" -- a Python ``\b`` check wrongly refuses to match when the
#: preceding character is itself a word character (a digit, an
#: underscore, or a letter), so ``4https://``, ``_https://``, and
#: ``x_https://`` were all real, live GFM autolinks that a boundary-
#: anchored pattern silently failed to defuse.
_BARE_URL_SCHEME_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*)://")


#: Matches a bare ``www.`` run, in *any* letter case -- GFM's own
#: ``www_match`` recognizer (cmark-gfm's ``extensions/autolink.c``) lower-
#: cases each candidate byte before comparing it against ``"www."``, so
#: ``WWW.``/``Www.``/etc. are just as live a trigger as lowercase
#: ``www.``; a plain case-sensitive substring match would silently miss
#: every non-lowercase spelling.
#:
#: Deliberately has **no** word-boundary anchor either, for the same
#: reason as ``_BARE_URL_SCHEME_RE`` above: cmark-gfm's ``www_match``
#: scan has no preceding-character restriction, so ``_www.`` is exactly
#: as live an autolink trigger as ``www.`` on its own.
_BARE_WWW_RE = re.compile(r"(?i)www\.")


#: Matches a bare ``local@domain.tld``-shaped run so it can be defused
#: even with no surrounding angle brackets -- GitHub Flavored Markdown's
#: extended autolink extension also recognizes a bare email address like
#: this as a live ``mailto:`` link, entirely independent of the
#: ``scheme://``/``www.`` triggers above.
#:
#: Modeled on GFM's own "extended email autolink" grammar (spec section
#: on autolinks): the local part is one or more characters that are
#: alphanumeric, or ``.``/``-``/``_``/``+`` (no requirement that it start
#: with an alphanumeric -- GFM's own rewind-from-``@`` scan does not
#: require that either); the domain is one or more ``.``-separated
#: segments of alphanumeric/``-``/``_`` characters, with at least one
#: period, and the final character must be alphanumeric (never ``-``/
#: ``_``). Unlike GFM's separate ``www.``/``scheme://`` domain grammar
#: (``check_domain`` in cmark-gfm), the *email* grammar does **not**
#: reject underscores in the last two segments -- ``foo@bar_baz.example``
#: is a real, live GFM autolink -- so this pattern deliberately allows
#: ``_`` (and ``-``) throughout every domain segment, in any letter case.
_BARE_EMAIL_RE = re.compile(
    r"(?i)([a-z0-9._%+\-]+)@((?:[a-z0-9_-]+\.)+[a-z0-9_-]*[a-z0-9])"
)


def _wrap_code_span_fragment(fragment: str) -> str:
    """Wrap *fragment* (never containing a backtick -- every input
    backtick is already replaced with a plain apostrophe earlier in
    :func:`_md_escape_inline`) in a *double*-backtick-delimited inline
    code span.

    A code span's content is never re-scanned for inline constructs --
    not by CommonMark's own core syntax (links, emphasis, raw HTML), and
    not by GitHub Flavored Markdown's *extended autolink* extension
    either, since that extension (like any linkify-style extension)
    recognizes a bare URL/``www.``/email trigger by scanning the
    already-assembled plain-text content of the document, and a code
    span's content is excluded from that scan entirely -- once a run of
    characters is consumed as a code span, no later positional scan ever
    revisits it, and no positional scan ever reaches back across a code
    span's boundary to stitch text from either side of it into one
    contiguous run either. This is what makes a code span -- unlike a
    backslash escape -- a representation that is actually inert in GFM:
    a backslash escape is only a *parse-time* marker that gets stripped
    before the assembled plain-text content is ever handed to the
    autolink extension, so the trigger substring reforms exactly as if
    the backslash had never been there, whereas a code span's delimiters
    survive as real structural boundaries all the way through parsing.

    Uses a *double*-backtick delimiter (not a single one) so two of this
    function's own inserted spans landing back-to-back with nothing
    between them (see :data:`_TOUCHING_CODE_SPAN_DELIMITERS_RE` below)
    can be told apart from a single four-backtick run and split back
    apart, and so that if this value is never additionally wrapped, the
    double backticks still open and close a perfectly ordinary code span
    on their own. This function is only ever used inside
    :func:`_md_escape_inline`, for prose/table-field values; identifier
    fields use :func:`_md_code_span` instead, which wraps a value's own
    *raw* content directly and never calls (or is called by)
    :func:`_md_escape_inline` -- the two are deliberately independent,
    non-composing sanitizers, so this function's own delimiters are
    never nested inside (or wrapped a second time by) the other.
    """
    return f"``{fragment}``"


#: Guards against two independently-wrapped code spans this function
#: inserts landing back-to-back with zero characters between them (this
#: happens whenever, e.g., a bare ``scheme://`` run is immediately
#: followed by a bare ``user@domain`` run with no separator, as in
#: ``https://user@evil.example``): CommonMark's code-span algorithm
#: matches a *maximal* run of backticks as one single delimiter, so two
#: adjacent, unrelated double-backtick pairs with nothing between them
#: would merge into one ambiguous four-backtick run and desynchronize
#: both spans' intended open/close pairing. Splitting any such run with
#: a real (if invisible) character in between keeps each of this
#: function's own double-backtick pairs independently well-formed.
_TOUCHING_CODE_SPAN_DELIMITERS_RE = re.compile(r"``(?=`)")


def _md_escape_inline(value: object) -> str:
    """Sanitize *value* for inclusion in a single rendered Markdown table
    cell, list item, or heading-adjacent line, so it can only ever render
    as safe, inert literal text -- never a live image, link, autolink, or
    raw HTML element.

    Collapses any embedded newline/carriage-return -- which could
    otherwise inject a forged extra table row or a spurious new heading
    line into the rendered pack -- to a single space. Every literal ``&``
    in the *input* is then escaped to ``&amp;`` -- before any other
    escaping step below, and in particular before this function
    introduces any ``&``-led escape of its own -- so that a decimal
    (``&#64;``), hexadecimal (``&#x40;``/``&#X40;``, either case), or
    named (``&commat;``, ``&lt;``, ``&NewLine;``, ...) character
    reference smuggled in by an assessment string can never be decoded by
    a downstream CommonMark/GFM renderer back into a live metacharacter
    (``@``, ``<``, a newline, ...) ahead of that renderer's own
    autolink/raw-HTML recognition -- GitHub Flavored Markdown's extended
    autolink extension in particular scans *already entity-decoded* text,
    so an un-neutralized entity reference is a complete bypass of every
    literal-character escape this function applies below. Because this
    step runs first and touches only ``&`` characters already present in
    the input, it can never re-escape (double-escape) an ``&lt;``/
    ``&gt;`` this function itself inserts further down. A literal
    backslash is escaped next, before any other character below, so a
    value ending in a backslash immediately before one of the characters
    this function itself escapes next can never combine with this
    function's own inserted escape backslash to look like an escaped
    backslash followed by unescaped, live syntax. ``[``, ``]``, ``(``,
    and ``)`` are then backslash-escaped: CommonMark/GFM require all of
    literal, unescaped brackets *and* parentheses to recognize either
    inline-link (``[text](url)``), image (``![alt](src)``), or
    reference-link (``[text][ref]``) syntax, so escaping every one of
    them makes every form -- regardless of URL scheme, including
    ``javascript:`` -- inert text rather than a clickable link or an
    image that would load an external resource. ``|`` is escaped (would
    otherwise split a table row into extra cells). A backtick is replaced
    with a plain apostrophe (would otherwise let the value break out of
    an inline code span this module wraps identifier-like values in
    elsewhere). ``<``/``>`` are HTML-entity-escaped, which defeats both a
    raw HTML tag and an angle-bracket autolink (``<https://...>``):
    neither can ever open or close once escaped.

    Finally, a bare ``scheme://`` run, a bare ``www.`` run (in any letter
    case), or a bare ``local@domain.tld``-shaped email address (in any
    letter case) -- any of which GitHub Flavored Markdown's extended
    autolink extension can turn into a live link with no brackets or
    angle brackets present at all -- has its exact trigger substring
    wrapped in its own double-backtick-delimited inline code span (see
    :func:`_wrap_code_span_fragment`) rather than merely backslash-
    escaped. A backslash escape is *not* effective here: it is only a
    parse-time marker that a CommonMark/GFM renderer strips before ever
    handing the assembled plain-text content to the autolink extension,
    so the exact same trigger substring (``https://``, ``www.``,
    ``user@domain``) reforms in that assembled text regardless of
    whether a backslash preceded it in the source -- ``https:\\/\\/``,
    ``www\\.``, and ``user\\@domain`` all still render as live GFM
    autolinks. A code span's content, in contrast, is a genuine
    structural boundary that survives parsing: it is never re-scanned by
    the autolink extension (or any other inline construct), and text on
    either side of it is never stitched back together into one
    contiguous run for the extension to match against, so wrapping the
    trigger substring this way is actually inert rather than only
    apparently escaped. Two such wraps landing back-to-back with nothing
    between them (e.g. a bare ``scheme://`` run immediately followed by
    a bare ``user@domain`` run) are kept apart by inserting a single
    invisible character between them, so their double-backtick
    delimiters can never merge into one ambiguous longer backtick run.

    All of this keeps the value fully human-readable; it is applied to
    every assessment-influenceable string this module ever interpolates
    into a table cell, list item, or heading-adjacent line, and this
    module never renders a probe's raw ``expected``/``observed`` payload
    value here at all, so there is nothing to leak through this escaping.
    """
    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;")
    text = text.replace("\\", "\\\\")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace("(", "\\(").replace(")", "\\)")
    text = text.replace("|", "\\|")
    text = text.replace("`", "'")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = _BARE_URL_SCHEME_RE.sub(lambda match: _wrap_code_span_fragment(match.group(0)), text)
    text = _BARE_WWW_RE.sub(lambda match: _wrap_code_span_fragment(match.group(0)), text)
    text = _BARE_EMAIL_RE.sub(lambda match: _wrap_code_span_fragment(match.group(0)), text)
    text = _TOUCHING_CODE_SPAN_DELIMITERS_RE.sub("``\u200b", text)
    return text


def _md_code_span(value: object) -> str:
    """Wrap *value*'s own raw content -- verbatim, never pre-escaped by
    :func:`_md_escape_inline` -- in a single Markdown inline code span
    that is always well-formed and therefore always inert, for an
    identifier-like field (an action/path/probe/finding/residual-risk id,
    a repository, or a commit).

    This function and :func:`_md_escape_inline` are deliberately
    independent, non-composing sanitizers. Composing them -- wrapping an
    already-escaped value (which may itself contain
    :func:`_wrap_code_span_fragment`'s own double-backtick-delimited
    spans for an embedded bare URL/``www.``/email trigger) in this
    function's own delimiter -- used to place this function's opening
    backtick directly adjacent, with zero characters between them, to an
    inner fragment's own leading double backtick, merging the two into
    one ambiguous, longer backtick run with no matching closing run
    anywhere in the string. CommonMark then fails to parse *any* code
    span there at all, so the raw trigger substring falls back to being
    scanned as ordinary text -- exactly the kind of bare
    ``user@evil.example``-shaped substring GitHub Flavored Markdown's
    extended autolink extension turns into a live ``mailto:`` link. (For
    example, the identifier ``user@evil.example/x`` used to round-trip
    through ``_md_escape_inline`` as ``` ``user@evil.example``/x ```,
    and wrapping *that* in one more pair of single backticks produced
    ` ```user@evil.example``/x` `, whose opening triple-backtick run
    never finds a matching triple-backtick closing run.)

    A code span's content is raw, literal text: unlike ordinary Markdown
    text content, it is never entity-decoded, never re-scanned for
    inline constructs (links, emphasis, raw HTML), and never rescanned by
    GitHub Flavored Markdown's extended autolink extension either (see
    :func:`_wrap_code_span_fragment`'s own docstring for why). Wrapping
    the *entire* raw value once is therefore already sufficient, by
    construction, to neutralize every metacharacter this module's
    prose/table escaping otherwise has to handle one at a time
    (backticks, pipes, brackets, parentheses, angle brackets, entity
    references, bare URL/``www.``/email autolink triggers) -- there is
    nothing left for a separate escaping pass to do, so this function
    never calls :func:`_md_escape_inline`.

    The delimiter itself is computed from *value*'s own content rather
    than fixed at one backtick, so it can never be defeated by a value
    that itself contains a backtick run: it is one backtick longer than
    the longest run of consecutive backticks found anywhere in *value*
    (including at either edge), which guarantees no substring inside the
    content can ever be mistaken by CommonMark's maximal-backtick-run
    matching for a same-length closing delimiter. If the content starts
    or ends with a literal backtick, a single literal space is added on
    that side first -- not to be confused with the delimiter itself --
    so the delimiter's own backticks are never directly adjacent (zero
    characters between them) to a leading/trailing backtick run *in the
    content*, which is exactly the same adjacency-merging hazard this
    function's own composition bug above came from, just internal to one
    value instead of between two nested wraps.
    """
    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    longest_run = 0
    current_run = 0
    for char in text:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    delimiter = "`" * (longest_run + 1)
    if text.startswith("`"):
        text = " " + text
    if text.endswith("`"):
        text = text + " "
    if not text:
        text = " "
    return f"{delimiter}{text}{delimiter}"


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
    """Compute *relative*'s destination path anchored at *root*, purely
    lexically.

    This never touches the filesystem for the *relative* portion of the
    path -- no ``.resolve()``, no symlink traversal, only a plain,
    in-memory ``.``/``..`` segment-stack normalization -- so the
    destination keeps its own literal identity exactly as named,
    including when the final (leaf) path component is itself a symlink.
    Resolving the *whole* candidate path here (as an earlier version of
    this function did) would silently substitute a destination-leaf
    symlink's resolved target for the destination itself, so every later
    check -- starting with preflight's own ``lstat`` of this very return
    value -- would then inspect the *target* rather than the symlink,
    exactly the destination-leaf-symlink attack this module must reject
    rather than silently follow. A ``..`` that would walk back past
    *root* itself is rejected outright as an escape attempt; every other
    ``..``/``.`` segment is resolved lexically against the segments
    already accumulated, never by asking the filesystem to resolve an
    actual (possibly symlinked) ancestor directory. Ancestor-directory
    symlinks are never followed at this stage either -- they are instead
    caught later, when this destination's ancestors are opened one
    no-follow directory at a time from an already-verified root.
    """
    relative = Path(relative)
    if relative.is_absolute():
        raise ArtifactWriteError(
            f"artifact path must be repository-relative, not absolute: {relative}"
        )
    root_resolved = root.resolve()
    stack: List[str] = []
    for part in relative.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise ArtifactWriteError(
                    f"artifact path escapes the assessed root: {relative}"
                )
            stack.pop()
            continue
        stack.append(part)
    if not stack:
        raise ArtifactWriteError(f"artifact path must not be empty: {relative}")
    return root_resolved.joinpath(*stack)


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


def _open_verified_parent(root_fd: int, root_resolved: Path, dest: Path) -> Tuple[int, str]:
    """Open and return a held-open, verified, no-follow directory fd for
    *dest*'s immediate parent (creating missing ancestor directories as
    needed), plus *dest*'s own leaf (final path-component) name.

    The caller owns the returned fd and must close it exactly once, but
    -- unlike a fresh reopen-and-close-immediately check -- is expected to
    keep it open across every subsequent stage/backup/commit/rollback/
    cleanup step for this destination. Because the fd is bound to the
    parent directory's own inode rather than to a path string, every
    later descriptor-relative operation performed against it (``dir_fd=``)
    still reaches the exact verified directory this fd was opened
    against, even if a *later* action renames, removes, or symlink-swaps
    that directory (or any of its own ancestors) at the path level --
    closing the exact TOCTOU window a reopen-by-path-every-time design
    would otherwise leave between one check and the next path-based
    operation.
    """
    parts = dest.relative_to(root_resolved).parts
    if not parts:
        raise ArtifactWriteError(
            f"artifact destination must not be the assessed root itself: {dest}"
        )
    parent_fd = _open_verified_dir_fd(root_fd, parts[:-1], dest.parent)
    return parent_fd, parts[-1]


def _fd_leaf_exists(parent_fd: int, name: str) -> bool:
    try:
        os.lstat(name, dir_fd=parent_fd)
        return True
    except FileNotFoundError:
        return False


def _fd_unlink_ignore_missing(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _stage_temp_file(parent_fd: int, dest: Path, leaf_name: str, data: bytes) -> str:
    """Stage *data* as a new, race-resistant temp file inside the
    already-open, already-verified *parent_fd* directory, and return its
    single-component staging name.

    Creates a randomly named temp file with
    ``O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW`` directly under
    *parent_fd* -- never by deriving or (re-)opening a path -- so staging
    itself can never be tricked into writing through a symlink, even if
    an ancestor directory was swapped after *parent_fd* was opened: the
    fd is bound to the verified directory's own inode, not to a path
    string. On any write/fsync failure the partial temp file is removed
    and the failure is re-raised as a bounded :class:`ArtifactWriteError`
    (never a raw, unbounded exception).
    """
    candidate: Optional[str] = None
    fd: Optional[int] = None
    for _attempt in range(8):
        candidate = f".{leaf_name}.{uuid.uuid4().hex}.stage"
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
        raise ArtifactWriteError(f"could not allocate a unique staging name for {dest}")
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
    return candidate


def _unique_backup_name(leaf_name: str) -> str:
    # A pure name generator with no filesystem interaction: collision
    # probability with a random UUID4 hex suffix is negligible, and this
    # avoids the create-then-unlink round trip (itself a small window for
    # a race) a filesystem-backed unique-name allocator would otherwise
    # need.
    return f".{leaf_name}.{uuid.uuid4().hex}.bak"


def _fsync_dir(dest: Path, parent_fd: int) -> None:
    """fsync *dest*'s parent directory via the already-open, held
    *parent_fd* -- never by reopening it by path -- so this fsync itself
    cannot be redirected by a later ancestor-directory swap either. *dest*
    is accepted only so a caller/spy can identify which artifact this
    fsync belongs to; it is never itself touched.
    """
    del dest
    os.fsync(parent_fd)


def _perform_replace(
    replace: Callable[[Path, Path], None],
    root_fd: int,
    root_resolved: Path,
    parent_fd: int,
    src_name: str,
    dst_name: str,
    src_display: Path,
    dst_display: Path,
    dest: Path,
) -> None:
    """Move *src_name* onto *dst_name*, both inside the already-verified,
    held-open *parent_fd* directory backing *dest*.

    When *replace* is still the default, untouched ``os.replace``, this
    performs a genuinely descriptor-relative, no-follow
    ``os.rename(..., src_dir_fd=parent_fd, dst_dir_fd=parent_fd)`` against
    the held-open verified directory fd directly -- immune to any
    ancestor-directory swap that happened after *parent_fd* was opened,
    since the fd is bound to the directory's own inode rather than to a
    path string. This keeps the production/default code path fully
    TOCTOU-safe end to end.

    When a test has substituted a custom *replace* callable (to simulate
    a bounded, injected failure partway through a transaction), that
    callable necessarily operates on plain path strings and so cannot
    benefit from the held-open fd's race-immunity on its own. Immediately
    before invoking it, *dest*'s full path is re-walked from the trusted
    root, one no-follow directory open at a time -- so an ancestor
    directory (or the leaf itself) swapped to a symlink/special file at
    any point up to this exact instant is still caught here and fails
    closed, rather than letting the injected, path-based callable write
    through it. This preserves full test-injectability of simulated
    failures without weakening the safety property under test.
    """
    if replace is os.replace:
        os.rename(src_name, dst_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        return
    parts = dest.relative_to(root_resolved).parts
    fresh_parent_fd = _open_verified_dir_fd(root_fd, parts[:-1], dest.parent)
    try:
        _verify_leaf_absent_or_regular(fresh_parent_fd, parts[-1], dest)
    finally:
        os.close(fresh_parent_fd)
    replace(src_display, dst_display)


def _open_verified_root(root: Path) -> Tuple[int, Path]:
    """Open and return a held-open, no-follow-verified fd for the assessed
    *root* directory itself, plus its purely lexical resolved ``Path``
    (built from the verified parent and the caller-named leaf, never by a
    second, independent filesystem resolve of the whole candidate).

    Resolving *root* with ``Path.resolve()`` and only then separately
    reopening that resolved path (the naive sequence this replaces)
    leaves a TOCTOU window in which the root itself could be renamed away
    and replaced -- e.g. by an attacker-controlled symlink -- between
    those two independent steps. Instead, only *root*'s parent is
    resolved (its own ancestors remain the same implicitly-trusted
    directories every other part of this module already treats as such);
    root's own leaf directory entry is then opened from that parent's fd
    with ``O_NOFOLLOW`` in one atomic step, so a root that is itself a
    symlink -- to anywhere, even an otherwise-harmless in-root directory
    -- is rejected outright rather than transparently followed.
    """
    root = Path(root)
    parent = root.parent
    if parent == root:
        # ``root`` names the filesystem root itself (e.g. "/"): there is
        # no distinct parent/leaf pair to no-follow-verify against, so
        # fall back to a single resolved open of the root exactly as
        # named.
        root_resolved = root.resolve()
        try:
            root_fd = os.open(root_resolved, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as error:
            raise ArtifactWriteError(
                f"could not open the assessed root directory: {root} ({error})"
            ) from error
        return root_fd, root_resolved
    parent_resolved = parent.resolve()
    try:
        parent_fd = os.open(parent_resolved, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise ArtifactWriteError(
            "could not open the assessed root's parent directory: "
            f"{parent_resolved} ({error})"
        ) from error
    try:
        try:
            root_fd = os.open(
                root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
            )
        except OSError as error:
            raise ArtifactWriteError(
                "the assessed root is unsafe (missing, not a plain "
                f"directory, or itself a symlink): {root} ({error})"
            ) from error
    finally:
        os.close(parent_fd)
    root_resolved = parent_resolved / root.name
    return root_fd, root_resolved


def _verify_root_still_bound(root_resolved: Path, root_fd: int, display: Path) -> None:
    """Re-verify that *root_resolved* is still, right now, exactly the
    same directory *root_fd* was opened against -- neither renamed away
    nor replaced (e.g. by a symlink) -- by comparing ``(st_dev, st_ino)``
    identity between a fresh no-follow ``lstat`` of the path and
    ``root_fd``'s own ``fstat``.

    Called at transaction checkpoints so a root-level swap that happens
    *after* the fd was opened (but before every mutating step has
    completed) is explicitly caught and this call fails closed, rather
    than silently trusting that the one check made when the fd was first
    opened still holds. Every actual mutating operation elsewhere in this
    module already goes through descriptor-relative (``dir_fd=``) calls
    anchored on this same held-open root fd, so this check is a
    deliberate, explicit rejection of the tampering itself -- not the
    only thing standing between a swap and an unsafe write.
    """
    try:
        current = os.lstat(root_resolved)
    except OSError as error:
        raise ArtifactWriteError(
            f"the assessed root is no longer accessible at {display}: {error}"
        ) from error
    expected = os.fstat(root_fd)
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ArtifactWriteError(
            "the assessed root was renamed or replaced (e.g. by a symlink) "
            f"during this call: {display}"
        )


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
    duplicates, no ancestor/descendant nesting) against their own purely
    lexical path identity, never a filesystem-resolved one -- so a
    destination whose own leaf is itself a symlink (even one pointing at
    an otherwise-harmless in-root regular file) is rejected here rather
    than silently treated as if it named that regular file directly. A
    verified, no-follow parent-directory fd is then opened for each
    destination and held open for the *entire remainder* of this call --
    staging, backup-aside, commit, rollback, and cleanup all operate
    against that one held-open fd via descriptor-relative
    (``dir_fd=``/``src_dir_fd=``/``dst_dir_fd=``) syscalls, never by
    re-deriving or re-opening a path. Because such an fd is bound to the
    directory's own inode rather than to a path string, every one of
    those operations remains safe even if an ancestor directory (or the
    whole subtree) is renamed, removed, or symlink-swapped by something
    else after that fd was opened -- there is no reopen-by-path window
    left for such a swap to redirect. The one exception is the injectable
    *replace* parameter itself: when a caller has substituted a custom,
    path-based callable (as tests do, to simulate a bounded failure), that
    callable is still invoked with real paths -- but only after this
    destination's full path has been freshly re-walked with the same
    no-follow verification immediately beforehand, so even that
    necessarily-path-based call still fails closed against a swap that
    happened up to that exact instant.

    If any step of the staging, backup, or replace phase fails, every
    backup already made is restored, every destination this call itself
    newly created is removed (its parent directory fsynced afterward too,
    not only a successfully restored backup's), and every staged temp file
    is cleaned up, before :class:`ArtifactWriteError` is raised -- the
    on-disk artifact set is left exactly as it was found, never a mix of
    old and new generations. If restoring a backup itself fails, that
    failure is never swallowed: the backup file is preserved (never
    deleted) for manual recovery, a successfully restored file's parent
    directory is fsynced, and the raised :class:`ArtifactWriteError`
    explicitly states that rollback did not fully restore the prior
    artifact set rather than claiming it did. Backups are deleted only
    once every replacement in the whole transaction has succeeded.

    The assessed root itself is opened once, up front, via a no-follow
    open of its own leaf directory entry from its (separately resolved)
    parent -- never by resolving the whole root path and only then
    separately reopening it by path, which would leave a TOCTOU window in
    which the root could be renamed away and replaced (e.g. by a symlink)
    between those two steps. A root that is itself a symlink is rejected
    outright. That same held-open root fd's identity is then explicitly
    re-verified (by comparing ``(st_dev, st_ino)``) at two further
    checkpoints -- immediately before preflight, and again immediately
    before the backup/commit mutating phase begins -- so a root-level
    rename-and-replace attack that happens *during* this call's own
    rendering or staging work is explicitly detected and rejected, rather
    than only implicitly tolerated because every mutating operation is
    already descriptor-relative and thus immune to it regardless.
    """
    root_fd, root_resolved = _open_verified_root(Path(root))
    try:
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

        _verify_root_still_bound(root_resolved, root_fd, Path(root))
        _preflight_destinations(root_resolved, [dest for dest, _data in destinations])

        # Open (and hold open for the rest of this call) one verified,
        # no-follow parent-directory fd per destination *before* any
        # staging begins, so every later step -- including the very first
        # one -- already benefits from full TOCTOU immunity.
        prepared: List[List[object]] = []  # [dest, parent_fd, leaf_name, data]
        try:
            for dest, data in destinations:
                parent_fd, leaf_name = _open_verified_parent(root_fd, root_resolved, dest)
                prepared.append([dest, parent_fd, leaf_name, data])
        except BaseException:
            for _dest, parent_fd, _leaf_name, _data in prepared:
                os.close(parent_fd)  # type: ignore[arg-type]
            raise
        try:
            for dest, parent_fd, leaf_name, _data in prepared:
                _verify_leaf_absent_or_regular(parent_fd, leaf_name, dest)  # type: ignore[arg-type]

            staged: List[Tuple[Path, int, str, str]] = []
            try:
                for dest, parent_fd, leaf_name, data in prepared:
                    staged_name = _stage_temp_file(parent_fd, dest, leaf_name, data)  # type: ignore[arg-type]
                    staged.append((dest, parent_fd, leaf_name, staged_name))  # type: ignore[arg-type]
            except BaseException as error:
                for _dest, parent_fd, _leaf_name, staged_name in staged:
                    _fd_unlink_ignore_missing(parent_fd, staged_name)
                if isinstance(error, ArtifactWriteError):
                    raise
                raise ArtifactWriteError(
                    f"failed to stage governed-actions artifacts: {error}"
                ) from error

            backups: List[Tuple[Path, int, str, str]] = []
            created_without_backup: List[Tuple[Path, int, str]] = []
            try:
                # Re-verified here (not just once, before preflight) so a
                # root-level rename/symlink-swap that happened *during*
                # staging is still caught before the mutating backup and
                # commit steps below ever run.
                _verify_root_still_bound(root_resolved, root_fd, Path(root))
                for dest, parent_fd, leaf_name, _staged_name in staged:
                    _verify_leaf_absent_or_regular(parent_fd, leaf_name, dest)
                    if _fd_leaf_exists(parent_fd, leaf_name):
                        backup_name = _unique_backup_name(leaf_name)
                        _perform_replace(
                            replace,
                            root_fd,
                            root_resolved,
                            parent_fd,
                            leaf_name,
                            backup_name,
                            dest,
                            dest.parent / backup_name,
                            dest,
                        )
                        backups.append((dest, parent_fd, leaf_name, backup_name))

                for dest, parent_fd, leaf_name, staged_name in staged:
                    _verify_leaf_absent_or_regular(parent_fd, leaf_name, dest)
                    _perform_replace(
                        replace,
                        root_fd,
                        root_resolved,
                        parent_fd,
                        staged_name,
                        leaf_name,
                        dest.parent / staged_name,
                        dest,
                        dest,
                    )
                    if not any(existing_dest == dest for existing_dest, _pf, _ln, _bn in backups):
                        created_without_backup.append((dest, parent_fd, leaf_name))
                    _fsync_dir(dest, parent_fd)
            except BaseException as error:
                rollback_errors: List[str] = []
                for dest, parent_fd, leaf_name in created_without_backup:
                    try:
                        os.unlink(leaf_name, dir_fd=parent_fd)
                    except OSError as unlink_error:
                        rollback_errors.append(
                            f"could not remove newly created artifact {dest}: {unlink_error}"
                        )
                        continue
                    try:
                        _fsync_dir(dest, parent_fd)
                    except OSError as fsync_error:
                        rollback_errors.append(
                            f"removed newly created artifact {dest} but could not "
                            f"fsync its parent directory (rollback may not be "
                            f"durable): {fsync_error}"
                        )
                for dest, parent_fd, leaf_name, backup_name in backups:
                    backup_display = dest.parent / backup_name
                    try:
                        _perform_replace(
                            replace,
                            root_fd,
                            root_resolved,
                            parent_fd,
                            backup_name,
                            leaf_name,
                            backup_display,
                            dest,
                            dest,
                        )
                    except OSError as restore_error:
                        # Never delete the backup when its own restore
                        # fails: it is the only remaining copy of the
                        # prior artifact, and is preserved here for
                        # manual recovery.
                        rollback_errors.append(
                            f"could not restore prior artifact at {dest} from "
                            f"backup {backup_display} (backup preserved for "
                            f"manual recovery): {restore_error}"
                        )
                        continue
                    try:
                        _fsync_dir(dest, parent_fd)
                    except OSError as fsync_error:
                        rollback_errors.append(
                            f"restored {dest} from backup {backup_display} but "
                            f"could not fsync its parent directory (backup "
                            f"preserved for manual verification): {fsync_error}"
                        )
                        continue
                    _fd_unlink_ignore_missing(parent_fd, backup_name)
                for _dest, parent_fd, _leaf_name, staged_name in staged:
                    _fd_unlink_ignore_missing(parent_fd, staged_name)
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

            for _dest, parent_fd, _leaf_name, backup_name in backups:
                _fd_unlink_ignore_missing(parent_fd, backup_name)

            return tuple(dest for dest, _pf, _ln, _sn in staged)  # type: ignore[return-value]
        finally:
            for _dest, parent_fd, _leaf_name, _data in prepared:
                os.close(parent_fd)  # type: ignore[arg-type]
    finally:
        os.close(root_fd)
