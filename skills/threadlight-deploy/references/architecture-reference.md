# Architecture decisions and runtime reference

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when explaining design choices or looking up runtime, environment, identity or Bicep details.

## Key Architecture Decisions

### Why Hosted Agents (not Prompt/Declarative)?

Prompt agents and Declarative agents run on Foundry's servers — no custom container.
They support platform-managed MCP tools via `MCPTool`. However, they CANNOT:

- Load skills dynamically via `SkillsProvider`
- Run custom ASGI middleware (normalise arguments, liveness probes)
- Inject runtime configuration into instructions (COSMOS_DATABASE, tool-use discipline)
- Execute complex multi-step orchestration with custom error handling

**Rule of thumb:** Deploy a Hosted Agent. A Prompt Agent is deployed only when the
SPEC explicitly sets `agent_type: prompt` with a `trivial_justification` (or the user
explicitly opts in) and every trivial criterion holds. Never pick a prompt agent on your
own initiative because it looks simpler, and never as the answer to a refused app-side request.

### Why SkillsProvider instead of hardcoded instructions?

- **Progressive disclosure** — agent loads skills on-demand, not all at once
- **Smaller context** — instructions stay short; details load when needed
- **Modularity** — add/remove skills without changing copilot-instructions.md

### Why GHCP SDK (Default Runtime)?

- **No timeout**: Invocations protocol uses SSE — no 120s gateway timeout on long tool loops
- **Streaming**: SSE event stream for real-time output
- **Simpler MCP**: `mcp_servers` parameter vs manual `get_mcp_tool()` calls

> Note: progressive skill loading via `SkillsProvider` is **NOT** a GHCP-only
> feature — MAF supports it equally well. See `threadlight-deploy/references/hosted-agent/maf`
> § Skill Loading for the MAF wiring (`context_providers=[skills_provider]`).

### When to Use MAF Instead

- Agent needs **Foundry Toolbox** (`web_search`, `code_interpreter`) — only available via `client.get_toolbox()`
- Agent needs **custom `@tool` Python functions** — GHCP doesn't support them
- Agent needs **file generation** (`save_report` @tool → XLSX/PDF/CSV via session files API)
- Agent needs **Toolbox + MCP** in the same runtime
- Agent primarily does **data queries with fast MCP tools** — MAF is 10-20x faster because GHCP's `load_skill` overhead dominates (20-34 extra tool calls per query). Benchmark: MAF+gpt-5.4-mini = 19s vs GHCP+gpt-5.4-mini = 220s for the same data query.

### Tool-Use Discipline

The container runtime auto-injects a "Tool-Use Discipline" section into instructions.
This is CRITICAL for eval scores — without it, agents over-call tools
(list_databases, get_schema) on every turn, causing `tool_selection` failures
(30-50% instead of 80%+).

---

## Reference: Container Runtime Architecture

### GHCP SDK variant (default)

```
container.py (GHCP variant)
│
├── _load_instructions()
│   └── Read copilot-instructions.md
│
├── SkillsProvider(skills_directory="skills/")
│   └── Progressive skill loading on demand
│
├── CopilotClient(model_provider=BYOK, mcp_servers={...})
│   └── DefaultAzureCredential → bearer token
│
└── InvocationAgentServerHost(agent).run()  →  port 8088 (SSE)
```

### MAF variant (fallback)

```
container.py (MAF variant)
│
├── _load_instructions()
│   └── Read copilot-instructions.md (+ config.json) — base prompt ONLY
│
├── Skill loading — pick ONE:
│   ├── (option A, recommended)
│   │     SkillsProvider.from_paths("skills/")
│   │     └── Progressive disclosure: advertise (~100 tok/skill) → load_skill on demand
│   └── (option B, legacy)
│         _load_skills()
│         └── Read skills/**/SKILL.md → append to instructions (concat at startup)
│
├── _load_mcp_config()
│   ├── Try /app/mcp-config.json (with ${ENV_VAR} expansion)
│   └── Fall back to MCP_SERVER_URL env var
│
├── FoundryChatClient(project_endpoint, model, credential)
│   └── DefaultAzureCredential — keyless auth to Foundry
│
├── _create_mcp_tools(client, config)
│   └── MCPStreamableHTTPTool(url, parse_tool_results=_extractor)
│
├── Agent(
│     client, instructions, tools,
│     context_providers=[skills_provider] if option A else [],
│     default_options={"store": False},
│   )
│
└── ResponsesHostServer(agent).run()  →  port 8088
```

