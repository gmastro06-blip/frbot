from __future__ import annotations

from contracts.engine import EngineIntent, EngineOutput, TickInput
from contracts.runtime import RuntimeContext


def tick(ctx: RuntimeContext, tick_input: TickInput) -> EngineOutput:
    """Pure engine tick: state -> state + intent.

    Tick is valid iff capture_age_ms <= max_capture_age_ms.
    """
    ctx.telemetry.tick_count += 1
    ctx.telemetry.last_capture_age_ms = int(tick_input.capture_age_ms)
    ctx.telemetry.last_tick_valid = tick_input.capture_age_ms <= tick_input.max_capture_age_ms

    if not ctx.telemetry.last_tick_valid:
        return EngineOutput(intent=EngineIntent.NOOP, abort_reason='capture stale')

    return EngineOutput(intent=EngineIntent.NOOP)
