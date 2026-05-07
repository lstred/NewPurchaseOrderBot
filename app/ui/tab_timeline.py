"""
Inventory Timeline tab — per-SKU 180-day forward projection.
Shows current inventory, incoming POs, daily consumption, and projected stockouts.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget, QFrame, QScrollArea,
)

import plotly.graph_objects as go

from app.services.metrics_service import DatasetBundle
from app.ui.widgets import (
    DataTable, FilterSidebar, SectionTitle, HSep, KpiCard,
    make_chart_widget, update_chart_widget,
)
import app.ui.theme as theme


class TimelineTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._chart_widget = None
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self._sidebar = FilterSidebar()
        self._sidebar.filters_changed.connect(self._on_filter_change)
        root.addWidget(self._sidebar)

        # Main
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(12)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.addWidget(SectionTitle("Inventory Timeline"))
        ctrl.addStretch()
        ctrl.addWidget(QLabel("SKU:"))
        self._sku_combo = QComboBox()
        self._sku_combo.setMinimumWidth(240)
        self._sku_combo.currentTextChanged.connect(self._on_sku_changed)
        ctrl.addWidget(self._sku_combo)
        cl.addLayout(ctrl)
        cl.addWidget(HSep())

        # KPI mini row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self._kpis = {
            "inventory_sy": KpiCard("Current Inventory (SY)", "—", "info"),
            "on_order_sy": KpiCard("On Order (SY)", "—", "accent"),
            "avg_daily": KpiCard("Avg Daily Sales (SY)", "—", "text"),
            "days_of_inv": KpiCard("Days of Inventory", "—", "success"),
            "stockout_day": KpiCard("Projected Stockout", "—", "danger"),
        }
        for card in self._kpis.values():
            card.setMinimumWidth(140)
            kpi_row.addWidget(card)
        cl.addLayout(kpi_row)

        # Chart placeholder
        self._chart_placeholder = QLabel("Select a SKU to view its inventory timeline.")
        self._chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_placeholder.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 14px;")
        self._chart_placeholder.setMinimumHeight(360)
        cl.addWidget(self._chart_placeholder)

        # PO detail table
        cl.addWidget(HSep())
        cl.addWidget(QLabel("Scheduled Purchase Orders:"))
        self._po_table = DataTable(["Order #", "ETA Date", "Qty (SY)", "Supplier"])
        self._po_table.setMaximumHeight(180)
        cl.addWidget(self._po_table)

        # Recommendation
        self._rec_frame = QFrame()
        self._rec_frame.setObjectName("alert_card_warn")
        rec_l = QVBoxLayout(self._rec_frame)
        self._rec_label = QLabel("")
        self._rec_label.setWordWrap(True)
        self._rec_label.setStyleSheet(f"color: {theme.get('warning')}; font-size: 13px;")
        rec_l.addWidget(self._rec_label)
        cl.addWidget(self._rec_frame)
        self._rec_frame.setVisible(False)

        cl.addStretch()
        root.addWidget(content)

    # ------------------------------------------------------------------

    def refresh(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle
        if bundle.filter_values is not None and not bundle.filter_values.empty:
            self._sidebar.populate(bundle.filter_values)
        self._repopulate_combo(bundle.sku_metrics)

    def select_sku(self, sku: str) -> None:
        idx = self._sku_combo.findText(sku)
        if idx >= 0:
            self._sku_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------

    def _repopulate_combo(self, df: Optional[pd.DataFrame]) -> None:
        self._sku_combo.blockSignals(True)
        current = self._sku_combo.currentText()
        self._sku_combo.clear()
        if df is not None and not df.empty:
            skus = sorted(df["sku"].unique().tolist())
            self._sku_combo.addItems(skus)
            idx = self._sku_combo.findText(current)
            if idx >= 0:
                self._sku_combo.setCurrentIndex(idx)
        self._sku_combo.blockSignals(False)
        self._on_sku_changed(self._sku_combo.currentText())

    def _on_filter_change(self, filters: dict) -> None:
        if self._bundle is None:
            return
        df = self._bundle.sku_metrics
        self._repopulate_combo(df)

    def _on_sku_changed(self, sku: str) -> None:
        if not sku or self._bundle is None:
            return
        self._render_sku(sku)

    def _render_sku(self, sku: str) -> None:
        if self._bundle is None:
            return
        from app.services.metrics_service import get_sku_timeline
        metrics_df = self._bundle.sku_metrics

        # SKU row
        row = metrics_df[metrics_df["sku"] == sku]
        if row.empty:
            return
        row = row.iloc[0]

        inv_sy = float(row.get("inventory_sy", 0))
        on_order = float(row.get("on_order_sy", 0))
        avg_daily = float(row.get("avg_daily_sales_sy", 0))
        _inf = float("inf")
        doi = float(row.get("days_of_inventory", _inf))

        self._kpis["inventory_sy"].set_value(f"{inv_sy:,.1f} SY")
        self._kpis["on_order_sy"].set_value(f"{on_order:,.1f} SY")
        self._kpis["avg_daily"].set_value(f"{avg_daily:.2f} SY/day")
        self._kpis["days_of_inv"].set_value(
            f"{doi:.0f}d" if doi < _inf else "∞",
            "success" if doi > 60 else "warning" if doi > 20 else "danger",
        )

        # Build timeline lazily
        timeline_df = get_sku_timeline(sku, self._bundle)

        # Stockout projection
        stockout_day = None
        if timeline_df is not None and not timeline_df.empty:
            so_rows = timeline_df[timeline_df["stockout"]]
            if not so_rows.empty:
                stockout_day = so_rows.iloc[0]["date"]

        self._kpis["stockout_day"].set_value(
            str(stockout_day) if stockout_day else "No stockout",
            "danger" if stockout_day else "success",
        )

        # PO events for this SKU (pre-built, always correct)
        po_events = self._bundle.po_events.get(sku, [])

        # Build chart
        if timeline_df is not None and not timeline_df.empty:
            fig = self._build_fig(sku, timeline_df, po_events)
            if self._chart_widget is None:
                self._chart_widget = make_chart_widget(fig)
                # Replace placeholder
                layout = self.layout()
                content_widget = layout.itemAt(1).widget()
                cl = content_widget.layout()
                # Find and replace placeholder
                for i in range(cl.count()):
                    item = cl.itemAt(i)
                    if item and item.widget() is self._chart_placeholder:
                        cl.removeWidget(self._chart_placeholder)
                        self._chart_placeholder.hide()
                        cl.insertWidget(i, self._chart_widget)
                        break
            else:
                update_chart_widget(self._chart_widget, fig)

        # PO detail table — populate from po_events (same source as chart markers)
        po_rows = []
        for ev in po_events:
            eta = ev.get("eta_date")
            po_rows.append([
                str(ev.get("order_number", "")),
                str(eta) if pd.notna(eta) else "No ETA",
                f"{ev.get('quantity_sy', 0):,.1f}",
                str(ev.get("supplier_number", "")),
            ])
        po_rows.sort(key=lambda r: r[1])
        self._po_table.populate(po_rows)

        # Recommendation
        rec = self._build_recommendation(row, stockout_day, po_events, sku)
        if rec:
            self._rec_label.setText(rec)
            self._rec_frame.setVisible(True)
        else:
            self._rec_frame.setVisible(False)

    def _build_fig(self, sku: str, df: pd.DataFrame, po_events: list):
        c = theme.DARK if theme.is_dark() else theme.LIGHT
        dates = df["date"].tolist()

        fig = go.Figure()

        # ── Stockout zone ──────────────────────────────────────────────────
        so = df[df["stockout"]]
        if not so.empty:
            fig.add_vrect(
                x0=so.iloc[0]["date"], x1=df.iloc[-1]["date"],
                fillcolor="rgba(224,82,96,0.12)",
                layer="below", line_width=0,
                annotation_text="Stockout Zone",
                annotation_position="top left",
                annotation_font_color=c["danger"],
            )

        # ── Inventory projection area ──────────────────────────────────────
        fig.add_trace(go.Scatter(
            x=dates, y=df["inventory_sy"].tolist(),
            mode="lines", name="Projected Inventory",
            line=dict(color=c["accent"], width=2.5),
            fill="tozeroy",
            fillcolor="rgba(78,140,255,0.12)",
            hovertemplate="<b>%{x|%b %d}</b><br>Inventory: %{y:,.1f} SY<extra></extra>",
        ))

        # ── PO receipt vertical lines + markers ────────────────────────────
        receipt_totals: dict = {}
        receipt_orders: dict = {}
        for ev in po_events:
            d = ev.get("eta_date")
            if d and pd.notna(d):
                receipt_totals[d] = receipt_totals.get(d, 0.0) + ev.get("quantity_sy", 0)
                receipt_orders.setdefault(d, []).append(ev.get("order_number", ""))

        if receipt_totals:
            receipt_dates = list(receipt_totals.keys())
            receipt_qtys  = [receipt_totals[d] for d in receipt_dates]

            df_idx = df.set_index("date")
            receipt_y_on_curve = []
            for d in receipt_dates:
                if d in df_idx.index:
                    receipt_y_on_curve.append(df_idx.loc[d, "inventory_sy"])
                else:
                    receipt_y_on_curve.append(0)

            fig.add_trace(go.Scatter(
                x=receipt_dates,
                y=receipt_y_on_curve,
                mode="markers",
                name="PO Receipt",
                marker=dict(
                    symbol="triangle-up",
                    size=14,
                    color=c["success"],
                    line=dict(color=c["success"], width=2),
                ),
                customdata=[[receipt_totals[d], ", ".join(receipt_orders[d])] for d in receipt_dates],
                hovertemplate=(
                    "<b>PO Receipt — %{x|%b %d, %Y}</b><br>"
                    "Incoming: <b>%{customdata[0]:,.1f} SY</b><br>"
                    "Order(s): %{customdata[1]}<extra></extra>"
                ),
            ))

            for d, qty in receipt_totals.items():
                fig.add_vline(
                    x=str(d),
                    line=dict(color=c["success"], width=1.5, dash="dot"),
                    annotation_text=f"+{qty:,.0f} SY",
                    annotation_position="top",
                    annotation_font=dict(color=c["success"], size=11),
                    annotation_bgcolor="rgba(0,0,0,0)",
                )

        fig.update_layout(
            paper_bgcolor=c["chart_bg"],
            plot_bgcolor=c["chart_bg"],
            font=dict(color=c["text"], family="Segoe UI"),
            title=dict(text=f"Inventory Projection — {sku}", font=dict(size=14)),
            xaxis=dict(gridcolor=c["border"], title="Date"),
            yaxis=dict(gridcolor=c["border"], title="Quantity (SY)"),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=60, r=20, t=50, b=50),
            hovermode="x unified",
        )
        return fig

    def _build_recommendation(self, row, stockout_day, po_events: list, sku: str) -> str:
        avg_daily = float(row.get("avg_daily_sales_sy", 0))
        inv_sy = float(row.get("inventory_sy", 0))
        on_order = float(row.get("on_order_sy", 0))
        lead_time = int(row.get("lead_time_days", 30))
        target = float(row.get("stockturn_target", 4.0))

        if avg_daily == 0:
            return ""

        target_doi = 365.0 / target
        target_qty = avg_daily * target_doi
        needed = max(target_qty - inv_sy - on_order, 0)

        if stockout_day and not po_events:
            from datetime import date
            days_until = (stockout_day - date.today()).days
            return (
                f"⚠ Projected stockout in {days_until} day(s) with no open POs. "
                f"Reorder immediately — lead time is ~{lead_time} day(s). "
                f"Recommended order quantity: {needed:.0f} SY to reach {target:.1f}x turn target."
            )
        if row.get("overstock_flag"):
            lt_demand = avg_daily * lead_time
            inv_at_arrival = max(inv_sy - lt_demand, 0)
            proj = inv_at_arrival + on_order
            proj_days = proj / avg_daily if avg_daily > 0 else float("inf")
            proj_days_str = f"{proj_days:.0f}d" if proj_days < float("inf") else "∞"
            return (
                f"⚠ Overstock: after the on-order arrives, projected supply is {proj:,.0f} SY "
                f"(~{proj_days_str}), exceeding 3× the {lead_time}-day lead-time demand "
                f"({lt_demand * 3:,.0f} SY). Consider pausing or reducing future orders."
            )
        if row.get("excess_order_flag"):
            return (
                f"⚠ Excess orders on the books: total supply ({inv_sy + on_order:.0f} SY) "
                f"exceeds {target:.1f}x turn target for this period."
            )
        return ""
