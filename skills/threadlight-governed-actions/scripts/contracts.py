"""Typed contracts for threadlight-governed-actions.

Frozen dataclasses describing the assessor's read-only observations: source
provenance, action/path inventories, evidence references, probe results, and
the structured findings the assessor emits. Nothing here executes an action,
mutates a target repository, or carries a prompt/argument/output/secret
payload — see ``canonical.py`` for the payload-free audit guard and the
canonical-hashing / atomic-write helpers used to bind evidence to exact file
bytes.

threadlight-governed-actions is read-only and observational: Agent Hooks is
cooperative/alpha and is never treated as a security boundary here, and
nothing in this module invents customer-specific policy, thresholds,
approvers, identities, or risk appetite — those are supplied by the target
repository/deployment and only ever referenced, never fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional, Tuple


SCHEMA_VERSION = "1.0.0"
ASSESSOR_VERSION = "0.1.0"
SKILL_VERSION = "0.1.0"

SUPPORTED_PHASES: Tuple[str, ...] = ("design", "pre-deploy", "post-deploy")
CONSEQUENCE_CLASSES: Tuple[str, ...] = (
    "read",
    "write",
    "external-egress",
    "irreversible",
)
EXECUTION_MODES: Tuple[str, ...] = (
    "interactive",
    "batch",
    "background",
    "subagent",
    "direct-tool",
    "provider-hosted-tool",
)
STATUSES: Tuple[str, ...] = (
    "pass",
    "must-fix",
    "should-fix",
    "not-verified",
    "not-applicable",
)
VERDICTS: Tuple[str, ...] = ("governed", "partial", "ungoverned")


class UnsafeTargetError(ValueError):
    """Raised when an assessment target is unsafe to read or reference.

    For example: a path that escapes the assessment root, or a live-mode
    request aimed at a target that was not explicitly opted into staging.
    """


Phase = Literal["design", "pre-deploy", "post-deploy"]
Status = Literal["pass", "must-fix", "should-fix", "not-verified", "not-applicable"]
Consequence = Literal["read", "write", "external-egress", "irreversible"]


@dataclass(frozen=True)
class SourceRef:
    repository: str
    commit: str
    dirty: bool


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    kind: str
    source: str
    sha256: str
    collected_at: Optional[str]
    freshness_seconds: Optional[int]
    live_verified: bool
    phase: Phase
    repository: str
    source_commit: str
    target_environment: Optional[str]
    policy_set_sha256: Optional[str]


@dataclass(frozen=True)
class Finding:
    finding_id: str
    status: Status
    phase: Phase
    plane: Literal["runtime", "change", "both"]
    reason_code: str
    summary: str
    details: str
    affected_actions: Tuple[str, ...] = ()
    affected_paths: Tuple[str, ...] = ()
    evidence_refs: Tuple[str, ...] = ()
    remediation_ids: Tuple[str, ...] = ()
    residual_risk_ref: Optional[str] = None


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    display_name: str
    aliases: Tuple[str, ...]
    owner: Optional[str]
    declaration_refs: Tuple[str, ...]
    implementation_refs: Tuple[str, ...]
    input_schema_sha256: Optional[str]
    output_schema_sha256: Optional[str]
    source: str
    consequence: Optional[Consequence]
    secondary_consequences: Tuple[Consequence, ...]
    reversible: Optional[bool]
    compensation_ref: Optional[str]
    execution_modes: Tuple[str, ...]
    provider_hosted: bool
    approval_required: Optional[bool]
    policy_ids: Tuple[str, ...] = ()
    policy_binding: Optional[str] = None
    binding_requires_approval: Optional[bool] = None
    binding_requires_output: Optional[bool] = None
    binding_requires_durable_audit: Optional[bool] = None
    known_runtime_paths: Tuple[str, ...] = ()
    inventory_status: Status = "not-verified"


@dataclass(frozen=True)
class PathRecord:
    path_id: str
    action_id: str
    mode: str
    nodes: Tuple[str, ...]
    pre_action_seam: Optional[str]
    equivalent_control_ref: Optional[str]
    covered: bool
    status: Status
    evidence_refs: Tuple[str, ...]
    discovered: bool = False
    executed: bool = False


@dataclass(frozen=True)
class DetectionEvidence:
    detected: bool
    references: Tuple[str, ...]
    ambiguity: Optional[str]


@dataclass(frozen=True)
class AdapterObservations:
    entry_points: Tuple[Dict[str, object], ...]
    actions: Tuple[ActionRecord, ...]
    mediation: Tuple[PathRecord, ...]
    probe_cases: Tuple[Dict[str, object], ...]


@dataclass(frozen=True)
class ProbeEvidence:
    """Payload-free provenance for one evidence id a probe actually cites.

    A :class:`ProbeResult` names the evidence its status rests on only by
    id (``evidence_refs``); an orchestrator that has to bind that id to a
    real :class:`EvidenceRef` needs to know *what artifact the probe
    actually observed* and *what its content hashes to* -- neither of
    which can ever be derived from the id string itself. This carries
    exactly that, and nothing else: never the observed record's own
    contents, arguments, or any other raw probe payload.

    ``sha256`` is always the digest of a real artifact the probe observed
    or generated (a canonical argument/binding digest the target itself
    recorded, or the canonical digest of an actually-observed ledger/
    audit record) -- never a hash of *evidence_id*.
    """

    evidence_id: str
    kind: str
    source: str
    sha256: str


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    action_id: Optional[str]
    path_id: Optional[str]
    status: Status
    reason_code: str
    expected: str
    observed: str
    evidence_refs: Tuple[str, ...]
    #: Provenance for the ids in ``evidence_refs``, when the probe that
    #: produced this result observed the underlying artifacts itself.
    #: Empty when a probe reports no evidence at all; an ``evidence_refs``
    #: id with no matching entry here is an unresolvable citation, which
    #: an orchestrator must treat as not-verified rather than bind to
    #: invented provenance.
    evidence_items: Tuple[ProbeEvidence, ...] = ()


@dataclass(frozen=True)
class AssessmentOptions:
    root: Path
    phase: Phase
    emit: bool = False
    gate: bool = False
    live_github: bool = False
    live_azure: bool = False
    staging: bool = False
    repository: Optional[str] = None
    default_branch: Optional[str] = None
    subscription: Optional[str] = None
    staging_resource_group: Optional[str] = None
    deploy_identity: Optional[str] = None
    now: str = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class AssessmentResult:
    source: SourceRef
    actions: Tuple[ActionRecord, ...]
    paths: Tuple[PathRecord, ...]
    probes: Tuple[ProbeResult, ...]
    findings: Tuple[Finding, ...]
    evidence: Tuple[EvidenceRef, ...]
    policy_hashes: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    pins: Mapping[str, object] = field(default_factory=dict)
    conformance_claims: Tuple[Mapping[str, object], ...] = ()
    conformance_reports: Tuple[Mapping[str, object], ...] = ()
    change_plane: Mapping[str, object] = field(default_factory=dict)
    residual_risks: Tuple[Mapping[str, object], ...] = ()
    #: The trusted, deterministic instant this assessment was captured at
    #: (an RFC3339 timestamp string), independent of any evidence's own
    #: ``collected_at``. Freshness is evaluated *at* this instant, never
    #: at the newest evidence timestamp -- an assessment made from a mix
    #: of evidence collected at different times must judge staleness
    #: relative to when the assessment itself ran, not relative to
    #: whichever evidence happens to be newest. Optional and defaulting
    #: to ``None`` for backward compatibility with existing callers built
    #: before this field existed; a missing/unparseable value degrades
    #: freshness conservatively (schema-compatible stale/nulls) rather
    #: than inventing a trusted instant. Intended to be populated from
    #: ``AssessmentOptions.now`` once an orchestrator wires the two
    #: together.
    captured_at: Optional[str] = None
    #: The lifecycle phase this assessment was actually run at, mirroring
    #: ``AssessmentOptions.phase``: an assessment-level claim ("what phase
    #: was *this run* judging"), never a computed aggregate of the phases
    #: any individual finding or evidence record happens to carry. Optional
    #: and defaulting to ``None`` for backward compatibility with existing
    #: callers built before this field existed; a ``None`` value is
    #: rendered as a conservative fallback derived only from the
    #: assessment's own assertions (``Finding.phase``), never from
    #: ``EvidenceRef.phase`` -- evidence describes when it was collected,
    #: not what phase the assessment itself claims to be judging, so an
    #: uncited, probe-only, or explicitly distrusted evidence record must
    #: never be able to escalate or otherwise redefine that claim. Intended
    #: to be populated from ``AssessmentOptions.phase`` once an
    #: orchestrator wires the two together.
    phase: Optional[Phase] = None
    #: Whether ``--live-github`` was explicitly selected for *this* run,
    #: mirroring ``AssessmentOptions.live_github``. Exists only so
    #: ``exit_code`` can distinguish an optional, unselected live
    #: capability (still not-verified, still gate-exempt) from one the
    #: CLI explicitly requested but that came back unresolved or
    #: incomplete (which must fail ``--gate``) -- without any global
    #: mutable state. Defaults to ``False`` for backward compatibility
    #: with existing callers built before this field existed. Intended to
    #: be populated from ``AssessmentOptions.live_github`` once an
    #: orchestrator wires the two together.
    live_github_selected: bool = False
    #: Whether live Azure evidence collection (``--subscription`` +
    #: ``--staging-resource-group`` + ``--deploy-identity``, all three
    #: together) was explicitly selected for *this* run, mirroring
    #: ``AssessmentOptions.live_azure``. Exists for exactly the same
    #: reason as ``live_github_selected`` above, for the two Azure GHCP
    #: reason codes that mean "statically unverifiable"
    #: (``"azure-login-not-verified-statically"``,
    #: ``"identity-separation-not-verified-statically"``): so
    #: ``exit_code`` can distinguish an optional, unselected live Azure
    #: capability (still not-verified, still gate-exempt) from one the
    #: CLI explicitly requested but that came back unresolved or
    #: incomplete -- for example because live Azure collection itself
    #: succeeded but the resulting evidence still could not resolve
    #: genuinely distinct identities -- which must fail ``--gate`` like
    #: any other selected-but-incomplete live evidence. Defaults to
    #: ``False`` for backward compatibility with existing callers built
    #: before this field existed. Intended to be populated from
    #: ``AssessmentOptions.live_azure`` once an orchestrator wires the
    #: two together.
    live_azure_selected: bool = False
