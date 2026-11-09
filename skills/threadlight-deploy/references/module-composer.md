# Phase 6: Module composer (Bicep)

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when composing `infra/main.bicep` from SPEC § 11c.

## Phase 6: Module Composer (Bicep) — read SPEC § 11c, include exactly the right modules

Phase 5 above bootstraps the agent / azd skeleton. Phase 6 wires the
**process-specific infrastructure** by reading SPEC § 11c (Tech Stack module
selectors) and composing only the modules that process actually needs.

### Why a composer (not one big main.bicep)

Each of the 13 catalog processes uses a different mix of services:
- KYC needs `cosmos + search + foundry-iq + doc-intel + speech` (no event-grid)
- Order Fallout needs `cosmos + service-bus + aca-job + aca-mcp` (no doc-intel/speech)
- Supplier Risk needs `cosmos + foundry-iq + event-grid + storage-blob`

A monolithic `main.bicep` would be 70% `if` blocks. The composer pattern
includes **only the modules SPEC § 11c explicitly selects**, plus the
always-on baseline (`uami` + `acr` + `app-insights` + `foundry-account`).

### Step 1 — Read SPEC § 11c

The spec writes § 11c as the **canonical kebab-case selector table**
defined in `threadlight-design/references/speckit-template.md` § 11c.
Phase 6 reads that table verbatim — the rows are the source of truth
for module inclusion. Example excerpt:

```markdown
| Module             | Selected? | Purpose in this process                          |
| `cosmos-db`        | yes       | Persistent case state, audit log                 |
| `ai-search`        | yes       | Foundry IQ Knowledge Base backing                |
| `foundry-iq-index` | yes       | KB: kyc-policies (sources: blob/policies/kyc/)   |
| `azure-vision`     | yes       | Damage-photo classification                       |
| `aca-job`          | yes       | sla-watcher cron `*/15 * * * *`                  |
| `aca-mcp`          | yes       | customer-data MCP                                |
| `aca-bot`          | yes       | Teams bot for analyst HITL                       |
```

**Selector vocabulary is THE contract.** Use the kebab-case names from
the SPEC template verbatim — every other place in the toolchain
(`threadlight-deploy/references/azd-modules` Bicep module library, this composer) must match. Do
**not** invent a parallel YAML/camelCase namespace; that creates
silent no-ops where the composer doesn't recognize the SPEC's selector
and produces a Bicep tree missing the module the SPEC asked for.

### Step 1.5 — Apply SPEC implications

Per `speckit-template.md` § 11c, certain selectors imply others. Phase 6
**enforces** these silently — not failures, additions:

| When SPEC selects… | Composer auto-adds | Reason |
|--------------------|---------------------|--------|
| `aca-bot` | `aca-mcp` (if not already selected) | Bot needs MCP for case lookups |
| `event-grid` | `aca-job` (if not already selected) | Need a receiver for events |
| `service-bus` | `aca-job` (if not already selected) | Need a consumer for the queue |
| `foundry-iq-index` | `ai-search`, `storage-blob` | KB infra dependencies |

Surface the auto-additions in the composer's stdout so the user sees
``adding `aca-mcp` because `aca-bot` was selected'' — silent additions
that surprise the user are anti-pattern.

### Step 1.7 — Apply Phase 1.5 deployment posture (supported-now rows only)

Phase 6 reads `specs/deployment-posture.md` written by Phase 1.5 and
threads each **`supported-now`** override into the matching Bicep module
parameter (or the agent `azure.yaml`). Each **`deferred`** override
surfaces as a `<!-- TODO(posture): ... -->` comment in `main.bicep`
instead — never as a fake or no-op parameter that pretends the module
supports it.

