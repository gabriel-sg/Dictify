from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from dictify.config import AppConfig, load_config
from dictify.server.pipeline import Pipeline
from dictify.server.transcriber import Transcriber

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

    app = FastAPI(title="Dictify", version="0.1.0", lifespan=lifespan)
    app.state.config = config

    from dictify.server.routes import router
    app.include_router(router)

    return app
