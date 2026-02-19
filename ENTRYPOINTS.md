# Everything-app entrypoints and gateway

## Gateway

Use **JAT Gateway** for the orchestrator layer when available. It may be more stable than the current OpenClaw gateway for this stack. Evaluate JAT Gateway when implementing; the upstream JAT project (or Squad, its fork) provides the gateway component.

- **Current:** OpenClaw gateway daemon; TUI and Clawpanion talk to it (Clawpanion via subagent-dashboard shim on port 8080).
- **Option:** **JAT Gateway** — adopt or integrate the JAT project’s gateway for the everything-app if it proves more stable.
- **Ollama + OpenClaw (official):** Run the gateway with local models via **`ollama launch openclaw`**. This configures OpenClaw to use Ollama and starts the gateway; recommended models include `qwen3-coder`, `glm-4.7`, `gpt-oss:20b`, `gpt-oss:120b`. See [workspace/OLLAMA.md](OLLAMA.md) and [Ollama docs](https://docs.ollama.com/llms.txt).

## One input + send

Single entrypoint: one text input and a send button. Options:

| Option | Description |
|--------|-------------|
| **TUI** | Existing OpenClaw TUI: one place to type and send. No extra app. |
| **Clawpanion** | JAT-inspired IDE at `workspace/clawpanion-web`. Full IDE + sessions; integrate with gateway (or JAT Gateway when adopted). Set `PUBLIC_API_BASE_URL` to gateway/shim URL. |
| **Ghost** | Minimal app at `workspace/ghost`: one text input + send button. Sends to gateway (or subagent-dashboard) at configurable API base URL. Use **JAT Gateway** when adopted. |
| **Minimal Mac app** | Thin client (SwiftUI or Electron): text input + send → same payloads as TUI to the gateway. Reuse gateway API/auth. When using **JAT Gateway**, point the app at that gateway. |

All entrypoints send user messages into the same pipeline: gateway → router → spawn → skills/Playwright. Prefer **JAT Gateway** for the gateway layer where possible.
