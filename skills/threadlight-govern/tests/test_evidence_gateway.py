"""Evidence PEP tests with real native ACS/OPA, Entra, MCP and approval protocol."""
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx
import jwt
import pytest

from test_evidence_attestations import fixture
from test_gateway import Credential, GatewayHarness, gateway, registry

pytestmark = pytest.mark.governance_runtime


def test_without_evidence_post_policy_still_reads_current_host_facts(tmp_path):
    async def run():
        h = await GatewayHarness().initialize(
            tmp_path, post='{"decision":"deny"} if input.snapshot.safe.release == false else := {"decision":"allow"}')
        state = {"release": True}
        h.dispatcher.safe_provider = lambda facts: {"scope": facts["scope"], **state}
        request = h.downstream.request
        async def changed(**kwargs):
            result = await request(**kwargs)
            state["release"] = False
            return result
        h.downstream.request = changed
        try:
            result = await h.call()
            assert result == {"status": "blocked", "reason_code": "output_denied"}
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


async def harness(path, *, deferred=False, inline=False):
    e, signer, req, identity, args, result, adapter, provider, facts = fixture()
    document = registry()
    action = document["actions"][0]
    action["input_schema"]["properties"].update(
        case_id={"type": "string", "maxLength": 128},
        expected_etag={"type": "string", "maxLength": 128})
    action["input_schema"]["properties"]["amount"]["maximum"] = 2000
    action["input_schema"]["required"] += ["case_id", "expected_etag"]
    action["evidence_requirement"] = req.model_dump()
    if deferred:
        action.update(approval_mode="deferred", approval_roles=["Approver"], approval_requirement="always")
    elif inline:
        action["approval_roles"] = ["Approver"]
    h = await GatewayHarness().initialize(
        path, document=document,
        decision='{"decision":"allow"} if input.snapshot.safe.evidence.claims.purchase_verified == true else := {"decision":"deny"}')
    if deferred or inline:
        h.approval = gateway("receipts").HTTPControlPlaneApprovalService(
            base_url="https://control.example", scope="api://governance/.default",
            credential=Credential(h.cp.token()), http=h.cp.client, poll_interval=0.01)
        h.dispatcher = h.new_dispatcher()
    token = (await provider.issue(identity, args))["attestation"]
    return h, e, signer, req, identity, args, adapter, provider, token


def test_evidence_required_before_policy_and_automatic_allow(tmp_path, caplog):
    async def run():
        h, e, _, _, _, args, _, _, token = await harness(tmp_path)
        try:
            assert (await h.call(arguments=args))["reason_code"] == "evidence_required"
            assert not h.calls
            reply = await h.call(arguments=args, evidence_token=token)
            assert reply["status"] == "completed", reply
            assert len(h.calls) == 1 and json.loads(h.calls[0].content) == args
            assert token not in str(h.calls[0].headers)
            records = json.dumps([*h.store.docs.values(), *h.receipt_bodies()])
            assert token not in records + caplog.text
            assert "purchase_verified" not in records
            assert h.receipt_bodies()[-1]["evidence_fingerprint"].startswith("sha256:")
        finally:
            await h.close()
    asyncio.run(run())


def test_renewal_preserves_consent_but_changed_evidence_does_not(tmp_path):
    async def run():
        h, e, _, _, identity, args, adapter, provider, token = await harness(tmp_path, deferred=True)
        try:
            pending = await h.call(arguments=args, evidence_token=token)
            assert pending["status"] == "pending_approval", pending
            assert token not in json.dumps(pending)
            assert (await h.cp.post("decide", human=True, intent=pending["approval_intent"],
                                   approved=True, approving_role="Approver")).status_code == 200
            original = adapter.verify
            async def changed(*values):
                result = await original(*values)
                return result.model_copy(update={"claims": {**result.claims, "defect_verified": True}})
            adapter.verify = changed
            changed_token = (await provider.issue(identity, args))["attestation"]
            rejected = await h.call(arguments=args, evidence_token=changed_token)
            assert rejected["reason_code"] == "approval_context_changed"
            assert not h.calls
            adapter.verify = original
            renewed = (await provider.issue(identity, args))["attestation"]
            assert renewed != token
            resumed = await h.call(arguments=args, evidence_token=renewed)
            assert resumed["status"] == "completed", resumed
            assert len(h.calls) == 1
            # Completed outcome retrieval requires no live attestation or provider.
            replay = await h.call(arguments=args)
            assert replay == resumed and len(h.calls) == 1 and len(h.gets) == 1
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("deferred", [False, True])
def test_expiry_during_credential_wait_never_sends(tmp_path, deferred):
    async def run():
        h, e, signer, req, identity, args, _, provider, token = await harness(tmp_path, deferred=deferred)
        try:
            if deferred:
                pending = await h.call(arguments=args, evidence_token=token)
                await h.cp.post("decide", human=True, intent=pending["approval_intent"],
                                approved=True, approving_role="Approver")
            claims = jwt.decode(token, options={"verify_signature": False})
            claims.update(iat=int(time.time()), exp=int(time.time()) + 1)
            token = jwt.encode(claims, signer.key, algorithm="RS256",
                               headers={"kid": req.keys[0].kid, "typ": e.EVIDENCE_TYPE})
            async def wait():
                await asyncio.sleep(1.1)
            h.credential.hook = wait
            result = await h.call(arguments=args, evidence_token=token)
            assert result["reason_code"] == "evidence_expired", result
            assert not h.calls
        finally:
            await h.close()
    asyncio.run(run())


