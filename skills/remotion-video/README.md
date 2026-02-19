# Remotion Video — OpenClaw Skill

Create and render **programmatic videos** with [Remotion](https://www.remotion.dev) (React → MP4/PNG). This skill lets OpenClaw agents scaffold projects, run Remotion Studio, and render locally or via AWS Lambda.

## Quick start

1. **Prerequisites:** Node 16+ (or Bun 1.0.3+).
2. **Scaffold a project** (optional):
   ```bash
   npx create-video@latest
   ```
   Pick a template (e.g. Hello World), then `cd` into the new folder.
3. **Configure the skill:** Set `projectPath` in `config.json` to your Remotion project root (absolute or relative to workspace).
4. **Preview:** In project root, run `npm run dev` to open Remotion Studio.
5. **Render:** From project root:
   ```bash
   npx remotion render src/index.tsx MyComposition out.mp4
   npx remotion still src/index.tsx MyComposition frame.png
   ```

## What’s in this skill

- **SKILL.md** — Full description, research summary, commands, config, and references.
- **config.json** — `projectPath`, optional `nodePath` and `lambda` settings.
- **scripts/** — Optional helpers for agents (e.g. render with config-driven paths).

## Links

- [Remotion](https://www.remotion.dev)
- [Docs](https://www.remotion.dev/docs)
- [Lambda](https://www.remotion.dev/docs/lambda)
- [Templates](https://www.remotion.dev/templates)
