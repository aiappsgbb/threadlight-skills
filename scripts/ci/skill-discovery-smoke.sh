#!/usr/bin/env bash
# scripts/ci/skill-discovery-smoke.sh
#
# Fail-fast gate: asserts the Copilot CLI can load every skill under
# skills/ via the Skill tool. Catches the silent loader-drop regression
# first observed in run #26772059599 where skill(threadlight-auto)
# returned "Skill not found" but the workflow passed because the agent
# fell back to Read.
#
# Usage: scripts/ci/skill-discovery-smoke.sh
# Exits 0 if every skill is discoverable; 1 on first failure.

set -euo pipefail
cd "$(dirname "$0")/../.."

# Cross-platform timeout (Linux ships 'timeout'; macOS needs coreutils)
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT=timeout
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT=gtimeout
else
  echo "::error::Need 'timeout' (Linux) or 'gtimeout' (macOS via 'brew install coreutils')" >&2
  exit 1
fi

if ! command -v copilot >/dev/null 2>&1; then
  echo "::error::copilot CLI not found on PATH. Install: npm install -g @github/copilot" >&2
  exit 1
fi

mapfile -t SKILLS < <(find skills -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort)
[[ ${#SKILLS[@]} -gt 0 ]] || { echo "::error::No skills under skills/" >&2; exit 1; }

echo "Probing ${#SKILLS[@]} skills: ${SKILLS[*]}"

FAILED=()
for skill in "${SKILLS[@]}"; do
  # Ask the agent to call Skill and quote the literal 'name:' value from
  # the SKILL.md frontmatter. If the agent paraphrases the description
  # but quotes the literal name field, we get a hallucination-resistant
  # signal that the Skill tool actually returned file content.
  prompt="Call the Skill tool with name=\"$skill\". If the tool returns the skill's content, find the line in the frontmatter that starts with 'name:' and reply EXACTLY: ECHO:$skill:<the literal name value>. If the tool returns 'Skill not found' or any error, reply EXACTLY: MISSING:$skill. Do not use any other tool. Do not call Read."

  out=""
  exit_code=0
  out=$($TIMEOUT 60 copilot --no-custom-instructions --allow-all-tools -p "$prompt" 2>&1) || exit_code=$?

  if [[ $exit_code -eq 124 || $exit_code -eq 137 ]]; then
    echo "  ⏱ $skill TIMEOUT after 60s (exit $exit_code)"
    FAILED+=("$skill(timeout)")
    continue
  fi

  if grep -Eq "^ECHO:${skill}:[[:space:]]*${skill}[[:space:]]*\$" <<<"$out"; then
    echo "  ✓ $skill"
  else
    echo "  ✗ $skill (copilot exit=$exit_code)"
    echo "    last 5 lines of output: $(echo "$out" | tail -5 | tr '\n' '|')"
    FAILED+=("$skill")
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "::error::${#FAILED[@]} skill(s) failed discovery: ${FAILED[*]}" >&2
  exit 1
fi

echo "All ${#SKILLS[@]} skills discoverable."