---

## Reference: Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `FOUNDRY_PROJECT_ENDPOINT` | ✅ (auto) | **RESERVED — platform injects automatically.** Do NOT declare in agent.yaml. Container reads via `os.environ`. |
| `AZURE_AI_PROJECT_ENDPOINT` | No | Alternative endpoint name (fallback in container.py) |
| `AZURE_CONTAINER_REGISTRY_ENDPOINT` | ✅ | ACR endpoint (set by azd from Bicep) |
| `AZURE_CLIENT_ID` | Bot only | UAMI client ID for bot (set in ACA env vars by Bicep) |
| `PROJECT_ENDPOINT` | Bot only | Project endpoint for bot (set in ACA env vars by Bicep) |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | ✅ | Model deployment name (declared in agent.yaml env_vars — user-defined, NOT reserved) |
| `MODEL_DEPLOYMENT_NAME` | No | Fallback model name (prefer AZURE_AI_MODEL_DEPLOYMENT_NAME) |
| `MCP_SERVER_URL` | No | Legacy single MCP server URL (prefer mcp-config.json) |
| `PORT` | No | Listen port (default: 8088) |

> **Reserved environment variables:** All `FOUNDRY_*` and `AGENT_*`
> prefixed variables are reserved by the platform. Do NOT declare them in
> `agent.yaml` `environment_variables` — the platform injects them automatically.
> Declaring them causes `invalid_payload` errors at `create_version` time.
>
> **Also reserved:** `APPLICATIONINSIGHTS_CONNECTION_STRING`.
> Even when the platform's auto-injection silently fails (see Gotchas table for
> the AppIn AAD-rejected / ApiKey-silent-drop trap), you CANNOT escape-hatch by
> setting it in `agent.yaml` — `create_version` fails immediately with
> `invalid_request_error`: "Environment variable
> 'APPLICATIONINSIGHTS_CONNECTION_STRING' is reserved for platform use" (request
> ID available in platform logs). The only safe path is a
> guarded `_init_telemetry()` in `container.py` so the agent runs even when AppIn
> is broken — see the OpenTelemetry section above.

---

## Reference: Identity & RBAC

> **See the `threadlight-deploy/references/hosted-agent/maf` reference** for the complete RBAC reference — identity model,
> required role assignments for deployer/project MI/agent identities, manual assignment
> commands, and RBAC propagation timing.

**Essential for deploy (quick reference):**
- Each hosted agent gets **two Entra identities** at deploy time (instance + blueprint)
- Both need `Foundry User` (`53ca6127-db72-4b80-b1b0-d745d6d5456d`, formerly `Azure AI User`) on Foundry account AND project — assign by GUID
- Deployer needs `Azure AI Project Manager` on the project
- Set `AZURE_TENANT_ID` in azd env for postdeploy RBAC auto-assignment
- RBAC propagation takes 5-15 minutes for new service principals

---

## Reference: Bicep Parameters (Refreshed Preview)

