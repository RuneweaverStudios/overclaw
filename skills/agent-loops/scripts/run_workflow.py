#!/usr/bin/env python3
"""
Run a prebuilt agent loop (workflow). Loads a workflow YAML, runs each step
via the agent-swarm router and optionally spawns subagents (when gateway is available).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
ROUTER = OPENCLAW_HOME / "workspace" / "skills" / "agent-swarm" / "scripts" / "router.py"
WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


def load_workflow(workflow_id: str) -> dict:
    for ext in (".json", ".yaml"):
        path = WORKFLOWS_DIR / f"{workflow_id}{ext}"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                if ext == ".json":
                    return json.load(f)
                try:
                    import yaml
                    return yaml.safe_load(f)
                except ImportError:
                    raise RuntimeError("PyYAML required for YAML workflows: pip install pyyaml")
    raise FileNotFoundError(f"Workflow not found: {workflow_id} (looked in {WORKFLOWS_DIR})")


def run_router_spawn(task: str) -> dict:
    """Get spawn params from router for a task. Returns dict with task, model, sessionTarget."""
    if not ROUTER.exists():
        return {"task": task, "model": "openrouter/google/gemini-2.5-flash", "sessionTarget": "isolated"}
    result = subprocess.run(
        [sys.executable, str(ROUTER), "spawn", "--json", task],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(OPENCLAW_HOME),
        env={**os.environ, "OPENCLAW_HOME": str(OPENCLAW_HOME)},
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"task": task, "model": "openrouter/google/gemini-2.5-flash", "sessionTarget": "isolated"}
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"task": task, "model": "openrouter/google/gemini-2.5-flash", "sessionTarget": "isolated"}


def run_workflow(workflow_id: str, user_input: str, dry_run: bool = True) -> list:
    """
    Run workflow steps. Each step: resolve task from template -> router spawn -> (if not dry_run) sessions_spawn.
    Returns list of step outputs (spawn params or placeholder).
    """
    wf = load_workflow(workflow_id)
    steps = wf.get("steps", [])
    context = {"user_input": user_input}
    outputs = []

    for i, step in enumerate(steps):
        step_id = step.get("id", f"step_{i}")
        task_tpl = step.get("task_template", step.get("task", ""))
        task = task_tpl
        for key, value in context.items():
            task = task.replace("{{ " + key + " }}", str(value))
        agent = step.get("agent", "default")

        spawn_params = run_router_spawn(task)
        spawn_params["step_id"] = step_id
        spawn_params["agent"] = agent
        outputs.append(spawn_params)

        if not dry_run:
            # When gateway is available, call sessions_spawn here (e.g. via openclaw CLI or gateway API)
            print(f"  [would spawn] {spawn_params.get('model', '?')} for: {spawn_params.get('task', '')[:60]}...")
        else:
            print(f"  [{step_id}] {spawn_params.get('model', '?')} -> {spawn_params.get('task', '')[:60]}...")

        # Placeholder output for next step's input_from
        context[f"{step_id}_output"] = f"[Output of {step_id}]"

    return outputs


def main():
    import argparse
    p = argparse.ArgumentParser(description="Run an agent loop (workflow)")
    p.add_argument("workflow", nargs="?", default="ship_feature", help="Workflow id (e.g. ship_feature)")
    p.add_argument("input", nargs="?", default="Add a dark mode toggle to settings", help="User input for the workflow")
    p.add_argument("--dry-run", action="store_true", default=True, help="Only run router per step, do not spawn (default: True)")
    p.add_argument("--apply", action="store_true", help="Actually spawn subagents (requires gateway)")
    args = p.parse_args()
    dry_run = not getattr(args, "apply", False)
    print(f"Workflow: {args.workflow} | Input: {args.input[:50]}... | Dry-run: {dry_run}")
    run_workflow(args.workflow, args.input, dry_run=dry_run)


if __name__ == "__main__":
    main()
