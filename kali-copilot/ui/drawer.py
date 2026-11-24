"""
ui/drawer.py
------------
Half-screen PyQt6 dark-mode Agent Drawer.

Layout (top → bottom):
  ┌────────────────────────────────────────────┐
  │  Header: title | model selector | toggles   │
  ├────────────────────────────────────────────┤
  │  Chat & Execution Feed (scrollable)         │
  │  · User messages                           │
  │  · Assistant markdown responses            │
  │  · Command Cards (cmd | exit | output)     │
  │  · Confirm widgets (Approve / Reject)      │
  ├────────────────────────────────────────────┤
  │  Quick-action chips                        │
  ├────────────────────────────────────────────┤
  │  Input area  [Stop Agent]  [Send ↵]        │
  └────────────────────────────────────────────┘
"""

from __future__ import annotations

import html
import textwrap
from typing import Callable, Optional

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    QSize, pyqtSignal, QThread, pyqtSlot
)
from PyQt6.QtGui import (
    QColor, QFont, QPalette, QTextCursor, QIcon,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QComboBox, QCheckBox, QTextEdit,
    QFrame, QSizePolicy, QApplication, QSplitter,
    QToolButton, QSpacerItem,
)


# ---------------------------------------------------------------------------
# Dark theme palette
# ---------------------------------------------------------------------------
BG_DARK      = "#0F1117"
BG_CARD      = "#1A1D27"
BG_INPUT     = "#1E2130"
BORDER_DARK  = "#2D3148"
ACCENT_BLUE  = "#3B82F6"
ACCENT_VIOLET= "#8B5CF6"
TEXT_PRIMARY = "#E2E8F0"
TEXT_MUTED   = "#64748B"
SUCCESS_GRN  = "#10B981"
ERROR_RED    = "#EF4444"
AMBER        = "#F59E0B"


# ---------------------------------------------------------------------------
# Utility: simple markdown → HTML (single-pass, no dependency)
# ---------------------------------------------------------------------------
def _md_to_html(text: str) -> str:
    """Convert a small subset of Markdown to HTML for QTextBrowser."""
    import re
    # Code blocks
    text = re.sub(
        r"```(\w*)\n(.*?)```",
        lambda m: (
            f'<pre style="background:#0a0d14;border-radius:6px;padding:10px;'
            f'overflow-x:auto;font-family:monospace;font-size:12px;">'
            f'{html.escape(m.group(2))}</pre>'
        ),
        text, flags=re.DOTALL,
    )
    # Inline code
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#1e2130;padding:1px 4px;border-radius:3px;font-family:monospace;">\1</code>',
        text,
    )
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # Headers
    text = re.sub(r"^### (.+)$", r'<h3 style="color:#8B5CF6;">\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$",  r'<h2 style="color:#8B5CF6;">\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r"^# (.+)$",   r'<h1 style="color:#8B5CF6;">\1</h1>', text, flags=re.MULTILINE)
    # Horizontal rule
    text = re.sub(r"^---+$", "<hr>", text, flags=re.MULTILINE)
    # Newlines → <br>
    text = text.replace("\n", "<br>")
    return text


