#!/usr/bin/env bash
# start-overclaw.sh — Launch the OverClaw integration stack (nanobot + overstory)
#
# Architecture:
#   nanobot (Ollama Mistral) = Entry Point & Orchestrator
#   overstory = Subagent Coordination & Orchestration
#   Bridge = Seamless API Translation
#
# Usage:
#   ./scripts/start-overclaw.sh          # Start everything
#   ./scripts/start-overclaw.sh status    # Check status
#   ./scripts/start-overclaw.sh stop      # Stop everything

set -euo pipefail

WORKSPACE="/Users/ghost/.openclaw/workspace"
NANOBOT_VENV="/Users/ghost/.nanobot-venv"
OVERSTORY_BIN="$HOME/.bun/bin/overstory"
OLLAMA_BIN="/opt/homebrew/bin/ollama"
NANOBOT_CONFIG="$HOME/.nanobot/config.json"
LOG_DIR="$WORKSPACE/.overstory/logs"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[nanobot+overstory]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✓${NC} $1"; }
warn(){ echo -e "${YELLOW}  ⚠${NC} $1"; }
err() { echo -e "${RED}  ✗${NC} $1"; }

check_status() {
    log "Checking system status..."
    echo ""

    # Ollama
    if pgrep -x "ollama" > /dev/null 2>&1; then
        ok "Ollama: running"
        if "$OLLAMA_BIN" list 2>/dev/null | grep -q "mistral"; then
            ok "  Mistral model: available"
        else
            warn "  Mistral model: not pulled (run: ollama pull mistral)"
        fi
    else
        err "Ollama: not running (run: ollama serve)"
    fi

    # nanobot gateway
    if curl -s http://localhost:18790/health > /dev/null 2>&1; then
        ok "nanobot gateway: running (port 18790)"
    elif pgrep -f "nanobot gateway" > /dev/null 2>&1; then
        warn "nanobot gateway: starting..."
    else
        err "nanobot gateway: not running"
    fi

    # overstory
    if [ -d "$WORKSPACE/.overstory" ]; then
        ok "overstory: initialized"
        cd "$WORKSPACE" && "$OVERSTORY_BIN" status 2>/dev/null || warn "  overstory status unavailable"
    else
        err "overstory: not initialized (run: overstory init)"
    fi

    # Bridge
    if [ -f "$WORKSPACE/skills/nanobot-overstory-bridge/scripts/task_router.py" ]; then
        ok "nanobot-overstory bridge: installed"
    else
        err "nanobot-overstory bridge: not found"
    fi

    # Playwright MCP
    if [ -f "$WORKSPACE/skills/playwright-mcp/scripts/playwright_mcp_server.py" ]; then
        ok "Playwright MCP: installed"
    else
        err "Playwright MCP: not found"
    fi

    # Creative agents
    local agents=("researcher" "social-media-manager" "blogger" "scribe")
    for agent in "${agents[@]}"; do
        if [ -f "$WORKSPACE/.overstory/agent-defs/$agent.md" ]; then
            ok "Agent: $agent"
        else
            err "Agent: $agent (not found)"
        fi
    done

    echo ""
}

start_all() {
    log "Starting nanobot + overstory integration stack..."
    echo ""

    mkdir -p "$LOG_DIR"

    # 1. Start Ollama
    log "Starting Ollama..."
    if pgrep -x "ollama" > /dev/null 2>&1; then
        ok "Ollama already running"
    else
        "$OLLAMA_BIN" serve > "$LOG_DIR/ollama.log" 2>&1 &
        sleep 2
        if pgrep -x "ollama" > /dev/null 2>&1; then
            ok "Ollama started"
        else
            err "Ollama failed to start — check $LOG_DIR/ollama.log"
            return 1
        fi
    fi

    # 2. Ensure Mistral model
    log "Checking Mistral model..."
    if "$OLLAMA_BIN" list 2>/dev/null | grep -q "mistral"; then
        ok "Mistral model available"
    else
        log "Pulling Mistral model (this may take a few minutes)..."
        "$OLLAMA_BIN" pull mistral 2>&1 | tail -3
        ok "Mistral model pulled"
    fi

    # 3. Start nanobot gateway
    log "Starting nanobot gateway..."
    if curl -s http://localhost:18790/health > /dev/null 2>&1; then
        ok "nanobot gateway already running"
    else
        source "$NANOBOT_VENV/bin/activate"
        nanobot gateway > "$LOG_DIR/nanobot-gateway.log" 2>&1 &
        NANOBOT_PID=$!
        echo "$NANOBOT_PID" > "$LOG_DIR/nanobot-gateway.pid"

        local retries=0
        while [ $retries -lt 15 ]; do
            if curl -s http://localhost:18790/health > /dev/null 2>&1; then
                ok "nanobot gateway started (PID: $NANOBOT_PID, port 18790)"
                break
            fi
            retries=$((retries + 1))
            sleep 1
        done

        if [ $retries -ge 15 ]; then
            warn "nanobot gateway may still be starting — check $LOG_DIR/nanobot-gateway.log"
        fi
    fi

    # 4. Verify overstory
    log "Verifying overstory..."
    if [ -d "$WORKSPACE/.overstory" ]; then
        ok "overstory initialized"
    else
        cd "$WORKSPACE" && "$OVERSTORY_BIN" init 2>&1
        ok "overstory initialized"
    fi

    # 5. Generate skills manifest and agent context
    log "Generating skills manifest and agent context..."
    python3 "$WORKSPACE/skills/nanobot-overstory-bridge/scripts/generate_agent_context.py" \
        --output "$WORKSPACE/.overstory/gateway-context.md" \
        --manifest "$WORKSPACE/.overstory/skills-manifest.json" 2>/dev/null
    ok "Skills manifest and agent context generated"

    echo ""
    log "Stack is ready!"
    echo ""
    echo "  nanobot gateway:  http://localhost:18790"
    echo "  Ollama (Mistral): http://localhost:11434"
    echo "  overstory:        $WORKSPACE/.overstory/"
    echo ""
    echo "  Bridge scripts:   $WORKSPACE/skills/nanobot-overstory-bridge/scripts/"
    echo "  Playwright MCP:   $WORKSPACE/skills/playwright-mcp/scripts/"
    echo "  Creative agents:  $WORKSPACE/.overstory/agent-defs/"
    echo ""
    echo "  Logs:             $LOG_DIR/"
    echo ""
}

stop_all() {
    log "Stopping nanobot + overstory stack..."

    if [ -f "$LOG_DIR/nanobot-gateway.pid" ]; then
        local pid
        pid=$(cat "$LOG_DIR/nanobot-gateway.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            ok "nanobot gateway stopped (PID: $pid)"
        fi
        rm -f "$LOG_DIR/nanobot-gateway.pid"
    else
        pkill -f "nanobot gateway" 2>/dev/null && ok "nanobot gateway stopped" || warn "nanobot gateway not running"
    fi

    ok "Ollama left running (stop manually with: pkill ollama)"
    echo ""
}

case "${1:-start}" in
    start)   start_all ;;
    stop)    stop_all ;;
    status)  check_status ;;
    restart) stop_all; sleep 2; start_all ;;
    *)       echo "Usage: $0 {start|stop|status|restart}"; exit 1 ;;
esac
