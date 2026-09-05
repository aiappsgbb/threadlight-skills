"""Producer contracts: real SDK/ASGI boundaries, external identity and Cosmos seams only."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import uuid

import httpx
import pytest

from test_control_plane import APP, HUMAN, TENANT, WORKLOAD, OTHER, MemoryStore, module as cp
from test_gateway import registry, gateway

cp("models")


INPUT = {
    "type": "object", "properties": {
        "probe_run_id": {"type": "string", "minLength": 36, "maxLength": 36},
        "variant": {"type": "string", "enum": ["allow", "deny"]}},
    "required": ["probe_run_id", "variant"], "additionalProperties": False,
}
OUTPUT = {
    "type": "object", "properties": {"status": {"type": "string", "enum": ["noop"]}},
    "required": ["status"], "additionalProperties": False,
}


def probe_registry():
    doc = registry()
    doc["actions"][0].update(
        name="governance_probe_noop", scope="governance-probe",
        endpoint="https://fixture.example/governance/noop",
        outcome_endpoint="https://fixture.example/governance/outcomes",
        input_schema=deepcopy(INPUT), output_schema=deepcopy(OUTPUT),
        probe_safe=True,
        probe_contract={"protocol": "threadlight-probe/v1", "fixture_id": "staging-noop", "effect": "noop"})
    return doc


def test_registry_normal_default_is_not_probe_safe():
    action = gateway("dispatcher").Registry.model_validate(registry()).actions[0]
    assert getattr(action, "probe_safe", None) is False
    assert action.probe_contract is None


def test_registry_explicit_bounded_noop_probe():
    model = gateway("dispatcher").Registry
    assert model.model_validate(probe_registry()).actions[0].probe_safe is True
    for flag in (1, 0, "true", None):
        doc = probe_registry()
        doc["actions"][0]["probe_safe"] = flag
        with pytest.raises(ValueError):
            model.model_validate(doc)
    for mutation in ("schema", "endpoint", "contract", "production", "disabled"):
        doc = probe_registry()
        action = doc["actions"][0]
        if mutation == "schema":
            action["input_schema"]["properties"]["url"] = {"type": "string", "maxLength": 100}
        elif mutation == "endpoint":
            action["endpoint"] = "https://fixture.example/refunds"
        elif mutation == "contract":
            action["probe_contract"]["effect"] = "business-write"
        elif mutation == "production":
            doc["deployment"]["environment"] = "production"
        else:
            action["probe_safe"] = False
        with pytest.raises(ValueError):
            model.model_validate(doc)


def service(store=None, producer="gateway"):
    assert hasattr(cp("models"), "ProbeContext"), "missing bounded receipt correlation"
    probes = cp("probes")
    doc = probe_registry()
    reg = gateway("dispatcher").Registry.model_validate(doc)
    return probes.ProbeService(
        store=store or MemoryStore(), registry=reg, policy_digest="sha256:" + "b" * 64,
        producer=producer, fresh=lambda: None)


def expected(s, variant="deny"):
    return {"subject": WORKLOAD, "action": "governance_probe_noop", "variant": variant,
            "deployment": s.registry.deployment.model_dump(mode="json"),
            "policy_digest": s.policy_digest}


def test_probe_state_registered_before_invoke_terminal_and_exact_scope():
    async def case():
        s = service()
        run = str(uuid.uuid4())
        with pytest.raises(cp("storage").Missing):
            await s.status(WORKLOAD, run)
        baseline = await s.register(run, expected(s))
        assert baseline["tenant"] == TENANT and baseline["binding"] == "safe"
        assert baseline["fixture_id"] == "staging-noop"
        assert baseline["counts"] == dict.fromkeys(("received", "intercepted", "dispatch", "effect", "completed"), 0)
        assert baseline["terminal"] is None
        for key, value in (("subject", OTHER), ("action", "refund"), ("policy_digest", "sha256:" + "c"*64)):
            bad = {**expected(s), key: value}
            with pytest.raises(ValueError):
                await s.register(run, bad)
        for key, value in (("agent_version", "2"), ("image_digest", "sha256:" + "c"*64)):
            bad = expected(s)
            bad["deployment"][key] = value
            with pytest.raises(ValueError):
                await s.register(run, bad)
        context = await s.begin(WORKLOAD, run, "governance_probe_noop", "deny",
                                session_id="native-session", call_id="native-call")
        assert context.probe_run_id == run
        await s.intercept(context, decision="deny", receipt_id="a"*32)
        await s.complete(context, terminal="denied")
        after = await s.status(WORKLOAD, run)
        assert after["counts"] == {"received": 1, "intercepted": 1, "dispatch": 0, "effect": 0, "completed": 1}
        assert after["terminal"] == "denied"
        assert after["events"][1]["receipt_id"] == "a"*32
        assert all("recorded_at" in e and "event_id" in e for e in after["events"])
        with pytest.raises(cp("storage").Missing):
            await s.status(OTHER, run)
        with pytest.raises((ValueError, cp("storage").Conflict)):
            await s.begin(WORKLOAD, run, "governance_probe_noop", "deny",
                          session_id="other", call_id="other")
    asyncio.run(case())


def test_probe_cas_failures_never_become_zero_and_models_reject_claimed_counts():
    async def case():
        s = service()
        run = str(uuid.uuid4())
        await s.register(run, expected(s))
        with pytest.raises(ValueError):
            await s.register(str(uuid.uuid4()), {**expected(s), "counts": {"effect": 0}})
        s.store.failed = True
        with pytest.raises(Exception):
            await s.status(WORKLOAD, run)
        s.store.failed = False
        scope, key = next(iter(s.store.docs))
        s.store.docs[(scope, key)][0]["counts"]["effect"] = False
        with pytest.raises(RuntimeError):
            await s.status(WORKLOAD, run)
    asyncio.run(case())


def controller_config():
    return {HUMAN: {"client_id": APP, "subjects": [WORKLOAD], "actions": ["governance_probe_noop"]}}


def controller_token(h, *, read=False):
    return h.token(changes={"oid": HUMAN, "roles": [
        "Governance.Probe.Read" if read else "Governance.Probe.Control"]})


def test_probe_control_auth_scope_and_no_event_upload():
    async def case():
        from test_control_plane import Harness
        h = Harness()
        s = service()
        probes = cp("probes")
        assert hasattr(probes, "control_app"), "authenticated probe control API missing"
        settings = h.settings.model_dump()
        settings["probe_controllers"] = controller_config()
        h.auth = cp("auth").EntraAuth(cp("auth").Settings.model_validate(settings), h.http)
        app = probes.control_app(s, h.auth)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://probe.example") as c:
            run = str(uuid.uuid4())
            url = "/governance/probes/" + run
            headers = {"Authorization": "Bearer " + controller_token(h)}
            assert (await c.get(url + "?subject=" + WORKLOAD)).status_code == 401
            assert (await c.post(url, headers={"Authorization": "Bearer " + h.token()},
                                 json=expected(s))).status_code == 403
            assert (await c.get(url + "?subject=" + WORKLOAD, headers=headers)).status_code == 404
            assert (await c.post(url, headers=headers, json=expected(s))).status_code == 201
            assert (await c.post(url, headers=headers, json=expected(s))).status_code == 409
            assert (await c.get(url + "?subject=" + OTHER, headers=headers)).status_code == 403
            assert (await c.post(url, headers=headers,
                                 json={**expected(s), "counts": {"effect": 100}})).status_code == 400
            assert (await c.post("/governance/probes/" + str(uuid.uuid4()),
                                 headers={"Authorization": "Bearer " + controller_token(h, read=True)},
                                 json=expected(s))).status_code == 403
            s.store.failed = True
            failure = await c.get(url + "?subject=" + WORKLOAD, headers=headers)
            assert failure.status_code == 503 and "UNKNOWN" in failure.text and "PRIVATE" not in failure.text
        await h.close()
    asyncio.run(case())


def fixture_module():
    from pathlib import Path
    import importlib.util
    import sys
    root = Path(__file__).resolve().parents[3]
    path = root / "skills/threadlight-safe-check/references/probe-fixture"
    assert (path / "app.py").exists(), "runnable authenticated noop fixture missing"
    if "govern_probe_fixture" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "govern_probe_fixture", path / "__init__.py", submodule_search_locations=[str(path)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
    return __import__("govern_probe_fixture.app", fromlist=["app"])


@pytest.mark.governance_runtime
def test_probe_gateway_native_mcp_to_real_fixture_deny_and_positive(tmp_path):
    async def case():
        from test_gateway import GatewayHarness, Credential
        h = await GatewayHarness().initialize(
            tmp_path, document=probe_registry(),
            decision='{"decision": "deny"} if input.policy_target.value.variant == "deny" else := {"decision": "allow"}')
        probes = cp("probes")
        assert hasattr(h.dispatcher, "probes"), "gateway probe instrumentation missing"
        h.dispatcher.probes = probes.ProbeService(
            store=MemoryStore(), registry=h.policy.registry, policy_digest=h.policy.digest,
            producer="gateway", fresh=h.policy.fresh)
        effects = probes.ProbeService(
            store=MemoryStore(), registry=h.policy.registry, policy_digest=h.policy.digest,
            producer="fixture", fresh=h.policy.fresh)
        settings = h.cp.settings.model_dump()
        settings["probe_controllers"] = controller_config()
        h.cp.auth = cp("auth").EntraAuth(cp("auth").Settings.model_validate(settings), h.cp.http)
        h.dispatcher.auth = h.cp.auth
        fixture = fixture_module().create_app(
            probes=effects, auth=h.cp.auth, callers={OTHER: APP})
        await h.downstream.aclose()
        h.downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(h.cp.token(changes={"oid": OTHER})), transport=httpx.ASGITransport(app=fixture))
        h.dispatcher.downstream = h.downstream
        app = gateway("server").create_app(h.dispatcher)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://gateway.example") as client:
                for variant in ("deny", "allow"):
                    run = str(uuid.uuid4())
                    registration = expected(h.dispatcher.probes, variant)
                    headers = {"Authorization": "Bearer " + controller_token(h.cp)}
                    url = "/governance/probes/" + run
                    assert (await client.post(url, headers=headers, json=registration)).status_code == 201
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture),
                                                 base_url="https://fixture.example") as fc:
                        assert (await fc.post(url, headers=headers, json=registration)).status_code == 201
                    result = await client.post("/mcp", headers={
                        "Authorization": "Bearer " + h.cp.token(),
                        "Accept": "application/json, text/event-stream", "Idempotency-Key": run,
                    }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "governance_probe_noop",
                        "arguments": {"probe_run_id": run, "variant": variant}}})
                    assert result.status_code == 200, result.text
                    body = result.json()["result"]["structuredContent"]
                    assert result.json()["result"]["_meta"]["threadlight.probe.receipt_id"] == body["receipt_id"]
                    assert body["status"] == ("blocked" if variant == "deny" else "completed"), body
                    state = (await client.get(url + "?subject=" + WORKLOAD, headers=headers)).json()
                    downstream = await effects.status(WORKLOAD, run)
                    assert state["counts"]["received"] == state["counts"]["intercepted"] == 1
                    assert state["counts"]["dispatch"] == downstream["counts"]["effect"] == (variant == "allow")
                    assert state["counts"]["completed"] == 1
                    assert state["terminal"] == ("denied" if variant == "deny" else "completed")
                    receipt_id = body["receipt_id"]
                    receipt = await h.cp.client.get("/receipts/" + receipt_id,
                                                   headers={"Authorization": "Bearer " + h.cp.token()})
                    assert receipt.status_code == 200, receipt.text
                    assert receipt.json()["probe"]["probe_run_id"] == run
                    assert receipt.json()["decision"] == variant
                    assert receipt.json()["probe"]["deployment"] == registration["deployment"]
        await h.close()
    asyncio.run(case())


def test_fixture_two_workers_atomic_idempotent_noop_and_unavailable():
    async def case():
        from test_control_plane import Harness
        h = Harness()
        s = service(producer="fixture")
        module = fixture_module()
        run = str(uuid.uuid4())
        await s.register(run, expected(s, "allow"))
        args = {"probe_run_id": run, "variant": "allow"}
        facts = {"tenant": TENANT, "subject": WORKLOAD, "client": APP,
                 "action": "governance_probe_noop", "scope": "governance-probe",
                 "policy": s.policy_digest, "deployment": expected(s)["deployment"]}
        d = gateway("dispatcher").digest
        headers = {"Authorization": "Bearer " + h.token(changes={"oid": OTHER}), "Idempotency-Key": "a"*64,
                   "X-Action-Hash": d({"facts": facts, "arguments": args}),
                   "X-Governance-Provenance": "b"*32, "X-Tenant-ID": TENANT,
                   "X-Requester-ID": WORKLOAD, "X-Requester-Client": APP,
                   "X-Action-ID": facts["action"], "X-Policy-Digest": s.policy_digest,
                   "X-Deployment-Hash": d(facts["deployment"])}
        apps = [module.create_app(probes=service(s.store, "fixture"), auth=h.auth, callers={OTHER: APP})
                for _ in range(2)]
        clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=a),
                                    base_url="https://fixture.example") for a in apps]
        direct = await clients[0].post("/governance/noop", json=args,
                                       headers={**headers, "Authorization": "Bearer " + h.token()})
        assert direct.status_code == 403
        assert (await s.status(WORKLOAD, run))["counts"]["effect"] == 0
        replies = await asyncio.gather(*(c.post("/governance/noop", json=args, headers=headers) for c in clients))
        assert [r.status_code for r in replies] == [200, 200], [r.text for r in replies]
        assert replies[0].json() == replies[1].json()
        fetched = await clients[0].get("/governance/outcomes",
                                      headers={**headers, "X-Probe-Run-ID": run})
        assert fetched.status_code == 200 and fetched.json() == replies[0].json()
        assert (await s.status(WORKLOAD, run))["counts"]["effect"] == 1
        bad = await clients[0].post("/governance/noop", json=args,
                                   headers={**headers, "X-Action-Hash": "sha256:"+"f"*64})
        assert bad.status_code == 400
        s.store.failed = True
        failure = await clients[0].post("/governance/noop", json=args, headers=headers)
        assert failure.status_code == 503 and "UNKNOWN" in failure.text
        for client in clients:
            await client.aclose()
        await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("variant", ["deny", "allow", "unreached"])
@pytest.mark.parametrize("path", ["agent", "host"])
def test_probe_real_native_maf_hooks_and_fixture(tmp_path, variant, path, monkeypatch):
    async def case():
        import importlib
        from test_gateway import GatewayHarness, Credential
        from test_runtime_provider import contract, runtime, native_model_client, tool_responses
        from skills._shared.governance import validate_governance_contract
        from test_policy_bundle import bundle_module
        h = await GatewayHarness().initialize(
            tmp_path, document=probe_registry(),
            decision='{"decision": "deny"} if input.policy_target.value.variant == "deny" else := {"decision": "allow"}')
        rt = runtime()
        assert hasattr(rt, "NativeProbeTelemetry"), "native pre-tool correlation missing"
        doc = contract()
        doc["tools"][0]["id"] = "governance_probe_noop"
        doc["tools"][0]["requires"] = ["audit"]
        native = service(producer="native")
        native.registry, native.policy_digest, native.fresh = h.policy.registry, h.policy.digest, h.policy.fresh
        effects = cp("probes").ProbeService(
            store=MemoryStore(), registry=h.policy.registry, policy_digest=h.policy.digest,
            producer="fixture", fresh=h.policy.fresh)
        fixture = fixture_module().create_app(probes=effects, auth=h.cp.auth, callers={OTHER: APP})
        downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(h.cp.token(changes={"oid": OTHER})), transport=httpx.ASGITransport(app=fixture))
        spool = rt.DurableSpool(tmp_path / "native-receipts")
        audit_h = None
        if path == "host":
            from pathlib import Path
            deploy_tests = Path(__file__).resolve().parents[3] / "skills/threadlight-deploy/tests"
            monkeypatch.syspath_prepend(str(deploy_tests))
            from test_governance_wiring import audit_harness
            spool, audit_h, _ = audit_harness(tmp_path)
            await spool.__aenter__()
        p = rt.AcsGovernanceProvider(
            contract=doc, bundle_path=h.bundle.root, expected_digest=h.policy.digest,
            bundle_verifier=bundle_module().verify_bundle, contract_validator=validate_governance_contract,
            signature_verifier=cp("client").PolicySnapshot(rt.VerifiedPolicy(h.policy.digest, h.policy.expires_at)),
            safe_provider=lambda identity: {"scope": "governance-probe"},
            principal=WORKLOAD, tenant=TENANT, agent_version="1",
            image_digest=h.policy.registry.deployment.image_digest, audit=spool, environment="preproduction")
        telemetry = rt.NativeProbeTelemetry(provider=p, service=native, downstream=downstream, client_id=APP)
        run = str(uuid.uuid4())
        args = {"probe_run_id": run, "variant": "allow" if variant == "unreached" else variant}
        await native.register(run, expected(native, args["variant"]))
        await effects.register(run, expected(effects, args["variant"]))
        responses = tool_responses("governance_probe_noop", args)
        if variant == "unreached":
            responses = responses[-1:]
        client = native_model_client(responses, foundry=True)
        if path == "host":
            import sys
            from pathlib import Path
            from types import SimpleNamespace
            deploy = Path(__file__).resolve().parents[3] / "skills/threadlight-deploy"
            monkeypatch.syspath_prepend(str(deploy / "tests"))
            monkeypatch.syspath_prepend(str(deploy / "references/governance"))
            monkeypatch.syspath_prepend(str(deploy.parent / "threadlight-govern/references"))
            monkeypatch.setitem(sys.modules, "runtime", rt)
            from test_governance_wiring import module
            container = module("maf-container")
            container.BASE = tmp_path / "host"
            container.BASE.mkdir()
            (container.BASE / "copilot-instructions.md").write_text("Use only the installed noop.")
            (container.BASE / "skills").mkdir()
            monkeypatch.setitem(sys.modules, "governance_application", SimpleNamespace(tools=[], middleware=[]))
            p.deployment_agent_id = "agent-1"
            telemetry.auth = h.cp.auth
            host = container.build_host(p, client=client, configure_observability=None)
            assert any(r.path == "/governance/probes/{run_id}" for r in host.routes)
        else:
            agent = rt.create_governed_agent(p, client=client, tools=[telemetry.tool()],
                                            id="agent-1", name="agent-1", default_options={"store": False})
        try:
            if path == "host":
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host),
                                             base_url="https://host.example") as hc:
                    response = await hc.post("/responses", json={
                        "input": "Invoke the explicitly installed noop fixture", "store": False,
                        "stream": False})
                    assert response.status_code == 200, response.text
                    assert response.json().get("error") is None, response.text
            else:
                await agent.run("Invoke the explicitly installed noop fixture")
        except Exception as error:
            # Native SDK may terminate the denied run; only service evidence establishes interception.
            assert variant == "deny", type(error).__name__
        state = await native.status(WORKLOAD, run)
        effect = await effects.status(WORKLOAD, run)
        if variant == "unreached":
            assert state["counts"]["intercepted"] == 0 and state["terminal"] is None
        else:
            assert state["counts"]["intercepted"] == 1, (state, response.text if path == "host" else "")
            assert state["counts"]["dispatch"] == effect["counts"]["effect"] == int(variant == "allow")
            assert state["terminal"] == ("denied" if variant == "deny" else "completed")
            receipt_id = state["events"][1]["receipt_id"]
            receipt = json.loads((spool.directory / (receipt_id + ".json")).read_text())
            assert receipt["probe"]["probe_run_id"] == run
            assert receipt["probe"]["call_id"] == state["context"]["call_id"]
            assert receipt["decision"] == variant
            if audit_h is not None:
                remote = await audit_h.client.get("/receipts/" + receipt_id,
                    headers={"Authorization": "Bearer " + audit_h.token()})
                assert remote.status_code == 200 and remote.json()["probe"] == receipt["probe"]
                assert remote.json()["decision"] == variant
        if audit_h is not None:
            await spool.__aexit__(None, None, None)
            await audit_h.close()
        await downstream.aclose()
        await client.client.close()
        await h.close()
    asyncio.run(case())


@asynccontextmanager
async def native_probe_host(tmp_path, monkeypatch, variants, *, bound_read=False):
    """Real Responses host, ACS/OPA and remote-ack audit; only external IO is local."""
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from test_gateway import GatewayHarness, Credential
    from test_runtime_provider import contract, runtime, native_model_client, tool_responses
    from test_policy_bundle import bundle_module
    from skills._shared.governance import validate_governance_contract

    deploy = Path(__file__).resolve().parents[3] / "skills/threadlight-deploy"
    monkeypatch.syspath_prepend(str(deploy / "tests"))
    monkeypatch.syspath_prepend(str(deploy / "references/governance"))
    monkeypatch.syspath_prepend(str(deploy.parent / "threadlight-govern/references"))
    from test_governance_wiring import audit_harness, module

    h = await GatewayHarness().initialize(
        tmp_path, document=probe_registry(),
        decision='{"decision": "deny"} if input.policy_target.value.variant == "deny" else := {"decision": "allow"}')
    rt = runtime()
    native = cp("probes").ProbeService(
        store=MemoryStore(), registry=h.policy.registry, policy_digest=h.policy.digest,
        producer="native", fresh=h.policy.fresh)
    effects = cp("probes").ProbeService(
        store=MemoryStore(), registry=h.policy.registry, policy_digest=h.policy.digest,
        producer="fixture", fresh=h.policy.fresh)
    fixture = fixture_module().create_app(probes=effects, auth=h.cp.auth, callers={OTHER: APP})
    downstream = gateway("dispatcher").DownstreamClient(
        credential=Credential(h.cp.token(changes={"oid": OTHER})), transport=httpx.ASGITransport(app=fixture))
    spool, audit, _ = audit_harness(tmp_path)
    doc = contract()
    doc["tools"][0].update(id="governance_probe_noop", requires=["audit"])
    if bound_read:
        doc["tools"][1] = {**doc["tools"][0], "id": "read"}
    p = rt.AcsGovernanceProvider(
        contract=doc, bundle_path=h.bundle.root, expected_digest=h.policy.digest,
        bundle_verifier=bundle_module().verify_bundle, contract_validator=validate_governance_contract,
        signature_verifier=cp("client").PolicySnapshot(rt.VerifiedPolicy(h.policy.digest, h.policy.expires_at)),
        safe_provider=lambda identity: {"scope": "governance-probe"},
        principal=WORKLOAD, tenant=TENANT, agent_version="1",
        image_digest=h.policy.registry.deployment.image_digest, audit=spool, environment="preproduction")
    p.deployment_agent_id = "agent-1"
    telemetry = rt.NativeProbeTelemetry(provider=p, service=native, downstream=downstream, client_id=APP)
    telemetry.auth = h.cp.auth
    runs, responses = [], []
    for index, variant in enumerate(variants):
        run = str(uuid.uuid4())
        runs.append(run)
        for producer in (native, effects):
            await producer.register(run, expected(producer, variant))
        tool, final = tool_responses("governance_probe_noop", {"probe_run_id": run, "variant": variant})
        tool.messages[0].contents[0].call_id = f"call-{index}"
        responses.append(tool)
    responses.append(final)
    client = native_model_client(responses, foundry=True)
    monkeypatch.setitem(sys.modules, "runtime", rt)
    container = module("maf-container")
    container.BASE = tmp_path / "host"
    container.BASE.mkdir()
    (container.BASE / "copilot-instructions.md").write_text("Use only the installed noop.")
    (container.BASE / "skills").mkdir()
    application = SimpleNamespace(tools=[], middleware=[])
    monkeypatch.setitem(sys.modules, "governance_application", application)
    async with spool:
        try:
            yield SimpleNamespace(
                host=lambda: container.build_host(p, client=client, configure_observability=None),
                provider=p, telemetry=telemetry, native=native, effects=effects,
                audit=audit, spool=spool, runs=runs, client=client, application=application, runtime=rt)
        finally:
            await downstream.aclose()
            await client.client.close()
            await audit.close()
            await h.close()


@pytest.mark.governance_runtime
@pytest.mark.parametrize("variants", [("deny",), ("allow",), ("deny", "allow", "deny")],
                         ids=["deny", "allow", "multiple"])
def test_probe_native_host_send_boundary(tmp_path, monkeypatch, variants):
    async def case():
        async with native_probe_host(tmp_path, monkeypatch, variants) as h:
            host, observed = h.host(), []
            untouched = str(uuid.uuid4())
            await h.native.register(untouched, expected(h.native))
            async def app(scope, receive, send):
                async def observed_send(message):
                    if message["type"] == "http.response.body":
                        for line in message.get("body", b"").splitlines():
                            if not line.startswith(b"data: "):
                                continue
                            event = json.loads(line[6:])
                            item = event.get("item", {})
                            if (event["type"] == "response.output_item.done"
                                    and item.get("type") == "function_call_output"):
                                index = int(item["call_id"].removeprefix("call-"))
                                variant, run = variants[index], h.runs[index]
                                state = await h.native.status(WORKLOAD, run)
                                observed.append(state)
                                assert state["counts"] == {
                                    "received": 1, "intercepted": 1, "dispatch": int(variant == "allow"),
                                    "effect": 0, "completed": 1}, state
                                assert state["terminal"] == ("denied" if variant == "deny" else "completed")
                                receipt_id = state["events"][1]["receipt_id"]
                                receipt = await h.audit.client.get("/receipts/" + receipt_id,
                                    headers={"Authorization": "Bearer " + h.audit.token()})
                                assert receipt.status_code == 200
                                assert receipt.json()["decision"] == variant
                                assert receipt.json()["probe"]["probe_run_id"] == run
                                assert receipt.json()["probe"]["call_id"] == state["context"]["call_id"]
                                assert ("threadlight:policy_deny" in json.dumps(item)) == (variant == "deny")
                                unused = await h.native.status(WORKLOAD, untouched)
                                assert not any(unused["counts"].values()) and unused["terminal"] is None
                            if event["type"] == "response.output_text.delta":
                                assert len(observed) == len(variants), "assistant output preceded the probe observation"
                    await send(message)
                await host(scope, receive, observed_send)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://host.example") as client:
                response = await asyncio.wait_for(client.post("/responses", json={
                    "input": "Invoke the explicitly installed noop fixture", "store": False, "stream": True}), 10)
            assert response.status_code == 200 and len(observed) == len(variants), response.text
            assert len({state["events"][1]["receipt_id"] for state in observed}) == len(variants)
            for run, variant in zip(h.runs, variants):
                effect = await h.effects.status(WORKLOAD, run)
                assert effect["counts"]["effect"] == int(variant == "allow")
    asyncio.run(case())


def order_after_native_deny(h, monkeypatch):
    """Order a later effect, not already-running siblings in MAF's parallel batch."""
    from agent_framework import FunctionMiddleware
    denied = asyncio.Event()
    sink = h.runtime.create_governed_agent.__globals__["NativeRecordSink"]
    original = sink.__call__
    def observe(self, record):
        original(self, record)
        if (record.interception_point.value == "pre_tool_call"
                and record.verdict.reason == "threadlight:policy_deny"):
            denied.set()
    monkeypatch.setattr(sink, "__call__", observe)
    class FollowingTool(FunctionMiddleware):
        async def process(self, context, call_next):
            await asyncio.wait_for(denied.wait(), 2)
            await call_next()
    h.application.middleware = [FollowingTool()]


