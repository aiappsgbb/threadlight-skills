"""Task 11: bounded, explicit, double-opt-in MAF governance scaffold.

This module writes exactly five starter files (:data:`SCAFFOLD_FILES`) into
a target project when, and only when, the caller explicitly opts in twice:
``scaffold_kind == "maf"`` *and* ``confirm_scaffold is True``. Either opt-in
alone -- or both absent -- refuses and writes nothing (:class:`ScaffoldRefusedError`).

What this module is *not*:

- It is **not a policy author**. Every scaffolded file is a deliberately
  empty/deny-by-default starting point: the policy template's ``rules``
  and ``approvers`` are always ``[]``, and no business threshold, named
  approver, tenant id, role assignment, or deployment target is ever
  invented anywhere in the templates this module places. Turning the
  scaffold into a real, customer-specific policy is delegated entirely to
  the ``foundry-agt`` skill (policy authoring) and, if the workflow needs
  a real deploy pipeline, to ``threadlight-cicd`` (pipeline expansion) --
  this module writes neither.
- It does **not** make the target project "governed". A freshly
  scaffolded project still has an empty policy, no wired interceptor
  call, and no CI enforcement of either -- the governed-actions
  assessor's own findings for such a project remain ``must-fix``/
  ``not-verified`` exactly as they would for a project with no
  governance files at all, until a customer actually authors policy and
  wires the interceptor into their own runtime.
- It never runs during a normal ``--phase design``/``pre-deploy``/
  ``post-deploy`` assessment: :mod:`governed_actions` only ever calls
  into this module when the CLI's own two scaffold flags were both given,
  and the two code paths never share a call site.

Write safety: every destination is preflighted (existence, symlink
ancestors, and mutual nesting) before a single byte is touched; the five
files' contents are all staged into a private, same-filesystem staging
directory first; only once every one of them has been fully written and
fsynced are they placed at their real destinations, one at a time, via a
descriptor-relative, no-follow, exclusive-create-or-fail hard link (never
a plain ``rename``/``replace``, which would silently allow overwriting an
existing destination). Any failure at any point during placement rolls
back every destination already placed, so a partially-written scaffold
can never survive a failed :func:`scaffold` call.
"""
from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: Exactly the five files a successful scaffold ever writes, in a fixed,
#: deterministic order: destination path (relative to the target root)
#: -> template filename under ``references/scaffold/``. This mapping is
#: never extended, never derived from the target, and never reordered at
#: runtime -- a sixth file, or a different destination for one of these
#: five, would be inventing scope this task's contract does not grant.
SCAFFOLD_FILES: Dict[str, str] = {
    "src/governance/agent_hooks_interceptor.py": "agent_hooks_interceptor.py.tmpl",
    "policies/governed-actions.policy.yaml": "governed-actions.policy.yaml.tmpl",
    "tests/governance/approval-binding-fixture.json": "approval-binding-fixture.json.tmpl",
    "tests/governance/test_governed_actions_contract.py": "test_governed_actions_contract.py.tmpl",
    ".github/workflows/governed-actions.yml": "governed-actions.yml.tmpl",
}

#: The only ``scaffold_kind`` this module currently understands. Any other
#: value -- including a superficially plausible one -- is refused exactly
#: like a missing one: this module never infers or invents a second kind.
SUPPORTED_SCAFFOLD_KINDS: Tuple[str, ...] = ("maf",)

#: Where the ``*.tmpl`` sources for :data:`SCAFFOLD_FILES` live -- this
#: skill's own references directory, never target-controlled.
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "references" / "scaffold"

#: This skill's own installation root. A *root* argument equal to, nested
#: inside, or an ancestor of this directory names the assessor's own
#: project rather than an external customer/target project -- this tool
#: must never scaffold governance files into itself.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: Prefix for the private, same-filesystem staging directory this module
#: creates directly under the (already-verified) target root for the
#: duration of one scaffold call, and always removes again -- on success
#: after every file has been placed, and on any failure as part of
#: rollback. Never left behind, and never itself one of the five
#: destinations a caller could observe as scaffold output.
_STAGING_DIR_PREFIX = ".governed-actions-scaffold-stage-"


class ScaffoldRefusedError(ValueError):
    """Raised whenever :func:`scaffold` declines to write anything.

    A plain :class:`ValueError` subclass (never a bare exception, never
    ``sys.exit``) so callers -- in particular the ``governed_actions``
    CLI -- can catch it exactly like every other invalid-input condition
    this codebase already classifies as exit code 2. Every refusal this
    module raises happens strictly before any file is staged or placed,
    so a caller can always be certain that when this is raised, the
    target project was not modified at all.
    """


