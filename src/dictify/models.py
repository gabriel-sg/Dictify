from __future__ import annotations

from pydantic import BaseModel


class StepDetail(BaseModel):
    step_type: str
    model: str | None = None
    system_prompt: str | None = None
    input_text: str = ""
    output_text: str = ""
    time_ms: int = 0


class TranscribeResponse(BaseModel):
    text: str
    raw_text: str
    language: str
    processing_time_ms: int
    whisper_model: str | None = None
    whisper_time_ms: int | None = None
    steps: list[StepDetail] | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    whisper_model: str
