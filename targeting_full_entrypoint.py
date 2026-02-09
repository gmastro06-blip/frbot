from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair
from diagnostics.jsonlog import log as log_json
from diagnostics.logger import configure_logger
from diagnostics.last_frames import snapshot
from rules.targeting import select_targeting_intent
from runtime.battle_list_semantics import detect_battle_list
from runtime.pacing import wait_until_ns
from runtime.profile import cap_ticks
from runtime.targeting_preflight import run as targeting_preflight_run
from runtime.targeting_runner import execute_intent


_GATE = 'targeting_full'


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


def _frames_dir() -> Path:
    raw = (_env_str('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return Path(str(raw))
    profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    if profile == 'prod_full':
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _write_evidence_manifest(*, evidence_dir: Path, capture: object) -> None:
    try:
        src = (_env_str('FRBOT_CAPTURE_SOURCE', 'client') or 'client').strip().lower()
        payload = {
            'capture_source': ('obs_source' if src == 'obs_source' else ('obs' if src == 'obs' else 'client')),
            'obs_source_name': str(getattr(capture, 'obs_source_name', '') or _env_str('FRBOT_OBS_SOURCE_NAME', '') or ''),
            'obs_projector_title': str(_env_str('FRBOT_OBS_PROJECTOR_TITLE', '') or ''),
            'ts': int(time.time()),
        }
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / 'evidence_manifest.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception:
        return


def _write_last_result(
    *,
    evidence_dir: Path,
    ok: bool,
    outcome_kind: str,
    actions_sent: int,
    successes: int,
    before_ppm: str | None,
    after_ppm: str | None,
    evidence_reason: str,
) -> None:
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            'gate': _GATE,
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'actions_sent': int(actions_sent),
            'successes': int(successes),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'evidence_reason': str(evidence_reason),
        }
        (evidence_dir / f'{_GATE}_last_result.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _load_targeting_full_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_TARGETING_FULL_BACKEND', _env_str('FRBOT_TARGETING_BACKEND', 'real')) or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),
        enable_cavebot=False,
        enable_targeting=True,
        battle_list_roi=_env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
        max_attempts_per_target=_env_int('FRBOT_MAX_ATTEMPTS_PER_TARGET', 2),
        max_time_ms_per_target=_env_int('FRBOT_MAX_TIME_MS_PER_TARGET', 2500),
    )


def run_targeting_full_only() -> int:
    """targeting_full gate.

    Requirements:
    - Evidence-or-abort
    - Strong window binding before every input
    - Bounded ticks
    """

    max_total_ticks = cap_ticks(_env_int('FRBOT_TARGETING_FULL_MAX_TICKS', 60))
    evidence_dir = _frames_dir()

    actions_sent = 0

    capture = None
    try:
        cfg = _load_targeting_full_config_from_env()

        if cfg.mode.strip().lower() == 'real' and sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            _write_last_result(
                evidence_dir=evidence_dir,
                ok=False,
                outcome_kind='unsupported_platform',
                actions_sent=0,
                successes=0,
                before_ppm=None,
                after_ppm=None,
                evidence_reason='unsupported_platform',
            )
            return 1

        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = targeting_preflight_run(ctx)
        _write_evidence_manifest(evidence_dir=evidence_dir, capture=capture)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        next_tick_ns = time.monotonic_ns()

        for tick_index in range(int(max_total_ticks)):
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

            res = select_targeting_intent(ctx.targeting.target, obs.entries)
            if res.abort_reason is not None:
                raise PreflightFailed(res.abort_reason)

            selected_name = res.intent.target_name if res.intent is not None else None

            if res.intent is not None:
                actions_sent += 1
                execute_intent(ctx, capture=capture, input_=input_, binding=binding, intent=res.intent, gate=_GATE)

            log_json(
                logger,
                event='tick',
                gate=_GATE,
                tick_index=int(tick_index),
                selected_target=str(selected_name),
                attempts_used=int(ctx.targeting.attempt_count),
                locked=bool(ctx.targeting.target.locked),
            )

            ctx.telemetry.tick_count += 1

            if ctx.targeting.target.locked:
                before, after = snapshot(_GATE)
                before_ppm, after_ppm = dump_pair(gate=_GATE, before=before, after=after, reason='success', out_dir=evidence_dir)
                if not before_ppm or not after_ppm:
                    raise PreflightFailed('targeting_full_missing_evidence_frames')

                _write_last_result(
                    evidence_dir=evidence_dir,
                    ok=True,
                    outcome_kind='locked',
                    actions_sent=int(actions_sent),
                    successes=1,
                    before_ppm=before_ppm,
                    after_ppm=after_ppm,
                    evidence_reason='success',
                )
                log_json(logger, event='success', gate=_GATE, target=str(ctx.targeting.target.target_name))
                return 0

            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

        raise PreflightFailed('target_not_acquired')

    except PreflightFailed as exc:
        before, after = snapshot(_GATE)
        # If no intent was executed, snapshot may be empty; fall back to direct capture.
        try:
            if (before is None or after is None) and capture is not None:
                before = before or capture.grab()
                after = after or capture.grab()
        except Exception:
            pass
        before_ppm, after_ppm = dump_pair(gate=_GATE, before=before, after=after, reason=str(exc), out_dir=evidence_dir)
        _write_last_result(
            evidence_dir=evidence_dir,
            ok=False,
            outcome_kind='failed',
            actions_sent=int(actions_sent),
            successes=0,
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            evidence_reason=str(exc),
        )
        write_fatal(str(exc), exc)
        return 1
    except ContractViolation as exc:
        write_fatal('runtime crashed', exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1
