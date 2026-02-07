from __future__ import annotations

import os
import sys
from pathlib import Path

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair, dump_frame_ppm, dump_enabled
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot

from runtime.combat_basic_preflight import run as combat_basic_preflight_run
from runtime.combat_basic_runner import execute_combat_basic_once


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _load_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_COMBAT_BASIC_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=True,

        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        target_hp_bar_roi=_env_str('FRBOT_TARGET_HP_BAR_ROI', 'target_hp_bar'),
        combat_cooldown_roi=_env_str('FRBOT_COMBAT_COOLDOWN_ROI', 'combat_cooldown'),
        combat_feedback_roi=_env_str('FRBOT_COMBAT_FEEDBACK_ROI', 'combat_feedback'),

        combat_target_hp_decrease_min=_env_float('FRBOT_COMBAT_BASIC_TARGET_HP_DECREASE_MIN', 0.02),
        attack_key=_env_str('FRBOT_ATTACK_KEY', 'SPACE'),

        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_combat_basic_only() -> int:
    """combat_basic feature gate.

    Contract:
    - Enabled by default.
    - 1 intent -> 1 input -> AFTER -> evidence -> decide.
    - No targeting, no movement, no looting.
    """

    try:
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_config_from_env()
        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        # Gate preflight must run before runtime.log.
        capture, input_, binding = combat_basic_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        outcome = execute_combat_basic_once(ctx, capture=capture, input_=input_, binding=binding)

        # Evidence artifacts (required):
        # - fixed names for human inspection
        # - plus a timestamped pair that matches evidence inventory regex
        before, after = snapshot('combat_basic')
        if before is not None and after is not None:
            frames_dir = Path('diagnostics') / 'frames'
            dump_frame_ppm(before, frames_dir / 'combat_basic_success_before.ppm')
            dump_frame_ppm(after, frames_dir / 'combat_basic_success_after.ppm')
            dump_pair(gate='combat_basic', before=before, after=after, reason='success')

        log_json(
            logger,
            event='tick',
            gate='combat_basic',
            intent_type='combat_basic',
            inputs_sent=int(ctx.combat.inputs_sent),
            action_type=str(getattr(ctx.combat, 'last_action_type', '') or ''),
            action_value=str(getattr(ctx.combat, 'last_action_value', '') or ''),
            click_xy=getattr(ctx.combat, 'last_click_xy', None),
            evidence_kind=str(outcome.evidence.evidence_kind),
            evidence_ok=bool(outcome.evidence.evidence_ok),
            hp_before=getattr(outcome.evidence, 'hp_before', None),
            hp_after=getattr(outcome.evidence, 'hp_after', None),
            feedback_before=bool(getattr(outcome.evidence, 'feedback_before', False)),
            feedback_after=bool(getattr(outcome.evidence, 'feedback_after', False)),
            locked_before=bool(getattr(outcome.evidence, 'locked_before', False)),
            locked_after=bool(getattr(outcome.evidence, 'locked_after', False)),
            abort_reason='none',
        )

        log_json(
            logger,
            event='success',
            gate='combat_basic',
            status='SUCCESS',
            result=str(outcome.evidence.evidence_kind),
        )
        return 0

    except PreflightFailed as exc:
        if dump_enabled():
            before, after = snapshot('combat_basic')
            if before is not None or after is not None:
                dump_pair(gate='combat_basic', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='combat_basic', reason=str(exc))
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
    raise SystemExit(run_combat_basic_only())
