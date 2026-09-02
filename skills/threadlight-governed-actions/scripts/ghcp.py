"""Static assessment of the GitHub Copilot change plane.

Assesses the pull-request/CI/deployment supply chain a GitHub Copilot
coding-agent's changes travel through before they reach a live branch or a
live Azure environment: whether a change can only land via a pull request
(never a direct push, never a ``pull_request_target`` job that checks out
untrusted head content), whether ownership/required-check coverage is
declared, whether required CI actually exercises the Conformance Test Kit
(CTK) and governed application probes (and, when the target ships an eval
suite, that suite's own runner), whether every third-party and first-party
action reference is pinned to an exact 40-character commit SHA, whether
Azure deployment authenticates via OIDC/workload-identity-federation
(WIF) rather than a long-lived secret, and whether build/test and
deployment identities are kept separate.

This is a *different* plane from runtime mediation (``mediation.py``):
change-plane controls govern who can change and ship code and through what
gates, never GitHub Copilot's own internal reasoning or tool-calling loop.
``assess_change_plane`` therefore always reports
``ghcp_internal_loop_intercepted`` as ``False`` — this assessor makes no
claim, and never will, about intercepting that internal loop; only the
runtime-mediation plane addresses tool-call interception at all, and only
for the target's own declared actions.

Every check here is read-only and file-based: no GitHub API call, no
Azure API call, no git subprocess. ``assess_change_plane`` accepts
``live_github``/``live_azure`` as plain, already-collected evidence
mappings (their collection is a separate, later concern); when both are
``None`` the assessor still emits every finding warranted by the local
files alone, but a handful of controls — whether a branch's protection
rule or required-status-check list is actually enforced on GitHub, and
whether two Azure principals are actually distinct — can never be proven
by local files by themselves and are reported ``not-verified`` rather
than an inferred ``pass``. Static evidence never upgrades itself into
live proof.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from contracts import EvidenceRef, Finding, Status

import canonical


class ChangePlaneError(RuntimeError):
    """Raised when a change-plane input cannot be read or parsed at all."""


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# A `uses:` ref must end in exactly this many lowercase hex characters after
# the last `@` to count as pinned; local `./`-prefixed actions are exempt.
_SHA_PIN_RE = re.compile(r"^[0-9a-f]{40}$")

_DEPLOY_ACTION_MARKERS: Tuple[str, ...] = (
    "azure/webapps-deploy",
    "azure/functions-action",
    "azure/aks-deploy",
    "azure/container-apps-deploy-action",
    "azure/arm-deploy",
)

_SECRET_LOGIN_KEYS: Tuple[str, ...] = (
    "creds",
    "client-secret",
    "password",
    "publish-profile",
)

_UNTRUSTED_CHECKOUT_REF_MARKERS: Tuple[str, ...] = (
    "github.event.pull_request.head",
)

_CTK_MARKER_RE = re.compile(r"\bctk\b", re.IGNORECASE)
_APPLICATION_PROBE_MARKER_RE = re.compile(r"application[-_ ]probe", re.IGNORECASE)

# Patterns a real CODEOWNERS file must declare coverage for. "Governance
# tests" is interpreted as the skill's own ``tests/**`` tree; the two
# governed-actions evidence JSON paths are the manifest/apply-plan the
# governed-actions workflow itself produces.
_REQUIRED_CODEOWNERS_PATTERNS: Tuple[str, ...] = (
    "src/governance/**",
    "policies/**",
    "tests/**",
    ".github/workflows/governed-actions.yml",
    "tests/governed-actions-manifest.json",
    "tests/governed-actions-apply-plan.json",
)

_OWNERSHIP_FILENAMES: Tuple[str, ...] = ("CODEOWNERS", ".github/CODEOWNERS")


@dataclass(frozen=True)
class WorkflowAssessment:
    """Static control statuses for a single workflow file.

    ``pass``/``must-fix`` only; ``ci_probes`` reflects only what this single
    workflow's own steps declare (CTK + application probe), never repo-wide
    eval-suite discovery, which needs the whole tree and is layered on in
    :func:`assess_change_plane`.
    """

    path: Path
    triggers: Tuple[str, ...]
    is_deploy: bool
    pr_gate: Status
    permissions: Status
    sha_pins: Status
    oidc_wif: Status
    ci_probes: Status
    identity_ref: Optional[str]
    sha_violations: Tuple[str, ...]


@dataclass(frozen=True)
class ChangePlaneResult:
    controls: Mapping[str, "Status | bool"]
    findings: Tuple[Finding, ...]
    evidence: Tuple[EvidenceRef, ...]


# ---------------------------------------------------------------------------
# Workflow document loading
# ---------------------------------------------------------------------------


def _load_workflow_document(path: Path) -> object:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ChangePlaneError(f"cannot read workflow file {path}: {error}") from error
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - environment guard
        raise ChangePlaneError(
            "pyyaml is required to parse GitHub Actions workflows "
            f"({path}); install it with `pip install pyyaml`"
        ) from error
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as error:
        raise ChangePlaneError(f"invalid YAML in workflow file {path}: {error}") from error
    if not isinstance(document, Mapping):
        raise ChangePlaneError(f"workflow file {path} must parse to a mapping")
    return document


def _trigger_names(document: Mapping) -> Tuple[str, ...]:
    # PyYAML's 1.1 resolver treats the bare `on:` key as the boolean `True`,
    # not the string "on" -- both keys are checked so the real trigger
    # mapping is never missed regardless of how the file happens to load.
    triggers = document.get("on", document.get(True))
    if triggers is None:
        return ()
    if isinstance(triggers, str):
        return (triggers,)
    if isinstance(triggers, Sequence):
        return tuple(str(item) for item in triggers)
    if isinstance(triggers, Mapping):
        return tuple(str(key) for key in triggers.keys())
    return ()


def _walk(node: object):
    """Yield every mapping found anywhere in a nested document."""
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _all_steps(document: Mapping) -> List[Mapping]:
    steps: List[Mapping] = []
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if not isinstance(job, Mapping):
                continue
            job_steps = job.get("steps")
            if isinstance(job_steps, list):
                for step in job_steps:
                    if isinstance(step, Mapping):
                        steps.append(step)
    return steps


def _uses_refs(document: Mapping) -> List[str]:
    return [
        str(mapping["uses"])
        for mapping in _walk(document)
        if isinstance(mapping, Mapping) and "uses" in mapping and mapping["uses"]
    ]


def _job_ids_and_names(document: Mapping) -> List[str]:
    labels: List[str] = []
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job_id, job in jobs.items():
            labels.append(str(job_id))
            if isinstance(job, Mapping) and job.get("name"):
                labels.append(str(job["name"]))
    return labels


# ---------------------------------------------------------------------------
# Individual static control checks (rule 4: SHA pinning)
# ---------------------------------------------------------------------------


def _sha_pin_status(document: Mapping) -> Tuple[Status, Tuple[str, ...]]:
    violations: List[str] = []
    for ref in _uses_refs(document):
        if ref.startswith("./") or ref.startswith("../"):
            continue  # local actions carry no floating-tag supply-chain risk
        if "@" not in ref:
            violations.append(ref)
            continue
        _, _, pinned = ref.rpartition("@")
        if not _SHA_PIN_RE.match(pinned):
            violations.append(ref)
    status: Status = "must-fix" if violations else "pass"
    return status, tuple(violations)


# ---------------------------------------------------------------------------
# Permissions (rule 6, permissions half) -- feeds GHCP-004 alongside SHA pins
# ---------------------------------------------------------------------------


def _permissions_declared_and_least_privilege(value: object) -> Optional[bool]:
    """Return True/False for an explicit permissions value, or None if the
    key itself is entirely absent (caller decides what that means)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() != "write-all"
    if isinstance(value, Mapping):
        return True
    return None


