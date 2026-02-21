#!/usr/bin/env python3
"""E2E test: task requiring approval → spawn lead+worker → worker mails lead → lead approves via gateway (no simulation).

Flow:
1. POST /api/route with a task that routes to a worker so we get lead + worker.
2. Task instructs worker: when you hit a confirmation prompt, send mail to your lead (parent), then wait.
3. Accept disclaimers for lead and worker.
4. Wait for REAL mail from worker to lead with "Need approval" (no simulation). Fail if timeout.
5. Wait for the LEAD to approve (lead runs approve_agent via gateway_tools; we poll worker terminal to see prompt accepted). Fail if lead does not approve in time.

Run from workspace root. Requires: gateway on 18800, overstory, tmux. Lead must have gateway_tools and instructions to approve on "Need approval" mail.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MAIL_DB = WORKSPACE / ".overstory" / "mail.db"
GATEWAY_URL = os.environ.get("OVERCLAW_GATEWAY_URL", "http://localhost:18800")


def log(msg: str) -> None:
    print(f"[test] {msg}", flush=True)


def route_spawn(task: str) -> dict:
    import urllib.request
    req = urllib.request.Request(
        f"{GATEWAY_URL}/api/route",
        data=json.dumps({"task": task, "spawn": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def accept_disclaimer(agent: str) -> dict:
    import urllib.request
    import urllib.parse
    req = urllib.request.Request(
        f"{GATEWAY_URL}/api/agents/{urllib.parse.quote(agent)}/accept-disclaimer",
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def approve_agent(agent: str) -> dict:
    import urllib.request
    import urllib.parse
    req = urllib.request.Request(
        f"{GATEWAY_URL}/api/agents/{urllib.parse.quote(agent)}/approve",
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def send_mail(from_agent: str, to_agent: str, message: str) -> dict:
    import urllib.request
    req = urllib.request.Request(
        f"{GATEWAY_URL}/api/agents/mail",
        data=json.dumps({"from": from_agent, "to": to_agent, "message": message}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_recent_mail(limit: int = 20) -> list[dict]:
    if not MAIL_DB.exists():
        return []
    conn = sqlite3.connect(str(MAIL_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT from_agent, to_agent, subject, body FROM messages ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_worker_terminal(worker_name: str, lines: int = 150) -> str:
    import urllib.request
    import urllib.parse
    req = urllib.request.Request(
        f"{GATEWAY_URL}/api/agents/{urllib.parse.quote(worker_name)}/terminal?lines={lines}",
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        return (data.get("output") or "")


def main() -> int:
    # Check gateway is up and approve route exists (gateway must be restarted to load new route)
    log("0. Check gateway and /api/agents/{name}/approve...")
    try:
        import urllib.request
        import urllib.parse
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/agents/test-agent-approve-route/approve",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        err = str(e)
        if "404" in err:
            log("   Approve route not found (404). Restart OverClaw gateway to load it, then re-run.")
            return 1
        if "Connection refused" in err or "Errno 61" in err:
            log("   Gateway not running. Start it (e.g. ./scripts/start-overclaw.sh) then re-run.")
            return 1
        # 500 or other error likely means route exists but agent doesn't
    log("   Gateway and approve route OK.")
    # Task that must route to builder (script/implement) so we get lead + worker.
    task = (
        "Implement a tiny script in the workspace that runs one bash command. "
        "Run the script so Claude shows a confirmation prompt (Do you want to run this?). "
        "As soon as you see that prompt, send mail to your lead (Parent in your startup beacon, e.g. lead-abc12345): "
        "curl -s -X POST http://localhost:18800/api/agents/mail -H 'Content-Type: application/json' "
        "-d '{\"from\":\"YOUR_AGENT_NAME\",\"to\":\"LEAD_NAME\",\"message\":\"Need approval\"}' "
        "with YOUR_AGENT_NAME and LEAD_NAME set. Then wait for the lead to approve you."
    )
    log("1. POST /api/route (spawn=true)...")
    try:
        result = route_spawn(task)
    except Exception as e:
        log(f"   route_spawn failed: {e}")
        return 1
    if result.get("error"):
        log(f"   error: {result['error']}")
        return 1
    if not result.get("spawned"):
        log("   not spawned (spawned=false or spawn_error)")
        if result.get("spawn_error"):
            log(f"   spawn_error: {result['spawn_error']}")
        log(f"   {json.dumps({k: v for k, v in result.items() if k not in ('original_task', 'description')}, indent=2)[:800]}")
        return 1
    task_id = result.get("task_id", "")[:8]
    lead_name = f"lead-{task_id}"
    worker_name = result.get("name") or result.get("capability", "builder") + "-" + task_id
    log(f"   task_id={task_id} lead={lead_name} worker={worker_name}")
    log("2. Accept disclaimers for lead and worker...")
    for name in [lead_name, worker_name]:
        try:
            r = accept_disclaimer(name)
            log(f"   {name}: ok={r.get('ok')}")
        except Exception as e:
            log(f"   {name}: {e}")
    log("3. Wait for REAL 'Need approval' mail from worker to lead (no simulation)...")
    mail_deadline = time.time() + 300  # 5 min for worker to hit prompt and send mail
    seen_approval_mail = False
    while time.time() < mail_deadline:
        mail = get_recent_mail(50)
        for m in mail:
            from_a = (m.get("from_agent") or "").strip()
            to_a = (m.get("to_agent") or "").strip()
            body = (m.get("body") or "").strip()
            if to_a == lead_name and "need approval" in body.lower():
                seen_approval_mail = True
                worker_name = from_a
                log(f"   Found real mail from {worker_name} -> {lead_name}: Need approval")
                break
        if seen_approval_mail:
            break
        time.sleep(5)
    if not seen_approval_mail:
        log("   FAIL: Worker did not send 'Need approval' mail within 5 min. No simulation.")
        return 1

    log("4. Wait for LEAD to approve worker (lead runs approve_agent; we poll worker terminal)...")
    # Snapshot worker terminal now; we'll look for it to change (prompt accepted)
    try:
        initial_output = get_worker_terminal(worker_name)
    except Exception as e:
        log(f"   Could not get worker terminal: {e}")
        initial_output = ""
    approve_deadline = time.time() + 120  # 2 min for lead to approve
    approved = False
    while time.time() < approve_deadline:
        time.sleep(10)
        try:
            current = get_worker_terminal(worker_name)
        except Exception:
            current = ""
        # Consider approved if terminal output grew significantly (lead sent Down+Enter, worker continued)
        if len(current) > len(initial_output) + 200:
            approved = True
            log("   Worker terminal advanced after approval.")
            break
        # Or if the confirmation prompt text disappeared / we see new content after it
        if "Do you want to" in initial_output and ("Do you want to" not in current[-2000:] or len(current) > len(initial_output) + 100):
            approved = True
            log("   Worker prompt accepted (output changed).")
            break
    if not approved:
        log("   FAIL: Lead did not approve worker within 2 min. Lead must run: python3 $GATEWAY_TOOLS exec-tool --tool approve_agent --params '{\"agent\":\"<worker>\"}' when it receives Need approval mail.")
        return 1
    log("5. Done. Approval flow: spawn -> worker mails lead -> lead approves (no simulation).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