| Posture row (from deployment-posture.md) | Target module / file | Parameter / token (supported-now) | Deferred? |
|------------------------------------------|----------------------|------------------------------------|-----------|
| `replicas: ha-min-replicas`              | `aca-bot.bicep`, `aca-mcp.bicep`, agent container | `minReplicas` / `maxReplicas` (verify per module) | no |
| `model_pinning: ga-pinned`               | `agent.yaml` / `azure.yaml` `config.deployments` | `__MODEL_VERSION__` token (substitute GA version) | no |
| `retention: regulated-7y`                | `app-insights.bicep` (Log Analytics linked) | `logAnalyticsRetentionDays` | no |
| `retention: customer-defined`            | `app-insights.bicep`                | `logAnalyticsRetentionDays` (operator-supplied integer) | no |
| `backup_dr: pitr+failover` (PITR slice)  | `cosmos-db.bicep` (when selected)   | `enableContinuousBackup: true` | no |
| `continuous_eval: scheduled`             | `microsoft-foundry` hook scaffold       | post-deploy script registration | no |
| `networking: private-required`           | `main.bicep` (top of file)          | — | **yes** → TODO comment block |
| `defender: on`                           | `main.bicep` (top of file)          | — | **yes** → TODO comment block |
| `cost_guardrails: budget+alerts`         | `main.bicep` (top of file)          | — | **yes** → TODO comment block |
| `backup_dr: pitr+failover` (failover)    | `main.bicep` (top of file)          | — | **yes** → TODO comment block |

When a row is marked `deferred`, write a comment block of this shape at
the top of `main.bicep` (right after the `targetScope = 'resourceGroup'`
declaration) so the operator sees it on review:

```bicep
// TODO(posture: production-bound, networking: private-required):
//   `specs/deployment-posture.md` declares `private-required` networking,
//   but this scaffold ships with public ingress. To complete the
//   private-network posture, follow the `threadlight-deploy/references/azd-modules` VNet + Private
//   Endpoints recipe (out of scope for the current threadlight-deploy
//   composer release). Recorded on 2026-05-23 by alex@partner.example.
```

This is the honesty contract — every deferred row in the posture file
MUST have a corresponding TODO block; the composer never silently swallows
a posture decision it can't implement.

### Step 2 — Resolve module set (canonical inclusion order)

**Always include** (not in § 11c selectors — these are baseline):
`uami → acr → app-insights → foundry-account`. Notice **`key-vault` is
NOT in the always-include set** — Threadlight pilots are keyless by
mandate (managed identity end-to-end). Only include `key-vault` when
SPEC § 11c explicitly selects `key-vault: yes` because the process
integrates with a customer-side service that demands a literal API key.

`cosmos-db` is the most common selection (case state, audit) but is
**conditional** — a stateless single-call agent doesn't need it.

Conditionally include based on SPEC § 11c selector rows:
- `cosmos-db: yes` → include `cosmos-db.bicep`
- `ai-search: yes` → include `ai-search.bicep`
- `foundry-iq-index: yes` → include `foundry-iq-index.bicep` (delegates to `foundry-iq` skill)
- `azure-vision`/`doc-intel`/`azure-speech` `: yes` → include corresponding modules (delegates to `azure-ai` skill)
- `event-grid`/`service-bus`/`storage-blob` `: yes` → include corresponding modules
- `aca-mcp: yes` → include `aca-mcp.bicep` per server (delegates to `threadlight-mcp-aca` skill)
- `aca-job: yes` → include `aca-job.bicep` per job (delegates to `threadlight-event-triggers` skill)
- `aca-bot: yes` → include `aca-bot.bicep` (delegates to `foundry-teams-bot` skill)

### Step 3 — Compose `infra/main.bicep`

> **Phase 5 vs Phase 6 — what each writes.** Phase 5 (`azd ai agent init`)
> stubs `infra/main.bicep` with the always-create baseline + agent
> definition extension hooks ONLY. Phase 6 **edits** that stub: it adds
> the `module foo 'modules/foo.bicep' = if (deployFoo) { ... }` blocks
> for the SPEC-selected modules and writes the corresponding files into
> `infra/modules/`. The stub from Phase 5 stays as the orchestrator;
> Phase 6 fills it in. **Never overwrite `main.bicep` from scratch in
> Phase 6** — that drops the agent extension wiring from Phase 5.
>
> In generate-only / pilot layout, Phase 5 writes the pilot's self-contained
> `infra/main.bicep` instead of an `azd ai agent init` stub. Phase 6 extends it
> the same way (adds `modules/*.bicep` blocks); env vars go on the inline agent
> service in `azure.yaml`, not in `agent.yaml`.

