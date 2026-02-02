from __future__ import annotations

import os
import time

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from rules.combat import select_combat_intent
from runtime.combat_preflight import run as combat_preflight_run
from runtime.combat_runner import execute_combat_intent


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


def _load_combat_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_COMBAT_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=True,  # reuse HP/MP contract readers
        enable_combat=_env_bool('FRBOT_ENABLE_COMBAT', True),

        battle_list_roi=_env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        target_hp_bar_roi=_env_str('FRBOT_TARGET_HP_BAR_ROI', 'target_hp_bar'),
        combat_cooldown_roi=_env_str('FRBOT_COMBAT_COOLDOWN_ROI', 'combat_cooldown'),
        combat_feedback_roi=_env_str('FRBOT_COMBAT_FEEDBACK_ROI', 'combat_feedback'),

        hp_bar_roi=_env_str('FRBOT_HP_BAR_ROI', 'hp_bar'),
        mp_bar_roi=_env_str('FRBOT_MP_BAR_ROI', 'mp_bar'),
        hp_text_roi=_env_str('FRBOT_HP_TEXT_ROI', 'hp_text'),
        mp_text_roi=_env_str('FRBOT_MP_TEXT_ROI', 'mp_text'),

        heal_consistency_tol=_env_float('FRBOT_HEAL_CONSISTENCY_TOL', 0.05),

        attack_key=_env_str('FRBOT_ATTACK_KEY', 'SPACE'),
        combat_target_hp_decrease_min=_env_float('FRBOT_COMBAT_TARGET_HP_DECREASE_MIN', 0.02),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),

        max_attempts_per_target=_env_int('FRBOT_MAX_ATTEMPTS_PER_TARGET', 2),
        max_time_ms_per_target=_env_int('FRBOT_MAX_TIME_MS_PER_TARGET', 2500),
    )


def run_combat_only() -> int:
    """Combat-only executable mode.

    - Requires pre-locked target (verified each tick)
    - Exactly one attack per intent
    - Semantic evidence-or-abort
    - Finite time
    """

    max_total_ticks = _env_int('FRBOT_COMBAT_MAX_TICKS', 30)

    try:
        cfg = _load_combat_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        # Preflight must run before we create runtime.log.
        capture, input_, binding = combat_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_period = 1.0 / max(1e-6, float(ctx.config.tick_hz))

        for tick_index in range(int(max_total_ticks)):
            try:
                binding.assert_bound()
            except Exception:
                raise PreflightFailed('combat_invalid_state')

            res = select_combat_intent(
                target=ctx.targeting.target,
                attack_key=str(ctx.config.attack_key),
                target_hp_decrease_min=float(ctx.config.combat_target_hp_decrease_min),
            )
            if res.abort_reason is not None:
                log_json(
                    logger,
                    event='tick',
                    gate='combat',
                    tick_index=int(tick_index),
                    intent='CombatIntent',
                    attempts_used=int(ctx.combat.attempt_count),
                    intents_emitted=int(ctx.combat.intents_emitted),
                    inputs_sent=int(ctx.combat.inputs_sent),
                    target=str(ctx.targeting.target.target_name),
                    attacked_ok=False,
                    abort_reason=str(res.abort_reason),
                )
                raise PreflightFailed(res.abort_reason)
            if res.intent is None:
                raise PreflightFailed('combat_invalid_state')

            ctx.combat.intents_emitted += 1

            try:
                attacked_ok = execute_combat_intent(ctx, capture=capture, input_=input_, binding=binding, intent=res.intent)
            except PreflightFailed as exc:
                # Emit a final per-tick audit line before aborting.
                log_json(
                    logger,
                    event='tick',
                    gate='combat',
                    tick_index=int(tick_index),
                    intent='CombatIntent',
                    attempts_used=int(ctx.combat.attempt_count),
                    intents_emitted=int(ctx.combat.intents_emitted),
                    inputs_sent=int(ctx.combat.inputs_sent),
                    target=str(ctx.targeting.target.target_name),
                    attacked_ok=False,
                    abort_reason=str(exc),
                )
                raise

            log_json(
                logger,
                event='tick',
                gate='combat',
                tick_index=int(tick_index),
                intent='CombatIntent',
                attempts_used=int(ctx.combat.attempt_count),
                intents_emitted=int(ctx.combat.intents_emitted),
                inputs_sent=int(ctx.combat.inputs_sent),
                target=str(ctx.targeting.target.target_name),
                attacked_ok=bool(attacked_ok),
                abort_reason='none',
            )

            ctx.telemetry.tick_count += 1

            if attacked_ok:
                log_json(
                    logger,
                    event='success',
                    gate='combat',
                    status='SUCCESS',
                    result='combat_evidence_ok',
                    target=str(ctx.targeting.target.target_name),
                )
                return 0

            time.sleep(tick_period)

        raise PreflightFailed('combat_timeout')

    except PreflightFailed as exc:
        if dump_enabled():
            before, after = snapshot('combat')
            if before is not None or after is not None:
                dump_pair(gate='combat', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='combat', reason=str(exc))
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
