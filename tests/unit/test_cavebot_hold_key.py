"""Tests for hold-key (smooth-walk) semantics in sendinput mode.

Covers four cases in execute_cavebot_tick:
  1. Default (non-sendinput) mode → press_key used, key_down/key_up never called.
  2. sendinput first tick for a direction → key_down called, held_key set.
  3. sendinput same direction consecutive tick → no physical input (key already held).
  4. sendinput direction change → key_up old + key_down new.
  5. sendinput terminal abort → key_up released, held_key cleared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from contracts.capture import Frame
from contracts.runtime import (
    RuntimeConfig,
    RuntimeContext,
    RuntimeState,
    RuntimeStatus,
    RuntimeTelemetry,
    Waypoint,
)
from contracts.window import WindowBindingStatus, WindowRect
from runtime.cavebot_runner import CavebotProgressEval, execute_cavebot_tick
from runtime.cavebot_semantics import ProgressResult


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _CaptureStub:
    frames: list[Frame]
    name: str = 'capture-stub'

    def verify(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def grab(self) -> Frame:
        if not self.frames:
            raise AssertionError('no frames configured')
        if len(self.frames) == 1:
            return self.frames[0]
        return self.frames.pop(0)


@dataclass
class _TrackingInputStub:
    """Records press_key / key_down / key_up calls separately."""

    press_calls: list[str] = field(default_factory=list)
    down_calls: list[str] = field(default_factory=list)
    up_calls: list[str] = field(default_factory=list)
    name: str = 'tracking-input-stub'

    def verify(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def assert_bound(self, hwnd: int | None = None) -> None:
        return

    def press_noop(self) -> None:
        return

    def press_key(self, key: str) -> None:
        self.press_calls.append(str(key))

    def key_down(self, key: str) -> None:
        self.down_calls.append(str(key))

    def key_up(self, key: str) -> None:
        self.up_calls.append(str(key))

    def click(self, x: int, y: int) -> None:
        return


@dataclass
class _BindingStub:
    name: str = 'binding-stub'

    def verify(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def snapshot(self) -> WindowBindingStatus:
        return WindowBindingStatus(
            backend='mock',
            verified=True,
            hwnd=1234,
            rect=WindowRect(left=10, top=10, right=400, bottom=300),
        )

    def assert_bound(self) -> None:
        return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frame(*, marker: tuple[int, int] | None) -> Frame:
    w, h = 32, 32
    rgb = bytearray(w * h * 3)
    if marker is not None:
        cx, cy = marker
        for yy in range(max(0, cy - 1), min(h, cy + 2)):
            for xx in range(max(0, cx - 1), min(w, cx + 2)):
                i = (yy * w + xx) * 3
                rgb[i] = 255
                rgb[i + 1] = 0
                rgb[i + 2] = 255
    return Frame(
        width=w,
        height=h,
        monotonic_ts_ns=1,
        digest_hex='digest',
        rgb=b'',
        minimap_detected=True,
        minimap_rgb=bytes(rgb),
        minimap_width=w,
        minimap_height=h,
        minimap_digest_hex='mini',
    )


def _ctx() -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='mock',
        player_marker_rgb='255,0,255',
        player_marker_tol=5,
        player_marker_min_pixels=3,
        player_marker_max_pixels=0,
        cavebot_min_pixel_delta=1,
    )
    return RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.RUNNING),
        telemetry=RuntimeTelemetry(),
    )


def _waypoint(
    *,
    waypoint_id: str = 'wp0',
    x: int = 20,
    y: int = 10,
    z: int = 7,
    radius_px: int = 1,
    max_ticks: int = 20,
    waypoint_type: str = 'walk',
    options: dict[str, Any] | None = None,
) -> Waypoint:
    return Waypoint(
        waypoint_id=waypoint_id,
        x=x,
        y=y,
        z=z,
        radius_px=radius_px,
        max_ticks=max_ticks,
        waypoint_type=waypoint_type,
        options=dict(options or {}),
    )


def _ok_eval(
    distance_before: float = 10.0,
    distance_after: float = 8.0,
    angle: float = 0.0,
    marker_after_pos: tuple[int, int] = (12, 10),
) -> CavebotProgressEval:
    import runtime.cavebot_runner as runner

    return CavebotProgressEval(
        progress=ProgressResult(
            distance_before_px=distance_before,
            distance_after_px=distance_after,
            angle_deg=angle,
            moved_toward_waypoint=True,
        ),
        status='ok',
        marker_after=runner.MinimapMarker(x_px=marker_after_pos[0], y_px=marker_after_pos[1], pixel_count=9),
        sel_after_confidence=1.0,
        sel_after_candidate_id=0,
        sel_after_candidates=(),
        sel_after_details={},
        inferred_dx=0,
        inferred_dy=0,
        inferred_sad_best=0.0,
        inferred_sad_0=0.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hold_key_always_active_uses_key_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold-key is unconditional: key_down is used regardless of FRBOT_INPUT_METHOD."""
    import runtime.cavebot_runner as runner

    monkeypatch.delenv('FRBOT_INPUT_METHOD', raising=False)
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: None)
    monkeypatch.setattr(runner, '_progress_from_frames', lambda ctx, bef, aft, wp: _ok_eval())

    ctx = _ctx()
    input_ = _TrackingInputStub()
    execute_cavebot_tick(
        ctx,
        capture=_CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(12, 10))]),
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(x=20, y=10),
        tick_index=0,
    )

    assert len(input_.press_calls) == 0
    assert input_.down_calls == ['RIGHT']
    assert len(input_.up_calls) == 0
    assert ctx.cavebot_gate.telemetry.held_key == 'RIGHT'


