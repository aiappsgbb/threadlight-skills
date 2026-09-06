#!/usr/bin/env python3
"""Package selected governance, then bind independently built deployment images."""
from __future__ import annotations

import argparse
from copy import deepcopy
from functools import wraps
import importlib
import importlib.util
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid
import tomllib

CATALOG = Path(__file__).resolve().parents[4]
REFERENCE = Path(__file__).resolve().parent
GOVERN = CATALOG / "skills/threadlight-govern"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
RESOURCE = rf"/subscriptions/{UUID}/resourceGroups/[^/]+/providers/"


def export_local_validation(project):
    """Export real catalog tooling/ownership; no installed or live evidence is copied."""
    project = Path(project)
    tools = project / ".governance-tools"
    for skill in ("_shared", "threadlight-govern", "threadlight-deploy",
                  "threadlight-governed-actions", "threadlight-safe-check"):
        shutil.copytree(CATALOG / "skills" / skill, tools / "skills" / skill,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                                     "*.egg-info", "build", "dist"))
    # Explicit package wins over the generated application's smaller vendored
    # skills namespace during local validation; deployment source is unchanged.
    (tools / "skills/__init__.py").write_text("")
    (tools / "scripts/ci").mkdir(parents=True)
    for name in ("run-governance-pin-tests.py", "governance_ctk.py"):
        shutil.copyfile(CATALOG / "scripts/ci" / name, tools / "scripts/ci" / name)
    for name in ("Cargo.toml", "Cargo.lock", "src/main.rs"):
        destination = tools / "scripts/ci/ctk-oracle" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CATALOG / "scripts/ci/ctk-oracle" / name, destination)
    (tools / "source-manifest.json").write_text(json.dumps({
        "files": {p.relative_to(tools).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(tools.rglob("*")) if p.is_file()},
        "scope": "exported local validation sources only; not live evidence",
    }, indent=2) + "\n")
    (project / ".github/workflows").mkdir(parents=True)
    shutil.copyfile(CATALOG / ".github/CODEOWNERS", project / ".github/CODEOWNERS")
    shutil.copyfile(REFERENCE / "native-local.yml", project / ".github/workflows/native-local.yml")
    with (project / ".gitignore").open("a") as ignored:
        ignored.write("\n.governance-validation/\n__pycache__/\n*.pyc\n")
    (project / "governance/change-plane.json").write_text(json.dumps({
        "scope": "standalone", "workflows": [".github/workflows/native-local.yml"],
        "ownership": "copied from catalog CODEOWNERS; operator review required; not live verification",
    }, indent=2) + "\n")


