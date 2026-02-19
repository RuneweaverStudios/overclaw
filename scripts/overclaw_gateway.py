#!/usr/bin/env python3
"""OverClaw Gateway — HTTP API for the OverClaw stack.

Provides a unified HTTP entry point for:
  - Health checks & status
  - Chat (routes to Ollama Mistral orchestrator)
  - Agent management (delegates to overstory)
  - Skill/tool discovery and execution
  - Memory access

Port 18800 by default (separate from legacy openclaw-gateway on 18789).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import uvicorn

try:
    import yaml
except ImportError:
    yaml = None  # Optional dependency
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

_SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("OVERCLAW_WORKSPACE", str(_SCRIPT_DIR.parent)))
SKILLS_DIR = WORKSPACE / "skills"
OVERSTORY_BIN = os.environ.get("OVERSTORY_BIN", os.path.expanduser("~/.bun/bin/overstory"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral:latest")
PORT = int(os.environ.get("OVERCLAW_PORT", "18800"))

log = logging.getLogger("overclaw-gateway")

BOOT_TIME = time.time()

sys.path.insert(0, str(WORKSPACE / "skills" / "nanobot-overstory-bridge" / "scripts"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ollama_chat(messages: list[dict], model: str | None = None, stream: bool = False) -> dict:
    """Send a chat completion request to Ollama."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json={
            "model": model or OLLAMA_MODEL,
            "messages": messages,
            "stream": stream,
        })
        resp.raise_for_status()
        return resp.json()


async def _run_command(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> dict:
    """Run a shell command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd or WORKSPACE),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode().strip(),
            "stderr": stderr.decode().strip(),
        }
    except asyncio.TimeoutError:
        proc.kill()
        return {"returncode": -1, "error": f"Command timed out after {timeout}s"}


async def _overstory_run(args: list[str], timeout: int = 60) -> dict:
    """Run an overstory CLI command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        OVERSTORY_BIN, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": f"overstory timed out after {timeout}s"}

    out = stdout.decode().strip()
    if proc.returncode != 0:
        return {"error": stderr.decode().strip() or out, "exit_code": proc.returncode}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


def _discover_skills() -> list[dict]:
    """Import and use gateway_tools discovery."""
    try:
        from gateway_tools import discover_skills
        return discover_skills(SKILLS_DIR)
    except ImportError:
        return [{"error": "gateway_tools not importable"}]


def _discover_tools() -> list[dict]:
    try:
        from gateway_tools import discover_tools
        return discover_tools(SKILLS_DIR)
    except ImportError:
        return [{"error": "gateway_tools not importable"}]


# ---------------------------------------------------------------------------
# Route: Health
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "service": "overclaw-gateway",
        "port": PORT,
        "uptime_s": round(time.time() - BOOT_TIME, 1),
        "timestamp": _now_iso(),
    })


# ---------------------------------------------------------------------------
# Route: Status (full stack)
# ---------------------------------------------------------------------------

async def status(request: Request) -> JSONResponse:
    result: dict[str, Any] = {
        "timestamp": _now_iso(),
        "gateway": {"status": "ok", "port": PORT, "uptime_s": round(time.time() - BOOT_TIME, 1)},
    }

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            data = resp.json()
            models = [m.get("name", "?") for m in data.get("models", [])]
            result["ollama"] = {"status": "ok", "url": OLLAMA_URL, "models": models}
    except Exception as exc:
        result["ollama"] = {"status": "unreachable", "error": str(exc)}

    # Overstory
    try:
        ov = await _overstory_run(["status"], timeout=10)
        result["overstory"] = {"status": "ok", "data": ov}
    except Exception as exc:
        result["overstory"] = {"status": "error", "error": str(exc)}

    # Nanobot agent (check if process alive)
    try:
        pid_file = WORKSPACE / ".overstory" / "logs" / "nanobot-agent.pid"
        if pid_file.is_file():
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            result["nanobot_agent"] = {"status": "running", "pid": pid}
        else:
            result["nanobot_agent"] = {"status": "no_pid_file"}
    except ProcessLookupError:
        result["nanobot_agent"] = {"status": "dead", "note": "PID in file but process gone"}
    except Exception as exc:
        result["nanobot_agent"] = {"status": "unknown", "error": str(exc)}

    result["workspace"] = str(WORKSPACE)
    result["skills_count"] = len(_discover_skills())
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Route: Chat (Ollama Mistral orchestrator)
# ---------------------------------------------------------------------------

