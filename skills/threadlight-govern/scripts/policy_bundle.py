"""Build immutable, content-addressed local ACS/Rego bundles.

Validation uses the installed exact-pin native loader, not a parallel policy
schema. Publication is a single no-replace directory rename. Signatures and
deployment enforcement are deliberately outside this module.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import importlib
from importlib.metadata import version as distribution_version
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_canonical = importlib.import_module("skills.threadlight-governed-actions.scripts.canonical")
canonical_bytes = _canonical.canonical_bytes
sha256_hex = _canonical.sha256_hex

SCHEMA = "threadlight-policy-bundle/v1"
METADATA = "bundle-metadata.json"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class PolicyBundle:
    root: Path
    manifest_path: Path
    bundle_digest: str
    files: tuple[dict, ...]


def checked_path(path: Path) -> Path:
    """Reject symlink components, including dangling links and lexical traversal."""
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("path traversal is forbidden")
    path = path.absolute()
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError(f"symlink is forbidden: {component}")
    return path


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("references must be local POSIX relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("reference escapes the bundle")
    if not path.parts or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path.parts
    ):
        raise ValueError("non-canonical bundle path")
    return path.as_posix()


def _read_file(path: Path) -> bytes:
    checked_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("only regular bundle files are supported")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("bundle file exceeds size limit")
    return data


def _snapshot(root: Path, *, include_metadata=False) -> dict[str, bytes]:
    root = checked_path(root)
    if not root.is_dir():
        raise ValueError("bundle root must be a directory")
    files = {}
    total = 0

    def traversal_error(error):
        raise error

    for base, dirs, names in os.walk(root, followlinks=False, onerror=traversal_error):
        for name in sorted(dirs + names):
            path = checked_path(Path(base) / name)
            relative = _relative_path(path.relative_to(root).as_posix())
            if path.is_dir():
                continue
            data = _read_file(path)
            total += len(data)
            if total > MAX_BUNDLE_BYTES:
                raise ValueError("bundle exceeds size limit")
            if relative != METADATA or include_metadata:
                files[relative] = data
    return dict(sorted(files.items()))


def _confine_references(root: Path, files: dict[str, bytes]) -> None:
    import yaml

    visited = set()

    def reference(value, parent):
        path = _relative_path(value)
        relative = (parent / path).as_posix()
        if relative not in files and not any(p.startswith(relative + "/") for p in files):
            raise ValueError(f"missing bundle reference: {relative}")
        return relative

    def policy(config, parent):
        if not isinstance(config, dict):
            raise ValueError("policy configuration must be an object")
        if config.get("type", "rego") != "rego":
            raise ValueError("this bundle builder supports local Rego policies only")
        if "bundle_url" in config:
            raise ValueError("remote policy bundles are forbidden")
        if "bundle" in config:
            reference(config["bundle"], parent)
        adapter = {k: v for k, v in config.items() if k not in {"type", "id", "query", "bundle"}}
        if set(adapter) - {"data", "data_paths"}:
            raise ValueError("unsupported Rego adapter configuration")
        for value in adapter.values():
            for item in value if isinstance(value, list) else [value]:
                reference(item, parent)

    def manifest(name):
        if name in visited:
            return  # Native loader independently rejects extends cycles.
        visited.add(name)
        document = yaml.safe_load(files[name])
        if not isinstance(document, dict):
            raise ValueError("manifest must be an object")
        parent = PurePosixPath(name).parent
        for inherited in document.get("extends", []):
            manifest(reference(inherited, parent))
        if document.get("annotators") or document.get("approval"):
            raise ValueError("external annotators and approval backends are host configuration")
        for config in document.get("policies", {}).values():
            policy(config, parent)
        for point in document.get("intervention_points", {}).values():
            policy(point.get("policy", {}), parent)

    try:
        manifest("manifest.yaml")
    except (KeyError, TypeError, AttributeError, yaml.YAMLError):
        raise ValueError("invalid local manifest references") from None


def validate_native_manifest(root: Path) -> None:
    pins = json.loads((ROOT / "skills/_shared/governance-upstream-pin.json").read_text())
    pin = pins["acs"]
    if distribution_version(pin["distribution"]) != pin["version"]:
        raise ValueError(f"native loader requires {pin['distribution']}=={pin['version']}")
    from agent_control_specification import AgentControl
    files = _snapshot(root)
    _confine_references(root, files)
    try:
        AgentControl.from_path(str(root / "manifest.yaml"))
    except (ValueError, RuntimeError, OSError):
        raise ValueError("ACS native manifest rejected") from None


def _content(policy_id: str, version: str, files: dict[str, bytes]) -> dict:
    if not isinstance(policy_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", policy_id):
        raise ValueError("policy_id must be an identifier")
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*(?:[A-Za-z][0-9A-Za-z.-]*)?", version):
        raise ValueError("version must be pinned")
    return {
        "schema": SCHEMA, "policy_id": policy_id, "version": version,
        "files": [
            {"path": path, "size_bytes": len(data), "sha256": "sha256:" + sha256_hex(data)}
            for path, data in sorted(files.items())
        ],
    }


def _publish(staging: Path, destination: Path) -> None:
    """Use OS no-replace semantics, including when another writer wins the race."""
    checked_path(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(staging), os.fsencode(destination), 0x4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    else:
        raise OSError("atomic no-replace directory publication requires Linux or macOS")
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def build_bundle(*, source: Path, destination: Path, policy_id: str, version: str) -> PolicyBundle:
    source, destination = checked_path(source), checked_path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("source and destination must not overlap")
    if not destination.parent.is_dir():
        raise ValueError("destination parent must already exist")
    files = _snapshot(source, include_metadata=True)
    if METADATA in files:
        raise ValueError("source must not contain generated bundle metadata")
    content = _content(policy_id, version, files)
    digest = "sha256:" + sha256_hex(canonical_bytes(content))
    metadata = {**content, "bundle_digest": digest, "signature": None}
    staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        for relative, data in {**files, METADATA: canonical_bytes(metadata) + b"\n"}.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        validate_native_manifest(staging)
        verify_bundle(staging, expected_digest=digest)
        _publish(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return PolicyBundle(destination, destination / "manifest.yaml", digest, tuple(content["files"]))


def verify_bundle(root: Path, *, expected_digest: str | None = None) -> PolicyBundle:
    """Check exact bytes and file set; a digest is integrity, not publisher identity."""
    root = checked_path(root)
    files = _snapshot(root, include_metadata=True)
    try:
        raw_metadata = files.pop(METADATA)
        metadata = json.loads(raw_metadata)
        content = _content(metadata["policy_id"], metadata["version"], files)
        digest = "sha256:" + sha256_hex(canonical_bytes(content))
        expected = {**content, "bundle_digest": digest, "signature": None}
        if metadata != expected or raw_metadata != canonical_bytes(expected) + b"\n":
            raise ValueError("bundle metadata or content mismatch")
        if expected_digest is not None and digest != expected_digest:
            raise ValueError("bundle does not match trusted expected digest")
        _confine_references(root, files)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid bundle metadata: {exc}") from exc
    return PolicyBundle(root, root / "manifest.yaml", digest, tuple(content["files"]))