def _permissions_status(document: Mapping) -> Status:
    workflow_level = _permissions_declared_and_least_privilege(document.get("permissions"))
    if workflow_level is False:
        return "must-fix"

    job_level_results: List[Optional[bool]] = []
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if not isinstance(job, Mapping):
                continue
            job_level_results.append(
                _permissions_declared_and_least_privilege(job.get("permissions"))
            )

    if any(result is False for result in job_level_results):
        return "must-fix"

    if workflow_level is True or any(result is True for result in job_level_results):
        return "pass"

    # Neither the workflow nor any job declares explicit permissions at all.
    return "must-fix"


def _grants_id_token_write(document: Mapping) -> bool:
    def _grants(value: object) -> bool:
        return (
            isinstance(value, Mapping)
            and str(value.get("id-token", "")).strip().lower() == "write"
        )

    if _grants(document.get("permissions")):
        return True
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if isinstance(job, Mapping) and _grants(job.get("permissions")):
                return True
    return False


# ---------------------------------------------------------------------------
# Azure login / OIDC (rule 5)
# ---------------------------------------------------------------------------


def _azure_login_steps(document: Mapping) -> List[Mapping]:
    return [
        step
        for step in _all_steps(document)
        if str(step.get("uses", "")).split("@", 1)[0].strip().lower() == "azure/login"
    ]


