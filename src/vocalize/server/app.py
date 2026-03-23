from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from vocalize.config import AppConfig, load_config
from vocalize.server.pipeline import Pipeline
from vocalize.server.transcriber import Transcriber

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AppConfig = app.state.config
    transcriber = Transcriber(config.whisper)
    try:
        transcriber.load()
    except Exception as e:
        logger.error("Failed to load Whisper model: %s", e)
        raise SystemExit(1) from e
    app.state.transcriber = transcriber
    app.state.pipeline = Pipeline(config.pipeline, config.llm)
    yield


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(title="Vocalize", version="0.1.0", lifespan=lifespan)
    app.state.config = config

    from vocalize.server.routes import router
    app.include_router(router)

    return app
