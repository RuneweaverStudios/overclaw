# Agent Loops

Prebuilt multi-agent workflows. Each workflow is a fixed sequence (or parallel) of steps; each step is routed via Agent Swarm and can spawn a subagent.

## Run a workflow

```bash
# From workspace root (OPENCLAW_HOME or ~/.openclaw)
python3 workspace/skills/agent-loops/scripts/run_workflow.py ship_feature "Add dark mode to settings"

# With custom input
python3 workspace/skills/agent-loops/scripts/run_workflow.py ship_feature "Your feature description"

# Actually spawn subagents (requires gateway)
python3 workspace/skills/agent-loops/scripts/run_workflow.py ship_feature "Add dark mode" --apply
```

## Workflows

- **ship_feature** — PM (scope) → Dev (implement) → Editor (docs/changelog)

## Requirements

- PyYAML: `pip install pyyaml`
- Agent Swarm router at `workspace/skills/agent-swarm/scripts/router.py`
- For `--apply`: OpenClaw gateway running so subagents can be spawned
