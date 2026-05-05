"""
Entry point for the Inventory Control application.
Run: python main.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Ensure project root is on sys.path even when bundled with PyInstaller
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _global_exception_hook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(msg, file=sys.stderr)
    try:
        from PyQt6.QtWidgets import QMessageBox, QApplication
        if QApplication.instance():
            QMessageBox.critical(None, "Unhandled Error", msg[:2000])
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _global_exception_hook


def main() -> None:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt

    # Must be set before QApplication is created — required for WebEngine
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("Inventory Control")
    app.setOrganizationName("NRF")

    from app.ui.main_window import MainWindow
    window = MainWindow(app)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
