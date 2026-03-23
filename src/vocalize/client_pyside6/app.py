from __future__ import annotations

import asyncio
import faulthandler
import logging
import signal
import sys
import threading
from datetime import datetime
from functools import partial

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QTimer, Signal

from vocalize.config import AppConfig
from vocalize.client_pyside6.api_client import ApiClient
from vocalize.client_pyside6.debug_store import DebugStore, DebugInteraction
from vocalize.client_pyside6.hotkey import HotkeyListener
from vocalize.client_pyside6.main_window import MainWindow
from vocalize.client_pyside6.overlay import OverlayWidget
from vocalize.client_pyside6.recorder import AudioRecorder
from vocalize.client_pyside6.typer import TextTyper, get_foreground_window

logger = logging.getLogger(__name__)


class _AppSignals(QObject):
    """Thread-safe bridge: any thread emits, Qt main thread receives."""
    refresh_debug = Signal()
    set_overlay = Signal(str)


class VocalizeApp:
    def __init__(self, config: AppConfig):
        self.config = config
        server_url = f"http://{config.server.host}:{config.server.port}"

        self.recorder = AudioRecorder(config.audio)
        self.typer = TextTyper()
        self.api = ApiClient(server_url)
        self.debug_store = DebugStore()

        self._hotkey_es: HotkeyListener | None = None
        self._hotkey_en: HotkeyListener | None = None
        self._hotkey_raw: HotkeyListener | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._processing = False
        self._language: str | None = "es"
        self._skip_pipeline: bool = False
        self._target_hwnd: int = 0

        # Qt objects (created in run())
        self._qt_app: QApplication | None = None
        self._signals: _AppSignals | None = None
        self._window: MainWindow | None = None
        self._overlay: OverlayWidget | None = None

    def run(self) -> None:
        # Enable faulthandler to log native crashes (segfaults) to stderr and log file
        faulthandler.enable()
        crash_log = open("logs/client-ui-crash.log", "a")
        faulthandler.enable(file=crash_log)

        logger.info("Starting Vocalize PySide6 client...")

        # Start asyncio event loop in background thread
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        # Start hotkeys
        self._start_hotkeys()

        # Create Qt application (must be in main thread)
        self._qt_app = QApplication(sys.argv)
        self._qt_app.setApplicationName("Vocalize")

        # Allow Ctrl+C from terminal to quit
        signal.signal(signal.SIGINT, lambda *_: self._qt_app.quit())
        # Timer to let Python process signals (Qt blocks the main thread)
        self._signal_timer = QTimer()
        self._signal_timer.start(200)
        self._signal_timer.timeout.connect(lambda: None)

        # Thread-safe signals bridge
        self._signals = _AppSignals()
        self._signals.set_overlay.connect(self._on_overlay_signal)
        self._signals.refresh_debug.connect(self._on_refresh_debug_signal)

        # Create overlay
        self._overlay = OverlayWidget()

        # Create main window
        self._window = MainWindow(self.config, self.debug_store)
        self._window.settings_tab.set_callbacks(
            on_hotkeys_changed=self._restart_hotkeys,
            on_device_changed=self._update_device,
        )
        self._window.show()

        logger.info(
            "Client ready. Hold %s (ES) or %s (EN) to record, %s (raw/no LLM).",
            self.config.hotkey.keys_es,
            self.config.hotkey.keys_en,
            self.config.hotkey.keys_raw,
        )

        # Run Qt event loop
        exit_code = self._qt_app.exec()

        # Cleanup
        self._cleanup()
        sys.exit(exit_code)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # --- Qt main-thread signal handlers ---

    def _on_overlay_signal(self, state: str) -> None:
        if self._overlay:
            self._overlay.set_state(state)

    def _on_refresh_debug_signal(self) -> None:
        if self._window:
            self._window.debug_tab.refresh_list()

    # --- Hotkeys ---

    def _start_hotkeys(self) -> None:
        self._hotkey_es = HotkeyListener(
            self.config.hotkey.keys_es,
            on_press=partial(self._on_record_start, "es", False),
            on_release=self._on_record_stop,
        )
        self._hotkey_en = HotkeyListener(
            self.config.hotkey.keys_en,
            on_press=partial(self._on_record_start, "en", False),
            on_release=self._on_record_stop,
        )
        raw_lang = self.config.hotkey.raw_language
        raw_lang = None if raw_lang == "auto" else raw_lang
        self._hotkey_raw = HotkeyListener(
            self.config.hotkey.keys_raw,
            on_press=partial(self._on_record_start, raw_lang, True),
            on_release=self._on_record_stop,
        )
        self._hotkey_es.start()
        self._hotkey_en.start()
        self._hotkey_raw.start()

    def _stop_hotkeys(self) -> None:
        if self._hotkey_es:
            self._hotkey_es.stop()
            self._hotkey_es = None
        if self._hotkey_en:
            self._hotkey_en.stop()
            self._hotkey_en = None
        if self._hotkey_raw:
            self._hotkey_raw.stop()
            self._hotkey_raw = None

    def _restart_hotkeys(self) -> None:
        self._stop_hotkeys()
        self._start_hotkeys()
        logger.info("Hotkeys restarted with new config.")

    def _update_device(self, device_id: int | None) -> None:
        self.recorder.set_device(device_id)
        logger.info("Audio device updated to: %s", device_id)

    # --- Recording flow (called from hotkey threads) ---

    def _on_record_start(self, language: str | None, skip_pipeline: bool = False) -> None:
        try:
            if self._processing:
                return
            self._language = language
            self._skip_pipeline = skip_pipeline
            self._target_hwnd = get_foreground_window()
            logger.info("Recording started (language=%s, target_hwnd=%s).", language, self._target_hwnd)
            self.recorder.start()
            if self._signals:
                self._signals.set_overlay.emit("recording")
        except Exception:
            logger.exception("Error starting recording")
            self._safe_reset()

    def _on_record_stop(self) -> None:
        try:
            if not self.recorder.is_recording:
                return
            logger.info("Recording stopped. Processing...")
            audio_data = self.recorder.stop()
            if self._signals:
                self._signals.set_overlay.emit("processing")

            if not audio_data:
                logger.warning("No audio recorded.")
                if self._signals:
                    self._signals.set_overlay.emit("idle")
                return

            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._process(audio_data, self._language, self._target_hwnd, self._skip_pipeline),
                    self._loop,
                )
        except Exception:
            logger.exception("Error stopping recording")
            self._safe_reset()

    def _safe_reset(self) -> None:
        self._processing = False
        try:
            if self.recorder.is_recording:
                self.recorder.stop()
        except Exception:
            logger.debug("Error stopping recorder during reset", exc_info=True)
        if self._signals:
            try:
                self._signals.set_overlay.emit("idle")
            except Exception:
                logger.debug("Error resetting overlay during reset", exc_info=True)

    # --- Async processing (runs in asyncio thread) ---

    async def _process(self, audio_data: bytes, language: str | None, target_hwnd: int, skip_pipeline: bool = False) -> None:
        self._processing = True
        try:
            response = await self.api.transcribe(audio_data, language=language, skip_pipeline=skip_pipeline)
            logger.info(
                "Transcription: %s (raw: %s, lang: %s, %dms)",
                response.text, response.raw_text,
                response.language, response.processing_time_ms,
            )

            if response.text.strip():
                try:
                    logger.info(
                        "Typing text (%d chars, hwnd=%s, method=%s)...",
                        len(response.text), target_hwnd,
                        "clipboard" if len(response.text) > 50 else "direct",
                    )
                    # Run in a separate thread — clipboard Win32 API
                    # needs a thread with a message pump, not the asyncio
                    # IOCP proactor thread which causes access violations.
                    await asyncio.to_thread(
                        self.typer.type_text, response.text, target_hwnd
                    )
                    logger.info("Text typed successfully.")
                except Exception:
                    logger.exception("Failed to type text: %r", response.text)

            # Signal overlay via Qt main thread
            if self._signals:
                self._signals.set_overlay.emit("done")

            # Save to debug store
            if self.debug_store.enabled:
                interaction = DebugInteraction(
                    timestamp=datetime.now().isoformat(),
                    language=language,
                    audio_blob=audio_data,
                    raw_text=response.raw_text,
                    final_text=response.text,
                    whisper_model=response.whisper_model or "",
                    whisper_time_ms=response.whisper_time_ms or 0,
                    total_time_ms=response.processing_time_ms,
                    steps=response.steps or [],
                )
                self.debug_store.save(interaction)
                # Refresh debug tab via Qt main thread
                if self._signals:
                    self._signals.refresh_debug.emit()

        except Exception:
            logger.exception("Processing failed")
            if self._signals:
                try:
                    self._signals.set_overlay.emit("idle")
                except Exception:
                    logger.debug("Failed to reset overlay", exc_info=True)
        finally:
            self._processing = False

    # --- Cleanup ---

    def _cleanup(self) -> None:
        logger.info("Shutting down...")
        self._stop_hotkeys()

        if self._loop and self._loop.is_running():
            # Close API client while loop is still running
            future = asyncio.run_coroutine_threadsafe(self.api.close(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass

            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=5)

        self.debug_store.close()
