"""Separate interactive requesting-user client. Never install this as an agent tool."""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from azure.core.exceptions import AzureError
import httpx

from .confirmation import ConfirmationIntent, ContextRequest, digest, https
from .models import Nonce, ObjectId, canonical, parse, strict_json


def validate_view(value):
    try:
        if (set(value) != {"intent", "intent_digest", "facts", "arguments", "state"}
                or len(canonical(value)) > 16384 or value["state"] != "pending"):
            raise ValueError()
        intent = parse(ConfirmationIntent, canonical(value["intent"]))
        if (digest(intent) != value["intent_digest"] or digest(value["facts"]) != intent.facts_hash
                or digest({"facts": value["facts"], "arguments": value["arguments"]}) != intent.action_hash
                or not datetime.now(timezone.utc) < intent.expires_at <= intent.policy_expires_at):
            raise ValueError()
        return intent
    except (ValueError, KeyError, TypeError):
        raise ValueError("confirmation_display_invalid") from None


class InteractiveUserCredential:
    """The pinned Azure Identity browser credential is synchronous, not aio."""
    def __init__(self, *, tenant_id, client_id):
        from azure.identity import InteractiveBrowserCredential
        self.credential = InteractiveBrowserCredential(tenant_id=tenant_id, client_id=client_id, timeout=120)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await asyncio.to_thread(self.credential.close)

    async def get_token(self, scope, **kwargs):
        return await asyncio.to_thread(self.credential.get_token, scope, **kwargs)

    async def authenticate(self, **kwargs):
        return await asyncio.to_thread(self.credential.authenticate, **kwargs)


class ConfirmationUserClient:
    def __init__(self, *, base_url, scope, tenant, credential, http):
        https(base_url)
        if urlsplit(base_url).path not in ("", "/") or not scope.endswith("/Governance.Confirm"):
            raise ValueError("confirmation_client_configuration")
        parse(ObjectId, canonical(tenant))
        self.url, self.scope, self.tenant = base_url.rstrip("/"), scope, tenant
        self.credential, self.http = credential, http

    async def request(self, method, path, body=None, *, challenge=False):
        token = await self.credential.get_token(self.scope, enable_cae=True)

        async def send(token):
            if token.expires_on <= datetime.now(timezone.utc).timestamp():
                raise ValueError("confirmation_token_expired")
            async with asyncio.timeout(10):
                async with self.http.stream(method, self.url + path,
                        headers={"Authorization": "Bearer " + token.token, "Content-Type": "application/json"},
                        content=canonical(body) if body is not None else None, follow_redirects=False) as response:
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 16384:
                            raise ValueError("confirmation_response_invalid")
                    return response.status_code, response.headers, bytes(raw)
        status, headers, raw = await send(token)
        if status == 401 and challenge:
            header = headers.get("www-authenticate", "")
            match = re.fullmatch(
                r'Bearer error="insufficient_claims", authorization_uri="'
                + re.escape(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/authorize")
                + r'", claims="([A-Za-z0-9+/=]{1,2048})"', header)
            if not match:
                raise ValueError("confirmation_challenge_invalid")
            claims = strict_json(base64.b64decode(match[1], validate=True))
            requested = claims.get("access_token", {}).get("acrs", {})
            if (set(claims) != {"access_token"} or set(claims["access_token"]) != {"acrs"}
                    or set(requested) != {"essential", "value"} or requested["essential"] is not True
                    or not re.fullmatch(r"c[1-9][0-9]?", requested["value"])):
                raise ValueError("confirmation_challenge_invalid")
            # A cached token may already contain acrs but predate protection.
            # Explicit interaction requests fresh issuance, not a new MFA factor.
            if headers.get("retry-after") != "1":
                raise ValueError("confirmation_challenge_invalid")
            await asyncio.sleep(1)
            await self.credential.authenticate(
                scopes=[self.scope], claims=canonical(claims).decode(), enable_cae=True)
            token = await self.credential.get_token(self.scope, claims=canonical(claims).decode(), enable_cae=True)
            status, _, raw = await send(token)
        if status != 200:
            raise ValueError("confirmation_not_completed")
        return strict_json(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["register", "confirm", "reject"])
    parser.add_argument("--request", type=Path)
    parser.add_argument("--confirmation-id")
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--client-id", required=True)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        print("Interactive requesting-user terminal required; no unattended consent.", file=sys.stderr)
        return 2

    async def run():
        parse(ObjectId, canonical(args.client_id))
        async with InteractiveUserCredential(tenant_id=args.tenant, client_id=args.client_id) as credential, \
                httpx.AsyncClient(timeout=10, trust_env=False) as http:
            client = ConfirmationUserClient(base_url=args.control_plane_url, scope=args.scope,
                                            tenant=args.tenant, credential=credential, http=http)
            if args.operation == "register":
                if args.request is None or args.confirmation_id is not None:
                    raise ValueError("context_request_required")
                with args.request.open("rb") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError("context_request_too_large")
                request = parse(ContextRequest, raw)
                print(json.dumps(request.model_dump(mode="json"), indent=2))
                if input("Type REGISTER " + request.operation_id + ": ").strip() != "REGISTER " + request.operation_id:
                    raise ValueError("context_not_registered")
                return await client.request("POST", "/confirmation/contexts", request.model_dump(mode="json"))
            parse(Nonce, canonical(args.confirmation_id))
            path = "/confirmation/" + args.confirmation_id
            view = await client.request("GET", path)
            intent = validate_view(view)
            if intent.confirmation_id != args.confirmation_id or intent.tenant != args.tenant:
                raise ValueError("confirmation_identity_mismatch")
            print(json.dumps({"action": intent.action, "operation_id": intent.operation_id,
                "arguments": view["arguments"], "action_hash": intent.action_hash,
                "expires_at": intent.expires_at.isoformat()}, indent=2, ensure_ascii=True))
            expected = args.operation.upper() + " " + view["intent_digest"]
            if input("Type " + expected + ": ").strip() != expected:
                raise ValueError("confirmation_not_submitted")
            validate_view(view)
            result = await client.request("POST", path, {
                "intent_digest": view["intent_digest"], "approved": args.operation == "confirm"}, challenge=True)
            if result != {"confirmation_id": intent.confirmation_id,
                          "status": "confirmed" if args.operation == "confirm" else "rejected"}:
                raise ValueError("confirmation_response_invalid")
            return result
    try:
        print(json.dumps(asyncio.run(run())))
        return 0
    except (OSError, ValueError, EOFError, AzureError, httpx.HTTPError, TimeoutError):
        print("Confirmation not completed; verify requesting-user identity, intent and configuration.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
