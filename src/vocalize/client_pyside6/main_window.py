from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QSystemTrayIcon, QMenu, QStatusBar,
)

from vocalize.config import AppConfig
from vocalize.client_pyside6.debug_store import DebugStore
from vocalize.client_pyside6.debug_tab import DebugTab
from vocalize.client_pyside6.settings_tab import SettingsTab

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, debug_store: DebugStore, parent=None):
        super().__init__(parent)
        self._config = config
        self._debug_store = debug_store

        self.setWindowTitle("Vocalize")
        self.setMinimumSize(800, 600)
        self.resize(900, 650)

        # Tabs
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._settings_tab = SettingsTab(config)
        self._tabs.addTab(self._settings_tab, "Settings")

        self._debug_tab = DebugTab(debug_store)
        self._tabs.addTab(self._debug_tab, "Debug")

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # System tray
        self._tray = None
        self._setup_tray()

        # Styling
        self._apply_style()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("Vocalize")

        menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def closeEvent(self, event) -> None:
        self._quit()
        event.accept()

    @property
    def settings_tab(self) -> SettingsTab:
        return self._settings_tab

    @property
    def debug_tab(self) -> DebugTab:
        return self._debug_tab

    def set_status(self, message: str) -> None:
        self._status_bar.showMessage(message)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QTabWidget::pane {
                border: 1px solid #45475a;
                background-color: #1e1e2e;
            }
            QTabBar::tab {
                background-color: #313244;
                color: #cdd6f4;
                padding: 8px 20px;
                border: 1px solid #45475a;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #1e1e2e;
                border-bottom: 2px solid #89b4fa;
            }
            QTabBar::tab:hover {
                background-color: #45475a;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #45475a;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                background-color: #181825;
                color: #cdd6f4;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #89b4fa;
            }
            QComboBox, QLineEdit, QPlainTextEdit {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover {
                border-color: #89b4fa;
            }
            QComboBox::drop-down {
                border: none;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 6px 16px;
            }
            QPushButton:hover {
                background-color: #45475a;
                border-color: #89b4fa;
            }
            QPushButton:pressed {
                background-color: #585b70;
            }
            QTableWidget {
                background-color: #181825;
                color: #cdd6f4;
                border: 1px solid #45475a;
                gridline-color: #313244;
                selection-background-color: #45475a;
                selection-color: #cdd6f4;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #89b4fa;
                padding: 6px;
                border: 1px solid #45475a;
                font-weight: bold;
            }
            QScrollArea {
                border: none;
                background-color: #1e1e2e;
            }
            QCheckBox {
                color: #cdd6f4;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #45475a;
                border-radius: 4px;
                background-color: #313244;
            }
            QCheckBox::indicator:checked {
                background-color: #89b4fa;
                border-color: #89b4fa;
            }
            QLabel {
                color: #cdd6f4;
            }
            QStatusBar {
                background-color: #181825;
                color: #a6adc8;
                border-top: 1px solid #45475a;
            }
            QSplitter::handle {
                background-color: #45475a;
                width: 2px;
            }
            QMenu {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
            }
            QMenu::item:selected {
                background-color: #45475a;
            }
        """)
