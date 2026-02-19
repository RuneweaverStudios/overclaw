# OverClaw

Hybrid AI agent orchestration. Local Ollama Mistral as the orchestrator, Claude Code agent swarm via [overstory](https://github.com/jayminwest/overstory) for the heavy lifting.

Built on [nanobot](https://github.com/HKUDS/nanobot) (persistent AI backend) + overstory (multi-agent Claude Code swarm).

## Architecture

```
User Request
     │
     ▼
┌──────────────────────────────┐
│  OverClaw Gateway (:18800)   │  ← HTTP API entry point
│  /api/chat, /api/route,      │
│  /api/agents, /api/skills    │
└──────────┬───────────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
┌──────────┐  ┌─────────────────────────────┐
│  Ollama  │  │        overstory             │
│  Mistral │  │  Claude Code agent swarm     │
│ (:11434) │  │                              │
│          │  │  coordinator → supervisor    │
│ orchestr-│  │      → worker agents         │
│ ation &  │  │  (git worktrees + tmux)      │
│ routing  │  │                              │
└──────────┘  └─────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/RuneweaverStudios/overclaw.git
cd overclaw

# 2. Install dependencies
brew install ollama tmux
curl -fsSL https://bun.sh/install | bash
pip install nanobot-ai  # or: python3 -m venv ~/.nanobot-venv && pip install nanobot-ai

# Install overstory
bun install -g overstory

# 3. Start everything
./scripts/start-overclaw.sh

# 4. Check status
./scripts/start-overclaw.sh status
```

## Port Map

| Service | Port | Description |
|---|---|---|
| **OverClaw Gateway** | **18800** | Main HTTP API — use this |
| Ollama | 11434 | Local LLM serving Mistral |
| Legacy OpenClaw | 18789 | Previous OpenClaw gateway (not used by OverClaw) |
| Legacy nanobot | 18790 | Previous nanobot default (not used by OverClaw) |

> **Existing nanobot / OpenClaw users:** OverClaw deliberately uses port **18800** to avoid conflicts with your existing setup. Both can run side by side.

## API Endpoints

### Health & Status
```bash
curl http://localhost:18800/health
curl http://localhost:18800/api/status
```

### Chat with Orchestrator
```bash
curl -X POST http://localhost:18800/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "research trending AI frameworks"}'
```

### Route Tasks to Agents
```bash
# Classify only (dry run)
curl -X POST http://localhost:18800/api/route \
  -H "Content-Type: application/json" \
  -d '{"task": "write a blog post about local LLMs"}'

# Classify and spawn agent
curl -X POST http://localhost:18800/api/route \
  -H "Content-Type: application/json" \
  -d '{"task": "write a blog post about local LLMs", "spawn": true}'
```

### Agent Management
```bash
# List agents
curl http://localhost:18800/api/agents

# Spawn directly
curl -X POST http://localhost:18800/api/agents/spawn \
  -H "Content-Type: application/json" \
  -d '{"task": "build a REST API", "capability": "builder"}'

# Inspect agent
curl http://localhost:18800/api/agents/builder-abc123
```

### Skills & Tools
```bash
# Discover
curl http://localhost:18800/api/skills
curl http://localhost:18800/api/tools

# Execute
curl -X POST http://localhost:18800/api/skills/exec \
  -H "Content-Type: application/json" \
  -d '{"skill": "scribe", "args": "--mode daily --json"}'
```

### Memory
```bash
# Read
curl http://localhost:18800/api/memory
curl "http://localhost:18800/api/memory?section=Events"

# Write
curl -X POST http://localhost:18800/api/memory \
  -H "Content-Type: application/json" \
  -d '{"section": "Events", "content": "Deployed OverClaw v1"}'
```

## Agent Capabilities

| Agent | Capability | Skills |
|---|---|---|
| **Researcher** | `researcher` | last30days, web search, Playwright scraping |
| **Social Media Manager** | `social-media-manager` | Playwright MCP, OAuth, social automation |
| **Blogger** | `blogger` | Humanizer, researcher integration, SEO |
| **Scribe** | `scribe` | Log analysis, memory curation, daily notes |
| **Builder** | `builder` | Code, build, deploy, fix |
| **Scout** | `scout` | Explore, find, search |
| **Reviewer** | `reviewer` | Code review, merge, audit |

## Project Structure

```
overclaw/
├── scripts/
│   ├── overclaw_gateway.py    # HTTP API gateway (port 18800)
│   └── start-overclaw.sh      # Stack launcher (start/stop/status)
├── agents/                     # Agent definitions (enriched with gateway tools)
├── skills/
│   ├── nanobot-overstory-bridge/  # Core bridge (router, client, gateway-tools CLI)
│   ├── playwright-mcp/            # Playwright browser automation as MCP
│   ├── creative-agents/           # Researcher, blogger, social, scribe integrations
│   ├── skills-compat/             # OpenClaw skill format compatibility
│   └── ...                        # 30+ other skills
├── .overstory/
│   ├── config.yaml             # overstory configuration
│   ├── agent-defs/             # overstory agent definitions (12 agents)
│   ├── gateway-context.md      # Auto-generated: tools & skills for agents
│   └── skills-manifest.json    # Auto-generated: skills for discovery
└── memory/                     # Daily notes and long-term memory
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OVERCLAW_PORT` | `18800` | Gateway port |
| `OVERCLAW_WORKSPACE` | (auto) | Workspace root |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `mistral:latest` | Orchestrator model |
| `OVERSTORY_BIN` | `~/.bun/bin/overstory` | Path to overstory |
| `NANOBOT_GATEWAY_URL` | `http://localhost:18800` | Used by gateway-tools CLI |

## Management

```bash
# Start
./scripts/start-overclaw.sh

# Status
./scripts/start-overclaw.sh status

# Stop
./scripts/start-overclaw.sh stop

# Restart
./scripts/start-overclaw.sh restart
```

## License

MIT