def project_transaction(operation):
    """Prepare in a same-filesystem shadow; publish only changed, owned paths.

    This is cooperative per-file atomicity, not a crash-atomic project swap.
    A rollback never removes the project or an unrelated writer's files.
    """
    @wraps(operation)
    def transactional(project, document, *, configuration=None):
        if validate_contract(document)["governance"]["mode"] == "off":
            return {"status": "off"}
        if operation.__name__ in ("generate", "package_native") and configuration is None:
            raise ValueError("configuration_required")
        if operation.__name__ == "bind":
            validate_images((configuration or {}).get("images", {}))
        bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
        checked = bundle_api.checked_path
        project = checked(Path(project))
        import yaml
        seeds = {Path("infra")}
        if operation.__name__ != "foundation":
            seeds.update(Path(name) for name in (
                "azure.yaml", "agent.yaml", ".threadlight/governance-package.json",
                ".threadlight/governance-deployment.json", "src/govern-control-plane", "src/govern-gateway",
                "src/governance-control-plane"))
            azure = yaml.safe_load(checked(project / "azure.yaml").read_text())
            config = configuration or {}
            if operation.__name__ not in ("generate", "package_native"):
                package = json.loads(checked(project / ".threadlight/governance-package.json").read_text())
                config = package["configuration"]
            relative = Path(azure["services"][config["agent_service"]]["project"])
            if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
                raise ValueError("agent_source_must_be_inside_project")
            seeds.add(relative)
        seeds = sorted(p for p in seeds if not any(other in p.parents for other in seeds))

        def inventory(root):
            result = {}
            for seed in seeds:
                path = checked(root / seed)
                paths = [path, *path.rglob("*")] if path.is_dir() else [path]
                for item in paths:
                    checked(item)
                    if not item.exists():
                        continue
                    info = item.stat()
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        raise ValueError("regular_project_paths_required")
                    result[item.relative_to(root)] = (
                        stat.S_IMODE(info.st_mode), None if item.is_dir() else item.read_bytes())
            return result

        before = inventory(project)
        staging = project / f".governance-transaction-{uuid.uuid4().hex}"
        staging.mkdir(mode=0o700)
        shadow, backups = staging / "project", staging / "backups"
        shadow.mkdir()
        backups.mkdir()
        preserve_recovery = False
        try:
            for seed in seeds:
                source, destination = project / seed, shadow / seed
                if source.is_dir():
                    shutil.copytree(source, destination)
                elif source.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            result = operation(shadow, document, configuration=configuration)
            after = inventory(shadow)
            if inventory(project) != before:
                raise ValueError("project_changed_during_generation")
            changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
            created_dirs, removed_dirs, written = [], [], []

            def mkdir(path):
                if not path.exists():
                    mkdir(path.parent)
                    checked(path).mkdir()
                    created_dirs.append(path)

            try:
                for relative in changed:
                    destination = checked(project / relative)
                    old, new = before.get(relative), after.get(relative)
                    if old is not None and old[1] is not None:
                        backup = backups / relative
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(destination, backup)
                    if new is not None and new[1] is None:
                        if old is not None and old[1] is not None:
                            raise ValueError("generated_path_type_changed")
                        mkdir(destination)
                    elif new is not None:
                        if old is not None and old[1] is None:
                            raise ValueError("generated_path_type_changed")
                        mkdir(destination.parent)
                        (shadow / relative).replace(destination)
                        written.append(relative)
                    elif old[1] is not None:
                        destination.unlink()
                        written.append(relative)
                # Stage replacement may remove old bundle subdirectories; never rmtree user paths.
                for relative in sorted(set(before) - set(after), key=lambda p: len(p.parts), reverse=True):
                    if before[relative][1] is None:
                        checked(project / relative).rmdir()
                        removed_dirs.append(relative)
            except BaseException:
                for relative in reversed(removed_dirs):
                    try:
                        checked(project / relative).mkdir(mode=before[relative][0])
                    except (OSError, ValueError):
                        preserve_recovery = True
                for relative in reversed(written):
                    try:
                        destination = checked(project / relative)
                        new = after.get(relative)
                        expected = None if new is None else new[1]
                        actual = destination.read_bytes() if destination.is_file() else None
                        if actual != expected or destination.is_dir():
                            preserve_recovery = True
                            continue
                        if relative in before:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            (backups / relative).replace(destination)
                        else:
                            destination.unlink()
                    except (OSError, ValueError):
                        preserve_recovery = True
                for path in reversed(created_dirs):
                    try:
                        if path.exists() and not any(path.iterdir()):
                            checked(path).rmdir()
                    except (OSError, ValueError):
                        preserve_recovery = True
                if preserve_recovery:
                    raise OSError(f"generation_rollback_conflict_recovery:{staging.name}") from None
                raise
            return result
        finally:
            if not preserve_recovery:
                shutil.rmtree(staging)
    return transactional


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


def validate_environment(config):
    if config.get("environment") not in ("development", "staging", "preproduction", "production"):
        raise ValueError("explicit_governance_environment_required")
    return config["environment"]


def validate_probe_observability(config):
    if "probe_observability" not in config:
        return None
    from govern_control_plane.probes import ProbeOptIn
    from govern_control_plane.models import canonical, parse
    option = parse(ProbeOptIn, canonical(config["probe_observability"]))
    if config.get("environment") not in ("staging", "preproduction"):
        raise ValueError("staging_probe_only")
    return option.model_dump(mode="json")


def portable_configuration(config, contract, framework):
    portable = {k: v for k, v in config.items() if k not in (
        "bundle_path", "signed_envelope", "network", "agent_service")}
    portable["contract"] = deepcopy(contract)
    normalized = validate_contract(contract)
    for source, target in zip(normalized["tools"], portable["contract"]["tools"], strict=True):
        target["requires"] = source["requires"]
    for source, target in zip(normalized["governance"]["lifecycle_bindings"],
                              portable["contract"]["governance"]["lifecycle_bindings"], strict=True):
        target["requires"] = source["requires"]
    if framework == "microsoft-agent-framework":
        portable["audit_delivery"] = "remote-ack"
    else:
        portable.pop("policy_digest", None)
    return portable


