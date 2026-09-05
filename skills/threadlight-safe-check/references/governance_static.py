"""Read-only packaging gate. Declarations never become live enforcement evidence."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path


def enabled(document):
    value = document.get("governance")
    return "governance" in document and (not isinstance(value, dict) or value.get("mode") != "off")


def generator():
    try:
        return importlib.import_module("govern_deployment.generate")
    except ModuleNotFoundError:
        return importlib.import_module("skills.threadlight-deploy.references.governance.generate")


def contained(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("project-relative-path-required")
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink-not-allowed")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path-outside-project")
    return path


def read(path):
    from govern_control_plane.models import strict_json
    with path.open("rb") as stream:
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError("configuration-too-large")
    return strict_json(raw)


def bindings(contract, policy_digest=None, environment="preproduction"):
    result = []
    for item in contract["tools"] + [
        {**b, "id": "lifecycle:" + b["lifecycle_point"], "intervention_points": [b["lifecycle_point"]]}
        for b in contract["governance"]["lifecycle_bindings"]
    ]:
        bound = item["enforcement_path"] != "none"
        result.append({
            "binding_id": item["id"], "tool_id": item["id"],
            "enforcement_path": item["enforcement_path"],
            "intervention_points": item["intervention_points"],
            "mode": contract["governance"]["environment_modes"][environment],
            "safe_principles": item["safe_principles"], "status": "unverified" if bound else "unbound",
            "policy_digest": policy_digest, "probe_ids": [], "evidence_refs": [],
        })
    return result


def host_environment(project, agent, service, config):
    import yaml
    from skills._shared.governance_configuration import environment_values
    actual = environment_values(service.get("env", {}), service.get("environmentVariables", []))
    image = service.get("image") or "${TL_GOV_AGENT_IMAGE}"
    spool = "${TL_GOV_SPOOL_DIR}"
    if image != "${TL_GOV_AGENT_IMAGE}":
        spool = actual.get("TL_GOV_SPOOL_DIR", "")
        if not spool.startswith("/") or spool == "/":
            raise ValueError("host-spool-configuration-unavailable")
    expected = generator().agent_environment(config, image, spool)
    if actual != expected:
        raise ValueError("host-governance-environment-mismatch")
    for legacy in (project / "agent.yaml", agent / "agent.yaml"):
        legacy = contained(project, legacy.relative_to(project))
        if legacy.exists():
            value = yaml.safe_load(legacy.read_text())
            if environment_values(value.get("environment_variables", {})) != expected:
                raise ValueError("legacy-host-governance-environment-mismatch")
    return expected


def check(project, document):
    if not enabled(document):
        return {}
    result = {"scope": "static-declarations-not-enforcement", "stage": "pre-image",
              "bindings": [], "gaps": [], "unverified": ["signature-verification-required-at-collection",
                                                       "live-effect-closure-not-verified"]}
    gaps = result["gaps"]
    try:
        import yaml
        from govern_bundle.policy_bundle import PIN_FILE, verify_bundle, validate_native_manifest
        from govern_control_plane.models import SignedBundle, parse
        gen = generator()
        auxiliary = contained(project, "infra/registry-pull.bicep")
        # Task10's bootstrap deployment consumes this separately from main.bicep.
        # Only the exact shipped helper is exempt; arbitrary orphans still fail.
        if auxiliary.is_file() and auxiliary.read_bytes() == (gen.REFERENCE / "registry-pull.bicep").read_bytes():
            result["auxiliary_modules"] = ["registry-pull.bicep"]
        document = {k: document[k] for k in ("framework", "governance", "tools")}
        contract = gen.validate_contract(document)
        package = read(contained(project, ".threadlight/governance-package.json"))
        packaged = gen.validate_contract(package["contract"])
        if packaged != contract or package["framework"] != document["framework"]:
            raise ValueError("packaged-contract-runtime-or-required-controls-changed")
        config = package["configuration"]
        result["bindings"] = bindings(contract, config.get("policy_digest"), config["environment"])
        agent, frozen = gen.frozen_configuration(project, package)
        agent = contained(project, agent.relative_to(project))
        azure = yaml.safe_load(contained(project, "azure.yaml").read_text())
        svc = azure["services"][config["agent_service"]]
        if svc["host"] != "azure.ai.agent":
            raise ValueError("hosted-runtime-path-mismatch")
        env = host_environment(project, agent, svc, config)

        def same(actual, expected, label):
            actual = contained(project, actual.relative_to(project))
            if not actual.is_file():
                gaps.append(f"selected binding has no runtime adapter: {label}")
            elif actual.read_bytes() != expected.read_bytes():
                gaps.append(f"selected runtime adapter changed: {label}")

        native = contract["framework"] == "microsoft-agent-framework"
        pins = read(PIN_FILE)
        same(agent / "container.py", gen.REFERENCE / ("maf-container.py" if native else "ghcp-container.py"),
             "container.py")
        runtime_spec = importlib.util.find_spec("govern_native")
        runtime = Path(runtime_spec.origin).parent if runtime_spec else gen.GOVERN / "references/runtime"
        if native:
            for expected in runtime.glob("*.py"):
                same(agent / "runtime" / expected.name, expected, "runtime/" + expected.name)
            same(agent / "audit_delivery.py", gen.REFERENCE / "audit_delivery.py", "audit_delivery.py")
        for name, destination in (
            ("govern_bundle.policy_bundle", "govern_bundle/policy_bundle.py"),
            ("govern_canonical.canonical", "govern_canonical/canonical.py"),
            ("skills._shared.governance", "skills/_shared/governance.py"),
            ("skills._shared.manifest", "skills/_shared/manifest.py"),
        ):
            same(agent / destination, Path(importlib.import_module(name).__file__), destination)
        import govern_control_plane
        control = Path(govern_control_plane.__file__).parent
        for expected in control.glob("*.py"):
            same(agent / "vendor/control-plane" / expected.name, expected, "agent-control-plane/" + expected.name)
            same(agent / "govern_control_plane" / expected.name, expected, "agent-control-client/" + expected.name)
        for name in ("govern-control-plane", "govern-gateway"):
            service = azure["services"].get(name, {})
            if service.get("project") != "./src/" + name or service.get("host") != "containerapp":
                raise ValueError("control-plane-gateway-service-source-mismatch")
            directory = contained(project, "src/" + name)
            same(directory / "service_entry.py", gen.REFERENCE / "service_entry.py", name + "/service_entry.py")
            for expected in control.glob("*.py"):
                same(directory / "vendor/control-plane" / expected.name, expected, name + "/" + expected.name)
            docker = (directory / "Dockerfile").read_text()
            if docker != gen.dockerfile_text(gateway=name == "govern-gateway", opa_pin=pins["opa"]):
                raise ValueError("service-docker-adapter-not-installed")
        same(project / "infra/governance.bicep", gen.REFERENCE / "governance.bicep", "governance.bicep")
        import govern_gateway
        for expected in Path(govern_gateway.__file__).parent.glob("*.py"):
            same(project / "src/govern-gateway/vendor/gateway" / expected.name, expected,
                 "gateway/" + expected.name)
        docker = (agent / "Dockerfile").read_text()
        if docker != gen.dockerfile_text(agent=True,
                gateway=native and "probe_observability" in config, opa_pin=pins["opa"]):
            raise ValueError("agent-docker-adapter-not-installed")
        root = agent / "policy" if native else project / "src/govern-gateway/policy"
        deployment_path = contained(project, ".threadlight/governance-deployment.json")
        deployment = read(deployment_path) if deployment_path.exists() else None
        envelope_path = contained(project, ("src/govern-gateway/policy-envelope.json" if not native
                                           else (agent / "policy-envelope.json").relative_to(project)))
        final_gateway = not native and (envelope_path.exists() or deployment is not None)
        # The GHCP package records bootstrap input, not the later image-bound registry.
        # These are offline declarations: none is an authority to verify its own signature.
        signed = parse(SignedBundle, (envelope_path.read_bytes() if native or final_gateway
                                     else json.dumps(package["signed_policy"]).encode()))
        source = "immutable-package" if native else "bootstrap-package"
        expected_digest = config["policy_digest"]
        if final_gateway:
            source = "deployment-binding" if deployment is not None else "staged-envelope"
            expected_digest = (deployment["bindings"]["policy_digest"] if deployment is not None
                               else signed.envelope.content_digest)
        bundle = verify_bundle(root, expected_digest=expected_digest)
        validate_native_manifest(bundle.root)
        gen.validate_policy(bundle, signed, config)
        if native and signed.model_dump(mode="json") != package["signed_policy"]:
            raise ValueError("frozen-signed-policy-changed")
        registry = None
        image = svc.get("image")
        if final_gateway:
            registry = gen.validate_gateway_policy(bundle, signed, config, document, image,
                deployment["bindings"] if deployment is not None else None)
        elif not native and (root / "gateway-registry.json").exists():
            from govern_gateway.dispatcher import Registry
            registry = parse(Registry, (root / "gateway-registry.json").read_bytes())
        gen.validate_bundle_contract(bundle, document, config, registry)
        result["policy_trust"] = {"source": source, "digest": bundle.bundle_digest,
                                  "signature_verified": False}
        result["bindings"] = bindings(contract, bundle.bundle_digest, config["environment"])
        if not native and not final_gateway:
            result["unverified"].append("final-gateway-policy-not-staged")
        if image and image != "${TL_GOV_AGENT_IMAGE}":
            result["stage"] = "image-bound"
            digest = image.split("@")[1]
            if env.get("TL_GOV_IMAGE_DIGEST") != digest:
                raise ValueError("image-metadata-digest-mismatch")
            if registry is not None and registry.deployment.image_digest != digest:
                raise ValueError("signed-registry-image-mismatch")
        else:
            result["unverified"].append("image-not-built")
        if deployment is not None:
            b, infra = deployment["bindings"], deployment["infrastructure"]
            if (infra["runtime"] != contract["framework"] or infra["tenant_id"] != config["tenant_id"]
                    or infra["environment"] != config["environment"] or b["policy_digest"] != bundle.bundle_digest
                    or deployment["images"]["agent"] != image):
                raise ValueError("deployment-binding-disagrees-with-package")
            if not native and (infra["agent_id"] != config["agent_id"]
                               or infra["approver_roles"] != config["approver_roles"]):
                raise ValueError("deployment-binding-disagrees-with-package")
            for service in ("control", "gateway"):
                settings = b[service + "_config"]
                scope = config["control_plane_scope" if service == "control" else "gateway_scope"]
                if (settings["tenant_id"] != config["tenant_id"] or settings["key_id"] != config["key_id"]
                        or scope != f"api://{settings['audience']}/.default"
                        or settings["approver_roles"] != config["approver_roles"]):
                    raise ValueError("service-auth-or-role-binding-mismatch")
            if b["gateway_config"]["control_plane_url"] != config["control_plane_url"]:
                raise ValueError("service-endpoint-binding-mismatch")
            from skills._shared.governance_configuration import SERVICE_ENVIRONMENT, environment_values, project_environment
            for name, key, kind in (("govern-control-plane", "control_config", "control-plane"),
                                    ("govern-gateway", "gateway_config", "gateway")):
                service = azure["services"][name]
                overrides = environment_values(service.get("env", {}), service.get("environmentVariables", []),
                                                names=SERVICE_ENVIRONMENT)
                if overrides:
                    setting = "GOV_CONFIG_JSON" if kind == "control-plane" else "GATEWAY_CONFIG_JSON"
                    expected = {setting: json.dumps(b[key]), "TL_GOV_SERVICE": kind}
                    if kind == "control-plane":
                        expected["AZURE_CLIENT_ID"] = b["control_client"]
                    actual = project_environment(overrides, names=SERVICE_ENVIRONMENT)
                    wanted = project_environment(expected, names=SERVICE_ENVIRONMENT)
                    if any(wanted.get(k) != v for k, v in actual.items()):
                        raise ValueError("service-governance-environment-mismatch")
            parameters = contained(project, "infra/main.parameters.json")
            if parameters.exists():
                values = read(parameters)["parameters"]
                for key, expected in (("governanceBindings", b), ("governanceImages", deployment["images"]),
                                      ("governanceConfig", infra)):
                    if values.get(key, {}).get("value") != expected:
                        raise ValueError("generated-service-configuration-mismatch")
            if native and "native_probe_config" in b:
                from govern_gateway.probe_runtime import ProbeConfiguration
                from govern_control_plane.models import canonical
                association = parse(ProbeConfiguration, canonical(b["native_probe_config"]))
                expected = {"agent_id": config["agent_id"], "agent_version": b["agent_version"],
                            "image_digest": image.split("@")[1], "environment": config["environment"],
                            "subscription": config["subscription"], "resource_group": config["resource_group"]}
                if (association.expected_deployment.model_dump(mode="json") != expected
                        or association.producer != "native" or association.key_id != config["key_id"]
                        or association.service_client_id != b["agent_client_id"]
                        or association.downstream_client_id != b["downstream_client"]
                        or {k: v.model_dump(mode="json") for k, v in association.probe_controllers.items()}
                        != b["control_config"]["probe_controllers"]):
                    raise ValueError("native-probe-association-binding-mismatch")
                options = contained(project, ".threadlight/governance-probe.json")
                if not options.is_file():
                    raise ValueError("native-probe-association-content-unavailable")
                try:
                    if __package__:
                        from .governance_probe import load_configuration, preflight
                    else:
                        from governance_probe import load_configuration, preflight
                    probe_config = load_configuration(project, options)
                    _, registered, _ = preflight(probe_config)
                    if (registered.native_policy_digest != bundle.bundle_digest
                            or registered.deployment.model_dump(mode="json") != expected):
                        raise ValueError()
                except Exception:
                    raise ValueError("native-probe-association-content-mismatch") from None
        else:
            from skills._shared.governance_configuration import SERVICE_ENVIRONMENT, environment_values
            for name in ("govern-control-plane", "govern-gateway"):
                service = azure["services"][name]
                if environment_values(service.get("env", {}), service.get("environmentVariables", []),
                                      names=SERVICE_ENVIRONMENT):
                    raise ValueError("service-configuration-requires-frozen-binding")
            result["unverified"].append("deployment-binding-not-built")
        # Credential/RBAC/egress closure is not inferred from packaging.
        for item in contract["tools"]:
            if item["enforcement_path"] == "governed-tool-gateway":
                result["unverified"].append("effect-closure:" + item["id"])
    except Exception as error:
        # Only fixed local diagnostics; never stringify parsers that echo configuration.
        reason = str(error) if type(error) is ValueError and str(error).replace("-", "").isalnum() else "package-invalid-or-incomplete"
        gaps.append("governance: " + reason)
    return result