def test_unknown_outcome_never_reopens_on_renewal(tmp_path):
    async def run():
        h, _, _, _, identity, args, _, provider, token = await harness(tmp_path)
        try:
            h.downstream_status = 503
            assert (await h.call(arguments=args, evidence_token=token))["status"] == "unavailable"
            h.downstream_status = 200
            token = (await provider.issue(identity, args))["attestation"]
            assert (await h.call(arguments=args, evidence_token=token))["reason_code"] == "outcome_unknown"
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


def test_maf_hoists_evidence_into_request_metadata(tmp_path):
    async def run():
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "threadlight-deploy/tests"))
        from test_maf_gateway_client import client_module
        h, e, _, _, _, args, _, _, token = await harness(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app):
                async def authorize():
                    h.policy.fresh()
                client = client_module().GovernedMCPTools(
                    url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=Credential(h.cp.token()), authorize=authorize, selected_tools=["refund"],
                    transport_factory=lambda: httpx.ASGITransport(app=app))
                await client.connect()
                result = await client.functions[0].invoke(
                    arguments={**args, e.EVIDENCE_ARGUMENT: token}, skip_parsing=True)
                assert result == {"status": "refunded"}
                assert json.loads(h.calls[0].content) == args
        finally:
            await h.close()
    asyncio.run(run())


def test_concurrent_evidence_use_has_only_one_effect(tmp_path):
    async def run():
        h, _, _, _, _, args, _, _, token = await harness(tmp_path)
        try:
            results = await asyncio.gather(
                h.call(arguments=args, evidence_token=token),
                h.call(arguments=args, evidence_token=token, dispatcher=h.new_dispatcher()))
            assert any(result["status"] == "completed" for result in results)
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("expire_while_reviewing", [False, True])
def test_inline_human_approval_never_substitutes_for_current_evidence(tmp_path, expire_while_reviewing):
    async def run():
        h, e, signer, req, _, args, _, _, token = await harness(tmp_path, inline=True)
        if expire_while_reviewing:
            claims = jwt.decode(token, options={"verify_signature": False})
            claims.update(iat=int(time.time()), exp=int(time.time()) + 1)
            token = jwt.encode(claims, signer.key, algorithm="RS256",
                               headers={"kid": req.keys[0].kid, "typ": e.EVIDENCE_TYPE})
        post = h.approval.post
        intents = []
        async def human(body):
            response = await post(body)
            if body["operation"] == "request" and response[0] == 202:
                intents.append(body["intent"])
                if expire_while_reviewing:
                    await asyncio.sleep(1.1)
                assert (await h.cp.post("decide", human=True, intent=body["intent"],
                                       approved=True, approving_role="Approver")).status_code == 200
            return response
        h.approval.post = human
        try:
            assert (await h.call(arguments=args))["reason_code"] == "evidence_required"
            assert not intents
            result = await h.call(arguments=args, evidence_token=token)
            assert len(intents) == 1
            assert result["status"] == ("blocked" if expire_while_reviewing else "completed"), result
            assert len(h.calls) == (0 if expire_while_reviewing else 1)
        finally:
            await h.close()
    asyncio.run(run())


