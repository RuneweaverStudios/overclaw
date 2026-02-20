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
import contextlib
import json
import sqlite3
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
SETTINGS_DANGEROUSLY_SKIP_FILE = WORKSPACE / ".overstory" / "dangerously-skip-permissions"
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


async def _overstory_run(args: list[str], timeout: int = 60, cwd: Path | None = None) -> dict:
    """Run an overstory CLI command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        OVERSTORY_BIN, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
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


async def _create_bead_and_spec(task: str, task_id: str | None = None) -> dict:
    """Create Bead task (if enabled) and spec file. Returns task_id, sling_task_id, spec_path, bead_created."""
    task_id = task_id or f"oc-{uuid.uuid4().hex[:8]}"
    spec_dir = WORKSPACE / ".overstory" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"{task_id}.md"
    spec_path.write_text(f"# Task: {task}\n\n{task}\n")
    bead_created = False
    sling_task_id = task_id
    bead_id = None

    bd_bin = None
    try:
        bd_check = await _run_command(["which", "bd"], timeout=2, cwd=WORKSPACE)
        if bd_check.get("returncode") == 0 and bd_check.get("stdout", "").strip():
            bd_bin = bd_check.get("stdout", "").strip()
        if not bd_bin:
            for path in ("/opt/homebrew/bin/bd", "/usr/local/bin/bd"):
                if Path(path).exists():
                    bd_bin = path
                    break
    except Exception:
        pass
    bd_available = bd_bin is not None
    beads_enabled = False
    try:
        if yaml:
            config_path = WORKSPACE / ".overstory" / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    beads_enabled = config.get("beads", {}).get("enabled", False)
    except Exception:
        pass
    if beads_enabled and bd_available:
        try:
            bd_create_result = await _run_command([
                str(bd_bin), "create", task,
                "--priority", "1",
                "--type", "task",
                "--json",
            ], timeout=10, cwd=WORKSPACE)
            if bd_create_result.get("returncode") == 0:
                bd_output = bd_create_result.get("stdout", "").strip()
                # bd may print a warning before the JSON; extract first {...} object
                start = bd_output.find("{")
                if start >= 0:
                    depth = 0
                    end = -1
                    for i in range(start, len(bd_output)):
                        if bd_output[i] == "{":
                            depth += 1
                        elif bd_output[i] == "}":
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end > start:
                        try:
                            bd_json = json.loads(bd_output[start:end])
                            bead_id = bd_json.get("id")
                            if bead_id:
                                bead_created = True
                                sling_task_id = bead_id
                                log.info("Created Bead task %s via bd CLI", bead_id)
                        except (json.JSONDecodeError, KeyError):
                            pass
                if not bead_created and bd_output:
                    log.warning("Bead create produced no id. stderr: %s", bd_create_result.get("stderr", "")[:200])
        except Exception as exc:
            log.warning("Bead create failed: %s", exc)

    return {
        "task_id": task_id,
        "sling_task_id": sling_task_id,
        "spec_path": spec_path,
        "bead_created": bead_created,
        "bead_id": bead_id,
    }


def _tmux_session_from_sling_result(sling_result: dict) -> str | None:
    """Extract tmux session name from sling result (JSON or raw text)."""
    if sling_result.get("tmuxSession"):
        return sling_result["tmuxSession"]
    raw = sling_result.get("raw") or ""
    # "Tmux: overstory-overclaw-lead-xxx" or "Tmux: overstory-overclaw-builder-xxx"
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("Tmux:") or "Tmux:" in line:
            parts = line.split("Tmux:", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


def _build_beacon(agent_name: str, capability: str, task_id: str, parent: str | None = None, depth: int = 0) -> str:
    """Build Overstory-style startup beacon (single line). Sent after disclaimer so the agent receives the task."""
    ts = datetime.now(timezone.utc).isoformat()
    parent_str = parent or "none"
    parts = [
        f"[OVERSTORY] {agent_name} ({capability}) {ts} task:{task_id}",
        f"Depth: {depth} | Parent: {parent_str}",
        f"Startup: read .claude/CLAUDE.md, run mulch prime, check mail (overstory mail check --agent {agent_name}), then begin task {task_id}",
    ]
    return " — ".join(parts)


async def _get_agent_info_for_session(session_name: str) -> dict | None:
    """Get agent_name, capability, task_id, parent for a tmux session from overstory status --json."""
    try:
        status = await _overstory_run(["status", "--json"], timeout=8, cwd=WORKSPACE)
        if status.get("error"):
            return None
        agents = status.get("agents") or []
        for a in agents:
            name = a.get("agentName") or a.get("name")
            if not name:
                continue
            tmux = a.get("tmuxSession") or f"overstory-overclaw-{name}"
            if tmux == session_name or session_name.endswith(name):
                task_id = a.get("taskId") or a.get("beadId") or a.get("task_id") or name
                return {
                    "agent_name": name,
                    "capability": a.get("capability") or (name.split("-")[0] if "-" in name else "agent"),
                    "task_id": task_id,
                    "parent": a.get("parent"),
                }
    except Exception as e:
        log.debug("_get_agent_info_for_session: %s", e)
    return None


async def _send_beacon_after_disclaimer(session_name: str) -> bool:
    """After disclaimer was accepted, send the startup beacon so the agent is prompted to do its assigned work."""
    info = await _get_agent_info_for_session(session_name)
    if not info:
        # Fallback: derive from session name (e.g. overstory-overclaw-lead-abc123 -> lead-abc123, task_id=lead-abc123)
        short = session_name.replace("overstory-overclaw-", "") if "overstory-overclaw-" in session_name else session_name
        info = {"agent_name": short, "capability": short.split("-")[0] if "-" in short else "agent", "task_id": short, "parent": None}
    await asyncio.sleep(1)  # Let TUI be ready for input
    beacon = _build_beacon(
        info["agent_name"],
        info["capability"],
        info["task_id"],
        parent=info.get("parent"),
    )
    out = await _send_tmux_keys(session_name, beacon, delay_after_s=0)
    if out.get("error"):
        log.debug("Send beacon after disclaimer failed for %s: %s", session_name, out["error"])
        return False
    await _send_tmux_keys(session_name, "", delay_after_s=0)  # Submit
    log.info("Sent startup beacon to %s (task %s)", session_name, info.get("task_id"))
    return True


async def _send_tmux_keys(session_name: str, keys: str, delay_after_s: float = 0.5) -> dict:
    """Send keys to a tmux session (e.g. to accept Claude disclaimer). Appends Enter."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", session_name, keys, "Enter",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORKSPACE),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": "tmux send-keys timed out"}
    err = (stderr or b"").decode().strip() if stderr else ""
    if proc.returncode != 0:
        return {"error": err or f"tmux send-keys exited {proc.returncode}"}
    if delay_after_s > 0:
        await asyncio.sleep(delay_after_s)
    return {"ok": True}


