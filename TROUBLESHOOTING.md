# OpenClaw workspace – troubleshooting

## 1. zsh: command not found: HEARTBEAT_OK / no matches found: (workspace context)

**What’s happening:** The agent’s reply (e.g. `HEARTBEAT_OK`) or part of the heartbeat prompt (e.g. “(workspace context)”) is being **executed as shell commands** in zsh. So you see:

- `zsh: command not found: HEARTBEAT_OK`
- `zsh: no matches found: (workspace context).`
- `zsh: number expected`

**Cause:** The OpenClaw TUI or gateway is sending the assistant’s **text response** into a shell (or something is `eval`’ing it). `HEARTBEAT_OK` and “(workspace context)” are **literal reply text**, not commands.

**Fix (client/TUI side):** The client must **never** execute the assistant’s message content as shell commands. It should only **display** the reply. If you control the TUI/gateway code, change it so the agent’s reply is shown in the UI only, not passed to `exec`/`eval`/subprocess.

**Workaround (user):** Until the client is fixed, avoid flows that send heartbeat prompts in a way that gets executed (e.g. run the TUI from a context that doesn’t pipe replies into zsh).

---

## 2. openclaw doctor: SyntaxError: Unexpected token ')'

**What’s happening:** Running `openclaw doctor` (or other CLI commands) fails with:

```text
[openclaw] Failed to start CLI: SyntaxError: Unexpected token ')'
    at compileSourceTextModule (node:internal/modules/esm/utils:305:16)
    ...
```

**Cause:** A JavaScript syntax error in the OpenClaw CLI’s built `dist/` when Node parses one of the ESM modules. This can be:

- A **Node version** mismatch (e.g. CLI built for Node 20, you’re on Node 25).
- A **bug in the openclaw package** (e.g. bad build or invalid syntax in a chunk).

**Fixes to try:**

1. **Use Node 20 LTS** (openclaw is often tested on Node 20):
   ```bash
   nvm use 20   # or: brew install node@20 and use that
   openclaw doctor
   ```
2. **Reinstall/upgrade the CLI:**
   ```bash
   npm update -g openclaw
   # or, if installed via Homebrew:
   brew upgrade openclaw
   ```
3. **Report to OpenClaw** with:
   - Your Node version: `node -v` (e.g. v25.6.1)
   - OpenClaw version: `npm list -g openclaw` (e.g. 2026.2.15)
   - Full error output

**Current versions observed:** openclaw@2026.2.15, Node v25.6.1. If you're on Node 25, switching to Node 20 LTS is the first thing to try.

---

## 3. "Unhandled stop reason: error" / "connected | error" (run stops mid-turn)

**What's happening:** The TUI shows something like:

- `Unhandled stop reason: error`
- `connected | error`
- Session line: `agent main | session main (webchat:…) | openrouter/google/gemini-2.5-flash | think low | tokens 288k/1.0m (27%)`

The run stops before the agent finishes; the stream from the provider (OpenRouter/Google) ended with an error.

**Cause:** The **model run** (API stream) ended with an error. Common causes:

- **Provider/OpenRouter error** (rate limit, internal 5xx, timeout)
- **Context/token limit** (e.g. hitting a cap before the 1M limit you see in the UI)
- **Network or gateway** closing the stream unexpectedly

This is a **run/stream error**, not necessarily a local process crash. The gateway may keep running; the current turn just didn't complete.

**What we checked in your setup:**

- **`~/.openclaw/logs/gateway.log`** — No line containing `Unhandled stop reason: error` in the tail we searched. So either the gateway doesn't write that exact string to `gateway.log`, or it's only in the TUI/stream. If the string never appears in `gateway.log`, the gateway-guard **continue-on-error** watcher won't trigger.
- **`~/.openclaw/logs/gateway.err.log`** — Shows: port-in-use / "gateway already running" loops (earlier), delivery-recovery time budget exceeded, Telegram `BOT_COMMANDS_TOO_MUCH`, and skills-remote probe timeouts. Nothing that directly explains the run error.
- **Gateway restarts** — Logs show multiple SIGTERM restarts in a short time (e.g. 15:56, 15:56, 16:23, 16:29, 16:39, 16:50). That can interrupt runs; could be gateway-guard `ensure --apply` or another supervisor.

