from __future__ import annotations

import json
from pathlib import Path

import pytest


pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from models import Script, Waypoint
from storage import canonical_json, save_script
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
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_load_dialog_defaults_to_waypoints_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Waypoints").mkdir(parents=True, exist_ok=True)

    captured: dict[str, str] = {}

    def _fake_open(_parent: object, _title: str, start_dir: str, _flt: str) -> tuple[str, str]:
        captured["start_dir"] = str(start_dir)
        return "", ""

    monkeypatch.setattr("ui_main.QFileDialog.getOpenFileName", _fake_open)

    w = MainWindow()
    try:
        w._on_load_clicked()
        app.processEvents()
        assert captured.get("start_dir") == str(tmp_path / "Waypoints")
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_restores_last_loaded_script_on_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    wp_dir = tmp_path / "Waypoints"
    wp_dir.mkdir(parents=True, exist_ok=True)

    script_path = wp_dir / "remember_me.json"
    script = Script(name="remembered", waypoints=[Waypoint(type="walk", x=10, y=20, z=7)])
    save_script(str(script_path), script)

    monkeypatch.setattr(
        "ui_main.QFileDialog.getOpenFileName",
        lambda *_a, **_kw: (str(script_path), "JSON (*.json)"),
    )

    w1 = MainWindow()
    try:
        w1._on_load_clicked()
        app.processEvents()
        assert str(w1._current_path) == str(script_path)
    finally:
        w1._last_saved_state = canonical_json(w1._script)
        w1.close()

    w2 = MainWindow()
    try:
        app.processEvents()
        assert str(w2._current_path) == str(script_path)
        assert str(w2._script.name) == "remembered"
        assert len(w2._script.waypoints) == 1
    finally:
        w2._last_saved_state = canonical_json(w2._script)
        w2.close()


def test_ui_clears_stale_last_loaded_script_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "diagnostics").mkdir(parents=True, exist_ok=True)

    missing_path = tmp_path / "Waypoints" / "missing_script.json"
    state_path = tmp_path / "diagnostics" / "ui_state.json"
    state_path.write_text(
        json.dumps({"last_script_path": str(missing_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    w = MainWindow()
    try:
        app.processEvents()
        state_now = json.loads(state_path.read_text(encoding="utf-8", errors="replace") or "{}")
        assert str(state_now.get("last_script_path") or "") == ""
        assert w._current_path is None
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()