async def _send_tmux_keys_only(session_name: str, *keys: str) -> dict:
    """Send key(s) to tmux without appending Enter (e.g. C-c for Ctrl+C)."""
    if not keys:
        return {"ok": True}
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", session_name, *keys,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORKSPACE),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": "tmux send-keys timed out"}
    err = (stderr or b"").decode().strip() if stderr else ""
    if proc.returncode != 0:
        return {"error": err or f"tmux send-keys exited {proc.returncode}"}
    return {"ok": True}


async def _sling_agent(
    sling_task_id: str,
    capability: str,
    name: str,
    spec_path: Path,
    parent: str | None = None,
    force: bool = False,
    timeout: int = 120,
) -> dict:
    """Run overstory sling with the given params. Uses --spec so overstory does not require Bead."""
    args = [
        "sling", sling_task_id,
        "--capability", capability,
        "--name", name,
        "--json",
    ]
    if parent:
        args.extend(["--parent", parent])
    if force:
        args.append("--force-hierarchy")
    if spec_path.exists():
        try:
            spec_arg = str(spec_path.relative_to(WORKSPACE))
        except ValueError:
            spec_arg = str(spec_path)
        args.extend(["--spec", spec_arg])
    result = await _overstory_run(args, timeout=timeout, cwd=WORKSPACE)
    if result.get("error"):
        return result
    # Overstory sends its beacon at 3s while the agent is still on the disclaimer, so it's lost.
    # We accept the disclaimer first (select option 2 "Yes, I accept"), then send the beacon ourselves.
    session_name = _tmux_session_from_sling_result(result)
    if session_name:
        await asyncio.sleep(2)  # Let disclaimer appear
        # Down to select 2. Yes, I accept; do NOT send Enter with Down (would confirm option 1).
        await _send_tmux_keys_only(session_name, "Down")
        await asyncio.sleep(0.25)
        await _send_tmux_keys(session_name, "", delay_after_s=0)
        await asyncio.sleep(1)  # Let TUI be ready for the beacon
        task_id = result.get("taskId") or result.get("beadId") or sling_task_id
        agent_name = result.get("agentName") or name
        cap = result.get("capability") or capability
        beacon = _build_beacon(agent_name, cap, task_id, parent=parent, depth=0)
        await _send_tmux_keys(session_name, beacon, delay_after_s=0)
        await _send_tmux_keys(session_name, "", delay_after_s=0)  # Submit the beacon
    return result


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
# Mistral analyzer: can complete? direct answer / follow-up questions / handoff
# ---------------------------------------------------------------------------

_ANALYZER_SYSTEM = """You are a task analyzer. For the user's message you must decide:
1. Can you complete the task yourself? (simple Q&A, greetings, factual one-shot answers, no code execution, no multi-step specialist work like research/code/build/campaigns)
2. If yes: reply with a single JSON object only, no other text: {"can_complete": true, "direct_answer": "your full reply here", "follow_up_questions": null, "articulated_task": null, "handoff_xml": null}
3. If no: you need to hand off to specialist agents. Either ask for more context (max 2 questions) OR produce the handoff.
   - If you need more context: {"can_complete": false, "direct_answer": null, "follow_up_questions": ["question 1?", "question 2?"], "articulated_task": null, "handoff_xml": null}
   - If you have enough context (or follow_up_answers were provided): set articulated_task (one clear sentence for the specialist) and handoff_xml (robust XML for the orchestrator). Example:
     {"can_complete": false, "direct_answer": null, "follow_up_questions": null, "articulated_task": "Research recent AI trends and summarize in 3 bullets", "handoff_xml": "<handoff><intent>research</intent><task>Research recent AI trends and summarize in 3 bullets</task><context>User asked for AI trends summary.</context></handoff>"}
Output only one valid JSON object, no markdown, no explanation."""

_ARTICULATE_SYSTEM = """You have the user's original request and their follow-up answers. Produce a single JSON object only:
{"articulated_task": "one clear sentence for the specialist agent", "handoff_xml": "<handoff><intent>...</intent><task>...</task><context>...</context></handoff>"}
The articulated_task should be the exact task to send to the orchestrator. The handoff_xml should be robust XML with intent, task, and context. Output only that JSON, no other text."""


def _extract_json_from_response(text: str) -> dict:
    """Extract a JSON object from Ollama response (may be wrapped in markdown or have trailing text)."""
    text = (text or "").strip()
    # Try raw parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown code block
    if "```" in text:
        start = text.find("```")
        if "json" in text[:start + 10].lower():
            start = text.find("\n", start) + 1
        else:
            start = start + 3
        end = text.find("```", start)
        if end != -1:
            text = text[start:end]
    # Find first { and last }
    i = text.find("{")
    j = text.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(text[i : j + 1])
        except json.JSONDecodeError:
            pass
    return {}


async def _ollama_analyze(
    message: str,
    history: list[dict],
    follow_up_answers: list[str] | None = None,
) -> dict:
    """Call Ollama to analyze the task. Returns dict with can_complete, direct_answer, follow_up_questions, articulated_task, handoff_xml."""
    messages: list[dict] = [{"role": "system", "content": _ANALYZER_SYSTEM}]
    for h in history[-10:]:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    user_content = message
    if follow_up_answers:
        user_content += "\n\n[Follow-up answers from user]\n" + "\n".join(f"- " + a for a in follow_up_answers)
    messages.append({"role": "user", "content": user_content})

    result = await _ollama_chat(messages)
    raw = (result.get("message") or {}).get("content", "")
    out = _extract_json_from_response(raw)
    return {
        "can_complete": bool(out.get("can_complete")),
        "direct_answer": out.get("direct_answer"),
        "follow_up_questions": out.get("follow_up_questions") if isinstance(out.get("follow_up_questions"), list) else None,
        "articulated_task": out.get("articulated_task"),
        "handoff_xml": out.get("handoff_xml"),
    }


async def _ollama_articulate(message: str, follow_up_answers: list[str]) -> dict:
    """Build articulated_task and handoff_xml from original message + follow-up answers."""
    messages = [
        {"role": "system", "content": _ARTICULATE_SYSTEM},
        {"role": "user", "content": f"Original request: {message}\n\nFollow-up answers:\n" + "\n".join(f"- {a}" for a in follow_up_answers)},
    ]
    result = await _ollama_chat(messages)
    raw = (result.get("message") or {}).get("content", "")
    return _extract_json_from_response(raw)


