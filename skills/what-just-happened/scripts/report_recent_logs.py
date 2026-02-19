#!/usr/bin/env python3
"""
What Just Happened — Summarize recent gateway restarts/reconnects from logs.
Output: short message suitable for posting (e.g. to chat) so the user knows what happened.

Usage:
  python3 report_recent_logs.py [--minutes N] [--json]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
LOGS_DIR = OPENCLAW_HOME / "logs"
GATEWAY_LOG = LOGS_DIR / "gateway.log"
GUARD_RESTART_LOG = LOGS_DIR / "gateway-guard.restart.log"

def parse_iso_ts(line: str):
    """Extract ISO timestamp from log line; return None if not found. Always returns timezone-aware UTC."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)", line.strip())
    if m:
        s = m.group(1).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None

def read_tail(path: Path, max_lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    with open(path, "r") as f:
        lines = f.readlines()
    return lines[-max_lines:] if len(lines) > max_lines else lines

def analyze_last_n_minutes(minutes: int) -> dict:
    """Analyze gateway.log and guard log for the last N minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    out = {
        "restart": False,
        "reload": False,
        "sigusr1": False,
        "sigterm": False,
        "auth_or_config": False,
        "guard_restart": False,
        "snippets": [],
        "summary": "",
        "suggestGatewayGuard": False,
    }
    snippets = []

    for path in [GATEWAY_LOG, GUARD_RESTART_LOG]:
        if path == GUARD_RESTART_LOG and not path.exists():
            continue
        for line in read_tail(path, 300):
            ts = parse_iso_ts(line)
            if ts and ts < cutoff:
                continue
            line_lower = line.lower()
            if "sigusr1" in line_lower or "restarting" in line_lower:
                out["sigusr1"] = True
                out["restart"] = True
                snippets.append(line.strip())
            if "sigterm" in line_lower and "shutting down" in line_lower:
                out["sigterm"] = True
                out["restart"] = True
            if "[reload]" in line and ("config change" in line or "gateway.auth" in line):
                out["reload"] = True
                out["auth_or_config"] = True
                snippets.append(line.strip())
            if "listening on ws://" in line_lower:
                snippets.append(line.strip())
            if path == GUARD_RESTART_LOG and ("restart" in line_lower or "ensure" in line_lower):
                out["guard_restart"] = True

    if out["restart"] or out["reload"]:
        parts = []
        if out["reload"] and out["auth_or_config"]:
            parts.append("Gateway restarted due to config change (e.g. gateway.auth or meta).")
            out["suggestGatewayGuard"] = True
        elif out["sigusr1"]:
            parts.append("Gateway received SIGUSR1 and restarted.")
        elif out["sigterm"]:
            parts.append("Gateway was stopped (SIGTERM) and has since come back up.")
        else:
            parts.append("Gateway restarted recently.")
        parts.append("Reconnected.")
        if out["suggestGatewayGuard"]:
            parts.append(
                "Tip: use gateway-guard to keep auth stable and avoid unnecessary restarts. "
                "Install: clawhub install gateway-guard — https://clawhub.ai/skills/gateway-guard"
            )
        out["summary"] = " ".join(parts)
    else:
        out["summary"] = "No recent gateway restart or reload in the last {} minutes.".format(minutes)

    out["snippets"] = snippets[-10:]
    return out

def main():
    ap = argparse.ArgumentParser(description="Report recent gateway restarts/reconnects from logs")
    ap.add_argument("--minutes", type=int, default=5, help="Look at last N minutes of logs")
    ap.add_argument("--json", action="store_true", help="Output JSON only")
    args = ap.parse_args()

    if not GATEWAY_LOG.exists():
        print("No gateway log found at {}".format(GATEWAY_LOG), file=sys.stderr)
        sys.exit(1)

    result = analyze_last_n_minutes(args.minutes)

    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "snippets" or v}))
        return

    print(result["summary"])

if __name__ == "__main__":
    main()