def validate_policy(bundle, signed, config):
    from datetime import datetime, timezone
    metadata = json.loads((bundle.root / "bundle-metadata.json").read_text())
    envelope = signed.envelope
    if (envelope.tenant_id != config["tenant_id"] or envelope.key_id != config["key_id"]
            or envelope.policy_id != config["policy_id"] or envelope.version != config["policy_version"]
            or envelope.content_digest != bundle.bundle_digest
            or envelope.expires_at <= datetime.now(timezone.utc)
            or metadata["policy_id"] != envelope.policy_id or metadata["version"] != envelope.version):
        raise ValueError("signed_policy_identity_or_expiry_invalid")


def validate_bundle_contract(bundle, document, config, registry=None):
    """Check control semantics, not just action-name coverage or a valid content hash."""
    import yaml
    from govern_control_plane.models import Identifier, canonical, parse
    document = validate_contract(document)
    environment = validate_environment(config)
    roles = parse(list[Identifier], canonical(config["approver_roles"]))
    if len(roles) != len(set(roles)) or len(roles) > 16:
        raise ValueError("invalid_approval_roles")

    def declarations(path):
        raw = yaml.safe_load(path.read_text())
        merged = {"policies": {}, "intervention_points": {}}
        for parent in raw.get("extends", []):
            inherited = declarations(path.parent / parent)
            for key in merged:
                merged[key].update(inherited[key])
        for key in merged:
            merged[key].update(raw.get(key, {}))
        return merged

    declared = declarations(bundle.manifest_path)
    selected = [tool for tool in document["tools"] if tool["policy_binding"] is not None]
    lifecycle = document["governance"]["lifecycle_bindings"]
    ghcp = document["framework"] == "github-copilot-sdk"
    if ghcp and (lifecycle or document["governance"]["environment_modes"][environment] != "enforce"):
        raise ValueError("ghcp_contract_environment_or_lifecycle_unsupported")
    targets = {
        "pre_tool_call": "$.tool_call.args", "post_tool_call": "$.tool_result",
        "input": "$.input", "output": "$.output", "pre_model_call": "$.messages",
        "post_model_call": "$.response", "startup": "$.agent_init", "shutdown": "$.summary",
    }
    for binding in selected + lifecycle:
        points = binding.get("intervention_points", [binding.get("lifecycle_point")])
        requirements = set(binding["requires"])
        if "operator-review" in requirements:
            raise ValueError("operator_review_contract_unsupported")
        if not ghcp and requirements & {"idempotency", "idempotency-or-transaction"}:
            raise ValueError("maf_transaction_contract_unsupported")
        if ghcp and (not set(points) <= {"pre_tool_call", "post_tool_call"}
                     or "pre_tool_call" not in points):
            raise ValueError("ghcp_intervention_contract_unsupported")
        if requirements & {"approval", "human-approval-record"} and not roles:
            raise ValueError("contract_approval_roles_required")
        if requirements & {"output", "output-mediation"} and not set(points) & {
                "post_tool_call", "output", "post_model_call"}:
            raise ValueError("output_mediation_contract_unsupported")
        for point in points:
            native = {"startup": "agent_startup", "shutdown": "agent_shutdown"}.get(point, point)
            actual = declared["intervention_points"].get(native, {})
            policy_id = binding["policy_binding"]
            if (policy_id not in declared["policies"] or actual.get("policy", {}).get("id") != policy_id
                    or actual.get("policy_target") != targets[point]):
                raise ValueError("selected_policy_binding_mismatch")
    if registry is None:
        return
    if registry.deployment.environment != environment:
        raise ValueError("signed_registry_environment_mismatch")
    if {a.name for a in registry.actions} != {t["id"] for t in selected}:
        raise ValueError("signed_registry_selection_mismatch")
    actions = {action.name: action for action in registry.actions}
    for tool in selected:
        action = actions[tool["id"]]
        post = tool["policy_binding"] if "post_tool_call" in tool["intervention_points"] else None
        if action.policy_binding != tool["policy_binding"] or action.post_policy_binding != post:
            raise ValueError("signed_registry_contract_binding_mismatch")
        # Nonempty Task9 roles mandate approval even when Rego says allow.
        # Each action retains its own narrower roles; a union is not an intent.
        if ((set(tool["requires"]) & {"approval", "human-approval-record"} and not action.approval_roles)
                or not set(action.approval_roles) <= set(roles)):
            raise ValueError("signed_registry_contract_approval_mismatch")