# ---------------------------------------------------------------------------
# Route: Message (analyze → direct answer / follow-up / handoff to route)
# ---------------------------------------------------------------------------

async def message(request: Request) -> JSONResponse:
    """Single entry: Ollama analyzes task. If it can complete, answer directly. If not and route_to_agents: ask up to 2 follow-ups, then articulate and hand off to route (spawn).
    
    POST /api/message
    {
      "message": "user text",
      "history": [{"role":"user"|"assistant","content":"..."}],
      "route_to_agents": true,
      "follow_up_answers": ["ans1", "ans2"]  // optional, when returning from need_follow_up
    }
    """
    body = await request.json()
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    route_to_agents = body.get("route_to_agents", False)
    history = body.get("history") or []
    follow_up_answers = body.get("follow_up_answers")
    if follow_up_answers is not None and not isinstance(follow_up_answers, list):
        follow_up_answers = [str(follow_up_answers)] if follow_up_answers else None

    try:
        analysis = await _ollama_analyze(user_message, history, follow_up_answers)
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": f"Ollama error: {exc.response.status_code}"}, status_code=502)
    except Exception as exc:
        log.exception("Analyzer failed")
        return JSONResponse({"error": f"Analyzer failed: {exc}"}, status_code=500)

    can_complete = analysis.get("can_complete")
    direct_answer = analysis.get("direct_answer")
    follow_up_questions = analysis.get("follow_up_questions")
    articulated_task = analysis.get("articulated_task")
    handoff_xml = analysis.get("handoff_xml")

    # Mistral can complete → return direct answer (respects route_to_agents: user may still have wanted to route, but we answer directly)
    if can_complete and direct_answer:
        return JSONResponse({
            "response": direct_answer,
            "model": OLLAMA_MODEL,
            "source": "ollama_direct",
        })

    # Cannot complete and Route to agents is OFF → Ollama replies directly with best effort or suggestion
    if not route_to_agents:
        fallback = direct_answer or "This needs specialist agents (research, code, build, etc.). Turn on \"Route to agents\" to hand off to the orchestrator."
        return JSONResponse({
            "response": fallback,
            "model": OLLAMA_MODEL,
            "source": "ollama_fallback",
        })

    # Route to agents ON, cannot complete: need follow-up or handoff
    if follow_up_questions and not follow_up_answers:
        # Cap at 2 questions
        questions = follow_up_questions[:2]
        return JSONResponse({
            "need_follow_up": True,
            "questions": questions,
            "original_message": user_message,
        })

    # Build articulated task if we have follow-up answers and analysis didn't provide it
    if follow_up_answers and (not articulated_task or not handoff_xml):
        articulate = await _ollama_articulate(user_message, follow_up_answers)
        articulated_task = articulated_task or articulate.get("articulated_task") or user_message
        handoff_xml = handoff_xml or articulate.get("handoff_xml") or ""

    task_for_route = (articulated_task or user_message).strip()
    context = body.get("context") or {}
    if handoff_xml:
        context["handoff_xml"] = handoff_xml

    try:
        result = await _do_route_task(task_for_route, spawn=True, context=context)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def _do_route_task(task: str, spawn: bool, context: dict | None = None) -> dict:
    """Internal: run task router and optionally spawn. Returns result dict (not JSONResponse)."""
    from task_router import TaskRouter

    router = TaskRouter(overstory_client=None)
    result = router.route_task(task, context=context or {})

    if not spawn:
        return result

    worker_caps = {"builder", "scout", "reviewer"}
    task_id = result.get("task_id", f"oc-{uuid.uuid4().hex[:8]}")
    created = await _create_bead_and_spec(task, task_id=task_id)
    sling_task_id = created["sling_task_id"]
    spec_path = created["spec_path"]
    await asyncio.sleep(2)

    try:
        if result.get("capability") in worker_caps:
            lead_name = f"lead-{task_id[:8]}"
            sling_result = await _sling_agent(
                sling_task_id, "lead", lead_name, spec_path, timeout=120
            )
            if sling_result.get("error"):
                result["spawn_error"] = sling_result.get("error")
                result["spawned"] = False
                return result
            await asyncio.sleep(2)
            sling_result = await _sling_agent(
                sling_task_id,
                result["capability"],
                result["name"],
                spec_path,
                parent=lead_name,
                timeout=120,
            )
        else:
            sling_result = await _sling_agent(
                sling_task_id,
                result["capability"],
                result["name"],
                spec_path,
                timeout=120,
            )
        if sling_result.get("error"):
            result["spawn_error"] = sling_result.get("error")
            result["spawned"] = False
        else:
            result["spawn_result"] = sling_result
            result["spawned"] = True
            agent_name = result.get("name") or f"{result.get('capability', 'agent')}-{task_id[:8]}"
            await _send_mail_to_agent("orchestrator", agent_name, task, "normal")
    except Exception as exc:
        result["spawn_error"] = str(exc)
        result["spawned"] = False
    return result


# ---------------------------------------------------------------------------
# Route: Route (classify + optionally spawn)
# ---------------------------------------------------------------------------

async def route_task(request: Request) -> JSONResponse:
    """Route a task through the task router, optionally spawning an agent.
    
    POST /api/route
    {"task": "...", "spawn": false, "context": {}}
    
    When spawn=true, the gateway creates Bead/spec and runs sling (lead first for workers)
    so overstory gets a valid task id/spec instead of the bridge's raw task_id.
    """
    body = await request.json()
    task = body.get("task", "")
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)

    try:
        result = await _do_route_task(
            task,
            spawn=body.get("spawn", False),
            context=body.get("context"),
        )
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

    created = await _create_bead_and_spec(task, task_id=task_id)
    sling_task_id = created["sling_task_id"]
    spec_path = created["spec_path"]
    # Delay after DB operations (bead/spec creation) before spawning agent (reduce DB lock contention)
    await asyncio.sleep(2)

    result = await _sling_agent(
        sling_task_id,
        capability,
        name,
        spec_path,
        parent=body.get("parent"),
        force=body.get("force", False),
        timeout=120,
    )
    result["agent_name"] = name
    result["capability"] = capability
    result["task_id"] = body.get("task_id", task_id)
    result["sling_task_id"] = sling_task_id
    if created.get("bead_created") and created.get("bead_id"):
        result["bead_id"] = created["bead_id"]
        result["bead_created"] = True
    if spec_path:
        result["spec_file"] = str(spec_path.relative_to(WORKSPACE))
    # So "check mail" has something to show: send task to the new agent
    if not result.get("error"):
        await _send_mail_to_agent("orchestrator", name, task, "normal")
    return JSONResponse(result)


