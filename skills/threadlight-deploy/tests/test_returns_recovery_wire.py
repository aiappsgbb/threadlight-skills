"""Actual Azure Cosmos serialization and FastAPI auth, external services only doubled."""
import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import AsyncHttpResponse, AsyncHttpTransport
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/threadlight-govern/tests"))
from test_control_plane import Harness, TENANT, WORKLOAD, OTHER, APP

pytestmark = pytest.mark.governance_runtime


class Reply(AsyncHttpResponse):
    def __init__(self, request, body, status=200):
        super().__init__(request, None)
        self.status_code, self.reason = status, "external-service-fixture"
        self.headers = {"Content-Type": "application/json", "x-ms-activity-id": "local"}
        self.content_type = "application/json"
        self.raw = json.dumps(body).encode()

    def body(self):
        return self.raw

    async def load_body(self):
        pass


class CosmosHTTP(AsyncHttpTransport):
    def __init__(self):
        self.docs = {("RMA-1", "RMA-1"): {
            "id": "RMA-1", "case_id": "RMA-1", "kind": "case", "_etag": '"1"',
            "status": "in_triage", "amount": 10, "eligible": True, "high_risk": False}}
        self.batches, self.effects = [], 0
        self.lose_ack = False

    async def open(self): pass
    async def close(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass

    async def send(self, request, **kwargs):
        path = urlsplit(request.url).path.rstrip("/")
        if request.method == "GET":
            if not path:
                return Reply(request, {"id": "test", "_rid": "test", "readableLocations": [],
                                      "writableLocations": [],
                                      "userConsistencyPolicy": {"defaultConsistencyLevel": "Strong"}})
            if "/docs/" in path:
                partition = json.loads(request.headers["x-ms-documentdb-partitionkey"])[0]
                record = self.docs.get((partition, path.rsplit("/", 1)[1]))
                return Reply(request, record or {"code": "NotFound"}, 200 if record else 404)
            return Reply(request, {"id": "cases", "_rid": "YQ==", "_self": "dbs/test/colls/cases/",
                                  "partitionKey": {"paths": ["/case_id"], "kind": "Hash", "version": 2}})
        assert request.method == "POST"
        assert request.headers["x-ms-cosmos-is-batch-request"] == "True"
        partition = json.loads(request.headers["x-ms-documentdb-partitionkey"])[0]
        operations = json.loads(request.body)
        self.batches.append(operations)
        staged = deepcopy(self.docs)
        for i, op in enumerate(operations):
            record = op["resourceBody"]
            key = (partition, record["id"])
            conflict = ((op["operationType"] == "Create" and key in staged)
                        or (op["operationType"] == "Replace"
                            and staged.get(key, {}).get("_etag") != op["ifMatch"]))
            if conflict:
                return Reply(request, [{"statusCode": 409 if j == i else 424}
                                       for j in range(len(operations))])
            staged[key] = {**record, "_etag": '"2"'}
        self.docs = staged
        self.effects += sum(op["resourceBody"].get("kind") == "decision-audit" for op in operations)
        if self.lose_ack:
            self.lose_ack = False
            from azure.core.exceptions import ServiceResponseError
            raise ServiceResponseError("test lost durable ACK")
        return Reply(request, [{"statusCode": 201, "resourceBody": op["resourceBody"]} for op in operations])


def load_backend():
    path = ROOT / "skills/threadlight-deploy/references/governance/returns_mcp_backend.py"
    spec = importlib.util.spec_from_file_location("returns_recovery_backend", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", ["fence-first", "effect-first", "lost-fence-ack", "wrong-provenance",
                                 "wrong-arguments", "agent-caller"])
