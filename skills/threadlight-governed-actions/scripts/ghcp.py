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

Task 8 adds two optional, read-only *collectors* --
:func:`collect_live_github` and :func:`collect_live_azure` -- that a
caller may run separately, independently of ``assess_change_plane``.
They are the only functions in this module that ever run an external
command (always through an injected runner, never a real subprocess in
a unit test), and only ever a fixed set of read-only ``gh api``/``az
... list`` commands: no write, no mutation, no secret.
:func:`collect_live_github`'s ``data["default_branch"]``,
``data["branch_protection"]``, and ``data["environments"]`` are shaped
to match exactly what ``assess_change_plane`` already reads from a
``live_github`` mapping, so a caller *may* pass that data straight
through as ``live_github`` if it chooses to. :func:`collect_live_azure`
collects a materially different kind of evidence (federated-credential
trust and role-assignment/role-definition detail) that
``assess_change_plane``'s own ``live_azure`` parameter does not consume
today -- this module makes no claim that collector's output can be
passed to ``assess_change_plane`` directly. ``assess_change_plane``
itself remains exactly as described above -- purely file-based, never
calling either collector on its own.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import struct
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

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
# pull_request_target events, and always attacker-controlled), a
# literal `refs/pull/...` ref (the fork PR's own ref namespace, whether
# `/head` or `/merge`), and `github.event.pull_request.merge_commit_sha`
# -- a *sibling* field to `.head`, not nested under it, so it is not
# caught by the `.head` substring above, but the merge commit it names
# is GitHub's own speculative merge of the PR author's head into the
# base branch: it still incorporates the attacker's own diff, so
# checking it out under `pull_request_target`'s elevated
# permissions/secrets is exactly as untrusted as checking out the head
# ref/sha directly. Checking out the trusted base ref from an
# attacker-controlled `repository:` fork (or vice versa) is just as
# untrusted as either alone -- both the ref *and* the repository must
# stay on the trusted base for a `pull_request_target` checkout to be
# safe.
_UNTRUSTED_CHECKOUT_REF_MARKERS: Tuple[str, ...] = (
    "github.event.pull_request.head",
    "github.event.pull_request.merge_commit_sha",
    "github.head_ref",
    "refs/pull/",
)

# GitHub Actions expression syntax allows bracket-indexed property access
# (`github.event.pull_request['head']['ref']`,
# `github.event.pull_request["head"]["sha"]`) as an exact equivalent to
# dotted access (`github.event.pull_request.head.ref`) -- purely
# rewriting a marker's dotted form into bracket notation must never be
# enough to dodge the substring checks above. Every recognized
# `['name']`/`["name"]` index is rewritten to its dotted equivalent
# before any marker or trust check is attempted, so both forms are
# caught identically.
_INDEXED_EXPRESSION_ACCESS_RE = re.compile(r"\[\s*['\"]([A-Za-z0-9_]+)['\"]\s*\]")

# A single `${{ ... }}` expression segment, captured non-greedily so a
# value embedding more than one expression is inspected one expression
# at a time.
_EXPRESSION_SEGMENT_RE = re.compile(r"\$\{\{\s*(?P<inner>.*?)\s*\}\}", re.DOTALL)

# A raw `git` command run in a `run:` step executes in an actual POSIX
# shell, which GitHub Actions itself feeds every declared `env:` entry
# to as a real OS-level environment variable -- so a workflow author
# can route the very same untrusted PR-head data into a raw git
# command's operand via ordinary shell variable syntax
# (`git fetch origin $PR_REF` / `git fetch origin "${PR_REF}"`)
# instead of a `${{ env.PR_REF }}` GitHub-expression indirection, and
# it is exactly as dangerous. `${VAR}` is matched as a distinct
# alternative from bare `$VAR` (rather than one pattern with optional
# braces) precisely so neither alternative can ever consume the `${{`
# that opens a GitHub expression segment: the char immediately after
# `${` in `${{ ... }}` is itself `{`, which never satisfies either
# alternative's identifier-start requirement, so the two syntaxes can
# never be confused for one another.
_SHELL_VARIABLE_REFERENCE_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


def _shell_variable_reference_names(text: str) -> List[str]:
    """Every distinct shell `$VAR`/`${VAR}` variable name referenced in
    ``text`` -- never a GitHub `${{ ... }}` expression segment, which
    uses a syntactically distinct, non-overlapping form (see
    :data:`_SHELL_VARIABLE_REFERENCE_RE`).
    """
    names = []
    for match in _SHELL_VARIABLE_REFERENCE_RE.finditer(text):
        names.append(match.group(1) or match.group(2))
    return names

# The only GitHub context expressions this module can actually reason
# about as always resolving to trusted, base-branch/base-repository-
# scoped data for a `pull_request_target` checkout's own `ref:`/
# `repository:` input -- exact context field names, checked as-is
# (never a prefix match), plus a small set of dotted-object prefixes
# whose *entire* subtree is base-scoped.
_TRUSTED_CHECKOUT_EXPRESSIONS: Tuple[str, ...] = (
    "github.repository",
    "github.sha",
    "github.ref",
    "github.ref_name",
    "github.ref_type",
    "github.base_ref",
)
_TRUSTED_CHECKOUT_EXPRESSION_PREFIXES: Tuple[str, ...] = (
    "github.event.repository.",
    "github.event.pull_request.base.",
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

# A *recognized runner invocation* -- the actual command being run is
# literally `ctk`/`probes` (optionally via `./` or `python[3] -m`) --
# anchored at the start of an already shell-segment-isolated command
# line. `_CTK_MARKER_RE`/`_APPLICATION_PROBE_MARKER_RE` above match the
# marker word *anywhere*, which a `grep -r ctk .`, `cat ctk.log`, `find
# . -name '*ctk*'`, or a log filename mentioning either word would all
# satisfy without ever actually running anything; these two are used
# instead wherever rule 3 needs proof of genuine execution.
_RECOGNIZED_CTK_RUNNER_RE = re.compile(
    r"^(?:\./)?(?:python3?\s+-m\s+)?ctk\b", re.IGNORECASE
)
_RECOGNIZED_APPLICATION_PROBE_RUNNER_RE = re.compile(
    r"^(?:\./)?(?:python3?\s+-m\s+)?probes\b", re.IGNORECASE
)


def _ci_probes_satisfied(text: str) -> bool:
    """True only if ``text`` (already execution-reachable ``run:``
    command text, one shell segment per line) contains, on some line, a
    recognized CTK-runner invocation, and, on some line, a recognized
    application-probe-runner invocation whose *own* line also names the
    application probe specifically -- never merely because both marker
    words appear somewhere in the text at all, which a `grep`/`cat`/
    `find` command or a log filename mentioning them would satisfy
    without ever actually running either.
    """
    lines = text.splitlines()
    ctk_ok = any(_RECOGNIZED_CTK_RUNNER_RE.match(line) for line in lines)
    probe_ok = any(
        _RECOGNIZED_APPLICATION_PROBE_RUNNER_RE.match(line)
        and _APPLICATION_PROBE_MARKER_RE.search(line)
        for line in lines
    )
    return ctk_ok and probe_ok

# Recognized test/eval-suite runner invocations -- a command *prefix* that
# actually executes tests/evaluations, as opposed to one that merely
# inspects, lists, or prints a path (`ls evals`, `find evals -name ...`,
# `cat evals/x.py`, an echoed mention, ...). Discovering an eval suite's
# own path token in a `run:` command is not by itself proof the suite is
# ever *executed* -- rule 3 requires a recognized runner name to appear
# alongside that path token before treating the eval suite as covered.
_RECOGNIZED_EVAL_RUNNER_RE = re.compile(
    r"\b(?:pytest|py\.test|python3?\s+-m\s+(?:pytest|unittest)|unittest2?"
    r"|npm\s+(?:run\s+)?test\b|yarn\s+test\b|pnpm\s+test\b|npx\s+jest\b|jest\b"
    r"|mocha\b|go\s+test\b|dotnet\s+test\b|cargo\s+test\b|rspec\b|tox\b|nose2?\b)",
    re.IGNORECASE,
)


def _eval_directory_invoked_by_recognized_runner(run_text: str, directory: Path) -> bool:
    """True only if a *recognized* test/eval runner invocation (pytest,
    unittest, npm/yarn/pnpm test, jest, mocha, go test, dotnet test, cargo
    test, rspec, tox, ...) and a whole, boundary-delimited reference to
    ``directory`` both appear on the same line of ``run_text``.

    A command that merely lists, prints, or searches the eval directory
    (``ls evals``, ``cat evals/x.py``, ``find evals -name '*.py'``, an
    echoed mention, ...) never satisfies this: the eval directory's path
    token appearing anywhere in the run text is not, by itself, proof the
    suite is ever actually executed. Binding both to the same line is a
    deliberately conservative approximation of "the runner is invoked
    against this path" without needing a full shell parser.
    """
    token_pattern = _path_token_pattern(directory)
    for line in run_text.splitlines():
        if _RECOGNIZED_EVAL_RUNNER_RE.search(line) and token_pattern.search(line):
            return True
    return False

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

# Top-level directory names conventionally used for Infrastructure-as-
# Code in a repository. Deliberately matched only at the repository
# root -- a nested directory elsewhere that merely happens to share one
# of these names (a vendored dependency's own `infra/` folder, say) is
# not this repository's own infrastructure surface, and requiring
# CODEOWNERS coverage for it would be a false positive.
_INFRASTRUCTURE_DIRECTORY_NAMES: Tuple[str, ...] = (
    "infra",
    "infrastructure",
    "terraform",
    "bicep",
)

# File suffixes that unambiguously mark a file as Infrastructure-as-Code
# regardless of which directory it lives in: Terraform's own `.tf`/
# `.tf.json`, and Azure's own Bicep template format.
_IAC_FILE_SUFFIXES: Tuple[str, ...] = (".tf", ".tf.json", ".bicep")

# The exact `$schema` substring every genuine ARM (Azure Resource
# Manager) JSON template declares -- a far more reliable, conservative
# signal than guessing from a `.json` file's name or directory alone,
# which would false-positive on every unrelated JSON file in the repo.
_ARM_TEMPLATE_SCHEMA_RE = re.compile(
    r'"\$schema"\s*:\s*"[^"]*deploymentTemplate\.json', re.IGNORECASE
)

# Only ever sniffed for the fixed, tiny `$schema` marker above -- never
# fully parsed/loaded -- so an arbitrarily large `.json` file elsewhere
# in the tree can never make this scan slow or memory-heavy.
_ARM_TEMPLATE_SNIFF_BYTES = 4096

# The Azure Developer CLI (azd) manifest -- conventionally at the
# repository root -- declares and drives this repository's actual
# deployment (`azd up`/`azd deploy`); its presence is as unambiguous a
# deployment-descriptor signal as an infrastructure directory.
_DEPLOYMENT_MANIFEST_ROOT_FILENAMES: Tuple[str, ...] = ("azure.yaml", "azure.yml")

# A Dockerfile "variant" -- the bare canonical `Dockerfile`, a suffixed
# form (`Dockerfile.prod`), or a prefixed form (`api.Dockerfile`) --
# each unambiguously names a container build/deployment descriptor
# regardless of which directory it lives in, matched case-insensitively
# since the convention itself is not case-sensitive in practice.
_DOCKERFILE_NAME_RE = re.compile(r"^dockerfile(?:\..+)?$|^.+\.dockerfile$", re.IGNORECASE)

# The exact filename Helm itself requires at the root of every chart --
# a far more reliable, conservative signal than guessing from a
# directory name like `charts/` alone.
_HELM_CHART_FILENAME = "chart.yaml"

# The two top-level keys every genuine Kubernetes manifest declares --
# sniffed narrowly (never fully parsed) so an unrelated YAML file
# elsewhere in the tree can never false-positive as a deployment
# descriptor merely for using either word in some other sense.
_KUBERNETES_MANIFEST_API_VERSION_RE = re.compile(r"^apiVersion:\s*\S", re.MULTILINE)
_KUBERNETES_MANIFEST_KIND_RE = re.compile(r"^kind:\s*\S", re.MULTILINE)

# Bounds the same class of sniff read as `_ARM_TEMPLATE_SNIFF_BYTES`,
# for exactly the same reason: a large, unrelated YAML file elsewhere
# in the tree can never make this scan slow or memory-heavy.
_KUBERNETES_MANIFEST_SNIFF_BYTES = 4096

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
    deploy_identity_refs: Tuple[str, ...] = ()
    non_deploy_identity_refs: Tuple[str, ...] = ()
    environment_identity_refs: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()


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


def _sanitize_yaml_error(error: "yaml.YAMLError") -> str:
    """A parse failure's own sanitized error class name plus, when
    available, its 1-indexed line/column -- never the parser's own
    message text or source-line snippet.

    PyYAML's own ``str(error)`` for a ``MarkedYAMLError`` (the common
    case -- a scanner/parser/composer error) embeds the literal
    surrounding source text, for example the exact broken line and
    whatever it happens to contain -- including any credential-shaped
    value a user pasted into an otherwise-malformed workflow file. A
    malformed-YAML finding must never retain any of that content, only
    the fact that parsing failed and roughly where; `error.problem`,
    `error.context`, `error.note`, and `str(error)` itself are
    therefore never interpolated here.
    """
    class_name = type(error).__name__
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    if mark is not None:
        return f"{class_name} at line {mark.line + 1}, column {mark.column + 1}"
    return class_name


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
        raise ChangePlaneError(
            f"invalid YAML in workflow file {path}: {_sanitize_yaml_error(error)}"
        ) from error
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


# The `azure/cli` marketplace action executes arbitrary Azure CLI
# commands supplied through its own `inlineScript:` input rather than a
# `run:` step body -- a deploy command hidden inside one (`az webapp
# deploy ...`, `az deployment group create ...`) is exactly as real a
# deployment as the identical command typed directly into a `run:`
# step, and every check that inspects "actual command text" must
# inspect this input too, not just `run:`.
_AZURE_CLI_ACTION_MARKER = "azure/cli"


def _azure_cli_inline_script_texts(document: Mapping) -> Tuple[str, ...]:
    """Every ``inlineScript:`` input text from an ``azure/cli`` action
    step anywhere in the document."""
    texts: List[str] = []
    for step in _all_steps(document):
        if str(step.get("uses", "")).split("@", 1)[0].strip().lower() != _AZURE_CLI_ACTION_MARKER:
            continue
        with_block = step.get("with")
        if not isinstance(with_block, Mapping):
            continue
        script = _with_input_value(with_block, "inlineScript")
        if isinstance(script, str):
            texts.append(script)
    return tuple(texts)


def _job_azure_cli_inline_script_texts(job: Mapping) -> Tuple[str, ...]:
    """Same as :func:`_azure_cli_inline_script_texts`, scoped to just
    this one job's own steps."""
    texts: List[str] = []
    steps = job.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            if str(step.get("uses", "")).split("@", 1)[0].strip().lower() != _AZURE_CLI_ACTION_MARKER:
                continue
            with_block = step.get("with")
            if not isinstance(with_block, Mapping):
                continue
            script = _with_input_value(with_block, "inlineScript")
            if isinstance(script, str):
                texts.append(script)
    return tuple(texts)


def _run_command_texts(document: Mapping) -> Tuple[str, ...]:
    """Every step's actual ``run:`` shell command text, plus every
    ``azure/cli`` step's own ``inlineScript:`` text -- never a step
    ``name``, a ``uses:`` reference, or a YAML comment, none of which are
    evidence that a command genuinely executes."""
    return tuple(
        step["run"]
        for step in _all_steps(document)
        if isinstance(step.get("run"), str)
    ) + _azure_cli_inline_script_texts(document)


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


def _with_input_value(with_block: Mapping, key: str) -> object:
    """The value declared for ``key`` in a step's ``with:`` block,
    matched case-insensitively -- GitHub Actions itself resolves a
    `with:` input name case-insensitively in practice: every declared
    `with:` entry is exposed to the invoked action as an
    ``INPUT_<NAME>`` environment variable built by uppercasing the
    literal YAML key exactly as written, and an action's own
    ``@actions/core`` ``getInput()`` helper uppercases the name it is
    asked to read the very same way before looking that variable up --
    so a workflow author spelling a security-relevant input as
    ``Client-Id:``, ``CLIENT-ID:``, or ``client-id:`` all resolve to
    the exact same actual input, regardless of which casing this
    module happens to compare against. Comparing a `with:` key by
    exact, case-sensitive text would silently miss a security-relevant
    input (``ref``, ``repository``, ``client-id``, ``creds``,
    ``publish-profile``, ...) declared in a different letter case.
    Returns ``None`` if no key case-insensitively equal to ``key``
    exists in ``with_block`` at all. The *first* case-insensitive match
    (in the block's own declared order) is returned if more than one
    such key is somehow present -- YAML itself does not allow a
    genuinely duplicate key within one mapping, so this only matters
    for an already-malformed document.
    """
    target = key.strip().lower()
    for actual_key, value in with_block.items():
        if str(actual_key).strip().lower() == target:
            return value
    return None


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


_SAFE_IDENTITY_REF_RE = re.compile(
    r"^\$\{\{\s*[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)+\s*\}\}$"
)

_IDENTITY_REF_PARTS_RE = re.compile(
    r"^\$\{\{([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_-]+)+)\}\}$"
)

# GitHub Actions documents `secrets`/`vars` -- both the context name
# itself and every name looked up on it -- as genuinely case-insensitive:
# a secret/variable is stored (and looked up) as uppercase regardless of
# how its name was entered or referenced, so `secrets.AZURE_CLIENT_ID`
# and `SECRETS.azure_client_id` name the exact same underlying secret.
# This is a documented special case, not a general rule: ordinary
# property dereference elsewhere (`github.sha` vs. `github.SHA`,
# `env.FOO` vs. `env.foo`, `needs.build.outputs.id`, ...) remains
# case-sensitive, since GitHub Actions does not uppercase-normalize
# environment-variable names, job/step ids, or output names the way it
# does for `secrets`/`vars`.
_CASE_INSENSITIVE_IDENTITY_CONTEXTS = frozenset({"secrets", "vars"})


def _canonicalize_github_expression_reference(value: str) -> Optional[str]:
    """The canonical form of ``value`` as a single, validated GitHub
    Actions context reference (a bare dotted path like
    ``secrets.AZURE_CLIENT_ID``, wrapped in ``${{ ... }}`` and nothing
    else), or ``None`` if it is not one at all.

    Two spellings of the exact same context reference can differ
    purely in incidental, semantically meaningless ways -- extra or
    missing whitespace just inside the ``${{ ... }}`` wrapper
    (``${{ secrets.X }}`` vs. ``${{secrets.X}}`` vs. ``${{  secrets.X  }}``),
    bracket-indexed property access used as an exact equivalent of
    dotted access (``secrets['X']`` vs. ``secrets.X``), or -- only for
    the `secrets`/`vars` contexts specifically, per GitHub's own
    documented case-insensitive storage/lookup for those two contexts
    -- differing letter case in the context name and/or the
    secret/variable name itself (``${{ SECRETS.Azure_Client_Id }}`` is
    the identical secret as ``${{ secrets.AZURE_CLIENT_ID }}``).
    Comparing two such references for identity by their raw,
    as-written text would treat them as different identities even
    though they resolve to the exact same underlying secret/variable/
    output, which is precisely backwards for rule 6's shared-identity
    detection: this function normalizes bracket-indexed access to its
    dotted equivalent, strips all internal whitespace, and -- only for
    a `secrets`/`vars` reference -- uppercases the context keyword and
    the full remaining dotted path, before validating the result
    against :data:`_SAFE_IDENTITY_REF_RE`, so every such variant
    canonicalizes to the identical form. A non-`secrets`/`vars`
    reference (`github.*`, `env.*`, `needs.*`, ...) is left exactly as
    normalized -- its case is semantically significant and must not be
    collapsed.
    """
    normalized = _normalize_indexed_github_expression(value.strip())
    normalized = re.sub(r"\s+", "", normalized)
    if not _SAFE_IDENTITY_REF_RE.match(normalized):
        return None
    parts_match = _IDENTITY_REF_PARTS_RE.match(normalized)
    if parts_match is None:
        return normalized
    context_name, rest = parts_match.group(1), parts_match.group(2)
    if context_name.lower() in _CASE_INSENSITIVE_IDENTITY_CONTEXTS:
        return "${{" + context_name.lower() + rest.upper() + "}}"
    return normalized


def _redact_identity_ref(value: str) -> str:
    """A workflow's own literal ``client-id``/``creds`` text, safe to
    retain and render anywhere in this module's evidence only when it
    is itself a single, validated GitHub Actions context reference --
    a bare, dotted path like ``secrets.AZURE_CLIENT_ID``, ``vars.FOO``,
    ``env.FOO``, or ``needs.build.outputs.id``, wrapped in
    ``${{ ... }}`` and nothing else.

    Anything else is redacted, including a string literal, a function
    call (``fromJSON(...)``, ``format(...)``, ...), an operator
    expression, or string concatenation *even when wrapped in*
    ``${{ ... }}`` -- an expression is not itself proof its payload is
    a safe reference rather than an inline credential a workflow
    author hardcoded directly into the expression (e.g.
    ``${{ 'hunter2' }}``); only a bare context reference this module
    can actually validate is preserved. Every other value -- an
    unwrapped inline literal, or an unvalidated expression -- is
    replaced here with a short, non-reversible fingerprint, so two
    occurrences of the very same inline value can still be recognized
    as equal across a report without the underlying credential ever
    appearing in any finding, log, or report.

    A validated reference is returned in its *canonical* form (see
    :func:`_canonicalize_github_expression_reference`), not the exact
    as-written text -- two semantically identical references that
    differ only in incidental whitespace or bracket-vs-dot indexing
    must compare equal wherever this module intersects/deduplicates
    identity references, not merely where they happen to be spelled
    identically.
    """
    canonical = _canonicalize_github_expression_reference(value)
    if canonical is not None:
        return canonical
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<inline-identity-value-redacted:sha256:{digest}>"



def _dedupe_preserve_order(items: Sequence[str]) -> Tuple[str, ...]:
    seen: Set[str] = set()
    unique: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)


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
            value = _with_input_value(with_block, key)
            if value:
                refs.append(_redact_identity_ref(str(value)))
    return _dedupe_preserve_order(refs)


