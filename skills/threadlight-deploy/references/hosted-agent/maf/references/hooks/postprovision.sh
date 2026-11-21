#!/bin/sh
# azd postprovision hook: grant the data-plane roles that evaluation, evaluation
# rules and Foundry Insights need, so `azd ai eval generate` works right after a
# fresh `azd up` with `infra.provider: microsoft.foundry`, without manual RBAC.
#
# The provider creates the account, project, App Insights and Log Analytics but
# grants the project managed identity only AcrPull, and gives the deploying user
# no Foundry data-plane role (live, Nov 2026: `azd ai eval generate --evaluator`
# failed with 401 "lacks ... AIServices/assets/write" for the user, then
# "Model authentication or authorization failed" for the project identity).
#
# Roles are assigned by role definition ID, never by display name: "Azure AI
# User" was renamed "Foundry User" and the old name no longer resolves; the IDs
# did not change. Sources:
#   https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-permissions
#   https://learn.microsoft.com/azure/foundry/observability/how-to/agent-insights
#
# Project managed identity:
#   Foundry User                   on the Foundry account (evaluation rules; model
#                                  inference needs account scope, not project only)
#   Cognitive Services OpenAI User on the account hosting the judge/analysis model
#                                  (THREADLIGHT_JUDGE_ACCOUNT_ID, default: this account)
#   Monitoring Reader              on the connected App Insights and its Log
#                                  Analytics workspace (Insights, trace evaluation);
#                                  Privileged Monitoring Data Reader instead when
#                                  THREADLIGHT_PROTECTED_GENAI_CONTENT=true
# Deploying identity (signed-in user, or AZURE_PRINCIPAL_ID + AZURE_PRINCIPAL_TYPE):
#   Foundry User                   on the Foundry account (azd ai eval)
#   Set THREADLIGHT_SKIP_DEPLOYER_ROLE=true when a pipeline grants it elsewhere.
#
# Idempotent: an assignment that already exists for the same principal, role
# ID and scope is reported and skipped, so a re-run creates nothing.
# Role assignments take a few minutes to propagate before the first run.
set -eu

ROLE_FOUNDRY_USER=53ca6127-db72-4b80-b1b0-d745d6d5456d
ROLE_OPENAI_USER=5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
ROLE_MONITORING_READER=43d0d8ad-25c7-4714-9337-8ba259a9fe05
ROLE_PRIVILEGED_MONITORING_READER=dbc9c667-e97f-4491-aee6-90b9cf960190

PROJECT_ID="${AZURE_AI_PROJECT_ID:?AZURE_AI_PROJECT_ID missing from azd env}"
ACCOUNT_ID="${PROJECT_ID%/projects/*}"
JUDGE_ACCOUNT_ID="${THREADLIGHT_JUDGE_ACCOUNT_ID:-$ACCOUNT_ID}"
APPI_ID="${AZURE_APPLICATION_INSIGHTS_RESOURCE_ID:-${APPLICATIONINSIGHTS_RESOURCE_ID:-}}"
LAW_ID="${AZURE_LOG_ANALYTICS_WORKSPACE_RESOURCE_ID:-}"
SLEEP="${THREADLIGHT_HOOK_RETRY_SLEEP:-10}"
TELEMETRY_ROLE="$ROLE_MONITORING_READER"
if [ "${THREADLIGHT_PROTECTED_GENAI_CONTENT:-false}" = "true" ]; then
  TELEMETRY_ROLE="$ROLE_PRIVILEGED_MONITORING_READER"
fi

# assign <principal-id> <principal-type> <role-id> <scope>
assign() {
  count=$(az role assignment list --scope "$4" --role "$3" \
    --query "length([?principalId=='$1'])" -o tsv)
  if [ "${count:-0}" != "0" ]; then
    echo "postprovision: role $3 already present for $1 at $4"
    return 0
  fi
  az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" \
    --role "$3" --scope "$4" -o none
  echo "postprovision: granted role $3 to $1 at $4"
}

mi=""
for attempt in 1 2 3 4 5 6; do
  mi=$(az rest --method get --url "https://management.azure.com${PROJECT_ID}?api-version=2025-06-01" \
    --query identity.principalId -o tsv 2>/dev/null || true)
  [ -n "$mi" ] && [ "$mi" != "None" ] && break
  mi=""
  sleep "$SLEEP"
done
if [ -z "$mi" ]; then
  echo "postprovision: the Foundry project has no system-assigned managed identity" >&2
  exit 1
fi

assign "$mi" ServicePrincipal "$ROLE_FOUNDRY_USER" "$ACCOUNT_ID"
assign "$mi" ServicePrincipal "$ROLE_OPENAI_USER" "$JUDGE_ACCOUNT_ID"
for scope in "$APPI_ID" "$LAW_ID"; do
  if [ -n "$scope" ]; then
    assign "$mi" ServicePrincipal "$TELEMETRY_ROLE" "$scope"
  fi
done
if [ -z "$APPI_ID" ]; then
  echo "postprovision: no App Insights resource in the azd env; Insights and trace evaluation roles skipped" >&2
fi

if [ "${THREADLIGHT_SKIP_DEPLOYER_ROLE:-false}" != "true" ]; then
  deployer="${AZURE_PRINCIPAL_ID:-}"
  deployer_type="${AZURE_PRINCIPAL_TYPE:-User}"
  if [ -z "$deployer" ]; then
    deployer=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
  fi
  if [ -n "$deployer" ]; then
    assign "$deployer" "$deployer_type" "$ROLE_FOUNDRY_USER" "$ACCOUNT_ID"
  else
    echo "postprovision: deploying identity unknown; set AZURE_PRINCIPAL_ID to grant it Foundry User" >&2
  fi
fi
echo "postprovision: role assignments can take a few minutes to propagate"
