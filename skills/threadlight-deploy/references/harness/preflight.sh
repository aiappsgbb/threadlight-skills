#!/bin/sh
# azd preprovision/predeploy hook: the Copilot harness is preview and azd only
# accepts harness fields when this flag is exported in the shell (and in CI)
# before azd starts. https://learn.microsoft.com/azure/foundry/agents/how-to/use-tools-skills-copilot-harness
set -eu
if [ "${AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS:-}" != "true" ]; then
  echo "preflight: export AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true (shell and CI) before azd provision/deploy" >&2
  exit 1
fi
echo "preflight: Copilot harness preview flag set"
