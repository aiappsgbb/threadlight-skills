"""Task 10: governed-actions lifecycle CLI.

Wires the read-only assessor's existing Tasks 1-9 modules (``inventory``,
``inputs``, ``mediation``, ``probes``, ``alerts``, ``ghcp``,
``maf_adapter``, ``canonical``, ``render``) into one runnable command,
``python3 skills/threadlight-governed-actions/scripts/governed_actions.py``,
covering all three lifecycle phases (``design``, ``pre-deploy``,
``post-deploy``).

Trust boundaries this module preserves (never re-derives, never widens):

- The assessor is read-only except for its own, explicit artifact
  emission: nothing here is ever written to the assessed *target* unless
  ``--emit`` is passed, and even then only the three artifacts
  :mod:`render` already defines are ever written, through its own
  existing, transactional :func:`render.write_artifacts`.
- Agent Hooks cooperative/alpha status, "conformance is not
  certification", GHCP's own explicit refusal to claim interception of
  GitHub Copilot's internal reasoning/tool-calling loop, and the lack of
  support for provider-hosted side-effecting tools without an
  equivalent, declared server-side control are every one of them
  properties of the underlying Task 1-9 modules this file calls into
  unchanged -- this module adds no new claim about any of them, and
  narrows none of the existing ones.
- No business policy, threshold, approver, identity, permission, or risk
  appetite is invented anywhere in this file: every finding this module's
  own orchestration produces (as opposed to ones already produced by the
  modules it calls) is a direct, mechanical pass-through of what those
  modules themselves returned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

import alerts
import canonical
import contracts
import ghcp
import inputs
import inventory
import maf_adapter
import mediation
import probes
import render
import scaffold

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Timeout, in seconds, applied to every real subprocess call this module
#: issues (git provenance reads and, when explicitly requested, live
#: gh/az evidence collection). A read-only local/remote command that has
#: not returned within this bound is treated as unusable, never awaited
#: indefinitely.
_COMMAND_TIMEOUT_SECONDS = 30.0

#: The assessor's own canonical complete-tested-tuple pin file, read for
#: pre-deploy pin comparison. Never target-controlled.
_REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"
_UPSTREAM_PIN_PATH = _REFERENCES_DIR / "upstream-pin.json"

#: The one required, common evidence file both enforcement and privacy
#: probe sets read from. Its absence is a normal, common "no probe
#: contract declared" state -- reported as an explicit not-verified
#: finding by this module, never allowed to raise out of
#: ``probes.load_probe_contract`` and be misclassified as an invalid-input
#: abort for what is, for the overwhelming majority of targets that never
#: opted into these probes, an entirely expected condition.
_PROBE_CONTRACT_RELATIVE_PATH = Path("governance") / "probe-contract.json"

#: The reason codes GHCP's own ``assess_change_plane`` assigns to a
#: control that is simply *statically unverifiable* -- i.e. one that was
#: never actually attempted because no corresponding ``--live-github``/
#: live-Azure evidence was requested at all, as opposed to one that was
#: requested and failed. Per the gate semantics this module implements,
#: an optional, unselected live capability may remain not-verified
#: without failing ``--gate``; a selected-but-failed one may not. Each
#: reason code below is therefore mapped to the ``AssessmentResult``
#: attribute recording whether *its* underlying live capability was
#: selected on this run: the code is only ever "optional and unselected"
#: -- and so gate-exempt -- when that attribute is ``False``. When it is
#: ``True``, the same reason code means static-only fallback happened
#: despite an explicit live request (e.g. GHCP's static analysis ran
#: ahead of -- or instead of -- live collection actually completing, or
#: live collection succeeded but could not itself resolve the control),
#: so it must fail the gate like any other selected-but-incomplete live
#: evidence. Every live capability the CLI *did* select surfaces its own
#: collection failure (when collection itself fails) via a different
#: reason code (``github-live-evidence-unavailable`` and friends), which
#: is never in this mapping and is therefore never exempt -- exactly
#: "every live capability explicitly selected by CLI arguments" must
#: resolve.
_CONDITIONAL_LIVE_EXEMPT_REASON_CODES: Mapping[str, str] = {
    "branch-protection-not-verified-statically": "live_github_selected",
    "azure-login-not-verified-statically": "live_azure_selected",
    "identity-separation-not-verified-statically": "live_azure_selected",
}


class ArgumentError(ValueError):
    """Raised by this module's own argument parser instead of exiting.

    :mod:`argparse` itself calls ``sys.exit`` on a parse error; that would
    make ``main`` untestable and would bypass this module's own uniform
    exit-code classification. This module's parser subclass raises this
    (a plain :class:`ValueError` subclass) instead, so ``main`` can catch
    it exactly like every other invalid-input condition and return ``2``.
    """


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - trivial
        raise ArgumentError(message)


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="governed_actions.py",
        description=(
            "Read-only governed-actions lifecycle assessor: design, "
            "pre-deploy, and post-deploy phases."
        ),
    )
    parser.add_argument("--target", default=".", help="path to the assessed project (default: .)")
    parser.add_argument(
        "--phase",
        required=False,
        default=None,
        choices=contracts.SUPPORTED_PHASES,
        help="lifecycle phase to assess; required unless --scaffold/--confirm-scaffold is given",
    )
    parser.add_argument(
        "--scaffold",
        choices=scaffold.SUPPORTED_SCAFFOLD_KINDS,
        default=None,
        help=(
            "write the bounded, double-opt-in governance scaffold "
            "(SCAFFOLD_FILES) into --target instead of assessing it; "
            "requires --confirm-scaffold as well"
        ),
    )
    parser.add_argument(
        "--confirm-scaffold",
        dest="confirm_scaffold",
        action="store_true",
        help="the second, explicit opt-in required together with --scaffold",
    )
    parser.add_argument(
        "--emit",
        action="store_true",
        help="write the three governed-actions artifacts (default: read-only, no writes)",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="return a nonzero exit code when the selected phase's requirements are not met",
    )
    parser.add_argument("--repo", dest="repository", default=None, help="owner/repository override")
    parser.add_argument(
        "--staging-resource-group",
        dest="staging_resource_group",
        default=None,
        help="non-production Azure resource group; required for --phase post-deploy",
    )
    parser.add_argument("--subscription", default=None, help="Azure subscription id/name")
    parser.add_argument("--deploy-identity", dest="deploy_identity", default=None, help="Azure deploy identity name")
    parser.add_argument(
        "--live-github",
        dest="live_github",
        action="store_true",
        help="collect optional, read-only live GitHub evidence via 'gh api'",
    )
    parser.add_argument(
        "--manifest-path",
        dest="manifest_path",
        type=Path,
        default=render.DEFAULT_MANIFEST_RELATIVE_PATH,
        help="manifest artifact path, relative to --target",
    )
    parser.add_argument(
        "--evidence-path",
        dest="evidence_path",
        type=Path,
        default=render.DEFAULT_EVIDENCE_RELATIVE_PATH,
        help="evidence-pack artifact path, relative to --target",
    )
    parser.add_argument(
        "--apply-plan-path",
        dest="apply_plan_path",
        type=Path,
        default=render.DEFAULT_APPLY_PLAN_RELATIVE_PATH,
        help="apply-plan artifact path, relative to --target",
    )
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse *argv* (``sys.argv[1:]`` when ``None``) into a validated
    :class:`argparse.Namespace`.

    Raises :class:`ArgumentError`/:class:`ValueError` (never calls
    ``sys.exit``) for both a plain argparse usage error and this CLI's
    cross-flag prerequisites:

    - ``--phase`` is required unless ``--scaffold`` and/or
      ``--confirm-scaffold`` was given -- the two scaffold flags' own
      double opt-in is enforced later, by :func:`scaffold.scaffold`
      itself, never here.
    - ``--phase post-deploy`` requires ``--staging-resource-group``
      naming an explicit, non-production staging resource group, since
      post-deploy must never be run, even read-only, against an unnamed
      or ambiguous target environment.
    """
    parser = _build_parser()
    namespace = parser.parse_args(list(argv) if argv is not None else None)
    scaffold_requested = namespace.scaffold is not None or namespace.confirm_scaffold
    if not scaffold_requested and namespace.phase is None:
        raise ArgumentError(
            "--phase is required (unless --scaffold/--confirm-scaffold is given)"
        )
    if namespace.phase == "post-deploy" and not namespace.staging_resource_group:
        raise ArgumentError(
            "--phase post-deploy requires --staging-resource-group naming an "
            "explicit, non-production staging resource group"
        )
    return namespace


# ---------------------------------------------------------------------------
# resolve_source
# ---------------------------------------------------------------------------