# ---------------------------------------------------------------------------
# Deploy classification and PR-only gating (rule 1)
# ---------------------------------------------------------------------------


# GitHub Actions itself permits a top-level caller workflow plus up to
# nine levels of chained reusable workflows (ten levels total): "You can
# connect a maximum of ten levels of workflows - that is, the top-level
# caller workflow and up to nine levels of reusable workflows." Every one
# of those nine levels must be traced -- a lower bound here would falsely
# classify a deploy that only appears at, say, level 7, 8, or 9 as
# non-deploying even though GitHub itself fully supports and would run
# such a chain. A call chain that still has an *unresolved* further call
# beyond this boundary cannot be proven non-deploying by this static
# traversal; see `_local_reusable_workflow_deploys` for the fail-closed
# behavior that applies once this bound is reached.
_MAX_REUSABLE_WORKFLOW_DEPTH = 9


def _local_reusable_workflow_calls(document: Mapping) -> Tuple[str, ...]:
    """Every ``uses:`` reference on a job that *calls* a reusable
    workflow (a job with no ``steps:`` of its own, only a top-level
    ``uses:``) -- never a step-level ``uses:`` referencing an ordinary
    marketplace action, which this must not conflate with a workflow
    call."""
    calls: List[str] = []
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job in jobs.values():
            if not isinstance(job, Mapping) or "steps" in job:
                continue
            uses = job.get("uses")
            if isinstance(uses, str) and uses.strip():
                calls.append(uses.strip())
    return tuple(calls)


def _resolve_local_reusable_workflow_path(root: Path, uses_ref: str) -> Optional[Path]:
    """Resolve a local reusable-workflow ``uses: ./...`` reference to
    its real, on-disk path -- strictly relative to the repository root,
    exactly as GitHub Actions itself resolves such a reference -- or
    ``None`` if this is not a local reusable-workflow reference at all,
    if it contains a ``..`` path-traversal segment, or if resolving it
    (following any symlink) would land outside the repository root
    entirely. A resolved path that does not exist as a regular file is
    also rejected. Every rejection here is a fail-closed "not verified
    as a local reusable-workflow reference", never a crash and never a
    silently-followed path escape.
    """
    ref = uses_ref.split("@", 1)[0].strip()
    if not ref.startswith("./"):
        return None
    if ".." in Path(ref).parts:
        return None
    try:
        resolved_root = root.resolve()
        candidate = (root / ref).resolve()
    except OSError:
        return None
    if candidate != resolved_root and resolved_root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _local_reusable_workflow_deploys(
    root: Path,
    uses_ref: str,
    _visited: Optional[Set[Path]] = None,
    _depth: int = 0,
) -> bool:
    """True if a local reusable-workflow reference resolves to a
    workflow file that either itself deploys, or itself calls (possibly
    transitively) another local reusable workflow that deploys -- so a
    push-triggered caller that merely delegates its actual deployment
    to a separate reusable-workflow file is classified a deploy exactly
    as if it had deployed directly.

    Every level up to and including :data:`_MAX_REUSABLE_WORKFLOW_DEPTH`
    (GitHub's own supported nesting bound) is traced. Cycle-safe: a
    resolved path already visited earlier on this same call chain is
    skipped (it was, or is being, evaluated on the branch that first
    visited it, so re-descending into it here could not reveal anything
    new) -- this never loops indefinitely or exhausts the stack. An
    unresolvable reference (not a local ``./`` reference, path-escaping,
    or missing on disk) genuinely resolves to "no evidence this way" and
    stays non-deploying, exactly as before.

    A chain that is *still* going -- there is a further call left to
    follow -- once this traversal has already reached the supported
    depth boundary is different: this static analysis cannot safely
    resolve, and therefore cannot prove non-deploying, whatever lies
    beyond that boundary. Rather than silently asserting non-deployment
    it cannot actually establish, that case fails closed and is
    conservatively treated as a deploy.
    """
    if _depth >= _MAX_REUSABLE_WORKFLOW_DEPTH:
        return True
    resolved = _resolve_local_reusable_workflow_path(root, uses_ref)
    if resolved is None:
        return False
    visited = _visited if _visited is not None else set()
    if resolved in visited:
        return False
    visited.add(resolved)
    try:
        called_document = _load_workflow_document(resolved)
    except ChangePlaneError:
        return False
    if not isinstance(called_document, Mapping):
        return False
    if _is_deploy_workflow(called_document):
        return True
    nested_refs = _local_reusable_workflow_calls(called_document)
    if not nested_refs:
        return False
    return any(
        _local_reusable_workflow_deploys(root, nested_ref, visited, _depth + 1)
        for nested_ref in nested_refs
    )


def _is_deploy_workflow(document: Mapping, root: Optional[Path] = None) -> bool:
    """True if this workflow deploys -- via a known marketplace deploy
    action, a raw Azure/azd CLI deploy command run directly in a `run:`
    step or an `azure/cli` step's own `inlineScript:` input, a job
    id/name that says so, or by calling (directly or transitively) a
    local reusable workflow that itself deploys -- classified
    regardless of which job any of this lives in or what that job
    happens to be named: a job named "release" running a bare `az
    webapp deploy` command is still a deploy for rule 1's purposes.

    ``root`` -- when supplied -- additionally lets a local reusable-
    workflow ``uses: ./...`` call be resolved and inspected; omitted
    (the default), only this document's own directly-declared evidence
    is considered, exactly as before.
    """
    uses_refs = [ref.split("@", 1)[0].strip().lower() for ref in _uses_refs(document)]
    if any(marker in ref for ref in uses_refs for marker in _DEPLOY_ACTION_MARKERS):
        return True
    run_text = "\n".join(_run_command_texts(document))
    if _DEPLOY_RUN_COMMAND_RE.search(run_text):
        return True
    labels = [label.lower() for label in _job_ids_and_names(document)]
    if any("deploy" in label for label in labels):
        return True
    if root is not None:
        for uses_ref in _local_reusable_workflow_calls(document):
            if _local_reusable_workflow_deploys(root, uses_ref):
                return True
    return False


