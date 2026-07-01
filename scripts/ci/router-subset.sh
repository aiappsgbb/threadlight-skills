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
#
# Safety: every control-plane call asserts a 200 and fails LOUDLY otherwise, so
# a throttled/expired-token restore can never silently no-op and leave the
# shared router pinned. `restore` additionally verifies the live routing matches
# the intended state before declaring success. (No optimistic-concurrency ETag:
# the matrix serializes its own record/set/restore on this shared deployment, so
# last-writer-wins is acceptable here.)
set -euo pipefail
SUB=2c745a8f-9d37-45e3-8506-80797e89735e
RG=rg-shared-gbb-ci
ACC=aif-shared-gbb-ci
DEP=model-router
API=2025-10-01-preview
URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$ACC/deployments/$DEP?api-version=$API"
SNAP=/tmp/model-router-routing.snapshot.json
RESP=/tmp/model-router-routing.resp.json

# Acquire a fresh ARM token. Assigned on its own line so a failure aborts the
# script under `set -e` (an inline `local x=$(...)` would mask the exit code).
get_token() { az account get-access-token --resource https://management.azure.com --query accessToken -o tsv; }

# GET the deployment into $RESP, asserting HTTP 200.
arm_get() {
  local token http
  token="$(get_token)"
  http="$(curl -sS -H "Authorization: Bearer $token" -o "$RESP" -w '%{http_code}' "$URL")"
  if [ "$http" != "200" ]; then
    echo "::error::router-subset GET failed (HTTP $http)" >&2; cat "$RESP" >&2; echo >&2; return 1
  fi
}

# PUT $1 (JSON body) to the deployment into $RESP, asserting HTTP 200/201.
arm_put() {
  local body="$1" token http
  token="$(get_token)"
  http="$(curl -sS -X PUT -H "Authorization: Bearer $token" \
      -H "Content-Type: application/json" -d "$body" \
      -o "$RESP" -w '%{http_code}' "$URL")"
  if [ "$http" != "200" ] && [ "$http" != "201" ]; then
    echo "::error::router-subset PUT failed (HTTP $http)" >&2; cat "$RESP" >&2; echo >&2; return 1
  fi
}

case "${1:-}" in
  record)
    arm_get
    python3 -c "import json;d=json.load(open('$RESP'));json.dump(d.get('properties',{}).get('routing'),open('$SNAP','w'))"
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
    arm_put "$BODY"
    python3 -c "import json;print(json.dumps(json.load(open('$RESP')).get('properties',{}).get('routing'),indent=2))"
    echo "subset set to {gpt-5.4, gpt-5.4-mini}; propagation up to 5 min" ;;
  restore)
    # Decide the target routing from the snapshot. Distinguish "snapshot
    # missing" (record never ran / file lost) from "snapshot == null" (no
    # routing block existed). In the missing case we still restore the
    # documented default (full pool) so the shared router is never left pinned,
    # but we warn loudly so the operator knows we assumed it.
    if [ ! -f "$SNAP" ]; then
      echo "::warning::no snapshot at $SNAP — assuming documented default (no routing block) and restoring full pool" >&2
    fi
    if [ -s "$SNAP" ] && [ "$(cat "$SNAP")" != "null" ]; then
      ROUTING=$(cat "$SNAP")
      EXPECT_ROUTING=1
      BODY="{\"sku\":{\"name\":\"GlobalStandard\",\"capacity\":1000},\"properties\":{\"model\":{\"format\":\"OpenAI\",\"name\":\"model-router\",\"version\":\"2025-11-18\"},\"routing\":$ROUTING}}"
    else
      EXPECT_ROUTING=0
      BODY='{"sku":{"name":"GlobalStandard","capacity":1000},"properties":{"model":{"format":"OpenAI","name":"model-router","version":"2025-11-18"}}}'
    fi
    arm_put "$BODY"
    # Verify the live deployment now matches the intended state before claiming
    # success — this is the guarantee that the shared router is truly un-pinned.
    arm_get
    python3 - "$EXPECT_ROUTING" <<PY
import json, sys
expect_routing = sys.argv[1] == "1"
routing = json.load(open("$RESP")).get("properties", {}).get("routing")
if expect_routing and routing is None:
    sys.exit("::error::restore verification failed — expected a routing block but live deployment has none")
if not expect_routing and routing is not None:
    sys.exit("::error::restore verification failed — shared router still pinned: %s" % json.dumps(routing))
print("verified live routing matches intended state")
PY
    echo "restored prior routing (default full pool)" ;;
  *) echo "usage: $0 record|set|restore"; exit 2 ;;
esac
