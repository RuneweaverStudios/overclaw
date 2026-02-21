# .overstory/ — OverClaw Agent Swarm

This directory is managed by [overstory](https://github.com/jayminwest/overstory) as part of the **OverClaw** stack.

OverClaw uses overstory to turn a single Claude Code session into a multi-agent team by spawning worker agents in git worktrees via tmux, coordinating them through a custom SQLite mail system, and merging their work back with tiered conflict resolution.

## OverClaw Stack

| Component | Port | Role |
|---|---|---|
| **OverClaw Gateway** | 18800 | HTTP API entry point |
| **Ollama (Mistral)** | 11434 | Orchestrator LLM |
| **nanobot agent** | — | Background agent (heartbeat, cron, channels) |
| **overstory** | — | Subagent coordination & swarm |

> **Note:** OverClaw gateway runs on port 18800, separate from legacy OpenClaw (18789) and nanobot defaults (18790).

## Mulch (expertise system)

**Mulch** is overstory’s expertise/knowledge layer. Agents use it to load and record project context:

- **`mulch prime`** — Load domain or file-scoped expertise at session start.
- **`mulch record`** — Record insights (conventions, patterns, failures, decisions) for future sessions.
- **`mulch search`** / **`mulch query`** — Search stored expertise.
- **`mulch learn`** — Run at session stop to learn from the session (used in Stop hooks).

**Implementation (Overstory GitHub):** Overstory runs the mulch CLI from the **project root** only (where `.mulch/` lives). See [overstory config](https://github.com/jayminwest/overstory/blob/main/.overstory/config.yaml) (`mulch: { enabled, domains, primeFormat }`), [mulch client](https://github.com/jayminwest/overstory/blob/main/src/mulch/client.ts) (prime, record, query, search, learn, etc.), and [prime command](https://github.com/jayminwest/overstory/blob/main/src/commands/prime.ts) (`createMulchClient(config.project.root)`). The upstream default hooks do not include a Stop hook for `mulch learn`; OverClaw adds it so the main-workspace session can be learned from, gated so it runs only when (1) `mulch` is on PATH, (2) `.mulch/mulch.config.yaml` exists in the current directory, (3) not inside `.overstory/worktrees`, and (4) `OVERSTORY_AGENT_NAME` is unset (orchestrator only). That avoids ENOENT in worktrees, which have no `.mulch` directory.

Config is in `config.yaml` under `mulch: { enabled: true, domains: [], primeFormat: markdown }`. Agent defs in `agent-defs/` describe when to use `mulch prime`, `mulch record`, etc.

**If `mulch` is not installed:** The Stop hook runs `mulch learn` only when the `mulch` command is available. If `mulch` is missing, the hook no-ops so you don’t get “mulch: command not found”. OverClaw's ./scripts/install.sh installs mulch (step 7a); run it if mulch is missing.

## Key Commands

- `overstory init`          — Initialize this directory
- `overstory status`        — Show active agents and state
- `overstory sling <id>`    — Spawn a worker agent
- `overstory mail check`    — Check agent messages
- `overstory merge`         — Merge agent work back
- `overstory dashboard`     — Live TUI monitoring
- `overstory doctor`        — Run health checks

## Merge queue & pipeline integration

The **merge queue** (shown in the OverClaw UI and dashboard) holds branches that leads have signaled as **merge_ready** after builder work passes reviewer. The full pipeline is:

**Coordinator → Lead → Scout / Builder → Reviewer → merge_ready → Merge**

The OverClaw gateway **integrates this pipeline** so it runs without a live coordinator process:

1. **Route + spawn** (e.g. UI “Route to agents”, or `POST /api/route` with `spawn: true`) creates a bead/spec and slings a **lead** plus a **builder** (or other worker). The lead runs scout → builder → reviewer and sends **merge_ready** to the coordinator when work is approved.
2. **Merge drain loop** — The gateway runs a background task that periodically (default every 45s; set `OVERCLAW_MERGE_DRAIN_INTERVAL` to change) checks `overstory status` for the merge queue count. When the queue has items, it runs **`overstory merge --all`** so those branches are merged in FIFO order. The dashboard “Merge Queue” count goes back to 0 as merges complete.
3. **Manual drain** — You can trigger a single drain with **`POST /api/overstory/merge`**. Response: `{ "drained": N, "result": ... }` or `{ "drained": 0 }` if the queue was empty.

So: when you use route+spawn for code/builder tasks, the lead and builder (and reviewer) run in Overstory worktrees; when the lead sends merge_ready, the gateway’s merge drain will run `overstory merge --all` and the merge queue will be utilized. No separate coordinator session is required for merging.

## Structure

- `config.yaml`             — Project configuration
- `agent-manifest.json`     — Agent registry
- `hooks.json`              — Claude Code hooks config
- `agent-defs/`             — Agent definition files (.md)
- `gateway-context.md`      — Auto-generated context for agents (tools & skills available)
- `skills-manifest.json`    — Auto-generated skills manifest for programmatic discovery
- `specs/`                  — Task specifications
- `agents/`                 — Per-agent state and identity
- `worktrees/`              — Git worktrees (gitignored)
- `logs/`                   — Runtime logs (gitignored)
