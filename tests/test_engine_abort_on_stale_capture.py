from __future__ import annotations

from contracts.engine import EngineInput
from contracts.runtime import Tile
from core.engine import tick


def test_engine_aborts_on_stale_capture() -> None:
    inp = EngineInput(
        now_ts_ms=1_000,
        capture_age_ms=10_000,
        max_capture_age_ms=500,
        tick_count=0,
        current_position=Tile(x=0, y=0, z=7, walkable=True),
        target_tile=None,
        last_positions=(),
    )

    out = tick(inp, enable_cavebot=False)

    assert not out.ok
    assert out.abort_reason == 'capture stale'
    assert out.telemetry is not None
    assert out.telemetry.last_tick_valid is False
