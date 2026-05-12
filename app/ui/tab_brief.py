"""
Daily Brief tab — replaces the v3.x AI Q&A chat (v4.0).

Features
--------
* Date picker (default = yesterday) selects the brief's reporting date.
* Provider/Model dropdowns (read from store.get_ai_config / write on change).
* "✨ Generate Brief" runs gather_brief_data + provider call on a worker thread.
* Brief is rendered as premium HTML inside QTextBrowser.
* Export: 📄 PDF (via QPrinter+QTextDocument), 📧 HTML (file), 📋 Copy HTML.
* Memory notes sidebar — same persistent "teach me once" mechanism as v3.x;
  the AI honors these on every brief generation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, QDate, QUrl
from PyQt6.QtGui import QTextDocument, QPageLayout, QPageSize
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFileDialog, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSizePolicy, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from app.ai.brief import (
    BriefResult, gather_brief_data, generate_brief,
)
from app.ai.brief_renderer import render_to_html
from app.ai.providers import DEFAULT_MODELS
from app.data import store
from app.services.metrics_service import DatasetBundle
import app.ui.theme as theme


# Per-provider known-good models, populated into the model combo.
_PROVIDER_MODELS = {
    "anthropic": ["claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5"],
    "openai":    ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o1-mini"],
    "google":    ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _BriefWorker(QObject):
    finished = pyqtSignal(object)  # BriefResult
    error    = pyqtSignal(str)

    def __init__(self, target_date: date, bundle: DatasetBundle,
                 provider: str, api_key: str, model: str):
        super().__init__()
        self._target = target_date
        self._bundle = bundle
        self._provider = provider
        self._api_key = api_key
        self._model = model

    def run(self) -> None:
        try:
            result = generate_brief(
                self._target, self._bundle,
                self._provider, self._api_key, self._model,
            )
            self.finished.emit(result)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Tab
# ---------------------------------------------------------------------------

class BriefTab(QWidget):

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._last_result: Optional[BriefResult] = None
        self._last_kpis: dict = {}
        self._thread: Optional[QThread] = None
        self._worker: Optional[_BriefWorker] = None

        self._build_ui()
        self._load_provider_config()
        self._refresh_notes()
        self._show_placeholder()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # ---- Top control bar -----------------------------------------------
        bar = QFrame()
        bar.setObjectName("briefBar")
        bar.setStyleSheet(f"""
            QFrame#briefBar {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:0.5 #8b5cf6, stop:1 #ec4899);
                border-radius: 10px;
                padding: 10px;
            }}
            QFrame#briefBar QLabel {{ color: white; font-weight: 600; }}
        """)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 8, 14, 8)
        bl.setSpacing(10)

        title = QLabel("📋  Daily Brief")
        title.setStyleSheet("color: white; font-size: 18px; font-weight: 800;")
        bl.addWidget(title)
        bl.addStretch()

        bl.addWidget(QLabel("Date:"))
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setMinimumWidth(120)
        y = date.today() - timedelta(days=1)
        self._date.setDate(QDate(y.year, y.month, y.day))
        bl.addWidget(self._date)

        bl.addSpacing(8)
        bl.addWidget(QLabel("Provider:"))
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["anthropic", "openai", "google"])
        self._provider_combo.setMinimumWidth(110)
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        bl.addWidget(self._provider_combo)

        bl.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setMinimumWidth(170)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        bl.addWidget(self._model_combo)

        self._btn_generate = QPushButton("✨ Generate Brief")
        self._btn_generate.setMinimumHeight(36)
        self._btn_generate.setStyleSheet("""
            QPushButton {
                background: white; color: #6366f1;
                border-radius: 6px; padding: 6px 18px;
                font-weight: 700;
            }
            QPushButton:hover { background: #f3f4f6; }
            QPushButton:disabled { background: #d1d5db; color: #9ca3af; }
        """)
        self._btn_generate.clicked.connect(self._on_generate)
        bl.addWidget(self._btn_generate)

        outer.addWidget(bar)

        # ---- Status line ---------------------------------------------------
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {theme.get('text_muted')}; padding: 0 4px;")
        outer.addWidget(self._status)

        # ---- Splitter: brief | notes sidebar -------------------------------
        split = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(split, 1)

        # Brief viewer
        self._viewer = QTextBrowser()
        self._viewer.setOpenExternalLinks(True)
        self._viewer.setStyleSheet("QTextBrowser { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; }")
        split.addWidget(self._viewer)

        # Sidebar (notes + actions)
        side = QFrame()
        side.setObjectName("briefSide")
        side.setStyleSheet(f"QFrame#briefSide {{ background: {theme.get('bg_card')}; border-radius: 8px; }}")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)

        # Export action group
        export_lbl = QLabel("EXPORT")
        export_lbl.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        sl.addWidget(export_lbl)

        self._btn_pdf  = QPushButton("📄  Export PDF")
        self._btn_html = QPushButton("📧  Export HTML for Email")
        self._btn_copy = QPushButton("📋  Copy HTML to Clipboard")
        for b in (self._btn_pdf, self._btn_html, self._btn_copy):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            sl.addWidget(b)
        self._btn_pdf.clicked.connect(self._on_export_pdf)
        self._btn_html.clicked.connect(self._on_export_html)
        self._btn_copy.clicked.connect(self._on_copy)

        sl.addSpacing(12)

        notes_hdr = QHBoxLayout()
        notes_lbl = QLabel("🧠  AI MEMORY NOTES")
        notes_lbl.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        notes_hdr.addWidget(notes_lbl)
        notes_hdr.addStretch()
        self._btn_add_note = QPushButton("+ Add")
        self._btn_add_note.setObjectName("flat")
        self._btn_add_note.clicked.connect(self._on_add_note)
        notes_hdr.addWidget(self._btn_add_note)
        sl.addLayout(notes_hdr)

        hint = QLabel("Rules and preferences applied on every brief.\nThe AI will honor these every time.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 11px;")
        sl.addWidget(hint)

        self._notes_list = QListWidget()
        self._notes_list.itemDoubleClicked.connect(lambda *_: self._on_edit_note())
        sl.addWidget(self._notes_list, 1)

        note_btns = QHBoxLayout()
        self._btn_edit_note = QPushButton("Edit")
        self._btn_edit_note.setObjectName("flat")
        self._btn_edit_note.clicked.connect(self._on_edit_note)
        note_btns.addWidget(self._btn_edit_note)
        self._btn_del_note = QPushButton("Delete")
        self._btn_del_note.setObjectName("flat")
        self._btn_del_note.clicked.connect(self._on_delete_note)
        note_btns.addWidget(self._btn_del_note)
        note_btns.addStretch()
        sl.addLayout(note_btns)

        side.setMinimumWidth(260)
        side.setMaximumWidth(360)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([900, 280])

    # ------------------------------------------------------------------ Public
    def refresh(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle

    # ------------------------------------------------------------------ Provider config
    def _load_provider_config(self) -> None:
        cfg = store.get_ai_config()
        provider = (cfg.get("provider") or "openai").lower()
        idx = self._provider_combo.findText(provider)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)
        self._populate_models(provider, preselect=cfg.get("model", ""))

    def _populate_models(self, provider: str, preselect: str = "") -> None:
        models = _PROVIDER_MODELS.get(provider, [])
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if preselect and preselect in models:
            self._model_combo.setCurrentText(preselect)
        elif models:
            # Use DEFAULT_MODELS recommended for this provider
            default = DEFAULT_MODELS.get(provider, models[0])
            if default in models:
                self._model_combo.setCurrentText(default)
        self._model_combo.blockSignals(False)

    def _on_provider_changed(self, name: str) -> None:
        self._populate_models(name)
        self._save_provider_config()

    def _on_model_changed(self, _name: str) -> None:
        self._save_provider_config()

    def _save_provider_config(self) -> None:
        cfg = store.get_ai_config()
        cfg["provider"] = self._provider_combo.currentText()
        cfg["model"]    = self._model_combo.currentText().strip()
        store.set_ai_config(cfg)

    # ------------------------------------------------------------------ Notes
    def _refresh_notes(self) -> None:
        self._notes_list.clear()
        for n in store.get_ai_notes():
            item = QListWidgetItem(n["text"])
            item.setData(Qt.ItemDataRole.UserRole, n["id"])
            self._notes_list.addItem(item)

    def _on_add_note(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "Add AI Memory Note",
            "Rule, preference, or business nuance the AI should always apply:",
        )
        if ok and text.strip():
            store.add_ai_note(text.strip())
            self._refresh_notes()

    def _on_edit_note(self) -> None:
        item = self._notes_list.currentItem()
        if not item:
            return
        nid = item.data(Qt.ItemDataRole.UserRole)
        text, ok = QInputDialog.getMultiLineText(
            self, "Edit AI Memory Note", "Rule:", item.text(),
        )
        if ok and text.strip():
            store.update_ai_note(nid, text.strip())
            self._refresh_notes()

    def _on_delete_note(self) -> None:
        item = self._notes_list.currentItem()
        if not item:
            return
        if QMessageBox.question(
            self, "Delete note", "Delete this AI memory note?"
        ) == QMessageBox.StandardButton.Yes:
            store.delete_ai_note(item.data(Qt.ItemDataRole.UserRole))
            self._refresh_notes()

    # ------------------------------------------------------------------ Generate
    def _on_generate(self) -> None:
        if self._bundle is None or self._bundle.sku_metrics.empty:
            QMessageBox.warning(
                self, "No data",
                "Wait for the main dataset to finish loading, then try again.",
            )
            return

        cfg = store.get_ai_config()
        api_key = cfg.get("api_key", "").strip()
        if not api_key:
            QMessageBox.information(
                self, "API key required",
                "Add your AI provider API key in the Settings tab first.",
            )
            return

        # Re-entrancy / dead-thread guard (mirrors main_window pattern)
        try:
            if self._thread is not None and self._thread.isRunning():
                return
        except RuntimeError:
            self._thread = None
            self._worker = None

        provider = self._provider_combo.currentText()
        model    = self._model_combo.currentText().strip() or DEFAULT_MODELS.get(provider, "")
        qd = self._date.date()
        target_date = date(qd.year(), qd.month(), qd.day())

        self._set_busy(True, f"Generating brief for {target_date.isoformat()}…")

        self._thread = QThread(self)
        self._worker = _BriefWorker(target_date, self._bundle, provider, api_key, model)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_brief_ready)
        self._worker.error.connect(self._on_brief_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self._thread is not None and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(5000)
        except RuntimeError:
            pass
        super().closeEvent(event)

    def _on_brief_ready(self, result: BriefResult) -> None:
        self._set_busy(False)
        if result.error:
            self._on_brief_error(result.error)
            return
        self._last_result = result
        # Re-gather KPIs for the renderer (cheap — doesn't re-query SQL except yesterday tables already cached at OS level)
        try:
            data = gather_brief_data(result.target_date, self._bundle)
            self._last_kpis = data.portfolio_kpis
        except Exception:
            self._last_kpis = {}
        html_text = render_to_html(result, kpis=self._last_kpis, mode="app")
        self._viewer.setHtml(html_text)
        for b in (self._btn_pdf, self._btn_html, self._btn_copy):
            b.setEnabled(True)
        self._status.setText(
            f"✓ Brief ready · {result.elapsed_sec:.1f}s · "
            f"~{result.tokens_in:,} in / ~{result.tokens_out:,} out · "
            f"≈ ${result.cost_usd:.4f}"
        )

    def _on_brief_error(self, msg: str) -> None:
        self._set_busy(False)
        self._status.setText(f"✗ Error: {msg}")
        self._status.setStyleSheet(f"color: {theme.get('danger')}; padding: 0 4px;")
        QMessageBox.critical(self, "Brief generation failed", msg)
        self._status.setStyleSheet(f"color: {theme.get('text_muted')}; padding: 0 4px;")

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._btn_generate.setEnabled(not busy)
        self._btn_generate.setText("⏳ Generating…" if busy else "✨ Generate Brief")
        if msg:
            self._status.setText(msg)
        elif not busy:
            self._status.setText("")

    # ------------------------------------------------------------------ Export
    def _on_export_pdf(self) -> None:
        if not self._last_result:
            return
        default = f"daily_brief_{self._last_result.target_date.isoformat()}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export brief as PDF", str(Path.home() / "Documents" / default),
            "PDF (*.pdf)",
        )
        if not path:
            return
        try:
            html_text = render_to_html(self._last_result, kpis=self._last_kpis, mode="pdf")
            doc = QTextDocument()
            doc.setHtml(html_text)
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            try:
                printer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
                printer.setPageOrientation(QPageLayout.Orientation.Portrait)
            except Exception:
                pass
            doc.print(printer)
            QMessageBox.information(self, "PDF saved", f"Saved to:\n{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "PDF export failed", str(e))

    def _on_export_html(self) -> None:
        if not self._last_result:
            return
        default = f"daily_brief_{self._last_result.target_date.isoformat()}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export brief as HTML", str(Path.home() / "Documents" / default),
            "HTML (*.html)",
        )
        if not path:
            return
        try:
            html_text = render_to_html(self._last_result, kpis=self._last_kpis, mode="email")
            Path(path).write_text(html_text, encoding="utf-8")
            QMessageBox.information(
                self, "HTML saved",
                f"Saved to:\n{path}\n\nThis file is styled for email — open it and copy/paste "
                "the rendered content into Outlook, or attach the file directly.",
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "HTML export failed", str(e))

    def _on_copy(self) -> None:
        if not self._last_result:
            return
        html_text = render_to_html(self._last_result, kpis=self._last_kpis, mode="email")
        QApplication.clipboard().setText(html_text)
        self._status.setText("✓ HTML copied to clipboard.")

    # ------------------------------------------------------------------ Placeholder
    def _show_placeholder(self) -> None:
        ph = """
        <html><body style="font-family: 'Segoe UI', Arial, sans-serif; padding: 60px;
                           color: #6b7280; text-align: center;">
        <div style="max-width: 540px; margin: 0 auto;">
            <div style="font-size: 64px; margin-bottom: 16px;">📋</div>
            <h2 style="color: #1f2937; font-weight: 700; margin: 0 0 12px 0;">Ready when you are</h2>
            <p style="font-size: 14px; line-height: 1.6; margin: 0 0 22px 0;">
                Pick a date (defaults to <strong>yesterday</strong>) and click
                <strong>✨ Generate Brief</strong>. The AI will produce a thorough yet
                at-a-glance executive briefing focused on the two business priorities:
                avoiding 12-month inventory and avoiding stockouts.
            </p>
            <p style="font-size: 12px; color: #9ca3af; margin: 0;">
                Configure your AI provider + API key in the Settings tab.
            </p>
        </div>
        </body></html>
        """
        self._viewer.setHtml(ph)
