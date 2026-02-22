# Three.js Reference — Asset Packs, 2D Sprites & World Systems

> **Version:** 1.0 — February 2026
> **Scope:** Vanilla Three.js & React Three Fiber (R3F) — games, roguelikes, configurators, hybrid 2.5D
> **Stack:** Three.js r183+, R3F 9, drei 10, Rapier 2, Yuka, three-mesh-bvh, Vite 6, TypeScript

---

## Table of Contents

1. [Philosophy & Core Rules](#1-philosophy--core-rules)
2. [Asset Preparation](#2-asset-preparation)
3. [Folder Structure & Manifests](#3-folder-structure--manifests)
4. [Loading — Vanilla](#4-loading--vanilla)
5. [Loading — React Three Fiber](#5-loading--react-three-fiber)
6. [World Design & Size Ratios](#6-world-design--size-ratios)
7. [Sprite Animation & FSM](#7-sprite-animation--fsm)
8. [Procedural Chunked Terrain](#8-procedural-chunked-terrain)
9. [Collision & A* Pathfinding](#9-collision--a-pathfinding)
10. [Physics — Rapier Integration](#10-physics--rapier-integration)
11. [AI — Yuka Steering Behaviors](#11-ai--yuka-steering-behaviors)
12. [BVH Accelerated Raycasting](#12-bvh-accelerated-raycasting)
13. [Free CC0 Asset Sources](#13-free-cc0-asset-sources)
14. [Project Templates & GitHub Starters](#14-project-templates--github-starters)
15. [Performance & Memory Rules](#15-performance--memory-rules)
16. [Common Pitfalls & Fixes](#16-common-pitfalls--fixes)
17. [Full Vanilla Starter (index.html)](#17-full-vanilla-starter)
18. [Full R3F TypeScript Starter](#18-full-r3f-typescript-starter)

---

## 1. Philosophy & Core Rules

Treat **asset packs** as self-contained, versioned, disposable modules.

Every pack must be:
- **Optimized** at export time (Draco, MeshOpt, KTX2)
- **Lazy-loadable** (manifest-driven, fetched on demand)
- **Explicitly disposable** (memory is the enemy in long sessions)
- **Manifest-driven** (single source of truth per pack)

**Hard rules:**
- Never load the same asset twice — always check cache first
- Never forget to dispose — call `unloadPack()` on level/character transitions
- If memory exceeds 300 MB after 5 minutes, you forgot `dispose()`

---

## 2. Asset Preparation

### Toolchain (run in CI or build step)

```bash
npx gltf-transform draco input.glb output.glb
npx gltf-transform meshopt output.glb output.glb
npx gltf-transform ktx output.glb output.glb --format ktx2
```

### Required Formats

| Asset Type   | Format                              | Notes                                   |
|--------------|-------------------------------------|-----------------------------------------|
| Models       | `.glb` (binary GLTF)               | Single file, Draco + MeshOpt compressed |
| Textures     | KTX2 + Basis (or WebP/AVIF)        | GPU-ready                               |
| Sprites      | PNG sprite sheets                   | Power-of-2 dimensions, `NearestFilter`  |
| Environments | `.hdr` (RGBE or EXR)               | For IBL / skybox                        |

### LOD Strategy

Create 3 variants per model (Low/Mid/High) inside the same `.glb` and switch with `THREE.LOD`.

---

## 3. Folder Structure & Manifests

### Directory Layout

```
/public/assets/packs/
  characters/hero/
    model.glb
    sprite-sheet.png
    manifest.json
    thumbnails/
      idle.webp
  environments/forest/
    scene.glb
    env.hdr
    tiles/
      grass.png
```

### manifest.json (Universal Format)

```json
{
  "name": "Hero Character Pack",
  "type": "character",
  "model": "model.glb",
  "sheet": "sprite-sheet.png",
  "cols": 8,
  "rows": 8,
  "tileSize": 32,
  "animations": {
    "idle":   { "start": 0,  "len": 4,  "fps": 8,  "loop": true },
    "walk":   { "start": 8,  "len": 6,  "fps": 12, "loop": true },
    "attack": { "start": 16, "len": 5,  "fps": 15, "loop": false }
  },
  "preview": "preview.webp",
  "sizeMB": 2.8
}
```

**`type` values:** `"model"` | `"sprite"` | `"environment"` | `"tileset"`

---

## 4. Loading — Vanilla

### AssetPackManager

```js
class AssetPackManager {
  constructor() {
    this.cache = new Map();
    this.packs = new Map();
    this.loadingManager = new THREE.LoadingManager();
  }

  async loadPack(packId, baseUrl) {
    if (this.packs.has(packId)) return this.packs.get(packId);
    const manifest = await (await fetch(`${baseUrl}/manifest.json`)).json();

    let resource;
    if (manifest.model) {
      const loader = new THREE.GLTFLoader(this.loadingManager);
      resource = await loader.loadAsync(`${baseUrl}/${manifest.model}`);
    } else if (manifest.sheet) {
      const texture = await new THREE.TextureLoader(this.loadingManager)
        .loadAsync(`${baseUrl}/${manifest.sheet}`);
      texture.magFilter = THREE.NearestFilter;
      texture.minFilter = THREE.NearestMipmapNearestFilter;
      texture.repeat.set(1 / manifest.cols, 1 / manifest.rows);
      resource = { texture, manifest };
    }

    const pack = { manifest, resource, baseUrl };
    this.packs.set(packId, pack);
    return pack;
  }

  unloadPack(packId) {
    const pack = this.packs.get(packId);
    if (!pack) return;

    if (pack.resource?.scene) {
      pack.resource.scene.traverse(obj => {
        if (obj.geometry) obj.geometry.dispose();
        if (obj.material) {
          if (Array.isArray(obj.material)) obj.material.forEach(m => m.dispose());
          else obj.material.dispose();
        }
      });
    }
    if (pack.resource?.texture) pack.resource.texture.dispose();
    this.packs.delete(packId);
  }
}
```

### Sprite-Only Loader (Lightweight)

```js
async loadSpritePack(packId, url, manifest) {
  if (this.packs.has(packId)) return this.packs.get(packId);
  const texture = await new THREE.TextureLoader().loadAsync(url);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestMipmapNearestFilter;
  texture.repeat.set(1 / manifest.cols, 1 / manifest.rows);
  const pack = { manifest, texture };
  this.packs.set(packId, pack);
  return pack;
}
```

---

## 5. Loading — React Three Fiber

### drei Hooks

```tsx
import { useGLTF, useTexture, Preload } from '@react-three/drei'

function Hero({ packId }: { packId: string }) {
  const { scene } = useGLTF(`/assets/packs/${packId}/model.glb`);
  const sheet = useTexture(`/assets/packs/${packId}/sprite-sheet.png`);
  return <primitive object={scene} />;
}

// Root
<Suspense fallback={<Loading />}>
  <Hero packId="hero" />
  <Preload all />
</Suspense>
```

Call `useGLTF.preload(url)` at module scope for every pack you expect to need.

---

## 6. World Design & Size Ratios

### Architecture

- **Chunked tiles:** 16×16 or 32×32 tiles per chunk
- **Layers:** Background (parallax) → Ground (InstancedMesh) → Entities (Sprites) → Foreground → UI
- **Hybrid 2.5D:** 3D terrain + billboard sprites (`material.depthWrite = false`)
- **Procedural:** `simplex-noise` or hash noise + Kenney tiles for infinite worlds
- **Culling:** Frustum checks + InstancedMesh for grass/trees

### Global Scale Constant

```js
const PIXELS_PER_UNIT = 32;
```

| Element      | Pixel Size | World Units | Notes               |
|--------------|-----------|-------------|----------------------|
| Tile         | 32×32     | 1×1         | Standard grid        |
| Player       | 32×64     | 1×2         | Human scale          |
| Tree / Prop  | 48×64     | 1.5×2       | Matches player       |
| UI Nameplate | 128×32    | Screen-space | Use CSS2DRenderer    |

### Scale Helper

```js
function scaleSprite(sprite, texture) {
  sprite.scale.set(
    texture.image.width / PIXELS_PER_UNIT,
    texture.image.height / PIXELS_PER_UNIT,
    1
  );
}
```

### Pixel-Perfect Ortho Camera

Set `OrthographicCamera` with `left = -width/2`, `right = width/2` and match `PIXELS_PER_UNIT`.

### Isometric Trick

Scale Y by `Math.cos(Math.PI / 6) ≈ 0.866`.

---

## 7. Sprite Animation & FSM

### SpriteEntity Class

```js
class SpriteEntity {
  constructor(pack) {
    this.manifest = pack.manifest;
    this.material = new THREE.SpriteMaterial({
      map: pack.texture,
      transparent: true,
      depthTest: false
    });
    this.sprite = new THREE.Sprite(this.material);
    this.state = 'idle';
    this.frame = 0;
    this.timer = 0;
    this.velocity = new THREE.Vector3();
  }

  setState(state) {
    if (this.state === state) return;
    this.state = state;
    this.frame = this.manifest.animations[state].start;
    this.timer = 0;
  }

  update(delta) {
    const anim = this.manifest.animations[this.state];
    this.timer += delta;
    if (this.timer > 1 / anim.fps) {
      this.frame = (this.frame - anim.start + 1) % anim.len + anim.start;
      this.timer = 0;

      const col = this.frame % this.manifest.cols;
      const row = Math.floor(this.frame / this.manifest.cols);
      this.material.map.offset.set(
        col / this.manifest.cols,
        1 - (row + 1) / this.manifest.rows  // WebGL Y-flip
      );
    }
  }

  dispose() {
    this.material.dispose();
    this.sprite.removeFromParent();
  }
}
```

For 500+ sprites, write a custom `InstancedSprite` shader using `InstancedMesh`.

---

## 8. Procedural Chunked Terrain

### Deterministic Fractal Noise

```js
function hashNoise(x, z) {
  let n = x * 1619 + z * 31337;
  n = (n << 13) ^ n;
  return 1.0 - ((n * (n * n * 15731 + 789221) + 1376312589) & 0x7fffffff) / 1073741824.0;
}

function getProceduralHeight(x, z) {
  let height = 0, amp = 1.8, freq = 0.028;
  for (let i = 0; i < 5; i++) {
    height += hashNoise(x * freq, z * freq) * amp;
    amp *= 0.48;
    freq *= 2.1;
  }
  return height;
}
```

### WorldChunkManager

```js
const CHUNK_SIZE = 16;
const TILE_SIZE = 2;

class WorldChunkManager {
  constructor(scene) {
    this.scene = scene;
    this.chunks = new Map();
  }

  loadChunk(cx, cz) {
    const key = `${cx},${cz}`;
    if (this.chunks.has(key)) return;

    const count = CHUNK_SIZE * CHUNK_SIZE;
    const geo = new THREE.PlaneGeometry(TILE_SIZE, TILE_SIZE);
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
    const instanced = new THREE.InstancedMesh(geo, mat, count);

    let idx = 0;
    for (let tx = 0; tx < CHUNK_SIZE; tx++) {
      for (let tz = 0; tz < CHUNK_SIZE; tz++) {
        const wx = (cx * CHUNK_SIZE + tx) * TILE_SIZE + TILE_SIZE * 0.5;
        const wz = (cz * CHUNK_SIZE + tz) * TILE_SIZE + TILE_SIZE * 0.5;
        const height = getProceduralHeight(wx, wz);

        const biome = hashNoise(wx * 0.042, wz * 0.042) * 2 - 1;
        let color = new THREE.Color(0x3a7a3a);       // grass
        if (biome < -0.25) color.setHex(0x8b5a2b);   // dirt
        else if (biome > 0.45) color.setHex(0x4488dd); // water

        const matrix = new THREE.Matrix4()
          .makeRotationX(-Math.PI / 2)
          .setPosition(wx, height, wz);

        instanced.setMatrixAt(idx, matrix);
        instanced.setColorAt(idx, color);
        idx++;
      }
    }

    instanced.instanceMatrix.needsUpdate = true;
    instanced.instanceColor.needsUpdate = true;

    this.scene.add(instanced);
    this.chunks.set(key, { instanced, cx, cz });
  }

  unloadChunk(key) {
    const c = this.chunks.get(key);
    if (!c) return;
    this.scene.remove(c.instanced);
    c.instanced.dispose();
    this.chunks.delete(key);
  }

  updateChunks(playerPos) {
    const cx = Math.floor(playerPos.x / (CHUNK_SIZE * TILE_SIZE));
    const cz = Math.floor(playerPos.z / (CHUNK_SIZE * TILE_SIZE));

    for (let dx = -2; dx <= 2; dx++)
      for (let dz = -2; dz <= 2; dz++)
        this.loadChunk(cx + dx, cz + dz);

    for (let [key] of this.chunks) {
      const [ccx, ccz] = key.split(',').map(Number);
      if (Math.abs(ccx - cx) > 3 || Math.abs(ccz - cz) > 3)
        this.unloadChunk(key);
    }
  }

  getHeightAt(x, z) {
    return getProceduralHeight(x, z);
  }
}
```

**Production tips:**
- Move noise to a Web Worker + cache chunks as JSON
- Add tree `InstancedMesh` inside `loadChunk` when `biome > 0.6`
- Chunks auto-dispose — stays under 150 MB after 10+ minutes

---

## 9. Collision & A* Pathfinding

### Slope-Based Collision

```js
const MAX_CLIMB = 1.2;

const proposed = pos.clone().add(dir.multiplyScalar(speed));
const currH = world.getHeightAt(pos.x, pos.z);
const propH = world.getHeightAt(proposed.x, proposed.z);
if (Math.abs(propH - currH) <= MAX_CLIMB) {
  pos.copy(proposed);
}
```

### Grid A* Pathfinder

8-directional, slope-aware, Manhattan heuristic. Works anywhere on the infinite terrain because it queries the same deterministic noise function.

```js
class PathFinder {
  constructor(getHeightFn) {
    this.getHeight = getHeightFn;
  }

  findPath(startX, startZ, goalX, goalZ) {
    const step = TILE_SIZE;
    const maxNodes = 1200;

    const sx = Math.round(startX / step);
    const sz = Math.round(startZ / step);
    const gx = Math.round(goalX / step);
    const gz = Math.round(goalZ / step);

    if (Math.hypot(gx - sx, gz - sz) > 60) return null;

    const openSet = new Set([`${sx},${sz}`]);
    const cameFrom = new Map();
    const gScore = new Map(); gScore.set(`${sx},${sz}`, 0);
    const fScore = new Map(); fScore.set(`${sx},${sz}`, this.heuristic(sx, sz, gx, gz));

    let nodesVisited = 0;

    while (openSet.size > 0 && nodesVisited < maxNodes) {
      let currentKey = null, bestF = Infinity;
      for (let key of openSet) {
        const f = fScore.get(key);
        if (f < bestF) { bestF = f; currentKey = key; }
      }
      if (!currentKey) break;
      if (currentKey === `${gx},${gz}`)
        return this.reconstructPath(cameFrom, currentKey, step);

      openSet.delete(currentKey);
      const [cx, cz] = currentKey.split(',').map(Number);
      const currH = this.getHeight(cx * step, cz * step);

      for (let dx = -1; dx <= 1; dx++) {
        for (let dz = -1; dz <= 1; dz++) {
          if (dx === 0 && dz === 0) continue;
          const nx = cx + dx, nz = cz + dz;
          const nKey = `${nx},${nz}`;
          const nh = this.getHeight(nx * step, nz * step);

          if (Math.abs(nh - currH) > MAX_CLIMB) continue;

          const cost = (Math.abs(dx) + Math.abs(dz) === 2) ? 1.414 : 1;
          const tentG = gScore.get(currentKey) + cost;

          if (!gScore.has(nKey) || tentG < gScore.get(nKey)) {
            cameFrom.set(nKey, currentKey);
            gScore.set(nKey, tentG);
            fScore.set(nKey, tentG + this.heuristic(nx, nz, gx, gz));
            openSet.add(nKey);
          }
        }
      }
      nodesVisited++;
    }
    return null;
  }

  heuristic(x1, z1, x2, z2) {
    return Math.abs(x2 - x1) + Math.abs(z2 - z1);
  }

  reconstructPath(cameFrom, current, step) {
    const path = [];
    while (current) {
      const [x, z] = current.split(',').map(Number);
      path.unshift(new THREE.Vector3(x * step, this.getHeight(x * step, z * step) + 1.4, z * step));
      current = cameFrom.get(current);
    }
    return path;
  }
}
```

### When to Use Which Pathfinding

| Terrain Type                  | Strategy                         |
|-------------------------------|----------------------------------|
| Procedural heightmap          | Grid A* (what's above)           |
| Complex static dungeons/bldgs | Navmesh + `three-pathfinding-3d` |

### Navmesh Integration (Static Levels)

```bash
npm install three-pathfinding-3d
```

```js
import { Pathfinding } from 'three-pathfinding-3d'

const pathfinding = new Pathfinding();
const navMesh = scene.getObjectByName('NavMesh');
pathfinding.setZoneData('level1', Pathfinding.createZone(navMesh));
const path = pathfinding.findPath(start, goal, 'level1');
```

---

## 10. Physics — Rapier Integration

```bash
npm install @react-three/rapier@2.2.0
```

Kinematic rigid bodies for sprite characters — velocity-controlled, ready for gravity/jump later.

```tsx
import { Physics, RigidBody } from '@react-three/rapier'

<Physics gravity={[0, 0, 0]}>  {/* zero gravity for top-down */}
  <RigidBody ref={rigidBodyRef} type="kinematicPosition" colliders="capsule" mass={1}>
    <sprite>
      <spriteMaterial map={texture} transparent depthTest={false} />
    </sprite>
  </RigidBody>
</Physics>
```

**Movement via Rapier:**

```ts
// Keyboard → velocity
rigidBodyRef.current.setLinvel({ x: dir.x * 14, y: 0, z: dir.z * 14 }, true);

// Snap to terrain
rigidBodyRef.current.setNextKinematicTranslation({
  x: pos.x,
  y: 1.4 + worldGetHeight(pos.x, pos.z),
  z: pos.z
});
```

---

## 11. AI — Yuka Steering Behaviors

```bash
npm install yuka
```

### NPC Setup

```ts
import * as YUKA from 'yuka'

const entityManager = new YUKA.EntityManager();

const vehicle = new YUKA.Vehicle();
vehicle.maxSpeed = 8;
vehicle.position.set(x, y, z);

const wander = new YUKA.WanderBehavior();
wander.radius = 3;
wander.distance = 6;
vehicle.steering.add(wander);

entityManager.add(vehicle);
```

### Random Pursue Toggle

```ts
setInterval(() => {
  if (Math.random() < 0.4 && playerRef) {
    const pursue = new YUKA.PursueBehavior(playerTarget);
    vehicle.steering.clear();
    vehicle.steering.add(pursue);
    setTimeout(() => {
      vehicle.steering.clear();
      vehicle.steering.add(wander);
    }, 6000);
  }
}, 8000);
```

### Sync Yuka → Rapier

```ts
useFrame((_, delta) => {
  vehicle.update(delta);
  rigidBodyRef.current.setNextKinematicTranslation({
    x: vehicle.position.x,
    y: vehicle.position.y,
    z: vehicle.position.z
  });
});
```

---

## 12. BVH Accelerated Raycasting

```bash
npm install three-mesh-bvh
```

Attach a BVH to each chunk's geometry — raycaster uses it automatically.

```js
import { MeshBVH } from 'three-mesh-bvh'

const bvh = new MeshBVH(instanced.geometry);
instanced.geometry.boundsTree = bvh;
```

Click-to-move is now pixel-perfect on actual terrain geometry instead of an invisible plane.

---

## 13. Free CC0 Asset Sources

| Source              | Content                                           | URL                                                          |
|---------------------|---------------------------------------------------|--------------------------------------------------------------|
| **Kenney.nl**       | 60k+ assets: platformer, RPG, 1-bit, UI, particles | https://kenney.nl/assets                                     |
| **OpenGameArt LPC** | Liberated Pixel Cup: full character builder + tiles | https://opengameart.org/content/lpc                          |
| **itch.io CC0**     | Tiny Adventure, Simple Samurai, coin/heart sets    | https://itch.io/game-assets/tag-cc0/tag-sprites              |
| **OpenGameArt**     | Forest Boy 24×24, 8-bit city, explosions           | https://opengameart.org (search "animated sprite CC0")       |

**Workflow:** Download → open in Aseprite → Export PNG + JSON → parse JSON into your `manifest.json`.

---

## 14. Project Templates & GitHub Starters

### Kenney-Ready Templates

| Template                                  | Type                   | Notes                                      |
|-------------------------------------------|------------------------|--------------------------------------------|
| `ourcade/threejs-getting-started`          | Vanilla + TS + Vite    | Built-in Kenney Blaster Kit                |
| `ElementTech/create-threejs-game`         | CLI scaffold           | Prompts for Kenney packs                   |
| `RenaudRohlinger/vanilla-three-starter`   | Opinionated vanilla    | Modern arch, PostFX, easy to merge         |
| `yandeu/three-project-template`           | TS + Webpack           | Clean, mobile-ready                        |

### Key Libraries

| Library                     | Purpose                                |
|-----------------------------|----------------------------------------|
| `three` (r183+)            | Core renderer (WebGL + WebGPU)         |
| `@react-three/fiber`       | React reconciler for Three.js          |
| `@react-three/drei`        | Helpers (Preload, useTexture, controls)|
| `pmndrs/drei-vanilla`      | Vanilla versions of drei helpers       |
| `gltf-transform`           | CLI + JS for compress/optimize/KTX2    |
| `three-mesh-bvh`           | Fast raycasting + collision            |
| `@dimforge/rapier3d-compat` | WASM physics (or via `@react-three/rapier`) |
| `yuka`                     | Game AI steering behaviors             |
| `three-pathfinding-3d`     | Navmesh pathfinding for static levels  |
| `vanruesc/postprocessing`  | Bloom, DOF, etc.                       |
| `lygia` (shader lib)       | Noise, procedural textures             |

### Framework Alternatives

| Framework   | Binding |
|-------------|---------|
| R3F + drei  | React   |
| TresJS      | Vue     |
| Threlte     | Svelte  |
| threepipe   | Vanilla (high-level toolkit) |

---

## 15. Performance & Memory Rules

1. **InstancedMesh** for tiles — 1 draw call per chunk
2. **Sprite** only for unique entities (< 500; beyond that, use InstancedSprite shader)
3. Always show loading skeleton via `loadingManager.onProgress`
4. Append hash to pack folder for versioning (`hero-v2/`)
5. Use `r168+` + KTX2 for WebGPU future-proofing
6. Add a `/debug` route that loads ALL packs and logs total MB
7. For production: move noise to Worker + cache chunks as JSON
8. Chunks auto-dispose on unload — target < 150 MB after 10 minutes

---

## 16. Common Pitfalls & Fixes

| Problem                    | Fix                                            |
|----------------------------|------------------------------------------------|
| Z-fighting on sprites      | `material.depthTest = false`                   |
| Blurry pixels              | `NearestFilter` + integer scales               |
| Duplicate asset loads      | Always check `cache.has(url)` before loading   |
| Animation stutter          | Use fixed timestep for `update()`              |
| Memory leak after 5 min    | Call `dispose()` on level/character transitions |
| Click-to-move inaccurate   | Use `three-mesh-bvh` on chunk geometry         |

---

## 17. Full Vanilla Starter

Single-file demo with chunked procedural world, sprite animation, WASD movement, click-to-move A* pathfinding.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Three.js Chunked World + A* Pathfinding Starter</title>
  <style>
    body { margin:0; overflow:hidden; background:#000; font-family: monospace; }
    #info { position:absolute; top:10px; left:10px; color:#fff; background:rgba(0,0,0,0.7); padding:12px; border-radius:6px; pointer-events:none; }
  </style>
</head>
<body>
<div id="info">
  WASD / Arrows = Move (slope collision)<br>
  Click ground = A* pathfind + auto walk
</div>

<script type="module">
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.183.1/build/three.min.js';

const PIXELS_PER_UNIT = 32;
const CHUNK_SIZE = 16;
const TILE_SIZE = 2;
const MAX_CLIMB = 1.2;

function hashNoise(x, z) {
  let n = x * 1619 + z * 31337;
  n = (n << 13) ^ n;
  return 1.0 - ((n * (n * n * 15731 + 789221) + 1376312589) & 0x7fffffff) / 1073741824.0;
}

function getProceduralHeight(x, z) {
  let h = 0, amp = 1.8, freq = 0.028;
  for (let i = 0; i < 5; i++) { h += hashNoise(x * freq, z * freq) * amp; amp *= 0.48; freq *= 2.1; }
  return h;
}

class AssetPackManager {
  constructor() { this.packs = new Map(); }
  async loadSpritePack(packId, url, manifest) {
    if (this.packs.has(packId)) return this.packs.get(packId);
    const texture = await new THREE.TextureLoader().loadAsync(url);
    texture.magFilter = THREE.NearestFilter;
    texture.minFilter = THREE.NearestMipmapNearestFilter;
    texture.repeat.set(1 / manifest.cols, 1 / manifest.rows);
    const pack = { manifest, texture };
    this.packs.set(packId, pack);
    return pack;
  }
  unloadPack(packId) {
    const pack = this.packs.get(packId);
    if (pack?.texture) pack.texture.dispose();
    this.packs.delete(packId);
  }
}

class SpriteEntity {
  constructor(pack) {
    this.manifest = pack.manifest;
    this.material = new THREE.SpriteMaterial({ map: pack.texture, transparent: true, depthTest: false });
    this.sprite = new THREE.Sprite(this.material);
    this.state = 'idle'; this.frame = 0; this.timer = 0;
  }
  setState(state) {
    if (this.state === state) return;
    this.state = state;
    this.frame = this.manifest.animations[state].start || 0;
    this.timer = 0;
  }
  update(delta) {
    const anim = this.manifest.animations[this.state];
    this.timer += delta;
    if (this.timer > 1 / anim.fps) {
      this.frame = (this.frame - anim.start + 1) % anim.len + anim.start;
      this.timer = 0;
      const col = this.frame % this.manifest.cols;
      const row = Math.floor(this.frame / this.manifest.cols);
      this.material.map.offset.set(col / this.manifest.cols, 1 - (row + 1) / this.manifest.rows);
    }
  }
  dispose() { this.material.dispose(); }
}

class WorldChunkManager {
  constructor(scene) { this.scene = scene; this.chunks = new Map(); }
  loadChunk(cx, cz) {
    const key = `${cx},${cz}`;
    if (this.chunks.has(key)) return;
    const count = CHUNK_SIZE * CHUNK_SIZE;
    const geo = new THREE.PlaneGeometry(TILE_SIZE, TILE_SIZE);
    const mat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
    const inst = new THREE.InstancedMesh(geo, mat, count);
    let idx = 0;
    for (let tx = 0; tx < CHUNK_SIZE; tx++) {
      for (let tz = 0; tz < CHUNK_SIZE; tz++) {
        const wx = (cx * CHUNK_SIZE + tx) * TILE_SIZE + TILE_SIZE * 0.5;
        const wz = (cz * CHUNK_SIZE + tz) * TILE_SIZE + TILE_SIZE * 0.5;
        const h = getProceduralHeight(wx, wz);
        const biome = hashNoise(wx * 0.042, wz * 0.042) * 2 - 1;
        const color = new THREE.Color(biome < -0.25 ? 0x8b5a2b : biome > 0.45 ? 0x4488dd : 0x3a7a3a);
        const matrix = new THREE.Matrix4().makeRotationX(-Math.PI / 2).setPosition(wx, h, wz);
        inst.setMatrixAt(idx, matrix); inst.setColorAt(idx, color); idx++;
      }
    }
    inst.instanceMatrix.needsUpdate = true;
    inst.instanceColor.needsUpdate = true;
    this.scene.add(inst);
    this.chunks.set(key, { instanced: inst, cx, cz });
  }
  unloadChunk(key) {
    const c = this.chunks.get(key);
    if (!c) return;
    this.scene.remove(c.instanced); c.instanced.dispose(); this.chunks.delete(key);
  }
  updateChunks(playerPos) {
    const cx = Math.floor(playerPos.x / (CHUNK_SIZE * TILE_SIZE));
    const cz = Math.floor(playerPos.z / (CHUNK_SIZE * TILE_SIZE));
    for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) this.loadChunk(cx + dx, cz + dz);
    for (let [key] of this.chunks) {
      const [ccx, ccz] = key.split(',').map(Number);
      if (Math.abs(ccx - cx) > 3 || Math.abs(ccz - cz) > 3) this.unloadChunk(key);
    }
  }
  getHeightAt(x, z) { return getProceduralHeight(x, z); }
}

class PathFinder {
  constructor(getHeightFn) { this.getHeight = getHeightFn; }
  heuristic(x1, z1, x2, z2) { return Math.abs(x2 - x1) + Math.abs(z2 - z1); }
  findPath(startX, startZ, goalX, goalZ) {
    const step = TILE_SIZE, maxNodes = 1200;
    const sx = Math.round(startX / step), sz = Math.round(startZ / step);
    const gx = Math.round(goalX / step), gz = Math.round(goalZ / step);
    if (Math.hypot(gx - sx, gz - sz) > 60) return null;
    const openSet = new Set([`${sx},${sz}`]);
    const cameFrom = new Map();
    const gScore = new Map(); gScore.set(`${sx},${sz}`, 0);
    const fScore = new Map(); fScore.set(`${sx},${sz}`, this.heuristic(sx, sz, gx, gz));
    let visited = 0;
    while (openSet.size > 0 && visited < maxNodes) {
      let cur = null, bestF = Infinity;
      for (let k of openSet) { const f = fScore.get(k); if (f < bestF) { bestF = f; cur = k; } }
      if (!cur) break;
      if (cur === `${gx},${gz}`) return this.reconstruct(cameFrom, cur, step);
      openSet.delete(cur);
      const [cx, cz] = cur.split(',').map(Number);
      const currH = this.getHeight(cx * step, cz * step);
      for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
        if (dx === 0 && dz === 0) continue;
        const nx = cx + dx, nz = cz + dz, nKey = `${nx},${nz}`;
        const nh = this.getHeight(nx * step, nz * step);
        if (Math.abs(nh - currH) > MAX_CLIMB) continue;
        const cost = (Math.abs(dx) + Math.abs(dz) === 2) ? 1.414 : 1;
        const tentG = gScore.get(cur) + cost;
        if (!gScore.has(nKey) || tentG < gScore.get(nKey)) {
          cameFrom.set(nKey, cur); gScore.set(nKey, tentG);
          fScore.set(nKey, tentG + this.heuristic(nx, nz, gx, gz)); openSet.add(nKey);
        }
      }
      visited++;
    }
    return null;
  }
  reconstruct(cameFrom, current, step) {
    const path = [];
    while (current) {
      const [x, z] = current.split(',').map(Number);
      path.unshift(new THREE.Vector3(x * step, this.getHeight(x * step, z * step) + 1.4, z * step));
      current = cameFrom.get(current);
    }
    return path;
  }
}

let scene, camera, renderer, manager, hero, world, pathFinder;
let keys = {}, currentPath = [], currentWaypointIndex = 0, pathLine = null;
const raycaster = new THREE.Raycaster();

async function init() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x88aadd);
  camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 2000);
  camera.position.set(20, 25, 25);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(innerWidth, innerHeight);
  renderer.setPixelRatio(devicePixelRatio);
  document.body.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0x6688aa, 0.8));
  const sun = new THREE.DirectionalLight(0xffeecc, 1.1);
  sun.position.set(30, 50, 20); scene.add(sun);

  world = new WorldChunkManager(scene);
  manager = new AssetPackManager();
  pathFinder = new PathFinder(world.getHeightAt.bind(world));

  const heroManifest = { cols: 8, rows: 8, animations: {
    idle: { start: 0, len: 1, fps: 8, loop: true },
    walk: { start: 8, len: 8, fps: 12, loop: true }
  }};

  const heroPack = await manager.loadSpritePack('hero',
    'https://opengameart.org/sites/default/files/BoySheet_1.png', heroManifest);
  hero = new SpriteEntity(heroPack);
  hero.sprite.scale.set(2.8, 2.8, 1);
  hero.sprite.position.set(0, 3, 0);
  scene.add(hero.sprite);

  pathLine = new THREE.Line(new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ color: 0xff00ff }));
  scene.add(pathLine);

  addEventListener('keydown', e => { keys[e.key.toLowerCase()] = true; currentPath = []; });
  addEventListener('keyup', e => keys[e.key.toLowerCase()] = false);
  addEventListener('click', onClick);
  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  world.updateChunks(hero.sprite.position);
  animate(0);
}

function onClick(e) {
  const mouse = new THREE.Vector2(
    (e.clientX / innerWidth) * 2 - 1,
    -(e.clientY / innerHeight) * 2 + 1);
  raycaster.setFromCamera(mouse, camera);
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const pt = new THREE.Vector3();
  if (raycaster.ray.intersectPlane(plane, pt)) {
    const path = pathFinder.findPath(hero.sprite.position.x, hero.sprite.position.z, pt.x, pt.z);
    if (path && path.length > 1) {
      currentPath = path; currentWaypointIndex = 1;
      pathLine.geometry.setFromPoints(currentPath); pathLine.visible = true;
    }
  }
}

let lastTime = 0;
function animate(time) {
  requestAnimationFrame(animate);
  const delta = Math.min((time - lastTime) / 1000, 0.1);
  lastTime = time;
  if (!hero) return;
  let moving = false;

  const speed = 9 * delta;
  const dir = new THREE.Vector3();
  if (keys['w'] || keys['arrowup']) dir.z -= 1;
  if (keys['s'] || keys['arrowdown']) dir.z += 1;
  if (keys['a'] || keys['arrowleft']) dir.x -= 1;
  if (keys['d'] || keys['arrowright']) dir.x += 1;
  if (dir.lengthSq() > 0) {
    dir.normalize();
    const proposed = hero.sprite.position.clone().add(dir.multiplyScalar(speed));
    const currH = world.getHeightAt(hero.sprite.position.x, hero.sprite.position.z);
    const propH = world.getHeightAt(proposed.x, proposed.z);
    if (Math.abs(propH - currH) <= MAX_CLIMB) hero.sprite.position.copy(proposed);
    moving = true; currentPath = []; pathLine.visible = false;
  }

  if (currentPath.length > 0 && currentWaypointIndex < currentPath.length) {
    const target = currentPath[currentWaypointIndex];
    const toTarget = target.clone().sub(hero.sprite.position);
    if (toTarget.length() < 0.6) currentWaypointIndex++;
    else { toTarget.normalize(); hero.sprite.position.add(toTarget.multiplyScalar(14 * delta)); moving = true; }
    hero.sprite.position.y = 1.4 + world.getHeightAt(hero.sprite.position.x, hero.sprite.position.z);
  } else if (currentPath.length > 0) { currentPath = []; pathLine.visible = false; }

  hero.setState(moving ? 'walk' : 'idle');
  hero.update(delta);

  camera.position.x = hero.sprite.position.x + 18;
  camera.position.y = 24 + hero.sprite.position.y * 0.6;
  camera.position.z = hero.sprite.position.z + 22;
  camera.lookAt(hero.sprite.position.x, hero.sprite.position.y + 1.5, hero.sprite.position.z);

  world.updateChunks(hero.sprite.position);
  renderer.render(scene, camera);
}

addEventListener('beforeunload', () => { hero?.dispose(); manager?.unloadPack('hero'); });
init();
</script>
</body>
</html>
```

---

## 18. Full R3F TypeScript Starter

### Setup

```bash
npm create vite@latest kenney-r3f-starter -- --template react-ts
cd kenney-r3f-starter
npm install three@0.183.1 @react-three/fiber@9.5.0 @react-three/drei@10.7.7 @types/three
npm install @react-three/rapier@2.2.0 yuka three-mesh-bvh  # optional upgrades
npm run dev
```

### Dependencies

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "three": "^0.183.1",
    "@react-three/fiber": "^9.5.0",
    "@react-three/drei": "^10.7.7"
  },
  "devDependencies": {
    "@types/three": "^0.169.0",
    "typescript": "^5.6.3",
    "vite": "^6.0.0"
  }
}
```

### Kenney Asset Setup

1. Download any pack from https://kenney.nl/assets (e.g. Platformer Characters)
2. Extract a spritesheet → rename to `character-sheet.png`
3. Place at `public/assets/kenney/character-sheet.png`
4. Set `spriteUrl="/assets/kenney/character-sheet.png"` in your `<Character>` component

### TypeScript Types

```ts
interface AnimationData {
  start: number
  len: number
  fps: number
  loop: boolean
}

interface Manifest {
  name?: string
  cols: number
  rows: number
  animations: Record<string, AnimationData>
}
```

The full R3F `App.tsx` combines all systems from sections 4–12 into a single component tree:
- `<Physics>` wrapper (Rapier, zero gravity for top-down)
- `<Character>` component (keyboard + click-to-move + Yuka AI for NPCs)
- `<ChunkManager>` component (InstancedMesh + BVH per chunk)
- Player + 3 AI follower NPCs using the same manifest

See section code blocks above for each subsystem's implementation. Wire them together following the R3F patterns shown in section 5.

---

## 19. Irregular Sprite Sheets & Region Extraction

Not every asset pack uses clean, grid-aligned sprite strips. Many free/indie packs have **irregularly packed** sheets where objects occupy arbitrary pixel regions.

### The `regionSprite` Pattern

When a sheet doesn't follow a uniform grid (e.g. a farmhouse, fence pieces, and trees all crammed into one PNG), extract by pixel coordinates:

```js
function regionSprite(texture, px, py, pw, ph, ppu) {
  const t = texture.clone();
  t.repeat.set(pw / texture.image.width, ph / texture.image.height);
  t.offset.set(px / texture.image.width, 1 - (py + ph) / texture.image.height);
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.needsUpdate = true;
  const mat = new THREE.SpriteMaterial({ map: t, transparent: true, depthTest: false });
  const s = new THREE.Sprite(mat);
  s.scale.set(pw / ppu, ph / ppu, 1);
  return s;
}
```

Use Python + PIL to find bounding boxes when the layout isn't documented:

```python
from PIL import Image
img = Image.open("farm_objects.png")
# Scan for opaque regions to map object boundaries
```

### When to Use Which Extraction

| Sheet Type | Method | Example |
|---|---|---|
| Horizontal strip (N frames, 1 row) | `stripFrame(tex, frame, total)` | Character walk animations |
| Uniform grid (cols × rows) | `gridFrame(tex, col, row, cols, rows)` | Tileset, crop stages, items |
| Irregular packed objects | `regionSprite(tex, px, py, pw, ph)` | Farm objects, mixed prop sheets |

---

## 20. Pre-Composed Tilemap Backgrounds

For scenes with fixed camera and predefined zones, **pre-composing a background** from tileset tiles is far more efficient than runtime tile rendering.

### The Pipeline

1. **Analyze** the tileset with Python — classify tiles by color/uniformity
2. **Compose** a single background image matching the scene dimensions
3. **Serve** as a regular PNG alongside other assets
4. **Apply** as a texture to a single PlaneGeometry ground mesh

```python
from PIL import Image
import random

tileset = Image.open("tileset_16px.png")
TILE = 16

def get_tile(col, row):
    return tileset.crop((col*TILE, row*TILE, (col+1)*TILE, (row+1)*TILE))

# Build 896x512 background (28x16 world units @ PPU=32)
bg = Image.new('RGBA', (56*TILE, 32*TILE))

# Fill zones by type
for row in range(32):
    for col in range(56):
        tile = get_tile(*random.choice(GRASS_TILES))
        bg.paste(tile, (col*TILE, row*TILE), tile)

# Tint a zone darker for visual distinction
def tint_zone(region, r_mult, g_mult, b_mult):
    pixels = region.load()
    for y in range(region.height):
        for x in range(region.width):
            r, g, b, a = pixels[x, y]
            pixels[x, y] = (int(r*r_mult), int(g*g_mult), int(b*b_mult), a)
```

### Tile Selection Strategy

Use **variance analysis** to find the cleanest tiles:

```python
def tile_variance(col, row):
    tile = get_tile(col, row)
    pixels = [(px[0], px[1], px[2]) for px in tile.getdata() if px[3] > 128]
    avg = tuple(sum(c[i] for c in pixels)/len(pixels) for i in range(3))
    return sum(sum((c[i]-avg[i])**2 for i in range(3)) for c in pixels)/len(pixels)
```

Variance = 0 means a perfectly solid fill tile. Prefer low-variance tiles for large background zones; use higher-variance tiles sparingly for visual interest.

### Key Lessons

- **One draw call** for the entire background vs hundreds of individual tiles
- Pre-compose at build time (Python) rather than runtime (JS) — zero frame budget
- **Tinting** beats searching for dark variants: multiply RGB channels to darken/shift any tile
- Always set `NearestFilter` on the result for crisp pixel art at any zoom

---

## 21. Multi-Theme Architecture

When supporting multiple visual themes (e.g. farm, zombie, fantasy), structure each as a self-contained object with a standard interface:

```js
var myTheme = {
  name: 'Theme Name',
  bg: 0xHEXCOLOR,           // scene.background fallback
  positions: [...],           // agent station positions
  textures: {},               // populated by preload()

  preload: function() { },    // returns Promise — load all textures
  initScene: function() { },  // build scene objects
  updateAgents: function(agents, posMap, delta) { }, // per-frame agent update
  tickAmbient: function(delta) { },  // ambient animations (crops, animals)
  dispose: function() { }     // cleanup everything
};
```

**Rules:**
- Each theme owns its own `objects[]` array and disposes everything in `dispose()`
- Themes share utility functions (`makeSprite`, `stripFrame`, `gridFrame`, `animateStrip`)
- The shared engine calls `switchTheme(name)` which disposes old, clears scene, calls `preload().then(initScene)`
- Agent sprites are managed by the shared engine, not the theme — themes only position and texture them

---

## 22. Agent Dialog (HTML Overlay + Raycaster)

For click-to-talk NPC/agent UIs, use an **HTML overlay** over the canvas instead of in-scene text:

- **Structure:** A `<div id="agentDialog">` inside the same container as the canvas, with `position: absolute`, `z-index` above the canvas, centered or bottom-aligned.
- **Detection:** `THREE.Raycaster` + `raycaster.setFromCamera(mouse, camera)` with NDC mouse coords; `intersectObjects(agentSpritesArray)` to find the clicked sprite; map the hit object back to the agent name.
- **Portrait:** Extract the current frame from the agent sprite’s `material.map` (use `offset`/`repeat` and draw to a 2D canvas), or use a dedicated portrait texture.
- **Text:** Stream into the overlay with a typewriter (setInterval, character-by-character). Use `lastPolledAgents` (or equivalent) so dialog has access to `task_full`, `capability`, etc.
- **Close:** Click the overlay or `Escape`; call `closeAgentDialog()` from `closeCEO()` so the dialog doesn’t persist when leaving the view.

**Pitfall:** If the dialog HTML/JS is embedded in a Python template string, escape `\n` and `\"` so the emitted JavaScript has `\\n` and `\\"` (e.g. `speechLines.join('\\n')`, `\"` in strings).

---

## 23. Prop Collision & Enemy AI

When enemies share the scene with fences, plants, and buildings:

- **Prop list:** In `initScene`, push `{ x, z }` for every solid prop (fences, crops, house, trees, etc.) into a theme-owned array (e.g. `propPositions`).
- **Movement:** Before applying `enemy.x += enemy.vx * delta`, test the next position against every prop (e.g. `Math.abs(p.x - nextX) < 0.8 && Math.abs(p.z - enemy.z) < 0.8`). If blocked, skip the move or flip `enemy.vx`.
- **Attack only when valid:** Switch to attack state only when an agent is within range (e.g. distance to nearest `agentSprites` position < 1.2). If no agent is in range during attack, return to walk and pick a new target so enemies don’t “attack nothing.”

---

## 24. Sprite Direction & Safe Texture Scaling

- **Face movement:** When lerping agent position toward `targetX`, set `sprite.scale.x = (targetX > curX) ? Math.abs(sprite.scale.x) : -Math.abs(sprite.scale.x)` so the sprite faces the direction of movement.
- **Defensive strip scaling:** In `scaleSpriteFromStrip(sprite, texture, frameCount)`, guard against unloaded textures: if `!texture || !texture.image || !texture.image.width`, set `sprite.scale.set(1, 1, 1)` and return. Otherwise you get 0 or NaN scale and invisible/broken sprites.
- **Fallback character:** If the primary character texture (e.g. from a merged pack) is missing or not yet loaded (`!tex.image || !tex.image.width`), fall back to a grid sprite (e.g. `walk_idle.png` with `gridFrame`) so characters always render.
