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
import zlib
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

# A workflow that runs one of these commands directly (via the Azure/azd
# CLI in a `run:` step) is a deployment just as much as one that uses a
# marketplace deploy action -- rule 1 (GHCP-001) must classify it as
# `is_deploy` regardless of which job or step happens to run it, and
# regardless of what that job is named: a job named "release" or "ship"
# running `az webapp deploy` is still a deploy for this purpose.
#
# Deliberately excludes a bare `az group create` (or any other resource-
# management command without a deployment-shaped verb attached to a
# deploy-relevant resource type): creating an empty resource group is not
# itself a deployment, and treating it as one would be a false positive
# this module must not produce. `az deployment ... create` (ARM/Bicep
# deployments) is still covered via the `deployment` marker.
_DEPLOY_RUN_COMMAND_RE = re.compile(
    r"\baz\s+(?:webapp|functionapp|containerapp|staticwebapp|aks|acr|"
    r"deployment)\b[^\n]*\b(?:deploy(?:ment)?|up|create|update)\b"
    r"|\bazd\s+(?:deploy|up)\b",
    re.IGNORECASE,
)

_SECRET_LOGIN_KEYS: Tuple[str, ...] = (
    "creds",
    "client-secret",
    "password",
    "publish-profile",
)

# Every shape a `pull_request_target`-triggered checkout's `ref:` *or*
# `repository:` input can take that actually resolves to the pull
# request's own, potentially attacker-controlled head content or head
# repository fork rather than the trusted base branch/repository: the
# full `github.event.pull_request.head` object path (covers its `.ref`,
# `.sha`, `.repo.full_name`, `.repo.clone_url`, ... fields via
# substring -- an attacker's own fork repository is exactly as
# untrusted as their own head ref/sha), the `github.head_ref` shorthand
# context variable (only ever populated for pull_request/
# pull_request_target events, and always attacker-controlled), and a
# literal `refs/pull/...` ref (the fork PR's own ref namespace, whether
# `/head` or `/merge`). Checking out the trusted base ref from an
# attacker-controlled `repository:` fork (or vice versa) is just as
# untrusted as either alone -- both the ref *and* the repository must
# stay on the trusted base for a `pull_request_target` checkout to be
# safe.
_UNTRUSTED_CHECKOUT_REF_MARKERS: Tuple[str, ...] = (
    "github.event.pull_request.head",
    "github.head_ref",
    "refs/pull/",
)

