from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QScrollArea,
    QLabel, QGroupBox, QPlainTextEdit, QHeaderView, QMessageBox,
)

from vocalize.client_pyside6.audio_player import AudioPlayerWidget
from vocalize.client_pyside6.debug_store import DebugStore

logger = logging.getLogger(__name__)


class DebugTab(QWidget):
    debug_toggled = Signal(bool)

    def __init__(self, debug_store: DebugStore, parent=None):
        super().__init__(parent)
        self._store = debug_store

        layout = QVBoxLayout(self)

        # --- Top bar ---
        top_bar = QHBoxLayout()

        self._enable_check = QCheckBox("Enable debug logging")
        self._enable_check.setChecked(debug_store.enabled)
        self._enable_check.toggled.connect(self._on_toggle)
        top_bar.addWidget(self._enable_check)

        top_bar.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self.refresh_list)
        top_bar.addWidget(refresh_btn)

        clear_btn = QPushButton("Clear All")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._clear_all)
        top_bar.addWidget(clear_btn)

        layout.addLayout(top_bar)

        # --- Splitter: table + detail ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Time", "Lang", "Duration", "Text"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.verticalHeader().setVisible(False)
        self._table.currentCellChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._table)

        # Right: detail scroll area
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_widget = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_widget)
        self._detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Audio section
        audio_group = QGroupBox("Audio")
        audio_layout = QVBoxLayout(audio_group)
        self._audio_player = AudioPlayerWidget()
        audio_layout.addWidget(self._audio_player)
        self._detail_layout.addWidget(audio_group)

        # Transcription section
        transcription_group = QGroupBox("Transcription (Whisper)")
        trans_layout = QVBoxLayout(transcription_group)
        self._whisper_info = QLabel("")
        self._whisper_info.setWordWrap(True)
        trans_layout.addWidget(self._whisper_info)
        self._raw_text = QPlainTextEdit()
        self._raw_text.setReadOnly(True)
        self._raw_text.setMaximumHeight(100)
        trans_layout.addWidget(QLabel("Raw transcription:"))
        trans_layout.addWidget(self._raw_text)
        self._detail_layout.addWidget(transcription_group)

        # LLM section
        self._llm_group = QGroupBox("LLM Post-Processing")
        self._llm_layout = QVBoxLayout(self._llm_group)
        self._llm_content = QWidget()
        self._llm_content_layout = QVBoxLayout(self._llm_content)
        self._llm_content_layout.setContentsMargins(0, 0, 0, 0)
        self._llm_layout.addWidget(self._llm_content)
        self._detail_layout.addWidget(self._llm_group)

        # Final output section
        output_group = QGroupBox("Final Output")
        output_layout = QVBoxLayout(output_group)
        self._final_text = QPlainTextEdit()
        self._final_text.setReadOnly(True)
        self._final_text.setMaximumHeight(100)
        output_layout.addWidget(self._final_text)
        self._detail_layout.addWidget(output_group)

        self._detail_layout.addStretch()

        self._detail_scroll.setWidget(self._detail_widget)
        splitter.addWidget(self._detail_scroll)

        splitter.setSizes([350, 550])
        layout.addWidget(splitter)

        # Store interaction IDs for table rows
        self._row_ids: list[int] = []

        # Load existing history on startup
        self.refresh_list()

    def refresh_list(self) -> None:
        summaries = self._store.list_recent(200)
        self._table.setRowCount(len(summaries))
        self._row_ids.clear()

        for row, s in enumerate(summaries):
            self._row_ids.append(s.id)
            # Time: show just time portion if today, else date+time
            time_str = s.timestamp[11:19] if len(s.timestamp) > 19 else s.timestamp
            self._table.setItem(row, 0, QTableWidgetItem(time_str))
            self._table.setItem(row, 1, QTableWidgetItem(s.language or ""))
            self._table.setItem(row, 2, QTableWidgetItem(f"{s.audio_duration_s:.1f}s"))
            text_item = QTableWidgetItem(s.final_text[:80])
            text_item.setToolTip(s.final_text)
            self._table.setItem(row, 3, text_item)

    def _on_toggle(self, checked: bool) -> None:
        self._store.enabled = checked
        self.debug_toggled.emit(checked)
        logger.info("Debug logging %s", "enabled" if checked else "disabled")

    def _clear_all(self) -> None:
        reply = QMessageBox.question(
            self, "Clear All",
            "Delete all debug history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._store.clear_all()
            self.refresh_list()
            self._clear_detail()

    def _on_selection_changed(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        if row < 0 or row >= len(self._row_ids):
            return
        interaction_id = self._row_ids[row]
        interaction = self._store.get(interaction_id)
        if interaction is None:
            return
        self._show_detail(interaction)

    def _show_detail(self, interaction) -> None:
        # Audio
        self._audio_player.load_audio(interaction.audio_blob, interaction.audio_duration_s)

        # Whisper
        self._whisper_info.setText(
            f"Model: {interaction.whisper_model or 'N/A'}  |  "
            f"Time: {interaction.whisper_time_ms}ms"
        )
        self._raw_text.setPlainText(interaction.raw_text)

        # LLM steps - clear old content
        self._clear_llm_content()

        if interaction.steps:
            for i, step in enumerate(interaction.steps):
                step_label = QLabel(
                    f"<b>Step {i+1}: {step.step_type}</b>  |  "
                    f"Model: {step.model or 'N/A'}  |  "
                    f"Time: {step.time_ms}ms"
                )
                step_label.setWordWrap(True)
                self._llm_content_layout.addWidget(step_label)

                if step.system_prompt:
                    prompt_label = QLabel("System prompt:")
                    self._llm_content_layout.addWidget(prompt_label)
                    prompt_text = QPlainTextEdit(step.system_prompt)
                    prompt_text.setReadOnly(True)
                    prompt_text.setMaximumHeight(80)
                    self._llm_content_layout.addWidget(prompt_text)

                input_label = QLabel("Input:")
                self._llm_content_layout.addWidget(input_label)
                input_text = QPlainTextEdit(step.input_text)
                input_text.setReadOnly(True)
                input_text.setMaximumHeight(60)
                self._llm_content_layout.addWidget(input_text)

                output_label = QLabel("Output:")
                self._llm_content_layout.addWidget(output_label)
                output_text = QPlainTextEdit(step.output_text)
                output_text.setReadOnly(True)
                output_text.setMaximumHeight(60)
                self._llm_content_layout.addWidget(output_text)
        else:
            self._llm_content_layout.addWidget(QLabel("No pipeline steps recorded."))

        # Final output
        self._final_text.setPlainText(interaction.final_text)

    def _clear_llm_content(self) -> None:
        while self._llm_content_layout.count():
            item = self._llm_content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _clear_detail(self) -> None:
        self._audio_player.load_audio(b"")
        self._whisper_info.setText("")
        self._raw_text.clear()
        self._clear_llm_content()
        self._final_text.clear()
