# Phase 2: Deployment artifact templates

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased; the content-filtering recipe is
> replaced by a pointer to the official guardrails guidance). Read it when generating the runtime files in design mode.

Create these files in the project root:

### 1. `src/agent/copilot-instructions.md` — Agent System Prompt

Transform AGENTS.md into a runtime system prompt:

```markdown
# {Agent Name}

{Purpose from AGENTS.md — 2-3 sentences}

## Behavioral Guidelines

{Copy behavioral guidelines from AGENTS.md}

## Available Tools

{List the MCP/built-in tools with usage guidance}

## Compliance

{Copy compliance constraints from AGENTS.md}
```

**Rules:**
- Keep it concise (500-1500 words) — this is injected every turn
- Remove deployment metadata, local dev info, tables that reference SKILL.md paths
- Focus on WHAT the agent should DO, not how it's implemented
- Include tool-use discipline directive (see reference)

### 2. `src/agent/skills/` Directory

Skills are generated directly by `threadlight-design` into `src/agent/skills/`.
No copying needed — deploy reads them in place.

```
skills/
├── scan-competitor-x/
│   └── SKILL.md
├── generate-report/
│   └── SKILL.md
└── detect-changes/
    └── SKILL.md
```

These are surfaced to the agent through `SkillsProvider` (recommended,
progressive disclosure) **or** loaded at startup by a `_load_skills()`
helper that appends them to instructions (legacy concat). See
`threadlight-deploy/references/hosted-agent/maf` § Skill Loading for the trade-off and a
production-tested defensive `_build_skills_provider()` helper.

**For every generated MAF agent/executor**, apply the local
[autonomous skill-read contract](../../_shared/maf-skill-approval.md), including
`disable_load_skill_approval=True` and `disable_read_skill_resource_approval=True`
only on the trusted packaged provider. Preserve script/business approval settings
and existing governance; install the portable `RequireResolvedApprovals` boundary
for unattended hosts without an approval channel. Test actual skill/resource
delivery and explicit residual-approval errors before parsing report text.
Do not assume an external companion example already includes this integration.

### 3. `src/agent/mcp-config.json` — MCP Server Configuration

> **NOTE:** This config is used by the container runtime. At startup, `_load_mcp_config()`
> reads this file, expands `${ENV_VAR}` placeholders, and creates tools via `client.get_mcp_tool()`.

Map configured MCP servers for Foundry runtime (NOT local dev):

```json
{
  "servers": {
    "cosmos-tools": {
      "type": "http",
      "url": "${MCP_SERVER_URL}/mcp"
    }
  }
}
```

**Rules:**
- Use `${ENV_VAR}` placeholders — resolved at container start
- Remove local-only servers (Playwright MCP, local Azure MCP, stdio servers)
- Only include servers accessible from Foundry containers (remote HTTP endpoints)
- The runtime expands env vars automatically

**Foundry tool → runtime mapping:**

| Design Tool | Runtime | Notes |
|-------------|---------|-------|
| Browser Automation | **MCP ACA** — deploy Playwright as a remote MCP server | Local Playwright cannot run inside hosted agent containers. Use `npx @playwright/mcp` on ACA. |
| Web Search | **Foundry Toolbox** — `client.get_toolbox("toolbox-name")` | Built-in Toolbox tool type. No Bing resource needed. *MAF only.* |
| Code Interpreter | **Foundry Toolbox** — add `code_interpreter` to Toolbox | Computation and data processing. *MAF only.* |
| File Generation | **Custom `@tool`** — `save_report` writing to `$HOME` | XLSX (openpyxl), PDF (fpdf2), CSV/HTML (text). Downloadable via session files API + FileConsentCard in Teams. *MAF only.* See `threadlight-deploy/references/hosted-agent/maf` reference § File Generation. |
| **Knowledge sources (docs, policies, KB)** | **Foundry IQ** — Azure AI Search with agentic retrieval | For static/semi-static knowledge (policies, regulations, product docs). See `foundry-iq` skill. Creates Knowledge Base with query planning + citations. |
| **API data (dynamic, transactional)** | **MCP ACA** — custom or mock MCP server | For live data (CRM, orders, transactions). See `threadlight-mcp-aca` skill. |
| **Cosmos DB** | **MCP ACA** — .NET MCPToolKit (10 tools out of the box) | See `threadlight-mcp-aca` Option A. Deploy as `src/mcp/` or shared ACA. |
| Azure AI Search (direct) | Foundry Toolbox or custom MCP | Use Toolbox if available, or deploy custom MCP ACA |
| Custom data store | Custom MCP server (deploy as ACA or Azure Functions) | Proven pattern — see `threadlight-mcp-aca` |

