"""Fixed authenticated Task9 HTTP contract. The only effect is a durable noop count."""
from __future__ import annotations

import asyncio

from govern_control_plane.models import Digest, Nonce, ObjectId, canonical, parse, strict_json
from govern_control_plane.probes import (
    PROBE_INPUT, bounded_body, control_app, failure, hashed, request_identity,
)
from govern_gateway.dispatcher import digest, validated
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


def create_app(*, probes, auth, callers):
    if probes.producer != "fixture" or not callers:
        raise ValueError("fixture_authority_required")
    if set(callers).intersection(subject for action in probes.registry.actions for subject in action.workloads):
        raise ValueError("separate_fixture_caller_required")

    async def noop(request):
        try:
            async with asyncio.timeout(10):
                identity = await request_identity(request, auth)
                if (identity.tenant != probes.registry.tenant_id or identity.workload is None
                        or callers.get(identity.subject) != identity.client):
                    raise PermissionError()
                def header(name, model):
                    values = request.headers.getlist(name)
                    if len(values) != 1:
                        raise ValueError()
                    return parse(model, canonical(values[0]))
                tenant = header("x-tenant-id", ObjectId)
                subject = header("x-requester-id", ObjectId)
                client = header("x-requester-client", ObjectId)
                action = probes.action(request.headers.get("x-action-id"), subject)
                if tenant != probes.registry.tenant_id:
                    raise PermissionError()
                run = request.headers.get("x-probe-run-id")
                args = None
                if request.method == "POST":
                    args = validated(strict_json(await bounded_body(request)), PROBE_INPUT)
                    if run is not None and run != args["probe_run_id"]:
                        raise ValueError()
                    run = args["probe_run_id"]
                state, _ = await probes.load(subject, run)
                if args is None:
                    args = {"probe_run_id": run, "variant": state.registration.variant}
                facts = {"tenant": tenant, "subject": subject, "client": client,
                         "action": action.name, "scope": action.scope, "policy": probes.policy_digest,
                         "deployment": probes.registry.deployment.model_dump(mode="json")}
                action_hash = header("x-action-hash", Digest)
                provenance = header("x-governance-provenance", Nonce)
                key = header("idempotency-key", str)
                if (len(key) != 64 or any(c not in "0123456789abcdef" for c in key)
                        or header("x-policy-digest", Digest) != probes.policy_digest
                        or header("x-deployment-hash", Digest) != digest(facts["deployment"])
                        or action_hash != digest({"facts": facts, "arguments": args})):
                    raise ValueError()
                effect_key = digest([key, action_hash, provenance, identity.subject, client])
                if request.method == "POST":
                    state = await probes.effect(subject, run, variant=args["variant"],
                        effect_key=effect_key, action_hash=action_hash, receipt_id=provenance)
                elif (state.effect_key != effect_key or state.action_hash != action_hash
                      or state.terminal != "completed"):
                    raise ValueError()
                return JSONResponse({"receipt_id": hashed([run, effect_key])[:32], "result": {"status": "noop"}})
        except Exception as error:
            from govern_gateway.dispatcher import GateError
            return failure(ValueError() if isinstance(error, GateError) else error)

    return Starlette(routes=[
        Route("/governance/noop", noop, methods=["POST"]),
        Route("/governance/outcomes", noop, methods=["GET"]),
        Mount("/", app=control_app(probes, auth)),
    ])


def production_app():
    import os
    from contextlib import asynccontextmanager
    from govern_gateway.probe_runtime import open_runtime, read_configuration
    active = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal active
        path = os.environ.get("PROBE_CONFIG_FILE")
        if not path:
            raise ValueError("explicit_probe_configuration_required")
        config = read_configuration(path)
        if config.producer != "fixture":
            raise ValueError("fixture_configuration_required")
        async with open_runtime(config) as (service, auth, _):
            active = create_app(probes=service, auth=auth, callers=config.fixture_callers)
            yield
        active = None

    async def forward(scope, receive, send):
        await active(scope, receive, send)
    return Starlette(routes=[Mount("/", app=forward)], lifespan=lifespan)


def main():
    import uvicorn
    uvicorn.run(production_app(), host="0.0.0.0", port=8000, access_log=False, log_level="critical")
