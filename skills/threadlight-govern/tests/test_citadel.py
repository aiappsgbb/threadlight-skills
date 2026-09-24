"""Citadel contract and dual-policy PEP regressions; cloud boundaries stay explicit."""
import asyncio
from copy import deepcopy
import sys

import pytest

from test_control_plane import APP, DIGEST, KEY, OTHER, TENANT, WORKLOAD, Harness, module as cp
from test_gateway import GatewayHarness, gateway, registry


def contract():
    action = registry()["actions"][0]
    policy = {"policy_id": "safe", "version": "1", "digest": DIGEST, "key_id": KEY}
    return {
        "schema": "threadlight-citadel-binding/v1",
        "generation": "1",
        "tenant_id": TENANT,
        "deployment": registry()["deployment"],
        "gateway_url": "https://gateway.example/mcp",
        "public_url": "https://citadel.example/mcp/returns",
        "proxy": {"principal": OTHER, "client": "77777777-7777-7777-7777-777777777777",
                  "audience": "api://pep"},
        "producer": {
            "schema": "threadlight-citadel-producer/v1", "contract_id": "returns",
            "version": "1", "tenant_id": TENANT, "policy": policy,
            "action": action, "facts_provider": "host-identity-v1",
            "effect_protocol": "registered-http-v1",
        },
        "consumer": {
            "schema": "threadlight-citadel-consumer/v1", "contract_id": "returns-agent",
            "version": "1", "tenant_id": TENANT,
            "policy": {**policy, "policy_id": "consumer", "digest": "sha256:" + "b" * 64},
            "principal": WORKLOAD, "client": APP, "audience": "api://consumer",
            "producer_id": "returns", "producer_version": "1",
            "action": deepcopy(action),
        },
    }


def citadel():
    from pathlib import Path
    assert (Path(__file__).resolve().parents[1] / "references/gateway/citadel.py").exists(), (
        "Missing versioned Citadel contract and native composition")
    return gateway("citadel")


def test_citadel_valid_contract_preserves_full_binding():
    api = citadel()
    binding = cp("models").parse(api.Binding, cp("models").canonical(contract()))
    effective = binding.registry()
    assert effective.deployment.model_dump(mode="json") == registry()["deployment"]
    assert effective.actions[0].model_dump(mode="json") == gateway("dispatcher").Action.model_validate(
        registry()["actions"][0]).model_dump(mode="json")
    assert binding.consumer_digest != binding.producer_digest
    assert binding.registry().tenant_id == TENANT


@pytest.mark.parametrize("fault", [
    "unknown", "missing", "tenant", "target", "schema", "workload", "producer-version",
    "proxy-caller", "audience", "dual-review", "reserved-context", "protocol",
])
def test_citadel_conflicting_contract_fails_closed(fault):
    api = citadel()
    doc = contract()
    if fault == "unknown":
        doc["allow"] = True
    elif fault == "missing":
        del doc["producer"]
    elif fault == "tenant":
        doc["consumer"]["tenant_id"] = OTHER
    elif fault == "target":
        doc["consumer"]["action"]["endpoint"] = "https://different.example/refunds"
    elif fault == "schema":
        doc["consumer"]["action"]["input_schema"]["properties"]["amount"]["maximum"] = 999
    elif fault == "workload":
        doc["producer"]["action"]["workloads"] = [OTHER]
    elif fault == "producer-version":
        doc["consumer"]["producer_version"] = "2"
    elif fault == "proxy-caller":
        doc["proxy"]["principal"] = WORKLOAD
    elif fault == "audience":
        doc["proxy"]["audience"] = doc["consumer"]["audience"]
    elif fault == "dual-review":
        for owner, role in (("producer", "Finance"), ("consumer", "Operations")):
            doc[owner]["action"]["approval_roles"] = [role]
            doc[owner]["action"]["approval_mode"] = "deferred"
    elif fault == "reserved-context":
        for owner in ("producer", "consumer"):
            doc[owner]["action"]["input_schema"]["properties"]["governance_operation_id"] = {
                "type": "string", "maxLength": 64}
    else:
        doc["producer"]["effect_protocol"] = "arbitrary-mcp"
    with pytest.raises(ValueError):
        cp("models").parse(api.Binding, cp("models").canonical(doc))


