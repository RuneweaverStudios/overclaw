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

## Key Commands

- `overstory init`          — Initialize this directory
- `overstory status`        — Show active agents and state
- `overstory sling <id>`    — Spawn a worker agent
- `overstory mail check`    — Check agent messages
- `overstory merge`         — Merge agent work back
- `overstory dashboard`     — Live TUI monitoring
- `overstory doctor`        — Run health checks

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
