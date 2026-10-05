#!/bin/sh
# azd postdeploy hook: grant the hosted agent's INSTANCE identity Foundry User
# (role GUID, formerly "Azure AI User") on the Foundry account and project.
# The blueprint identity never gets role assignments. Idempotent.
set -eu

ROLE_FOUNDRY_USER=53ca6127-db72-4b80-b1b0-d745d6d5456d
AGENT_NAME="${THREADLIGHT_AGENT_NAME:-__AGENT_NAME__}"
PROJECT_SCOPE="${AZURE_AI_PROJECT_ID:?AZURE_AI_PROJECT_ID missing from azd env}"
ACCOUNT_SCOPE="${PROJECT_SCOPE%/projects/*}"

pid=""
for attempt in 1 2 3 4 5 6; do
  pid=$(azd ai agent show "$AGENT_NAME" -o json 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("instance_identity") or {}).get("principal_id")) or "")' || true)
  [ -n "$pid" ] && break
  sleep 10
done
if [ -z "$pid" ]; then
  echo "postdeploy: instance identity for $AGENT_NAME not found" >&2
  exit 1
fi

for scope in "$ACCOUNT_SCOPE" "$PROJECT_SCOPE"; do
  existing=$(az role assignment list --assignee "$pid" --role "$ROLE_FOUNDRY_USER" --scope "$scope" --query "[0].id" -o tsv 2>/dev/null || true)
  if [ -z "$existing" ]; then
    az role assignment create --assignee-object-id "$pid" --assignee-principal-type ServicePrincipal \
      --role "$ROLE_FOUNDRY_USER" --scope "$scope" -o none
    echo "postdeploy: granted Foundry User to instance identity at $scope"
  else
    echo "postdeploy: Foundry User already present at $scope"
  fi
done
# Publish the agent endpoint so threadlight-auto's deploy probe sees a completed deploy.
endpoint="${AZURE_AI_PROJECT_ENDPOINT:?AZURE_AI_PROJECT_ENDPOINT missing from azd env}"
azd env set AGENT_FQDN "${endpoint%/}/agents/$AGENT_NAME" >/dev/null
echo "postdeploy: AGENT_FQDN set for $AGENT_NAME"
echo "postdeploy: role propagation can take about 60 seconds before the first invocation succeeds"
