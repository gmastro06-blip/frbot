from __future__ import annotations

from types import SimpleNamespace
import pytest

from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeTelemetry, Waypoint
import runtime.cavebot_runner as runner
from contracts.errors import PreflightFailed


def _ctx() -> RuntimeContext:
    cfg = RuntimeConfig(mode='mock', player_marker_rgb='255,0,255', player_marker_tol=5, player_marker_min_pixels=3, player_marker_max_pixels=0, cavebot_min_pixel_delta=1)
    ctx = RuntimeContext(config=cfg, status=SimpleNamespace(), telemetry=RuntimeTelemetry())
    # minimal cavebot_gate with telemetry
    ctx.cavebot_gate = SimpleNamespace(telemetry=SimpleNamespace())
    return ctx


def _wp() -> Waypoint:
    return Waypoint(waypoint_id='wp', x=10, y=10, z=7, radius_px=1, max_ticks=5, waypoint_type='walk', options={})


def test_emit_and_raise_abort_attaches_details(monkeypatch) -> None:
    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))

    ctx = _ctx()
    wp = _wp()

    details = {'reason': 'test', 'extra': 123}

    with pytest.raises(PreflightFailed) as ei:
        runner._emit_and_raise_abort(
            ctx=ctx,
            input_=None,
            gate='cavebot',
            tick_index=1,
            abort_reason='cavebot_test_abort',
            blocked_reason='none',
            pnf=False,
            inputs_sent=0,
            last_keys_sent=[],
            before_marker=None,
            after_marker=None,
            distance_before_px=0.0,
            distance_after_px=0.0,
            angle_deg=0.0,
            waypoint=wp,
            details=details,
        )

    exc = ei.value
    assert isinstance(exc, PreflightFailed)
    assert hasattr(exc, 'details')
    assert exc.details.get('reason') == 'test'


def test_emit_abort_return_includes_extra_payload(monkeypatch) -> None:
    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))
    ctx = _ctx()
    wp = _wp()

    out = runner._emit_abort_return(
        ctx=ctx,
        input_=None,
        gate='cavebot',
        tick_index=2,
        abort_reason='cavebot_test_abort2',
        blocked_reason='none',
        pnf=False,
        inputs_sent=1,
        last_keys_sent=['X'],
        before_marker=None,
        after_marker=None,
        distance_before_px=1.0,
        distance_after_px=2.0,
        angle_deg=0.0,
        waypoint=wp,
        extra={'foo': 'bar'},
    )

    assert out.abort_reason == 'cavebot_test_abort2'
    assert traces
    abort = traces[-1]
    assert abort['event'] == 'abort'
    assert abort.get('foo') == 'bar'


def test_input_contract_violation_raised_when_override_has_multiple_keys(monkeypatch) -> None:
    # This test uses the tick_keys_override test hook to simulate a contract violation
    traces: list[dict] = []
    monkeypatch.setattr(runner, '_append_trace', lambda *, gate, payload: traces.append(dict(payload)))
    ctx = _ctx()

    # Prepare capture that will be used for a special-action waypoint so the code reaches the contract check
    class _Capture:
        def __init__(self):
            self.called = 0

        def grab(self):
            self.called += 1
            # minimal frame with detected minimap
            from contracts.capture import Frame
            return Frame(width=32, height=32, monotonic_ts_ns=1, digest_hex='d', rgb=b'', minimap_detected=True, minimap_rgb=b'\x00'*32*32*3, minimap_width=32, minimap_height=32, minimap_digest_hex='m')

    capture = _Capture()

    # Make waypoint a special action and ensure special key mapping returns a key
    from contracts.runtime import Waypoint as _WP
    wp = _WP(waypoint_id='wp', x=10, y=10, z=7, radius_px=1, max_ticks=5, waypoint_type='rope', options={'action_kind': 'rope'})
    monkeypatch.setattr(runner, '_special_action_key_for_waypoint', lambda waypoint: 'F8')

    with pytest.raises(Exception):
        runner.execute_cavebot_tick(
            ctx,
            capture=capture,
            input_=SimpleNamespace(press_key=lambda k: None, press_noop=lambda: None, key_down=lambda k: None, key_up=lambda k: None, auto_walk_tick=lambda k: None),
            binding=SimpleNamespace(assert_bound=lambda: None, snapshot=lambda: {}),
            waypoint=wp,
            tick_index=1,
            gate='cavebot',
            tick_keys_override=['A', 'B'],
        )
