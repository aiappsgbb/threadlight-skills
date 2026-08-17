#!/usr/bin/env bash
# Build Cowork-installable skill zips into docs/downloads/.
#
# Microsoft Copilot Cowork (M365 Frontier) installs custom skills from a zip
# upload inside a Cowork chat: the seller attaches the zip and asks Cowork to
# install the skill. Cowork unpacks the zip and registers it for future chats.
#
# Each zip published here MUST:
#   - Be FLAT: SKILL.md at the zip root (no parent folder). Cowork rejects
#     zips that bury SKILL.md inside a subdirectory.
#   - Stay within Cowork per-skill limits: SKILL.md ≤ 1 MB,
#     ≤ 20 companion files, ≤ 5 MB per companion, ≤ 10 MB total companion size
#   - Contain only skills that are Cowork-safe — i.e. SKILL.md instructions
#     do not require shell execution, docker, azd, playwright Chromium launch,
#     or ffmpeg at runtime
#
# Re-run this script whenever a Cowork-safe skill changes. The output zip is
# committed to docs/downloads/ so it ships with the GH Pages site.
#
# Currently Cowork-safe: threadlight-design, threadlight-qualify

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

  # Flat zip: SKILL.md at the root, references/ as a sibling.
  # Cowork's installer expects SKILL.md at the top level of the archive.
  (cd "${SRC_DIR}/${skill}" && zip -r --quiet "${zip_path}" . -x "*.DS_Store" "*/__pycache__/*")

  # Enforce Cowork per-skill limits
  skill_md_bytes=$(wc -c < "${SRC_DIR}/${skill}/SKILL.md" | tr -d ' ')
  companion_count=$(find "${SRC_DIR}/${skill}" -type f ! -name SKILL.md | wc -l | tr -d ' ')
  companion_bytes=$(find "${SRC_DIR}/${skill}" -type f ! -name SKILL.md -exec wc -c {} + | tail -n1 | awk '{print $1}')

  echo "✓ ${skill}.zip ($(wc -c < "${zip_path}" | tr -d ' ') bytes)"
  echo "  SKILL.md: ${skill_md_bytes} / 1048576 bytes"
  echo "  companions: ${companion_count} / 20 files, ${companion_bytes} / 10485760 bytes total"

  # Verify SKILL.md is at the zip root (no parent folder)
  if ! unzip -l "${zip_path}" | awk '{print $4}' | grep -qx 'SKILL.md'; then
    echo "  ✗ SKILL.md not at zip root — Cowork will reject this archive" >&2; exit 1
  fi

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

# ---------------------------------------------------------------------------
# threadlight-qualify — Cowork-safe qualification skill with a vendored runtime.
#
# Unlike the design skill (whole-folder zip), qualify ships a curated FLAT zip:
#   SKILL.md
#   scripts/qualify.py
#   references/sizing-manifest.schema.json
#   references/citadel-sizing.json
#   vendor/model-catalog.json      (dated model catalog, the only vendored data)
#   vendor/cost-runtime.zip        (importable cost engine — code only)
# No tests, fixtures, goldens, or __pycache__ ship. The runtime needs no az /
# azd / Bicep / Docker / customer credentials — pure stdlib Python.
# ---------------------------------------------------------------------------

