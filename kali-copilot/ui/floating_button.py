"""
ui/floating_button.py
---------------------
Always-on-top floating pill button ("✨ Ask AI") that sits in the bottom-right
corner of the screen and toggles the AgentDrawer on click.

Visual states
  idle      →  "✨ Ask AI"           (electric blue gradient)
  open      →  "✖ Close"            (purple)
  working   →  "⚙ Working…"         (amber pulse animation)
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

from PyQt6.QtCore import (
    Qt, QPoint, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QRect,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QFont, QCursor
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
IDLE_GRAD_START  = QColor("#3B82F6")   # blue-500
IDLE_GRAD_END    = QColor("#8B5CF6")   # violet-500
OPEN_COLOUR      = QColor("#7C3AED")   # violet-600
WORKING_COLOUR_A = QColor("#F59E0B")   # amber-400
WORKING_COLOUR_B = QColor("#EF4444")   # red-400
TEXT_COLOUR      = QColor("#FFFFFF")
SHADOW_COLOUR    = QColor(0, 0, 0, 80)

BUTTON_W   = 130
BUTTON_H   = 44
CORNER_R   = 22    # pill radius
MARGIN_R   = 24    # px from right edge
MARGIN_B   = 72    # px from bottom edge (above taskbar)


class FloatingButton(QWidget):
    """
    Frameless, always-on-top pill button.
    Emits `toggled` signal (open: bool) on click.
    """
    toggled = pyqtSignal(bool)

    def __init__(self):
        super().__init__(None)
        self._open    = False
        self._working = False
        self._drag_pos: Optional[QPoint] = None
        self._pulse   = 0.0           # 0.0 → 1.0 for working animation
        self._pulse_dir = 1

        self._setup_window()
        self._position_to_corner()
        self._start_pulse_timer()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(BUTTON_W, BUTTON_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _position_to_corner(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.x() + geom.width()  - BUTTON_W - MARGIN_R
            y = geom.y() + geom.height() - BUTTON_H - MARGIN_B
            self.move(x, y)

    def _start_pulse_timer(self):
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_timer.start(30)   # ~33 fps

    def _tick_pulse(self):
        if not self._working:
            self._pulse = 0.0
            return
        self._pulse += 0.03 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse_dir = 1
        self.update()

    # ------------------------------------------------------------------
    # Public state API
    # ------------------------------------------------------------------
    def set_open(self, open_: bool):
        self._open = open_
        self._working = False
        self.update()

    def set_working(self, working: bool):
        self._working = working
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)   # inset for shadow space

        # --- drop shadow ---
        shadow_rect = rect.translated(0, 2)
        painter.setBrush(SHADOW_COLOUR)
        painter.setPen(Qt.PenStyle.NoPen)
        path_shadow = QPainterPath()
        path_shadow.addRoundedRect(
            shadow_rect.x(), shadow_rect.y(),
            shadow_rect.width(), shadow_rect.height(),
            CORNER_R, CORNER_R
        )
        painter.drawPath(path_shadow)

        # --- background gradient / colour ---
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), CORNER_R, CORNER_R)

        if self._working:
            t = self._pulse
            r = int(WORKING_COLOUR_A.red()   * (1-t) + WORKING_COLOUR_B.red()   * t)
            g = int(WORKING_COLOUR_A.green() * (1-t) + WORKING_COLOUR_B.green() * t)
            b = int(WORKING_COLOUR_A.blue()  * (1-t) + WORKING_COLOUR_B.blue()  * t)
            painter.setBrush(QColor(r, g, b))
        elif self._open:
            painter.setBrush(OPEN_COLOUR)
        else:
            grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            grad.setColorAt(0, IDLE_GRAD_START)
            grad.setColorAt(1, IDLE_GRAD_END)
            painter.setBrush(grad)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        # --- label ---
        if self._working:
            label = "⚙ Working…"
        elif self._open:
            label = "✖  Close"
        else:
            label = "✨ Ask AI"

        painter.setPen(TEXT_COLOUR)
        font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift+drag to reposition
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            else:
                self._open = not self._open
                self.toggled.emit(self._open)
                self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_pos = None
