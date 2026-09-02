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

import hashlib
import re
import struct
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
# only -- never a step name or a YAML comment. Each candidate marker is
# additionally checked for a *negation* cue, and for a detection/inspection
# context (a `grep`/`rg`/`awk` command merely searching for the marker
# rather than issuing it), immediately preceding it on the same line --
# see `_has_bypass_commands`. The generic standalone `--force`/`--admin`
# flags used by countless unrelated CLI tools are deliberately *not*
# matched on their own: only bound to the specific `git push`/`merge`
# command shape that actually bypasses this change plane.
_BYPASS_MARKER_PATTERNS: Tuple[str, ...] = (
    r"\[skip ci\]",
    r"\[ci skip\]",
    r"\[skip actions\]",
    r"\[actions skip\]",
    r"\*\*\*no_ci\*\*\*",
    r"\bgit\s+push\b[^\n]*(?:-f\b|--force(?:-with-lease)?\b)",
    r"\bbypass\b",
    r"\badmin[-_ ]?merge\b",
    r"\b(?:gh\s+pr\s+merge|pr\s+merge)\b[^\n]*--admin\b",
)
_BYPASS_MARKER_RE = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _BYPASS_MARKER_PATTERNS
)

# A negation cue appearing *before* a bypass marker on the same line (a
# policy comment, a lint check's own descriptive echo, ...) means the line
# is talking *about* the bypass, not issuing it -- e.g. "must never use
# git push --force" or "reviewers should not admin-merge".
_NEGATION_CUE_RE = re.compile(
    r"\b(?:not|never|don'?t|does\s?n'?t|won'?t|shouldn'?t|wouldn'?t|"
    r"can'?t|cannot|must\s+not|disallow(?:s|ed|ing)?|"
    r"prevent(?:s|ed|ing)?|block(?:s|ed|ing)?|reject(?:s|ed|ing)?|"
    r"forbid(?:s|den|ding)?|without)\b",
    re.IGNORECASE,
)

# A detection/inspection command searching *for* the marker text (to
# confirm its absence, most commonly) rather than a step that actually
# issues it -- e.g. `grep -q '[skip ci]' ... || echo clean`.
_DETECTION_CONTEXT_RE = re.compile(r"\b(?:grep|rg|ripgrep|awk)\b", re.IGNORECASE)

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

# Every location GitHub itself recognizes a CODEOWNERS file at.
_OWNERSHIP_FILENAMES: Tuple[str, ...] = (
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
)

# A CODEOWNERS pattern line establishes real ownership only when followed
# by at least one owner token that is actually shaped like a GitHub
# username/team (`@user`, `@org/team`) or an email address -- a bare
# pattern with no owner (or garbage after it) names no one responsible and
# confers no real coverage, however complete the pattern itself looks.
_CODEOWNERS_OWNER_TOKEN_RE = re.compile(
    r"^(?:@[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)?"
    r"|[^@\s]+@[^@\s]+\.[^@\s]+)$"
)

# Static bounds on the YAML this module will ever attempt to parse -- a
# malicious or merely corrupted workflow file must be converted into a
# `ChangePlaneError`/per-file finding, never allowed to hang or exhaust
# memory, regardless of how it is crafted.
_MAX_WORKFLOW_FILE_BYTES = 1_000_000  # 1 MB: far larger than any real workflow
_MAX_YAML_CONSTRUCTED_NODES = 20_000
_MAX_YAML_NESTING_DEPTH = 100

# Azure secret-credential shapes that can appear directly in a step's
# `run:` shell text or an `env:` block -- not only a `with:` input -- so
# rule 5's secret-credential scan is never confined to `azure/login`'s or
# a deploy action's own declared inputs. Matched against variable/flag
# *names*, never a value, so a finding can report *that* a secret-shaped
# credential is used without ever echoing the credential itself.
_SECRET_ENV_VAR_NAME_RE = re.compile(
    r"\b(?:AZURE|ARM)_(?:CLIENT_SECRET|PASSWORD|CREDENTIALS)\b", re.IGNORECASE
)
_AZ_LOGIN_SECRET_FLAG_RE = re.compile(
    r"\baz\s+login\b[^\n]*--(?:password|service-principal-secret)\b",
    re.IGNORECASE,
)
_PUBLISH_PROFILE_RUN_RE = re.compile(r"publish[-_]profile", re.IGNORECASE)


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