@pytest.mark.governance_runtime
@pytest.mark.parametrize("following", ["probe", "unbound"])
def test_probe_native_batch_flush_before_next_effect(tmp_path, monkeypatch, following):
    async def case():
        async with native_probe_host(tmp_path, monkeypatch, ["deny", "allow"]) as h:
            from agent_framework import FunctionTool
            order_after_native_deny(h, monkeypatch)
            first, second = h.client.responses[:2]
            if following == "unbound":
                second.messages[0].contents[0].name = "read"
                second.messages[0].contents[0].arguments = "{}"
            first.messages[0].contents.extend(second.messages[0].contents)
            h.client.responses.pop(1)
            seen = []
            async def before_effect():
                state = await h.native.status(WORKLOAD, h.runs[0])
                seen.append(state)
            original = h.effects.effect
            async def effect(*args, **kwargs):
                await before_effect()
                return await original(*args, **kwargs)
            h.effects.effect = effect
            async def read():
                await before_effect()
                return "public read result"
            h.application.tools = [FunctionTool(name="read", func=read)]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.host()),
                                         base_url="https://host.example") as client:
                response = await asyncio.wait_for(client.post("/responses", json={
                    "input": "Invoke the tools", "store": False, "stream": True}), 10)
            assert response.status_code == 200 and seen, response.text
            assert all(state["terminal"] == "denied" and state["counts"]["intercepted"] == 1
                       and state["counts"]["completed"] == 1 for state in seen), seen
            second_state = await h.native.status(WORKLOAD, h.runs[1])
            assert second_state["terminal"] == ("completed" if following == "probe" else None)
            assert second_state["counts"]["intercepted"] == int(following == "probe")
            if following == "probe":
                assert second_state["context"]["call_id"] != seen[0]["context"]["call_id"]
                assert second_state["events"][1]["receipt_id"] != seen[0]["events"][1]["receipt_id"]
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("failure", [None, "intercept", "complete", "timeout", "bridge-timeout"])
@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("bound_read", [False, True], ids=["unbound", "bound"])
def test_probe_native_queued_worker_terminal_boundary(
        tmp_path, monkeypatch, caplog, failure, workers, bound_read):
    from concurrent.futures import Future, ThreadPoolExecutor
    from contextvars import ContextVar
    from functools import partial
    from threading import Event, get_ident
    from agent_framework import FunctionInvocationContext, FunctionTool

    async def case():
        async with native_probe_host(tmp_path, monkeypatch, ["deny"], bound_read=bound_read) as h:
            loop, owner = asyncio.get_running_loop(), get_ident()
            in_worker = ContextVar("queued_probe_test_worker", default=False)
            progress, persist = asyncio.Event(), asyncio.Event()
            jobs, seen, counts, attempts, records = [], [], [], [], []

            class QueuedExecutor(ThreadPoolExecutor):
                def submit(self, fn, /, *args, **kwargs):
                    if (isinstance(fn, partial) and fn.args
                            and getattr(fn.args[0], "__module__", "") in {
                                "agent_framework._tools",
                                "skills.threadlight-govern.references.runtime.maf_agent_hooks_acs",
                            }):
                        future = Future()
                        def run():
                            if not future.set_running_or_notify_cancel():
                                return
                            def terminal():
                                token = in_worker.set(True)
                                try:
                                    return fn.args[0](*fn.args[1:], **fn.keywords)
                                finally:
                                    in_worker.reset(token)
                            try:
                                future.set_result(fn.func(terminal))
                            except BaseException as error:
                                future.set_exception(error)
                        jobs.append((future, run))
                        return future
                    return super().submit(fn, *args, **kwargs)

                def release(self):
                    assert len(jobs) == 2 and not any(f.running() for f, _ in jobs)
                    for _, run in jobs:
                        super().submit(run)

            executor = QueuedExecutor(max_workers=workers)
            loop.set_default_executor(executor)
            sink = h.runtime.create_governed_agent.__globals__["NativeRecordSink"]
            original_sink = sink.__call__
            def record(self, value):
                original_sink(self, value)
                if (value.interception_point.value == "pre_tool_call"
                        and value.verdict.reason == "threadlight:policy_deny"):
                    records.append(value)
                    executor.release()
            monkeypatch.setattr(sink, "__call__", record)

            # Hold storage until a queued job reaches either its body (the bug)
            # or the worker-originated flush. Context copying across the bridge is
            # observable without replacing native dispatch or policy evaluation.
            original_flush = h.telemetry.flush
            async def flush(state):
                if in_worker.get():
                    assert asyncio.get_running_loop() is loop and get_ident() == owner
                    progress.set()
                return await original_flush(state)
            monkeypatch.setattr(h.telemetry, "flush", flush)
            for name in ("intercept", "complete"):
                original = getattr(h.native, name)
                async def write(context, _name=name, _original=original, **kwargs):
                    assert asyncio.get_running_loop() is loop and get_ident() == owner
                    attempts.append(_name)
                    await persist.wait()
                    if failure == "bridge-timeout" and _name == "intercept":
                        # An unresponsive owner loop must not admit the next
                        # worker or release output after its bridge timed out.
                        Event().wait(0.8)
                    if failure == _name:
                        raise OSError("PRIVATE QUEUED PROBE STORE")
                    if failure == "timeout":
                        await asyncio.Event().wait()
                    return await _original(context, **kwargs)
                monkeypatch.setattr(h.native, name, write)

            def read(ctx: FunctionInvocationContext):
                state = asyncio.run_coroutine_threadsafe(
                    h.native.status(WORKLOAD, h.runs[0]), loop).result(timeout=2)
                seen.append(state)
                counts.append(ctx.function)
                loop.call_soon_threadsafe(progress.set)
                return "public read result"
            h.application.tools = [FunctionTool(name="read", func=read)]
            denial = h.client.responses[0].messages[0].contents[0]
            reads = []
            for index in range(2):
                call = deepcopy(denial)
                call.name, call.arguments, call.call_id = "read", "{}", f"read-{index}"
                reads.append(call)
            h.client.responses[0].messages[0].contents[:] = [*reads, denial]
            h.provider.timeout = 0.5
            untouched = str(uuid.uuid4())
            await h.native.register(untouched, expected(h.native))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.host()),
                                         base_url="https://host.example") as client:
                request = asyncio.create_task(client.post("/responses", json={
                    "input": "Read then probe", "store": False, "stream": True}))
                try:
                    await asyncio.wait_for(progress.wait(), 3)
                finally:
                    persist.set()
                response = await asyncio.wait_for(request, 5)
            assert len(records) == 1 and len(jobs) == 2
            assert "PRIVATE" not in response.text + caplog.text
            if failure:
                assert not seen, "queued body ran behind an unpersisted required denial"
                assert "threadlight:probe_unavailable" in response.text
                assert not any(line.startswith("data: ") and
                               json.loads(line[6:]).get("item", {}).get("type") == "function_call_output"
                               for line in response.text.splitlines())
            else:
                assert len(seen) == 2, response.text
                assert all(state["terminal"] == "denied" and state["counts"] == {
                    "received": 1, "intercepted": 1, "dispatch": 0, "effect": 0, "completed": 1,
                } for state in seen), seen
                assert counts[0] is counts[1]
                assert (counts[0].invocation_count, counts[0].invocation_exception_count) == (2, 0)
            assert attempts.count("intercept") == 1
            unused = await h.native.status(WORKLOAD, untouched)
            assert not any(unused["counts"].values()) and unused["terminal"] is None
            assert (await h.effects.status(WORKLOAD, h.runs[0]))["counts"]["effect"] == 0
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_probe_native_pending_denial_is_run_scoped(tmp_path, monkeypatch):
    from agent_framework import FunctionTool
    from test_runtime_provider import tool_responses

    async def case():
        async with native_probe_host(tmp_path, monkeypatch, ["deny"]) as h:
            flushing, release = asyncio.Event(), asyncio.Event()
            seen, attempts = [], []
            async def fail(context, **kwargs):
                attempts.append(context.probe_run_id)
                flushing.set()
                await release.wait()
                raise OSError("PRIVATE OTHER RUN STORE")
            monkeypatch.setattr(h.native, "intercept", fail)
            def read():
                seen.append("unbound")
                return "public read result"
            h.application.tools = [FunctionTool(name="read", func=read)]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.host()),
                                         base_url="https://host.example") as client:
                payload = {"input": "Use the tools", "store": False, "stream": True}
                first = asyncio.create_task(client.post("/responses", json=payload))
                await asyncio.wait_for(flushing.wait(), 3)
                h.client.responses[:] = tool_responses("read", {})
                try:
                    second = await asyncio.wait_for(client.post("/responses", json=payload), 3)
                    assert seen == ["unbound"], second.text
                    assert "public read result" in second.text
                    assert not first.done()
                finally:
                    release.set()
                failed = await asyncio.wait_for(first, 3)
                assert "threadlight:probe_unavailable" in failed.text
                assert "PRIVATE" not in failed.text + second.text
                h.client.responses[:] = tool_responses("read", {})
                later = await asyncio.wait_for(client.post("/responses", json=payload), 3)
                assert seen == ["unbound", "unbound"] and "public read result" in later.text
            assert attempts == h.runs, "a different host run must not retry/borrow this nonce"
            state = await h.native.status(WORKLOAD, h.runs[0])
            assert state["terminal"] is None and state["counts"]["intercepted"] == 0
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["sync", "async", "on-loop", "awaitable"])
def test_probe_native_unbound_validation_and_budget_unchanged(tmp_path, monkeypatch, enabled, kind):
    from agent_framework import FunctionInvocationContext, FunctionTool
    from pydantic import BaseModel, field_validator
    from test_runtime_provider import tool_responses

    async def case():
        async with native_probe_host(tmp_path, monkeypatch, ["deny"]) as h:
            if not enabled:
                h.provider.probes = None
            validations, effects, evaluations = [], [], []
            class Input(BaseModel):
                amount: int
                @field_validator("amount")
                @classmethod
                def validate_amount(cls, value):
                    validations.append(value)
                    return value
            def body(amount, ctx):
                effects.append((amount, ctx.function.invocation_count,
                                ctx.function.invocation_exception_count))
                return "public read result"
            async def async_read(amount: int, ctx: FunctionInvocationContext):
                return body(amount, ctx)
            def sync_read(amount: int, ctx: FunctionInvocationContext):
                return async_read(amount, ctx) if kind == "awaitable" else body(amount, ctx)
            tool = FunctionTool(name="read", func=async_read if kind == "async" else sync_read,
                                input_model=Input, max_invocations=2)
            tool._invoke_sync_on_event_loop = kind == "on-loop"
            schema = deepcopy(tool.parameters())
            h.application.tools = [tool]
            original = h.provider._engine.evaluate_intervention_point
            async def evaluate(*args, **kwargs):
                evaluations.append(args)
                return await original(*args, **kwargs)
            monkeypatch.setattr(h.provider._engine, "evaluate_intervention_point", evaluate)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.host()),
                                         base_url="https://host.example") as client:
                for _ in range(3):
                    h.client.responses[:] = tool_responses("read", {"amount": 4})
                    await asyncio.wait_for(client.post("/responses", json={
                        "input": "Read", "store": False, "stream": True}), 3)
            assert effects == [(4, 1, 0), (4, 2, 0)]
            assert validations == [4] * 6
            assert not evaluations, "the optional unbound wrapper must never invoke ACS"
            # The pinned native provider adds this flag even without probes.
            assert tool.input_model is Input
            assert tool.parameters() == {**schema, "additionalProperties": False}
            state = await h.native.status(WORKLOAD, h.runs[0])
            assert not any(state["counts"].values()) and state["terminal"] is None
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("failure", ["intercept", "complete", "timeout"])
def test_probe_native_host_flush_failure_no_output_or_future_effect(tmp_path, monkeypatch, caplog, failure):
    async def case():
        async with native_probe_host(tmp_path, monkeypatch, ["deny", "allow"]) as h:
            order_after_native_deny(h, monkeypatch)
            # One native batch can dispatch its next tool before yielding an update.
            h.client.responses[0].messages[0].contents.extend(
                h.client.responses.pop(1).messages[0].contents)
            method = "complete" if failure == "complete" else "intercept"
            original = getattr(h.native, method)
            attempts = []
            async def fail(context, **kwargs):
                if context.probe_run_id == h.runs[0]:
                    attempts.append(context.probe_run_id)
                    if failure == "timeout":
                        await asyncio.Event().wait()
                    raise OSError("PRIVATE PROBE STORAGE SECRET")
                return await original(context, **kwargs)
            setattr(h.native, method, fail)
            h.provider.timeout = 0.5
            public = []
            host = h.host()
            async def app(scope, receive, send):
                async def observed_send(message):
                    if message["type"] == "http.response.body":
                        for line in message.get("body", b"").splitlines():
                            if line.startswith(b"data: "):
                                event = json.loads(line[6:])
                                if (event["type"] == "response.output_text.delta"
                                        or (event["type"] == "response.output_item.done"
                                            and event.get("item", {}).get("type") == "function_call_output")):
                                    public.append(event)
                    await send(message)
                await host(scope, receive, observed_send)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://host.example") as client:
                response = await asyncio.wait_for(client.post("/responses", json={
                    "input": "Invoke the tools", "store": False, "stream": True}), 5)
            assert attempts, "test did not reach the actual native record flush"
            assert not public, public
            assert "threadlight:probe_unavailable" in response.text, response.text
            assert "PRIVATE" not in response.text + caplog.text
            assert len(attempts) == 1, "a failed partial flush must not be retried during finalization"
            for run in h.runs:
                state = await h.native.status(WORKLOAD, run)
                assert state["counts"]["dispatch"] == 0 and state["terminal"] is None
                assert (await h.effects.status(WORKLOAD, run))["counts"]["effect"] == 0
    asyncio.run(case())


