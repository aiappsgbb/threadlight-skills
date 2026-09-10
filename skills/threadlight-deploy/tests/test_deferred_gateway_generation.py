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