def _tmux_session_for_agent(agents: list, agent_name: str) -> str | None:
    """Get tmux session name for an agent from overstory status agents list."""
    for a in agents or []:
        if (a.get("agentName") or a.get("name")) == agent_name:
            return a.get("tmuxSession")
    return None


async def _get_agent_session_names() -> list[str]:
    """Get list of tmux session names for agents. Uses overstory status, or tmux list-sessions if DB locked."""
    try:
        status = await _overstory_run(["status", "--json"], timeout=8, cwd=WORKSPACE)
        if status.get("error"):
            raise RuntimeError(status.get("error", "unknown"))
        agents = status.get("agents") or []
        names = []
        for a in agents:
            name = a.get("agentName") or a.get("name")
            if not name:
                continue
            session = _tmux_session_for_agent(agents, name) or f"overstory-overclaw-{name}"
            names.append(session)
        return names
    except Exception:
        # Fallback: list tmux sessions matching overstory-overclaw-*
        result = await _run_command(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            timeout=5,
            cwd=WORKSPACE,
        )
        if result.get("returncode") != 0:
            return []
        out = (result.get("stdout") or "").strip()
        return [s.strip() for s in out.splitlines() if s.strip().startswith("overstory-overclaw-")]


def _output_has_confirm_prompt(text: str) -> bool:
    """True if terminal output shows a Claude/CLI confirm prompt (Do you want to ... 1. Yes / 2. ... / 3. No)."""
    if not text or "Do you want to" not in text:
        return False
    tail = text[-2000:] if len(text) > 2000 else text
    # Must look like a menu: has option 1 (Yes) and at least one other option (2 or 3)
    has_yes = "1. Yes" in tail or "1. Yes," in tail or "❯ 1." in tail
    has_other = "2." in tail or "3. No" in tail
    return bool(has_yes and has_other)


def _output_has_bypass_disclaimer(text: str) -> bool:
    """True if terminal shows Bypass Permissions disclaimer (1. No, exit / 2. Yes, I accept)."""
    if not text:
        return False
    tail = text[-3000:] if len(text) > 3000 else text
    return "Bypass Permissions mode" in tail and "Yes, I accept" in tail and ("1. No" in tail or "2. Yes" in tail)


async def _capture_tmux_pane(session_name: str, lines: int = 80) -> str:
    """Capture last N lines from tmux pane. Returns '' if tmux fails."""
    result = await _run_command(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", f"-{lines}"],
        timeout=5,
        cwd=WORKSPACE,
    )
    if result.get("returncode") == 0:
        return result.get("stdout", "")
    return ""


DISCLAIMER_WATCHER_INTERVAL_S = 3.0  # Poll tmux every 3s so disclaimers are accepted as soon as they appear


async def _run_disclaimer_accept_once() -> int:
    """Check all agent tmux panes for Bypass disclaimer; send Down then Enter to each. Returns count accepted."""
    sessions = await _get_agent_session_names()
    accepted = 0
    for session_name in sessions:
        try:
            output = await _capture_tmux_pane(session_name, lines=120)
            if not _output_has_bypass_disclaimer(output):
                continue
            # Down to select "2. Yes, I accept"; short delay so TUI updates; then Enter
            out = await _send_tmux_keys_only(session_name, "Down")
            if out.get("error"):
                continue
            await asyncio.sleep(0.25)
            out = await _send_tmux_keys(session_name, "", delay_after_s=0)
            if not out.get("error"):
                accepted += 1
                log.info("Disclaimer watcher: accepted for %s", session_name)
                await _send_beacon_after_disclaimer(session_name)
        except Exception as e:
            log.debug("Disclaimer watcher: %s for %s: %s", type(e).__name__, session_name, e)
    return accepted


async def _disclaimer_watcher_loop() -> None:
    """Background loop: check all agent tmux panes and accept Bypass disclaimer every N seconds. First run immediately."""
    log.info("Disclaimer watcher started (interval %.1fs)", DISCLAIMER_WATCHER_INTERVAL_S)
    while True:
        try:
            n = await _run_disclaimer_accept_once()
            if n > 0:
                log.info("Disclaimer watcher: accepted %d agent(s)", n)
            await asyncio.sleep(DISCLAIMER_WATCHER_INTERVAL_S)
        except asyncio.CancelledError:
            log.info("Disclaimer watcher stopped")
            break
        except Exception as e:
            log.debug("Disclaimer watcher tick: %s", e)
            await asyncio.sleep(DISCLAIMER_WATCHER_INTERVAL_S)


async def agents_auto_accept_prompts(request: Request) -> JSONResponse:
    """POST /api/agents/auto-accept-prompts — for each agent: if Bypass disclaimer → Down+Enter; if generic confirm → Enter."""
    try:
        sessions = await _get_agent_session_names()
        # Map session name back to short name for response (session is overstory-overclaw-NAME)
        accepted = []
        for session_name in sessions:
            try:
                output = await _capture_tmux_pane(session_name, lines=120)
                if _output_has_bypass_disclaimer(output):
                    out = await _send_tmux_keys_only(session_name, "Down")
                    if not out.get("error"):
                        await asyncio.sleep(0.25)
                        out = await _send_tmux_keys(session_name, "", delay_after_s=0)
                    if not out.get("error"):
                        short = session_name.replace("overstory-overclaw-", "") if "overstory-overclaw-" in session_name else session_name
                        accepted.append(short)
                        log.info("Auto-accepted Bypass disclaimer for %s", session_name)
                        await _send_beacon_after_disclaimer(session_name)
                    continue
                if not _output_has_confirm_prompt(output):
                    continue
                out = await _send_tmux_keys(session_name, "", delay_after_s=0)
                if not out.get("error"):
                    short = session_name.replace("overstory-overclaw-", "") if "overstory-overclaw-" in session_name else session_name
                    accepted.append(short)
                    log.info("Auto-accepted confirm prompt for %s", session_name)
            except Exception:
                pass
        return JSONResponse({"ok": True, "accepted": accepted})
    except Exception as e:
        log.exception("agents_auto_accept_prompts failed")
        return JSONResponse({"ok": False, "error": str(e), "accepted": []})


