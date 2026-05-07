"""
Problem Areas tab — focused triage view of three actionable inventory issues:

    • Overstock           — Inventory greatly exceeds demand × lead time.
    • Runout Risk         — Inventory + open POs cannot cover demand × lead time.
    • Zero Stock & No PO  — Item has demand but nothing on hand and nothing on order.

Each alert is presented as a clean card with snooze and timeline actions.
The header offers pill-style filter toggles so the user can narrow the view to
one or more problem types at a glance.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.data.store import is_snoozed, snooze_alert
from app.services.metrics_service import DatasetBundle
from app.ui.timeline_popup import TimelineDialog
from app.ui.widgets import FilterSidebar, HSep, SectionTitle
import app.ui.theme as theme


# ---------------------------------------------------------------------------
# Alert type definitions
# ---------------------------------------------------------------------------

# key → (display label, theme color key, icon, explanation)
_ALERT_TYPES: dict[str, tuple[str, str, str, str]] = {
    "overstock": (
        "Overstock",
        "warning",
        "▲",
        "Inventory greatly exceeds demand for the next lead-time window.",
    ),
    "runout_risk": (
        "Runout Risk",
        "danger",
        "▼",
        "At current sales rate, inventory will run out before the next PO arrives.",
    ),
    "no_stock": (
        "Zero Stock & No PO",
        "danger",
        "●",
        "Item has active demand but nothing on hand and nothing on order.",
    ),
}


# ---------------------------------------------------------------------------
# Alert Card
# ---------------------------------------------------------------------------

class AlertCard(QFrame):
    """One alert row — left color bar, info block, action buttons."""

    snoozed = pyqtSignal(str)              # alert_key
    timeline_requested = pyqtSignal(str)   # sku

    def __init__(self, alert_type: str, row: pd.Series, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._alert_type = alert_type
        self._sku = str(row.get("sku", "")).strip()
        self._po_qty = float(row.get("on_order_sy", 0))
        self._alert_key = f"{alert_type}:{self._sku}"

        label, color_key, icon, explanation = _ALERT_TYPES.get(
            alert_type, (alert_type, "warning", "•", "")
        )
        accent = theme.get(color_key)
        bg = theme.get("bg_card")
        border = theme.get("border")

        self.setObjectName("alert_card")
        self.setStyleSheet(
            f"QFrame#alert_card {{"
            f"  background-color: {bg};"
            f"  border: 1px solid {border};"
            f"  border-left: 5px solid {accent};"
            f"  border-radius: 6px;"
            f"}}"
            f"QFrame#alert_card:hover {{"
            f"  border-color: {accent};"
            f"}}"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 14, 12)
        outer.setSpacing(14)

        # ── Left: info block ────────────────────────────────────────────
        info = QVBoxLayout()
        info.setSpacing(4)

        # Header line: icon + alert label
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(
            f"color: {accent}; font-size: 14px; font-weight: 700;"
        )
        head_row.addWidget(icon_lbl)
        type_lbl = QLabel(label)
        type_lbl.setStyleSheet(
            f"color: {accent}; font-weight: 700; font-size: 13px;"
            "letter-spacing: 0.4px; text-transform: uppercase;"
        )
        head_row.addWidget(type_lbl)
        head_row.addStretch()
        info.addLayout(head_row)

        # SKU + description
        desc = str(row.get("sku_description", "")).strip()
        sku_html = (
            f"<span style='font-weight: 700; font-size: 14px; color: {theme.get('text')};'>"
            f"{self._sku}</span>"
        )
        if desc:
            sku_html += (
                f"<span style='color: {theme.get('text_muted')};'>"
                f"&nbsp;&nbsp;—&nbsp;&nbsp;{desc}</span>"
            )
        sku_label = QLabel(sku_html)
        sku_label.setTextFormat(Qt.TextFormat.RichText)
        info.addWidget(sku_label)

        # Explanation
        ex_lbl = QLabel(explanation)
        ex_lbl.setWordWrap(True)
        ex_lbl.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        info.addWidget(ex_lbl)

        # Metrics chips row
        metrics_row = self._build_metrics_row(row)
        if metrics_row:
            info.addLayout(metrics_row)

        outer.addLayout(info, 1)

        # ── Right: action buttons stacked ───────────────────────────────
        actions = QVBoxLayout()
        actions.setSpacing(6)
        actions.addStretch()

        snooze_btn = QPushButton("⏰  Snooze")
        snooze_btn.setObjectName("flat")
        snooze_btn.setMinimumWidth(110)
        snooze_btn.setToolTip("Hide this alert for a number of days or until PO qty changes")
        snooze_btn.clicked.connect(self._show_snooze_dialog)
        actions.addWidget(snooze_btn)

        timeline_btn = QPushButton("📈  Timeline")
        timeline_btn.setObjectName("flat")
        timeline_btn.setMinimumWidth(110)
        timeline_btn.setToolTip("View 180-day inventory projection for this SKU")
        timeline_btn.clicked.connect(lambda: self.timeline_requested.emit(self._sku))
        actions.addWidget(timeline_btn)

        actions.addStretch()
        outer.addLayout(actions)

    # ------------------------------------------------------------------

    def _build_metrics_row(self, row: pd.Series) -> Optional[QHBoxLayout]:
        inv = float(row.get("inventory_sy", 0) or 0)
        on_order = float(row.get("on_order_sy", 0) or 0)
        avg_daily = float(row.get("avg_daily_sales_sy", 0) or 0)
        _inf = float("inf")
        doi = float(row.get("days_of_inventory", _inf) or _inf)
        target = float(row.get("stockturn_target", 4.0) or 4.0)
        target_doi = 365.0 / target if target > 0 else _inf

        chips: list[tuple[str, str]] = [
            ("Inventory",    f"{inv:,.1f} SY"),
            ("On Order",     f"{on_order:,.1f} SY"),
            ("Avg Daily",    f"{avg_daily:.2f} SY/day"),
            ("Days of Inv",  f"{doi:.0f}d" if doi < _inf else "∞"),
            ("Target DOI",   f"{target_doi:.0f}d @ {target:.1f}x" if target_doi < _inf else "—"),
        ]

        row_lo = QHBoxLayout()
        row_lo.setSpacing(6)
        row_lo.setContentsMargins(0, 4, 0, 0)
        for k, v in chips:
            row_lo.addWidget(self._chip(k, v))
        row_lo.addStretch()
        return row_lo

    def _chip(self, key: str, val: str) -> QLabel:
        lbl = QLabel(
            f"<span style='color: {theme.get('text_muted')};'>{key}:&nbsp;</span>"
            f"<span style='color: {theme.get('text')}; font-weight: 600;'>{val}</span>"
        )
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(
            f"background-color: {theme.get('bg')};"
            f"border: 1px solid {theme.get('border')};"
            "border-radius: 4px; padding: 3px 8px; font-size: 11px;"
        )
        return lbl

    # ------------------------------------------------------------------

    def _show_snooze_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Snooze Alert")
        dlg.setMinimumWidth(360)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        header = QLabel(
            f"Snooze the <b>{_ALERT_TYPES[self._alert_type][0]}</b> alert "
            f"for <b>{self._sku}</b>:"
        )
        header.setWordWrap(True)
        lay.addWidget(header)

        mode_combo = QComboBox()
        mode_combo.addItems([
            "Snooze for N days",
            "Snooze until PO quantity changes",
        ])
        lay.addWidget(mode_combo)

        days_spin = QSpinBox()
        days_spin.setRange(1, 365)
        days_spin.setValue(30)
        days_spin.setPrefix("Days: ")
        lay.addWidget(days_spin)

        mode_combo.currentIndexChanged.connect(
            lambda idx: days_spin.setEnabled(idx == 0)
        )

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            if mode_combo.currentIndex() == 0:
                until = date.today() + timedelta(days=days_spin.value())
                snooze_alert(self._alert_key, until_date=until,
                             po_qty_at_snooze=self._po_qty)
            else:
                snooze_alert(self._alert_key, until_date=None,
                             po_qty_at_snooze=self._po_qty)
            self.snoozed.emit(self._alert_key)


# ---------------------------------------------------------------------------
# Filter pill toggle button
# ---------------------------------------------------------------------------

class _FilterPill(QPushButton):
    """Checkable pill button showing alert-type label + count."""

    def __init__(self, alert_type: str, label: str, color_key: str,
                 icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.alert_type = alert_type
        self._label = label
        self._color = theme.get(color_key)
        self._icon = icon
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(32)
        self.set_count(0)
        self._restyle()
        self.toggled.connect(lambda _: self._restyle())

    def set_count(self, n: int) -> None:
        self.setText(f"  {self._icon}  {self._label}  ·  {n}  ")

    def _restyle(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: {self._color};"
                f"  color: white;"
                f"  border: 1px solid {self._color};"
                f"  border-radius: 16px;"
                f"  padding: 4px 14px;"
                f"  font-weight: 700;"
                f"  font-size: 12px;"
                f"}}"
                f"QPushButton:hover {{ opacity: 0.9; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: transparent;"
                f"  color: {theme.get('text_muted')};"
                f"  border: 1px solid {theme.get('border')};"
                f"  border-radius: 16px;"
                f"  padding: 4px 14px;"
                f"  font-weight: 600;"
                f"  font-size: 12px;"
                f"}}"
                f"QPushButton:hover {{"
                f"  border-color: {self._color};"
                f"  color: {self._color};"
                f"}}"
            )


# ---------------------------------------------------------------------------
# Problem Areas Tab
# ---------------------------------------------------------------------------

class ProblemAreasTab(QWidget):
    sku_selected = pyqtSignal(str)

    # Maximum cards rendered per pagination batch.  Anything above this would
    # cause the UI to freeze for several seconds on large datasets.
    _PAGE_SIZE = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bundle: Optional[DatasetBundle] = None
        self._last_filters: dict = {}
        self._pills: dict[str, _FilterPill] = {}
        self._alerts: dict[str, list[pd.Series]] = {}   # cached after _build_alerts()
        self._visible_count: int = 0                    # how many cards rendered so far
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)            # debounce filter/pill toggles
        self._refresh_timer.timeout.connect(self._do_refresh)
        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = FilterSidebar()
        self._sidebar.filters_changed.connect(self._on_sidebar_filter)
        root.addWidget(self._sidebar)

        # ── Right side ─────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 16, 20, 16)
        rl.setSpacing(10)

        # Title row
        title_row = QHBoxLayout()
        title_row.addWidget(SectionTitle("Problem Areas"))
        title_row.addStretch()
        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet(
            f"color: {theme.get('text_muted')}; font-size: 12px;"
        )
        title_row.addWidget(self._lbl_summary)
        rl.addLayout(title_row)

        # Subtitle
        note = QLabel(
            "Triage view of items that need attention. "
            "SKUs launched less than 6 months ago are excluded. "
            "Snoozed alerts automatically reactivate when PO quantity changes."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        rl.addWidget(note)

        # ── Filter pill bar ────────────────────────────────────────────
        pill_bar = QFrame()
        pill_bar.setObjectName("pill_bar")
        pill_bar.setStyleSheet(
            f"QFrame#pill_bar {{"
            f"  background-color: {theme.get('bg_card')};"
            f"  border: 1px solid {theme.get('border')};"
            f"  border-radius: 8px;"
            f"}}"
        )
        pl = QHBoxLayout(pill_bar)
        pl.setContentsMargins(14, 10, 14, 10)
        pl.setSpacing(8)

        show_lbl = QLabel("Show:")
        show_lbl.setStyleSheet(
            f"color: {theme.get('text')}; font-weight: 600; font-size: 12px;"
        )
        pl.addWidget(show_lbl)

        for atype, (label, color_key, icon, _) in _ALERT_TYPES.items():
            pill = _FilterPill(atype, label, color_key, icon)
            pill.toggled.connect(self._refresh_view)
            self._pills[atype] = pill
            pl.addWidget(pill)

        pl.addStretch()

        # All / None convenience buttons
        btn_all = QPushButton("All")
        btn_all.setObjectName("flat")
        btn_all.setFixedWidth(60)
        btn_all.clicked.connect(lambda: self._set_all_pills(True))
        pl.addWidget(btn_all)

        btn_none = QPushButton("None")
        btn_none.setObjectName("flat")
        btn_none.setFixedWidth(60)
        btn_none.clicked.connect(lambda: self._set_all_pills(False))
        pl.addWidget(btn_none)

        rl.addWidget(pill_bar)

        # ── Scrollable alert list ──────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._alert_container = QWidget()
        self._alert_layout = QVBoxLayout(self._alert_container)
        self._alert_layout.setContentsMargins(0, 4, 0, 0)
        self._alert_layout.setSpacing(8)
        self._alert_layout.addStretch()
        self._scroll.setWidget(self._alert_container)
        rl.addWidget(self._scroll, 1)

        root.addWidget(right, 1)

    # ── Public API ─────────────────────────────────────────────────────

    def refresh(self, bundle: DatasetBundle) -> None:
        self._bundle = bundle
        if bundle.filter_values is not None and not bundle.filter_values.empty:
            self._sidebar.populate(bundle.filter_values)
        self._schedule_refresh()

    # ── Internal ───────────────────────────────────

    def _schedule_refresh(self) -> None:
        """Debounced trigger — collapses bursts of toggles into one rebuild."""
        self._refresh_timer.start()

    def _on_sidebar_filter(self, filters: dict) -> None:
        self._last_filters = filters
        self._schedule_refresh()

    def _set_all_pills(self, on: bool) -> None:
        for p in self._pills.values():
            p.blockSignals(True)
            p.setChecked(on)
            p.blockSignals(False)
        self._schedule_refresh()

    def _apply_sidebar_filters(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        f = self._last_filters
        q = (f.get("sku_search", "") or "").strip().upper()
        if q:
            df = df[df["sku"].str.upper().str.contains(q, na=False)]
        if f.get("cost_centers"):
            df = df[df["cost_center"].isin(f["cost_centers"])]
        if f.get("suppliers"):
            df = df[df["supplier_number"].isin(f["suppliers"])]
        if f.get("price_classes"):
            df = df[df["price_class"].isin(f["price_classes"])]
        if f.get("product_lines"):
            df = df[df["product_line"].isin(f["product_lines"])]
        if f.get("sku_ratings"):
            df = df[df["sku_rating"].isin(f["sku_ratings"])]
        return df

    def _build_alerts(self, df: pd.DataFrame) -> dict[str, list[pd.Series]]:
        """Return {alert_type: [row, row, ...]} after snooze + new-item exclusion.

        Each list is sorted by total_qty_sy (sales volume) DESCENDING so the
        most impactful items appear at the top.  Vectorised pre-filter for
        performance — we only iterate rows that actually qualify for an alert.
        """
        out: dict[str, list[pd.Series]] = {k: [] for k in _ALERT_TYPES}
        if df is None or df.empty:
            return out

        today = date.today()

        # ---- Pre-compute boolean masks (vectorised) -------------------
        sku_col       = df["sku"].astype(str).str.strip()
        launch_col    = df.get("launch_date")
        po_qty_col    = pd.to_numeric(df.get("on_order_sy", 0), errors="coerce").fillna(0)
        inv_col       = pd.to_numeric(df.get("inventory_sy", 0), errors="coerce").fillna(0)
        avg_daily_col = pd.to_numeric(df.get("avg_daily_sales_sy", 0), errors="coerce").fillna(0)
        bo_qty_col    = pd.to_numeric(df.get("strict_bo_qty_sy", 0), errors="coerce").fillna(0)
        sales_col     = pd.to_numeric(df.get("total_qty_sy", 0), errors="coerce").fillna(0)

        if launch_col is not None:
            def _is_new(d):
                if not pd.notna(d) or not isinstance(d, date):
                    return False
                return (today - d).days < 180
            new_mask = launch_col.apply(_is_new)
        else:
            new_mask = pd.Series(False, index=df.index)

        overstock_flag = df.get("overstock_flag", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        runout_flag    = df.get("runout_risk",    pd.Series(False, index=df.index)).fillna(False).astype(bool)

        # Overstock: only include items that have an open PO OR an active
        # backorder against them — those are the ones worth acting on now.
        overstock_mask = (
            overstock_flag
            & (~new_mask)
            & ((po_qty_col > 0) | (bo_qty_col > 0))
        )
        runout_mask    = runout_flag
        nostock_mask   = (
            (inv_col <= 0)
            & (po_qty_col <= 0)
            & (avg_daily_col > 0)
            & (~new_mask)
        )

        masks: dict[str, pd.Series] = {
            "overstock":   overstock_mask,
            "runout_risk": runout_mask,
            "no_stock":    nostock_mask,
        }

        for atype, mask in masks.items():
            if not mask.any():
                continue
            sub = df.loc[mask].copy()
            # Sort by sales DESC so highest-impact items appear first.
            sub["_sales_for_sort"] = sales_col.loc[mask].values
            sub = sub.sort_values("_sales_for_sort", ascending=False)
            for _, row in sub.iterrows():
                sku = str(row.get("sku", "")).strip()
                if not sku:
                    continue
                po_qty = float(row.get("on_order_sy", 0) or 0)
                if is_snoozed(f"{atype}:{sku}", po_qty):
                    continue
                out[atype].append(row)

        return out

    def _clear_alerts(self) -> None:
        # Remove every widget except the trailing stretch
        while self._alert_layout.count() > 1:
            item = self._alert_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()

    def _do_refresh(self) -> None:
        """Full rebuild of alert list — always renders the first PAGE_SIZE cards."""
        if self._bundle is None:
            return
        df = self._apply_sidebar_filters(self._bundle.sku_metrics)
        self._alerts = self._build_alerts(df)

        # Update pill counts (shows TOTAL matched, not just rendered)
        for atype, pill in self._pills.items():
            pill.set_count(len(self._alerts.get(atype, [])))

        self._visible_count = 0
        self._render_cards(reset=True)

    def _render_cards(self, reset: bool) -> None:
        """Render the next batch of alert cards (or rebuild from scratch)."""
        order = ["runout_risk", "no_stock", "overstock"]
        # Build a flat ordered list of (atype, row) pairs, respecting pill toggles
        flat: list[tuple[str, pd.Series]] = []
        for atype in order:
            if not self._pills[atype].isChecked():
                continue
            for row in self._alerts.get(atype, []):
                flat.append((atype, row))

        total_matching = len(flat)

        # Repaint: throttle UI updates while we manipulate widgets
        self._alert_container.setUpdatesEnabled(False)
        try:
            if reset:
                self._clear_alerts()
                self._visible_count = 0

            # Render up to PAGE_SIZE more cards from the flat list
            new_target = min(self._visible_count + self._PAGE_SIZE, total_matching)
            for atype, row in flat[self._visible_count:new_target]:
                card = AlertCard(atype, row, self._alert_container)
                card.snoozed.connect(self._on_card_snoozed)
                card.timeline_requested.connect(self._on_timeline_requested)
                self._alert_layout.insertWidget(self._alert_layout.count() - 1, card)
            self._visible_count = new_target

            # Empty state
            if total_matching == 0:
                empty = QLabel(self._empty_state_text(self._alerts))
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                empty.setWordWrap(True)
                empty.setStyleSheet(
                    f"color: {theme.get('text_muted')}; "
                    f"font-size: 14px; padding: 60px 20px;"
                )
                self._alert_layout.insertWidget(self._alert_layout.count() - 1, empty)
            elif self._visible_count < total_matching:
                # "Load more" footer button
                more_btn = QPushButton(
                    f"▼  Show next {min(self._PAGE_SIZE, total_matching - self._visible_count)} "
                    f"·  ({self._visible_count:,} of {total_matching:,} shown)"
                )
                more_btn.setObjectName("flat")
                more_btn.setMinimumHeight(40)
                more_btn.clicked.connect(self._on_load_more)
                self._alert_layout.insertWidget(self._alert_layout.count() - 1, more_btn)
        finally:
            self._alert_container.setUpdatesEnabled(True)

        # Header summary
        total_all = sum(len(v) for v in self._alerts.values())
        if total_all == total_matching:
            self._lbl_summary.setText(
                f"{total_all:,} active alert{'s' if total_all != 1 else ''}"
            )
        else:
            self._lbl_summary.setText(
                f"{total_matching:,} of {total_all:,} alerts shown"
            )

    def _on_load_more(self) -> None:
        # Remove the load-more button (it's right before the trailing stretch)
        for i in range(self._alert_layout.count() - 2, -1, -1):
            item = self._alert_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, QPushButton) and w.objectName() == "flat":
                self._alert_layout.takeAt(i)
                w.deleteLater()
                break
        self._render_cards(reset=False)

    def _empty_state_text(self, alerts: dict[str, list]) -> str:
        any_checked = any(p.isChecked() for p in self._pills.values())
        if not any_checked:
            return "No problem types selected.  Choose one or more pills above to view alerts."
        if sum(len(v) for v in alerts.values()) == 0:
            return "✓  No active alerts.  All items look healthy."
        return "No alerts match the current filter combination."

    # ── Card signals ──────────────────────────────────────────────────

    def _on_card_snoozed(self, _key: str) -> None:
        self._schedule_refresh()

    def _on_timeline_requested(self, sku: str) -> None:
        if self._bundle is None or not sku:
            return
        dlg = TimelineDialog(sku, self._bundle, self)
        dlg.open_in_tab.connect(self.sku_selected)
        dlg.show()
