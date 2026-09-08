"""Native canonical agent, real ACS/OPA and external-service-only fault injection."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys
from urllib.parse import urlparse

import pytest
import yaml

from test_governance_runtime import EXAMPLE, ROOT, RMA, application, sequence
from local_probe import MemoryCosmos
from azure.core.pipeline.transport import AsyncHttpTransport, AsyncHttpResponse

pytestmark = pytest.mark.governance_runtime


def native_case(tmp_path, monkeypatch, store, *, lifetime=60, decision="approve_refund"):
    import runtime
    from test_policy_bundle import bundle_module
    from test_runtime_provider import native_model_client
    from test_governance_wiring import module
    from skills._shared.governance import validate_governance_contract

    app = application(store)
    if decision == "request_more_info":
        app.backend.customers.pop(store.docs[RMA]["customer_id"])
    bundle = bundle_module().build_bundle(
        source=EXAMPLE / "src/agent/governance/policy", destination=tmp_path / "policy",
        policy_id="returns-write-v1", version="1")
    expiry = datetime.now(timezone.utc) + timedelta(seconds=lifetime)
    receipts = []

    class Authority:
        def verify(self, _):
            return runtime.VerifiedPolicy(bundle.bundle_digest, expiry)

    class Audit:
        def append(self, **fields):
            receipts.append(fields)
            return "durable-test-ack"

    definition = yaml.safe_load((EXAMPLE / "agent.yaml").read_text())
    provider = runtime.AcsGovernanceProvider(
        contract={k: definition[k] for k in ("framework", "governance", "tools")},
        bundle_path=bundle.root, expected_digest=bundle.bundle_digest,
        bundle_verifier=bundle_module().verify_bundle, contract_validator=validate_governance_contract,
        signature_verifier=Authority(), audit=Audit(), safe_provider=app.safe_evidence,
        trusted_context_provider=app.trusted_context, principal="trusted-workload",
        tenant="trusted-tenant", agent_version="local-test", image_digest="sha256:" + "a" * 64)
    provider.deployment_agent_id = "returns-triage"
    monkeypatch.setitem(sys.modules, "governance_application", app)
    monkeypatch.syspath_prepend(str(ROOT / "skills/threadlight-deploy/references/governance"))
    host_module = module("maf-container")
    host_module.BASE = EXAMPLE / "src/agent"
    client = native_model_client(sequence(decision=decision))
    host = host_module.build_host(provider, client=client, configure_observability=None)
    return app, provider, host, client, receipts, expiry


async def run_case(host, client):
    from runtime import GovernedToolUnavailable
    try:
        await host._agent.run("triage")
    except GovernedToolUnavailable:
        pass
    prompt = (EXAMPLE / "src/agent/copilot-instructions.md").read_text()
    assert client.requests and all(prompt in body.get("instructions", "")
                                   for _, body in client.requests)


async def past(expiry):
    await asyncio.sleep(max(0, (expiry - datetime.now(timezone.utc)).total_seconds()) + 0.05)


@pytest.mark.parametrize("decision", ["approve_refund", "request_more_info"])
def test_policy_expiry_during_final_cosmos_read_blocks_transaction(tmp_path, monkeypatch, decision):
    class DelayedCosmos(MemoryCosmos):
        async def read_item(self, **kwargs):
            result = await super().read_item(**kwargs)
            if len(self.reads) == 3:
                await past(expiry)
            return result

    store = DelayedCosmos()
    app, provider, host, client, receipts, expiry = native_case(
        tmp_path, monkeypatch, store, lifetime=4, decision=decision)
    asyncio.run(run_case(host, client))
    assert len(store.reads) == 3
    assert receipts[0]["decision"] == "allow", "ACS allowed before the delayed final read"
    assert store.decisions == [], "expired policy reached the actual Cosmos transaction"
    assert any(r["reason_code"] == "threadlight:policy_unavailable" for r in receipts)
    assert not any("rationale" in r for r in receipts), "failure receipt must remain sanitized"


def test_evidence_lease_expiry_at_final_read_is_audited(tmp_path, monkeypatch):
    class DelayedCosmos(MemoryCosmos):
        async def read_item(self, **kwargs):
            result = await super().read_item(**kwargs)
            if len(self.reads) == 3:
                await asyncio.sleep(20.1)
            return result
    store = DelayedCosmos()
    app, provider, host, client, receipts, expiry = native_case(tmp_path, monkeypatch, store)
    asyncio.run(run_case(host, client))
    assert len(store.reads) == 3 and not store.decisions
    assert any(r["reason_code"] == "threadlight:trusted_context_expired" for r in receipts)
    assert expiry > datetime.now(timezone.utc)


class CosmosResponse(AsyncHttpResponse):
    def __init__(self, request, body, status=200, headers=None):
        super().__init__(request, None)
        self.status_code, self.reason = status, "external-service-fixture"
        self.headers = {"Content-Type": "application/json", "x-ms-activity-id": "fixture",
                        **(headers or {})}
        self.content_type = "application/json"
        self._body = json.dumps(body).encode()

    def body(self):
        return self._body

    async def load_body(self):
        pass


class CosmosHTTP(AsyncHttpTransport):
    """Only the HTTP service is fake; SDK serialization/auth/retry are real."""
    def __init__(self, store):
        self.store = store
        self.requests, self.batches = [], []
        self.before_open = None
        self.retry = False
        self.uncertain = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def open(self):
        if self.before_open:
            before, self.before_open = self.before_open, None
            await before()

    async def close(self):
        pass

    async def send(self, request, **kwargs):
        self.requests.append(request)
        assert request.headers["authorization"].startswith("type=aad&ver=1.0&sig=")
        path = urlparse(request.url).path.rstrip("/")
        if request.method == "GET":
            if not path:
                body = {"id": "fixture", "_rid": "fixture", "readableLocations": [],
                        "writableLocations": [], "userConsistencyPolicy": {"defaultConsistencyLevel": "Session"}}
            elif "/docs/" in path:
                body = await self.store.read_item(item=path.rsplit("/", 1)[1],
                                                   partition_key=json.loads(request.headers[
                                                       "x-ms-documentdb-partitionkey"])[0])
            else:
                body = {"id": "cases", "_rid": "YQ==", "_self": "dbs/test/colls/cases/",
                        "partitionKey": {"paths": ["/case_id"], "kind": "Hash", "version": 2}}
            return CosmosResponse(request, body)
        assert request.method == "POST" and path == "/dbs/test/colls/cases/docs"
        assert request.headers["x-ms-cosmos-is-batch-request"] == "True"
        body = json.loads(request.body)
        self.batches.append(body)
        if self.retry:
            self.retry = False
            return CosmosResponse(request, {"code": "TooManyRequests"}, 429,
                                  {"x-ms-retry-after-ms": "4100"})
        operations = [("replace", (body[0]["id"], body[0]["resourceBody"]),
                       {"if_match_etag": body[0]["ifMatch"]}),
                      ("create", (body[1]["resourceBody"],), {})]
        try:
            results = await self.store.execute_item_batch(
                operations, json.loads(request.headers["x-ms-documentdb-partitionkey"])[0])
        except RuntimeError:
            return CosmosResponse(request, [{"statusCode": 412}, {"statusCode": 424}])
        if self.uncertain:
            from azure.core.exceptions import ServiceResponseError
            self.uncertain = False
            raise ServiceResponseError("external service lost completion")
        return CosmosResponse(request, results)


@pytest.mark.parametrize("fault", ["valid", "credential", "credential-fallback", "retry", "transport-open",
                                  "wire-body", "wire-partition", "wire-etag", "wire-path", "wire-host", "wire-query",
                                  "uncertain",
                                  "late-retry", "unsupported", "mismatched"])
def test_real_cosmos_http_postauth_terminal_boundary(tmp_path, monkeypatch, fault):
    from contextlib import AsyncExitStack
    from azure.core.credentials import AccessToken
    from azure.core.exceptions import HttpResponseError
    store = MemoryCosmos()
    app, provider, host, client, receipts, expiry = native_case(
        tmp_path, monkeypatch, store,
        lifetime=4 if fault in {"credential", "credential-fallback", "retry", "transport-open"} else 60)
    service = CosmosHTTP(store)
    service.retry = fault == "retry"
    service.uncertain = fault == "uncertain"
    token_calls = []

    class Credential:
        async def get_token(self, *args, **kwargs):
            token_calls.append(args)
            if len(store.reads) == 3 and fault == "transport-open":
                service.before_open = lambda: past(expiry)
            if len(store.reads) == 3 and fault.startswith("credential"):
                await past(expiry)
                if fault == "credential-fallback" and len(token_calls) == 5:
                    raise HttpResponseError("AADSTS500011 external scope unavailable")
            # Force actual SDK re-acquisition, including the batch request.
            return AccessToken("external-token", 1)

    from cosmos_effect import CosmosEffectTransport
    transport = CosmosEffectTransport(service, endpoint="https://fixture.documents.azure.com")
    app.backend.effect_transport = transport
    if fault == "unsupported":
        app.backend.effect_transport = None
    if fault == "mismatched":
        app.backend.effect_transport = CosmosEffectTransport(
            service, endpoint="https://fixture.documents.azure.com")
    late_tasks, late_denials = [], []
    release = asyncio.Event()

    def wire_fault(pipeline_request):
        if fault == "late-retry" and pipeline_request.http_request.method == "POST":
            async def late_send():
                from runtime import GovernedToolUnavailable
                await release.wait()
                try:
                    await transport.send(pipeline_request.http_request)
                except GovernedToolUnavailable:
                    late_denials.append(True)
            late_tasks.append(asyncio.create_task(late_send()))
        if (len(store.reads) != 3 or not fault.startswith("wire-")
                or pipeline_request.http_request.method != "POST"):
            return
        request = pipeline_request.http_request
        if fault == "wire-path":
            request.url += "-other"
        elif fault == "wire-host":
            request.url = request.url.replace("fixture.documents.azure.com", "other.documents.azure.com")
        elif fault == "wire-query":
            request.headers["x-ms-documentdb-isquery"] = "True"
        elif fault == "wire-partition":
            request.headers["x-ms-documentdb-partitionkey"] = '["another-case"]'
        else:
            body = json.loads(request.body)
            if fault == "wire-etag":
                body[0].pop("ifMatch")
            else:
                body[1]["resourceBody"]["decision"] = "deny_refund"
            request.set_json_body(body)

    async def scenario():
        async with AsyncExitStack() as stack:
            app.backend.container = await transport.connect(
                stack=stack, credential=Credential(), database="test", container="cases",
                raw_request_hook=wire_fault)
            await run_case(host, client)
            release.set()
            await asyncio.gather(*late_tasks)
            # A rejected selected write must not suppress ordinary reads.
            assert (await app.backend.get_case(RMA))["id"] == RMA
    asyncio.run(scenario())
    assert receipts[0]["decision"] == "allow"
    assert len(token_calls) >= (5 if fault in {"unsupported", "mismatched"} else 6)
    assert len(service.batches) == (1 if fault in {"valid", "retry", "uncertain", "late-retry"} else 0)
    assert len(store.decisions) == int(fault in {"valid", "uncertain", "late-retry"})
    if fault.startswith("wire-") or fault in {"unsupported", "mismatched"}:
        assert any(r["reason_code"] == "threadlight:effect_unavailable" for r in receipts)
    elif fault == "late-retry":
        assert late_denials == [True]
        assert any(r["reason_code"] == "threadlight:effect_authorization_unavailable" for r in receipts)
    elif fault not in {"valid", "uncertain"}:
        assert any(r["reason_code"] == "threadlight:policy_unavailable" for r in receipts)
