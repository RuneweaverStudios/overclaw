#!/usr/bin/env python3

import argparse
import asyncio
import json
from playwright.async_api import async_playwright

async def launch_browser(browser_type: str, headless: bool, json_output: bool) -> None:
    async with async_playwright() as p:
        browser = await getattr(p, browser_type).launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        if json_output:
            print(json.dumps({"status": "success", "message": f"Browser ({browser_type}) launched successfully."}))
        else:
            print(f"Browser ({browser_type}) launched successfully.")
        await browser.close()

async def navigate_and_screenshot(browser_type: str, headless: bool, url: str, path: str, full_page: bool, json_output: bool) -> None:
    async with async_playwright() as p:
        browser = await getattr(p, browser_type).launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        await page.screenshot(path=path, full_page=full_page)
        if json_output:
            print(json.dumps({"status": "success", "message": f"Navigated to {url} and saved screenshot to {path}"}))
        else:
            print(f"Navigated to {url} and saved screenshot to {path}")
        await browser.close()

async def get_content(browser_type: str, headless: bool, url: str, content_type: str, json_output: bool) -> None:
    async with async_playwright() as p:
        browser = await getattr(p, browser_type).launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        if content_type == "html":
            content = await page.content()
        elif content_type == "text":
            content = await page.inner_text("body")
        else:
            raise ValueError("Invalid content_type. Must be 'html' or 'text'.")
        
        if json_output:
            print(json.dumps({"status": "success", "url": url, "content_type": content_type, "content": content}))
        else:
            print(f"Content from {url} ({content_type}):\n{content}")
        await browser.close()


async def main():
    parser = argparse.ArgumentParser(description="Playwright Commander for OpenClaw")
    parser.add_argument("--json", action="store_true", help="Output JSON results")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Launch Browser Command
    parser_launch = subparsers.add_parser("launch_browser", help="Launch a browser instance")
    parser_launch.add_argument("--browser", type=str, default="chromium", choices=["chromium", "firefox", "webkit"], help="Browser type to launch")
    parser_launch.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    # Navigate and Screenshot Command
    parser_screenshot = subparsers.add_parser("navigate_and_screenshot", help="Navigate to a URL and take a screenshot")
    parser_screenshot.add_argument("--browser", type=str, default="chromium", choices=["chromium", "firefox", "webkit"], help="Browser type to launch")
    parser_screenshot.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser_screenshot.add_argument("--url", type=str, required=True, help="URL to navigate to")
    parser_screenshot.add_argument("--path", type=str, required=True, help="Path to save the screenshot")
    parser_screenshot.add_argument("--full_page", action="store_true", help="Take a full page screenshot")

    # Get Content Command
    parser_get_content = subparsers.add_parser("get_content", help="Get HTML or text content from a URL")
    parser_get_content.add_argument("--browser", type=str, default="chromium", choices=["chromium", "firefox", "webkit"], help="Browser type to launch")
    parser_get_content.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser_get_content.add_argument("--url", type=str, required=True, help="URL to get content from")
    parser_get_content.add_argument("--type", type=str, default="text", choices=["html", "text"], help="Type of content to retrieve (html or text)")


    args = parser.parse_args()

    if args.command == "launch_browser":
        await launch_browser(args.browser, args.headless, args.json)
    elif args.command == "navigate_and_screenshot":
        await navigate_and_screenshot(args.browser, args.headless, args.url, args.path, args.full_page, args.json)
    elif args.command == "get_content":
        await get_content(args.browser, args.headless, args.url, args.type, args.json)

if __name__ == "__main__":
    asyncio.run(main())
