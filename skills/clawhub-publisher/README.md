# ClawHub Publisher

OpenClaw skill: sync local skills (developed by you on GitHub) to ClawHub. Runs as a daily cron job or manually.

- Scans `workspace/skills` for dirs with `.git` and `SKILL.md` or `_meta.json`
- Keeps only skills whose git `origin` contains your GitHub org (e.g. RuneweaverStudios)
- For each: checks ClawHub for existing version; publishes first version (new) or latest/bumped version (update)
- Delay between publishes to avoid rate limits
- Writes log to `OPENCLAW_HOME/logs/clawhub-publisher.log` and summary to `clawhub-publisher-last.json`

## Quick start

```bash
# Config: edit config.json or set CLAWHUB_PUBLISHER_GITHUB_ORG
python3 scripts/clawhub_sync.py
# Dry run
python3 scripts/clawhub_sync.py --dry-run --json
```

## Cron

A daily job runs at 5 AM (0 5 * * *) and asks the agent to run the sync and report when complete. See OpenClaw cron list.