def test_probe_store_sdk_cas_adapter_strong_reads_and_failed_ack():
    async def case():
        from types import SimpleNamespace
        from azure.core import MatchConditions
        from azure.cosmos.exceptions import CosmosHttpResponseError as HttpResponseError
        from azure.cosmos.documents import DatabaseAccount
        probes = cp("probes")
        class Documents:
            def __init__(self):
                self.docs, self.etag, self.options = {}, 0, []
                self.fail = False
            async def read(self):
                return {"partitionKey": {"paths": ["/scope"]}}
            async def read_item(self, *, item, partition_key):
                if (partition_key, item) not in self.docs:
                    raise HttpResponseError(status_code=404)
                return deepcopy(self.docs[(partition_key, item)])
            async def create_item(self, *, body):
                key = body["scope"], body["id"]
                if key in self.docs:
                    raise HttpResponseError(status_code=409)
                self.etag += 1
                self.docs[key] = {**deepcopy(body), "_etag": str(self.etag)}
            async def replace_item(self, *, item, body, etag, match_condition):
                self.options.append(match_condition)
                await asyncio.sleep(0)
                if self.fail:
                    raise HttpResponseError(status_code=503)
                key = body["scope"], item
                if self.docs[key]["_etag"] != etag:
                    raise HttpResponseError(status_code=412)
                self.etag += 1
                self.docs[key] = {**deepcopy(body), "_etag": str(self.etag)}
        docs = Documents()
        account = DatabaseAccount()
        account._WritableLocations = [{}]
        account.ConsistencyPolicy = {"defaultConsistencyLevel": "Strong"}
        async def read_account():
            return account
        store = probes.ProbeStore(None, docs, account_reader=read_account)
        s = service(store, "fixture")
        run = str(uuid.uuid4())
        await s.register(run, expected(s, "allow"))
        async def effect():
            return await s.effect(WORKLOAD, run, variant="allow", effect_key="sha256:"+"d"*64,
                                  action_hash="sha256:"+"e"*64, receipt_id="a"*32)
        results = await asyncio.gather(effect(), effect())
        assert [r.counts["effect"] for r in results] == [1, 1]
        assert set(docs.options) == {MatchConditions.IfNotModified}
        run = str(uuid.uuid4())
        await s.register(run, expected(s, "allow"))
        docs.fail = True
        with pytest.raises(RuntimeError):
            await effect()
        account.ConsistencyPolicy = {"defaultConsistencyLevel": "Session"}
        with pytest.raises(RuntimeError):
            await s.status(WORKLOAD, run)
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_probe_dispatch_is_wire_boundary_not_credential_attempt(tmp_path):
    async def case():
        from test_gateway import GatewayHarness
        h = await GatewayHarness().initialize(tmp_path, document=probe_registry())
        s = cp("probes").ProbeService(
            store=MemoryStore(), registry=h.policy.registry, policy_digest=h.policy.digest,
            producer="gateway", fresh=h.policy.fresh)
        h.dispatcher.probes = s
        run = str(uuid.uuid4())
        await s.register(run, expected(s, "allow"))
        async def unavailable():
            raise RuntimeError("PRIVATE")
        h.credential.hook = unavailable
        await h.dispatcher.dispatch(authorization="Bearer " + h.cp.token(),
                                   action="governance_probe_noop",
                                   arguments={"probe_run_id": run, "variant": "allow"},
                                   idempotency_key=run)
        state = await s.status(WORKLOAD, run)
        assert state["counts"]["intercepted"] == 1
        assert state["counts"]["dispatch"] == 0 and state["terminal"] is None
        await h.close()
    asyncio.run(case())


