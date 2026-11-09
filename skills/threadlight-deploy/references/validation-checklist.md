# Phase 3: Validation checklist

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it after generating files, before presenting them or running `azd up`.

## Phase 3: Validate & Auto-Review (mandatory)

After generating all files, **walk through this checklist item by item**. Every
generated file must be accounted for. This is the single most important step —
if you skip it, broken or missing files ship to the user.

### Output Checklist

Check every file. Mark each ✅ or fix before presenting.

#### `src/agent/` — Hosted agent container
- [ ] `src/agent/container.py` — exists, matches chosen runtime (GHCP or MAF)
- [ ] `src/agent/Dockerfile` — uses `mcr.microsoft.com/oryx/python:3.12`, copies all agent files; GHCP: `pip install --require-hashes -r requirements.lock` with `src/agent/requirements.lock` present; MAF inline template: `uv sync`
- [ ] `src/agent/pyproject.toml` — correct deps for chosen variant, `prerelease = "if-necessary-or-explicit"`
- [ ] `src/agent/copilot-instructions.md` — exists, 500-1500 words, matches AGENTS.md
- [ ] `src/agent/skills/` — has all skills from AGENTS.md, no extra, no missing
- [ ] `src/agent/config/` — process configuration from spec (if applicable)
- [ ] `src/agent/mcp-config.json` — only remote HTTP servers, includes mock MCP endpoints for mocked systems, no unresolved `${ENV_VAR}` placeholders
- [ ] Agent definition — **inline** in `azure.yaml` (agent service entry, `kind: hosted`) as in the GHCP pilot scaffold (`references/pilot`); no separate `agent.yaml` copies to keep in sync

#### `src/mcp/` — MCP server (if mocked systems or Cosmos)
- [ ] `src/mcp/server.py` — tools match spec § 6 contracts for mocked systems
- [ ] Sample data — `src/mcp/Dockerfile` copies `specs/sample-data/` into the image (`SAMPLE_DATA_DIR`), as in `references/pilot/mcp`
- [ ] `src/mcp/Dockerfile` — builds and runs the MCP server
- [ ] `src/mcp/requirements.lock` — `==` pinned, `--hash=sha256:` locked, includes `mcp`
- [ ] *(Skip this section entirely if no mocked systems and no Cosmos)*

#### `src/bot/` — Teams bot (if Teams needed)
- [ ] `src/bot/bot.py` — uses `get_openai_client(agent_name=...)` (NOT `agent_reference`)
- [ ] `src/bot/app.py` — aiohttp server with MsalConnectionManager
- [ ] `src/bot/Dockerfile` — `mcr.microsoft.com/oryx/python:3.12`, port 80
- [ ] `src/bot/requirements.txt` — includes microsoft-agents-* + openai
- [ ] `src/bot/build_manifest.py` — replaces all manifest tokens; **MUST fail loudly** if `BOT_APP_ID` env var is missing or still a placeholder (e.g. `<uami-client-id>`) — silent fallback to a placeholder produces a zip that passes `azd deploy` but fails Teams schema validation at sideload time with `String "<uami-client-id>" does not match regex pattern`. Guard: `if not bot_id or bot_id.startswith("<"): raise SystemExit("BOT_APP_ID not set")`
- [ ] `src/bot/teams_package/manifest.json` — has `__BOT_APP_ID__` placeholder tokens ready for postprovision (NEVER literal `<uami-client-id>` — use double-underscore `__` tokens that are obviously wrong if leaked)
- [ ] **Bot ACA env block has ALL FOUR `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*` vars** (CLIENTID, TENANTID, AUTHORITYENDPOINT, **AUTHTYPE=`UserManagedIdentity`**) — missing `AUTHTYPE` is a silent-killer bug: bot looks healthy from outside, JWT probe passes, every real Teams message returns HTTP 500 with AADSTS7000216. See `foundry-teams-bot` skill § Bicep snippet.
- [ ] **Bot Service `appType: UserAssignedMSI`** with `msaAppId` = UAMI clientId AND `msaAppMSIResourceId` = UAMI ARM ID
- [ ] *(Skip this section entirely if Teams not needed)*

