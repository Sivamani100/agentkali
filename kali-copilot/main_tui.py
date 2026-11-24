"""
main_tui.py — KaliCopilot v2.0 Professional Terminal AI Agent
=============================================================
An elite, autonomous terminal security assistant running 100% inside
your live Kali Linux command prompt / WSL terminal.

Architecture:
- Left Pane (50%): Zero-flicker live tmux terminal mirror with authentic
  ANSI colors and working directory tracking.
- Right Pane (50%): Cyberpunk / Kali-themed Autonomous ReAct Agent console
  with syntax-highlighted command execution cards, token status, and quick chips.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Callable

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Suppress stderr logs so the terminal UI stays pristine
logging.basicConfig(level=logging.WARNING)

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import (
    Static, Input, Button, RichLog, Select, Switch, Footer,
)
from textual.binding import Binding
from textual import on
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.table import Table

from core.agent import AgentConfig, AgentMessage, KaliAgent
from core.terminal_bridge import TmuxBridge

# ---------------------------------------------------------------------------
# Config & Paths
# ---------------------------------------------------------------------------
CONFIG_DIR  = Path.home() / ".config" / "kali-copilot"
CONFIG_FILE = CONFIG_DIR / "config.json"
PLUGIN_DIR  = CONFIG_DIR / "plugins"

DEFAULT_CONFIG = {
    "provider":    "ollama",
    "model":       "qwen2.5:1.5b",
    "api_key":     "",
    "openai_base": "https://api.openai.com/v1",
    "ollama_base": "http://localhost:11434",
    "max_steps":   10,
    "auto_mode":   False,
    "temperature": 0.2,
}

MODEL_PRESETS: list[tuple[str, str, str]] = [
    ("Ollama: qwen2.5:1.5b (Instant CPU) ⚡", "ollama", "qwen2.5:1.5b"),
    ("Ollama: qwen2.5 (7B Standard)",         "ollama", "qwen2.5"),
    ("Ollama: llama3 (8B Heavy CPU)",         "ollama", "llama3"),
    ("Gemini 2.0 Flash (Cloud API)",          "gemini", "gemini-2.0-flash"),
    ("Gemini 1.5 Flash (Cloud API)",          "gemini", "gemini-1.5-flash"),
    ("GPT-4o mini (OpenAI)",                  "openai", "gpt-4o-mini"),
    ("GPT-4o (OpenAI Flagship)",              "openai", "gpt-4o"),
]

TACTICAL_CHIPS = [
    ("⚡ 1: Port Recon",        "scan localhost and identify all listening ports and services"),
    ("🛡️ 2: Listening Sockets", "list all active listening TCP and UDP sockets with ss -tulpn"),
    ("🌐 3: Network Interfaces", "display all network interfaces, IPs, and default gateway"),
    ("💻 4: System Health",     "check CPU load, RAM usage, disk capacity, and top 5 processes"),
    ("🔍 5: Explain Last Error", "read the last terminal error and explain root cause and fix"),
    ("📁 6: Audit Current Dir",  "inspect files, permissions, and security posture of current directory"),
]


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    with CONFIG_FILE.open() as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    cfg["api_key"] = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or cfg.get("api_key", "")
    )
    return cfg


# ---------------------------------------------------------------------------
# Styling: High-End Kali Cyberpunk Terminal Palette
# ---------------------------------------------------------------------------
APP_CSS = """
Screen {
    background: #090D16;
    layout: horizontal;
}

/* ── LEFT PANE: LIVE TERMINAL MIRROR ──────────────────────── */
#terminal-panel {
    width: 50%;
    layout: vertical;
    background: #050811;
    border-right: solid #1E293B;
}

#terminal-header {
    height: 3;
    background: #0D1322;
    border-bottom: solid #1E293B;
    layout: horizontal;
    align: left middle;
    padding: 0 1;
}

#terminal-title {
    color: #38BDF8;
    text-style: bold;
    width: auto;
}

