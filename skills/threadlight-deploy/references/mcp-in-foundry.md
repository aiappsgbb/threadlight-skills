# MCP in Foundry

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when wiring or debugging MCP servers for a hosted agent.

## MCP in Foundry

Hosted agent containers connect to MCP servers using `client.get_mcp_tool()` from
`FoundryChatClient`. The container loads `mcp-config.json` at startup and creates
tool instances for each configured server.

```
┌────────────────────────┐     HTTPS      ┌─────────────────────┐
│  Hosted Agent Container │ ─────────────► │  MCP ACA            │
│  client.get_mcp_tool()  │               │  (e.g. Cosmos MCP)  │
│  Agent + ResponsesHost  │               │  Port 8080 /mcp     │
└────────────────────────┘                └─────────────────────┘
```

**How it works:**
- Container loads `mcp-config.json` at startup (or `MCP_SERVER_URL` env var)
- Creates `client.get_mcp_tool(name=..., url=..., approval_mode="never_require")` per server
- Passes tools to `Agent(tools=[...])` alongside skill-loaded instructions
- Container manages the entire MCP lifecycle

**MCP protocol requirements (ALL 6 must return HTTP 200):**
1. `initialize` — Protocol handshake
2. `notifications/initialized` — Client notification
3. `tools/list` — Discover available tools
4. `prompts/list` — Required by agent-framework (even if empty)
5. `resources/list` — Required by agent-framework (even if empty)
6. `logging/setlevel` — Set log level (lowercase!)

### Custom MCP Servers

For data stores not covered by Foundry built-ins, deploy your own MCP server:

| Option | Best For | Endpoint Pattern |
|--------|----------|-----------------|
| **Azure Container App** | Cosmos DB, custom APIs | `https://<aca>.azurecontainerapps.io/mcp` |
| **Azure Functions** | Lightweight, consumption-billed | `https://<func>.azurewebsites.net/runtime/webhooks/mcp` |

**Transport requirements:**
- Foundry only accepts **remote HTTP** MCP endpoints (no stdio/local)
- ACA: Streamable HTTP at `/mcp` endpoint (JSON-RPC over HTTP POST)
- Azure Functions: HTTP Streamable transport
- Non-streaming tool call timeout: **100 seconds**
- Private MCP (VNet) requires Standard Agent Setup
