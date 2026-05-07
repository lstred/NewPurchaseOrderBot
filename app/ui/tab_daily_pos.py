"""
Daily POs tab — purchase orders entered on a selected date, grouped by operator.

Features
--------
* Date picker (defaults to today) with a Load button.
* Each operator gets a collapsible section header showing their name (if mapped)
  and an individual SKU-level DataTable.
* Same column-manager and colour-rule dialogs as the Overview drill-down.
* Double-click any SKU row → opens the TimelineDialog popup (using the main bundle).
* Operator initials → full name mappings are persisted in
  ``%APPDATA%\\PurchaseOrderBot\\operator_names.json``.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QDate, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDateEdit,
)

from app.services.metrics_service import DatasetBundle
from app.ui.widgets import DataTable, HSep, KpiCard, SectionTitle
from app.ui.overview_dialogs import ColumnManagerDialog, ThresholdRulesDialog
from app.ui.timeline_popup import TimelineDialog
import app.ui.theme as theme

_INF = float("inf")

# Column definitions for every operator's PO table.
# Columns from the SQL query are always available; inventory/metrics columns
# show "—" until the main bundle has been loaded.
_TABLE_COLS: list[str] = [
    "Order #",
    "SKU",
    "Description",
    "Price Class",
    "Cost Center",
    "Qty (SY)",
    "Qty (Raw)",
    "UOM",
    "ETA Date",
    "Supplier",
    "Unit Cost",
    "Ext. Price",
    "Lead Time",
    "Inventory (SY)",
    "On Order (SY)",
    "Avg Daily (SY)",
    "Days of Inv",
    "Rating",
    "Runout Risk",
]

_TABLE_ID = "daily_pos"


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _LoadWorker(QObject):
    finished = pyqtSignal(object)   # pd.DataFrame
    error    = pyqtSignal(str)

    def __init__(self, target_date: date) -> None:
        super().__init__()
        self._date = target_date

    def run(self) -> None:
        try:
            from app.data.loaders import load_daily_pos
            df = load_daily_pos(self._date)
            self.finished.emit(df)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _build_row(row: pd.Series, metrics_map: dict) -> list:
    """Convert one PO order-line row + optional sku_metrics entry into a display list."""
    base_sku = str(row.get("base_sku", row.get("sku", ""))).strip()
    m = metrics_map.get(base_sku)

    qty_sy    = float(row.get("quantity_sy", 0))
    inv_sy    = float(m.get("inventory_sy",    0)) if m else 0.0
    on_order  = float(m.get("on_order_sy",     0)) if m else 0.0
    avg_daily = float(m.get("avg_daily_sales_sy", 0)) if m else 0.0
    doi       = float(m.get("days_of_inventory",  _INF)) if m else _INF

    eta     = row.get("eta_date")
    eta_str = str(eta)[:10] if pd.notna(eta) else "No ETA"

    lt = int(row.get("lead_time_days", 30))

    runout = m.get("runout_risk", False) if m else False

    return [
        str(row.get("order_number", "")),
        base_sku,
        str(row.get("sku_description", "")).strip(),
        str(row.get("price_class_desc", "")).strip(),
        str(row.get("cost_center", "")).strip(),
        f"{qty_sy:,.1f}",
        f"{float(row.get('quantity_ordered', 0)):,.1f}",
        str(row.get("unit_of_measure", "")).strip(),
        eta_str,
        str(row.get("supplier_number", "")).strip(),
        f"{float(row.get('cost_per_um', 0)):,.4f}",
        f"{float(row.get('extended_price', 0)):,.2f}",
        f"{lt}d",
        f"{inv_sy:,.1f}"                                          if m else "—",
        f"{on_order:,.1f}"                                        if m else "—",
        f"{avg_daily:.3f}"                                        if m else "—",
        (f"{doi:.0f}d" if doi < _INF else "∞")                   if m else "—",
        str(m.get("sku_rating", "—"))                             if m else "—",
        ("⚠  Yes" if runout else "No")                            if m else "—",
    ]


# ---------------------------------------------------------------------------
# Operator section widget
# ---------------------------------------------------------------------------

class _OperatorSection(QFrame):
    """Collapsible section card for one operator."""

    row_double_clicked = pyqtSignal(str)   # emits base_sku

    def __init__(self, initials: str, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._initials  = initials
        self._expanded  = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ──────────────────────────────────────────────────
        self._header = QFrame()
        self._header.setObjectName("sidebar")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)

        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(14, 9, 14, 9)
        hl.setSpacing(8)

        initials_lbl = QLabel(initials if initials != "—" else "(Blank)")
        initials_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {theme.get('accent')};"
        )
        hl.addWidget(initials_lbl)

        self._name_lbl = QLabel(display_name)
        self._name_lbl.setStyleSheet(
            f"font-size: 13px; color: {theme.get('text')};"
        )
        self._name_lbl.setVisible(bool(display_name))
        hl.addWidget(self._name_lbl)

        self._dash_lbl = QLabel(" — ")
        self._dash_lbl.setStyleSheet(f"color: {theme.get('border')};")
        self._dash_lbl.setVisible(bool(display_name))
        hl.addWidget(self._dash_lbl)

        self._stats_lbl = QLabel()
        self._stats_lbl.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px;"
        )
        hl.addWidget(self._stats_lbl)

        hl.addStretch()

        self._toggle_btn = QPushButton("▲")
        self._toggle_btn.setObjectName("flat")
        self._toggle_btn.setFixedSize(30, 28)
        self._toggle_btn.setToolTip("Collapse / expand this section")
        self._toggle_btn.clicked.connect(self._toggle)
        hl.addWidget(self._toggle_btn)

        # Click anywhere on header to toggle
        self._header.mousePressEvent = lambda _e: self._toggle()

        root.addWidget(self._header)

        # ── DataTable ────────────────────────────────────────────────────
        self._table = DataTable(_TABLE_COLS, table_id=_TABLE_ID)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.setToolTip("Double-click any row to open its inventory timeline")
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        root.addWidget(self._table)

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def table(self) -> DataTable:
        return self._table

    def set_name(self, name: str) -> None:
        self._name_lbl.setText(name)
        self._name_lbl.setVisible(bool(name))
        self._dash_lbl.setVisible(bool(name))

    def populate(self, rows: list[list]) -> None:
        self._table.populate(rows)
        lines = len(rows)
        try:
            total_sy = sum(float(r[5].replace(",", "")) for r in rows)
        except (ValueError, IndexError):
            total_sy = 0.0
        self._stats_lbl.setText(
            f"{lines:,} line{'s' if lines != 1 else ''}  │  {total_sy:,.1f} SY"
        )

    def apply_rules(self, rules: list[dict]) -> None:
        self._table.set_rules(rules)

    def apply_column_prefs(self, prefs: dict[str, bool]) -> None:
        for col, visible in prefs.items():
            self._table.set_column_visible(col, visible)

    # ── Private ──────────────────────────────────────────────────────────

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._table.setVisible(self._expanded)
        self._toggle_btn.setText("▲" if self._expanded else "▼")

    def _on_double_click(self, row: int, _col: int) -> None:
        # Column 1 = SKU
        item = self._table.item(row, 1)
        if item:
            self.row_double_clicked.emit(item.text().strip())


# ---------------------------------------------------------------------------
# Operator names management dialog
# ---------------------------------------------------------------------------

class OperatorNamesDialog(QDialog):
    """Map operator initials to full names.  Stored in AppData JSON."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Operator Names")
        self.setMinimumSize(500, 400)
        self.resize(560, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        hint = QLabel(
            "Map operator initials to full names.  Names are displayed in section "
            "headers and remembered across sessions."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        root.addWidget(hint)

        # Table
        self._tbl = QTableWidget(0, 2)
        self._tbl.setHorizontalHeaderLabels(["Initials", "Full Name"])
        self._tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl.setColumnWidth(0, 110)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self._tbl)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_add = QPushButton("＋  Add Row")
        btn_add.setObjectName("flat")
        btn_add.clicked.connect(lambda: self._add_row("", ""))

        btn_remove = QPushButton("Remove Selected")
        btn_remove.setObjectName("flat")
        btn_remove.clicked.connect(self._remove_selected)

        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # OK / Cancel
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._save_and_accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._load_existing()

    def _load_existing(self) -> None:
        from app.data.store import get_operator_names
        for initials, name in sorted(get_operator_names().items()):
            self._add_row(initials, name)

    def _add_row(self, initials: str, name: str) -> None:
        r = self._tbl.rowCount()
        self._tbl.insertRow(r)
        self._tbl.setItem(r, 0, QTableWidgetItem(initials))
        self._tbl.setItem(r, 1, QTableWidgetItem(name))
        self._tbl.scrollToBottom()
        if not initials:
            self._tbl.editItem(self._tbl.item(r, 0))

    def _remove_selected(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._tbl.selectedIndexes()}, reverse=True
        )
        for r in rows:
            self._tbl.removeRow(r)

    def _save_and_accept(self) -> None:
        from app.data.store import save_all_operator_names
        names: dict[str, str] = {}
        for r in range(self._tbl.rowCount()):
            i_item = self._tbl.item(r, 0)
            n_item = self._tbl.item(r, 1)
            initials = (i_item.text() if i_item else "").strip().upper()
            name     = (n_item.text() if n_item else "").strip()
            if initials:
                names[initials] = name
        save_all_operator_names(names)
        self.accept()


