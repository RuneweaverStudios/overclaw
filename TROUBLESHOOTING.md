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