def frozen_configuration(project, package):
    import yaml
    packaged = package["configuration"]
    validate_environment(packaged)
    azure = yaml.safe_load((project / "azure.yaml").read_text())
    relative = Path(azure["services"][packaged["agent_service"]]["project"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("agent_source_must_be_inside_project")
    agent = project / relative
    frozen = json.loads((agent / "governance-config.json").read_text())
    if frozen != portable_configuration(packaged, package["contract"], package["framework"]):
        raise ValueError("frozen_agent_configuration_changed")
    return agent, frozen


def validate_gateway_policy(bundle, signed, config, document, agent_image, bindings=None):
    """Shared stage/bind/static association checks; signature verification stays live."""
    from govern_control_plane.models import parse
    from govern_gateway.dispatcher import Registry
    registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
    validate_policy(bundle, signed, config)
    validate_bundle_contract(bundle, document, config, registry)
    if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9./_-]+@sha256:[0-9a-f]{64}", agent_image or ""):
        raise ValueError("deployment_images_require_built_digests")
    if (registry.native_policy_digest is not None
            or registry.deployment.image_digest != agent_image.split("@")[1]
            or registry.deployment.agent_id != config["agent_id"]
            or registry.tenant_id != config["tenant_id"]
            or registry.gateway_url != config["gateway_url"]):
        raise ValueError("signed_registry_deployment_mismatch")
    if validate_probe_observability(config) and (
            not any(a.probe_safe for a in registry.actions)
            or registry.deployment.subscription != config["subscription"]
            or registry.deployment.resource_group != config["resource_group"]):
        raise ValueError("signed_probe_deployment_mismatch")
    if bindings is None:
        return registry
    if (registry.deployment.agent_version != bindings["agent_version"]
            or bindings["policy_digest"] != bundle.bundle_digest
            or any(bindings[key] != config[key] for key in ("policy_id", "policy_version", "key_id"))):
        raise ValueError("signed_registry_deployment_mismatch")
    if not all({a.endpoint, a.outcome_endpoint} <= set(bindings["allowed_endpoints"]) for a in registry.actions):
        raise ValueError("downstream_allowlist_mismatch")
    from govern_control_plane.app import AzureConfiguration
    from govern_control_plane.models import canonical
    from govern_gateway.server import Configuration
    control = parse(AzureConfiguration, canonical(bindings["control_config"]))
    gateway = parse(Configuration, canonical(bindings["gateway_config"]))
    for service, settings in (("control_plane", control), ("gateway", gateway)):
        if (settings.tenant_id != config["tenant_id"] or settings.key_id != config["key_id"]
                or settings.approver_roles != config["approver_roles"]
                or config[service + "_scope"] != f"api://{settings.audience}/.default"):
            raise ValueError("service_auth_binding_mismatch")
    if (gateway.policy_digest != bundle.bundle_digest
            or gateway.policy_id != config["policy_id"] or gateway.policy_version != config["policy_version"]
            or gateway.gateway_url != config["gateway_url"]
            or gateway.control_plane_url != config["control_plane_url"]
            or gateway.control_plane_scope != config["control_plane_scope"]
            or gateway.service_client_id != bindings["gateway_client"]
            or gateway.service_principal != bindings["gateway_principal"]
            or gateway.service_agent_id != config["agent_id"]
            or gateway.downstream_client_id != bindings["downstream_client"]
            or gateway.allowed_endpoints != bindings["allowed_endpoints"]):
        raise ValueError("service_auth_binding_mismatch")
    if len({bindings[key] for key in ("agent_principal", "gateway_principal", "downstream_principal")}) != 3:
        raise ValueError("distinct_workload_identities_required")
    for settings, subject, client in (
        (control, bindings["agent_principal"], bindings["agent_client_id"]),
        (control, bindings["gateway_principal"], bindings["gateway_client"]),
        (gateway, bindings["agent_principal"], bindings["agent_client_id"]),
    ):
        workload = settings.workloads.get(subject)
        if (workload is None or workload.client_id != client or workload.agent_id != config["agent_id"]
                or config["policy_id"] not in workload.policies):
            raise ValueError("workload_allowlist_mismatch")
    if any(bindings["agent_principal"] not in action.workloads for action in registry.actions):
        raise ValueError("signed_registry_workload_mismatch")
    return registry


@project_transaction
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
    config = deepcopy(configuration)
    validate_environment(config)
    probe_option = validate_probe_observability(config)
    if probe_option:
        from govern_control_plane.models import Identifier, ObjectId, canonical
        parse(ObjectId, canonical(config["subscription"]))
        parse(Identifier, canonical(config["resource_group"]))
        selected_probe = next((t for t in document["tools"] if t["id"] == "governance_probe_noop"), None)
        if (selected_probe is None or selected_probe["policy_binding"] is None
                or selected_probe["intervention_points"] != ["pre_tool_call"]
                or document["governance"]["environment_modes"][config["environment"]] != "enforce"):
            raise ValueError("explicit_enforced_probe_binding_required")
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
    validate_policy(bundle, signed, config)
    registry = None
    if document["framework"] == "github-copilot-sdk" and (bundle.root / "gateway-registry.json").exists():
        from govern_gateway.dispatcher import Registry
        registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
    validate_bundle_contract(bundle, source_contract, config, registry)
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
        configure_entrypoint(agent, target, template)
        if document["framework"] == "microsoft-agent-framework":
            shutil.copyfile(REFERENCE / "audit_delivery.py", target / "audit_delivery.py")
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
        portable = portable_configuration(config, source_contract, document["framework"])
        (target / "governance-config.json").write_text(json.dumps(portable, indent=2) + "\n")
        vendor_control_plane(target)
        if probe_option and document["framework"] == "microsoft-agent-framework":
            vendor_gateway(target)
        write_dockerfile(target, agent=True,
                         gateway=bool(probe_option and document["framework"] == "microsoft-agent-framework"))
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
            "signed_policy": signed.model_dump(mode="json"),
            "status": "packaged-not-deployed",
        }, indent=2) + "\n")
    finally:
        shutil.rmtree(staging)
    return {"status": "packaged-not-deployed", "signature": "runtime-keyvault-verification-required",
            "policy_digest": bundle.bundle_digest}


