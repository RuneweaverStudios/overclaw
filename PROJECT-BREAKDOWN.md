# Everything-App / Ghost — Project Breakdown & Dev Notes

Complete planning, features, and build guide for the personal "everything" app (Ghost + OpenClaw orchestrator, agent loops, Ollama, role agents). **Generous dev notes** for anyone continuing or porting the project.

---

## 1. Project overview

**Vision:** A single entrypoint (one text input + send) that connects to an orchestrator. The orchestrator routes work to local AI (Ollama), role-based agents (CEO, PM, Dev, PR, Editor, Social, Customer service), and prebuilt agent loops (e.g. ship feature, launch campaign). Optional: proxies/VPN/VPS for identity rotation and near-infinite free-tier tool use; Playwright for free web chats and browser automation. Can be hosted on a Mac Mini and called from Telegram.

**What was built in this workspace:**

- **Ghost** — Minimal web UI: one input + send, shows “understood” task/model and live “what it’s doing” (session status + transcript). Lives at `workspace/ghost/`.
- **Entrypoints & gateway docs** — `ENTRYPOINTS.md` (TUI, Clawpanion, Ghost; JAT Gateway; Ollama + OpenClaw).
- **Ollama + OpenClaw + Claude Code** — `OLLAMA.md` documents official `ollama launch openclaw` and `ollama launch claude`, recommended models, manual env setup.
- **Agent loops** — `workspace/skills/agent-loops/`: workflow definitions (e.g. `ship_feature.json`), runner `run_workflow.py` that uses the agent-swarm router per step (dry-run or `--apply` with gateway).
- **Dashboard spawn response** — Subagent-dashboard `/api/sessions/spawn` now returns `task` and `model` so Ghost can display “understood” and route clearly.

**What exists in OpenClaw already (pre–this project):** Gateway, agent-swarm router (OpenRouter models), sessions_spawn, playwright-commander, skills with scripts, Telegram delivery, subagent-dashboard (Flask API on 8080), Clawpanion (JAT-inspired IDE).

---

## 2. Planning (from feasibility plan)

### 2.1 Core idea

- **One input + send** → gateway → router → spawn → skills/Playwright.
- **Local-first:** Most work on Ollama; remote free tools via identity rotation (proxies/VPN/VPS) for privacy and multiplied free-tier use.
- **Gateway options:** OpenClaw gateway, **JAT Gateway** (evaluated for stability), **Ollama + OpenClaw** (`ollama launch openclaw`).

### 2.2 Playwright + free web chats

- Playwright can drive free chat UIs (e.g. Claude.ai, ChatGPT, Grok), type prompt, submit, scrape reply, feed back to host. No API key for that path; rate limits and ToS apply.

### 2.3 Orchestrator + identity pool

- Orchestrator keeps most action on Ollama; when it needs a remote free tool, it picks an identity from a pool (proxy/VPN/VPS), runs Playwright/HTTP through it, rotates so each “user” stays under limits. Result: near-infinite free tool use when orchestrated, plus privacy.

### 2.4 Role-based agents

- Agents: Social media manager, CEO, Project manager, Software developer, PR representative, Editor, Customer service. Each has custom prompts and tools/scripts. Orchestrator assigns; agents can delegate sub-tasks (depth capped to avoid bloat). Use for multi-step/project work; simple Q&A goes to generalist/Ollama.

### 2.5 Prebuilt agent loops / workflows

- Workflows are fixed multi-agent sequences (e.g. PM → Dev → Editor). User or intent selects a workflow; workflow runner runs steps in order, passes context, uses router (and optionally spawn) per step. Stored in `workflows/` (JSON or YAML).

### 2.6 What actually constrains you

- **Free:** Only local AI + your infra is free; cloud APIs cost or are rate-limited.
- **ToS/legal:** Automation and proxy/VPN abuse can violate ToS or law; design is technical, responsibility is yours.
- **Technical:** No fundamental blockers; gaps are product/design (UI, Telegram-in, local-LLM tier, proxy layer).

---

## 3. Features (implemented vs planned)