async def chat(request: Request) -> JSONResponse:
    """Chat with the Ollama Mistral orchestrator.
    
    POST /api/chat
    {
      "message": "research AI trends",
      "system": "optional system prompt",
      "model": "optional model override",
      "history": [{"role": "user", "content": "..."}]
    }
    """
    body = await request.json()
    message = body.get("message", "")
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    messages: list[dict] = []

    system_prompt = body.get("system", (
        "You are the OverClaw orchestrator. You route tasks to specialized agents "
        "via overstory. Available capabilities: researcher, social-media-manager, "
        "blogger, scribe, builder, scout, reviewer. Analyze the user's request and "
        "decide which agent(s) should handle it. Reply with your analysis and the "
        "action to take."
    ))
    messages.append({"role": "system", "content": system_prompt})

    for msg in body.get("history", []):
        messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    messages.append({"role": "user", "content": message})

    try:
        result = await _ollama_chat(messages, model=body.get("model"))
        return JSONResponse({
            "response": result.get("message", {}).get("content", ""),
            "model": result.get("model", OLLAMA_MODEL),
            "done": result.get("done", True),
            "total_duration_ms": round(result.get("total_duration", 0) / 1e6, 1),
        })
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": f"Ollama error: {exc.response.status_code}"}, status_code=502)
    except Exception as exc:
        return JSONResponse({"error": f"Chat failed: {exc}"}, status_code=500)


# ---------------------------------------------------------------------------
# Route: Route (classify + optionally spawn)
# ---------------------------------------------------------------------------

async def route_task(request: Request) -> JSONResponse:
    """Route a task through the task router, optionally spawning an agent.
    
    POST /api/route
    {"task": "...", "spawn": false, "context": {}}
    """
    body = await request.json()
    task = body.get("task", "")
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)

    try:
        from task_router import TaskRouter
        from overstory_client import OverstoryClient

        client = OverstoryClient(binary=OVERSTORY_BIN) if body.get("spawn") else None
        router = TaskRouter(overstory_client=client)
        result = router.route_task(task, context=body.get("context"))
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Route: Agents
# ---------------------------------------------------------------------------

async def agents_list(request: Request) -> JSONResponse:
    """GET /api/agents — list overstory agents."""
    result = await _overstory_run(["status"])
    return JSONResponse(result)


async def agents_spawn(request: Request) -> JSONResponse:
    """POST /api/agents/spawn — spawn a new overstory agent.
    
    {"task": "...", "capability": "builder", "name": "optional", "force": false}

    overstory hierarchy: coordinator -> lead -> workers (builder/scout/reviewer).
    Use capability="lead" for top-level spawns, or force=true to bypass.
    A task-id is auto-generated (overstory requires one for Beads tracking).
    """
    body = await request.json()
    task = body.get("task", "")
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)

    capability = body.get("capability")
    if not capability:
        from task_router import TaskRouter
        router = TaskRouter()
        cap_info = router.determine_capability(task)
        capability = cap_info["capability"]

    name = body.get("name", f"{capability}-{uuid.uuid4().hex[:6]}")
    task_id = body.get("task_id", f"oc-{uuid.uuid4().hex[:8]}")

    # Create Bead task or spec file before spawning
    spec_path = None
    bead_created = False
    
    # Check if bd CLI is available
    bd_available = False
    try:
        bd_check = await _run_command(["which", "bd"], timeout=2)
        bd_available = bd_check.get("returncode") == 0
    except Exception:
        pass
    
    # Check if beads is enabled in config
    beads_enabled = False
    try:
        if yaml:
            config_path = WORKSPACE / ".overstory" / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    beads_enabled = config.get("beads", {}).get("enabled", False)
    except Exception:
        pass  # Default to disabled if we can't read config
    
    # Only use beads if enabled AND bd CLI is available
    use_beads = beads_enabled and bd_available
    
    bead_id = None
    if use_beads:
        try:
            # Create Bead task using bd CLI (bd auto-generates the ID)
            bd_create_result = await _run_command([
                "bd", "create", task,
                "--priority", "1",
                "--type", "task",
                "--json"
            ], timeout=10)
            
            if bd_create_result.get("returncode") == 0:
                try:
                    # Parse the JSON response to get the Bead ID
                    bd_output = bd_create_result.get("stdout", "").strip()
                    # bd outputs JSON to stdout (warnings go to stderr)
                    # JSON may be multi-line, so parse the entire stdout
                    bd_json = json.loads(bd_output)
                    bead_id = bd_json.get("id")
                    if bead_id:
                        bead_created = True
                        log.info("Created Bead task %s via bd CLI", bead_id)
                        # Use the Bead ID as the task_id for sling
                        task_id = bead_id
                    else:
                        log.warning("bd create succeeded but no 'id' in response: %s", bd_json)
                except json.JSONDecodeError as exc:
                    log.warning("Failed to parse bd create JSON: %s. Output: %s", exc, bd_output[:500])
                except (KeyError, IndexError) as exc:
                    log.warning("Failed to extract Bead ID: %s", exc)
        except FileNotFoundError:
            log.warning("bd CLI not found despite check")
        except Exception as exc:
            log.warning("Failed to create Bead task: %s", exc)
    
    # Always create spec file as well (for --spec flag)
    spec_dir = WORKSPACE / ".overstory" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"{task_id}.md"
    spec_path.write_text(f"# Task: {task}\n\n{task}\n")
    
    if not bead_created:
        log.info("Created spec file for task %s (beads disabled or unavailable)", task_id)

    # Use bead_id if we successfully created a Bead task, otherwise use original task_id
    sling_task_id = bead_id if bead_created and bead_id else task_id
    
    args = [
        "sling", sling_task_id,
        "--capability", capability,
        "--name", name,
    ]
    if body.get("parent"):
        args.extend(["--parent", body["parent"]])
    if body.get("force", False):
        args.append("--force-hierarchy")
    
    # If we created a spec file, add --spec flag (use absolute path)
    if spec_path and spec_path.exists():
        args.extend(["--spec", str(spec_path)])

    result = await _overstory_run(args, timeout=120)
    result["agent_name"] = name
    result["capability"] = capability
    result["task_id"] = body.get("task_id", task_id)  # Return original task_id from request if provided
    result["sling_task_id"] = sling_task_id
    if bead_created and bead_id:
        result["bead_id"] = bead_id
        result["bead_created"] = True
    if spec_path:
        result["spec_file"] = str(spec_path.relative_to(WORKSPACE))
    return JSONResponse(result)