async def agents_accept_disclaimer(request: Request) -> JSONResponse:
    """POST /api/agents/{name}/accept-disclaimer — Down then Enter to select 'Yes, I accept' on Bypass Permissions disclaimer."""
    name = request.path_params["name"]
    sessions = await _get_agent_session_names()
    session_name = next((s for s in sessions if s.endswith(name) or s == f"overstory-overclaw-{name}"), f"overstory-overclaw-{name}")
    out = await _send_tmux_keys_only(session_name, "Down")
    if out.get("error"):
        return JSONResponse({"ok": False, "agent": name, "error": out["error"], "session_used": session_name}, status_code=500)
    await asyncio.sleep(0.25)
    out = await _send_tmux_keys(session_name, "", delay_after_s=0)
    if out.get("error"):
        return JSONResponse({"ok": False, "agent": name, "error": out["error"], "session_used": session_name}, status_code=500)
    await _send_beacon_after_disclaimer(session_name)
    return JSONResponse({"ok": True, "agent": name, "message": "Sent Down+Enter (Yes, I accept) and startup beacon."})


async def agents_accept_all_disclaimers(request: Request) -> JSONResponse:
    """POST /api/agents/accept-all-disclaimers — for each agent: Down to select option 2 (Yes, I accept), then Enter. Requires gateway running (port 18800)."""
    try:
        sessions = await _get_agent_session_names()
        accepted = []
        for session_name in sessions:
            short = session_name.replace("overstory-overclaw-", "") if "overstory-overclaw-" in session_name else session_name
            out = await _send_tmux_keys_only(session_name, "Down")
            if out.get("error"):
                continue
            await asyncio.sleep(0.25)
            out = await _send_tmux_keys(session_name, "", delay_after_s=0)
            if not out.get("error"):
                accepted.append(short)
                log.info("Accept-all disclaimers: sent Down+Enter for %s", session_name)
                await _send_beacon_after_disclaimer(session_name)
        return JSONResponse({"ok": True, "accepted": accepted, "message": f"Sent Down+Enter and startup beacon to {len(accepted)} agent(s)."})
    except Exception as e:
        log.exception("accept_all_disclaimers failed")
        return JSONResponse({"ok": False, "error": str(e), "accepted": []})


async def agents_approve(request: Request) -> JSONResponse:
    """POST /api/agents/{name}/approve — send Down+Enter to accept the agent's current confirmation prompt.
    Use when a worker has requested approval via mail; the lead (or orchestrator) calls this to approve."""
    name = request.path_params["name"]
    status = await _overstory_run(["status", "--json"], timeout=10, cwd=WORKSPACE)
    agents = status.get("agents") or []
    session_name = _tmux_session_for_agent(agents, name) or f"overstory-overclaw-{name}"
    out = await _send_tmux_keys(session_name, "Down", delay_after_s=0)
    if out.get("error"):
        return JSONResponse({"ok": False, "agent": name, "error": out["error"], "session_used": session_name}, status_code=500)
    out = await _send_tmux_keys(session_name, "", delay_after_s=0)
    if out.get("error"):
        return JSONResponse({"ok": False, "agent": name, "error": out["error"], "session_used": session_name}, status_code=500)
    return JSONResponse({"ok": True, "agent": name, "message": "Sent Down+Enter (approval)."})


async def agents_restart_with_skip_permissions(request: Request) -> JSONResponse:
    """POST /api/agents/restart-with-skip-permissions — Enable skip-permissions, send double Ctrl+C to all agent tmux, then start claude --dangerously-skip-permissions in each."""
    try:
        SETTINGS_DANGEROUSLY_SKIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_DANGEROUSLY_SKIP_FILE.write_text("1")
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Could not enable setting: {e}", "restarted": []})
    try:
        status = await _overstory_run(["status", "--json"], timeout=10, cwd=WORKSPACE)
        if status.get("error"):
            return JSONResponse({"ok": False, "error": status["error"], "restarted": []})
        agents = status.get("agents") or []
        restarted = []
        for a in agents:
            name = a.get("agentName") or a.get("name")
            if not name:
                continue
            session_name = _tmux_session_for_agent(agents, name) or f"overstory-overclaw-{name}"
            out = await _send_tmux_keys_only(session_name, "C-c")
            if out.get("error"):
                log.warning("restart-skip-permissions: C-c #1 for %s: %s", name, out["error"])
            await asyncio.sleep(0.3)
            out = await _send_tmux_keys_only(session_name, "C-c")
            if out.get("error"):
                log.warning("restart-skip-permissions: C-c #2 for %s: %s", name, out["error"])
            await asyncio.sleep(0.5)
            out = await _send_tmux_keys(session_name, "claude --dangerously-skip-permissions", delay_after_s=0)
            if out.get("error"):
                log.warning("restart-skip-permissions: start claude for %s: %s", name, out["error"])
            else:
                restarted.append(name)
                log.info("Restarted %s with --dangerously-skip-permissions", name)
            # Wait for disclaimer, then select option 2 (Yes, I accept) and Enter, then "continue"
            await asyncio.sleep(2.5)
            await _send_tmux_keys_only(session_name, "Down")
            await asyncio.sleep(0.25)
            await _send_tmux_keys(session_name, "", delay_after_s=0)
            await asyncio.sleep(0.5)
            await _send_tmux_keys(session_name, "continue", delay_after_s=0)
        return JSONResponse({"ok": True, "restarted": restarted, "message": f"Enabled skip-permissions; sent C-c C-c, restarted claude, accept disclaimer, and continue in {len(restarted)} session(s)."})
    except Exception as e:
        log.exception("agents_restart_with_skip_permissions failed")
        return JSONResponse({"ok": False, "error": str(e), "restarted": []})


async def agents_accept_mail_check(request: Request) -> JSONResponse:
    """POST /api/agents/{name}/accept-mail-check — Down+Enter to select 'Yes, and don't ask again' for overstory mail check prompt."""
    name = request.path_params["name"]
    status = await _overstory_run(["status", "--json"], timeout=10, cwd=WORKSPACE)
    agents = status.get("agents") or []
    session_name = _tmux_session_for_agent(agents, name) or f"overstory-overclaw-{name}"
    # Option 1 = Yes, Option 2 = Yes don't ask again. Send Down then Enter to choose option 2.
    out = await _send_tmux_keys(session_name, "Down", delay_after_s=0)
    if out.get("error"):
        return JSONResponse({"ok": False, "agent": name, "error": out["error"], "session_used": session_name}, status_code=500)
    out = await _send_tmux_keys(session_name, "", delay_after_s=0)
    if out.get("error"):
        return JSONResponse({"ok": False, "agent": name, "error": out["error"], "session_used": session_name}, status_code=500)
    return JSONResponse({"ok": True, "agent": name, "message": "Sent Down+Enter (Yes, don't ask again for mail check)."})


