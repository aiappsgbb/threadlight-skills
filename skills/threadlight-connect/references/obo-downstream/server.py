"""User-scoped MCP downstream for an on-behalf-of (OBO) proof.

The server accepts only a Microsoft Entra *delegated* access token issued for
its own API (v2 token, ``aud`` = the API application ID) and decides access
from the token alone:

* no or invalid token (signature, issuer, audience, expiry) -> HTTP 401;
* app-only token (no ``scp``) -> HTTP 403 ``delegated_token_required``: the
  agent acting as itself is never enough;
* delegated token without the required scope -> HTTP 403 ``missing_scope``;
* signed-in user without the required app role -> the tool answers
  ``access_denied`` for that user.

The tool reports who the downstream saw: the user (``oid``, ``tid``) and the
actor application (``azp``/``appid``), so a run shows that user context was
preserved. Logs carry a short hash of the user object ID and the decision,
never a token or a claim set.

Environment: ``OBO_TENANT_ID``, ``OBO_API_CLIENT_ID`` (required),
``OBO_REQUIRED_SCOPE`` (default ``Records.Read``), ``OBO_REQUIRED_ROLE``
(default ``Records.Reader``), ``PORT`` (default 8080).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from typing import Any

import jwt
from mcp.server.fastmcp import Context, FastMCP

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("obo-downstream")

TENANT = os.environ.get("OBO_TENANT_ID", "")
AUDIENCE = os.environ.get("OBO_API_CLIENT_ID", "")
SCOPE = os.environ.get("OBO_REQUIRED_SCOPE", "Records.Read")
ROLE = os.environ.get("OBO_REQUIRED_ROLE", "Records.Reader")
_JWKS = jwt.PyJWKClient(f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys", cache_keys=True)

RECORDS = [
    {"id": "R-100", "title": "Quarterly supplier review", "classification": "internal"},
    {"id": "R-200", "title": "Return policy exception list", "classification": "internal"},
]


def user_ref(oid: str) -> str:
    """Short, non-reversible reference for logs (never the raw object ID)."""
    return hashlib.sha256(oid.encode()).hexdigest()[:10] if oid else "-"


def validate(token: str, *, key=None) -> dict:
    """Validate a v2 delegated token for this API; raise ``PermissionError(status, code)``."""
    try:
        signing_key = key or _JWKS.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, signing_key, algorithms=["RS256"], audience=AUDIENCE,
            issuer=f"https://login.microsoftonline.com/{TENANT}/v2.0",
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise PermissionError(401, f"invalid_token:{type(exc).__name__}") from None
    scopes = str(claims.get("scp") or "").split()
    if not scopes:
        raise PermissionError(403, "delegated_token_required")
    if SCOPE not in scopes:
        raise PermissionError(403, "missing_scope")
    return claims


def decide(claims: dict) -> dict:
    """The user-scoped decision and what the downstream saw (no secrets)."""
    seen = {
        "user_oid": claims.get("oid"),
        "user_tid": claims.get("tid"),
        "user_name": claims.get("preferred_username") or claims.get("upn"),
        "actor_app_id": claims.get("azp") or claims.get("appid"),
        "token_type": "delegated",
        "scopes": str(claims.get("scp") or "").split(),
    }
    allowed = ROLE in (claims.get("roles") or [])
    log.info("decision=%s user=%s actor=%s", "allow" if allowed else "deny",
             user_ref(str(claims.get("oid") or "")), seen["actor_app_id"])
    if not allowed:
        return {"result": "access_denied", "reason": f"user lacks app role {ROLE}", "seen": seen}
    return {"result": "ok", "records": RECORDS, "seen": seen}


class AuthMiddleware:
    """Pure ASGI middleware: every HTTP request needs a valid delegated token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        header = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        try:
            if not token:
                raise PermissionError(401, "missing_token")
            claims = validate(token)
        except PermissionError as exc:
            status, code = exc.args
            log.info("decision=reject status=%s reason=%s", status, code)
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json")]})
            return await send({"type": "http.response.body", "body": json.dumps({"error": code}).encode()})
        scope.setdefault("state", {})["claims"] = claims
        return await self.app(scope, receive, send)


mcp = FastMCP("obo-downstream", host="0.0.0.0", port=int(os.getenv("PORT") or "8080"), stateless_http=True)


@mcp.tool(structured_output=True)
def list_my_records(ctx: Context) -> dict[str, Any]:
    """List the records the signed-in user may read, and who the downstream saw."""
    request = ctx.request_context.request
    claims = ((getattr(request, "scope", None) or {}).get("state") or {}).get("claims")
    if not claims:
        return {"result": "access_denied", "reason": "no validated user token"}
    return decide(claims)


if __name__ == "__main__":
    if not (TENANT and AUDIENCE):
        sys.exit("OBO_TENANT_ID and OBO_API_CLIENT_ID are required")
    import uvicorn

    uvicorn.run(AuthMiddleware(mcp.streamable_http_app()), host="0.0.0.0",
                port=int(os.getenv("PORT") or "8080"), log_level="warning")
