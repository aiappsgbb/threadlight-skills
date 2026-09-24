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
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from pydantic import Field, ValidationError, model_validator

from govern_control_plane.auth import EntraAuth, Settings, Unauthorized
from govern_control_plane.attestations import (
    EvidenceError, EvidenceGrant, EvidenceProvider, EvidenceRequirement, SourceReference,
    VerificationResult, evidence_content,
)
from govern_control_plane.models import (
    Digest, Identifier, ObjectId, StrictModel, canonical, parse, strict_json,
)
from govern_gateway.citadel import Binding


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


def purchase_evidence(case, arguments):
    """The operator-controlled purchase snapshot is corroboration, not an uploaded receipt."""
    purchase = case.get("purchase")
    if (case.get("id") != arguments["case_id"] or case.get("_etag") != arguments["expected_etag"]
            or case.get("kind") != "case" or case.get("case_id") != case.get("id")
            or not isinstance(purchase, dict)
            or set(purchase) != {"reference", "revision", "customer_id", "amount", "currency"}
            or not isinstance(case.get("customer_id"), str)
            or type(case.get("amount")) is not int or case["amount"] < 0
            or type(purchase.get("amount")) is not int
            or purchase["customer_id"] != case["customer_id"] or purchase["amount"] != case["amount"]
            or purchase.get("currency") != case.get("currency") or not case.get("currency")
            or type(case.get("defect_declared", False)) is not bool):
        return None
    return VerificationResult(
        subject=case["customer_id"], case_id=case["id"], revision=case["_etag"],
        sources=[SourceReference(
            reference=purchase["reference"], revision=purchase["revision"], digest=digest(purchase))],
        claims={"purchase_verified": True, "amount": case["amount"],
                "defect_declared": case.get("defect_declared", False)})


class PurchaseAdapter:
    def __init__(self, container):
        self.container = container

    async def verify(self, identity, arguments):
        args = Decision.model_validate(arguments)
        case = await self.container.read_item(args.case_id, partition_key=args.case_id)
        return purchase_evidence(case, arguments)


def verify_purchase_binding(case, arguments, *, requirement, facts, expected_fingerprint):
    result = purchase_evidence(case, arguments)
    if (result is None or result.subject != requirement.subjects.get(case["id"])
            or digest(evidence_content(requirement, facts, arguments, result)) != expected_fingerprint):
        raise BusinessConflict()


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
    evidence_requirement: EvidenceRequirement | None = Field(
        default=None, exclude_if=lambda value: value is None)
    evidence_container: Identifier | None = None
    evidence_retention_seconds: Annotated[int, Field(gt=0, le=2147483647)] | None = None
    business_retention_policy: Identifier | None = None
    citadel_binding: Binding | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def explicit_evidence(cls, value):
        if isinstance(value, dict) and "evidence_requirement" in value and value["evidence_requirement"] is None:
            raise ValueError("evidence_configuration_required")
        if isinstance(value, dict) and "citadel_binding" in value and value["citadel_binding"] is None:
            raise ValueError("citadel_configuration_required")
        return value

    @model_validator(mode="after")
    def evidence_scope(self):
        if self.citadel_binding is not None:
            b = self.citadel_binding
            action = b.registry().actions[0]
            if (b.tenant_id != self.tenant_id or b.consumer.principal != self.agent_subject
                    or self.agent_subject not in self.workloads or self.writer_subject not in self.workloads
                    or self.workloads[self.agent_subject].client_id != b.consumer.client
                    or self.writer_subject in (b.proxy.principal, b.consumer.principal)
                    or self.workloads[self.writer_subject].client_id in (b.proxy.client, b.consumer.client)
                    or b.deployment.model_dump(mode="json") != self.deployment
                    or action.name != "returns_apply_decision" or action.scope != "returns"
                    or action.evidence_requirement != self.evidence_requirement):
                raise ValueError("citadel_producer_binding_mismatch")
        if self.evidence_requirement is not None:
            requirement = self.evidence_requirement
            if (requirement.profile != "returns-purchase-v1" or requirement.purpose != "record-return"
                    or requirement.case_field != "case_id" or requirement.revision_field != "expected_etag"
                    or set(requirement.subjects) != set(self.cases)
                    or self.key_id not in {key.kid for key in requirement.keys}
                    or not self.evidence_container or not self.evidence_retention_seconds
                    or not self.business_retention_policy
                    or self.evidence_container in (self.cosmos_container, self.read_audit_container)):
                raise ValueError("returns_evidence_configuration_required")
        elif any(value is not None for value in (
                self.evidence_container, self.evidence_retention_seconds, self.business_retention_policy)):
            raise ValueError("returns_evidence_opt_in_required")
        return self


