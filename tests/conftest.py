from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
from dataclasses import dataclass
from typing import Callable

import pytest

# Ensure repo root is importable for tests.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# === Shared Fixtures for Adapters ===

@dataclass
class MockFrameProvider:
    """Configurable mock frame provider for testing."""
    width: int = 4
    height: int = 4
    is_black: bool = False
    variance: float = 1.0

    def __call__(self, name: str, w: int, h: int) -> tuple[bytes, int, int]:
        if self.is_black:
            return bytes([0] * (w * h * 3)), w, h
        rgb = bytearray(w * h * 3)
        for i in range(0, len(rgb), 3):
            v = int(255 * self.variance * ((i // 3) % 255) / 255) + int(255 * (1 - self.variance) * 0.5)
            rgb[i] = v % 256
            rgb[i + 1] = (v * 2) % 256
            rgb[i + 2] = (v * 3) % 256
        return bytes(rgb), w, h


@pytest.fixture
def mock_frame_provider() -> Callable:
    return MockFrameProvider(width=4, height=4)


@pytest.fixture
def mock_black_frame_provider() -> Callable:
    return MockFrameProvider(width=4, height=4, is_black=True)


@pytest.fixture
def mock_low_variance_frame_provider() -> Callable:
    return MockFrameProvider(width=4, height=4, variance=0.01)


@pytest.fixture
def minimal_rois() -> dict:
    from contracts.evidence import Roi
    return {
        'minimap': Roi(name='minimap', x=0, y=0, width=2, height=2),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=2, height=2),
    }


@pytest.fixture
def full_rois() -> dict:
    from contracts.evidence import Roi
    return {
        'minimap': Roi(name='minimap', x=0, y=0, width=256, height=256),
        'battle_list': Roi(name='battle_list', x=256, y=0, width=100, height=300),
        'target_frame': Roi(name='target_frame', x=356, y=0, width=50, height=50),
        'hp_mp': Roi(name='hp_mp', x=0, y=300, width=100, height=30),
        'inventory_text': Roi(name='inventory_text', x=400, y=0, width=150, height=200),
    }


class MockCaptureAdapter:
    """Mock capture adapter for testing."""
    def __init__(self, provider: Callable, width: int = 4, height: int = 4):
        self._provider = provider
        self._width = width
        self._height = height
        self.name = 'mock-capture'
        self.grab_count = 0

    def preflight(self) -> bool:
        return True

    def grab(self):
        from adapters.capture.base import Frame
        self.grab_count += 1
        data, w, h = self._provider(self.name, self._width, self._height)
        return Frame(width=w, height=h, monotonic_ts_ns=0)


class MockInputAdapter:
    """Mock input adapter for testing."""
    def __init__(self):
        self.pressed_keys: list[str] = []
        self.clicked_xy: list[tuple[int, int]] = []
        self._bound = True

    def preflight(self) -> bool:
        return True

    def press_key(self, key: str) -> None:
        self.pressed_keys.append(key)

    def click(self, x: int, y: int) -> None:
        self.clicked_xy.append((x, y))

    def assert_bound(self, hwnd: int = 0) -> None:
        if not self._bound:
            raise RuntimeError("Not bound")


class MockWindowBinding:
    """Mock window binding for testing."""
    def __init__(self, bound: bool = True):
        self._bound = bound

    def preflight(self) -> bool:
        return self._bound

    def assert_bound(self) -> None:
        if not self._bound:
            raise RuntimeError("Window binding lost")

    def snapshot(self):
        mock = MagicMock()
        mock.hwnd = 12345 if self._bound else 0
        return mock


@pytest.fixture
def mock_capture(mock_frame_provider) -> MockCaptureAdapter:
    return MockCaptureAdapter(mock_frame_provider)


@pytest.fixture
def mock_input() -> MockInputAdapter:
    return MockInputAdapter()


@pytest.fixture
def mock_window_binding() -> MockWindowBinding:
    return MockWindowBinding(bound=True)


@pytest.fixture
def mock_window_binding_unbound() -> MockWindowBinding:
    return MockWindowBinding(bound=False)


@pytest.fixture
def clean_env(monkeypatch):
    original_env = os.environ.copy()
    yield monkeypatch
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_env(clean_env, monkeypatch):
    monkeypatch.setenv('FRBOT_MODE', 'mock')
    monkeypatch.setenv('FRBOT_CAPTURE_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_INPUT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', 'config/test.json')
    return monkeypatch


@pytest.fixture
def real_env(clean_env, monkeypatch):
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_CAPTURE_BACKEND', 'mss')
    monkeypatch.setenv('FRBOT_INPUT_BACKEND', 'pynput')
    monkeypatch.setenv('FRBOT_WINDOW_HWND', '12345')
    return monkeypatch


@pytest.fixture
def runtime_context(minimal_rois):
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeStatus, RuntimeTelemetry
    config = RuntimeConfig(mode='mock')
    return RuntimeContext(
        config=config,
        status=RuntimeStatus(),
        telemetry=RuntimeTelemetry(),
        rois=minimal_rois,
    )


@pytest.fixture
def runtime_context_full(full_rois):
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeStatus, RuntimeTelemetry
    config = RuntimeConfig(mode='mock')
    return RuntimeContext(
        config=config,
        status=RuntimeStatus(),
        telemetry=RuntimeTelemetry(),
        rois=full_rois,
    )


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
