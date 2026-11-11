#!/bin/sh
# azd postprovision hook (private profile): project connection to the private
# ACR with the project managed identity, so the hosted-agent platform pulls the
# image through the registry connection
# (https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks#combine-with-a-private-acr).
# Control plane only. Same write-once, trust-the-read-back pattern as
# postprovision.sh (Foundry connection writes returned 500 while saving).
set -eu

PROJECT_ID="${AZURE_AI_PROJECT_ID:?AZURE_AI_PROJECT_ID missing from azd env}"
ACR_ID="${AZURE_CONTAINER_REGISTRY_RESOURCE_ID:?AZURE_CONTAINER_REGISTRY_RESOURCE_ID missing from azd env}"
: "${AZURE_CONTAINER_REGISTRY_ENDPOINT:?AZURE_CONTAINER_REGISTRY_ENDPOINT missing from azd env}"
: "${AZURE_AI_PROJECT_PRINCIPAL_ID:?AZURE_AI_PROJECT_PRINCIPAL_ID missing from azd env}"
API="2025-04-01-preview"
BASE="https://management.azure.com${PROJECT_ID}/connections"
NAME="acr-${ACR_ID##*/}"
SLEEP="${THREADLIGHT_HOOK_RETRY_SLEEP:-10}"

find_connection() {
  az rest --method get --url "${BASE}?api-version=${API}" -o json 2>/dev/null | python3 -c '
import json, sys
want = sys.argv[1].lower()
try:
    items = json.load(sys.stdin).get("value", [])
except ValueError:
    items = []
for c in items:
    p = c.get("properties") or {}
    if p.get("category") == "ContainerRegistry" and ((p.get("metadata") or {}).get("ResourceId") or "").lower() == want:
        print(c.get("name", ""))
        break
' "$ACR_ID" || true
}

existing=$(find_connection)
if [ -n "$existing" ]; then
  echo "acr-connection: registry connection present ($existing)"
  exit 0
fi

umask 077
body=$(mktemp)
err=$(mktemp)
trap 'rm -f "$body" "$err"' EXIT INT TERM
python3 -c '
import json, os, sys
acr = os.environ["AZURE_CONTAINER_REGISTRY_RESOURCE_ID"]
json.dump({"properties": {
    "category": "ContainerRegistry", "target": os.environ["AZURE_CONTAINER_REGISTRY_ENDPOINT"],
    "authType": "ManagedIdentity", "isSharedToAll": True,
    "credentials": {"clientId": os.environ["AZURE_AI_PROJECT_PRINCIPAL_ID"], "resourceId": acr},
    "metadata": {"ResourceId": acr},
}}, open(sys.argv[1], "w"))
' "$body"

if az rest --method put --url "${BASE}/${NAME}?api-version=${API}" --body "@${body}" -o none 2>"$err"; then
  echo "acr-connection: registry connection created ($NAME)"
  exit 0
fi
for attempt in 1 2 3; do
  sleep "$SLEEP"
  saved=$(find_connection)
  if [ -n "$saved" ]; then
    echo "acr-connection: the service answered an error but saved the connection ($saved)." >&2
    exit 0
  fi
done
echo "acr-connection: could not create the registry connection:" >&2
head -c 1000 "$err" >&2
echo >&2
exit 1
