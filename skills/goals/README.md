# Goals | OpenClaw Skill

Generates a **morning goals and action plan** from your Notes (scribe output), with goals split into **personal / professional** and **short-term / long-term**, plus a daily motivation quote.

## Quick start

```bash
python3 workspace/skills/goals/scripts/goals.py
```

Output: `workspace/Notes/goals/YYYY-MM-DD_morning.md`

## What it uses

- **Notes folder** (from [scribe](https://github.com/Org/scribe)): `workspace/Notes/daily/`, `workspace/Notes/weekly/`
- **Memory** (optional): `workspace/MEMORY.md`, `workspace/memory/*.md`

## What you get

1. **Summary** — How many goals, personal vs professional, short vs long-term.
2. **Goals by category** — Professional list, personal list.
3. **Short-term goals** — This week / today.
4. **Long-term goals** — Month / quarter / year.
5. **Today's action plan** — Suggested next steps.
6. **Motivation & inspiration** — One quote (rotated daily).

## Run every morning

Example cron (7:00 AM):

```bash
0 7 * * * python3 /Users/ghost/.openclaw/workspace/skills/goals/scripts/goals.py
```

## Options

- `--openclaw-home /path` — OpenClaw home (default: `~/.openclaw`).
- `--json` — Print JSON (file path, counts, action plan, motivation).

## Dependencies

- Notes from the **scribe** skill (or similar markdown in `Notes/daily` and `Notes/weekly`).
- Python 3.7+.
