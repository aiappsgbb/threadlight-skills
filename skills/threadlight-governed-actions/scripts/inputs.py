"""Explicit input resolution for threadlight-governed-actions.

Locates every repository-relative input the assessor is allowed to read for a
given lifecycle phase, without ever inferring one input's presence from
another's, without ever guessing at optional live evidence, and without ever
returning an empty, success-shaped result when a discovered input turns out
to be unsafe or unreadable.

Nine input categories are discovered, each restricted to an explicit,
narrow rule so that "discovery" never silently widens into "anything under
the repository":

- ``spec`` — ``specs/SPEC.md``, required for the ``design`` and
  ``pre-deploy`` phases (its absence there is a missing prerequisite, not an
  empty inventory — see :func:`resolve_inputs`);
- ``registries`` — the same registry filenames Task 2's action inventory
  reads (``agent.yaml``, ``agent.yml``, ``tool-registry.json``,
  ``tool_registry.json``), directly under the target root;
- ``runtime_files`` — every ``*.py`` file under the target, excluding any
  hidden directory and any vendored/scratch tree (a virtual environment,
  ``node_modules``, ``site-packages``, ``__pycache__``, ``build``,
  ``dist``);
- ``policy_files`` — the same policy globs Task 2 reads
  (``governance/**/*.json|yaml|yml``, ``policies/**/*.json|yaml|yml``);
- ``approval_files`` — a subset of ``runtime_files`` whose AST contains one
  of the approval symbols ``issue_approval``, ``consume_approval``,
  ``redeem_approval``, or ``ApprovalHandler`` (as a function/class
  definition, a call target, an attribute access, or an import);
- ``test_and_report_files`` — every file under ``tests/**``,
  ``**/conformance/**``, or ``**/evals/**``;
- ``workflow_files`` — ``.github/workflows/*.yml`` and
  ``.github/workflows/*.yaml`` (direct children only);
- ``ownership_files`` — both CODEOWNERS locations GitHub recognizes for a
  root-level or ``.github``-scoped ownership file: ``CODEOWNERS`` and
  ``.github/CODEOWNERS``;
- ``dependency_files`` — ``pyproject.toml``, ``requirements*.txt``,
  ``uv.lock``, ``poetry.lock``, ``pdm.lock``, ``package.json``, and the
  common Node lockfiles (``package-lock.json``, ``npm-shrinkwrap.json``,
  ``yarn.lock``).

Every discovered candidate is passed through :func:`allowlisted_evidence_path`
before it is trusted: an absolute path, a ``..`` traversal, a symlink that
resolves outside the target root, an unreadable file, or a payload-bearing
JSON evidence file all raise :class:`InputResolutionError` naming the exact
repository-relative path and the finding IDs that category feeds — a parse
failure never gets silently dropped from an otherwise "successful" result.

Live evidence (GitHub branch/ruleset/required-check/environment state, Azure
role assignment and federated identity state) is out of scope for this
module entirely; ``resolve_inputs`` records both as always-unavailable
entries in ``missing_capabilities`` rather than omitting them, since a
missing key would read as "not applicable" instead of "not attempted here".
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Set, Tuple

import canonical
import contracts
from contracts import Phase


class InputResolutionError(ValueError):
    """Raised when a required input is missing or cannot be trusted.

    Examples: ``specs/SPEC.md`` absent for ``design``/``pre-deploy``; no
    registry or Python tool declaration at all for ``pre-deploy``; a
    discovered evidence candidate that is absolute, escapes the target root
    (directly or via a symlink), cannot be read, or (for JSON evidence)
    fails to parse or carries payload-bearing content. Callers must fix the
    target or the input, not have the resolver paper over it.
    """


@dataclass(frozen=True)
class ResolvedInputs:
    spec: Optional[Path]
    registries: Tuple[Path, ...]
    runtime_files: Tuple[Path, ...]
    policy_files: Tuple[Path, ...]
    approval_files: Tuple[Path, ...]
    test_and_report_files: Tuple[Path, ...]
    workflow_files: Tuple[Path, ...]
    ownership_files: Tuple[Path, ...]
    dependency_files: Tuple[Path, ...]
    missing_capabilities: Mapping[str, Tuple[str, ...]]


# ---------------------------------------------------------------------------
# Shared constants (kept in lockstep with Task 2's inventory.py)
# ---------------------------------------------------------------------------

_REGISTRY_FILENAMES: Tuple[str, ...] = (
    "agent.yaml",
    "agent.yml",
    "tool-registry.json",
    "tool_registry.json",
)

_POLICY_GLOBS: Tuple[str, ...] = (
    "governance/**/*.json",
    "governance/**/*.yaml",
    "governance/**/*.yml",
    "policies/**/*.json",
    "policies/**/*.yaml",
    "policies/**/*.yml",
)

_EXCLUDED_DIR_NAMES: frozenset = frozenset(
    {"venv", "node_modules", "site-packages", "__pycache__", "build", "dist"}
)

_APPROVAL_SYMBOLS: frozenset = frozenset(
    {"issue_approval", "consume_approval", "redeem_approval", "ApprovalHandler"}
)

_TEST_AND_REPORT_GLOBS: Tuple[str, ...] = (
    "tests/**/*",
    "**/conformance/**/*",
    "**/evals/**/*",
)

_OWNERSHIP_LOCATIONS: Tuple[str, ...] = ("CODEOWNERS", ".github/CODEOWNERS")

_DEPENDENCY_FIXED_NAMES: Tuple[str, ...] = (
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
)

# Which finding IDs each input category feeds, surfaced in
# InputResolutionError messages so a broken/unsafe input names the checks it
# blocks rather than just its own path.
_CATEGORY_FINDING_IDS: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "spec": ("ACT-001", "ACT-002"),
        "registries": ("ACT-001", "ACT-002"),
        "runtime_files": ("ACT-001", "MED-001", "MED-002"),
        "policy_files": ("MED-001", "ENF-001"),
        "approval_files": ("APR-001",),
        "test_and_report_files": ("PIN-001", "GHCP-003"),
        "workflow_files": ("GHCP-001", "GHCP-003", "GHCP-004"),
        "ownership_files": ("GHCP-002",),
        "dependency_files": ("PIN-001",),
    }
)

# GitHub/Azure live evidence is never gathered by this module; both entries
# are always present (never omitted) so a caller can see exactly what
# resolve_inputs did not attempt, rather than reading a missing key as "not
# applicable".
_MISSING_CAPABILITIES: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "live_github": (
            "branch protection/ruleset state, required-check state, "
            "environment protection, and workflow identity claims require a "
            "live GitHub query; resolve_inputs only resolves local "
            "repository-relative inputs",
        ),
        "live_azure": (
            "role assignment and federated identity configuration require a "
            "live Azure query; resolve_inputs only resolves local "
            "repository-relative inputs",
        ),
    }
)


def _is_vendored_or_hidden(relative: Path) -> bool:
    """True if any path segment is hidden (``.``-prefixed) or vendored/scratch.

    Mirrors ``inventory._is_excluded_python_path`` so a phantom file living
    in a virtual environment, ``node_modules``, ``site-packages``,
    ``__pycache__``, ``build``, or ``dist`` — or under any hidden directory
    such as ``.git``/``.venv`` — is never treated as a trustworthy input,
    regardless of which category is discovering it.
    """
    return any(
        part.startswith(".") or part in _EXCLUDED_DIR_NAMES
        for part in relative.parts
    )


# ---------------------------------------------------------------------------
# Per-category discovery (each returns repository-relative paths only)
# ---------------------------------------------------------------------------


def _discover_registries(root: Path) -> Tuple[Path, ...]:
    found = [
        Path(name) for name in _REGISTRY_FILENAMES if (root / name).is_file()
    ]
    return tuple(sorted(found, key=lambda p: p.as_posix()))


def _discover_python_runtime_files(root: Path) -> Tuple[Path, ...]:
    found = []
    for candidate in sorted(root.rglob("*.py")):
        relative = candidate.relative_to(root)
        if _is_vendored_or_hidden(relative):
            continue
        found.append(relative)
    return tuple(found)


def _discover_policy_files(root: Path) -> Tuple[Path, ...]:
    found: Set[Path] = set()
    for pattern in _POLICY_GLOBS:
        for match in root.glob(pattern):
            if not match.is_file():
                continue
            relative = match.relative_to(root)
            if _is_vendored_or_hidden(relative):
                continue
            found.add(relative)
    return tuple(sorted(found, key=lambda p: p.as_posix()))


def _contains_approval_symbol(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in _APPROVAL_SYMBOLS:
                return True
        elif isinstance(node, ast.Name):
            if node.id in _APPROVAL_SYMBOLS:
                return True
        elif isinstance(node, ast.Attribute):
            if node.attr in _APPROVAL_SYMBOLS:
                return True
        elif isinstance(node, ast.alias):
            if node.name in _APPROVAL_SYMBOLS or (
                node.asname is not None and node.asname in _APPROVAL_SYMBOLS
            ):
                return True
    return False


def _discover_approval_files(
    root: Path, python_files: Iterable[Path]
) -> Tuple[Path, ...]:
    """Return the subset of *python_files* whose AST names an approval symbol.

    A file that cannot even be parsed (syntax error, unreadable, undecodable)
    is silently excluded here rather than treated as a match — it simply
    cannot be proven to declare an approval symbol, the same posture Task 2
    takes for its own AST-based tool discovery. It remains discoverable
    (and, if unsafe, rejected) via ``runtime_files``.
    """
    found = []
    for relative in python_files:
        absolute = root / relative
        try:
            source = absolute.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(absolute))
        except (OSError, SyntaxError, UnicodeDecodeError, MemoryError, RecursionError):
            continue
        if _contains_approval_symbol(tree):
            found.append(relative)
    return tuple(found)


def _discover_test_and_report_files(root: Path) -> Tuple[Path, ...]:
    found: Set[Path] = set()
    for pattern in _TEST_AND_REPORT_GLOBS:
        for match in root.glob(pattern):
            if not match.is_file():
                continue
            relative = match.relative_to(root)
            if _is_vendored_or_hidden(relative):
                continue
            found.add(relative)
    return tuple(sorted(found, key=lambda p: p.as_posix()))


def _discover_workflow_files(root: Path) -> Tuple[Path, ...]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return ()
    found = []
    for pattern in ("*.yml", "*.yaml"):
        for match in workflows_dir.glob(pattern):
            if match.is_file():
                found.append(match.relative_to(root))
    return tuple(sorted(found, key=lambda p: p.as_posix()))


def _discover_ownership_files(root: Path) -> Tuple[Path, ...]:
    found = [
        Path(location)
        for location in _OWNERSHIP_LOCATIONS
        if (root / location).is_file()
    ]
    return tuple(found)


def _discover_dependency_files(root: Path) -> Tuple[Path, ...]:
    found: Set[Path] = set()
    for name in _DEPENDENCY_FIXED_NAMES:
        if (root / name).is_file():
            found.add(Path(name))
    for match in root.glob("requirements*.txt"):
        if match.is_file():
            found.add(match.relative_to(root))
    return tuple(sorted(found, key=lambda p: p.as_posix()))


def _validate_and_collect(
    root: Path, candidates: Iterable[Path], category: str
) -> Tuple[Path, ...]:
    """Validate every candidate with :func:`allowlisted_evidence_path`.

    A rejection is never swallowed or silently dropped: it is re-raised as
    an :class:`InputResolutionError` naming *category*'s affected finding
    IDs, so ``resolve_inputs`` can never return an empty, success-shaped
    result in place of a real parse failure.
    """
    collected = []
    for candidate in candidates:
        try:
            allowlisted_evidence_path(root, candidate)
        except InputResolutionError as error:
            finding_ids = ", ".join(_CATEGORY_FINDING_IDS.get(category, ()))
            raise InputResolutionError(
                f"{error} (affects {finding_ids or 'no known findings'})"
            ) from error
        collected.append(candidate)
    return tuple(sorted(collected, key=lambda p: p.as_posix()))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def allowlisted_evidence_path(target: Path, candidate: Path) -> Path:
    """Validate *candidate* as a safe, repository-relative evidence path.

    *candidate* must be relative (an absolute path is rejected outright) and
    must not contain a ``..`` path segment. It is then resolved against
    *target* — following any symlink to its real target — and rejected if
    that real target lies outside *target* (a symlink cannot be used to
    smuggle evidence in from outside the repository). The resolved path
    must exist and be readable; a JSON file must parse and must be free of
    payload-bearing keys (``canonical.validate_payload_free_audit``) —
    prompts, arguments, outputs, secrets, and tokens are never acceptable
    evidence content.

    Returns the resolved (symlink-followed) absolute :class:`Path` on
    success. Every rejection raises :class:`InputResolutionError` naming the
    original (repository-relative) candidate exactly as given.
    """
    root = Path(target).resolve()
    candidate_path = Path(candidate)
    rel_display = candidate_path.as_posix()

    if candidate_path.is_absolute():
        raise InputResolutionError(
            f"evidence path must be repository-relative, not absolute: {rel_display}"
        )
    if ".." in candidate_path.parts:
        raise InputResolutionError(
            f"evidence path must not contain '..' traversal: {rel_display}"
        )

    resolved = (root / candidate_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise InputResolutionError(
            "evidence path escapes the repository root (possibly via a "
            f"symlink): {rel_display}"
        ) from error

    if not resolved.exists():
        raise InputResolutionError(f"evidence path does not exist: {rel_display}")

    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise InputResolutionError(
            f"cannot read evidence path {rel_display}: {error}"
        ) from error

    if resolved.suffix == ".json":
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InputResolutionError(
                f"evidence path {rel_display} is not valid JSON: {error}"
            ) from error
        try:
            canonical.validate_payload_free_audit(value)
        except canonical.PayloadExposureError as error:
            raise InputResolutionError(
                f"evidence path {rel_display} contains payload-bearing "
                f"content and cannot be used as evidence: {error}"
            ) from error

    return resolved


def resolve_inputs(target: Path, phase: Phase) -> ResolvedInputs:
    """Resolve every repository-relative input available for *phase*.

    Requires ``specs/SPEC.md`` for ``design``/``pre-deploy`` (its absence
    there is a missing prerequisite — :class:`InputResolutionError` —
    rather than an empty action inventory) and, for ``pre-deploy`` only, a
    nonempty runtime/tool declaration set (at least one action registry or
    Python source file). Every discovered candidate is validated with
    :func:`allowlisted_evidence_path`; a rejection propagates as an
    :class:`InputResolutionError` naming the offending path and the finding
    IDs that category feeds, so a parse failure never gets silently dropped
    from an otherwise "successful" result.

    GitHub and Azure live evidence are always recorded as unavailable
    capabilities (see ``missing_capabilities``): this function resolves only
    local repository-relative inputs.
    """
    if phase not in contracts.SUPPORTED_PHASES:
        raise InputResolutionError(f"unsupported phase: {phase!r}")

    root = Path(target).resolve()
    if not root.is_dir():
        raise InputResolutionError(f"assessment target is not a directory: {target}")

    spec_relative = Path("specs") / "SPEC.md"
    spec: Optional[Path] = spec_relative if (root / spec_relative).is_file() else None
    if spec is None and phase in ("design", "pre-deploy"):
        raise InputResolutionError(
            f"specs/SPEC.md is required for the '{phase}' phase and was not "
            f"found under {root}"
        )
    if spec is not None:
        allowlisted_evidence_path(root, spec)

    registries = _validate_and_collect(root, _discover_registries(root), "registries")
    runtime_files = _validate_and_collect(
        root, _discover_python_runtime_files(root), "runtime_files"
    )
    policy_files = _validate_and_collect(
        root, _discover_policy_files(root), "policy_files"
    )
    approval_files = _validate_and_collect(
        root, _discover_approval_files(root, runtime_files), "approval_files"
    )
    test_and_report_files = _validate_and_collect(
        root, _discover_test_and_report_files(root), "test_and_report_files"
    )
    workflow_files = _validate_and_collect(
        root, _discover_workflow_files(root), "workflow_files"
    )
    ownership_files = _validate_and_collect(
        root, _discover_ownership_files(root), "ownership_files"
    )
    dependency_files = _validate_and_collect(
        root, _discover_dependency_files(root), "dependency_files"
    )

    if phase == "pre-deploy" and not registries and not runtime_files:
        raise InputResolutionError(
            "pre-deploy requires a nonempty runtime/tool declaration set (an "
            f"action registry or a Python tool implementation) and none was "
            f"found under {root}"
        )

    return ResolvedInputs(
        spec=spec,
        registries=registries,
        runtime_files=runtime_files,
        policy_files=policy_files,
        approval_files=approval_files,
        test_and_report_files=test_and_report_files,
        workflow_files=workflow_files,
        ownership_files=ownership_files,
        dependency_files=dependency_files,
        missing_capabilities=_MISSING_CAPABILITIES,
    )
