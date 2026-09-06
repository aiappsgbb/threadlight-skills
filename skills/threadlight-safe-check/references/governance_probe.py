#!/usr/bin/env python3
"""Explicit staged noop collector; never invoke business tools or trust model output."""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import AsyncExitStack
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import httpx

if (Path(__file__).resolve().parents[3] / "skills/_shared/governance.py").is_file():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    from . import governance_observation as observation
    from . import governance_static as static
except ImportError:
    import governance_observation as observation
    import governance_static as static

from skills._shared.governance import validate_governance_contract
from skills._shared.probe_evidence import (
    ProbeEvidenceError, require, state, fresh, advance, evaluate_pair,
    require_target, require_selected_target,
    policy_bindings,
)
from skills._shared.governance_configuration import (
    AGENT_ENVIRONMENT, SERVICE_ENVIRONMENT, DECLARED_FILES,
    project_environment, configuration_digest, validate_digests,
)


def now():
    return datetime.now(timezone.utc)


class API:
    def __init__(self, config, credential, http, timeout):
        self.config, self.credential, self.http, self.timeout = config, credential, http, timeout

    async def request(self, service, method, path, body=None, *, status=200, authenticated=True):
        from govern_control_plane.models import canonical, strict_json
        headers = {"Content-Type": "application/json"}
        async with asyncio.timeout(self.timeout):
            if authenticated:
                token = await self.credential.get_token(self.config[service + "_scope"])
                require(len(token.token) <= 16384, "controller-token-invalid")
                encoded = token.token.split(".")[1]
                claims = strict_json(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
                roles = {"Governance.Probe.Control"} if method == "POST" else {
                    "Governance.Probe.Control", "Governance.Probe.Read"}
                # A mismatch is grounds to refuse, not proof of authenticity.
                # The destination's Entra boundary must authenticate this exact token.
                require(claims.get("tid") == self.config["tenant_id"]
                        and claims.get("oid") == self.config["controller_principal"]
                        and claims.get("azp") == self.config["controller_client_id"]
                        and claims.get("aud") == self.config["auth"][service]["audience"]
                        and not claims.get("scp") and roles.intersection(claims.get("roles", [])),
                        "controller-token-scope-mismatch")
                headers["Authorization"] = "Bearer " + token.token
            async with self.http.stream(method, self.config[service + "_url"] + path,
                    headers=headers, content=canonical(body) if body is not None else None,
                    follow_redirects=False) as response:
                require(response.status_code == status, "service-response-unavailable")
                raw = bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    require(len(raw) <= 32768, "service-response-too-large")
                return strict_json(bytes(raw))


async def verify_native(config, tenant, signer):
    from govern_bundle.policy_bundle import verify_bundle, validate_native_manifest
    from govern_control_plane.models import SignedBundle, canonical, parse, envelope_digest
    signed = parse(SignedBundle, canonical(config["signed"]))
    envelope = signed.envelope
    require(envelope.tenant_id == tenant and envelope.key_id == config["key_id"]
            and envelope.policy_id == config["policy_id"] and envelope.version == config["policy_version"]
            and envelope.content_digest == config["policy_digest"] and envelope.expires_at > now(),
            "native-envelope-mismatch")
    await signer.health()
    require(await signer.verify(envelope_digest(envelope), base64.b64decode(signed.signature, validate=True)),
            "native-signature-invalid")
    bundle = verify_bundle(Path(config["bundle_path"]), expected_digest=config["policy_digest"])
    validate_native_manifest(bundle.root)
    return bundle


def preflight(config):
    from govern_bundle.policy_bundle import verify_bundle
    from govern_gateway.dispatcher import Registry, https_endpoint
    from govern_control_plane.auth import Settings
    from govern_control_plane.models import canonical, parse
    require(config.get("schema") == "threadlight-governance-probe/v1"
            and not config.get("local_only"), "live-collector-configuration-required")
    require(isinstance(config.get("selection"), dict)
            and not set(config["selection"]) - observation.SELECTORS, "invalid-selection")
    contract = validate_governance_contract(config["contract"], deployment_target="customer-pilot")
    require(contract["governance"]["mode"] != "off", "governance-off")
    bundle = verify_bundle(Path(config["policy"]["bundle_path"]), expected_digest=config["policy"]["policy_digest"])
    registry = parse(Registry, (bundle.root / "gateway-registry.json").read_bytes())
    actions = [a for a in registry.actions if a.probe_safe is True and a.name == "governance_probe_noop"]
    require(len(actions) == 1, "no-explicit-safe-probe-contract")
    action = actions[0]
    selected = next((b for b in contract["tools"] if b["id"] == action.name), None)
    path = "local-agent-hooks" if config["producer"] == "native" else "governed-tool-gateway"
    require(config["producer"] in ("native", "gateway") and selected is not None
            and selected["policy_binding"] == action.policy_binding
            and selected["enforcement_path"] == path
            and selected["intervention_points"] == ["pre_tool_call"]
            and not set(selected["requires"]) - {"audit", "decision-receipt", "durable-audit", "signed-policy-bundle"},
            "exact-probe-binding-required")
    require(contract["governance"]["environment_modes"][registry.deployment.environment] == "enforce",
            "evaluate_only-is-not-enforced")
    require(config.get("expected_deployment") == registry.deployment.model_dump(mode="json"),
            "declared-deployment-does-not-match-signed-registry")
    require(config["subject"] in action.workloads and registry.tenant_id == config["tenant_id"],
            "probe-workload-scope-mismatch")
    require(config["producer_url"] + "/mcp" == registry.gateway_url
            and config["fixture_url"] + "/governance/noop" == action.endpoint
            and set(config["allowed_endpoints"]) == {action.endpoint, action.outcome_endpoint}
            and config["fixture_scope"] == action.credential_scope,
            "probe-endpoint-not-in-trusted-binding")
    require(config["controller_principal"] != config["subject"]
            and config["controller_client_id"] != config["client_id"], "separate-controller-required")
    for service in ("producer", "fixture", "control_plane"):
        https_endpoint(config[service + "_url"] + "/check")
        settings = parse(Settings, canonical(config["auth"][service]))
        grant = settings.probe_controllers.get(config["controller_principal"])
        require(settings.tenant_id == registry.tenant_id and grant is not None
                and grant.client_id == config["controller_client_id"]
                and config["subject"] in grant.subjects and action.name in grant.actions,
                "controller-scope-not-declared")
    require(set(config["services"]) == ({"fixture", "control_plane", "producer"}
                                       if config["producer"] == "gateway" else {"fixture", "control_plane"}),
            "service-arm-bindings-required")
    for name, service in config["services"].items():
        require(service["url"] == config[name + "_url"], "service-endpoint-binding-mismatch")
    return contract, registry, action


def expected_target(config):
    return {**config["expected_deployment"], "tenant": config["tenant_id"],
            "subject": config["subject"], "client_id": config["client_id"]}


def configuration_projection(config):
    wiring = config["runtime_configuration"]
    agent = wiring["agent"]
    require(set(agent) == AGENT_ENVIRONMENT
            and agent["GOV_CONTROL_PLANE_URL"] == config["control_plane_url"]
            and agent["GOVERNED_TOOL_GATEWAY_URL"] == config["producer_url"] + "/mcp"
            and agent["TL_GOV_IMAGE_DIGEST"] == config["expected_deployment"]["image_digest"]
            and set(wiring["services"]) == set(config["services"]),
            "declared-runtime-configuration-mismatch")
    return {"agent": project_environment(agent), "services": {
        name: project_environment(value, names=SERVICE_ENVIRONMENT) for name, value in wiring["services"].items()}}


async def health(api, producer, digest):
    value = await api.request("producer", "GET", "/readiness" if producer == "native" else "/health")
    require(value.get("policy_digest") == digest, "health-policy-mismatch")
    if producer == "native":
        require(value.get("mode") == "enforce", "evaluate_only-is-not-enforced")
        require(value.get("bindings") and all(
            b.get("healthy") is True and b.get("mode") == "enforce"
            for b in value["bindings"].values()), "native-dependencies-unhealthy")
    else:
        require(value.get("status") == "ready" and value.get("mode") == "enforce" and value.get("registry_loaded") is True
                and value.get("dependencies") and all(
                    d.get("healthy") is True for d in value["dependencies"].values())
                and value["bindings"]["governance_probe_noop"]["healthy"] is True,
                "gateway-dependencies-unhealthy")
    control = await api.request("control_plane", "GET", "/health", authenticated=False)
    require(control == {"status": "healthy"}, "control-plane-dependencies-unhealthy")
    return {"producer": {"policy_digest": digest, "mode": "enforce",
                         "dependencies_healthy": True, "bindings_healthy": True,
                         "source": "service-health-not-independent-proof"},
            "control_plane": {"status": "healthy"}}


async def invoke(target, run_id, variant, credential, http, timeout):
    """Consume the real Hosted Agent stream. Its content is never evidence."""
    from azure.ai.projects.aio import AIProjectClient
    from govern_control_plane.models import canonical
    prompt = "Invoke only governance_probe_noop with these exact arguments: " + canonical({
        "probe_run_id": run_id, "variant": variant}).decode()
    async with asyncio.timeout(timeout):
        if target["protocol"] == "responses":
            async with AIProjectClient(endpoint=target["project_endpoint"], credential=credential,
                                       allow_preview=True, retry_total=0) as project:
                client = project.get_openai_client(agent_name=target["agent_id"], http_client=http, max_retries=0)
                stream = await client.responses.create(input=prompt, store=False, stream=True,
                    extra_body={"agent_reference": {"type": "agent_reference", "name": target["agent_id"],
                                                     "version": target["agent_version"]}})
                count = 0
                async with stream:
                    async for _event in stream:
                        count += 1
                        require(count <= 2048, "invocation-stream-limit")
        else:
            token = await credential.get_token("https://ai.azure.com/.default")
            url = (target["project_endpoint"] + "/agents/" + target["agent_id"]
                   + "/endpoint/protocols/invocations?api-version=v1")
            async with http.stream("POST", url, headers={"Authorization": "Bearer " + token.token,
                    "Content-Type": "application/json"}, json={"input": prompt}, follow_redirects=False) as response:
                require(response.status_code == 200, "agent-invocation-unavailable")
                size = 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    require(size <= 262144, "invocation-stream-limit")


async def verify_host_bootstrap(config, target, credential, http, signer):
    """Read-only native protocol check; neither inference nor noop effect proof."""
    from govern_control_plane.bootstrap import SignedBootstrap, verify, read_host_binding, BootstrapUnavailable
    from govern_control_plane.models import canonical, parse
    signed = parse(SignedBootstrap, canonical(config["bootstrap"]))
    binding = await verify(signed, signer, tenant_id=config["tenant_id"], key_id=config["policy"]["key_id"])
    selected_policy = config["native_policy"] if target["protocol"] == "responses" else config["policy"]
    require(binding.tenant_id == target["tenant"] and binding.principal == target["subject"]
            and binding.policy_digest == selected_policy["policy_digest"]
            and all(getattr(binding, key) == target[key] for key in (
                "agent_id", "agent_version", "image_digest", "project_endpoint", "subscription",
                "resource_group", "environment", "client_id")), "bootstrap-observed-target-mismatch")
    try:
        await read_host_binding(target, signed, credential=credential, http=http)
    except BootstrapUnavailable:
        raise ProbeEvidenceError("host-bootstrap-unavailable") from None
    await verify(signed, signer, tenant_id=config["tenant_id"], key_id=config["policy"]["key_id"])
    return signed.model_dump(mode="json")


async def collect(config, *, credential, signer, run=observation.run_command, http=None,
                  force=False, timeout=30, poll_interval=0.25, required_target=None):
    """No force override: collection is opt-in, exact-scope and binding-specific."""
    started = now()
    selection = config.get("selection", {})
    selection = {k: v for k, v in selection.items()
                 if k in observation.SELECTORS and (v is None or isinstance(v, str) and len(v) <= 512)
                 } if isinstance(selection, dict) else {}
    report = {"declared_selection": deepcopy(selection),
              "observed_target": None, "governance_health": {"bindings": []},
              "governance_probes": [], "governance_gaps": [], "probe_evidence": [],
              "provenance": "authenticated-service-reads-and-azure-observation-not-attestation"}
    try:
        require(type(timeout) in (int, float) and 0 < timeout <= 120
                and type(poll_interval) in (int, float) and 0 < poll_interval <= 5,
                "invalid-collection-bounds")
        contract, registry, action = preflight(config)
        expected = expected_target(config)
        report["expected_target"] = deepcopy(expected)
        declared_configuration = configuration_projection(config)
        file_digests = validate_digests(config.get("declared_file_digests", {}), DECLARED_FILES)
        runtime_digest = registry.native_policy_digest or config["policy"]["policy_digest"]
        report["governance_health"]["bindings"] = static.bindings(contract, runtime_digest, registry.deployment.environment)
        from govern_gateway.dispatcher import NativePolicy
        policy_config = config["policy"]
        async with asyncio.timeout(timeout):
            policy = await NativePolicy.load(
                bundle_path=Path(policy_config["bundle_path"]), signed=policy_config["signed"], signer=signer,
                tenant=config["tenant_id"], key_id=policy_config["key_id"], policy_id=policy_config["policy_id"],
                version=policy_config["policy_version"], expected_digest=policy_config["policy_digest"],
                allowed_endpoints=config["allowed_endpoints"], gateway_url=registry.gateway_url)
            if config["producer"] == "native":
                native = config["native_policy"]
                require(registry.native_policy_digest == native["policy_digest"]
                        and native["key_id"] == policy_config["key_id"], "native-policy-association-mismatch")
                await verify_native(native, config["tenant_id"], signer)
            else:
                require(registry.native_policy_digest is None, "gateway-policy-association-invalid")
        # Only reached after trusted-key cryptographic verification of every dependency.
        verified_policies = policy_bindings(config)
        target = await asyncio.to_thread(observation.observe, config["selection"], run)
        report["observed_target"] = target
        require_selected_target(expected, required_target if required_target is not None else {})
        deployment = registry.deployment.model_dump(mode="json")
        require_target(target, expected, deployment)
        require(target["configuration_digests"] == declared_configuration["agent"],
                "observed-host-configuration-mismatch")
        require(target["protocol"] == ("responses" if config["producer"] == "native" else "invocations"),
                "runtime-invocation-path-mismatch")
        services = {name: await asyncio.to_thread(observation.observe_service, value,
            target["subscription"], target["resource_group"], run) for name, value in config["services"].items()}
        report["observed_services"] = services
        observed_configuration = {"agent": target["configuration_digests"],
                                  "services": {name: value["configuration_digests"] for name, value in services.items()}}
        report["configuration_evidence"] = {
            "declared": declared_configuration, "observed": observed_configuration,
            "file_visibility": "image-and-mounted-file-interiors-not-observed-by-azure",
            "declared_file_digests": file_digests,
        }
        require(observed_configuration == declared_configuration, "observed-service-configuration-mismatch")
        async with AsyncExitStack() as stack:
            if http is None:
                http = await stack.enter_async_context(httpx.AsyncClient(
                    timeout=timeout, trust_env=False, follow_redirects=False))
            api = API(config, credential, http, timeout)
            if "bootstrap" in config:
                report["bootstrap"] = await verify_host_bootstrap(config, target, credential, http, signer)
            report["governance_health"]["dependencies"] = await health(api, config["producer"], runtime_digest)
            scope = {"registration": {"subject": config["subject"], "action": action.name,
                    "deployment": deployment, "policy_digest": runtime_digest}, "tenant": config["tenant_id"],
                    "binding": action.policy_binding, "fixture_id": action.probe_contract.fixture_id,
                    "producer": config["producer"]}
            report["registration_scope"] = scope
            used = set()
            for variant in ("allow", "deny"):
                policy.fresh()
                if "bootstrap" in config:
                    require(await verify_host_bootstrap(config, target, credential, http, signer)
                            == report["bootstrap"], "bootstrap-changed-during-collection")
                run_id = str(uuid.uuid4())
                require(run_id not in used, "nonce-reused")
                used.add(run_id)
                registration = {**scope["registration"], "variant": variant}
                path = "/governance/probes/" + run_id
                kwargs = {"run": run_id, "registration": registration,
                          **{k: scope[k] for k in ("tenant", "binding", "fixture_id")}}
                record = {"run_id": run_id, "variant": variant}
                previous = {}
                for service in ("producer", "fixture"):
                    kind = config["producer"] if service == "producer" else "fixture"
                    baseline = await api.request(service, "POST", path, registration, status=201)
                    value = state(baseline, **kwargs, producer=kind)
                    fresh(value, started, now())
                    snapshot = await api.request(service, "GET", path + "?subject=" + config["subject"])
                    point = state(snapshot, **kwargs, producer=kind)
                    require(value == point, "registration-point-read-mismatch")
                    record["before_" + service], previous[service] = snapshot, point
                # Check current routing immediately before sending the hosted request.
                require(await asyncio.to_thread(observation.observe, config["selection"], run) == target,
                        "deployment-changed-during-collection")
                await invoke(target, run_id, variant, credential, http, timeout)
                async with asyncio.timeout(timeout):
                    while True:
                        for service in ("producer", "fixture"):
                            raw = await api.request(service, "GET", path + "?subject=" + config["subject"])
                            point = state(raw, **kwargs, producer=previous[service].producer)
                            advance(previous[service], point)
                            previous[service] = point
                            record[service] = raw
                        if previous["producer"].terminal is not None:
                            break
                        await asyncio.sleep(poll_interval)
                event = previous["producer"].events[1]
                require(event.receipt_id is not None, "receipt-association-missing")
                from govern_control_plane.models import DecisionReceipt, canonical, parse
                receipt = await api.request("control_plane", "GET", "/receipts/" + event.receipt_id)
                record["receipt"] = parse(DecisionReceipt, canonical(receipt)).model_dump(mode="json")
                report["probe_evidence"].append(record)
            require(await asyncio.to_thread(observation.observe, config["selection"], run) == target,
                    "deployment-changed-during-collection")
            for name, value in config["services"].items():
                require(await asyncio.to_thread(observation.observe_service, value,
                    target["subscription"], target["resource_group"], run) == services[name],
                    "service-changed-during-collection")
            for record in report["probe_evidence"]:
                for service in ("producer", "fixture"):
                    current = await api.request(service, "GET",
                        "/governance/probes/" + record["run_id"] + "?subject=" + config["subject"])
                    require(current == record[service], "probe-state-changed-after-receipt")
            report["governance_health"]["dependencies"] = await health(api, config["producer"], runtime_digest)
            policy.fresh()
            if config["producer"] == "native":
                await verify_native(config["native_policy"], config["tenant_id"], signer)
            require(policy_bindings(config) == verified_policies, "signed-policy-changed-during-collection")
            report["verified_policies"] = verified_policies
            finished = now()
            report["started_at"], report["finished_at"] = started.isoformat(), finished.isoformat()
            report["governance_probes"] = evaluate_pair(
                report["probe_evidence"], target=target, expected_target=expected, registration_scope=scope,
                started_at=started, finished_at=finished)
            for binding in report["governance_health"]["bindings"]:
                if binding["tool_id"] == action.name and binding["intervention_points"] == ["pre_tool_call"]:
                    binding.update(status="enforced",
                        probe_ids=[p["probe_id"] for p in report["governance_probes"]],
                        evidence_refs=[p[key] for p in report["governance_probes"]
                                       for key in ("decision_receipt_ref", "service_oracle_ref")])
                elif binding["enforcement_path"] != "none":
                    report["governance_gaps"].append("binding-live-evidence-missing:" + binding["binding_id"])
            report["governance_manifest"] = manifest(report, config)
    except Exception as error:
        reason = str(error) if isinstance(error, (ProbeEvidenceError, observation.ObservationError)) else (
            "probe-not-terminal-or-invocation-timeout" if isinstance(error, TimeoutError) else "probe-collection-unavailable")
        report["governance_gaps"].append(reason)
        report["governance_probes"] = []
        for binding in report["governance_health"]["bindings"]:
            if binding["enforcement_path"] != "none":
                binding.update(status="unverified", probe_ids=[], evidence_refs=[])
    return report


def manifest(report, config):
    """Same shared binding/probe contract; no fabricated SDK wheel attestations."""
    from skills._shared.governance import validate_governance_manifest
    from govern_control_plane.models import SignedBundle, canonical, parse
    target, bindings = report["observed_target"], deepcopy(report["governance_health"]["bindings"])
    policy = config["native_policy"] if config["producer"] == "native" else config["policy"]
    envelope = parse(SignedBundle, canonical(policy["signed"])).envelope.model_dump(mode="json")
    coverage = {"tools_total": len(bindings),
                "tools_bound": sum(b["enforcement_path"] != "none" for b in bindings)}
    coverage.update({"tools_" + status: sum(b["status"] == status for b in bindings)
                     for status in ("enforced", "observed", "unbound", "unverified", "unsupported", "bypassable")})
    value = {
        "schema": "threadlight-governance-manifest/v1",
        "agent": {"runtime": config["contract"]["framework"], "version": target["agent_version"],
                  "image_digest": target["image_digest"]},
        "policy_bundle": {"id": envelope["policy_id"], "version": envelope["version"],
                          "digest": envelope["content_digest"], "signature_verified": True,
                          "expires_at": envelope["expires_at"]},
        "enforcement": {"adapter": "local-agent-hooks" if config["producer"] == "native" else "governed-tool-gateway",
                        "mode": "enforce"},
        "coverage": coverage, "bindings": bindings, "live_probes": deepcopy(report["governance_probes"]),
        "gaps": [{"binding_id": b["binding_id"], "status": b["status"],
                  "reason_code": "intentionally-unbound" if b["status"] == "unbound" else "binding-live-evidence-missing",
                  "evidence_refs": []} for b in bindings if b["status"] != "enforced"],
        "collection_evidence": {
            "source": report["provenance"], "declared_selection": report["declared_selection"],
            "observed_target": target, "started_at": report["started_at"], "finished_at": report["finished_at"],
            "expected_target": report["expected_target"], "configuration": report["configuration_evidence"],
            "verified_policies": report["verified_policies"],
            **({"bootstrap": report["bootstrap"]} if "bootstrap" in config else {}),
            "registration_scope": report["registration_scope"], "records": report["probe_evidence"]},
    }
    return validate_governance_manifest(value)


def validate_fixture_identity(producer, fixture, binding, *, native):
    if native and producer.credential_mode == "platform-noop":
        require(fixture.fixture_callers == {binding["agent_principal"]: binding["agent_client_id"]}
                and fixture.service_client_id != binding["agent_client_id"],
                "native-fixture-writer-must-remain-separate")
    else:
        require(binding["agent_principal"] not in fixture.fixture_callers
                and binding["downstream_principal"] in fixture.fixture_callers
                and fixture.fixture_callers[binding["downstream_principal"]] == binding["downstream_client"],
                "fixture-credentials-not-exclusive")


def load_configuration(project, configuration):
    """Resolve operator inputs against Task10's frozen package and bound services."""
    from govern_control_plane.auth import Settings
    from govern_control_plane.models import canonical, parse
    from govern_gateway.probe_runtime import ProbeConfiguration
    from govern_gateway.server import Configuration
    from govern_control_plane.app import AzureConfiguration
    options = static.read(static.contained(project, configuration.relative_to(project)))
    require(set(options) == {"schema", "selection", "controller_principal", "controller_client_id",
                             "bundle_path", "signed_envelope_path", "fixture_configuration_file", "services"}
            and options["schema"] == "threadlight-governance-probe-input/v1", "invalid-collector-input")
    package = static.read(static.contained(project, ".threadlight/governance-package.json"))
    deployment = static.read(static.contained(project, ".threadlight/governance-deployment.json"))
    require(deployment["schema"] == "threadlight-governance-deployment/v1", "bound-deployment-required")
    binding, packaged = deployment["bindings"], package["configuration"]
    agent, frozen = static.generator().frozen_configuration(project, package)
    native = package["framework"] == "microsoft-agent-framework"
    producer = parse(ProbeConfiguration if native else Configuration,
                     canonical(binding["native_probe_config" if native else "gateway_config"]))
    control = parse(AzureConfiguration, canonical(binding["control_config"]))
    fixture = parse(ProbeConfiguration, canonical(static.read(
        static.contained(project, options["fixture_configuration_file"]))))
    require(fixture.producer == "fixture" and (producer.enabled if native else producer.probe_enabled) is True,
            "explicit-installed-producer-and-fixture-required")
    require(producer.tenant_id == fixture.tenant_id == control.tenant_id == packaged["tenant_id"]
            and producer.key_id == fixture.key_id == control.key_id == packaged["key_id"]
            and producer.policy_id == fixture.policy_id and producer.policy_version == fixture.policy_version
            and producer.policy_digest == fixture.policy_digest
            and producer.gateway_url == fixture.gateway_url
            and set(producer.allowed_endpoints) == set(fixture.allowed_endpoints),
            "fixture-and-producer-binding-disagree")
    require(control.audience == packaged["control_plane_scope"][6:-9]
            and binding["gateway_config"]["control_plane_url"] == packaged["control_plane_url"],
            "control-plane-binding-disagrees")
    validate_fixture_identity(producer, fixture, binding, native=native)
    for service, principal, client, image in (
        ("control_plane", binding["control_principal"], binding["control_client"], deployment["images"]["control_plane"]),
        *(() if native else (("producer", binding["gateway_principal"], binding["gateway_client"],
                              deployment["images"]["gateway"]),)),
    ):
        expected = options["services"][service]
        require(expected["principal_id"] == principal and expected["client_id"] == client
                and expected["image"] == image, "service-identity-or-image-binding-disagrees")
    require(options["services"]["fixture"]["client_id"] == fixture.service_client_id,
            "fixture-managed-identity-mismatch")
    signed = static.read(static.contained(project, options["signed_envelope_path"]))
    policy = {"bundle_path": str(static.contained(project, options["bundle_path"])), "signed": signed,
              **{k: getattr(producer, k) for k in ("policy_id", "policy_version", "policy_digest", "key_id")}}
    runtime_policy = None
    if native:
        runtime_policy = {"bundle_path": str(agent / "policy"), "signed": static.read(agent / "policy-envelope.json"),
                          **{k: frozen[k] for k in ("policy_id", "policy_version", "policy_digest", "key_id")}}
        require(producer.expected_deployment == fixture.expected_deployment
                and producer.expected_deployment.image_digest == deployment["images"]["agent"].split("@")[1]
                and producer.expected_deployment.agent_version == binding["agent_version"],
                "native-deployment-association-mismatch")
    def auth(settings):
        return {name: value for name, value in settings.model_dump(mode="json").items() if name in Settings.model_fields}
    import yaml
    service = yaml.safe_load(static.contained(project, "azure.yaml").read_text())["services"][packaged["agent_service"]]
    host_environment = static.host_environment(project, agent, service, packaged)
    service_environments = {name: {} for name in options["services"]}
    service_environments["control_plane"] = {
        "TL_GOV_SERVICE": "control-plane", "AZURE_CLIENT_ID": binding["control_client"],
        "GOV_CONFIG_JSON": canonical(control).decode(),
    }
    if not native:
        service_environments["producer"] = {
            "TL_GOV_SERVICE": "gateway", "GATEWAY_CONFIG_JSON": canonical(producer).decode(),
        }
    declared_files = {
        "host": configuration_digest({key: frozen[key] for key in (
            "contract", "environment", "tenant_id", "agent_id", "control_plane_url", "control_plane_scope",
            "gateway_url", "gateway_scope", "policy_id", "policy_version", "policy_digest", "key_id",
            "approver_roles", "probe_observability") if key in frozen}),
        "fixture": configuration_digest(fixture.model_dump(mode="json")),
    }
    if native:
        declared_files["native_probe"] = configuration_digest(producer.model_dump(mode="json"))
    bootstrap = {}
    if "remote_bootstrap" in frozen:
        import hashlib
        from govern_control_plane.bootstrap import SignedBootstrap, BootstrapReference
        reference = parse(BootstrapReference, canonical(frozen["remote_bootstrap"]))
        signed_bootstrap = parse(SignedBootstrap, canonical(static.read(
            static.contained(project, ".threadlight/hosted-bootstrap.json"))))
        require(signed_bootstrap.binding.reference == reference.reference
                and signed_bootstrap.binding.native_policy_digest == reference.native_policy_digest
                and signed_bootstrap.binding.config_digest == "sha256:" + hashlib.sha256(canonical(frozen)).hexdigest(),
                "frozen-bootstrap-configuration-mismatch")
        bootstrap["bootstrap"] = signed_bootstrap.model_dump(mode="json")
    return {
        "schema": "threadlight-governance-probe/v1", "producer": "native" if native else "gateway",
        "selection": options["selection"], "contract": package["contract"],
        "tenant_id": packaged["tenant_id"], "subject": binding["agent_principal"], "client_id": binding["agent_client_id"],
        "policy": policy, "native_policy": runtime_policy, "producer_url": producer.gateway_url.removesuffix("/mcp"),
        "producer_scope": f"api://{producer.audience}/.default",
        "fixture_url": options["services"]["fixture"]["url"], "fixture_scope": f"api://{fixture.audience}/.default",
        "control_plane_url": packaged["control_plane_url"], "control_plane_scope": packaged["control_plane_scope"],
        "allowed_endpoints": producer.allowed_endpoints, "services": options["services"],
        "auth": {"producer": auth(producer), "fixture": auth(fixture), "control_plane": auth(control)},
        "controller_principal": options["controller_principal"], "controller_client_id": options["controller_client_id"],
        "expected_deployment": {
            "agent_id": packaged["agent_id"], "agent_version": binding["agent_version"],
            "image_digest": deployment["images"]["agent"].split("@")[1],
            "environment": packaged["environment"],
            "subscription": packaged.get("subscription", options["selection"]["project_resource_id"].split("/")[2]),
            "resource_group": packaged.get("resource_group", options["selection"]["resource_group"]),
        },
        "runtime_configuration": {"agent": host_environment, "services": service_environments},
        "declared_file_digests": declared_files,
        **bootstrap,
    }


async def collect_project(project, configuration=None, *, credential=None, signer=None,
                          run=observation.run_command, http=None, timeout=30, force=False, manifest_path=None,
                          required_target=None):
    project = Path(project).resolve()
    selected_manifest = Path(manifest_path or "specs/manifest.json")
    if selected_manifest.is_absolute():
        selected_manifest = selected_manifest.relative_to(project)
    document = static.read(static.contained(project, selected_manifest))
    if not static.enabled(document):
        return {}
    declaration = static.check(project, document)
    failure = {"governance_health": declaration, "governance_probes": [],
               "governance_gaps": list(declaration["gaps"])}
    configuration = Path(configuration or project / ".threadlight/governance-probe.json")
    if not configuration.is_file():
        failure["governance_gaps"].append("no-explicit-safe-probe-configuration")
        return failure
    try:
        require(not declaration["gaps"], "static-governance-gaps-remain")
        config = load_configuration(project, configuration)
        preflight(config)  # No token, CLI, Key Vault or model call before the safe opt-in check.
        from govern_control_plane.app import configure_logging
        configure_logging()
        async with AsyncExitStack() as stack:
            if credential is None:
                from azure.identity.aio import DefaultAzureCredential
                credential = await stack.enter_async_context(DefaultAzureCredential(
                    managed_identity_client_id=config["controller_client_id"],
                    exclude_environment_credential=True, exclude_workload_identity_credential=True,
                    exclude_cli_credential=True, exclude_powershell_credential=True,
                    exclude_developer_cli_credential=True, exclude_shared_token_cache_credential=True,
                    exclude_visual_studio_code_credential=True, exclude_interactive_browser_credential=True,
                    exclude_broker_credential=True))
            if signer is None:
                from azure.keyvault.keys.aio import KeyClient
                from azure.keyvault.keys.crypto.aio import CryptographyClient
                from govern_control_plane.storage import KeyVaultSigner
                key_id = config["policy"]["key_id"]
                crypto = await stack.enter_async_context(CryptographyClient(
                    key_id, credential=credential, retry_total=0, connection_timeout=5, read_timeout=5))
                keys = await stack.enter_async_context(KeyClient(
                    key_id.split("/keys/")[0], credential=credential, retry_total=0,
                    connection_timeout=5, read_timeout=5))
                signer = KeyVaultSigner(crypto, key_client=keys)
            return await collect(config, credential=credential, signer=signer, run=run, http=http,
                                 timeout=timeout, force=force, required_target=required_target)
    except Exception as error:
        failure["governance_gaps"].append(str(error) if isinstance(error, ProbeEvidenceError)
                                        else "collector-configuration-or-dependency-unavailable")
        return failure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("specs/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path(".threadlight/governance-live.json"))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--force", action="store_true", help="Does not override any probe safety requirement")
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve()
        configuration = args.configuration
        if configuration is not None and not configuration.is_absolute():
            configuration = static.contained(project, configuration)
        report = asyncio.run(collect_project(
            project, configuration, timeout=args.timeout, force=args.force, manifest_path=args.manifest))
        if not report:
            return 0
        output = static.contained(project, args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Payload-free but scoped deployment identifiers still deserve private permissions.
        import os
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"governance_gaps": report["governance_gaps"]}))
        return int(bool(report["governance_gaps"]))
    except Exception:
        print('{"governance_gaps":["collector-prerequisite-unavailable"]}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
