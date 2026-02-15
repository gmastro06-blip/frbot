from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from models import Waypoint
from storage import canonical_json
from ui_main import MainWindow


class _FakeProc:
    def __init__(self) -> None:
        self._code: int | None = None
        self.terminated = False
        self.pid = 4321

    def poll(self) -> int | None:
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self._code = 0

    def wait(self, timeout: float | None = None) -> int:
        return int(self._code or 0)

    def kill(self) -> None:
        self._code = 1


def test_ui_cavebot_start_runs_external_process(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    proc = _FakeProc()
    monkeypatch.setattr("ui_main.subprocess.Popen", lambda *args, **kwargs: proc)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        assert w._cavebot_running is True
        assert "process" in w.lbl_cavebot_runtime.text().lower()

        w._on_cavebot_tick()
        assert w._cavebot_running is True

        proc._code = 0
        w._on_cavebot_tick()
        assert w._cavebot_running is False
        assert "ok" in w.lbl_cavebot_status.text().lower()
        assert "Tick: 0 | WP: -" == w.lbl_cavebot_runtime.text()
    finally:
        w.close()


def test_ui_cavebot_tick_updates_runtime_overlay_from_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    proc = _FakeProc()
    monkeypatch.setattr("ui_main.subprocess.Popen", lambda *args, **kwargs: proc)

    trace_dir = tmp_path / "diagnostics" / "frames"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace = trace_dir / "cavebot_trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "event": "abort",
                "tick_index": 7,
                "blocked_reason": "move_key_no_effect",
                "pnf": False,
                "last_keys_sent": ["RIGHT"],
                "distance_after_px": 12.3,
                "angle_deg": 0.0,
                "waypoint": {"waypoint_id": "wp0"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        w._on_cavebot_tick()

        txt = w.lbl_cavebot_runtime.text().lower()
        assert "block:move_key_no_effect" in txt
        assert "pnf:0" in txt
        assert "key:right" in txt
    finally:
        w.close()


def test_ui_cavebot_tick_shows_roi_sanity_reason_when_roi_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    proc = _FakeProc()
    monkeypatch.setattr("ui_main.subprocess.Popen", lambda *args, **kwargs: proc)

    trace_dir = tmp_path / "diagnostics" / "frames"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace = trace_dir / "cavebot_trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "event": "abort",
                "tick_index": 9,
                "blocked_reason": "roi_invalid",
                "roi_sanity_reason": "minimap_roi_black_or_static",
                "pnf": False,
                "last_keys_sent": ["UP"],
                "distance_after_px": 20.0,
                "angle_deg": 0.0,
                "waypoint": {"waypoint_id": "wp_roi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        w._on_cavebot_tick()

        txt = w.lbl_cavebot_runtime.text().lower()
        assert "block:roi_invalid" in txt
        assert "roi:minimap_roi_black_or_static" in txt
    finally:
        w.close()


def test_route_world_lock_status_label_toggles() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        assert w.lbl_route_world_lock.text() == "✗ World lock: OFF"
        assert "FRBOT_ROUTE_LOCALIZE_MIN_SCORE" in w.lbl_route_world_lock.toolTip()
        w._set_route_world_lock_status(True, score=0.83)
        assert "✓ World lock: ON" in w.lbl_route_world_lock.text()
        w._set_route_world_lock_status(False, score=0.41)
        assert w.lbl_route_world_lock.text() == "✗ World lock: OFF (0.41)"
        w._set_route_world_lock_status(False)
        assert w.lbl_route_world_lock.text() == "✗ World lock: OFF (0.41)"
    finally:
        w.close()


def test_route_localize_min_score_setting_updates_env_and_tooltip() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        w.spin_route_localize_min_score.setValue(0.73)
        app.processEvents()

        assert os.environ.get("FRBOT_ROUTE_LOCALIZE_MIN_SCORE", "") == "0.73"
        assert "0.73" in w.lbl_route_world_lock.toolTip()
    finally:
        w.close()


def test_route_localize_min_score_import_env_applies_to_ui() -> None:
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        w._apply_env_values_to_ui({"FRBOT_ROUTE_LOCALIZE_MIN_SCORE": "0.81"})
        w._apply_runtime_env_from_ui()
        app.processEvents()

        assert w.spin_route_localize_min_score.value() == pytest.approx(0.81, abs=1e-6)
        assert os.environ.get("FRBOT_ROUTE_LOCALIZE_MIN_SCORE", "") == "0.81"
    finally:
        w.close()


def test_ui_cavebot_start_sets_world_waypoint_space_when_route_has_world_coords(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get("env", {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w._script.waypoints = [
            Waypoint(
                type="walk",
                x=33123,
                y=32211,
                z=7,
                options={"coord_space": "world", "world_x": 33123, "world_y": 32211, "world_z": 7},
            )
        ]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._cavebot_running is True
        assert captured_env.get("FRBOT_CAVEBOT_WAYPOINT_SPACE", "") == "world"

        payload = json.loads(str(captured_env["FRBOT_CAVEBOT_WAYPOINTS"]))
        assert payload[0]["options"]["coord_space"] == "world"
        assert int(payload[0]["options"]["world_x"]) == 33123
        assert int(payload[0]["options"]["world_y"]) == 32211
        assert int(payload[0]["options"]["world_z"]) == 7
    finally:
        w._on_cavebot_stop_clicked()
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_uses_waypoints_file_for_large_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)

    captured_env: dict[str, str] = {}

    proc = _FakeProc()

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get('env', {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=i, y=i, z=7) for i in range(5000)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._cavebot_running is True
        assert "FRBOT_CAVEBOT_WAYPOINTS_FILE" in captured_env
        assert "FRBOT_CAVEBOT_WAYPOINTS" not in captured_env

        waypoints_file = Path(captured_env["FRBOT_CAVEBOT_WAYPOINTS_FILE"])
        assert waypoints_file.exists()
        assert waypoints_file.is_file()

        w._on_cavebot_stop_clicked()
        app.processEvents()
        assert "FRBOT_CAVEBOT_WAYPOINTS_FILE" not in os.environ
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_sanitizes_invalid_window_hwnd(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    monkeypatch.setenv("FRBOT_WINDOW_HWND", "not-a-valid-hwnd")
    monkeypatch.delenv("FRBOT_WINDOW_TITLE", raising=False)

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get('env', {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._cavebot_running is True
        assert "FRBOT_WINDOW_HWND" not in captured_env
        assert captured_env.get("FRBOT_WINDOW_TITLE", "") == "Tibia"
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_falls_back_when_config_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    valid_cfg = cfg_dir / "rois_prod_full.json"
    valid_cfg.write_text(
        """{"rois":{"minimap":{"x":0,"y":0,"width":10,"height":10},"battle_list":{"x":0,"y":0,"width":10,"height":10},"hp_mp":{"x":0,"y":0,"width":10,"height":10},"target_frame":{"x":0,"y":0,"width":10,"height":10}}}""",
        encoding="utf-8",
    )

    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get('env', {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w.input_config_path.setText("config/does_not_exist.json")
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._cavebot_running is True
        assert Path(captured_env.get("FRBOT_CONFIG_PATH", "")).resolve() == valid_cfg.resolve()
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_anchors_recorder_waypoints_to_minimap_center(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)

    cfg = tmp_path / "rois_prod_emergency.json"
    cfg.write_text(
        """{"rois":{"minimap":{"x":0,"y":0,"width":120,"height":80},"battle_list":{"x":0,"y":0,"width":10,"height":10},"hp_mp":{"x":0,"y":0,"width":10,"height":10},"target_frame":{"x":0,"y":0,"width":10,"height":10}}}""",
        encoding="utf-8",
    )

    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get("env", {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w.input_config_path.setText(str(cfg))
        w._script.metadata = {"recorder": {"default_z": 0, "simplify_straight_every": 3}}
        w._script.waypoints = [
            Waypoint(type="walk", x=0, y=0, z=0),
            Waypoint(type="walk", x=-2, y=-1, z=0),
        ]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        payload = json.loads(str(captured_env["FRBOT_CAVEBOT_WAYPOINTS"]))
        assert payload[0]["x"] == 60
        assert payload[0]["y"] == 40
        assert payload[0]["z"] == 7
        assert payload[1]["x"] == 58
        assert payload[1]["y"] == 39
        assert payload[1]["z"] == 7
    finally:
        w._on_cavebot_stop_clicked()
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_anchors_relative_waypoints_without_recorder_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)

    cfg = tmp_path / "rois_prod_emergency.json"
    cfg.write_text(
        """{"rois":{"minimap":{"x":0,"y":0,"width":120,"height":80},"battle_list":{"x":0,"y":0,"width":10,"height":10},"hp_mp":{"x":0,"y":0,"width":10,"height":10},"target_frame":{"x":0,"y":0,"width":10,"height":10}}}""",
        encoding="utf-8",
    )

    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get("env", {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w.input_config_path.setText(str(cfg))
        w._script.metadata = {}
        w._script.waypoints = [
            Waypoint(type="walk", x=-17, y=51, z=7),
            Waypoint(type="walk", x=-19, y=52, z=7),
        ]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        payload = json.loads(str(captured_env["FRBOT_CAVEBOT_WAYPOINTS"]))
        assert payload[0]["x"] == 60
        assert payload[0]["y"] == 40
        assert payload[0]["z"] == 7
        assert payload[1]["x"] == 58
        assert payload[1]["y"] == 41
        assert payload[1]["z"] == 7
    finally:
        w._on_cavebot_stop_clicked()
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_uses_profile_compatible_roi_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRBOT_PROFILE", "prod_emergency")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    full_cfg = cfg_dir / "rois_prod_full.json"
    full_cfg.write_text(
        """{"rois":{"minimap":{"x":0,"y":0,"width":10,"height":10},"battle_list":{"x":0,"y":0,"width":10,"height":10},"hp_mp":{"x":0,"y":0,"width":10,"height":10},"target_frame":{"x":0,"y":0,"width":10,"height":10},"hp_bar":{"x":0,"y":0,"width":10,"height":10}}}""",
        encoding="utf-8",
    )
    emergency_cfg = tmp_path / "rois_prod_emergency.json"
    emergency_cfg.write_text(
        """{"rois":{"minimap":{"x":0,"y":0,"width":10,"height":10},"battle_list":{"x":0,"y":0,"width":10,"height":10},"hp_mp":{"x":0,"y":0,"width":10,"height":10},"target_frame":{"x":0,"y":0,"width":10,"height":10}}}""",
        encoding="utf-8",
    )

    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get("env", {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w.input_config_path.setText(str(full_cfg))
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._cavebot_running is True
        assert Path(captured_env.get("FRBOT_CONFIG_PATH", "")).resolve() == emergency_cfg.resolve()
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_sets_wrong_direction_guard_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    monkeypatch.delenv("FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG", raising=False)
    monkeypatch.delenv("FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK", raising=False)
    monkeypatch.delenv("FRBOT_CAVEBOT_MARKER_AREA_RATIO_MAX", raising=False)
    monkeypatch.delenv("FRBOT_CAVEBOT_STUCK_WINDOW", raising=False)

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get("env", {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert captured_env.get("FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG") == "130"
        assert captured_env.get("FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK") == "6"
        assert captured_env.get("FRBOT_CAVEBOT_MARKER_AREA_RATIO_MAX") == "0.80"
        assert captured_env.get("FRBOT_CAVEBOT_STUCK_WINDOW") == "12"
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_replaces_legacy_green_marker_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    captured_env: dict[str, str] = {}
    proc = _FakeProc()

    monkeypatch.setenv("FRBOT_PLAYER_MARKER_RGB", "0,200,0")
    monkeypatch.setenv("FRBOT_PLAYER_MARKER_TOL", "45")

    def _fake_popen(*_args: object, **kwargs: object) -> _FakeProc:
        env = kwargs.get("env", {})
        if isinstance(env, dict):
            for k, v in env.items():
                captured_env[str(k)] = str(v)
        return proc

    monkeypatch.setattr("ui_main.subprocess.Popen", _fake_popen)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert captured_env.get("FRBOT_PLAYER_MARKER_RGB") == "255,0,255"
        assert captured_env.get("FRBOT_PLAYER_MARKER_TOL") == "30"
    finally:
        w._on_cavebot_stop_clicked()
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_cavebot_start_does_not_dirty_unchanged_script(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    proc = _FakeProc()
    monkeypatch.setattr("ui_main.subprocess.Popen", lambda *args, **kwargs: proc)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        assert w._is_dirty() is False

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._cavebot_running is True
        assert w._is_dirty() is False
    finally:
        w._on_cavebot_stop_clicked()
        w._last_saved_state = canonical_json(w._script)
        w.close()


def test_ui_close_after_cavebot_start_does_not_prompt_save(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    proc = _FakeProc()
    monkeypatch.setattr("ui_main.subprocess.Popen", lambda *args, **kwargs: proc)

    class _UnexpectedUnsavedDialog:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("Unsaved dialog should not be shown for clean script")

    monkeypatch.setattr("ui_main.QMessageBox", _UnexpectedUnsavedDialog)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()

        assert w._is_dirty() is False
        assert w.close() is True
    finally:
        if w.isVisible():
            w.close()


def test_ui_cavebot_stop_uses_taskkill_tree_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    proc = _FakeProc()
    taskkill_calls: list[list[str]] = []

    monkeypatch.setattr("ui_main.subprocess.Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr("ui_main.sys.platform", "win32", raising=False)

    def _fake_run(cmd: object, **_kwargs: object) -> object:
        if isinstance(cmd, list):
            taskkill_calls.append([str(x) for x in cmd])
        proc._code = 0
        return type("_RunResult", (), {"returncode": 0})()

    monkeypatch.setattr("ui_main.subprocess.run", _fake_run)

    w = MainWindow()
    try:
        w._script.waypoints = [Waypoint(type="walk", x=10, y=20, z=7)]
        w._last_saved_state = canonical_json(w._script)

        w._on_cavebot_start_clicked()
        app.processEvents()
        assert w._cavebot_running is True

        w._on_cavebot_stop_clicked()
        app.processEvents()

        assert w._cavebot_running is False
        assert taskkill_calls
        assert taskkill_calls[0][0].lower() == "taskkill"
        assert "/t" in [x.lower() for x in taskkill_calls[0]]
        assert "/f" in [x.lower() for x in taskkill_calls[0]]
    finally:
        w._last_saved_state = canonical_json(w._script)
        w.close()
