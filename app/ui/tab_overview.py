"""
Overview tab — portfolio KPI summary + per-SKU table.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget, QPushButton,
)

from app.services.metrics_service import DatasetBundle
from app.ui.widgets import (
    DataTable, FilterSidebar, KpiCard, SectionTitle, HSep, make_badge,
)
from app.ui.overview_dialogs import ColumnManagerDialog, ThresholdRulesDialog
import app.ui.theme as theme


def _safe_days(v) -> str:
    """Safely format a days-since value that may be None or float NaN."""
    if v is None:
        return "—"
    try:
        f = float(v)
        return "—" if math.isnan(f) else str(int(f))
    except (TypeError, ValueError):
        return "—"


class OverviewTab(QWidget):
    sku_selected = pyqtSignal(str)
    filters_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self._sidebar = FilterSidebar()
        self._sidebar.filters_changed.connect(self.filters_changed)
        root.addWidget(self._sidebar)

        # Main content
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(12)

        # KPI row
        self._kpi_row = QHBoxLayout()
        self._kpi_row.setSpacing(12)
        self._kpis: dict[str, KpiCard] = {}
        kpi_defs = [
            ("Stock Turn", "stock_turn", "accent"),
            ("Fill Rate", "fill_rate", "success"),
            ("Days of Inventory", "days_of_inventory", "info"),
            ("Runout Risk SKUs", "runout_sku_count", "danger"),
            ("Overstock SKUs", "overstock_count", "warning"),
            ("Total SKUs", "total_skus", "text"),
        ]
        for label, key, color in kpi_defs:
            card = KpiCard(label, "—", color)
            self._kpis[key] = card
            self._kpi_row.addWidget(card)

        cl.addLayout(self._kpi_row)
        cl.addWidget(HSep())

        # Table title + export
        row = QHBoxLayout()
        row.addWidget(SectionTitle("SKU Inventory Overview"))
        row.addStretch()
        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(f"color: {theme.get('text_muted')};")
        row.addWidget(self._lbl_count)
        cl.addLayout(row)

        # Table controls toolbar
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        btn_cols = QPushButton("⚙  Columns")
        btn_cols.setObjectName("flat")
        btn_cols.setToolTip("Show, hide, or reorder table columns")
        btn_cols.clicked.connect(self._open_column_manager)
        ctrl.addWidget(btn_cols)

        btn_rules = QPushButton("◈  Color Rules")
        btn_rules.setObjectName("flat")
        btn_rules.setToolTip("Define threshold-based row and cell highlight rules")
        btn_rules.clicked.connect(self._open_rules_dialog)
        ctrl.addWidget(btn_rules)

        ctrl.addStretch()
        cl.addLayout(ctrl)

        # Table
        self._table_cols = [
            "SKU", "Description", "Price Class", "Cost Center", "Rating",
            "Inventory (SY)", "On Order (SY)", "Pending PO", "Net Inv",
            "Avg Daily (SY)", "Orders", "Backorders", "BO Qty (SY)",
            "Days of Inv", "Inv Age (days)", "Fill Rate", "Runout Risk",
            "Days Since Sale", "Launch Date", "Turn", "Target Turn",
        ]
        self._table = DataTable(self._table_cols)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        cl.addWidget(self._table)

        root.addWidget(content)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle
        if bundle.filter_values is not None and not bundle.filter_values.empty:
            self._sidebar.populate(bundle.filter_values)
        self._refresh_kpis(bundle.summary)
        self._apply_saved_rules()          # load before populate so colors apply
        self._refresh_table(bundle.sku_metrics)
        self._apply_saved_column_prefs()   # restore hidden cols after populate

    def apply_filters(self, filters: dict) -> None:
        if self._bundle is None:
            return
        df = self._bundle.sku_metrics
        df = self._filter_metrics(df, filters)
        self._refresh_table(df)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _refresh_kpis(self, summary: dict) -> None:
        if not summary:
            return
        self._kpis["stock_turn"].set_value(f"{summary.get('stock_turn', 0):.2f}x")
        fr = summary.get("fill_rate", 0)
        self._kpis["fill_rate"].set_value(f"{fr * 100:.1f}%", "success" if fr >= 0.95 else "warning")
        self._kpis["days_of_inventory"].set_value(f"{summary.get('days_of_inventory', 0):.0f}d")
        self._kpis["runout_sku_count"].set_value(str(summary.get("runout_sku_count", 0)), "danger")
        self._kpis["overstock_count"].set_value(str(summary.get("overstock_count", 0)), "warning")
        self._kpis["total_skus"].set_value(str(summary.get("total_skus", 0)), "text")

    def _refresh_table(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            self._table.setRowCount(0)
            self._lbl_count.setText("No data")
            return

        rows = []
        _inf = float("inf")
        for _, row in df.iterrows():
            doi = row.get("days_of_inventory", _inf)
            doi_str = f"{doi:.0f}" if doi < _inf else "∞"
            fr = row.get("fill_rate", 1.0)
            turn = row.get("stock_turn", 0)
            launch = row.get("launch_date")
            rows.append([
                row.get("sku", ""),
                row.get("sku_description", ""),
                row.get("price_class_desc", row.get("price_class", "")),
                row.get("cost_center", ""),
                row.get("sku_rating", ""),
                f"{row.get('inventory_sy', 0):.1f}",
                f"{row.get('on_order_sy', 0):.1f}",
                f"{row.get('po_pending_qty', 0):.1f}",
                f"{row.get('net_inventory_sy', 0):.1f}",
                f"{row.get('avg_daily_sales_sy', 0):.2f}",
                str(int(row.get("orders_count", 0))),
                str(int(row.get("backorder_count", 0))),
                f"{row.get('strict_bo_qty_sy', 0):.1f}",
                doi_str,
                f"{row.get('inventory_age_days', 0):.0f}",
                f"{fr * 100:.1f}%",
                "Yes" if row.get("runout_risk") else "No",
                _safe_days(row.get("days_since_last_sale")),
                str(launch) if pd.notna(launch) else "—",
                f"{turn:.2f}x",
                f"{row.get('stockturn_target', 4.0):.1f}x",
            ])

        self._table.populate(rows)
        self._lbl_count.setText(f"{len(rows):,} SKUs")

    def _filter_metrics(self, df: pd.DataFrame, filters: dict) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        q = filters.get("sku_search", "").strip().upper()
        if q:
            sku_match = df["sku"].str.upper().str.contains(q, na=False)
            if "sku_description" in df.columns:
                sku_match = sku_match | df["sku_description"].str.upper().str.contains(q, na=False)
            df = df[sku_match]
        if filters.get("cost_centers"):
            df = df[df["cost_center"].isin(filters["cost_centers"])]
        if filters.get("suppliers"):
            df = df[df["supplier_number"].isin(filters["suppliers"])]
        if filters.get("price_classes"):
            df = df[df["price_class"].isin(filters["price_classes"])]
        if filters.get("product_lines"):
            df = df[df["product_line"].isin(filters["product_lines"])]
        if filters.get("sku_ratings"):
            df = df[df["sku_rating"].isin(filters["sku_ratings"])]
        return df

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item:
            self.sku_selected.emit(item.text())

    # ------------------------------------------------------------------
    # Persisted table settings
    # ------------------------------------------------------------------

    def _apply_saved_rules(self) -> None:
        from app.data.store import get_table_rules
        self._table.set_rules(get_table_rules("overview"))

    def _apply_saved_column_prefs(self) -> None:
        from app.data.store import get_column_prefs
        for col, visible in get_column_prefs("overview").items():
            self._table.set_column_visible(col, visible)

    def _open_column_manager(self) -> None:
        from app.data.store import set_column_prefs
        dlg = ColumnManagerDialog(self._table._column_names, self._table, self)
        if dlg.exec():
            prefs = dlg.get_prefs()
            set_column_prefs("overview", prefs)
            for col, visible in prefs.items():
                self._table.set_column_visible(col, visible)

    def _open_rules_dialog(self) -> None:
        from app.data.store import get_table_rules, set_table_rules
        dlg = ThresholdRulesDialog(
            self._table._column_names, get_table_rules("overview"), self
        )
        if dlg.exec():
            rules = dlg.get_rules()
            set_table_rules("overview", rules)
            self._table.set_rules(rules)
            # Re-populate so colors take effect immediately
            if self._bundle is not None:
                filters = self._sidebar.get_filters()
                df = self._filter_metrics(self._bundle.sku_metrics, filters)
                self._refresh_table(df)