# ---------------------------------------------------------------------------
# Opt-in gate
# ---------------------------------------------------------------------------


def _require_double_opt_in(scaffold_kind: Optional[str], confirm_scaffold: bool) -> None:
    """Refuse unless *both* explicit opt-ins were given.

    ``--scaffold maf`` alone, ``--confirm-scaffold`` alone, an unknown
    scaffold kind, or neither: every one of these refuses identically and
    writes nothing. Only the exact combination of a supported
    *scaffold_kind* and ``confirm_scaffold is True`` proceeds.
    """
    if scaffold_kind not in SUPPORTED_SCAFFOLD_KINDS or confirm_scaffold is not True:
        raise ScaffoldRefusedError(
            "scaffold refused: both a supported --scaffold kind (currently "
            f"only {SUPPORTED_SCAFFOLD_KINDS!r}) and --confirm-scaffold "
            "must be given together; neither opt-in alone is sufficient, "
            "and nothing was written"
        )


# ---------------------------------------------------------------------------
# Root validation
# ---------------------------------------------------------------------------


def _is_nested(outer: Path, inner: Path) -> bool:
    """True if *inner* is *outer* itself, or lives underneath it."""
    try:
        inner.relative_to(outer)
        return True
    except ValueError:
        return False


def _verify_root(root: Path) -> Path:
    """Validate *root* and return its resolved, real path.

    Refuses: a missing path; a path that is not a directory; a path that
    is itself a symlink (checked with a no-follow ``lstat`` so a symlinked
    root is never silently followed); the filesystem root itself; and any
    root equal to, nested inside, or an ancestor of this skill's own
    installation directory (:data:`_PACKAGE_ROOT`) -- scaffolding is only
    ever for an external target project, never for the assessor's own
    source tree.
    """
    root = Path(root)
    try:
        leaf_info = root.lstat()
    except OSError as error:
        raise ScaffoldRefusedError(
            f"scaffold refused: root does not exist or is not accessible: {root} ({error})"
        ) from error
    if stat.S_ISLNK(leaf_info.st_mode):
        raise ScaffoldRefusedError(f"scaffold refused: root must not be a symlink: {root}")
    if not root.is_dir():
        raise ScaffoldRefusedError(f"scaffold refused: root is not a directory: {root}")

    resolved = root.resolve(strict=True)

    if resolved.parent == resolved:
        raise ScaffoldRefusedError(
            f"scaffold refused: root must not be the filesystem root: {resolved}"
        )
    if _is_nested(_PACKAGE_ROOT, resolved) or _is_nested(resolved, _PACKAGE_ROOT):
        raise ScaffoldRefusedError(
            "scaffold refused: root is inside (or contains) the assessor's "
            f"own installation and is not a valid target project: {resolved}"
        )
    return resolved


# ---------------------------------------------------------------------------
# Destination preflight (fail fast, before any staging or writes)
# ---------------------------------------------------------------------------


def _destination_for(resolved_root: Path, relative: str) -> Path:
    parts = [part for part in Path(relative).parts if part not in ("", ".")]
    if any(part == ".." for part in parts) or not parts:
        raise ScaffoldRefusedError(
            f"scaffold refused: malformed internal destination: {relative!r}"
        )
    return resolved_root.joinpath(*parts)


def _refuse_if_leaf_exists(dest: Path) -> None:
    """Refuse if *dest* already exists in any form -- regular file,
    directory, or symlink, including a dangling (broken-target) symlink.

    A no-follow ``lstat`` (rather than ``Path.exists()``, which follows
    symlinks and would report ``False`` for a dangling one) is used
    specifically so a symlink planted at a destination path can never be
    mistaken for "nothing here yet".
    """
    try:
        dest.lstat()
    except FileNotFoundError:
        return
    raise ScaffoldRefusedError(f"scaffold refused: destination already exists: {dest}")


def _refuse_if_any_ancestor_is_symlink(resolved_root: Path, dest: Path) -> None:
    """Refuse if any already-existing ancestor directory of *dest*
    (between *resolved_root* and ``dest.parent``) is a symlink, or exists
    but is not itself a directory.

    This is the cheap, path-based preflight check ("fail fast, nothing
    touched yet"); the real, TOCTOU-safe enforcement happens later, when
    each ancestor is actually opened with ``O_NOFOLLOW`` while staging.
    """
    current = resolved_root
    for part in dest.relative_to(resolved_root).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ScaffoldRefusedError(
                f"scaffold refused: destination parent is a symlink: {current}"
            )
        if current.exists() and not current.is_dir():
            raise ScaffoldRefusedError(
                "scaffold refused: destination parent exists and is not a "
                f"directory: {current}"
            )


