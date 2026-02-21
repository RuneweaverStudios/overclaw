# .mulch/

This directory is managed by [mulch](https://github.com/jayminwest/mulch) — a structured expertise layer for coding agents. [Overstory](https://github.com/jayminwest/overstory) uses mulch from the project root only (see [overstory mulch client](https://github.com/jayminwest/overstory/blob/main/src/mulch/client.ts) and [prime command](https://github.com/jayminwest/overstory/blob/main/src/commands/prime.ts)); worktrees do not have a `.mulch` directory.

## Key Commands

- `mulch init`      — Initialize a .mulch directory
- `mulch add`       — Add a new domain
- `mulch record`    — Record an expertise record
- `mulch edit`      — Edit an existing record
- `mulch query`     — Query expertise records
- `mulch prime [domain]` — Output a priming prompt (optionally scoped to one domain)
- `mulch search`   — Search records across domains
- `mulch learn`    — Learn from session (changed files, suggest domains to record)
- `mulch status`    — Show domain statistics
- `mulch validate`  — Validate all records against the schema
- `mulch prune`     — Remove expired records

## Structure

- `mulch.config.yaml` — Configuration file
- `expertise/`        — JSONL files, one per domain