#terminal-cwd {
    color: #64748B;
    margin-left: 2;
    width: 1fr;
}

#terminal-log {
    height: 1fr;
    background: #050811;
    color: #CBD5E1;
    padding: 0 1;
    scrollbar-color: #1E293B;
    scrollbar-background: #050811;
}

/* ── RIGHT PANE: AUTONOMOUS AGENT CONSOLE ─────────────────── */
#chat-panel {
    width: 50%;
    layout: vertical;
    background: #090D16;
}

#chat-header {
    height: 3;
    background: #0D1322;
    border-bottom: solid #1E293B;
    layout: horizontal;
    align: left middle;
    padding: 0 1;
}

#header-badge {
    color: #A855F7;
    text-style: bold;
    width: auto;
    margin-right: 1;
}

#model-select {
    width: 32;
    background: #111827;
    color: #E2E8F0;
    border: solid #1E293B;
}

#auto-label {
    color: #94A3B8;
    width: auto;
    margin-left: 1;
}

Switch {
    background: #111827;
    margin-left: 1;
}

#chat-feed {
    height: 1fr;
    background: #090D16;
    padding: 1;
    scrollbar-color: #1E293B;
    scrollbar-background: #090D16;
}

/* ── CONFIRMATION GATE CARD ──────────────────────────────── */
#confirm-area {
    height: auto;
    display: none;
    background: #19160B;
    border: solid #F59E0B;
    padding: 1 2;
    margin: 0 1 1 1;
}

#confirm-warning {
    color: #F59E0B;
    text-style: bold;
}

#confirm-cmd-label {
    color: #F8FAFC;
    margin-top: 1;
}

#confirm-buttons {
    layout: horizontal;
    height: 3;
    margin-top: 1;
}

#approve-btn {
    background: #10B981;
    color: #FFFFFF;
    text-style: bold;
    border: none;
    margin-right: 1;
}

#reject-btn {
    background: #EF4444;
    color: #FFFFFF;
    text-style: bold;
    border: none;
}

/* ── TACTICAL ACTION CHIPS ────────────────────────────────── */
#chips-row {
    height: auto;
    layout: horizontal;
    background: #0B101D;
    padding: 1;
    border-top: solid #1E293B;
    overflow-x: auto;
}

Button.chip {
    background: #111827;
    color: #94A3B8;
    border: solid #1E293B;
    min-width: 0;
    height: 3;
    margin-right: 1;
}

Button.chip:hover {
    background: #1E293B;
    color: #38BDF8;
    border: solid #38BDF8;
}

/* ── INPUT BOX & CONTROLS ─────────────────────────────────── */
#input-area {
    height: auto;
    background: #0D1322;
    border-top: solid #1E293B;
    padding: 1;
    layout: vertical;
}

#input-row {
    layout: horizontal;
    height: 3;
}

#user-input {
    width: 1fr;
    background: #111827;
    color: #F8FAFC;
    border: solid #1E293B;
}

#user-input:focus {
    border: solid #38BDF8;
}

#send-btn {
    background: #2563EB;
    color: #FFFFFF;
    text-style: bold;
    border: none;
    min-width: 10;
    margin-left: 1;
}

#send-btn:hover {
    background: #1D4ED8;
}

#stop-btn {
    background: #DC2626;
    color: #FFFFFF;
    text-style: bold;
    border: none;
    min-width: 15;
    margin-left: 1;
    display: none;
}

#hint {
    color: #475569;
    height: 1;
    margin-top: 0;
}

