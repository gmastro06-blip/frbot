from __future__ import annotations

from contracts.engine import EngineInput
from contracts.runtime import Tile
from core.engine import tick


def test_engine_emits_cavebot_move_intent() -> None:
    out = tick(
        EngineInput(
            now_ts_ms=1000,
            capture_age_ms=0,
            max_capture_age_ms=500,
            tick_count=0,
            current_position=Tile(x=0, y=0, z=7),
            target_tile=Tile(x=1, y=0, z=7),
            last_positions=(),
        ),
        enable_cavebot=True,
    )

    assert out.ok
    assert out.telemetry is not None
    assert out.telemetry.tick_count == 1
    assert len(out.intents) == 1

    intent = out.intents[0]
    assert intent.reason == 'cavebot'
    assert intent.direction == 'right'