def configure_entrypoint(agent, target, template):
    """Keep the canonical wrapper and its actual served factory in one module.

    Arbitrary application entrypoints still use the existing generic generator;
    only the exact reviewed native wrapper opts into the named host module.
    """
    wrapper = REFERENCE / "maf-entrypoint.py"
    existing = agent / "container.py"
    if (template == "maf-container.py" and existing.is_file()
            and existing.read_bytes() == wrapper.read_bytes()):
        shutil.copyfile(existing, target / "container.py")
        shutil.copyfile(REFERENCE / template, target / "governance_host.py")
    else:
        shutil.copyfile(REFERENCE / template, target / "container.py")


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


def dockerfile_text(*, agent=False, gateway=False, opa_pin=None):
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
        pin = opa_pin or json.loads((CATALOG / "skills/_shared/governance-upstream-pin.json").read_text())["opa"]
        text += (
            f"ADD https://openpolicyagent.org/downloads/v{pin['version']}/opa_linux_amd64_static /app/opa\n"
            f"RUN echo '{pin['linux_amd64_static_sha256']}  /app/opa' | sha256sum -c - && chmod 555 /app/opa\n"
            "ENV ACS_OPA_PATH=/app/opa\n")
    if not agent:
        text += "COPY . ./\n"
    text += 'EXPOSE 8088\nCMD ["python", "container.py"]\n' if agent else (
        'EXPOSE 8000\nCMD ["python", "service_entry.py"]\n')
    return text


def write_dockerfile(target, *, agent=False, gateway=False):
    text = dockerfile_text(agent=agent, gateway=gateway)
    (target / "Dockerfile").write_text(text)
    (target / ".dockerignore").write_text(
        ".git\n.azure\n.threadlight\n.env\n.env.*\n**/__pycache__\n**/.venv\n"
        "deployment*.json\nagent.yaml\nagent.manifest.yaml\n*.pem\n*.key\nrun/\naudit/\n")


def agent_environment(config, image="${TL_GOV_AGENT_IMAGE}", spool_directory="${TL_GOV_SPOOL_DIR}"):
    """The closed host wiring emitted by Task10 and checked by its consumers."""
    return {
        "GOV_CONTROL_PLANE_URL": config["control_plane_url"],
        "GOVERNED_TOOL_GATEWAY_URL": config["gateway_url"],
        "TL_GOV_IMAGE_DIGEST": "${TL_GOV_IMAGE_DIGEST}" if image == "${TL_GOV_AGENT_IMAGE}" else image.split("@")[1],
        "TL_GOV_SPOOL_DIR": spool_directory,
    }


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
    values = agent_environment(config)
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


