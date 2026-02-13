"""Python 3.11 + PySide6 Waypoint/Script Editor.

Install:
    pip install PySide6

Run:
    python app.py

Notes:
- This app is UI + typed model + JSON persistence only.
- No third-party automation is implemented; action buttons only add waypoints and emit signals/logs.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from logging_setup import setup_logger
from ui_main import MainWindow


def main() -> int:
    setup_logger(log_path="runtime_ui.log")
    app = QApplication(sys.argv)

    w = MainWindow()
    w.show()

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