> **Knowledge vs API data:** Use the spec § 7 (Knowledge Sources) vs § 5 (System Integrations)
> distinction to choose:
> - **Knowledge sources** (documents, policies, search indexes) → **Foundry IQ** (agentic retrieval
>   with query planning, multi-hop reasoning, citations). See `foundry-iq` skill.
> - **API data** (CRM, ERP, transactional systems) → **MCP server** (mock or real).
>   See `threadlight-mcp-aca` skill.
> - **Cosmos DB** → MCPToolKit as `src/mcp/` — provides 10 tools, deploy as ACA.

> **Key constraints for MAF hosted agents:**
>
> 1. **No local browser** — hosted agent containers are headless Python environments.
>    Deploy browser automation as a remote MCP server on ACA.
>
> 2. **Foundry Toolbox is the preferred tool source** — create a Toolbox with `web_search`
>    and/or `code_interpreter` tools. Load via `client.get_toolbox("name")` in container.py.
>    The Toolbox is an MCP endpoint managed by the platform — no infrastructure to deploy.
>
> 3. **Session files for report output** — custom `@tool` functions can write files to
>    `Path.home()` (agent's `$HOME`). Files persist across turns and are downloadable via
>    the session files API: `GET .../sessions/{sid}/files/content?path=filename`.

### Foundry Toolbox Setup

Create a Toolbox via REST API (or automate in a postprovision hook):

```bash
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
curl -X POST "$PROJECT_ENDPOINT/toolboxes/my-tools/versions?api-version=v1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Foundry-Features: Toolboxes=V1Preview" \
  -d '{"tools":[{"type":"web_search","name":"web_search"},{"type":"code_interpreter","name":"code_interpreter"}]}'
```

In container.py, load as async:
```python
toolbox = await client.get_toolbox(os.environ["TOOLBOX_NAME"])
agent = Agent(client=client, tools=[mcp_tool, toolbox], ...)
```

### Content Filtering for Sensitive Domains

Agents in regulated domains (tobacco, pharma, weapons) can trip the default
content filters on legitimate queries. Create and attach a custom guardrail
(RAI policy) with the official `microsoft-foundry` skill:
`foundry-agent/create/references/guardrails/` (`guardrail-api-create.md`,
`guardrail-attach.md`). Threadlight keeps no content-filter recipe of its own.

**Automate via postprovision hook** — add to `azure.yaml`:
```yaml
hooks:
    postprovision:
        shell: pwsh
        run: 'cd infra/scripts && uv sync --frozen --quiet && uv run postdeploy.py'
```

The hook script creates the Toolbox, RAI policy, and Teams manifest idempotently.

### 4. `src/agent/container.py` — Agent Runtime

Generate the container runtime based on the chosen variant (see Phase 1 § 1d).

#### GHCP SDK variant (default)

**Copy the reference template** from the `threadlight-deploy/references/hosted-agent/ghcp` reference's
`references/container.py` and adapt. Start the azd project from the canonical
pilot scaffold in `references/hosted-agent/ghcp/references/pilot` (`azure.yaml`
with `remoteBuild: true`, complete `infra/main.bicep` with Foundry account +
project + model + role GUIDs, the MCP server, `.dockerignore` and the
`hooks/postdeploy.sh` instance-identity grant). Do not hand-write these files.
Copy `references/Dockerfile`, `references/requirements.lock` and
`references/dockerignore` (as `.dockerignore`) next to `container.py`; the
Dockerfile installs from the hashed `requirements.lock` (`--require-hashes`),
so the build fails without it. The entrypoint name stays `container.py`.

- Model provider: BYOK with `DefaultAzureCredential` → bearer token
- Instructions: loaded from `copilot-instructions.md`
- Skills: loaded via `SkillsProvider` from `skills/` directory (progressive discovery)
- MCP: configured via `mcp_servers` parameter

The runtime uses `CopilotClient` + `InvocationAgentServerHost`:
1. `CopilotClient` with BYOK auth (DefaultAzureCredential → bearer token)
2. `SkillsProvider` reads `skills/` directory for progressive skill loading
3. MCP servers configured via `mcp_servers` parameter
4. `InvocationAgentServerHost` serves the Invocations protocol (SSE streaming)
5. Diagnostic HTTP server on import failure (keeps container alive for debugging)

#### MAF variant (`maf-agent-capabilities` and `deterministic-workflow` routes)

**Copy the reference template** from the `threadlight-deploy/references/hosted-agent/maf` companion
skill and adapt:

The runtime uses `Agent` + `FoundryChatClient` + `ResponsesHostServer`:
1. `FoundryChatClient` with `DefaultAzureCredential` for Foundry auth
2. **Skill loading** — `SkillsProvider.from_paths(skills_dir)` wired via
   `context_providers=[skills_provider]` for progressive disclosure
   (recommended), **or** legacy `_load_skills()` that reads all SKILL.md
   files and appends to instructions. Pick one — never both. See
   `threadlight-deploy/references/hosted-agent/maf` § Skill Loading for the trade-off.
3. `_create_mcp_tools()` creates tools via `MCPStreamableHTTPTool` with a
   `parse_tool_results` extractor (avoids the [<Content>] repr leak)
4. `ResponsesHostServer(agent).run()` serves the Responses protocol
5. Diagnostic HTTP server on import failure

**Do NOT write the container runtime from scratch** — always start from the reference
template in the corresponding skill.

#### Custom Tools (Function Tools)

`Agent` accepts a `tools` parameter for custom Python functions the agent can invoke
at runtime. This is useful for capabilities not covered by MCP — e.g., API calls,
data lookups, or computations.

**Recommended approach — `@tool` decorator:**

```python
from agent_framework import tool
from typing import Annotated
from pydantic import Field

@tool(approval_mode="never_require")
def search_web(
    query: Annotated[str, Field(description="Search query")],
) -> str:
    """Search the web and return results."""
    # Call your search API here
    return "search results..."
```

**Pass to agent:**
```python
agent = Agent(
    client=client,
    instructions=instructions,
    tools=[search_web, mcp_tool_1],
    default_options={"store": False},
    # ...
)
```

**Constraints:**
- Both sync and async functions supported
- Use `Annotated[type, Field(description=...)]` for parameter documentation
- Tool results are returned as text to the LLM for reasoning

> **Tip:** Custom tools are a practical workaround for missing platform capabilities.
> For example, if you need web search but can't use MCP, define a custom tool that
> calls a search API (Tavily, SerpAPI, etc.) directly from Python.

### 5. `src/agent/pyproject.toml` — Python Dependencies

**Copy the inline `pyproject.toml` template below** and replace `__PROJECT_NAME__`:

```toml
[project]
name = "__PROJECT_NAME__-agent"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
    "agent-framework-core==1.3.0",
    "agent-framework-foundry==1.3.0",
    "agent-framework-foundry-hosting>=1.0.0a260421",
    "azure-identity>=1.19.0,<1.26.0a0",
    "mcp>=1.10.0,<2",
    "python-dotenv>=1.0.0",
]

[tool.uv]
required-environments = ["sys_platform == 'linux' and platform_machine == 'x86_64'"]
prerelease = "if-necessary-or-explicit"

[tool.setuptools]
packages = []
```

> **If using `hatchling` as the build backend** (instead of `setuptools`),
> replace `[tool.setuptools] packages = []` with:
> ```toml
> [build-system]
> requires = ["hatchling"]
> build-backend = "hatchling.build"
>
> [tool.hatch.build.targets.wheel]
> packages = ["src"]
> ```
> Without `packages = ["src"]`, `pip install .` fails with
> "Unable to determine which files to ship inside the wheel" because
> hatchling can't auto-discover the project directory.

**CRITICAL: Uses `uv` (not `pip`)** — the `prerelease = "if-necessary-or-explicit"` setting
lets uv resolve prerelease packages that have explicit prerelease markers in their version
specs (e.g. `>=1.0.0a260421`), while keeping all other packages on GA (e.g. `azure-identity`
resolves to 1.25.3 GA, NOT 1.26.0b2 beta). Do NOT use `"allow"` — it pulls beta versions
of azure-identity and other packages unintentionally.

> **🛑 PROVEN-WORKING REFERENCE PINS**
>
> The recipe above is the validated working set. Earlier guidance recommended
> **open ranges** (`agent-framework-core>=1.0.0`) plus an explicit
> `azure-ai-agentserver-responses>=1.0.0b4` — that resolves to a stack uv
> accepts at install time but that crashes the container at first invocation,
> returning the opaque `server_error/model:""`.
>
> **Mandatory rules:**
> - **Pin** `agent-framework-core==1.3.0` and `agent-framework-foundry==1.3.0`. NOT open ranges.
> - **Drop** any explicit `azure-ai-agentserver-responses` line. Let `agent-framework-foundry-hosting`
>   pull its own pinned transitive (`==1.0.0b4` currently, but the right value for whatever
>   foundry-hosting wants).
> - **Add** explicit `mcp>=1.10.0,<2`. `agent-framework-core 1.3.0` does NOT auto-pull it, and
>   `MCPStreamableHTTPTool` will fail at runtime without it. Keep the `<2` bound: mcp 2.x
>   removes `mcp.server.fastmcp`.
> - **Include** `[tool.setuptools] packages = []` — uv needs it to resolve cleanly when
>   the project itself isn't installed (`uv sync --no-install-project`).
> - **Reference**: keep a canonical `src/agent/pyproject.toml` in each process repo
>   and diff against the validated working shape when in doubt.

> **⚠️ Dependency Pinning & Future Updates**
>
> The refreshed hosted agents preview (April 2026) uses these packages:
>
> | Package | Version | Type | Notes |
> |---------|---------|------|-------|
> | `agent-framework` | `>=1.1.0` | ✅ Stable | Meta-package; pulls in core + foundry + openai |
> | `agent-framework-foundry-hosting` | `>=1.0.0a260421` | ⚠️ Alpha | Bridge AF↔protocol; pins agentserver-core==2.0.0b2 + agentserver-responses==1.0.0b4 |
> | `azure-identity` | `>=1.19.0` | ✅ Stable | DefaultAzureCredential for Foundry auth |
>
> **When updating deps in the future:**
> 1. Check PyPI for new versions of `agent-framework-foundry-hosting`
> 2. Inspect its `Requires-Dist` metadata for pinned agentserver versions
> 3. Test locally first: `docker build --platform linux/amd64 -t test-agent .` then
>    `docker run --rm -p 8088:8088 --env-file .env test-agent` — check for import errors
> 4. Run the hosted agent smoke test (`test_hosted_agent.py`)
> 5. Update the pinned versions in BOTH `pyproject-template.toml` AND this SKILL.md code block

If the agent uses OpenTelemetry, add:
```
azure-monitor-opentelemetry>=1.6.4
opentelemetry-sdk>=1.27.0
```

### 6. `src/agent/Dockerfile` — Self-Contained Container

**Copy the inline `Dockerfile` template below** and adapt:

```dockerfile
FROM mcr.microsoft.com/oryx/python:3.12

WORKDIR /app

# Uses uv for dependency management (prerelease support for hosting package)
# Pin uv:0.7 — uv:latest (0.8+) resolves prereleases differently and may pick
# incompatible pre-built wheels. Validated working pin.
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project \
    && rm -rf /root/.cache

COPY container.py .
COPY copilot-instructions.md .
COPY skills/ skills/
COPY mcp-config.json .

EXPOSE 8088

CMD [".venv/bin/python", "container.py"]
```

**Key facts:**
- **Use MCR base images** — `mcr.microsoft.com/oryx/python:3.12` (no Docker Hub rate limits on ACR Tasks builds). Do NOT use `python:3.12-slim` from Docker Hub — ACR Tasks hits unauthenticated pull limits.
- Self-contained — NO base image dependency, NO ACR reference
- Uses **uv** (not pip) for dependency management — `prerelease = "if-necessary-or-explicit"` in pyproject.toml
- Port 8088 is the standard Foundry agent port
- `azd deploy` builds the container remotely — no local Docker needed
- `--platform linux/amd64` only needed for local builds (Foundry runs AMD64)
- Entrypoint is `.venv/bin/python` (uv creates a virtualenv)
- ResponsesHostServer handles liveness probes natively — no HEALTHCHECK needed

### 7. `deploy-notes.md` — Deployment Guide

````markdown
# Deployment Guide

## Architecture

This agent deploys as a **Microsoft Foundry Hosted Agent** using the
`azd ai agent` extension. `azd up` handles everything declaratively.

```
┌─────────────────────────────────────────┐
│  Azure Resources (provisioned by Bicep)  │
│  ┌─────────────────────────────────┐    │
│  │  Foundry Platform                │    │
│  │  ┌───────────────────────────┐  │    │
│  │  │  Hosted Agent Container    │  │    │
│  │  │  - src/agent/container.py  │  │    │
│  │  │  - copilot-instructions.md │  │    │
│  │  │  - skills/                 │  │    │
│  │  └──────────┬────────────────┘  │    │
│  │             │                    │    │
│  │  ┌──────────▼────────────────┐  │    │
│  │  │  MCP ACA (if needed)       │  │    │
│  │  └───────────────────────────┘  │    │
│  └─────────────────────────────────┘    │
│                                          │
│  ┌─────────────────────────────────┐    │
│  │  Bot ACA (Teams integration)     │    │
│  │  - bot.py → Foundry Hosted Agent │    │
│  └──────────┬──────────────────────┘    │
│             │                            │
│  ┌──────────▼──────────────────────┐    │
│  │  Azure Bot Service + Teams       │    │
│  └──────────────────────────────────┘    │
└─────────────────────────────────────────┘
```
````

## Prerequisites

- Azure CLI (`az`) and Azure Developer CLI (`azd`) installed
- `azd ai agent` extension: `azd extension install azure.ai.agents` (≥0.1.30-preview)
- Azure subscription with Contributor + **Azure AI Project Manager** on Foundry project
- Set `AZURE_TENANT_ID` in azd env: `azd env set AZURE_TENANT_ID <your-tenant-id>`

> **Posture-specific RBAC (production-bound).** If SPEC § 11f declares
> `deployment_target: production-bound` — or Phase 1.5 records the same in
> `specs/deployment-posture.md` — additional Azure RBAC may be required:
> **Network Contributor** on the VNet RG (for Private Endpoints),
> **Defender for Cloud Plan Administrator** (to enable Defender), and (when
> backup/DR is on) **Cosmos DB Backup Operator**. Confirm assignments
> before `azd up`; the production-grade checklist in Phase 1.5 lists the
> full set per chosen overrides.

> **Tenant hygiene before `azd up`.** If you work across multiple Azure
> tenants, set up per-tenant `AZURE_CONFIG_DIR` / `AZD_CONFIG_DIR` in
> the calling shell **first** — see the **`threadlight-tenant-isolation`**
> skill. Without isolation, an `azd up` here may silently deploy into
> whatever subscription another shell last `az account set` against.

> **Run [`threadlight-local-test` **Pattern 0 — Quickstart**](../../threadlight-local-test/SKILL.md)
> before `azd up`.** It exercises the same MAF `Agent + SkillsProvider`
> wiring against `specs/sample-data/*.json` stubs — no Cosmos, no MCP
> boot — so prompts/skills/tool calls are smoke-validated locally in
> minutes, before the 20-30 min deploy round-trip.

> **`azd` has its own auth chain — `az login` is not enough.**
> `azd ai agent show`, `azd deploy`, and the rest of the `azd` family
> use **`AzureDeveloperCliCredential`**, which reads tokens from
> `$AZD_CONFIG_DIR/auth/` — completely separate from the `az` CLI
> token cache under `$AZURE_CONFIG_DIR`. Even with both env vars
> pointed at the same alias, an `az login --tenant <id>` does **NOT**
> satisfy `azd`. You must run **`azd auth login --tenant-id <id>`**
> in addition to `az login`. Symptoms when missed: `azd ai agent show`
> hangs or returns `ERROR: not logged in. Try running 'azd auth
> login'`, even though `az account show` works fine. This is the #2
> "what changed?" trap after the multi-sub gotcha — bake both into
> your shell startup script.
>
> Verified working sequence (per-shell, after the alias env vars are
> exported per `threadlight-tenant-isolation`):
>
> ```bash
> az login --tenant "$TENANT_ID"
> az account set --subscription "$DEFAULT_SUB"
> azd auth login --tenant-id "$TENANT_ID"   # NOT optional
> az account show --query "{tenant:tenantId, sub:name}" -o table
> ```

## Deploy Steps

0. **Give the deploying user a Foundry data-plane role on the project (before the agent deploy):**
   Subscription/RG `Owner` or `Contributor` is control-plane only; creating the
   hosted agent version needs `Foundry User` (`53ca6127-db72-4b80-b1b0-d745d6d5456d`,
   formerly `Azure AI User`) or `Foundry Project Manager` at the project scope,
   otherwise the deploy fails with `403 Forbidden ERROR CODE: UserError`.
   Always assign roles by **GUID**: `az` 2.86 no longer resolves the name `Azure AI User`.
   Threadlight `azd-modules` grants `Foundry User` on the account when
   `deployingUserObjectId` is set. The official `azd ai agent init` template does
   not: run `azd provision`, then assign the role and wait for propagation:
   ```bash
   az role assignment create --role 53ca6127-db72-4b80-b1b0-d745d6d5456d \
     --assignee-object-id "$(az ad signed-in-user show --query id -o tsv)" \
     --assignee-principal-type User --scope "$(azd env get-value AZURE_AI_PROJECT_ID)"
   sleep 60   # observed propagation delay; then run step 1
   ```
   See `references/hosted-agent/ghcp/README.md` § "Deploying user".

1. **Deploy everything with one command:**
   ```bash
   azd env set AZURE_TENANT_ID <your-tenant-id>
   azd up
   ```
   The `azd ai agent` extension handles:
   - Provisions Azure resources (Foundry project, ACR, monitoring, Bot Service, ACA)
   - Deploys the model (declared in azure.yaml)
   - Builds the agent container remotely via ACR
   - Creates the hosted agent version in Foundry
   - **Auto-assigns `Azure AI User` to agent identity** (requires `Azure AI Project Manager` + `AZURE_TENANT_ID`)
   - Builds and deploys the Teams bot to ACA

2. **Test the agent:**
   ```bash
   azd ai agent invoke "Hello! What can you do?"
   ```
   Or open the Foundry portal → Agents → find your agent → Chat playground.

3. **If you get 401 errors after deploy:**
   The extension's postdeploy hook auto-assigns RBAC. If it failed (missing
   `AZURE_TENANT_ID`), manually assign roles — see Identity & RBAC reference.

