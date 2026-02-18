from __future__ import annotations

import os
import sys
import time

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from runtime.env_bootstrap import load_repo_env
from runtime.fish_guard import FishSettings, is_fish_biting, parse_rgb, should_fish, auto_fish_tick
from runtime.pacing import wait_until_ns
from runtime.preflight import preflight
from runtime.profile import cap_ticks


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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def _load_fish_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_FISH_BACKEND', 'real') or 'real').strip().lower()
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


def _load_fish_settings_from_env() -> FishSettings:
    return FishSettings(
        roi_name=str(_env_str('FRBOT_FISH_ROI', 'fishing_indicator') or 'fishing_indicator').strip() or 'fishing_indicator',
        fish_key=str(_env_str('FRBOT_FISH_KEY', 'F10') or 'F10').strip() or 'F10',
        bite_rgb=parse_rgb(_env_str('FRBOT_FISH_BITE_RGB', '0,255,0'), (0, 255, 0)),
        color_tol=max(0, min(255, int(_env_int('FRBOT_FISH_RGB_TOL', 30)))),
        match_ratio_min=max(0.0, min(1.0, float(_env_float('FRBOT_FISH_MATCH_RATIO_MIN', 0.05)))),
        fish_interval_ms=max(0, int(_env_int('FRBOT_FISH_INTERVAL_MS', 2000))),
        bite_timeout_ms=max(0, int(_env_int('FRBOT_FISH_BITE_TIMEOUT_MS', 5000))),
    )


def run_fish_only() -> int:
    max_total_ticks = cap_ticks(_env_int('FRBOT_FISH_MAX_TICKS', 1200))

    try:
        cfg = _load_fish_config_from_env()
        settings = _load_fish_settings_from_env()

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
            raise PreflightFailed('fish_roi_missing')

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        next_tick_ns = time.monotonic_ns()

        last_fish_ts_ms: int | None = None
        fish_count: int = 0
        bite_count: int = 0

        for tick_index in range(int(max_total_ticks)):
            try:
                binding.assert_bound()
            except Exception:
                raise PreflightFailed('fish_window_binding_lost')

            frame = capture.grab()
            now_ms = int(time.monotonic_ns() // 1_000_000)

            fished, new_ts, bite_ratio = auto_fish_tick(
                frame, roi, settings, now_ms, last_fish_ts_ms
            )

            pressed = False
            if fished:
                try:
                    binding.assert_bound()
                    snap = binding.snapshot()
                    input_.assert_bound(int(getattr(snap, 'hwnd', 0)))
                    input_.press_key(str(settings.fish_key))
                except Exception as exc:
                    raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc
                pressed = True
                last_fish_ts_ms = now_ms
                fish_count += 1

            if bite_ratio > 0:
                bite_count += 1

            log_json(
                logger,
                event='tick',
                gate='fish',
                tick_index=int(tick_index),
                status='RUNNING',
                fished=bool(pressed),
                bite_ratio=float(bite_ratio),
                fish_key=str(settings.fish_key),
                fish_count=fish_count,
                bite_count=bite_count,
            )

            ctx.telemetry.tick_count += 1

            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

        log_json(logger, event='completed', gate='fish', status='SUCCESS', ticks=int(max_total_ticks), fish_count=fish_count, bite_count=bite_count)
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
    raise SystemExit(run_fish_only())