def test_citadel_review_and_confirmation_remain_independent():
    api = citadel()
    doc = contract()
    doc["producer"]["action"].update(approval_roles=["Finance"], approval_mode="deferred")
    doc["consumer"]["action"]["confirmation_requirement"] = {
        "trigger": "policy", "provider_profile": "requester", "max_age_seconds": 300}
    binding = cp("models").parse(api.Binding, cp("models").canonical(doc))
    selected = binding.registry().actions[0]
    assert selected.approval_roles == ["Finance"]
    assert selected.confirmation_requirement.provider_profile == "requester"
    assert selected.approval_mode == "deferred"


@pytest.mark.parametrize(("producer", "consumer", "expected"), [
    ("allow", "allow", "allow"), ("deny", "allow", "deny"),
    ("allow", "deny", "deny"), ("escalate", "allow", "escalate"),
    ("transform", "allow", "unsupported"),
])
def test_citadel_same_input_and_deny_precedence(producer, consumer, expected):
    api = citadel()
    doc = contract()
    doc["producer"]["action"].update(approval_roles=["Finance"], approval_mode="deferred")
    binding = cp("models").parse(api.Binding, cp("models").canonical(doc))
    seen = []

    class Policy:
        def __init__(self, decision):
            self.decision = decision

        async def evaluate(self, point, action, arguments, safe, result=None):
            seen.append(deepcopy((arguments, safe)))
            arguments["amount"] = 900  # A policy may not contaminate the other input.
            return self.decision, arguments if self.decision == "transform" else {"amount": 5}

    async def case():
        args, safe = {"amount": 5}, {"scope": "refunds"}
        if expected == "unsupported":
            with pytest.raises(gateway("dispatcher").GateError, match="citadel_transform_unsupported"):
                await api.evaluate_pair(
                    binding, Policy(producer), Policy(consumer), "pre_tool_call", args, safe)
        else:
            outcome, actual = await api.evaluate_pair(
                binding, Policy(producer), Policy(consumer), "pre_tool_call", args, safe)
            assert (outcome, actual) == (expected, args)
        assert seen == [(args, safe), (args, safe)]
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["none", "missing", "spoof", "spoof-policy", "audience", "direct", "duplicate", "tenant"])
def test_citadel_proxy_and_original_caller_are_independently_authenticated(fault):
    async def case():
        api = citadel()
        binding = cp("models").parse(api.Binding, cp("models").canonical(contract()))
        h = Harness()
        proxy = binding.proxy
        proxy_settings = h.settings.model_copy(update={
            "audience": proxy.audience,
            "workloads": {proxy.principal: cp("auth").Workload(
                client_id=proxy.client, agent_id="proxy", policies=["safe"])}})
        caller_settings = h.settings.model_copy(update={"audience": binding.consumer.audience})
        ingress = api.CitadelIngress(
            binding, cp("auth").EntraAuth(caller_settings, h.http),
            cp("auth").EntraAuth(proxy_settings, h.http))
        headers = [(b"authorization", ("Bearer " + h.token(changes={
            "aud": proxy.audience, "oid": proxy.principal, "azp": proxy.client})).encode()),
            (api.CALLER_HEADER, ("Bearer " + h.token(changes={
                "aud": "wrong" if fault == "audience" else binding.consumer.audience,
                "tid": OTHER if fault == "tenant" else TENANT})).encode())]
        if fault == "missing":
            headers.pop()
        elif fault == "spoof":
            headers.append((b"x-enduser-id", WORKLOAD.encode()))
        elif fault == "spoof-policy":
            headers.append((b"x-policy-digest", DIGEST.encode()))
        elif fault == "direct":
            headers[0] = (b"authorization", headers[1][1])
        elif fault == "duplicate":
            headers.append(headers[1])
        try:
            if fault == "none":
                request = await ingress.authenticate(headers)
                assert request.identity.subject == WORKLOAD
                assert request.identity.client == APP
            else:
                with pytest.raises(cp("auth").Unauthorized):
                    await ingress.authenticate(headers)
        finally:
            await h.close()
    asyncio.run(case())