async def agents_inspect(request: Request) -> JSONResponse:
    """GET /api/agents/{name} — inspect a specific agent."""
    name = request.path_params["name"]
    result = await _overstory_run(["inspect", name])
    return JSONResponse(result)


async def agents_terminal(request: Request) -> JSONResponse:
    """GET /api/agents/{name}/terminal — capture tmux output, or overstory inspect transcript if tmux unavailable.
    Always returns 200 with JSON; errors are in body so the UI can show a message instead of 500."""
    try:
        name = request.path_params.get("name") or ""
        if not name:
            return JSONResponse({"output": "", "session": "", "source": "error", "error": "agent name required"}, status_code=400)
        session_name = f"overstory-overclaw-{name}"
        try:
            lines = int(request.query_params.get("lines", "100"))
        except (ValueError, TypeError):
            lines = 100
        lines = max(1, min(lines, 500))

        # Try tmux capture first (only works when gateway shares the same tmux server as agents)
        try:
            result = await _run_command(
                ["tmux", "capture-pane", "-t", session_name, "-p", "-S", f"-{lines}"],
                timeout=5,
                cwd=WORKSPACE,
            )
            if result.get("returncode") == 0:
                return JSONResponse({"output": result.get("stdout", ""), "session": session_name, "source": "tmux"})
        except Exception:
            pass

        # Fallback: overstory inspect (works without tmux; shows transcript/tool calls)
        output = ""
        try:
            inspect_result = await _overstory_run(
                ["inspect", name, "--no-tmux", "--limit", "50"],
                timeout=15,
                cwd=WORKSPACE,
            )
            if isinstance(inspect_result, dict) and "error" not in inspect_result:
                raw = inspect_result.get("raw") or str(inspect_result)
                return JSONResponse({
                    "output": raw,
                    "session": session_name,
                    "source": "inspect",
                    "attach_cmd": f"tmux attach -t {session_name}",
                })
            err = inspect_result.get("error", "") if isinstance(inspect_result, dict) else str(inspect_result)
            raw = inspect_result.get("raw", "") if isinstance(inspect_result, dict) else ""
            if "database is locked" in (err + raw).lower() or "locked" in (err + raw).lower():
                output = "Transcript temporarily unavailable (database busy). Use the command above to watch live in your terminal."
            else:
                output = (raw or err)[:8000]
        except Exception as e:
            output = str(e)

        return JSONResponse({
            "output": f"[Tmux not available from this process. Run in your terminal to watch live:\n  tmux attach -t {session_name}\n\n--- Transcript (overstory inspect) ---\n{output}",
            "session": session_name,
            "source": "inspect",
            "attach_cmd": f"tmux attach -t {session_name}",
        })
    except Exception as e:
        log.exception("agents_terminal failed for %s", request.path_params.get("name"))
        return JSONResponse({
            "output": "",
            "session": request.path_params.get("name", ""),
            "source": "error",
            "error": str(e),
        })


# ---------------------------------------------------------------------------
# Route: Worktrees (prune when task complete — existing overstory feature)
# ---------------------------------------------------------------------------

async def worktrees_clean(request: Request) -> JSONResponse:
    """POST /api/worktrees/clean — prune completed worktrees (overstory worktree clean --completed)."""
    all_flag = request.query_params.get("all", "").lower() in ("1", "true", "yes")
    force_flag = request.query_params.get("force", "").lower() in ("1", "true", "yes")
    args = ["worktree", "clean", "--all"] if all_flag else ["worktree", "clean", "--completed"]
    if force_flag:
        args.append("--force")
    result = await _overstory_run(args, timeout=60, cwd=WORKSPACE)
    if result.get("error"):
        result["logMessage"] = f"Prune failed: {result.get('error', 'unknown')}"
        return JSONResponse(result, status_code=500)
    # Build a one-line log message for terminal log
    removed = result.get("removed") or result.get("worktrees") or result.get("cleaned")
    if isinstance(removed, list) and removed:
        result["logMessage"] = f"Pruned {len(removed)} completed worktrees: {', '.join(str(x) for x in removed[:10])}{'…' if len(removed) > 10 else ''}"
    elif isinstance(removed, list):
        result["logMessage"] = "Pruned 0 completed worktrees."
    else:
        raw = result.get("raw") or ""
        result["logMessage"] = f"Pruned completed worktrees. {raw.strip()[:200]}" if raw.strip() else "Pruned completed worktrees."
    return JSONResponse(result)


async def zombies_list(request: Request) -> JSONResponse:
    """GET /api/zombies — list agents with state=zombie (from overstory status --json)."""
    try:
        result = await _overstory_run(["status", "--json"], timeout=10, cwd=WORKSPACE)
    except Exception as e:
        log.exception("zombies_list: overstory run failed")
        return JSONResponse({"error": str(e), "zombies": [], "count": 0}, status_code=500)
    if result.get("error"):
        return JSONResponse({"error": result["error"], "zombies": [], "count": 0})
    agents = result.get("agents") or []
    zombies = []
    for a in agents:
        if not isinstance(a, dict):
            continue
        if (a.get("state") or "").lower() == "zombie":
            zombies.append(a)
    return JSONResponse({"zombies": zombies, "count": len(zombies)})


def _lead_for_agent(agent_name: str) -> str:
    """Return the lead to notify for this agent. Workers: lead-{suffix}; lead zombie: orchestrator."""
    if not agent_name:
        return "orchestrator"
    if agent_name.startswith("lead-"):
        return "orchestrator"
    for prefix in ("scout-", "builder-", "reviewer-"):
        if agent_name.startswith(prefix):
            suffix = agent_name[len(prefix):]
            return f"lead-{suffix}"
    return "orchestrator"


