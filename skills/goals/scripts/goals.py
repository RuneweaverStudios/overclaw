#!/usr/bin/env python3
"""
Goals | OpenClaw Skill

Reads the Notes folder (from scribe skill), infers user goals, categorizes into
personal/professional and short/long-term, and generates a morning brief with
action plan and motivation. Designed to run every morning.
"""

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Default Notes path relative to workspace (scribe output)
NOTES_DAILY = "Notes/daily"
NOTES_WEEKLY = "Notes/weekly"
NOTES_GOALS = "Notes/goals"
MEMORY_DIR = "memory"
MEMORY_FILE = "MEMORY.md"

# Keywords for classification
PROFESSIONAL_KEYWORDS = [
    "skill", "blog", "publish", "project", "gateway", "agent", "router", "fix", "bug",
    "code", "deploy", "clawhub", "openclaw", "draft", "editor", "website", "api",
    "work", "task", "sprint", "release", "repo", "github", "pr", "merge",
]
PERSONAL_KEYWORDS = [
    "health", "exercise", "learn", "read", "family", "hobby", "rest", "sleep",
    "meditate", "walk", "personal", "side project", "fun",
]

# Motivation and inspiration (rotated by day of year)
MOTIVATION = [
    "The best time to start was yesterday. The next best time is now.",
    "Small steps every day add up to big results.",
    "Focus on progress, not perfection.",
    "Your only limit is you. Push it.",
    "Do one thing today that your future self will thank you for.",
    "Clarity comes from engagement, not thought.",
    "Start where you are. Use what you have. Do what you can.",
    "The secret of getting ahead is getting started.",
    "Quality is not an act, it is a habit.",
    "Make today count.",
    "One task at a time. One win at a time.",
    "Your goals are valid. Your effort is enough.",
    "Routine sets the stage; action steals the show.",
    "Morning sets the tone. Own it.",
    "Short-term discipline, long-term freedom.",
]


def _openclaw_workspace(openclaw_home: Path) -> Path:
    return openclaw_home / "workspace"


def _notes_daily(openclaw_home: Path) -> Path:
    return _openclaw_workspace(openclaw_home) / "Notes" / "daily"


def _notes_weekly(openclaw_home: Path) -> Path:
    return _openclaw_workspace(openclaw_home) / "Notes" / "weekly"


def _notes_goals(openclaw_home: Path) -> Path:
    return _openclaw_workspace(openclaw_home) / "Notes" / "goals"


def _memory_dir(openclaw_home: Path) -> Path:
    return _openclaw_workspace(openclaw_home) / MEMORY_DIR


def _memory_file(openclaw_home: Path) -> Path:
    return _openclaw_workspace(openclaw_home) / MEMORY_FILE


def _read_text(path: Path, max_chars: int = 50000) -> str:
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def _list_recent_md(dir_path: Path, days_back: int = 14) -> List[Path]:
    if not dir_path.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days_back)
    out = []
    for p in dir_path.glob("*.md"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


# Section titles and patterns to skip when extracting goals (scribe noise)
_SKIP_HEADERS = {
    "daily note", "weekly note", "summary", "logs", "chat history", "memory files",
    "drafts", "behavior", "configuration", "trends", "patterns", "errors", "warnings",
    "daily summary", "weekly summary", "weekly note", "action plan", "motivation",
    "gateway events", "subagent activity", "config loaded",
    "recent activity", "model preferences",
}
_SKIP_PATTERNS = re.compile(
    r"^\s*\*\*[^*]+\*\*:\s*[\d,]+\s*(errors|warnings|events|spawns|messages|files)|"
    r"^(size|modified|preview):|"
    r"^\d{4}-\d{2}-\d{2}(T|\s)|"
    r"^-?\s*\*\*\[?\d|"
    r"^\[?\d{4}-\d{2}-\d{2}[T\s]|"  # timestamp start
    r"^errors\s*\(\d+\)|^warnings\s*\(\d+\)",
    re.IGNORECASE
)


def _should_skip_header(clean: str) -> bool:
    lower = clean.lower()
    if any(skip in lower for skip in _SKIP_HEADERS):
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}\s", clean):  # "2026-02-17 ..."
        return True
    return False


def _should_skip_bullet(clean: str) -> bool:
    lower = clean.lower()
    if any(x in lower for x in ("bytes", "modified:", "preview:", "size:", "errors)", "warnings)", "config**:", "loaded successfully")):
        return True
    if re.match(r"^\*\*[^*]+\*\*:\s*[\d,]", clean):  # "**Logs**: 386"
        return True
    if re.match(r"^errors\s*\(\d+\)|^warnings\s*\(\d+\)", lower):
        return True
    if re.match(r"^\[\d{4}-\d{2}-\d{2}", clean):  # "[2026-02-16 ..."
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}[T\s]", clean):  # ISO date / timestamp at start
        return True
    if clean.startswith("Error:") or "exit=1" in clean or "stderr=" in clean or "ensure --apply" in clean:
        return True
    if "WARNING_" in clean or "openclaw_tools not found" in lower:
        return True
    if re.match(r"^(default|aliases):", lower) or "configured" in lower and len(clean) < 60:
        return True
    if clean == "Model Preferences":
        return True
    return False


