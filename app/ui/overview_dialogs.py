"""
Dialogs for the Overview tab:
  - ColumnManagerDialog   — show/hide table columns
  - ThresholdRulesDialog  — manage color-threshold rules list
  - AddEditRuleDialog     — create or edit a single rule
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import app.ui.theme as theme


# ---------------------------------------------------------------------------
# Column visibility manager
# ---------------------------------------------------------------------------

class ColumnManagerDialog(QDialog):
    """Show/hide columns in the overview DataTable.

    The user can also drag column headers directly in the table to reorder them.
    """

    def __init__(self, columns: list[str], table, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Columns")
        self.setMinimumWidth(340)
        self.setMinimumHeight(440)
        self._columns = columns
        self._table = table

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        hint = QLabel(
            "Uncheck columns to hide them. Drag column headers directly in the "
            "table to reorder."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        layout.addWidget(hint)

        # Scroll area — one checkbox per column
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.setSpacing(6)
        vl.setContentsMargins(4, 4, 4, 4)

        self._checks: dict[str, QCheckBox] = {}
        for col in columns:
            cb = QCheckBox(col)
            col_idx = columns.index(col)
            cb.setChecked(not table.isColumnHidden(col_idx))
            vl.addWidget(cb)
            self._checks[col] = cb

        vl.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # Quick-select helpers
        helper_row = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_all.setObjectName("flat")
        btn_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self._checks.values()])
        btn_none = QPushButton("Deselect All")
        btn_none.setObjectName("flat")
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb in self._checks.values()])
        helper_row.addWidget(btn_all)
        helper_row.addWidget(btn_none)
        helper_row.addStretch()
        layout.addLayout(helper_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_prefs(self) -> dict[str, bool]:
        return {col: cb.isChecked() for col, cb in self._checks.items()}


# ---------------------------------------------------------------------------
# Threshold / color-rule manager
# ---------------------------------------------------------------------------

class ThresholdRulesDialog(QDialog):
    """Manage the full list of color-threshold rules for the overview table."""

    _HEADERS = ["Column", "Operator", "Value", "Apply to", "Background", "Text Color"]

    def __init__(self, columns: list[str], rules: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Color Rules")
        self.setMinimumSize(700, 440)
        self._columns = columns
        self._rules: list[dict] = list(rules)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        hint = QLabel(
            "Highlight rows or cells when a column value meets a condition. "
            "Rules are applied top-to-bottom; later rules override earlier ones for the same row."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 12px;")
        layout.addWidget(hint)

        self._rule_table = QTableWidget(0, len(self._HEADERS))
        self._rule_table.setHorizontalHeaderLabels(self._HEADERS)
        self._rule_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._rule_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._rule_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rule_table.setAlternatingRowColors(True)
        self._rule_table.verticalHeader().setVisible(False)
        layout.addWidget(self._rule_table)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        for label, slot in [
            ("Add Rule", self._add_rule),
            ("Edit Rule", self._edit_rule),
            ("Delete Rule", self._delete_rule),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_list()

    def _refresh_list(self) -> None:
        self._rule_table.setRowCount(0)
        for rule in self._rules:
            r = self._rule_table.rowCount()
            self._rule_table.insertRow(r)
            self._rule_table.setItem(r, 0, QTableWidgetItem(rule.get("column", "")))
            self._rule_table.setItem(r, 1, QTableWidgetItem(rule.get("op", "")))
            self._rule_table.setItem(r, 2, QTableWidgetItem(str(rule.get("value", ""))))

            # "Apply to" shows row/cell plus the target column when cross-column is used
            target = rule.get("target", "row").lower()
            apply_col = rule.get("apply_column", "")
            if target == "cell" and apply_col:
                target_str = f"Cell → {apply_col}"
            else:
                target_str = target.title()
            self._rule_table.setItem(r, 3, QTableWidgetItem(target_str))

            bg = rule.get("bg_color", "")
            fg = rule.get("fg_color", "")

            bg_item = QTableWidgetItem(bg or "—")
            if bg:
                bg_item.setBackground(QColor(bg))
                bg_item.setForeground(QColor("#ffffff" if _is_dark(bg) else "#000000"))

            fg_item = QTableWidgetItem(fg or "—")
            if fg:
                fg_item.setBackground(QColor(fg))
                fg_item.setForeground(QColor("#ffffff" if _is_dark(fg) else "#000000"))

            self._rule_table.setItem(r, 4, bg_item)
            self._rule_table.setItem(r, 5, fg_item)

    def _add_rule(self) -> None:
        dlg = AddEditRuleDialog(self._columns, None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._rules.append(dlg.get_rule())
            self._refresh_list()

    def _edit_rule(self) -> None:
        row = self._rule_table.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        dlg = AddEditRuleDialog(self._columns, self._rules[row], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._rules[row] = dlg.get_rule()
            self._refresh_list()

    def _delete_rule(self) -> None:
        row = self._rule_table.currentRow()
        if 0 <= row < len(self._rules):
            self._rules.pop(row)
            self._refresh_list()

    def get_rules(self) -> list[dict]:
        return list(self._rules)


# ---------------------------------------------------------------------------
# Single-rule add / edit dialog
# ---------------------------------------------------------------------------

class AddEditRuleDialog(QDialog):
    """Create or edit a single color-threshold rule."""

    _OPS = [">", ">=", "<", "<=", "=", "!=", "contains"]

    def __init__(self, columns: list[str], rule: Optional[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Rule" if rule is None else "Edit Rule")
        self.setMinimumWidth(460)
        self._rule: dict = dict(rule) if rule else {}
        self._bg_color: str = self._rule.get("bg_color", "")
        self._fg_color: str = self._rule.get("fg_color", "")
        self._columns = columns

        layout = QFormLayout(self)
        self._form = layout
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Evaluate column
        self._col_combo = QComboBox()
        self._col_combo.addItems(columns)
        if self._rule.get("column") in columns:
            self._col_combo.setCurrentText(self._rule["column"])
        layout.addRow("Evaluate Column:", self._col_combo)

        # Operator
        self._op_combo = QComboBox()
        self._op_combo.addItems(self._OPS)
        if self._rule.get("op") in self._OPS:
            self._op_combo.setCurrentText(self._rule["op"])
        layout.addRow("Operator:", self._op_combo)

        # Value
        self._val_edit = QLineEdit(str(self._rule.get("value", "")))
        self._val_edit.setPlaceholderText("e.g. 90  or  Yes  or  No")
        layout.addRow("Value:", self._val_edit)

        # Apply to
        self._target_combo = QComboBox()
        self._target_combo.addItems(["Row", "Cell"])
        if self._rule.get("target") == "cell":
            self._target_combo.setCurrentIndex(1)
        layout.addRow("Apply to:", self._target_combo)

        # Apply Column (only visible when target = Cell)
        self._apply_col_lbl = QLabel("Highlight Column:")
        self._apply_col_combo = QComboBox()
        self._apply_col_combo.addItem("— (same as evaluate column)", "")
        for c in columns:
            self._apply_col_combo.addItem(c, c)
        existing_apply = self._rule.get("apply_column", "")
        if existing_apply:
            idx = self._apply_col_combo.findData(existing_apply)
            if idx >= 0:
                self._apply_col_combo.setCurrentIndex(idx)
        layout.addRow(self._apply_col_lbl, self._apply_col_combo)

        # Background color
        self._bg_preview, bg_widget = self._make_color_row("bg")
        layout.addRow("Background:", bg_widget)

        # Text color
        self._fg_preview, fg_widget = self._make_color_row("fg")
        layout.addRow("Text Color:", fg_widget)

        hint = QLabel(
            "Tip: numeric fields compare as numbers; text fields compare as strings.\n"
            "Use \u2018Highlight Column\u2019 to color a different column based on the evaluated condition."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 11px;")
        layout.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # Wire target toggle
        self._target_combo.currentTextChanged.connect(self._on_target_changed)
        self._on_target_changed(self._target_combo.currentText())

    def _make_color_row(self, which: str) -> tuple[QLabel, QWidget]:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        preview = QLabel()
        preview.setFixedSize(28, 28)
        row.addWidget(preview)

        btn = QPushButton("Choose…")
        btn.clicked.connect(lambda: self._pick_color(which))
        row.addWidget(btn)

        clear = QPushButton("Clear")
        clear.setObjectName("flat")
        clear.clicked.connect(lambda: self._clear_color(which))
        row.addWidget(clear)

        row.addStretch()

        self._update_preview_widget(which, preview)
        return preview, w

    def _pick_color(self, which: str) -> None:
        current = self._bg_color if which == "bg" else self._fg_color
        initial = QColor(current) if current else QColor(255, 255, 255)
        color = QColorDialog.getColor(initial, self, "Choose Color")
        if color.isValid():
            if which == "bg":
                self._bg_color = color.name()
            else:
                self._fg_color = color.name()
            preview = self._bg_preview if which == "bg" else self._fg_preview
            self._update_preview_widget(which, preview)

    def _clear_color(self, which: str) -> None:
        if which == "bg":
            self._bg_color = ""
            self._update_preview_widget("bg", self._bg_preview)
        else:
            self._fg_color = ""
            self._update_preview_widget("fg", self._fg_preview)

    @staticmethod
    def _update_preview_widget(which: str, preview: QLabel) -> None:
        # We can't read the color value here easily without a closure,
        # so callers pass it directly. This method is only used as a static helper.
        pass

    def _update_preview_widget(self, which: str, preview: QLabel) -> None:  # noqa: F811
        color = self._bg_color if which == "bg" else self._fg_color
        if color:
            preview.setStyleSheet(
                f"background: {color}; border: 1px solid #888; border-radius: 4px;"
            )
        else:
            preview.setStyleSheet(
                "background: transparent; border: 1px dashed #888; border-radius: 4px;"
            )

    def _on_target_changed(self, text: str) -> None:
        """Show/hide the 'Highlight Column' row depending on whether target is Cell."""
        is_cell = text.lower() == "cell"
        self._apply_col_lbl.setVisible(is_cell)
        self._apply_col_combo.setVisible(is_cell)

    def get_rule(self) -> dict:
        apply_col = ""
        if self._target_combo.currentText().lower() == "cell":
            apply_col = self._apply_col_combo.currentData() or ""
        return {
            "column":       self._col_combo.currentText(),
            "op":           self._op_combo.currentText(),
            "value":        self._val_edit.text().strip(),
            "target":       self._target_combo.currentText().lower(),
            "apply_column": apply_col,
            "bg_color":     self._bg_color,
            "fg_color":     self._fg_color,
        }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _is_dark(hex_color: str) -> bool:
    """Return True if hex_color has low perceived brightness (better for white text)."""
    try:
        c = QColor(hex_color)
        # Standard relative luminance approximation
        return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) < 128
    except Exception:
        return True
