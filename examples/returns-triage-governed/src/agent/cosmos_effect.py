"""Selected batch guard at Azure Core's public, post-auth transport boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
import inspect
import json
from urllib.parse import unquote, urlsplit

from azure.core.pipeline.transport import AioHttpTransport, AsyncHttpTransport
from runtime import reject_effect
from runtime.evidence import digest


class CosmosEffectTransport(AsyncHttpTransport):
    def __init__(self, transport=None, *, endpoint):
        self._transport = transport if transport is not None else AioHttpTransport()
        self._traced = False
        self._batch = ContextVar("returns_cosmos_batch", default=None)
        address = urlsplit(endpoint)
        if (address.scheme != "https" or not address.hostname or address.username or address.password
                or address.path not in {"", "/"} or address.query or address.fragment):
            raise ValueError("unsupported_cosmos_effect_endpoint")
        self._origin = (address.scheme, address.hostname, address.port or 443)
        self._endpoint = endpoint
        self._containers = []

    async def connect(self, *, stack, credential, database, container, raw_request_hook=None):
        """Own the public client construction; no private pipeline introspection."""
        from azure.cosmos.aio import CosmosClient
        client = await stack.enter_async_context(CosmosClient(
            self._endpoint, credential=credential, transport=self,
            enable_endpoint_discovery=False, raw_request_hook=raw_request_hook))
        result = client.get_database_client(database).get_container_client(container)
        self._containers.append(result)
        return result

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def open(self):
        if isinstance(self._transport, AioHttpTransport) and not self._traced:
            if self._transport.session is not None:
                raise ValueError("cosmos_effect_requires_owned_trace_session")
            import aiohttp
            trace = aiohttp.TraceConfig()
            async def before_headers(session, context, params):
                state = self._batch.get()
                if state is not None and params.headers.get("x-ms-cosmos-is-batch-request") == "True":
                    await self._authorize(state)
                    self._wire(state, params.method, str(params.url), params.headers, state["wire_body"])
            async def before_chunk(session, context, params):
                state = self._batch.get()
                if state is not None and params.method == "POST" and state.get("wire_body") is not None:
                    await self._authorize(state)
                    if digest(json.loads(params.chunk)) != state["body"]:
                        reject_effect()
            trace.on_request_headers_sent.append(before_headers)
            trace.on_request_chunk_sent.append(before_chunk)
            self._transport.session = aiohttp.ClientSession(
                trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False, trace_configs=[trace])
            self._traced = True
        await self._transport.open()

    async def close(self):
        await self._transport.close()

    async def sleep(self, duration):
        await self._transport.sleep(duration)

    async def _authorize(self, state):
        checked = state["check"]()
        if inspect.isawaitable(checked):
            await checked

    def _wire(self, state, method, url, headers, body):
        valid = False
        try:
            address = urlsplit(url)
            valid = (
                state["active"] and method == "POST"
                and (address.scheme, address.hostname, address.port or 443) == self._origin
                and not address.username and not address.password and not address.query and not address.fragment
                and unquote(address.path).rstrip("/") == state["path"]
                and headers.get("x-ms-cosmos-is-batch-request") == "True"
                and headers.get("x-ms-documentdb-isquery") != "True"
                and headers.get("x-ms-cosmos-batch-atomic") == "True"
                and headers.get("x-ms-cosmos-batch-continue-on-error") == "False"
                and digest(json.loads(headers["x-ms-documentdb-partitionkey"])) == state["partition"]
                and digest(json.loads(body)) == state["body"])
        except (TypeError, ValueError, KeyError):
            pass
        if not valid:
            reject_effect()

    @contextmanager
    def batch(self, check, container, partition_key, operations):
        if not any(container is owned for owned in self._containers):
            reject_effect()
        # Bind the complete semantic wire target, including CAS and atomic audit.
        # No SDK private formatter: only this adapter's replace/create protocol.
        replace, create = operations
        expected = [
            {"operationType": "Replace", "id": replace[1][0], "resourceBody": replace[1][1],
             "ifMatch": replace[2]["if_match_etag"]},
            {"operationType": "Create", "resourceBody": create[1][0]},
        ]
        with self._guarded_batch(check, container, partition_key, expected):
            yield

    @contextmanager
    def recovery_fence(self, check, container, partition_key, document):
        if (not isinstance(document, dict) or document.get("kind") != "no-effect-fence"
                or document.get("case_id") != partition_key
                or not isinstance(document.get("id"), str)
                or not document["id"].startswith("decision-")
                or not isinstance(document.get("provenance"), dict)
                or not isinstance(document.get("arguments"), dict)):
            reject_effect()
        with self._guarded_batch(
                check, container, partition_key, [{"operationType": "Create", "resourceBody": document}]):
            yield

    @contextmanager
    def append_audit(self, check, container, partition_key, document):
        if (not isinstance(document, dict) or set(document) != {"id", "scope", "body"}
                or not isinstance(document["id"], str) or not document["id"].startswith("read-")
                or document["scope"] != partition_key or not isinstance(document["body"], dict)
                or document["body"].get("kind") != "case-read"
                or document["body"].get("action_id") != "returns_get_case"
                or document["body"].get("policy_binding") != "none"):
            reject_effect()
        with self._guarded_batch(
                check, container, partition_key, [{"operationType": "Create", "resourceBody": document}]):
            yield

    @contextmanager
    def append_evidence(self, check, container, partition_key, document):
        if (not isinstance(document, dict) or set(document) != {"id", "scope", "body"}
                or not isinstance(document["id"], str) or not document["id"].startswith("evidence-")
                or document["scope"] != partition_key or not isinstance(document["body"], dict)
                or document["body"].get("kind") != "evidence-verification"
                or set(document["body"]) - {"kind", "profile", "case_id", "revision", "status",
                                           "recorded_at", "retention_policy", "fingerprint", "sources", "claims"}):
            reject_effect()
        with self._guarded_batch(
                check, container, partition_key, [{"operationType": "Create", "resourceBody": document}]):
            yield

    @contextmanager
    def _guarded_batch(self, check, container, partition_key, expected):
        if not any(container is owned for owned in self._containers):
            reject_effect()
        state = {"check": check, "body": digest(expected), "partition": digest([partition_key]),
                 "path": "/" + container.container_link.strip("/") + "/docs", "active": True}
        token = self._batch.set(state)
        try:
            yield
        finally:
            state["active"] = False
            self._batch.reset(token)

    async def send(self, request, **kwargs):
        # Credential acquisition and SDK retry/scheduling already completed.
        # Pre-open the delegate before checking, so transport setup cannot yield
        # between authorization and handing off the request.
        await self.open()
        state = self._batch.get()
        query = request.headers.get("x-ms-documentdb-isquery") == "True"
        batch = request.headers.get("x-ms-cosmos-is-batch-request") == "True"
        read_only = not batch and (request.method == "GET" or request.method == "POST" and query)
        if state is not None and not read_only:
            await self._authorize(state)
            self._wire(state, request.method, request.url, request.headers, request.body)
            state["wire_body"] = request.body
            await self._authorize(state)
        return await self._transport.send(request, **kwargs)
