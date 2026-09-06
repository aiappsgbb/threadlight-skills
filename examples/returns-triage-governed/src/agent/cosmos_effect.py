"""Selected batch guard at Azure Core's public, post-auth transport boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from urllib.parse import unquote, urlsplit

from azure.core.pipeline.transport import AioHttpTransport, AsyncHttpTransport
from runtime import reject_effect
from runtime.evidence import digest


class CosmosEffectTransport(AsyncHttpTransport):
    def __init__(self, transport=None, *, endpoint):
        self._transport = transport if transport is not None else AioHttpTransport()
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
        await self._transport.open()

    async def close(self):
        await self._transport.close()

    async def sleep(self, duration):
        await self._transport.sleep(duration)

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
        state = {"check": check, "body": digest(expected), "partition": digest([partition_key]),
                 "path": "/" + container.container_link.strip("/") + "/docs", "active": True}
        token = self._batch.set(state)
        try:
            check()
            yield
        finally:
            state["active"] = False
            self._batch.reset(token)

    async def send(self, request, **kwargs):
        # Credential acquisition and SDK retry/scheduling already completed.
        # Pre-open the delegate before checking, so transport setup cannot yield
        # between authorization and handing off the request.
        await self._transport.open()
        state = self._batch.get()
        query = request.headers.get("x-ms-documentdb-isquery") == "True"
        batch = request.headers.get("x-ms-cosmos-is-batch-request") == "True"
        read_only = not batch and (request.method == "GET" or request.method == "POST" and query)
        if state is not None and not read_only:
            state["check"]()
            valid = False
            try:
                address = urlsplit(request.url)
                valid = (
                    state["active"] and request.method == "POST" and batch and not query
                    and (address.scheme, address.hostname, address.port or 443) == self._origin
                    and not address.username and not address.password and not address.query and not address.fragment
                    and unquote(address.path).rstrip("/") == state["path"]
                    and request.headers.get("x-ms-cosmos-is-batch-request") == "True"
                    and request.headers.get("x-ms-cosmos-batch-atomic") == "True"
                    and request.headers.get("x-ms-cosmos-batch-continue-on-error") == "False"
                    and digest(json.loads(request.headers["x-ms-documentdb-partitionkey"])) == state["partition"]
                    and digest(json.loads(request.body)) == state["body"]
                )
            except (TypeError, ValueError, KeyError):
                pass
            if not valid:
                reject_effect()
            state["check"]()
        return await self._transport.send(request, **kwargs)
