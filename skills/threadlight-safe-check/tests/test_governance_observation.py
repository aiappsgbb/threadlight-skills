import copy
import json
import subprocess

import pytest

from test_governance_gates import reference

TENANT = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"
SUBJECT = "33333333-3333-3333-3333-333333333333"
CLIENT = "44444444-4444-4444-4444-444444444444"
PROJECT = f"/subscriptions/{SUB}/resourceGroups/staging/providers/Microsoft.CognitiveServices/accounts/account/projects/project"
ENDPOINT = "https://account.services.ai.azure.com/api/projects/project"
DIGEST = "sha256:" + "a" * 64


def selection():
    return {"subscription": SUB, "resource_group": "staging", "project_resource_id": PROJECT,
            "agent_name": "probe-agent", "requested_version": "1"}


class ARMFoundry:
    """Structured documented ARM/Foundry responses, never collector output."""
    def __init__(self):
        self.calls = []
        self.subscription = {"subscriptionId": SUB, "tenantId": TENANT, "state": "Enabled"}
        self.project = {"id": PROJECT, "type": "Microsoft.CognitiveServices/accounts/projects",
                        "name": "account/project", "properties": {
                            "provisioningState": "Succeeded", "endpoints": {"AI Foundry API": ENDPOINT}}}
        self.version = {
            "object": "agent.version", "name": "probe-agent", "id": "probe-agent:1",
            "version": "1", "status": "active", "metadata": {}, "created_at": 1788624000,
            "instance_identity": {"principal_id": SUBJECT, "client_id": CLIENT},
            "definition": {"kind": "hosted", "cpu": "1", "memory": "2Gi",
                           "container_configuration": {"image": "account.azurecr.io/agent@" + DIGEST},
                           "environment_variables": {"TL_GOV_IMAGE_DIGEST": DIGEST},
                           "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}]}}
        self.agent = {"object": "agent", "name": "probe-agent", "id": "probe-agent",
                      "state": "enabled", "versions": {"latest": self.version},
                      "agent_endpoint": {"version_selector": {"version_selection_rules": [
                          {"type": "FixedRatio", "agent_version": "1", "traffic_percentage": 100}]},
                          "protocols": ["responses"], "protocol_configuration": {"responses": {}}}}
        self.resources = {}

    def __call__(self, command):
        self.calls.append(list(command))
        assert command[:2] not in (["az", "login"], ["az", "account"]) or command[:3] == ["az", "account", "list"]
        assert "--subscription" in command
        if command[:3] == ["az", "resource", "show"]:
            rid = command[command.index("--ids") + 1]
            data = self.project if rid == PROJECT else self.resources[rid]
        elif command[:2] == ["az", "rest"]:
            assert command[command.index("--method") + 1] == "GET"
            url = command[command.index("--url") + 1]
            if url.startswith("https://management.azure.com/subscriptions/"):
                data = self.subscription
            elif "/versions/" in url:
                data = self.version
            else:
                data = self.agent
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(data), "")


def test_observe_actual_pinned_foundry_version_and_preserve_selection():
    observer = reference("governance_observation")
    run = ARMFoundry()
    original = selection()
    result = observer.observe(original, run)
    assert result["tenant"] == TENANT and result["agent_version"] == "1"
    assert result["image_digest"] == DIGEST and result["subject"] == SUBJECT
    assert result["client_id"] == CLIENT and result["project_endpoint"] == ENDPOINT
    assert result["source"] == "azure-arm-and-foundry"
    assert original == selection()
    assert any("/versions/1?api-version=v1" in " ".join(call) for call in run.calls)


@pytest.mark.parametrize("alias", ["@latest", "latest"])
def test_native_sdk_latest_selector_resolves_only_the_observed_current_version(alias):
    from azure.ai.projects.models import FixedRatioVersionSelectionRule, VersionSelector

    observer = reference("governance_observation")
    run = ARMFoundry()
    selector = VersionSelector(version_selection_rules=[
        FixedRatioVersionSelectionRule(agent_version=alias, traffic_percentage=100),
    ]).as_dict()
    run.agent["agent_endpoint"]["version_selector"] = selector
    result = observer.observe(selection(), run)
    assert result["agent_version"] == result["latest_version"] == "1"
    assert result["version_selector"] == selector["version_selection_rules"]
    assert any("/versions/1?api-version=v1" in " ".join(call) for call in run.calls)

    run.version["version"] = "2"
    with pytest.raises(observer.ObservationError, match="requested-version-is-not-current"):
        observer.observe(selection(), run)


@pytest.mark.parametrize("alias", ["@latest-preview", "@latest ", "$latest", "LATEST"])
def test_unknown_latest_selectors_are_not_guessed(alias):
    observer = reference("governance_observation")
    run = ARMFoundry()
    run.agent["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["agent_version"] = alias
    with pytest.raises(observer.ObservationError, match="current-version-not-observed"):
        observer.observe(selection(), run)


@pytest.mark.parametrize("missing", ["protocols", "protocol_configuration"])
def test_declared_container_protocol_must_be_exposed_by_the_observed_endpoint(missing):
    observer = reference("governance_observation")
    run = ARMFoundry()
    run.version["definition"]["protocol_versions"][0]["protocol"] = "invocations"
    run.agent["agent_endpoint"]["protocols"] = ["responses", "invocations"]
    run.agent["agent_endpoint"]["protocol_configuration"]["invocations"] = {}
    run.agent["agent_endpoint"][missing] = ["responses"] if missing == "protocols" else {"responses": {}}
    with pytest.raises(observer.ObservationError, match="invocation-protocol-not-exposed"):
        observer.observe(selection(), run)


@pytest.mark.parametrize("fault", ["tenant", "scope", "image-missing", "image-tag", "identity", "version",
                                   "not-current", "traffic", "boolean", "disabled", "protocol", "routing-extension"])
def test_observation_never_substitutes_declared_metadata(fault):
    observer = reference("governance_observation")
    run = ARMFoundry()
    if fault == "tenant":
        run.subscription.pop("tenantId")
    elif fault == "scope":
        run.project["id"] = PROJECT.replace("/staging/", "/other/")
    elif fault == "image-missing":
        run.version["definition"].pop("container_configuration")
    elif fault == "image-tag":
        run.version["definition"]["container_configuration"]["image"] = "image:latest"
    elif fault == "identity":
        run.version.pop("instance_identity")
    elif fault == "version":
        run.version["version"] = "2"
    elif fault == "not-current":
        run.agent["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["agent_version"] = "2"
    elif fault == "traffic":
        run.agent["agent_endpoint"]["version_selector"]["version_selection_rules"].append(
            {"type": "FixedRatio", "agent_version": "2", "traffic_percentage": 1})
    elif fault == "boolean":
        run.agent["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["traffic_percentage"] = True
    elif fault == "disabled":
        run.agent["state"] = "disabled"
    elif fault == "protocol":
        run.version["definition"]["protocol_versions"] = []
    else:
        run.agent["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["unknown"] = "PRIVATE"
    with pytest.raises(observer.ObservationError):
        observer.observe(selection(), run)


def test_observation_failures_never_echo_cli_payloads():
    observer = reference("governance_observation")
    for stdout, stderr in (("PRIVATE", "PRIVATE"), ('{"accessToken":"PRIVATE"}', "PRIVATE")):
        with pytest.raises(observer.ObservationError) as failure:
            observer.observe(selection(), lambda cmd: subprocess.CompletedProcess(cmd, 1, stdout, stderr))
        assert "PRIVATE" not in str(failure.value)


def test_reusable_command_collector_imports_as_portable_package():
    import importlib
    # Exercise relative package imports, also packaged by the collector wheel.
    module = importlib.import_module("skills.threadlight-governed-actions.scripts.ghcp")
    assert module.resolve_subscription_id(SUB, lambda command: pytest.fail("unneeded CLI")) == SUB


@pytest.mark.parametrize("fault", ["stale", "multiple"])
def test_observed_service_template_is_not_serving_revision(fault):
    observer = reference("governance_observation")
    run = ARMFoundry()
    rid = f"/subscriptions/{SUB}/resourceGroups/staging/providers/Microsoft.App/containerApps/gateway"
    expected = {"resource_id": rid, "url": "https://gateway.example", "image": "account.azurecr.io/service@" + DIGEST,
                "principal_id": SUBJECT, "client_id": CLIENT}
    run.resources[rid] = {"id": rid, "type": "Microsoft.App/containerApps",
        "identity": {"userAssignedIdentities": {"id": {"principalId": SUBJECT, "clientId": CLIENT}}},
        "properties": {"provisioningState": "Succeeded", "runningStatus": "Running",
            "latestReadyRevisionName": "old" if fault == "stale" else "new", "latestRevisionName": "new",
            "configuration": {"activeRevisionsMode": "Multiple" if fault == "multiple" else "Single",
                              "ingress": {"fqdn": "gateway.example"}},
            "template": {"containers": [{"image": expected["image"]}]}}}
    with pytest.raises(observer.ObservationError):
        observer.observe_service(expected, SUB, "staging", run)
