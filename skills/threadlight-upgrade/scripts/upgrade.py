#!/usr/bin/env python3
"""upgrade.py — the UPGRADE leg: compatibility and preview-drift scanning.

`threadlight-upgrade` is a **plan-only, advisory** skill: it scans a
normalized description of a pilot (its dependency pins, its hosted-agent
runtime policy, its governance profile, its model families in use) against a
versioned, dated `references/compatibility-matrix.json` and reports drift as
three findings:

    UPG-001  matrix/dependency staleness
             — the matrix itself hasn't been reviewed inside its own
               `review_window_days`, or a pinned dependency is behind (or a
               prerelease pinned to) the matrix's recorded `stable` release.
    UPG-002  preview/runtime-policy expiry drift
             — the project targets a `preview`/`deprecated` surface (a hosted
               -agent protocol mode, a governance profile, a model family),
               especially one whose `expiry_triggers` have fired (per the
               caller-supplied `triggered_expiry_conditions`) or whose own
               `expiry` date has passed.
    UPG-003  official source verification
             — whether the matrix's recorded state for each surface/target
               was actually corroborated against an official source. This
               skill makes **no network calls**; corroboration is fixture
               -driven via an injectable `source_results` mapping. When a
               check has no result to corroborate against, it is
               `not-verified` — the exact, literal reason is "Official source
               unavailable; no latest version was inferred." and no
               `latest_version` is ever fabricated for it.

This skill **never edits the project** and **does not implement `--apply`** —
there is no such flag, and passing one is a normal argparse usage error. Every
finding funnels into one ordered, de-duplicated, file-by-file `plan` (each
item: `order`, `path`, `reason`, `from`, `to`) that a human (or
`threadlight-auto`, manually, leg-by-leg) can act on. Handing that plan off
into an actual dependency bump / policy edit is a **manual, human-driven
step** — this skill never performs it.

Version comparison (semver + common Python/PEP-440-flavoured prereleases —
`a`/`alpha`, `b`/`beta`, `rc`/`c`/`pre`/`preview`, `dev`) is hand-rolled,
stdlib-only, and numeric (release segments are compared as padded integer
tuples, never as strings). A version this parser cannot confidently place
(a range specifier, a bare `latest`, anything unrecognized) is never guessed
at — it surfaces as `not-verified`.

stdlib-only. No third-party dependencies, no network I/O anywhere in this
module. Only the project-inspection inputs (an optional `--project-file` /
`--pyproject-path` / `--package-json-path` / `--runtime-policy-path`) and the
`--manifest-path` output are resolved and confined inside `--project-root`
before they are ever opened or written — an escape (an absolute path outside
root, a `..` traversal, or a symlink resolving outside root) is rejected
before any such file is touched, and every project-inspection read is
strictly read-only. The compatibility matrix (`--matrix-path`) and the
fixture-driven source-check results (`--source-results-path`) are, by
contrast, explicit operator-supplied read-only fixture inputs resolved
relative to the current working directory: they are deliberately **not**
confined to `--project-root` (an operator may keep a shared matrix outside
the pilot repo), are opened read-only, and are never written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Shared envelope (skills/_shared/manifest.py) — insert repo root on sys.path
# so `skills._shared.manifest` resolves as an implicit namespace package both
# in-repo and when this script is invoked standalone.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills._shared.manifest import (  # noqa: E402
    ManifestValidationError,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)

TOOL_VERSION = "0.1.0"
MANIFEST_SCHEMA = "threadlight.upgrade/v1"
MATRIX_SCHEMA = "threadlight-upgrade-compatibility-matrix/v1"

SURFACES = frozenset({
    "hosted-agent-protocol",
    "agent-framework",
    "toolbox",
    "skill-publication",
    "governance-profile",
    "model-family",
})
# Surfaces whose target is an installable, version-bearing package/artifact —
# these are matched against a project's `dependencies` map. The remaining
# surfaces are matched against project *usage* (a runtime policy mode, a
# governance profile, a model family) rather than a version string.
DEPENDENCY_SURFACES = frozenset({"agent-framework", "toolbox", "skill-publication"})
STATES = frozenset({"stable", "preview", "deprecated"})
FINDING_IDS = ("UPG-001", "UPG-002", "UPG-003")
FINDING_STATUS_ENUM = frozenset({"pass", "must-fix", "should-fix", "not-verified"})

DEFAULT_ARTIFACT_PATHS = {
    "agent-framework": "pyproject.toml",
    "toolbox": "pyproject.toml",
    "skill-publication": "pyproject.toml",
    "hosted-agent-protocol": "runtime-policy.json",
    "governance-profile": "governance-profile.yaml",
    "model-family": "model-config.json",
}

DEFAULT_MANIFEST_PATH = "specs/upgrade-manifest.json"

# The exact, literal not-verified detail message when official-source
# corroboration is unavailable — no network call is ever made, and no
# `latest_version` is fabricated for an unverified check.
SOURCE_UNAVAILABLE_MESSAGE = "Official source unavailable; no latest version was inferred."

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UpgradeMatrixError(ValueError):
    """Raised when `references/compatibility-matrix.json` (or a caller
    -supplied matrix) is the wrong shape. Always raised BEFORE any scan is
    performed or any manifest is built — a malformed matrix never produces
    partial/guessed output.
    """


class UpgradeProjectError(ValueError):
    """Raised when the normalized `project` input, a `today` value, an
    `artifact_paths` override, or a built manifest is the wrong shape.
    Also raised when a CLI-supplied path would escape `--project-root`.
    Always raised before any file is read/written.
    """


# ---------------------------------------------------------------------------
# Secret / forbidden-content detection (mirrors threadlight-ground/-connect):
# structural, never entropy-based — an opaque id, hash, or ordinary URL is
# never rejected for merely looking random.
# ---------------------------------------------------------------------------
_FORBIDDEN_KEY_WORDS = frozenset({
    "token", "secret", "password", "credential", "credentials",
    "authorization", "content", "prompt", "completion", "completions",
    "payload",
})
_FORBIDDEN_KEY_SUBSTRINGS = ("api_key", "apikey", "access_key", "connection_string")

_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bASIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),
    re.compile(r"\beyJ[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]{6,}\.[0-9A-Za-z_-]+"),
    re.compile(r"://[^/\s:@]+:[^/\s:@]+@"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)(?:^|;)\s*(?:accountkey|sharedaccesskey|"
        r"sharedaccesssignature)\s*=\s*[^;\s]+"
    ),
    re.compile(
        r"(?i)\b(?:pass(?:word)?|secret|api[_-]?key|access[_-]?key|"
        r"client[_-]?secret|bearer)\b\s*[:=]\s*\S+"
    ),
    # A URL query string carrying a token/key/secret-shaped parameter, e.g. a
    # SAS-style ?token=...&key=... reference smuggled through a `source` URL.
    re.compile(
        r"[?&](?:token|access_token|api_key|apikey|client_secret|password|"
        r"secret|sig)=[^&\s]+"
    ),
)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in _FORBIDDEN_KEY_SUBSTRINGS):
        return True
    words = re.split(r"[^a-z0-9]+", lowered)
    return any(word in _FORBIDDEN_KEY_WORDS for word in words)


def _looks_like_secret(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _SECRET_VALUE_PATTERNS)


def _assert_no_unsafe_content(obj: Any) -> None:
    """Recursively reject any credential/content-shaped KEY and any
    secret-shaped VALUE anywhere in a manifest/matrix before it is returned
    or written.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                if _is_forbidden_key(key):
                    raise UpgradeProjectError(
                        "upgrade manifest must not contain "
                        "credential/content-shaped keys"
                    )
                if _looks_like_secret(key):
                    raise UpgradeProjectError(
                        "upgrade manifest must not contain secret-shaped values"
                    )
            _assert_no_unsafe_content(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_unsafe_content(item)
    elif isinstance(obj, str):
        if _looks_like_secret(obj):
            raise UpgradeProjectError(
                "upgrade manifest must not contain secret-shaped values"
            )


# Repo-relative path only — no absolute path, no `..` traversal, no
# whitespace, no URL scheme. Mirrors the `plan[].path` schema pattern.
_SAFE_PATH_RE = re.compile(
    r"^(?!/)(?!.*\.\.)(?=.*[A-Za-z0-9])[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)


def _require_safe_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_PATH_RE.match(value):
        raise UpgradeProjectError(
            f"{label} must be a repo-relative path (no absolute path, no "
            f"'..' traversal, no whitespace, no URL scheme)"
        )
    return value


def _require_safe_reference(value: Any, label: str) -> str:
    """A `source` reference string: non-empty, and never a credential/token
    -bearing value (a plain doc URL is fine; a URL with embedded userinfo or
    a token/key/secret query parameter is not).
    """
    if not isinstance(value, str) or not value.strip():
        raise UpgradeMatrixError(f"{label} must be a non-empty string")
    if _looks_like_secret(value):
        raise UpgradeMatrixError(
            f"{label} must not contain credentials/tokens/secrets"
        )
    return value


# ---------------------------------------------------------------------------
# Version parsing/comparison — stdlib-only, numeric, never lexical.
# ---------------------------------------------------------------------------
_PRERELEASE_RANK = {
    "a": 1, "alpha": 1,
    "b": 2, "beta": 2,
    "rc": 3, "c": 3, "pre": 3, "preview": 3,
}
_VERSION_RE = re.compile(
    r"""^
    [vV]?
    (?P<release>\d+(?:\.\d+){0,3})
    (?:[-._]?(?P<pre>a|alpha|b|beta|rc|c|pre|preview)\.?(?P<pre_num>\d*))?
    (?:[-._]?(?P<dev>dev)\.?(?P<dev_num>\d*))?
    (?:\+[A-Za-z0-9.]+)?
    $""",
    re.IGNORECASE | re.VERBOSE,
)


def parse_version(text: Any) -> Optional[dict]:
    """Parse *text* into a structured version, or return None when it cannot
    be confidently placed (a range specifier, a bare 'latest', an empty
    string, or any other ambiguous shape). Never guesses.
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None
    match = _VERSION_RE.match(candidate)
    if not match:
        return None

    release = tuple(int(part) for part in match.group("release").split("."))
    pre = match.group("pre")
    dev = match.group("dev")

    if pre is not None:
        stage_rank = _PRERELEASE_RANK[pre.lower()]
        pre_num_raw = match.group("pre_num")
        stage_num = int(pre_num_raw) if pre_num_raw else 0
        is_prerelease = True
    elif dev is not None:
        stage_rank = 0
        dev_num_raw = match.group("dev_num")
        stage_num = int(dev_num_raw) if dev_num_raw else 0
        is_prerelease = True
    else:
        stage_rank = 4
        stage_num = 0
        is_prerelease = False

    return {
        "raw": text,
        "release": release,
        "stage_rank": stage_rank,
        "stage_num": stage_num,
        "is_prerelease": is_prerelease,
    }


def _version_sort_key(parsed: dict, length: int) -> tuple:
    release = parsed["release"] + (0,) * (length - len(parsed["release"]))
    return release + (parsed["stage_rank"], parsed["stage_num"])


def compare_versions(a_text: Any, b_text: Any) -> Optional[int]:
    """Compare two version strings numerically. Returns -1/0/1, or None when
    either side cannot be confidently parsed (never a lexical/string
    comparison, and never a guess).
    """
    a = parse_version(a_text)
    b = parse_version(b_text)
    if a is None or b is None:
        return None
    length = max(len(a["release"]), len(b["release"]))
    key_a = _version_sort_key(a, length)
    key_b = _version_sort_key(b, length)
    if key_a < key_b:
        return -1
    if key_a > key_b:
        return 1
    return 0


def _same_release_core(a: dict, b: dict) -> bool:
    length = max(len(a["release"]), len(b["release"]))
    pad_a = a["release"] + (0,) * (length - len(a["release"]))
    pad_b = b["release"] + (0,) * (length - len(b["release"]))
    return pad_a == pad_b


def _classify_dependency(
    current_version_text: str, stable_version_text: Optional[str]
) -> tuple:
    """Return (classification, from, to) where classification is one of
    'pass', 'prerelease-pinned', 'behind-stable', 'not-verified'. Never
    infers `to` from anything but the matrix's own `stable` field.
    """
    current = parse_version(current_version_text)
    if current is None:
        return "not-verified", None, None
    if not stable_version_text:
        return "pass", None, None
    stable = parse_version(stable_version_text)
    if stable is None:
        return "not-verified", None, None
    comparison = compare_versions(current_version_text, stable_version_text)
    if comparison is None:
        return "not-verified", None, None
    if comparison >= 0:
        return "pass", None, None
    if current["is_prerelease"] and _same_release_core(current, stable):
        return "prerelease-pinned", current_version_text, stable_version_text
    return "behind-stable", current_version_text, stable_version_text


# ---------------------------------------------------------------------------
# Compatibility matrix validation
# ---------------------------------------------------------------------------
_MATRIX_TOP_KEYS = frozenset({"schema", "version", "date", "source", "entries"})
_ENTRY_REQUIRED_KEYS = frozenset({
    "surface", "target", "state", "source", "last_reviewed", "review_window_days",
})
_ENTRY_OPTIONAL_KEYS = frozenset({"stable", "replacement", "expiry", "expiry_triggers"})
_ENTRY_ALL_KEYS = _ENTRY_REQUIRED_KEYS | _ENTRY_OPTIONAL_KEYS


def _require_date_str(value: Any, label: str, *, error=UpgradeMatrixError) -> date:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise error(f"{label} must be a YYYY-MM-DD date string")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise error(f"{label} must be a valid calendar date") from exc


def _require_nonempty_str(value: Any, label: str, *, error=UpgradeMatrixError) -> str:
    if not isinstance(value, str) or not value:
        raise error(f"{label} must be a non-empty string")
    return value


def validate_matrix(matrix: Any) -> None:
    """Validate `compatibility-matrix.json` shape. Raises `UpgradeMatrixError`
    before any scan is attempted — a malformed matrix never produces a
    partial/guessed manifest.
    """
    if not isinstance(matrix, dict):
        raise UpgradeMatrixError("matrix must be an object")
    missing = _MATRIX_TOP_KEYS.difference(matrix)
    if missing:
        raise UpgradeMatrixError(
            "matrix missing required key(s): " + ", ".join(sorted(missing))
        )
    unknown = set(matrix).difference(_MATRIX_TOP_KEYS)
    if unknown:
        raise UpgradeMatrixError(
            "matrix has unknown key(s): " + ", ".join(sorted(unknown))
        )
    if matrix["schema"] != MATRIX_SCHEMA:
        raise UpgradeMatrixError(f"matrix.schema must be {MATRIX_SCHEMA!r}")
    _require_nonempty_str(matrix["version"], "matrix.version")
    _require_date_str(matrix["date"], "matrix.date")
    _require_safe_reference(matrix["source"], "matrix.source")

    entries = matrix["entries"]
    if not isinstance(entries, list):
        raise UpgradeMatrixError("matrix.entries must be an array")

    seen_targets: set = set()
    for index, entry in enumerate(entries):
        label = f"matrix.entries[{index}]"
        if not isinstance(entry, dict):
            raise UpgradeMatrixError(f"{label} must be an object")
        missing = _ENTRY_REQUIRED_KEYS.difference(entry)
        if missing:
            raise UpgradeMatrixError(
                f"{label} missing required key(s): " + ", ".join(sorted(missing))
            )
        unknown = set(entry).difference(_ENTRY_ALL_KEYS)
        if unknown:
            raise UpgradeMatrixError(
                f"{label} has unknown key(s): " + ", ".join(sorted(unknown))
            )
        if entry["surface"] not in SURFACES:
            raise UpgradeMatrixError(
                f"{label}.surface must be one of {sorted(SURFACES)}"
            )
        target = _require_nonempty_str(entry["target"], f"{label}.target")
        if entry["state"] not in STATES:
            raise UpgradeMatrixError(f"{label}.state must be one of {sorted(STATES)}")
        _require_safe_reference(entry["source"], f"{label}.source")
        _require_date_str(entry["last_reviewed"], f"{label}.last_reviewed")

        review_window_days = entry["review_window_days"]
        if (
            isinstance(review_window_days, bool)
            or not isinstance(review_window_days, int)
            or review_window_days <= 0
        ):
            raise UpgradeMatrixError(
                f"{label}.review_window_days must be a positive integer"
            )
        if "stable" in entry:
            _require_nonempty_str(entry["stable"], f"{label}.stable")
        if "replacement" in entry:
            _require_nonempty_str(entry["replacement"], f"{label}.replacement")
        if "expiry" in entry:
            _require_date_str(entry["expiry"], f"{label}.expiry")
        if "expiry_triggers" in entry:
            triggers = entry["expiry_triggers"]
            if not isinstance(triggers, list) or not triggers:
                raise UpgradeMatrixError(
                    f"{label}.expiry_triggers must be a non-empty array"
                )
            for trigger_index, trigger in enumerate(triggers):
                _require_nonempty_str(
                    trigger, f"{label}.expiry_triggers[{trigger_index}]"
                )

        if target in seen_targets:
            raise UpgradeMatrixError(f"duplicate matrix target {target!r}")
        seen_targets.add(target)


def load_matrix(path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        matrix = json.load(handle)
    validate_matrix(matrix)
    return matrix


def _index_matrix_entries(entries: list) -> dict:
    return {entry["target"]: entry for entry in entries}


# ---------------------------------------------------------------------------
# Project normalization helpers
# ---------------------------------------------------------------------------
def _coerce_date(today: Any) -> date:
    if isinstance(today, datetime):
        return today.date()
    if isinstance(today, date):
        return today
    if isinstance(today, str):
        try:
            return datetime.strptime(today, "%Y-%m-%d").date()
        except ValueError as exc:
            raise UpgradeProjectError(
                f"today must be a YYYY-MM-DD string, got {today!r}"
            ) from exc
    raise UpgradeProjectError("today must be a date, datetime, or YYYY-MM-DD string")


def _project_object_field(project: dict, field: str) -> dict:
    value = project.get(field)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise UpgradeProjectError(f"project.{field} must be an object")
    return value


def _project_array_field(project: dict, field: str) -> list:
    value = project.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise UpgradeProjectError(f"project.{field} must be an array")
    return value


def _validate_string_mapping(value: dict, label: str) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise UpgradeProjectError(f"{label} keys must be non-empty strings")
        if not isinstance(item, str) or not item.strip():
            raise UpgradeProjectError(
                f"{label}[{key!r}] must be a non-empty string"
            )


def _validate_string_array(value: list, label: str) -> None:
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise UpgradeProjectError(
                f"{label}[{index}] must be a non-empty string"
            )


def _normalize_project(project: Any) -> dict:
    if not isinstance(project, dict):
        raise UpgradeProjectError("project must be an object")

    normalized = dict(project)
    for field in ("dependencies", "runtime_policy"):
        mapping = _project_object_field(project, field)
        _validate_string_mapping(mapping, f"project.{field}")
        normalized[field] = dict(mapping)

    for field in ("model_families", "triggered_expiry_conditions"):
        values = _project_array_field(project, field)
        _validate_string_array(values, f"project.{field}")
        normalized[field] = list(values)

    artifact_paths = _project_object_field(project, "artifact_paths")
    for surface, path in artifact_paths.items():
        if not isinstance(surface, str) or not surface.strip():
            raise UpgradeProjectError(
                "project.artifact_paths keys must be non-empty strings"
            )
        if surface not in SURFACES:
            raise UpgradeProjectError(
                f"project.artifact_paths has unknown surface {surface!r}"
            )
        _require_safe_path(path, f"project.artifact_paths[{surface!r}]")
    normalized["artifact_paths"] = dict(artifact_paths)

    dependency_paths = _project_object_field(project, "dependency_paths")
    for name, path in dependency_paths.items():
        if not isinstance(name, str) or not name.strip():
            raise UpgradeProjectError(
                "project.dependency_paths keys must be non-empty strings"
            )
        _require_safe_path(path, f"project.dependency_paths[{name!r}]")
    normalized["dependency_paths"] = dict(dependency_paths)
    return normalized


def _normalize_artifact_paths(raw: Any) -> tuple:
    """Return (paths, explicit_surfaces): the merged surface->path map (matrix
    defaults overlaid with any caller override) plus the set of surfaces the
    caller *explicitly* overrode. The explicit set lets dependency-provenance
    (which artifact a pin was actually parsed from) win over a mere default
    while still yielding to a deliberate operator override.
    """
    paths = dict(DEFAULT_ARTIFACT_PATHS)
    explicit: set = set()
    if raw is None:
        return paths, explicit
    if not isinstance(raw, dict):
        raise UpgradeProjectError("project.artifact_paths must be an object")
    for surface, path in raw.items():
        if surface not in SURFACES:
            raise UpgradeProjectError(
                f"project.artifact_paths has unknown surface {surface!r}"
            )
        paths[surface] = _require_safe_path(path, f"artifact_paths[{surface}]")
        explicit.add(surface)
    return paths, explicit


def _normalize_dependency_paths(raw: Any) -> dict:
    """Return `{dependency-name: repo-relative-path}` recording which artifact
    each dependency pin was actually parsed from (a package.json pin -> the
    package.json path, a pyproject pin -> the pyproject path). Never guessed;
    only ever populated from a real, supplied artifact.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise UpgradeProjectError("project.dependency_paths must be an object")
    result: dict = {}
    for name, path in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise UpgradeProjectError(
                "project.dependency_paths keys must be non-empty strings"
            )
        result[name] = _require_safe_path(path, f"dependency_paths[{name}]")
    return result


def _artifact_path_for(
    surface: str,
    name: Optional[str],
    artifact_paths: dict,
    explicit_surfaces: set,
    dependency_paths: dict,
) -> str:
    """Resolve the plan-item artifact path for a surface/dependency with a
    deterministic, source-recorded precedence: an explicit `artifact_paths`
    override wins first, then the dependency's actual parsed-from provenance,
    then the matrix default for the surface. A JS dependency is therefore
    never silently attributed to `pyproject.toml`.
    """
    if surface in explicit_surfaces:
        return artifact_paths[surface]
    if name is not None and name in dependency_paths:
        return dependency_paths[name]
    return artifact_paths.get(surface, DEFAULT_ARTIFACT_PATHS[surface])


def _plan_item(path: str, reason: str, from_value, to_value) -> dict:
    return {"path": path, "reason": reason, "from": from_value, "to": to_value}


def _finalize_plan(items: list) -> list:
    """De-duplicate by (path, reason), sort deterministically, and assign a
    1-based, strictly increasing `order`.
    """
    unique: dict = {}
    for item in items:
        key = (item["path"], item["reason"])
        unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: (item["path"], item["reason"]))
    return [
        {
            "order": index + 1,
            "path": item["path"],
            "reason": item["reason"],
            "from": item["from"],
            "to": item["to"],
        }
        for index, item in enumerate(ordered)
    ]


# ---------------------------------------------------------------------------
# UPG-001 — matrix/dependency staleness
# ---------------------------------------------------------------------------
def check_matrix_and_dependency_staleness(
    project: dict,
    entries: list,
    entries_by_target: dict,
    today: date,
    artifact_paths: dict,
    explicit_surfaces: set,
    dependency_paths: dict,
) -> tuple:
    stale_entries = []
    for entry in entries:
        last_reviewed = _require_date_str(
            entry["last_reviewed"], "entry.last_reviewed", error=UpgradeProjectError
        )
        age_days = (today - last_reviewed).days
        if age_days > entry["review_window_days"]:
            stale_entries.append({
                "surface": entry["surface"],
                "target": entry["target"],
                "last_reviewed": entry["last_reviewed"],
                "review_window_days": entry["review_window_days"],
                "age_days": age_days,
            })
    stale_entries.sort(key=lambda item: (item["surface"], item["target"]))

    behind_stable = []
    prerelease_pinned = []
    not_verified_deps = []
    plan_items = []

    dependencies = project["dependencies"]

    for name in sorted(dependencies):
        version_text = dependencies[name]
        entry = entries_by_target.get(name)
        if entry is None or entry["surface"] not in DEPENDENCY_SURFACES:
            continue
        classification, current, stable = _classify_dependency(
            version_text, entry.get("stable")
        )
        path = _artifact_path_for(
            entry["surface"], name, artifact_paths, explicit_surfaces, dependency_paths
        )
        if classification == "not-verified":
            not_verified_deps.append(name)
        elif classification == "prerelease-pinned":
            prerelease_pinned.append({"name": name, "from": current, "to": stable})
            plan_items.append(
                _plan_item(
                    path, f"{name} is pinned to prerelease {current}", current, stable
                )
            )
        elif classification == "behind-stable":
            behind_stable.append({"name": name, "from": current, "to": stable})
            plan_items.append(
                _plan_item(
                    path,
                    f"{name} is behind the matrix stable release {stable}",
                    current,
                    stable,
                )
            )
        # 'pass' -> nothing to report

    has_drift = bool(behind_stable or prerelease_pinned)
    if not_verified_deps:
        status = "not-verified"
        reason = "dependency-version-not-verified"
    elif stale_entries and has_drift:
        status = "should-fix"
        reason = "matrix-stale-and-dependency-drift"
    elif has_drift:
        status = "should-fix"
        reason = "dependency-drift"
    elif stale_entries:
        status = "should-fix"
        reason = "matrix-stale"
    else:
        status = "pass"
        reason = "fresh"

    detail = {"reason": reason}
    if stale_entries:
        detail["stale_entries"] = stale_entries
    if behind_stable:
        detail["dependencies_behind_stable"] = behind_stable
    if prerelease_pinned:
        detail["dependencies_prerelease_pinned"] = prerelease_pinned
    if not_verified_deps:
        detail["dependencies_not_verified"] = sorted(not_verified_deps)

    finding = {"id": "UPG-001", "status": status, "detail": detail}
    return finding, plan_items


# ---------------------------------------------------------------------------
# UPG-002 — preview/runtime-policy expiry drift
# ---------------------------------------------------------------------------
def _collect_usages(project: dict) -> list:
    """Return [(surface, label, target), ...] for every non-dependency
    surface the project declares usage of.
    """
    usages = []

    runtime_policy = project["runtime_policy"]
    for agent_name in sorted(runtime_policy):
        target = runtime_policy[agent_name]
        usages.append(("hosted-agent-protocol", agent_name, target))

    governance_profile = project.get("governance_profile")
    if governance_profile:
        if not isinstance(governance_profile, str):
            raise UpgradeProjectError("project.governance_profile must be a string")
        usages.append(("governance-profile", "governance_profile", governance_profile))

    model_families = project["model_families"]
    for family in sorted(model_families):
        usages.append(("model-family", family, family))

    return usages


def check_usage_drift(
    project: dict, entries_by_target: dict, today: date, artifact_paths: dict
) -> tuple:
    preview_usages = []
    deprecated_usages = []
    expired_decisions = []
    not_in_matrix = []
    plan_items = []

    raw_triggered = project["triggered_expiry_conditions"]
    triggered = set(raw_triggered)

    for surface, label, target in _collect_usages(project):
        entry = entries_by_target.get(target)
        if entry is None or entry["surface"] != surface:
            not_in_matrix.append(f"{label}:{target}")
            continue

        state = entry["state"]
        if state == "stable":
            continue

        path = artifact_paths.get(surface, DEFAULT_ARTIFACT_PATHS[surface])
        replacement = entry.get("replacement") or entry.get("stable")
        expiry_text = entry.get("expiry")
        expired = False
        if expiry_text:
            expiry_dt = _require_date_str(
                expiry_text, "entry.expiry", error=UpgradeProjectError
            )
            expired = today >= expiry_dt

        if state == "deprecated" or expired:
            record = {"label": label, "target": target, "state": state}
            if expired:
                record["expiry"] = expiry_text
                expired_decisions.append(record)
                if state == "preview":
                    reason_text = (
                        f"{label} targets {target}, whose preview decision "
                        f"expired on {expiry_text}"
                    )
                else:
                    reason_text = (
                        f"{label} targets {target}, which was deprecated and "
                        f"expired on {expiry_text}"
                    )
            else:
                deprecated_usages.append(record)
                reason_text = f"{label} targets deprecated surface {target}"
            plan_items.append(_plan_item(path, reason_text, target, replacement))
            continue

        # state == "preview" and not expired
        expiry_triggers = set(entry.get("expiry_triggers") or [])
        fired = sorted(expiry_triggers & triggered)
        record = {"label": label, "target": target, "state": state}
        if fired:
            record["fired_triggers"] = fired
            reason_text = (
                f"{label} targets preview surface {target}, which the "
                f"official source is retiring ({', '.join(fired)} triggered)"
            )
        else:
            reason_text = f"{label} targets preview surface {target}"
        preview_usages.append(record)
        plan_items.append(_plan_item(path, reason_text, target, replacement))

    if not_in_matrix:
        status = "not-verified"
        reason = "usage-target-not-in-matrix"
    elif preview_usages or deprecated_usages or expired_decisions:
        status = "should-fix"
        reason = "preview-or-deprecated-usage"
    else:
        status = "pass"
        reason = "no-drift"

    detail = {"reason": reason}
    if preview_usages:
        detail["preview_usages"] = preview_usages
    if deprecated_usages:
        detail["deprecated_usages"] = deprecated_usages
    if expired_decisions:
        detail["expired_decisions"] = expired_decisions
    if not_in_matrix:
        detail["not_in_matrix"] = sorted(not_in_matrix)

    finding = {"id": "UPG-002", "status": status, "detail": detail}
    return finding, plan_items


# ---------------------------------------------------------------------------
# UPG-003 — official source verification (fixture-driven, no network)
# ---------------------------------------------------------------------------
def _referenced_target_paths(
    project: dict,
    artifact_paths: dict,
    entries_by_target: dict,
    explicit_surfaces: set,
    dependency_paths: dict,
) -> dict:
    """Map "surface:target" -> artifact path for every surface/target the
    project actually references (dependencies + usages), so a confirmed
    preview-to-GA transition only produces a plan item when it is actually
    actionable for this project. Dependency targets carry the same
    parsed-from provenance as UPG-001, so a JS dependency's transition plan
    item points at the real package.json, never a defaulted pyproject.toml.
    """
    mapping: dict = {}

    dependencies = project["dependencies"]
    for name in dependencies:
        entry = entries_by_target.get(name)
        if entry and entry["surface"] in DEPENDENCY_SURFACES:
            path = _artifact_path_for(
                entry["surface"], name, artifact_paths, explicit_surfaces, dependency_paths
            )
            mapping[f"{entry['surface']}:{name}"] = path

    for surface, _label, target in _collect_usages(project):
        entry = entries_by_target.get(target)
        if entry and entry["surface"] == surface:
            path = _artifact_path_for(
                surface, None, artifact_paths, explicit_surfaces, dependency_paths
            )
            mapping[f"{surface}:{target}"] = path

    return mapping


def check_source_verification(
    entries: list, source_results: Optional[dict], usage_paths: dict
) -> tuple:
    check_keys = sorted(f"{entry['surface']}:{entry['target']}" for entry in entries)

    if not source_results:
        detail = {
            "reason": "official-source-unavailable",
            "message": SOURCE_UNAVAILABLE_MESSAGE,
        }
        if check_keys:
            detail["unverified_checks"] = check_keys
        finding = {"id": "UPG-003", "status": "not-verified", "detail": detail}
        return finding, []

    if not isinstance(source_results, dict):
        raise UpgradeProjectError("source_results must be an object or None")

    unverified = []
    transitions = []
    plan_items = []

    for entry in entries:
        key = f"{entry['surface']}:{entry['target']}"
        result = source_results.get(key)
        if not isinstance(result, dict) or "state" not in result:
            unverified.append(key)
            continue
        source_state = result["state"]
        if source_state not in STATES:
            unverified.append(key)
            continue
        if source_state == entry["state"]:
            continue

        transition = {
            "surface": entry["surface"],
            "target": entry["target"],
            "from_state": entry["state"],
            "to_state": source_state,
        }
        latest_version = result.get("latest_version")
        if isinstance(latest_version, str) and latest_version:
            transition["latest_version"] = latest_version
        transitions.append(transition)

        path = usage_paths.get(key)
        if path:
            to_value = latest_version or entry.get("replacement") or entry.get("stable") or source_state
            plan_items.append(
                _plan_item(
                    path,
                    f"official source confirms {entry['target']} moved from "
                    f"{entry['state']} to {source_state}",
                    entry["state"],
                    to_value,
                )
            )

    if unverified:
        status = "not-verified"
        detail = {
            "reason": "official-source-unverified",
            "message": SOURCE_UNAVAILABLE_MESSAGE,
            "unverified_checks": sorted(unverified),
        }
        # A coexisting confirmed transition is preserved even though the
        # overall check remains not-verified for the checks that are gaps —
        # mirrors threadlight-ground's "negative evidence is never erased by
        # a coverage gap" pattern. No `latest_version` above was fabricated
        # for any of the *unverified* checks themselves.
        if transitions:
            detail["transitions"] = transitions
        finding = {"id": "UPG-003", "status": status, "detail": detail}
        # Plan items for confirmed transitions are only meaningful once they
        # are actually corroborated; keep them even though the finding as a
        # whole is not-verified (a partial, honest scan still yields a
        # partial, honest plan).
        return finding, plan_items

    if transitions:
        finding = {
            "id": "UPG-003",
            "status": "should-fix",
            "detail": {"reason": "preview-to-ga-transition", "transitions": transitions},
        }
        return finding, plan_items

    finding = {
        "id": "UPG-003",
        "status": "pass",
        "detail": {"reason": "official-source-verified"},
    }
    return finding, plan_items


# ---------------------------------------------------------------------------
# freshness.source_oldest_at — matrix review dates + any source check dates
# actually used, or None honestly when nothing is available.
# ---------------------------------------------------------------------------
def _compute_source_oldest_at(entries: list, source_results: Optional[dict]) -> Optional[str]:
    candidates = []
    for entry in entries:
        parsed = _require_date_str(
            entry["last_reviewed"], "entry.last_reviewed", error=UpgradeProjectError
        )
        candidates.append(datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc))

    if isinstance(source_results, dict):
        for result in source_results.values():
            if not isinstance(result, dict):
                continue
            checked_at = result.get("checked_at")
            if isinstance(checked_at, str) and _DATE_RE.match(checked_at):
                try:
                    parsed = datetime.strptime(checked_at, "%Y-%m-%d").date()
                except ValueError:
                    continue
                candidates.append(
                    datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
                )

    if not candidates:
        return None
    return min(candidates).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Manifest schema validation — hand-rolled mirror of