def _oidc_status(document: Mapping, login_steps: Sequence[Mapping]) -> Status:
    if not login_steps:
        return "pass"  # nothing to assess: rule 5 is not-applicable, not a finding
    if not _grants_id_token_write(document):
        return "must-fix"
    for step in login_steps:
        with_block = step.get("with")
        if not isinstance(with_block, Mapping):
            return "must-fix"
        keys_lower = {str(key).strip().lower() for key in with_block.keys()}
        if keys_lower & set(_SECRET_LOGIN_KEYS):
            return "must-fix"
        if "client-id" not in keys_lower or "tenant-id" not in keys_lower:
            return "must-fix"
    return "pass"


def _identity_ref(login_steps: Sequence[Mapping]) -> Optional[str]:
    for step in login_steps:
        with_block = step.get("with")
        if not isinstance(with_block, Mapping):
            continue
        for key in ("client-id", "creds"):
            if with_block.get(key):
                return str(with_block[key])
    return None


# ---------------------------------------------------------------------------
# Deploy classification and PR-only gating (rule 1)
# ---------------------------------------------------------------------------


def _is_deploy_workflow(document: Mapping) -> bool:
    uses_refs = [ref.split("@", 1)[0].strip().lower() for ref in _uses_refs(document)]
    if any(marker in ref for ref in uses_refs for marker in _DEPLOY_ACTION_MARKERS):
        return True
    labels = [label.lower() for label in _job_ids_and_names(document)]
    return any("deploy" in label for label in labels)


def _has_untrusted_checkout(document: Mapping) -> bool:
    for step in _all_steps(document):
        uses = str(step.get("uses", "")).split("@", 1)[0].strip().lower()
        if uses != "actions/checkout":
            continue
        with_block = step.get("with")
        if not isinstance(with_block, Mapping):
            continue
        ref_value = str(with_block.get("ref", ""))
        if any(marker in ref_value for marker in _UNTRUSTED_CHECKOUT_REF_MARKERS):
            return True
    return False


def _pr_gate_status(document: Mapping, triggers: Tuple[str, ...], is_deploy: bool) -> Status:
    if "pull_request_target" in triggers and _has_untrusted_checkout(document):
        return "must-fix"
    if "push" in triggers and is_deploy:
        return "must-fix"
    return "pass"


# ---------------------------------------------------------------------------
# CI CTK / application-probe presence (rule 3, per-workflow half)
# ---------------------------------------------------------------------------


def _document_search_text(document: Mapping) -> str:
    """Render a parsed workflow document back to text for marker searches.

    Deliberately built from the *parsed* document, never the raw file text:
    a YAML comment (for example a docstring mentioning "CTK" while
    describing a fixture that intentionally omits it) must never count as
    evidence that a step actually runs it.
    """
    return json.dumps(document, default=str)