**What to do:**

1. **Let the agent resume (if you want auto "continue"):**  
   If the gateway (or another log the gateway writes) actually logs `Unhandled stop reason: error`, you can use gateway-guard to send "continue" so the agent gets another turn:
   ```bash
   python3 /Users/ghost/.openclaw/workspace/skills/gateway-guard/scripts/gateway_guard.py continue-on-error --once
   ```
   For automatic recovery, install the watcher (checks every 30s):
   ```bash
   bash /Users/ghost/.openclaw/workspace/skills/gateway-guard/scripts/install_watcher.sh
   ```
   If the error **only** appears in the TUI and never in `gateway.log`, the watcher will never see it — then the fix is on the gateway side to log run/stream errors to `gateway.log`.

2. **Reduce run errors from the provider:**
   - Shorter conversations or smaller context (e.g. start a new chat if the thread is very long).
   - Retry the same message once (transient provider/network errors often succeed on retry).

3. **Avoid unnecessary gateway restarts:**  
   If gateway-guard or another script is restarting the gateway very often, that can cut off runs. Check:
   - `gateway_guard.py status --json` and only run `ensure --apply` when there's an auth mismatch.
   - Any LaunchAgents/cron that call `gateway stop`/restart; space them out so they don't restart during an active run.

4. **Inspect primary gateway log:**  
   The gateway also writes to `/tmp/openclaw/openclaw-YYYY-MM-DD.log`. If you have access, search that file for "error" or "stop reason" right after a crash to see the exact server/stream error.

---

## 4. Connection refused / urlopen error [Errno 61]

**What's happening:** The TUI or a script shows `<urlopen error [Errno 61] Connection refused>` (or similar). The client is trying to reach the OpenClaw gateway and nothing is listening on that port.

**Fix — detect and connect automatically:** Use the gateway-guard wrapper so the gateway is started (and ready) before your client connects:

```bash
# Ensure gateway is up, then start the TUI
/Users/ghost/.openclaw/workspace/skills/gateway-guard/scripts/ensure_gateway_then.sh openclaw tui
```

With no arguments it just ensures the gateway is running and waits for the port: `ensure_gateway_then.sh`. You can also run `gateway_guard.py ensure --apply --wait` before starting the TUI manually.

---

## 5. Agents turn into zombies / don't finish tasks / don't send mail (OverClaw / Overstory)

**What's happening:** Overstory agents (lead, builder, researcher, blogger, etc.) show up as **zombies** in status. Tasks never complete and inter-agent mail doesn't get sent or processed.

**Cause:** Overstory marks an agent as **zombie** when the underlying process or tmux session is dead or disconnected but the agent record is still in its state. Zombie agents don't run tools, don't complete work, and don't read/send mail — so the swarm appears stuck.

**Tools and skills to use:**

1. **List zombies (OverClaw Gateway):**
   ```bash
   curl -s http://localhost:18800/api/zombies
   ```
   Or use the **Zombie Hunter** panel in the OverClaw dashboard (checks every 5 minutes).

2. **Slay zombies (clear worktrees + sessions so new agents can run):**
   ```bash
   curl -s -X POST http://localhost:18800/api/zombies/slay
   ```
   Or click **Slay** in the dashboard. This runs `overstory clean --worktrees --sessions`: removes worktrees, kills tmux sessions, and clears agent state so status shows 0 zombies.

3. **From the workspace (no gateway):**
   ```bash
   cd /Users/ghost/.openclaw/workspace
   overstory status --json    # see agents and state
   overstory clean --worktrees --sessions --json   # slay zombies and clear state
   ```

4. **OpenClaw subagents (different system):** If you're on OpenClaw (not OverClaw) and subagents seem stuck, use **subagent-tracker** to see who's active and **subagent-dashboard** to cancel or resume:
   ```bash
   python3 /Users/ghost/.openclaw/workspace/skills/subagent-tracker/scripts/subagent_tracker.py list --active 30 --summary
   python3 /Users/ghost/.openclaw/workspace/skills/subagent-tracker/scripts/subagent_tracker.py check --stall-minutes 30
   ```
   Dashboard: **Cancel Job** / **Resume** on stalled cards.

