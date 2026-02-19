---
name: gateway-watchdog
description: Check and recover OpenClaw gateway when it disconnects or auth (token/password) mismatches—common after installing the macOS companion app on top of a global (curl) install.
version: 1.0.0
---

# Gateway Watchdog

## Description

Check and recover OpenClaw gateway when it disconnects or auth (token/password) mismatches—common after installing the macOS companion app on top of a global (curl) install.

# Gateway Watchdog

Use this skill when the OpenClaw gateway keeps disconnecting, shows token/password mismatch, or you have multiple gateways (e.g. after installing the macOS companion app after a global `curl` or `npm` install).

**Requires:** The **gateway-guard** skill must be installed (`workspace/skills/gateway-guard/`). The watchdog calls `gateway_guard.py` for status and ensure.


## Usage

- Gateway disconnects or "device_token_mismatch" / auth errors
- You installed the macOS companion app after a global install and now have multiple gateways or config mismatch
- You want to check or fix gateway auth against `openclaw.json` and optionally run a recovery daemon

**Check gateway status (JSON):**
```bash
python3 scripts/gateway_guard.py status --json
```
*(Use gateway-guard skill path, e.g. `workspace/skills/gateway-guard/scripts/gateway_guard.py`.)*

**Fix auth mismatch and restart gateway:**
```bash
python3 scripts/gateway_guard.py ensure --apply --json
```
*(Same: use gateway-guard script path.)*

**Run watchdog in foreground (checks every 10s, recovers if not ok):**
```bash
python3 workspace/skills/gateway-watchdog/scripts/gateway_watchdog.py
```

**Install watchdog as a LaunchAgent daemon (optional):**
```bash
bash workspace/skills/gateway-watchdog/scripts/install_watchdog.sh
```

**Unload (stop) the watchdog:**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

**Reload (restart) the watchdog after a change:**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```


## Paths

- Resolve script paths against this skill directory (parent of `SKILL.md`).
- Example full path: `$OPENCLAW_HOME/workspace/skills/gateway-watchdog/scripts/gateway_watchdog.py`
- The watchdog script calls `$OPENCLAW_HOME/workspace/skills/gateway-guard/scripts/gateway_guard.py` for status and ensure.
