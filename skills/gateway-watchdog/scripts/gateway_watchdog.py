#!/usr/bin/env python3
"""
Gateway watchdog daemon for OpenClaw.

Runs every 10 seconds; if gateway is not connected (not running or auth mismatch),
runs: gateway_guard status --json, ensure --apply --json, then openclaw gateway.
Uses the gateway-guard skill's script (same workspace). Daemon-friendly: no TTY, SIGTERM for clean exit, logs to OPENCLAW_HOME/logs.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

INTERVAL_SEC = 10
_shutdown = False


def _openclaw_home():
    return Path(os.environ.get("OPENCLAW_HOME") or os.path.expanduser("~/.openclaw"))


def _guard_script():
    """Resolve gateway_guard.py from the gateway-guard skill (sibling skill dir)."""
    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    guard_skill = skill_dir.parent / "gateway-guard" / "scripts" / "gateway_guard.py"
    if guard_skill.exists():
        return guard_skill
    return _openclaw_home() / "workspace" / "skills" / "gateway-guard" / "scripts" / "gateway_guard.py"


def _log_path():
    return _openclaw_home() / "logs" / "gateway-watchdog.log"


def _log(msg: str) -> None:
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if not _shutdown:
        print(line.strip(), flush=True)


def _run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def check_status():
    """Run gateway_guard status --json and return parsed result or None on failure."""
    guard = _guard_script()
    if not guard.exists():
        _log("guard script not found: %s" % guard)
        return None
    env = os.environ.copy()
    env.setdefault("OPENCLAW_HOME", str(_openclaw_home()))
    try:
        r = _run(
            [sys.executable, str(guard), "status", "--json"],
            timeout=12,
        )
        if r.returncode is None or not r.stdout.strip():
            return None
        return json.loads(r.stdout.strip())
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        _log("status check failed: %s" % e)
        return None


def run_recovery() -> None:
    """Run status --json, ensure --apply --json, then openclaw gateway."""
    guard = str(_guard_script())
    env = os.environ.copy()
    env.setdefault("OPENCLAW_HOME", str(_openclaw_home()))

    try:
        _run([sys.executable, guard, "status", "--json"], timeout=12)
    except subprocess.TimeoutExpired:
        pass

    try:
        r = _run(
            [sys.executable, guard, "ensure", "--apply", "--json"],
            timeout=25,
        )
        if r.stdout:
            _log("ensure --apply: %s" % r.stdout.strip()[:200])
    except subprocess.TimeoutExpired:
        _log("ensure --apply timed out")

    try:
        subprocess.Popen(
            ["openclaw", "gateway"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(_openclaw_home()),
        )
        _log("started openclaw gateway")
    except FileNotFoundError:
        _log("openclaw not found in PATH")


def _sigterm(_signum, _frame) -> None:
    global _shutdown
    _shutdown = True


def main() -> None:
    signal.signal(signal.SIGTERM, _sigterm)
    _log("gateway watchdog started (interval=%ss)" % INTERVAL_SEC)

    while not _shutdown:
        result = check_status()
        if result is None:
            _log("status check failed, running recovery")
            run_recovery()
        elif not result.get("ok", False):
            _log("gateway not ok (reason=%s), running recovery" % result.get("reason", "?"))
            run_recovery()

        for _ in range(INTERVAL_SEC):
            if _shutdown:
                break
            time.sleep(1)

    _log("gateway watchdog stopped")


if __name__ == "__main__":
    main()
    sys.exit(0)
