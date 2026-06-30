#!/usr/bin/env bash
# scripts/ci/router-subset.sh
#
# Record / set / restore the model-router custom subset on the shared CI
# Foundry account (aif-shared-gbb-ci). Used by the router-validation matrix to
# constrain model-router to {gpt-5.4, gpt-5.4-mini} for the experiment, then
# put the shared deployment back exactly as it was found.
#
#   record   snapshot the current routing block to /tmp (run BEFORE set)
#   set      pin routing to {gpt-5.4, gpt-5.4-mini} (propagation up to ~5 min)
#   restore  put routing back to the recorded snapshot (or the default full
#            pool if there was no routing block) — ALWAYS run this after the
#            matrix, even on failure, to avoid leaving the shared router pinned.
set -euo pipefail
SUB=2c745a8f-9d37-45e3-8506-80797e89735e
RG=rg-shared-gbb-ci
ACC=aif-shared-gbb-ci
DEP=model-router
API=2025-10-01-preview
URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACC/deployments/$DEP?api-version=$API"
SNAP=/tmp/model-router-routing.snapshot.json
TOKEN() { az account get-access-token --resource https://management.azure.com --query accessToken -o tsv; }

case "${1:-}" in
  record)
    curl -s -H "Authorization: Bearer $(TOKEN)" "$URL" \
      | python3 -c "import sys,json;d=json.load(sys.stdin);json.dump(d.get('properties',{}).get('routing'),open('$SNAP','w'))"
    echo "recorded routing -> $SNAP"; cat "$SNAP"; echo ;;
  set)
    BODY=$(cat <<JSON
{ "sku": {"name":"GlobalStandard","capacity":1000},
  "properties": {
    "model": {"format":"OpenAI","name":"model-router","version":"2025-11-18"},
    "routing": { "mode":"balanced", "models": [
      {"format":"OpenAI","name":"gpt-5.4","version":"2026-03-05"},
      {"format":"OpenAI","name":"gpt-5.4-mini","version":"2026-03-17"}
    ]}}}
JSON
)
    curl -s -X PUT -H "Authorization: Bearer $(TOKEN)" \
      -H "Content-Type: application/json" -d "$BODY" "$URL" \
      | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin).get('properties',{}).get('routing'),indent=2))"
    echo "subset set to {gpt-5.4, gpt-5.4-mini}; propagation up to 5 min" ;;
  restore)
    # current account has NO routing block by default -> PUT without routing
    if [ -s "$SNAP" ] && [ "$(cat "$SNAP")" != "null" ]; then
      ROUTING=$(cat "$SNAP")
      BODY="{\"sku\":{\"name\":\"GlobalStandard\",\"capacity\":1000},\"properties\":{\"model\":{\"format\":\"OpenAI\",\"name\":\"model-router\",\"version\":\"2025-11-18\"},\"routing\":$ROUTING}}"
    else
      BODY='{"sku":{"name":"GlobalStandard","capacity":1000},"properties":{"model":{"format":"OpenAI","name":"model-router","version":"2025-11-18"}}}'
    fi
    curl -s -X PUT -H "Authorization: Bearer $(TOKEN)" \
      -H "Content-Type: application/json" -d "$BODY" "$URL" >/dev/null
    echo "restored prior routing (default full pool)" ;;
  *) echo "usage: $0 record|set|restore"; exit 2 ;;
esac
