#!/usr/bin/env bash
# scripts/ci/foundry-strong-arm.sh
#
# Idempotently create the gpt-5.4 "strong arm" deployment on the shared CI
# Foundry account (aif-shared-gbb-ci). Used by the router-validation matrix
# (skills/threadlight-router-bench `validate` mode) as the quality ceiling the
# model-router subset {gpt-5.4, gpt-5.4-mini} is allowed to escalate to.
#
# Safe to re-run: if the deployment already exists it exits 0 without mutating.
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

if az cognitiveservices account deployment show \
     --name "$ACC" -g "$RG" --deployment-name "$NAME" >/dev/null 2>&1; then
  echo "deployment $NAME already exists"; exit 0
fi

echo "creating $NAME ($MODEL $VERSION)..."
az cognitiveservices account deployment create \
  --name "$ACC" -g "$RG" \
  --deployment-name "$NAME" \
  --model-name "$MODEL" --model-version "$VERSION" --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 100

az cognitiveservices account deployment show \
  --name "$ACC" -g "$RG" --deployment-name "$NAME" \
  --query "{name:name, model:properties.model.name, ver:properties.model.version, cap:sku.capacity}" -o table