def _is_deploy_job(job_id: object, job: Mapping) -> bool:
    """True if *this specific* job -- not necessarily the workflow as a
    whole -- deploys: one of its own steps references a known Azure
    deploy action or runs a raw Azure/azd CLI deploy command directly,
    or the job's own id/name says so. Mirrors
    :func:`_is_deploy_workflow`'s three signals, but scoped to just
    this one job's own steps/id/name rather than the whole document --
    a workflow mixing a build/test job and a separate deploy job side
    by side must have each job classified on its own terms, since rule
    6's identity-separation check needs to know precisely which job
    within a workflow is the deploying one, not merely whether *some*
    job in the whole workflow deploys.
    """
    steps = job.get("steps")
    step_list = (
        [step for step in steps if isinstance(step, Mapping)]
        if isinstance(steps, list)
        else []
    )
    uses_refs = [
        str(step.get("uses", "")).split("@", 1)[0].strip().lower()
        for step in step_list
        if step.get("uses")
    ]
    if any(marker in ref for ref in uses_refs for marker in _DEPLOY_ACTION_MARKERS):
        return True
    run_text = "\n".join(
        [step["run"] for step in step_list if isinstance(step.get("run"), str)]
        + list(_job_azure_cli_inline_script_texts(job))
    )
    if _DEPLOY_RUN_COMMAND_RE.search(run_text):
        return True
    labels = [str(job_id).lower()]
    job_name = job.get("name")
    if job_name:
        labels.append(str(job_name).lower())
    return any("deploy" in label for label in labels)


