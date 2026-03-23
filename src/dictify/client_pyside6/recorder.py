from __future__ import annotations

import io
import logging
import threading
import wave

import numpy as np
import sounddevice as sd

from dictify.config import AudioConfig

logger = logging.getLogger(__name__)


class AudioRecorder:
    def __init__(self, config: AudioConfig):
        self.sample_rate = config.sample_rate
        self._channels_config = config.channels  # 0 = auto
        self.device_id = config.device_id
        self.input_gain = config.input_gain
        self._active_channels: int = 1  # resolved at start()
        self._buffer: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    @property
    def channels(self) -> int:
        return self._active_channels

    def set_device(self, device_id: int | None) -> None:
        self.device_id = device_id

    def _resolve_channels(self) -> int:
        """Return channels to use: config value, or device native (capped at 2)."""
        if self._channels_config > 0:
            return self._channels_config
        try:
            dev = sd.query_devices(self.device_id if self.device_id is not None else sd.default.device[0])
            native = int(dev["max_input_channels"])
            resolved = min(native, 2)  # cap at stereo — enough for SNR gain
            logger.debug("Auto-detected %d input channels (using %d)", native, resolved)
            return max(resolved, 1)
        except Exception:
            logger.debug("Could not query device channels, defaulting to 1", exc_info=True)
            return 1

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    logger.debug("Error closing leftover stream", exc_info=True)
                self._stream = None

            self._buffer.clear()
            self._active_channels = self._resolve_channels()
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self._active_channels,
                dtype="int16",
                device=self.device_id,
                callback=self._callback,
            )
            self._stream.start()
            logger.debug(
                "Audio stream started (sr=%d, ch=%d, dev=%s)",
                self.sample_rate, self._active_channels, self.device_id,
            )

    def stop(self) -> bytes:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    logger.exception("Error closing audio stream")
                self._stream = None

            if not self._buffer:
                return b""

            audio = np.concatenate(self._buffer)
            self._buffer.clear()

        logger.debug("Audio captured: %d samples (%.1fs)", len(audio), len(audio) / self.sample_rate)
        return self._to_wav(audio)

    @property
    def is_recording(self) -> bool:
        try:
            return self._stream is not None and self._stream.active
        except Exception:
            return False

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)
        chunk = indata.copy()
        if self.input_gain != 1.0:
            chunk = np.clip(
                chunk.astype(np.float32) * self.input_gain,
                -32768, 32767,
            ).astype(np.int16)
        with self._lock:
            self._buffer.append(chunk)

    def _to_wav(self, audio: np.ndarray) -> bytes:
        n_channels = audio.shape[1] if audio.ndim == 2 else 1
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    @staticmethod
    def list_input_devices() -> list[dict]:
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                result.append({
                    "id": i,
                    "name": d["name"],
                    "sample_rate": d["default_samplerate"],
                    "channels": d["max_input_channels"],
                })
        return result

    @staticmethod
    def get_default_input_device() -> int | None:
        try:
            return sd.default.device[0]
        except Exception:
            return None
