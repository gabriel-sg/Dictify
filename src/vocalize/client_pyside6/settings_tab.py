from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout, QComboBox,
    QLineEdit, QPushButton, QPlainTextEdit, QLabel, QHBoxLayout,
    QMessageBox, QSlider, QProgressBar,
)

import numpy as np

from vocalize.config import AppConfig, AudioConfig, save_config
from vocalize.client_pyside6.recorder import AudioRecorder
from vocalize.client_pyside6.audio_player import AudioPlayerWidget

logger = logging.getLogger(__name__)


class KeyCaptureLineEdit(QLineEdit):
    """Line edit that captures a key combination when in capture mode."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)
        self._capturing = False
        self._keys_held: set[str] = set()

    def start_capture(self) -> None:
        self._capturing = True
        self._keys_held.clear()
        self.setText("Press keys...")
        self.setStyleSheet("border: 2px solid #E53935;")

    def stop_capture(self) -> None:
        self._capturing = False
        self.setStyleSheet("")

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            return
        key_name = self._key_name(event)
        if key_name:
            self._keys_held.add(key_name)
            self.setText("+".join(sorted(self._keys_held)))

    def keyReleaseEvent(self, event) -> None:
        if not self._capturing:
            return
        if self._keys_held:
            self.setText("+".join(sorted(self._keys_held)))
            self.stop_capture()

    @staticmethod
    def _key_name(event) -> str | None:
        key = event.key()
        modifiers = {
            Qt.Key.Key_Control: "ctrl",
            Qt.Key.Key_Alt: "alt",
            Qt.Key.Key_Shift: "shift",
            Qt.Key.Key_Meta: "windows",
        }
        if key in modifiers:
            return modifiers[key]
        text = event.text().strip()
        if text and text.isprintable():
            return text.lower()
        # Map special keys
        special = {
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_Return: "enter",
            Qt.Key.Key_Escape: "escape",
            Qt.Key.Key_Tab: "tab",
            Qt.Key.Key_F1: "f1", Qt.Key.Key_F2: "f2", Qt.Key.Key_F3: "f3",
            Qt.Key.Key_F4: "f4", Qt.Key.Key_F5: "f5", Qt.Key.Key_F6: "f6",
            Qt.Key.Key_F7: "f7", Qt.Key.Key_F8: "f8", Qt.Key.Key_F9: "f9",
            Qt.Key.Key_F10: "f10", Qt.Key.Key_F11: "f11", Qt.Key.Key_F12: "f12",
        }
        return special.get(key)


class SettingsTab(QWidget):
    config_changed = Signal()

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self._config = config
        self._on_hotkeys_changed: Callable | None = None
        self._on_device_changed: Callable | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # --- Microphone ---
        mic_group = QGroupBox("Microphone")
        mic_layout = QFormLayout(mic_group)

        self._mic_combo = QComboBox()
        self._refresh_devices()
        mic_layout.addRow("Input Device:", self._mic_combo)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self._refresh_devices)
        mic_layout.addRow("", refresh_btn)

        layout.addWidget(mic_group)

        # --- Mic Test ---
        mic_test_group = QGroupBox("Microphone Test")
        mic_test_layout = QVBoxLayout(mic_test_group)

        # Gain slider
        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel("Input Gain:"))
        self._gain_slider = QSlider(Qt.Orientation.Horizontal)
        self._gain_slider.setMinimum(10)
        self._gain_slider.setMaximum(500)
        self._gain_slider.setValue(int(config.audio.input_gain * 100))
        self._gain_slider.setTickInterval(50)
        self._gain_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        gain_row.addWidget(self._gain_slider)
        self._gain_label = QLabel(f"{config.audio.input_gain:.1f}x")
        self._gain_label.setFixedWidth(50)
        gain_row.addWidget(self._gain_label)
        mic_test_layout.addLayout(gain_row)

        # Level meter
        self._level_meter = QProgressBar()
        self._level_meter.setMaximum(100)
        self._level_meter.setValue(0)
        self._level_meter.setTextVisible(False)
        self._level_meter.setFixedHeight(12)
        self._level_meter.setStyleSheet("""
            QProgressBar { background-color: #313244; border: 1px solid #45475a; border-radius: 4px; }
            QProgressBar::chunk { background-color: #a6e3a1; border-radius: 3px; }
        """)
        mic_test_layout.addWidget(self._level_meter)

        # Record / Stop buttons + player
        test_btn_row = QHBoxLayout()
        self._test_record_btn = QPushButton("Record Test")
        self._test_record_btn.setFixedWidth(120)
        self._test_record_btn.clicked.connect(self._toggle_test_recording)
        test_btn_row.addWidget(self._test_record_btn)

        self._test_player = AudioPlayerWidget()
        self._test_player.setEnabled(False)
        test_btn_row.addWidget(self._test_player)
        test_btn_row.addStretch()
        mic_test_layout.addLayout(test_btn_row)

        layout.addWidget(mic_test_group)

        # Test recording state
        self._test_recorder: AudioRecorder | None = None
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(50)
        self._level_timer.timeout.connect(self._update_level_meter)

        # --- Hotkeys ---
        hotkey_group = QGroupBox("Hotkeys")
        hotkey_layout = QFormLayout(hotkey_group)

        self._keys_es = KeyCaptureLineEdit(config.hotkey.keys_es)
        es_row = QHBoxLayout()
        es_row.addWidget(self._keys_es)
        self._es_capture_btn = QPushButton("Record")
        self._es_capture_btn.setFixedWidth(70)
        self._es_capture_btn.clicked.connect(lambda: self._keys_es.start_capture())
        es_row.addWidget(self._es_capture_btn)
        es_container = QWidget()
        es_container.setLayout(es_row)
        hotkey_layout.addRow("Spanish:", es_container)

        self._keys_en = KeyCaptureLineEdit(config.hotkey.keys_en)
        en_row = QHBoxLayout()
        en_row.addWidget(self._keys_en)
        self._en_capture_btn = QPushButton("Record")
        self._en_capture_btn.setFixedWidth(70)
        self._en_capture_btn.clicked.connect(lambda: self._keys_en.start_capture())
        en_row.addWidget(self._en_capture_btn)
        en_container = QWidget()
        en_container.setLayout(en_row)
        hotkey_layout.addRow("English:", en_container)

        layout.addWidget(hotkey_group)

        # --- LLM Prompt ---
        llm_group = QGroupBox("LLM Post-Processing")
        llm_layout = QVBoxLayout(llm_group)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._model_label = QLabel(config.llm.model)
        self._model_label.setStyleSheet("font-weight: bold;")
        model_row.addWidget(self._model_label)
        model_row.addStretch()
        llm_layout.addLayout(model_row)

        llm_layout.addWidget(QLabel("System Prompt:"))
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setMaximumHeight(150)
        prompt = self._get_llm_prompt()
        self._prompt_edit.setPlainText(prompt)
        llm_layout.addWidget(self._prompt_edit)

        layout.addWidget(llm_group)

        # --- Save ---
        save_btn = QPushButton("Save Settings")
        save_btn.setFixedHeight(36)
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

        layout.addStretch()

    def set_callbacks(
        self,
        on_hotkeys_changed: Callable | None = None,
        on_device_changed: Callable | None = None,
    ) -> None:
        self._on_hotkeys_changed = on_hotkeys_changed
        self._on_device_changed = on_device_changed

    def _refresh_devices(self) -> None:
        self._mic_combo.clear()
        devices = AudioRecorder.list_input_devices()
        default_id = AudioRecorder.get_default_input_device()
        current_device = self._config.audio.device_id

        self._mic_combo.addItem("System Default", None)
        selected_idx = 0

        for d in devices:
            ch = d["channels"]
            if ch >= 2:
                cap = "2ch" if ch == 2 else f"{ch}ch→2ch"
            else:
                cap = "mono"
            label = f"{d['name']} ({int(d['sample_rate'])} Hz, {cap})"
            self._mic_combo.addItem(label, d["id"])
            if current_device is not None and d["id"] == current_device:
                selected_idx = self._mic_combo.count() - 1

        self._mic_combo.setCurrentIndex(selected_idx)

    def _get_llm_prompt(self) -> str:
        for step in self._config.pipeline.steps:
            if step.type == "llm_rewrite" and step.enabled:
                return step.params.get("prompt", "")
        return ""

    def _save(self) -> None:
        # Mic
        device_id = self._mic_combo.currentData()
        old_device = self._config.audio.device_id
        self._config.audio.device_id = device_id
        self._config.audio.input_gain = self._gain_slider.value() / 100.0

        # Hotkeys
        old_es = self._config.hotkey.keys_es
        old_en = self._config.hotkey.keys_en
        self._config.hotkey.keys_es = self._keys_es.text()
        self._config.hotkey.keys_en = self._keys_en.text()

        # LLM prompt
        new_prompt = self._prompt_edit.toPlainText()
        for step in self._config.pipeline.steps:
            if step.type == "llm_rewrite":
                step.params["prompt"] = new_prompt

        try:
            save_config(self._config)
            logger.info("Settings saved.")
        except Exception:
            logger.exception("Failed to save config")
            QMessageBox.critical(self, "Error", "Failed to save configuration.")
            return

        # Notify callbacks
        hotkeys_changed = (old_es != self._config.hotkey.keys_es or
                          old_en != self._config.hotkey.keys_en)
        if hotkeys_changed and self._on_hotkeys_changed:
            self._on_hotkeys_changed()

        if old_device != device_id and self._on_device_changed:
            self._on_device_changed(device_id)

        self.config_changed.emit()
        QMessageBox.information(self, "Saved", "Settings saved successfully.")

    # --- Mic Test ---

    def _on_gain_changed(self, value: int) -> None:
        gain = value / 100.0
        self._gain_label.setText(f"{gain:.1f}x")
        if self._test_recorder is not None:
            self._test_recorder.input_gain = gain

    def _toggle_test_recording(self) -> None:
        if self._test_recorder is not None and self._test_recorder.is_recording:
            self._stop_test_recording()
        else:
            self._start_test_recording()

    def _start_test_recording(self) -> None:
        device_id = self._mic_combo.currentData()
        gain = self._gain_slider.value() / 100.0
        audio_cfg = AudioConfig(
            sample_rate=self._config.audio.sample_rate,
            channels=self._config.audio.channels,
            device_id=device_id,
            input_gain=gain,
        )
        self._test_recorder = AudioRecorder(audio_cfg)
        try:
            self._test_recorder.start()
        except Exception:
            logger.exception("Failed to start test recording")
            QMessageBox.critical(self, "Error", "Failed to start recording. Check your microphone.")
            self._test_recorder = None
            return
        self._test_record_btn.setText("Stop")
        self._test_record_btn.setStyleSheet("background-color: #E53935; color: white;")
        self._test_player.setEnabled(False)
        self._level_timer.start()

    def _stop_test_recording(self) -> None:
        self._level_timer.stop()
        self._level_meter.setValue(0)
        if self._test_recorder is None:
            return
        wav_bytes = self._test_recorder.stop()
        self._test_recorder = None
        self._test_record_btn.setText("Record Test")
        self._test_record_btn.setStyleSheet("")
        if wav_bytes:
            import wave, io
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                duration = frames / rate
            self._test_player.setEnabled(True)
            self._test_player.load_audio(wav_bytes, duration)

    def _update_level_meter(self) -> None:
        if self._test_recorder is None:
            return
        with self._test_recorder._lock:
            if not self._test_recorder._buffer:
                return
            last_chunk = self._test_recorder._buffer[-1]
        rms = np.sqrt(np.mean(last_chunk.astype(np.float32) ** 2))
        level = min(100, int(rms / 327.67))
        self._level_meter.setValue(level)
