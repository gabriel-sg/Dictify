from __future__ import annotations

import logging
import sys
import threading
import time

from pynput.keyboard import Controller, Key
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Window focus — platform dispatch
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    def get_foreground_window() -> int:
        return _user32.GetForegroundWindow()

    def set_foreground_window(hwnd: int) -> bool:
        if not hwnd:
            return False
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        result = _user32.SetForegroundWindow(hwnd)
        if not result:
            current_thread = _kernel32.GetCurrentThreadId()
            target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
            if current_thread != target_thread:
                _user32.AttachThreadInput(current_thread, target_thread, True)
                result = _user32.SetForegroundWindow(hwnd)
                _user32.AttachThreadInput(current_thread, target_thread, False)
        return bool(result)

elif sys.platform == "darwin":

    def get_foreground_window():
        """Return active app info dict, or None."""
        try:
            from AppKit import NSWorkspace  # type: ignore[import]
            return NSWorkspace.sharedWorkspace().activeApplication()
        except Exception:
            return None

    def set_foreground_window(app_info) -> bool:
        """Activate the previously captured app via osascript."""
        if not app_info:
            return False
        try:
            import subprocess
            bundle = app_info.get("NSApplicationBundleIdentifier", "")
            if bundle:
                subprocess.run(
                    ["osascript", "-e", f'tell application id "{bundle}" to activate'],
                    check=False,
                    capture_output=True,
                )
            return True
        except Exception:
            logger.debug("set_foreground_window (macOS) failed", exc_info=True)
            return False

else:  # Linux / other

    def get_foreground_window():
        """Return the active X11 window ID via xdotool, or None."""
        try:
            import subprocess
            r = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                return int(r.stdout.strip())
        except Exception:
            pass
        return None

    def set_foreground_window(wid) -> bool:
        """Focus a window by X11 window ID via xdotool."""
        if not wid:
            return False
        try:
            import subprocess
            subprocess.run(
                ["xdotool", "windowfocus", str(wid)],
                check=False,
                capture_output=True,
            )
            return True
        except Exception:
            logger.debug("set_foreground_window (Linux) failed", exc_info=True)
            return False


# ---------------------------------------------------------------------------
# Qt clipboard bridge (cross-platform — no changes needed)
# ---------------------------------------------------------------------------

class _ClipboardBridge(QObject):
    """Thread-safe bridge to set clipboard text via Qt main thread."""
    request = Signal(str)

    def __init__(self):
        super().__init__()
        self._done = threading.Event()
        self.request.connect(self._on_request)

    def _on_request(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        logger.debug("[clipboard] text set via QClipboard (%d chars)", len(text))
        self._done.set()

    def set_text(self, text: str, timeout: float = 2.0) -> bool:
        self._done.clear()
        self.request.emit(text)
        return self._done.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# TextTyper
# ---------------------------------------------------------------------------

class TextTyper:
    PASTE_THRESHOLD = 50

    def __init__(self):
        self._kb = Controller()
        self._clipboard = _ClipboardBridge()

    def type_text(self, text: str, target_hwnd=None) -> None:
        if not text:
            return

        logger.debug("[typer] start type_text: %d chars, target=%s", len(text), target_hwnd)

        if target_hwnd:
            current = get_foreground_window()
            if current != target_hwnd:
                logger.debug("[typer] restoring focus: %s -> %s", current, target_hwnd)
                ok = set_foreground_window(target_hwnd)
                logger.debug("[typer] set_foreground_window returned %s", ok)
                time.sleep(0.05)

        logger.debug("[typer] using %s method", "direct" if len(text) <= self.PASTE_THRESHOLD else "clipboard")
        if len(text) <= self.PASTE_THRESHOLD:
            self._type_direct(text)
        else:
            self._paste_via_clipboard(text)
        logger.debug("[typer] type_text done")

    def _type_direct(self, text: str) -> None:
        try:
            self._kb.type(text)
        except Exception:
            logger.exception("Direct typing failed, falling back to clipboard paste")
            self._paste_via_clipboard(text)

    def _paste_via_clipboard(self, text: str) -> None:
        try:
            logger.debug("[typer] setting clipboard text via Qt")
            if not self._clipboard.set_text(text):
                logger.error("[typer] QClipboard timed out, skipping paste")
                return
            time.sleep(0.05)
            logger.debug("[typer] sending paste shortcut")
            if sys.platform == "darwin":
                self._kb.press(Key.cmd)
                self._kb.press("v")
                self._kb.release("v")
                self._kb.release(Key.cmd)
            else:
                self._kb.press(Key.ctrl)
                self._kb.press("v")
                self._kb.release("v")
                self._kb.release(Key.ctrl)
            logger.debug("[typer] paste shortcut sent, waiting")
            time.sleep(0.1)
        except Exception:
            logger.exception("Clipboard paste failed")