def _bounded_yaml_loader_class(yaml_module):
    """Build a `SafeLoader` subclass that bounds node count and nesting depth.

    Plain `SafeLoader` has no limit on how many nodes it will construct or
    how deeply it will recurse, so a crafted workflow file (a "billion
    laughs" alias-expansion bomb, or merely deep nesting) can exhaust
    memory or the call stack before ever reaching `_load_workflow_document`'s
    own callers. Every `construct_object` call -- including one triggered by
    resolving a YAML alias back to an already-defined anchor, which is
    exactly how alias-expansion amplification happens even though the
    anchor itself is only defined once -- counts against the node budget,
    and `compose_node`'s recursion is wrapped to enforce a depth budget.
    Either budget being exceeded raises `yaml.YAMLError`, the same failure
    type this loader's caller already translates into `ChangePlaneError`.
    """

    class _BoundedSafeLoader(yaml_module.SafeLoader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ghcp_node_count = 0
            self._ghcp_depth = 0

        def construct_object(self, node, deep=False):
            self._ghcp_node_count += 1
            if self._ghcp_node_count > _MAX_YAML_CONSTRUCTED_NODES:
                raise yaml_module.YAMLError(
                    "workflow YAML exceeds the maximum constructed-node bound"
                )
            return super().construct_object(node, deep=deep)

        def compose_node(self, parent, index):
            self._ghcp_depth += 1
            if self._ghcp_depth > _MAX_YAML_NESTING_DEPTH:
                raise yaml_module.YAMLError(
                    "workflow YAML exceeds the maximum nesting-depth bound"
                )
            try:
                return super().compose_node(parent, index)
            finally:
                self._ghcp_depth -= 1

    return _BoundedSafeLoader


def _load_workflow_document(path: Path) -> object:
    try:
        file_size = path.stat().st_size
    except OSError as error:
        raise ChangePlaneError(f"cannot stat workflow file {path}: {error}") from error
    if file_size > _MAX_WORKFLOW_FILE_BYTES:
        raise ChangePlaneError(
            f"workflow file {path} exceeds the maximum allowed size "
            f"({file_size} > {_MAX_WORKFLOW_FILE_BYTES} bytes)"
        )
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
        document = yaml.load(raw_text, Loader=_bounded_yaml_loader_class(yaml))
    except yaml.YAMLError as error:
        raise ChangePlaneError(f"invalid YAML in workflow file {path}: {error}") from error
    except RecursionError as error:  # pragma: no cover - defense in depth
        raise ChangePlaneError(
            f"workflow file {path} is nested too deeply to parse"
        ) from error
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


def _walk(node: object, _seen: Optional[Set[int]] = None):
    """Yield every mapping found anywhere in a nested document.

    Traverses the document as the *DAG* it can actually be -- a YAML
    anchor referenced by more than one alias parses to the identical
    Python object at every use site, not a distinct copy -- rather than
    as a tree: each container is visited at most once, keyed by object
    identity. This is both the semantically correct behavior for an
    aliased document (an aliased step block should not be double-counted
    just because two jobs reuse it) and a load-bearing safety property:
    building an aliased document is cheap thanks to PyYAML's own
    construction-time memoization, so `_load_workflow_document`'s node
    budget alone would not stop a subsequent *un*-memoized walk of the
    resulting object graph from costing exponential time in the alias
    nesting depth. Memoizing here caps this walk at the same constructed-
    node bound the loader already enforces.
    """
    if _seen is None:
        _seen = set()
    if isinstance(node, Mapping):
        if id(node) in _seen:
            return
        _seen.add(id(node))
        yield node
        for value in node.values():
            yield from _walk(value, _seen)
    elif isinstance(node, list):
        if id(node) in _seen:
            return
        _seen.add(id(node))
        for item in node:
            yield from _walk(item, _seen)


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

    Two further false-positive guards apply per match, scoped to the text
    preceding the marker on its own line: a *negation* cue (a policy
    comment or lint step's own echo describing the bypass in order to
    forbid it, e.g. "must never use git push --force") and a *detection*
    context (a `grep`/`rg`/`awk` command searching for the marker's
    literal text to confirm its absence, rather than a step issuing it)
    each mean the line is talking about the bypass, not performing it.
    """
    joined = "\n".join(_run_command_texts(document) + _conditional_texts(document))
    for pattern in _BYPASS_MARKER_RE:
        for match in pattern.finditer(joined):
            line_start = joined.rfind("\n", 0, match.start()) + 1
            prefix = joined[line_start : match.start()]
            if _NEGATION_CUE_RE.search(prefix) or _DETECTION_CONTEXT_RE.search(prefix):
                continue
            return True
    return False


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
    """Explicit, least-privilege permissions are required at the workflow
    level *and* independently on every job -- GitHub Actions lets an
    omitted job silently inherit the workflow-level default, so a sibling
    job's own explicit declaration is never evidence for a job that
    doesn't declare its own: an implicit inherited default is exactly the
    ambient-permission risk rule 6 exists to reject, even when some other
    job in the same file happens to be fully explicit.
    """
    workflow_level = _permissions_declared_and_least_privilege(document.get("permissions"))
    if workflow_level is not True:
        return "must-fix"

    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if not isinstance(job, Mapping):
                continue
            job_level = _permissions_declared_and_least_privilege(job.get("permissions"))
            if job_level is not True:
                return "must-fix"

    return "pass"


# ---------------------------------------------------------------------------
# Azure login / OIDC (rule 5)
# ---------------------------------------------------------------------------


def _has_secret_credential_input(step: Mapping) -> bool:
    """True if a step's ``with:`` block sets any key in
    :data:`_SECRET_LOGIN_KEYS` (``creds``, ``client-secret``, ``password``,
    ``publish-profile``, ...) -- a long-lived secret credential rather than
    OIDC/workload-identity-federation, regardless of which action reads
    it."""
    with_block = step.get("with")
    if not isinstance(with_block, Mapping):
        return False
    keys_lower = {str(key).strip().lower() for key in with_block.keys()}
    return bool(keys_lower & set(_SECRET_LOGIN_KEYS))


def _env_key_names(document: Mapping) -> Tuple[str, ...]:
    """Every key name declared in an ``env:`` block anywhere in the
    document -- workflow-level, job-level, or step-level -- checked only
    against variable *names*, never a value, so a secret-shaped credential
    can be reported without ever echoing the credential itself."""
    names: List[str] = []

    def _collect(env: object) -> None:
        if isinstance(env, Mapping):
            names.extend(str(key) for key in env.keys())

    _collect(document.get("env"))
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if not isinstance(job, Mapping):
                continue
            _collect(job.get("env"))
            steps = job.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, Mapping):
                        _collect(step.get("env"))
    return tuple(names)


def _has_secret_azure_run_or_env(document: Mapping) -> bool:
    """True if the workflow authenticates to Azure with a long-lived
    secret *outside* any action's own declared ``with:`` inputs: a step
    shelling out to ``az login`` with a raw ``--password``/
    ``--service-principal-secret`` flag, a raw ``publish-profile`` deploy
    command, or an ``env:`` block (at any scope) declaring a variable
    named like an Azure/ARM client secret, password, or credentials blob.
    Every check matches variable *names* or fixed command flags only --
    never a secret's actual value -- so this can report *that* a secret-
    shaped credential path exists without ever echoing the credential
    itself.
    """
    run_texts = _run_command_texts(document)
    if any(_AZ_LOGIN_SECRET_FLAG_RE.search(text) for text in run_texts):
        return True
    if any(_PUBLISH_PROFILE_RUN_RE.search(text) for text in run_texts):
        return True
    return any(_SECRET_ENV_VAR_NAME_RE.search(name) for name in _env_key_names(document))


def _job_steps(document: Mapping) -> List[Tuple[Mapping, Mapping]]:
    """Every ``(job, step)`` pair in the document, preserving which job
    each step belongs to. Required wherever a check depends on a step's
    job-scoped *effective* permissions: GitHub Actions permissions are not
    additive across jobs -- a job's own ``permissions:`` block, if
    present, completely replaces the workflow-level default for that job
    rather than merging with it, so which job a step lives in changes
    which permissions actually apply to it.
    """
    pairs: List[Tuple[Mapping, Mapping]] = []
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if not isinstance(job, Mapping):
                continue
            steps = job.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, Mapping):
                        pairs.append((job, step))
    return pairs


def _azure_login_job_steps(document: Mapping) -> List[Tuple[Mapping, Mapping]]:
    return [
        (job, step)
        for job, step in _job_steps(document)
        if str(step.get("uses", "")).split("@", 1)[0].strip().lower() == "azure/login"
    ]


def _azure_deploy_action_job_steps(document: Mapping) -> List[Tuple[Mapping, Mapping]]:
    """Every ``(job, step)`` pair whose step references one of the known
    Azure deployment actions (:data:`_DEPLOY_ACTION_MARKERS` --
    ``azure/webapps-deploy``, ``azure/functions-action``,
    ``azure/arm-deploy``, ...).

    These actions can authenticate directly with their own secret input
    (most commonly ``publish-profile`` or ``creds``) without any
    ``azure/login`` step ever appearing in the workflow at all, so rule
    5's secret-credential scan must inspect them too, not just
    ``azure/login``.
    """
    return [
        (job, step)
        for job, step in _job_steps(document)
        if str(step.get("uses", "")).split("@", 1)[0].strip().lower()
        in _DEPLOY_ACTION_MARKERS
    ]


def _effective_job_permissions(document: Mapping, job: Mapping) -> object:
    """A job's *effective* permissions: its own ``permissions:`` block if
    it declares one at all, otherwise the workflow-level default -- never
    a merge of the two, matching GitHub Actions' own override semantics.
    """
    job_permissions = job.get("permissions")
    if job_permissions is not None:
        return job_permissions
    return document.get("permissions")


def _job_grants_id_token_write(document: Mapping, job: Mapping) -> bool:
    value = _effective_job_permissions(document, job)
    return (
        isinstance(value, Mapping)
        and str(value.get("id-token", "")).strip().lower() == "write"
    )


def _oidc_status(
    document: Mapping,
    login_job_steps: Sequence[Tuple[Mapping, Mapping]],
    deploy_action_job_steps: Sequence[Tuple[Mapping, Mapping]],
) -> Status:
    # A raw `az login`/publish-profile secret path, or a secret-shaped env
    # var declared anywhere, is rejected regardless of which step or job
    # it lives in -- the secret is the change-plane risk, independent of
    # which action (if any) happens to be the one authenticating.
    if _has_secret_azure_run_or_env(document):
        return "must-fix"
    # A deployment action authenticating directly with its own secret
    # input (e.g. `azure/webapps-deploy`'s `publish-profile`) is rejected
    # even when no `azure/login` step exists anywhere in the workflow --
    # the secret is the change-plane risk, not which action happens to
    # read it.
    if any(_has_secret_credential_input(step) for _job, step in deploy_action_job_steps):
        return "must-fix"
    if not login_job_steps:
        return "pass"  # nothing else to assess: rule 5 is not-applicable, not a finding
    for job, step in login_job_steps:
        # `id-token: write` must be granted in *this* login step's own
        # job -- a sibling job granting it is not evidence this job's
        # `azure/login` step can actually mint an OIDC token, since job
        # permissions do not merge across jobs.
        if not _job_grants_id_token_write(document, job):
            return "must-fix"
        with_block = step.get("with")
        if not isinstance(with_block, Mapping):
            return "must-fix"
        if _has_secret_credential_input(step):
            return "must-fix"
        keys_lower = {str(key).strip().lower() for key in with_block.keys()}
        if "client-id" not in keys_lower or "tenant-id" not in keys_lower:
            return "must-fix"
    return "pass"


def _identity_ref(login_job_steps: Sequence[Tuple[Mapping, Mapping]]) -> Optional[str]:
    for _job, step in login_job_steps:
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


def _push_targets_branches(document: Mapping) -> bool:
    """True unless the ``push`` trigger is scoped to tags only.

    GitHub Actions treats a ``push`` trigger that filters on ``tags``/
    ``tags-ignore`` but declares no ``branches``/``branches-ignore`` filter
    as restricted to tag pushes alone -- it never fires for an ordinary
    branch push at all, so rule 1's "direct push to a protected branch"
    concern (GHCP-001) does not apply. Any other shape -- no filters at
    all, an explicit ``branches`` filter alongside (or instead of) a tags
    filter, or the bare/list ``on: push`` form -- can fire for a branch
    push and is treated as targeting branches.
    """
    triggers = document.get("on", document.get(True))
    push_value: object = None
    if isinstance(triggers, Mapping):
        push_value = triggers.get("push")
    elif triggers == "push":
        return True
    elif isinstance(triggers, Sequence) and not isinstance(triggers, str):
        return "push" in (str(item) for item in triggers)
    if push_value is None:
        return "push" in _trigger_names(document)
    if not isinstance(push_value, Mapping):
        return True  # `push:` with no filters (null/empty) fires for any branch
    has_tag_filter = "tags" in push_value or "tags-ignore" in push_value
    has_branch_filter = "branches" in push_value or "branches-ignore" in push_value
    return has_branch_filter or not has_tag_filter


def _pr_gate_status(document: Mapping, triggers: Tuple[str, ...], is_deploy: bool) -> Status:
    if "pull_request_target" in triggers and _has_untrusted_checkout(document):
        return "must-fix"
    if "push" in triggers and is_deploy and _push_targets_branches(document):
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
    login_job_steps = _azure_login_job_steps(document)
    deploy_action_job_steps = _azure_deploy_action_job_steps(document)
    return WorkflowAssessment(
        path=path,
        triggers=triggers,
        is_deploy=is_deploy,
        pr_gate=_pr_gate_status(document, triggers, is_deploy),
        permissions=_permissions_status(document),
        sha_pins=sha_pins,
        oidc_wif=_oidc_status(document, login_job_steps, deploy_action_job_steps),
        ci_probes=_ci_probes_static_status(document, triggers),
        identity_ref=_identity_ref(login_job_steps),
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
    """Every pattern in a CODEOWNERS file that actually names at least one
    real owner -- a ``@user``/``@org/team`` GitHub reference or an email
    address following the pattern on the same line. A pattern with no
    owner token (or only unrecognized text after it) names no one
    responsible and confers no real ownership coverage, however complete
    the pattern itself looks, so it is not counted.
    """
    patterns: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        pattern, owners = tokens[0], tokens[1:]
        if any(_CODEOWNERS_OWNER_TOKEN_RE.match(owner) for owner in owners):
            patterns.add(pattern)
    return patterns


def _codeowners_recursive_prefix(pattern: str) -> Optional[str]:
    """The directory this CODEOWNERS pattern recursively covers, or
    ``None`` if it is not a recursive-directory-style pattern at all (an
    exact file, or a single-``*`` one-level glob, neither of which reaches
    every file below it).

    A leading ``/`` is a cosmetic root anchor here -- both ``tests/**``
    and ``/tests/**`` return the same ``"tests"`` prefix, since our
    required patterns are always already repo-root-relative and GitHub's
    own CODEOWNERS matching anchors a pattern containing an internal
    slash to the repository root regardless of whether it is also
    prefixed with one. A bare ``*``/``**`` (with or without a leading
    slash) is the repository-wide catch-all and returns ``""``, an
    ancestor of every path.
    """
    normalized = pattern.strip().lstrip("/")
    if normalized in ("", "*", "**"):
        return ""
    if normalized.endswith("/**"):
        return normalized[: -len("/**")]
    if normalized.endswith("/"):
        return normalized.rstrip("/")
    return None


def _is_ancestor_or_equal(ancestor: str, path: str) -> bool:
    if ancestor == "":
        return True  # the repository root is an ancestor of every path
    return path == ancestor or path.startswith(ancestor + "/")


def _codeowners_pattern_covers(declared_pattern: str, requirement: str) -> bool:
    """True if a single declared CODEOWNERS pattern covers every file the
    ``requirement`` pattern would need covered.

    Recognizes a repository-wide catch-all (``*``/``**``), a broader
    ancestor directory glob covering a narrower required one (``src/**``
    covers ``src/governance/**``; ``tests/**`` covers the two explicit
    ``tests/governed-actions-*.json`` file requirements), and a leading
    ``/`` root anchor as equivalent to no leading slash at all -- never
    only literal string equality. A required *recursive* directory
    (``tests/**``) is only ever fully covered by another recursive glob
    whose own directory is that directory or an ancestor of it; a
    single-level ``tests/*`` glob does not reach a nested file and is
    correctly never treated as covering it.
    """
    declared_prefix = _codeowners_recursive_prefix(declared_pattern)
    required_prefix = _codeowners_recursive_prefix(requirement)

    if required_prefix is not None:
        return declared_prefix is not None and _is_ancestor_or_equal(
            declared_prefix, required_prefix
        )

    # The requirement is an exact file path: covered by the identical
    # literal pattern, or by a recursive directory glob that is an
    # ancestor of it.
    target = requirement.strip().lstrip("/")
    if declared_prefix is not None:
        return _is_ancestor_or_equal(declared_prefix, target)
    return declared_pattern.strip().lstrip("/") == target


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


def _rel(root: Path, path: object) -> str:
    """A finding's ``affected_paths`` entry as a repository-relative,
    POSIX-style path -- never the absolute filesystem path this module
    happened to read the file from, which would leak the assessment
    host's own directory layout into evidence meant to describe the
    repository itself. Falls back to the path as given if it does not
    actually resolve under ``root`` (should not happen for any path this
    module discovers itself, but never worth raising over)."""
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def _reject_workflow_symlink_escape(root: Path, path: Path) -> None:
    """Reject a workflow file that is itself a symlink, or that resolves
    -- following any symlinked ancestor directory -- outside ``root``.

    A symlinked workflow file could otherwise be used to make this module
    read and hash an arbitrary file elsewhere on the host under the guise
    of a workflow. Deliberately conservative: *any* symlink is rejected,
    even one that would currently resolve to stay within ``root``, since
    a symlink's target can change between this check and any later read
    of it (a classic time-of-check/time-of-use gap).
    """
    if path.is_symlink():
        raise ChangePlaneError(f"workflow file {path} is a symlink, not a real file")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ChangePlaneError(f"cannot resolve workflow file {path}: {error}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ChangePlaneError(
            f"workflow file {path} resolves outside the assessment root"
        ) from error


def _unreadable_workflow_assessment(path: Path, reason: str) -> WorkflowAssessment:
    """A conservative, "must-fix everything" stand-in for a workflow file
    this module could not safely read, parse, or bound at all (oversized,
    an alias/nesting bomb, a symlink escape, malformed YAML, ...).

    Never introduces a new finding id: it surfaces through the exact same
    GHCP-001..GHCP-006 offender scans every other workflow's assessment
    does, since a file this module cannot safely inspect can never be
    treated as passing any check it feeds. ``triggers=()`` so it is never
    counted as the passing pull_request-triggered workflow rule 3
    requires; ``sha_violations`` still names *why* it could not be
    assessed, surfacing the failure's cause in GHCP-004's reported detail
    text.
    """
    return WorkflowAssessment(
        path=path,
        triggers=(),
        is_deploy=False,
        pr_gate="must-fix",
        permissions="must-fix",
        sha_pins="must-fix",
        oidc_wif="must-fix",
        ci_probes="must-fix",
        identity_ref=None,
        sha_violations=(f"{path.name}: {reason}",),
    )


def _assess_workflow_or_flag(root: Path, path: Path) -> Tuple[WorkflowAssessment, bool]:
    """Assess one workflow file, converting *any* failure to safely
    reject, read, or parse it into a conservative "must-fix everything"
    stand-in assessment rather than letting it crash the entire
    repository assessment -- one bad workflow file must never erase every
    other workflow's real results.

    Returns ``(assessment, safe_to_hash)``: ``safe_to_hash`` is ``False``
    for a file this module rejected before or during parsing, so it is
    excluded from the workflow-set evidence hash rather than
    re-attempting to read it there too.
    """
    try:
        _reject_workflow_symlink_escape(root, path)
        return assess_workflow(path), True
    except ChangePlaneError as error:
        return _unreadable_workflow_assessment(path, str(error)), False


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

    A workflow file this module cannot safely read, parse, or bound
    (oversized, a YAML alias/nesting bomb, a symlink escaping the
    repository, malformed YAML, ...) never raises out of this function
    and never erases the rest of the assessment: it is converted into a
    conservative "must-fix everything" stand-in that still surfaces
    through GHCP-001..GHCP-006 like any other failing workflow, while
    every other discovered workflow is still assessed and reported
    normally.
    """
    root = Path(root).resolve()
    discovered_paths = _discover_workflow_files(root)
    assessments_list: List[WorkflowAssessment] = []
    safe_workflow_paths: List[Path] = []
    for path in discovered_paths:
        assessment, safe_to_hash = _assess_workflow_or_flag(root, path)
        assessments_list.append(assessment)
        if safe_to_hash:
            safe_workflow_paths.append(path)
    assessments = tuple(assessments_list)

    findings: List[Finding] = []
    evidence: List[EvidenceRef] = []
    controls: Dict[str, "Status | bool"] = {}

    _assess_pr_gate(root, assessments, findings, controls)
    _assess_codeowners(root, assessments, live_github, findings, controls)
    _assess_ci_probes(root, assessments, findings, controls)
    _assess_actions_and_permissions(root, assessments, findings, controls)
    _assess_oidc(root, assessments, findings, controls)
    _assess_identity_separation(assessments, live_azure, findings, controls)

    # Rule 7: this plane never claims to intercept GitHub Copilot's own
    # internal reasoning/tool-calling loop -- only the PR/CI/deployment
    # supply chain a change travels through. Set unconditionally.
    controls["ghcp_internal_loop_intercepted"] = False

    if safe_workflow_paths:
        workflow_evidence = _workflow_set_evidence(root, tuple(safe_workflow_paths))
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
    """A real ``.git`` directory (or a ``.git`` file pointing at a
    worktree/submodule's real gitdir) belonging to ``root`` itself -- or
    ``None`` if ``root`` is not itself a git checkout.

    Deliberately checks only ``root``, never an ancestor directory: a
    target that happens to live *inside* some unrelated outer git
    repository (for example a bare fixture directory nested under this
    project's own checkout) is not that outer repository, and attributing
    its provenance to the outer repo's remote/commit would be exactly the
    kind of fabricated evidence this module must never produce. Purely a
    filesystem read -- this module never shells out to git.
    """
    if not root.is_dir():
        return None
    git_path = root / ".git"
    if git_path.is_dir():
        return git_path
    if git_path.is_file():
        try:
            content = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not content.startswith("gitdir:"):
            return None
        gitdir = Path(content.split(":", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = (root / gitdir).resolve()
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


def _decode_git_index_varint(data: bytes, offset: int) -> Tuple[int, int]:
    """Decode one of git index-format-4's path-compression variable-width
    integers starting at ``offset``, returning ``(value, next_offset)``.
    Uses git's own MSB-continuation encoding (each continuation byte adds
    one before shifting in the next 7 bits) -- not standard LEB128 -- per
    ``Documentation/technical/index-format.txt``.
    """
    byte = data[offset]
    offset += 1
    value = byte & 0x7F
    while byte & 0x80:
        byte = data[offset]
        offset += 1
        value = ((value + 1) << 7) + (byte & 0x7F)
    return value, offset


def _read_git_index_entries(git_dir: Path) -> Optional[Dict[str, Tuple[int, str]]]:
    """Read every staged file's ``(size, blob sha1)`` from a real,
    on-disk ``.git/index``, keyed by its repository-relative POSIX path --
    purely a binary-format read, never a ``git`` subprocess.

    Supports index format versions 2, 3, and 4. Version 4's path-
    compression scheme is fully decoded rather than treated as
    unparseable, since it has been git's own default for years and
    refusing to parse it would make dirty-working-tree detection inert
    in most real repositories. Returns ``None`` if the index cannot be
    read or does not parse as a recognized, well-formed index -- callers
    must treat that exactly like an unconfirmable clean state, never like
    an empty repository.
    """
    index_path = git_dir / "index"
    if not index_path.is_file():
        return None
    try:
        data = index_path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"DIRC":
        return None
    try:
        version, entry_count = struct.unpack(">II", data[4:12])
        if version not in (2, 3, 4):
            return None
        entries: Dict[str, Tuple[int, str]] = {}
        offset = 12
        previous_path = ""
        for _ in range(entry_count):
            entry_start = offset
            fields = struct.unpack(">10I", data[offset : offset + 40])
            offset += 40
            sha1_hex = data[offset : offset + 20].hex()
            offset += 20
            flags = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2
            if flags & 0x4000 and version >= 3:
                offset += 2  # extended flags -- not needed for a dirty check
            if version >= 4:
                strip_length, offset = _decode_git_index_varint(data, offset)
                nul_index = data.index(b"\x00", offset)
                suffix = data[offset:nul_index].decode("utf-8")
                offset = nul_index + 1
                path = previous_path[: len(previous_path) - strip_length] + suffix
                previous_path = path
            else:
                nul_index = data.index(b"\x00", offset)
                path = data[offset:nul_index].decode("utf-8")
                entry_length = nul_index - entry_start + 1
                padding = (8 - (entry_length % 8)) % 8
                offset = nul_index + 1 + padding
            entries[path] = (fields[9], sha1_hex)
    except (struct.error, IndexError, UnicodeDecodeError, ValueError):
        return None
    return entries


def _git_blob_sha1(data: bytes) -> str:
    """The git blob object id git itself would assign to ``data`` --
    ``sha1("blob {len}\\0" + data)`` -- computed only to compare against
    the index's own recorded blob id for a dirty-working-tree check, not
    used as evidence of anything beyond that comparison."""
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def _workflow_set_is_clean(
    git_dir: Path, root: Path, workflow_paths: Tuple[Path, ...]
) -> bool:
    """True only if every workflow file's on-disk bytes match the exact
    blob git's own index has staged for it -- i.e. the working tree is not
    dirty with respect to these specific files.

    Any index-parse failure, a missing index entry, or a content mismatch
    is treated as "not confirmed clean", never as "confirmed clean": this
    check only ever makes evidence *more* conservative, never fabricates a
    clean result it cannot actually verify. This intentionally only
    catches *unstaged* working-tree-vs-index drift, not a staged-but-
    uncommitted index-vs-HEAD difference, which would require parsing
    commit/tree objects.
    """
    entries = _read_git_index_entries(git_dir)
    if entries is None:
        return False
    for path in workflow_paths:
        entry = entries.get(path.relative_to(root).as_posix())
        if entry is None:
            return False
        _recorded_size, recorded_sha1 = entry
        try:
            data = path.read_bytes()
        except OSError:
            return False
        if _git_blob_sha1(data) != recorded_sha1:
            return False
    return True


def _workflow_set_evidence(
    root: Path, workflow_paths: Tuple[Path, ...]
) -> Optional[EvidenceRef]:
    git_dir = _find_git_dir(root)
    repository = _resolve_repository_identifier(git_dir) if git_dir else None
    source_commit = _resolve_commit_sha(git_dir) if git_dir else None
    # ``EvidenceRef.repository``/``source_commit`` are required, non-``None``
    # fields; when trustworthy values cannot be read straight from real,
    # on-disk git metadata -- including when the working tree is dirty
    # with respect to the very files being hashed, since pairing
    # `source_commit` with a hash of *uncommitted* bytes would misrepresent
    # what is actually recorded at that commit -- this evidence entry is
    # omitted entirely rather than filled with a directory-name guess, an
    # all-zero placeholder SHA, or a commit reference the on-disk bytes
    # don't actually match. An assessor must never fabricate the evidence
    # it reports.
    if git_dir is None or repository is None or source_commit is None:
        return None
    if not _workflow_set_is_clean(git_dir, root, workflow_paths):
        return None
    try:
        hashed = canonical.hash_files(root, workflow_paths)
    except canonical.CanonicalizationError:
        return None
    return EvidenceRef(
        evidence_id="ghcp-workflows",
        kind="file-set",
        # Joined with `\n`, not `/`: the separator must never collide with
        # a character a real repository-relative path can itself contain,
        # or two joined paths (`"a/b.yml"`, `"c.yml"`) would be
        # indistinguishable from a single nested one (`"a/b.yml/c.yml"`).
        source="\n".join(
            sorted(path.relative_to(root).as_posix() for path in workflow_paths)
        ),
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
    root: Path,
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
            affected_paths=tuple(_rel(root, a.path) for a in offenders),
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
                    "No `CODEOWNERS`, `.github/CODEOWNERS`, or `docs/CODEOWNERS` "
                    "file was found. A file with any other name (for example "
                    "`CODEOWNERS.absent`) is never treated as ownership "
                    "evidence, however complete its declared patterns look."
                ),
            )
        )
        return

    declared = _parse_codeowners_patterns(ownership_path)
    missing = tuple(
        requirement
        for requirement in _REQUIRED_CODEOWNERS_PATTERNS
        if not any(
            _codeowners_pattern_covers(pattern, requirement) for pattern in declared
        )
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
                    f"{ownership_path} does not declare an owned pattern "
                    f"covering: {', '.join(missing)}."
                ),
                affected_paths=(_rel(root, ownership_path),),
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
            affected_paths=(_rel(root, ownership_path),),
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

    eval_suite_files = _discover_eval_suite_files(root)
    eval_directories = (
        _eval_suite_directories(root, eval_suite_files) if eval_suite_files else set()
    )

    def _satisfies_required_ci(assessment: "WorkflowAssessment") -> bool:
        if assessment.ci_probes != "pass":
            return False
        if not eval_directories:
            return True
        return _eval_runner_referenced((assessment,), eval_directories)

    # Rule 3 requires that *at least one* pull_request-triggered workflow
    # runs the full required CI -- CTK, an application probe, and (if the
    # repo ships one) the eval suite's own exact runner command -- not
    # that *every* PR workflow does. A repo can legitimately run other,
    # benign PR-triggered workflows (linting, docs previews, ...)
    # alongside its real gating CI; those must never fail this check on
    # their own as long as some workflow actually gates the change.
    if any(_satisfies_required_ci(a) for a in pr_workflows):
        controls["ghcp_ci_probes"] = "pass"
        return

    controls["ghcp_ci_probes"] = "must-fix"
    ctk_probe_offenders = tuple(a for a in pr_workflows if a.ci_probes == "must-fix")
    if len(ctk_probe_offenders) == len(pr_workflows):
        findings.append(
            _ci_probes_finding(
                "missing-ctk-or-application-probe",
                "No pull_request-triggered workflow runs both a CTK and a "
                "governed application-probe.",
                tuple(_rel(root, a.path) for a in pr_workflows),
            )
        )
        return

    findings.append(
        _ci_probes_finding(
            "missing-eval-suite-runner",
            "The repository ships an eval suite under `**/evals/**` but no "
            "pull_request-triggered workflow that runs CTK and an "
            "application probe also references its exact directory path.",
            tuple(_rel(root, path) for path in eval_suite_files),
        )
    )


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
    root: Path,
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
        affected_paths.extend(_rel(root, a.path) for a in sha_offenders)
    if permission_offenders:
        details_parts.append(
            "Workflow or job permissions are missing, implicit, or "
            "`write-all` (not explicit least privilege) in: "
            + ", ".join(_rel(root, a.path) for a in permission_offenders)
            + "."
        )
        affected_paths.extend(_rel(root, a.path) for a in permission_offenders)

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
    root: Path,
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
                "One or more `azure/login` steps, or Azure deployment "
                "action steps (`azure/webapps-deploy`, `azure/functions-"
                "action`, `azure/arm-deploy`, ...), read a client secret, "
                "password, publish profile, or service-principal secret "
                "-- as an input, a `run:` command flag, or an `env:` "
                "variable name -- or an `azure/login` step is not backed "
                "by an explicit `permissions: id-token: write` grant in "
                "that same job, instead of authenticating via OpenID "
                "Connect / workload identity federation."
            ),
            affected_paths=tuple(_rel(root, a.path) for a in offenders),
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
