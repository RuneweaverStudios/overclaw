# Integrating Three.js with Cursor: A Complete Guide

> How I used a custom reference doc + Cursor's AI to go from zero to a working Three.js game prototype without memorizing a single API.

---

## The Problem

Three.js is massive. The docs are spread across examples, GitHub issues, blog posts, and Stack Overflow threads from 2014 that reference deprecated APIs. Every time I needed to do something — load a sprite sheet, set up chunked terrain, wire up pathfinding — I was context-switching between a dozen tabs, copy-pasting snippets that didn't work together, and fighting version mismatches.

I needed a single source of truth that Cursor's agent could reference directly. Something opinionated, versioned, and laser-focused on the exact patterns I was building with.

## The Approach

1. Build a comprehensive Three.js reference doc tailored to my project's needs
2. Drop it into the repo so Cursor always has access
3. Use Cursor's AI to implement features by pointing it at the reference
4. Iterate the reference as I learn what works and what doesn't

---

## Step 1: Write the Reference Doc

The reference lives at `docs/threejs-reference.md`. It's ~1,100 lines covering everything from asset preparation to full starter templates. The key decision was making it **prescriptive, not encyclopedic** — it doesn't explain every Three.js option, it tells you exactly what to use and why.

### What's in the reference

| Section | What it covers |
|---|---|
| Philosophy & Core Rules | Asset pack architecture, cache-first loading, mandatory disposal |
| Asset Preparation | Draco/MeshOpt/KTX2 compression pipeline |
| Folder Structure & Manifests | Convention for `manifest.json` per pack — animations, tile sizes, metadata |
| Loading (Vanilla + R3F) | `AssetPackManager` class, drei hooks, `Suspense` patterns |
| World Design & Size Ratios | `PIXELS_PER_UNIT = 32`, global scale constant, ortho camera setup |
| Sprite Animation & FSM | `SpriteEntity` class with state machine, frame-by-frame UV offset |
| Procedural Chunked Terrain | `WorldChunkManager` using `InstancedMesh`, deterministic hash noise |
| Collision & A\* Pathfinding | Slope-based collision, grid-based A\* that queries the noise function |
| Physics (Rapier) | Kinematic rigid bodies for sprite characters |
| AI (Yuka) | Steering behaviors, wander/pursue, syncing Yuka to Rapier |
| BVH Raycasting | `three-mesh-bvh` for pixel-perfect click-to-move |
| Free CC0 Assets | Kenney.nl, OpenGameArt LPC, itch.io sources |
| Performance & Memory | InstancedMesh rules, disposal patterns, memory budgets |
| Full Starters | Complete vanilla HTML + complete R3F TypeScript scaffolds |

### Why this structure works with Cursor

Each section is self-contained with runnable code. When I ask Cursor to "add pathfinding to the player," it finds the `PathFinder` class, the `MAX_CLIMB` constant, the `onClick` handler that wires it all together, and the performance notes — all in one place. No ambiguity about which version of Three.js, which import style, or which patterns to follow.

---

## Step 2: Project Setup

### Placing the reference

```
/docs/threejs-reference.md    ← the single source of truth
```

That's it. Because Cursor indexes your workspace, the agent can read this file whenever it needs Three.js context. No special configuration. No `.cursorrules` incantation. Just a well-written markdown file in the repo.

### Stack decisions (locked in the reference header)

```
Three.js r183+, R3F 9, drei 10, Rapier 2, Yuka, three-mesh-bvh, Vite 6, TypeScript
```

Pinning versions in the reference prevents the AI from hallucinating APIs from older Three.js versions (a constant problem otherwise — Three.js has broken backward compatibility many times).

---

## Step 3: Using Cursor with the Reference

### Pattern: "Read the reference, then implement"

The most effective workflow is explicit about what you want Cursor to reference. Instead of vague prompts, point directly at the doc.

**Prompt examples that work well:**

```
Using @docs/threejs-reference.md, implement the WorldChunkManager
from section 8 with biome coloring.
```

```
Reference @docs/threejs-reference.md section 7 — create a SpriteEntity
class that handles idle and walk animations from a sprite sheet.
```

```
Following the asset pack pattern in @docs/threejs-reference.md,
set up loading for a Kenney character sprite sheet with proper
NearestFilter and UV repeat.
```

The `@docs/threejs-reference.md` syntax tells Cursor to read and include that file's content in its context window. Without it, you're relying on the agent's training data (which may be outdated or wrong for Three.js specifics).

### Pattern: "Compose systems from reference sections"

The reference sections are designed to compose. Once the individual systems exist, you can ask Cursor to wire them together:

```
Using @docs/threejs-reference.md, connect the PathFinder (section 9)
to click-to-move. When the user clicks the ground, raycast to find
the position, run A* pathfinding, and animate the hero sprite along
the path. Use the slope collision from the same section.
```

Because the reference defines shared constants (`TILE_SIZE`, `MAX_CLIMB`, `PIXELS_PER_UNIT`) and compatible interfaces, the AI can compose systems that actually work together on the first try.

