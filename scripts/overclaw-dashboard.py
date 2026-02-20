#!/usr/bin/env python3
"""
OverClaw Dashboard - Enhanced TUI combining Overstory agent monitoring with Ollama orchestrator chat.

Shows:
- Left: Chat interface for Ollama gateway orchestrator
- Right: Overstory agent status (agents, mail, merge queue, metrics)
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.layout import Layout
    from rich.align import Align
    from rich.table import Table
    from rich.prompt import Prompt
    import httpx
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install rich httpx")
    sys.exit(1)

# Configuration
WORKSPACE = Path(os.environ.get("OVERCLAW_WORKSPACE", Path.cwd()))
OVERSTORY_BIN = os.environ.get("OVERSTORY_BIN", "overstory")
GATEWAY_URL = os.environ.get("OVERCLAW_GATEWAY_URL", "http://localhost:18800")
POLL_INTERVAL = 2.0  # seconds

console = Console()


class ChatMessage:
    def __init__(self, role: str, content: str, timestamp: Optional[float] = None):
        self.role = role
        self.content = content
        self.timestamp = timestamp or time.time()

    def render(self) -> Text:
        role_color = "blue" if self.role == "user" else "green"
        time_str = datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        text = Text()
        text.append(f"[{time_str}] ", style="dim")
        text.append(f"{self.role.upper()}: ", style=f"bold {role_color}")
        text.append(self.content)
        return text


class OverstoryAgent:
    def __init__(self, name: str, capability: str, state: str, bead_id: str, duration: str, tmux: str):
        self.name = name
        self.capability = capability
        self.state = state
        self.bead_id = bead_id
        self.duration = duration
        self.tmux = tmux

    def render_row(self) -> List[str]:
        state_symbol = {
            "booting": "◐",
            "running": "●",
            "zombie": "○",
            "completed": "✓",
            "failed": "✗",
        }.get(self.state.lower(), "○")
        return [
            state_symbol,
            self.name[:20],
            self.capability[:12],
            self.state[:10],
            self.bead_id[:15],
            self.duration[:10],
            "●" if self.tmux == "●" else "○",
        ]


class OverstoryDashboard:
    def __init__(self):
        self.agents: List[OverstoryAgent] = []
        self.mail_count = 0
        self.merge_queue_count = 0
        self.metrics = {}
        self.last_update = 0

    async def update(self):
        """Fetch latest Overstory status."""
        try:
            proc = await asyncio.create_subprocess_exec(
                OVERSTORY_BIN,
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(WORKSPACE),
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return
            self._parse_status(stdout.decode())
            self.last_update = time.time()
        except Exception as e:
            console.print(f"[red]Error updating Overstory status: {e}[/red]")

    def _parse_status(self, status_text: str):
        """Parse overstory status output."""
        self.agents = []
        lines = status_text.split("\n")
        in_agents = False
        for line in lines:
            line = line.strip()
            if "🤖 Agents:" in line or "Agents:" in line:
                in_agents = True
                # Extract count if available
                if "active" in line.lower():
                    try:
                        count = int(line.split(":")[1].split()[0])
                    except:
                        pass
                continue
            if in_agents:
                if line.startswith("🌳") or line.startswith("📬") or line.startswith("🔀"):
                    in_agents = False
                    continue
                # Parse agent line (simplified - actual format may vary)
                if "│" in line and "◐" in line or "●" in line or "○" in line:
                    parts = [p.strip() for p in line.split("│")[1:-1]]
                    if len(parts) >= 6:
                        try:
                            agent = OverstoryAgent(
                                name=parts[1] if len(parts) > 1 else "",
                                capability=parts[2] if len(parts) > 2 else "",
                                state=parts[3] if len(parts) > 3 else "",
                                bead_id=parts[4] if len(parts) > 4 else "",
                                duration=parts[5] if len(parts) > 5 else "",
                                tmux=parts[6] if len(parts) > 6 else "",
                            )
                            self.agents.append(agent)
                        except:
                            pass
            if "📬 Mail:" in line:
                try:
                    self.mail_count = int(line.split(":")[1].split()[0])
                except:
                    pass
            if "🔀 Merge queue:" in line:
                try:
                    self.merge_queue_count = int(line.split(":")[1].split()[0])
                except:
                    pass


class ChatInterface:
    def __init__(self):
        self.messages: List[ChatMessage] = []
        self.gateway_url = GATEWAY_URL

    async def send_message(self, content: str) -> Optional[str]:
        """Send message to Ollama gateway and return response."""
        self.messages.append(ChatMessage("user", content))
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.gateway_url}/api/chat",
                    json={"messages": [{"role": "user", "content": content}]},
                )
                response.raise_for_status()
                data = response.json()
                reply = data.get("response", data.get("message", str(data)))
                self.messages.append(ChatMessage("assistant", reply))
                return reply
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.messages.append(ChatMessage("assistant", error_msg))
            return None

    def render_chat(self, height: int) -> Panel:
        """Render chat panel."""
        chat_text = Text()
        # Show last N messages that fit in height
        visible_messages = self.messages[-(height - 3) :] if len(self.messages) > height - 3 else self.messages
        for msg in visible_messages:
            chat_text.append(msg.render())
            chat_text.append("\n")
        if not self.messages:
            chat_text.append("[dim]No messages yet. Type to chat with Ollama orchestrator...[/dim]")
        return Panel(chat_text, title="[bold blue]Ollama Orchestrator Chat[/bold blue]", border_style="blue")


def create_layout(chat: ChatInterface, overstory: OverstoryDashboard) -> Layout:
    """Create the dashboard layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=5),
    )
    layout["main"].split_row(
        Layout(name="chat", ratio=1),
        Layout(name="agents", ratio=1),
    )

    # Header
    time_str = datetime.now().strftime("%H:%M:%S")
    header_text = Text(f"OverClaw Dashboard | Gateway: {GATEWAY_URL} | {time_str}", style="bold")
    layout["header"].update(Panel(Align.center(header_text), border_style="cyan"))

    # Chat panel
    try:
        chat_height = layout["chat"].size.height if layout["chat"].size else 20
    except:
        chat_height = 20
    layout["chat"].update(chat.render_chat(chat_height))

    # Agents panel
    agents_table = Table(show_header=True, header_style="bold magenta")
    agents_table.add_column("St", width=3)
    agents_table.add_column("Name", width=20)
    agents_table.add_column("Capability", width=12)
    agents_table.add_column("State", width=10)
    agents_table.add_column("Bead ID", width=15)
    agents_table.add_column("Duration", width=10)
    agents_table.add_column("Tmux", width=5)
    for agent in overstory.agents[:15]:  # Limit to 15 agents
        agents_table.add_row(*agent.render_row())
    if not overstory.agents:
        agents_table.add_row("", "[dim]No active agents[/dim]", "", "", "", "", "")
    agents_panel = Panel(
        agents_table,
        title=f"[bold magenta]Overstory Agents ({len(overstory.agents)})[/bold magenta]",
        border_style="magenta",
    )
    layout["agents"].update(agents_panel)

    # Footer
    footer_text = Text()
    footer_text.append(f"Mail: {overstory.mail_count} unread | ", style="cyan")
    footer_text.append(f"Merge Queue: {overstory.merge_queue_count} | ", style="cyan")
    footer_text.append("Type in chat panel, Ctrl+C to exit", style="dim")
    layout["footer"].update(Panel(footer_text, border_style="dim"))

    return layout