def _refuse_duplicates_or_nesting(destinations: Sequence[Path]) -> None:
    seen: List[Path] = []
    for dest in destinations:
        for other in seen:
            if dest == other or _is_nested(dest, other) or _is_nested(other, dest):
                raise ScaffoldRefusedError(
                    "scaffold refused: destinations must be distinct and "
                    f"must not nest inside one another: {dest} and {other}"
                )
        seen.append(dest)


def _preflight_destinations(resolved_root: Path) -> List[Tuple[Path, str]]:
    """Resolve and validate every destination before any write begins.

    Returns an ordered ``(destination, template_filename)`` list matching
    :data:`SCAFFOLD_FILES`'s own iteration order.
    """
    destinations: List[Path] = []
    ordered: List[Tuple[Path, str]] = []
    for relative, template_name in SCAFFOLD_FILES.items():
        dest = _destination_for(resolved_root, relative)
        _refuse_if_leaf_exists(dest)
        _refuse_if_any_ancestor_is_symlink(resolved_root, dest)
        destinations.append(dest)
        ordered.append((dest, template_name))
    _refuse_duplicates_or_nesting(destinations)
    return ordered


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def _load_template_bytes(template_name: str) -> bytes:
    path = _TEMPLATES_DIR / template_name
    try:
        return path.read_bytes()
    except OSError as error:
        raise ScaffoldRefusedError(
            f"scaffold refused: could not read template {template_name!r}: {error}"
        ) from error


# ---------------------------------------------------------------------------
# Staging + atomic, exclusive-create placement
# ---------------------------------------------------------------------------