4. **If the bot returns `server_error` in Teams:**
   Stale conversations from previous agent versions cause persistent `server_error`.
   Type `!reset` in the Teams chat, or the bot auto-retries with a fresh conversation.

5. **Grant deployer Cosmos data-plane RBAC for verification probes (mandatory if `cosmos-db` selected):**

   `azd up` grants the agent UAMI Cosmos data-plane access, but **NOT** the deployer
   principal. Every post-deploy verification probe — counting rows, inspecting
   `kyc-audit-log` to prove BR-XXX persistence, checking `kyc-cases` for stuck
   case_ids — fails `Forbidden — principal does not have RBAC permissions to
   perform action Microsoft.DocumentDB/databaseAccounts/readMetadata` until you
   manually grant it. **Verified across recent pilots.** Add this as the FIRST step
   of any post-deploy verification runbook:

   ```bash
   ME=$(az ad signed-in-user show --query id -o tsv)
   az cosmosdb sql role assignment create \
     -g "$AZURE_RESOURCE_GROUP" -a "$AZURE_COSMOS_ACCOUNT_NAME" \
     --role-definition-id 00000000-0000-0000-0000-000000000001 \
     --principal-id "$ME" --scope "/"
   sleep 25   # RBAC propagation
   ```

   Use role `00000000-0000-0000-0000-000000000001` = `Cosmos DB Built-in Data
   Reader` (read-only is sufficient for verification; for write probes use
   `00000000-0000-0000-0000-000000000002` = Data Contributor). For a permanent
   fix, add this to the `postdeploy.py` hook in `infra/scripts/`.

