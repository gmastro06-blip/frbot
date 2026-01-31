from __future__ import annotations

import os
import time

from contracts.errors import PreflightFailed
from contracts.engine import TickInput
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from core.engine import tick as engine_tick
from runtime.preflight import preflight


def _load_config_from_env() -> RuntimeConfig:
    mode = os.environ.get('FRBOT_MODE', 'real')
    tick_hz_raw = os.environ.get('FRBOT_TICK_HZ', '20')
    return RuntimeConfig(mode=mode, tick_hz=float(tick_hz_raw))


def run() -> int:
    try:
        cfg = _load_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_ = preflight(ctx)

        # Only configure runtime logging AFTER preflight succeeds.
        logger = configure_logger()

        if capture is None:
            raise PreflightFailed('preflight did not provide a capture adapter')

        # If preflight returned, we are READY with verified adapters.
        ctx.status.state = RuntimeState.RUNNING

        tick_period = 1.0 / ctx.config.tick_hz
        max_age_ms = int(os.environ.get('FRBOT_FRAME_MAX_AGE_MS', '500'))
        start_ns = time.monotonic_ns()

        # Deterministic loop: runs 1s, and logs progress persistently.
        while True:
            frame = capture.grab()  # verified by preflight
            now_ns = time.monotonic_ns()

            age_ns = now_ns - frame.monotonic_ts_ns
            capture_age_ms = int(age_ns // 1_000_000)

            ctx.telemetry.last_frame_ts_ns = frame.monotonic_ts_ns
            tick_input = TickInput(
                now_ts_ns=now_ns,
                frame_ts_ns=frame.monotonic_ts_ns,
                capture_age_ms=capture_age_ms,
                max_capture_age_ms=max_age_ms,
            )
            out = engine_tick(ctx, tick_input)
            if not out.ok:
                raise PreflightFailed(out.abort_reason or 'engine abort')

            logger.info(
                'mode=%s tick_count=%d capture_age_ms=%d tick_valid=%s frame_ts_ns=%d now_ts_ns=%d',
                ctx.config.mode,
                ctx.telemetry.tick_count,
                ctx.telemetry.last_capture_age_ms,
                ctx.telemetry.last_tick_valid,
                frame.monotonic_ts_ns,
                now_ns,
            )

            if (time.monotonic_ns() - start_ns) >= 1_000_000_000:
                logger.info('completed tick_count=%d', ctx.telemetry.tick_count)
                return 0

            time.sleep(tick_period)

    except PreflightFailed as exc:
        write_fatal(str(exc), exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
