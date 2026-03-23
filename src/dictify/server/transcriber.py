from __future__ import annotations

import io
import logging
import wave

import numpy as np
from faster_whisper import WhisperModel

from dictify.config import WhisperConfig

logger = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, config: WhisperConfig):
        self.config = config
        self.model: WhisperModel | None = None

    def load(self) -> None:
        logger.info(
            "Loading Whisper model %s on %s (%s)...",
            self.config.model,
            self.config.device,
            self.config.compute_type,
        )
        self.model = WhisperModel(
            self.config.model,
            device=self.config.device,
            compute_type=self.config.compute_type,
        )
        logger.info("Whisper model loaded.")

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> tuple[str, str]:
        """Transcribe audio bytes to text. Returns (text, detected_language).

        Args:
            language: Override language for this request. Falls back to config.
        """
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        audio = self._decode_audio(audio_bytes, sample_rate)

        # Prioritize passed language. If "auto", tell Whisper to detect (None).
        # Otherwise fall back to config default.
        requested_lang = language if language is not None else self.config.language
        if requested_lang == "auto":
            effective_language = None
        else:
            effective_language = requested_lang

        logger.info(
            "[Transcribe] beam_size=%s vad_filter=%s language=%s",
            self.config.beam_size, self.config.vad_filter, effective_language,
        )
        segments, info = self.model.transcribe(
            audio,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            language=effective_language,
        )

        text = " ".join(seg.text.strip() for seg in segments)
        return text, info.language

    def transcribe_audio(
        self,
        audio: np.ndarray,
        sample_rate: int,
        language: str | None = None,
        beam_size: int | None = None,
    ) -> tuple[str, str]:
        """Transcribe a float32 numpy array directly, bypassing WAV encode/decode.

        Args:
            audio: float32 numpy array, mono or stereo. Values in [-1.0, 1.0].
            sample_rate: Sample rate of the audio array.
            language: Override language. Falls back to config.language.
            beam_size: Override beam size. Falls back to config.beam_size.

        Returns:
            (text, detected_language)
        """
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)
        if sample_rate != 16000:
            audio = self._resample_to_16k(audio, sample_rate)

        effective_beam = beam_size if beam_size is not None else self.config.beam_size
        
        # Prioritize passed language. If "auto", tell Whisper to detect (None).
        # Otherwise fall back to config default.
        requested_lang = language if language is not None else self.config.language
        if requested_lang == "auto":
            effective_language = None
        else:
            effective_language = requested_lang

        logger.info(
            "[Transcribe] beam_size=%s vad_filter=%s language=%s",
            effective_beam, self.config.vad_filter, effective_language,
        )
        segments, info = self.model.transcribe(
            audio,
            beam_size=effective_beam,
            vad_filter=self.config.vad_filter,
            language=effective_language,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text, info.language

    @staticmethod
    def _resample_to_16k(audio: np.ndarray, sr: int) -> np.ndarray:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(16000, sr)
        return resample_poly(audio, 16000 // g, sr // g).astype(np.float32)

    def _decode_audio(self, audio_bytes: bytes, default_sr: int) -> np.ndarray:
        """Decode WAV bytes to float32 numpy array. Falls back to raw PCM."""
        try:
            buf = io.BytesIO(audio_bytes)
            with wave.open(buf, "rb") as wf:
                sr = wf.getframerate()
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())

            dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sample_width, np.int16)
            audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)

            if dtype == np.int16:
                audio /= 32768.0
            elif dtype == np.int32:
                audio /= 2147483648.0
            elif dtype == np.int8:
                audio /= 128.0

            if n_channels > 1:
                audio = audio.reshape(-1, n_channels).mean(axis=1)

            # Resample to 16kHz if needed
            if sr != 16000:
                audio = self._resample_to_16k(audio, sr)

            return audio
        except wave.Error:
            # Assume raw 16-bit PCM
            audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return audio