@project_transaction
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
        raise ValueError("host_owned_spool_directory_required")
    project = Path(project)
    package = json.loads((project / ".threadlight/governance-package.json").read_text())
    if document != package["contract"]:
        raise ValueError("packaged_contract_changed")
    frozen_configuration(project, package)
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


@project_transaction
def stage_gateway(project, document, *, configuration=None):
    """Stage the final signed registry after the agent image exists, before gateway build."""
    if validate_contract(document)["governance"]["mode"] == "off":
        return {"status": "off"}
    from govern_control_plane.models import SignedBundle, canonical, parse
    config = configuration or {}
    project = Path(project)
    package = json.loads((project / ".threadlight/governance-package.json").read_text())
    if package["contract"] != document or package["framework"] != "github-copilot-sdk":
        raise ValueError("selected_gateway_contract_required")
    bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    bundle = bundle_api.verify_bundle(Path(config["gateway_bundle"]), expected_digest=config["policy_digest"])
    bundle_api.validate_native_manifest(bundle.root)
    signed = parse(SignedBundle, Path(config["signed_envelope"]).read_bytes())
    expected = package["configuration"]
    frozen_configuration(project, package)
    validate_gateway_policy(bundle, signed, expected, document, config.get("agent_image"))
    target = project / "src/govern-gateway/policy"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(bundle.root, target)
    # Keep the envelope outside the native digest-covered bundle, unchanged.
    (project / "src/govern-gateway/policy-envelope.json").write_bytes(canonical(signed))
    digest = tree_digest(project / "src/govern-gateway")
    return {"status": "staged-rebuild-gateway-required", "gateway_source_digest": digest,
            "policy_digest": bundle.bundle_digest}


