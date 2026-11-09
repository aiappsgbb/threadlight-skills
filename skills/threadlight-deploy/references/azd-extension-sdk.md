# SDK deployment via the `azd ai agent` extension

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when you need the extension `azure.yaml` shape or a manual agent deployment.

## Reference: SDK Deployment (via `azd ai agent` Extension)

The `azd ai agent` extension (`azure.ai.agents >= 0.1.0-preview`) handles hosted agent
deployment declaratively. No custom deploy scripts are needed.

### How it works

1. **azure.yaml** declares `host: azure.ai.agent` for the agent service
2. **Model deployments** are declared in `config.deployments` (not in Bicep)
3. **Pre-provision hooks** set env vars: `ENABLE_HOSTED_AGENTS`, `AI_PROJECT_DEPLOYMENTS`
4. **azd up** runs: provision → deploy model → build container (ACR remote) → create agent

```yaml
# azure.yaml — agent + MCP service declaration
services:
  my-agent:
    project: ./src/agent
    host: azure.ai.agent
    language: docker
    docker:
      remoteBuild: true
    config:
      container:
        resources:
          cpu: "1"
          memory: 2Gi
      deployments:
        - model:
            format: OpenAI
            name: gpt-5.4-mini
            version: "2026-03-17"
          name: gpt-5.4-mini
          sku:
            capacity: 120
            name: GlobalStandard

  # MCP server as ACA (if src/mcp/ exists — mock or Cosmos)
  mcp:
    project: ./src/mcp
    host: containerapp
    language: python
    docker:
      path: ./src/mcp/Dockerfile
      context: ./src/mcp
      remoteBuild: true
```

> **If `src/mcp/` exists**, the MCP ACA service MUST be in azure.yaml — otherwise
> `azd up` won't build or deploy it and the agent has no MCP tools at runtime.
> Set `MCP_SERVER_FQDN` in agent.yaml env vars to `${SERVICE_MCP_FQDN}` (azd
> resolves this to the deployed ACA's FQDN after provisioning).
>
> **MCP ACA also needs:**
> - Shared UAMI assigned (for `DefaultAzureCredential` inside the MCP server)
> - `AcrPull` role on the ACR for the UAMI (so ACA can pull the container image)
> - `minReplicas: 1` in Bicep (cold start from scale-to-0 adds 200-500s latency)

### Install the extension

```bash
azd extension install azure.ai.agents
```

### Manual agent deployment (alternative)

If not using the extension, you can deploy manually using the Foundry SDK:

```python
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    HostedAgentDefinition,
    ProtocolVersionRecord,
    AgentProtocol,
)
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True)

definition = HostedAgentDefinition(
    container_protocol_versions=[
        ProtocolVersionRecord(
            protocol=AgentProtocol.RESPONSES,
            version="1.0.0",
        )
    ],
    cpu="1",
    memory="2Gi",
    image=f"<acr>.azurecr.io/<agent>:{unix_timestamp}",
    environment_variables={
        # NOTE: FOUNDRY_PROJECT_ENDPOINT is RESERVED — platform auto-injects it
        "AZURE_AI_PROJECT_ENDPOINT": "https://...",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-5.4",  # default for production agents; gpt-5.4-mini only for trivial 1-2-step flows
    },
)

version = client.agents.create_version(
    agent_name="agent-my-process",
    definition=definition,
    metadata={"enableVnextExperience": "true"},
)
```

> **Image tags MUST be unique** (use unix timestamp). Foundry deduplicates
> `create_version` when the tag matches a previous version.
> **`metadata={"enableVnextExperience": "true"}`** is required for the refreshed preview.
