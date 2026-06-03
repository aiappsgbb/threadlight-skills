#!/usr/bin/env bash
# scripts/ci/skill-discovery-smoke.sh
#
# Fail-fast gate: asserts every skill under skills/ is registered in the
# Copilot CLI's Skill-tool catalog. Catches the silent loader-drop
# regression first observed in run #26772059599 where the agent fell
# back to Read on SKILL.md when skill(<name>) returned "Skill not found",
# masking the failure as "success".
#
# Protocol: `copilot --output-format json` emits JSONL on stdout including
# `session.skills_loaded` events whose `data.skills` is the registered
# catalog as `[{name, ...}, ...]` or `[name, ...]`. We parse the last
# non-empty event and diff against the on-disk skills/ directory listing.
#
# This takes the model out of the assertion path entirely: ~10s,
# deterministic, no prompt formatting variance.
#
# Usage: scripts/ci/skill-discovery-smoke.sh
# Exits 0 if every skill is present in the registry; 1 otherwise.

set -euo pipefail
cd "$(dirname "$0")/../.."

# --- Requirements ----------------------------------------------------------
for cmd in jq copilot; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "::error::Required command not on PATH: $cmd" >&2
    exit 1
  }
done

if command -v timeout >/dev/null 2>&1; then
  TIMEOUT=timeout
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT=gtimeout
else
  echo "::error::Need 'timeout' (Linux) or 'gtimeout' (macOS via 'brew install coreutils')" >&2
  exit 1
fi

# --- Expected: every directory under skills/ -------------------------------
mapfile -t EXPECTED < <(find skills -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort)
[[ ${#EXPECTED[@]} -gt 0 ]] || { echo "::error::No skill directories under skills/" >&2; exit 1; }
echo "Expecting ${#EXPECTED[@]} skills under skills/: ${EXPECTED[*]}"

# --- One Copilot call → save JSONL for artifact upload ---------------------
mkdir -p smoke-outputs
LOG=smoke-outputs/discovery.jsonl

echo "Calling copilot to enumerate Skill-tool registry (JSON output mode)..."
rc=0
"$TIMEOUT" 120 copilot \
  --output-format json \
  --no-color --silent --stream off \
  --no-custom-instructions --allow-all-tools \
  -p 'reply with the single word: ok' \
  > "$LOG" 2>&1 || rc=$?

if [[ $rc -ne 0 ]]; then
  echo "::error::copilot CLI exited $rc (timeout=124, oom=137). First 80 lines of output:" >&2
  head -80 "$LOG" >&2 || true
  exit "$rc"
fi

# --- Parse: last session.skills_loaded with non-empty catalog --------------
ACTUAL=$(
  grep -F '"type":"session.skills_loaded"' "$LOG" \
    | jq -r 'select(.data.skills | length > 0) | .data.skills | map(.name // .)[]' \
    2>/dev/null | sort -u || true
)

if [[ -z "$ACTUAL" ]]; then
  echo "::error::No session.skills_loaded events with non-empty catalog in $LOG" >&2
  echo "First 80 lines of CLI output:" >&2
  head -80 "$LOG" >&2 || true
  exit 1
fi

ACTUAL_COUNT=$(echo "$ACTUAL" | wc -l | tr -d ' ')
echo "Registry reports $ACTUAL_COUNT skill(s) total."

# --- Diff expected vs actual ----------------------------------------------
MISSING=$(comm -23 <(printf '%s\n' "${EXPECTED[@]}") <(echo "$ACTUAL") || true)

if [[ -n "$MISSING" ]]; then
  echo "::error::Skill(s) present under skills/ but MISSING from Copilot CLI registry:" >&2
  echo "$MISSING" | sed 's/^/  - /' >&2
  echo "" >&2
  echo "Registered skills matching threadlight-* (for context):" >&2
  echo "$ACTUAL" | grep -E '^threadlight-' | sed 's/^/  + /' >&2 || echo "  (none)" >&2
  echo "" >&2
  echo "Full discovery JSONL saved to: $LOG" >&2
  exit 1
fi

echo "✓ All ${#EXPECTED[@]} skill(s) under skills/ are present in the Copilot CLI registry."
