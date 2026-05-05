"""
Reusable widget components shared across tabs.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QLineEdit, QCheckBox, QComboBox, QListWidget,
    QListWidgetItem, QScrollArea, QSplitter, QGroupBox,
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
        self.resizeRowsToContents()


# ---------------------------------------------------------------------------
# Filter sidebar
# ---------------------------------------------------------------------------

class FilterSidebar(QFrame):
    filters_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(12)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = SectionTitle("Filters")
        self._layout.addWidget(title)

        # SKU search
        self._sku_search = self._make_group("Search")
        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("SKU or description…")
        self._sku_input.textChanged.connect(self._emit)
        self._sku_search.layout().addWidget(self._sku_input)

        # Cost center
        self._cc_group = self._make_group("Cost Center")
        self._cc_list = self._make_list()
        self._cc_group.layout().addWidget(self._cc_list)

        # Supplier
        self._sup_group = self._make_group("Supplier")
        self._sup_list = self._make_list()
        self._sup_group.layout().addWidget(self._sup_list)

        # Price class
        self._pc_group = self._make_group("Price Class")
        self._pc_list = self._make_list()
        self._pc_group.layout().addWidget(self._pc_list)

        # Product line
        self._pl_group = self._make_group("Product Line")
        self._pl_list = self._make_list()
        self._pl_group.layout().addWidget(self._pl_list)

        # SKU rating
        self._rating_group = self._make_group("SKU Rating")
        self._rating_checks: dict[str, QCheckBox] = {}
        for r in ("A", "B", "C", "D"):
            cb = QCheckBox(f"Rating {r}")
            cb.setChecked(True)
            cb.stateChanged.connect(self._emit)
            self._rating_group.layout().addWidget(cb)
            self._rating_checks[r] = cb

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

    def _make_group(self, title: str) -> QGroupBox:
        gb = QGroupBox(title)
        vl = QVBoxLayout(gb)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(4)
        self._layout.addWidget(gb)
        return gb

    def _make_list(self) -> QListWidget:
        lw = QListWidget()
        lw.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        lw.setMaximumHeight(120)
        lw.itemSelectionChanged.connect(self._emit)
        return lw

    def populate(self, filter_values) -> None:
        import pandas as pd
        if filter_values is None or (isinstance(filter_values, pd.DataFrame) and filter_values.empty):
            return
        self._populate_list(self._cc_list, sorted(filter_values["cost_center"].dropna().unique().tolist()))
        self._populate_list(self._sup_list, sorted(filter_values["supplier_number"].dropna().unique().tolist()))
        # Price class — show description + code
        pc_items = []
        for _, row in filter_values[["price_class", "price_class_desc"]].drop_duplicates().dropna().iterrows():
            label = f"{row['price_class']} — {row['price_class_desc']}" if row["price_class_desc"] else row["price_class"]
            pc_items.append((label, row["price_class"]))
        pc_items.sort()
        self._pc_list.clear()
        for label, code in pc_items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self._pc_list.addItem(item)

        pl_items = []
        for _, row in filter_values[["product_line", "product_line_desc"]].drop_duplicates().dropna().iterrows():
            label = f"{row['product_line']} — {row['product_line_desc']}" if row["product_line_desc"] else row["product_line"]
            pl_items.append((label, row["product_line"]))
        pl_items.sort()
        self._pl_list.clear()
        for label, code in pl_items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self._pl_list.addItem(item)

    def _populate_list(self, lw: QListWidget, values: list) -> None:
        lw.clear()
        for v in values:
            if v and not str(v).startswith("1"):
                item = QListWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, str(v))
                lw.addItem(item)

    def get_filters(self) -> dict:
        def selected(lw: QListWidget) -> list:
            return [lw.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lw.count()) if lw.item(i).isSelected()]

        return {
            "sku_search": self._sku_input.text().strip(),
            "cost_centers": selected(self._cc_list) or None,
            "suppliers": selected(self._sup_list) or None,
            "price_classes": selected(self._pc_list) or None,
            "product_lines": selected(self._pl_list) or None,
            "sku_ratings": [r for r, cb in self._rating_checks.items() if cb.isChecked()],
        }

    def _emit(self) -> None:
        self.filters_changed.emit(self.get_filters())

    def _reset(self) -> None:
        self._sku_input.clear()
        for lw in (self._cc_list, self._sup_list, self._pc_list, self._pl_list):
            lw.clearSelection()
        for cb in self._rating_checks.values():
            cb.setChecked(True)
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
