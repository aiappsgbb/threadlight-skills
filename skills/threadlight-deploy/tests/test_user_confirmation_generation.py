"""The signed requesting-user requirement must survive real native source generation."""
from copy import deepcopy
import asyncio
import json

import pytest

from test_maf_gateway_generation import gateway_inputs, package, stage
from test_governance_quality import snapshot
from test_governance_wiring import module
from test_gateway import gateway

pytestmark = pytest.mark.governance_runtime


@pytest.fixture(autouse=True)
def current_gateway_schema():
    gateway("dispatcher")


def selected(registry):
    registry["actions"][0].update(approval_roles=[], confirmation_requirement={
        "trigger": "policy", "provider_profile": "email", "max_age_seconds": 300,
    })


def confirmation_inputs(path, **kwargs):
    data = gateway_inputs(path, requires=["user-confirmation"], registry_change=selected, **kwargs)
    data[2]["confirmation_container"] = "user-confirmations"
    data[3]["infrastructure"]["confirmation_container"] = "user-confirmations"
    return data


def confirmation_authority(data):
    from test_control_plane import Harness
    from test_user_confirmation import confirmation_config
    h = Harness()
    try:
        authority = confirmation_config(h)
    finally:
        asyncio.run(h.close())
    _, _, config, deployment, _ = data
    binding = deployment["bindings"]
    authority.update(
        cosmos_container=config["confirmation_container"], public_url=config["control_plane_url"],
        gateway_principals=[binding["gateway_principal"]])
    profile = authority["profiles"].pop("email-basic")
    authority["profiles"] = {"email": profile}
    profile["users"][0]["client"] = deployment["infrastructure"]["human_clients"][0]
    profile["workloads"] = [{
        "workload": binding["agent_principal"], "client": binding["agent_client_id"],
        "agent_id": config["agent_id"], "actions": ["act"],
    }]
    return authority


@pytest.mark.parametrize("declared", [True, False])
def test_one_sided_user_confirmation_selection_fails_before_generation(tmp_path, declared):
    data = gateway_inputs(
        tmp_path, requires=["user-confirmation"] if declared else [],
        registry_change=None if declared else selected)
    project, document, config, _, _ = data
    config["confirmation_container"] = "user-confirmations"
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_registry_confirmation_requirement_mismatch"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_confirmation_generation_requires_explicit_ephemeral_store(tmp_path):
    project, document, config, _, _ = confirmation_inputs(tmp_path)
    del config["confirmation_container"]
    before = snapshot(project)
    with pytest.raises(ValueError, match="confirmation_container_required"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_confirmation_package_vendors_real_protocol_and_freezes_store(tmp_path):
    data = confirmation_inputs(tmp_path)
    project = data[0]
    package(data)
    frozen = json.loads((project / "src/agent/governance-config.json").read_text())
    assert frozen["confirmation_container"] == "user-confirmations"
    for name in ("confirmation.py", "confirmation_client.py", "confirmation_entra.py", "confirmation_user.py"):
        assert (project / "src/agent/vendor/control-plane" / name).is_file()
        assert (project / "src/govern-control-plane/vendor/control-plane" / name).is_file()


def test_confirmation_bind_rejects_store_change_before_writes(tmp_path):
    data = confirmation_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    project, document, _, deployment, _ = data
    changed = deepcopy(deployment)
    changed["infrastructure"]["confirmation_container"] = "another-store"
    before = snapshot(project)
    with pytest.raises(ValueError, match="confirmation_container_changed"):
        generator.bind(project, document, configuration=changed)
    assert snapshot(project) == before


def test_confirmation_bind_needs_real_matching_provider_configuration(tmp_path):
    data = confirmation_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    project, document, _, deployment, _ = data
    before = snapshot(project)
    with pytest.raises(ValueError, match="confirmation_configuration_required"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before


def test_confirmation_bind_preserves_authority_and_requesting_user_access(tmp_path):
    data = confirmation_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    project, document, _, deployment, _ = data
    deployment["confirmation"] = confirmation_authority(data)
    generator.bind(project, document, configuration=deployment)
    bound = json.loads((project / ".threadlight/governance-deployment.json").read_text())["bindings"]
    control = bound["control_config"]
    assert control["confirmation"]["cosmos_container"] == "user-confirmations"
    assert control["confirmation"]["gateway_principals"] == [deployment["bindings"]["gateway_principal"]]
    assert control["confirmation_subjects"] == [
        deployment["confirmation"]["profiles"]["email"]["users"][0]["subject"]]
    repeated = deepcopy(deployment)
    repeated["bindings"] = bound
    del repeated["confirmation"]
    generator.bind(project, document, configuration=repeated)
    rebound = json.loads((project / ".threadlight/governance-deployment.json").read_text())["bindings"]
    assert rebound["control_config"]["confirmation"] == control["confirmation"]


@pytest.mark.parametrize("fault", ["null", "profile", "workload", "user-client", "origin", "gateway"])
def test_confirmation_bind_rejects_partial_authority_without_writing(tmp_path, fault):
    data = confirmation_inputs(tmp_path)
    generator = package(data)
    stage(generator, data)
    project, document, _, deployment, _ = data
    authority = confirmation_authority(data)
    if fault == "null":
        authority = None
    elif fault == "profile":
        authority["profiles"] = {"other": authority["profiles"]["email"]}
    elif fault == "workload":
        authority["profiles"]["email"]["workloads"][0]["actions"] = ["different_action"]
    elif fault == "user-client":
        authority["profiles"]["email"]["users"][0]["client"] = "99999999-9999-9999-9999-999999999999"
    elif fault == "origin":
        authority["public_url"] = "https://another.example"
    else:
        authority["gateway_principals"] = ["99999999-9999-9999-9999-999999999999"]
    deployment["confirmation"] = authority
    before = snapshot(project)
    with pytest.raises(ValueError, match="confirmation_"):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before
