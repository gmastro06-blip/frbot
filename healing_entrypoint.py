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
from rules.healing import select_heal_intent
from runtime.healing_preflight import run as healing_preflight_run
from runtime.healing_runner import _cooldown_ok_to_cast, _read_hp_mp, execute_heal_intent
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


def _load_healing_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_HEALING_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=_env_bool('FRBOT_ENABLE_HEALING', True),

        hp_bar_roi=_env_str('FRBOT_HP_BAR_ROI', 'hp_bar'),
        mp_bar_roi=_env_str('FRBOT_MP_BAR_ROI', 'mp_bar'),
        hp_text_roi=_env_str('FRBOT_HP_TEXT_ROI', 'hp_text'),
        mp_text_roi=_env_str('FRBOT_MP_TEXT_ROI', 'mp_text'),
        heal_cooldown_roi=_env_str('FRBOT_HEAL_COOLDOWN_ROI', 'heal_cooldown'),
        heal_feedback_roi=_env_str('FRBOT_HEAL_FEEDBACK_ROI', 'combat_feedback'),

        heal_hp_threshold=_env_float('FRBOT_HEAL_HP_THRESHOLD', 0.5),
        heal_mp_min=_env_float('FRBOT_HEAL_MP_MIN', 0.0),
        heal_mp_cost=_env_float('FRBOT_HEAL_MP_COST', 0.0),
        heal_hp_increase_min=_env_float('FRBOT_HEAL_HP_INCREASE_MIN', 0.02),
        heal_consistency_tol=_env_float('FRBOT_HEAL_CONSISTENCY_TOL', 0.05),

        heal_key=_env_str('FRBOT_HEAL_KEY', 'F1'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),

        max_attempts_per_heal=_env_int('FRBOT_MAX_ATTEMPTS_PER_HEAL', 2),
        max_time_ms_per_heal=_env_int('FRBOT_MAX_TIME_MS_PER_HEAL', 2500),
    )


def run_healing_only() -> int:
    """Healing-only executable mode.

    - No targeting, no movement, no cavebot.
    - Evidence-or-abort.
    - Finite time: max ticks.
    """

    max_total_ticks = cap_ticks(_env_int('FRBOT_HEALING_MAX_TICKS', 30))

    try:
        cfg = _load_healing_config_from_env()

        # Mock backend is used in CI on Linux; only REAL runs are Windows-only.
        if cfg.mode.strip().lower() == 'real' and sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = healing_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        next_tick_ns = time.monotonic_ns()

        for tick_index in range(int(max_total_ticks)):
            try:
                binding.assert_bound()
            except Exception:
                raise PreflightFailed('healing_window_binding_lost')

            frame = capture.grab()
            hp, mp, src = _read_hp_mp(ctx, frame)

            # Cooldown must be semantically observable every tick.
            ok_to_cast = _cooldown_ok_to_cast(ctx, frame)

            res = select_heal_intent(
                hp=hp,
                mp=mp,
                hp_threshold=float(ctx.config.heal_hp_threshold),
                mp_min=float(ctx.config.heal_mp_min),
                mp_cost=float(ctx.config.heal_mp_cost),
                heal_key=str(ctx.config.heal_key),
                hp_increase_min=float(ctx.config.heal_hp_increase_min),
            )
            if res.abort_reason is not None:
                raise PreflightFailed(res.abort_reason)

            # Deterministic success: if no heal is required and HP is readable, exit.
            if res.intent is None and float(hp) > float(ctx.config.heal_hp_threshold):
                log_json(
                    logger,
                    event='success',
                    gate='healing',
                    status='SUCCESS',
                    result='no_heal_needed',
                    hp=float(hp),
                    mp=float(mp),
                    source=str(src),
                )
                return 0

            healed = False
            if res.intent is not None:
                if not ok_to_cast:
                    # Cooldown is observable and active: abort (no casts).
                    log_json(
                        logger,
                        event='tick',
                        gate='healing',
                        tick_index=int(tick_index),
                        hp=float(hp),
                        mp=float(mp),
                        source=str(src),
                        intent='HealIntent',
                        attempts_used=int(ctx.healing.attempt_count),
                        cooldown_ok=bool(ok_to_cast),
                        healed=False,
                    )
                    raise PreflightFailed('heal_on_cooldown')

                try:
                    healed = execute_heal_intent(ctx, capture=capture, input_=input_, binding=binding, intent=res.intent)
                except PreflightFailed as exc:
                    # Emit a final per-tick audit line before aborting.
                    if str(exc) == 'heal_unverified':
                        log_json(
                            logger,
                            event='tick',
                            gate='healing',
                            tick_index=int(tick_index),
                            hp=float(hp),
                            mp=float(mp),
                            source=str(src),
                            intent='HealIntent',
                            attempts_used=int(ctx.healing.attempt_count),
                            cooldown_ok=True,
                            healed=False,
                        )
                    raise

                if healed:
                    log_json(
                        logger,
                        event='success',
                        gate='healing',
                        status='SUCCESS',
                        result='healed',
                        hp=float(hp),
                        mp=float(mp),
                        source=str(src),
                    )
                    return 0

            log_json(
                logger,
                event='tick',
                gate='healing',
                tick_index=int(tick_index),
                hp=float(hp),
                mp=float(mp),
                source=str(src),
                intent=('HealIntent' if res.intent is not None else 'None'),
                attempts_used=int(ctx.healing.attempt_count),
                cooldown_ok=bool(ok_to_cast),
                healed=bool(healed),
            )

            ctx.telemetry.tick_count += 1
            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

        raise PreflightFailed('heal_not_acquired')

    except PreflightFailed as exc:
        if dump_enabled():
            before, after = snapshot('healing')
            if before is not None or after is not None:
                dump_pair(gate='healing', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='healing', reason=str(exc))
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
