"""Bounded read-only ARM + Foundry v1 observations, independent of declarations.

Wire fields: azure-ai-projects 2.3.0 AgentDetails, VersionSelector,
AgentVersionDetails, HostedAgentDefinition and ContainerConfiguration.
https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-agent
"""
from __future__ import annotations

import importlib
import re
import subprocess
from urllib.parse import urlsplit
from skills._shared.governance_configuration import project_environment, SERVICE_ENVIRONMENT

UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
SELECTORS = frozenset({"subscription", "resource_group", "project_resource_id", "agent_name", "requested_version"})


class ObservationError(ValueError):
    pass


def primitives():
    try:
        return importlib.import_module("govern_canonical.ghcp")
    except ModuleNotFoundError:
        return importlib.import_module("skills.threadlight-governed-actions.scripts.ghcp")


def run_command(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def command_json(run, args):
    value, error, code = primitives()._run_read_only_command(run, args)
    if error or not isinstance(value, dict):
        raise ObservationError("azure-observation-unavailable")
    return value


def resource(run, resource_id, subscription):
    if not resource_id.startswith(f"/subscriptions/{subscription}/resourceGroups/"):
        raise ObservationError("resource-scope-mismatch")
    value = command_json(run, ["az", "resource", "show", "--ids", resource_id,
                               "--subscription", subscription, "-o", "json"])
    if value.get("id", "").casefold() != resource_id.casefold():
        raise ObservationError("resource-identity-mismatch")
    return value


def observe(selection, run=run_command):
    try:
        if (not isinstance(selection, dict) or set(selection) - SELECTORS
                or not SELECTORS.difference({"requested_version"}) <= set(selection)
                or any(not isinstance(value, str) or not 0 < len(value) <= 512
                       for key, value in selection.items() if not (key == "requested_version" and value is None))):
            raise ObservationError("invalid-selection")
        subscription = primitives().resolve_subscription_id(selection["subscription"], run)
        project_id = selection["project_resource_id"]
        if not re.fullmatch(
            rf"/subscriptions/{UUID}/resourceGroups/[A-Za-z0-9_.()-]+/providers/"
            r"Microsoft.CognitiveServices/accounts/[A-Za-z0-9-]+/projects/[A-Za-z0-9_-]+", project_id):
            raise ObservationError("project-selector-invalid")
        if (project_id.split("/")[2] != subscription
                or project_id.split("/")[4] != selection["resource_group"]):
            raise ObservationError("project-selector-scope-mismatch")
        account = command_json(run, [
            "az", "rest", "--method", "GET", "--url",
            f"https://management.azure.com/subscriptions/{subscription}?api-version=2022-12-01",
            "--subscription", subscription, "-o", "json"])
        if (account.get("subscriptionId") != subscription or account.get("state") != "Enabled"
                or not re.fullmatch(UUID, account.get("tenantId", ""))):
            raise ObservationError("subscription-observation-incomplete")
        project = resource(run, project_id, subscription)
        if (project["type"].lower() != "microsoft.cognitiveservices/accounts/projects"
                or project["properties"]["provisioningState"] != "Succeeded"):
            raise ObservationError("project-not-ready")
        endpoints = project["properties"]["endpoints"]
        # Consume an ARM-returned URL; never construct one from a selected account name.
        candidates = {url.rstrip("/") for url in endpoints.values() if isinstance(url, str)
                      and re.fullmatch(r"https://[a-z0-9-]+\.services\.ai\.azure\.com/api/projects/[A-Za-z0-9_-]+/?", url)}
        if len(candidates) != 1:
            raise ObservationError("project-endpoint-not-observed")
        endpoint = candidates.pop()
        name = selection["agent_name"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", name):
            raise ObservationError("agent-selector-invalid")

        def foundry(path):
            return command_json(run, ["az", "rest", "--method", "GET", "--url",
                endpoint + path + "?api-version=v1", "--resource", "https://ai.azure.com",
                "--subscription", subscription, "-o", "json"])

        agent = foundry("/agents/" + name)
        if agent["name"] != name or agent["object"] != "agent" or agent["state"] != "enabled":
            raise ObservationError("agent-not-active")
        rules = agent["agent_endpoint"]["version_selector"]["version_selection_rules"]
        if (len(rules) != 1 or set(rules[0]) != {"type", "agent_version", "traffic_percentage"}
                or rules[0]["type"] != "FixedRatio"
                or type(rules[0]["traffic_percentage"]) is not int or rules[0]["traffic_percentage"] != 100):
            raise ObservationError("current-version-not-unambiguously-pinned")
        latest = agent["versions"]["latest"]["version"]
        if not isinstance(latest, str) or not re.fullmatch(r"\d+", latest):
            raise ObservationError("latest-version-not-observed")
        current = rules[0]["agent_version"]
        if current == "latest":
            current = agent["versions"]["latest"]["version"]
        if not isinstance(current, str) or not re.fullmatch(r"\d+", current):
            raise ObservationError("current-version-not-observed")
        if selection.get("requested_version") not in (None, current):
            raise ObservationError("requested-version-is-not-current")
        version = foundry(f"/agents/{name}/versions/{current}")
        definition = version["definition"]
        if (version["name"] != name or version["version"] != current
                or version["object"] != "agent.version" or version["status"] != "active"
                or definition["kind"] != "hosted" or version.get("draft", False) is not False):
            raise ObservationError("version-not-active-hosted")
        image = definition["container_configuration"]["image"]
        if not re.fullmatch(r"[a-z0-9.-]+\.azurecr\.io/[A-Za-z0-9_./-]+@sha256:[0-9a-f]{64}", image):
            raise ObservationError("immutable-image-not-observed")
        identity = version["instance_identity"]
        if any(not re.fullmatch(UUID, identity[key]) for key in ("principal_id", "client_id")):
            raise ObservationError("agent-identity-not-observed")
        protocols = definition["protocol_versions"]
        choices = [p["protocol"] for p in protocols
                   if p.get("version") == "2.0.0" and p.get("protocol") in ("responses", "invocations")]
        if len(choices) != 1:
            raise ObservationError("supported-invocation-protocol-not-observed")
        if definition.get("environment_variables", {}).get("TL_GOV_IMAGE_DIGEST") != image.split("@")[1]:
            raise ObservationError("runtime-image-environment-mismatch")
        return {"source": "azure-arm-and-foundry", "tenant": account["tenantId"],
                "subscription": project["id"].split("/")[2], "resource_group": project["id"].split("/")[4],
                "project_resource_id": project["id"], "project_endpoint": endpoint,
                "agent_id": version["name"], "agent_version": version["version"],
                "image_digest": image.split("@")[1], "subject": identity["principal_id"],
                "client_id": identity["client_id"], "protocol": choices[0],
                "version_selector": rules, "latest_version": latest,
                "configuration_digests": project_environment(definition.get("environment_variables", {}))}
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("azure-observation-incomplete") from None


def observe_service(expected, subscription, resource_group, run=run_command):
    """Observe ACA revision image, ingress and UAMI from ARM, not service self-report."""
    try:
        value = resource(run, expected["resource_id"], subscription)
        if (value["id"].split("/")[4] != resource_group
                or value["type"].lower() != "microsoft.app/containerapps"):
            raise ObservationError("service-resource-scope-mismatch")
        properties = value["properties"]
        origin = "https://" + properties["configuration"]["ingress"]["fqdn"]
        identities = value["identity"]["userAssignedIdentities"].values()
        identity = next((i for i in identities if i["principalId"] == expected["principal_id"]
                         and i["clientId"] == expected["client_id"]), None)
        containers = properties["template"]["containers"]
        if (identity is None or properties["provisioningState"] != "Succeeded"
                or properties["runningStatus"] != "Running" or origin != expected["url"]
                or properties["configuration"]["activeRevisionsMode"] != "Single"
                or properties["latestRevisionName"] != properties["latestReadyRevisionName"]
                or len(containers) != 1 or containers[0]["image"] != expected["image"]
                or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", containers[0]["image"])):
            raise ObservationError("service-deployment-mismatch")
        return {"resource_id": value["id"], "url": origin,
                "principal_id": identity["principalId"], "client_id": identity["clientId"],
                "image": containers[0]["image"], "revision": properties["latestReadyRevisionName"],
                "source": "azure-arm",
                "configuration_digests": project_environment(containers[0].get("env", []),
                                                              names=SERVICE_ENVIRONMENT)}
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("service-observation-incomplete") from None
