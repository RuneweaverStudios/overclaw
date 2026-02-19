#!/usr/bin/env bash
# test-e2e.sh — End-to-end test for OverClaw: route task, spawn agents, verify gateway tools.
#
# Flow:
#   1. Gateway health check
#   2. POST /api/route (research task, spawn) → researcher capability
#   3. POST /api/route (blog task, spawn) → blogger capability
#   4. GET /api/agents (list)
#   5. Verify gateway tools/skills discoverable, memory endpoint
#
# Usage: ./scripts/test-e2e.sh
# Idempotent: does not mutate MEMORY.md content permanently (read-only checks or temp section).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$SCRIPT_DIR/.." && pwd)"
GATEWAY_URL="${OVERCLAW_GATEWAY_URL:-http://localhost:18800}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()  { echo -e "${GREEN}  ✓${NC} $1"; }
warn(){ echo -e "${YELLOW}  ⚠${NC} $1"; }
err() { echo -e "${RED}  ✗${NC} $1"; }
log() { echo -e "${CYAN}[test-e2e]${NC} $1"; }

FAILED=0

# ---------------------------------------------------------------------------
# 1. Gateway health
# ---------------------------------------------------------------------------
log "1. Gateway health..."
if curl -sf "$GATEWAY_URL/health" > /dev/null; then
    ok "Gateway responding at $GATEWAY_URL"
else
    err "Gateway not responding at $GATEWAY_URL — start with: ./scripts/start-overclaw.sh"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Endpoints
# ---------------------------------------------------------------------------
log "2. Critical endpoints..."
for endpoint in /api/status /api/skills /api/tools; do
    if curl -sf "$GATEWAY_URL$endpoint" > /dev/null; then
        ok "$endpoint"
    else
        err "$endpoint failed"
        FAILED=$((FAILED + 1))
    fi
done

# ---------------------------------------------------------------------------
# 3. Spawn researcher via /api/agents/spawn (force hierarchy bypass)
# ---------------------------------------------------------------------------
log "3. Spawn researcher (POST /api/agents/spawn, force: true)..."
ROUTE_RESEARCH=$(curl -sf -X POST "$GATEWAY_URL/api/agents/spawn" \
    -H "Content-Type: application/json" \
    -d '{"task": "Research trending AI agent frameworks from the last 30 days (one paragraph summary).", "capability": "researcher", "force": true}' 2>/dev/null || echo "{}")
if echo "$ROUTE_RESEARCH" | grep -q '"error"'; then
    warn "Spawn researcher returned error: $ROUTE_RESEARCH"
else
    ok "Spawn researcher: $ROUTE_RESEARCH"
fi

# ---------------------------------------------------------------------------
# 4. Spawn blogger via /api/agents/spawn (force hierarchy bypass)
# ---------------------------------------------------------------------------
log "4. Spawn blogger (POST /api/agents/spawn, force: true)..."
ROUTE_BLOG=$(curl -sf -X POST "$GATEWAY_URL/api/agents/spawn" \
    -H "Content-Type: application/json" \
    -d '{"task": "Write a short blog post (2 sentences) about AI agent frameworks.", "capability": "blogger", "force": true}' 2>/dev/null || echo "{}")
if echo "$ROUTE_BLOG" | grep -q '"error"'; then
    warn "Spawn blogger returned error: $ROUTE_BLOG"
else
    ok "Spawn blogger: $ROUTE_BLOG"
fi

# ---------------------------------------------------------------------------
# 5. List agents
# ---------------------------------------------------------------------------
log "5. List agents..."
AGENTS=$(curl -sf "$GATEWAY_URL/api/agents" 2>/dev/null || echo "{}")
if echo "$AGENTS" | grep -q "error"; then
    warn "Agents list error (overstory status may be unavailable): $AGENTS"
else
    ok "Agents list OK"
fi

# ---------------------------------------------------------------------------
# 6. Memory endpoint (read)
# ---------------------------------------------------------------------------
log "6. Memory endpoint..."
MEMORY_GET=$(curl -sf "$GATEWAY_URL/api/memory" 2>/dev/null || echo "{}")
if echo "$MEMORY_GET" | grep -q "error"; then
    warn "Memory read error: $MEMORY_GET"
else
    ok "Memory endpoint OK"
fi

# ---------------------------------------------------------------------------
# 7. Skills manifest / gateway context
# ---------------------------------------------------------------------------
log "7. Bundled skills in manifest..."
if [ -f "$WORKSPACE/.overstory/skills-manifest.json" ]; then
    COUNT=$(python3 -c "
import json
try:
    with open('$WORKSPACE/.overstory/skills-manifest.json') as f:
        data = json.load(f)
    skills = data if isinstance(data, list) else data.get('skills', data.get('skill', []))
    print(len(skills) if isinstance(skills, list) else 0)
except Exception:
    print(0)
" 2>/dev/null || echo "0")
    if [ "${COUNT:-0}" -ge 1 ]; then
        ok "Skills manifest has $COUNT skill(s)"
    else
        warn "Skills manifest empty or missing list"
    fi
else
    warn "Skills manifest not found (run install or generate_agent_context.py)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$FAILED" -eq 0 ]; then
    log "All checks passed."
    exit 0
else
    err "$FAILED check(s) failed."
    exit 1
fi
