"""
TimelineDialog — floating 180-day inventory projection for any SKU.

Can be opened from the Overview table (double-click a row) or from
any AlertCard in the Problem Areas tab.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

import plotly.graph_objects as go

from app.services.metrics_service import DatasetBundle, get_sku_timeline
from app.ui.widgets import DataTable, HSep, KpiCard, make_chart_widget, update_chart_widget
import app.ui.theme as theme

_INF = float("inf")


class TimelineDialog(QDialog):
    """Modal (non-blocking) inventory timeline popup for a single SKU."""

    # Emitted when the user wants to navigate to the full Timeline tab
    open_in_tab = pyqtSignal(str)

    def __init__(self, sku: str, bundle: DatasetBundle, parent=None):
        super().__init__(parent)
        self._sku = sku
        self._bundle = bundle
        self._chart_widget = None

        self.setWindowTitle(f"Inventory Timeline — {sku}")
        self.setMinimumSize(1020, 660)
        self.resize(1100, 720)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._build_ui()
        self._render()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        # --- Header row ---
        header_row = QHBoxLayout()
        self._sku_label = QLabel()
        self._sku_label.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {theme.get('text')};"
        )
        header_row.addWidget(self._sku_label)
        header_row.addStretch()

        btn_tab = QPushButton("Open in Timeline Tab →")
        btn_tab.setObjectName("flat")
        btn_tab.clicked.connect(lambda: self.open_in_tab.emit(self._sku))
        header_row.addWidget(btn_tab)

        root.addLayout(header_row)

        self._desc_label = QLabel()
        self._desc_label.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 13px;")
        root.addWidget(self._desc_label)
        root.addWidget(HSep())

        # --- KPI cards ---
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self._kpis = {
            "inventory_sy": KpiCard("Current Inventory (SY)", "—", "info"),
            "on_order_sy":  KpiCard("On Order (SY)", "—", "accent"),
            "avg_daily":    KpiCard("Avg Daily Sales (SY)", "—", "text"),
            "days_of_inv":  KpiCard("Days of Inventory", "—", "success"),
            "stockout_day": KpiCard("Projected Stockout", "—", "danger"),
        }
        for card in self._kpis.values():
            card.setMinimumWidth(150)
            kpi_row.addWidget(card)
        root.addLayout(kpi_row)

        # --- Chart placeholder ---
        self._chart_placeholder = QLabel("Building chart…")
        self._chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_placeholder.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 14px;"
        )
        self._chart_placeholder.setMinimumHeight(320)
        root.addWidget(self._chart_placeholder)

        # --- PO table ---
        root.addWidget(HSep())
        po_header = QHBoxLayout()
        po_header.addWidget(QLabel("Scheduled Purchase Orders:"))
        po_header.addStretch()
        root.addLayout(po_header)

        self._po_table = DataTable(["Order #", "ETA Date", "Qty (SY)", "Supplier"])
        self._po_table.setMaximumHeight(160)
        root.addWidget(self._po_table)

        # --- Recommendation banner ---
        self._rec_frame = QFrame()
        self._rec_frame.setObjectName("alert_card_warn")
        rec_l = QVBoxLayout(self._rec_frame)
        rec_l.setContentsMargins(12, 8, 12, 8)
        self._rec_label = QLabel()
        self._rec_label.setWordWrap(True)
        self._rec_label.setStyleSheet(
            f"color: {theme.get('warning')}; font-size: 13px;"
        )
        rec_l.addWidget(self._rec_label)
        root.addWidget(self._rec_frame)
        self._rec_frame.setVisible(False)

        # --- Close button ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_btn.setMinimumWidth(100)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> None:
        sku = self._sku
        bundle = self._bundle
        metrics_df = bundle.sku_metrics

        row = metrics_df[metrics_df["sku"] == sku]
        if row.empty:
            self._sku_label.setText(sku)
            self._desc_label.setText("No metrics available for this SKU.")
            return
        row = row.iloc[0]

        # Header
        self._sku_label.setText(sku)
        self._desc_label.setText(str(row.get("sku_description", "")))

        # KPIs
        inv_sy    = float(row.get("inventory_sy", 0))
        on_order  = float(row.get("on_order_sy", 0))
        avg_daily = float(row.get("avg_daily_sales_sy", 0))
        doi       = float(row.get("days_of_inventory", _INF))

        self._kpis["inventory_sy"].set_value(f"{inv_sy:,.1f} SY")
        self._kpis["on_order_sy"].set_value(f"{on_order:,.1f} SY")
        self._kpis["avg_daily"].set_value(f"{avg_daily:.2f} SY/day")
        self._kpis["days_of_inv"].set_value(
            f"{doi:.0f}d" if doi < _INF else "∞",
            "success" if doi > 60 else "warning" if doi > 20 else "danger",
        )

        # Get/build timeline lazily
        timeline_df = get_sku_timeline(sku, bundle)

        # Stockout
        stockout_day = None
        if timeline_df is not None and not timeline_df.empty:
            so_rows = timeline_df[timeline_df["stockout"]]
            if not so_rows.empty:
                stockout_day = so_rows.iloc[0]["date"]

        self._kpis["stockout_day"].set_value(
            str(stockout_day) if stockout_day else "No stockout",
            "danger" if stockout_day else "success",
        )

        # Chart
        po_events = bundle.po_events.get(sku, [])
        if timeline_df is not None and not timeline_df.empty:
            fig = _build_fig(sku, timeline_df, po_events)
            self._chart_widget = make_chart_widget(fig)
            layout = self.layout()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget() is self._chart_placeholder:
                    layout.removeWidget(self._chart_placeholder)
                    self._chart_placeholder.hide()
                    layout.insertWidget(i, self._chart_widget)
                    break

        # PO table — use po_events dict (always correct, no base_sku lookup needed)
        po_rows = []
        for ev in po_events:
            po_rows.append([
                ev.get("order_number", ""),
                str(ev.get("eta_date", "")),
                f"{ev.get('quantity_sy', 0):,.1f}",
                ev.get("supplier_number", ""),
            ])
        # Sort by ETA date
        po_rows.sort(key=lambda r: r[1])
        self._po_table.populate(po_rows)

        # Recommendation
        rec = _build_recommendation(row, stockout_day)
        if rec:
            self._rec_label.setText(rec)
            self._rec_frame.setVisible(True)
        else:
            self._rec_frame.setVisible(False)


# ---------------------------------------------------------------------------
# Module-level helpers (shared with TimelineTab)
# ---------------------------------------------------------------------------

def _build_fig(sku: str, df: pd.DataFrame, po_events: list):
    c = theme.DARK if theme.is_dark() else theme.LIGHT
    dates = df["date"].tolist()

    fig = go.Figure()

    # ── Stockout zone (below inventory line) ──────────────────────────────
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

    # ── Inventory projection area ─────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=dates, y=df["inventory_sy"].tolist(),
        mode="lines", name="Projected Inventory",
        line=dict(color=c["accent"], width=2.5),
        fill="tozeroy",
        fillcolor="rgba(78,140,255,0.12)",
        hovertemplate="<b>%{x|%b %d}</b><br>Inventory: %{y:,.1f} SY<extra></extra>",
    ))

    # ── PO receipt markers — vertical dotted lines + annotation labels ────
    # Group po_events by date so multiple POs on same day are combined
    receipt_totals: dict = {}
    receipt_orders: dict = {}
    for ev in po_events:
        d = ev.get("eta_date")
        if d and pd.notna(d):
            receipt_totals[d] = receipt_totals.get(d, 0.0) + ev.get("quantity_sy", 0)
            receipt_orders.setdefault(d, []).append(ev.get("order_number", ""))

    if receipt_totals:
        # Find y-axis max for annotation positioning
        y_max = max(df["inventory_sy"].max(), max(receipt_totals.values())) * 1.05

        receipt_dates = list(receipt_totals.keys())
        receipt_qtys  = [receipt_totals[d] for d in receipt_dates]

        # Scatter markers on the inventory curve at receipt dates
        # Get the inventory level at each receipt date
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

        # Add a vertical dashed line for each receipt date
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


def _build_recommendation(row: pd.Series, stockout_day) -> str:
    avg_daily = float(row.get("avg_daily_sales_sy", 0))
    inv_sy    = float(row.get("inventory_sy", 0))
    on_order  = float(row.get("on_order_sy", 0))
    lead_time = int(row.get("lead_time_days", 30))
    target    = float(row.get("stockturn_target", 4.0))

    if avg_daily == 0:
        return ""

    target_doi = 365.0 / target if target > 0 else _INF
    target_qty = avg_daily * target_doi
    needed = max(target_qty - inv_sy - on_order, 0)

    if stockout_day and on_order == 0:
        days_until = (stockout_day - date.today()).days
        return (
            f"\u26a0 Projected stockout in {days_until} day(s) with no open POs. "
            f"Reorder immediately — lead time is ~{lead_time} day(s). "
            f"Recommended order qty: {needed:,.0f} SY to reach {target:.1f}\u00d7 turn target."
        )
    if row.get("overstock_flag"):
        doi = float(row.get("days_of_inventory", _INF))
        doi_str = f"{doi:.0f}d" if doi < _INF else "\u221e"
        return (
            f"\u26a0 Overstock: {doi_str} of inventory vs. target {target_doi:.0f}d "
            f"({target:.1f}\u00d7 turn). Consider pausing or reducing future orders."
        )
    if row.get("excess_order_flag"):
        total = inv_sy + on_order
        return (
            f"\u26a0 Excess orders: total supply ({total:,.0f} SY) exceeds "
            f"{target:.1f}\u00d7 turn target for this period."
        )
    return ""
