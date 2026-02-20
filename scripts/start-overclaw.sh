#!/usr/bin/env bash
# start-overclaw.sh — Launch the OverClaw integration stack
#
# Architecture:
#   OverClaw Gateway (port 18800) = HTTP API entry point
#   Ollama Mistral                = Orchestrator LLM
#   nanobot agent                 = Background agent (heartbeat, cron, channels)
#   overstory                     = Subagent coordination & swarm
#
# Usage:
#   ./scripts/start-overclaw.sh          # Start everything
#   ./scripts/start-overclaw.sh status   # Check status
#   ./scripts/start-overclaw.sh stop     # Stop everything

set -euo pipefail

# Resolve paths relative to this script (portable — works on any machine)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$SCRIPT_DIR/.." && pwd)"
export OVERCLAW_WORKSPACE="$WORKSPACE"
NANOBOT_VENV="${NANOBOT_VENV:-$HOME/.nanobot-venv}"
# Also check overclaw-venv (from install.sh)
if [ ! -d "$NANOBOT_VENV" ] && [ -d "$HOME/.overclaw-venv" ]; then
    NANOBOT_VENV="$HOME/.overclaw-venv"
fi
OVERSTORY_BIN="${OVERSTORY_BIN:-$(command -v overstory 2>/dev/null || echo "$HOME/.bun/bin/overstory")}"
OLLAMA_BIN="${OLLAMA_BIN:-$(command -v ollama 2>/dev/null || echo "/opt/homebrew/bin/ollama")}"
LOG_DIR="$WORKSPACE/.overstory/logs"
GATEWAY_SCRIPT="$WORKSPACE/scripts/overclaw_gateway.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[overclaw]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✓${NC} $1"; }
warn(){ echo -e "${YELLOW}  ⚠${NC} $1"; }
err() { echo -e "${RED}  ✗${NC} $1"; }

