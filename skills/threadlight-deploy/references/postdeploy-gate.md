# Phase 3.5: Post-deploy completeness gate

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it right after `azd up` and before announcing success.

## Phase 3.5: Post-deploy completeness gate (MANDATORY)

> ⚠️ **If `azd up` fails or the container doesn't respond, CHECK LOGS
> FIRST before retrying.** Run `az containerapp logs show -g <rg> -n <app>
> --type system --tail 20` for infrastructure events (port mismatch,
> probe failures, image pull errors) and `--type console` for application
> crashes. See `threadlight-deploy/references/azd-modules` § "ACA Container Debugging" for the full
> playbook. Never retry blind.

> **Canonical implementation: `threadlight-safe-check` skill.** Invoke as
> `python3 tests/safe_check.py --phase post-deploy` immediately after
> `azd up` returns 0 (and wire as `azd hooks postdeploy`). The detailed
> step-by-step below is preserved for understanding what the gate does;
> in practice run the consolidated CLI rather than reimplementing.

> **Why this is non-negotiable.** "PoC complete" is NOT the same as
> "`azd up` returned 0". It means **every Azure resource declared by
> SPEC § 11c is in `az resource list`, every channel declared by SPEC
> § 8 is reachable, and every scheduled job is running**. Without
> this gate, a recent PoC was reported "deployed
> and evaluated" with `aca-bot`, `aca-job`, and `workspace-ui` all
> silently missing. Run this gate **before** announcing success.

### Step 1 ` capture deployed state

```bash
# Make sure threadlight-tenant-isolation env vars are set first
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
az resource list -g "$RG" \
   --query "[].{type:type, name:name}" -o json > tests/deployed-resources.json
az containerapp list -g "$RG" \
   --query "[].{name:name, fqdn:properties.configuration.ingress.fqdn, state:properties.runningStatus}" \
   -o json > tests/deployed-containerapps.json
az containerapp job list -g "$RG" \
   --query "[].{name:name, schedule:properties.configuration.scheduleTriggerConfig.cronExpression}" \
   -o json > tests/deployed-jobs.json
```

### Step 2 ` build expected list from SPEC

For every `yes` row in SPEC § 11c, look up the expected resource
type(s):

