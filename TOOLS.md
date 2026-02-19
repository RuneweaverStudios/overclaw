# TOOLS.md - Local Notes

**ClawHub:** When the new instance is live, update `clawhub install` and `clawhub.ai/...` links here and across the workspace.

Skills define _how_ tools work. This file is for _your_ specifics.

## Chat UI (TUI) – "Untrusted metadata" and internal text

**"Conversation info (untrusted metadata)" block:** A patch was applied to the OpenClaw package so this block is no longer prepended to messages. See **TOOLS-PATCHES.md** in this workspace for patch details. If you reinstall or update OpenClaw npm, patches are lost — re-apply using that file. The agent is still instructed (AGENTS.md) not to echo any remaining internal text.

**Internal wrap-up:** Orchestrator strips it (delegate rule §10). **Subagent delivery:** See TOOLS-PATCHES.md; re-apply after OpenClaw npm update.

## Power users: API bills & the agent swarm

**Pain point:** API bills add up; one premium model for everything is expensive. **Benefits:** **Agent swarm** routes to the best model (creative→Kimi, code→MiniMax) and price. **See progress:** Say **"agent status"** — use **subagent-tracker** (TUI has no live indicator).

**Installation:**
- **Agent Swarm:** `clawhub install agent-swarm` or clone `https://github.com/RuneweaverStudios/agent-swarm` into `workspace/skills/agent-swarm`.
- **Subagent Tracker:** `clawhub install subagent-tracker` or clone into `workspace/skills/subagent-tracker`. Required for “agent status” and duplicate-check before spawn.

**Use examples:**
- Say **“agent status”** or **“what’s the sub-agent doing?”** → orchestrator runs the tracker and reports active subagents (Agent 1, Agent 2, … with Task X/Y and model).
- Queue several tasks (poem, bug fix, research) → each goes to the right model; say **“agent status”** anytime to see progress.
- **“List active subagents”** / **“show subagent progress”** → same tracker output.

**Commands (absolute paths):**
```bash
# Router: spawn params for delegation
python3 /Users/ghost/.openclaw/workspace/skills/agent-swarm/scripts/router.py spawn --json "<task>"

# Tracker: list active subagents (use --summary for one clean block)
python3 /Users/ghost/.openclaw/workspace/skills/subagent-tracker/scripts/subagent_tracker.py list --active 30 --summary

# Tracker: status for one session
python3 /Users/ghost/.openclaw/workspace/skills/subagent-tracker/scripts/subagent_tracker.py status <session-id>

# Tracker: tail transcript
python3 /Users/ghost/.openclaw/workspace/skills/subagent-tracker/scripts/subagent_tracker.py tail <session-id> --lines 15
```

## Exec host (node vs sandbox)

If exec fails for router/gateway_guard, set **`tools.exec.host`** to **`"sandbox"`** in `openclaw.json` and restart the gateway. Workspace uses **sandbox**.

## Agent Swarm

Your intelligent model router. Installed at: `workspace/skills/agent-swarm/` (or `~/.openclaw/workspace/skills/agent-swarm/` from home).

**Requirements:** **OpenRouter** — ALL models (default, orchestrator, sub-agents) use OpenRouter. Every model ID **must** start with `openrouter/` (e.g. `openrouter/moonshotai/kimi-k2.5`). Never pass bare IDs like `minimax/kimi-k2.5` — they fail with "No API key for provider". Set OpenRouter API key in OpenClaw auth.

**Default:** New sessions and the orchestrator use **Gemini 2.5 Flash** (openrouter/google/gemini-2.5-flash) via OpenRouter — fast, cheap, reliable at tool-calling / `sessions_spawn`. TUI reply header: e.g. "Gemini 2.5 Flash (default)".

**Task execution:** The main agent must delegate via Agent Swarm + **sessions_spawn**; see agent-swarm SKILL.md. Run `spawn --json` to get params, then call **sessions_spawn** with that `task` and **exact `model`** (e.g. `openrouter/moonshotai/kimi-k2.5` for creative)—never substitute another model (e.g. Claude Sonnet 4). Forward the sub-agent's reply; if you label, use the router's model (e.g. "Using: Kimi k2.5"), not Sonnet 4.

