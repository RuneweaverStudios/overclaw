#!/usr/bin/env bash
# Test Overstory's exact spawn + dashboard (no OverClaw gateway).
# Uses: overstory sling (same as Overstory), overstory status/dashboard.
# Agents use .overstory/bin/claude wrapper; .overstory/dangerously-skip-permissions = 0
# so the wrapper strips --dangerously-skip-permissions and agents don't see the disclaimer.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERSTORY_BIN="${OVERSTORY_BIN:-$HOME/.bun/bin/overstory}"
SETTING_FILE="$WORKSPACE/.overstory/dangerously-skip-permissions"
# So overstory-spawned agents use the wrapper (strip flag when setting = 0)
export PATH="$WORKSPACE/.overstory/bin:${PATH:-}"

echo "[1/5] Ensure no bypass disclaimer (setting file = 0)..."
mkdir -p "$WORKSPACE/.overstory"
echo "0" > "$SETTING_FILE"
echo "   Written 0 to $SETTING_FILE (wrapper will strip flag for new agents)"

echo "[2/5] Clean existing sessions and worktrees..."
cd "$WORKSPACE"
"$OVERSTORY_BIN" clean --worktrees --sessions 2>/dev/null || true
sleep 1

echo "[3/5] Spawn one agent (Overstory exact: overstory sling)..."
TASK_ID="test-exact-$(date +%s)"
SPEC_DIR="$WORKSPACE/.overstory/specs"
mkdir -p "$SPEC_DIR"
echo -e "# Task: Test exact Overstory spawn\n\nEcho hello and exit." > "$SPEC_DIR/$TASK_ID.md"
# If beads enabled, create bead first (same as gateway)
SLING_ID="$TASK_ID"
if command -v bd >/dev/null 2>&1; then
  BD_OUT="$(bd create "Test exact Overstory spawn" --priority 1 --type task --json 2>/dev/null)" || true
  if [ -n "$BD_OUT" ]; then
    BEAD_ID="$(echo "$BD_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)"
    if [ -n "$BEAD_ID" ]; then SLING_ID="$BEAD_ID"; fi
  fi
fi
SPEC_PATH=".overstory/specs/$TASK_ID.md"
# Coordinator can only spawn lead; use --force-hierarchy for a single lead test
"$OVERSTORY_BIN" sling "$SLING_ID" --capability lead --name "lead-$TASK_ID" --spec "$SPEC_PATH" --force-hierarchy --json || {
  echo "   Sling failed. If beads required, ensure 'bd create' ran or disable beads in .overstory/config.yaml"
  exit 1
}
echo "   Spawned lead-$TASK_ID (task_id=$SLING_ID)"

echo "[4/5] Wait 15s for agent to pass wrapper (no disclaimer when flag stripped)..."
sleep 15

echo "[5/5] Overstory status (exact UI data source)..."
"$OVERSTORY_BIN" status --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
agents = d.get('agents') or []
print('   Agents:', len(agents))
for a in agents:
    print('   -', a.get('agentName'), a.get('state'), a.get('capability'))
if agents:
    states = [a.get('state') for a in agents]
    if any(s == 'working' for s in states):
        print('   SUCCESS: At least one agent in working state (exact Overstory spawn + wrapper works)')
        sys.exit(0)
    if all(s == 'booting' for s in states):
        print('   WARN: All still booting (disclaimer may still be showing if wrapper not used)')
    sys.exit(0)
sys.exit(1)
" || exit 1

echo ""
echo "Done. Run 'overstory dashboard' in another terminal to use the exact Overstory TUI (no gateway)."
echo "Then retool OverClaw: gateway spawn uses same sling; UI can poll agent terminals less often."
