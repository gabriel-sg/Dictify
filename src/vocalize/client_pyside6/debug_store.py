from __future__ import annotations

import json
import logging
import sqlite3
import struct
import wave
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from vocalize.models import StepDetail

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = _PROJECT_ROOT / "db" / "debug.db"


@dataclass
class DebugInteraction:
    id: int = 0
    timestamp: str = ""
    language: str = ""
    audio_blob: bytes = b""
    audio_duration_s: float = 0.0
    raw_text: str = ""
    final_text: str = ""
    whisper_model: str = ""
    whisper_time_ms: int = 0
    total_time_ms: int = 0
    steps: list[StepDetail] = field(default_factory=list)


@dataclass
class DebugInteractionSummary:
    id: int
    timestamp: str
    language: str
    audio_duration_s: float
    final_text: str


def _audio_duration(wav_bytes: bytes) -> float:
    if not wav_bytes:
        return 0.0
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / rate if rate else 0.0
    except Exception:
        return 0.0


class DebugStore:
    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()
        self.enabled = False

    def _create_table(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                language TEXT,
                audio_blob BLOB,
                audio_duration_s REAL,
                raw_text TEXT,
                final_text TEXT,
                whisper_model TEXT,
                whisper_time_ms INTEGER,
                total_time_ms INTEGER,
                steps_json TEXT
            )
        """)
        self._conn.commit()

    def save(self, interaction: DebugInteraction) -> int:
        if not self.enabled:
            return 0
        duration = _audio_duration(interaction.audio_blob)
        steps_json = json.dumps(
            [s.model_dump() for s in interaction.steps] if interaction.steps else []
        )
        cursor = self._conn.execute(
            """INSERT INTO interactions
               (timestamp, language, audio_blob, audio_duration_s, raw_text,
                final_text, whisper_model, whisper_time_ms, total_time_ms, steps_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                interaction.timestamp or datetime.now().isoformat(),
                interaction.language,
                interaction.audio_blob,
                duration,
                interaction.raw_text,
                interaction.final_text,
                interaction.whisper_model,
                interaction.whisper_time_ms,
                interaction.total_time_ms,
                steps_json,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def list_recent(self, limit: int = 100) -> list[DebugInteractionSummary]:
        rows = self._conn.execute(
            """SELECT id, timestamp, language, audio_duration_s, final_text
               FROM interactions ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            DebugInteractionSummary(
                id=r[0], timestamp=r[1], language=r[2],
                audio_duration_s=r[3] or 0.0,
                final_text=(r[4] or "")[:100],
            )
            for r in rows
        ]

    def get(self, interaction_id: int) -> DebugInteraction | None:
        row = self._conn.execute(
            """SELECT id, timestamp, language, audio_blob, audio_duration_s,
                      raw_text, final_text, whisper_model, whisper_time_ms,
                      total_time_ms, steps_json
               FROM interactions WHERE id = ?""",
            (interaction_id,),
        ).fetchone()
        if not row:
            return None
        steps_data = json.loads(row[10]) if row[10] else []
        steps = [StepDetail.model_validate(s) for s in steps_data]
        return DebugInteraction(
            id=row[0], timestamp=row[1], language=row[2],
            audio_blob=row[3] or b"", audio_duration_s=row[4] or 0.0,
            raw_text=row[5] or "", final_text=row[6] or "",
            whisper_model=row[7] or "", whisper_time_ms=row[8] or 0,
            total_time_ms=row[9] or 0, steps=steps,
        )

    def delete(self, interaction_id: int) -> None:
        self._conn.execute("DELETE FROM interactions WHERE id = ?", (interaction_id,))
        self._conn.commit()

    def clear_all(self) -> None:
        self._conn.execute("DELETE FROM interactions")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
