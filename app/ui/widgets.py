"""
Reusable widget components shared across tabs.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QLineEdit, QCheckBox, QScrollArea, QGroupBox,
)

import app.ui.theme as theme


# ---------------------------------------------------------------------------
# Rule matching for DataTable threshold coloring
# ---------------------------------------------------------------------------

def _rule_matches(cell_val: str, op: str, threshold: str) -> bool:
    """Return True if cell_val satisfies the threshold rule."""
    # Strip non-numeric characters for numeric comparison
    clean = re.sub(r"[^0-9.\-]", "", cell_val)
    try:
        num = float(clean)
        thr = float(threshold)
        if op == ">":  return num > thr
        if op == ">=": return num >= thr
        if op == "<":  return num < thr
        if op == "<=": return num <= thr
        if op == "=":  return abs(num - thr) < 1e-9
        if op == "!=": return abs(num - thr) >= 1e-9
    except ValueError:
        pass
    # String fallback
    cv, tv = cell_val.strip().lower(), threshold.strip().lower()
    if op == "=":        return cv == tv
    if op == "!=":       return cv != tv
    if op == "contains": return tv in cv
    return False


# ---------------------------------------------------------------------------
# KPI card
# ---------------------------------------------------------------------------

class KpiCard(QFrame):
    def __init__(self, label: str, value: str = "—", color_key: str = "accent", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self._value_label = QLabel(value)
        self._value_label.setObjectName("kpi_value")
        self._value_label.setStyleSheet(f"color: {theme.get(color_key)}; font-size: 28px; font-weight: 700;")

        self._label = QLabel(label.upper())
        self._label.setObjectName("kpi_label")

        layout.addWidget(self._value_label)
        layout.addWidget(self._label)

    def set_value(self, val: str, color_key: str = "accent") -> None:
        self._value_label.setText(val)
        self._value_label.setStyleSheet(f"color: {theme.get(color_key)}; font-size: 28px; font-weight: 700;")

    def refresh_theme(self) -> None:
        self.setStyleSheet("")  # trigger re-apply via parent QSS


# ---------------------------------------------------------------------------
# Section heading
# ---------------------------------------------------------------------------

class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("section_title")
        font = QFont()
        font.setPointSize(14)
        font.setWeight(QFont.Weight.Bold)
        self.setFont(font)


# ---------------------------------------------------------------------------
# Horizontal rule separator
# ---------------------------------------------------------------------------

class HSep(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"color: {theme.get('border')};")
        self.setFixedHeight(1)


# ---------------------------------------------------------------------------
# Sortable table with column visibility and threshold-coloring rules
# ---------------------------------------------------------------------------

class DataTable(QTableWidget):
    def __init__(self, columns: list[str], parent=None):
        super().__init__(0, len(columns), parent)
        self._column_names: list[str] = list(columns)
        self._rules: list[dict] = []
        self.setHorizontalHeaderLabels(columns)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionsMovable(True)  # drag-to-reorder
        self.verticalHeader().setVisible(False)
        self.setShowGrid(True)

    # ------------------------------------------------------------------
    # Column visibility
    # ------------------------------------------------------------------

    def set_column_visible(self, col_name: str, visible: bool) -> None:
        """Show or hide a column by its header label."""
        try:
            idx = self._column_names.index(col_name)
            self.setColumnHidden(idx, not visible)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Threshold coloring rules
    # ------------------------------------------------------------------

    def set_rules(self, rules: list[dict]) -> None:
        """Set the coloring rules applied on every populate() call."""
        self._rules = list(rules)

    # ------------------------------------------------------------------
    # Data population with rule-based coloring
    # ------------------------------------------------------------------

    def populate(self, rows: list[list]) -> None:
        self.setUpdatesEnabled(False)
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for r, row_data in enumerate(rows):
            row_bg: str | None = None
            row_fg: str | None = None
            cell_overrides: dict[int, tuple[str | None, str | None]] = {}

            for rule in self._rules:
                col_name = rule.get("column", "")
                try:
                    col_idx = self._column_names.index(col_name)
                except ValueError:
                    continue
                if col_idx >= len(row_data):
                    continue
                cell_val = str(row_data[col_idx]) if row_data[col_idx] is not None else ""
                if _rule_matches(cell_val, rule.get("op", ">"), str(rule.get("value", ""))):
                    bg = rule.get("bg_color") or None
                    fg = rule.get("fg_color") or None
                    if rule.get("target") == "row":
                        row_bg, row_fg = bg, fg
                    else:
                        cell_overrides[col_idx] = (bg, fg)

            for c, val in enumerate(row_data):
                item = QTableWidgetItem(str(val) if val is not None else "")
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

                # Determine effective colors for this cell
                if c in cell_overrides:
                    bg, fg = cell_overrides[c]
                else:
                    bg, fg = row_bg, row_fg

                if bg:
                    item.setBackground(QColor(bg))
                if fg:
                    item.setForeground(QColor(fg))

                self.setItem(r, c, item)
        self.setSortingEnabled(True)
        self.setUpdatesEnabled(True)


# ---------------------------------------------------------------------------
# Filter sidebar
# ---------------------------------------------------------------------------

class _CheckList(QWidget):
    """Scrollable group of checkboxes — replaces QListWidget multi-select."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checks: dict[str, QCheckBox] = {}  # value → checkbox

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMaximumHeight(115)
        self._scroll.setMinimumHeight(30)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget()
        self._inner = QVBoxLayout(self._container)
        self._inner.setContentsMargins(2, 2, 2, 2)
        self._inner.setSpacing(2)
        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll)

    def populate(self, items: list[tuple[str, str]]) -> None:
        """items: list of (display_label, filter_value)."""
        # Remove old widgets
        while self._inner.count():
            w = self._inner.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._checks.clear()

        for label, value in items:
            cb = QCheckBox(label)
            cb.setProperty("_fv", value)
            cb.stateChanged.connect(self.changed)
            self._inner.addWidget(cb)
            self._checks[value] = cb

    def get_selected(self) -> list[str]:
        return [v for v, cb in self._checks.items() if cb.isChecked()]

    def clear_all(self) -> None:
        for cb in self._checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self.changed.emit()

    def has_selection(self) -> bool:
        return any(cb.isChecked() for cb in self._checks.values())

    def set_valid(self, valid_vals: set) -> None:
        """Disable and uncheck items not in valid_vals (caller must block signals)."""
        for val, cb in self._checks.items():
            is_valid = val in valid_vals
            if not is_valid and cb.isChecked():
                cb.setChecked(False)
            cb.setEnabled(is_valid)