@project_transaction
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
    probe_option = validate_probe_observability(packaged)
    if validate_probe_observability(infrastructure) != probe_option:
        raise ValueError("probe_opt_in_changed")
    # Entra v2 aud is the canonical application UUID, NOT the api:// scope URI.
    # Reject noncanonical/cross-service inputs rather than normalizing token audiences
    # differently from Task8's strict JWT verifier.
    for service in ("control_plane", "gateway"):
        if (packaged[f"{service}_scope"] != f"api://{infrastructure[f'{service}_app_id']}/.default"
                or packaged[f"{service}_url"] != config["observations"][f"{service}_url"]):
            raise ValueError("service_auth_binding_mismatch")
    if (infrastructure["runtime"] != package["framework"]
            or infrastructure["tenant_id"] != packaged["tenant_id"]
            or bindings["key_id"] != packaged["key_id"]
            or infrastructure["agent_id"] != packaged["agent_id"]
            or infrastructure["network"] != packaged["network"]):
        raise ValueError("deployment_trust_changed")
    agent, frozen = frozen_configuration(project, package)
    if infrastructure["environment"] != frozen["environment"]:
        raise ValueError("frozen_deployment_environment_mismatch")
    if (bindings["policy_id"] != frozen["policy_id"]
            or bindings["policy_version"] != frozen["policy_version"]
            or infrastructure["approver_roles"] != frozen["approver_roles"]):
        raise ValueError("frozen_policy_or_approval_configuration_mismatch")
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
        "control_plane_scope": packaged["control_plane_scope"],
        "service_client_id": bindings["gateway_client"], "service_principal": bindings["gateway_principal"],
        "service_agent_id": infrastructure["agent_id"], "downstream_client_id": bindings["downstream_client"],
        "cosmos_url": foundation_values["cosmos_url"], "cosmos_database": "governance",
        "cosmos_container": "gateway-idempotency", "bundle_path": "/app/policy",
        "policy_id": frozen["policy_id"], "policy_version": frozen["policy_version"],
        "policy_digest": bindings["policy_digest"], "allowed_endpoints": bindings["allowed_endpoints"],
    }
    if probe_option:
        controllers = bindings["probe_controllers"]
        control["probe_controllers"] = controllers
        if package["framework"] == "github-copilot-sdk":
            gateway.update(probe_enabled=True, probe_container="probe-gateway", probe_controllers=controllers)
    bindings["control_config"] = parse(AzureConfiguration, canonical(control)).model_dump(mode="json")
    bindings["gateway_config"] = parse(Configuration, canonical(gateway)).model_dump(mode="json")
    from govern_control_plane.models import SignedBundle
    bundle_api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    if package["framework"] == "microsoft-agent-framework":
        if probe_option and not all(config.get(key) for key in (
                "probe_runtime_configuration", "probe_bundle", "probe_signed_envelope")):
            raise ValueError("native_probe_binding_required")
        if bindings["policy_digest"] != packaged["policy_digest"]:
            raise ValueError("embedded_local_policy_digest_changed")
        bundle = bundle_api.verify_bundle(agent / "policy", expected_digest=bindings["policy_digest"])
        signed = parse(SignedBundle, (agent / "policy-envelope.json").read_bytes())
        validate_policy(bundle, signed, frozen)
        if signed.model_dump(mode="json") != package["signed_policy"]:
            raise ValueError("frozen_signed_policy_changed")
        bundle_api.validate_native_manifest(bundle.root)
        validate_bundle_contract(bundle, document, frozen)
        if probe_option:
            probe_declaration = native_probe_binding(config, bindings, packaged, images, document)
            bindings["native_probe_config"] = deepcopy(config["probe_runtime_configuration"])
    else:
        bundle = bundle_api.verify_bundle(Path(config["gateway_bundle"]), expected_digest=bindings["policy_digest"])
        bundle_api.validate_native_manifest(bundle.root)
        signed = parse(SignedBundle, (project / "src/govern-gateway/policy-envelope.json").read_bytes())
        validate_gateway_policy(bundle, signed, packaged, document, images["agent"], bindings)
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
    if probe_option:
        deployment["probe_observability"] = (
            probe_declaration if package["framework"] == "microsoft-agent-framework" else {
                "status": "declared-unverified", "registry_digest": bundle.bundle_digest,
                "policy_digest": bindings["policy_digest"], "fixture_installed": False})
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
    expected_environment = {
        "GOV_CONTROL_PLANE_URL": packaged["control_plane_url"],
        "GOVERNED_TOOL_GATEWAY_URL": packaged["gateway_url"],
        "TL_GOV_IMAGE_DIGEST": images["agent"].split("@")[1],
    }
    environment = dict(service.get("env", {}))
    for entry in service.get("environmentVariables", []):
        if entry["name"] in environment and environment[entry["name"]] != entry["value"]:
            raise ValueError("service_auth_binding_mismatch")
        environment[entry["name"]] = entry["value"]
    if any(environment.get(key) != value for key, value in expected_environment.items()):
        raise ValueError("service_auth_binding_mismatch")
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


def native_probe_binding(config, bindings, packaged, images, document):
    from govern_control_plane.models import SignedBundle, canonical, parse
    from govern_gateway.dispatcher import Registry
    from govern_gateway.probe_runtime import ProbeConfiguration
    runtime = parse(ProbeConfiguration, canonical(config["probe_runtime_configuration"]))
    api = importlib.import_module("skills.threadlight-govern.scripts.policy_bundle")
    bundle = api.verify_bundle(Path(config["probe_bundle"]), expected_digest=runtime.policy_digest)
    signed = parse(SignedBundle, Path(config["probe_signed_envelope"]).read_bytes())
    validate_policy(bundle, signed, runtime.model_dump(mode="json"))
    registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
    expected = {
        "agent_id": packaged["agent_id"], "agent_version": bindings["agent_version"],
        "image_digest": images["agent"].split("@")[1], "environment": packaged["environment"],
        "subscription": packaged["subscription"], "resource_group": packaged["resource_group"]}
    if (runtime.producer != "native" or runtime.tenant_id != packaged["tenant_id"]
            or runtime.key_id != packaged["key_id"] or registry.tenant_id != packaged["tenant_id"]
            or runtime.service_client_id != bindings["agent_client_id"]
            or runtime.downstream_client_id != bindings["downstream_client"]
            or runtime.cosmos_url != config["observations"]["foundation"]["cosmos_url"]
            or runtime.cosmos_database != "governance"
            or runtime.expected_deployment.model_dump(mode="json") != expected
            or registry.deployment.model_dump(mode="json") != expected
            or runtime.gateway_url != registry.gateway_url
            or registry.native_policy_digest != bindings["policy_digest"]
            or {k: v.model_dump() for k, v in runtime.probe_controllers.items()} != bindings["probe_controllers"]):
        raise ValueError("native_probe_binding_mismatch")
    action = registry.actions[0]
    workload = runtime.workloads.get(bindings["agent_principal"])
    if (len(registry.actions) != 1 or not action.probe_safe
            or action.workloads != [bindings["agent_principal"]]
            or workload is None or workload.client_id != bindings["agent_client_id"]
            or workload.agent_id != packaged["agent_id"]
            or set(runtime.allowed_endpoints) != {action.endpoint, action.outcome_endpoint}
            or any(not set(grant.subjects) <= set(action.workloads)
                   or grant.actions != [action.name] for grant in runtime.probe_controllers.values())):
        raise ValueError("native_probe_scope_mismatch")
    selected = deepcopy(document)
    selected["tools"] = [t for t in selected["tools"] if t["id"] == action.name]
    selected["governance"]["lifecycle_bindings"] = []
    validate_bundle_contract(bundle, selected, packaged, registry)
    return {"status": "declared-unverified", "registry_digest": bundle.bundle_digest,
            "policy_digest": bindings["policy_digest"], "fixture_installed": False}


