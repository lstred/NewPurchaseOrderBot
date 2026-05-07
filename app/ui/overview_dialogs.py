"""
Dialogs for the Overview tab:
  - ColumnManagerDialog   — show/hide table columns
  - ThresholdRulesDialog  — manage color-threshold rules list
  - AddEditRuleDialog     — create or edit a single rule (supports multi-condition AND logic)
  - _ConditionRow         — one condition row widget inside AddEditRuleDialog
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
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

    _HEADERS = ["Conditions", "Apply to", "Background", "Text Color"]

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
            "Highlight rows or cells when conditions are met. "
            "Each rule can have multiple conditions — all must match simultaneously. "
            "Rules are applied top-to-bottom; later rules override earlier ones."
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

            # Conditions summary — supports new {conditions:[...]} and legacy flat format
            conditions = rule.get("conditions") or [
                {
                    "column": rule.get("column", ""),
                    "op":     rule.get("op", ""),
                    "value":  str(rule.get("value", "")),
                }
            ]
            cond_str = "  AND  ".join(
                f"{c.get('column', '')} {c.get('op', '')} {c.get('value', '')}"
                for c in conditions
            )
            self._rule_table.setItem(r, 0, QTableWidgetItem(cond_str))

            # Apply-to column
            target = rule.get("target", "row").lower()
            apply_col = rule.get("apply_column", "")
            if target == "cell" and apply_col:
                target_str = f"Cell \u2192 {apply_col}"
            else:
                target_str = target.title()
            self._rule_table.setItem(r, 1, QTableWidgetItem(target_str))

            bg = rule.get("bg_color", "")
            fg = rule.get("fg_color", "")

            bg_item = QTableWidgetItem(bg or "\u2014")
            if bg:
                bg_item.setBackground(QColor(bg))
                bg_item.setForeground(QColor("#ffffff" if _is_dark(bg) else "#000000"))

            fg_item = QTableWidgetItem(fg or "\u2014")
            if fg:
                fg_item.setBackground(QColor(fg))
                fg_item.setForeground(QColor("#ffffff" if _is_dark(fg) else "#000000"))

            self._rule_table.setItem(r, 2, bg_item)
            self._rule_table.setItem(r, 3, fg_item)

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
# Single condition row widget
# ---------------------------------------------------------------------------

class _ConditionRow(QWidget):
    """One condition row: [Column ▼] [Operator ▼] [Value ──────────] [×]"""

    removed = pyqtSignal(object)   # emits self
    _OPS = [">"  , ">=", "<", "<=", "=", "!=", "contains"]

    def __init__(self, columns: list[str], cond: dict | None = None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(6)

        self._col_combo = QComboBox()
        self._col_combo.addItems(columns)
        self._col_combo.setMinimumWidth(150)
        if cond and cond.get("column") in columns:
            self._col_combo.setCurrentText(cond["column"])
        row.addWidget(self._col_combo)

        self._op_combo = QComboBox()
        self._op_combo.addItems(self._OPS)
        self._op_combo.setFixedWidth(80)
        if cond and cond.get("op") in self._OPS:
            self._op_combo.setCurrentText(cond["op"])
        row.addWidget(self._op_combo)

        self._val_edit = QLineEdit(str(cond["value"]) if cond and "value" in cond else "")
        self._val_edit.setPlaceholderText("value…")
        row.addWidget(self._val_edit, 1)   # stretches to fill remaining space

        btn_rm = QPushButton("×")
        btn_rm.setObjectName("flat")
        btn_rm.setFixedSize(26, 26)
        btn_rm.setToolTip("Remove this condition")
        btn_rm.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(btn_rm)

    def get_condition(self) -> dict:
        return {
            "column": self._col_combo.currentText(),
            "op":     self._op_combo.currentText(),
            "value":  self._val_edit.text().strip(),
        }


# ---------------------------------------------------------------------------
# Single-rule add / edit dialog
# ---------------------------------------------------------------------------

class AddEditRuleDialog(QDialog):
    """Create or edit a color rule with one or more AND conditions."""

    def __init__(self, columns: list[str], rule: Optional[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Rule" if rule is None else "Edit Rule")
        self.setMinimumWidth(580)
        self._rule: dict = dict(rule) if rule else {}
        self._bg_color: str = self._rule.get("bg_color", "")
        self._fg_color: str = self._rule.get("fg_color", "")
        self._columns = columns
        self._condition_rows: list[_ConditionRow] = []

        root = QVBoxLayout(self)
        root.setSpacing(14)
        root.setContentsMargins(24, 20, 24, 20)

        # ── Conditions section ─────────────────────────────────────────────
        cond_hdr = QLabel("Conditions \u2014 ALL must match simultaneously:")
        cond_hdr.setStyleSheet(f"font-weight: 600; color: {theme.get('text')};")
        root.addWidget(cond_hdr)

        self._cond_container = QWidget()
        self._cond_layout = QVBoxLayout(self._cond_container)
        self._cond_layout.setContentsMargins(0, 0, 0, 0)
        self._cond_layout.setSpacing(4)
        root.addWidget(self._cond_container)

        # Load existing conditions (backwards-compatible with legacy flat format)
        existing = self._rule.get("conditions") or []
        if not existing:
            col = self._rule.get("column", "")
            if col:
                existing = [{
                    "column": col,
                    "op":     self._rule.get("op", ">"),
                    "value":  str(self._rule.get("value", "")),
                }]
        for cond in existing:
            self._add_condition_row(cond)
        if not existing:
            self._add_condition_row(None)   # start with one blank row

        add_btn = QPushButton("\uff0b  Add Condition")
        add_btn.setObjectName("flat")
        add_btn.clicked.connect(lambda: self._add_condition_row(None))
        row_add = QHBoxLayout()
        row_add.addWidget(add_btn)
        row_add.addStretch()
        root.addLayout(row_add)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {theme.get('border')};")
        root.addWidget(sep)

        # ── Target / highlight settings ────────────────────────────────────
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        root.addLayout(form)

        self._target_combo = QComboBox()
        self._target_combo.addItems(["Row", "Cell"])
        if self._rule.get("target") == "cell":
            self._target_combo.setCurrentIndex(1)
        form.addRow("Apply to:", self._target_combo)

        self._apply_col_lbl = QLabel("Highlight Column:")
        self._apply_col_combo = QComboBox()
        self._apply_col_combo.addItem("\u2014 (auto: 1st condition\u2019s column)", "")
        for c in columns:
            self._apply_col_combo.addItem(c, c)
        existing_apply = self._rule.get("apply_column", "")
        if existing_apply:
            idx = self._apply_col_combo.findData(existing_apply)
            if idx >= 0:
                self._apply_col_combo.setCurrentIndex(idx)
        form.addRow(self._apply_col_lbl, self._apply_col_combo)

        self._bg_preview, bg_widget = self._make_color_row("bg")
        form.addRow("Background:", bg_widget)

        self._fg_preview, fg_widget = self._make_color_row("fg")
        form.addRow("Text Color:", fg_widget)

        hint = QLabel(
            "Tip: numeric fields compare as numbers; text fields compare as strings.  "
            "All conditions must be true simultaneously to trigger the rule."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.get('text_muted')}; font-size: 11px;")
        root.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._target_combo.currentTextChanged.connect(self._on_target_changed)
        self._on_target_changed(self._target_combo.currentText())

    # ------------------------------------------------------------------
    # Condition row management
    # ------------------------------------------------------------------

    def _add_condition_row(self, cond: dict | None) -> None:
        row_widget = _ConditionRow(self._columns, cond, self)
        row_widget.removed.connect(self._remove_condition_row)
        self._condition_rows.append(row_widget)
        self._cond_layout.addWidget(row_widget)

    def _remove_condition_row(self, row: "_ConditionRow") -> None:
        if len(self._condition_rows) <= 1:
            return   # always keep at least one condition
        self._condition_rows.remove(row)
        self._cond_layout.removeWidget(row)
        row.deleteLater()

    # ------------------------------------------------------------------
    # Color pickers
    # ------------------------------------------------------------------

    def _make_color_row(self, which: str) -> tuple[QLabel, QWidget]:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        preview = QLabel()
        preview.setFixedSize(28, 28)
        row.addWidget(preview)

        btn = QPushButton("Choose\u2026")
        btn.clicked.connect(lambda: self._pick_color(which))
        row.addWidget(btn)

        clear = QPushButton("Clear")
        clear.setObjectName("flat")
        clear.clicked.connect(lambda: self._clear_color(which))
        row.addWidget(clear)

        row.addStretch()

        self._update_preview(which, preview)
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
            self._update_preview(which, preview)

    def _clear_color(self, which: str) -> None:
        if which == "bg":
            self._bg_color = ""
            self._update_preview("bg", self._bg_preview)
        else:
            self._fg_color = ""
            self._update_preview("fg", self._fg_preview)

    def _update_preview(self, which: str, preview: QLabel) -> None:
        color = self._bg_color if which == "bg" else self._fg_color
        if color:
            preview.setStyleSheet(
                f"background: {color}; border: 1px solid #888; border-radius: 4px;"
            )
        else:
            preview.setStyleSheet(
                "background: transparent; border: 1px dashed #888; border-radius: 4px;"
            )

    # ------------------------------------------------------------------
    # Target toggle
    # ------------------------------------------------------------------

    def _on_target_changed(self, text: str) -> None:
        is_cell = text.lower() == "cell"
        self._apply_col_lbl.setVisible(is_cell)
        self._apply_col_combo.setVisible(is_cell)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def get_rule(self) -> dict:
        conditions = [
            row.get_condition()
            for row in self._condition_rows
            if row.get_condition()["column"]
        ]
        if not conditions:
            conditions = [{"column": "", "op": ">", "value": ""}]
        apply_col = ""
        if self._target_combo.currentText().lower() == "cell":
            apply_col = self._apply_col_combo.currentData() or ""
        return {
            "conditions":   conditions,
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
        return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) < 128
    except Exception:
        return True