# ---------------------------------------------------------------------------
# Daily POs tab
# ---------------------------------------------------------------------------

class DailyPOsTab(QWidget):
    """Purchase orders entered on a selected date, grouped by operator."""

    sku_selected = pyqtSignal(str)   # → navigate to Timeline tab

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._po_df:  Optional[pd.DataFrame]  = None
        self._sections: list[_OperatorSection] = []
        self._thread: Optional[QThread]  = None
        self._worker: Optional[_LoadWorker] = None
        self._build_ui()

    # ── Public API ──────────────────────────────────────────────────────

    def refresh(self, bundle: DatasetBundle) -> None:
        """Called whenever the main data bundle is reloaded."""
        self._bundle = bundle
        # If PO data is already loaded, re-render to merge updated metrics.
        if self._po_df is not None and not self._po_df.empty:
            self._render()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ─────────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setObjectName("sidebar")
        toolbar.setFixedHeight(54)

        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(16, 0, 16, 0)
        tl.setSpacing(10)

        lbl_date = QLabel("Date:")
        lbl_date.setStyleSheet(f"color: {theme.get('text')}; font-weight: 600;")
        tl.addWidget(lbl_date)

        self._date_edit = QDateEdit()
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setMinimumWidth(120)
        today = date.today()
        self._date_edit.setDate(QDate(today.year, today.month, today.day))
        tl.addWidget(self._date_edit)

        self._btn_load = QPushButton("Load POs")
        self._btn_load.setMinimumWidth(90)
        self._btn_load.clicked.connect(self._load_pos)
        tl.addWidget(self._btn_load)

        # vertical divider
        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.Shape.VLine)
        vdiv.setStyleSheet(f"color: {theme.get('border')};")
        vdiv.setFixedHeight(24)
        tl.addWidget(vdiv)

        self._status_lbl = QLabel("Select a date and click  Load POs")
        self._status_lbl.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px;"
        )
        tl.addWidget(self._status_lbl)

        tl.addStretch()

        btn_names = QPushButton("👤  Operator Names")
        btn_names.setObjectName("flat")
        btn_names.setToolTip("Map operator initials to full names")
        btn_names.clicked.connect(self._open_names_dialog)
        tl.addWidget(btn_names)

        vdiv2 = QFrame()
        vdiv2.setFrameShape(QFrame.Shape.VLine)
        vdiv2.setStyleSheet(f"color: {theme.get('border')};")
        vdiv2.setFixedHeight(24)
        tl.addWidget(vdiv2)

        self._btn_cols = QPushButton("⚙  Columns")
        self._btn_cols.setObjectName("flat")
        self._btn_cols.setToolTip("Show, hide, or reorder columns")
        self._btn_cols.clicked.connect(self._open_column_manager)
        tl.addWidget(self._btn_cols)

        self._btn_rules = QPushButton("◈  Color Rules")
        self._btn_rules.setObjectName("flat")
        self._btn_rules.setToolTip("Define threshold-based row and cell highlight rules")
        self._btn_rules.clicked.connect(self._open_rules_dialog)
        tl.addWidget(self._btn_rules)

        root.addWidget(toolbar)

        # ── Scrollable content area ──────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(12)

        # Placeholder shown before first load
        self._placeholder = QLabel(
            "Select a date and click  Load POs  to view purchase orders for that day."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 14px; padding: 60px;"
        )
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch()

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

    # ── Data loading ─────────────────────────────────────────────────────

    def _load_pos(self) -> None:
        if self._thread and self._thread.isRunning():
            return

        qd = self._date_edit.date()
        target = date(qd.year(), qd.month(), qd.day())

        self._btn_load.setEnabled(False)
        self._btn_load.setText("Loading…")
        self._status_lbl.setText(f"Loading POs for {target}…")
        self._status_lbl.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px;"
        )

        self._thread = QThread(self)
        self._worker = _LoadWorker(target)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_load_done)
        self._worker.error.connect(self._on_load_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_load_done(self, df: pd.DataFrame) -> None:
        self._po_df = df
        self._btn_load.setEnabled(True)
        self._btn_load.setText("Load POs")
        self._render()

    def _on_load_error(self, msg: str) -> None:
        self._btn_load.setEnabled(True)
        self._btn_load.setText("Load POs")
        self._status_lbl.setText(f"Error loading POs: {msg}")
        self._status_lbl.setStyleSheet(
            f"color: {theme.get('danger')}; font-size: 12px;"
        )

    # ── Rendering ────────────────────────────────────────────────────────

    def _clear_sections(self) -> None:
        self._sections.clear()
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _render(self) -> None:
        df = self._po_df
        if df is None:
            return

        self._clear_sections()

        if df.empty:
            qd = self._date_edit.date()
            target = date(qd.year(), qd.month(), qd.day())
            lbl = QLabel(f"No purchase orders found for {target}.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {theme.get('text_muted')}; font-size: 14px; padding: 60px;"
            )
            self._content_layout.addWidget(lbl)
            self._content_layout.addStretch()
            self._status_lbl.setText(f"No POs found for selected date.")
            return

        # Metrics map from the main bundle (may be None on first load)
        metrics_map: dict = {}
        if self._bundle is not None and not self._bundle.sku_metrics.empty:
            metrics_map = self._bundle.sku_metrics.set_index("sku").to_dict("index")

        # Operator name mapping
        from app.data.store import get_operator_names, get_table_rules, get_column_prefs
        op_names = get_operator_names()
        rules    = get_table_rules(_TABLE_ID)
        prefs    = get_column_prefs(_TABLE_ID)

        # Normalise operator initials
        df = df.copy()
        df["operator_initials"] = (
            df["operator_initials"].fillna("").str.strip().replace("", "—")
        )

        operators  = sorted(df["operator_initials"].unique())
        total_lines = 0
        total_sy    = 0.0

        for initials in operators:
            op_df = df[df["operator_initials"] == initials]
            name  = op_names.get(initials, "") if initials != "—" else ""

            section = _OperatorSection(initials, name, self._content)
            section.apply_rules(rules)
            for col, vis in prefs.items():
                section.apply_column_prefs({col: vis})
            section.table.restore_column_widths()
            section.row_double_clicked.connect(self._on_sku_double_click)

            rows = [_build_row(r, metrics_map) for _, r in op_df.iterrows()]
            section.populate(rows)

            self._sections.append(section)
            self._content_layout.addWidget(section)

            total_lines += len(rows)
            total_sy    += float(op_df["quantity_sy"].sum())

        self._content_layout.addStretch()

        qd = self._date_edit.date()
        target   = date(qd.year(), qd.month(), qd.day())
        op_count = len(operators)
        self._status_lbl.setText(
            f"{target}  │  {total_lines:,} line{'s' if total_lines != 1 else ''}  │  "
            f"{op_count} operator{'s' if op_count != 1 else ''}  │  {total_sy:,.1f} SY total"
        )
        self._status_lbl.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px;"
        )

    # ── Signal handlers ──────────────────────────────────────────────────

    def _on_sku_double_click(self, sku: str) -> None:
        if not sku or self._bundle is None:
            return
        dlg = TimelineDialog(sku, self._bundle, self)
        dlg.open_in_tab.connect(self.sku_selected)
        dlg.show()

    # ── Dialogs ──────────────────────────────────────────────────────────

    def _open_names_dialog(self) -> None:
        dlg = OperatorNamesDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Re-render to update section headers with new names
            if self._po_df is not None and not self._po_df.empty:
                self._render()

    def _open_column_manager(self) -> None:
        # Use the first section's table as the reference; if none loaded yet,
        # create a temporary table just for the dialog.
        from app.data.store import set_column_prefs

        if self._sections:
            ref_table = self._sections[0].table
        else:
            # No data loaded — open dialog with a dummy table so the user can
            # still configure columns for when data arrives.
            ref_table = DataTable(_TABLE_COLS, table_id=_TABLE_ID)
            saved = get_column_prefs_safe()
            for col, vis in saved.items():
                ref_table.set_column_visible(col, vis)

        dlg = ColumnManagerDialog(_TABLE_COLS, ref_table, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            prefs = dlg.get_prefs()
            set_column_prefs(_TABLE_ID, prefs)
            # Apply to all current sections
            for sec in self._sections:
                for col, visible in prefs.items():
                    sec.table.set_column_visible(col, visible)

    def _open_rules_dialog(self) -> None:
        from app.data.store import get_table_rules, set_table_rules
        rules = get_table_rules(_TABLE_ID)
        dlg = ThresholdRulesDialog(_TABLE_COLS, rules, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_rules = dlg.get_rules()
            set_table_rules(_TABLE_ID, new_rules)
            for sec in self._sections:
                sec.apply_rules(new_rules)
            # Re-render to repaint with new colours
            if self._po_df is not None and not self._po_df.empty:
                self._render()


# ---------------------------------------------------------------------------
# Helper — safe prefs read without import circularity
# ---------------------------------------------------------------------------

def get_column_prefs_safe() -> dict[str, bool]:
    from app.data.store import get_column_prefs
    return get_column_prefs(_TABLE_ID)