async def zombies_slay(request: Request) -> JSONResponse:
    """POST /api/zombies/slay — kill zombie agents (overstory clean --worktrees --sessions).
    Before slaying, auto-mail the lead for each zombie so the lead is notified."""
    status_result = await _overstory_run(["status", "--json"], timeout=10, cwd=WORKSPACE)
    if status_result.get("error"):
        return JSONResponse({"error": status_result["error"], "slain": 0})
    agents = status_result.get("agents") or []
    zombies = [a for a in agents if (a.get("state") or "").lower() == "zombie"]
    zombie_names = [a.get("agentName") or a.get("name", "") for a in zombies]
    slain = len(zombie_names)
    if slain == 0:
        return JSONResponse({"slain": 0, "message": "No zombies to slay.", "zombies": []})
    # Auto-mail the lead for each zombie before slaying
    for name in zombie_names:
        lead = _lead_for_agent(name)
        msg = f"Zombie detected: {name} was marked zombie and is being slain (clean --worktrees --sessions). You may want to respawn or reassign the task."
        try:
            await _send_mail_to_agent("gateway", lead, msg, "high")
        except Exception as e:
            log.warning("Failed to mail lead %s about zombie %s: %s", lead, name, e)
    clean_result = await _overstory_run(["clean", "--worktrees", "--sessions"], timeout=30, cwd=WORKSPACE)
    if clean_result.get("error"):
        return JSONResponse({"error": clean_result["error"], "slain": 0, "zombies": zombie_names})
    return JSONResponse({
        "slain": slain,
        "zombies": zombie_names,
        "message": "Your agents were zombies so I had to kill them.",
    })


def _mail_db_path() -> Path:
    return WORKSPACE / ".overstory" / "mail.db"


# Serialize mail.db access to avoid "database is locked" (one writer/reader at a time per process).
_mail_db_lock: asyncio.Lock | None = None


