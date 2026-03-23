from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile

from dictify.models import HealthResponse, TranscribeResponse

router = APIRouter(prefix="/api")

MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 10 MB (~5 min at 16kHz/16bit)


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: Request,
    audio: UploadFile,
    language: str | None = Form(default=None),
    skip_pipeline: bool = Form(default=False),
) -> TranscribeResponse:
    start = time.perf_counter()

    audio_bytes = await audio.read()
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(413, "Audio too large (max 10 MB)")

    transcriber = request.app.state.transcriber
    pipeline = request.app.state.pipeline
    config = request.app.state.config

    whisper_start = time.perf_counter()
    raw_text, detected_lang = await asyncio.to_thread(
        transcriber.transcribe, audio_bytes, config.audio.sample_rate, language
    )
    whisper_ms = int((time.perf_counter() - whisper_start) * 1000)

    if skip_pipeline:
        text, steps = raw_text, []
    else:
        text, steps = await pipeline.run(raw_text)

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return TranscribeResponse(
        text=text,
        raw_text=raw_text,
        language=detected_lang,
        processing_time_ms=elapsed_ms,
        whisper_model=config.whisper.model,
        whisper_time_ms=whisper_ms,
        steps=steps,
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    transcriber = request.app.state.transcriber
    config = request.app.state.config
    return HealthResponse(
        status="ok",
        model_loaded=transcriber.is_loaded,
        whisper_model=config.whisper.model,
    )
