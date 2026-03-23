from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# Config lives in the repo root (next to pyproject.toml)
CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 9876


class ServerConfig(BaseModel):
    host: str = DEFAULT_SERVER_HOST
    port: int = DEFAULT_SERVER_PORT


class WhisperConfig(BaseModel):
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    beam_size: int = 3
    vad_filter: bool = True
    language: str | None = "es"


class HotkeyConfig(BaseModel):
    keys_es: str = "ctrl+alt+s"
    keys_en: str = "ctrl+alt+e"
    keys_raw: str = "ctrl+alt+z"
    raw_language: str = "auto"  # "es", "en", or "auto" (let Whisper detect)


class AudioConfig(BaseModel):
    sample_rate: int = 16000
    channels: int = 0  # 0 = auto-detect from device (recommended), or set explicitly
    device_id: int | None = None
    input_gain: float = 1.0


class PipelineStepConfig(BaseModel):
    type: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    steps: list[PipelineStepConfig] = Field(default_factory=lambda: [
        PipelineStepConfig(
            type="llm_rewrite",
            params={"prompt_file": "prompts/transcription_editor.md"},
        ),
    ])


class LLMConfig(BaseModel):
    provider: str = "ollama"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    hotkey: HotkeyConfig = Field(default_factory=HotkeyConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def _resolve_env_vars(data: Any) -> Any:
    """Resolve ${ENV_VAR} references in string values."""
    if isinstance(data, str) and data.startswith("${") and data.endswith("}"):
        env_var = data[2:-1]
        return os.environ.get(env_var, data)
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_env_vars(v) for v in data]
    return data


def load_config(path: Path | None = None) -> AppConfig:
    """Load configuration from YAML file, falling back to defaults."""
    config_path = path or CONFIG_PATH
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        raw = _resolve_env_vars(raw)
        return AppConfig.model_validate(raw)
    return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Serialize config back to YAML file."""
    config_path = path or CONFIG_PATH
    data = config.model_dump(exclude_defaults=False)
    with open(config_path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
