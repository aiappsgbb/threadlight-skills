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
import uuid

from azure.core.exceptions import AzureError
from azure.cosmos.exceptions import CosmosBatchOperationError, CosmosResourceNotFoundError
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


CASE_FIELDS = ("id", "_etag", "status", "amount", "eligible", "high_risk")


def read_audit_record(case, identity, *, tenant, deployment):
    return {
        "id": "read-" + uuid.uuid4().hex,
        "scope": tenant + ":" + identity.subject,
        "body": {
            "kind": "case-read", "action_id": "returns_get_case", "policy_binding": "none",
            "subject": identity.subject, "client": identity.client,
            "case_id": case["id"], "case_revision": case["_etag"],
            "deployment": deepcopy(deployment),
            "result_digest": digest({key: case[key] for key in CASE_FIELDS}),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def append_read_audit(transport, container, document, authorize):
    operations = [("create", (document,), {})]
    with transport.append_audit(authorize, container, document["scope"], document):
        await authorize()
        await container.execute_item_batch(batch_operations=operations, partition_key=document["scope"])
    return document["id"]


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
    read_audit_container: Identifier | None = None
    recovery_enabled: bool = False


def recovery_fence(arguments, *, operation_id, provenance):
    args = Decision.model_validate(arguments)
    return {"id": operation_id, "case_id": args.case_id, "kind": "no-effect-fence",
            "arguments": args.model_dump(), "provenance": deepcopy(provenance),
            "recorded_at": datetime.now(timezone.utc).isoformat()}


def recovery_outcome(record, arguments, *, operation_id, provenance):
    args = Decision.model_validate(arguments)
    if (not isinstance(record, dict) or record.get("id") != operation_id
            or record.get("case_id") != args.case_id or record.get("provenance") != provenance
            or record.get("arguments") != args.model_dump()):
        raise BusinessConflict()
    if record.get("kind") == "decision-audit" and isinstance(record.get("result"), dict):
        return "completed"
    if record.get("kind") == "no-effect-fence":
        return "not_executed"
    raise BusinessConflict()


def create_app(*, configuration=None, credential_factory=None, transport_factory=None, auth_transport=None):
    from azure.identity.aio import ManagedIdentityCredential
    from cosmos_effect import CosmosEffectTransport

    config = parse(Configuration, canonical(configuration) if configuration is not None
                   else Path(os.environ["RETURNS_CONFIG_FILE"]).read_bytes())
    credential_factory = credential_factory or ManagedIdentityCredential
    transport_factory = transport_factory or CosmosEffectTransport
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
            credential = await stack.enter_async_context(credential_factory(
                client_id=config.service_client_id, retry_total=0))
            transport = transport_factory(endpoint=config.cosmos_url)
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
                timeout=5, trust_env=False, follow_redirects=False, transport=auth_transport))
            auth = EntraAuth(config, http)
            await auth.health()
            state.update(container=container, auth=auth, transport=transport)
            if config.read_audit_container is not None:
                audit_transport = transport_factory(endpoint=config.cosmos_url)
                audit = await audit_transport.connect(
                    stack=stack, credential=credential, database=config.cosmos_database,
                    container=config.read_audit_container)
                properties = await audit.read()
                if properties.get("partitionKey", {}).get("paths") != ["/scope"] or "defaultTtl" in properties:
                    raise ValueError("persistent_read_audit_partition_required")
                state.update(read_audit=audit, read_audit_transport=audit_transport)
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
            if (record.get("kind") not in {"decision-audit", "no-effect-fence"}
                    or record.get("provenance") != expected):
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
            if config.read_audit_container is not None:
                await state["read_audit"].read()
        return {"status": "ready", "effect": "cosmos-return-decision-not-settlement"}

    @app.get("/cases/{case_id}")
    async def read_case(case_id: str, request: Request):
        identity = await authenticated(request)
        if case_id not in config.cases:
            raise BusinessConflict()
        record = await state["container"].read_item(case_id, partition_key=case_id)
        result = {key: record[key] for key in CASE_FIELDS}
        if config.read_audit_container is not None:
            async def reauthorize():
                await authenticated(request)
            document = read_audit_record(
                record, identity, tenant=config.tenant_id, deployment=config.deployment)
            result["read_audit_id"] = await append_read_audit(
                state["read_audit_transport"], state["read_audit"], document, reauthorize)
        return result

    @app.get("/outcomes")
    async def outcome(request: Request):
        await authenticated(request, writer=True)
        operation, proof = provenance(request)
        record = await existing(operation, proof)
        if record is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if record["kind"] != "decision-audit":
            raise BusinessConflict()
        return {"receipt_id": operation, "result": record["result"]}

    async def decision_arguments(request, proof):
        body = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16384:
                    raise BusinessConflict()
        args = Decision.model_validate(strict_json(bytes(body)))
        if args.case_id not in config.cases:
            raise BusinessConflict()
        facts = {
            "tenant": config.tenant_id, "subject": config.agent_subject,
            "client": config.workloads[config.agent_subject].client_id,
            "action": "returns_apply_decision", "scope": "returns",
            "policy": config.policy_digest, "deployment": config.deployment,
        }
        if digest({"facts": facts, "arguments": args.model_dump()}) != proof["action_hash"]:
            raise BusinessConflict()
        return args

    @app.post("/recovery")
    async def recover(request: Request):
        await authenticated(request, writer=True)
        if not config.recovery_enabled:
            return JSONResponse({"error": "recovery_unsupported"}, status_code=403)
        operation, proof = provenance(request)
        args = await decision_arguments(request, proof)
        container = state["container"]
        record = await existing(operation, proof)
        if record is None:
            fence = recovery_fence(args.model_dump(), operation_id=operation, provenance=proof)
            async def reauthorize():
                await authenticated(request, writer=True)
            # Same ID and partition as the atomic business audit. Either this
            # create or the business transaction wins; a 404 alone proves nothing.
            try:
                with state["transport"].recovery_fence(reauthorize, container, args.case_id, fence):
                    await reauthorize()
                    await container.execute_item_batch(
                        batch_operations=[("create", (fence,), {})], partition_key=args.case_id)
            except CosmosBatchOperationError as exc:
                if exc.status_code not in (409, 412):
                    raise
            record = await existing(operation, proof)
        state_name = recovery_outcome(
            record, args.model_dump(), operation_id=operation, provenance=proof)
        await authenticated(request, writer=True)
        return {"state": state_name, "receipt_id": operation, "action_hash": proof["action_hash"],
                "provenance": proof["receipt_id"], "operation_key": request.headers["idempotency-key"],
                "deployment_hash": proof["x-deployment-hash"]}

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
            if previous["kind"] != "decision-audit" or previous["arguments"] != arguments:
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
