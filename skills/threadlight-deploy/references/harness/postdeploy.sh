#!/bin/sh
# azd postdeploy hook for a Foundry Copilot harness agent (prompt agent,
# harness.type microsoft_copilot_preview): grant the agent INSTANCE identity
# Foundry User (role GUID) on the Foundry PROJECT. Learn documents this grant for
# Toolbox calls and returned files. Tested Nov 2026: without it an input_file
# passed by Foundry FILE ID was silently skipped (empty workspace, no error);
# with it the file was staged. Never grant it to the project managed identity.
# Idempotent. Allow up to five minutes, then test from a NEW conversation.
set -eu

ROLE_FOUNDRY_USER=53ca6127-db72-4b80-b1b0-d745d6d5456d
AGENT_NAME="${THREADLIGHT_AGENT_NAME:-__AGENT_NAME__}"
PROJECT_SCOPE="${AZURE_AI_PROJECT_ID:?AZURE_AI_PROJECT_ID missing from azd env}"

pid=""
for attempt in 1 2 3 4 5 6; do
  pid=$(azd ai agent show "$AGENT_NAME" -o json 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("instance_identity") or {}).get("principal_id")) or "")' || true)
  [ -n "$pid" ] && break
  sleep 10
done
if [ -z "$pid" ]; then
  echo "postdeploy: agent identity for $AGENT_NAME not found" >&2
  exit 1
fi

existing=$(az role assignment list --assignee "$pid" --role "$ROLE_FOUNDRY_USER" --scope "$PROJECT_SCOPE" --query "[0].id" -o tsv 2>/dev/null || true)
if [ -z "$existing" ]; then
  az role assignment create --assignee-object-id "$pid" --assignee-principal-type ServicePrincipal \
    --role "$ROLE_FOUNDRY_USER" --scope "$PROJECT_SCOPE" -o none
  echo "postdeploy: granted Foundry User to the harness agent identity on the project"
else
  echo "postdeploy: Foundry User already present on the project"
fi
endpoint="${AZURE_AI_PROJECT_ENDPOINT:?AZURE_AI_PROJECT_ENDPOINT missing from azd env}"
azd env set AGENT_FQDN "${endpoint%/}/agents/$AGENT_NAME" >/dev/null
echo "postdeploy: role propagation can take up to five minutes; test file inputs from a new conversation"
