"""Canonical hashing, payload-free audit guard, and atomic writes.

threadlight-governed-actions is read-only: every manifest, evidence record,
and audit record it produces or inspects must be reproducible from exact
bytes (never a re-serialization that could silently drift) and must never
carry prompt/argument/output/secret payload content — only hashes and
structural references. This module is the single place those two invariants
are enforced.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonicalized (e.g. a non-finite float)."""


class PayloadExposureError(ValueError):
    """Raised when a payload-carrying key is found in a payload-free record."""


# Case-insensitive key names that would carry prompt/argument/output/secret
# content. Compared by *exact* (lower-cased) key match — not substring — so
# hash-suffixed fields such as ``input_hash``/``output_hash`` are permitted.
_BANNED_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "message",
        "arguments",
        "args",
        "input",
        "output",
        "result",
        "secret",
        "secrets",
        "token",
        "authorization",
        "body",
        "payload",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
        "cookie",
        "content",
    }
)


def _reject_non_finite(value: object) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(
                "value must be finite (JSON canonicalization rejects "
                f"NaN/+inf/-inf): {value!r}"
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_non_finite(key)
            _reject_non_finite(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def canonical_bytes(value: object) -> bytes:
    """Return deterministic, sorted, compact UTF-8 JSON bytes for *value*.

    Recursively rejects non-finite floats (NaN/+inf/-inf) with a
    :class:`CanonicalizationError` before handing off to ``json.dumps`` with
    ``sort_keys=True``, ``ensure_ascii=False``, ``allow_nan=False`` and
    compact ``(",", ":")`` separators, so the same logical value always
    produces the same bytes (and therefore the same hash).

    Any value ``json.dumps`` itself cannot serialize — an unsupported type
    (``TypeError``), a circular reference (``ValueError``), or nesting deep
    enough to blow the interpreter's recursion limit (``RecursionError``) —
    is translated into a :class:`CanonicalizationError` with a useful
    message rather than leaking an unrelated built-in exception type to
    callers who only expect canonicalization failures.
    """
    try:
        _reject_non_finite(value)
        text = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except CanonicalizationError:
        # Already a precise, specific failure (e.g. the non-finite-float
        # check) — propagate as-is instead of re-wrapping with a vaguer
        # message below.
        raise
    except RecursionError as error:
        raise CanonicalizationError(
            "value is nested too deeply (or is circular) to canonicalize"
        ) from error
    except (TypeError, ValueError) as error:
        raise CanonicalizationError(
            f"value cannot be canonicalized to JSON: {error}"
        ) from error
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def hash_files(root: Path, paths: Iterable[Path]) -> dict[str, object]:
    """Hash *paths* (repository-relative) under *root*, binding path + bytes.

    Every entry binds the exact repository-relative path (POSIX separators)
    to the exact SHA-256 of the file's raw bytes — no normalization, no
    re-serialization. A path that resolves outside *root* is rejected rather
    than silently hashed, since a hash over the wrong file (or one outside
    the repository) would be worse than no hash at all.

    Behaves like a *set* of files, not a list: the same repository-relative
    path supplied more than once (whether as a literal duplicate, via a
    ``./`` prefix, or via a different but equivalent path spelling) is
    de-duplicated to a single entry, and the result is sorted by path — so
    ``set_sha256`` is invariant to both the order and the duplication of the
    input, and only ever depends on the distinct set of (path, bytes) pairs
    actually hashed.

    Every input path is resolved with ``Path.resolve()``, which follows
    symlinks to their real target. A symlink whose real target lies within
    *root* is therefore hashed under its *resolved* (real) repository-
    relative path rather than the symlink's own nominal path — hashing a
    symlink and hashing its target directly produce the identical entry.
    This is deliberate, not silently permissive: it means a symlink cannot
    be used to record a distinct nominal path for the same underlying file,
    and only a symlink whose real target actually escapes *root* is
    rejected as an escape (the same rejection any other escaping path gets).

    Any failure to actually read a file's bytes (it doesn't exist, it's a
    directory, a permission error, or any other OS-level read failure) is
    translated into a :class:`CanonicalizationError` naming the offending
    repository-relative path, rather than leaking a raw
    ``OSError``/``FileNotFoundError``/``IsADirectoryError`` — hash_files has
    exactly one failure-mode exception type callers need to handle.
    """
    root_path = Path(root).resolve()
    entries_by_path: dict[str, dict[str, object]] = {}
    for raw_path in paths:
        candidate = Path(raw_path)
        absolute = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root_path / candidate).resolve()
        )
        try:
            relative = absolute.relative_to(root_path)
        except ValueError as error:
            raise CanonicalizationError(
                f"path escapes assessment root: {raw_path!r}"
            ) from error
        relative_posix = relative.as_posix()
        if relative_posix in entries_by_path:
            continue
        try:
            data = absolute.read_bytes()
        except OSError as error:
            raise CanonicalizationError(
                f"cannot read file to hash: {relative_posix!r} ({error})"
            ) from error
        entries_by_path[relative_posix] = {
            "path": relative_posix,
            "sha256": f"sha256:{sha256_hex(data)}",
        }

    entries = sorted(entries_by_path.values(), key=lambda entry: entry["path"])
    set_digest = sha256_hex(canonical_bytes(entries))
    return {
        "algorithm": "sha256",
        "files": entries,
        "set_sha256": f"sha256:{set_digest}",
    }


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def validate_payload_free_audit(record: Mapping[str, object]) -> None:
    """Recursively assert *record* carries no payload-bearing key.

    Walks every nested mapping and sequence in *record* and raises
    :class:`PayloadExposureError` the moment a key case-insensitively equals
    one of the banned payload-carrying names (``prompt``, ``messages``,
    ``message``, ``arguments``, ``args``, ``input``, ``output``, ``result``,
    ``secret``, ``secrets``, ``token``, ``authorization``, ``body``,
    ``payload``, ``password``, ``passwd``, ``api_key``, ``apikey``,
    ``access_token``, ``refresh_token``, ``credential``, ``credentials``,
    ``cookie``, ``content``). Matching is exact key equality, not
    substring, so derived hash fields such as ``input_hash``/``output_hash``/
    ``password_hash``/``content_hash`` are explicitly permitted.

    Fails closed on structure: a nested value that is not JSON-native
    (mapping, list/tuple, string, int, float, bool, or ``None``) — for
    example a ``set`` or an arbitrary object — is itself rejected with
    :class:`PayloadExposureError` rather than silently skipped, since such a
    value cannot be proven payload-free (it cannot even be recursed into or
    canonically serialized) and an audit record must never contain content
    the assessor cannot fully account for.
    """
    _check_payload_free(record, "$")


