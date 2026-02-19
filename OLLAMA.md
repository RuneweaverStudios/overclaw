# Ollama and OpenClaw

## Official Ollama + OpenClaw integration

Ollama has **official OpenClaw support**. Use it to run OpenClaw with local models and start the gateway.

**Docs index:** https://docs.ollama.com/llms.txt (discover all Ollama docs)

### Install OpenClaw (if not already)

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### Quick setup with Ollama

```bash
ollama launch openclaw
```

This configures OpenClaw to use Ollama and starts the gateway. If the gateway is already running, it will auto-reload the changes.

Configure without launching:

```bash
ollama launch openclaw --config
```

<Note>Previously known as Clawdbot: `ollama launch clawdbot` still works as an alias.</Note>

### Recommended models (OpenClaw + Ollama)

- `qwen3-coder`
- `glm-4.7`
- `gpt-oss:20b`
- `gpt-oss:120b`

Cloud models: [ollama.com/search?c=cloud](https://ollama.com/search?c=cloud)

OpenClaw prefers a **context window of at least 64k tokens**; see [Ollama – Context length](https://docs.ollama.com/context-length).

---

## Claude Code + Ollama

**Claude Code** is Anthropic’s agentic coding tool (read, modify, execute code in your working directory). You can use **open models** with Claude Code via Ollama’s Anthropic-compatible API (e.g. `glm-4.7`, `qwen3-coder`, `gpt-oss`).

**Docs index:** https://docs.ollama.com/llms.txt

### Install Claude Code

- **macOS / Linux:** `curl -fsSL https://claude.ai/install.sh | bash`
- **Windows (PowerShell):** `irm https://claude.ai/install.ps1 | iex`
- Overview: [Claude Code docs](https://code.claude.com/docs/en/overview)

### Quick setup with Ollama

```bash
ollama launch claude
```

Configure without launching:

```bash
ollama launch claude --config
```

### Manual setup

Claude Code talks to Ollama via the Anthropic-compatible API.

1. Set environment variables:

```bash
export ANTHROPIC_AUTH_TOKEN=ollama
export ANTHROPIC_API_KEY=""
export ANTHROPIC_BASE_URL=http://localhost:11434
```

2. Run Claude Code with an Ollama model:

```bash
claude --model gpt-oss:20b
# or
claude --model qwen3-coder
```

Or inline:

```bash
ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_BASE_URL=http://localhost:11434 ANTHROPIC_API_KEY="" claude --model qwen3-coder
```

**Note:** Claude Code needs a large context window; recommend **at least 64k tokens**. See [Ollama – Context length](https://docs.ollama.com/context-length).

### Recommended models (Claude Code + Ollama)

- `qwen3-coder`
- `glm-4.7`
- `gpt-oss:20b`
- `gpt-oss:120b`

Cloud models: [ollama.com/search?c=cloud](https://ollama.com/search?c=cloud).

---

## Is Ollama installed?

Check with:

```bash
which ollama && ollama --version
```

If not installed:

- **macOS:** `brew install ollama` or download from [ollama.com](https://ollama.com)
- Then run `ollama serve` and pull a model, e.g. `ollama pull qwen3-coder` or `ollama pull glm-4.7`

---

## Can Ollama orchestrate and use tools?

**Yes.** Ollama supports **tool calling** (function calling): the model can request tool invocations and you run them and pass results back.

- **Docs:** [Ollama – Tool calling](https://docs.ollama.com/capabilities/tool-calling)
- **API:** Use the `/api/chat` endpoint with a `tools` array (name, description, parameters). The model returns `tool_calls`; you execute the functions and add a message with `role: "tool"` and the result, then call the API again.
- **Models:** Tool calling is supported by e.g. Llama 3.1, Command-R+, Firefunction v2, Mistral Nemo.

So Ollama can act as the orchestrator: call Ollama → parse `tool_calls` → run tools (exec, file read, etc.) → append results → repeat. The official **ollama launch openclaw** flow wires OpenClaw’s gateway to Ollama so your entrypoints (Ghost, TUI, etc.) can use local models and tools through the same pipeline.
