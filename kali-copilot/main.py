"""
main.py
-------
KaliCopilot — Entry point.

Responsibilities
  1. Load / create ~/.config/kali-copilot/config.json
  2. Ensure the tmux session exists
  3. Tile the existing terminal window to the left half
  4. Launch the PyQt6 application:
       - FloatingButton (bottom-right, always-on-top)
       - AgentDrawer    (right 50%, hidden initially)
  5. Wire signals between UI ↔ KaliAgent
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtWidgets import QApplication

from core.agent import AgentConfig, AgentMessage, KaliAgent
from core.terminal_bridge import TmuxBridge
from ui.floating_button import FloatingButton
from ui.drawer import AgentDrawer

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("kali-copilot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_DIR  = Path.home() / ".config" / "kali-copilot"
CONFIG_FILE = CONFIG_DIR / "config.json"
PLUGIN_DIR  = CONFIG_DIR / "plugins"

DEFAULT_CONFIG = {
    "provider":    "gemini",
    "model":       "gemini-1.5-flash",
    "api_key":     "",
    "openai_base": "https://api.openai.com/v1",
    "ollama_base": "http://localhost:11434",
    "max_steps":   10,
    "auto_mode":   False,
    "temperature": 0.3,
}


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        logger.info("Created default config at %s", CONFIG_FILE)

    with CONFIG_FILE.open() as f:
        cfg = json.load(f)

    # Merge with defaults for any missing keys
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)

    # Allow env-var overrides
    cfg["api_key"] = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or cfg.get("api_key", "")
    )
    return cfg


def build_agent_config(cfg: dict) -> AgentConfig:
    return AgentConfig(
        provider    = cfg["provider"],
        model       = cfg["model"],
        api_key     = cfg["api_key"],
        openai_base = cfg["openai_base"],
        ollama_base = cfg["ollama_base"],
        max_steps   = int(cfg["max_steps"]),
        auto_mode   = bool(cfg["auto_mode"]),
        temperature = float(cfg["temperature"]),
    )


# ---------------------------------------------------------------------------
# Qt signal bridge (thread-safe callbacks → main thread)
# ---------------------------------------------------------------------------
class _SignalBridge(QObject):
    """
    Bridges agent callbacks (running in background thread) to Qt signals
    so we can safely update widgets from the main GUI thread.
    """
    new_message      = pyqtSignal(str, str)          # role, content
    command_result   = pyqtSignal(str, int, str, float)  # cmd, exit_code, output, dur
    confirm_request  = pyqtSignal(str, str, object, object)  # cmd, warning, approve_cb, reject_cb
    agent_done       = pyqtSignal()
    agent_error      = pyqtSignal(str)
    step_update      = pyqtSignal(int, int)           # step, max_steps
    working_changed  = pyqtSignal(bool)


# ---------------------------------------------------------------------------
# App controller
# ---------------------------------------------------------------------------
class KaliCopilotApp:
    """Owns all top-level objects and wires them together."""

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._history: list[AgentMessage] = []
        self._agent_thread: Optional[threading.Thread] = None

        # --- Core ---
        self._bridge = TmuxBridge()
        self._agent_config = build_agent_config(cfg)
        self._agent = KaliAgent(
            config     = self._agent_config,
            bridge     = self._bridge,
            plugin_dir = str(PLUGIN_DIR),
        )

        # --- Signal bridge ---
        self._signals = _SignalBridge()

        # --- UI ---
        self._btn    = FloatingButton()
        self._drawer = AgentDrawer()

        self._connect_signals()

        # --- Show the floating button ---
        self._btn.show()
        self._btn.raise_()
        self._btn.activateWindow()

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------
    def _connect_signals(self):
        # Floating button
        self._btn.toggled.connect(self._on_toggle)

        # Drawer → App
        self._drawer.send_requested.connect(self._on_send)
        self._drawer.stop_requested.connect(self._on_stop)
        self._drawer.model_changed.connect(self._on_model_changed)
        self._drawer.auto_mode_changed.connect(self._on_auto_mode_changed)
        self._drawer.clear_requested.connect(self._on_clear)
        self._drawer.rerun_requested.connect(lambda cmd: self._on_send(cmd))
        self._drawer.quick_action.connect(self._on_send)

        # Signal bridge → Drawer (all in main thread)
        self._signals.new_message.connect(self._drawer.show_message)
        self._signals.command_result.connect(self._drawer.show_command_card)
        self._signals.confirm_request.connect(self._drawer.show_confirm)
        self._signals.agent_done.connect(self._on_agent_done)
        self._signals.agent_error.connect(self._on_agent_error)
        self._signals.working_changed.connect(self._on_working_changed)

    # ------------------------------------------------------------------
    # UI handlers
    # ------------------------------------------------------------------
    def _on_toggle(self, open_: bool):
        if open_:
            self._drawer.show()
            self._drawer.raise_()
            self._tile_terminal()
        else:
            self._drawer.hide()

    def _tile_terminal(self):
        """Attempt to tile the terminal to left 50% (best-effort)."""
        t = threading.Thread(
            target=TmuxBridge.tile_terminal_left, daemon=True
        )
        t.start()

    def _on_send(self, text: str):
        if not text.strip():
            return
        if self._agent_thread and self._agent_thread.is_alive():
            self._drawer.show_message("assistant", "⚠️ Agent is still running. Click 🛑 Stop Agent first.")
            return

        # Validate API key for cloud providers
        provider = self._agent_config.provider
        if provider in ("gemini", "openai") and not self._agent_config.api_key:
            self._drawer.show_message(
                "assistant",
                f"⚠️ No API key set for **{provider}**.\n\n"
                f"Set the environment variable:\n"
                f"- Gemini: `export GEMINI_API_KEY=your_key`\n"
                f"- OpenAI: `export OPENAI_API_KEY=your_key`\n\n"
                f"Or edit `~/.config/kali-copilot/config.json` and restart.",
            )
            return

        # Show user message
        self._drawer.show_message("user", text)
        user_msg = AgentMessage(role="user", content=text)
        self._history.append(user_msg)

        # Set working state
        self._signals.working_changed.emit(True)
        self._btn.set_working(True)

        # Build callbacks
        def on_message(msg: AgentMessage):
            if msg.command_result:
                r = msg.command_result
                self._signals.command_result.emit(r.command, r.exit_code, r.output, r.duration_s)
            else:
                self._signals.new_message.emit(msg.role, msg.content)

        def on_tool_start(name, args):
            pass  # Could show a "running tool…" indicator if desired

        def on_tool_result(result):
            pass  # Handled via on_message command_result path

        def on_confirm(command, warning, approve_cb, reject_cb):
            self._signals.confirm_request.emit(command, warning, approve_cb, reject_cb)

        def on_step(step, max_steps):
            self._signals.step_update.emit(step, max_steps)

        def on_done():
            self._signals.agent_done.emit()

        def on_error(err):
            self._signals.agent_error.emit(err)

        callbacks = {
            "on_message":        on_message,
            "on_tool_start":     on_tool_start,
            "on_tool_result":    on_tool_result,
            "on_confirm_request": on_confirm,
            "on_step":           on_step,
            "on_done":           on_done,
            "on_error":          on_error,
        }

        self._agent_thread = self._agent.run_async(
            goal=text,
            history=self._history[:-1],  # pass all history except last (goal) which agent prepends
            callbacks=callbacks,
        )

    def _on_stop(self):
        self._agent.stop()

    def _on_model_changed(self, provider: str, model_name: str):
        self._agent_config.provider = provider
        self._agent_config.model    = model_name
        self._agent = KaliAgent(
            config=self._agent_config,
            bridge=self._bridge,
            plugin_dir=str(PLUGIN_DIR),
        )
        logger.info("Switched to %s / %s", provider, model_name)

    def _on_auto_mode_changed(self, enabled: bool):
        self._agent_config.auto_mode = enabled
        # Rebuild agent with new config
        self._agent = KaliAgent(
            config=self._agent_config,
            bridge=self._bridge,
            plugin_dir=str(PLUGIN_DIR),
        )

    def _on_clear(self):
        self._history.clear()

    def _on_agent_done(self):
        self._signals.working_changed.emit(False)
        self._btn.set_working(False)

    def _on_agent_error(self, err: str):
        self._drawer.show_message("assistant", f"❌ **Agent error:** {err}")
        self._signals.working_changed.emit(False)
        self._btn.set_working(False)

    def _on_working_changed(self, working: bool):
        self._drawer.set_working(working)
        self._btn.set_working(working)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    logger.info("KaliCopilot starting…")

    cfg = load_config()
    logger.info("Config loaded: provider=%s, model=%s", cfg["provider"], cfg["model"])

    # Set up tmux session
    bridge = TmuxBridge()
    try:
        created = bridge.ensure_session()
        if created:
            logger.info("New tmux session '%s' created.", bridge.session)
        else:
            logger.info("Attached to existing tmux session '%s'.", bridge.session)
    except Exception as e:
        logger.warning("tmux session setup failed: %s — continuing without terminal bridge.", e)

    # --- PyQt6 Application ---
    app = QApplication(sys.argv)
    app.setApplicationName("KaliCopilot")
    app.setApplicationDisplayName("KaliCopilot")

    # Dark-mode palette
    from PyQt6.QtGui import QPalette, QColor
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window,     QColor("#0F1117"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#E2E8F0"))
    palette.setColor(QPalette.ColorRole.Base,       QColor("#1A1D27"))
    palette.setColor(QPalette.ColorRole.Text,       QColor("#E2E8F0"))
    app.setPalette(palette)

    controller = KaliCopilotApp(cfg)

    logger.info("KaliCopilot is running. Click ✨ Ask AI in the bottom-right corner.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