def _extract_goals_from_text(text: str) -> List[Tuple[str, str]]:
    """Extract goal-like phrases: headers, bullet lines, and 'goal/todo/want/need' sentences."""
    goals = []
    lines = text.split("\n")
    for line in lines:
        s = line.strip()
        if not s or len(s) < 8:
            continue
        if _SKIP_PATTERNS.search(s):
            continue
        # Headers as goals (skip known scribe sections)
        if re.match(r"^#+\s+.+", s):
            clean = re.sub(r"^#+\s+", "", s).strip()
            if _should_skip_header(clean) or re.match(r"^\d+$", clean):
                continue
            if 8 < len(clean) < 200:
                goals.append((clean, "header"))
        # Bullets: skip metadata, timestamps, stats
        elif re.match(r"^[-*]\s+", s) or re.match(r"^\d+\.\s+", s):
            clean = re.sub(r"^[-*]\s+", "", s)
            clean = re.sub(r"^\d+\.\s+", "", clean)
            if _should_skip_bullet(clean):
                continue
            if 10 < len(clean) < 400:
                goals.append((clean, "bullet"))
    # Sentence patterns (goal/todo/want/need)
    for m in re.finditer(r"(?:goal|todo|want to|need to|should|plan to|finish|ship|publish|fix|build)\s+[^.!?\n]{5,120}", text, re.IGNORECASE):
        goals.append((m.group(0).strip(), "sentence"))
    return goals


def _classify_goal(text: str) -> Tuple[str, str]:
    """Returns (personal|professional, short_term|long_term)."""
    t = text.lower()
    personal_score = sum(1 for k in PERSONAL_KEYWORDS if k in t)
    professional_score = sum(1 for k in PROFESSIONAL_KEYWORDS if k in t)
    domain = "personal" if personal_score > professional_score else "professional"
    # Short vs long: mention of "today", "this week", "now" -> short; "month", "quarter", "year" -> long
    if any(x in t for x in ("today", "this week", "this morning", "now", "asap", "urgent")):
        horizon = "short_term"
    elif any(x in t for x in ("this month", "quarter", "this year", "long term", "eventually")):
        horizon = "long_term"
    else:
        horizon = "short_term"  # default to short for actionable items
    return domain, horizon


