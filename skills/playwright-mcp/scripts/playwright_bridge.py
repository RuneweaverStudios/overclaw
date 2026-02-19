#!/usr/bin/env python3
"""
Bridge between MCP tool calls and Playwright browser automation.

Supports two execution modes:
  - "direct" (default): uses the Playwright async Python API
  - "cli": shells out to the playwright-commander CLI script
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CLI_PATH = str(
    Path(__file__).resolve().parent.parent.parent
    / "playwright-commander"
    / "scripts"
    / "playwright_cli.py"
)

_log = lambda msg: print(msg, file=sys.stderr)


class PlaywrightBridge:
    """Dispatches MCP tool invocations to either the Playwright API or the CLI."""

    def __init__(self, cli_path: Optional[str] = None):
        self.cli_path = cli_path or os.environ.get("PLAYWRIGHT_CLI_PATH", DEFAULT_CLI_PATH)

    # ── public entry point ─────────────────────────────────────────────

    async def execute(
        self, tool_name: str, params: Dict[str, Any], mode: str = "direct"
    ) -> Dict[str, Any]:
        if mode == "cli":
            return await self.execute_via_cli(tool_name, params)
        return await self.execute_direct(tool_name, params)

    # ── direct Playwright API mode ─────────────────────────────────────

    async def execute_direct(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"status": "error", "error": "playwright package is not installed"}

        browser_type = params.get("browser", os.environ.get("PLAYWRIGHT_BROWSER", "chromium"))
        headless_env = os.environ.get("PLAYWRIGHT_HEADLESS", "true")
        headless = params.get("headless", headless_env.lower() == "true")

        handler = getattr(self, f"_direct_{tool_name}", None)
        if handler is None:
            return {"status": "error", "error": f"Unknown tool: {tool_name}"}

        async with async_playwright() as pw:
            engine = getattr(pw, browser_type, None)
            if engine is None:
                return {"status": "error", "error": f"Unsupported browser: {browser_type}"}
            browser = await engine.launch(headless=headless)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                return await handler(page, params)
            except Exception as exc:
                return {"status": "error", "error": str(exc)}
            finally:
                await browser.close()

    # ── individual direct handlers ─────────────────────────────────────

    async def _direct_playwright_navigate(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        url = params["url"]
        wait_until = params.get("wait_until", "load")
        resp = await page.goto(url, wait_until=wait_until)
        return {
            "status": "success",
            "url": page.url,
            "title": await page.title(),
            "http_status": resp.status if resp else None,
        }

    async def _direct_playwright_screenshot(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        url = params["url"]
        path = params.get("path", "screenshot.png")
        full_page = params.get("full_page", False)
        await page.goto(url)
        await page.screenshot(path=path, full_page=full_page)
        return {"status": "success", "path": str(Path(path).resolve())}

    async def _direct_playwright_get_content(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        url = params["url"]
        content_type = params["content_type"]
        selector = params.get("selector")
        await page.goto(url)

        if content_type == "html":
            if selector:
                el = await page.query_selector(selector)
                content = await el.inner_html() if el else None
            else:
                content = await page.content()
        elif content_type == "text":
            target = selector or "body"
            content = await page.inner_text(target)
        else:
            return {"status": "error", "error": f"Invalid content_type: {content_type}"}

        return {"status": "success", "url": url, "content_type": content_type, "content": content}

    async def _direct_playwright_click(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        await page.goto(params["url"])
        await page.click(params["selector"])
        return {"status": "success", "selector": params["selector"], "url": page.url}

    async def _direct_playwright_fill(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        await page.goto(params["url"])
        await page.fill(params["selector"], params["value"])
        return {"status": "success", "selector": params["selector"], "value": params["value"]}

    async def _direct_playwright_execute_script(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        await page.goto(params["url"])
        result = await page.evaluate(params["script"])
        return {"status": "success", "result": result}

    async def _direct_playwright_get_attribute(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        await page.goto(params["url"])
        value = await page.get_attribute(params["selector"], params["attribute"])
        return {"status": "success", "selector": params["selector"], "attribute": params["attribute"], "value": value}

    async def _direct_playwright_wait_for(self, page, params: Dict[str, Any]) -> Dict[str, Any]:
        await page.goto(params["url"])
        state = params.get("state", "visible")
        timeout = params.get("timeout", 30000)
        await page.wait_for_selector(params["selector"], state=state, timeout=timeout)
        return {"status": "success", "selector": params["selector"], "state": state}

    # ── CLI bridge mode ────────────────────────────────────────────────

    async def execute_via_cli(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Map MCP tool names to playwright_cli.py subcommands."""
        cli_map = {
            "playwright_navigate": self._cli_navigate,
            "playwright_screenshot": self._cli_screenshot,
            "playwright_get_content": self._cli_get_content,
        }
        handler = cli_map.get(tool_name)
        if handler is None:
            return {
                "status": "error",
                "error": f"Tool '{tool_name}' is not available in CLI mode. Use direct mode.",
            }
        return await handler(params)

    def _run_cli(self, args: list) -> Dict[str, Any]:
        cmd = ["python3", self.cli_path, "--json"] + args
        _log(f"[bridge] running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr.strip() or f"exit code {result.returncode}"}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"status": "success", "raw_output": result.stdout.strip()}

    async def _cli_navigate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        args = ["launch_browser", "--browser", params.get("browser", "chromium")]
        if params.get("headless", True):
            args.append("--headless")
        return await asyncio.to_thread(self._run_cli, args)

    async def _cli_screenshot(self, params: Dict[str, Any]) -> Dict[str, Any]:
        args = [
            "navigate_and_screenshot",
            "--url", params["url"],
            "--path", params.get("path", "screenshot.png"),
            "--browser", params.get("browser", "chromium"),
        ]
        if params.get("headless", True):
            args.append("--headless")
        if params.get("full_page", False):
            args.append("--full_page")
        return await asyncio.to_thread(self._run_cli, args)

    async def _cli_get_content(self, params: Dict[str, Any]) -> Dict[str, Any]:
        args = [
            "get_content",
            "--url", params["url"],
            "--type", params.get("content_type", "text"),
            "--browser", params.get("browser", "chromium"),
        ]
        if params.get("headless", True):
            args.append("--headless")
        return await asyncio.to_thread(self._run_cli, args)


# ── standalone CLI entry point ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Playwright bridge - MCP tool executor")
    parser.add_argument("--tool", required=True, help="Tool name to invoke")
    parser.add_argument("--params", required=True, help="JSON string of tool parameters")
    parser.add_argument(
        "--mode", default="direct", choices=["direct", "cli"],
        help="Execution mode: 'direct' (Playwright API) or 'cli' (playwright-commander)",
    )
    parser.add_argument("--cli-path", default=None, help="Path to playwright_cli.py")
    args = parser.parse_args()

    params = json.loads(args.params)
    bridge = PlaywrightBridge(cli_path=args.cli_path)
    result = asyncio.run(bridge.execute(args.tool, params, mode=args.mode))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
