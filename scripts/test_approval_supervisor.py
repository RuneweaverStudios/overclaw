#!/usr/bin/env python3
"""Test approval supervisor: mail to lead triggers analyze + respond so no approvals are missed.

Requires: gateway on 18800, Ollama running (optional — on failure messages are treated as approval).
Run: python3 scripts/test_approval_supervisor.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:18800")
MAIL_DB = os.path.join(WORKSPACE, ".overstory", "mail.db")


def log(msg: str) -> None:
    print(f"[test] {msg}", flush=True)


def post_mail(from_agent: str, to_agent: str, body: str) -> bool:
    import json
    data = json.dumps({"from": from_agent, "to": to_agent, "message": body}).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL.rstrip('/')}/api/agents/mail",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except urllib.error.URLError as e:
        log(f"POST mail failed: {e}")
        return False


def trigger_supervisor_run() -> int:
    """Run approval supervisor once; return approved count."""
    import json
    req = urllib.request.Request(
        f"{GATEWAY_URL.rstrip('/')}/api/debug/approval-supervisor-run",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read())
            return out.get("approved", 0)
    except urllib.error.URLError as e:
        log(f"Trigger supervisor failed: {e}")
        return -1


def count_unread_to_lead() -> int:
    if not os.path.isfile(MAIL_DB):
        return 0
    try:
        conn = sqlite3.connect(MAIL_DB, timeout=5)
        cur = conn.execute(
            """SELECT COUNT(*) FROM messages
               WHERE read = 0 AND (to_agent = 'lead' OR to_agent LIKE 'lead-%')"""
        )
        n = cur.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def main() -> int:
    log("Approval supervisor test — ensure supervisor is always active and responds on every new mail")
    # 1) Health check gateway
    try:
        req = urllib.request.Request(f"{GATEWAY_URL.rstrip('/')}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status != 200:
                log("Gateway health returned non-200")
                return 1
    except urllib.error.URLError as e:
        log(f"Gateway not reachable at {GATEWAY_URL}. Start it first (e.g. ./scripts/start-overclaw.sh or run overclaw_gateway.py).")
        log(f"Error: {e}")
        return 1
    log("Gateway OK")
    # 2) Send approval-request mail to lead
    from_agent = "builder-test-approval"
    body = "Need approval for next step. Please approve so I can continue."
    if not post_mail(from_agent, "lead", body):
        log("Failed to POST mail")
        return 1
    log("Posted mail to lead (Need approval)")
    # 3) Give loop a moment (or wake already happened)
    time.sleep(0.5)
    unread_before = count_unread_to_lead()
    # 4) Trigger one supervisor run (simulates wake on new mail)
    approved = trigger_supervisor_run()
    if approved < 0:
        log("Failed to trigger approval supervisor run")
        return 1
    log(f"Supervisor run: approved={approved}")
    # 5) Trigger again to ensure we process (second run should have 0 new)
    approved2 = trigger_supervisor_run()
    time.sleep(0.2)
    unread_after = count_unread_to_lead()
    # 6) Assert: message should be read (unread count dropped or approved >= 1)
    if unread_after < 0:
        log("Could not read mail.db")
        return 1
    if unread_before is None:
        unread_before = 0
    if approved >= 1 or unread_after < unread_before:
        log("PASS: Supervisor processed approval mail (message read or approved).")
        return 0
    log(f"FAIL: unread_before={unread_before} unread_after={unread_after} approved={approved}. Expected message to be processed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
