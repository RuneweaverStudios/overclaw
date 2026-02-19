# HEARTBEAT.md

# Keep this file empty (or with only comments) to skip heartbeat API calls.

# Add tasks below when you want the agent to check something periodically.

## Gateway restart check
If the gateway may have restarted recently (e.g. user just reconnected TUI), run what-just-happened and announce: `python3 /Users/ghost/.openclaw/workspace/skills/what-just-happened/scripts/report_recent_logs.py --minutes 5`. If it reports a restart or errors, summarize and propose a solution (e.g. gateway-guard) to the user.
