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

# Rule 1 also rejects an explicit bypass command, not just an unprotected
# trigger: a commit-message skip marker GitHub Actions itself honors (which
# would silently stop a required check from ever running at all), a forced
# push over a protected ref, or an administrative override of branch
# protection/required reviews. Matched conservatively (favoring a false
# positive over a missed bypass) against actual `run:`/`if:` command text
# only -- never a step name or a YAML comment.
_BYPASS_MARKER_PATTERNS: Tuple[str, ...] = (
    r"\[skip ci\]",
    r"\[ci skip\]",
    r"\[skip actions\]",
    r"\[actions skip\]",
    r"\*\*\*no_ci\*\*\*",
    r"--force(?:-with-lease)?\b",
    r"\bgit\s+push\b[^\n]*(?:-f\b|--force)",
    r"\bbypass\b",
    r"\badmin[-_ ]?merge\b",
    r"--admin\b",
)
_BYPASS_MARKER_RE = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _BYPASS_MARKER_PATTERNS
)

# Rule 6 (permissions half): scopes that actually grant repository write
# access and therefore count toward the "too many write scopes" and
# "workflow can modify itself" excessive-permission heuristics.
# `id-token` is deliberately excluded -- rule 5 requires granting it for
# OIDC/WIF, so it is never itself evidence of excessive privilege.
_WRITE_RISK_SCOPES: Tuple[str, ...] = (
    "contents",
    "actions",
    "packages",
    "deployments",
    "issues",
    "pull-requests",
    "discussions",
    "pages",
    "repository-projects",
    "security-events",
    "statuses",
    "checks",
)
# More than this many simultaneous write scopes looks like `write-all`
# spelled out scope-by-scope rather than a genuinely narrowed grant.
_MAX_LEAST_PRIVILEGE_WRITE_SCOPES = 2

# Same shared exclusion rule `inputs.py`'s `test_and_report_files` category
# uses (mirrored, not imported, since this module never depends on
# `inputs.resolve_inputs`, which requires `specs/SPEC.md`): a hidden
# directory or vendored/scratch tree is never a trustworthy eval-suite
# source, regardless of which check is discovering it.
_EXCLUDED_DIR_NAMES: frozenset = frozenset(
    {"venv", "node_modules", "site-packages", "__pycache__", "build", "dist"}
)

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


def _run_command_texts(document: Mapping) -> Tuple[str, ...]:
    """Every step's actual ``run:`` shell command text -- never a step
    ``name``, a ``uses:`` reference, or a YAML comment, none of which are
    evidence that a command genuinely executes."""
    return tuple(
        step["run"]
        for step in _all_steps(document)
        if isinstance(step.get("run"), str)
    )


def _conditional_texts(document: Mapping) -> Tuple[str, ...]:
    """Every step- or job-level ``if:`` condition string in the document."""
    texts: List[str] = [
        str(step["if"]) for step in _all_steps(document) if isinstance(step.get("if"), str)
    ]
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if isinstance(job, Mapping) and isinstance(job.get("if"), str):
                texts.append(str(job["if"]))
    return tuple(texts)


def _has_bypass_commands(document: Mapping) -> bool:
    """True if a run command or an if-condition contains a change-plane
    bypass marker: a commit-message CI-skip marker GitHub Actions itself
    honors, a forced push, or an administrative/required-review override.
    Checked conservatively against real command/condition text only, never
    a step name or comment, so this can only under-detect a bypass phrased
    in some other way -- never flag a workflow for merely mentioning one.
    """
    joined = "\n".join(_run_command_texts(document) + _conditional_texts(document))
    return any(pattern.search(joined) for pattern in _BYPASS_MARKER_RE)


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
    key itself is entirely absent (caller decides what that means).

    A mapping is rejected, not just the literal ``write-all`` string, when
    it grants ``actions: write`` (lets a workflow modify workflows -- a
    supply-chain risk on its own) or grants ``write`` to more than
    :data:`_MAX_LEAST_PRIVILEGE_WRITE_SCOPES` scopes, since spelling out
    every scope as ``write`` individually is functionally equivalent to
    ``write-all`` and is rejected the same way.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() != "write-all"
    if isinstance(value, Mapping):
        write_scopes = {
            str(scope).strip().lower()
            for scope, granted in value.items()
            if str(scope).strip().lower() in _WRITE_RISK_SCOPES
            and str(granted).strip().lower() == "write"
        }
        if "actions" in write_scopes:
            return False
        if len(write_scopes) > _MAX_LEAST_PRIVILEGE_WRITE_SCOPES:
            return False
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
    if _has_bypass_commands(document):
        return "must-fix"
    return "pass"


# ---------------------------------------------------------------------------
# CI CTK / application-probe presence (rule 3, per-workflow half)
# ---------------------------------------------------------------------------