6. **Cosmos firewall pilot-posture (mandatory if `cosmos-db` selected) — see `threadlight-mcp-aca` SKILL.md § "Cosmos firewall + ACA egress" for the full runbook.** Quick check:

   ```bash
   az cosmosdb show -g "$AZURE_RESOURCE_GROUP" -n "$AZURE_COSMOS_ACCOUNT_NAME" \
     --query "{publicNetAccess:publicNetworkAccess,ipRules:ipRules,bypass:networkAclBypass}" -o json
   ```

   If `publicNetAccess: Disabled` OR `ipRules: []` (and ACA-side writes are
   failing), apply the runbook from `threadlight-mcp-aca`. The `threadlight-deploy/references/azd-modules`
   `cosmos-db.bicep` module defaults to `pilotPosture=true` to avoid this trap
   on fresh pilots; the `ipRules` array still needs the ACA egress IP added
   post-deploy (extracted from the `ca-*-cosmos-mcp-*` ACA logs).

## Bot Implementation Notes

> **See the `foundry-teams-bot` skill** for complete bot implementation — code patterns,
> file sending, user identity, Bicep wiring, re-provision safety, and troubleshooting.

Key rule: bot MUST use `get_openai_client(agent_name=...)` — NOT the old `agent_reference` pattern.