# references/upgrade-manifest.schema.json. stdlib-only; a test-only
# jsonschema parity suite pins this to the shipped schema file.
# ---------------------------------------------------------------------------
_DETAIL_STRING_LIST_KEYS = frozenset(
    {"dependencies_not_verified", "not_in_matrix", "unverified_checks"}
)
_DETAIL_OBJECT_SPECS = {
    "stale_entries": (
        {"surface", "target", "last_reviewed", "review_window_days", "age_days"},
        frozenset(),
    ),
    "dependencies_behind_stable": ({"name", "from", "to"}, frozenset()),
    "dependencies_prerelease_pinned": ({"name", "from", "to"}, frozenset()),
    "preview_usages": ({"label", "target", "state"}, frozenset({"fired_triggers"})),
    "deprecated_usages": ({"label", "target", "state"}, frozenset()),
    "expired_decisions": ({"label", "target", "state", "expiry"}, frozenset()),
    "transitions": (
        {"surface", "target", "from_state", "to_state"},
        frozenset({"latest_version"}),
    ),
}
_DETAIL_ALLOWED_KEYS = frozenset(
    {"reason", "message", *_DETAIL_STRING_LIST_KEYS, *_DETAIL_OBJECT_SPECS}
)
_FINDING_REASON_ENUM = frozenset({
    "fresh", "matrix-stale", "dependency-drift", "dependency-version-not-verified",
    "matrix-stale-and-dependency-drift",
    "no-drift", "preview-or-deprecated-usage", "usage-target-not-in-matrix",
    "official-source-unavailable", "official-source-unverified",
    "preview-to-ga-transition", "official-source-verified",
})
_MANIFEST_TOP_LEVEL_KEYS = frozenset(
    {"schema", "tool_version", "generated_at", "freshness", "status", "findings", "plan"}
)
_PLAN_ITEM_KEYS = frozenset({"order", "path", "reason", "from", "to"})


