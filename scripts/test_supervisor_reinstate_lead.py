#!/usr/bin/env python3
"""Test: supervisor reinstates missing leads when mail is to lead-X and that lead is not active.

1. Ensure gateway is up.
2. Insert unread mail: builder-4b36c48 -> lead-4b36c480: Need approval.
3. POST /api/debug/reinstate-missing-leads-run (should spawn lead-4b36c480 if not active).
4. POST /api/debug/approval-supervisor-run (process approvals).
5. Optionally verify lead appears in status or mail was processed.

Run from workspace root. Requires: gateway on 18800.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MAIL_DB = WORKSPACE / ".overstory" / "mail.db"
GATEWAY_URL = os.environ.get("OVERCLAW_GATEWAY_URL", "http://localhost:18800")


def log(msg: str) -> None:
    print(f"[test] {msg}", flush=True)


def http_post(url: str, data: dict | None = None, timeout: int = 130) -> dict:
    body = json.dumps(data).encode() if data else b""
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def http_get(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def ensure_mail_to_missing_lead() -> None:
    """Ensure there is unread mail to lead-4b36c480 so reinstate has something to act on."""
    MAIL_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MAIL_DB), timeout=10)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           from_agent TEXT NOT NULL,
           to_agent TEXT NOT NULL,
           subject TEXT NOT NULL DEFAULT '',
           body TEXT NOT NULL DEFAULT '',
           priority TEXT NOT NULL DEFAULT 'normal',
           read INTEGER NOT NULL DEFAULT 0,
           created_at REAL NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO messages (from_agent, to_agent, subject, body, priority, read, created_at) VALUES (?,?,?,?,?,0,?)",
        ("builder-4b36c48", "lead-4b36c480", "Need approval", "Need approval for next step. Please approve.", "normal", time.time()),
    )
    conn.commit()
    conn.close()
    log("Inserted unread mail: builder-4b36c48 -> lead-4b36c480: Need approval")


def main() -> int:
    log("1. Check gateway...")
    try:
        health = http_get(f"{GATEWAY_URL}/health")
        if not health.get("ok", True):
            log("Gateway health not ok")
            return 1
    except Exception as e:
        log(f"Gateway unreachable: {e}. Start gateway on 18800.")
        return 1
    log("   Gateway OK")

    log("2. Ensure unread mail to lead-4b36c480...")
    ensure_mail_to_missing_lead()

    log("3. Run reinstate missing leads...")
    try:
        r = http_post(f"{GATEWAY_URL}/api/debug/reinstate-missing-leads-run", timeout=130)
        reinstated = r.get("reinstated", 0)
        if r.get("error"):
            log(f"   Reinstate returned: {r.get('error')} (reinstated={reinstated})")
        else:
            log(f"   Reinstated {reinstated} lead(s)")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log("   Reinstate endpoint 404 — restart gateway to load new route, then re-run this test.")
        else:
            log(f"   Reinstate failed: {e}")
        if e.code == 404:
            return 1
    except Exception as e:
        log(f"   Reinstate failed: {e}")
        return 1

    log("4. Run approval supervisor once...")
    try:
        a = http_post(f"{GATEWAY_URL}/api/debug/approval-supervisor-run", timeout=60)
        approved = a.get("approved", 0)
        log(f"   Approved {approved} agent(s)")
    except Exception as e:
        log(f"   Approval run failed: {e}")

    log("5. Status check...")
    try:
        status = http_get(f"{GATEWAY_URL}/api/status", timeout=10)
        agents = status.get("agents") or []
        names = [a.get("agentName") or a.get("name") for a in agents if a.get("agentName") or a.get("name")]
        if "lead-4b36c480" in names:
            log("   lead-4b36c480 is now in status (reinstate worked)")
        else:
            log("   lead-4b36c480 not in status (may need overstory status --json; or reinstate spawns async)")
    except Exception as e:
        log(f"   Status failed: {e}")

    log("Done. Check gateway logs for 'Reinstated missing lead' and agent list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
