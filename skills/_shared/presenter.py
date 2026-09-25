"""Read-only presenter contract assessment, not a runner or live attestation."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import re
import sys
import zipfile

from .agentops import (
    bounded_command, canonical_hash, parse_json,
    read_bytes, read_json, safe_path, sha256,
)

CONTRACT = "specs/presenter-contract.json"
EVIDENCE = ".threadlight/presenter-evidence.json"
TARGET = ".threadlight/presenter-target.json"
PUBLICATION = ".threadlight/presenter-publication.json"
DEPLOYMENT_PIN = parse_json(Path(__file__).with_name("presenter-deployment-pin.json").read_bytes())
GROUPS = ("runtime", "interface", "source", "script", "sizing")
PACKAGE_CASES = (
    "exact_sdk", "response_shape", "retry_dispatch", "metadata_reads",
    "pending", "terminal_stream", "persistence_readback", "recovery",
)
CHECKS = {
    "package": {"level": "native", "inputs": ("runtime",)},
    "deployment": {"level": "hosted", "inputs": ("runtime",)},
    "backend": {"level": "hosted", "inputs": ("runtime", "source")},
    "script": {"level": "hosted", "inputs": GROUPS},
    "human": {"level": "human", "inputs": GROUPS},
}
STATES = ("source-ready", "deployed", "backend-verified", "script-verified", "human-accepted")
PREDECESSORS = {"package": (), "deployment": (), "backend": ("package", "deployment"),
                "script": ("backend",), "human": ("script",)}
SERVICE_PACKAGE_CASES = ("schema_validated", "image_runtime", "startup", "adapter")
SERVICE_FILES = ("manifest", "create_entrypoint", "lockfile", "adapter", "dockerfile")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value, *, empty=False):
    return isinstance(value, list) and (empty or bool(value)) and all(text(v) for v in value)


def timestamp(value):
    require(text(value), "timestamp-missing")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp-timezone-required")
    return result


def selected(root):
    path = safe_path(root, "specs/manifest.json", exists=False)
    if not path.exists():
        return False
    manifest = read_json(root, "specs/manifest.json")
    require(isinstance(manifest, dict), "manifest-not-object")
    if "delivery_profile" not in manifest:
        return False
    require(manifest["delivery_profile"] in ("default", "presenter-ready"),
            "invalid-delivery-profile")
    return manifest["delivery_profile"] == "presenter-ready"


def load_contract(root):
    contract = read_json(root, CONTRACT)
    require(isinstance(contract, dict) and
            contract.get("schema") == "threadlight-presenter-contract/v1", "invalid-presenter-contract")
    for name in ("process_id", "owner"):
        require(text(contract.get(name)), f"missing-{name}")
    hours = contract.get("evidence_max_age_hours")
    require(type(hours) in (int, float) and math.isfinite(hours) and 0 < hours <= 8760,
            "evidence-max-age-hours-required")
    description = contract.get("description", {})
    for name in ("role", "problem", "fictional_company", "agent_contribution",
                 "outcome", "human_responsibility"):
        require(text(description.get(name)), f"description-{name}")
    for name in ("inputs", "deterministic_rules", "inclusions", "exclusions"):
        require(strings(description.get(name)), f"description-{name}")
    journey = contract.get("journey", {})
    for name in ("entry", "interaction", "recovery"):
        require(text(journey.get(name)), f"journey-{name}")
    for name in ("persistence", "download"):
        require(type(journey.get(name)) is bool, f"journey-{name}")
    if journey["persistence"]:
        for name in ("store", "readback", "reopen"):
            require(text(journey.get(name)), f"journey-{name}")
    else:
        require(text(journey.get("nonpersistent_reason")), "nonpersistent-reason-required")
    if journey["download"]:
        require(text(journey.get("download_artifact")), "download-artifact-required")
    availability = contract.get("availability", {})
    for name in ("presenter_access", "historical_read", "session", "source_revision",
                 "preparation", "concurrency", "idempotency", "uncertain_effect"):
        require(text(availability.get(name)), f"availability-{name}")
    require(timestamp(availability.get("expires_at")) > timestamp(availability.get("effective_at")),
            "source-validity-order")
    deploy = contract.get("deployment", {})
    require(deploy.get("guidance") == DEPLOYMENT_PIN, "deployment-guidance-pin-mismatch")
    require(deploy.get("consumer") in ("unified-azd", "native-sdk"), "deployment-consumer-required")
    for name in ("manifest", "runtime_root", "entrypoint", "lockfile", "adapter", "model_env"):
        require(text(deploy.get(name)), f"deployment-{name}")
    require(deploy.get("protocol") in ("responses", "invocations"), "deployment-protocol")
    require(deploy.get("protocol_version") == "2.0.0", "deployment-protocol-version")
    inputs = contract.get("inputs", {})
    require(isinstance(inputs, dict) and set(inputs) == set(GROUPS), "input-groups-required")
    for group in GROUPS:
        require(strings(inputs[group]), f"inputs-{group}")
    sizing = contract.get("sizing", {})
    require(sizing.get("status") in ("proposed", "measured"), "sizing-status")
    for name in ("model_tokens", "model_rounds", "logical_tool_calls", "resource_units", "comparison"):
        require(text(sizing.get(name)), f"sizing-{name}")
    require(strings(sizing.get("unpriced"), empty=True), "sizing-unpriced")
    publication = contract.get("publication", {})
    require(strings(publication.get("files"), empty=True) and
            isinstance(publication.get("bundles"), list), "publication-contract")
    for bundle in publication["bundles"]:
        require(isinstance(bundle, dict) and text(bundle.get("path"))
                and text(bundle.get("source_root")), "publication-bundle")
    return contract


def file_inventory(root, paths):
    inventory = {}
    for relative in paths:
        path = safe_path(root, relative, exists=False)
        require(path.exists(), "input-missing")
        candidates = [path]
        if path.is_dir():
            candidates = []
            for child in sorted(path.rglob("*")):
                require(not child.is_symlink(), "symlink-input")
                if child.is_file() and not set(child.relative_to(path).parts) & {
                    "__pycache__", ".pytest_cache", ".git", ".venv", "node_modules",
                }:
                    candidates.append(child)
            require(candidates, "empty-input-directory")
        for child in candidates:
            relative_file = child.relative_to(root).as_posix()
            inventory[relative_file] = sha256(read_bytes(root, relative_file))
    return inventory


def fingerprints(root, contract):
    root = Path(root).resolve()
    sections = {
        "runtime": {"deployment": contract["deployment"],
                    "inventory": read_json(root, "specs/manifest.json").get("deployment_manifest", {})},
        "interface": {"description": contract["description"], "journey": contract["journey"],
                      "access": {k: contract["availability"][k] for k in
                                 ("presenter_access", "historical_read", "session")}},
        "source": {"availability": {k: v for k, v in contract["availability"].items()
                                   if k not in ("presenter_access", "historical_read", "session")},
                   "rules": contract["description"]["deterministic_rules"]},
        "script": {"journey": contract["journey"], "owner": contract["owner"]},
        "sizing": {"sizing": contract["sizing"]},
    }
    result = {}
    for group in GROUPS:
        paths = list(contract["inputs"][group])
        if group == "runtime":
            paths.extend(_runtime_paths(root, contract))
        result[group] = canonical_hash({
            "process_id": contract["process_id"], "contract": sections[group],
            "files": file_inventory(root, paths),
        })
    return result


def _deployment_document(root, contract):
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("presenter: install pyyaml to validate the selected deployment consumer") from exc
    # Reject ambiguous YAML keys rather than accepting the parser's last value.
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            require(isinstance(key, str) and key not in result, "duplicate-yaml-key")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        return yaml.load(read_bytes(root, contract["deployment"]["manifest"]), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError("presenter deployment: invalid-yaml") from exc


def _runtime_paths(root, contract):
    deploy = contract["deployment"]
    paths = [deploy[name] for name in ("manifest", "runtime_root", "entrypoint", "lockfile", "adapter")]
    if deploy["consumer"] == "native-sdk":
        paths.append(deploy["create_entrypoint"])
        for service in _native_services(deploy):
            paths.extend(service[name] for name in SERVICE_FILES)
            paths.extend(service["inputs"])
            context = service["src"].removeprefix("./")
            for relative in (f"{context}/.dockerignore", service["dockerfile"] + ".dockerignore"):
                if safe_path(root, relative, exists=False).exists():
                    paths.append(relative)
    else:
        document = _deployment_document(root, contract)
        paths.extend(service["project"].removeprefix("./")
                     for service in document["services"].values() if "project" in service)
        infra = document.get("infra", {})
        if infra.get("provider", "bicep") != "microsoft.foundry":
            paths.append(infra.get("path", "infra"))
    return paths


def _native_services(deploy):
    services = deploy.get("ancillary_services", [])
    require(isinstance(services, list), "native-services-list")
    if "ancillary_services" in deploy:
        require(deploy["consumer"] == "native-sdk" and bool(services),
                "native-services-consumer")
        require(text(deploy.get("service")), "native-agent-service-name")
    names = {deploy.get("service")}
    for service in services:
        require(isinstance(service, dict) and set(service) == {
            "name", "resource_name", "host", "role", "src", "consumer", "inputs", *SERVICE_FILES,
        }, "native-service-shape")
        require(all(text(service[key]) for key in ("name", "resource_name", "src", *SERVICE_FILES)),
                "native-service-paths")
        require(service["name"] not in names, "native-service-duplicate")
        names.add(service["name"])
        require(service["host"] == "containerapp" and service["consumer"] == "containerapp-arm",
                "native-service-consumer")
        require(service["role"] in ("mcp", "workspace"), "native-service-role")
        require(strings(service["inputs"]), "native-service-inputs")
    require(len({s["resource_name"] for s in services}) == len(services),
            "native-service-resource-duplicate")
    return services


def _service_definition(root, service):
    """Bounded ARM resource checks; full schema/native SDK proof stays with the producer."""
    value = read_json(root, service["manifest"])
    require(isinstance(value, dict) and value.get("type") == "Microsoft.App/containerApps",
            "native-service-resource-type")
    require(value.get("name") == service["resource_name"] and text(value.get("location")),
            "native-service-resource-identity")
    require(isinstance(value.get("apiVersion"), str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-preview)?", value["apiVersion"]),
            "native-service-api-version")
    identity = value.get("identity", {})
    require(isinstance(identity, dict) and identity.get("type") in
            ("SystemAssigned", "UserAssigned", "SystemAssigned, UserAssigned"),
            "native-service-managed-identity")
    if "UserAssigned" in identity["type"]:
        assigned = identity.get("userAssignedIdentities")
        require(isinstance(assigned, dict) and bool(assigned)
                and all(text(k) and isinstance(v, dict) for k, v in assigned.items()),
                "native-service-user-identity")
    properties = value["properties"]
    require(isinstance(properties, dict) and text(properties.get("managedEnvironmentId")),
            "native-service-environment")
    template = properties["template"]
    containers = template["containers"]
    require(isinstance(containers, list) and len(containers) == 1
            and not template.get("initContainers"), "native-service-single-container")
    container = containers[0]
    require(isinstance(container, dict) and text(container.get("name")), "native-service-container")
    require(text(container.get("image")) and
            re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", container["image"]),
            "native-service-image-digest")
    ingress = properties["configuration"]["ingress"]
    require(isinstance(ingress, dict) and type(ingress.get("external")) is bool
            and type(ingress.get("targetPort")) is int and 0 < ingress["targetPort"] <= 65535,
            "native-service-ingress")
    traffic = ingress.get("traffic", [])
    require(isinstance(traffic, list) and len(traffic) <= 1, "native-service-traffic-split-unsupported")
    if traffic:
        route = traffic[0]
        require(isinstance(route, dict) and type(route.get("weight")) is int
                and route["weight"] == 100 and (
                    (route.get("latestRevision") is True and not route.get("revisionName"))
                    or (route.get("latestRevision", False) is False and text(route.get("revisionName")))
                ), "native-service-traffic-route")
    return value


def _native_inventory(root, deploy, inventory):
    services = _native_services(deploy)
    if not services:
        if inventory is not None:
            require(not inventory.get("scheduled_jobs"), "native-ancillary-consumer-required")
            require(all(item.get("src", "").removeprefix("./") == deploy["runtime_root"]
                        for item in inventory.get("services", [])),
                    "native-ancillary-consumer-required")
        return
    require(isinstance(inventory, dict) and isinstance(inventory.get("services"), list),
            "native-complete-inventory-required")
    require(not inventory.get("scheduled_jobs"), "native-jobs-unsupported")
    expected = {deploy["service"]: ("azure.ai.agent", deploy["runtime_root"])}
    expected.update({s["name"]: (s["host"], s["src"].removeprefix("./")) for s in services})
    actual = {}
    for item in inventory["services"]:
        require(isinstance(item, dict) and text(item.get("name"))
                and item["name"] not in actual and text(item.get("src")),
                "native-inventory-service-shape")
        actual[item["name"]] = (item.get("host"), item["src"].removeprefix("./"))
    require(actual == expected, "native-service-inventory-drift")
    for service in services:
        context = safe_path(root, service["src"], exists=False)
        require(context.is_dir(), "native-service-context")
        for key in SERVICE_FILES:
            read_bytes(root, service[key])
        require(safe_path(root, service["dockerfile"]).is_relative_to(context),
                "native-service-dockerfile-context")
        sources = [safe_path(root, path, exists=False) for path in service["inputs"]]
        require(any(source.is_dir() for source in sources), "native-service-source-directory")
        for source in sources:
            require(source.exists() and source != Path(root).resolve()
                    and source.is_relative_to(context), "native-service-source-directory")
        file_inventory(Path(root).resolve(), service["inputs"])
        _service_definition(root, service)


def deployment_gaps(root, contract, *, inventory=None, packaged=False):
    """Validate the selected native consumer, never infer it from whichever file exists."""
    try:
        deploy = contract["deployment"]
        _native_services(deploy)
        document = _deployment_document(root, contract)
        for folder in (Path(root), safe_path(root, deploy["runtime_root"], exists=False)):
            for name in ("agent.yaml", "agent.manifest.yaml"):
                require(not (folder / name).exists(), "competing-deployment-manifest")
        if deploy["consumer"] == "unified-azd":
            require(deploy["manifest"] == "azure.yaml", "unified-manifest-location")
            service = document["services"][deploy["service"]]
            require(service["host"] == "azure.ai.agent", "unified-host")
            require(service["project"].removeprefix("./") == deploy["runtime_root"], "runtime-layout-drift")
            require("config" not in service and service["kind"] == "hosted", "unified-kind")
            protocols = service["protocols"]
            variables = service.get("environmentVariables", [])
            require(isinstance(variables, list), "unified-environment-list")
            environment = {}
            for variable in variables:
                name = variable["name"]
                require(isinstance(name, str) and name not in environment, "duplicate-environment-name")
                environment[name] = variable["value"]
            provider = document.get("infra", {}).get("provider", "bicep")
            require(provider in ("microsoft.foundry", "bicep", "terraform"), "infra-provider")
            if provider != "microsoft.foundry":
                infra = document.get("infra", {}).get("path", "infra")
                file_inventory(Path(root).resolve(), [infra])
            if inventory is not None:
                for declared in inventory.get("services", []):
                    actual = document["services"][declared["name"]]
                    require(actual["host"] == declared["host"]
                            and actual["project"].removeprefix("./") == declared["src"].removeprefix("./"),
                            "deployment-service-drift")
                    if packaged:
                        read_bytes(root, declared["src"].removeprefix("./").rstrip("/") + "/Dockerfile")
                scheduled = inventory.get("scheduled_jobs", [])
                for job in scheduled:
                    require(any(s.get("name") == job["name"] for s in inventory.get("services", [])),
                            "scheduled-job-service-missing")
            if packaged:
                read_bytes(root, deploy["runtime_root"].rstrip("/") + "/Dockerfile")
        else:
            # Frozen native create definition, not a second azd manifest.
            require(not (Path(root) / "azure.yaml").exists(), "competing-deployment-consumer")
            require(text(deploy.get("create_entrypoint")), "native-create-entrypoint")
            read_bytes(root, deploy["create_entrypoint"])
            require(document["kind"] == "hosted", "native-kind")
            protocols = document["protocol_versions"]
            environment = document.get("environment_variables", {})
            if packaged:
                read_bytes(root, deploy["runtime_root"].rstrip("/") + "/Dockerfile")
            _native_inventory(root, deploy, inventory)
        require(protocols == [{"protocol": deploy["protocol"], "version": deploy["protocol_version"]}],
                "deployment-protocol-drift")
        require(isinstance(environment, dict) and text(environment.get(deploy["model_env"])),
                "deployment-model-environment")
        require(all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()),
                "deployment-environment-shape")
        require(not any(k.startswith(("FOUNDRY_", "AGENT_")) or
                        k == "APPLICATIONINSIGHTS_CONNECTION_STRING" for k in environment),
                "reserved-platform-environment")
        for name in ("entrypoint", "lockfile", "adapter"):
            read_bytes(root, deploy[name])
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        return [f"presenter deployment: {type(exc).__name__}: {exc}"]
    return []


def _target(root, contract):
    value = read_json(root, TARGET)
    services = _native_services(contract["deployment"])
    fields = {"attempt", "environment", "version", "image", "identity"}
    require(isinstance(value, dict) and set(value) == fields | ({"services"} if services else set()),
            "target-shape")
    _target_tuple(value)
    if services:
        targets = value["services"]
        require(isinstance(targets, dict) and set(targets) == {s["name"] for s in services},
                "native-service-target-inventory")
        for service in services:
            target = targets[service["name"]]
            require(isinstance(target, dict) and set(target) == fields | {
                "resource_name", "definition_sha256"}, "native-service-target-shape")
            _target_tuple(target)
            definition = _service_definition(root, service)
            require(target["resource_name"] == definition["name"]
                    and target["definition_sha256"] == sha256(read_bytes(root, service["manifest"]))
                    and target["image"] == definition["properties"]["template"]["containers"][0]["image"],
                    "native-service-target-definition")
            traffic = definition["properties"]["configuration"]["ingress"].get("traffic", [])
            if traffic and traffic[0].get("revisionName"):
                require(target["version"] == traffic[0]["revisionName"], "native-service-target-route")
    return value


def _target_tuple(value):
    require(all(text(value.get(k)) for k in ("attempt", "environment", "version", "image", "identity")),
            "target-empty")
    require(re.fullmatch(r"(?:[^@\s]+@)?sha256:[0-9a-f]{64}", value["image"]), "target-image-digest")


def _service_facts(root, contract, check, facts):
    services = _native_services(contract["deployment"])
    if not services or check not in ("package", "deployment"):
        return
    recorded = facts.get("services")
    require(isinstance(recorded, dict) and set(recorded) == {s["name"] for s in services},
            "native-service-proof-inventory")
    for service in services:
        proof = recorded[service["name"]]
        require(isinstance(proof, dict), "native-service-proof-shape")
        required = SERVICE_PACKAGE_CASES if check == "package" else ("observed",)
        require(all(proof.get(name) is True for name in required), "native-service-proof-cases")
        require(proof.get("manifest_sha256") == sha256(read_bytes(root, service["manifest"]))
                and proof.get("adapter_sha256") == sha256(read_bytes(root, service["adapter"])),
                "native-service-proof-binding")
        refs = proof.get("evidence")
        require(isinstance(refs, list) and bool(refs), "native-service-retained-output")
        for ref in refs:
            require(isinstance(ref, dict) and sha256(read_bytes(root, ref["path"])) == ref["sha256"],
                    "native-service-output-digest")


def _receipt(root, contract, check, reference, hashes, now, references):
    require(isinstance(reference, dict), "receipt-reference")
    raw = read_bytes(root, reference["path"])
    require(sha256(raw) == reference["sha256"], "receipt-digest-mismatch")
    value = parse_json(raw)
    require(isinstance(value, dict) and value.get("schema") == "threadlight-presenter-receipt/v1",
            "receipt-schema")
    require(value.get("process_id") == contract["process_id"] and value.get("check") == check,
            "receipt-process-or-check")
    require(value.get("level") == CHECKS[check]["level"], "receipt-evidence-level")
    require(value.get("result") == "pass", "receipt-not-passing")
    observed = timestamp(value.get("observed_at"))
    require(observed <= now, "receipt-from-future")
    refs = value.get("evidence")
    require(isinstance(refs, list) and bool(refs), "retained-output-required")
    for ref in refs:
        require(sha256(read_bytes(root, ref["path"])) == ref["sha256"], "retained-output-digest")
    predecessors = {key: references.get(key, {}).get("sha256") for key in PREDECESSORS[check]}
    if None in predecessors.values() or value.get("predecessors") != predecessors:
        return {"status": "stale", "reason": "Predecessor receipt changed or missing."}
    for key in PREDECESSORS[check]:
        predecessor = read_json(root, references[key]["path"])
        require(timestamp(predecessor.get("observed_at")) <= observed, "receipt-predates-predecessor")
    expected = {group: hashes[group] for group in CHECKS[check]["inputs"]}
    if value.get("inputs") != expected:
        return {"status": "stale", "reason": "Changed declared inputs; rerun only this check."}
    if check != "package" and value.get("target") != _target(root, contract):
        return {"status": "stale", "reason": "Deployment attempt/identity/version/image changed."}
    facts = value.get("facts", {})
    require(isinstance(facts, dict), "receipt-facts")
    _service_facts(root, contract, check, facts)
    if check == "package":
        require(all(facts.get(name) is True for name in PACKAGE_CASES), "packaged-integration-cases")
    if check in ("backend", "script"):
        require(all(facts.get(name) is True for name in ("interaction", "terminal_success")),
                "terminal-result-required")
        if check == "script":
            require(facts.get("entry") is True, "entry-not-exercised")
        if contract["journey"]["persistence"]:
            require(all(facts.get(name) is True for name in
                        ("persisted", "independent_readback", "reopened")), "saved-result-journey")
            require(all(text(facts.get(name)) for name in
                        ("operation_id", "result_id", "writer_session", "reader_session", "readback_method")),
                    "readback-correlation")
            require(facts["reader_session"] != facts["writer_session"], "readback-not-independent")
        if contract["journey"]["download"]:
            require(facts.get("download_verified") is True, "promised-download-not-verified")
    if check == "human":
        require(text(facts.get("accepted_by")) and facts.get("first_time_presenter") is True,
                "human-acceptance-required")
    if check != "package" and now - observed > timedelta(hours=contract["evidence_max_age_hours"]):
        return {"status": "stale", "action": "refresh-observation",
                "reason": "Recorded evidence validity elapsed; refresh observation, not the underlying effect."}
    return {"status": "verified", "reason": "Bound recorded evidence; not an independent attestation.",
            "receipt": reference}


def assess(root, *, now=None):
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    report = {"enabled": True, "ready": False,
              "evidence_authority": "recorded-not-independently-attested",
              "states": dict.fromkeys(STATES, "pending"), "checks": {}, "gaps": []}
    try:
        if not selected(root):
            return {"enabled": False}
        contract = load_contract(root)
        hashes = fingerprints(root, contract)
        inventory = read_json(root, "specs/manifest.json").get("deployment_manifest")
        gaps = deployment_gaps(root, contract, inventory=inventory)
        require(not gaps, "; ".join(gaps))
        report["states"]["source-ready"] = "verified"
        availability = contract["availability"]
        report["source_usable"] = timestamp(availability["effective_at"]) <= now < timestamp(availability["expires_at"])
        report["owner"] = contract["owner"]
        evidence_path = safe_path(root, EVIDENCE, exists=False)
        index = read_json(root, EVIDENCE) if evidence_path.exists() else {
            "schema": "threadlight-presenter-evidence/v1", "checks": {}}
        require(isinstance(index, dict) and index.get("schema") == "threadlight-presenter-evidence/v1"
                and isinstance(index.get("checks"), dict)
                and not (set(index["checks"]) - set(CHECKS)), "evidence-index-shape")
        for check in CHECKS:
            reference = index["checks"].get(check)
            result = {"status": "pending", "reason": "No recorded evidence."}
            if reference is not None:
                try:
                    result = _receipt(root, contract, check, reference, hashes, now, index["checks"])
                except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                    result = {"status": "blocked", "reason": str(exc)}
            report["checks"][check] = result
        chains = {
            "deployed": ("deployment",),
            "backend-verified": ("package", "deployment", "backend"),
            "script-verified": ("package", "deployment", "backend", "script"),
            "human-accepted": tuple(CHECKS),
        }
        for state, checks in chains.items():
            statuses = [report["checks"][c]["status"] for c in checks]
            report["states"][state] = next(
                (s for s in ("blocked", "stale", "pending") if s in statuses), "verified")
        report["ready"] = all(v == "verified" for v in report["states"].values()) and report["source_usable"]
        report["next_check"] = next((c for c in CHECKS if report["checks"][c]["status"] != "verified"), None)
        if not report["source_usable"]:
            report["gaps"].append("Source not currently usable for new work; historical reads remain independent.")
            report["next_check"] = report["next_check"] or "explicit-source-preparation"
        report["publication"] = _publication_status(root, contract)
        if report["publication"]["status"] not in ("verified", "not-requested"):
            report["ready"] = False
            report["next_check"] = report["next_check"] or "publication"
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        report["ready"] = False
        report["states"]["source-ready"] = "blocked"
        report["gaps"].append(str(exc))
        report["next_check"] = "contract"
    return report


def validate_comparison(root, relative):
    """Read additive comparison bindings without changing either canonical receipt."""
    value = read_json(root, relative)
    require(isinstance(value, dict) and value.get("schema") == "threadlight-presenter-comparison/v1",
            "comparison-schema")
    require(text(value.get("method")) and text(value.get("process_id")), "comparison-method")
    receipts = []
    for side in ("baseline", "candidate"):
        ref = value[side]
        raw = read_bytes(root, ref["path"])
        require(sha256(raw) == ref["sha256"], "comparison-receipt-digest")
        receipt = parse_json(raw)
        require(receipt.get("schema") == "threadlight-presenter-receipt/v1"
                and receipt.get("process_id") == value["process_id"], "comparison-process")
        require(receipt.get("level") in ("native", "hosted"), "comparison-evidence-level")
        receipts.append(receipt)
    baseline, candidate = receipts
    require(baseline["level"] == candidate["level"] and baseline["check"] == candidate["check"],
            "comparison-incompatible-evidence")
    require(text(baseline["inputs"].get("source")) and
            baseline["inputs"]["source"] == candidate["inputs"].get("source"), "comparison-source-drift")
    return {"status": "verified", "process_id": value["process_id"],
            "baseline": value["baseline"], "candidate": value["candidate"],
            "scope": "receipt-binding-not-quality-or-cost-verification"}


def _publication_status(root, contract):
    if not (contract["publication"]["files"] or contract["publication"]["bundles"]):
        return {"status": "not-requested"}
    if not safe_path(root, PUBLICATION, exists=False).exists():
        return {"status": "pending", "reason": "Final immutable publication proof required."}
    try:
        proof = read_json(root, PUBLICATION)
        commit = proof["source_commit"]
        require(_git(root, "rev-parse", "HEAD").decode().strip() == commit, "publication-not-final-commit")
        require(proof == verify_publication(root, contract, commit), "publication-proof-drift")
        paths = [p for group in contract["inputs"].values() for p in group]
        paths += _runtime_paths(root, contract)
        paths += [CONTRACT, *contract["publication"]["files"]]
        paths += [item["path"] for item in contract["publication"]["bundles"]]
        for path, digest in file_inventory(root, paths).items():
            require(sha256(_git(root, "show", f"{commit}:{path}")) == digest,
                    "publication-worktree-drift")
        return proof
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        return {"status": "blocked", "reason": str(exc)}


def _git(root, *args, limit=8 * 1024 * 1024):
    code, output, _ = bounded_command(["git", "-C", str(root), *args], cwd=root, max_bytes=limit)
    require(code == 0, "immutable-source-unavailable")
    return output


def verify_publication(root, contract, commit):
    """Read blobs from an exact commit, including complete archive source subtrees."""
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "immutable-commit-required")
    tree = {}
    for row in _git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        tree[raw_path.decode()] = (mode, kind, oid)

    def blob(path):
        safe_path(root, path, exists=False)
        require(path in tree and tree[path][0] in ("100644", "100755")
                and tree[path][1] == "blob", "publication-file-not-in-tree")
        return _git(root, "cat-file", "blob", tree[path][2])

    require(parse_json(blob(CONTRACT)) == contract, "publication-contract-not-source-bound")
    files = {p: sha256(blob(p)) for p in contract["publication"]["files"]}
    for bundle in contract["publication"]["bundles"]:
        prefix = bundle["source_root"].rstrip("/") + "/"
        safe_path(root, bundle["source_root"], exists=False)
        expected = {p[len(prefix):]: blob(p) for p in tree if p.startswith(prefix)}
        require(expected, "bundle-source-empty")
        raw = blob(bundle["path"])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)) and set(names) == set(expected),
                    "bundle-tree-members-mismatch")
            for name, data in expected.items():
                info = archive.getinfo(name)
                require(info.file_size == len(data), "bundle-size-mismatch")
                require(archive.read(name) == data, "bundle-source-bytes-mismatch")
        files[bundle["path"]] = sha256(raw)
    return {"status": "verified", "source_commit": commit, "artifacts": files,
            "scope": "immutable-source-publication-not-hosted-acceptance"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--publication-commit")
    parser.add_argument("--comparison", help="Validate one additive comparison's immutable receipt bindings")
    args = parser.parse_args(argv)
    try:
        require(not (args.publication_commit and args.comparison), "choose-one-assessment")
        report = (validate_comparison(args.root, args.comparison) if args.comparison else
                  verify_publication(args.root, load_contract(args.root), args.publication_commit)
                  if args.publication_commit else assess(args.root))
        print(json.dumps(report, indent=2))
        return 0 if report.get("status") == "verified" or report.get("ready") or not report.get("enabled", True) else 1
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