# A raw `git fetch`/`checkout`/`clone`/`pull` command run directly in a
# `run:` step is an "equivalent checkout mechanism" to `actions/
# checkout`'s own `ref:`/`repository:` inputs -- it can resolve exactly
# the same attacker-controlled head content or fork repository without
# ever going through the checkout action at all, and rule 1 must catch
# it just as conservatively.
_UNTRUSTED_CHECKOUT_RUN_COMMAND_RE = re.compile(
    r"\bgit\s+(?:fetch|checkout|clone|pull)\b", re.IGNORECASE
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

# Every location GitHub itself recognizes a CODEOWNERS file at, in
# GitHub's own precedence order: ``.github/CODEOWNERS`` is consulted
# first, then the repository root ``CODEOWNERS``, then ``docs/
# CODEOWNERS`` -- the first one present is the one GitHub actually
# uses, and `_find_ownership_file` must check them in this exact order.
_OWNERSHIP_FILENAMES: Tuple[str, ...] = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
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
#
# Beyond the `AZURE_`/`ARM_`-prefixed names, also flag the bare canonical
# names a service-principal secret is conventionally stored under
# regardless of vendor prefix -- `SP_PASSWORD`, `CLIENT_SECRET`, and
# `SERVICE_PRINCIPAL_SECRET` -- but only as a *whole* env var name (both
# `\b` boundaries anchor against the surrounding underscores too, since
# `_` is a word character): this catches the exact canonical key without
# widening into an unrelated name that merely contains one of these
# words as a sub-part of a longer identifier.
_SECRET_ENV_VAR_NAME_RE = re.compile(
    r"\b(?:(?:AZURE|ARM)_(?:CLIENT_SECRET|PASSWORD|CREDENTIALS)"
    r"|SP_PASSWORD|CLIENT_SECRET|SERVICE_PRINCIPAL_SECRET)\b",
    re.IGNORECASE,
)
# `az login --service-principal ... --password SECRET` and its exact
# short-flag equivalent `az login --service-principal ... -p SECRET` are
# both the same long-lived-secret login path -- the short `-p` form is
# just as canonical as the long flag and must not be missed. `-p` is
# matched only as its own standalone token (not preceded or followed by
# a word/hyphen character) so this never fires on an unrelated long
# flag that merely contains "-p" as a substring (`--param`, `--profile`,
# `-profile`, ...). The scan for either flag form is confined to the
# same shell command as `az login` itself -- it stops at the next
# `&&`/`;`/`|` command separator -- so a `-p` (or `--password`) flag
# belonging to a different, chained command on the same `run:` line
# (`az login --identity && curl -o out -p file`) is never misread as
# `az login`'s own secret flag.
_AZ_LOGIN_SECRET_FLAG_RE = re.compile(
    r"\baz\s+login\b(?:(?!&&|;|\||\n).)*?"
    r"(?:--(?:password|service-principal-secret)\b|(?<![-\w])-p(?![-\w]))",
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
    identity_refs: Tuple[str, ...]
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
    shelling out to ``az login`` with a raw ``--password``/``-p``/
    ``--service-principal-secret`` flag, a raw ``publish-profile`` deploy
    command, or an ``env:`` block (at any scope) declaring a variable
    named like an Azure/ARM client secret, password, or credentials blob
    (or the bare canonical names ``SP_PASSWORD``, ``CLIENT_SECRET``,
    ``SERVICE_PRINCIPAL_SECRET``).
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


def _has_azure_deploy_evidence(
    document: Mapping, deploy_action_job_steps: Sequence[Tuple[Mapping, Mapping]]
) -> bool:
    """True only when this workflow is specifically an *Azure* deployment
    -- a known Azure deploy-action step, or a raw `az`/`azd` CLI deploy
    command run directly -- never merely a job whose id/name happens to
    say "deploy" (which `_is_deploy_workflow`'s generic heuristic also
    matches, including for entirely non-Azure targets). Rule 5's "no
    login evidence" downgrade must fire only for a workflow this module
    can actually tell is deploying to Azure; a job named "deploy" that
    ships to some unrelated platform is not itself evidence of anything
    OIDC/WIF-related.
    """
    if deploy_action_job_steps:
        return True
    run_text = "\n".join(_run_command_texts(document))
    return bool(_DEPLOY_RUN_COMMAND_RE.search(run_text))


def _oidc_status(
    document: Mapping,
    login_job_steps: Sequence[Tuple[Mapping, Mapping]],
    deploy_action_job_steps: Sequence[Tuple[Mapping, Mapping]],
    azure_deploy_evidence: bool,
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
        if azure_deploy_evidence:
            # This workflow demonstrably deploys to Azure -- via a known
            # deploy action or a raw az/azd CLI command -- yet declares no
            # visible `azure/login` step confirming *how* it authenticates
            # at all. Absence of a secret-shaped credential is not itself
            # proof of OIDC/WIF: it is equally consistent with a
            # federated identity this module simply cannot see evidence
            # of here, or a login step this static scan missed entirely.
            # Either way this can never be inferred as a `pass`.
            return "not-verified"
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


_SAFE_IDENTITY_REF_RE = re.compile(r"^\$\{\{.*\}\}$", re.DOTALL)


def _redact_identity_ref(value: str) -> str:
    """A workflow's own literal ``client-id``/``creds`` text, safe to
    retain and render anywhere in this module's evidence only when it
    is itself a ``${{ ... }}`` expression -- almost always
    ``secrets.*``, but also a safe context/env reference -- never an
    actual inline credential value. Rule 6 must never retain or render
    an inline (non-expression) value: it is replaced here with a short,
    non-reversible fingerprint, so two occurrences of the very same
    inline value can still be recognized as equal across a report
    without the underlying credential ever appearing in any finding,
    log, or report.
    """
    if _SAFE_IDENTITY_REF_RE.match(value.strip()):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<inline-identity-value-redacted:sha256:{digest}>"


def _identity_refs(login_job_steps: Sequence[Tuple[Mapping, Mapping]]) -> Tuple[str, ...]:
    """Every distinct client-id/creds reference from *every* ``azure/
    login`` step in a workflow -- not merely its first one. A workflow
    can authenticate as more than one identity (multiple jobs, or
    multiple login steps within one job), and rule 6's identity-
    separation check must inspect all of them, not just the first
    login step it happens to encounter.

    A value that is not itself a ``${{ ... }}`` expression -- an inline
    credential a developer mistakenly hardcoded instead of referencing
    a secret -- is redacted before it is ever returned: this is the
    single point every identity-separation finding's text is ultimately
    built from, so redacting here keeps an inline credential from ever
    being retained or rendered anywhere downstream.
    """
    refs: List[str] = []
    for _job, step in login_job_steps:
        with_block = step.get("with")
        if not isinstance(with_block, Mapping):
            continue
        for key in ("client-id", "creds"):
            value = with_block.get(key)
            if value:
                refs.append(_redact_identity_ref(str(value)))
    seen: Set[str] = set()
    unique: List[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return tuple(unique)


# ---------------------------------------------------------------------------
# Deploy classification and PR-only gating (rule 1)
# ---------------------------------------------------------------------------


def _is_deploy_workflow(document: Mapping) -> bool:
    """True if this workflow deploys -- via a known marketplace deploy
    action, a raw Azure/azd CLI deploy command run directly in a `run:`
    step, or a job id/name that says so -- classified regardless of
    which job any of this lives in or what that job happens to be
    named: a job named "release" running a bare `az webapp deploy`
    command is still a deploy for rule 1's purposes.
    """
    uses_refs = [ref.split("@", 1)[0].strip().lower() for ref in _uses_refs(document)]
    if any(marker in ref for ref in uses_refs for marker in _DEPLOY_ACTION_MARKERS):
        return True
    run_text = "\n".join(_run_command_texts(document))
    if _DEPLOY_RUN_COMMAND_RE.search(run_text):
        return True
    labels = [label.lower() for label in _job_ids_and_names(document)]
    return any("deploy" in label for label in labels)


def _has_untrusted_checkout(document: Mapping) -> bool:
    """True if any step -- an `actions/checkout` step's own `ref:` *or*
    `repository:` input, or a raw `git fetch`/`checkout`/`clone`/`pull`
    command run directly (an equivalent checkout mechanism) -- resolves
    to the pull request's own, potentially attacker-controlled head
    content or head repository fork, rather than staying on the
    trusted base branch *and* base repository. A `pull_request_target`
    checkout is only ever safe when *both* stay trusted: an
    attacker-controlled `repository:` fork checked out at an
    otherwise-trusted-looking `ref:` is just as dangerous as an
    attacker-controlled `ref:` on the trusted repository, since either
    one alone hands `pull_request_target`'s elevated permissions/
    secrets to content the pull request's author actually controls.
    """
    for step in _all_steps(document):
        uses = str(step.get("uses", "")).split("@", 1)[0].strip().lower()
        if uses == "actions/checkout":
            with_block = step.get("with")
            if isinstance(with_block, Mapping):
                for key in ("ref", "repository"):
                    value = str(with_block.get(key, ""))
                    if any(
                        marker in value for marker in _UNTRUSTED_CHECKOUT_REF_MARKERS
                    ):
                        return True
        run_text = step.get("run")
        if isinstance(run_text, str) and _UNTRUSTED_CHECKOUT_RUN_COMMAND_RE.search(
            run_text
        ):
            if any(marker in run_text for marker in _UNTRUSTED_CHECKOUT_REF_MARKERS):
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

# A line that only *echoes*, *prints*, or heredoc-emits text (rather than
# actually invoking a real command) is never evidence that whatever
# marker it happens to contain was genuinely run -- a step faking CTK/
# application-probe/eval-runner evidence with a bare `echo "ctk
# application-probe"` line must not satisfy rule 3.
_INERT_RUN_LINE_RE = re.compile(r"^(?:echo\b|printf\b|print\s*\()", re.IGNORECASE)


def _is_statically_disabled(step: Mapping, job: Optional[Mapping]) -> bool:
    """True only if a step's own `if:` (or its containing job's `if:`) is
    a literal, statically-false condition -- YAML's bare `false`/`0`, or
    an equivalent quoted string -- never a guess about a dynamic
    expression this module cannot evaluate. A step or job with no `if:`
    key at all, or one whose condition depends on runtime context this
    module cannot resolve, is never treated as disabled: only a
    condition that can *never* evaluate true, however GitHub actually
    runs it, is excluded.
    """
    candidates: List[object] = [step.get("if")]
    if isinstance(job, Mapping):
        candidates.append(job.get("if"))
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, bool):
            if candidate is False:
                return True
            continue
        text = str(candidate).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
            text = text[1:-1].strip()
        if text.lower() in ("false", "0"):
            return True
    return False


def _governance_relevant_run_text(run: str) -> str:
    """A step's own ``run:`` text, with shell comment lines and bare
    output-only (``echo``/``printf``/``print(...)``) lines stripped --
    what remains is the only part of a step's command text rule 3's
    CTK/application-probe/eval-runner checks may ever treat as evidence
    something was actually invoked.
    """
    kept_lines = [
        line
        for line in run.splitlines()
        if line.strip() and not line.strip().startswith("#")
        and not _INERT_RUN_LINE_RE.match(line.strip())
    ]
    return "\n".join(kept_lines)


def _governance_relevant_run_command_texts(document: Mapping) -> Tuple[str, ...]:
    """Every step's actual, execution-reachable ``run:`` shell command
    text -- mirrors `_run_command_texts`, but additionally excludes a
    step (or its containing job) that is statically disabled, and
    strips comment/echo-only lines from what remains. Used only for
    rule 3's CTK/application-probe and eval-suite-runner presence
    checks: a step this module can tell will never actually execute, or
    a line that only prints or comments on a marker rather than really
    running it, must never satisfy either check.
    """
    texts: List[str] = []
    for job, step in _job_steps(document):
        if _is_statically_disabled(step, job):
            continue
        run = step.get("run")
        if not isinstance(run, str):
            continue
        kept = _governance_relevant_run_text(run)
        if kept:
            texts.append(kept)
    return tuple(texts)


def _ci_probes_static_status(document: Mapping, triggers: Tuple[str, ...]) -> Status:
    if "pull_request" not in triggers:
        return "pass"  # rule 3 only binds required (PR-triggered) CI
    # Deliberately searched against the actual, execution-reachable
    # `run:` command text only -- never a step `name`, a `uses:`
    # reference, a YAML comment, a disabled/always-false step, or a bare
    # `echo`/`printf` line: a step merely *named* "Run CTK" whose command
    # never runs it, a fixture docstring mentioning "CTK" while
    # describing why it is intentionally absent, an `if: false`-guarded
    # step, or an `echo "ctk application-probe"` line, must never count
    # as evidence a step actually runs one.
    text = "\n".join(_governance_relevant_run_command_texts(document))
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
        oidc_wif=_oidc_status(
            document,
            login_job_steps,
            deploy_action_job_steps,
            _has_azure_deploy_evidence(document, deploy_action_job_steps),
        ),
        ci_probes=_ci_probes_static_status(document, triggers),
        identity_refs=_identity_refs(login_job_steps),
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


def _parse_codeowners_entries(path: Path) -> Tuple[Tuple[str, bool], ...]:
    """Every non-comment, non-blank CODEOWNERS line, in file order, as
    ``(pattern, has_owner)`` pairs -- including a line with *no* owner
    token at all, which is a valid CODEOWNERS shape that explicitly
    disowns any path it matches (an intentional "no one owns this"
    declaration, not a malformed line to discard).

    ``has_owner`` is true only when at least one token following the
    pattern is actually shaped like a real GitHub username/team
    (``@user``, ``@org/team``) or an email address; a pattern followed
    by only unrecognized text still counts as a declared (ownerless)
    entry for last-match-wins purposes, it simply carries no owner.
    """
    entries: List[Tuple[str, bool]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        pattern, owners = tokens[0], tokens[1:]
        has_owner = any(_CODEOWNERS_OWNER_TOKEN_RE.match(owner) for owner in owners)
        entries.append((pattern, has_owner))
    return tuple(entries)


def _codeowners_requirement_owned(
    entries: Sequence[Tuple[str, bool]], requirement: str
) -> bool:
    """Whether ``requirement`` is genuinely owned once every declared
    CODEOWNERS entry is resolved in file order.

    Real CODEOWNERS resolution is last-match-wins: for any given path,
    the *last* line in the file whose pattern matches it decides
    ownership (or the explicit lack of one) -- never simply "any
    matching line that happens to have an owner", which would let an
    earlier owner survive a later disowning line meant to override it.
    """
    owned = False
    for pattern, has_owner in entries:
        if _codeowners_pattern_covers(pattern, requirement):
            owned = has_owner
    return owned


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
    """True only if a pull_request-triggered workflow's actual,
    execution-reachable ``run:`` command text references one of
    ``eval_directories`` as a whole, boundary-delimited path token --
    never merely because the word "evals" (or an unrelated path that
    happens to contain it) appears anywhere in the document, and never
    from a disabled/always-false step or a comment/echo-only line that
    merely mentions the path rather than actually invoking a runner
    against it.
    """
    if not eval_directories:
        return False
    run_text = "\n".join(
        text
        for assessment in pr_workflows
        for text in _governance_relevant_run_command_texts(
            _load_workflow_document(assessment.path)
        )
    )
    return any(
        _path_token_pattern(directory).search(run_text) for directory in eval_directories
    )


# ---------------------------------------------------------------------------
# Live-evidence helpers
# ---------------------------------------------------------------------------


def _deploy_job_environment_names(assessments: Tuple["WorkflowAssessment", ...]) -> Set[str]:
    """Every GitHub Environment name a deploy job actually declares via
    its own ``environment:`` key (a bare string, or a mapping's
    ``name:``) -- used only to check environment protection rules "as
    available": a workflow that names no environment never requires
    this evidence at all.
    """
    names: Set[str] = set()
    for assessment in assessments:
        if not assessment.is_deploy:
            continue
        document = _load_workflow_document(assessment.path)
        jobs = document.get("jobs")
        if not isinstance(jobs, Mapping):
            continue
        for job in jobs.values():
            if not isinstance(job, Mapping):
                continue
            environment = job.get("environment")
            if isinstance(environment, Mapping):
                environment = environment.get("name")
            if isinstance(environment, str) and environment.strip():
                names.add(environment.strip())
    return names


def _environment_protection_confirmed(
    live_github: Optional[Mapping], declared_environments: Set[str]
) -> bool:
    """True unless live evidence explicitly says a deploy job's own
    declared GitHub Environment is unprotected.

    Checked only "as available": a workflow that declares no
    environment, or live evidence that supplies no ``environments``
    mapping at all, never blocks confirmation on this alone -- GitHub
    Environments are optional, and this module can never require
    evidence for a control the target may not even use.
    """
    if not declared_environments:
        return True
    if not live_github:
        return True
    environments = live_github.get("environments")
    if not isinstance(environments, Mapping):
        return True
    for name in declared_environments:
        entry = environments.get(name)
        if isinstance(entry, Mapping) and entry.get("protected") is False:
            return False
    return True


def _job_satisfies_ci_probes(job: Mapping) -> bool:
    """True only if this specific job's own steps -- never a sibling
    job's -- actually run both a CTK and an application probe, using
    the same execution-reachability rules as `_ci_probes_static_status`
    (excluding a disabled/always-false step and comment/echo-only
    lines).
    """
    texts: List[str] = []
    steps = job.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            if _is_statically_disabled(step, job):
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            kept = _governance_relevant_run_text(run)
            if kept:
                texts.append(kept)
    text = "\n".join(texts)
    return bool(_CTK_MARKER_RE.search(text) and _APPLICATION_PROBE_MARKER_RE.search(text))


def _required_check_job_names(assessments: Tuple["WorkflowAssessment", ...]) -> Set[str]:
    """Job ids/names of *only* the specific job(s), within a passing
    pull_request-triggered workflow, whose own steps actually run both a
    CTK and an application probe -- never every job in that workflow
    file. A branch-protection required-status-check list must name the
    real gating job GitHub reports the check under; an unrelated
    sibling job (lint, docs-preview, ...) that merely lives alongside
    the real gating job in the same file is not itself evidence of
    anything, and naming it in required-status-checks would never
    actually enforce this repo's real gating CI.
    """
    names: Set[str] = set()
    for assessment in assessments:
        if "pull_request" not in assessment.triggers:
            continue
        if assessment.ci_probes != "pass":
            continue
        document = _load_workflow_document(assessment.path)
        jobs = document.get("jobs")
        if not isinstance(jobs, Mapping):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, Mapping) or not _job_satisfies_ci_probes(job):
                continue
            names.add(str(job_id).strip().lower())
            if job.get("name"):
                names.add(str(job["name"]).strip().lower())
    return names


def _protection_flag_enabled(value: object) -> bool:
    """Normalize a GitHub branch-protection boolean flag, which the API
    can report either as a bare boolean or as ``{"enabled": bool}`` --
    absent/``None`` is treated as disabled (``False``), never inferred
    as enabled."""
    if isinstance(value, Mapping):
        return bool(value.get("enabled"))
    return bool(value)


def _branch_protection_confirmed(
    live_github: Optional[Mapping],
    required_check_names: Set[str],
    declared_environments: Set[str] = frozenset(),
) -> bool:
    """True only if live evidence proves the default branch is
    *substantively* protected -- reviews required (from real,
    API-shaped data: CODEOWNER review explicitly required *and* a
    positive required approving-review count), required checks naming
    this repo's real gating CI, admins not exempt, force pushes
    disallowed, and no named bypass allowance -- plus, "as available",
    that any GitHub Environment a deploy job actually uses is itself
    reported protected. A required-status-check list that merely names
    *some* check, a protection rule that exempts admins or still
    allows a forced push, or a `required_pull_request_reviews` object
    that never actually turns on CODEOWNER review or requires at least
    one approval, can never be resolved into a `pass` -- each is
    exactly the kind of gap static files alone could never see, and
    this function exists precisely so live evidence -- when supplied --
    is actually held to that full standard rather than a partial one.

    ``required_pull_request_reviews`` can also be reported as a bare
    boolean by a simplified test fixture (rather than GitHub's own
    nested object shape); that simplified form is deliberately still
    accepted at face value -- it stays conservative (any falsy value
    still fails closed) but is never held to the CODEOWNER-review/
    approval-count checks a real API response can actually supply.
    """
    if not live_github:
        return False
    default_branch = live_github.get("default_branch")
    branch_protection = live_github.get("branch_protection")
    if not default_branch or not isinstance(branch_protection, Mapping):
        return False
    rule = branch_protection.get(default_branch)
    if not isinstance(rule, Mapping):
        return False
    reviews = rule.get("required_pull_request_reviews")
    if not reviews:
        return False
    if isinstance(reviews, Mapping):
        # Real, API-shaped evidence must explicitly turn on CODEOWNER
        # review and require at least one approval -- a mapping present
        # for some other reason (e.g. only `dismiss_stale_reviews`) is
        # not itself proof reviews are actually required at all.
        if not reviews.get("require_code_owner_reviews"):
            return False
        approving_count = reviews.get("required_approving_review_count")
        if not isinstance(approving_count, int) or isinstance(approving_count, bool):
            return False
        if approving_count <= 0:
            return False
        bypass = reviews.get("bypass_pull_request_allowances")
        if isinstance(bypass, Mapping):
            if any(bypass.get(key) for key in ("users", "teams", "apps")):
                return False
        elif bypass:
            return False
    if not _protection_flag_enabled(rule.get("enforce_admins")):
        return False
    if _protection_flag_enabled(rule.get("allow_force_pushes")):
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
    if not (context_names & required_check_names):
        return False
    return _environment_protection_confirmed(live_github, declared_environments)


def _distinct_identities_confirmed(
    live_azure: Optional[Mapping],
    deploy_identities: Set[str],
    other_identities: Set[str],
) -> Optional[bool]:
    """Whether live Azure evidence confirms *these workflows'* own
    declared identity references actually resolve to genuinely distinct
    principals -- ``True`` if confirmed distinct, ``False`` if live
    evidence itself proves they resolve to the very same principal, or
    ``None`` if the evidence supplied is insufficient to say either way.

    A workflow's ``client-id``/``creds`` is almost always a
    ``${{ secrets.* }}`` expression, never a literal value this module
    could read itself, so correlating it to a real Azure principal is
    necessarily a live-evidence concern: ``live_azure`` must supply an
    ``identity_principal_ids`` mapping from each declared identity
    reference to the principal id it actually resolves to. Counting
    *unrelated* role assignments elsewhere in the tenant -- with no tie
    back to what these specific workflows actually declare -- proves
    nothing about whether deploy and build/test truly use separate
    identities.
    """
    if not live_azure:
        return None
    identity_principal_ids = live_azure.get("identity_principal_ids")
    if not isinstance(identity_principal_ids, Mapping):
        return None
    if not deploy_identities or not other_identities:
        return None
    deploy_principals = {identity_principal_ids.get(ref) for ref in deploy_identities}
    other_principals = {identity_principal_ids.get(ref) for ref in other_identities}
    if None in deploy_principals or None in other_principals:
        # A declared identity reference this live evidence never resolved
        # at all can never be treated as confirmed distinct.
        return None
    return deploy_principals.isdisjoint(other_principals)


# ---------------------------------------------------------------------------
# Public: assess_change_plane
# ---------------------------------------------------------------------------


def _rel(root: Path, path: object) -> str:
    """A finding's ``affected_paths`` entry as a repository-relative,
    POSIX-style path -- never the absolute filesystem path this module
    happened to read the file from, which would leak the assessment
    host's own directory layout into evidence meant to describe the
    repository itself.

    A path resolving outside ``root`` altogether (a symlink escaping the
    repository is the only way this module's own discovery can hand this
    function such a path) still relativizes against the *unresolved*
    candidate as a fallback; only if even that fails does this return
    just the file's own name -- never an absolute path, under any
    circumstance.
    """
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        pass
    try:
        return candidate.relative_to(Path(root)).as_posix()
    except ValueError:
        return candidate.name


def _rel_unresolved(root: Path, path: Path) -> str:
    """A path's own repository-relative location under ``root``, without
    ever following a symlink to compute it -- used only for error text
    about a path this module has deliberately *not* resolved yet (a
    symlink it is rejecting, or one whose resolution itself failed).
    Falls back to just the file name, never the absolute path, if
    ``path`` does not turn out to sit directly under ``root``.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


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
        raise ChangePlaneError(
            f"workflow file {_rel_unresolved(root, path)} is a symlink, "
            "not a real file"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ChangePlaneError(
            f"cannot resolve workflow file {_rel_unresolved(root, path)}: "
            f"{error}"
        ) from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ChangePlaneError(
            f"workflow file {_rel_unresolved(root, path)} resolves outside "
            "the assessment root"
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
        identity_refs=(),
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
    (schema: ``branch_protection``/``default_branch``/``environments`` and
    ``identity_principal_ids`` respectively); when either is ``None`` the
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


def _read_loose_git_object(common_dir: Path, sha1: str) -> Optional[Tuple[str, bytes]]:
    """Read one git loose object (``.git/objects/<aa>/<38 hex chars>``) and
    return its ``(type, body)`` -- ``None`` if the object does not exist as
    a loose object on disk.

    Deliberately does not read git packfiles at all: parsing the packfile
    format (delta chains, offset/ref-delta resolution) is out of scope for
    this read-only, dependency-free module. An object that exists only in
    a packfile (for example after ``git gc``) is treated exactly like a
    missing object -- the caller must fall back to "not confirmed",
    never fabricate a match.
    """
    if len(sha1) != 40:
        return None
    object_path = common_dir / "objects" / sha1[:2] / sha1[2:]
    if not object_path.is_file():
        return None
    try:
        raw = zlib.decompress(object_path.read_bytes())
    except (OSError, zlib.error):
        return None
    header, _, body = raw.partition(b"\x00")
    obj_type, _, _size = header.partition(b" ")
    return obj_type.decode("ascii", errors="replace"), body


def _parse_git_tree_entries(body: bytes) -> Dict[str, str]:
    """Parse a git ``tree`` object's body into ``{name: sha1_hex}`` --
    each entry is ``<mode> <name>\\0<20 raw sha1 bytes>`` back to back,
    with no other separator between entries.
    """
    entries: Dict[str, str] = {}
    offset = 0
    length = len(body)
    while offset < length:
        space_index = body.index(b" ", offset)
        nul_index = body.index(b"\x00", space_index)
        name = body[space_index + 1 : nul_index].decode("utf-8", errors="replace")
        sha1_bytes = body[nul_index + 1 : nul_index + 21]
        entries[name] = sha1_bytes.hex()
        offset = nul_index + 21
    return entries


def _resolve_head_blob_sha1(
    common_dir: Path, source_commit: str, relative_path: str
) -> Optional[str]:
    """Resolve the blob sha1 actually committed at ``source_commit`` for
    ``relative_path`` (a repository-relative POSIX path), by walking
    commit -> root tree -> subtree -> ... -> blob through real, on-disk
    loose git objects only -- never a git subprocess, and never a guess
    when any object along that walk is missing or malformed (including
    if it exists only in a packfile; see :func:`_read_loose_git_object`).
    """
    commit_obj = _read_loose_git_object(common_dir, source_commit)
    if commit_obj is None or commit_obj[0] != "commit":
        return None
    first_line = commit_obj[1].split(b"\n", 1)[0]
    if not first_line.startswith(b"tree "):
        return None
    tree_sha1 = first_line[len(b"tree ") :].decode("ascii", errors="replace").strip()
    segments = [segment for segment in relative_path.split("/") if segment]
    if not segments:
        return None
    current_sha1 = tree_sha1
    for segment in segments[:-1]:
        tree_obj = _read_loose_git_object(common_dir, current_sha1)
        if tree_obj is None or tree_obj[0] != "tree":
            return None
        entries = _parse_git_tree_entries(tree_obj[1])
        next_sha1 = entries.get(segment)
        if next_sha1 is None:
            return None
        current_sha1 = next_sha1
    final_tree_obj = _read_loose_git_object(common_dir, current_sha1)
    if final_tree_obj is None or final_tree_obj[0] != "tree":
        return None
    return _parse_git_tree_entries(final_tree_obj[1]).get(segments[-1])


def _workflow_set_is_clean(
    git_dir: Path,
    root: Path,
    workflow_paths: Tuple[Path, ...],
    source_commit: Optional[str],
) -> bool:
    """True only if every workflow file's on-disk bytes match the exact
    blob git's own index has staged for it (working tree not dirty vs.
    the index), *and* the index's own staged blob for it matches what is
    actually committed at ``source_commit`` (the index not dirty vs.
    HEAD -- i.e. no staged-but-uncommitted change either).

    Any index-parse failure, a missing index entry, a content mismatch,
    or an unresolvable HEAD blob (including one only reachable through a
    packfile this module deliberately never parses) is treated as "not
    confirmed clean", never as "confirmed clean": this check only ever
    makes evidence *more* conservative, never fabricates a clean result
    it cannot actually verify.
    """
    entries = _read_git_index_entries(git_dir)
    if entries is None:
        return False
    common_dir = _git_common_dir(git_dir)
    for path in workflow_paths:
        relative_path = path.relative_to(root).as_posix()
        entry = entries.get(relative_path)
        if entry is None:
            return False
        _recorded_size, recorded_sha1 = entry
        try:
            data = path.read_bytes()
        except OSError:
            return False
        if _git_blob_sha1(data) != recorded_sha1:
            return False
        if source_commit is None:
            return False
        head_sha1 = _resolve_head_blob_sha1(common_dir, source_commit, relative_path)
        if head_sha1 != recorded_sha1:
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
    if not _workflow_set_is_clean(git_dir, root, workflow_paths, source_commit):
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
    if not assessments:
        # No workflow was even discovered to check -- there is nothing here
        # this rule could have actually verified, so the honest answer is
        # "nothing to check", never the vacuous `pass` an empty offenders
        # tuple would otherwise produce.
        controls["ghcp_pr_only_gate"] = "not-applicable"
        return
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

    entries = _parse_codeowners_entries(ownership_path)
    missing = tuple(
        requirement
        for requirement in _REQUIRED_CODEOWNERS_PATTERNS
        if not _codeowners_requirement_owned(entries, requirement)
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
                    f"{_rel(root, ownership_path)} does not declare an owned "
                    f"pattern covering: {', '.join(missing)}."
                ),
                affected_paths=(_rel(root, ownership_path),),
            )
        )
        return

    if _branch_protection_confirmed(
        live_github,
        _required_check_job_names(assessments),
        _deploy_job_environment_names(assessments),
    ):
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
                f"{_rel(root, ownership_path)} declares complete static "
                "coverage, but no live GitHub branch-protection evidence "
                "was supplied (`live_github` is None or incomplete); static "
                "files can never prove a branch protection rule or "
                "required-check list is actually enforced."
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
    if not assessments:
        controls["ghcp_pinned_least_privilege"] = "not-applicable"
        return
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
    if not assessments:
        controls["ghcp_azure_oidc"] = "not-applicable"
        return
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
    deploy_identities = {
        ref for a in assessments if a.is_deploy for ref in a.identity_refs
    }
    other_identities = {
        ref for a in assessments if not a.is_deploy for ref in a.identity_refs
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

    live_confirmed = _distinct_identities_confirmed(
        live_azure, deploy_identities, other_identities
    )
    if live_confirmed is True:
        controls["ghcp_identity_separation"] = "pass"
        return

    if live_confirmed is False:
        # Static files declare no overlapping identity reference, but live
        # Azure evidence itself resolved these workflows' own declared
        # references to the very same principal -- a shared identity live
        # evidence proved, not merely one static files failed to disprove.
        controls["ghcp_identity_separation"] = "must-fix"
        findings.append(
            Finding(
                finding_id="GHCP-006",
                status="must-fix",
                phase="pre-deploy",
                plane="change",
                reason_code="live-confirmed-shared-identity",
                summary=(
                    "Build/test/deploy identities are shared, over-broad, or "
                    "not evidenced."
                ),
                details=(
                    "Live Azure evidence resolved deploy identity reference(s) "
                    f"{sorted(deploy_identities)} and non-deploy identity "
                    f"reference(s) {sorted(other_identities)} to at least one "
                    "shared principal."
                ),
            )
        )
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
                "reference, but no live Azure evidence resolving these "
                "workflows' own declared identity references to genuinely "
                "distinct principals was supplied (`live_azure` is None or "
                "incomplete)."
            ),
        )
    )