## Authentication

- **KEYLESS ONLY** — never use API keys for Azure services
- Each hosted agent gets a **dedicated Entra identity** at deploy time (platform-managed)
- `FoundryChatClient` / `CopilotClient` uses `DefaultAzureCredential` in the container
- **Shared UAMI for all other resources**: Bot ACA, MCP ACA, postprovision hooks, and
  any other deployed resource should share **one User-Assigned Managed Identity**:
  - Created by `infra/bot/uami.bicep` (or a shared `infra/identity/uami.bicep`)
  - Assigned to: Bot ACA, MCP ACA, and any other ACA/Function
  - `AZURE_CLIENT_ID` env var set on all ACAs pointing to the shared UAMI
  - RBAC: assign `Foundry User` (`53ca6127-db72-4b80-b1b0-d745d6d5456d`) on Foundry account + project, `Cognitive Services OpenAI User`,
    plus any data-plane roles (Cosmos, Search, etc.) to this single UAMI
- **Azure AI Project Manager** role required at project scope to deploy

> **Why one shared UAMI?** Multiple system-assigned MIs mean multiple RBAC assignments
> to manage. A single shared UAMI simplifies RBAC, Bicep, and troubleshooting —
> one identity, one set of role assignments, one place to debug auth failures.

## Health Probes