5. **Mail:** Sending mail is via `POST /api/agents/mail` (gateway) or `overstory mail send`. Mail is written to the mail DB; zombie agents never *read* their inbox, so delivery only helps once agents are running again. Slay zombies first, then spawn new tasks.

**Prevention:** Keep the OverClaw dashboard open so the Zombie Hunter runs every 5 minutes and slays zombies automatically, or run `curl -X POST http://localhost:18800/api/zombies/slay` periodically (e.g. from a cron or after starting the stack).

**Keep Claude process from exiting (OverClaw):** The stack uses a wrapper at `.overstory/bin/claude` so the agent process does not exit and become a zombie. When you run `./scripts/start-overclaw.sh`, it sets `PATH` so that wrapper is used: it unsets `CI`, sets `TERM`, runs the real Claude Code binary, and if Claude exits it restarts it in a loop so the tmux pane stays alive. Start the stack with `./scripts/start-overclaw.sh` (not by hand without that PATH) so the wrapper is in effect. To point to a different Claude binary, set `CLAUDE_CODE_BIN` before starting (e.g. `export CLAUDE_CODE_BIN=/path/to/claude`).

---

## 6. Bypass Permissions disclaimer stuck on all agents (OverClaw)

**What's happening:** Every builder/lead/scout (and other) session shows the **Bypass Permissions** warning (“1. No, exit / 2. Yes, I accept”) and never moves on. You may also see **Mail check** and **Accept disclaimer** prompts.

**Quick fix:**

1. **Ensure the OverClaw gateway is running** (port 18800). The gateway runs a **disclaimer watcher** every 3 seconds that sends Down+Enter to each agent’s tmux pane to accept the disclaimer. If the gateway isn’t running, that watcher doesn’t run.
2. **Use the dashboard:** Click **Accept all disclaimers** (sends Down+Enter to every agent). If you have many sessions, click it once; it may take a few seconds to hit all of them.
3. **Or call the API:**
   ```bash
   curl -s -X POST http://localhost:18800/api/agents/accept-all-disclaimers
   ```
   Or to both accept disclaimers and any generic “confirm” prompts:
   ```bash
   curl -s -X POST http://localhost:18800/api/agents/auto-accept-prompts
   ```

**If it keeps coming back:** The watcher only knows about tmux sessions that Overstory reports in `overstory status --json` (or sessions named `overstory-overclaw-*` as a fallback). If your sessions use different names, the watcher won’t see them — use the **Accept all disclaimers** button or the curl commands above when you open the dashboard.

**Optional:** Use **Restart agents (skip perms)** in the dashboard (or the gateway’s restart-with-skip-permissions endpoint) so agents start with `claude --dangerously-skip-permissions` and the disclaimer is accepted once automatically.

---

## 7. Agents section: Error: database is locked (OverClaw)

**What's happening:** The Agents panel in the OverClaw dashboard shows **Error: database is locked** (or similar).

**Cause:** Overstory’s internal SQLite DB (or the shared mail DB) is busy — multiple processes (gateway loops, UI, overstory CLI, spawned agents) can hit it at once and SQLite returns “database is locked”.

**What we did:**
- **UI** retries `overstory status` up to 3 times with a short delay when it sees “database is locked” or “locked”. If it still fails, the message shown is: **“Agents temporarily unavailable (database busy). Try again in a moment.”**
- **mail.db** in the gateway uses WAL mode and a per-process lock so only one reader/writer at a time; the UI uses WAL and a 15s busy timeout when reading mail.db.

**What you can do:** Refresh the dashboard in a few seconds; the retries usually succeed. If it persists, avoid running many overstory commands in parallel (e.g. wait for the dashboard to load before running `overstory status` in a terminal).

---

## 8. Confused/stuck agents, mail unread, “lead/supervisor should have intervened”

**What’s happening:** The dashboard shows several agents (e.g. builders) and **Mail (N unread)** with messages like “Need approval for next step”. Workers are stuck waiting for approval and no lead/supervisor responded.

**Cause:** The **Ollama approval supervisor** (in the gateway) is supposed to act as the lead: it reads unread mail **to** lead/supervisor/approval-supervisor/coordinator from `.overstory/mail.db`, uses Ollama to detect approval requests, and sends **Down+Enter** to the **sender’s** tmux session so the worker’s prompt is approved. If that doesn’t happen, one or more of:

1. **Gateway not running** — approval supervisor only runs when the OverClaw gateway is up (e.g. `./scripts/start-overclaw.sh` or gateway on 18800).
2. **Ollama not running** — the supervisor uses Ollama to classify “is this an approval request?”. If Ollama is down, it treats messages as approval (to avoid missing any) and still tries to send keys; if Ollama is unreachable you may see log noise.
3. **Tmux session mismatch** — the supervisor sends keys to `overstory-overclaw-{from_agent}` (e.g. `overstory-overclaw-builder-12243f7e`). If the sender in mail is different from the session name (e.g. old test agent `builder-test-ap`), or the gateway process doesn’t have access to the same tmux server, the send fails and the message stays unread.
4. **No lead agent in the swarm** — the dashboard may show only builders (e.g. after **Prune completed** removed lead worktrees while builder worktrees stayed; see **§9. Dashboard shows 4 (or N) builders with no leads**). The **approval supervisor is the substitute**: it doesn’t require a live “lead” agent; it sends Down+Enter to workers from the gateway. When there are no active leads, the 2-minute lead window is skipped and the supervisor acts immediately. So the main fix is (1)–(3).

**What to do:**

1. **Check gateway and supervisor**
   - Ensure the OverClaw gateway is running (e.g. `curl -s http://localhost:18800/health`).
   - Check **GET** `http://localhost:18800/api/debug/approval-supervisor-status`:
     - `unread_to_lead_supervisor`: how many unread messages the supervisor considers (to lead/supervisor/approval-supervisor/coordinator).
     - `last_error`: if set, the last time the supervisor tried to send Down+Enter it failed (e.g. tmux session not found). Use this to see which sender/session failed.
   - Trigger one run manually: **POST** `http://localhost:18800/api/debug/approval-supervisor-run` (returns `{ "approved": n }`).

2. **Check tmux sessions**
   - Run `tmux list-sessions` and confirm you see sessions like `overstory-overclaw-builder-12243f7e`. If mail is from `builder-test-ap` but only `builder-12243f7e` sessions exist, those messages will never be approved (session doesn’t exist). Clean up old test agents or prune/slay so only current agents have mail.

3. **Clear stuck agents and mail**
   - **Approve all & clean** (dashboard): Runs **reinstate missing leads** first (spawns any lead that has unread mail but wasn’t active, e.g. `lead-4b36c480`), then processes all pending approval mail with **no lead window** (approves immediately). Use this when builders are stalled waiting for a lead that never responded. It also **dedupes mail**: after approving (or skipping) a sender once, all other unread “Need approval” from that sender are marked read, so the inbox doesn’t stay full of repeated messages.
   - Use the dashboard **Prune completed** to clean finished worktrees.
   - Use **Zombie Hunter → Slay** (or **POST /api/zombies/slay**) to kill zombie agents; this also notifies leads via mail before slaying.
   - After that, spawn a **new** task (with a lead if you want a real lead in the swarm); the approval supervisor will still auto-approve workers that mail “to lead” as long as their tmux session exists and the gateway can send keys.

4. **Which gateway logs to check (multiple gateways on the machine)**
   - **OverClaw** (dashboard on 5050, API on 18800) uses the **OverClaw gateway** (`scripts/overclaw_gateway.py`), not the main OpenClaw (TUI) gateway. Its logs are under the **workspace**: `WORKSPACE/.overstory/logs/overclaw-gateway.log`. The nanobot background agent logs to `WORKSPACE/.overstory/logs/nanobot-agent.log`.
   - The **what-just-happened** skill checks both: the main OpenClaw gateway at `~/.openclaw/logs/gateway.log` and, when a workspace is available, OverClaw at `WORKSPACE/.overstory/logs/overclaw-gateway.log`. Use `--overclaw-only` to only check OverClaw: `python3 skills/what-just-happened/scripts/report_recent_logs.py --minutes 5 --overclaw-only`.

