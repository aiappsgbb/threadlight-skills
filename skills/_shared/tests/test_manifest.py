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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": None}, "missing required keys"),
        ({"findings": {}}, "findings"),
        ({"freshness": {"valid_for_hours": 1.5}}, "valid_for_hours"),
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


def test_atomic_write_json_preserves_valid_file_when_validation_fails(tmp_path):
    path = tmp_path / "nested" / "manifest.json"
    original = valid_envelope()
    atomic_write_json(path, original)
    original_bytes = path.read_bytes()

    with pytest.raises(ManifestValidationError):
        atomic_write_json(path, valid_envelope(status="passed"))

    assert path.read_bytes() == original_bytes
    assert json.loads(path.read_text()) == original


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
