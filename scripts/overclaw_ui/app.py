#!/usr/bin/env python3
"""
OverClaw UI - Web interface matching Overstory dashboard layout with tabbed Terminal/Output on the right.

Left: Overstory-style panels (Agents, Mail, Merge queue, Metrics, Bun log).
Right: Tabs - TERMINAL, OUTPUT, PROBLEMS, PORTS, DEBUG CONSOLE.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from flask import Flask, Response, jsonify, render_template_string, request, send_from_directory

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent


def _resolve_root_workspace() -> Path:
    """Resolve workspace root: OVERCLAW_WORKSPACE if set and has skills/, else walk up from script to find skills/."""
    env_ws = os.environ.get("OVERCLAW_WORKSPACE", "").strip()
    if env_ws:
        p = Path(env_ws).resolve()
        if p.is_dir() and (p / "skills").is_dir():
            return p
    candidate = SCRIPT_DIR
    for _ in range(10):
        skills_dir = candidate / "skills"
        if skills_dir.is_dir():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return SCRIPT_DIR.parent.parent  # fallback: parent of scripts


ROOT_WORKSPACE = _resolve_root_workspace()
WORKSPACE = ROOT_WORKSPACE  # kept for compatibility; effective project folder from get_effective_project_folder()

SPECS_DIR = ROOT_WORKSPACE / ".overstory" / "specs"


def _task_description_for_agent(name: str, bead_id: str) -> tuple[str, str]:
    """Resolve short and full task description from spec files. Returns (task_short, task_full)."""
    task_short = ""
    task_full = ""
    if not name or name == "Approval supervisor":
        return task_short, task_full
    # Try oc-{suffix}.md from agent name (e.g. lead-acf799e7 -> oc-acf799e7.md)
    suffix = ""
    for prefix in ("lead-", "builder-", "scout-", "reviewer-", "scribe-", "blogger-", "researcher-", "social-media-manager-"):
        if name.startswith(prefix):
            suffix = name[len(prefix):].strip()
            break
    if not suffix and "-" in name:
        suffix = name.split("-", 1)[-1]
    candidates = []
    if suffix:
        candidates.append(SPECS_DIR / f"oc-{suffix}.md")
    bead = (bead_id or "").strip()
    if bead and bead != "—":
        candidates.append(SPECS_DIR / f"{bead}.md")
    for path in candidates:
        if path.is_file():
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").strip()
                if raw.startswith("# Task:"):
                    after_header = raw.replace("# Task:", "", 1).strip()
                    # First meaningful line is often the task (next line after header)
                    lines = [s.strip() for s in after_header.split("\n") if s.strip()]
                    task_full = lines[0] if lines else after_header[:2000]
                    if len(lines) > 1 and len(lines[0]) < 20:
                        task_full = lines[1] if len(lines[1]) > len(lines[0]) else task_full
                    task_full = task_full[:2000]
                    task_short = (task_full[:80] + "…") if len(task_full) > 80 else task_full
                    break
            except Exception:
                pass
    return task_short, task_full


def _resolve_last30days_store() -> Path:
    """Resolve path to last30days store.py by walking up until we find skills/last30days/scripts/store.py."""
    store_name = Path("skills") / "last30days" / "scripts" / "store.py"
    candidate = ROOT_WORKSPACE
    for _ in range(10):
        p = candidate / store_name
        if p.is_file():
            return p
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return ROOT_WORKSPACE / store_name  # fallback to default
OVERSTORY_BIN = os.environ.get("OVERSTORY_BIN", "overstory")
GATEWAY_URL = os.environ.get("OVERCLAW_GATEWAY_URL", "http://localhost:18800")
MAIL_DB_PATH = ROOT_WORKSPACE / ".overstory" / "mail.db"
LOG_DIR = ROOT_WORKSPACE / ".overstory" / "logs"
UI_SETTINGS_DIR = ROOT_WORKSPACE / ".overclaw_ui"
UI_SETTINGS_PATH = UI_SETTINGS_DIR / "settings.json"

app = Flask(__name__)

# ---------------------------------------------------------------------------
# UI settings: default/current project folder (default = overstory workspace)
# ---------------------------------------------------------------------------
# Sentinel for "no project" (agents ignore repos); distinct from "" which falls back to default
CURRENT_PROJECT_NONE = "__none__"

_DEFAULT_UI_SETTINGS = {
    "default_project_folder": "overstory",  # "overstory" = ROOT_WORKSPACE; or absolute path under root
    "current_project_folder": "",           # "" = use default; __none__ = no project; or path (must be under ROOT_WORKSPACE)
    "create_project_on_next_prompt": False, # when True, next chat message creates a new folder from prompt
    "refresh_interval_ms": 3000,            # dashboard poll (MacBook-friendly default; 500, 1000, 2000, 3000, 5000)
    "sidebar_collapsed_default": False,    # start with sidebar collapsed
}


def _load_ui_settings() -> Dict[str, any]:
    """Load UI settings from .overclaw_ui/settings.json."""
    out = dict(_DEFAULT_UI_SETTINGS)
    if not UI_SETTINGS_PATH.is_file():
        return out
    try:
        data = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
        for k in _DEFAULT_UI_SETTINGS:
            if k in data:
                out[k] = data[k]
    except Exception:
        pass
    return out


def _save_ui_settings(settings: Dict[str, any]) -> None:
    """Persist UI settings."""
    UI_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    UI_SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _path_under_root(path_val: str) -> Optional[Path]:
    """Resolve path to a directory under ROOT_WORKSPACE; return None if invalid."""
    if not path_val:
        return None
    p = Path(path_val)
    if not p.is_absolute():
        p = ROOT_WORKSPACE / p
    try:
        p = p.resolve()
        p.relative_to(ROOT_WORKSPACE)
    except (ValueError, OSError):
        return None
    return p if p.is_dir() else None


def get_effective_project_folder() -> Path:
    """Current project folder: overstory workspace by default, or selected subfolder (changeable in settings). CURRENT_PROJECT_NONE falls back to ROOT_WORKSPACE for tree/paths."""
    s = _load_ui_settings()
    current = (s.get("current_project_folder") or "").strip()
    default = (s.get("default_project_folder") or "overstory").strip()
    if current and current != CURRENT_PROJECT_NONE:
        candidate = _path_under_root(current)
        if candidate is not None:
            return candidate
    if current == CURRENT_PROJECT_NONE:
        return ROOT_WORKSPACE  # no project selected; use root for file tree etc.
    if default and default != "overstory":
        candidate = _path_under_root(default)
        if candidate is not None:
            return candidate
    return ROOT_WORKSPACE


def _slug_from_prompt(message: str, max_len: int = 48) -> str:
    """Generate a safe folder name from the initial prompt."""
    s = re.sub(r"[^a-z0-9\s-]", "", message[:max_len].lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-") or "project"
    return s[:max_len]

# Store terminal output logs
_terminal_logs: List[Dict[str, any]] = []
_bun_logs: List[Dict[str, any]] = []

# Integrated Claude CLI process (what agents use / are spawned from)
_claude_process: Optional[subprocess.Popen] = None
_claude_output_lines: List[str] = []
_claude_output_lock = threading.Lock()
_claude_skip_permissions = False
CLAUDE_BIN = os.environ.get("CLAUDE_CLI_BIN", "claude")


def overstory_status():
    """Run overstory status and return parsed data for dashboard. Retries on database locked."""
    result = None
    for attempt in range(3):
        try:
            result = subprocess.run(
                [OVERSTORY_BIN, "status"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(WORKSPACE),
            )
            if result.returncode == 0:
                break
            err = (result.stderr or result.stdout or "")
            last_error = result.stderr or "overstory status failed"
            if "database is locked" in err.lower() or "locked" in err.lower() or "sqlite" in err.lower():
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {
                    "error": "Agents temporarily unavailable (database busy). Try again in a moment.",
                    "agents": [],
                    "raw": "",
                }
            return {"error": last_error, "agents": [], "raw": result.stdout}
        except subprocess.TimeoutExpired:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            return {"error": "Status timed out. Try again.", "agents": [], "raw": ""}
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            return {"error": str(e), "agents": [], "raw": ""}
    if not result or result.returncode != 0:
        return {"error": "overstory status failed", "agents": [], "raw": ""}
    try:
        raw = result.stdout
        agents = []
        mail_count = 0
        merge_count = 0
        in_agents = False
        in_worktrees = False
        worktrees = []
        for line in raw.split("\n"):
            line = line.strip()
            if "🤖 Agents:" in line or "Agents:" in line:
                in_agents = True
                try:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        mail_count = int(parts[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass
                continue
            if in_agents:
                if "🌳" in line or "Worktrees:" in line:
                    in_agents = False
                    in_worktrees = True
                elif ("●" in line or "◐" in line or "○" in line) and "│" in line:
                    parts = [p.strip() for p in line.split("│")][1:-1]
                    if len(parts) >= 6:
                        name = parts[1][:20] if len(parts) > 1 else ""
                        bead_id = parts[4][:16] if len(parts) > 4 else ""
                        task_short, task_full = _task_description_for_agent(name, bead_id)
                        agents.append({
                            "state_icon": "●" if "●" in line else "◐" if "◐" in line else "○",
                            "name": name,
                            "capability": parts[2][:12] if len(parts) > 2 else "",
                            "state": parts[3][:10] if len(parts) > 3 else "",
                            "bead_id": bead_id,
                            "duration": parts[5][:10] if len(parts) > 5 else "",
                            "tmux": "●" if len(parts) > 6 and "●" in (parts[6] or "") else "○",
                            "task_short": task_short,
                            "task_full": task_full,
                        })
            if in_worktrees and line.startswith("overstory/"):
                worktrees.append(line)
            if "📬 Mail:" in line:
                try:
                    mail_count = int(line.split(":")[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass
            if "🔀 Merge queue:" in line:
                try:
                    merge_count = int(line.split(":")[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass

        # If no agents from table but we have worktrees, show agents from worktrees (overstory may report "0 active" while worktrees exist)
        if not agents and worktrees:
            seen = set()
            for wt in worktrees:
                # e.g. overstory/builder-75c1f0b4/workspace-0vk or overstory/lead-75c1f0b4/workspace-0vk
                parts = wt.split("/")
                if len(parts) >= 2:
                    name = parts[1].strip()
                    if name and name not in seen:
                        seen.add(name)
                        cap = name.split("-")[0] if "-" in name else name[:12]
                        bead_id = parts[2][:16] if len(parts) > 2 else ""
                        task_short, task_full = _task_description_for_agent(name[:24], bead_id)
                        agents.append({
                            "state_icon": "●",
                            "name": name[:24],
                            "capability": cap[:12],
                            "state": "worktree",
                            "bead_id": bead_id,
                            "duration": "—",
                            "tmux": "○",
                            "task_short": task_short,
                            "task_full": task_full,
                        })

        # Always show Approval supervisor first (runs in gateway, not an overstory tmux agent)
        agents.insert(0, {
            "state_icon": "●",
            "name": "Approval supervisor",
            "capability": "supervisor",
            "state": "active",
            "bead_id": "—",
            "duration": "—",
            "tmux": "○",
            "system": True,
            "task_short": "",
            "task_full": "",
        })

        # Calculate average duration
        avg_duration = "0s"
        if agents:
            total_seconds = 0
            for agent in agents:
                duration_str = agent.get("duration", "0s") or "0s"
                if duration_str == "—":
                    continue
                # Parse duration like "6m 52s" or "4m 48s"
                try:
                    parts = duration_str.split()
                    seconds = 0
                    for part in parts:
                        if part.endswith("m"):
                            seconds += int(part[:-1]) * 60
                        elif part.endswith("s"):
                            seconds += int(part[:-1])
                        total_seconds += seconds
                except (ValueError, AttributeError):
                    pass
            if total_seconds > 0:
                avg_seconds = total_seconds / len(agents)
                if avg_seconds < 60:
                    avg_duration = f"{int(avg_seconds)}s"
                else:
                    avg_duration = f"{int(avg_seconds // 60)}m {int(avg_seconds % 60)}s"

        return {
            "raw": raw,
            "agents": agents,
            "worktrees": worktrees,
            "mail_count": mail_count,
            "merge_count": merge_count,
            "metrics": {"totalSessions": len(agents), "avgDuration": avg_duration},
        }
    except Exception as e:
        return {"error": str(e), "agents": [], "mail_count": 0, "merge_count": 0}


def get_mail_items(limit: int = 50) -> List[Dict[str, any]]:
    """Get mail items from overstory mail database (sent and received)."""
    mail_items = []
    if not MAIL_DB_PATH.exists():
        return mail_items

    try:
        conn = sqlite3.connect(str(MAIL_DB_PATH), timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT from_agent, to_agent, subject, body, created_at FROM messages "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        for row in cursor.fetchall():
            created_at = row["created_at"]
            time_ago = ""
            if created_at is not None:
                try:
                    elapsed = time.time() - float(created_at)
                    if elapsed < 60:
                        time_ago = f"{int(elapsed)}s ago"
                    elif elapsed < 3600:
                        time_ago = f"{int(elapsed // 60)}m ago"
                    else:
                        time_ago = f"{int(elapsed // 3600)}h ago"
                except (TypeError, ValueError):
                    pass

            from_agent = (row["from_agent"] or "").strip()
            to_agent = (row["to_agent"] or "").strip()
            subject = (row["subject"] or row["body"] or "").strip()
            if not subject and row["body"]:
                subject = (row["body"] or "").strip()[:50]

            mail_items.append({
                "from": from_agent,
                "to": to_agent,
                "subject": subject,
                "body": row["body"] or "",
                "time_ago": time_ago,
            })
        conn.close()
    except Exception as e:
        import traceback
        sys.stderr.write(f"get_mail_items error: {e}\n{traceback.format_exc()}")

    return mail_items


def get_mail_unread_count() -> int:
    """Return number of unread messages in mail.db (for Mail panel header)."""
    if not MAIL_DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(str(MAIL_DB_PATH), timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE read = 0").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def get_bun_logs(limit: int = 50) -> List[Dict[str, any]]:
    """Get bun log entries showing agent activity."""
    logs = []
    
    # Try to get agent status and create log entries
    try:
        status_data = overstory_status()
        agents = status_data.get("agents", [])
        current_time = datetime.now().strftime("%H:%M:%S")
        
        for agent in agents:
            agent_name = agent.get("name", "unknown")
            tmux_status = "up" if agent.get("tmux") == "●" else "down"
            state = agent.get("state", "working")
            
            logs.append({
                "time": current_time,
                "agent": agent_name,
                "status": state.lower(),
                "tmux": tmux_status,
                "pid": "up",  # We don't have PID info from status
            })
    except:
        pass
    
    # Also try to read from log files if they exist
    if LOG_DIR.exists():
        try:
            import re
            log_files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            for log_file in log_files[:5]:  # Read last 5 log files
                try:
                    with open(log_file, "r") as f:
                        lines = f.readlines()[-20:]  # Last 20 lines
                        for line in lines:
                            line = line.strip()
                            if line:
                                # Try to parse log line - format: [HH:MM:SS] agent-name: status
                                if ":" in line and ("[" in line or "agent" in line.lower()):
                                    # Extract time if present
                                    time_match = re.search(r'\[(\d{2}:\d{2}:\d{2})\]', line)
                                    time_part = time_match.group(1) if time_match else datetime.now().strftime("%H:%M:%S")
                                    
                                    # Extract agent name and status
                                    parts = line.split(":")
                                    if len(parts) >= 2:
                                        agent_part = parts[0].replace("[", "").replace("]", "").strip()
                                        status_part = parts[1].strip() if len(parts) > 1 else "working"
                                        
                                        logs.append({
                                            "time": time_part,
                                            "agent": agent_part,
                                            "status": status_part.split()[0] if status_part else "working",
                                            "tmux": "up",
                                            "pid": "up",
                                        })
                except:
                    pass
        except:
            pass
    
    # Ensure we have recent entries even if no logs found
    if not logs:
        current_time = datetime.now().strftime("%H:%M:%S")
        logs.append({
            "time": current_time,
            "agent": "coordinator",
            "status": "working",
            "tmux": "up",
            "pid": "up",
        })
    
    return logs[-limit:]  # Return last N entries


def add_terminal_log(message: str, log_type: str = "info"):
    """Add a message to terminal logs."""
    _terminal_logs.append({
        "timestamp": time.time(),
        "message": message,
        "type": log_type,
    })
    # Keep only last 1000 entries
    if len(_terminal_logs) > 1000:
        _terminal_logs.pop(0)


def get_terminal_logs_from_status():
    """Generate terminal log entries from overstory status."""
    logs = []
    try:
        status_data = overstory_status()
        agents = status_data.get("agents", [])
        
        if agents:
            agent_names = [a.get("name", "") for a in agents if a.get("name")]
            if agent_names:
                logs.append({
                    "timestamp": time.time(),
                    "message": f"Dispatched. {len(agents)} agents are now working: {', '.join(agent_names[:5])}",
                    "type": "info",
                })
                logs.append({
                    "timestamp": time.time(),
                    "message": f"Tracking as group. Monitoring for progress and merge-ready signals.",
                    "type": "info",
                })
        
        # Add status update
        logs.append({
            "timestamp": time.time(),
            "message": f"OverClaw Status: {len(agents)} active agents",
            "type": "success",
        })
    except:
        pass
    
    return logs


@app.route("/")
def index():
    """Serve the OverClaw UI (Overstory dashboard + tabbed terminal/output)."""
    return render_template_string(INDEX_HTML, gateway_url=GATEWAY_URL)


@app.route("/favicon.ico")
def favicon():
    """Avoid 404 for browser favicon requests."""
    return "", 204


@app.route("/api/overstory")
def api_overstory():
    """Dashboard data from overstory status."""
    return jsonify(overstory_status())


@app.route("/api/mail")
def api_mail():
    """Get mail items (sent and received) and unread count."""
    return jsonify({
        "mail_items": get_mail_items(),
        "mail_count": get_mail_unread_count(),
    })


# ---------------------------------------------------------------------------
# Workspace / repo / file tree / skills (topbar + sidebar)
# ---------------------------------------------------------------------------

SKILLS_DIR = WORKSPACE / "skills"
LAST30DAYS_STORE = _resolve_last30days_store()

# Backend cache for GitHub auth and repos (avoids hitting gh on every dropdown open)
_GITHUB_AUTH_CACHE: Optional[Dict[str, any]] = None
_GITHUB_AUTH_CACHE_TIME: float = 0
_GITHUB_REPOS_CACHE: Optional[List[str]] = None
_GITHUB_REPOS_CACHE_TIME: float = 0
GITHUB_AUTH_CACHE_TTL_S = 60
GITHUB_REPOS_CACHE_TTL_S = 300


def get_workspace_info() -> Dict[str, any]:
    """Current project folder, git root, GitHub remote URL, branch (uses effective project folder from settings). When user chose None, project_none=True and folder='None'."""
    s = _load_ui_settings()
    current = (s.get("current_project_folder") or "").strip()
    project_none = current == CURRENT_PROJECT_NONE
    project = get_effective_project_folder()
    info = {
        "folder": "None" if project_none else project.name,
        "path": "" if project_none else str(project),
        "project_none": project_none,
        "root_workspace": str(ROOT_WORKSPACE),
        "git_root": None,
        "branch": None,
        "remote_url": None,
        "github_url": None,
        "is_github": False,
    }
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project),
        )
        if r.returncode == 0 and r.stdout.strip():
            info["git_root"] = r.stdout.strip()
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project),
        )
        if r.returncode == 0 and r.stdout.strip():
            info["branch"] = r.stdout.strip()
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project),
        )
        if r.returncode == 0 and r.stdout.strip():
            url = r.stdout.strip()
            info["remote_url"] = url
            if "github.com" in url:
                info["is_github"] = True
                # Normalize to https and strip .git
                u = url.replace("git@github.com:", "https://github.com/").replace(".git", "")
                if not u.startswith("http"):
                    u = "https://github.com/" + u.split("github.com")[-1].lstrip("/")
                info["github_url"] = u
    except Exception:
        pass
    return info


def get_file_tree(root: Optional[Path] = None, max_depth: int = 4, max_children: int = 100) -> List[Dict[str, any]]:
    """Build a shallow file tree for the sidebar. Uses effective project folder when root not given."""
    root = root or get_effective_project_folder()
    if not root.is_dir():
        return []

    def scan(parent: Path, depth: int, base: Path) -> List[Dict[str, any]]:
        if depth <= 0:
            return []
        out = []
        try:
            entries = sorted(parent.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for i, p in enumerate(entries):
                if i >= max_children:
                    break
                if p.name.startswith(".") and p.name != ".overstory":
                    continue
                if p.name == "node_modules" or p.name == "__pycache__":
                    continue
                node = {"name": p.name, "path": str(p), "type": "dir" if p.is_dir() else "file"}
                if p.is_dir() and depth > 1:
                    node["children"] = scan(p, depth - 1, base)
                out.append(node)
        except (OSError, PermissionError):
            pass
        return out

    return scan(root, max_depth, root)


def _parse_skill_frontmatter(text: str) -> Dict[str, str]:
    """Minimal YAML frontmatter parse for SKILL.md."""
    import re
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip("'\"")
    return result


def get_skills_installed() -> List[Dict[str, any]]:
    """List installed skills from workspace skills dir."""
    out = []
    if not SKILLS_DIR.is_dir():
        return out
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        try:
            fm = _parse_skill_frontmatter(skill_md.read_text(encoding="utf-8"))
        except Exception:
            fm = {}
        out.append({
            "name": fm.get("name") or skill_dir.name,
            "description": fm.get("description", ""),
            "path": str(skill_dir),
        })
    return out


def get_skills_trending() -> List[Dict[str, any]]:
    """Trending topics from last30days skill (store.py trending --days 30)."""
    if not LAST30DAYS_STORE.is_file():
        app.logger.warning("Trending: store script not found at %s", LAST30DAYS_STORE)
        return []
    # Run from the workspace that contains the store (skills/last30days/scripts/store.py -> 4 levels up)
    store_workspace = LAST30DAYS_STORE.parent.parent.parent.parent
    try:
        r = subprocess.run(
            [sys.executable, str(LAST30DAYS_STORE), "trending", "--days", "30"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(store_workspace),
        )
        if r.returncode != 0:
            app.logger.warning(
                "Trending: store.py exited %s stderr=%s",
                r.returncode,
                (r.stderr or "").strip() or r.stdout[:200],
            )
            return []
        data = json.loads(r.stdout)
        return data.get("trending", [])
    except subprocess.TimeoutExpired:
        app.logger.warning("Trending: store.py timed out")
        return []
    except json.JSONDecodeError as e:
        app.logger.warning("Trending: store.py invalid JSON %s", e)
        return []
    except Exception as e:
        app.logger.warning("Trending: %s", e)
        return []


@app.route("/api/workspace")
def api_workspace():
    """Current project folder and GitHub repo info for topbar."""
    return jsonify(get_workspace_info())


def get_github_auth_status(force_refresh: bool = False) -> Dict[str, any]:
    """Check gh auth and return user login + avatar if logged in. Uses backend cache (TTL 60s) unless force_refresh."""
    global _GITHUB_AUTH_CACHE, _GITHUB_AUTH_CACHE_TIME
    now = time.time()
    if not force_refresh and _GITHUB_AUTH_CACHE is not None and (now - _GITHUB_AUTH_CACHE_TIME) < GITHUB_AUTH_CACHE_TTL_S:
        return _GITHUB_AUTH_CACHE
    out = {"logged_in": False, "login": None, "avatar_url": None, "name": None}
    gh = shutil.which("gh")
    if not gh:
        _GITHUB_AUTH_CACHE = out
        _GITHUB_AUTH_CACHE_TIME = now
        return out
    try:
        r = subprocess.run(
            [gh, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            _GITHUB_AUTH_CACHE = out
            _GITHUB_AUTH_CACHE_TIME = now
            return out
        r2 = subprocess.run(
            [gh, "api", "user", "--jq", "{\"login\": .login, \"avatar_url\": .avatar_url, \"name\": .name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r2.returncode != 0 or not r2.stdout.strip():
            _GITHUB_AUTH_CACHE = out
            _GITHUB_AUTH_CACHE_TIME = now
            return out
        data = json.loads(r2.stdout)
        out["logged_in"] = True
        out["login"] = data.get("login") or ""
        out["avatar_url"] = data.get("avatar_url") or ""
        out["name"] = data.get("name") or out["login"]
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception):
        pass
    _GITHUB_AUTH_CACHE = out
    _GITHUB_AUTH_CACHE_TIME = now
    return out


@app.route("/api/github-auth")
def api_github_auth():
    """GET: gh auth status and user info (login, avatar_url) for topbar. ?refresh=1 bypasses cache."""
    force = request.args.get("refresh") == "1"
    return jsonify(get_github_auth_status(force_refresh=force))


@app.route("/api/github-auth/login", methods=["POST"])
def api_github_auth_login():
    """Try to open gh auth login in a new tmux window; else return instruction."""
    out = {"ok": False, "message": "", "instruction": "Run in your terminal: gh auth login"}
    if not shutil.which("gh"):
        out["message"] = "GitHub CLI (gh) is not installed."
        return jsonify(out), 400
    tmux = os.environ.get("TMUX")
    if tmux:
        try:
            subprocess.Popen(
                ["tmux", "new-window", "-n", "gh-auth", "gh", "auth", "login"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            out["ok"] = True
            out["message"] = "Opened tmux window \"gh-auth\". Complete login there, then refresh."
        except Exception as e:
            out["message"] = str(e)
    else:
        out["message"] = "Not in a tmux session. Run in your terminal: gh auth login"
    return jsonify(out)


def get_github_repos(force_refresh: bool = False) -> List[str]:
    """List user's GitHub repos (full_name) when gh is logged in. Limit 100. Uses backend cache (TTL 5min) unless force_refresh."""
    global _GITHUB_REPOS_CACHE, _GITHUB_REPOS_CACHE_TIME
    now = time.time()
    if not force_refresh and _GITHUB_REPOS_CACHE is not None and (now - _GITHUB_REPOS_CACHE_TIME) < GITHUB_REPOS_CACHE_TTL_S:
        return _GITHUB_REPOS_CACHE
    repos: List[str] = []
    gh = shutil.which("gh")
    if not gh:
        _GITHUB_REPOS_CACHE = repos
        _GITHUB_REPOS_CACHE_TIME = now
        return repos
    try:
        r = subprocess.run(
            [gh, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            _GITHUB_REPOS_CACHE = repos
            _GITHUB_REPOS_CACHE_TIME = now
            return repos
        r2 = subprocess.run(
            [gh, "api", "user/repos", "--paginate", "--jq", ".[].full_name"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r2.returncode != 0 or not r2.stdout.strip():
            _GITHUB_REPOS_CACHE = repos
            _GITHUB_REPOS_CACHE_TIME = now
            return repos
        names = [n.strip() for n in r2.stdout.strip().split("\n") if n.strip()][:100]
        repos = names
    except (subprocess.TimeoutExpired, Exception):
        pass
    _GITHUB_REPOS_CACHE = repos
    _GITHUB_REPOS_CACHE_TIME = now
    return repos


@app.route("/api/github-repos")
def api_github_repos():
    """List user's GitHub repos (full_name). Limit 100. Backend cached 5min. ?refresh=1 bypasses cache."""
    force = request.args.get("refresh") == "1"
    return jsonify({"repos": get_github_repos(force_refresh=force)})


def _open_project_path(path_val: str) -> Optional[Path]:
    """Resolve path under ROOT_WORKSPACE and set as current project. Returns path or None."""
    candidate = _path_under_root(path_val)
    if candidate is None:
        return None
    s = _load_ui_settings()
    s["current_project_folder"] = str(candidate)
    _save_ui_settings(s)
    return candidate


def _open_project_repo(repo_full_name: str) -> Dict[str, any]:
    """Ensure repo exists in workspace (clone if not), set as current project. Returns { path, cloned, error? }."""
    out = {"path": None, "cloned": False}
    if not repo_full_name or "/" not in repo_full_name:
        out["error"] = "Invalid repo: use owner/name"
        return out
    gh = shutil.which("gh")
    if not gh:
        out["error"] = "GitHub CLI (gh) not installed"
        return out
    # Prefer folder name = repo name (no owner prefix) so owner/repo -> repo
    repo_name = repo_full_name.split("/")[-1]
    target = ROOT_WORKSPACE / repo_name
    if target.is_dir():
        # Already exists; set as current
        s = _load_ui_settings()
        s["current_project_folder"] = str(target)
        _save_ui_settings(s)
        out["path"] = str(target)
        return out
    try:
        subprocess.run(
            [gh, "repo", "clone", repo_full_name, str(target)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT_WORKSPACE),
        )
        if not target.is_dir():
            out["error"] = "Clone failed or path missing"
            return out
        s = _load_ui_settings()
        s["current_project_folder"] = str(target)
        _save_ui_settings(s)
        out["path"] = str(target)
        out["cloned"] = True
    except subprocess.TimeoutExpired:
        out["error"] = "Clone timed out"
    except Exception as e:
        out["error"] = str(e)
    return out


@app.route("/api/project/open", methods=["POST"])
def api_project_open():
    """Open a folder by path (under workspace) or by repo owner/name (clone if not exists). Then set current project and return path."""
    data = request.get_json() or {}
    path_val = (data.get("path") or "").strip()
    repo = (data.get("repo") or "").strip()
    if path_val:
        p = _open_project_path(path_val)
        if p is None:
            return jsonify({"error": "Invalid or inaccessible path"}), 400
        return jsonify({"path": str(p), "cloned": False})
    if repo:
        result = _open_project_repo(repo)
        if result.get("error"):
            return jsonify(result), 400
        return jsonify(result)
    return jsonify({"error": "Provide path or repo"}), 400


@app.route("/api/project/create-folder", methods=["POST"])
def api_project_create_folder():
    """Create a new subfolder under the workspace and return its path. Request: { \"name\": \"folder-name\" } (relative path; no ..)."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip().replace("\\", "/")
    if not name:
        return jsonify({"error": "Folder name is required"}), 400
    parts = [p for p in name.split("/") if p and p not in (".", "..")]
    if not parts:
        return jsonify({"error": "Invalid folder name"}), 400
    # Limit depth to avoid abuse
    if len(parts) > 5:
        return jsonify({"error": "Path too deep (max 5 segments)"}), 400
    relative = "/".join(parts)
    path = ROOT_WORKSPACE / relative
    try:
        path = path.resolve()
        path.relative_to(ROOT_WORKSPACE)
    except (ValueError, OSError):
        return jsonify({"error": "Path must be under workspace"}), 400
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"Cannot create folder: {e}"}), 400
    return jsonify({"path": relative, "absolute": str(path)}), 201


@app.route("/api/ui-settings", methods=["GET", "POST"])
def api_ui_settings():
    """Get or set UI-only settings: default_project_folder, current_project_folder, create_project_on_next_prompt, refresh_interval_ms, sidebar_collapsed_default."""
    if request.method == "GET":
        s = _load_ui_settings()
        s["root_workspace"] = str(ROOT_WORKSPACE)
        return jsonify(s)
    data = request.get_json() or {}
    s = _load_ui_settings()
    for key in ("default_project_folder", "current_project_folder", "create_project_on_next_prompt", "refresh_interval_ms", "sidebar_collapsed_default"):
        if key in data:
            if key == "create_project_on_next_prompt" or key == "sidebar_collapsed_default":
                s[key] = bool(data[key])
            elif key == "refresh_interval_ms":
                v = data[key]
                if isinstance(v, int) and v in (500, 1000, 2000, 3000, 5000):
                    s[key] = v
            else:
                s[key] = data[key] if isinstance(data[key], str) else str(data[key] or "")
    _save_ui_settings(s)
    return jsonify(_load_ui_settings())


def _sessions_json_path() -> Optional[Path]:
    """Path to OpenClaw sessions.json (agent token usage)."""
    openclaw_home = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
    p = openclaw_home / "agents" / "main" / "sessions" / "sessions.json"
    return p if p.is_file() else None


def _session_matches_project(session: dict, project_path: str) -> bool:
    """True if session is associated with the given project path (workspace/cwd)."""
    if not project_path or not project_path.strip():
        return False
    pp = project_path.strip().rstrip("/")
    for key in ("workspace", "workspacePath", "projectPath", "cwd", "path"):
        val = (session.get(key) or "").strip().rstrip("/")
        if val and (val == pp or val.startswith(pp + "/")):
            return True
    return False


@app.route("/api/session-usage")
def api_session_usage():
    """Aggregate input/output token usage from OpenClaw sessions.json.
    Query: scope=all (default) | project; when scope=project pass project_path= for filtering."""
    out = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "sessions": 0}
    path = _sessions_json_path()
    if not path:
        return jsonify(out)
    scope = (request.args.get("scope") or "all").strip().lower()
    project_path = (request.args.get("project_path") or "").strip()
    if scope != "project":
        project_path = ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return jsonify(out)
        for key, session in data.items():
            if not key.startswith("agent:") or not isinstance(session, dict):
                continue
            if scope == "project" and project_path and not _session_matches_project(session, project_path):
                continue
            out["sessions"] += 1
            out["inputTokens"] += int(session.get("inputTokens") or 0)
            out["outputTokens"] += int(session.get("outputTokens") or 0)
        out["totalTokens"] = out["inputTokens"] + out["outputTokens"]
    except (json.JSONDecodeError, OSError):
        pass
    return jsonify(out)


def get_workspace_subfolders() -> List[Dict[str, str]]:
    """Direct subfolders of ROOT_WORKSPACE (one level, dirs only). For custom path dropdown."""
    if not ROOT_WORKSPACE.is_dir():
        return []
    out = []
    try:
        for p in sorted(ROOT_WORKSPACE.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir() and not p.name.startswith(".") and p.name not in ("node_modules", "__pycache__"):
                out.append({"name": p.name, "path": str(p)})
    except (OSError, PermissionError):
        pass
    return out


@app.route("/api/workspace/subfolders")
def api_workspace_subfolders():
    """List direct subfolders of the workspace root for the custom path dropdown."""
    return jsonify(get_workspace_subfolders())


@app.route("/api/file-tree")
def api_file_tree():
    """File tree for sidebar. Optional query: root=path, depth=int. Default root = effective project folder."""
    root_arg = request.args.get("root", "").strip()
    depth = request.args.get("depth", type=int) or 4
    root = Path(root_arg) if root_arg else get_effective_project_folder()
    return jsonify(get_file_tree(root, max_depth=depth))


@app.route("/api/skills/installed")
def api_skills_installed():
    """Installed skills for sidebar browser."""
    return jsonify(get_skills_installed())


@app.route("/api/skills/trending")
def api_skills_trending():
    """Trending topics from last30days for sidebar."""
    return jsonify(get_skills_trending())


def _claude_reader_loop():
    """Background thread: read Claude CLI stdout and append to _claude_output_lines."""
    global _claude_process
    if not _claude_process or not _claude_process.stdout:
        return
    try:
        for line in iter(_claude_process.stdout.readline, ""):
            with _claude_output_lock:
                _claude_output_lines.append(line.rstrip("\n\r"))
    except Exception:
        pass
    finally:
        with _claude_output_lock:
            _claude_output_lines.append("[Claude CLI process ended]")
        _claude_process = None


def _start_claude_cli(dangerously_skip_permissions: bool = False) -> bool:
    """Start Claude CLI subprocess if not already running. Returns True if started or already running."""
    global _claude_process, _claude_skip_permissions, _claude_output_lines
    with _claude_output_lock:
        if _claude_process is not None and _claude_process.poll() is None:
            return True
        _claude_process = None
        _claude_output_lines = []
    claude_bin = shutil.which(CLAUDE_BIN)
    if not claude_bin:
        with _claude_output_lock:
            _claude_output_lines.append(f"[Error: {CLAUDE_BIN} not found in PATH. Install Claude CLI or set CLAUDE_CLI_BIN.]")
        return False
    cmd = [claude_bin, "--dangerously-skip-permissions"] if dangerously_skip_permissions else [claude_bin]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(WORKSPACE),
            env={**os.environ},
        )
        _claude_process = proc
        _claude_skip_permissions = dangerously_skip_permissions
        with _claude_output_lock:
            _claude_output_lines.append(f"[Started: {' '.join(cmd)}]")
        t = threading.Thread(target=_claude_reader_loop, daemon=True)
        t.start()
        return True
    except Exception as e:
        with _claude_output_lock:
            _claude_output_lines.append(f"[Error starting Claude CLI: {e}]")
        return False


@app.route("/api/terminal")
def api_terminal():
    """Get terminal output logs (legacy log view)."""
    status_logs = get_terminal_logs_from_status()
    all_logs = _terminal_logs[-50:] + status_logs[-10:]
    all_logs.sort(key=lambda x: x.get("timestamp", 0))
    return jsonify({"logs": all_logs[-100:]})


@app.route("/api/terminal/log", methods=["POST"])
def api_terminal_log_add():
    """Append a line to the terminal log (e.g. for agent errors or zombie hunter messages)."""
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    log_type = data.get("type", "info")
    if message:
        add_terminal_log(message, log_type)
    return jsonify({"ok": True})


@app.route("/api/terminal/stream")
def api_terminal_stream():
    """SSE stream of integrated Claude CLI output. Auto-starts Claude CLI on first connect.
    Query: dangerously_skip_permissions=1 to add the flag (used when starting)."""
    dangerously = request.args.get("dangerously_skip_permissions", "").lower() in ("1", "true", "yes")
    _start_claude_cli(dangerously_skip_permissions=dangerously)

    def generate():
        last_index = 0
        while True:
            with _claude_output_lock:
                if last_index < len(_claude_output_lines):
                    chunk = _claude_output_lines[last_index:]
                    last_index = len(_claude_output_lines)
                    yield f"data: {json.dumps({'lines': chunk})}\n\n"
            if _claude_process is None or (_claude_process.poll() is not None):
                break
            time.sleep(0.15)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/terminal/input", methods=["POST"])
def api_terminal_input():
    """Send a line to the integrated Claude CLI stdin."""
    global _claude_process
    data = request.get_json() or {}
    line = data.get("line", "")
    if _claude_process is None or _claude_process.poll() is not None:
        return jsonify({"error": "Claude CLI not running"}), 400
    try:
        _claude_process.stdin.write(line + "\n")
        _claude_process.stdin.flush()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bun-log")
def api_bun_log():
    """Get bun log entries."""
    return jsonify({"logs": get_bun_logs()})


def _ensure_create_project_from_prompt(message: str) -> Optional[Path]:
    """If create_project_on_next_prompt is set, create a new folder under ROOT_WORKSPACE from prompt and set it as current. Returns new path or None."""
    s = _load_ui_settings()
    if not s.get("create_project_on_next_prompt"):
        return None
    slug = _slug_from_prompt(message)
    if not slug:
        slug = "project"
    base = ROOT_WORKSPACE / slug
    path = base
    n = 1
    while path.exists():
        n += 1
        path = ROOT_WORKSPACE / f"{slug}-{n}"
    path.mkdir(parents=True, exist_ok=True)
    s["current_project_folder"] = str(path)
    s["create_project_on_next_prompt"] = False
    _save_ui_settings(s)
    add_terminal_log(f"Created project folder: {path.name}", "info")
    return path


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Proxy chat to gateway. Gateway expects { message, history?, system? }. If no project selected, creates one from initial prompt."""
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    # When no folder selected (current empty, default overstory) and this is the initial prompt (no history), create project from prompt
    s = _load_ui_settings()
    has_history = bool(data.get("history"))
    if not has_history and (s.get("current_project_folder") or "").strip() == "" and (s.get("default_project_folder") or "overstory").strip() == "overstory" and (s.get("current_project_folder") or "").strip() != CURRENT_PROJECT_NONE:
        s["create_project_on_next_prompt"] = True
        _save_ui_settings(s)
    # When "create project on next prompt" is set, create folder from this message and enter it
    created_path = _ensure_create_project_from_prompt(message)
    
    add_terminal_log(f"User message: {message}", "user")
    
    gateway_url = f"{GATEWAY_URL.rstrip('/')}/api/chat"
    try:
        payload = {
            "message": message,
            "history": data.get("history", []),
            "system": data.get("system"),
        }
        with httpx.Client(timeout=180.0) as client:
            r = client.post(gateway_url, json=payload)
            r.raise_for_status()
            response_data = r.json()
            add_terminal_log(f"Gateway response received", "info")
            if created_path is not None:
                response_data["created_project"] = created_path.name
            return jsonify(response_data)
    except httpx.ConnectError as e:
        add_terminal_log(f"Gateway connection failed: {str(e)}", "error")
        return jsonify({
            "error": "Gateway unreachable. Start the OverClaw gateway (port 18800) and ensure ZAI_API_KEY is set in .env.",
            "gateway_url": gateway_url,
        }), 502
    except httpx.HTTPStatusError as e:
        add_terminal_log(f"Gateway error {e.response.status_code}: {e.response.text[:200]}", "error")
        return jsonify({"error": f"Gateway {e.response.status_code}", "detail": e.response.text[:500]}), 502
    except Exception as e:
        import traceback
        add_terminal_log(f"Error: {str(e)}", "error")
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/message", methods=["POST"])
def api_message():
    """Unified entry: proxy to gateway /api/message. Mistral analyzes; direct answer, follow-up questions, or handoff to route (spawn)."""
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    s = _load_ui_settings()
    history = data.get("history") or []
    has_history = bool(history)
    if not has_history and (s.get("current_project_folder") or "").strip() == "" and (s.get("default_project_folder") or "overstory").strip() == "overstory" and (s.get("current_project_folder") or "").strip() != CURRENT_PROJECT_NONE:
        s["create_project_on_next_prompt"] = True
        _save_ui_settings(s)
    created_path = _ensure_create_project_from_prompt(message)
    # So agents only read/edit under current project (omit when None or default root)
    s = _load_ui_settings()
    current = (s.get("current_project_folder") or "").strip()
    project_path = None if current == CURRENT_PROJECT_NONE or not current else str(get_effective_project_folder())
    gateway_url = f"{GATEWAY_URL.rstrip('/')}/api/message"
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(
                gateway_url,
                json={
                    "message": message,
                    "history": history,
                    "route_to_agents": data.get("route_to_agents", False),
                    "follow_up_answers": data.get("follow_up_answers"),
                    "context": data.get("context", {}),
                    "project_path": project_path,
                },
            )
            r.raise_for_status()
            out = r.json()
            if created_path is not None:
                out["created_project"] = created_path.name
            return jsonify(out)
    except httpx.ConnectError as e:
        return jsonify({
            "error": "Gateway unreachable. Start the OverClaw gateway (port 18800) and ensure ZAI_API_KEY is set in .env.",
            "detail": str(e),
        }), 502
    except httpx.HTTPStatusError as e:
        try:
            err_body = e.response.json()
            err_msg = err_body.get("error") or e.response.text or str(e)
        except Exception:
            err_msg = (e.response.text or str(e))[:500]
        return jsonify({"error": err_msg, "detail": (e.response.text or "")[:500]}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/route", methods=["POST"])
def api_route():
    """Proxy route/spawn to gateway. Gateway expects { task, spawn?, context?, project_path? }. Creates project from task when no folder selected."""
    data = request.get_json() or {}
    task = data.get("task", "").strip()
    if not task:
        return jsonify({"error": "task is required"}), 400
    s = _load_ui_settings()
    ctx = data.get("context") or {}
    has_history = bool(ctx.get("history"))
    if not has_history and (s.get("current_project_folder") or "").strip() == "" and (s.get("default_project_folder") or "overstory").strip() == "overstory" and (s.get("current_project_folder") or "").strip() != CURRENT_PROJECT_NONE:
        s["create_project_on_next_prompt"] = True
        _save_ui_settings(s)
    created_path = _ensure_create_project_from_prompt(task)
    s = _load_ui_settings()
    current = (s.get("current_project_folder") or "").strip()
    project_path = None if current == CURRENT_PROJECT_NONE or not current else str(get_effective_project_folder())
    gateway_url = f"{GATEWAY_URL.rstrip('/')}/api/route"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                gateway_url,
                json={
                    "task": task,
                    "spawn": data.get("spawn", True),
                    "context": data.get("context", {}),
                    "project_path": project_path,
                },
            )
            r.raise_for_status()
            out = r.json()
            if created_path is not None:
                out["created_project"] = created_path.name
            return jsonify(out)
    except httpx.ConnectError as e:
        return jsonify({"error": f"Gateway connection failed: {str(e)}"}), 502
    except httpx.HTTPStatusError as e:
        return jsonify({"error": f"Gateway {e.response.status_code}", "detail": (e.response.text or "")[:500]}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/agents/<name>/terminal")
def api_agent_terminal(name):
    """Proxy to gateway: get agent tmux terminal output. Always return 200 so frontend can show error in body."""
    try:
        lines = request.args.get("lines", "100")
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{GATEWAY_URL.rstrip('/')}/api/agents/{name}/terminal?lines={lines}")
            try:
                body = r.json()
            except Exception:
                body = {"output": "", "session": f"overstory-overclaw-{name}", "source": "error", "error": r.text or f"HTTP {r.status_code}"}
            # Return 200 so UI can render body; put gateway error in body if 5xx
            if r.status_code >= 400:
                body["error"] = body.get("error") or f"Gateway returned {r.status_code}"
            return jsonify(body), 200
    except Exception as e:
        return jsonify({"output": "", "session": f"overstory-overclaw-{name}", "source": "error", "error": str(e)}), 200


@app.route("/api/agents/<name>/accept-disclaimer", methods=["POST"])
def api_agent_accept_disclaimer(name):
    """Proxy to gateway: send Down+Enter to accept Bypass Permissions disclaimer for this agent."""
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/agents/{name}/accept-disclaimer")
            try:
                body = r.json()
            except Exception:
                body = {"ok": False, "error": r.text or f"HTTP {r.status_code}"}
            return jsonify(body), r.status_code
    except httpx.ConnectError as e:
        return jsonify({"ok": False, "error": f"Gateway unreachable: {e!s}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/agents/<name>/accept-mail-check", methods=["POST"])
def api_agent_accept_mail_check(name):
    """Proxy to gateway: send Down+Enter to accept 'Yes, don't ask again' for overstory mail check prompt."""
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/agents/{name}/accept-mail-check")
            try:
                body = r.json()
            except Exception:
                body = {"ok": False, "error": r.text or f"HTTP {r.status_code}"}
            return jsonify(body), r.status_code
    except httpx.ConnectError as e:
        return jsonify({"ok": False, "error": f"Gateway unreachable: {e!s}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/agents/restart-with-skip-permissions", methods=["POST"])
def api_agents_restart_with_skip_permissions():
    """Proxy to gateway: enable skip-permissions, C-c C-c all agents, restart claude with flag."""
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/agents/restart-with-skip-permissions")
            body = r.json() if r.content else {}
            if r.status_code != 200 and "error" not in body:
                body["ok"] = False
                body["error"] = body.get("error") or f"Gateway returned {r.status_code}"
            return jsonify(body), 200
    except httpx.ConnectError:
        return jsonify({"ok": False, "error": "Gateway unreachable (start OverClaw gateway on port 18800)", "restarted": []}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "restarted": []}), 200


@app.route("/api/agents/auto-accept-prompts", methods=["POST"])
def api_agents_auto_accept_prompts():
    """Proxy to gateway: auto-accept confirm prompts for all agents (send Enter)."""
    try:
        with httpx.Client(timeout=25.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/agents/auto-accept-prompts")
            body = r.json() if r.content else {}
            if r.status_code != 200 and "error" not in body:
                body["ok"] = False
                body["error"] = body.get("error") or f"Gateway returned {r.status_code}"
            return jsonify(body), 200
    except httpx.ConnectError:
        return jsonify({"ok": False, "error": "Gateway unreachable", "accepted": []}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "accepted": []}), 200


@app.route("/api/worktrees/clean", methods=["POST"])
def api_worktrees_clean():
    """Proxy to gateway: overstory worktree clean --completed."""
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/worktrees/clean")
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/supervisor/approve-all", methods=["POST"])
def api_supervisor_approve_all():
    """Proxy to gateway: process all pending approval mail (unblock builders when no lead)."""
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/supervisor/approve-all")
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "approved": 0}), 200


@app.route("/api/supervisor/inject-lead", methods=["POST"])
def api_supervisor_inject_lead():
    """Proxy to gateway: spawn a lead and reassign unread lead/supervisor mail to it."""
    try:
        with httpx.Client(timeout=130.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/supervisor/inject-lead")
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "lead_name": "", "mail_reassigned": 0}), 200


@app.route("/api/agents/spawn", methods=["POST"])
def api_agents_spawn():
    """Proxy to gateway: spawn an overstory agent (e.g. approval-supervisor for cleanup)."""
    try:
        with httpx.Client(timeout=120.0) as client:
            body = request.get_json() or {}
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/agents/spawn", json=body)
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 200


@app.route("/api/overstory/merge", methods=["POST"])
def api_overstory_merge():
    """Proxy to gateway: overstory merge --all (drain merge queue)."""
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/overstory/merge")
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e), "drained": 0}), 200


