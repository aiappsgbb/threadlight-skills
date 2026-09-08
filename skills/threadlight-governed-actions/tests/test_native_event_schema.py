"""Schema-only LOCAL-14 shape checks; no native runtime or model execution."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from skills._shared.native_local_evidence import _FIELDS

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "skills/threadlight-governed-actions/references/governed-actions-manifest.schema.json"
DIGEST = "sha256:" + "a" * 64
IMPORTS = ("agent_framework", "agent_framework.foundry", "agent_framework_foundry_hosting",
           "agent_primitives", "agent_hooks", "agent_control_specification")
EVENTS = ("installed", "complete", *sorted(_FIELDS))


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA.read_text())
    fragment = {
        "$schema": schema["$schema"], "$defs": schema["$defs"],
        **schema["properties"]["native_local"]["properties"]["events"]["items"],
    }
    jsonschema.Draft202012Validator.check_schema(fragment)
    return jsonschema.Draft202012Validator(fragment, format_checker=jsonschema.FormatChecker())


def event(name):
    if name == "complete":
        return {"event": name, "counter": 1}
    if name == "installed":
        pins = json.loads((ROOT / "skills/_shared/governance-upstream-pin.json").read_text())
        versions = {pins[key]["distribution"]: pins[key]["version"] for key in ("agt", "acs", "agent_hooks")}
        versions.update(pins["maf"])
        return {
            "event": name, "counter": 1, "source_hashes": {"project:agent.yaml": "a" * 64},
            "observation": {
                "schema": "native-installed-observation/v1", "execution_mode": "local",
                "phase": "pre-deploy", "platform": "linux-amd64", "python_version": "3.12.14",
                "collected_at": "2026-09-08T08:00:00Z", "deployed_image": "not-verified",
                "opa_sha256": pins["opa"]["linux_amd64_static_sha256"],
                "imports": {name: "/fixture/" + name.replace(".", "/") + "/__init__.py" for name in IMPORTS},
                "packages": [{"distribution": name, "version": version, **pins["wheels"][name]}
                             for name, version in versions.items()],
            },
        }
    receipt = {
        "receipt_id": "receipt-1", "correlation_id": "call-1", "action_id": "returns_apply_decision",
        "action_hash": DIGEST, "policy_digest": DIGEST, "decision": "allow", "reason_code": "policy_allow",
        "agent_version": "local-fixture", "image_digest": "sha256:" + "0" * 64,
        "recorded_at": "2026-09-08T08:00:00Z",
    }
    values = {
        "expected_resolved_path": "native.host:Host._agent", "resolved_path": "native.host:Host._agent",
        "tool_id": "returns_list_open", "queries": 1, "acs_evaluations": 0, "effects": 0,
        "point": "pre_tool_call", "snapshot_hash": DIGEST, "result_hash": DIGEST,
        "native_decision": "allow", "read_names": ["returns_get_case"],
        "decision": "allow", "receipt_id": "receipt-1", "action_hash": DIGEST,
        "policy_hash": DIGEST, "receipt_hash": DIGEST, "receipt": receipt,
        "argument_hash": DIGEST, "original_argument_hash": DIGEST, "effect_count": 1,
        "served_sha256": "a" * 64, "request_sha256": DIGEST, "scope": "scripted local model",
        "output_hash": DIGEST, "reads": 1, "decisions": ["allow"], "approval_requests": 0,
        "binding_status": "unverified", "items": 2, "child_factory_hash": "a" * 64,
        "exception_class": "GovernedToolUnavailable", "intent_hash": DIGEST, "nonce_hash": DIGEST,
        "status": 200, "grant_hash": DIGEST, "approved": True, "requests": 2, "distinct_nonces": 2,
    }
    return {"event": name, "counter": 1, "action_id": "returns_apply_decision",
            "case": "allow", "mode": "interactive", **{key: values[key] for key in _FIELDS[name].split()}}


@pytest.mark.parametrize("name", EVENTS)
def test_native_event_schema_accepts_each_recorder_shape(validator, name):
    validator.validate(event(name))


@pytest.mark.parametrize("name", EVENTS)
def test_native_event_schema_rejects_unexpected_fields(validator, name):
    value = event(name)
    value["unexpected_payload"] = "not evidence"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(value)


@pytest.mark.parametrize("name", EVENTS)
def test_native_event_schema_requires_each_declared_field(validator, name):
    value = event(name)
    for key in value:
        missing = deepcopy(value)
        del missing[key]
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(missing)


@pytest.mark.parametrize(("name", "key", "value"), [
    ("complete", "action_id", "returns_apply_decision"),
    ("installed", "mode", "interactive"),
    ("terminal", "receipt_hash", DIGEST),
    ("approval_request", "receipt", {}),
])
def test_native_event_schema_rejects_fields_from_other_event_types(validator, name, key, value):
    value = {**event(name), key: value}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(value)


@pytest.mark.parametrize("location", ["observation", "package", "imports", "source_hashes", "receipt"])
def test_native_event_schema_closes_nested_records(validator, location):
    value = event("pre_action_decision" if location == "receipt" else "installed")
    target = {
        "observation": lambda: value["observation"],
        "package": lambda: value["observation"]["packages"][0],
        "imports": lambda: value["observation"]["imports"],
        "source_hashes": lambda: value["source_hashes"],
        "receipt": lambda: value["receipt"],
    }[location]()
    target["unexpected_payload"] = "not evidence"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(value)