def test_probe_runtime_disabled_by_default_and_requires_separate_state_and_controller():
    from test_control_plane import Harness
    h = Harness()
    try:
        model = gateway("server").Configuration
        assert "probe_enabled" in model.model_fields, "production probe opt-in is missing"
        assert model.model_fields["probe_enabled"].default is False
        runtime = gateway("probe_runtime")
        config = {
            **h.settings.model_dump(), "enabled": True, "producer": "fixture",
            "service_client_id": OTHER, "cosmos_url": "https://probe.documents.azure.com:443/",
            "cosmos_database": "governance", "cosmos_container": "probe-fixture",
            "bundle_path": "/config/probe-policy", "signed_envelope_path": "/config/envelope.json",
            "policy_id": "safe", "policy_version": "1", "policy_digest": "sha256:" + "b"*64,
            "gateway_url": "https://gateway.example/mcp",
            "allowed_endpoints": ["https://fixture.example/governance/noop",
                                  "https://fixture.example/governance/outcomes"],
            "expected_deployment": probe_registry()["deployment"],
            "fixture_callers": {WORKLOAD: APP}, "probe_controllers": controller_config(),
        }
        assert runtime.ProbeConfiguration.model_validate(config).enabled
        for field, value in (("enabled", 1), ("enabled", False),
                             ("cosmos_container", "governance-records"),
                             ("probe_controllers", {})):
            with pytest.raises(ValueError):
                runtime.ProbeConfiguration.model_validate({**config, field: value})
        assert callable(fixture_module().production_app)
    finally:
        asyncio.run(h.close())


