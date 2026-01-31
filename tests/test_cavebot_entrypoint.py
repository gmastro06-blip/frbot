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

    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_ATTEMPTS_PER_WAYPOINT', '3')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT', '10')
    monkeypatch.setenv('FRBOT_CAVEBOT_MIN_PIXEL_DELTA', '2')

    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS', '30')

    # Strong binding ok by default.
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')


def test_cavebot_progress_success(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    # Marker should move RIGHT by 1 px and reach target.
    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 2,
                    'y': 1,
                    'z': 7,
                    'expected_direction': 'E',
                    'min_pixel_delta': 1,
                    'max_ticks_without_progress': 10,
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
    assert 'inputs_sent=1' in runtime_log


def test_cavebot_abort_no_progress(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    # Set min delta to 3, but mock movement step is 2 (progress_ok) => jitter/no-progress.
    monkeypatch.setenv('FRBOT_CAVEBOT_MIN_PIXEL_DELTA', '3')
    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 100,
                    'y': 0,
                    'z': 7,
                    'expected_direction': 'E',
                    'min_pixel_delta': 3,
                    'max_ticks_without_progress': 10,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_PROGRESS_OK', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_no_progress' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert 'inputs_sent=1' in runtime_log
    assert 'inputs_sent=2' not in runtime_log


def test_cavebot_abort_wrong_direction(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 10,
                    'y': 0,
                    'z': 7,
                    'expected_direction': 'E',
                    'min_pixel_delta': 1,
                    'max_ticks_without_progress': 10,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_WRONG_DIRECTION', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_wrong_direction' in fatal


def test_cavebot_abort_marker_missing(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 10,
                    'y': 0,
                    'z': 7,
                    'expected_direction': 'E',
                    'min_pixel_delta': 1,
                    'max_ticks_without_progress': 10,
                }
            ]
        ),
    )

    # Noise-only mode: minimap changes but marker is not rendered.
    monkeypatch.setenv('MOCK_CAVEBOT_NOISE_ONLY', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_marker_not_found' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_cavebot_no_spam_inputs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
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
                    'expected_direction': 'E',
                    'min_pixel_delta': 2,
                    'max_ticks_without_progress': 10,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'true')

    assert run_cavebot_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # With marker static, we retry deterministically up to max attempts.
    assert 'inputs_sent=3' in runtime_log
    assert 'inputs_sent=4' not in runtime_log


def test_cavebot_waypoint_attempt_limit(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_ATTEMPTS_PER_WAYPOINT', '2')
    monkeypatch.setenv(
        'FRBOT_CAVEBOT_WAYPOINTS',
        json.dumps(
            [
                {
                    'waypoint_id': 'wp0',
                    'x': 100,
                    'y': 0,
                    'z': 7,
                    'expected_direction': 'E',
                    'min_pixel_delta': 2,
                    'max_ticks_without_progress': 10,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'true')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_waypoint_stuck' in fatal


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
                    'expected_direction': 'E',
                    'min_pixel_delta': 1,
                    'max_ticks_without_progress': 10,
                }
            ]
        ),
    )

    # Preflight passes, but tick binding fails.
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '0')

    assert run_cavebot_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'cavebot_window_binding_lost' in fatal
