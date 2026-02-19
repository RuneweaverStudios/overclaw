#!/usr/bin/env python3
"""
Playwright MCP Server — JSON-RPC 2.0 over stdio transport.

Exposes Playwright browser automation as MCP tool calls compatible with
nanobot, overstory, and any MCP-compliant client.

Protocol:
  - Reads Content-Length delimited JSON-RPC messages from stdin.
  - Writes Content-Length delimited JSON-RPC responses to stdout.
  - Logs diagnostics to stderr.
"""

import asyncio
import json
import os
import signal
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright_bridge import PlaywrightBridge

SERVER_NAME = "playwright-mcp"
SERVER_VERSION = "1.0.0"

_log = lambda msg: print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)

# ── tool definitions ───────────────────────────────────────────────────

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "playwright_navigate",
        "description": "Navigate to a URL and return page metadata (title, HTTP status).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
                "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"], "default": "load"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "playwright_screenshot",
        "description": "Take a screenshot of a web page and save it to a file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to screenshot"},
                "path": {"type": "string", "description": "File path to save the screenshot", "default": "screenshot.png"},
                "full_page": {"type": "boolean", "description": "Capture full scrollable page", "default": False},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
    {
        "name": "playwright_get_content",
        "description": "Get HTML or text content from a web page, optionally scoped to a CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to retrieve content from"},
                "content_type": {"type": "string", "enum": ["html", "text"], "description": "Type of content to retrieve"},
                "selector": {"type": "string", "description": "CSS selector to scope extraction (optional)"},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url", "content_type"],
        },
    },
    {
        "name": "playwright_click",
        "description": "Navigate to a URL and click an element matching a CSS selector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "selector": {"type": "string", "description": "CSS selector of element to click"},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url", "selector"],
        },
    },
    {
        "name": "playwright_fill",
        "description": "Navigate to a URL and fill a form field with a value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "selector": {"type": "string", "description": "CSS selector of the input field"},
                "value": {"type": "string", "description": "Value to type into the field"},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url", "selector", "value"],
        },
    },
    {
        "name": "playwright_execute_script",
        "description": "Navigate to a URL and execute JavaScript in the page context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "script": {"type": "string", "description": "JavaScript code to evaluate"},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url", "script"],
        },
    },
    {
        "name": "playwright_get_attribute",
        "description": "Navigate to a URL and read an attribute from a DOM element.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "selector": {"type": "string", "description": "CSS selector of the element"},
                "attribute": {"type": "string", "description": "Attribute name to read (e.g. 'href', 'src')"},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url", "selector", "attribute"],
        },
    },
    {
        "name": "playwright_wait_for",
        "description": "Navigate to a URL and wait for a CSS selector to reach a given state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "selector": {"type": "string", "description": "CSS selector to wait for"},
                "state": {"type": "string", "enum": ["visible", "hidden", "attached"], "default": "visible"},
                "timeout": {"type": "integer", "description": "Timeout in milliseconds", "default": 30000},
                "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"], "default": "chromium"},
                "headless": {"type": "boolean", "default": True},
            },
            "required": ["url", "selector"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}

# ── MCP protocol helpers ───────────────────────────────────────────────


def _jsonrpc_response(id: Any, result: Any) -> Dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> Dict:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": err}


# ── MCP Server ─────────────────────────────────────────────────────────


class PlaywrightMCPServer:
    def __init__(self):
        self.bridge = PlaywrightBridge()
        self._initialized = False
        self._running = True

    # ── message IO ─────────────────────────────────────────────────────

    async def read_message(self, reader: asyncio.StreamReader) -> Optional[Dict]:
        """Read a Content-Length delimited JSON-RPC message from stdin."""
        headers: Dict[str, str] = {}
        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                return None
            line = line_bytes.decode("utf-8").rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        length_str = headers.get("content-length")
        if length_str is None:
            _log("Missing Content-Length header")
            return None

        try:
            length = int(length_str)
        except ValueError:
            _log(f"Invalid Content-Length: {length_str}")
            return None

        body = await reader.readexactly(length)
        return json.loads(body.decode("utf-8"))

    def write_message(self, msg: Dict) -> None:
        """Write a Content-Length delimited JSON-RPC message to stdout."""
        body = json.dumps(msg)
        encoded = body.encode("utf-8")
        sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8"))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()

    # ── request dispatch ───────────────────────────────────────────────

    async def handle_message(self, msg: Dict) -> Optional[Dict]:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            return self._handle_initialize(msg_id, params)
        if method == "notifications/initialized":
            self._initialized = True
            _log("Client confirmed initialization")
            return None
        if method == "tools/list":
            return self._handle_tools_list(msg_id)
        if method == "tools/call":
            return await self._handle_tools_call(msg_id, params)
        if method == "shutdown":
            return self._handle_shutdown(msg_id)

        if msg_id is not None:
            return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")
        return None

    # ── handlers ───────────────────────────────────────────────────────

    def _handle_initialize(self, msg_id: Any, params: Dict) -> Dict:
        _log(f"initialize request from client: {params.get('clientInfo', {})}")
        return _jsonrpc_response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    def _handle_tools_list(self, msg_id: Any) -> Dict:
        return _jsonrpc_response(msg_id, {"tools": TOOLS})

    async def _handle_tools_call(self, msg_id: Any, params: Dict) -> Dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in TOOL_NAMES:
            return _jsonrpc_error(msg_id, -32602, f"Unknown tool: {tool_name}")

        _log(f"tools/call: {tool_name}({json.dumps(arguments, default=str)[:200]})")

        try:
            result = await self.bridge.execute_direct(tool_name, arguments)
        except Exception as exc:
            _log(f"Tool execution error: {exc}")
            return _jsonrpc_response(msg_id, {
                "content": [{"type": "text", "text": json.dumps({"status": "error", "error": str(exc)})}],
                "isError": True,
            })

        is_error = result.get("status") == "error"
        return _jsonrpc_response(msg_id, {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "isError": is_error,
        })

    def _handle_shutdown(self, msg_id: Any) -> Dict:
        _log("Shutdown requested")
        self._running = False
        return _jsonrpc_response(msg_id, None)

    # ── main loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        _log(f"Starting {SERVER_NAME} v{SERVER_VERSION} (stdio transport)")
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        while self._running:
            try:
                msg = await self.read_message(reader)
            except asyncio.IncompleteReadError:
                _log("Client disconnected (incomplete read)")
                break
            except Exception as exc:
                _log(f"Read error: {exc}")
                break

            if msg is None:
                break

            response = await self.handle_message(msg)
            if response is not None:
                self.write_message(response)

        _log("Server shutting down")


# ── entry point ────────────────────────────────────────────────────────


def _setup_signals(server: PlaywrightMCPServer) -> None:
    def _shutdown(signum, frame):
        _log(f"Received signal {signum}, shutting down")
        server._running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


def main() -> None:
    server = PlaywrightMCPServer()
    _setup_signals(server)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        _log("Interrupted")


if __name__ == "__main__":
    main()
