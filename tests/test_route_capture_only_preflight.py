from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from contracts.capture import Frame
from contracts.errors import PreflightFailed
from contracts.evidence import Roi
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.config_loader import LoadedConfig


@dataclass(frozen=True)
class _VerifyResult:
    ok: bool
    reason: str = ''


class _FakeCapture:
    name = 'obs_source'

    def __init__(self, frame: Frame) -> None:
        self._frame = frame

    def verify(self) -> _VerifyResult:
        return _VerifyResult(ok=True)

    def grab(self) -> Frame:
        return self._frame


def _mk_ctx(tmp_path: Path) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='real',
        tick_hz=20.0,
        config_path=str(tmp_path / 'runtime_config.json'),
        bot_config_path=str(tmp_path / 'bot_config.json'),
        enable_cavebot=False,
        minimap_roi='minimap',
        window_hwnd=0,
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


def test_run_capture_only_does_not_depend_on_startup_guards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from runtime import route_preflight as rp

    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'Tibia_Fuente')

    rois = {
        'minimap': Roi(name='minimap', x=1, y=1, width=2, height=2),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=1, height=1),
        'hp_mp': Roi(name='hp_mp', x=0, y=0, width=1, height=1),
        'target_frame': Roi(name='target_frame', x=0, y=0, width=1, height=1),
    }
    monkeypatch.setattr(rp, 'load_rois', lambda _ctx: LoadedConfig(rois=rois, frame_width=4, frame_height=4), raising=True)

    frame = Frame(
        width=4,
        height=4,
        monotonic_ts_ns=1,
        digest_hex='d',
        rgb=bytes([1] * (4 * 4 * 3)),
        minimap_detected=True,
        minimap_rgb=bytes([2] * (2 * 2 * 3)),
        minimap_width=2,
        minimap_height=2,
        minimap_digest_hex='m',
    )
    monkeypatch.setattr(rp, 'ObsSourceRealCapture', lambda **_k: _FakeCapture(frame), raising=True)
    monkeypatch.setattr(rp, 'detect_player_marker', lambda *_a, **_k: object(), raising=True)

    ctx = _mk_ctx(tmp_path)
    cap = rp.run_capture_only(ctx)

    assert cap.name == 'obs_source'
    assert ctx.status.state == RuntimeState.READY
    assert ctx.capture is not None and ctx.capture.verified is True


def test_run_capture_only_fails_when_obs_source_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from runtime import route_preflight as rp

    monkeypatch.delenv('FRBOT_OBS_SOURCE_NAME', raising=False)
    ctx = _mk_ctx(tmp_path)

    with pytest.raises(PreflightFailed) as ei:
        rp.run_capture_only(ctx)
    assert str(ei.value) == 'obs_source_not_found'


def test_run_capture_only_fails_when_minimap_roi_out_of_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from runtime import route_preflight as rp

    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'Tibia_Fuente')

    rois = {
        'minimap': Roi(name='minimap', x=10, y=10, width=5, height=5),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=1, height=1),
        'hp_mp': Roi(name='hp_mp', x=0, y=0, width=1, height=1),
        'target_frame': Roi(name='target_frame', x=0, y=0, width=1, height=1),
    }
    monkeypatch.setattr(rp, 'load_rois', lambda _ctx: LoadedConfig(rois=rois, frame_width=4, frame_height=4), raising=True)

    frame = Frame(
        width=4,
        height=4,
        monotonic_ts_ns=1,
        digest_hex='d',
        rgb=bytes([1] * (4 * 4 * 3)),
        minimap_detected=True,
        minimap_rgb=bytes([2] * (2 * 2 * 3)),
        minimap_width=2,
        minimap_height=2,
        minimap_digest_hex='m',
    )
    monkeypatch.setattr(rp, 'ObsSourceRealCapture', lambda **_k: _FakeCapture(frame), raising=True)
    monkeypatch.setattr(rp, 'detect_player_marker', lambda *_a, **_k: object(), raising=True)

    ctx = _mk_ctx(tmp_path)
    with pytest.raises(PreflightFailed) as ei:
        rp.run_capture_only(ctx)
    assert str(ei.value) == 'minimap_roi_out_of_bounds'


def test_run_capture_only_allows_initial_missing_player_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from runtime import route_preflight as rp

    monkeypatch.setenv('FRBOT_OBS_SOURCE_NAME', 'Tibia_Fuente')

    rois = {
        'minimap': Roi(name='minimap', x=1, y=1, width=2, height=2),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=1, height=1),
        'hp_mp': Roi(name='hp_mp', x=0, y=0, width=1, height=1),
        'target_frame': Roi(name='target_frame', x=0, y=0, width=1, height=1),
    }
    monkeypatch.setattr(rp, 'load_rois', lambda _ctx: LoadedConfig(rois=rois, frame_width=4, frame_height=4), raising=True)

    frame = Frame(
        width=4,
        height=4,
        monotonic_ts_ns=1,
        digest_hex='d',
        rgb=bytes([1] * (4 * 4 * 3)),
        minimap_detected=True,
        minimap_rgb=bytes([2] * (2 * 2 * 3)),
        minimap_width=2,
        minimap_height=2,
        minimap_digest_hex='m',
    )
    monkeypatch.setattr(rp, 'ObsSourceRealCapture', lambda **_k: _FakeCapture(frame), raising=True)
    monkeypatch.setattr(rp, 'detect_player_marker', lambda *_a, **_k: None, raising=True)

    ctx = _mk_ctx(tmp_path)
    cap = rp.run_capture_only(ctx)

    assert cap.name == 'obs_source'
    assert ctx.status.state == RuntimeState.READY
    assert ctx.status.reason == 'minimap_player_not_found'