def _ci_probes_static_status(document: Mapping, triggers: Tuple[str, ...]) -> Status:
    if "pull_request" not in triggers:
        return "pass"  # rule 3 only binds required (PR-triggered) CI
    text = _document_search_text(document)
    if _CTK_MARKER_RE.search(text) and _APPLICATION_PROBE_MARKER_RE.search(text):
        return "pass"
    return "must-fix"


# ---------------------------------------------------------------------------
# Public: assess_workflow
# ---------------------------------------------------------------------------


def assess_workflow(path: Path) -> WorkflowAssessment:
    """Assess a single GitHub Actions workflow file's static change-plane
    controls: PR-only deploy gating, explicit least-privilege permissions,
    SHA pinning, OIDC/WIF Azure login, and (for pull_request-triggered
    workflows) CTK/application-probe presence.
    """
    path = Path(path)
    document = _load_workflow_document(path)
    triggers = _trigger_names(document)
    is_deploy = _is_deploy_workflow(document)
    sha_pins, sha_violations = _sha_pin_status(document)
    login_steps = _azure_login_steps(document)
    return WorkflowAssessment(
        path=path,
        triggers=triggers,
        is_deploy=is_deploy,
        pr_gate=_pr_gate_status(document, triggers, is_deploy),
        permissions=_permissions_status(document),
        sha_pins=sha_pins,
        oidc_wif=_oidc_status(document, login_steps),
        ci_probes=_ci_probes_static_status(document, triggers),
        identity_ref=_identity_ref(login_steps),
        sha_violations=sha_violations,
    )


# ---------------------------------------------------------------------------
# Repo-wide discovery
# ---------------------------------------------------------------------------


def _discover_workflow_files(root: Path) -> Tuple[Path, ...]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return ()
    found = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    return tuple(sorted(set(found)))


def _find_ownership_file(root: Path) -> Optional[Path]:
    for relative in _OWNERSHIP_FILENAMES:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def _parse_codeowners_patterns(path: Path) -> Set[str]:
    patterns: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.add(stripped.split()[0])
    return patterns


def _discover_eval_suite_files(root: Path) -> Tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(sorted(root.glob("**/evals/**/*")))


# ---------------------------------------------------------------------------
# Live-evidence helpers
# ---------------------------------------------------------------------------


def _branch_protection_confirmed(live_github: Optional[Mapping]) -> bool:
    if not live_github:
        return False
    default_branch = live_github.get("default_branch")
    branch_protection = live_github.get("branch_protection")
    if not default_branch or not isinstance(branch_protection, Mapping):
        return False
    rule = branch_protection.get(default_branch)
    if not isinstance(rule, Mapping):
        return False
    return bool(rule.get("required_pull_request_reviews")) and bool(
        rule.get("required_status_checks")
    )


def _distinct_identities_confirmed(live_azure: Optional[Mapping]) -> bool:
    if not live_azure:
        return False
    role_assignments = live_azure.get("role_assignments")
    if not isinstance(role_assignments, Sequence):
        return False
    principal_ids = {
        entry.get("principal_id")
        for entry in role_assignments
        if isinstance(entry, Mapping) and entry.get("principal_id")
    }
    return len(principal_ids) >= 2


# ---------------------------------------------------------------------------
# Public: assess_change_plane
# ---------------------------------------------------------------------------


