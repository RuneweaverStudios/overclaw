---
name: remotion-video
displayName: Remotion Video
description: Create and render programmatic videos with Remotion (React-based MP4/PNG). Scaffold projects, run Studio, render locally or via Lambda.
version: 1.0.0
---

# Remotion Video

## Description

Integrates OpenClaw with [Remotion](https://www.remotion.dev): create **real MP4 videos (and stills) programmatically with React**. Parametrize content, render locally or serverless (AWS Lambda), and build custom video apps or automation pipelines.

## Deep research summary (Remotion)

- **What it is:** A framework for making videos with code. You build React components; Remotion turns them into MP4 (or PNG/JPEG stills). Think "React for video": compositions, sequences, props, and standard React tooling.
- **Core concepts:**
  - **Composition:** The root unit of a renderable video. You register it with `<Composition id="..." component={MyComp} durationInFrames={...} width={...} height={...} fps={30} />`. Each composition has an `id`, dimensions, FPS, duration in frames, and optional `defaultProps` (JSON-serializable).
  - **Sequence / Still:** Compose clips in time with `<Sequence>`, or render a single frame with `<Still>`.
  - **Remotion Studio:** Dev UI (`npm run dev`) to preview, scrub, and tweak props.
  - **Player:** Embed Remotion in any React app and drive it at runtime (no render to file).
- **Rendering:**
  - **Local/SSR:** `npx remotion render <entry|serve-url> <composition-id> <output.mp4>` and `npx remotion still ...` for stills. Or use Node APIs: `renderMedia()`, `renderStill()` from `@remotion/renderer`.
  - **Lambda:** Deploy project to S3, run distributed render on AWS Lambda (massive concurrency; ~80s video in ~15s, 2h video in ~12min). Cost on the order of cents per render; videos &lt;~80min at Full HD due to Lambda timeout/storage. CLI: `npx remotion lambda`; Node API: `@remotion/lambda`.
- **Scaffolding:** `npx create-video@latest` — choose template (Hello World, Next.js, React Router, etc.). Requires Node 16+ or Bun 1.0.3+.
- **License:** Free for individuals and teams ≤3; [Company/Enterprise](https://www.remotion.dev) for 4+ people or commercial automation (e.g. Remotion for Automators, Editor Starter).
- **Use cases:** Music visualization, captions, screencasts, year-in-review, custom video editors, data-driven explainers, automated social clips.

## Usage

This skill lets OpenClaw agents:

- **Scaffold** a Remotion project: run `npx create-video@latest` in a target directory (or use existing project path from `config.json`).
- **Run Remotion Studio:** `npm run dev` (or equivalent) in the project root for interactive preview.
- **Render video:** `npx remotion render <entry> <composition-id> <output.mp4>` with optional `--props='{"key":"value"}'`, `--codec`, `--quality`, etc.
- **Render still:** `npx remotion still <entry> <composition-id> <output.png>` with optional `--frame`, `--image-format`, `--props`.
- **Lambda (optional):** If AWS and `@remotion/lambda` are configured, delegate batch or high-throughput renders to Lambda via CLI or Node API.

Agents should read this SKILL.md and `config.json` to know the project path and any Lambda/S3 settings before running commands.

## Commands

| Command | Purpose |
|--------|--------|
| `npx create-video@latest` | Scaffold new Remotion project (interactive). |
| `npm run dev` | Start Remotion Studio (in project root). |
| `npx remotion render <entry> <composition-id> <out.mp4>` | Render video to MP4 (or other codec). |
| `npx remotion still <entry> <composition-id> <out.png>` | Render single frame to image. |
| `npx remotion lambda ...` | Lambda deploy/render (if `@remotion/lambda` installed). |

Use `--help` on any `npx remotion` command for flags (e.g. `--props`, `--codec`, `--frame`).

**Skill script (optional):** From the skill root, `scripts/remotion_render.py` reads `config.json` and runs render/still from the configured `projectPath`:

- `python3 scripts/remotion_render.py status` — show resolved project path and config.
- `python3 scripts/remotion_render.py render <entry> <composition-id> <output.mp4> [--props '{}']`
- `python3 scripts/remotion_render.py still <entry> <composition-id> <output.png> [--frame 0] [--props '{}']`

## Purpose

Remotion Video connects OpenClaw’s automation and agent workflows to Remotion’s programmatic video stack: generate or parameterize videos from data, scripts, or user requests; render locally for quick iterations or via Lambda for scale; and optionally embed the Player in apps for live preview without rendering to file.

## Prerequisites

1. **Node.js 16+** or **Bun 1.0.3+** on the machine (or in the path used by the skill).
2. **Remotion project:** Either created with `npx create-video@latest` or an existing repo that uses `remotion`, `@remotion/bundler`, `@remotion/renderer`, etc.
3. **Optional – Lambda:** AWS account, credentials, and `@remotion/lambda` in the project for cloud rendering. See [Remotion Lambda](https://www.remotion.dev/docs/lambda).

## Configuration (`config.json`)

- **projectPath:** Absolute or workspace-relative path to the Remotion project root (where `package.json` and Remotion entry live). Used by scripts to run `npm run dev`, `npx remotion render`, etc.
- **nodePath:** (Optional) Path to `node` (or `bun`) if not on PATH.
- **lambda:** (Optional) Object with `enabled`, `region`, `s3Bucket`, etc., for Lambda-based renders (see Remotion Lambda docs).

## Tools needed

| Tool | Purpose | Required |
|------|---------|----------|
| **Node 16+** or **Bun** | Run Remotion and npm/npx | Yes |
| **npm / npx** | Install deps, run Studio, run remotion CLI | Yes |
| **Remotion project** | Compositions and entry point | Yes |
| **AWS (Lambda)** | Distributed cloud render | Optional |

## Directory structure

```
remotion-video/
├── SKILL.md          # This file
├── _meta.json        # Skill metadata
├── config.json       # projectPath, optional nodePath / lambda
├── README.md         # Quick start and links
└── scripts/          # (Optional) helpers for scaffold/render from OpenClaw
```

## Keywords

- **remotion**, **video**, **programmatic video**, **react video**
- **render**, **mp4**, **composition**, **still**
- **Remotion Studio**, **Remotion Player**, **Remotion Lambda**
- **create-video**, **npx remotion render**, **npx remotion still**

## References

- [Remotion – Make videos programmatically](https://www.remotion.dev)
- [Remotion Docs](https://www.remotion.dev/docs)
- [Composition](https://www.remotion.dev/docs/composition)
- [Player](https://www.remotion.dev/docs/player)
- [Lambda](https://www.remotion.dev/docs/lambda)
- [CLI render](https://www.remotion.dev/docs/cli/render) / [CLI still](https://www.remotion.dev/docs/cli/still)
