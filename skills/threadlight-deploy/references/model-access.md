# Phase 1e: Model access pattern

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when the agent uses an existing model through an AI Gateway (APIM).

#### 1e. Choose model access pattern

| Pattern | When to Use | How |
|---------|------------|-----|
| **Direct deployment** (default) | You deploy the model in your own Foundry project | `azure.yaml` `config.deployments` — model created by `azd up` |
| **AI Gateway (APIM)** | Use an existing model on another Foundry resource, or a shared/governed model pool | `ApiManagement` connection in the Foundry project → APIM routes to backend AI Services |

**Use AI Gateway when:**
- Customer has existing model deployments they want to reuse
- A shared model pool is managed centrally (e.g., Citadel hub)
- Governance requires routing through APIM (logging, rate limiting, policies)
- You need models from a different Azure region or subscription

> **See `microsoft-foundry` skill** for the full AI Gateway setup —
> APIM connection creation, `connectionName/deploymentName` pattern,
> Bicep for managed connections, and troubleshooting.

When using AI Gateway:
- **Remove** the model from `azure.yaml` `config.deployments` (it's already deployed elsewhere)
- Set `MODEL_DEPLOYMENT_NAME` in `agent.yaml` to `connectionName/deploymentName`
- Ensure the Foundry project has an `ApiManagement` connection to the APIM gateway
- **Use `authType: "AAD"` (recommended)** — no Key Vault needed. ApiKey auth requires a Key Vault on the project, which our Bicep scaffold doesn't create.
- Works with both GHCP SDK (BYOK) and MAF (FoundryChatClient) — routing is transparent

> **See `threadlight-deploy/references/hosted-agent/ghcp` reference** for the full GHCP reference (container.py template,
> pyproject.toml, agent.yaml, invocation patterns, troubleshooting).
> **See `threadlight-deploy/references/hosted-agent/maf` reference** for the full MAF reference.