check_status() {
    log "Checking OverClaw stack status..."
    echo ""

    # OverClaw Gateway
    if curl -sf http://localhost:18800/health > /dev/null 2>&1; then
        local uptime
        uptime=$(curl -sf http://localhost:18800/health 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('uptime_s','?'))" 2>/dev/null || echo "?")
        ok "OverClaw Gateway: running (port 18800, uptime ${uptime}s)"
    else
        err "OverClaw Gateway: not running"
    fi

    # Ollama
    if pgrep -x "ollama" > /dev/null 2>&1; then
        ok "Ollama: running"
        if "$OLLAMA_BIN" list 2>/dev/null | grep -iq "mistral" || curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q "mistral"; then
            ok "  Mistral model: available"
        else
            warn "  Mistral model: not pulled (run: ollama pull mistral)"
        fi
    else
        err "Ollama: not running"
    fi

    # nanobot agent
    if [ -f "$LOG_DIR/nanobot-agent.pid" ]; then
        local pid
        pid=$(cat "$LOG_DIR/nanobot-agent.pid")
        if kill -0 "$pid" 2>/dev/null; then
            ok "nanobot agent: running (PID: $pid)"
        else
            err "nanobot agent: dead (stale PID: $pid)"
        fi
    else
        warn "nanobot agent: not tracked"
    fi

    # overstory
    if [ -d "$WORKSPACE/.overstory" ]; then
        ok "overstory: initialized"
        cd "$WORKSPACE" && "$OVERSTORY_BIN" status 2>/dev/null || warn "  overstory status unavailable"
    else
        err "overstory: not initialized"
    fi

    # Bridge
    if [ -f "$WORKSPACE/skills/nanobot-overstory-bridge/scripts/task_router.py" ]; then
        ok "Bridge: installed"
    else
        err "Bridge: not found"
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
    log "Starting OverClaw stack..."
    echo ""

    mkdir -p "$LOG_DIR"

    # 1. Ollama
    log "1/5 Ollama..."
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

    # 2. Mistral model
    log "2/5 Mistral model..."
    if "$OLLAMA_BIN" list 2>/dev/null | grep -iq "mistral" || curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q "mistral"; then
        ok "Mistral available"
    else
        log "Pulling Mistral (this may take a few minutes)..."
        "$OLLAMA_BIN" pull mistral 2>&1 | tail -3
        ok "Mistral pulled"
    fi

    # 3. nanobot background agent (channels, heartbeat, cron)
    log "3/5 nanobot agent..."
    if [ -f "$LOG_DIR/nanobot-agent.pid" ] && kill -0 "$(cat "$LOG_DIR/nanobot-agent.pid")" 2>/dev/null; then
        ok "nanobot agent already running"
    else
        source "$NANOBOT_VENV/bin/activate"
        nanobot gateway > "$LOG_DIR/nanobot-agent.log" 2>&1 &
        local nb_pid=$!
        echo "$nb_pid" > "$LOG_DIR/nanobot-agent.pid"
        sleep 2
        if kill -0 "$nb_pid" 2>/dev/null; then
            ok "nanobot agent started (PID: $nb_pid)"
        else
            warn "nanobot agent may have failed — check $LOG_DIR/nanobot-agent.log"
        fi
    fi

    # 4. OverClaw HTTP Gateway
    log "4/5 OverClaw Gateway..."
    if curl -sf http://localhost:18800/health > /dev/null 2>&1; then
        ok "OverClaw Gateway already running"
    else
        source "$NANOBOT_VENV/bin/activate"
        # Resolve real Claude Code binary before prepending wrapper to PATH (so overstory agents get a PTY and stay running)
        CLAUDE_CODE_BIN="${CLAUDE_CODE_BIN:-$(command -v claude 2>/dev/null || echo "/opt/homebrew/bin/claude")}"
        export CLAUDE_CODE_BIN
        export PATH="$WORKSPACE/.overstory/bin:$PATH"
        unset CI
        export TERM="${TERM:-xterm-256color}"
        python3 "$GATEWAY_SCRIPT" > "$LOG_DIR/overclaw-gateway.log" 2>&1 &
        local gw_pid=$!
        echo "$gw_pid" > "$LOG_DIR/overclaw-gateway.pid"

        local retries=0
        while [ $retries -lt 10 ]; do
            if curl -sf http://localhost:18800/health > /dev/null 2>&1; then
                ok "OverClaw Gateway started (PID: $gw_pid, port 18800)"
                break
            fi
            retries=$((retries + 1))
            sleep 1
        done

        if [ $retries -ge 10 ]; then
            err "OverClaw Gateway failed to start — check $LOG_DIR/overclaw-gateway.log"
            return 1
        fi
    fi

    # 5. overstory + manifests
    log "5/5 overstory & manifests..."
    if [ -d "$WORKSPACE/.overstory" ]; then
        ok "overstory initialized"
    else
        cd "$WORKSPACE" && "$OVERSTORY_BIN" init 2>&1
        ok "overstory initialized"
    fi

    python3 "$WORKSPACE/skills/nanobot-overstory-bridge/scripts/generate_agent_context.py" \
        --output "$WORKSPACE/.overstory/gateway-context.md" \
        --manifest "$WORKSPACE/.overstory/skills-manifest.json" 2>/dev/null
    ok "Skills manifest generated"

    # Gateway verification: ensure all expected endpoints respond
    log "Verifying gateway endpoints..."
    local vfail=0
    for endpoint in /health /api/status /api/skills /api/tools; do
        if curl -sf "http://localhost:18800$endpoint" > /dev/null 2>&1; then
            ok "  $endpoint"
        else
            warn "  $endpoint not responding"
            vfail=$((vfail + 1))
        fi
    done
    if [ -f "$WORKSPACE/.overstory/skills-manifest.json" ]; then
        ok "  skills-manifest.json present"
    else
        warn "  skills-manifest.json missing"
        vfail=$((vfail + 1))
    fi
    if [ "$vfail" -gt 0 ]; then
        warn "Gateway verification had $vfail issue(s) — check logs"
    else
        ok "Gateway verification passed"
    fi

    echo ""
    log "OverClaw is running!"
    echo ""
    echo -e "  ${CYAN}OverClaw Gateway${NC}  http://localhost:18800"
    echo -e "  ${CYAN}  /health${NC}         Health check"
    echo -e "  ${CYAN}  /api/status${NC}     Full stack status"
    echo -e "  ${CYAN}  /api/chat${NC}       Chat with Mistral orchestrator"
    echo -e "  ${CYAN}  /api/route${NC}      Route task to agent capability"
    echo -e "  ${CYAN}  /api/agents${NC}     List / spawn / inspect agents"
    echo -e "  ${CYAN}  /api/skills${NC}     Discover skills"
    echo -e "  ${CYAN}  /api/tools${NC}      Discover tools"
    echo -e "  ${CYAN}  /api/memory${NC}     Read/write MEMORY.md"
    echo ""
    echo -e "  ${CYAN}Ollama (Mistral)${NC}  http://localhost:11434"
    echo -e "  ${CYAN}Logs${NC}              $LOG_DIR/"
    echo ""
}

stop_all() {
    log "Stopping OverClaw stack..."

    # OverClaw Gateway
    if [ -f "$LOG_DIR/overclaw-gateway.pid" ]; then
        local pid
        pid=$(cat "$LOG_DIR/overclaw-gateway.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            ok "OverClaw Gateway stopped (PID: $pid)"
        fi
        rm -f "$LOG_DIR/overclaw-gateway.pid"
    else
        pkill -f "overclaw_gateway.py" 2>/dev/null && ok "OverClaw Gateway stopped" || warn "OverClaw Gateway not running"
    fi

    # nanobot agent
    if [ -f "$LOG_DIR/nanobot-agent.pid" ]; then
        local pid
        pid=$(cat "$LOG_DIR/nanobot-agent.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            ok "nanobot agent stopped (PID: $pid)"
        fi
        rm -f "$LOG_DIR/nanobot-agent.pid"
    else
        pkill -f "nanobot gateway" 2>/dev/null && ok "nanobot agent stopped" || warn "nanobot agent not running"
    fi

    ok "Ollama left running (stop with: pkill ollama)"
    echo ""
}

case "${1:-start}" in
    start)   start_all ;;
    stop)    stop_all ;;
    status)  check_status ;;
    restart) stop_all; sleep 2; start_all ;;
    *)       echo "Usage: $0 {start|stop|status|restart}"; exit 1 ;;
esac