async def native_pair(path, producer="allow", consumer="allow", *, review=False, confirmation=False, document=None):
    """Actual ACS bundles, signature verification and the unchanged HTTP PEP."""
    from test_policy_bundle import bundle_module
    from datetime import datetime, timedelta, timezone
    import json
    api = citadel()
    doc = deepcopy(document) if document is not None else contract()
    doc["consumer"]["audience"] = "api://governance"
    if review:
        doc["producer"]["action"].update(approval_roles=["Approver"], approval_mode="deferred")
    if confirmation:
        from test_user_confirmation import selection
        doc["consumer"]["action"]["confirmation_requirement"] = selection()
    owners = []
    for name, decision in (("producer", producer), ("consumer", consumer)):
        h = await GatewayHarness().initialize(
            path / name, policy_id=name, decision={"decision": decision},
            document={**registry(), "deployment": doc["deployment"],
                      "gateway_url": doc["gateway_url"], "tenant_id": doc["tenant_id"],
                      "actions": [doc[name]["action"]]})
        doc[name]["policy"].update(policy_id=name, digest=h.policy.digest)
        owners.append(h)
    binding = cp("models").parse(api.Binding, cp("models").canonical(doc))
    h = await GatewayHarness().initialize(path / "generation", policy_id="unused",
                                         document=binding.registry().model_dump(mode="json", by_alias=True))
    h.cp.settings.workloads[WORKLOAD] = h.cp.settings.workloads[WORKLOAD].model_copy(
        update={"policies": ["safe"]})
    source = path / "generation/source"
    (source / "citadel-binding.json").write_bytes(cp("models").canonical(doc))
    built = bundle_module().build_bundle(
        source=source, destination=path / "generation/final", policy_id="safe", version="1")
    envelope = cp("models").parse(cp("models").BundleEnvelope, json.dumps({
        "policy_id": "safe", "version": "1", "content_digest": built.bundle_digest,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "tenant_id": TENANT, "key_id": KEY}).encode())
    signed = await h.cp.service.publish(envelope)
    generation = await gateway("dispatcher").NativePolicy.load(
        bundle_path=built.root, signed=signed, signer=h.cp.signer,
        tenant=TENANT, key_id=KEY, policy_id="safe", version="1",
        expected_digest=built.bundle_digest, gateway_url=doc["gateway_url"],
        citadel_generation=True,
        allowed_endpoints=[doc["producer"]["action"][k] for k in ("endpoint", "outcome_endpoint")])
    envelopes = {}
    for name, owner in zip(("producer", "consumer"), owners):
        signed_owner = cp("models").parse(cp("models").SignedBundle, cp("models").canonical(owner.signed))
        envelopes[name, "1"] = await h.cp.service.publish(signed_owner.envelope)
    h.policy = await api.CitadelPolicy.load(
        generation=generation, producer_path=owners[0].bundle.root,
        consumer_path=owners[1].bundle.root, envelopes=envelopes, signer=h.cp.signer)
    h.dispatcher = h.new_dispatcher()
    for owner in owners:
        await owner.close()
    return h


@pytest.mark.governance_runtime
@pytest.mark.parametrize(("producer", "consumer", "effects"), [
    ("allow", "allow", 1), ("deny", "allow", 0), ("allow", "deny", 0),
])
def test_citadel_native_dispatch_binds_both_policies(tmp_path, producer, consumer, effects):
    async def case():
        h = await native_pair(tmp_path, producer, consumer)
        try:
            result = await h.call()
            assert result["status"] == ("completed" if effects else "blocked")
            assert len(h.calls) == effects
            assert h.receipt_bodies()[0]["policy_digest"] == h.policy.digest
            if effects:
                headers = h.calls[0].headers
                assert headers["X-Citadel-Binding"] == gateway("dispatcher").digest(h.policy.binding)
                assert headers["X-Citadel-Producer-Policy"] == h.policy.producer.digest
                assert headers["X-Citadel-Consumer-Policy"] == h.policy.consumer.digest
                assert (await h.call()) == result
                assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


