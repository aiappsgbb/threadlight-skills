"""
Publish the cost-actuals/reconciliation pair and its human report durably.

See `docs/superpowers/specs/2026-08-18-cost-actuals-reconciliation-design.md`
§7.3 (canonical pair, immutable history, commit marker) and §7.4 (the four
separate headline numbers), and
`skills/threadlight-consumption-iq/references/cost-reconciliation-manifest-schema.md`
for the documents this module writes. Every document handed here is produced
offline by `cost_actuals.build_actuals_manifest` and `reconcile.reconcile_costs`;
this module performs no network access and no process execution, only local
file I/O.

## Validate everything, then write; never the other way round

`emit_reconciliation` renders both JSON payloads and the whole Markdown report
in memory and validates every document, digest and destination path BEFORE it
creates a single directory or temp file. A rejected emission therefore leaves
the destination tree byte-for-byte as it was — a half-written artifact is
worse than no artifact, because a consumer cannot tell it apart from a
complete one.

## The canonical reconciliation is the commit marker

Publish order is: immutable history first, then canonical actuals, then the
report, then the canonical reconciliation LAST. Each file is written to a
named temp file in its own destination directory, flushed, `os.fsync`ed,
`chmod`ed to `ARTIFACT_MODE` and closed before any publish runs, and each
destination directory is `os.fsync`ed after its publish so the rename itself
survives a crash. Every directory this call has to CREATE is fsynced before
the first publish, so no directory-durability failure can happen after a
canonical file is already visible.

## Durability is bounded by the platform, and says so

On POSIX, both halves hold: the bytes are fsynced, and the directory entry
that names them is fsynced too. On Windows there is no file descriptor for a
directory, so `_fsync_directory` is a documented no-op — attempting the
`os.open` would raise `PermissionError` mid-publish, which is precisely the
half-published outcome this module exists to prevent. The file fsync and the
atomic `os.replace` are retained there in full; only directory-ENTRY
durability is weaker, meaning a crash immediately after a rename can lose the
rename itself. `canonical_pair_is_complete` still reads that outcome
correctly, because a lost rename is indistinguishable from a rename that
never happened.

## Destination identity is normalized conservatively

Two destinations that differ only in case, or that reach the same file
through a symlinked ancestor, are ONE file on a case-insensitive operator
filesystem (macOS, Windows) — and publishing both would silently overwrite
one artifact with another. Every report/actuals/reconciliation/history path
is therefore reduced to `os.path.realpath(os.path.abspath(path)).casefold()`
and checked for collisions BEFORE any directory is created or byte written.
The casefold is deliberately conservative: it refuses a case-only alias even
on a case-sensitive filesystem where it would technically be safe, because a
tree published on Linux is routinely reviewed on macOS.

This module deliberately does **not** claim cross-file atomicity — POSIX
offers none for a multi-file publish. It claims something weaker and
checkable instead: a partially published set can never *look* complete.
`reconciliation.actuals_ref.sha256` pins the exact actuals document, so an
interrupted publish leaves either the previous consistent pair (when the
actuals replace failed) or a newer actuals next to an older reconciliation
whose hash no longer matches (when the report or reconciliation replace
failed). `canonical_pair_is_complete` reports the second case as `False`,
which is exactly the fail-closed reading `threadlight-production-ready`
needs before it consumes either file.

## History is immutable evidence

Each snapshot lives at
`<history_root>/<YYYY-MM-DD>--<YYYY-MM-DD>/<reconciliation generated_at
compact Z>/`. Both path components are re-formatted from PARSED `datetime`
values, never interpolated from raw document strings, so no traversal-shaped
input can escape `history_root` — it fails window validation long before it
reaches a path. Re-emitting an identical payload is a no-op (idempotent), a
different payload for the same window and instant raises
`HistoryConflictError` rather than overwriting evidence, and a snapshot
interrupted after one of its two files can be completed only when the file
already on disk still matches.

The key is the RECONCILIATION instant, not the collection instant, because
they answer different questions. `actuals.generated_at` (`collected_at` in
the report) is when Cost Management was read; `reconciliation.generated_at`
(`reconciled_at`) is when that evidence was re-projected against a forecast
and a policy. A pricing refresh or a SPEC edit re-reconciles evidence
collected days ago — offline, with no Azure call — and each of those verdicts
is its own auditable snapshot. Keying on the collection instant would instead
make every re-reconciliation of unchanged evidence collide with the first
one. One collected actuals document therefore appears verbatim in several
snapshots; `reconciliation.actuals_ref.sha256` is what binds each verdict to
the exact evidence bytes underneath it, so nothing is ambiguous about which
source a snapshot re-projected.

A history entry is put in place with `os.link`, never `os.replace`. That is
the whole difference between "immutable" and "immutable unless two publishers
race": the existence check necessarily happens before the payload is staged,
and `os.replace` would happily overwrite an entry a concurrent publisher
created inside that window. `os.link` fails with `FileExistsError` instead,
and the loser then re-reads the entry that actually won — identical payload
is idempotent, a different one is a `HistoryConflictError`. `os.link` exists
on Windows; a filesystem that cannot provide hard links surfaces as a legible
`OSError` rather than as a false conflict.

## Credential-shaped free-form evidence is refused

`provenance` and `warnings` are the only free-form, operator-populated bags in
either document; every other field is a typed value produced by the cores.
Because history is immutable, a token accidentally recorded in one of them
could never be redacted afterwards, so a provenance key whose full normalized
spelling names a credential (`token`, `access_token`, `refresh_token`,
`bearer_token`, `authorization`, `secret`, `client_secret`, `password`,
`api_key`, ...) or a credential-shaped `key: value`/`Bearer <token>` fragment
in a warning or provenance value fails the emission outright. The key check
is an EXACT match on the normalized spelling, not a substring one: legitimate
provenance keys that merely mention tokens — `token_doc`,
`token_source_resource_id`, `model_token_count`, `token_metrics` — are
evidence, not secrets, and publish unaffected, as do resource, subscription,
tenant and correlation IDs.

## The report never converts attribution evidence into spend

Observed spend is the Cost Management total and nothing else. The report
renders Azure Monitor token rows as volume diagnostics under their own
heading, never as money, and the run-rate and unit-cost headlines are
authoritative only when their own evidence gate passed.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from reconcile import ReconciliationInputError, sha256_json


ACTUALS_SCHEMA = "threadlight-cost-actuals/v1"
RECONCILIATION_SCHEMA = "threadlight-cost-reconciliation/v1"

HISTORY_ACTUALS_NAME = "actuals.json"
HISTORY_RECONCILIATION_NAME = "reconciliation.json"

# Fixed mode for every published artifact. `tempfile` stages at 0600, and an
# artifact that inherited that mode would be unreadable to the CI job, the
# reviewer and every downstream tool that consumes this evidence — while
# still being world-readable is harmless, because nothing here may contain a
# secret (see the credential guard below). Set explicitly rather than left to
# the process umask so the published tree is reproducible.
ARTIFACT_MODE = 0o644

PASS = "pass"
NOT_VERIFIED = "not-verified"
SHOULD_FIX = "should-fix"

# One spelling for "this number was not measured". Never `0`, never `$0.00`:
# a fabricated zero reads as a measured absence of cost, which is the single
# most misleading thing this report could say.
NOT_MEASURED = "not measured"

_STATUS_VALUES = frozenset({PASS, NOT_VERIFIED})
_VERDICT_VALUES = frozenset({PASS, NOT_VERIFIED, SHOULD_FIX})

_ISO_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)
_ISO_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Compact, filesystem-safe spelling of the same instant. Derived by
# re-formatting the parsed `datetime`, so it can only ever be digits, `T` and
# `Z` — never a path separator or a `..` segment from the source document.
_SNAPSHOT_FORMAT = "%Y-%m-%dT%H%M%SZ"

_SHA256_HEX_RE = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)

# Top-level keys each document must carry. A document missing one of these is
# not a v1 document, and publishing it would put a shape no consumer can read
# into immutable history.
_ACTUALS_REQUIRED_KEYS = (
    "schema",
    "generated_at",
    "status",
    "scope",
    "window",
    "cost",
    "usage",
    "provenance",
    "warnings",
)
_RECONCILIATION_REQUIRED_KEYS = (
    "schema",
    "generated_at",
    "status",
    "variance_status",
    "forecast_ref",
    "actuals_ref",
    "policy_ref",
    "policy_snapshot",
    "policy_errors",
    "maturity",
    "totals",
    "unit_economics",
    "coverage",
    "drivers",
    "warnings",
)

# Exact markers for mapping KEYS inside `provenance`, matched against the
# casefolded key with `_`, `-` and spaces normalized away, so `access_token`,
# `Access-Token` and `accesstoken` are all caught by the same entry.
#
# This is an EXACT match, deliberately not a substring one: both the
# reconciliation and actuals cores legitimately emit provenance keys that
# *contain* the substring "token" without naming a credential at all —
# `token_doc`, `token_source_resource_id`, `model_token_count` and
# `token_metrics` describe token-volume evidence, not a bearer credential.
# Flagging any key that merely mentions tokens would refuse that evidence
# outright, so only a key whose FULL normalized spelling names a credential
# trips this guard.
_CREDENTIAL_KEY_MARKERS = frozenset(
    {
        "token",
        "accesstoken",
        "refreshtoken",
        "bearertoken",
        "idtoken",
        "authorization",
        "secret",
        "clientsecret",
        "password",
        "passwd",
        "credential",
        "apikey",
    }
)
# Credential-shaped fragments inside free-text VALUES (warnings, provenance
# strings). A bare word is not enough — `max_token_volume_variance_pct` is a
# legitimate threshold name and reconcile's own warnings discuss token volume
# — so a marker only trips the guard when it is immediately followed by an
# assignment, or when it introduces a bearer credential.
#
# The bare `token`, `sig` and `accountkey` alternatives carry a
# `(?<![A-Za-z0-9])` lookbehind rather than `\b`, so `_token=` and `&sig=`
# are caught while `design=` (which contains "sig") is not, and they require
# the assignment to follow IMMEDIATELY: `token: <value>` is a leaked bearer
# credential, `token volume` and `input_tokens: 1200` are evidence. `sig=`
# and `AccountKey=` are the two Azure spellings that carry a secret in a URL
# or a connection string, and neither has a legitimate prose form.
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?:access[ _-]?token|refresh[ _-]?token|id[ _-]?token|client[ _-]?secret"
    r"|secret|password|passwd|authorization|api[ _-]?key)\s*[:=]\s*\S"
    r"|(?<![A-Za-z0-9])token\s*[:=]\s*\S"
    r"|(?<![A-Za-z0-9])sig\s*=\s*\S"
    r"|(?<![A-Za-z0-9])account[ _-]?key\s*=\s*\S"
    r"|bearer\s+[A-Za-z0-9._~+/-]{8,}",
    re.IGNORECASE | re.ASCII,
)

# Markdown metacharacters that can break a table cell, inject raw HTML, or
# turn quoted evidence into formatting. `_` is deliberately NOT escaped:
# CommonMark does not emphasize intraword underscores, and every field name in
# these documents is snake_case, so escaping it would make the report unreadable
# without making it any safer.
_MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*\[\]<>#|~])")
# Longest run of backticks in a value decides how long its code fence must be.
_BACKTICK_RUN_RE = re.compile(r"`+")


class HistoryConflictError(RuntimeError):
    """An immutable history entry already exists with a different payload."""


class EmissionValidationError(ValueError):
    """A document, digest or destination path is not publishable as-is."""


# ---------------------------------------------------------------------------
# Narrow field validation
# ---------------------------------------------------------------------------


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EmissionValidationError(
            f"{label} must be a mapping, got {type(value).__name__}"
        )
    return value


def _require_keys(document: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    for key in keys:
        if key not in document:
            raise EmissionValidationError(f"{label} is missing required key {key!r}")


def _require_literal(
    document: dict[str, Any], key: str, expected: str, label: str
) -> None:
    value = document.get(key)
    if value != expected:
        raise EmissionValidationError(
            f"{label} {key} must be {expected!r}, got {value!r}"
        )


def _require_enum(
    document: dict[str, Any], key: str, allowed: frozenset[str], label: str
) -> str:
    value = document.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise EmissionValidationError(
            f"{label} {key} must be one of "
            f"{', '.join(sorted(allowed))}, got {value!r}"
        )
    return value


def _require_str_list(document: dict[str, Any], key: str, label: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, list) or any(
        not isinstance(entry, str) for entry in value
    ):
        raise EmissionValidationError(f"{label} {key} must be a list of str")
    return value


def _require_instant(value: object, label: str) -> datetime:
    """Parse one canonical `YYYY-MM-DDTHH:MM:SSZ` UTC instant.

    A `+00:00` offset denotes the same moment but a different string, and
    these values are hashed and compared byte-for-byte across artifacts, so
    exactly one spelling is publishable.
    """
    if not isinstance(value, str) or _ISO_UTC_RE.fullmatch(value) is None:
        raise EmissionValidationError(
            f"{label} must be a canonical UTC instant (YYYY-MM-DDTHH:MM:SSZ), "
            f"got {value!r}"
        )
    try:
        parsed = datetime.strptime(value, _ISO_UTC_FORMAT)
    except ValueError as exc:
        raise EmissionValidationError(
            f"{label} is not a real calendar instant: {value!r}"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _require_digest(reference: object, key: str, label: str) -> str:
    """A reference digest is 64 hex characters and nothing else.

    A published artifact whose provenance cannot be re-derived from the bytes
    it names is not auditable, and history keeps it forever. This is the
    strict rule for `forecast_ref.sha256` and `actuals_ref.sha256`, both of
    which are computed by this module or by the cores from bytes actually
    published alongside them — an unusable value there is a caller bug, not
    degraded evidence, so it must never publish. `policy_ref.spec_sha256` is
    validated separately by `_require_policy_ref`, which does NOT enforce
    this shape (see that function for why).
    """
    mapping = _require_mapping(reference, label)
    value = mapping.get(key)
    if not isinstance(value, str) or _SHA256_HEX_RE.fullmatch(value) is None:
        raise EmissionValidationError(
            f"{label} {key} must be a 64-character SHA-256 hex digest, "
            f"got {value!r}"
        )
    return value


def _require_policy_ref(reference: object, label: str) -> None:
    """`policy_ref` is validated structurally, never for anchor strength.

    `reconcile.reconcile_costs` deliberately EMITS an unusable
    `spec_sha256` — a placeholder, a truncated hash, an empty string, or
    literally `"TBD"` — instead of refusing to produce a manifest, and
    degrades every threshold-gated verdict (`maturity.status`,
    `unit_economics.*`, `variance_status`, `drivers.payg_ptu.status`) to
    `not-verified` so a consumer sees exactly that the anchor could not be
    re-derived. Refusing to *publish* that degraded-but-genuine evidence
    would suppress the one artifact a consumer needs to see the warning and
    the still-good observed numbers, so this validates only the STRUCTURE
    `reconcile_costs` always emits — `path`, `section` and a string-typed
    `spec_sha256` — and never the 64-hex shape `_require_digest` enforces
    for `forecast_ref`/`actuals_ref`.
    """
    mapping = _require_mapping(reference, label)
    _require_keys(mapping, ("path", "section", "spec_sha256"), label)
    path = mapping.get("path")
    if not isinstance(path, str) or not path:
        raise EmissionValidationError(
            f"{label} path must be a non-empty string, got {path!r}"
        )
    section = mapping.get("section")
    if not isinstance(section, int) or isinstance(section, bool):
        raise EmissionValidationError(
            f"{label} section must be an int, got {section!r}"
        )
    spec_sha256 = mapping.get("spec_sha256")
    if not isinstance(spec_sha256, str):
        raise EmissionValidationError(
            f"{label} spec_sha256 must be a string (an unusable anchor "
            "degrades every gated verdict but is still published verbatim), "
            f"got {spec_sha256!r}"
        )


def _canonical_json_text(document: dict[str, Any], label: str) -> str:
    """Serialize one document deterministically, or refuse to.

    `allow_nan=False` is the point: `NaN`/`Infinity` are not JSON, and Python
    emits them happily by default, so a non-finite number would otherwise
    reach a file no strict parser can read back.
    """
    try:
        payload = json.dumps(
            document,
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EmissionValidationError(
            f"{label} is not strictly JSON-serializable: {exc}"
        ) from exc
    return payload + "\n"


def _canonical_digest(document: dict[str, Any], label: str) -> str:
    try:
        return sha256_json(document)
    except ReconciliationInputError as exc:
        raise EmissionValidationError(f"{label} cannot be hashed: {exc}") from exc


# ---------------------------------------------------------------------------
# Credential guard
# ---------------------------------------------------------------------------


def _normalized_key(key: str) -> str:
    return key.casefold().replace("_", "").replace("-", "").replace(" ", "")


def _scan_for_credentials(value: object, label: str) -> None:
    """Refuse free-form evidence that looks like a credential.

    History is immutable: a secret written here can never be redacted, only
    rotated. The guard is therefore deliberately conservative about KEYS and
    deliberately narrow about VALUES, so that legitimate token-volume prose
    (which the reconciliation core emits) still publishes.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _normalized_key(key) in _CREDENTIAL_KEY_MARKERS:
                raise EmissionValidationError(
                    f"{label} carries credential-shaped key {key!r}; "
                    "immutable history must not record secrets"
                )
            _scan_for_credentials(item, f"{label}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_for_credentials(item, f"{label}[{index}]")
        return
    if isinstance(value, str) and _CREDENTIAL_VALUE_RE.search(value):
        raise EmissionValidationError(
            f"{label} carries a credential-shaped value; immutable history "
            "must not record secrets"
        )


# ---------------------------------------------------------------------------
# Document validation
# ---------------------------------------------------------------------------


def _validate_actuals(actuals: object) -> tuple[datetime, datetime, datetime]:
    document = _require_mapping(actuals, "actuals")
    _require_keys(document, _ACTUALS_REQUIRED_KEYS, "actuals")
    _require_literal(document, "schema", ACTUALS_SCHEMA, "actuals")
    _require_enum(document, "status", _STATUS_VALUES, "actuals")
    generated_at = _require_instant(
        document.get("generated_at"), "actuals generated_at"
    )

    window = _require_mapping(document.get("window"), "actuals window")
    start = _require_instant(window.get("start"), "actuals window start")
    end = _require_instant(window.get("end"), "actuals window end")
    if start >= end:
        raise EmissionValidationError(
            "actuals window start must precede window end "
            f"(start={window.get('start')!r}, end={window.get('end')!r})"
        )

    _require_mapping(document.get("scope"), "actuals scope")
    _require_mapping(document.get("cost"), "actuals cost")
    _require_mapping(document.get("usage"), "actuals usage")
    _require_mapping(document.get("provenance"), "actuals provenance")
    _require_str_list(document, "warnings", "actuals")

    _scan_for_credentials(document["provenance"], "actuals provenance")
    _scan_for_credentials(document["warnings"], "actuals warnings")
    return start, end, generated_at


def _validate_reconciliation(
    reconciliation: object, actuals: dict[str, Any], collected_at: datetime
) -> datetime:
    """Validate the reconciliation half and return the instant it names.

    `actuals.generated_at` is when the bill was COLLECTED;
    `reconciliation.generated_at` is when that evidence was RE-PROJECTED
    against a forecast and a policy. They are two different events and the
    second can never precede the first, but it very often follows it: a
    pricing refresh or a SPEC edit re-reconciles evidence collected days ago
    without touching Azure at all. Equality is the ordinary fast path — the
    first reconciliation of fresh evidence — and is accepted for exactly
    that reason, not because the two values are the same fact.
    """
    document = _require_mapping(reconciliation, "reconciliation")
    _require_keys(document, _RECONCILIATION_REQUIRED_KEYS, "reconciliation")
    _require_literal(document, "schema", RECONCILIATION_SCHEMA, "reconciliation")
    _require_enum(document, "status", _STATUS_VALUES, "reconciliation")
    _require_enum(document, "variance_status", _VERDICT_VALUES, "reconciliation")

    reconciled_at = _require_instant(
        document.get("generated_at"), "reconciliation generated_at"
    )
    if reconciled_at < collected_at:
        raise EmissionValidationError(
            "reconciliation generated_at must not precede the actuals "
            "generated_at it re-projects (actuals collected_at="
            f"{actuals['generated_at']!r}, reconciliation reconciled_at="
            f"{document['generated_at']!r})"
        )

    maturity = _require_mapping(document.get("maturity"), "reconciliation maturity")
    _require_enum(maturity, "status", _STATUS_VALUES, "reconciliation maturity")
    if not isinstance(maturity.get("checks"), list):
        raise EmissionValidationError("reconciliation maturity checks must be a list")

    unit_economics = _require_mapping(
        document.get("unit_economics"), "reconciliation unit_economics"
    )
    _require_enum(
        unit_economics, "status", _STATUS_VALUES, "reconciliation unit_economics"
    )
    _require_enum(
        unit_economics,
        "target_status",
        _VERDICT_VALUES,
        "reconciliation unit_economics",
    )

    _require_mapping(document.get("totals"), "reconciliation totals")
    _require_mapping(document.get("coverage"), "reconciliation coverage")
    _require_mapping(document.get("drivers"), "reconciliation drivers")
    _require_mapping(document.get("policy_snapshot"), "reconciliation policy_snapshot")
    _require_str_list(document, "policy_errors", "reconciliation")
    _require_str_list(document, "warnings", "reconciliation")

    _require_digest(
        document.get("forecast_ref"), "sha256", "reconciliation forecast_ref"
    )
    _require_policy_ref(document.get("policy_ref"), "reconciliation policy_ref")
    recorded = _require_digest(
        document.get("actuals_ref"), "sha256", "reconciliation actuals_ref"
    )
    expected = _canonical_digest(actuals, "actuals")
    if recorded.casefold() != expected:
        raise EmissionValidationError(
            "reconciliation actuals_ref sha256 does not match the actuals "
            f"document it is published with (recorded={recorded}, "
            f"actual={expected})"
        )

    _scan_for_credentials(document["warnings"], "reconciliation warnings")
    return reconciled_at


# ---------------------------------------------------------------------------
# Destination paths
# ---------------------------------------------------------------------------


def _as_path(value: object, label: str) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    raise EmissionValidationError(
        f"{label} must be a path, got {type(value).__name__}"
    )


def _canonical_identity(path: Path) -> str:
    """One conservative spelling of "which file is this?".

    `abspath` removes `.`/`..` and the process CWD, `realpath` resolves every
    symlinked ancestor (a symlink two levels up is not caught by
    `_reject_symlink`, which only inspects the path and its parent), and
    `casefold` collapses spellings that differ only in case — which are the
    SAME file on macOS and Windows.

    Deliberately conservative in both directions it can be: it resolves more
    than strictly necessary on a case-sensitive filesystem, because refusing
    an emission is recoverable and silently overwriting one artifact with
    another is not.
    """
    return os.path.realpath(os.path.abspath(path)).casefold()


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise EmissionValidationError(
            f"{label} {path} is a symlink; publishing through a symlink would "
            "write outside the declared destination"
        )


def _validate_destinations(destinations: list[tuple[str, Path]]) -> None:
    seen: dict[str, str] = {}
    for label, path in destinations:
        _reject_symlink(path, label)
        _reject_symlink(path.parent, f"{label} parent directory")
        key = _canonical_identity(path)
        if key in seen:
            raise EmissionValidationError(
                f"{label} and {seen[key]} must be distinct paths, both resolve "
                f"to the same file as {path}"
            )
        seen[key] = label


# ---------------------------------------------------------------------------
# Durable writes
# ---------------------------------------------------------------------------


def _stage(destination: Path, text: str, created: list[Path]) -> Path:
    """Write `text` to a named temp file in the destination's own directory.

    Same directory, so the later `os.replace`/`os.link` is a rename or link
    within one filesystem and cannot fail with a cross-device error after the
    caller was told the payload was durable. The temp path is recorded before
    the write begins, so cleanup can find it even if the write itself fails.

    The mode is set on the STAGED file, not after publishing: `os.replace`
    carries the inode's mode with it and `os.link` shares the inode outright,
    so the artifact is readable from the instant it becomes visible.
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp = Path(handle.name)
    created.append(temp)
    with handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    # Path form, not the file-descriptor form: `os.chmod` accepts an fd only
    # where `os.fchmod` exists, so passing one would raise `TypeError` on
    # Windows. By path it is portable — Windows simply maps the write bits
    # onto its read-only flag.
    os.chmod(temp, ARTIFACT_MODE)
    return temp


def _fsync_directory(path: Path) -> None:
    """Persist the rename itself, not just the bytes it points at.

    Windows has no file descriptor for a directory: `os.open` on one raises
    `PermissionError`. The guard is therefore in front of the `os.open`, not
    wrapped around it — a mid-publish exception here would abort AFTER some
    artifacts were already renamed into place, which is exactly the
    half-published state this module exists to prevent. The file fsync and
    the atomic replace are unaffected on Windows; only directory-entry
    durability is weaker there (see the module docstring).
    """
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir_tracked(directory: Path, created: list[Path]) -> None:
    """Create `directory` and record every level this call actually created.

    Only the newly created levels need their own durability treatment; a
    directory that already existed was already durable.
    """
    missing: list[Path] = []
    probe = directory
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    directory.mkdir(parents=True, exist_ok=True)
    for path in reversed(missing):
        if path not in created:
            created.append(path)


def _fsync_created_directories(created: list[Path]) -> None:
    """Persist every directory this call created, before anything publishes.

    A directory entry that was never fsynced can vanish in a crash, taking
    the artifact inside it with it. This runs BEFORE the first publish on
    purpose: a directory fsync that failed afterwards would leave a canonical
    file already visible, and this module's whole contract is that a partial
    publish can never look complete.
    """
    ordered: list[Path] = []
    for directory in created:
        for candidate in (directory.parent, directory):
            if candidate not in ordered:
                ordered.append(candidate)
    for directory in ordered:
        _fsync_directory(directory)


def _publish(temp: Path, destination: Path) -> None:
    os.replace(temp, destination)
    _fsync_directory(destination.parent)


def _publish_history(
    temp: Path, destination: Path, document: dict[str, Any], digest: str
) -> None:
    """Create an immutable history entry, or prove the one there is identical.

    `os.link` is the point: it CREATES, and fails when the name is already
    taken. `os.replace` would overwrite an entry that a concurrent publisher
    created after this call's existence check — a window that is small but
    entirely real, and the only way to lose collected evidence here.
    """
    try:
        os.link(temp, destination)
    except FileExistsError:
        if _history_entry_state(destination, document, digest):
            return
        raise HistoryConflictError(
            f"history entry {destination} was created and then removed by "
            "another publisher while this snapshot was being written; "
            "re-run the emission rather than racing for the same entry"
        ) from None
    except OSError as exc:
        raise OSError(
            f"could not create history entry {destination} as a hard link "
            f"from {temp}: {exc}"
        ) from exc
    _fsync_directory(destination.parent)


def _cleanup(created: list[Path]) -> None:
    """Remove only the temp files THIS call created.

    A temp file that was already renamed into place is gone, and every other
    file in these directories belongs to somebody else — a concurrent
    publisher's staged temp file included.

    Every `OSError` is suppressed. This runs in a `finally`, so an exception
    raised here would REPLACE the failure that actually aborted the publish,
    and a leftover dotfile is a far smaller problem than an operator being
    told the wrong reason their emission failed.
    """
    for temp in created:
        try:
            os.unlink(temp)
        except OSError:
            continue


def _history_entry_state(path: Path, document: dict[str, Any], digest: str) -> bool:
    """Return True when this immutable entry already holds this exact payload.

    Raises `HistoryConflictError` when an entry exists that this call cannot
    prove identical — including one whose bytes do not parse. Overwriting it
    would destroy evidence; silently accepting it would claim a snapshot that
    was never verified.
    """
    if not path.exists():
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HistoryConflictError(
            f"history entry {path} exists but does not parse as JSON, so it "
            "cannot be proven identical to the payload being published"
        ) from exc
    if not isinstance(existing, dict) or _canonical_digest(
        existing, f"history entry {path}"
    ) != digest:
        raise HistoryConflictError(
            f"history entry {path} already exists with a different payload; "
            "collected evidence is immutable, so publish a new snapshot "
            "instead of overwriting this one"
        )
    return True


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def emit_reconciliation(
    *,
    actuals: dict[str, object],
    reconciliation: dict[str, object],
    report_path: Path,
    actuals_path: Path,
    reconciliation_path: Path,
    history_root: Path,
) -> None:
    """Validate both documents, write history, then publish the latest pair.

    Nothing is written until every document, digest and path has validated,
    and nothing is published until every payload has been staged and fsynced.
    The canonical reconciliation is replaced LAST, as the commit marker for
    the whole set (see the module docstring).

    Raises `EmissionValidationError` for a document, digest or path that must
    not be published, `HistoryConflictError` when an immutable history entry
    already holds a different payload, and `OSError` for a genuine I/O
    failure. Neither input document is mutated.
    """
    start, end, collected_at = _validate_actuals(actuals)
    reconciled_at = _validate_reconciliation(reconciliation, actuals, collected_at)

    actuals_text = _canonical_json_text(actuals, "actuals")
    reconciliation_text = _canonical_json_text(reconciliation, "reconciliation")
    report_text = render_reconciliation_report(actuals, reconciliation)

    report_path = _as_path(report_path, "report_path")
    actuals_path = _as_path(actuals_path, "actuals_path")
    reconciliation_path = _as_path(reconciliation_path, "reconciliation_path")
    history_root = _as_path(history_root, "history_root")

    window_dir = history_root / (
        f"{start.date().isoformat()}--{end.date().isoformat()}"
    )
    # Keyed by when the RECONCILIATION was computed, not by when the evidence
    # was collected: one collection is legitimately re-projected many times,
    # and each of those verdicts needs its own immutable snapshot.
    snapshot_dir = window_dir / reconciled_at.strftime(_SNAPSHOT_FORMAT)
    history_actuals = snapshot_dir / HISTORY_ACTUALS_NAME
    history_reconciliation = snapshot_dir / HISTORY_RECONCILIATION_NAME

    for label, directory in (
        ("history_root", history_root),
        ("history window directory", window_dir),
        ("history snapshot directory", snapshot_dir),
    ):
        _reject_symlink(directory, label)
    _validate_destinations(
        [
            ("actuals_path", actuals_path),
            ("report_path", report_path),
            ("reconciliation_path", reconciliation_path),
            ("history actuals", history_actuals),
            ("history reconciliation", history_reconciliation),
        ]
    )

    # Both history entries are checked before either is written, so a
    # conflicting snapshot aborts with nothing written at all. The check is
    # necessarily racy on its own — `_publish_history` closes that window by
    # CREATING each entry rather than replacing it.
    actuals_digest = _canonical_digest(actuals, "actuals")
    reconciliation_digest = _canonical_digest(reconciliation, "reconciliation")
    pending: list[tuple[Path, str, dict[str, Any], str]] = []
    if not _history_entry_state(history_actuals, actuals, actuals_digest):
        pending.append((history_actuals, actuals_text, actuals, actuals_digest))
    if not _history_entry_state(
        history_reconciliation, reconciliation, reconciliation_digest
    ):
        pending.append(
            (
                history_reconciliation,
                reconciliation_text,
                reconciliation,
                reconciliation_digest,
            )
        )

    created: list[Path] = []
    try:
        directories: list[Path] = []
        _mkdir_tracked(snapshot_dir, directories)
        for destination in (actuals_path, report_path, reconciliation_path):
            _mkdir_tracked(destination.parent, directories)
        _fsync_created_directories(directories)

        history_staged = [
            (_stage(destination, text, created), destination, document, digest)
            for destination, text, document, digest in pending
        ]
        canonical_staged = [
            (_stage(destination, text, created), destination)
            for destination, text in (
                (actuals_path, actuals_text),
                (report_path, report_text),
                (reconciliation_path, reconciliation_text),
            )
        ]
        for temp, destination, document, digest in history_staged:
            _publish_history(temp, destination, document, digest)
        for temp, destination in canonical_staged:
            _publish(temp, destination)
    finally:
        _cleanup(created)


def canonical_pair_is_complete(
    actuals_path: Path, reconciliation_path: Path
) -> bool:
    """True only when the reconciliation on disk commits that exact actuals.

    This is the consumer-side gate for the multi-file publish: it re-derives
    the canonical hash of the actuals document and compares it with the
    reconciliation's own `actuals_ref.sha256`, and requires the
    reconciliation's `generated_at` (when the verdict was computed) not to
    precede the actuals' `generated_at` (when the bill was collected).

    The hash — not the timestamp — is what binds the pair. Two documents may
    legitimately name different instants, because the same collected evidence
    is re-reconciled whenever the forecast or the SPEC changes; requiring
    equality here would reject every one of those pairs. The ordering check
    is kept because a verdict that claims to predate its own evidence is
    incoherent however its hash reads.

    It is the ONE function in this module that answers with `False` instead
    of raising. A missing file, unreadable bytes, a foreign schema, a scalar
    JSON document and a stale hash are all "this pair is not a completed
    publish" — the exact question the caller asked — and a consumer that must
    decide whether to trust an artifact should not have to catch five
    exception types to learn that it must not.
    """
    try:
        actuals = _load_document(actuals_path)
        reconciliation = _load_document(reconciliation_path)
        if actuals is None or reconciliation is None:
            return False
        if actuals.get("schema") != ACTUALS_SCHEMA:
            return False
        if reconciliation.get("schema") != RECONCILIATION_SCHEMA:
            return False
        reference = reconciliation.get("actuals_ref")
        if not isinstance(reference, dict):
            return False
        recorded = reference.get("sha256")
        if not isinstance(recorded, str) or _SHA256_HEX_RE.fullmatch(recorded) is None:
            return False
        if recorded.casefold() != sha256_json(actuals):
            return False
        collected_at = _parse_instant(actuals.get("generated_at"))
        reconciled_at = _parse_instant(reconciliation.get("generated_at"))
        if collected_at is None or reconciled_at is None:
            return False
        return reconciled_at >= collected_at
    except (OSError, ValueError, TypeError, ReconciliationInputError):
        return False


def _parse_instant(value: object) -> Optional[datetime]:
    """Parse a canonical instant, or `None` — never raise. Reader-side twin
    of `_require_instant`, used by the one function here that answers with
    `False` instead of raising."""
    try:
        return _require_instant(value, "generated_at")
    except EmissionValidationError:
        return None


def _load_document(path: object) -> Optional[dict[str, Any]]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return document if isinstance(document, dict) else None


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _mapping(value: object) -> dict[str, Any]:
    """Read-only view of a nested block: `{}` when it is not a mapping.

    The top-level blocks are validated before rendering; nested content
    (a maturity check entry, a model row) is producer-extensible, so the
    renderer degrades to "nothing to show" rather than raising over an
    unexpected shape in a document that already validated.
    """
    return value if isinstance(value, dict) else {}


def _sequence(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _escape(value: object) -> str:
    """Escape one untrusted string for inline Markdown/table use."""
    text = value if isinstance(value, str) else repr(value)
    collapsed = text.replace("\r", " ").replace("\n", " ")
    return _MARKDOWN_ESCAPE_RE.sub(r"\\\1", collapsed)


def _code(value: object) -> str:
    """Render a value as a code span that cannot be closed from the inside.

    A backslash is LITERAL inside a code span, so a backtick in the content
    cannot be escaped — it has to be fenced by a longer run of backticks
    (CommonMark §6.1). Escaping it instead, as this used to, emitted a
    visible `\\` and let the span close early, so the rest of the table row
    was re-parsed as Markdown.

    `|` is the one character that still needs escaping here: GFM resolves the
    table-cell `\\|` escape BEFORE inline parsing, so an unescaped pipe splits
    the row even inside code. Everything else is inert inside a code span.
    """
    text = value if isinstance(value, str) else repr(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    longest = max(
        (len(run.group()) for run in _BACKTICK_RUN_RE.finditer(text)), default=0
    )
    fence = "`" * (longest + 1)
    # CommonMark strips one leading and one trailing space back off, so this
    # padding separates the fence from the content without changing it.
    if not text.strip(" ") or text.startswith("`") or text.endswith("`"):
        text = f" {text} "
    return f"{fence}{text}{fence}"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _money(value: object, *, places: int = 2) -> str:
    """Format a USD ledger amount, or say plainly that it was not measured."""
    if value is None:
        return NOT_MEASURED
    if not _is_number(value):
        return _escape(value)
    amount = float(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.{places}f}"


def _rate(value: object) -> str:
    """Cost-per-interaction keeps four decimal places: it is a rate, and at
    cent precision a sub-cent unit cost would round to `$0.00`."""
    return _money(value, places=4)


def _percent(value: object) -> str:
    if value is None:
        return NOT_MEASURED
    if not _is_number(value):
        return _escape(value)
    return f"{float(value) * 100:.2f}%"


def _count(value: object) -> str:
    if value is None:
        return NOT_MEASURED
    if not _is_number(value):
        return _escape(value)
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.2f}"


def _status(value: object) -> str:
    return _code(value) if isinstance(value, str) else NOT_MEASURED


def _compact(value: object) -> str:
    """Render a structured `actual`/`required` payload for a table cell."""
    if value is None:
        return NOT_MEASURED
    if isinstance(value, str):
        return _escape(value)
    try:
        return _escape(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError):
        return _escape(repr(value))


def render_reconciliation_report(
    actuals: dict[str, object], reconciliation: dict[str, object]
) -> str:
    """Render `docs/cost-reconciliation.md` from the two documents.

    Public and independently testable: the report is the half of this
    artifact a human actually reads, and its gating rules (RFC §7.4) deserve
    tests that do not have to touch the filesystem. Neither input is mutated.
    """
    actuals = _require_mapping(actuals, "actuals")
    reconciliation = _require_mapping(reconciliation, "reconciliation")
    _require_literal(actuals, "schema", ACTUALS_SCHEMA, "actuals")
    _require_literal(reconciliation, "schema", RECONCILIATION_SCHEMA, "reconciliation")

    blocks = [
        _render_header(actuals, reconciliation),
        _render_headlines(actuals, reconciliation),
        _render_variance(reconciliation),
        _render_maturity(reconciliation),
        _render_unit_economics(reconciliation),
        _render_attribution(reconciliation),
        _render_coverage(reconciliation),
        _render_usage(actuals),
        _render_driver(reconciliation),
        _render_policy(reconciliation),
        _render_warnings(actuals, reconciliation),
        _render_provenance(actuals, reconciliation),
    ]
    return "\n\n".join(block.strip("\n") for block in blocks) + "\n"


def _render_header(
    actuals: dict[str, Any], reconciliation: dict[str, Any]
) -> str:
    window = _mapping(actuals.get("window"))
    cost = _mapping(actuals.get("cost"))
    return "\n".join(
        [
            "# Cost reconciliation",
            "",
            f"> Observed window {_code(window.get('start'))} to "
            f"{_code(window.get('end'))} "
            f"({_count(window.get('complete_days'))} complete UTC days), "
            f"collected_at {_code(actuals.get('generated_at'))}, "
            f"reconciled_at {_code(reconciliation.get('generated_at'))}.",
            "> Source: Azure Cost Management `Usage` query, accounting metric "
            f"{_code(cost.get('basis'))}, cost column "
            f"{_code(cost.get('cost_column'))}, currency "
            f"{_code(cost.get('currency'))}.",
            f"> Evidence collection: {_status(actuals.get('status'))}. "
            f"Overall reconciliation status: "
            f"{_status(reconciliation.get('status'))}. Cost variance: "
            f"{_status(reconciliation.get('variance_status'))}.",
            "",
            "Observed spend below is what Azure Cost Management reported for "
            "the window. Model token figures are volume evidence explaining "
            "where that spend went; they are never added to a money total.",
        ]
    )


def _render_headlines(
    actuals: dict[str, Any], reconciliation: dict[str, Any]
) -> str:
    totals = _mapping(reconciliation.get("totals"))
    maturity = _mapping(reconciliation.get("maturity"))
    unit_economics = _mapping(reconciliation.get("unit_economics"))
    window = _mapping(actuals.get("window"))
    days = _count(window.get("complete_days"))

    lines = ["## Headline numbers", "", "### Projected monthly Azure cost", ""]
    lines += [
        f"**{_money(totals.get('forecast_monthly_usd'))}** per month, from the "
        "projection in `specs/cost-manifest.json`.",
        "",
        "### Observed Azure spend",
        "",
        f"**{_money(totals.get('actual_window_usd'))}** over the observed "
        f"window of {days} complete UTC days.",
        "",
        "### Observed monthly run-rate",
        "",
    ]

    run_rate = totals.get("actual_monthly_run_rate_usd")
    if maturity.get("status") == PASS:
        lines += [
            f"**{_money(run_rate)}** per month, extrapolated from the observed "
            "window at 30 days per month. Every maturity check below passed, "
            "so this is the run-rate to quote."
        ]
    else:
        lines += [
            f"**{_code(NOT_VERIFIED)}** — the maturity checks below did not "
            "all pass, so no monthly run-rate may be quoted from this window."
        ]
        if run_rate is not None:
            lines += [
                "",
                f"Observed extrapolation, for diagnosis only: "
                f"{_money(run_rate)} per month.",
            ]

    lines += ["", "### Cost per successful interaction", ""]
    cost_per_interaction = unit_economics.get(
        "cost_per_successful_interaction_usd"
    )
    if unit_economics.get("status") == PASS:
        lines += [
            f"**{_rate(cost_per_interaction)}** per successful interaction, "
            f"over {_count(unit_economics.get('successful_interactions'))} "
            "successful interactions."
        ]
    else:
        lines += [
            f"**{_code(NOT_VERIFIED)}** — the unit-economics evidence gate did "
            "not pass, so this workload has no verified cost per successful "
            "interaction for this window."
        ]
        if cost_per_interaction is not None:
            lines += [
                "",
                "Observed division, for diagnosis only: "
                f"{_rate(cost_per_interaction)} per successful interaction.",
            ]
    return "\n".join(lines)


def _render_variance(reconciliation: dict[str, Any]) -> str:
    totals = _mapping(reconciliation.get("totals"))
    snapshot = _mapping(reconciliation.get("policy_snapshot"))
    return "\n".join(
        [
            "## Cost variance against the projection",
            "",
            "| Measure | Value |",
            "| --- | --- |",
            f"| Projected spend for this window | "
            f"{_money(totals.get('forecast_window_usd'))} |",
            f"| Observed spend for this window | "
            f"{_money(totals.get('actual_window_usd'))} |",
            f"| Variance | {_money(totals.get('variance_window_usd'))} |",
            f"| Variance share of the projection | "
            f"{_percent(totals.get('variance_pct'))} |",
            f"| Declared tolerance (`max_forecast_variance_pct`) | "
            f"{_percent(snapshot.get('max_forecast_variance_pct'))} |",
            f"| Verdict (`variance_status`) | "
            f"{_status(reconciliation.get('variance_status'))} |",
        ]
    )


def _render_maturity(reconciliation: dict[str, Any]) -> str:
    maturity = _mapping(reconciliation.get("maturity"))
    lines = [
        "## Maturity checks",
        "",
        f"Overall: {_status(maturity.get('status'))}. Every check must pass "
        "before the run-rate and unit-cost headlines above may be quoted.",
        "",
        "| Check | Status | Observed | Required | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in _sequence(maturity.get("checks")):
        check = _mapping(entry)
        lines.append(
            f"| {_code(check.get('id'))} | {_status(check.get('status'))} | "
            f"{_compact(check.get('actual'))} | "
            f"{_compact(check.get('required'))} | "
            f"{_escape(check.get('detail', ''))} |"
        )
    return "\n".join(lines)


def _render_unit_economics(reconciliation: dict[str, Any]) -> str:
    unit_economics = _mapping(reconciliation.get("unit_economics"))
    return "\n".join(
        [
            "## Unit economics",
            "",
            "| Measure | Value |",
            "| --- | --- |",
            f"| Evidence gate (`status`) | "
            f"{_status(unit_economics.get('status'))} |",
            f"| Successful interactions observed | "
            f"{_count(unit_economics.get('successful_interactions'))} |",
            f"| Cost per successful interaction | "
            f"{_rate(unit_economics.get('cost_per_successful_interaction_usd'))} |",
            f"| Declared target | {_rate(unit_economics.get('target_usd'))} |",
            f"| Target comparison (`target_status`) | "
            f"{_status(unit_economics.get('target_status'))} |",
        ]
    )


def _render_attribution(reconciliation: dict[str, Any]) -> str:
    coverage = _mapping(reconciliation.get("coverage"))
    lines = [
        "## Resource attribution",
        "",
        "### Observed spend matched to a projected resource",
        "",
    ]
    matched = _sequence(coverage.get("matched_resources"))
    if matched:
        lines += [
            "| Observed resource | Type | Projected (window) | Observed "
            "(window) | Match |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in matched:
            resource = _mapping(entry)
            lines.append(
                f"| {_escape(resource.get('actual_resource_id'))} | "
                f"{_escape(resource.get('resource_type'))} | "
                f"{_money(resource.get('forecast_window_usd'))} | "
                f"{_money(resource.get('actual_window_usd'))} | "
                f"{_code(resource.get('match_method'))} |"
            )
    else:
        lines.append("No observed resource matched a projected resource.")

    lines += [
        "",
        "### Observed spend on resources the projection never modeled",
        "",
        f"Total: {_money(coverage.get('unmodeled_actual_usd'))}.",
        "",
    ]
    unmodeled = _sequence(coverage.get("unmodeled_resources"))
    if unmodeled:
        lines += ["| Resource | Type | Observed (window) |", "| --- | --- | --- |"]
        for entry in unmodeled:
            resource = _mapping(entry)
            lines.append(
                f"| {_escape(resource.get('resource_id'))} | "
                f"{_escape(resource.get('resource_type'))} | "
                f"{_money(resource.get('period_cost_usd'))} |"
            )
    else:
        lines.append("Every observed resource was modeled by the projection.")

    lines += [
        "",
        "### Projected resources with no observed spend",
        "",
        f"Total: {_money(coverage.get('forecast_not_observed_usd'))} of "
        "projected window cost.",
        "",
    ]
    not_observed = _sequence(coverage.get("forecast_not_observed_resources"))
    if not_observed:
        lines += [
            "| Projected resource | Type | Projected (window) |",
            "| --- | --- | --- |",
        ]
        for entry in not_observed:
            resource = _mapping(entry)
            identifiers = ", ".join(
                _escape(value)
                for value in _sequence(resource.get("forecast_resource_ids"))
            )
            lines.append(
                f"| {identifiers} | {_escape(resource.get('resource_type'))} | "
                f"{_money(resource.get('forecast_window_usd'))} |"
            )
    else:
        lines.append("Every projected resource was observed.")
    return "\n".join(lines)


def _render_coverage(reconciliation: dict[str, Any]) -> str:
    coverage = _mapping(reconciliation.get("coverage"))
    snapshot = _mapping(reconciliation.get("policy_snapshot"))
    return "\n".join(
        [
            "## Coverage",
            "",
            "Two different measures. They are never merged into one number.",
            "",
            "| Measure | Value | What it means |",
            "| --- | --- | --- |",
            "| `projection_attribution_coverage_pct` | "
            f"{_percent(coverage.get('projection_attribution_coverage_pct'))} "
            "| Share of observed cost that could be mapped to a projected "
            "resource. This is the gated measure. |",
            "| `source_resource_id_coverage_pct` | "
            f"{_percent(coverage.get('source_resource_id_coverage_pct'))} | "
            "Share of observed cost arriving on rows carrying a resource ID "
            "at all. Source quality only, never gated. |",
            "| `min_projection_attribution_coverage_pct` | "
            f"{_percent(snapshot.get('min_projection_attribution_coverage_pct'))}"
            " | The declared minimum the first measure is judged against. |",
        ]
    )


def _render_usage(actuals: dict[str, Any]) -> str:
    usage = _mapping(actuals.get("usage"))
    lines = [
        "## Interaction and model evidence",
        "",
        "| Measure | Status | Value |",
        "| --- | --- | --- |",
        f"| Total interactions | "
        f"{_status(usage.get('interaction_status'))} | "
        f"{_count(usage.get('total_interactions'))} |",
        f"| Successful interactions | "
        f"{_status(usage.get('interaction_status'))} | "
        f"{_count(usage.get('successful_interactions'))} |",
        f"| Per-model token attribution | "
        f"{_status(usage.get('model_attribution_status'))} | see below |",
        "",
    ]
    models = _sequence(usage.get("models"))
    if models:
        lines += [
            "| Model | Deployment | Input tokens | Output tokens |",
            "| --- | --- | --- | --- |",
        ]
        for entry in models:
            row = _mapping(entry)
            lines.append(
                f"| {_escape(row.get('model'))} | "
                f"{_escape(row.get('deployment'))} | "
                f"{_count(row.get('input_tokens'))} | "
                f"{_count(row.get('output_tokens'))} |"
            )
        lines += [
            "",
            "Token volumes explain where spend went. They are not spend.",
        ]
    else:
        lines.append("No per-model token rows were collected for this window.")
    return "\n".join(lines)


def _render_driver(reconciliation: dict[str, Any]) -> str:
    driver = _mapping(_mapping(reconciliation.get("drivers")).get("payg_ptu"))
    return "\n".join(
        [
            "## PAYG/PTU driver",
            "",
            "Token volume only. This block never carries a dollar figure.",
            "",
            "| Measure | Value |",
            "| --- | --- |",
            f"| Verdict | {_status(driver.get('status'))} |",
            f"| Projected monthly tokens | "
            f"{_count(driver.get('forecast_monthly_tokens'))} |",
            f"| Observed monthly tokens | "
            f"{_count(driver.get('observed_monthly_tokens'))} |",
            f"| Volume variance | "
            f"{_percent(driver.get('observed_volume_variance_pct'))} |",
            f"| Declared tolerance ({_code(driver.get('threshold_field'))}) | "
            f"{_percent(driver.get('threshold_pct'))} |",
            "",
            _escape(driver.get("detail", "")),
        ]
    )


def _render_policy(reconciliation: dict[str, Any]) -> str:
    reference = _mapping(reconciliation.get("policy_ref"))
    snapshot = _mapping(reconciliation.get("policy_snapshot"))
    errors = _sequence(reconciliation.get("policy_errors"))
    lines = [
        "## Declared policy",
        "",
        f"Read from {_code(reference.get('path'))} section "
        f"{_compact(reference.get('section'))}, SPEC digest "
        f"{_code(reference.get('spec_sha256'))}.",
        "",
        "| Declared value | Setting |",
        "| --- | --- |",
    ]
    for key in sorted(snapshot):
        value = snapshot[key]
        rendered = NOT_MEASURED if value is None else _compact(value)
        lines.append(f"| {_code(key)} | {rendered} |")
    lines += ["", "### Policy errors", ""]
    if errors:
        lines.append(
            "Reported verbatim by the SPEC parser. Every threshold-gated "
            "verdict above is `not-verified` while these stand."
        )
        lines.append("")
        lines += [f"- {_escape(error)}" for error in errors]
    else:
        lines.append("None. The declared policy parsed cleanly.")
    return "\n".join(lines)


def _render_warnings(
    actuals: dict[str, Any], reconciliation: dict[str, Any]
) -> str:
    lines = ["## Warnings", ""]
    for label, entries in (
        ("Reconciliation", _sequence(reconciliation.get("warnings"))),
        ("Evidence collection", _sequence(actuals.get("warnings"))),
    ):
        lines += [f"### {label}", ""]
        if entries:
            lines += [f"- {_escape(entry)}" for entry in entries]
        else:
            lines.append("None.")
        lines.append("")
    return "\n".join(lines)


def _render_provenance(
    actuals: dict[str, Any], reconciliation: dict[str, Any]
) -> str:
    forecast_ref = _mapping(reconciliation.get("forecast_ref"))
    actuals_ref = _mapping(reconciliation.get("actuals_ref"))
    policy_ref = _mapping(reconciliation.get("policy_ref"))
    lines = [
        "## Provenance",
        "",
        "Every input is pinned by digest. Re-derive any of them from the "
        "named bytes to re-verify this report.",
        "",
        "| Input | Path | Digest |",
        "| --- | --- | --- |",
        f"| Projection | {_code(forecast_ref.get('path'))} | "
        f"{_code(forecast_ref.get('sha256'))} |",
        f"| Observed evidence | {_code(actuals_ref.get('path'))} | "
        f"{_code(actuals_ref.get('sha256'))} |",
        f"| Declared policy | {_code(policy_ref.get('path'))} | "
        f"{_code(policy_ref.get('spec_sha256'))} |",
        "",
        "### Collection scope",
        "",
    ]
    lines += _key_value_rows(_mapping(actuals.get("scope")))
    lines += ["", "### Collection provenance", ""]
    lines += _key_value_rows(_mapping(actuals.get("provenance")))
    lines += [
        "",
        # Two distinct events, named distinctly. Both documents spell this
        # field `generated_at`; what differs is what each one generated.
        f"`collected_at` {_code(actuals.get('generated_at'))} — when Cost "
        "Management was read.",
        f"`reconciled_at` {_code(reconciliation.get('generated_at'))} — when "
        "that evidence was re-projected against the forecast and policy "
        "above. The same evidence is reconciled again whenever either "
        "changes, so this is at or after `collected_at`, never before it.",
    ]
    return "\n".join(lines)


def _key_value_rows(mapping: dict[str, Any]) -> list[str]:
    if not mapping:
        return ["None recorded."]
    rows = ["| Key | Value |", "| --- | --- |"]
    for key in sorted(mapping, key=str):
        rows.append(f"| {_code(key)} | {_compact(mapping[key])} |")
    return rows