def test_generator_probe_opt_in_is_explicit_and_native_registry_not_self_embedded():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "probe_generation", root / "skills/threadlight-deploy/references/governance/generate.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert hasattr(gen, "validate_probe_observability"), "generation probe contract missing"
    assert gen.validate_probe_observability({"environment": "production"}) is None
    opt = {"enabled": True, "configuration_file": "/mnt/governance-probe/config.json"}
    assert gen.validate_probe_observability({"environment": "preproduction", "probe_observability": opt}) == opt
    for option in ({"enabled": 1}, {"enabled": False}, {**opt, "counts": {}},
                   {**opt, "configuration_file": "/app/probe.json"}):
        with pytest.raises(ValueError):
            gen.validate_probe_observability({"environment": "preproduction", "probe_observability": option})
    with pytest.raises(ValueError):
        gen.validate_probe_observability({"environment": "production", "probe_observability": opt})
    reg = probe_registry()
    reg["native_policy_digest"] = "sha256:" + "c"*64
    assert gateway("dispatcher").Registry.model_validate(reg).native_policy_digest == reg["native_policy_digest"]
    bicep = (root / "skills/threadlight-deploy/references/governance/governance.bicep").read_text()
    assert "probe-fixture" in bicep and "probe-native" in bicep and "probe-gateway" in bicep