def _mail_db_connection(mail_db: Path):
    """Open mail.db with WAL and longer timeout to reduce locked errors."""
    conn = sqlite3.connect(str(mail_db), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


async def _with_mail_db_lock():
    """Get the mail DB lock (created in lifespan). Use around any mail.db access."""
    global _mail_db_lock
    if _mail_db_lock is None:
        _mail_db_lock = asyncio.Lock()
    return _mail_db_lock


def _fetch_unread_mail_to_lead_supervisor() -> list[dict]:
    """Read mail.db for ALL unread messages to lead/supervisor/approval-supervisor/coordinator.
    Matches overstory's expected recipients so the Ollama approval supervisor processes the same mail overstory shows."""
    mail_db = _mail_db_path()
    if not mail_db.is_file():
        return []
    try:
        conn = _mail_db_connection(mail_db)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT id, from_agent, to_agent, body FROM messages
               WHERE read = 0 AND (
                 to_agent = 'lead' OR to_agent = 'supervisor'
                 OR to_agent = 'approval-supervisor' OR to_agent = 'coordinator'
                 OR to_agent LIKE 'lead-%' OR to_agent LIKE 'supervisor-%'
                 OR to_agent LIKE 'approval-supervisor-%' OR to_agent LIKE 'coordinator-%'
               ) ORDER BY created_at ASC""",
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.debug("_fetch_unread_mail_to_lead_supervisor: %s", e)
        return []


def _mark_mail_read(msg_id: int) -> None:
    """Mark a message as read in mail.db."""
    mail_db = _mail_db_path()
    if not mail_db.is_file():
        return
    try:
        conn = _mail_db_connection(mail_db)
        conn.execute("UPDATE messages SET read = 1 WHERE id = ?", (msg_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug("_mark_mail_read: %s", e)


async def _ollama_is_approval_request(body: str) -> bool:
    """Use Ollama to analyze: is this message a worker requesting approval? Returns True if yes, False otherwise. On failure, treat as approval to avoid missing any."""
    if not (body or "").strip():
        return False
    body = (body or "")[:2000]
    prompt = f"""Is this message a worker or agent requesting that someone approve their action (e.g. "Need approval", "please approve", "requesting approval", "waiting for approval")? Reply with exactly one word: yes or no.

Message:
{body[:1500]}"""
    try:
        result = await _ollama_chat([{"role": "user", "content": prompt}], model=OLLAMA_MODEL)
        text = (result.get("message") or {}).get("content") or ""
        return "yes" in text.lower().strip()[:10]
    except Exception as e:
        log.debug("Ollama approval check failed, treating as approval request: %s", e)
        return True


APPROVAL_SUPERVISOR_INTERVAL_S = 1.0
_approval_supervisor_wake: asyncio.Event | None = None  # Set in lifespan so new mail can wake the loop immediately


async def _approval_supervisor_once() -> int:
    """Analyze every unread mail to lead/supervisor with Ollama; if approval request, approve sender. Ensures no approvals are missed."""
    lock = await _with_mail_db_lock()
    async with lock:
        candidates = _fetch_unread_mail_to_lead_supervisor()
    approved = 0
    for row in candidates:
        msg_id = row.get("id")
        from_agent = (row.get("from_agent") or "").strip()
        body = (row.get("body") or "")[:2000]
        if not from_agent:
            if msg_id:
                async with lock:
                    _mark_mail_read(msg_id)
            continue
        if not await _ollama_is_approval_request(body):
            if msg_id:
                async with lock:
                    _mark_mail_read(msg_id)
            continue
        try:
            status = await _overstory_run(["status", "--json"], timeout=10, cwd=WORKSPACE)
            agents = status.get("agents") or []
            session_name = _tmux_session_for_agent(agents, from_agent) or f"overstory-overclaw-{from_agent}"
            out = await _send_tmux_keys(session_name, "Down", delay_after_s=0)
            if out.get("error"):
                log.debug("Approval supervisor: approve failed for %s: %s", from_agent, out["error"])
                continue
            out = await _send_tmux_keys(session_name, "", delay_after_s=0)
            if out.get("error"):
                log.debug("Approval supervisor: Enter failed for %s: %s", from_agent, out["error"])
                continue
            approved += 1
            log.info("Approval supervisor (Ollama): approved %s", from_agent)
        except Exception as e:
            log.debug("Approval supervisor: %s for %s: %s", type(e).__name__, from_agent, e)
        if msg_id:
            async with lock:
                _mark_mail_read(msg_id)
    return approved


async def _approval_supervisor_loop() -> None:
    """Always active: on every new mail (wake) or every 1s, analyze all unread mail to lead/supervisor with Ollama and respond to approval requests."""
    log.info("Approval supervisor (Ollama) started — trigger on every new mail, interval %.1fs", APPROVAL_SUPERVISOR_INTERVAL_S)
    while True:
        try:
            n = await _approval_supervisor_once()
            if n:
                log.info("Approval supervisor: approved %d agent(s)", n)
        except Exception as e:
            log.debug("Approval supervisor tick: %s", e)
        try:
            await asyncio.wait_for(_approval_supervisor_wake.wait(), timeout=APPROVAL_SUPERVISOR_INTERVAL_S)
            _approval_supervisor_wake.clear()
        except asyncio.TimeoutError:
            pass


def _write_mail_to_db(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> None:
    """Persist sent mail to workspace .overstory/mail.db so the dashboard shows it (sent and received)."""
    mail_db = _mail_db_path()
    mail_db.parent.mkdir(parents=True, exist_ok=True)
    schema = """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT,
        from_agent TEXT NOT NULL,
        to_agent TEXT NOT NULL,
        subject TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'normal',
        read INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_msg_to ON messages(to_agent);
    CREATE INDEX IF NOT EXISTS idx_msg_read ON messages(read);
    """
    now = time.time()
    subject = (message.strip().split("\n")[0][:200]) if message.strip() else "No subject"
    body_text = (message or "").strip() or "(no body)"
    try:
        conn = _mail_db_connection(mail_db)
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO messages (from_agent, to_agent, subject, body, priority, created_at) VALUES (?,?,?,?,?,?)",
            (from_agent or "orchestrator", to_agent or "", subject, body_text, priority or "normal", now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("Could not write mail to %s: %s", mail_db, e)


async def _send_mail_to_agent(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> None:
    """Persist to mail.db and send via overstory (uses --subject and --body). Wakes approval supervisor so it analyzes and responds immediately."""
    lock = await _with_mail_db_lock()
    async with lock:
        _write_mail_to_db(from_agent, to_agent, message, priority)
    if _approval_supervisor_wake:
        _approval_supervisor_wake.set()
    subject = (message.strip().split("\n")[0][:200]) if message.strip() else "No subject"
    body_text = (message or "").strip()[:2000] or "(no body)"
    await _overstory_run([
        "mail", "send",
        "--from", from_agent,
        "--to", to_agent,
        "--subject", subject,
        "--body", body_text,
        "--priority", priority,
    ], timeout=5, cwd=WORKSPACE)


async def agents_mail(request: Request) -> JSONResponse:
    """POST /api/agents/mail — send inter-agent mail.
    
    {"from": "orchestrator", "to": "builder-abc123", "message": "..."}
    """
    body = await request.json()
    from_agent = body.get("from", "orchestrator")
    to_agent = body.get("to", "")
    message = body.get("message", "")
    priority = body.get("priority", "normal")
    await _send_mail_to_agent(from_agent, to_agent, message, priority)
    return JSONResponse({"ok": True})


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

async def debug_approval_supervisor_run(request: Request) -> JSONResponse:
    """POST /api/debug/approval-supervisor-run — run approval supervisor once (for tests). Returns {approved: n}."""
    try:
        n = await _approval_supervisor_once()
        return JSONResponse({"ok": True, "approved": n})
    except Exception as e:
        log.exception("debug_approval_supervisor_run failed")
        return JSONResponse({"ok": False, "error": str(e), "approved": 0})


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
# Settings (dangerously-skip-permissions for agents; UI toggle syncs here)
# ---------------------------------------------------------------------------

async def settings_get(request: Request) -> JSONResponse:
    """GET /api/settings — Read dangerously_skip_permissions (used by wrapper for agents)."""
    try:
        if SETTINGS_DANGEROUSLY_SKIP_FILE.exists():
            raw = SETTINGS_DANGEROUSLY_SKIP_FILE.read_text().strip()
            value = raw in ("1", "true", "yes")
        else:
            value = True  # default: keep current behavior
        return JSONResponse({"dangerously_skip_permissions": value})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def settings_put(request: Request) -> JSONResponse:
    """POST /api/settings — Set dangerously_skip_permissions. Body: { \"dangerously_skip_permissions\": false }."""
    try:
        body = await request.json()
        value = body.get("dangerously_skip_permissions", True)
        SETTINGS_DANGEROUSLY_SKIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_DANGEROUSLY_SKIP_FILE.write_text("1" if value else "0")
        return JSONResponse({"dangerously_skip_permissions": bool(value)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

routes = [
    Route("/health", health, methods=["GET"]),
    Route("/api/status", status, methods=["GET"]),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/message", message, methods=["POST"]),
    Route("/api/route", route_task, methods=["POST"]),
    Route("/api/agents", agents_list, methods=["GET"]),
    Route("/api/agents/spawn", agents_spawn, methods=["POST"]),
    Route("/api/agents/mail", agents_mail, methods=["POST"]),
    Route("/api/agents/{name}", agents_inspect, methods=["GET"]),
    Route("/api/agents/{name}/terminal", agents_terminal, methods=["GET"]),
    Route("/api/agents/auto-accept-prompts", agents_auto_accept_prompts, methods=["POST"]),
    Route("/api/agents/accept-all-disclaimers", agents_accept_all_disclaimers, methods=["POST"]),
    Route("/api/agents/restart-with-skip-permissions", agents_restart_with_skip_permissions, methods=["POST"]),
    Route("/api/agents/{name}/accept-disclaimer", agents_accept_disclaimer, methods=["POST"]),
    Route("/api/agents/{name}/approve", agents_approve, methods=["POST"]),
    Route("/api/agents/{name}/accept-mail-check", agents_accept_mail_check, methods=["POST"]),
    Route("/api/worktrees/clean", worktrees_clean, methods=["POST"]),
    Route("/api/zombies", zombies_list, methods=["GET"]),
    Route("/api/zombies/slay", zombies_slay, methods=["POST"]),
    Route("/api/skills", skills_list, methods=["GET"]),
    Route("/api/skills/exec", skills_exec, methods=["POST"]),
    Route("/api/tools", tools_list, methods=["GET"]),
    Route("/api/tools/exec", tools_exec, methods=["POST"]),
    Route("/api/memory", memory_read, methods=["GET"]),
    Route("/api/memory", memory_write, methods=["POST"]),
    Route("/api/settings", settings_get, methods=["GET"]),
    Route("/api/settings", settings_put, methods=["POST"]),
    Route("/api/debug/approval-supervisor-run", debug_approval_supervisor_run, methods=["POST"]),
]

@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """Start background disclaimer watcher and Ollama approval supervisor (always active, trigger on every new mail)."""
    global _approval_supervisor_wake, _mail_db_lock
    _approval_supervisor_wake = asyncio.Event()
    _mail_db_lock = asyncio.Lock()
    watcher = asyncio.create_task(_disclaimer_watcher_loop())
    approval_supervisor = asyncio.create_task(_approval_supervisor_loop())
    try:
        yield
    finally:
        watcher.cancel()
        approval_supervisor.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        try:
            await approval_supervisor
        except asyncio.CancelledError:
            pass


app = Starlette(routes=routes, lifespan=lifespan)
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
