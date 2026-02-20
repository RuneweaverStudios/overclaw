# OverClaw UI

Web interface that matches **Overstory's dashboard layout** with **tabbed Chat and Agent Terminals** on the right.

## Layout

- **Left**: Overstory-style panels (Agents, Mail, Merge queue, Metrics), refreshed every 3s from `overstory status`.
- **Right**: Tabs
  - **Chat (Ollama Orchestrator)**: Full chat input and history; sends to gateway `/api/chat`.
  - **Agent Terminals**: Instructions and commands to attach to each agent's tmux session (e.g. `tmux attach -t overstory-overclaw-lead-cfe688`).

## Requirements

- Overstory CLI on PATH
- OverClaw gateway running (default `http://localhost:18800`)
- Python 3.9+

## Run

```bash
cd /Users/ghost/.openclaw/workspace/scripts/overclaw_ui
pip install -r requirements.txt
python3 app.py
```

Then open **http://localhost:5050**.

## Env

- `OVERCLAW_WORKSPACE` — project root (default: parent of `scripts`)
- `OVERSTORY_BIN` — overstory CLI (default: `overstory`)
- `OVERCLAW_GATEWAY_URL` — gateway URL (default: `http://localhost:18800`)
- `PORT` — UI port (default: 5050)

## Overstory reference

- Overstory uses **tmux** for each agent; the dashboard is a TUI (`overstory dashboard`) with Agents, Mail, Merge queue, Metrics.
- This UI replicates that panel layout on the left and adds the orchestrator chat + terminal attach info on the right.