`ResponsesHostServer` handles liveness and readiness probes natively — no custom
middleware needed.

## Monitoring & Telemetry

The Bicep scaffold creates Application Insights + Log Analytics workspace when
`ENABLE_MONITORING=true` (default). But you also need to **connect AppInsights to
the Foundry project** for eval telemetry and agent tracing to work.

### What the scaffold provides

- `infra/core/monitor/` — creates Application Insights + Log Analytics workspace
- `APPLICATIONINSIGHTS_CONNECTION_STRING` — **RESERVED by the platform** for hosted
  agents. Do NOT set it in agent.yaml — the platform injects it automatically.

### What you must do manually (or via postprovision hook)

1. **Create an AppInsights connection on the Foundry ACCOUNT (not project):**

   The Foundry **account** needs an `AppInsights` connection so that agent traces
   and eval telemetry appear in the Foundry portal. This is NOT automatic.

   > **Key details:**
   > - Category is `AppInsights` (NOT `ApplicationInsights`)
   > - Target is the **ARM resource ID** (NOT the connection string)
   > - Metadata must include `ApiType: Azure`
   > - Connection is at **account level**, not project level

   ```bash
   # Via Azure REST API
   ACCOUNT_SCOPE="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>"
   APPINSIGHTS_ARM_ID="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Insights/components/<appinsights>"

   az rest --method PUT \
     --url "${ACCOUNT_SCOPE}/connections/<connection-name>?api-version=2025-10-01-preview" \
     --body '{
       "properties": {
         "category": "AppInsights",
         "target": "'${APPINSIGHTS_ARM_ID}'",
         "authType": "AAD",
         "metadata": {
           "ApiType": "Azure"
         }
       }
     }'
   ```

