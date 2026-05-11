"""
Settings tab — stock-turn targets at all filter levels.
Detects and resolves conflicts when multiple levels are configured for the same SKU.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, 
    QPushButton, QScrollArea, QVBoxLayout, QWidget, QFrame,
)

from app.data.store import get_all_targets, set_target, delete_target, get_all_launch_dates, get_ai_config, set_ai_config
from app.ui.widgets import SectionTitle, HSep, DataTable
import app.ui.theme as theme

from PyQt6.QtWidgets import QLineEdit


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
        self._targets_table = DataTable(["Key", "Level", "Value", "Target Turn"])
        self._targets_table.setMaximumHeight(220)
        cl.addWidget(self._targets_table)

        del_btn = QPushButton("Delete Selected Target")
        del_btn.setObjectName("danger")
        del_btn.setFixedWidth(200)
        del_btn.clicked.connect(self._delete_selected_target)
        cl.addWidget(del_btn)
        self._refresh_targets_table()

        cl.addWidget(HSep())

        # ------------- AI Provider -------------
        gb_ai = QGroupBox("AI Provider (for the AI tab)")
        ai_form = QFormLayout(gb_ai)
        self._ai_provider = QComboBox()
        self._ai_provider.addItems(["anthropic", "google", "openai"])
        ai_form.addRow("Provider:", self._ai_provider)
        self._ai_model = QComboBox()
        self._ai_model.setEditable(True)
        ai_form.addRow("Model:", self._ai_model)
        self._ai_key = QLineEdit()
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setPlaceholderText("Paste API key (stored locally in %APPDATA%\\PurchaseOrderBot\\ai_config.json)")
        ai_form.addRow("API Key:", self._ai_key)
        save_ai_btn = QPushButton("Save AI Settings")
        save_ai_btn.clicked.connect(self._save_ai_config)
        ai_form.addRow("", save_ai_btn)
        self._ai_save_status = QLabel("")
        self._ai_save_status.setStyleSheet(f"color:{theme.get('success')}; font-weight:600;")
        ai_form.addRow("", self._ai_save_status)
        ai_info = QLabel(
            "<b>Suggested models &amp; costs (per 1M tokens):</b><br>"
            "&nbsp;&nbsp;• <b>Anthropic</b> — claude-sonnet-4-5 ($3 in / $15 out) — best at SQL · "
            "<a href='https://console.anthropic.com'>console.anthropic.com</a><br>"
            "&nbsp;&nbsp;• <b>Google</b> — gemini-2.5-flash ($0.30 in / $2.50 out) — cheapest, very good · "
            "<a href='https://aistudio.google.com'>aistudio.google.com</a><br>"
            "&nbsp;&nbsp;• <b>OpenAI</b> — gpt-4o-mini ($0.15 in / $0.60 out) — cheapest of all · "
            "<a href='https://platform.openai.com'>platform.openai.com</a>"
        )
        ai_info.setOpenExternalLinks(True)
        ai_info.setWordWrap(True)
        ai_form.addRow(ai_info)
        cl.addWidget(gb_ai)
        # Wire provider → model auto-list (BEFORE first load so saved value sticks)
        self._ai_provider.currentTextChanged.connect(self._on_provider_changed)
        self._on_provider_changed(self._ai_provider.currentText())
        self._load_ai_config()

        cl.addWidget(HSep())

        # Launch dates override
        cl.addWidget(QLabel("Override Launch Dates (leave blank to use auto-detected date):"))
        self._launch_table = DataTable(["SKU", "Auto-Detected Launch Date"])
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

    def _load_ai_config(self) -> None:
        cfg = get_ai_config()
        provider = cfg.get("provider", "anthropic")
        idx = self._ai_provider.findText(provider)
        if idx >= 0:
            self._ai_provider.setCurrentIndex(idx)
        # _on_provider_changed already populated model list; now apply saved model if any
        saved_model = cfg.get("model", "").strip()
        if saved_model:
            self._ai_model.setCurrentText(saved_model)
        self._ai_key.setText(cfg.get("api_key", ""))

    def _on_provider_changed(self, provider: str) -> None:
        """Repopulate model dropdown with known models for the selected provider."""
        models = {
            "anthropic": ["claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5"],
            "google":    ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
            "openai":    ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        }.get(provider.lower(), [])
        current = self._ai_model.currentText().strip()
        self._ai_model.blockSignals(True)
        self._ai_model.clear()
        self._ai_model.addItems(models)
        # Pick the first (recommended) entry by default; preserve user's typed value if it was non-empty
        if current and current not in models:
            self._ai_model.setCurrentText(current)
        elif models:
            self._ai_model.setCurrentIndex(0)
        self._ai_model.blockSignals(False)

    def _save_ai_config(self) -> None:
        provider = self._ai_provider.currentText().strip()
        model = self._ai_model.currentText().strip()
        api_key = self._ai_key.text().strip()
        set_ai_config({"provider": provider, "model": model, "api_key": api_key})
        if not api_key:
            self._ai_save_status.setStyleSheet(f"color:{theme.get('warning')}; font-weight:600;")
            self._ai_save_status.setText("⚠  Saved — but no API key was entered. Add one to use the AI tab.")
        else:
            self._ai_save_status.setStyleSheet(f"color:{theme.get('success')}; font-weight:600;")
            self._ai_save_status.setText(f"✓  Saved.  Provider: {provider}   Model: {model or '(default)'}")
        # Auto-clear the status after 6 seconds
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(6000, lambda: self._ai_save_status.setText(""))

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
            rows.append([key, level_label, value, f"{val:.1f}x"])
        self._targets_table.populate(rows)

    def _delete_selected_target(self) -> None:
        selected = self._targets_table.selectedItems()
        if not selected:
            return
        row = self._targets_table.currentRow()
        key_item = self._targets_table.item(row, 0)
        if key_item:
            key = key_item.text()
            delete_target(key)
            self._refresh_targets_table()

    def _refresh_launch_table(self) -> None:
        launch_dates = get_all_launch_dates()
        rows = [[sku, str(d)] for sku, d in sorted(launch_dates.items())]
        self._launch_table.populate(rows)
