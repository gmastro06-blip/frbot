from __future__ import annotations

import pytest


pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from ui_main import MainWindow


def test_ui_main_constructs_and_routes() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert w.screens.currentIndex() == 0
        w.btn_home_cavebot.click()
        app.processEvents()
        assert w.screens.currentIndex() == 2
        w.btn_screen_healing.click()
        app.processEvents()
        assert w.screens.currentIndex() == 1
        w.btn_healing_back.click()
        app.processEvents()
        assert w.screens.currentIndex() == 0
    finally:
        w.close()