@app.route("/api/zombies")
def api_zombies():
    """Proxy to gateway: list zombie agents. Always return 200 so UI doesn't break; errors in body."""
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{GATEWAY_URL.rstrip('/')}/api/zombies")
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"zombies": [], "count": 0}
            if r.status_code >= 400:
                data.setdefault("error", f"Gateway returned {r.status_code}")
                data["zombies"] = data.get("zombies", [])
                data["count"] = data.get("count", 0)
            return jsonify(data), 200
    except httpx.ConnectError as e:
        return jsonify({
            "error": f"Gateway unreachable at {GATEWAY_URL}. Is it running?",
            "zombies": [],
            "count": 0,
        }), 200
    except Exception as e:
        return jsonify({"error": str(e), "zombies": [], "count": 0}), 200


@app.route("/api/zombies/slay", methods=["POST"])
def api_zombies_slay():
    """Proxy to gateway: slay zombie agents (clean --worktrees)."""
    try:
        with httpx.Client(timeout=35.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/zombies/slay")
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e), "slain": 0}), 500


@app.route("/api/agents/kill-all", methods=["POST"])
def api_agents_kill_all():
    """Proxy to gateway: kill all agents, clear mail and task queue (fresh start)."""
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/agents/kill-all")
            text = (r.text or "").strip()
            if not text:
                return jsonify({
                    "ok": False,
                    "error": "Gateway returned empty response (is it running?)",
                    "cleaned": False,
                    "mail_cleared": 0,
                }), r.status_code if r.status_code >= 400 else 502
            try:
                data = r.json()
            except ValueError:
                preview = (text[:200] + "…") if len(text) > 200 else text
                return jsonify({
                    "ok": False,
                    "error": "Gateway returned invalid JSON. Response preview: " + (preview or "(empty)"),
                    "cleaned": False,
                    "mail_cleared": 0,
                }), 502
            return jsonify(data), r.status_code
    except httpx.ConnectError as e:
        return jsonify({
            "ok": False,
            "error": f"Gateway unreachable at {GATEWAY_URL}. Is it running?",
            "cleaned": False,
            "mail_cleared": 0,
        }), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "cleaned": False, "mail_cleared": 0}), 500


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """Proxy to gateway: get or set dangerously_skip_permissions (UI toggle controls agents)."""
    try:
        with httpx.Client(timeout=5.0) as client:
            if request.method == "POST":
                data = request.get_json() or {}
                r = client.post(f"{GATEWAY_URL.rstrip('/')}/api/settings", json=data)
            else:
                r = client.get(f"{GATEWAY_URL.rstrip('/')}/api/settings")
            return jsonify(r.json()), r.status_code
    except Exception as e:
        out = {"error": str(e)}
        if request.method == "GET":
            out["dangerously_skip_permissions"] = True
        return jsonify(out), 500


@app.route("/api/gateway/health")
def api_gateway_health():
    """Check gateway connectivity."""
    try:
        r = httpx.get(f"{GATEWAY_URL.rstrip('/')}/health", timeout=5.0)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/gateway/status")
def api_gateway_status():
    """Proxy gateway full status (orchestrator, overstory, etc.) for setup wizard."""
    try:
        r = httpx.get(f"{GATEWAY_URL.rstrip('/')}/api/status", timeout=10.0)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e), "orchestrator": {}, "overstory": {}})


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

@app.route("/static/pa/<path:filename>")
def pa_static(filename):
    return send_from_directory(os.path.join(_REPO_ROOT, "assets", "packs", "postapocalypse"), filename)

@app.route("/static/sw/<path:filename>")
def sw_static(filename):
    return send_from_directory(os.path.join(_REPO_ROOT, "assets", "packs", "sunnyside"), filename)

@app.route("/static/tf/<path:filename>")
def tf_static(filename):
    return send_from_directory(os.path.join(_REPO_ROOT, "assets", "packs", "tinyfarm"), filename)

@app.route("/static/farm/<path:filename>")
def farm_static(filename):
    return send_from_directory(os.path.join(_REPO_ROOT, "assets", "packs", "Farm"), filename)

