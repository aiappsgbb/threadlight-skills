#!/usr/bin/env bash
# scripts/ci/foundry-strong-arm.sh
#
# Idempotently create the per-model deployments the router-validation matrix
# (skills/threadlight-router-bench `validate` mode) needs on the shared CI
# Foundry account:
#
#   gpt-6-luna     fixed "small" arm, and a router subset member
#   gpt-6.1-terra  router subset member
#   gpt-6.1-sol    fixed "strong" arm (quality ceiling), and a router subset member
#
# Each router subset member is deployed in the same account on purpose: model
# router serves routed requests from your own deployment of the exact model
# name and version first, which avoided the router-managed capacity throttle
# observed in Nov 2026 (see references/findings/2026-11-20-...md).
#
# Safe to re-run: creates a deployment when absent, raises its capacity to
# TARGET_CAP when undersized (a create-or-update PUT; there is no
# `az ... deployment update` subcommand), and no-ops otherwise.
# Run a quota pre-check first (`az cognitiveservices usage list -l <region>`);
# if a model's GlobalStandard quota is exhausted, stop and fix quota rather
# than running the matrix with a missing own deployment.
set -euo pipefail

# Target comes from the operator's environment (az CLI's current subscription
# is used); nothing is embedded here.
ACC="${ROUTER_ACCOUNT:?set ROUTER_ACCOUNT to the approved Foundry account}"
RG="${ROUTER_RESOURCE_GROUP:?set ROUTER_RESOURCE_GROUP to the approved resource group}"
TARGET_CAP="${TARGET_CAP:-250}"   # TPM cap (x1000)

# name:version, verified against the model router 2025-11-18 pool listing.
MODELS=("gpt-6-luna:2026-09-22" "gpt-6.1-terra:2026-10-26" "gpt-6.1-sol:2026-09-29")

create_or_update() {
  az cognitiveservices account deployment create \
    --name "$ACC" -g "$RG" \
    --deployment-name "$1" \
    --model-name "$1" --model-version "$2" --model-format OpenAI \
    --sku-name GlobalStandard --sku-capacity "$TARGET_CAP"
}

for spec in "${MODELS[@]}"; do
  NAME="${spec%%:*}"; VERSION="${spec#*:}"
  if az cognitiveservices account deployment show \
       --name "$ACC" -g "$RG" --deployment-name "$NAME" >/dev/null 2>&1; then
    CUR_CAP="$(az cognitiveservices account deployment show \
      --name "$ACC" -g "$RG" --deployment-name "$NAME" \
      --query "sku.capacity" -o tsv 2>/dev/null)"
    if [ -n "$CUR_CAP" ] && [ "$CUR_CAP" -ge "$TARGET_CAP" ]; then
      echo "deployment $NAME already exists at cap $CUR_CAP (>= $TARGET_CAP) — nothing to do"
      continue
    fi
    echo "deployment $NAME exists at cap ${CUR_CAP:-unknown} — raising to $TARGET_CAP..."
  else
    echo "creating $NAME ($VERSION) at cap $TARGET_CAP..."
  fi
  create_or_update "$NAME" "$VERSION"
done

az cognitiveservices account deployment list --name "$ACC" -g "$RG" \
  --query "[].{name:name, model:properties.model.name, ver:properties.model.version, cap:sku.capacity}" -o table
