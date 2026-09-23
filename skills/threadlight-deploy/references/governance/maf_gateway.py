"""Selected native MAF functions backed by the authenticated stateless MCP PEP."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import math
import re
import time
from urllib.parse import urlsplit
import uuid

from agent_framework import Agent, FunctionTool, SKIP_PARSING
from agent_framework.exceptions import ToolExecutionException
import httpcore
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import Tool as MCPTool
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator

from govern_control_plane.models import Identifier, canonical, parse, strict_json
from govern_control_plane.attestations import EVIDENCE_ARGUMENT, EVIDENCE_META
from govern_control_plane.review import validate_pending_review
from skills._shared.governance import validate_governance_contract
from skill_approval import RequireResolvedApprovals


class GatewayToolError(ToolExecutionException):
    pass


class BoundedResponse(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > 65536:
                raise GatewayToolError("threadlight:gateway_response_limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class AuthorizedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, guard):
        self.inner, self.guard = inner, guard

    @staticmethod
    def same_headers(expected, actual):
        if actual == expected:
            return True
        tracing = {b"traceparent", b"tracestate", b"baggage"}
        if (tuple((key, value) for key, value in actual if key.lower() not in tracing)
                != tuple((key, value) for key, value in expected if key.lower() not in tracing)):
            return False
        # Native HTTPX instrumentation injects its active span after this wrapper.
        # Accept only that exact W3C context, never arbitrary header mutations.
        current = {}
        TraceContextTextMapPropagator().inject(current)
        W3CBaggagePropagator().inject(current)
        observed = {}
        for key, value in actual:
            if key.lower() in tracing:
                try:
                    name, text = key.lower().decode("ascii"), value.decode("ascii")
                except UnicodeDecodeError:
                    return False
                if name in observed:
                    return False
                observed[name] = text
        return bool(current.get("traceparent")) and observed == current

    async def handle_async_request(self, request):
        method, url, headers = request.method, request.url, tuple(request.headers.raw)
        if type(request.stream) is not httpx.ByteStream:
            raise GatewayToolError("threadlight:gateway_transport_unsupported")
        body = b"".join(request.stream)
        if request.extensions.get("trace") is not None:
            raise GatewayToolError("threadlight:gateway_trace_unsupported")

        async def trace(event, info):
            if event.startswith("http2."):
                raise GatewayToolError("threadlight:gateway_transport_unsupported")
            if event in ("http11.send_request_headers.started", "http11.send_request_body.started"):
                await self.guard()
                core = info.get("request")
                changed = []
                if type(core) is not httpcore.Request:
                    changed.append("request_type")
                else:
                    if core.method != method.encode("ascii"):
                        changed.append("method")
                    if ((core.url.scheme, core.url.host, core.url.port, core.url.target)
                            != (url.raw_scheme, url.raw_host, url.port, url.raw_path)):
                        changed.append("target")
                    if not self.same_headers(headers, tuple(core.headers)):
                        before = {key.lower(): value for key, value in headers}
                        after = {key.lower(): value for key, value in core.headers}
                        names = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
                        known = {b"authorization", b"idempotency-key", b"host", b"content-type",
                                 b"content-length", b"traceparent", b"tracestate", b"baggage"}
                        changed.append("headers:" + ",".join(sorted(name.decode() for name in names & known)))
                        if names - known:
                            changed.append("other_headers")
                    if type(core.stream) is not httpx.ByteStream or b"".join(core.stream) != body:
                        changed.append("body")
                if changed:
                    raise GatewayToolError("threadlight:gateway_wire_changed:" + ";".join(changed))

        await self.guard()
        request.extensions["trace"] = trace
        response = await self.inner.handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise GatewayToolError("threadlight:gateway_encoding_unsupported")
        response.stream = BoundedResponse(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


class GovernedMCPTools:
    def __init__(self, *, url, scope, credential, authorize, selected_tools,
                 timeout=20.0, transport_factory=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path in ("", "/") or str(httpx.URL(url)) != url):
            raise ValueError("canonical_gateway_mcp_url_required")
        if not re.fullmatch(r"api://[A-Za-z0-9._/-]+/\.default", scope):
            raise ValueError("gateway_audience_required")
        if (not isinstance(selected_tools, list) or not 1 <= len(selected_tools) <= 64
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name)
                       for name in selected_tools)
                or len(set(selected_tools)) != len(selected_tools)):
            raise ValueError("explicit_unique_gateway_tools_required")
        if not callable(authorize) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("bounded_gateway_authorization_required")
        self.url, self.scope, self.credential = url, scope, credential
        self.authorize, self.timeout = authorize, timeout
        self.selected = tuple(selected_tools)
        self.transport_factory = transport_factory
        self._functions = None
        self._descriptors = {}
        self._deferred_descriptors = None
        self._connect_lock = asyncio.Lock()

    @property
    def functions(self):
        if self._functions is None:
            raise GatewayToolError("threadlight:gateway_not_connected")
        return list(self._functions)

    @asynccontextmanager
    async def _session(self, *, call=None):
        active = True
        token_expiry = None
        token_deadline = None

        async def guard():
            if not active:
                raise GatewayToolError("threadlight:gateway_call_closed")
            await self.authorize()
            if (not active or token_expiry is not None and time.time() >= token_expiry
                    or token_deadline is not None and time.monotonic() >= token_deadline):
                raise GatewayToolError("threadlight:gateway_authorization_expired")

        async def stamp(request):
            nonlocal token_expiry, token_deadline
            if request.method != "POST" or request.url != httpx.URL(self.url) or len(request.content) > 32768:
                raise GatewayToolError("threadlight:gateway_request_unsupported")
            document = strict_json(request.content)
            method = document.get("method")
            if method not in ("initialize", "notifications/initialized", "ping", "tools/list", "tools/call"):
                raise GatewayToolError("threadlight:gateway_method_unsupported")
            if method == "tools/call":
                params = document["params"]
                if (call is None or params.get("name") != call["name"]
                        or canonical(params.get("arguments", {})) != call["arguments"]
                        or params.get("_meta", {}).get(EVIDENCE_META) != call.get("evidence")):
                    raise GatewayToolError("threadlight:gateway_action_changed")
                request.headers["Idempotency-Key"] = call["key"]
            await guard()
            token = await self.credential.get_token(self.scope)
            if (isinstance(token.expires_on, bool) or not isinstance(token.expires_on, (int, float))
                    or not math.isfinite(token.expires_on)):
                raise GatewayToolError("threadlight:gateway_credential_invalid")
            # Refresh cannot lengthen any request already in this short-lived session.
            token_expiry = min(token_expiry, token.expires_on) if token_expiry is not None else token.expires_on
            deadline = time.monotonic() + max(0, token.expires_on - time.time())
            token_deadline = min(token_deadline, deadline) if token_deadline is not None else deadline
            await guard()
            request.headers["Authorization"] = "Bearer " + token.token
            request.headers["Accept-Encoding"] = "identity"

        inner = (self.transport_factory() if self.transport_factory is not None else
                 httpx.AsyncHTTPTransport(http2=False, retries=0, trust_env=False))
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                        transport=AuthorizedTransport(inner, guard), timeout=self.timeout,
                        trust_env=False, follow_redirects=False, event_hooks={"request": [stamp]}) as http:
                    async with streamable_http_client(
                            self.url, http_client=http, terminate_on_close=False) as (read, write, _):
                        async with ClientSession(
                                read, write, read_timeout_seconds=timedelta(seconds=self.timeout)) as session:
                            await session.initialize()
                            await guard()
                            yield session
        except GatewayToolError:
            raise
        except (McpError, httpx.HTTPError, ValueError, RuntimeError, TimeoutError, ExceptionGroup):
            # MCP task groups wrap transport and authorization failures; never expose their payloads.
            raise GatewayToolError("threadlight:gateway_unavailable") from None
        finally:
            active = False

    async def _inventory(self, session):
        result = await session.list_tools()
        if result.nextCursor is not None or len(result.tools) > 64:
            raise GatewayToolError("threadlight:gateway_inventory_unsupported")
        tools = {tool.name: tool for tool in result.tools}
        if len(tools) != len(result.tools) or not set(self.selected) <= set(tools):
            raise GatewayToolError("threadlight:gateway_selection_unavailable")
        return {name: tools[name].model_dump(mode="json", by_alias=True) for name in self.selected}

    async def connect(self):
        if self._functions is not None:
            raise GatewayToolError("threadlight:gateway_already_connected")
        async with self._session() as session:
            descriptors = await self._inventory(session)
        if len(canonical(descriptors)) > 65536:
            raise GatewayToolError("threadlight:gateway_inventory_limit")
        self._descriptors = descriptors
        self._functions = [
            self._function(name, descriptor) for name, descriptor in descriptors.items()
        ]

    def deferred_functions(self, descriptors):
        """Expose a frozen inventory without requiring authority at host startup."""
        if self._functions is not None or self._deferred_descriptors is not None:
            raise GatewayToolError("threadlight:gateway_already_connected")
        if not isinstance(descriptors, Mapping) or set(descriptors) != set(self.selected):
            raise ValueError("exact_deferred_gateway_inventory_required")
        normalized = {}
        for name in self.selected:
            descriptor = MCPTool.model_validate(descriptors[name])
            if descriptor.name != name:
                raise ValueError("deferred_gateway_tool_name_mismatch")
            normalized[name] = descriptor.model_dump(mode="json", by_alias=True)
        if len(canonical(normalized)) > 65536:
            raise GatewayToolError("threadlight:gateway_inventory_limit")
        functions = [
            self._function(name, descriptor, deferred_connect=True)
            for name, descriptor in normalized.items()
        ]
        self._deferred_descriptors = normalized
        return functions

    def _function(self, name, descriptor, *, deferred_connect=False):
        deferred = (descriptor.get("_meta") or {}).get("threadlight.approval_mode") == "deferred"
        evidence_selected = (descriptor.get("_meta") or {}).get("threadlight.evidence") == EVIDENCE_META
        schema = deepcopy(descriptor["inputSchema"])
        if deferred:
            if "governance_operation_id" in schema.get("properties", {}):
                raise GatewayToolError("threadlight:gateway_reserved_field")
            schema.setdefault("properties", {})["governance_operation_id"] = {
                "type": "string", "minLength": 1, "maxLength": 128,
                "description": "Resume an existing pending operation; this reference is not approval.",
            }

        async def invoke(**arguments):
            if deferred_connect:
                async with self._connect_lock:
                    if self._functions is None:
                        await self.connect()
                    if self._descriptors != self._deferred_descriptors:
                        raise GatewayToolError("threadlight:gateway_inventory_changed")
            operation = arguments.pop("governance_operation_id", None) if deferred else None
            evidence = arguments.pop(EVIDENCE_ARGUMENT, None) if evidence_selected else None
            if operation is not None:
                operation = parse(Identifier, canonical(operation))
            call = {"name": name, "arguments": canonical(arguments), "key": operation or uuid.uuid4().hex,
                    "evidence": evidence}
            async with self._session(call=call) as session:
                if await self._inventory(session) != self._descriptors:
                    raise GatewayToolError("threadlight:gateway_inventory_changed")
                reply = await session.call_tool(
                    name, arguments=deepcopy(arguments),
                    **({"meta": {EVIDENCE_META: evidence}} if evidence is not None else {}))
            body = reply.structuredContent
            if not isinstance(body, dict):
                raise GatewayToolError("threadlight:gateway_invalid_result")
            if body.get("status") == "pending_approval":
                if not deferred or reply.isError:
                    raise GatewayToolError("threadlight:gateway_invalid_pending_result")
                return self._pending_result(body, call, arguments)
            if body.get("reason_code") == "outcome_unknown":
                raise GatewayToolError("threadlight:gateway_outcome_unknown")
            if body.get("reason_code") == "output_denied":
                raise GatewayToolError("threadlight:gateway_output_denied")
            if body.get("status") == "unavailable":
                raise GatewayToolError("threadlight:gateway_unavailable_or_unknown")
            if body.get("status") == "blocked":
                raise GatewayToolError("threadlight:gateway_denied")
            if reply.isError or body.get("status") != "completed" or not isinstance(body.get("result"), dict):
                raise GatewayToolError("threadlight:gateway_invalid_result")
            return body["result"]

        return FunctionTool(name=name, description=descriptor.get("description") or f"Governed action: {name}",
                            input_model=schema, func=invoke,
                            result_parser=SKIP_PARSING)

    def _pending_result(self, body, call, arguments):
        try:
            validate_pending_review(body)
            if body["operation_id"] != call["key"] or body["review_context"]["action"] != call["name"]:
                raise ValueError("pending_action_mismatch")
        except (ValueError, TypeError, KeyError):
            raise GatewayToolError("threadlight:gateway_invalid_pending_result") from None
        return {**deepcopy(body), "resume_arguments": deepcopy(arguments)}


async def create_gateway_agent(*, config, client, local_tools, instructions, credential,
                               authorize, transport_factory=None, context_providers=(),
                               deferred_descriptors=None):
    document = validate_governance_contract(
        config["contract"], deployment_target="customer-pilot", runtime="microsoft-agent-framework")
    selected = [tool for tool in document["tools"] if tool["policy_binding"] is not None]
    reads = [tool for tool in document["tools"] if tool["policy_binding"] is None]
    if (document["framework"] != "microsoft-agent-framework" or not selected
            or document["governance"]["lifecycle_bindings"]
            or document["governance"]["environment_modes"][config["environment"]] != "enforce"
            or any(tool["enforcement_path"] != "governed-tool-gateway" for tool in selected)
            or any(tool["consequence"] != "read" for tool in reads)):
        raise ValueError("maf_gateway_only_contract_required")
    if (any(not isinstance(tool, FunctionTool) for tool in local_tools)
            or len(local_tools) != len(reads)
            or {tool.name for tool in local_tools} != {tool["id"] for tool in reads}):
        raise ValueError("exact_unbound_read_local_tool_inventory_required")
    remote = GovernedMCPTools(
        url=config["gateway_url"], scope=config["gateway_scope"], credential=credential,
        authorize=authorize, selected_tools=[tool["id"] for tool in selected],
        transport_factory=transport_factory)
    if deferred_descriptors is None:
        await remote.connect()
        functions = remote.functions
    else:
        functions = remote.deferred_functions(deferred_descriptors)

    class GatewayAgent(Agent):
        def run(self, messages=None, **kwargs):
            # Request surfaces cannot introduce code tools, middleware or provider options.
            options = kwargs.get("options")
            if any(kwargs.get(key) is not None for key in (
                    "tools", "middleware", "compaction_strategy", "tokenizer",
                    "function_invocation_kwargs", "client_kwargs")) or (
                        options is not None and (
                            not isinstance(options, Mapping)
                            or set(options) - {"store", "temperature", "top_p", "max_tokens",
                                               "allow_multiple_tool_calls"}
                            or options.get("store", False) is not False)):
                raise GatewayToolError("threadlight:gateway_configuration_override")
            return super().run(messages, **kwargs)

    return GatewayAgent(
        client=client, id=config["agent_id"], name=config["agent_id"],
        instructions=instructions, tools=[*local_tools, *functions],
        context_providers=list(context_providers), default_options={"store": False},
        middleware=[RequireResolvedApprovals()])
