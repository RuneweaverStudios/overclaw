#!/usr/bin/env python3
"""
End-to-end test: trigger a multi-agent task (lead + builder), poll until completion.
NO SIMULATION OR FALLBACKS. Success only when the builder agent creates the marker file
inside its worktree (overstory forbids writes to canonical repo root).
"""
import json
import os
import sys
import time
from pathlib import Path
WORKSPACE = Path(os.environ.get("OVERCLAW_WORKSPACE", Path(__file__).resolve().parent.parent))
GATEWAY_URL = os.environ.get("OVERCLAW_GATEWAY_URL", "http://localhost:18800")
MARKER_FILENAME = "test_multi_agent_done.txt"
MARKER_CONTENT = "multi-agent e2e test completed.\n"
# Task must be worktree-safe: builder can only write inside its worktree
TASK = (
    "Create a file named test_multi_agent_done.txt in your worktree root (the current working directory) "
    "containing exactly the line: multi-agent e2e test completed. Do nothing else."
)


def req(method: str, path: str, json_body: dict = None) -> dict:
    import urllib.request
    url = f"{GATEWAY_URL.rstrip('/')}{path}"
    data = json.dumps(json_body).encode() if json_body else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def overstory_status_json() -> dict:
    import subprocess
    overstory_bin = os.environ.get("OVERSTORY_BIN", os.path.expanduser("~/.bun/bin/overstory"))
    try:
        out = subprocess.run(
            [overstory_bin, "status", "--json"],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            return {"error": out.stderr or out.stdout, "agents": []}
        return json.loads(out.stdout)
    except Exception as e:
        return {"error": str(e), "agents": []}


def main():
    print("1. Checking gateway...")
    health = req("GET", "/health")
    if health.get("error") or health.get("status") != "ok":
        print("   FAIL: Gateway not ready:", health)
        return 1
    print("   OK: Gateway ready")

    print("2. Clearing zombies if any...")
    slay = req("POST", "/api/zombies/slay")
    if slay.get("slain", 0) > 0:
        print("   Slain", slay["slain"], "zombies")
    else:
        print("   No zombies")

    print("3. Triggering multi-agent task (route + spawn)...")
    route = req("POST", "/api/route", {"task": TASK, "spawn": True})
    if route.get("error"):
        print("   FAIL: route error:", route["error"])
        return 1
    if not route.get("spawned", True):
        print("   FAIL: spawn failed:", route.get("spawn_error", route))
        return 1
    task_id = route.get("task_id", "")
    # Builder worktree from gateway spawn result (overstory sling --json returns worktree)
    spawn_result = route.get("spawn_result") or {}
    builder_worktree = spawn_result.get("worktree")
    if not builder_worktree or not Path(builder_worktree).exists():
        print("   FAIL: No builder worktree in spawn result:", spawn_result)
        return 1
    wt = Path(builder_worktree)
    print("   OK: Spawned. Task ID:", task_id, "Builder worktree:", wt)

    time.sleep(5)
    timeout_s = 300
    step_s = 10
    start = time.time()
    while time.time() - start < timeout_s:
        elapsed = int(time.time() - start)
        st = overstory_status_json()
        agents = st.get("agents") or []
        states = [a.get("state", "") for a in agents]
        all_zombie = agents and all((s or "").lower() == "zombie" for s in states)

        if wt:
            marker_path = wt / MARKER_FILENAME
            if marker_path.exists():
                content = marker_path.read_text()
                if "multi-agent e2e test completed" in content:
                    print(f"   SUCCESS (t={elapsed}s): Builder created marker in worktree.")
                    return 0

        if all_zombie:
            print(f"   FAIL (t={elapsed}s): All agents zombie. No simulation.")
            return 1

        time.sleep(step_s)

    print("   FAIL: Timeout. Builder did not create marker file in worktree.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