def test_probe_receipt_controller_reads_only_explicit_subject_action_scope():
    async def case():
        from test_control_plane import Harness, receipt
        h = Harness()
        h.settings = cp("auth").Settings.model_validate({
            **h.settings.model_dump(), "probe_controllers": controller_config()})
        h.auth.settings = h.settings
        h.service.settings = h.settings
        s = service()
        run = str(uuid.uuid4())
        await s.register(run, expected(s))
        context = await s.begin(WORKLOAD, run, "governance_probe_noop", "deny",
                                session_id="session", call_id="call")
        wire = receipt()
        wire.update(probe=context.model_dump(mode="json"), action_id=context.action,
                    policy_digest=context.policy_digest, agent_version=context.deployment.agent_version,
                    decision="deny")
        response = await h.client.post("/receipts", json=wire,
                                       headers={"Authorization": "Bearer " + h.token()})
        assert response.status_code == 200, response.text
        result = await h.client.get("/receipts/" + wire["receipt_id"],
                    headers={"Authorization": "Bearer " + controller_token(h, read=True)})
        assert result.status_code == 200, result.text
        h.settings.probe_controllers[HUMAN].subjects[:] = [OTHER]
        result = await h.client.get("/receipts/" + wire["receipt_id"],
                    headers={"Authorization": "Bearer " + controller_token(h, read=True)})
        assert result.status_code == 403
        await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_probe_late_fixture_is_not_completed_until_actual_await_finishes(tmp_path):
    async def case():
        from test_gateway import GatewayHarness, Credential
        h = await GatewayHarness().initialize(tmp_path, document=probe_registry())
        s = cp("probes").ProbeService(store=MemoryStore(), registry=h.policy.registry,
            policy_digest=h.policy.digest, producer="gateway", fresh=h.policy.fresh)
        effects = cp("probes").ProbeService(store=MemoryStore(), registry=h.policy.registry,
            policy_digest=h.policy.digest, producer="fixture", fresh=h.policy.fresh)
        h.dispatcher.probes = s
        fixture = fixture_module().create_app(probes=effects, auth=h.cp.auth, callers={OTHER: APP})
        arrived, release = asyncio.Event(), asyncio.Event()
        async def delayed(scope, receive, send):
            arrived.set()
            await release.wait()
            await fixture(scope, receive, send)
        await h.downstream.aclose()
        h.downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(h.cp.token(changes={"oid": OTHER})), transport=httpx.ASGITransport(app=delayed))
        h.dispatcher.downstream = h.downstream
        run = str(uuid.uuid4())
        for producer in (s, effects):
            await producer.register(run, expected(producer, "allow"))
        task = asyncio.create_task(h.dispatcher.dispatch(
            authorization="Bearer " + h.cp.token(), action="governance_probe_noop",
            arguments={"probe_run_id": run, "variant": "allow"}, idempotency_key=run))
        await asyncio.wait_for(arrived.wait(), 5)
        pending = await s.status(WORKLOAD, run)
        assert pending["counts"]["dispatch"] == 1 and pending["terminal"] is None
        assert (await effects.status(WORKLOAD, run))["counts"]["effect"] == 0
        release.set()
        assert (await task)["status"] == "completed"
        assert (await s.status(WORKLOAD, run))["terminal"] == "completed"
        assert (await effects.status(WORKLOAD, run))["counts"]["effect"] == 1
        await h.close()
    asyncio.run(case())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault", ["unregistered", "variant", "expired", "storage", "tenant", "action", "payload"])