def _ci_probes_static_status(document: Mapping, triggers: Tuple[str, ...]) -> Status:
    if "pull_request" not in triggers:
        return "pass"  # rule 3 only binds required (PR-triggered) CI
    # Deliberately searched against the actual `run:` command text only --
    # never a step `name`, a `uses:` reference, or a YAML comment: a step
    # merely *named* "Run CTK" whose command never runs it, or a fixture
    # docstring mentioning "CTK" while describing why it is intentionally
    # absent, must never count as evidence that a step actually runs it.
    text = "\n".join(_run_command_texts(document))
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
    """Every file under a ``**/evals/**`` tree, mirroring `inputs.py`'s
    ``test_and_report_files`` exclusion rule: a hidden directory or a
    vendored/scratch tree (``node_modules``, ``build``, ...) is never a
    trustworthy eval-suite source."""
    if not root.is_dir():
        return ()
    files: List[Path] = []
    for candidate in root.glob("**/evals/**/*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if any(
            part.startswith(".") or part in _EXCLUDED_DIR_NAMES
            for part in relative.parts
        ):
            continue
        files.append(candidate)
    return tuple(sorted(files))


def _eval_suite_directories(root: Path, eval_files: Tuple[Path, ...]) -> Set[Path]:
    """Every repo-relative directory path (an ``evals`` directory itself, or
    a deeper ``.../evals`` directory) that actually contains a discovered
    eval-suite file, used to require an *exact* runner-command reference
    rather than a bare substring match against the word "evals" anywhere.
    """
    directories: Set[Path] = set()
    for file_path in eval_files:
        relative = file_path.relative_to(root)
        for index, part in enumerate(relative.parts):
            if part == "evals":
                directories.add(Path(*relative.parts[: index + 1]))
    return directories


def _path_token_pattern(relative_path: Path) -> "re.Pattern[str]":
    token = re.escape(relative_path.as_posix())
    return re.compile(rf"(?<![\w./-]){token}(?![\w./-])")


def _eval_runner_referenced(
    pr_workflows: Sequence["WorkflowAssessment"], eval_directories: Set[Path]
) -> bool:
    """True only if a pull_request-triggered workflow's actual ``run:``
    command text references one of ``eval_directories`` as a whole,
    boundary-delimited path token -- never merely because the word "evals"
    (or an unrelated path that happens to contain it) appears anywhere in
    the document.
    """
    if not eval_directories:
        return False
    run_text = "\n".join(
        text
        for assessment in pr_workflows
        for text in _run_command_texts(_load_workflow_document(assessment.path))
    )
    return any(
        _path_token_pattern(directory).search(run_text) for directory in eval_directories
    )


# ---------------------------------------------------------------------------
# Live-evidence helpers
# ---------------------------------------------------------------------------


def _required_check_job_names(assessments: Tuple["WorkflowAssessment", ...]) -> Set[str]:
    """Job ids/names of every pull_request-triggered workflow whose own
    static CTK/application-probe presence already passed -- the set of
    check names GitHub's required-status-check list would need to name for
    a branch-protection rule to actually be enforcing this repo's real
    gating CI, not merely *some* unrelated status check.
    """
    names: Set[str] = set()
    for assessment in assessments:
        if "pull_request" not in assessment.triggers:
            continue
        if assessment.ci_probes != "pass":
            continue
        document = _load_workflow_document(assessment.path)
        names.update(label.strip().lower() for label in _job_ids_and_names(document))
    return names


def _branch_protection_confirmed(
    live_github: Optional[Mapping], required_check_names: Set[str]
) -> bool:
    if not live_github:
        return False
    default_branch = live_github.get("default_branch")
    branch_protection = live_github.get("branch_protection")
    if not default_branch or not isinstance(branch_protection, Mapping):
        return False
    rule = branch_protection.get(default_branch)
    if not isinstance(rule, Mapping):
        return False
    if not bool(rule.get("required_pull_request_reviews")):
        return False
    required_status_checks = rule.get("required_status_checks")
    contexts = (
        required_status_checks.get("contexts")
        if isinstance(required_status_checks, Mapping)
        else required_status_checks
    )
    if not isinstance(contexts, Sequence) or isinstance(contexts, (str, bytes)):
        return False
    context_names = {str(context).strip().lower() for context in contexts}
    if not context_names:
        return False
    # A required-status-check list can name *some* check without naming the
    # one that actually runs this repo's CTK/application-probe CI; that
    # gap can never be resolved by inferring a `pass` -- it stays
    # not-verified unless a genuine gating job name is actually enforced.
    return bool(context_names & required_check_names)


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
    root = Path(root).resolve()
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
        workflow_evidence = _workflow_set_evidence(root, workflow_paths)
        if workflow_evidence is not None:
            evidence.append(workflow_evidence)

    findings.sort(key=lambda finding: finding.finding_id)
    return ChangePlaneResult(
        controls=controls,
        findings=tuple(findings),
        evidence=tuple(evidence),
    )