def assess_change_plane(
    root: Path,
    live_github: Optional[Mapping] = None,
    live_azure: Optional[Mapping] = None,
) -> ChangePlaneResult:
    """Assess an entire repository's GitHub Copilot change plane.

    Aggregates every discovered workflow's :func:`assess_workflow` result
    plus repo-wide CODEOWNERS/eval-suite discovery into the six
    GHCP-001..GHCP-006 findings and their backing ``controls``/``evidence``.
    ``live_github``/``live_azure`` are already-collected evidence mappings
    (schema: ``branch_protection``/``default_branch`` and
    ``role_assignments`` respectively); when either is ``None`` the
    corresponding live-only confirmations stay ``not-verified`` rather than
    an inferred ``pass`` -- static files alone can never prove a branch
    protection rule or a required-check list is actually enforced on
    GitHub, nor that two Azure principals are genuinely distinct.
    """
    root = Path(root)
    workflow_paths = _discover_workflow_files(root)
    assessments = tuple(assess_workflow(path) for path in workflow_paths)

    findings: List[Finding] = []
    evidence: List[EvidenceRef] = []
    controls: Dict[str, "Status | bool"] = {}

    _assess_pr_gate(assessments, findings, controls)
    _assess_codeowners(root, assessments, live_github, findings, controls)
    _assess_ci_probes(root, assessments, findings, controls)
    _assess_actions_and_permissions(assessments, findings, controls)
    _assess_oidc(assessments, findings, controls)
    _assess_identity_separation(assessments, live_azure, findings, controls)

    # Rule 7: this plane never claims to intercept GitHub Copilot's own
    # internal reasoning/tool-calling loop -- only the PR/CI/deployment
    # supply chain a change travels through. Set unconditionally.
    controls["ghcp_internal_loop_intercepted"] = False

    if workflow_paths:
        evidence.append(_workflow_set_evidence(root, workflow_paths))

    findings.sort(key=lambda finding: finding.finding_id)
    return ChangePlaneResult(
        controls=controls,
        findings=tuple(findings),
        evidence=tuple(evidence),
    )


def _workflow_set_evidence(root: Path, workflow_paths: Tuple[Path, ...]) -> EvidenceRef:
    hashed = canonical.hash_files(root, workflow_paths)
    return EvidenceRef(
        evidence_id="ghcp-workflows",
        kind="file-set",
        source="/".join(sorted(str(path.relative_to(root)) for path in workflow_paths)),
        sha256=str(hashed["set_sha256"]),
        collected_at=None,
        freshness_seconds=None,
        live_verified=False,
        phase="pre-deploy",
        repository=root.resolve().name,
        source_commit="0" * 40,
        target_environment=None,
        policy_set_sha256=None,
    )


# --- GHCP-001: PR-only deploy gating -----------------------------------