_OWNER_REPOSITORY_PATTERN = re.compile(r"[:/](?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")
_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _run_git(args: Sequence[str], target: Path) -> str:
    """Run exactly ``git -C <target> <args>`` and return its stripped
    stdout, raising :class:`ValueError` on any non-zero exit or a
    command that cannot even be started (missing ``git`` binary, a
    target that does not exist, ...). A non-git *target* always fails
    here first, at ``rev-parse --show-toplevel`` -- exactly the "non-Git
    target is invalid input" requirement.
    """
    command = ["git", "-C", str(target), *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"could not run {' '.join(command)}: {error}") from error
    if completed.returncode != 0:
        raise ValueError(
            f"{' '.join(command)} failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def resolve_source(root: Path) -> contracts.SourceRef:
    """Resolve *root*'s real git provenance via exactly the equivalent of:

    - ``git -C <root> rev-parse --show-toplevel``
    - ``git -C <root> config --get remote.origin.url``
    - ``git -C <root> rev-parse HEAD``
    - ``git -C <root> status --porcelain --untracked-files=no``

    A non-Git *root* is invalid input: the first command above fails and
    this raises :class:`ValueError`. ``remote.origin.url`` is normalized
    to ``owner/repository`` (SSH or HTTPS form, with or without a
    trailing ``.git``). ``HEAD`` must resolve to exactly a 40-character
    commit; anything else is rejected rather than silently truncated or
    padded. Dirty state (any tracked-file modification the untracked-
    files-excluded porcelain status reports) is always recorded on the
    returned :class:`~contracts.SourceRef` exactly as observed -- never
    suppressed, coerced, or overridden -- so it can only ever be
    represented explicitly downstream, never hidden.
    """
    target = Path(root)
    _run_git(["rev-parse", "--show-toplevel"], target)
    remote_url = _run_git(["config", "--get", "remote.origin.url"], target)
    commit = _run_git(["rev-parse", "HEAD"], target)
    if not _COMMIT_SHA_PATTERN.match(commit):
        raise ValueError(
            f"git rev-parse HEAD did not return a 40-character commit sha: {commit!r}"
        )
    status_output = _run_git(["status", "--porcelain", "--untracked-files=no"], target)

    match = _OWNER_REPOSITORY_PATTERN.search(remote_url)
    if match is None:
        raise ValueError(
            f"cannot normalize remote.origin.url to 'owner/repository': {remote_url!r}"
        )
    repository = f"{match.group('owner')}/{match.group('repo')}"
    return contracts.SourceRef(
        repository=repository,
        commit=commit,
        dirty=bool(status_output.strip()),
    )


def _resolve_default_branch(root: Path) -> Optional[str]:
    """Best-effort local read of the remote's default branch, used only
    to shape a ``--live-github`` request. Never raises and never
    fabricates a guess: when the local ref is unavailable (e.g. this
    checkout never ran ``git remote set-head origin --auto``) or resolves
    to an empty string, this returns ``None`` rather than an arbitrary
    fallback branch name (``"main"`` or otherwise) -- an assumed branch
    that happens not to be the real default would silently mis-scope a
    live branch-protection check into checking the wrong branch. The
    caller is responsible for reporting this ambiguity as an explicit
    not-verified finding rather than converting it into a pass.
    """
    try:
        ref = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
    except ValueError:
        return None
    if not ref:
        return None
    return ref.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Live evidence command runner (injectable for tests, matching ghcp.py's
# own CommandRunner convention)
# ---------------------------------------------------------------------------


def _default_command_runner(command: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """The real, read-only ``gh``/``az`` command runner used outside of
    tests. Every command this module ever passes here comes from
    :mod:`ghcp`'s own fixed, read-only ``collect_live_github``/
    ``collect_live_azure`` command lists -- never a mutation, never
    assembled from anything other than CLI-supplied identifiers.
    """
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spec_section_8_evidence(
    inv: inventory.InventoryResult, source: contracts.SourceRef, now: str
) -> Tuple[contracts.EvidenceRef, ...]:
    return (
        contracts.EvidenceRef(
            evidence_id="EVID-spec-section-8",
            kind="static-file-hash",
            source="specs/SPEC.md#section-8",
            sha256=inv.spec_section_sha256,
            collected_at=now,
            freshness_seconds=0,
            live_verified=False,
            phase="design",
            repository=source.repository,
            source_commit=source.commit,
            target_environment=None,
            policy_set_sha256=None,
        ),
    )


def _design_runtime_not_verified_finding() -> contracts.Finding:
    """design never executes any runtime application probe (enforcement,
    privacy, approval, or output mediation) -- only pre-deploy/post-deploy
    do. Reported explicitly so a SAFE-complete, design-only assessment
    can never be mistaken for one where runtime governance is already
    proven; reuses ENF-001 (the catalog's own "no enforcement seam
    confirmed" finding) with a design-specific reason code distinguishing
    this from a real, attempted-but-failing enforcement check.
    """
    return contracts.Finding(
        finding_id="ENF-001",
        status="not-verified",
        phase="design",
        plane="runtime",
        reason_code="runtime-checks-not-verified-in-design",
        summary="Runtime application probes are not run during design.",
        details=(
            "design only builds the action inventory and validates "
            "SAFE-completeness of declared actions; no enforcement, "
            "privacy, approval, or output-mediation probe is ever "
            "executed at this phase, so runtime governance coverage "
            "remains not-verified until at least pre-deploy runs."
        ),
    )


def _design_ghcp_not_verified_finding() -> contracts.Finding:
    """design never runs GHCP's static workflow analysis or collects any
    ``--live-github``/``--live-azure`` evidence -- only pre-deploy does.
    Reported explicitly so a SAFE-complete, design-only assessment can
    never be mistaken for one where the GitHub Copilot change plane is
    already proven; reuses GHCP-002 (the catalog's own "branch
    protection/CODEOWNERS coverage missing or unavailable" finding) with
    a reason code distinct from pre-deploy's own
    ``branch-protection-not-verified-statically`` -- design never even
    attempts a GHCP check, so reusing that exact reason code would
    misrepresent what actually ran.
    """
    return contracts.Finding(
        finding_id="GHCP-002",
        status="not-verified",
        phase="design",
        plane="change",
        reason_code="ghcp-not-assessed-in-design",
        summary="GitHub Copilot change-plane checks are not run during design.",
        details=(
            "design never runs GHCP's static workflow analysis or any "
            "--live-github/--live-azure evidence collection; branch "
            "protection, required checks, CODEOWNERS coverage, and every "
            "other change-plane control remain not-verified until at "
            "least pre-deploy runs."
        ),
    )


def _assess_design(
    root: Path, source: contracts.SourceRef, options: contracts.AssessmentOptions
) -> contracts.AssessmentResult:
    """design: inventory and SAFE-requirement validation only.

    Never executes a runtime application probe or a GHCP static/live
    change-plane check -- but never lets that absence of coverage go
    unreported either: two explicit not-verified findings mark both as
    preliminary, unproven evidence rather than silence, so a
    SAFE-complete design-only assessment can never claim full governed
    status. Neither finding is ever ``must-fix``, so this never widens
    design's own gate (see :func:`exit_code`: only an ACT-001/ACT-002
    must-fix finding can fail it).
    """
    inv = inventory.build_action_inventory(root)
    findings = inv.findings + (
        _design_runtime_not_verified_finding(),
        _design_ghcp_not_verified_finding(),
    )
    return contracts.AssessmentResult(
        source=source,
        actions=inv.actions,
        paths=(),
        probes=(),
        findings=findings,
        evidence=_spec_section_8_evidence(inv, source, options.now),
        policy_hashes=(),
        pins={},
        conformance_claims=(),
        conformance_reports=(),
        change_plane={},
        residual_risks=(),
        captured_at=options.now,
        phase="design",
        live_azure_selected=options.live_azure,
    )


def _missing_probe_contract_finding(phase: str) -> contracts.Finding:
    """A single, explicit not-verified finding standing in for the whole
    enforcement/privacy probe suite when the target never declared
    ``governance/probe-contract.json`` -- reported rather than allowing
    ``probes.load_probe_contract`` to raise for what is, for the great
    majority of targets, an entirely expected, non-error absence.
    """
    return contracts.Finding(
        finding_id="ENF-001",
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="probe-contract-unavailable",
        summary="Application probe suite could not be run: no probe contract declared.",
        details=(
            "governance/probe-contract.json is absent, so the enforcement and "
            "privacy application probe sets were never run; this is reported "
            "not-verified rather than inferred as a pass."
        ),
    )


def _run_probe_sets(
    root: Path,
    phase: str,
    discovered_paths: Sequence[contracts.PathRecord],
) -> Tuple[Tuple[contracts.ProbeResult, ...], Tuple[contracts.Finding, ...]]:
    """Run the enforcement and privacy application probe suites.

    Returns the raw probe results plus only the findings that stand in
    for a probe suite that was never run at all; every finding *derived
    from* a probe result is produced later, by
    :func:`_bind_probe_evidence`, so that a probe whose cited evidence
    cannot be resolved is downgraded before -- never after -- its
    finding is derived.

    A declared but malformed/tampered ``governance/probe-contract.json``
    deliberately propagates ``probes.ProbeContractError`` (a
    ``ValueError``) rather than being reported as a benign absent
    contract: a contract that exists but cannot be trusted is an error
    the operator must see, never a quiet not-verified.
    """
    if not (root / _PROBE_CONTRACT_RELATIVE_PATH).is_file():
        return (), (_missing_probe_contract_finding(phase),)
    findings: List[contracts.Finding] = []
    try:
        enforcement_results = probes.run_enforcement_probe_set(root)
    except probes.ProbeToolingError as error:
        enforcement_results = tuple(getattr(error, "partial_results", ()))
        findings.append(
            _probe_tooling_unavailable_finding(
                finding_id="ENF-001",
                phase=phase,
                reason_code="enforcement-probe-unavailable",
                summary="Enforcement probes could not be completed.",
                details=(
                    "The enforcement probe runner could not produce an observable "
                    "outcome for at least one declared action, so ENF-001 is "
                    "reported not-verified rather than aborting the assessment."
                ),
            )
        )
    try:
        path_results = probes.run_execution_path_probe_set(root, discovered_paths)
    except probes.ProbeToolingError as error:
        path_results = tuple(getattr(error, "partial_results", ()))
        findings.append(
            _probe_tooling_unavailable_finding(
                finding_id="MED-002",
                phase=phase,
                reason_code="execution-path-probe-unavailable",
                summary="Execution-path probes could not be completed.",
                details=(
                    "At least one contract-bound path dispatch did not produce "
                    "an observable assessor-owned proof-channel receipt, so mediation "
                    "coverage remains not-verified."
                ),
            )
        )
    try:
        privacy_results = probes.run_privacy_probe_set(root)
    except probes.ProbeToolingError as error:
        privacy_results = tuple(getattr(error, "partial_results", ()))
        findings.append(
            _probe_tooling_unavailable_finding(
                finding_id="AUD-001",
                phase=phase,
                reason_code="payload-free-audit-probe-unavailable",
                summary="Payload-free audit probe could not be completed.",
                details=(
                    "The payload-free audit probe could not produce observable "
                    "audit evidence for this assessment, so AUD-001 is reported "
                    "not-verified rather than aborting the assessment."
                ),
            )
        )
    return enforcement_results + path_results + privacy_results, tuple(findings)


#: Which finding id an unresolvable-evidence downgrade must be reported
#: under, per probe kind. Purely a mapping onto the existing finding
#: catalog -- never a new control or business policy.
_PROBE_FINDING_IDS: Mapping[str, str] = {
    probes.APPROVAL_PROBE_ID: "APR-001",
    probes.OUTPUT_PROBE_ID: "OUT-001",
    probes.AUDIT_PROBE_ID: "AUD-001",
}
_DEFAULT_PROBE_FINDING_ID = "ENF-001"


def _probe_tooling_unavailable_finding(
    *,
    finding_id: str,
    phase: str,
    reason_code: str,
    summary: str,
    details: str,
) -> contracts.Finding:
    return contracts.Finding(
        finding_id=finding_id,
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code=reason_code,
        summary=summary,
        details=details,
    )


def _probe_action_unattributed_finding(
    probe: contracts.ProbeResult, phase: str, affected_actions: Tuple[str, ...]
) -> contracts.Finding:
    finding_id = _PROBE_FINDING_IDS.get(probe.probe_id, _DEFAULT_PROBE_FINDING_ID)
    return contracts.Finding(
        finding_id=finding_id,
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="probe-action-unattributed",
        summary="Probe result could not be attributed to a single declared action.",
        details=(
            "The probe contract left this probe without a single action "
            "attribution after trying, in order, top-level action_id, "
            "approval_binding.action_id, and a sole actions[0] declaration. "
            "Because multiple declared actions remained possible, the result is "
            "reported not-verified instead of being borrowed by a different action."
        ),
        affected_actions=affected_actions,
    )


def _probe_evidence_unresolved_finding(
    probe: contracts.ProbeResult,
    phase: str,
    path_evidence_by_id: Mapping[str, Tuple[str, ...]],
) -> contracts.Finding:
    """An explicit not-verified finding for a probe that cited evidence
    the probe pipeline could not actually resolve to an observed
    artifact.

    Never binds the citation to invented provenance and never lets the
    probe's own status stand on evidence that does not exist: the
    result is reported not-verified so it fails ``--gate`` exactly like
    any other unproven control.
    """
    is_path_receipt = probe.probe_id in probes.PATH_PROBE_IDS
    finding_id = (
        "MED-002"
        if is_path_receipt
        else _PROBE_FINDING_IDS.get(probe.probe_id, _DEFAULT_PROBE_FINDING_ID)
    )
    path_refs = (
        path_evidence_by_id.get(probe.path_id, ())
        if is_path_receipt and probe.path_id is not None
        else ()
    )
    return contracts.Finding(
        finding_id=finding_id,
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="probe-evidence-unresolved",
        summary="Probe outcome could not be trusted: cited evidence was never resolved to an observed artifact.",
        details=(
            f"Probe {probe.probe_id!r} cited evidence this assessment "
            "could not bind to any artifact the probe pipeline actually "
            "observed, so its outcome is reported not-verified rather "
            "than bound to invented provenance or accepted on an "
            "unresolvable citation."
        ),
        affected_actions=(probe.action_id,) if probe.action_id else (),
        affected_paths=(probe.path_id,) if is_path_receipt and probe.path_id else (),
        evidence_refs=tuple(sorted(path_refs)),
    )


def _disambiguated_evidence_id(evidence_id: str, item: contracts.ProbeEvidence) -> str:
    """A deterministic, payload-free id for one distinct observed
    provenance among several that collide under *evidence_id*.

    Separate probe child processes commonly emit the exact same
    ``evidence_id`` text (a target's own audit-sequence counter is
    typically a fresh module global in every subprocess, so each
    probe's first durable audit record is always literally
    ``"audit-0001"``) while each backing it with a genuinely different
    real ledger record. Built only from the original id and the
    sha256 digest the probe itself actually observed for *that*
    artifact -- never from any raw record/payload value, and never
    Python's randomized ``hash()`` -- so the result is identical
    across processes and every ``PYTHONHASHSEED``. The same (kind,
    source, sha256) recurring under *evidence_id* always yields this
    same rewritten id, so a citation that is genuinely re-observed
    unchanged is never needlessly rewritten twice differently.
    """
    digest_hex = item.sha256.split(":", 1)[-1] if ":" in item.sha256 else item.sha256
    return f"{evidence_id}~{digest_hex[:16]}"


def _bind_probe_evidence(
    probe_results: Sequence[contracts.ProbeResult],
    source: contracts.SourceRef,
    options: contracts.AssessmentOptions,
    policy_hashes: Sequence[Mapping[str, str]],
    *,
    path_evidence_by_id: Optional[Mapping[str, Tuple[str, ...]]] = None,
) -> Tuple[Tuple[contracts.ProbeResult, ...], Tuple[contracts.Finding, ...], Tuple[contracts.EvidenceRef, ...]]:
    """Bind every probe-cited evidence id to a real :class:`contracts.EvidenceRef`.

    Each entry is built *only* from provenance the probe pipeline
    itself observed (``ProbeResult.evidence_items``: the artifact kind,
    the target-declared source it came from, and the SHA-256 of the
    artifact's actual canonical bytes) and bound to this assessment's
    own repository, source commit, phase, capture instant, and policy
    set. Nothing is ever synthesized from the evidence id itself, and
    no probe payload is ever carried into an evidence entry.

    A probe citing an id with no such observed provenance is downgraded
    to not-verified (with an explicit finding) instead of being bound
    to invented evidence, so a missing underlying artifact can never
    become a pass -- or a ``governed`` verdict.

    A single evidence id is never allowed to represent conflicting
    provenance either. Separate probes -- most often separate probe
    child processes -- routinely cite the exact same id while each
    having actually observed a different real artifact (see
    :func:`_disambiguated_evidence_id`); an id whose citing probes
    disagree on (kind, source, sha256) is *never* collapsed onto
    whichever probe happened to bind first. Each distinct observed
    tuple is instead rewritten, deterministically, to its own unique
    canonical id -- honestly preserving every real artifact rather than
    publishing false shared provenance -- and every citing probe's
    ``evidence_refs``/``evidence_items`` (and, through
    :func:`probes.findings_from_probes`, every finding derived from it)
    is updated to cite that rewritten id, so no dangling original id
    survives once it was ever ambiguous. The same collision check is
    unconditional -- applied to *every* candidate final id, whether it
    is such a rewrite or an ordinary, never-rewritten literal id --
    because a probe's literal, target-controlled id can just as easily
    collide with a *different* id's already-bound, differently
    provenanced evidence, in either processing order: a rewrite bound
    first and a later literal citation matching it verbatim is exactly
    as unsafe to reuse as two rewrites colliding with each other. Only
    when the final id would collide with such already-bound, differently
    provenanced evidence -- meaning safe disambiguation or reuse is not
    possible -- is that one probe's citation instead downgraded to
    not-verified through the same ``probe-evidence-unresolved``
    semantics, never reused as if it were safe. An id cited by only one
    distinct (kind, source, sha256) tuple, however many probes share it
    or in whatever order they are processed, is left exactly as it was:
    real repeated identical provenance is still deduplicated to a single
    evidence entry.
    """
    policy_set_sha256 = render.canonical_policy_set_sha256(policy_hashes)
    findings: List[contracts.Finding] = []
    path_evidence_by_id = path_evidence_by_id or {}

    # First pass: filter out probes citing an id their own
    # evidence_items never resolved at all -- unchanged from before,
    # and kept separate from provenance-conflict detection below, which
    # only ever looks at probes that did resolve every citation.
    resolved: Dict[int, Tuple[contracts.ProbeResult, Dict[str, contracts.ProbeEvidence]]] = {}
    invalid_indices: List[int] = []
    for index, probe in enumerate(probe_results):
        items = {item.evidence_id: item for item in probe.evidence_items}
        if any(ref not in items for ref in probe.evidence_refs):
            invalid_indices.append(index)
            continue
        resolved[index] = (probe, items)

    # Which ids carry more than one distinct (kind, source, sha256)
    # tuple among the probes that did resolve -- i.e. ids whose
    # citing probes actually disagree on provenance, never an id
    # legitimately shared by probes that observed the same artifact.
    provenance_by_id: Dict[str, set] = {}
    for probe, items in resolved.values():
        for ref in probe.evidence_refs:
            item = items[ref]
            provenance_by_id.setdefault(ref, set()).add((item.kind, item.source, item.sha256))
    conflicting_ids = {ref for ref, provenances in provenance_by_id.items() if len(provenances) > 1}

    kept: List[contracts.ProbeResult] = []
    evidence_by_id: Dict[str, contracts.EvidenceRef] = {}
    for index, probe in enumerate(probe_results):
        if index in invalid_indices:
            kept.append(
                replace(
                    probe,
                    status="not-verified",
                    reason_code="probe-evidence-unresolved",
                    evidence_refs=(),
                    evidence_items=(),
                )
            )
            findings.append(
                _probe_evidence_unresolved_finding(
                    probe, options.phase, path_evidence_by_id
                )
            )
            continue

        _, items = resolved[index]
        rewritten_refs: List[str] = []
        rewritten_items: List[contracts.ProbeEvidence] = []
        unresolvable = False
        for ref in probe.evidence_refs:
            item = items[ref]
            final_id = ref
            if ref in conflicting_ids:
                final_id = _disambiguated_evidence_id(ref, item)
            # Unconditional: whether *final_id* is the literal cited id
            # or its deterministic disambiguation, it must never be
            # silently accepted just because some earlier probe already
            # bound that same string -- a probe whose own literal,
            # never-rewritten id happens to collide with another id's
            # rewrite (or vice versa, in either processing order) is
            # exactly as unsafe to reuse as two conflicting rewrites
            # colliding with each other.
            existing = evidence_by_id.get(final_id)
            if existing is not None and (
                existing.kind,
                existing.source,
                existing.sha256,
            ) != (item.kind, item.source, item.sha256):
                # *final_id* would collide with a different id's
                # already-bound, differently provenanced evidence --
                # never safe to reuse, so this probe's citation is
                # downgraded instead.
                unresolvable = True
                break
            rewritten_refs.append(final_id)
            rewritten_items.append(replace(item, evidence_id=final_id))

        if unresolvable:
            kept.append(
                replace(
                    probe,
                    status="not-verified",
                    reason_code="probe-evidence-unresolved",
                    evidence_refs=(),
                    evidence_items=(),
                )
            )
            findings.append(
                _probe_evidence_unresolved_finding(
                    probe, options.phase, path_evidence_by_id
                )
            )
            continue

        probe = replace(
            probe, evidence_refs=tuple(rewritten_refs), evidence_items=tuple(rewritten_items)
        )
        kept.append(probe)
        for final_id, item in zip(rewritten_refs, rewritten_items):
            if final_id in evidence_by_id:
                continue
            evidence_by_id[final_id] = contracts.EvidenceRef(
                evidence_id=item.evidence_id,
                kind=item.kind,
                source=item.source,
                sha256=item.sha256,
                collected_at=options.now,
                freshness_seconds=0,
                live_verified=False,
                phase=options.phase,
                repository=source.repository,
                source_commit=source.commit,
                target_environment=None,
                policy_set_sha256=policy_set_sha256,
            )
    findings.extend(probes.findings_from_probes(tuple(kept), phase=options.phase))
    evidence = tuple(evidence_by_id[key] for key in sorted(evidence_by_id))
    return tuple(kept), tuple(findings), evidence


#: The exact file suffixes a static analyzer in this assessor cites as a
#: repository-relative source path: the action-registry formats
#: :data:`inventory._REGISTRY_FILENAMES` recognizes, the Python modules
#: ``mediation``/``maf_adapter`` scan, and the SPEC Markdown. Anything
#: else a finding cites is an opaque protected-system identifier (a probe
#: digest, an alert/GHCP collector id, a spec-section anchor) whose
#: provenance belongs to the collector that produced it -- never to this
#: path, which would otherwise have to guess at a file to hash.
_STATIC_SOURCE_EVIDENCE_SUFFIXES: FrozenSet[str] = frozenset(
    {".json", ".md", ".py", ".yaml", ".yml"}
)


def _is_static_source_evidence_ref(reference: str) -> bool:
    """True when *reference* is shaped like a repository-relative source
    path this module may safely attempt to read.

    Purely a shape check, deliberately strict and allowlist-based:
    absolute paths, Windows-style separators, NUL bytes, empty or
    ``.``/``..`` components (so no traversal spelling survives), and any
    suffix outside :data:`_STATIC_SOURCE_EVIDENCE_SUFFIXES` are all
    rejected here, before anything touches the filesystem. Passing this
    check never implies the file exists, is readable, or stays inside
    the assessment root -- :func:`_static_source_sha256` proves each of
    those separately against the real filesystem.
    """
    if not reference or reference != reference.strip():
        return False
    if "\\" in reference or "\x00" in reference:
        return False
    candidate = PurePosixPath(reference)
    if candidate.is_absolute():
        return False
    parts = candidate.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return candidate.suffix.lower() in _STATIC_SOURCE_EVIDENCE_SUFFIXES


def _static_source_sha256(root: Path, reference: str) -> Optional[str]:
    """The ``sha256:`` digest of the real bytes at *reference* under
    *root*, or ``None`` when that cannot be proven safely.

    Delegates every path-safety and read decision to
    :func:`canonical.hash_files`, the established validator: it resolves
    the candidate, rejects anything whose real target escapes *root*
    (including a symlink pointing outside it), and raises
    :class:`canonical.CanonicalizationError` rather than leaking a raw
    ``OSError`` when the file is absent, a directory, or unreadable.
    Every one of those cases returns ``None`` here, leaving the citation
    unresolved so the verdict cannot pass on it.

    A citation that resolves to a *different* in-root path than the one
    it names (an in-root symlink alias) is also left unresolved: this
    module will only ever bind an evidence entry whose id and ``source``
    name the exact file whose bytes it hashed, never an alias for
    another one.
    """
    try:
        hashed = canonical.hash_files(Path(root), (Path(reference),))
    except (canonical.CanonicalizationError, OSError, ValueError):
        return None
    entries = hashed["files"]
    if len(entries) != 1:
        return None
    entry = entries[0]
    if entry["path"] != reference:
        return None
    return str(entry["sha256"])


def _bind_static_source_evidence(
    root: Path,
    findings: Sequence[contracts.Finding],
    source: contracts.SourceRef,
    options: contracts.AssessmentOptions,
    policy_hashes: Sequence[Mapping[str, str]],
    already_collected: FrozenSet[str] = frozenset(),
    paths: Sequence[contracts.PathRecord] = (),
) -> Tuple[contracts.EvidenceRef, ...]:
    """Bind every repository-relative source path a *static* finding
    cites to a real :class:`contracts.EvidenceRef`.

    ``inventory``/``mediation``/``maf_adapter`` cite the exact
    repository-relative paths they statically read (``agent.yaml``,
    ``app/agent.py``, ...) as their own ``Finding.evidence_refs``. This
    reads each such file for real and hashes its actual bytes, then
    binds the entry to this assessment's own repository, source commit,
    capture instant, and canonical policy set -- exactly the provenance
    :func:`_bind_probe_evidence` binds for a probe-observed artifact,
    and exactly what :mod:`render`'s own trust checks compare against.

    ``phase`` is bound to the phase of the findings that actually cite
    the path, not to *options.phase*: like :func:`_spec_section_8_evidence`,
    this is static-analysis evidence, and the analysis that read the file
    is the design-plane one whose findings cite it. Binding the running
    phase instead would leave every design-phase citation failing
    :mod:`render`'s own evidence/finding phase agreement check.

    Deliberately narrow, and never permissive:

    - only citations shaped like a repository-relative source path are
      even considered (:func:`_is_static_source_evidence_ref`), so an
      opaque protected-system identifier -- a probe's ``sha256:`` digest,
      an alert/GHCP collector id, the spec-section anchor -- is never
      converted through this path;
    - the digest is always computed over the file's real bytes on disk
      (:func:`_static_source_sha256`), never synthesized from the
      evidence id, the path string, or anything else that would let a
      citation resolve without the artifact existing;
    - a citation whose file is absent, unreadable, escaping, or an alias
      for a different path is left *unresolved* rather than bound to
      invented provenance, so it keeps failing ``render``'s
      required-evidence trust check and the verdict cannot pass;
    - an id already collected by another, authoritative collector
      (*already_collected*) is never re-bound or overwritten here.

    Only the payload-free ``{path, sha256}`` binding is ever carried: no
    file content, argument, prompt, or output ever reaches an evidence
    entry.
    """
    policy_set_sha256 = render.canonical_policy_set_sha256(policy_hashes)
    phases_by_reference: Dict[str, List[str]] = {}
    for finding in findings:
        for reference in finding.evidence_refs:
            if reference in already_collected:
                continue
            if not _is_static_source_evidence_ref(reference):
                continue
            phases_by_reference.setdefault(reference, []).append(finding.phase)
    for path in paths:
        for reference in path.evidence_refs:
            if reference in already_collected:
                continue
            if not _is_static_source_evidence_ref(reference):
                continue
            phases_by_reference.setdefault(reference, []).append(options.phase)
    collected: List[contracts.EvidenceRef] = []
    for reference in sorted(phases_by_reference):
        digest = _static_source_sha256(root, reference)
        if digest is None:
            continue
        collected.append(
            contracts.EvidenceRef(
                evidence_id=reference,
                kind="static-file-hash",
                source=reference,
                sha256=digest,
                collected_at=options.now,
                freshness_seconds=0,
                live_verified=False,
                phase=sorted(set(phases_by_reference[reference]))[0],
                repository=source.repository,
                source_commit=source.commit,
                target_environment=None,
                policy_set_sha256=policy_set_sha256,
            )
        )
    return tuple(collected)


def _approval_not_verified_finding(phase: str) -> contracts.Finding:
    """APR-001 is reported explicit not-verified whenever the target
    declares no usable, deterministic approval binding of its own --
    never silently skipped, and never "wired" from invented data.

    ``probes.run_approval_probe`` requires a complete, customer/
    business-specific :class:`probes.ApprovalBinding` (subject, role,
    tenant, policy, arguments, validity window, nonce). This
    orchestrator never invents one: it runs the probe only against a
    binding the *target itself* declared in its own probe contract (see
    :func:`_declared_approval_binding`), and otherwise reports
    not-verified, which is always truthful.
    """
    return contracts.Finding(
        finding_id="APR-001",
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="approval-binding-unavailable",
        summary="Approval anti-replay probe could not be run: no deterministic approval binding is available.",
        details=(
            "run_approval_probe requires a complete, customer/business-"
            "specific ApprovalBinding (subject, role, tenant, policy, "
            "arguments, validity window, nonce); the target declared no "
            "usable approval binding of its own in "
            "governance/probe-contract.json, so the APR-001 approval-"
            "binding/anti-replay probe was never run. This is reported "
            "not-verified rather than inferred as a pass or invented "
            "from placeholder approver/policy data."
        ),
    )


#: The exact, complete field set a target must declare under the probe
#: contract's ``approval_binding`` object for APR-001 to be runnable.
#: Every field is required and none is ever defaulted: an approver,
#: tenant, policy, nonce, or validity window this orchestrator invented
#: would make an APR-001 "pass" meaningless.
_APPROVAL_BINDING_FIELDS: Tuple[str, ...] = (
    "target_scope",
    "requesting_subject",
    "approving_subject",
    "approving_role",
    "tenant",
    "policy_id",
    "policy_hash",
    "action_id",
    "arguments",
    "issued_at",
    "expires_at",
    "nonce",
)


def _declared_approval_binding(root: Path) -> Optional[probes.ApprovalBinding]:
    """The exact approval binding the *target itself* declared, or ``None``.

    Read from ``governance/probe-contract.json``'s ``approval_binding``
    object, which must declare exactly :data:`_APPROVAL_BINDING_FIELDS`
    -- no missing field is defaulted and no extra field is accepted --
    with non-empty string values and flat, scalar ``arguments``.

    Redeeming a nonce necessarily appends to the target's own declared,
    persistent nonce ledger, so this additionally requires the contract
    to declare ``side_effect_mode: "synthetic"``: an explicit, target-
    side opt-in that the declared binding is synthetic probe data, never
    a real approval. Anything else -- absent object, wrong shape, live
    side-effect mode, unparseable contract -- yields ``None``, and
    APR-001 stays explicitly not-verified.
    """
    contract_path = root / _PROBE_CONTRACT_RELATIVE_PATH
    if not contract_path.is_file():
        return None
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping) or raw.get("side_effect_mode") != "synthetic":
        return None
    declared = raw.get("approval_binding")
    if not isinstance(declared, Mapping) or set(declared) != set(_APPROVAL_BINDING_FIELDS):
        return None
    arguments = declared["arguments"]
    if not isinstance(arguments, Mapping) or not all(
        isinstance(key, str) and isinstance(value, (str, int, float)) and not isinstance(value, bool)
        for key, value in arguments.items()
    ):
        return None
    values: Dict[str, object] = {"arguments": {str(key): value for key, value in arguments.items()}}
    for field in _APPROVAL_BINDING_FIELDS:
        if field == "arguments":
            continue
        value = declared[field]
        if not isinstance(value, str) or not value:
            return None
        values[field] = value
    return probes.ApprovalBinding(**values)  # type: ignore[arg-type]


def _load_probe_contract_context(
    root: Path,
) -> Tuple[Optional[Mapping[str, object]], Optional[Mapping[str, object]]]:
    if not (root / _PROBE_CONTRACT_RELATIVE_PATH).is_file():
        return None, None
    raw_contract = probes.load_raw_probe_contract(root)
    probe_contract = probes.load_probe_contract(root)
    return raw_contract, probe_contract


def _run_approval_coverage(
    root: Path, phase: str, now: str
) -> Tuple[Tuple[contracts.ProbeResult, ...], Tuple[contracts.Finding, ...]]:
    """Explicit APR-001 approval-binding/anti-replay coverage.

    Runs the real ``probes.run_approval_probe_sequence`` against the
    exact binding the target declared for itself, at this assessment's
    own ``now``. The whole point is that a single redemption attempt
    can only ever prove a *first* use was accepted: the sequence
    additionally replays that binding byte-for-byte and reuses its
    already-consumed nonce once per design-required mutated dimension,
    so a target whose anti-replay control genuinely holds earns an
    honest APR-001 pass on its first assessment, and one that fails
    open on a replay or a mutated binding earns an honest must-fix on
    that same first assessment -- never only on a second run, and never
    depending on state left behind in the target. When no usable
    declared binding (or contract) exists, reports APR-001 not-verified
    explicitly rather than inventing one.
    """
    binding = _declared_approval_binding(root)
    if binding is None:
        return (), (_approval_not_verified_finding(phase),)
    try:
        results = probes.run_approval_probe_sequence(root, binding, now=now)
    except probes.ProbeContractError:
        return (), (_approval_not_verified_finding(phase),)
    except probes.ProbeToolingError as error:
        return tuple(getattr(error, "partial_results", ())), (
            _probe_tooling_unavailable_finding(
                finding_id="APR-001",
                phase=phase,
                reason_code="approval-probe-unavailable",
                summary="Approval anti-replay probe could not be completed.",
                details=(
                    "The approval anti-replay probe could not produce an observable "
                    "result for the declared binding, so APR-001 is reported "
                    "not-verified rather than aborting the assessment."
                ),
            ),
        )
    return results, ()


def _output_contract_unavailable_finding(phase: str) -> contracts.Finding:
    """A single, explicit not-verified finding standing in for the
    OUT-001 output-mediation probe when the target never declared a
    usable output probe contract -- reported rather than allowing
    ``probes.load_output_contract``/``run_output_probe`` to raise for
    what is, for the great majority of targets, an entirely expected,
    non-error absence.
    """
    return contracts.Finding(
        finding_id="OUT-001",
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="output-contract-unavailable",
        summary="Output mediation probe could not be run: no output probe contract declared.",
        details=(
            "governance/probe-contract.json does not declare a usable "
            "output-mediation contract (dispatch/audit_sink/"
            "observation_ledger), so the OUT-001 output-mediation probe "
            "was never run; this is reported not-verified rather than "
            "inferred as a pass."
        ),
    )


def _run_output_coverage(
    root: Path, phase: str
) -> Tuple[Tuple[contracts.ProbeResult, ...], Tuple[contracts.Finding, ...]]:
    """Explicit OUT-001 output-mediation coverage.

    Runs the deterministic, side-effect-free ``"deny"`` verdict probe --
    the same fixed synthetic verdict ``run_privacy_probe_set`` already
    uses for AUD-001 -- whenever the target declares a usable output
    probe contract; never fabricates approver/output/threshold data or a
    different verdict. When no such contract is declared, reports
    OUT-001 not-verified explicitly instead of silently never assessing
    it.
    """
    try:
        result = probes.run_output_probe(root, "deny")
    except probes.ProbeContractError:
        return (), (_output_contract_unavailable_finding(phase),)
    except probes.ProbeToolingError:
        return (), (
            _probe_tooling_unavailable_finding(
                finding_id="OUT-001",
                phase=phase,
                reason_code="output-probe-unavailable",
                summary="Output mediation probe could not be completed.",
                details=(
                    "The output mediation probe could not produce an observable "
                    "result for this assessment, so OUT-001 is reported "
                    "not-verified rather than aborting the assessment."
                ),
            ),
        )
    return (result,), ()


def _bound_action_evidence_refs(root: Path, action: contracts.ActionRecord) -> Tuple[str, ...]:
    refs = list(action.declaration_refs)
    if (root / _PROBE_CONTRACT_RELATIVE_PATH).is_file():
        refs.append(str(_PROBE_CONTRACT_RELATIVE_PATH))
    return tuple(dict.fromkeys(refs))


def _action_is_bound(action: contracts.ActionRecord) -> bool:
    return action.policy_binding is not None or bool(action.policy_ids)


def _probe_action_resolution(
    actions: Sequence[contracts.ActionRecord],
) -> Tuple[Mapping[str, contracts.ActionRecord], Mapping[str, str]]:
    registry = {action.action_id: action for action in actions}
    return registry, inventory.build_alias_index(registry)


def _canonical_probe_action_id(
    raw_action_id: str,
    registry: Mapping[str, contracts.ActionRecord],
    alias_index: Mapping[str, str],
) -> str:
    return inventory.canonicalize_action_id(raw_action_id, registry, alias_index)


def _bound_policy_binding_text(action: contracts.ActionRecord) -> str:
    bindings = action.policy_ids or ((action.policy_binding,) if action.policy_binding else ())
    if not bindings:
        return "declared runtime binding"
    digest = hashlib.sha256("\0".join(sorted(bindings)).encode("utf-8")).hexdigest()[:12]
    label = "binding" if len(bindings) == 1 else "bindings"
    return f"{len(bindings)} declared runtime policy {label} (set-sha256:{digest})"


def _bound_action_missing_enforcement_proof_finding(
    root: Path, action: contracts.ActionRecord, phase: str
) -> contracts.Finding:
    binding_text = _bound_policy_binding_text(action)
    return contracts.Finding(
        finding_id="ENF-001",
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="bound-action-missing-enforcement-proof",
        summary=f"Bound action '{action.action_id}' has no verified enforcement probe proof.",
        details=(
            f"Action '{action.action_id}' declares runtime policy binding(s) "
            f"{binding_text}, and governance/probe-contract.json names it for "
            "enforcement probing, but every enforcement probe result for that "
            "action remained not-verified. Unproven enforcement stays an "
            "explicit ENF-001 finding rather than disappearing."
        ),
        affected_actions=(action.action_id,),
        evidence_refs=_bound_action_evidence_refs(root, action),
    )


def _canonicalize_probe_results(
    probe_results: Sequence[contracts.ProbeResult],
    actions: Sequence[contracts.ActionRecord],
) -> Tuple[contracts.ProbeResult, ...]:
    registry, alias_index = _probe_action_resolution(actions)
    normalized: List[contracts.ProbeResult] = []
    for probe in probe_results:
        if probe.action_id is None:
            normalized.append(probe)
            continue
        normalized.append(
            replace(
                probe,
                action_id=_canonical_probe_action_id(probe.action_id, registry, alias_index),
            )
        )
    return tuple(normalized)


def _probe_contract_action_validation_findings(
    root: Path,
    actions: Sequence[contracts.ActionRecord],
    *,
    raw_contract: Optional[Mapping[str, object]] = None,
    probe_contract: Optional[Mapping[str, object]] = None,
) -> Tuple[contracts.Finding, ...]:
    if raw_contract is None and not (root / _PROBE_CONTRACT_RELATIVE_PATH).is_file():
        return ()
    probe_contract = probe_contract if probe_contract is not None else probes.load_probe_contract(root)
    registry, alias_index = _probe_action_resolution(actions)
    raw_contract = raw_contract if raw_contract is not None else probes.load_raw_probe_contract(root)
    declared_actions = {
        _canonical_probe_action_id(str(action_id), registry, alias_index)
        for action_id in raw_contract.get("actions", ())
        if isinstance(action_id, str) and action_id.strip()
    }
    selected_output_action = raw_contract.get("action_id")
    if isinstance(selected_output_action, str) and selected_output_action:
        declared_actions.add(
            _canonical_probe_action_id(selected_output_action, registry, alias_index)
        )
    approval_binding = raw_contract.get("approval_binding")
    if isinstance(approval_binding, Mapping):
        selected_approval_action = approval_binding.get("action_id")
        if isinstance(selected_approval_action, str) and selected_approval_action:
            declared_actions.add(
                _canonical_probe_action_id(selected_approval_action, registry, alias_index)
            )
    inventory_actions = set(registry)
    unknown = sorted({action_id for action_id in declared_actions if action_id not in inventory_actions})
    findings = tuple(
        contracts.Finding(
            finding_id="ENF-001",
            status="not-verified",
            phase="pre-deploy",
            plane="runtime",
            reason_code="probe-action-not-in-inventory",
            summary=f"Probe contract action '{action_id}' is absent from the inventory.",
            details=(
                f"governance/probe-contract.json declares action '{action_id}', but the "
                "action inventory did not declare it from registry/spec/Python sources. "
                "Probe actions must not silently outgrow the inventory."
            ),
            affected_actions=(action_id,),
            evidence_refs=(str(_PROBE_CONTRACT_RELATIVE_PATH),),
        )
        for action_id in unknown
    )
    return findings


def _declared_enforcement_probe_actions(
    root: Path,
    actions: Sequence[contracts.ActionRecord],
    *,
    probe_contract: Optional[Mapping[str, object]] = None,
) -> Tuple[str, ...]:
    if probe_contract is None and not (root / _PROBE_CONTRACT_RELATIVE_PATH).is_file():
        return ()
    registry, alias_index = _probe_action_resolution(actions)
    contract = probe_contract if probe_contract is not None else probes.load_probe_contract(root)
    return tuple(
        _canonical_probe_action_id(str(action_id), registry, alias_index)
        for action_id in contract["actions"]
    )


def _bound_action_not_probed_finding(
    root: Path, action: contracts.ActionRecord, phase: str
) -> contracts.Finding:
    binding_text = _bound_policy_binding_text(action)
    return contracts.Finding(
        finding_id="ENF-001",
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code="bound-action-not-probed",
        summary=f"Bound action '{action.action_id}' has no enforcement probe coverage.",
        details=(
            f"Action '{action.action_id}' declares runtime policy binding(s) "
            f"{binding_text}, but governance/probe-contract.json names no "
            "enforcement probe action for it. Bound actions must be probed "
            "explicitly; omission is never inferred as covered."
        ),
        affected_actions=(action.action_id,),
        evidence_refs=_bound_action_evidence_refs(root, action),
    )


def _required_runtime_proof_missing_finding(
    root: Path,
    action: contracts.ActionRecord,
    phase: str,
    *,
    finding_id: str,
    reason_code: str,
    control_name: str,
) -> contracts.Finding:
    return contracts.Finding(
        finding_id=finding_id,
        status="not-verified",
        phase=phase,
        plane="runtime",
        reason_code=reason_code,
        summary=f"Bound action '{action.action_id}' has no verified {control_name} proof.",
        details=(
            f"Action '{action.action_id}' is runtime-bound in the inventory and "
            f"declares {control_name} as required, but this assessment produced "
            "no passing probe result for that same action. Missing proof is "
            "reported not-verified rather than invented or borrowed from a "
            "different action."
        ),
        affected_actions=(action.action_id,),
        evidence_refs=_bound_action_evidence_refs(root, action),
    )


def _all_not_verified(statuses: Sequence[str]) -> bool:
    remaining = {status for status in statuses if status != "not-applicable"}
    return not remaining or remaining == {"not-verified"}


def _reconcile_bound_action_probe_coverage(
    root: Path,
    actions: Sequence[contracts.ActionRecord],
    probe_results: Sequence[contracts.ProbeResult],
    phase: str,
    *,
    raw_contract: Optional[Mapping[str, object]] = None,
    probe_contract: Optional[Mapping[str, object]] = None,
) -> Tuple[contracts.Finding, ...]:
    registry, alias_index = _probe_action_resolution(actions)
    declared_probe_actions = set(
        _declared_enforcement_probe_actions(root, actions, probe_contract=probe_contract)
    )
    passing_actions_by_probe: Dict[str, set[str]] = {}
    enforcement_statuses_by_action: Dict[str, set[str]] = {}
    unattributed_probe_ids = {
        probe.probe_id for probe in probe_results if probe.reason_code == "probe-action-unattributed"
    }
    for probe in probe_results:
        if probe.action_id is None:
            continue
        canonical_action_id = _canonical_probe_action_id(probe.action_id, registry, alias_index)
        if (
            probe.probe_id not in _PROBE_FINDING_IDS
            and probe.probe_id not in probes.PATH_PROBE_IDS
        ):
            enforcement_statuses_by_action.setdefault(canonical_action_id, set()).add(probe.status)
        if probe.status != "pass":
            continue
        passing_actions_by_probe.setdefault(probe.probe_id, set()).add(canonical_action_id)

    findings: List[contracts.Finding] = list(
        replace(finding, phase=phase)
        for finding in _probe_contract_action_validation_findings(
            root,
            actions,
            raw_contract=raw_contract,
            probe_contract=probe_contract,
        )
    )
    for probe in probe_results:
        if probe.reason_code != "probe-action-unattributed":
            continue
        findings.append(
            _probe_action_unattributed_finding(
                probe, phase, tuple(sorted(declared_probe_actions))
            )
        )
    for action in actions:
        if not _action_is_bound(action):
            continue
        if action.action_id not in declared_probe_actions:
            findings.append(_bound_action_not_probed_finding(root, action, phase))
        elif _all_not_verified(tuple(enforcement_statuses_by_action.get(action.action_id, ()))):
            findings.append(_bound_action_missing_enforcement_proof_finding(root, action, phase))

        approval_required = (
            action.binding_requires_approval is True or action.approval_required is True
        )
        if (
            probes.APPROVAL_PROBE_ID not in unattributed_probe_ids
            and (
                approval_required
                and action.action_id
                not in passing_actions_by_probe.get(probes.APPROVAL_PROBE_ID, set())
            )
        ):
            findings.append(
                _required_runtime_proof_missing_finding(
                    root,
                    action,
                    phase,
                    finding_id="APR-001",
                    reason_code="bound-action-missing-approval-proof",
                    control_name="approval",
                )
            )
        if (
            probes.OUTPUT_PROBE_ID not in unattributed_probe_ids
            and (
                action.binding_requires_output is True
                and action.action_id
                not in passing_actions_by_probe.get(probes.OUTPUT_PROBE_ID, set())
            )
        ):
            findings.append(
                _required_runtime_proof_missing_finding(
                    root,
                    action,
                    phase,
                    finding_id="OUT-001",
                    reason_code="bound-action-missing-output-proof",
                    control_name="output mediation",
                )
            )
        if (
            probes.AUDIT_PROBE_ID not in unattributed_probe_ids
            and (
                action.binding_requires_durable_audit is True
                and action.action_id
                not in passing_actions_by_probe.get(probes.AUDIT_PROBE_ID, set())
            )
        ):
            findings.append(
                _required_runtime_proof_missing_finding(
                    root,
                    action,
                    phase,
                    finding_id="AUD-001",
                    reason_code="bound-action-missing-durable-audit-proof",
                    control_name="durable audit",
                )
            )
    return tuple(findings)


#: The assessor's own tested complete-tuple pin, split into the two
#: manifest-schema pin groups. Purely a technical grouping of
#: :data:`maf_adapter._OBSERVED_TUPLE_KEYS` -- never a business policy
#: choice about which dependency/specification "matters more".
_PIN_DEPENDENCY_TUPLE_KEYS: Tuple[str, ...] = ("agent-hooks-sdk", "agent-framework-core")
_PIN_SPECIFICATION_TUPLE_KEYS: Tuple[str, ...] = (
    "agent-hooks-spec",
    "ctk-vectors",
    "conformance-python",
    "acs-policy-schema",
)


def _pins_summary(expected_tuple: Mapping[str, str]) -> Dict[str, object]:
    """Deterministic, payload-free ``pins`` summary built from the
    assessor's own tested complete pin tuple (``pin_comparison.expected``,
    read straight from ``references/upstream-pin.json`` -- never the
    target's own possibly-absent "observed" state), so it is always
    available regardless of whether the target declared
    ``governance/installed-packages.json`` at all. Each value already
    carries its own commit/hash provenance (see
    ``maf_adapter._expected_tuple_from_pin``); ``probe_suite`` is left
    unset since ``render._normalize_pins`` already fills in a
    schema-compatible default for it.
    """
    return {
        "dependencies": tuple(
            {"name": key, "version": expected_tuple[key]} for key in _PIN_DEPENDENCY_TUPLE_KEYS
        ),
        "specifications": tuple(
            {"name": key, "version": expected_tuple[key]} for key in _PIN_SPECIFICATION_TUPLE_KEYS
        ),
    }


def _change_plane_workflows(
    root: Path, evidence: Sequence[contracts.EvidenceRef]
) -> Tuple[Mapping[str, object], ...]:
    """Per-workflow-file ``{path, sha256}`` entries for the change-plane
    artifact, derived only from the already-computed aggregate
    ``ghcp-workflows`` evidence reference this same pre-deploy run
    already collected -- never re-walks the filesystem independently.
    Returns an empty tuple whenever that evidence is absent (e.g. a dirty
    workflow set, or no workflow files at all) rather than fabricating
    one.
    """
    for item in evidence:
        if item.evidence_id != "ghcp-workflows":
            continue
        paths = tuple(line for line in item.source.split("\n") if line)
        try:
            hashed = canonical.hash_files(root, paths)
        except canonical.CanonicalizationError:
            return ()
        return tuple(hashed["files"])
    return ()


def _change_plane_summary(
    root: Path,
    source: contracts.SourceRef,
    options: contracts.AssessmentOptions,
    evidence: Sequence[contracts.EvidenceRef],
) -> Dict[str, object]:
    """Deterministic, payload-free ``change_plane`` summary: the assessed
    repository, the tested workflow file hashes, and -- only when the CLI
    itself supplied a deploy identity (``--deploy-identity``), never a
    raw federated-credential/role-assignment payload from live Azure
    evidence -- a single conservative ``deploy`` identity entry.
    """
    identities: Tuple[Mapping[str, object], ...] = ()
    if options.deploy_identity:
        identities = ({"identity": options.deploy_identity, "kind": "deploy"},)
    return {
        "repository": source.repository,
        "workflows": _change_plane_workflows(root, evidence),
        "identities": identities,
    }


def _conformance_claims_from_controls(
    controls: Mapping[str, object]
) -> Tuple[Mapping[str, object], ...]:
    """One conformance claim per GHCP change-plane ``Status`` control --
    a mechanical, non-invented pass-through of what
    ``ghcp.assess_change_plane`` already computed, never a new judgment
    of its own. The one boolean control
    (``ghcp_internal_loop_intercepted``) is skipped: it is never a
    ``Status`` value and a conformance claim's own ``status`` field can
    never accept one.
    """
    return tuple(
        {
            "claim_id": key,
            "description": f"GitHub Copilot change-plane control: {key}.",
            "status": value,
            "evidence_refs": (),
        }
        for key, value in controls.items()
        if isinstance(value, str)
    )


def _default_branch_unresolved_finding() -> contracts.Finding:
    """``--live-github`` was selected, but no default branch could be
    established at all (neither ``--default-branch`` nor a local
    ``origin/HEAD`` ref) -- so no live branch-protection request is ever
    sent for a guessed branch name. Reuses GHCP's own
    ``github-live-evidence-unavailable`` reason code (the same one
    ``ghcp._github_not_verified`` assigns to any other failed live
    GitHub collection): this is semantically the same "live GitHub
    evidence could not be collected" outcome, and -- since that reason
    code is not a key of :data:`_CONDITIONAL_LIVE_EXEMPT_REASON_CODES`,
    unlike ``branch-protection-not-verified-statically`` -- it correctly
    fails ``--gate`` without any further exemption logic.
    """
    return contracts.Finding(
        finding_id="GHCP-002",
        status="not-verified",
        phase="pre-deploy",
        plane="change",
        reason_code="github-live-evidence-unavailable",
        summary="Live GitHub branch-protection evidence could not be collected: no default branch could be established.",
        details=(
            "--live-github was selected, but neither --default-branch "
            "nor a local origin/HEAD ref could establish this "
            "repository's default branch; rather than guess a branch "
            "name (e.g. an assumed 'main'), no live GitHub evidence "
            "collection was attempted at all."
        ),
    )


def _collect_selected_live_evidence(
    root: Path,
    options: contracts.AssessmentOptions,
    default_branch: Optional[str],
) -> Tuple[Optional[Mapping[str, object]], Optional[Mapping[str, object]], List[contracts.Finding]]:
    """Collect only the live evidence explicitly selected by CLI
    arguments -- ``--live-github`` for GitHub, all three of
    ``--subscription``/``--staging-resource-group``/``--deploy-identity``
    together for Azure -- appending any resulting not-verified finding.
    Never invents a selection the CLI did not make.

    When ``--live-github`` was selected but *default_branch* is
    ``None`` (no local ``origin/HEAD`` ref and no ``--default-branch``
    override), never calls ``ghcp.collect_live_github`` with a guessed
    branch name: it appends
    :func:`_default_branch_unresolved_finding` instead and leaves
    ``live_github`` as ``None``.
    """
    findings: List[contracts.Finding] = []
    live_github: Optional[Mapping[str, object]] = None
    if options.live_github:
        repository = options.repository
        if not repository:
            raise ValueError("--live-github requires --repo owner/repository")
        if default_branch is None:
            findings.append(_default_branch_unresolved_finding())
        else:
            result = ghcp.collect_live_github(repository, default_branch, _default_command_runner)
            if result.finding is not None:
                findings.append(result.finding)
            live_github = result.data

    live_azure: Optional[Mapping[str, object]] = None
    if options.subscription and options.staging_resource_group and options.deploy_identity:
        result = ghcp.collect_live_azure(
            options.subscription,
            options.staging_resource_group,
            options.deploy_identity,
            _default_command_runner,
        )
        if result.finding is not None:
            findings.append(result.finding)
        live_azure = result.data

    return live_github, live_azure, findings


def _assess_repository_controls(
    root: Path,
    source: contracts.SourceRef,
    options: contracts.AssessmentOptions,
    phase: str,
) -> contracts.AssessmentResult:
    """Assess the complete repository control set for a deploy phase."""
    inv = inventory.build_action_inventory(root)
    findings: List[contracts.Finding] = list(inv.findings)
    evidence: List[contracts.EvidenceRef] = list(_spec_section_8_evidence(inv, source, options.now))

    policy_hashes: Tuple[Mapping[str, str], ...] = ()
    if inv.policy_paths:
        policy_hashes = tuple(canonical.hash_files(root, inv.policy_paths)["files"])

    pin = maf_adapter.load_upstream_pin(_UPSTREAM_PIN_PATH)
    observed_tuple = maf_adapter.MAFAdapter().resolved_tuple(root)
    pin_comparison = maf_adapter.compare_upstream_tuple(observed_tuple, pin)
    if pin_comparison.finding is not None:
        findings.append(replace(pin_comparison.finding, phase=phase))

    graph = mediation.build_mediation_graph(root, inv.actions, maf_adapter.MAFAdapter())
    provider_graph = mediation.assess_provider_paths(root, inv.actions)
    paths = graph.paths + provider_graph.paths
    findings.extend(provider_graph.findings)

    probe_results, probe_findings = _run_probe_sets(root, phase, graph.paths)
    findings.extend(probe_findings)
    raw_probe_contract, probe_contract = _load_probe_contract_context(root)

    approval_probe_results, approval_findings = _run_approval_coverage(
        root, phase, options.now
    )
    findings.extend(approval_findings)
    output_probe_results, output_findings = _run_output_coverage(root, phase)
    findings.extend(output_findings)
    probe_results = _canonicalize_probe_results(
        probe_results + approval_probe_results + output_probe_results,
        inv.actions,
    )

    probe_results, derived_findings, probe_evidence = _bind_probe_evidence(
        probe_results,
        source,
        options,
        policy_hashes,
        path_evidence_by_id={
            path.path_id: path.evidence_refs for path in graph.paths
        },
    )
    mediated_paths, mediation_findings = mediation.apply_execution_receipts(
        graph.paths, probe_results, phase=phase
    )
    paths = mediated_paths + provider_graph.paths
    findings.extend(mediation_findings)
    findings.extend(derived_findings)
    findings.extend(
        _reconcile_bound_action_probe_coverage(
            root,
            inv.actions,
            probe_results,
            phase,
            raw_contract=raw_probe_contract,
            probe_contract=probe_contract,
        )
    )
    evidence.extend(probe_evidence)

    alert_finding, alert_evidence = alerts.assess_alerts(root, phase, None)
    findings.append(alert_finding)
    evidence.extend(alert_evidence)

    default_branch = options.default_branch or _resolve_default_branch(root)
    live_github, live_azure, live_findings = _collect_selected_live_evidence(
        root, options, default_branch
    )
    findings.extend(replace(finding, phase=phase) for finding in live_findings)

    change_plane_result = ghcp.assess_change_plane(root, live_github, live_azure)
    findings.extend(replace(finding, phase=phase) for finding in change_plane_result.findings)
    evidence.extend(replace(ref, phase=phase) for ref in change_plane_result.evidence)

    pins = _pins_summary(pin_comparison.expected)
    change_plane = _change_plane_summary(root, source, options, evidence)
    conformance_claims = _conformance_claims_from_controls(change_plane_result.controls)

    findings.sort(key=lambda finding: (finding.finding_id, finding.reason_code))
    # Static findings cite the repository-relative source paths they
    # actually read; bind each to a real, byte-hashed evidence entry so no
    # finding is left resting on a citation that resolves to nothing.
    # Runs last, and never over an id another collector already bound, so
    # it can only ever add provenance this assessment proved for itself.
    evidence.extend(
        _bind_static_source_evidence(
            root,
            findings,
            source,
            options,
            policy_hashes,
            already_collected=frozenset(ref.evidence_id for ref in evidence),
            paths=paths,
        )
    )
    return contracts.AssessmentResult(
        source=source,
        actions=inv.actions,
        paths=paths,
        probes=probe_results,
        findings=tuple(findings),
        evidence=tuple(evidence),
        policy_hashes=policy_hashes,
        pins=pins,
        conformance_claims=conformance_claims,
        conformance_reports=(),
        change_plane=change_plane,
        residual_risks=(),
        captured_at=options.now,
        phase=phase,
        live_github_selected=options.live_github,
        live_azure_selected=options.live_azure,
    )


def _assess_pre_deploy(
    root: Path, source: contracts.SourceRef, options: contracts.AssessmentOptions
) -> contracts.AssessmentResult:
    """Run the complete pre-deploy repository and application assessment."""
    return _assess_repository_controls(root, source, options, "pre-deploy")


def _assess_post_deploy(
    root: Path, source: contracts.SourceRef, options: contracts.AssessmentOptions
) -> contracts.AssessmentResult:
    """Rerun the complete assessment against a non-production deployment."""
    probes.validate_post_deploy_target("post-deploy", options.staging, destructive=False)
    return _assess_repository_controls(root, source, options, "post-deploy")


def assess(options: contracts.AssessmentOptions) -> contracts.AssessmentResult:
    """Run the read-only lifecycle assessment selected by *options*.

    Every phase's :class:`~contracts.AssessmentResult` is stamped
    ``phase=options.phase`` explicitly by this dispatcher -- never
    inferred from any finding, evidence label, or other module's own
    output -- so the authoritative lifecycle phase is always bound
    deterministically from the CLI's own selection and can never be
    established or escalated by evidence.
    """
    root = Path(options.root)
    source = resolve_source(root)
    inputs.resolve_inputs(root, options.phase)

    if options.phase == "design":
        return _assess_design(root, source, options)
    if options.phase == "pre-deploy":
        return _assess_pre_deploy(root, source, options)
    if options.phase == "post-deploy":
        return _assess_post_deploy(root, source, options)
    raise ValueError(f"unsupported phase: {options.phase!r}")  # pragma: no cover - guarded by argparse choices


# ---------------------------------------------------------------------------
# exit_code
# ---------------------------------------------------------------------------


def exit_code(result: contracts.AssessmentResult, gate: bool) -> int:
    """Return the process exit code for a *completed* assessment.

    Without ``--gate``, a completed assessment always returns ``0``
    regardless of findings (section "Writes and exits").

    With ``--gate``:

    - Any ``must-fix`` finding always fails the gate (``1``), in every
      phase.
    - ``design`` requires nothing further: ``inventory.build_action_inventory``
      only ever emits ``ACT-001``/``ACT-002``, and ``ACT-001`` (a missing
      SAFE declaration) is always ``not-verified``, never ``must-fix``, so
      design's own gate is naturally "no ACT-001/ACT-002 must-fix" without
      any extra phase-specific code here.
    - ``pre-deploy``/``post-deploy`` additionally fail the gate on any
      remaining ``not-verified`` finding, *except* one whose
      ``reason_code`` is a key of
      :data:`_CONDITIONAL_LIVE_EXEMPT_REASON_CODES` **and** whose mapped
      ``AssessmentResult`` selection attribute (``live_github_selected``/
      ``live_azure_selected``) is ``False`` -- meaning that particular
      live capability was never even attempted because it was not
      selected on the CLI, so the control staying not-verified is an
      optional, unselected one: the verdict remains partial, and it does
      not fail the gate. When the corresponding selection attribute is
      ``True`` instead, the exact same reason code must fail the gate,
      since it now means a live capability the caller *did* explicitly
      select still could not be resolved. Every live capability the CLI
      selected surfaces its own collection failure (when collection
      itself fails) via a different reason code
      (``github-live-evidence-unavailable`` and friends), which is never
      a key of :data:`_CONDITIONAL_LIVE_EXEMPT_REASON_CODES` and so is
      never exempt -- exactly "every live capability explicitly selected
      by CLI arguments" must resolve.
    """
    if not gate:
        return 0

    for finding in result.findings:
        if finding.status == "must-fix":
            return 1

    if result.phase == "design":
        return 0

    for finding in result.findings:
        if finding.status != "not-verified":
            continue
        selection_attr = _CONDITIONAL_LIVE_EXEMPT_REASON_CODES.get(finding.reason_code)
        if selection_attr is not None and not getattr(result, selection_attr):
            continue
        return 1

    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _options_from_namespace(namespace: argparse.Namespace) -> contracts.AssessmentOptions:
    root = Path(namespace.target).resolve()
    return contracts.AssessmentOptions(
        root=root,
        phase=namespace.phase,
        emit=namespace.emit,
        gate=namespace.gate,
        live_github=namespace.live_github,
        live_azure=bool(
            namespace.subscription
            and namespace.staging_resource_group
            and namespace.deploy_identity
        ),
        staging=bool(namespace.staging_resource_group),
        repository=namespace.repository,
        default_branch=None,
        subscription=namespace.subscription,
        staging_resource_group=namespace.staging_resource_group,
        deploy_identity=namespace.deploy_identity,
        now=_now(),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """The CLI entry point.

    Exit codes (see the module docstring and the design for the full
    rationale):

    - ``0``: assessment completed; if ``--gate`` was set, the selected
      phase's requirements passed. When ``--scaffold``/``--confirm-scaffold``
      was given instead, ``0`` means the bounded governance scaffold was
      written and nothing else in this docstring's phase-assessment
      description applies to that run.
    - ``1``: ``--gate`` failed because a must-fix (or, outside design, a
      remaining not-verified) finding remains; any artifacts ``--emit``
      requested were still validly written.
    - ``2``: invalid input or a missing prerequisite prevented a valid
      assessment -- raised as :class:`argparse.ArgumentError`/
      :class:`ArgumentError`/:class:`ValueError` anywhere in argument
      parsing or assessment (this codebase's own convention already
      makes essentially every "malformed/missing prerequisite" error a
      ``ValueError`` subclass, so no additional per-module special-casing
      is required here). This also covers
      :class:`scaffold.ScaffoldRefusedError` (itself a ``ValueError``
      subclass) for a scaffold request that was refused -- e.g. only one
      of ``--scaffold``/``--confirm-scaffold`` given, or an unsafe target.
    - ``3``: an assessor/adapter/probe/artifact failure -- every
      exception that is not one of the above, whether a *declared*
      tool failure (e.g. :class:`render.ArtifactWriteError`) or a
      genuinely unexpected one, is reported here by exception class name
      and never allowed to masquerade as a completed, success-shaped
      result. No partial artifact ever replaces a prior valid one:
      :func:`render.write_artifacts` itself only ever fully commits or
      fully rolls back.
    """
    try:
        namespace = parse_args(argv)
    except (argparse.ArgumentError, ArgumentError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if namespace.scaffold is not None or namespace.confirm_scaffold:
        # Explicit scaffold CLI path -- an entirely separate mode from
        # phase assessment. Never reaches `_options_from_namespace`/
        # `assess()`/`render.write_artifacts` below: the two code paths
        # do not share a call site, so a normal lifecycle-phase run can
        # never invoke `scaffold.scaffold`, and a scaffold run never
        # performs a phase assessment.
        try:
            written = scaffold.scaffold(
                Path(namespace.target), namespace.scaffold, namespace.confirm_scaffold
            )
        except (argparse.ArgumentError, ArgumentError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        except Exception as error:  # noqa: BLE001 - deliberately broad; see docstring.
            print(f"{type(error).__name__}: {error}", file=sys.stderr)
            return 3
        for path in written:
            print(path)
        return 0

    options = _options_from_namespace(namespace)

    try:
        result = assess(options)
    except (argparse.ArgumentError, ArgumentError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - deliberately broad; see docstring.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 3

    if options.emit:
        try:
            render.write_artifacts(
                options.root,
                result,
                namespace.manifest_path,
                namespace.evidence_path,
                namespace.apply_plan_path,
            )
        except Exception as error:  # noqa: BLE001 - deliberately broad; see docstring.
            print(f"{type(error).__name__}: {error}", file=sys.stderr)
            return 3

    return exit_code(result, options.gate)


if __name__ == "__main__":
    sys.exit(main())