def test_citadel_effect_wire_includes_generation_and_both_policy_digests():
    import httpx
    from test_gateway import Credential
    async def case():
        api = citadel()
        b = cp("models").parse(api.Binding, cp("models").canonical(contract()))
        requests = []
        async def remote(request):
            requests.append(request)
            return httpx.Response(200, json={"receipt_id": "one", "result": {"status": "refunded"}})
        downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(), transport=httpx.MockTransport(remote))
        try:
            await downstream.request(
                action=b.registry().actions[0], arguments={"amount": 5}, key="one",
                action_hash=DIGEST, provenance="receipt",
                facts={"tenant": TENANT, "subject": WORKLOAD, "action": "refund", "policy": DIGEST,
                       "deployment": contract()["deployment"], "citadel": b.provenance()},
                guard=lambda: None)
            assert requests[0].headers["X-Citadel-Binding"] == gateway("dispatcher").digest(b)
            assert requests[0].headers["X-Citadel-Producer-Policy"] == b.producer.policy.digest
            assert requests[0].headers["X-Citadel-Consumer-Policy"] == b.consumer.policy.digest
        finally:
            await downstream.aclose()
    asyncio.run(case())


def test_citadel_overlay_is_opt_in_preserves_baseline_and_never_buffers():
    import xml.etree.ElementTree as ET
    api = citadel()
    binding = cp("models").parse(api.Binding, cp("models").canonical(contract()))
    overlay = gateway("citadel_package")
    assert overlay.policies(None, asset_id="returns", publish="original", access="access") == (
        "original", "access")
    publish, access = overlay.policies(binding, asset_id="returns")
    root = ET.fromstring(publish)
    fragments = {node.attrib["fragment-id"] for node in root.iter("include-fragment")}
    assert {"security-handler", "mcp-usage", "raise-alert-events"} <= fragments
    assert root.find("backend/forward-request").attrib["buffer-response"] == "false"
    assert "context.Response.Body" not in publish
    assert "retry" not in publish
    assert "x-threadlight-consumer-authorization" in publish
    assert root.find(".//authentication-managed-identity").attrib["resource"] == binding.proxy.audience
    product = ET.fromstring(access)
    assert product.find(".//set-variable[@name='contractToolApis']").attrib["value"] == "returns"
    assert product.find(".//set-variable[@name='jwtAudience']").attrib["value"] == binding.consumer.audience
    assert product.find(".//set-variable[@name='jwtIssuer']").attrib["value"] == (
        f"https://login.microsoftonline.com/{binding.tenant_id}/v2.0")
    assert "context.Api.Id" in access
    assert product.find(".//return-response/set-status").attrib["code"] == "403"
    with pytest.raises(ValueError, match="custom_policy"):
        overlay.policies(binding, asset_id="returns", publish="unreviewed")


def test_citadel_container_installs_declared_service_version():
    from pathlib import Path
    import tomllib
    root = Path(__file__).resolve().parents[1] / "references/gateway"
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert f"threadlight-govern-gateway=={version}" in (root / "Dockerfile").read_text()


def test_citadel_rawxml_hook_preserves_csharp_expressions_without_unescaping_literals():
    api = gateway("citadel_package")
    source = '<policies><inbound><when condition="@(x == &quot;ok&quot; &amp;&amp; y &lt; 2)" />' \
             '<set-header name="example"><value>A &amp; B</value></set-header></inbound></policies>'
    result = api.raw_policy_xml(source)
    assert 'condition="@(x == "ok" && y < 2)"' in result
    assert '<value>A &amp; B</value>' in result


def test_citadel_native_apim_contract_uses_observed_endpoint_map_and_origin():
    api = gateway("citadel_package")
    binding = cp("models").parse(citadel().Binding, cp("models").canonical(contract()))
    result = api.mcp_api_properties(binding)
    assert result["type"] == "mcp"
    assert result["path"] == "mcp/returns"
    assert result["serviceUrl"] == "https://gateway.example"
    assert result["mcpProperties"]["endpoints"] == {"message": {"uriTemplate": "/"}}
    assert "mcpPropperties" not in result
    assert result["subscriptionRequired"] is True


def ingress_for(h):
    b = h.policy.binding
    settings = h.cp.settings.model_copy(update={
        "audience": b.proxy.audience,
        "workloads": {b.proxy.principal: cp("auth").Workload(
            client_id=b.proxy.client, agent_id="proxy", policies=["safe"])}})
    h.dispatcher.ingress = citadel().CitadelIngress(
        b, h.cp.auth, cp("auth").EntraAuth(settings, h.cp.http))
    return {
        "Authorization": "Bearer " + h.cp.token(changes={
            "aud": b.proxy.audience, "oid": b.proxy.principal, "azp": b.proxy.client}),
        "x-threadlight-consumer-authorization": "Bearer " + h.cp.token(),
        "Idempotency-Key": "one",
    }


