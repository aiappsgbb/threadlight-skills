import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path


VALID_STATUSES = frozenset({"complete", "partial", "aborted"})
REQUIRED_KEYS = frozenset(
    {"schema", "tool_version", "generated_at", "freshness", "status", "findings"}
)

# RFC 3339 ``date-time`` shape with a MANDATORY timezone offset. The separator
# is ``T`` (or lowercase ``t`` per RFC 3339's case-insensitive note) — never a
# space or any other whitespace — and the offset is ``Z``/``z`` or an explicit
# ``±HH:MM``. The clock and offset digit ranges are constrained to RFC 3339's
# (hour 00-23, minute/second 00-59) so an out-of-range time such as ``24:00:00``
# is refused here rather than slipping through ``datetime.fromisoformat`` (which
# accepts hour 24 on some CPython versions). Calendar validity that the date
# digit classes still allow (e.g. month 13 or ``2026-02-30``) is rejected by the
# subsequent parse. Mirrors the ``format: date-time`` semantics the manifest
# schemas declare and the RFC-3339 backend jsonschema's ``FormatChecker`` uses,
# but stays stdlib-only.
_RFC3339_DATETIME_RE = re.compile(
    r"""
    ^
    \d{4}-\d{2}-\d{2}                        # full-date (YYYY-MM-DD)
    [Tt]                                     # date-time separator (T/t only)
    (?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d        # partial-time hh:mm:ss (00-23:00-59)
    (?:\.\d+)?                               # optional time-secfrac
    (?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)   # time-offset: Z/z or ±HH:MM (required)
    $
    """,
    re.VERBOSE,
)


class ManifestValidationError(ValueError):
    pass


def _is_draft7_integer(value):
    """Return True when *value* is a JSON-Schema Draft-07 integer.

    Draft-07 treats an integer as any number with a zero fractional part, so a
    float like ``1.0`` IS an integer (``1`` and ``1.0`` are the same JSON value)
    while ``1.5`` is not. A bool is never an integer, and non-finite floats
    (``nan``/``inf``) are excluded. Mirrors what jsonschema's Draft7Validator
    accepts for ``{"type": "integer"}``.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and value.is_integer()
    )


def _validate_iso8601_timestamp(value, field, *, nullable=False):
    """Validate an RFC 3339 ``date-time`` string with a mandatory timezone.

    Enforces the JSON-Schema ``format: date-time`` contract the manifest schemas
    declare: a full ``YYYY-MM-DDThh:mm:ss`` date-time whose offset is ``Z`` or an
    explicit ``±HH:MM``. This is intentionally stricter than
    :func:`datetime.fromisoformat`, which also accepts naive (timezone-less)
    datetimes, a space separator, and bare dates. Rejected: timezone-less
    datetimes, date-only values, a space (or any other whitespace) separator,
    surrounding whitespace, and impossible calendar/clock values (e.g. month 13
    or ``2026-02-30``). ``Z``/``z`` and lowercase ``t`` are accepted per RFC
    3339's case-insensitive note. Stdlib-only — no rfc3339 dependency.
    """
    expectation = (
        "None or an ISO-8601 timestamp"
        if nullable
        else "an ISO-8601 timestamp"
    )
    if not isinstance(value, str) or not _RFC3339_DATETIME_RE.match(value):
        raise ManifestValidationError(f"{field} must be {expectation}")

    # The regex fixes the full shape — separator, clock/offset ranges, and the
    # mandatory timezone. Parsing then rejects the only thing the date digit
    # classes still allow: an impossible calendar day (month 13, 2026-02-30).
    # Normalize a trailing Z/z to +00:00 so fromisoformat accepts it everywhere.
    normalized = value[:-1] + "+00:00" if value[-1] in "Zz" else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ManifestValidationError(
            f"{field} must be {expectation}"
        ) from error


def validate_envelope(envelope):
    missing = REQUIRED_KEYS.difference(envelope)
    if missing:
        raise ManifestValidationError(
            f"missing required keys: {', '.join(sorted(missing))}"
        )

    for field in ("schema", "tool_version", "generated_at"):
        value = envelope[field]
        if not isinstance(value, str) or not value:
            raise ManifestValidationError(
                f"{field} must be a non-empty string"
            )

    _validate_iso8601_timestamp(envelope["generated_at"], "generated_at")

    if (
        not isinstance(envelope["status"], str)
        or envelope["status"] not in VALID_STATUSES
    ):
        raise ManifestValidationError(f"unknown status: {envelope['status']!r}")

    if not isinstance(envelope["findings"], list):
        raise ManifestValidationError("findings must be a list")

    freshness = envelope["freshness"]
    if not isinstance(freshness, dict):
        raise ManifestValidationError("freshness must be an object")

    required_freshness_keys = {"valid_for_hours", "source_oldest_at"}
    missing_freshness_keys = required_freshness_keys.difference(freshness)
    if missing_freshness_keys:
        raise ManifestValidationError(
            "freshness missing required keys: "
            + ", ".join(sorted(missing_freshness_keys))
        )

    source_oldest_at = freshness["source_oldest_at"]
    if source_oldest_at is not None:
        if not isinstance(source_oldest_at, str) or not source_oldest_at:
            raise ManifestValidationError(
                "freshness.source_oldest_at must be None or an ISO-8601 timestamp"
            )
        _validate_iso8601_timestamp(
            source_oldest_at,
            "freshness.source_oldest_at",
            nullable=True,
        )

    valid_for_hours = freshness["valid_for_hours"]
    # Draft-07 integer semantics (see _is_draft7_integer): an integral float
    # such as 1.0 is a valid integer, a bool/1.5/nan/inf is not. The original
    # numeric value is preserved as-is (not normalized to int) — validation only
    # asserts it satisfies the schema's {"type": "integer", "minimum": 1}.
    if not _is_draft7_integer(valid_for_hours) or valid_for_hours <= 0:
        raise ManifestValidationError(
            "freshness.valid_for_hours must be a positive integer"
        )


def build_envelope(
    *,
    schema,
    tool_version,
    status,
    generated_at,
    valid_for_hours,
    source_oldest_at,
    findings,
    payload=None,
):
    envelope = {
        "schema": schema,
        "tool_version": tool_version,
        "generated_at": generated_at,
        "freshness": {
            "valid_for_hours": valid_for_hours,
            "source_oldest_at": source_oldest_at,
        },
        "status": status,
        "findings": findings,
    }
    if payload is not None:
        reserved_keys = REQUIRED_KEYS.intersection(payload)
        if reserved_keys:
            raise ManifestValidationError(
                "payload must not override reserved keys: "
                + ", ".join(sorted(reserved_keys))
            )
        envelope.update(payload)
    validate_envelope(envelope)
    return envelope


def atomic_write_json(path, envelope):
    validate_envelope(envelope)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                envelope,
                temporary,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
