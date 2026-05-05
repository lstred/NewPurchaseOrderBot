"""
Settings tab — stock-turn targets at all filter levels.
Detects and resolves conflicts when multiple levels are configured for the same SKU.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from app.data.store import get_all_targets, set_target, get_all_launch_dates, set_launch_date
from app.ui.widgets import SectionTitle, HSep, DataTable
import app.ui.theme as theme

from datetime import date


class SettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_values: Optional[pd.DataFrame] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        root.addWidget(SectionTitle("Settings & Stock-Turn Targets"))
        root.addWidget(QLabel(
            "Set stock-turn targets at any level. More specific targets override broader ones. "
            "When multiple levels match a SKU, the app will notify you and let you choose which applies."
        ))
        root.addWidget(HSep())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setSpacing(16)
        cl.setContentsMargins(0, 0, 0, 0)

        # Global target
        gb_global = QGroupBox("Global Default")
        fl = QFormLayout(gb_global)
        self._global_spin = self._make_spin()
        fl.addRow("Global Target Turn:", self._global_spin)
        save_btn = QPushButton("Save Global")
        save_btn.clicked.connect(lambda: self._save_target("global", self._global_spin.value()))
        fl.addRow("", save_btn)
        cl.addWidget(gb_global)

        # Per-level target editor
        gb_level = QGroupBox("Add / Edit Level-Specific Target")
        fl2 = QFormLayout(gb_level)
        self._level_combo = QComboBox()
        self._level_combo.addItems(["Cost Center", "Price Class", "Product Line", "Supplier", "SKU"])
        fl2.addRow("Level:", self._level_combo)
        self._level_value_combo = QComboBox()
        self._level_value_combo.setEditable(True)
        fl2.addRow("Value:", self._level_value_combo)
        self._level_spin = self._make_spin()
        fl2.addRow("Target Turn:", self._level_spin)
        btn_add = QPushButton("Save Target")
        btn_add.clicked.connect(self._save_level_target)
        fl2.addRow("", btn_add)
        self._level_combo.currentTextChanged.connect(self._populate_level_values)
        cl.addWidget(gb_level)

        # Current targets table
        cl.addWidget(QLabel("All Configured Targets:"))
        self._targets_table = DataTable(["Key", "Level", "Value", "Target Turn", ""])
        self._targets_table.setMaximumHeight(240)
        cl.addWidget(self._targets_table)
        self._refresh_targets_table()

        cl.addWidget(HSep())

        # Launch dates override
        cl.addWidget(QLabel("Override Launch Dates (leave blank to use auto-detected date):"))
        self._launch_table = DataTable(["SKU", "Current Launch Date", "Override"])
        self._launch_table.setMaximumHeight(200)
        cl.addWidget(self._launch_table)

        cl.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll)

        # Load defaults
        self._load_global()

    # ------------------------------------------------------------------

    def refresh(self, filter_values: Optional[pd.DataFrame]) -> None:
        self._filter_values = filter_values
        self._populate_level_values(self._level_combo.currentText())
        self._refresh_launch_table()

    def _make_spin(self) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(0.1, 50.0)
        sp.setSingleStep(0.5)
        sp.setDecimals(1)
        sp.setValue(4.0)
        return sp

    def _load_global(self) -> None:
        targets = get_all_targets()
        self._global_spin.setValue(targets.get("global", 4.0))

    def _save_target(self, key: str, value: float) -> None:
        set_target(key, value)
        self._refresh_targets_table()

    def _save_level_target(self) -> None:
        level = self._level_combo.currentText()
        value = self._level_value_combo.currentText().strip()
        if not value:
            return
        prefix_map = {
            "Cost Center": "cc",
            "Price Class": "pc",
            "Product Line": "pl",
            "Supplier": "sup",
            "SKU": "sku",
        }
        prefix = prefix_map.get(level, "cc")
        key = f"{prefix}:{value}"
        self._save_target(key, self._level_spin.value())

    def _populate_level_values(self, level: str) -> None:
        self._level_value_combo.clear()
        if self._filter_values is None or self._filter_values.empty:
            return
        col_map = {
            "Cost Center": "cost_center",
            "Price Class": "price_class",
            "Product Line": "product_line",
            "Supplier": "supplier_number",
        }
        col = col_map.get(level)
        if col and col in self._filter_values.columns:
            vals = sorted(self._filter_values[col].dropna().unique().tolist())
            self._level_value_combo.addItems([str(v) for v in vals if v])

    def _refresh_targets_table(self) -> None:
        targets = get_all_targets()
        rows = []
        prefix_labels = {"global": "Global", "cc": "Cost Center", "pc": "Price Class",
                         "pl": "Product Line", "sup": "Supplier", "sku": "SKU"}
        for key, val in sorted(targets.items()):
            if ":" in key:
                prefix, value = key.split(":", 1)
                level_label = prefix_labels.get(prefix, prefix)
            else:
                level_label = "Global"
                value = "—"
            rows.append([key, level_label, value, f"{val:.1f}x", "Delete"])
        self._targets_table.populate(rows)

    def _refresh_launch_table(self) -> None:
        launch_dates = get_all_launch_dates()
        rows = []
        for sku, d in sorted(launch_dates.items()):
            rows.append([sku, str(d), ""])
        self._launch_table.populate(rows)
