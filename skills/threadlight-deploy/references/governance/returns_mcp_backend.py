"""Narrow returns decision service; gateway caller and Cosmos writer are distinct."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
from typing import Annotated, Literal

from azure.core.exceptions import AzureError
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from pydantic import Field, ValidationError

from govern_control_plane.auth import EntraAuth, Settings, Unauthorized
from govern_control_plane.models import (
    Digest, Identifier, ObjectId, StrictModel, canonical, parse, strict_json,
)


class BusinessConflict(Exception):
    pass


class Decision(StrictModel):
    case_id: Identifier
    expected_etag: Annotated[str, Field(min_length=1, max_length=128)]
    decision: Literal[
        "approve_refund", "deny_refund", "escalate_to_supervisor", "request_more_info"
    ]
    reason: Annotated[str, Field(min_length=1, max_length=512)]


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def decision_batch(case, arguments, *, operation_id, provenance):
    args = Decision.model_validate(arguments)
    if (
        case.get("id") != args.case_id or case.get("case_id") != args.case_id
        or case.get("kind") != "case" or case.get("_etag") != args.expected_etag
        or case.get("status") != "in_triage"
    ):
        raise BusinessConflict()
    if (
        type(case.get("amount")) is not int or case["amount"] < 0
        or type(case.get("eligible")) is not bool
        or type(case.get("high_risk")) is not bool
    ):
        raise BusinessConflict()
    high_risk = case["amount"] > 500 or case["high_risk"]
    if (
        high_risk and args.decision != "escalate_to_supervisor"
        or args.decision == "approve_refund" and not case["eligible"]
        or args.decision == "deny_refund" and case["eligible"]
    ):
        raise BusinessConflict()
    updated = {key: deepcopy(value) for key, value in case.items() if not key.startswith("_")}
    status = {
        "escalate_to_supervisor": "escalated",
        "request_more_info": "in_triage",
        "approve_refund": "closed",
        "deny_refund": "closed",
    }[args.decision]
    updated.update(status=status, decision=args.decision, audit_id=operation_id)
    result = {"case_id": args.case_id, "decision": args.decision, "audit_id": operation_id}
    audit = {
        "id": operation_id, "case_id": args.case_id, "kind": "decision-audit",
        "arguments": args.model_dump(), "provenance": deepcopy(provenance),
        "result": result, "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    return [
        ("replace", (case["id"], updated), {"if_match_etag": args.expected_etag}),
        ("create", (audit,), {}),
    ], result


class Configuration(Settings):
    cosmos_url: Annotated[str, Field(pattern=r"^https://[a-z0-9-]+\.documents\.azure\.com:443/$")]
    cosmos_database: Identifier
    cosmos_container: Identifier
    service_client_id: ObjectId
    writer_subject: ObjectId
    agent_subject: ObjectId
    cases: Annotated[list[Identifier], Field(min_length=1, max_length=16)]
    policy_digest: Digest
    deployment: dict


def create_app():
    from azure.identity.aio import ManagedIdentityCredential
    from cosmos_effect import CosmosEffectTransport

    config = parse(Configuration, Path(os.environ["RETURNS_CONFIG_FILE"]).read_bytes())
    if (
        config.writer_subject == config.agent_subject
        or {config.writer_subject, config.agent_subject} != set(config.workloads)
    ):
        raise ValueError("distinct_explicit_business_callers_required")
    logger = logging.getLogger("returns_business")
    for name in ("azure", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    state = {}

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            credential = await stack.enter_async_context(ManagedIdentityCredential(
                client_id=config.service_client_id, retry_total=0))
            transport = CosmosEffectTransport(endpoint=config.cosmos_url)
            container = await transport.connect(
                stack=stack, credential=credential,
                database=config.cosmos_database, container=config.cosmos_container)
            properties = await container.read()
            if (
                properties.get("partitionKey", {}).get("paths") != ["/case_id"]
                or "defaultTtl" in properties
            ):
                raise ValueError("persistent_case_partition_required")
            http = await stack.enter_async_context(httpx.AsyncClient(
                timeout=5, trust_env=False, follow_redirects=False))
            auth = EntraAuth(config, http)
            await auth.health()
            state.update(container=container, auth=auth, transport=transport)
            yield
            state.clear()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def authenticated(request, *, writer=False):
        identity = await state["auth"].authenticate(request.headers.get("authorization"))
        allowed = {config.writer_subject} if writer else {config.agent_subject}
        if identity.subject not in allowed or identity.workload is None:
            raise Unauthorized()
        return identity

    def provenance(request):
        expected = {
            "x-tenant-id": config.tenant_id,
            "x-requester-id": config.agent_subject,
            "x-action-id": "returns_apply_decision",
            "x-policy-digest": config.policy_digest,
            "x-deployment-hash": digest(config.deployment),
        }
        if any(request.headers.get(key) != value for key, value in expected.items()):
            raise BusinessConflict()
        key = parse(Identifier, canonical(request.headers.get("idempotency-key")))
        result = {
            **expected,
            "receipt_id": parse(Identifier, canonical(request.headers.get("x-governance-provenance"))),
            "action_hash": parse(Digest, canonical(request.headers.get("x-action-hash"))),
        }
        operation = "decision-" + digest([config.tenant_id, config.agent_subject, key])[7:]
        return operation, result

    async def existing(operation, expected):
        for case_id in config.cases:
            try:
                record = await state["container"].read_item(operation, partition_key=case_id)
            except CosmosResourceNotFoundError:
                continue
            if record.get("kind") != "decision-audit" or record.get("provenance") != expected:
                raise BusinessConflict()
            return record
        return None

    @app.exception_handler(Unauthorized)
    async def unauthorized(request, error):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @app.exception_handler(BusinessConflict)
    async def conflict(request, error):
        return JSONResponse({"error": "business_conflict"}, status_code=409)

    @app.exception_handler(ValidationError)
    async def invalid(request, error):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(AzureError)
    async def unavailable(request, error):
        logger.error("returns_backend_unavailable")
        return JSONResponse({"error": "outcome_unknown"}, status_code=503)

    @app.get("/health")
    async def health():
        async with asyncio.timeout(5):
            await state["auth"].health()
            await state["container"].read()
        return {"status": "ready", "effect": "cosmos-return-decision-not-settlement"}

    @app.get("/cases/{case_id}")
    async def read_case(case_id: str, request: Request):
        await authenticated(request)
        if case_id not in config.cases:
            raise BusinessConflict()
        record = await state["container"].read_item(case_id, partition_key=case_id)
        return {key: record[key] for key in (
            "id", "_etag", "status", "amount", "eligible", "high_risk")}

    @app.get("/outcomes")
    async def outcome(request: Request):
        await authenticated(request, writer=True)
        operation, proof = provenance(request)
        record = await existing(operation, proof)
        if record is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {"receipt_id": operation, "result": record["result"]}

    @app.post("/decisions")
    async def decide(request: Request):
        await authenticated(request, writer=True)
        operation, proof = provenance(request)
        body = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16384:
                    return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            args = Decision.model_validate(strict_json(bytes(body)))
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=422)
        arguments = args.model_dump()
        if args.case_id not in config.cases:
            raise BusinessConflict()
        facts = {
            "tenant": config.tenant_id, "subject": config.agent_subject,
            "client": config.workloads[config.agent_subject].client_id,
            "action": "returns_apply_decision", "scope": "returns",
            "policy": config.policy_digest, "deployment": config.deployment,
        }
        if digest({"facts": facts, "arguments": arguments}) != proof["action_hash"]:
            raise BusinessConflict()
        previous = await existing(operation, proof)
        if previous is not None:
            if previous["arguments"] != arguments:
                raise BusinessConflict()
            return {"receipt_id": operation, "result": previous["result"]}
        container = state["container"]
        case = await container.read_item(args.case_id, partition_key=args.case_id)
        operations, result = decision_batch(
            case, arguments, operation_id=operation, provenance=proof)

        async def reauthorize():
            await authenticated(request, writer=True)

        with state["transport"].batch(reauthorize, container, args.case_id, operations):
            await reauthorize()
            await container.execute_item_batch(
                batch_operations=operations, partition_key=args.case_id)
        return {"receipt_id": operation, "result": result}

    return app