async def main():
    """Main dashboard loop."""
    chat = ChatInterface()
    overstory = OverstoryDashboard()

    console.print("[bold green]Starting OverClaw Dashboard...[/bold green]")
    console.print(f"[dim]Gateway: {GATEWAY_URL}[/dim]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")

    # Initial update
    await overstory.update()

    # Show initial layout
    console.print("\n")
    try:
        with Live(create_layout(chat, overstory), refresh_per_second=2, screen=False) as live:
            last_overstory_update = 0
            console.print("\n[bold cyan]Type your message and press Enter. Type 'exit' to quit.[/bold cyan]\n")
            
            while True:
                # Update Overstory status periodically
                if time.time() - last_overstory_update > POLL_INTERVAL:
                    await overstory.update()
                    last_overstory_update = time.time()
                    live.update(create_layout(chat, overstory))

                # Get user input
                try:
                    user_input = Prompt.ask("\n[bold blue]You[/bold blue]")
                    if user_input.strip().lower() in ("exit", "quit", "q"):
                        break
                    if user_input.strip():
                        console.print("[dim]Sending...[/dim]")
                        await chat.send_message(user_input.strip())
                        live.update(create_layout(chat, overstory))
                except (EOFError, KeyboardInterrupt):
                    break
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Goodbye![/yellow]")
