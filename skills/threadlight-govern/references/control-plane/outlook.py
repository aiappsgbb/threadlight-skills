"""Native Outlook witness authority, independently verified through authenticated ARM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from typing import Annotated
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx
from pydantic import Field, model_validator

from .models import (
    ApprovalGrant, Digest, Identifier, ObjectId, ProbeDeployment, ReviewMetadata,
    StrictModel, Timestamp, canonical, parse, strict_json,
)
from .review import validate_pending_review
from .storage import Conflict

ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com//.default"
API_VERSION = "2019-05-01"
Email = Annotated[str, Field(
    min_length=3, max_length=254, pattern=r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$")]


class OutlookUnavailable(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class OutlookResponder(StrictModel):
    home_tenant: ObjectId
    home_subject: ObjectId
    approver: ObjectId
    role: Identifier


class OutlookWorkflow(StrictModel):
    workflow_resource_id: Annotated[str, Field(
        max_length=1024,
        pattern=r"^/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft\.Logic/workflows/[A-Za-z0-9_-]+$")]
    workflow_version: Annotated[str, Field(pattern=r"^[0-9]{1,64}$")]
    workflow_digest: Digest
    sender_principal: ObjectId
    recipient: Email
    approved_option: Identifier = "Approve"
    rejected_option: Identifier = "Reject"


class OutlookConfiguration(OutlookWorkflow):
    trigger_url: Annotated[str, Field(max_length=2048)]
    requesters: Annotated[tuple[ObjectId, ...], Field(min_length=1, max_length=64)]
    responders: Annotated[tuple[OutlookResponder, ...], Field(min_length=1, max_length=32)]
    actions: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)] = (
        "returns_apply_decision",)

    @model_validator(mode="after")
    def restricted(self):
        url = urlsplit(self.trigger_url)
        if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".logic.azure.com")
                or url.username or url.password or url.fragment or url.port not in (None, 443)
                or parse_qs(url.query) != {"api-version": ["2016-10-01"]}
                or not url.path.endswith("/triggers/Review_notification_requested/paths/invoke")
                or self.approved_option == self.rejected_option
                or len(set(self.requesters)) != len(self.requesters)
                or len({(r.home_tenant, r.home_subject) for r in self.responders}) != len(self.responders)
                or any(r.approver in self.requesters or r.approver == self.sender_principal
                       for r in self.responders)):
            raise ValueError("invalid_outlook_configuration")
        return self


class NativeResponse(StrictModel):
    SelectedOption: Identifier
    UserEmailAddress: Email
    UserTenantId: ObjectId
    UserId: ObjectId


def workflow_digest(workflow):
    properties = workflow["properties"]
    contract = {key: properties[key] for key in ("definition", "parameters", "accessControl")}
    return "sha256:" + hashlib.sha256(canonical(contract)).hexdigest()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


class OutlookWitness:
    """Shared native connector/ARM witness; never creates reviewer or user grants."""
    def __init__(self, configuration: OutlookWorkflow, credential, http: httpx.AsyncClient, *, trigger_url=None):
        self.config, self.credential, self.http = configuration, credential, http
        self.trigger_url = trigger_url if trigger_url is not None else configuration.trigger_url

    async def request(self, method, url, *, guard, payload=None, tracking=None):
        async def check():
            result = guard()
            if inspect.isawaitable(result):
                await result
        await check()
        token = await self.credential.get_token(ARM_SCOPE)
        async def authorized():
            await check()
            if token.expires_on <= datetime.now(timezone.utc).timestamp():
                raise OutlookUnavailable("outlook_credential_expired")
        async def trace(event, info):
            if event in ("http11.send_request_headers.started", "http11.send_request_body.started"):
                await authorized()
        await authorized()
        headers = {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}
        if tracking is not None:
            headers["x-ms-client-tracking-id"] = tracking
        try:
            async with self.http.stream(
                    method, url, headers=headers, content=canonical(payload) if payload is not None else None,
                    follow_redirects=False, extensions={"trace": trace}) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise OutlookUnavailable("outlook_response_too_large")
                await authorized()
                if response.status_code not in (200, 202):
                    raise OutlookUnavailable("outlook_remote_unavailable")
                return response.status_code, dict(response.headers), strict_json(bytes(raw)) if raw else None
        except httpx.HTTPError:
            raise OutlookUnavailable("outlook_transport_unavailable") from None

    async def arm(self, suffix, *, guard):
        status, _, document = await self.request(
            "GET", ARM + self.config.workflow_resource_id + suffix, guard=guard)
        if status != 200 or not isinstance(document, dict):
            raise OutlookUnavailable("outlook_arm_response_invalid")
        return document

    async def verify_workflow(self, control, *, guard, trigger_name, strict_access=False):
        workflow = await self.arm("?api-version=" + API_VERSION, guard=guard)
        properties = workflow["properties"]
        if (workflow["id"].lower() != self.config.workflow_resource_id.lower()
                or properties["state"] != "Enabled" or properties["version"] != self.config.workflow_version
                or workflow_digest(workflow) != self.config.workflow_digest):
            raise Conflict()
        actual = urlsplit(properties["accessEndpoint"])
        expected = urlsplit(self.trigger_url)
        if (actual.scheme != "https" or actual.hostname != expected.hostname
                or actual.port not in (None, 443)
                or expected.path != actual.path.rstrip("/") + f"/triggers/{trigger_name}/paths/invoke"):
            raise Conflict()
        access = properties["accessControl"]["triggers"]
        policies = access["openAuthenticationPolicies"]["policies"]
        required_claims = {
            "iss": f"https://sts.windows.net/{control.settings.tenant_id}/",
            "aud": "https://management.azure.com/", "oid": self.config.sender_principal,
        }
        if (access["sasAuthenticationPolicy"]["state"] != "Disabled"
                or not any(p["type"] == "AAD" and {c["name"]: c["value"] for c in p["claims"]} == required_claims
                           for p in policies.values())
                or strict_access and (len(policies) != 1
                    or any(len(p["claims"]) != len(required_claims) for p in policies.values()))):
            raise Conflict()
        definition = properties["definition"]
        gate = definition["actions"]["Require_fresh_pending"]
        email = gate["actions"]["Send_approval_email"]
        message = email["inputs"]["body"]["Message"]
        output = definition["outputs"]["outlook_decision"]["value"]
        if (set(definition["actions"]) != {"Require_fresh_pending"}
                or set(gate["actions"]) != {"Send_approval_email"}
                or email["type"] != "ApiConnectionWebhook"
                or email["inputs"]["path"] != "/approvalmail/$subscriptions"
                or message["To"].casefold() != self.config.recipient.casefold()
                or message["Options"].split(",") != [self.config.approved_option, self.config.rejected_option]
                or message["ShowHTMLConfirmationDialog"] is not True
                or output["response"] != "@body('Send_approval_email')" or output["request"] != "@triggerBody()"
                or output["control_plane_grant_created"] is not False
                or output["business_effect_executed"] is not False):
            raise Conflict()
        if strict_access and (
                set(definition.get("triggers", {})) != {trigger_name}
                or definition["triggers"][trigger_name].get("type") != "Request"):
            raise Conflict()

    async def recover_run(self, tracking, *, guard):
        document = await self.arm("/runs?api-version=" + API_VERSION + "&$top=100", guard=guard)
        rows = document.get("value")
        if (not isinstance(rows, list) or len(rows) > 100
                or document.get("nextLink") or document.get("@odata.nextLink")):
            raise OutlookUnavailable("outlook_recovery_requires_operator")
        candidates = [r for r in rows
                      if r.get("properties", {}).get("correlation", {}).get("clientTrackingId") == tracking]
        if len(candidates) != 1:
            raise OutlookUnavailable("outlook_notification_outcome_unknown")
        candidate = candidates[0]
        run_id = parse(Identifier, canonical(candidate["name"]))
        if candidate["id"].lower() != (self.config.workflow_resource_id + "/runs/" + run_id).lower():
            raise Conflict()
        return run_id

    async def response(self, control, *, run_id, tracking, payload, created_at, expires_at, guard):
        run = await self.arm("/runs/" + run_id + "?api-version=" + API_VERSION, guard=guard)
        properties = run["properties"]
        if (run["id"].lower() != (self.config.workflow_resource_id + "/runs/" + run_id).lower()
                or properties["workflow"]["id"].lower() != (
                    self.config.workflow_resource_id + "/versions/" + self.config.workflow_version).lower()
                or properties["correlation"]["clientTrackingId"] != tracking):
            raise Conflict()
        if properties["status"] in ("Running", "Waiting"):
            return None
        if properties["status"] != "Succeeded":
            raise OutlookUnavailable("outlook_review_not_completed")
        value = properties["outputs"]["outlook_decision"]["value"]
        if (value["request"] != payload or value["control_plane_grant_created"] is not False
                or value["business_effect_executed"] is not False):
            raise Conflict()
        try:
            response = parse(NativeResponse, canonical(value["response"]))
            decision_at = parse(Timestamp, canonical(value["observed_at"]))
            started = parse(Timestamp, canonical(properties["startTime"]))
            ended = parse(Timestamp, canonical(properties["endTime"]))
            created = parse(Timestamp, canonical(created_at))
        except ValueError:
            raise Conflict() from None
        if (response.UserEmailAddress.casefold() != self.config.recipient.casefold()
                or response.SelectedOption not in (self.config.approved_option, self.config.rejected_option)
                or not created - timedelta(seconds=5) <= started <= decision_at <= ended
                or ended - decision_at > timedelta(seconds=5)
                or ended > control.now() + timedelta(seconds=5) or ended >= expires_at):
            raise Conflict()
        return response, decision_at, digest(value)


class NativeOutlook(OutlookWitness):
    def selected(self, intent):
        return intent.principal in self.config.requesters

    def authorize_configuration(self, control):
        settings = control.settings
        if (not set(self.config.requesters).issubset(settings.workloads)
                or any(r.approver not in settings.approver_subjects
                       or r.role not in settings.approver_roles
                       or r.home_subject in settings.workloads for r in self.config.responders)):
            raise Conflict()

    def payload(self, control, intent, review: ReviewMetadata):
        self.authorize_configuration(control)
        document = {
            "status": "pending_approval", "approval_intent": intent.model_dump(mode="json"),
            **review.model_dump(mode="json"),
        }
        validate_pending_review(document)
        facts = review.review_context
        if set(facts) != {"tenant", "subject", "client", "action", "scope", "policy", "deployment"}:
            raise Conflict()
        deployment = parse(ProbeDeployment, canonical(facts["deployment"]))
        subject = control.settings.workloads.get(facts["subject"])
        if (digest(review.operation_id)[7:] != intent.session_id or subject is None
                or subject.client_id != facts["client"] or subject.agent_id != intent.agent_id
                or deployment.agent_id != intent.agent_id or facts["action"] not in self.config.actions
                or not isinstance(review.proposed_arguments.get("case_id"), str)):
            raise Conflict()
        return {
            "operation_id": review.operation_id, "action_hash": intent.action_hash,
            "case_id": review.proposed_arguments["case_id"],
            "proposed_arguments": review.proposed_arguments,
            "expires_at": intent.expires_at.isoformat(),
            "approval_intent": intent.model_dump(mode="json"),
            "review_context": facts,
        }

    async def health(self, control, *, intent=None):
        self.authorize_configuration(control)
        guard = (lambda: control.fresh(intent)) if intent is not None else (lambda: None)
        await self.verify_workflow(control, guard=guard, trigger_name="Review_notification_requested")

    def initial(self, control, intent, review):
        if review is None:
            raise Conflict()
        return {
            "payload": self.payload(control, intent, review),
            "workflow_version": self.config.workflow_version,
            "workflow_digest": self.config.workflow_digest,
            "dispatch_state": "prepared", "run_id": None,
            "created_at": control.now().isoformat(),
        }

    def validate_record(self, control, intent, record, review=None):
        self.authorize_configuration(control)
        outlook = record.get("outlook")
        if (not self.selected(intent) or not isinstance(outlook, dict)
                or outlook["workflow_version"] != self.config.workflow_version
                or outlook["workflow_digest"] != self.config.workflow_digest):
            raise Conflict()
        payload = outlook["payload"]
        stored_review = parse(ReviewMetadata, canonical({
            key: payload[key] for key in ("operation_id", "review_context", "proposed_arguments")}))
        if (self.payload(control, intent, stored_review) != payload
                or review is not None and review != stored_review):
            raise Conflict()
        return outlook

    async def persist(self, control, intent, record, etag, outlook):
        replacement = {**record, "outlook": outlook}
        control.fresh(intent)
        await control.store.replace(intent.tenant, "approval:" + intent.nonce, replacement, etag)
        control.fresh(intent)
        actual, actual_etag = await control.store.read(intent.tenant, "approval:" + intent.nonce)
        control.fresh(intent)
        if actual != replacement:
            raise Conflict()
        return actual, actual_etag

    async def validate_authority(self, control, intent, record):
        outlook = self.validate_record(control, intent, record)
        authority, grant = record.get("authority", {}), record["grant"]
        if (authority.get("kind") != "outlook-native/v1"
                or authority.get("workflow_resource_id") != self.config.workflow_resource_id
                or authority.get("workflow_version") != self.config.workflow_version
                or authority.get("workflow_digest") != self.config.workflow_digest
                or authority.get("run_id") != outlook["run_id"]
                or not any(r.home_tenant == authority.get("home_tenant")
                           and r.home_subject == authority.get("home_subject")
                           and r.approver == authority.get("approver") == grant["approver"]
                           and r.role == authority.get("role") == grant["approver_role"]
                           for r in self.config.responders)):
            raise Conflict()
        await self.health(control, intent=intent)

    async def recover_dispatch(self, control, intent):
        return await self.recover_run(intent.nonce, guard=lambda: control.fresh(intent))

    async def resolve(self, control, intent, record, etag):
        outlook = self.validate_record(control, intent, record)
        await self.health(control, intent=intent)
        if outlook["dispatch_state"] == "prepared":
            outlook = {**outlook, "dispatch_state": "sending"}
            record, etag = await self.persist(control, intent, record, etag, outlook)
            status, headers, _ = await self.request(
                "POST", self.config.trigger_url, guard=lambda: control.fresh(intent),
                payload=outlook["payload"], tracking=intent.nonce)
            if status != 202 or not headers.get("x-ms-workflow-run-id"):
                raise OutlookUnavailable("outlook_notification_outcome_unknown")
            run_id = parse(Identifier, canonical(headers["x-ms-workflow-run-id"]))
            record, etag = await self.persist(control, intent, record, etag, {
                **outlook, "dispatch_state": "sent", "run_id": run_id})
            return 202, {"status": "pending"}
        if outlook["dispatch_state"] == "sending":
            run_id = await self.recover_dispatch(control, intent)
            record, etag = await self.persist(control, intent, record, etag, {
                **outlook, "dispatch_state": "sent", "run_id": run_id})
            outlook = record["outlook"]
        if outlook["dispatch_state"] != "sent":
            raise OutlookUnavailable("outlook_dispatch_state_invalid")
        run_id = parse(Identifier, canonical(outlook["run_id"]))
        observed = await self.response(control, run_id=run_id, tracking=intent.nonce,
            payload=outlook["payload"], created_at=outlook["created_at"], expires_at=intent.expires_at,
            guard=lambda: control.fresh(intent))
        if observed is None:
            return 202, {"status": "pending"}
        response, decision_at, response_digest = observed
        responder = next((r for r in self.config.responders
                          if (r.home_subject, r.home_tenant) == (response.UserId, response.UserTenantId)), None)
        if (responder is None or responder.role not in intent.allowed_roles
                or responder.approver == intent.principal):
            raise Conflict()
        await self.health(control, intent=intent)
        await control.intent_policy(intent)
        grant = ApprovalGrant(
            intent=intent, approved=response.SelectedOption == self.config.approved_option,
            approver=responder.approver, approver_tenant=intent.tenant,
            approver_role=responder.role, provenance=uuid.uuid4().hex,
        ).model_dump(mode="json")
        replacement = {
            **record, "state": "decided", "grant": grant,
            "authority": {
                "kind": "outlook-native/v1", "workflow_resource_id": self.config.workflow_resource_id,
                "workflow_version": self.config.workflow_version, "workflow_digest": self.config.workflow_digest,
                "run_id": run_id, "home_tenant": response.UserTenantId, "home_subject": response.UserId,
                "approver": responder.approver, "role": responder.role,
                "decision_at": decision_at.isoformat(), "response_digest": response_digest,
            },
        }
        control.fresh(intent)
        await control.store.replace(intent.tenant, "approval:" + intent.nonce, replacement, etag)
        control.fresh(intent)
        return 200, {"grant": grant}
