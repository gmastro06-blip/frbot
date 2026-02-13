from __future__ import annotations

import os
import sys
import time

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from runtime.hunger_guard import HungerSettings, is_hungry, parse_rgb, should_press_eat
from runtime.pacing import wait_until_ns
from runtime.preflight import preflight
from runtime.profile import cap_ticks


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if name == 'FRBOT_WINDOW_HWND' and raw is not None and str(raw).strip() != '':
        s = str(raw).strip()
        if s.lower().startswith('0x') and len(s) > 2 and set(s[2:].lower()) == {'x'}:
            return int(default)
        try:
            return int(s, 0)
        except Exception as exc:
            raise PreflightFailed('window_hwnd_invalid') from exc
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def _load_hunger_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_HUNGER_BACKEND', 'real') or 'real').strip().lower()
    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 10.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),
        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def _load_hunger_settings_from_env() -> HungerSettings:
    return HungerSettings(
        roi_name=str(_env_str('FRBOT_HUNGER_ROI', 'hunger_status') or 'hunger_status').strip() or 'hunger_status',
        eat_key=str(_env_str('FRBOT_EAT_KEY', 'F9') or 'F9').strip() or 'F9',
        hungry_rgb=parse_rgb(_env_str('FRBOT_HUNGER_RGB', '255,170,0'), (255, 170, 0)),
        color_tol=max(0, min(255, int(_env_int('FRBOT_HUNGER_RGB_TOL', 28)))),
        match_ratio_min=max(0.0, min(1.0, float(_env_float('FRBOT_HUNGER_MATCH_RATIO_MIN', 0.08)))),
        eat_interval_ms=max(0, int(_env_int('FRBOT_EAT_INTERVAL_MS', 1200))),
    )


def run_hunger_only() -> int:
    max_total_ticks = cap_ticks(_env_int('FRBOT_HUNGER_MAX_TICKS', 1200))

    try:
        cfg = _load_hunger_config_from_env()
        settings = _load_hunger_settings_from_env()

        if cfg.mode.strip().lower() == 'real' and sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = preflight(ctx)
        roi = ctx.rois.get(str(settings.roi_name))
        if roi is None:
            raise PreflightFailed('hunger_roi_missing')

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        next_tick_ns = time.monotonic_ns()

        last_eat_ts_ms: int | None = None

        for tick_index in range(int(max_total_ticks)):
            try:
                binding.assert_bound()
            except Exception:
                raise PreflightFailed('hunger_window_binding_lost')

            frame = capture.grab()
            hungry, ratio = is_hungry(frame, roi, settings)

            now_ms = int(time.monotonic_ns() // 1_000_000)
            pressed = False
            if should_press_eat(
                hungry=bool(hungry),
                now_ms=int(now_ms),
                last_eat_ts_ms=last_eat_ts_ms,
                eat_interval_ms=int(settings.eat_interval_ms),
            ):
                try:
                    binding.assert_bound()
                    snap = binding.snapshot()
                    input_.assert_bound(int(getattr(snap, 'hwnd', 0)))
                    input_.press_key(str(settings.eat_key))
                except Exception as exc:
                    raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc
                pressed = True
                last_eat_ts_ms = int(now_ms)

            log_json(
                logger,
                event='tick',
                gate='hunger',
                tick_index=int(tick_index),
                status='RUNNING',
                hungry=bool(hungry),
                hunger_ratio=float(ratio),
                pressed_eat=bool(pressed),
                eat_key=str(settings.eat_key),
            )

            ctx.telemetry.tick_count += 1

            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

        log_json(logger, event='completed', gate='hunger', status='SUCCESS', ticks=int(max_total_ticks))
        return 0

    except PreflightFailed as exc:
        write_fatal(str(exc), exc)
        return 1
    except ContractViolation as exc:
        if 'Unsupported mode:' in str(exc):
            write_fatal('invalid_mode', exc)
            return 1
        write_fatal('runtime crashed', exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run_hunger_only())
