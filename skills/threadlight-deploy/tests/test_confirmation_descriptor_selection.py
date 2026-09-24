"""Catch stale reviewer metadata before freezing a user-only confirmation host."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_user_confirmation_generation import confirmation_inputs
from test_governance_wiring import module
from test_governance_quality import snapshot

pytestmark = pytest.mark.governance_runtime


def descriptor(config):
    action = json.loads((Path(config["bundle_path"]) / "gateway-registry.json").read_text())[
        "actions"][0]
    return {
        "name": action["name"], "description": "Governed action: act",
        "inputSchema": deepcopy(action["input_schema"]),
        "_meta": {"threadlight.confirmation": "governance_request_context"},
    }


@pytest.mark.parametrize("fault", ["reviewer", "missing-confirmation", "extra-evidence", "wrong-tool"])
def test_signed_descriptor_selection_mismatch_is_rejected_before_generation(tmp_path, fault):
    project, document, config, _, _ = confirmation_inputs(tmp_path)
    selected = descriptor(config)
    if fault == "reviewer":
        selected["_meta"]["threadlight.approval_mode"] = "deferred"
    elif fault == "missing-confirmation":
        selected["_meta"] = {}
    elif fault == "extra-evidence":
        selected["_meta"]["threadlight.evidence"] = "threadlight/evidence"
    else:
        selected["name"] = "another_action"
    config["gateway_descriptors"] = {"act": selected}
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_gateway_descriptor_selection_mismatch"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before
