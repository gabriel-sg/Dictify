# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Dictify — push-to-talk voice-to-text desktop app. User holds a hotkey, speaks, releases, and transcribed+post-processed text is typed at the cursor position. Client-server architecture (FastAPI) for potential cloud/network deployment.

## Commands

```bash
uv sync                    # Install/update dependencies
uv run server              # Start API server (loads Whisper model on GPU)
uv run client              # Start GUI client (PySide6 with settings + debug)
uv run ui                  # Start GUI client (alias)
uv run dictify            # Start both (server as subprocess + GUI client)
docker compose up -d       # Start Ollama container (LLM post-processing)
```

Test the server independently:
```bash
curl http://localhost:9876/api/health
curl -X POST http://localhost:9876/api/transcribe -F "audio=@test.wav"
```

## Architecture

**Two-process split:** server (ML inference) and client (desktop UX) communicate over HTTP. Server can run remotely/in Docker; client must run on the desktop for hotkey capture and text insertion.

### Server (`src/dictify/server/`)
- `app.py` — FastAPI factory with lifespan that loads Whisper model into GPU VRAM at startup.
- `transcriber.py` — Wraps faster-whisper (CTranslate2). Decodes WAV/PCM → float32 numpy → transcribe. Model stays loaded between requests. Handles resampling to 16kHz if needed.
- `pipeline.py` — Sequential chain of `PipelineStep` subclasses. Each step: `async process(text) -> text`. Built-in step: `LLMRewrite` (Ollama via OpenAI-compatible API, handles cleanup + filler word removal). Returns `StepDetail` with timing and I/O for each step.
- `routes.py` — `POST /api/transcribe` (multipart WAV upload + optional `language` field, max 10 MB), `GET /api/health`. Transcription runs in thread pool via `asyncio.to_thread()`. Response includes per-step details (`steps`, `whisper_time_ms`, `whisper_model`).
- `ollama.py` — Ollama model management: checks availability, auto-pulls missing models via Ollama native API. Used at server startup to auto-pull missing models.

### Client (`src/dictify/client_pyside6/`)
Full PySide6 desktop app with settings, debug inspector, and system tray.
- `app.py` — Orchestrator. Qt main thread runs the event loop; asyncio runs in a background thread. Thread-safe bridge via `_AppSignals` (Qt signals). Manages hotkeys, recording flow, and debug store. Uses `faulthandler` for native crash logging.
- `main_window.py` — `QMainWindow` with tab widget (Settings, Debug), status bar, system tray icon with context menu. Catppuccin Mocha dark theme via QSS.
- `settings_tab.py` — Microphone device selection, hotkey capture (`KeyCaptureLineEdit`), LLM prompt editing. Saves config to YAML via `save_config()`. Callbacks notify app to restart hotkeys or update audio device live.
- `debug_tab.py` — Toggle debug logging, table of interactions, detail panel with audio playback, raw/final text, per-step LLM I/O. Stored in SQLite.
- `debug_store.py` — SQLite (WAL mode) persistence for `DebugInteraction` objects. Stores audio blobs, timestamps, Whisper/LLM details. DB at `db/debug.db`.
- `audio_player.py` — `QMediaPlayer`-based audio playback widget. Writes WAV blob to temp file for Qt playback.
- `overlay.py` — PySide6 frameless widget, centered at bottom of screen with drop shadow. States: idle (hidden), recording (red), processing (orange), done (green flash, auto-hides after 1.2s).
- `hotkey.py` — Global hotkey via `keyboard` library's low-level hook. Tracks individual key up/down events via scan codes.
- `recorder.py` — `sounddevice.InputStream` with callback appending to buffer (16-bit PCM). Thread-safe via lock. Returns WAV bytes on stop.
- `typer.py` — Short text (≤50 chars): `pynput.keyboard.Controller.type()`. Long text: clipboard save → paste via Ctrl+V → clipboard restore. Uses Win32 clipboard API directly via ctypes.
- `api_client.py` — httpx async client with 30s timeout.

### Shared
- `config.py` — Loads YAML from `config.yaml` in repo root, falls back to defaults. `save_config()` writes back to YAML. Pydantic models. `AudioConfig` includes optional `device_id`. Supports `${ENV_VAR}` resolution in string values.
- `models.py` — Pydantic models: `TranscribeResponse` (includes `whisper_model`, `whisper_time_ms`, `steps: list[StepDetail]`), `HealthResponse`, `StepDetail` (per-pipeline-step I/O and timing).
- `cli.py` — Entry points: `run_server()`, `run_client_ui()`, `main()` (server + GUI client). Configures rotating file logging and unhandled exception hooks. Server auto-pulls Ollama model on start.

## Configuration

Config file: `config.yaml` in the repo root (gitignored — copy from defaults in `config.py`). Edit and restart to apply, or use the GUI client's Settings tab to edit live. Key sections: `server`, `whisper`, `hotkey`, `audio`, `pipeline`, `llm`.

Pipeline steps are configured in YAML and built dynamically in `Pipeline._build_steps()`. To add a new step type: subclass `PipelineStep`, add a branch in `_build_steps()`.

## Key Constraints

- `typer.py` uses Windows-specific ctypes calls (`user32`, `kernel32`) for clipboard and window focus — not cross-platform
- `keyboard` library needs the process to be running (not as a Windows service) for global hooks
- Whisper model loads at server startup (3-5s) and stays in GPU VRAM — no cold-start penalty per request
- Ollama runs in Docker with NVIDIA GPU passthrough (`docker-compose.yaml`) — nothing installed on host
- GUI client requires PySide6 >= 6.6 (Qt 6)
- All code, comments, and docs in English