def validate_infrastructure(config):
    validate_environment(config)
    validate_probe_observability(config)
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


@project_transaction
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


@project_transaction
def package_native(project, document, *, configuration=None):
    """Source-only Task10 closure for an existing hub; never bind or provision.

    Unlike generate/bind this deliberately needs no invented deployment identity,
    signing envelope, image digest, or replacement network. The resulting host
    still requires all of those relevant runtime inputs before selected effects.
    """
    import yaml
    document = validate_contract(document)
    if document["framework"] != "microsoft-agent-framework":
        raise ValueError("native_source_requires_maf")
    if any(t["enforcement_path"] == "governed-tool-gateway" for t in document["tools"]):
        raise ValueError("native_source_does_not_mediate_gateway_tools")
    config = configuration or {}
    probe = validate_probe_observability(config)
    azure = yaml.safe_load((project / "azure.yaml").read_text())
    service = azure["services"][config["agent_service"]]
    if service["host"] != "azure.ai.agent":
        raise ValueError("explicit_hosted_agent_required")
    agent = project / service["project"]
    if not (agent / "governance_application.py").is_file() or not (agent / "container.py").is_file():
        raise ValueError("native_application_and_entrypoint_required")
    target = project / (".native-source-" + uuid.uuid4().hex)
    target.mkdir()
    try:
        copy_sources(target)
        vendor_control_plane(target)
        if probe:
            vendor_gateway(target)
        shutil.copyfile(REFERENCE / "maf-container.py", target / "governance_host.py")
        shutil.copyfile(REFERENCE / "audit_delivery.py", target / "audit_delivery.py")
        (target / "pyproject.toml").write_text(merge_dependencies(
            (agent / "pyproject.toml").read_text(), (REFERENCE / "pyproject-maf.toml").read_text()))
        write_dockerfile(target, agent=True, gateway=bool(probe))
        for path in target.iterdir():
            if path.name != "skills" and path.is_dir() and (agent / path.name).exists():
                raise ValueError("native_generated_namespace_already_exists")
            if path.is_dir():
                shutil.copytree(path, agent / path.name, dirs_exist_ok=path.name == "skills")
            else:
                shutil.copyfile(path, agent / path.name)
        control = project / "src/governance-control-plane"
        control.mkdir()
        vendor_control_plane(control)
        shutil.copyfile(REFERENCE / "service_entry.py", control / "service_entry.py")
        write_dockerfile(control)
    finally:
        shutil.rmtree(target)
    return {"status": "source-packaged-unverified", "signature": "not-configured",
            "deployment": "not-bound", "probe_fixture_installed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("foundation", "generate", "agent-image", "stage-gateway", "bind", "package-native"):
        command = commands.add_parser(name)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--contract", type=Path, required=True)
        command.add_argument("--configuration", type=Path)
    args = parser.parse_args()
    result = {"foundation": foundation, "generate": generate,
              "package-native": package_native,
              "agent-image": agent_image, "stage-gateway": stage_gateway, "bind": bind}[args.command](
        args.project, json.loads(args.contract.read_text()),
        configuration=json.loads(args.configuration.read_text()) if args.configuration else None)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
