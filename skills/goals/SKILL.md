---
name: goals
displayName: Goals | OpenClaw Skill
description: Determines user goals from the Notes folder (scribe), categorizes into personal/professional and short/long-term, and produces a morning action plan with motivation and inspiration.
version: 1.0.0
---

# Goals | OpenClaw Skill

Uses the **Notes** folder (from the **scribe** skill) to infer your goals, split them into **personal** vs **professional** and **short-term** vs **long-term**, and writes a **morning brief** with an action plan and daily motivation.

## Description

- **Input:** `workspace/Notes/daily/`, `workspace/Notes/weekly/` (scribe output), plus `workspace/memory/` and `MEMORY.md` when present.
- **Output:** One markdown file per day in `workspace/Notes/goals/` named `YYYY-MM-DD_morning.md`.
- **Contents:** Summary at the top, then:
  - Goals by category (professional / personal)
  - Short-term and long-term goals
  - Today’s action plan (suggested next steps)
  - Motivation & inspiration (rotating quote)

Designed to be run **every morning** (e.g. via cron or OpenClaw scheduled job).

## Installation

```bash
clawhub install goals
```

Or clone into your skills directory:

```bash
git clone https://github.com/Org/goals.git workspace/skills/goals
```

## Usage

Run once (e.g. from workspace root or OpenClaw home):

```bash
python3 workspace/skills/goals/scripts/goals.py
```

With custom OpenClaw home:

```bash
python3 workspace/skills/goals/scripts/goals.py --openclaw-home /path/to/openclaw
```

JSON output (for cron or automation):

```bash
python3 workspace/skills/goals/scripts/goals.py --json
```

## Examples

**Example 1: Morning run**  
*Scenario:* You want today’s goals and action plan.  
*Action:* Run `python3 workspace/skills/goals/scripts/goals.py`.  
*Outcome:* `workspace/Notes/goals/YYYY-MM-DD_morning.md` with summary, goals (personal/professional, short/long-term), action plan, and a motivation quote.

**Example 2: Every morning via cron**  
*Scenario:* Automate the morning brief.  
*Action:* Schedule `goals.py` (e.g. 7:00 AM).  
*Outcome:* A fresh goals file each day based on latest scribe Notes and memory.

**Example 3: After scribe**  
*Scenario:* Run scribe first, then goals.  
*Action:* `scribe.py --mode daily` then `goals.py`.  
*Outcome:* Goals are derived from the latest daily (and weekly) notes.

## Commands

```bash
python3 workspace/skills/goals/scripts/goals.py                  # Generate today's morning brief
python3 workspace/skills/goals/scripts/goals.py --json          # JSON output
python3 workspace/skills/goals/scripts/goals.py --openclaw-home /path  # Custom home
```

- **--openclaw-home** — OpenClaw home directory (default: `~/.openclaw`).
- **--json** — Print result as JSON (file path, counts, action plan, motivation).

## What this skill does

1. **Reads Notes** — Scans `workspace/Notes/daily/` and `workspace/Notes/weekly/` (from scribe).
2. **Reads memory** — Uses `workspace/MEMORY.md` and `workspace/memory/*.md` when present.
3. **Extracts goals** — Headings, bullets, and goal/todo/want/need-style phrases.
4. **Classifies** — Personal vs professional (keywords); short-term vs long-term (time cues).
5. **Action plan** — Suggests a small set of next actions (short-term professional first, then personal/long-term).
6. **Motivation** — Picks a daily quote (rotated by day of year).
7. **Writes brief** — One file per day in `workspace/Notes/goals/YYYY-MM-DD_morning.md` with a **summary at the top**.

## Output format

Each morning file has:

- **Summary** — Counts (goals, personal/professional, short/long-term).
- **Goals by category** — Professional list, then personal list.
- **Short-term goals** — For this week / today.
- **Long-term goals** — For month/quarter/year.
- **Today's action plan** — Numbered list of suggested actions.
- **Motivation & inspiration** — One quote.

## Requirements

- Python 3.7+
- Notes produced by the **scribe** skill (or similar markdown in `Notes/daily` and `Notes/weekly`).
- Write access to `workspace/Notes/goals/`.

## Run every morning

**Cron (example):**

```bash
# 7:00 AM daily
0 7 * * * python3 /Users/ghost/.openclaw/workspace/skills/goals/scripts/goals.py >> /Users/ghost/.openclaw/logs/goals.log 2>&1
```

**OpenClaw cron job (example):**

```json
{
  "payload": {
    "kind": "agentTurn",
    "message": "Run goals.py to generate this morning's goals and action plan from Notes.",
    "model": "openrouter/google/gemini-2.5-flash",
    "thinking": "low",
    "timeoutSeconds": 60
  },
  "schedule": {
    "kind": "cron",
    "cron": "0 7 * * *"
  },
  "delivery": { "mode": "announce" },
  "sessionTarget": "isolated",
  "name": "Morning Goals"
}
```

## Security & privacy

- **Reads:** `workspace/Notes/`, `workspace/memory/`, `workspace/MEMORY.md`.
- **Writes:** Only `workspace/Notes/goals/`.
- All processing is local; no data is sent externally.