def test_completed_effect_survives_expiry_and_provider_outage(tmp_path):
    async def run():
        h, e, signer, req, _, args, adapter, _, token = await harness(tmp_path)
        claims = jwt.decode(token, options={"verify_signature": False})
        claims.update(iat=int(time.time()), exp=int(time.time()) + 1)
        token = jwt.encode(claims, signer.key, algorithm="RS256",
                           headers={"kid": req.keys[0].kid, "typ": e.EVIDENCE_TYPE})
        request = h.downstream.request
        async def slow_reply(**kwargs):
            result = await request(**kwargs)
            if not kwargs.get("retrieve"):
                await asyncio.sleep(1.1)
            return result
        async def unavailable(*_):
            raise OSError("provider offline")
        adapter.verify = unavailable
        h.downstream.request = slow_reply
        try:
            result = await h.call(arguments=args, evidence_token=token)
            assert result["status"] == "completed", result
            assert (await h.call(arguments=args, evidence_token=token)) == result
            assert len(h.calls) == 1 and len(h.gets) == 1
        finally:
            await h.close()
    asyncio.run(run())


def test_same_pep_and_native_rego_accept_loan_fixture_claims(tmp_path):
    async def run():
        e, signer, requirement, identity, args, _, adapter, _, _ = fixture()
        requirement = requirement.model_copy(update={"profile": "loan-income-fixture-v1"})
        document = registry()
        action = document["actions"][0]
        action.update(name="loan_record_decision", evidence_requirement=requirement.model_dump())
        action["input_schema"]["properties"].update(
            case_id={"type": "string", "maxLength": 128},
            expected_etag={"type": "string", "maxLength": 128})
        action["input_schema"]["properties"]["amount"]["maximum"] = 2000
        action["input_schema"]["required"] += ["case_id", "expected_etag"]
        h = await GatewayHarness().initialize(
            tmp_path, document=document,
            decision='{"decision":"allow"} if input.snapshot.safe.evidence.claims.income_corroborated == true else := {"decision":"deny"}')
        payroll = None
        async def verify(*_):
            if payroll is None:
                return None
            return e.VerificationResult(
                subject="customer-1", case_id=args["case_id"], revision=args["expected_etag"],
                sources=[e.SourceReference(reference="fixture-payroll", revision="v1",
                                           digest=e.fingerprint(payroll))],
                claims={"income_corroborated": True, "annual_income": payroll["annual_income"]})
        adapter.verify = verify
        provider = e.EvidenceProvider(
            requirement=requirement, action=action["name"], tenant=identity.tenant,
            signer=signer, key_id=requirement.keys[0].kid, adapter=adapter,
            grants=[e.EvidenceGrant(principal=identity.subject, client=identity.client,
                                    case_id=args["case_id"], subject="customer-1")])
        try:
            assert (await provider.issue(identity, args))["status"] == "insufficient_evidence"
            payroll = {"annual_income": 50000}
            token = (await provider.issue(identity, args))["attestation"]
            result = await h.dispatcher.dispatch(
                authorization="Bearer " + h.cp.token(), action=action["name"], arguments=args,
                idempotency_key="loan-fixture-1", evidence_token=token)
            assert result["status"] == "completed", result
            assert len(h.calls) == 1
        finally:
            await h.close()
    asyncio.run(run())


def test_ghcp_relay_binds_and_hoists_evidence_without_resume_claim(tmp_path):
    async def run():
        import importlib.util
        path = Path(__file__).resolve().parents[2] / "threadlight-deploy/references/governance/ghcp-container.py"
        spec = importlib.util.spec_from_file_location("evidence_ghcp_relay", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        h, e, _, _, _, args, _, _, token = await harness(tmp_path)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app)) as http:
                relay = module.McpRelay(
                    gateway_url="https://gateway.example/mcp", scope="api://gateway/.default",
                    credential=Credential(h.cp.token()), invocation_id="fixture", tools=["refund"], http=http)
                arguments = {**args, e.EVIDENCE_ARGUMENT: token}
                ticket = await relay.pre_mcp({
                    "serverName": "threadlight-governed", "toolName": "refund", "toolCallId": "c1",
                    "sessionId": "s1", "arguments": arguments}, {})
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app()),
                                            base_url="http://relay") as client:
                    response = await client.post("/mcp", headers={
                        "X-Threadlight-Relay": relay.secret, "MCP-Protocol-Version": "2025-03-26"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                            "name": "refund", "arguments": arguments, "_meta": ticket["metaToUse"]}})
                    assert response.status_code == 200, response.text
                    assert response.json()["result"]["structuredContent"]["status"] == "completed"
                    assert json.loads(h.calls[0].content) == args
        finally:
            await h.close()
    asyncio.run(run())
