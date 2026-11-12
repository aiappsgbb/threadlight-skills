#!/bin/sh
# azd postdeploy hook: grant the hosted agent's identity (an Entra agent
# identity, `instance_identity` in `azd ai agent show`) the least-privilege
# role for model inference, Cognitive Services OpenAI User (role GUID), on the
# Foundry ACCOUNT only. The GHCP BYOK call POST {project}/openai/v1/responses is
# authorized as Microsoft.CognitiveServices/accounts/OpenAI/responses/write;
# live on 2026-11-11 this single grant was sufficient, while Foundry User would
# also allow agent management, listkeys and connection listsecrets
# (docs/agent-operations.md "Identity model"). The blueprint gets no roles.
#
# Exception: THREADLIGHT_MODEL_GATEWAY=external (private profile, customer's own
# pass-through proxy). Live on 2026-11-12 the same call through a proxy got 403
# with OpenAI User alone, also in a new session, and 200 once Foundry User was
# added at account scope. Not documented on Learn; observed only.
#
# Grant before the first invocation. A sandbox session that already received a
# 401 kept failing for 20 minutes after the grant; a new session
# (`azd ai agent invoke --new-session`) worked at once. Idempotent.
set -eu

ROLE_OPENAI_USER=5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
ROLE_FOUNDRY_USER=53ca6127-db72-4b80-b1b0-d745d6d5456d
ROLES="$ROLE_OPENAI_USER"
if [ "${THREADLIGHT_MODEL_GATEWAY:-}" = "external" ]; then
  ROLES="$ROLE_OPENAI_USER $ROLE_FOUNDRY_USER"
fi
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
  echo "postdeploy: agent identity for $AGENT_NAME not found" >&2
  exit 1
fi

for scope in "$ACCOUNT_SCOPE"; do
  for role in $ROLES; do
    existing=$(az role assignment list --assignee "$pid" --role "$role" --scope "$scope" --query "[0].id" -o tsv 2>/dev/null || true)
    if [ -z "$existing" ]; then
      az role assignment create --assignee-object-id "$pid" --assignee-principal-type ServicePrincipal \
        --role "$role" --scope "$scope" -o none
      echo "postdeploy: granted role $role to the agent identity at $scope"
    else
      echo "postdeploy: role $role already present at $scope"
    fi
  done
done
# Publish the agent endpoint so threadlight-auto's deploy probe sees a completed deploy.
endpoint="${AZURE_AI_PROJECT_ENDPOINT:?AZURE_AI_PROJECT_ENDPOINT missing from azd env}"
azd env set AGENT_FQDN "${endpoint%/}/agents/$AGENT_NAME" >/dev/null
echo "postdeploy: AGENT_FQDN set for $AGENT_NAME"
echo "postdeploy: role propagation can take about 60 seconds before the first invocation succeeds"
