"""
Overview tab — portfolio KPI summary + price-class/SKU table.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QSizePolicy,
    QVBoxLayout, QWidget, QPushButton, QFrame, QStackedWidget,
)

from app.services.metrics_service import DatasetBundle
from app.ui.widgets import (
    DataTable, FilterSidebar, KpiCard, SectionTitle, HSep, make_badge,
)
from app.ui.overview_dialogs import ColumnManagerDialog, ThresholdRulesDialog
from app.ui.timeline_popup import TimelineDialog
import app.ui.theme as theme


_INF = float("inf")


def _safe_days(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        return "—" if math.isnan(f) else str(int(f))
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# Price-class drill-down dialog
# ---------------------------------------------------------------------------

class PriceClassDetailDialog(QDialog):
    """Modal showing all SKUs in one price class with totals."""

    def __init__(self, pc_code: str, pc_desc: str, df: pd.DataFrame,
                 bundle: DatasetBundle, parent=None):
        super().__init__(parent)
        self._bundle = bundle
        self._df = df
        self.setWindowTitle(f"Price Class: {pc_code}")
        self.setMinimumSize(1200, 680)
        self.resize(1360, 740)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        ttl = QLabel(pc_code)
        ttl.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {theme.get('text')};"
        )
        hdr.addWidget(ttl)
        if pc_desc:
            sub = QLabel(f"  {pc_desc}")
            sub.setStyleSheet(
                f"font-size: 14px; color: {theme.get('text_muted')};"
            )
            hdr.addWidget(sub)
        hdr.addStretch()
        root.addLayout(hdr)
        root.addWidget(HSep())

        # Toolbar: Columns + Color Rules
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        btn_detail_cols = QPushButton("⚙  Columns")
        btn_detail_cols.setObjectName("flat")
        btn_detail_cols.setToolTip("Show, hide, or reorder columns in this dialog")
        btn_detail_rules = QPushButton("◈  Color Rules")
        btn_detail_rules.setObjectName("flat")
        btn_detail_rules.setToolTip("Define threshold-based row/cell highlight rules")
        toolbar.addWidget(btn_detail_cols)
        toolbar.addWidget(btn_detail_rules)
        toolbar.addStretch()
        root.addLayout(toolbar)

        # KPI summary cards
        inv_sy    = df["inventory_sy"].sum()
        on_order  = df["on_order_sy"].sum()
        avg_daily = df["avg_daily_sales_sy"].sum()
        doi       = inv_sy / avg_daily if avg_daily > 0 else _INF
        oc        = df["orders_count"].sum()
        w_fr      = ((df["fill_rate"] * df["orders_count"]).sum() / oc) if oc > 0 else 1.0
        runout    = int(df["runout_risk"].sum()) if "runout_risk" in df.columns else 0

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        for label, val, color in [
            ("SKUs in Class",     str(len(df)),     "text"),
            ("Inventory (SY)",    f"{inv_sy:,.0f}", "info"),
            ("On Order (SY)",     f"{on_order:,.0f}", "accent"),
            ("Avg Daily (SY)",    f"{avg_daily:.2f}", "text"),
            ("Days of Inventory",
             f"{doi:.0f}d" if doi < _INF else "∞",
             "success" if doi > 60 else "warning" if doi > 20 else "danger"),
            ("Fill Rate",
             f"{w_fr*100:.1f}%",
             "success" if w_fr >= 0.95 else "warning"),
            ("Runout Risk SKUs",  str(runout),
             "danger" if runout > 0 else "text"),
        ]:
            card = KpiCard(label, val, color)
            card.setMinimumWidth(110)
            kpi_row.addWidget(card)
        root.addLayout(kpi_row)

        # SKU count label
        count_lbl = QLabel(
            f"{len(df):,} SKUs  —  double-click any row to view its timeline"
        )
        count_lbl.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px; margin-bottom: 4px;"
        )
        root.addWidget(count_lbl)

        # SKU table
        table_cols = [
            "SKU", "Description", "Rating",
            "Inventory (SY)", "On Order (SY)", "Net Inv",
            "Avg Daily (SY)", "Days of Inv", "Inv Age (days)", "Fill Rate",
            "Runout Risk", "Days Since Sale", "Launch Date", "Lead Time (days)",
            "Turn", "Target Turn",
        ]
        self._table = DataTable(table_cols, table_id="overview_detail")
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.setToolTip("Double-click to view inventory timeline")
        from app.data.store import get_table_rules, get_column_prefs
        self._table.set_rules(get_table_rules("overview"))
        for col, visible in get_column_prefs("overview_detail").items():
            self._table.set_column_visible(col, visible)
        self._table.restore_column_widths()

        # Wire toolbar buttons (defined above, before the table)
        btn_detail_cols.clicked.connect(self._open_column_manager)
        btn_detail_rules.clicked.connect(self._open_rules_dialog)

        rows, totals = self._build_rows(df)
        self._table.populate(rows)
        root.addWidget(self._table)

        # Totals strip
        tot_frame = QFrame()
        tot_frame.setObjectName("card")
        tot_lay = QHBoxLayout(tot_frame)
        tot_lay.setContentsMargins(12, 6, 12, 6)
        tot_lay.setSpacing(20)
        for k, v in totals.items():
            lbl = QLabel(f"<b>{k}:</b> {v}")
            lbl.setStyleSheet(f"color: {theme.get('text')}; font-size: 12px;")
            tot_lay.addWidget(lbl)
        tot_lay.addStretch()
        root.addWidget(tot_frame)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_btn.setMinimumWidth(100)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _build_rows(self, df: pd.DataFrame):
        rows = []
        for _, row in df.iterrows():
            doi = row.get("days_of_inventory", _INF)
            doi_str = f"{doi:.0f}" if doi < _INF else "∞"
            fr = row.get("fill_rate", 1.0)
            rows.append([
                row.get("sku", ""),
                row.get("sku_description", ""),
                row.get("sku_rating", ""),
                f"{row.get('inventory_sy', 0):,.1f}",
                f"{row.get('on_order_sy', 0):,.1f}",
                f"{row.get('net_inventory_sy', 0):,.1f}",
                f"{row.get('avg_daily_sales_sy', 0):.2f}",
                doi_str,
                f"{row.get('inventory_age_days', 0):.0f}",
                f"{fr * 100:.1f}%",
                "Yes" if row.get("runout_risk") else "No",
                _safe_days(row.get("days_since_last_sale")),
                str(row.get("launch_date")) if pd.notna(row.get("launch_date")) else "—",
                str(int(row.get("lead_time_days", 30))),
                f"{row.get('stock_turn', 0):.2f}x",
                f"{row.get('stockturn_target', 4.0):.1f}x",
            ])

        inv_tot  = df["inventory_sy"].sum()
        ord_tot  = df["on_order_sy"].sum()
        avg_tot  = df["avg_daily_sales_sy"].sum()
        doi_tot  = inv_tot / avg_tot if avg_tot > 0 else _INF
        oc       = df["orders_count"].sum()
        fr_tot   = ((df["fill_rate"] * df["orders_count"]).sum() / oc) if oc > 0 else 1.0
        totals = {
            "SKUs":       f"{len(df):,}",
            "Inventory":  f"{inv_tot:,.1f} SY",
            "On Order":   f"{ord_tot:,.1f} SY",
            "Avg Daily":  f"{avg_tot:.2f} SY/day",
            "Days of Inv": f"{doi_tot:.0f}d" if doi_tot < _INF else "∞",
            "Fill Rate":  f"{fr_tot*100:.1f}%",
        }
        return rows, totals

    def _open_column_manager(self) -> None:
        from app.data.store import get_column_prefs, set_column_prefs
        dlg = ColumnManagerDialog(self._table._column_names, self._table, self)
        if dlg.exec():
            prefs = dlg.get_prefs()
            set_column_prefs("overview_detail", prefs)
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
            rows, _ = self._build_rows(self._df)
            self._table.populate(rows)

    def _on_double_click(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item and self._bundle is not None:
            dlg = TimelineDialog(item.text(), self._bundle, self)
            dlg.show()


# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------

class OverviewTab(QWidget):
    sku_selected = pyqtSignal(str)
    filters_changed = pyqtSignal(dict)

    _PC_TABLE_COLS = [
        "Price Class", "Description", "SKUs",
        "Inventory (SY)", "On Order (SY)", "Net Inv",
        "Avg Daily (SY)", "Days of Inv", "Fill Rate",
        "Runout Risk", "Overstock", "Stock Turn", "Ratings A/B/C/D",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._view_mode: str = "price_class"
        self._current_df: Optional[pd.DataFrame] = None
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

        # Thin vertical border
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {theme.get('border')};")
        sep.setFixedWidth(1)
        root.addWidget(sep)

        # Main content
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(10)

        # KPI row
        self._kpi_row = QHBoxLayout()
        self._kpi_row.setSpacing(12)
        self._kpis: dict[str, KpiCard] = {}
        kpi_defs = [
            ("Stock Turn",        "stock_turn",       "accent"),
            ("Fill Rate",         "fill_rate",        "success"),
            ("Days of Inventory", "days_of_inventory","info"),
            ("Runout Risk SKUs",  "runout_sku_count",  "danger"),
            ("Overstock SKUs",    "overstock_count",   "warning"),
            ("Total SKUs",        "total_skus",        "text"),
        ]
        for label, key, color in kpi_defs:
            card = KpiCard(label, "—", color)
            self._kpis[key] = card
            self._kpi_row.addWidget(card)
        cl.addLayout(self._kpi_row)
        cl.addWidget(HSep())

        # Title + count row
        title_row = QHBoxLayout()
        self._table_title = SectionTitle("Price Class Overview")
        title_row.addWidget(self._table_title)
        title_row.addStretch()
        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px;"
        )
        title_row.addWidget(self._lbl_count)
        cl.addLayout(title_row)

        # Toolbar
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)

        self._btn_pc_view  = QPushButton("By Price Class")
        self._btn_sku_view = QPushButton("By SKU")
        for btn, mode in [
            (self._btn_pc_view, "price_class"),
            (self._btn_sku_view, "sku"),
        ]:
            btn.setObjectName("flat")
            btn.setCheckable(True)
            btn.setMinimumWidth(110)
            _m = mode
            btn.clicked.connect(lambda checked=False, m=_m: self._set_view(m))
            ctrl.addWidget(btn)
        self._btn_pc_view.setChecked(True)

        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.Shape.VLine)
        vdiv.setStyleSheet(f"color: {theme.get('border')};")
        vdiv.setFixedWidth(1)
        vdiv.setFixedHeight(22)
        ctrl.addWidget(vdiv)

        btn_cols = QPushButton("⚙  Columns")
        btn_cols.setObjectName("flat")
        btn_cols.setToolTip("Show, hide, or reorder SKU table columns")
        btn_cols.clicked.connect(self._open_column_manager)
        ctrl.addWidget(btn_cols)

        btn_rules = QPushButton("◈  Color Rules")
        btn_rules.setObjectName("flat")
        btn_rules.setToolTip("Define threshold-based row and cell highlight rules")
        btn_rules.clicked.connect(self._open_rules_dialog)
        ctrl.addWidget(btn_rules)

        ctrl.addStretch()
        cl.addLayout(ctrl)

        # Stacked tables
        self._stack = QStackedWidget()

        self._pc_table = DataTable(self._PC_TABLE_COLS, table_id="overview_pc")
        self._pc_table.cellDoubleClicked.connect(self._on_pc_double_clicked)
        self._pc_table.setToolTip(
            "Double-click a price class to drill down into its individual SKUs"
        )
        self._stack.addWidget(self._pc_table)

        self._table_cols = [
            "SKU", "Description", "Price Class", "Cost Center", "Rating",
            "Inventory (SY)", "On Order (SY)", "Net Inv",
            "Avg Daily (SY)", "Orders", "Backorders", "BO Qty (SY)",
            "Days of Inv", "Inv Age (days)", "Fill Rate", "Runout Risk",
            "Days Since Sale", "Launch Date", "Lead Time (days)", "Turn", "Target Turn",
        ]
        self._table = DataTable(self._table_cols, table_id="overview")
        self._table.cellDoubleClicked.connect(self._on_sku_double_clicked)
        self._table.setToolTip("Double-click any row to view its inventory timeline")
        self._stack.addWidget(self._table)

        cl.addWidget(self._stack)
        root.addWidget(content)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle
        if bundle.filter_values is not None and not bundle.filter_values.empty:
            self._sidebar.populate(bundle.filter_values)
        self._refresh_kpis(bundle.summary)
        self._apply_saved_rules()
        self._current_df = bundle.sku_metrics
        self._refresh_current_view()
        self._apply_saved_column_prefs()

    def apply_filters(self, filters: dict) -> None:
        if self._bundle is None:
            return
        self._current_df = self._filter_metrics(self._bundle.sku_metrics, filters)
        self._refresh_current_view()

    # ------------------------------------------------------------------
    # View toggling
    # ------------------------------------------------------------------

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        is_pc = (mode == "price_class")
        self._btn_pc_view.setChecked(is_pc)
        self._btn_sku_view.setChecked(not is_pc)
        self._table_title.setText(
            "Price Class Overview" if is_pc else "SKU Inventory Overview"
        )
        self._stack.setCurrentIndex(0 if is_pc else 1)
        self._refresh_current_view()

    def _refresh_current_view(self) -> None:
        df = self._current_df
        if df is None or df.empty:
            self._pc_table.setRowCount(0)
            self._table.setRowCount(0)
            self._lbl_count.setText("No data")
            return
        if self._view_mode == "price_class":
            self._refresh_pc_table(df)
        else:
            self._refresh_sku_table(df)

    # ------------------------------------------------------------------
    # Price-class table
    # ------------------------------------------------------------------

    def _refresh_pc_table(self, df: pd.DataFrame) -> None:
        rows = self._build_pc_rows(df)
        self._pc_table.populate(rows)
        self._lbl_count.setText(
            f"{len(rows):,} price classes  ·  {len(df):,} SKUs"
            "  —  double-click a row to drill down"
        )

    def _build_pc_rows(self, df: pd.DataFrame) -> list:
        rows = []
        if "price_class" not in df.columns:
            return rows
        for pc_code, g in df.groupby("price_class", sort=True):
            desc = ""
            if "price_class_desc" in g.columns:
                d = str(g["price_class_desc"].iloc[0])
                desc = d if d not in ("nan", "", "None") else ""

            inv_sy    = g["inventory_sy"].sum()
            on_order  = g["on_order_sy"].sum()
            net_inv   = g["net_inventory_sy"].sum()
            avg_daily = g["avg_daily_sales_sy"].sum()
            doi       = inv_sy / avg_daily if avg_daily > 0 else _INF
            oc        = g["orders_count"].sum()
            fr        = ((g["fill_rate"] * g["orders_count"]).sum() / oc) if oc > 0 else 1.0
            runout    = int(g["runout_risk"].sum()) if "runout_risk" in g.columns else 0
            overstock = int(g["overstock_flag"].sum()) if "overstock_flag" in g.columns else 0
            turn      = avg_daily * 365 / inv_sy if inv_sy > 0 else 0.0
            ratings   = (
                "/".join(str(int((g["sku_rating"] == r).sum())) for r in "ABCD")
                if "sku_rating" in g.columns else "—"
            )
            rows.append([
                str(pc_code), desc, str(len(g)),
                f"{inv_sy:,.1f}", f"{on_order:,.1f}",
                f"{net_inv:,.1f}",
                f"{avg_daily:.2f}",
                f"{doi:.0f}" if doi < _INF else "∞",
                f"{fr * 100:.1f}%",
                str(runout), str(overstock), f"{turn:.2f}x", ratings,
            ])
        return rows

    # ------------------------------------------------------------------
    # SKU table
    # ------------------------------------------------------------------

    def _refresh_sku_table(self, df: pd.DataFrame) -> None:
        rows = []
        for _, row in df.iterrows():
            doi = row.get("days_of_inventory", _INF)
            doi_str = f"{doi:.0f}" if doi < _INF else "∞"
            fr   = row.get("fill_rate", 1.0)
            turn = row.get("stock_turn", 0)
            launch = row.get("launch_date")
            rows.append([
                row.get("sku", ""),
                row.get("sku_description", ""),
                row.get("price_class_desc", row.get("price_class", "")),
                row.get("cost_center", ""),
                row.get("sku_rating", ""),
                f"{row.get('inventory_sy', 0):,.1f}",
                f"{row.get('on_order_sy', 0):,.1f}",
                f"{row.get('net_inventory_sy', 0):,.1f}",
                f"{row.get('avg_daily_sales_sy', 0):.2f}",
                str(int(row.get("orders_count", 0))),
                str(int(row.get("backorder_count", 0))),
                f"{row.get('strict_bo_qty_sy', 0):,.1f}",
                doi_str,
                f"{row.get('inventory_age_days', 0):.0f}",
                f"{fr * 100:.1f}%",
                "Yes" if row.get("runout_risk") else "No",
                _safe_days(row.get("days_since_last_sale")),
                str(launch) if pd.notna(launch) else "—",
                str(int(row.get("lead_time_days", 30))),
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
            mask = df["sku"].str.upper().str.contains(q, na=False)
            if "sku_description" in df.columns:
                mask = mask | df["sku_description"].str.upper().str.contains(q, na=False)
            df = df[mask]
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

    # ------------------------------------------------------------------
    # KPI cards
    # ------------------------------------------------------------------

    def _refresh_kpis(self, summary: dict) -> None:
        if not summary:
            return
        self._kpis["stock_turn"].set_value(f"{summary.get('stock_turn', 0):.2f}x")
        fr = summary.get("fill_rate", 0)
        self._kpis["fill_rate"].set_value(
            f"{fr * 100:.1f}%", "success" if fr >= 0.95 else "warning"
        )
        self._kpis["days_of_inventory"].set_value(
            f"{summary.get('days_of_inventory', 0):.0f}d"
        )
        self._kpis["runout_sku_count"].set_value(
            str(summary.get("runout_sku_count", 0)), "danger"
        )
        self._kpis["overstock_count"].set_value(
            str(summary.get("overstock_count", 0)), "warning"
        )
        self._kpis["total_skus"].set_value(
            str(summary.get("total_skus", 0)), "text"
        )

    # ------------------------------------------------------------------
    # Click handlers
    # ------------------------------------------------------------------

    def _on_pc_double_clicked(self, row: int, _col: int) -> None:
        if self._bundle is None or self._current_df is None:
            return
        pc_item   = self._pc_table.item(row, 0)
        desc_item = self._pc_table.item(row, 1)
        if not pc_item:
            return
        pc_code = pc_item.text()
        pc_desc = desc_item.text() if desc_item else ""
        pc_df = self._current_df[self._current_df["price_class"] == pc_code]
        if pc_df.empty:
            return
        dlg = PriceClassDetailDialog(pc_code, pc_desc, pc_df, self._bundle, self)
        dlg.exec()

    def _on_sku_double_clicked(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item and self._bundle is not None:
            dlg = TimelineDialog(item.text(), self._bundle, self)
            dlg.open_in_tab.connect(self.sku_selected)
            dlg.show()

    # ------------------------------------------------------------------
    # Persisted settings
    # ------------------------------------------------------------------

    def _apply_saved_rules(self) -> None:
        from app.data.store import get_table_rules
        rules = get_table_rules("overview")
        self._table.set_rules(rules)
        self._pc_table.set_rules(rules)

    def _apply_saved_column_prefs(self) -> None:
        from app.data.store import get_column_prefs
        for col, visible in get_column_prefs("overview").items():
            self._table.set_column_visible(col, visible)
        for col, visible in get_column_prefs("overview_pc").items():
            self._pc_table.set_column_visible(col, visible)
        self._table.restore_column_widths()
        self._pc_table.restore_column_widths()

    def _open_column_manager(self) -> None:
        from app.data.store import set_column_prefs
        if self._view_mode == "price_class":
            tbl = self._pc_table
            key = "overview_pc"
        else:
            tbl = self._table
            key = "overview"
        dlg = ColumnManagerDialog(tbl._column_names, tbl, self)
        if dlg.exec():
            prefs = dlg.get_prefs()
            set_column_prefs(key, prefs)
            for col, visible in prefs.items():
                tbl.set_column_visible(col, visible)

    def _open_rules_dialog(self) -> None:
        from app.data.store import get_table_rules, set_table_rules
        # Offer columns from both views so rules work across the board
        all_cols = list(dict.fromkeys(
            self._table._column_names + self._pc_table._column_names
        ))
        dlg = ThresholdRulesDialog(all_cols, get_table_rules("overview"), self)
        if dlg.exec():
            rules = dlg.get_rules()
            set_table_rules("overview", rules)
            self._table.set_rules(rules)
            self._pc_table.set_rules(rules)
            if self._bundle is not None:
                filters = self._sidebar.get_filters()
                self._current_df = self._filter_metrics(
                    self._bundle.sku_metrics, filters
                )
                self._refresh_current_view()