# ---------------------------------------------------------------------------
# Reusable card widgets
# ---------------------------------------------------------------------------
class CommandCard(QFrame):
    """Displays a single shell command execution with status badge and collapsible output."""

    rerun_requested = pyqtSignal(str)

    def __init__(self, command: str, exit_code: int, output: str, duration: float = 0.0):
        super().__init__()
        self.command   = command
        self.exit_code = exit_code
        self._expanded = False

        self.setObjectName("CommandCard")
        self.setStyleSheet(f"""
            #CommandCard {{
                background: {BG_CARD};
                border: 1px solid {BORDER_DARK};
                border-radius: 8px;
                padding: 2px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # --- top row: $ cmd  |  badge  |  duration  |  copy  ---
        top = QHBoxLayout()

        prompt = QLabel("$")
        prompt.setStyleSheet(f"color:{ACCENT_BLUE};font-family:monospace;font-weight:bold;")

        cmd_lbl = QLabel(textwrap.shorten(command, width=72, placeholder="…"))
        cmd_lbl.setStyleSheet(f"color:{TEXT_PRIMARY};font-family:monospace;font-size:12px;")
        cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        badge_color = SUCCESS_GRN if exit_code == 0 else ERROR_RED
        badge = QLabel(f" {exit_code} ")
        badge.setStyleSheet(
            f"background:{badge_color};color:#fff;border-radius:4px;"
            f"font-size:10px;font-weight:bold;padding:1px 4px;"
        )

        dur_lbl = QLabel(f"{duration:.1f}s")
        dur_lbl.setStyleSheet(f"color:{TEXT_MUTED};font-size:10px;")

        copy_btn = QToolButton()
        copy_btn.setText("📋")
        copy_btn.setToolTip("Copy command")
        copy_btn.setStyleSheet("background:transparent;border:none;")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(command))

        rerun_btn = QToolButton()
        rerun_btn.setText("▶")
        rerun_btn.setToolTip("Re-run command")
        rerun_btn.setStyleSheet(f"background:transparent;border:none;color:{ACCENT_BLUE};")
        rerun_btn.clicked.connect(lambda: self.rerun_requested.emit(command))

        expand_btn = QToolButton()
        expand_btn.setText("▼ Output")
        expand_btn.setStyleSheet(f"background:transparent;border:none;color:{TEXT_MUTED};font-size:10px;")
        expand_btn.clicked.connect(self._toggle_output)

        top.addWidget(prompt)
        top.addWidget(cmd_lbl, 1)
        top.addWidget(badge)
        top.addWidget(dur_lbl)
        top.addWidget(copy_btn)
        top.addWidget(rerun_btn)
        top.addWidget(expand_btn)
        layout.addLayout(top)

        # --- collapsible output ---
        self._output_widget = QLabel()
        self._output_widget.setText(
            f'<pre style="font-family:monospace;font-size:11px;color:{TEXT_MUTED};'
            f'white-space:pre-wrap;word-break:break-all;">'
            f'{html.escape(output[-2000:])}</pre>'
        )
        self._output_widget.setTextFormat(Qt.TextFormat.RichText)
        self._output_widget.setWordWrap(True)
        self._output_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._output_widget.setVisible(False)
        self._expand_btn = expand_btn
        layout.addWidget(self._output_widget)

    def _toggle_output(self):
        self._expanded = not self._expanded
        self._output_widget.setVisible(self._expanded)
        self._expand_btn.setText("▲ Output" if self._expanded else "▼ Output")


class ConfirmWidget(QFrame):
    """Approval widget shown when the agent requests user confirmation."""

    approved = pyqtSignal()
    rejected = pyqtSignal()

    def __init__(self, command: str, warning: str):
        super().__init__()
        self.setObjectName("ConfirmWidget")
        self.setStyleSheet(f"""
            #ConfirmWidget {{
                background: #1c1a0f;
                border: 1px solid {AMBER};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        icon_lbl = QLabel("⚠️  Approval Required")
        icon_lbl.setStyleSheet(f"color:{AMBER};font-weight:bold;font-size:13px;")

        warn_lbl = QLabel(warning)
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet(f"color:{TEXT_PRIMARY};")

        cmd_lbl = QLabel(f'<code style="background:#0a0d14;padding:4px 8px;border-radius:4px;">{html.escape(command)}</code>')
        cmd_lbl.setTextFormat(Qt.TextFormat.RichText)
        cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        btn_row = QHBoxLayout()
        approve_btn = QPushButton("✅  Approve & Run")
        approve_btn.setStyleSheet(
            f"background:{SUCCESS_GRN};color:#fff;border:none;border-radius:6px;"
            f"padding:6px 14px;font-weight:bold;"
        )
        approve_btn.clicked.connect(self._approve)

        reject_btn = QPushButton("❌  Reject")
        reject_btn.setStyleSheet(
            f"background:{ERROR_RED};color:#fff;border:none;border-radius:6px;"
            f"padding:6px 14px;font-weight:bold;"
        )
        reject_btn.clicked.connect(self._reject)

        btn_row.addWidget(approve_btn)
        btn_row.addWidget(reject_btn)
        btn_row.addStretch()

        layout.addWidget(icon_lbl)
        layout.addWidget(warn_lbl)
        layout.addWidget(cmd_lbl)
        layout.addLayout(btn_row)

    def _approve(self):
        self.setEnabled(False)
        self.approved.emit()

    def _reject(self):
        self.setEnabled(False)
        self.rejected.emit()


# ---------------------------------------------------------------------------
# Bubble (user / assistant text)
# ---------------------------------------------------------------------------
class BubbleWidget(QLabel):
    def __init__(self, content: str, role: str):
        super().__init__()
        is_user = role == "user"
        bg   = "#1e3a5f" if is_user else BG_CARD
        border = ACCENT_BLUE if is_user else BORDER_DARK
        self.setStyleSheet(
            f"background:{bg};border:1px solid {border};border-radius:10px;"
            f"padding:10px 14px;color:{TEXT_PRIMARY};"
        )
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setText(_md_to_html(content))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setAlignment(
            Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft
        )


# ---------------------------------------------------------------------------
# Feed container
# ---------------------------------------------------------------------------
class ChatFeed(QScrollArea):
    """Scrollable vertical feed of chat bubbles and command cards."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"background:{BG_DARK};border:none;")

        self._container = QWidget()
        self._container.setStyleSheet(f"background:{BG_DARK};")
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)
        self._layout.addStretch()

        self.setWidget(self._container)

    def _insert_before_stretch(self, widget: QWidget):
        idx = self._layout.count() - 1   # before the trailing stretch
        self._layout.insertWidget(idx, widget)
        # Scroll to bottom
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))

    def add_bubble(self, content: str, role: str):
        w = BubbleWidget(content, role)
        row = QHBoxLayout()
        if role == "user":
            row.addStretch()
        row.addWidget(w, 0 if role == "user" else 1)
        if role != "user":
            row.addStretch()
        frame = QWidget()
        frame.setLayout(row)
        self._insert_before_stretch(frame)

    def add_command_card(
        self,
        command: str,
        exit_code: int,
        output: str,
        duration: float = 0.0,
        rerun_cb: Optional[Callable[[str], None]] = None,
    ) -> CommandCard:
        card = CommandCard(command, exit_code, output, duration)
        if rerun_cb:
            card.rerun_requested.connect(rerun_cb)
        self._insert_before_stretch(card)
        return card

    def add_confirm_widget(
        self,
        command: str,
        warning: str,
        approve_cb: Callable,
        reject_cb: Callable,
    ) -> ConfirmWidget:
        cw = ConfirmWidget(command, warning)
        cw.approved.connect(approve_cb)
        cw.rejected.connect(reject_cb)
        self._insert_before_stretch(cw)
        return cw

    def add_thinking_indicator(self) -> QLabel:
        dots = QLabel("⚙️  Agent thinking…")
        dots.setObjectName("thinking")
        dots.setStyleSheet(f"color:{TEXT_MUTED};font-style:italic;")
        self._insert_before_stretch(dots)
        return dots

    def remove_widget(self, widget: QWidget):
        self._layout.removeWidget(widget)
        widget.deleteLater()

    def clear(self):
        while self._layout.count() > 1:  # keep the trailing stretch
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


# ---------------------------------------------------------------------------
# Main Drawer
# ---------------------------------------------------------------------------
class AgentDrawer(QWidget):
    """
    Half-screen slide-in drawer.

    Public API
    ----------
    show_message(role, content)
    show_command_card(command, exit_code, output, duration)
    show_confirm(command, warning, approve_cb, reject_cb)
    set_working(bool)
    clear()

    Signals
    -------
    send_requested(text: str)
    stop_requested()
    model_changed(provider: str, model_name: str)
    auto_mode_changed(enabled: bool)
    clear_requested()
    rerun_requested(command: str)
    quick_action(text: str)
    """

    send_requested   = pyqtSignal(str)
    stop_requested   = pyqtSignal()
    model_changed    = pyqtSignal(str, str)
    auto_mode_changed = pyqtSignal(bool)
    clear_requested  = pyqtSignal()
    rerun_requested  = pyqtSignal(str)
    quick_action     = pyqtSignal(str)

    # Model presets: (display, provider, model_name)
    MODEL_PRESETS = [
        ("Gemini 1.5 Flash",   "gemini", "gemini-1.5-flash"),
        ("Gemini 1.5 Pro",     "gemini", "gemini-1.5-pro"),
        ("Gemini 2.0 Flash",   "gemini", "gemini-2.0-flash"),
        ("GPT-4o",             "openai", "gpt-4o"),
        ("GPT-4o mini",        "openai", "gpt-4o-mini"),
        ("Ollama (qwen2.5) ✅", "ollama", "qwen2.5"),
        ("Ollama (llama3.1)",  "ollama", "llama3.1"),
        ("Ollama (llama3)",    "ollama", "llama3"),
        ("Ollama (mistral)",   "ollama", "mistral"),
        ("Ollama (codellama)", "ollama", "codellama"),
    ]

    QUICK_CHIPS = [
        "Explain last error",
        "System status",
        "List open ports",
        "Show running processes",
        "Network interfaces",
        "Disk usage",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thinking_lbl: Optional[QLabel] = None
        self._working = False

        self._setup_window()
        self._build_ui()
        self._apply_dark_stylesheet()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            w = geom.width() // 2
            h = geom.height()
            x = geom.x() + geom.width() // 2
            y = geom.y()
            self.setGeometry(x, y, w, h)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header ----
        root.addWidget(self._make_header())

        # ---- Divider ----
        root.addWidget(self._hline())

        # ---- Chat feed ----
        self.feed = ChatFeed()
        root.addWidget(self.feed, 1)

        # ---- Quick action chips ----
        root.addWidget(self._make_chips())

        # ---- Divider ----
        root.addWidget(self._hline())

        # ---- Input area ----
        root.addWidget(self._make_input_area())

    def _make_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setObjectName("Header")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(10)

        icon = QLabel("🤖")
        icon.setStyleSheet("font-size:18px;")
        title = QLabel("KaliCopilot")
        title.setStyleSheet(
            f"color:{TEXT_PRIMARY};font-size:14px;font-weight:bold;"
            f"font-family:'Segoe UI',sans-serif;"
        )

        # Model selector
        self.model_combo = QComboBox()
        for display, _, _ in self.MODEL_PRESETS:
            self.model_combo.addItem(display)
        self.model_combo.setStyleSheet(
            f"background:{BG_INPUT};color:{TEXT_PRIMARY};border:1px solid {BORDER_DARK};"
            f"border-radius:5px;padding:3px 8px;font-size:11px;min-width:140px;"
        )
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)

        # Auto mode toggle
        self.auto_toggle = QCheckBox("Auto-run")
        self.auto_toggle.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px;")
        self.auto_toggle.toggled.connect(self.auto_mode_changed.emit)
        self.auto_toggle.setToolTip(
            "Auto-run: safe commands execute automatically.\n"
            "Manual: agent asks for approval on every command."
        )

        # Clear button
        clear_btn = QPushButton("🗑 Clear")
        clear_btn.setStyleSheet(
            f"background:transparent;color:{TEXT_MUTED};border:none;font-size:11px;"
        )
        clear_btn.clicked.connect(self._on_clear)

        h.addWidget(icon)
        h.addWidget(title)
        h.addStretch()
        h.addWidget(self.model_combo)
        h.addWidget(self.auto_toggle)
        h.addWidget(clear_btn)
        return bar

    def _make_chips(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 4, 12, 4)
        h.setSpacing(6)
        for chip_text in self.QUICK_CHIPS:
            btn = QPushButton(chip_text)
            btn.setStyleSheet(
                f"background:{BG_CARD};color:{TEXT_MUTED};border:1px solid {BORDER_DARK};"
                f"border-radius:12px;padding:3px 10px;font-size:10px;"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, t=chip_text: self.quick_action.emit(t))
            h.addWidget(btn)
        h.addStretch()
        return bar

    def _make_input_area(self) -> QWidget:
        area = QWidget()
        area.setObjectName("InputArea")
        v = QVBoxLayout(area)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        # Text box
        self.input_box = _AutoResizeTextEdit()
        self.input_box.setPlaceholderText("Ask anything about your Kali terminal… (Enter to send, Shift+Enter for newline)")
        self.input_box.setStyleSheet(
            f"background:{BG_INPUT};color:{TEXT_PRIMARY};border:1px solid {BORDER_DARK};"
            f"border-radius:8px;padding:8px;font-size:12px;font-family:'Consolas',monospace;"
        )
        self.input_box.send_signal.connect(self._on_send)

        # Buttons row
        btn_row = QHBoxLayout()

        self.stop_btn = QPushButton("🛑 Stop Agent")
        self.stop_btn.setStyleSheet(
            f"background:{ERROR_RED};color:#fff;border:none;border-radius:6px;"
            f"padding:7px 14px;font-weight:bold;font-size:12px;"
        )
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)

        self.send_btn = QPushButton("Send  ↵")
        self.send_btn.setStyleSheet(
            f"background:{ACCENT_BLUE};color:#fff;border:none;border-radius:6px;"
            f"padding:7px 20px;font-weight:bold;font-size:12px;"
        )
        self.send_btn.clicked.connect(self._on_send)

        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.send_btn)

        v.addWidget(self.input_box)
        v.addLayout(btn_row)
        return area

    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"border-top:1px solid {BORDER_DARK};")
        line.setFixedHeight(1)
        return line

    def _apply_dark_stylesheet(self):
        self.setStyleSheet(f"""
            AgentDrawer, QWidget {{
                background: {BG_DARK};
            }}
            #Header {{
                background: {BG_CARD};
                border-bottom: 1px solid {BORDER_DARK};
            }}
            #InputArea {{
                background: {BG_CARD};
                border-top: 1px solid {BORDER_DARK};
            }}
            QScrollBar:vertical {{
                background: {BG_DARK};
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER_DARK};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def show_message(self, role: str, content: str):
        """Add a user or assistant text bubble."""
        self._remove_thinking()
        self.feed.add_bubble(content, role)

    def show_command_card(
        self,
        command: str,
        exit_code: int,
        output: str,
        duration: float = 0.0,
    ):
        """Add a command execution card."""
        self._remove_thinking()
        self.feed.add_command_card(
            command, exit_code, output, duration,
            rerun_cb=lambda cmd: self.rerun_requested.emit(cmd),
        )

    def show_confirm(
        self,
        command: str,
        warning: str,
        approve_cb: Callable,
        reject_cb: Callable,
    ):
        """Add a confirmation widget to the feed."""
        self._remove_thinking()
        self.feed.add_confirm_widget(command, warning, approve_cb, reject_cb)

    def set_working(self, working: bool):
        self._working = working
        self.stop_btn.setVisible(working)
        self.send_btn.setEnabled(not working)
        if working and not self._thinking_lbl:
            self._thinking_lbl = self.feed.add_thinking_indicator()
        elif not working:
            self._remove_thinking()

    def clear(self):
        self.feed.clear()

    def get_selected_model(self) -> tuple[str, str]:
        """Return (provider, model_name) for the selected preset."""
        idx = self.model_combo.currentIndex()
        _, provider, model_name = self.MODEL_PRESETS[idx]
        return provider, model_name

    def get_auto_mode(self) -> bool:
        return self.auto_toggle.isChecked()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_send(self):
        text = self.input_box.toPlainText().strip()
        if text:
            self.input_box.clear()
            self.send_requested.emit(text)

    def _on_clear(self):
        self.clear()
        self.clear_requested.emit()

    def _on_model_changed(self, idx: int):
        _, provider, model_name = self.MODEL_PRESETS[idx]
        self.model_changed.emit(provider, model_name)

    def _remove_thinking(self):
        if self._thinking_lbl:
            self.feed.remove_widget(self._thinking_lbl)
            self._thinking_lbl = None


# ---------------------------------------------------------------------------
# Auto-resize text edit with Enter-to-send
# ---------------------------------------------------------------------------
class _AutoResizeTextEdit(QTextEdit):
    send_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(120)
        self.setMinimumHeight(44)
        self.document().contentsChanged.connect(self._adjust_height)

    def _adjust_height(self):
        doc_h = int(self.document().size().height())
        new_h = max(44, min(doc_h + 16, 120))
        self.setFixedHeight(new_h)

    def keyPressEvent(self, event):  # noqa: N802
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_signal.emit()
        else:
            super().keyPressEvent(event)
