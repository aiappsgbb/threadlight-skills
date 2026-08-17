import json

import pytest

import skills._shared.manifest as manifest_module
from skills._shared.manifest import (
    ManifestValidationError,
    atomic_write_json,
    build_envelope,
    validate_envelope,
)


def valid_envelope(**overrides):
    envelope = {
        "schema": "threadlight.test/v1",
        "tool_version": "0.1.0",
        "generated_at": "2026-08-17T10:00:00Z",
        "freshness": {
            "valid_for_hours": 24,
            "source_oldest_at": "2026-08-17T09:00:00Z",
        },
        "status": "complete",
        "findings": [],
    }
    envelope.update(overrides)
    return envelope


def test_build_envelope_declares_contract_fields():
    envelope = build_envelope(
        schema="threadlight.test/v1",
        tool_version="0.1.0",
        status="partial",
        generated_at="2026-08-17T10:00:00Z",
        valid_for_hours=24,
        source_oldest_at="2026-08-17T09:00:00Z",
        findings=[{"id": "TST-001", "status": "not-verified"}],
    )

    assert envelope["schema"] == "threadlight.test/v1"
    assert envelope["status"] == "partial"
    assert envelope["freshness"] == {
        "valid_for_hours": 24,
        "source_oldest_at": "2026-08-17T09:00:00Z",
    }
    assert envelope["findings"] == [{"id": "TST-001", "status": "not-verified"}]


@pytest.mark.parametrize("status", ["complete", "partial", "aborted"])
def test_validate_envelope_accepts_declared_statuses(status):
    assert validate_envelope(valid_envelope(status=status)) is None


def test_validate_envelope_rejects_success_shaped_unknown_status():
    with pytest.raises(ManifestValidationError, match="status"):
        validate_envelope(valid_envelope(status="passed"))


@pytest.mark.parametrize("status", [[], {}])
def test_validate_envelope_rejects_non_string_status(status):
    with pytest.raises(ManifestValidationError, match="status"):
        validate_envelope(valid_envelope(status=status))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": None}, "missing required keys"),
        ({"findings": {}}, "findings"),
        (
            {
                "freshness": {
                    "valid_for_hours": 1.5,
                    "source_oldest_at": None,
                }
            },
            "valid_for_hours",
        ),
    ],
)
def test_validate_envelope_rejects_invalid_contract_fields(change, message):
    envelope = valid_envelope(**change)
    if change == {"schema": None}:
        del envelope["schema"]

    with pytest.raises(ManifestValidationError, match=message):
        validate_envelope(envelope)


def test_build_envelope_merges_payload():
    envelope = build_envelope(
        schema="threadlight.test/v1",
        tool_version="0.1.0",
        status="complete",
        generated_at="2026-08-17T10:00:00Z",
        valid_for_hours=24,
        source_oldest_at="2026-08-17T09:00:00Z",
        findings=[],
        payload={"summary": {"checked": 3}},
    )

    assert envelope["summary"] == {"checked": 3}


def test_build_envelope_rejects_payload_reserved_keys_without_overriding_status():
    with pytest.raises(
        ManifestValidationError,
        match=r"payload must not override reserved keys: schema, status",
    ):
        build_envelope(
            schema="threadlight.test/v1",
            tool_version="0.1.0",
            status="aborted",
            generated_at="2026-08-17T10:00:00Z",
            valid_for_hours=24,
            source_oldest_at=None,
            findings=[],
            payload={"schema": "other/v1", "status": "complete"},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", ""),
        ("schema", None),
        ("tool_version", ""),
        ("tool_version", 1),
        ("generated_at", ""),
        ("generated_at", None),
    ],
)
def test_validate_envelope_rejects_invalid_required_scalar_types(field, value):
    with pytest.raises(
        ManifestValidationError, match=rf"{field} must be a non-empty string"
    ):
        validate_envelope(valid_envelope(**{field: value}))


@pytest.mark.parametrize(
    "generated_at",
    [
        "not-a-timestamp",
        "2026-08-17",
        "2026-13-40T10:00:00Z",
        # Timezone-less date-time: rejected — RFC 3339 requires an offset.
        "2026-08-17T10:00:00",
        # Invalid clock/calendar values that are still well-shaped.
        "2026-08-17T24:00:00Z",
        "2026-02-30T10:00:00Z",
        # Whitespace is never part of an RFC 3339 date-time: a space separator,
        # and leading/trailing whitespace, are all rejected.
        "2026-08-17 10:00:00Z",
        " 2026-08-17T10:00:00Z",
        "2026-08-17T10:00:00Z\n",
    ],
)
def test_validate_envelope_rejects_invalid_generated_at(generated_at):
    with pytest.raises(
        ManifestValidationError,
        match="generated_at must be an ISO-8601 timestamp",
    ):
        validate_envelope(valid_envelope(generated_at=generated_at))


@pytest.mark.parametrize(
    "generated_at",
    [
        "2026-08-17T10:00:00Z",
        "2026-08-17T10:00:00+00:00",
        "2026-08-17T10:00:00-05:00",
        "2026-08-17t10:00:00z",
        "2026-08-17T10:00:00.500Z",
        "2026-08-17T10:00:00+23:59",
    ],
)
def test_validate_envelope_accepts_timezone_aware_generated_at(generated_at):
    assert validate_envelope(valid_envelope(generated_at=generated_at)) is None