def operation_facts(config):
    facts = {
        "tenant": config.tenant_id, "subject": config.agent_subject,
        "client": config.workloads[config.agent_subject].client_id,
        "action": "returns_apply_decision", "scope": "returns",
        "policy": config.policy_digest, "deployment": config.deployment,
    }
    if config.citadel_binding is not None:
        facts["citadel"] = config.citadel_binding.provenance()
    return facts


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
            if config.evidence_requirement is not None:
                from azure.keyvault.keys.aio import KeyClient
                from azure.keyvault.keys.crypto.aio import CryptographyClient
                from govern_control_plane.storage import KeyVaultSigner
                crypto = await stack.enter_async_context(CryptographyClient(
                    config.key_id, credential, retry_total=0))
                keys = await stack.enter_async_context(KeyClient(
                    config.key_id.split("/keys/")[0], credential, retry_total=0))
                signer = KeyVaultSigner(crypto, key_client=keys)
                await signer.health()
                evidence_transport = CosmosEffectTransport(endpoint=config.cosmos_url)
                evidence_store = await evidence_transport.connect(
                    stack=stack, credential=credential, database=config.cosmos_database,
                    container=config.evidence_container)
                properties = await evidence_store.read()
                if (properties.get("partitionKey", {}).get("paths") != ["/scope"]
                        or properties.get("defaultTtl") != config.evidence_retention_seconds):
                    raise ValueError("explicit_business_evidence_retention_required")
                state.update(evidence_store=evidence_store, evidence_transport=evidence_transport,
                    evidence=EvidenceProvider(
                        requirement=config.evidence_requirement, action="returns_apply_decision",
                        tenant=config.tenant_id, signer=signer, key_id=config.key_id,
                        adapter=PurchaseAdapter(container),
                        grants=[EvidenceGrant(
                            principal=config.agent_subject,
                            client=config.workloads[config.agent_subject].client_id,
                            case_id=case_id, subject=subject)
                            for case_id, subject in config.evidence_requirement.subjects.items()]))
            if config.read_audit_container is not None:
                audit_transport = CosmosEffectTransport(endpoint=config.cosmos_url)
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
        if config.citadel_binding is not None:
            b = config.citadel_binding
            expected.update({
                "x-citadel-binding": digest(b),
                "x-citadel-producer-policy": b.producer.policy.digest,
                "x-citadel-consumer-policy": b.consumer.policy.digest,
            })
        elif any(key.lower().startswith("x-citadel-") for key in request.headers):
            raise BusinessConflict()
        if any(request.headers.get(key) != value for key, value in expected.items()):
            raise BusinessConflict()
        key = parse(Identifier, canonical(request.headers.get("idempotency-key")))
        result = {
            **expected,
            "receipt_id": parse(Identifier, canonical(request.headers.get("x-governance-provenance"))),
            "action_hash": parse(Digest, canonical(request.headers.get("x-action-hash"))),
        }
        if config.evidence_requirement is not None:
            result["evidence_fingerprint"] = parse(
                Digest, canonical(request.headers.get("x-evidence-fingerprint")))
        elif request.headers.get("x-evidence-fingerprint") is not None:
            raise BusinessConflict()
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

    @app.exception_handler(EvidenceError)
    async def evidence_error(request, error):
        return JSONResponse({"status": "unavailable", "reason_code": str(error)}, status_code=503)

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
        return {"receipt_id": operation, "result": record["result"]}

    if config.evidence_requirement is not None:
        @app.post("/evidence/purchase")
        async def verify_purchase(request: Request):
            identity = await authenticated(request)
            body = bytearray()
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 16384:
                        return JSONResponse({"error": "request_too_large"}, status_code=413)
                try:
                    arguments = Decision.model_validate(strict_json(bytes(body))).model_dump()
                except ValueError:
                    return JSONResponse({"error": "invalid_request"}, status_code=422)
                result = await state["evidence"].issue(identity, arguments)
                # Business verification retention is independent of token validity.
                from govern_control_plane.attestations import verify_attestation
                facts = {"tenant": identity.tenant, "subject": identity.subject,
                         "client": identity.client, "action": "returns_apply_decision"}
                verified = (verify_attestation(result["attestation"], config.evidence_requirement,
                                               facts, arguments)
                            if result["status"] == "verified" else None)
                document = {
                    "id": "evidence-" + uuid.uuid4().hex, "scope": config.tenant_id + ":" + identity.subject,
                    "body": {
                        "kind": "evidence-verification", "profile": config.evidence_requirement.profile,
                        "case_id": arguments["case_id"], "revision": arguments["expected_etag"],
                        "status": result["status"], "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "retention_policy": config.business_retention_policy,
                        **({"fingerprint": verified.fingerprint, "sources": verified.safe["sources"],
                            "claims": verified.safe["claims"]} if verified else {}),
                    },
                }
                async def reauthorize():
                    await authenticated(request)
                    if verified:
                        verified.fresh()
                with state["evidence_transport"].append_evidence(
                        reauthorize, state["evidence_store"], document["scope"], document):
                    await reauthorize()
                    await state["evidence_store"].execute_item_batch(
                        batch_operations=[("create", (document,), {})], partition_key=document["scope"])
                await reauthorize()
                return result

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
        facts = operation_facts(config)
        if config.evidence_requirement is not None:
            facts["evidence_fingerprint"] = proof["evidence_fingerprint"]
        if digest({"facts": facts, "arguments": arguments}) != proof["action_hash"]:
            raise BusinessConflict()
        previous = await existing(operation, proof)
        if previous is not None:
            if previous["arguments"] != arguments:
                raise BusinessConflict()
            return {"receipt_id": operation, "result": previous["result"]}
        container = state["container"]
        case = await container.read_item(args.case_id, partition_key=args.case_id)
        if config.evidence_requirement is not None:
            verify_purchase_binding(
                case, arguments, requirement=config.evidence_requirement, facts=facts,
                expected_fingerprint=proof["evidence_fingerprint"])
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