def _job_scoped_identity_refs(
    document: Mapping,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Every distinct client-id/creds reference from every ``azure/
    login`` step in this workflow, bucketed by whether *that step's own
    job* -- not the workflow as a whole -- is itself a deploy job:
    returns ``(deploy_identity_refs, non_deploy_identity_refs)``.

    A workflow can mix a build/test job with a separate deploy job side
    by side in the very same file; rule 6 must compare identities at
    this same job-scoped granularity, or a build/test job and a deploy
    job that happen to share the exact same identity within a single
    workflow file would never be caught -- the whole-workflow-level
    :attr:`WorkflowAssessment.is_deploy` flag folds both jobs'
    identity references into the very same bucket, since it only asks
    whether *some* job in the file deploys.
    """
    deploy_refs: List[str] = []
    other_refs: List[str] = []
    jobs = document.get("jobs")
    if isinstance(jobs, Mapping):
        for job_id, job in jobs.items():
            if not isinstance(job, Mapping):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            bucket = deploy_refs if _is_deploy_job(job_id, job) else other_refs
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                uses = str(step.get("uses", "")).split("@", 1)[0].strip().lower()
                if uses != "azure/login":
                    continue
                with_block = step.get("with")
                if not isinstance(with_block, Mapping):
                    continue
                for key in ("client-id", "creds"):
                    value = _with_input_value(with_block, key)
                    if value:
                        bucket.append(_redact_identity_ref(str(value)))
    return _dedupe_preserve_order(deploy_refs), _dedupe_preserve_order(other_refs)


def _job_environment_name(job: Mapping) -> Optional[str]:
    """The GitHub Environment name a single job declares via its own
    ``environment:`` key (a bare string, or a mapping's ``name:``), or
    ``None`` if the job declares no environment at all."""
    environment = job.get("environment")
    if isinstance(environment, Mapping):
        environment = environment.get("name")
    if isinstance(environment, str) and environment.strip():
        return environment.strip()
    return None


def _environment_scoped_identity_refs(
    document: Mapping,
) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Every ``(environment_name, identity_refs)`` pair from every job
    that declares *both* its own GitHub ``environment:`` name *and* at
    least one ``azure/login`` client-id/creds reference.

    Declaring a named ``environment:`` is itself unambiguous
    deployment-target evidence, independent of whether that job also
    separately satisfies the generic :func:`_is_deploy_job` name/uses
    heuristic -- a job named merely ``deploy`` with no declared
    environment tells rule 6 nothing about *which* environment tier it
    targets, while a job that explicitly says ``environment: production``
    (regardless of its own job id) does. Rule 6 needs this
    environment-tier-scoped view specifically to catch a production
    identity that is also used for a staging/development deploy, which
    the coarser build-vs-deploy bucketing in
    :func:`_job_scoped_identity_refs` cannot distinguish.
    """
    pairs: List[Tuple[str, Tuple[str, ...]]] = []
    jobs = document.get("jobs")
    if not isinstance(jobs, Mapping):
        return ()
    for job in jobs.values():
        if not isinstance(job, Mapping):
            continue
        environment_name = _job_environment_name(job)
        if environment_name is None:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        refs: List[str] = []
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            uses = str(step.get("uses", "")).split("@", 1)[0].strip().lower()
            if uses != "azure/login":
                continue
            with_block = step.get("with")
            if not isinstance(with_block, Mapping):
                continue
            for key in ("client-id", "creds"):
                value = _with_input_value(with_block, key)
                if value:
                    refs.append(_redact_identity_ref(str(value)))
        if refs:
            pairs.append((environment_name, tuple(_dedupe_preserve_order(refs))))
    return tuple(pairs)


def _normalize_indexed_github_expression(text: str) -> str:
    return _INDEXED_EXPRESSION_ACCESS_RE.sub(r".\1", text)


def _expression_is_trusted_checkout_context(inner: str) -> bool:
    if inner in _TRUSTED_CHECKOUT_EXPRESSIONS:
        return True
    return any(
        inner.startswith(prefix) for prefix in _TRUSTED_CHECKOUT_EXPRESSION_PREFIXES
    )


def _resolve_env_literal(
    document: Mapping, job: Mapping, step: Mapping, name: str
) -> Optional[str]:
    """The raw value declared for `env.NAME`, resolved from the
    *narrowest* scope that actually declares it -- this step's own
    `env:`, then its containing job's, then the workflow-level default
    -- or ``None`` if no scope declares it at all. The returned value
    may itself still be another expression (or a secret reference);
    the caller re-applies the same trust evaluation to it rather than
    assuming a resolved value is automatically safe.
    """
    for env in (step.get("env"), job.get("env"), document.get("env")):
        if isinstance(env, Mapping) and name in env:
            value = env.get(name)
            return None if value is None else str(value)
    return None


def _checkout_value_is_provably_trusted(
    value: str,
    document: Mapping,
    job: Mapping,
    step: Mapping,
    _depth: int = 0,
) -> bool:
    """True only if ``value`` (a `pull_request_target` checkout's own
    `ref:`/`repository:` input) can actually be shown to stay on the
    trusted base branch/repository -- fail-closed: an unrecognized
    dynamic expression this module cannot actually reason about is
    never assumed safe merely because it fails to match a known-bad
    marker.

    A plain literal string with no `${{ ... }}` expression at all is
    always trusted (it is whatever static text the workflow author
    hardcoded, not driven by pull-request event data). An expression is
    trusted only when *every* `${{ ... }}` segment it contains is
    itself one of a small set of GitHub contexts this module can
    actually reason about as always resolving to base-branch-scoped
    data (see :data:`_TRUSTED_CHECKOUT_EXPRESSIONS`/
    :data:`_TRUSTED_CHECKOUT_EXPRESSION_PREFIXES`), or a
    `${{ env.NAME }}` indirection whose own value is statically
    declared (in the step's, job's, or workflow's own `env:` block)
    and itself resolves to something provably trusted by this same
    rule, applied recursively. Every other expression -- referencing
    `inputs.*`, `vars.*`, `needs.*`, a function call, or an
    unresolvable/self-referential env indirection -- fails closed.
    """
    if _depth > 5:
        return False  # unbounded/self-referential env indirection: fail closed
    normalized = _normalize_indexed_github_expression(value)
    if any(marker in normalized for marker in _UNTRUSTED_CHECKOUT_REF_MARKERS):
        return False
    if "${{" not in normalized:
        return True
    segments = _EXPRESSION_SEGMENT_RE.findall(normalized)
    if not segments:
        return False  # malformed/unbalanced expression syntax: fail closed
    for inner in segments:
        inner = inner.strip()
        env_match = re.match(r"^env\.([A-Za-z_][A-Za-z0-9_]*)$", inner)
        if env_match:
            resolved = _resolve_env_literal(document, job, step, env_match.group(1))
            if resolved is None or not _checkout_value_is_provably_trusted(
                resolved, document, job, step, _depth + 1
            ):
                return False
            continue
        if not _expression_is_trusted_checkout_context(inner):
            return False
    return True


def _shell_operand_is_provably_trusted(
    value: str,
    document: Mapping,
    job: Mapping,
    step: Mapping,
    _depth: int = 0,
) -> bool:
    """True only if ``value`` -- as an actual POSIX shell executing a
    raw `git fetch`/`checkout`/`clone`/`pull` command's `run:` line
    would see it -- can be shown to stay on the trusted base branch/
    repository, fail-closed exactly like
    :func:`_checkout_value_is_provably_trusted`. Recognizes and
    resolves *both* forms a workflow author can freely mix within the
    very same shell command: a `${{ ... }}` GitHub-expression segment,
    and a raw shell `$VAR`/`${VAR}` variable reference -- since GitHub
    Actions itself feeds every declared `env:` entry to the runner
    shell as a real OS-level environment variable, `git fetch origin
    $PR_REF` is exactly as capable of touching untrusted PR-head
    content as `git fetch origin ${{ env.PR_REF }}`, just spelled with
    ordinary shell syntax instead of a GitHub expression. Either form
    is resolved from the same step/job/workflow `env:` scopes and its
    resolved value re-checked recursively by this same rule -- an
    unresolved variable name, or a resolved value that is itself
    untrusted or unrecognized (through further `${{ ... }}` or further
    chained `$VAR` syntax), fails closed.
    """
    if _depth > 5:
        return False  # unbounded/self-referential indirection: fail closed
    normalized = _normalize_indexed_github_expression(value)
    if any(marker in normalized for marker in _UNTRUSTED_CHECKOUT_REF_MARKERS):
        return False
    has_expression = "${{" in normalized
    shell_var_names = _shell_variable_reference_names(normalized)
    if not has_expression and not shell_var_names:
        return True  # plain literal text: never driven by PR event data
    if has_expression:
        segments = _EXPRESSION_SEGMENT_RE.findall(normalized)
        if not segments:
            return False  # malformed/unbalanced expression syntax: fail closed
        for inner in segments:
            inner = inner.strip()
            env_match = re.match(r"^env\.([A-Za-z_][A-Za-z0-9_]*)$", inner)
            if env_match:
                resolved = _resolve_env_literal(document, job, step, env_match.group(1))
                if resolved is None or not _shell_operand_is_provably_trusted(
                    resolved, document, job, step, _depth + 1
                ):
                    return False
                continue
            if not _expression_is_trusted_checkout_context(inner):
                return False
    for name in shell_var_names:
        resolved = _resolve_env_literal(document, job, step, name)
        if resolved is None or not _shell_operand_is_provably_trusted(
            resolved, document, job, step, _depth + 1
        ):
            return False
    return True


def _raw_git_checkout_line_is_untrusted(
    line: str, document: Mapping, job: Mapping, step: Mapping
) -> bool:
    """True if a single physical `run:` line naming a raw `git fetch`/
    `checkout`/`clone`/`pull` command (an equivalent checkout mechanism
    to `actions/checkout`'s own `ref:`/`repository:` inputs) can be
    shown to touch the pull request's own attacker-controlled head
    content or head repository fork -- fail-closed, exactly the same
    trust standard rule 1 already applies to `actions/checkout`'s own
    inputs, just scoped to this one line rather than the whole
    (possibly multi-line) `run:` block, to avoid an unrelated
    expression elsewhere in the same step's script incidentally
    tripping this check.

    A trailing shell comment on the line is stripped first (a bare
    ``#`` split, consistent with this module's existing conservative,
    non-shell-quote-aware comment handling) so an unrelated expression
    mentioned only in commentary can never affect the result. The line
    is then evaluated by :func:`_shell_operand_is_provably_trusted`,
    which resolves *both* `${{ env.NAME }}` GitHub-expression
    indirection *and* raw shell `$VAR`/`${VAR}` variable indirection
    against the step's/job's/workflow's own `env:` declarations,
    recursively; an unresolved name, or any other dynamic content this
    module cannot actually reason about as base-scoped, fails closed
    exactly like an unrecognized `actions/checkout` input does.
    """
    stripped_line = line.split("#", 1)[0]
    if not _UNTRUSTED_CHECKOUT_RUN_COMMAND_RE.search(
        _normalize_indexed_github_expression(stripped_line)
    ):
        return False
    return not _shell_operand_is_provably_trusted(stripped_line, document, job, step)


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

    An `actions/checkout` step's own `ref:`/`repository:` value is
    checked fail-closed via :func:`_checkout_value_is_provably_trusted`
    -- a dynamic expression is rejected unless it is actually shown to
    stay base-scoped, not merely because it fails to match a known-bad
    marker by name. The raw-git-command path applies the very same
    fail-closed standard, per :func:`_raw_git_checkout_line_is_untrusted`,
    scoped to just the physical line naming the git command (rather
    than the whole free-form `run:` block) -- including resolving any
    `${{ env.NAME }}` indirection used as a command operand -- so an
    unresolved or otherwise-unrecognized dynamic ref/repository operand
    fails closed exactly like it would for `actions/checkout` itself.
    """
    for job, step in _job_steps(document):
        uses = str(step.get("uses", "")).split("@", 1)[0].strip().lower()
        if uses == "actions/checkout":
            with_block = step.get("with")
            if isinstance(with_block, Mapping):
                for key in ("ref", "repository"):
                    raw_value = _with_input_value(with_block, key)
                    if not raw_value:
                        continue
                    if not _checkout_value_is_provably_trusted(
                        str(raw_value), document, job, step
                    ):
                        return True
        run_text = step.get("run")
        if isinstance(run_text, str):
            for line in run_text.splitlines():
                if _raw_git_checkout_line_is_untrusted(line, document, job, step):
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


# A step's/job's `if:` condition, expressed as raw GitHub Actions
# expression syntax, is normalized down to its innermost literal text by
# repeatedly stripping a matching `${{ ... }}` wrapper and/or matching
# quotes -- so `false`, `'false'`, `${{ false }}`, `${{ 'false' }}`, and
# even a doubly-wrapped `${{ 'false' }}` embedded in more `${{ }}` are
# all recognized as exactly the same statically-false condition.
_EXPRESSION_WRAPPER_RE = re.compile(r"^\$\{\{\s*(?P<inner>.*)\s*\}\}$", re.DOTALL)


def _normalize_static_if_condition(value: object) -> Optional[str]:
    """The innermost literal text of a step's/job's `if:` condition, with
    every layer of a `${{ ... }}` expression wrapper and/or matching
    quotes stripped -- or ``None`` if ``value`` is not itself a string
    (a bare YAML boolean is handled directly by the caller instead).
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    previous = None
    while previous != text:
        previous = text
        match = _EXPRESSION_WRAPPER_RE.match(text)
        if match:
            text = match.group("inner").strip()
            continue
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
            text = text[1:-1].strip()
    return text


def _is_statically_disabled(step: Mapping, job: Optional[Mapping]) -> bool:
    """True only if a step's own `if:` (or its containing job's `if:`) is
    a literal, statically-false condition -- YAML's bare `false`/`0`, an
    equivalent quoted string, or any of those wrapped in a GitHub Actions
    `${{ ... }}` expression (however many layers deep) -- never a guess
    about a dynamic expression this module cannot evaluate. A step or
    job with no `if:` key at all, or one whose condition depends on
    runtime context this module cannot resolve, is never treated as
    disabled: only a condition that can *never* evaluate true, however
    GitHub actually runs it, is excluded.
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
        text = _normalize_static_if_condition(candidate)
        if text is not None and text.lower() in ("false", "0"):
            return True
    return False


# Shell operators GitHub Actions' own `bash`/`sh` step shells use to
# chain, conditionally chain, or pipe multiple distinct commands
# together on a single `run:` line.
_SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|]")


def _shell_command_segments(line: str) -> List[str]:
    return [segment.strip() for segment in _SHELL_SEGMENT_SPLIT_RE.split(line)]


def _governance_relevant_run_text(run: str) -> str:
    """A step's own ``run:`` text, split into its individual shell
    command segments (at ``&&``/``||``/``;``/``|``), with a whole
    comment line, a trailing ``#``-comment fragment on a segment, and a
    bare output-only (``echo``/``printf``/``print(...)``) segment all
    stripped -- what remains is the only part of a step's command text
    rule 3's CTK/application-probe/eval-runner checks may ever treat as
    evidence something was actually invoked.

    Segment-level filtering matters because a line that does not itself
    *start* with ``echo``/``printf`` can still chain one in
    (``true && echo "python -m ctk run-vectors"``) purely to smuggle a
    marker word past a whole-line-only check without ever actually
    invoking anything; each segment on a line is therefore inspected,
    and filtered, entirely on its own.
    """
    kept_segments: List[str] = []
    for line in run.splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        for segment in _shell_command_segments(stripped_line):
            segment = segment.split("#", 1)[0].strip()
            if not segment or _INERT_RUN_LINE_RE.match(segment):
                continue
            kept_segments.append(segment)
    return "\n".join(kept_segments)


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


#: The exact workflow-level `env` key the Task 11 scaffold's own
#: ``governed-actions.yml.tmpl`` sets to mark itself as still an
#: unmodified, customer-unreviewed placeholder -- never present in any
#: workflow this module does not itself ship a template for, and never
#: matched against anything but this precise key/value pair (never a
#: comment, a job/step name, or any other free-text marker word).
_SCAFFOLD_UNMODIFIED_ENV_NAME = "GOVERNED_ACTIONS_SCAFFOLD_UNMODIFIED"
_SCAFFOLD_UNMODIFIED_ENV_VALUE = "true"


def _is_unmodified_scaffold_workflow(document: Mapping) -> bool:
    """True only if *document* still carries the Task 11 scaffold's own
    unmodified-marker: a workflow-level ``env.
    GOVERNED_ACTIONS_SCAFFOLD_UNMODIFIED`` set to the exact literal
    string ``"true"``.

    This is the *only* signal rule 3 ever uses to tell a still-
    scaffolded, customer-unreviewed workflow apart from a real one that
    happens to run the same recognized CTK/application-probe commands
    -- the scaffold's own `run:` lines are deliberately real, runnable
    commands (so the template can be wired up by simply deleting this
    one key), not a fake/broken placeholder that would fail rule 3 on
    its own merits regardless of whether a customer ever reviewed it.
    A customer wiring this workflow for real removes (or changes the
    value of) this key as part of actually reviewing what it runs, at
    which point this returns ``False`` again and the workflow is
    assessed exactly like any other pull_request-triggered workflow.
    """
    env = document.get("env")
    if not isinstance(env, Mapping):
        return False
    return env.get(_SCAFFOLD_UNMODIFIED_ENV_NAME) == _SCAFFOLD_UNMODIFIED_ENV_VALUE


def _ci_probes_static_status(document: Mapping, triggers: Tuple[str, ...]) -> Status:
    if "pull_request" not in triggers:
        return "pass"  # rule 3 only binds required (PR-triggered) CI
    if _is_unmodified_scaffold_workflow(document):
        # An unmodified copy of the Task 11 scaffold's own workflow
        # template must never itself count as CI-probe evidence: its
        # `run:` lines exist so the template is realistic and easy to
        # wire up, not because a customer has actually reviewed and
        # committed to running it. Every other still-scaffolded control
        # in this repository stays must-fix/not-verified until a
        # customer acts; rule 3 must not be the one exception that a
        # bare `--scaffold maf --confirm-scaffold` flips to pass.
        return "must-fix"
    # Deliberately searched against the actual, execution-reachable
    # `run:` command text only -- never a step `name`, a `uses:`
    # reference, a YAML comment, a disabled/always-false step, or a bare
    # `echo`/`printf` line: a step merely *named* "Run CTK" whose command
    # never runs it, a fixture docstring mentioning "CTK" while
    # describing why it is intentionally absent, an `if: false`-guarded
    # step, or an `echo "ctk application-probe"` line, must never count
    # as evidence a step actually runs one.
    text = "\n".join(_governance_relevant_run_command_texts(document))
    if _ci_probes_satisfied(text):
        return "pass"
    return "must-fix"


# ---------------------------------------------------------------------------
# Public: assess_workflow
# ---------------------------------------------------------------------------


def _infer_repo_root_from_workflow_path(path: Path) -> Optional[Path]:
    """The repository root implied by a workflow file's own conventional
    location (``<root>/.github/workflows/<name>.yml``) -- or ``None`` if
    ``path`` is not laid out this way, in which case a local reusable-
    workflow call from it simply cannot be resolved and is left
    unfollowed rather than guessed at.
    """
    workflows_dir = path.parent
    github_dir = workflows_dir.parent
    if workflows_dir.name != "workflows" or github_dir.name != ".github":
        return None
    return github_dir.parent


def assess_workflow(path: Path) -> WorkflowAssessment:
    """Assess a single GitHub Actions workflow file's static change-plane
    controls: PR-only deploy gating, explicit least-privilege permissions,
    SHA pinning, OIDC/WIF Azure login, and (for pull_request-triggered
    workflows) CTK/application-probe presence.
    """
    path = Path(path)
    document = _load_workflow_document(path)
    triggers = _trigger_names(document)
    is_deploy = _is_deploy_workflow(document, root=_infer_repo_root_from_workflow_path(path))
    sha_pins, sha_violations = _sha_pin_status(document)
    login_job_steps = _azure_login_job_steps(document)
    deploy_action_job_steps = _azure_deploy_action_job_steps(document)
    deploy_identity_refs, non_deploy_identity_refs = _job_scoped_identity_refs(document)
    environment_identity_refs = _environment_scoped_identity_refs(document)
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
        deploy_identity_refs=deploy_identity_refs,
        non_deploy_identity_refs=non_deploy_identity_refs,
        environment_identity_refs=environment_identity_refs,
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


def _parse_codeowners_entries(path: Path) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Every non-comment, non-blank CODEOWNERS line, in file order, as
    ``(pattern, owner_tokens)`` pairs -- including a line with *no* owner
    token at all, which is a valid CODEOWNERS shape that explicitly
    disowns any path it matches (an intentional "no one owns this"
    declaration, not a malformed line to discard).

    ``owner_tokens`` retains only the tokens following the pattern that
    are actually shaped like a real GitHub username/team (``@user``,
    ``@org/team``) or an email address, as a tuple rather than a
    collapsed boolean -- a later, narrower entry re-declaring the exact
    same owner set is not a real ownership change and must not be
    confused with one that disowns the path or reassigns it to someone
    else. A pattern followed by only unrecognized text still counts as
    a declared (ownerless) entry for last-match-wins purposes; it
    simply carries an empty owner-token tuple.
    """
    entries: List[Tuple[str, Tuple[str, ...]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        pattern, owners = tokens[0], tokens[1:]
        owner_tokens = tuple(
            owner for owner in owners if _CODEOWNERS_OWNER_TOKEN_RE.match(owner)
        )
        entries.append((pattern, owner_tokens))
    return tuple(entries)


def _codeowners_pattern_is_depth_unanchored(pattern: str) -> bool:
    """True if ``pattern`` is a *depth-unanchored* CODEOWNERS pattern --
    GitHub's own gitignore-style matching applies a pattern with no
    internal ``/`` (ignoring a leading ``**/`` and/or a trailing ``/``)
    at *every* depth in the repository tree, not merely at the
    repository root or one specific relative path. ``*.md``, a bare
    ``README.md``, and ``**/*.yml`` are all depth-unanchored this way;
    ``src/README.md`` and a leading-``/``-anchored ``/README.md`` are
    not. The bare repository-wide catch-all (``*``/``**``) is excluded
    here -- it is already handled as a universal ancestor by
    :func:`_codeowners_recursive_prefix`/:func:`_is_ancestor_or_equal`.
    """
    normalized = pattern.strip()
    if normalized.startswith("/"):
        return False
    while normalized.startswith("**/"):
        normalized = normalized[3:]
    normalized = normalized.rstrip("/")
    return "/" not in normalized and normalized not in ("", "*", "**")


def _codeowners_strip_leading_recursive_glob(pattern: str) -> str:
    """``pattern``, with an optional single leading ``/`` root anchor
    and every *repeated* leading ``**/`` segment stripped, plus any
    trailing ``/``.

    GitHub's own gitignore-style matching treats a leading ``**/`` as
    matching *zero or more* leading directory segments -- ``**/foo``
    matches a root-level ``foo`` exactly as readily as a deeply nested
    ``a/b/foo``. Every place this module derives a path or prefix from
    a declared pattern's own text for an ancestor/equality/overlap
    comparison needs that same "zero-or-more" allowance applied first,
    or the *shallowest* (zero-directory) match a leading ``**/``
    pattern is equally capable of would be silently missed.
    """
    normalized = pattern.strip().lstrip("/")
    while normalized.startswith("**/"):
        normalized = normalized[3:]
    return normalized.rstrip("/")


def _codeowners_pattern_basename_glob(pattern: str) -> str:
    return _codeowners_strip_leading_recursive_glob(pattern)


def _codeowners_anchored_pattern_regex(pattern: str) -> Optional["re.Pattern[str]"]:
    """Compile a root-anchored, `/`-bearing CODEOWNERS pattern (one
    with an internal path separator, such as ``.github/workflows/*.yml``
    or ``src/*/README.md``) into a regex matching a full repo-relative
    POSIX path, honoring gitignore-style wildcard semantics: a `*`
    matches any run of characters *within* one path segment only (it
    never crosses a `/`, so ``.github/workflows/*.yml`` never matches
    something nested one directory deeper), and a `?` matches exactly
    one such character. A literal `**` run is treated as an unbounded
    ``.*`` -- deliberately more permissive than strict segment-only
    gitignore ``**`` semantics -- so a pattern using it is never
    under-matched here; this module otherwise has no reason to treat a
    doubled wildcard as anything other than "matches more, not less"
    when the goal is conservatively detecting a possible last-match
    override, not precisely replicating full gitignore matching.

    A *leading* ``**/`` (after any repeated occurrence) is handled
    distinctly from one occurring elsewhere in the pattern: it compiles
    to an *optional* ``(?:.*/)?`` group rather than an unconditional
    ``.*`` immediately followed by a literal ``/`` -- gitignore's own
    leading ``**/`` matches *zero or more* leading directory segments,
    so ``**/.github/workflows/*.yml`` must match the exact root-level
    file ``.github/workflows/x.yml`` (zero leading segments) just as
    readily as a deeper ``vendor/.github/workflows/x.yml``; requiring
    a literal ``/`` unconditionally right after the leading ``**``
    would wrongly reject the very shallowest, most common case.

    Returns ``None`` for a pattern with no internal `/` at all *after*
    stripping any leading ``**/`` -- depth-unanchored basename patterns
    are matched separately, at any depth, via
    :func:`_codeowners_pattern_is_depth_unanchored` callers.
    """
    normalized = pattern.strip().lstrip("/").rstrip("/")
    leading_any_depth = False
    while normalized.startswith("**/"):
        leading_any_depth = True
        normalized = normalized[3:]
    if "/" not in normalized:
        return None
    parts = ["^"]
    if leading_any_depth:
        parts.append("(?:.*/)?")
    index = 0
    length = len(normalized)
    while index < length:
        char = normalized[index]
        if char == "*" and index + 1 < length and normalized[index + 1] == "*":
            parts.append(".*")
            index += 2
            continue
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
        index += 1
    parts.append("$")
    return re.compile("".join(parts))


def _codeowners_pattern_nested_within(pattern: str, required_prefix: str) -> bool:
    """True if a declared ``pattern`` is a proper subset of the directory
    tree named by ``required_prefix`` -- the reverse relationship of
    `_codeowners_pattern_covers`. Used to find a *later* entry that,
    under real last-match-wins resolution, carves an override out of an
    otherwise fully-owned required tree rather than one that covers the
    whole tree itself.

    A depth-unanchored pattern (``*.md``, ``**/*.yml``, a bare
    ``README.md``, ...) is conservatively always treated as a potential
    override: GitHub's own matching applies it at *any* depth,
    including deep inside this required tree, and this module cannot
    enumerate the tree's actual file contents from pattern text alone
    to rule that out.
    """
    if _codeowners_pattern_is_depth_unanchored(pattern):
        return True
    declared_prefix = _codeowners_recursive_prefix(pattern)
    if declared_prefix is not None:
        return declared_prefix != required_prefix and _is_ancestor_or_equal(
            required_prefix, declared_prefix
        )
    # An exact-file (or single-level glob) pattern still carves out a
    # hole if its own literal path falls inside the required tree -- a
    # leading `**/` is stripped first, since it can resolve to that
    # literal path with zero leading directories too.
    target = _codeowners_strip_leading_recursive_glob(pattern)
    return _is_ancestor_or_equal(required_prefix, target)


def _codeowners_requirement_owned(
    entries: Sequence[Tuple[str, Tuple[str, ...]]], requirement: str
) -> bool:
    """Whether ``requirement`` is genuinely owned once every declared
    CODEOWNERS entry is resolved in file order.

    Real CODEOWNERS resolution is last-match-wins *per file*, not per
    requirement pattern: for any given path, the *last* line in the
    file whose pattern matches it decides ownership (or the explicit
    lack of one). A broad entry covering the whole required tree is
    therefore not enough on its own -- a *later*, narrower entry
    nested inside that tree wins for every file it matches, so it can
    silently disown (or reassign) part of a tree this check would
    otherwise report as fully owned. Once the broad covering entry is
    found, every subsequent entry nested inside the required tree is
    inspected: if any of them declares a different owner set (an empty
    set counts as different from a non-empty one), the requirement can
    no longer be proven fully owned, however broad the covering entry
    looked in isolation. A narrower entry re-declaring the identical
    owner set is not an override in substance and does not break
    coverage; a narrower entry appearing *before* the covering entry is
    irrelevant, since the later broad entry already overrides it.
    """
    owned = False
    owner_tokens: Tuple[str, ...] = ()
    covering_index: Optional[int] = None
    for index, (pattern, entry_owner_tokens) in enumerate(entries):
        if _codeowners_pattern_covers(pattern, requirement):
            owned = bool(entry_owner_tokens)
            owner_tokens = entry_owner_tokens
            covering_index = index
    if not owned:
        return False

    required_prefix = _codeowners_recursive_prefix(requirement)
    if required_prefix is None:
        # An exact file requirement has no descendant subtree a later,
        # narrower entry could carve a hole out of.
        return True

    covering_owner_set = frozenset(owner_tokens)
    for pattern, entry_owner_tokens in entries[covering_index + 1 :]:
        if not _codeowners_pattern_nested_within(pattern, required_prefix):
            continue
        if frozenset(entry_owner_tokens) != covering_owner_set:
            return False
    return True


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
    ancestor of every path. A leading, repeated ``**/`` is stripped
    first too -- it matches zero or more leading directory segments, so
    ``**/tests/**`` covers exactly the same root-relative ``"tests"``
    tree ``tests/**`` does (plus additional, irrelevant nested-elsewhere
    matches no required pattern here would ever ask about).
    """
    normalized = pattern.strip().lstrip("/")
    if normalized in ("", "*", "**"):
        return ""
    while normalized.startswith("**/"):
        normalized = normalized[3:]
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
    # literal pattern, by a recursive directory glob that is an
    # ancestor of it, by a depth-unanchored basename pattern whose glob
    # matches the exact file's own basename (GitHub's own
    # gitignore-style matching applies such a pattern at any depth,
    # including to a single exact required file), or by an anchored,
    # `/`-bearing nested-path glob (``.github/workflows/*.yml``,
    # ``**/.github/workflows/*.yml``) whose own wildcarded path
    # matches the exact file's full path.
    target = requirement.strip().lstrip("/")
    if declared_prefix is not None:
        return _is_ancestor_or_equal(declared_prefix, target)
    if _codeowners_pattern_is_depth_unanchored(declared_pattern):
        basename_glob = _codeowners_pattern_basename_glob(declared_pattern)
        if fnmatch.fnmatch(target.rsplit("/", 1)[-1], basename_glob):
            return True
    else:
        nested_pattern_regex = _codeowners_anchored_pattern_regex(declared_pattern)
        if nested_pattern_regex is not None and nested_pattern_regex.match(target):
            return True
    return _codeowners_strip_leading_recursive_glob(declared_pattern) == target



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


def _looks_like_arm_template(path: Path) -> bool:
    """True if ``path`` -- sniffed for only its first
    :data:`_ARM_TEMPLATE_SNIFF_BYTES` bytes, never fully parsed -- looks
    like a genuine Azure Resource Manager (ARM) JSON template: one
    declaring the ``$schema`` every real ARM template carries. Guessing
    from a `.json` file's name or directory alone would false-positive
    on every unrelated JSON file in the repository; this content sniff
    is deliberately narrow instead.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(_ARM_TEMPLATE_SNIFF_BYTES)
    except OSError:
        return False
    return bool(_ARM_TEMPLATE_SCHEMA_RE.search(head))


def _looks_like_kubernetes_manifest(path: Path) -> bool:
    """True if ``path`` -- sniffed for only its first
    :data:`_KUBERNETES_MANIFEST_SNIFF_BYTES` bytes, never fully parsed --
    declares both ``apiVersion:`` and ``kind:`` at the start of a line,
    the two keys every genuine Kubernetes manifest carries. Guessing
    from a `.yml`/`.yaml` file's name or directory alone would false-
    positive on every unrelated YAML file in the repository (including
    this module's own GitHub Actions workflow fixtures); this content
    sniff is deliberately narrow instead.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(_KUBERNETES_MANIFEST_SNIFF_BYTES)
    except OSError:
        return False
    return bool(
        _KUBERNETES_MANIFEST_API_VERSION_RE.search(head)
        and _KUBERNETES_MANIFEST_KIND_RE.search(head)
    )


def _discover_infrastructure_codeowners_requirements(root: Path) -> Tuple[str, ...]:
    """Every additional CODEOWNERS requirement this repository's own,
    actually-present infrastructure/Infrastructure-as-Code (IaC) surface
    -- or recognized deployment descriptor -- calls for: deterministic
    and conservative. A fixed set of conventional top-level directory
    names (:data:`_INFRASTRUCTURE_DIRECTORY_NAMES`) is checked for
    existence at the repository root only; a root-level Azure Developer
    CLI manifest (:data:`_DEPLOYMENT_MANIFEST_ROOT_FILENAMES`) is
    likewise checked only at the root; and the whole tree (excluding
    hidden and vendored/scratch directories, per
    :data:`_EXCLUDED_DIR_NAMES`) is scanned for an individual Terraform/
    Bicep file (:data:`_IAC_FILE_SUFFIXES`), a genuine ARM JSON template
    (sniffed via :func:`_looks_like_arm_template`), a Dockerfile variant
    (:data:`_DOCKERFILE_NAME_RE`), a Helm chart's own
    :data:`_HELM_CHART_FILENAME`, or a genuine Kubernetes manifest
    (sniffed via :func:`_looks_like_kubernetes_manifest`) -- each
    reported as its own containing top-level directory (or, for a file
    sitting directly at the repository root, that exact file's own
    name) so CODEOWNERS coverage can be required for precisely the
    infrastructure/deployment surface this repository actually
    declares.

    Returns an empty tuple -- never inventing a requirement -- for a
    repository with no infrastructure/IaC/deployment-descriptor surface
    at all; a category this repository has no matching directory or
    file for is simply absent from the result, not silently required
    anyway.
    """
    if not root.is_dir():
        return ()
    discovered: Set[str] = set()
    for name in _INFRASTRUCTURE_DIRECTORY_NAMES:
        if (root / name).is_dir():
            discovered.add(f"{name}/**")
    for name in _DEPLOYMENT_MANIFEST_ROOT_FILENAMES:
        if (root / name).is_file():
            discovered.add(name)
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if any(
            part.startswith(".") or part in _EXCLUDED_DIR_NAMES
            for part in relative.parts
        ):
            continue
        name_lower = candidate.name.lower()
        is_relevant = any(name_lower.endswith(suffix) for suffix in _IAC_FILE_SUFFIXES)
        if not is_relevant and name_lower.endswith(".json"):
            is_relevant = _looks_like_arm_template(candidate)
        if not is_relevant and _DOCKERFILE_NAME_RE.match(candidate.name):
            is_relevant = True
        if not is_relevant and name_lower == _HELM_CHART_FILENAME:
            is_relevant = True
        if not is_relevant and name_lower.endswith((".yml", ".yaml")):
            is_relevant = _looks_like_kubernetes_manifest(candidate)
        if not is_relevant:
            continue
        if len(relative.parts) == 1:
            discovered.add(relative.as_posix())
        else:
            discovered.add(f"{relative.parts[0]}/**")
    return tuple(sorted(discovered))


def _path_token_pattern(relative_path: Path) -> "re.Pattern[str]":
    token = re.escape(relative_path.as_posix())
    return re.compile(rf"(?<![\w./-]){token}(?![\w./-])")


def _eval_runner_referenced(
    pr_workflows: Sequence["WorkflowAssessment"], eval_directories: Set[Path]
) -> bool:
    """True only if a pull_request-triggered workflow's actual,
    execution-reachable ``run:`` command text invokes a *recognized*
    test/eval runner against one of ``eval_directories`` as a whole,
    boundary-delimited path token -- never merely because the word
    "evals" (or an unrelated path that happens to contain it) appears
    anywhere in the document, never from a disabled/always-false step
    or a comment/echo-only line, and never from a command that merely
    lists, prints, or searches the path (``ls evals``, ``cat
    evals/x.py``, ``find evals -name ...``) without ever actually
    executing anything against it.
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
        _eval_directory_invoked_by_recognized_runner(run_text, directory)
        for directory in eval_directories
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
    """True only if live evidence *explicitly, completely* confirms every
    one of a deploy job's own declared GitHub Environments is itself
    protected.

    Checked only when at least one environment is actually declared: a
    workflow that names no environment has nothing here to confirm.
    Once at least one environment *is* declared, though, this can never
    default to "confirmed" merely because live evidence is silent about
    it -- an absent ``live_github``, a missing ``environments`` mapping,
    a declared environment name missing from that mapping, or an entry
    that does not itself explicitly say ``protected: true`` are all
    exactly the same "not actually confirmed" gap, not proof of
    anything: a provenance-free or incomplete mapping can never be
    accepted as confirmation any more than live evidence GitHub itself
    reported as unprotected can.
    """
    if not declared_environments:
        return True
    if not live_github:
        return False
    environments = live_github.get("environments")
    if not isinstance(environments, Mapping):
        return False
    for name in declared_environments:
        entry = environments.get(name)
        if not isinstance(entry, Mapping) or entry.get("protected") is not True:
            return False
    return True


def _job_run_texts(job: Mapping) -> List[str]:
    """A single job's own execution-reachable ``run:`` command texts --
    excluding a disabled/always-false step and comment/echo-only lines
    -- factored out so both CTK/application-probe presence and eval-
    runner presence can be checked against exactly the same text for
    exactly the same job.
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
    return texts


def _job_satisfies_ci_probes(job: Mapping) -> bool:
    """True only if this specific job's own steps -- never a sibling
    job's -- actually run both a CTK and an application probe, using
    the same execution-reachability rules as `_ci_probes_static_status`
    (excluding a disabled/always-false step and comment/echo-only
    lines).
    """
    text = "\n".join(_job_run_texts(job))
    return _ci_probes_satisfied(text)


def _job_references_eval(job: Mapping, eval_directories: Set[Path]) -> bool:
    """True only if this specific job's own execution-reachable ``run:``
    text invokes a *recognized* test/eval runner against one of
    ``eval_directories`` as a whole, boundary-delimited path token --
    mirrors `_eval_runner_referenced`, but scoped to a single job so the
    eval suite's own gating job can be identified separately from
    whichever job runs CTK/application probes, when a repo splits the
    two across sibling jobs in the same workflow.
    """
    if not eval_directories:
        return False
    text = "\n".join(_job_run_texts(job))
    return any(
        _eval_directory_invoked_by_recognized_runner(text, directory)
        for directory in eval_directories
    )


def _required_check_job_roles(
    assessments: Tuple["WorkflowAssessment", ...], eval_directories: Set[Path]
) -> List[Tuple[Set[str], Set[str]]]:
    """One entry per pull_request-triggered, fully-passing workflow that
    satisfies rule 3's required CI: a ``(ctk_probe_names,
    eval_names)`` pair, where ``ctk_probe_names`` is every job id/name
    within that workflow whose own steps satisfy CTK + application
    probe (alternatives -- naming any one of them gates on that role),
    and ``eval_names`` is every job id/name whose own steps reference
    the repo's eval suite runner (also alternatives; empty when the
    repo ships no eval suite).

    A branch-protection required-status-check list only actually
    enforces this workflow's real gating CI when it names *both*
    roles: at least one CTK/application-probe job name, *and* -- if
    the repo ships an eval suite -- at least one eval-referencing job
    name too. A repo that splits its eval suite into a separate job
    from its CTK/application-probe job must have *both* job names
    required, never just one; a repo that runs everything in one
    combined job satisfies both roles by naming that single job, since
    its name then appears in both sets.
    """
    roles: List[Tuple[Set[str], Set[str]]] = []
    for assessment in assessments:
        if "pull_request" not in assessment.triggers:
            continue
        if assessment.ci_probes != "pass":
            continue
        document = _load_workflow_document(assessment.path)
        jobs = document.get("jobs")
        if not isinstance(jobs, Mapping):
            continue
        ctk_probe_names: Set[str] = set()
        eval_names: Set[str] = set()
        for job_id, job in jobs.items():
            if not isinstance(job, Mapping):
                continue
            names = {str(job_id).strip().lower()}
            if job.get("name"):
                names.add(str(job["name"]).strip().lower())
            if _job_satisfies_ci_probes(job):
                ctk_probe_names.update(names)
            if _job_references_eval(job, eval_directories):
                eval_names.update(names)
        if not ctk_probe_names:
            continue
        if eval_directories and not eval_names:
            # This workflow satisfies CTK/application-probe, but no job
            # within it references the repo's own eval suite anywhere --
            # `_satisfies_required_ci` already excludes this workflow
            # from static rule 3 for the same reason; it must never
            # contribute a required-check role here either, since naming
            # its CTK/application-probe job alone could never actually
            # gate on the eval suite too.
            continue
        roles.append((ctk_probe_names, eval_names if eval_directories else set()))
    return roles


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
    required_check_roles: Sequence[Tuple[Set[str], Set[str]]],
    declared_environments: Set[str] = frozenset(),
) -> bool:
    """True only if live evidence proves the default branch is
    *substantively* protected -- reviews required (from real,
    API-shaped data: CODEOWNER review explicitly required *and* a
    positive required approving-review count), required checks naming
    *every* job this repo's real gating CI actually needs enforced,
    admins not exempt, force pushes disallowed, and no named bypass
    allowance -- plus, "as available", that any GitHub Environment a
    deploy job actually uses is itself reported protected. A
    required-status-check list that merely names *some* check, that
    names a workflow's CTK/application-probe job but not its separate
    eval-suite job, a protection rule that exempts admins or still
    allows a forced push, or a `required_pull_request_reviews` object
    that never actually turns on CODEOWNER review or requires at least
    one approval, can never be resolved into a `pass` -- each is
    exactly the kind of gap static files alone could never see, and
    this function exists precisely so live evidence -- when supplied --
    is actually held to that full standard rather than a partial one.

    ``required_pull_request_reviews`` must itself be GitHub's own
    nested object shape -- a bare boolean (``true``/``false``), however
    a simplified test fixture might report it, is never itself
    structured evidence that CODEOWNER review and a positive required
    approval count are actually turned on, and is always rejected
    (fails closed) rather than accepted at face value.
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
    if not isinstance(reviews, Mapping):
        return False
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
    # A required-status-check list can name *some* check without naming
    # the one(s) that actually run this repo's real gating CI -- every
    # role (CTK/application-probe, and the eval suite's own job when the
    # repo ships one) a qualifying workflow needs must itself be named,
    # not merely one of them; that gap can never be resolved by
    # inferring a `pass` -- it stays not-verified unless a genuinely
    # complete gating job set is actually enforced.
    if not required_check_roles:
        return False
    if not any(
        (ctk_probe_names & context_names)
        and (not eval_names or (eval_names & context_names))
        for ctk_probe_names, eval_names in required_check_roles
    ):
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

    ``deploy_identities``/``other_identities`` are already in this
    module's own canonical reference form (see
    :func:`_canonicalize_github_expression_reference`); the supplied
    mapping's own keys are canonicalized the same way before lookup,
    so a caller keying that mapping with an incidental whitespace or
    bracket-vs-dot spelling variant of the same reference still
    resolves correctly rather than silently missing the match.
    """
    if not live_azure:
        return None
    identity_principal_ids = live_azure.get("identity_principal_ids")
    if not isinstance(identity_principal_ids, Mapping):
        return None
    if not deploy_identities or not other_identities:
        return None
    canonical_principal_ids = {
        _canonicalize_github_expression_reference(str(key)) or key: principal
        for key, principal in identity_principal_ids.items()
    }
    deploy_principals = {canonical_principal_ids.get(ref) for ref in deploy_identities}
    other_principals = {canonical_principal_ids.get(ref) for ref in other_identities}
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


def _sanitize_workflow_error_text(root: Path, path: Path, message: str) -> str:
    """A caught ``ChangePlaneError``'s own message, with every occurrence
    of this workflow file's absolute filesystem path -- and its
    resolved form, if it differs -- replaced by its repository-relative
    location. `_load_workflow_document`'s own error messages (and the
    lower-level ``OSError``/``yaml.YAMLError`` text they wrap) embed
    whatever path this module happened to read the file from; a parse
    or read failure must never leak the assessment host's own directory
    layout into a retained finding any more than a successful read
    does.
    """
    sanitized = message
    relative = _rel_unresolved(root, path)
    for absolute_form in {str(path), str(Path(path).absolute())}:
        if absolute_form and absolute_form != relative:
            sanitized = sanitized.replace(absolute_form, relative)
    try:
        resolved_form = str(path.resolve())
    except OSError:
        resolved_form = ""
    if resolved_form and resolved_form != relative:
        sanitized = sanitized.replace(resolved_form, relative)
    return sanitized


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
        reason = _sanitize_workflow_error_text(root, path, str(error))
        return _unreadable_workflow_assessment(path, reason), False


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


# ---------------------------------------------------------------------------
# Task 8: optional, read-only live GitHub/Azure evidence collection.
#
# Everything below runs exactly the fixed, read-only commands documented on
# each function -- never a mutation, never a secret -- through an injected
# *run* callable this module never calls directly as a real subprocess; a
# real caller wires *run* to an actual ``gh``/``az`` invocation (for example
# ``subprocess.run``), and every unit test wires a scripted double instead.
# A command runner is any callable accepting a command list and returning an
# object exposing ``.returncode``/``.stdout`` (``subprocess.CompletedProcess``
# satisfies this without adaptation).
# ---------------------------------------------------------------------------

CommandRunner = Callable[[Sequence[str]], object]


@dataclass(frozen=True)
class LiveEvidenceResult:
    """The outcome of one optional, read-only live-evidence collection
    attempt.

    *data* is the already-redacted, canonicalizable evidence this
    collection actually observed. *evidence* is always empty: neither
    :func:`collect_live_github` nor :func:`collect_live_azure` is ever
    handed a local checkout to resolve a real, 40-hex ``source_commit``
    from (their signatures take only a repository/subscription
    identifier), so there is no trustworthy provenance either could ever
    bind an :class:`EvidenceRef` to -- and this module must never
    fabricate one merely to have something to return. A caller that
    also holds real repository provenance may build its own
    :class:`EvidenceRef` around this result's *data*; that is
    deliberately left to the caller. *finding* is ``None`` on a fully
    successful collection (exactly like every other "pass" outcome in
    this module, which never manufactures a finding for a control that
    fully checks out) and otherwise the single ``"not-verified"``
    :class:`Finding` recording, without ever exposing raw command
    output, why the affected GHCP control cannot be confirmed live.
    """

    status: Status
    data: Mapping[str, object]
    evidence: Tuple[EvidenceRef, ...]
    finding: Optional[Finding]


#: Hard upper bound on a read-only command's stdout, in bytes, this module
#: will ever attempt to parse as JSON. This is a bounded-external-result
#: guard, not a business policy: no real ``gh``/``az`` read-only response
#: this module queries is ever legitimately this large, so anything past it
#: is treated as unusable (and never even handed to ``json.loads``) rather
#: than risking pathological parse/canonicalization cost.
_MAX_LIVE_COMMAND_STDOUT_BYTES = 2_000_000


def _run_read_only_command(
    run: CommandRunner, command: List[str]
) -> Tuple[Optional[object], Optional[str], Optional[int]]:
    """Run *command* through the injected *run* callable and parse its
    stdout as JSON.

    Returns ``(payload, None, exit_code)`` on success or
    ``(None, error_class, exit_code)`` on failure. *error_class* is
    always one of a small, fixed set of labels (``"missing-cli"``,
    ``"timeout"``, ``"cli-error"``, ``"malformed-json"``) -- never the
    command's raw stderr text -- so a failure can be reported without
    ever exposing whatever the real CLI actually printed (which could
    itself carry a token, an internal hostname, or other operator-only
    detail). *exit_code* is the real, numeric process exit code
    whenever one actually exists (``None`` only when the command could
    never even start, i.e. a missing CLI binary or a timeout) --
    redacting stderr must never also discard the one CLI-failure detail
    (the exit code itself) that carries no secret and is genuinely
    useful for triage.

    Only the runner-boundary failures a real command runner (for
    example one wired to ``subprocess.run``) can actually declare are
    ever caught here: a missing CLI binary (``FileNotFoundError``), a
    runner-enforced timeout (``subprocess.TimeoutExpired``), any other
    declared subprocess failure (``subprocess.SubprocessError``), or a
    lower-level OS failure (``OSError``). A programmer error in *run*
    that is none of these is never silently swallowed here.

    A successful response is still only ever as usable as it is
    actually well-formed: stdout larger than
    :data:`_MAX_LIVE_COMMAND_STDOUT_BYTES`, text that is not valid JSON
    (or is nested/circular deeply enough to exhaust recursion), and a
    value that parses as JSON but cannot itself be canonicalized (for
    example a non-finite ``NaN``/``Infinity`` float ``json.loads``
    itself would otherwise silently accept) are all reported the same
    ``"malformed-json"`` way -- never partially trusted merely because
    parsing itself happened to succeed.
    """
    try:
        completed = run(command)
    except FileNotFoundError:
        return None, "missing-cli", None
    except subprocess.TimeoutExpired:
        return None, "timeout", None
    except subprocess.SubprocessError:
        return None, "cli-error", None
    except OSError:
        return None, "cli-error", None
    exit_code = getattr(completed, "returncode", None)
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        exit_code = None
    if exit_code != 0:
        return None, "cli-error", exit_code
    stdout = getattr(completed, "stdout", "")
    if isinstance(stdout, (str, bytes)) and len(stdout) > _MAX_LIVE_COMMAND_STDOUT_BYTES:
        return None, "malformed-json", exit_code
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError, RecursionError):
        return None, "malformed-json", exit_code
    try:
        canonical.canonical_bytes(payload)
    except canonical.CanonicalizationError:
        return None, "malformed-json", exit_code
    return payload, None, exit_code


def _command_failure_detail(error_class: str, exit_code: Optional[int]) -> str:
    """A single, secret-free phrase describing *why* a read-only
    command's result could not be used -- the numeric *exit_code*
    (never raw stderr) plus the fixed *error_class* label."""
    if exit_code is None:
        return f"{error_class} (no process exit code available)"
    return f"{error_class}, exit code {exit_code}"


def _is_json_list(payload: object) -> bool:
    return isinstance(payload, list)


def _is_json_mapping(payload: object) -> bool:
    return isinstance(payload, Mapping)


def _is_nonempty_json_mapping(payload: object) -> bool:
    """Whether *payload* is a JSON object with at least one field -- a
    real GitHub API response for the endpoints this guards is never
    legitimately an empty ``{}``; an empty mapping proves nothing and
    must never be treated as complete evidence merely because it parsed
    as valid JSON.
    """
    return isinstance(payload, Mapping) and len(payload) > 0


def _is_json_list_of_mappings(payload: object) -> bool:
    """Whether *payload* is a JSON array whose every element is itself a
    JSON object -- the shape every real GitHub/Azure "list" response
    uses for its entries. An empty list is still accepted here (zero
    rulesets, zero federated credentials, etc. is a legitimate real
    state); only an element that is not itself an object is rejected.
    """
    return isinstance(payload, list) and all(isinstance(entry, Mapping) for entry in payload)


def _github_not_verified(
    finding_id: str,
    repository: str,
    error_class: str,
    exit_code: Optional[int],
) -> LiveEvidenceResult:
    detail = _command_failure_detail(error_class, exit_code)
    finding = Finding(
        finding_id=finding_id,
        status="not-verified",
        phase="pre-deploy",
        plane="change",
        reason_code="github-live-evidence-unavailable",
        summary=(
            "Live GitHub evidence could not be collected for the "
            "assessed repository."
        ),
        details=(
            f"A read-only 'gh api' call against {repository} failed "
            f"({detail}); {finding_id} cannot be confirmed from live "
            "GitHub state and is reported not-verified rather than pass."
        ),
    )
    return LiveEvidenceResult(
        status="not-verified",
        data={"error_class": error_class, "exit_code": exit_code},
        evidence=(),
        finding=finding,
    )


#: A GitHub Environment's real ``protection_rules`` entries are objects
#: naming a ``type``. Only these two are actual authorization/deployment
#: -branch restrictions -- ``required_reviewers`` (an explicit human
#: approval gate) and ``branch_policy`` (a deployment-branch restriction).
#: ``wait_timer`` is a mere delay with no approval or restriction of any
#: kind, and is deliberately excluded: this project never treats a
#: wait-timer-only environment as "protected". Any rule ``type`` this
#: project does not itself recognize -- including any future GitHub rule
#: type -- fails closed the same way: it is simply never counted toward
#: protection, rather than being guessed at either way.
_RESTRICTIVE_ENVIRONMENT_PROTECTION_RULE_TYPES = frozenset(
    {"required_reviewers", "branch_policy"}
)


def _environment_protection_rule_is_restrictive(rule: object) -> bool:
    """Whether a single ``protection_rules`` entry is itself recognized,
    real evidence of an authorization/deployment-branch restriction --
    never an invented reviewer identity or approval-count threshold, just
    whether GitHub's own ``type`` field names one of the two rule kinds
    that actually restrict who/what can deploy. A non-mapping entry, or
    one missing/naming an unrecognized ``type``, is never restrictive."""
    if not isinstance(rule, Mapping):
        return False
    return rule.get("type") in _RESTRICTIVE_ENVIRONMENT_PROTECTION_RULE_TYPES


def _github_environment_protection(payload: object) -> Dict[str, object]:
    """Transform GitHub's real ``GET /repos/{repo}/environments`` response
    shape (``{"environments": [{"name": ..., "protection_rules": [...]}]}``)
    into the ``{name: {"protected": bool}}`` mapping ``assess_change_plane``
    already consumes as ``live_github["environments"]``.

    "Protected" is never inferred from a merely non-empty
    ``protection_rules`` list -- a ``wait_timer`` entry is only a
    deployment delay, not any actual authorization control, so a
    wait-timer-only (or empty, or entirely unrecognized-rule-type) list
    is always ``protected: False``. Only a recognized restrictive rule
    (``required_reviewers`` or ``branch_policy``; see
    ``_RESTRICTIVE_ENVIRONMENT_PROTECTION_RULE_TYPES``) makes an entry
    ``protected: True`` -- a direct, non-fabricated transform of what
    GitHub itself reported, never an invented reviewer identity or
    threshold. An unrecognized rule ``type`` fails closed the same way
    an absent rule does: it is simply never counted toward protection.
    Any environment entry this function cannot recognize (no string
    ``name``) is skipped rather than guessed at; the caller validates
    the top-level shape before this is ever invoked, so a malformed
    *payload* is never silently accepted here either.
    """
    environments: Dict[str, object] = {}
    entries = payload.get("environments") if isinstance(payload, Mapping) else None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            rules = entry.get("protection_rules")
            protected = isinstance(rules, list) and any(
                _environment_protection_rule_is_restrictive(rule) for rule in rules
            )
            environments[name] = {"protected": protected}
    return environments


def _validate_github_environments_payload(payload: object) -> bool:
    """Whether *payload* is a usable ``GET /repos/{repo}/environments``
    response: a mapping with an ``environments`` list, and -- unlike a
    transform that would merely skip an entry it cannot recognize -- a
    single unrecognized entry (not itself an object, a blank/non-string
    ``name``, or a non-list ``protection_rules`` when present) fails the
    *entire* payload. An empty ``environments`` list is still accepted
    (a repository can legitimately have zero environments configured);
    only an entry that is present but malformed is ever rejected.
    """
    if not isinstance(payload, Mapping):
        return False
    entries = payload.get("environments")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, Mapping):
            return False
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            return False
        protection_rules = entry.get("protection_rules")
        if protection_rules is not None and not isinstance(protection_rules, list):
            return False
    return True


def _validate_github_actions_permissions_payload(payload: object) -> bool:
    """Whether *payload* is a usable ``GET
    /repos/{repo}/actions/permissions/workflow`` response: a nonempty
    mapping naming a nonblank string ``default_workflow_permissions`` --
    the one field every real response of this endpoint always carries,
    and the field GHCP-002's least-privilege workflow-permissions check
    actually needs.
    """
    if not _is_nonempty_json_mapping(payload):
        return False
    permissions = payload.get("default_workflow_permissions")
    return isinstance(permissions, str) and bool(permissions)


def _validate_github_oidc_customization_payload(payload: object) -> bool:
    """Whether *payload* is a usable ``GET
    /repos/{repo}/actions/oidc/customization/sub`` response: a nonempty
    mapping naming an ``include_claim_keys`` list -- the one field every
    real response of this endpoint always carries, and the field
    GHCP-005's OIDC/WIF subject-claim check actually needs. An empty
    ``include_claim_keys`` list is still accepted (GitHub's own default
    is an empty customization); only a missing or non-list value is
    rejected.
    """
    if not _is_nonempty_json_mapping(payload):
        return False
    return isinstance(payload.get("include_claim_keys"), list)


#: GitHub's own documented default ``per_page`` for the two endpoints
#: :func:`collect_live_github` calls that can genuinely paginate
#: (``rulesets`` and ``environments``) -- not a number this project
#: invented. This collector never adds a ``per_page``/``page`` query
#: parameter or a follow-up page request (the five-command contract is
#: fixed), so a bare list sitting at exactly this many entries can never
#: be told apart from a silently truncated first page, and is
#: conservatively treated as unproven rather than trusted.
_GITHUB_DEFAULT_PAGE_SIZE = 30


def _rulesets_page_is_complete(payload: object) -> bool:
    """Whether a rulesets list already known to be a JSON array of
    objects can be proven to be the *entire* list rather than a
    possibly-truncated first page.

    The rulesets endpoint's response is a bare JSON array with no
    ``total_count`` (or any other) completeness envelope, so the only
    signal available here is size: a list strictly shorter than
    GitHub's own default page size could not have been truncated by
    pagination (a real API never returns a partial page followed by a
    "no more pages" state without also filling that page first), so it
    is proven complete; a list at or above that size cannot be told
    apart from a truncated first page and is conservatively reported
    incomplete instead of guessed at as proof of a real, larger state.
    """
    return isinstance(payload, list) and len(payload) < _GITHUB_DEFAULT_PAGE_SIZE


def _environments_page_is_complete(payload: object) -> bool:
    """Whether an environments response already known to be a usable
    mapping with an ``environments`` list can be proven complete.

    GitHub's real ``GET /repos/{repo}/environments`` response also
    reports its own ``total_count`` of environments across every page;
    when that field is present as an integer it is this endpoint's own
    explicit completeness signal and is trusted over the page-size
    heuristic below -- the returned list is complete only when
    ``total_count`` exactly matches how many entries were actually
    returned (naming *more* than were returned proves this is only a
    partial page; naming fewer is just as unusable as any other
    unrecognized/inconsistent shape). When ``total_count`` is absent or
    not an integer, this falls back to the same default-page-size
    heuristic :func:`_rulesets_page_is_complete` uses.
    """
    if not isinstance(payload, Mapping):
        return False
    entries = payload.get("environments")
    if not isinstance(entries, list):
        return False
    total_count = payload.get("total_count")
    if isinstance(total_count, int) and not isinstance(total_count, bool):
        return total_count == len(entries)
    return len(entries) < _GITHUB_DEFAULT_PAGE_SIZE


def _always_complete_page(payload: object) -> bool:
    """Completeness check for the three ``collect_live_github`` endpoints
    that never paginate at all: each returns exactly one JSON object,
    never a list, so there is no "first page" to distrust."""
    return True


#: Each of ``collect_live_github``'s five endpoints, in call order, paired
#: with the GHCP control its live evidence affects, the shape validator
#: proving its response is actually usable (not merely valid JSON), and a
#: completeness check proving a validated response is not merely an
#: unproven/possibly-truncated first page. Only the OIDC subject-
#: customization endpoint speaks to GHCP-005 (Azure OIDC/WIF trust, from
#: the GitHub side); every other endpoint speaks to GHCP-002 (branch
#: protection / required reviews / rulesets) -- matching exactly the two
#: controls the design's collector contract ever attributes a GitHub
#: live-evidence gap to.
_GITHUB_STEPS: Tuple[Tuple[str, str, str, Callable[[object], bool], Callable[[object], bool]], ...] = (
    ("rulesets", "rulesets?includes_parents=true", "GHCP-002", _is_json_list_of_mappings, _rulesets_page_is_complete),
    ("branch_protection", "branches/{default_branch}/protection", "GHCP-002", _is_nonempty_json_mapping, _always_complete_page),
    ("actions_permissions_workflow", "actions/permissions/workflow", "GHCP-002", _validate_github_actions_permissions_payload, _always_complete_page),
    ("environments", "environments", "GHCP-002", _validate_github_environments_payload, _environments_page_is_complete),
    ("oidc_customization_sub", "actions/oidc/customization/sub", "GHCP-005", _validate_github_oidc_customization_payload, _always_complete_page),
)


def _sorted_by_field(
    entries: Sequence[Mapping[str, object]], field: str
) -> List[Mapping[str, object]]:
    """Return *entries* (each already known to be a JSON mapping) sorted
    into one deterministic, fully reproducible order.

    Primarily sorted by *field* whenever a given entry names it as a
    nonblank string; an entry missing that field (or naming it with
    something other than a nonblank string) sorts after every entry
    that has one. Every entry's own canonical JSON bytes are *always*
    appended as a secondary tie-breaker -- even among entries that share
    the exact same *field* value -- so two entries with a duplicate
    ``name``/``roleDefinitionName`` (but different other content) are
    never left to fall back on Python's merely input-order-stable sort:
    without that tie-breaker, two live API responses that return the
    same *set* of elements in a different transient order could still
    produce a different sorted order (and therefore a different digest)
    whenever any duplicate field value is present. This makes a
    live-evidence digest invariant to whatever transient order the live
    API itself happened to return elements in, and makes it change only
    when the actual *set* of elements genuinely changes.
    """

    def _key(entry: Mapping[str, object]) -> Tuple[int, str, str]:
        value = entry.get(field)
        canonical_repr = canonical.canonical_bytes(entry).decode("utf-8", "replace")
        if isinstance(value, str) and value:
            return (0, value, canonical_repr)
        return (1, "", canonical_repr)

    return sorted(entries, key=_key)


def _with_collected_digest(data: Mapping[str, object]) -> Dict[str, object]:
    """Return a copy of *data* with a deterministic ``collected_sha256``
    integrity digest added -- a canonical SHA-256 hash of *data* itself
    (never including the digest, since it is computed before the digest
    key is added), so any later re-collection with the exact same live
    state reproduces the exact same digest, and any change to that state
    changes it. The digest is payload-free (a hash, never the underlying
    values) and exposes nothing beyond what *data* already carries.
    """
    digest = canonical.sha256_hex(canonical.canonical_bytes(data))
    return {**data, "collected_sha256": f"sha256:{digest}"}


def collect_live_github(
    repository: str, default_branch: str, run: CommandRunner
) -> LiveEvidenceResult:
    """Collect optional, read-only live GitHub evidence for *repository*
    at *default_branch*.

    Runs exactly five ``gh api`` read commands, in this fixed order,
    through the injected *run* callable:

    1. ``gh api repos/{repository}/rulesets?includes_parents=true``
    2. ``gh api repos/{repository}/branches/{default_branch}/protection``
    3. ``gh api repos/{repository}/actions/permissions/workflow``
    4. ``gh api repos/{repository}/environments``
    5. ``gh api repos/{repository}/actions/oidc/customization/sub``

    Collection stops at the first command that fails -- a 401/403
    surfaces from ``gh`` as a non-zero exit, a missing ``gh`` binary as
    ``FileNotFoundError``, and each is reported the same "not-verified"
    way -- and also stops at the first response that parses as JSON but
    is not shaped (and, where the endpoint's real API response always
    carries a required field, not *populated*) the way that endpoint's
    real GitHub API response always is: for example rulesets that is
    not a JSON array of objects, a branch-protection or workflow-
    permissions response that is an empty ``{}``, a workflow-permissions
    response missing its own ``default_workflow_permissions`` string, an
    environments response with even one entry missing a nonblank
    ``name``, or an OIDC customization response missing its own
    ``include_claim_keys`` list. An incomplete or unrecognized payload
    is exactly as unusable as no payload at all, and must never be
    reported ``"pass"`` merely because it happened to parse. Every
    failure is attributed to whichever GHCP control that specific
    endpoint's live evidence affects (GHCP-002 for every endpoint except
    the OIDC subject-customization one, which affects GHCP-005), never
    blurred into a single generic control. This function never records
    a raw stderr string -- only a small fixed error-class label and the
    real numeric process exit code.

    ``rulesets`` is sorted by name (falling back to each entry's own
    canonical bytes when unnamed) before it is stored or hashed, so the
    returned evidence -- and its ``collected_sha256`` digest -- is
    invariant to whatever transient order the live API happened to
    return elements in, and changes only when the actual live state
    does. The returned *data* also names the *repository*/
    *default_branch* scope this collection is bound to, so the digest
    itself is bound to that scope and can never be silently reused
    across a different repository or branch.

    This function issues exactly the five commands above -- no
    pagination flag or follow-up page request is ever added, matching
    this project's fixed live-command contract. Both ``rulesets`` and
    ``environments`` can in principle paginate on a real repository with
    an unusually large number of entries, and ``gh api`` still returns
    only that endpoint's first page here; rather than guessing whether
    that page is the whole live state, this function actively proves it
    from whatever completeness signal the endpoint itself offers
    (``environments``'s own ``total_count``, or -- when that signal is
    unavailable -- a bare list's length against GitHub's own documented
    default page size). A page that cannot be proven complete this way
    is reported ``"not-verified"``/``"incomplete-first-page"`` exactly
    like an outright command failure or a malformed shape (see above),
    so a truncated live response can never be mistaken for full live
    verification of a control.
    """
    collected: Dict[str, object] = {}
    for key, endpoint_suffix, finding_id, validate, is_complete in _GITHUB_STEPS:
        endpoint = f"repos/{repository}/{endpoint_suffix.format(default_branch=default_branch)}"
        payload, error_class, exit_code = _run_read_only_command(run, ["gh", "api", endpoint])
        if error_class is not None:
            return _github_not_verified(finding_id, repository, error_class, exit_code)
        if not validate(payload):
            return _github_not_verified(finding_id, repository, "malformed-shape", exit_code)
        if not is_complete(payload):
            return _github_not_verified(finding_id, repository, "incomplete-first-page", exit_code)
        collected[key] = payload

    data: Dict[str, object] = {
        "repository": repository,
        "default_branch": default_branch,
        "rulesets": _sorted_by_field(collected["rulesets"], "name"),
        "branch_protection": {default_branch: collected["branch_protection"]},
        "actions_permissions_workflow": collected["actions_permissions_workflow"],
        "environments": _github_environment_protection(collected["environments"]),
        "oidc_customization_sub": collected["oidc_customization_sub"],
    }
    return LiveEvidenceResult(status="pass", data=_with_collected_digest(data), evidence=(), finding=None)


def _azure_not_verified(
    finding_id: str,
    reason_code: str,
    detail_prefix: str,
    error_class: str,
    exit_code: Optional[int] = None,
) -> LiveEvidenceResult:
    detail = (
        error_class
        if error_class == "missing-input"
        else _command_failure_detail(error_class, exit_code)
    )
    finding = Finding(
        finding_id=finding_id,
        status="not-verified",
        phase="pre-deploy",
        plane="change",
        reason_code=reason_code,
        summary=(
            "Live Azure identity/role evidence could not be collected for "
            "the assessed deployment identity."
        ),
        details=f"{detail_prefix} ({detail}); {finding_id} cannot be confirmed from live Azure state and is reported not-verified rather than pass.",
    )
    return LiveEvidenceResult(
        status="not-verified",
        data={"error_class": error_class, "exit_code": exit_code},
        evidence=(),
        finding=finding,
    )


#: A conservative *technical* safety bound on how many distinct unique
#: role names (and therefore how many ``az role definition list``
#: commands) :func:`collect_live_azure` will ever fan out to for one
#: identity's role assignments -- never a customer-facing governance
#: policy on how many roles a deployment identity may legitimately hold.
#: A real, well-scoped staging deployment identity is never assigned
#: anywhere near this many distinct roles in one resource group; a
#: result that names more than this is itself an anomaly this bounded
#: collector cannot safely process without risking runaway command
#: fan-out against whatever ``run`` callable the caller injected, so it
#: is reported not-verified *before* a single additional command for it
#: is ever issued.
_MAX_AZURE_ROLE_DEFINITION_LOOKUPS = 50


def _validate_role_assignment_role_names(role_assignments: object) -> Optional[Tuple[str, ...]]:
    """The unique, sorted role names a real ``az role assignment list``
    response actually names -- or ``None`` when *role_assignments* is not
    a JSON array, contains an entry that is not itself a JSON object, or
    contains an entry whose ``roleDefinitionName`` is missing, blank, or
    not a string. Any of these is treated as an incomplete result, never
    silently skipped and treated as "no role to look up": a caller that
    cannot enumerate every real role assigned to the identity can never
    prove least-privilege/identity-separation from what it did receive.
    """
    if not isinstance(role_assignments, list):
        return None
    names: Set[str] = set()
    for entry in role_assignments:
        if not isinstance(entry, Mapping):
            return None
        name = entry.get("roleDefinitionName")
        if not isinstance(name, str) or not name:
            return None
        names.add(name)
    return tuple(sorted(names))


def _matching_role_definition(role_name: str, definition: object) -> Optional[Mapping[str, object]]:
    """The single role-definition object a real ``az role definition
    list --name {role_name}`` response names for *role_name* -- or
    ``None`` when *definition* is not a JSON array, does not contain
    *exactly* one entry, that one entry is not itself a JSON object, or
    that entry's own ``roleName`` does not match *role_name*. Azure's
    real CLI, queried by exact ``--name``, always resolves to precisely
    one matching definition for a role that genuinely exists; anything
    else (zero matches, more than one, or a mismatched name) is an
    ambiguous or incomplete result that can never prove what
    *role_name* actually grants.
    """
    if not isinstance(definition, list) or len(definition) != 1:
        return None
    entry = definition[0]
    if not isinstance(entry, Mapping) or entry.get("roleName") != role_name:
        return None
    return entry


def collect_live_azure(
    subscription: str,
    resource_group: str,
    deploy_identity: str,
    run: CommandRunner,
) -> LiveEvidenceResult:
    """Collect optional, read-only live Azure evidence proving
    *deploy_identity*'s federated-credential (OIDC/WIF) trust and its
    actual role assignments in *resource_group*.

    Runs, in order, through the injected *run* callable:

    1. ``az identity federated-credential list --identity-name
       {deploy_identity} --resource-group {resource_group} --subscription
       {subscription} -o json``
    2. ``az role assignment list --assignee {deploy_identity}
       --resource-group {resource_group} --subscription {subscription}
       --all -o json``
    3. ``az role definition list --name {role_name} --subscription
       {subscription} -o json`` -- once per unique role name the
       assignment list returned, in sorted order.

    Every command is a read-only ``list`` call; none can mutate or carry
    a secret. A missing *subscription*/*resource_group*/*deploy_identity*
    is reported ``"not-verified"`` before any command runs at all (there
    is nothing safe to query). A federated-credential-list command that
    fails outright (non-zero exit, missing CLI, unparseable output), or
    that succeeds but returns a payload that is not a JSON array of
    objects the way Azure's real CLI output always is, is attributed to
    GHCP-005 -- either way the OIDC/WIF evidence that control needs is
    unusable, and an incomplete result is exactly as unusable as no
    result at all. A role-assignment-list or role-definition-list
    command that fails outright, or that succeeds but returns a payload
    that is not shaped the way Azure's real CLI output always is (not a
    JSON array, an assignment entry that is not itself a JSON object, a
    missing/blank/non-string ``roleDefinitionName``, or a role-
    definition-list response that does not resolve to *exactly one*
    matching definition object naming the same role), is attributed to
    GHCP-006 (least-privilege / identity-separation evidence): an
    incomplete, ambiguous, or unrecognized result can never prove
    identities are actually separate and least-privileged, so it must
    never be reported "pass". A role-assignment list naming more unique
    roles than :data:`_MAX_AZURE_ROLE_DEFINITION_LOOKUPS` -- a
    conservative *technical* fan-out safety bound this function enforces
    before issuing a single role-definition-list command, never an
    invented governance policy -- is likewise attributed to GHCP-006 and
    reported not-verified. This function never records a raw stderr
    string or any command argument beyond the identifiers the caller
    supplied -- only a small fixed error-class label and the real
    numeric process exit code.

    ``federated_credentials`` and ``role_assignments`` are each sorted
    (by ``name``/``roleDefinitionName`` respectively, falling back to
    each entry's own canonical bytes when unnamed) before they are
    stored or hashed, so the returned evidence -- and its
    ``collected_sha256`` digest -- is invariant to whatever transient
    order the live API happened to return elements in, and changes only
    when the actual live state does. The returned *data* also names the
    *subscription*/*resource_group*/*deploy_identity* scope this
    collection is bound to, so the digest itself is bound to that scope
    and can never be silently reused across a different identity or
    subscription.
    """
    if not subscription or not resource_group or not deploy_identity:
        return _azure_not_verified(
            "GHCP-006",
            "azure-live-inputs-missing",
            "collect_live_azure requires a non-empty subscription, "
            "resource group, and deploy identity before any Azure command "
            "can safely be issued",
            "missing-input",
        )

    federated_credentials, error_class, exit_code = _run_read_only_command(
        run,
        [
            "az", "identity", "federated-credential", "list",
            "--identity-name", deploy_identity,
            "--resource-group", resource_group,
            "--subscription", subscription,
            "-o", "json",
        ],
    )
    if error_class is not None:
        return _azure_not_verified(
            "GHCP-005",
            "azure-federated-credential-evidence-unavailable",
            "A read-only 'az identity federated-credential list' call failed",
            error_class,
            exit_code,
        )
    if not _is_json_list_of_mappings(federated_credentials):
        return _azure_not_verified(
            "GHCP-005",
            "azure-federated-credential-evidence-malformed",
            "A read-only 'az identity federated-credential list' call "
            "returned a response that was not the expected JSON array of objects",
            "malformed-shape",
            exit_code,
        )

    role_assignments, error_class, exit_code = _run_read_only_command(
        run,
        [
            "az", "role", "assignment", "list",
            "--assignee", deploy_identity,
            "--resource-group", resource_group,
            "--subscription", subscription,
            "--all",
            "-o", "json",
        ],
    )
    if error_class is not None:
        return _azure_not_verified(
            "GHCP-006",
            "azure-role-assignment-evidence-unavailable",
            "A read-only 'az role assignment list' call failed",
            error_class,
            exit_code,
        )
    role_names = _validate_role_assignment_role_names(role_assignments)
    if role_names is None:
        return _azure_not_verified(
            "GHCP-006",
            "azure-role-assignment-evidence-malformed",
            "A read-only 'az role assignment list' call returned a "
            "response that was not the expected JSON array of objects "
            "each naming a role",
            "malformed-shape",
            exit_code,
        )
    if len(role_names) > _MAX_AZURE_ROLE_DEFINITION_LOOKUPS:
        return _azure_not_verified(
            "GHCP-006",
            "azure-role-definition-fanout-exceeded",
            "The role assignment list named more unique roles than this "
            "collector's bounded role-definition-lookup fan-out safety "
            f"limit allows ({len(role_names)} > "
            f"{_MAX_AZURE_ROLE_DEFINITION_LOOKUPS})",
            "excessive-fanout",
        )

    role_definitions: Dict[str, object] = {}
    for role_name in role_names:
        definition, error_class, exit_code = _run_read_only_command(
            run,
            [
                "az", "role", "definition", "list",
                "--name", role_name,
                "--subscription", subscription,
                "-o", "json",
            ],
        )
        if error_class is not None:
            return _azure_not_verified(
                "GHCP-006",
                "azure-role-definition-evidence-unavailable",
                f"A read-only 'az role definition list' call for role "
                f"{role_name!r} failed",
                error_class,
                exit_code,
            )
        matching_definition = _matching_role_definition(role_name, definition)
        if matching_definition is None:
            return _azure_not_verified(
                "GHCP-006",
                "azure-role-definition-evidence-malformed",
                f"A read-only 'az role definition list' call for role "
                f"{role_name!r} did not resolve to exactly one matching "
                "role-definition object",
                "malformed-shape",
                exit_code,
            )
        role_definitions[role_name] = matching_definition

    observed_scopes = []
    observed_identities = []
    identity_fields = {
        "agent_name", "agent_version", "image_digest", "policy_digest", "environment",
    }
    for record in federated_credentials:
        resource_id = record.get("id")
        identity_match = re.fullmatch(
            r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/"
            r"Microsoft\.ManagedIdentity/userAssignedIdentities/([^/]+)/"
            r"federatedIdentityCredentials/[^/]+/?",
            resource_id, re.I,
        ) if isinstance(resource_id, str) else None
        if identity_match:
            observed_identities.append({"deploy_identity": identity_match[1]})
    for record in federated_credentials + role_assignments:
        scopes = [record[field] for field in ("id", "scope") if field in record]
        for scope in scopes or [None]:
            match = re.match(
                r"^/subscriptions/([^/]+)(?:/resourceGroups/([^/]+))?(?:/|$)",
                scope, re.I,
            ) if isinstance(scope, str) else None
            observed_scopes.append(
                {"subscription": match[1], **({"resource_group": match[2]} if match[2] else {})}
                if match else {}
            )
        for values in (record, record.get("properties", {})):
            if isinstance(values, Mapping):
                identity = {key: values[key] for key in identity_fields if key in values}
                if identity:
                    observed_identities.append(identity)
    data: Dict[str, object] = {
        "selected_scope": {
            "subscription": subscription,
            "resource_group": resource_group,
            "deploy_identity": deploy_identity,
        },
        "observed_scopes": sorted(observed_scopes, key=canonical.canonical_bytes),
        "observed_identities": sorted(observed_identities, key=canonical.canonical_bytes),
        "federated_credentials": _sorted_by_field(federated_credentials, "name"),
        "role_assignments": _sorted_by_field(role_assignments, "roleDefinitionName"),
        "role_definitions": role_definitions,
    }
    return LiveEvidenceResult(status="pass", data=_with_collected_digest(data), evidence=(), finding=None)

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
    required_patterns = _REQUIRED_CODEOWNERS_PATTERNS + (
        _discover_infrastructure_codeowners_requirements(root)
    )
    missing = tuple(
        requirement
        for requirement in required_patterns
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

    eval_suite_files = _discover_eval_suite_files(root)
    eval_directories = (
        _eval_suite_directories(root, eval_suite_files) if eval_suite_files else set()
    )
    if _branch_protection_confirmed(
        live_github,
        _required_check_job_roles(assessments, eval_directories),
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
    if offenders:
        controls["ghcp_azure_oidc"] = "must-fix"
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
        return
    # A workflow this module could tell deploys to Azure, but with no
    # visible `azure/login` step at all, is never itself a `must-fix` --
    # absence of a secret-shaped credential is not proof of OIDC/WIF, it
    # is equally consistent with a federated identity this static scan
    # simply cannot see. It must never be silently rolled up into an
    # aggregate `pass` either: a single per-workflow `not-verified`
    # anywhere in the set means this repo's own Azure-OIDC posture as a
    # whole is not-verified, not confirmed passing.
    unverified = tuple(a for a in assessments if a.oidc_wif == "not-verified")
    if unverified:
        controls["ghcp_azure_oidc"] = "not-verified"
        findings.append(
            Finding(
                finding_id="GHCP-005",
                status="not-verified",
                phase="pre-deploy",
                plane="change",
                reason_code="azure-login-not-verified-statically",
                summary="Azure deployment uses a long-lived secret instead of OIDC/WIF.",
                details=(
                    "One or more workflows demonstrably deploy to Azure "
                    "(a known Azure deploy action, or a raw `az`/`azd` CLI "
                    "deploy command) yet declare no visible `azure/login` "
                    "step confirming how they authenticate; static files "
                    "alone can never prove OpenID Connect / workload "
                    "identity federation is what is actually used."
                ),
                affected_paths=tuple(_rel(root, a.path) for a in unverified),
            )
        )
        return
    controls["ghcp_azure_oidc"] = "pass"


# --- GHCP-006: build/test/deploy identity separation -------------------

# GitHub Environment names recognized as the "production" tier and the
# "non-production" (staging/development) tier respectively -- narrow and
# fixed deliberately: an unrecognized or custom environment name (e.g.
# `"qa"`, `"uat"`, a team-specific name) must never be *guessed* into
# either tier, per this check's own conservatism requirement. A name
# outside both sets simply never participates in this specific
# production-vs-non-production comparison at all.
_PRODUCTION_ENVIRONMENT_NAMES: frozenset = frozenset({"production", "prod"})
_LOWER_ENVIRONMENT_NAMES: frozenset = frozenset(
    {"staging", "stage", "development", "dev"}
)


def _environment_tier(name: str) -> Optional[str]:
    """``"production"``, ``"non-production"``, or ``None`` for a GitHub
    Environment name outside both recognized tiers -- an unrecognized or
    custom name is deliberately left unclassified rather than guessed
    into either tier."""
    normalized = name.strip().lower()
    if normalized in _PRODUCTION_ENVIRONMENT_NAMES:
        return "production"
    if normalized in _LOWER_ENVIRONMENT_NAMES:
        return "non-production"
    return None


def _assess_identity_separation(
    assessments: Tuple[WorkflowAssessment, ...],
    live_azure: Optional[Mapping],
    findings: List[Finding],
    controls: Dict[str, "Status | bool"],
) -> None:
    production_identities: Set[str] = set()
    non_production_identities: Set[str] = set()
    for a in assessments:
        for environment_name, refs in a.environment_identity_refs:
            tier = _environment_tier(environment_name)
            if tier == "production":
                production_identities.update(refs)
            elif tier == "non-production":
                non_production_identities.update(refs)

    environment_tier_shared = production_identities & non_production_identities
    if environment_tier_shared:
        controls["ghcp_identity_separation"] = "must-fix"
        findings.append(
            Finding(
                finding_id="GHCP-006",
                status="must-fix",
                phase="pre-deploy",
                plane="change",
                reason_code="production-environment-identity-overlap",
                summary=(
                    "Build/test/deploy identities are shared, over-broad, or "
                    "not evidenced."
                ),
                details=(
                    "A production-environment deploy identity is also used "
                    "for a staging/development-environment deploy: "
                    f"{', '.join(sorted(environment_tier_shared))}."
                ),
            )
        )
        return

    deploy_identities = {
        ref for a in assessments for ref in a.deploy_identity_refs
    }
    other_identities = {
        ref for a in assessments for ref in a.non_deploy_identity_refs
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