5. **Lead first, supervisor fallback**
   - **Leads** are expected to approve first: they run `overstory mail check` and use the `approve_agent` tool (or gateway approve endpoint) when workers send “Need approval” mail. The **Ollama approval supervisor** in the gateway is a **fallback**: when there is at least one active lead, it only acts on messages that have been unread for **2 minutes** (`APPROVAL_SUPERVISOR_LEAD_WINDOW_S`), so leads get a window to approve first. When there are **no active leads**, the window is 0 and the supervisor acts immediately. **Mail to a missing lead** (e.g. to `lead-4b36c480` when that lead is not in the agent list) gets **0 window** so the supervisor approves immediately; the reinstate loop will spawn that lead on the next tick so future mail can be handled by the lead.

6. **Gateway logs (supervisor failures)**
   - When the supervisor fails to send keys, the gateway logs at **INFO** with the session name it tried. Check **OverClaw** gateway log: `WORKSPACE/.overstory/logs/overclaw-gateway.log`.

---

## 9. Dashboard shows 4 (or N) builders with no leads

**What’s happening:** The Agents list shows several builders (e.g. builder-12243f7e, builder-4b36c480, …) and **no lead** agents. You expect every worker to have a lead.

**Causes:**

1. **Lead worktrees were pruned first**  
   When you use **Route** (or gateway route with spawn), the gateway correctly spawns **lead + worker** per task (e.g. lead-12243f7e and builder-12243f7e). The lead’s session often finishes or goes idle before the builder; **Prune completed** (or auto-prune) then removes **completed** worktrees. Lead worktrees get pruned; builder worktrees are **skipped** because they have unmerged branches. The dashboard’s agent list is built from **overstory status** and/or **remaining worktrees**, so you end up seeing only the builders.

2. **Direct spawn without a lead (fixed)**  
   **POST /api/agents/spawn** with `capability: "builder"` and no `parent` used to create a single builder with no lead. The gateway now **always spawns a lead first** when capability is builder/scout/reviewer and `parent` is not set (unless `force: true`). So new spawns from that endpoint will show a lead + worker. Use `force: true` only when you intentionally want a top-level agent without a lead.

**What to do:**

- **Going forward:** Use **Route** (or **POST /api/route** with `spawn: true`) for tasks that need workers; the gateway creates lead + worker. Use **POST /api/agents/spawn** only when you need a single agent (e.g. lead or coordinator); for builder/scout/reviewer the gateway now auto-creates the lead.
- **Current state:** If you already have N builders and no leads, the **Ollama approval supervisor** still handles approval mail (and with **no active leads** the 2-minute lead window is removed, so the supervisor acts immediately). To get a lead back for new work, spawn a new task via Route; for existing builders you can leave them as-is or slay and respawn with Route.
- **Prune behavior:** Prune only removes **completed** worktrees; it skips worktrees with unmerged branches. So “builders with no leads” is expected after pruning when leads completed and builders are still working.

---

## 10. Why agents appear to do nothing

**What you see:** Builders sit for a long time ("Still waiting for lead…", "Only the original orchestrator message is present"); other builders or the lead show no new activity.

**Why each type appears idle:**

1. **Builder stuck on "wait for lead response" (e.g. builder-4b36c480)** — The builder sent "Need approval" to its lead and is looping on `overstory mail check` waiting for a **mail** reply. The gateway sends Down+Enter and (after the fix) sends a mail "Approved. You may proceed." If the gateway wasn't restarted after the fix, or the builder's "Need approval" was already marked read before the fix, the builder never sees a new message.

2. **Builders that already closed their task (e.g. builder-54aff61e, builder-esr-test)** — They ran `bd close workspace-…` and have no open beads; they are **idle** waiting for new work. The dashboard still lists them because their tmux sessions are running.

3. **Lead (e.g. lead-inject-d0ebaaac)** — Leads run `overstory mail check` and approve workers. If the lead session is idle, the **Ollama approval supervisor** in the gateway is the fallback: it processes unread mail and sends Down+Enter + approval mail.

**What to do:**

- **Unblock a builder waiting for approval:** (1) Restart the OverClaw gateway. (2) Click **Approve all & clean**. (3) If the builder still sees only the original message, send approval mail manually: **POST** `http://localhost:18800/api/supervisor/send-approval-mail` with body `{"to": "builder-4b36c480"}` (optionally `"from": "lead-4b36c480"`). The next time the builder runs `overstory mail check` it will see "Approved. You may proceed."
- **Idle builders with no open task:** Use **Prune completed**; or **Zombie Hunter → Slay** to stop them.
- **Lead not acting:** Ensure the gateway is running and use **Approve all & clean**.