The scaffold's `infra/main.parameters.json` includes these critical parameters:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `ENABLE_HOSTED_AGENTS` | `false` (set to `true` by extension preprovision hook) | Enables hosted agent infrastructure (ACR, agent capabilities) |
| `ENABLE_CAPABILITY_HOST` | **`false`** | **MUST be false.** Hosted agents no longer use a capability host; Microsoft Learn says to remove capability host creation from provisioning scripts ([migrate hosted agents](https://learn.microsoft.com/azure/foundry/agents/how-to/migrate-hosted-agent-preview)). The pilot `infra/main.bicep` creates none. |
| `ENABLE_MONITORING` | `true` | Creates Application Insights + Log Analytics |
| `USE_EXISTING_AI_PROJECT` | `false` | Set `true` to point at existing Foundry project |

> **⚠️ `ENABLE_CAPABILITY_HOST=false` is mandatory.** The current hosted-agent backend
> provisions its own infrastructure: "You no longer need to create an account-level
> capability host" and "Remove capability host creation steps from provisioning scripts"
> ([Microsoft Learn: migrate hosted agents](https://learn.microsoft.com/azure/foundry/agents/how-to/migrate-hosted-agent-preview)).
> Capability hosts remain only for a bring-your-own-resources (standard) agent setup
> ([capability hosts](https://learn.microsoft.com/azure/foundry/agents/concepts/capability-hosts)).
> The old default was `true` (for initial preview). If your Bicep still defaults to
> `true`, provisioning will fail or create unnecessary resources. The pilot
> `infra/main.bicep` dropped its capability hosts on 2026-11-07 to follow this
> guidance; that change has not yet been re-proven by a live deploy.
>
> **⚠️ MIGRATION: Delete existing CapabilityHosts.** If the Foundry account had a previous
> initial preview deployment, an old CapabilityHost resource may still exist. Its presence
> blocks the refreshed preview API — you'll get `"The requested experience is not available
> for this subscription"` even with `ENABLE_CAPABILITY_HOST=false`. Delete it:
> ```bash
> az rest --method DELETE --url "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/capabilityHosts/agents?api-version=2025-10-01-preview"
> ```
> Deletion takes 2-5 minutes. Also remove the `capabilityHosts` resource from `ai-project.bicep`.
>
> **⚠️ Foundry project Bicep MUST include `identity` block.** The
> `Microsoft.CognitiveServices/accounts/projects` resource **silently fails
> with 500 InternalServerError** if the `identity` block is omitted. The
> backend AML RP rejects with 400 (missing managed identity) but the
> CogServices RP wraps it as 500 with no actionable message. Always include:
> ```bicep
> resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
>   parent: aiAccount
>   name: aiProjectName
>   location: location
>   identity: {
>     type: 'SystemAssigned'   // MANDATORY — omitting causes misleading 500
>   }
>   properties: { displayName: aiProjectName }
> }
> ```
> The parent AI account also needs `allowProjectManagement: true` in its
> `properties` block (requires `2025-04-01-preview` or later API version —
> `2024-10-01` silently ignores the property).

### Region Availability

Not all regions support hosted agents. If you get `"The requested experience is not
available for this subscription"` during `azd deploy`, try a different region.

East US 2 is in the documented hosted-agent region list, as are East US, North
Central US, Sweden Central, Canada Central, Australia East and West US 3
([Microsoft Learn: hosted agents, region availability](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#region-availability),
checked 2026-11-07). It is also the `runtime-policy.json` default region. An
April 2026 field test saw "experience not available" in `eastus2`. The cause
was not established; this skill's migration note above (field experience, not
documented on Learn) links that error to a leftover initial-preview capability
host, so check that before switching region.
Model availability and quota are separate per region: confirm both before
`azd up`.

> Always check [Region availability](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#region-availability)
> for the latest supported regions.

---

## Reference: MCP ACA Deployment

> **See the dedicated `threadlight-mcp-aca` skill** for full details on deploying MCP servers
> as Azure Container Apps or Azure Functions — including Cosmos DB MCPToolKit, Playwright MCP,
> protocol requirements, Bicep modules, and authentication patterns.

### Mock Systems → Mock MCP Server

For systems marked **mock** in the spec, generate a mock MCP server using
`threadlight-mcp-aca` Option D (Mock MCP). This ensures the demo agent has callable
tools backed by sample data — the customer sees real MCP tool calls.

For systems using **Cosmos DB**, generate a Cosmos MCPToolKit deployment using
`threadlight-mcp-aca` Option A — provides 10 tools out of the box.

1. Run the `threadlight-mcp-aca` skill to generate `src/mcp/` from spec tool contracts
2. Deploy to ACA (or run locally for dev)
3. Wire the endpoint into `src/agent/mcp-config.json`:

```json
{
  "servers": {
    "mock-tools": {
      "type": "http",
      "url": "${MOCK_MCP_URL}/mcp"
    }
  }
}
```

4. Add to `deploy-notes.md`:

```
📎 Mock Systems (demo data — swap when onboarding):
  - {system-name}: mock MCP at ${MOCK_MCP_URL}
    → See threadlight-mcp-aca skill to deploy real MCP when system is accessible.
    → Tool contracts stay the same — only the endpoint URL changes.
```