#### Root config files (MUST be at repo root — azd requires this)

> **`azure.yaml` stays at repo root**, NOT under `src/`. The `azd` CLI and the
> `azd ai agent` extension look for it there. Only the *source code*
> (`container.py`, Dockerfile, skills, etc.) goes under `src/`. The
> `project: ./src/agent` field in `azure.yaml` tells azd where the Dockerfile is.
>
> **One agent definition, inline.** The canonical layout is the GHCP pilot
> scaffold (`references/hosted-agent/ghcp/references/pilot`). The hosted agent
> is declared once, inline on its `azure.yaml` service entry. Do not
> also emit `agent.yaml` or `src/agent/agent.yaml`: two definitions drift. If an
> interactive `azd ai agent init` run produced an `agent.yaml`, fold it into
> `azure.yaml` and delete the file.

- [ ] `azure.yaml` — inline hosted agent service: `host: azure.ai.agent`, `project: src/agent`, `kind: hosted`, `protocols` (invocations), `container.resources`, `uses: [ai-project]`; `FOUNDRY_PROJECT_ENDPOINT` only on the `azure.ai.project` service, never in the agent's environment
- [ ] `azure.yaml` — if `src/mcp/` exists: MCP service declared with `host: containerapp` and `docker.path: src/mcp/Dockerfile` (build context = repo root so `specs/sample-data/` is copied)
- [ ] `deploy-notes.md` / `DEPLOY.md` — references `azd up`, lists mock systems with swap instructions

#### `infra/` — Bicep scaffold
- [ ] `infra/main.bicep` — exists; self-contained pilot orchestrator from `references/pilot/infra` (no `infra/core/` tree), plus Phase 6 `infra/modules/` when the SPEC selects them
- [ ] No capability host is created for hosted agents: no `capabilityHosts` resource in `infra/`, and `ENABLE_CAPABILITY_HOST=false` wherever an `azd ai agent init` template exposes that parameter
- [ ] `bicep build infra/main.bicep` — compiles offline
- [ ] `infra/bot/` — present if Teams included, absent if not

#### `scripts/` — Hooks
- [ ] Postprovision/postdeploy hooks present if needed (Toolbox, RBAC, manifest)

#### `tests/` — Eval and smoke test
- [ ] `tests/invoke_agent.py` — smoke test script
- [ ] `tests/operation_evidence.py` — copied beside `invoke_agent.py` (its required import)
- [ ] `evals/eval_dataset.jsonl` — one line per spec § 9 scenario (if spec exists)
- [ ] `evals/run_evals.py` + `evals/eval-config.json` — Foundry eval: built-in evaluators + SPEC-derived rubric (if spec exists)
- [ ] `tests/postdeploy-manifest.json` — post-deploy checks for Phase 3.5

#### Global checks
- [ ] No secrets or API keys in any generated file
- [ ] No hardcoded local file paths
- [ ] Runtime variant (GHCP/MAF) consistent across container.py, pyproject.toml, Dockerfile
- [ ] All `__PLACEHOLDER__` tokens replaced with actual values
- [ ] Shared UAMI: one UAMI for bot + MCP ACA + hooks; `AZURE_CLIENT_ID` set on all ACAs
- [ ] AppInsights connection to Foundry project exists (or postprovision hook creates it)
- [ ] **`container.py` wraps telemetry init defensively** (no raw `configure_azure_monitor()` at module/main scope — use `_init_telemetry()` or `init_telemetry()` helper that no-ops on missing env / SDK import / SDK raise; see `threadlight-deploy/references/observability` gap O-011)

#### SPEC § 11c module-selector cross-check (MANDATORY)

> **Use the consolidated `threadlight-safe-check` skill** for the
> mechanical implementation of this check (and Phase 3.5 below). The
> manual matrix below is preserved for documentation; the canonical
> automation is:
>
> ```bash
> python3 tests/safe_check.py --phase pre-deploy
> ```
>
> Wire it as an `azd hooks predeploy` so missing services abort the
> docker build before wasting an ACR push.

