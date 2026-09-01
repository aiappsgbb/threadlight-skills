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
        "arguments",
        "args",
        "input",
        "output",
        "result",
        "secret",
        "token",
        "authorization",
        "body",
        "payload",
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
    """
    _reject_non_finite(value)
    text = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
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
    the repository) would be worse than no hash at all. The returned
    ``set_sha256`` is the SHA-256 of the canonical bytes of the (sorted)
    file-entry list, so the whole set can be pinned with one value.
    """
    root_path = Path(root).resolve()
    entries: list[dict[str, object]] = []
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
        data = absolute.read_bytes()
        entries.append(
            {"path": relative.as_posix(), "sha256": f"sha256:{sha256_hex(data)}"}
        )

    entries.sort(key=lambda entry: entry["path"])
    set_digest = sha256_hex(canonical_bytes(entries))
    return {
        "algorithm": "sha256",
        "files": entries,
        "set_sha256": f"sha256:{set_digest}",
    }


def validate_payload_free_audit(record: Mapping[str, object]) -> None:
    """Recursively assert *record* carries no payload-bearing key.

    Walks every nested mapping and sequence in *record* and raises
    :class:`PayloadExposureError` the moment a key case-insensitively equals
    one of the banned payload-carrying names (``prompt``, ``messages``,
    ``arguments``, ``args``, ``input``, ``output``, ``result``, ``secret``,
    ``token``, ``authorization``, ``body``, ``payload``). Matching is exact
    key equality, not substring, so derived hash fields such as
    ``input_hash``/``output_hash`` are explicitly permitted.
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


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write *data* to *path*, fsyncing file and parent directory.

    Writes to a ``NamedTemporaryFile`` in the same directory as *path* (so
    the final ``os.replace`` is same-filesystem and therefore atomic),
    fsyncs the temp file's contents, replaces the destination, then fsyncs
    the parent directory so the rename itself is durable. Any error removes
    only the temp file (never the destination) and re-raises.
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