def test_hold_key_sendinput_first_tick_uses_key_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """In sendinput mode, first tick for a direction calls key_down and sets held_key."""
    import runtime.cavebot_runner as runner

    monkeypatch.setenv('FRBOT_INPUT_METHOD', 'sendinput')
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: None)
    monkeypatch.setattr(runner, '_progress_from_frames', lambda ctx, bef, aft, wp: _ok_eval())

    ctx = _ctx()
    input_ = _TrackingInputStub()
    execute_cavebot_tick(
        ctx,
        capture=_CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(12, 10))]),
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(x=20, y=10),
        tick_index=0,
    )

    assert len(input_.press_calls) == 0
    assert input_.down_calls == ['RIGHT']
    assert len(input_.up_calls) == 0
    assert ctx.cavebot_gate.telemetry.held_key == 'RIGHT'


def test_hold_key_sendinput_same_direction_skips_physical_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """In sendinput mode, consecutive tick with same direction sends no physical input."""
    import runtime.cavebot_runner as runner

    monkeypatch.setenv('FRBOT_INPUT_METHOD', 'sendinput')
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: None)
    monkeypatch.setattr(runner, '_progress_from_frames', lambda ctx, bef, aft, wp: _ok_eval())

    ctx = _ctx()
    ctx.cavebot_gate.telemetry.held_key = 'RIGHT'  # Already held from prior tick
    input_ = _TrackingInputStub()
    execute_cavebot_tick(
        ctx,
        capture=_CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(12, 10))]),
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(x=20, y=10),
        tick_index=1,
    )

    assert len(input_.press_calls) == 0
    assert len(input_.down_calls) == 0, 'no key_down if already held in same direction'
    assert len(input_.up_calls) == 0
    assert ctx.cavebot_gate.telemetry.held_key == 'RIGHT'


def test_hold_key_sendinput_direction_change_releases_old_holds_new(monkeypatch: pytest.MonkeyPatch) -> None:
    """In sendinput mode, direction change: key_up old key then key_down new key."""
    import runtime.cavebot_runner as runner

    monkeypatch.setenv('FRBOT_INPUT_METHOD', 'sendinput')
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: None)
    monkeypatch.setattr(runner, '_progress_from_frames', lambda ctx, bef, aft, wp: _ok_eval(
        marker_after_pos=(10, 12),
    ))

    ctx = _ctx()
    ctx.cavebot_gate.telemetry.held_key = 'RIGHT'  # Previously holding RIGHT
    input_ = _TrackingInputStub()
    # Waypoint directly below: same x, higher y → DOWN
    execute_cavebot_tick(
        ctx,
        capture=_CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(10, 12))]),
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(x=10, y=20),
        tick_index=2,
    )

    assert input_.up_calls == ['RIGHT']
    assert input_.down_calls == ['DOWN']
    assert len(input_.press_calls) == 0
    assert ctx.cavebot_gate.telemetry.held_key == 'DOWN'


def test_hold_key_sendinput_terminal_abort_releases_held_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """In sendinput mode, a terminal abort (stuck_detected) releases the held movement key."""
    import runtime.cavebot_runner as runner

    monkeypatch.setenv('FRBOT_INPUT_METHOD', 'sendinput')
    monkeypatch.setenv('FRBOT_CAVEBOT_STUCK_WINDOW', '3')
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: None)
    monkeypatch.setattr(runner, '_progress_from_frames', lambda ctx, bef, aft, wp: CavebotProgressEval(
        progress=ProgressResult(
            distance_before_px=10.0,
            distance_after_px=10.0,
            angle_deg=0.0,
            moved_toward_waypoint=False,
        ),
        status='cavebot_no_progress',
        marker_after=runner.MinimapMarker(x_px=10, y_px=10, pixel_count=9),
        sel_after_confidence=1.0,
        sel_after_candidate_id=0,
        sel_after_candidates=(),
        sel_after_details={},
        inferred_dx=0,
        inferred_dy=0,
        inferred_sad_best=0.0,
        inferred_sad_0=0.0,
    ))

    ctx = _ctx()
    ctx.cavebot_gate.telemetry.held_key = 'RIGHT'
    ctx.cavebot_gate.telemetry.last_n_distances = [10.0, 10.0]
    input_ = _TrackingInputStub()

    out = execute_cavebot_tick(
        ctx,
        capture=_CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(10, 10))]),
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(),
        tick_index=5,
    )

    assert out.abort_reason == 'cavebot_stuck_detected'
    assert 'RIGHT' in input_.up_calls, 'key_up must be called on terminal abort'
    assert ctx.cavebot_gate.telemetry.held_key is None
