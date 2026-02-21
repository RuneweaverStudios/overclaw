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
from flask import Flask, Response, jsonify, render_template_string, request

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
    if not name or name == "Ollama supervisor":
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

_DEFAULT_UI_SETTINGS = {
    "default_project_folder": "overstory",  # "overstory" = ROOT_WORKSPACE; or absolute path under root
    "current_project_folder": "",           # "" = use default; or path (must be under ROOT_WORKSPACE)
    "create_project_on_next_prompt": False, # when True, next chat message creates a new folder from prompt
    "refresh_interval_ms": 1000,            # dashboard poll interval (500, 1000, 2000, 5000)
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
    """Current project folder: overstory workspace by default, or selected subfolder (changeable in settings)."""
    s = _load_ui_settings()
    current = (s.get("current_project_folder") or "").strip()
    default = (s.get("default_project_folder") or "overstory").strip()
    if current:
        candidate = _path_under_root(current)
        if candidate is not None:
            return candidate
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

        # Always show Ollama approval supervisor first (runs in gateway, not an overstory tmux agent)
        agents.insert(0, {
            "state_icon": "●",
            "name": "Ollama supervisor",
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
    """Current project folder, git root, GitHub remote URL, branch (uses effective project folder from settings)."""
    project = get_effective_project_folder()
    info = {
        "folder": project.name,
        "path": str(project),
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
                if isinstance(v, int) and v in (500, 1000, 2000, 5000):
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


@app.route("/api/session-usage")
def api_session_usage():
    """Aggregate input/output token usage from OpenClaw sessions.json (all sessions)."""
    out = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "sessions": 0}
    path = _sessions_json_path()
    if not path:
        return jsonify(out)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return jsonify(out)
        for key, session in data.items():
            if not key.startswith("agent:"):
                continue
            out["sessions"] += 1
            out["inputTokens"] += int(session.get("inputTokens") or 0)
            out["outputTokens"] += int(session.get("outputTokens") or 0)
        out["totalTokens"] = out["inputTokens"] + out["outputTokens"]
    except (json.JSONDecodeError, OSError):
        pass
    return jsonify(out)


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
    if not has_history and (s.get("current_project_folder") or "").strip() == "" and (s.get("default_project_folder") or "overstory").strip() == "overstory":
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
        with httpx.Client(timeout=120.0) as client:
            r = client.post(gateway_url, json=payload)
            r.raise_for_status()
            response_data = r.json()
            add_terminal_log(f"Gateway response received", "info")
            if created_path is not None:
                response_data["created_project"] = created_path.name
            return jsonify(response_data)
    except httpx.ConnectError as e:
        add_terminal_log(f"Gateway connection failed: {str(e)}", "error")
        return jsonify({"error": f"Gateway connection failed: {str(e)}", "gateway_url": gateway_url}), 502
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
    if not has_history and (s.get("current_project_folder") or "").strip() == "" and (s.get("default_project_folder") or "overstory").strip() == "overstory":
        s["create_project_on_next_prompt"] = True
        _save_ui_settings(s)
    created_path = _ensure_create_project_from_prompt(message)
    gateway_url = f"{GATEWAY_URL.rstrip('/')}/api/message"
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                gateway_url,
                json={
                    "message": message,
                    "history": history,
                    "route_to_agents": data.get("route_to_agents", False),
                    "follow_up_answers": data.get("follow_up_answers"),
                    "context": data.get("context", {}),
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


@app.route("/api/route", methods=["POST"])
def api_route():
    """Proxy route/spawn to gateway. Gateway expects { task, spawn?, context? }. Creates project from task when no folder selected."""
    data = request.get_json() or {}
    task = data.get("task", "").strip()
    if not task:
        return jsonify({"error": "task is required"}), 400
    s = _load_ui_settings()
    ctx = data.get("context") or {}
    has_history = bool(ctx.get("history"))
    if not has_history and (s.get("current_project_folder") or "").strip() == "" and (s.get("default_project_folder") or "overstory").strip() == "overstory":
        s["create_project_on_next_prompt"] = True
        _save_ui_settings(s)
    created_path = _ensure_create_project_from_prompt(task)
    gateway_url = f"{GATEWAY_URL.rstrip('/')}/api/route"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                gateway_url,
                json={
                    "task": task,
                    "spawn": data.get("spawn", True),
                    "context": data.get("context", {}),
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
      <span class="header-refresh">refresh: <span id="refreshInterval">1000ms</span></span>
    </div>
    <div class="header-controls">
      <button id="restartWithSkipPermsBtn" class="header-btn-long" title="Enable skip-permissions, send Ctrl+C twice to all agent tmux, then start claude --dangerously-skip-permissions in each">Restart agents (skip perms)</button>
      <button id="approveAllAndCleanBtn" class="header-btn-long" title="Process pending approval mail, drain merge queue, then prune completed worktrees (unblocks builders when no lead)">Approve all &amp; clean</button>
      <button id="injectLeadBtn" class="header-btn-long" title="Spawn a lead and reassign all unread lead/supervisor mail to it (use when agents have no lead)">Inject Lead</button>
      <button id="pruneWorktreesBtn" class="header-btn-long" title="Prune completed worktrees (overstory worktree clean --completed)">Prune completed</button>
      <button id="killAllAgentsBtn" class="header-btn-long header-btn-danger" title="Stop all agents, clear all mail and task queue so you can open a new project fresh">Kill all agents</button>
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
              <input type="text" id="customProjectPath" class="project-path-input" placeholder="Path under workspace">
            </div>
            <div id="newProjectFolderWrap" class="project-setting-row" style="display:none;">
              <input type="text" id="newProjectFolderName" class="project-path-input" placeholder="New folder name">
              <button type="button" id="newProjectFolderBtn" class="project-create-folder-btn">Create & set default</button>
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
        <div class="tab active" data-tab="chat">Chat (Ollama)</div>
        <div class="tab" data-tab="terminal">TERMINAL</div>
        <div class="tab" data-tab="output">OUTPUT</div>
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
          <div class="terminal-output" id="chatHistory" class="chat-history-scroll" style="flex: 1; min-height: 0; overflow-y: auto; margin-bottom: 8px;"></div>
          <label class="route-toggle"><input type="checkbox" id="routeToAgents" checked> Route to agents (spawn task; when off, Ollama replies directly)</label>
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
      <div id="tabOutput" class="tab-content">
        <div class="terminal-output" id="outputContent">[Command outputs will appear here]</div>
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
      <div class="footer-tokens" id="footerTokens" title="Sum of input/output tokens across all agents (main + subagents) in sessions.json">
        <span>Tokens (all agents):</span>
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
            <option value="1000" selected>1 s</option>
            <option value="2000">2 s</option>
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
      <div class="settings-actions">
        <button type="button" class="secondary" id="settingsCancelBtn">Cancel</button>
        <button type="button" id="settingsSaveBtn">Save</button>
      </div>
    </div>
  </div>
  <script>
    const GATEWAY_URL = {{ gateway_url|tojson }};
    let refreshIntervalId = null;
    let REFRESH_INTERVAL = 1000; // 1s default; overridden from ui-settings
    let zombiesSlayedTotal = 0;
    const ZOMBIE_CHECK_INTERVAL_MS = 300000; // 5 minutes
    let zombieCountdownSeconds = 300; // 5 minutes
    let zombieCountdownInterval = null;
    const agentErrorLogged = {};
    let gatewayUnreachableLogged = false;
    const expandedAgentTerminals = new Set();
    const SCROLL_BOTTOM_THRESHOLD = 30;

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
        updateCurrentPath(workspaceData.path || '');
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

      if (btn && dropdown) {
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          dropdown.classList.toggle('visible');
          if (dropdown.classList.contains('visible')) {
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
      fetch('/api/file-tree').then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); }).then(nodes => {
        renderFileTree(nodes || [], rootEl);
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
        function showFolderWraps() {
          const v = sel.value;
          customWrap.style.display = v === '__custom__' ? 'block' : 'none';
          if (newWrap) newWrap.style.display = v === '__new__' ? 'block' : 'none';
          if (v !== '__custom__' && v !== '__new__') saveProjectSetting('default_project_folder', 'overstory');
        }
        try {
          const r = await fetch('/api/ui-settings');
          const s = await r.json();
          const def = (s.default_project_folder || 'overstory').trim();
          if (def && def !== 'overstory') {
            sel.value = '__custom__';
            customInput.value = def;
          } else {
            sel.value = 'overstory';
          }
          showFolderWraps();
        } catch (_) { showFolderWraps(); }
        sel.addEventListener('change', showFolderWraps);
        if (showNewBtn) showNewBtn.addEventListener('click', function() {
          sel.value = '__new__';
          showFolderWraps();
          if (newInput) { newInput.value = ''; newInput.focus(); }
        });
        customInput.addEventListener('change', function() {
          saveProjectSetting('default_project_folder', this.value.trim() || 'overstory');
        });
        if (newBtn && newInput) {
          newBtn.addEventListener('click', async function() {
            const name = newInput.value.trim();
            if (!name) { alert('Enter a folder name'); return; }
            try {
              const res = await fetch('/api/project/create-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
              const data = await res.json();
              if (!res.ok) { alert(data.error || 'Failed to create folder'); return; }
              saveProjectSetting('default_project_folder', data.path);
              sel.value = '__custom__';
              customInput.value = data.path;
              newInput.value = '';
              showFolderWraps();
              loadWorkspace();
              loadFileTree();
            } catch (e) { alert('Failed: ' + (e.message || 'Unknown error')); }
          });
        }
        function saveProjectSetting(key, value) {
          fetch('/api/ui-settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [key]: value }) }).then(function() { loadWorkspace(); loadFileTree(); }).catch(function(){});
        }
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

    // Auto-prune completed worktrees every 10 minutes and log to terminal
    const AUTO_PRUNE_INTERVAL_MS = 10 * 60 * 1000;
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
      zombieCountdownSeconds = 300; // 5 minutes
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

    // Periodically check for zombies and slay them (every 5 minutes)
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

    // Refresh agent terminals (tmux output for each agent) — Chat tab only; polled less often to avoid load
    async function refreshAgentTerminals() {
      const chatTab = document.getElementById('tabChat');
      if (!chatTab || !chatTab.classList.contains('active')) return;
      const list = document.getElementById('agentTerminalsList');
      if (!list) return;
      try {
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
        list.innerHTML = terminals.filter(t => t).map(t => {
          var expanded = expandedAgentTerminals.has(t.name);
          var cap = (t.capability || '').toLowerCase();
          var isLeadOrSupervisor = cap === 'lead' || cap === 'supervisor';
          var bodyDisplay = (isLeadOrSupervisor && !expanded) ? 'none' : 'block';
          return `
          <div class="agent-terminal-item ${expanded ? 'agent-terminal-expanded' : ''} ${cap === 'lead' ? 'agent-terminal-lead' : ''} ${cap === 'supervisor' ? 'agent-terminal-supervisor' : ''}" data-agent-name="${escapeHtml(t.name)}" style="border: 1px solid #30363d; border-radius: 4px; overflow: hidden;">
            <div class="agent-terminal-header" style="background: #161b22; padding: 6px 10px; font-size: 12px; font-weight: 600; color: #58a6ff; cursor: pointer; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px;">
              <span onclick="toggleAgentTerminalExpand('${escapeHtml(t.name)}')" title="Click to expand/collapse">${t.capability ? `<span style="color: #8b949e;">[${t.capability}]</span> ` : ''}${t.name}</span>
              <span style="display: flex; gap: 6px; align-items: center;">
                <button type="button" onclick="event.stopPropagation(); toggleAgentTerminalExpand('${escapeHtml(t.name)}'); return false;" style="padding: 4px 8px; font-size: 11px; background: #21262d; color: #8b949e; border: 1px solid #30363d; border-radius: 4px; cursor: pointer;">${expanded ? 'Collapse' : 'Expand'}</button>
                ${t.system ? '' : `<button type="button" class="accept-mail-check-btn" onclick="event.stopPropagation(); acceptMailCheck('${escapeHtml(t.name)}', this);" style="padding: 4px 8px; font-size: 11px; background: #1f6feb; color: #fff; border: none; border-radius: 4px; cursor: pointer;" title="Accept 'overstory mail check' prompt (don't ask again)">Mail check</button>
                <button type="button" class="accept-disclaimer-btn" onclick="event.stopPropagation(); acceptDisclaimer('${escapeHtml(t.name)}', this);" style="padding: 4px 10px; font-size: 11px; background: #238636; color: #fff; border: none; border-radius: 4px; cursor: pointer;">Accept disclaimer</button>`}
              </span>
            </div>
            <div class="agent-terminal-body" style="display: ${bodyDisplay};">
              ${t.attachCmd ? `<div class="agent-terminal-attach" style="background: #21262d; padding: 6px 8px; font-size: 11px; color: #7ee787; font-family: monospace;">Watch live: <code style="user-select: all;">${escapeHtml(t.attachCmd)}</code></div>` : ''}
              <div class="agent-terminal-output" style="background: #0d1117; color: #e6edf3; font-family: monospace; font-size: 11px; padding: 8px; padding-bottom: 12px; white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(t.output)}</div>
              <button type="button" class="scroll-to-bottom-btn" onclick="var o=this.previousElementSibling; if(o) { o.scrollTop=o.scrollHeight; }" style="margin-top: 4px; padding: 2px 8px; font-size: 10px; background: #21262d; color: #8b949e; border: 1px solid #30363d; border-radius: 4px; cursor: pointer;">Scroll to bottom</button>
            </div>
          </div>
        `;
        }).join('');
        list.querySelectorAll('.agent-terminal-output').forEach(function(el) {
          requestAnimationFrame(function() {
            el.scrollTop = el.scrollHeight;
          });
        });
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

    // Gateway health (bottom bar)
    async function refreshGatewayHealth() {
      const el = document.getElementById('footerGatewayStatus');
      if (!el) return;
      try {
        const r = await fetch('/api/gateway/health', { cache: 'no-store' });
        const data = await r.json().catch(function() { return { ok: false }; });
        const ok = r.ok && (data.ok === true || data.status === 'ok');
        el.textContent = ok ? 'Gateway OK' : 'Gateway NOT OK';
        el.className = 'footer-gateway ' + (ok ? 'ok' : 'not-ok');
        el.title = ok ? 'Gateway is reachable' : (data.error || 'Gateway unreachable');
      } catch (_) {
        el.textContent = 'Gateway NOT OK';
        el.className = 'footer-gateway not-ok';
        el.title = 'Gateway unreachable';
      }
    }

    // Session token usage (bottom bar)
    function formatTokens(n) {
      if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
      if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
      return String(n);
    }
    var lastSessionUsage = { inputTokens: 0, outputTokens: 0, totalTokens: 0 };
    async function refreshSessionUsage() {
      try {
        const r = await fetch('/api/session-usage');
        const data = await r.json();
        const inT = data.inputTokens || 0;
        const outT = data.outputTokens || 0;
        const total = data.totalTokens || inT + outT;
        if (inT === lastSessionUsage.inputTokens && outT === lastSessionUsage.outputTokens) return;
        lastSessionUsage = { inputTokens: inT, outputTokens: outT, totalTokens: total };
        const elIn = document.getElementById('footerTokenIn');
        const elOut = document.getElementById('footerTokenOut');
        const elTotal = document.getElementById('footerTokenTotal');
        if (elIn) elIn.textContent = formatTokens(inT);
        if (elOut) elOut.textContent = formatTokens(outT);
        if (elTotal) elTotal.textContent = '(' + formatTokens(total) + ' total)';
      } catch (_) {}
    }

    // Refresh functions
    function refreshNow() {
      refreshDashboard();
      refreshMail();
      refreshBunLog();
      refreshTerminal();
      refreshAgentTerminals();
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

    // Chat functionality (unified /api/message: Mistral analyzes → direct answer / follow-up / handoff)
    const chatHistory = document.getElementById('chatHistory');
    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    const chatStatus = document.getElementById('chatStatus');
    let chatHistoryList = [];
    let pendingFollowUp = null;  // { original_message, questions } when need_follow_up returned

    function appendChat(role, text, isError) {
      chatHistoryList.push({ role, text, isError });
      const div = document.createElement('div');
      div.className = 'terminal-line ' + (isError ? 'error' : role === 'user' ? 'user' : 'info');
      div.textContent = (role === 'user' ? 'You: ' : 'Orchestrator: ') + text;
      chatHistory.appendChild(div);
      chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function showThinking() {
      const row = document.getElementById('thinkingRow');
      if (row) return;
      const div = document.createElement('div');
      div.id = 'thinkingRow';
      div.className = 'terminal-line thinking-row';
      div.innerHTML = 'Orchestrator: Thinking <span class="dot"></span><span class="dot"></span><span class="dot"></span>';
      chatHistory.appendChild(div);
      chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function hideThinking() {
      const row = document.getElementById('thinkingRow');
      if (row) row.remove();
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
            const summary = spawned ? 'Task routed; agent spawned.' : (data.capability ? `Routed (${data.capability}).` : 'Routed.');
            appendChat('assistant', summary + (data.message ? ' ' + data.message : '') + (data.spawn_error ? ' Spawn error: ' + data.spawn_error : '') + (data.created_project ? ' Project folder: ' + data.created_project : ''));
          } else {
            appendChat('assistant', data.response || data.message || JSON.stringify(data));
          }
        }
      } catch (e) {
        hideThinking();
        chatStatus.textContent = '';
        chatStatus.classList.remove('thinking');
        appendChat('assistant', 'Error: ' + e.message, true);
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
          REFRESH_INTERVAL = parseInt(ui.refresh_interval_ms, 10) || 1000;
          if (refreshSelect) {
            refreshSelect.value = String(REFRESH_INTERVAL);
            if (![500, 1000, 2000, 5000].includes(REFRESH_INTERVAL)) refreshSelect.value = '1000';
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
            refresh_interval_ms: parseInt(refreshSelect.value, 10) || 1000,
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
        refreshTick++;
        if (refreshTick % 3 === 0) refreshAgentTerminals();
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
        REFRESH_INTERVAL = parseInt(s.refresh_interval_ms, 10) || 1000;
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
