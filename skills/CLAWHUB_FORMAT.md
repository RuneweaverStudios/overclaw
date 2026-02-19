# ClawHub format readiness

Skills in this workspace are normalized for **ClawHub** so they can be published and discovered. Use the official helper when creating or editing skills:

**→ [Create Agent Skills | ClawHub](https://clawhub.ai/bowen31337/create-agent-skills)**

## Required for ClawHub

### 1. `_meta.json` (schema openclaw.skill.v1)

Each skill should have `_meta.json` with:

| Field | Required | Notes |
|-------|----------|--------|
| `schema` | Yes | `"openclaw.skill.v1"` |
| `name` | Yes | Slug (lowercase, hyphenated), e.g. `subagent-tracker` |
| `version` | Yes | Semver, e.g. `1.0.0` |
| `description` | Yes | One-sentence; when to use |
| `platform` | Yes | `"openclaw"` |
| `author` | Recommended | e.g. OpenClaw, Austin |
| `tags` | Recommended | Array of strings for discovery |
| `displayName` | Optional | Human-readable name |

Optional: `icon`, `color`, `dependencies`, `skills` (depends on skill).

### 2. `SKILL.md` (ClawHub-optimized structure)

- **Frontmatter:** `name`, `displayName`, `description`, `version` (YAML between `---`).
- **Sections:** Description, Installation, Usage, Examples, Commands (see [skill-doc-formatter](https://clawhub.ai/bowen31337/create-agent-skills) and `TEMPLATE_CLAWHUB_SKILL.md` in skill-doc-formatter).
- **Security:** If the skill reads `openclaw.json`, disclose which fields. For CLI examples with user input, warn that programmatic use must use `subprocess.run(..., [..., user_message], ...)` (no shell interpolation).

### 3. No non-text files in the repo that ClawHub rejects

Do not commit binary or git-internal files that ClawHub does not accept (e.g. remove or ignore as needed per ClawHub’s rules).

## What was done in this workspace

All skills under `workspace/skills` now have **ClawHub-format `_meta.json`**:

- **Schema:** Every skill has `"schema": "openclaw.skill.v1"`.
- **Slug name:** `name` is lowercase, hyphenated (e.g. `skill-doctor`, `cron-health-check`).
- **Platform:** `platform: "openclaw"` set where missing.
- **Tags:** Added or normalized for discovery.

Skills that already had the schema (e.g. agent-swarm, gateway-guard, FACEPALM, remotion-video, what-just-happened, clawhub-publisher, gateway-watchdog, launchagent-manager, skill-tester) were left as-is. Others were added or updated to match the table above.

## Format or validate a skill

- **Format SKILL.md for ClawHub:**  
  `python3 workspace/skills/skill-doc-formatter/scripts/format_skill_doc.py <skill-dir>/SKILL.md`

- **Security review (optional):**  
  `python3 workspace/skills/skill-doc-formatter/scripts/format_skill_doc.py <skill-dir>/SKILL.md --security-check`

- **Template:** Copy `workspace/skills/skill-doc-formatter/TEMPLATE_CLAWHUB_SKILL.md` when creating a new skill.