def test_probe_invalid_run_cannot_produce_deny_or_dispatch_evidence(tmp_path, fault):
    async def case():
        from test_gateway import GatewayHarness
        from datetime import datetime, timedelta, timezone
        h = await GatewayHarness().initialize(tmp_path, document=probe_registry(),
                                             decision={"decision": "deny"})
        s = cp("probes").ProbeService(store=MemoryStore(), registry=h.policy.registry,
            policy_digest=h.policy.digest, producer="gateway", fresh=h.policy.fresh)
        h.dispatcher.probes = s
        run = str(uuid.uuid4())
        if fault != "unregistered":
            await s.register(run, expected(s, "deny"))
        if fault == "expired":
            scope, key = next(iter(s.store.docs))
            s.store.docs[(scope, key)][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        if fault == "storage":
            s.store.failed = True
        args = {"probe_run_id": run, "variant": "allow" if fault == "variant" else "deny"}
        if fault == "payload":
            args["customer_url"] = "PRIVATE"
        result = await h.dispatcher.dispatch(
            authorization="Bearer " + h.cp.token(changes={"tid": OTHER} if fault == "tenant" else {}),
            action="refund" if fault == "action" else "governance_probe_noop",
            arguments=args, idempotency_key=run)
        assert "receipt_id" not in result and result["status"] != "completed"
        assert not h.calls and not h.receipt_bodies()
        assert "PRIVATE" not in json.dumps(result)
        await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["context", "terminal"])
