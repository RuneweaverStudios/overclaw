# gateway-watchdog

**OpenClaw skill: periodic gateway check and auto-recovery.**

Runs every 10 seconds. If the gateway is not running or auth does not match config, runs the **gateway-guard** skill's `ensure --apply` and starts `openclaw gateway`. Install as a LaunchAgent for hands-off recovery.

**Requires:** [gateway-guard](https://clawhub.ai/RuneweaverStudios/gateway-guard) skill installed (same workspace). The watchdog invokes `gateway_guard.py` for status and ensure.

## Install

Copy this folder into your OpenClaw workspace: `workspace/skills/gateway-watchdog/`.

Ensure **gateway-guard** is at `workspace/skills/gateway-guard/`.

## Quick start

```bash
# Run once in foreground (checks every 10s)
python3 workspace/skills/gateway-watchdog/scripts/gateway_watchdog.py

# Install as LaunchAgent (starts at login, keeps running)
bash workspace/skills/gateway-watchdog/scripts/install_watchdog.sh
```

## Unload / Reload

**Stop the watchdog daemon:**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

**Start (or restart) the watchdog daemon:**
```bash
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

**Reload after updating the skill or plist:**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

Logs: `OPENCLAW_HOME/logs/gateway-watchdog.log` (and `.out.log` / `.err.log` from LaunchAgent).