def _dedupe_goals(goals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for g in goals:
        key = (g.get("goal", "").lower()[:80], g.get("domain"), g.get("horizon"))
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


def _build_goals(openclaw_home: Path, daily_days: int = 7, weekly_days: int = 14) -> List[Dict[str, Any]]:
    goals_raw: List[Tuple[str, str]] = []
    workspace = _openclaw_workspace(openclaw_home)

    # Daily notes
    daily_dir = _notes_daily(openclaw_home)
    for p in _list_recent_md(daily_dir, days_back=daily_days):
        goals_raw.extend(_extract_goals_from_text(_read_text(p)))

    # Weekly notes
    weekly_dir = _notes_weekly(openclaw_home)
    for p in _list_recent_md(weekly_dir, days_back=weekly_days):
        goals_raw.extend(_extract_goals_from_text(_read_text(p)))

    # Memory
    memory_path = _memory_file(openclaw_home)
    if memory_path.exists():
        goals_raw.extend(_extract_goals_from_text(_read_text(memory_path, max_chars=15000)))
    for p in _list_recent_md(_memory_dir(openclaw_home), days_back=daily_days):
        goals_raw.extend(_extract_goals_from_text(_read_text(p)))

    # Build structured goals
    structured = []
    for goal_text, source in goals_raw:
        if len(goal_text) < 5:
            continue
        domain, horizon = _classify_goal(goal_text)
        structured.append({
            "goal": goal_text[:300],
            "domain": domain,
            "horizon": horizon,
            "source": source,
        })
    return _dedupe_goals(structured)


def _action_plan(goals: List[Dict[str, Any]], max_items: int = 5) -> List[str]:
    """Suggest top actions: short-term professional first, then short-term personal, then long-term."""
    def ok(a: str) -> bool:
        if len(a) < 12 or len(a) > 250:
            return False
        if a.startswith("**") or a.startswith("- [") or "bytes" in a.lower() or "modified" in a.lower():
            return False
        return True

    short_pro = [g["goal"] for g in goals if g["domain"] == "professional" and g["horizon"] == "short_term" and ok(g["goal"])]
    short_per = [g["goal"] for g in goals if g["domain"] == "personal" and g["horizon"] == "short_term" and ok(g["goal"])]
    long_pro = [g["goal"] for g in goals if g["domain"] == "professional" and g["horizon"] == "long_term" and ok(g["goal"])]
    long_per = [g["goal"] for g in goals if g["domain"] == "personal" and g["horizon"] == "long_term" and ok(g["goal"])]
    ordered = (short_pro[:2] + short_per[:1] + long_pro[:1] + long_per[:1])[:max_items]
    if not ordered and goals:
        ordered = [g["goal"] for g in goals if ok(g["goal"])][:max_items]
    # Prefer header-style and sentence-style goals over raw paths for action plan
    return ordered


def _morning_quote() -> str:
    day_of_year = datetime.now().timetuple().tm_yday
    return MOTIVATION[day_of_year % len(MOTIVATION)]


def _render_morning_brief(
    goals: List[Dict[str, Any]],
    action_plan: List[str],
    motivation: str,
    generated_at: str,
) -> str:
    def by_domain(g: Dict[str, Any]) -> str:
        return g.get("domain", "professional")

    def by_horizon(g: Dict[str, Any]) -> str:
        return g.get("horizon", "short_term")

    personal = [g for g in goals if by_domain(g) == "personal"]
    professional = [g for g in goals if by_domain(g) == "professional"]
    short_term = [g for g in goals if by_horizon(g) == "short_term"]
    long_term = [g for g in goals if by_horizon(g) == "long_term"]

    lines = [
        "# Morning Goals & Action Plan",
        "",
        f"**Generated:** {generated_at}",
        "",
        "## Summary",
        "",
        f"- **Goals identified:** {len(goals)} (personal: {len(personal)}, professional: {len(professional)})",
        f"- **Short-term:** {len(short_term)} | **Long-term:** {len(long_term)}",
        "",
        "---",
        "",
        "## Goals by category",
        "",
        "### Professional",
        "",
    ]
    for g in professional[:15]:
        lines.append(f"- [{g['horizon']}] {g['goal']}")
    lines.extend(["", "### Personal", ""])
    for g in personal[:15]:
        lines.append(f"- [{g['horizon']}] {g['goal']}")
    if not personal:
        lines.append("- *(No personal goals extracted from Notes. Add them in memory or daily notes.)*")

    lines.extend([
        "",
        "## Short-term goals",
        "",
    ])
    for g in short_term[:10]:
        lines.append(f"- {g['goal']}")
    if not short_term:
        lines.append("- *(None inferred. Focus on one small win today.)*")

    lines.extend([
        "",
        "## Long-term goals",
        "",
    ])
    for g in long_term[:10]:
        lines.append(f"- {g['goal']}")
    if not long_term:
        lines.append("- *(None inferred. Consider adding a monthly or quarterly goal in MEMORY.md.)*")

    lines.extend([
        "",
        "## Today's action plan",
        "",
    ])
    for i, action in enumerate(action_plan, 1):
        lines.append(f"{i}. {action}")
    if not action_plan:
        lines.append("1. Review Notes and pick one priority for today.")

    lines.extend([
        "",
        "## Motivation & inspiration",
        "",
        f"> {motivation}",
        "",
    ])
    return "\n".join(lines)


def run(openclaw_home: Path) -> Tuple[Path, Dict[str, Any]]:
    goals_dir = _notes_goals(openclaw_home)
    goals_dir.mkdir(parents=True, exist_ok=True)

    goals = _build_goals(openclaw_home)
    action_plan = _action_plan(goals)
    motivation = _morning_quote()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = _render_morning_brief(goals, action_plan, motivation, generated_at)
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = goals_dir / f"{today}_morning.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    result = {
        "goals_file": str(out_path),
        "goals_count": len(goals),
        "personal_count": len([g for g in goals if g.get("domain") == "personal"]),
        "professional_count": len([g for g in goals if g.get("domain") == "professional"]),
        "short_term_count": len([g for g in goals if g.get("horizon") == "short_term"]),
        "long_term_count": len([g for g in goals if g.get("horizon") == "long_term"]),
        "action_plan": action_plan,
        "motivation": motivation,
    }
    return out_path, result


def main():
    parser = argparse.ArgumentParser(
        description="Goals: morning brief from Notes (scribe) — goals, action plan, motivation."
    )
    parser.add_argument(
        "--openclaw-home",
        type=str,
        default=None,
        help="OpenClaw home directory (default: ~/.openclaw)",
    )
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    openclaw_home = Path(args.openclaw_home) if args.openclaw_home else Path.home() / ".openclaw"

    try:
        out_path, result = run(openclaw_home)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Morning goals written to: {result['goals_file']}")
            print(f"Goals: {result['goals_count']} (personal: {result['personal_count']}, professional: {result['professional_count']})")
            print(f"Action plan: {len(result['action_plan'])} items")
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e), "goals_file": None}))
        else:
            raise


if __name__ == "__main__":
    main()
