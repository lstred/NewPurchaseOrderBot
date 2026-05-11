"""
AI tab — natural-language → SQL → results.

* User types a question in plain English.
* A configured LLM (Claude / OpenAI / Gemini) returns a SELECT query.
* SQL is validated (read-only) then executed via app.data.db.read_dataframe.
* Results render in a DataTable.

Configuration (provider, API key, model) lives in the Settings tab and is
persisted to %APPDATA%\\PurchaseOrderBot\\ai_config.json.
"""

from __future__ import annotations

import re
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ai.providers import call_provider, AIError, DEFAULT_MODELS
from app.ai.schema import SCHEMA_PROMPT
from app.data import store
from app.data.db import read_dataframe
from app.ui.widgets import DataTable, SectionTitle
import app.ui.theme as theme


# Forbidden keywords (case-insensitive, word-boundary)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|EXEC(?:UTE)?|MERGE|ALTER|CREATE|GRANT|REVOKE|XP_|SP_)\b",
    re.IGNORECASE,
)
_STARTS_SELECT = re.compile(r"^\s*(WITH|SELECT)\b", re.IGNORECASE)


def _sanitize_sql(text: str) -> str:
    """Strip code fences / common prose if the model added them despite instructions."""
    s = text.strip()
    # Remove ```sql / ``` fences
    if s.startswith("```"):
        s = s.strip("`")
        # remove leading 'sql' marker
        s = re.sub(r"^sql\s*\n?", "", s, flags=re.IGNORECASE)
        # drop closing fence remnant
        s = s.rstrip("`").strip()
    # If model returned "Here's the query:\nSELECT..." trim any prose before SELECT/WITH
    m = re.search(r"(?is)(SELECT|WITH)\s.*", s)
    if m:
        s = m.group(0)
    return s.strip().rstrip(";")


def validate_sql(sql: str) -> Optional[str]:
    """Return None if SQL is safe to run, else an error message."""
    if not sql:
        return "Empty SQL."
    if not _STARTS_SELECT.match(sql):
        return "Only SELECT (or WITH ... SELECT) queries are allowed."
    if _FORBIDDEN.search(sql):
        return "Query contains a forbidden keyword (write/DDL operations are blocked)."
    if ";" in sql:
        return "Multiple statements are not allowed (remove ';')."
    return None


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _AIWorker(QObject):
    finished = pyqtSignal(object)  # dict: {sql, df, error}

    def __init__(self, provider: str, api_key: str, model: str, question: str):
        super().__init__()
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._question = question

    def run(self) -> None:
        result = {"sql": "", "df": None, "error": None}
        try:
            raw = call_provider(
                self._provider, self._api_key, self._model,
                SCHEMA_PROMPT, self._question,
            )
            sql = _sanitize_sql(raw)
            result["sql"] = sql
            err = validate_sql(sql)
            if err:
                result["error"] = err
            else:
                result["df"] = read_dataframe(sql)
        except AIError as e:
            result["error"] = f"AI error: {e}"
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Tab widget
# ---------------------------------------------------------------------------

