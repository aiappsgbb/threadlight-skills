#!/usr/bin/env bash
# scripts/ci/foundry-strong-arm.sh
#
# Idempotently create the gpt-5.4 "strong arm" deployment on the shared CI
# Foundry account (aif-shared-gbb-ci). Used by the router-validation matrix
# (skills/threadlight-router-bench `validate` mode) as the quality ceiling the
# model-router subset {gpt-5.4, gpt-5.4-mini} is allowed to escalate to.
#
# Safe to re-run: idempotent — creates the deployment when absent, raises its
# capacity to TARGET_CAP when an older/undersized deployment exists (via a
# create-or-update PUT; there is no `az ... deployment update` subcommand), and
# no-ops when capacity is already adequate.
# Run a quota pre-check first (see the `usage` note below); if the gpt-5.4
# GlobalStandard limit is 0/exhausted, SKIP the strong arm — the matrix still
# runs with mini + router, and validation_scorecard tolerates a missing strong
# arm (rounds-factor checks are skipped when the strong arm is absent).
set -euo pipefail

ACC=aif-shared-gbb-ci
RG=rg-shared-gbb-ci
NAME=gpt-5.4
MODEL=gpt-5.4
VERSION=2026-03-05         # verified catalog version (westus3/swedencentral)
TARGET_CAP=250            # TPM cap (×1000). 100 was undersized — strong + router 429'd on gpt-5.4.

create_or_update() {
  # PUT-upsert: creates when absent, raises capacity when present. `create` maps
  # to the CognitiveServices deployments PUT, which is idempotent — re-running it
  # with a higher --sku-capacity updates the existing deployment in place.
  az cognitiveservices account deployment create \
    --name "$ACC" -g "$RG" \
    --deployment-name "$NAME" \
    --model-name "$MODEL" --model-version "$VERSION" --model-format OpenAI \
    --sku-name GlobalStandard --sku-capacity "$TARGET_CAP"
}

if az cognitiveservices account deployment show \
     --name "$ACC" -g "$RG" --deployment-name "$NAME" >/dev/null 2>&1; then
  CUR_CAP="$(az cognitiveservices account deployment show \
    --name "$ACC" -g "$RG" --deployment-name "$NAME" \
    --query "sku.capacity" -o tsv 2>/dev/null)"
  if [ -n "$CUR_CAP" ] && [ "$CUR_CAP" -ge "$TARGET_CAP" ]; then
    echo "deployment $NAME already exists at cap $CUR_CAP (>= $TARGET_CAP) — nothing to do"; exit 0
  fi
  echo "deployment $NAME exists at cap ${CUR_CAP:-unknown} — raising to $TARGET_CAP..."
  create_or_update
else
  echo "creating $NAME ($MODEL $VERSION) at cap $TARGET_CAP..."
  create_or_update
fi

az cognitiveservices account deployment show \
  --name "$ACC" -g "$RG" --deployment-name "$NAME" \
  --query "{name:name, model:properties.model.name, ver:properties.model.version, cap:sku.capacity}" -o table
