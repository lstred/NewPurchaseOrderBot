"""
Centralized style/theme definitions.
Supports dark and light modes.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette, QFont
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# ---------------------------------------------------------------------------
# Colour palettes
# ---------------------------------------------------------------------------

DARK = {
    "bg": "#1e2127",
    "bg_card": "#272b33",
    "bg_sidebar": "#1a1d23",
    "bg_alt": "#2c313a",
    "border": "#3a3f4b",
    "text": "#e6eaf0",
    "text_muted": "#8b909a",
    "accent": "#4e8cff",
    "accent_hover": "#6ba0ff",
    "success": "#3ecf8e",
    "warning": "#f0a500",
    "danger": "#e05260",
    "info": "#56b6d9",
    "chart_bg": "#1e2127",
}

LIGHT = {
    "bg": "#f4f6fa",
    "bg_card": "#ffffff",
    "bg_sidebar": "#e8ecf3",
    "bg_alt": "#edf0f7",
    "border": "#d0d5e0",
    "text": "#1a1d23",
    "text_muted": "#6b717f",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "success": "#16a34a",
    "warning": "#d97706",
    "danger": "#dc2626",
    "info": "#0891b2",
    "chart_bg": "#f4f6fa",
}

_current: dict = DARK
_is_dark: bool = True


def is_dark() -> bool:
    return _is_dark


def get(key: str) -> str:
    return _current.get(key, "#ffffff")


def toggle(app: QApplication) -> bool:
    global _current, _is_dark
    _is_dark = not _is_dark
    _current = DARK if _is_dark else LIGHT
    apply_to_app(app)
    return _is_dark


def apply_to_app(app: QApplication) -> None:
    c = _current
    qss = f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-family: "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{
        background-color: {c['bg']};
    }}
    QTabWidget::pane {{
        border: 1px solid {c['border']};
        background: {c['bg']};
    }}
    QTabBar::tab {{
        background: {c['bg_alt']};
        color: {c['text_muted']};
        padding: 8px 20px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        border: 1px solid {c['border']};
        border-bottom: none;
        margin-right: 2px;
        min-width: 120px;
    }}
    QTabBar::tab:selected {{
        background: {c['bg_card']};
        color: {c['text']};
        border-bottom: 2px solid {c['accent']};
    }}
    QTabBar::tab:hover {{
        background: {c['bg_card']};
        color: {c['text']};
    }}
    QPushButton {{
        background-color: {c['accent']};
        color: #ffffff;
        border: none;
        border-radius: 4px;
        padding: 6px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {c['accent_hover']};
    }}
    QPushButton:pressed {{
        background-color: {c['bg_alt']};
        color: {c['text']};
    }}
    QPushButton#flat {{
        background-color: transparent;
        color: {c['accent']};
        border: 1px solid {c['accent']};
    }}
    QPushButton#flat:hover {{
        background-color: {c['accent']};
        color: #ffffff;
    }}
    QPushButton#danger {{
        background-color: {c['danger']};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
        background-color: {c['bg_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 4px;
        padding: 5px 8px;
        selection-background-color: {c['accent']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {c['bg_card']};
        color: {c['text']};
        selection-background-color: {c['accent']};
        border: 1px solid {c['border']};
    }}
    QTableWidget, QTableView {{
        background-color: {c['bg_card']};
        alternate-background-color: {c['bg_alt']};
        gridline-color: {c['border']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 4px;
        selection-background-color: {c['accent']};
    }}
    QHeaderView::section {{
        background-color: {c['bg_alt']};
        color: {c['text']};
        border: none;
        border-bottom: 2px solid {c['border']};
        padding: 6px 8px;
        font-weight: 600;
    }}
    QScrollBar:vertical {{
        background: {c['bg_alt']};
        width: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']};
        border-radius: 4px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: {c['bg_alt']};
        height: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {c['border']};
        border-radius: 4px;
        min-width: 20px;
    }}
    QLabel#section_title {{
        font-size: 15px;
        font-weight: 700;
        color: {c['text']};
    }}
    QLabel#kpi_value {{
        font-size: 28px;
        font-weight: 700;
        color: {c['accent']};
    }}
    QLabel#kpi_label {{
        font-size: 11px;
        color: {c['text_muted']};
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    QFrame#card {{
        background-color: {c['bg_card']};
        border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QFrame#sidebar {{
        background-color: {c['bg_sidebar']};
        border-right: 1px solid {c['border']};
    }}
    QFrame#alert_card {{
        border-left: 4px solid {c['danger']};
        background-color: {c['bg_card']};
        border-radius: 4px;
    }}
    QFrame#alert_card_warn {{
        border-left: 4px solid {c['warning']};
        background-color: {c['bg_card']};
        border-radius: 4px;
    }}
    QCheckBox {{
        color: {c['text']};
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 3px;
        border: 1px solid {c['border']};
        background: {c['bg_alt']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    QSplitter::handle {{
        background: {c['border']};
        width: 1px;
    }}
    QGroupBox {{
        border: 1px solid {c['border']};
        border-radius: 6px;
        margin-top: 12px;
        padding-top: 8px;
        font-weight: 600;
        color: {c['text_muted']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {c['text_muted']};
        font-size: 11px;
        text-transform: uppercase;
    }}
    QStatusBar {{
        background: {c['bg_sidebar']};
        color: {c['text_muted']};
        border-top: 1px solid {c['border']};
        font-size: 11px;
    }}
    QToolTip {{
        background-color: {c['bg_card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px 8px;
        border-radius: 4px;
    }}
    QListWidget {{
        background-color: {c['bg_card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 4px;
    }}
    QListWidget::item:selected {{
        background-color: {c['accent']};
        color: #ffffff;
    }}
    QListWidget::item:hover {{
        background-color: {c['bg_alt']};
    }}
    QMenu {{
        background-color: {c['bg_card']};
        color: {c['text']};
        border: 1px solid {c['border']};
    }}
    QMenu::item:selected {{
        background-color: {c['accent']};
        color: #ffffff;
    }}
    """
    app.setStyleSheet(qss)
