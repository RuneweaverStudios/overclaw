# OpenClaw UI patches (reference)

If the TUI shows "Conversation info (untrusted metadata)" or subagent wrap-up text in chat, re-apply these patches after any OpenClaw npm update.

**Conversation info block:** In `node_modules/openclaw/dist/` (or `$(npm root -g)/openclaw/dist/`), find `buildInboundUserContextPrefix` in: `reply-*.js`, `pi-embedded-*.js`. Change the condition that pushes the conversation-info block to `if (false)` so that block is never added.

**Subagent wrap-up text:** In the same dist, find `buildAnnounceReplyInstruction` in: `subagent-registry-*.js`, `reply-*.js`, `plugin-sdk/reply-*.js`, `pi-embedded-*.js`. Change the return line from `` return `A completed ${params.announceType} is ready for...`; `` to `return "";` so the wrap-up instruction is not sent to the model.

File names (hashes) change with each OpenClaw version; search for the function names above.
