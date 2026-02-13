from __future__ import annotations

import json
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cavebot_entrypoint import run_cavebot_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'minimap': {'x': 2, 'y': 2, 'width': 64, 'height': 64},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _base_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'cavebot')
    monkeypatch.setenv('FRBOT_CAVEBOT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))

    # Marker detector settings match MockWorld default marker color.
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_RGB', '255,0,255')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_TOL', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MAX_PIXELS', '0')

    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_ATTEMPTS_PER_WAYPOINT', '10')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT', '50')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS', '100')

    # Strong binding ok by default.
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')


def test_cavebot_progress_requires_distance_decrease(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    # Marker should move toward waypoint; progress is ONLY distance reduction.
    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 4,
                    'y': 1,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 20,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_PROGRESS_OK', 'true')
    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'false')
    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_WRONG_DIRECTION', 'false')
    monkeypatch.setenv('MOCK_CAVEBOT_NOISE_ONLY', 'false')

    assert run_cavebot_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"abort_reason":"none"' in runtime_log

def test_cavebot_abort_wrong_direction_angle_gt_90(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 0,
                    'y': 1,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 20,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_WRONG_DIRECTION', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_wrong_direction' in fatal

def test_cavebot_stuck_detected_after_five_ticks(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 100,
                    'y': 0,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 50,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_stuck_detected' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # One intent max per tick; ensure no unexpected extra inputs were sent.
    assert '"inputs_sent":5' in runtime_log


def test_cavebot_dual_marker_prefers_moving_marker(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    # With dual-marker enabled, MockWorld starts the moving marker at (10,10).
    # If the static marker is chosen instead, direction/progress will be wrong and cavebot aborts.
    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 18,
                    'y': 10,
                    'z': 7,
                    'radius_px': 2,
                    'max_ticks': 20,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_PROGRESS_OK', 'true')
    monkeypatch.setenv('MOCK_CAVEBOT_DUAL_MARKER', 'true')
    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'false')
    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_WRONG_DIRECTION', 'false')
    monkeypatch.setenv('MOCK_CAVEBOT_NOISE_ONLY', 'false')

    assert run_cavebot_only() == 0


def test_cavebot_minimap_force_black_aborts_roi_black(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 8,
                    'y': 0,
                    'z': 7,
                    'radius_px': 2,
                    'max_ticks': 5,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MINIMAP_FORCE_BLACK', 'true')
    monkeypatch.setenv('MOCK_CAVEBOT_NOISE_ONLY', 'false')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_marker_roi_black' in fatal

def test_cavebot_waypoint_timeout_aborts_deterministically(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 100,
                    'y': 0,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 2,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_PROGRESS_OK', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_waypoint_timeout' in fatal


def test_cavebot_waypoint_reached_requires_two_ticks_stable(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 2,
                    'y': 1,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 20,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_PROGRESS_OK', 'true')

    assert run_cavebot_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"event":"WAYPOINT_REACHED"' in runtime_log


def test_cavebot_runner_never_spams_inputs_one_per_tick(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 100,
                    'y': 0,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 50,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'true')

    assert run_cavebot_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # Must not send more than one input per tick; inputs_sent should be a simple counter.
    assert '"inputs_sent":2' in runtime_log
    assert '"inputs_sent":999' not in runtime_log


def test_cavebot_window_binding_lost(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 2,
                    'y': 0,
                    'z': 7,
                    'radius_px': 0,
                    'max_ticks': 20,
                }
            ]
        ),
    )

    # Preflight passes, but tick binding fails.
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '0')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_window_binding_lost' in fatal
