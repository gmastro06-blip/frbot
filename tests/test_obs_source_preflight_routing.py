from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import pytest

from contracts.errors import PreflightFailed
from contracts.evidence import Roi
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.config_loader import LoadedConfig


@dataclass(frozen=True)
class _DummyDet:
    pos: tuple[int, int]


@dataclass(frozen=True)
class _VerifyResult:
    ok: bool
    reason: str = ''


def _mk_ctx(tmp_path: Path) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='real',
        tick_hz=20.0,
        config_path=str(tmp_path / 'runtime_config.json'),
        bot_config_path=str(tmp_path / 'bot_config.json'),
        enable_cavebot=False,
        minimap_roi='minimap',
        window_hwnd=0x222,
        window_title_substring='Tibia',
        player_marker_rgb='255,0,255',
        player_marker_tol=30,
        player_marker_min_pixels=1,
        player_marker_max_pixels=0,
        player_marker_min_fill_ratio=0.1,
        player_marker_max_aspect_ratio=4.0,
        pixels_per_tile=1.0,
        max_attempts_per_waypoint=3,
        max_time_ms_per_waypoint=5000,
    )
    return RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )


def test_obs_source_preflight_does_not_touch_projector(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from runtime import preflight as preflight_mod

    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_CAPTURE_SOURCE', 'obs_source')
    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'TibiaSource')
    monkeypatch.setenv('FRBOT_CAPTURE_BACKEND', 'mss')
    monkeypatch.setenv('FRBOT_OBS_LUMA_STD_MIN', '0')

    # Guard rails are tested separately; keep preflight unit-test deterministic.
    monkeypatch.setattr(preflight_mod, 'enforce_prod_emergency_real_startup_guards', lambda **_k: None, raising=True)

    # If this gets called, the routing is wrong for obs_source.
    monkeypatch.setattr(preflight_mod, 'resolve_obs_projector_hwnd', lambda: (_ for _ in ()).throw(AssertionError('should not be called')), raising=True)

    # Deterministic input binding and adapter.
    class FakeBinding:
        def __init__(self, *, hwnd: int, title_substring: str) -> None:
            self.hwnd = hwnd
            self.title_substring = title_substring

        def verify(self) -> _VerifyResult:
            return _VerifyResult(ok=True, reason='')

        def assert_bound(self) -> None:
            return None

    monkeypatch.setattr(preflight_mod, 'Win32WindowBinding', FakeBinding, raising=True)
    monkeypatch.setattr(preflight_mod, 'resolve_input_hwnd', lambda **_k: 0x222, raising=True)

    class FakeInput:
        name = 'fake_input'

        def __init__(self, *, hwnd: int) -> None:
            self.hwnd = hwnd

        def verify(self) -> _VerifyResult:
            return _VerifyResult(ok=True, reason='')

    monkeypatch.setattr(preflight_mod, 'Win32HwndKeyboard', FakeInput, raising=True)

    # Minimal ROI + frame contract.
    rois = {
        'minimap': Roi(name='minimap', x=0, y=0, width=2, height=2),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=2, height=2),
        'hp_mp': Roi(name='hp_mp', x=0, y=0, width=2, height=2),
        'target_frame': Roi(name='target_frame', x=0, y=0, width=2, height=2),
    }
    monkeypatch.setattr(preflight_mod, 'load_rois', lambda _ctx: LoadedConfig(rois=rois, frame_width=4, frame_height=4), raising=True)

    # Bypass marker detection + ROI bounds contract for this routing test.
    monkeypatch.setattr(preflight_mod, 'validate_prod_emergency_real_rois_in_bounds', lambda **_k: None, raising=True)
    monkeypatch.setattr(preflight_mod, 'detect_player_marker', lambda *_a, **_k: _DummyDet(pos=(0, 0)), raising=True)

    # Force obs_source provider to be deterministic without OBS/WebSocket dependency.
    def provider(_name: str, _w: int, _h: int) -> Tuple[bytes, int, int]:
        w, h = 4, 4
        rgb = bytearray(w * h * 3)
        for i in range(0, len(rgb), 3):
            rgb[i] = (i // 3) % 255
        return bytes(rgb), w, h

    monkeypatch.setattr(preflight_mod.ObsSourceRealCapture, '_provider_fn', lambda self: provider, raising=True)

    ctx = _mk_ctx(tmp_path)
    cap, inp, binding = preflight_mod.preflight(ctx)

    assert getattr(cap, 'name', '') == 'obs_source'
    assert getattr(cap, 'obs_source_name', '') == 'TibiaSource'
    assert inp.verify().ok is True
    assert binding.verify().ok is True


def test_obs_source_preflight_requires_frame_dimensions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from runtime import preflight as preflight_mod

    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_PROFILE', 'prod_emergency')
    monkeypatch.setenv('FRBOT_MODE', 'real')
    monkeypatch.setenv('FRBOT_CAPTURE_SOURCE', 'obs_source')
    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'TibiaSource')

    monkeypatch.setattr(preflight_mod, 'enforce_prod_emergency_real_startup_guards', lambda **_k: None, raising=True)

    class FakeBinding:
        def __init__(self, *, hwnd: int, title_substring: str) -> None:
            self.hwnd = hwnd
            self.title_substring = title_substring

        def verify(self) -> _VerifyResult:
            return _VerifyResult(ok=True, reason='')

        def assert_bound(self) -> None:
            return None

    monkeypatch.setattr(preflight_mod, 'Win32WindowBinding', FakeBinding, raising=True)
    monkeypatch.setattr(preflight_mod, 'resolve_input_hwnd', lambda **_k: 0x222, raising=True)

    class FakeInput:
        name = 'fake_input'

        def __init__(self, *, hwnd: int) -> None:
            self.hwnd = hwnd

        def verify(self) -> _VerifyResult:
            return _VerifyResult(ok=True, reason='')

    monkeypatch.setattr(preflight_mod, 'Win32HwndKeyboard', FakeInput, raising=True)

    rois = {
        'minimap': Roi(name='minimap', x=0, y=0, width=2, height=2),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=2, height=2),
        'hp_mp': Roi(name='hp_mp', x=0, y=0, width=2, height=2),
        'target_frame': Roi(name='target_frame', x=0, y=0, width=2, height=2),
    }
    monkeypatch.setattr(preflight_mod, 'load_rois', lambda _ctx: LoadedConfig(rois=rois, frame_width=None, frame_height=None), raising=True)

    ctx = _mk_ctx(tmp_path)
    with pytest.raises(PreflightFailed) as ei:
        preflight_mod.preflight(ctx)
    assert str(ei.value) == 'config_invalid_schema'
