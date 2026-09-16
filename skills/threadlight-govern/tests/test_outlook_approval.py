"""Offline native-channel tests: real control-plane models, RSA policy and CAS."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from test_control_plane import APP, DIGEST, HUMAN, TENANT, WORKLOAD, Harness, module, run

HOME_TENANT = "77777777-7777-7777-7777-777777777777"
HOME_USER = "88888888-8888-8888-8888-888888888888"
SENDER = "99999999-9999-9999-9999-999999999999"
WORKFLOW = f"/subscriptions/{TENANT}/resourceGroups/rg-test/providers/Microsoft.Logic/workflows/review"
VERSION = "08584121459363052877"
RUN = "08584121458741569751074360916CU12"
URL = "https://prod-1.region.logic.azure.com/workflows/abc/triggers/Review_notification_requested/paths/invoke?api-version=2016-10-01"
EMAIL = "reviewer@example.com"


def test_native_outlook_default_decision_labels_are_english():
    native = module("outlook")
    assert native.OutlookConfiguration.model_fields["approved_option"].default == "Approve"
    assert native.OutlookConfiguration.model_fields["rejected_option"].default == "Reject"


def digest(value):
    return "sha256:" + hashlib.sha256(module("models").canonical(value)).hexdigest()


class NativeHarness:
    async def initialize(self, *, control=None, actions=("returns_apply_decision",),
                         approved_option="Approve", rejected_option="Reject"):
        self.approved_option = approved_option
        self.h = control if control is not None else await Harness().initialize()
        self.review = {
            "operation_id": "d" * 32,
            "review_context": {
                "tenant": TENANT, "subject": WORKLOAD, "client": APP,
                "action": "returns_apply_decision", "scope": "returns", "policy": DIGEST,
                "deployment": {
                    "agent_id": "agent-1", "agent_version": "4", "image_digest": DIGEST,
                    "environment": "preproduction", "subscription": TENANT, "resource_group": "rg-test",
                },
            },
            "proposed_arguments": {
                "case_id": "RMA-1", "decision": "escalate_to_supervisor",
                "expected_etag": "revision-1", "reason": "Synthetic supervisor review",
            },
        }
        self.h.wire_intent.update(
            action_hash=digest({"facts": self.review["review_context"],
                                "arguments": self.review["proposed_arguments"]}),
            context_identity=digest(self.review["review_context"]),
            session_id=digest(self.review["operation_id"])[7:],
        )
        self.workflow = {
            "id": WORKFLOW,
            "properties": {
                "version": VERSION, "state": "Enabled",
                "accessEndpoint": "https://prod-1.region.logic.azure.com/workflows/abc",
                "accessControl": {"triggers": {
                    "sasAuthenticationPolicy": {"state": "Disabled"},
                    "openAuthenticationPolicies": {"policies": {"control": {"type": "AAD", "claims": [
                        {"name": "iss", "value": f"https://sts.windows.net/{TENANT}/"},
                        {"name": "aud", "value": "https://management.azure.com/"},
                        {"name": "oid", "value": SENDER},
                    ]}}},
                }},
                "parameters": {},
                "definition": {
                    "actions": {"Require_fresh_pending": {"actions": {"Send_approval_email": {
                        "type": "ApiConnectionWebhook", "inputs": {
                            "path": "/approvalmail/$subscriptions",
                            "body": {"Message": {"To": EMAIL, "Options": f"{approved_option},{rejected_option}",
                                                "ShowHTMLConfirmationDialog": True}},
                        },
                    }}}},
                    "outputs": {"outlook_decision": {"value": {
                        "response": "@body('Send_approval_email')", "request": "@triggerBody()",
                        "control_plane_grant_created": False, "business_effect_executed": False,
                    }}},
                },
            },
        }
        self.posts = 0
        self.state = "Running"
        self.fail_after_send = False
        self.recovery_candidates = None
        self.mutation = lambda value: None
        self.credential = SimpleNamespace(get_token=self.token)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.transport))
        native = module("outlook")
        config = native.OutlookConfiguration.model_validate_json(json.dumps({
            "workflow_resource_id": WORKFLOW, "workflow_version": VERSION,
            "workflow_digest": native.workflow_digest(self.workflow),
            "trigger_url": URL, "sender_principal": SENDER, "recipient": EMAIL,
            "requesters": [WORKLOAD],
            "actions": list(actions),
            "approved_option": approved_option, "rejected_option": rejected_option,
            "responders": [{"home_tenant": HOME_TENANT, "home_subject": HOME_USER,
                            "approver": HUMAN, "role": "Approver"}],
        }))
        self.h.service.outlook = native.NativeOutlook(config, self.credential, self.http)
        return self

    async def token(self, scope):
        assert scope == "https://management.azure.com//.default"
        return SimpleNamespace(token="offline-arm-token", expires_on=10**12)

    async def transport(self, request):
        assert request.headers["authorization"] == "Bearer offline-arm-token"
        if request.method == "POST":
            assert str(request.url) == URL
            self.posts += 1
            self.payload = json.loads(request.content)
            assert request.headers["x-ms-client-tracking-id"] == self.payload["approval_intent"]["nonce"]
            self.started = datetime.now(timezone.utc).isoformat()
            if self.fail_after_send:
                self.fail_after_send = False
                raise httpx.ReadTimeout("offline lost acknowledgement", request=request)
            return httpx.Response(202, headers={"x-ms-workflow-run-id": RUN})
        if request.url.path == WORKFLOW:
            return httpx.Response(200, json=self.workflow)
        if request.url.path == WORKFLOW + "/runs":
            values = self.recovery_candidates
            if values is None:
                values = [{"name": RUN, "id": WORKFLOW + "/runs/" + RUN, "properties": {
                    "correlation": {"clientTrackingId": self.payload["approval_intent"]["nonce"]}}}]
            return httpx.Response(200, json={"value": values})
        if request.url.path == WORKFLOW + "/runs/" + RUN:
            now = datetime.now(timezone.utc).isoformat()
            value = {
                "id": WORKFLOW + "/runs/" + RUN,
                "properties": {
                    "status": self.state, "startTime": self.started, "endTime": now,
                    "workflow": {"id": WORKFLOW + "/versions/" + VERSION},
                    "correlation": {"clientTrackingId": self.payload["approval_intent"]["nonce"]},
                    "outputs": {"outlook_decision": {"value": {
                        "request": self.payload, "observed_at": now,
                        "response": {"SelectedOption": self.approved_option, "UserEmailAddress": EMAIL,
                                     "UserTenantId": HOME_TENANT, "UserId": HOME_USER},
                        "control_plane_grant_created": False, "business_effect_executed": False,
                    }}},
                },
            }
            self.mutation(value)
            return httpx.Response(200, json=value)
        raise AssertionError(f"Unexpected native ARM path: {request.method} {request.url.path}")

    async def request(self, **changes):
        return await self.h.post("request", intent=self.h.wire_intent,
                                 review={**deepcopy(self.review), **changes})

    async def close(self):
        await self.http.aclose()
        await self.h.close()


def test_native_outlook_choice_creates_one_bound_grant_and_preserves_provenance_on_consume():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request()).status_code == 202
            assert (await n.request()).status_code == 202
            assert n.posts == 1
            n.state = "Succeeded"
            response = await n.request()
            assert response.status_code == 200, response.text
            grant = response.json()["grant"]
            assert grant["approved"] and grant["approver"] == HUMAN
            assert grant["approver_tenant"] == TENANT
            assert grant["intent"] == n.h.wire_intent
            result = await n.h.post("consume", intent=n.h.wire_intent, grant=grant)
            assert result.status_code == 200
            record, _ = await n.h.store.read(TENANT, "approval:" + n.h.wire_intent["nonce"])
            assert record["authority"]["kind"] == "outlook-native/v1"
            assert record["authority"]["home_subject"] == HOME_USER
            assert record["authority"]["home_tenant"] == HOME_TENANT
            assert record["authority"]["run_id"] == RUN
            assert record["state"] == "consumed"
            assert (await n.h.post("consume", intent=n.h.wire_intent, grant=grant)).status_code == 409
            assert n.posts == 1
        finally:
            await n.close()
    run(scenario())


@pytest.mark.parametrize("fault", ["request", "nonce", "version", "subject", "tenant", "email", "option"])
def test_native_outlook_rejects_unbound_or_unidentified_choices(fault):
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request()).status_code == 202
            n.state = "Succeeded"
            def mutate(value):
                properties = value["properties"]
                decision = properties["outputs"]["outlook_decision"]["value"]
                if fault == "request":
                    decision["request"] = {**decision["request"], "operation_id": "e" * 32}
                elif fault == "nonce":
                    properties["correlation"]["clientTrackingId"] = "e" * 32
                elif fault == "version":
                    properties["workflow"]["id"] += "-other"
                else:
                    field = {"subject": "UserId", "tenant": "UserTenantId",
                             "email": "UserEmailAddress", "option": "SelectedOption"}[fault]
                    decision["response"][field] = ""
            n.mutation = mutate
            assert (await n.request()).status_code in (403, 409)
            record, _ = await n.h.store.read(TENANT, "approval:" + n.h.wire_intent["nonce"])
            assert record["state"] == "pending" and record["grant"] is None
        finally:
            await n.close()
    run(scenario())


def test_native_channel_cannot_be_bypassed_by_a_delegated_decide_call():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request()).status_code == 202
            response = await n.h.post("decide", human=True, intent=n.h.wire_intent,
                                     approved=True, approving_role="Approver")
            assert response.status_code == 403
            assert n.posts == 1
        finally:
            await n.close()
    run(scenario())


def test_native_channel_rejects_changed_review_before_notification():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request(operation_id="e" * 32)).status_code in (409, 422)
            assert n.posts == 0
            assert not n.h.store.docs
        finally:
            await n.close()
    run(scenario())


def test_native_context_health_advertises_required_review_metadata_without_sending_mail():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            context = {key: n.h.wire_intent[key] for key in (
                "principal", "agent_id", "tenant", "allowed_roles")}
            result = await n.h.client.get("/health", headers=n.h.headers(),
                                          params={"approval_context": json.dumps(context)})
            assert result.status_code == 200
            assert result.json()["approval_review_required"] is True
            assert n.posts == 0 and not n.h.store.docs
        finally:
            await n.close()
    run(scenario())


def test_consume_cannot_poll_or_decide_a_still_pending_native_request():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request()).status_code == 202
            invalid = {"intent": n.h.wire_intent, "approved": True, "approver": HUMAN,
                       "approver_tenant": TENANT, "approver_role": "Approver", "provenance": "e" * 32}
            response = await n.h.post("consume", intent=n.h.wire_intent, grant=invalid)
            assert response.status_code == 409
        finally:
            await n.close()
    run(scenario())


def test_lost_notification_ack_is_reconciled_without_sending_a_second_email():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            n.fail_after_send = True
            assert (await n.request()).status_code == 503
            assert n.posts == 1
            assert (await n.request()).status_code == 202
            assert n.posts == 1
            n.state = "Succeeded"
            assert (await n.request()).status_code == 200
            assert n.posts == 1
        finally:
            await n.close()
    run(scenario())


def test_unresolved_notification_outcome_does_not_retry_or_grant():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            n.fail_after_send = True
            assert (await n.request()).status_code == 503
            n.recovery_candidates = []
            assert (await n.request()).status_code == 503
            assert n.posts == 1
            record, _ = await n.h.store.read(TENANT, "approval:" + n.h.wire_intent["nonce"])
            assert record["state"] == "pending" and record["grant"] is None
        finally:
            await n.close()
    run(scenario())


def test_native_rejection_is_a_real_negative_one_use_grant():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request()).status_code == 202
            n.state = "Succeeded"
            n.mutation = lambda value: value["properties"]["outputs"]["outlook_decision"]["value"][
                "response"].update(SelectedOption="Reject")
            response = await n.request()
            assert response.status_code == 200
            grant = response.json()["grant"]
            assert grant["approved"] is False
            assert (await n.h.post("consume", intent=n.h.wire_intent, grant=grant)).status_code == 200
            assert (await n.h.post("consume", intent=n.h.wire_intent, grant=grant)).status_code == 409
        finally:
            await n.close()
    run(scenario())


def test_native_channel_disable_blocks_consumption_of_an_existing_grant():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            assert (await n.request()).status_code == 202
            n.state = "Succeeded"
            grant = (await n.request()).json()["grant"]
            n.workflow["properties"]["state"] = "Disabled"
            assert (await n.h.post("consume", intent=n.h.wire_intent, grant=grant)).status_code == 409
            record, _ = await n.h.store.read(TENANT, "approval:" + n.h.wire_intent["nonce"])
            assert record["state"] == "decided"
        finally:
            await n.close()
    run(scenario())


def test_outlook_dependency_failure_does_not_disable_unrelated_control_plane_services():
    async def scenario():
        n = await NativeHarness().initialize()
        try:
            n.workflow["properties"]["state"] = "Disabled"
            assert (await n.h.client.get("/health")).status_code == 200
            context = {key: n.h.wire_intent[key] for key in (
                "principal", "agent_id", "tenant", "allowed_roles")}
            result = await n.h.client.get("/health", headers=n.h.headers(),
                                          params={"approval_context": json.dumps(context)})
            assert result.status_code == 503
            assert n.posts == 0
        finally:
            await n.close()
    run(scenario())


def test_explicit_legacy_locale_is_not_reinterpreted_as_the_english_default():
    async def scenario():
        n = await NativeHarness().initialize(approved_option="Approva", rejected_option="Rifiuta")
        try:
            assert (await n.request()).status_code == 202
            n.state = "Succeeded"
            result = await n.request()
            assert result.status_code == 200 and result.json()["grant"]["approved"] is True
        finally:
            await n.close()
    run(scenario())