# Single-page HTML: Overstory-style left + tabbed Terminal/Output right
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>overstory dashboard v0.2.0</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html { height: 100%; }
    body {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      background: #0d1117;
      color: #e6edf3;
      height: 100%;
      min-height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .header {
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 8px 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 13px;
      flex-shrink: 0;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .header-title {
      font-weight: 600;
      color: #58a6ff;
    }
    .header-time {
      color: #8b949e;
    }
    .header-refresh {
      color: #8b949e;
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .header-controls {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .header-controls button {
      background: transparent;
      border: 1px solid #30363d;
      color: #8b949e;
      padding: 4px 8px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 11px;
    }
    .header-controls button:hover {
      background: #21262d;
      color: #e6edf3;
    }
    .header-controls button.header-btn-danger {
      color: #f85149;
      border-color: #f85149;
    }
    .header-controls button.header-btn-danger:hover {
      background: rgba(248, 81, 73, 0.15);
      color: #ff7b72;
    }
    .repo-dropdown-wrap {
      position: relative;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .github-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      background: #21262d;
      border: 1px solid #30363d;
      border-radius: 6px;
      color: #8b949e;
      font-size: 12px;
      text-decoration: none;
      cursor: pointer;
    }
    .github-btn:hover { background: #30363d; color: #e6edf3; border-color: #484f58; }
    .github-btn.connected { color: #7ee787; border-color: #238636; }
    .github-btn .github-icon { width: 18px; height: 18px; flex-shrink: 0; }
    .github-btn .github-avatar { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
    .github-btn .github-username { max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .github-login-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 9999;
      align-items: center;
      justify-content: center;
    }
    .github-login-modal.visible { display: flex; }
    .github-login-panel {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 20px;
      max-width: 380px;
      width: 90%;
    }
    .github-login-panel h3 { margin-bottom: 12px; font-size: 14px; color: #e6edf3; }
    .github-login-panel p { font-size: 12px; color: #8b949e; margin-bottom: 12px; }
    .github-login-panel code { background: #0d1117; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
    .github-login-actions { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
    .github-login-actions button {
      padding: 8px 12px;
      font-size: 12px;
      background: #238636;
      color: #fff;
      border: none;
      border-radius: 6px;
      cursor: pointer;
    }
    .github-login-actions button.secondary { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
    .github-login-actions button.secondary:hover { background: #30363d; color: #e6edf3; }
    .github-login-actions button:hover { background: #2ea043; }
    .github-login-close { position: absolute; top: 12px; right: 12px; background: none; border: none; color: #8b949e; cursor: pointer; font-size: 18px; padding: 0; line-height: 1; }
    .github-login-close:hover { color: #e6edf3; }
    .open-folder-wrap { position: relative; display: inline-block; }
    .open-folder-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      background: #21262d;
      border: 1px solid #30363d;
      border-radius: 6px;
      color: #e6edf3;
      font-size: 12px;
      cursor: pointer;
      max-width: 220px;
    }
    .open-folder-btn:hover { background: #30363d; color: #e6edf3; }
    .open-folder-btn svg { width: 16px; height: 16px; flex-shrink: 0; }
    .open-folder-btn .repo-dropdown-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .open-folder-btn .repo-dropdown-chevron { margin-left: 2px; opacity: 0.7; font-size: 10px; }
    .of-loading { padding: 12px; font-size: 12px; color: #8b949e; text-align: center; }
    .of-loading::after { content: ''; display: inline-block; width: 14px; height: 14px; margin-left: 8px; vertical-align: middle; border: 2px solid #30363d; border-top-color: #58a6ff; border-radius: 50%; animation: of-spin 0.7s linear infinite; }
    @keyframes of-spin { to { transform: rotate(360deg); } }
    .of-signin-btn {
      display: block;
      width: 100%;
      padding: 10px 12px;
      margin: 4px 0;
      background: #238636;
      color: #fff;
      border: none;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      text-align: center;
    }
    .of-signin-btn:hover { background: #2ea043; color: #fff; }
    .open-folder-dropdown {
      display: none;
      position: absolute;
      top: 100%;
      left: 0;
      margin-top: 4px;
      min-width: 220px;
      max-height: 320px;
      overflow-y: auto;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.4);
      z-index: 1000;
    }
    .open-folder-dropdown.visible { display: block; }
    .open-folder-dropdown .of-item {
      display: block;
      width: 100%;
      padding: 8px 12px;
      text-align: left;
      background: none;
      border: none;
      color: #e6edf3;
      font-size: 12px;
      cursor: pointer;
      border-radius: 0;
    }
    .open-folder-dropdown .of-item:hover { background: #21262d; }
    .open-folder-dropdown .of-divider { height: 1px; background: #30363d; margin: 4px 0; }
    .open-folder-dropdown .of-head { padding: 6px 12px; font-size: 10px; color: #8b949e; text-transform: uppercase; }
    .browse-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 9999;
      align-items: center;
      justify-content: center;
    }
    .browse-modal.visible { display: flex; }
    .browse-modal-panel {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 16px;
      width: 90%;
      max-width: 400px;
      max-height: 70vh;
      display: flex;
      flex-direction: column;
    }
    .browse-modal-panel h3 { margin-bottom: 12px; font-size: 14px; color: #e6edf3; }
    .browse-modal-tree { flex: 1; min-height: 200px; overflow: auto; margin-bottom: 12px; }
    .browse-modal-tree .tree-item { cursor: pointer; }
    .sidebar {
      width: 260px;
      min-width: 260px;
      background: #161b22;
      border-right: 1px solid #30363d;
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      transition: width 0.2s, min-width 0.2s;
      overflow: hidden;
    }
    .sidebar.collapsed {
      width: 40px;
      min-width: 40px;
    }
    .sidebar.collapsed .sidebar-content { display: none; }
    .sidebar-toggle {
      flex-shrink: 0;
      width: 40px;
      height: 36px;
      background: transparent;
      border: none;
      border-bottom: 1px solid #30363d;
      color: #8b949e;
      cursor: pointer;
      font-size: 16px;
      padding: 0;
    }
    .sidebar-toggle:hover { background: #21262d; color: #e6edf3; }
    .sidebar-content {
      flex: 1;
      min-height: 0;
      overflow: auto;
      padding: 8px;
    }
    .sidebar-section {
      margin-bottom: 16px;
    }
    .sidebar-section-title {
      font-size: 11px;
      font-weight: 600;
      color: #8b949e;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 6px;
      padding: 4px 0;
    }
    .file-tree ul, .skills-list { list-style: none; padding-left: 0; margin: 0; }
    .file-tree li { padding: 2px 0; font-size: 12px; }
    .file-tree .tree-item {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 2px 6px;
      border-radius: 4px;
      cursor: pointer;
      color: #8b949e;
    }
    .file-tree .tree-item:hover { background: #21262d; color: #e6edf3; }
    .file-tree .tree-item .tree-arrow { width: 14px; text-align: center; font-size: 10px; color: #6e7681; }
    .file-tree .tree-item.dir .tree-arrow::before { content: '▶'; }
    .file-tree .tree-item.dir.open .tree-arrow::before { content: '▼'; }
    .file-tree .tree-item-project { font-weight: 600; color: #58a6ff; }
    .file-tree .file-tree-children { padding-left: 4px; }
    .skills-browser-tabs { display: flex; gap: 4px; margin-bottom: 8px; }
    .skills-browser-tabs button {
      padding: 4px 10px;
      font-size: 11px;
      background: #21262d;
      border: 1px solid #30363d;
      color: #8b949e;
      border-radius: 4px;
      cursor: pointer;
    }
    .skills-browser-tabs button:hover { color: #e6edf3; }
    .skills-browser-tabs button.active { background: #238636; color: #fff; border-color: #238636; }
    .skill-item, .trending-item {
      padding: 6px 8px;
      font-size: 11px;
      border-radius: 4px;
      margin-bottom: 4px;
      background: #21262d;
      color: #8b949e;
      border: 1px solid transparent;
    }
    .skill-item:hover, .trending-item:hover { background: #30363d; color: #e6edf3; }
    .skill-item .skill-name { font-weight: 600; color: #58a6ff; }
    .trending-item .trending-name { font-weight: 600; color: #a371f7; }
    .project-settings { font-size: 11px; }
    .project-setting-row { margin-bottom: 6px; display: block; }
    .project-setting-label { display: block; color: #8b949e; margin-bottom: 2px; }
    .project-select, .project-path-input {
      width: 100%;
      padding: 4px 8px;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 4px;
      color: #e6edf3;
      font-size: 11px;
    }
    .project-create-folder-btn {
      margin-top: 4px;
      padding: 4px 8px;
      font-size: 11px;
      background: #238636;
      border: 1px solid #2ea043;
      color: #fff;
      border-radius: 4px;
      cursor: pointer;
    }
    .project-create-folder-btn:hover { background: #2ea043; }
    #showNewFolderBtn { width: 100%; margin-top: 2px; }
    .project-new-folder-row { display: flex; align-items: center; gap: 6px; }
    .project-new-folder-row .project-path-input { flex: 1; min-width: 0; margin-top: 0; }
    .project-new-create-btn { flex-shrink: 0; margin-top: 0; }
    .project-new-btn {
      margin-top: 8px;
      width: 100%;
      padding: 6px 8px;
      font-size: 11px;
      background: #21262d;
      border: 1px solid #30363d;
      color: #58a6ff;
      border-radius: 4px;
      cursor: pointer;
    }
    .project-new-btn:hover { background: #30363d; color: #e6edf3; }
    .main-layout {
      display: flex;
      flex: 1;
      min-height: 0;
    }
    .main-content { flex: 1; min-width: 0; display: flex; min-height: 0; }
    .left {
      flex: 0 0 58%;
      min-width: 0;
      padding: 12px;
      overflow: hidden;
      border-right: 1px solid #30363d;
      display: flex;
      flex-direction: column;
    }
    .left-top {
      flex: 1;
      min-height: 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    #leftPanelAgents {
      flex: 1;
      min-height: 120px;
      min-width: 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      margin-bottom: 12px;
    }
    #leftPanelAgents .panel-body {
      flex: 1 1 0;
      min-height: 0;
      overflow-y: auto;
      overflow-x: auto;
      max-height: none;
      display: block;
      -webkit-overflow-scrolling: touch;
      padding: 0;
      line-height: 0;
    }
    #leftPanelAgents .agents-table {
      margin: 0;
      border-collapse: collapse;
      border-spacing: 0;
      line-height: normal;
      vertical-align: top;
    }
    #leftPanelAgents .agents-table th,
    #leftPanelAgents .agents-table td {
      padding: 4px 8px;
    }
    #leftPanelAgents .agents-table .task-cell { max-width: 220px; font-size: 11px; }
    #leftPanelAgents .agents-table .task-summary { cursor: pointer; max-width: 200px; }
    #leftPanelAgents .agents-table .task-full { max-height: 120px; overflow: auto; }
    #leftPanelMail, #leftPanelMerge, #leftPanelMetrics {
      flex: 0 0 auto;
      margin-bottom: 12px;
    }
    #leftPanelMail .panel-body { height: 72px; max-height: 72px; overflow: auto; }
    #leftPanelMerge .panel-body { height: 40px; max-height: 40px; overflow: hidden; }
    #leftPanelMetrics .panel-body { height: 40px; max-height: 40px; overflow: hidden; }
    #leftPanelTerminalLog { flex: 0 0 auto; height: 200px; max-height: 200px; margin-bottom: 12px; overflow: hidden; }
    #leftPanelTerminalLog .panel-body { height: 172px; max-height: 172px; overflow: auto; }
    #leftPanelBunLog { flex: 0 0 auto; height: 56px; max-height: 56px; overflow: hidden; }
    #leftPanelBunLog .bun-log { height: 36px; max-height: 36px; overflow: auto; }
    .right {
      flex: 1 1 42%;
      min-width: 0;
      display: flex;
      flex-direction: column;
      border-left: 1px solid #30363d;
    }
    .panel {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      margin-bottom: 12px;
      overflow: hidden;
    }
    .panel-header {
      padding: 8px 12px;
      font-weight: 600;
      border-bottom: 1px solid #30363d;
      color: #58a6ff;
      font-size: 12px;
    }
    .panel-body {
      padding: 10px;
      font-size: 12px;
      white-space: pre-wrap;
    }
    .panel-body.mail-body {
      font-family: inherit;
      white-space: normal;
    }
    .mail-item {
      padding: 6px 8px;
      border-bottom: 1px solid #21262d;
      font-size: 11px;
      cursor: pointer;
    }
    .mail-item:hover {
      background: #21262d;
    }
    .mail-item:last-child {
      border-bottom: none;
    }
    .agents-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .agents-table th, .agents-table td {
      padding: 4px 8px;
      text-align: left;
      border-bottom: 1px solid #21262d;
    }
    .agents-table th { color: #8b949e; font-weight: 600; }
    .agents-table .task-cell { max-width: 220px; font-size: 11px; }
    .agents-table .task-details { margin: 0; }
    .agents-table .task-summary { cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; display: inline-block; }
    .agents-table .task-full { margin: 4px 0 0 0; padding: 6px; font-size: 10px; white-space: pre-wrap; word-break: break-word; max-height: 120px; overflow: auto; background: #21262d; border-radius: 4px; }
    .bun-log {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 10px;
      font-family: inherit;
      max-height: 56px;
      overflow-y: auto;
      flex-shrink: 0;
    }
    .bun-log-header {
      padding: 4px 0 2px 0;
      font-weight: 600;
      color: #8b949e;
      font-size: 10px;
    }
    .bun-log-line {
      margin-bottom: 1px;
      color: #8b949e;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .tabs {
      display: flex;
      background: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 0 8px;
      gap: 4px;
      flex-shrink: 0;
    }
    .tab {
      padding: 10px 16px;
      cursor: pointer;
      color: #8b949e;
      font-size: 13px;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
    }
    .tab:hover { color: #e6edf3; }
    .tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
    .tab-content {
      display: none;
      flex: 1;
      flex-direction: column;
      min-height: 0;
      padding: 12px;
      overflow: auto;
      font-size: 12px;
      font-family: inherit;
    }
    .tab-content.active {
      display: flex;
    }
    .chat-tab-split {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .chat-tab-split .chat-top-half,
    .chat-tab-split .chat-bottom-half {
      min-height: 0;
    }
    .chat-tab-split .chat-top-half {
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .chat-tab-split .chat-bottom-half {
      overflow: hidden;
    }
    #agentTerminalsList {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
    }
    #agentTerminalsList .agent-terminal-item {
      flex: 0 0 auto;
      min-height: 0;
    }
    .agent-terminal-item.agent-terminal-lead .agent-terminal-body,
    .agent-terminal-item.agent-terminal-supervisor .agent-terminal-body {
      display: none;
    }
    .agent-terminal-item.agent-terminal-lead.agent-terminal-expanded .agent-terminal-body,
    .agent-terminal-item.agent-terminal-supervisor.agent-terminal-expanded .agent-terminal-body {
      display: block;
    }
    .agent-terminal-output {
      height: 200px;
      max-height: 200px;
      overflow-y: auto;
      overflow-x: hidden;
      -webkit-overflow-scrolling: touch;
      overflow-anchor: none;
      padding-bottom: 12px;
      display: block;
    }
    .agent-terminal-item.agent-terminal-expanded .agent-terminal-output {
      height: auto;
      min-height: 400px;
      max-height: min(75vh, 700px);
    }
    .agent-terminal-output {
      scroll-behavior: auto;
    }
    .agent-terminal-item.agent-terminal-active {
      border-color: #58a6ff;
      box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.35);
    }
    .chat-history-scroll {
      -webkit-overflow-scrolling: touch;
    }
    .terminal-output {
      background: #0d1117;
      color: #e6edf3;
      font-family: inherit;
      white-space: pre-wrap;
      word-wrap: break-word;
      flex: 1;
      overflow: auto;
      padding: 8px;
      border: 1px solid #30363d;
      border-radius: 4px;
    }
    .kanban-board {
      display: flex;
      gap: 12px;
      padding: 12px;
      min-height: 0;
      overflow-x: auto;
      overflow-y: hidden;
      align-items: flex-start;
    }
    .kanban-column {
      flex: 0 0 200px;
      min-width: 200px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      max-height: 100%;
    }
    .kanban-column-header {
      padding: 10px 12px;
      font-weight: 600;
      font-size: 12px;
      color: #58a6ff;
      border-bottom: 1px solid #30363d;
      border-radius: 8px 8px 0 0;
      background: #21262d;
    }
    .kanban-column-cards {
      padding: 8px;
      overflow-y: auto;
      min-height: 80px;
      flex: 1;
    }
    .kanban-card {
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 10px;
      margin-bottom: 8px;
      font-size: 12px;
      cursor: default;
    }
    .kanban-card:last-child { margin-bottom: 0; }
    .kanban-card-name { font-weight: 600; color: #e6edf3; margin-bottom: 4px; }
    .kanban-card-state { color: #8b949e; font-size: 11px; }
    .kanban-card-duration { color: #7ee787; font-size: 11px; }
    .kanban-card-task { color: #8b949e; font-size: 11px; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
    .kanban-card.zombie { border-color: #f85149; background: #1c1212; }
    .kanban-card .state-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }
    .kanban-card .state-dot.working { background: #7ee787; }
    .kanban-card .state-dot.idle { background: #8b949e; }
    .kanban-card .state-dot.zombie { background: #f85149; }
    .terminal-line {
      margin-bottom: 4px;
    }
    .terminal-line.info { color: #e6edf3; }
    .terminal-line.user { color: #58a6ff; }
    .terminal-line.error { color: #f85149; }
    .terminal-line.success { color: #7ee787; }
    button {
      padding: 8px 16px;
      background: #238636;
      color: #fff;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
    }
    button:hover { background: #2ea043; }
    button:disabled { background: #21262d; color: #8b949e; cursor: not-allowed; }
    .chat-footer { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
    .route-toggle { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 12px; color: #8b949e; cursor: pointer; }
    .route-toggle input { cursor: pointer; }
    .chat-status { color: #58a6ff; font-size: 13px; font-weight: 500; min-height: 1.2em; }
    .thinking-row { color: #58a6ff; font-style: italic; }
    .thinking-row .dot { display: inline-block; width: 4px; height: 4px; background: #58a6ff; border-radius: 50%; margin-left: 2px; animation: pulse 0.8s ease-in-out infinite; }
    .thinking-row .dot:nth-child(2) { animation-delay: 0.2s; }
    .thinking-row .dot:nth-child(3) { animation-delay: 0.4s; }
    .thinking-console { opacity: 0.45; font-family: var(--mono-font, ui-monospace, monospace); font-size: 11px; color: #8b949e; padding: 3px 0; min-height: 1.2em; }
    .thinking-console.active { display: block; }
    .thinking-console:not(.active) { display: none; }
    @keyframes pulse { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
    .claude-cli-section { padding: 12px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; }
    .claude-cli-header { font-weight: 600; margin-bottom: 8px; color: #e6edf3; }
    .claude-cmd-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; flex-wrap: wrap; }
    .claude-cmd { flex: 1; min-width: 200px; padding: 8px 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 4px; font-size: 13px; }
    .claude-cli-note { font-size: 11px; color: #8b949e; margin: 8px 0 0; }
    .terminal-input-row { display: flex; gap: 8px; margin-top: 8px; flex-shrink: 0; }
    .terminal-input { flex: 1; padding: 8px 12px; background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 4px; font-family: inherit; font-size: 13px; }
    .terminal-input:focus { outline: none; border-color: #58a6ff; }
    .terminal-current-path {
      font-size: 11px;
      color: #8b949e;
      padding: 4px 0 6px 0;
      margin: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .terminal-current-path span { color: #58a6ff; }
    .footer {
      flex-shrink: 0;
      background: #161b22;
      border-top: 1px solid #30363d;
      padding: 4px 12px;
      display: flex;
      align-items: center;
      font-size: 11px;
      color: #8b949e;
      min-height: 24px;
      gap: 12px;
    }
    .footer-left { flex-shrink: 0; }
    .footer-center { flex: 1; display: flex; justify-content: center; align-items: center; min-width: 0; }
    .footer-right { flex-shrink: 0; display: flex; align-items: center; gap: 12px; margin-left: auto; }
    .footer-tokens {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .footer-token-toggle { display: flex; gap: 0; }
    .footer-token-scope {
      padding: 2px 6px;
      font-size: 10px;
      background: #21262d;
      border: 1px solid #30363d;
      color: #8b949e;
      cursor: pointer;
      border-radius: 3px;
    }
    .footer-token-scope:first-child { border-radius: 3px 0 0 3px; border-right-width: 0; }
    .footer-token-scope:last-child { border-radius: 0 3px 3px 0; }
    .footer-token-scope:hover { color: #e6edf3; background: #30363d; }
    .footer-token-scope.active { background: #388bfd; color: #fff; border-color: #388bfd; }
    .footer-tokens span { color: #e6edf3; }
    .footer-tokens .token-in { color: #58a6ff; }
    .footer-tokens .token-out { color: #7ee787; }
    .footer-gateway.ok { color: #7ee787; }
    .footer-gateway.not-ok { color: #f85149; }
    .footer-zombie {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 11px;
      color: #8b949e;
    }
    .footer-zombie .footer-zombie-label { margin-right: 2px; }
    .footer-zombie .footer-zombie-value { color: #7ee787; font-weight: 600; }
    .footer-zombie .footer-zombie-detected { color: #f85149; }
    .footer-zombie .footer-zombie-btn {
      padding: 2px 8px;
      font-size: 10px;
      background: #21262d;
      color: #f85149;
      border: 1px solid #30363d;
      border-radius: 4px;
      cursor: pointer;
      font-weight: 600;
    }
    .footer-zombie .footer-zombie-btn:hover { background: #30363d; color: #ff7b72; }
    .footer-settings-btn {
      background: transparent;
      border: 1px solid #30363d;
      color: #8b949e;
      padding: 4px 10px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 11px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .footer-settings-btn:hover { background: #21262d; color: #e6edf3; }
    .settings-modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      z-index: 9999;
      align-items: center;
      justify-content: center;
    }
    .settings-modal.visible { display: flex; }
    .settings-panel {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 20px;
      max-width: 420px;
      width: 90%;
      max-height: 85vh;
      overflow-y: auto;
    }
    .settings-panel h3 { margin-bottom: 16px; font-size: 14px; color: #58a6ff; }
    .settings-section { margin-bottom: 16px; }
    .settings-section-title { font-size: 11px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
    .settings-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
    .settings-row label { font-size: 12px; color: #e6edf3; flex: 1; }
    .settings-row select, .settings-row input[type="number"] {
      padding: 6px 10px;
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 4px;
      color: #e6edf3;
      font-size: 12px;
      min-width: 100px;
    }
    .settings-panel .settings-close {
      position: absolute;
      top: 12px;
      right: 12px;
      background: none;
      border: none;
      color: #8b949e;
      cursor: pointer;
      font-size: 18px;
      padding: 0;
      line-height: 1;
    }
    .settings-panel .settings-close:hover { color: #e6edf3; }
    .settings-panel .settings-actions { margin-top: 16px; display: flex; gap: 8px; justify-content: flex-end; }
    .settings-panel .settings-actions button { padding: 6px 14px; font-size: 12px; }
    .settings-panel .settings-actions button.secondary { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
    .settings-panel .settings-actions button.secondary:hover { background: #30363d; color: #e6edf3; }
    .nanoclaw-wizard-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: none; align-items: center; justify-content: center; z-index: 10000; }
    .nanoclaw-wizard-modal.visible { display: flex; }
    .nanoclaw-wizard-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; width: 90%; max-width: 560px; max-height: 85vh; display: flex; flex-direction: column; overflow: hidden; }
    .nanoclaw-wizard-panel h3 { padding: 12px 16px; border-bottom: 1px solid #30363d; font-size: 14px; color: #58a6ff; }
    .nanoclaw-wizard-console { flex: 1; min-height: 220px; max-height: 360px; overflow-y: auto; padding: 12px; font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.45; background: #0d1117; color: #e6edf3; }
    .nanoclaw-wizard-console .wizard-line { white-space: pre-wrap; word-break: break-word; padding: 2px 0; }
    .nanoclaw-wizard-console .wizard-ts { color: #6e7681; margin-right: 8px; }
    .nanoclaw-wizard-console .wizard-ok { color: #7ee787; }
    .nanoclaw-wizard-console .wizard-fail { color: #f85149; }
    .nanoclaw-wizard-console .wizard-pending { color: #8b949e; }
    .nanoclaw-wizard-actions { padding: 12px 16px; border-top: 1px solid #30363d; display: flex; gap: 8px; justify-content: flex-end; }
    .nanoclaw-wizard-actions button { padding: 8px 16px; font-size: 12px; border-radius: 6px; cursor: pointer; border: 1px solid #30363d; background: #21262d; color: #e6edf3; }
    .nanoclaw-wizard-actions button.primary { background: #238636; border-color: #238636; }
    .nanoclaw-wizard-actions button.primary:hover { background: #2ea043; }
    .nanoclaw-wizard-actions button.primary:disabled { opacity: 0.6; cursor: not-allowed; }
    .nanoclaw-wizard-actions button:hover { background: #30363d; }

    .ceo-fullscreen { position: fixed; inset: 0; z-index: 10001; background: #0d1117; display: none; flex-direction: column; align-items: stretch; justify-content: stretch; }
    .ceo-fullscreen.visible { display: flex; }
    .ceo-fullscreen #ceoCanvas { flex: 1; width: 100%; height: 100%; display: block; }
    .ceo-close { position: absolute; top: 12px; right: 12px; z-index: 10002; width: 36px; height: 36px; border-radius: 6px; border: 1px solid #30363d; background: #21262d; color: #e6edf3; font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
    .ceo-close:hover { background: #30363d; }
    .ceo-theme-select { position: absolute; top: 12px; left: 12px; z-index: 10002; padding: 6px 10px; border-radius: 6px; border: 1px solid #30363d; background: #21262d; color: #e6edf3; font-size: 12px; font-family: inherit; cursor: pointer; }
    .ceo-theme-select:hover { background: #30363d; }
    .ceo-credit { position: absolute; bottom: 8px; right: 12px; z-index: 10002; font-size: 10px; color: rgba(139,148,158,0.5); pointer-events: none; }
    .agent-dialog { position: absolute; bottom: 24px; left: 50%; transform: translateX(-50%); z-index: 10003; width: 90%; max-width: 640px; background: rgba(22,27,34,0.95); border: 2px solid #58a6ff; border-radius: 8px; padding: 16px; display: flex; gap: 16px; align-items: flex-start; cursor: pointer; image-rendering: pixelated; box-shadow: 0 0 20px rgba(88,166,255,0.15); }
    .agent-dialog-portrait { width: 80px; height: 80px; flex-shrink: 0; image-rendering: pixelated; border: 1px solid #30363d; border-radius: 4px; background: #161b22; overflow: hidden; }
    .agent-dialog-portrait canvas { width: 100%; height: 100%; image-rendering: pixelated; display: block; }
    .agent-dialog-content { flex: 1; min-width: 0; }
    .agent-dialog-name { font-size: 13px; font-weight: 600; color: #58a6ff; margin-bottom: 6px; font-family: ui-monospace, monospace; }
    .agent-dialog-name .agent-dialog-role { font-size: 11px; font-weight: 400; color: #8b949e; margin-left: 8px; }
    .agent-dialog-text { font-size: 12px; color: #e6edf3; font-family: ui-monospace, monospace; line-height: 1.6; min-height: 40px; white-space: pre-wrap; }
    .agent-dialog-hint { font-size: 10px; color: #484f58; margin-top: 8px; text-align: right; }

    /* --- Mobile: single-column chat-first layout (like conversation UI) --- */
    @media (max-width: 768px) {
      html { height: 100%; min-height: 100dvh; min-height: -webkit-fill-available; }
      body {
        overflow: hidden;
        height: 100%;
        min-height: 100dvh;
        min-height: 100vh;
        min-height: -webkit-fill-available;
      }
      .header {
        flex-wrap: nowrap;
        gap: 6px;
        padding: 6px 8px;
        min-width: 0;
      }
      .header-left {
        flex: 1;
        min-width: 0;
        gap: 6px;
        overflow: hidden;
      }
      .header-title { font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 80px; }
      .header-time, .header-refresh { display: none; }
      .header-controls {
        flex-shrink: 0;
        gap: 4px;
      }
      .header-controls button {
        padding: 4px 6px;
        font-size: 10px;
      }
      .header-controls .header-btn-long { display: none; }
      .repo-dropdown-wrap { min-width: 0; overflow: hidden; }
      .github-btn {
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
      }
      .github-btn .github-username { max-width: 60px; }
      .open-folder-btn {
        min-width: 0;
        max-width: 90px;
        overflow: hidden;
      }
      .open-folder-btn .repo-dropdown-label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .main-layout {
        flex-direction: column;
        flex: 1;
        min-height: 0;
        overflow: hidden;
      }
      .sidebar {
        position: fixed;
        top: 0;
        left: -280px;
        bottom: 0;
        width: 280px;
        min-width: 280px;
        z-index: 1001;
        box-shadow: 4px 0 20px rgba(0,0,0,0.4);
        transition: left 0.2s ease;
      }
      .sidebar.mobile-open { left: 0; }
      .sidebar-overlay {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,0.5);
        z-index: 1000;
      }
      .sidebar-overlay.visible { display: block; }
      .main-content {
        flex-direction: column;
        flex: 1;
        min-height: 0;
        width: 100%;
        overflow: hidden;
        min-width: 0;
      }
      .left {
        display: none;
        flex: none;
        width: 100%;
        max-height: 40vh;
        overflow: auto;
        padding: 10px;
        border-right: none;
        border-bottom: 1px solid #30363d;
      }
      .left.mobile-panels-open {
        display: flex;
        flex-direction: column;
      }
      .right {
        flex: 1;
        width: 100%;
        min-width: 0;
        border-left: none;
        display: flex;
        flex-direction: column;
        min-height: 0;
        overflow: hidden;
      }
      .tabs {
        padding: 6px 8px;
        gap: 2px;
        flex-wrap: nowrap;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        min-width: 0;
        flex-shrink: 0;
      }
      .tab {
        padding: 6px 10px;
        font-size: 11px;
        white-space: nowrap;
        flex-shrink: 0;
      }
      #tabChat.chat-tab-split {
        flex-direction: column;
        padding: 0;
        min-height: 0;
        flex: 1;
        overflow: hidden;
      }
      #tabChat .chat-top-half {
        flex: 1 1 0;
        min-height: 0;
        border-bottom: 1px solid #30363d;
        overflow: hidden;
        min-width: 0;
      }
      #tabChat .chat-top-half .panel-header,
      #tabChat .chat-top-half .terminal-current-path {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      #agentTerminalsList {
        min-width: 0;
        overflow-x: hidden;
      }
      #agentTerminalsList .agent-terminal-item {
        min-width: 0;
        overflow: hidden;
      }
      #agentTerminalsList .agent-terminal-header {
        flex-wrap: wrap;
        gap: 4px;
      }
      #agentTerminalsList .agent-terminal-output,
      #agentTerminalsList .agent-terminal-body {
        word-wrap: break-word;
        overflow-wrap: break-word;
        white-space: pre-wrap;
        overflow-x: hidden;
        max-width: 100%;
      }
      #tabChat .chat-bottom-half {
        flex: 1 1 0;
        min-height: 0;
        display: flex;
        flex-direction: column;
        padding: 8px;
        padding-bottom: calc(8px + env(safe-area-inset-bottom));
        overflow: hidden;
      }
      #tabChat .chat-bottom-half .chat-history-scroll {
        flex: 1;
        min-height: 80px;
        -webkit-overflow-scrolling: touch;
        overflow-x: hidden;
      }
      #tabChat .chat-bottom-half .route-toggle {
        margin-bottom: 6px;
        font-size: 11px;
        white-space: normal;
        line-height: 1.3;
        flex-shrink: 0;
      }
      #tabChat .chat-bottom-half textarea#chatInput {
        min-height: 44px;
        padding: 8px 12px;
        border-radius: 12px;
        font-size: 14px;
        flex-shrink: 0;
        margin-bottom: 6px;
      }
      #tabChat .chat-bottom-half .chat-footer {
        padding-top: 6px;
        border-top: 1px solid #30363d;
        flex-shrink: 0;
      }
      #tabChat .chat-bottom-half .chat-footer button#sendBtn {
        padding: 10px 20px;
        border-radius: 12px;
      }
      /* Chat flush to bottom: hide footer on mobile so chat area extends to screen bottom */
      .footer {
        display: none;
      }
      /* Chat message bubbles on mobile */
      #chatHistory .terminal-line {
        margin-bottom: 10px;
        padding: 10px 14px;
        border-radius: 12px;
        max-width: 95%;
      }
      #chatHistory .terminal-line.user {
        background: #1f6feb;
        color: #e6edf3;
        margin-left: 0;
        margin-right: auto;
      }
      #chatHistory .terminal-line.info,
      #chatHistory .terminal-line.thinking-row {
        background: #21262d;
        border: 1px solid #30363d;
        margin-left: 0;
        margin-right: auto;
      }
      #chatHistory .terminal-line.error {
        background: rgba(248, 81, 73, 0.15);
        border: 1px solid #f85149;
      }
      .chat-footer {
        flex-wrap: wrap;
        gap: 8px;
      }
      .mobile-hamburger {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        padding: 0;
        background: transparent;
        border: 1px solid #30363d;
        border-radius: 6px;
        color: #8b949e;
        cursor: pointer;
        font-size: 18px;
        flex-shrink: 0;
      }
      .mobile-hamburger:hover { background: #21262d; color: #e6edf3; }
      .mobile-panels-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        padding: 0;
        background: transparent;
        border: 1px solid #30363d;
        border-radius: 6px;
        color: #8b949e;
        cursor: pointer;
        font-size: 14px;
        flex-shrink: 0;
      }
      .mobile-panels-btn:hover { background: #21262d; color: #e6edf3; }
    }
    @media (min-width: 769px) {
      .mobile-hamburger { display: none; }
      .mobile-panels-btn { display: none; }
    }
  </style>
</head>
<body>
  <div class="header">
    <button type="button" class="mobile-hamburger" id="mobileHamburger" aria-label="Open menu">&#9776;</button>
    <div class="header-left">
      <div class="repo-dropdown-wrap">
        <span id="githubBtnWrap">
          <a href="javascript:void(0)" id="githubBtn" class="github-btn" rel="noopener" title="GitHub">
            <svg class="github-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
            <span id="githubBtnLabel">GitHub</span>
          </a>
        </span>
        <div id="githubLoginModal" class="github-login-modal">
          <div class="github-login-panel" style="position:relative;">
            <button type="button" class="github-login-close" id="githubLoginClose" aria-label="Close">&times;</button>
            <h3>Log in to GitHub</h3>
            <p>Use the GitHub CLI to authenticate. Run this in your terminal or open a tmux window to run it:</p>
            <p><code>gh auth login</code></p>
            <div class="github-login-actions">
              <button type="button" id="githubLoginTmuxBtn">Open in tmux window</button>
              <button type="button" class="secondary" id="githubLoginCopyBtn">Copy command</button>
              <button type="button" class="secondary" id="githubLoginRefreshBtn">I've logged in — refresh</button>
            </div>
          </div>
        </div>
        <div class="open-folder-wrap">
          <button type="button" class="open-folder-btn" id="openFolderBtn" title="Switch repo or open folder">
            <svg viewBox="0 0 16 16" fill="currentColor"><path d="M1.75 1A1.75 1.75 0 000 2.75v10.5C0 14.216.784 15 1.75 15h12.5A1.75 1.75 0 0016 13.25v-8.5A1.75 1.75 0 0014.25 3H7.5a.25.25 0 01-.2-.1L5.875 1.5A1.75 1.75 0 004.25 1H1.75z"/></svg>
            <span class="repo-dropdown-label" id="repoDropdownLabel">Open folder or repo</span>
            <span class="repo-dropdown-chevron">▾</span>
          </button>
          <div id="openFolderDropdown" class="open-folder-dropdown">
            <button type="button" class="of-item" id="openFolderBrowse">Browse folders…</button>
            <button type="button" class="of-item" id="openFolderNone">None</button>
            <div class="of-divider"></div>
            <div class="of-head" id="openFolderWorkspaceHead">Workspace</div>
            <div id="openFolderWorkspaceList"></div>
            <div class="of-divider"></div>
            <div class="of-head" id="openFolderReposHead">Your repos</div>
            <div id="openFolderReposList"></div>
          </div>
        </div>
        <div id="browseModal" class="browse-modal">
          <div class="browse-modal-panel">
            <h3>Select folder</h3>
            <div class="browse-modal-tree" id="browseModalTree"><ul id="browseModalTreeRoot"></ul></div>
            <button type="button" class="secondary" id="browseModalClose" style="align-self:flex-end;">Cancel</button>
          </div>
        </div>
      </div>
      <span class="header-title">overstory dashboard v0.2.0</span>
      <span class="header-time" id="currentTime">12:55:31 PM</span>
      <span class="header-refresh">refresh: <span id="refreshInterval">3000ms</span></span>
    </div>
    <div class="header-controls">
      <button id="restartWithSkipPermsBtn" class="header-btn-long" title="Enable skip-permissions, send Ctrl+C twice to all agent tmux, then start claude --dangerously-skip-permissions in each">Restart agents (skip perms)</button>
      <button id="approveAllAndCleanBtn" class="header-btn-long" title="Process pending approval mail, drain merge queue, then prune completed worktrees (unblocks builders when no lead)">Approve all &amp; clean</button>
      <button id="injectLeadBtn" class="header-btn-long" title="Spawn a lead and reassign all unread lead/supervisor mail to it (use when agents have no lead)">Inject Lead</button>
      <button id="pruneWorktreesBtn" class="header-btn-long" title="Prune completed worktrees (overstory worktree clean --completed)">Prune completed</button>
      <button id="killAllAgentsBtn" class="header-btn-long header-btn-danger" title="Stop all agents, clear all mail and task queue so you can open a new project fresh">Kill all agents</button>
      <button type="button" id="ceoBtn" class="header-btn-long" title="CEO view: animated office">CEO</button>
      <button type="button" class="mobile-panels-btn" id="mobilePanelsBtn" aria-label="Toggle agents and mail panels" title="Agents &amp; Mail">▦</button>
      <button onclick="refreshNow()" title="Refresh">↻</button>
    </div>
  </div>
  <div class="sidebar-overlay" id="sidebarOverlay" aria-hidden="true"></div>
  <div class="main-layout">
    <aside class="sidebar" id="sidebar">
      <button type="button" class="sidebar-toggle" id="sidebarToggle" title="Toggle sidebar">◀</button>
      <div class="sidebar-content">
        <div class="sidebar-section">
          <div class="sidebar-section-title">Project</div>
          <div class="project-settings">
            <label class="project-setting-row">
              <span class="project-setting-label">Default folder</span>
              <select id="defaultProjectFolder" class="project-select" title="Default project folder">
                <option value="overstory">Overstory workspace</option>
                <option value="__custom__">Custom path…</option>
                <option value="__new__">Create new subfolder…</option>
              </select>
            </label>
            <div class="project-setting-row">
              <button type="button" id="showNewFolderBtn" class="project-create-folder-btn" title="Create a new subfolder in the workspace and set it as default">+ New folder</button>
            </div>
            <div id="customProjectPathWrap" class="project-setting-row" style="display:none;">
              <select id="customProjectPath" class="project-select project-subfolders-select" title="Choose a workspace subfolder">
                <option value="">— Select folder —</option>
              </select>
            </div>
            <div id="newProjectFolderWrap" class="project-setting-row project-new-folder-row" style="display:none;">
              <input type="text" id="newProjectFolderName" class="project-path-input" placeholder="New folder name">
              <button type="button" id="newProjectFolderBtn" class="project-create-folder-btn project-new-create-btn" title="Create folder and set as default">Create</button>
            </div>
          </div>
        </div>
        <div class="sidebar-section">
          <div class="sidebar-section-title">Explorer</div>
          <div class="file-tree" id="fileTree"><ul id="fileTreeRoot"></ul></div>
        </div>
        <div class="sidebar-section">
          <div class="sidebar-section-title">Skills</div>
          <div class="skills-browser-tabs">
            <button type="button" class="skills-tab active" data-tab="installed">Installed</button>
            <button type="button" class="skills-tab" data-tab="trending">Trending (last 30d)</button>
          </div>
          <div id="skillsInstalledList" class="skills-list"></div>
          <div id="skillsTrendingList" class="skills-list" style="display:none;"></div>
        </div>
      </div>
    </aside>
    <div class="main-content">
    <div class="left">
      <div class="left-top">
        <div id="leftPanelAgents" class="panel">
          <div class="panel-header">Agents (<span id="agentCount">0</span>)</div>
          <div class="panel-body">
            <table class="agents-table">
              <thead><tr><th>St</th><th>Name</th><th>Capability</th><th>State</th><th>Bead ID</th><th>Duration</th><th>Tmux</th><th>Task</th></tr></thead>
              <tbody id="agentsBody"></tbody>
            </table>
          </div>
        </div>
        <div id="leftPanelMail" class="panel">
          <div class="panel-header">Mail (<span id="mailCount">0</span> unread)</div>
          <div class="panel-body mail-body" id="mailBody">—</div>
        </div>
        <div id="leftPanelMerge" class="panel">
          <div class="panel-header">Merge Queue (<span id="mergeCount">0</span>)</div>
          <div class="panel-body" id="mergeBody">—</div>
        </div>
        <div id="leftPanelMetrics" class="panel">
          <div class="panel-header">Metrics</div>
          <div class="panel-body" id="metricsBody">Total sessions: 0 | Avg duration: 0s</div>
        </div>
      </div>
      <div id="leftPanelTerminalLog" class="panel">
        <div class="panel-header">Terminal Log</div>
        <div class="panel-body terminal-output" id="leftTerminalLog" style="overflow: auto; font-size: 11px;">[Loading terminal log...]</div>
      </div>
      <div id="leftPanelBunLog" class="panel">
        <div class="bun-log-header">Bun log</div>
        <div class="bun-log" id="bunLog">
          <div class="bun-log-line">[Loading…]</div>
        </div>
      </div>
    </div>
    <div class="right">
      <div class="tabs">
        <div class="tab active" data-tab="chat">Chat (Z AI)</div>
        <div class="tab" data-tab="terminal">TERMINAL</div>
        <div class="tab" data-tab="kanban">KANBAN</div>
        <div class="tab" data-tab="problems">PROBLEMS</div>
        <div class="tab" data-tab="ports">PORTS</div>
        <div class="tab" data-tab="debug">DEBUG CONSOLE</div>
      </div>
      <div id="tabChat" class="tab-content active chat-tab-split" style="flex-direction: column; min-height: 0; padding: 0;">
        <div class="chat-top-half" style="flex: 0 0 50%; min-height: 0; display: flex; flex-direction: column; border-bottom: 1px solid #30363d;">
          <div class="panel-header" style="margin: 0; border-radius: 0;">Agent Terminals (live tmux output)</div>
          <div id="agentTerminalsCurrentPath" class="terminal-current-path" title="Current project path">Path: <span>—</span></div>
          <div id="agentTerminalsList" class="agent-terminals-scroll" style="flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; background: #0d1117;">
            <div style="color: #8b949e; font-size: 12px;">[Loading agents...]</div>
          </div>
        </div>
        <div class="chat-bottom-half" style="flex: 0 0 50%; min-height: 0; display: flex; flex-direction: column; padding: 12px;">
          <div class="terminal-output chat-history-scroll" id="chatHistory" style="flex: 1; min-height: 0; overflow-y: auto; margin-bottom: 8px;"></div>
          <div id="thinkingConsole" class="thinking-console" aria-live="polite"></div>
          <label class="route-toggle"><input type="checkbox" id="showThinking" checked> Show thinking (low-opacity console)</label>
          <label class="route-toggle"><input type="checkbox" id="routeToAgents" checked> Route to agents (spawn task; when off, orchestrator replies directly)</label>
          <textarea id="chatInput" placeholder="Type a message or task for the orchestrator…" style="width: 100%; min-height: 60px; padding: 8px; background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 4px; font-family: inherit; resize: vertical; margin-bottom: 8px;"></textarea>
          <div class="chat-footer">
            <button id="sendBtn">Send</button>
            <span id="chatStatus" class="chat-status"></span>
          </div>
        </div>
      </div>
      <div id="tabTerminal" class="tab-content" style="flex-direction: column;">
        <div class="claude-cli-section">
          <div class="claude-cli-header">Integrated terminal — Claude CLI (agents use this)</div>
          <div id="integratedTerminalCurrentPath" class="terminal-current-path" title="Current project path">Path: <span>—</span></div>
          <label class="route-toggle"><input type="checkbox" id="dangerouslySkipPermissions"> Add <code>--dangerously-skip-permissions</code> (integrated terminal + spawned agents)</label>
          <p class="claude-cli-note">Opening this tab auto-starts Claude CLI below. Type in the input and press Enter to send. Toggle applies on next start (restart page to change).</p>
        </div>
        <div class="terminal-output" id="terminalOutput" style="flex: 1; margin-top: 8px; min-height: 200px; max-height: 300px;">[Connecting to Claude CLI…]</div>
        <div class="terminal-input-row">
          <input type="text" id="terminalInput" placeholder="Type a command for Claude CLI…" class="terminal-input" autocomplete="off">
          <button type="button" id="terminalSendBtn">Send</button>
        </div>
      </div>
      <div id="tabKanban" class="tab-content" style="flex-direction: column; min-height: 0; overflow: hidden;">
        <div class="panel-header" style="margin: 0; border-radius: 0;">Agent Kanban</div>
        <div id="kanbanBoard" class="kanban-board">[Loading agents…]</div>
      </div>
      <div id="tabProblems" class="tab-content">
        <div class="terminal-output" id="problemsContent">[Problems/errors will appear here]</div>
      </div>
      <div id="tabPorts" class="tab-content">
        <div class="terminal-output" id="portsContent">[Port information will appear here]</div>
      </div>
      <div id="tabDebug" class="tab-content">
        <div class="terminal-output" id="debugContent">[Debug console output will appear here]</div>
      </div>
    </div>
    </div>
  </div>
  <div class="footer" id="footerBar">
    <div class="footer-left">
      <div class="footer-tokens" id="footerTokens" title="Token usage from sessions">
        <span class="footer-token-toggle">
          <button type="button" id="tokenScopeAll" class="footer-token-scope active" title="All sessions">All</button>
          <button type="button" id="tokenScopeProject" class="footer-token-scope" title="Current project only">Project</button>
        </span>
        <span class="token-in" id="footerTokenIn">0</span> in
        <span class="token-out" id="footerTokenOut">0</span> out
        <span id="footerTokenTotal" style="color: #8b949e;">(0 total)</span>
      </div>
    </div>
    <div class="footer-center">
      <div id="footerZombie" class="footer-zombie">
        <span class="footer-zombie-label">🧟</span>
        <span>Slayed: <span id="zombiesSlayedCount" class="footer-zombie-value">0</span></span>
        <span>Detected: <span id="zombiesDetectedCount" class="footer-zombie-value footer-zombie-detected">0</span></span>
        <span>Next: <span id="zombieCountdown" class="footer-zombie-value">5:00</span></span>
        <button type="button" id="bloodRitualBtn" class="footer-zombie-btn" title="Slay zombie agents">Slay</button>
      </div>
    </div>
    <div class="footer-right">
      <span id="footerGatewayStatus" class="footer-gateway" title="Gateway health">Gateway —</span>
      <button type="button" class="footer-settings-btn" id="footerSettingsBtn" title="Settings and customization">&#9881; Settings</button>
    </div>
  </div>
  <div id="settingsModal" class="settings-modal">
    <div class="settings-panel" style="position: relative;">
      <button type="button" class="settings-close" id="settingsModalClose" aria-label="Close">&times;</button>
      <h3>Settings</h3>
      <div class="settings-section">
        <div class="settings-section-title">Dashboard</div>
        <div class="settings-row">
          <label for="settingRefreshInterval">Refresh interval</label>
          <select id="settingRefreshInterval">
            <option value="500">500 ms</option>
            <option value="1000">1 s</option>
            <option value="2000">2 s</option>
            <option value="3000" selected>3 s</option>
            <option value="5000">5 s</option>
          </select>
        </div>
        <div class="settings-row">
          <label for="settingSidebarCollapsed">Sidebar collapsed by default</label>
          <input type="checkbox" id="settingSidebarCollapsed">
        </div>
      </div>
      <div class="settings-section">
        <div class="settings-section-title">Project</div>
        <div class="settings-row">
          <label for="settingDefaultFolder">Default project folder</label>
          <select id="settingDefaultFolder">
            <option value="overstory">Overstory workspace</option>
            <option value="__custom__">Custom path…</option>
            <option value="__new__">Create new subfolder…</option>
          </select>
        </div>
        <div class="settings-row" id="settingCustomPathWrap" style="display: none;">
          <label for="settingCustomPath">Custom path</label>
          <input type="text" id="settingCustomPath" placeholder="Path under workspace" style="flex: 1;">
        </div>
        <div class="settings-row" id="settingNewFolderWrap" style="display: none;">
          <label for="settingNewFolderName">New folder name</label>
          <input type="text" id="settingNewFolderName" placeholder="New folder name" style="flex: 1;">
          <button type="button" id="settingNewFolderBtn">Create & set default</button>
        </div>
      </div>
      <div class="settings-section">
        <div class="settings-section-title">Agent / Terminal</div>
        <div class="settings-row">
          <label for="settingSkipPermissions">Dangerously skip permissions (agents + integrated terminal)</label>
          <input type="checkbox" id="settingSkipPermissions">
        </div>
      </div>
      <div class="settings-section">
        <div class="settings-section-title">Nanoclaw</div>
        <div class="settings-row">
          <label>Gateway and orchestrator setup</label>
          <button type="button" id="nanoclawWizardBtn" class="secondary">Run setup wizard</button>
        </div>
      </div>
      <div class="settings-actions">
        <button type="button" class="secondary" id="settingsCancelBtn">Cancel</button>
        <button type="button" id="settingsSaveBtn">Save</button>
      </div>
    </div>
  </div>
  <div id="nanoclawWizardModal" class="nanoclaw-wizard-modal">
    <div class="nanoclaw-wizard-panel">
      <h3>Nanoclaw setup wizard</h3>
      <div id="nanoclawWizardConsole" class="nanoclaw-wizard-console">Run the wizard to check gateway, Z AI, and chat.</div>
      <div class="nanoclaw-wizard-actions">
        <button type="button" id="nanoclawWizardClose">Close</button>
        <button type="button" id="nanoclawWizardRun" class="primary">Run</button>
      </div>
    </div>
  </div>
  <div id="ceoModal" class="ceo-fullscreen" aria-hidden="true">
    <select id="ceoTheme" class="ceo-theme-select">
      <option value="tinyfarm">Tiny Wonder Farm</option>
      <option value="zombie">Zombie Survival</option>
    </select>
    <button type="button" class="ceo-close" id="ceoClose" aria-label="Close CEO view">&times;</button>
    <canvas id="ceoCanvas"></canvas>
    <div id="agentDialog" class="agent-dialog" style="display:none;">
      <div class="agent-dialog-portrait" id="agentDialogPortrait"></div>
      <div class="agent-dialog-content">
        <div class="agent-dialog-name" id="agentDialogName"></div>
        <div class="agent-dialog-text" id="agentDialogText"></div>
      </div>
    </div>
    <span class="ceo-credit" id="ceoCredit"></span>
  </div>
  <script src="https://unpkg.com/three@0.149.0/build/three.min.js"></script>
  <script defer>
    const GATEWAY_URL = {{ gateway_url|tojson }};
    let refreshIntervalId = null;
    let REFRESH_INTERVAL = 3000; // 3s default (MacBook-friendly); overridden from ui-settings
    let zombiesSlayedTotal = 0;
    const ZOMBIE_CHECK_INTERVAL_MS = 60000; // 1 minute (zombie slayer runs once per minute)
    let zombieCountdownSeconds = 60; // 1 minute
    let zombieCountdownInterval = null;
    const agentErrorLogged = {};
    let gatewayUnreachableLogged = false;
    const expandedAgentTerminals = new Set();
    const SCROLL_BOTTOM_THRESHOLD = 30;
    let activeAgentTerminal = null;
    let cachedTerminalOutput = null;

    // --- Topbar: repo dropdown + GitHub (icon, login / avatar+username) | Sidebar: file tree + skills ---
    const GITHUB_ICON_SVG = '<svg class="github-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>';
    function updateCurrentPath(pathStr) {
      const pathDisplay = pathStr || '—';
      const el1 = document.getElementById('agentTerminalsCurrentPath');
      const el2 = document.getElementById('integratedTerminalCurrentPath');
      if (el1) { const s = el1.querySelector('span'); if (s) s.textContent = pathDisplay; el1.title = pathDisplay; }
      if (el2) { const s = el2.querySelector('span'); if (s) s.textContent = pathDisplay; el2.title = pathDisplay; }
    }
    function repoLabelFromWorkspace(workspaceData) {
      if (!workspaceData) return 'Open folder or repo';
      if (workspaceData.project_none) return 'None';
      var url = workspaceData.github_url || workspaceData.remote_url || '';
      if (url && url.indexOf('github.com') !== -1) {
        var match = url.match(/github\.com[/:]([^/]+\/[^/]+?)(?:\.git)?\/?$/);
        if (match) return match[1];
      }
      return (workspaceData.folder || workspaceData.path || '').split('/').pop() || 'Open folder or repo';
    }
    function updateGitHubButton(workspaceData, ghData) {
      const btn = document.getElementById('githubBtn');
      const repoLabelEl = document.getElementById('repoDropdownLabel');
      if (repoLabelEl) repoLabelEl.textContent = repoLabelFromWorkspace(workspaceData);
      if (workspaceData) {
        updateCurrentPath(workspaceData.project_none ? '—' : (workspaceData.path || ''));
      }
      if (!btn) return;
      const repoUrl = (workspaceData && workspaceData.github_url) || '';
      if (ghData && ghData.logged_in) {
        btn.href = repoUrl || ('https://github.com/' + (ghData.login || ''));
        btn.setAttribute('target', '_blank');
        btn.classList.add('connected');
        btn.title = repoUrl ? 'Open repo on GitHub' : ('@' + (ghData.login || ''));
        btn.innerHTML = GITHUB_ICON_SVG + (ghData.avatar_url ? '<img class="github-avatar" src="' + ghData.avatar_url + '" alt="">' : '') + '<span class="github-username">' + (ghData.login || 'GitHub') + '</span>';
        btn.onclick = null;
      } else {
        btn.href = 'javascript:void(0)';
        btn.removeAttribute('target');
        btn.classList.remove('connected');
        btn.title = 'Log in to GitHub';
        btn.innerHTML = GITHUB_ICON_SVG + '<span id="githubBtnLabel">Login</span>';
        btn.onclick = function(e) { e.preventDefault(); document.getElementById('githubLoginModal').classList.add('visible'); };
      }
      if (!workspaceData) updateCurrentPath('—');
    }
    function loadWorkspace() {
      fetch('/api/workspace').then(r => r.json()).then(workspaceData => {
        if (window.syncSidebarDropdownToWorkspace) window.syncSidebarDropdownToWorkspace(workspaceData);
        fetch('/api/github-auth').then(r => r.json()).then(ghData => {
          updateGitHubButton(workspaceData, ghData);
        }).catch(() => updateGitHubButton(workspaceData, null));
      }).catch(() => {
        fetch('/api/github-auth').then(r => r.json()).then(ghData => updateGitHubButton(null, ghData)).catch(() => {});
      });
    }
    function initGitHubLoginModal() {
      const modal = document.getElementById('githubLoginModal');
      const closeBtn = document.getElementById('githubLoginClose');
      const tmuxBtn = document.getElementById('githubLoginTmuxBtn');
      const copyBtn = document.getElementById('githubLoginCopyBtn');
      const githubBtn = document.getElementById('githubBtn');
      if (githubBtn) githubBtn.addEventListener('click', function(e) {
        if (!this.classList.contains('connected')) { e.preventDefault(); e.stopPropagation(); if (modal) modal.classList.add('visible'); }
      }, true);
      if (closeBtn) closeBtn.addEventListener('click', function() { modal.classList.remove('visible'); });
      if (modal) modal.addEventListener('click', function(e) { if (e.target === modal) modal.classList.remove('visible'); });
      if (tmuxBtn) tmuxBtn.addEventListener('click', async function() {
        try {
          const r = await fetch('/api/github-auth/login', { method: 'POST' });
          const data = await r.json();
          alert(data.message || (data.ok ? 'Tmux window opened.' : ''));
          if (data.ok) modal.classList.remove('visible');
        } catch (e) { alert(e.message); }
      });
      if (copyBtn) copyBtn.addEventListener('click', function() {
        navigator.clipboard.writeText('gh auth login').then(function() { copyBtn.textContent = 'Copied!'; setTimeout(function() { copyBtn.textContent = 'Copy command'; }, 1500); });
      });
      var refreshBtn = document.getElementById('githubLoginRefreshBtn');
      if (refreshBtn) refreshBtn.addEventListener('click', function() {
        modal.classList.remove('visible');
        fetch('/api/github-auth?refresh=1').then(function() { loadWorkspace(); }).catch(function() { loadWorkspace(); });
      });
    }

    // --- Open Folder: dropdown (Browse + GitHub repos), clone if needed, cd terminals ---
    function cdTerminalTo(path) {
      if (!path) return;
      fetch('/api/terminal/input', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ line: 'cd ' + path }) }).catch(function(){});
    }
    function openProjectThenRefresh(payload) {
      fetch('/api/project/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        .then(function(r) { return r.ok ? r.json() : Promise.reject(new Error('Open failed')); })
        .then(function(data) {
          if (data.path) {
            cdTerminalTo(data.path);
            if (window.refreshProjectUI) window.refreshProjectUI();
          }
        })
        .catch(function(e) { alert(e.message || 'Failed to open project'); });
    }
    function initOpenFolder() {
      var btn = document.getElementById('openFolderBtn');
      var dropdown = document.getElementById('openFolderDropdown');
      var browseBtn = document.getElementById('openFolderBrowse');
      var reposList = document.getElementById('openFolderReposList');
      var reposHead = document.getElementById('openFolderReposHead');
      var browseModal = document.getElementById('browseModal');
      var browseTreeRoot = document.getElementById('browseModalTreeRoot');
      var browseClose = document.getElementById('browseModalClose');

      var workspaceHead = document.getElementById('openFolderWorkspaceHead');
      var workspaceList = document.getElementById('openFolderWorkspaceList');
      if (btn && dropdown) {
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          dropdown.classList.toggle('visible');
          if (dropdown.classList.contains('visible')) {
            workspaceList.innerHTML = '<div class="of-loading">Loading…</div>';
            fetch('/api/workspace/subfolders').then(function(r) { return r.json(); }).then(function(subfolders) {
              workspaceList.innerHTML = '';
              (subfolders || []).forEach(function(f) {
                var el = document.createElement('button');
                el.type = 'button';
                el.className = 'of-item';
                el.textContent = f.name || f.path || '';
                el.dataset.path = f.path || '';
                el.addEventListener('click', function(e) {
                  e.stopPropagation();
                  dropdown.classList.remove('visible');
                  openProjectThenRefresh({ path: this.dataset.path });
                });
                workspaceList.appendChild(el);
              });
              if (!workspaceList.children.length) workspaceList.innerHTML = '<div class="of-head" style="padding:8px 12px;">No subfolders</div>';
            }).catch(function() {
              workspaceList.innerHTML = '<div class="of-head" style="padding:8px 12px; color:#f85149;">Failed to load</div>';
            });
            reposHead.style.display = 'block';
            reposList.innerHTML = '<div class="of-loading">Loading repos</div>';
            fetch('/api/github-auth').then(function(r) { return r.json(); }).then(function(gh) {
              if (!gh.logged_in) {
                reposHead.style.display = 'none';
                reposList.innerHTML = '';
                var signInBtn = document.createElement('button');
                signInBtn.type = 'button';
                signInBtn.className = 'of-signin-btn';
                signInBtn.textContent = 'Sign in with GitHub';
                signInBtn.title = 'Run gh auth login';
                signInBtn.addEventListener('click', function() {
                  dropdown.classList.remove('visible');
                  var modal = document.getElementById('githubLoginModal');
                  if (modal) modal.classList.add('visible');
                });
                reposList.appendChild(signInBtn);
                return;
              }
              reposHead.style.display = 'block';
              fetch('/api/github-repos').then(function(r) { return r.json(); }).then(function(data) {
                var repos = data.repos || [];
                reposList.innerHTML = '';
                repos.forEach(function(fullName) {
                  var el = document.createElement('button');
                  el.type = 'button';
                  el.className = 'of-item';
                  el.textContent = fullName;
                  el.dataset.repo = fullName;
                  el.addEventListener('click', function() {
                    dropdown.classList.remove('visible');
                    openProjectThenRefresh({ repo: this.dataset.repo });
                  });
                  reposList.appendChild(el);
                });
                if (repos.length === 0) reposList.innerHTML = '<div class="of-head" style="padding:8px 12px;">No repos</div>';
              }).catch(function() {
                reposList.innerHTML = '<div class="of-head" style="padding:8px 12px; color:#f85149;">Failed to load repos</div>';
              });
            }).catch(function() {
              reposList.innerHTML = '<div class="of-head" style="padding:8px 12px; color:#f85149;">Failed to load</div>';
            });
          }
        });
        document.addEventListener('click', function() { dropdown.classList.remove('visible'); });
      }
      var noneBtn = document.getElementById('openFolderNone');
      if (noneBtn) noneBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.remove('visible');
        fetch('/api/ui-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current_project_folder: '__none__' }) })
          .then(function() { if (window.refreshProjectUI) window.refreshProjectUI(); })
          .catch(function() { alert('Failed to set None'); });
      });
      if (browseBtn) browseBtn.addEventListener('click', function() {
        dropdown.classList.remove('visible');
        browseModal.classList.add('visible');
        fetch('/api/ui-settings').then(function(r) { return r.json(); }).then(function(s) {
          var root = s.root_workspace || '';
          fetch('/api/file-tree?root=' + encodeURIComponent(root) + '&depth=5').then(function(r) { return r.json(); }).then(function(nodes) {
            browseTreeRoot.innerHTML = '';
            function addNode(node, ul) {
              var li = document.createElement('li');
              var isDir = node.type === 'dir';
              var span = document.createElement('span');
              span.className = 'tree-item' + (isDir ? ' dir' : '');
              span.innerHTML = '<span class="tree-arrow"></span>' + (node.name || '');
              span.dataset.path = node.path || '';
              if (isDir) {
                span.addEventListener('click', function(e) {
                  e.stopPropagation();
                  openProjectThenRefresh({ path: this.dataset.path });
                  browseModal.classList.remove('visible');
                });
                var subUl = document.createElement('ul');
                (node.children || []).forEach(function(c) { addNode(c, subUl); });
                li.appendChild(span);
                if (subUl.children.length) li.appendChild(subUl);
              } else li.appendChild(span);
              ul.appendChild(li);
            }
            (nodes || []).forEach(function(n) { addNode(n, browseTreeRoot); });
          });
        });
      });
      if (browseClose) browseClose.addEventListener('click', function() { browseModal.classList.remove('visible'); });
      if (browseModal) browseModal.addEventListener('click', function(e) { if (e.target === browseModal) browseModal.classList.remove('visible'); });
    }
    function renderFileTree(nodes, ul) {
      ul.innerHTML = '';
      (nodes || []).forEach(node => {
        const li = document.createElement('li');
        const isDir = node.type === 'dir' && (node.children || []).length > 0;
        const span = document.createElement('span');
        span.className = 'tree-item' + (isDir ? ' dir' : '');
        span.innerHTML = '<span class="tree-arrow"></span>' + (node.name || '');
        span.dataset.path = node.path || '';
        if (isDir) {
          span.addEventListener('click', function() {
            this.classList.toggle('open');
            const sub = this.nextElementSibling;
            if (sub) sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
          });
          const subUl = document.createElement('ul');
          subUl.style.display = 'none';
          renderFileTree(node.children || [], subUl);
          li.appendChild(span);
          li.appendChild(subUl);
        } else {
          span.title = node.path;
          li.appendChild(span);
        }
        ul.appendChild(li);
      });
    }
    function loadFileTree() {
      const rootEl = document.getElementById('fileTreeRoot');
      if (!rootEl) return;
      fetch('/api/workspace').then(r => r.ok ? r.json() : {}).then(function(workspace) {
        workspace = workspace || {};
        var projectPath = workspace.path || '';
        var rootForTree = (workspace.project_none && workspace.root_workspace) ? workspace.root_workspace : '';
        var fileTreeUrl = '/api/file-tree';
        if (rootForTree) fileTreeUrl += '?root=' + encodeURIComponent(rootForTree);
        return fetch(fileTreeUrl).then(function(r) { return r.ok ? r.json() : []; }).then(function(nodes) {
          const projectName = workspace.project_none ? 'Workspace' : (workspace.folder || (workspace.path || '').split('/').pop() || 'Project');
          rootEl.innerHTML = '';
          var projectLi = document.createElement('li');
          var projectSpan = document.createElement('span');
          projectSpan.className = 'tree-item dir tree-item-project';
          projectSpan.innerHTML = '<span class="tree-arrow"></span>' + (projectName || 'Project');
          projectSpan.dataset.path = projectPath || rootForTree || '';
          projectSpan.title = projectPath || rootForTree || 'No project selected';
          var childrenUl = document.createElement('ul');
          childrenUl.classList.add('file-tree-children');
          if (nodes.length) {
            projectSpan.classList.add('open');
            renderFileTree(nodes, childrenUl);
          } else if (projectPath || rootForTree) {
            projectSpan.classList.add('open');
            childrenUl.innerHTML = '<li class="tree-item" style="color:#6e7681;padding-left:18px">Empty folder</li>';
          } else {
            projectSpan.classList.add('open');
            childrenUl.innerHTML = '<li class="tree-item" style="color:#6e7681;padding-left:18px">Select a folder or repo above</li>';
          }
        projectSpan.addEventListener('click', function() {
          this.classList.toggle('open');
          var sub = this.nextElementSibling;
          if (sub) sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
        });
        projectLi.appendChild(projectSpan);
        projectLi.appendChild(childrenUl);
        rootEl.appendChild(projectLi);
        });
      }).catch(function(err) {
        rootEl.innerHTML = '<li class="tree-item" style="color:#f85149">Failed to load file tree</li>';
      });
    }
    function loadSkills() {
      const installedEl = document.getElementById('skillsInstalledList');
      const trendingEl = document.getElementById('skillsTrendingList');
      if (installedEl) {
        fetch('/api/skills/installed').then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); }).then(items => {
          installedEl.innerHTML = items.length ? items.map(s => '<div class="skill-item"><span class="skill-name">' + (s.name || '') + '</span><br><span style="font-size:10px;color:#6e7681">' + (s.description || '').slice(0, 60) + '</span></div>').join('') : '<div class="skill-item">No skills found</div>';
        }).catch(function() {
          installedEl.innerHTML = '<div class="skill-item" style="color:#f85149">Failed to load skills</div>';
        });
      }
      if (trendingEl) {
        fetch('/api/skills/trending').then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); }).then(items => {
          trendingEl.innerHTML = items.length ? items.map(t => '<div class="trending-item"><span class="trending-name">' + (t.name || '') + '</span><br><span style="font-size:10px">' + (t.new_findings || 0) + ' findings</span></div>').join('') : '<div class="trending-item">No trending data. Add topics in last30days and run research to see trending.</div>';
        }).catch(function() {
          trendingEl.innerHTML = '<div class="trending-item" style="color:#a371f7">Could not load trending</div>';
        });
      }
    }
    function initSidebar() {
      const sidebar = document.getElementById('sidebar');
      const toggle = document.getElementById('sidebarToggle');
      const overlay = document.getElementById('sidebarOverlay');
      const hamburger = document.getElementById('mobileHamburger');
      const leftPanel = document.querySelector('.left');
      const panelsBtn = document.getElementById('mobilePanelsBtn');
      const collapsed = localStorage.getItem('overclaw_sidebar_collapsed') === '1';
      if (collapsed) { sidebar.classList.add('collapsed'); toggle.textContent = '▶'; toggle.title = 'Show sidebar'; } else { toggle.title = 'Hide sidebar'; }
      toggle.addEventListener('click', function() {
        sidebar.classList.toggle('collapsed');
        const isCollapsed = sidebar.classList.contains('collapsed');
        toggle.textContent = isCollapsed ? '▶' : '◀';
        toggle.title = isCollapsed ? 'Show sidebar' : 'Hide sidebar';
        localStorage.setItem('overclaw_sidebar_collapsed', isCollapsed ? '1' : '0');
        if (window.innerWidth <= 768) { sidebar.classList.remove('mobile-open'); if (overlay) overlay.classList.remove('visible'); }
      });
      if (hamburger && overlay) {
        hamburger.addEventListener('click', function() {
          sidebar.classList.toggle('mobile-open');
          overlay.classList.toggle('visible');
          overlay.setAttribute('aria-hidden', sidebar.classList.contains('mobile-open') ? 'false' : 'true');
        });
        overlay.addEventListener('click', function() {
          sidebar.classList.remove('mobile-open');
          overlay.classList.remove('visible');
          overlay.setAttribute('aria-hidden', 'true');
        });
      }
      if (panelsBtn && leftPanel) {
        panelsBtn.addEventListener('click', function() {
          leftPanel.classList.toggle('mobile-panels-open');
        });
      }
      document.querySelectorAll('.skills-tab').forEach(btn => {
        btn.addEventListener('click', function() {
          document.querySelectorAll('.skills-tab').forEach(b => b.classList.remove('active'));
          this.classList.add('active');
          const tab = this.dataset.tab;
          document.getElementById('skillsInstalledList').style.display = tab === 'installed' ? 'block' : 'none';
          document.getElementById('skillsTrendingList').style.display = tab === 'trending' ? 'block' : 'none';
        });
      });
      initGitHubLoginModal();
      initOpenFolder();
      loadWorkspace();
      loadFileTree();
      loadSkills();
      window.refreshProjectUI = function() { loadWorkspace(); loadFileTree(); loadSkills(); };
      // Project settings: default folder + New project + Create new subfolder
      (async function initProjectSettings() {
        const sel = document.getElementById('defaultProjectFolder');
        const customWrap = document.getElementById('customProjectPathWrap');
        const customInput = document.getElementById('customProjectPath');
        const newWrap = document.getElementById('newProjectFolderWrap');
        const newInput = document.getElementById('newProjectFolderName');
        const newBtn = document.getElementById('newProjectFolderBtn');
        const showNewBtn = document.getElementById('showNewFolderBtn');
        if (!sel) return;
        function showFolderWraps(selectedPath) {
          const v = sel.value;
          customWrap.style.display = v === '__custom__' ? 'block' : 'none';
          if (newWrap) newWrap.style.display = v === '__new__' ? 'block' : 'none';
          if (v === '__custom__' && selectedPath === undefined) populateCustomDropdown();
          if (v !== '__custom__' && v !== '__new__') saveProjectSettingBoth('default_project_folder', 'overstory', 'current_project_folder', '');
        }
        async function populateCustomDropdown(selectedPath) {
          if (!customInput || customInput.tagName !== 'SELECT') return;
          const currentVal = selectedPath != null ? selectedPath : (customInput.value || '');
          try {
            const r = await fetch('/api/workspace/subfolders');
            const subfolders = await r.json();
            customInput.innerHTML = '<option value="">— Select folder —</option><option value="__new__">＋ New folder…</option>';
            (subfolders || []).forEach(function(f) {
              const opt = document.createElement('option');
              opt.value = f.path || '';
              opt.textContent = f.name || f.path || '';
              customInput.appendChild(opt);
            });
            if (currentVal && currentVal !== '__new__') {
              const hasOpt = Array.from(customInput.options).some(function(o) { return o.value === currentVal; });
              if (!hasOpt) {
                const label = currentVal.split('/').pop() || currentVal;
                const opt = document.createElement('option');
                opt.value = currentVal;
                opt.textContent = label + ' (current)';
                customInput.appendChild(opt);
              }
              customInput.value = currentVal;
            }
          } catch (_) {}
        }
        try {
          const r = await fetch('/api/ui-settings');
          const s = await r.json();
          const def = (s.default_project_folder || 'overstory').trim();
          if (def && def !== 'overstory') {
            sel.value = '__custom__';
            showFolderWraps(def);
            await populateCustomDropdown(def);
          } else {
            sel.value = 'overstory';
            showFolderWraps();
          }
          fetch('/api/workspace').then(function(wr) { return wr.ok ? wr.json() : {}; }).then(function(ws) {
            if (ws && window.syncSidebarDropdownToWorkspace) window.syncSidebarDropdownToWorkspace(ws);
          });
        } catch (_) { showFolderWraps(); }
        sel.addEventListener('change', showFolderWraps);
        if (showNewBtn) showNewBtn.addEventListener('click', function() {
          sel.value = '__new__';
          showFolderWraps();
          if (newInput) { newInput.value = ''; newInput.focus(); }
        });
        customInput.addEventListener('change', function() {
          if (this.value === '__new__') {
            sel.value = '__new__';
            showFolderWraps();
            if (newInput) { newInput.value = ''; newInput.focus(); }
            return;
          }
          var path = this.value.trim() || 'overstory';
          saveProjectSettingBoth('default_project_folder', path, 'current_project_folder', path);
        });
        if (newBtn && newInput) {
          newBtn.addEventListener('click', async function() {
            const name = newInput.value.trim();
            if (!name) { alert('Enter a folder name'); return; }
            try {
              const res = await fetch('/api/project/create-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
              const data = await res.json();
              if (!res.ok) { alert(data.error || 'Failed to create folder'); return; }
              saveProjectSettingBoth('default_project_folder', data.path, 'current_project_folder', data.path);
              sel.value = '__custom__';
              showFolderWraps(data.path);
              await populateCustomDropdown(data.path);
              if (customInput) customInput.value = data.path;
              newInput.value = '';
              loadWorkspace();
              loadFileTree();
            } catch (e) { alert('Failed: ' + (e.message || 'Unknown error')); }
          });
        }
        function saveProjectSetting(key, value) {
          fetch('/api/ui-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [key]: value }) }).then(function() { loadWorkspace(); loadFileTree(); }).catch(function(){});
        }
        function saveProjectSettingBoth(k1, v1, k2, v2) {
          var payload = {};
          payload[k1] = v1;
          payload[k2] = v2;
          fetch('/api/ui-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(function() { loadWorkspace(); loadFileTree(); }).catch(function(){});
        }
        function syncSidebarDropdownToWorkspace(workspace) {
          if (!sel || !workspace) return;
          var path = (workspace.path || '').trim();
          var projectNone = workspace.project_none === true;
          if (projectNone) {
            sel.value = 'overstory';
            if (customWrap) customWrap.style.display = 'none';
            return;
          }
          var root = (workspace.root_workspace || '').replace(/\/$/, '');
          if (path && path !== root) {
            sel.value = '__custom__';
            if (customWrap) customWrap.style.display = 'block';
            populateCustomDropdown(path).then(function() {
              if (customInput) customInput.value = path;
            });
          } else {
            sel.value = 'overstory';
            if (customWrap) customWrap.style.display = 'none';
          }
        }
        window.syncSidebarDropdownToWorkspace = syncSidebarDropdownToWorkspace;
      })();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initSidebar); else initSidebar();

    // When user scrolls up in a section, pause auto-scroll on poll until they scroll back to bottom
    function setupScrollPause(el) {
      if (!el || el._scrollPauseSetup) return;
      el._scrollPauseSetup = true;
      el._autoScrollPaused = false;
      el.addEventListener('scroll', function() {
        var atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD;
        el._autoScrollPaused = !atBottom;
      });
    }
    function scrollToBottomIfNotPaused(el) {
      if (!el) return;
      if (el._autoScrollPaused) return;
      el.scrollTop = el.scrollHeight;
    }

    // Update time display
    function updateTime() {
      const now = new Date();
      const timeStr = now.toLocaleTimeString('en-US', { hour12: true, hour: '2-digit', minute: '2-digit', second: '2-digit' });
      document.getElementById('currentTime').textContent = timeStr;
    }
    setInterval(updateTime, 1000);
    updateTime();

    // Integrated terminal: auto-start Claude CLI stream when TERMINAL tab is active
    let terminalEventSource = null;
    function connectTerminalStream() {
      if (terminalEventSource) return;
      const skip = document.getElementById('dangerouslySkipPermissions').checked;
      const url = '/api/terminal/stream?dangerously_skip_permissions=' + (skip ? '1' : '0');
      terminalEventSource = new EventSource(url);
      const terminalOutput = document.getElementById('terminalOutput');
      terminalEventSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.lines && data.lines.length) {
            data.lines.forEach(line => {
              const div = document.createElement('div');
              div.className = 'terminal-line info';
              div.textContent = line;
              terminalOutput.appendChild(div);
            });
            scrollToBottomIfNotPaused(terminalOutput);
          }
        } catch (err) {}
      };
      terminalEventSource.onerror = () => { terminalEventSource = null; };
    }
    function sendTerminalInput() {
      const input = document.getElementById('terminalInput');
      const line = (input.value || '').trim();
      if (!line) return;
      input.value = '';
      fetch('/api/terminal/input', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ line }) }).catch(() => {});
    }

    // Tabs — ensure only the selected tab's content is visible (chat form only in Chat tab)
    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const tabKey = tab.dataset.tab;
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => {
          c.classList.remove('active');
          c.style.display = 'none';
        });
        tab.classList.add('active');
        const tabName = tabKey.charAt(0).toUpperCase() + tabKey.slice(1);
        const contentId = 'tab' + tabName;
        const contentEl = document.getElementById(contentId);
        if (contentEl) {
          contentEl.classList.add('active');
          contentEl.style.display = 'flex';
        }
        if (tabKey === 'terminal') {
          connectTerminalStream();
          refreshAgentTerminals();
        }
        if (tabKey === 'kanban') refreshKanban();
      });
    });
    // Initial state: only Chat tab content visible
    document.querySelectorAll('.tab-content').forEach(c => {
      if (!c.classList.contains('active')) c.style.display = 'none';
      else c.style.display = 'flex';
    });
    document.getElementById('terminalSendBtn').addEventListener('click', sendTerminalInput);
    document.getElementById('terminalInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); sendTerminalInput(); } });
    if (document.querySelector('.tab[data-tab="terminal"]').classList.contains('active')) connectTerminalStream();

    // Sync dangerously-skip-permissions toggle with gateway (controls integrated terminal + agent wrapper)
    (async function loadSettings() {
      try {
        const r = await fetch('/api/settings');
        const data = await r.json();
        const cb = document.getElementById('dangerouslySkipPermissions');
        if (cb && typeof data.dangerously_skip_permissions !== 'undefined') cb.checked = !!data.dangerously_skip_permissions;
      } catch (_) {}
    })();
    var dangerouslySkipCb = document.getElementById('dangerouslySkipPermissions');
    if (dangerouslySkipCb) dangerouslySkipCb.addEventListener('change', async function() {
      try {
        await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dangerously_skip_permissions: this.checked }) });
      } catch (_) {}
    });

    // Prune completed worktrees (overstory worktree clean --completed) and log
    async function runPruneCompletedWorktrees(isAuto) {
      const prefix = isAuto ? 'Auto-prune: ' : '';
      try {
        const r = await fetch('/api/worktrees/clean', { method: 'POST' });
        const data = await r.json().catch(function() { return {}; });
        const logMsg = prefix + (data.logMessage || (r.ok ? 'Pruned completed worktrees.' : 'Prune failed.'));
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: logMsg, type: r.ok ? 'info' : 'error' }) }).catch(function(){});
        if (r.ok) refreshNow();
        return r.ok;
      } catch (e) {
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: prefix + 'Prune failed: ' + e.message, type: 'error' }) }).catch(function(){});
        return false;
      }
    }
    document.getElementById('pruneWorktreesBtn').addEventListener('click', async function() {
      const btn = this;
      btn.disabled = true;
      btn.textContent = 'Pruning…';
      const ok = await runPruneCompletedWorktrees(false);
      btn.textContent = ok ? 'Pruned' : 'Error';
      setTimeout(() => { btn.disabled = false; btn.textContent = 'Prune completed'; }, 2000);
    });

    async function runApproveAllAndClean() {
      try {
        const r = await fetch('/api/supervisor/approve-all', { method: 'POST' });
        const data = await r.json().catch(function() { return {}; });
        const approved = data.approved || 0;
        const reinstated = data.reinstated || 0;
        const err = data.last_error || data.error;
        let msg = data.ok ? (reinstated ? reinstated + ' lead(s) reinstated; ' : '') + approved + ' approved.' : (err || 'failed');
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Approve all: ' + msg, type: data.ok ? 'info' : 'error' }) }).catch(function(){});
        if (err) console.warn('approve-all:', err);
        const m = await fetch('/api/overstory/merge', { method: 'POST' });
        const mData = await m.json().catch(function() { return {}; });
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Merge: ' + (m.ok ? (mData.drained || 0) + ' drained.' : (mData.error || 'failed')), type: m.ok ? 'info' : 'error' }) }).catch(function(){});
        const ok = await runPruneCompletedWorktrees(false);
        if (ok) refreshNow();
        return ok;
      } catch (e) {
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Approve all & clean failed: ' + e.message, type: 'error' }) }).catch(function(){});
        return false;
      }
    }
    document.getElementById('approveAllAndCleanBtn').addEventListener('click', async function() {
      const btn = this;
      btn.disabled = true;
      btn.textContent = '…';
      await runApproveAllAndClean();
      btn.textContent = 'Done';
      setTimeout(function() { btn.disabled = false; btn.textContent = 'Approve all & clean'; }, 2000);
    });

    document.getElementById('injectLeadBtn').addEventListener('click', async function() {
      const btn = this;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const r = await fetch('/api/supervisor/inject-lead', { method: 'POST' });
        const data = await r.json().catch(function() { return {}; });
        if (data.ok && data.spawned) {
          const msg = 'Inject Lead: ' + data.lead_name + (data.mail_reassigned ? ' — ' + data.mail_reassigned + ' mail reassigned' : '');
          fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: msg, type: 'info' }) }).catch(function(){});
          btn.textContent = 'Injected';
          refreshNow();
        } else {
          fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Inject Lead failed: ' + (data.error || 'unknown'), type: 'error' }) }).catch(function(){});
          btn.textContent = 'Error';
        }
      } catch (e) {
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Inject Lead failed: ' + e.message, type: 'error' }) }).catch(function(){});
        btn.textContent = 'Error';
      }
      setTimeout(function() { btn.disabled = false; btn.textContent = 'Inject Lead'; }, 2500);
    });

    document.getElementById('restartWithSkipPermsBtn').addEventListener('click', async function() {
      const btn = this;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const r = await fetch('/api/agents/restart-with-skip-permissions', { method: 'POST' });
        const data = await r.json().catch(function() { return {}; });
        if (data.ok) {
          btn.textContent = 'Done';
          if (data.restarted && data.restarted.length) fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Restart (skip perms): ' + (data.message || data.restarted.join(', ')), type: 'info' }) }).catch(function(){});
          refreshNow();
        } else {
          btn.textContent = 'Error';
          alert(data.error || 'Failed');
        }
      } catch (e) {
        btn.textContent = 'Error';
        alert(e.message || 'Failed');
      }
      setTimeout(function() { btn.disabled = false; btn.textContent = 'Restart agents (skip perms)'; }, 3000);
    });

    document.getElementById('killAllAgentsBtn').addEventListener('click', async function() {
      if (!confirm('Kill all agents and clear mail and task queue? This stops every agent and empties the inbox so you can open a new project fresh. Continue?')) {
        return;
      }
      const btn = this;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const r = await fetch('/api/agents/kill-all', { method: 'POST' });
        const text = await r.text();
        let data = { ok: false, error: 'Invalid response' };
        if (text && text.trim()) {
          try {
            data = JSON.parse(text);
          } catch (_) {
            data.error = 'Server returned invalid response. Is the gateway running?';
          }
        } else {
          data.error = 'Empty response. Is the gateway running?';
        }
        if (data.ok) {
          const msg = (data.mail_cleared ? data.mail_cleared + ' mail cleared. ' : '') + (data.message || 'All agents stopped.');
          fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Kill all: ' + msg, type: 'info' }) }).catch(function(){});
          btn.textContent = 'Done';
          refreshNow();
        } else {
          fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Kill all failed: ' + (data.error || 'unknown'), type: 'error' }) }).catch(function(){});
          btn.textContent = 'Error';
          alert(data.error || 'Kill all failed');
        }
      } catch (e) {
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Kill all failed: ' + e.message, type: 'error' }) }).catch(function(){});
        btn.textContent = 'Error';
        alert(e.message || 'Kill all failed');
      }
      setTimeout(function() { btn.disabled = false; btn.textContent = 'Kill all agents'; }, 2500);
    });

    // Auto-prune completed worktrees every 15 minutes (MacBook-friendly)
    const AUTO_PRUNE_INTERVAL_MS = 15 * 60 * 1000;
    setInterval(function() { runPruneCompletedWorktrees(true); }, AUTO_PRUNE_INTERVAL_MS);

    // Zombie Hunter: slay zombies and update indicator
    function updateZombieSlayedIndicator() {
      const el = document.getElementById('zombiesSlayedCount');
      if (el) el.textContent = zombiesSlayedTotal;
    }
    function updateZombieCountdown() {
      const mins = Math.floor(zombieCountdownSeconds / 60);
      const secs = zombieCountdownSeconds % 60;
      const el = document.getElementById('zombieCountdown');
      if (el) el.textContent = mins + ':' + (secs < 10 ? '0' : '') + secs;
    }
    function resetZombieCountdown() {
      zombieCountdownSeconds = 60; // 1 minute
      updateZombieCountdown();
    }
    async function slayZombies() {
      const btn = document.getElementById('bloodRitualBtn');
      if (btn) { btn.disabled = true; btn.textContent = '…'; }
      try {
        const r = await fetch('/api/zombies/slay', { method: 'POST' });
        const data = await r.json();
        if (data.slain > 0) {
          zombiesSlayedTotal += data.slain;
          updateZombieSlayedIndicator();
          const text = data.message || 'Your agents were zombies so I had to kill them.';
          fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: '🧟 Zombie Hunter: ' + text + ' (' + data.slain + ' slain)', type: 'info' }) }).catch(function(){});
          resetZombieCountdown();
        }
      } catch (e) {
        fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: '🧟 Zombie Hunter: Slay failed: ' + e.message, type: 'error' }) }).catch(function(){});
      }
      if (btn) { btn.disabled = false; btn.textContent = 'Slay'; }
    }
    document.getElementById('bloodRitualBtn').addEventListener('click', slayZombies);
    updateZombieSlayedIndicator();
    updateZombieCountdown();

    // Countdown timer (updates every second)
    zombieCountdownInterval = setInterval(() => {
      if (zombieCountdownSeconds > 0) {
        zombieCountdownSeconds--;
        updateZombieCountdown();
      } else {
        resetZombieCountdown();
      }
    }, 1000);

    // Periodically check for zombies and slay them (every 1 minute; gateway also runs slayer every 60s)
    setInterval(async () => {
      try {
        const r = await fetch('/api/zombies');
        const data = await r.json();
        const countEl = document.getElementById('zombiesDetectedCount');
        if (countEl) countEl.textContent = data.count || 0;
        if (data.count > 0) {
          await slayZombies();
        }
      } catch (_) {}
    }, ZOMBIE_CHECK_INTERVAL_MS);

    // Also check zombie count on dashboard refresh
    async function checkZombies() {
      try {
        const r = await fetch('/api/zombies');
        const data = await r.json();
        const countEl = document.getElementById('zombiesDetectedCount');
        if (countEl) countEl.textContent = data.count || 0;
      } catch (_) {}
    }

    var agentTerminalScrollState = {};
    function resumeAgentTerminal() {
      activeAgentTerminal = null;
      cachedTerminalOutput = null;
      refreshAgentTerminals();
    }
    // Refresh agent terminals (tmux output for each agent) — Chat tab only; polled less often to avoid load
    async function refreshAgentTerminals() {
      const chatTab = document.getElementById('tabChat');
      if (!chatTab || !chatTab.classList.contains('active')) return;
      const list = document.getElementById('agentTerminalsList');
      if (!list) return;
      try {
        list.querySelectorAll('.agent-terminal-item').forEach(function(item) {
          var name = item.getAttribute('data-agent-name');
          if (!name) return;
          var out = item.querySelector('.agent-terminal-output');
          if (!out) return;
          var atBottom = out.scrollHeight - out.scrollTop - out.clientHeight <= SCROLL_BOTTOM_THRESHOLD;
          agentTerminalScrollState[name] = { atBottom: atBottom, scrollTop: out.scrollTop, scrollHeight: out.scrollHeight };
          if (name === activeAgentTerminal && cachedTerminalOutput) {
            cachedTerminalOutput.scrollTop = out.scrollTop;
          }
        });
        const r = await fetch('/api/overstory');
        const data = await r.json();
        if (data.error || !data.agents || data.agents.length === 0) {
          list.innerHTML = '<div style="color: #8b949e; font-size: 12px;">No active agents</div>';
          gatewayUnreachableLogged = false;
          return;
        }
        var isGatewayError = false;
        const terminals = await Promise.all(
          data.agents.slice(0, 10).map(async (agent) => {
            const name = agent.name || '';
            if (!name) return null;
            if (name === activeAgentTerminal && cachedTerminalOutput) {
              return {
                name,
                capability: agent.capability || '',
                output: cachedTerminalOutput.output,
                attachCmd: 'tmux attach -t overstory-overclaw-' + name,
                source: '',
                system: !!agent.system
              };
            }
            try {
              const termR = await fetch(`/api/agents/${name}/terminal?lines=50`);
              const termData = await termR.json();
              const err = termData.error || null;
              if (err) {
                if ((err + '').indexOf('Connection refused') !== -1 || (err + '').indexOf('Gateway unreachable') !== -1 || (err + '').indexOf('Errno 61') !== -1) {
                  isGatewayError = true;
                  return { name, capability: agent.capability || '', output: '[Gateway unreachable — start OverClaw gateway on port 18800 to view terminals]', attachCmd: 'tmux attach -t overstory-overclaw-' + name, source: '', system: !!agent.system };
                }
                if (agentErrorLogged[name] !== err) {
                  agentErrorLogged[name] = err;
                  fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Agent ' + name + ': ' + (err + '').slice(0, 300), type: 'error' }) }).catch(function(){});
                }
              }
              return {
                name,
                capability: agent.capability || '',
                output: termData.output || termData.error || '[No output]',
                attachCmd: termData.attach_cmd || null,
                source: termData.source || '',
                system: !!agent.system
              };
            } catch (e) {
              if ((e.message + '').indexOf('Connection refused') !== -1 || (e.message + '').indexOf('Failed to fetch') !== -1) isGatewayError = true;
              if (agentErrorLogged[name] !== 'load') agentErrorLogged[name] = 'load';
              return { name, capability: agent.capability || '', output: '[Error loading terminal]', attachCmd: null, source: '', system: !!agent.system };
            }
          })
        );
        if (isGatewayError && !gatewayUnreachableLogged) {
          gatewayUnreachableLogged = true;
          fetch('/api/terminal/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Gateway unreachable. Start OverClaw gateway (port 18800) to view agent terminals and use Accept disclaimer.', type: 'error' }) }).catch(function(){});
        }
        if (!isGatewayError) gatewayUnreachableLogged = false;
        var termList = terminals.filter(t => t);
        var newNames = termList.map(function(t) { return t.name; });
        var existingItems = list.querySelectorAll('.agent-terminal-item[data-agent-name]');
        var existingNames = Array.prototype.map.call(existingItems, function(el) { return el.getAttribute('data-agent-name'); });
        var sameList = existingNames.length === newNames.length && newNames.every(function(n, i) { return existingNames[i] === n; });

        if (sameList && existingItems.length > 0) {
          termList.forEach(function(t, index) {
            var item = existingItems[index];
            var out = item ? item.querySelector('.agent-terminal-output') : null;
            if (!out) return;
            var currentText = out.textContent || '';
            var newOutput = t.output || '';
            if (currentText === newOutput) return;
            out.textContent = newOutput;
            var name = t.name;
            var isActive = name === activeAgentTerminal && cachedTerminalOutput;
            if (isActive) {
              out.scrollTop = cachedTerminalOutput.scrollTop;
            } else {
              var state = agentTerminalScrollState[name];
              if (state && !state.atBottom) {
                var newSh = out.scrollHeight;
                var oldSh = state.scrollHeight;
                var oldSt = state.scrollTop;
                out.scrollTop = oldSh > 0 ? Math.min(newSh - 1, Math.round((oldSt / oldSh) * newSh)) : 0;
              } else {
                out.scrollTop = out.scrollHeight;
              }
            }
          });
        } else {
          list.innerHTML = termList.map(t => {
            var expanded = expandedAgentTerminals.has(t.name);
            var cap = (t.capability || '').toLowerCase();
            var isLeadOrSupervisor = cap === 'lead' || cap === 'supervisor';
            var bodyDisplay = (isLeadOrSupervisor && !expanded) ? 'none' : 'block';
            var isActive = t.name === activeAgentTerminal;
            return `
          <div class="agent-terminal-item ${expanded ? 'agent-terminal-expanded' : ''} ${cap === 'lead' ? 'agent-terminal-lead' : ''} ${cap === 'supervisor' ? 'agent-terminal-supervisor' : ''} ${isActive ? 'agent-terminal-active' : ''}" data-agent-name="${escapeHtml(t.name)}" style="border: 1px solid #30363d; border-radius: 4px; overflow: hidden;">
            <div class="agent-terminal-header" style="background: #161b22; padding: 6px 10px; font-size: 12px; font-weight: 600; color: #58a6ff; cursor: pointer; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px;">
              <span onclick="toggleAgentTerminalExpand('${escapeHtml(t.name)}')" title="Click to expand/collapse">${t.capability ? `<span style="color: #8b949e;">[${t.capability}]</span> ` : ''}${t.name}</span>
              <span style="display: flex; gap: 6px; align-items: center;">
                ${isActive ? '<button type="button" onclick="event.stopPropagation(); resumeAgentTerminal(); return false;" style="padding: 4px 8px; font-size: 11px; background: #58a6ff; color: #fff; border: none; border-radius: 4px; cursor: pointer;" title="Resume live updates">Resume</button>' : ''}
                <button type="button" onclick="event.stopPropagation(); toggleAgentTerminalExpand(\'' + escapeHtml(t.name) + '\'); return false;" style="padding: 4px 8px; font-size: 11px; background: #21262d; color: #8b949e; border: 1px solid #30363d; border-radius: 4px; cursor: pointer;">${expanded ? 'Collapse' : 'Expand'}</button>
                ${t.system ? '' : `<button type="button" class="accept-mail-check-btn" onclick="event.stopPropagation(); acceptMailCheck('${escapeHtml(t.name)}', this);" style="padding: 4px 8px; font-size: 11px; background: #1f6feb; color: #fff; border: none; border-radius: 4px; cursor: pointer;" title="Accept 'overstory mail check' prompt (don't ask again)">Mail check</button>
                <button type="button" class="accept-disclaimer-btn" onclick="event.stopPropagation(); acceptDisclaimer('${escapeHtml(t.name)}', this);" style="padding: 4px 10px; font-size: 11px; background: #238636; color: #fff; border: none; border-radius: 4px; cursor: pointer;">Accept disclaimer</button>`}
              </span>
            </div>
            <div class="agent-terminal-body" style="display: ${bodyDisplay};">
              ${t.attachCmd ? `<div class="agent-terminal-attach" style="background: #21262d; padding: 6px 8px; font-size: 11px; color: #7ee787; font-family: monospace;">Watch live: <code style="user-select: all;">${escapeHtml(t.attachCmd)}</code></div>` : ''}
              <div class="agent-terminal-output" style="background: #0d1117; color: #e6edf3; font-family: monospace; font-size: 11px; padding: 8px; padding-bottom: 12px; white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(t.output)}</div>
              <button type="button" class="scroll-to-bottom-btn" onclick="var o=this.previousElementSibling; if(o) o.scrollTo({top: o.scrollHeight, behavior: 'smooth'});" style="margin-top: 4px; padding: 2px 8px; font-size: 10px; background: #21262d; color: #8b949e; border: 1px solid #30363d; border-radius: 4px; cursor: pointer;">Scroll to bottom</button>
            </div>
          </div>
        `;
          }).join('');
          list.querySelectorAll('.agent-terminal-item').forEach(function(item, index) {
            var name = item.getAttribute('data-agent-name');
            if (!name) return;
            var out = item.querySelector('.agent-terminal-output');
            if (!out) return;
            var isActive = name === activeAgentTerminal && cachedTerminalOutput;
            function applyScroll() {
              if (isActive) {
                out.scrollTop = cachedTerminalOutput.scrollTop;
                out._activeScrollListener = function() {
                  if (activeAgentTerminal === name && cachedTerminalOutput) cachedTerminalOutput.scrollTop = out.scrollTop;
                };
                out.addEventListener('scroll', out._activeScrollListener, { passive: true });
              } else {
                var state = agentTerminalScrollState[name];
                if (state && !state.atBottom) {
                  var newSh = out.scrollHeight;
                  var oldSh = state.scrollHeight;
                  var oldSt = state.scrollTop;
                  out.scrollTop = oldSh > 0 ? Math.min(newSh - 1, Math.round((oldSt / oldSh) * newSh)) : 0;
                } else {
                  out.scrollTop = out.scrollHeight;
                }
              }
            }
            if (isActive) {
              requestAnimationFrame(applyScroll);
            } else {
              setTimeout(applyScroll, index * 35);
            }
          });
        }
      } catch (e) {
        list.innerHTML = '<div style="color: #f85149; font-size: 12px;">Error loading agent terminals: ' + e.message + '</div>';
      }
    }
    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }
    function toggleAgentTerminalExpand(agentName) {
      var isExpanding = !expandedAgentTerminals.has(agentName);
      if (expandedAgentTerminals.has(agentName)) {
        expandedAgentTerminals.delete(agentName);
      } else {
        expandedAgentTerminals.add(agentName);
      }
      var items = document.querySelectorAll('.agent-terminal-item[data-agent-name]');
      for (var i = 0; i < items.length; i++) {
        if (items[i].getAttribute('data-agent-name') === agentName) {
          items[i].classList.toggle('agent-terminal-expanded');
          var body = items[i].querySelector('.agent-terminal-body');
          if (body && (items[i].classList.contains('agent-terminal-lead') || items[i].classList.contains('agent-terminal-supervisor'))) {
            body.style.display = isExpanding ? 'block' : 'none';
          }
          break;
        }
      }
    }

    (function setupAgentTerminalActivation() {
      var list = document.getElementById('agentTerminalsList');
      if (!list) return;
      list.addEventListener('click', function(e) {
        if (e.target.closest('button')) return;
        var item = e.target.closest('.agent-terminal-item');
        if (!item) return;
        var name = item.getAttribute('data-agent-name');
        if (!name) return;
        if (name === activeAgentTerminal) {
          resumeAgentTerminal();
          return;
        }
        var out = item.querySelector('.agent-terminal-output');
        if (!out) return;
        activeAgentTerminal = name;
        cachedTerminalOutput = { output: out.textContent, scrollTop: out.scrollTop };
        item.classList.add('agent-terminal-active');
        document.querySelectorAll('.agent-terminal-item.agent-terminal-active').forEach(function(el) {
          if (el.getAttribute('data-agent-name') !== name) el.classList.remove('agent-terminal-active');
        });
      });
    })();

    async function acceptMailCheck(agentName, btn) {
      if (btn) btn.disabled = true;
      try {
        const r = await fetch('/api/agents/' + encodeURIComponent(agentName) + '/accept-mail-check', { method: 'POST' });
        const data = await r.json().catch(function() { return { ok: false, error: 'Invalid response' }; });
        if (r.ok && data.ok) {
          if (btn) btn.textContent = 'Sent';
          setTimeout(function() { refreshAgentTerminals(); }, 500);
        } else {
          if (btn) btn.disabled = false;
          var msg = data.error || data.message || ('HTTP ' + r.status);
          if (data.session_used) msg += ' (session: ' + data.session_used + ')';
          alert('Accept mail check failed: ' + msg);
        }
      } catch (e) {
        if (btn) btn.disabled = false;
        alert('Accept mail check failed: ' + e.message);
      }
    }
    async function acceptDisclaimer(agentName, btn) {
      if (btn) btn.disabled = true;
      try {
        const r = await fetch('/api/agents/' + encodeURIComponent(agentName) + '/accept-disclaimer', { method: 'POST' });
        const data = await r.json().catch(function() { return { ok: false, error: 'Invalid response' }; });
        if (r.ok && data.ok) {
          if (btn) btn.textContent = 'Sent';
          setTimeout(function() { refreshAgentTerminals(); }, 500);
        } else {
          if (btn) btn.disabled = false;
          var msg = data.error || data.message || ('HTTP ' + r.status);
          if (data.session_used) msg += ' (session: ' + data.session_used + ')';
          alert('Accept disclaimer failed: ' + msg);
        }
      } catch (e) {
        if (btn) btn.disabled = false;
        alert('Error: ' + e.message + '. Is the gateway running at ' + (window.GATEWAY_URL || 'localhost:18800') + '?');
      }
    }

    // Gateway health (bottom bar) — only update DOM when value changes
    var lastGatewayOk = null;
    var lastGatewayText = '';
    var lastGatewayTitle = '';
    async function refreshGatewayHealth() {
      const el = document.getElementById('footerGatewayStatus');
      if (!el) return;
      var ok = false;
      var text = 'Gateway NOT OK';
      var title = 'Gateway unreachable';
      try {
        const r = await fetch('/api/gateway/health', { cache: 'no-store' });
        const data = await r.json().catch(function() { return { ok: false }; });
        ok = r.ok && (data.ok === true || data.status === 'ok');
        text = ok ? 'Gateway OK' : 'Gateway NOT OK';
        title = ok ? 'Gateway is reachable' : (data.error || 'Gateway unreachable');
      } catch (_) {}
      if (lastGatewayOk === ok && lastGatewayText === text && lastGatewayTitle === title) return;
      lastGatewayOk = ok;
      lastGatewayText = text;
      lastGatewayTitle = title;
      el.textContent = text;
      el.className = 'footer-gateway ' + (ok ? 'ok' : 'not-ok');
      el.title = title;
    }

    // Session token usage (bottom bar) — scope toggle All / Project, only update DOM when values change
    function formatTokens(n) {
      if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
      if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
      return String(n);
    }
    var tokenScope = 'all'; // 'all' | 'project'
    var lastDisplayedIn = null, lastDisplayedOut = null, lastDisplayedTotal = null;
    var lastDisplayedTotalError = false;
    async function refreshSessionUsage() {
      var url = '/api/session-usage?scope=' + (tokenScope === 'project' ? 'project' : 'all');
      if (tokenScope === 'project') {
        try {
          const w = await fetch('/api/workspace', { cache: 'no-store' });
          const ws = await w.json();
          var projectPath = (ws && ws.path) ? encodeURIComponent(ws.path) : '';
          if (projectPath) url += '&project_path=' + projectPath;
        } catch (_) {}
      }
      try {
        const r = await fetch(url, { cache: 'no-store' });
        const data = await r.json();
        const inT = data.inputTokens || 0;
        const outT = data.outputTokens || 0;
        const total = data.totalTokens || inT + outT;
        const strIn = formatTokens(inT);
        const strOut = formatTokens(outT);
        const strTotal = '(' + formatTokens(total) + ' total)';
        if (lastDisplayedIn === strIn && lastDisplayedOut === strOut && lastDisplayedTotal === strTotal && !lastDisplayedTotalError) return;
        lastDisplayedIn = strIn;
        lastDisplayedOut = strOut;
        lastDisplayedTotal = strTotal;
        lastDisplayedTotalError = false;
        const elIn = document.getElementById('footerTokenIn');
        const elOut = document.getElementById('footerTokenOut');
        const elTotal = document.getElementById('footerTokenTotal');
        if (elIn) elIn.textContent = strIn;
        if (elOut) elOut.textContent = strOut;
        if (elTotal) elTotal.textContent = strTotal;
      } catch (_) {
        if (lastDisplayedTotalError) return;
        lastDisplayedTotalError = true;
        lastDisplayedTotal = null;
        const elTotal = document.getElementById('footerTokenTotal');
        if (elTotal) elTotal.textContent = '(—)';
      }
    }
    (function initTokenScopeToggle() {
      const btnAll = document.getElementById('tokenScopeAll');
      const btnProject = document.getElementById('tokenScopeProject');
      if (!btnAll || !btnProject) return;
      function setScope(scope) {
        tokenScope = scope;
        btnAll.classList.toggle('active', scope === 'all');
        btnProject.classList.toggle('active', scope === 'project');
        lastDisplayedIn = null;
        lastDisplayedOut = null;
        lastDisplayedTotal = null;
        refreshSessionUsage();
      }
      btnAll.addEventListener('click', function() { setScope('all'); });
      btnProject.addEventListener('click', function() { setScope('project'); });
    })();

    // Refresh functions
    function refreshNow() {
      refreshDashboard();
      refreshMail();
      refreshBunLog();
      refreshTerminal();
      refreshAgentTerminals();
      refreshKanban();
      checkZombies();
      refreshSessionUsage();
      refreshGatewayHealth();
    }

    // Initial zombie check
    checkZombies();
    refreshGatewayHealth();

    // Overstory dashboard polling — update only changed values to avoid jitter
    var lastAgentsSnapshot = '';
    var lastMailCount = null;
    var lastMergeCount = null;
    var lastMetricsText = '';
    async function refreshDashboard() {
      try {
        const r = await fetch('/api/overstory');
        const data = await r.json();
        const agentCountEl = document.getElementById('agentCount');
        const mailCountEl = document.getElementById('mailCount');
        const mergeCountEl = document.getElementById('mergeCount');
        const metricsBodyEl = document.getElementById('metricsBody');
        const tbody = document.getElementById('agentsBody');
        if (data.error) {
          if (tbody.innerHTML.indexOf(data.error) === -1) tbody.innerHTML = '<tr><td colspan="8">' + data.error + '</td></tr>';
          return;
        }
        if (agentCountEl && agentCountEl.textContent !== String(data.agents.length)) agentCountEl.textContent = data.agents.length;
        const mailCount = data.mail_count != null ? Number(data.mail_count) : 0;
        if (mailCountEl && lastMailCount !== mailCount) { mailCountEl.textContent = mailCount; lastMailCount = mailCount; }
        const mergeCount = data.merge_count != null ? Number(data.merge_count) : 0;
        if (mergeCountEl && lastMergeCount !== mergeCount) { mergeCountEl.textContent = mergeCount; lastMergeCount = mergeCount; }
        const metricsText = 'Total sessions: ' + (data.agents.length) + ' | Avg duration: ' + (data.metrics && data.metrics.avgDuration || '0s');
        if (metricsBodyEl && lastMetricsText !== metricsText) { metricsBodyEl.textContent = metricsText; lastMetricsText = metricsText; }
        function escapeHtml(s) { if (s == null) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
        function taskCell(a) {
          const short = (a.task_short || '').trim();
          const full = (a.task_full || short).trim();
          if (!short && !full) return '<td class="task-cell">—</td>';
          const summary = short || '—';
          const fullEsc = escapeHtml(full);
          const summaryEsc = escapeHtml(summary);
          return '<td class="task-cell"><details class="task-details"><summary class="task-summary" title="' + summaryEsc + '">' + summaryEsc + '</summary><pre class="task-full">' + fullEsc + '</pre></details></td>';
        }
        const newRows = data.agents.map(a => `<tr><td>${a.state_icon}</td><td>${a.name}</td><td>${a.capability}</td><td>${a.state}</td><td>${a.bead_id}</td><td>${a.duration}</td><td>${a.tmux}</td>${taskCell(a)}</tr>`).join('') || '<tr><td colspan="8">No agents</td></tr>';
        const snapshot = data.agents.length + '|' + newRows;
        if (snapshot !== lastAgentsSnapshot) {
          lastAgentsSnapshot = snapshot;
          requestAnimationFrame(function() { tbody.innerHTML = newRows; });
        }
      } catch (e) {
        const tbody = document.getElementById('agentsBody');
        const errRow = '<tr><td colspan="8">Failed to load: ' + e.message + '</td></tr>';
        if (tbody && tbody.innerHTML !== errRow) tbody.innerHTML = errRow;
      }
    }

    var KANBAN_STATUS_ORDER = ['queued', 'started', 'in progress', 'completed', 'idle', 'cancelled'];
    function durationToSeconds(dur) {
      if (!dur || dur === '—') return 0;
      var s = 0;
      dur.split(/\s+/).forEach(function(p) {
        if (p.endsWith('m')) s += parseInt(p, 10) * 60;
        else if (p.endsWith('s')) s += parseInt(p, 10);
      });
      return s;
    }
    function agentToTaskStatus(a) {
      var state = (a.state || '').toLowerCase();
      var tmuxUp = (a.tmux || '').indexOf('●') !== -1;
      var durSec = durationToSeconds(a.duration);
      var icon = a.state_icon || '';
      if (state.indexOf('zombie') !== -1 || state.indexOf('cancel') !== -1) return 'cancelled';
      if (state.indexOf('idle') !== -1) return 'idle';
      if (state.indexOf('complete') !== -1 || state.indexOf('done') !== -1 || state.indexOf('merged') !== -1) return 'completed';
      if (state.indexOf('work') !== -1 || icon === '●') {
        if (durSec < 45) return 'started';
        return 'in progress';
      }
      if (state === 'worktree' || !tmuxUp) return 'queued';
      return 'idle';
    }
    async function refreshKanban() {
      const board = document.getElementById('kanbanBoard');
      if (!board) return;
      const tab = document.querySelector('.tab[data-tab="kanban"]');
      if (tab && !tab.classList.contains('active')) return;
      try {
        const r = await fetch('/api/overstory');
        const data = await r.json();
        if (data.error) {
          board.textContent = data.error;
          return;
        }
        const agents = data.agents || [];
        function esc(s) { if (s == null) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
        var byCol = {};
        KANBAN_STATUS_ORDER.forEach(function(c) { byCol[c] = []; });
        agents.forEach(function(a) {
          var status = agentToTaskStatus(a);
          if (KANBAN_STATUS_ORDER.indexOf(status) === -1) status = 'idle';
          byCol[status].push(a);
        });
        var html = '';
        KANBAN_STATUS_ORDER.forEach(function(colKey) {
          var cards = byCol[colKey];
          var label = colKey.charAt(0).toUpperCase() + colKey.slice(1);
          html += '<div class="kanban-column"><div class="kanban-column-header">' + esc(label) + ' (' + cards.length + ')</div><div class="kanban-column-cards">';
          (cards || []).forEach(function(a) {
            var state = (a.state || '').toLowerCase();
            var isZombie = state.indexOf('zombie') !== -1;
            var dotClass = isZombie ? 'zombie' : (state.indexOf('work') !== -1 ? 'working' : 'idle');
            var cardClass = 'kanban-card' + (isZombie ? ' zombie' : '');
            var task = (a.task_short || a.task_full || '').trim().slice(0, 60);
            var cap = (a.capability || '').trim();
            html += '<div class="' + cardClass + '">';
            html += '<div class="kanban-card-name"><span class="state-dot ' + dotClass + '"></span>' + esc(a.name) + '</div>';
            if (cap) html += '<div class="kanban-card-state">' + esc(cap) + ' · ' + esc(a.state || '—') + '</div>';
            else html += '<div class="kanban-card-state">' + esc(a.state || '—') + '</div>';
            html += '<div class="kanban-card-duration">' + esc(a.duration || '') + '</div>';
            if (task) html += '<div class="kanban-card-task" title="' + esc(task) + '">' + esc(task) + '</div>';
            html += '</div>';
          });
          html += '</div></div>';
        });
        board.innerHTML = html || '<div style="color: #8b949e; padding: 16px;">No agents. Route a task from Chat to spawn agents.</div>';
      } catch (e) {
        board.textContent = 'Failed to load: ' + e.message;
      }
    }

    // Mail refresh (sent and received from mail.db) — update only when changed
    var lastMailHtml = '';
    async function refreshMail() {
      const mailBodyEl = document.getElementById('mailBody');
      const mailCountEl = document.getElementById('mailCount');
      try {
        const r = await fetch('/api/mail');
        const data = await r.json();
        if (mailCountEl && data.mail_count !== undefined && mailCountEl.textContent !== String(data.mail_count)) mailCountEl.textContent = data.mail_count;
        const newHtml = (data.mail_items && data.mail_items.length > 0)
          ? data.mail_items.map(item => {
              const from = (item.from || 'unknown').substring(0, 15);
              const to = (item.to || 'unknown').substring(0, 15);
              const subject = (item.subject || item.body || 'No subject').substring(0, 40);
              const timeAgo = item.time_ago || '';
              return `<div class="mail-item">${from} → ${to}: ${subject}${timeAgo ? ' (' + timeAgo + ')' : ''}</div>`;
            }).join('')
          : 'No mail yet. Send via gateway POST /api/agents/mail or overstory mail send.';
        if (mailBodyEl && newHtml !== lastMailHtml) {
          lastMailHtml = newHtml;
          if (data.mail_items && data.mail_items.length > 0) mailBodyEl.innerHTML = newHtml; else mailBodyEl.textContent = newHtml;
        }
      } catch (e) {
        if (mailBodyEl) mailBodyEl.textContent = 'Error loading mail: ' + e.message;
      }
    }

    // Bun log refresh (compact: last 3 lines only) — update only when changed
    var lastBunLogHtml = '';
    async function refreshBunLog() {
      try {
        const r = await fetch('/api/bun-log');
        const data = await r.json();
        const bunLog = document.getElementById('bunLog');
        if (!bunLog) return;
        const logs = (data.logs || []).slice(-3);
        const newHtml = logs.length > 0
          ? logs.map(log => {
              const line = `[${log.time}] ${log.agent}: ${log.status} (tmux=${log.tmux})`;
              return `<div class="bun-log-line" title="${line}">${line}</div>`;
            }).join('')
          : '<div class="bun-log-line">[No activity]</div>';
        if (newHtml !== lastBunLogHtml) {
          lastBunLogHtml = newHtml;
          bunLog.innerHTML = newHtml;
          scrollToBottomIfNotPaused(bunLog);
        }
      } catch (e) {
        const el = document.getElementById('bunLog');
        if (el && el.innerHTML !== '<div class="bun-log-line">[Error]</div>') el.innerHTML = '<div class="bun-log-line">[Error]</div>';
      }
    }

    // Terminal refresh — update only when changed to avoid jitter
    var lastTerminalLogHtml = '';
    async function refreshTerminal() {
      try {
        const r = await fetch('/api/terminal');
        const data = await r.json();
        const leftTerminalLog = document.getElementById('leftTerminalLog');
        if (!leftTerminalLog) return;
        const logs = (data.logs || []).slice(-50);
        const newHtml = logs.length > 0
          ? logs.map(log => {
              const timeStr = new Date(log.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
              const typeClass = log.type || 'info';
              return `<div class="terminal-line ${typeClass}">[${timeStr}] ${log.message}</div>`;
            }).join('')
          : '[No terminal output]';
        if (newHtml !== lastTerminalLogHtml) {
          lastTerminalLogHtml = newHtml;
          if (logs.length > 0) leftTerminalLog.innerHTML = newHtml; else leftTerminalLog.textContent = newHtml;
          scrollToBottomIfNotPaused(leftTerminalLog);
        }
      } catch (e) {
        // Ignore errors
      }
    }

    // Chat: unified /api/message → orchestrator (Z AI GLM)
    const chatHistory = document.getElementById('chatHistory');
    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    const chatStatus = document.getElementById('chatStatus');
    let chatHistoryList = [];
    let pendingFollowUp = null;

    function appendChat(role, text, isError) {
      chatHistoryList.push({ role, text, isError });
      const div = document.createElement('div');
      div.className = 'terminal-line ' + (isError ? 'error' : role === 'user' ? 'user' : 'info');
      div.textContent = (role === 'user' ? 'You: ' : 'Orchestrator: ') + (text != null ? String(text) : '');
      chatHistory.appendChild(div);
      chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    var thinkingConsoleInterval = null;
    function showThinking() {
      const showToggle = document.getElementById('showThinking');
      const consoleEl = document.getElementById('thinkingConsole');
      if (showToggle && !showToggle.checked) return;
      if (consoleEl) {
        consoleEl.textContent = '[' + new Date().toTimeString().slice(0, 8) + '] Orchestrator thinking...';
        consoleEl.classList.add('active');
        let dots = 0;
        thinkingConsoleInterval = setInterval(function() {
          if (!consoleEl.classList.contains('active')) { clearInterval(thinkingConsoleInterval); return; }
          dots = (dots + 1) % 4;
          consoleEl.textContent = '[' + new Date().toTimeString().slice(0, 8) + '] Orchestrator thinking' + '.'.repeat(dots);
        }, 500);
      }
      let row = document.getElementById('thinkingRow');
      if (row) return;
      row = document.createElement('div');
      row.id = 'thinkingRow';
      row.className = 'terminal-line thinking-row';
      row.innerHTML = 'Orchestrator: Thinking <span class="dot"></span><span class="dot"></span><span class="dot"></span>';
      chatHistory.appendChild(row);
      chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function hideThinking() {
      const row = document.getElementById('thinkingRow');
      if (row) row.remove();
      const consoleEl = document.getElementById('thinkingConsole');
      if (consoleEl) { consoleEl.classList.remove('active'); consoleEl.textContent = ''; }
      if (thinkingConsoleInterval) { clearInterval(thinkingConsoleInterval); thinkingConsoleInterval = null; }
    }

    async function sendMessage() {
      const routeToAgents = document.getElementById('routeToAgents').checked;
      let message = chatInput.value.trim();
      let followUpAnswers = null;

      if (pendingFollowUp) {
        const userReply = message;
        followUpAnswers = userReply.split(/\\n+/).map(s => s.trim()).filter(Boolean);
        if (followUpAnswers.length === 0) { sendBtn.disabled = false; return; }
        message = pendingFollowUp.original_message;
        pendingFollowUp = null;
      }
      if (!message) return;

      chatInput.value = '';
      if (!followUpAnswers) appendChat('user', message);
      else appendChat('user', 'Follow-up: ' + (followUpAnswers.join('; ')));
      sendBtn.disabled = true;
      chatStatus.textContent = routeToAgents ? 'Routing to agents…' : 'Agent is working…';
      chatStatus.classList.add('thinking');
      showThinking();

      const history = chatHistoryList.filter(m => !m.isError).slice(-20).map(m => ({ role: m.role, content: m.text }));

      try {
        const body = {
          message: message,
          history: history,
          route_to_agents: routeToAgents,
        };
        if (followUpAnswers && followUpAnswers.length) body.follow_up_answers = followUpAnswers;

        const r = await fetch('/api/message', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        hideThinking();
        chatStatus.textContent = '';
        chatStatus.classList.remove('thinking');

        if (!r.ok) {
          let errorText = `HTTP ${r.status}`;
          try {
            const errData = await r.json();
            errorText = errData.error || errData.detail || errorText;
          } catch {
            errorText = await r.text() || errorText;
          }
          appendChat('assistant', errorText, true);
        } else {
          const data = await r.json();
          if (data.created_project && typeof window.refreshProjectUI === 'function') window.refreshProjectUI();
          if (data.error) {
            appendChat('assistant', data.error + (data.detail ? ': ' + data.detail : ''), true);
          } else if (data.need_follow_up && Array.isArray(data.questions)) {
            pendingFollowUp = { original_message: data.original_message || message, questions: data.questions };
            const qText = 'I need a bit more context:\\n' + data.questions.map((q, i) => (i + 1) + '. ' + q).join('\\n') + '\\n\\nReply with your answers (one per line if multiple).';
            appendChat('assistant', qText);
          } else if (data.response !== undefined) {
            appendChat('assistant', data.response);
          } else if (data.spawned !== undefined || data.capability) {
            const spawned = data.spawned || data.spawn_result;
            const summary = spawned ? 'Task routed; agent spawned.' : (data.capability ? 'Routed (' + data.capability + ').' : 'Routed.');
            appendChat('assistant', summary + (data.message ? ' ' + data.message : '') + (data.spawn_error ? ' Spawn error: ' + data.spawn_error : '') + (data.created_project ? ' Project folder: ' + data.created_project : ''));
          } else {
            appendChat('assistant', data.response || data.message || JSON.stringify(data));
          }
        }
      } catch (e) {
        hideThinking();
        chatStatus.textContent = '';
        chatStatus.classList.remove('thinking');
        var msg = e.message || String(e);
        if (msg === 'Failed to fetch' || msg.indexOf('fetch') !== -1) {
          msg = 'Request failed. Ensure the OverClaw gateway is running (port 18800) and ZAI_API_KEY is set in .env. Run: ./scripts/start-overclaw.sh';
        }
        appendChat('assistant', 'Error: ' + msg, true);
      }
      sendBtn.disabled = false;
    }

    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keydown', (e) => { 
      if (e.key === 'Enter' && !e.shiftKey) { 
        e.preventDefault(); 
        sendMessage(); 
      } 
    });

    // Enable scroll-pause: scrolling up in a section disables auto-scroll-to-bottom on poll
    setupScrollPause(document.getElementById('leftTerminalLog'));
    setupScrollPause(document.getElementById('bunLog'));
    setupScrollPause(document.getElementById('terminalOutput'));

    // Settings modal: open/close, load, save
    (function initSettingsModal() {
      const modal = document.getElementById('settingsModal');
      const closeBtn = document.getElementById('settingsModalClose');
      const cancelBtn = document.getElementById('settingsCancelBtn');
      const saveBtn = document.getElementById('settingsSaveBtn');
      const footerSettingsBtn = document.getElementById('footerSettingsBtn');
      const refreshSelect = document.getElementById('settingRefreshInterval');
      const sidebarCollapsedCb = document.getElementById('settingSidebarCollapsed');
      const defaultFolderSel = document.getElementById('settingDefaultFolder');
      const customPathWrap = document.getElementById('settingCustomPathWrap');
      const customPathInput = document.getElementById('settingCustomPath');
      const newFolderWrap = document.getElementById('settingNewFolderWrap');
      const newFolderInput = document.getElementById('settingNewFolderName');
      const newFolderBtn = document.getElementById('settingNewFolderBtn');
      const skipPermsCb = document.getElementById('settingSkipPermissions');

      function openModal() {
        modal.classList.add('visible');
        loadSettingsIntoForm();
      }
      function closeModal() { modal.classList.remove('visible'); }

      async function loadSettingsIntoForm() {
        try {
          const [uiR, gwR] = await Promise.all([fetch('/api/ui-settings'), fetch('/api/settings')]);
          const ui = await uiR.json();
          const gw = await gwR.json().catch(function() { return {}; });
          REFRESH_INTERVAL = parseInt(ui.refresh_interval_ms, 10) || 3000;
          if (refreshSelect) {
            refreshSelect.value = String(REFRESH_INTERVAL);
            if (![500, 1000, 2000, 3000, 5000].includes(REFRESH_INTERVAL)) refreshSelect.value = '3000';
          }
          if (sidebarCollapsedCb) sidebarCollapsedCb.checked = !!ui.sidebar_collapsed_default;
          if (defaultFolderSel) {
            const def = (ui.default_project_folder || 'overstory').trim();
            defaultFolderSel.value = def && def !== 'overstory' ? '__custom__' : 'overstory';
            customPathWrap.style.display = defaultFolderSel.value === '__custom__' ? 'block' : 'none';
            if (newFolderWrap) newFolderWrap.style.display = defaultFolderSel.value === '__new__' ? 'block' : 'none';
            if (customPathInput) customPathInput.value = def !== 'overstory' ? def : '';
            if (newFolderInput) newFolderInput.value = '';
          }
          if (skipPermsCb) skipPermsCb.checked = !!gw.dangerously_skip_permissions;
        } catch (_) {}
      }

      async function saveSettings() {
        try {
          const payload = {
            refresh_interval_ms: parseInt(refreshSelect.value, 10) || 3000,
            sidebar_collapsed_default: !!sidebarCollapsedCb.checked,
            default_project_folder: defaultFolderSel.value === '__custom__' ? (customPathInput.value || '').trim() || 'overstory' : 'overstory'
          };
          await fetch('/api/ui-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
          await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dangerously_skip_permissions: !!skipPermsCb.checked }) });
          REFRESH_INTERVAL = payload.refresh_interval_ms;
          localStorage.setItem('overclaw_sidebar_collapsed', payload.sidebar_collapsed_default ? '1' : '0');
          var refEl = document.getElementById('refreshInterval');
          if (refEl) refEl.textContent = REFRESH_INTERVAL + 'ms';
          if (refreshIntervalId) clearInterval(refreshIntervalId);
          startRefreshLoop();
          if (typeof window.refreshProjectUI === 'function') window.refreshProjectUI();
        } catch (e) { alert('Save failed: ' + (e.message || 'Unknown error')); }
        closeModal();
      }

      if (footerSettingsBtn) footerSettingsBtn.addEventListener('click', openModal);
      if (closeBtn) closeBtn.addEventListener('click', closeModal);
      if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
      if (saveBtn) saveBtn.addEventListener('click', saveSettings);
      if (modal) modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });

      // Nanoclaw setup wizard: open from settings, run steps with console activity
      (function() {
        const wizardModal = document.getElementById('nanoclawWizardModal');
        const wizardConsole = document.getElementById('nanoclawWizardConsole');
        const wizardRunBtn = document.getElementById('nanoclawWizardRun');
        const wizardCloseBtn = document.getElementById('nanoclawWizardClose');
        const wizardOpenBtn = document.getElementById('nanoclawWizardBtn');
        function ts() { return new Date().toTimeString().slice(0, 8); }
        function log(line, status) {
          const div = document.createElement('div');
          div.className = 'wizard-line' + (status === 'ok' ? ' wizard-ok' : status === 'fail' ? ' wizard-fail' : '');
          const spanTs = document.createElement('span');
          spanTs.className = 'wizard-ts';
          spanTs.textContent = '[' + ts() + '] ';
          div.appendChild(spanTs);
          const icon = status === 'ok' ? '\u2713 ' : status === 'fail' ? '\u2717 ' : ' ';
          div.appendChild(document.createTextNode(icon + (typeof line === 'string' ? line : String(line))));
          wizardConsole.appendChild(div);
          wizardConsole.scrollTop = wizardConsole.scrollHeight;
        }
        function clearConsole() {
          wizardConsole.innerHTML = '';
        }
        wizardOpenBtn && wizardOpenBtn.addEventListener('click', function() {
          wizardModal.classList.add('visible');
          clearConsole();
          log('Click Run to check gateway, Z AI, and chat.', null);
        });
        wizardCloseBtn && wizardCloseBtn.addEventListener('click', function() { wizardModal.classList.remove('visible'); });
        wizardModal && wizardModal.addEventListener('click', function(e) { if (e.target === wizardModal) wizardModal.classList.remove('visible'); });
        wizardRunBtn && wizardRunBtn.addEventListener('click', async function() {
          const btn = wizardRunBtn;
          btn.disabled = true;
          clearConsole();
          log('Step 1: Checking gateway reachability…', null);
          try {
            const r = await fetch('/api/gateway/health', { cache: 'no-store' });
            const data = await r.json().catch(function() { return {}; });
            const ok = r.ok && (data.status === 'ok' || data.ok === true);
            if (ok) log('Gateway reachable (port ' + (data.port || '18800') + ')', 'ok');
            else log('Gateway unreachable. Start with ./scripts/start-overclaw.sh', 'fail');
          } catch (e) {
            log('Gateway unreachable: ' + (e.message || e), 'fail');
          }
          log('Step 2: Checking orchestrator (Z AI) config…', null);
          try {
            const r = await fetch('/api/gateway/status', { cache: 'no-store' });
            const data = await r.json().catch(function() { return {}; });
            const orch = data.orchestrator || {};
            const configured = !!orch.configured;
            const model = orch.model || '—';
            if (configured) log('Z AI key set, model: ' + model, 'ok');
            else log('ZAI_API_KEY not set. Add it to .env and restart the gateway.', 'fail');
          } catch (e) {
            log('Could not read gateway status: ' + (e.message || e), 'fail');
          }
          log('Step 3: Testing orchestrator chat…', null);
          try {
            const r = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'Setup test. Reply OK.' }) });
            const data = await r.json().catch(function() { return {}; });
            if (r.ok && (data.response || data.error === undefined)) log('Chat OK: orchestrator responded.', 'ok');
            else log('Chat failed: ' + (data.error || 'No response'), 'fail');
          } catch (e) {
            log('Chat request failed: ' + (e.message || e), 'fail');
          }
          log('Step 4: Checking overstory…', null);
          try {
            const r = await fetch('/api/gateway/status', { cache: 'no-store' });
            const data = await r.json().catch(function() { return {}; });
            const ov = data.overstory || {};
            const ovOk = ov.status === 'ok' || (ov.data && !ov.error);
            if (ovOk) log('Overstory status OK.', 'ok');
            else log('Overstory not ready (optional). Run overstory init if needed.', 'fail');
          } catch (e) {
            log('Overstory check skipped: ' + (e.message || e), 'fail');
          }
          log('Setup wizard finished.', null);
          btn.disabled = false;
        });
      })();

      if (defaultFolderSel) defaultFolderSel.addEventListener('change', function() {
        const v = this.value;
        customPathWrap.style.display = v === '__custom__' ? 'block' : 'none';
        if (newFolderWrap) newFolderWrap.style.display = v === '__new__' ? 'block' : 'none';
      });
      if (newFolderBtn && newFolderInput) newFolderBtn.addEventListener('click', async function() {
        const name = newFolderInput.value.trim();
        if (!name) { alert('Enter a folder name'); return; }
        try {
          const res = await fetch('/api/project/create-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
          const data = await res.json();
          if (!res.ok) { alert(data.error || 'Failed to create folder'); return; }
          if (customPathInput) customPathInput.value = data.path;
          if (defaultFolderSel) defaultFolderSel.value = '__custom__';
          if (customPathWrap) customPathWrap.style.display = 'block';
          if (newFolderWrap) { newFolderWrap.style.display = 'none'; newFolderInput.value = ''; }
          await fetch('/api/ui-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ default_project_folder: data.path }) });
          if (typeof window.refreshProjectUI === 'function') window.refreshProjectUI();
        } catch (e) { alert('Failed: ' + (e.message || 'Unknown error')); }
      });
    })();

    function startRefreshLoop() {
      var refreshTick = 0;
      if (refreshIntervalId) clearInterval(refreshIntervalId);
      refreshIntervalId = setInterval(function() {
        refreshDashboard();
        refreshMail();
        refreshBunLog();
        refreshTerminal();
        refreshSessionUsage();
        refreshGatewayHealth();
        refreshTick++;
        if (refreshTick % 3 === 0) refreshAgentTerminals();
        if (document.querySelector('.tab[data-tab="kanban"]') && document.querySelector('.tab[data-tab="kanban"]').classList.contains('active')) refreshKanban();
        if (refreshTick > 0 && refreshTick % 5 === 0) {
          fetch('/api/agents/auto-accept-prompts', { method: 'POST' }).catch(function(){});
        }
      }, REFRESH_INTERVAL);
    }

    // Initial load: ui-settings for refresh interval and sidebar default, then refresh and start loop
    (async function initRefreshAndSidebar() {
      try {
        const r = await fetch('/api/ui-settings');
        const s = await r.json();
        REFRESH_INTERVAL = parseInt(s.refresh_interval_ms, 10) || 3000;
        var refEl = document.getElementById('refreshInterval');
        if (refEl) refEl.textContent = REFRESH_INTERVAL + 'ms';
        if (localStorage.getItem('overclaw_sidebar_collapsed') === null && s.sidebar_collapsed_default) {
          localStorage.setItem('overclaw_sidebar_collapsed', '1');
          var sidebar = document.getElementById('sidebar');
          var toggle = document.getElementById('sidebarToggle');
          if (sidebar && toggle) {
            sidebar.classList.add('collapsed');
            toggle.textContent = '\u25B6';
            toggle.title = 'Show sidebar';
          }
        }
      } catch (_) {}
      refreshNow();
      startRefreshLoop();
    })();

    // --- CEO Office View: Theme Engine ---
    (function() {
      try {
      if (typeof THREE === 'undefined') { console.warn('[CEO] THREE not loaded'); return; }
      var ceoModal = document.getElementById('ceoModal');
      var ceoCanvas = document.getElementById('ceoCanvas');
      var ceoBtn = document.getElementById('ceoBtn');
      var ceoClose = document.getElementById('ceoClose');
      var ceoThemeSel = document.getElementById('ceoTheme');
      var ceoCredit = document.getElementById('ceoCredit');
      if (!ceoModal || !ceoCanvas || !ceoBtn || !ceoClose) { console.warn('[CEO] Missing DOM'); return; }

      var scene, camera, renderer;
      var agentSprites = {}, agentBubbles = {}, agentBadges = {};
      var previousAgentList = [], terminalCache = {}, inboxCounts = {};
      var lastPolledAgents = [];
      var dialogAgent = null, dialogStreamTimer = null;
      var agentDialogEl = document.getElementById('agentDialog');
      var mailFlights = [], previousMailLength = 0;
      var sendHomeQueue = [], sendHomeState = null, leadAgentName = null;
      var ceoPollTimer = null, ceoAnimId = null, ceoOpen = false;
      var activeTheme = null, themeState = {};
      var POLL_MS = 5000, BUBBLE_LINES = 5, CAP_AGENTS = 8;
      var texCache = {};
      var agentAnimStates = {};

      var PIXELS_PER_UNIT = 32;
      var TARGET_H = 480;
      var FRUSTUM_H = TARGET_H / PIXELS_PER_UNIT;

      function isLead(a) { return a.capability === 'lead' || (a.name && a.name.indexOf('lead-') === 0); }

      // --- Texture loader with cache ---
      var texLoader = new THREE.TextureLoader();
      function loadTex(url) {
        if (texCache[url]) return Promise.resolve(texCache[url]);
        return new Promise(function(ok, fail) {
          texLoader.load(url, function(t) {
            t.magFilter = THREE.NearestFilter;
            t.minFilter = THREE.NearestMipmapNearestFilter;
            t.colorSpace = THREE.SRGBColorSpace;
            texCache[url] = t;
            ok(t);
          }, undefined, fail);
        });
      }

      // Grid frame: clone texture and set UV for a cell in a grid sheet
      function gridFrame(tex, col, row, cols, rows) {
        var t = tex.clone();
        t.repeat.set(1 / cols, 1 / rows);
        t.offset.set(col / cols, 1 - (row + 1) / rows);
        t.minFilter = t.magFilter = THREE.NearestFilter;
        t.needsUpdate = true;
        return t;
      }
      // Strip frame: horizontal strip, N frames
      function stripFrame(tex, frame, total) {
        var t = tex.clone();
        t.repeat.set(1 / total, 1);
        t.offset.set(frame / total, 0);
        t.minFilter = t.magFilter = THREE.NearestFilter;
        t.needsUpdate = true;
        return t;
      }

      function makeSprite(tex, w, h) {
        var mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
        var s = new THREE.Sprite(mat);
        s.scale.set(w, h, 1);
        return s;
      }

      function scaleSprite(sprite, texture) {
        sprite.scale.set(texture.image.width / PIXELS_PER_UNIT, texture.image.height / PIXELS_PER_UNIT, 1);
      }

      function scaleSpriteFromStrip(sprite, texture, frameCount) {
        if (!texture || !texture.image || !texture.image.width) {
          sprite.scale.set(1, 1, 1);
          return;
        }
        var fw = frameCount ? texture.image.width / frameCount : texture.image.width;
        var fh = texture.image.height;
        if (!fw || !fh) { sprite.scale.set(1, 1, 1); return; }
        sprite.scale.set(fw / PIXELS_PER_UNIT, fh / PIXELS_PER_UNIT, 1);
      }

      function SpriteAnimState(totalFrames, fps) {
        this.frame = 0;
        this.timer = 0;
        this.totalFrames = totalFrames;
        this.fps = fps;
      }

      function animateStrip(sprite, tex, animState, delta) {
        animState.timer += delta;
        if (animState.timer >= 1 / animState.fps) {
          animState.timer = 0;
          animState.frame = (animState.frame + 1) % animState.totalFrames;
          sprite.material.map = stripFrame(tex, animState.frame, animState.totalFrames);
          sprite.material.map.needsUpdate = true;
        }
      }

      function disposeObject(obj) {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          if (Array.isArray(obj.material)) {
            obj.material.forEach(function(m) { if (m.map) m.map.dispose(); m.dispose(); });
          } else {
            if (obj.material.map) obj.material.map.dispose();
            obj.material.dispose();
          }
        }
        if (scene) scene.remove(obj);
      }

      // --- Bubble (shared) ---
      function makeBubbleTexture(lines) {
        var w = 256, h = 120;
        var padding = 12;
        var maxTextWidth = w - padding * 2;
        var lineHeight = 14;
        var c = document.createElement('canvas'); c.width = w; c.height = h;
        var ctx = c.getContext('2d');
        ctx.fillStyle = 'rgba(22,27,34,0.92)'; ctx.strokeStyle = '#30363d'; ctx.lineWidth = 2;
        ctx.beginPath();
        if (typeof ctx.roundRect === 'function') ctx.roundRect(4, 4, w - 8, h - 8, 8);
        else ctx.rect(4, 4, w - 8, h - 8);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#e6edf3'; ctx.font = '11px ui-monospace,monospace'; ctx.textAlign = 'left';
        var text = (lines || []).slice(-BUBBLE_LINES).join('\\n') || '...';
        if (text.length > 400) text = text.slice(-400);
        var y = 22;
        var maxLines = Math.floor((h - y - padding) / lineHeight);
        function wrapLine(str) {
          var result = [];
          var words = str.split(/\\s+/);
          var line = '';
          for (var i = 0; i < words.length; i++) {
            var trial = line ? line + ' ' + words[i] : words[i];
            if (ctx.measureText(trial).width <= maxTextWidth) {
              line = trial;
            } else {
              if (line) result.push(line);
              if (ctx.measureText(words[i]).width <= maxTextWidth) {
                line = words[i];
              } else {
                var rest = words[i];
                while (rest.length) {
                  var chunk = rest;
                  while (chunk.length && ctx.measureText(chunk).width > maxTextWidth) chunk = chunk.slice(0, -1);
                  result.push(chunk);
                  rest = rest.slice(chunk.length);
                }
                line = '';
              }
            }
          }
          if (line) result.push(line);
          return result;
        }
        function truncateToFit(str) {
          if (ctx.measureText(str).width <= maxTextWidth) return str;
          var s = str + '\u2026';
          while (s.length > 1 && ctx.measureText(s).width > maxTextWidth) s = str.slice(0, s.length - 2) + '\u2026';
          return s;
        }
        var flat = [];
        text.split('\\n').forEach(function(ln) {
          var trimmed = ln.trim();
          if (!trimmed) return;
          wrapLine(trimmed).forEach(function(sub) { flat.push(sub); });
        });
        if (flat.length === 0) flat.push('...');
        for (var i = 0; i < flat.length && i < maxLines && y < h - padding; i++) {
          var display = truncateToFit(flat[i]);
          ctx.fillText(display, padding, y);
          y += lineHeight;
        }
        var tex = new THREE.CanvasTexture(c);
        tex.minFilter = tex.magFilter = THREE.NearestFilter;
        return tex;
      }

      // --- Speech bubbles: summarized task + mail + personality ---
      function speechLinesForAgent(agent, inboxCount, mailForAgent, otherNames) {
        var name = (agent && agent.name) ? agent.name : '';
        var capability = ((agent && agent.capability) || '').toLowerCase();
        var state = ((agent && agent.state) || '').toLowerCase();
        var taskShort = (agent && agent.task_short) ? String(agent.task_short).trim() : '';
        var taskFull = (agent && agent.task_full) ? String(agent.task_full).trim() : taskShort;
        var isWorking = state.indexOf('work') !== -1 || (agent && agent.state_icon === '●');
        var isLead = capability === 'lead' || (name && name.indexOf('lead') === 0);
        var role = isLead ? 'lead' : (capability.indexOf('builder') !== -1 ? 'builder' : (capability.indexOf('scout') !== -1 ? 'scout' : (capability.indexOf('review') !== -1 ? 'reviewer' : 'default')));
        var lines = [];
        function hash(s) { var h = 0; for (var i = 0; i < (s || '').length; i++) h = ((h << 5) - h) + s.charCodeAt(i); return h >>> 0; }
        var personalityIdx = hash(name) % 6;
        var personalities = [
          { idle: "Just thinking...", mail: "Ooh, mail!", wait: "I should ask my lead.", task: "On it." },
          { idle: "Taking a breather.", mail: "Someone wrote me!", wait: "Agent is taking forever to reply.", task: "Got it." },
          { idle: "Hmm.", mail: "More mail. Great.", wait: "This is hard, I should email my lead.", task: "Working on it." },
          { idle: "Idle for now.", mail: "Mail from the team!", wait: "Waiting on a reply...", task: "Sure thing." },
          { idle: "Standing by.", mail: "Inbox says hi.", wait: "Where's that reply?", task: "Let's go." },
          { idle: "Chillin'.", mail: "Letter time!", wait: "Maybe I'll ping the lead.", task: "On the case." }
        ];
        var p = personalities[personalityIdx];
        function shortenTask(t, maxLen) {
          if (!t) return '';
          t = t.replace(/^\\s+/, '').replace(/\\s+$/, '');
          if (t.length <= (maxLen || 45)) return t;
          var last = t.lastIndexOf('.', maxLen);
          if (last > 20) return t.slice(0, last + 1);
          if (t.lastIndexOf(' ', maxLen) > 15) return t.slice(0, t.lastIndexOf(' ', maxLen)) + '…';
          return t.slice(0, maxLen - 1) + '…';
        }
        if (taskShort) {
          var q = shortenTask(taskShort, 42);
          if (q && q.indexOf('?') === -1 && q.length > 10 && !/^(fix|add|implement|write|run)/i.test(q)) q = q.replace(/\\.$/, '') + '?';
          if (q) lines.push(q);
        }
        if (mailForAgent && mailForAgent.length > 0) {
          var last = mailForAgent[0];
          var from = (last.from || 'someone').replace(/[-0-9a-f]{4,}$/i, '').trim() || 'someone';
          var subj = (last.subject || last.body || '').trim().slice(0, 28);
          if (subj) lines.push("Mail from " + from + ": \\"" + subj + (subj.length >= 28 ? "…" : "") + "\\"");
          else lines.push("Mail from " + from + "!");
        } else if (inboxCount > 0) lines.push(p.mail);
        if (isLead) {
          if (lines.length === 0) lines.push("Keeping an eye on everyone.");
        } else if (role === 'reviewer' && isWorking && otherNames && otherNames.length > 0) {
          var other = otherNames[hash(name + 'x') % otherNames.length];
          if (other && other !== name) {
            if (lines.length === 0) lines.push("Reviewing " + other + "'s work.");
            else if (lines.length === 1) lines.push("Reviewing " + other + "'s work.");
          }
        }
        if (lines.length === 0) {
          if (isWorking) lines.push(p.task);
          else lines.push(p.idle);
        }
        if (lines.length < 2 && isWorking && !isLead && Math.abs(hash(name + 'w')) % 3 === 0) lines.push(p.wait);
        return lines.slice(0, 3);
      }

      // --- Station positions for themed layouts ---
      var ZOMBIE_POSITIONS = [
        { x: 0, y: 0.01, z: -1, role: 'lead' },
        { x: -3, y: 0.01, z: 0, role: 'builder' },
        { x: 3, y: 0.01, z: 0, role: 'builder' },
        { x: -6, y: 0.01, z: 1, role: 'scout' },
        { x: 6, y: 0.01, z: 1, role: 'scout' },
        { x: -2, y: 0.01, z: 2.5, role: 'reviewer' },
        { x: 2, y: 0.01, z: 2.5, role: 'default' },
        { x: 0, y: 0.01, z: 3.5, role: 'default' }
      ];
      var SUNNYSIDE_POSITIONS = [
        { x: 0, y: 0.01, z: -2, role: 'lead' },
        { x: -4, y: 0.01, z: -0.5, role: 'builder' },
        { x: 4, y: 0.01, z: -0.5, role: 'builder' },
        { x: -6, y: 0.01, z: 1.5, role: 'scout' },
        { x: 6, y: 0.01, z: 1.5, role: 'scout' },
        { x: -2, y: 0.01, z: 2, role: 'reviewer' },
        { x: 2, y: 0.01, z: 2, role: 'reviewer' },
        { x: 0, y: 0.01, z: 3.5, role: 'default' }
      ];

      function getAgentRole(a) {
        if (isLead(a)) return 'lead';
        var c = (a.capability || '').toLowerCase();
        if (c.indexOf('builder') !== -1) return 'builder';
        if (c.indexOf('scout') !== -1) return 'scout';
        if (c.indexOf('review') !== -1) return 'reviewer';
        return 'default';
      }

      function assignPositions(agents, positions) {
        var map = {};
        var used = {};
        for (var i = 0; i < agents.length && i < CAP_AGENTS; i++) {
          var role = getAgentRole(agents[i]);
          var bestIdx = -1;
          for (var j = 0; j < positions.length; j++) {
            if (used[j]) continue;
            if (positions[j].role === role) { bestIdx = j; break; }
          }
          if (bestIdx < 0) {
            for (var j2 = 0; j2 < positions.length; j2++) { if (!used[j2]) { bestIdx = j2; break; } }
          }
          if (bestIdx >= 0) { used[bestIdx] = true; map[agents[i].name] = positions[bestIdx]; }
        }
        return map;
      }

      // =============== ZOMBIE THEME ===============
      var zombieTheme = {
        name: 'Zombie Survival',
        credit: 'Post-Apocalypse pack',
        bg: 0x0a0e0a,
        positions: ZOMBIE_POSITIONS,
        mailColor: 0xffa657,
        homeX: 12,
        textures: {},
        objects: [],
        zombies: [],

        preload: function() {
          var base = '/static/pa/';
          var paths = [
            'Character/Main/Idle/Character_side_idle-Sheet6.png',
            'Character/Main/Run/Character_side_run-Sheet6.png',
            'Character/Main/Punch/Character_side_punch-Sheet4.png',
            'Character/Bat/Bat_side_idle-and-run-Sheet6.png',
            'Character/Bat/Bat_side_attack-Sheet4.png',
            'Character/Guns/Gun/Gun_side_idle-and-run-Sheet6.png',
            'Character/Guns/Gun/Gun_side_shoot-Sheet3.png',
            'Character/Main/Pick-up/Character_side_Pick-up-Sheet3.png',
            'Enemies/Zombie_Small/Zombie_Small_Side_Walk-Sheet6.png',
            'Enemies/Zombie_Small/Zombie_Small_Side_Idle-Sheet6.png',
            'Enemies/Zombie_Big/Zombie_Big_Side_Walk-Sheet8.png'
          ];
          var self = this;
          return Promise.all(paths.map(function(p) {
            return loadTex(base + p).then(function(t) { self.textures[p] = t; }).catch(function() {});
          })).then(function() { console.log('[CEO:Zombie] Textures loaded'); });
        },

        initScene: function() {
          var self = this;
          self.objects = []; self.zombies = [];

          // Atmosphere: thick fog for post-apocalyptic feel
          scene.fog = new THREE.FogExp2(0x1a1812, 0.045);
          scene.background = new THREE.Color(self.bg);

          // Lighting: low sun (warm orange), dim ambient, fire point lights
          scene.add(new THREE.AmbientLight(0x332211, 0.5));
          var sun = new THREE.DirectionalLight(0xff8844, 1.0);
          sun.position.set(-8, 18, 10);
          sun.castShadow = false;
          scene.add(sun);
          var fill = new THREE.DirectionalLight(0x443322, 0.35);
          fill.position.set(5, 8, -5);
          scene.add(fill);
          var fire1 = new THREE.PointLight(0xff5522, 1.2, 12);
          fire1.position.set(5.5, 0.8, -1.8);
          scene.add(fire1); self.objects.push(fire1);
          var fire2 = new THREE.PointLight(0xff4422, 0.9, 10);
          fire2.position.set(-5.2, 0.7, 2.2);
          scene.add(fire2); self.objects.push(fire2);

          // Ground: dark cracked earth (larger)
          var floorGeo = new THREE.PlaneGeometry(36, 22);
          var floorMat = new THREE.MeshLambertMaterial({ color: 0x1e2618, side: THREE.DoubleSide });
          var floor = new THREE.Mesh(floorGeo, floorMat);
          floor.rotation.x = -Math.PI / 2; floor.position.y = -0.02;
          scene.add(floor); self.objects.push(floor);

          // Main road (cross shape)
          var pathMat = new THREE.MeshLambertMaterial({ color: 0x3d3d3d, side: THREE.DoubleSide });
          [[0, 0, 4, 22], [0, 0, 28, 2.2]].forEach(function(r) {
            var pathGeo = new THREE.PlaneGeometry(r[2], r[3]);
            var pathMesh = new THREE.Mesh(pathGeo, pathMat);
            pathMesh.rotation.x = -Math.PI / 2; pathMesh.position.set(r[0], -0.015, r[1]);
            scene.add(pathMesh); self.objects.push(pathMesh);
          });

          // Grass/debris patches (darker, varied)
          var grassMat = new THREE.MeshLambertMaterial({ color: 0x2d3d22, side: THREE.DoubleSide });
          [[-8, 3], [-4, -4], [6, 4], [9, -3], [-10, -2], [3, -5], [-7, 5]].forEach(function(p) {
            var gg = new THREE.PlaneGeometry(3 + Math.random() * 2, 2.5 + Math.random());
            var gm = new THREE.Mesh(gg, grassMat);
            gm.rotation.x = -Math.PI / 2; gm.position.set(p[0], -0.01, p[1]);
            scene.add(gm); self.objects.push(gm);
          });

          // Ruined shelter: posts + broken wall
          var wallMat = new THREE.MeshLambertMaterial({ color: 0x5c4a32 });
          [[-2.6, 1.0, -1.6], [2.6, 1.0, -1.6], [-2.6, 1.0, 1.1], [2.6, 1.0, 1.1]].forEach(function(p) {
            var wg = new THREE.BoxGeometry(0.7, 2.0, 0.7);
            var w = new THREE.Mesh(wg, wallMat);
            w.position.set(p[0], p[1], p[2]); scene.add(w); self.objects.push(w);
          });
          var roofGeo = new THREE.PlaneGeometry(8.5, 5.5);
          var roofMat = new THREE.MeshLambertMaterial({ color: 0x3d2e1a, side: THREE.DoubleSide });
          var roof = new THREE.Mesh(roofGeo, roofMat);
          roof.rotation.x = -Math.PI / 2; roof.position.set(0, 2.1, -0.2);
          scene.add(roof); self.objects.push(roof);

          // Barrels + rubble (more clusters)
          var barrelMat = new THREE.MeshLambertMaterial({ color: 0x6b3510 });
          [[5.2, 0.6, -2], [5.8, 0.6, -1.4], [5.4, 0.6, -0.9], [-5.2, 0.6, 2.2], [-5.8, 0.6, 2.6], [-5.4, 0.6, 1.8], [0, 0.4, -4.5]].forEach(function(p) {
            var bg = new THREE.BoxGeometry(0.65, p[1] * 2, 0.65);
            var b = new THREE.Mesh(bg, barrelMat);
            b.position.set(p[0], p[1], p[2]); scene.add(b); self.objects.push(b);
          });
          var rubbleMat = new THREE.MeshLambertMaterial({ color: 0x4a4a4a });
          [[-9, 0.3, 1], [-9.5, 0.25, 0.5], [8, 0.35, -2], [8.5, 0.3, -1.5]].forEach(function(p) {
            var rg = new THREE.BoxGeometry(1.2, p[1], 0.8);
            var r = new THREE.Mesh(rg, rubbleMat);
            r.position.set(p[0], p[1], p[2]); scene.add(r); self.objects.push(r);
          });

          // Fence line (back of scene)
          var fenceMat = new THREE.MeshLambertMaterial({ color: 0x454545 });
          for (var fx = -12; fx <= 12; fx += 2.0) {
            var fg = new THREE.BoxGeometry(1.9, 1.1, 0.15);
            var f = new THREE.Mesh(fg, fenceMat);
            f.position.set(fx, 0.5, -5.2); scene.add(f); self.objects.push(f);
          }

          // Ruined buildings (blocky structures)
          var buildingMat = new THREE.MeshLambertMaterial({ color: 0x4a4035 });
          var buildingMat2 = new THREE.MeshLambertMaterial({ color: 0x3d352a });
          [[-10, 1.2, 4], [11, 1.0, 3], [-11, 0.8, -2], [9, 1.4, -3]].forEach(function(p, i) {
            var bw = 2.5 + (i % 2) * 1.5, bd = 2 + (i % 2), bh = 2 + Math.random() * 1.5;
            var bg = new THREE.BoxGeometry(bw, bh, bd);
            var b = new THREE.Mesh(bg, i % 2 ? buildingMat2 : buildingMat);
            b.position.set(p[0], p[1], p[2]);
            scene.add(b); self.objects.push(b);
          });

          // Broken walls and barriers
          [[-7, 0.9, 4.5], [7, 0.8, 4], [-8, 0.7, -4], [10, 0.9, -2]].forEach(function(p) {
            var wg = new THREE.BoxGeometry(2.2, 1.4, 0.4);
            var w = new THREE.Mesh(wg, wallMat);
            w.position.set(p[0], p[1], p[2]);
            scene.add(w); self.objects.push(w);
          });

          // Crates and pallets
          [[3, 0.35, 2], [3.6, 0.35, 2.3], [4, 0.35, 1.8], [-4, 0.4, -3], [-4.5, 0.4, -2.8]].forEach(function(p) {
            var cg = new THREE.BoxGeometry(0.8, 0.7, 0.6);
            var c = new THREE.Mesh(cg, barrelMat);
            c.position.set(p[0], p[1], p[2]);
            scene.add(c); self.objects.push(c);
          });

          // Zombies: mix of small (walk + idle) and big (walk), at multiple depths
          var zwTex = self.textures['Enemies/Zombie_Small/Zombie_Small_Side_Walk-Sheet6.png'];
          var ziTex = self.textures['Enemies/Zombie_Small/Zombie_Small_Side_Idle-Sheet6.png'];
          var zbTex = self.textures['Enemies/Zombie_Big/Zombie_Big_Side_Walk-Sheet8.png'];
          var zombieSpecs = [
            { tex: zwTex, frames: 6, w: 3.0, h: 2.0, x: -8, z: -5.2, vx: 0.4, idle: false },
            { tex: zbTex, frames: 8, w: 3.2, h: 2.2, x: -3, z: -5.2, vx: 0.25, idle: false },
            { tex: ziTex, frames: 6, w: 3.0, h: 2.0, x: 2, z: -5.2, vx: 0, idle: true },
            { tex: zwTex, frames: 6, w: 3.0, h: 2.0, x: 7, z: -5.2, vx: -0.35, idle: false },
            { tex: zbTex, frames: 8, w: 3.2, h: 2.2, x: -6, z: -3.5, vx: 0.3, idle: false },
            { tex: zwTex, frames: 6, w: 3.0, h: 2.0, x: 6, z: -3.8, vx: -0.4, idle: false },
            { tex: ziTex, frames: 6, w: 3.0, h: 2.0, x: 0, z: -4, vx: 0, idle: true },
            { tex: zbTex, frames: 8, w: 3.2, h: 2.2, x: 9, z: 2, vx: -0.28, idle: false },
            { tex: zwTex, frames: 6, w: 3.0, h: 2.0, x: -9, z: 3, vx: 0.35, idle: false }
          ];
          zombieSpecs.forEach(function(spec) {
            var zt = spec.tex;
            if (!zt) return;
            var zf = stripFrame(zt, 0, spec.frames);
            var zs = makeSprite(zf, spec.w, spec.h);
            scaleSpriteFromStrip(zs, zt, spec.frames);
            zs.position.set(spec.x, 1.0, spec.z);
            scene.add(zs); self.objects.push(zs);
            self.zombies.push({
              sprite: zs, x: spec.x, z: spec.z, vx: spec.vx, origVx: spec.vx,
              frames: spec.frames, tex: zt, origTex: zt,
              animState: new SpriteAnimState(spec.frames, 5 + Math.random() * 2),
              idle: spec.idle, stateTimer: Math.random() * 5, isSmall: (spec.frames === 6 && !spec.idle)
            });
          });

          console.log('[CEO:Zombie] Scene built');
        },

        updateAgents: function(agents, posMap, delta) {
          var self = this;
          for (var i = 0; i < agents.length && i < CAP_AGENTS; i++) {
            var a = agents[i];
            var pos = posMap[a.name];
            if (!pos) continue;
            var role = getAgentRole(a);
            if (!agentAnimStates[a.name]) {
              agentAnimStates[a.name] = { actionPhase: false, phaseTimer: 0, animState: new SpriteAnimState(6, 5), currentTex: null, currentFrames: 6 };
            }
            var st = agentAnimStates[a.name];
            st.phaseTimer += delta || 0.016;
            if (st.phaseTimer > 4) { st.actionPhase = !st.actionPhase; st.phaseTimer = 0; st.animState.frame = 0; st.animState.timer = 0; }
            var tex, totalFrames;
            if (role === 'lead') {
              var idleTex = self.textures['Character/Guns/Gun/Gun_side_idle-and-run-Sheet6.png'];
              var shootTex = self.textures['Character/Guns/Gun/Gun_side_shoot-Sheet3.png'];
              if (st.actionPhase && shootTex) { tex = shootTex; totalFrames = 3; }
              else if (idleTex) { tex = idleTex; totalFrames = 6; }
            } else if (role === 'builder') {
              var punchTex = self.textures['Character/Main/Punch/Character_side_punch-Sheet4.png'];
              var idleTex2 = self.textures['Character/Main/Idle/Character_side_idle-Sheet6.png'];
              if (st.actionPhase && punchTex) { tex = punchTex; totalFrames = 4; }
              else if (idleTex2) { tex = idleTex2; totalFrames = 6; }
            } else if (role === 'scout') {
              var runTex = self.textures['Character/Main/Run/Character_side_run-Sheet6.png'];
              if (runTex) { tex = runTex; totalFrames = 6; }
            } else if (role === 'reviewer') {
              var batIdleTex = self.textures['Character/Bat/Bat_side_idle-and-run-Sheet6.png'];
              var batAtkTex = self.textures['Character/Bat/Bat_side_attack-Sheet4.png'];
              if (st.actionPhase && batAtkTex) { tex = batAtkTex; totalFrames = 4; }
              else if (batIdleTex) { tex = batIdleTex; totalFrames = 6; }
            } else {
              var pickTex = self.textures['Character/Main/Pick-up/Character_side_Pick-up-Sheet3.png'];
              var idleTex3 = self.textures['Character/Main/Idle/Character_side_idle-Sheet6.png'];
              if (st.actionPhase && pickTex) { tex = pickTex; totalFrames = 3; }
              else if (idleTex3) { tex = idleTex3; totalFrames = 6; }
            }
            if (!tex) continue;
            if (st.currentTex !== tex) { st.currentTex = tex; st.currentFrames = totalFrames; st.animState = new SpriteAnimState(totalFrames, 5); }
            if (!agentSprites[a.name]) {
              var ft = stripFrame(tex, 0, totalFrames);
              var s = makeSprite(ft, 3.0, 2.0);
              scaleSpriteFromStrip(s, tex, totalFrames);
              s._baseScaleX = s.scale.x; s._baseScaleY = s.scale.y;
              s.position.set(pos.x, pos.y + 1.0, pos.z);
              scene.add(s); agentSprites[a.name] = s;
            } else {
              animateStrip(agentSprites[a.name], tex, st.animState, delta || 0.016);
              agentSprites[a.name].position.set(pos.x, pos.y + 1.0, pos.z);
            }
          }
        },

        tickAmbient: function(delta) {
          var self = this;
          var ziTex = self.textures['Enemies/Zombie_Small/Zombie_Small_Side_Idle-Sheet6.png'];
          self.zombies.forEach(function(z) {
            // Small walking zombies cycle between walk and idle
            if (z.isSmall && ziTex) {
              z.stateTimer += delta;
              if (!z.idle && z.stateTimer > 5 + Math.random() * 3) {
                z.idle = true; z.stateTimer = 0; z.vx = 0;
                z.tex = ziTex; z.frames = 6;
                z.animState = new SpriteAnimState(6, 4);
              } else if (z.idle && z.stateTimer > 2.5) {
                z.idle = false; z.stateTimer = 0;
                z.vx = z.origVx || ((Math.random() > 0.5 ? 1 : -1) * 0.35);
                z.tex = z.origTex; z.frames = 6;
                z.animState = new SpriteAnimState(6, 5 + Math.random() * 2);
              }
            }
            if (!z.idle && z.vx !== 0) {
              z.x += z.vx * delta;
              if (z.x > 11 || z.x < -11) z.vx = -z.vx;
              z.sprite.position.x = z.x;
              if (z.vx < 0) z.sprite.scale.x = -Math.abs(z.sprite.scale.x);
              else z.sprite.scale.x = Math.abs(z.sprite.scale.x);
            }
            animateStrip(z.sprite, z.tex, z.animState, delta);
          });
        },

        dispose: function() {
          var self = this;
          if (scene.fog) scene.fog = null;
          self.objects.forEach(function(o) { disposeObject(o); });
          self.zombies.forEach(function(z) { if (z.sprite) disposeObject(z.sprite); });
          self.objects = []; self.zombies = [];
        }
      };


      // =============== TINY WONDER FARM THEME ===============
      var TINYFARM_POSITIONS = [
        { x: -1, y: 0.01, z: -2.5, role: 'lead' },
        { x: -4, y: 0.01, z: -0.5, role: 'builder' },
        { x: 4, y: 0.01, z: -0.5, role: 'builder' },
        { x: -6, y: 0.01, z: 1.5, role: 'scout' },
        { x: 6, y: 0.01, z: 1.5, role: 'scout' },
        { x: -2, y: 0.01, z: 2, role: 'reviewer' },
        { x: 2, y: 0.01, z: 2, role: 'reviewer' },
        { x: 0, y: 0.01, z: 3.5, role: 'default' }
      ];

      var tinyfarmTheme = {
        name: 'Tiny Wonder Farm',
        credit: 'Tiny Wonder Farm by Butterymilk; characters & elements by Daniel Diggle (Sunnyside World)',
        bg: 0xa5c543,
        positions: TINYFARM_POSITIONS,
        mailColor: 0xf4b467,
        homeX: -12,
        textures: {},
        objects: [],
        plants: [],
        plantTimer: 0,
        animals: [],
        crops: [],
        enemies: [],
        windmillSprite: null,
        windmillAnimState: null,
        fireSprites: [],
        enemySpawnTimer: 0,
        phaseTimer: 0,
        combatPhase: false,

        _hairForRole: function(role) {
          var map = { lead: 'spikeyhair', builder: 'bowlhair', scout: 'longhair', reviewer: 'curlyhair', default: 'mophair' };
          return map[role] || 'base';
        },

        _actionForRole: function(role, combat) {
          if (combat) {
            if (role === 'lead') return { folder: 'ATTACK', suf: 'attack', frames: 10 };
            if (role === 'builder') return { folder: 'AXE', suf: 'axe', frames: 10 };
            if (role === 'scout') return { folder: 'RUN', suf: 'run', frames: 8 };
            if (role === 'reviewer') return { folder: 'ATTACK', suf: 'attack', frames: 10 };
            return { folder: 'MINING', suf: 'mining', frames: 10 };
          }
          if (role === 'lead') return { folder: 'IDLE', suf: 'idle', frames: 9 };
          if (role === 'builder') return { folder: 'HAMMERING', suf: 'hamering', frames: 23 };
          if (role === 'scout') return { folder: 'WALKING', suf: 'walk', frames: 8 };
          if (role === 'reviewer') return { folder: 'WATERING', suf: 'watering', frames: 5 };
          return { folder: 'DIG', suf: 'dig', frames: 13 };
        },

        preload: function() {
          var self = this;
          var FARM_TWF = 'Tiny Wonder Farm Free 3/';
          var FARM_SW = 'Sunnyside_World_ASSET_PACK_V2.1 2/Sunnyside_World_Assets/';
          var farmTwfPaths = [
            FARM_TWF + 'objects&items/farm objects free.png',
            FARM_TWF + 'objects&items/plants free.png',
            FARM_TWF + 'objects&items/furniture free.png',
            FARM_TWF + 'objects&items/items free.png',
            FARM_TWF + 'tilemaps/spring farm tilemap.png',
            FARM_TWF + 'tilemaps/farm bridges free.png',
            FARM_TWF + 'tilemaps/farm inside free.png',
            FARM_TWF + 'characters/main character/walk and idle.png',
            FARM_TWF + 'characters/main character/portrait male.png',
            FARM_TWF + 'characters/main character/portrait female.png',
            FARM_TWF + 'characters/main character old/walk and idle.png',
            FARM_TWF + 'characters/main character old/portrait male.png',
            FARM_TWF + 'characters/main character old/portrait female.png'
          ];
          var farmElPaths = [
            FARM_SW + 'Elements/Other/spr_deco_windmill_strip9.png',
            FARM_SW + 'Elements/Animals/spr_deco_chicken_01_strip4.png',
            FARM_SW + 'Elements/Animals/spr_deco_cow_strip4.png',
            FARM_SW + 'Elements/Animals/spr_deco_sheep_01_strip4.png',
            FARM_SW + 'Elements/Animals/spr_deco_duck_01_strip4.png',
            FARM_SW + 'Elements/Animals/spr_deco_pig_01_strip4.png',
            FARM_SW + 'Elements/Animals/spr_deco_bird_01_strip4.png',
            FARM_SW + 'Elements/Plants/spr_deco_tree_01_strip4.png',
            FARM_SW + 'Elements/Plants/spr_deco_tree_02_strip4.png',
            FARM_SW + 'Elements/Plants/spr_deco_mushroom_red_01_strip4.png',
            FARM_SW + 'Elements/Plants/spr_deco_mushroom_blue_01_strip4.png',
            FARM_SW + 'Elements/VFX/Fire/spr_deco_fire_01_strip4.png',
            FARM_SW + 'Elements/VFX/Glint/spr_deco_glint_01_strip6.png'
          ];
          var hairTypes = ['base', 'bowlhair', 'curlyhair', 'longhair', 'mophair', 'shorthair', 'spikeyhair'];
          var farmCharPaths = [];
          hairTypes.forEach(function(h) {
            farmCharPaths.push(FARM_SW + 'Characters/Human/IDLE/' + h + '_idle_strip9.png');
            farmCharPaths.push(FARM_SW + 'Characters/Human/WALKING/' + h + '_walk_strip8.png');
          });
          var loadFarm = function(path) {
            return loadTex('/static/farm/' + encodeURIComponent(path)).then(function(t) { self.textures[path] = t; }).catch(function() {});
          };
          return Promise.all(farmTwfPaths.map(loadFarm).concat(farmElPaths.map(loadFarm)).concat(farmCharPaths.map(loadFarm))).then(function() {
            console.log('[CEO:TinyFarm] Farm pack textures loaded (' + Object.keys(self.textures).length + ')');
          });
        },

        _regionSprite: function(texKey, px, py, pw, ph, scale) {
          var tex = this.textures[texKey];
          if (!tex) return null;
          var t = tex.clone();
          t.repeat.set(pw / tex.image.width, ph / tex.image.height);
          t.offset.set(px / tex.image.width, 1 - (py + ph) / tex.image.height);
          t.magFilter = THREE.NearestFilter;
          t.minFilter = THREE.NearestFilter;
          t.needsUpdate = true;
          var mat = new THREE.SpriteMaterial({ map: t, transparent: true, depthTest: false });
          var s = new THREE.Sprite(mat);
          var sc = scale || 1;
          s.scale.set(sc * pw / PIXELS_PER_UNIT, sc * ph / PIXELS_PER_UNIT, 1);
          return s;
        },

        _addObj: function(s, x, y, z) {
          if (!s) return;
          s.position.set(x, y, z);
          (this.objectsGroup || scene).add(s);
          this.objects.push(s);
        },

        _tfTex: function(key) { return this.textures[key]; },
        farmTwfBase: 'Tiny Wonder Farm Free 3/',
        farmSwBase: 'Sunnyside_World_ASSET_PACK_V2.1 2/Sunnyside_World_Assets/',

        initScene: function() {
          var self = this;
          self.objects = []; self.plants = []; self.plantTimer = 0;
          self.animals = []; self.crops = []; self.enemies = [];
          self.fireSprites = []; self.windmillSprite = null;
          self.windmillAnimState = null;
          self.phaseTimer = 0; self.combatPhase = false;
          self.propPositions = [];

          function addProp(x, z) { self.propPositions.push({ x: x, z: z }); }

          var PIX = 32;
          var FARM_TWF = 'Tiny Wonder Farm Free 3/';
          var FARM_SW = 'Sunnyside_World_ASSET_PACK_V2.1 2/Sunnyside_World_Assets/';
          var fo = FARM_TWF + 'objects&items/farm objects free.png';
          var springTile = FARM_TWF + 'tilemaps/spring farm tilemap.png';
          var bridgesKey = FARM_TWF + 'tilemaps/farm bridges free.png';
          var insideKey = FARM_TWF + 'tilemaps/farm inside free.png';

          // 25.1 WorldData grid (single source of truth)
          var worldW = 28, worldD = 16;
          var cellsX = 28, cellsZ = 16;
          var worldData = {};
          function getTile(tx, tz) {
            var k = tx + ',' + tz;
            if (!worldData[k]) worldData[k] = { type: 'grass', object: null, variant: 0 };
            return worldData[k];
          }
          function setTile(tx, tz, type, object, variant) {
            var k = tx + ',' + tz;
            worldData[k] = { type: type, object: object || null, variant: variant || 0 };
          }
          for (var tz = 0; tz < cellsZ; tz++) {
            for (var tx = 0; tx < cellsX; tx++) { setTile(tx, tz, 'grass'); }
          }
          // 25.3 Four 5x5 plots with 1-tile path between (plots in lower-center)
          var plots = [[2,5,6,9],[10,5,14,9],[2,11,6,15],[10,11,14,15]];
          plots.forEach(function(p) {
            for (var tz = p[1]; tz <= p[3]; tz++) {
              for (var tx = p[0]; tx <= p[2]; tx++) { setTile(tx, tz, 'tilled'); }
            }
          });
          // 25.4 Paths: horizontal bottom (tz=4), between rows (tz=10), vertical between (tx=7,8)
          for (var tx = 0; tx < cellsX; tx++) { setTile(tx, 4, 'path'); setTile(tx, 10, 'path'); }
          for (var tz = 0; tz < cellsZ; tz++) { setTile(7, tz, 'path'); setTile(8, tz, 'path'); }
          // Water feature for ducks: 4x3 pond (right side)
          for (var tz = 2; tz <= 4; tz++) { for (var tx = 15; tx <= 18; tx++) { setTile(tx, tz, 'water'); } }
          self.worldData = worldData; self.cellsX = cellsX; self.cellsZ = cellsZ;

          // 25.2 Ground from atlas (manifest: grass 1,0; path 1,1; tilled 2,3; water 0,3)
          var tilemapCols = 9, tilemapRows = 20;
          var manifest = { grass: [1,0], path: [1,1], tilled: [2,3], water: [0,3], dirt: [1,0] };
          var objectsGroup = new THREE.Group();
          scene.add(objectsGroup);
          self.objectsGroup = objectsGroup;
          var bgTex = self.textures[springTile];
          if (bgTex && bgTex.image) {
            bgTex.wrapS = bgTex.wrapT = THREE.ClampToEdgeWrapping;
            bgTex.magFilter = THREE.NearestFilter;
            bgTex.minFilter = THREE.NearestFilter;
            var positions = []; var uvs = []; var indices = [];
            for (var tz = 0; tz < cellsZ; tz++) {
              for (var tx = 0; tx < cellsX; tx++) {
                var t = getTile(tx, tz);
                var m = manifest[t.type] || manifest.grass;
                var tileCol = Math.max(0, Math.min(tilemapCols - 1, m[0]));
                var tileRow = Math.max(0, Math.min(tilemapRows - 1, m[1]));
                var u0 = tileCol / tilemapCols, u1 = (tileCol + 1) / tilemapCols;
                var v0 = 1 - (tileRow + 1) / tilemapRows, v1 = 1 - tileRow / tilemapRows;
                var x0 = -worldW/2 + tx, x1 = x0 + 1;
                var z0 = -worldD/2 + tz, z1 = z0 + 1;
                var base = (tz * cellsX + tx) * 4;
                positions.push(x0, 0, z0,  x1, 0, z0,  x1, 0, z1,  x0, 0, z1);
                uvs.push(u0, v0,  u1, v0,  u1, v1,  u0, v1);
                indices.push(base, base+1, base+2,  base, base+2, base+3);
              }
            }
            var floorGeo = new THREE.BufferGeometry();
            floorGeo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
            floorGeo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
            floorGeo.setIndex(indices);
            floorGeo.rotateX(-Math.PI / 2);
            var floor = new THREE.Mesh(floorGeo, new THREE.MeshBasicMaterial({ map: bgTex, side: THREE.DoubleSide }));
            floor.position.y = -0.01;
            objectsGroup.add(floor); self.objects.push(floor);
          } else {
            var floorGeo = new THREE.PlaneGeometry(worldW, worldD);
            var floor = new THREE.Mesh(floorGeo, new THREE.MeshBasicMaterial({ color: 0x7cb342, side: THREE.DoubleSide }));
            floor.rotation.x = -Math.PI / 2; floor.position.y = -0.01;
            objectsGroup.add(floor); self.objects.push(floor);
          }

          // 25.6 House: base on tile center (center-top / right), Y = scale/2 + 0.5
          var houseTx = 12, houseTz = 1;
          var houseWx = -worldW/2 + houseTx + 0.5, houseWz = -worldD/2 + houseTz + 0.5;
          var house = self._regionSprite(fo, 0, 96, 128, 95, 1);
          if (house) {
            house.renderOrder = 100;
            var houseY = house.scale.y / 2 + 0.5;
            house.position.set(houseWx, houseY, houseWz);
            objectsGroup.add(house); self.objects.push(house);
            addProp(houseWx, houseWz);
          }
          var door = self._regionSprite(fo, 128, 128, 16, 48, 1.2);
          if (door) {
            door.renderOrder = 101;
            door.position.set(houseWx, door.scale.y / 2 + 0.5, houseWz + 0.8);
            objectsGroup.add(door); self.objects.push(door);
            addProp(houseWx, houseWz + 0.8);
          }

          // Interior/mill (farm inside free 96x128)
          var insideTex = self.textures[insideKey];
          if (insideTex) {
            var mill = self._regionSprite(insideKey, 0, 0, 96, 128, 1.0);
            if (mill) { mill.position.set(houseWx - 1.2, 1.2, houseWz); objectsGroup.add(mill); self.objects.push(mill); }
          }

          // Furniture (5x3 grid)
          var furnTex = self.textures[FARM_TWF + 'objects&items/furniture free.png'];
          if (furnTex) {
            var furn = [
              [0,0,houseWx-1.2,0.4,houseWz-1.5],[1,0,houseWx-2.2,0.4,houseWz-2],[2,0,houseWx-0.6,0.4,houseWz-1.4],[3,0,houseWx-2.6,0.4,houseWz-2.4],[4,0,houseWx-1.2,0.4,houseWz-1.6],
              [0,1,houseWx-3,0.4,houseWz-2.2],[1,1,houseWx-1.6,0.4,houseWz-1.2],[2,1,houseWx-0.4,0.4,houseWz-2.6],[3,1,houseWx+1,0.4,houseWz-1.4],[4,1,houseWx-2.8,0.4,houseWz-1.8],
              [0,2,houseWx-3.2,0.4,houseWz-2.5],[1,2,houseWx-1.8,0.4,houseWz-2.8],[2,2,houseWx-2,0.4,houseWz-3],[3,2,houseWx-0.4,0.4,houseWz-2.2],[4,2,houseWx-1.4,0.4,houseWz-2.8]
            ];
            furn.forEach(function(f) {
              var sp = makeSprite(gridFrame(furnTex, f[0], f[1], 5, 3), 0.7, 0.7);
              sp.position.set(f[2], f[3], f[4]);
              objectsGroup.add(sp); self.objects.push(sp);
            });
          }

          // 25.5 Fences: neighbor-aware (N,S,E,W) → vertical / horizontal / corners
          function hasFence(tx, tz) { var t = worldData[tx + ',' + tz]; return t && t.object === 'fence'; }
          function fenceVariant(tx, tz) {
            var n = hasFence(tx, tz - 1), s = hasFence(tx, tz + 1), e = hasFence(tx + 1, tz), w = hasFence(tx - 1, tz);
            if (n && s && !e && !w) return 'v';
            if (e && w && !n && !s) return 'h';
            if (n && e) return 'ne'; if (n && w) return 'nw'; if (s && e) return 'se'; if (s && w) return 'sw';
            if (n || s) return 'v'; if (e || w) return 'h';
            return 'v';
          }
          var fenceTiles = [];
          plots.forEach(function(p) {
            for (var tx = p[0]; tx <= p[2]; tx++) { fenceTiles.push([tx, p[1]]); fenceTiles.push([tx, p[3]]); }
            for (var tz = p[1]; tz <= p[3]; tz++) { fenceTiles.push([p[0], tz]); fenceTiles.push([p[2], tz]); }
          });
          var seen = {};
          fenceTiles = fenceTiles.filter(function(pt) { var k = pt[0]+','+pt[1]; if (seen[k]) return false; seen[k]=1; return true; });
          fenceTiles.forEach(function(pt) {
            var tx = pt[0], tz = pt[1];
            setTile(tx, tz, getTile(tx, tz).type, 'fence', 0);
          });
          fenceTiles.forEach(function(pt) {
            var tx = pt[0], tz = pt[1];
            var v = fenceVariant(tx, tz);
            var wx = -worldW/2 + tx + 0.5, wz = -worldD/2 + tz + 0.5;
            var seg = (v === 'h') ? self._regionSprite(fo, 0, 16, 48, 16, 1.2) : self._regionSprite(fo, 0, 0, 16, 48, 1.2);
            if (seg) {
              seg.position.set(wx, (v === 'h') ? 0.35 : 0.6, wz);
              objectsGroup.add(seg); self.objects.push(seg);
              addProp(wx, wz);
            }
          });

          // 25.7 Crops: 4x4 grid inside each plot (organized rows)
          var plantsTex = self.textures[FARM_TWF + 'objects&items/plants free.png'];
          if (plantsTex) {
            plots.forEach(function(p) {
              var ptx0 = p[0], ptz0 = p[1], ptx1 = p[2], ptz1 = p[3];
              var innerW = (ptx1 - ptx0 - 1), innerD = (ptz1 - ptz0 - 1);
              if (innerW < 2 || innerD < 2) return;
              for (var row = 0; row < 4 && row < innerD; row++) {
                for (var col = 0; col < 4 && col < innerW; col++) {
                  var cx = -worldW/2 + ptx0 + 1 + col + 0.5, cz = -worldD/2 + ptz0 + 1 + row + 0.5;
                  var ps = makeSprite(gridFrame(plantsTex, col % 5, row % 6, 5, 6), 0.7, 0.7);
                  ps.position.set(cx, 0.3, cz);
                  objectsGroup.add(ps); self.objects.push(ps);
                  self.plants.push({ sprite: ps, col: col % 5, row: row % 6, timer: Math.random() * 20 });
                  addProp(cx, cz);
                }
              }
            });
          }

          // Farm items (baskets, tools) at field edges and by path
          var itemsTex = self.textures[FARM_TWF + 'objects&items/items free.png'];
          if (itemsTex) {
            var pathY = -worldD/2 + 4.5, pathY2 = -worldD/2 + 10.5;
            [[0,0,-5.4,0.3,-1.6],[1,0,-4.2,0.3,-1.5],[2,0,-3,0.3,-1.6],[3,0,3.2,0.3,-1.5],[4,0,4,0.3,-1.6],
             [0,1,-5,0.3,-4.6],[1,1,2.8,0.3,-4.6],[2,1,-3.8,0.3,-4.4],[3,1,-2,0.3,-1.5],[4,1,-2.4,0.3,-1.6],
             [0,2,-5.4,0.3,-3],[1,2,3.4,0.3,-3],[2,2,4.4,0.3,-3.2],[3,2,-1.4,0.3,-4.6],[4,2,1.4,0.3,-4.4]].forEach(function(it) {
              var sp = makeSprite(gridFrame(itemsTex, it[0], it[1], 5, 3), 0.6, 0.6);
              sp.position.set(it[2], it[3], it[4]);
              objectsGroup.add(sp); self.objects.push(sp);
              addProp(it[2], it[4]);
            });
          }

          // Campfire by path
          var fireTex = self._tfTex(FARM_SW + 'Elements/VFX/Fire/spr_deco_fire_01_strip4.png');
          if (fireTex) {
            var fs = makeSprite(stripFrame(fireTex, 0, 4), 0.6, 0.6);
            scaleSpriteFromStrip(fs, fireTex, 4);
            fs.position.set(3.2, (fs.scale && fs.scale.y) ? fs.scale.y / 2 : 0.3, -0.2);
            objectsGroup.add(fs); self.objects.push(fs);
            self.fireSprites.push({ sprite: fs, animState: new SpriteAnimState(4, 8) });
          }

          // 25.6 Windmill: base on tile, Y = scale/2 + 0.5
          var wmTx = 21, wmTz = 2;
          var wmWx = -worldW/2 + wmTx + 0.5, wmWz = -worldD/2 + wmTz + 0.5;
          var baseSpr = self._regionSprite(fo, 64, 64, 64, 32, 2);
          if (baseSpr) {
            baseSpr.position.set(wmWx, baseSpr.scale.y / 2 + 0.5, wmWz);
            objectsGroup.add(baseSpr); self.objects.push(baseSpr);
          } else {
            var baseM = new THREE.Mesh(new THREE.PlaneGeometry(2.2, 1.8), new THREE.MeshBasicMaterial({ color: 0x6d4c2a, side: THREE.DoubleSide }));
            baseM.rotation.x = -Math.PI / 2; baseM.position.set(wmWx, 0.4, wmWz);
            objectsGroup.add(baseM); self.objects.push(baseM);
          }
          var wmTex = self._tfTex(FARM_SW + 'Elements/Other/spr_deco_windmill_strip9.png');
          if (wmTex) {
            self.windmillSprite = makeSprite(stripFrame(wmTex, 0, 9), 2.5, 2.5);
            scaleSpriteFromStrip(self.windmillSprite, wmTex, 9);
            self.windmillSprite.position.set(wmWx, 2, wmWz);
            objectsGroup.add(self.windmillSprite); self.objects.push(self.windmillSprite);
            self.windmillAnimState = new SpriteAnimState(9, 5);
          }
          addProp(wmWx, wmWz);

          // Storage crates by windmill
          if (itemsTex) {
            [[2,2,wmWx-1.2,0.3,wmWz+0.6],[3,2,wmWx-0.2,0.3,wmWz+0.8],[4,2,wmWx-0.8,0.3,wmWz+0.4]].forEach(function(it) {
              var sp = makeSprite(gridFrame(itemsTex, it[0], it[1], 5, 3), 0.6, 0.6);
              sp.position.set(it[2], it[3], it[4]);
              objectsGroup.add(sp); self.objects.push(sp);
              addProp(it[2], it[4]);
            });
          }

          // Water feature: pond for ducks (worldData pond at tx 15-18, tz 2-4 → center 16.5, 3)
          var pondWx = -worldW/2 + 16.5, pondWz = -worldD/2 + 3;
          var pondPlane = new THREE.Mesh(new THREE.PlaneGeometry(4, 3), new THREE.MeshBasicMaterial({ color: 0x4488cc, transparent: true, opacity: 0.9, side: THREE.DoubleSide }));
          pondPlane.rotation.x = -Math.PI / 2;
          pondPlane.position.set(pondWx, 0.02, pondWz);
          objectsGroup.add(pondPlane); self.objects.push(pondPlane);

          // Bridge over pond
          var bridgeTex = self.textures[bridgesKey];
          if (bridgeTex && bridgeTex.image) {
            var bw = bridgeTex.image.width, bh = bridgeTex.image.height;
            var bt = bridgeTex.clone();
            bt.repeat.set(80/bw, 48/bh);
            bt.offset.set(0, 1 - (16+48)/bh);
            bt.magFilter = bt.minFilter = THREE.NearestFilter;
            bt.needsUpdate = true;
            var bs = makeSprite(bt, 2, 1.2);
            bs.position.set(pondWx, 0.05, pondWz);
            objectsGroup.add(bs); self.objects.push(bs);
          }

          // Animals (ducks near pond at pondWx, pondWz)
          self._spawnAnimals();

          // 25.8 Trees & decor from farm objects free.png
          var edgeTrees = [[-11,-6],[-9,-7],[-12,-2],[-11,2],[-12,6],[-10,7],[9,-7],[11,-5],[12,-1],[11,2],[12,5],[10,7],[-7,-7.5],[-4,-7.5],[3,-7.5],[6,-7.5],[-6,7],[-2,7],[3,7],[7,7]];
          edgeTrees.forEach(function(p, i) {
            var srcX = (i % 3 === 2) ? 96 : 48;
            var tree = self._regionSprite(fo, srcX, 0, 48, 64);
            if (tree) {
              tree.position.set(p[0], 1, p[1]);
              objectsGroup.add(tree); self.objects.push(tree);
              addProp(p[0], p[1]);
            }
          });

          var tree1 = self._tfTex(FARM_SW + 'Elements/Plants/spr_deco_tree_01_strip4.png');
          var tree2 = self._tfTex(FARM_SW + 'Elements/Plants/spr_deco_tree_02_strip4.png');
          if (tree1) {
            [[-8,-6],[8,-6],[-9,4],[9,5]].forEach(function(p) {
              var s = makeSprite(stripFrame(tree1, 0, 4), 1, 1);
              scaleSpriteFromStrip(s, tree1, 4);
              s.position.set(p[0], 0.5, p[1]);
              objectsGroup.add(s); self.objects.push(s);
            });
          }
          if (tree2) {
            [[-6,-7],[7,-7],[8,6]].forEach(function(p) {
              var s = makeSprite(stripFrame(tree2, 0, 4), 1, 1);
              scaleSpriteFromStrip(s, tree2, 4);
              s.position.set(p[0], 0.5, p[1]);
              objectsGroup.add(s); self.objects.push(s);
            });
          }

          var mushR = self._tfTex(FARM_SW + 'Elements/Plants/spr_deco_mushroom_red_01_strip4.png');
          var mushB = self._tfTex(FARM_SW + 'Elements/Plants/spr_deco_mushroom_blue_01_strip4.png');
          if (mushR) {
            [[-10,-5],[11,-4],[-11,2],[9,3]].forEach(function(p) {
              var s = makeSprite(stripFrame(mushR, 0, 4), 0.5, 0.5);
              scaleSpriteFromStrip(s, mushR, 4);
              s.position.set(p[0], (s.scale && s.scale.y) ? s.scale.y/2 : 0.25, p[1]);
              objectsGroup.add(s); self.objects.push(s);
            });
          }
          if (mushB) {
            [[10,-6],[-12,4],[12,5],[-8,-4]].forEach(function(p) {
              var s = makeSprite(stripFrame(mushB, 0, 4), 0.5, 0.5);
              scaleSpriteFromStrip(s, mushB, 4);
              s.position.set(p[0], (s.scale && s.scale.y) ? s.scale.y/2 : 0.25, p[1]);
              objectsGroup.add(s); self.objects.push(s);
            });
          }

          var glintTex = self._tfTex(FARM_SW + 'Elements/VFX/Glint/spr_deco_glint_01_strip6.png');
          if (glintTex) {
            [[6.8,1,-5.4],[3.5,0.5,-0.1]].forEach(function(gp) {
              var gs = makeSprite(stripFrame(glintTex, 0, 6), 0.4, 0.4);
              scaleSpriteFromStrip(gs, glintTex, 6);
              gs.position.set(gp[0], gp[1], gp[2]);
              objectsGroup.add(gs); self.objects.push(gs);
              self.fireSprites.push({ sprite: gs, animState: new SpriteAnimState(6, 4), tex: glintTex, isGlint: true });
            });
          }

          console.log('[CEO:TinyFarm] Scene built (Section 25: WorldData, 4 plots, paths, pond, fences)');
        },

        _spawnAnimals: function() {
          var self = this;
          var animalDefs = [
            { key: self.farmSwBase + 'Elements/Animals/spr_deco_chicken_01_strip4.png', type: 'chicken', count: 3, xBase: -4, zBase: 1.5, xRange: 3, zRange: 1.5 },
            { key: self.farmSwBase + 'Elements/Animals/spr_deco_pig_01_strip4.png', type: 'pig', count: 2, xBase: -6, zBase: 2, xRange: 2, zRange: 1.5 },
            { key: self.farmSwBase + 'Elements/Animals/spr_deco_cow_strip4.png', type: 'cow', count: 2, xBase: -1, zBase: 3.5, xRange: 4, zRange: 1.5 },
            { key: self.farmSwBase + 'Elements/Animals/spr_deco_sheep_01_strip4.png', type: 'sheep', count: 2, xBase: 1, zBase: 3.5, xRange: 3, zRange: 1.5 },
            { key: self.farmSwBase + 'Elements/Animals/spr_deco_duck_01_strip4.png', type: 'duck', count: 3, xBase: 1.5, zBase: -5, xRange: 2.5, zRange: 2 },
            { key: self.farmSwBase + 'Elements/Animals/spr_deco_bird_01_strip4.png', type: 'bird', count: 2, xBase: -8, zBase: -2, xRange: 16, zRange: 1 }
          ];
          animalDefs.forEach(function(ad) {
            var tex = self._tfTex(ad.key);
            if (!tex) return;
            for (var i = 0; i < ad.count; i++) {
              var af = stripFrame(tex, 0, 4);
              var as = makeSprite(af, 1.0, 1.0);
              scaleSpriteFromStrip(as, tex, 4);
              var ax = ad.xBase + Math.random() * ad.xRange;
              var az = ad.zBase + Math.random() * ad.zRange;
              as.position.set(ax, as.scale.y / 2, az);
              scene.add(as); self.objects.push(as);
              self.animals.push({
                sprite: as, type: ad.type, x: ax, z: az,
                vx: (Math.random() - 0.5) * 0.3,
                tex: tex, animState: new SpriteAnimState(4, 5),
                xMin: ad.xBase, xMax: ad.xBase + ad.xRange,
                zMin: ad.zBase, zMax: ad.zBase + ad.zRange
              });
            }
          });
        },

        updateAgents: function(agents, posMap, delta) {
          var self = this;
          var hairTypes = ['base', 'bowlhair', 'curlyhair', 'longhair', 'mophair', 'shorthair', 'spikeyhair'];
          var charDefs = hairTypes.map(function(h) {
            return {
              idle: self.farmSwBase + 'Characters/Human/IDLE/' + h + '_idle_strip9.png',
              walk: self.farmSwBase + 'Characters/Human/WALKING/' + h + '_walk_strip8.png',
              idleFrames: 9,
              walkFrames: 8
            };
          });
          var d = delta || 0.016;
          var BASE_SCALE = 1.2;
          for (var i = 0; i < agents.length && i < CAP_AGENTS; i++) {
            var a = agents[i];
            var pos = posMap[a.name];
            if (!pos) continue;
            var charIdx = i % charDefs.length;
            var def = charDefs[charIdx];
            var idleTex = self.textures[def.idle];
            var walkTex = self.textures[def.walk];
            if (!idleTex && !walkTex) continue;
            var tex = walkTex || idleTex;
            var frames = def.walkFrames;
            if (!agentAnimStates[a.name]) {
              agentAnimStates[a.name] = {
                face: 1,
                charIdx: charIdx,
                idleAnim: new SpriteAnimState(def.idleFrames, 6),
                walkAnim: new SpriteAnimState(def.walkFrames, 10)
              };
            }
            var st = agentAnimStates[a.name];
            st.charIdx = charIdx;
            var curX = agentSprites[a.name] ? agentSprites[a.name].position.x : pos.x;
            var targetX = pos.x;
            var dx = targetX - curX;
            var moving = Math.abs(dx) > 0.02;
            var facingRight = (dx > 0) ? true : (dx < 0) ? false : (st.face === 1);
            if (dx > 0) st.face = 1; else if (dx < 0) st.face = 2;
            var animTex = moving ? (walkTex || idleTex) : (idleTex || walkTex);
            var animState = moving ? st.walkAnim : st.idleAnim;
            var animFrames = moving ? def.walkFrames : def.idleFrames;
            if (!animTex) animTex = tex;
            if (!agentSprites[a.name]) {
              var frameTex = stripFrame(animTex, 0, animFrames);
              var s = makeSprite(frameTex, BASE_SCALE, BASE_SCALE);
              scaleSpriteFromStrip(s, animTex, animFrames);
              s.position.set(pos.x, pos.y + 0.6, pos.z);
              scene.add(s); agentSprites[a.name] = s;
            } else {
              animateStrip(agentSprites[a.name], animTex, animState, d);
              agentSprites[a.name].position.x += dx * 0.08;
              agentSprites[a.name].position.y = pos.y + 0.6;
              agentSprites[a.name].position.z = pos.z;
              var scy = agentSprites[a.name].scale.y;
              agentSprites[a.name].scale.x = facingRight ? Math.abs(agentSprites[a.name].scale.x) : -Math.abs(agentSprites[a.name].scale.x);
              agentSprites[a.name].scale.y = scy;
            }

            var portraitKeys = [self.farmTwfBase + 'characters/main character/portrait male.png', self.farmTwfBase + 'characters/main character/portrait female.png', self.farmTwfBase + 'characters/main character old/portrait male.png', self.farmTwfBase + 'characters/main character old/portrait female.png'];
            var portraitKey = portraitKeys[i % 4];
            if (!agentSprites[a.name + '_portrait']) {
              var pTex = self.textures[portraitKey];
              if (pTex) {
                var ps = makeSprite(pTex, 0.5, 0.5);
                scene.add(ps); agentSprites[a.name + '_portrait'] = ps;
              }
            }
            if (agentSprites[a.name + '_portrait'] && agentSprites[a.name]) {
              agentSprites[a.name + '_portrait'].position.set(agentSprites[a.name].position.x, agentSprites[a.name].position.y + 0.7, agentSprites[a.name].position.z);
            }
          }
        },

        tickAmbient: function(delta) {
          var self = this;
          var plantsTex = self.textures[self.farmTwfBase + 'objects&items/plants free.png'];
          if (plantsTex) {
            self.plants.forEach(function(pl) {
              pl.timer += delta;
              if (pl.timer > 18) {
                pl.timer = 0;
                pl.row = (pl.row + 1) % 6;
                pl.sprite.material.map = gridFrame(plantsTex, pl.col, pl.row, 5, 6);
                pl.sprite.material.map.needsUpdate = true;
              }
            });
          }
          self.animals.forEach(function(an) {
            an.x += an.vx * delta;
            if (an.x < an.xMin || an.x > an.xMax) an.vx = -an.vx;
            an.sprite.position.x = an.x;
            if (an.vx < 0) an.sprite.scale.x = -Math.abs(an.sprite.scale.x);
            else an.sprite.scale.x = Math.abs(an.sprite.scale.x);
            animateStrip(an.sprite, an.tex, an.animState, delta);
          });
          if (self.windmillSprite && self.windmillAnimState) {
            var wmTex = self._tfTex(self.farmSwBase + 'Elements/Other/spr_deco_windmill_strip9.png');
            if (wmTex) animateStrip(self.windmillSprite, wmTex, self.windmillAnimState, delta);
          }
          self.fireSprites.forEach(function(f) {
            if (f.isGlint) {
              if (f.tex) animateStrip(f.sprite, f.tex, f.animState, delta);
            } else {
              var ft = self._tfTex(self.farmSwBase + 'Elements/VFX/Fire/spr_deco_fire_01_strip4.png');
              if (ft) animateStrip(f.sprite, ft, f.animState, delta);
            }
          });
          // 25.9 Depth sort every frame (painter's algorithm)
          if (self.objectsGroup && self.objectsGroup.children.length) {
            self.objectsGroup.children.sort(function(a, b) {
              var ay = a.position ? (a.position.y + (a.position.z || 0) * 0.01) : 0;
              var by = b.position ? (b.position.y + (b.position.z || 0) * 0.01) : 0;
              return by - ay;
            });
          }
        },

        dispose: function() {
          var self = this;
          self.objects.forEach(function(o) { disposeObject(o); });
          self.objects = []; self.plants = []; self.animals = []; self.crops = []; self.enemies = [];
          self.propPositions = []; self.windmillSprite = null; self.fireSprites = []; self.windmillAnimState = null;
          if (self.objectsGroup && self.objectsGroup.parent) self.objectsGroup.parent.remove(self.objectsGroup);
          self.objectsGroup = null; self.worldData = null;
        }
      };

      var CEO_THEMES = { zombie: zombieTheme, tinyfarm: tinyfarmTheme };

      // =============== SHARED ENGINE ===============
      function getThemeName() {
        try { return localStorage.getItem('ceoTheme') || 'tinyfarm'; } catch(e) { return 'tinyfarm'; }
      }
      function setThemeName(t) {
        try { localStorage.setItem('ceoTheme', t); } catch(e) {}
      }

      function clearAgentSprites() {
        for (var k in agentSprites) { disposeObject(agentSprites[k]); } agentSprites = {};
        for (var k2 in agentBubbles) { disposeObject(agentBubbles[k2]); } agentBubbles = {};
        for (var k3 in agentBadges) { disposeObject(agentBadges[k3].sprite); } agentBadges = {};
        mailFlights.forEach(function(f) { if (f.mesh) disposeObject(f.mesh); });
        mailFlights = []; sendHomeQueue = []; sendHomeState = null;
        agentAnimStates = {};
      }

      // --- Agent Dialog System ---
      var raycaster = new THREE.Raycaster();

      function renderPortrait(agentName) {
        var portraitDiv = document.getElementById('agentDialogPortrait');
        if (!portraitDiv) return;
        portraitDiv.innerHTML = '';
        var sprite = agentSprites[agentName];
        if (!sprite || !sprite.material || !sprite.material.map) return;
        var tex = sprite.material.map;
        var img = tex.image;
        if (!img) return;
        var canvas = document.createElement('canvas');
        canvas.width = 80; canvas.height = 80;
        var ctx = canvas.getContext('2d');
        ctx.imageSmoothingEnabled = false;
        var ox = tex.offset.x * img.width;
        var oy = (1 - tex.offset.y - tex.repeat.y) * img.height;
        var sw = tex.repeat.x * img.width;
        var sh = tex.repeat.y * img.height;
        ctx.drawImage(img, ox, oy, sw, sh, 0, 0, 80, 80);
        portraitDiv.appendChild(canvas);
      }

      function streamText(text, element, speed) {
        var i = 0;
        element.textContent = '';
        if (dialogStreamTimer) { clearInterval(dialogStreamTimer); dialogStreamTimer = null; }
        dialogStreamTimer = setInterval(function() {
          if (i < text.length) {
            element.textContent += text[i];
            i++;
          } else {
            clearInterval(dialogStreamTimer);
            dialogStreamTimer = null;
          }
        }, speed || 30);
      }

      function openAgentDialog(agentName) {
        if (!agentDialogEl) return;
        dialogAgent = agentName;
        var agent = null;
        for (var i = 0; i < lastPolledAgents.length; i++) {
          if (lastPolledAgents[i].name === agentName) { agent = lastPolledAgents[i]; break; }
        }
        var role = agent ? getAgentRole(agent) : 'default';
        var roleBadge = { lead: 'Lead', builder: 'Builder', scout: 'Scout', reviewer: 'Reviewer', 'default': 'Agent' };
        var nameEl = document.getElementById('agentDialogName');
        var textEl = document.getElementById('agentDialogText');
        var displayName = (agentName || '').replace(/[-0-9a-f]{8,}$/i, '').trim() || agentName;
        if (nameEl) nameEl.innerHTML = displayName + ' <span class="agent-dialog-role">' + (roleBadge[role] || 'Agent') + '</span>';
        renderPortrait(agentName);
        var otherNames = lastPolledAgents.map(function(a) { return a.name; });
        var mailForAgent = [];
        var speechLines = agent ? speechLinesForAgent(agent, inboxCounts[agentName] || 0, mailForAgent, otherNames.filter(function(n) { return n !== agentName; })) : ['...'];
        var taskFull = (agent && agent.task_full) ? String(agent.task_full).trim() : '';
        var fullText = speechLines.join('\\n');
        if (taskFull && fullText.indexOf(taskFull) === -1) {
          var shortTask = taskFull.length > 120 ? taskFull.slice(0, 117) + '...' : taskFull;
          fullText += '\\n\\n' + shortTask;
        }
        if (textEl) streamText(fullText, textEl, 25);
        agentDialogEl.style.display = 'flex';
      }

      function closeAgentDialog() {
        if (!agentDialogEl) return;
        agentDialogEl.style.display = 'none';
        dialogAgent = null;
        if (dialogStreamTimer) { clearInterval(dialogStreamTimer); dialogStreamTimer = null; }
      }

      if (agentDialogEl) {
        agentDialogEl.addEventListener('click', function(e) { e.stopPropagation(); closeAgentDialog(); });
      }

      ceoCanvas.addEventListener('click', function(event) {
        if (!ceoOpen || !camera) return;
        if (dialogAgent) { closeAgentDialog(); return; }
        var rect = ceoCanvas.getBoundingClientRect();
        var mouse = new THREE.Vector2(
          ((event.clientX - rect.left) / rect.width) * 2 - 1,
          -((event.clientY - rect.top) / rect.height) * 2 + 1
        );
        raycaster.setFromCamera(mouse, camera);
        var targets = [];
        for (var k in agentSprites) {
          if (agentSprites[k] && agentSprites[k].position) targets.push(agentSprites[k]);
        }
        var hits = raycaster.intersectObjects(targets);
        if (hits.length > 0) {
          var hitSprite = hits[0].object;
          for (var name in agentSprites) {
            if (agentSprites[name] === hitSprite) { openAgentDialog(name); break; }
          }
        }
      });

      document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && dialogAgent) { closeAgentDialog(); e.stopPropagation(); }
      });

      function initRenderer() {
        if (renderer) return;
        var w = ceoCanvas.clientWidth || 800, h = ceoCanvas.clientHeight || 600;
        var aspect = w / h;
        var halfH = FRUSTUM_H / 2;
        camera = new THREE.OrthographicCamera(-halfH * aspect, halfH * aspect, halfH, -halfH, 0.1, 100);
        camera.position.set(0, 10, 12);
        camera.lookAt(0, 0, 0);
        camera.updateProjectionMatrix();
        renderer = new THREE.WebGLRenderer({ canvas: ceoCanvas, antialias: false });
        renderer.setSize(w, h);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      }

      function switchTheme(name) {
        console.log('[CEO] Switching to', name);
        if (activeTheme) { activeTheme.dispose(); clearAgentSprites(); }
        if (!scene) { scene = new THREE.Scene(); }
        while (scene.children.length) scene.remove(scene.children[0]);
        agentSprites = {}; agentBubbles = {}; agentBadges = {};
        var theme = CEO_THEMES[name] || CEO_THEMES.tinyfarm;
        scene.background = new THREE.Color(theme.bg);
        activeTheme = theme;
        ceoCredit.textContent = theme.credit || '';
        theme.preload().then(function() {
          theme.initScene();
        }).catch(function(e) { console.error('[CEO] Theme preload error:', e); });
      }

      var lastDelta = 0.016;
      function updateAgentsShared(agents, mailItems) {
        if (!scene || !activeTheme) return;
        try {
          var positions = activeTheme.positions || TINYFARM_POSITIONS;
          var posMap = assignPositions(agents, positions);
          activeTheme.updateAgents(agents, posMap, lastDelta);
          var mailList = mailItems || [];
          var otherNames = agents.map(function(a) { return a.name; });
          for (var i = 0; i < agents.length && i < CAP_AGENTS; i++) {
            var name = agents[i].name;
            var sprite = agentSprites[name];
            if (!sprite) continue;
            var sp = sprite.position;
            if (!agentBubbles[name]) {
              var bmat = new THREE.SpriteMaterial({ map: makeBubbleTexture([]), transparent: true, depthTest: false });
              var b = new THREE.Sprite(bmat); b.scale.set(5, 2.0, 1);
              scene.add(b); agentBubbles[name] = b;
              var bc = document.createElement('canvas'); bc.width = 24; bc.height = 24;
              var btex = new THREE.CanvasTexture(bc); btex.minFilter = btex.magFilter = THREE.NearestFilter;
              var badge = new THREE.Sprite(new THREE.SpriteMaterial({ map: btex, transparent: true }));
              badge.scale.set(0.5, 0.5, 1); scene.add(badge);
              agentBadges[name] = { sprite: badge, canvas: bc };
            }
            agentBubbles[name].position.set(sp.x, sp.y + 1.8, sp.z);
            agentBadges[name].sprite.position.set(sp.x + 0.8, sp.y + 0.9, sp.z);
            var mailForAgent = mailList.filter(function(m) { return (m.to || '') === name; });
            var speechLines = speechLinesForAgent(agents[i], inboxCounts[name] || 0, mailForAgent, otherNames.filter(function(n) { return n !== name; }));
            agentBubbles[name].material.map = makeBubbleTexture(speechLines);
            agentBubbles[name].material.map.needsUpdate = true;
            var cnt = inboxCounts[name] || 0;
            if (cnt > 0) {
              var bcc = agentBadges[name].canvas, bctx = bcc.getContext('2d');
              bctx.clearRect(0, 0, 24, 24);
              bctx.fillStyle = '#f85149'; bctx.beginPath(); bctx.arc(12, 12, 11, 0, Math.PI * 2); bctx.fill();
              bctx.fillStyle = '#fff'; bctx.font = 'bold 12px sans-serif'; bctx.textAlign = 'center'; bctx.textBaseline = 'middle';
              bctx.fillText(cnt > 99 ? '99+' : String(cnt), 12, 12);
              agentBadges[name].sprite.material.map.needsUpdate = true;
              agentBadges[name].sprite.visible = true;
            } else { agentBadges[name].sprite.visible = false; }
          }
          // Remove gone agents
          var current = {};
          agents.forEach(function(a) { current[a.name] = true; });
          leadAgentName = null;
          agents.forEach(function(a) { if (isLead(a) && !leadAgentName) leadAgentName = a.name; });
          for (var key in agentSprites) {
            if (!current[key]) {
              disposeObject(agentSprites[key]); delete agentSprites[key];
              if (agentBubbles[key]) { disposeObject(agentBubbles[key]); delete agentBubbles[key]; }
              if (agentBadges[key]) { disposeObject(agentBadges[key].sprite); delete agentBadges[key]; }
              delete agentAnimStates[key];
            }
          }
        } catch (e) { console.error('[CEO] updateAgents error:', e); }
      }

      // Mail flights
      function createMailMesh() {
        var g = new THREE.PlaneGeometry(0.35, 0.25);
        var mc = (activeTheme && activeTheme.mailColor) ? activeTheme.mailColor : 0xffa657;
        var m = new THREE.MeshBasicMaterial({ color: mc, side: THREE.DoubleSide });
        var mesh = new THREE.Mesh(g, m); scene.add(mesh); return mesh;
      }
      function tickMailFlights(delta) {
        for (var i = mailFlights.length - 1; i >= 0; i--) {
          var f = mailFlights[i]; f.t += delta;
          if (f.t >= f.duration) { if (f.mesh) scene.remove(f.mesh); mailFlights.splice(i, 1); continue; }
          var u = Math.min(1, f.t / f.duration); u = u * u * (3 - 2 * u);
          if (!f.mesh) f.mesh = createMailMesh();
          f.mesh.position.x = f.from.x + (f.to.x - f.from.x) * u;
          f.mesh.position.y = f.from.y + (f.to.y - f.from.y) * u + 0.6;
          f.mesh.position.z = f.from.z + (f.to.z - f.from.z) * u;
        }
      }
      function updateMailFlights(agents, items) {
        var positions = activeTheme.positions || TINYFARM_POSITIONS;
        var posMap = assignPositions(agents, positions);
        (items || []).slice(0, 5).forEach(function(item) {
          var fp = posMap[item.from], tp = posMap[item.to];
          if (fp && tp) mailFlights.push({ from: fp, to: tp, t: 0, duration: 1.5, mesh: null });
        });
      }

      // Send them home
      function runSendThemHome(delta) {
        if (!sendHomeState) { if (sendHomeQueue.length) sendHomeState = { worker: sendHomeQueue.shift(), phase: 'lead', t: 0, duration: 0.8 }; return; }
        sendHomeState.t += delta;
        if (sendHomeState.phase === 'lead') {
          var ls = leadAgentName ? agentSprites[leadAgentName] : null;
          if (ls) { var u = Math.min(1, sendHomeState.t / sendHomeState.duration); var baseW = ls._baseScaleX || ls.scale.x; var baseH = ls._baseScaleY || ls.scale.y; ls.scale.set(baseW + Math.sin(u * Math.PI) * 0.3, baseH + Math.sin(u * Math.PI) * 0.3, 1); }
          if (sendHomeState.t >= sendHomeState.duration) {
            if (ls) { ls.scale.set(ls._baseScaleX || ls.scale.x, ls._baseScaleY || ls.scale.y, 1); }
            sendHomeState.phase = 'worker'; sendHomeState.t = 0; sendHomeState.duration = 1.5;
            var ws = agentSprites[sendHomeState.worker];
            if (ws) sendHomeState.startPos = ws.position.clone();
          }
        } else if (sendHomeState.phase === 'worker') {
          var ws2 = agentSprites[sendHomeState.worker];
          if (ws2 && sendHomeState.startPos) {
            var u2 = Math.min(1, sendHomeState.t / sendHomeState.duration);
            var targetX = (activeTheme && activeTheme.homeX !== undefined) ? activeTheme.homeX : 12;
            ws2.position.x = sendHomeState.startPos.x + (targetX - sendHomeState.startPos.x) * u2;
            ws2.material.opacity = 1 - u2;
          }
          if (sendHomeState.t >= sendHomeState.duration) {
            if (ws2) { disposeObject(ws2); delete agentSprites[sendHomeState.worker]; }
            if (agentBubbles[sendHomeState.worker]) { disposeObject(agentBubbles[sendHomeState.worker]); delete agentBubbles[sendHomeState.worker]; }
            if (agentBadges[sendHomeState.worker]) { disposeObject(agentBadges[sendHomeState.worker].sprite); delete agentBadges[sendHomeState.worker]; }
            delete agentAnimStates[sendHomeState.worker];
            sendHomeState = null;
          }
        }
      }

      // Polling
      function poll() {
        if (!ceoOpen) return;
        Promise.all([
          fetch('/api/overstory').then(function(r) { return r.json(); }),
          fetch('/api/mail').then(function(r) { return r.json(); })
        ]).then(function(res) {
          var agents = (res[0] || {}).agents || [];
          var mailItems = (res[1] || {}).mail_items || [];
          lastPolledAgents = agents;
          var cur = {}; agents.forEach(function(a) { cur[a.name] = true; });
          previousAgentList.forEach(function(a) { if (!cur[a.name] && !isLead(a)) sendHomeQueue.push(a.name); });
          previousAgentList = agents.slice();
          inboxCounts = {};
          mailItems.forEach(function(m) { inboxCounts[m.to] = (inboxCounts[m.to] || 0) + 1; });
          if (mailItems.length > previousMailLength) updateMailFlights(agents, mailItems.slice(0, mailItems.length - previousMailLength));
          previousMailLength = mailItems.length;
          var termPs = agents.slice(0, CAP_AGENTS).map(function(a) {
            return fetch('/api/agents/' + encodeURIComponent(a.name) + '/terminal?lines=' + BUBBLE_LINES)
              .then(function(r) { return r.json(); }).then(function(d) { terminalCache[a.name] = d.output || ''; }).catch(function() {});
          });
          Promise.all(termPs).then(function() { updateAgentsShared(agents, mailItems); });
        }).catch(function(e) { console.warn('[CEO] poll error:', e); });
      }

      function resize() {
        if (!renderer || !ceoCanvas) return;
        var w = ceoCanvas.clientWidth || 800, h = ceoCanvas.clientHeight || 600;
        renderer.setSize(w, h);
        var aspect = w / h;
        var halfH = FRUSTUM_H / 2;
        camera.left = -halfH * aspect; camera.right = halfH * aspect;
        camera.top = halfH; camera.bottom = -halfH;
        camera.updateProjectionMatrix();
      }

      var lastTime = 0;
      function animate(time) {
        if (!ceoOpen) return;
        ceoAnimId = requestAnimationFrame(animate);
        var delta = time - lastTime; lastTime = time;
        if (delta > 200) delta = 16; delta /= 1000;
        lastDelta = delta;
        tickMailFlights(delta);
        runSendThemHome(delta);
        if (activeTheme) try { activeTheme.tickAmbient(delta); } catch(e) {}
        try { if (renderer && scene && camera) renderer.render(scene, camera); } catch(e) {}
      }

      function openCEO() {
        console.log('[CEO] Opening');
        ceoOpen = true;
        ceoModal.classList.add('visible');
        ceoModal.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        try {
          initRenderer();
          var tn = getThemeName();
          if (ceoThemeSel) ceoThemeSel.value = tn;
          switchTheme(tn);
          lastTime = performance.now();
          poll();
          ceoPollTimer = setInterval(poll, POLL_MS);
          animate(lastTime);
        } catch(e) { console.error('[CEO] open error:', e); }
      }
      function closeCEO() {
        console.log('[CEO] Closing');
        closeAgentDialog();
        ceoOpen = false;
        if (ceoPollTimer) { clearInterval(ceoPollTimer); ceoPollTimer = null; }
        if (ceoAnimId) { cancelAnimationFrame(ceoAnimId); ceoAnimId = null; }
        ceoModal.classList.remove('visible');
        ceoModal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
      }

      ceoBtn.addEventListener('click', openCEO);
      ceoClose.addEventListener('click', closeCEO);
      window.addEventListener('resize', function() { if (ceoOpen) resize(); });
      if (ceoThemeSel) ceoThemeSel.addEventListener('change', function() {
        var v = ceoThemeSel.value;
        setThemeName(v);
        if (ceoOpen) switchTheme(v);
      });

      console.log('[CEO] Theme engine ready');
      } catch (e) { console.error('[CEO] Setup error:', e); }
    })();
  </script>
</body>
</html>
""".replace("{{ gateway_url }}", json.dumps(GATEWAY_URL))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"OverClaw UI: http://localhost:{port}")
    print(f"  Left: Overstory dashboard (agents, mail, merge, metrics, bun log)")
    print(f"  Right: Terminal/Output tabs (TERMINAL, OUTPUT, PROBLEMS, PORTS, DEBUG CONSOLE)")
    app.run(host="0.0.0.0", port=port, debug=True)
