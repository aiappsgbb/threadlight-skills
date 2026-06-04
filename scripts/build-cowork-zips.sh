#!/usr/bin/env bash
# Build Cowork-installable skill zips into docs/downloads/.
#
# Microsoft Copilot Cowork (M365 Frontier) discovers custom skills by reading
# subfolders of OneDrive/Documents/Cowork/skills/. To install a skill, the user
# downloads a zip whose top-level entry is <skill-name>/, unzips it into that
# OneDrive path, and waits ~35s for OneDrive sync.
#
# Each zip published here MUST:
#   - Contain a single top-level folder matching the skill's `name:` frontmatter
#   - Stay within Cowork per-skill limits: SKILL.md ≤ 1 MB,
#     ≤ 20 companion files, ≤ 5 MB per companion, ≤ 10 MB total companion size
#   - Contain only the skill folder under skills/ that is Cowork-safe — i.e.
#     SKILL.md instructions do not require shell execution, docker, azd,
#     playwright Chromium launch, or ffmpeg at runtime
#
# Re-run this script whenever a Cowork-safe skill changes. The output zip is
# committed to docs/downloads/ so it ships with the GH Pages site.
#
# Currently Cowork-safe: threadlight-design

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/docs/downloads"
SRC_DIR="${REPO_ROOT}/skills"

# List of Cowork-safe skill folder names (must match `name:` in each SKILL.md)
COWORK_SAFE_SKILLS=(
  threadlight-design
)

mkdir -p "${OUT_DIR}"

for skill in "${COWORK_SAFE_SKILLS[@]}"; do
  if [[ ! -f "${SRC_DIR}/${skill}/SKILL.md" ]]; then
    echo "ERROR: ${SRC_DIR}/${skill}/SKILL.md not found — aborting." >&2
    exit 1
  fi

  zip_path="${OUT_DIR}/${skill}.zip"
  rm -f "${zip_path}"

  (cd "${SRC_DIR}" && zip -r --quiet "${zip_path}" "${skill}" -x "*.DS_Store" "*/__pycache__/*")

  # Enforce Cowork per-skill limits
  skill_md_bytes=$(wc -c < "${SRC_DIR}/${skill}/SKILL.md" | tr -d ' ')
  companion_count=$(find "${SRC_DIR}/${skill}" -type f ! -name SKILL.md | wc -l | tr -d ' ')
  companion_bytes=$(find "${SRC_DIR}/${skill}" -type f ! -name SKILL.md -exec wc -c {} + | tail -n1 | awk '{print $1}')

  echo "✓ ${skill}.zip ($(wc -c < "${zip_path}" | tr -d ' ') bytes)"
  echo "  SKILL.md: ${skill_md_bytes} / 1048576 bytes"
  echo "  companions: ${companion_count} / 20 files, ${companion_bytes} / 10485760 bytes total"

  if (( skill_md_bytes > 1048576 )); then
    echo "  ✗ SKILL.md exceeds 1 MB Cowork limit" >&2; exit 1
  fi
  if (( companion_count > 20 )); then
    echo "  ✗ exceeds 20 companion file limit" >&2; exit 1
  fi
  if (( companion_bytes > 10485760 )); then
    echo "  ✗ companion files exceed 10 MB total" >&2; exit 1
  fi
done

echo ""
echo "Done. Commit ${OUT_DIR}/ to publish on GitHub Pages."
