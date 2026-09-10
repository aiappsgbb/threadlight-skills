"""Authenticated operator review of an exact pending action, never action execution."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from azure.core.exceptions import AzureError
import httpx

from .client import ApprovalUnavailable, ServiceTransport
from .models import ApprovalGrant, ApprovalRequest, Identifier, ObjectId, canonical, parse, strict_json


def validate_pending_review(document):
    """Validate display binding; the service remains the decision authority."""
    try:
        required = {"status", "operation_id", "approval_intent", "review_context", "proposed_arguments"}
        if (not isinstance(document, dict) or not required <= document.keys()
                or set(document) - required - {"resume_arguments"}
                or document["status"] != "pending_approval" or len(canonical(document)) > 65536):
            raise ValueError()
        parse(Identifier, canonical(document["operation_id"]))
        intent = parse(ApprovalRequest, canonical(document["approval_intent"]))
        facts, proposed = document["review_context"], document["proposed_arguments"]
        if not isinstance(facts, dict) or not isinstance(proposed, dict):
            raise ValueError()
        for key in ("tenant", "subject", "client"):
            parse(ObjectId, canonical(facts[key]))
        for key in ("action", "scope"):
            parse(Identifier, canonical(facts[key]))
        context_hash = "sha256:" + hashlib.sha256(canonical(facts)).hexdigest()
        action_hash = "sha256:" + hashlib.sha256(canonical({"facts": facts, "arguments": proposed})).hexdigest()
        now = datetime.now(timezone.utc)
        if (intent.context_identity != context_hash or intent.action_hash != action_hash
                or intent.policy_hash != facts["policy"] or intent.tenant != facts["tenant"]
                or not now < intent.expires_at <= intent.policy_expires_at):
            raise ValueError()
        return intent
    except (ValueError, TypeError, KeyError):
        raise ValueError("pending_review_invalid_or_expired") from None


async def decide(pending, *, approved, role, base_url, scope, credential, http=None):
    intent = validate_pending_review(pending)
    if type(approved) is not bool or role not in intent.allowed_roles:
        raise ValueError("review_decision_or_role_invalid")
    async with ServiceTransport(base_url=base_url, scope=scope, credential=credential, http=http) as service:
        status, body = await service.request("POST", "/approvals/resolve", {
            "operation": "decide", "intent": intent.model_dump(mode="json"),
            "approved": approved, "approving_role": role})
    if status != 200 or set(body) != {"grant"}:
        raise ApprovalUnavailable()
    grant = parse(ApprovalGrant, canonical(body["grant"]))
    if (grant.intent != intent or grant.approved is not approved
            or grant.approver_tenant != intent.tenant or grant.approver_role != role):
        raise ApprovalUnavailable()
    return {"operation_id": pending["operation_id"], "approved": approved, "execution": "not-started"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pending", type=Path, required=True)
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--client-id", required=True, help="Existing allowlisted public review UI application")
    parser.add_argument("--role", required=True)
    parser.add_argument("--decision", choices=("approve", "reject"), required=True)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        print("Interactive human terminal required; no unattended approval.", file=sys.stderr)
        return 2
    try:
        ServiceTransport.validate_configuration(args.control_plane_url, args.scope, 5)
        parse(ObjectId, canonical(args.tenant))
        parse(ObjectId, canonical(args.client_id))
        with args.pending.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("pending_review_too_large")
        pending = strict_json(raw)
        intent = validate_pending_review(pending)
        if intent.tenant != args.tenant or args.role not in intent.allowed_roles:
            raise ValueError("review_tenant_or_role_mismatch")
        print(json.dumps({
            "action": pending["review_context"]["action"], "tenant": intent.tenant,
            "proposed_arguments": pending["proposed_arguments"],
            "expires_at": intent.expires_at.isoformat(), "action_hash": intent.action_hash,
        }, indent=2, ensure_ascii=True))
        expected = args.decision.upper() + " " + intent.nonce
        if input(f"Type {expected} to record this decision: ").strip() != expected:
            print("Decision not submitted.", file=sys.stderr)
            return 2
        validate_pending_review(pending)
        from azure.identity import InteractiveBrowserCredential
        with InteractiveBrowserCredential(
                tenant_id=args.tenant, client_id=args.client_id, timeout=120) as browser:
            token = browser.get_token(args.scope)

        class ReviewCredential:
            async def get_token(self, scope):
                if scope != args.scope or time.time() >= token.expires_on:
                    raise ApprovalUnavailable()
                return token

        result = asyncio.run(decide(
            pending, approved=args.decision == "approve", role=args.role,
            base_url=args.control_plane_url, scope=args.scope, credential=ReviewCredential()))
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, ApprovalUnavailable, EOFError, AzureError, httpx.HTTPError):
        print("Review not completed; verify the pending action, identity and service configuration.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
