#!/usr/bin/env python3
"""Package selected governance, then bind independently built deployment images."""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import shutil
import sys
import uuid
import tomllib

CATALOG = Path(__file__).resolve().parents[4]
REFERENCE = Path(__file__).resolve().parent
GOVERN = CATALOG / "skills/threadlight-govern"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
RESOURCE = rf"/subscriptions/{UUID}/resourceGroups/[^/]+/providers/"


def validate_network(network):
    if network.get("posture") == "private-required":
        required = ("environment_id", "vnet_id", "private_endpoint_subnet_id",
                    "foundry_injection_subnet_id", "blob_dns_zone_id",
                    "cosmos_dns_zone_id", "keyvault_dns_zone_id")
        if any(not isinstance(network.get(key), str) or not network[key].startswith("/subscriptions/")
               for key in required):
            raise ValueError("private_network_dependencies_required")
        vnet = network["vnet_id"]
        if any(not network[key].startswith(vnet + "/subnets/") for key in (
                "private_endpoint_subnet_id", "foundry_injection_subnet_id")):
            raise ValueError("private_network_dependencies_must_share_reachable_vnet")
    elif network.get("posture") == "public-pilot":
        ranges = network.get("allowed_ips", [])
        parsed = [ipaddress.ip_network(value) for value in ranges]
        if not ranges or any(ip.version != 4 or ip.prefixlen in (0, 31)
                             or ipaddress.ip_address("0.0.0.0") in ip for ip in parsed):
            raise ValueError("explicit_restricted_ip_allowlist_required")
    else:
        raise ValueError("explicit_network_posture_required")
    if not re.fullmatch(RESOURCE + r"Microsoft.App/managedEnvironments/[^/]+",
                        network.get("environment_id", "")):
        raise ValueError("invalid_environment_id")
    return deepcopy(network)


def validate_contract(document):
    # The generator runs in the catalog; generated consumers get this same package.
    if str(CATALOG) not in sys.path:
        sys.path.insert(0, str(CATALOG))
    from skills._shared.governance import validate_governance_contract
    return validate_governance_contract(
        document, deployment_target="customer-pilot", runtime=document["framework"])


