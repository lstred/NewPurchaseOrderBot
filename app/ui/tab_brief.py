"""
Daily Brief tab (v4.1) — replaces the v3.x AI Q&A chat.

Layout:
  ┌──────────────────────────────────────────────────────────────────┐
  │  📋 Daily Brief    Date  Provider  Model        ✨ Generate     │  ← gradient bar
  ├──────────────────────────────────────────────────────────────────┤
  │  status line                          📄 PDF · 📧 HTML · 📋 Copy │  ← actions row
  ├──────────────────────────────────────────────────────────────────┤
  │                                                                  │
  │              full-width premium HTML brief viewer                │
  │                                                                  │
  └──────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QTextDocument, QPageLayout, QPageSize
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from app.ai.brief import BriefResult, gather_brief_data, generate_brief
from app.ai.brief_renderer import render_to_html
from app.ai.providers import DEFAULT_MODELS
from app.data import store
from app.services.metrics_service import DatasetBundle
import app.ui.theme as theme


_PROVIDER_MODELS = {
    "anthropic": ["claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5"],
    "openai":    ["gpt-5", "gpt-5-mini", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o1-mini"],
    "google":    ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
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
        self._show_placeholder()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # ---- Top control bar (gradient) ----------------------------------
        bar = QFrame()
        bar.setObjectName("briefBar")
        bar.setStyleSheet("""
            QFrame#briefBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:0.5 #8b5cf6, stop:1 #ec4899);
                border-radius: 10px;
            }
            QFrame#briefBar QLabel { color: white; font-weight: 600; }
            QFrame#briefBar QComboBox, QFrame#briefBar QDateEdit {
                background: rgba(255,255,255,0.18);
                color: white;
                border: 1px solid rgba(255,255,255,0.35);
                border-radius: 5px;
                padding: 4px 8px;
                min-height: 26px;
            }
            QFrame#briefBar QComboBox:hover, QFrame#briefBar QDateEdit:hover {
                background: rgba(255,255,255,0.28);
            }
            QFrame#briefBar QComboBox QAbstractItemView {
                background: #1f2937; color: white;
                selection-background-color: #6366f1;
            }
        """)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.setSpacing(10)

        title = QLabel("📋  Daily Brief")
        title.setStyleSheet("color: white; font-size: 18px; font-weight: 800;")
        bl.addWidget(title)
        bl.addStretch()

        bl.addWidget(QLabel("Date:"))
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setMinimumWidth(130)
        y = date.today() - timedelta(days=1)
        self._date.setDate(QDate(y.year, y.month, y.day))
        bl.addWidget(self._date)

        bl.addSpacing(6)
        bl.addWidget(QLabel("Provider:"))
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["openai", "anthropic", "google"])
        self._provider_combo.setMinimumWidth(110)
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        bl.addWidget(self._provider_combo)

        bl.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setMinimumWidth(180)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        bl.addWidget(self._model_combo)

        self._btn_generate = QPushButton("✨ Generate Brief")
        self._btn_generate.setMinimumHeight(34)
        self._btn_generate.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_generate.setStyleSheet("""
            QPushButton {
                background: white; color: #6366f1;
                border: none; border-radius: 6px;
                padding: 6px 18px; font-weight: 700;
            }
            QPushButton:hover { background: #f3f4f6; }
            QPushButton:disabled { background: #d1d5db; color: #9ca3af; }
        """)
        self._btn_generate.clicked.connect(self._on_generate)
        bl.addWidget(self._btn_generate)

        outer.addWidget(bar)

        # ---- Status / actions row -----------------------------------------
        actions = QHBoxLayout()
        actions.setContentsMargins(2, 0, 2, 0)
        actions.setSpacing(8)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {theme.get('text_muted')};")
        actions.addWidget(self._status)
        actions.addStretch()

        self._btn_pdf  = self._make_action_btn("📄  Export PDF")
        self._btn_html = self._make_action_btn("📧  Export HTML")
        self._btn_copy = self._make_action_btn("📋  Copy HTML")
        actions.addWidget(self._btn_pdf)
        actions.addWidget(self._btn_html)
        actions.addWidget(self._btn_copy)
        self._btn_pdf.clicked.connect(self._on_export_pdf)
        self._btn_html.clicked.connect(self._on_export_html)
        self._btn_copy.clicked.connect(self._on_copy)
        for b in (self._btn_pdf, self._btn_html, self._btn_copy):
            b.setEnabled(False)

        outer.addLayout(actions)

        # ---- Viewer (full width) ------------------------------------------
        self._viewer = QTextBrowser()
        self._viewer.setOpenExternalLinks(True)
        self._viewer.setStyleSheet(
            "QTextBrowser { background: #ffffff; border: 1px solid #e5e7eb;"
            " border-radius: 8px; padding: 4px; }"
        )
        outer.addWidget(self._viewer, 1)

    def _make_action_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setMinimumHeight(30)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {theme.get('bg_card')};
                color: {theme.get('text')};
                border: 1px solid {theme.get('border')};
                border-radius: 6px;
                padding: 4px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {theme.get('accent')};
                color: {theme.get('accent')};
            }}
            QPushButton:disabled {{
                color: {theme.get('text_muted')};
                border-color: {theme.get('border')};
                background: transparent;
            }}
        """)
        return b

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
            default = DEFAULT_MODELS.get(provider, models[0])
            self._model_combo.setCurrentText(default if default in models else models[0])
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
        self._status.setStyleSheet(f"color: {theme.get('success')};")

    def _on_brief_error(self, msg: str) -> None:
        self._set_busy(False)
        self._status.setText(f"✗ Error: {msg}")
        self._status.setStyleSheet(f"color: {theme.get('danger')};")
        QMessageBox.critical(self, "Brief generation failed", msg)

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._btn_generate.setEnabled(not busy)
        self._btn_generate.setText("⏳ Generating…" if busy else "✨ Generate Brief")
        if msg:
            self._status.setStyleSheet(f"color: {theme.get('text_muted')};")
            self._status.setText(msg)
        elif not busy:
            pass  # keep last status visible

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
                f"Saved to:\n{path}\n\nOpen the file and copy/paste the rendered "
                "content into Outlook, or attach the file directly.",
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "HTML export failed", str(e))

    def _on_copy(self) -> None:
        if not self._last_result:
            return
        html_text = render_to_html(self._last_result, kpis=self._last_kpis, mode="email")
        QApplication.clipboard().setText(html_text)
        self._status.setText("✓ HTML copied to clipboard.")
        self._status.setStyleSheet(f"color: {theme.get('success')};")

    # ------------------------------------------------------------------ Placeholder
    def _show_placeholder(self) -> None:
        ph = """
        <html><body style="font-family: 'Segoe UI', Arial, sans-serif; padding: 80px 40px;
                           color: #6b7280; text-align: center; background: #ffffff;">
        <div style="max-width: 620px; margin: 0 auto;">
            <div style="font-size: 72px; margin-bottom: 20px;">📋</div>
            <h1 style="color: #111827; font-weight: 800; font-size: 28px; margin: 0 0 14px 0;
                       background: linear-gradient(90deg, #6366f1, #8b5cf6, #ec4899);
                       -webkit-background-clip: text; color: transparent;">
                Ready when you are
            </h1>
            <p style="font-size: 15px; line-height: 1.7; margin: 0 0 28px 0; color: #4b5563;">
                Pick a date (defaults to <strong>yesterday</strong>) and click
                <strong>✨ Generate Brief</strong>. Your AI will produce a thorough yet
                at-a-glance executive briefing focused on the two priorities:
                avoiding 12-month inventory and avoiding stockouts.
            </p>
            <div style="display: inline-block; padding: 12px 22px; border-radius: 8px;
                        background: #f9fafb; border: 1px solid #e5e7eb; font-size: 12px; color: #6b7280;">
                Configure your AI provider + API key in the Settings tab.
            </div>
        </div>
        </body></html>
        """
        self._viewer.setHtml(ph)
