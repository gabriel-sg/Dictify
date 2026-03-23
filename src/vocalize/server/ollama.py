"""Ollama model management utilities."""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)


def _ollama_api_url(openai_base_url: str) -> str:
    """Convert OpenAI-compatible URL to Ollama native API URL.

    e.g. http://localhost:11434/v1 -> http://localhost:11434
    """
    return openai_base_url.rstrip("/").removesuffix("/v1")


def is_model_available(base_url: str, model: str) -> bool:
    """Check if a model is already downloaded in Ollama."""
    api_url = _ollama_api_url(base_url)
    try:
        resp = httpx.get(f"{api_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        # Ollama model names: "llama3.2:latest" matches "llama3.2"
        available = {m["name"] for m in models}
        # Check both exact match and with :latest suffix
        return model in available or f"{model}:latest" in available
    except Exception:
        logger.warning("Could not reach Ollama at %s", api_url)
        return False


def pull_model(base_url: str, model: str) -> bool:
    """Pull (download) a model in Ollama. Blocks until complete."""
    api_url = _ollama_api_url(base_url)
    logger.info("Pulling Ollama model '%s' from %s...", model, api_url)
    try:
        # Ollama pull API streams JSON lines with progress
        with httpx.stream(
            "POST",
            f"{api_url}/api/pull",
            json={"name": model},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                status = data.get("status", "")
                if "completed" in data and "total" in data:
                    pct = int(data["completed"] / data["total"] * 100) if data["total"] else 0
                    logger.info("  %s: %d%%", status, pct)
                elif status:
                    logger.info("  %s", status)
        logger.info("Model '%s' ready.", model)
        return True
    except Exception:
        logger.exception("Failed to pull model '%s'", model)
        return False


def ensure_model(base_url: str, model: str) -> bool:
    """Ensure a model is available in Ollama, pulling it if needed."""
    if is_model_available(base_url, model):
        logger.info("Ollama model '%s' already available.", model)
        return True
    logger.info("Ollama model '%s' not found, downloading...", model)
    return pull_model(base_url, model)