Footer {
    background: #0D1322;
    color: #64748B;
}
"""


# ---------------------------------------------------------------------------
# Main App Class
# ---------------------------------------------------------------------------
class KaliCopilotTUI(App):
    """
    KaliCopilot v2.0 — Production Terminal Autonomous Copilot.
    Directly wired to live tmux terminal pane with full ANSI rendering.
    """

    CSS = APP_CSS
    TITLE = "KaliCopilot"
    SUB_TITLE = "Autonomous Kali Linux AI Copilot"

    BINDINGS = [
        Binding("ctrl+c", "quit",         "Quit",        show=True),
        Binding("ctrl+l", "clear_chat",   "Clear Chat",  show=True),
        Binding("ctrl+s", "stop_agent",   "Stop Agent",  show=True),
        Binding("f2",     "toggle_auto",  "Auto Mode",   show=True),
        Binding("f5",     "force_refresh","Refresh",     show=True),
    ]

    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg
        self._history: list[AgentMessage] = []
        self._agent_thread: Optional[threading.Thread] = None
        self._working = False
        self._approve_cb: Optional[Callable] = None
        self._reject_cb:  Optional[Callable] = None
        self._last_pane_text: str = ""

        # Terminal bridge
        self._bridge = TmuxBridge()
        try:
            self._bridge.ensure_session()
        except Exception:
            pass

        # Agent configuration
        self._agent_config = AgentConfig(
            provider    = cfg["provider"],
            model       = cfg["model"],
            api_key     = cfg["api_key"],
            openai_base = cfg["openai_base"],
            ollama_base = cfg["ollama_base"],
            max_steps   = int(cfg["max_steps"]),
            auto_mode   = bool(cfg["auto_mode"]),
            temperature = float(cfg.get("temperature", 0.2)),
        )
        self._agent = self._build_agent()

    def _build_agent(self) -> KaliAgent:
        return KaliAgent(
            config=self._agent_config,
            bridge=self._bridge,
            plugin_dir=str(PLUGIN_DIR),
        )

    # ------------------------------------------------------------------
    # Compose TUI Layout
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        # LEFT PANE: Live terminal mirror
        with Vertical(id="terminal-panel"):
            with Horizontal(id="terminal-header"):
                yield Static("📟 LIVE TMUX PANE", id="terminal-title")
                yield Static(f"cwd: ~", id="terminal-cwd")
            yield RichLog(id="terminal-log", highlight=False, markup=False, wrap=True)

        # RIGHT PANE: Autonomous Agent Drawer
        with Vertical(id="chat-panel"):
            with Horizontal(id="chat-header"):
                yield Static("🐉 KALI COPILOT v2.0", id="header-badge")
                yield Select(
                    options=[(label, label) for label, _, _ in MODEL_PRESETS],
                    value=MODEL_PRESETS[0][0],
                    id="model-select",
                    allow_blank=False,
                )
                yield Static("Auto-Run", id="auto-label")
                yield Switch(value=False, id="auto-switch")

            # Chat feed
            yield RichLog(id="chat-feed", highlight=True, markup=True, wrap=True)

            # Interactive confirmation gate (pops up on sensitive commands)
            with Container(id="confirm-area"):
                yield Static("", id="confirm-warning")
                yield Static("", id="confirm-cmd-label")
                with Horizontal(id="confirm-buttons"):
                    yield Button("✔ Approve & Execute", id="approve-btn", variant="success")
                    yield Button("✘ Reject Command",     id="reject-btn",  variant="error")

            # Tactical Quick Action Chips
            with Horizontal(id="chips-row"):
                for label, prompt in TACTICAL_CHIPS:
                    yield Button(label, classes="chip", name=prompt)

            # Input area
            with Vertical(id="input-area"):
                with Horizontal(id="input-row"):
                    yield Input(
                        placeholder="Ask KaliCopilot or give a command goal… (Enter to send)",
                        id="user-input",
                    )
                    yield Button("Send ↵",        id="send-btn", variant="primary")
                    yield Button("🛑 Stop Agent", id="stop-btn", variant="error")
                yield Static(
                    "[dim]Shortcuts: [bold #38BDF8]Enter[/bold #38BDF8]: Send │ [bold #38BDF8]F2[/bold #38BDF8]: Auto Mode │ [bold #38BDF8]Ctrl+S[/bold #38BDF8]: Stop │ [bold #38BDF8]Ctrl+L[/bold #38BDF8]: Clear │ [bold #38BDF8]Ctrl+C[/bold #38BDF8]: Quit[/dim]",
                    id="hint",
                    markup=True,
                )

        yield Footer()

    # ------------------------------------------------------------------
    # App Mount & Init
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        # High-FPS terminal mirror loop (refreshes every 0.6s with zero flicker)
        self.set_interval(0.6, self._refresh_terminal)
        self.query_one("#user-input", Input).focus()

        # Render sleek greeting card
        banner_table = Table.grid(padding=1)
        banner_table.add_column(style="cyan", justify="right")
        banner_table.add_column(style="white")
        banner_table.add_row("Provider:", f"[bold cyan]{self._agent_config.provider}[/]")
        banner_table.add_row("Model:", f"[bold yellow]{self._agent_config.model}[/]")
        banner_table.add_row("Bridge:", "[bold green]tmux: kali_agent_session (active)[/]")
        banner_table.add_row("Mode:", "[yellow]Manual (Confirms commands)[/]")

        welcome_panel = Panel(
            banner_table,
            title="[bold #A855F7]⚡ KaliCopilot Autonomous Engine Ready[/bold #A855F7]",
            subtitle="[dim]Type a task below or click a tactical chip to start[/dim]",
            border_style="#38BDF8",
            padding=(1, 2),
        )
        self._append_chat(welcome_panel)

    # ------------------------------------------------------------------
    # High-Performance Terminal Pane Refresh (ANSI + Zero Flicker)
    # ------------------------------------------------------------------
    def _refresh_terminal(self) -> None:
        try:
            # Capture with -e for real terminal ANSI colors
            raw_ansi = self._bridge.capture_pane(lines=60, ansi=True)
            if raw_ansi == self._last_pane_text:
                return  # Skip repaint if unchanged (100% zero flicker!)

            self._last_pane_text = raw_ansi

            # Update CWD in header
            try:
                cwd = self._bridge.get_cwd() or "~"
                self.query_one("#terminal-cwd", Static).update(f"cwd: [cyan]{cwd}[/cyan]")
            except Exception:
                pass

            tlog = self.query_one("#terminal-log", RichLog)
            tlog.clear()

            # Render genuine Linux terminal color escape sequences via Rich Text.from_ansi
            ansi_text = Text.from_ansi(raw_ansi)
            tlog.write(ansi_text)
            tlog.scroll_end(animate=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Events & Actions
    # ------------------------------------------------------------------
    @on(Input.Submitted, "#user-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            event.input.value = ""
            self._send_message(text)

    @on(Button.Pressed, "#send-btn")
    def on_send_pressed(self) -> None:
        inp = self.query_one("#user-input", Input)
        text = inp.value.strip()
        if text:
            inp.value = ""
            self._send_message(text)

    @on(Button.Pressed, "#stop-btn")
    def on_stop_pressed(self) -> None:
        self.action_stop_agent()

    @on(Button.Pressed, "#approve-btn")
    def on_approve_pressed(self) -> None:
        cb = self._approve_cb
        self._hide_confirm()
        if cb:
            threading.Thread(target=cb, daemon=True).start()

    @on(Button.Pressed, "#reject-btn")
    def on_reject_pressed(self) -> None:
        cb = self._reject_cb
        self._hide_confirm()
        if cb:
            threading.Thread(target=cb, daemon=True).start()
        self._append_chat(Panel("[bold red]✘ Command rejected by operator.[/bold red]", border_style="red"))

    @on(Button.Pressed, ".chip")
    def on_chip_pressed(self, event: Button.Pressed) -> None:
        prompt = event.button.name or str(event.button.label)
        self._send_message(prompt)

    @on(Select.Changed, "#model-select")
    def on_model_changed(self, event: Select.Changed) -> None:
        label = str(event.value)
        for lbl, provider, model in MODEL_PRESETS:
            if lbl == label:
                self._agent_config.provider = provider
                self._agent_config.model    = model
                self._agent = self._build_agent()
                self._append_chat(
                    Panel(
                        f"Active Model updated: [bold yellow]{model}[/] via [cyan]{provider}[/]",
                        title="[dim]Model Switch[/dim]",
                        border_style="#1E293B",
                    )
                )
                break

    @on(Switch.Changed, "#auto-switch")
    def on_auto_toggled(self, event: Switch.Changed) -> None:
        self._agent_config.auto_mode = event.value
        self._agent = self._build_agent()
        if event.value:
            self._append_chat(
                Panel("[bold green]⚡ Autonomous Mode Enabled[/bold green]\n[dim]Safe commands execute instantly without manual approval.[/dim]", border_style="green")
            )
        else:
            self._append_chat(
                Panel("[bold yellow]🔒 Supervised Mode Active[/bold yellow]\n[dim]Agent requires operator confirmation before running shell commands.[/dim]", border_style="yellow")
            )

    def action_toggle_auto(self) -> None:
        sw = self.query_one("#auto-switch", Switch)
        sw.value = not sw.value

    def action_force_refresh(self) -> None:
        self._last_pane_text = ""
        self._refresh_terminal()

    def action_clear_chat(self) -> None:
        self.query_one("#chat-feed", RichLog).clear()
        self._history.clear()
        self._append_chat("[dim]Console history cleared.[/dim]")

    def action_stop_agent(self) -> None:
        self._agent.stop()
        self._append_chat(Panel("[bold red]🛑 Agent execution halted by operator.[/bold red]", border_style="red"))
        self._set_working(False)

    # ------------------------------------------------------------------
    # Dispatch & Message Execution
    # ------------------------------------------------------------------
    def _send_message(self, text: str) -> None:
        if self._working:
            self._append_chat("[bold yellow]⚠️ Agent is currently busy. Click Stop Agent or press Ctrl+S to abort.[/bold yellow]")
            return

        # Cloud API key check
        if self._agent_config.provider in ("gemini", "openai") and not self._agent_config.api_key:
            self._append_chat(Panel(
                f"[red]Missing API Key for [bold]{self._agent_config.provider}[/bold].\n\n"
                f"Export your key in your shell:\n"
                f"[cyan]export GEMINI_API_KEY='your_key'[/cyan]\n"
                f"[cyan]export OPENAI_API_KEY='your_key'[/cyan]\n"
                f"Or switch to local [bold yellow]Ollama: qwen2.5[/bold yellow] in the dropdown above.",
                title="[red]API Key Required[/red]",
                border_style="red",
            ))
            return

        # Format User Prompt
        self._append_chat(Panel(
            f"[bold white]{text}[/bold white]",
            title="[bold #38BDF8]❯ USER INTENT[/bold #38BDF8]",
            border_style="#38BDF8",
            padding=(0, 1),
        ))
        self._history.append(AgentMessage(role="user", content=text))
        self._set_working(True)

        # Background Agent Loop Callbacks
        def on_message(msg: AgentMessage):
            if msg.command_result:
                r = msg.command_result
                ec_color = "green" if r.exit_code == 0 else "red"
                ec_label = f"✔ EXIT {r.exit_code}" if r.exit_code == 0 else f"✘ EXIT {r.exit_code}"

                # Syntax-highlight the command
                syntax_cmd = Syntax(r.command, "bash", theme="monokai", line_numbers=False)

                table = Table.grid(padding=1)
                table.add_column(style="bold")
                table.add_column()
                table.add_row("Status:", f"[bold {ec_color}]{ec_label}[/]  │  Duration: [dim]{r.duration_s:.2f}s[/dim]")
                if r.timed_out:
                    table.add_row("Warning:", "[bold yellow]⏱️ Execution Timed Out[/bold yellow]")
                if r.password_prompt:
                    table.add_row("Prompt:", "[bold yellow]🔑 Sudo password detected! Enter password in left terminal pane.[/bold yellow]")

                # Clean preview output
                clean_output = r.output[-2500:] if r.output else "[dim](no stdout/stderr output)[/dim]"
                output_syntax = Syntax(clean_output, "bash", theme="monokai", word_wrap=True)

                card = Panel(
                    Vertical(
                        syntax_cmd,
                        table,
                        Panel(output_syntax, title="[dim]Terminal Output[/dim]", border_style="#1E293B", padding=(0, 1)),
                    ),
                    title=f"[bold #F59E0B]⚡ COMMAND CARD[/bold #F59E0B]",
                    border_style=ec_color,
                    padding=(0, 1),
                )
                self.call_from_thread(self._append_chat, card)

            elif msg.role == "assistant":
                self.call_from_thread(
                    self._append_chat,
                    Panel(
                        msg.content,
                        title="[bold #A855F7]🤖 COPILOT ANALYSIS[/bold #A855F7]",
                        border_style="#A855F7",
                        padding=(1, 2),
                    ),
                )

        def on_confirm(command: str, warning: str, approve_cb: Callable, reject_cb: Callable):
            self.call_from_thread(self._show_confirm, command, warning, approve_cb, reject_cb)

        def on_step(step: int, max_steps: int):
            self.call_from_thread(
                self._append_chat,
                f"[dim]⚡ Running ReAct Step {step}/{max_steps}...[/dim]",
            )

        def on_done():
            self.call_from_thread(self._set_working, False)

        def on_error(err: str):
            self.call_from_thread(
                self._append_chat,
                Panel(f"[bold red]✘ {err}[/bold red]", title="[red]Execution Error[/red]", border_style="red"),
            )
            self.call_from_thread(self._set_working, False)

        self._agent_thread = self._agent.run_async(
            goal=text,
            history=self._history[:-1],
            callbacks={
                "on_message":         on_message,
                "on_confirm_request": on_confirm,
                "on_step":            on_step,
                "on_done":            on_done,
                "on_error":           on_error,
                "on_tool_start":      lambda n, a: None,
                "on_tool_result":     lambda r: None,
            },
        )

    # ------------------------------------------------------------------
    # UI Helpers
    # ------------------------------------------------------------------
    def _append_chat(self, content) -> None:
        feed = self.query_one("#chat-feed", RichLog)
        feed.write(content)

    def _set_working(self, working: bool) -> None:
        self._working = working
        stop_btn = self.query_one("#stop-btn", Button)
        send_btn = self.query_one("#send-btn", Button)
        if working:
            stop_btn.styles.display = "block"
            send_btn.disabled = True
        else:
            stop_btn.styles.display = "none"
            send_btn.disabled = False
            self.query_one("#user-input", Input).focus()

    def _show_confirm(self, command: str, warning: str, approve_cb: Callable, reject_cb: Callable) -> None:
        self._approve_cb = approve_cb
        self._reject_cb  = reject_cb
        area = self.query_one("#confirm-area", Container)
        self.query_one("#confirm-warning", Static).update(f"⚠️  {warning}")
        self.query_one("#confirm-cmd-label", Static).update(f"$ [bold cyan]{command}[/bold cyan]")
        area.styles.display = "block"

        self._append_chat(Panel(
            f"[bold yellow]⚠️ OPERATOR CONFIRMATION REQUESTED[/bold yellow]\n\n"
            f"Command: [bold cyan]$ {command}[/bold cyan]\n"
            f"Reason:  [dim]{warning}[/dim]\n\n"
            f"Select [bold green]Approve[/bold green] or [bold red]Reject[/bold red] above ↑",
            title="[bold yellow]Security Gate[/bold yellow]",
            border_style="#F59E0B",
        ))

    def _hide_confirm(self) -> None:
        self.query_one("#confirm-area", Container).styles.display = "none"
        self._approve_cb = None
        self._reject_cb  = None


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
def main():
    cfg = load_config()
    app = KaliCopilotTUI(cfg)
    app.run()


if __name__ == "__main__":
    main()
