"""Trusted cooperative-host recorder. Never substitutes a PDP/PEP or trusts model JSON.

Only fd 3 carries proof (Docker attaches that fd to a private parent pipe);
ordinary stdout/stderr, including model output, cannot become evidence.
"""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def scenario(root, project, fixture, scratch, emit, *, case, mode="interactive", review=None):
    import httpx
    import yaml
    import jsonschema
    from azure.core.credentials import AccessToken
    from skills._shared.local_control_fixture import ControlFixture, TENANT, WORKLOAD, KEY
    from skills._shared.local_model_fixture import native_model_client, tool_responses
    from govern_control_plane.models import BundleEnvelope, SignedBundle, canonical, envelope_digest
    from govern_bundle.policy_bundle import build_bundle

    directory = scratch / (mode + "-" + case)
    directory.mkdir()
    factory_path = project / "src/agent/governance_host.py"
    if not factory_path.exists():
        factory_path = root / "skills/threadlight-deploy/references/governance/maf-container.py"
    factory = load("governance_host", factory_path)
    factory.BASE = directory
    store = fixture.MemoryCosmos()
    app = fixture.application(store)
    app_module = importlib.import_module("governance_application")
    app_module.tools, app_module.middleware = app.tools, app.middleware
    app_module._application = app
    app_module.trusted_context = app.trusted_context
    app_module.safe_evidence = app.safe_evidence
    container = load("container", project / "src/agent/container.py")
    assert container.build_host is factory.build_host
    bundle = build_bundle(source=project / "src/agent/governance/policy",
                          destination=directory / "policy", policy_id="returns-write-v1", version="1")
    definition = yaml.safe_load((project / "agent.yaml").read_text())
    selected = next(t for t in definition["tools"] if t["id"] == "returns_apply_decision")
    h = ControlFixture(agent_id="returns-triage", policy_id="returns-write-v1")
    envelope = BundleEnvelope(policy_id="returns-write-v1", version="1", content_digest=bundle.bundle_digest,
                              tenant_id=TENANT, key_id=KEY,
                              expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    await h.service.publish(envelope)
    signed = SignedBundle(envelope=envelope, signature=base64.b64encode(
        await h.signer.sign(envelope_digest(envelope))).decode())
    (directory / "policy-envelope.json").write_bytes(canonical(signed))

    class Credential:
        async def get_token(self, *args, **kwargs):
            return AccessToken(h.token(), 4102444800)
        async def close(self):
            pass

    config = dict(contract={k: definition[k] for k in ("framework", "governance", "tools")},
                  tenant_id=TENANT, key_id=KEY, policy_id="returns-write-v1", policy_version="1",
                  policy_digest=bundle.bundle_digest, control_plane_url="https://control.example",
                  control_plane_scope="api://governance/.default", audit_delivery="remote-ack",
                  spool_dir=str(directory / "audit"), principal=WORKLOAD, approver_roles=["Approver"],
                  agent_id="returns-triage", agent_version="local-fixture",
                  image_digest="sha256:" + "0" * 64)
    provider = await factory.build_provider(config, signer=h.signer, credential=Credential())
    provider.timeout = 1.0
    provider.approval_resolver.timeout = 0.65
    provider.approval_resolver.poll_interval = 0.01
    await provider.approval_resolver.http.aclose()
    pending, grants, decisions, evaluations = [], [], [], []
    identity = {"action_id": "returns_apply_decision", "mode": mode, "case": case}
    def record(event, **fields):
        emit(event, **identity, **fields)

    class ReviewedTransport(httpx.ASGITransport):
        async def handle_async_request(self, request):
            body = json.loads(request.content) if request.content else {}
            if body.get("operation") == "consume" and review == "changed":
                changed = deepcopy(body)
                changed["intent"]["action_hash"] = "sha256:" + "f" * 64
                response = await h.client.post("/approvals/resolve", headers=h.headers(), json=changed)
                record("approval_changed", status=response.status_code,
                       intent_hash=digest(changed["intent"]), grant_hash=digest(changed["grant"]))
                return response
            response = await super().handle_async_request(request)
            if request.url.path == "/approvals/resolve" and body.get("operation") == "consume":
                record("approval_consume", status=response.status_code,
                       intent_hash=digest(body["intent"]), grant_hash=digest(body["grant"]),
                       nonce_hash=digest(body["intent"]["nonce"]),
                       action_hash=body["intent"]["action_hash"], policy_hash=body["intent"]["policy_hash"])
            if request.url.path == "/approvals/resolve" and body.get("operation") == "request":
                pending.append(body["intent"])
                record("approval_request", intent_hash=digest(body["intent"]),
                       nonce_hash=digest(body["intent"]["nonce"]), status=response.status_code)
                assert not store.decisions or case == "approved"
                if review not in {None, "absent"}:
                    result = await h.client.post("/approvals/resolve", headers=h.headers(
                        human=True, changes={"roles": ["WrongRole"]} if review == "invalid-human" else None),
                        json={"operation": "decide", "intent": body["intent"],
                              "approved": review not in {"rejected"}, "approving_role": "Approver"})
                    record("human_decision", status=result.status_code, approved=review != "rejected",
                           intent_hash=digest(body["intent"]), grant_hash=digest(result.json().get("grant")))
                    if result.status_code == 200:
                        grants.append(result.json()["grant"])
                if review == "expired":
                    await asyncio.sleep(1.1)
            return response

    provider.approval_resolver.http = httpx.AsyncClient(
        transport=ReviewedTransport(app=h.app), base_url="https://control.example")
    provider.audit.credential_factory = Credential
    provider.audit.transport_factory = lambda: httpx.ASGITransport(app=h.app)
    evaluate = provider._engine.evaluate_intervention_point
    async def observed_evaluation(point, snapshot, **kwargs):
        assert snapshot["trusted"]["tool_call"]["name"] == identity["action_id"]
        result = await evaluate(point, snapshot, **kwargs)
        evaluations.append(result.verdict.decision.value)
        record("native_evaluation", point=str(point), snapshot_hash=digest(snapshot),
               result_hash=digest(str(result)), native_decision=result.verdict.decision.value,
               read_names=[r["name"] for r in snapshot["trusted"]["reads"]])
        return result
    provider._engine.evaluate_intervention_point = observed_evaluation
    append = provider.audit.append
    def observed_ack(**fields):
        assert fields["action_id"] == identity["action_id"]
        try:
            receipt_id = append(**fields)
        except Exception as error:
            record("audit_failure", exception_class=type(error).__name__)
            raise
        receipt = next(doc["receipt"] for doc, _ in h.store.docs.values()
                       if doc.get("receipt", {}).get("receipt_id") == receipt_id)
        assert not {"arguments", "prompt", "output", "rationale", "citations"} & receipt.keys()
        decisions.append(fields["decision"])
        record("pre_action_decision", decision=fields["decision"], receipt_id=receipt_id,
               action_hash=fields["action_hash"], policy_hash=fields["policy_hash"],
               receipt_hash=digest(receipt), receipt=deepcopy(receipt))
        return receipt_id
    provider.audit.append = observed_ack
    if case == "audit-down":
        h.store.failed = True
    execute_batch = store.execute_item_batch
    async def observed_batch(batch_operations, partition_key):
        audit = batch_operations[1][1][0]
        actual_arguments = {key: audit[key] for key in arguments}
        assert decisions[-1] == "allow"
        receipt = next(doc["receipt"] for doc, _ in h.store.docs.values()
                       if doc.get("receipt", {}).get("action_hash") == audit["action_hash"])
        assert receipt["decision"] == "allow" and receipt["policy_digest"] == audit["policy_hash"]
        result = await execute_batch(batch_operations, partition_key)
        record("invocation", argument_hash=digest(actual_arguments), original_argument_hash=digest(arguments),
               action_hash=audit["action_hash"], receipt_hash=digest(receipt),
               effect_count=len(store.decisions))
        return result
    store.execute_item_batch = observed_batch
    rma = fixture.RISK_RMA if review is not None else fixture.RMA
    decision = "escalate_to_supervisor" if review is not None else "approve_refund"
    if case == "incomplete":
        store.docs[rma]["reason_code"] = None
        decision = "request_more_info"
    if case == "risk-incomplete":
        store.docs[rma].update(reason_code=None)
        app.backend.customers[store.docs[rma]["customer_id"]].update(
            account_status="review_flagged", lifetime_return_rate=0.8)
    if case.startswith("missing-profile-"):
        app.backend.customers.pop(store.docs[rma]["customer_id"])
        if case in {"missing-profile-low", "missing-profile-wrong-info"}:
            decision = "request_more_info"
    responses = fixture.sequence(rma, fault="missing" if case == "deny" else
                                 "schema" if case == "schema" else "valid", decision=decision)
    arguments = json.loads(responses[-2].messages[0].contents[0].arguments)
    client = native_model_client(responses)
    # Only credentials/policy state are local fixtures. The model gets the actual
    # served instructions and SkillsProvider tree, not corrected root AGENTS.md.
    factory.BASE = project / "src/agent"
    host = container.build_host(provider, client=client, configure_observability=None)
    route = f"{type(host).__module__}:{type(host).__qualname__}._agent"
    record("routing_target", expected_resolved_path=route)
    record("resolved_path", resolved_path=route)
    try:
        async with provider.audit:
            if mode == "interactive" and case == "allow":
                client.responses[0:0] = tool_responses("returns_list_open", {})
                await host._agent.run("list open returns")
                assert store.queries == 1 and not store.decisions and not evaluations
                record("unbound_read", tool_id="returns_list_open", queries=store.queries,
                       acs_evaluations=len(evaluations), effects=len(store.decisions))
            if mode == "direct-tool":
                tool = next(t for t in app.tools if t.name == "returns_apply_decision")
                try:
                    await tool.invoke(arguments=arguments)
                except Exception as error:
                    record("direct_guard", exception_class=type(error).__name__)
                assert not store.decisions
            elif mode == "subagent":
                from agent_framework import Agent, FunctionTool
                async def delegate():
                    return (await host._agent.run("triage")).text
                parent = Agent(client=native_model_client(tool_responses("delegate", {})),
                               tools=[FunctionTool(name="delegate", func=delegate)])
                await parent.run("delegate triage")
                record("nested_host", child_factory_hash=hashlib.sha256(factory_path.read_bytes()).hexdigest())
            elif mode == "background":
                async def work():
                    return await host._agent.run("triage")
                await asyncio.create_task(work())
                record("background_completed")
            else:
                await host._agent.run("triage")
                if mode == "batch":
                    client.responses.extend(fixture.sequence(rma, fault="missing"))
                    await host._agent.run("next batch item without evidence")
                    record("batch_completed", items=2)
            prompt_bytes = (project / "src/agent/copilot-instructions.md").read_bytes()
            for request, body in client.requests:
                assert prompt_bytes.decode() in body.get("instructions", "")
                record("model_instructions", served_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
                       request_sha256=digest(body["instructions"]),
                       scope="actual HTTP instructions; scripted responses, not model business reasoning")
                for item in request:
                    if item.get("type") == "function_call_output":
                        try:
                            value = json.loads(item["output"])
                        except json.JSONDecodeError:
                            continue
                        if isinstance(value, dict) and value.get("ok") is True:
                            jsonschema.validate(value, selected["output_schema"])
                            record("schema_output", output_hash=digest(value))
            if case == "approved" and grants:
                response = await h.client.post("/approvals/resolve", headers=h.headers(),
                    json={"operation": "consume", "intent": pending[0], "grant": grants[0]})
                record("approval_replay", status=response.status_code,
                       intent_hash=digest(pending[0]), grant_hash=digest(grants[0]))
                client.responses.extend(fixture.sequence(
                    "RMA-2026-004440", decision="escalate_to_supervisor"))
                arguments = json.loads(client.responses[-2].messages[0].contents[0].arguments)
                await host._agent.run("fresh second handoff")
                record("approval_fresh", requests=len(pending), effects=len(store.decisions),
                       distinct_nonces=len({p["nonce"] for p in pending}))
            if case == "expired" and grants:
                await asyncio.sleep(1.1)
                response = await h.client.post("/approvals/resolve", headers=h.headers(),
                    json={"operation": "consume", "intent": pending[0], "grant": grants[0]})
                record("approval_expired", status=response.status_code,
                       intent_hash=digest(pending[0]), grant_hash=digest(grants[0]))
        record("terminal", effects=len(store.decisions), reads=len(store.reads),
               decisions=decisions, approval_requests=len(pending),
               binding_status=provider.health()["bindings"]["returns_apply_decision:pre_tool_call"]["status"])
    finally:
        await provider.approval_resolver.aclose()
        await h.close()


def main():
    payload = json.load(sys.stdin)
    root, project, scratch = (Path(payload[k]).resolve() for k in ("root", "project", "scratch"))
    fd = int(payload["proof_fd"])
    os.set_inheritable(fd, False)
    sys.path[:0] = [str(root), str(project / "src/agent"),
                    str(root / "skills/threadlight-govern/references"),
                    str(root / "skills/threadlight-deploy/references/governance")]
    counter = 0
    def emit(event, **fields):
        nonlocal counter
        counter += 1
        record = {"event": event, "proof_nonce": payload["proof_nonce"], "counter": counter, **fields}
        data = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        while data:
            data = data[os.write(fd, data):]
    from skills._shared.native_validation import observe
    from native_local import source_fingerprints
    pins = json.loads((root / "skills/_shared/governance-upstream-pin.json").read_text())
    wheelhouse = root / ".governance-validation/wheels"
    if not wheelhouse.is_dir():
        wheelhouse = root / ".governance-validation/deployment-wheels"
    emit("installed", observation=observe(pins, wheelhouse, root=root),
         source_hashes=source_fingerprints(root, project))
    fixture = load("returns_local_fixture", project / "scripts/local_probe.py")
    async def run():
        for mode in payload["execution_modes"]:
            for case in ("allow", "deny"):
                await scenario(root, project, fixture, scratch, emit, case=case, mode=mode)
        for case, review in (("approved", "approved"), ("rejected", "rejected"),
                             ("absent", "absent"), ("invalid-human", "invalid-human"),
                             ("expired", "expired"), ("changed", "changed"),
                             ("risk-incomplete", "approved"), ("audit-down", None),
                             ("incomplete", None), ("schema", None),
                             ("missing-profile-low", None), ("missing-profile-risk", "approved"),
                             ("missing-profile-absent", "absent"),
                             ("missing-profile-wrong-info", "approved")):
            await scenario(root, project, fixture, scratch, emit, case=case, review=review)
    asyncio.run(run())
    observe(pins, wheelhouse, root=root)
    emit("complete")


if __name__ == "__main__":
    main()