def test_probe_corrupt_durable_scope_and_terminal_are_unknown(fault):
    async def case():
        s = service()
        run = str(uuid.uuid4())
        await s.register(run, expected(s))
        context = await s.begin(WORKLOAD, run, "governance_probe_noop", "deny",
                                session_id="actual", call_id="actual")
        await s.intercept(context, decision="deny", receipt_id="a"*32)
        await s.complete(context, terminal="denied")
        scope, key = s.location(WORKLOAD, run)
        raw = s.store.docs[(scope, key)][0]
        if fault == "context":
            raw["context"]["deployment"]["image_digest"] = "sha256:" + "e"*64
        else:
            raw["terminal"] = "completed"
        with pytest.raises(RuntimeError):
            await s.status(WORKLOAD, run)
    asyncio.run(case())


@pytest.mark.governance_runtime
def test_native_probe_deployment_environment_is_not_inferred_from_enforce_mode():
    from types import SimpleNamespace
    from test_runtime_provider import runtime
    s = service(producer="native")
    p = SimpleNamespace(
        mode="enforce", audit=object(), principal=WORKLOAD, tenant=TENANT, agent_version="1",
        image_digest=s.registry.deployment.image_digest, environment="production",
        policy_digest=lambda: s.policy_digest,
        _tool_bindings=lambda name: [{"point": "pre_tool_call", "policy": "safe"}])
    with pytest.raises(ValueError, match="native_probe_deployment_mismatch"):
        runtime().NativeProbeTelemetry(provider=p, service=s, downstream=object(), client_id=APP)


def test_probe_fixture_docker_needs_no_runtime_build_backend():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "skills/threadlight-safe-check/references/probe-fixture/Dockerfile").read_text()
    assert "AS builder" in dockerfile and "COPY --from=builder" in dockerfile
    assert "--no-build-isolation /app/probe-fixture" not in dockerfile


def test_probe_fixture_never_accepts_agent_as_direct_caller():
    from test_control_plane import Harness
    h = Harness()
    try:
        with pytest.raises(ValueError, match="separate_fixture_caller_required"):
            fixture_module().create_app(probes=service(producer="fixture"), auth=h.auth,
                                        callers={WORKLOAD: APP})
    finally:
        asyncio.run(h.close())