| Feature | Status | Location / notes |
|--------|--------|-------------------|
| One input + send UI | Done | `workspace/ghost/index.html` |
| Show “understood” task + model | Done | Ghost; dashboard spawn returns `task`, `model` |
| Live “what it’s doing” (status + transcript) | Done | Ghost polls `/api/sessions/<id>` |
| Entrypoints doc (TUI, Ghost, Clawpanion, gateway) | Done | `workspace/ENTRYPOINTS.md` |
| Ollama + OpenClaw official integration doc | Done | `workspace/OLLAMA.md` |
| Claude Code + Ollama doc | Done | `workspace/OLLAMA.md` |
| Agent loop (workflow) definitions | Done | `workspace/skills/agent-loops/workflows/` |
| Workflow runner (router per step) | Done | `workspace/skills/agent-loops/scripts/run_workflow.py` |
| JAT Gateway noted as option | Done | ENTRYPOINTS.md, Ghost README |
| LOCAL (Ollama) tier in router | Not done | Plan: add LOCAL tier, route default to Ollama |
| Identity pool + identity manager | Not done | Plan: config + picker, wire Playwright/HTTP |
| Role agents (CEO, PM, Dev, etc.) | Not done | Plan: skills/profiles per role, role router |
| Telegram as input (bot → gateway) | Not done | Plan: bot forwards messages into gateway |
| Free web chat Playwright scripts | Not done | Plan: per-provider scripts in playwright-commander |
| Host on Mac Mini runbook | Not done | Plan: doc runbook for gateway + Ghost + Ollama |

---

## 4. Architecture (high level)

```
Entrypoints (TUI, Ghost, Telegram) → Gateway (OpenClaw or JAT or ollama launch openclaw)
  → Router (agent-swarm) → Spawn (sessions_spawn)
  → Ollama (local) / Role agents / Workflows
  → Playwright, Skills, Free web chat (optional identity pool)
```

- **Ghost** talks to subagent-dashboard at `http://localhost:8080` by default (or configurable API base). Dashboard has `/api/sessions/spawn`, `/api/sessions/<id>`, `/api/subagent/<id>/status`, `/api/subagent/<id>/transcript`.
- **Agent loops** run via `run_workflow.py`: load JSON/YAML → for each step resolve task from template → `router.py spawn --json <task>` → optional spawn (with `--apply` when gateway is available).
- **Router** lives at `workspace/skills/agent-swarm/scripts/router.py`; OpenRouter required for cloud models; LOCAL tier for Ollama is planned, not yet in config.

---

## 5. Key files and folders

| Path | Purpose |
|------|--------|
| `workspace/ghost/` | Ghost app: `index.html`, `README.md`, `.gitignore` |
| `workspace/ghost/index.html` | Single-page UI: input, send, API base, “Understood” block, “What it’s doing” (polling session + transcript) |
| `workspace/ENTRYPOINTS.md` | Entrypoints (TUI, Clawpanion, Ghost) and gateway (JAT, Ollama+OpenClaw) |
| `workspace/OLLAMA.md` | Ollama install, OpenClaw + Ollama, Claude Code + Ollama, tool calling, recommended models |
| `workspace/skills/agent-loops/` | Workflows + runner: `workflows/ship_feature.json`, `scripts/run_workflow.py`, `README.md` |
| `workspace/skills/agent-swarm/scripts/router.py` | Task → model routing; `spawn --json` for sessions_spawn params |
| `workspace/skills/subagent-dashboard/scripts/dashboard.py` | Flask API (8080): sessions, spawn, status, transcript; spawn response includes `task`, `model` |
| `workspace/skills/playwright-commander/` | Playwright skill (navigate, screenshot, get content); extend for free web chat scripts |
| `workspace/skills/gateway-guard/` | Gateway health; orchestrator flow uses it before spawn |

---

## 6. How to build / run

### 6.1 Prerequisites

- Python 3, Node (for Clawpanion if used).
- OpenClaw workspace at `~/.openclaw` (or `OPENCLAW_HOME`).
- OpenRouter API key for agent-swarm cloud models.
- Optional: Ollama installed; then `ollama launch openclaw` or `ollama launch claude` per OLLAMA.md.

### 6.2 Run Ghost

```bash
# 1. Serve Ghost (must be over HTTP for fetch to API)
cd /Users/ghost/.openclaw/workspace/ghost
python3 -m http.server 3000
# Or: npx serve -l 3000

# 2. Open http://localhost:3000

# 3. Backend: subagent-dashboard on 8080 (or gateway). If dashboard not running, start it:
# python3 workspace/skills/subagent-dashboard/scripts/dashboard.py  # (check dashboard README for exact command)
```

### 6.3 Run an agent loop

```bash
cd /Users/ghost/.openclaw
python3 workspace/skills/agent-loops/scripts/run_workflow.py ship_feature "Add dark mode to settings"
# With actual spawn (gateway must be running):
python3 workspace/skills/agent-loops/scripts/run_workflow.py ship_feature "Add dark mode" --apply
```

### 6.4 Ollama + OpenClaw

```bash
ollama launch openclaw   # configures OpenClaw to use Ollama and starts gateway
```

### 6.5 Claude Code + Ollama

```bash
ollama launch claude     # or set ANTHROPIC_* env and claude --model qwen3-coder
```

---

## 7. Dev notes

### 7.1 Paths and env