def _open_verified_dir_fd(root_fd: int, parts: Sequence[str], display: Path) -> int:
    """Walk *parts* (directory-component names only) from *root_fd*,
    opening each with ``O_NOFOLLOW`` so a symlink at any level -- even one
    swapped in after preflight ran -- raises instead of being silently
    traversed. A missing directory is created under the already-verified
    parent and then re-opened with the same no-follow flags, so a symlink
    raced into the gap between "not found" and "create" is still caught.
    The caller owns the returned fd and must close it.
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
                    raise ScaffoldRefusedError(
                        "scaffold refused: could not create/verify destination "
                        f"directory: {display} ({error})"
                    ) from error
            except OSError as error:
                raise ScaffoldRefusedError(
                    "scaffold refused: destination directory path is unsafe "
                    f"(not a plain directory -- e.g. a symlink): {display} ({error})"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_root_fd(resolved_root: Path) -> int:
    """Open a held-open, no-follow-verified directory fd for
    *resolved_root* itself, so every later create is descriptor-relative
    (``dir_fd=``) and immune to the root being renamed/symlink-swapped at
    the path level after this call returns.
    """
    parent = resolved_root.parent
    if parent == resolved_root:  # pragma: no cover - rejected earlier by _verify_root
        return os.open(resolved_root, os.O_RDONLY | os.O_DIRECTORY)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return os.open(
            resolved_root.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)


def _write_new_file(parent_fd: int, name: str, data: bytes, display: Path) -> None:
    """Create *name* under *parent_fd* with ``O_CREAT | O_EXCL`` (fail if
    it already exists) and ``O_NOFOLLOW`` (never write through a leaf
    symlink), write+fsync *data*, and close it.
    """
    try:
        fd = os.open(
            name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
    except FileExistsError as error:
        raise ScaffoldRefusedError(
            f"scaffold refused: destination already exists: {display}"
        ) from error
    except OSError as error:
        raise ScaffoldRefusedError(
            f"scaffold refused: could not create staging file for {display}: {error}"
        ) from error
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException as error:
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError:
            pass
        raise ScaffoldRefusedError(
            f"scaffold refused: failed to stage bytes for {display}: {error}"
        ) from error


def _stage_all(root_fd: int, staging_name: str, rendered: Sequence[Tuple[Path, str, bytes]]) -> int:
    """Create the private staging directory under *root_fd* and populate
    it with every rendered file, fully written and fsynced, before any
    real destination is touched. Returns the staging directory's own held
    -open fd; the caller owns it and must close it.
    """
    os.mkdir(staging_name, 0o700, dir_fd=root_fd)
    staging_fd = os.open(
        staging_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
    )
    try:
        for _dest, staged_name, data in rendered:
            _write_new_file(staging_fd, staged_name, data, Path(staging_name) / staged_name)
        os.fsync(staging_fd)
        return staging_fd
    except BaseException:
        os.close(staging_fd)
        _remove_staging(root_fd, staging_name)
        raise


def _remove_staging(root_fd: int, staging_name: str) -> None:
    try:
        staging_fd = os.open(
            staging_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
    except OSError:
        return
    try:
        for entry in os.listdir(staging_fd):
            try:
                os.unlink(entry, dir_fd=staging_fd)
            except OSError:
                pass
    finally:
        os.close(staging_fd)
    try:
        os.rmdir(staging_name, dir_fd=root_fd)
    except OSError:
        pass


def _place_all(
    root_fd: int,
    resolved_root: Path,
    staging_fd: int,
    rendered: Sequence[Tuple[Path, str, bytes]],
) -> None:
    """Atomically place every staged file at its real destination, one at
    a time, via a descriptor-relative, no-follow, exclusive-create-or-fail
    hard link from the already-written-and-fsynced staging copy.

    ``os.link`` either creates the destination directory entry pointing
    at the same, already-durable inode as the staging copy, or fails with
    ``FileExistsError`` if the destination already exists -- it never
    silently overwrites, unlike ``os.rename``/``os.replace``. If placement
    fails partway through, every destination already placed in this call
    is rolled back (unlinked) before the failure is re-raised, so no
    caller ever observes a partial scaffold.
    """
    placed: List[Tuple[int, Path, str]] = []
    try:
        for dest, staged_name, _data in rendered:
            parts = dest.relative_to(resolved_root).parts
            parent_fd = _open_verified_dir_fd(root_fd, parts[:-1], dest.parent)
            leaf_name = parts[-1]
            try:
                os.link(staged_name, leaf_name, src_dir_fd=staging_fd, dst_dir_fd=parent_fd)
            except FileExistsError as error:
                raise ScaffoldRefusedError(
                    f"scaffold refused: destination already exists: {dest}"
                ) from error
            except OSError as error:
                os.close(parent_fd)
                raise ScaffoldRefusedError(
                    f"scaffold refused: could not place destination {dest}: {error}"
                ) from error
            placed.append((parent_fd, dest, leaf_name))
    except BaseException:
        for parent_fd, _dest, leaf_name in placed:
            try:
                os.unlink(leaf_name, dir_fd=parent_fd)
            except OSError:
                pass
        for parent_fd, _dest, _leaf_name in placed:
            os.close(parent_fd)
        raise
    for parent_fd, _dest, _leaf_name in placed:
        os.close(parent_fd)


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


def scaffold(root: Path, scaffold_kind: Optional[str], confirm_scaffold: bool) -> Tuple[Path, ...]:
    """Write exactly :data:`SCAFFOLD_FILES` under *root*, or write nothing.

    Requires both ``scaffold_kind == "maf"`` and ``confirm_scaffold is
    True`` (see :func:`_require_double_opt_in`); refuses (raising
    :class:`ScaffoldRefusedError`, writing nothing) for: a missing opt-in;
    an unsafe *root* (missing, not a directory, a symlink, the filesystem
    root, or inside/containing this skill's own installation); any
    destination that already exists (including a dangling symlink); any
    existing destination-ancestor directory that is a symlink; and
    mutually nested/duplicate destinations.

    Every destination is preflighted before any write. All five files are
    then staged -- fully rendered, written, and fsynced -- in a private
    same-filesystem staging directory, and only placed at their real
    destinations, one at a time via an exclusive-create hard link, once
    every one of them staged successfully. Any failure during placement
    rolls back every destination already placed in this call, so a
    partial scaffold never survives a failed call.

    Returns the five destination paths (absolute, under *root*), in
    :data:`SCAFFOLD_FILES`'s own fixed order, on success.
    """
    _require_double_opt_in(scaffold_kind, confirm_scaffold)
    resolved_root = _verify_root(Path(root))
    ordered_destinations = _preflight_destinations(resolved_root)

    rendered: List[Tuple[Path, str, bytes]] = []
    for dest, template_name in ordered_destinations:
        data = _load_template_bytes(template_name)
        rendered.append((dest, dest.name, data))

    root_fd = _open_root_fd(resolved_root)
    try:
        staging_name = f"{_STAGING_DIR_PREFIX}{uuid.uuid4().hex}"
        staging_fd = _stage_all(root_fd, staging_name, rendered)
        try:
            _place_all(root_fd, resolved_root, staging_fd, rendered)
        finally:
            os.close(staging_fd)
            _remove_staging(root_fd, staging_name)
    finally:
        os.close(root_fd)

    return tuple(dest for dest, _staged_name, _data in rendered)