def _assess_pr_gate(
    assessments: Tuple[WorkflowAssessment, ...],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    offenders = tuple(a for a in assessments if a.pr_gate == "must-fix")
    controls["ghcp_pr_only_gate"] = "must-fix" if offenders else "pass"
    if not offenders:
        return
    findings.append(
        Finding(
            finding_id="GHCP-001",
            status="must-fix",
            phase="pre-deploy",
            plane="change",
            reason_code="unprotected-direct-change",
            summary="Protected branch accepts agent changes outside pull requests.",
            details=(
                "One or more workflows deploy on a direct `push` trigger, or "
                "run on `pull_request_target` while checking out the pull "
                "request's own untrusted head content, letting a change "
                "reach a protected branch or a live environment without "
                "going through a pull request at all."
            ),
            affected_paths=tuple(str(a.path) for a in offenders),
        )
    )


# --- GHCP-002: CODEOWNERS / branch-protection coverage -----------------


def _assess_codeowners(
    root: Path,
    assessments: Tuple[WorkflowAssessment, ...],
    live_github: Optional[Mapping],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    ownership_path = _find_ownership_file(root)
    if ownership_path is None:
        controls["ghcp_codeowners"] = "must-fix"
        findings.append(
            Finding(
                finding_id="GHCP-002",
                status="must-fix",
                phase="pre-deploy",
                plane="change",
                reason_code="codeowners-missing",
                summary=(
                    "CODEOWNERS, ruleset/branch protection, or required-check "
                    "coverage is missing or unavailable."
                ),
                details=(
                    "No `CODEOWNERS` or `.github/CODEOWNERS` file was found "
                    "at the repository root or under `.github/`. A file with "
                    "any other name (for example `CODEOWNERS.absent`) is "
                    "never treated as ownership evidence, however complete "
                    "its declared patterns look."
                ),
            )
        )
        return

    declared = _parse_codeowners_patterns(ownership_path)
    missing = tuple(
        pattern for pattern in _REQUIRED_CODEOWNERS_PATTERNS if pattern not in declared
    )
    if missing:
        controls["ghcp_codeowners"] = "must-fix"
        findings.append(
            Finding(
                finding_id="GHCP-002",
                status="must-fix",
                phase="pre-deploy",
                plane="change",
                reason_code="codeowners-incomplete-coverage",
                summary=(
                    "CODEOWNERS, ruleset/branch protection, or required-check "
                    "coverage is missing or unavailable."
                ),
                details=(
                    f"{ownership_path} does not declare a pattern covering: "
                    f"{', '.join(missing)}."
                ),
                affected_paths=(str(ownership_path),),
            )
        )
        return

    if _branch_protection_confirmed(live_github):
        controls["ghcp_codeowners"] = "pass"
        return

    # Static CODEOWNERS coverage is complete, but whether the branch is
    # actually protected on GitHub -- reviews required, required checks
    # enforced, admins included -- can never be proven by local files
    # alone; only live evidence can confirm it.
    controls["ghcp_codeowners"] = "not-verified"
    findings.append(
        Finding(
            finding_id="GHCP-002",
            status="not-verified",
            phase="pre-deploy",
            plane="change",
            reason_code="branch-protection-not-verified-statically",
            summary=(
                "CODEOWNERS, ruleset/branch protection, or required-check "
                "coverage is missing or unavailable."
            ),
            details=(
                f"{ownership_path} declares complete static coverage, but no "
                "live GitHub branch-protection evidence was supplied "
                "(`live_github` is None or incomplete); static files can "
                "never prove a branch protection rule or required-check "
                "list is actually enforced."
            ),
            affected_paths=(str(ownership_path),),
        )
    )


# --- GHCP-003: required CI CTK/application-probe/eval coverage ---------


def _assess_ci_probes(
    root: Path,
    assessments: Tuple[WorkflowAssessment, ...],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    pr_workflows = tuple(a for a in assessments if "pull_request" in a.triggers)
    if not pr_workflows:
        controls["ghcp_ci_probes"] = "must-fix"
        findings.append(
            _ci_probes_finding(
                "no-pull-request-triggered-ci",
                "No workflow runs required CI on `pull_request`, so no "
                "CTK/application-probe coverage can run before a change "
                "merges.",
                (),
            )
        )
        return

    offenders = tuple(a for a in pr_workflows if a.ci_probes == "must-fix")
    if offenders:
        controls["ghcp_ci_probes"] = "must-fix"
        findings.append(
            _ci_probes_finding(
                "missing-ctk-or-application-probe",
                "One or more pull_request-triggered workflows omit a CTK "
                "run, a governed application-probe run, or both.",
                tuple(str(a.path) for a in offenders),
            )
        )
        return

    eval_suite_files = _discover_eval_suite_files(root)
    if eval_suite_files:
        eval_runner_present = any(
            "evals" in _document_search_text(_load_workflow_document(a.path)).lower()
            for a in pr_workflows
        )
        if not eval_runner_present:
            controls["ghcp_ci_probes"] = "must-fix"
            findings.append(
                _ci_probes_finding(
                    "missing-eval-suite-runner",
                    "The repository ships an eval suite under `**/evals/**` "
                    "but no pull_request-triggered workflow runs it.",
                    tuple(str(path) for path in eval_suite_files),
                )
            )
            return

    controls["ghcp_ci_probes"] = "pass"


def _ci_probes_finding(reason_code: str, details: str, affected_paths: Tuple[str, ...]) -> Finding:
    return Finding(
        finding_id="GHCP-003",
        status="must-fix",
        phase="pre-deploy",
        plane="change",
        reason_code=reason_code,
        summary="Required CI omits CTK, application probes, or relevant evals.",
        details=details,
        affected_paths=affected_paths,
    )


# --- GHCP-004: SHA pinning + explicit least-privilege permissions ------


def _assess_actions_and_permissions(
    assessments: Tuple[WorkflowAssessment, ...],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    sha_offenders = tuple(a for a in assessments if a.sha_pins == "must-fix")
    permission_offenders = tuple(a for a in assessments if a.permissions == "must-fix")
    if not sha_offenders and not permission_offenders:
        controls["ghcp_pinned_least_privilege"] = "pass"
        return

    controls["ghcp_pinned_least_privilege"] = "must-fix"
    details_parts: List[str] = []
    affected_paths: List[str] = []
    if sha_offenders:
        refs = sorted({ref for a in sha_offenders for ref in a.sha_violations})
        details_parts.append(
            "Action reference(s) not pinned to an exact 40-character lowercase "
            f"commit SHA: {', '.join(refs)}."
        )
        affected_paths.extend(str(a.path) for a in sha_offenders)
    if permission_offenders:
        details_parts.append(
            "Workflow or job permissions are missing, implicit, or "
            "`write-all` (not explicit least privilege) in: "
            + ", ".join(str(a.path) for a in permission_offenders)
            + "."
        )
        affected_paths.extend(str(a.path) for a in permission_offenders)

    findings.append(
        Finding(
            finding_id="GHCP-004",
            status="must-fix",
            phase="pre-deploy",
            plane="change",
            reason_code="unpinned-action-or-excessive-permission",
            summary="Action SHA floats or workflow permission is excessive.",
            details=" ".join(details_parts),
            affected_paths=tuple(sorted(set(affected_paths))),
        )
    )


# --- GHCP-005: Azure OIDC/WIF login ------------------------------------


def _assess_oidc(
    assessments: Tuple[WorkflowAssessment, ...],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    offenders = tuple(a for a in assessments if a.oidc_wif == "must-fix")
    controls["ghcp_azure_oidc"] = "must-fix" if offenders else "pass"
    if not offenders:
        return
    findings.append(
        Finding(
            finding_id="GHCP-005",
            status="must-fix",
            phase="pre-deploy",
            plane="change",
            reason_code="secret-based-azure-login",
            summary="Azure deployment uses a long-lived secret instead of OIDC/WIF.",
            details=(
                "One or more `azure/login` steps read a client secret, "
                "password, publish profile, or service-principal secret, or "
                "are not backed by an explicit `permissions: id-token: write` "
                "grant, instead of authenticating via OpenID Connect / "
                "workload identity federation."
            ),
            affected_paths=tuple(str(a.path) for a in offenders),
        )
    )


# --- GHCP-006: build/test/deploy identity separation -------------------


def _assess_identity_separation(
    assessments: Tuple[WorkflowAssessment, ...],
    live_azure: Optional[Mapping],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    deploy_identities = {a.identity_ref for a in assessments if a.is_deploy and a.identity_ref}
    other_identities = {
        a.identity_ref for a in assessments if not a.is_deploy and a.identity_ref
    }

    if not deploy_identities and not other_identities:
        controls["ghcp_identity_separation"] = "not-applicable"
        return

    shared = deploy_identities & other_identities
    if shared:
        controls["ghcp_identity_separation"] = "must-fix"
        findings.append(
            Finding(
                finding_id="GHCP-006",
                status="must-fix",
                phase="pre-deploy",
                plane="change",
                reason_code="shared-identity",
                summary=(
                    "Build/test/deploy identities are shared, over-broad, or "
                    "not evidenced."
                ),
                details=(
                    "Deployment and test/build workflows authenticate as the "
                    f"same identity: {', '.join(sorted(shared))}."
                ),
            )
        )
        return

    if _distinct_identities_confirmed(live_azure):
        controls["ghcp_identity_separation"] = "pass"
        return

    controls["ghcp_identity_separation"] = "not-verified"
    findings.append(
        Finding(
            finding_id="GHCP-006",
            status="not-verified",
            phase="pre-deploy",
            plane="change",
            reason_code="identity-separation-not-verified-statically",
            summary=(
                "Build/test/deploy identities are shared, over-broad, or not "
                "evidenced."
            ),
            details=(
                "Static workflow files declare no overlapping identity "
                "reference, but no live Azure role-assignment evidence "
                "confirming at least two genuinely distinct principals was "
                "supplied (`live_azure` is None or incomplete)."
            ),
        )
    )
