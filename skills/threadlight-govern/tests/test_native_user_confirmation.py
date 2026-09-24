"""Actual native requester-mail witness, distinct from third-party ApprovalGrant."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from test_control_plane import HUMAN, TENANT, WORKLOAD, module
from test_gateway import Credential, gateway
from test_confirmation_gateway import harness, call, new_dispatcher
from test_user_confirmation import USER, user_headers
from test_outlook_approval import NativeHarness, SENDER, WORKFLOW, VERSION, RUN, EMAIL, HOME_USER

URL = ("https://prod-1.region.logic.azure.com/workflows/abc/"
       "triggers/User_confirmation_requested/paths/invoke?api-version=2016-10-01")


class NativeConfirmationHarness:
    async def initialize(self, control, *, user=USER):
        self.h = control
        template = await NativeHarness().initialize(control=control, actions=("refund",))
        self.workflow = deepcopy(template.workflow)
        await template.http.aclose()
        self.h.service.outlook = None
        self.workflow["properties"]["definition"]["triggers"] = {
            "User_confirmation_requested": {"type": "Request", "kind": "Http"}}
        self.state = "Running"
        self.posts = 0
        self.fail_after_send = False
        self.mutation = lambda value: None
        self.recovery_candidates = None
        self.recipient_user = user
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.transport))
        self.old_http = self.h.service.confirmation.http
        self.h.service.confirmation.http = self.http
        self.h.service.confirmation.credential = Credential()
        profile = self.h.service.confirmation.profile("email-basic").model_dump(mode="json")
        profile.update(kind="outlook-native", notification_url=URL,
            notification_scope="https://management.azure.com//.default",
            outlook={"workflow_resource_id": WORKFLOW, "workflow_version": VERSION,
                "workflow_digest": module("outlook").workflow_digest(self.workflow),
                "sender_principal": SENDER, "recipient": EMAIL,
                "home_tenant": TENANT, "home_subject": user})
        self.h.service.confirmation.config.profiles["email-basic"] = module("confirmation").parse(
            module("confirmation").ProviderProfile, json.dumps(profile).encode())
        return self

    async def transport(self, request):
        assert request.headers["authorization"] == "Bearer downstream-only"
        trace = request.extensions.get("trace")
        if trace:
            await trace("http11.send_request_headers.started", {})
            await trace("http11.send_request_body.started", {})
        if request.method == "POST":
            assert str(request.url) == URL
            self.posts += 1
            self.payload = json.loads(request.content)
            assert request.headers["x-ms-client-tracking-id"] == self.payload["confirmation_id"]
            self.started = datetime.now(timezone.utc).isoformat()
            if self.fail_after_send:
                self.fail_after_send = False
                raise httpx.ReadTimeout("ambiguous native send", request=request)
            return httpx.Response(202, headers={"x-ms-workflow-run-id": RUN})
        if request.url.path == WORKFLOW:
            return httpx.Response(200, json=self.workflow)
        if request.url.path == WORKFLOW + "/runs":
            candidates = self.recovery_candidates
            if candidates is None:
                candidates = [{"name": RUN, "id": WORKFLOW + "/runs/" + RUN, "properties": {
                    "correlation": {"clientTrackingId": self.payload["confirmation_id"]}}}]
            return httpx.Response(200, json={"value": candidates})
        if request.url.path == WORKFLOW + "/runs/" + RUN:
            now = datetime.now(timezone.utc).isoformat()
            value = {"id": WORKFLOW + "/runs/" + RUN, "properties": {
                "status": self.state, "startTime": self.started, "endTime": now,
                "workflow": {"id": WORKFLOW + "/versions/" + VERSION},
                "correlation": {"clientTrackingId": self.payload["confirmation_id"]},
                "outputs": {"outlook_decision": {"value": {
                    "request": self.payload, "observed_at": now,
                    "response": {"SelectedOption": "Approve", "UserEmailAddress": EMAIL,
                                 "UserTenantId": TENANT, "UserId": self.recipient_user},
                    "control_plane_grant_created": False, "business_effect_executed": False}}}}}
            self.mutation(value)
            return httpx.Response(200, json=value)
        raise AssertionError("unexpected native confirmation ARM request")

    async def close(self):
        await self.http.aclose()
        await self.old_http.aclose()


pytestmark = pytest.mark.governance_runtime


@pytest.mark.parametrize("approve", [True, False])
def test_native_requester_mail_buttons_without_cli_and_once_after_restart(tmp_path, approve):
    async def run():
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            pending = await call(h)
            assert pending["status"] == "pending_confirmation", pending
            assert n.posts == 1 and h.calls == []
            assert n.payload["proposed_arguments"] == {"amount": 5}
            assert n.payload["confirmation_id"] == pending["confirmation_id"]
            assert n.payload["status"] == "pending_confirmation"
            assert set(n.payload) == {"status", "confirmation_id", "operation_id", "action_hash",
                "intent_digest", "expires_at", "confirmation_intent", "review_context", "proposed_arguments"}
            assert n.payload["confirmation_intent"]["context_ref"] == h.context_ref
            assert "confirmation_url" not in n.payload
            assert not any(k.startswith("approval:") for _, k in h.cp.store.docs)
            assert await call(h, dispatcher=new_dispatcher(h)) == pending
            denied_cli = await h.cp.client.post("/confirmation/" + pending["confirmation_id"],
                headers=user_headers(h.cp), json={"approved": True, "intent_digest": n.payload["intent_digest"]})
            assert denied_cli.status_code in (401, 403, 409)
            n.state = "Succeeded"
            if not approve:
                n.mutation = lambda v: v["properties"]["outputs"]["outlook_decision"]["value"][
                    "response"].update(SelectedOption="Reject")
            result = await call(h, dispatcher=new_dispatcher(h))
            assert result["status"] == ("completed" if approve else "blocked"), result
            assert len(h.calls) == int(approve)
            assert await call(h, dispatcher=new_dispatcher(h)) == result
            assert n.posts == 1 and len(h.calls) == int(approve)
            record, _ = await h.cp.confirmation_store.read(TENANT, "confirmation:" + pending["confirmation_id"])
            assert record["authority"]["kind"] == "outlook-native"
            assert record["authority"]["home_subject"] == USER
            assert "grant" not in record
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("same_human", [True, False])
def test_native_requester_consent_is_not_independent_reviewer_authority(tmp_path, same_human):
    async def run():
        user = HUMAN if same_human else USER
        h = await harness(tmp_path, review=True, user=user)
        n = await NativeConfirmationHarness().initialize(h.cp, user=user)
        try:
            pending = await call(h)
            assert pending["status"] == "pending_confirmation"
            assert not any(k.startswith("approval:") for _, k in h.cp.store.docs)
            n.state = "Succeeded"
            review = await call(h)
            assert review["status"] == "pending_approval", review
            assert h.calls == []
            assert (await h.cp.post("decide", human=True, intent=review["approval_intent"],
                                   approved=True, approving_role="Approver")).status_code == 200
            result = await call(h)
            assert (result["status"] == "completed") is not same_human, result
            assert len(h.calls) == int(not same_human)
            assert n.posts == 1
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


def test_native_requester_mail_two_concurrent_resumes_one_effect(tmp_path):
    async def run():
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            first = await call(h)
            assert first["status"] == "pending_confirmation"
            n.state = "Succeeded"
            # First resolve creates authority without consuming it, to exercise
            # the effect reservation CAS across two independent gateway workers.
            record, _ = await h.cp.confirmation_store.read(TENANT, "confirmation:" + first["confirmation_id"])
            result = await h.cp.client.post("/confirmation/resolve", headers=h.cp.headers(), json={
                "operation": "resolve", "intent": record["intent"]})
            assert result.json()["status"] == "confirmed", result.text
            results = await asyncio.gather(call(h, dispatcher=new_dispatcher(h)),
                                           call(h, dispatcher=new_dispatcher(h)))
            assert any(r["status"] == "completed" for r in results), results
            assert len(h.calls) == 1 and n.posts == 1
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["deny", "pin", "recipient", "credential-expiry", "wire-facts", "outcome"])
def test_native_requester_mail_freshness_and_policy_guards(tmp_path, monkeypatch, failure):
    async def run():
        h = await harness(tmp_path, decision={"decision": "deny"} if failure == "deny" else None)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            if failure in ("pin", "recipient"):
                if failure == "pin":
                    n.workflow["properties"]["version"] = "changed"
                else:
                    n.workflow["properties"]["definition"]["actions"]["Require_fresh_pending"][
                        "actions"]["Send_approval_email"]["inputs"]["body"]["Message"]["To"] = "other@example.com"
                # Even a trusted pin cannot hide an incorrect fixed recipient.
                if failure == "recipient":
                    profile = h.cp.service.confirmation.profile("email-basic")
                    updated = profile.model_copy(update={"outlook": profile.outlook.model_copy(update={
                        "workflow_digest": module("outlook").workflow_digest(n.workflow)})})
                    h.cp.service.confirmation.config.profiles["email-basic"] = updated
            if failure == "credential-expiry":
                from test_confirmation_integration_fixes import Clock
                clock = Clock()
                clock.install(monkeypatch, module("confirmation"), module("confirmation_outlook"), module("outlook"))
                async def waited(request):
                    if request.method == "POST":
                        clock.advance(3600)
                n.http.event_hooks["request"].append(waited)
            pending = await call(h)
            if failure in ("deny", "pin", "recipient", "credential-expiry"):
                assert pending["status"] != "completed", pending
                assert n.posts == 0 and not h.calls
                return
            assert pending["status"] == "pending_confirmation", pending
            n.state = "Succeeded"
            if failure == "wire-facts":
                async def waited():
                    h.dispatcher.safe_provider = lambda f: {"scope": f["scope"], "changed": True}
                h.credential.hook = waited
            else:
                h.downstream_status = 503
            result = await call(h)
            assert result["status"] != "completed", result
            assert len(h.calls) == int(failure == "outcome")
            replay = await call(h)
            assert replay["reason_code"] == "outcome_unknown", replay
            assert n.posts == 1 and len(h.calls) == int(failure == "outcome")
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


def test_native_requester_mail_does_not_upgrade_prior_notification_intent(tmp_path):
    async def run():
        h = await harness(tmp_path)
        pending = await call(h)
        assert pending["status"] == "pending_confirmation"
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            result = await call(h)
            assert result["status"] != "completed"
            assert n.posts == 0 and h.calls == []
            record, _ = await h.cp.confirmation_store.read(TENANT, "confirmation:" + pending["confirmation_id"])
            assert record["state"] == "pending" and record["authority"] is None and "outlook" not in record
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


def test_native_requester_mail_actual_mcp_returns_pending_then_same_operation_result(tmp_path):
    async def run():
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        app = gateway("server").create_app(h.dispatcher)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url="https://gateway.example") as client:
                    headers = {"Accept": "application/json, text/event-stream",
                        "Authorization": "Bearer " + h.cp.token(), "Idempotency-Key": "one"}
                    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "refund", "arguments": {"amount": 5},
                        "_meta": {"governance_request_context": h.context_ref}}}
                    first = (await client.post("/mcp", headers=headers, json=body)).json()["result"]
                    assert first["isError"] is False and first["structuredContent"]["status"] == "pending_confirmation"
                    n.state = "Succeeded"
                    final = (await client.post("/mcp", headers=headers, json=body)).json()["result"]
                    assert final["structuredContent"]["status"] == "completed", final
                    assert n.posts == 1 and len(h.calls) == 1
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


def test_native_requester_mail_and_signed_evidence_share_exact_proposal(tmp_path):
    async def run():
        from test_evidence_attestations import fixture
        from test_gateway import registry
        _, _, requirement, identity, args, _, _, provider, _ = fixture()
        document = registry()
        action = document["actions"][0]
        action["input_schema"]["properties"].update(case_id={"type": "string", "maxLength": 128},
            expected_etag={"type": "string", "maxLength": 128})
        action["input_schema"]["properties"]["amount"]["maximum"] = 2000
        action["input_schema"]["required"] += ["case_id", "expected_etag"]
        action["evidence_requirement"] = requirement.model_dump()
        h = await harness(tmp_path, document=document,
            decision='{"decision":"allow"} if input.snapshot.safe.evidence.claims.purchase_verified == true else := {"decision":"deny"}')
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            assert (await call(h, arguments=args))["reason_code"] == "evidence_required"
            assert n.posts == 0
            token = (await provider.issue(identity, args))["attestation"]
            pending = await call(h, arguments=args, evidence_token=token)
            assert pending["status"] == "pending_confirmation", pending
            assert n.payload["proposed_arguments"] == args
            assert token not in json.dumps(n.payload)
            assert n.payload["review_context"]["evidence_fingerprint"].startswith("sha256:")
            n.state = "Succeeded"
            renewed = (await provider.issue(identity, args))["attestation"]
            result = await call(h, arguments=args, evidence_token=renewed)
            assert result["status"] == "completed", result
            assert n.posts == 1 and len(h.calls) == 1
            assert token not in json.dumps(list(h.store.docs.values()))
            assert await call(h, arguments=args) == result
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


def test_native_requester_mail_generic_subject_requires_explicit_actual_home_mapping(tmp_path):
    async def run():
        from test_confirmation_integration_fixes import signed_profile
        from test_user_confirmation import register_context
        from datetime import datetime
        import jwt
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            c = module("confirmation")
            profile = h.cp.service.confirmation.profile("email-basic")
            adapter = signed_profile(h.cp)["result_verifier"]
            binding = c.UserBinding(issuer=adapter["issuer"], subject="customer:alice",
                client=adapter["client_id"], delivery_ref="requester",
                requester_home_tenant=TENANT, requester_home_subject=USER)
            profile = profile.model_copy(update={"customer_identity": c.CustomerIdentityConfiguration(**adapter),
                                                "users": [binding]})
            h.cp.service.confirmation.config.profiles["email-basic"] = profile
            now = int(datetime.now(timezone.utc).timestamp())
            token = jwt.encode({"iss": adapter["issuer"], "sub": "customer:alice", "aud": adapter["audience"],
                "azp": adapter["client_id"], "scp": adapter["scope"], "iat": now - 1, "nbf": now - 1,
                "exp": now + 240}, h.cp.key, algorithm="RS256", headers={"kid": adapter["key_id"]})
            from test_control_plane import APP
            body = {"provider_profile": "email-basic", "workload": WORKLOAD, "client": APP,
                "agent_id": "agent-1", "action": "refund", "operation_id": "generic",
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()}
            response = await h.cp.client.post("/confirmation/contexts",
                headers={"Authorization": "Bearer " + token}, json=body)
            assert response.status_code == 200, response.text
            h.context_ref = response.json()["context_ref"]
            pending = await call(h, key="generic")
            assert pending["status"] == "pending_confirmation", pending
            n.state = "Succeeded"
            result = await call(h, key="generic")
            assert result["status"] == "completed", result
            assert len(h.calls) == 1
            # The generic user cannot be matched to Outlook by email alone.
            h.cp.service.confirmation.config.profiles["email-basic"] = profile.model_copy(update={
                "users": [binding.model_copy(update={"requester_home_tenant": None, "requester_home_subject": None})]})
            rejected = await h.cp.client.post("/confirmation/contexts",
                headers={"Authorization": "Bearer " + token}, json={**body, "operation_id": "unmapped"})
            assert rejected.status_code == 401
            assert n.posts == 1
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


def test_native_requester_email_does_not_offer_cli_confirmation_on_public_landing(tmp_path):
    async def run():
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            pending = await call(h)
            before, _ = await h.cp.confirmation_store.read(TENANT, "confirmation:" + pending["confirmation_id"])
            landing = await h.cp.client.get("/confirmation/open/" + pending["confirmation_id"])
            assert landing.status_code == 200
            assert "Approve" in landing.text and "Reject" in landing.text
            assert "python" not in landing.text and "confirmation_user" not in landing.text
            after, _ = await h.cp.confirmation_store.read(TENANT, "confirmation:" + pending["confirmation_id"])
            assert after == before and n.posts == 1 and not h.calls
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


@pytest.mark.governance_runtime
def test_native_requester_documentation_describes_buttons_not_default_cli():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "references"
    for name in ("control-plane", "gateway"):
        text = (root / name / "README.md").read_text()
        assert "outlook-native" in text and "inside the email" in text
        assert "CLI is only a developer diagnostic" in text


@pytest.mark.parametrize("fault", ["subject", "tenant", "email", "payload", "nonce", "workflow", "expired", "option"])
def test_native_requester_mail_rejects_unbound_or_wrong_responder(tmp_path, fault):
    async def run():
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            pending = await call(h)
            assert pending["status"] == "pending_confirmation", pending
            n.state = "Succeeded"
            def change(v):
                p = v["properties"]
                value = p["outputs"]["outlook_decision"]["value"]
                response = value["response"]
                if fault == "subject":
                    response["UserId"] = HUMAN
                elif fault == "tenant":
                    response["UserTenantId"] = HUMAN
                elif fault == "email":
                    response["UserEmailAddress"] = "other@example.com"
                elif fault == "payload":
                    value["request"] = {**value["request"], "proposed_arguments": {"amount": 999}}
                elif fault == "nonce":
                    p["correlation"]["clientTrackingId"] = "a" * 32
                elif fault == "workflow":
                    p["workflow"]["id"] = WORKFLOW + "/versions/1"
                elif fault == "option":
                    response["SelectedOption"] = "Maybe"
                else:
                    p["endTime"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            n.mutation = change
            result = await call(h)
            assert result["status"] != "completed", result
            assert h.calls == [] and n.posts == 1
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())


@pytest.mark.parametrize("recoverable", [True, False])
def test_native_requester_mail_ambiguous_send_recovers_witness_never_resends(tmp_path, recoverable):
    async def run():
        h = await harness(tmp_path)
        n = await NativeConfirmationHarness().initialize(h.cp)
        try:
            n.fail_after_send = True
            first = await call(h)
            assert first["status"] == "unavailable", first
            assert n.posts == 1 and not h.calls
            n.state = "Succeeded"
            if not recoverable:
                n.recovery_candidates = []
            result = await call(h, dispatcher=new_dispatcher(h))
            assert (result["status"] == "completed") is recoverable, result
            assert len(h.calls) == int(recoverable) and n.posts == 1
        finally:
            await n.close()
            await h.close()
    asyncio.run(run())