Generate `infra/main.bicep` as a thin orchestrator that calls each included
module in order, threads outputs through, and emits the env vars the agent
container needs. Every output the agent reads at runtime MUST appear in
`agent.yaml`'s `environment_variables:` block (with the correct schema —
see Step 4).

```bicep
// infra/main.bicep — extended by Phase 6 from the Phase 5 stub
module uami 'modules/uami.bicep' = { /* always */ }
module acr 'modules/acr.bicep' = { /* always */ }
module appInsights 'modules/app-insights.bicep' = { /* always — see threadlight-deploy observability reference (references/observability) for layers 2 + 3 */ }
module cosmos 'modules/cosmos-db.bicep' = if (deployCosmosDb) { /* ... */ }

module search 'modules/ai-search.bicep' = if (deployAiSearch) { /* ... */ }
module foundryIQ 'modules/foundry-iq-index.bicep' = if (deployFoundryIqIndex) {
  params: { searchService: search.outputs.serviceName, knowledgeBases: knowledgeBases }
}
module vision 'modules/azure-vision.bicep' = if (deployAzureVision) { /* ... */ }
// ... and so on for each selected module ...

module foundryAccount 'modules/foundry-account.bicep' = { /* always, last */ }

// Outputs surfaced to azd .env (consumed by agent.yaml via env-var substitution)
output AZURE_COSMOS_ENDPOINT string = deployCosmosDb ? cosmos.outputs.endpoint : ''
output AZURE_COSMOS_DATABASE string = deployCosmosDb ? cosmos.outputs.databaseName : ''
output AZURE_SEARCH_ENDPOINT string = deployAiSearch ? search.outputs.endpoint : ''
output AZURE_FOUNDRY_IQ_INDEX string = deployFoundryIqIndex ? foundryIQ.outputs.indexNames[0] : ''
output AZURE_VISION_DEPLOYMENT_NAME string = deployAzureVision ? vision.outputs.deploymentName : ''
```

### Step 4 — Wire outputs to `agent.yaml`

`agent.yaml` is the Foundry hosted-agent definition. Its
`environment_variables` field is a **list of `{name, value}` objects** (not
a flat dict), and the values are resolved by the **azd .env at agent-deploy
time**, not by Bicep interpolation. Phase 6 maps Bicep outputs into the
azd .env (Step 3 emits the `output` declarations above; azd populates them
into `.azure/<env>/.env` after `azd provision`); `agent.yaml` then
references them as `${VAR_NAME}`.

```yaml
# agent.yaml (Phase 6 amends)
environment_variables:
  - name: COSMOS_ENDPOINT
    value: ${AZURE_COSMOS_ENDPOINT}
  - name: COSMOS_DATABASE
    value: ${AZURE_COSMOS_DATABASE}
  - name: SEARCH_ENDPOINT
    value: ${AZURE_SEARCH_ENDPOINT}
  - name: FOUNDRY_IQ_INDEX
    value: ${AZURE_FOUNDRY_IQ_INDEX}
  - name: VISION_DEPLOYMENT_NAME
    value: ${AZURE_VISION_DEPLOYMENT_NAME}
  - name: MCP_CUSTOMER_DATA_URL
    value: ${AZURE_MCP_CUSTOMER_DATA_URL}
  # AZURE_CLIENT_ID is auto-injected by the agent-runtime when bound to a UAMI
  # APPLICATIONINSIGHTS_CONNECTION_STRING is auto-injected when an App Insights
  # resource is associated with the Foundry project — do NOT set it here.
```

