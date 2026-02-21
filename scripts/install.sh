#!/usr/bin/env bash
# install.sh — Install all OverClaw prerequisites
#
# Installs: Ollama, Mistral model, tmux, bun, Claude Code CLI,
#           Python venv with nanobot + gateway deps, overstory,
#           Playwright browsers
#
# Safe to run multiple times (idempotent).
#
# Usage:
#   ./scripts/install.sh           # Full install
#   ./scripts/install.sh --check   # Check what's installed/missing

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve paths relative to this script (portable — no hardcoded user paths)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${OVERCLAW_VENV:-$HOME/.overclaw-venv}"
NANOBOT_VENV="${NANOBOT_VENV:-$HOME/.nanobot-venv}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[overclaw-install]${NC} $1"; }
ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }
err()  { echo -e "${RED}  ✗${NC} $1"; }

MISSING=()

# ---------------------------------------------------------------------------
# Detect platform
# ---------------------------------------------------------------------------
OS="$(uname -s)"
ARCH="$(uname -m)"

has_cmd() { command -v "$1" &>/dev/null; }

install_brew_if_needed() {
    if ! has_cmd brew; then
        log "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if [[ "$ARCH" == "arm64" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        else
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        ok "Homebrew installed"
    fi
}

# ---------------------------------------------------------------------------
# Check mode
# ---------------------------------------------------------------------------
check_only() {
    log "Checking OverClaw prerequisites..."
    echo ""

    local all_ok=true

    # Python 3.10+
    if has_cmd python3; then
        local pyver
        pyver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        ok "Python $pyver"
    else
        err "Python 3 — not found"; all_ok=false
    fi

    # Ollama
    if has_cmd ollama; then
        ok "Ollama"
        if ollama list 2>/dev/null | grep -iq "mistral"; then
            ok "  Mistral model"
        elif curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q "mistral"; then
            ok "  Mistral model (via API)"
        else
            warn "  Mistral model — not pulled"; all_ok=false
        fi
    else
        err "Ollama — not found"; all_ok=false
    fi

    # tmux
    if has_cmd tmux; then ok "tmux"; else err "tmux — not found"; all_ok=false; fi

    # bun
    if has_cmd bun; then ok "bun"; else err "bun — not found"; all_ok=false; fi

    # beads (bd CLI)
    if has_cmd bd; then
        ok "beads (bd CLI) — $(bd --version 2>&1 | head -1 | grep -o 'version [0-9.]*' || echo 'installed')"
    else
        err "beads (bd CLI) — not found"; all_ok=false
    fi

    # Claude Code CLI
    if has_cmd claude; then
        ok "Claude Code CLI ($(claude --version 2>&1 | head -1))"
    else
        err "Claude Code CLI — not found"; all_ok=false
    fi

    # git
    if has_cmd git; then ok "git"; else err "git — not found"; all_ok=false; fi

    # curl
    if has_cmd curl; then ok "curl"; else err "curl — not found"; all_ok=false; fi

    # nanobot venv
    if [ -f "$NANOBOT_VENV/bin/nanobot" ]; then
        ok "nanobot venv ($NANOBOT_VENV)"
    elif [ -f "$VENV_DIR/bin/nanobot" ]; then
        ok "nanobot venv ($VENV_DIR)"
    else
        err "nanobot — not installed in venv"; all_ok=false
    fi

    # Python deps in venv
    local venv_python=""
    if [ -f "$NANOBOT_VENV/bin/python3" ]; then venv_python="$NANOBOT_VENV/bin/python3";
    elif [ -f "$VENV_DIR/bin/python3" ]; then venv_python="$VENV_DIR/bin/python3"; fi

    if [ -n "$venv_python" ]; then
        for pkg in starlette uvicorn httpx playwright flask flask_cors; do
            pkg_import=$(echo "$pkg" | sed 's/-/_/g')
            if "$venv_python" -c "import $pkg_import" 2>/dev/null; then
                ok "  Python: $pkg"
            else
                warn "  Python: $pkg — missing"; all_ok=false
            fi
        done
    fi

    # overstory
    if has_cmd overstory || [ -f "$HOME/.bun/bin/overstory" ]; then
        ok "overstory"
    else
        err "overstory — not found"; all_ok=false
    fi

    # mulch (expertise CLI for overstory agents)
    if has_cmd mulch || [ -f "$HOME/.bun/bin/mulch" ]; then
        ok "mulch (expertise CLI)"
    else
        err "mulch — not found (npm/bun: mulch-cli)"; all_ok=false
    fi

    # Playwright browsers
    if [ -n "$venv_python" ]; then
        if "$venv_python" -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
            ok "  Playwright browsers"
        else
            warn "  Playwright browsers — not installed"
        fi
    fi

    echo ""
    if $all_ok; then
        ok "All prerequisites satisfied!"
    else
        err "Some prerequisites missing — run: ./scripts/install.sh"
    fi
}

