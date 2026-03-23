from __future__ import annotations

import logging
import tempfile
import os

from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel

logger = logging.getLogger(__name__)


class AudioPlayerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._temp_file: str | None = None

        self._play_btn = QPushButton("Play")
        self._play_btn.setFixedWidth(70)
        self._play_btn.clicked.connect(self._toggle_play)

        self._duration_label = QLabel("0.0s")
        self._duration_label.setStyleSheet("color: #888;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._play_btn)
        layout.addWidget(self._duration_label)
        layout.addStretch()

        self._player.playbackStateChanged.connect(self._on_state_changed)

    def load_audio(self, wav_bytes: bytes, duration_s: float = 0.0) -> None:
        self._player.stop()
        self._cleanup_temp()

        if not wav_bytes:
            self._play_btn.setEnabled(False)
            self._duration_label.setText("No audio")
            return

        # Write to temp file for QMediaPlayer
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.write(fd, wav_bytes)
        os.close(fd)
        self._temp_file = path

        self._player.setSource(QUrl.fromLocalFile(path))
        self._play_btn.setEnabled(True)
        self._duration_label.setText(f"{duration_s:.1f}s")

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("Pause")
        else:
            self._play_btn.setText("Play")

    def _cleanup_temp(self) -> None:
        if self._temp_file and os.path.exists(self._temp_file):
            try:
                os.unlink(self._temp_file)
            except Exception:
                pass
            self._temp_file = None

    def cleanup(self) -> None:
        self._player.stop()
        self._cleanup_temp()