- **OPENCLAW_HOME** — Default `~/.openclaw`. Used by router, dashboard, agent-loops, gateway-guard. Set if your repo is elsewhere.
- **Workspace root** — Scripts assume workspace at `$OPENCLAW_HOME/workspace` (e.g. `/Users/ghost/.openclaw/workspace`). Ghost and agent-loops use paths relative to workspace or OPENCLAW_HOME.
- **Absolute paths in rules** — The orchestrator rule uses absolute paths (e.g. `/Users/ghost/.openclaw/workspace/skills/agent-swarm/scripts/router.py`) so exec works from any cwd (TUI/gateway).

### 7.2 APIs Ghost uses

- **POST /api/sessions/spawn** — Body: `{ "task": "...", "model": "openrouter/..." }`. Returns `sessionId`, `sessionKey`, `task`, `model` (dashboard now returns task/model from router).
- **GET /api/sessions/<sessionId>** — Session detail + `transcript` array. Ghost polls this for “What it’s doing”. Transcript events are objects (type, content, etc.); Ghost formats them as `[type] content`.

### 7.3 Dashboard spawn behavior

- Dashboard’s spawn endpoint runs the agent-swarm router with the given `task` and overwrites `model` (and optional `sessionTarget`) from router output. So you can send a default model; router’s choice is what’s returned. Actual spawning to gateway is still placeholder (“Gateway integration pending”) until gateway-attached API exists.

### 7.4 Agent-loop workflow format

- JSON (or YAML with PyYAML): `id`, `name`, `description`, `steps[]`. Each step: `id`, `agent`, `task_template` with `{{ user_input }}` or `{{ step_id_output }}`, `input_from`. Runner substitutes context into `task_template` and calls router for each step. For real multi-step execution with handoff of outputs, you’d need to run each step (spawn), wait for completion, then pass the output into the next step’s template (current runner uses placeholder `[Output of step_id]` when not integrated with gateway).

### 7.5 Adding a new workflow

- Add `workflows/<id>.json` (or `.yaml`) with same structure as `ship_feature.json`. Run with `run_workflow.py <id> "user input"`.

### 7.6 Router and LOCAL tier

- Router config is in `workspace/skills/agent-swarm/config.json`. To add a LOCAL tier for Ollama: add a tier (e.g. LOCAL) with a model id that your stack maps to Ollama (e.g. `ollama/llama3.1` or a custom id), and ensure the orchestrator or gateway can spawn a session that uses Ollama instead of OpenRouter for that tier.

### 7.7 Playwright free web chat

- playwright-commander has `navigate`, `get_content`, etc. To add free chat: add scripts (e.g. in the skill’s `scripts/`) that take a prompt, open a specific URL (e.g. claude.ai), fill input, submit, wait for reply, extract text, return it. Optionally pass proxy via Playwright context for identity rotation.

### 7.8 Saving a copy to Google Drive

- **Zip created:** A backup zip has been created at **`/Users/ghost/.openclaw/openclaw-everything-app-backup.zip`**. It includes this breakdown, ENTRYPOINTS.md, OLLAMA.md, Ghost app, and the agent-loops skill (workflows + runner).
- **Upload to Google Drive:**  
  1. Open [Google Drive](https://drive.google.com) in your browser.  
  2. Drag and drop `openclaw-everything-app-backup.zip` from Finder (navigate to `~/.openclaw/` and show hidden files if needed), or use **New → File upload** and select the zip.  
  Alternatively, if you use **Google Drive for Desktop**, copy the zip (or the whole `openclaw` workspace folder) into your synced Drive folder (e.g. **My Drive**).
- **Full repo backup:** To include the entire OpenClaw repo (all skills, config, etc.), run from a terminal:  
  `cd ~/.openclaw && zip -r openclaw-full-backup.zip . -x "*.git*" -x "*node_modules*" -x "__pycache__"`  
  then upload `openclaw-full-backup.zip` to Drive. Keep API keys and secrets out of the zip or exclude sensitive files.

---

## 8. Summary

- **Project:** Personal “everything” app with Ghost as minimal UI, OpenClaw as orchestrator, agent-swarm for routing, agent loops for multi-step workflows, and docs for Ollama + OpenClaw and Claude Code.
- **Planning:** Central orchestrator, local-first (Ollama), identity rotation for free-tier scaling, role agents, prebuilt workflows; ToS/legal and “free” limits acknowledged.
- **Built:** Ghost (UI + understood + activity), ENTRYPOINTS.md, OLLAMA.md (OpenClaw + Claude Code), agent-loops (workflows + runner), dashboard spawn returning task/model.
- **Next (from plan):** LOCAL tier, identity pool, role agents, Telegram-in, free web chat scripts, Mac Mini runbook.

Use this doc as the single reference for planning, features, and dev notes when continuing or copying the project.
