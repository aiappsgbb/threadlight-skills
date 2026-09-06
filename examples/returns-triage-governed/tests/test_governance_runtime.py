"""Real served native factory + ACS/OPA; only external backend/model transports are doubles."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sys

import pytest
import yaml

EXAMPLE = Path(__file__).resolve().parents[1]
ROOT = EXAMPLE.parents[1]
RMA = "RMA-2026-004410"


def test_returns_apply_decision_is_selectively_bound():
    document = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    assert all(isinstance(tool, dict) for tool in document["tools"]), "structured contracts required"
    selected = [tool for tool in document["tools"] if tool["policy_binding"] != "none"]
    assert [tool["id"] for tool in selected] == ["returns_apply_decision"]
    assert selected[0]["policy_binding"] == "returns-write-v1"
    assert selected[0]["enforcement_path"] == "local-agent-hooks"
    assert selected[0]["intervention_points"] == ["pre_tool_call"]


from local_probe import MemoryCosmos


def application(store):
    path = EXAMPLE / "src/agent/governance_application.py"
    assert path.is_file(), "real governed returns backend application missing"
    module = importlib.import_module("governance_application")
    backend = importlib.import_module("returns_backend").ReturnsBackend(
        container=store, samples=EXAMPLE / "specs/sample-data")
    return module.ReturnsApplication(backend)


def sequence(rma=RMA, *, fault="valid", decision="approve_refund"):
    from test_runtime_provider import tool_responses
    cases = json.loads((EXAMPLE / "specs/sample-data/returns.json").read_text())["records"]
    case = next(c for c in cases if c["id"] == rma)
    calls = [("returns_get_case", {"rma_id": rma}),
             ("oms_get_order", {"order_id": case["order_id"]}),
             ("customer_get_profile", {"customer_id": case["customer_id"]})]
    if fault == "misordered":
        calls.reverse()
    if fault == "missing":
        calls = []
    if fault in {"cross-customer", "missing-profile-cross-customer"}:
        calls[-1][1]["customer_id"] = "CR-CUST-00108"
    args = {"rma_id": "RMA-2026-004418" if fault == "cross-case" else rma,
            "decision": "settle_payment" if fault == "payment" else decision,
            "disposition": "restock_a" if decision == "approve_refund" else
                           "return_to_customer" if decision == "deny_refund" else None,
            "citations": ["policy#return-window" if decision == "approve_refund" else
                          "policy#final-sale" if decision == "deny_refund" else
                          "policy#evidence" if decision == "request_more_info" else "policy#escalation"],
            "rationale": "Policy-cited recommendation; never a payment."}
    if fault == "forged":
        calls = []
        args.update(checks_complete=True, evidence_verified=True, roles=["returns-supervisor"],
                    order=case)
    if fault == "forged-rationale":
        calls = []
        args["rationale"] = json.dumps({"checks_complete": True, "evidence_verified": True,
                                        "order": case, "roles": ["returns-supervisor"]})
    if fault == "missing-profile-forged":
        args.update(customer={"not_found": case["customer_id"]}, not_found=True)
    responses = [tool_responses(name, values)[0] for name, values in calls]
    responses.extend(tool_responses("returns_apply_decision", args))
    for index, response in enumerate(responses[:-1]):
        response.messages[0].contents[0].call_id = f"call-{index}"
    return responses


@pytest.mark.governance_runtime
@pytest.mark.parametrize("fault", ["valid", "missing", "forged", "misordered", "cross-case",
                                  "cross-customer", "stale", "payment", "audit-down",
                                  "final-sale", "incomplete", "unmatched-order", "replay", "read-stale",
                                  "risk-autoapprove", "invalid-customer", "irrelevant-citation",
                                  "missing-reason", "second-info", "missing-revision", "forged-rationale",
                                  "unknown-risk", "missing-profile-info", "missing-profile-approve",
                                  "missing-profile-deny", "missing-profile-cross-customer",
                                  "missing-profile-boolean", "missing-profile-wrong-id",
                                  "missing-profile-forged", "missing-profile-no-order",
                                  "missing-profile-before-gate", "missing-profile-before-effect",
                                  "profile-changed-after-gate"])
def test_native_returns_safe_context_and_cosmos_effect(tmp_path, monkeypatch, fault):
    from test_runtime_provider import native_model_client
    from test_policy_bundle import bundle_module
    from skills._shared.governance import validate_governance_contract
    import runtime
    store = MemoryCosmos()
    app = application(store)
    expected_write = fault in {"valid", "final-sale", "unmatched-order", "replay", "missing-reason",
                              "unknown-risk", "missing-profile-info", "missing-profile-no-order"}
    rma = "RMA-2026-004418" if fault in {"final-sale", "missing-profile-deny"} else "RMA-2026-004440" if fault in {"incomplete", "second-info"} else (
        "RMA-2026-004425" if fault == "risk-autoapprove" else RMA)
    outcome = "deny_refund" if fault in {"final-sale", "missing-profile-deny"} else (
        "request_more_info" if fault in {"incomplete", "unmatched-order", "missing-reason", "second-info",
                                        "unknown-risk", "missing-profile-info", "missing-profile-cross-customer",
                                        "missing-profile-boolean", "missing-profile-wrong-id",
                                        "missing-profile-forged", "missing-profile-no-order",
                                        "missing-profile-before-gate", "missing-profile-before-effect"}
        else "approve_refund")
    customer_id = store.docs[rma]["customer_id"]
    original_customer = deepcopy(app.backend.customers[customer_id])
    if fault.startswith("missing-profile-"):
        app.backend.customers.pop(customer_id)
    if fault == "missing-profile-cross-customer":
        app.backend.customers.pop("CR-CUST-00108")
    if fault == "missing-profile-boolean":
        app.backend.customers[customer_id] = {"not_found": True}
    if fault == "missing-profile-wrong-id":
        app.backend.customers[customer_id] = {"not_found": "CR-CUST-OTHER"}
    if fault == "missing-profile-no-order":
        app.backend.orders.pop(store.docs[rma]["order_id"])
    if fault == "unmatched-order":
        app.backend.orders.pop(store.docs[RMA]["order_id"])
    if fault in {"invalid-customer", "unknown-risk"}:
        app.backend.customers[store.docs[RMA]["customer_id"]].pop("lifetime_return_rate")
    if fault == "missing-reason":
        store.docs[rma]["reason_code"] = None
    if fault == "second-info":
        store.docs[rma]["info_requests"] = 1
    if fault == "missing-revision":
        store.docs[rma]["_etag"] = None
    bundle = bundle_module().build_bundle(
        source=EXAMPLE / "src/agent/governance/policy", destination=tmp_path / "policy",
        policy_id="returns-write-v1", version="1")

    class Authority:
        def verify(self, _):
            return runtime.VerifiedPolicy(bundle.bundle_digest, expires)
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    receipts = []
    class Audit:
        def append(self, **fields):
            if fault == "audit-down":
                raise OSError("remote-ack-unavailable")
            receipts.append(fields)
            if fault in {"missing-profile-before-effect", "profile-changed-after-gate"}:
                app.backend.customers[customer_id] = {**original_customer, "account_status": "review_flagged"}
            return "receipt-1"
    definition = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    provider = runtime.AcsGovernanceProvider(
        contract={key: definition[key] for key in ("framework", "governance", "tools")}, bundle_path=bundle.root,
        expected_digest=bundle.bundle_digest, bundle_verifier=bundle_module().verify_bundle,
        contract_validator=validate_governance_contract, signature_verifier=Authority(),
        safe_provider=app.safe_evidence, trusted_context_provider=app.trusted_context,
        audit=Audit(), principal="trusted-workload", tenant="trusted-tenant",
        agent_version="local-test", image_digest="sha256:" + "a"*64)
    provider.deployment_agent_id = "returns-triage"
    evaluated = []
    evaluate = provider._engine.evaluate_intervention_point
    async def capture(point, snapshot, **kwargs):
        evaluated.append(deepcopy(snapshot))
        result = await evaluate(point, snapshot, **kwargs)
        evaluated[-1]["native_result"] = str(result)
        return result
    provider._engine.evaluate_intervention_point = capture
    monkeypatch.setitem(sys.modules, "governance_application", app)
    from test_governance_wiring import module
    reference = ROOT / "skills/threadlight-deploy/references/governance"
    monkeypatch.syspath_prepend(str(reference))
    shared_host = module("maf-container")
    shared_host.BASE = EXAMPLE / "src/agent"
    monkeypatch.setitem(sys.modules, "governance_host", shared_host)
    container = importlib.import_module("container")
    if fault == "stale":
        store.before_batch = lambda: store.docs[RMA].update(_etag="2")
    client = native_model_client(sequence(rma, fault=fault, decision=outcome))
    if fault == "irrelevant-citation":
        content = client.responses[-2].messages[0].contents[0]
        args = json.loads(content.arguments)
        args["citations"] = ["policy#final-sale"]
        content.arguments = json.dumps(args)
    if fault in {"read-stale", "missing-profile-before-gate"}:
        snapshot = app.trusted_context
        async def change_before_snapshot(*args):
            if fault == "read-stale":
                store.docs[RMA]["_etag"] = "2"
            else:
                app.backend.customers[customer_id] = {**original_customer, "account_status": "review_flagged"}
            return await snapshot(*args)
        provider._trusted_context_provider = change_before_snapshot
    host = container.build_host(provider, client=client, configure_observability=None)
    async def scenario():
        try:
            await host._agent.run("triage")
            if fault == "replay":
                client.responses.extend(sequence())
                await host._agent.run("repeat exact decision")
        except Exception:
            if expected_write:
                raise
    asyncio.run(scenario())
    prompt = (EXAMPLE / "src/agent/copilot-instructions.md").read_text()
    assert client.requests and all(prompt in body.get("instructions", "") for _, body in client.requests)
    assert len(store.decisions) == (1 if expected_write else 0), json.dumps(
        {"receipts": receipts, "native_result": [s["native_result"] for s in evaluated],
         "tool_outputs": [v for v in client.requests[-1][0]
                         if v.get("type") == "function_call_output"]})
    if expected_write:
        assert store.docs[rma]["decision"] == outcome
        assert receipts[0]["decision"] == "allow"
        assert store.decisions[0]["action_hash"] == receipts[0]["action_hash"]
        if fault == "replay":
            result = [v for v in client.requests[-1][0] if v.get("type") == "function_call_output"][-1]
            assert json.loads(result["output"]).get("ok") is True
        if fault.startswith("missing-profile-"):
            trusted = evaluated[0]["trusted"]
            assert trusted["facts"]["customer"] == {"not_found": customer_id}
            assert trusted["reads"][-1] == {
                "name": "customer_get_profile", "result": {"not_found": customer_id}}
    elif fault in {"missing-profile-before-effect", "profile-changed-after-gate"}:
        assert receipts[0]["decision"] == "allow"
        assert len(store.reads) == 3, "actual backend must be read again after ACS/ACK"
    elif fault not in {"forged", "payment", "stale", "audit-down", "missing-profile-forged",
                       "missing-profile-before-effect", "profile-changed-after-gate"}:
        assert receipts[0]["decision"] == "deny", "real ACS must deny invalid backend context"
    assert len(evaluated) == (0 if fault in {"forged", "payment", "missing-profile-forged"}
                              else 2 if fault == "replay" else 1)


@pytest.mark.governance_runtime
def test_atomic_business_audit_retains_required_rule_and_actor_fields(tmp_path, monkeypatch):
    class AuditedCosmos(MemoryCosmos):
        async def execute_item_batch(self, batch_operations, partition_key):
            audit = batch_operations[1][1][0]
            assert audit["decision_trace"] == {"outcome": "approve_refund",
                                                "business_rules_fired": ["BR-001", "BR-005"]}
            assert audit["answer"]["citations"] == ["policy#return-window"]
            assert audit["actor"] == audit["principal"]
            return await super().execute_item_batch(batch_operations, partition_key)
    monkeypatch.setattr(sys.modules[__name__], "MemoryCosmos", AuditedCosmos)
    test_native_returns_safe_context_and_cosmos_effect(tmp_path, monkeypatch, "valid")


@pytest.mark.governance_runtime
@pytest.mark.parametrize("variant", ["allow", "deny"])
def test_optional_native_probe_only_proves_reserved_noop(tmp_path, monkeypatch, variant):
    import uuid
    import httpx
    import runtime
    from test_control_plane import Harness, MemoryStore, TENANT, WORKLOAD, OTHER, APP
    from test_probe_telemetry import probe_registry, fixture_module, expected
    from test_gateway import gateway, Credential
    from test_policy_bundle import bundle_module
    from test_runtime_provider import native_model_client, tool_responses
    from test_governance_wiring import module
    from govern_control_plane.probes import ProbeService
    from skills._shared.governance import validate_governance_contract
    store = MemoryCosmos()
    app = application(store)
    bundle = bundle_module().build_bundle(
        source=EXAMPLE / "src/agent/governance/policy", destination=tmp_path / "policy",
        policy_id="returns-write-v1", version="1")
    definition = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    binding = deepcopy(definition["tools"][-1])
    binding.update(id="governance_probe_noop", consequence="read")
    definition["tools"].append(binding)
    registry = probe_registry()
    registry["deployment"]["agent_id"] = "returns-triage"
    registry["actions"][0]["policy_binding"] = "returns-write-v1"
    registry["native_policy_digest"] = bundle.bundle_digest
    registry = gateway("dispatcher").Registry.model_validate(registry)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    async def scenario():
        h = Harness()
        native = ProbeService(store=MemoryStore(), registry=registry, policy_digest=bundle.bundle_digest,
                              producer="native", fresh=lambda: None)
        effects = ProbeService(store=MemoryStore(), registry=registry, policy_digest=bundle.bundle_digest,
                               producer="fixture", fresh=lambda: None)
        fixture = fixture_module().create_app(probes=effects, auth=h.auth, callers={OTHER: APP})
        downstream = gateway("dispatcher").DownstreamClient(
            credential=Credential(h.token(changes={"oid": OTHER})),
            transport=httpx.ASGITransport(app=fixture))
        from govern_control_plane.client import PolicySnapshot
        p = runtime.AcsGovernanceProvider(
            contract={k: definition[k] for k in ("framework", "governance", "tools")},
            bundle_path=bundle.root, expected_digest=bundle.bundle_digest,
            bundle_verifier=bundle_module().verify_bundle, contract_validator=validate_governance_contract,
            signature_verifier=PolicySnapshot(runtime.VerifiedPolicy(bundle.bundle_digest, expiry)),
            safe_provider=app.safe_evidence, trusted_context_provider=app.trusted_context,
            principal=WORKLOAD, tenant=TENANT, agent_version=registry.deployment.agent_version,
            image_digest=registry.deployment.image_digest, environment="preproduction",
            audit=runtime.DurableSpool(tmp_path / "probe-receipts"))
        p.deployment_agent_id = "returns-triage"
        telemetry = runtime.NativeProbeTelemetry(provider=p, service=native, downstream=downstream, client_id=APP)
        telemetry.auth = h.auth
        run = str(uuid.uuid4())
        for producer in (native, effects):
            await producer.register(run, expected(producer, variant))
        monkeypatch.setitem(sys.modules, "governance_application", app)
        monkeypatch.syspath_prepend(str(ROOT / "skills/threadlight-deploy/references/governance"))
        host_module = module("maf-container")
        host_module.BASE = EXAMPLE / "src/agent"
        host = host_module.build_host(p, client=native_model_client(tool_responses(
            "governance_probe_noop", {"probe_run_id": run, "variant": variant})),
            configure_observability=None)
        try:
            await host._agent.run("reserved noop only")
            state = await native.status(WORKLOAD, run)
            effect = await effects.status(WORKLOAD, run)
            assert state["terminal"] == ("completed" if variant == "allow" else "denied")
            assert state["counts"]["intercepted"] == 1
            assert effect["counts"]["effect"] == (1 if variant == "allow" else 0)
            assert not store.decisions
            assert p.health()["bindings"]["returns_apply_decision:pre_tool_call"]["status"] == "unverified"
        finally:
            await downstream.aclose()
            await h.close()
    asyncio.run(scenario())


@pytest.mark.governance_runtime
@pytest.mark.parametrize("review", ["absent", "invalid-human", "approved", "rejected"])
@pytest.mark.parametrize("overlap", ["complete", "flagged-missing-reason", "high-value-missing-photos",
                                    "wrong-request-more-info", "missing-profile",
                                    "missing-profile-wrong-info", "missing-profile-insert-during-approval"])
def test_native_supervisor_approval_remote_ack_and_one_use(tmp_path, monkeypatch, review, overlap):
    import base64
    import httpx
    import runtime
    from test_control_plane import Harness, WORKLOAD, TENANT, KEY
    from test_governance_wiring import module
    from test_policy_bundle import bundle_module
    from test_runtime_provider import native_model_client
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    from azure.core.credentials import AccessToken
    store = MemoryCosmos()
    app = application(store)
    if overlap in {"flagged-missing-reason", "wrong-request-more-info"}:
        store.docs["RMA-2026-004425"]["reason_code"] = None
        customer = app.backend.customers[store.docs["RMA-2026-004425"]["customer_id"]]
        customer.update(account_status="review_flagged", lifetime_return_rate=0.8)
    elif overlap == "high-value-missing-photos":
        store.docs["RMA-2026-004425"].update(reason_code="arrived_damaged", photos_provided=False)
    if overlap.startswith("missing-profile"):
        customer_id = store.docs["RMA-2026-004425"]["customer_id"]
        original_customer = app.backend.customers.pop(customer_id)
    wrong_info = overlap in {"wrong-request-more-info", "missing-profile-wrong-info"}
    monkeypatch.setitem(sys.modules, "governance_application", app)
    monkeypatch.syspath_prepend(str(ROOT / "skills/threadlight-deploy/references/governance"))
    host_module = module("maf-container")
    host_module.BASE = tmp_path
    (tmp_path / "copilot-instructions.md").write_bytes(
        (EXAMPLE / "src/agent/copilot-instructions.md").read_bytes())
    bundle = bundle_module().build_bundle(
        source=EXAMPLE / "src/agent/governance/policy", destination=tmp_path / "policy",
        policy_id="returns-write-v1", version="1")
    definition = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    config = dict(contract={k: definition[k] for k in ("framework", "governance", "tools")},
                  tenant_id=TENANT, key_id=KEY, policy_id="returns-write-v1", policy_version="1",
                  policy_digest=bundle.bundle_digest, control_plane_url="https://control.example",
                  control_plane_scope="api://governance/.default", audit_delivery="remote-ack",
                  spool_dir=str(tmp_path / "audit"), principal=WORKLOAD, approver_roles=["Approver"],
                  agent_id="returns-triage", agent_version="local-test", image_digest="sha256:" + "a"*64)
    async def scenario():
        h = Harness()
        workload = h.settings.workloads[WORKLOAD]
        h.settings.workloads[WORKLOAD] = workload.model_copy(
            update={"agent_id": "returns-triage", "policies": ["returns-write-v1"]})
        envelope = BundleEnvelope(
            policy_id="returns-write-v1", version="1", content_digest=bundle.bundle_digest,
            tenant_id=TENANT, key_id=KEY, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        await h.service.publish(envelope)
        signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
            await h.signer.sign(envelope_digest(envelope))).decode())
        (tmp_path / "policy-envelope.json").write_bytes(canonical(signed))

        class Credential:
            async def get_token(self, *args, **kwargs):
                return AccessToken(h.token(), 4102444800)
            async def close(self):
                pass
        provider = await host_module.build_provider(config, signer=h.signer, credential=Credential())
        assert provider._trusted_context_provider == app.trusted_context, "generated host must wire trusted producer"
        provider.timeout = 0.75
        provider.approval_resolver.timeout = 0.65
        provider.approval_resolver.poll_interval = 0.01
        await provider.approval_resolver.http.aclose()
        pending, grants = [], []
        class ReviewedTransport(httpx.ASGITransport):
            async def handle_async_request(self, request):
                response = await super().handle_async_request(request)
                body = json.loads(request.content) if request.content else {}
                if request.url.path == "/approvals/resolve" and body.get("operation") == "request":
                    assert response.status_code == 202
                    assert not store.decisions
                    pending.append(body["intent"])
                    if review != "absent":
                        headers = h.headers(human=True, changes={"roles": ["WrongRole"]}
                                            if review == "invalid-human" else None)
                        decided = await h.client.post("/approvals/resolve", headers=headers, json={
                            "operation": "decide", "intent": body["intent"],
                            "approved": review == "approved", "approving_role": "Approver"})
                        assert decided.status_code == (403 if review == "invalid-human" else 200)
                        if decided.status_code == 200:
                            grants.append(decided.json()["grant"])
                            if overlap == "missing-profile-insert-during-approval":
                                app.backend.customers[customer_id] = original_customer
                return response
        provider.approval_resolver.http = httpx.AsyncClient(
            transport=ReviewedTransport(app=h.app), base_url="https://control.example")
        provider.audit.credential_factory = Credential
        provider.audit.transport_factory = lambda: httpx.ASGITransport(app=h.app)
        def require_ack_before_effect():
            receipts = [doc["receipt"] for doc, _ in h.store.docs.values()
                        if doc.get("receipt", {}).get("decision") == "allow"]
            assert receipts, "Task8 remote durable ACK, not a local spool, must precede Cosmos write"
        store.before_batch = require_ack_before_effect
        try:
            async with provider.audit:
                client = native_model_client(sequence(
                        "RMA-2026-004425", decision="request_more_info" if wrong_info
                        else "escalate_to_supervisor"))
                host = host_module.build_host(provider, client=client, configure_observability=None)
                await host._agent.run("triage high value")
            prompt = (EXAMPLE / "src/agent/copilot-instructions.md").read_text()
            assert client.requests and all(prompt in body.get("instructions", "")
                                           for _, body in client.requests)
            assert len(pending) == (0 if wrong_info else 1)
            if wrong_info:
                assert any(doc.get("receipt", {}).get("decision") == "deny"
                           for doc, _ in h.store.docs.values()), "selected binding must deny wrong model choice"
            effect = review == "approved" and not wrong_info and overlap != "missing-profile-insert-during-approval"
            assert len(store.decisions) == int(effect)
            if effect:
                assert store.docs["RMA-2026-004425"]["status"] == "escalated"
                assert store.docs["RMA-2026-004425"]["decision"] == "escalate_to_supervisor"
                replay = await h.post("consume", intent=pending[0], grant=grants[0])
                assert replay.status_code == 409
        finally:
            await provider.approval_resolver.aclose()
            await h.close()
    asyncio.run(scenario())