# ---------------------------------------------------------------------------
# Full install
# ---------------------------------------------------------------------------
install_all() {
    log "Installing OverClaw prerequisites..."
    log "  Workspace: $WORKSPACE"
    log "  Venv:      $VENV_DIR"
    echo ""

    # 1. Homebrew (macOS)
    if [[ "$OS" == "Darwin" ]]; then
        log "1/16 Homebrew..."
        install_brew_if_needed
        ok "Homebrew ready"
    else
        log "1/16 Skipping Homebrew (not macOS)"
    fi

    # 2. Ollama
    log "2/16 Ollama..."
    if has_cmd ollama; then
        ok "Ollama already installed"
    else
        if [[ "$OS" == "Darwin" ]]; then
            brew install ollama
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
        ok "Ollama installed"
    fi

    # 3. tmux
    log "3/16 tmux..."
    if has_cmd tmux; then
        ok "tmux already installed"
    else
        if [[ "$OS" == "Darwin" ]]; then
            brew install tmux
        else
            sudo apt-get install -y tmux 2>/dev/null || sudo yum install -y tmux 2>/dev/null || {
                err "Could not install tmux — install manually"
            }
        fi
        ok "tmux installed"
    fi

    # 4. bun
    log "4/16 bun..."
    if has_cmd bun; then
        ok "bun already installed"
    else
        curl -fsSL https://bun.sh/install | bash
        export PATH="$HOME/.bun/bin:$PATH"
        ok "bun installed"
    fi

    # 4b. beads (bd CLI)
    log "4b/16 beads (bd CLI)..."
    if has_cmd bd; then
        ok "beads (bd CLI) already installed ($(bd --version 2>&1 | head -1 | grep -o 'version [0-9.]*' || echo 'installed'))"
    else
        if [[ "$OS" == "Darwin" ]]; then
            # Try Homebrew first
            if has_cmd brew; then
                brew install beads 2>/dev/null || {
                    # If brew link fails due to npm symlink conflict, remove it and retry
                    if [ -L /opt/homebrew/bin/bd ] && [ ! -f "$(readlink /opt/homebrew/bin/bd)" ]; then
                        rm /opt/homebrew/bin/bd 2>/dev/null || true
                        brew link --overwrite beads 2>/dev/null || true
                    fi
                }
                ok "beads installed via Homebrew"
            else
                # Fallback to install script
                curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash
                ok "beads installed via install script"
            fi
        else
            # Linux: use install script
            curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash
            ok "beads installed via install script"
        fi
    fi

    # Initialize beads if not already initialized
    if [ ! -d "$WORKSPACE/.beads" ]; then
        log "  Initializing beads in workspace..."
        cd "$WORKSPACE"
        bd init > /dev/null 2>&1 || warn "beads init failed (may already be initialized)"
        ok "beads initialized"
    else
        ok "beads already initialized"
    fi

    # 5. Claude Code CLI
    log "5/16 Claude Code CLI..."
    if has_cmd claude; then
        ok "Claude Code already installed ($(claude --version 2>&1 | head -1))"
    else
        if has_cmd npm; then
            npm install -g @anthropic-ai/claude-code
            ok "Claude Code installed via npm"
        elif has_cmd bun; then
            bun install -g @anthropic-ai/claude-code
            ok "Claude Code installed via bun"
        else
            warn "Claude Code not installed — install manually: npm install -g @anthropic-ai/claude-code"
            MISSING+=("claude")
        fi
    fi

    # 6. Python venv + nanobot + gateway deps
    log "6/16 Python venv + nanobot + dependencies..."
    local use_venv="$VENV_DIR"
    if [ -f "$NANOBOT_VENV/bin/nanobot" ]; then
        use_venv="$NANOBOT_VENV"
        ok "Using existing nanobot venv: $NANOBOT_VENV"
    fi

    if [ ! -d "$use_venv" ]; then
        python3 -m venv "$use_venv"
        ok "Created venv: $use_venv"
    fi

    source "$use_venv/bin/activate"

    pip install --upgrade pip --quiet

    # nanobot
    if ! "$use_venv/bin/python3" -c "import nanobot" 2>/dev/null; then
        log "  Installing nanobot..."
        if [ -d "/tmp/nanobot-install" ]; then
            pip install -e /tmp/nanobot-install --quiet
        else
            pip install nanobot-ai --quiet 2>/dev/null || {
                log "  Cloning nanobot from source..."
                git clone https://github.com/HKUDS/nanobot.git /tmp/nanobot-install 2>/dev/null
                pip install -e /tmp/nanobot-install --quiet
            }
        fi
        ok "nanobot installed"
    else
        ok "nanobot already installed"
    fi

    # Gateway deps (starlette, uvicorn, httpx, playwright, flask, flask-cors)
    log "  Installing gateway dependencies..."
    pip install starlette uvicorn httpx playwright flask flask-cors --quiet
    ok "Gateway dependencies installed"

    # Playwright browsers
    log "  Installing Playwright browsers..."
    "$use_venv/bin/python3" -m playwright install chromium 2>/dev/null || {
        warn "Playwright browser install failed — run manually: python3 -m playwright install chromium"
    }
    ok "Playwright browsers ready"

    deactivate 2>/dev/null || true

    # 7. overstory
    log "7/16 overstory..."
    if has_cmd overstory || [ -f "$HOME/.bun/bin/overstory" ]; then
        ok "overstory already installed"
    else
        if [ -d "/tmp/overstory-install" ]; then
            cd /tmp/overstory-install && bun install && bun link
            ok "overstory installed from local source"
        else
            bun install -g overstory 2>/dev/null || {
                log "  Cloning overstory from source..."
                git clone https://github.com/jayminwest/overstory.git /tmp/overstory-install
                cd /tmp/overstory-install && bun install && bun link
            }
            ok "overstory installed"
        fi
    fi

    # 7a. mulch (expertise CLI — overstory agents use mulch prime/record/learn)
    log "7a/16 mulch (expertise CLI)..."
    export PATH="${HOME}/.bun/bin:${HOME}/.local/bin:${PATH}"
    if has_cmd mulch || [ -f "$HOME/.bun/bin/mulch" ]; then
        ok "mulch already installed ($(mulch --version 2>/dev/null || echo 'mulch-cli'))"
    else
        bun install -g mulch-cli 2>/dev/null || npm install -g mulch-cli 2>/dev/null || {
            err "Could not install mulch-cli — run: bun install -g mulch-cli or npm install -g mulch-cli"
            MISSING+=("mulch-cli")
        }
        if has_cmd mulch || [ -f "$HOME/.bun/bin/mulch" ]; then
            ok "mulch installed"
        fi
    fi
    if [ -d "$WORKSPACE/.overstory" ] && [ ! -d "$WORKSPACE/.mulch" ]; then
        log "  Initializing mulch in workspace..."
        (cd "$WORKSPACE" && mulch init >/dev/null 2>&1) || true
        [ -d "$WORKSPACE/.mulch" ] && ok "  mulch initialized in workspace" || true
    fi

    # 8. Ollama Mistral model
    log "8/16 Ollama Mistral model..."
    if ! pgrep -x "ollama" > /dev/null 2>&1; then
        log "  Starting Ollama..."
        ollama serve > /dev/null 2>&1 &
        sleep 3
    fi
    if ollama list 2>/dev/null | grep -q "mistral"; then
        ok "Mistral model already pulled"
    else
        log "  Pulling Mistral (4.1 GB, may take a few minutes)..."
        ollama pull mistral
        ok "Mistral model pulled"
    fi

    # 9. overstory init + manifests
    log "9/16 Workspace initialization..."
    cd "$WORKSPACE"
    if [ ! -d "$WORKSPACE/.overstory" ]; then
        "$HOME/.bun/bin/overstory" init 2>/dev/null || overstory init 2>/dev/null || warn "overstory init failed"
        ok "overstory initialized"
    else
        ok "overstory already initialized"
    fi

    # nanobot config
    local nanobot_config="$HOME/.nanobot/config.json"
    if [ ! -f "$nanobot_config" ]; then
        mkdir -p "$HOME/.nanobot"
        cat > "$nanobot_config" << 'NBCFG'
{
  "providers": {
    "ollama": {
      "apiKey": "ollama",
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "agents": {
    "defaults": {
      "workspace": "",
      "model": "ollama/mistral:latest",
      "maxTokens": 8192,
      "temperature": 0.7,
      "maxToolIterations": 20,
      "memoryWindow": 50
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18800
  },
  "tools": {
    "mcpServers": {
      "playwright": {
        "command": "python3",
        "args": []
      }
    },
    "exec": {
      "timeout": 120
    }
  }
}
NBCFG
        # Patch workspace-specific paths
        local escaped_ws
        escaped_ws=$(echo "$WORKSPACE" | sed 's/[\/&]/\\&/g')
        sed -i.bak "s/\"workspace\": \"\"/\"workspace\": \"$escaped_ws\"/" "$nanobot_config"
        sed -i.bak "s|\"args\": \[\]|\"args\": [\"$escaped_ws/skills/playwright-mcp/scripts/playwright_mcp_server.py\"]|" "$nanobot_config"
        rm -f "${nanobot_config}.bak"
        ok "nanobot config created"
    else
        ok "nanobot config exists"
    fi

    # 10. Bundled skills: verify local + install external
    log "10/16 Bundled skills..."
    LOCAL_SKILLS=(nanobot-overstory-bridge overstory-integration skills-compat agent-swarm creative-agents playwright-mcp remotion-video goals)
    for skill in "${LOCAL_SKILLS[@]}"; do
        if [ -d "$WORKSPACE/skills/$skill" ]; then
            ok "  Found local skill: $skill"
        else
            err "  Local skill not found: $skill (expected in $WORKSPACE/skills/)"
            exit 1
        fi
    done

    if [ ! -d "$WORKSPACE/skills/last30days" ]; then
        log "  Installing last30days from GitHub..."
        git clone --depth 1 https://github.com/mvanhorn/last30days-skill.git "$WORKSPACE/skills/last30days" 2>/dev/null || {
            err "  Failed to clone last30days — run: git clone https://github.com/mvanhorn/last30days-skill.git $WORKSPACE/skills/last30days"
            MISSING+=("last30days")
        }
        [ -d "$WORKSPACE/skills/last30days" ] && ok "  last30days installed"
    else
        ok "  last30days already present"
    fi

    if [ ! -d "$WORKSPACE/skills/humanizer" ]; then
        log "  Installing humanizer (ClawHub: biostartechnology/humanizer)..."
        if git clone --depth 1 https://github.com/biostartechnology/humanizer.git "$WORKSPACE/skills/humanizer" 2>/dev/null; then
            ok "  humanizer installed from GitHub"
        else
            warn "  humanizer not found at GitHub — install manually from https://clawhub.ai/biostartechnology/humanizer into $WORKSPACE/skills/humanizer"
            MISSING+=("humanizer")
        fi
    else
        ok "  humanizer already present"
    fi

    # 11. Skill dependencies (requirements.txt for bundled skills)
    log "11/16 Skill dependencies..."
    BUNDLED_SKILLS=(nanobot-overstory-bridge overstory-integration skills-compat agent-swarm creative-agents playwright-mcp remotion-video goals last30days humanizer)
    for skill in "${BUNDLED_SKILLS[@]}"; do
        req_file="$WORKSPACE/skills/$skill/requirements.txt"
        if [ -f "$req_file" ]; then
            source "$use_venv/bin/activate"
            pip install -r "$req_file" --quiet 2>/dev/null && ok "  Dependencies for $skill" || warn "  Some deps failed for $skill"
            deactivate 2>/dev/null || true
        fi
    done
    ok "Skill dependencies done"

    # 12. Verify system tools (Bun for remotion-video)
    log "12/16 System tools..."
    if has_cmd bun; then
        ok "  Bun available for remotion-video: $(bun --version 2>/dev/null || true)"
    else
        err "  Bun required for remotion-video — install: curl -fsSL https://bun.sh/install | bash"
        MISSING+=("bun")
    fi

    # 13. Messaging channel setup (optional)
    log "13/16 Messaging channels (optional)..."
    if [ -t 0 ] && read -p "Configure Discord bot? (y/n) " -n 1 -r 2>/dev/null; then
        echo ""
        if [[ "$REPLY" =~ ^[Yy]$ ]]; then
            read -p "  Discord bot token (or leave empty to skip): " -r DISCORD_TOKEN
            if [ -n "$DISCORD_TOKEN" ]; then
                export DISCORD_BOT_TOKEN="$DISCORD_TOKEN"
                ok "  Discord token set (add to ~/.nanobot/config.json or env)"
            fi
        fi
    else
        ok "  Skipped (non-interactive)"
    fi

    # 14. Gateway verification (non-blocking)
    log "14/16 Gateway verification..."
    if curl -sf http://localhost:18800/health > /dev/null 2>&1; then
        ok "  Gateway already responding on 18800"
    else
        warn "  Gateway not running — start with: ./scripts/start-overclaw.sh"
    fi
    for endpoint in /health /api/status /api/skills /api/tools; do
        if curl -sf "http://localhost:18800$endpoint" > /dev/null 2>&1; then
            ok "  Endpoint $endpoint OK"
        else
            warn "  Endpoint $endpoint not available (start gateway first)"
        fi
    done

    # 15. Regenerate manifests (includes all 10 bundled skills)
    log "15/16 Regenerating manifests..."
    source "$use_venv/bin/activate"
    NANOBOT_WORKSPACE="$WORKSPACE" NANOBOT_SKILLS_DIR="$WORKSPACE/skills" python3 "$WORKSPACE/skills/nanobot-overstory-bridge/scripts/generate_agent_context.py" \
        --output "$WORKSPACE/.overstory/gateway-context.md" \
        --manifest "$WORKSPACE/.overstory/skills-manifest.json" 2>/dev/null || true
    deactivate 2>/dev/null || true
    ok "Skills manifest generated"

    echo ""
    log "Installation complete!"
    echo ""
    if [ ${#MISSING[@]} -gt 0 ]; then
        warn "Some optional components need manual install: ${MISSING[*]}"
        echo ""
    fi
    echo -e "  Next steps:"
    echo -e "    ${CYAN}./scripts/start-overclaw.sh${NC}          Start the stack"
    echo -e "    ${CYAN}./scripts/start-overclaw.sh status${NC}   Check status"
    echo -e "    ${CYAN}curl http://localhost:18800/health${NC}   Test gateway"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:---install}" in
    --check|check)   check_only ;;
    --install|install|"") install_all ;;
    *)               echo "Usage: $0 [--check | --install]"; exit 1 ;;
esac
