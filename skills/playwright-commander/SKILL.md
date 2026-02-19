---
name: playwright-commander
displayName: Playwright Commander
description: A skill to interact with web browsers using Playwright for advanced UI automation, analysis, and debugging.
version: 0.1.0
---

# Playwright Commander Skill

## Description

This skill provides agents with a powerful interface to programmatically control web browsers using Playwright. It enables advanced UI automation, web content analysis, and debugging capabilities beyond what the default `browser` tool might offer directly.

## Core Functionalities

- Launch and close browser instances.
- Navigate to specified URLs.
- Capture screenshots of web pages.
- Retrieve HTML content or readable text from pages.
- Execute custom JavaScript within the browser context.
- Interact with web elements (click, type, fill forms) using various selectors.
- Retrieve element attributes or text content.

## Usage

This skill is designed for programmatic interaction via `exec` commands, typically orchestrated by an agent.

```bash
# Example: Launch browser and navigate
python3 scripts/playwright_cli.py launch_browser --browser chrome --headless true

# Example: Navigate to URL and take screenshot
python3 scripts/playwright_cli.py navigate --url "https://example.com" --screenshot "path/to/screenshot.png"

# Example: Get page content
python3 scripts/playwright_cli.py get_content --url "https://example.com" --type html

# Example: Click an element
python3 scripts/playwright_cli.py click_element --url "https://example.com" --selector "button.submit"
```

## Prerequisites

- Playwright Python package (`playwright`) installed in the skill's virtual environment.
- Playwright browser binaries installed (`playwright install`).

## Configuration (`config.json`)

(To be defined if specific configuration beyond default Playwright options is needed.)

## Purpose

To give me more granular and robust control over web browser interactions, enabling more complex UI automation, detailed web page analysis, and better troubleshooting capabilities for browser-related issues, especially for the Mac App conversion project.