2. **RBAC for telemetry — ALL identities need access:**

   | Identity | Role | Scope | Why |
   |----------|------|-------|-----|
   | **Agent instance identity** | `Monitoring Metrics Publisher` | Application Insights | Agent container writes telemetry |
   | **Agent blueprint identity** | `Monitoring Metrics Publisher` | Application Insights | Platform internal telemetry |
   | **Project managed identity** | `Log Analytics Data Reader` | Log Analytics workspace | Read telemetry for evaluations |
   | **Shared UAMI** (bot, MCP) | `Log Analytics Data Reader` | Log Analytics workspace | Postdeploy hooks read telemetry |

   Get agent identities from `azd ai agent show` → `instance_identity.principal_id`
   and `blueprint.principal_id`. Assign to both.

   ```bash
   APPINSIGHTS_ID="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Insights/components/<appinsights>"

   # Agent identities (both instance + blueprint) — WRITE telemetry
   az role assignment create --assignee <INSTANCE_PRINCIPAL_ID> --role "Monitoring Metrics Publisher" --scope $APPINSIGHTS_ID
   az role assignment create --assignee <BLUEPRINT_PRINCIPAL_ID> --role "Monitoring Metrics Publisher" --scope $APPINSIGHTS_ID

   # Project MI — READ telemetry for evals
   az role assignment create --assignee <PROJECT_MI_PRINCIPAL_ID> --role "Log Analytics Data Reader" --scope $LOG_ANALYTICS_ID
   ```

### OpenTelemetry in container (MANDATORY — guarded init only)

> **Why this is mandatory, not optional.** The platform's
> `APPLICATIONINSIGHTS_CONNECTION_STRING` auto-injection is
> **best-effort, not contractual** — it can silently fail (for example,
> in some regions: AppIn account-level connection persisted
> with `credentials: null` after AAD-rejected → ApiKey-silent-drop;
> platform never injected the env var). When it fails AND `container.py`
> calls raw `configure_azure_monitor()` at the top of `main()`, the SDK
> raises `ValueError`, the container exits before `ResponsesHostServer`
> binds, and Foundry returns `server_error`/`model:""` on every smoke —
> with ZERO telemetry to debug it (because telemetry init is what
> crashed). The agent itself is fine; only the unguarded init kills it.
>
> **Hosted-agent `container.py` MUST wrap the init defensively** —
> use `init_telemetry()` from `threadlight-deploy/references/observability`'s
> `references/python/otel_init.py`, OR inline an 8-line equivalent
> that no-ops on missing env / SDK import / SDK raise. See the
> the threadlight-deploy observability reference (`references/observability`) (gap rows O-011 / O-012) for the
> full forensic and reference helper.

Add to `pyproject.toml`:

```toml
azure-monitor-opentelemetry>=1.6.4
opentelemetry-sdk>=1.27.0
```

And initialize in `container.py` — **guarded helper, NOT raw call**:

```python
# At module top
import logging, os
logger = logging.getLogger(__name__)

def _init_telemetry() -> None:
    """Guarded telemetry init — never lets a missing/broken AppIn kill the container."""
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.info("APPIN connection string not set — telemetry disabled (agent functional)")
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("App Insights telemetry init skipped: %s (agent functional)", exc)

def main() -> None:
    _init_telemetry()           # FIRST line — guarded, never raises
    # ... rest of main() ...
```

> **Anti-pattern (will silently kill your PoC):**
> `configure_azure_monitor()` as the first line of `main()` with no
> try/except. Causes the entire PoC to fail on any telemetry-platform
> glitch. The agent works fine without telemetry — let it.

> **Note:** The platform attempts to inject `APPLICATIONINSIGHTS_CONNECTION_STRING`
> into hosted agent containers automatically (best-effort) — do NOT declare
> it in `agent.yaml`, it is reserved. `create_version` rejects it with
> `invalid_request_error` (request IDs are available in platform logs).

## Evaluations

> **See the `microsoft-foundry` skill** for the complete evaluation guide — two-phase
> invoke+score pattern, 6 built-in evaluators, RBAC for judge models, and score interpretation.

### Generated eval files

If `specs/SPEC.md` § 9 contains evaluation scenarios (S-XXX), generate:

#### `tests/eval_dataset.jsonl`

One line per scenario, derived from spec § 9:

