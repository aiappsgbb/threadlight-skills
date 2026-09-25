"""Signed deferred approval settings must survive generation without weakening requirements."""
from copy import deepcopy
import json

import pytest

from test_maf_gateway_generation import gateway_inputs, package, stage
from test_governance_wiring import module
from test_governance_quality import inputs, snapshot
from test_gateway import gateway

pytestmark = pytest.mark.governance_runtime


@pytest.fixture(autouse=True)
def current_gateway_schema():
    gateway("dispatcher")


def deferred(registry):
    registry["actions"][0].update(
        approval_mode="deferred", approval_roles=["Approver"], approval_timeout_seconds=900)


def test_deferred_window_reaches_both_real_service_configurations(tmp_path):
    data = gateway_inputs(tmp_path, requires=["approval"], registry_change=deferred)
    project, document, config, deployment, _ = data
    config["approval_max_seconds"] = 900
    deployment["infrastructure"]["approval_max_seconds"] = 900
    generator = package(data)
    stage(generator, data)
    generator.bind(project, document, configuration=deployment)
    bound = json.loads((project / ".threadlight/governance-deployment.json").read_text())
    assert bound["bindings"]["control_config"]["approval_max_seconds"] == 900
    assert bound["bindings"]["gateway_config"]["approval_max_seconds"] == 900


def test_deferred_window_above_service_limit_rejects_before_writes(tmp_path):
    project, document, config, _, _ = gateway_inputs(tmp_path, registry_change=deferred)
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_registry_approval_timeout_mismatch"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_policy_condition_cannot_weaken_an_always_required_approval(tmp_path):
    def conditional(registry):
        registry["actions"][0].update(approval_requirement="policy", approval_roles=["Approver"])

    project, document, config, _, _ = gateway_inputs(
        tmp_path, requires=["approval"], registry_change=conditional)
    before = snapshot(project)
    with pytest.raises(ValueError, match="signed_registry_contract_approval_mismatch"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def test_bind_cannot_change_the_frozen_review_window(tmp_path):
    data = gateway_inputs(tmp_path, registry_change=deferred)
    project, document, config, deployment, _ = data
    config["approval_max_seconds"] = 900
    deployment["infrastructure"]["approval_max_seconds"] = 900
    generator = package(data)
    stage(generator, data)
    changed = deepcopy(deployment)
    changed["infrastructure"]["approval_max_seconds"] = 300
    before = snapshot(project)
    with pytest.raises(ValueError, match="frozen.*approval"):
        generator.bind(project, document, configuration=changed)
    assert snapshot(project) == before


def test_ghcp_cannot_select_deferred_mode_without_a_resume_adapter(tmp_path):
    project, document, config, _, _ = inputs(
        tmp_path, framework="github-copilot-sdk", requires=["approval"], registry_change=deferred)
    config["approval_max_seconds"] = 900
    before = snapshot(project)
    with pytest.raises(ValueError, match="ghcp_deferred_approval_resume_unsupported"):
        module("generate").generate(project, document, configuration=config)
    assert snapshot(project) == before


def outlook_profile(deployment):
    bindings = deployment["bindings"]
    tenant = deployment["infrastructure"]["tenant_id"]
    return {
        "workflow_resource_id": f"/subscriptions/{tenant}/resourceGroups/fixture/providers/Microsoft.Logic/workflows/review",
        "workflow_version": "1", "workflow_digest": "sha256:" + "d" * 64,
        "trigger_url": "https://fixture.region.logic.azure.com/workflows/fixture/triggers/Review_notification_requested/paths/invoke?api-version=2016-10-01",
        "sender_principal": bindings["control_principal"], "recipient": "reviewer@example.com",
        "requesters": [bindings["gateway_principal"]], "actions": ["act"],
        "responders": [{
            "home_tenant": tenant, "home_subject": deployment["infrastructure"]["approver_subjects"][0],
            "approver": deployment["infrastructure"]["approver_subjects"][0], "role": "Approver",
        }],
    }


def test_bind_preserves_explicit_native_outlook_selection_in_both_services(tmp_path):
    data = gateway_inputs(tmp_path)
    project, document, _, deployment, _ = data
    deployment["outlook_approval"] = outlook_profile(deployment)
    generator = package(data)
    stage(generator, data)
    generator.bind(project, document, configuration=deployment)
    bound = json.loads((project / ".threadlight/governance-deployment.json").read_text())["bindings"]
    assert bound["control_config"]["outlook_approval"]["workflow_digest"] == "sha256:" + "d" * 64
    assert bound["gateway_config"]["approval_channel"] == "outlook"
    assert (project / "src/govern-control-plane/vendor/control-plane/outlook.py").is_file()


def test_bind_preserves_explicit_scoped_operation_controller(tmp_path):
    data = gateway_inputs(tmp_path)
    project, document, _, deployment, _ = data
    infrastructure, bindings = deployment["infrastructure"], deployment["bindings"]
    operator = infrastructure["approver_subjects"][0]
    controllers = {operator: {"client_id": infrastructure["human_clients"][0],
                              "subjects": [bindings["agent_principal"]], "actions": ["act"]}}
    deployment["operation_controllers"] = controllers
    generator = package(data)
    stage(generator, data)
    generator.bind(project, document, configuration=deployment)
    bound = json.loads((project / ".threadlight/governance-deployment.json").read_text())["bindings"]
    assert bound["gateway_config"]["operations_required"] is True
    assert bound["gateway_config"]["operation_controllers"] == controllers
    assert not bound["control_config"]["operation_controllers"]


@pytest.mark.parametrize("fault", ["null", "requester", "sender", "role"])
def test_invalid_selected_outlook_configuration_never_falls_back_to_delegated(tmp_path, fault):
    data = gateway_inputs(tmp_path)
    project, document, _, deployment, _ = data
    profile = outlook_profile(deployment)
    if fault == "null":
        profile = None
    elif fault == "requester":
        profile["requesters"] = [deployment["bindings"]["agent_principal"]]
    elif fault == "sender":
        profile["sender_principal"] = deployment["bindings"]["agent_principal"]
    else:
        profile["responders"][0]["role"] = "Unassigned"
    deployment["outlook_approval"] = profile
    generator = package(data)
    stage(generator, data)
    before = snapshot(project)
    with pytest.raises(ValueError):
        generator.bind(project, document, configuration=deployment)
    assert snapshot(project) == before
