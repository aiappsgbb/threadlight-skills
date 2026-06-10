#!/usr/bin/env bash
#
# install-azd-hook.sh — auto-enable threadlight-production-ready in azd
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/aiappsgbb/threadlight-skills/main/skills/threadlight-production-ready/references/azd-hooks/install-azd-hook.sh | bash
#   # OR locally:
#   bash skills/threadlight-production-ready/references/azd-hooks/install-azd-hook.sh
#
# What it does:
#   - Adds a `postdeploy` hook to azure.yaml that runs
#     `python tests/production_ready.py --quiet` after every `azd up`.
#   - Copies the production_ready.py script into tests/ if not present.
#   - Idempotent: re-running is a no-op once installed.
#   - Soft-advisory: hook never fails the deploy (skill exits 0).
#
# Prereqs:
#   - azure.yaml at repo root (you have run `azd init` already)
#   - `bicep` CLI on PATH (`az bicep install` if missing)

set -euo pipefail

AZURE_YAML="${AZURE_YAML:-azure.yaml}"
TESTS_DIR="${TESTS_DIR:-tests}"
SCRIPT_NAME="production_ready.py"
HOOK_MARKER="# threadlight-production-ready auto-enabled hook"

if [[ ! -f "$AZURE_YAML" ]]; then
  echo "error: $AZURE_YAML not found. Run \`azd init\` first." >&2
  exit 1
fi

mkdir -p "$TESTS_DIR"
TARGET="$TESTS_DIR/$SCRIPT_NAME"

# Copy the script in if it's not there already.
if [[ ! -f "$TARGET" ]]; then
  THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  CANDIDATE="$THIS_DIR/../../scripts/$SCRIPT_NAME"
  if [[ -f "$CANDIDATE" ]]; then
    cp "$CANDIDATE" "$TARGET"
    echo "✓ copied $SCRIPT_NAME → $TARGET"
  else
    cat <<EOF >&2
warning: cannot find $SCRIPT_NAME at $CANDIDATE.
         Copy it manually from the threadlight-skills repo:
           skills/threadlight-production-ready/scripts/$SCRIPT_NAME
EOF
  fi
else
  echo "• $TARGET already present, skipping copy."
fi

# Append the hook if absent. We use a marker line for idempotency.
if grep -qF "$HOOK_MARKER" "$AZURE_YAML"; then
  echo "• hook already present in $AZURE_YAML."
else
  cat <<EOF >> "$AZURE_YAML"

# ${HOOK_MARKER}
# Runs the production-readiness scorecard after every \`azd up\`.
# Always exits 0 — never blocks a deploy. Inspect:
#   docs/production-readiness-report.md
#   tests/production-readiness-manifest.json
hooks:
  postdeploy:
    posix:
      shell: sh
      run: |
        python ${TESTS_DIR}/${SCRIPT_NAME} --quiet || true
    windows:
      shell: pwsh
      run: |
        python ${TESTS_DIR}/${SCRIPT_NAME} --quiet
        if (\$LASTEXITCODE -ne 0) { Write-Host "production-ready exited non-zero (advisory)"; \$global:LASTEXITCODE = 0 }
EOF
  echo "✓ appended postdeploy hook to $AZURE_YAML"
fi

# Install bicep if missing — v0.3.0 hard prerequisite.
if ! az bicep version >/dev/null 2>&1; then
  echo "• installing bicep CLI (required by v0.3.0)..."
  az bicep install
fi
az bicep version

echo ""
echo "Done. Next \`azd up\` will produce:"
echo "  - docs/production-readiness-report.md"
echo "  - tests/production-readiness-manifest.json"
echo "  - tests/production-readiness-trend.csv (per-run row)"
