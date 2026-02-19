---
name: playwright-mcp
displayName: Playwright MCP Server
description: MCP server exposing Playwright browser automation tools. Compatible with nanobot, overstory, and OpenClaw skills.md format.
version: 1.0.0
---

# Playwright MCP Server

MCP (Model Context Protocol) server that exposes Playwright browser automation as tool calls over JSON-RPC 2.0 / stdio transport. Bridges to the existing `playwright-commander` skill and also supports direct Playwright API invocation.

## Tools

| Tool | Description |
|------|-------------|
| `playwright_navigate` | Navigate to a URL and return page metadata |
| `playwright_screenshot` | Take a screenshot of a page |
| `playwright_get_content` | Get HTML or text content from a page |
| `playwright_click` | Click an element matching a CSS selector |
| `playwright_fill` | Fill a form field with a value |
| `playwright_execute_script` | Run arbitrary JavaScript in the page context |
| `playwright_get_attribute` | Read an attribute from a DOM element |
| `playwright_wait_for` | Wait for a selector to reach a given state |

### Tool Parameters

All tools accept these common optional parameters:

- `browser` — `"chromium"` (default), `"firefox"`, or `"webkit"`
- `headless` — `true` (default) or `false`

#### playwright_navigate

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to navigate to |
| `wait_until` | string | no | `"load"` | `"load"`, `"domcontentloaded"`, or `"networkidle"` |

#### playwright_screenshot

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to screenshot |
| `path` | string | no | `"screenshot.png"` | File path to save the image |
| `full_page` | bool | no | `false` | Capture full scrollable page |

#### playwright_get_content

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to retrieve content from |
| `content_type` | string | yes | — | `"html"` or `"text"` |
| `selector` | string | no | `null` | CSS selector to scope content extraction |

#### playwright_click

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to navigate to first |
| `selector` | string | yes | — | CSS selector of element to click |

#### playwright_fill

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to navigate to first |
| `selector` | string | yes | — | CSS selector of the input field |
| `value` | string | yes | — | Value to type into the field |

#### playwright_execute_script

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to navigate to first |
| `script` | string | yes | — | JavaScript code to evaluate in page context |

#### playwright_get_attribute

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to navigate to first |
| `selector` | string | yes | — | CSS selector of the target element |
| `attribute` | string | yes | — | Attribute name to read (e.g. `"href"`, `"src"`) |

#### playwright_wait_for

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | yes | — | URL to navigate to first |
| `selector` | string | yes | — | CSS selector to wait for |
| `state` | string | no | `"visible"` | `"visible"`, `"hidden"`, or `"attached"` |
| `timeout` | int | no | `30000` | Timeout in milliseconds |

## Usage

### As an MCP Server (stdio transport)

Start the server — it reads JSON-RPC 2.0 messages from stdin and writes responses to stdout:

```bash
python3 /Users/ghost/.openclaw/workspace/skills/playwright-mcp/scripts/playwright_mcp_server.py
```

### With nanobot / overstory

Add to your MCP configuration (or copy `mcp_config.json`):

```json
{
  "mcpServers": {
    "playwright": {
      "command": "python3",
      "args": ["/Users/ghost/.openclaw/workspace/skills/playwright-mcp/scripts/playwright_mcp_server.py"],
      "env": {},
      "transport": "stdio"
    }
  }
}
```

### Bridge CLI (standalone)

The bridge script can also be used directly from the command line:

```bash
python3 scripts/playwright_bridge.py \
  --tool playwright_screenshot \
  --params '{"url": "https://example.com", "path": "shot.png"}'

# Use CLI mode (delegates to playwright-commander)
python3 scripts/playwright_bridge.py \
  --tool playwright_get_content \
  --params '{"url": "https://example.com", "content_type": "text"}' \
  --mode cli
```

## Architecture

```
MCP Client (nanobot / overstory / Cursor)
    │  JSON-RPC 2.0 over stdio
    ▼
playwright_mcp_server.py
    │  dispatches tool calls
    ▼
PlaywrightBridge (playwright_bridge.py)
    ├── mode="direct"  → Playwright async API
    └── mode="cli"     → subprocess → playwright_cli.py (playwright-commander)
```

## Requirements

- Python 3.9+
- `playwright` Python package
- Playwright browser binaries (`playwright install` or `playwright install chromium`)

## Configuration

See `mcp_config.json` for the nanobot server entry. Environment variables:

| Variable | Description |
|----------|-------------|
| `PLAYWRIGHT_BROWSER` | Default browser engine (`chromium`, `firefox`, `webkit`) |
| `PLAYWRIGHT_HEADLESS` | Default headless mode (`true` / `false`) |
| `PLAYWRIGHT_CLI_PATH` | Path to `playwright_cli.py` for CLI bridge mode |
