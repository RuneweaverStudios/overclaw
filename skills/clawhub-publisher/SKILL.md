---
name: clawhub-publisher
displayName: ClawHub Publisher
description: Syncs OpenClaw skills to ClawHub. Scans local skills that are developed by the user (GitHub remote matches configured org), checks existing versions on ClawHub, and publishes first or latest version with rate-limit delays. Use for daily cron or manual sync.
version: 1.0.0
---

# ClawHub Publisher

Publishes your OpenClaw skills to ClawHub. Only considers skills that have a git repo and a remote pointing to your GitHub (e.g. RuneweaverStudios). For each such skill, checks if it already exists on ClawHub and the current version; uploads the first version for new skills or the latest (bumped) version for updates. Uses delays between uploads to avoid rate limits and writes a completion report.

## When to use

- Run daily via cron to keep ClawHub in sync with your GitHub skill repos
- Run manually after pushing skill changes to publish to ClawHub
- Ensure only your own skills (matching your GitHub org/user) are published

## Commands

```bash
# Default: scan OPENCLAW_HOME/workspace/skills, use delay 15s, GitHub org from config
python3 scripts/clawhub_sync.py

# Custom skills dir and delay
python3 scripts/clawhub_sync.py --skills-dir /path/to/skills --delay 18

# Dry run (no publish)
python3 scripts/clawhub_sync.py --dry-run

# JSON report only
python3 scripts/clawhub_sync.py --json
```

## Requirements

- `clawhub` CLI on PATH, authenticated (`clawhub login`)
- OpenClaw workspace with skills in `workspace/skills` (or `--skills-dir`)
- Optional: `CLAWHUB_PUBLISHER_GITHUB_ORG` or config key for GitHub org/user to filter "my" skills (default: RuneweaverStudios)
- Optional: `OPENCLAW_HOME` (default: `~/.openclaw`)

## Behavior

1. Scans skill directories (have `.git` and `SKILL.md` or `_meta.json`).
2. Keeps only skills whose git `origin` URL contains the configured GitHub org (e.g. `github.com/RuneweaverStudios/...`).
3. For each skill: reads slug (folder name or `_meta.json` name) and local version from `_meta.json` or SKILL.md frontmatter.
4. Queries ClawHub (`clawhub inspect <slug> --versions --json`). If skill not found or error, treats as new → publish with local version. If found, compares latest ClawHub version with local; if local is newer or different, publishes with patch-bumped version (or first version for new).
5. Waits `--delay` seconds between each publish to avoid rate limits.
6. Writes log to `OPENCLAW_HOME/logs/clawhub-publisher.log` and a summary to `OPENCLAW_HOME/logs/clawhub-publisher-last.json`.

## Cron

Add a daily job (e.g. 5 AM) to run the sync script with `--json` and optional `--best-effort` so one failure does not mark the whole run failed.