### Pattern: "Debug with the pitfalls table"

Section 16 of the reference is a pitfalls table. When something goes wrong, reference it directly:

```
My sprites are z-fighting. Check @docs/threejs-reference.md
section 16 for the fix and apply it.
```

The AI finds `material.depthTest = false` immediately instead of going down a rabbit hole of `polygonOffset` hacks.

---

## Step 4: What the Reference Enables

### Manifest-driven asset loading

The reference defines a universal `manifest.json` format. Every asset pack — characters, environments, tilesets — uses the same schema:

```json
{
  "name": "Hero Character Pack",
  "type": "character",
  "sheet": "sprite-sheet.png",
  "cols": 8,
  "rows": 8,
  "tileSize": 32,
  "animations": {
    "idle":   { "start": 0,  "len": 4,  "fps": 8,  "loop": true },
    "walk":   { "start": 8,  "len": 6,  "fps": 12, "loop": true },
    "attack": { "start": 16, "len": 5,  "fps": 15, "loop": false }
  }
}
```

When Cursor implements a new character, it follows this schema because the reference says to. No drift. No one-off formats.

### Deterministic procedural world

The hash noise function in the reference is pure — same input always produces the same output. This means:

- Chunks can be loaded/unloaded freely without state
- A\* pathfinding works on infinite terrain by querying the same noise function
- No seed management or world-save files needed for prototyping

### Memory safety by convention

The reference embeds disposal patterns directly into every class. `AssetPackManager.unloadPack()`, `SpriteEntity.dispose()`, `WorldChunkManager.unloadChunk()` — they're not afterthoughts, they're part of the core API. When Cursor generates code following the reference, disposal is baked in.

---

## Step 5: Iterating the Reference

The reference isn't static. As I build, I update it:

- **New pitfall discovered?** Add it to section 16
- **Found a better CC0 asset source?** Update section 13
- **Performance lesson learned?** Add it to section 15
- **New system needed (e.g., particle effects)?** Add a section

The reference grows with the project. Cursor always sees the latest version because it reads from disk, not from memory.

### Version header

The reference has a version header:

```
> **Version:** 1.0 — February 2026
> **Scope:** Vanilla Three.js & React Three Fiber (R3F)
> **Stack:** Three.js r183+, R3F 9, drei 10, Rapier 2, Yuka, three-mesh-bvh, Vite 6, TypeScript
```

Bump the version when you make significant changes. This helps track what the AI was working with if you need to debug a session later.

---

## Lessons Learned

### What worked

- **One file > many bookmarks.** Having everything in a single, opinionated reference eliminated the tab-switching tax entirely. Cursor reads one file and has full context.

- **Prescriptive > descriptive.** The reference doesn't say "here are 5 ways to load textures." It says "use `NearestFilter`, set `repeat` from the manifest, check cache first." Cursor follows instructions better than it navigates choices.

- **Runnable code in every section.** Every code block in the reference is copy-paste ready. The AI doesn't have to infer missing imports or guess at constructor signatures.

- **Shared constants create composability.** `PIXELS_PER_UNIT`, `TILE_SIZE`, `CHUNK_SIZE`, `MAX_CLIMB` — these constants appear across sections and create a shared vocabulary. When Cursor implements section 9 (pathfinding), it naturally uses the same `TILE_SIZE` from section 8 (terrain) because the reference is internally consistent.

- **The full starters are gold.** Sections 17 and 18 are complete, working applications. When all else fails, "start from the vanilla starter in section 17" gives Cursor an unambiguous starting point.

### What didn't work (at first)

- **Too much abstraction.** Early versions of the reference described patterns without concrete code. Cursor would generate plausible-looking but broken implementations. Adding complete, tested code blocks fixed this.

- **Missing the "why."** Without the philosophy section (section 1), Cursor would sometimes optimize for cleverness over the conventions I wanted. "Never load the same asset twice" and "never forget to dispose" are rules that need to be stated explicitly.

- **Not pinning versions.** Before the stack was pinned in the header, Cursor would occasionally use `THREE.Geometry` (removed in r125) or old drei imports. The version line solved this.

---

## Quick Reference: Cursor Prompts That Work

| Goal | Prompt |
|---|---|
| Scaffold a new project | "Using @docs/threejs-reference.md section 17, create the full vanilla starter" |
| Add a character | "Following @docs/threejs-reference.md, create a SpriteEntity from a Kenney sprite sheet with idle and walk animations" |
| Build terrain | "Implement the WorldChunkManager from @docs/threejs-reference.md section 8 with biome coloring" |
| Add pathfinding | "Wire up A\* click-to-move from @docs/threejs-reference.md section 9 with slope collision" |
| Add physics | "Add Rapier kinematic bodies from @docs/threejs-reference.md section 10 to the player sprite" |
| Add NPC AI | "Set up Yuka wander + pursue behaviors from @docs/threejs-reference.md section 11, synced to Rapier" |
| Fix a bug | "Check @docs/threejs-reference.md section 16 for [problem description]" |
| Optimize performance | "Review against @docs/threejs-reference.md section 15 performance rules and apply fixes" |
| Set up R3F | "Scaffold the R3F TypeScript starter from @docs/threejs-reference.md section 18" |