async def agents_inspect(request: Request) -> JSONResponse:
    """GET /api/agents/{name} — inspect a specific agent."""
    name = request.path_params["name"]
    result = await _overstory_run(["inspect", "--agent", name])
    return JSONResponse(result)


async def agents_mail(request: Request) -> JSONResponse:
    """POST /api/agents/mail — send inter-agent mail.
    
    {"from": "orchestrator", "to": "builder-abc123", "message": "..."}
    """
    body = await request.json()
    result = await _overstory_run([
        "mail", "send",
        "--from", body.get("from", "orchestrator"),
        "--to", body.get("to", ""),
        "--message", body.get("message", ""),
        "--priority", body.get("priority", "normal"),
    ])
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Route: Skills & Tools
# ---------------------------------------------------------------------------

async def skills_list(request: Request) -> JSONResponse:
    """GET /api/skills — list all available skills."""
    return JSONResponse({"skills": _discover_skills()})


async def tools_list(request: Request) -> JSONResponse:
    """GET /api/tools — list all available tools."""
    return JSONResponse({"tools": _discover_tools()})


async def skills_exec(request: Request) -> JSONResponse:
    """POST /api/skills/exec — execute a skill script.
    
    {"skill": "playwright-commander", "script": "playwright_cli.py", "args": "--help"}
    """
    body = await request.json()
    skill = body.get("skill", "")
    if not skill:
        return JSONResponse({"error": "skill is required"}, status_code=400)

    try:
        from gateway_tools import exec_skill
        result = exec_skill(skill, body.get("script"), body.get("args", ""))
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def tools_exec(request: Request) -> JSONResponse:
    """POST /api/tools/exec — execute a tool by name.
    
    {"tool": "playwright_navigate", "params": {"url": "https://..."}}
    """
    body = await request.json()
    tool = body.get("tool", "")
    if not tool:
        return JSONResponse({"error": "tool is required"}, status_code=400)

    try:
        from gateway_tools import exec_tool
        result = exec_tool(tool, body.get("params", {}))
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Route: Memory
# ---------------------------------------------------------------------------

async def memory_read(request: Request) -> JSONResponse:
    """GET /api/memory?section=... — read MEMORY.md."""
    section = request.query_params.get("section")
    try:
        from gateway_tools import memory_read as _mr
        return JSONResponse(_mr(section))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def memory_write(request: Request) -> JSONResponse:
    """POST /api/memory — write to MEMORY.md.
    
    {"section": "Events", "content": "OverClaw gateway started"}
    """
    body = await request.json()
    section = body.get("section", "")
    content = body.get("content", "")
    if not section or not content:
        return JSONResponse({"error": "section and content required"}, status_code=400)

    try:
        from gateway_tools import memory_write as _mw
        return JSONResponse(_mw(section, content))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

routes = [
    Route("/health", health, methods=["GET"]),
    Route("/api/status", status, methods=["GET"]),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/route", route_task, methods=["POST"]),
    Route("/api/agents", agents_list, methods=["GET"]),
    Route("/api/agents/spawn", agents_spawn, methods=["POST"]),
    Route("/api/agents/mail", agents_mail, methods=["POST"]),
    Route("/api/agents/{name}", agents_inspect, methods=["GET"]),
    Route("/api/skills", skills_list, methods=["GET"]),
    Route("/api/skills/exec", skills_exec, methods=["POST"]),
    Route("/api/tools", tools_list, methods=["GET"]),
    Route("/api/tools/exec", tools_exec, methods=["POST"]),
    Route("/api/memory", memory_read, methods=["GET"]),
    Route("/api/memory", memory_write, methods=["POST"]),
]

app = Starlette(routes=routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("OverClaw Gateway starting on port %d", PORT)
    log.info("  Workspace: %s", WORKSPACE)
    log.info("  Ollama:    %s (model: %s)", OLLAMA_URL, OLLAMA_MODEL)
    log.info("  Overstory: %s", OVERSTORY_BIN)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