> **Why this exists.** A recent investigation-style PoC shipped
> with `infra/bot/aca.bicep`, `infra/bot/bot-service.bicep`, and a
> `src/workspace/index.html` ` but `azure.yaml` only declared two
> services (`agent` + `mcp`), `infra/main.bicep` only wired `mcpApp`,
> and the workspace had no `Dockerfile`. Result: SPEC § 11c said
> `aca-bot: yes` and `aca-job: yes`; deployment ended up with **0 bot
> resources, 0 jobs, 0 workspace ACAs** ` and the deploy still
> reported success. `threadlight-safe-check --phase pre-deploy` would
> have caught this.

For **every `yes` row** in SPEC § 11c, walk this matrix:

| Selector | Must exist in `azure.yaml` services | Must be referenced from `infra/main.bicep` | Must have source under `src/` |
|---|---|---|---|
| `aca-mcp` | `host: containerapp`, `project: ./src/mcp` | `module mcpApp '<host>/container-app.bicep'` | `src/mcp/Dockerfile` + `server.py` |
| `aca-bot` | `host: containerapp`, `project: ./src/bot` | `module botAca 'bot/aca.bicep'` AND `module botService 'bot/bot-service.bicep'` | `src/bot/Dockerfile` + `bot.py` + `app.py` + `teams_package/manifest.json` |
| `aca-job` | `host: containerapp.job` (or postdeploy `az containerapp job create`) | `module job 'jobs/aca-job.bicep'` (or `core/host/container-app-job.bicep`) | `src/jobs/<name>/Dockerfile` + `main.py` (cron entrypoint) |
| `workspace-ui` (SPEC § 8b non-empty) | `host: containerapp`, `project: ./src/workspace` | `module workspaceAca 'core/host/container-app.bicep'` | `src/workspace/Dockerfile` + ACA-served HTML/SPA. **NOT just a static `index.html` opened from `file://`** ` see `threadlight-workspace-ui` Hosting section |
| `foundry-iq-index` | n/a (provisioned by `postprovision` hook) | `module knowledge 'modules/ai-search.bicep'` (the index) | `scripts/postprovision.py` calls `provision_knowledge_base()` |

For **every `yes` selector**, all three columns MUST be checked. If any
is missing: **STOP**, fix the gap, do not proceed to `azd up`.

#### Bicep-module orphan check (MANDATORY)

For every `infra/<dir>/*.bicep` file in the repo, run:

```bash
# From repo root
for f in $(find infra -name '*.bicep' -not -path '*/core/*' -not -path '*/modules/*'); do
    base=$(basename "$f" .bicep)
    if ! grep -q "module .*'.*$base\.bicep'" infra/main.bicep; then
        echo "ORPHAN: $f is not referenced from infra/main.bicep"
    fi
done
```

Orphan modules confuse future readers ("is this needed? was the deploy
broken?"). Either **wire them in** or **delete them**. No middle
ground. A recent PoC carried orphan
`infra/bot/aca.bicep` and `infra/bot/bot-service.bicep` for an entire
deploy cycle ` they looked deployed when reading `infra/`, but
weren't.

#### `src/`-folder orphan check (MANDATORY)

Mirror of the above for source code. For every `src/<dir>/`:

| Source folder | Must be declared in `azure.yaml` services | Action if not |
|---|---|---|
| `src/agent/` | `host: azure.ai.agent` | required ` always present |
| `src/mcp/` | `host: containerapp` (named `mcp`) | wire it OR delete `src/mcp/` |
| `src/bot/` | `host: containerapp` (named `bot`) | wire it OR delete `src/bot/` |
| `src/workspace/` | `host: containerapp` (named `workspace`) **AND** must have `Dockerfile` | wire it OR explicitly mark in SPEC § 8b as "demo-only static page" with no ACA hosting |
| `src/jobs/<name>/` | `host: containerapp.job` (named `<name>`) OR postdeploy `az containerapp job create` | wire it OR delete |

Same rule: orphan source folders are a deploy bug. Either ship them or
remove them.

**If any check fails:** fix it before presenting. Do not leave broken artifacts.