---

## Ollama 404 / heartbeat "Error calling LLM: … OllamaException - 404 page not found"

**What’s happening:** Heartbeat (or the TUI when using Ollama) logs: "Error calling LLM: litellm.APIConnectionError: OllamaException - 404 page not found". The client (e.g. LiteLLM) is calling Ollama but gets HTTP 404.

**Common causes and fixes:**

1. **Ollama not running** — Start Ollama: `ollama serve` (or `brew services start ollama` on macOS). Ensure the model is pulled: `ollama list` and `ollama pull mistral` if needed.
2. **Wrong base URL** — For OpenAI-compatible clients use `http://localhost:11434/v1` with **no trailing slash**. A trailing slash can cause 404. In nanobot/OpenClaw config, set `apiBase` to `http://localhost:11434/v1`.
3. **Old Ollama version** — The `/v1/chat/completions` endpoint requires a recent Ollama. Update: `ollama --version` and upgrade via `brew upgrade ollama` or [ollama.com](https://ollama.com).
4. **Use the gateway instead** — The OverClaw gateway exposes `/api/chat` and talks to Ollama itself. If the TUI or heartbeat is configured to call Ollama directly and 404 persists, point the client at the gateway (e.g. port 18800) so chat goes through the gateway’s Ollama integration.

---

## Mulch: command not found (Stop hook)

**What’s happening:** When a session stops you see: “Stop hook error: Failed with non-blocking status code: /bin/sh: mulch: command not found”.

**What mulch is:** Mulch is overstory’s expertise system (`mulch prime`, `mulch record`, `mulch search`, `mulch learn`). The Stop hook runs `mulch learn` so the session can be learned from. The `mulch` CLI may not be installed or on PATH in your environment.

**Fix applied:** The Stop hook only runs `mulch learn` when `mulch` is available, so the hook does not fail when mulch is missing. **Mulch is bundled with OverClaw:** run `./scripts/install.sh` and it will install `mulch-cli` (step 7a) and optionally run `mulch init` in the workspace. After install, `mulch` is on PATH (via `bun install -g mulch-cli` or `npm install -g mulch-cli`). See `.overstory/README.md` for a short mulch overview.

**ENOENT mulch.config.yaml in worktrees:** If you see “Stop hook error: … ENOENT: … open '…/worktrees/<agent>/.mulch/mulch.config.yaml'”, the Stop hook ran `mulch learn` from an agent worktree, which has no `.mulch` directory. The hook is now gated in three ways: (1) `mulch learn` runs only when `.mulch/mulch.config.yaml` exists in the current directory; (2) it skips when the current directory is inside `.overstory/worktrees`; (3) it skips when `OVERSTORY_AGENT_NAME` is set (agent sessions). In worktrees the hook no-ops for mulch, so the error should stop.

**Patch existing worktrees:** Each worktree has its own `.overstory/hooks.json` and `.claude/settings.local.json`. To update all existing worktrees with the safe mulch command, run: `python3 scripts/patch-worktree-mulch-hooks.py`. Then verify with: `./scripts/test_mulch_stop_hooks.sh`.

---

## Approval flow (lead approves worker)

**Intended flow:** Task requires approval → spawn lead + worker → worker hits a confirmation prompt → worker mails lead (“Need approval”) → lead approves (sends Down+Enter to worker’s terminal) → worker continues.

**Implementations:**

- **POST /api/agents/{name}/approve** (gateway) — sends Down+Enter to that agent’s tmux. Call this when a worker has requested approval (e.g. after you see mail from worker to lead with “Need approval”).
- **approve_agent** tool (gateway_tools) — built-in tool that calls the gateway approve endpoint. The lead agent can use this (e.g. when it has gateway_tools and reads “Need approval” mail) to approve a worker by name.

**E2E test:** `python3 scripts/test_approval_flow.py` from the workspace root. It spawns lead+worker with a task that may trigger a confirmation; if the worker mails the lead (or the test simulates that), it calls the approve endpoint. **Requires:** OverClaw gateway running on 18800 (restart gateway after adding the approve route so the test sees it).