def test_real_cosmos_fence_blocks_late_original_writer(case):
    module = load_backend()
    async def check():
        authority = Harness()
        wire = CosmosHTTP()
        sys.path.insert(0, str(ROOT / "examples/returns-triage-governed/src/agent"))
        sys.path.insert(0, str(ROOT / "skills/threadlight-govern/references"))
        from cosmos_effect import CosmosEffectTransport
        deployment = {"agent_id": "agent-1", "agent_version": "1"}
        cfg = module.Configuration.model_validate_json(json.dumps({
            **authority.settings.model_dump(mode="json"), "cosmos_url": "https://fixture.documents.azure.com:443/",
            "cosmos_database": "test", "cosmos_container": "cases", "service_client_id": APP,
            "writer_subject": OTHER, "agent_subject": WORKLOAD, "cases": ["RMA-1"],
            "policy_digest": "sha256:" + "a" * 64, "deployment": deployment, "recovery_enabled": True}))
        class Credential:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get_token(self, *args, **kwargs): return AccessToken("test-cosmos-only", 4102444800)
        app = module.create_app(
            configuration=cfg, credential_factory=lambda **kw: Credential(),
            transport_factory=lambda **kw: CosmosEffectTransport(wire, **kw),
            auth_transport=httpx.MockTransport(lambda req: httpx.Response(200, json=authority.jwks)))
        arguments = {"case_id": "RMA-1", "expected_etag": '"1"',
                     "decision": "approve_refund", "reason": "Eligible; no settlement."}
        facts = {"tenant": TENANT, "subject": WORKLOAD, "client": APP, "action": "returns_apply_decision",
                 "scope": "returns", "policy": cfg.policy_digest, "deployment": deployment}
        headers = {
            "Authorization": "Bearer " + authority.token(changes={"oid": OTHER}),
            "Idempotency-Key": "original-operation", "X-Tenant-ID": TENANT, "X-Requester-ID": WORKLOAD,
            "X-Action-ID": "returns_apply_decision", "X-Policy-Digest": cfg.policy_digest,
            "X-Deployment-Hash": module.digest(deployment), "X-Governance-Provenance": "central-1",
            "X-Action-Hash": module.digest({"facts": facts, "arguments": arguments}),
        }
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="https://business.example") as client:
                if case == "effect-first":
                    assert (await client.post("/decisions", headers=headers, json=arguments)).status_code == 200
                wire.lose_ack = case == "lost-fence-ack"
                changed = dict(headers)
                args = dict(arguments)
                if case == "wrong-provenance":
                    changed["X-Deployment-Hash"] = "sha256:" + "0" * 64
                elif case == "wrong-arguments":
                    args["reason"] = "changed"
                elif case == "agent-caller":
                    changed["Authorization"] = "Bearer " + authority.token()
                response = await client.post("/recovery", headers=changed, json=args)
                if case in {"wrong-provenance", "wrong-arguments", "agent-caller"}:
                    assert response.status_code in (401, 409)
                    assert not wire.batches and wire.effects == 0
                    return
                if case == "lost-fence-ack":
                    assert response.status_code == 503
                    response = await client.post("/recovery", headers=headers, json=arguments)
                assert response.status_code == 200, response.text
                assert response.json()["state"] == ("completed" if case == "effect-first" else "not_executed")
                before = deepcopy(wire.docs)
                late = await client.post("/decisions", headers=headers, json=arguments)
                assert late.status_code == (200 if case == "effect-first" else 409)
                assert wire.docs == before
                assert wire.effects == int(case == "effect-first")
                # Simulate a writer that already read the case before the fence.
                if case != "effect-first":
                    operation = response.json()["receipt_id"]
                    original_case = {"id": "RMA-1", "case_id": "RMA-1", "kind": "case",
                                     "_etag": '"1"', "status": "in_triage", "amount": 10,
                                     "eligible": True, "high_risk": False}
                    operations, _ = module.decision_batch(
                        original_case, arguments, operation_id=operation, provenance={})
                    from azure.cosmos.exceptions import CosmosBatchOperationError
                    async with AsyncExitStack() as stack:
                        transport = CosmosEffectTransport(wire, endpoint=cfg.cosmos_url)
                        container = await transport.connect(
                            stack=stack, credential=Credential(), database="test", container="cases")
                        with transport.batch(lambda: None, container, "RMA-1", operations):
                            with pytest.raises(CosmosBatchOperationError):
                                await container.execute_item_batch(
                                    batch_operations=operations, partition_key="RMA-1")
                    assert wire.docs == before and wire.effects == 0
        await authority.close()
    asyncio.run(check())
