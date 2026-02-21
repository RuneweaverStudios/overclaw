#!/usr/bin/env python3
"""Test agent mail: DB write + overstory mail send (--subject, --body). Run from workspace root."""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MAIL_DB = WORKSPACE / ".overstory" / "mail.db"
OVERSTORY_BIN = os.environ.get("OVERSTORY_BIN", os.path.expanduser("~/.bun/bin/overstory"))


def write_mail_to_db(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> None:
    MAIL_DB.parent.mkdir(parents=True, exist_ok=True)
    subject = (message.strip().split("\n")[0][:200]) if message.strip() else "No subject"
    body = (message or "").strip() or "(no body)"
    import time
    now = time.time()
    conn = sqlite3.connect(str(MAIL_DB), timeout=5)
    conn.execute(
        "INSERT INTO messages (from_agent, to_agent, subject, body, priority, created_at) VALUES (?,?,?,?,?,?)",
        (from_agent, to_agent, subject, body, priority, now),
    )
    conn.commit()
    conn.close()
    print("  DB: wrote to", MAIL_DB)


async def overstory_mail_send(from_agent: str, to_agent: str, subject: str, body: str, priority: str = "normal") -> dict:
    proc = await asyncio.create_subprocess_exec(
        OVERSTORY_BIN, "mail", "send",
        "--from", from_agent,
        "--to", to_agent,
        "--subject", subject[:200],
        "--body", body[:2000],
        "--priority", priority,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORKSPACE),
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode().strip()
    err = stderr.decode().strip()
    if proc.returncode != 0:
        return {"error": err or out, "exit_code": proc.returncode}
    return {"ok": True, "raw": out}


async def main():
    to = "lead-test"
    msg = "Test from test_agent_mail.py: mail with subject and body."
    print("Sending mail to", to, "...")
    write_mail_to_db("orchestrator", to, msg, "normal")
    subject = (msg.strip().split("\n")[0][:200]) if msg.strip() else "No subject"
    body = msg.strip()[:2000] or "(no body)"
    result = await overstory_mail_send("orchestrator", to, subject, body, "normal")
    if result.get("error"):
        print("  overstory mail send failed:", result.get("error"))
        sys.exit(1)
    print("  overstory: sent")
    print("OK: mail flow works. Check .overstory/mail.db and dashboard Mail panel.")


if __name__ == "__main__":
    asyncio.run(main())
