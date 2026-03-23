from __future__ import annotations

import logging

import httpx

from dictify.models import TranscribeResponse

logger = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def transcribe(
        self, audio_data: bytes, language: str | None = None, skip_pipeline: bool = False
    ) -> TranscribeResponse:
        data: dict = {}
        if language is not None:
            data["language"] = language
        if skip_pipeline:
            data["skip_pipeline"] = "true"
        response = await self._client.post(
            "/api/transcribe",
            files={"audio": ("recording.wav", audio_data, "audio/wav")},
            data=data,
        )
        response.raise_for_status()
        return TranscribeResponse.model_validate(response.json())

    async def health(self) -> bool:
        try:
            response = await self._client.get("/api/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
