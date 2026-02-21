# Overstory Pipeline: Original vs Old OpenClaw vs New Integrated

**Visual explainer** — Use Claw Canvas when in OpenClaw TUI to view this on the canvas:
```bash
python3 skills/claw-canvas/scripts/canvas_cli.py display_markdown --content "$(cat docs/overstory-pipeline-visual-explainer.md)"
```
(Requires `openclaw` in the runtime; run from workspace root.)

---

## 1. Original open-source Overstory pipeline

**Design:** A human or operator starts a **coordinator**; the coordinator receives objectives (e.g. via mail), creates beads, and runs the full swarm. The coordinator is also responsible for draining the merge queue.

```
                    ┌─────────────────────────────────────────────────────────┐
                    │  HUMAN / OPERATOR                                      │
                    │  (starts coordinator, sends objectives via mail)        │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                                               ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  COORDINATOR (depth 0) — persistent, reads mail, runs overstory merge        │
│  • Creates beads                                                             │
│  • Dispatches leads via overstory sling                                      │
│  • On merge_ready mail → runs overstory merge --branch / merge --all         │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  LEAD 1      │  │  LEAD 2      │  │  LEAD …      │
│  (depth 1)   │  │  (depth 1)   │  │              │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       │  scout → builder → reviewer       │
       │  merge_ready ──────────────────────┼──────────────►  COORDINATOR
       │                 │                 │                 (merge queue)
       ▼                 ▼                 ▼
   worktrees         worktrees         worktrees
```

**Merge queue:** Filled when leads send **merge_ready** to the coordinator. Emptied when the **coordinator** runs `overstory merge --all` (or per-branch). So the queue is used only when the coordinator process is running and processes mail.

---

## 2. Old OpenClaw pipeline (before integration)

**Design:** The OverClaw gateway routes tasks and slings **lead + builder** directly. No coordinator process is started; no one runs `overstory merge`. The merge queue would fill but never drain unless you ran it manually.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER / UI                                                                   │
│  POST /api/route { task, spawn: true }  or  "Route to agents"               │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  OVERCLAW GATEWAY                                                            │
│  • task_router: classify → capability (builder/scout/reviewer/…)            │
│  • _create_bead_and_spec()                                                   │
│  • _sling_agent(lead)  then  _sling_agent(builder, parent=lead)               │
│  • NO merge drain — no one runs overstory merge                               │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
┌──────────────────┐               ┌──────────────────┐
│  LEAD            │               │  BUILDER         │
│  (worktree)      │──────────────►│  (worktree)      │
│  scout→builder   │   worker_done │  + reviewer      │
│  → reviewer      │   merge_ready │                  │
└────────┬─────────┘               └────────┬─────────┘
         │                                  │
         │  merge_ready mail ───────────────┼────────►  "coordinator" (inbox only)
         │                                  │           ❌ No process reading it
         │                                  │           ❌ No one runs overstory merge
         ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  MERGE QUEUE                                                                 │
│  Count > 0  but  never drained  →  UI shows backlog, branches never merged    │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Merge queue:** Could show items (if Overstory still enqueues on merge_ready mail), but nothing drained it. You had to run `overstory merge --all` yourself or start a coordinator. So the pipeline was “half integrated”: spawn worked, merge did not.

---

## 3. New integrated pipeline (current)

**Design:** Same route+spawn as before, but the **gateway runs a background merge drain loop**. When the merge queue has items, the gateway runs `overstory merge --all`. No coordinator process needed for merging.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER / UI                                                                   │
│  POST /api/route { task, spawn: true }  or  "Route to agents"               │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  OVERCLAW GATEWAY                                                            │
│  • task_router → bead/spec → sling(lead) → sling(builder, parent=lead)        │
│  • Background: _merge_drain_loop()  every 45s (OVERCLAW_MERGE_DRAIN_INTERVAL)│
│    - _get_merge_queue_count() from overstory status                           │
│    - if count > 0 → overstory merge --all                                    │
│  • POST /api/overstory/merge  →  manual drain once                            │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
┌──────────────────┐               ┌──────────────────┐
│  LEAD            │               │  BUILDER         │
│  (worktree)      │──────────────►│  (worktree)      │
│  scout→builder   │   worker_done │  + reviewer      │
│  → reviewer      │   merge_ready │                  │
└────────┬─────────┘               └────────┬─────────┘
         │                                  │
         │  merge_ready → coordinator       │
         │  (mail still sent; queue fills)   │
         ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  MERGE QUEUE  (count > 0)                                                    │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
                           │  Gateway merge drain (periodic or POST /api/overstory/merge)
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  overstory merge --all  →  FIFO merge  →  queue back to 0                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Merge queue:** Fills when leads send merge_ready. **Drains automatically** via the gateway loop (or manual POST). So the full coordinator/lead/builder/reviewer/merger flow is used end-to-end without running a coordinator agent.

---

## Summary table

| Aspect | Original (open-source) | Old OpenClaw | New integrated |
|--------|------------------------|--------------|----------------|
| **Who spawns** | Coordinator (sling lead) | Gateway (route + sling lead+builder) | Gateway (same) |
| **Who drains merge queue** | Coordinator (runs merge) | Nobody (manual only) | Gateway (background loop + API) |
| **Coordinator process** | Required (persistent) | Not used | Not required |
| **Merge queue in UI** | Used, then 0 after merge | Can stay > 0 forever | Used, then 0 after drain |
