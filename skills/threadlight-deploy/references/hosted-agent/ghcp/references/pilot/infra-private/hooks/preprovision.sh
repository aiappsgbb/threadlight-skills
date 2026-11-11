#!/bin/sh
# azd preprovision hook (private profile). Control plane only; runs on the laptop.
#
# 1. ACR remote-build access. ACR quick builds (what `azd` remote build runs)
#    fail against a registry whose public network access is Disabled unless they
#    run on a dedicated agent pool, and `azd` doesn't select one:
#    https://learn.microsoft.com/azure/container-registry/container-registry-private-endpoints
#    Default (THREADLIGHT_ACR_BUILD_ACCESS=service-tag): the registry's public
#    endpoint stays Deny-by-default and allows only this region's
#    AzureContainerRegistry service-tag prefixes, where ACR Tasks run; pulls from
#    the VNet use the private endpoint. THREADLIGHT_ACR_BUILD_ACCESS=none turns
#    the public endpoint off (build images inside the network or with an agent pool).
# 2. In-VNet operator host key: an ed25519 key under ~/.ssh (never in the repo)
#    unless JUMPBOX_SSH_PUBLIC_KEY is already set. THREADLIGHT_OPERATOR_HOST=none
#    skips the host.
set -eu

LOCATION="${AZURE_LOCATION:?AZURE_LOCATION missing from azd env}"
ACCESS="${THREADLIGHT_ACR_BUILD_ACCESS:-service-tag}"

case "$ACCESS" in
  service-tag)
    prefixes=$(az network list-service-tags --location "$LOCATION" -o json | python3 -c '
import json, sys
region = sys.argv[1].lower()
tags = json.load(sys.stdin).get("values", [])
out = sorted({p for t in tags if t.get("name", "").startswith("AzureContainerRegistry.")
              and (t.get("properties") or {}).get("region", "").lower() == region
              for p in (t.get("properties") or {}).get("addressPrefixes", []) if ":" not in p})
print(",".join(out))
' "$LOCATION")
    if [ -z "$prefixes" ]; then
      echo "preprovision: no AzureContainerRegistry service-tag prefixes for $LOCATION" >&2
      exit 1
    fi
    azd env set THREADLIGHT_ACR_BUILD_PREFIXES "$prefixes" >/dev/null
    echo "preprovision: ACR public endpoint limited to $(echo "$prefixes" | tr ',' '\n' | wc -l | tr -d ' ') ACR Tasks prefixes for $LOCATION"
    ;;
  none)
    azd env set THREADLIGHT_ACR_BUILD_PREFIXES "" >/dev/null
    echo "preprovision: ACR public network access will be Disabled"
    ;;
  *)
    echo "preprovision: THREADLIGHT_ACR_BUILD_ACCESS must be service-tag or none, got $ACCESS" >&2
    exit 1
    ;;
esac

if [ "${THREADLIGHT_OPERATOR_HOST:-jumpbox}" = "jumpbox" ] && [ -z "${JUMPBOX_SSH_PUBLIC_KEY:-}" ]; then
  key="$HOME/.ssh/threadlight-${AZURE_ENV_NAME:?}-ops"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  [ -f "$key" ] || ssh-keygen -q -t ed25519 -N "" -C "threadlight-${AZURE_ENV_NAME}-ops" -f "$key"
  azd env set JUMPBOX_SSH_PUBLIC_KEY "$(cat "$key.pub")" >/dev/null
  echo "preprovision: operator host key $key"
fi
