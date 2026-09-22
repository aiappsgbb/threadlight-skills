"""Opt-in selection must survive real source packaging and signed registry validation."""
import json

import pytest

from test_maf_gateway_generation import gateway_inputs, package, stage
from test_governance_quality import snapshot
from test_governance_wiring import module
from test_evidence_attestations import fixture
from test_gateway import gateway

pytestmark = pytest.mark.governance_runtime


def selected(registry):
    gateway("dispatcher")
    requirement = fixture()[2].model_dump()
    action = registry["actions"][0]
    action["evidence_requirement"] = requirement
    action["input_schema"]["properties"].update(
        case_id={"type": "string", "maxLength": 128},
        expected_etag={"type": "string", "maxLength": 128})
    action["input_schema"]["required"] += ["case_id", "expected_etag"]


def test_generator_preserves_signed_evidence_and_portable_verifier(tmp_path):
    data = gateway_inputs(tmp_path, requires=["signed-evidence"], registry_change=selected)
    project, document, config, deployment, _ = data
    generator = package(data)
    stage(generator, data)
    generator.bind(project, document, configuration=deployment)
    registry = json.loads((project / "src/govern-gateway/policy/gateway-registry.json").read_text())
    assert registry["actions"][0]["evidence_requirement"]["profile"] == "purchase-v1"
    assert (project / "src/agent/vendor/control-plane/attestations.py").is_file()
    assert (project / "src/govern-gateway/vendor/control-plane/attestations.py").is_file()


@pytest.mark.parametrize("declared", [True, False])
def test_one_sided_evidence_selection_fails_before_generation(tmp_path, declared):
    data = gateway_inputs(
        tmp_path, requires=["signed-evidence"] if declared else [],
        registry_change=None if declared else selected)
    project, document, config, _, _ = data
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_registry_evidence_requirement_mismatch"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_null_or_invalid_requirement_is_not_off():
    from test_gateway import registry
    from pydantic import ValidationError
    for value in (None, {}, {"profile": "purchase-v1"}):
        action = registry()["actions"][0]
        action["evidence_requirement"] = value
        with pytest.raises(ValidationError):
            gateway("dispatcher").Action.model_validate(action)
