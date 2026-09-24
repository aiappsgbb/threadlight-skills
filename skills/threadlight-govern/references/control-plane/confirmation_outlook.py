"""Native in-mail requester consent from verified Outlook/ARM witness, never ApprovalGrant."""
from __future__ import annotations

from datetime import datetime, timezone

from .auth import Unauthorized
from .confirmation import RequestingUser, digest
from .models import Identifier, canonical, parse
from .outlook import OutlookWitness, OutlookUnavailable
from .storage import Conflict


class NativeUserConfirmation(OutlookWitness):
    def __init__(self, profile, credential, http):
        super().__init__(profile.outlook, credential, http, trigger_url=profile.notification_url)
        self.profile = profile

    def requester(self, control, service, user):
        binding = service.user_binding(self.profile, user)
        direct = (control.settings.tenant_id, user.subject) if user.issuer == control.settings.issuer else None
        home = ((binding.requester_home_tenant, binding.requester_home_subject)
                if binding.requester_home_subject is not None else direct)
        actual = (self.config.home_tenant, self.config.home_subject)
        if (home != actual or self.config.home_subject in control.settings.workloads
                or self.config.home_subject == self.config.sender_principal):
            raise Unauthorized()
        return binding

    def configuration(self, control):
        binding = self.profile.users[0]
        direct = ((control.settings.tenant_id, binding.subject)
                  if binding.issuer == control.settings.issuer else None)
        home = ((binding.requester_home_tenant, binding.requester_home_subject)
                if binding.requester_home_subject is not None else direct)
        if (home != (self.config.home_tenant, self.config.home_subject)
                or self.config.home_subject in control.settings.workloads
                or self.config.home_subject == self.config.sender_principal):
            raise Unauthorized()

    def payload(self, control, service, intent, record):
        user = parse(RequestingUser, canonical(record["user"]))
        self.requester(control, service, user)
        facts, arguments = record["facts"], record["arguments"]
        if (digest(facts) != intent.facts_hash
                or digest({"facts": facts, "arguments": arguments}) != intent.action_hash):
            raise Conflict()
        return {
            "status": "pending_confirmation",
            "confirmation_id": intent.confirmation_id, "operation_id": intent.operation_id,
            "action_hash": intent.action_hash, "intent_digest": digest(intent),
            "expires_at": intent.expires_at.isoformat(),
            "confirmation_intent": intent.model_dump(mode="json"),
            "review_context": facts, "proposed_arguments": arguments,
        }

    def initial(self, control, service, intent, record):
        return {
            "payload": self.payload(control, service, intent, record),
            "profile_digest": digest(self.profile), "workflow_version": self.config.workflow_version,
            "workflow_digest": self.config.workflow_digest, "dispatch_state": "prepared",
            "run_id": None, "created_at": control.now().isoformat(),
        }

    def validate_record(self, control, service, intent, record):
        outbox = record.get("outlook")
        if (not isinstance(outbox, dict) or outbox["profile_digest"] != digest(self.profile)
                or outbox["workflow_version"] != self.config.workflow_version
                or outbox["workflow_digest"] != self.config.workflow_digest
                or outbox["payload"] != self.payload(control, service, intent, record)):
            raise Conflict()
        return outbox

    async def guard(self, control, service, identity, intent, record):
        await service.fresh(control, intent)
        await service.context(control, intent)
        service.gateway_authority(control, identity)
        self.validate_record(control, service, intent, record)
        if intent.expires_at <= datetime.now(timezone.utc):
            raise Conflict()

    async def health(self, control, service, *, guard):
        self.configuration(control)
        await self.verify_workflow(control, guard=guard, trigger_name="User_confirmation_requested",
                                   strict_access=True)
        self.configuration(control)

    async def persist(self, control, service, identity, intent, record, etag, replacement):
        await self.guard(control, service, identity, intent, record)
        await service.store.replace(intent.tenant, "confirmation:" + intent.confirmation_id, replacement, etag)
        await self.guard(control, service, identity, intent, replacement)
        actual, actual_etag = await service.store.read(intent.tenant, "confirmation:" + intent.confirmation_id)
        await self.guard(control, service, identity, intent, actual)
        if actual != replacement:
            raise Conflict()
        return actual, actual_etag

    async def validate_authority(self, control, service, identity, intent, record):
        outbox = self.validate_record(control, service, intent, record)
        authority = record.get("authority", {})
        if (outbox["dispatch_state"] != "sent" or record["notification"] != "sent"
                or authority.get("kind") != "outlook-native"
                or authority.get("workflow_resource_id") != self.config.workflow_resource_id
                or authority.get("workflow_version") != self.config.workflow_version
                or authority.get("workflow_digest") != self.config.workflow_digest
                or authority.get("run_id") != outbox["run_id"]
                or authority.get("home_tenant") != self.config.home_tenant
                or authority.get("home_subject") != self.config.home_subject
                or authority.get("approved") is not record.get("approved")):
            raise Conflict()
        await self.health(control, service, guard=lambda: self.guard(control, service, identity, intent, record))
        service.authority_expiry(intent, self.profile, record)

    async def resolve(self, control, service, identity, intent, record, etag):
        outbox = self.validate_record(control, service, intent, record)
        guard = lambda: self.guard(control, service, identity, intent, record)
        if record["state"] != "pending":
            await self.validate_authority(control, service, identity, intent, record)
            return record, etag
        await self.health(control, service, guard=guard)
        if outbox["dispatch_state"] == "prepared":
            outbox = {**outbox, "dispatch_state": "sending"}
            record, etag = await self.persist(control, service, identity, intent, record, etag,
                {**record, "outlook": outbox, "notification": "sending"})
            status, headers, _ = await self.request("POST", self.trigger_url, guard=guard,
                payload=outbox["payload"], tracking=intent.confirmation_id)
            if status != 202 or not headers.get("x-ms-workflow-run-id"):
                raise OutlookUnavailable("outlook_notification_outcome_unknown")
            run_id = parse(Identifier, canonical(headers["x-ms-workflow-run-id"]))
            return await self.persist(control, service, identity, intent, record, etag,
                {**record, "outlook": {**outbox, "dispatch_state": "sent", "run_id": run_id},
                 "notification": "sent"})
        if outbox["dispatch_state"] == "sending":
            run_id = await self.recover_run(intent.confirmation_id, guard=guard)
            record, etag = await self.persist(control, service, identity, intent, record, etag,
                {**record, "outlook": {**outbox, "dispatch_state": "sent", "run_id": run_id},
                 "notification": "sent"})
            outbox = record["outlook"]
        if outbox["dispatch_state"] != "sent":
            raise OutlookUnavailable("outlook_dispatch_state_invalid")
        observed = await self.response(control, run_id=parse(Identifier, canonical(outbox["run_id"])),
            tracking=intent.confirmation_id, payload=outbox["payload"], created_at=outbox["created_at"],
            expires_at=intent.expires_at, guard=guard)
        if observed is None:
            return record, etag
        response, decision_at, response_digest = observed
        if (response.UserTenantId, response.UserId) != (self.config.home_tenant, self.config.home_subject):
            raise Unauthorized()
        await self.health(control, service, guard=guard)
        await guard()
        approved = response.SelectedOption == self.config.approved_option
        authority = {
            "kind": "outlook-native", "capability": "authenticated-consent",
            "profile_digest": digest(self.profile), "result_expires_at": None, "protection_generation": None,
            "workflow_resource_id": self.config.workflow_resource_id,
            "workflow_version": self.config.workflow_version, "workflow_digest": self.config.workflow_digest,
            "run_id": outbox["run_id"], "home_tenant": response.UserTenantId, "home_subject": response.UserId,
            "approved": approved, "decided_at": decision_at.isoformat(), "response_digest": response_digest,
        }
        replacement = {**record, "state": "decided", "approved": approved, "authority": authority}
        service.authority_expiry(intent, self.profile, replacement)
        actual, etag = await self.persist(control, service, identity, intent, record, etag, replacement)
        service.authority_expiry(intent, self.profile, actual)
        return actual, etag
