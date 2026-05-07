"""
Main application window.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, QTimer, QDate
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QStatusBar, QDateEdit, QFrame,
    QMessageBox,
)

from app.services.metrics_service import DatasetBundle, compute_all
from app.ui.tab_overview import OverviewTab
from app.ui.tab_timeline import TimelineTab
from app.ui.tab_fillrate import FillRateTab
from app.ui.tab_problems import ProblemAreasTab
from app.ui.tab_settings import SettingsTab
from app.data.db import validate_connection
import app.ui.theme as theme


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _Worker(QObject):
    finished = pyqtSignal(object)  # DatasetBundle
    error = pyqtSignal(str)

    def __init__(self, filters: dict, start_date: date, end_date: date):
        super().__init__()
        self._filters = filters
        self._start = start_date
        self._end = end_date

    def run(self) -> None:
        try:
            bundle = compute_all(self._filters, self._start, self._end)
            self.finished.emit(bundle)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._bundle: Optional[DatasetBundle] = None
        self._filters: dict = {}
        self._thread: Optional[QThread] = None
        self._worker: Optional[_Worker] = None

        self.setWindowTitle("Inventory Control — Purchase Order Bot")
        self.setMinimumSize(1280, 780)
        self.resize(1440, 900)

        theme.apply_to_app(app)
        self._build_ui()
        self._check_connection()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top toolbar
        toolbar = self._build_toolbar()
        main_layout.addWidget(toolbar)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._overview_tab = OverviewTab()
        self._timeline_tab = TimelineTab()
        self._fillrate_tab = FillRateTab()
        self._problems_tab = ProblemAreasTab()
        self._settings_tab = SettingsTab()

        self._tabs.addTab(self._overview_tab, "📊  Overview")
        self._tabs.addTab(self._timeline_tab, "📈  Inventory Timeline")
        self._tabs.addTab(self._fillrate_tab, "✅  Fill Rate")
        self._tabs.addTab(self._problems_tab, "⚠  Problem Areas")
        self._tabs.addTab(self._settings_tab, "⚙  Settings")

        main_layout.addWidget(self._tabs)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._conn_label = QLabel("● Checking connection…")
        self._conn_label.setStyleSheet(f"color: {theme.get('text_muted')};")
        self._status.addPermanentWidget(self._conn_label)
        self._status_msg = QLabel("")
        self._status.addWidget(self._status_msg)

        # Wire signals
        self._overview_tab.sku_selected.connect(self._on_sku_selected)
        self._overview_tab.filters_changed.connect(self._on_filters_changed)
        self._problems_tab.sku_selected.connect(self._on_sku_selected)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("sidebar")  # reuse sidebar bg style
        bar.setFixedHeight(54)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        # App title
        title = QLabel("Inventory Control")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {theme.get('text')};")
        lay.addWidget(title)
        lay.addStretch()

        # Date controls
        lay.addWidget(QLabel("From:"))
        self._date_start = QDateEdit()
        self._date_start.setDisplayFormat("yyyy-MM-dd")
        self._date_start.setCalendarPopup(True)
        self._date_start.setMinimumWidth(110)
        default_start = date(2025, 8, 4)
        self._date_start.setDate(
            QDate(default_start.year, default_start.month, default_start.day)
        )
        lay.addWidget(self._date_start)

        lay.addWidget(QLabel("To:"))
        self._date_end = QDateEdit()
        self._date_end.setDisplayFormat("yyyy-MM-dd")
        self._date_end.setCalendarPopup(True)
        self._date_end.setMinimumWidth(110)
        today = date.today()
        self._date_end.setDate(
            QDate(today.year, today.month, today.day)
        )
        lay.addWidget(self._date_end)

        # Refresh button
        self._btn_refresh = QPushButton("Refresh Data")
        self._btn_refresh.clicked.connect(self._load_data)
        lay.addWidget(self._btn_refresh)

        # Theme toggle
        self._btn_theme = QPushButton("☀ Light" if theme.is_dark() else "🌙 Dark")
        self._btn_theme.setObjectName("flat")
        self._btn_theme.clicked.connect(self._toggle_theme)
        lay.addWidget(self._btn_theme)

        return bar

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _check_connection(self) -> None:
        self._conn_label.setText("● Checking connection…")
        self._conn_label.setStyleSheet(f"color: {theme.get('warning')};")
        self._set_status("Connecting to SQL Server…")

        def check():
            ok = validate_connection()
            if ok:
                self._conn_label.setText("● Connected")
                self._conn_label.setStyleSheet(f"color: {theme.get('success')};")
                self._load_data()
            else:
                self._conn_label.setText("● Connection Failed")
                self._conn_label.setStyleSheet(f"color: {theme.get('danger')};")
                self._status_msg.setText(
                    "Cannot reach SQL Server. Check your connection string in config_local.py or %APPDATA%\\PurchaseOrderBot\\config.json"
                )

        QTimer.singleShot(200, check)

    def _load_data(self) -> None:
        if self._thread and self._thread.isRunning():
            return

        self._btn_refresh.setEnabled(False)
        self._btn_refresh.setText("Loading…")
        self._set_status("Loading data…")

        qs = self._date_start.date()
        qe = self._date_end.date()
        start = date(qs.year(), qs.month(), qs.day())
        end = date(qe.year(), qe.month(), qe.day())

        self._thread = QThread(self)
        self._worker = _Worker(self._filters, start, end)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_data_ready)
        self._worker.error.connect(self._on_data_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_data_ready(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("Refresh Data")

        if bundle.error:
            self._set_status(f"Error: {bundle.error}", error=True)
            return

        self._overview_tab.refresh(bundle)
        self._timeline_tab.refresh(bundle)
        self._fillrate_tab.refresh(bundle)
        self._problems_tab.refresh(bundle)
        self._settings_tab.refresh(bundle.filter_values)

        sm = bundle.summary
        ri = bundle.refresh_info

        refreshed = ri.get("refreshed", [])
        cached    = ri.get("cached", [])
        ts_ok     = ri.get("ts_ok", False)

        if not ri:
            cache_note = ""
        elif not ts_ok:
            cache_note = "full reload (sysTableUpdates unavailable)"
        elif not refreshed:
            cache_note = "⚡ all tables current — served from cache"
        else:
            def _fmt(names):
                return ", ".join(n.replace("_", " ") for n in names)
            parts = []
            if refreshed:
                parts.append(f"↻ refreshed: {_fmt(refreshed)}")
            if cached:
                parts.append(f"⚡ cached: {_fmt(cached)}")
            cache_note = "  ▪  ".join(parts)

        self._set_status(
            f"{sm.get('total_skus', 0):,} SKUs  │  "
            f"Turn: {sm.get('stock_turn', 0):.2f}×  │  "
            f"Fill: {sm.get('fill_rate', 0)*100:.1f}%  │  "
            f"{sm.get('overstock_count', 0)} overstock  │  "
            f"{sm.get('runout_sku_count', 0)} runout risk"
            + (f"     —  {cache_note}" if cache_note else "")
        )

    def _on_data_error(self, msg: str) -> None:
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("Refresh Data")
        self._set_status(f"Error loading data: {msg}", error=True)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_sku_selected(self, sku: str) -> None:
        self._tabs.setCurrentIndex(1)  # Timeline tab
        self._timeline_tab.select_sku(sku)

    def _on_filters_changed(self, filters: dict) -> None:
        self._filters = filters
        self._overview_tab.apply_filters(filters)

    def _toggle_theme(self) -> None:
        is_d = theme.toggle(self._app)
        self._btn_theme.setText("☀ Light" if is_d else "🌙 Dark")

    def _set_status(self, msg: str, error: bool = False) -> None:
        self._status_msg.setText(msg)
        color = theme.get("danger") if error else theme.get("text_muted")
        self._status_msg.setStyleSheet(f"color: {color};")
