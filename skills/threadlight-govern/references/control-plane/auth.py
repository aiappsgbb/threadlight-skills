"""Entra boundary: trusted configuration selects authority, never token URLs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Annotated, Literal

import httpx
import jwt
from pydantic import Field, model_validator

from .models import Identifier, KeyId, ObjectId, StrictModel, strict_json


class Workload(StrictModel):
    client_id: ObjectId
    agent_id: Identifier
    policies: Annotated[list[Identifier], Field(min_length=1, max_length=64)]


class Settings(StrictModel):
    tenant_id: ObjectId
    audience: Annotated[str, Field(min_length=1, max_length=256)]
    key_id: KeyId
    workloads: Annotated[dict[ObjectId, Workload], Field(min_length=1, max_length=128)]
    human_clients: Annotated[list[ObjectId], Field(min_length=1, max_length=128)]
    approver_subjects: Annotated[list[ObjectId], Field(min_length=1, max_length=256)]
    auditor_subjects: Annotated[list[ObjectId], Field(max_length=256)] = []
    approver_roles: Annotated[list[Identifier], Field(min_length=1, max_length=16)]
    token_version: Literal["1.0", "2.0"] = "2.0"
    request_timeout: Annotated[float, Field(gt=0, le=30)] = 5.0
    approval_max_seconds: Annotated[int, Field(gt=0, le=3600)] = 300

    @model_validator(mode="after")
    def separate_authorities(self):
        if set(self.workloads).intersection(self.approver_subjects):
            raise ValueError("self approval forbidden")
        return self

    @property
    def issuer(self):
        if self.token_version == "1.0":
            return f"https://sts.windows.net/{self.tenant_id}/"
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self):
        version = "v2.0/" if self.token_version == "2.0" else ""
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/{version}keys"


class Unauthorized(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    tenant: str
    subject: str
    client: str
    roles: frozenset[str]
    scopes: frozenset[str]
    workload: Workload | None


class EntraAuth:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http
        self.keys = {}
        self.refreshed = float("-inf")
        self.lock = asyncio.Lock()

    async def refresh(self):
        async with self.lock:
            if time.monotonic() - self.refreshed < 30:
                return
            async with asyncio.timeout(self.settings.request_timeout):
                async with self.http.stream("GET", self.settings.jwks_url,
                                            follow_redirects=False) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 128 * 1024:
                            raise Unauthorized()
            document = strict_json(bytes(raw))
            keys = {}
            if not isinstance(document, dict) or not 1 <= len(document.get("keys", [])) <= 64:
                raise Unauthorized()
            for key in document["keys"]:
                if (key.get("kty") != "RSA" or key.get("use", "sig") != "sig"
                        or key.get("alg", "RS256") != "RS256"):
                    continue
                kid = key["kid"]
                if not isinstance(kid, str) or not 1 <= len(kid) <= 128 or kid in keys:
                    raise Unauthorized()
                public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
                if public.key_size < 2048 or hasattr(public, "private_numbers"):
                    raise Unauthorized()
                keys[kid] = public
            if not keys:
                raise Unauthorized()
            self.keys, self.refreshed = keys, time.monotonic()

    async def health(self):
        await self.refresh()
        if not self.keys or time.monotonic() - self.refreshed > 300:
            raise Unauthorized()

    async def authenticate(self, authorization: str | None) -> Identity:
        try:
            if (not authorization or len(authorization) > 16384
                    or not authorization.startswith("Bearer ")):
                raise Unauthorized()
            token = authorization[7:]
            header = jwt.get_unverified_header(token)
            if (header.get("alg") != "RS256" or not isinstance(header.get("kid"), str)
                    or set(header).intersection({"jku", "jwk", "x5u", "crit"})):
                raise Unauthorized()
            if header["kid"] not in self.keys or time.monotonic() - self.refreshed > 300:
                await self.refresh()
            if time.monotonic() - self.refreshed > 300:
                raise Unauthorized()
            claims = jwt.decode(
                token, self.keys[header["kid"]], algorithms=["RS256"],
                issuer=self.settings.issuer, audience=self.settings.audience,
                options={"require": ["exp", "nbf", "iat", "iss", "aud", "tid", "oid", "ver"],
                         "strict_aud": True}, leeway=0,
            )
            if (claims["tid"] != self.settings.tenant_id
                    or claims["ver"] != self.settings.token_version
                    or any(type(claims[key]) is not int for key in ("exp", "nbf", "iat"))):
                raise Unauthorized()
            client = claims.get("azp" if self.settings.token_version == "2.0" else "appid")
            if (not isinstance(claims["oid"], str) or not isinstance(client, str)
                    or not isinstance(claims.get("roles"), list)
                    or not all(isinstance(role, str) for role in claims["roles"])):
                raise Unauthorized()
            roles = frozenset(claims["roles"])
            subject = claims["oid"]
            if claims.get("idtyp") == "app" and "scp" not in claims:
                workload = self.settings.workloads.get(subject)
                if (workload is None or client != workload.client_id
                        or "Governance.Workload" not in roles):
                    raise Unauthorized()
                return Identity(claims["tid"], subject, client, roles, frozenset(), workload)
            scopes = claims.get("scp")
            if (claims.get("idtyp") == "app" or not isinstance(scopes, str) or not scopes
                    or client not in self.settings.human_clients
                    or subject not in set(self.settings.approver_subjects + self.settings.auditor_subjects)):
                raise Unauthorized()
            return Identity(claims["tid"], subject, client, roles, frozenset(scopes.split()), None)
        except Exception:
            raise Unauthorized() from None