build_qualify_zip() {
  local skill="threadlight-qualify"
  local skill_root="${SRC_DIR}/${skill}"
  local cq_scripts="${SRC_DIR}/threadlight-consumption-iq/scripts"
  local cq_refs="${SRC_DIR}/threadlight-consumption-iq/references"
  local zip_path="${OUT_DIR}/${skill}.zip"

  if [[ ! -f "${skill_root}/SKILL.md" ]]; then
    echo "ERROR: ${skill_root}/SKILL.md not found — aborting." >&2
    exit 1
  fi

  # Safe temporary staging under the repo (never /tmp) with guaranteed cleanup.
  local build_tmp stage rt
  build_tmp="${REPO_ROOT}/.cowork-build-tmp"
  stage="${build_tmp}/stage.$$"
  rt="${build_tmp}/runtime.$$"
  trap 'rm -rf "${build_tmp}"' RETURN
  rm -rf "${build_tmp}"
  mkdir -p "${stage}" "${rt}"

  # --- inner runtime zip: importable cost engine (code only) ---------------
  mkdir -p "${stage}/vendor"
  cp "${cq_scripts}/cost_api.py" \
     "${cq_scripts}/meter_demand.py" \
     "${cq_scripts}/model_catalog.py" \
     "${cq_scripts}/emitter.py" \
     "${cq_scripts}/pricing_client.py" \
     "${cq_scripts}/discount.py" \
     "${rt}/"
  cp -R "${cq_scripts}/projectors" "${rt}/projectors"
  find "${rt}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "${rt}" -name '*.pyc' -delete 2>/dev/null || true
  ( cd "${rt}" && zip -r --quiet "${stage}/vendor/cost-runtime.zip" . \
      -x "*/__pycache__/*" "*.pyc" "*.DS_Store" )

  # --- outer flat staging: SKILL.md + exactly five companions --------------
  mkdir -p "${stage}/scripts" "${stage}/references"
  cp "${skill_root}/SKILL.md" "${stage}/SKILL.md"
  cp "${skill_root}/scripts/qualify.py" "${stage}/scripts/qualify.py"
  cp "${skill_root}/references/sizing-manifest.schema.json" "${stage}/references/sizing-manifest.schema.json"
  cp "${skill_root}/references/citadel-sizing.json" "${stage}/references/citadel-sizing.json"
  cp "${cq_refs}/model-catalog.json" "${stage}/vendor/model-catalog.json"

  rm -f "${zip_path}"
  ( cd "${stage}" && zip -r --quiet "${zip_path}" . -x "*.DS_Store" "*/__pycache__/*" )

  # --- enforce Cowork limits + the exact-five-companions contract ----------
  local skill_md_bytes companion_count companion_bytes
  skill_md_bytes=$(wc -c < "${stage}/SKILL.md" | tr -d ' ')
  companion_count=$(find "${stage}" -type f ! -name SKILL.md | wc -l | tr -d ' ')
  companion_bytes=$(find "${stage}" -type f ! -name SKILL.md -exec wc -c {} + | tail -n1 | awk '{print $1}')

  echo "✓ ${skill}.zip ($(wc -c < "${zip_path}" | tr -d ' ') bytes)"
  echo "  SKILL.md: ${skill_md_bytes} / 1048576 bytes"
  echo "  companions: ${companion_count} / 20 files, ${companion_bytes} / 10485760 bytes total"

  if ! unzip -l "${zip_path}" | awk '{print $4}' | grep -qx 'SKILL.md'; then
    echo "  ✗ SKILL.md not at zip root — Cowork will reject this archive" >&2; exit 1
  fi
  if (( companion_count != 5 )); then
    echo "  ✗ expected exactly 5 companions, found ${companion_count}" >&2
    unzip -l "${zip_path}" >&2
    exit 1
  fi
  # No tests / goldens / fixtures may leak into the package.
  if unzip -l "${zip_path}" | awk '{print $4}' | grep -Eq '(^|/)tests/|fixtures/|expected/|\.pyc$'; then
    echo "  ✗ package contains tests/goldens/fixtures — not allowed" >&2; exit 1
  fi
  if (( skill_md_bytes > 1048576 )); then
    echo "  ✗ SKILL.md exceeds 1 MB Cowork limit" >&2; exit 1
  fi
  if (( companion_bytes > 10485760 )); then
    echo "  ✗ companion files exceed 10 MB total" >&2; exit 1
  fi
}

build_qualify_zip

echo ""
echo "Done. Commit ${OUT_DIR}/ to publish on GitHub Pages."
