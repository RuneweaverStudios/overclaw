# OpenClaw LaunchAgents — Unload / Reload

Use these when stopping, starting, or restarting daemons after skill updates.

---

## Gateway Guard (watcher)

Single daemon: token sync + what-just-happened + continue-on-error.  
Plist: `com.openclaw.gateway-guard.watcher`

**Unload (stop):**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-guard.watcher.plist
```

**Load (start):**
```bash
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-guard.watcher.plist
```

**Reload (restart after change):**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-guard.watcher.plist
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-guard.watcher.plist
```

---

## Gateway Guard (continue-on-error only)

Optional: only the continue-on-error loop.  
Plist: `com.openclaw.gateway-guard.continue-on-error`

**Unload:** `launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-guard.continue-on-error.plist`  
**Load:** `launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-guard.continue-on-error.plist`

---

## Gateway Watchdog

Periodic gateway check + recovery (every 10s). Requires gateway-guard skill.  
Plist: `com.openclaw.gateway-watchdog`

**Unload (stop):**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

**Load (start):**
```bash
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

**Reload (restart after change):**
```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
launchctl load ~/Library/LaunchAgents/com.openclaw.gateway-watchdog.plist
```

---

## What-just-happened (gateway-back watcher)

Runs when gateway comes back; triggers what-just-happened summary.  
Plist: `com.openclaw.what-just-happened`  
(Usually superseded by gateway-guard watcher, which calls the what-just-happened script.)

**Unload:** `launchctl unload ~/Library/LaunchAgents/com.openclaw.what-just-happened.plist`  
**Load:** `launchctl load ~/Library/LaunchAgents/com.openclaw.what-just-happened.plist`

---

## Verify

```bash
launchctl list | grep openclaw
```
