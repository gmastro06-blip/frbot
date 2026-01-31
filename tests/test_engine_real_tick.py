from __future__ import annotations

from contracts.engine import TickInput
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from core.engine import tick


def test_engine_tick_increments_counter_and_records_frame_ts():
    ctx = RuntimeContext(
        config=RuntimeConfig(mode='mock'),
        status=RuntimeStatus(state=RuntimeState.RUNNING),
        telemetry=RuntimeTelemetry(),
    )
    ctx.telemetry.last_frame_ts_ns = 123
    out = tick(
        ctx,
        TickInput(
            now_ts_ns=1_000,
            frame_ts_ns=900,
            capture_age_ms=0,
            max_capture_age_ms=500,
        ),
    )
    assert out.ok
    assert ctx.telemetry.tick_count == 1
    assert ctx.telemetry.last_frame_ts_ns == 123
    assert ctx.telemetry.last_tick_valid is True
