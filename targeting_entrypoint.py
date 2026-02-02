from __future__ import annotations

import os
import sys
import time

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from rules.targeting import select_targeting_intent
from runtime.battle_list_semantics import detect_battle_list
from runtime.targeting_preflight import run as targeting_preflight_run
from runtime.targeting_runner import execute_intent
from runtime.profile import cap_ticks
from runtime.pacing import wait_until_ns


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else raw


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip() not in {'0', 'false', 'no', 'off'}


def _load_targeting_config_from_env() -> RuntimeConfig:
    # FRBOT_MODE=targeting is the *feature* mode.
    # Backend adapters still operate in {real,mock}.
    backend = (_env_str('FRBOT_TARGETING_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=_env_bool('FRBOT_ENABLE_TARGETING', True),

        battle_list_roi=_env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),

        max_attempts_per_target=_env_int('FRBOT_MAX_ATTEMPTS_PER_TARGET', 2),
        max_time_ms_per_target=_env_int('FRBOT_MAX_TIME_MS_PER_TARGET', 2500),
    )


def run_targeting_only() -> int:
    """Targeting-only executable mode.

    Deterministic, auditable, abort-first.
    - No movement, no minimap, no spells.
    - Terminates in finite time (max_total_ticks).
    """

    max_total_ticks = cap_ticks(_env_int('FRBOT_TARGETING_MAX_TICKS', 30))

    try:
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_targeting_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        # Preflight must run before we create runtime.log.
        capture, input_, binding = targeting_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        next_tick_ns = time.monotonic_ns()

        for tick_index in range(int(max_total_ticks)):
            # Binding hard gate each tick.
            try:
                binding.assert_bound()
            except Exception:
                raise PreflightFailed('targeting_window_binding_lost')

            frame = capture.grab()

            battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
            if battle_roi is None:
                raise PreflightFailed('battle_list_not_detected')

            obs = detect_battle_list(frame, battle_roi)
            if obs is None:
                raise PreflightFailed('battle_list_not_detected')

            # Candidates defined by the invariant filter.
            candidates = tuple(
                e
                for e in obs.entries
                if bool(e.name) and e.is_attackable is True and e.hp_bar_visible is True
            )

            res = select_targeting_intent(ctx.targeting.target, obs.entries)
            if res.abort_reason is not None:
                raise PreflightFailed(res.abort_reason)

            selected_name = res.intent.target_name if res.intent is not None else None

            if res.intent is not None:
                execute_intent(ctx, capture=capture, input_=input_, binding=binding, intent=res.intent)

            log_json(
                logger,
                event='tick',
                gate='targeting',
                tick_index=int(tick_index),
                candidates_count=int(len(candidates)),
                selected_target=str(selected_name),
                attempts_used=int(ctx.targeting.attempt_count),
                locked=bool(ctx.targeting.target.locked),
            )

            ctx.telemetry.tick_count += 1

            if ctx.targeting.target.locked:
                log_json(logger, event='success', gate='targeting', target=str(ctx.targeting.target.target_name))
                return 0

            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

        raise PreflightFailed('target_not_acquired')

    except PreflightFailed as exc:
        if dump_enabled():
            before, after = snapshot('targeting')
            if before is not None or after is not None:
                dump_pair(gate='targeting', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='targeting', reason=str(exc))
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
