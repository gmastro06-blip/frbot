from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.capture import Frame
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry, Waypoint
from contracts.window import WindowBindingStatus, WindowRect
from runtime.cavebot_runner import CavebotProgressEval, execute_cavebot_tick
from runtime.cavebot_semantics import ProgressResult


@dataclass
class _CaptureStub:
    frames: list[Frame]
    name: str = 'capture-stub'

    def verify(self) -> None:
        raise NotImplementedError

    def grab(self) -> Frame:
        if not self.frames:
            raise AssertionError('no frames configured')
        if len(self.frames) == 1:
            return self.frames[0]
        return self.frames.pop(0)


@dataclass
class _InputStub:
    keys: list[str]
    name: str = 'input-stub'

    def verify(self) -> None:
        raise NotImplementedError

    def assert_bound(self, hwnd: int | None = None) -> None:
        return

    def press_noop(self) -> None:
        return

    def press_key(self, key: str) -> None:
        self.keys.append(str(key))

    def click(self, x: int, y: int) -> None:
        return


@dataclass
class _BindingStub:
    name: str = 'binding-stub'

    def verify(self) -> None:
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


def _frame(*, marker: tuple[int, int] | None) -> Frame:
    w = 32
    h = 32
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
    return RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.RUNNING), telemetry=RuntimeTelemetry())


def _waypoint(
    *,
    waypoint_id: str = 'wp0',
    x: int = 20,
    y: int = 20,
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


def test_blocked_move_key_no_effect_sets_trace_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.cavebot_runner as runner

    traces: list[dict] = []
    monkeypatch.setenv('FRBOT_CAVEBOT_STUCK_WINDOW', '3')
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))

    def _fake_eval(ctx: RuntimeContext, before_f: Frame, after_f: Frame, waypoint: Waypoint) -> CavebotProgressEval:
        return CavebotProgressEval(
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
        )

    monkeypatch.setattr(runner, '_progress_from_frames', _fake_eval)

    ctx = _ctx()
    ctx.cavebot_gate.telemetry.last_n_distances = [10.0, 10.0]
    capture = _CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(10, 10))])
    input_ = _InputStub(keys=[])

    out = execute_cavebot_tick(
        ctx,
        capture=capture,
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(),
        tick_index=5,
        gate='cavebot_full',
    )

    assert out.abort_reason == 'cavebot_stuck_detected'
    abort = [p for p in traces if p.get('event') == 'abort'][-1]
    assert abort['blocked_reason'] == 'move_key_no_effect'
    assert abort['pnf'] is False
    assert abort['inputs_sent'] == 1
    assert len(abort['last_keys_sent']) == 1


def test_blocked_roi_invalid_uses_minimap_roi_black_or_static(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.cavebot_runner as runner

    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))

    def _fake_select(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            marker=None,
            candidates=(),
            selected_candidate_id=None,
            confidence=0.0,
            abort_reason='cavebot_marker_roi_black',
            details={'full_std_luma': 30.0, 'roi_std_luma': 0.2},
        )

    monkeypatch.setattr(runner, 'select_player_marker', _fake_select)

    ctx = _ctx()
    capture = _CaptureStub(frames=[_frame(marker=None), _frame(marker=None)])
    input_ = _InputStub(keys=[])

    with pytest.raises(Exception):
        execute_cavebot_tick(
            ctx,
            capture=capture,
            input_=input_,
            binding=_BindingStub(),
            waypoint=_waypoint(),
            tick_index=1,
            gate='cavebot_full',
        )

    abort = [p for p in traces if p.get('event') == 'abort'][-1]
    assert abort['blocked_reason'] == 'roi_invalid'
    assert abort['roi_sanity_reason'] == 'minimap_roi_black_or_static'
    assert abort['inputs_sent'] == 0


def test_blocked_path_not_found_when_wrong_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.cavebot_runner as runner

    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))

    def _fake_eval(ctx: RuntimeContext, before_f: Frame, after_f: Frame, waypoint: Waypoint) -> CavebotProgressEval:
        return CavebotProgressEval(
            progress=ProgressResult(
                distance_before_px=8.0,
                distance_after_px=9.0,
                angle_deg=180.0,
                moved_toward_waypoint=False,
            ),
            status='cavebot_wrong_direction',
            marker_after=runner.MinimapMarker(x_px=9, y_px=9, pixel_count=9),
            sel_after_confidence=1.0,
            sel_after_candidate_id=0,
            sel_after_candidates=(),
            sel_after_details={},
            inferred_dx=0,
            inferred_dy=0,
            inferred_sad_best=0.0,
            inferred_sad_0=0.0,
        )

    monkeypatch.setattr(runner, '_progress_from_frames', _fake_eval)

    ctx = _ctx()
    capture = _CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(9, 9))])
    input_ = _InputStub(keys=[])

    out = execute_cavebot_tick(
        ctx,
        capture=capture,
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(),
        tick_index=2,
        gate='cavebot_full',
    )

    assert out.abort_reason == 'cavebot_wrong_direction'
    abort = [p for p in traces if p.get('event') == 'abort'][-1]
    assert abort['blocked_reason'] == 'path_not_found'
    assert abort['pnf'] is True
    assert abort['inputs_sent'] == 1


def test_blocked_needs_special_action_without_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.cavebot_runner as runner

    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))
    monkeypatch.setattr(runner, '_special_action_key_for_waypoint', lambda waypoint: '')

    ctx = _ctx()
    capture = _CaptureStub(frames=[_frame(marker=(10, 10))])
    input_ = _InputStub(keys=[])

    out = execute_cavebot_tick(
        ctx,
        capture=capture,
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(waypoint_type='rope', options={'action_kind': 'rope'}),
        tick_index=3,
        gate='cavebot_full',
    )

    assert out.abort_reason == 'cavebot_needs_special_action'
    abort = [p for p in traces if p.get('event') == 'abort'][-1]
    assert abort['blocked_reason'] == 'needs_special_action'
    assert abort['pnf'] is True
    assert abort['inputs_sent'] == 0


def test_special_action_executes_single_input_and_emits_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.cavebot_runner as runner

    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))

    ctx = _ctx()
    capture = _CaptureStub(frames=[_frame(marker=(10, 10)), _frame(marker=(12, 10))])
    input_ = _InputStub(keys=[])

    out = execute_cavebot_tick(
        ctx,
        capture=capture,
        input_=input_,
        binding=_BindingStub(),
        waypoint=_waypoint(waypoint_type='rope', options={'action_kind': 'rope'}),
        tick_index=4,
        gate='cavebot_full',
    )

    assert out.reached_waypoint is True
    assert len(input_.keys) == 1
    action_event = [p for p in traces if p.get('event') == 'WAYPOINT_ACTION'][-1]
    assert action_event['inputs_sent'] == 1
    assert len(action_event['last_keys_sent']) == 1
    assert action_event['blocked_reason'] == 'none'
