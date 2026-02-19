from __future__ import annotations

import json
from pathlib import Path

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


def test_tick_input_only_events(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # Setup env similar to other cavebot tests but make vision rare and input frequent.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FRBOT_MODE', 'cavebot')
    monkeypatch.setenv('FRBOT_CAVEBOT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_RGB', '255,0,255')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_TOL', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MAX_PIXELS', '0')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_ATTEMPTS_PER_WAYPOINT', '10')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT', '50')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS', '30')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    # Make vision rare and input fast.
    monkeypatch.setenv('FRBOT_VISION_HZ', '1')
    monkeypatch.setenv('FRBOT_INPUT_HZ', '10')

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
                    'max_ticks': 20,
                }
            ]
        ),
    )

    monkeypatch.setenv('MOCK_CAVEBOT_PROGRESS_OK', 'true')
    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_STATIC', 'false')
    monkeypatch.setenv('MOCK_CAVEBOT_MARKER_WRONG_DIRECTION', 'false')
    monkeypatch.setenv('MOCK_CAVEBOT_NOISE_ONLY', 'false')

    # Run; we expect the run to finish successfully while generating input-only ticks.
    assert run_cavebot_only() in (0, 1)

    # Check runtime log contains at least one tick_input_only entry.
    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert 'tick_input_only' in runtime_log
