#!/usr/bin/env bash
# Threadlight Skills — devcontainer post-create.
#
# Installs GitHub Copilot CLI and wires all 16 threadlight skills from this
# checkout, so a fresh Codespace is ready to explore the pipeline. Safe to
# re-run (idempotent) and non-fatal: a hiccup here should not fail container
# creation — it just prints guidance.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKETPLACE_NAME="threadlight-skills"
PLUGIN_REF="threadlight-skills@threadlight-skills"

log()  { printf '\033[1;36m[threadlight]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[threadlight]\033[0m %s\n' "$*" >&2; }

# 1. Install Copilot CLI (skip if already on PATH).
if command -v copilot >/dev/null 2>&1; then
  log "Copilot CLI already installed: $(copilot --version 2>/dev/null | head -1)"
else
  log "Installing GitHub Copilot CLI..."
  if command -v sudo >/dev/null 2>&1; then
    # Root install lands in /usr/local/bin (already on PATH).
    curl -fsSL https://gh.io/copilot-install | sudo bash || warn "Copilot CLI install script failed."
  else
    # Fallback: user install to ~/.local (ensure it is on PATH).
    curl -fsSL https://gh.io/copilot-install | bash || warn "Copilot CLI install script failed."
    case ":$PATH:" in
      *":$HOME/.local/bin:"*) : ;;
      *) echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" ;;
    esac
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

if ! command -v copilot >/dev/null 2>&1; then
  warn "Copilot CLI is not available; skipping skill wiring."
  warn "Install it manually: https://gh.io/copilot-install"
  exit 0
fi

# 2. Register this checkout as a local plugin marketplace (idempotent).
if copilot plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE_NAME"; then
  log "Local marketplace already registered."
else
  log "Registering local marketplace from $REPO_ROOT ..."
  copilot plugin marketplace add "$REPO_ROOT" || warn "Could not add local marketplace."
fi

# 3. Install the threadlight skills from the local marketplace (idempotent).
if copilot plugin list 2>/dev/null | grep -q "$MARKETPLACE_NAME"; then
  log "threadlight-skills plugin already installed."
else
  log "Installing threadlight skills..."
  copilot plugin install "$PLUGIN_REF" || warn "Could not install threadlight-skills plugin."
fi

# 4. Next-steps banner.
cat <<'BANNER'

  ─────────────────────────────────────────────────────────────
   Threadlight Skills — ready.
   Next:
     1. Run:  copilot
     2. On first launch, sign in with:  /login
     3. Try:  "use threadlight-design to draft a SPEC from this brief: ..."

   Note: this is a thin, consumer image. Azure deploy tooling
   (azd/az/bicep/Docker) and some MCP/agent tools (e.g. workiq)
   are not installed here — use a full local/VNet environment for
   the deploy and production legs.
  ─────────────────────────────────────────────────────────────

BANNER
