# Tasks Required for Agents to Create Their Gardens

In the overclaw/overstory system, a **"garden"** refers to an agent's isolated **worktree workspace** - the git worktree where each agent operates independently. When `overstory sling` spawns an agent, it creates a garden with the following tasks:

---

## 1. Git Worktree Creation

**Task:** Create an isolated git worktree for the agent

**Steps:**
- `git worktree add <worktree-path> -b <branch-name>`
- Branch naming convention: `overstory/<agent-name>/<task-id>`
- Worktree path: `.overstory/worktrees/<agent-name>-<hash>/`
- Sets up isolated working directory that shares the git object database

**Responsible:** `overstory sling` command

---

## 2. Agent Overlay Generation

**Task:** Generate `.claude/CLAUDE.md` with agent-specific context

**Content includes:**
- Agent identity (name, capability type)
- Task assignment (task ID, spec path)
- File scope (which files the agent may modify)
- Branch name (worktree branch)
- Parent agent (for mail communication)
- Worktree path (explicitly stated for PATH_BOUNDARY_VIOLATION prevention)
- Depth level (for spawn hierarchy limits)
- Agent-specific instructions (role, workflow, constraints, failure modes)

**Template:** `templates/overlay.md.tmpl` (in overstory repo)

**Responsible:** `overstory sling` command

---

## 3. Task Specification Creation

**Task:** Create the task spec file that the agent will read

**Location:** `.overstory/specs/<task-id>.md`

**Content includes:**
- Task description
- Requirements/acceptance criteria
- File scope (if applicable)
- Any technical constraints
- Parent context (if part of larger workflow)

**Responsible:** `overstory sling` (or gateway via `_create_bead_and_spec`)

---

## 4. Bead Issue Creation

**Task:** Register the task in the beads tracking system

**Steps:**
- Create entry in `.beads/issues.jsonl`
- Assign unique task ID (format: `workspace-<short-hash>`)
- Set status to "open" or "in_progress"
- Include title, priority, issue_type, timestamps

**Responsible:** `overstory sling` or gateway

---

## 5. tmux Session Setup

**Task:** Launch the agent in an isolated tmux session

**Steps:**
- Create new tmux session: `tmux new-session -d -s <session-name>`
- Set session name: `agent-<agent-name>` or similar
- Configure environment variables:
  - `OVERSTORY_AGENT_NAME=<agent-name>`
  - `NANOBOT_GATEWAY_URL=<gateway-url>`
  - `NANOBOT_WORKSPACE=<workspace-root>`
  - `NANOBOT_SKILLS_DIR=<skills-dir>`
  - `GATEWAY_TOOLS=<gateway-tools-cli>`
- Start Claude Code CLI in the session
- Navigate to worktree root as working directory

**Responsible:** `overstory sling` command

---

## 6. Mailbox Initialization

**Task:** Set up the agent's mail inbox for communication

**Steps:**
- Mail system uses SQLite-based messaging
- Agent can check mail via `overstory mail check --agent <agent-name>`
- Parent/child communication via `overstory mail send --to <recipient>`
- Inbox path: Configured in overstory mail system

**Responsible:** `overstory sling` / mail system

---

## 7. Environment Variable Configuration

**Task:** Set required environment variables for agent context

**Variables:**
- `OVERSTORY_AGENT_NAME` - Agent's unique identifier
- `NANOBOT_GATEWAY_URL` - Gateway endpoint (default: http://localhost:18800)
- `NANOBOT_WORKSPACE` - Workspace root directory
- `NANOBOT_SKILLS_DIR` - Path to skills directory
- `GATEWAY_TOOLS` - Path to gateway-tools CLI
- `OVERCLAW_PORT` - Gateway port (optional, defaults to 18800)

**Responsible:** `overstory sling` command

---

## 8. Agent Manifest Registration

**Task:** Register the agent in the agent manifest

**Location:** `.overstory/agent-manifest.json`

**Content tracked:**
- Agent name
- Capability type
- Status (spawned, running, completed, failed, terminated)
- Task ID
- Start time
- Worktree path
- tmux session name

**Responsible:** `overstory sling` command

---

## 9. Mulch Expertise Setup (if enabled)

**Task:** Configure mulch expertise system for the agent

**Steps:**
- Check if `mulch` is installed
- If enabled in config, agent will run `mulch prime <domain>` at startup
- Set up for `mulch record` at session end
- Note: Worktrees typically don't have `.mulch/` directory - mulch runs from project root only

**Responsible:** Agent definition (overlay instructions)

---

## 10. Hooks Configuration

**Task:** Set up Claude Code hooks for the agent session

**Location:** `.overstory/hooks.json` (referenced in worktree)

**Hooks may include:**
- Pre-commit hooks (validation, linting)
- Post-merge hooks (cleanup, notifications)
- Stop hooks (mulch learn for expertise capture - guarded with checks)

**Responsible:** `overstory sling` (inherits from project config)

---

## Summary: Complete Garden Creation Flow

```
1. git worktree add        → Isolated workspace
2. Generate CLAUDE.md      → Agent instructions/context
3. Create spec file        → Task requirements
4. Create bead issue       → Task tracking
5. Start tmux session      → Isolated execution environment
6. Set env vars           → Gateway/agent context
7. Initialize mailbox     → Communication channel
8. Register in manifest   → Agent registry
9. Configure mulch        → Expertise system (optional)
10. Apply hooks           → Session automation
```

---

## Cleanup (Garden Removal)

**When an agent completes or is terminated:**

1. **Close bead issue** - `bd close <task-id> --reason "<summary>"`
2. **Send completion mail** - `overstory mail send --type result`
3. **Commit work** - Changes committed to worktree branch
4. **Merge** - `overstory merge` integrates work back to main (via coordinator or gateway merge drain)
5. **Clean worktree** - `overstory worktree clean --completed` removes completed worktrees (optional)

---

## Key Files in an Agent's Garden

| File/Directory | Purpose |
|----------------|---------|
| `.claude/CLAUDE.md` | Agent overlay - instructions, context, file scope |
| `.overstory/specs/<task-id>.md` | Task specification |
| `.git/` | Git worktree metadata (shared object DB) |
| Entire project tree | Copy of repo at worktree branch |

---

## References

- Overstory config: `.overstory/config.yaml`
- Overlay template: `templates/overlay.md.tmpl` (in overstory repo)
- Agent definitions: `.overstory/agent-defs/*.md`
- Pipeline docs: `docs/overstory-pipeline-visual-explainer.md`
