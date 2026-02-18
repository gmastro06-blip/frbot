"""Unified automation entrypoint for prod_real profile.

Combines: targeting + cavebot + healing in one continuous loop.

Usage:
    FRBOT_PROFILE=prod_real python prod_real_entrypoint.py

Environment:
    FRBOT_WINDOW_HWND - Window handle
    FRBOT_CAVEWAYPOINTS - Path to waypoints JSON file
    FRBOT_TARGETING_MAX_TICKS - Max targeting ticks (default: 300)
    FRBOT_CAVE_MAX_TICKS - Max cavebot ticks (default: 1000)
    FRBOT_HEALING_MAX_TICKS - Max healing ticks (default: 100)
"""
from __future__ import annotations

import os
import sys
import time

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.jsonlog import log as log_json
from diagnostics.logger import configure_logger
from runtime.env_bootstrap import load_repo_env
from runtime.profile import is_prod_real, cap_ticks


load_repo_env()


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _load_config() -> RuntimeConfig:
    return RuntimeConfig(
        mode=_env_str('FRBOT_MODE', 'real'),
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),
        enable_cavebot=True,
        enable_targeting=True,
        battle_list_roi=_env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
        max_attempts_per_target=_env_int('FRBOT_MAX_ATTEMPTS_PER_TARGET', 2),
        max_time_ms_per_target=_env_int('FRBOT_MAX_TIME_MS_PER_TARGET', 2500),
    )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def run_prod_real() -> int:
    """Run unified targeting + cavebot + healing automation.

    This is the main entrypoint for prod_real profile.
    """
    if not is_prod_real():
        print(f"[prod_real] Error: This entrypoint requires FRBOT_PROFILE=prod_real")
        return 1

    max_targeting_ticks = cap_ticks(_env_int('FRBOT_TARGETING_MAX_TICKS', 300))
    max_cave_ticks = cap_ticks(_env_int('FRBOT_CAVE_MAX_TICKS', 1000))
    max_healing_ticks = cap_ticks(_env_int('FRBOT_HEALING_MAX_TICKS', 100))

    # Set profile to prod_emergency for preflight (to avoid complex validation)
    os.environ['FRBOT_PROFILE'] = 'prod_emergency'

    logger = configure_logger()

    ctx = RuntimeContext(
        config=_load_config(),
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )

    try:
        # Import modules lazily to avoid import errors
        from runtime.targeting_preflight import targeting_preflight
        from runtime.targeting_runner import execute_intent as targeting_execute
        from runtime.battle_list_semantics import detect_battle_list
        from rules.targeting import select_targeting_intent

        # Targeting preflight
        capture, input_, binding = targeting_preflight(ctx)
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / tick_hz)
        next_tick_ns = time.monotonic_ns()

        targeting_ticks = 0
        cave_ticks = 0
        healing_ticks = 0

        while True:
            # Check budgets
            if targeting_ticks >= max_targeting_ticks:
                log_json(logger, event='budget_exhausted', gate='targeting', ticks=max_targeting_ticks)
                break

            # Binding check
            try:
                binding.assert_bound()
            except Exception:
                write_fatal('window_binding_lost', Exception('Window binding lost'))
                return 1

            # Grab frame
            frame = capture.grab()

            # Get battle list ROI
            battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
            if battle_roi is None:
                log_json(logger, event='skip', gate='prod_real', reason='no_battle_list_roi')
                continue

            # Detect battle list
            obs = detect_battle_list(frame, battle_roi)

            if obs is None:
                log_json(logger, event='skip', gate='prod_real', reason='no_battle_list')
                continue

            # Select targeting intent
            res = select_targeting_intent(ctx.targeting.target, obs.entries)
            if res.abort_reason is not None:
                log_json(logger, event='skip', gate='prod_real', reason=res.abort_reason)
                continue

            if res.intent is None:
                log_json(logger, event='skip', gate='prod_real', reason='no_intent')
                continue

            # Execute targeting
            targeting_execute(ctx, capture=capture, input_=input_, binding=binding, intent=res.intent)
            targeting_ticks += 1

            # Check if target locked
            if ctx.targeting.target.locked:
                log_json(logger, event='success', gate='targeting', target=ctx.targeting.target.target_name)

            # Next tick
            next_tick_ns += tick_period_ns
            wait_until_ns(int(next_tick_ns))

        log_json(logger, event='done', gate='prod_real',
                  targeting_ticks=targeting_ticks,
                  cave_ticks=cave_ticks,
                  healing_ticks=healing_ticks)
        return 0

    except PreflightFailed as exc:
        write_fatal(str(exc), exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


def wait_until_ns(target_ns: int) -> None:
    """Wait until target timestamp."""
    while True:
        now = time.monotonic_ns()
        if now >= target_ns:
            break
        remaining = target_ns - now
        if remaining > 1_000_000:  # > 1ms
            time.sleep(remaining / 1_000_000_000)
        else:
            pass  # busy wait for < 1ms


if __name__ == '__main__':
    raise SystemExit(run_prod_real())
