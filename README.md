# Vocalize

Push-to-talk voice-to-text for your desktop. Hold a hotkey, speak, release — your words appear as text at the cursor position, cleaned up by an LLM.

## How It Works

```
Hold hotkey ──> Microphone records ──> Release hotkey
                                            │
                                            ▼
                                   Audio sent to server
                                            │
                                            ▼
                                  Whisper transcribes
                                    speech to text
                                            │
                                            ▼
                                  LLM cleans up text          ← skipped with raw hotkey
                                 (grammar, filler words)
                                            │
                                            ▼
                                  Text typed at cursor
```

The app uses a **client-server architecture**:

- **Server** — Runs Whisper (speech-to-text) on your GPU, then passes the result through an LLM pipeline for post-processing. Can run on a remote machine.
- **Client** — PySide6 GUI app. Captures your hotkey, records audio, sends it to the server, and types the result into whatever app has focus.

A small overlay appears on your screen: red while recording, orange while processing, green when done.

## Requirements

- **OS:** Windows 10/11 (client uses Win32 APIs for clipboard and window management)
- **Python:** 3.11+
- **GPU:** NVIDIA GPU with CUDA support (for Whisper inference)
- **Docker:** For running Ollama (LLM post-processing)
- **Package manager:** [uv](https://docs.astral.sh/uv/)

### Hardware

- Whisper `large-v3-turbo` uses ~6 GB of VRAM. Smaller models (e.g. `base`, `small`) use less.
- The Ollama LLM model runs separately in Docker with GPU passthrough. A 4B model needs ~3 GB VRAM; a 12B model needs ~8 GB.

## Getting Started

### 1. Clone and install

```bash
git clone https://github.com/gabriel-sg/vocalize.git
cd vocalize
uv sync
```

### 2. Start Ollama (LLM post-processing)

Ollama runs in Docker with GPU passthrough — no need to install it on your host.

```bash
docker compose up -d
```

### 3. Configure

Copy the default config and edit as needed:

```bash
cp config.example.yaml config.yaml
```

Or create `config.yaml` in the repo root. Example:

```yaml
server:
  host: "127.0.0.1"
  port: 9876

whisper:
  model: "large-v3-turbo"    # Whisper model size
  device: "cuda"
  compute_type: "float16"
  beam_size: 3
  vad_filter: true
  language: "es"             # Default language (overridden per-hotkey)

hotkey:
  keys_es: "ctrl+alt+s"     # Hold to record in Spanish (with LLM)
  keys_en: "ctrl+alt+e"     # Hold to record in English (with LLM)
  keys_raw: "ctrl+alt+z"    # Hold to record without LLM (raw Whisper output)
  raw_language: "auto"      # Language for raw hotkey: "es", "en", or "auto"

audio:
  sample_rate: 16000
  channels: 0               # 0 = auto-detect from device
  input_gain: 1.0           # Software amplification (1.0 = no change)

pipeline:
  steps:
    - type: "llm_rewrite"
      enabled: true
      params:
        prompt_file: "prompts/transcription_editor.md"
        temperature: 0.1
        max_tokens: 512

llm:
  provider: "ollama"
  model: "nemotron-3-nano:4b"
  base_url: "http://localhost:11434/v1"
  api_key: "ollama"
```

If no `config.yaml` exists, sensible defaults are used. The GUI client can also edit and save settings from within the app.

### 4. Run

The server auto-pulls the configured Ollama model on first startup.

```bash
# Start server + GUI client together (recommended)
uv run vocalize

# Or start components separately (e.g. server on another machine)
uv run server   # server only
uv run ui       # GUI client only (connects to a running server)
```

### 5. Use it

1. Hold **Ctrl+Alt+S** to record in Spanish, or **Ctrl+Alt+E** for English (both run LLM post-processing).
2. Hold **Ctrl+Alt+Z** to record without LLM — raw Whisper output, any language.
3. Speak while holding the hotkey.
4. Release the hotkey — the overlay turns orange while processing.
5. The transcribed text is typed at your cursor position.

## GUI Client

The PySide6 GUI client provides:

- **Settings tab** — Change microphone input device, customize hotkeys (with key capture), edit the LLM system prompt, and save configuration without restarting.
- **Debug tab** — Toggle debug logging to inspect every interaction: play back recorded audio, view raw Whisper output, see each LLM pipeline step (input/output/timing), and the final typed text. History is stored in a local SQLite database (`db/debug.db`).
- **System tray** — Minimize to tray, restore with double-click, quit from context menu.
- **Overlay** — Frameless always-on-top widget centered at the bottom of the screen with drop shadow.

## Technology Stack

| Component | Technology |
|---|---|
| Speech-to-text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2 backend, GPU-accelerated) |
| LLM post-processing | [Ollama](https://ollama.com/) via OpenAI-compatible API |
| Server framework | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn |
| GUI client | [PySide6](https://doc.qt.io/qtforpython-6/) (Qt 6) |
| Audio capture | [sounddevice](https://python-sounddevice.readthedocs.io/) (PortAudio) |
| Audio playback | Qt Multimedia (`QMediaPlayer`) |
| Global hotkeys | [keyboard](https://github.com/boppreh/keyboard) (low-level OS hooks) |
| Text insertion | [pynput](https://github.com/moses-palmer/pynput) + Win32 clipboard API |
| Debug storage | SQLite (WAL mode) |
| HTTP client | [httpx](https://www.python-httpx.org/) (async) |
| Configuration | YAML + [Pydantic](https://docs.pydantic.dev/) |
| Package management | [uv](https://docs.astral.sh/uv/) + [hatchling](https://hatch.pypa.io/) |

## API Endpoints

The server exposes a REST API, so you can integrate it with other tools:

```bash
# Health check
curl http://localhost:9876/api/health

# Transcribe an audio file (with LLM post-processing)
curl -X POST http://localhost:9876/api/transcribe \
  -F "audio=@recording.wav" \
  -F "language=en"

# Transcribe without LLM post-processing
curl -X POST http://localhost:9876/api/transcribe \
  -F "audio=@recording.wav" \
  -F "language=en" \
  -F "skip_pipeline=true"
```

**Response:**
```json
{
  "text": "cleaned transcription",
  "raw_text": "raw whisper output",
  "language": "en",
  "processing_time_ms": 1234,
  "whisper_model": "large-v3-turbo",
  "whisper_time_ms": 820,
  "steps": [
    {
      "step_type": "llm_rewrite",
      "model": "nemotron-3-nano:4b",
      "input_text": "raw whisper output",
      "output_text": "cleaned transcription",
      "time_ms": 350
    }
  ]
}
```

## Logs

Rotating log files are written to `logs/` in the repo root:
- `logs/server.log` — server-side events, Whisper/LLM processing
- `logs/client-ui.log` — GUI client events
- `logs/client-ui-crash.log` — native crash dumps (faulthandler)

## License

MIT