@pytest.mark.parametrize(
    "source_oldest_at",
    [
        "",
        "not-a-timestamp",
        "2026-08-17",
        42,
        # Timezone-less, invalid clock, and whitespace/space-separated values are
        # rejected exactly like generated_at (a non-null source_oldest_at is a
        # full RFC 3339 date-time).
        "2026-08-17T10:00:00",
        "2026-08-17T24:00:00Z",
        "2026-08-17 10:00:00Z",
        " 2026-08-17T10:00:00Z",
    ],
)
def test_validate_envelope_rejects_invalid_source_oldest_at(source_oldest_at):
    envelope = valid_envelope()
    envelope["freshness"]["source_oldest_at"] = source_oldest_at

    with pytest.raises(
        ManifestValidationError,
        match=(
            "freshness.source_oldest_at must be None or an ISO-8601 timestamp"
        ),
    ):
        validate_envelope(envelope)


@pytest.mark.parametrize(
    "source_oldest_at",
    [
        "2026-08-17T09:00:00Z",
        "2026-08-17T09:00:00+00:00",
        "2026-08-17T09:00:00-05:00",
        "2026-08-17t09:00:00z",
    ],
)
def test_validate_envelope_accepts_timezone_aware_source_oldest_at(source_oldest_at):
    envelope = valid_envelope()
    envelope["freshness"]["source_oldest_at"] = source_oldest_at

    assert validate_envelope(envelope) is None


def test_validate_envelope_accepts_none_source_oldest_at():
    envelope = valid_envelope()
    envelope["freshness"]["source_oldest_at"] = None

    assert validate_envelope(envelope) is None


@pytest.mark.parametrize("missing_key", ["valid_for_hours", "source_oldest_at"])
def test_validate_envelope_requires_complete_freshness_metadata(missing_key):
    envelope = valid_envelope()
    del envelope["freshness"][missing_key]

    with pytest.raises(
        ManifestValidationError,
        match=rf"freshness missing required keys: {missing_key}",
    ):
        validate_envelope(envelope)


@pytest.mark.parametrize("valid_for_hours", [0, -1, True, 1.5, "24"])
def test_validate_envelope_rejects_non_positive_integer_validity(valid_for_hours):
    envelope = valid_envelope()
    envelope["freshness"]["valid_for_hours"] = valid_for_hours

    with pytest.raises(
        ManifestValidationError,
        match="freshness.valid_for_hours must be a positive integer",
    ):
        validate_envelope(envelope)


@pytest.mark.parametrize("valid_for_hours", [1.0, 24.0, 720.0])
def test_validate_envelope_accepts_integral_float_validity(valid_for_hours):
    # Draft-07 integer semantics: a zero-fraction float (1.0) is the same JSON
    # value as the integer 1, so it satisfies {"type": "integer", "minimum": 1}.
    envelope = valid_envelope()
    envelope["freshness"]["valid_for_hours"] = valid_for_hours

    assert validate_envelope(envelope) is None
    # The original numeric value is preserved as-is (not normalized to int).
    assert envelope["freshness"]["valid_for_hours"] == valid_for_hours


@pytest.mark.parametrize(
    "valid_for_hours",
    [1.5, 0.5, -1.0, 0.0, float("nan"), float("inf"), float("-inf"), True, False],
)
def test_validate_envelope_rejects_non_integral_or_nonfinite_validity(valid_for_hours):
    # A non-integral float (1.5), a non-finite float (nan/inf), a bool, and any
    # non-positive integral value are all rejected — matching Draft-07's integer
    # type plus the schema's minimum of 1.
    envelope = valid_envelope()
    envelope["freshness"]["valid_for_hours"] = valid_for_hours

    with pytest.raises(
        ManifestValidationError,
        match="freshness.valid_for_hours must be a positive integer",
    ):
        validate_envelope(envelope)


def test_atomic_write_json_preserves_valid_file_when_validation_fails(tmp_path):
    path = tmp_path / "nested" / "manifest.json"
    original = valid_envelope()
    atomic_write_json(path, original)
    original_bytes = path.read_bytes()

    with pytest.raises(ManifestValidationError):
        atomic_write_json(path, valid_envelope(status="passed"))

    assert path.read_bytes() == original_bytes
    assert json.loads(path.read_text()) == original


def test_atomic_write_json_preserves_valid_file_when_payload_contains_nan(
    tmp_path,
):
    path = tmp_path / "nested" / "manifest.json"
    original = valid_envelope()
    atomic_write_json(path, original)
    original_bytes = path.read_bytes()
    invalid = valid_envelope(findings=[{"value": float("nan")}])

    with pytest.raises(ValueError):
        atomic_write_json(path, invalid)

    assert path.read_bytes() == original_bytes
    assert json.loads(path.read_text()) == original
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_atomic_write_json_cleans_temp_file_when_replacement_is_interrupted(
    tmp_path, monkeypatch
):
    path = tmp_path / "nested" / "manifest.json"
    original = valid_envelope()
    atomic_write_json(path, original)
    original_bytes = path.read_bytes()

    def interrupt_replace(source, destination):
        raise KeyboardInterrupt

    monkeypatch.setattr(manifest_module.os, "replace", interrupt_replace)

    with pytest.raises(KeyboardInterrupt):
        atomic_write_json(path, valid_envelope(status="partial"))

    assert path.read_bytes() == original_bytes
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []
