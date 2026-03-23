from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout, QApplication, QGraphicsDropShadowEffect

logger = logging.getLogger(__name__)

STATE_COLORS = {
    "recording": "#E53935",
    "processing": "#FB8C00",
    "done": "#43A047",
}

STATE_LABELS = {
    "idle": "",
    "recording": "Recording...",
    "processing": "Processing...",
    "done": "Done!",
}

OVERLAY_WIDTH = 220
OVERLAY_HEIGHT = 48
PADDING = 20
DONE_DISPLAY_MS = 1200


class OverlaySignals(QObject):
    state_changed = Signal(str)


class OverlayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = OverlaySignals()
        self.signals.state_changed.connect(self._apply_state)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(OVERLAY_WIDTH, OVERLAY_HEIGHT)

        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "color: white; font: bold 13pt 'Segoe UI', 'SF Pro Display', 'Ubuntu', sans-serif; "
            "border-radius: 10px; padding: 6px 8px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 2)
        self._label.setGraphicsEffect(shadow)

        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(self._hide)

        self._position_bottom_center()
        self.hide()

    def _position_bottom_center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.left() + (geom.width() - OVERLAY_WIDTH) // 2
            y = geom.bottom() - OVERLAY_HEIGHT - PADDING
            self.move(x, y)

    def set_state(self, state: str) -> None:
        self.signals.state_changed.emit(state)

    def _apply_state(self, state: str) -> None:
        color = STATE_COLORS.get(state)
        text = STATE_LABELS.get(state, "")

        if color is None:
            self.hide()
            return

        self._label.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"font: bold 13pt 'Segoe UI', 'SF Pro Display', 'Ubuntu', sans-serif; border-radius: 10px; padding: 6px 8px;"
        )
        self._label.setText(text)
        self.show()

        if state == "done":
            self._done_timer.start(DONE_DISPLAY_MS)

    def _hide(self) -> None:
        self.hide()