```jsonl
{"id": "S-001", "query": "Process loan: credit score 780, income $120K, amount $50K", "expected": "Approved", "business_rules": ["BR-001", "BR-003"], "category": "happy-path"}
{"id": "S-002", "query": "Process loan: credit score 520", "expected": "Declined", "business_rules": ["BR-001"], "category": "negative"}
{"id": "S-003", "query": "Process loan: credit score 580, DTI 40%", "expected": "Sent to human review", "business_rules": ["BR-001", "BR-002"], "category": "boundary"}
```

Each line maps directly to a scenario row in the spec. `query` is the canonical
input field; `prompt` is accepted as an alias (generate-only and
`run_evals.py` map it to `query`).

#### `tests/run_evals.py`

Invoke + score script using a two-phase pattern (invoke every scenario,
then score the saved responses with the official microsoft-foundry
`foundry-agent/observe` evaluators). **MUST** include three
Windows-survival defenses from day one — every PoC that has skipped them
has lost full eval batches mid-run (the Windows cp1252 console trap):

1. **UTF-8 stdout wrap** so the agent's `→` / em-dashes don't crash
2. **`safe()` ASCII-strip** on every agent string before printing
3. **Incremental JSON writes** after each scenario, not at the end

```python
"""Run eval scenarios against the deployed agent.

Windows-survival defenses baked in (DO NOT REMOVE):
- UTF-8 stdout wrap so the agent's non-ASCII output cannot crash us
- safe() ASCII-strip when printing agent strings
- Incremental JSON writes so a mid-run crash keeps prior results
"""
import io, json, sys, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from openai import APIError

PROJECT_ENDPOINT = "<from azd env>"
AGENT_NAME = "<from agent.yaml>"
RESULTS_PATH = Path("tests/eval-results.json")

def safe(s, n=None):
    if s is None:
        return ""
    if n:
        s = s[:n]
    return s.encode("ascii", errors="replace").decode("ascii")

project = AIProjectClient(endpoint=PROJECT_ENDPOINT,
                          credential=DefaultAzureCredential(),
                          allow_preview=True)
oai = project.get_openai_client(agent_name=AGENT_NAME)

dataset = [json.loads(line) for line in
           Path("tests/eval_dataset.jsonl").read_text(encoding="utf-8").splitlines()
           if line.strip()]

# Cold-start retry loop (microsoft-foundry warmup pattern)
for attempt in range(1, 5):
    try:
        w = oai.responses.create(input="Reply 'OK' if ready.", stream=False)
        if w.status == "completed":
            break
    except Exception as e:
        print(f"[warmup] attempt={attempt} EXC: {safe(str(e), 200)}")
    if attempt < 4:
        time.sleep(60)

results = []
RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)

for item in dataset:
    sid = item["id"]
    print(f"--- {sid} [{item.get('category', '')}] ---")
    t0 = time.time()
    rec = {"id": sid, **item}
    try:
        r = oai.responses.create(input=item["query"], stream=False)
        elapsed = time.time() - t0
        out_text = ""
        for o in (r.output or []):
            for c in (getattr(o, "content", None) or []):
                t = getattr(c, "text", None)
                if t:
                    out_text += t.value if hasattr(t, "value") else str(t)
        rec.update({"status": r.status, "elapsed_s": round(elapsed, 1),
                    "response_id": r.id, "output_len": len(out_text),
                    "output_first_400": safe(out_text, 400)})
        print(f"  status={r.status} elapsed={elapsed:.1f}s")
        print(f"  output_first_400: {safe(out_text, 400)!r}")
    except APIError as e:
        rec.update({"status": "api_error", "code": e.code,
                    "elapsed_s": round(time.time() - t0, 1)})
    except Exception as e:
        rec.update({"status": "exception", "exc_type": type(e).__name__,
                    "exc_msg": safe(str(e), 300),
                    "elapsed_s": round(time.time() - t0, 1)})
    results.append(rec)
    # Write after EVERY scenario - mid-run crash keeps prior results
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    time.sleep(5)

print(f"\n{len(results)} scenarios invoked. Run microsoft-foundry to score.")
```

Run with `python -X utf8 tests/run_evals.py` (or set `PYTHONUTF8=1` /
`PYTHONIOENCODING=utf-8` in the parent shell) for a 4th defense layer.

#### `tests/invoke_agent.py`

Simple smoke test — invoke the deployed agent with a single message. Copy
`references/hosted-agent/ghcp/references/invoke_agent.py` **and**
`references/hosted-agent/maf/references/python/operation_evidence.py` into
`tests/` (the script imports the helper; outside the skill layout it stops with
an explicit `ModuleNotFoundError` naming it). Each run needs a new, write-once
`INVOCATION_EVIDENCE_FILE`; an unset or existing path exits 1 before any token
is requested:

```bash
INVOCATION_EVIDENCE_FILE="$(mktemp -d)/invocation-evidence.jsonl" python tests/invoke_agent.py "<prompt>"
```
