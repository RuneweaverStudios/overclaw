#!/usr/bin/env bash
# Install the gateway watchdog as a LaunchAgent daemon.
# Expands OPENCLAW_HOME and script path, copies plist to ~/Library/LaunchAgents, loads it.
# Requires gateway-guard skill installed (watchdog calls gateway_guard.py for status/ensure).

set -e
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SCRIPT="$SCRIPT_DIR/gateway_watchdog.py"
PLIST_SRC="$SCRIPT_DIR/com.openclaw.gateway-watchdog.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist"

if [[ ! -f "$WATCHDOG_SCRIPT" ]]; then
  echo "Error: gateway_watchdog.py not found at $WATCHDOG_SCRIPT"
  exit 1
fi
if [[ ! -f "$PLIST_SRC" ]]; then
  echo "Error: plist not found at $PLIST_SRC"
  exit 1
fi

mkdir -p "$OPENCLAW_HOME/logs"
sed -e "s|OPENCLAW_HOME|$OPENCLAW_HOME|g" -e "s|OPENCLAW_WATCHDOG_SCRIPT|$WATCHDOG_SCRIPT|g" "$PLIST_SRC" > "$PLIST_DEST"
echo "Installed $PLIST_DEST"
launchctl load "$PLIST_DEST"
echo "Loaded. To stop: launchctl unload $PLIST_DEST"
echo "Logs: $OPENCLAW_HOME/logs/gateway-watchdog.log and .out.log / .err.log"
