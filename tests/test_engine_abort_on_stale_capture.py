from __future__ import annotations

from contracts.engine import Observation, TickInput
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from core.engine import tick


def test_engine_aborts_on_stale_capture() -> None:
    ctx = RuntimeContext(
        config=RuntimeConfig(mode='mock'),
        status=RuntimeStatus(state=RuntimeState.RUNNING),
        telemetry=RuntimeTelemetry(),
    )

    out = tick(
        ctx,
        TickInput(
            now_ts_ns=1_000_000_000,
            frame_ts_ns=0,
            capture_age_ms=10_000,
            max_capture_age_ms=500,
            observation=Observation(),
        ),
    )

    assert not out.ok
    assert out.abort_reason == 'capture stale'
    assert out.telemetry is not None
    assert out.telemetry.last_tick_valid is False
