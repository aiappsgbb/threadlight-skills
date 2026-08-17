import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


VALID_STATUSES = frozenset({"complete", "partial", "aborted"})
REQUIRED_KEYS = frozenset(
    {"schema", "tool_version", "generated_at", "freshness", "status", "findings"}
)


class ManifestValidationError(ValueError):
    pass


def _validate_iso8601_timestamp(value, field, *, nullable=False):
    expectation = (
        "None or an ISO-8601 timestamp"
        if nullable
        else "an ISO-8601 timestamp"
    )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ManifestValidationError(f"{field} must be {expectation}") from error

    if not any(separator in value for separator in ("T", "t", " ")):
        raise ManifestValidationError(f"{field} must be {expectation}")


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
    if (
        isinstance(valid_for_hours, bool)
        or not isinstance(valid_for_hours, int)
        or valid_for_hours <= 0
    ):
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
