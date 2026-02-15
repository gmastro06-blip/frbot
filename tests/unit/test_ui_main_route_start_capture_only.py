from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from contracts.errors import PreflightFailed
from contracts.capture import Frame
from ui_main import MainWindow


class _FakeCapture:
    name = 'obs_source'

    def grab(self) -> Frame:
        return Frame(
            width=4,
            height=4,
            monotonic_ts_ns=1,
            digest_hex='d',
            rgb=bytes([1] * (4 * 4 * 3)),
            minimap_detected=True,
            minimap_rgb=bytes([1] * (2 * 2 * 3)),
            minimap_width=2,
            minimap_height=2,
            minimap_digest_hex='mm',
        )


def test_ui_route_start_sets_recording_true_with_capture_only(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr('ui_main.route_capture_only_preflight', lambda _ctx: _FakeCapture())

    w = MainWindow()
    try:
        w._on_route_start_clicked()
        app.processEvents()

        assert w._route_recording_active is True
        assert w._route_session is not None
        assert 'grabando' in w.lbl_route_status.text().lower()
    finally:
        w.close()


def test_ui_route_start_retries_with_detected_obs_source(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])

    calls = {"n": 0}

    def _fake_preflight(_ctx: object) -> _FakeCapture:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PreflightFailed("obs_source_not_found")
        assert os.environ.get("FRBOT_OBS_SOURCE_NAME") == "Tibia Monitor"
        return _FakeCapture()

    monkeypatch.setattr("ui_main.route_capture_only_preflight", _fake_preflight)
    monkeypatch.setattr("ui_main.list_obs_input_names", lambda: ["Display Capture", "Tibia Monitor"])

    w = MainWindow()
    try:
        w._on_route_start_clicked()
        app.processEvents()

        assert calls["n"] == 2
        assert w._route_recording_active is True
        assert w._route_session is not None
        assert "grabando" in w.lbl_route_status.text().lower()
    finally:
        w.close()


def test_ui_route_start_tries_multiple_obs_sources_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])

    calls = {"n": 0}

    def _fake_preflight(_ctx: object) -> _FakeCapture:
        calls["n"] += 1
        src = str(os.environ.get("FRBOT_OBS_SOURCE_NAME", "") or "")
        if src == "Display Capture":
            raise PreflightFailed("minimap_player_not_found")
        if src == "Tibia Monitor":
            return _FakeCapture()
        raise PreflightFailed("obs_source_not_found")

    monkeypatch.setattr("ui_main.route_capture_only_preflight", _fake_preflight)
    monkeypatch.setattr("ui_main.list_obs_input_names", lambda: ["Display Capture", "Tibia Monitor"])
    monkeypatch.setenv("FRBOT_OBS_SOURCE_NAME", "Missing Source")

    w = MainWindow()
    try:
        w._on_route_start_clicked()
        app.processEvents()

        assert calls["n"] >= 2
        assert w._route_recording_active is True
        assert w._route_session is not None
        assert os.environ.get("FRBOT_OBS_SOURCE_NAME") == "Tibia Monitor"
        assert "grabando" in w.lbl_route_status.text().lower()
    finally:
        w.close()