def _check_payload_free(value: object, location: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _BANNED_KEYS:
                raise PayloadExposureError(
                    f"{location}.{key_text} is a banned payload-carrying "
                    "field; audit records must be payload-free"
                )
            _check_payload_free(item, f"{location}.{key_text}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_payload_free(item, f"{location}[{index}]")
        return
    if not isinstance(value, _JSON_SCALAR_TYPES):
        raise PayloadExposureError(
            f"{location} is not a JSON-native value "
            f"(found {type(value).__name__}); audit records must contain "
            "only JSON-native mappings, sequences, and scalars so they can "
            "be proven payload-free"
        )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write *data* to *path*, fsyncing file and parent directory.

    Writes to a ``NamedTemporaryFile`` in the same directory as *path* (so
    the final ``os.replace`` is same-filesystem and therefore atomic),
    fsyncs the temp file's contents, replaces the destination, then fsyncs
    the parent directory so the rename itself is durable. ``os.replace`` is
    the commit point: once it returns, the destination holds the new
    content; the mandatory parent-directory fsync afterwards is durability
    for that already-committed rename, not part of the commit decision
    itself, and any error there still propagates rather than being
    swallowed. Any error removes only the temp file (never the destination)
    and re-raises.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        temp_path = None
        dir_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: object) -> None:
    """Atomically write the canonical JSON bytes of *value* to *path*."""
    atomic_write_bytes(Path(path), canonical_bytes(value))