def generate(project, document, *, configuration=None):
    source_contract = deepcopy(document)
    document = validate_contract(document)
    if document["governance"]["mode"] == "off":
        return {"status": "off"}
    if configuration is None:
        raise ValueError("configuration_required")
    import yaml
    from govern_control_plane.models import SignedBundle, parse
    from govern_control_plane.client import ServiceTransport
    from datetime import datetime, timezone
    config = deepcopy(configuration)
    validate_network(config["network"])
    for key in ("tenant_id",):
        if not re.fullmatch(UUID, config.get(key, "")):
            raise ValueError("invalid_tenant_id")
    for key in ("control_plane_scope", "gateway_scope"):
        if not re.fullmatch(rf"api://{UUID}/\.default", config.get(key, "")):
            raise ValueError("provided_entra_application_scope_required")
    if config["control_plane_scope"] == config["gateway_scope"]:
        raise ValueError("separate_service_audiences_required")
    ServiceTransport.validate_configuration(config["control_plane_url"], config["control_plane_scope"], 5)
    bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    bundle = bundle_api.verify_bundle(Path(config["bundle_path"]), expected_digest=config["policy_digest"])
    bundle_api.validate_native_manifest(bundle.root)
    signed_raw = Path(config["signed_envelope"]).read_bytes()
    signed = parse(SignedBundle, signed_raw)
    envelope = signed.envelope
    if (envelope.tenant_id != config["tenant_id"] or envelope.key_id != config["key_id"]
            or envelope.policy_id != config["policy_id"] or envelope.version != config["policy_version"]
            or envelope.content_digest != bundle.bundle_digest
            or envelope.expires_at <= datetime.now(timezone.utc)):
        raise ValueError("signed_policy_identity_or_expiry_invalid")
    project = Path(project).resolve()
    azure = yaml.safe_load((project / "azure.yaml").read_text())
    service = azure["services"][config["agent_service"]]
    relative = Path(service["project"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("agent_source_must_be_inside_project")
    agent = project / relative
    if service.get("host") != "azure.ai.agent":
        raise ValueError("explicit_hosted_agent_required")
    if document["framework"] == "microsoft-agent-framework":
        if not (agent / "governance_application.py").is_file():
            raise ValueError("host_owned_governance_application_required")
        if any(t["enforcement_path"] == "governed-tool-gateway" for t in document["tools"]):
            raise ValueError("maf_gateway_adapter_not_selected_use_local_hooks_or_explicit_ghcp")
        template = "maf-container.py"
    else:
        template = "ghcp-container.py"
        if document["governance"]["lifecycle_bindings"]:
            raise ValueError("ghcp_lifecycle_hooks_unsupported")
        spec = importlib.util.spec_from_file_location("governed_ghcp", REFERENCE / template)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        runtime.route_mcp_servers(config["mcp_servers"], source_contract, config["mcp_bindings"],
                                  config["gateway_url"])
    for name in ("govern-control-plane", "govern-gateway"):
        if name in azure["services"] or (project / "src" / name).exists():
            raise ValueError("governance_services_already_exist")
    # Staging lives beside the destination, not in the OS temporary directory.
    staging = project / f".governance-stage-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        target = staging / "agent"
        target.mkdir()
        copy_sources(target)
        shutil.copyfile(REFERENCE / template, target / "container.py")
        pyproject = (REFERENCE / "pyproject-maf.toml").read_text()
        if document["framework"] == "github-copilot-sdk":
            pyproject = pyproject.replace('  "agent-framework-core==1.14.0",', '  "github-copilot-sdk==1.0.1",')
            pyproject = "\n".join(line for line in pyproject.splitlines()
                                  if "agent-framework-foundry" not in line) + "\n"
        if (agent / "pyproject.toml").exists():
            pyproject = merge_dependencies((agent / "pyproject.toml").read_text(), pyproject)
        (target / "pyproject.toml").write_text(pyproject)
        if document["framework"] == "microsoft-agent-framework":
            if (bundle.root / "gateway-registry.json").exists():
                raise ValueError("local_agent_bundle_must_not_embed_its_own_image_digest")
            shutil.copytree(bundle.root, target / "policy")
            (target / "policy-envelope.json").write_bytes(signed_raw)
        portable = {k: v for k, v in config.items() if k not in (
            "bundle_path", "signed_envelope", "network", "agent_service")}
        portable["contract"] = source_contract
        if document["framework"] == "github-copilot-sdk":
            portable.pop("policy_digest", None)
        (target / "governance-config.json").write_text(json.dumps(portable, indent=2) + "\n")
        vendor_control_plane(target)
        write_dockerfile(target, agent=True)
        for name in ("govern-control-plane", "govern-gateway"):
            service_target = staging / name
            service_target.mkdir()
            vendor_control_plane(service_target)
            shutil.copyfile(REFERENCE / "service_entry.py", service_target / "service_entry.py")
            if name == "govern-gateway":
                vendor_gateway(service_target)
                shutil.copytree(bundle.root, service_target / "policy")
            write_dockerfile(service_target, gateway=name == "govern-gateway")
        for path in target.iterdir():
            destination = agent / path.name
            if destination.exists() and destination.is_dir() and path.name != "skills":
                raise ValueError(f"generated_path_already_exists:{path.name}")
        for path in target.iterdir():
            if path.is_dir():
                shutil.copytree(path, agent / path.name, dirs_exist_ok=path.name == "skills")
            else:
                shutil.copyfile(path, agent / path.name)
        for name in ("govern-control-plane", "govern-gateway"):
            shutil.copytree(staging / name, project / "src" / name)
        compose(project, azure, config, document["framework"])
        package = project / ".threadlight"
        package.mkdir(exist_ok=True)
        (package / "governance-package.json").write_text(json.dumps({
            "schema": "threadlight-governance-package/v1", "configuration": config,
            "framework": document["framework"], "contract": source_contract,
            "status": "packaged-not-deployed",
        }, indent=2) + "\n")
    finally:
        shutil.rmtree(staging)
    return {"status": "packaged-not-deployed", "signature": "runtime-keyvault-verification-required",
            "policy_digest": bundle.bundle_digest}


def merge_dependencies(original, template):
    original_data = tomllib.loads(original)
    existing = original_data["project"].get("dependencies", [])
    added = tomllib.loads(template)["project"]["dependencies"]
    names = {dep.split("==")[0].lower() for dep in added}
    retained = [dep for dep in existing if re.split(r"[<>=!~;\[]", dep)[0].lower() not in names]
    combined = "dependencies = " + json.dumps(retained + added, indent=2)
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", original)
    if not project:
        raise ValueError("project_table_required")
    block = project.group()
    if re.search(r"(?m)^dependencies\s*=", block):
        block, count = re.subn(r"(?ms)^dependencies\s*=\s*\[.*?^\]", combined, block)
        if not count:
            block, count = re.subn(r"(?m)^dependencies\s*=\s*\[[^\n]*\]", combined, block)
        if count != 1:
            raise ValueError("unsupported_dependency_table")
    else:
        block += combined + "\n"
    result = original[:project.start()] + block + original[project.end():]
    if tomllib.loads(result)["project"]["dependencies"] != retained + added:
        raise ValueError("dependency_merge_failed")
    if "setuptools" not in original_data.get("tool", {}):
        # These are source-run hosted apps, not a distribution of all vendored
        # namespaces. Preserve any explicit application packaging configuration.
        result += "\n[tool.setuptools]\npackages = []\n"
    return result


def vendor_control_plane(target):
    destination = target / "vendor/control-plane"
    destination.mkdir(parents=True)
    for path in (GOVERN / "references/control-plane").iterdir():
        if path.suffix in (".py", ".toml"):
            shutil.copyfile(path, destination / path.name)


def vendor_gateway(target):
    destination = target / "vendor/gateway"
    destination.mkdir(parents=True)
    for path in (GOVERN / "references/gateway").glob("*.py"):
        shutil.copyfile(path, destination / path.name)
    for name, source in (
        ("govern_bundle", GOVERN / "scripts/policy_bundle.py"),
        ("govern_canonical", CATALOG / "skills/threadlight-governed-actions/scripts/canonical.py"),
        ("govern_shared", CATALOG / "skills/_shared/governance-upstream-pin.json"),
    ):
        (destination / name).mkdir()
        (destination / name / "__init__.py").write_text("")
        shutil.copyfile(source, destination / name / source.name)
    pyproject = (GOVERN / "references/gateway/pyproject.toml").read_text()
    pyproject = pyproject.replace('govern_bundle = "../../scripts"', 'govern_bundle = "govern_bundle"')
    pyproject = pyproject.replace('govern_canonical = "../../../threadlight-governed-actions/scripts"',
                                  'govern_canonical = "govern_canonical"')
    pyproject = pyproject.replace('govern_shared = "../../../_shared"', 'govern_shared = "govern_shared"')
    (destination / "pyproject.toml").write_text(pyproject)


def write_dockerfile(target, *, agent=False, gateway=False):
    # Copy the complete source context, explicitly excluding user credentials and deployment state.
    text = """ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY vendor/ ./vendor/
RUN python -m pip install --no-cache-dir ./vendor/control-plane
"""
    if gateway:
        text += "RUN python -m pip install --no-cache-dir ./vendor/gateway\n"
    if agent:
        text += "COPY . ./\nRUN python -m pip install --no-cache-dir .\n"
    if agent or gateway:
        pin = json.loads((CATALOG / "skills/_shared/governance-upstream-pin.json").read_text())["opa"]
        text += (
            f"ADD https://openpolicyagent.org/downloads/v{pin['version']}/opa_linux_amd64_static /app/opa\n"
            f"RUN echo '{pin['linux_amd64_static_sha256']}  /app/opa' | sha256sum -c - && chmod 555 /app/opa\n"
            "ENV ACS_OPA_PATH=/app/opa\n")
    if not agent:
        text += "COPY . ./\n"
    text += 'EXPOSE 8088\nCMD ["python", "container.py"]\n' if agent else (
        'EXPOSE 8000\nCMD ["python", "service_entry.py"]\n')
    (target / "Dockerfile").write_text(text)
    (target / ".dockerignore").write_text(
        ".git\n.azure\n.threadlight\n.env\n.env.*\n**/__pycache__\n**/.venv\n"
        "deployment*.json\nagent.yaml\nagent.manifest.yaml\n*.pem\n*.key\nrun/\naudit/\n")


def compose(project, azure, config, framework):
    import yaml
    additions = yaml.safe_load((REFERENCE / "azure-services.yaml").read_text())
    azure["services"].update(additions["services"])
    versions = azure.setdefault("requiredVersions", {})
    versions.setdefault("azd", ">=1.27.1")
    extensions = versions.setdefault("extensions", {})
    for name, minimum in (("azure.ai.agents", 8), ("azure.ai.projects", 4)):
        current = extensions.get(name)
        match = re.fullmatch(r">=1\.0\.0-beta\.(\d+)", current or "")
        if current is None or (match and int(match.group(1)) < minimum):
            extensions[name] = f">=1.0.0-beta.{minimum}"
    service = azure["services"][config["agent_service"]]
    service["image"] = "${TL_GOV_AGENT_IMAGE}"
    values = {
        "GOV_CONTROL_PLANE_URL": config["control_plane_url"],
        "GOVERNED_TOOL_GATEWAY_URL": config["gateway_url"],
        "TL_GOV_IMAGE_DIGEST": "${TL_GOV_IMAGE_DIGEST}",
        "TL_GOV_SPOOL_DIR": "${TL_GOV_SPOOL_DIR}",
    }
    # Current azd uses env; preserve older environmentVariables without duplicate definitions.
    if "environmentVariables" in service:
        entries = {entry["name"]: entry for entry in service["environmentVariables"]}
        entries.update({name: {"name": name, "value": value} for name, value in values.items()})
        service["environmentVariables"] = list(entries.values())
    else:
        service.setdefault("env", {}).update(values)
    service["protocols"] = [{"protocol": "responses" if framework == "microsoft-agent-framework"
                            else "invocations", "version": "2.0.0"}]
    azure["infra"] = {"provider": "bicep", "path": "./infra"}
    (project / "azure.yaml").write_text(yaml.safe_dump(azure, sort_keys=False))
    # Existing two-file consumers retain synchronized env declarations; do not create a new legacy authority.
    for path in (project / "agent.yaml", project / service["project"] / "agent.yaml"):
        if path.exists():
            legacy = yaml.safe_load(path.read_text())
            update_environment(legacy, "environment_variables", values)
            path.write_text(yaml.safe_dump(legacy, sort_keys=False))
    compose_infra(project)


def compose_infra(project):
    infra = project / "infra"
    infra.mkdir(exist_ok=True)
    for name in ("governance.bicep", "registry-pull.bicep"):
        shutil.copyfile(REFERENCE / name, infra / name)
    main = infra / "main.bicep"
    existing = main.read_text() if main.exists() else "targetScope = 'resourceGroup'\n"
    if "module governance 'governance.bicep'" in existing:
        return
    if re.search(r"targetScope\s*=\s*['\"]subscription", existing):
        raise ValueError("eject_resource_group_composition_required")
    main.write_text(existing + """
param governanceConfig object
param governancePhase string = 'foundation'
param governanceImages object = {}
param governanceBindings object = {}
module governance 'governance.bicep' = {
  name: 'governance'
  params: {
    config: governanceConfig
    phase: governancePhase
    images: governanceImages
    bindings: governanceBindings
  }
}
output GOV_CONTROL_PLANE_URL string = governance.outputs.GOV_CONTROL_PLANE_URL
output GOVERNED_TOOL_GATEWAY_URL string = governance.outputs.GOVERNED_TOOL_GATEWAY_URL
output TL_GOV_FOUNDATION object = {
  key_id: governance.outputs.TL_GOV_KEY_VERSION
  control_client: governance.outputs.TL_GOV_CONTROL_CLIENT
  control_principal: governance.outputs.TL_GOV_CONTROL_PRINCIPAL
  gateway_client: governance.outputs.TL_GOV_GATEWAY_CLIENT
  gateway_principal: governance.outputs.TL_GOV_GATEWAY_PRINCIPAL
  downstream_client: governance.outputs.TL_GOV_DOWNSTREAM_CLIENT
  downstream_principal: governance.outputs.TL_GOV_DOWNSTREAM_PRINCIPAL
  publisher_client: governance.outputs.TL_GOV_PUBLISHER_CLIENT
  publisher_principal: governance.outputs.TL_GOV_PUBLISHER_PRINCIPAL
  blob_url: governance.outputs.TL_GOV_BLOB_URL
  cosmos_url: governance.outputs.TL_GOV_COSMOS_URL
}
""")


def validate_images(images):
    for name in ("agent", "control_plane", "gateway"):
        if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9./_-]+@sha256:[0-9a-f]{64}", images.get(name, "")):
            raise ValueError("deployment_images_require_built_digests")
    return images


def update_environment(definition, field, values):
    if field in ("environmentVariables", "environment_variables"):
        current = definition.get(field, [])
        if isinstance(current, dict):
            current = [{"name": name, "value": value} for name, value in current.items()]
        entries = {entry["name"]: entry for entry in current}
        entries.update({name: {"name": name, "value": value} for name, value in values.items()})
        definition[field] = list(entries.values())
    else:
        definition.setdefault(field, {}).update(values)


def agent_image(project, document, *, configuration=None):
    """Freeze the hosted definition BEFORE discovering the real version and instance identity."""
    if validate_contract(document)["governance"]["mode"] == "off":
        return {"status": "off"}
    config = configuration or {}
    image = config.get("agent_image", "")
    if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9./_-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("deployment_images_require_built_digests")
    directory = config["spool_directory"]
    if not isinstance(directory, str) or not directory.startswith("/") or directory == "/":
        raise ValueError("host_owned_spool_mount_required")
    project = Path(project)
    package = json.loads((project / ".threadlight/governance-package.json").read_text())
    if document != package["contract"]:
        raise ValueError("packaged_contract_changed")
    import yaml
    path = project / "azure.yaml"
    azure = yaml.safe_load(path.read_text())
    service = azure["services"][package["configuration"]["agent_service"]]
    service["image"] = image
    values = {"TL_GOV_IMAGE_DIGEST": image.split("@")[1], "TL_GOV_SPOOL_DIR": directory}
    update_environment(service, "environmentVariables" if "environmentVariables" in service else "env", values)
    path.write_text(yaml.safe_dump(azure, sort_keys=False))
    for path in (project / "agent.yaml", project / service["project"] / "agent.yaml"):
        if path.exists():
            legacy = yaml.safe_load(path.read_text())
            update_environment(legacy, "environment_variables", values)
            legacy["image"] = image
            path.write_text(yaml.safe_dump(legacy, sort_keys=False))
    return {"status": "ready-for-bootstrap-registration", "image": image}


def stage_gateway(project, document, *, configuration=None):
    """Stage the final signed registry after the agent image exists, before gateway build."""
    if validate_contract(document)["governance"]["mode"] == "off":
        return {"status": "off"}
    from govern_control_plane.models import SignedBundle, canonical, parse
    from govern_gateway.dispatcher import Registry
    from datetime import datetime, timezone
    config = configuration or {}
    project = Path(project)
    package = json.loads((project / ".threadlight/governance-package.json").read_text())
    if package["contract"] != document or package["framework"] != "github-copilot-sdk":
        raise ValueError("selected_gateway_contract_required")
    bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    bundle = bundle_api.verify_bundle(Path(config["gateway_bundle"]), expected_digest=config["policy_digest"])
    bundle_api.validate_native_manifest(bundle.root)
    registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
    signed = parse(SignedBundle, Path(config["signed_envelope"]).read_bytes())
    expected = package["configuration"]
    if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9./_-]+@sha256:[0-9a-f]{64}", config.get("agent_image", "")):
        raise ValueError("deployment_images_require_built_digests")
    if (signed.envelope.content_digest != bundle.bundle_digest
            or signed.envelope.tenant_id != expected["tenant_id"]
            or signed.envelope.key_id != expected["key_id"]
            or signed.envelope.expires_at <= datetime.now(timezone.utc)
            or registry.deployment.image_digest != config["agent_image"].split("@")[1]
            or registry.gateway_url != expected["gateway_url"]):
        raise ValueError("signed_registry_deployment_mismatch")
    target = project / "src/govern-gateway/policy"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(bundle.root, target)
    # Keep the envelope outside the native digest-covered bundle, unchanged.
    (project / "src/govern-gateway/policy-envelope.json").write_bytes(canonical(signed))
    digest = tree_digest(project / "src/govern-gateway")
    return {"status": "staged-rebuild-gateway-required", "gateway_source_digest": digest,
            "policy_digest": bundle.bundle_digest}


