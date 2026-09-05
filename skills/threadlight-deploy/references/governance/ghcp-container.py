"""Copilot SDK / Invocations adapter. Only selected MCP effects use the gateway."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import time
from urllib.parse import urlsplit


def route_mcp_servers(servers, contract, bindings, gateway_url):
    result = deepcopy(servers)
    selected = [t for t in contract["tools"] if t["policy_binding"] not in (None, "none")]
    if not selected:
        return result
    return _route_selected(result, servers, selected, bindings, gateway_url)


def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class McpRelay:
        """The pinned SDK hook changes _meta, NOT headers. Bridge only on loopback.

        The relay refreshes the gateway-audience bearer for bootstrap and every call.
        Host-issued tickets bind SDK call identity to exact arguments; no model token,
        caller URL, caller Authorization header or model-provided nonce is forwarded.
        """
        def __init__(self, *, gateway_url, scope, credential, invocation_id, tools, http):
            self.url, self.scope, self.credential, self.http = gateway_url, scope, credential, http
            self.invocation_id, self.tools = invocation_id, frozenset(tools)
            self.secret = secrets.token_urlsafe(32)
            self.tickets = {}

        async def pre_mcp(self, event, context):
            if event["serverName"] != "threadlight-governed":
                return None
            if event["toolName"] not in self.tools or not event.get("toolCallId"):
                raise ValueError("governance_call_identity_required")
            key = hashlib.sha256(canonical([
                self.invocation_id, event["sessionId"], event["toolCallId"],
            ])).hexdigest()
            body = canonical([event["toolName"], event["arguments"]])
            previous = self.tickets.get(key)
            if previous is not None and previous != body:
                raise ValueError("governance_call_identity_reused")
            self.tickets[key] = body
            return {"metaToUse": {**(event.get("_meta") or {}), "threadlight/call": key}}

        def app(self):
            from starlette.applications import Starlette
            from starlette.responses import JSONResponse, Response
            from starlette.routing import Route

            async def forward(request):
                if not secrets.compare_digest(request.headers.get("X-Threadlight-Relay", ""), self.secret):
                    return JSONResponse({"error": "unauthorized"}, 401)
                try:
                    raw = bytearray()
                    async with asyncio.timeout(5):
                        async for part in request.stream():
                            raw.extend(part)
                            if len(raw) > 32768:
                                raise ValueError()
                    document = json.loads(raw)
                    method = document["method"]
                    if method not in ("initialize", "notifications/initialized", "ping", "tools/list", "tools/call"):
                        raise ValueError()
                    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
                    if method == "tools/call":
                        params = document["params"]
                        key = params.get("_meta", {}).get("threadlight/call")
                        if (not key or self.tickets.get(key) != canonical([
                                params["name"], params.get("arguments", {})])):
                            return JSONResponse({"error": "invalid_call_identity"}, 403)
                        headers["Idempotency-Key"] = key
                        headers["X-Correlation-ID"] = key
                    # MCP's protocol header is not an identity claim.
                    if "MCP-Protocol-Version" in request.headers:
                        headers["MCP-Protocol-Version"] = request.headers["MCP-Protocol-Version"]
                    token = await self.credential.get_token(self.scope)
                    headers["Authorization"] = f"Bearer {token.token}"
                    async with self.http.stream("POST", self.url, content=canonical(document),
                                                headers=headers, follow_redirects=False) as response:
                        output = bytearray()
                        async for part in response.aiter_bytes():
                            output.extend(part)
                            if len(output) > 65536:
                                raise ValueError()
                        return Response(bytes(output), status_code=response.status_code,
                                        media_type=response.headers.get("Content-Type", "application/json"))
                except Exception:
                    return JSONResponse({"error": "gateway_unavailable"}, 503)
            return Starlette(routes=[Route("/mcp", forward, methods=["POST"])])


def build_host(config, **host_options):
        from azure.ai.agentserver.invocations import InvocationAgentServerHost
        from azure.identity.aio import DefaultAzureCredential
        from copilot import CopilotClient, PermissionHandler, ProviderConfig
        from copilot.session_events import SessionEventType
        import httpx
        from starlette.responses import JSONResponse, StreamingResponse
        import uvicorn

        base = Path(__file__).resolve().parent
        routed = route_mcp_servers(
            config["mcp_servers"], config["contract"], config["mcp_bindings"],
            os.environ["GOVERNED_TOOL_GATEWAY_URL"])

        class GovernedHost(InvocationAgentServerHost):
            async def _readiness_endpoint(self, request):
                try:
                    from datetime import datetime, timezone
                    from govern_control_plane.client import ServiceTransport
                    from govern_control_plane.models import SignedBundle, canonical, parse
                    async with DefaultAzureCredential() as credential, httpx.AsyncClient(
                            timeout=5, trust_env=False, follow_redirects=False) as http:
                        async with ServiceTransport(
                                base_url=os.environ["GOV_CONTROL_PLANE_URL"],
                                scope=config["control_plane_scope"], credential=credential, http=http) as service:
                            _, signed = await service.request("GET", f"/bundles/{config['policy_id']}/{config['policy_version']}")
                        policy = parse(SignedBundle, canonical(signed)).envelope
                        if (policy.tenant_id != config["tenant_id"] or policy.key_id != config["key_id"]
                                or policy.policy_id != config["policy_id"] or policy.version != config["policy_version"]
                                or policy.expires_at <= datetime.now(timezone.utc)):
                            raise ValueError("policy_unavailable")
                        token = await credential.get_token(config["gateway_scope"])
                        response = await http.get(os.environ["GOVERNED_TOOL_GATEWAY_URL"].rsplit("/", 1)[0] + "/health",
                                                  headers={"Authorization": f"Bearer {token.token}"})
                        health = response.json()
                        if (response.status_code != 200 or health.get("policy_digest") != policy.content_digest
                                or any(not health["bindings"][name]["healthy"]
                                       for name in routed["threadlight-governed"]["tools"])):
                            raise ValueError()
                        return JSONResponse({"scope": "gateway-controls-not-effect-closure",
                                             "bindings": health["bindings"]})
                except Exception:
                    return JSONResponse({"status": "unavailable"}, 503)

        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
        host = GovernedHost(**host_options)

        async def stream(invocation_id, prompt):
            async with DefaultAzureCredential() as credential, httpx.AsyncClient(
                    timeout=30, trust_env=False, follow_redirects=False) as http:
                model_token = await credential.get_token("https://ai.azure.com/.default")
                relay = McpRelay(
                    gateway_url=os.environ["GOVERNED_TOOL_GATEWAY_URL"], scope=config["gateway_scope"],
                    credential=credential, invocation_id=invocation_id,
                    tools=routed["threadlight-governed"]["tools"], http=http)
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                sock.listen()
                server = uvicorn.Server(uvicorn.Config(relay.app(), access_log=False, log_level="critical"))
                task = asyncio.create_task(server.serve(sockets=[sock]))
                client = CopilotClient()
                session = None
                unsubscribe = None
                try:
                    async with asyncio.timeout(10):
                        while not server.started:
                            if task.done():
                                task.result()
                                raise RuntimeError("relay_start_failed")
                            await asyncio.sleep(0.01)
                    servers = deepcopy(routed)
                    servers["threadlight-governed"].update(
                        url=f"http://127.0.0.1:{port}/mcp", headers={"X-Threadlight-Relay": relay.secret})
                    await client.start()
                    session = await client.create_session(
                        provider=ProviderConfig(type="azure", base_url=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                                                wire_api="responses", bearer_token=model_token.token),
                        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                        system_message={"mode": "replace", "content": (base / "copilot-instructions.md").read_text()},
                        skill_directories=[str(base / "skills")],
                        working_directory=str(Path.home()), streaming=True,
                        mcp_servers=servers, hooks={"on_pre_mcp_tool_call": relay.pre_mcp},
                        on_permission_request=PermissionHandler.approve_all,
                        enable_config_discovery=False,
                    )
                    queue = asyncio.Queue()
                    def event_received(event):
                        queue.put_nowait(event)
                    unsubscribe = session.on(event_received)
                    # New session + fresh BYOK bearer per invocation; never run past its validity.
                    async with asyncio.timeout(max(1, model_token.expires_on - time.time() - 60)):
                        await session.send(prompt)
                        while True:
                            event = await queue.get()
                            if event.type == SessionEventType.SESSION_IDLE:
                                break
                            if event.type == SessionEventType.SESSION_ERROR:
                                raise RuntimeError("agent_unavailable")
                            if event.type.value in ("assistant.message", "assistant.message_delta"):
                                yield b"data: " + canonical(event.to_dict()) + b"\n\n"
                    yield b"event: done\ndata: " + canonical({"invocation_id": invocation_id}) + b"\n\n"
                except Exception:
                    yield b'data: {"type":"error","message":"agent_unavailable"}\n\n'
                finally:
                    if unsubscribe:
                        unsubscribe()
                    if session:
                        await session.disconnect()
                    await client.stop()
                    server.should_exit = True
                    await task
                    sock.close()

        @host.invoke_handler
        async def invoke(request):
            try:
                data = await request.json()
                if not isinstance(data.get("input"), str) or not data["input"].strip():
                    raise ValueError()
            except Exception:
                return JSONResponse({"error": "invalid_request"}, 400)
            return StreamingResponse(stream(request.state.invocation_id, data["input"]),
                                     media_type="text/event-stream")
        return host


def _route_selected(result, servers, selected, bindings, gateway_url):
    url = urlsplit(gateway_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path):
        raise ValueError("invalid_gateway_url")
    if "threadlight-governed" in servers:
        raise ValueError("reserved_gateway_server")
    actions = []
    for tool in selected:
        if tool["enforcement_path"] != "governed-tool-gateway":
            raise ValueError("github_copilot_local_hooks_unsupported")
        binding = bindings.get(tool["id"])
        if not binding or binding["server"] not in result:
            raise ValueError("missing_mcp_binding")
        name, original = binding["server"], binding["tool"]
        for other, candidate in servers.items():
            if other != name and candidate.get("url") == servers[name].get("url"):
                inventory = candidate.get("tools", ["*"])
                if original in inventory or "*" in inventory:
                    raise ValueError("bound_tool_alias_accessible")
        configured = result[name]
        inventory = configured.get("tools")
        if not isinstance(inventory, list) or "*" in inventory or original not in inventory:
            raise ValueError("explicit_tool_inventory_required")
        if original != tool["id"]:
            raise ValueError("gateway_action_name_must_match_tool_id")
        configured["tools"] = [t for t in inventory if t != original]
        if not configured["tools"]:
            del result[name]
        actions.append(tool["id"])
    result["threadlight-governed"] = {
        "type": "http", "url": gateway_url, "tools": actions,
    }
    return result


if __name__ == "__main__":
    configuration = json.loads((Path(__file__).resolve().parent / "governance-config.json").read_text())
    build_host(configuration).run()
