from __future__ import annotations

from contracts.engine import EngineInput, EngineOutput, EngineTelemetry

from rules.cavebot import select_cavebot_intent


def tick(inp: EngineInput, *, enable_cavebot: bool) -> EngineOutput:
    """Pure engine tick: state -> state + intent.

    Tick is valid iff capture_age_ms <= max_capture_age_ms.
    """
    prev_count = int(inp.tick_count)
    last_tick_valid = inp.capture_age_ms <= inp.max_capture_age_ms
    telemetry = EngineTelemetry(
        tick_count=prev_count + 1,
        last_capture_age_ms=int(inp.capture_age_ms),
        last_tick_valid=bool(last_tick_valid),
    )

    if not telemetry.last_tick_valid:
        return EngineOutput(
            intents=(), telemetry=telemetry, abort_reason="capture stale"
        )

    res = select_cavebot_intent(inp, enable_cavebot=enable_cavebot)
    if res.abort_reason:
        return EngineOutput(
            intents=(), telemetry=telemetry, abort_reason=res.abort_reason
        )
    if res.intent is not None:
        return EngineOutput(intents=(res.intent,), telemetry=telemetry)

    return EngineOutput(intents=(), telemetry=telemetry)
