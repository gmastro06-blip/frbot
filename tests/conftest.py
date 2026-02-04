from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root is importable for tests.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep tests deterministic and isolated from developer machine env.
    monkeypatch.delenv('FRBOT_PROFILE', raising=False)
    monkeypatch.delenv('FRBOT_MAX_TICKS', raising=False)
    monkeypatch.delenv('FRBOT_CAPTURE_SOURCE', raising=False)
    monkeypatch.delenv('FRBOT_OBS_SOURCE_NAME', raising=False)
    monkeypatch.delenv('FRBOT_OBS_PROJECTOR_TITLE', raising=False)
    monkeypatch.delenv('FRBOT_REAL_FRAMES_DIR', raising=False)
    monkeypatch.delenv('FRBOT_PLAYER_MARKER_RGB_EFFECTIVE', raising=False)
    monkeypatch.delenv('FRBOT_PLAYER_MARKER_MIN_PIXELS_EFFECTIVE', raising=False)
    monkeypatch.setenv('FRBOT_MINIMAP_ROI', 'minimap')
    monkeypatch.setenv('FRBOT_ENABLE_CAVEBOT', '1')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_RGB', '255,0,255')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_TOL', '0')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MAX_PIXELS', '0')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MIN_FILL_RATIO', '0.0')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MAX_ASPECT_RATIO', '50.0')
    monkeypatch.setenv('FRBOT_PIXELS_PER_TILE', '1.0')
    monkeypatch.delenv('FRBOT_BOT_CONFIG_PATH', raising=False)