| Selector | Expected `Microsoft.*` resource types |
|---|---|
| `foundry-account` | `Microsoft.CognitiveServices/accounts` (account + nested project) |
| `cosmos-db` | `Microsoft.DocumentDB/databaseAccounts` |
| `ai-search` | `Microsoft.Search/searchServices` |
| `app-insights` | `Microsoft.Insights/components` + `Microsoft.OperationalInsights/workspaces` |
| `acr` | `Microsoft.ContainerRegistry/registries` |
| `uami` | `Microsoft.ManagedIdentity/userAssignedIdentities` |
| `aca-environment` | `Microsoft.App/managedEnvironments` |
| `aca-mcp` | `Microsoft.App/containerApps` (1 named `*-mcp-*` or `ca-mcp-*`) |
| `aca-bot` | `Microsoft.App/containerApps` (1 named `*-bot-*`) **AND** `Microsoft.BotService/botServices` |
| `aca-job` | `Microsoft.App/jobs` (1 per cron entry) |
| `workspace-ui` | `Microsoft.App/containerApps` (1 named `*-workspace-*` or `*-ui-*`) |
| `event-grid` | `Microsoft.EventGrid/topics` (or `systemTopics`) |
| `service-bus` | `Microsoft.ServiceBus/namespaces` |
| `key-vault` | `Microsoft.KeyVault/vaults` (only if explicitly `yes` ` keyless-by-default) |
| `storage-blob` | `Microsoft.Storage/storageAccounts` |
| `foundry-iq-index` | `Microsoft.Search/searchServices` (named `*-iq-*`) AND `azd env get-value FOUNDRY_IQ_KB_NAME` resolves |

### Step 3 ` diff and assert

```python
# tests/postdeploy_gate.py ` make this part of the deploy script.
import json, sys
from pathlib import Path

deployed = json.loads(Path("tests/deployed-resources.json").read_text())
deployed_types = {r["type"] for r in deployed}

# Build expected from SPEC § 11c. Hand-maintain this list per process,
# or read it from specs/manifest.json -> deployment_manifest.expected_resource_types
expected = {
    "Microsoft.CognitiveServices/accounts",
    "Microsoft.DocumentDB/databaseAccounts",
    "Microsoft.Search/searchServices",
    "Microsoft.App/managedEnvironments",
    "Microsoft.App/containerApps",     # mcp + bot + workspace
    "Microsoft.App/jobs",              # deadline-watcher cron
    "Microsoft.BotService/botServices",
    "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.ContainerRegistry/registries",
    "Microsoft.Insights/components",
}

missing = expected - deployed_types
if missing:
    print(f"GAP: missing resource types: {missing}")
    sys.exit(1)

# Per-app instance checks for the ACAs (counts matter ` 3 ACAs expected)
acas = json.loads(Path("tests/deployed-containerapps.json").read_text())
aca_names = {a["name"] for a in acas}
required_aca_patterns = {"mcp": False, "bot": False, "workspace": False}
for n in aca_names:
    for k in required_aca_patterns:
        if k in n.lower(): required_aca_patterns[k] = True
unmet = [k for k, v in required_aca_patterns.items() if not v]
if unmet:
    print(f"GAP: missing required ACA roles: {unmet}")
    sys.exit(1)

print("OK - post-deploy completeness gate passed")
```

### Step 4 ` channel reachability

For every Human Interaction channel in SPEC § 8, run a smoke check:

```bash
# Workspace UI ` HTTP 200 on the FQDN
WORKSPACE_FQDN=$(jq -r '.[] | select(.name | contains("workspace")) | .fqdn' tests/deployed-containerapps.json)
[ -n "$WORKSPACE_FQDN" ] && curl -fsSL "https://$WORKSPACE_FQDN/" -o /dev/null && echo "workspace OK"

# Bot ` ACA running + Bot Service registered
BOT_NAME=$(jq -r '.[] | select(.name | contains("bot")) | .name' tests/deployed-containerapps.json)
[ -n "$BOT_NAME" ] && az containerapp show -g "$RG" -n "$BOT_NAME" --query properties.runningStatus -o tsv

# Scheduled jobs ` cron expression matches SPEC § 10b
az containerapp job list -g "$RG" --query "[].{name:name, schedule:properties.configuration.scheduleTriggerConfig.cronExpression}" -o table
```

### Step 5 ` write the gate result

Persist `tests/postdeploy-manifest.json`:

```json
{
  "deployed_at": "2026-05-10T22:30:00Z",
  "rg": "rg-<your-process>-poc",
  "checked_selectors": ["foundry-account", "cosmos-db", "ai-search", "aca-mcp", "aca-bot", "aca-job", "workspace-ui"],
  "deployed_resources": ["` types ` "],
  "channels": [
    { "name": "Analyst Workspace", "fqdn": "ca-workspace-`.`.azurecontainerapps.io", "status": "OK" },
    { "name": "Teams adaptive card", "bot_name": "ca-bot-`", "status": "OK" }
  ],
  "scheduled_jobs": [
    { "name": "deadline-watcher", "schedule": "*/15 * * * *", "status": "OK" }
  ],
  "gaps": []
}
```

> **`gaps` MUST be empty for "PoC complete".** If non-empty, the
> deploy is incomplete ` either fix the gap (preferred) or update
> SPEC § 11c to flip the selector to `no` with a documented reason
> ("scheduled job deferred to v2"). Silently shipping with gaps is
> the failure mode this whole gate exists to prevent.

### Anti-pattern: "the agent runs in the portal so we're done"

The PoC is **NOT done** when:
- Only the hosted agent + 1 MCP ACA are deployed but SPEC § 11c
  declared more (`aca-bot`, `aca-job`, `workspace-ui`).
- The smoke probe / eval invokes the agent successfully but the
  agent's deployed surface area doesn't match SPEC § 8 channels.
- Bicep modules are present in `infra/` but not wired into
  `main.bicep` (orphans).
- Source folders exist under `src/` but aren't declared in
  `azure.yaml` services.
- `tests/postdeploy-manifest.json` doesn't exist or has non-empty
  `gaps[]`.

If any of the above is true, the PoC is partial. Communicate that
honestly to the user (with the gap list) instead of declaring
victory.
