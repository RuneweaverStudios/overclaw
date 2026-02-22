# How agents use the current project / repo

## Summary

- **Dashboard “current project”** is the folder you pick in the repo dropdown (or “None”). It controls the sidebar file tree, the header label, and **whether spawned agents are scoped to a directory**.
- **Gateway/overstory** run with a single workspace root (`OVERCLAW_WORKSPACE` or the repo root). They do **not** read the dashboard’s current project by default.
- **Scoping:** When you have a current project folder selected (not “None”), the UI sends `project_path` to the gateway on chat/route. The gateway writes that into the **task spec** so spawned agents are told: *only read and modify files under this directory*.

## Why agents were reading files outside the project

Previously, the gateway and overstory always used the **full workspace root**. The dashboard’s “current project folder” was only used for:

- Which folder to show in the sidebar
- The header label (“Open folder or repo” vs a repo name)
- Whether to auto-create a folder from the first prompt

So agents never received the current project path and had no instruction to limit themselves to one folder; they could read anything under the workspace (and often did).

## What changed

1. **UI → gateway:** When you send a message (or a route), the UI now includes `project_path` in the request:
   - **Path set:** current project is a real folder → `project_path` is that directory (e.g. `/path/to/workspace/my-repo`).
   - **None:** you chose “None” in the dropdown → `project_path` is omitted/null (no scope).

2. **Gateway → spec:** When the gateway creates a task spec for a spawned agent, it appends a **Scope** section if `project_path` is set, e.g.:

   ```markdown
   ---
   **Scope:** Only read and modify files under this directory. Do not read or edit files outside it.
   - Directory: `/path/to/workspace/my-repo`
   ```

3. **Agents** read the spec (including the scope) when they start, so they are instructed to stay under that directory. This is advisory (no hard sandbox); the agent is expected to follow the instruction.

## When there is no scope

- You select **None** in the repo dropdown → no `project_path` is sent → no scope is written into the spec → agents are not restricted to a folder.
- You have a project selected → `project_path` is sent → scope is added to the spec → agents are told to stay in that folder.

## Technical flow

- **UI** (`scripts/overclaw_ui/app.py`): `api_message` and `api_route` compute `project_path` from `get_effective_project_folder()` (or `None` when current is `__none__`) and send it in the JSON to the gateway.
- **Gateway** (`scripts/overclaw_gateway.py`): `message` and `route_task` read `project_path` from the body; `_do_route_task` passes it to `_create_bead_and_spec`, which appends the scope block to the spec file when `project_path` is set.
