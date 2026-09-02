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
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

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

#: Fallback default branch name used only when a local git ref for the
#: remote's HEAD cannot be resolved (e.g. ``origin/HEAD`` was never set
#: locally). This is a technical default for shelling out to
#: ``gh api .../branches/{default_branch}/...``, never a business policy
#: choice -- it only ever matters when ``--live-github`` is also passed.
_DEFAULT_BRANCH_FALLBACK = "main"

#: The reason codes GHCP's own ``assess_change_plane`` assigns to a
#: control that is simply *statically unverifiable* -- i.e. one that was
#: never actually attempted because no corresponding ``--live-github``/
#: live-Azure evidence was requested at all, as opposed to one that was
#: requested and failed. Per the gate semantics this module implements,
#: an optional, unselected live capability may remain not-verified
#: without failing ``--gate``; a selected-but-failed one may not.
_OPTIONAL_UNSELECTED_LIVE_REASON_CODES = frozenset(
    {
        "branch-protection-not-verified-statically",
        "azure-login-not-verified-statically",
        "identity-separation-not-verified-statically",
    }
)


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
        required=True,
        choices=contracts.SUPPORTED_PHASES,
        help="lifecycle phase to assess",
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
    one cross-flag prerequisite: ``--phase post-deploy`` requires
    ``--staging-resource-group`` naming an explicit, non-production
    staging resource group, since post-deploy must never be run,
    even read-only, against an unnamed or ambiguous target environment.
    """
    parser = _build_parser()
    namespace = parser.parse_args(list(argv) if argv is not None else None)
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


def _resolve_default_branch(root: Path) -> str:
    """Best-effort local read of the remote's default branch, used only
    to shape a ``--live-github`` request. Never raises: falls back to
    :data:`_DEFAULT_BRANCH_FALLBACK` when the local ref is unavailable
    (e.g. this checkout never ran ``git remote set-head origin --auto``),
    since this is a technical fallback, not a business decision the CLI
    may legitimately invent evidence around.
    """
    try:
        ref = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
    except ValueError:
        return _DEFAULT_BRANCH_FALLBACK
    return ref.rsplit("/", 1)[-1] if ref else _DEFAULT_BRANCH_FALLBACK


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


def _assess_design(
    root: Path, source: contracts.SourceRef, options: contracts.AssessmentOptions
) -> contracts.AssessmentResult:
    """design: inventory and SAFE-requirement validation only."""
    inv = inventory.build_action_inventory(root)
    return contracts.AssessmentResult(
        source=source,
        actions=inv.actions,
        paths=(),
        probes=(),
        findings=inv.findings,
        evidence=_spec_section_8_evidence(inv, source, options.now),
        policy_hashes=(),
        pins={},
        conformance_claims=(),
        conformance_reports=(),
        change_plane={},
        residual_risks=(),
        captured_at=options.now,
        phase="design",
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


def _run_probe_sets(root: Path, phase: str) -> Tuple[Tuple[contracts.ProbeResult, ...], Tuple[contracts.Finding, ...]]:
    if not (root / _PROBE_CONTRACT_RELATIVE_PATH).is_file():
        return (), (_missing_probe_contract_finding(phase),)
    probe_results = probes.run_enforcement_probe_set(root) + probes.run_privacy_probe_set(root)
    return probe_results, probes.findings_from_probes(probe_results)


def _collect_selected_live_evidence(
    root: Path,
    options: contracts.AssessmentOptions,
    default_branch: str,
) -> Tuple[Optional[Mapping[str, object]], Optional[Mapping[str, object]], List[contracts.Finding]]:
    """Collect only the live evidence explicitly selected by CLI
    arguments -- ``--live-github`` for GitHub, all three of
    ``--subscription``/``--staging-resource-group``/``--deploy-identity``
    together for Azure -- appending any resulting not-verified finding.
    Never invents a selection the CLI did not make.
    """
    findings: List[contracts.Finding] = []
    live_github: Optional[Mapping[str, object]] = None
    if options.live_github:
        repository = options.repository
        if not repository:
            raise ValueError("--live-github requires --repo owner/repository")
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


def _assess_pre_deploy(
    root: Path, source: contracts.SourceRef, options: contracts.AssessmentOptions
) -> contracts.AssessmentResult:
    """pre-deploy: inventory, pins, static mediation graph, policy
    hashing, approval binding (via the same mediation graph), probes,
    alerts, and GHCP static/live checks."""
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
        findings.append(pin_comparison.finding)

    graph = mediation.build_mediation_graph(root, inv.actions, maf_adapter.MAFAdapter())
    provider_graph = mediation.assess_provider_paths(root, inv.actions)
    paths = graph.paths + provider_graph.paths
    findings.extend(graph.findings)
    findings.extend(provider_graph.findings)

    probe_results, probe_findings = _run_probe_sets(root, "pre-deploy")
    findings.extend(probe_findings)

    alert_finding, alert_evidence = alerts.assess_alerts(root, "pre-deploy", None)
    findings.append(alert_finding)
    evidence.extend(alert_evidence)

    default_branch = options.default_branch or _resolve_default_branch(root)
    live_github, live_azure, live_findings = _collect_selected_live_evidence(
        root, options, default_branch
    )
    findings.extend(live_findings)

    change_plane_result = ghcp.assess_change_plane(root, live_github, live_azure)
    findings.extend(change_plane_result.findings)
    evidence.extend(change_plane_result.evidence)

    findings.sort(key=lambda finding: (finding.finding_id, finding.reason_code))
    return contracts.AssessmentResult(
        source=source,
        actions=inv.actions,
        paths=paths,
        probes=probe_results,
        findings=tuple(findings),
        evidence=tuple(evidence),
        policy_hashes=policy_hashes,
        pins={},
        conformance_claims=(),
        conformance_reports=(),
        change_plane={},
        residual_risks=(),
        captured_at=options.now,
        phase="pre-deploy",
    )


def _assess_post_deploy(
    root: Path, source: contracts.SourceRef, options: contracts.AssessmentOptions
) -> contracts.AssessmentResult:
    """post-deploy: rerun only the non-destructive application probes,
    plus whatever live evidence the CLI explicitly selected. Never reruns
    GHCP's static change-plane analysis (that is a pre-deploy-only,
    static-artifact concern) and never runs a destructive probe."""
    probes.validate_post_deploy_target("post-deploy", options.staging, destructive=False)

    probe_results, findings = _run_probe_sets(root, "post-deploy")
    findings = list(findings)
    evidence: List[contracts.EvidenceRef] = []

    default_branch = options.default_branch or _resolve_default_branch(root)
    live_github, live_azure, live_findings = _collect_selected_live_evidence(
        root, options, default_branch
    )
    findings.extend(live_findings)
    # live_github/live_azure payloads carry no trustworthy, locally-bound
    # provenance of their own (see ghcp.LiveEvidenceResult); only their
    # findings -- never a fabricated EvidenceRef -- are surfaced here.
    del live_github, live_azure

    findings.sort(key=lambda finding: (finding.finding_id, finding.reason_code))
    return contracts.AssessmentResult(
        source=source,
        actions=(),
        paths=(),
        probes=probe_results,
        findings=tuple(findings),
        evidence=tuple(evidence),
        policy_hashes=(),
        pins={},
        conformance_claims=(),
        conformance_reports=(),
        change_plane={},
        residual_risks=(),
        captured_at=options.now,
        phase="post-deploy",
    )


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
      ``reason_code`` is one of the three GHCP reason codes that mean "a
      live capability was never even attempted because it was not
      selected on the CLI" (see
      :data:`_OPTIONAL_UNSELECTED_LIVE_REASON_CODES`) -- an optional,
      unselected live capability may remain not-verified, keeps the
      verdict partial, and does not fail the gate. Every live capability
      the CLI *did* select surfaces its own failure (when collection
      fails) via a different reason code
      (``github-live-evidence-unavailable`` and friends), which is never
      exempted here -- exactly "every live capability explicitly selected
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
        if (
            finding.status == "not-verified"
            and finding.reason_code not in _OPTIONAL_UNSELECTED_LIVE_REASON_CODES
        ):
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
      phase's requirements passed.
    - ``1``: ``--gate`` failed because a must-fix (or, outside design, a
      remaining not-verified) finding remains; any artifacts ``--emit``
      requested were still validly written.
    - ``2``: invalid input or a missing prerequisite prevented a valid
      assessment -- raised as :class:`argparse.ArgumentError`/
      :class:`ArgumentError`/:class:`ValueError` anywhere in argument
      parsing or assessment (this codebase's own convention already
      makes essentially every "malformed/missing prerequisite" error a
      ``ValueError`` subclass, so no additional per-module special-casing
      is required here).
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
