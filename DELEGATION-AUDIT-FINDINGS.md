# Delegation & categorization audit – findings

## What was checked

- **OpenClaw logs:** `~/.openclaw/logs/gateway.log`
- **Session history:** `~/.openclaw/agents/main/sessions/*.jsonl` and `sessions.json`
- **Agent-swarm docs:** SKILL.md and router flow (classify by difficulty/type → spawn)

## Findings

### 1. Gateway logs do **not** show categorization

- `gateway.log` only records session start with **model** (e.g. `[gateway] agent model: openrouter/google/gemini-2.5-flash`).
- It does **not** log:
  - Invocations of `router.py` (those run in the client/TUI via `exec`).
  - Tier, classification, or task text.

So you **cannot** tell from gateway logs alone whether a prompt was categorized by difficulty/type or whether the router was used.

### 2. Session history shows spawn, not tier

- **sessions.json:** Stores per-session `model`, `modelOverride`, etc. It does **not** store `tier`, `classification`, or `task` for the main session.
- **Subagent runs:** `agents/main/subagents/runs.json` (if present) can hold `task` / `taskIndex`; it does not store tier/classification.
- **Session transcripts (e.g. `*.jsonl`):** Do show the flow:
  - Tool call `exec` with `router.py spawn --json "<user message>"`.
  - Tool call `sessions_spawn` with `task`, `model`, `sessionTarget`.
- So **categorization is only visible** in the router’s **output** inside those transcripts (the JSON from `router.py spawn --json` includes `recommendation.tier`, `recommendation.classification`, etc.). It is not stored in a dedicated field in session storage or gateway.

### 3. One session showed the intended flow (with a bug)

From a recent session transcript:

- Orchestrator ran: `python3 .../friday-router/scripts/router.py spawn --json "write a poem about my gf Mai"` (old path; should be `agent-swarm`).
- It then called `sessions_spawn` with `model: "openrouter/minimax/kimi-k2.5"` (creative → Kimi).
- So **categorization and spawn were used**, but:
  - The path was **friday-router**, not **agent-swarm**.
  - The model id was **wrong**: `openrouter/minimax/kimi-k2.5` (Minimax) instead of `openrouter/moonshotai/kimi-k2.5` (Kimi). The gateway returned `model not allowed: openrouter/minimax/kimi-k2.5`.

So prompts **can** be categorized by type and passed to spawn; the gap is path/config (agent-swarm vs friday-router, correct model id) and the fact that **enforcement is only by rule**, not by a single mandatory tool.

### 4. Current “enforcement” is rule-only

- The **openclaw-orchestrator-delegate** rule says: run gateway-guard, then **router.py spawn --json**, then duplicate check, then **sessions_spawn** with the router’s `task` and `model`.
- The orchestrator could ignore this and call `sessions_spawn` without running the router, or do the task itself. There is **no platform or skill-level enforcement** that “spawn is only allowed after categorization”.

## What “enforced by the skill/tool” can mean

1. **Audit trail (skill):**  
   The agent-swarm skill can **log every delegation**: when `router.py spawn --json` is run, append to a log file (e.g. `logs/agent-swarm-delegations.jsonl`) at least: `task`, `tier`, `model`, `timestamp`. That gives a record that categorization happened and which tier/model was chosen, without changing the gateway.

2. **Single entry point (skill):**  
   Document and use a **single script** that the orchestrator must call to get spawn params (e.g. `router.py spawn --json` or a thin wrapper). The rule states that the orchestrator **must** get params from this script before calling `sessions_spawn`. The script can write the same audit log as above.

3. **Platform enforcement (gateway):**  
   The gateway could require that every `sessions_spawn` (or certain sessions) include a token or metadata from the router (e.g. tier or a signed payload). That would require OpenClaw gateway changes and is outside the skill.

## Recommendations

- **Use agent-swarm path everywhere:** Ensure the orchestrator rule and any docs use `workspace/skills/agent-swarm/scripts/router.py` (not friday-router). The rule already uses agent-swarm; any remaining references to friday-router should be updated.
- **Add delegation audit log:** In agent-swarm, when `spawn --json` is used and returns params (no `needs_config_patch`), append one line to `OPENCLAW_HOME/logs/agent-swarm-delegations.jsonl` with task, tier, model, timestamp. That makes it possible to verify that prompts were categorized and which tier/model was chosen.
- **Keep rule strict:** The orchestrator rule should continue to require: get spawn params only by running the router (or the single script above), then call `sessions_spawn` with that output; do not call `sessions_spawn` without having run the router first.
- **Optional:** Add a small “delegate_task” wrapper script that runs the router and writes the audit line, then prints the same JSON, so the rule has one canonical entry point for “get spawn params and record categorization.”

## Changes made (enforcement by skill)

- **Delegation audit log:** `router.py spawn --json` (single and parallel) now appends one JSONL line per delegation to `OPENCLAW_HOME/logs/agent-swarm-delegations.jsonl` with keys: `ts`, `task`, `tier`, `model`, `reasoning`. This gives an audit trail that categorization ran and which tier/model was chosen.
- **Orchestrator rule:** The rule now states that categorization and spawn are enforced by the skill: spawn params must come only from the router, and successful router runs are logged for audit.