def bind(project, document, *, configuration=None):
    """Offline binding of BUILT images and trusted phase-one observations; no Azure writes."""
    if validate_contract(document)["governance"]["mode"] == "off":
        return {"status": "off"}
    config = configuration or {}
    images = validate_images(config.get("images", {}))
    project = Path(project).resolve()
    package = json.loads((project / ".threadlight/governance-package.json").read_text())
    if document != package["contract"]:
        raise ValueError("packaged_contract_changed")
    infrastructure = config["infrastructure"]
    validate_infrastructure(infrastructure)
    bindings = deepcopy(config["bindings"])
    from govern_control_plane.auth import Settings
    from govern_control_plane.models import Digest, Identifier, ObjectId, canonical, parse
    for key in ("agent_principal", "agent_client_id", "gateway_principal", "gateway_client",
                "downstream_principal", "downstream_client"):
        parse(ObjectId, canonical(bindings[key]))
    parse(Identifier, canonical(bindings["agent_version"]))
    parse(Digest, canonical(bindings["policy_digest"]))
    if len({bindings[key] for key in ("agent_principal", "gateway_principal", "downstream_principal")}) != 3:
        raise ValueError("distinct_workload_identities_required")
    packaged = package["configuration"]
    if (infrastructure["runtime"] != package["framework"]
            or infrastructure["tenant_id"] != packaged["tenant_id"]
            or bindings["key_id"] != packaged["key_id"]
            or infrastructure["agent_id"] != packaged["agent_id"]
            or infrastructure["network"] != packaged["network"]):
        raise ValueError("deployment_trust_changed")
    common = {key: infrastructure[key] for key in (
        "tenant_id", "human_clients", "approver_subjects", "auditor_subjects", "approver_roles")}
    common["key_id"] = bindings["key_id"]
    settings = {}
    for service in ("control_plane", "gateway"):
        settings[service] = parse(Settings, canonical({
            **common, "audience": infrastructure[f"{service}_app_id"],
            "workloads": bindings["control_workloads" if service == "control_plane" else "gateway_workloads"],
        }))
    for service, subject, client in (
        ("control_plane", bindings["agent_principal"], bindings["agent_client_id"]),
        ("control_plane", bindings["gateway_principal"], bindings["gateway_client"]),
        ("gateway", bindings["agent_principal"], bindings["agent_client_id"]),
    ):
        workload = settings[service].workloads.get(subject)
        if (workload is None or workload.client_id != client or workload.agent_id != infrastructure["agent_id"]
                or bindings["policy_id"] not in workload.policies):
            raise ValueError("workload_allowlist_mismatch")
    verify_observations(infrastructure, bindings, config["observations"])
    from govern_control_plane.app import AzureConfiguration
    from govern_gateway.server import Configuration
    observed = config["observations"]
    foundation_values = observed["foundation"]
    control = {
        **common, "audience": infrastructure["control_plane_app_id"],
        "workloads": bindings["control_workloads"], "blob_url": foundation_values["blob_url"],
        "blob_container": "signed-catalog", "cosmos_url": foundation_values["cosmos_url"],
        "cosmos_database": "governance", "cosmos_container": "governance-records",
    }
    gateway = {
        **common, "audience": infrastructure["gateway_app_id"], "workloads": bindings["gateway_workloads"],
        "gateway_url": observed["gateway_url"], "control_plane_url": observed["control_plane_url"],
        "control_plane_scope": f"api://{infrastructure['control_plane_app_id']}/.default",
        "service_client_id": bindings["gateway_client"], "service_principal": bindings["gateway_principal"],
        "service_agent_id": infrastructure["agent_id"], "downstream_client_id": bindings["downstream_client"],
        "cosmos_url": foundation_values["cosmos_url"], "cosmos_database": "governance",
        "cosmos_container": "gateway-idempotency", "bundle_path": "/app/policy",
        "policy_id": bindings["policy_id"], "policy_version": bindings["policy_version"],
        "policy_digest": bindings["policy_digest"], "allowed_endpoints": bindings["allowed_endpoints"],
    }
    bindings["control_config"] = parse(AzureConfiguration, canonical(control)).model_dump(mode="json")
    bindings["gateway_config"] = parse(Configuration, canonical(gateway)).model_dump(mode="json")
    if package["framework"] == "microsoft-agent-framework":
        if bindings["policy_digest"] != packaged["policy_digest"]:
            raise ValueError("embedded_local_policy_digest_changed")
    else:
        from govern_gateway.dispatcher import Registry
        bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
        bundle = bundle_api.verify_bundle(Path(config["gateway_bundle"]), expected_digest=bindings["policy_digest"])
        registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
        if (registry.deployment.image_digest != images["agent"].split("@")[1]
                or registry.deployment.agent_version != bindings["agent_version"]
                or registry.deployment.agent_id != infrastructure["agent_id"]
                or registry.tenant_id != infrastructure["tenant_id"]
                or registry.gateway_url != observed["gateway_url"]):
            raise ValueError("signed_registry_deployment_mismatch")
        if not all({action.endpoint, action.outcome_endpoint} <= set(bindings["allowed_endpoints"])
                   for action in registry.actions):
            raise ValueError("downstream_allowlist_mismatch")
        if {a.name for a in registry.actions} != {
                t["id"] for t in document["tools"] if t["policy_binding"] not in (None, "none")}:
            raise ValueError("signed_registry_selection_mismatch")
        current = project / "src/govern-gateway/policy"
        if tree_digest(current) != tree_digest(bundle.root):
            raise ValueError("stage_gateway_before_build_and_bind")
        # Gateway image must be rebuilt AFTER embedding its registry. Its digest is
        # external to that registry (which binds the AGENT image), avoiding a cycle.
        if config.get("gateway_source_digest") != tree_digest(project / "src/govern-gateway"):
            raise ValueError("gateway_image_must_match_final_packaged_source")
    deployment = {"schema": "threadlight-governance-deployment/v1", "images": images,
                  "infrastructure": infrastructure, "bindings": bindings,
                  "status": "bound-unverified", "scope": "not-live-effect-closure"}
    parameters = project / "infra/main.parameters.json"
    payload = json.loads(parameters.read_text()) if parameters.exists() else {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0", "parameters": {}}
    payload["parameters"].update({
        "governanceConfig": {"value": infrastructure}, "governancePhase": {"value": "services"},
        "governanceImages": {"value": images}, "governanceBindings": {"value": bindings}})
    import yaml
    azure_path = project / "azure.yaml"
    azure = yaml.safe_load(azure_path.read_text())
    service = azure["services"][packaged["agent_service"]]
    if service["image"] != images["agent"]:
        raise ValueError("bootstrap_agent_image_before_binding")
    parameters.write_text(json.dumps(payload, indent=2) + "\n")
    (project / ".threadlight/governance-deployment.json").write_text(json.dumps(deployment, indent=2) + "\n")
    return {"status": "bound-unverified", "images": images}


def tree_digest(root):
    import hashlib
    files = {}
    for path in sorted(root.rglob("*")):
        if (path.is_file() and "__pycache__" not in path.parts
                and path.name not in ("agent.yaml", "agent.manifest.yaml")
                and not any(part in ("build", "dist") or part.endswith(".egg-info") for part in path.parts)):
            files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def validate_infrastructure(config):
    validate_network(config["network"])
    for key in ("tenant_id", "control_plane_app_id", "gateway_app_id"):
        if not re.fullmatch(UUID, config.get(key, "")):
            raise ValueError("provided_entra_application_ids_required")
    if config["control_plane_app_id"] == config["gateway_app_id"]:
        raise ValueError("separate_service_audiences_required")
    if config.get("acr_authorization") != "rbac":
        raise ValueError("abac_registry_requires_repository_scoped_pull_module")
    if not re.fullmatch(RESOURCE + r"Microsoft.ContainerRegistry/registries/[^/]+", config["acr_id"]):
        raise ValueError("existing_registry_required")
    for key, pattern in (("prefix", r"[a-z][a-z0-9-]{1,18}"),
                         ("storage_name", r"[a-z0-9]{3,24}"),
                         ("cosmos_name", r"[a-z][a-z0-9-]{2,43}"),
                         ("vault_name", r"[a-z][a-z0-9-]{1,22}[a-z0-9]")):
        if not re.fullmatch(pattern, config.get(key, "")):
            raise ValueError("invalid_dedicated_resource_name")


def verify_observations(config, bindings, observations):
    """Validate explicit read-only ARM/Entra exports, not a self-reported ready boolean."""
    environment = observations["environment"]
    if environment["id"] != config["network"]["environment_id"]:
        raise ValueError("environment_observation_mismatch")
    domain = environment["properties"]["defaultDomain"]
    if (observations["control_plane_url"] != f"https://{config['prefix']}-control.{domain}"
            or observations["gateway_url"] != f"https://{config['prefix']}-gateway.{domain}/mcp"):
        raise ValueError("service_endpoint_observation_mismatch")
    if config["network"]["posture"] == "private-required":
        vnet = environment["properties"]["vnetConfiguration"]
        if (vnet.get("internal") is not True or not vnet["infrastructureSubnetId"].startswith(
                config["network"]["vnet_id"] + "/subnets/")
                or observations["foundry_agent_subnet_id"] != config["network"]["foundry_injection_subnet_id"]):
            raise ValueError("private_foundry_reachability_required")
        links = observations.get("private_dns_links", [])
        for key in ("blob_dns_zone_id", "cosmos_dns_zone_id", "keyvault_dns_zone_id"):
            if not any(
                    link.get("id", "").startswith(config["network"][key] + "/virtualNetworkLinks/")
                    and link.get("properties", {}).get("virtualNetwork", {}).get("id") == config["network"]["vnet_id"]
                    and link["properties"].get("provisioningState") == "Succeeded" for link in links):
                raise ValueError("private_dns_vnet_link_observations_required")
    for key in ("gateway_client", "gateway_principal", "downstream_client", "downstream_principal", "key_id"):
        if observations["foundation"][key] != bindings[key]:
            raise ValueError("foundation_identity_mismatch")
    applications = {app["appId"]: app for app in observations["applications"]}
    assignments = observations["app_role_assignments"]
    for service, subjects in (
        ("control_plane", [bindings["agent_principal"], bindings["gateway_principal"]]),
        ("gateway", [bindings["agent_principal"]]),
    ):
        app_id = config[f"{service}_app_id"]
        app = applications[app_id]
        if f"api://{app_id}" not in app["identifierUris"] or app["api"]["requestedAccessTokenVersion"] != 2:
            raise ValueError("entra_audience_configuration_required")
        roles = {role["value"]: role["id"] for role in app["appRoles"]
                 if role["isEnabled"] and "Application" in role["allowedMemberTypes"]}
        role = roles.get("Governance.Workload")
        if not role or not all(any(
                a["principalId"] == subject and a["resourceId"] == app["servicePrincipalId"]
                and a["appRoleId"] == role for a in assignments) for subject in subjects):
            raise ValueError("entra_workload_app_role_assignment_required")
    downstream = observations.get("downstream_authorizations", [])
    if not downstream or any(
            grant.get("principal_id") != bindings["downstream_principal"]
            or not re.fullmatch(UUID, grant.get("role_id", ""))
            or not grant.get("scope", "").startswith("api://") for grant in downstream):
        raise ValueError("downstream_identity_authorization_required")


def foundation(project, document, *, configuration=None):
    if validate_contract(document)["governance"]["mode"] == "off":
        return {"status": "off"}
    validate_infrastructure(configuration)
    project = Path(project)
    compose_infra(project)
    path = project / "infra/main.parameters.json"
    payload = json.loads(path.read_text()) if path.exists() else {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0", "parameters": {}}
    payload["parameters"].update({
        "governanceConfig": {"value": configuration}, "governancePhase": {"value": "foundation"}})
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return {"status": "foundation-generated-not-provisioned"}


def copy_sources(target):
    """Explicit portable namespaces, including the shared validator's real imports."""
    for source, namespace in (
        (GOVERN / "references/runtime", "runtime"),
        (GOVERN / "references/control-plane", "govern_control_plane"),
    ):
        destination = target / namespace
        destination.mkdir()
        for path in source.iterdir():
            if path.suffix in (".py", ".json"):
                shutil.copyfile(path, destination / path.name)
    for name in ("govern_bundle", "govern_canonical", "govern_shared", "skills", "skills/_shared"):
        (target / name).mkdir(parents=True, exist_ok=True)
        (target / name / "__init__.py").write_text("")
    for source, destination in (
        (GOVERN / "scripts/policy_bundle.py", "govern_bundle/policy_bundle.py"),
        (CATALOG / "skills/threadlight-governed-actions/scripts/canonical.py", "govern_canonical/canonical.py"),
        (CATALOG / "skills/_shared/governance-upstream-pin.json", "govern_shared/governance-upstream-pin.json"),
        (CATALOG / "skills/_shared/governance.py", "skills/_shared/governance.py"),
        (CATALOG / "skills/_shared/manifest.py", "skills/_shared/manifest.py"),
    ):
        shutil.copyfile(source, target / destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("foundation", "generate", "agent-image", "stage-gateway", "bind"):
        command = commands.add_parser(name)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--contract", type=Path, required=True)
        command.add_argument("--configuration", type=Path)
    args = parser.parse_args()
    result = {"foundation": foundation, "generate": generate,
              "agent-image": agent_image, "stage-gateway": stage_gateway, "bind": bind}[args.command](
        args.project, json.loads(args.contract.read_text()),
        configuration=json.loads(args.configuration.read_text()) if args.configuration else None)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
