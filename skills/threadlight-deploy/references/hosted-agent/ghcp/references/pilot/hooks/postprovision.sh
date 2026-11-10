#!/bin/sh
# azd postprovision hook: connect the App Insights resource to the Foundry
# project (project monitoring). Foundry injects APPLICATIONINSIGHTS_CONNECTION_STRING
# into hosted agents only when this connection exists:
# https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry
#
# Why a hook and not Bicep: on 2026-11-10 in eastus2 every Foundry connection
# write returned 500 InternalServerError although the service saved it. ARM
# kept retrying the PUT and `azd up` hung until cancelled. This hook writes once,
# then trusts only a read-back: an error with a saved connection passes, an error
# with nothing saved fails. Idempotent; an existing connection is reused.
# The connection string is sent from a private temp file and never printed.
set -eu

PROJECT_ID="${AZURE_AI_PROJECT_ID:?AZURE_AI_PROJECT_ID missing from azd env}"
APPI_ID="${APPLICATIONINSIGHTS_RESOURCE_ID:?APPLICATIONINSIGHTS_RESOURCE_ID missing from azd env}"
: "${APPLICATIONINSIGHTS_CONNECTION_STRING:?APPLICATIONINSIGHTS_CONNECTION_STRING missing from azd env}"
API="2025-04-01-preview"
BASE="https://management.azure.com${PROJECT_ID}/connections"
NAME="${APPI_ID##*/}"
SLEEP="${THREADLIGHT_HOOK_RETRY_SLEEP:-10}"

# Prints the name of the project's AppInsights connection to this resource, if any.
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
    if p.get("category") == "AppInsights" and (p.get("target") or "").lower() == want:
        print(c.get("name", ""))
        break
' "$APPI_ID" || true
}

record() {
  azd env set AZURE_AI_APPINSIGHTS_CONNECTION_ID "${PROJECT_ID}/connections/$1" >/dev/null
  echo "postprovision: App Insights connected to the Foundry project ($1)"
}

existing=$(find_connection)
if [ -n "$existing" ]; then
  record "$existing"
  exit 0
fi

umask 077
body=$(mktemp)
err=$(mktemp)
trap 'rm -f "$body" "$err"' EXIT INT TERM
python3 -c '
import json, os, sys
appi = os.environ["APPLICATIONINSIGHTS_RESOURCE_ID"]
json.dump({"properties": {
    "category": "AppInsights", "target": appi, "authType": "ApiKey", "isSharedToAll": True,
    "credentials": {"key": os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"]},
    "metadata": {"ApiType": "Azure", "ResourceId": appi},
}}, open(sys.argv[1], "w"))
' "$body"

if az rest --method put --url "${BASE}/${NAME}?api-version=${API}" --body "@${body}" -o none 2>"$err"; then
  record "$NAME"
  exit 0
fi

for attempt in 1 2 3; do
  sleep "$SLEEP"
  saved=$(find_connection)
  if [ -n "$saved" ]; then
    echo "postprovision: the service answered an error but saved the connection; continuing." >&2
    echo "  service error: $(head -c 300 "$err")" >&2
    record "$saved"
    exit 0
  fi
done
echo "postprovision: could not connect App Insights to the Foundry project:" >&2
head -c 1000 "$err" >&2
echo >&2
exit 1
