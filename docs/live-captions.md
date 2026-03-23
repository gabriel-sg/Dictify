# Live Captions

Transcribe system audio in real time — meetings, videos, podcasts — directly from your speakers (WASAPI loopback). Output appears in the terminal and is saved to timestamped files in `output/`.

Requires Windows (WASAPI loopback) and a loaded Whisper model on GPU.

---

## Quick start

```bash
uv run live-captions
```

Press **Ctrl+C** to stop. Files are saved automatically.

---

## How it works

### Two-tier transcription

Every session runs two transcription tiers simultaneously:

| Tier | Interval | Beam size | Overlap | Purpose |
|------|----------|-----------|---------|---------|
| **Draft** | every 8s | 1 (fast) | 1s | Live feedback in the terminal |
| **Final** | every 24s | 5 (quality) | 2s | Authoritative record, written to files |

Drafts give immediate feedback (~0.5–1s GPU latency). Finals cover a longer window with more linguistic context and higher beam search quality. Both intervals are configurable.

**Overlap:** each window prepends audio from the tail of the previous window before sending to Whisper. This gives the model context for words that fall on a chunk boundary, significantly reducing cut sentences and mid-word errors.

### Three-thread architecture

```
[Reader thread]  →  transcription_queue  →  [Transcription thread]  →  display_queue  →  [Main thread / UI]
```

- **Reader thread** reads the WASAPI loopback stream continuously into draft and final audio buffers. It never waits on GPU — audio capture never drops frames due to a slow transcription.
- **Transcription thread** serialises all GPU calls. Applies silence detection (skips chunks with RMS < 0.001) and filters known Whisper hallucinations before pushing results to the display queue.
- **Main thread** renders the Rich terminal UI on demand (only when new text arrives) and writes to both output files.

### Silence and hallucination filtering

Silent audio (e.g. paused video, quiet room) is detected by RMS energy and skipped before reaching the GPU. Common Whisper hallucinations ("thanks for watching", "[music]", etc.) are filtered after transcription. Neither appears in output files.

---

## Terminal UI

```
+------------------------------------------------------------------+
|  LIVE CAPTIONS   Speakers (Realtek)   lang=es   draft=8s/final=24s  Ctrl+C to stop  |
+------------------------------------------------------------------+
|                       TRANSCRIPTIONS                             |
|   [17:34:15]  He was saying the implementation requires...       |
|   [17:34:39]  consideration of the buffer size in production.    |
|   [17:35:03]  The key insight is that serialising GPU calls...   |
|                                                                  |
+------------------------------------------------------------------+
|                          DRAFTS                                  |
|   >>  [17:35:11]  the second point is about threading and...     |
|   >>  [17:35:19]  why serialising GPU calls matters for perf...  |
|   >>  [17:35:27]  the third point covers the overlap window...   |
+------------------------------------------------------------------+
```

- **TRANSCRIPTIONS** — permanent history of final transcriptions. Fills ~70% of the terminal height; scrolls as more lines arrive.
- **DRAFTS** — rolling FIFO of recent draft lines shown in yellow. Fills ~30% of the terminal height. Lines are never cleared between finals — new drafts push old ones out from the top naturally.

Both panels adapt automatically when you resize the terminal. The number of visible draft lines scales with the panel height, with a small safety buffer for wrapped lines.

---

## Output files

Each session creates two files in `output/` (configurable with `--output-dir`), named with the session start timestamp:

```
output/
  captions_2026-03-23_174200.txt        # all transcriptions
  captions_2026-03-23_174200_final.txt  # finals only
```

### `captions_STAMP.txt` — full log

Contains everything: drafts (prefixed `[DRAFT HH:MM:SS]`) and finals. Useful for comparing draft vs. final quality at any given moment.

```
--- Session 2026-03-23 17:42:00 ---

[DRAFT 17:42:08] he was talking about the implementation requires
[DRAFT 17:42:16] careful consideration of the buffer size
[DRAFT 17:42:24] and timing constraints for real-time processing
[17:42:24] He was talking about how the implementation requires careful consideration of the buffer size and timing constraints for real-time processing.
[DRAFT 17:42:32] the second point is about threading...
```

### `captions_STAMP_final.txt` — finals only

Clean transcript with only the high-quality 24s transcriptions. Ready to paste into an LLM for summarisation, Q&A, or notes.

```
--- Session 2026-03-23 17:42:00 ---

[17:42:24] He was talking about how the implementation requires careful consideration of the buffer size and timing constraints for real-time processing.
[17:43:12] The key insight is that serialising GPU calls through a queue prevents contention while maintaining throughput.
```

---

## CLI reference

```
uv run live-captions [options]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--draft-interval SECS` | `-D` | `8` | Seconds per draft transcription |
| `--final-interval SECS` | `-F` | `24` | Seconds per final transcription (must be a multiple of `--draft-interval`) |
| `--output-dir DIR` | | `output/` | Directory for output files |
| `--device ID` | `-d` | auto | Loopback device ID (see `--list-devices`) |
| `--language LANG` | `-l` | auto-detect | Language code: `es`, `en`, `fr`, etc. |
| `--list-devices` | | | Print all audio devices and exit |

### Examples

```bash
# Default — Spanish, 8s drafts, 24s finals
uv run live-captions

# English, faster finals (16s)
uv run live-captions -l en -D 8 -F 16

# Specific loopback device
uv run live-captions -d 12

# Save to a custom directory
uv run live-captions --output-dir ~/meetings/2026-03-23
```

---

## Finding your loopback device

By default the command auto-selects the loopback device for the system's default audio output. If you have multiple output devices or the auto-detection fails, list all devices and pass the correct ID:

```bash
uv run live-captions --list-devices
```

```
    ID  Name                                                     In  Out     Rate  Loopback
  ──────────────────────────────────────────────────────────────────────────────────────────
     0  Microsoft Sound Mapper - Input                            2    0    44100
     1  Microphone (Realtek Audio)                                2    0    44100
    12  Speakers (Realtek Audio) [Loopback]                       2    0    48000  <-- loopback
    13  HDMI Output (NVIDIA) [Loopback]                           2    0    48000  <-- loopback

uv run live-captions -d 12
```

---

## Tuning tips

**Better quality, more latency** — increase both intervals:
```bash
uv run live-captions -D 10 -F 30
```
Whisper was trained on 30s windows and performs best near that length. Larger finals give more linguistic context.

**Faster feedback, less accurate drafts** — decrease the draft interval:
```bash
uv run live-captions -D 5 -F 20
```

**Fix a language** — auto-detection runs per chunk and can switch mid-session. If you know the language, pin it:
```bash
uv run live-captions -l es
```

**Post-process the final file with an LLM** — the `_final.txt` file is designed for this. Example prompt:
```
Here is a transcript of a meeting. Summarise the key points and action items.

[paste contents of captions_*_final.txt]
```

---

## Requirements

- Windows 10/11 (WASAPI loopback is Windows-only)
- `pyaudiowpatch` — installed automatically via `uv sync`
- Whisper model on GPU — configured in `config.yaml` under `whisper.model` (default: `large-v3-turbo`)
- The Vocalize server does **not** need to be running — live captions loads Whisper directly in-process
