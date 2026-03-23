from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable

from pynput import keyboard as _kb

logger = logging.getLogger(__name__)

KEY_ALIASES = {
    "cmd": "windows",
    "win": "windows",
}

# Map pynput special Key constants to logical names used in config strings
_SPECIAL_KEY_MAP: dict[_kb.Key, str] = {
    _kb.Key.ctrl_l: "ctrl",
    _kb.Key.ctrl_r: "ctrl",
    _kb.Key.alt_l: "alt",
    _kb.Key.alt_r: "alt",
    _kb.Key.alt_gr: "alt",
    _kb.Key.shift_l: "shift",
    _kb.Key.shift_r: "shift",
    _kb.Key.cmd_l: "windows",
    _kb.Key.cmd_r: "windows",
    _kb.Key.cmd: "windows",
    _kb.Key.space: "space",
    _kb.Key.enter: "enter",
    _kb.Key.tab: "tab",
    _kb.Key.esc: "escape",
    _kb.Key.f1: "f1",
    _kb.Key.f2: "f2",
    _kb.Key.f3: "f3",
    _kb.Key.f4: "f4",
    _kb.Key.f5: "f5",
    _kb.Key.f6: "f6",
    _kb.Key.f7: "f7",
    _kb.Key.f8: "f8",
    _kb.Key.f9: "f9",
    _kb.Key.f10: "f10",
    _kb.Key.f11: "f11",
    _kb.Key.f12: "f12",
}


def _normalize(k: str) -> str:
    k = k.strip().lower()
    return KEY_ALIASES.get(k, k)


def _event_to_name(key: _kb.Key | _kb.KeyCode) -> str | None:
    """Map a pynput key event to a logical config name, or None if not relevant."""
    if isinstance(key, _kb.Key):
        return _SPECIAL_KEY_MAP.get(key)
    if isinstance(key, _kb.KeyCode):
        # key.char can be None when modifier keys (Ctrl, Alt) are held — Windows
        # doesn't synthesize a character in that case. Fall back to vk (virtual
        # key code) so that e.g. Ctrl+Alt+S still resolves to "s".
        if key.char:
            return key.char.lower()
        if key.vk is not None and 65 <= key.vk <= 90:
            return chr(key.vk).lower()
        if key.vk is not None and 48 <= key.vk <= 57:
            return chr(key.vk)
    return None


class HotkeyListener:
    def __init__(
        self,
        keys: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ):
        self._target_names = {_normalize(k) for k in keys.split("+")}
        self.on_press = on_press
        self.on_release = on_release
        self._held: set[str] = set()
        self._active = False
        self._lock = threading.Lock()
        self._raw_keys = keys
        self._listener: _kb.Listener | None = None

    def start(self) -> None:
        if (
            sys.platform == "linux"
            and os.environ.get("WAYLAND_DISPLAY")
            and not os.environ.get("DISPLAY")
        ):
            logger.warning(
                "Wayland detected without XWayland — global hotkey capture may not work. "
                "Run under XWayland or set DISPLAY to an X11 socket."
            )
        logger.info("Registering hotkey: %s (targets=%s)", self._raw_keys, self._target_names)
        self._listener = _kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        logger.info("Hotkey active. Hold %s to record.", self._raw_keys)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        logger.info("Hotkey listener stopped.")

    def _on_press(self, key: _kb.Key | _kb.KeyCode) -> None:
        try:
            name = _event_to_name(key)
            if name not in self._target_names:
                return
            with self._lock:
                self._held.add(name)
                if self._held >= self._target_names and not self._active:
                    self._active = True
                    logger.info("Recording STARTED (hotkey pressed)")
                    threading.Thread(
                        target=self._safe_call,
                        args=(self.on_press,),
                        daemon=True,
                    ).start()
        except Exception:
            logger.exception("Error in hotkey press handler")

    def _on_release(self, key: _kb.Key | _kb.KeyCode) -> None:
        try:
            name = _event_to_name(key)
            if name not in self._target_names:
                return
            with self._lock:
                self._held.discard(name)
                if self._active and not (self._held >= self._target_names):
                    self._active = False
                    logger.info("Recording STOPPED (hotkey released)")
                    threading.Thread(
                        target=self._safe_call,
                        args=(self.on_release,),
                        daemon=True,
                    ).start()
        except Exception:
            logger.exception("Error in hotkey release handler")

    @staticmethod
    def _safe_call(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            logger.exception("Error in hotkey callback %s", fn)