class AITab(QWidget):
    """Ask plain-English questions, get SQL + results."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_AIWorker] = None
        self._build_ui()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(SectionTitle("🤖  AI Query"))

        info = QLabel(
            "Ask a question about your data — for example:  "
            "<i>“top 20 SKUs by sales last 90 days”</i>  or  "
            "<i>“how many backordered lines for cost center 010 this month?”</i>"
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{theme.get('text_muted')};")
        root.addWidget(info)

        # Input row
        in_row = QHBoxLayout()
        in_row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type your question and press Enter…")
        self._input.returnPressed.connect(self._on_send)
        self._input.setMinimumHeight(34)
        self._send_btn = QPushButton("Ask")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setMinimumHeight(34)
        in_row.addWidget(self._input, stretch=1)
        in_row.addWidget(self._send_btn)
        root.addLayout(in_row)

        # Status / error label
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{theme.get('text_muted')};")
        root.addWidget(self._status)

        # Splitter: SQL on top, results on bottom
        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)

        sql_wrap = QWidget()
        sw = QVBoxLayout(sql_wrap)
        sw.setContentsMargins(0, 0, 0, 0)
        sw.setSpacing(4)
        sw.addWidget(SectionTitle("Generated SQL"))
        self._sql_view = QTextEdit()
        self._sql_view.setReadOnly(False)  # let user tweak before re-run if desired
        self._sql_view.setPlaceholderText("Generated SQL will appear here…")
        self._sql_view.setStyleSheet(
            f"background:{theme.get('bg_card')}; color:{theme.get('text')}; "
            f"font-family:Consolas,'Courier New',monospace; font-size:12px;"
        )
        sw.addWidget(self._sql_view)
        run_row = QHBoxLayout()
        run_row.addStretch(1)
        self._run_btn = QPushButton("▶ Run SQL")
        self._run_btn.clicked.connect(self._on_run_manual)
        run_row.addWidget(self._run_btn)
        sw.addLayout(run_row)
        split.addWidget(sql_wrap)

        res_wrap = QWidget()
        rw = QVBoxLayout(res_wrap)
        rw.setContentsMargins(0, 0, 0, 0)
        rw.setSpacing(4)
        rw.addWidget(SectionTitle("Results"))
        self._table = DataTable([], table_id="ai_results")
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rw.addWidget(self._table)
        split.addWidget(res_wrap)

        split.setSizes([220, 480])
        root.addWidget(split, stretch=1)

    # ---- send / receive ---------------------------------------------------

    def _on_send(self) -> None:
        question = self._input.text().strip()
        if not question:
            return
        cfg = store.get_ai_config()
        provider = cfg.get("provider", "anthropic")
        api_key = cfg.get("api_key", "")
        model = cfg.get("model") or DEFAULT_MODELS.get(provider, "")
        if not api_key:
            QMessageBox.warning(
                self, "AI not configured",
                "No API key is configured.\n\nGo to the Settings tab → AI Provider "
                "section and enter an API key for your chosen provider."
            )
            return
        # Don't double-fire
        try:
            if self._thread is not None and self._thread.isRunning():
                self._status.setText("Already running…")
                return
        except RuntimeError:
            self._thread = None
            self._worker = None

        self._set_busy(True, f"Asking {provider}…")
        self._sql_view.clear()

        self._thread = QThread(self)
        self._worker = _AIWorker(provider, api_key, model, question)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_run_manual(self) -> None:
        """Run whatever SQL is currently in the editor (lets user tweak then re-run)."""
        sql = _sanitize_sql(self._sql_view.toPlainText())
        if not sql:
            return
        err = validate_sql(sql)
        if err:
            self._status.setText(f"⚠ {err}")
            self._status.setStyleSheet(f"color:{theme.get('danger')};")
            return
        self._set_busy(True, "Running query…")
        try:
            df = read_dataframe(sql)
            self._render_results(df)
            self._status.setText(f"✓ {len(df):,} rows")
            self._status.setStyleSheet(f"color:{theme.get('success')};")
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"⚠ {type(e).__name__}: {e}")
            self._status.setStyleSheet(f"color:{theme.get('danger')};")
        finally:
            self._set_busy(False)

    def _on_finished(self, result: dict) -> None:
        self._set_busy(False)
        sql = result.get("sql", "")
        if sql:
            self._sql_view.setPlainText(sql)
        err = result.get("error")
        df = result.get("df")
        if err:
            self._status.setText(f"⚠ {err}")
            self._status.setStyleSheet(f"color:{theme.get('danger')};")
            return
        if df is None:
            self._status.setText("No results returned.")
            return
        self._render_results(df)
        self._status.setText(f"✓ {len(df):,} rows")
        self._status.setStyleSheet(f"color:{theme.get('success')};")

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    # ---- helpers ----------------------------------------------------------

    def _render_results(self, df: pd.DataFrame) -> None:
        cols = [str(c) for c in df.columns]
        self._table.set_columns(cols)
        rows: list[list[str]] = []
        for _, row in df.iterrows():
            cells: list[str] = []
            for c in cols:
                v = row[c]
                if pd.isna(v):
                    cells.append("—")
                elif isinstance(v, float):
                    cells.append(f"{v:,.2f}")
                elif isinstance(v, int):
                    cells.append(f"{v:,}")
                else:
                    cells.append(str(v))
            rows.append(cells)
        self._table.populate(rows)

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._send_btn.setEnabled(not busy)
        self._run_btn.setEnabled(not busy)
        self._input.setEnabled(not busy)
        if busy:
            self._status.setStyleSheet(f"color:{theme.get('text_muted')};")
            self._status.setText(msg or "Working…")

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self._thread is not None and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
        except RuntimeError:
            pass
        super().closeEvent(event)
