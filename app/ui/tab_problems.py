"""
Problem Areas tab — overstock, excess orders, stockouts, aging.
Supports snooze per alert.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
    QComboBox, QCheckBox,
)

from app.data.store import snooze_alert, is_snoozed, unsnooze_alert
from app.services.metrics_service import DatasetBundle
from app.ui.widgets import FilterSidebar, SectionTitle, HSep
from app.ui.timeline_popup import TimelineDialog
import app.ui.theme as theme


_ALERT_TYPES = {
    "overstock": ("Overstock", "warning", "This SKU has too much inventory relative to its stock-turn target."),
    "excess_order": ("Excess Orders", "warning", "Open POs would push total supply well above the target turn rate."),
    "stockout": ("Out of Stock", "danger", "No inventory on hand but the item has active demand."),
    "runout_risk": ("Runout Risk", "danger", "At current sales rate, inventory will be exhausted before a PO can arrive."),
    "aging": ("Aging Inventory", "info", "This SKU has not sold in 18+ months."),
}


class AlertCard(QFrame):
    snoozed = pyqtSignal(str, str)          # alert_key, "snoozed"
    timeline_requested = pyqtSignal(str)    # sku

    def __init__(self, alert_type: str, row: pd.Series, parent=None):
        super().__init__(parent)
        self._alert_type = alert_type
        self._sku = str(row.get("sku", ""))
        self._po_qty = float(row.get("on_order_sy", 0))
        self._alert_key = f"{alert_type}:{self._sku}"

        type_label, color_key, explanation = _ALERT_TYPES.get(alert_type, (alert_type, "warning", ""))
        border_color = theme.get(color_key)

        self.setObjectName("alert_card" if color_key == "danger" else "alert_card_warn")
        self.setStyleSheet(
            f"QFrame#alert_card, QFrame#alert_card_warn {{"
            f"border-left: 4px solid {border_color};"
            f"background-color: {theme.get('bg_card')};"
            f"border-radius: 4px;"
            f"padding: 8px;}}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # Title row
        title_row = QHBoxLayout()
        type_lbl = QLabel(type_label)
        type_lbl.setStyleSheet(f"color: {border_color}; font-weight: 700; font-size: 13px;")
        title_row.addWidget(type_lbl)
        title_row.addStretch()

        snooze_btn = QPushButton("Snooze")
        snooze_btn.setObjectName("flat")
        snooze_btn.setFixedWidth(70)
        snooze_btn.clicked.connect(self._show_snooze_dialog)
        title_row.addWidget(snooze_btn)

        timeline_btn = QPushButton("📈 Timeline")
        timeline_btn.setObjectName("flat")
        timeline_btn.setFixedWidth(90)
        timeline_btn.setToolTip("View 180-day inventory projection for this SKU")
        timeline_btn.clicked.connect(lambda: self.timeline_requested.emit(self._sku))
        title_row.addWidget(timeline_btn)

        lay.addLayout(title_row)

        # SKU + description
        sku_label = QLabel(f"<b>{self._sku}</b> — {row.get('sku_description', '')}")
        sku_label.setStyleSheet(f"color: {theme.get('text')};")
        lay.addWidget(sku_label)

        # Explanation
        ex_lbl = QLabel(explanation)
        ex_lbl.setWordWrap(True)
        ex_lbl.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        lay.addWidget(ex_lbl)

        # Metrics row
        metrics = self._build_metrics_text(row)
        if metrics:
            m_lbl = QLabel(metrics)
            m_lbl.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 11px; font-family: monospace;")
            lay.addWidget(m_lbl)

    def _build_metrics_text(self, row: pd.Series) -> str:
        parts = []
        inv = row.get("inventory_sy", 0)
        on_order = row.get("on_order_sy", 0)
        avg_daily = row.get("avg_daily_sales_sy", 0)
        _inf = float("inf")
        doi = row.get("days_of_inventory", _inf)
        target = row.get("stockturn_target", 4.0)
        target_doi = 365.0 / target if target > 0 else _inf

        parts.append(f"Inventory: {inv:,.1f} SY")
        parts.append(f"On Order: {on_order:,.1f} SY")
        parts.append(f"Avg Daily Sales: {avg_daily:.2f} SY/day")
        doi_str = f"{doi:.0f}d" if doi < _inf else "∞"
        parts.append(f"Days of Inventory: {doi_str} (target: {target_doi:.0f}d @ {target:.1f}x turn)")
        return "   |   ".join(parts)

    def _show_snooze_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Snooze Alert")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"Snooze alert for <b>{self._sku}</b>:"))

        mode_combo = QComboBox()
        mode_combo.addItems(["Snooze for N days", "Snooze until PO quantity changes"])
        lay.addWidget(mode_combo)

        days_spin = QSpinBox()
        days_spin.setRange(1, 365)
        days_spin.setValue(30)
        days_spin.setPrefix("Days: ")
        lay.addWidget(days_spin)

        def _on_mode_change(idx):
            days_spin.setEnabled(idx == 0)

        mode_combo.currentIndexChanged.connect(_on_mode_change)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            if mode_combo.currentIndex() == 0:
                until = date.today() + timedelta(days=days_spin.value())
                snooze_alert(self._alert_key, until_date=until, po_qty_at_snooze=self._po_qty)
            else:
                snooze_alert(self._alert_key, until_date=None, po_qty_at_snooze=self._po_qty)
            self.snoozed.emit(self._alert_key, "snoozed")


class ProblemAreasTab(QWidget):
    sku_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
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
        cl.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.addWidget(SectionTitle("Problem Areas"))
        header_row.addStretch()
        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(f"color: {theme.get('text_muted')};")
        header_row.addWidget(self._lbl_count)
        cl.addLayout(header_row)

        note = QLabel(
            "SKUs launched less than 6 months ago are excluded from problem alerts. "
            "Snoozed alerts automatically reactivate when PO quantity changes."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        cl.addWidget(note)
        cl.addWidget(HSep())

        # Scrollable alert list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._alert_container = QWidget()
        self._alert_layout = QVBoxLayout(self._alert_container)
        self._alert_layout.setContentsMargins(0, 0, 0, 0)
        self._alert_layout.setSpacing(10)
        self._alert_layout.addStretch()
        self._scroll.setWidget(self._alert_container)
        cl.addWidget(self._scroll)

        root.addWidget(content)

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
        # Clear
        while self._alert_layout.count() > 1:
            item = self._alert_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if df is None or df.empty:
            self._lbl_count.setText("No data")
            return

        cards_added = 0
        alert_map = {
            "overstock": "overstock_flag",
            "excess_order": "excess_order_flag",
            "stockout": "stockout_flag",
            "runout_risk": "runout_risk",
        }

        today = date.today()

        for _, row in df.iterrows():
            # Skip new items (< 6 months)
            launch = row.get("launch_date")
            is_new = (
                pd.notna(launch) and isinstance(launch, date)
                and (today - launch).days < 180
            )

            for alert_type, flag_col in alert_map.items():
                if flag_col in df.columns and row.get(flag_col):
                    if is_new and alert_type in ("overstock", "excess_order"):
                        continue
                    alert_key = f"{alert_type}:{row['sku']}"
                    po_qty = float(row.get("on_order_sy", 0))
                    if is_snoozed(alert_key, po_qty):
                        continue
                    card = AlertCard(alert_type, row)
                    card.snoozed.connect(lambda key, _: self._on_snoozed(key))
                    card.timeline_requested.connect(self._on_timeline_requested)
                    self._alert_layout.insertWidget(self._alert_layout.count() - 1, card)
                    cards_added += 1

            # Aging check (not gated by is_new since aging implies item is old)
            dsls = row.get("days_since_last_sale")
            if dsls is not None and not pd.isna(dsls) and float(dsls) >= 540:
                alert_key = f"aging:{row['sku']}"
                if not is_snoozed(alert_key, 0.0):
                    card = AlertCard("aging", row)
                    card.snoozed.connect(lambda key, _: self._on_snoozed(key))
                    card.timeline_requested.connect(self._on_timeline_requested)
                    self._alert_layout.insertWidget(self._alert_layout.count() - 1, card)
                    cards_added += 1

        self._lbl_count.setText(f"{cards_added} active alert(s)")

    def _on_snoozed(self, _key: str) -> None:
        if self._bundle:
            self._render(self._bundle.sku_metrics)

    def _on_timeline_requested(self, sku: str) -> None:
        if self._bundle is not None:
            dlg = TimelineDialog(sku, self._bundle, self)
            dlg.open_in_tab.connect(self.sku_selected)
            dlg.show()

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