# --- read-only git metadata resolution (never a subprocess, never a
# fabricated placeholder) ------------------------------------------------


def _find_git_dir(root: Path) -> Optional[Path]:
    """Walk upward from ``root`` for a real ``.git`` directory, or a
    ``.git`` file pointing at a worktree/submodule's real gitdir. Purely a
    filesystem read -- this module never shells out to git."""
    start = root if root.is_dir() else root.parent
    for candidate in (start, *start.parents):
        git_path = candidate / ".git"
        if git_path.is_dir():
            return git_path
        if git_path.is_file():
            try:
                content = git_path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not content.startswith("gitdir:"):
                continue
            gitdir = Path(content.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (candidate / gitdir).resolve()
            if gitdir.is_dir():
                return gitdir
    return None


def _git_common_dir(git_dir: Path) -> Path:
    """The real, shared gitdir a linked worktree's ``config``, ``refs``, and
    ``packed-refs`` actually live in. A worktree's own gitdir (found by
    :func:`_find_git_dir`) holds only its own ``HEAD``/``index``; a
    ``commondir`` file there points at the main checkout's gitdir, which is
    where the origin remote and branch refs are actually recorded. A plain,
    non-worktree repository has no ``commondir`` file and is simply its own
    common dir."""
    commondir_path = git_dir / "commondir"
    if not commondir_path.is_file():
        return git_dir
    try:
        content = commondir_path.read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir
    if not content:
        return git_dir
    candidate = Path(content)
    if not candidate.is_absolute():
        candidate = (git_dir / candidate).resolve()
    return candidate if candidate.is_dir() else git_dir


_SSH_REMOTE_RE = re.compile(r"^git@[^:/]+:(?P<slug>.+?)(?:\.git)?/?$")
_HTTPS_REMOTE_RE = re.compile(r"^https?://[^/]+/(?P<slug>.+?)(?:\.git)?/?$")


def _resolve_repository_identifier(git_dir: Path) -> Optional[str]:
    """Parse the common gitdir's ``config``'s ``[remote "origin"]`` URL
    into an ``owner/repo``-style identifier, or ``None`` if there is no
    origin remote to read -- never a directory-name guess."""
    config_path = _git_common_dir(git_dir) / "config"
    if not config_path.is_file():
        return None
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    in_origin_remote = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin_remote = stripped.lower() == '[remote "origin"]'
            continue
        if not in_origin_remote or not stripped.lower().startswith("url"):
            continue
        _, _, value = stripped.partition("=")
        url = value.strip()
        for pattern in (_SSH_REMOTE_RE, _HTTPS_REMOTE_RE):
            match = pattern.match(url)
            if match:
                return match.group("slug")
    return None


def _resolve_commit_sha(git_dir: Path) -> Optional[str]:
    """Resolve ``HEAD`` (read from the worktree-local gitdir, since each
    linked worktree has its own) to a real 40-lowercase-hex commit SHA by
    reading loose or packed refs from the common gitdir -- never a
    fabricated all-zero value."""
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return None
    try:
        head_content = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if _SHA_PIN_RE.match(head_content):
        return head_content
    if not head_content.startswith("ref:"):
        return None
    ref = head_content.split(":", 1)[1].strip()
    common_dir = _git_common_dir(git_dir)
    ref_path = common_dir / ref
    if ref_path.is_file():
        try:
            sha = ref_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return sha if _SHA_PIN_RE.match(sha) else None
    packed_refs_path = common_dir / "packed-refs"
    if not packed_refs_path.is_file():
        return None
    try:
        packed_lines = packed_refs_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in packed_lines:
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return sha if _SHA_PIN_RE.match(sha) else None
    return None


def _workflow_set_evidence(
    root: Path, workflow_paths: Tuple[Path, ...]
) -> Optional[EvidenceRef]:
    git_dir = _find_git_dir(root)
    repository = _resolve_repository_identifier(git_dir) if git_dir else None
    source_commit = _resolve_commit_sha(git_dir) if git_dir else None
    # ``EvidenceRef.repository``/``source_commit`` are required, non-``None``
    # fields; when trustworthy values cannot be read straight from real,
    # on-disk git metadata this evidence entry is omitted entirely rather
    # than filled with a directory-name guess or an all-zero placeholder
    # SHA -- an assessor must never fabricate the evidence it reports.
    if repository is None or source_commit is None:
        return None
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
        repository=repository,
        source_commit=source_commit,
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

    if _branch_protection_confirmed(live_github, _required_check_job_names(assessments)):
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
        eval_directories = _eval_suite_directories(root, eval_suite_files)
        if not _eval_runner_referenced(pr_workflows, eval_directories):
            controls["ghcp_ci_probes"] = "must-fix"
            findings.append(
                _ci_probes_finding(
                    "missing-eval-suite-runner",
                    "The repository ships an eval suite under `**/evals/**` "
                    "but no pull_request-triggered workflow's run command "
                    "references its exact directory path.",
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
