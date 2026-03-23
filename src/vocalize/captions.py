"""Live captions: continuous WASAPI loopback transcription → files + Rich terminal UI.

Two-tier transcription:
  - Draft every N seconds (beam=1, fast) — shown in yellow in the terminal.
  - Final every M seconds (beam=5, quality) — written to history and both output files.

Three threads:
  1. Reader thread  — reads WASAPI stream into draft/final buffers, never blocks on GPU.
  2. Transcription thread — serialises all GPU calls via queue; applies silence + hallucination filters.
  3. Main thread — runs Rich Live UI at 4 Hz, drains display queue, writes files.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

logger = logging.getLogger(__name__)

BLOCK_FRAMES = 512
_RMS_SILENCE = 0.001
_MAX_RETRIES = 5
_RETRY_BACKOFF = 1.0

_HALLUCINATIONS: frozenset[str] = frozenset({
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "subtitles by",
    "transcribed by",
    "[music]",
    "[applause]",
    "you",
    "gracias por ver",
    "suscríbete",
})


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def list_devices() -> None:
    """Print all audio devices, highlighting loopback-capable ones."""
    with pyaudio.PyAudio() as p:
        print(f"  {'ID':>4}  {'Name':<55}  {'In':>3}  {'Out':>3}  {'Rate':>7}  Loopback")
        print("  " + "-" * 88)
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            is_loopback = d.get("isLoopbackDevice", False)
            tag = " <-- loopback" if is_loopback else ""
            print(
                f"   {i:>4}  {d['name']:<55}  "
                f"{int(d['maxInputChannels']):>3}  {int(d['maxOutputChannels']):>3}  "
                f"{int(d['defaultSampleRate']):>7}{tag}"
            )
    print("\nPass a loopback device ID with -d <ID> to capture that specific output.")


def _find_default_loopback(p: pyaudio.PyAudio) -> dict | None:
    """Return device info for the loopback of the default WASAPI output."""
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        logger.warning("WASAPI not available on this system.")
        return None

    default_out_idx = wasapi_info["defaultOutputDevice"]
    default_out = p.get_device_info_by_index(default_out_idx)
    logger.debug("Default output device: [%d] %s", default_out_idx, default_out["name"])

    for loopback in p.get_loopback_device_info_generator():
        if default_out["name"] in loopback["name"]:
            logger.debug("Matched loopback: [%d] %s", loopback["index"], loopback["name"])
            return loopback

    for loopback in p.get_loopback_device_info_generator():
        logger.debug("Falling back to loopback: [%d] %s", loopback["index"], loopback["name"])
        return loopback

    return None


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def _is_silent(audio: np.ndarray) -> bool:
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) < _RMS_SILENCE


def _is_hallucination(text: str) -> bool:
    return text.strip().lower().rstrip(".") in _HALLUCINATIONS


# ---------------------------------------------------------------------------
# UI state and layout
# ---------------------------------------------------------------------------

@dataclass
class _UIState:
    history: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=200))
    drafts: list[tuple[str, str]] = field(default_factory=list)
    draft_interval: int = 8
    final_interval: int = 24
    device_name: str = ""
    language: str = "auto"
    max_drafts: int = 3  # updated each render based on terminal height


def _build_layout(state: _UIState):
    from rich.console import Group
    from rich import box
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    try:
        term_height = os.get_terminal_size().lines
    except OSError:
        term_height = 30

    header_size = 3
    remaining = max(term_height - header_size, 8)
    drafts_size = max(int(remaining * 0.30), 4)
    history_size = remaining - drafts_size

    # How many draft lines fit: panel content rows minus a 3-line safety buffer for wraps
    state.max_drafts = max((drafts_size - 2) - 3, 1)

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=header_size),
        Layout(name="history", size=history_size),
        Layout(name="drafts", size=drafts_size),
    )

    # Header
    lang_display = state.language or "auto"
    header_text = Text()
    header_text.append(" LIVE CAPTIONS", style="bold white")
    header_text.append(f"   {state.device_name}", style="dim")
    header_text.append(f"   lang={lang_display}", style="cyan")
    header_text.append(f"   draft={state.draft_interval}s / final={state.final_interval}s", style="dim")
    header_text.append("   Ctrl+C to stop", style="dim red")
    layout["header"].update(Panel(header_text, style="bold"))

    # History panel — show only as many rows as fit (panel border = 2 lines)
    rows_visible = max(history_size - 2, 1)
    visible_history = list(state.history)[-rows_visible:]
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1), expand=True)
    table.add_column("ts", style="dim green", no_wrap=True, width=10)
    table.add_column("text", style="white")
    for ts_str, text in visible_history:
        table.add_row(f"[{ts_str}]", text)
    layout["history"].update(Panel(table, title="[bold white]TRANSCRIPTIONS[/bold white]", border_style="white"))

    # Drafts panel
    draft_items: list = []
    for ts_str, text in state.drafts:
        draft_items.append(Text(f"  >>  [{ts_str}] {text}", style="dim yellow"))
    if not draft_items:
        draft_items.append(Text("  >>  waiting...", style="dim"))

    layout["drafts"].update(Panel(
        Group(*draft_items),
        title="[yellow]DRAFTS[/yellow]",
        border_style="yellow",
    ))

    return layout


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

def _reader_thread(
    p: pyaudio.PyAudio,
    dev_info: dict,
    sample_rate: int,
    channels: int,
    draft_frames: int,
    final_frames: int,
    draft_overlap_frames: int,
    final_overlap_frames: int,
    transcription_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    """Reads audio from WASAPI stream continuously, enqueues draft/final jobs."""
    for attempt in range(_MAX_RETRIES):
        if stop_event.is_set():
            return
        try:
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=sample_rate,
                frames_per_buffer=BLOCK_FRAMES,
                input=True,
                input_device_index=int(dev_info["index"]),
            )
        except OSError as e:
            logger.error("Failed to open audio stream (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF)
                continue
            stop_event.set()
            return

        draft_buffer: list[np.ndarray] = []
        final_buffer: list[np.ndarray] = []
        draft_collected = 0
        final_collected = 0
        # Overlap tails: last N frames of the previous window, prepended to the next
        draft_overlap = np.zeros((0, channels), dtype=np.float32)
        final_overlap = np.zeros((0, channels), dtype=np.float32)

        try:
            while not stop_event.is_set():
                try:
                    raw = stream.read(BLOCK_FRAMES, exception_on_overflow=False)
                except OSError as e:
                    logger.warning("Audio read error (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, e)
                    break

                chunk = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)
                draft_buffer.append(chunk)
                final_buffer.append(chunk)
                draft_collected += len(chunk)
                final_collected += len(chunk)

                # Final check first — takes priority, cancels coincident draft
                if final_collected >= final_frames:
                    final_audio = np.concatenate(final_buffer)
                    send = np.concatenate([final_overlap, final_audio]) if len(final_overlap) else final_audio
                    try:
                        transcription_queue.put(("final", send), timeout=2.0)
                    except queue.Full:
                        logger.warning("Transcription queue full — dropping final job")
                    # Save tail for next final; seed next draft overlap from end of this window too
                    final_overlap = final_audio[-final_overlap_frames:]
                    draft_overlap = final_audio[-draft_overlap_frames:]
                    final_buffer.clear()
                    final_collected = 0
                    draft_buffer.clear()
                    draft_collected = 0

                elif draft_collected >= draft_frames:
                    draft_audio = np.concatenate(draft_buffer)
                    send = np.concatenate([draft_overlap, draft_audio]) if len(draft_overlap) else draft_audio
                    try:
                        transcription_queue.put_nowait(("draft", send))
                    except queue.Full:
                        logger.debug("Transcription queue full — dropping draft job")
                    draft_overlap = draft_audio[-draft_overlap_frames:]
                    draft_buffer.clear()
                    draft_collected = 0

        finally:
            stream.stop_stream()
            stream.close()

        if stop_event.is_set():
            return

        # Stream broke — retry
        logger.warning("Reconnecting audio stream (attempt %d/%d)...", attempt + 1, _MAX_RETRIES)
        time.sleep(_RETRY_BACKOFF)

    logger.error("Max reconnect attempts reached. Stopping.")
    stop_event.set()


def _transcription_thread(
    transcriber,
    sample_rate: int,
    language: str | None,
    transcription_queue: queue.Queue,
    display_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    """Pulls audio jobs from queue, runs Whisper, pushes (kind, text, ts) to display queue."""
    while not stop_event.is_set() or not transcription_queue.empty():
        try:
            kind, audio = transcription_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if _is_silent(audio):
            if kind == "final":
                display_queue.put(("final", "", datetime.now()))
            continue

        beam = 1 if kind == "draft" else 5
        try:
            text, _ = transcriber.transcribe_audio(
                audio,
                sample_rate=sample_rate,
                language=language,
                beam_size=beam,
            )
            text = text.strip()
        except Exception:
            logger.exception("Transcription failed")
            text = ""

        if text and _is_hallucination(text):
            logger.debug("Filtered hallucination: %r", text)
            text = ""

        display_queue.put((kind, text, datetime.now()))


# ---------------------------------------------------------------------------
# Event handler and main run loop
# ---------------------------------------------------------------------------

def _handle_event(
    kind: str,
    text: str,
    ts: datetime,
    state: _UIState,
    all_file,
    final_file,
) -> None:
    ts_str = ts.strftime("%H:%M:%S")
    text = text.strip()

    if kind == "draft":
        if text:
            keep = max(state.max_drafts - 1, 0)
            state.drafts = state.drafts[-keep:] + [(ts_str, text)] if keep else [(ts_str, text)]
            all_file.write(f"[DRAFT {ts_str}] {text}\n")
            all_file.flush()

    elif kind == "final":
        if text:
            state.history.append((ts_str, text))
            all_file.write(f"[{ts_str}] {text}\n")
            all_file.flush()
            final_file.write(f"[{ts_str}] {text}\n")
            final_file.flush()


def run(
    output_dir: Path,
    device_id: int | None,
    draft_interval: int,
    final_interval: int,
    language: str | None,
) -> None:
    from rich.console import Console
    from rich.live import Live

    from vocalize.config import load_config
    from vocalize.server.transcriber import Transcriber

    config = load_config()
    transcriber = Transcriber(config.whisper)
    logger.info("Loading Whisper model %s...", config.whisper.model)
    transcriber.load()
    logger.info("Whisper model loaded.")

    stop_event = threading.Event()

    # SIGTERM support (e.g. killed by process manager)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    output_dir.mkdir(parents=True, exist_ok=True)
    session_stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    all_path = output_dir / f"captions_{session_stamp}.txt"
    final_path = output_dir / f"captions_{session_stamp}_final.txt"

    transcription_queue: queue.Queue = queue.Queue(maxsize=8)
    display_queue: queue.Queue = queue.Queue()

    with pyaudio.PyAudio() as p:
        if device_id is not None:
            dev_info = p.get_device_info_by_index(device_id)
        else:
            dev_info = _find_default_loopback(p)
            if dev_info is None:
                logger.error(
                    "No WASAPI loopback device found. "
                    "Run --list-devices and pass a loopback device ID with -d <ID>."
                )
                sys.exit(1)

        sample_rate = int(dev_info["defaultSampleRate"])
        channels = min(int(dev_info["maxInputChannels"]) or 2, 2)
        draft_frames = int(sample_rate * draft_interval)
        final_frames = int(sample_rate * final_interval)
        draft_overlap_frames = int(sample_rate * 1)   # 1s context from previous draft
        final_overlap_frames = int(sample_rate * 2)   # 2s context from previous final

        logger.info(
            "Capturing from [%d] %s @ %dHz, %dch  draft=%ds final=%ds  language=%s",
            dev_info["index"], dev_info["name"], sample_rate, channels,
            draft_interval, final_interval, language or "auto",
        )
        logger.info("Output dir: %s", output_dir.resolve())

        session_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"--- Session {session_ts} ---\n\n"

        with (
            open(all_path, "a", encoding="utf-8") as all_file,
            open(final_path, "a", encoding="utf-8") as final_file,
        ):
            all_file.write(header)
            all_file.flush()
            final_file.write(header)
            final_file.flush()

            state = _UIState(
                draft_interval=draft_interval,
                final_interval=final_interval,
                device_name=dev_info["name"][:40],
                language=language or "auto",
            )

            reader = threading.Thread(
                target=_reader_thread,
                args=(p, dev_info, sample_rate, channels, draft_frames, final_frames,
                      draft_overlap_frames, final_overlap_frames,
                      transcription_queue, stop_event),
                daemon=True,
                name="reader",
            )
            transcriber_t = threading.Thread(
                target=_transcription_thread,
                args=(transcriber, sample_rate, language, transcription_queue,
                      display_queue, stop_event),
                daemon=True,
                name="transcriber",
            )

            reader.start()
            transcriber_t.start()

            console = Console(force_terminal=True)
            layout = _build_layout(state)

            try:
                with Live(layout, console=console, auto_refresh=False, screen=False) as live:
                    while not stop_event.is_set():
                        changed = False

                        # Drain display queue — each event marks a change
                        while True:
                            try:
                                kind, text, ts = display_queue.get_nowait()
                            except queue.Empty:
                                break
                            _handle_event(kind, text, ts, state, all_file, final_file)
                            changed = True

                        if changed:
                            live.update(_build_layout(state), refresh=True)

                        time.sleep(0.25)

                    # Drain remaining display events after stop
                    while not display_queue.empty():
                        try:
                            kind, text, ts = display_queue.get_nowait()
                            _handle_event(kind, text, ts, state, all_file, final_file)
                        except queue.Empty:
                            break

                    live.update(_build_layout(state), refresh=True)
            except KeyboardInterrupt:
                stop_event.set()

            reader.join(timeout=3.0)
            transcriber_t.join(timeout=5.0)

    console.print(f"\n[green]Session saved:[/green]")
    console.print(f"  All:    {all_path}")
    console.print(f"  Finals: {final_path}")
    logger.info("Live captions stopped.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Live captions: transcribe system audio (WASAPI loopback) to files. "
            "Captures what plays through your speakers — meetings, videos, etc."
        ),
    )
    parser.add_argument(
        "--draft-interval", "-D",
        type=int,
        default=8,
        metavar="SECS",
        help="Seconds per draft transcription chunk (default: 8)",
    )
    parser.add_argument(
        "--final-interval", "-F",
        type=int,
        default=24,
        metavar="SECS",
        help="Seconds per final quality transcription (default: 24, must be multiple of --draft-interval)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        metavar="DIR",
        help="Directory for output files (default: output/)",
    )
    parser.add_argument(
        "--device", "-d",
        type=int,
        default=None,
        metavar="ID",
        help="Loopback device ID. Use --list-devices to find it.",
    )
    parser.add_argument(
        "--language", "-l",
        type=str,
        default=None,
        metavar="LANG",
        help="Language code ('es', 'en', etc.). Default: auto-detect per chunk.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List audio devices and exit.",
    )

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    if args.final_interval % args.draft_interval != 0:
        parser.error(
            f"--final-interval ({args.final_interval}) must be a multiple of "
            f"--draft-interval ({args.draft_interval})"
        )
    if args.draft_interval < 1 or args.final_interval < 1:
        parser.error("Intervals must be >= 1 second")

    run(
        output_dir=args.output_dir,
        device_id=args.device,
        draft_interval=args.draft_interval,
        final_interval=args.final_interval,
        language=args.language,
    )