def _require_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{label} must be an object")
    return value


def _require_keys(value: dict, required: set, label: str) -> None:
    missing = required.difference(value)
    if missing:
        raise ManifestValidationError(
            f"{label} missing required key(s): " + ", ".join(sorted(missing))
        )


def _reject_unknown_keys(value: dict, allowed: set, label: str) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise ManifestValidationError(
            f"{label} has unknown key(s): " + ", ".join(sorted(unknown))
        )


def _require_string(value: Any, label: str, *, min_length: int = 0) -> None:
    if not isinstance(value, str) or len(value) < min_length:
        suffix = "a non-empty string" if min_length else "a string"
        raise ManifestValidationError(f"{label} must be {suffix}")


def _require_nullable_string(value: Any, label: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ManifestValidationError(f"{label} must be a non-empty string or null")


def _require_array(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{label} must be an array")
    return value


def _require_string_array(value: Any, label: str, *, min_length: int = 0) -> None:
    for index, item in enumerate(_require_array(value, label)):
        _require_string(item, f"{label}[{index}]", min_length=min_length)


def _require_int(value: Any, label: str, *, minimum=None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestValidationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ManifestValidationError(f"{label} must be >= {minimum}")


def _validate_detail(detail: Any, label: str) -> None:
    detail = _require_object(detail, label)
    _require_keys(detail, {"reason"}, label)
    _reject_unknown_keys(detail, _DETAIL_ALLOWED_KEYS, label)
    if detail["reason"] not in _FINDING_REASON_ENUM:
        raise ManifestValidationError(f"{label}.reason must be a known reason code")
    if "message" in detail:
        _require_string(detail["message"], f"{label}.message", min_length=1)
    for key in _DETAIL_STRING_LIST_KEYS:
        if key in detail:
            _require_string_array(detail[key], f"{label}.{key}", min_length=1)
    for key, (required_sub, optional_sub) in _DETAIL_OBJECT_SPECS.items():
        if key not in detail:
            continue
        allowed_sub = required_sub | optional_sub
        for item_index, item in enumerate(_require_array(detail[key], f"{label}.{key}")):
            item_label = f"{label}.{key}[{item_index}]"
            item = _require_object(item, item_label)
            _require_keys(item, required_sub, item_label)
            _reject_unknown_keys(item, allowed_sub, item_label)
            for sub_key, sub_value in item.items():
                if sub_key == "review_window_days":
                    _require_int(sub_value, f"{item_label}.{sub_key}", minimum=1)
                elif sub_key == "age_days":
                    _require_int(sub_value, f"{item_label}.{sub_key}", minimum=0)
                elif sub_key == "state" or sub_key.endswith("_state"):
                    if sub_value not in STATES:
                        raise ManifestValidationError(
                            f"{item_label}.{sub_key} must be one of {sorted(STATES)}"
                        )
                elif sub_key == "fired_triggers":
                    _require_string_array(
                        sub_value, f"{item_label}.{sub_key}", min_length=1
                    )
                else:
                    _require_string(sub_value, f"{item_label}.{sub_key}", min_length=1)


def validate_upgrade_manifest(manifest: dict) -> None:
    """Hand-rolled schema check mirroring
    `references/upgrade-manifest.schema.json`, layered on the shared
    envelope's own validation. The recursive forbidden-key/secret-value scan
    runs FIRST so unsafe content fails loud before any shape check.
    """
    validate_envelope(manifest)
    manifest = _require_object(manifest, "upgrade manifest")
    _assert_no_unsafe_content(manifest)
    _require_keys(manifest, {"plan"}, "upgrade manifest")
    _reject_unknown_keys(manifest, _MANIFEST_TOP_LEVEL_KEYS, "upgrade manifest")

    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ManifestValidationError(f"schema must be {MANIFEST_SCHEMA!r}")

    seen_ids = set()
    for index, finding in enumerate(_require_array(manifest["findings"], "findings")):
        label = f"findings[{index}]"
        finding = _require_object(finding, label)
        _require_keys(finding, {"id", "status"}, label)
        _reject_unknown_keys(finding, {"id", "status", "detail"}, label)
        if finding["id"] not in FINDING_IDS:
            raise ManifestValidationError(f"{label}.id must be one of {FINDING_IDS}")
        if finding["id"] in seen_ids:
            raise ManifestValidationError(f"duplicate finding id {finding['id']!r}")
        seen_ids.add(finding["id"])
        if finding["status"] not in FINDING_STATUS_ENUM:
            raise ManifestValidationError(
                f"{label}.status must be one of {sorted(FINDING_STATUS_ENUM)}"
            )
        if "detail" in finding:
            _validate_detail(finding["detail"], f"{label}.detail")

    if set(FINDING_IDS) != seen_ids:
        raise ManifestValidationError(
            "findings must contain exactly " + ", ".join(FINDING_IDS)
        )

    plan = _require_array(manifest["plan"], "plan")
    seen_plan_keys = set()
    previous_order = 0
    for index, item in enumerate(plan):
        label = f"plan[{index}]"
        item = _require_object(item, label)
        _require_keys(item, _PLAN_ITEM_KEYS, label)
        _reject_unknown_keys(item, _PLAN_ITEM_KEYS, label)
        _require_int(item["order"], f"{label}.order", minimum=1)
        if item["order"] != previous_order + 1:
            raise ManifestValidationError(
                f"{label}.order must be consecutive starting at 1"
            )
        previous_order = item["order"]
        if not isinstance(item["path"], str) or not _SAFE_PATH_RE.match(item["path"]):
            raise ManifestValidationError(
                f"{label}.path must be a repo-relative path (no absolute "
                f"path, no '..' traversal, no URL scheme)"
            )
        _require_string(item["reason"], f"{label}.reason", min_length=1)
        _require_nullable_string(item["from"], f"{label}.from")
        _require_nullable_string(item["to"], f"{label}.to")

        plan_key = (item["path"], item["reason"])
        if plan_key in seen_plan_keys:
            raise ManifestValidationError(
                f"duplicate plan item for path/reason {plan_key!r}"
            )
        seen_plan_keys.add(plan_key)


# ---------------------------------------------------------------------------
# scan_project — the public entry point
# ---------------------------------------------------------------------------
def scan_project(
    project: dict,
    matrix: dict,
    today: Any,
    source_results: Optional[dict] = None,
) -> dict:
    """Scan a normalized *project* against a versioned compatibility
    *matrix* as of *today*, returning a fully schema-validated
    `threadlight.upgrade/v1` manifest. Never edits anything, never makes a
    network call — *source_results* is an injectable, fixture-driven mapping
    ("surface:target" -> {"state": ..., "latest_version": ..., "checked_at":
    ...}); an absent/omitted entry is honestly reported as not-verified.

    *project* keys (all optional): `dependencies` (name -> version string),
    `runtime_policy` (agent name -> hosted-agent-protocol target),
    `governance_profile` (a single target string), `model_families` (a list
    of target strings), `triggered_expiry_conditions` (a list of official
    trigger names already confirmed to have fired — never inferred here),
    `artifact_paths` (surface -> repo-relative path override), and
    `dependency_paths` (dependency name -> the repo-relative artifact the pin
    was actually parsed from, e.g. a package.json pin -> that package.json).
    A `dependency_paths` entry supplies deterministic plan-item provenance
    (below an explicit `artifact_paths` override, above the surface default),
    so a JS pin is never silently attributed to `pyproject.toml`.
    """
    project = _normalize_project(project)

    validate_matrix(matrix)
    today_date = _coerce_date(today)
    entries = matrix["entries"]
    entries_by_target = _index_matrix_entries(entries)
    artifact_paths, explicit_surfaces = _normalize_artifact_paths(
        project.get("artifact_paths")
    )
    dependency_paths = _normalize_dependency_paths(project.get("dependency_paths"))

    upg001, upg001_plan = check_matrix_and_dependency_staleness(
        project, entries, entries_by_target, today_date,
        artifact_paths, explicit_surfaces, dependency_paths,
    )
    upg002, upg002_plan = check_usage_drift(
        project, entries_by_target, today_date, artifact_paths
    )
    usage_paths = _referenced_target_paths(
        project, artifact_paths, entries_by_target, explicit_surfaces, dependency_paths
    )
    upg003, upg003_plan = check_source_verification(entries, source_results, usage_paths)

    findings = [upg001, upg002, upg003]
    plan = _finalize_plan([*upg001_plan, *upg002_plan, *upg003_plan])

    status = (
        "partial"
        if any(finding["status"] == "not-verified" for finding in findings)
        else "complete"
    )

    manifest = build_envelope(
        schema=MANIFEST_SCHEMA,
        tool_version=TOOL_VERSION,
        status=status,
        generated_at=_now_iso(),
        valid_for_hours=24,
        source_oldest_at=_compute_source_oldest_at(entries, source_results),
        findings=findings,
        payload={"plan": plan},
    )
    # Validate the whole manifest (schema shape + forbidden keys + secret
    # values) before returning, so a caller/`--json`/disk never sees invalid
    # or oversharing data.
    validate_upgrade_manifest(manifest)
    return manifest


def write_upgrade_manifest(path, manifest: dict) -> None:
    """Schema-validate + forbidden-key/secret scan, THEN atomically write. A
    prior valid manifest at *path* is untouched unless every check passes.
    """
    validate_upgrade_manifest(manifest)
    atomic_write_json(path, manifest)


# ---------------------------------------------------------------------------
# Read-only project-fixture parsers (best-effort convenience — the CLI never
# writes to any of these files, and `scan_project` never requires them).
#
# Both parsers share one exactness contract: a dependency spec is only ever
# reduced to a bare, comparable version when the WHOLE spec (after an optional
# single exact-equality operator) is a version `parse_version` can confidently
# place. Every range (`^`/`~`/`>=`/`<=`/`>`/`<`), compound (`>=1.2 <2`), OR
# (`||`), hyphen (`1 - 2`), wildcard (`*`/`1.x`/`==1.2.*`), dist-tag
# (`latest`), and `workspace:`/`file:`/`link:`/`git`/URL spec is "ambiguous":
# represented verbatim so it surfaces as `not-verified` downstream — never
# stripped to a guessable core (a should-fix guess), never silently dropped,
# and never an `IndexError` on an all-operator spec.
# ---------------------------------------------------------------------------
_DEP_SPLIT_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:\[[^\]]*\])?\s*(.*)$")
# An exact npm spec: an optional single leading `=` (never `>=`/`<=`/`^`/`~`/
# `>`/`<`) then a bare version literal, and nothing else.
_NPM_EXACT_RE = re.compile(r"^=?\s*(v?\d[0-9A-Za-z.+-]*)$", re.IGNORECASE)
# An exact PEP 508 constraint: a single `==`/`===` clause with a literal
# version and nothing else (no `,` compound, no `.*` wildcard, no `~=`).
_PEP_EXACT_RE = re.compile(r"^===?\s*(v?\d[0-9A-Za-z.+-]*)$", re.IGNORECASE)


def _classify_spec(spec: str, exact_pattern) -> tuple:
    """Classify one dependency spec as ('exact', bare-version),
    ('ambiguous', spec) — represented so it yields not-verified — or
    ('empty', None) when there is no constraint at all. Only a spec whose
    entire body is a confidently exact version is 'exact'; a range/compound/
    wildcard/protocol/tag spec is 'ambiguous', never guessed.
    """
    text = spec.strip()
    if not text:
        return "empty", None
    match = exact_pattern.match(text)
    if match and parse_version(match.group(1)) is not None:
        return "exact", match.group(1)
    return "ambiguous", text


def _merge_dependency_value(
    dependencies: dict, sources: dict, name: str, value: str, source: str
) -> None:
    if name in dependencies and dependencies[name] != value:
        raise UpgradeProjectError(
            f"pyproject.toml has conflicting specs for dependency {name!r} "
            f"in {sources[name]} and {source}"
        )
    dependencies[name] = value
    sources[name] = source


def _parse_pep_requirement(requirement: Any, label: str) -> Optional[tuple]:
    if not isinstance(requirement, str) or not requirement.strip():
        raise UpgradeProjectError(f"{label} must be a non-empty string")
    match = _DEP_SPLIT_RE.match(requirement)
    if not match:
        raise UpgradeProjectError(f"{label} must be a valid dependency string")
    name = match.group(1)
    constraint = match.group(2).split(";", 1)[0].strip()
    kind, value = _classify_spec(constraint, _PEP_EXACT_RE)
    if kind == "empty":
        return None
    return name, value


def _parse_poetry_spec(spec: str) -> tuple:
    kind, value = _classify_spec(spec, _PEP_EXACT_RE)
    if kind == "ambiguous":
        kind, value = _classify_spec(spec, _NPM_EXACT_RE)
    return kind, value


def parse_pyproject_dependencies(text: str) -> dict:
    """Extract `{name: spec}` from PEP 621 dependency arrays (including
    optional groups) and Poetry dependency tables using `tomllib`. A
    confidently exact literal pin is reduced to a bare, comparable version;
    every other constraint is represented verbatim so it surfaces as
    not-verified downstream — never guessed. An unpinned PEP 621 bare name is
    skipped. Present sections and entries are shape-checked before use.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return {}

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UpgradeProjectError(f"invalid pyproject.toml: {exc}") from exc

    dependencies: dict = {}
    sources: dict = {}

    project = data.get("project")
    if project is not None:
        if not isinstance(project, dict):
            raise UpgradeProjectError("pyproject.toml project must be a table")
        if "dependencies" in project:
            project_dependencies = project["dependencies"]
            if not isinstance(project_dependencies, list):
                raise UpgradeProjectError(
                    "pyproject.toml project.dependencies must be an array"
                )
            for index, requirement in enumerate(project_dependencies):
                label = f"pyproject.toml project.dependencies[{index}]"
                parsed = _parse_pep_requirement(requirement, label)
                if parsed is not None:
                    name, value = parsed
                    _merge_dependency_value(
                        dependencies, sources, name, value, label
                    )

        if "optional-dependencies" in project:
            optional = project["optional-dependencies"]
            if not isinstance(optional, dict):
                raise UpgradeProjectError(
                    "pyproject.toml project.optional-dependencies must be a table"
                )
            for group, requirements in optional.items():
                if not isinstance(group, str) or not group.strip():
                    raise UpgradeProjectError(
                        "pyproject.toml project.optional-dependencies keys must "
                        "be non-empty strings"
                    )
                group_label = (
                    f"pyproject.toml project.optional-dependencies[{group!r}]"
                )
                if not isinstance(requirements, list):
                    raise UpgradeProjectError(f"{group_label} must be an array")
                for index, requirement in enumerate(requirements):
                    label = f"{group_label}[{index}]"
                    parsed = _parse_pep_requirement(requirement, label)
                    if parsed is not None:
                        name, value = parsed
                        _merge_dependency_value(
                            dependencies, sources, name, value, label
                        )

    tool = data.get("tool")
    if tool is not None:
        if not isinstance(tool, dict):
            raise UpgradeProjectError("pyproject.toml tool must be a table")
        poetry = tool.get("poetry")
        if poetry is not None:
            if not isinstance(poetry, dict):
                raise UpgradeProjectError(
                    "pyproject.toml tool.poetry must be a table"
                )
            poetry_tables = []
            for table_name in ("dependencies", "dev-dependencies"):
                if table_name in poetry:
                    poetry_tables.append(
                        (f"tool.poetry.{table_name}", poetry[table_name])
                    )
            groups = poetry.get("group")
            if groups is not None:
                if not isinstance(groups, dict):
                    raise UpgradeProjectError(
                        "pyproject.toml tool.poetry.group must be a table"
                    )
                for group_name, group in groups.items():
                    if not isinstance(group_name, str) or not group_name.strip():
                        raise UpgradeProjectError(
                            "pyproject.toml tool.poetry.group keys must be "
                            "non-empty strings"
                        )
                    group_label = f"tool.poetry.group.{group_name}"
                    if not isinstance(group, dict):
                        raise UpgradeProjectError(
                            f"pyproject.toml {group_label} must be a table"
                        )
                    if "dependencies" in group:
                        poetry_tables.append(
                            (
                                f"{group_label}.dependencies",
                                group["dependencies"],
                            )
                        )

            for table_name, dependency_table in poetry_tables:
                table_label = f"pyproject.toml {table_name}"
                if not isinstance(dependency_table, dict):
                    raise UpgradeProjectError(f"{table_label} must be a table")
                for name, spec in dependency_table.items():
                    if not isinstance(name, str) or not name.strip():
                        raise UpgradeProjectError(
                            f"{table_label} keys must be non-empty strings"
                        )
                    item_label = f"{table_label}[{name!r}]"
                    if not isinstance(spec, str) or not spec.strip():
                        raise UpgradeProjectError(
                            f"{item_label} must be a non-empty string"
                        )
                    kind, value = _parse_poetry_spec(spec)
                    if kind == "empty":
                        continue
                    _merge_dependency_value(
                        dependencies, sources, name, value, item_label
                    )
    return dependencies


def parse_package_json_dependencies(text: str) -> dict:
    """Extract `{name: spec}` from all four package.json dependency sections.
    Only a confidently exact literal version (optionally a single leading `=`)
    is reduced to a bare, comparable version; every range, compound, OR,
    hyphen, wildcard, dist-tag, and
    `workspace:`/`file:`/`link:`/`git`/URL spec is represented verbatim so it
    surfaces as not-verified downstream — never stripped to a guessable core,
    never silently dropped, and never an IndexError on an all-operator spec.
    Conflicting declarations across sections are rejected.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UpgradeProjectError(f"invalid package.json: {exc}") from exc
    if not isinstance(data, dict):
        raise UpgradeProjectError("package.json must contain a JSON object")

    dependencies: dict = {}
    dependency_sections: dict = {}
    for key in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        if key not in data:
            continue
        section = data[key]
        if not isinstance(section, dict):
            raise UpgradeProjectError(f"package.json {key} must be an object")
        for name, spec in section.items():
            if not isinstance(name, str) or not name.strip():
                raise UpgradeProjectError(
                    f"package.json {key} keys must be non-empty strings"
                )
            if not isinstance(spec, str):
                raise UpgradeProjectError(
                    f"package.json {key}[{name!r}] must be a string"
                )
            kind, value = _classify_spec(spec, _NPM_EXACT_RE)
            if kind == "empty":
                continue
            if name in dependencies and dependencies[name] != value:
                raise UpgradeProjectError(
                    f"package.json has conflicting specs for dependency {name!r} "
                    f"in {dependency_sections[name]} and {key}"
                )
            dependencies[name] = value
            dependency_sections[name] = key
    return dependencies


def parse_runtime_policy_file(text: str) -> dict:
    """A runtime-policy fixture is simply `{"agent-name": "target-mode"}`."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UpgradeProjectError(f"invalid runtime-policy file: {exc}") from exc
    if not isinstance(data, dict):
        raise UpgradeProjectError("runtime-policy file must contain a JSON object")
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
_DEFAULT_MATRIX_PATH = Path(__file__).resolve().parents[1] / "references" / "compatibility-matrix.json"


def _resolve_within_root(root: str, relative_path: str) -> str:
    """Resolve *relative_path* under *root* and reject anything that escapes
    the project root (an absolute path outside it, a `..` traversal
    including through a missing parent, or a symlink resolving outside it).
    """
    root_path = Path(root).resolve(strict=True)
    candidate = Path(relative_path)
    if not candidate.is_absolute():
        candidate = root_path / candidate

    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(root_path)
    except ValueError:
        raise UpgradeProjectError(
            f"path {relative_path!r} escapes the project root"
        )
    return str(resolved_candidate)


def _read_project_json(root: str, relative_path: str) -> Any:
    full_path = _resolve_within_root(root, relative_path)
    with open(full_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_project_text(root: str, relative_path: str) -> str:
    full_path = _resolve_within_root(root, relative_path)
    with open(full_path, "r", encoding="utf-8") as handle:
        return handle.read()


def _project_relative_path(root: str, relative_path: str) -> str:
    """The repo-relative, plan-safe form of a confined project artifact path,
    used to record where each dependency pin was actually parsed from. The
    path is resolved/confined first (rejecting any escape), then expressed
    relative to *root* so a plan item cites e.g. `package.json`, never an
    absolute path.
    """
    full_path = _resolve_within_root(root, relative_path)
    rel = os.path.relpath(full_path, root)
    return _require_safe_path(rel, f"project artifact path {relative_path!r}")


def _merge_parsed_dependencies(
    project: dict, dependency_paths: dict, parsed: dict, source_path: str
) -> None:
    """Merge a parser's `{name: spec}` into the project's `dependencies` and
    record each name's parsed-from provenance. If a name was already parsed
    from a *different* artifact (e.g. it appears in both pyproject.toml and
    package.json), reject the conflict deterministically rather than silently
    picking a winner and mis-attributing the plan item.
    """
    current = project.get("dependencies")
    if current is None:
        merged = {}
    elif not isinstance(current, dict):
        raise UpgradeProjectError("project.dependencies must be an object")
    else:
        merged = dict(current)
    for name, spec in parsed.items():
        existing_source = dependency_paths.get(name)
        if existing_source is not None and existing_source != source_path:
            raise UpgradeProjectError(
                f"dependency {name!r} is declared in both {existing_source!r} "
                f"and {source_path!r}; resolve the conflict so its upgrade plan "
                f"item is attributed to exactly one artifact"
            )
        merged[name] = spec
        dependency_paths[name] = source_path
    project["dependencies"] = merged


def build_normalized_project(args, root: str) -> dict:
    project: dict = {}
    if args.project_file:
        loaded = _read_project_json(root, args.project_file)
        if not isinstance(loaded, dict):
            raise UpgradeProjectError("--project-file must contain a JSON object")
        project = loaded

    project = _normalize_project(project)

    # Seed provenance from any dependency_paths the project-file itself
    # declared, then let each parsed artifact record (and conflict-check) its
    # own pins on top.
    dependency_paths = _normalize_dependency_paths(project.get("dependency_paths"))

    if args.pyproject_path:
        source_path = _project_relative_path(root, args.pyproject_path)
        parsed = parse_pyproject_dependencies(_read_project_text(root, args.pyproject_path))
        _merge_parsed_dependencies(project, dependency_paths, parsed, source_path)

    if args.package_json_path:
        source_path = _project_relative_path(root, args.package_json_path)
        parsed = parse_package_json_dependencies(_read_project_text(root, args.package_json_path))
        _merge_parsed_dependencies(project, dependency_paths, parsed, source_path)

    if args.runtime_policy_path:
        text = _read_project_text(root, args.runtime_policy_path)
        parsed = parse_runtime_policy_file(text)
        current_policy = project.get("runtime_policy")
        if current_policy is None:
            merged = {}
        elif not isinstance(current_policy, dict):
            raise UpgradeProjectError("project.runtime_policy must be an object")
        else:
            merged = dict(current_policy)
        merged.update(parsed)
        project["runtime_policy"] = merged

    if dependency_paths:
        project["dependency_paths"] = dependency_paths

    return project


def _load_matrix_arg(matrix_path: Optional[str]) -> dict:
    path = matrix_path if matrix_path else str(_DEFAULT_MATRIX_PATH)
    return load_matrix(path)


def _load_source_results_arg(source_results_path: Optional[str]) -> Optional[dict]:
    if not source_results_path:
        return None
    with open(source_results_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise UpgradeProjectError("--source-results-path must contain a JSON object")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "threadlight-upgrade — PLAN-ONLY compatibility/preview-drift scanner. "
            "Never edits the project; there is no --apply. No network calls; "
            "official-source corroboration is fixture-driven via "
            "--source-results-path."
        )
    )
    parser.add_argument("--project-root", default=".", help="pilot repo root (default cwd)")
    parser.add_argument(
        "--matrix-path", default=None,
        help="override the shipped references/compatibility-matrix.json (absolute "
             "or relative to the current working directory)",
    )
    parser.add_argument(
        "--project-file", default=None,
        help="JSON normalized project object, relative to --project-root",
    )
    parser.add_argument(
        "--pyproject-path", default=None,
        help="read-only pyproject.toml to merge dependency pins from, relative to --project-root",
    )
    parser.add_argument(
        "--package-json-path", default=None,
        help="read-only package.json to merge dependency pins from, relative to --project-root",
    )
    parser.add_argument(
        "--runtime-policy-path", default=None,
        help="read-only runtime-policy JSON to merge into project.runtime_policy, "
             "relative to --project-root",
    )
    parser.add_argument(
        "--source-results-path", default=None,
        help="fixture-driven official source-check results (JSON); no network is "
             "ever performed by this tool",
    )
    parser.add_argument(
        "--today", default=None,
        help="YYYY-MM-DD override for deterministic runs (default: current UTC date)",
    )
    parser.add_argument("--manifest-path", default=DEFAULT_MANIFEST_PATH,
                         help="where to write the manifest, relative to --project-root")
    parser.add_argument("--emit", action="store_true", help="write the manifest to disk")
    parser.add_argument("--json", action="store_true", help="print manifest JSON to stdout")
    parser.add_argument(
        "--gate", action="store_true",
        help="exit 2 when any finding is should-fix or not-verified",
    )
    args = parser.parse_args(argv)

    try:
        root_path = Path(args.project_root).resolve(strict=True)
        if not root_path.is_dir():
            raise NotADirectoryError(f"{root_path} is not a directory")
        root = str(root_path)
    except OSError as exc:
        print(f"error: invalid project root {args.project_root}: {exc}")
        return 1

    try:
        project = build_normalized_project(args, root)
        matrix = _load_matrix_arg(args.matrix_path)
        source_results = _load_source_results_arg(args.source_results_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1

    today = args.today or datetime.now(timezone.utc).date().isoformat()

    try:
        manifest = scan_project(project, matrix, today, source_results=source_results)
    except (UpgradeProjectError, UpgradeMatrixError, ManifestValidationError) as exc:
        print(f"error: {exc}")
        return 1

    if args.emit:
        try:
            manifest_full_path = _resolve_within_root(root, args.manifest_path)
            write_upgrade_manifest(manifest_full_path, manifest)
        except (UpgradeProjectError, ManifestValidationError, OSError) as exc:
            print(f"error: could not write manifest: {exc}")
            return 1

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        should_fix = [f["id"] for f in manifest["findings"] if f["status"] == "should-fix"]
        not_verified = [f["id"] for f in manifest["findings"] if f["status"] == "not-verified"]
        print(f"status: {manifest['status']}")
        print(f"should-fix: {', '.join(should_fix) or 'none'}")
        print(f"not-verified: {', '.join(not_verified) or 'none'}")
        print(f"plan items: {len(manifest['plan'])}")

    if args.gate and any(
        f["status"] in ("should-fix", "not-verified") for f in manifest["findings"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