@pytest.mark.governance_runtime
def test_citadel_native_sdk_mcp_and_rest_share_exact_operation(tmp_path):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async def case():
        h = await native_pair(tmp_path)
        headers = ingress_for(h)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url="https://gateway.example", headers=headers) as http:
                    async with streamable_http_client(
                            "https://gateway.example/mcp", http_client=http,
                            terminate_on_close=False) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            initialized = await session.initialize()
                            assert initialized.protocolVersion >= "2025-06-18"
                            tools = (await session.list_tools()).tools
                            assert [t.name for t in tools] == ["refund"]
                            result = await session.call_tool("refund", {"amount": 5})
                            assert result.structuredContent["status"] == "completed"
                    response = await http.post("/operations/refund", json={"arguments": {"amount": 5}})
                    assert response.status_code == 200
                    assert response.json() == result.structuredContent
                    assert len(h.calls) == 1
                    for extra in (
                        {"Authorization": headers["x-threadlight-consumer-authorization"]},
                        {"Host": "citadel.example"}, {"Origin": "https://untrusted.example"},
                        {"x-enduser-id": WORKLOAD},
                    ):
                        denied = await http.post("/operations/refund",
                            json={"arguments": {"amount": 5}}, headers=extra)
                        assert denied.status_code == 401
                    unknown = await http.post("/operations/shell", json={"arguments": {}})
                    assert unknown.status_code == 403
                    assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault", ["consumer-expiry", "consumer-tamper", "facts-drift", "audit-down", "lost-ack"])
def test_citadel_native_terminal_failures_never_add_effects(tmp_path, fault):
    async def case():
        h = await native_pair(tmp_path)
        try:
            async def wait():
                if fault == "consumer-expiry":
                    h.policy.consumer.deadline = 0
                elif fault == "consumer-tamper":
                    (h.policy.consumer.bundle.root / "safe.rego").write_text("package tampered")
                elif fault == "facts-drift":
                    h.dispatcher.safe_provider = lambda facts: {"scope": facts["scope"], "changed": True}
            if fault in ("consumer-expiry", "consumer-tamper", "facts-drift"):
                h.credential.hook = wait
            elif fault == "audit-down":
                h.cp.store.failed = True
            else:
                h.downstream_status = 503
            result = await h.call()
            assert result["status"] != "completed", result
            count = len(h.calls)
            assert count == int(fault == "lost-ack")
            result = await h.call()
            assert result["status"] != "completed"
            assert len(h.calls) == count
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_citadel_native_confirmation_and_reviewer_both_bind_effective_generation(tmp_path):
    from test_gateway import Credential
    from test_user_confirmation import USER, configure_confirmation, register_context
    from test_confirmation_gateway import decide
    async def case():
        h = await native_pair(tmp_path, review=True, confirmation=True)
        await configure_confirmation(h.cp)
        h.confirming_user = USER
        h.dispatcher.confirmations = cp("confirmation_client").ConfirmationClient(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client)
        h.dispatcher.approvals = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client)
        context = await register_context(h.cp)
        assert context.status_code == 200
        h.context_ref = context.json()["context_ref"]
        try:
            pending = await asyncio.wait_for(h.call(requesting_user_context=h.context_ref), 2)
            assert pending["status"] == "pending_confirmation", pending
            assert not h.calls
            await decide(h, pending)
            review = await h.call(requesting_user_context=h.context_ref)
            assert review["status"] == "pending_approval", review
            assert review["approval_intent"]["policy_hash"] == h.policy.digest
            assert not h.calls
            assert (await h.cp.post("decide", human=True, intent=review["approval_intent"],
                                   approved=True, approving_role="Approver")).status_code == 200
            result = await h.call(requesting_user_context=h.context_ref)
            assert result["status"] == "completed", result
            assert await h.call(requesting_user_context=h.context_ref) == result
            assert len(h.calls) == 1
            intents = [body["confirmation_intent"] for body, _ in h.store.docs.values()
                       if "confirmation_intent" in body]
            assert not intents  # Permanent completed ledger keeps hashes, not authority payloads.
        finally:
            await h.cp.confirmation_http.aclose()
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_citadel_native_assembler_and_mixed_generation_fail_closed(tmp_path):
    async def case():
        h = await native_pair(tmp_path)
        try:
            b = h.policy.binding
            assembled = gateway("citadel_package").assemble(
                b, producer_path=h.policy.producer.bundle.root, consumer_path=h.policy.consumer.bundle.root,
                destination=tmp_path / "assembled", policy_id="generation", version="2")
            raw = (assembled.root / "citadel-binding.json").read_bytes()
            assert cp("models").parse(citadel().Binding, raw) == b
            with pytest.raises(ValueError):
                gateway("citadel_package").assemble(
                    b, producer_path=h.policy.consumer.bundle.root,
                    consumer_path=h.policy.producer.bundle.root,
                    destination=tmp_path / "bad", policy_id="generation", version="2")
            assert not (tmp_path / "bad").exists()
        finally:
            await h.close()
    asyncio.run(case())