---

## The Meta-Lesson

The reference doc isn't really about Three.js. It's about **giving AI a well-structured, opinionated codebase to work from.** The same approach works for any complex library:

1. Write down exactly how you want things done
2. Include runnable code, not just descriptions
3. Pin versions, name constants, define interfaces
4. Put it where the AI can read it
5. Reference it explicitly in your prompts

The AI is only as good as the context you give it. A 1,100-line reference doc is a small investment compared to the hours you'd spend correcting hallucinated APIs and debugging version mismatches.

---

## Addendum: Lessons from the Tiny Wonder Farm Integration

After building two themes (Sunnyside World, Zombie Survival), we added a third — Tiny Wonder Farm — and learned several things worth documenting.

### Asset Packs Vary Wildly in Structure

Sunnyside World had cleanly separated strips per animation, per character, per direction. Tiny Wonder Farm crammed everything into a handful of sheets:

| Pack | Files | Organization |
|---|---|---|
| Sunnyside World | 120+ files | One strip per animation per hair style |
| PostApocalypse | 11 files | One strip per animation |
| Tiny Wonder Farm | 13 files | Everything in grid sheets and irregular packed PNGs |

The lesson: **your loading code needs to handle all three patterns** — horizontal strips, uniform grids, and arbitrary pixel-region extraction. The reference doc now covers all three (sections 4, 19).

### Python as a Build Tool for Pixel Art

The most surprising win was using Python + PIL as a **tile analysis and background composition tool**. Instead of trying to render 1,792 individual tiles at runtime:

1. Analyze the tileset programmatically to classify tiles by color and uniformity
2. Compose a single background PNG matching the scene layout
3. Apply as one texture on one plane — one draw call

This pattern scaled to three different tilesets with zero runtime cost. The variance analysis trick (section 20 of the reference) was key — it finds the cleanest "fill" tiles automatically regardless of how the tileset is organized.

### The Theme Interface Pattern

With three themes, the abstraction became clear:

```
preload() → initScene() → updateAgents() / tickAmbient() → dispose()
```

Each theme is a plain JS object with these five methods. The shared engine handles scene management, camera, agent sprites, mail flights, and bubbles. Themes only handle their own visual flavor. This made adding Tiny Wonder Farm straightforward — copy the interface, swap the textures and positions.

### What Cursor Needed to Build the Third Theme

The prompt was essentially:

```
Build a 3rd Three.js CEO world using the Tiny Wonder Farm asset pack.
Analyze and use ALL included assets intuitively.
```

With the reference doc updated (sections 19-21), Cursor could:
- Use `regionSprite` for the irregularly-packed farm objects sheet
- Use `gridFrame` for the uniform 5x6 plants grid and 5x3 items/furniture grids
- Use the pre-composed background pipeline for the spring farm tilemap
- Follow the theme interface pattern from sections 19-21

No additional context needed. The reference doc was sufficient to produce the complete theme.

### Asset Management: Keep It in the Repo

We learned to copy only the used assets into `assets/packs/` and update Flask routes to use repo-relative paths. The total cost:

| Pack | Files | Size |
|---|---|---|
| Sunnyside | 121 | ~300 KB |
| PostApocalypse | 11 | ~30 KB |
| Tiny Wonder Farm | 13 + 1 composed BG | ~50 KB |
| **Total** | **146** | **~680 KB** |

Git-friendly. No dependency on the Downloads folder. No hardcoded absolute paths.

### Merged Farm Theme & Credits

The **Sunnyside World** theme was removed; its assets are used only inside **Tiny Wonder Farm**. Tiny Farm loads from both `/static/tf/` (Tiny Wonder Farm) and `/static/sw/` (Sunnyside). Credits in the UI: *Tiny Wonder Farm by Butterymilk; characters & elements by Daniel Diggle (Sunnyside World)* so both creators are credited for the merged pack.

### Agent Dialog & Zombie Props

- **Agent dialog:** Click an agent in the CEO view → HTML overlay at the bottom with enlarged portrait and typewriter text. Implemented with `THREE.Raycaster`, a single overlay div, and `lastPolledAgents` for speech text (see reference section 22).
- **Zombie Apocalypse** was given more set dressing: ruined block buildings, broken walls, and crate clusters so the scene feels less empty (reference section 21; theme still uses the same interface).

### Character Sprite Fallback

If Sunnyside character strips fail to load (or `texture.image` isn’t ready), the Tiny Farm theme falls back to the TF `walk_idle.png` grid so agent sprites are never missing. Combined with defensive `scaleSpriteFromStrip` (reference section 24), character sprites stay visible.

---

*Reference doc: `docs/threejs-reference.md` (v1.1, February 2026)*
*Built with: Cursor, Three.js r149, Python + PIL, vibes*