> **Schema gotchas** observed in earlier rounds:
> - `environment_variables` is a list of `{name, value}` dicts, not a flat
>   key→value mapping. The Foundry hosted-agent schema validator rejects
>   the flat form silently in some preview revs.
> - You can't use Bicep interpolation (`${cosmos.outputs.endpoint}`)
>   inside `agent.yaml` — the agent control plane doesn't see Bicep
>   outputs. Always go via azd .env.
> - Don't set `APPLICATIONINSIGHTS_CONNECTION_STRING` — it's reserved
>   and auto-injected when the project + App Insights are associated.
>   Setting it manually causes telemetry collisions.

### Step 5 — Hook scripts (postprovision / postdeploy)

If SPEC § 11c selects modules that need post-provision wiring (e.g.,
`foundry-iq-index` needs to run a Knowledge Agent provisioning script
after AI Search is up; `aca-job` needs `publish_aca` to push the job
image; `aca-bot` needs the Teams manifest sideload), Phase 6 **merges**
its hook needs with whatever Phase 5 already wrote. Never overwrite an
existing `hooks:` block — that's how the Phase 5 Teams-manifest hook
gets clobbered. Read, merge, write.

The merged form chains `&&`-style with a single shell invocation per
hook to preserve order:

```yaml
# azure.yaml (after Phase 6 merge)
hooks:
  postprovision:
    shell: pwsh
    run: |
      cd infra/scripts && uv sync --frozen
      uv run bootstrap_foundry_iq.py
      uv run sideload_teams_manifest.py   # added in Phase 5
  postdeploy:
    shell: pwsh
    run: |
      cd infra/scripts && uv sync --frozen
      uv run publish_aca_jobs.py
      uv run publish_aca_mcp.py
```

> **`bootstrap_foundry_iq.py` MUST follow `foundry-iq/SKILL.md` § Bootstrap script: hardening checklist.**
> The seven-point checklist (no `az rest` for uploads, fail-fast on rc, key
> sanitization regex, content chunking for the 32766-byte term limit, RBAC
> propagation wait, post-upload count verification, idempotent recovery script)
> is mandatory — silent bootstrap failures here = empty index = silent agent
> quality regression that doesn't show up until evals run. The hosted-agent
> identity callout in that same skill file (blueprint + instance MIs need
> `Search Index Data Reader` directly; project / account MIs are insufficient)
> drives whether you also need a `postdeploy_grant_agent_search_rbac.py` hook
> — add it whenever the consumer is a Foundry hosted agent.

For more than two scripts, write a `infra/scripts/postdeploy.py`
dispatcher that invokes each subscript in order — that keeps `azure.yaml`
stable across spec changes. The dispatcher pattern is documented in
[`references/azd-modules/README.md`](azd-modules/README.md) § "Recommended: Python + `postdeploy` hook (cross-platform)".

These scripts are **vendored into the project** so they don't depend on
network access at deploy time. The factory shapes are defined by `threadlight-deploy/references/azd-modules`.

### Step 6 — Validation

Phase 6 ends with three checks (all must pass before Phase 7):

```bash
# 1. Compile check — catches schema errors before deploy
az bicep build --file infra/main.bicep

# 2. Preview the deployment plan against your azd env
azd provision --preview

# 3. Diagnose the project against the Foundry hosted-agent schema.
#    NOT `azd ai agent validate` — that subcommand does not exist and fails
#    with `unknown command`. See references/azd-cli-contract.md § 1.
#    Exit codes: 0 = a check passed and none failed, 1 = a check failed,
#    2 = everything was skipped (which is NOT success).
azd ai agent doctor
```

> **Running any `azd ai agent` command non-interactively?** Read
> [`references/azd-cli-contract.md`](azd-cli-contract.md) first. It
> pins the exact flags `--no-prompt` requires (`--runtime`, `--entry-point`,
> `--deploy-mode`), captured from the pinned toolchain rather than fetched from
> the internet at deploy time. Guessing `--runtime` is the most common first
> failure of an automated deploy.

> **The full Bicep module catalog** lives in [`references/azd-modules/README.md`](azd-modules/README.md) →
> "Composable Bicep Module Library". This skill orchestrates inclusion;
> the `references/azd-modules` reference owns the module shapes.