def test_citadel_producer_reconstructs_exact_effective_request_facts():
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    path = Path(__file__).resolve().parents[3] / (
        "skills/threadlight-deploy/references/governance/returns_mcp_backend.py")
    spec = importlib.util.spec_from_file_location("citadel_returns_backend", path)
    backend = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = backend
    spec.loader.exec_module(backend)
    b = cp("models").parse(citadel().Binding, cp("models").canonical(contract()))
    config = SimpleNamespace(tenant_id=TENANT, agent_subject=WORKLOAD, policy_digest=DIGEST,
        deployment=contract()["deployment"], workloads={WORKLOAD: SimpleNamespace(client_id=APP)},
        citadel_binding=b)
    facts = backend.operation_facts(config)
    assert facts["citadel"] == b.provenance()
    assert facts["policy"] == DIGEST
    config.citadel_binding = None
    assert "citadel" not in backend.operation_facts(config)


@pytest.mark.governance_runtime
def test_citadel_native_returns_producer_authenticates_and_records_once(tmp_path, monkeypatch):
    import importlib.util
    import json
    from pathlib import Path
    import httpx
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    from test_gateway import Credential
    root = Path(__file__).resolve().parents[3]
    writer = "88888888-8888-8888-8888-888888888888"
    writer_client = "99999999-9999-9999-9999-999999999999"
    async def case():
        document = json.loads((root / "examples/citadel-governance/binding.template.json").read_text())
        h = await native_pair(tmp_path, document=document)
        path = root / "skills/threadlight-deploy/references/governance/returns_mcp_backend.py"
        spec = importlib.util.spec_from_file_location("citadel_returns_service", path)
        backend = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = backend
        spec.loader.exec_module(backend)
        monkeypatch.syspath_prepend(str(root / "skills/threadlight-govern/references"))
        monkeypatch.syspath_prepend(str(root / "examples/returns-triage-governed/src/agent"))
        from cosmos_effect import CosmosEffectTransport
        config = {
            **h.cp.settings.model_dump(mode="json"), "audience": "api://business",
            "workloads": {
                WORKLOAD: h.cp.settings.workloads[WORKLOAD].model_dump(mode="json"),
                writer: {"client_id": writer_client, "agent_id": "writer", "policies": ["safe"]}},
            "cosmos_url": "https://fixture.documents.azure.com:443/",
            "cosmos_database": "business", "cosmos_container": "returns",
            "service_client_id": writer_client, "writer_subject": writer, "agent_subject": WORKLOAD,
            "cases": ["RMA-ALLOW"], "policy_digest": h.policy.digest,
            "deployment": document["deployment"],
            "citadel_binding": h.policy.binding.model_dump(mode="json"),
        }
        config_file = tmp_path / "backend.json"
        config_file.write_text(json.dumps(config))
        monkeypatch.setenv("RETURNS_CONFIG_FILE", str(config_file))
        records = {"RMA-ALLOW": {"id": "RMA-ALLOW", "case_id": "RMA-ALLOW", "kind": "case",
            "_etag": '"revision-1"', "status": "in_triage", "amount": 40,
            "eligible": True, "high_risk": False}}
        effects = []

        class CredentialFixture:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class Container:
            container_link = "dbs/business/colls/returns"
            async def read(self):
                return {"partitionKey": {"paths": ["/case_id"]}}
            async def read_item(self, item, partition_key):
                if item not in records:
                    raise CosmosResourceNotFoundError()
                return deepcopy(records[item])
            async def execute_item_batch(self, *, batch_operations, partition_key):
                assert batch_operations[0][2]["if_match_etag"] == records[partition_key]["_etag"]
                effects.append(deepcopy(batch_operations))
                for operation, args, options in batch_operations:
                    record = args[1] if operation == "replace" else args[0]
                    records[record["id"]] = deepcopy(record)

        async def connect(transport, **kwargs):
            container = Container()
            transport._containers.append(container)
            return container
        monkeypatch.setattr(CosmosEffectTransport, "connect", connect)
        monkeypatch.setattr("azure.identity.aio.ManagedIdentityCredential", CredentialFixture)
        monkeypatch.setattr(backend, "EntraAuth",
            lambda settings, http: cp("auth").EntraAuth(settings, h.cp.http))
        app = backend.create_app()
        token = h.cp.token(changes={"aud": "api://business", "oid": writer, "azp": writer_client})
        await h.downstream.aclose()
        h.downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(token), transport=httpx.ASGITransport(app=app))
        h.dispatcher.downstream = h.downstream
        args = {"case_id": "RMA-ALLOW", "expected_etag": '"revision-1"',
                "decision": "approve_refund", "reason": "Eligible; recommendation only."}
        async def call():
            return await h.dispatcher.dispatch(authorization="Bearer " + h.cp.token(),
                action="returns_apply_decision", arguments=args, idempotency_key="original")
        try:
            async with app.router.lifespan_context(app):
                result = await call()
                assert result["status"] == "completed", result
                assert await call() == result
                assert len(effects) == 1
                proof = effects[0][1][1][0]["provenance"]
                assert proof["x-citadel-binding"] == gateway("dispatcher").digest(h.policy.binding)
                assert proof["action_hash"] == h.receipt_bodies()[0]["action_hash"]
                async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="https://business.example") as client:
                    direct = await client.post("/decisions", json=args,
                        headers={"Authorization": "Bearer " + h.cp.token()})
                    assert direct.status_code == 401
                    spoof = await client.post("/decisions", json=args, headers={
                        "Authorization": "Bearer " + token, "X-Citadel-Binding": DIGEST})
                    assert spoof.status_code == 409
                assert len(effects) == 1
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_citadel_native_example_builds_three_bundles_without_authority(tmp_path):
    import importlib.util
    from pathlib import Path
    citadel()
    root = Path(__file__).resolve().parents[3]
    path = root / "examples/citadel-governance/build.py"
    spec = importlib.util.spec_from_file_location("citadel_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    output = example.build(tmp_path / "example")
    binding = cp("models").parse(citadel().Binding, (output / "binding.json").read_bytes())
    assert binding.producer.policy.digest != binding.consumer.policy.digest
    assert (output / "generation/citadel-binding.json").read_bytes() == (output / "binding.json").read_bytes()
    assert (output / "source-result.json").read_text().find("synthetic-unsigned-local-only") >= 0
    api = cp("models").strict_json((output / "apim-api.json").read_bytes())
    assert api["api_version"] == gateway("citadel_package").APIM_API_VERSION
    assert api["properties"] == gateway("citadel_package").mcp_api_properties(binding)
    with pytest.raises(FileExistsError):
        example.build(output)


def test_citadel_native_gate_rejects_gateway_only_evidence(tmp_path):
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("citadel_pin_gate", root / "scripts/ci/run-governance-pin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    names = [
        "test_gateway_native_deny_receipt_zero_effects",
        "test_gateway_native_duplicate_same_outcome_one_effect",
        "test_gateway_native_actual_mcp_authenticated_protocol",
        "test_gateway_native_transform_and_post_deny",
        "test_gateway_native_concurrent_reservation_single_winner",
        "test_gateway_native_approval_consume_replay_and_fresh_positive",
        "test_gateway_native_transformed_approval_binds_actual_effect",
        "test_gateway_native_transport_wait_expiry_and_no_redirect",
        "test_gateway_native_post_transform_duplicate_uses_same_enforced_arguments",
    ]
    report = tmp_path / "without-citadel.xml"
    report.write_text("<testsuite>" + "".join(f'<testcase name="{name}"/>' for name in names) + "</testsuite>")
    with pytest.raises(RuntimeError):
        runner.verify_gateway_junit(report)
