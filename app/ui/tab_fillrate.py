"""
Fill Rate tab — per-SKU fill rate analysis.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

import plotly.graph_objects as go

from app.services.metrics_service import DatasetBundle
from app.ui.widgets import (
    DataTable, FilterSidebar, KpiCard, SectionTitle, HSep,
    make_chart_widget, update_chart_widget,
)
import app.ui.theme as theme


class FillRateTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._chart_widget = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = FilterSidebar()
        self._sidebar.filters_changed.connect(self._on_filter)
        root.addWidget(self._sidebar)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(12)

        cl.addWidget(SectionTitle("Fill Rate Analysis"))
        cl.addWidget(HSep())

        # KPI row
        kpi_row = QHBoxLayout()
        self._kpis = {
            "portfolio_fill": KpiCard("Portfolio Fill Rate", "—", "success"),
            "below_90": KpiCard("SKUs Below 90%", "—", "warning"),
            "below_80": KpiCard("SKUs Below 80%", "—", "danger"),
            "zero_fill": KpiCard("0% Fill SKUs", "—", "danger"),
        }
        for c in self._kpis.values():
            kpi_row.addWidget(c)
        cl.addLayout(kpi_row)
        cl.addWidget(HSep())

        # Chart placeholder
        self._chart_placeholder = QLabel("Loading fill rate chart…")
        self._chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_placeholder.setStyleSheet(f"color: {theme.get('text_muted')};")
        self._chart_placeholder.setMinimumHeight(280)
        cl.addWidget(self._chart_placeholder)

        cl.addWidget(QLabel("Per-SKU Fill Rate:"))
        self._table = DataTable([
            "SKU", "Description", "Price Class", "Cost Center",
            "Orders", "Filled", "Backorders", "Fill Rate %", "Rating",
        ])
        cl.addWidget(self._table)

        root.addWidget(content)

    # ------------------------------------------------------------------

    def refresh(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle
        if bundle.filter_values is not None and not bundle.filter_values.empty:
            self._sidebar.populate(bundle.filter_values)
        self._render(bundle.sku_metrics)

    def _on_filter(self, filters: dict) -> None:
        if self._bundle is None:
            return
        df = self._bundle.sku_metrics
        df = self._apply_filters(df, filters)
        self._render(df)

    def _render(self, df: Optional[pd.DataFrame]) -> None:
        if df is None or df.empty:
            return

        port_fr = df["fill_rate"].mean()
        below_90 = int((df["fill_rate"] < 0.90).sum())
        below_80 = int((df["fill_rate"] < 0.80).sum())
        zero_fill = int((df["fill_rate"] == 0).sum())

        self._kpis["portfolio_fill"].set_value(
            f"{port_fr * 100:.1f}%",
            "success" if port_fr >= 0.95 else "warning" if port_fr >= 0.85 else "danger",
        )
        self._kpis["below_90"].set_value(str(below_90), "warning" if below_90 > 0 else "success")
        self._kpis["below_80"].set_value(str(below_80), "danger" if below_80 > 0 else "success")
        self._kpis["zero_fill"].set_value(str(zero_fill), "danger" if zero_fill > 0 else "success")

        # Histogram chart
        fig = self._build_chart(df)
        if self._chart_widget is None:
            self._chart_widget = make_chart_widget(fig)
            # replace placeholder
            content_widget = self.layout().itemAt(1).widget()
            cl = content_widget.layout()
            for i in range(cl.count()):
                item = cl.itemAt(i)
                if item and item.widget() is self._chart_placeholder:
                    cl.removeWidget(self._chart_placeholder)
                    self._chart_placeholder.hide()
                    cl.insertWidget(i, self._chart_widget)
                    break
        else:
            update_chart_widget(self._chart_widget, fig)

        # Table
        rows = []
        for _, row in df.sort_values("fill_rate").iterrows():
            fr = row.get("fill_rate", 1.0)
            rows.append([
                row.get("sku", ""),
                row.get("sku_description", ""),
                row.get("price_class_desc", row.get("price_class", "")),
                row.get("cost_center", ""),
                str(int(row.get("orders_count", 0))),
                str(int(row.get("filled_count", 0))),
                str(int(row.get("backorder_count", 0))),
                f"{fr * 100:.1f}%",
                row.get("sku_rating", ""),
            ])
        self._table.populate(rows)

    def _build_chart(self, df: pd.DataFrame):
        c = theme.DARK if theme.is_dark() else theme.LIGHT
        pct = (df["fill_rate"] * 100).round(1)
        fig = go.Figure(go.Histogram(
            x=pct, nbinsx=20,
            marker_color=c["accent"], opacity=0.85,
            hovertemplate="Fill Rate: %{x:.1f}%<br>SKUs: %{y}<extra></extra>",
        ))
        # Add 90% reference line
        fig.add_vline(x=90, line_color=c["warning"], line_dash="dash",
                      annotation_text="90% target", annotation_font_color=c["warning"])
        fig.update_layout(
            paper_bgcolor=c["chart_bg"], plot_bgcolor=c["chart_bg"],
            font=dict(color=c["text"], family="Segoe UI"),
            title="Fill Rate Distribution (%)",
            xaxis=dict(title="Fill Rate (%)", gridcolor=c["border"]),
            yaxis=dict(title="# SKUs", gridcolor=c["border"]),
            margin=dict(l=60, r=20, t=50, b=50),
        )
        return fig

    def _apply_filters(self, df: pd.DataFrame, filters: dict) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        q = filters.get("sku_search", "").strip().upper()
        if q:
            df = df[df["sku"].str.upper().str.contains(q, na=False)]
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