class FilterSidebar(QFrame):
    filters_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(215)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(10, 12, 10, 12)
        self._layout.setSpacing(8)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Debounce timer — only emit after 250 ms of inactivity
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._emit)

        # Stored filter_values DataFrame for dynamic cascade updates
        self._full_fv = None

        # Title
        title = SectionTitle("Filters")
        self._layout.addWidget(title)

        # Search box
        search_gb = self._make_group_box("Search")
        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("SKU or description…")
        self._sku_input.textChanged.connect(self._schedule_emit)
        search_gb.layout().addWidget(self._sku_input)

        # Checkbox filter groups
        self._cc_list, _ = self._make_check_group("Cost Center")
        self._sup_list, _ = self._make_check_group("Supplier")
        self._pc_list, _ = self._make_check_group("Price Class")
        self._pl_list, _ = self._make_check_group("Product Line")

        # SKU Rating (fixed 4 options — horizontal checkboxes)
        rating_gb = self._make_group_box("SKU Rating")
        self._rating_checks: dict[str, QCheckBox] = {}
        row_lay = QHBoxLayout()
        row_lay.setSpacing(6)
        for r in ("A", "B", "C", "D"):
            cb = QCheckBox(r)
            cb.setChecked(True)
            cb.stateChanged.connect(self._schedule_emit)
            row_lay.addWidget(cb)
            self._rating_checks[r] = cb
        row_lay.addStretch()
        rating_gb.layout().addLayout(row_lay)

        # Reset all button
        btn_reset = QPushButton("Reset Filters")
        btn_reset.setObjectName("flat")
        btn_reset.clicked.connect(self._reset)
        self._layout.addWidget(btn_reset)
        self._layout.addStretch()

        scroll.setWidget(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------

    def _make_group_box(self, title: str) -> QGroupBox:
        gb = QGroupBox(title)
        vl = QVBoxLayout(gb)
        vl.setContentsMargins(6, 4, 6, 6)
        vl.setSpacing(4)
        self._layout.addWidget(gb)
        return gb

    def _make_check_group(self, title: str) -> tuple:
        """Group box with a Clear link + _CheckList. Returns (checklist, groupbox)."""
        gb = QGroupBox(title)
        vl = QVBoxLayout(gb)
        vl.setContentsMargins(6, 2, 6, 6)
        vl.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("link_btn")
        clear_btn.setFixedHeight(16)
        clear_btn.setStyleSheet(
            f"color: {theme.get('accent')}; border: none; background: transparent; "
            f"font-size: 11px; padding: 0;"
        )
        hdr.addWidget(clear_btn)
        vl.addLayout(hdr)

        cl = _CheckList()
        cl.changed.connect(self._on_filter_changed)
        clear_btn.clicked.connect(cl.clear_all)
        vl.addWidget(cl)

        self._layout.addWidget(gb)
        return cl, gb

    def populate(self, filter_values) -> None:
        import pandas as pd
        if filter_values is None or (isinstance(filter_values, pd.DataFrame) and filter_values.empty):
            return

        # Save for cascade filtering
        self._full_fv = filter_values

        # Cost centers — exclude those starting with '1' (internal use only)
        cc_vals = sorted({
            str(v) for v in filter_values["cost_center"].dropna().unique()
            if v and not str(v).startswith("1")
        })
        self._cc_list.populate([(v, v) for v in cc_vals])

        # Suppliers — no code-prefix exclusion
        sup_vals = sorted({
            str(v) for v in filter_values["supplier_number"].dropna().unique() if v
        })
        self._sup_list.populate([(v, v) for v in sup_vals])

        # Price classes — "CODE — Description"
        pc_df = filter_values[["price_class", "price_class_desc"]].drop_duplicates()
        pc_items: list[tuple[str, str]] = []
        for _, row in pc_df.dropna(subset=["price_class"]).iterrows():
            pc = str(row["price_class"]).strip()
            desc = str(row.get("price_class_desc", "")).strip()
            label = f"{pc} — {desc}" if desc and desc not in ("", "nan") else pc
            if pc:
                pc_items.append((label, pc))
        pc_items.sort()
        self._pc_list.populate(pc_items)

        # Product lines — "CODE — Description"
        pl_df = filter_values[["product_line", "product_line_desc"]].drop_duplicates()
        pl_items: list[tuple[str, str]] = []
        for _, row in pl_df.dropna(subset=["product_line"]).iterrows():
            pl = str(row["product_line"]).strip()
            desc = str(row.get("product_line_desc", "")).strip()
            label = f"{pl} — {desc}" if desc and desc not in ("", "nan") else pl
            if pl:
                pl_items.append((label, pl))
        pl_items.sort()
        self._pl_list.populate(pl_items)

    def get_filters(self) -> dict:
        return {
            "sku_search":   self._sku_input.text().strip(),
            "cost_centers": self._cc_list.get_selected() or None,
            "suppliers":    self._sup_list.get_selected() or None,
            "price_classes": self._pc_list.get_selected() or None,
            "product_lines": self._pl_list.get_selected() or None,
            "sku_ratings":  [r for r, cb in self._rating_checks.items() if cb.isChecked()],
        }

    def _on_filter_changed(self) -> None:
        """Called when any checklist checkbox changes: cascade then debounce."""
        self._update_dependent_filters()
        self._timer.start()

    def _schedule_emit(self) -> None:
        """Debounce for the search box (no cascade needed)."""
        self._timer.start()

    def _emit(self) -> None:
        self.filters_changed.emit(self.get_filters())

    def _update_dependent_filters(self) -> None:
        """Narrow each filter group to only options compatible with other active selections."""
        import pandas as pd

        fv = self._full_fv
        if fv is None or not isinstance(fv, pd.DataFrame) or fv.empty:
            return

        cc_sel  = set(self._cc_list.get_selected())
        sup_sel = set(self._sup_list.get_selected())
        pc_sel  = set(self._pc_list.get_selected())
        pl_sel  = set(self._pl_list.get_selected())

        # Block all checkbox signals to prevent re-entrancy during update
        all_cbs = [
            cb
            for cl in (self._cc_list, self._sup_list, self._pc_list, self._pl_list)
            for cb in cl._checks.values()
        ]
        for cb in all_cbs:
            cb.blockSignals(True)

        try:
            if not any((cc_sel, sup_sel, pc_sel, pl_sel)):
                # Nothing selected — re-enable everything
                for cl in (self._cc_list, self._sup_list, self._pc_list, self._pl_list):
                    cl.set_valid(set(cl._checks.keys()))
            else:
                cc_valid  = self._compute_valid(
                    fv, "cost_center",
                    ("supplier_number", sup_sel), ("price_class", pc_sel), ("product_line", pl_sel))
                sup_valid = self._compute_valid(
                    fv, "supplier_number",
                    ("cost_center", cc_sel), ("price_class", pc_sel), ("product_line", pl_sel))
                pc_valid  = self._compute_valid(
                    fv, "price_class",
                    ("cost_center", cc_sel), ("supplier_number", sup_sel), ("product_line", pl_sel))
                pl_valid  = self._compute_valid(
                    fv, "product_line",
                    ("cost_center", cc_sel), ("supplier_number", sup_sel), ("price_class", pc_sel))

                self._cc_list.set_valid(cc_valid)
                self._sup_list.set_valid(sup_valid)
                self._pc_list.set_valid(pc_valid)
                self._pl_list.set_valid(pl_valid)
        finally:
            for cb in all_cbs:
                cb.blockSignals(False)

    def _compute_valid(self, fv, dim: str, *constraints) -> set:
        """Return valid values for ``dim`` after applying all other active selections."""
        import pandas as pd

        mask = pd.Series(True, index=fv.index)
        for col, sel in constraints:
            if sel and col in fv.columns:
                mask &= fv[col].isin(sel)
        return set(fv.loc[mask, dim].dropna().astype(str).str.strip())

    def _reset(self) -> None:
        """Clear all selections and re-enable every filter item."""
        self._sku_input.blockSignals(True)
        self._sku_input.clear()
        self._sku_input.blockSignals(False)

        all_cbs = [
            cb
            for cl in (self._cc_list, self._sup_list, self._pc_list, self._pl_list)
            for cb in cl._checks.values()
        ]
        for cb in all_cbs:
            cb.blockSignals(True)
        try:
            for cl in (self._cc_list, self._sup_list, self._pc_list, self._pl_list):
                for cb in cl._checks.values():
                    cb.setChecked(False)
                    cb.setEnabled(True)
        finally:
            for cb in all_cbs:
                cb.blockSignals(False)

        for cb in self._rating_checks.values():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)

        self._emit()


# ---------------------------------------------------------------------------
# Status badge
# ---------------------------------------------------------------------------

def make_badge(text: str, color_key: str = "accent") -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"background:{theme.get(color_key)}; color:#fff; border-radius:4px;"
        "padding: 2px 8px; font-size: 11px; font-weight: 600;"
    )
    return lbl


# ---------------------------------------------------------------------------
# Plotly chart widget (renders HTML in QWebEngineView if available,
# falls back to a placeholder label)
# ---------------------------------------------------------------------------

def make_chart_widget(fig=None, parent=None) -> QWidget:
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        view = QWebEngineView(parent)
        if fig is not None:
            html = fig.to_html(include_plotlyjs="cdn", full_html=True)
            view.setHtml(html)
        return view
    except ImportError:
        lbl = QLabel("Install PyQt6-WebEngine to display charts.", parent)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {theme.get('text_muted')};")
        return lbl


def update_chart_widget(widget: QWidget, fig) -> None:
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        if isinstance(widget, QWebEngineView):
            html = fig.to_html(include_plotlyjs="cdn", full_html=True)
            widget.setHtml(html)
    except ImportError:
        pass