**Stabilize gateway.auth (only overwrite when incorrect):** Set a fixed token (or password) in `openclaw.json` → `gateway.auth` so the TUI/browser service doesn’t keep rewriting it and triggering restarts. Run once: `python3 /Users/ghost/.openclaw/workspace/skills/gateway-guard/scripts/gateway_guard.py ensure --apply --json`. The **gateway-guard** skill only writes `gateway.auth` when it’s incorrect (missing or wrong). If you leave `gateway.auth.token` empty, `ensure --apply` will generate a token and write it once. After that, keep that token in config and use the same token in the TUI.

**Gateway auth:** Use the **gateway-guard** skill (`workspace/skills/gateway-guard/`). If you see "Gateway auth issue" or `device_token_mismatch`, run (absolute path for any cwd):
`python3 /Users/ghost/.openclaw/workspace/skills/gateway-guard/scripts/gateway_guard.py status --json`
If mismatch/non-running, auto-fix with:
`python3 /Users/ghost/.openclaw/workspace/skills/gateway-guard/scripts/gateway_guard.py ensure --apply --json`
For automatic recovery every 10s, install the **gateway-watchdog** skill (`workspace/skills/gateway-watchdog/`); it uses the gateway-guard script and runs as a LaunchAgent via `bash workspace/skills/gateway-watchdog/scripts/install_watchdog.sh`.

**Disconnect after submit:** Gateway restarts (SIGUSR1) when `openclaw.json` changes (e.g. gateway.auth). Mitigation: stabilize gateway.auth (gateway-guard ensure --apply).

**Gateway won't reconnect:** Run gateway_guard `status --json` then `ensure --apply --json`; use same auth in TUI as in `openclaw.json`. If needed, kill port 18789 and run `openclaw gateway`.

**Sub-agent results not showing / no progress in chat:** The orchestrator is required to **wait** for `sessions_spawn` to return before replying (see delegate rule "Wait for spawn before replying"). If you see "Using: Kimi k2.5" (or similar) but no follow-up with the sub-agent's output, the turn may have ended before the spawn completed. To **track** what sub-agents are doing: use the **subagent-tracker** skill — e.g. "list active subagents" or "show me what the sub-agent is doing". Commands: `python3 /Users/ghost/.openclaw/workspace/skills/subagent-tracker/scripts/subagent_tracker.py list --active 30` and `status <session-id>` / `tail <session-id>`. Run↔task mapping is in `subagents/runs.json`.

**Gateway force-killed / restarted / reopened TUI:** Cron runs every 2 min, so a summary may appear shortly after reconnect. For an **immediate** summary, say **"what just happened?"** or "I restarted the gateway"—the agent will run the what-just-happened skill and reply with what the logs show and a suggested fix (e.g. gateway-guard).

**Quick commands:** (absolute path so exec works from any cwd)
```bash
# Show session default model
python3 /Users/ghost/.openclaw/workspace/skills/agent-swarm/scripts/router.py default

# Classify a task (most common)
python3 /Users/ghost/.openclaw/workspace/skills/agent-swarm/scripts/router.py classify "build React app"

# Get spawn params (JSON for sessions_spawn)
python3 /Users/ghost/.openclaw/workspace/skills/agent-swarm/scripts/router.py spawn --json "fix bug"

# List models
python3 /Users/ghost/.openclaw/workspace/skills/agent-swarm/scripts/router.py models
```

**Tier shortcuts:**
- **Default / orchestrator** → Gemini 2.5 Flash (session default; fast, cheap, reliable tool-calling)
- FAST → Gemini 2.5 Flash (cheap & fast; only when task is simple)
- CODE → MiniMax 2.5 (coding)
- REASONING → GLM-5 (logic/math)
- CREATIVE → Kimi k2.5 (writing/design)
- RESEARCH → Grok Fast (web search)
- VISION → GPT-4o (images)

## Troubleshooting

**Gateway auth:** Run `gateway_guard ensure --apply` when the gateway is running so the token is written once; TUI and gateway then use the same auth and config-change restarts are reduced. **TOOLS.md size:** Bootstrap sends only the first 9020 characters. This file is kept under that limit; long patch details live in **TOOLS-PATCHES.md**.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
