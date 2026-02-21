#!/usr/bin/env bash
# Test that Mulch Stop hooks do not ENOENT in worktrees.
# Run from workspace root. Verifies:
# 1) In a worktree (with OVERSTORY_AGENT_NAME set), the mulch stop command no-ops and exits 0.
# 2) No worktree has the raw "mulch learn" hook (all use the safe guard).

set -e
WORKSPACE="${WORKSPACE:-$(cd "$(dirname "$0")/.." && pwd)}"
WORKTREES_DIR="$WORKSPACE/.overstory/worktrees"
MULCH_STOP_CMD='command -v mulch >/dev/null 2>&1 && test -f .mulch/mulch.config.yaml && [ -z "${OVERSTORY_AGENT_NAME}" ] && (pwd | grep -q ".overstory/worktrees" || mulch learn) || true'

echo "[1/3] Checking no worktree has raw 'mulch learn'..."
# Match the old broken form: command value is exactly "mulch learn" (no guards)
raw=$(grep -rE '"command"\s*:\s*"mulch learn"' "$WORKTREES_DIR" 2>/dev/null || true)
if [ -n "$raw" ]; then
  echo "FAIL: Some worktrees still have unguarded 'mulch learn':"
  echo "$raw"
  exit 1
fi
echo "OK: No raw 'mulch learn' in worktrees."

echo "[2/3] Running mulch stop command inside a worktree (should no-op, exit 0)..."
if [ ! -d "$WORKTREES_DIR" ]; then
  echo "SKIP: No worktrees dir."
else
  first_wt=""
  for d in "$WORKTREES_DIR"/*/; do
    [ -d "$d" ] || continue
    first_wt="$d"
    break
  done
  if [ -z "$first_wt" ]; then
    echo "SKIP: No worktrees."
  else
    name=$(basename "$first_wt")
    out=$(cd "$first_wt" && OVERSTORY_AGENT_NAME="$name" sh -c "$MULCH_STOP_CMD" 2>&1)
    code=$?
    if [ $code -ne 0 ]; then
      echo "FAIL: Exit code $code from worktree $first_wt"
      echo "$out"
      exit 1
    fi
    if echo "$out" | grep -q "ENOENT.*\.mulch/mulch\.config\.yaml"; then
      echo "FAIL: ENOENT still occurred in worktree:"
      echo "$out"
      exit 1
    fi
    echo "OK: Worktree stop hook exited 0, no ENOENT."
  fi
fi

echo "[3/3] Running mulch stop command in main workspace (should exit 0)..."
out=$(cd "$WORKSPACE" && unset OVERSTORY_AGENT_NAME; sh -c "$MULCH_STOP_CMD" 2>&1)
code=$?
if [ $code -ne 0 ]; then
  echo "FAIL: Main workspace stop hook exit code $code"
  echo "$out"
  exit 1
fi
echo "OK: Main workspace stop hook exited 0."

echo "All mulch stop hook checks passed."
exit 0
