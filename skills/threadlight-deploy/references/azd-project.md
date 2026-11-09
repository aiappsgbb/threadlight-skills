# Phase 5: azd project (extension-based scaffold)

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when generating the azd project skeleton.

## Phase 5: Generate azd Project (Extension-Based Scaffold)

Generate a complete `azd up`-ready project using the **`azd ai agent` extension**
(`azure.ai.agents >= 0.1.0-preview`). The extension handles container build, model
deployment, and hosted agent creation declaratively — no custom deploy scripts needed.

The scaffold uses **vendored Bicep modules** from the official
[azd-ai-starter-basic](https://github.com/Azure-Samples/azd-ai-starter-basic)
template. This ensures correct resource structure for the extension while remaining
self-contained (no network dependency on the template repo).

### Step 1: Generate the base project

> **Generate-only and canonical layout.** In [generate-only mode](../SKILL.md#generate-only-mode)
> this step does NOT run `azd ai agent init`. `scripts/generate_only.py` writes
> the skeleton from the vendored GHCP pilot scaffold (`references/pilot`): an
> `azure.yaml` with the hosted agent defined **inline**, and a self-contained
> `infra/main.bicep` + `main.parameters.json`. It writes no `agent.yaml` and no
> `infra/core/`. When the pilot layout is used, the `agent.yaml` and `core/`
> references below do not apply: environment variables live on the inline
> agent service in `azure.yaml`. Never mix the two layouts in one repository.

Generate the `azd`-ready skeleton with the **`azd ai agent` extension**
(`azd ai agent init` — `azure.ai.agents >= 0.1.0-preview`; see
`references/upstream-pin.md` for the pinned `azd-ai-starter-basic` SHA). The
extension emits `azure.yaml`, `agent.yaml`, and the vendored `infra/`
(`main.bicep`, `main.parameters.json`, and `core/` — Bicep modules vendored from
the starter, **do not modify**). Phase 2's `src/agent/` runtime files and any
`src/mcp/` · `src/bot/` services then slot into this skeleton. The full layout
after Phase 2 (and the optional bot in Phase 4):

```
project/
├── agent.yaml                # Agent definition (ContainerAgent schema)
├── azure.yaml                # azd config — extension declares agent + bot services
├── src/
│   ├── agent/                # Phase 2 files go here
│   │   ├── container.py
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── copilot-instructions.md
│   │   ├── skills/
│   │   ├── config/
│   │   └── mcp-config.json
│   │
│   ├── mcp/                  # Mock/Cosmos MCP server (if needed)
│   │   ├── server.py
│   │   ├── data/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   └── bot/                  # Teams bot (optional)
│       ├── bot.py
│       ├── app.py
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── build_manifest.py # Builds copilot_package.zip for sideloading
│       └── teams_package/
│           ├── manifest.json
│           ├── color.png
│           └── outline.png
│
├── infra/
│   ├── main.bicep
│   ├── main.parameters.json
│   ├── abbreviations.json
│   ├── bot/                  # Teams bot infrastructure (optional)
│   │   ├── uami.bicep
│   │   ├── aca.bicep
│   │   ├── bot-service.bicep
│   │   └── fetch-container-image.bicep
│   └── core/                 # Vendored from azd-ai-starter-basic (DO NOT MODIFY)
│       ├── ai/
│       ├── host/
│       ├── monitor/
│       ├── search/
│       └── storage/
│
├── scripts/                # Infra hooks only (postprovision, postdeploy)
│
└── src/bot/                # build_manifest.py lives with bot code
```

### Step 2: Replace placeholder tokens

Replace these tokens in the generated and Phase 2 files:

| Token | Value | Source | Files |
|-------|-------|--------|-------|
| `__PROJECT_NAME__` | kebab-case agent name (e.g., `tech-news-digest`) | AGENTS.md | `agent.yaml`, `azure.yaml`, `src/bot/bot.py`, `pyproject.toml` |
| `__AGENT_DESCRIPTION__` | One-line agent description | AGENTS.md | `agent.yaml` |
| `__AGENT_NAME__` | Display name (e.g., `Tech News Digest`) | AGENTS.md | `infra/bot/bot-service.bicep` |
| `__MODEL_NAME__` | Model name (default: `gpt-5.4`) | AGENTS.md or default | `azure.yaml` |
| `__MODEL_DEPLOYMENT_NAME__` | Model deployment name (default: same as `__MODEL_NAME__`) | AGENTS.md or default | `agent.yaml` |
| `__MODEL_VERSION__` | Model version — **must match model name** (see lookup table below) | Azure model catalog | `azure.yaml` |
| `__MODEL_CAPACITY__` | TPM capacity (default: `120`) | Default | `azure.yaml` |
| `__DEVELOPER_NAME__` | Developer/org name for Teams manifest | User/org | `src/bot/teams_package/manifest.json` |
| `__BOT_APP_ID__` | UAMI client ID for bot (replaced at postprovision) | Bicep output | `src/bot/teams_package/manifest.json` |

> **⚠️ `__BOT_APP_ID__` silent-placeholder trap.** The postprovision hook runs
> `build_manifest.py` which reads `BOT_APP_ID` from the environment. `azd`
> injects Bicep outputs as env vars during hooks, so this works during `azd up`.
> But if anyone runs the script **manually** (e.g., `python scripts/build_manifest.py`
> without first exporting the var), a silent fallback like `os.environ.get("BOT_APP_ID",
> "<uami-client-id>")` produces a manifest that:
> - Passes `azd deploy` without error (the zip is valid, just has wrong IDs)
> - Fails at Teams sideload with: `String "<uami-client-id>" does not match regex
>   pattern "^[0-9a-fA-F]{8}-..."` on `id`, `copilotAgents[0].id`, and `bots[0].botId`
>
> **Fix:** `build_manifest.py` MUST raise `SystemExit` if `BOT_APP_ID` is empty or
> starts with `<`. Never use a human-readable fallback for UUID fields — use
> `00000000-0000-0000-0000-000000000000` if you must have a default (it's obviously
> wrong but passes the regex), or better, just fail.

> **Note**: Model deployment is now declared in `azure.yaml` `config.deployments` —
> NOT in Bicep. The `azd ai agent` extension handles model creation via pre-provision hooks.

#### Model Version Lookup

> **See the `threadlight-deploy/references/hosted-agent/maf` reference** for the complete model version lookup table.

Verify with: `az cognitiveservices account list-models --resource-group <rg> --name <account> -o table`

### Step 3: Generate `deploy-notes.md`

Generate the deployment guide using the template in Phase 2 § 7, replacing
`{Agent Name}` with the actual agent display name.

### How `azd up` works with the extension

```
azd up
  ├── azd provision → Bicep creates Azure resources
  │   ├── Foundry AI Account + Project (core/ai/ai-project.bicep)
  │   ├── Azure Container Registry (created by ai-project when hosted agents enabled)
  │   ├── Application Insights + Log Analytics (core/monitor/)
  │   ├── UAMI for bot (bot/uami.bicep)
  │   ├── ACA Environment + Bot Container App (bot/aca.bicep)
  │   └── Azure Bot Service + MsTeamsChannel (bot/bot-service.bicep)
  │
  ├── postprovision hook → builds Teams manifest package
  │   └── scripts/build_manifest.py → src/bot/copilot_package.zip
  │
  ├── azd ai agent extension (automatic) →
  │   ├── Deploys model from azure.yaml config.deployments
  │   ├── Builds agent container remotely via ACR
  │   └── Creates hosted agent version in Foundry
  │
  └── azd deploy → builds other containers, deploys to ACA
      ├── Builds src/mcp/ container via ACR (if MCP service declared)
      ├── Builds src/bot/ container via ACR (if bot service declared)
      └── Deploys to ACA (external ingress)
```

> **Post-provision rule (mandatory).** After running `azd provision` — whether
> to add a new service, update Bicep, or change parameters — ALWAYS run
> `azd deploy` for **ALL services**, not just the new one. Reason: Bicep
> modules declare ACA containers with a placeholder image
> (`mcr.microsoft.com/azuredocs/containerapps-helloworld`). Provision
> resets every ACA to that placeholder unless the module uses the
> `fetch-container-image` pattern (see `threadlight-deploy/references/azd-modules` reference). Running
> `azd deploy` (no service argument) rebuilds and deploys all services,
> restoring the correct images.
>
> **Battle-scar.** `azd provision` to add a workspace ACA
> silently reset the Teams bot and MCP server to the helloworld page.
> The bot stopped responding in Teams the night before the demo.

### Complete output structure

After all phases, the project should contain:

```
project/                    # ← REPO ROOT
├── AGENTS.md               # Original design (unchanged)
├── specs/                   # SpecKit (from threadlight-design, unchanged)
│
├── agent.yaml              # ⚠️ MUST be at root AND copied to src/agent/
├── azure.yaml              # ⚠️ MUST be at root — azd reads this
│
├── src/
│   ├── agent/              # Hosted agent container
│   │   ├── container.py    # Runtime (GHCP default or MAF fallback)
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── copilot-instructions.md
│   │   ├── skills/         # Process skills
│   │   ├── config/         # Process configuration
│   │   └── mcp-config.json # Runtime MCP config
│   │
│   ├── mcp/                # Mock/custom MCP server (if mocked systems or Cosmos)
│   │   ├── server.py       # FastMCP tools backed by sample data
│   │   ├── data/           # Copied from specs/sample-data/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   └── bot/                # Teams bot (optional)
│       ├── bot.py
│       ├── app.py
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── build_manifest.py  # Builds copilot_package.zip
│       └── teams_package/  # Manifest + icons
│
├── infra/                  # Bicep scaffold
│   ├── main.bicep
│   ├── main.parameters.json
│   ├── core/               # Vendored from azd-ai-starter-basic
│   └── bot/                # Bot infra (optional)
│
├── scripts/                # Infra hooks only (postprovision, postdeploy)
│
├── tests/                  # Test/invocation scripts
│   ├── invoke_agent.py     # Smoke test — invoke deployed agent
│   └── operation_evidence.py  # Evidence helper imported by invoke_agent.py
│
└── deploy-notes.md         # Deployment guide
```
