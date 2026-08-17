import json
import os
import tempfile
from pathlib import Path


VALID_STATUSES = frozenset({"complete", "partial", "aborted"})
REQUIRED_KEYS = frozenset(
    {"schema", "tool_version", "generated_at", "freshness", "status", "findings"}
)


class ManifestValidationError(ValueError):
    pass


def validate_envelope(envelope):
    missing = REQUIRED_KEYS.difference(envelope)
    if missing:
        raise ManifestValidationError(
            f"missing required keys: {', '.join(sorted(missing))}"
        )

    if envelope["status"] not in VALID_STATUSES:
        raise ManifestValidationError(f"unknown status: {envelope['status']!r}")

    if not isinstance(envelope["findings"], list):
        raise ManifestValidationError("findings must be a list")

    freshness = envelope["freshness"]
    valid_for_hours = (
        freshness.get("valid_for_hours") if isinstance(freshness, dict) else None
    )
    if isinstance(valid_for_hours, bool) or not isinstance(valid_for_hours, int):
        raise ManifestValidationError(
            "freshness.valid_for_hours must be an integer"
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
            json.dump(envelope, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
